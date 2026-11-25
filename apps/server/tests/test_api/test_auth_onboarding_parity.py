"""Parity tests between auth endpoints (/me, /login, /refresh, /verify-email) `onboarding_required` and GET /api/v1/persona/onboarding."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from api import verification
from config.datetime_utils import utcnow
from models import User, UserPersonaProfile, WorkspaceOnboarding
from services.core.auth_service import hash_password


async def _create_user(
    db_session: Session,
    *,
    username: str,
    email: str,
    created_at: datetime,
    password: str = "password123",
    email_verified: bool = True,
) -> User:
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        email_verified=email_verified,
        is_active=True,
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _login_full(client: AsyncClient, username: str, password: str = "password123") -> dict:
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
async def test_auth_me_login_refresh_match_persona_onboarding_for_new_and_completed_user(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    rollout_at = (utcnow() - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    monkeypatch.setenv("PERSONA_ONBOARDING_ROLLOUT_AT", rollout_at)
    monkeypatch.setenv("PERSONA_ONBOARDING_NEW_USER_WINDOW_DAYS", "7")

    await _create_user(
        db_session,
        username="parity_new_user",
        email="parity_new_user@example.com",
        created_at=utcnow() - timedelta(days=1),
    )

    login_payload = await _login_full(client, "parity_new_user")
    token = login_payload["access_token"]
    refresh_token = login_payload["refresh_token"]

    me_before = (await client.get("/api/auth/me", headers=_auth_headers(token))).json()
    persona_before = (await client.get("/api/v1/persona/onboarding", headers=_auth_headers(token))).json()
    refresh_before = (await client.post("/api/auth/refresh", json={"refresh_token": refresh_token})).json()

    assert persona_before["required"] is True
    assert login_payload["user"]["onboarding_required"] is True
    assert me_before["onboarding_required"] is True
    assert refresh_before["user"]["onboarding_required"] is True

    # Complete onboarding via PUT /api/v1/persona/onboarding
    save_resp = await client.put(
        "/api/v1/persona/onboarding",
        headers=_auth_headers(refresh_before["access_token"]),
        json={
            "selected_personas": ["serial"],
            "selected_goals": [],
            "experience_level": "beginner",
            "skipped": False,
        },
    )
    assert save_resp.status_code == 200
    assert save_resp.json()["required"] is False

    me_after = (await client.get("/api/auth/me", headers=_auth_headers(refresh_before["access_token"]))).json()
    persona_after = (
        await client.get("/api/v1/persona/onboarding", headers=_auth_headers(refresh_before["access_token"]))
    ).json()
    assert persona_after["required"] is False
    assert me_after["onboarding_required"] is False


@pytest.mark.integration
async def test_auth_me_matches_persona_onboarding_after_skip(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    rollout_at = (utcnow() - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    monkeypatch.setenv("PERSONA_ONBOARDING_ROLLOUT_AT", rollout_at)
    monkeypatch.setenv("PERSONA_ONBOARDING_NEW_USER_WINDOW_DAYS", "7")

    await _create_user(
        db_session,
        username="parity_skip_user",
        email="parity_skip_user@example.com",
        created_at=utcnow() - timedelta(hours=6),
    )
    token = (await _login_full(client, "parity_skip_user"))["access_token"]

    assert (await client.get("/api/auth/me", headers=_auth_headers(token))).json()["onboarding_required"] is True

    skip_resp = await client.put(
        "/api/v1/persona/onboarding",
        headers=_auth_headers(token),
        json={"selected_personas": [], "selected_goals": [], "experience_level": "beginner", "skipped": True},
    )
    assert skip_resp.status_code == 200

    me_after = (await client.get("/api/auth/me", headers=_auth_headers(token))).json()
    persona_after = (await client.get("/api/v1/persona/onboarding", headers=_auth_headers(token))).json()
    assert me_after["onboarding_required"] is False
    assert me_after["onboarding_required"] == persona_after["required"]


@pytest.mark.integration
async def test_auth_me_matches_persona_onboarding_for_pre_rollout_and_aged_users(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setenv("PERSONA_ONBOARDING_ROLLOUT_AT", "2026-03-05T16:00:00Z")
    monkeypatch.setenv("PERSONA_ONBOARDING_NEW_USER_WINDOW_DAYS", "7")
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "0")

    await _create_user(
        db_session,
        username="parity_pre_rollout",
        email="parity_pre_rollout@example.com",
        created_at=datetime(2026, 2, 1, 0, 0, 0),
    )
    await _create_user(
        db_session,
        username="parity_aged_out",
        email="parity_aged_out@example.com",
        created_at=utcnow() - timedelta(days=30),
    )

    for username in ("parity_pre_rollout", "parity_aged_out"):
        token = (await _login_full(client, username))["access_token"]
        me_resp = (await client.get("/api/auth/me", headers=_auth_headers(token))).json()
        persona_resp = (await client.get("/api/v1/persona/onboarding", headers=_auth_headers(token))).json()
        assert me_resp["onboarding_required"] is False
        assert me_resp["onboarding_required"] == persona_resp["required"]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("rollout_percent", "has_profile", "expected"),
    [
        ("100", False, True),
        ("0", False, False),
        ("100", True, False),
    ],
)
async def test_auth_me_matches_persona_onboarding_for_pending_first_work_qualification(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
    rollout_percent: str,
    has_profile: bool,
    expected: bool,
):
    monkeypatch.setenv("PERSONA_ONBOARDING_ROLLOUT_AT", "2026-03-05T16:00:00Z")
    monkeypatch.setenv("PERSONA_ONBOARDING_NEW_USER_WINDOW_DAYS", "7")
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", rollout_percent)

    username = f"parity_fw_{rollout_percent}_{int(has_profile)}"
    user = await _create_user(
        db_session,
        username=username,
        email=f"{username}@example.com",
        # Outside legacy 7-day window so only WorkspaceOnboarding pending state can require onboarding.
        created_at=utcnow() - timedelta(days=20),
    )
    db_session.add(WorkspaceOnboarding(user_id=user.id, status="pending"))
    if has_profile:
        db_session.add(
            UserPersonaProfile(
                user_id=user.id,
                selected_personas='["serial"]',
                selected_goals="[]",
                experience_level="beginner",
                skipped=False,
            )
        )
    db_session.commit()

    token = (await _login_full(client, username))["access_token"]
    me_resp = (await client.get("/api/auth/me", headers=_auth_headers(token))).json()
    persona_resp = (await client.get("/api/v1/persona/onboarding", headers=_auth_headers(token))).json()
    assert persona_resp["required"] is expected
    assert me_resp["onboarding_required"] is expected


@pytest.mark.integration
async def test_auth_me_degrades_gracefully_when_onboarding_lookup_fails(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    await _create_user(
        db_session,
        username="parity_db_error_user",
        email="parity_db_error_user@example.com",
        created_at=utcnow() - timedelta(days=1),
    )
    token = (await _login_full(client, "parity_db_error_user"))["access_token"]

    def _boom(_session, _user):
        raise OperationalError("SELECT 1", {}, Exception("transient db error"))

    monkeypatch.setattr(
        "services.features.persona_onboarding_service.is_persona_onboarding_required",
        _boom,
    )

    response = await client.get("/api/auth/me", headers=_auth_headers(token))
    assert response.status_code == 200
    assert response.json()["username"] == "parity_db_error_user"
    assert response.json()["onboarding_required"] is None


@pytest.mark.integration
@pytest.mark.parametrize(
    ("username", "age_days", "expected"),
    [
        ("parity_verify_new", 1, True),
        ("parity_verify_aged", 30, False),
    ],
)
async def test_verify_email_includes_onboarding_required_matching_persona_onboarding(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
    username: str,
    age_days: int,
    expected: bool,
):
    rollout_at = (utcnow() - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    monkeypatch.setenv("PERSONA_ONBOARDING_ROLLOUT_AT", rollout_at)
    monkeypatch.setenv("PERSONA_ONBOARDING_NEW_USER_WINDOW_DAYS", "7")
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "0")
    monkeypatch.setattr(verification, "verify_code", AsyncMock(return_value=(True, None)))

    email = f"{username}@example.com"
    await _create_user(
        db_session,
        username=username,
        email=email,
        created_at=utcnow() - timedelta(days=age_days),
        email_verified=False,
    )

    verify_resp = await client.post(
        "/api/auth/verify-email",
        json={"email": email, "code": "123456"},
    )
    assert verify_resp.status_code == 200
    verify_payload = verify_resp.json()
    assert verify_payload["user"]["email_verified"] is True
    assert verify_payload["user"]["onboarding_required"] is expected

    token = verify_payload["access_token"]
    persona_resp = (await client.get("/api/v1/persona/onboarding", headers=_auth_headers(token))).json()
    me_resp = (await client.get("/api/auth/me", headers=_auth_headers(token))).json()
    assert verify_payload["user"]["onboarding_required"] == persona_resp["required"]
    assert verify_payload["user"]["onboarding_required"] == me_resp["onboarding_required"]
