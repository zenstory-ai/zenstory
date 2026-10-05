"""
Subscription Service - Manages user subscriptions.

Handles subscription creation, renewal, and status management.
"""
from datetime import datetime, timedelta

from sqlmodel import Session, select

from config.datetime_utils import utcnow
from models.entities import User
from models.subscription import SubscriptionHistory, SubscriptionPlan, UserSubscription
from services.subscription.defaults import (
    DEFAULT_FREE_PLAN_DISPLAY_NAME,
    DEFAULT_FREE_PLAN_DISPLAY_NAME_EN,
    clone_default_free_features,
)


class SubscriptionService:
    """Service for subscription operations."""

    def _lock_user_for_subscription_mutation(
        self, session: Session, user_id: str
    ) -> User:
        """Serialize subscription mutations on the durable user row."""
        user = session.exec(
            select(User)
            .where(User.id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()
        if not user:
            raise ValueError("User not found")
        return user

    def _get_user_subscription_for_update(
        self, session: Session, user_id: str
    ) -> UserSubscription | None:
        """Read the current subscription under lock, bypassing stale ORM state."""
        return session.exec(
            select(UserSubscription)
            .where(UserSubscription.user_id == user_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).first()

    def get_user_subscription(
        self, session: Session, user_id: str
    ) -> UserSubscription | None:
        """Get user's active subscription."""
        return session.exec(
            select(UserSubscription).where(UserSubscription.user_id == user_id)
        ).first()

    def get_effective_paid_plan(
        self, session: Session, user_id: str
    ) -> SubscriptionPlan | None:
        """The paid plan the user is entitled to right now, if any."""
        subscription = self.get_user_subscription(session, user_id)
        if not subscription or subscription.status != "active":
            return None
        period_end = subscription.current_period_end
        if period_end.tzinfo is None:
            from datetime import UTC
            period_end = period_end.replace(tzinfo=UTC)
        if period_end <= utcnow():
            return None
        plan = self.get_plan_by_id(session, subscription.plan_id)
        if not plan or plan.name == "free":
            return None
        return plan

    def get_plan_by_name(self, session: Session, plan_name: str) -> SubscriptionPlan | None:
        """Get subscription plan by name."""
        return session.exec(
            select(SubscriptionPlan).where(SubscriptionPlan.name == plan_name)
        ).first()

    def get_plan_by_id(self, session: Session, plan_id: str) -> SubscriptionPlan | None:
        """Get subscription plan by ID."""
        return session.exec(
            select(SubscriptionPlan).where(SubscriptionPlan.id == plan_id)
        ).first()

    def _get_or_create_free_plan(
        self, session: Session, *, commit: bool = True
    ) -> SubscriptionPlan:
        """Ensure free plan exists and return it."""
        free_plan = self.get_plan_by_name(session, "free")
        if free_plan:
            return free_plan

        now = utcnow()
        free_plan = SubscriptionPlan(
            name="free",
            display_name=DEFAULT_FREE_PLAN_DISPLAY_NAME,
            display_name_en=DEFAULT_FREE_PLAN_DISPLAY_NAME_EN,
            price_monthly_cents=0,
            price_yearly_cents=0,
            features=clone_default_free_features(),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(free_plan)
        if commit:
            session.commit()
            session.refresh(free_plan)
        else:
            session.flush()
        return free_plan

    def ensure_user_subscription_and_quota(
        self,
        session: Session,
        user_id: str,
        source: str = "system_backfill",
        commit: bool = True,
    ) -> dict[str, bool]:
        """
        Ensure a user has both a subscription record and a quota record.

        This method is used for:
        - registration / OAuth bootstrap
        - legacy data backfill on login/admin operations
        """
        self._lock_user_for_subscription_mutation(session, user_id)

        created_subscription = False
        created_quota = False

        if not self._get_user_subscription_for_update(session, user_id):
            self._get_or_create_free_plan(session, commit=False)
            self.create_user_subscription(
                session=session,
                user_id=user_id,
                plan_name="free",
                duration_days=36500,  # 100 years for long-lived free tier
                metadata={"source": source},
                commit=False,
            )
            created_subscription = True

        from services.quota_service import quota_service

        if not quota_service.get_user_quota(session, user_id):
            quota_service.create_default_quota(session, user_id, commit=False)
            created_quota = True

        if created_subscription or created_quota:
            if commit:
                session.commit()
            else:
                session.flush()

        return {
            "created_subscription": created_subscription,
            "created_quota": created_quota,
        }

    def create_user_subscription(
        self,
        session: Session,
        user_id: str,
        plan_name: str,
        duration_days: int,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> UserSubscription:
        """
        Create or extend a user subscription.

        Args:
            session: Database session
            user_id: User ID
            plan_name: Plan name (e.g., "pro")
            duration_days: Duration in days
            metadata: Optional metadata for history

        Returns:
            Created or updated UserSubscription
        """
        self._lock_user_for_subscription_mutation(session, user_id)

        plan = self.get_plan_by_name(session, plan_name)
        if not plan:
            raise ValueError(f"Plan '{plan_name}' not found")

        now = utcnow()
        existing_sub = self._get_user_subscription_for_update(session, user_id)

        if existing_sub:
            old_plan_id = existing_sub.plan_id
            is_upgrade = old_plan_id != plan.id
            period_end = existing_sub.current_period_end

            # Handle both timezone-aware and naive datetimes for comparison
            if period_end.tzinfo is None:
                from datetime import UTC
                period_end = period_end.replace(tzinfo=UTC)

            if is_upgrade:
                # Plan upgrade should start immediately and not inherit previous plan duration.
                new_start = now
                new_end = now + timedelta(days=duration_days)
            else:
                # Same-plan renewal extends only a running subscription. A cancelled
                # or expired row may still carry a future period_end (admins revoke
                # by status), and renewing must not resurrect those revoked days.
                if existing_sub.status == "active" and period_end >= now:
                    new_start = existing_sub.current_period_start
                    renewal_base = existing_sub.current_period_end
                else:
                    new_start = now
                    renewal_base = now
                new_end = renewal_base + timedelta(days=duration_days)

            existing_sub.plan_id = plan.id
            existing_sub.current_period_start = new_start
            existing_sub.current_period_end = new_end
            existing_sub.status = "active"
            existing_sub.cancel_at_period_end = False
            existing_sub.updated_at = now

            session.add(existing_sub)

            # Log history
            action = "upgraded" if is_upgrade else "renewed"
            self._log_history(
                session, user_id, action, plan.name,
                new_start, new_end, metadata
            )

            if commit:
                session.commit()
                session.refresh(existing_sub)
            else:
                session.flush()
            return existing_sub
        else:
            # Create new subscription
            period_end = now + timedelta(days=duration_days)

            subscription = UserSubscription(
                user_id=user_id,
                plan_id=plan.id,
                status="active",
                current_period_start=now,
                current_period_end=period_end,
                cancel_at_period_end=False
            )
            session.add(subscription)
            session.flush()  # Get ID without committing

            # Log history
            self._log_history(
                session, user_id, "created", plan.name,
                now, period_end, metadata
            )

            if commit:
                session.commit()
                session.refresh(subscription)
            else:
                session.flush()
            return subscription

    def ensure_user_subscription(
        self,
        session: Session,
        user_id: str,
        plan_name: str = "free",
        duration_days: int = 36500,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> UserSubscription:
        """
        Ensure a user has a subscription record.

        Returns existing subscription if present; otherwise creates one.
        """
        self._lock_user_for_subscription_mutation(session, user_id)
        existing_sub = self._get_user_subscription_for_update(session, user_id)
        if existing_sub:
            return existing_sub
        return self.create_user_subscription(
            session=session,
            user_id=user_id,
            plan_name=plan_name,
            duration_days=duration_days,
            metadata=metadata,
            commit=commit,
        )

    def upgrade_subscription(
        self,
        session: Session,
        user_id: str,
        new_plan_name: str,
        duration_days: int,
        metadata: dict | None = None,
        commit: bool = True,
    ) -> UserSubscription:
        """
        Upgrade or renew a subscription.

        This service uses the same period calculation logic as create_user_subscription,
        so admin upgrades and redemption upgrades remain consistent.
        """
        return self.create_user_subscription(
            session=session,
            user_id=user_id,
            plan_name=new_plan_name,
            duration_days=duration_days,
            metadata=metadata,
            commit=commit,
        )

    def cancel_subscription(
        self,
        session: Session,
        user_id: str,
        immediately: bool = False
    ) -> UserSubscription | None:
        """
        Cancel a user subscription.

        Args:
            session: Database session
            user_id: User ID
            immediately: If True, cancel immediately; otherwise cancel at period end

        Returns:
            Updated UserSubscription or None if not found
        """
        self._lock_user_for_subscription_mutation(session, user_id)
        subscription = self._get_user_subscription_for_update(session, user_id)
        if not subscription:
            return None

        now = utcnow()

        if immediately:
            subscription.status = "cancelled"
            subscription.current_period_end = now
            action = "cancelled"
            end_date = now
        else:
            subscription.cancel_at_period_end = True
            action = "cancelled"
            end_date = subscription.current_period_end

        subscription.updated_at = now
        session.add(subscription)

        plan = self.get_plan_by_id(session, subscription.plan_id)
        plan_name = plan.name if plan else "unknown"

        self._log_history(
            session, user_id, action, plan_name,
            subscription.current_period_start, end_date,
            {"immediately": immediately}
        )

        session.commit()
        session.refresh(subscription)
        return subscription

    def check_subscription_status(
        self, session: Session, user_id: str
    ) -> tuple[bool, str, datetime | None]:
        """
        Check if user has an active subscription.

        Returns: (is_active, plan_name, expires_at)
        """
        self._lock_user_for_subscription_mutation(session, user_id)
        subscription = self._get_user_subscription_for_update(session, user_id)

        if not subscription:
            return (False, "free", None)

        if subscription.status != "active":
            return (False, "free", subscription.current_period_end)

        now = utcnow()
        # Handle both timezone-aware and naive datetimes for comparison
        period_end = subscription.current_period_end
        if period_end.tzinfo is None:
            from datetime import UTC
            period_end = period_end.replace(tzinfo=UTC)
        if period_end < now:
            # Mark as expired
            subscription.status = "expired"
            subscription.updated_at = now
            session.add(subscription)

            plan = self.get_plan_by_id(session, subscription.plan_id)
            plan_name = plan.name if plan else "unknown"

            self._log_history(
                session, user_id, "expired", plan_name,
                subscription.current_period_start, subscription.current_period_end
            )

            session.commit()
            return (False, "free", subscription.current_period_end)

        plan = self.get_plan_by_id(session, subscription.plan_id)
        plan_name = plan.name if plan else "free"

        return (True, plan_name, subscription.current_period_end)

    def _log_history(
        self,
        session: Session,
        user_id: str,
        action: str,
        plan_name: str,
        start_date: datetime,
        end_date: datetime | None = None,
        event_metadata: dict | None = None
    ) -> SubscriptionHistory:
        """Log a subscription history event."""
        history = SubscriptionHistory(
            user_id=user_id,
            action=action,
            plan_name=plan_name,
            start_date=start_date,
            end_date=end_date,
            event_metadata=event_metadata or {}
        )
        session.add(history)
        return history


# Singleton instance
subscription_service = SubscriptionService()
