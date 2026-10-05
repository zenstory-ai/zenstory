"""Payment order persistence for server-authoritative checkout and fulfillment."""

from datetime import datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from .utils import generate_uuid


class PaymentOrder(SQLModel, table=True):
    """Immutable purchase snapshot plus payment and fulfillment state."""

    __tablename__ = "payment_order"
    __table_args__ = (
        Index("ix_payment_order_created_at", "created_at"),
        Index("ix_payment_order_status_created_at", "status", "created_at"),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    out_trade_no: str = Field(unique=True, index=True, max_length=32)
    trade_no: str | None = Field(default=None, unique=True, index=True, max_length=64)
    user_id: str = Field(foreign_key="user.id", index=True)
    plan_name: str = Field(max_length=32)
    plan_display_name: str
    product_name: str = Field(max_length=100)
    cycle: str = Field(max_length=16)
    amount_cents: int
    duration_days: int
    payment_method: str = Field(max_length=16)
    status: str = Field(default="pending", max_length=16, index=True)
    fulfillment_status: str = Field(default="pending", max_length=16, index=True)
    failure_reason: str | None = Field(default=None, max_length=100)
    # Where the buyer entered checkout (pricing CTA, quota modal, ...); copied into
    # SubscriptionHistory.event_metadata.upgrade_source when the order is fulfilled.
    upgrade_source: str | None = Field(default=None, max_length=64)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    paid_at: datetime | None = None
    fulfilled_at: datetime | None = None
