"""
Tests for SubscriptionService.

Unit tests for the subscription management service, covering:
- User subscription retrieval
- Subscription creation and extension
- Subscription cancellation
- Subscription status checking
- History logging
"""
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session

from models import User
from models.subscription import (
    SubscriptionPlan,
    UserSubscription,
    SubscriptionHistory,
    UsageQuota,
)
from services.subscription.subscription_service import subscription_service


@pytest.fixture
def test_user(db_session: Session):
    """Create a test user for subscription testing."""
    user = User(
        email="subtest@example.com",
        username="subtest",
        hashed_password="hashed_password",
        name="Subscription Test User",
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
            "ai_conversations_per_day": 10,
            "max_projects": 3,
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
            "ai_conversations_per_day": -1,
            "max_projects": -1,
        },
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.unit
class TestGetUserSubscription:
    """Tests for get_user_subscription method."""

    def test_get_user_subscription_no_subscription(self, db_session: Session, test_user):
        """Test getting subscription for user without subscription."""
        result = subscription_service.get_user_subscription(db_session, test_user.id)

        assert result is None

    def test_get_user_subscription_with_subscription(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test getting subscription for user with subscription."""
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

        result = subscription_service.get_user_subscription(db_session, test_user.id)

        assert result is not None
        assert result.user_id == test_user.id
        assert result.plan_id == pro_plan.id
        assert result.status == "active"

    def test_get_user_subscription_unique_per_user(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test that each user can only have one subscription."""
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

        # Getting subscription should return the same one
        result = subscription_service.get_user_subscription(db_session, test_user.id)
        assert result.id == subscription.id


@pytest.mark.unit
class TestGetPlanByName:
    """Tests for get_plan_by_name method."""

    def test_get_plan_by_name_exists(self, db_session: Session, free_plan):
        """Test getting an existing plan by name."""
        result = subscription_service.get_plan_by_name(db_session, "free")

        assert result is not None
        assert result.name == "free"
        assert result.display_name == "Free"

    def test_get_plan_by_name_not_exists(self, db_session: Session):
        """Test getting a non-existent plan."""
        result = subscription_service.get_plan_by_name(db_session, "nonexistent")

        assert result is None

    def test_get_plan_by_name_pro(self, db_session: Session, pro_plan):
        """Test getting pro plan by name."""
        result = subscription_service.get_plan_by_name(db_session, "pro")

        assert result is not None
        assert result.name == "pro"
        assert result.price_monthly_cents == 2900


@pytest.mark.unit
class TestGetPlanById:
    """Tests for get_plan_by_id method."""

    def test_get_plan_by_id_exists(self, db_session: Session, free_plan):
        """Test getting an existing plan by ID."""
        result = subscription_service.get_plan_by_id(db_session, free_plan.id)

        assert result is not None
        assert result.id == free_plan.id
        assert result.name == "free"

    def test_get_plan_by_id_not_exists(self, db_session: Session):
        """Test getting a non-existent plan by ID."""
        result = subscription_service.get_plan_by_id(db_session, "nonexistent-id")

        assert result is None


@pytest.mark.unit
class TestCreateUserSubscription:
    """Tests for create_user_subscription method."""

    def test_create_new_subscription(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test creating a new subscription for user without one."""
        result = subscription_service.create_user_subscription(
            db_session, test_user.id, "pro", 30
        )

        assert result is not None
        assert result.user_id == test_user.id
        assert result.plan_id == pro_plan.id
        assert result.status == "active"
        assert result.current_period_end > result.current_period_start

    def test_create_subscription_creates_history(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test that creating subscription logs history."""
        subscription_service.create_user_subscription(
            db_session, test_user.id, "pro", 30
        )

        # Check history was created
        from sqlmodel import select
        history = db_session.exec(
            select(SubscriptionHistory).where(
                SubscriptionHistory.user_id == test_user.id
            )
        ).first()

        assert history is not None
        assert history.action == "created"
        assert history.plan_name == "pro"

    def test_create_subscription_invalid_plan(self, db_session: Session, test_user):
        """Test creating subscription with invalid plan name."""
        with pytest.raises(ValueError) as exc_info:
            subscription_service.create_user_subscription(
                db_session, test_user.id, "invalid_plan", 30
            )

        assert "not found" in str(exc_info.value).lower()

    def test_extend_existing_subscription(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test extending an existing active subscription."""
        now = datetime.utcnow()
        existing = UserSubscription(
            user_id=test_user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db_session.add(existing)
        db_session.commit()

        original_end = existing.current_period_end

        result = subscription_service.create_user_subscription(
            db_session, test_user.id, "pro", 30
        )

        assert result is not None
        # Should extend from original end date
        assert result.current_period_end > original_end

    def test_extend_expired_subscription(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test extending an expired subscription."""
        now = datetime.utcnow()
        expired = UserSubscription(
            user_id=test_user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now - timedelta(days=60),
            current_period_end=now - timedelta(days=30),  # Expired
        )
        db_session.add(expired)
        db_session.commit()

        result = subscription_service.create_user_subscription(
            db_session, test_user.id, "pro", 30
        )

        assert result is not None
        assert result.status == "active"
        # Should start from now, not from expired end
        assert result.current_period_start >= now - timedelta(minutes=1)

    def test_upgrade_subscription(
        self, db_session: Session, test_user, free_plan, pro_plan
    ):
        """Test upgrading a subscription to a higher tier."""
        now = datetime.utcnow()
        existing = UserSubscription(
            user_id=test_user.id,
            plan_id=free_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
        db_session.add(existing)
        db_session.commit()

        result = subscription_service.create_user_subscription(
            db_session, test_user.id, "pro", 30
        )

        assert result is not None
        assert result.plan_id == pro_plan.id

        # Check history shows upgrade
        from sqlmodel import select
        history = db_session.exec(
            select(SubscriptionHistory).where(
                SubscriptionHistory.user_id == test_user.id,
                SubscriptionHistory.action == "upgraded",
            )
        ).first()

        assert history is not None
        assert history.plan_name == "pro"

    def test_create_subscription_with_metadata(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test creating subscription with metadata."""
        metadata = {"source": "redemption_code", "code_id": "test-code-id"}

        result = subscription_service.create_user_subscription(
            db_session, test_user.id, "pro", 30, metadata=metadata
        )

        assert result is not None

        # Check history has metadata
        from sqlmodel import select
        history = db_session.exec(
            select(SubscriptionHistory).where(
                SubscriptionHistory.user_id == test_user.id
            )
        ).first()

        assert history is not None
        assert history.event_metadata == metadata


@pytest.mark.unit
class TestCancelSubscription:
    """Tests for cancel_subscription method."""

    def test_cancel_subscription_at_period_end(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test cancelling subscription at period end."""
        now = datetime.utcnow()
        subscription = UserSubscription(
            user_id=test_user.id,
            plan_id=pro_plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            cancel_at_period_end=False,
        )
        db_session.add(subscription)
        db_session.commit()

        result = subscription_service.cancel_subscription(
            db_session, test_user.id, immediately=False
        )

        assert result is not None
        assert result.cancel_at_period_end is True
        assert result.status == "active"  # Still active until period end

    def test_cancel_subscription_immediately(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test cancelling subscription immediately."""
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

        result = subscription_service.cancel_subscription(
            db_session, test_user.id, immediately=True
        )

        assert result is not None
        assert result.status == "cancelled"
        assert result.current_period_end <= datetime.utcnow()

    def test_cancel_subscription_no_subscription(self, db_session: Session, test_user):
        """Test cancelling when user has no subscription."""
        result = subscription_service.cancel_subscription(db_session, test_user.id)

        assert result is None

    def test_cancel_subscription_creates_history(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test that cancelling subscription logs history."""
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

        subscription_service.cancel_subscription(
            db_session, test_user.id, immediately=True
        )

        # Check history was created
        from sqlmodel import select
        history = db_session.exec(
            select(SubscriptionHistory).where(
                SubscriptionHistory.user_id == test_user.id,
                SubscriptionHistory.action == "cancelled",
            )
        ).first()

        assert history is not None
        assert history.plan_name == "pro"


@pytest.mark.unit
class TestCheckSubscriptionStatus:
    """Tests for check_subscription_status method."""

    def test_check_status_no_subscription(self, db_session: Session, test_user):
        """Test checking status for user without subscription."""
        is_active, plan_name, expires_at = subscription_service.check_subscription_status(
            db_session, test_user.id
        )

        assert is_active is False
        assert plan_name == "free"
        assert expires_at is None

    def test_check_status_active_subscription(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test checking status for active subscription."""
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

        is_active, plan_name, expires_at = subscription_service.check_subscription_status(
            db_session, test_user.id
        )

        assert is_active is True
        assert plan_name == "pro"
        assert expires_at is not None

    def test_check_status_expired_subscription(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test checking status for expired subscription."""
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

        is_active, plan_name, expires_at = subscription_service.check_subscription_status(
            db_session, test_user.id
        )

        assert is_active is False
        assert plan_name == "free"
        assert expires_at is not None

    def test_check_status_marks_expired(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test that checking status updates expired subscription."""
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

        subscription_service.check_subscription_status(db_session, test_user.id)

        # Refresh to see updated status
        db_session.refresh(subscription)
        assert subscription.status == "expired"

    def test_check_status_cancelled_subscription(
        self, db_session: Session, test_user, pro_plan
    ):
        """Test checking status for cancelled subscription."""
        now = datetime.utcnow()
        subscription = UserSubscription(
            user_id=test_user.id,
            plan_id=pro_plan.id,
            status="cancelled",
            current_period_start=now - timedelta(days=60),
            current_period_end=now + timedelta(days=30),
        )
        db_session.add(subscription)
        db_session.commit()

        is_active, plan_name, expires_at = subscription_service.check_subscription_status(
            db_session, test_user.id
        )

        assert is_active is False
        assert plan_name == "free"


@pytest.mark.unit
class TestLogHistory:
    """Tests for _log_history internal method."""

    def test_log_history_basic(self, db_session: Session, test_user):
        """Test basic history logging."""
        now = datetime.utcnow()
        end = now + timedelta(days=30)

        result = subscription_service._log_history(
            db_session,
            test_user.id,
            "created",
            "pro",
            now,
            end,
        )

        assert result is not None
        assert result.user_id == test_user.id
        assert result.action == "created"
        assert result.plan_name == "pro"
        assert result.start_date == now
        assert result.end_date == end

    def test_log_history_with_metadata(self, db_session: Session, test_user):
        """Test history logging with metadata."""
        now = datetime.utcnow()
        metadata = {"source": "test", "amount": 2900}

        result = subscription_service._log_history(
            db_session,
            test_user.id,
            "renewed",
            "pro",
            now,
            None,
            metadata,
        )

        assert result is not None
        assert result.event_metadata == metadata

    def test_log_history_various_actions(self, db_session: Session, test_user):
        """Test logging various action types."""
        now = datetime.utcnow()
        actions = ["created", "upgraded", "renewed", "expired", "cancelled", "migrated"]

        for action in actions:
            result = subscription_service._log_history(
                db_session,
                test_user.id,
                action,
                "pro",
                now,
                None,
            )
            assert result is not None
            assert result.action == action
