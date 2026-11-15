"""
Stories are scoped to their novel via Story.novel_id.

- upsert_story matches only within one novel (no cross-novel / cross-user merge)
- stories are visible without a storyline (storyline stage failed or disabled)
- legacy rows without novel_id stay visible through their storyline
- a soft-deleted novel's stories disappear from every read path
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

from agent.context.assembler import ContextAssembler
from models.material_models import Chapter, Novel, Plot, Story, StoryLine
from services.material.stories_service import StoriesService
from tests.test_api.test_materials import (
    create_test_job,
    create_test_novel,
    create_test_user,
)

_STORY = {"title": "Moonlit Betrayal", "synopsis": "A secret betrayal changes the hero's route."}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add_story(db_session, **fields) -> Story:
    story = Story(**fields)
    db_session.add(story)
    db_session.commit()
    db_session.refresh(story)
    return story


@pytest.mark.integration
def test_upsert_story_is_scoped_per_novel(db_session):
    novel_a = Novel(user_id="user-a", title="Same Book")
    novel_b = Novel(user_id="user-b", title="Same Book")
    db_session.add_all([novel_a, novel_b])
    db_session.commit()

    svc = StoriesService()
    story_a = svc.upsert_story(db_session, novel_a.id, dict(_STORY))
    story_b = svc.upsert_story(db_session, novel_b.id, {**_STORY, "core_objective": "b-only"})
    story_a_again = svc.upsert_story(db_session, novel_a.id, {**_STORY, "chapter_range": "1-3"})
    db_session.commit()

    assert story_a != story_b
    assert story_a_again == story_a
    row_a = db_session.get(Story, story_a)
    row_b = db_session.get(Story, story_b)
    assert (row_a.novel_id, row_a.core_objective, row_a.chapter_range) == (novel_a.id, None, "1-3")
    assert (row_b.novel_id, row_b.core_objective) == (novel_b.id, "b-only")


@pytest.mark.integration
def test_attach_stories_to_storyline_ignores_other_novels_stories(db_session):
    novel_a = Novel(user_id="user-a", title="A")
    novel_b = Novel(user_id="user-b", title="B")
    db_session.add_all([novel_a, novel_b])
    db_session.commit()

    svc = StoriesService()
    own = svc.upsert_story(db_session, novel_a.id, {"title": "own", "synopsis": "s"})
    foreign = svc.upsert_story(db_session, novel_b.id, {"title": "foreign", "synopsis": "s"})
    storyline_id = svc.create_storyline(db_session, novel_a.id, {"title": "Arc"})

    assert svc.attach_stories_to_storyline(db_session, storyline_id, [own, foreign]) == 1
    db_session.commit()
    assert db_session.get(Story, own).story_line_id == storyline_id
    assert db_session.get(Story, foreign).story_line_id is None


@pytest.mark.integration
async def test_stories_visible_without_storyline(client: AsyncClient, db_session):
    """Storyline stage failed or disabled: stories still show up everywhere."""
    user, token = await create_test_user(client, db_session, "storyscope1")
    novel = create_test_novel(db_session, user.id, "No Storyline Novel")
    create_test_job(db_session, novel.id, "completed")
    story = _add_story(db_session, novel_id=novel.id, **_STORY)

    stories = await client.get(f"/api/v1/materials/{novel.id}/stories", headers=_auth(token))
    assert stories.status_code == 200
    assert [item["id"] for item in stories.json()] == [story.id]

    summary = await client.get(f"/api/v1/materials/{novel.id}/summary", headers=_auth(token))
    assert summary.json()["stories_count"] == 1
    assert summary.json()["storylines_count"] == 0

    library = await client.get("/api/v1/materials/library-summary", headers=_auth(token))
    assert library.json()[0]["counts"]["stories"] == 1

    search = await client.get("/api/v1/materials/search", params={"q": "Moonlit"}, headers=_auth(token))
    assert any(
        item["entity_type"] == "stories" and item["entity_id"] == story.id and item["novel_id"] == novel.id
        for item in search.json()
    )

    preview = await client.get(
        f"/api/v1/materials/{novel.id}/stories/{story.id}/preview", headers=_auth(token)
    )
    assert preview.status_code == 200
    assert "Moonlit Betrayal" in preview.json()["markdown"]

    items = ContextAssembler()._get_attached_library_materials(
        db_session, user.id, [{"novel_id": novel.id, "entity_type": "stories", "entity_id": story.id}]
    )
    assert len(items) == 1


@pytest.mark.integration
async def test_legacy_story_without_novel_id_visible_via_storyline(client: AsyncClient, db_session):
    user, token = await create_test_user(client, db_session, "storyscope2")
    novel = create_test_novel(db_session, user.id, "Legacy Novel")
    create_test_job(db_session, novel.id, "completed")
    storyline = StoryLine(novel_id=novel.id, title="Main Arc")
    db_session.add(storyline)
    db_session.commit()
    db_session.refresh(storyline)
    legacy = _add_story(db_session, story_line_id=storyline.id, **_STORY)
    assert legacy.novel_id is None

    stories = await client.get(f"/api/v1/materials/{novel.id}/stories", headers=_auth(token))
    assert [item["id"] for item in stories.json()] == [legacy.id]

    storylines = await client.get(f"/api/v1/materials/{novel.id}/storylines", headers=_auth(token))
    assert storylines.json()[0]["stories_count"] == 1

    summary = await client.get(f"/api/v1/materials/{novel.id}/summary", headers=_auth(token))
    assert summary.json()["stories_count"] == 1

    preview = await client.get(
        f"/api/v1/materials/{novel.id}/stories/{legacy.id}/preview", headers=_auth(token)
    )
    assert preview.status_code == 200

    items = ContextAssembler()._get_attached_library_materials(
        db_session, user.id, [{"novel_id": novel.id, "entity_type": "stories", "entity_id": legacy.id}]
    )
    assert len(items) == 1


@pytest.mark.integration
async def test_story_of_other_novel_does_not_leak_through_storyline(client: AsyncClient, db_session):
    """A historically merged story owned by novel A but pointing at B's storyline stays in A."""
    user_a, token_a = await create_test_user(client, db_session, "storyscope3a")
    user_b, token_b = await create_test_user(client, db_session, "storyscope3b")
    novel_a = create_test_novel(db_session, user_a.id, "Novel A")
    novel_b = create_test_novel(db_session, user_b.id, "Novel B")
    create_test_job(db_session, novel_a.id, "completed")
    create_test_job(db_session, novel_b.id, "completed")
    storyline_b = StoryLine(novel_id=novel_b.id, title="B Arc")
    db_session.add(storyline_b)
    db_session.commit()
    db_session.refresh(storyline_b)
    story = _add_story(db_session, novel_id=novel_a.id, story_line_id=storyline_b.id, **_STORY)

    b_stories = await client.get(f"/api/v1/materials/{novel_b.id}/stories", headers=_auth(token_b))
    assert b_stories.json() == []
    b_storylines = await client.get(f"/api/v1/materials/{novel_b.id}/storylines", headers=_auth(token_b))
    assert b_storylines.json()[0]["stories_count"] == 0
    b_preview = await client.get(
        f"/api/v1/materials/{novel_b.id}/stories/{story.id}/preview", headers=_auth(token_b)
    )
    assert b_preview.status_code == 404
    b_search = await client.get("/api/v1/materials/search", params={"q": "Moonlit"}, headers=_auth(token_b))
    assert b_search.json() == []

    a_stories = await client.get(f"/api/v1/materials/{novel_a.id}/stories", headers=_auth(token_a))
    assert [item["id"] for item in a_stories.json()] == [story.id]


@pytest.mark.integration
async def test_deleted_novel_hides_its_stories(client: AsyncClient, db_session):
    """Novel deletion is a soft delete: its stories must disappear from every read path."""
    user, token = await create_test_user(client, db_session, "storyscope4")
    novel = create_test_novel(db_session, user.id, "Deleted Novel")
    create_test_job(db_session, novel.id, "completed")
    chapter = Chapter(novel_id=novel.id, chapter_number=1, title="c1")
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)
    db_session.add(Plot(chapter_id=chapter.id, index=0, plot_type="SETUP", description="d"))
    story = _add_story(db_session, novel_id=novel.id, **_STORY)

    deleted = await client.delete(f"/api/v1/materials/{novel.id}", headers=_auth(token))
    assert deleted.status_code == 200

    stories = await client.get(f"/api/v1/materials/{novel.id}/stories", headers=_auth(token))
    assert stories.status_code == 403
    search = await client.get("/api/v1/materials/search", params={"q": "Moonlit"}, headers=_auth(token))
    assert search.json() == []
    library = await client.get("/api/v1/materials/library-summary", headers=_auth(token))
    assert library.json() == []
    items = ContextAssembler()._get_attached_library_materials(
        db_session, user.id, [{"novel_id": novel.id, "entity_type": "stories", "entity_id": story.id}]
    )
    assert items == []


@pytest.mark.integration
async def test_library_summary_and_search_filter_stories_without_coalesce(client: AsyncClient, db_session):
    """Story filters must stay index-friendly: no coalesce(Story.novel_id, ...) in WHERE.

    Attribution (legacy rows via storyline, new rows via novel_id) must stay correct.
    """
    from sqlalchemy import event

    user, token = await create_test_user(client, db_session, "storyscope5")
    novel_new = create_test_novel(db_session, user.id, "New Rows")
    novel_legacy = create_test_novel(db_session, user.id, "Legacy Rows")
    create_test_job(db_session, novel_new.id, "completed")
    create_test_job(db_session, novel_legacy.id, "completed")
    storyline = StoryLine(novel_id=novel_legacy.id, title="Legacy Arc")
    db_session.add(storyline)
    db_session.commit()
    db_session.refresh(storyline)
    new_story = _add_story(db_session, novel_id=novel_new.id, **_STORY)
    legacy_story = _add_story(db_session, story_line_id=storyline.id, **_STORY)

    statements: list[str] = []

    def _capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(" ".join(statement.lower().split()))

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", _capture)
    try:
        library = await client.get("/api/v1/materials/library-summary", headers=_auth(token))
        search = await client.get("/api/v1/materials/search", params={"q": "Moonlit"}, headers=_auth(token))
    finally:
        event.remove(engine, "before_cursor_execute", _capture)

    counts = {item["id"]: item["counts"]["stories"] for item in library.json()}
    assert counts == {novel_new.id: 1, novel_legacy.id: 1}
    story_hits = {
        (item["entity_id"], item["novel_id"]) for item in search.json() if item["entity_type"] == "stories"
    }
    assert story_hits == {(new_story.id, novel_new.id), (legacy_story.id, novel_legacy.id)}

    story_queries = [s for s in statements if "from stories" in s]
    assert story_queries, "expected story queries to be captured"
    for sql in story_queries:
        where_clause = sql.split(" where ", 1)[1].split(" group by ", 1)[0]
        assert "coalesce(stories.novel_id" not in where_clause, sql
