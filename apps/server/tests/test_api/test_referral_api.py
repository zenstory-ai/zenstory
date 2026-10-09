"""
Tests for Referral API endpoints.

Integration tests for the referral system API, covering:
- GET /api/v1/referral/codes - Get user's invite codes
- POST /api/v1/referral/codes - Create new invite code
- POST /api/v1/referral/codes/{code}/validate - Validate invite code
- GET /api/v1/referral/stats - Get referral statistics
- GET /api/v1/referral/rewards - Get user rewards
- DELETE /api/v1/referral/codes/{code_id} - Deactivate invite code
"""
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import User
from models.points import PointsTransaction
from models.referral import (
    InviteCode,
    Referral,
    UserReward,
    UserStats,
    REFERRAL_STATUS_PENDING,
    REFERRAL_STATUS_REWARDED,
    REWARD_TYPE_POINTS,
    REWARD_TYPE_PRO_TRIAL,
)
from services.core.auth_service import hash_password
from services.features.referral_service import MAX_INVITE_CODES_PER_USER


@pytest.fixture
async def auth_headers(client: AsyncClient, db_session: Session):
    """Create a verified user and return auth headers."""
    user = User(
        email="test@example.com",
        username="testuser",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Login to get token
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "testuser",
            "password": "testpassword123",
        }
    )

    assert response.status_code == 200
    data = response.json()
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.fixture
async def auth_headers_superuser(client: AsyncClient, db_session: Session):
    """Create a verified superuser and return auth headers."""
    user = User(
        email="superuser@example.com",
        username="superuser",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
        is_superuser=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Login to get token
    response = await client.post(
        "/api/auth/login",
        data={
            "username": "superuser",
            "password": "testpassword123",
        }
    )

    assert response.status_code == 200
    data = response.json()
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.fixture
async def auth_headers_user2(client: AsyncClient, db_session: Session):
    """Create a second verified user and return auth headers."""
    user = User(
        email="test2@example.com",
        username="testuser2",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    response = await client.post(
        "/api/auth/login",
        data={
            "username": "testuser2",
            "password": "testpassword123",
        }
    )

    assert response.status_code == 200
    data = response.json()
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.mark.integration
class TestGetInviteCodes:
    """Tests for GET /api/v1/referral/codes endpoint."""

    async def test_get_invite_codes_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.get("/api/v1/referral/codes")
        assert response.status_code == 401

    async def test_get_invite_codes_empty(self, client: AsyncClient, auth_headers):
        """Test getting codes when user has none."""
        response = await client.get(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data == []

    async def test_get_invite_codes_success(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test getting all invite codes for a user."""
        from models.referral import InviteCode
        from sqlalchemy import text as sql_text

        # Get user from DB to get the ID
        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create some invite codes directly in DB
        code1 = InviteCode(
            code="AAAA-1111",
            owner_id=user_id,
            max_uses=3,
            current_uses=0,
            is_active=True,
        )
        code2 = InviteCode(
            code="BBBB-2222",
            owner_id=user_id,
            max_uses=3,
            current_uses=1,
            is_active=True,
        )
        db_session.add(code1)
        db_session.add(code2)
        db_session.commit()

        response = await client.get(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

        # Check response format
        for code in data:
            assert "id" in code
            assert "code" in code
            assert "max_uses" in code
            assert "current_uses" in code
            assert "is_active" in code
            assert "created_at" in code


@pytest.mark.integration
class TestCreateInviteCode:
    """Tests for POST /api/v1/referral/codes endpoint."""

    async def test_create_invite_code_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.post("/api/v1/referral/codes")
        assert response.status_code == 401

    async def test_create_invite_code_success(self, client: AsyncClient, auth_headers):
        """Test successful creation of an invite code."""
        response = await client.post(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )

        assert response.status_code == 201
        data = response.json()

        assert "id" in data
        assert "code" in data
        assert "max_uses" in data
        assert "current_uses" in data
        assert "is_active" in data
        assert data["is_active"] is True
        assert data["current_uses"] == 0

    async def test_create_invite_code_max_limit(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test that users cannot create more than max allowed codes."""
        from sqlalchemy import text as sql_text

        # Get user from DB to get the ID
        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create max codes (max limit)
        for i in range(MAX_INVITE_CODES_PER_USER):
            code = InviteCode(
                code=f"CODE-{i:04d}",
                owner_id=user_id,
                max_uses=3,
                current_uses=0,
                is_active=True,
            )
            db_session.add(code)
        db_session.commit()

        # Try to create one more code
        response = await client.post(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )

        assert response.status_code == 400
        data = response.json()
        assert "ERR_REFERRAL_MAX_CODES_REACHED" in data.get("detail", "") or "MAX_CODES" in data.get("error_code", "")

    async def test_create_invite_code_unlimited_for_superuser(self, client: AsyncClient, auth_headers_superuser):
        """Test superuser can create more than MAX_INVITE_CODES_PER_USER codes via user endpoint."""
        create_count = MAX_INVITE_CODES_PER_USER + 2

        for _ in range(create_count):
            response = await client.post(
                "/api/v1/referral/codes",
                headers=auth_headers_superuser,
            )
            assert response.status_code == 201

        list_response = await client.get(
            "/api/v1/referral/codes",
            headers=auth_headers_superuser,
        )
        assert list_response.status_code == 200
        codes = list_response.json()
        assert len(codes) == create_count


@pytest.mark.integration
class TestValidateInviteCode:
    """Tests for POST /api/v1/referral/codes/{code}/validate endpoint."""

    async def test_validate_invite_code_public(self, client: AsyncClient, db_session: Session):
        """Test that validation endpoint is public (no auth required)."""
        from models.referral import InviteCode
        from sqlalchemy import text as sql_text

        # Create a user and code directly
        user_result = db_session.exec(
            sql_text("SELECT id FROM user LIMIT 1")
        ).first()

        if user_result:
            user_id = user_result[0]
            code = InviteCode(
                code="TEST-1234",
                owner_id=user_id,
                max_uses=3,
                current_uses=0,
                is_active=True,
            )
            db_session.add(code)
            db_session.commit()

            # Validate without auth
            response = await client.post(
                "/api/v1/referral/codes/TEST-1234/validate"
            )

            assert response.status_code == 200
            data = response.json()
            assert "valid" in data
            assert "message" in data

    async def test_validate_invite_code_valid(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test validation of a valid invite code."""
        from sqlalchemy import text as sql_text

        # Create a code first
        create_response = await client.post(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )
        assert create_response.status_code == 201
        code_data = create_response.json()
        code = code_data["code"]

        # Validate it
        response = await client.post(
            f"/api/v1/referral/codes/{code}/validate"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True
        assert data["message"] == "Invite code is valid"

    async def test_validate_invite_code_invalid(self, client: AsyncClient):
        """Test validation of an invalid invite code."""
        response = await client.post(
            "/api/v1/referral/codes/INVALID-CODE/validate"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert data["message"] == "Invite code is invalid or unavailable"

    async def test_validate_invite_code_expired(self, client: AsyncClient, db_session: Session):
        """Test validation of an expired invite code."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user LIMIT 1")
        ).first()

        if user_result:
            user_id = user_result[0]
            code = InviteCode(
                code="OLDC-0D01",
                owner_id=user_id,
                max_uses=3,
                current_uses=0,
                is_active=True,
                expires_at=datetime.utcnow() - timedelta(days=1),
            )
            db_session.add(code)
            db_session.commit()

            response = await client.post(
                "/api/v1/referral/codes/OLDC-0D01/validate"
            )

            assert response.status_code == 200
            data = response.json()
            assert data["valid"] is False
            assert data["message"] == "Invite code is invalid or unavailable"

    async def test_validate_invite_code_exhausted(self, client: AsyncClient, db_session: Session):
        """Test validation of an exhausted invite code."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user LIMIT 1")
        ).first()

        if user_result:
            user_id = user_result[0]
            code = InviteCode(
                code="FULL-CODE",
                owner_id=user_id,
                max_uses=3,
                current_uses=3,
                is_active=True,
            )
            db_session.add(code)
            db_session.commit()

            response = await client.post(
                "/api/v1/referral/codes/FULL-CODE/validate"
            )

            assert response.status_code == 200
            data = response.json()
            assert data["valid"] is False
            assert data["message"] == "Invite code is invalid or unavailable"

    async def test_validate_invite_code_rate_limited(self, client: AsyncClient):
        """Test public validation endpoint is protected by rate limiting."""
        from middleware.rate_limit import _rate_limit_store

        _rate_limit_store.clear()
        try:
            for _ in range(30):
                response = await client.post("/api/v1/referral/codes/INVALID-CODE/validate")
                assert response.status_code == 200

            response = await client.post("/api/v1/referral/codes/INVALID-CODE/validate")
            assert response.status_code == 429
            assert response.json()["error_code"] == "ERR_AUTH_RATE_LIMIT_EXCEEDED"
        finally:
            _rate_limit_store.clear()


@pytest.mark.integration
class TestGetReferralStats:
    """Tests for GET /api/v1/referral/stats endpoint."""

    async def test_get_referral_stats_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.get("/api/v1/referral/stats")
        assert response.status_code == 401

    async def test_get_referral_stats_success(self, client: AsyncClient, auth_headers):
        """Test getting referral statistics."""
        response = await client.get(
            "/api/v1/referral/stats",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        assert "total_invites" in data
        assert "successful_invites" in data
        assert "total_points" in data
        assert "available_points" in data

    async def test_get_referral_stats_with_data(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test getting stats when user has referral data."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create user stats
        stats = UserStats(
            user_id=user_id,
            total_invites=5,
            successful_invites=3,
            total_points=300,
            available_points=200,
        )
        db_session.add(stats)
        db_session.add(
            PointsTransaction(
                user_id=user_id,
                amount=200,
                balance_after=200,
                transaction_type="referral",
            )
        )
        db_session.commit()

        response = await client.get(
            "/api/v1/referral/stats",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_invites"] == 5
        assert data["successful_invites"] == 3
        assert data["total_points"] == 300
        assert data["available_points"] == 200


@pytest.mark.integration
class TestGetUserRewards:
    """Tests for GET /api/v1/referral/rewards endpoint."""

    async def test_get_user_rewards_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.get("/api/v1/referral/rewards")
        assert response.status_code == 401

    async def test_get_user_rewards_empty(self, client: AsyncClient, auth_headers):
        """Test getting rewards when user has none."""
        response = await client.get(
            "/api/v1/referral/rewards",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data == []

    async def test_get_user_rewards_with_data(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test getting rewards when user has some."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create some rewards
        reward1 = UserReward(
            user_id=user_id,
            reward_type=REWARD_TYPE_POINTS,
            amount=100,
            source="referral",
            is_used=False,
        )
        reward2 = UserReward(
            user_id=user_id,
            reward_type=REWARD_TYPE_PRO_TRIAL,
            amount=14,
            source="referral",
            is_used=False,
        )
        db_session.add(reward1)
        db_session.add(reward2)
        db_session.commit()

        response = await client.get(
            "/api/v1/referral/rewards",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

        # Check response format
        for reward in data:
            assert "id" in reward
            assert "reward_type" in reward
            assert "amount" in reward
            assert "source" in reward
            assert "is_used" in reward


@pytest.mark.integration
class TestDeactivateInviteCode:
    """Tests for DELETE /api/v1/referral/codes/{code_id} endpoint."""

    async def test_deactivate_invite_code_unauthorized(self, client: AsyncClient):
        """Test that unauthenticated requests are rejected."""
        response = await client.delete("/api/v1/referral/codes/some-id")
        assert response.status_code == 401

    async def test_deactivate_invite_code_not_found(self, client: AsyncClient, auth_headers):
        """Test deactivating a non-existent code."""
        response = await client.delete(
            "/api/v1/referral/codes/non-existent-id",
            headers=auth_headers,
        )

        assert response.status_code == 404

    async def test_deactivate_invite_code_not_owner(self, client: AsyncClient, auth_headers, auth_headers_user2, db_session: Session):
        """Test that users can only deactivate their own codes."""
        from sqlalchemy import text as sql_text

        # Get user2's ID
        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'test2@example.com'")
        ).first()
        user2_id = user_result[0] if user_result else None

        # Create code owned by user2
        code = InviteCode(
            code="USER-2CODE",
            owner_id=user2_id,
            max_uses=3,
            current_uses=0,
            is_active=True,
        )
        db_session.add(code)
        db_session.commit()
        db_session.refresh(code)

        # Try to deactivate with user1
        response = await client.delete(
            f"/api/v1/referral/codes/{code.id}",
            headers=auth_headers,
        )

        assert response.status_code == 403

    async def test_deactivate_invite_code_success(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test successful deactivation of an invite code."""
        # Create a code first
        create_response = await client.post(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )
        assert create_response.status_code == 201
        code_data = create_response.json()
        code_id = code_data["id"]

        # Deactivate it
        response = await client.delete(
            f"/api/v1/referral/codes/{code_id}",
            headers=auth_headers,
        )

        assert response.status_code == 204

        # Verify it's deactivated
        code = db_session.get(InviteCode, code_id)
        assert code.is_active is False
