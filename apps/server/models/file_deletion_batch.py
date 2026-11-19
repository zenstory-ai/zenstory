"""Immutable records for recoverable file-tree deletions."""

from datetime import datetime

from sqlmodel import Field, SQLModel

from config.datetime_utils import utcnow

from .utils import generate_uuid


class FileDeletionBatch(SQLModel, table=True):
    """The exact live rows changed to deleted by one user action."""

    __tablename__ = "file_deletion_batch"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    source: str = Field(max_length=32)
    root_ids_json: str
    items_json: str
    item_count: int
    target_hash: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    restored_at: datetime | None = Field(default=None, index=True)
