"""Upgrade funnel event model for monetization entry analytics."""

from datetime import datetime

from sqlalchemy import JSON, Column, Index
from sqlmodel import Field, SQLModel

from config.datetime_utils import utcnow

from .utils import generate_uuid

UPGRADE_FUNNEL_ACTION_EXPOSE = "expose"
UPGRADE_FUNNEL_ACTION_CLICK = "click"
UPGRADE_FUNNEL_ACTION_CONVERSION = "conversion"

UPGRADE_FUNNEL_ACTIONS = {
    UPGRADE_FUNNEL_ACTION_EXPOSE,
    UPGRADE_FUNNEL_ACTION_CLICK,
    UPGRADE_FUNNEL_ACTION_CONVERSION,
}

UPGRADE_FUNNEL_EVENT_NAME_BY_ACTION = {
    UPGRADE_FUNNEL_ACTION_EXPOSE: "upgrade_entry_expose",
    UPGRADE_FUNNEL_ACTION_CLICK: "upgrade_entry_click",
    UPGRADE_FUNNEL_ACTION_CONVERSION: "upgrade_entry_conversion",
}

UPGRADE_FUNNEL_SURFACES = {"modal", "toast", "page", "badge", "card"}
UPGRADE_FUNNEL_CTAS = {"primary", "secondary", "direct"}


class UpgradeFunnelEvent(SQLModel, table=True):
    """Per-user upgrade entry funnel event."""

    __tablename__ = "upgrade_funnel_event"
    __table_args__ = (
        Index(
            "ix_upgrade_funnel_event_source_action_occurred",
            "source",
            "action",
            "occurred_at",
        ),
        Index(
            "ix_upgrade_funnel_event_user_source_created",
            "user_id",
            "source",
            "created_at",
        ),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)

    event_name: str = Field(max_length=64, index=True)
    action: str = Field(max_length=24, index=True)
    source: str = Field(max_length=128, index=True)
    surface: str = Field(max_length=24)
    cta: str | None = Field(default=None, max_length=24)
    destination: str | None = Field(default=None, max_length=128)

    event_metadata: dict = Field(default_factory=dict, sa_column=Column(JSON))
    occurred_at: datetime = Field(default_factory=utcnow, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
