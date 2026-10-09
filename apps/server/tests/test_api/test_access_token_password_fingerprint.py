"""Access tokens carry a password fingerprint (``pwf``) so a password change ends them."""
from uuid import uuid4

import pytest
from httpx import AsyncClient
from jose import jwt

from models import User
from services.core.auth_service import (
    ALGORITHM,
    SECRET_KEY,
    create_access_token,
    get_optional_current_user,
    hash_password,
)

PASSWORD = "old-secret-1"


def _make_user(db_session) -> User:
    user = User(
        username=f"u{uuid4().hex[:12]}",
        email=f"pwf-{uuid4().hex}@example.com",
        hashed_password=hash_password(PASSWORD),
        email_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _login(client: AsyncClient, user: User, password: str = PASSWORD) -> dict:
    response = await client.post("/api/auth/login", data={"username": user.email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.integration
async def test_issued_access_tokens_carry_the_fingerprint(client, db_session):
    user = _make_user(db_session)

    tokens = await _login(client, user)
    refreshed = await client.post("/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    for token in (tokens["access_token"], refreshed.json()["access_token"]):
        claims = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        assert claims["sub"] == user.id
        assert len(claims["pwf"]) == 16
        assert claims["pwf"] not in user.hashed_password


@pytest.mark.integration
async def test_legacy_token_without_fingerprint_still_works(client, db_session):
    user = _make_user(db_session)
    legacy = create_access_token({"sub": user.id})

    response = await client.get("/api/auth/me", headers=_bearer(legacy))

    assert response.status_code == 200
    assert response.json()["id"] == user.id


@pytest.mark.integration
async def test_change_password_ends_existing_access_tokens(client, db_session):
    user = _make_user(db_session)
    first = await _login(client, user)
    second = await _login(client, user)

    changed = await client.post(
        "/api/auth/change-password",
        json={"old_password": PASSWORD, "new_password": "new-secret-2"},
        headers=_bearer(first["access_token"]),
    )

    assert changed.status_code == 200
    for old in (first, second):
        response = await client.get("/api/auth/me", headers=_bearer(old["access_token"]))
        assert response.status_code == 401
        assert response.json()["error_code"] == "ERR_AUTH_TOKEN_INVALID"
        validated = await client.get("/api/auth/validate-token", params={"token": old["access_token"]})
        assert validated.status_code == 401
    fresh = await _login(client, user, "new-secret-2")
    assert (await client.get("/api/auth/me", headers=_bearer(fresh["access_token"]))).status_code == 200


@pytest.mark.integration
async def test_optional_user_ignores_a_stale_fingerprint(db_session):
    user = _make_user(db_session)
    stale = create_access_token({"sub": user.id, "pwf": "0" * 16})
    legacy = create_access_token({"sub": user.id})

    assert await get_optional_current_user(token=stale, session=db_session) is None
    current = await get_optional_current_user(token=legacy, session=db_session)
    assert current is not None and current.id == user.id
