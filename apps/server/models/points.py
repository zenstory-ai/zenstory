"""
Points and check-in models for gamification system.

Defines SQLModel entities for:
- PointsTransaction: Points earning/spending history with expiry tracking
- CheckInRecord: Daily check-in records with streak tracking
"""

from datetime import date, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from .utils import generate_uuid


class PointsTransaction(SQLModel, table=True):
    """
    Points transaction model with expiry tracking.

    Tracks all points earning and spending with:
    - Balance snapshots for audit
    - Expiry management for earned points (12 months)
    - Source tracking for different earning/spending types
    """

    __tablename__ = "points_transaction"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)

    # Transaction details
    amount: int  # Positive=earning, negative=spending
    balance_after: int  # Balance snapshot after transaction
    transaction_type: str  # check_in, check_in_streak, referral, skill_contribution, inspiration_contribution, profile_complete, redeem_pro, unlock_material_slot, unlock_skill_slot, unlock_inspiration_copy
    source_id: str | None = None  # Related entity ID (referral_id, skill_id, etc.)
    description: str | None = None

    # Expiry management (only for earning transactions)
    expires_at: datetime | None = None  # Expires after 12 months
    is_expired: bool = Field(default=False, index=True)
    expired_at: datetime | None = None

    created_at: datetime = Field(default_factory=datetime.utcnow)


class CheckInRecord(SQLModel, table=True):
    """
    Check-in record model with streak tracking.

    Tracks daily check-ins with:
    - Beijing calendar date for consistent daily tracking
    - Streak days count at check-in time
    - Points earned for this check-in
    """

    __tablename__ = "check_in_record"
    __table_args__ = (
        UniqueConstraint("user_id", "check_in_date", name="uq_check_in_record_user_date"),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)

    # Check-in date (Beijing calendar date; legacy rows may contain the UTC date)
    check_in_date: date = Field(index=True)

    # Streak days count at this check-in
    streak_days: int = Field(default=1)

    # Points earned for this check-in
    points_earned: int

    created_at: datetime = Field(default_factory=datetime.utcnow)
