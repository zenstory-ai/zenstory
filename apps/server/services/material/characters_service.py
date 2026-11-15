"""
Characters service - SQLModel version.
Handles Character query operations.
"""
from __future__ import annotations

from sqlmodel import Session, select

from models.material_models import Character


class CharactersService:
    """Character domain service using SQLModel patterns."""

    def list_by_novel(
        self, session: Session, novel_id: int
    ) -> list[Character]:
        """List all characters for a novel."""
        statement = select(Character).where(Character.novel_id == novel_id)
        return list(session.exec(statement).all())
