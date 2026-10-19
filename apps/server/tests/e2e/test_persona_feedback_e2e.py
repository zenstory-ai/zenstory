"""
Request-driven persona and feedback e2e workflows.

These tests cover onboarding persistence and feedback submission through the
real HTTP layer without depending on browser-only behavior.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from models import User, UserFeedback, UserPersonaProfile
from services.core.auth_service import hash_password

pytestmark = pytest.mark.e2e

VALID_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\xf8\x0f\x00\x01\x01\x01\x00\x18\xdd\x8d\xe1"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _identity(prefix: str) -> tuple[str, str]:
    suffix = uuid4().hex[:8]
    username = f"{prefix}_{suffix}"
    return username, f"{username}@example.com"


async def _create_user(
    db_session: Session,
    *,
    prefix: str,
    created_at: datetime | None = None,
    password: str = "password123",
) -> User:
    username, email = _identity(prefix)
    now = created_at or utcnow()
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        email_verified=True,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _login(client: AsyncClient, *, identifier: str, password: str = "password123") -> dict:
    response = await client.post(
        "/api/auth/login",
        data={"username": identifier, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


@pytest.mark.asyncio
async def test_persona_onboarding_roundtrip_requires_then_persists_profile(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    rollout_at = (utcnow() - timedelta(days=2)).isoformat().replace("+00:00", "Z")
    monkeypatch.setenv("PERSONA_ONBOARDING_ROLLOUT_AT", rollout_at)
    monkeypatch.setenv("PERSONA_ONBOARDING_NEW_USER_WINDOW_DAYS", "7")

    user = await _create_user(
        db_session,
        prefix="persona_flow",
        created_at=utcnow() - timedelta(days=1),
    )
    login_payload = await _login(client, identifier=user.username)
    headers = _auth_headers(login_payload["access_token"])

    onboarding_response = await client.get("/api/v1/persona/onboarding", headers=headers)
    assert onboarding_response.status_code == 200
    onboarding_payload = onboarding_response.json()
    assert onboarding_payload["required"] is True
    assert onboarding_payload["profile"] is None

    save_response = await client.put(
        "/api/v1/persona/onboarding",
        headers=headers,
        json={
            "selected_personas": ["serial", "professional"],
            "selected_goals": ["improveQuality", "monetize"],
            "experience_level": "advanced",
            "skipped": False,
        },
    )
    assert save_response.status_code == 200
    saved_payload = save_response.json()
    assert saved_payload["required"] is False
    assert saved_payload["profile"]["selected_personas"] == ["serial", "professional"]
    assert saved_payload["profile"]["selected_goals"] == ["improveQuality", "monetize"]
    assert saved_payload["profile"]["experience_level"] == "advanced"

    stored_profile = db_session.exec(
        select(UserPersonaProfile).where(UserPersonaProfile.user_id == user.id)
    ).first()
    assert stored_profile is not None
    assert stored_profile.skipped is False
    assert "serial" in json.loads(stored_profile.selected_personas)


@pytest.mark.asyncio
async def test_persona_onboarding_pre_rollout_user_is_not_forced_into_flow(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("PERSONA_ONBOARDING_ROLLOUT_AT", "2026-03-05T16:00:00Z")

    user = await _create_user(
        db_session,
        prefix="persona_old",
        created_at=datetime(2026, 2, 20, 0, 0, 0),
    )
    login_payload = await _login(client, identifier=user.email)

    response = await client.get(
        "/api/v1/persona/onboarding",
        headers=_auth_headers(login_payload["access_token"]),
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["required"] is False
    assert payload["profile"] is None


@pytest.mark.asyncio
async def test_feedback_submission_roundtrip_persists_text_screenshot_and_debug_fields(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    monkeypatch.setenv("FEEDBACK_UPLOAD_DIR", str(tmp_path))

    user = await _create_user(db_session, prefix="feedback_flow")
    login_payload = await _login(client, identifier=user.username)
    headers = _auth_headers(login_payload["access_token"])

    response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Toolbar button overlaps after theme switch.",
            "source_page": "editor",
            "source_route": "/project/test-project",
            "trace_id": "trace-e2e-001",
            "request_id": "req-e2e-001",
            "agent_run_id": "run-e2e-001",
            "project_id": "project-e2e-001",
            "agent_session_id": "session-e2e-001",
        },
        files={"screenshot": ("bug.png", VALID_PNG_BYTES, "image/png")},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["message"] == "Feedback submitted successfully."

    stored_feedback = db_session.exec(
        select(UserFeedback).where(UserFeedback.id == payload["id"])
    ).first()
    assert stored_feedback is not None
    assert stored_feedback.user_id == user.id
    assert stored_feedback.source_page == "editor"
    assert stored_feedback.source_route == "/project/test-project"
    assert stored_feedback.trace_id == "trace-e2e-001"
    assert stored_feedback.request_id == "req-e2e-001"
    assert stored_feedback.agent_run_id == "run-e2e-001"
    assert stored_feedback.project_id == "project-e2e-001"
    assert stored_feedback.agent_session_id == "session-e2e-001"
    assert stored_feedback.screenshot_original_name == "bug.png"
    assert stored_feedback.screenshot_content_type == "image/png"
    assert stored_feedback.screenshot_size_bytes == len(VALID_PNG_BYTES)
    assert stored_feedback.screenshot_path is not None
    assert Path(stored_feedback.screenshot_path).exists()


@pytest.mark.asyncio
async def test_feedback_invalid_source_page_is_rejected(client: AsyncClient, db_session: Session):
    user = await _create_user(db_session, prefix="feedback_invalid")
    login_payload = await _login(client, identifier=user.email)

    response = await client.post(
        "/api/v1/feedback",
        data={
            "issue_text": "Bad source page should fail validation.",
            "source_page": "home",
        },
        headers=_auth_headers(login_payload["access_token"]),
    )

    assert response.status_code == 422
    payload = response.json()
    assert payload["error_code"] == ErrorCode.VALIDATION_ERROR
