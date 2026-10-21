"""Account-level interface preferences that should follow the user across devices."""
from datetime import datetime

from sqlmodel import Field, SQLModel

from config.datetime_utils import utcnow

SUPPORTED_UI_LANGUAGES = ("zh", "en")


class UserPreference(SQLModel, table=True):
    __tablename__ = "user_preference"

    user_id: str = Field(primary_key=True, foreign_key="user.id")
    # Interface language the user last chose or signed up with; None until known.
    ui_language: str | None = Field(default=None, max_length=8)
    updated_at: datetime = Field(default_factory=utcnow)
