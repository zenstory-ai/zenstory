"""Tests for MCP tool wrappers."""

import asyncio
import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agent.tools.mcp_tools import (
    create_file,
    delete_file,
    edit_file,
    query_files,
    update_project,
)
from agent.tools.tool_context import AgentRequestContext


def _parse_payload(result: dict) -> dict:
    text = result["content"][0]["text"]
    return json.loads(text)


def _mock_req_ctx(
    mock_executor: MagicMock,
    *,
    project_id: str = "proj-1",
    session_id: str | None = "sess-1",
    user_id: str | None = None,
) -> AgentRequestContext:
    req_ctx = AgentRequestContext(
        session=MagicMock(),
        user_id=user_id,
        project_id=project_id,
        session_id=session_id,
    )
    req_ctx.executor = MagicMock(return_value=mock_executor)
    return req_ctx


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_supports_empty_string_and_returns_compat_fields():
    mock_executor = MagicMock()
    mock_executor.update_project_status.return_value = {
        "project_id": "proj-1",
        "updated_fields": ["summary"],
        "current_status": {"summary": ""},
    }

    req_ctx = _mock_req_ctx(mock_executor)
    result = await update_project({"summary": ""}, request_context=req_ctx)

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["project_status"]["updated_fields"] == ["summary"]
    assert payload["data"]["updated_fields"] == ["summary"]
    assert payload["data"]["project_id"] == "proj-1"
    assert "current_status" not in payload["data"]
    assert payload["data"]["project_status"]["current_status"]["summary"] == ""

    mock_executor.update_project_status.assert_called_once_with(
        project_id="proj-1",
        summary="",
        current_phase=None,
        writing_style=None,
        notes=None,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_accepts_current_phase_camel_case_alias():
    mock_executor = MagicMock()
    mock_executor.update_project_status.return_value = {
        "project_id": "proj-1",
        "updated_fields": ["current_phase"],
        "current_status": {"current_phase": "已写至第5章"},
    }

    req_ctx = _mock_req_ctx(mock_executor)
    result = await update_project({"currentPhase": "已写至第5章"}, request_context=req_ctx)

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["updated_fields"] == ["current_phase"]
    assert "current_status" not in payload["data"]
    assert payload["data"]["project_status"]["current_status"]["current_phase"] == "已写至第5章"

    mock_executor.update_project_status.assert_called_once_with(
        project_id="proj-1",
        summary=None,
        current_phase="已写至第5章",
        writing_style=None,
        notes=None,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_passes_empty_tasks_array_when_present():
    mock_executor = MagicMock()
    mock_executor.execute_update_plan.return_value = {
        "tasks": [],
    }

    req_ctx = _mock_req_ctx(mock_executor)
    with patch("agent.tools.mcp_tools._suggest_auto_current_phase_from_drafts", return_value=None):
        result = await update_project({"tasks": []}, request_context=req_ctx)

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["plan"] == {"tasks": []}

    mock_executor.execute_update_plan.assert_called_once_with(
        session_id="sess-1",
        tasks=[],
        user_id=None,
        project_id="proj-1",
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_forwards_trusted_written_artifact_ids_outside_model_args():
    mock_executor = MagicMock()
    mock_executor.execute_update_plan.return_value = {"status": "success", "tasks": []}

    req_ctx = _mock_req_ctx(mock_executor)
    with patch("agent.tools.mcp_tools._suggest_auto_current_phase_from_drafts", return_value=None):
        result = await update_project(
            {"tasks": []},
            request_context=req_ctx,
            written_artifact_ids=["file-1"],
        )

    assert _parse_payload(result)["status"] == "success"
    mock_executor.execute_update_plan.assert_called_once_with(
        session_id="sess-1",
        tasks=[],
        user_id=None,
        project_id="proj-1",
        written_artifact_ids=["file-1"],
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_tasks_only_can_auto_sync_current_phase():
    mock_executor = MagicMock()
    mock_executor.execute_update_plan.return_value = {
        "tasks": [{"task": "写第5章", "status": "done"}],
    }
    mock_executor.update_project_status.return_value = {
        "project_id": "proj-1",
        "updated_fields": ["current_phase"],
        "current_status": {"current_phase": "已写至第5章"},
    }

    req_ctx = _mock_req_ctx(mock_executor)
    with patch(
        "agent.tools.mcp_tools._suggest_auto_current_phase_from_drafts",
        return_value="已写至第5章",
    ):
        result = await update_project(
            {"tasks": [{"task": "写第5章", "status": "done"}]},
            request_context=req_ctx,
        )

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["plan"]["tasks"] == [{"task": "写第5章", "status": "done"}]
    assert payload["data"]["updated_fields"] == ["current_phase"]
    assert "current_status" not in payload["data"]
    assert payload["data"]["project_status"]["current_status"]["current_phase"] == "已写至第5章"

    mock_executor.execute_update_plan.assert_called_once_with(
        session_id="sess-1",
        tasks=[{"task": "写第5章", "status": "done"}],
        user_id=None,
        project_id="proj-1",
    )
    mock_executor.update_project_status.assert_called_once_with(
        project_id="proj-1",
        summary=None,
        current_phase="已写至第5章",
        writing_style=None,
        notes=None,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_tasks_only_logs_info_not_warning():
    mock_executor = MagicMock()
    mock_executor.execute_update_plan.return_value = {
        "tasks": [{"task": "写第5章", "status": "done"}],
    }

    req_ctx = _mock_req_ctx(mock_executor)
    with patch(
        "agent.tools.mcp_tools._suggest_auto_current_phase_from_drafts",
        return_value=None,
    ), patch("agent.tools.mcp_tools.log_with_context") as mock_log:
        result = await update_project(
            {"tasks": [{"task": "写第5章", "status": "done"}]},
            request_context=req_ctx,
        )

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["plan"]["tasks"] == [{"task": "写第5章", "status": "done"}]
    mock_executor.update_project_status.assert_not_called()

    mock_log.assert_called_once()
    assert mock_log.call_args.args[1] == 20
    assert mock_log.call_args.args[2] == "update_project received task-board-only payload"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_tasks_only_auto_sync_failure_is_non_blocking():
    mock_executor = MagicMock()
    mock_executor.execute_update_plan.return_value = {
        "tasks": [{"task": "写第5章", "status": "done"}],
    }

    req_ctx = _mock_req_ctx(mock_executor)
    with patch(
        "agent.tools.mcp_tools._suggest_auto_current_phase_from_drafts",
        side_effect=RuntimeError("boom"),
    ), patch("agent.tools.mcp_tools.log_with_context") as mock_log:
        result = await update_project(
            {"tasks": [{"task": "写第5章", "status": "done"}]},
            request_context=req_ctx,
        )

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["plan"]["tasks"] == [{"task": "写第5章", "status": "done"}]
    assert "project_status" not in payload["data"]

    mock_executor.execute_update_plan.assert_called_once_with(
        session_id="sess-1",
        tasks=[{"task": "写第5章", "status": "done"}],
        user_id=None,
        project_id="proj-1",
    )
    mock_executor.update_project_status.assert_not_called()

    assert mock_log.call_count >= 2
    error_calls = [call for call in mock_log.call_args_list if call.args[1] == 40]
    assert error_calls


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("plan_status", ["ignored", "error"])
async def test_update_project_propagates_rejected_plan_without_phase_sync(
    plan_status,
):
    mock_executor = MagicMock()
    mock_executor.execute_update_plan.return_value = {
        "status": plan_status,
        "reason": "validation_rejected",
        "error": "invalid transition",
        "tasks": [],
    }

    req_ctx = _mock_req_ctx(mock_executor)
    with patch(
        "agent.tools.mcp_tools._suggest_auto_current_phase_from_drafts",
    ) as suggest_phase:
        result = await update_project(
            {"tasks": [{"task": "写第5章", "status": "done"}]},
            request_context=req_ctx,
        )

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert payload["error_type"] == "update_plan_rejected"
    assert payload["mutation_applied"] is False
    assert payload["data"]["plan"]["status"] == plan_status
    suggest_phase.assert_not_called()
    mock_executor.update_project_status.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_reports_partial_when_fields_update_but_plan_is_rejected():
    mock_executor = MagicMock()
    mock_executor.update_project_status.return_value = {
        "project_id": "proj-1",
        "updated_fields": ["summary"],
        "current_status": {"summary": "new"},
    }
    mock_executor.execute_update_plan.return_value = {
        "status": "ignored",
        "reason": "unverified_done",
        "tasks": [],
    }

    req_ctx = _mock_req_ctx(mock_executor)
    result = await update_project({"summary": "new", "tasks": []}, request_context=req_ctx)

    payload = _parse_payload(result)
    assert payload["status"] == "partial"
    assert payload["mutation_applied"] is True
    assert payload["data"]["updated_fields"] == ["summary"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_infers_order_from_episode_title_when_order_missing(db_session):
    """create_file tool should infer `order` from titles like 第8集 when caller omits order."""
    from models import File, Project, User

    suffix = uuid4().hex[:8]
    user = User(
        email=f"mcp-tools-order-{suffix}@example.com",
        username=f"mcp_tools_order_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"MCP Tools Order Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    folder = File(
        project_id=project.id,
        title="分集大纲",
        file_type="folder",
        order=0,
    )
    db_session.add(folder)
    db_session.commit()
    db_session.refresh(folder)

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-1",
    )
    result = await create_file(
        {
            "title": "第8集：测试",
            "file_type": "outline",
            "parent_id": folder.id,
            "content": "outline",
        },
        request_context=req_ctx,
    )

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["title"] == "第8集：测试"
    assert payload["data"]["order"] == 8


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_preserves_explicit_order_even_when_title_has_another_sequence(db_session):
    """A validated explicit order is structured input and wins over title parsing."""
    from models import File, Project, User

    suffix = uuid4().hex[:8]
    user = User(
        email=f"mcp-tools-order-fix-{suffix}@example.com",
        username=f"mcp_tools_order_fix_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"MCP Tools Order Fix Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    folder = File(
        project_id=project.id,
        title="正文",
        file_type="folder",
        order=0,
    )
    db_session.add(folder)
    db_session.commit()
    db_session.refresh(folder)

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-order-fix",
    )
    result = await create_file(
        {
            "title": "第58章 真相",
            "file_type": "draft",
            "parent_id": folder.id,
            "order": 580,
            "content": "content",
        },
        request_context=req_ctx,
    )

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["title"] == "第58章 真相"
    assert payload["data"]["order"] == 580


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_explicit_order_wins_title_sequence_for_chapter_files(db_session):
    """Chapter-like files also preserve a validated explicit order."""
    from models import File, Project, User

    suffix = uuid4().hex[:8]
    user = User(
        email=f"mcp-tools-order-lock-{suffix}@example.com",
        username=f"mcp_tools_order_lock_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"MCP Tools Order Lock Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    folder = File(
        project_id=project.id,
        title="正文",
        file_type="folder",
        order=0,
    )
    db_session.add(folder)
    db_session.commit()
    db_session.refresh(folder)

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-order-lock",
    )
    result = await create_file(
        {
            "title": "第58章 真相",
            "file_type": "draft",
            "parent_id": folder.id,
            "order": 1,
            "content": "content",
        },
        request_context=req_ctx,
    )

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["title"] == "第58章 真相"
    assert payload["data"]["order"] == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_edit_file_forwards_continue_on_error():
    mock_executor = MagicMock()
    mock_executor.edit_file.return_value = {"id": "f-1", "edits_applied": 1}

    req_ctx = _mock_req_ctx(mock_executor, session_id=None)
    result = await edit_file(
        {
            "id": "f-1",
            "edits": [{"op": "append", "text": "x"}],
            "continue_on_error": True,
        },
        request_context=req_ctx,
    )

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    mock_executor.edit_file.assert_called_once_with(
        id="f-1",
        edits=[{"op": "append", "text": "x"}],
        continue_on_error=True,
        normalize_quotes=True,
        read_snapshot_fn=None,
        exact_only=True,
        report_author_edits=True,
        body_guards=False,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_edit_file_reports_access_refusal_as_permission_denied():
    """F-27：权限拒绝发生在写入之前，要报 permission_denied 且明确一处都没改，
    不能落进「可能已套用」的通用分支。"""
    from agent.tools.permissions import ForbiddenError

    mock_executor = MagicMock()
    mock_executor.edit_file.side_effect = ForbiddenError("Access denied")

    req_ctx = _mock_req_ctx(mock_executor, session_id=None)
    result = await edit_file(
        {"id": "f-1", "edits": [{"op": "append", "text": "x"}]},
        request_context=req_ctx,
    )

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert payload["error_type"] == "permission_denied"
    assert payload["edits_applied"] == 0
    assert payload["mutation_applied"] is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_edit_file_accepts_file_id_alias():
    """Backward compatibility: accept file_id as an alias for id."""
    mock_executor = MagicMock()
    mock_executor.edit_file.return_value = {"id": "f-1", "edits_applied": 1}

    req_ctx = _mock_req_ctx(mock_executor, session_id=None)
    result = await edit_file(
        {
            "file_id": "f-1",
            "edits": [{"op": "append", "text": "x"}],
        },
        request_context=req_ctx,
    )

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    mock_executor.edit_file.assert_called_once_with(
        id="f-1",
        edits=[{"op": "append", "text": "x"}],
        continue_on_error=False,
        normalize_quotes=True,
        read_snapshot_fn=None,
        exact_only=True,
        report_author_edits=True,
        body_guards=False,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_result_payload_is_truncated_when_exceeding_limit():
    mock_executor = MagicMock()
    mock_executor.edit_file.return_value = {
        "id": "f-1",
        "content": "x" * 2000,
    }

    req_ctx = _mock_req_ctx(mock_executor, session_id=None)
    with patch("agent.tools.mcp_tools.TOOL_RESULT_MAX_CHARS", 180):
        result = await edit_file(
            {
                "id": "f-1",
                "edits": [{"op": "append", "text": "x"}],
            },
            request_context=req_ctx,
        )

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["truncated"] is True
    assert payload["max_chars"] == 180
    assert payload["original_length"] > 180
    assert payload["data"]["truncated"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_error_payload_is_truncated_when_exceeding_limit():
    mock_executor = MagicMock()
    mock_executor.edit_file.side_effect = ValueError("boom-" + ("x" * 2000))

    req_ctx = _mock_req_ctx(mock_executor, session_id=None)
    with patch("agent.tools.mcp_tools.TOOL_RESULT_MAX_CHARS", 180):
        result = await edit_file(
            {
                "id": "f-1",
                "edits": [{"op": "append", "text": "x"}],
            },
            request_context=req_ctx,
        )

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert payload["truncated"] is True
    assert payload["max_chars"] == 180
    assert payload["original_length"] > 180


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_files_forwards_new_wrapper_params():
    mock_executor = MagicMock()
    mock_executor.query_files.return_value = [{"id": "f-1"}]

    req_ctx = _mock_req_ctx(mock_executor, session_id=None)
    result = await query_files(
        {
            "query": "chapter",
            "response_mode": "summary",
            "content_preview_chars": 120,
            "include_content": False,
        },
        request_context=req_ctx,
    )

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    mock_executor.query_files.assert_called_once_with(
        project_id="proj-1",
        query="chapter",
        file_type=None,
        file_types=None,
        parent_id=None,
        limit=50,
        offset=0,
        response_mode="summary",
        content_preview_chars=120,
        include_content=False,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_files_surfaces_type_error_without_retrying_or_dropping_arguments():
    calls: list[dict] = []

    def _legacy_query_files(**kwargs):
        calls.append(dict(kwargs))
        if "response_mode" in kwargs:
            raise TypeError("query_files() got an unexpected keyword argument 'response_mode'")
        return [{"id": "f-legacy"}]

    mock_executor = MagicMock()
    mock_executor.query_files.side_effect = _legacy_query_files

    req_ctx = _mock_req_ctx(mock_executor, session_id=None)
    result = await query_files(
        {
            "query": "chapter",
            "response_mode": "full",
            "content_preview_chars": 500,
            "include_content": True,
        },
        request_context=req_ctx,
    )

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert "unexpected keyword argument 'response_mode'" in payload["error"]
    assert len(calls) == 1
    assert "response_mode" in calls[0]
    assert calls[0]["project_id"] == "proj-1"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_files_does_not_interpret_width_or_id_words_in_type_error():
    mock_executor = MagicMock()
    mock_executor.query_files.side_effect = TypeError("width/id conversion failed inside executor")

    req_ctx = _mock_req_ctx(mock_executor, session_id=None)
    result = await query_files(
        {"id": "file-1", "response_mode": "full"},
        request_context=req_ctx,
    )

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert payload["error"] == "width/id conversion failed inside executor"
    mock_executor.query_files.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool_func", "sync_helper", "args"),
    [
        (create_file, "_create_file_sync", {"title": "T", "file_type": "draft"}),
        (edit_file, "_edit_file_sync", {"id": "file-1", "edits": []}),
        (delete_file, "_delete_file_sync", {"id": "file-1"}),
        (query_files, "_query_files_sync", {"query": "hero"}),
        (update_project, "_update_project_sync", {"tasks": []}),
    ],
)
async def test_offloaded_wrappers_cleanup_owned_session(tool_func, sync_helper, args):
    """Offloaded wrappers run sync helpers via asyncio.to_thread with explicit request_context."""

    async def _run_inline(func, *call_args):
        return func(*call_args)

    req_ctx = AgentRequestContext(session=None, user_id="u1", project_id="proj-1", session_id="sess-1")
    with (
        patch("agent.tools.mcp_tools.asyncio.to_thread", side_effect=_run_inline) as mock_to_thread,
        patch(
            f"agent.tools.mcp_tools.{sync_helper}",
            return_value={"content": [{"type": "text", "text": '{"status":"success"}'}]},
        ) as mock_sync,
    ):
        result = await tool_func(args, request_context=req_ctx)

    assert _parse_payload(result)["status"] == "success"
    mock_to_thread.assert_called_once()
    mock_sync.assert_called_once()
    assert mock_sync.call_args.args[0] == args
    assert mock_sync.call_args.args[1] is req_ctx


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_empty_file_sets_pending_marker_on_offload_path(db_session):
    """Regression: the pending-empty-file marker must survive the Postgres-style
    offload path.

    When create_file offloads _create_file_sync with asyncio.to_thread
    (production/Postgres), the sync impl runs in a COPIED context. The marker it
    sets there must still be visible in the caller's (event-loop) context —
    otherwise the consecutive-empty-file guard and the loop agent's empty-file
    correction are both disabled in production.
    """
    from models import Project, User

    suffix = uuid4().hex[:8]
    user = User(
        email=f"mcp-empty-{suffix}@example.com",
        username=f"mcp_empty_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"MCP Empty File Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-empty",
    )
    result = await create_file(
        {
            "title": "空章节",
            "file_type": "draft",
            "content": "",  # empty -> expects <file>…</file> streaming to follow
        },
        request_context=req_ctx,
    )

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    # The marker must be visible in THIS (caller/event-loop) context.
    assert req_ctx.has_pending_empty_file() is True
    pending = req_ctx.get_pending_empty_file()
    assert pending is not None
    assert pending["file_id"] == payload["data"]["id"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_consecutive_empty_create_file_rejected_across_tool_tasks(db_session):
    """连续创建第二个空文件必须被拦截 — 即使两次 create_file 分别运行在
    SDK 为每次工具调用包的独立 asyncio 子任务里。

    第一个空文件的 pending 标记若只存在于子任务的 contextvars 副本中，第二次
    create_file（另一个子任务）就看不到守卫，stream_adapter 会无条件覆盖捕获
    目标，第一个文件已流出的正文被静默丢弃。
    """
    from models import Project, User

    suffix = uuid4().hex[:8]
    user = User(
        email=f"mcp-guard-{suffix}@example.com",
        username=f"mcp_guard_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"MCP Guard Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-guard",
    )
    first = await asyncio.create_task(
        create_file(
            {
                "title": "第1章",
                "file_type": "draft",
                "content": "",
            },
            request_context=req_ctx,
        )
    )
    first_payload = _parse_payload(first)
    assert first_payload["status"] == "success"
    # 子任务里设置的标记必须在父上下文可见
    assert req_ctx.has_pending_empty_file() is True

    second = await asyncio.create_task(
        create_file(
            {
                "title": "第2章",
                "file_type": "draft",
                "content": "",
            },
            request_context=req_ctx,
        )
    )
    second_payload = _parse_payload(second)
    assert second_payload["status"] == "error"
    assert "第1章" in second_payload["error"]


def _make_user_and_project(db_session, tag: str):
    """建一个可用于 create_file 的真实 user + novel project。"""
    from models import Project, User

    suffix = uuid4().hex[:8]
    user = User(
        email=f"mcp-{tag}-{suffix}@example.com",
        username=f"mcp_{tag}_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"MCP {tag} Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_folder_does_not_set_pending_empty_file_marker(db_session):
    """创建文件夹不得置 pending-empty-file 标记，否则本轮后续建档被永久阻断。

    create_file 的 schema 没有 content 参数（content 恒为 ""），文件夹又永远不会
    收到 <file>…</file> 正文，所以标记一旦置上就没有清除路径：分卷/分章结构
    （先建「第一卷」文件夹再在其下建章节）会在第二次 create_file 上直接报错。
    """
    user, project = _make_user_and_project(db_session, "folder")

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-folder",
    )
    folder = await asyncio.create_task(
        create_file(
            {
                "title": "第一卷",
                "file_type": "folder",
            },
            request_context=req_ctx,
        )
    )
    folder_payload = _parse_payload(folder)
    assert folder_payload["status"] == "success"
    assert folder_payload["data"]["file_type"] == "folder"
    assert req_ctx.has_pending_empty_file() is False

    chapter = await asyncio.create_task(
        create_file(
            {
                "title": "第1章",
                "file_type": "draft",
                "parent_id": folder_payload["data"]["id"],
            },
            request_context=req_ctx,
        )
    )
    chapter_payload = _parse_payload(chapter)
    assert chapter_payload["status"] == "success"
    assert chapter_payload["data"]["parent_id"] == folder_payload["data"]["id"]

    # 章节（真正等待流式正文的文件）仍然要置标记
    pending = req_ctx.get_pending_empty_file()
    assert pending is not None
    assert pending["file_id"] == chapter_payload["data"]["id"]
    assert pending["title"] == "第1章"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_folder_marker_free_on_offload_path(db_session):
    """Postgres 卸载路径（asyncio.to_thread）同样不得给文件夹置标记。"""
    user, project = _make_user_and_project(db_session, "folderoff")

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-folder-offload",
    )
    folder = await create_file(
        {
            "title": "第二卷",
            "file_type": "folder",
        },
        request_context=req_ctx,
    )
    assert _parse_payload(folder)["status"] == "success"
    assert req_ctx.has_pending_empty_file() is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_folder_still_blocked_while_file_awaits_content(db_session):
    """已有待写入正文的文件时，建文件夹仍被拦截（保留防内容丢失的守卫）。

    stream_adapter 会把最近一次 create_file 的返回无条件设为流式捕获目标，
    因此在正文写完前插入任何 create_file（含文件夹）都会让前一个文件的正文写错
    位置；这里的拦截是可恢复的：写完正文后标记清除，建文件夹即可继续。
    """
    user, project = _make_user_and_project(db_session, "folderguard")

    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-folder-guard",
    )
    chapter = await create_file({"title": "第1章", "file_type": "draft"}, request_context=req_ctx)
    assert _parse_payload(chapter)["status"] == "success"
    assert req_ctx.has_pending_empty_file() is True

    blocked = await create_file({"title": "第一卷", "file_type": "folder"}, request_context=req_ctx)
    blocked_payload = _parse_payload(blocked)
    assert blocked_payload["status"] == "error"
    assert "第1章" in blocked_payload["error"]

    # 正文写入后（stream_adapter 清除标记）文件夹可以正常创建
    req_ctx.clear_pending_empty_file()
    folder = await create_file({"title": "第一卷", "file_type": "folder"}, request_context=req_ctx)
    assert _parse_payload(folder)["status"] == "success"
    assert req_ctx.has_pending_empty_file() is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_clears_stale_pending_marker_after_edit_file_wrote_body(db_session):
    """上一个空文件的正文已经用 edit_file(op=append) 写进去：标记陈旧，不能再挡新文件。"""
    user, project = _make_user_and_project(db_session, "stale")
    req_ctx = AgentRequestContext(
        session=db_session, user_id=user.id, project_id=project.id, session_id="sess-stale"
    )
    first = _parse_payload(await create_file({"title": "陈砚", "file_type": "character"}, request_context=req_ctx))
    assert first["status"] == "success"
    first_id = first["data"]["id"]

    edited = _parse_payload(
        await edit_file(
            {"id": first_id, "edits": [{"op": "append", "text": "陈砚，二十七岁。"}]},
            request_context=req_ctx,
        )
    )
    assert edited["status"] == "success"
    # edit_file 不清标记（这是陈旧标记的来源）
    assert req_ctx.has_pending_empty_file() is True

    second = _parse_payload(await create_file({"title": "苏晚", "file_type": "character"}, request_context=req_ctx))
    assert second["status"] == "success"
    pending_ids = [entry["file_id"] for entry in req_ctx.get_pending_empty_files()]
    assert pending_ids == [second["data"]["id"]]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_rejection_names_the_unblocking_action(db_session):
    """上一个空文件确实还没正文：拒绝信息点名文件 id 和解除办法，并带顺序性 error_type。"""
    user, project = _make_user_and_project(db_session, "unwritten")
    req_ctx = AgentRequestContext(
        session=db_session, user_id=user.id, project_id=project.id, session_id="sess-unwritten"
    )
    first = _parse_payload(await create_file({"title": "沈砚", "file_type": "character"}, request_context=req_ctx))
    first_id = first["data"]["id"]

    rejected = _parse_payload(await create_file({"title": "苏晚", "file_type": "character"}, request_context=req_ctx))
    assert rejected["status"] == "error"
    assert rejected["error_type"] == "pending_empty_file_unwritten"
    assert rejected["pending_file_id"] == first_id
    assert "<file>" in rejected["error"]
    assert "苏晚" in rejected["error"]
    # 标记仍指向那个真正没写正文的文件
    assert [entry["file_id"] for entry in req_ctx.get_pending_empty_files()] == [first_id]


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("args", "expected_limit"),
    [
        ({"response_mode": "full"}, 10),
        ({"include_content": "true"}, 10),
        ({"response_mode": "full", "limit": 30}, 30),
        ({"id": "f-1", "response_mode": "full"}, 50),
        ({}, 50),
        ({"response_mode": "summary"}, 50),
    ],
)
async def test_query_files_full_listing_defaults_to_small_limit(args, expected_limit):
    mock_executor = MagicMock()
    mock_executor.query_files.return_value = []

    req_ctx = _mock_req_ctx(mock_executor, session_id=None)
    await query_files(args, request_context=req_ctx)

    assert mock_executor.query_files.call_args.kwargs["limit"] == expected_limit


@pytest.mark.asyncio
@pytest.mark.unit
async def test_oversized_query_result_keeps_every_file_identity_and_truncates_content():
    """超限时保留每条的 id/title/file_type/status，只截 content，而不是整体换成占位符。"""
    files = [
        {"id": f"f-{index}", "title": f"第{index}章", "file_type": "draft", "status": "done", "content": "字" * 3_000}
        for index in range(8)
    ]
    mock_executor = MagicMock()
    mock_executor.query_files.return_value = files

    req_ctx = _mock_req_ctx(mock_executor)
    with patch("agent.tools.mcp_tools.TOOL_RESULT_MAX_CHARS", 8_000):
        result = await query_files({"response_mode": "full", "limit": 8}, request_context=req_ctx)

    text = result["content"][0]["text"]
    assert len(text) <= 8_000
    payload = json.loads(text)
    assert payload["status"] == "success"
    assert payload["truncated"] is True
    assert 'query_files(id=…, response_mode="full")' in payload["note"]
    assert [item["id"] for item in payload["data"]] == [file["id"] for file in files]
    for item, original in zip(payload["data"], files, strict=True):
        assert item["title"] == original["title"]
        assert item["file_type"] == "draft"
        assert item["status"] == "done"
        assert item["content_truncated"] is True
        assert item["content_length"] == 3_000
        assert original["content"].startswith(item["content"])


@pytest.mark.asyncio
@pytest.mark.unit
async def test_oversized_parallel_payload_keeps_failure_signal():
    from agent.tools.mcp_tools import _make_result

    payload = {
        "status": "success",
        "mutation_applied": False,
        "data": {
            "any_failed": True,
            "failed": 1,
            "tasks": [
                {"id": "t1", "status": "failed", "error": "boom", "result": None},
                {"id": "t2", "status": "completed", "error": None, "result": {"content": "y" * 50_000}},
            ],
        },
    }
    with patch("agent.tools.mcp_tools.TOOL_RESULT_MAX_CHARS", 4_000):
        text = _make_result(payload, tool_name="parallel_execute")["content"][0]["text"]

    compact = json.loads(text)
    assert len(text) <= 4_000
    assert compact["mutation_applied"] is False
    assert compact["data"]["any_failed"] is True
    assert compact["data"]["tasks"][0] == {"id": "t1", "status": "failed", "error": "boom", "result": None}
    assert compact["data"]["tasks"][1]["result"]["content_truncated"] is True


# ---------------------------------------------------------------------------
# 同一条回复里 "<file>上一集</file> + create_file(下一集)"：on_llm_end 在执行
# 工具前标记上一集正文已写出，守卫只被尚未写出正文的条目挡住。
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_streamed_entry_no_longer_blocks_reservation_but_unwritten_one_does():
    from agent.tools.mcp_tools import _PendingEmptyFileState

    state = _PendingEmptyFileState()
    state.add("ep1", "第1集")

    # 未标记：真实的"正文还没写就再建"照旧被拒
    assert state.try_reserve("t1", "第2集") == (False, "第1集")

    state.mark_latest_body_streamed()
    assert state.try_reserve("t2", "第2集") == (True, None)
    state.bind("t2", "ep2", "第2集")
    # 条目本身不删：仍由 adapter 落库后按 file_id 清除
    assert [e["file_id"] for e in state.snapshot()] == ["ep1", "ep2"]
    assert state.has_pending() is True
    assert state.latest() == {"file_id": "ep2", "title": "第2集"}

    # 第2集还没写正文：第3集被第2集（而不是已写出的第1集）挡住
    assert state.try_reserve("t3", "第3集") == (False, "第2集")


@pytest.mark.unit
def test_streamed_entry_still_blocked_by_in_flight_reservation():
    from agent.tools.mcp_tools import _PendingEmptyFileState

    state = _PendingEmptyFileState()
    state.add("ep1", "第1集")
    state.mark_body_streamed("ep1")
    assert state.try_reserve("t1", "第2集") == (True, None)
    # 第2集还在建库途中（未 bind）：并发的第3集仍被拒
    assert state.try_reserve("t2", "第3集") == (False, "第2集")


@pytest.mark.unit
def test_streamed_mark_is_reset_when_same_file_pends_again():
    from agent.tools.mcp_tools import _PendingEmptyFileState

    state = _PendingEmptyFileState()
    state.add("ep1", "第1集")
    state.mark_body_streamed("ep1")
    # 幂等复用命中同一文件、重新等待正文：旧标记作废
    assert state.try_reserve("t1", "第1集") == (True, None)
    state.bind("t1", "ep1", "第1集")
    assert state.try_reserve("t2", "第2集") == (False, "第1集")

    state.mark_body_streamed("ep1")
    state.add("ep1", "第1集")
    assert state.try_reserve("t3", "第2集") == (False, "第1集")


@pytest.mark.unit
def test_streamed_mark_cleared_with_entries_and_ignored_for_unknown_ids():
    from agent.tools.mcp_tools import _PendingEmptyFileState

    state = _PendingEmptyFileState()
    state.mark_body_streamed("ghost")
    state.mark_latest_body_streamed()
    state.add("ghost", "幽灵")
    # 未登记时的标记是 no-op，不会让之后置位的同 id 条目直接放行
    assert state.try_reserve("t1", "第1集") == (False, "幽灵")

    state.mark_body_streamed("ghost")
    state.discard("ghost")
    state.add("ghost", "幽灵")
    assert state.try_reserve("t2", "第1集") == (False, "幽灵")

    state.mark_body_streamed("ghost")
    state.clear_all()
    state.add("ghost", "幽灵")
    assert state.try_reserve("t3", "第1集") == (False, "幽灵")

    state.mark_body_streamed("ghost")
    state.discard()
    state.add("ghost", "幽灵")
    assert state.try_reserve("t4", "第1集") == (False, "幽灵")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_allowed_after_previous_body_streamed_in_same_reply(db_session):
    """上一集的正文已在本条回复里写完（库里还没落）：紧跟的 create_file(下一集) 必须成功。"""
    user, project = _make_user_and_project(db_session, "streamed")
    req_ctx = AgentRequestContext(
        session=db_session, user_id=user.id, project_id=project.id, session_id="sess-streamed"
    )
    first = _parse_payload(await create_file({"title": "第1集", "file_type": "draft"}, request_context=req_ctx))
    first_id = first["data"]["id"]

    req_ctx.mark_latest_pending_body_streamed()
    second = _parse_payload(
        await asyncio.create_task(create_file({"title": "第2集", "file_type": "draft"}, request_context=req_ctx))
    )
    assert second["status"] == "success"
    second_id = second["data"]["id"]
    # 两条都还在：第1集等 adapter 落库清除，第2集等自己的正文
    assert [e["file_id"] for e in req_ctx.get_pending_empty_files()] == [
        first_id,
        second_id,
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_two_create_files_without_body_in_between_still_rejected(db_session):
    """同一条回复里只有两个 create_file、中间没写 <file> 正文：第二个照旧被拒。"""
    user, project = _make_user_and_project(db_session, "doublecreate")
    req_ctx = AgentRequestContext(
        session=db_session, user_id=user.id, project_id=project.id, session_id="sess-double"
    )
    first = _parse_payload(await create_file({"title": "第4集", "file_type": "draft"}, request_context=req_ctx))
    assert first["status"] == "success"
    rejected = _parse_payload(await create_file({"title": "第5集", "file_type": "draft"}, request_context=req_ctx))
    assert rejected["status"] == "error"
    assert rejected["error_type"] == "pending_empty_file_unwritten"
    assert rejected["pending_file_id"] == first["data"]["id"]


def _count_files_titled(db_session, project_id: str, title: str) -> int:
    from sqlmodel import select

    from models import File

    rows = db_session.exec(
        select(File).where(
            File.project_id == project_id,
            File.title == title,
            File.is_deleted.is_(False),
        )
    ).all()
    return len(list(rows))


@pytest.mark.asyncio
@pytest.mark.unit
async def test_repeat_create_file_of_the_pending_empty_file_returns_that_file(db_session):
    """刚建好、正文还空着的文件，模型下一次回复又用同样参数 create_file：返回同一个文件让它写正文，不报错。"""
    user, project = _make_user_and_project(db_session, "repeatcreate")
    req_ctx = AgentRequestContext(
        session=db_session, user_id=user.id, project_id=project.id, session_id="sess-repeat"
    )
    first = _parse_payload(await create_file({"title": "灵脉与修炼等级", "file_type": "lore"}, request_context=req_ctx))
    assert first["status"] == "success"
    first_id = first["data"]["id"]

    repeat = _parse_payload(
        await asyncio.create_task(
            create_file({"title": "灵脉与修炼等级", "file_type": "lore"}, request_context=req_ctx)
        )
    )
    assert repeat["status"] == "success"
    assert repeat["data"]["id"] == first_id
    assert repeat["data"]["content"] == ""
    # 不是「复用已有文件」：它就是本轮刚建的空文件，适配器照常重新等 <file> 正文
    assert repeat["data"].get("reused_existing") is not True
    assert repeat["mutation_applied"] is False
    assert "<file>" in repeat["data"]["next_step"]
    assert _count_files_titled(db_session, project.id, "灵脉与修炼等级") == 1
    assert [e["file_id"] for e in req_ctx.get_pending_empty_files()] == [first_id]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_repeat_create_file_with_different_type_or_parent_still_rejected(db_session):
    """同标题但类型或父目录不同，不是同一个文件：照旧按一次一个文件拒绝。"""
    user, project = _make_user_and_project(db_session, "repeatmismatch")
    req_ctx = AgentRequestContext(
        session=db_session, user_id=user.id, project_id=project.id, session_id="sess-mismatch"
    )
    folder = _parse_payload(await create_file({"title": "卷一", "file_type": "folder"}, request_context=req_ctx))
    folder_id = folder["data"]["id"]
    first = _parse_payload(
        await create_file({"title": "雾港码头", "file_type": "draft", "parent_id": folder_id}, request_context=req_ctx)
    )
    assert first["status"] == "success"

    other_type = _parse_payload(
        await create_file({"title": "雾港码头", "file_type": "lore", "parent_id": folder_id}, request_context=req_ctx)
    )
    assert other_type["status"] == "error"
    assert other_type["error_type"] == "pending_empty_file_unwritten"

    other_parent = _parse_payload(
        await create_file({"title": "雾港码头", "file_type": "draft"}, request_context=req_ctx)
    )
    assert other_parent["status"] == "error"
    assert other_parent["error_type"] == "pending_empty_file_unwritten"
    assert _count_files_titled(db_session, project.id, "雾港码头") == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_repeat_create_file_while_another_create_is_in_flight_still_rejected(db_session):
    """有别的 create_file 正在建库途中：不认作重复，照旧拒绝。"""
    user, project = _make_user_and_project(db_session, "repeatinflight")
    req_ctx = AgentRequestContext(
        session=db_session, user_id=user.id, project_id=project.id, session_id="sess-inflight"
    )
    first = _parse_payload(await create_file({"title": "周砚", "file_type": "character"}, request_context=req_ctx))
    assert first["status"] == "success"
    req_ctx.pending_empty_file_state._reservations["ticket-inflight"] = "许青"

    repeat = _parse_payload(await create_file({"title": "周砚", "file_type": "character"}, request_context=req_ctx))
    assert repeat["status"] == "error"
    assert repeat["error_type"] == "pending_empty_file_unwritten"


def _set_author_round(
    db_session, user, project, message: str, *, notes_append_allowed: bool = False
) -> AgentRequestContext:
    return AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id=f"sess-notes-{uuid4().hex[:6]}",
        author_message=message,
        notes_append_allowed=notes_append_allowed,
    )


def _stored_notes(db_session, project) -> str:
    db_session.expire_all()
    from models import Project

    return db_session.get(Project, project.id).notes or ""


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_appends_remembered_notes_and_reports_them(db_session):
    """Explicit remember: new entries are appended as author notes; author text stays byte-for-byte."""
    from config.project_notes import CONFIRMED_HEADER, UNLABELED_HEADER

    user, project = _make_user_and_project(db_session, f"notes-add-{uuid4().hex[:5]}")
    existing = f"作者旧约定\n{CONFIRMED_HEADER}\n作者事实"
    project.notes = existing
    db_session.add(project)
    db_session.commit()
    req_ctx = _set_author_round(
        db_session, user, project, "记住：女主要到第五章才出场；主角左手有旧伤，阴天会疼", notes_append_allowed=True
    )
    payload = _parse_payload(
        await update_project(
            {
                "summary": "新摘要",
                "notes": "作者事实\n女主要到第五章才出场；主角左手有旧伤，阴天会疼",
            },
            request_context=req_ctx,
        )
    )
    assert payload["status"] == "success"
    data = payload["data"]
    assert data["notes_added"] == ["女主要到第五章才出场", "主角左手有旧伤，阴天会疼"]
    assert "notes_not_saved" not in data and "notes_note" not in data
    assert data["updated_fields"] == ["summary", "notes"]
    stored = _stored_notes(db_session, project)
    assert stored == f"{existing}\n{UNLABELED_HEADER}\n女主要到第五章才出场\n主角左手有旧伤，阴天会疼"
    assert "current_status" not in data
    assert data["project_status"]["current_status"]["notes"] == stored

    # Copying the notes back (or passing nothing new) writes nothing: no fake output.
    copied = _parse_payload(await update_project({"notes": "女主要到第五章才出场"}, request_context=req_ctx))
    assert "notes_added" not in copied["data"]
    assert copied["data"]["updated_fields"] == []
    assert _stored_notes(db_session, project) == stored


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_notes_over_limit_save_nothing_and_say_so(db_session):
    from agent.tools.mcp_tools import NOTES_FULL_NOTE

    user, project = _make_user_and_project(db_session, f"notes-full-{uuid4().hex[:5]}")
    existing = "甲" * 3997
    project.notes = existing
    db_session.add(project)
    db_session.commit()
    req_ctx = _set_author_round(db_session, user, project, "记住：主角怕水", notes_append_allowed=True)
    payload = _parse_payload(
        await update_project({"summary": "新摘要", "notes": "主角怕水"}, request_context=req_ctx)
    )

    assert payload["status"] == "success"
    assert "notes_added" not in payload["data"]
    assert payload["data"]["notes_note"] == NOTES_FULL_NOTE
    assert payload["data"]["updated_fields"] == ["summary"]
    assert _stored_notes(db_session, project) == existing


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_partial_plan_rejection_still_reports_added_notes(db_session, monkeypatch):
    from agent.tools.file_ops import FileToolExecutor

    user, project = _make_user_and_project(db_session, f"notes-partial-{uuid4().hex[:5]}")
    monkeypatch.setattr(
        FileToolExecutor,
        "execute_update_plan",
        lambda self, **kwargs: {"status": "error", "error": "任务计划板未更新"},
    )
    req_ctx = _set_author_round(db_session, user, project, "记住：主角怕水", notes_append_allowed=True)
    payload = _parse_payload(
        await update_project({"summary": "新摘要", "notes": "主角怕水", "tasks": []}, request_context=req_ctx)
    )

    assert payload["status"] == "partial"
    assert payload["data"]["notes_added"] == ["主角怕水"]
    assert payload["data"]["updated_fields"] == ["summary", "notes"]
    assert _stored_notes(db_session, project) == "主角怕水"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_preserves_existing_authoritative_notes_when_model_omits_or_rewrites(db_session):
    from config.project_notes import CONFIRMED_HEADER

    user, project = _make_user_and_project(db_session, "notes-preserve")
    existing = f"旧约定\n{CONFIRMED_HEADER}\n主角不会游泳"
    project.notes = existing
    db_session.add(project)
    db_session.commit()
    req_ctx = _set_author_round(db_session, user, project, "记住：主角会游泳", notes_append_allowed=True)
    for notes in ("", "主角不会游泳", f"{CONFIRMED_HEADER}\n旧约定"):
        payload = _parse_payload(await update_project({"notes": notes}, request_context=req_ctx))
        assert payload["status"] == "success"
        assert "notes_added" not in payload["data"]
        assert _stored_notes(db_session, project) == existing
    # A rewrite cannot replace the author's entry; it is only appended after it.
    payload = _parse_payload(await update_project({"notes": "主角会游泳"}, request_context=req_ctx))
    assert payload["data"]["notes_added"] == ["主角会游泳"]
    assert _stored_notes(db_session, project).startswith(existing + "\n")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_removes_legacy_proposal_block_on_write(db_session):
    from config.project_notes import CONFIRMED_HEADER

    user, project = _make_user_and_project(db_session, f"notes-legacy-{uuid4().hex[:5]}")
    author_part = f"作者旧约定\n{CONFIRMED_HEADER}\n作者事实"
    legacy_stored = f"{author_part}\n\n【AI 提议·待作者确认】\n沈度是关键配角；北境封印"
    project.notes = legacy_stored
    db_session.add(project)
    db_session.commit()
    req_ctx = _set_author_round(db_session, user, project, "记住本轮的新约定", notes_append_allowed=True)
    status_only = _parse_payload(await update_project({"current_phase": "第1章"}, request_context=req_ctx))
    assert "current_status" not in status_only["data"]
    assert status_only["data"]["project_status"]["current_status"]["notes"] == author_part

    # The model copies the notes back with the legacy block: nothing is added or written
    # (no billable output just for a cleanup that reads already apply).
    copied = _parse_payload(
        await update_project(
            {"notes": "作者旧约定\n作者事实\n\n【AI 提议·待作者确认】\n沈度是关键配角；北境封印"},
            request_context=req_ctx,
        )
    )
    assert "notes_added" not in copied["data"]
    assert copied["data"]["updated_fields"] == []
    assert "current_status" not in copied["data"]
    assert copied["data"]["project_status"]["current_status"]["notes"] == author_part
    assert _stored_notes(db_session, project) == legacy_stored

    # The next real append writes the author part, the new entry, and no legacy block.
    payload = _parse_payload(await update_project({"notes": "主角怕水"}, request_context=req_ctx))

    assert payload["status"] == "success"
    assert payload["data"]["notes_added"] == ["主角怕水"]
    assert _stored_notes(db_session, project) == f"{author_part}\n【未标来源】\n主角怕水"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_merges_notes_against_author_patch_committed_after_tool_begins(
    db_session,
    monkeypatch,
):
    """The locked project row, not an early MCP read, is the notes merge baseline."""
    from sqlmodel import Session

    from agent.tools.file_ops import FileToolExecutor
    from api.projects import PatchProjectRequest, patch_project
    from config.project_notes import CONFIRMED_HEADER

    user, project = _make_user_and_project(db_session, "notes-race")
    project.notes = f"{CONFIRMED_HEADER}\n旧事实"
    db_session.add(project)
    db_session.commit()
    engine = db_session.get_bind()
    original_update = FileToolExecutor.update_project_status
    interleaved = False

    def update_after_author_patch(executor, *args, **kwargs):
        nonlocal interleaved
        if not interleaved:
            interleaved = True
            with Session(engine) as author_session:
                patch_project(
                    project.id,
                    PatchProjectRequest(notes=f"{CONFIRMED_HEADER}\n旧事实\n作者刚确认"),
                    current_user=user,
                    session=author_session,
                )
        return original_update(executor, *args, **kwargs)

    monkeypatch.setattr(FileToolExecutor, "update_project_status", update_after_author_patch)
    req_ctx = _set_author_round(db_session, user, project, "记住本轮的新约定", notes_append_allowed=True)
    payload = _parse_payload(await update_project({"notes": "旧事实\n模型新增推断"}, request_context=req_ctx))

    assert payload["status"] == "success"
    assert payload["data"]["notes_added"] == ["模型新增推断"]
    assert _stored_notes(db_session, project) == (
        f"{CONFIRMED_HEADER}\n旧事实\n作者刚确认\n【未标来源】\n模型新增推断"
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_keyword_query_returns_verbatim_match_snippets(db_session):
    """Production (2026-10-21): a rename across files took 27 full reads because keyword search
    only listed file names; the matches let the model edit without reading every file."""
    from models import File, Project, User

    suffix = uuid4().hex[:8]
    user = User(email=f"mcp-q-{suffix}@example.com", username=f"mcp_q_{suffix}", hashed_password="hashed",
                email_verified=True, is_active=True)
    db_session.add(user)
    db_session.commit()
    project = Project(name=f"Q {suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    body = "开头。" * 40 + "Dax 举起银刀。" + "中间。" * 40 + "碗不到，银刀不能落。"
    hit = File(project_id=project.id, title="第46集", file_type="draft", content=body)
    miss = File(project_id=project.id, title="第1集", file_type="draft", content="没有那个词。")
    db_session.add_all([hit, miss])
    db_session.commit()

    req_ctx = AgentRequestContext(session=db_session, user_id=user.id, project_id=project.id, session_id="sess-q")
    payload = _parse_payload(await query_files({"query": "银刀"}, request_context=req_ctx))

    rows = {row["title"]: row for row in payload["data"]}
    assert set(rows) == {"第46集"}
    assert rows["第46集"]["match_count"] == 2
    snippets = rows["第46集"]["matches"]
    assert len(snippets) == 2 and all("银刀" in s and s in body for s in snippets)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("loop_agent", [True, False])
async def test_empty_create_file_only_skips_the_body_turn_for_the_loop_agent(db_session, loop_agent):
    """Production (2026-10-21): 「建立第10章、第11章，不用写内容，建立标题就行」 was refused four times and tripped
    the breaker, because every empty create_file waited for a <file> body. The loop agent's empty=true is its own
    judgement; without the loop agent `empty` does not switch off the body turn."""
    from agent.tools.registry import LOOP_AGENT_TYPE

    user, project = _make_user_and_project(db_session, f"empty{int(loop_agent)}")
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-empty",
        current_agent=LOOP_AGENT_TYPE if loop_agent else None,
    )
    first = _parse_payload(
        await create_file({"title": "第一卷大纲", "file_type": "outline", "empty": True}, request_context=req_ctx)
    )
    second = _parse_payload(
        await create_file({"title": "第二卷大纲", "file_type": "outline", "empty": True}, request_context=req_ctx)
    )

    assert first["status"] == "success"
    if not loop_agent:
        assert first["data"]["awaiting_body"] is True
        assert second["error_type"] == "pending_empty_file_unwritten"
    else:
        for payload in (first, second):
            assert payload["status"] == "success"
            assert payload["data"]["empty_by_request"] is True
            assert "awaiting_body" not in payload["data"]


# ---------------------------------------------------------------------------
# 工具动作不再写 artifact ledger，超限结果不再持久化 / 回填（D-02）：
# 写工具和超限截断都不应为此另开数据库 session。
# ---------------------------------------------------------------------------


class _SessionFactorySpy:
    """只计数，任何调用都说明有人另开了 session。"""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("tool path opened a separate session")


@pytest.fixture
def session_factory_spy(monkeypatch):
    import database

    spy = _SessionFactorySpy()
    monkeypatch.setattr(database, "create_session", spy)
    yield spy


@pytest.mark.unit
def test_oversized_tool_result_is_bounded_without_opening_a_session(session_factory_spy):
    from agent.tools.mcp_tools import _make_result

    payload = {
        "status": "success",
        "data": [{"id": "f-1", "title": "第一章", "content": "字" * 300_000}],
    }
    text = _make_result(payload, tool_name="query_files")["content"][0]["text"]

    compact = json.loads(text)
    assert session_factory_spy.calls == 0
    assert "overflow_ref" not in compact
    assert compact["truncated"] is True
    assert compact["data"][0]["id"] == "f-1"
    assert compact["data"][0]["content_truncated"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_successful_writes_do_not_open_a_ledger_session(session_factory_spy):
    mock_executor = MagicMock()
    mock_executor.create_file.return_value = {"id": "f-1", "title": "第一章", "file_type": "draft", "content": "正文"}
    mock_executor.delete_file_with_meta.return_value = {"id": "f-1", "title": "第一章", "file_type": "draft"}
    mock_executor.update_project_status.return_value = {
        "project_id": "proj-1",
        "updated_fields": ["summary"],
        "current_status": {"summary": "新"},
    }

    req_ctx = _mock_req_ctx(mock_executor, user_id="user-1", project_id="proj-1", session_id="sess-1")
    created = _parse_payload(
        await create_file({"title": "第一章", "file_type": "draft", "content": "正文"}, request_context=req_ctx)
    )
    deleted = _parse_payload(await delete_file({"id": "f-1"}, request_context=req_ctx))
    updated = _parse_payload(await update_project({"summary": "新"}, request_context=req_ctx))

    assert [created["status"], deleted["status"], updated["status"]] == ["success"] * 3
    assert session_factory_spy.calls == 0


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("author_edit", ["", "主角左手已经痊愈"])
async def test_deleted_or_edited_notes_are_not_replayed_on_ordinary_writing(db_session, author_edit):
    """Reproduce memory -> manual PATCH -> ordinary writing in the same chat."""
    from api.projects import PatchProjectRequest, patch_project

    user, project = _make_user_and_project(db_session, f"notes-revival-{uuid4().hex[:5]}")
    old = "主角左手有旧伤，阴天会疼"
    # A successful current router decision, not words in author_message, grants this append.
    req_ctx = _set_author_round(db_session, user, project, f"记住：{old}", notes_append_allowed=True)
    remembered = _parse_payload(await update_project({"notes": old}, request_context=req_ctx))
    assert remembered["data"]["notes_added"] == [old]
    patch_project(
        project.id,
        PatchProjectRequest(notes=author_edit, expected_notes=old),
        current_user=user,
        session=db_session,
    )
    # Keep the same chat id but start a fresh request. The model replays its old tool args.
    session_id = req_ctx.session_id
    req_ctx_2 = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id=session_id,
        author_message="写第一章开头，不更新项目资料",
    )
    replay = _parse_payload(
        await update_project({"notes": old, "summary": "本轮正常简介"}, request_context=req_ctx_2)
    )
    assert _stored_notes(db_session, project) == author_edit
    assert "notes_added" not in replay["data"]
    assert "notes_note" not in replay["data"]
    assert replay["data"]["notes_skipped_reason"]
    assert replay["data"]["updated_fields"] == ["summary"]
    assert db_session.get(type(project), project.id).summary == "本轮正常简介"
    # A new explicit request may remember the same fact again after deletion or editing.
    req_ctx_3 = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id=session_id,
        author_message=f"再记住：{old}",
        notes_append_allowed=True,
    )
    remembered_again = _parse_payload(await update_project({"notes": old}, request_context=req_ctx_3))
    assert remembered_again["data"]["notes_added"] == [old]
    assert old in _stored_notes(db_session, project)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_consumed_steering_revokes_notes_in_existing_task_snapshot(db_session):
    import asyncio

    user, project = _make_user_and_project(db_session, f'notes-steer-{uuid4().hex[:5]}')
    consumed = []
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id='same-chat',
        author_message='记住：主角怕水',
        author_steering=consumed,
        notes_append_allowed=True,
    )
    ready = asyncio.Event()

    async def later_call():
        await ready.wait()
        return _parse_payload(await update_project({'notes': '主角怕水'}, request_context=req_ctx))

    # This task shares the request context and consumed steering list.
    pending = asyncio.create_task(later_call())
    consumed.append('先停下，我改一下要求')
    ready.set()
    payload = await pending
    assert payload['data']['notes_skipped_reason']
    assert 'notes_added' not in payload['data'] and 'notes_note' not in payload['data']
    assert _stored_notes(db_session, project) == ''
