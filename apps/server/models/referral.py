"""
Referral system models (DORMANT).

Retained strictly for Alembic metadata compatibility and historical data
preservation (no DROP TABLE). Runtime code no longer reads or writes:
- InviteCode
- Referral
- UserReward
- UserStats
"""

from datetime import datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from .utils import generate_uuid

# Referral status constants
REFERRAL_STATUS_PENDING = "PENDING"        # Invitee registered but not completed requirements
REFERRAL_STATUS_COMPLETED = "COMPLETED"    # Invitee completed requirements
REFERRAL_STATUS_REWARDED = "REWARDED"      # Rewards distributed

# Reward type constants
REWARD_TYPE_POINTS = "points"              # Loyalty points


class InviteCode(SQLModel, table=True):
    """
    Invite code model.

    Stores invitation codes that users can share to invite new users.
    Each code has usage limits and optional expiration.
    """

    __tablename__ = "invite_code"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    code: str = Field(
        unique=True,
        index=True,
        description="Unique invite code in format XXXX-XXXX"
    )
    owner_id: str = Field(
        foreign_key="user.id",
        index=True,
        description="User who owns this invite code"
    )
    max_uses: int = Field(
        default=3,
        ge=1,
        description="Maximum number of times this code can be used"
    )
    current_uses: int = Field(
        default=0,
        ge=0,
        description="Number of times this code has been used"
    )
    is_active: bool = Field(
        default=True,
        index=True,
        description="Whether this invite code is active"
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiration timestamp"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this code was created"
    )


class Referral(SQLModel, table=True):
    """
    Referral relationship model.

    Tracks the relationship between an inviter and invitee.
    Includes fraud detection fields and reward status.
    """

    __tablename__ = "referral"
    __table_args__ = (
        Index("ix_referral_created_at", "created_at"),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    inviter_id: str = Field(
        foreign_key="user.id",
        index=True,
        description="User who sent the invitation"
    )
    invitee_id: str = Field(
        foreign_key="user.id",
        unique=True,
        index=True,
        description="User who was invited (unique per invitee)"
    )
    invite_code_id: str = Field(
        foreign_key="invite_code.id",
        index=True,
        description="Invite code used for this referral"
    )
    status: str = Field(
        default=REFERRAL_STATUS_PENDING,
        index=True,
        description="Referral status: PENDING, COMPLETED, REWARDED"
    )
    inviter_rewarded: bool = Field(
        default=False,
        description="Whether the inviter has received their reward"
    )
    invitee_rewarded: bool = Field(
        default=False,
        description="Whether the invitee has received their reward"
    )
    # Fraud detection fields
    device_fingerprint: str | None = Field(
        default=None,
        description="Device fingerprint for fraud detection"
    )
    ip_address: str | None = Field(
        default=None,
        description="IP address at registration time"
    )
    fraud_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Fraud risk score (0.0-1.0)"
    )
    # Timestamps
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When the referral was created (invitee registered)"
    )
    completed_at: datetime | None = Field(
        default=None,
        description="When the referral requirements were completed"
    )
    rewarded_at: datetime | None = Field(
        default=None,
        description="When rewards were distributed"
    )


class UserReward(SQLModel, table=True):
    """
    User reward model.

    Stores rewards given to users from various sources, primarily referrals.
    Supports different reward types with optional expiration.
    """

    __tablename__ = "user_reward"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(
        foreign_key="user.id",
        index=True,
        description="User who received this reward"
    )
    reward_type: str = Field(
        index=True,
        description="Type of reward: points, pro_trial, credits"
    )
    amount: int = Field(
        ge=1,
        description="Amount/quantity of the reward"
    )
    source: str = Field(
        default="referral",
        index=True,
        description="Source of the reward (referral, promotion, etc.)"
    )
    referral_id: str | None = Field(
        default=None,
        foreign_key="referral.id",
        description="Related referral if source is referral"
    )
    expires_at: datetime | None = Field(
        default=None,
        description="Optional expiration timestamp for the reward"
    )
    is_used: bool = Field(
        default=False,
        index=True,
        description="Whether this reward has been used"
    )
    used_at: datetime | None = Field(
        default=None,
        description="When this reward was used"
    )
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When this reward was created"
    )


class UserStats(SQLModel, table=True):
    """
    User statistics model for referral tracking.

    Aggregates invitation and reward statistics for each user.
    Updated whenever a referral status changes.
    """

    __tablename__ = "user_stats"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(
        foreign_key="user.id",
        unique=True,
        index=True,
        description="User these stats belong to"
    )
    total_invites: int = Field(
        default=0,
        ge=0,
        description="Total number of invitations sent"
    )
    successful_invites: int = Field(
        default=0,
        ge=0,
        description="Number of completed referrals"
    )
    total_points: int = Field(
        default=0,
        ge=0,
        description="Total points earned from referrals"
    )
    available_points: int = Field(
        default=0,
        ge=0,
        description="Points available for redemption"
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        description="When these stats were last updated"
    )
