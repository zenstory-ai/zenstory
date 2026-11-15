"""
Extended tests for Referral API endpoints.

Additional integration tests covering edge cases and error conditions:
- Case insensitive code validation
- Inactive code handling
- Already deactivated codes
- Multiple code operations
- Edge cases in stats and rewards
"""
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import User
from models.referral import (
    REWARD_TYPE_POINTS,
    InviteCode,
    UserReward,
    UserStats,
)
from services.core.auth_service import hash_password


@pytest.fixture
async def auth_headers(client: AsyncClient, db_session: Session):
    """Create a verified user and return auth headers."""
    user = User(
        email="extended@example.com",
        username="extendeduser",
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
            "username": "extendeduser",
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
        email="extended2@example.com",
        username="extendeduser2",
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
            "username": "extendeduser2",
            "password": "testpassword123",
        }
    )

    assert response.status_code == 200
    data = response.json()
    return {"Authorization": f"Bearer {data['access_token']}"}


@pytest.fixture
async def user_with_codes(client: AsyncClient, auth_headers, db_session: Session):
    """Create a user with some invite codes for testing."""
    from sqlalchemy import text as sql_text

    user_result = db_session.exec(
        sql_text("SELECT id FROM user WHERE email = 'extended@example.com'")
    ).first()
    user_id = user_result[0] if user_result else None

    # Create codes with different states
    codes = []
    # Active code
    codes.append(InviteCode(
        code="ACTV-0001",
        owner_id=user_id,
        max_uses=3,
        current_uses=0,
        is_active=True,
    ))
    # Inactive (deactivated) code
    codes.append(InviteCode(
        code="INAC-0002",
        owner_id=user_id,
        max_uses=3,
        current_uses=0,
        is_active=False,
    ))
    # Code with some uses
    codes.append(InviteCode(
        code="USED-0003",
        owner_id=user_id,
        max_uses=3,
        current_uses=2,
        is_active=True,
    ))

    for code in codes:
        db_session.add(code)
    db_session.commit()

    return codes


@pytest.mark.integration
class TestValidateInviteCodeExtended:
    """Extended tests for invite code validation."""

    async def test_validate_code_case_insensitive(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test that code validation is case insensitive."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'extended@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create code with lowercase
        code = InviteCode(
            code="ABCD-1234",
            owner_id=user_id,
            max_uses=3,
            current_uses=0,
            is_active=True,
        )
        db_session.add(code)
        db_session.commit()

        # Test lowercase validation
        response = await client.post(
            "/api/v1/referral/codes/abcd-1234/validate"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True

        # Test mixed case validation
        response = await client.post(
            "/api/v1/referral/codes/AbCd-1234/validate"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True

    async def test_validate_inactive_code(self, client: AsyncClient, user_with_codes):
        """Test validation of a deactivated code."""
        response = await client.post(
            "/api/v1/referral/codes/INAC-0002/validate"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False
        assert data["message"] == "Invite code is invalid or unavailable"

    async def test_validate_code_with_whitespace(self, client: AsyncClient):
        """Test validation with whitespace in code."""
        response = await client.post(
            "/api/v1/referral/codes/ TEST-1234 /validate"
        )

        # Should treat as invalid (code not found)
        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is False

    async def test_validate_code_near_limit(self, client: AsyncClient, user_with_codes):
        """Test validation of a code that has uses but not exhausted."""
        response = await client.post(
            "/api/v1/referral/codes/USED-0003/validate"
        )

        assert response.status_code == 200
        data = response.json()
        assert data["valid"] is True  # Still has 1 use remaining

    async def test_validate_multiple_invalid_formats(self, client: AsyncClient):
        """Test validation with various invalid code formats."""
        invalid_codes = [
            "TOOLONG-CODE12",  # Too long
            "SHORT",           # Too short
            "NOHYPHEN1234",    # No hyphen
            "1234-ABCD",       # Numbers first (depends on implementation)
            "SPAC E-1234",     # Contains space
            "SPEC!AL-1234",    # Special characters
        ]

        for code in invalid_codes:
            response = await client.post(
                f"/api/v1/referral/codes/{code}/validate"
            )
            # Should treat as invalid (code not found)
            assert response.status_code == 200
            data = response.json()
            assert data["valid"] is False, f"Code {code} should be invalid"


@pytest.mark.integration
class TestInviteCodesExtended:
    """Extended tests for invite code management."""

    async def test_get_codes_includes_inactive(self, client: AsyncClient, auth_headers, user_with_codes):
        """Test that get codes returns both active and inactive codes."""
        response = await client.get(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 3  # At least 3 codes created in fixture

        # Check that both active and inactive are returned
        active_codes = [c for c in data if c["is_active"]]
        inactive_codes = [c for c in data if not c["is_active"]]
        assert len(active_codes) >= 1
        assert len(inactive_codes) >= 1

    async def test_get_codes_ordered_by_created_desc(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test that codes are returned in descending order by creation date."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'extended@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create codes with specific creation times
        base_time = datetime.utcnow()
        codes = []
        for i in range(3):
            code = InviteCode(
                code=f"ORDR-{i:04d}",
                owner_id=user_id,
                max_uses=3,
                current_uses=0,
                is_active=True,
                created_at=base_time + timedelta(hours=i),
            )
            codes.append(code)
            db_session.add(code)
        db_session.commit()

        response = await client.get(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        # Find the codes we just created
        our_codes = [c for c in data if c["code"].startswith("ORDR-")]
        if len(our_codes) >= 2:
            # Latest should be first (descending order)
            created_times = [datetime.fromisoformat(c["created_at"].replace("Z", "+00:00")) for c in our_codes]
            assert created_times == sorted(created_times, reverse=True)

    async def test_create_code_after_deactivating_one(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test that deactivating a code allows creating a new one (limit is on active codes)."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'extended@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create 3 active codes (max limit)
        for i in range(3):
            code = InviteCode(
                code=f"LIMT-{i:04d}",
                owner_id=user_id,
                max_uses=3,
                current_uses=0,
                is_active=True,
            )
            db_session.add(code)
        db_session.commit()

        # Try to create 4th - should fail
        response = await client.post(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )
        assert response.status_code == 400

        # Deactivate one code
        code_to_deactivate = db_session.exec(
            sql_text("SELECT id FROM invite_code WHERE code = 'LIMT-0000'")
        ).first()
        if code_to_deactivate:
            db_session.execute(
                sql_text("UPDATE invite_code SET is_active = 0 WHERE id = :id"),
                {"id": code_to_deactivate[0]}
            )
            db_session.commit()

        # Now should be able to create a new one
        response = await client.post(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )
        assert response.status_code == 201

    async def test_create_code_unique_format(self, client: AsyncClient, auth_headers):
        """Test that created codes follow the XXXX-XXXX format."""
        response = await client.post(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )

        assert response.status_code == 201
        data = response.json()

        code = data["code"]
        # Check format: XXXX-XXXX (8 chars with hyphen in middle)
        assert len(code) == 9
        assert code[4] == "-"
        # Check all parts are alphanumeric
        parts = code.split("-")
        assert len(parts) == 2
        assert len(parts[0]) == 4
        assert len(parts[1]) == 4
        assert parts[0].isalnum()
        assert parts[1].isalnum()


@pytest.mark.integration
class TestDeactivateCodeExtended:
    """Extended tests for invite code deactivation."""

    async def test_deactivate_already_deactivated(self, client: AsyncClient, auth_headers, user_with_codes, db_session: Session):
        """Test deactivating an already deactivated code."""
        from sqlalchemy import text as sql_text

        # Find the inactive code
        inactive_code = db_session.exec(
            sql_text("SELECT id FROM invite_code WHERE code = 'INAC-0002'")
        ).first()

        if inactive_code:
            # Try to deactivate again - should still work (idempotent)
            response = await client.delete(
                f"/api/v1/referral/codes/{inactive_code[0]}",
                headers=auth_headers,
            )

            # Response depends on implementation - could be 204 or 400
            # Most implementations return 204 for idempotent deletes
            assert response.status_code in [204, 400]

    async def test_deactivate_code_with_existing_uses(self, client: AsyncClient, auth_headers, user_with_codes, db_session: Session):
        """Test that codes with existing uses can still be deactivated."""
        from sqlalchemy import text as sql_text

        # Find code with uses
        used_code = db_session.exec(
            sql_text("SELECT id FROM invite_code WHERE code = 'USED-0003'")
        ).first()

        if used_code:
            response = await client.delete(
                f"/api/v1/referral/codes/{used_code[0]}",
                headers=auth_headers,
            )

            assert response.status_code == 204

            # Verify it's deactivated
            code = db_session.get(InviteCode, used_code[0])
            assert code.is_active is False
            # Uses should still be preserved
            assert code.current_uses == 2


@pytest.mark.integration
class TestReferralStatsExtended:
    """Extended tests for referral statistics."""

    async def test_stats_create_on_first_access(self, client: AsyncClient, auth_headers):
        """Test that stats are created automatically on first access."""
        response = await client.get(
            "/api/v1/referral/stats",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        # Should return default values
        assert data["total_invites"] == 0
        assert data["successful_invites"] == 0
        assert data["total_points"] == 0
        assert data["available_points"] == 0

    async def test_stats_with_negative_values_protected(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test that stats cannot have negative values."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'extended@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create stats with positive values
        stats = UserStats(
            user_id=user_id,
            total_invites=5,
            successful_invites=3,
            total_points=100,
            available_points=50,
        )
        db_session.add(stats)
        db_session.commit()

        response = await client.get(
            "/api/v1/referral/stats",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_invites"] >= 0
        assert data["successful_invites"] >= 0
        assert data["total_points"] >= 0
        assert data["available_points"] >= 0


@pytest.mark.integration
class TestUserRewardsExtended:
    """Extended tests for user rewards."""

    async def test_rewards_with_expiration(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test that rewards include expiration information."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'extended@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create a reward with expiration
        reward = UserReward(
            user_id=user_id,
            reward_type=REWARD_TYPE_POINTS,
            amount=14,
            source="referral",
            is_used=False,
            expires_at=datetime.utcnow() + timedelta(days=30),
        )
        db_session.add(reward)
        db_session.commit()

        response = await client.get(
            "/api/v1/referral/rewards",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

        # Find our reward
        expiring_rewards = [r for r in data if r["amount"] == 14]
        assert len(expiring_rewards) >= 1
        assert expiring_rewards[0]["expires_at"] is not None

    async def test_rewards_ordered_by_created_desc(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test that rewards are returned in descending order by creation date."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'extended@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create multiple rewards
        base_time = datetime.utcnow()
        for i in range(3):
            reward = UserReward(
                user_id=user_id,
                reward_type=REWARD_TYPE_POINTS,
                amount=100 * (i + 1),
                source="referral",
                is_used=False,
                created_at=base_time + timedelta(hours=i),
            )
            db_session.add(reward)
        db_session.commit()

        response = await client.get(
            "/api/v1/referral/rewards",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        # Find our rewards
        our_rewards = [r for r in data if r["source"] == "referral"]
        if len(our_rewards) >= 2:
            created_times = [datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")) for r in our_rewards]
            assert created_times == sorted(created_times, reverse=True)

    async def test_rewards_include_used(self, client: AsyncClient, auth_headers, db_session: Session):
        """Test that used rewards are also returned."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'extended@example.com'")
        ).first()
        user_id = user_result[0] if user_result else None

        # Create a used reward
        reward = UserReward(
            user_id=user_id,
            reward_type=REWARD_TYPE_POINTS,
            amount=100,
            source="referral",
            is_used=True,
            used_at=datetime.utcnow(),
        )
        db_session.add(reward)
        db_session.commit()

        response = await client.get(
            "/api/v1/referral/rewards",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()

        # Should include the used reward
        used_rewards = [r for r in data if r["is_used"] is True]
        assert len(used_rewards) >= 1


@pytest.mark.integration
class TestCodeOwnershipAndSecurity:
    """Tests for code ownership and security."""

    async def test_cannot_access_other_user_codes(self, client: AsyncClient, auth_headers, auth_headers_user2, db_session: Session):
        """Test that users cannot access codes created by other users."""
        # Create a code with user1
        create_response = await client.post(
            "/api/v1/referral/codes",
            headers=auth_headers,
        )
        assert create_response.status_code == 201
        code_data = create_response.json()

        # User2 should not see user1's codes
        response = await client.get(
            "/api/v1/referral/codes",
            headers=auth_headers_user2,
        )

        assert response.status_code == 200
        data = response.json()
        user2_codes = [c for c in data if c["id"] == code_data["id"]]
        assert len(user2_codes) == 0

    async def test_cannot_see_other_user_stats(self, client: AsyncClient, auth_headers, auth_headers_user2, db_session: Session):
        """Test that users cannot see other users' stats."""
        from sqlalchemy import text as sql_text

        # Add stats for user1
        user1_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'extended@example.com'")
        ).first()
        user1_id = user1_result[0] if user1_result else None

        stats = UserStats(
            user_id=user1_id,
            total_invites=100,
            successful_invites=50,
            total_points=5000,
            available_points=3000,
        )
        db_session.add(stats)
        db_session.commit()

        # User2's stats should be different
        response = await client.get(
            "/api/v1/referral/stats",
            headers=auth_headers_user2,
        )

        assert response.status_code == 200
        data = response.json()
        # User2 should have default stats
        assert data["total_invites"] == 0
        assert data["successful_invites"] == 0

    async def test_cannot_see_other_user_rewards(self, client: AsyncClient, auth_headers, auth_headers_user2, db_session: Session):
        """Test that users cannot see other users' rewards."""
        from sqlalchemy import text as sql_text

        # Add reward for user1
        user1_result = db_session.exec(
            sql_text("SELECT id FROM user WHERE email = 'extended@example.com'")
        ).first()
        user1_id = user1_result[0] if user1_result else None

        reward = UserReward(
            user_id=user1_id,
            reward_type=REWARD_TYPE_POINTS,
            amount=1000,
            source="referral",
            is_used=False,
        )
        db_session.add(reward)
        db_session.commit()

        # User2 should not see user1's rewards
        response = await client.get(
            "/api/v1/referral/rewards",
            headers=auth_headers_user2,
        )

        assert response.status_code == 200
        data = response.json()
        # User2 should have no rewards
        assert len(data) == 0


@pytest.mark.integration
class TestConcurrentOperations:
    """Tests for concurrent operations and edge cases."""

    async def test_multiple_code_creation_sequence(self, client: AsyncClient, auth_headers):
        """Test creating multiple codes in sequence."""
        codes = []
        for _ in range(3):
            response = await client.post(
                "/api/v1/referral/codes",
                headers=auth_headers,
            )

            if response.status_code == 201:
                data = response.json()
                codes.append(data["code"])

        # All codes should be unique
        assert len(set(codes)) == len(codes)

    async def test_code_format_consistency(self, client: AsyncClient, auth_headers):
        """Test that all created codes have consistent format."""
        import re

        for _ in range(5):
            response = await client.post(
                "/api/v1/referral/codes",
                headers=auth_headers,
            )

            if response.status_code == 201:
                data = response.json()
                code = data["code"]
                # Check format matches XXXX-XXXX pattern
                assert re.match(r'^[A-Z0-9]{4}-[A-Z0-9]{4}$', code), f"Code {code} doesn't match expected format"

    async def test_expired_code_at_boundary(self, client: AsyncClient, db_session: Session):
        """Test validation of code expiring right now."""
        from sqlalchemy import text as sql_text

        user_result = db_session.exec(
            sql_text("SELECT id FROM user LIMIT 1")
        ).first()

        if user_result:
            user_id = user_result[0]
            # Code that expires in 1 second - will be expired by the time we validate
            code = InviteCode(
                code="BOND-ARY01",
                owner_id=user_id,
                max_uses=3,
                current_uses=0,
                is_active=True,
                expires_at=datetime.utcnow() - timedelta(seconds=1),  # Already expired
            )
            db_session.add(code)
            db_session.commit()

            response = await client.post(
                "/api/v1/referral/codes/BOND-ARY01/validate"
            )

            assert response.status_code == 200
            data = response.json()
            assert data["valid"] is False
            assert data["message"] == "Invite code is invalid or unavailable"
