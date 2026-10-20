"""
Project operations for agent tools.

This module provides operations related to project management:
- update_project_status: Update project status information for AI context
- execute_update_plan: Save tasks to Redis task board

Extracted from the monolithic file_executor.py for better maintainability.
"""

import json
import re
from collections.abc import Callable, Sequence
from typing import Any, cast

from sqlmodel import Session, desc, select

from agent.tools.permissions import check_project_ownership
from agent.tools.task_progress import (
    ArtifactEvidence,
    artifact_units,
    format_units,
    task_artifact_type,
    task_evidence_spec,
    validate_task_artifact_fields,
)
from config.datetime_utils import utcnow
from config.project_status import (
    PROJECT_TITLE_AUTHOR_TOOL,
    PROJECT_TITLE_LEDGER_ACTION,
    PROJECT_TITLE_SOURCE_AI,
    PROJECT_TITLE_SOURCE_AUTHOR,
    normalize_project_status_payload,
)
from config.project_templates import PROJECT_TEMPLATES_BY_LANG
from models import AgentArtifactLedger, File, Project
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

PROJECT_TITLE_MAX_CHARS = 30

# 项目还叫这些名字时，说明作者没自己起过名：AI 给作品起了名就可以跟着改。
# 来源是各类型、各语言模板的 default_project_name，加上前端的兜底名。
DEFAULT_PROJECT_NAMES = frozenset(
    {
        str(template.get("default_project_name") or "").strip()
        for templates in PROJECT_TEMPLATES_BY_LANG.values()
        for template in templates.values()
    }
    | {"我的项目", "未命名项目", "Untitled"}
) - {""}

TITLE_SKIPPED_AUTHOR_NAMED = "author_named"
TITLE_SKIPPED_INVALID = "invalid_title"
TITLE_SKIPPED_UNCHANGED = "unchanged"
TITLE_SOURCE_UNAVAILABLE = "provenance_unavailable"

# 每次 update_project 改了项目名，都在 agent_artifact_ledger 记一行（同一次提交）。
# 项目表没有元数据字段，「现在这个名字是不是 AI 自己起的」只能靠这条记录判断：
# 最近一条记录是 AI 自动命名、且名字和项目现在的名字一样，才算 AI 起的名。

# 拒绝改名时写进工具结果的说明。必须给出界面上真实存在的入口，模型才不会自己编一个。
# 这里不提 author_requested：每次拒绝都会带这段话，写了就等于教模型「再带上标记重试」。
AUTHOR_NAMED_TITLE_NOTE = (
    "项目名「{current}」是作者自己起的，这次没改，不要再换参数重试。"
    "需要提到改名时告诉作者：点顶部的项目名打开项目切换器，"
    "再点项目名旁的铅笔（「编辑项目名称」）就能自己改。"
)

_TITLE_STRIP_CHARS = re.compile(r"[《》“”]")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalize_project_title(raw: Any) -> str | None:
    """去掉书名号、弯引号和首尾空白（中间连续空白并成一个），长度 1–30 才有效。"""
    if not isinstance(raw, str):
        return None
    title = _WHITESPACE_RUN.sub(" ", _TITLE_STRIP_CHARS.sub("", raw)).strip()
    if not title or len(title) > PROJECT_TITLE_MAX_CHARS:
        return None
    return title


class ProjectOperations:
    """
    Project-related operations for AI agent tools.

    This class provides operations for managing project status and task plans,
    with permission checking and logging.
    """

    def __init__(self, session: Session, user_id: str | None = None):
        """
        Initialize project operations.

        Args:
            session: Database session
            user_id: Current user ID (UUID string, for permission checks)
        """
        self.session = session
        self.user_id = user_id

    @staticmethod
    def _normalize_task_phase(task: dict[str, Any]) -> str | None:
        """Return normalized phase identity key if available."""
        phase_id = task.get("phase_id")
        if not isinstance(phase_id, str):
            return None
        normalized_phase = phase_id.strip()
        if not normalized_phase:
            return None
        return f"phase:{normalized_phase}"

    @classmethod
    def _normalize_task_phase_identity(cls, task: dict[str, Any]) -> tuple[str, str | None] | None:
        """Return normalized phase identity key with task name to avoid phase_id reuse collisions."""
        phase_key = cls._normalize_task_phase(task)
        if not phase_key:
            return None

        task_key = cls._normalize_task_name(task)
        return (phase_key, task_key)

    @staticmethod
    def _normalize_task_name(task: dict[str, Any]) -> str | None:
        """Return normalized legacy task-name identity key if available."""
        task_name = task.get("task")
        if not isinstance(task_name, str):
            return None
        normalized_task = task_name.strip()
        if not normalized_task:
            return None
        return f"task:{normalized_task}"

    @staticmethod
    def _coerce_task_name_field(task: dict[str, Any]) -> None:
        """
        Best-effort normalization for task-board payloads.

        In production we occasionally see LLM tool calls (or legacy clients)
        sending task objects that use `title` / `name` instead of `task`.
        The task board is non-critical metadata; we normalize common aliases
        into the canonical `task` field before validation to reduce noisy
        warnings and keep the board usable.
        """
        task_name = task.get("task")
        if isinstance(task_name, str) and task_name.strip():
            return

        for alias_key in ("title", "name", "text", "description"):
            alias_value = task.get(alias_key)
            if isinstance(alias_value, str) and alias_value.strip():
                task["task"] = alias_value
                return

    def _validate_update_plan_tasks(
        self,
        tasks: list[Any],
        previous_tasks: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Validate update_plan tasks for lightweight phase-state-machine rules.

        Rules:
        1) At most one in_progress task in the current batch.
        2) Block rollback from done -> in_progress compared with previous board.
        3) Legacy task/status supports pending/in_progress and historical done.
           New done needs an explicit conversation kind or typed file evidence.
        """
        in_progress_count = 0
        valid_statuses = {"pending", "in_progress", "done"}
        previous_by_identity = {
            self._task_identity(task): task for task in previous_tasks or [] if isinstance(task, dict)
        }

        for index, task in enumerate(tasks):
            if not isinstance(task, dict):
                raise ValueError(f"Invalid task at index {index}: expected object")

            task_name = task.get("task")
            if not isinstance(task_name, str) or not task_name.strip():
                raise ValueError(f"Invalid task at index {index}: 'task' is required")

            validate_task_artifact_fields(task)
            if "task_kind" in task and task["task_kind"] not in {"file", "conversation"}:
                raise ValueError("task_kind must be file or conversation")
            if task.get("task_kind") == "conversation" and any(
                field in task for field in ("artifact_file_type", "artifact_refs", "unit_spec")
            ):
                raise ValueError("conversation tasks cannot claim file delivery fields")
            prior = previous_by_identity.get(self._task_identity(task))
            legacy_prior = previous_by_identity.get((None, self._normalize_task_name(task)))
            if task.get("task_kind") == "conversation" and any(
                entry and (entry.get("task_kind") == "file" or task_artifact_type(entry) is not None)
                for entry in (prior, legacy_prior)
            ):
                raise ValueError("file delivery tasks cannot be reclassified as conversation")
            status = task.get("status")
            if status not in valid_statuses:
                raise ValueError(
                    f"Invalid task at index {index}: status must be one of {sorted(valid_statuses)}"
                )

            if status == "in_progress":
                in_progress_count += 1

        if in_progress_count > 1:
            raise ValueError(
                f"同时有 {in_progress_count} 个任务为 in_progress，只能保留一个；"
                "其余未开始的任务改为 pending 后，重新提交完整 tasks。"
            )

        historical_tasks = previous_tasks or []
        done_phase_keys: set[tuple[str, str | None]] = set()
        done_legacy_task_keys: set[str] = set()
        for previous in historical_tasks:
            if not isinstance(previous, dict) or previous.get("status") != "done":
                continue

            # Prefer phase identity (phase_id + task). Only fallback to task-name
            # identity when historical entry has no phase_id (legacy payload).
            phase_key = self._normalize_task_phase_identity(previous)
            if phase_key:
                done_phase_keys.add(phase_key)
                continue

            task_key = self._normalize_task_name(previous)
            if task_key:
                done_legacy_task_keys.add(task_key)

        if not done_phase_keys and not done_legacy_task_keys:
            return

        rollback_targets: list[str] = []
        for index, task in enumerate(tasks):
            if task.get("status") != "in_progress":
                continue

            phase_key = self._normalize_task_phase_identity(task)
            task_key = self._normalize_task_name(task)

            # If current task has phase_id, match by (phase_id + task name).
            # This prevents false positives when phase_id is reused, while still
            # allowing same task names across different phases.
            is_rollback = (
                bool(phase_key and phase_key in done_phase_keys)
                or bool(not phase_key and task_key and task_key in done_legacy_task_keys)
            )

            if is_rollback:
                rollback_targets.append(
                    str(task.get("phase_id") or task.get("task") or f"index-{index}")
                )

        if rollback_targets:
            targets = ", ".join(rollback_targets)
            raise ValueError(
                f"Invalid task transition: done -> in_progress rollback is not allowed ({targets})"
            )

    _ARTIFACT_CONTRACT_FIELDS = ("task_kind", "artifact_file_type", "artifact_refs", "unit_spec", "artifact")
    _SERVER_PROGRESS_FIELDS = ("done_units", "progress", "completed_artifact_refs")
    _RENDERED_PROGRESS_SUFFIX_RE = re.compile(r"\s*[（(]已写：[^（）()]*[）)]\s*$")

    @classmethod
    def _task_identity(cls, task: dict[str, Any]) -> tuple[str | None, str | None]:
        # Phase-aware identities never fall back to a same-name task in another phase.
        return cls._normalize_task_phase_identity(task) or (None, cls._normalize_task_name(task))

    @classmethod
    def _carry_artifact_contract(
        cls, tasks: list[Any], previous_tasks: list[dict[str, Any]],
    ) -> None:
        recorded = {cls._task_identity(task): task for task in previous_tasks if isinstance(task, dict)}
        for task in tasks:
            if not isinstance(task, dict):
                continue
            name = task.get("task")
            if isinstance(name, str):
                task["task"] = cls._RENDERED_PROGRESS_SUFFIX_RE.sub("", name.strip())
            prior = recorded.get(cls._task_identity(task))
            if not prior or any(
                field in task and task[field] != prior.get(field)
                for field in cls._ARTIFACT_CONTRACT_FIELDS
            ):
                continue
            for field in cls._ARTIFACT_CONTRACT_FIELDS:
                if field in prior:
                    task.setdefault(field, prior[field])

    def _live_artifacts(self, project_id: str | None) -> list[ArtifactEvidence]:
        if not project_id:
            return []
        rows = self.session.exec(
            select(File).where(File.project_id == project_id, File.is_deleted.is_(False))
            .execution_options(populate_existing=True)
        ).all()
        return [
            ArtifactEvidence(row.id, row.title, row.file_type, row.get_metadata())
            for row in rows if row.content.strip()
        ]

    @staticmethod
    def _matching_artifacts(task: dict[str, Any], artifacts: list[ArtifactEvidence]) -> list[ArtifactEvidence]:
        file_type = task_artifact_type(task)
        refs = set(task.get("artifact_refs") or [])
        matching = [item for item in artifacts if item.file_type == file_type and (not refs or item.id in refs)]
        spec = task_evidence_spec(task)
        if spec:
            return [item for item in matching if artifact_units([item], spec.unit, file_type) & spec.numbers]
        title = str(task.get("artifact") or "").strip()
        return matching if refs else [item for item in matching if title and title == item.title.strip()]

    @staticmethod
    def _missing_artifact_evidence(task: dict[str, Any], matching: list[ArtifactEvidence]) -> str:
        file_type = task_artifact_type(task)
        if file_type is None:
            return "明确的产物类型；非文件任务须显式标记 task_kind=conversation"
        refs = set(task.get("artifact_refs") or [])
        if refs and not refs <= {item.id for item in matching}:
            return "对应类型的产物文件"
        spec = task_evidence_spec(task)
        if spec is not None:
            missing = set(spec.numbers) - artifact_units(matching, spec.unit, file_type)
            return format_units(missing, spec.unit) if missing else ""
        if refs or task.get("artifact"):
            return "" if matching else "完全同名、对应类型的产物文件"
        return "明确的产物引用或编号"

    def _refresh_verified_progress(
        self,
        tasks: list[dict[str, Any]],
        previous_tasks: list[dict[str, Any]],
        project_id: str | None,
        written_artifact_ids: Sequence[str] | None,
    ) -> list[dict[str, str]]:
        """Only persisted backend file IDs and trusted writes can retain progress.

        Model-supplied counters/IDs are stripped before this method. A new manual
        file completion is checked against current project files; conversational
        completion is explicit, never inferred from task wording.
        """
        recorded = {self._task_identity(task): task for task in previous_tasks if isinstance(task, dict)}
        needs_files = any(task_artifact_type(task) is not None for task in tasks)
        try:
            artifacts = self._live_artifacts(project_id) if needs_files else []
        except Exception as exc:
            logger.warning("Task artifact verification unavailable", extra={"error_type": type(exc).__name__})
            artifacts = []
        written_ids = set(written_artifact_ids or [])
        unverified = []
        for task in tasks:
            prior = recorded.get(self._task_identity(task))
            same_contract = bool(prior) and all(
                task.get(field) == prior.get(field) for field in self._ARTIFACT_CONTRACT_FIELDS
            )
            file_type = task_artifact_type(task)
            is_conversation = task.get("task_kind") == "conversation"
            if file_type is None:
                # Retain legacy completed history, but do not create new untyped facts.
                historical_done = same_contract and prior.get("status") == "done"
                if task.get("status") == "done" and not is_conversation and not historical_done:
                    unverified.append({"task": str(task["task"]), "missing": self._missing_artifact_evidence(task, [])})
                continue

            matching = self._matching_artifacts(task, artifacts)
            prior_refs = set(prior.get("completed_artifact_refs") or []) if same_contract else set()
            verified = [item for item in matching if item.id in prior_refs | written_ids]
            if task.get("status") == "done":
                missing = self._missing_artifact_evidence(task, matching)
                if missing:
                    # A previously verified completion can become stale after human edits.
                    # Correct that fact instead of retaining a false done status.
                    if same_contract and prior.get("status") == "done" and prior_refs:
                        task["status"] = "pending"
                    else:
                        unverified.append({"task": str(task["task"]), "missing": missing})
                        continue
                else:
                    verified = matching
            if verified:
                task["completed_artifact_refs"] = sorted(item.id for item in verified)
            spec = task_evidence_spec(task)
            if spec:
                covered = artifact_units(verified, spec.unit, file_type) & spec.numbers
                missing_units = set(spec.numbers) - covered
                if covered and not missing_units and not self._missing_artifact_evidence(task, verified):
                    task["status"] = "done"
                elif covered:
                    task["done_units"] = sorted(covered)
                    task["progress"] = f"已写：{format_units(covered, spec.unit)}；待写：{format_units(missing_units, spec.unit)}"
            elif verified and not self._missing_artifact_evidence(task, verified):
                task["status"] = "done"
        return unverified

    def update_project_status(
        self,
        project_id: str,
        summary: str | None = None,
        current_phase: str | None = None,
        writing_style: str | None = None,
        notes: str | None = None,
        title: str | None = None,
        author_requested: bool = False,
        *,
        notes_transform: Callable[[str | None], str] | None = None,
    ) -> dict[str, Any]:
        """Update metadata using fresh locked state, including any source-aware note merge."""
        from services.project_service import project_metadata_write_lock

        with project_metadata_write_lock(project_id):
            return self._update_project_status_locked(
                project_id, summary, current_phase, writing_style, notes, title,
                author_requested, notes_transform=notes_transform,
            )

    def _update_project_status_locked(
        self,
        project_id: str,
        summary: str | None = None,
        current_phase: str | None = None,
        writing_style: str | None = None,
        notes: str | None = None,
        title: str | None = None,
        author_requested: bool = False,
        *,
        notes_transform: Callable[[str | None], str] | None = None,
    ) -> dict[str, Any]:
        """
        Update project status information for AI context awareness.

        Args:
            project_id: Project ID
            summary: Project summary/background
            current_phase: Current writing phase description
            writing_style: Writing style guidelines
            notes: Additional notes for AI assistant
            title: Work title (≤30 chars, no 《》). Renames the project while
                it still has a default name or the name AI auto-naming gave it;
                a name the author chose is always kept.
            author_requested: Legacy parameter, ignored; model claims never grant authority.

        Returns:
            Updated project status fields; with ``title``, also
            ``project_name_updated`` and, when not renamed, ``title_skipped``
            (author_named / invalid_title / unchanged / provenance_unavailable)

        Raises:
            PermissionError: If user doesn't have permission
            ValueError: If project not found
        """
        # Serialize with the author's explicit rename, and refresh stale ORM state
        # before deciding whether the current title is still AI-owned.
        self.session.exec(
            select(Project).where(Project.id == project_id).with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        project = check_project_ownership(self.session, project_id, self.user_id)

        if notes_transform is not None:
            notes = notes_transform(project.notes)

        # Track what was updated
        updated_fields = []

        # Normalize and validate status fields.
        # Empty string is preserved so callers can explicitly clear a field.
        raw_updates = {
            "summary": summary,
            "current_phase": current_phase,
            "writing_style": writing_style,
            "notes": notes,
        }
        normalized_updates = normalize_project_status_payload(raw_updates)

        for field_name, raw_value in raw_updates.items():
            if raw_value is not None:
                setattr(project, field_name, normalized_updates.get(field_name, ""))
                updated_fields.append(field_name)

        title_result: dict[str, Any] = {}
        if title is not None:
            title_result = self._apply_ai_title(
                project, title, author_requested=author_requested
            )

        has_status_fields = any(value is not None for value in raw_updates.values())
        if has_status_fields or title_result.get("project_name_updated"):
            # Update timestamp
            project.updated_at = utcnow()
            self.session.commit()
            self.session.refresh(project)
        else:
            # Nothing to write, but the FOR UPDATE above must not outlive this call: the tool
            # records its artifact ledger in a separate session right after, and that insert
            # waits on this row. Returning with the lock held deadlocked the request (2026-10-20).
            self.session.commit()

        result: dict[str, Any] = {
            "project_id": project.id,
            "updated_fields": updated_fields,
            "current_status": {
                "summary": project.summary,
                "current_phase": project.current_phase,
                "writing_style": project.writing_style,
                "notes": project.notes,
            },
        }
        result.update(title_result)
        return result

    def _apply_ai_title(
        self, project: Project, raw_title: Any, *, author_requested: bool = False
    ) -> dict[str, Any]:
        """改项目名。

        - 默认名、或 AI 自动命名留下的名字：直接改成新作品名。
        - 作者自己起的名字：不能由 Agent 改，作者使用项目设置中的明确改名操作。
        """
        del author_requested  # Model/tool booleans do not grant author-title authority.
        title = normalize_project_title(raw_title)
        if title is None:
            return {
                "project_name_updated": False,
                "title_skipped": TITLE_SKIPPED_INVALID,
                "title_error": (
                    f"作品名去掉书名号后需为 1–{PROJECT_TITLE_MAX_CHARS} 字，本次未改项目名"
                ),
            }
        current_name = (project.name or "").strip()
        if current_name == title:
            return {"project_name_updated": False, "title_skipped": TITLE_SKIPPED_UNCHANGED}
        title_source = self._current_title_source(project, current_name)
        if title_source == TITLE_SOURCE_UNAVAILABLE:
            return {
                "project_name_updated": False,
                "title_skipped": TITLE_SOURCE_UNAVAILABLE,
                "title_note": "无法确认项目名的命名来源，本次未改名；作者可以通过项目设置直接改名。",
            }
        if title_source == PROJECT_TITLE_SOURCE_AUTHOR or (
            current_name not in DEFAULT_PROJECT_NAMES and title_source != PROJECT_TITLE_SOURCE_AI
        ):
            return {
                "project_name_updated": False,
                "title_skipped": TITLE_SKIPPED_AUTHOR_NAMED,
                "title_note": AUTHOR_NAMED_TITLE_NOTE.format(current=current_name),
            }
        project.name = title
        self.session.add(
            AgentArtifactLedger(
                project_id=project.id,
                user_id=self.user_id,
                action=PROJECT_TITLE_LEDGER_ACTION,
                tool_name="update_project",
                artifact_ref=f"project:{project.id}",
                payload=json.dumps(
                    {
                        "project_name": title,
                        "source": PROJECT_TITLE_SOURCE_AI,
                    },
                    ensure_ascii=False,
                ),
            )
        )
        return {"project_name_updated": True, "project_name": title}

    def _current_title_source(self, project: Project, current_name: str) -> str | None:
        """Explicit human title events override older AI naming, even after A→B→A."""
        try:
            # Human ownership is permanent in this tool's contract. Do not let
            # timestamp ties (or clock skew) resurrect an older AI naming grant.
            author_event = self.session.exec(
                select(AgentArtifactLedger.id).where(
                    AgentArtifactLedger.project_id == project.id,
                    AgentArtifactLedger.action == PROJECT_TITLE_LEDGER_ACTION,
                    AgentArtifactLedger.tool_name == PROJECT_TITLE_AUTHOR_TOOL,
                ).limit(1)
            ).first()
            if author_event is not None:
                return PROJECT_TITLE_SOURCE_AUTHOR
            payload = self.session.exec(
                select(AgentArtifactLedger.payload)
                .where(
                    AgentArtifactLedger.project_id == project.id,
                    AgentArtifactLedger.action == PROJECT_TITLE_LEDGER_ACTION,
                )
                .order_by(desc(AgentArtifactLedger.created_at))
                .limit(1)
            ).first()
        except Exception as exc:  # 读不到记录时按作者起的名字处理，宁可不改
            log_with_context(
                logger,
                30,  # WARNING
                "Failed to read project title ledger",
                project_id=project.id,
                error=str(exc),
            )
            return TITLE_SOURCE_UNAVAILABLE
        if payload is None:
            return None
        if not isinstance(payload, str):
            return TITLE_SOURCE_UNAVAILABLE
        try:
            record = json.loads(payload)
        except (TypeError, ValueError):
            return TITLE_SOURCE_UNAVAILABLE
        if not isinstance(record, dict):
            return TITLE_SOURCE_UNAVAILABLE
        # Source is durable authority; name equality only checks legacy AI-state consistency.
        if record.get("source") == PROJECT_TITLE_SOURCE_AUTHOR:
            return PROJECT_TITLE_SOURCE_AUTHOR
        if record.get("source") == PROJECT_TITLE_SOURCE_AI:
            if str(record.get("project_name") or "").strip() == current_name:
                return PROJECT_TITLE_SOURCE_AI
            # Legacy/direct writes did not record a source event. A name newer
            # than the last AI event is protected, never granted AI authority.
            return PROJECT_TITLE_SOURCE_AUTHOR
        return TITLE_SOURCE_UNAVAILABLE

    def execute_update_plan(
        self,
        session_id: str,
        tasks: list[dict[str, Any]],
        user_id: str | None = None,
        project_id: str | None = None,
        *,
        written_artifact_ids: Sequence[str] | None = None,
    ) -> dict[str, Any]:
        """
        Execute update_plan tool - save tasks to Redis task board.

        Args:
            session_id: Session identifier for task board
            tasks: List of task dictionaries with 'task' and 'status' fields
            user_id: Current user ID for runtime isolation
            project_id: Current project ID for runtime isolation

        Returns:
            Success message with task list summary
        """
        task_count = len(tasks) if isinstance(tasks, list) else 0
        log_with_context(
            logger,
            20,  # INFO
            "execute_update_plan started",
            session_id=session_id,
            task_count=task_count,
        )

        try:
            from services.infra.task_board_service import task_board_service

            if not isinstance(tasks, list):
                log_with_context(
                    logger,
                    30,  # WARNING
                    "execute_update_plan invalid tasks payload",
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    tasks_type=type(tasks).__name__,
                )
                return {
                    "status": "ignored",
                    "message": "任务计划板未更新（tasks 参数类型错误）",
                    "reason": "invalid_tasks_payload",
                    "error": f"tasks must be an array, got {type(tasks).__name__}",
                    "task_summary": {
                        "total": 0,
                        "pending": 0,
                        "in_progress": 0,
                        "done": 0,
                    },
                    "tasks": [],
                }

            # Normalize common alias fields (e.g. title/name -> task) before validation.
            normalized_tasks: list[Any] = []
            for task in tasks:
                if not isinstance(task, dict):
                    normalized_tasks.append(task)
                    continue

                normalized_task = dict(task)
                self._coerce_task_name_field(normalized_task)
                for field in self._SERVER_PROGRESS_FIELDS:
                    normalized_task.pop(field, None)
                normalized_tasks.append(normalized_task)

            tasks_payload = normalized_tasks

            # Lightweight phase-state-machine validation (backward compatible).
            previous_tasks = task_board_service.get_tasks(
                session_id,
                user_id=user_id,
                project_id=project_id,
            ) or []
            self._carry_artifact_contract(tasks_payload, previous_tasks)

            try:
                self._validate_update_plan_tasks(tasks=tasks_payload, previous_tasks=previous_tasks)
            except ValueError as validation_error:
                # Task board is best-effort metadata. Validation failures should not
                # crash the writing workflow; keep the previous board unchanged.
                pending = sum(
                    1
                    for t in tasks_payload
                    if isinstance(t, dict) and t.get("status") == "pending"
                )
                in_progress = sum(
                    1
                    for t in tasks_payload
                    if isinstance(t, dict) and t.get("status") == "in_progress"
                )
                done = sum(
                    1 for t in tasks_payload if isinstance(t, dict) and t.get("status") == "done"
                )

                log_with_context(
                    logger,
                    30,  # WARNING
                    "execute_update_plan validation rejected",
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                    error=str(validation_error),
                    error_type=type(validation_error).__name__,
                )

                return {
                    "status": "ignored",
                    "message": "任务计划板未更新（校验未通过）",
                    "reason": "validation_rejected",
                    "error": str(validation_error),
                    "task_summary": {
                        "total": len(tasks_payload),
                        "pending": pending,
                        "in_progress": in_progress,
                        "done": done,
                    },
                    "tasks": tasks_payload,
                }

            # Now safe to treat payload as list[dict], because validation succeeded.
            validated_tasks = cast(list[dict[str, Any]], tasks_payload)

            unverified = self._refresh_verified_progress(
                validated_tasks, previous_tasks, project_id, written_artifact_ids
            )
            if unverified:
                return {
                    "status": "ignored", "reason": "unverified_done",
                    "message": "任务计划板未更新：完成状态缺少可核验的对应产物",
                    "unverified_done": unverified, "tasks": validated_tasks,
                }

            # Save tasks to Redis
            success = task_board_service.save_tasks(
                session_id,
                validated_tasks,
                user_id=user_id,
                project_id=project_id,
            )

            if not success:
                raise ValueError("Failed to save tasks to Redis")

            # Build task summary
            pending = sum(1 for t in validated_tasks if t.get("status") == "pending")
            in_progress = sum(1 for t in validated_tasks if t.get("status") == "in_progress")
            done = sum(1 for t in validated_tasks if t.get("status") == "done")

            log_with_context(
                logger,
                20,  # INFO
                "execute_update_plan completed",
                session_id=session_id,
                pending=pending,
                in_progress=in_progress,
                done=done,
            )

            result: dict[str, Any] = {
                "status": "success",
                "message": "任务计划板已更新",
                "task_summary": {
                    "total": len(validated_tasks),
                    "pending": pending,
                    "in_progress": in_progress,
                    "done": done,
                },
                "tasks": validated_tasks,
            }
            return result

        except Exception as e:
            log_with_context(
                logger,
                40,  # ERROR
                "Error executing update_plan",
                session_id=session_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            tasks_list: list[Any] = tasks if isinstance(tasks, list) else []
            pending = sum(
                1 for t in tasks_list if isinstance(t, dict) and t.get("status") == "pending"
            )
            in_progress = sum(
                1 for t in tasks_list if isinstance(t, dict) and t.get("status") == "in_progress"
            )
            done = sum(1 for t in tasks_list if isinstance(t, dict) and t.get("status") == "done")

            # Task plan is best-effort metadata. Never raise: keep the writing workflow running.
            return {
                "status": "error",
                "message": "任务计划板更新失败（已忽略，不影响写作流程）",
                "reason": "internal_error",
                "error": str(e),
                "task_summary": {
                    "total": task_count,
                    "pending": pending,
                    "in_progress": in_progress,
                    "done": done,
                },
                "tasks": tasks_list,
            }


__all__ = [
    "ProjectOperations",
]
