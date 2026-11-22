"""Regression tests for structured ui_action='natural_polish' on the single-loop agent.

Verifies:
- Pydantic & route-level validation (rejects unknown ui_action, missing/wrong-project/folder target file before quota reservation).
- Per-turn <turn_context> injection without mutating static system_prompt (prompt-cache prefix intact).
- Official DB PublicSkill '去AI味' is loaded directly without requiring UserAddedSkill; falls back cleanly when unseeded.
- Selection vs full-chapter execution guidance + screenplay △/dialogue/scene-heading preservation rules.
- No stale ui_action leakage to subsequent turns in the same session.
- Artifact-evidence zero-change quota refund (file_written == False -> refunded with kind='no_change',
  file_written == True -> charged) while preserving actual llm_usage cost ledger.
- Obsolete POST /api/v1/editor/natural-polish endpoint is removed (404).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from agent.core.stream_billing import (
    NATURAL_POLISH_NO_CHANGE_BILLING_REASON,
    REFUND_KIND_NO_CHANGE,
    StreamBillingTracker,
    quota_refunded_frame,
)
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop.turn_context import build_loop_turn_context
from agent.loop.ui_actions import OFFICIAL_NATURAL_POLISH_SKILL_NAME, UI_ACTION_NATURAL_POLISH
from models import ChatSession, File, Project, PublicSkill, UsageQuota, User
from services.core.auth_service import hash_password

pytestmark = pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _make_user_project_file(
    client: AsyncClient,
    db_session: Session,
    *,
    file_type: str = "draft",
    content: str = "他推开门。屋里很暗。他心头一紧，指节泛白。",
) -> tuple[User, Project, File, str]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"deai_{suffix}",
        email=f"deai_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"作品_{suffix}",
        owner_id=user.id,
        project_type="screenplay" if file_type == "script" else "novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    file = File(
        project_id=project.id,
        title="第1集 走廊对峙" if file_type == "script" else "第一章 暗房",
        file_type=file_type,
        content=content,
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    login = await client.post("/api/auth/login", data={"username": user.username, "password": "password123"})
    assert login.status_code == 200
    return user, project, file, login.json()["access_token"]


def _quota_used(db_session: Session, user_id: str) -> int:
    db_session.expire_all()
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user_id)).first()
    return quota.ai_conversations_used if quota else 0


# ---------------------------------------------------------------------------
# 1. Request validation & ownership checks (before quota reservation)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_obsolete_editor_natural_polish_endpoint_is_removed(client: AsyncClient, db_session: Session):
    _user, project, _file, token = await _make_user_project_file(client, db_session)
    response = await client.post(
        "/api/v1/editor/natural-polish",
        json={"project_id": project.id, "selected_text": "原始正文"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_unknown_ui_action_rejected_with_422_without_charging(client: AsyncClient, db_session: Session):
    user, project, file, token = await _make_user_project_file(client, db_session)
    response = await client.post(
        "/api/v1/agent/stream",
        json={
            "project_id": project.id,
            "message": "润色",
            "ui_action": "invented_action",
            "metadata": {"current_file_id": file.id},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422
    assert _quota_used(db_session, user.id) == 0


@pytest.mark.integration
async def test_natural_polish_requires_owned_non_folder_target_file(client: AsyncClient, db_session: Session):
    user, project, _file, token = await _make_user_project_file(client, db_session)
    _other_user, _other_project, other_file, _other_token = await _make_user_project_file(client, db_session)
    folder = File(project_id=project.id, title="正文", file_type="folder")
    db_session.add(folder)
    db_session.commit()
    db_session.refresh(folder)

    headers = {"Authorization": f"Bearer {token}"}

    # Missing current_file_id -> 422
    missing = await client.post(
        "/api/v1/agent/stream",
        json={"project_id": project.id, "message": "去AI味", "ui_action": UI_ACTION_NATURAL_POLISH},
        headers=headers,
    )
    assert missing.status_code == 422

    # File belonging to another project -> 404 (never leaks cross-project existence)
    foreign = await client.post(
        "/api/v1/agent/stream",
        json={
            "project_id": project.id,
            "message": "去AI味",
            "ui_action": UI_ACTION_NATURAL_POLISH,
            "metadata": {"current_file_id": other_file.id},
        },
        headers=headers,
    )
    assert foreign.status_code == 404

    # Folder target -> 422
    folder_resp = await client.post(
        "/api/v1/agent/stream",
        json={
            "project_id": project.id,
            "message": "去AI味",
            "ui_action": UI_ACTION_NATURAL_POLISH,
            "metadata": {"current_file_id": folder.id},
        },
        headers=headers,
    )
    assert folder_resp.status_code == 422
    assert _quota_used(db_session, user.id) == 0


# ---------------------------------------------------------------------------
# 2. <turn_context> injection, official skill source-of-truth & cache stability
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_turn_context_injects_official_skill_without_user_added_skill(db_session: Session):
    user = User(
        username="turn_ctx_official",
        email="turn_ctx_official@example.com",
        hashed_password="x",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(name="暗房", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    chapter = File(project_id=project.id, title="第一章", file_type="draft", content="正文内容。")
    db_session.add(chapter)
    official_skill = PublicSkill(
        name=OFFICIAL_NATURAL_POLISH_SKILL_NAME,
        description="官方去AI味",
        instructions="AUTHORITATIVE_OFFICIAL_DEAI_METHOD_MARKER",
        source="official",
        status="approved",
    )
    db_session.add(official_skill)
    db_session.commit()

    # User has ZERO UserAddedSkill rows, yet still gets the authoritative official instructions.
    turn_sel = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": chapter.id, "current_file_type": "draft", "current_file_title": chapter.title},
        language="zh",
        author_message="给第一章去AI味",
        ui_action=UI_ACTION_NATURAL_POLISH,
        selected_text="正文内容。",
    )
    assert "AUTHORITATIVE_OFFICIAL_DEAI_METHOD_MARKER" in turn_sel.text
    assert "只允许修改选区内的文字" in turn_sel.text
    assert "edit_file" in turn_sel.text
    assert "禁止调用 `write_chapter`" in turn_sel.text
    assert "不要做非必要的 `query_files`" in turn_sel.text
    assert "△" in turn_sel.text

    # Without selected_text: full-chapter guidance
    turn_full = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": chapter.id, "current_file_type": "draft", "current_file_title": chapter.title},
        language="zh",
        author_message="给第一章去AI味",
        ui_action=UI_ACTION_NATURAL_POLISH,
        selected_text=None,
    )
    assert "AUTHORITATIVE_OFFICIAL_DEAI_METHOD_MARKER" in turn_full.text
    assert "整篇原地去AI味" in turn_full.text
    assert "只允许修改选区内的文字" not in turn_full.text


@pytest.mark.unit
def test_turn_context_screenplay_english_and_fallback_when_unseeded(db_session: Session):
    user = User(
        username="turn_ctx_script",
        email="turn_ctx_script@example.com",
        hashed_password="x",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(name="Drama", owner_id=user.id, project_type="screenplay")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    script = File(
        project_id=project.id,
        title="Episode 1",
        file_type="script",
        content="【场1】走廊 · 夜 · 内\n△ 他心头一紧。\n林岚：你早就知道了。",
    )
    db_session.add(script)
    db_session.commit()

    turn = build_loop_turn_context(
        db_session,
        project_id=project.id,
        user_id=user.id,
        metadata={"current_file_id": script.id, "current_file_type": "script", "current_file_title": script.title},
        language="en",
        author_message="Humanize Episode 1",
        ui_action=UI_ACTION_NATURAL_POLISH,
        selected_text=None,
    )
    assert "ui_action=natural_polish" in turn.text
    assert 'Action lines starting with "△"' in turn.text
    assert "Dialogue lines" in turn.text
    assert "Do NOT call `write_chapter`" in turn.text
    assert "single batched `edit_file`" in turn.text


@pytest.mark.asyncio
@pytest.mark.unit
async def test_natural_polish_keeps_system_prompt_unchanged_and_does_not_leak_to_next_turn(db_session: Session):
    from agent.service import AgentService

    user = User(
        username="cache_deai",
        email="cache_deai@example.com",
        hashed_password="x",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(name="雾港", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    chapter = File(project_id=project.id, title="第一章", file_type="draft", content="雾港的夜很冷。")
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add_all([chapter, chat])
    db_session.commit()

    async def _run(message: str, ui_action: str | None) -> dict[str, Any]:
        captured: dict[str, Any] = {}

        def fake_loop(state, **_kwargs):
            captured.update(state)

            async def stream():
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "已处理。"})
                yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

            return stream()

        with patch("agent.service.run_loop_agent_streaming", side_effect=fake_loop):
            service = AgentService()
            async for _event in service.process_stream(
                project_id=project.id,
                user_id=user.id,
                message=message,
                session=db_session,
                session_id=chat.id,
                ui_action=ui_action,
                metadata={"current_file_id": chapter.id, "current_file_type": "draft", "current_file_title": chapter.title},
            ):
                pass
        return captured

    polish_turn = await _run("给《第一章》去AI味", UI_ACTION_NATURAL_POLISH)
    followup_turn = await _run("把第二段再收紧一点", None)

    # Static system prompt must be byte-identical across turns (preserves prompt cache prefix).
    assert polish_turn["system_prompt"] == followup_turn["system_prompt"]
    assert "ui_action=natural_polish" not in polish_turn["system_prompt"]

    # Turn context has polish guidance on the polish turn ONLY, never leaking to the follow-up turn.
    assert "ui_action=natural_polish" in polish_turn["user_message"]
    assert "ui_action=natural_polish" not in followup_turn["user_message"]


# ---------------------------------------------------------------------------
# 3. Artifact-evidence zero-change quota refund vs edit-applied charge
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_stream_billing_tracker_refunds_zero_edit_natural_polish_only():
    # Zero edits (even with prose reply): natural_polish refunds, ordinary chat charges.
    tracker_noop = StreamBillingTracker()
    tracker_noop.observe(_sse("content", {"text": "这段文字已经很自然了，没有发现需要修改的AI腔，因此保留原文未动。" * 3}))
    tracker_noop.observe(_sse("done", {"session_id": "s1"}))
    assert tracker_noop.file_written is False
    assert tracker_noop.decide(
        client_disconnected=False,
        unexpected_exception=False,
        provider_attempted=True,
        ui_action=UI_ACTION_NATURAL_POLISH,
    ) == (NATURAL_POLISH_NO_CHANGE_BILLING_REASON, True)
    assert tracker_noop.decide(
        client_disconnected=False,
        unexpected_exception=False,
        provider_attempted=True,
        ui_action=None,
    ) == ("completed", False)

    # No-op edit_file (mutation_applied=False) still counts as zero-change -> refunded.
    tracker_noop_edit = StreamBillingTracker()
    tracker_noop_edit.observe(
        _sse("tool_result", {"tool_name": "edit_file", "status": "success", "data": {"mutation_applied": False}})
    )
    tracker_noop_edit.observe(_sse("done", {"session_id": "s1"}))
    assert tracker_noop_edit.file_written is False
    assert tracker_noop_edit.decide(
        client_disconnected=False,
        unexpected_exception=False,
        provider_attempted=True,
        ui_action=UI_ACTION_NATURAL_POLISH,
    ) == (NATURAL_POLISH_NO_CHANGE_BILLING_REASON, True)

    # Real edit_file (mutation_applied=True) -> charged.
    tracker_edited = StreamBillingTracker()
    tracker_edited.observe(
        _sse("tool_result", {"tool_name": "edit_file", "status": "success", "data": {"mutation_applied": True, "edits_applied": 2}})
    )
    tracker_edited.observe(_sse("done", {"session_id": "s1"}))
    assert tracker_edited.file_written is True
    assert tracker_edited.decide(
        client_disconnected=False,
        unexpected_exception=False,
        provider_attempted=True,
        ui_action=UI_ACTION_NATURAL_POLISH,
    ) == ("completed", False)

    # Refund frame carries kind="no_change" (not "error").
    frame = quota_refunded_frame(NATURAL_POLISH_NO_CHANGE_BILLING_REASON)
    assert f'"kind": "{REFUND_KIND_NO_CHANGE}"' in frame


@pytest.mark.integration
async def test_stream_api_refunds_quota_when_natural_polish_makes_no_edits_and_charges_when_edited(
    client: AsyncClient, db_session: Session
):
    user, project, file, token = await _make_user_project_file(client, db_session)
    headers = {"Authorization": f"Bearer {token}"}

    class _NoChangeService:
        async def process_stream(self, **_kwargs):
            yield _sse("session_started", {"session_id": "s-noop"})
            yield _sse("content", {"text": "这段文字已经很自然，无需改动。"})
            yield _sse("done", {"session_id": "s-noop", "file_mutated": False})

    with patch("api.agent.get_agent_service", return_value=_NoChangeService()):
        resp_noop = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": project.id,
                "message": "给第一章去AI味",
                "ui_action": UI_ACTION_NATURAL_POLISH,
                "metadata": {"current_file_id": file.id},
            },
            headers=headers,
        )
    assert resp_noop.status_code == 200
    assert "event: quota_refunded" in resp_noop.text
    assert '"kind": "no_change"' in resp_noop.text
    assert _quota_used(db_session, user.id) == 0

    class _EditedService:
        async def process_stream(self, **_kwargs):
            yield _sse("session_started", {"session_id": "s-edit"})
            yield _sse(
                "tool_result",
                {"tool_name": "edit_file", "status": "success", "data": {"mutation_applied": True, "edits_applied": 1}},
            )
            yield _sse("content", {"text": "已完成去AI味修改。"})
            yield _sse("done", {"session_id": "s-edit", "file_mutated": True})

    with patch("api.agent.get_agent_service", return_value=_EditedService()):
        resp_edit = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": project.id,
                "message": "给第一章去AI味",
                "ui_action": UI_ACTION_NATURAL_POLISH,
                "metadata": {"current_file_id": file.id},
            },
            headers=headers,
        )
    assert resp_edit.status_code == 200
    assert "event: quota_refunded" not in resp_edit.text
    assert _quota_used(db_session, user.id) == 1
