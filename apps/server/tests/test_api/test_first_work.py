"""HTTP proof: default workspace has no model or AI-quota side effects."""
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlmodel import select

from config.datetime_utils import utcnow
from models import ActivationEvent, File, Project, UsageQuota, User, UserPersonaProfile, WorkspaceOnboarding
from services.core.auth_service import create_access_token
from services.first_work_service import enroll_new_user, initialize_first_work


@pytest.fixture(autouse=True)
def enabled_first_work_rollout(monkeypatch):
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "100")


def author(session):
    suffix = uuid4().hex
    user = User(username=suffix, email=f"{suffix}@example.test", hashed_password="unused", email_verified=True)
    session.add(user)
    enroll_new_user(session, user)
    session.add(UserPersonaProfile(user_id=user.id, skipped=True))
    now = utcnow()
    quota = UsageQuota(user_id=user.id, period_start=now, period_end=now + timedelta(days=1),
                       ai_conversations_used=3, last_reset_at=now)
    session.add(quota)
    session.commit()
    headers = {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}
    return user, quota, headers


async def test_create_open_refresh_and_manual_save_never_generate_or_charge(client, db_session, monkeypatch):
    user, quota, headers = author(db_session)
    generated = MagicMock(generate_suggestions=AsyncMock(return_value=["unexpected model suggestion"]))
    monkeypatch.setattr("agent.suggest_service.get_suggest_service", lambda: generated)
    first = await client.post("/api/v1/workspace/first-work", headers=headers, json={"preferred_type": "short"})
    assert first.status_code == 200
    state = first.json()
    assert state["static_guidance"] is True
    for _ in range(2):
        repeat = await client.post("/api/v1/workspace/first-work", headers=headers, json={})
        assert repeat.json()["project_id"] == state["project_id"]
        opened = await client.post("/api/v1/workspace/first-work/open", headers=headers)
        assert opened.json()["opened_at"] is not None
        assert (await client.get(f"/api/v1/files/{state['file_id']}", headers=headers)).json()["content"] == ""
        suggested = await client.post("/api/v1/agent/suggest", headers=headers, json={"project_id": state["project_id"]})
        assert suggested.json() == {"suggestions": []}
    assert generated.generate_suggestions.await_count == 0
    assert db_session.exec(select(ActivationEvent)).all() == []
    assert db_session.get(UsageQuota, quota.id).ai_conversations_used == 3
    saved = await client.put(f"/api/v1/files/{state['file_id']}", headers=headers,
                             json={"content": "这是作者手动粘贴的正文。"})
    assert saved.status_code == 200
    actual = (await client.get("/api/v1/workspace/first-work", headers=headers)).json()
    assert actual["first_manual_write_at"] is not None and actual["first_ai_requested_at"] is None
    assert actual["static_guidance"] is True
    assert db_session.get(UsageQuota, quota.id).ai_conversations_used == 3
    assert [e.event_name for e in db_session.exec(select(ActivationEvent)).all()] == ["first_file_saved"]
    # A quota-rejected explicit task does not fabricate an accepted AI action.
    denied = await client.post("/api/v1/agent/stream", headers=headers,
                               json={"project_id": state["project_id"], "message": "作者主动请求"})
    assert denied.status_code == 402
    assert db_session.get(WorkspaceOnboarding, user.id).first_ai_requested_at is None


async def test_author_project_rename_and_source_statistics_are_separate(client, db_session):
    user, _, headers = author(db_session)
    first = initialize_first_work(db_session, user.id)
    renamed = await client.put(f"/api/v1/projects/{first.project_id}", headers=headers, json={"name": "作者改名"})
    assert renamed.status_code == 200
    assert db_session.get(Project, first.project_id).name == "作者改名"
    # The durable source relation survives renaming; names are not a source filter.
    assert db_session.get(WorkspaceOnboarding, user.id).project_id == first.project_id
    user.is_superuser = True
    db_session.add(user)
    db_session.commit()
    stats = await client.get("/api/admin/dashboard/stats", headers=headers)
    assert stats.status_code == 200 and stats.json()["total_projects"] == 1
    assert stats.json()["author_created_projects"] == 0 and stats.json()["auto_initialized_projects"] == 1
    manual = await client.post("/api/v1/projects", headers=headers, json={"name": "主动作品"})
    assert manual.status_code == 200
    stats = await client.get("/api/admin/dashboard/stats", headers=headers)
    assert stats.json()["total_projects"] == 2
    assert stats.json()["author_created_projects"] == 1 and stats.json()["auto_initialized_projects"] == 1


async def test_explicit_accepted_task_ends_static_guidance_and_uses_existing_quota(client, db_session, monkeypatch):
    user, quota, headers = author(db_session)
    quota.ai_conversations_used = 2
    db_session.add(quota)
    db_session.commit()
    state = initialize_first_work(db_session, user.id)
    # Replace the paid execution boundary, retaining real auth/reservation/API/pump.
    class NoPaidModelService:
        async def process_stream(self, **kwargs):
            yield 'event: session_started\ndata: {"session_id":"synthetic-first-task"}\n\n'
            yield 'event: done\ndata: {}\n\n'
    monkeypatch.setattr("api.agent.get_agent_service", lambda: NoPaidModelService())
    response = await client.post("/api/v1/agent/stream", headers=headers,
                                 json={"project_id": state.project_id, "message": "作者明确发送的创意"})
    assert response.status_code == 200 and "event: done" in response.text
    current = (await client.get("/api/v1/workspace/first-work", headers=headers)).json()
    assert current["static_guidance"] is False and current["first_ai_requested_at"] is not None
    assert db_session.get(UsageQuota, quota.id).ai_conversations_used == 3
    suggestions = MagicMock(generate_suggestions=AsyncMock(return_value=["跟进作者当前任务"]))
    monkeypatch.setattr("agent.suggest_service.get_suggest_service", lambda: suggestions)
    requested = await client.post("/api/v1/agent/suggest", headers=headers, json={"project_id": state.project_id})
    assert requested.json() == {"suggestions": ["跟进作者当前任务"]}
    suggestions.generate_suggestions.assert_awaited_once()


async def test_ai_review_save_is_not_attributed_as_manual_first_writing(client, db_session):
    user, _, headers = author(db_session)
    state = initialize_first_work(db_session, user.id)
    response = await client.put(f"/api/v1/files/{state.file_id}", headers=headers,
                                 json={"content": "合成的 AI 审阅后正文", "change_type": "ai_edit"})
    assert response.status_code == 200
    current = (await client.get("/api/v1/workspace/first-work", headers=headers)).json()
    assert current["first_manual_write_at"] is None


async def test_persisted_new_registration_waits_for_explicit_onboarding_even_after_old_age_window(client, db_session):
    user, _, headers = author(db_session)
    profile = db_session.exec(select(UserPersonaProfile).where(UserPersonaProfile.user_id == user.id)).one()
    db_session.delete(profile)
    user.created_at = utcnow() - timedelta(days=8)
    db_session.add(user)
    db_session.commit()
    response = await client.get("/api/v1/persona/onboarding", headers=headers)
    assert response.status_code == 200 and response.json()["required"] is True
    result = await client.post("/api/v1/workspace/first-work", headers=headers, json={})
    assert result.json()["status"] == "waiting_onboarding"
    assert db_session.exec(select(Project)).all() == []


async def test_closing_rollout_keeps_created_work_static_and_pending_work_uninitialized(client, db_session, monkeypatch):
    created_user, _, created_headers = author(db_session)
    created = initialize_first_work(db_session, created_user.id)
    pending_user, _, pending_headers = author(db_session)
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "0")
    pending = await client.post("/api/v1/workspace/first-work", headers=pending_headers, json={})
    assert pending.json()["status"] == "disabled"
    assert db_session.exec(select(Project).where(Project.owner_id == pending_user.id)).all() == []
    repeated = await client.post("/api/v1/workspace/first-work", headers=created_headers, json={})
    assert repeated.json()["project_id"] == created.project_id and repeated.json()["static_guidance"] is True
    model = MagicMock(generate_suggestions=AsyncMock())
    monkeypatch.setattr("agent.suggest_service.get_suggest_service", lambda: model)
    response = await client.post("/api/v1/agent/suggest", headers=created_headers, json={"project_id": created.project_id})
    assert response.json() == {"suggestions": []}
    model.generate_suggestions.assert_not_called()
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "100")
    resumed = await client.post("/api/v1/workspace/first-work", headers=pending_headers, json={})
    assert resumed.json()["status"] == "created"


@pytest.mark.parametrize("delete_mode", ["soft", "hard"])
async def test_deleted_default_file_never_enables_automatic_model_suggestions(client, db_session, monkeypatch, delete_mode):
    user, quota, headers = author(db_session)
    state = initialize_first_work(db_session, user.id)
    if delete_mode == "soft":
        deleted = await client.delete(f"/api/v1/files/{state.file_id}", headers=headers)
        assert deleted.status_code == 200
    else:
        db_session.delete(db_session.get(File, state.file_id))
        db_session.commit()
    current = (await client.get("/api/v1/workspace/first-work", headers=headers)).json()
    assert current["available"] is False and current["static_guidance"] is True
    assert current["opened_at"] is None
    opened = await client.post("/api/v1/workspace/first-work/open", headers=headers)
    assert opened.json()["opened_at"] is not None
    assert opened.json()["available"] is False and opened.json()["static_guidance"] is True
    model = MagicMock(generate_suggestions=AsyncMock())
    monkeypatch.setattr("agent.suggest_service.get_suggest_service", lambda: model)
    for _ in range(2):
        response = await client.post("/api/v1/agent/suggest", headers=headers, json={"project_id": state.project_id})
        assert response.json() == {"suggestions": []}
    model.generate_suggestions.assert_not_called()
    assert db_session.get(UsageQuota, quota.id).ai_conversations_used == 3
    assert db_session.get(WorkspaceOnboarding, user.id).first_ai_requested_at is None


@pytest.mark.parametrize("mode", ["accepted", "quota_rejected", "model_failure", "observer_failure", "observer_and_model_failure"])
async def test_explicit_polish_records_first_ai_only_after_reservation_without_changing_refunds(client, db_session, monkeypatch, mode):
    user, quota, headers = author(db_session)
    quota.ai_conversations_used = 3 if mode == "quota_rejected" else 2
    db_session.add(quota)
    db_session.commit()
    state = initialize_first_work(db_session, user.id)
    model_failed = mode in {"model_failure", "observer_and_model_failure"}
    observation_failed = mode in {"observer_failure", "observer_and_model_failure"}
    started = 0

    class _PolishStreamService:
        async def process_stream(self, **kwargs):
            nonlocal started
            started += 1
            assert kwargs.get("ui_action") == "natural_polish"
            yield 'event: session_started\ndata: {"session_id":"synthetic-polish"}\n\n'
            if model_failed:
                yield 'event: error\ndata: {"message":"synthetic model failure","code":"ERR_INTERNAL_SERVER_ERROR","retryable":true,"refundable":true}\n\n'
                return
            yield 'event: tool_result\ndata: {"tool_name":"edit_file","status":"success","data":{"mutation_applied":true,"edits_applied":1}}\n\n'
            yield 'event: done\ndata: {"session_id":"synthetic-polish"}\n\n'

    monkeypatch.setattr("api.agent.get_agent_service", lambda: _PolishStreamService())
    observation = None
    if observation_failed:
        from unittest.mock import Mock
        observation = Mock(side_effect=RuntimeError("synthetic observation failure"))
        monkeypatch.setattr("services.first_work_service.record_first_work_action", observation)
    response = await client.post(
        "/api/v1/agent/stream",
        headers=headers,
        json={
            "project_id": state.project_id,
            "message": "给当前章节去AI味",
            "selected_text": "作者粘贴的合成原文。",
            "ui_action": "natural_polish",
            "metadata": {"current_file_id": state.file_id, "entry": "editor_natural_polish"},
        },
    )
    current = (await client.get("/api/v1/workspace/first-work", headers=headers)).json()
    if mode == "quota_rejected":
        assert response.status_code == 402
        assert started == 0
        assert current["first_ai_requested_at"] is None
    elif observation_failed:
        assert response.status_code == 200
        assert started == 1
        observation.assert_called_once()
        assert current["first_ai_requested_at"] is None
        assert db_session.exec(select(UsageQuota.ai_conversations_used).where(UsageQuota.id == quota.id)).one() == (2 if model_failed else 3)
    else:
        assert response.status_code == 200
        assert started == 1
        assert current["first_ai_requested_at"] is not None and current["static_guidance"] is False
        assert db_session.exec(select(UsageQuota.ai_conversations_used).where(UsageQuota.id == quota.id)).one() == (2 if model_failed else 3)
