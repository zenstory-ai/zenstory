"""
Quota Service - Manages usage quotas and limits.

All quota operations use atomic database updates to prevent race conditions.
"""
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, col, func, select

from config.datetime_utils import BEIJING_TIMEZONE, beijing_day_bounds, normalize_datetime_to_utc, utcnow
from models.entities import Project, User
from models.skill import UserSkill
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.subscription.defaults import (
    DEFAULT_FREE_PLAN_DISPLAY_NAME,
    DEFAULT_FREE_PLAN_DISPLAY_NAME_EN,
    clone_default_free_features,
    resolve_plan_feature,
)

# Feature type to (limit_field, used_field) mapping
FEATURE_QUOTA_MAP = {
    "material_upload": ("material_uploads", "material_uploads_used"),
    "material_decompose": ("material_decompositions", "material_decompositions_used"),
    "skill_create": ("custom_skills", "skill_creates_used"),
    "inspiration_copy": ("inspiration_copies_monthly", "inspiration_copies_used"),
}

FEATURE_RESPONSE_KEY_MAP = {
    "material_upload": "material_uploads",
    "material_decompose": "material_decompositions",
    "skill_create": "skill_creates",
    "inspiration_copy": "inspiration_copies",
}

FEATURE_ACCESS_MAP = {
    "material_upload": "materials_library_access",
    "material_decompose": "materials_library_access",
}

# Default free tier features (canonical fallback)
DEFAULT_FREE_TIER_FEATURES = clone_default_free_features()


class QuotaService:
    """Service for checking and enforcing usage quotas."""

    def get_user_quota(self, session: Session, user_id: str) -> UsageQuota | None:
        """Get user's current usage quota."""
        return session.exec(
            select(UsageQuota).where(UsageQuota.user_id == user_id)
        ).first()

    def get_user_plan(self, session: Session, user_id: str, *, commit: bool = True) -> SubscriptionPlan:
        """
        Get user's current subscription plan. Defaults to free plan.
        """
        subscription = session.exec(
            select(UserSubscription).where(UserSubscription.user_id == user_id)
        ).first()

        if subscription and subscription.status == "active":
            now = utcnow()
            period_end = subscription.current_period_end
            if period_end.tzinfo is None and now.tzinfo is not None:
                from datetime import UTC
                period_end = period_end.replace(tzinfo=UTC)

            # Active-but-expired subscriptions should not keep paid plan quotas.
            if period_end <= now:
                self._expire_if_still_lapsed(session, subscription.id, now, commit=commit)
            else:
                plan = session.exec(
                    select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id)
                ).first()
                if plan:
                    return plan

        if subscription and subscription.status != "active":
            plan = session.exec(
                select(SubscriptionPlan).where(SubscriptionPlan.id == subscription.plan_id)
            ).first()
            if plan and plan.name == "free":
                return plan

        # Default to free plan with hardcoded fallback
        free_plan = session.exec(
            select(SubscriptionPlan).where(SubscriptionPlan.name == "free")
        ).first()

        if free_plan:
            return free_plan

        # Fallback: create in-memory default free plan (not persisted)
        return self._create_default_free_plan()

    def _expire_if_still_lapsed(
        self, session: Session, subscription_id: str, now: datetime, *, commit: bool = True
    ) -> None:
        """
        Mark a lapsed subscription expired without clobbering a concurrent renewal.

        The WHERE re-checks status and period end at write time, so a payment that
        extended the period after our read leaves the row untouched (no lost update).
        """
        cutoff = now.replace(tzinfo=None)
        result = session.exec(
            update(UserSubscription)
            .where(
                UserSubscription.id == subscription_id,
                UserSubscription.status == "active",
                UserSubscription.current_period_end <= cutoff,
            )
            .values(status="expired", updated_at=now)
        )
        if not commit:
            session.flush()
        elif result.rowcount:
            session.commit()
        else:
            session.rollback()

    def get_plan_feature(
        self, plan: SubscriptionPlan | None, key: str, fallback: object = 0
    ) -> object:
        """A plan's feature value, defaulting to that plan's preset (not free's)."""
        if plan is None:
            return resolve_plan_feature("free", None, key, fallback)
        return resolve_plan_feature(plan.name, plan.features, key, fallback)

    def get_ai_conversation_limit(self, plan: SubscriptionPlan | None) -> int:
        """Daily AI conversation limit (-1 = unlimited) for checks, charges and quota views."""
        return int(self.get_plan_feature(plan, "ai_conversations_per_day"))

    def count_custom_skills(self, session: Session, user_id: str) -> int:
        return int(
            session.exec(
                select(func.count())
                .select_from(UserSkill)
                .where(UserSkill.user_id == user_id)
            ).one()
        )

    def check_custom_skill_slot(
        self, session: Session, user_id: str
    ) -> tuple[bool, int, int]:
        """
        Check the custom-skill cap against skills the user currently owns.

        The limit is a count of owned skills, so deleting one frees a slot. Before
        counting, this takes a write lock on the user's quota row (PostgreSQL row
        lock / SQLite writer lock) that is held until the caller commits the new
        skill or rolls back, so concurrent creates cannot both pass the check.
        The monthly ``skill_creates_used`` counter is bumped under the same lock
        and kept only as an activity metric.

        Returns: (allowed, owned_count, limit)
        """
        plan = self.get_user_plan(session, user_id)
        limit = int(self.get_plan_feature(plan, "custom_skills"))
        quota = self._get_or_create_quota(session, user_id)
        self._reset_monthly_quota_if_needed(session, quota)

        session.exec(
            update(UsageQuota)
            .where(UsageQuota.user_id == user_id)
            .values(skill_creates_used=UsageQuota.skill_creates_used + 1)
        )
        used = self.count_custom_skills(session, user_id)
        if limit == -1:
            return (True, used, -1)
        return (used < limit, used, limit)

    def check_project_limit(
        self,
        session: Session,
        user_id: str,
        *,
        for_creation: bool = False,
    ) -> tuple[bool, int, int]:
        """
        Check whether a user can create a new (active) project.

        Creation callers serialize on the durable owner until their commit or
        rollback. Checking-only callers retain the existing read/reset behavior.

        Returns: (allowed, used, limit)
        - allowed: True if user can proceed
        - used: current count of active (non-deleted) projects
        - limit: plan max_projects (-1 for unlimited)
        """
        if for_creation:
            if session.get_bind().dialect.name == "postgresql":
                owner_id = session.exec(
                    select(User.id).where(User.id == user_id).with_for_update(key_share=True)
                ).one_or_none()
            else:
                # SQLite serializes writers across the database, including
                # different owners. Do not mutate the owner's timestamp.
                result = session.exec(
                    update(User).where(col(User.id) == user_id).values(id=User.id)
                    .execution_options(synchronize_session=False)
                )
                owner_id = user_id if result.rowcount else None
            if owner_id is None:
                raise RuntimeError("Project creation owner is missing")

        plan = self.get_user_plan(session, user_id, commit=False) if for_creation else self.get_user_plan(session, user_id)
        fallback_limit = DEFAULT_FREE_TIER_FEATURES.get("max_projects", 3)
        raw_limit = self.get_plan_feature(plan, "max_projects", fallback_limit)
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError):
            limit = int(fallback_limit)

        used = int(
            session.exec(
                select(func.count())
                .select_from(Project)
                .where(
                    Project.owner_id == user_id,
                    Project.is_deleted.is_(False),
                )
            ).one()
        )

        if limit == -1:
            return (True, used, -1)

        return (used < limit, used, limit)

    def get_plan_limits(self, plan: SubscriptionPlan) -> dict:
        """Get limits dict from plan features."""
        return plan.features

    def has_feature_access(
        self,
        session: Session,
        user_id: str,
        feature_key: str,
        *,
        default: bool = False,
    ) -> bool:
        """Return whether a user has access to a boolean-style entitlement."""
        plan = self.get_user_plan(session, user_id)
        raw_value = self.get_plan_feature(plan, feature_key, default)

        if isinstance(raw_value, bool):
            return raw_value
        if isinstance(raw_value, (int, float)):
            return raw_value != 0
        if isinstance(raw_value, str):
            normalized = raw_value.strip().lower()
            if normalized in {"true", "1", "yes", "on"}:
                return True
            if normalized in {"false", "0", "no", "off"}:
                return False

        if raw_value is None:
            return default

        return default

    def _create_default_free_plan(self) -> SubscriptionPlan:
        """
        Create an in-memory default free plan as fallback.

        Used when no free plan exists in the database (e.g., fresh install,
        migration not run). This ensures the quota system always has a valid
        plan to work with.

        Returns:
            SubscriptionPlan: A non-persisted free plan with default features.
        """
        now = utcnow()
        return SubscriptionPlan(
            id="default-free-plan",
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

    def check_ai_conversation_quota(
        self, session: Session, user_id: str
    ) -> tuple[bool, int, int]:
        """
        Check if user can start an AI conversation.

        Returns: (allowed, used, limit)
        - allowed: True if user can proceed
        - used: number of conversations used today
        - limit: daily limit (-1 for unlimited)
        """
        plan = self.get_user_plan(session, user_id)
        limit = self.get_ai_conversation_limit(plan)

        # Reset quota if needed
        quota = self._get_or_create_quota(session, user_id)
        self._reset_quota_if_needed(session, quota)

        # Unlimited
        if limit == -1:
            return (True, quota.ai_conversations_used, -1)

        allowed = quota.ai_conversations_used < limit
        return (allowed, quota.ai_conversations_used, limit)

    def get_quota_snapshot(
        self,
        session: Session,
        user_id: str,
        plan: SubscriptionPlan | None = None,
    ) -> dict:
        """
        Get all quota metrics in one pass.

        This method is intended for read APIs so we only perform reset checks once
        per request, instead of calling multiple quota-check methods that each do
        their own reset logic.
        """
        plan = plan or self.get_user_plan(session, user_id)

        quota = self._get_or_create_quota(session, user_id)
        self._reset_quota_if_needed(session, quota)
        self._reset_monthly_quota_if_needed(session, quota)

        # Refresh to ensure latest values after possible reset commits.
        session.refresh(quota)

        ai_limit = self.get_ai_conversation_limit(plan)
        snapshot = {
            "ai_conversations": {
                "used": quota.ai_conversations_used,
                "limit": ai_limit,
                "reset_at": normalize_datetime_to_utc(quota.period_end),
            }
        }

        for feature_type, (limit_field, used_field) in FEATURE_QUOTA_MAP.items():
            response_key = FEATURE_RESPONSE_KEY_MAP[feature_type]
            snapshot[response_key] = {
                "used": getattr(quota, used_field, 0),
                "limit": self.get_plan_feature(plan, limit_field),
                "reset_at": normalize_datetime_to_utc(quota.monthly_period_end) if quota.monthly_period_end else None,
            }
        # Custom skills cap what the user owns now, not monthly creations.
        snapshot["skill_creates"] = {
            **snapshot["skill_creates"],
            "used": self.count_custom_skills(session, user_id),
            "reset_at": None,
        }

        return snapshot

    def get_admin_quota_view(self, session: Session, user_id: str) -> dict:
        """
        One user's quota for the admin console, read through get_quota_snapshot.

        Plan resolution (a lapsed paid period falls back to free), limits and the
        lazy Beijing day/month resets are the ones enforcement uses, so the admin
        sees the numbers the user's next request is checked against.
        """
        plan = self.get_user_plan(session, user_id)
        snapshot = self.get_quota_snapshot(session, user_id, plan=plan)
        return {
            "plan_name": plan.name,
            "plan_display_name": plan.display_name,
            "plan_display_name_en": plan.display_name_en,
            "ai_conversations": snapshot["ai_conversations"],
            "material_decompositions": snapshot["material_decompositions"],
            "inspiration_copies": snapshot["inspiration_copies"],
            "custom_skills": snapshot["skill_creates"],
        }

    def get_current_month_totals(self, session: Session) -> dict:
        """
        Monthly counters summed over quota rows whose period is the current
        Beijing month.

        Rows still holding an earlier month (not yet lazily reset) are skipped, so
        old usage never leaks into this month's totals.
        """
        now = utcnow()
        period_start = self._get_month_start(now)
        period_end = self._get_next_month_start(now)
        now_naive = now.replace(tzinfo=None)  # the columns store naive UTC
        totals = session.exec(
            select(
                func.coalesce(func.sum(UsageQuota.material_decompositions_used), 0),
                func.coalesce(func.sum(UsageQuota.inspiration_copies_used), 0),
                func.coalesce(func.sum(UsageQuota.skill_creates_used), 0),
            ).where(
                UsageQuota.monthly_period_start <= now_naive,
                UsageQuota.monthly_period_end > now_naive,
            )
        ).one()
        return {
            "period_start": period_start,
            "period_end": period_end,
            "material_decompositions": int(totals[0] or 0),
            "inspiration_copies": int(totals[1] or 0),
            "skills_created": int(totals[2] or 0),
        }

    def consume_ai_conversation(self, session: Session, user_id: str) -> bool:
        """Consume a daily unit; use reserve_ai_conversation when it may be refunded."""
        return self.reserve_ai_conversation(session, user_id) is not None

    def reserve_ai_conversation(self, session: Session, user_id: str) -> datetime | None:
        """
        Increment AI conversation count and return the charged day's UTC start.

        The period comes from the same atomic UPDATE as the charge, rather than
        the request/response timestamp. None means the quota was exceeded.
        """
        plan = self.get_user_plan(session, user_id)
        limit = self.get_ai_conversation_limit(plan)

        quota = self._get_or_create_quota(session, user_id)
        self._reset_quota_if_needed(session, quota)

        query = (
            update(UsageQuota)
            .where(UsageQuota.user_id == user_id)
            .values(ai_conversations_used=UsageQuota.ai_conversations_used + 1)
            .returning(UsageQuota.period_start)
        )
        if limit != -1:
            query = query.where(UsageQuota.ai_conversations_used < limit)

        period_start = session.exec(query).scalar_one_or_none()
        if period_start is None:
            session.rollback()
            return None

        session.commit()
        return normalize_datetime_to_utc(period_start)

    def release_ai_conversation(
        self, session: Session, user_id: str, *, period_start: datetime | None = None
    ) -> bool:
        """
        Decrement AI conversation count atomically as a compensation action.

        Returns True if one unit was refunded, False when there is nothing to refund
        or the target row does not exist. A reservation's period_start prevents
        a failed request from refunding another day's usage after midnight.
        """
        quota = self._get_or_create_quota(session, user_id)
        self._reset_quota_if_needed(session, quota)
        session.refresh(quota)

        query = (
            update(UsageQuota)
            .where(UsageQuota.user_id == user_id)
            .where(UsageQuota.ai_conversations_used > 0)
            .values(ai_conversations_used=UsageQuota.ai_conversations_used - 1)
        )
        if period_start is not None:
            query = query.where(UsageQuota.period_start == normalize_datetime_to_utc(period_start))

        result = session.exec(query)
        if result.rowcount == 0:
            session.rollback()
            return False

        session.commit()
        return True

    def create_default_quota(
        self, session: Session, user_id: str, commit: bool = True
    ) -> UsageQuota:
        """Create default usage quota for a new user."""
        now = utcnow()
        period_start, period_end = beijing_day_bounds(now)

        quota = UsageQuota(
            user_id=user_id,
            period_start=period_start,
            period_end=period_end,
            ai_conversations_used=0,
            last_reset_at=now,
            monthly_period_start=self._get_month_start(now),
            monthly_period_end=self._get_next_month_start(now),
        )
        session.add(quota)
        if commit:
            session.commit()
            session.refresh(quota)
        else:
            session.flush()
        return quota

    def ensure_default_quota(
        self, session: Session, user_id: str, commit: bool = True
    ) -> UsageQuota:
        """Get existing quota or create a default one if missing."""
        existing = self.get_user_quota(session, user_id)
        if existing:
            return existing
        return self.create_default_quota(session, user_id, commit=commit)

    def _get_or_create_quota(self, session: Session, user_id: str, *, commit: bool = True) -> UsageQuota:
        """Get existing quota or create new one."""
        quota = self.get_user_quota(session, user_id)
        if not quota:
            quota = self.create_default_quota(session, user_id, commit=commit)
        return quota

    def _reset_quota_if_needed(self, session: Session, quota: UsageQuota) -> bool:
        """
        Reset daily quota at Beijing midnight, lazily on the next access.

        Same-day legacy rolling windows are normalized without losing usage.
        Returns True only when this call reset the daily counter.
        """
        now = utcnow()
        period_start, period_end = beijing_day_bounds(now)

        last_reset = quota.last_reset_at
        if normalize_datetime_to_utc(last_reset) >= period_start:
            if (
                normalize_datetime_to_utc(quota.period_start) != period_start
                or normalize_datetime_to_utc(quota.period_end) != period_end
            ):
                # Only repair timestamps; never overwrite a concurrent consumption.
                session.exec(
                    update(UsageQuota)
                    .where(UsageQuota.id == quota.id, UsageQuota.last_reset_at == last_reset)
                    .values(period_start=period_start, period_end=period_end)
                    .execution_options(synchronize_session=False)
                )
                session.commit()
                session.refresh(quota)
            return False

        # A stale reader must not reset usage consumed after another request reset it.
        result = session.exec(
            update(UsageQuota)
            .where(UsageQuota.id == quota.id, UsageQuota.last_reset_at < period_start)
            .values(
                ai_conversations_used=0,
                last_reset_at=now,
                period_start=period_start,
                period_end=period_end,
            )
            .execution_options(synchronize_session=False)
        )
        session.commit()
        session.refresh(quota)
        return result.rowcount > 0

    def check_feature_quota(
        self, session: Session, user_id: str, feature_type: str
    ) -> tuple[bool, int, int]:
        """
        Check if user can use a feature within quota limits.

        Args:
            feature_type: One of "material_upload", "material_decompose",
                         "skill_create", "inspiration_copy"

        Returns: (allowed, used, limit)
            - allowed: True if user can proceed
            - used: current usage count
            - limit: quota limit (-1 for unlimited)
        """
        if feature_type not in FEATURE_QUOTA_MAP:
            raise ValueError(f"Unknown feature type: {feature_type}")

        limit_field, used_field = FEATURE_QUOTA_MAP[feature_type]

        # Get plan and limit
        plan = self.get_user_plan(session, user_id)
        limit = self.get_plan_feature(plan, limit_field)

        if feature_type == "skill_create":
            # Read-only view; creation paths must use check_custom_skill_slot.
            used = self.count_custom_skills(session, user_id)
            return (True, used, -1) if limit == -1 else (used < limit, used, limit)

        # Get or create quota, with monthly reset check
        quota = self._get_or_create_quota(session, user_id)
        self._reset_monthly_quota_if_needed(session, quota)

        # Get current usage
        used = getattr(quota, used_field, 0)

        # Unlimited
        if limit == -1:
            return (True, used, -1)

        allowed = used < limit
        return (allowed, used, limit)

    def consume_feature_quota(
        self, session: Session, user_id: str, feature_type: str
    ) -> bool:
        """Consume a monthly unit; use reserve_feature_quota when it may be refunded."""
        return self.reserve_feature_quota(session, user_id, feature_type) is not None

    def reserve_feature_quota(
        self, session: Session, user_id: str, feature_type: str, *, commit: bool = True
    ) -> datetime | None:
        """
        Atomically consume a feature unit and return its charged month's UTC start.

        None means the quota was exceeded. Persist this period for asynchronous
        work so that a later failure cannot refund a different month's usage.
        With commit=False the caller owns commit/rollback of reservation and job.
        """
        if feature_type not in FEATURE_QUOTA_MAP:
            raise ValueError(f"Unknown feature type: {feature_type}")

        limit_field, used_field = FEATURE_QUOTA_MAP[feature_type]
        used_column = getattr(UsageQuota, used_field)

        plan = self.get_user_plan(session, user_id, commit=commit)
        limit = self.get_plan_feature(plan, limit_field)

        quota = self._get_or_create_quota(session, user_id, commit=commit)
        self._reset_monthly_quota_if_needed(session, quota, commit=commit)

        query = (
            update(UsageQuota)
            .where(UsageQuota.user_id == user_id)
            .values(**{used_field: used_column + 1})
            .returning(UsageQuota.monthly_period_start)
        )
        if limit != -1:
            query = query.where(used_column < limit)

        period_start = session.exec(query).scalar_one_or_none()
        if period_start is None:
            if commit:
                session.rollback()
            return None

        if commit:
            session.commit()
        return normalize_datetime_to_utc(period_start)

    def release_feature_quota(
        self, session: Session, user_id: str, feature_type: str,
        *, period_start: datetime | None = None, consumed_at: datetime | None = None,
        commit: bool = True,
    ) -> bool:
        """
        Decrement a monthly feature quota atomically as a compensation action.

        Returns True if one unit was refunded, False when there is nothing to
        refund or the target row does not exist. A reservation's period_start
        matches the actual charged month. consumed_at is a fallback for older
        callers without a recorded reservation period. With commit=False this
        never commits or rolls back; the caller atomically settles its job too.
        """
        if feature_type not in FEATURE_QUOTA_MAP:
            raise ValueError(f"Unknown feature type: {feature_type}")

        _, used_field = FEATURE_QUOTA_MAP[feature_type]
        used_column = getattr(UsageQuota, used_field)

        quota = self.get_user_quota(session, user_id)
        if quota is None:
            return False
        self._reset_monthly_quota_if_needed(session, quota, commit=commit)
        session.refresh(quota)

        query = (
            update(UsageQuota)
            .where(UsageQuota.user_id == user_id)
            .where(used_column > 0)
            .values(**{used_field: used_column - 1})
        )
        if period_start is not None:
            query = query.where(UsageQuota.monthly_period_start == normalize_datetime_to_utc(period_start))
        elif consumed_at is not None:
            query = query.where(UsageQuota.monthly_period_start == self._get_month_start(consumed_at))

        result = session.exec(query)
        if result.rowcount == 0:
            if commit:
                session.rollback()
            return False

        if commit:
            session.commit()
        return True

    # ------------------------------------------------------------------
    # 素材拆书免费试用（一个账号一次，见 material_settings.MATERIAL_TRIAL_*）
    # ------------------------------------------------------------------
    def material_trial_status(self, session: Session, user_id: str) -> dict[str, Any]:
        """{"enabled", "available", "used", "max_chapters"}；有素材库权益的账号不需要试用。"""
        from config.material_settings import material_settings

        quota = self.get_user_quota(session, user_id)
        used = bool(quota and quota.material_trial_used_at is not None)
        enabled = bool(material_settings.MATERIAL_TRIAL_ENABLED)
        has_access = self.has_feature_access(session, user_id, "materials_library_access")
        return {
            "enabled": enabled,
            "available": enabled and not used and not has_access,
            "used": used,
            "max_chapters": int(material_settings.MATERIAL_TRIAL_MAX_CHAPTERS),
        }

    def reserve_material_trial(self, session: Session, user_id: str, *, commit: bool = True) -> bool:
        """原子地占用这个账号唯一的一次试用；已经用过返回 False。"""
        self._get_or_create_quota(session, user_id, commit=commit)
        result = session.exec(
            update(UsageQuota)
            .where(UsageQuota.user_id == user_id, UsageQuota.material_trial_used_at.is_(None))
            .values(material_trial_used_at=utcnow())
        )
        if result.rowcount != 1:
            if commit:
                session.rollback()
            return False
        if commit:
            session.commit()
        return True

    def release_material_trial(self, session: Session, user_id: str, *, commit: bool = True) -> bool:
        """平台原因失败时退还试用（清空占用时间）；没有可退的返回 False。"""
        result = session.exec(
            update(UsageQuota)
            .where(UsageQuota.user_id == user_id, UsageQuota.material_trial_used_at.is_not(None))
            .values(material_trial_used_at=None)
        )
        if result.rowcount != 1:
            if commit:
                session.rollback()
            return False
        if commit:
            session.commit()
        return True

    def _reset_monthly_quota_if_needed(
        self, session: Session, quota: UsageQuota, *, commit: bool = True
    ) -> bool:
        """
        Reset monthly quota at Beijing midnight on the first of the month.

        Jump directly to the current month after inactivity. Normalize legacy
        UTC windows without clearing same-month usage. Missing period metadata
        is initialized without discarding usage whose month is unknown.
        """
        now = utcnow()

        period_start = self._get_month_start(now)
        period_end = self._get_next_month_start(now)
        old_start, old_end = quota.monthly_period_start, quota.monthly_period_end
        if (
            old_start is not None and normalize_datetime_to_utc(old_start) == period_start
            and old_end is not None and normalize_datetime_to_utc(old_end) == period_end
        ):
            return False

        reset_needed = (
            (old_start is not None and normalize_datetime_to_utc(old_start) < period_start)
            or (old_end is not None and normalize_datetime_to_utc(old_end) <= normalize_datetime_to_utc(now))
        )
        values = {"monthly_period_start": period_start, "monthly_period_end": period_end}
        if reset_needed:
            values.update(
                material_uploads_used=0,
                material_decompositions_used=0,
                skill_creates_used=0,
                inspiration_copies_used=0,
            )
        # Compare-and-set: only one request may reset a given old window.
        result = session.exec(
            update(UsageQuota)
            .where(
                UsageQuota.id == quota.id,
                UsageQuota.monthly_period_start == old_start,
                UsageQuota.monthly_period_end == old_end,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        if commit:
            session.commit()
        else:
            session.flush()
        session.refresh(quota)
        return reset_needed and result.rowcount > 0

    def _get_month_start(self, date: datetime) -> datetime:
        """Get Beijing month start as a UTC timestamp (including naive UTC input)."""
        local_start = normalize_datetime_to_utc(date).astimezone(BEIJING_TIMEZONE).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        return local_start.astimezone(UTC)

    def _get_next_month_start(self, date: datetime) -> datetime:
        """Get next Beijing month start as a UTC timestamp."""
        local_start = self._get_month_start(date).astimezone(BEIJING_TIMEZONE)
        if local_start.month == 12:
            local_end = local_start.replace(year=local_start.year + 1, month=1)
        else:
            local_end = local_start.replace(month=local_start.month + 1)
        return local_end.astimezone(UTC)


# Singleton instance
quota_service = QuotaService()
