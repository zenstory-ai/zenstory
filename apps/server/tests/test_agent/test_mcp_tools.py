"""Tests for MCP tool wrappers."""

import asyncio
import json
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agent.tools.mcp_tools import (
    create_file,
    delete_file,
    edit_file,
    handoff_to_agent,
    hybrid_search,
    query_files,
    request_clarification,
    update_project,
)


def _parse_payload(result: dict) -> dict:
    text = result["content"][0]["text"]
    return json.loads(text)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_update_project_supports_empty_string_and_returns_compat_fields():
    mock_executor = MagicMock()
    mock_executor.update_project_status.return_value = {
        "project_id": "proj-1",
        "updated_fields": ["summary"],
        "current_status": {"summary": ""},
    }

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ):
        result = await update_project({"summary": ""})

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["project_status"]["updated_fields"] == ["summary"]
    assert payload["data"]["updated_fields"] == ["summary"]
    assert payload["data"]["project_id"] == "proj-1"
    assert payload["data"]["current_status"]["summary"] == ""

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

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ):
        result = await update_project({"currentPhase": "已写至第5章"})

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["updated_fields"] == ["current_phase"]
    assert payload["data"]["current_status"]["current_phase"] == "已写至第5章"

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

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ):
        result = await update_project({"tasks": []})

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

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ), patch(
        "agent.tools.mcp_tools._suggest_auto_current_phase_from_drafts",
        return_value="已写至第5章",
    ):
        result = await update_project({"tasks": [{"task": "写第5章", "status": "done"}]})

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["plan"]["tasks"] == [{"task": "写第5章", "status": "done"}]
    assert payload["data"]["updated_fields"] == ["current_phase"]
    assert payload["data"]["current_status"]["current_phase"] == "已写至第5章"

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

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ), patch(
        "agent.tools.mcp_tools._suggest_auto_current_phase_from_drafts",
        return_value=None,
    ), patch("agent.tools.mcp_tools.log_with_context") as mock_log:
        result = await update_project({"tasks": [{"task": "写第5章", "status": "done"}]})

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

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ), patch(
        "agent.tools.mcp_tools._suggest_auto_current_phase_from_drafts",
        side_effect=RuntimeError("boom"),
    ), patch("agent.tools.mcp_tools.log_with_context") as mock_log:
        result = await update_project({"tasks": [{"task": "写第5章", "status": "done"}]})

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
async def test_create_file_infers_order_from_episode_title_when_order_missing(db_session):
    """create_file tool should infer `order` from titles like 第8集 when caller omits order."""
    from agent.tools.mcp_tools import ToolContext
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

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-1",
    )
    try:
        result = await create_file({
            "title": "第8集：测试",
            "file_type": "outline",
            "parent_id": folder.id,
            "content": "outline",
        })
    finally:
        ToolContext.clear_context()

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["title"] == "第8集：测试"
    assert payload["data"]["order"] == 8


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_normalizes_suspicious_explicit_order_from_title(db_session):
    """Explicit order like 580 for 第58章 should be normalized before persistence."""
    from agent.tools.mcp_tools import ToolContext
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

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-order-fix",
    )
    try:
        result = await create_file({
            "title": "第58章 真相",
            "file_type": "draft",
            "parent_id": folder.id,
            "order": 580,
            "content": "content",
        })
    finally:
        ToolContext.clear_context()

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["title"] == "第58章 真相"
    assert payload["data"]["order"] == 58


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_prefers_title_sequence_over_explicit_order_for_chapter_files(db_session):
    """Chapter-like draft/outline/script files should ignore conflicting explicit order."""
    from agent.tools.mcp_tools import ToolContext
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

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-order-lock",
    )
    try:
        result = await create_file({
            "title": "第58章 真相",
            "file_type": "draft",
            "parent_id": folder.id,
            "order": 1,
            "content": "content",
        })
    finally:
        ToolContext.clear_context()

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["title"] == "第58章 真相"
    assert payload["data"]["order"] == 58


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handoff_to_agent_rejects_legacy_reviewer_alias():
    """Legacy reviewer alias should no longer be accepted."""
    result = await handoff_to_agent({
        "target_agent": "reviewer",
        "reason": "legacy alias",
    })

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert "Invalid target_agent" in payload["error"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handoff_to_agent_rejects_case_insensitive_reviewer_alias():
    """Reviewer alias should be rejected even with mixed case input."""
    result = await handoff_to_agent({
        "target_agent": "Reviewer",
        "reason": "legacy alias with mixed case",
    })

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert "Invalid target_agent" in payload["error"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handoff_to_agent_keeps_artifact_refs():
    result = await handoff_to_agent({
        "target_agent": "writer",
        "reason": "继续创作",
        "artifact_refs": ["file-1", "  ", "file-2"],
    })

    payload = _parse_payload(result)
    assert payload["status"] == "handoff"
    assert payload["artifact_refs"] == ["file-1", "file-2"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handoff_to_agent_defaults_context_to_reason_when_missing():
    """Context is required downstream; tool should fall back to reason when omitted."""
    result = await handoff_to_agent({
        "target_agent": "quality_reviewer",
        "reason": "内容已完成，请进行质量检查",
        "context": "   ",
    })

    payload = _parse_payload(result)
    assert payload["status"] == "handoff"
    assert payload["context"] == "内容已完成，请进行质量检查"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handoff_to_agent_generates_context_when_both_reason_and_context_missing():
    """Even empty handoff packets should carry a stable handoff context string."""
    result = await handoff_to_agent({
        "target_agent": "quality_reviewer",
        "reason": " ",
        "context": "",
    })

    payload = _parse_payload(result)
    assert payload["status"] == "handoff"
    assert payload["context"]
    assert "quality_reviewer" in payload["context"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handoff_to_agent_merges_recent_ledger_refs():
    with patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ), patch(
        "agent.tools.mcp_tools._load_recent_artifact_refs_for_handoff",
        return_value=["file-a", "file-b"],
    ):
        result = await handoff_to_agent({
            "target_agent": "writer",
            "reason": "继续",
            "artifact_refs": ["file-b", "file-c"],
        })

    payload = _parse_payload(result)
    assert payload["status"] == "handoff"
    assert payload["artifact_refs"] == ["file-b", "file-c", "file-a"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handoff_to_agent_backfills_overflow_refs_into_context():
    overflow_ref = "tool_result_overflow:abc123"
    with patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ), patch(
        "agent.tools.mcp_tools._load_recent_artifact_refs_for_handoff",
        return_value=[overflow_ref],
    ), patch(
        "agent.tools.mcp_tools._load_tool_result_overflow_entry",
        return_value={
            "overflow_ref": overflow_ref,
            "tool_name": "query_files",
            "status": "success",
            "original_length": 4096,
            "serialized_payload": '{"status":"success","data":"..."}',
        },
    ):
        result = await handoff_to_agent({
            "target_agent": "writer",
            "reason": "继续",
            "context": "已完成检索",
        })

    payload = _parse_payload(result)
    assert payload["status"] == "handoff"
    assert payload["artifact_refs"] == [overflow_ref]
    assert "[工具外溢引用回填]" in payload["context"]
    assert overflow_ref in payload["context"]
    assert payload["overflow_backfill"][0]["overflow_ref"] == overflow_ref


@pytest.mark.unit
def test_load_recent_artifact_refs_for_handoff_filters_out_non_handoff_actions(db_session):
    from agent.tools import mcp_tools
    from models import AgentArtifactLedger, ChatSession, Project, User

    suffix = uuid4().hex[:8]
    user = User(
        email=f"mcp-tools-{suffix}@example.com",
        username=f"mcp_tools_{suffix}",
        hashed_password="hashed",
        name="MCP Tools Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"MCP Tools Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    chat_session = ChatSession(
        user_id=user.id,
        project_id=project.id,
        title="MCP Tools Test Chat",
        is_active=True,
        message_count=0,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)

    base_time = datetime.utcnow()
    db_session.add(
        AgentArtifactLedger(
            project_id=project.id,
            session_id=chat_session.id,
            user_id=user.id,
            action="create_file",
            tool_name="create_file",
            artifact_ref="file-1",
            created_at=base_time - timedelta(seconds=2),
        )
    )
    db_session.add(
        AgentArtifactLedger(
            project_id=project.id,
            session_id=chat_session.id,
            user_id=user.id,
            action="unknown_action",
            tool_name="unknown_tool",
            artifact_ref=f"unknown:{chat_session.id}",
            payload=json.dumps({"note": "ignored"}),
            created_at=base_time,
        )
    )
    db_session.add(
        AgentArtifactLedger(
            project_id=project.id,
            session_id=chat_session.id,
            user_id=user.id,
            action="edit_file",
            tool_name="edit_file",
            artifact_ref="file-2",
            created_at=base_time - timedelta(seconds=1),
        )
    )
    db_session.commit()

    with patch("agent.tools.mcp_tools._get_ledger_session", return_value=(db_session, False)):
        refs = mcp_tools._load_recent_artifact_refs_for_handoff(
            project_id=project.id,
            session_id=chat_session.id,
            limit=10,
        )

    assert refs == ["file-2", "file-1"]


@pytest.mark.unit
def test_load_tool_result_overflow_entry_returns_payload(db_session):
    from agent.tools import mcp_tools
    from models import AgentArtifactLedger, ChatSession, Project, User

    suffix = uuid4().hex[:8]
    user = User(
        email=f"overflow-ref-{suffix}@example.com",
        username=f"overflow_ref_{suffix}",
        hashed_password="hashed",
        name="Overflow Ref User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"Overflow Ref Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    chat_session = ChatSession(
        user_id=user.id,
        project_id=project.id,
        title="Overflow Ref Chat",
        is_active=True,
        message_count=0,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)

    overflow_ref = "tool_result_overflow:test-ref"
    db_session.add(
        AgentArtifactLedger(
            project_id=project.id,
            session_id=chat_session.id,
            user_id=user.id,
            action="tool_result_overflow",
            tool_name="query_files",
            artifact_ref=overflow_ref,
            payload=json.dumps(
                {
                    "schema_version": 1,
                    "status": "success",
                    "tool_name": "query_files",
                    "original_length": 3210,
                    "serialized_payload": '{"status":"success","data":"large"}',
                },
                ensure_ascii=False,
            ),
        )
    )
    db_session.commit()

    with patch("agent.tools.mcp_tools._get_ledger_session", return_value=(db_session, False)), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": project.id, "session_id": chat_session.id},
    ):
        entry = mcp_tools._load_tool_result_overflow_entry(overflow_ref)

    assert entry is not None
    assert entry["overflow_ref"] == overflow_ref
    assert entry["tool_name"] == "query_files"
    assert entry["status"] == "success"
    assert entry["original_length"] == 3210
    assert entry["serialized_payload"] == '{"status":"success","data":"large"}'


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handoff_to_agent_rejects_self_handoff():
    with patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"current_agent": "writer"},
    ):
        result = await handoff_to_agent({
            "target_agent": "writer",
            "reason": "self",
        })

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert "Self handoff is not allowed" in payload["error"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_edit_file_forwards_continue_on_error():
    mock_executor = MagicMock()
    mock_executor.edit_file.return_value = {"id": "f-1", "edits_applied": 1}

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1"},
    ):
        result = await edit_file({
            "id": "f-1",
            "edits": [{"op": "append", "text": "x"}],
            "continue_on_error": True,
        })

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    mock_executor.edit_file.assert_called_once_with(
        id="f-1",
        edits=[{"op": "append", "text": "x"}],
        continue_on_error=True,
        normalize_quotes=True,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_edit_file_accepts_file_id_alias():
    """Backward compatibility: accept file_id as an alias for id."""
    mock_executor = MagicMock()
    mock_executor.edit_file.return_value = {"id": "f-1", "edits_applied": 1}

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1"},
    ):
        result = await edit_file({
            "file_id": "f-1",
            "edits": [{"op": "append", "text": "x"}],
        })

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    mock_executor.edit_file.assert_called_once_with(
        id="f-1",
        edits=[{"op": "append", "text": "x"}],
        continue_on_error=False,
        normalize_quotes=True,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_records_artifact_ledger_on_success():
    mock_executor = MagicMock()
    mock_executor.create_file.return_value = {
        "id": "f-1",
        "title": "chapter1",
        "file_type": "draft",
    }

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ), patch("agent.tools.mcp_tools._record_artifact_ledger") as mock_record:
        result = await create_file({
            "title": "chapter1",
            "file_type": "draft",
            "content": "正文",
        })

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    mock_record.assert_called_once()
    kwargs = mock_record.call_args.kwargs
    assert kwargs["action"] == "create_file"
    assert kwargs["tool_name"] == "create_file"
    assert kwargs["artifact_refs"] == ["f-1"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_result_payload_is_truncated_when_exceeding_limit():
    mock_executor = MagicMock()
    mock_executor.edit_file.return_value = {
        "id": "f-1",
        "content": "x" * 2000,
    }

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1"},
    ), patch(
        "agent.tools.mcp_tools.TOOL_RESULT_MAX_CHARS",
        180,
    ):
        result = await edit_file({
            "id": "f-1",
            "edits": [{"op": "append", "text": "x"}],
        })

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["truncated"] is True
    assert payload["max_chars"] == 180
    assert payload["original_length"] > 180
    assert payload["data"]["truncated"] is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_result_payload_persists_overflow_ref_when_ledger_available():
    mock_executor = MagicMock()
    mock_executor.edit_file.return_value = {
        "id": "f-1",
        "content": "x" * 2000,
    }

    def _record_side_effect(**_kwargs):
        return True

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ), patch(
        "agent.tools.mcp_tools.TOOL_RESULT_MAX_CHARS",
        180,
    ), patch(
        "agent.tools.mcp_tools._record_artifact_ledger",
        side_effect=_record_side_effect,
    ) as mock_record:
        result = await edit_file({
            "id": "f-1",
            "edits": [{"op": "append", "text": "x"}],
        })

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["truncated"] is True
    assert payload["overflow_ref"].startswith("tool_result_overflow:")

    overflow_calls = [
        call.kwargs for call in mock_record.call_args_list if call.kwargs.get("action") == "tool_result_overflow"
    ]
    assert len(overflow_calls) == 1
    overflow_call = overflow_calls[0]
    assert overflow_call["tool_name"] == "edit_file"
    assert overflow_call["artifact_refs"] == [payload["overflow_ref"]]
    assert overflow_call["payload"]["schema_version"] == 1
    assert overflow_call["payload"]["original_length"] > 180


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_result_payload_drops_overflow_ref_when_ledger_persist_fails():
    mock_executor = MagicMock()
    mock_executor.edit_file.return_value = {
        "id": "f-1",
        "content": "x" * 2000,
    }

    def _record_side_effect(**kwargs):
        if kwargs.get("action") == "tool_result_overflow":
            return False
        return True

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ), patch(
        "agent.tools.mcp_tools.TOOL_RESULT_MAX_CHARS",
        180,
    ), patch(
        "agent.tools.mcp_tools._record_artifact_ledger",
        side_effect=_record_side_effect,
    ):
        result = await edit_file({
            "id": "f-1",
            "edits": [{"op": "append", "text": "x"}],
        })

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["truncated"] is True
    assert "overflow_ref" not in payload


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_error_payload_is_truncated_when_exceeding_limit():
    mock_executor = MagicMock()
    mock_executor.edit_file.side_effect = ValueError("boom-" + ("x" * 2000))

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1"},
    ), patch(
        "agent.tools.mcp_tools.TOOL_RESULT_MAX_CHARS",
        180,
    ):
        result = await edit_file({
            "id": "f-1",
            "edits": [{"op": "append", "text": "x"}],
        })

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

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1"},
    ):
        result = await query_files({
            "query": "chapter",
            "response_mode": "summary",
            "content_preview_chars": 120,
            "include_content": False,
        })

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    mock_executor.query_files.assert_called_once_with(
        project_id="proj-1",
        query="chapter",
        file_type=None,
        file_types=None,
        parent_id=None,
        metadata_filter=None,
        limit=50,
        offset=0,
        response_mode="summary",
        content_preview_chars=120,
        include_content=False,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_query_files_falls_back_for_legacy_executor_signature():
    calls: list[dict] = []

    def _legacy_query_files(**kwargs):
        calls.append(dict(kwargs))
        if "response_mode" in kwargs:
            raise TypeError("query_files() got an unexpected keyword argument 'response_mode'")
        return [{"id": "f-legacy"}]

    mock_executor = MagicMock()
    mock_executor.query_files.side_effect = _legacy_query_files

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1"},
    ):
        result = await query_files({
            "query": "chapter",
            "response_mode": "full",
            "content_preview_chars": 500,
            "include_content": True,
        })

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert len(calls) == 2
    assert "response_mode" in calls[0]
    assert "response_mode" not in calls[1]
    assert calls[1]["project_id"] == "proj-1"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_hybrid_search_forwards_args_to_executor(monkeypatch):
    monkeypatch.setenv("AGENT_TOOL_HYBRID_SEARCH_ENABLED", "true")
    mock_executor = MagicMock()
    mock_executor.hybrid_search.return_value = {
        "query": "hero",
        "result_count": 1,
        "results": [{"entity_id": "f-1", "fused_score": 0.88}],
    }

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1"},
    ):
        result = await hybrid_search({
            "query": "hero",
            "top_k": 5,
            "entity_types": ["draft"],
            "min_score": 0.2,
        })

    payload = _parse_payload(result)
    assert payload["status"] == "success"
    assert payload["data"]["result_count"] == 1
    mock_executor.hybrid_search.assert_called_once_with(
        project_id="proj-1",
        query="hero",
        top_k=5,
        entity_types=["draft"],
        min_score=0.2,
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_hybrid_search_executor_failure_returns_error(monkeypatch):
    """When executor.hybrid_search raises, tool returns error with structured log."""
    monkeypatch.setenv("AGENT_TOOL_HYBRID_SEARCH_ENABLED", "true")
    mock_executor = MagicMock()
    mock_executor.hybrid_search.side_effect = RuntimeError("vector infra down")

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1"},
    ), patch("agent.tools.mcp_tools.log_with_context") as mock_log:
        result = await hybrid_search({"query": "hero", "top_k": 5})

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    mock_executor.hybrid_search.assert_called_once()
    mock_log.assert_called_once()
    assert mock_log.call_args.args[2] == "Agent hybrid_search failed"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool_func", "sync_helper", "args"),
    [
        (create_file, "_create_file_sync", {"title": "T", "file_type": "draft"}),
        (edit_file, "_edit_file_sync", {"id": "file-1", "edits": []}),
        (delete_file, "_delete_file_sync", {"id": "file-1"}),
        (query_files, "_query_files_sync", {"query": "hero"}),
        (hybrid_search, "_hybrid_search_sync", {"query": "hero"}),
        (update_project, "_update_project_sync", {"tasks": []}),
    ],
)
async def test_offloaded_wrappers_cleanup_owned_session(tool_func, sync_helper, args):
    """PostgreSQL offloaded wrappers should deterministically close ToolContext-owned sessions."""

    async def _run_inline(func, *call_args):
        return func(*call_args)

    with (
        patch("agent.tools.mcp_tools._should_offload_tool_execution", return_value=True),
        patch("agent.tools.mcp_tools.asyncio.to_thread", side_effect=_run_inline) as mock_to_thread,
        patch(
            f"agent.tools.mcp_tools.{sync_helper}",
            return_value={"content": [{"type": "text", "text": '{"status":"success"}'}]},
        ) as mock_sync,
        patch("agent.tools.mcp_tools.ToolContext._cleanup_owned_session") as mock_cleanup,
    ):
        result = await tool_func(args)

    assert _parse_payload(result)["status"] == "success"
    mock_to_thread.assert_called_once()
    mock_sync.assert_called_once_with(args)
    mock_cleanup.assert_called_once()


@pytest.mark.unit
def test_record_artifact_ledger_rolls_back_on_commit_error():
    from agent.tools import mcp_tools

    mock_session = MagicMock()
    mock_session.commit.side_effect = RuntimeError("db write failed")

    class DummyLedger:
        def __init__(self, **_kwargs):
            pass

    with patch("agent.tools.mcp_tools._get_ledger_session", return_value=(mock_session, False)), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1", "user_id": "user-1"},
    ), patch("models.AgentArtifactLedger", DummyLedger):
        mcp_tools._record_artifact_ledger(
            action="create_file",
            tool_name="create_file",
            artifact_refs=["file-1"],
            payload={"title": "chapter1"},
        )

    mock_session.rollback.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_request_clarification_returns_structured_payload():
    result = await request_clarification({
        "question": "请确认主角姓名",
        "context": "已完成大纲第一章",
        "details": ["主角姓名", "时代背景"],
    })

    payload = _parse_payload(result)
    assert payload["status"] == "clarification_needed"
    assert payload["question"] == "请确认主角姓名"
    assert payload["context"] == "已完成大纲第一章"
    assert payload["details"] == ["主角姓名", "时代背景"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_request_clarification_requires_question():
    result = await request_clarification({
        "question": "   ",
        "context": "任意上下文",
    })

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert "question is required" in payload["error"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_empty_file_sets_pending_marker_on_offload_path(db_session):
    """Regression: the pending-empty-file marker must survive the Postgres-style
    offload path.

    When create_file offloads _create_file_sync with asyncio.to_thread
    (production/Postgres), the sync impl runs in a COPIED context. The marker it
    sets there must still be visible in the caller's (event-loop) context —
    otherwise the consecutive-empty-file guard and the writing_graph correction
    loop are both disabled in production.
    """
    from agent.tools.mcp_tools import ToolContext
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

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-empty",
    )
    ToolContext.clear_pending_empty_file()
    try:
        # Force the offload branch so the sync impl runs inside asyncio.to_thread
        # (a copied context), reproducing the production Postgres path.
        with patch(
            "agent.tools.mcp_tools._should_offload_tool_execution",
            return_value=True,
        ):
            result = await create_file({
                "title": "空章节",
                "file_type": "draft",
                "content": "",  # empty -> expects <file>…</file> streaming to follow
            })

        payload = _parse_payload(result)
        assert payload["status"] == "success"
        # The marker must be visible in THIS (caller/event-loop) context.
        assert ToolContext.has_pending_empty_file() is True
        pending = ToolContext.get_pending_empty_file()
        assert pending is not None
        assert pending["file_id"] == payload["data"]["id"]
    finally:
        ToolContext.clear_pending_empty_file()
        ToolContext.clear_context()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_consecutive_empty_create_file_rejected_across_tool_tasks(db_session):
    """连续创建第二个空文件必须被拦截 — 即使两次 create_file 分别运行在
    SDK 为每次工具调用包的独立 asyncio 子任务里。

    第一个空文件的 pending 标记若只存在于子任务的 contextvars 副本中，第二次
    create_file（另一个子任务）就看不到守卫，stream_adapter 会无条件覆盖捕获
    目标，第一个文件已流出的正文被静默丢弃。
    """
    from agent.tools.mcp_tools import ToolContext
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

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-guard",
    )
    try:
        first = await asyncio.create_task(create_file({
            "title": "第1章",
            "file_type": "draft",
            "content": "",
        }))
        first_payload = _parse_payload(first)
        assert first_payload["status"] == "success"
        # 子任务里设置的标记必须在父上下文可见
        assert ToolContext.has_pending_empty_file() is True

        second = await asyncio.create_task(create_file({
            "title": "第2章",
            "file_type": "draft",
            "content": "",
        }))
        second_payload = _parse_payload(second)
        assert second_payload["status"] == "error"
        assert "第1章" in second_payload["error"]
    finally:
        ToolContext.clear_pending_empty_file()
        ToolContext.clear_context()


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
    from agent.tools.mcp_tools import ToolContext

    user, project = _make_user_and_project(db_session, "folder")

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-folder",
    )
    try:
        folder = await asyncio.create_task(create_file({
            "title": "第一卷",
            "file_type": "folder",
        }))
        folder_payload = _parse_payload(folder)
        assert folder_payload["status"] == "success"
        assert folder_payload["data"]["file_type"] == "folder"
        assert ToolContext.has_pending_empty_file() is False

        chapter = await asyncio.create_task(create_file({
            "title": "第1章",
            "file_type": "draft",
            "parent_id": folder_payload["data"]["id"],
        }))
        chapter_payload = _parse_payload(chapter)
        assert chapter_payload["status"] == "success"
        assert chapter_payload["data"]["parent_id"] == folder_payload["data"]["id"]

        # 章节（真正等待流式正文的文件）仍然要置标记
        pending = ToolContext.get_pending_empty_file()
        assert pending is not None
        assert pending["file_id"] == chapter_payload["data"]["id"]
        assert pending["title"] == "第1章"
    finally:
        ToolContext.clear_pending_empty_file()
        ToolContext.clear_context()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_folder_marker_free_on_offload_path(db_session):
    """Postgres 卸载路径（asyncio.to_thread）同样不得给文件夹置标记。"""
    from agent.tools.mcp_tools import ToolContext

    user, project = _make_user_and_project(db_session, "folderoff")

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-folder-offload",
    )
    try:
        with patch(
            "agent.tools.mcp_tools._should_offload_tool_execution",
            return_value=True,
        ):
            folder = await create_file({
                "title": "第二卷",
                "file_type": "folder",
            })
        assert _parse_payload(folder)["status"] == "success"
        assert ToolContext.has_pending_empty_file() is False
    finally:
        ToolContext.clear_pending_empty_file()
        ToolContext.clear_context()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_folder_still_blocked_while_file_awaits_content(db_session):
    """已有待写入正文的文件时，建文件夹仍被拦截（保留防内容丢失的守卫）。

    stream_adapter 会把最近一次 create_file 的返回无条件设为流式捕获目标，
    因此在正文写完前插入任何 create_file（含文件夹）都会让前一个文件的正文写错
    位置；这里的拦截是可恢复的：写完正文后标记清除，建文件夹即可继续。
    """
    from agent.tools.mcp_tools import ToolContext

    user, project = _make_user_and_project(db_session, "folderguard")

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-folder-guard",
    )
    try:
        chapter = await create_file({"title": "第1章", "file_type": "draft"})
        assert _parse_payload(chapter)["status"] == "success"
        assert ToolContext.has_pending_empty_file() is True

        blocked = await create_file({"title": "第一卷", "file_type": "folder"})
        blocked_payload = _parse_payload(blocked)
        assert blocked_payload["status"] == "error"
        assert "第1章" in blocked_payload["error"]

        # 正文写入后（stream_adapter 清除标记）文件夹可以正常创建
        ToolContext.clear_pending_empty_file()
        folder = await create_file({"title": "第一卷", "file_type": "folder"})
        assert _parse_payload(folder)["status"] == "success"
        assert ToolContext.has_pending_empty_file() is False
    finally:
        ToolContext.clear_pending_empty_file()
        ToolContext.clear_context()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_clears_stale_pending_marker_after_edit_file_wrote_body(db_session):
    """上一个空文件的正文已经用 edit_file(op=append) 写进去：标记陈旧，不能再挡新文件。"""
    from agent.tools.mcp_tools import ToolContext, edit_file

    user, project = _make_user_and_project(db_session, "stale")
    ToolContext.set_context(
        session=db_session, user_id=user.id, project_id=project.id, session_id="sess-stale"
    )
    try:
        first = _parse_payload(await create_file({"title": "陈砚", "file_type": "character"}))
        assert first["status"] == "success"
        first_id = first["data"]["id"]

        edited = _parse_payload(
            await edit_file({"id": first_id, "edits": [{"op": "append", "text": "陈砚，二十七岁。"}]})
        )
        assert edited["status"] == "success"
        # edit_file 不清标记（这是陈旧标记的来源）
        assert ToolContext.has_pending_empty_file() is True

        second = _parse_payload(await create_file({"title": "苏晚", "file_type": "character"}))
        assert second["status"] == "success"
        pending_ids = [entry["file_id"] for entry in ToolContext.get_pending_empty_files()]
        assert pending_ids == [second["data"]["id"]]
    finally:
        ToolContext.clear_pending_empty_file()
        ToolContext.clear_context()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_file_rejection_names_the_unblocking_action(db_session):
    """上一个空文件确实还没正文：拒绝信息点名文件 id 和解除办法，并带顺序性 error_type。"""
    from agent.tools.mcp_tools import ToolContext

    user, project = _make_user_and_project(db_session, "unwritten")
    ToolContext.set_context(
        session=db_session, user_id=user.id, project_id=project.id, session_id="sess-unwritten"
    )
    try:
        first = _parse_payload(await create_file({"title": "沈砚", "file_type": "character"}))
        first_id = first["data"]["id"]

        rejected = _parse_payload(await create_file({"title": "苏晚", "file_type": "character"}))
        assert rejected["status"] == "error"
        assert rejected["error_type"] == "pending_empty_file_unwritten"
        assert rejected["pending_file_id"] == first_id
        assert "<file>" in rejected["error"]
        assert f"edit_file(id={first_id}" in rejected["error"]
        assert "苏晚" in rejected["error"]
        # 标记仍指向那个真正没写正文的文件
        assert [entry["file_id"] for entry in ToolContext.get_pending_empty_files()] == [first_id]
    finally:
        ToolContext.clear_pending_empty_file()
        ToolContext.clear_context()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_hybrid_search_disabled_returns_recoverable_tool_error(monkeypatch):
    """关闭时不是 success+空结果，而是点名替代工具的可恢复错误，且不触达检索后端。"""
    monkeypatch.setenv("AGENT_TOOL_HYBRID_SEARCH_ENABLED", "false")
    mock_executor = MagicMock()

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1"},
    ):
        result = await hybrid_search({"query": "hero"})

    payload = _parse_payload(result)
    assert payload["status"] == "error"
    assert payload["error_type"] == "tool_disabled"
    assert "query_files(query=...)" in payload["error"]
    assert "不要再调用 hybrid_search" in payload["error"]
    mock_executor.hybrid_search.assert_not_called()


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

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1"},
    ):
        await query_files(args)

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

    with patch("agent.tools.mcp_tools.ToolContext.get_executor", return_value=mock_executor), patch(
        "agent.tools.mcp_tools.ToolContext._get_context",
        return_value={"project_id": "proj-1", "session_id": "sess-1"},
    ), patch("agent.tools.mcp_tools.TOOL_RESULT_MAX_CHARS", 8_000), patch(
        "agent.tools.mcp_tools._record_artifact_ledger", return_value=True
    ):
        result = await query_files({"response_mode": "full", "limit": 8})

    text = result["content"][0]["text"]
    assert len(text) <= 8_000
    payload = json.loads(text)
    assert payload["status"] == "success"
    assert payload["truncated"] is True
    assert payload["overflow_ref"].startswith("tool_result_overflow:")
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
    with patch("agent.tools.mcp_tools.TOOL_RESULT_MAX_CHARS", 4_000), patch(
        "agent.tools.mcp_tools._record_artifact_ledger", return_value=False
    ):
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
    from agent.tools.mcp_tools import ToolContext

    user, project = _make_user_and_project(db_session, "streamed")
    ToolContext.set_context(
        session=db_session, user_id=user.id, project_id=project.id, session_id="sess-streamed"
    )
    try:
        first = _parse_payload(await create_file({"title": "第1集", "file_type": "draft"}))
        first_id = first["data"]["id"]

        ToolContext.mark_latest_pending_body_streamed()
        second = _parse_payload(
            await asyncio.create_task(create_file({"title": "第2集", "file_type": "draft"}))
        )
        assert second["status"] == "success"
        second_id = second["data"]["id"]
        # 两条都还在：第1集等 adapter 落库清除，第2集等自己的正文
        assert [e["file_id"] for e in ToolContext.get_pending_empty_files()] == [
            first_id,
            second_id,
        ]
    finally:
        ToolContext.clear_context()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_two_create_files_without_body_in_between_still_rejected(db_session):
    """同一条回复里只有两个 create_file、中间没写 <file> 正文：第二个照旧被拒。"""
    from agent.tools.mcp_tools import ToolContext

    user, project = _make_user_and_project(db_session, "doublecreate")
    ToolContext.set_context(
        session=db_session, user_id=user.id, project_id=project.id, session_id="sess-double"
    )
    try:
        first = _parse_payload(await create_file({"title": "第4集", "file_type": "draft"}))
        assert first["status"] == "success"
        rejected = _parse_payload(await create_file({"title": "第5集", "file_type": "draft"}))
        assert rejected["status"] == "error"
        assert rejected["error_type"] == "pending_empty_file_unwritten"
        assert rejected["pending_file_id"] == first["data"]["id"]
    finally:
        ToolContext.clear_context()
