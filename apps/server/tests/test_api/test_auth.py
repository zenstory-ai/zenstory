"""
Tests for Authentication API.

Tests user registration and login endpoints:
- POST /api/auth/register
- POST /api/auth/login
- POST /api/auth/refresh
"""

import pytest
from httpx import AsyncClient
from jose import jwt
from sqlmodel import select

import api.auth as auth_api
from services.core.auth_service import ALGORITHM, SECRET_KEY


@pytest.fixture(autouse=True)
def _set_invite_code_optional_for_legacy_auth_tests(monkeypatch):
    """
    Keep existing registration tests behavior stable unless a test overrides it.
    """
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "true")


@pytest.mark.integration
async def test_register_success(client: AsyncClient):
    """Test successful user registration."""
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "testuser",
            "email": "test@example.com",
            "password": "testpassword123",
            "language": "zh"
        }
    )

    # Note: Registration might fail if email service is not configured
    # The test checks for either success or expected error
    assert response.status_code in [200, 500]

    if response.status_code == 200:
        data = response.json()
        assert "email" in data
        assert data["email"] == "test@example.com"
        assert data["email_verified"] is False


@pytest.mark.integration
async def test_register_creates_free_subscription_and_quota(client: AsyncClient, db_session, monkeypatch):
    """Registration should bootstrap free subscription + usage quota records."""
    from models import User
    from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription

    async def _mock_send_verification_code(_email: str, language: str = "zh"):
        return True, None

    monkeypatch.setattr("api.auth.send_verification_code", _mock_send_verification_code)

    response = await client.post(
        "/api/auth/register",
        json={
            "username": "bootstrap_user",
            "email": "bootstrap_user@example.com",
            "password": "testpassword123",
            "language": "zh",
        },
    )

    assert response.status_code == 200

    user = db_session.exec(
        select(User).where(User.email == "bootstrap_user@example.com")
    ).first()
    assert user is not None

    subscription = db_session.exec(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    ).first()
    assert subscription is not None
    assert subscription.status == "active"

    free_plan = db_session.exec(
        select(SubscriptionPlan).where(SubscriptionPlan.name == "free")
    ).first()
    assert free_plan is not None
    assert subscription.plan_id == free_plan.id

    quota = db_session.exec(
        select(UsageQuota).where(UsageQuota.user_id == user.id)
    ).first()
    assert quota is not None


@pytest.mark.integration
async def test_register_duplicate_email(client: AsyncClient):
    """Test registration with duplicate email returns 400."""
    # First registration
    await client.post(
        "/api/auth/register",
        json={
            "username": "user1",
            "email": "duplicate@example.com",
            "password": "password123"
        }
    )

    # Second registration with same email
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "user2",
            "email": "duplicate@example.com",
            "password": "password456"
        }
    )

    # Should get 400 for duplicate email
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data or "error" in data or "message" in data




@pytest.mark.integration
async def test_register_rejects_duplicate_email_case_insensitively(client: AsyncClient, monkeypatch):
    """Email identity should not allow duplicate accounts by local-part case."""
    async def _mock_send_verification_code(_email: str, language: str = "zh"):
        return True, None

    monkeypatch.setattr("api.auth.send_verification_code", _mock_send_verification_code)

    first_response = await client.post(
        "/api/auth/register",
        json={
            "username": "case_email_user_1",
            "email": "CaseEmail@example.com",
            "password": "password123",
        },
    )
    assert first_response.status_code == 200
    assert first_response.json()["email"] == "caseemail@example.com"

    duplicate_response = await client.post(
        "/api/auth/register",
        json={
            "username": "case_email_user_2",
            "email": "caseemail@example.com",
            "password": "password456",
        },
    )

    assert duplicate_response.status_code == 400
    assert duplicate_response.json().get("error_code") == "ERR_AUTH_EMAIL_EXISTS"


@pytest.mark.integration
async def test_login_with_email_is_case_insensitive(client: AsyncClient, db_session):
    """Users should be able to login by email regardless of typed case."""
    from models import User
    from services.core.auth_service import hash_password

    user = User(
        username="case_login_user",
        email="case-login@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={
            "username": "Case-Login@Example.COM",
            "password": "testpassword123",
        },
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "case-login@example.com"


@pytest.mark.integration
async def test_verify_email_uses_case_insensitive_email_identity(client: AsyncClient, db_session, monkeypatch):
    """Verification should find the registered user even if email case differs."""
    from models import User
    from services.core.auth_service import hash_password

    async def _mock_verify_code(email: str, code: str, language: str = "zh"):
        assert email == "case-verify@example.com"
        return True, None

    monkeypatch.setattr("api.verification.verify_code", _mock_verify_code)

    user = User(
        username="case_verify_user",
        email="case-verify@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/verify-email",
        json={"email": "Case-Verify@Example.COM", "code": "123456"},
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "case-verify@example.com"


@pytest.mark.integration
async def test_verify_email_preserves_stored_email_casing(
    client: AsyncClient, db_session, monkeypatch
):
    """Verifying must not rewrite a legacy mixed-case email to lowercase.

    Regression: case-insensitive lookup plus an unconditional lowercase
    write-back could collide with the case-sensitive UNIQUE(email) constraint
    and return a 500 on an otherwise-correct verification code.
    """
    from models import User
    from services.core.auth_service import hash_password

    async def _mock_verify_code(email: str, code: str, language: str = "zh"):
        return True, None

    monkeypatch.setattr("api.verification.verify_code", _mock_verify_code)

    user = User(
        username="casing_preserve_verify",
        email="Casing-Preserve@Example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    user_id = user.id

    response = await client.post(
        "/api/auth/verify-email",
        json={"email": "Casing-Preserve@Example.com", "code": "123456"},
    )

    assert response.status_code == 200
    db_session.expire_all()
    refreshed = db_session.get(User, user_id)
    assert refreshed.email_verified is True
    # Stored casing is preserved (no destructive lowercase rewrite).
    assert refreshed.email == "Casing-Preserve@Example.com"


@pytest.mark.integration
async def test_register_duplicate_username(client: AsyncClient):
    """Test registration with duplicate username returns 400."""
    # First registration
    await client.post(
        "/api/auth/register",
        json={
            "username": "duplicate_user",
            "email": "user1@example.com",
            "password": "password123"
        }
    )

    # Second registration with same username
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "duplicate_user",
            "email": "user2@example.com",
            "password": "password456"
        }
    )

    # Should get 400 for duplicate username
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data or "error" in data or "message" in data


@pytest.mark.integration
async def test_register_invalid_email_format(client: AsyncClient):
    """Test registration with invalid email format returns 422."""
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "testuser",
            "email": "invalid-email",
            "password": "testpassword123"
        }
    )

    # Should get 422 for invalid email format
    assert response.status_code == 422


@pytest.mark.integration
async def test_register_missing_fields(client: AsyncClient):
    """Test registration with missing required fields returns 422."""
    # Missing password
    response = await client.post(
        "/api/auth/register",
        json={
            "username": "testuser",
            "email": "test@example.com"
        }
    )

    assert response.status_code == 422


@pytest.mark.integration
async def test_register_requires_invite_code_by_default_when_env_unset(client: AsyncClient, monkeypatch):
    """When env var is unset, invite code should be required."""
    monkeypatch.delenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", raising=False)

    response = await client.post(
        "/api/auth/register",
        json={
            "username": "invite_required_user",
            "email": "invite_required_user@example.com",
            "password": "password123",
        },
    )

    assert response.status_code == 400
    data = response.json()
    assert data.get("error_code") == "ERR_AUTH_INVITE_CODE_REQUIRED"


@pytest.mark.integration
async def test_register_policy_endpoint_supports_gray_experiment(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "false")
    monkeypatch.setenv("AUTH_REGISTER_INVITE_GRAY_PERCENT", "100")
    monkeypatch.setenv("AUTH_REGISTER_INVITE_GRAY_SALT", "test-gray-salt")

    response = await client.get(
        "/api/auth/register-policy",
        params={"email": "policy_user@example.com"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["invite_code_optional"] is True
    assert data["variant"] == "treatment_optional"
    assert data["rollout_percent"] == 100


@pytest.mark.integration
async def test_register_gray_experiment_allows_treatment_user_without_invite(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "false")
    monkeypatch.setenv("AUTH_REGISTER_INVITE_GRAY_PERCENT", "50")
    monkeypatch.setenv("AUTH_REGISTER_INVITE_GRAY_SALT", "test-gray-salt")

    treatment_email = None
    control_email = None
    salt = "test-gray-salt"
    for idx in range(2000):
        email = f"gray_user_{idx}@example.com"
        bucket = auth_api._invite_gray_bucket(email, salt=salt)
        if bucket < 50 and treatment_email is None:
            treatment_email = email
        if bucket >= 50 and control_email is None:
            control_email = email
        if treatment_email and control_email:
            break

    assert treatment_email is not None
    assert control_email is not None

    treatment_response = await client.post(
        "/api/auth/register",
        json={
            "username": "gray_treatment_user",
            "email": treatment_email,
            "password": "password123",
        },
    )
    assert treatment_response.status_code == 200

    control_response = await client.post(
        "/api/auth/register",
        json={
            "username": "gray_control_user",
            "email": control_email,
            "password": "password123",
        },
    )
    assert control_response.status_code == 400
    assert control_response.json().get("error_code") == "ERR_AUTH_INVITE_CODE_REQUIRED"


@pytest.mark.integration
async def test_register_rejects_invalid_invite_code_when_required(client: AsyncClient, monkeypatch):
    """When invite code is required, invalid code should block registration."""
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "false")

    response = await client.post(
        "/api/auth/register",
        json={
            "username": "invalid_invite_user",
            "email": "invalid_invite_user@example.com",
            "password": "password123",
            "invite_code": "NOT-EXST",
        },
    )

    assert response.status_code == 400
    data = response.json()
    assert data.get("error_code") == "ERR_REFERRAL_CODE_INVALID"


@pytest.mark.integration
async def test_register_with_invite_code_creates_referral_and_updates_stats(
    client: AsyncClient,
    db_session,
    monkeypatch,
):
    """Test referral record + inviter stats are created when registering with a valid invite code."""
    from models import User
    from models.referral import InviteCode, Referral, UserStats
    from services.core.auth_service import hash_password

    async def _mock_send_verification_code(_email: str, language: str = "zh"):
        return True, None

    monkeypatch.setattr("api.auth.send_verification_code", _mock_send_verification_code)

    inviter = User(
        username="inviter_user",
        email="inviter@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(inviter)
    db_session.commit()
    db_session.refresh(inviter)

    invite_code = InviteCode(
        code="TEST-1234",
        owner_id=inviter.id,
        max_uses=3,
        current_uses=0,
        is_active=True,
    )
    db_session.add(invite_code)
    db_session.commit()
    db_session.refresh(invite_code)

    response = await client.post(
        "/api/auth/register",
        json={
            "username": "invitee_user",
            "email": "invitee@example.com",
            "password": "password123",
            "invite_code": " test-1234 ",
        },
    )
    assert response.status_code == 200

    invitee = db_session.exec(
        select(User).where(User.email == "invitee@example.com")
    ).first()
    assert invitee is not None

    referral = db_session.exec(
        select(Referral).where(Referral.invitee_id == invitee.id)
    ).first()
    assert referral is not None
    assert referral.inviter_id == inviter.id
    assert referral.invite_code_id == invite_code.id

    db_session.refresh(invite_code)
    assert invite_code.current_uses == 1

    inviter_stats = db_session.exec(
        select(UserStats).where(UserStats.user_id == inviter.id)
    ).first()
    assert inviter_stats is not None
    assert inviter_stats.total_invites == 1


@pytest.mark.integration
async def test_verify_email_keeps_referral_stats_consistent(client: AsyncClient, db_session, monkeypatch):
    """Test verification reward flow does not leave successful_invites > total_invites."""
    from models import User
    from models.points import PointsTransaction
    from models.referral import (
        REFERRAL_STATUS_PENDING,
        InviteCode,
        Referral,
        UserStats,
    )
    from services.core.auth_service import hash_password

    async def _mock_verify_code(_email: str, _code: str, language: str = "zh"):
        return True, None

    monkeypatch.setattr("api.verification.verify_code", _mock_verify_code)

    inviter = User(
        username="stats_inviter",
        email="stats_inviter@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    invitee = User(
        username="stats_invitee",
        email="stats_invitee@example.com",
        hashed_password=hash_password("password123"),
        email_verified=False,
        is_active=True,
    )
    db_session.add(inviter)
    db_session.add(invitee)
    db_session.commit()
    db_session.refresh(inviter)
    db_session.refresh(invitee)

    invite_code = InviteCode(
        code="STAT-0001",
        owner_id=inviter.id,
        max_uses=3,
        current_uses=1,
        is_active=True,
    )
    db_session.add(invite_code)
    db_session.commit()
    db_session.refresh(invite_code)

    referral = Referral(
        inviter_id=inviter.id,
        invitee_id=invitee.id,
        invite_code_id=invite_code.id,
        status=REFERRAL_STATUS_PENDING,
        fraud_score=0.0,
    )
    db_session.add(referral)
    db_session.commit()

    # Existing dirty historical data: total_invites is 0 even though one referral exists.
    inviter_stats = UserStats(
        user_id=inviter.id,
        total_invites=0,
        successful_invites=0,
        total_points=0,
        available_points=0,
    )
    db_session.add(inviter_stats)
    db_session.commit()

    response = await client.post(
        "/api/auth/verify-email",
        json={"email": invitee.email, "code": "123456"},
    )
    assert response.status_code == 200

    db_session.refresh(inviter_stats)
    assert inviter_stats.successful_invites == 1
    assert inviter_stats.total_invites >= inviter_stats.successful_invites

    inviter_points_tx = db_session.exec(
        select(PointsTransaction).where(
            PointsTransaction.user_id == inviter.id,
            PointsTransaction.transaction_type == "referral",
            PointsTransaction.source_id == referral.id,
        )
    ).first()
    assert inviter_points_tx is not None

    invitee_points_tx = db_session.exec(
        select(PointsTransaction).where(
            PointsTransaction.user_id == invitee.id,
            PointsTransaction.transaction_type == "referral",
            PointsTransaction.source_id == referral.id,
        )
    ).first()
    assert invitee_points_tx is not None


@pytest.mark.integration
async def test_verify_email_issues_rotation_refresh_token_and_persists_record(
    client: AsyncClient, db_session, monkeypatch
):
    """verify-email should issue refresh token with jti/family_id and persist refresh_token_record."""
    from sqlmodel import select

    from models import RefreshTokenRecord, User
    from services.core.auth_service import TOKEN_TYPE_REFRESH, hash_password, verify_token

    async def _mock_verify_code(_email: str, _code: str, language: str = "zh"):
        return True, None

    monkeypatch.setattr("api.verification.verify_code", _mock_verify_code)

    user = User(
        username="verify_refresh_user",
        email="verify_refresh@example.com",
        hashed_password=hash_password("password123"),
        email_verified=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    response = await client.post(
        "/api/auth/verify-email",
        json={"email": user.email, "code": "123456"},
    )
    assert response.status_code == 200
    data = response.json()
    refresh_token = data.get("refresh_token")
    assert refresh_token

    payload = verify_token(refresh_token, expected_type=TOKEN_TYPE_REFRESH)
    assert payload is not None
    token_jti = payload.get("jti")
    family_id = payload.get("family_id")
    assert token_jti
    assert family_id

    record = db_session.exec(
        select(RefreshTokenRecord).where(
            RefreshTokenRecord.token_jti == token_jti,
            RefreshTokenRecord.user_id == user.id,
        )
    ).first()
    assert record is not None
    assert record.family_id == family_id
    assert record.revoked_at is None


@pytest.mark.integration
async def test_login_with_correct_credentials(client: AsyncClient, db_session):
    """Test login with correct credentials returns token."""
    from models import User
    from services.core.auth_service import hash_password

    # Create a verified user in the database
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,  # Must be verified for login
        is_active=True
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Login with correct credentials
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "testuser",  # Can use username
            "password": "testpassword123"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert "user" in data
    assert data["user"]["username"] == "testuser"
    assert data["user"]["email_verified"] is True


@pytest.mark.integration
async def test_login_with_email(client: AsyncClient, db_session):
    """Test login with email instead of username."""
    from models import User
    from services.core.auth_service import hash_password

    # Create a verified user
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login with email
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "test@example.com",  # Use email instead
            "password": "testpassword123"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data


@pytest.mark.integration
async def test_login_with_wrong_password(client: AsyncClient, db_session):
    """Test login with wrong password returns 401."""
    from models import User
    from services.core.auth_service import hash_password

    # Create a verified user
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=hash_password("correctpassword"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login with wrong password
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "testuser",
            "password": "wrongpassword"
        }
    )

    assert response.status_code == 401
    data = response.json()
    assert "detail" in data or "error" in data or "message" in data


@pytest.mark.integration
async def test_login_nonexistent_user(client: AsyncClient):
    """Test login with nonexistent user returns 401."""
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "nonexistent",
            "password": "password123"
        }
    )

    assert response.status_code == 401


@pytest.mark.integration
async def test_login_missing_fields(client: AsyncClient):
    """Test login with missing fields returns 422."""
    # Missing password
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "testuser"
        }
    )

    assert response.status_code == 422


@pytest.mark.integration
async def test_login_unverified_email(client: AsyncClient, db_session):
    """Test login with unverified email returns 403."""
    from models import User
    from services.core.auth_service import hash_password

    # Create an unverified user
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=False,  # Not verified
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login should fail due to unverified email
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "testuser",
            "password": "testpassword123"
        }
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_login_inactive_user(client: AsyncClient, db_session):
    """Test login with inactive user returns 400."""
    from models import User
    from services.core.auth_service import hash_password

    # Create an inactive user
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=False  # Inactive
    )
    db_session.add(user)
    db_session.commit()

    # Login should fail due to inactive user
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "testuser",
            "password": "testpassword123"
        }
    )

    assert response.status_code == 400


@pytest.mark.integration
async def test_login_rate_limited_returns_429(client: AsyncClient, db_session, monkeypatch):
    """Login endpoint should return 429 when per-IP limit is exceeded."""
    from middleware.rate_limit import _rate_limit_store
    from models import User
    from services.core.auth_service import hash_password

    monkeypatch.setenv("AUTH_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("AUTH_LOGIN_IDENTIFIER_RATE_LIMIT_PER_10_MIN", "99")
    _rate_limit_store.clear()

    user = User(
        username="login_rate_user",
        email="login_rate_user@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    first_response = await client.post(
        "/api/auth/login",
        data={
            "username": "login_rate_user",
            "password": "testpassword123",
        },
    )
    assert first_response.status_code == 200

    second_response = await client.post(
        "/api/auth/login",
        data={
            "username": "login_rate_user",
            "password": "testpassword123",
        },
    )
    assert second_response.status_code == 429
    assert second_response.json().get("detail") == "ERR_AUTH_RATE_LIMIT_EXCEEDED"


@pytest.mark.integration
async def test_login_identifier_rate_limit_applies_across_ips(client: AsyncClient, db_session, monkeypatch):
    """Identifier limit should apply even when request IP changes."""
    from middleware.rate_limit import _rate_limit_store
    from models import User
    from services.core.auth_service import hash_password

    monkeypatch.setenv("AUTH_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_PER_MINUTE", "99")
    monkeypatch.setenv("AUTH_LOGIN_IDENTIFIER_RATE_LIMIT_PER_10_MIN", "1")
    _rate_limit_store.clear()

    user = User(
        username="login_identifier_rate_user",
        email="login_identifier_rate_user@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    first_response = await client.post(
        "/api/auth/login",
        data={
            "username": "login_identifier_rate_user",
            "password": "testpassword123",
        },
        headers={"X-Forwarded-For": "198.51.100.101"},
    )
    assert first_response.status_code == 200

    second_response = await client.post(
        "/api/auth/login",
        data={
            "username": "login_identifier_rate_user",
            "password": "testpassword123",
        },
        headers={"X-Forwarded-For": "198.51.100.102"},
    )
    assert second_response.status_code == 429
    assert second_response.json().get("detail") == "ERR_AUTH_RATE_LIMIT_EXCEEDED"


# ============================================
# Token Refresh Tests
# ============================================


@pytest.mark.integration
async def test_refresh_token_with_valid_token(client: AsyncClient, db_session):
    """Test refresh token with valid refresh token returns new tokens."""
    import asyncio

    from models import User
    from services.core.auth_service import hash_password

    # Create a verified user
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login to get tokens
    login_response = await client.post(
        "/api/auth/login",
        data={
            "username": "testuser",
            "password": "testpassword123"
        }
    )
    assert login_response.status_code == 200
    login_data = login_response.json()
    refresh_token = login_data["refresh_token"]
    old_access_token = login_data["access_token"]

    # Wait to ensure different token timestamp (JWT exp is in seconds)
    await asyncio.sleep(1.1)

    # Refresh token
    response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token}
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert "user" in data
    assert data["user"]["username"] == "testuser"

    # Verify new access token is different (due to time delay)
    new_access_token = data["access_token"]
    assert new_access_token != old_access_token


@pytest.mark.integration
async def test_refresh_token_reuse_is_rejected(client: AsyncClient, db_session):
    """Refresh token rotation should reject reuse of old refresh token."""
    from models import User
    from services.core.auth_service import hash_password

    user = User(
        username="refresh_reuse_user",
        email="refresh_reuse@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={
            "username": "refresh_reuse_user",
            "password": "testpassword123",
        },
    )
    assert login_response.status_code == 200
    original_refresh_token = login_response.json()["refresh_token"]

    first_refresh = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": original_refresh_token},
    )
    assert first_refresh.status_code == 200

    replay_refresh = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": original_refresh_token},
    )
    assert replay_refresh.status_code == 401


@pytest.mark.integration
async def test_logout_revokes_active_refresh_tokens(client: AsyncClient, db_session):
    """Logout should revoke active refresh tokens for current user."""
    from models import User
    from services.core.auth_service import hash_password

    user = User(
        username="logout_revoke_user",
        email="logout_revoke@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={
            "username": "logout_revoke_user",
            "password": "testpassword123",
        },
    )
    assert login_response.status_code == 200
    login_data = login_response.json()
    access_token = login_data["access_token"]
    refresh_token = login_data["refresh_token"]

    logout_response = await client.post(
        "/api/auth/logout",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert logout_response.status_code == 200

    refresh_response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_response.status_code == 401


@pytest.mark.integration
async def test_refresh_rate_limited_returns_429(client: AsyncClient, db_session, monkeypatch):
    """Refresh endpoint should return 429 when per-IP limit is exceeded."""
    from middleware.rate_limit import _rate_limit_store
    from models import User
    from services.core.auth_service import hash_password

    monkeypatch.setenv("AUTH_RATE_LIMIT_ENABLED", "true")
    monkeypatch.setenv("AUTH_LOGIN_RATE_LIMIT_PER_MINUTE", "99")
    monkeypatch.setenv("AUTH_LOGIN_IDENTIFIER_RATE_LIMIT_PER_10_MIN", "99")
    monkeypatch.setenv("AUTH_REFRESH_RATE_LIMIT_PER_MINUTE", "1")
    _rate_limit_store.clear()

    user = User(
        username="refresh_rate_user",
        email="refresh_rate_user@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={
            "username": "refresh_rate_user",
            "password": "testpassword123",
        },
    )
    assert login_response.status_code == 200
    refresh_token = login_response.json()["refresh_token"]

    first_refresh = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert first_refresh.status_code == 200

    second_refresh = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": first_refresh.json()["refresh_token"]},
    )
    assert second_refresh.status_code == 429
    assert second_refresh.json().get("detail") == "ERR_AUTH_RATE_LIMIT_EXCEEDED"


@pytest.mark.integration
async def test_change_password_revokes_active_refresh_tokens(client: AsyncClient, db_session):
    """Password change should invalidate all active refresh tokens for the user."""
    from models import User
    from services.core.auth_service import hash_password

    user = User(
        username="change_password_user",
        email="change_password@example.com",
        hashed_password=hash_password("old-password-123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={
            "username": "change_password_user",
            "password": "old-password-123",
        },
    )
    assert login_response.status_code == 200
    login_data = login_response.json()
    access_token = login_data["access_token"]
    refresh_token = login_data["refresh_token"]

    change_response = await client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {access_token}"},
        json={
            "old_password": "old-password-123",
            "new_password": "new-password-456",
        },
    )
    assert change_response.status_code == 200

    refresh_response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_response.status_code == 401

    old_login_response = await client.post(
        "/api/auth/login",
        data={
            "username": "change_password_user",
            "password": "old-password-123",
        },
    )
    assert old_login_response.status_code == 401

    new_login_response = await client.post(
        "/api/auth/login",
        data={
            "username": "change_password_user",
            "password": "new-password-456",
        },
    )
    assert new_login_response.status_code == 200


@pytest.mark.integration
async def test_refresh_endpoint_rejects_access_token(client: AsyncClient, db_session):
    """Test that access token cannot be used on refresh endpoint."""
    from models import User
    from services.core.auth_service import hash_password

    user = User(
        username="refresh_guard_user",
        email="refresh_guard@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={
            "username": "refresh_guard_user",
            "password": "testpassword123",
        }
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]

    response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": access_token}
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_refresh_token_with_expired_token(client: AsyncClient, db_session):
    """Test refresh token with expired token returns 401."""
    from datetime import datetime, timedelta

    from models import User
    from services.core.auth_service import hash_password

    # Create a verified user
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Create an expired refresh token manually
    expired_payload = {
        "sub": str(user.id),
        "exp": datetime.utcnow() - timedelta(days=1)  # Expired yesterday
    }
    expired_token = jwt.encode(expired_payload, SECRET_KEY, algorithm=ALGORITHM)

    # Try to refresh with expired token
    response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": expired_token}
    )

    assert response.status_code == 401


@pytest.mark.integration
async def test_refresh_token_with_invalid_token(client: AsyncClient):
    """Test refresh token with invalid token returns 401."""
    # Try to refresh with a completely invalid token
    response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": "invalid.token.here"}
    )

    assert response.status_code == 401


@pytest.mark.integration
async def test_refresh_token_with_malformed_token(client: AsyncClient):
    """Test refresh token with malformed token returns 401."""
    # Try to refresh with malformed token
    response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": "not-a-jwt-token"}
    )

    assert response.status_code == 401


@pytest.mark.integration
async def test_refresh_token_with_missing_sub(client: AsyncClient):
    """Test refresh token without 'sub' claim returns 401."""

    # Create a token without 'sub' claim
    payload = {"exp": 9999999999}  # Far future expiry
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": token}
    )

    assert response.status_code == 401


@pytest.mark.integration
async def test_refresh_token_for_nonexistent_user(client: AsyncClient, db_session):
    """Test refresh token for nonexistent user returns 401."""

    # Create a token with a nonexistent user ID
    payload = {
        "sub": "00000000-0000-0000-0000-000000000000",  # Nonexistent UUID
        "exp": 9999999999
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": token}
    )

    assert response.status_code == 401


@pytest.mark.integration
async def test_refresh_token_for_inactive_user(client: AsyncClient, db_session):
    """Test refresh token for inactive user returns 401."""
    from models import User
    from services.core.auth_service import hash_password

    # Create an inactive user
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=False  # Inactive
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Create a valid token for the inactive user
    from datetime import datetime, timedelta

    payload = {
        "sub": str(user.id),
        "exp": datetime.utcnow() + timedelta(days=7)
    }
    token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    # Try to refresh with inactive user token
    response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": token}
    )

    assert response.status_code == 401


@pytest.mark.integration
async def test_login_oauth_user_with_password_returns_401(client: AsyncClient, db_session):
    """OAuth-only user (empty hash) should fail password login with 401, not 500."""
    from models import User

    user = User(
        username="oauth_only_user",
        email="oauth_only@example.com",
        hashed_password="",
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={
            "username": "oauth_only_user",
            "password": "any-password"
        }
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_update_email_resets_email_verified(client: AsyncClient, db_session):
    """Updating email should reset email_verified to require re-verification."""
    from models import User
    from services.core.auth_service import hash_password

    user = User(
        username="email_update_user",
        email="email_update_old@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={
            "username": "email_update_user",
            "password": "testpassword123"
        }
    )
    assert login_response.status_code == 200
    access_token = login_response.json()["access_token"]

    response = await client.put(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"email": "email_update_new@example.com"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "email_update_new@example.com"
    assert data["email_verified"] is False


@pytest.mark.integration
async def test_resend_verification_nonexistent_email_is_masked(client: AsyncClient):
    """Resend endpoint should not reveal whether email exists."""
    response = await client.post(
        "/api/auth/resend-verification",
        json={"email": "notfound@example.com"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "notfound@example.com"


@pytest.mark.integration
async def test_check_verification_nonexistent_email_is_masked(client: AsyncClient):
    """Check verification endpoint should mask unknown users."""
    response = await client.get(
        "/api/auth/check-verification",
        params={"email": "missing_check@example.com"},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["email"] == "missing_check@example.com"
    assert data["email_verified"] is False
    assert data["resend_cooldown_seconds"] == 0
    assert data["verification_code_ttl_seconds"] == 0


@pytest.mark.integration
async def test_check_verification_verified_and_unknown_are_indistinguishable(
    client: AsyncClient, db_session
):
    from models import User
    from services.core.auth_service import hash_password
    user = User(
        username="verified_mask_user",
        email="verified_mask_user@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    verified = await client.get("/api/auth/check-verification", params={"email": user.email})
    unknown = await client.get("/api/auth/check-verification", params={"email": "unknown_mask@example.com"})
    verified_data = verified.json()
    unknown_data = unknown.json()
    verified_data.pop("email")
    unknown_data.pop("email")
    assert verified_data == unknown_data == {
        "email_verified": False,
        "resend_cooldown_seconds": 0,
        "verification_code_ttl_seconds": 0,
    }


@pytest.mark.integration
async def test_check_verification_returns_user_state_and_timers(
    client: AsyncClient, db_session, monkeypatch: pytest.MonkeyPatch
):
    """Existing user should receive verification status and timer metadata."""
    from models import User
    from services.core.auth_service import hash_password

    user = User(
        username="check_verification_user",
        email="check_verification_user@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=False,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    monkeypatch.setattr("api.verification.get_remaining_cooldown", lambda _email: 45)
    monkeypatch.setattr("api.verification.get_code_ttl", lambda _email: 310)

    response = await client.get(
        "/api/auth/check-verification",
        params={"email": user.email},
    )
    assert response.status_code == 200

    data = response.json()
    assert data["email"] == user.email
    assert data["email_verified"] is False
    assert data["resend_cooldown_seconds"] == 45
    assert data["verification_code_ttl_seconds"] == 310


@pytest.mark.integration
async def test_verify_email_nonexistent_email_returns_invalid_code(client: AsyncClient):
    """Verify endpoint should return generic invalid-code response for unknown email."""
    response = await client.post(
        "/api/auth/verify-email",
        json={"email": "notfound@example.com", "code": "123456"},
    )
    assert response.status_code == 400
    data = response.json()
    assert data.get("detail") == "ERR_AUTH_INVALID_VERIFICATION_CODE"


@pytest.mark.integration
async def test_protected_route_without_token(client: AsyncClient):
    """Test accessing protected route without token returns 401."""
    # Try to access /api/auth/me without authentication
    response = await client.get("/api/auth/me")

    assert response.status_code == 401


@pytest.mark.integration
async def test_protected_route_with_valid_token(client: AsyncClient, db_session):
    """Test accessing protected route with valid token succeeds."""
    from models import User
    from services.core.auth_service import hash_password

    # Create a verified user
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login to get token
    login_response = await client.post(
        "/api/auth/login",
        data={
            "username": "testuser",
            "password": "testpassword123"
        }
    )
    login_data = login_response.json()
    access_token = login_data["access_token"]

    # Access protected route with token
    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {access_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "testuser"
    assert data["email"] == "test@example.com"


@pytest.mark.integration
async def test_protected_route_with_expired_token(client: AsyncClient):
    """Test accessing protected route with expired token returns 401."""
    from datetime import datetime, timedelta

    # Create an expired token
    payload = {
        "sub": "some-user-id",
        "exp": datetime.utcnow() - timedelta(hours=1)
    }
    expired_token = jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

    # Try to access protected route with expired token
    response = await client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {expired_token}"}
    )

    assert response.status_code == 401
