"""
Subscription models for user billing and quota management.

Defines SQLModel entities for:
- SubscriptionPlan: Available subscription tiers (e.g. free, pro, max)
- UserSubscription: User's active subscription
- RedemptionCode: Gift/redemption codes for subscription upgrades
- UsageQuota: Period-based usage tracking
- SubscriptionHistory: Subscription change history
- AdminAuditLog: Admin action audit trail
"""

from datetime import datetime

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from .utils import generate_uuid


class SubscriptionPlan(SQLModel, table=True):
    """
    Subscription plan model.

    Defines available subscription tiers with pricing and features.
    Tiers: "free", "pro" (extensible)
    """

    __tablename__ = "subscription_plan"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    name: str = Field(unique=True, index=True)  # e.g. "free", "pro"
    display_name: str
    display_name_en: str | None = None
    price_monthly_cents: int = 0
    price_yearly_cents: int = 0
    # Stripe fields (future)
    stripe_price_id_monthly: str | None = None
    stripe_price_id_yearly: str | None = None
    features: dict = Field(default_factory=dict, sa_column=Column(JSON))
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class UserSubscription(SQLModel, table=True):
    """
    User subscription model.

    Tracks each user's active subscription and billing period.
    One subscription per user (user_id is unique).
    """

    __tablename__ = "user_subscription"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", unique=True, index=True)
    plan_id: str = Field(foreign_key="subscription_plan.id")
    status: str = "active"  # "active", "expired", "cancelled"
    current_period_start: datetime
    current_period_end: datetime
    cancel_at_period_end: bool = False
    # Stripe fields (future)
    stripe_subscription_id: str | None = None
    stripe_customer_id: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class RedemptionCode(SQLModel, table=True):
    """
    Redemption code model for subscription upgrades.

    Supports:
    - Single-use codes (one redemption)
    - Multi-use codes (max_uses limit)
    Codes can grant Pro tier access for a specified duration.
    """

    __tablename__ = "redemption_code"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    code: str = Field(unique=True, index=True)
    code_type: str = "single_use"  # "single_use", "multi_use"
    tier: str = Field(foreign_key="subscription_plan.name")
    duration_days: int
    max_uses: int | None = None
    current_uses: int = 0
    created_by: str = Field(foreign_key="user.id")
    is_active: bool = True
    expires_at: datetime | None = None
    notes: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    redeemed_by: list = Field(default_factory=list, sa_column=Column(JSON))


class UsageQuota(SQLModel, table=True):
    """
    Usage quota model for period-based tracking.

    Tracks AI conversation usage within a billing period.
    One quota per user (user_id is unique).
    """

    __tablename__ = "usage_quota"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", unique=True, index=True)
    period_start: datetime
    period_end: datetime
    ai_conversations_used: int = 0
    last_reset_at: datetime = Field(default_factory=datetime.utcnow)
    # 月度功能使用量
    material_uploads_used: int = Field(default=0)
    material_decompositions_used: int = Field(default=0)
    skill_creates_used: int = Field(default=0)
    inspiration_copies_used: int = Field(default=0)
    # 素材拆书免费试用：一个账号一次，不随月度重置；平台原因失败退还时清空
    material_trial_used_at: datetime | None = Field(default=None)
    # 月度周期
    monthly_period_start: datetime | None = Field(default=None)
    monthly_period_end: datetime | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class SubscriptionHistory(SQLModel, table=True):
    """
    Subscription history model for audit trail.

    Records all subscription changes:
    - created: New subscription
    - upgraded: Plan upgrade
    - renewed: Subscription renewal
    - expired: Subscription expiration
    - cancelled: Subscription cancellation
    - migrated: Data migration
    """

    __tablename__ = "subscription_history"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    action: str  # "created", "upgraded", "renewed", "expired", "cancelled", "migrated"
    plan_name: str
    start_date: datetime
    end_date: datetime | None = None
    event_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AdminAuditLog(SQLModel, table=True):
    """
    Admin audit log model for administrative actions.

    Tracks all admin operations for security and compliance:
    - Code generation/management
    - Subscription modifications
    - User management actions
    """

    __tablename__ = "admin_audit_log"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    admin_user_id: str = Field(foreign_key="user.id", index=True)
    action: str
    resource_type: str = Field(index=True)  # "code", "subscription", etc.
    resource_id: str | None = None
    old_value: dict | None = Field(default=None, sa_column=Column(JSON))
    new_value: dict | None = Field(default=None, sa_column=Column(JSON))
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
