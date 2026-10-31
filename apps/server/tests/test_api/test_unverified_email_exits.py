"""Exits for password accounts whose email is not verified yet.

- A password-proven login tells the author which address to verify, so a
  username login can still reach the verification page.
- A resend during the cooldown answers 429 with the remaining seconds instead
  of a generic delivery failure, so the page can say how long to wait.
- Unknown and verified addresses keep the masked generic success.
"""

from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from core.error_codes import ErrorCode


def _password_user(db_session, *, username: str, email: str, verified: bool):
    from models import User
    from services.core.auth_service import hash_password

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password("OwnedPassword1!"),
        email_verified=verified,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.mark.integration
async def test_unverified_username_login_names_the_address_to_verify(client: AsyncClient, db_session):
    _password_user(db_session, username="pending_writer", email="pending_writer@example.com", verified=False)

    response = await client.post(
        "/api/auth/login",
        data={"username": "pending_writer", "password": "OwnedPassword1!"},
    )

    assert response.status_code == 403
    body = response.json()
    assert body["error_code"] == ErrorCode.AUTH_EMAIL_NOT_VERIFIED
    assert body["error_detail"] == {"email": "pending_writer@example.com"}


@pytest.mark.integration
async def test_wrong_password_for_unverified_account_reveals_nothing(client: AsyncClient, db_session):
    _password_user(db_session, username="pending_guess", email="pending_guess@example.com", verified=False)

    response = await client.post(
        "/api/auth/login",
        data={"username": "pending_guess", "password": "not-the-password"},
    )

    assert response.status_code == 401
    body = response.json()
    assert body["error_code"] == ErrorCode.AUTH_INVALID_CREDENTIALS
    assert "pending_guess@example.com" not in response.text


@pytest.mark.integration
async def test_resend_during_cooldown_reports_remaining_seconds(
    client: AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch
):
    _password_user(db_session, username="cooldown_writer", email="cooldown_writer@example.com", verified=False)
    send = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("api.verification.send_verification_code", send)
    monkeypatch.setattr("api.verification.get_remaining_cooldown", lambda _email: 42)

    response = await client.post(
        "/api/auth/resend-verification",
        json={"email": "cooldown_writer@example.com"},
    )

    assert response.status_code == 429
    body = response.json()
    assert body["error_code"] == ErrorCode.AUTH_VERIFICATION_COOLDOWN
    assert body["error_detail"] == {"retry_after_seconds": 42}
    assert response.headers["retry-after"] == "42"
    send.assert_not_awaited()


@pytest.mark.integration
@pytest.mark.parametrize("existing", ["unknown", "verified"])
async def test_resend_cooldown_does_not_unmask_unknown_or_verified(
    client: AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch, existing: str
):
    if existing == "verified":
        _password_user(db_session, username="done_writer", email="masked@example.com", verified=True)
    send = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("api.verification.send_verification_code", send)
    monkeypatch.setattr("api.verification.get_remaining_cooldown", lambda _email: 42)

    response = await client.post("/api/auth/resend-verification", json={"email": "masked@example.com"})

    assert response.status_code == 200
    assert response.json()["email"] == "masked@example.com"
    send.assert_not_awaited()


@pytest.mark.integration
async def test_resend_without_cooldown_still_sends(
    client: AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch
):
    _password_user(db_session, username="ready_writer", email="ready_writer@example.com", verified=False)
    send = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("api.verification.send_verification_code", send)
    monkeypatch.setattr("api.verification.get_remaining_cooldown", lambda _email: 0)

    response = await client.post("/api/auth/resend-verification", json={"email": "ready_writer@example.com"})

    assert response.status_code == 200
    send.assert_awaited_once()


@pytest.mark.integration
async def test_resend_delivery_failure_keeps_generic_retryable_code(
    client: AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch
):
    _password_user(db_session, username="flaky_writer", email="flaky_writer@example.com", verified=False)
    monkeypatch.setattr(
        "api.verification.send_verification_code", AsyncMock(return_value=(False, "provider down"))
    )
    monkeypatch.setattr("api.verification.get_remaining_cooldown", lambda _email: 0)

    response = await client.post("/api/auth/resend-verification", json={"email": "flaky_writer@example.com"})

    assert response.status_code == 400
    assert response.json()["error_code"] == ErrorCode.AUTH_RESEND_FAILED
    assert "provider down" not in response.text
