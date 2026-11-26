"""Cold archive storage for historical ``chat_message`` rows older than the hot retention window.

Keeps the hot ``chat_message`` table and its TOAST storage lean (default 7 days)
while preserving 100% of historical user chat readability, feedback submission,
edit/delete tool-card undo/restore state, and returning-user conversation replay.
"""

from __future__ import annotations

import weakref
from datetime import datetime
from typing import Any

from sqlalchemy import Column, ForeignKey, Index, String, Text
from sqlmodel import Field, SQLModel

from .utils import generate_uuid


class ChatMessageArchive(SQLModel, table=True):
    """Compacted cold archive row mirroring ``chat_message`` plus ``archived_at``."""

    __tablename__ = "chat_message_archive"
    __table_args__ = (
        Index(
            "ix_chat_message_archive_session_created_at_id",
            "session_id",
            "created_at",
            "id",
        ),
        Index("ix_chat_message_archive_archived_at", "archived_at"),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    session_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("chat_session.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    role: str
    content: str = Field(sa_column=Column(Text, nullable=False))
    tool_calls: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    tool_call_id: str | None = None
    reasoning_content: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    message_metadata: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    # Naive UTC, matching ChatMessage.created_at.
    created_at: datetime = Field(default_factory=datetime.utcnow)
    archived_at: datetime = Field(default_factory=datetime.utcnow)


# Engines where chat_message_archive has already been observed. Never caches False
# so late table creation (e.g. create_all or migration) is picked up immediately.
_ENGINES_WITH_ARCHIVE_TABLE: weakref.WeakSet[Any] = weakref.WeakSet()


def has_chat_message_archive_table(connection: Any) -> bool:
    """Return True if ``chat_message_archive`` exists on ``connection`` (cached once present).

    Accepts either a SQLAlchemy ``Connection`` or a ``Session``.
    """
    if connection is None:
        return False
    if not hasattr(connection, "dialect"):
        get_conn = getattr(connection, "connection", None)
        if not callable(get_conn):
            return False
        connection = get_conn()
    engine = getattr(connection, "engine", None)
    if engine is not None and engine in _ENGINES_WITH_ARCHIVE_TABLE:
        return True
    dialect = getattr(connection, "dialect", None)
    if dialect is None:
        return False
    exists = bool(dialect.has_table(connection, ChatMessageArchive.__tablename__))
    if exists and engine is not None:
        _ENGINES_WITH_ARCHIVE_TABLE.add(engine)
    return exists
