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
