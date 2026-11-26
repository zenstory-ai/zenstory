"""Regression tests for cross-file hookups wired with tool governance (Batch 3A).

Covers the 3 cross-file hookups needed for the low/medium-risk tool governance changes:
1. service.py -> ui_action pass-through to AgentRequestContext -> read_skill_resource(PublicSkill('去AI味'))
2. mcp_tools.py -> create_file(file_type='folder') succeeds while a <file> body is pending
3. stream_adapter.py -> skip _handle_edit_file_result when guard_skipped=True
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from sqlmodel import Session

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop.ui_actions import OFFICIAL_NATURAL_POLISH_SKILL_NAME, UI_ACTION_NATURAL_POLISH
from agent.openai_agents.repeat_edit_guard import EDIT_REPEAT_LIMIT_ERROR_TYPE
from agent.service import AgentService
from agent.stream_adapter import create_stream_adapter
from agent.tools.mcp_tools import create_file, read_skill_resource
from agent.tools.tool_context import AgentRequestContext
from models import ChatSession, File, Project, PublicSkill, SkillResource, User
from services.core.auth_service import hash_password


def _make_user_project_session(db_session: Session, suffix: str = "tgh") -> tuple[User, Project, ChatSession]:
    user = User(
        username=f"user_{suffix}",
        email=f"user_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    chat = ChatSession(
        user_id=user.id,
        project_id=project.id,
        title="Hookup Test Session",
        is_active=True,
    )
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    return user, project, chat


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")
async def test_service_passes_ui_action_to_request_context_enabling_official_skill_resource(db_session: Session):
    """ui_action='natural_polish' reaches AgentRequestContext and allows read_skill_resource on PublicSkill('去AI味')."""
    user, project, chat = _make_user_project_session(db_session, "ui_action")

    focus_file = File(
        project_id=project.id,
        title="第1章 试读",
        file_type="draft",
        content="他不由得微微一笑，心中百感交集。",
    )
    official_skill = PublicSkill(
        name=OFFICIAL_NATURAL_POLISH_SKILL_NAME,
        description="官方去AI味技能",
        instructions="按 references/checklist.md 检查AI腔。",
        source="official",
        status="approved",
    )
    db_session.add(focus_file)
    db_session.add(official_skill)
    db_session.commit()
    db_session.refresh(focus_file)
    db_session.refresh(official_skill)
    db_session.add(
        SkillResource(
            public_skill_id=official_skill.id,
            path="references/checklist.md",
            content="禁止使用「不由得」「微微一笑」。",
            size=16,
        )
    )
    db_session.commit()

    captured_ui_action: list[Any] = []
    resource_result: list[dict[str, Any]] = []

    def fake_loop(state, **_kwargs):
        req_ctx: AgentRequestContext = state["request_context"]
        captured_ui_action.append(req_ctx.ui_action)

        async def stream():
            local_ctx = AgentRequestContext(
                session=db_session,
                user_id=req_ctx.user_id,
                project_id=req_ctx.project_id,
                session_id=req_ctx.session_id,
                ui_action=req_ctx.ui_action,
            )
            raw = await read_skill_resource(
                {"name": OFFICIAL_NATURAL_POLISH_SKILL_NAME, "path": "references/checklist.md"},
                request_context=local_ctx,
            )
            payload = json.loads(raw["content"][0]["text"])
            resource_result.append(payload)
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "去AI味完成。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return stream()

    service = AgentService()
    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
        _ = [
            frame
            async for frame in service.process_stream(
                project_id=project.id,
                user_id=user.id,
                message="帮我去AI味",
                session=db_session,
                session_id=chat.id,
                metadata={"current_file_id": focus_file.id},
                ui_action=UI_ACTION_NATURAL_POLISH,
            )
        ]

    assert captured_ui_action == [UI_ACTION_NATURAL_POLISH]
    assert len(resource_result) == 1
    assert resource_result[0]["status"] == "success"
    assert "禁止使用" in resource_result[0]["data"]["content"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_folder_succeeds_while_another_file_awaits_body(db_session: Session):
    """create_file(file_type='folder') does not reserve/conflict with an active pending <file> body."""
    user, project, chat = _make_user_project_session(db_session, "folder_pending")
    req_ctx = AgentRequestContext(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id=chat.id,
    )
    try:
        outline_res = await create_file(
            {"title": "第一卷大纲", "file_type": "outline"},
            request_context=req_ctx,
        )
        outline_payload = json.loads(outline_res["content"][0]["text"])
        assert outline_payload["status"] == "success"
        assert outline_payload["data"]["awaiting_body"] is True
        outline_id = outline_payload["data"]["id"]
        assert req_ctx.has_pending_empty_file() is True

        folder_res = await create_file(
            {"title": "设定资料夹", "file_type": "folder"},
            request_context=req_ctx,
        )
        folder_payload = json.loads(folder_res["content"][0]["text"])
        assert folder_payload["status"] == "success"
        assert folder_payload["mutation_applied"] is True
        assert folder_payload["data"]["file_type"] == "folder"
        assert folder_payload["data"].get("awaiting_body") is not True

        pending = req_ctx.get_pending_empty_files()
        assert [p["file_id"] for p in pending] == [outline_id]
    finally:
        req_ctx.clear()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_adapter_skips_file_edit_events_on_guard_skipped():
    """When RepeatEditGuard returns guard_skipped=True, StreamAdapter emits tool_result but NO file_edit_* events."""
    adapter = create_stream_adapter(project_id="p1", user_id="u1")
    skipped_payload = {
        "status": "skipped",
        "guard_skipped": True,
        "reason": "post_write_limit",
        "message": "已达上限",
        "error_type": EDIT_REPEAT_LIMIT_ERROR_TYPE,
        "tool_name": "edit_file",
        "id": "ch1",
        "edits_applied": 0,
        "mutation_applied": False,
        "user_message": "这一步的细微调整已跳过，之前的修改都已保存。",
    }

    async def wf_gen():
        yield StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": "call-skip-1",
                "name": "edit_file",
                "result": {"content": [{"type": "text", "text": json.dumps(skipped_payload, ensure_ascii=False)}]},
            },
        )
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    sse_events = [ev async for ev in adapter.process_workflow_events(wf_gen())]
    event_types = [ev.type.value for ev in sse_events]
    assert "tool_result" in event_types
    assert "file_edit_start" not in event_types
    assert "file_edit_applied" not in event_types
    assert "file_edit_end" not in event_types
    tool_res = next(ev for ev in sse_events if ev.type.value == "tool_result")
    assert tool_res.data["status"] == "success"
    assert tool_res.data["data"]["guard_skipped"] is True
    assert "message" not in tool_res.data["data"]
    assert tool_res.data["data"]["user_message"] == "这一步的细微调整已跳过，之前的修改都已保存。"
