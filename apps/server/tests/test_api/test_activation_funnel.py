"""
Integration tests for activation funnel tracking.
"""

from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import (
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT,
    ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
    ACTIVATION_EVENT_PROJECT_CREATED,
    ACTIVATION_EVENT_SIGNUP_SUCCESS,
    ActivationEvent,
    User,
)
from services.core.auth_service import hash_password
from services.features.activation_event_service import activation_event_service


async def _create_user(
    db_session: Session,
    *,
    username: str,
    email: str,
    password: str = "password123",
    is_superuser: bool = False,
) -> User:
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        email_verified=True,
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _login(client: AsyncClient, username: str, password: str = "password123") -> str:
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
async def test_register_records_signup_activation_event(client: AsyncClient, db_session: Session, monkeypatch):
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "true")

    async def _fake_send_verification_code(_email: str, language: str = "zh"):
        return True, None

    monkeypatch.setattr("api.auth.send_verification_code", _fake_send_verification_code)
    monkeypatch.setattr("api.auth.clear_registration_verification", AsyncMock())

    response = await client.post(
        "/api/auth/register",
        json={
            "username": "new_activation_user",
            "email": "new_activation_user@example.com",
            "password": "password123",
        },
    )
    assert response.status_code == 200

    user = db_session.exec(
        select(User).where(User.username == "new_activation_user")
    ).first()
    assert user is not None

    signup_event = db_session.exec(
        select(ActivationEvent).where(
            ActivationEvent.user_id == user.id,
            ActivationEvent.event_name == ACTIVATION_EVENT_SIGNUP_SUCCESS,
        )
    ).first()
    assert signup_event is not None


@pytest.mark.integration
async def test_project_and_file_updates_record_activation_events(client: AsyncClient, db_session: Session):
    await _create_user(
        db_session,
        username="activation_flow_user",
        email="activation_flow_user@example.com",
    )
    token = await _login(client, "activation_flow_user")
    headers = _auth_headers(token)

    project_resp = await client.post(
        "/api/v1/projects",
        json={"name": "Activation Project", "project_type": "novel"},
        headers=headers,
    )
    assert project_resp.status_code == 200
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Chapter 1", "file_type": "draft", "content": ""},
        headers=headers,
    )
    assert file_resp.status_code == 200
    file_id = file_resp.json()["id"]

    save_resp = await client.put(
        f"/api/v1/files/{file_id}",
        json={"content": "first save from user"},
        headers=headers,
    )
    assert save_resp.status_code == 200

    ai_accept_resp = await client.put(
        f"/api/v1/files/{file_id}",
        json={
            "content": "first save from user with ai accepted change",
            "change_type": "ai_edit",
            "change_source": "ai",
            "change_summary": "review accepted",
        },
        headers=headers,
    )
    assert ai_accept_resp.status_code == 200

    # Repeat user save should not create duplicate first_file_saved event.
    second_save_resp = await client.put(
        f"/api/v1/files/{file_id}",
        json={"content": "second user save", "change_type": "edit", "change_source": "user"},
        headers=headers,
    )
    assert second_save_resp.status_code == 200

    events = db_session.exec(
        select(ActivationEvent).where(ActivationEvent.project_id == project_id)
    ).all()
    names = [event.event_name for event in events]

    assert ACTIVATION_EVENT_PROJECT_CREATED in names
    assert ACTIVATION_EVENT_FIRST_FILE_SAVED in names
    assert ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED in names
    assert names.count(ACTIVATION_EVENT_FIRST_FILE_SAVED) == 1


@pytest.mark.integration
async def test_admin_activation_funnel_counts_one_signup_cohort_within_a_day(
    client: AsyncClient, db_session: Session
):
    """Each step counts users of the same signup cohort who got there within 24 hours."""
    await _create_user(
        db_session,
        username="activation_admin",
        email="activation_admin@example.com",
        is_superuser=True,
    )
    user_a = await _create_user(
        db_session,
        username="activation_user_a",
        email="activation_user_a@example.com",
    )
    user_b = await _create_user(
        db_session,
        username="activation_user_b",
        email="activation_user_b@example.com",
    )
    user_late = await _create_user(
        db_session,
        username="activation_user_late",
        email="activation_user_late@example.com",
    )

    for event_name in (
        ACTIVATION_EVENT_SIGNUP_SUCCESS,
        ACTIVATION_EVENT_PROJECT_CREATED,
        ACTIVATION_EVENT_FIRST_FILE_SAVED,
        ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT,
        ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED,
        ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ):
        activation_event_service.record_once(db_session, user_id=user_a.id, event_name=event_name)
    activation_event_service.record_once(
        db_session, user_id=user_b.id, event_name=ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT
    )
    # Sent the first message two days after signing up: outside the first-day window.
    late = activation_event_service.record_once(
        db_session, user_id=user_late.id, event_name=ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT
    )
    user_late.created_at = late.created_at - timedelta(days=2)
    db_session.add(user_late)
    db_session.commit()

    admin_token = await _login(client, "activation_admin")

    response = await client.get(
        "/api/admin/dashboard/activation-funnel?days=30",
        headers=_auth_headers(admin_token),
    )
    assert response.status_code == 200
    data = response.json()

    assert data["window_days"] == 30
    assert 0 <= data["activation_rate"] <= 1

    steps = {step["event_name"]: step["users"] for step in data["steps"]}
    assert list(steps) == [
        ACTIVATION_EVENT_SIGNUP_SUCCESS,
        ACTIVATION_EVENT_PROJECT_CREATED,
        ACTIVATION_EVENT_FIRST_FILE_SAVED,
        ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT,
        ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED,
        ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ]

    # Other xdist workers may share the SQLite DB: compare against this test's users only.
    funnel_users = {
        event_name: _cohort_users(db_session, event_name)
        for event_name in steps
        if event_name != ACTIVATION_EVENT_SIGNUP_SUCCESS
    }
    assert user_a.id in funnel_users[ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED]
    assert user_b.id in funnel_users[ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT]
    assert user_b.id not in funnel_users[ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED]
    assert user_late.id not in funnel_users[ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT]
    # The signup step is the cohort (customers only), not the signup_success rows.
    assert steps[ACTIVATION_EVENT_SIGNUP_SUCCESS] >= 3
    assert steps[ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT] >= 2


def _cohort_users(db_session: Session, event_name: str) -> set[str]:
    """Users the service counts for one step (same rule as get_funnel_stats)."""
    from services.features.activation_event_service import ACTIVATION_FUNNEL_COHORT_HOURS

    rows = db_session.exec(
        select(ActivationEvent.user_id, ActivationEvent.created_at, User.created_at)
        .join(User, User.id == ActivationEvent.user_id)
        .where(ActivationEvent.event_name == event_name, User.is_superuser.is_(False))
    ).all()
    return {
        user_id
        for user_id, event_at, signed_up_at in rows
        if event_at - signed_up_at <= timedelta(hours=ACTIVATION_FUNNEL_COHORT_HOURS)
    }
