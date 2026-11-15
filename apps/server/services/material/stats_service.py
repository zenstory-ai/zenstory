"""
Stats service - SQLModel version.
Handles stage completion statistics for the material library pipeline.
"""
from __future__ import annotations

from sqlmodel import Session, func, select

from models.material_models import Chapter, Plot


class StatsService:
    """Statistics domain service using SQLModel patterns."""

    def count_stage1(self, session: Session, novel_id: int) -> dict[str, int]:
        """Count stage1 completion: chapter summaries and plot points."""
        summaries_count = session.exec(
            select(func.count(Chapter.id))
            .where(Chapter.novel_id == novel_id)
            .where(Chapter.summary.isnot(None))
        ).one()

        plots_count = session.exec(
            select(func.count(Plot.id))
            .join(Chapter, Plot.chapter_id == Chapter.id)
            .where(Chapter.novel_id == novel_id)
        ).one()

        return {"summaries_count": summaries_count, "plots_count": plots_count}
