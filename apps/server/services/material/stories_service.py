"""
Stories service - SQLModel version.
Handles Story, StoryLine, and StoryPlotLink operations.
"""
from __future__ import annotations

from sqlalchemy import and_, or_
from sqlmodel import Session, select

from models.material_models import Chapter, Plot, Story, StoryLine, StoryPlotLink

from .serialization import serialize_optional_json


def story_in_novel(novel_id: int):
    """SQL condition: the story belongs to ``novel_id``.

    Legacy rows without ``novel_id`` still resolve through their storyline.
    """
    return or_(
        Story.novel_id == novel_id,
        and_(
            Story.novel_id.is_(None),
            Story.story_line_id.in_(select(StoryLine.id).where(StoryLine.novel_id == novel_id)),
        ),
    )


class StoriesService:
    """Story aggregation service using SQLModel patterns."""

    def upsert_story(self, session: Session, novel_id: int, story_data: dict) -> int:
        """Upsert a story within one novel (matched on title + synopsis). Returns story ID."""
        title = story_data.get("title")
        synopsis = story_data.get("synopsis")

        # Check for existing story in the same novel only
        statement = select(Story).where(
            Story.novel_id == novel_id,
            Story.title == title,
            Story.synopsis == synopsis,
        )
        existing = session.exec(statement).first()

        if existing:
            # Update existing story
            existing.core_objective = story_data.get("core_objective") or existing.core_objective
            existing.core_conflict = story_data.get("core_conflict") or existing.core_conflict
            existing.story_type = story_data.get("story_type") or existing.story_type
            existing.chapter_range = story_data.get("chapter_range") or existing.chapter_range
            existing.themes = serialize_optional_json(story_data.get("themes")) or existing.themes
            session.add(existing)
            session.flush()
            return existing.id

        # Create new story
        new_story = Story(
            novel_id=novel_id,
            title=title,
            synopsis=synopsis,
            core_objective=story_data.get("core_objective"),
            core_conflict=story_data.get("core_conflict"),
            story_type=story_data.get("story_type"),
            chapter_range=story_data.get("chapter_range"),
            themes=serialize_optional_json(story_data.get("themes")),
        )
        session.add(new_story)
        session.flush()
        return new_story.id

    def attach_plots_to_story(
        self, session: Session, story_id: int, plot_ids: list[int]
    ) -> int:
        """Attach plots to a story. Returns count of links created.

        ``plot_ids`` come from the LLM, so they are validated: unknown plots, plots
        from another novel (when the story has ``novel_id``) and plots already linked
        to the story (re-runs) are skipped.
        """
        story = session.get(Story, story_id)
        if not story:
            return 0
        linked = set(
            session.exec(select(StoryPlotLink.plot_id).where(StoryPlotLink.story_id == story_id)).all()
        )
        count = 0
        for order_index, raw_plot_id in enumerate(plot_ids):
            try:
                plot_id = int(raw_plot_id)
            except (TypeError, ValueError):
                continue
            if plot_id in linked:
                continue
            plot = session.get(Plot, plot_id)
            if not plot:
                continue
            if story.novel_id is not None:
                chapter = session.get(Chapter, plot.chapter_id)
                if not chapter or chapter.novel_id != story.novel_id:
                    continue

            linked.add(plot_id)
            link = StoryPlotLink(
                story_id=story_id,
                plot_id=plot_id,
                order_index=order_index,
                role="MAIN",
            )
            session.add(link)
            count += 1

        session.flush()
        return count

    def create_storyline(self, session: Session, novel_id: int, data: dict) -> int:
        """Create a storyline. Returns storyline ID."""
        storyline = StoryLine(
            novel_id=novel_id,
            title=data.get("title"),
            description=data.get("description"),
            main_characters=serialize_optional_json(data.get("main_characters")),
            themes=serialize_optional_json(data.get("themes")),
        )
        session.add(storyline)
        session.flush()
        return storyline.id

    def attach_stories_to_storyline(
        self, session: Session, storyline_id: int, story_ids: list[int]
    ) -> int:
        """Attach stories of the storyline's novel to it. Returns count of updated stories."""
        storyline = session.get(StoryLine, storyline_id)
        if not storyline:
            return 0
        updated = 0
        for sid in story_ids:
            story = session.get(Story, sid)
            if story and story.novel_id == storyline.novel_id:
                story.story_line_id = storyline_id
                session.add(story)
                updated += 1

        session.flush()
        return updated
