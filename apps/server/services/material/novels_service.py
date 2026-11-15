"""
Novel service - SQLModel version.
Handles Novel CRUD operations.
"""
from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from models.material_models import Chapter, Novel


class NovelsService:
    """Novel domain service using SQLModel patterns."""

    def get_by_id(self, session: Session, novel_id: int) -> Novel | None:
        """Get novel by ID."""
        return session.get(Novel, novel_id)

    def get_by_content_hash(self, session: Session, content_hash: str, user_id: str | None = None) -> Novel | None:
        """Find novel by content hash in source_meta, optionally filtered by user."""
        statement = (
            select(Novel)
            .where(Novel.source_meta.is_not(None))
            .where(Novel.source_meta.contains(f'"md5_checksum": "{content_hash}"'))
        )
        if user_id:
            statement = statement.where(Novel.user_id == user_id)
        return session.exec(statement).first()

    def list_chapter_ids(self, session: Session, novel_id: int) -> list[int]:
        """List all chapter IDs for a novel."""
        statement = select(Chapter.id).where(Chapter.novel_id == novel_id)
        results = session.exec(statement).all()
        return list(results)

    def create_novel(self, session: Session, data: dict[str, Any]) -> Novel:
        """Create a new novel."""
        if "source_meta" in data and isinstance(data["source_meta"], dict):
            data["source_meta"] = json.dumps(data["source_meta"])
        novel = Novel(**data)
        session.add(novel)
        session.flush()
        return novel

    def update_synopsis(self, session: Session, novel_id: int, synopsis: str) -> None:
        """Update novel synopsis."""
        novel = session.get(Novel, novel_id)
        if not novel:
            raise ValueError(f"小说不存在: {novel_id}")
        novel.synopsis = synopsis
        session.add(novel)
        session.flush()
