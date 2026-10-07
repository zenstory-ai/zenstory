"""
Tests for ReferralService.

Unit tests for the referral/invite code management service, covering:
- Invite code generation and validation
- Invite code creation with limits
- Referral creation and tracking
- Reward distribution
"""
import re
from datetime import timedelta

import pytest
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import User
from models.points import PointsTransaction
from models.referral import (
    REFERRAL_STATUS_COMPLETED,
    REFERRAL_STATUS_PENDING,
    REFERRAL_STATUS_REWARDED,
    REWARD_TYPE_POINTS,
    InviteCode,
    UserReward,
    UserStats,
)
from services.features.points_service import points_service
from services.features.referral_service import (
    INVITE_CODE_CHARS,
    INVITE_CODE_LENGTH,
    INVITEE_REWARD_POINTS,
    INVITER_REWARD_POINTS,
    MAX_INVITE_CODES_PER_USER,
    complete_referral_and_reward,
    create_invite_code,
    create_referral,
    generate_invite_code,
    get_user_invite_codes,
    get_user_referral_stats,
    validate_invite_code,
)


@pytest.fixture
def test_user(db_session: Session):
    """Create a test user for referral testing."""
    user = User(
        email="test@example.com",
        username="testuser",
        hashed_password="hashed_password",
        name="Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_user_2(db_session: Session):
    """Create a second test user for referral testing."""
    user = User(
        email="test2@example.com",
        username="testuser2",
        hashed_password="hashed_password",
        name="Test User 2",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_user_3(db_session: Session):
    """Create a third test user for referral testing."""
    user = User(
        email="test3@example.com",
        username="testuser3",
        hashed_password="hashed_password",
        name="Test User 3",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.mark.unit
class TestGenerateInviteCode:
    """Tests for generate_invite_code function."""

    def test_generate_invite_code_format(self):
        """Test that generated code follows XXXX-XXXX format."""
        code = generate_invite_code()

        # Should match format XXXX-XXXX
        assert re.match(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$", code), f"Code {code} doesn't match XXXX-XXXX format"

        # Should have correct total length (8 chars + 1 hyphen)
        assert len(code) == INVITE_CODE_LENGTH + 1

    def test_generate_invite_code_characters(self):
        """Test that generated code only uses allowed characters."""
        code = generate_invite_code()

        # Remove hyphen for character check
        code_chars = code.replace("-", "")

        # All characters should be from INVITE_CODE_CHARS
        for char in code_chars:
            assert char in INVITE_CODE_CHARS, f"Character {char} not in allowed chars"

    def test_generate_invite_code_uniqueness(self):
        """Test that multiple calls generate different codes (probabilistic)."""
        codes = set()
        for _ in range(100):
            code = generate_invite_code()
            codes.add(code)

        # With 100 codes, we should have at least 95 unique ones
        # (probabilistically almost all should be unique)
        assert len(codes) >= 95, "Generated codes are not sufficiently unique"

    def test_generate_invite_code_no_confusing_chars(self):
        """Test that generated codes don't contain confusing characters."""
        confusing_chars = {"0", "O", "1", "I", "L"}

        for _ in range(100):
            code = generate_invite_code()
            code_chars = set(code.replace("-", ""))
            # Should not contain any confusing characters
            assert not code_chars.intersection(confusing_chars), f"Code {code} contains confusing characters"


@pytest.mark.unit
class TestCreateInviteCode:
    """Tests for create_invite_code function."""

    @pytest.mark.asyncio
    async def test_create_invite_code_success(self, db_session: Session, test_user):
        """Test successful creation of an invite code."""
        invite_code = create_invite_code(test_user.id, db_session)

        assert invite_code is not None
        assert invite_code.code is not None
        assert invite_code.owner_id == test_user.id
        assert invite_code.is_active is True
        assert invite_code.max_uses == 3
        assert invite_code.current_uses == 0
        assert re.match(r"^[A-Z0-9]{4}-[A-Z0-9]{4}$", invite_code.code)

    @pytest.mark.asyncio
    async def test_create_invite_code_max_limit(self, db_session: Session, test_user):
        """Test that users cannot create more than MAX_INVITE_CODES_PER_USER codes."""
        # Create max number of codes
        for _ in range(MAX_INVITE_CODES_PER_USER):
            create_invite_code(test_user.id, db_session)

        # Try to create one more - should fail
        with pytest.raises(APIException) as exc_info:
            create_invite_code(test_user.id, db_session)

        assert exc_info.value.error_code == ErrorCode.REFERRAL_MAX_CODES_REACHED
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_create_invite_code_ignore_max_limit_allows_more_than_max(
        self,
        db_session: Session,
        test_user,
    ):
        """Test that callers can bypass the max active invite code limit when requested."""
        create_count = MAX_INVITE_CODES_PER_USER + 2

        for _ in range(create_count):
            create_invite_code(
                test_user.id,
                db_session,
                ignore_max_limit=True,
            )

        active_codes = db_session.exec(
            select(InviteCode)
            .where(InviteCode.owner_id == test_user.id)
            .where(InviteCode.is_active == True)
        ).all()

        assert len(active_codes) == create_count

    @pytest.mark.asyncio
    async def test_create_invite_code_can_create_after_deactivation(self, db_session: Session, test_user):
        """Test that users can create new codes after deactivating existing ones."""
        # Create max codes
        codes = []
        for _ in range(MAX_INVITE_CODES_PER_USER):
            code = create_invite_code(test_user.id, db_session)
            codes.append(code)

        # Deactivate one code
        codes[0].is_active = False
        db_session.add(codes[0])
        db_session.commit()

        # Should now be able to create a new one
        new_code = create_invite_code(test_user.id, db_session)
        assert new_code is not None

    @pytest.mark.asyncio
    async def test_create_invite_code_unique_codes(self, db_session: Session, test_user):
        """Test that each created code is unique."""
        codes = []
        for _ in range(MAX_INVITE_CODES_PER_USER):
            code = create_invite_code(test_user.id, db_session)
            codes.append(code.code)

        # All codes should be unique
        assert len(codes) == len(set(codes))


@pytest.mark.unit
class TestValidateInviteCode:
    """Tests for validate_invite_code function."""

    @pytest.mark.asyncio
    async def test_validate_invite_code_valid(self, db_session: Session, test_user):
        """Test validation of a valid invite code."""
        # Create a code
        created = create_invite_code(test_user.id, db_session)

        # Validate it
        is_valid, invite_code, error = validate_invite_code(created.code, db_session)

        assert is_valid is True
        assert invite_code is not None
        assert invite_code.code == created.code
        assert error == ""

    @pytest.mark.asyncio
    async def test_validate_invite_code_case_insensitive(self, db_session: Session, test_user):
        """Test that code validation is case-insensitive."""
        created = create_invite_code(test_user.id, db_session)

        # Validate with lowercase
        is_valid, invite_code, error = validate_invite_code(created.code.lower(), db_session)

        assert is_valid is True
        assert invite_code is not None

    @pytest.mark.asyncio
    async def test_validate_invite_code_invalid(self, db_session: Session):
        """Test validation of a non-existent invite code."""
        is_valid, invite_code, error = validate_invite_code("XXXX-XXXX", db_session)

        assert is_valid is False
        assert invite_code is None
        assert error == "Invalid invite code"

    @pytest.mark.asyncio
    async def test_validate_invite_code_expired(self, db_session: Session, test_user):
        """Test validation of an expired invite code."""
        # Create a code with expiration in the past
        invite_code = InviteCode(
            code="ABCD-1234",
            owner_id=test_user.id,
            max_uses=3,
            current_uses=0,
            is_active=True,
            expires_at=utcnow() - timedelta(days=1),
        )
        db_session.add(invite_code)
        db_session.commit()

        is_valid, result_code, error = validate_invite_code("ABCD-1234", db_session)

        assert is_valid is False
        assert result_code is None
        assert "expired" in error.lower()

    @pytest.mark.asyncio
    async def test_validate_invite_code_exhausted(self, db_session: Session, test_user):
        """Test validation of an invite code that has reached max uses."""
        # Create a code that's been fully used
        invite_code = InviteCode(
            code="ABCD-1234",
            owner_id=test_user.id,
            max_uses=3,
            current_uses=3,
            is_active=True,
        )
        db_session.add(invite_code)
        db_session.commit()

        is_valid, result_code, error = validate_invite_code("ABCD-1234", db_session)

        assert is_valid is False
        assert result_code is None
        assert "usage limit" in error.lower() or "reached" in error.lower()

    @pytest.mark.asyncio
    async def test_validate_invite_code_deactivated(self, db_session: Session, test_user):
        """Test validation of a deactivated invite code."""
        # Create an inactive code
        invite_code = InviteCode(
            code="ABCD-1234",
            owner_id=test_user.id,
            max_uses=3,
            current_uses=0,
            is_active=False,
        )
        db_session.add(invite_code)
        db_session.commit()

        is_valid, result_code, error = validate_invite_code("ABCD-1234", db_session)

        assert is_valid is False
        assert result_code is None
        assert "deactivated" in error.lower()


@pytest.mark.unit
class TestCreateReferral:
    """Tests for create_referral function."""

    @pytest.mark.asyncio
    async def test_create_referral_success(self, db_session: Session, test_user, test_user_2):
        """Test successful creation of a referral relationship."""
        # Create invite code for inviter
        invite_code = create_invite_code(test_user.id, db_session)

        # Create referral
        referral = await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
        )

        assert referral is not None
        assert referral.inviter_id == test_user.id
        assert referral.invitee_id == test_user_2.id
        assert referral.invite_code_id == invite_code.id
        assert referral.status == REFERRAL_STATUS_PENDING

        # Check invite code usage was incremented
        db_session.refresh(invite_code)
        assert invite_code.current_uses == 1

    @pytest.mark.asyncio
    async def test_create_referral_updates_stats(self, db_session: Session, test_user, test_user_2):
        """Test that creating a referral updates inviter's stats."""
        invite_code = create_invite_code(test_user.id, db_session)

        await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
        )

        # Check stats were updated
        stats = db_session.exec(
            select(UserStats).where(UserStats.user_id == test_user.id)
        ).first()

        assert stats is not None
        assert stats.total_invites == 1

    @pytest.mark.asyncio
    async def test_create_referral_already_exists(self, db_session: Session, test_user, test_user_2):
        """Test that a user can only have one referral."""
        invite_code = create_invite_code(test_user.id, db_session)

        # Create first referral
        await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
        )

        # Create another code and try to refer same user again
        invite_code_2 = create_invite_code(test_user.id, db_session)

        with pytest.raises(APIException) as exc_info:
            await create_referral(
                invite_code=invite_code_2,
                invitee_id=test_user_2.id,
                session=db_session,
            )

        assert exc_info.value.error_code == ErrorCode.REFERRAL_ALREADY_EXISTS

    @pytest.mark.asyncio
    async def test_create_referral_with_fraud_detection_data(self, db_session: Session, test_user, test_user_2):
        """Test creating referral with device fingerprint and IP."""
        invite_code = create_invite_code(test_user.id, db_session)

        referral = await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
            device_fingerprint="abc123",
            ip_address="192.168.1.1",
        )

        assert referral.device_fingerprint == "abc123"
        assert referral.ip_address == "192.168.1.1"


@pytest.mark.unit
class TestCompleteReferralAndReward:
    """Tests for complete_referral_and_reward function."""

    @pytest.mark.asyncio
    async def test_complete_referral_and_reward(self, db_session: Session, test_user, test_user_2):
        """Test completing a referral and distributing rewards."""
        # Setup: create invite code and referral
        invite_code = create_invite_code(test_user.id, db_session)
        referral = await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
        )

        # Complete the referral
        await complete_referral_and_reward(referral.id, db_session)

        # Refresh and check referral status
        db_session.refresh(referral)
        assert referral.status == REFERRAL_STATUS_REWARDED
        assert referral.completed_at is not None
        assert referral.rewarded_at is not None
        assert referral.inviter_rewarded is True
        assert referral.invitee_rewarded is True

    @pytest.mark.asyncio
    async def test_complete_referral_inviter_reward(self, db_session: Session, test_user, test_user_2):
        """Test that inviter receives points reward."""
        invite_code = create_invite_code(test_user.id, db_session)
        referral = await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
        )

        await complete_referral_and_reward(referral.id, db_session)

        # Check inviter reward
        inviter_rewards = db_session.exec(
            select(UserReward).where(UserReward.user_id == test_user.id)
        ).all()

        assert len(inviter_rewards) == 1
        assert inviter_rewards[0].reward_type == REWARD_TYPE_POINTS
        assert inviter_rewards[0].amount == INVITER_REWARD_POINTS
        assert inviter_rewards[0].source == "referral"
        assert inviter_rewards[0].referral_id == referral.id
        assert inviter_rewards[0].is_used is True

        ledger_tx = db_session.exec(
            select(PointsTransaction).where(
                PointsTransaction.user_id == test_user.id,
                PointsTransaction.transaction_type == "referral",
                PointsTransaction.source_id == referral.id,
            )
        ).first()
        assert ledger_tx is not None
        assert ledger_tx.amount == INVITER_REWARD_POINTS

    @pytest.mark.asyncio
    async def test_complete_referral_invitee_reward(
        self,
        db_session: Session,
        test_user,
        test_user_2,
    ):
        """Test that invitee receives points reward."""
        invite_code = create_invite_code(test_user.id, db_session)
        referral = await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
        )

        await complete_referral_and_reward(referral.id, db_session)

        # Check invitee reward
        invitee_rewards = db_session.exec(
            select(UserReward).where(UserReward.user_id == test_user_2.id)
        ).all()

        assert len(invitee_rewards) == 1
        assert invitee_rewards[0].reward_type == REWARD_TYPE_POINTS
        assert invitee_rewards[0].amount == INVITEE_REWARD_POINTS
        assert invitee_rewards[0].source == "referral"
        assert invitee_rewards[0].is_used is True

        ledger_tx = db_session.exec(
            select(PointsTransaction).where(
                PointsTransaction.user_id == test_user_2.id,
                PointsTransaction.transaction_type == "referral",
                PointsTransaction.source_id == referral.id,
            )
        ).first()
        assert ledger_tx is not None
        assert ledger_tx.amount == INVITEE_REWARD_POINTS

    @pytest.mark.asyncio
    async def test_complete_referral_updates_stats(self, db_session: Session, test_user, test_user_2):
        """Test that completing a referral updates user stats."""
        invite_code = create_invite_code(test_user.id, db_session)
        referral = await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
        )

        await complete_referral_and_reward(referral.id, db_session)

        # Check inviter stats
        stats = db_session.exec(
            select(UserStats).where(UserStats.user_id == test_user.id)
        ).first()

        assert stats.successful_invites == 1
        assert stats.total_points == INVITER_REWARD_POINTS
        assert stats.available_points == INVITER_REWARD_POINTS

    @pytest.mark.asyncio
    async def test_complete_referral_blocks_repeat_ip_rewards(
        self,
        db_session: Session,
        test_user,
        test_user_2,
        test_user_3,
    ):
        """Second referral from same inviter+IP in short window should be blocked."""
        invite_code = create_invite_code(test_user.id, db_session)
        first_referral = await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
            ip_address="10.0.0.1",
        )
        await complete_referral_and_reward(first_referral.id, db_session)

        second_referral = await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_3.id,
            session=db_session,
            ip_address="10.0.0.1",
        )
        await complete_referral_and_reward(second_referral.id, db_session)
        db_session.refresh(second_referral)

        assert second_referral.status == REFERRAL_STATUS_COMPLETED
        assert second_referral.inviter_rewarded is False
        assert second_referral.invitee_rewarded is False

    @pytest.mark.asyncio
    async def test_complete_referral_not_found(self, db_session: Session):
        """Test completing a non-existent referral raises error."""
        with pytest.raises(APIException) as exc_info:
            await complete_referral_and_reward("non-existent-id", db_session)

        assert exc_info.value.error_code == ErrorCode.REFERRAL_NOT_FOUND

    @pytest.mark.asyncio
    async def test_complete_referral_idempotent(self, db_session: Session, test_user, test_user_2):
        """Test that completing an already rewarded referral is idempotent."""
        invite_code = create_invite_code(test_user.id, db_session)
        referral = await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
        )

        # Complete once
        await complete_referral_and_reward(referral.id, db_session)

        # Complete again - should not raise error
        await complete_referral_and_reward(referral.id, db_session)

        # Should still only have one set of rewards
        inviter_rewards = db_session.exec(
            select(UserReward).where(UserReward.user_id == test_user.id)
        ).all()
        assert len(inviter_rewards) == 1


@pytest.mark.unit
class TestGetUserReferralStats:
    """Tests for get_user_referral_stats function."""

    @pytest.mark.asyncio
    async def test_get_user_referral_stats_empty(self, db_session: Session, test_user):
        """Test getting stats for user with no referrals."""
        stats = get_user_referral_stats(test_user.id, db_session)

        assert stats["total_invites"] == 0
        assert stats["successful_invites"] == 0
        assert stats["total_points"] == 0
        assert stats["available_points"] == 0

    @pytest.mark.asyncio
    async def test_get_user_referral_stats_success(self, db_session: Session, test_user, test_user_2):
        """Test getting stats after successful referral."""
        invite_code = create_invite_code(test_user.id, db_session)
        referral = await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
        )
        await complete_referral_and_reward(referral.id, db_session)

        stats = get_user_referral_stats(test_user.id, db_session)

        assert stats["total_invites"] == 1
        assert stats["successful_invites"] == 1
        assert stats["total_points"] == INVITER_REWARD_POINTS
        assert stats["available_points"] == INVITER_REWARD_POINTS

    @pytest.mark.asyncio
    async def test_get_user_referral_stats_uses_wallet_available_points(
        self, db_session: Session, test_user, test_user_2
    ):
        """Available points should reflect the unified points wallet balance."""
        invite_code = create_invite_code(test_user.id, db_session)
        referral = await create_referral(
            invite_code=invite_code,
            invitee_id=test_user_2.id,
            session=db_session,
        )
        await complete_referral_and_reward(referral.id, db_session)

        points_service.spend_points(
            session=db_session,
            user_id=test_user.id,
            amount=20,
            transaction_type="admin_adjustment",
        )

        stats = get_user_referral_stats(test_user.id, db_session)
        assert stats["total_points"] == INVITER_REWARD_POINTS
        assert stats["available_points"] == INVITER_REWARD_POINTS - 20


@pytest.mark.unit
class TestGetUserInviteCodes:
    """Tests for get_user_invite_codes function."""

    @pytest.mark.asyncio
    async def test_get_user_invite_codes_empty(self, db_session: Session, test_user):
        """Test getting codes for user with no codes."""
        codes = get_user_invite_codes(test_user.id, db_session)
        assert codes == []

    @pytest.mark.asyncio
    async def test_get_user_invite_codes_success(self, db_session: Session, test_user):
        """Test getting all invite codes for a user."""
        # Create multiple codes
        created_codes = []
        for _ in range(2):
            code = create_invite_code(test_user.id, db_session)
            created_codes.append(code)

        codes = get_user_invite_codes(test_user.id, db_session)

        assert len(codes) == 2
        # Should be ordered by created_at desc
        assert codes[0].created_at >= codes[1].created_at
