"""一轮正常结束时按本请求实际写出的文件补一次任务板进度（update_project(tasks=[...])）。

只认结构化证据：本请求写过（RepeatReadGuard 台账）、落库非空、类型与编号对得上的文件。
tool_use_id ``workflow_auto_completion`` 是回放与前端识别这次自动补记的标识，不能改。
"""

import asyncio
from typing import Any

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.openai_agents.events import parse_mcp_tool_payload
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.tools.mcp_tools import ToolContext, update_project
from agent.tools.task_progress import (
    ArtifactEvidence,
    artifact_units,
    format_units,
    task_artifact_type,
    task_evidence_spec,
    validate_task_artifact_fields,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


def _artifact_written(artifact: str, written: list[ArtifactEvidence]) -> bool:
    """A legacy label requires exact title identity, never a substring."""
    return bool(artifact.strip()) and any(artifact.strip() == item.title.strip() for item in written)


def _build_completion_task_payload(
    tasks: list[dict[str, Any]],
    written_artifacts: list[ArtifactEvidence],
    live_artifacts: list[ArtifactEvidence] | None = None,
) -> list[dict[str, Any]] | None:
    """Complete file tasks only from live, typed evidence written in this request."""
    written = [item for item in written_artifacts if isinstance(item, ArtifactEvidence)]
    if not written:
        return None
    updated_tasks: list[dict[str, Any]] = []
    has_updates = False
    for task in tasks:
        if not isinstance(task, dict):
            continue
        updated = dict(task)
        if updated.get("status") not in {"in_progress", "pending"}:
            updated_tasks.append(updated)
            continue
        try:
            validate_task_artifact_fields(updated)
        except ValueError:
            updated_tasks.append(updated)
            continue
        file_type = task_artifact_type(updated)
        if file_type is None:
            updated_tasks.append(updated)
            continue
        refs = set(updated.get("artifact_refs") or [])
        matching = [item for item in written if item.file_type == file_type and (not refs or item.id in refs)]
        if not matching:
            updated_tasks.append(updated)
            continue
        spec = task_evidence_spec(updated)
        label = str(updated.get("artifact") or "").strip()
        if spec is not None:
            prior_ids = set(updated.get("completed_artifact_refs") or [])
            live_prior = [
                item for item in live_artifacts or []
                if item.id in prior_ids and item.file_type == file_type and (not refs or item.id in refs)
            ]
            relevant = [
                item for item in matching + live_prior
                if artifact_units([item], spec.unit, file_type) & spec.numbers
            ]
            if not relevant:
                updated_tasks.append(updated)
                continue
            updated["completed_artifact_refs"] = sorted({item.id for item in relevant})
            covered = artifact_units(relevant, spec.unit, file_type) & spec.numbers
            missing = set(spec.numbers) - covered
            if not missing:
                updated["status"] = "done"
                updated.pop("progress", None)
                updated.pop("done_units", None)
            else:
                updated["status"] = "in_progress"
                updated["done_units"] = sorted(covered)
                updated["progress"] = f"已写：{format_units(covered, spec.unit)}；待写：{format_units(missing, spec.unit)}"
        elif refs:
            prior_ids = set(updated.get("completed_artifact_refs") or [])
            live_prior = [item for item in live_artifacts or [] if item.id in prior_ids and item.file_type == file_type and item.id in refs]
            covered_ids = {item.id for item in matching + live_prior}
            updated["completed_artifact_refs"] = sorted(covered_ids)
            if refs <= covered_ids:
                updated["status"] = "done"
        elif label and _artifact_written(label, matching):
            updated["status"] = "done"
            updated["completed_artifact_refs"] = sorted(item.id for item in matching if item.title.strip() == label)
        has_updates |= updated != task
        updated_tasks.append(updated)
    return updated_tasks if has_updates else None


def _existing_written_artifacts(file_ids: list[str]) -> list[ArtifactEvidence]:
    """Read nonempty live files scoped to this request's project."""
    if not file_ids:
        return []
    from sqlmodel import select

    from models import File
    with ToolContext.short_lived_session() as session:
        query = select(File).where(File.id.in_(file_ids), File.is_deleted.is_(False))
        project_id = ToolContext.get_project_id()
        if project_id:
            query = query.where(File.project_id == project_id)
        rows = session.exec(query).all()
        return [ArtifactEvidence(row.id, row.title, row.file_type, row.get_metadata()) for row in rows if row.content.strip()]


def _live_project_artifacts(project_id: str) -> list[ArtifactEvidence] | None:
    """Current typed file evidence for revalidating persisted progress."""
    from sqlmodel import select

    from models import File
    try:
        with ToolContext.short_lived_session() as session:
            rows = session.exec(select(File).where(File.project_id == project_id, File.is_deleted.is_(False))).all()
            return [ArtifactEvidence(row.id, row.title, row.file_type, row.get_metadata()) for row in rows if row.content.strip()]
    except Exception as exc:
        logger.warning("Unable to verify live task artifacts", extra={"error_type": type(exc).__name__})
        return None


def _has_incomplete_tasks(tasks: list[dict[str, Any]]) -> bool:
    return any(isinstance(task, dict) and task.get("status") in ("in_progress", "pending") for task in tasks)


def _update_project_failed(tool_result: Any) -> bool:
    """Return True if an ``update_project`` MCP envelope or flat mock dict indicates failure."""
    if not isinstance(tool_result, dict):
        return False
    if tool_result.get("is_error") is True or tool_result.get("status") in {"error", "partial"}:
        return True
    payload = parse_mcp_tool_payload(tool_result)
    return isinstance(payload, dict) and payload.get("status") not in (None, "success")


async def auto_finalize_task_board_on_completion(
    guard: RepeatReadGuard | None = None,
) -> tuple[list[StreamEvent], bool]:
    """
    在 workflow 完成时按实际产物补一次 update_project(tasks=[...])。

    返回 ``(events, has_incomplete)``：``has_incomplete`` 直接基于本次已读取/更新后的任务列表，
    避免调用方再重读一次任务板。只有本请求确实写出对应文件的任务改为 done（规则见
    _build_completion_task_payload；pending 任务只认编号或产出物对上的文件），什么都没写时不读也不补。
    """
    session_id = ToolContext.get_session_id()
    if not session_id:
        return [], False

    user_id = ToolContext.get_user_id()
    project_id = ToolContext.get_project_id()

    try:
        # 本请求的写入证据：工具确认改动的文件 + StreamAdapter 确认落库的正文（整章替换的定位结果不算）。
        written_ids = list(
            dict.fromkeys(
                [*(guard.written_file_ids() if guard is not None else []), *ToolContext.saved_bodies_this_request()]
            )
        )
        if not written_ids:
            return [], False

        from services.infra.task_board_service import task_board_service

        current_tasks = task_board_service.get_tasks(
            session_id,
            user_id=user_id,
            project_id=project_id,
        ) or []
        if not _has_incomplete_tasks(current_tasks):
            return [], False
        written_artifacts = await asyncio.to_thread(_existing_written_artifacts, written_ids)
        live_artifacts: list[ArtifactEvidence] | None = None
        if project_id and any(
            isinstance(task, dict) and task.get("completed_artifact_refs")
            for task in current_tasks
        ):
            live_artifacts = await asyncio.to_thread(_live_project_artifacts, project_id)
        completion_tasks = _build_completion_task_payload(
            current_tasks, written_artifacts, live_artifacts
        )
        if not completion_tasks:
            return [], True

        tool_result = await update_project({"tasks": completion_tasks}, written_artifact_ids=written_ids)
        still_incomplete = True if _update_project_failed(tool_result) else _has_incomplete_tasks(completion_tasks)
        tool_use_id = "workflow_auto_completion"
        return [
            StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={
                    "id": tool_use_id,
                    "name": "update_project",
                    "status": "complete",
                    "input": {"tasks": completion_tasks},
                },
            ),
            StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "tool_use_id": tool_use_id,
                    "name": "update_project",
                    "result": tool_result,
                },
            ),
        ], still_incomplete
    except Exception as e:
        log_with_context(
            logger,
            30,  # WARNING
            "Auto finalize task board failed",
            error=str(e),
            error_type=type(e).__name__,
            session_id=session_id,
            user_id=user_id,
            project_id=project_id,
        )
        return [], False

