"""
Story plots service - SQLModel version.
Handles StoryPlotLink operations for story-plot relationships.
"""
from __future__ import annotations

from sqlmodel import Session

from models.material_models import StoryPlotLink


class StoryPlotsService:
    """Story-plot relationship service using SQLModel patterns."""

    def link_plot_to_story(
        self,
        session: Session,
        story_id: int,
        plot_id: int,
        order_index: int | None = None,
        role: str | None = None
    ) -> StoryPlotLink:
        """Link a plot to a story."""
        link = StoryPlotLink(
            story_id=story_id,
            plot_id=plot_id,
            order_index=order_index,
            role=role
        )
        session.add(link)
        session.flush()
        return link
