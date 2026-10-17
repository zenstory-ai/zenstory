"""The single operator-managed announcement shown in the writing app."""

from datetime import datetime

from sqlalchemy import CheckConstraint
from sqlmodel import Field, SQLModel

from config.datetime_utils import utcnow

ANNOUNCEMENT_CONFIG_ID = "current"


class AnnouncementConfig(SQLModel, table=True):
    """Singleton configuration; dismissal state deliberately stays client-side."""

    __tablename__ = "announcement_config"
    __table_args__ = (
        CheckConstraint("id = 'current'", name="ck_announcement_config_singleton"),
    )

    id: str = Field(default=ANNOUNCEMENT_CONFIG_ID, primary_key=True, max_length=16)
    title: str = Field(default="", max_length=80)
    description: str = Field(default="", max_length=240)
    action_label: str | None = Field(default=None, max_length=24)
    action_url: str | None = Field(default=None, max_length=2048)
    enabled: bool = Field(default=False)
    # Content version: changing visibility alone intentionally does not advance it.
    version: int = Field(default=0, ge=0)
    updated_at: datetime = Field(default_factory=utcnow)
    updated_by: str | None = Field(default=None, foreign_key="user.id")
