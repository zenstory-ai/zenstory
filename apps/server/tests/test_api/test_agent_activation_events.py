"""New-user funnel milestones recorded by /agent/stream: first message sent, first reply succeeded."""

from uuid import uuid4

import pytest
from sqlmodel import select

from models import (
    ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT,
    ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED,
    ActivationEvent,
    Project,
    User,
)
from services.core.auth_service import create_access_token


def _author(session):
    suffix = uuid4().hex
    user = User(username=suffix, email=f"{suffix}@example.test", hashed_password="unused", email_verified=True)
    session.add(user)
    session.commit()
    project = Project(name="我的小说", owner_id=user.id, project_type="novel")
    session.add(project)
    session.commit()
    headers = {"Authorization": f"Bearer {create_access_token({'sub': user.id})}"}
    return user, project, headers


def _service(*frames: str):
    """Replaces the paid execution boundary; auth, quota reservation, pump and billing stay real."""

    class NoPaidModelService:
        async def process_stream(self, **_kwargs):
            yield 'event: session_started\ndata: {"session_id":"synthetic"}\n\n'
            for frame in frames:
                yield frame

    return NoPaidModelService()


def _events(session, user_id: str) -> dict[str, ActivationEvent]:
    rows = session.exec(select(ActivationEvent).where(ActivationEvent.user_id == user_id)).all()
    return {row.event_name: row for row in rows}


@pytest.mark.integration
async def test_completed_first_round_records_message_and_reply(client, db_session, monkeypatch):
    user, project, headers = _author(db_session)
    monkeypatch.setattr("api.agent.get_agent_service", lambda: _service("event: done\ndata: {}\n\n"))

    response = await client.post(
        "/api/v1/agent/stream",
        headers=headers,
        json={"project_id": project.id, "message": "想写个重生复仇的都市文", "metadata": {"entry": "starter_example"}},
    )
    assert response.status_code == 200 and "event: done" in response.text

    events = _events(db_session, user.id)
    assert events[ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT].event_metadata == {"entry": "starter_example"}
    assert events[ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT].project_id == project.id
    assert events[ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED].event_metadata == {"entry": "starter_example"}

    # Later messages keep the first records (one per account).
    second = await client.post(
        "/api/v1/agent/stream",
        headers=headers,
        json={"project_id": project.id, "message": "接着写"},
    )
    assert second.status_code == 200
    assert _events(db_session, user.id)[ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT].event_metadata == {
        "entry": "starter_example"
    }


@pytest.mark.integration
async def test_refunded_failure_records_the_message_but_not_a_reply(client, db_session, monkeypatch):
    user, project, headers = _author(db_session)
    monkeypatch.setattr(
        "api.agent.get_agent_service",
        lambda: _service('event: error\ndata: {"message":"boom","code":"ERR_INTERNAL","retryable":true}\n\n'),
    )

    response = await client.post(
        "/api/v1/agent/stream",
        headers=headers,
        json={"project_id": project.id, "message": "写个开头"},
    )
    assert response.status_code == 200

    events = _events(db_session, user.id)
    assert events[ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT].event_metadata == {"entry": "typed"}
    assert ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED not in events


@pytest.mark.integration
async def test_a_broken_activation_write_never_breaks_the_round(client, db_session, monkeypatch):
    _user, project, headers = _author(db_session)
    monkeypatch.setattr("api.agent.get_agent_service", lambda: _service("event: done\ndata: {}\n\n"))

    def broken(*_args, **_kwargs):
        raise RuntimeError("activation store unavailable")

    monkeypatch.setattr("api.agent.activation_event_service.record_once_best_effort", broken)

    response = await client.post(
        "/api/v1/agent/stream",
        headers=headers,
        json={"project_id": project.id, "message": "写个开头"},
    )
    assert response.status_code == 200
    assert "event: done" in response.text


def test_the_offloaded_activation_write_swallows_its_own_failures(monkeypatch):
    """PostgreSQL path: the background thread's short session failing only logs."""
    import api.agent as agent_api

    def broken_session():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(agent_api, "create_session", broken_session)

    agent_api._record_activation_sync("user-1", "project-1", ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT, "typed")


def test_best_effort_record_rolls_back_and_continues_when_the_write_fails(monkeypatch):
    from unittest.mock import MagicMock

    from services.features.activation_event_service import activation_event_service

    session = MagicMock()

    def broken(*_args, **_kwargs):
        raise RuntimeError("unique index unavailable")

    monkeypatch.setattr(activation_event_service, "record_once", broken)

    activation_event_service.record_once_best_effort(
        session, user_id="user-1", event_name=ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT
    )
    session.rollback.assert_called_once()
