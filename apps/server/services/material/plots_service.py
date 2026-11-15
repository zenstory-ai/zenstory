"""
Plots service - SQLModel version.
Handles Plot bulk insert and query operations.
"""
from __future__ import annotations

import json
from collections.abc import Iterable

from sqlmodel import Session, select

from models.material_models import Chapter, Plot


class PlotsService:
    """Plot domain service using SQLModel patterns."""

    def bulk_insert(
        self, session: Session, _novel_id: int | None = None, plots: Iterable[dict] = ()
    ) -> int:
        """Bulk insert plots for a novel. Returns count of created plots."""
        created = 0
        for p in plots:
            # 处理 characters - 需要转为 JSON 字符串
            characters = p.get("data", {}).get("characters") or p.get("characters")
            if characters and isinstance(characters, list):
                characters = json.dumps(characters, ensure_ascii=False)

            plot = Plot(
                chapter_id=p["chapter_id"],
                index=p.get("index", 0),
                plot_type=p.get("type", "OTHER"),
                description=p.get("description", ""),
                characters=characters,
            )
            session.add(plot)
            created += 1
        session.flush()
        return created

    def list_by_novel(
        self, session: Session, novel_id: int
    ) -> list[Plot]:
        """List all plots for a novel by joining through chapters."""
        statement = (
            select(Plot)
            .join(Chapter, Plot.chapter_id == Chapter.id)
            .where(Chapter.novel_id == novel_id)
            .order_by(Chapter.chapter_number, Plot.index)
        )
        return list(session.exec(statement).all())

    def list_by_chapter_ids(
        self, session: Session, chapter_ids: list[int]
    ) -> list[Plot]:
        """List plots by chapter IDs, ordered by chapter_id and index."""
        if not chapter_ids:
            return []

        statement = (
            select(Plot)
            .where(Plot.chapter_id.in_(chapter_ids))
            .order_by(Plot.chapter_id, Plot.index)
        )
        return list(session.exec(statement).all())
