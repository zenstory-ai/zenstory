from __future__ import annotations

import json

from sqlmodel import Session

from models.material_models import (
    Chapter,
    Character,
    Novel,
    Plot,
    Story,
    StoryLine,
    StoryPlotLink,
)
from services.material.golden_finger_service import GoldenFingerService
from services.material.relationships_service import RelationshipsService
from services.material.stories_service import StoriesService
from services.material.story_plots_service import StoryPlotsService
from services.material.world_view_service import WorldViewService


def _create_novel(db_session: Session) -> Novel:
    novel = Novel(user_id="user-1", title="Domain Novel", author="Author")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)
    return novel


def _create_chapter(db_session: Session, novel_id: int, chapter_number: int) -> Chapter:
    chapter = Chapter(novel_id=novel_id, chapter_number=chapter_number, title=f"Chapter {chapter_number}")
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)
    return chapter


def test_golden_finger_service_upsert_paths(db_session: Session):
    novel = _create_novel(db_session)
    chapter = _create_chapter(db_session, novel.id, 1)
    service = GoldenFingerService()

    assert service.upsert(
        db_session,
        novel.id,
        {
            "name": "系统",
            "type": "system",
            "description": "初始版本",
            "first_appearance_chapter_id": chapter.id,
            "evolution_history": [{"stage": 1}],
        },
    ) == "created"

    created = service.get_by_name(db_session, novel.id, "系统")
    assert created is not None
    assert created.type == "system"
    assert json.loads(created.evolution_history or "[]") == [{"stage": 1}]

    assert service.upsert(
        db_session,
        novel.id,
        {
            "name": "系统",
            "description": "升级后版本",
            "evolution_history": [{"stage": 2}],
        },
    ) == "updated"

    db_session.commit()
    db_session.refresh(created)
    assert created.description == "升级后版本"
    assert json.loads(created.evolution_history or "[]") == [{"stage": 2}]


def test_relationships_service_handles_bidirectional_upsert(db_session: Session):
    novel = _create_novel(db_session)
    alice = Character(novel_id=novel.id, name="Alice")
    bob = Character(novel_id=novel.id, name="Bob")
    db_session.add(alice)
    db_session.add(bob)
    db_session.commit()
    db_session.refresh(alice)
    db_session.refresh(bob)

    service = RelationshipsService()
    created, updated = service.upsert_relationships(
        db_session,
        novel.id,
        [
            {
                "character_a_id": alice.id,
                "character_b_id": bob.id,
                "relationship_type": "ally",
                "sentiment": "friendly",
                "description": "初次合作",
            }
        ],
    )
    assert (created, updated) == (1, 0)

    rel = service.get_relationship(db_session, novel.id, alice.id, bob.id)
    reverse_rel = service.get_relationship(db_session, novel.id, bob.id, alice.id)
    assert rel is not None
    assert reverse_rel is not None
    assert rel.id == reverse_rel.id

    created, updated = service.upsert_relationships(
        db_session,
        novel.id,
        [
            {
                "character_a_id": bob.id,
                "character_b_id": alice.id,
                "relationship_type": "family",
                "description": "双向更新",
            },
            {"character_a_id": alice.id},
        ],
    )
    db_session.refresh(rel)
    assert (created, updated) == (0, 1)
    assert rel.relationship_type == "family"
    assert rel.description == "双向更新"


def test_story_services_attach_storylines_and_story_plot_links(db_session: Session):
    novel = _create_novel(db_session)
    chapter_one = _create_chapter(db_session, novel.id, 1)
    chapter_two = _create_chapter(db_session, novel.id, 2)

    plot_one = Plot(chapter_id=chapter_one.id, index=0, plot_type="SETUP", description="开场")
    plot_two = Plot(chapter_id=chapter_two.id, index=0, plot_type="TURNING_POINT", description="转折")
    db_session.add(plot_one)
    db_session.add(plot_two)
    db_session.commit()
    db_session.refresh(plot_one)
    db_session.refresh(plot_two)

    stories_service = StoriesService()
    story_id = stories_service.upsert_story(
        db_session,
        novel.id,
        {
            "title": "主线剧情",
            "synopsis": "主角踏上旅途",
            "themes": ["成长"],
        },
    )
    same_story_id = stories_service.upsert_story(
        db_session,
        novel.id,
        {
            "title": "主线剧情",
            "synopsis": "主角踏上旅途",
            "core_objective": "守护村庄",
            "chapter_range": "1-2",
        },
    )
    assert story_id == same_story_id

    storyline_id = stories_service.create_storyline(
        db_session,
        novel.id,
        {"title": "主线", "description": "贯穿始终", "themes": ["成长"]},
    )
    assert stories_service.attach_stories_to_storyline(db_session, storyline_id, [story_id, 999999]) == 1
    assert stories_service.attach_plots_to_story(db_session, story_id, [plot_one.id, 999999]) == 1

    plot_link_service = StoryPlotsService()
    link = plot_link_service.link_plot_to_story(db_session, story_id, plot_two.id, order_index=1)

    db_session.commit()
    story = db_session.get(Story, story_id)
    storyline = db_session.get(StoryLine, storyline_id)
    assert story is not None
    assert storyline is not None
    assert story.story_line_id == storyline_id
    assert json.loads(storyline.themes or "[]") == ["成长"]
    assert isinstance(link, StoryPlotLink)
    assert link.story_id == story_id
    assert link.plot_id == plot_two.id


def test_world_view_service_upsert_paths(db_session: Session):
    novel = _create_novel(db_session)
    service = WorldViewService()

    assert service.upsert(
        db_session,
        novel.id,
        {"power_system": "Qi", "key_factions": ["青云门"]},
    ) == "created"

    created = service.get_by_novel(db_session, novel.id)
    assert created is not None
    assert created.power_system == "Qi"
    assert json.loads(created.key_factions or "[]") == ["青云门"]

    assert service.upsert(
        db_session,
        novel.id,
        {"world_structure": "三界", "special_rules": ["渡劫"]},
    ) == "updated"

    db_session.commit()
    updated = service.get_by_novel(db_session, novel.id)
    assert updated is not None
    assert updated.id == created.id
    assert updated.world_structure == "三界"
    assert json.loads(updated.special_rules or "[]") == ["渡劫"]


def test_attach_plots_to_story_rejects_foreign_novel_plots_and_duplicates(db_session):
    from sqlmodel import select

    from models.material_models import Chapter, Novel, Plot, Story, StoryPlotLink
    from services.material.stories_service import StoriesService

    novel = Novel(user_id="u-attach", title="Mine")
    other = Novel(user_id="u-attach-2", title="Theirs")
    db_session.add_all([novel, other])
    db_session.commit()
    own_chapter = Chapter(novel_id=novel.id, chapter_number=1, title="c1")
    other_chapter = Chapter(novel_id=other.id, chapter_number=1, title="c1")
    db_session.add_all([own_chapter, other_chapter])
    db_session.commit()
    own_plot = Plot(chapter_id=own_chapter.id, index=0, plot_type="SETUP", description="own")
    other_plot = Plot(chapter_id=other_chapter.id, index=0, plot_type="SETUP", description="other")
    db_session.add_all([own_plot, other_plot])
    db_session.commit()

    svc = StoriesService()
    story_id = svc.upsert_story(db_session, novel.id, {"title": "t", "synopsis": "s"})

    assert svc.attach_plots_to_story(db_session, story_id, [own_plot.id, other_plot.id, own_plot.id]) == 1
    # Re-running the aggregation must not duplicate links.
    assert svc.attach_plots_to_story(db_session, story_id, [own_plot.id]) == 0
    db_session.commit()

    links = db_session.exec(select(StoryPlotLink.plot_id).where(StoryPlotLink.story_id == story_id)).all()
    assert links == [own_plot.id]

    # Legacy story without novel_id: no novel check, dedupe still applies.
    legacy = Story(title="legacy", synopsis="s")
    db_session.add(legacy)
    db_session.commit()
    assert svc.attach_plots_to_story(db_session, legacy.id, [other_plot.id, other_plot.id]) == 1
