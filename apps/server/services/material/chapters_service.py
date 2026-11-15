"""
Chapter service - SQLModel version.
Handles Chapter read/write operations.
"""
from __future__ import annotations

from collections.abc import Iterable

from sqlmodel import Session, select

from models.material_models import Chapter, Novel


class ChaptersService:
    """Chapter domain service using SQLModel patterns."""

    def get_by_id(self, session: Session, chapter_id: int) -> Chapter | None:
        """Get chapter by ID."""
        return session.get(Chapter, chapter_id)

    def list_ids_by_novel(self, session: Session, novel_id: int) -> list[int]:
        """List all chapter IDs for a novel."""
        statement = select(Chapter.id).where(Chapter.novel_id == novel_id)
        results = session.exec(statement).all()
        return list(results)

    def list_by_novel_ordered(
        self, session: Session, novel_id: int, chapter_ids: list[int] | None = None
    ) -> list[Chapter]:
        """List chapters for a novel, ordered by chapter_number."""
        statement = select(Chapter).where(Chapter.novel_id == novel_id)
        if chapter_ids:
            statement = statement.where(Chapter.id.in_(chapter_ids))
        statement = statement.order_by(Chapter.chapter_number)
        return list(session.exec(statement).all())

    def list_ids_by_number_range(
        self, session: Session, novel_id: int, start_number: int, end_number: int
    ) -> list[int]:
        """List chapter IDs within a chapter number range."""
        statement = (
            select(Chapter.id)
            .where(Chapter.novel_id == novel_id)
            .where(Chapter.chapter_number >= start_number)
            .where(Chapter.chapter_number <= end_number)
            .order_by(Chapter.chapter_number)
        )
        results = session.exec(statement).all()
        return list(results)

    def get_chapter_core_fields(
        self, session: Session, chapter_id: int
    ) -> dict[str, str] | None:
        """Get core fields of a chapter."""
        chapter = session.get(Chapter, chapter_id)
        if not chapter:
            return None
        return {
            "novel_id": chapter.novel_id,
            "title": chapter.title or "",
            "number": chapter.chapter_number or 0,
            "content": chapter.original_content or "",
        }

    def create_chapters(
        self, session: Session, novel: Novel, chapters_data: Iterable[dict]
    ) -> list[int]:
        """Create multiple chapters for a novel."""
        chapter_ids: list[int] = []
        for data in chapters_data:
            chapter = Chapter(
                novel_id=novel.id,
                chapter_number=data["chapter_number"],
                title=data.get("title"),
                original_content=data.get("content"),
            )
            session.add(chapter)
            session.flush()
            chapter_ids.append(chapter.id)
        return chapter_ids

    def save_summary(self, session: Session, chapter_id: int, summary: str) -> None:
        """Save chapter summary."""
        chapter = session.get(Chapter, chapter_id)
        if chapter:
            chapter.summary = summary
            session.add(chapter)
            session.flush()
