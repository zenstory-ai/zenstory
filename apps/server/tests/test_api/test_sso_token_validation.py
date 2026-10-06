"""SSO validation must respect current account state, not JWT issuance state."""

import pytest
from services.auth import create_access_token

from core.error_codes import ErrorCode
from models import User


@pytest.mark.parametrize("email_verified", [True, False])
@pytest.mark.parametrize("active", [True, False])
async def test_sso_token_validation_respects_current_account_state(
    client, db_session, active, email_verified,
):
    user = User(
        username=f"sso-state-{active}-{email_verified}",
        email=f"sso-state-{active}-{email_verified}@example.test",
        hashed_password="unused-local-fixture", is_active=True,
        email_verified=email_verified,
    )
    db_session.add(user)
    db_session.commit()
    token = create_access_token({"sub": user.id})
    user.is_active = active
    db_session.add(user)
    db_session.commit()
    response = await client.get("/api/auth/validate-token", params={"token": token})
    if active:
        assert response.status_code == 200
        assert response.json() == {"id": user.id, "username": user.username, "email": user.email}
    else:
        assert response.status_code == 400
        assert response.json()["error_code"] == ErrorCode.AUTH_INACTIVE_USER
        assert user.email not in response.text
        assert user.username not in response.text
    db_session.refresh(user)
    assert user.is_active is active
    assert user.email_verified is email_verified


@pytest.mark.parametrize("kind", ["invalid", "missing-sub", "missing-user"])
async def test_sso_token_validation_preserves_invalid_token_rejection(client, kind):
    token = {
        "invalid": "not-a-valid-jwt",
        "missing-sub": create_access_token({}),
        "missing-user": create_access_token({"sub": "absent-local-user"}),
    }[kind]
    response = await client.get("/api/auth/validate-token", params={"token": token})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error_code"] == ErrorCode.AUTH_TOKEN_INVALID
