"""Persona onboarding accepts the short-story and screenwriter identities."""

from __future__ import annotations

from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import User, UserPersonaProfile
from services.core.auth_service import hash_password


async def _create_user_and_login(client: AsyncClient, db_session: Session, username: str) -> str:
    created_at = datetime(2026, 3, 6, 0, 0, 0)
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        created_at=created_at,
        updated_at=created_at,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


@pytest.mark.integration
async def test_short_story_and_screenwriter_personas_are_saved(
    client: AsyncClient,
    db_session: Session,
):
    token = await _create_user_and_login(client, db_session, "persona_new_ids_user")

    response = await client.put(
        "/api/v1/persona/onboarding",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "selected_personas": ["screenwriter", "short_story", "serial"],
            "selected_goals": ["finishBook"],
            "experience_level": "beginner",
            "skipped": False,
        },
    )

    assert response.status_code == 200
    assert response.json()["profile"]["selected_personas"] == [
        "screenwriter",
        "short_story",
        "serial",
    ]

    # Storage stays a JSON string column; no schema change.
    user = db_session.exec(select(User).where(User.username == "persona_new_ids_user")).one()
    profile = db_session.exec(
        select(UserPersonaProfile).where(UserPersonaProfile.user_id == user.id)
    ).one()
    assert profile.selected_personas == '["screenwriter", "short_story", "serial"]'


@pytest.mark.integration
async def test_unknown_persona_ids_are_still_rejected(
    client: AsyncClient,
    db_session: Session,
):
    token = await _create_user_and_login(client, db_session, "persona_new_ids_reject")

    response = await client.put(
        "/api/v1/persona/onboarding",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "selected_personas": ["short_story", "playwright"],
            "selected_goals": [],
            "experience_level": "beginner",
            "skipped": False,
        },
    )

    assert response.status_code == 400
