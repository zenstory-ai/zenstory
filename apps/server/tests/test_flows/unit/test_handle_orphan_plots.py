from __future__ import annotations

import importlib
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

orphan_mod = importlib.import_module("flows.atomic_tasks.narrative.handle_orphan_plots")
decorator_mod = importlib.import_module("flows.utils.decorators.prefect")


class _FakeClient:
    def extract_json_from_response(self, _response):
        return {"assign": {"501": [1]}, "unassigned": [2]}


def test_assign_orphans_passes_novel_id_keyword_to_batch_task(monkeypatch):
    """
    Regression: assign_orphans_with_llm_task calls
    _assign_single_batch_task(novel_id=...). The batch task used to declare
    `_novel_id`, so every batch raised TypeError (swallowed by the per-batch
    except) and no orphan plot was ever assigned.
    """
    monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(orphan_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(orphan_mod, "call_material_llm", lambda **kwargs: "raw-llm-response")
    monkeypatch.setattr(orphan_mod, "get_material_llm_client", lambda: _FakeClient())
    executed: list[list[dict]] = []
    monkeypatch.setattr(
        orphan_mod,
        "_execute_assignments",
        lambda assignments, **_allowed: executed.append(assignments) or len(assignments),
    )

    batch_task = orphan_mod._assign_single_batch_task
    # Call the undecorated function so Python binds the keyword arguments exactly
    # as the real task invocation does (a mismatch raises TypeError).
    monkeypatch.setattr(orphan_mod, "_assign_single_batch_task", lambda **kwargs: batch_task.fn(**kwargs))

    result = orphan_mod.assign_orphans_with_llm_task.fn(
        novel_id=42,
        orphan_plot_ids=[1, 2],
        stories=[],
        all_plots=[
            {"id": 1, "chapter_id": 10, "description": "orphan one", "characters": []},
            {"id": 2, "chapter_id": 10, "description": "orphan two", "characters": []},
        ],
    )

    assert result == {"assigned_count": 1, "unassigned_count": 1}
    assert executed == [[{"plot_id": 1, "story_id": 501}]]


class _LLMClientReturning:
    def __init__(self, data):
        self.data = data

    def extract_json_from_response(self, _response):
        return self.data


@pytest.mark.integration
def test_batch_drops_llm_ids_outside_batch_and_skips_existing_links(monkeypatch, db_session):
    """LLM-returned IDs are untrusted: only this batch's stories/orphans are linked, never twice."""
    from sqlmodel import select

    from models.material_models import Chapter, Novel, Plot, Story, StoryPlotLink

    novel = Novel(user_id="u-orphan", title="Orphans")
    other = Novel(user_id="u-other", title="Other")
    db_session.add_all([novel, other])
    db_session.commit()
    chapter = Chapter(novel_id=novel.id, chapter_number=1, title="c1")
    db_session.add(chapter)
    db_session.commit()
    plots = [Plot(chapter_id=chapter.id, index=i, plot_type="SETUP", description=f"p{i}") for i in range(4)]
    story = Story(novel_id=novel.id, title="mine", synopsis="s")
    foreign_story = Story(novel_id=other.id, title="theirs", synopsis="s")
    db_session.add_all([*plots, story, foreign_story])
    db_session.commit()
    p_orphan, p_linked, p_not_in_batch, _ = plots
    db_session.add(StoryPlotLink(story_id=story.id, plot_id=p_linked.id, order_index=0))
    db_session.commit()

    @contextmanager
    def _session_ctx():
        yield db_session

    monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(orphan_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(orphan_mod, "get_db_session", _session_ctx)
    monkeypatch.setattr(orphan_mod, "call_material_llm", lambda **kwargs: "raw")
    monkeypatch.setattr(
        orphan_mod,
        "get_material_llm_client",
        lambda: _LLMClientReturning({
            "assign": {
                str(story.id): [p_orphan.id, p_orphan.id, p_linked.id, p_not_in_batch.id],
                str(foreign_story.id): [p_orphan.id],
                "999999": [p_orphan.id],
            },
            "unassigned": [],
        }),
    )

    all_plots = [
        {"id": p.id, "chapter_id": chapter.id, "description": p.description, "characters": []} for p in plots
    ]
    result = orphan_mod._assign_single_batch_task.fn(
        novel_id=novel.id,
        orphan_plot_ids=[p_orphan.id, p_linked.id],
        stories=[{"id": story.id, "title": "mine", "synopsis": "s", "plot_ids": [p_linked.id]}],
        all_plots=all_plots,
        batch_num=1,
        total_batches=1,
    )

    assert result["assigned_count"] == 1
    links = db_session.exec(select(StoryPlotLink.story_id, StoryPlotLink.plot_id)).all()
    assert sorted(links) == sorted([(story.id, p_linked.id), (story.id, p_orphan.id)])
