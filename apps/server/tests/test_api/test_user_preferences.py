"""Account interface language: stored at sign-up, read after sign-in, updated when the user switches."""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlmodel import select

from models import User
from services.core.auth_service import hash_password


async def _login_headers(client: AsyncClient, db_session, username: str) -> dict[str, str]:
    db_session.add(
        User(
            username=username,
            email=f"{username}@example.com",
            hashed_password=hash_password("testpassword123"),
            email_verified=True,
            is_active=True,
        )
    )
    db_session.commit()
    response = await client.post("/api/auth/login", data={"username": username, "password": "testpassword123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.integration
async def test_language_follows_the_account_across_devices(client: AsyncClient, db_session):
    headers = await _login_headers(client, db_session, "pref_user")

    assert (await client.get("/api/auth/me", headers=headers)).json()["ui_language"] is None

    response = await client.put("/api/auth/me/preferences", headers=headers, json={"ui_language": "en-US"})
    assert response.status_code == 200
    assert response.json() == {"ui_language": "en"}
    # The user payload the app already loads carries it, so a new device can open in it.
    assert (await client.get("/api/auth/me", headers=headers)).json()["ui_language"] == "en"
    login = await client.post("/api/auth/login", data={"username": "pref_user", "password": "testpassword123"})
    assert login.json()["user"]["ui_language"] == "en"

    # An interface language the app does not offer leaves the stored choice alone.
    response = await client.put("/api/auth/me/preferences", headers=headers, json={"ui_language": "fr"})
    assert response.json() == {"ui_language": "en"}


@pytest.mark.integration
async def test_preferences_require_sign_in(client: AsyncClient):
    response = await client.put("/api/auth/me/preferences", json={"ui_language": "en"})
    assert response.status_code == 401


@pytest.mark.integration
async def test_sign_up_stores_the_interface_language(client: AsyncClient, db_session, monkeypatch):
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "true")
    monkeypatch.setattr("api.auth.clear_registration_verification", AsyncMock())
    monkeypatch.setattr("api.auth.send_verification_code", AsyncMock(return_value=(True, None)))

    response = await client.post(
        "/api/auth/register",
        json={
            "username": "english_writer",
            "email": "english_writer@example.com",
            "password": "testpassword123",
            "language": "en",
        },
    )
    assert response.status_code == 200

    from models import UserPreference

    user = db_session.exec(select(User).where(User.email == "english_writer@example.com")).one()
    assert db_session.get(UserPreference, user.id).ui_language == "en"


@pytest.mark.integration
def test_staging_an_unsupported_language_reads_nothing(db_session):
    """Sign-up stages the language inside its own transaction: an unsupported value must not touch the session."""
    from unittest.mock import MagicMock

    from services.features.user_preference_service import set_ui_language

    session = MagicMock()
    assert set_ui_language(session, "user-1", "fr") is None
    assert session.method_calls == []


@pytest.mark.integration
async def test_only_if_unset_backfills_missing_or_null_without_overwriting_saved_language(
    client: AsyncClient, db_session
):
    """Backfill writes when absent or NULL, and returns the existing value untouched when already set."""
    from models import UserPreference

    headers = await _login_headers(client, db_session, "only_if_unset_user")
    user = db_session.exec(select(User).where(User.username == "only_if_unset_user")).one()

    # No preference row yet -> writes 'en' and returns 'en'.
    resp = await client.put(
        "/api/auth/me/preferences",
        headers=headers,
        json={"ui_language": "en-US", "only_if_unset": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ui_language": "en"}
    assert (await client.get("/api/auth/me", headers=headers)).json()["ui_language"] == "en"

    # Already set to 'en' -> only_if_unset='zh' must NOT overwrite it, and returns actual 'en'.
    resp = await client.put(
        "/api/auth/me/preferences",
        headers=headers,
        json={"ui_language": "zh-CN", "only_if_unset": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ui_language": "en"}
    assert (await client.get("/api/auth/me", headers=headers)).json()["ui_language"] == "en"

    # Explicit switch (only_if_unset omitted / False) still overwrites to 'zh'.
    resp = await client.put(
        "/api/auth/me/preferences",
        headers=headers,
        json={"ui_language": "zh"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ui_language": "zh"}

    # Row exists with NULL ui_language -> only_if_unset writes the new language.
    pref = db_session.get(UserPreference, user.id)
    assert pref is not None
    pref.ui_language = None
    db_session.add(pref)
    db_session.commit()

    resp = await client.put(
        "/api/auth/me/preferences",
        headers=headers,
        json={"ui_language": "en", "only_if_unset": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ui_language": "en"}
    assert (await client.get("/api/auth/me", headers=headers)).json()["ui_language"] == "en"


@pytest.mark.integration
def test_get_ui_language_failure_preserves_outer_transaction(db_session):
    """A failing preference read inside an open transaction must roll back only its savepoint."""
    from sqlalchemy.exc import OperationalError

    from services.core.auth_service import hash_password
    from services.features.user_preference_service import get_ui_language

    user = User(
        username="savepoint_user",
        email="savepoint_user@example.com",
        hashed_password=hash_password("password123"),
        email_verified=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    assert db_session.in_transaction()

    original_get = db_session.get

    def failing_get(entity, ident, *args, **kwargs):
        if entity.__name__ == "UserPreference":
            raise OperationalError("SELECT * FROM user_preference", {}, Exception("table missing"))
        return original_get(entity, ident, *args, **kwargs)

    db_session.get = failing_get  # type: ignore[method-assign]
    try:
        assert get_ui_language(db_session, user.id) is None
    finally:
        db_session.get = original_get  # type: ignore[method-assign]

    # Outer transaction and uncommitted flush remain intact.
    assert db_session.in_transaction()
    db_session.commit()
    assert db_session.exec(select(User).where(User.email == "savepoint_user@example.com")).first() is not None
