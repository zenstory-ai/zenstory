"""
Tests for QuotaService.

Unit tests for the quota management service, covering:
- User quota retrieval
- Plan-based limit checking
- AI conversation quota management
- Feature quota management
- Quota reset logic
"""
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlmodel import Session

from config.datetime_utils import normalize_datetime_to_utc
from models import User
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.quota_service import FEATURE_QUOTA_MAP, quota_service
from services.subscription.subscription_service import subscription_service


@pytest.fixture
def test_user(db_session: Session):
    """Create a test user for quota testing."""
    user = User(
        email="quota@example.com",
        username="quotauser",
        hashed_password="hashed_password",
        name="Quota Test User",
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
        features={
            "ai_conversations_per_day": 20,
            "max_projects": 3,
            "materials_library_access": False,
            "material_uploads": 0,
            "material_decompositions": 0,
            "custom_skills": 3,
            "inspiration_copies_monthly": 10,
        },
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
        features={
            "ai_conversations_per_day": -1,  # unlimited
            "max_projects": -1,
            "materials_library_access": True,
            "material_uploads": 5,
            "material_decompositions": 5,
            "custom_skills": -1,
            "inspiration_copies_monthly": -1,
        },
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.unit
class TestGetUserQuota:
    """Tests for get_user_quota method."""

    def test_get_user_quota_no_quota(self, db_session: Session, test_user):
        """Test getting quota for user without quota record."""
        result = quota_service.get_user_quota(db_session, test_user.id)

        assert result is None

    def test_get_user_quota_with_quota(self, db_session: Session, test_user):
        """Test getting quota for user with quota record."""
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            ai_conversations_used=5,
            last_reset_at=now,
        )
        db_session.add(quota)
        db_session.commit()

        result = quota_service.get_user_quota(db_session, test_user.id)

        assert result is not None
        assert result.user_id == test_user.id
        assert result.ai_conversations_used == 5


@pytest.mark.unit
class TestGetUserPlan:
    """Tests for get_user_plan method."""

    def test_get_user_plan_free_default(self, db_session: Session, test_user, free_plan):
        """Test that users without subscription get free plan."""
        result = quota_service.get_user_plan(db_session, test_user.id)

        assert result is not None
        assert result.name == "free"

    def test_get_user_plan_with_subscription(
        self, db_session: Session, test_user, free_plan, pro_plan
    ):
        """Test getting plan for user with active subscription."""
        now = datetime.utcnow()
        subscription = UserSubscription(
            user_id=test_user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db_session.add(subscription)
        db_session.commit()

        result = quota_service.get_user_plan(db_session, test_user.id)

        assert result is not None
        assert result.name == "pro"

    def test_get_user_plan_expired_subscription(
        self, db_session: Session, test_user, free_plan, pro_plan
    ):
        """Test that expired subscription returns free plan."""
        now = datetime.utcnow()
        subscription = UserSubscription(
            user_id=test_user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now - timedelta(days=60),
            current_period_end=now - timedelta(days=30),  # Expired
        )
        db_session.add(subscription)
        db_session.commit()

        result = quota_service.get_user_plan(db_session, test_user.id)

        assert result is not None
        assert result.name == "free"

    def test_get_user_plan_cancelled_subscription(
        self, db_session: Session, test_user, free_plan, pro_plan
    ):
        """Test that cancelled subscription returns free plan."""
        now = datetime.utcnow()
        subscription = UserSubscription(
            user_id=test_user.id,
            plan_id=pro_plan.id,
            status="cancelled",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db_session.add(subscription)
        db_session.commit()

        result = quota_service.get_user_plan(db_session, test_user.id)

        assert result is not None
        assert result.name == "free"


@pytest.mark.unit
class TestGetPlanLimits:
    """Tests for get_plan_limits method."""

    def test_get_plan_limits_free(self, db_session: Session, free_plan):
        """Test getting limits for free plan."""
        result = quota_service.get_plan_limits(free_plan)

        assert result is not None
        assert result["ai_conversations_per_day"] == 20
        assert result["max_projects"] == 3

    def test_get_plan_limits_pro(self, db_session: Session, pro_plan):
        """Test getting limits for pro plan."""
        result = quota_service.get_plan_limits(pro_plan)

        assert result is not None
        assert result["ai_conversations_per_day"] == -1  # Unlimited
        assert result["max_projects"] == -1  # Unlimited


@pytest.mark.unit
class TestFeatureAccess:
    def test_has_feature_access_defaults_to_false_for_free(self, db_session: Session, test_user, free_plan):
        assert quota_service.has_feature_access(
            db_session,
            test_user.id,
            "materials_library_access",
        ) is False

    def test_has_feature_access_true_for_paid(self, db_session: Session, test_user, pro_plan):
        now = datetime.utcnow()
        db_session.add(
            UserSubscription(
                user_id=test_user.id,
                plan_id=pro_plan.id,
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
            )
        )
        db_session.commit()

        assert quota_service.has_feature_access(
            db_session,
            test_user.id,
            "materials_library_access",
        ) is True

    def test_has_feature_access_falls_back_to_default_when_flag_missing(self, db_session: Session, test_user):
        plan = SubscriptionPlan(
            name="custom-default",
            display_name="Custom Default",
            display_name_en="Custom Default",
            price_monthly_cents=0,
            price_yearly_cents=0,
            features={"other_feature": True},
            is_active=True,
        )
        now = datetime.utcnow()
        db_session.add(plan)
        db_session.commit()
        db_session.refresh(plan)
        db_session.add(
            UserSubscription(
                user_id=test_user.id,
                plan_id=plan.id,
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
            )
        )
        db_session.commit()

        assert quota_service.has_feature_access(db_session, test_user.id, "materials_library_access") is False
        assert quota_service.has_feature_access(
            db_session,
            test_user.id,
            "materials_library_access",
            default=True,
        ) is False
        assert quota_service.has_feature_access(
            db_session, test_user.id, "unknown_feature", default=True,
        ) is True

    def test_materials_access_does_not_infer_from_quantities(self, db_session: Session, test_user):
        plan = SubscriptionPlan(
            name="legacy-materials",
            display_name="Legacy Materials",
            display_name_en="Legacy Materials",
            price_monthly_cents=0,
            price_yearly_cents=0,
            features={"material_decompositions": 2},
            is_active=True,
        )
        now = datetime.utcnow()
        db_session.add(plan)
        db_session.commit()
        db_session.refresh(plan)
        db_session.add(
            UserSubscription(
                user_id=test_user.id,
                plan_id=plan.id,
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
            )
        )
        db_session.commit()

        assert quota_service.has_feature_access(
            db_session,
            test_user.id,
            "materials_library_access",
        ) is False

    def test_empty_pro_features_keep_paid_materials_access(self, db_session: Session, test_user, pro_plan):
        pro_plan.features = {}
        now = datetime.now(UTC)
        db_session.add(pro_plan)
        db_session.add(UserSubscription(
            user_id=test_user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        ))
        db_session.commit()
        assert quota_service.has_feature_access(db_session, test_user.id, "materials_library_access") is True


@pytest.mark.unit
class TestCheckAIConversationQuota:
    """Tests for check_ai_conversation_quota method."""

    def test_check_quota_no_quota_record(
        self, db_session: Session, test_user, free_plan
    ):
        """Test checking quota when user has no quota record."""
        allowed, used, limit = quota_service.check_ai_conversation_quota(
            db_session, test_user.id
        )

        # Should create quota and return defaults
        assert allowed is True
        assert used == 0
        assert limit == 20  # Free plan limit

    def test_check_quota_within_limit(
        self, db_session: Session, test_user, free_plan
    ):
        """Test checking quota when within limit."""
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            ai_conversations_used=10,
            last_reset_at=now,
        )
        db_session.add(quota)
        db_session.commit()

        allowed, used, limit = quota_service.check_ai_conversation_quota(
            db_session, test_user.id
        )

        assert allowed is True
        assert used == 10
        assert limit == 20

    def test_check_quota_at_limit(
        self, db_session: Session, test_user, free_plan
    ):
        """Test checking quota when at limit."""
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            ai_conversations_used=20,
            last_reset_at=now,
        )
        db_session.add(quota)
        db_session.commit()

        allowed, used, limit = quota_service.check_ai_conversation_quota(
            db_session, test_user.id
        )

        assert allowed is False
        assert used == 20
        assert limit == 20

    def test_check_quota_unlimited(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test checking quota for unlimited plan."""
        now = datetime.utcnow()
        subscription = UserSubscription(
            user_id=test_user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db_session.add(subscription)

        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            ai_conversations_used=1000,
            last_reset_at=now,
        )
        db_session.add(quota)
        db_session.commit()

        allowed, used, limit = quota_service.check_ai_conversation_quota(
            db_session, test_user.id
        )

        assert allowed is True
        assert limit == -1  # Unlimited


@pytest.mark.unit
class TestConsumeAIConversation:
    """Tests for consume_ai_conversation method."""

    def test_consume_within_limit(
        self, db_session: Session, test_user, free_plan
    ):
        """Test consuming quota when within limit."""
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            ai_conversations_used=5,
            last_reset_at=now,
        )
        db_session.add(quota)
        db_session.commit()

        result = quota_service.consume_ai_conversation(db_session, test_user.id)

        assert result is True

        # Verify increment
        db_session.refresh(quota)
        assert quota.ai_conversations_used == 6

    def test_consume_at_limit(
        self, db_session: Session, test_user, free_plan
    ):
        """Test consuming quota when at limit."""
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            ai_conversations_used=20,
            last_reset_at=now,
        )
        db_session.add(quota)
        db_session.commit()

        result = quota_service.consume_ai_conversation(db_session, test_user.id)

        assert result is False

        # Verify no increment
        db_session.refresh(quota)
        assert quota.ai_conversations_used == 20

    def test_consume_creates_quota(
        self, db_session: Session, test_user, free_plan
    ):
        """Test that consuming creates quota if not exists."""
        # This should work because check_quota creates the record
        result = quota_service.consume_ai_conversation(db_session, test_user.id)

        assert result is True

        # Verify quota was created
        quota = quota_service.get_user_quota(db_session, test_user.id)
        assert quota is not None
        assert quota.ai_conversations_used == 1


@pytest.mark.unit
class TestReleaseAIConversation:
    """Tests for release_ai_conversation compensation method."""

    def test_release_decrements_usage(
        self, db_session: Session, test_user
    ):
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            ai_conversations_used=3,
            last_reset_at=now,
        )
        db_session.add(quota)
        db_session.commit()

        released = quota_service.release_ai_conversation(db_session, test_user.id)
        assert released is True
        db_session.refresh(quota)
        assert quota.ai_conversations_used == 2

    def test_release_noop_when_usage_zero(
        self, db_session: Session, test_user
    ):
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            ai_conversations_used=0,
            last_reset_at=now,
        )
        db_session.add(quota)
        db_session.commit()

        released = quota_service.release_ai_conversation(db_session, test_user.id)
        assert released is False
        db_session.refresh(quota)
        assert quota.ai_conversations_used == 0

    def test_release_checks_period_reset_before_decrement(
        self,
        db_session: Session,
        test_user,
    ):
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now - timedelta(days=1),
            period_end=now - timedelta(seconds=1),
            ai_conversations_used=2,
            last_reset_at=now - timedelta(days=1),
        )
        db_session.add(quota)
        db_session.commit()

        with patch.object(
            quota_service,
            "_reset_quota_if_needed",
            wraps=quota_service._reset_quota_if_needed,
        ) as mock_reset:
            released = quota_service.release_ai_conversation(db_session, test_user.id)

        assert released is False
        assert mock_reset.call_count == 1


@pytest.mark.unit
class TestCreateDefaultQuota:
    """Tests for create_default_quota method."""

    def test_create_default_quota(self, db_session: Session, test_user):
        """Test creating default quota for user."""
        result = quota_service.create_default_quota(db_session, test_user.id)

        assert result is not None
        assert result.user_id == test_user.id
        assert result.ai_conversations_used == 0
        assert result.period_start is not None
        assert result.period_end is not None
        assert result.last_reset_at is not None

    def test_create_default_quota_period(self, db_session: Session, test_user):
        """Test that default quota has correct period."""
        result = quota_service.create_default_quota(db_session, test_user.id)

        # Daily window should be 24 hours
        delta = result.period_end - result.period_start
        assert delta == timedelta(hours=24)


@pytest.mark.unit
class TestCheckFeatureQuota:
    """Tests for check_feature_quota method."""

    def test_check_feature_quota_valid_type(
        self, db_session: Session, test_user, free_plan
    ):
        """Test checking quota for valid feature type with zero-limit free access."""
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            material_uploads_used=0,
            last_reset_at=now,
            monthly_period_start=now - timedelta(days=1),
            monthly_period_end=now + timedelta(days=1),
        )
        db_session.add(quota)
        db_session.commit()

        allowed, used, limit = quota_service.check_feature_quota(
            db_session, test_user.id, "material_upload"
        )

        assert allowed is False
        assert used == 0
        assert limit == 0

    def test_check_feature_quota_invalid_type(
        self, db_session: Session, test_user, free_plan
    ):
        """Test checking quota for invalid feature type."""
        with pytest.raises(ValueError) as exc_info:
            quota_service.check_feature_quota(
                db_session, test_user.id, "invalid_feature"
            )

        assert "Unknown feature type" in str(exc_info.value)

    def test_check_feature_quota_at_limit(
        self, db_session: Session, test_user, free_plan
    ):
        """Test checking feature quota at zero limit."""
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            material_uploads_used=0,
            last_reset_at=now,
            monthly_period_start=now - timedelta(days=1),
            monthly_period_end=now + timedelta(days=1),
        )
        db_session.add(quota)
        db_session.commit()

        allowed, used, limit = quota_service.check_feature_quota(
            db_session, test_user.id, "material_upload"
        )

        assert allowed is False
        assert used == 0
        assert limit == 0

    def test_check_feature_quota_paid_limit(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test checking feature quota for a paid plan with a finite configured limit."""
        now = datetime.utcnow()
        subscription = UserSubscription(
            user_id=test_user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db_session.add(subscription)

        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            material_decompositions_used=4,
            last_reset_at=now,
            monthly_period_start=now - timedelta(days=1),
            monthly_period_end=now + timedelta(days=1),
        )
        db_session.add(quota)
        db_session.commit()

        allowed, used, limit = quota_service.check_feature_quota(
            db_session, test_user.id, "material_decompose"
        )

        assert allowed is True
        assert used == 4
        assert limit == 5

    def test_check_all_feature_types(
        self, db_session: Session, test_user, free_plan
    ):
        """Test checking quota for all valid feature types."""
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            material_uploads_used=0,
            material_decompositions_used=0,
            skill_creates_used=0,
            inspiration_copies_used=0,
            last_reset_at=now,
        )
        db_session.add(quota)
        db_session.commit()

        for feature_type in FEATURE_QUOTA_MAP.keys():
            allowed, used, limit = quota_service.check_feature_quota(
                db_session, test_user.id, feature_type
            )

            expected_allowed = limit == -1 or limit > used
            assert allowed is expected_allowed
            assert used == 0
            assert limit >= 0 or limit == -1


@pytest.mark.unit
class TestConsumeFeatureQuota:
    """Tests for consume_feature_quota method."""

    def test_consume_feature_within_limit(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test consuming feature quota when within limit."""
        now = datetime.utcnow()
        db_session.add(
            UserSubscription(
                user_id=test_user.id,
                plan_id=pro_plan.id,
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
            )
        )
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            material_decompositions_used=2,
            last_reset_at=now,
            monthly_period_start=now - timedelta(days=1),
            monthly_period_end=now + timedelta(days=1),
        )
        db_session.add(quota)
        db_session.commit()

        result = quota_service.consume_feature_quota(
            db_session, test_user.id, "material_decompose"
        )

        assert result is True

        # Verify increment
        db_session.refresh(quota)
        assert quota.material_decompositions_used == 3

    def test_consume_feature_at_limit(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test consuming feature quota when at limit."""
        now = datetime.utcnow()
        db_session.add(
            UserSubscription(
                user_id=test_user.id,
                plan_id=pro_plan.id,
                status="active",
                current_period_start=now,
                current_period_end=now + timedelta(days=30),
            )
        )
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            material_decompositions_used=5,
            last_reset_at=now,
            monthly_period_start=now - timedelta(days=1),
            monthly_period_end=now + timedelta(days=1),
        )
        db_session.add(quota)
        db_session.commit()

        result = quota_service.consume_feature_quota(
            db_session, test_user.id, "material_decompose"
        )

        assert result is False

        # Verify no increment
        db_session.refresh(quota)
        assert quota.material_decompositions_used == 5

    def test_consume_feature_invalid_type(
        self, db_session: Session, test_user, free_plan
    ):
        """Test consuming feature quota with invalid type."""
        with pytest.raises(ValueError):
            quota_service.consume_feature_quota(
                db_session, test_user.id, "invalid_feature"
            )


@pytest.mark.unit
class TestReleaseFeatureQuota:
    """Tests for release_feature_quota compensation method."""

    def test_release_feature_decrements_monthly_usage(
        self, db_session: Session, test_user
    ):
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            material_decompositions_used=3,
            last_reset_at=now,
            monthly_period_start=now - timedelta(days=1),
            monthly_period_end=now + timedelta(days=1),
        )
        db_session.add(quota)
        db_session.commit()

        released = quota_service.release_feature_quota(
            db_session, test_user.id, "material_decompose"
        )

        assert released is True
        db_session.refresh(quota)
        assert quota.material_decompositions_used == 2

    def test_release_feature_noop_when_usage_zero(
        self, db_session: Session, test_user
    ):
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            material_decompositions_used=0,
            last_reset_at=now,
            monthly_period_start=now - timedelta(days=1),
            monthly_period_end=now + timedelta(days=1),
        )
        db_session.add(quota)
        db_session.commit()

        released = quota_service.release_feature_quota(
            db_session, test_user.id, "material_decompose"
        )

        assert released is False
        db_session.refresh(quota)
        assert quota.material_decompositions_used == 0

    def test_release_feature_invalid_type(
        self, db_session: Session, test_user
    ):
        with pytest.raises(ValueError):
            quota_service.release_feature_quota(
                db_session, test_user.id, "invalid_feature"
            )


@pytest.mark.unit
class TestQuotaReset:
    """Tests for quota reset logic."""

    def test_reset_quota_if_needed_same_day(
        self, db_session: Session, test_user, free_plan
    ):
        """Test that quota is not reset within same day."""
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            ai_conversations_used=15,
            last_reset_at=now,  # Just reset
        )
        db_session.add(quota)
        db_session.commit()

        # Check quota (which triggers reset check)
        quota_service.check_ai_conversation_quota(db_session, test_user.id)

        db_session.refresh(quota)
        # Should not reset
        assert quota.ai_conversations_used == 15

    def test_reset_quota_if_needed_after_24h(
        self, db_session: Session, test_user, free_plan
    ):
        """Test that quota resets after 24 hours."""
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now - timedelta(days=2),
            period_end=now + timedelta(days=28),
            ai_conversations_used=20,
            last_reset_at=now - timedelta(hours=25),  # 25 hours ago
        )
        db_session.add(quota)
        db_session.commit()

        # Check quota (which triggers reset check)
        allowed, used, _ = quota_service.check_ai_conversation_quota(
            db_session, test_user.id
        )

        db_session.refresh(quota)
        # Should have reset
        assert quota.ai_conversations_used == 0
        assert allowed is True
        assert used == 0

    def test_reset_monthly_quota(self, db_session: Session, test_user, free_plan):
        """Test monthly quota reset."""
        now = datetime.utcnow()
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            material_uploads_used=10,
            material_decompositions_used=5,
            skill_creates_used=3,
            inspiration_copies_used=10,
            last_reset_at=now,
            monthly_period_start=now - timedelta(days=31),
            monthly_period_end=now - timedelta(days=1),  # Period ended
        )
        db_session.add(quota)
        db_session.commit()

        # Check feature quota (which triggers monthly reset check)
        quota_service.check_feature_quota(db_session, test_user.id, "material_upload")

        db_session.refresh(quota)
        # Should have reset monthly counters
        assert quota.material_uploads_used == 0
        assert quota.material_decompositions_used == 0
        assert quota.skill_creates_used == 0
        assert quota.inspiration_copies_used == 0


@pytest.mark.unit
class TestBeijingDailyQuota:
    """Daily quota follows Beijing calendar days, independently of other quotas."""

    @pytest.mark.parametrize(
        ("now", "expected_start"),
        [
            (datetime(2026, 10, 5, 15, 59, 59, tzinfo=UTC), datetime(2026, 10, 4, 16, tzinfo=UTC)),
            (datetime(2026, 10, 5, 16, tzinfo=UTC), datetime(2026, 10, 5, 16, tzinfo=UTC)),
            (datetime(2026, 12, 31, 16, tzinfo=UTC), datetime(2026, 12, 31, 16, tzinfo=UTC)),
        ],
    )
    def test_new_quota_uses_beijing_midnight_bounds(
        self, db_session, test_user, monkeypatch, now, expected_start
    ):
        monkeypatch.setattr("services.quota_service.utcnow", lambda: now)

        quota = quota_service.create_default_quota(db_session, test_user.id)

        assert normalize_datetime_to_utc(quota.period_start) == expected_start
        assert normalize_datetime_to_utc(quota.period_end) == expected_start + timedelta(days=1)
        assert quota.ai_conversations_used == 0

    @pytest.mark.parametrize(
        ("last_reset", "now", "expected_reset", "expected_start"),
        [
            # Beijing 23:59:59: not yet the next day.
            (datetime(2026, 10, 5, 15, 58), datetime(2026, 10, 5, 15, 59, 59), False, datetime(2026, 10, 4, 16)),
            # Beijing midnight, only two minutes after the previous reset.
            (datetime(2026, 10, 5, 15, 58), datetime(2026, 10, 5, 16), True, datetime(2026, 10, 5, 16)),
            # UTC midnight is not Beijing midnight.
            (datetime(2026, 10, 4, 23, 59), datetime(2026, 10, 5, 0), False, datetime(2026, 10, 4, 16)),
            # Inactivity must not move the next reset to the time of access.
            (datetime(2026, 10, 1, 3), datetime(2026, 10, 5, 7), True, datetime(2026, 10, 4, 16)),
            # Beijing month and year transitions.
            (datetime(2026, 10, 31, 15, 59), datetime(2026, 10, 31, 16), True, datetime(2026, 10, 31, 16)),
            (datetime(2026, 12, 31, 15, 59), datetime(2026, 12, 31, 16), True, datetime(2026, 12, 31, 16)),
        ],
    )
    @pytest.mark.parametrize("aware_last_reset", [False, True])
    def test_reset_and_legacy_window_normalization(
        self, db_session, test_user, monkeypatch,
        last_reset, now, expected_reset, expected_start, aware_last_reset
    ):
        now = now.replace(tzinfo=UTC)
        monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=last_reset,
            period_end=last_reset + timedelta(hours=24),
            last_reset_at=last_reset,
            ai_conversations_used=20,
            material_uploads_used=4,
            material_decompositions_used=3,
            monthly_period_start=datetime(2026, 10, 1),
            monthly_period_end=datetime(2027, 2, 1),
        )
        db_session.add(quota)
        db_session.commit()
        db_session.refresh(quota)
        if aware_last_reset:
            quota.last_reset_at = last_reset.replace(tzinfo=UTC)
            db_session.flush()

        reset = quota_service._reset_quota_if_needed(db_session, quota)
        db_session.refresh(quota)

        assert reset is expected_reset
        assert quota.ai_conversations_used == (0 if expected_reset else 20)
        assert normalize_datetime_to_utc(quota.period_start) == expected_start.replace(tzinfo=UTC)
        assert normalize_datetime_to_utc(quota.period_end) == expected_start.replace(tzinfo=UTC) + timedelta(days=1)
        if not expected_reset:
            assert normalize_datetime_to_utc(quota.last_reset_at) == last_reset.replace(tzinfo=UTC)
        assert quota.material_uploads_used == 4
        assert quota.material_decompositions_used == 3
        assert quota.monthly_period_start == datetime(2026, 10, 1)
        assert quota.monthly_period_end == datetime(2027, 2, 1)

    def test_stale_second_reset_preserves_new_day_consumption(
        self, db_session, test_user, free_plan, monkeypatch
    ):
        old_reset = datetime(2026, 10, 5, 15, 59)
        monkeypatch.setattr("services.quota_service.utcnow", lambda: datetime(2026, 10, 5, 16, tzinfo=UTC))
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=old_reset,
            period_end=old_reset + timedelta(hours=24),
            last_reset_at=old_reset,
            ai_conversations_used=20,
        )
        db_session.add(quota)
        db_session.commit()
        db_session.refresh(quota)

        with Session(db_session.get_bind(), expire_on_commit=False) as other_session:
            stale_quota = quota_service.get_user_quota(other_session, test_user.id)
            assert stale_quota.ai_conversations_used == 20
            assert quota_service.consume_ai_conversation(db_session, test_user.id) is True

            assert quota_service._reset_quota_if_needed(other_session, stale_quota) is False
            assert stale_quota.ai_conversations_used == 1

        db_session.refresh(quota)
        assert quota.ai_conversations_used == 1

    def test_snapshot_resets_daily_only_and_keeps_membership_expiry(
        self, db_session, test_user, pro_plan, monkeypatch
    ):
        now = datetime(2026, 10, 5, 16, tzinfo=UTC)
        monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
        old_reset = datetime(2026, 10, 5, 15, 59)
        subscription = UserSubscription(
            user_id=test_user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=datetime(2026, 10, 1),
            current_period_end=datetime(2026, 10, 31, 12),
        )
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=old_reset,
            period_end=old_reset + timedelta(hours=24),
            last_reset_at=old_reset,
            ai_conversations_used=20,
            material_decompositions_used=3,
            monthly_period_start=datetime(2026, 10, 1),
            monthly_period_end=datetime(2026, 11, 1),
        )
        db_session.add(subscription)
        db_session.add(quota)
        db_session.commit()

        snapshot = quota_service.get_quota_snapshot(db_session, test_user.id)

        assert snapshot["ai_conversations"]["used"] == 0
        assert snapshot["ai_conversations"]["reset_at"] == datetime(2026, 10, 6, 16, tzinfo=UTC)
        assert snapshot["material_decompositions"]["used"] == 3
        assert snapshot["material_decompositions"]["reset_at"] == datetime(2026, 10, 31, 16, tzinfo=UTC)
        db_session.refresh(subscription)
        assert subscription.current_period_end == datetime(2026, 10, 31, 12)


@pytest.mark.unit
class TestBeijingMonthlyQuota:
    """Monthly counters use Beijing months, not UTC months or rolling windows."""

    @pytest.mark.parametrize(
        ("now", "expected_start", "expected_end"),
        [
            (datetime(2026, 10, 31, 15, 59, 59), datetime(2026, 9, 30, 16), datetime(2026, 10, 31, 16)),
            (datetime(2026, 10, 31, 16), datetime(2026, 10, 31, 16), datetime(2026, 11, 30, 16)),
            (datetime(2026, 12, 31, 16), datetime(2026, 12, 31, 16), datetime(2027, 1, 31, 16)),
        ],
    )
    def test_new_quota_initializes_current_beijing_month(
        self, db_session, test_user, monkeypatch, now, expected_start, expected_end
    ):
        monkeypatch.setattr("services.quota_service.utcnow", lambda: now.replace(tzinfo=UTC))

        quota = quota_service.create_default_quota(db_session, test_user.id)

        assert normalize_datetime_to_utc(quota.monthly_period_start) == expected_start.replace(tzinfo=UTC)
        assert normalize_datetime_to_utc(quota.monthly_period_end) == expected_end.replace(tzinfo=UTC)

    @pytest.mark.parametrize(
        ("now", "old_start", "old_end", "expected_reset", "expected_start", "expected_end"),
        [
            (datetime(2026, 10, 31, 15, 59, 59), datetime(2026, 9, 30, 16), datetime(2026, 10, 31, 16), False, datetime(2026, 9, 30, 16), datetime(2026, 10, 31, 16)),
            (datetime(2026, 10, 31, 16), datetime(2026, 9, 30, 16), datetime(2026, 10, 31, 16), True, datetime(2026, 10, 31, 16), datetime(2026, 11, 30, 16)),
            (datetime(2026, 11, 1), datetime(2026, 10, 31, 16), datetime(2026, 11, 30, 16), False, datetime(2026, 10, 31, 16), datetime(2026, 11, 30, 16)),
            # Legacy UTC months: reset eight hours earlier, or retain same-month use.
            (datetime(2026, 10, 31, 16), datetime(2026, 10, 1), datetime(2026, 11, 1), True, datetime(2026, 10, 31, 16), datetime(2026, 11, 30, 16)),
            (datetime(2026, 10, 5, 3), datetime(2026, 10, 1), datetime(2026, 11, 1), False, datetime(2026, 9, 30, 16), datetime(2026, 10, 31, 16)),
            # Multiple months of inactivity must be resolved in a single access.
            (datetime(2026, 10, 5, 3), datetime(2026, 4, 1), datetime(2026, 5, 1), True, datetime(2026, 9, 30, 16), datetime(2026, 10, 31, 16)),
            (datetime(2026, 12, 31, 16), datetime(2026, 11, 30, 16), datetime(2026, 12, 31, 16), True, datetime(2026, 12, 31, 16), datetime(2027, 1, 31, 16)),
            (datetime(2024, 2, 29, 16), datetime(2024, 1, 31, 16), datetime(2024, 2, 29, 16), True, datetime(2024, 2, 29, 16), datetime(2024, 3, 31, 16)),
        ],
    )
    def test_month_boundaries_and_legacy_periods(
        self, db_session, test_user, monkeypatch,
        now, old_start, old_end, expected_reset, expected_start, expected_end
    ):
        monkeypatch.setattr("services.quota_service.utcnow", lambda: now.replace(tzinfo=UTC))
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=1),
            last_reset_at=now,
            ai_conversations_used=7,
            material_uploads_used=4,
            material_decompositions_used=3,
            inspiration_copies_used=2,
            monthly_period_start=old_start,
            monthly_period_end=old_end,
        )
        db_session.add(quota)
        db_session.commit()

        reset = quota_service._reset_monthly_quota_if_needed(db_session, quota)
        db_session.refresh(quota)

        assert reset is expected_reset
        assert normalize_datetime_to_utc(quota.monthly_period_start) == expected_start.replace(tzinfo=UTC)
        assert normalize_datetime_to_utc(quota.monthly_period_end) == expected_end.replace(tzinfo=UTC)
        assert quota.material_uploads_used == (0 if expected_reset else 4)
        assert quota.material_decompositions_used == (0 if expected_reset else 3)
        assert quota.inspiration_copies_used == (0 if expected_reset else 2)
        assert quota.ai_conversations_used == 7
        assert quota_service._reset_monthly_quota_if_needed(db_session, quota) is False

    def test_missing_monthly_period_retains_usage(self, db_session, test_user, monkeypatch):
        now = datetime(2026, 10, 5, 7, tzinfo=UTC)
        monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=1),
            last_reset_at=now,
            material_decompositions_used=3,
        )
        db_session.add(quota)
        db_session.commit()

        quota_service._reset_monthly_quota_if_needed(db_session, quota)
        db_session.refresh(quota)

        assert quota.material_decompositions_used == 3
        assert normalize_datetime_to_utc(quota.monthly_period_start) == datetime(2026, 9, 30, 16, tzinfo=UTC)
        assert normalize_datetime_to_utc(quota.monthly_period_end) == datetime(2026, 10, 31, 16, tzinfo=UTC)

    @pytest.mark.parametrize("feature", ["material_upload", "material_decompose"])
    def test_stale_second_monthly_reset_preserves_new_month_consumption(
        self, db_session, test_user, pro_plan, monkeypatch, feature
    ):
        now = datetime(2026, 10, 31, 16, tzinfo=UTC)
        monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
        monkeypatch.setattr(quota_service, "get_user_plan", lambda *_, **__: pro_plan)
        quota = UsageQuota(
            user_id=test_user.id,
            period_start=now,
            period_end=now + timedelta(days=1),
            last_reset_at=now,
            monthly_period_start=datetime(2026, 10, 1),
            monthly_period_end=datetime(2026, 11, 1),
            material_uploads_used=5,
            material_decompositions_used=5,
        )
        db_session.add(quota)
        db_session.commit()
        db_session.refresh(quota)

        with Session(db_session.get_bind(), expire_on_commit=False) as other_session:
            stale_quota = quota_service.get_user_quota(other_session, test_user.id)
            assert quota_service.consume_feature_quota(db_session, test_user.id, feature) is True
            assert quota_service._reset_monthly_quota_if_needed(other_session, stale_quota) is False
            _, used_field = FEATURE_QUOTA_MAP[feature]
            assert getattr(stale_quota, used_field) == 1

        db_session.refresh(quota)
        assert getattr(quota, used_field) == 1

    def test_purchase_and_renewal_preserve_monthly_usage(
        self, db_session, test_user, pro_plan, monkeypatch
    ):
        now = datetime(2026, 10, 5, 7, tzinfo=UTC)
        monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
        quota = quota_service.create_default_quota(db_session, test_user.id)
        quota.material_decompositions_used = 3
        db_session.add(quota)
        db_session.commit()

        with patch("services.subscription.subscription_service.utcnow", return_value=now):
            subscription_service.create_user_subscription(db_session, test_user.id, "pro", 30)
            subscription = subscription_service.create_user_subscription(db_session, test_user.id, "pro", 30)
        snapshot = quota_service.get_quota_snapshot(db_session, test_user.id)

        assert snapshot["material_decompositions"]["used"] == 3
        assert snapshot["material_decompositions"]["reset_at"] == datetime(2026, 10, 31, 16, tzinfo=UTC)
        assert normalize_datetime_to_utc(subscription.current_period_end) == now + timedelta(days=60)


@pytest.mark.unit
class TestQuotaReservationPeriods:
    """Refunds must match the period actually charged by the atomic update."""

    @pytest.mark.parametrize(("features", "expected_limit", "allowed"), [({}, -1, True), ({"ai_conversations_per_day": 0}, 0, False)])
    def test_daily_checks_and_reservations_use_the_same_plan_defaults(
        self, db_session, test_user, pro_plan, monkeypatch, features, expected_limit, allowed
    ):
        now = datetime(2026, 10, 5, 7, tzinfo=UTC)
        monkeypatch.setattr("services.quota_service.utcnow", lambda: now)
        pro_plan.features = features
        db_session.add(pro_plan)
        db_session.add(UserSubscription(
            user_id=test_user.id, plan_id=pro_plan.id, status="active",
            current_period_start=now, current_period_end=now + timedelta(days=30),
        ))
        quota = quota_service.create_default_quota(db_session, test_user.id)
        quota.ai_conversations_used = 20
        db_session.add(quota)
        db_session.commit()

        checked, used, limit = quota_service.check_ai_conversation_quota(db_session, test_user.id)
        assert (checked, used, limit) == (allowed, 20, expected_limit)
        reservation = quota_service.reserve_ai_conversation(db_session, test_user.id)
        assert (reservation is not None) is allowed
        snapshot = quota_service.get_quota_snapshot(db_session, test_user.id)
        assert snapshot["ai_conversations"]["limit"] == expected_limit
        assert snapshot["ai_conversations"]["used"] == (21 if allowed else 20)

    def test_previous_day_reservation_cannot_refund_new_day(
        self, db_session, test_user, free_plan, monkeypatch
    ):
        clock = {"now": datetime(2026, 10, 5, 15, 59, 59, tzinfo=UTC)}
        monkeypatch.setattr("services.quota_service.utcnow", lambda: clock["now"])
        old_period = quota_service.reserve_ai_conversation(db_session, test_user.id)
        assert old_period == datetime(2026, 10, 4, 16, tzinfo=UTC)

        clock["now"] = datetime(2026, 10, 5, 16, tzinfo=UTC)
        new_period = quota_service.reserve_ai_conversation(db_session, test_user.id)
        assert new_period == datetime(2026, 10, 5, 16, tzinfo=UTC)
        assert quota_service.release_ai_conversation(db_session, test_user.id, period_start=old_period) is False
        assert quota_service.get_quota_snapshot(db_session, test_user.id)["ai_conversations"]["used"] == 1
        assert quota_service.release_ai_conversation(db_session, test_user.id, period_start=new_period) is True
        assert quota_service.get_quota_snapshot(db_session, test_user.id)["ai_conversations"]["used"] == 0

    @pytest.mark.parametrize("feature", ["material_upload", "material_decompose"])
    def test_previous_month_reservation_cannot_refund_new_month(
        self, db_session, test_user, pro_plan, monkeypatch, feature
    ):
        clock = {"now": datetime(2026, 10, 31, 15, 59, 59, tzinfo=UTC)}
        monkeypatch.setattr("services.quota_service.utcnow", lambda: clock["now"])
        monkeypatch.setattr(quota_service, "get_user_plan", lambda *_, **__: pro_plan)
        old_period = quota_service.reserve_feature_quota(db_session, test_user.id, feature)
        assert old_period == datetime(2026, 9, 30, 16, tzinfo=UTC)

        clock["now"] = datetime(2026, 10, 31, 16, tzinfo=UTC)
        new_period = quota_service.reserve_feature_quota(db_session, test_user.id, feature)
        assert new_period == datetime(2026, 10, 31, 16, tzinfo=UTC)
        assert quota_service.release_feature_quota(db_session, test_user.id, feature, period_start=old_period) is False
        quota = quota_service.get_user_quota(db_session, test_user.id)
        db_session.refresh(quota)
        _, used_field = FEATURE_QUOTA_MAP[feature]
        assert getattr(quota, used_field) == 1
        assert quota_service.release_feature_quota(db_session, test_user.id, feature, period_start=new_period) is True
        db_session.refresh(quota)
        assert getattr(quota, used_field) == 0

    @pytest.mark.parametrize("feature", ["ai_conversation", "material_decompose"])
    def test_denied_reservation_returns_none_without_charging(
        self, db_session, test_user, free_plan, monkeypatch, feature
    ):
        monkeypatch.setattr("services.quota_service.utcnow", lambda: datetime(2026, 10, 5, 7, tzinfo=UTC))
        quota = quota_service.create_default_quota(db_session, test_user.id)
        quota.ai_conversations_used = 20
        db_session.add(quota)
        db_session.commit()

        if feature == "ai_conversation":
            assert quota_service.reserve_ai_conversation(db_session, test_user.id) is None
        else:
            assert quota_service.reserve_feature_quota(db_session, test_user.id, feature) is None
        db_session.refresh(quota)
        assert quota.ai_conversations_used == 20
        assert quota.material_decompositions_used == 0


@pytest.mark.unit
class TestFeatureQuotaMap:
    """Tests for FEATURE_QUOTA_MAP configuration."""

    def test_feature_quota_map_completeness(self):
        """Test that all expected features are in the map."""
        expected_features = [
            "material_upload",
            "material_decompose",
            "skill_create",
            "inspiration_copy",
        ]

        for feature in expected_features:
            assert feature in FEATURE_QUOTA_MAP

    def test_feature_quota_map_field_names(self):
        """Test that mapped field names are valid UsageQuota attributes."""
        for _feature, (_limit_field, used_field) in FEATURE_QUOTA_MAP.items():
            # Verify fields exist in UsageQuota model
            assert hasattr(UsageQuota, used_field), f"Missing field: {used_field}"
