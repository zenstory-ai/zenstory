"""Tests for admin prompt reload endpoint."""

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import User
from services.core.auth_service import hash_password


async def create_user(
    db_session: Session,
    username: str,
    email: str,
    password: str = "password123",
    *,
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


async def login_user(client: AsyncClient, username: str, password: str = "password123") -> str:
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
async def test_admin_prompts_reload_success_triggers_reload_function(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    admin = await create_user(
        db_session,
        "admin_prompt_reload",
        "admin_prompt_reload@example.com",
        is_superuser=True,
    )
    token = await login_user(client, admin.username)

    called = {"count": 0}

    def fake_reload_prompts() -> dict[str, int | str]:
        called["count"] += 1
        return {"source": "primary_database", "count": 2, "version": 4}

    monkeypatch.setattr("agent.prompts.reload_prompts", fake_reload_prompts)

    response = await client.post(
        "/api/admin/prompts/reload",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json() == {
        "message": "System prompt configurations reloaded successfully",
        "source": "primary_database",
        "count": 2,
        "version": 4,
    }
    assert called["count"] == 1


@pytest.mark.integration
async def test_admin_prompts_reload_returns_generic_error_when_source_unavailable(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    admin = await create_user(
        db_session,
        "admin_prompt_reload_failure",
        "admin_prompt_reload_failure@example.com",
        is_superuser=True,
    )
    token = await login_user(client, admin.username)

    def fake_reload_prompts() -> dict[str, int | str]:
        raise RuntimeError("postgresql://user:password@private-host/database")

    monkeypatch.setattr("agent.prompts.reload_prompts", fake_reload_prompts)

    response = await client.post(
        "/api/admin/prompts/reload",
        headers=auth_headers(token),
    )

    assert response.status_code == 503
    body = response.json()
    assert body["error_code"] == "ERR_SERVICE_UNAVAILABLE"
    assert "password" not in str(body)


@pytest.mark.integration
async def test_admin_prompts_reload_forbidden_for_non_superuser(
    client: AsyncClient,
    db_session: Session,
):
    user = await create_user(
        db_session,
        "prompt_reload_normal_user",
        "prompt_reload_normal_user@example.com",
    )
    token = await login_user(client, user.username)

    response = await client.post(
        "/api/admin/prompts/reload",
        headers=auth_headers(token),
    )

    assert response.status_code == 403
