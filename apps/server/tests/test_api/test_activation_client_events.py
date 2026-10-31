"""New-user screen milestones reported by the web app (POST /api/v1/activation/events)."""

from uuid import uuid4

import pytest
from sqlmodel import select

from models import (
    ACTIVATION_EVENT_AI_FILE_OPENED,
    ACTIVATION_EVENT_AI_SEND_BLOCKED_QUOTA,
    ACTIVATION_EVENT_DASHBOARD_VIEWED,
    ACTIVATION_EVENT_PERSONA_SUBMITTED,
    CHANGE_SOURCE_AI,
    ActivationEvent,
    File,
    FileVersion,
    Project,
    User,
)
from services.core.auth_service import create_access_token

URL = "/api/v1/activation/events"


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


def _file(session, project, *, change_source: str | None, file_type: str = "draft") -> File:
    file = File(project_id=project.id, title="第一章", content="正文", file_type=file_type)
    session.add(file)
    session.commit()
    if change_source is not None:
        session.add(
            FileVersion(
                file_id=file.id,
                project_id=project.id,
                version_number=1,
                content="正文",
                is_base_version=True,
                change_type="create",
                change_source=change_source,
            )
        )
        session.commit()
    return file


def _events(session, user_id: str) -> dict[str, ActivationEvent]:
    session.expire_all()
    rows = session.exec(select(ActivationEvent).where(ActivationEvent.user_id == user_id)).all()
    return {row.event_name: row for row in rows}


@pytest.mark.integration
async def test_dashboard_viewed_is_recorded_once_per_account(client, db_session):
    user, _project, headers = _author(db_session)

    first = await client.post(URL, headers=headers, json={"event_name": "dashboard_viewed", "is_mobile": True})
    second = await client.post(URL, headers=headers, json={"event_name": "dashboard_viewed", "is_mobile": False})

    assert first.status_code == 200 and first.json() == {"recorded": True}
    assert second.status_code == 200 and second.json() == {"recorded": True}
    rows = db_session.exec(
        select(ActivationEvent).where(
            ActivationEvent.user_id == user.id,
            ActivationEvent.event_name == ACTIVATION_EVENT_DASHBOARD_VIEWED,
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].event_metadata == {"is_mobile": True}


@pytest.mark.integration
async def test_ai_file_opened_requires_an_ai_version_of_an_owned_file(client, db_session):
    user, project, headers = _author(db_session)
    own_file = _file(db_session, project, change_source="user")
    ai_file = _file(db_session, project, change_source=CHANGE_SOURCE_AI, file_type="outline")

    not_ai = await client.post(
        URL, headers=headers, json={"event_name": "ai_file_opened", "file_id": own_file.id, "is_mobile": True}
    )
    assert not_ai.status_code == 200 and not_ai.json() == {"recorded": False}
    assert ACTIVATION_EVENT_AI_FILE_OPENED not in _events(db_session, user.id)

    opened = await client.post(
        URL,
        headers=headers,
        json={"event_name": "ai_file_opened", "file_id": ai_file.id, "is_mobile": True, "while_ai_writing": False},
    )
    assert opened.status_code == 200 and opened.json() == {"recorded": True}
    event = _events(db_session, user.id)[ACTIVATION_EVENT_AI_FILE_OPENED]
    assert event.project_id == project.id
    # file_type comes from the stored file, not from the client.
    assert event.event_metadata == {"is_mobile": True, "file_type": "outline", "while_ai_writing": False}


@pytest.mark.integration
async def test_ai_file_opened_ignores_another_accounts_file(client, db_session):
    user, _project, headers = _author(db_session)
    _other, other_project, _ = _author(db_session)
    foreign = _file(db_session, other_project, change_source=CHANGE_SOURCE_AI)

    response = await client.post(URL, headers=headers, json={"event_name": "ai_file_opened", "file_id": foreign.id})

    assert response.status_code == 200 and response.json() == {"recorded": False}
    assert _events(db_session, user.id) == {}


@pytest.mark.integration
async def test_ai_send_blocked_quota_keeps_only_enum_metadata(client, db_session):
    user, project, headers = _author(db_session)

    response = await client.post(
        URL,
        headers=headers,
        json={
            "event_name": "ai_send_blocked_quota",
            "project_id": project.id,
            "surface": "chat",
            "server_refused": False,
            "is_mobile": False,
        },
    )

    assert response.status_code == 200 and response.json() == {"recorded": True}
    event = _events(db_session, user.id)[ACTIVATION_EVENT_AI_SEND_BLOCKED_QUOTA]
    assert event.project_id == project.id
    assert event.event_metadata == {"surface": "chat", "server_refused": False, "is_mobile": False}


@pytest.mark.integration
async def test_foreign_project_id_is_not_attached(client, db_session):
    user, _project, headers = _author(db_session)
    _other, other_project, _ = _author(db_session)

    response = await client.post(
        URL,
        headers=headers,
        json={"event_name": "ai_send_blocked_quota", "project_id": other_project.id, "surface": "dashboard"},
    )

    assert response.status_code == 200
    assert _events(db_session, user.id)[ACTIVATION_EVENT_AI_SEND_BLOCKED_QUOTA].project_id is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "body",
    [
        {"event_name": "first_ai_message_sent"},  # server-owned milestone
        {"event_name": "persona_submitted"},  # must say whether it was skipped
        {"event_name": "dashboard_viewed", "prompt": "一段提示词"},  # no free-form fields
        {"event_name": "ai_send_blocked_quota", "surface": "editor"},
        {"event_name": "ai_file_opened"},  # needs a file
        {"event_name": "ai_file_opened", "file_id": "x" * 65},
    ],
)
async def test_rejects_unknown_events_and_free_form_fields(client, db_session, body):
    user, _project, headers = _author(db_session)

    response = await client.post(URL, headers=headers, json=body)

    assert response.status_code in (400, 422)
    assert _events(db_session, user.id) == {}


@pytest.mark.integration
async def test_requires_authentication(client):
    response = await client.post(URL, json={"event_name": "dashboard_viewed"})
    assert response.status_code == 401


@pytest.mark.integration
async def test_persona_submitted_keeps_the_first_answer_kind(client, db_session):
    user, _project, headers = _author(db_session)

    skipped = await client.post(URL, headers=headers, json={"event_name": "persona_submitted", "skipped": True})
    answered = await client.post(URL, headers=headers, json={"event_name": "persona_submitted", "skipped": False})

    assert skipped.status_code == 200 and answered.status_code == 200
    rows = db_session.exec(
        select(ActivationEvent).where(
            ActivationEvent.user_id == user.id,
            ActivationEvent.event_name == ACTIVATION_EVENT_PERSONA_SUBMITTED,
        )
    ).all()
    assert len(rows) == 1
    assert rows[0].event_metadata == {"skipped": True}


@pytest.mark.integration
async def test_rate_limit_is_per_account_across_client_addresses(client, db_session, monkeypatch):
    import api.activation as activation_api

    user, _project, headers = _author(db_session)
    calls = []

    def limit(request, key, max_requests, window_seconds, **kwargs):
        calls.append((key, kwargs))
        return len(calls) <= 1, 0

    monkeypatch.setattr(activation_api, "check_rate_limit", limit)
    first = await client.post(URL, headers=headers, json={"event_name": "dashboard_viewed"})
    second = await client.post(URL, headers=headers, json={"event_name": "dashboard_viewed"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert calls == [(f"activation_events:{user.id}", {"include_client_ip": False})] * 2
