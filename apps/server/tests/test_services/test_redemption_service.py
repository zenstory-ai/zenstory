"""
Tests for RedemptionService.

Unit tests for the redemption code service, covering:
- Code format validation
- Checksum verification
- Code redemption
- Usage tracking
"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlmodel import Session

from core.error_codes import ErrorCode
from models import User
from models.subscription import (
    RedemptionCode,
    SubscriptionPlan,
    UserSubscription,
)
from services.subscription.redemption_service import redemption_service


@pytest.fixture
def test_user(db_session: Session):
    """Create a test user for redemption testing."""
    user = User(
        email="redemption@example.com",
        username="redemptionuser",
        hashed_password="hashed_password",
        name="Redemption Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_admin(db_session: Session):
    """Create an admin user for code creation."""
    user = User(
        email="admin@example.com",
        username="adminuser",
        hashed_password="hashed_password",
        name="Admin User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def free_plan(db_session: Session):
    """Create a free subscription plan."""
    plan = SubscriptionPlan(
        name="free",
        display_name="Free",
        display_name_en="Free",
        price_monthly_cents=0,
        price_yearly_cents=0,
        features={"ai_conversations_per_day": 10, "max_projects": 3},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.fixture
def pro_plan(db_session: Session):
    """Create a pro subscription plan."""
    plan = SubscriptionPlan(
        name="pro",
        display_name="Pro",
        display_name_en="Pro",
        price_monthly_cents=2900,
        price_yearly_cents=29000,
        features={"ai_conversations_per_day": -1, "max_projects": -1},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.fixture
def mock_hmac_secret():
    """Mock HMAC secret for testing."""
    return "test-secret-key-must-be-at-least-32-characters-long"


@pytest.mark.unit
class TestValidateCodeFormat:
    """Tests for validate_code_format method."""

    def test_validate_code_format_valid(self):
        """Test validation of valid code formats."""
        valid_codes = [
            "ERG-PRO7M-ABCD-12345678",
            "ERG-PRO1M-XYZ1-ABCDEFGH",
            "ERG-PRO-1234-12345678",
            "ERG-FREE7M-AAAA-00000000",
        ]

        for code in valid_codes:
            assert redemption_service.validate_code_format(code) is True

    def test_validate_code_format_invalid(self):
        """Test validation of invalid code formats."""
        invalid_codes = [
            "INVALID-CODE",
            "ERG-P-ABCD-12345678",  # Tier+duration part too short
            "erg-pro7m-abcd-12345678",  # Lowercase
            "ERG-PRO7MABCD12345678",  # Missing hyphens
            "ERG-PRO7M-ABC-12345678",  # Checksum too short
            "ERG-PRO7M-ABCD-1234567",  # Random too short
            "ERG-PRO7M-ABCDE-12345678",  # Checksum too long
            "",
            "ERG-PRO7M-ABCD-123456789",  # Random too long
        ]

        for code in invalid_codes:
            assert redemption_service.validate_code_format(code) is False

    def test_validate_code_format_edge_cases(self):
        """Test validation with edge case inputs."""
        assert redemption_service.validate_code_format("") is False
        with pytest.raises(TypeError):
            redemption_service.validate_code_format(None)  # type: ignore[arg-type]
        assert redemption_service.validate_code_format("ERG-PRO7M-ABCD-12345678 ") is False  # Trailing space


@pytest.mark.unit
class TestVerifyChecksum:
    """Tests for verify_checksum method."""

    def test_verify_checksum_valid(self, mock_hmac_secret):
        """Test checksum verification with valid code."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Generate a valid checksum for testing
            tier_duration = "PRO7M"
            random_part = "12345678"
            message = f"{tier_duration}-{random_part}"
            signature = hmac.new(
                mock_hmac_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
            expected_checksum = signature[:4].hex().upper()[:4]

            code = f"ERG-{tier_duration}-{expected_checksum}-{random_part}"

            assert redemption_service.verify_checksum(code) is True

    def test_verify_checksum_invalid(self, mock_hmac_secret):
        """Test checksum verification with invalid checksum."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Code with invalid checksum
            code = "ERG-PRO7M-XXXX-12345678"  # Wrong checksum

            assert redemption_service.verify_checksum(code) is False

    def test_verify_checksum_wrong_format(self):
        """Test checksum verification with wrong format."""
        assert redemption_service.verify_checksum("INVALID-CODE") is False

    def test_verify_checksum_different_secret(self, mock_hmac_secret):
        """Test that checksum fails with different secret."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Generate checksum with different secret
            wrong_secret = "wrong-secret-key-must-be-at-least-32-chars"
            tier_duration = "PRO7M"
            random_part = "12345678"
            message = f"{tier_duration}-{random_part}"
            signature = hmac.new(
                wrong_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
            wrong_checksum = signature[:4].hex().upper()[:4]

            code = f"ERG-{tier_duration}-{wrong_checksum}-{random_part}"

            # Should fail because we're using mock_hmac_secret for verification
            assert redemption_service.verify_checksum(code) is False


@pytest.mark.unit
class TestGetCodeByCode:
    """Tests for get_code_by_code method."""

    def test_get_code_by_code_exists(
        self, db_session: Session, test_admin, pro_plan
    ):
        """Test getting an existing code."""
        code = RedemptionCode(
            code="ERG-PRO7M-ABCD-12345678",
            code_type="single_use",
            tier="pro",
            duration_days=30,
            max_uses=1,
            current_uses=0,
            created_by=test_admin.id,
            is_active=True,
        )
        db_session.add(code)
        db_session.commit()

        result = redemption_service.get_code_by_code(
            db_session, "ERG-PRO7M-ABCD-12345678"
        )

        assert result is not None
        assert result.code == "ERG-PRO7M-ABCD-12345678"

    def test_get_code_by_code_not_exists(self, db_session: Session):
        """Test getting a non-existent code."""
        result = redemption_service.get_code_by_code(
            db_session, "ERG-PRO7M-XXXX-12345678"
        )

        assert result is None


@pytest.mark.unit
class TestRedeemCode:
    """Tests for redeem_code method."""

    def test_redeem_code_invalid_format(self, db_session: Session, test_user):
        """Test redeeming code with invalid format."""
        success, message, info = redemption_service.redeem_code(
            db_session, "INVALID", test_user.id
        )

        assert success is False
        assert info is None
        assert message == ErrorCode.REDEMPTION_CODE_INVALID

    def test_redeem_code_invalid_checksum(
        self, db_session: Session, test_user, mock_hmac_secret
    ):
        """Test redeeming code with invalid checksum."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            success, message, info = redemption_service.redeem_code(
                db_session, "ERG-PRO7M-XXXX-12345678", test_user.id
            )

            assert success is False
            assert info is None

    def test_redeem_code_not_in_database(
        self, db_session: Session, test_user, mock_hmac_secret, free_plan, pro_plan
    ):
        """Test redeeming code that passes validation but not in database."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Generate valid checksum
            tier_duration = "PRO7M"
            random_part = "12345678"
            message = f"{tier_duration}-{random_part}"
            signature = hmac.new(
                mock_hmac_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
            checksum = signature[:4].hex().upper()[:4]
            code = f"ERG-{tier_duration}-{checksum}-{random_part}"

            success, message, info = redemption_service.redeem_code(
                db_session, code, test_user.id
            )

            assert success is False
            assert info is None

    def test_redeem_code_disabled(
        self, db_session: Session, test_user, test_admin, free_plan, pro_plan, mock_hmac_secret
    ):
        """Test redeeming a disabled code."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Generate valid checksum
            tier_duration = "PRO7M"
            random_part = "12345678"
            message = f"{tier_duration}-{random_part}"
            signature = hmac.new(
                mock_hmac_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
            checksum = signature[:4].hex().upper()[:4]
            code = f"ERG-{tier_duration}-{checksum}-{random_part}"

            # Create disabled code
            redemption_code = RedemptionCode(
                code=code,
                code_type="single_use",
                tier="pro",
                duration_days=30,
                max_uses=1,
                current_uses=0,
                created_by=test_admin.id,
                is_active=False,  # Disabled
            )
            db_session.add(redemption_code)
            db_session.commit()

            success, message, info = redemption_service.redeem_code(
                db_session, code, test_user.id
            )

            assert success is False
            assert info is None

    def test_redeem_code_expired(
        self, db_session: Session, test_user, test_admin, free_plan, pro_plan, mock_hmac_secret
    ):
        """Test redeeming an expired code."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Generate valid checksum
            tier_duration = "PRO7M"
            random_part = "12345678"
            message = f"{tier_duration}-{random_part}"
            signature = hmac.new(
                mock_hmac_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
            checksum = signature[:4].hex().upper()[:4]
            code = f"ERG-{tier_duration}-{checksum}-{random_part}"

            # Create expired code
            redemption_code = RedemptionCode(
                code=code,
                code_type="single_use",
                tier="pro",
                duration_days=30,
                max_uses=1,
                current_uses=0,
                created_by=test_admin.id,
                is_active=True,
                expires_at=datetime.utcnow() - timedelta(days=1),  # Expired
            )
            db_session.add(redemption_code)
            db_session.commit()

            success, message, info = redemption_service.redeem_code(
                db_session, code, test_user.id
            )

            assert success is False
            assert info is None

    def test_redeem_code_max_uses_reached(
        self, db_session: Session, test_user, test_admin, free_plan, pro_plan, mock_hmac_secret
    ):
        """Test redeeming a code that has reached max uses."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Generate valid checksum
            tier_duration = "PRO7M"
            random_part = "12345678"
            message = f"{tier_duration}-{random_part}"
            signature = hmac.new(
                mock_hmac_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
            checksum = signature[:4].hex().upper()[:4]
            code = f"ERG-{tier_duration}-{checksum}-{random_part}"

            # Create code with max uses reached
            redemption_code = RedemptionCode(
                code=code,
                code_type="multi_use",
                tier="pro",
                duration_days=30,
                max_uses=3,
                current_uses=3,  # All used
                created_by=test_admin.id,
                is_active=True,
            )
            db_session.add(redemption_code)
            db_session.commit()

            success, message, info = redemption_service.redeem_code(
                db_session, code, test_user.id
            )

            assert success is False
            assert info is None

    def test_redeem_code_already_redeemed_by_user(
        self, db_session: Session, test_user, test_admin, free_plan, pro_plan, mock_hmac_secret
    ):
        """Test redeeming a code already redeemed by the same user."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Generate valid checksum
            tier_duration = "PRO7M"
            random_part = "12345678"
            message = f"{tier_duration}-{random_part}"
            signature = hmac.new(
                mock_hmac_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
            checksum = signature[:4].hex().upper()[:4]
            code = f"ERG-{tier_duration}-{checksum}-{random_part}"

            # Create code already redeemed by this user
            redemption_code = RedemptionCode(
                code=code,
                code_type="multi_use",
                tier="pro",
                duration_days=30,
                max_uses=10,
                current_uses=1,
                created_by=test_admin.id,
                is_active=True,
                redeemed_by=[test_user.id],  # Already redeemed
            )
            db_session.add(redemption_code)
            db_session.commit()

            success, message, info = redemption_service.redeem_code(
                db_session, code, test_user.id
            )

            assert success is False
            assert info is None
            assert message == ErrorCode.REDEMPTION_CODE_ALREADY_REDEEMED_BY_YOU

    def test_redeem_code_success(
        self, db_session: Session, test_user, test_admin, free_plan, pro_plan, mock_hmac_secret
    ):
        """Test successful code redemption."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Generate valid checksum
            tier_duration = "PRO7M"
            random_part = "12345678"
            message = f"{tier_duration}-{random_part}"
            signature = hmac.new(
                mock_hmac_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
            checksum = signature[:4].hex().upper()[:4]
            code = f"ERG-{tier_duration}-{checksum}-{random_part}"

            # Create valid code
            redemption_code = RedemptionCode(
                code=code,
                code_type="single_use",
                tier="pro",
                duration_days=30,
                max_uses=1,
                current_uses=0,
                created_by=test_admin.id,
                is_active=True,
            )
            db_session.add(redemption_code)
            db_session.commit()

            success, message, info = redemption_service.redeem_code(
                db_session, code, test_user.id
            )

            assert success is True
            assert info is not None
            assert info["tier"] == "pro"
            assert info["duration_days"] == 30
            assert "subscription_id" in info

    def test_redeem_code_increments_usage(
        self, db_session: Session, test_user, test_admin, free_plan, pro_plan, mock_hmac_secret
    ):
        """Test that redemption increments code usage."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Generate valid checksum
            tier_duration = "PRO7M"
            random_part = "12345678"
            message = f"{tier_duration}-{random_part}"
            signature = hmac.new(
                mock_hmac_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
            checksum = signature[:4].hex().upper()[:4]
            code = f"ERG-{tier_duration}-{checksum}-{random_part}"

            # Create valid code
            redemption_code = RedemptionCode(
                code=code,
                code_type="multi_use",
                tier="pro",
                duration_days=30,
                max_uses=5,
                current_uses=0,
                created_by=test_admin.id,
                is_active=True,
            )
            db_session.add(redemption_code)
            db_session.commit()

            initial_uses = redemption_code.current_uses

            redemption_service.redeem_code(db_session, code, test_user.id)

            # Refresh to see updated usage
            db_session.refresh(redemption_code)
            assert redemption_code.current_uses == initial_uses + 1

    def test_redeem_code_adds_user_to_redeemed_by(
        self, db_session: Session, test_user, test_admin, free_plan, pro_plan, mock_hmac_secret
    ):
        """Test that redemption adds user to redeemed_by list."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Generate valid checksum
            tier_duration = "PRO7M"
            random_part = "12345678"
            message = f"{tier_duration}-{random_part}"
            signature = hmac.new(
                mock_hmac_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
            checksum = signature[:4].hex().upper()[:4]
            code = f"ERG-{tier_duration}-{checksum}-{random_part}"

            # Create valid code
            redemption_code = RedemptionCode(
                code=code,
                code_type="multi_use",
                tier="pro",
                duration_days=30,
                max_uses=5,
                current_uses=0,
                created_by=test_admin.id,
                is_active=True,
                redeemed_by=[],
            )
            db_session.add(redemption_code)
            db_session.commit()

            redemption_service.redeem_code(db_session, code, test_user.id)

            # Refresh to see updated redeemed_by
            db_session.refresh(redemption_code)
            assert test_user.id in redemption_code.redeemed_by

    def test_redeem_code_creates_subscription(
        self, db_session: Session, test_user, test_admin, free_plan, pro_plan, mock_hmac_secret
    ):
        """Test that redemption creates user subscription."""
        with patch.object(
            redemption_service, "get_hmac_secret", return_value=mock_hmac_secret
        ):
            # Generate valid checksum
            tier_duration = "PRO7M"
            random_part = "12345678"
            message = f"{tier_duration}-{random_part}"
            signature = hmac.new(
                mock_hmac_secret.encode(),
                message.encode(),
                hashlib.sha256,
            ).digest()
            checksum = signature[:4].hex().upper()[:4]
            code = f"ERG-{tier_duration}-{checksum}-{random_part}"

            # Create valid code
            redemption_code = RedemptionCode(
                code=code,
                code_type="single_use",
                tier="pro",
                duration_days=30,
                max_uses=1,
                current_uses=0,
                created_by=test_admin.id,
                is_active=True,
            )
            db_session.add(redemption_code)
            db_session.commit()

            success, message, info = redemption_service.redeem_code(
                db_session, code, test_user.id
            )

            assert success is True

            # Verify subscription was created
            from sqlmodel import select
            subscription = db_session.exec(
                select(UserSubscription).where(UserSubscription.user_id == test_user.id)
            ).first()

            assert subscription is not None
            assert subscription.status == "active"


@pytest.mark.unit
class TestGetHmacSecret:
    """Tests for get_hmac_secret method."""

    def test_get_hmac_secret_valid(self, mock_hmac_secret):
        """Test getting HMAC secret from environment."""
        with patch.dict(os.environ, {"REDEMPTION_CODE_HMAC_SECRET": mock_hmac_secret}):
            result = redemption_service.get_hmac_secret()
            assert result == mock_hmac_secret

    def test_get_hmac_secret_too_short(self):
        """Test that short secret raises error."""
        with patch.dict(os.environ, {"REDEMPTION_CODE_HMAC_SECRET": "too-short"}):
            with pytest.raises(ValueError) as exc_info:
                redemption_service.get_hmac_secret()

            assert "at least 32 characters" in str(exc_info.value).lower()

    def test_get_hmac_secret_empty(self):
        """Test that empty secret raises error."""
        with patch.dict(os.environ, {"REDEMPTION_CODE_HMAC_SECRET": ""}, clear=False):
            with pytest.raises(ValueError):
                redemption_service.get_hmac_secret()
