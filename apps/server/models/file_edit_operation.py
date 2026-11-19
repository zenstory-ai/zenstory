"""Immutable anchors for recoverable AI file edits."""

from datetime import datetime

from sqlalchemy import Index
from sqlmodel import Field, SQLModel

from config.datetime_utils import utcnow

from .utils import generate_uuid


class FileEditOperation(SQLModel, table=True):
    """One recoverable AI mutation without duplicating manuscript bodies."""

    __tablename__ = "file_edit_operation"
    __table_args__ = (
        Index("ix_file_edit_operation_owner_file_created", "user_id", "project_id", "file_id", "created_at"),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    file_id: str = Field(foreign_key="file.id", index=True)
    kind: str = Field(index=True)
    before_version_id: str = Field(foreign_key="file_version.id", index=True)
    after_version_id: str = Field(foreign_key="file_version.id", index=True)
    before_title: str
    after_title: str
    before_order: int
    after_order: int
    after_updated_at: datetime
    after_content_sha256: str = Field(max_length=64)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    undone_at: datetime | None = Field(default=None, index=True)
    undo_version_id: str | None = Field(default=None, foreign_key="file_version.id", index=True)
