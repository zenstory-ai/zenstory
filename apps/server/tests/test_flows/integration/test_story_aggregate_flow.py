from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from tests.test_flows.conftest import FakeMonitor

story_mod = importlib.import_module("flows.pipelines.subflows.story_aggregate_flow")


class _FakeCheckpointManager:
    def __init__(self):
        self.update_calls = []

    def update_checkpoint(self, stage, status=None, data=None):
        self.update_calls.append((stage, status, data))

    def get_checkpoint(self, _stage):
        return None


@pytest.mark.integration
class TestStoryAggregateFlow:
    def test_flow_with_all_feature_flags_disabled(self, monkeypatch):
        cp = _FakeCheckpointManager()

        monkeypatch.setattr(story_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(story_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        checkpoint_args = []
        monkeypatch.setattr(
            story_mod,
            "create_checkpoint_manager",
            lambda novel_id, job_id=None: checkpoint_args.append((novel_id, job_id)) or cp,
        )

        monkeypatch.setattr(story_mod.settings, "ENABLE_NOVEL_SYNOPSIS", False)
        monkeypatch.setattr(story_mod.settings, "ENABLE_STORY_AGGREGATION", False)
        monkeypatch.setattr(story_mod.settings, "ENABLE_STORYLINE_GENERATION", False)
        monkeypatch.setattr(story_mod.settings, "ENABLE_META_EXTRACTION", False)

        result = story_mod.story_aggregate_flow.fn(novel_id=1, chapter_ids=[1, 2], job_id=77)

        assert result["novel_id"] == 1
        assert result["synopsis_generated"] is False
        assert result["stories_count"] == 0
        assert result["storylines_count"] == 0
        assert checkpoint_args == [(1, 77)]
        assert cp.update_calls[-1][0] == "stage2a"

    def test_flow_handles_no_frameworks_when_story_aggregation_enabled(self, monkeypatch):
        cp = _FakeCheckpointManager()

        monkeypatch.setattr(story_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(story_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        checkpoint_args = []
        monkeypatch.setattr(
            story_mod,
            "create_checkpoint_manager",
            lambda novel_id, job_id=None: checkpoint_args.append((novel_id, job_id)) or cp,
        )

        monkeypatch.setattr(story_mod.settings, "ENABLE_NOVEL_SYNOPSIS", False)
        monkeypatch.setattr(story_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
        monkeypatch.setattr(story_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(story_mod.settings, "ENABLE_STORY_AGGREGATION", True)
        monkeypatch.setattr(story_mod.settings, "ENABLE_STORYLINE_GENERATION", True)
        monkeypatch.setattr(story_mod.settings, "ENABLE_META_EXTRACTION", False)
        monkeypatch.setattr(
            story_mod,
            "identify_story_frameworks_with_chunking",
            lambda **kwargs: {"story_frameworks": [], "chunking_info": {"chunk_count": 0}},
        )

        result = story_mod.story_aggregate_flow.fn(novel_id=2, chapter_ids=[1])

        assert result["stories_count"] == 0
        assert result["storylines_count"] == 0
        assert result["status"] == "completed"
        assert checkpoint_args == [(2, None)]

    def test_flow_marks_completed_with_errors_when_some_story_frameworks_fail(self, monkeypatch):
        cp = _FakeCheckpointManager()

        monkeypatch.setattr(story_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(story_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        monkeypatch.setattr(story_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: cp)

        monkeypatch.setattr(story_mod.settings, "ENABLE_NOVEL_SYNOPSIS", False)
        monkeypatch.setattr(story_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
        monkeypatch.setattr(story_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(story_mod.settings, "ENABLE_STORY_AGGREGATION", True)
        monkeypatch.setattr(story_mod.settings, "ENABLE_STORYLINE_GENERATION", True)
        monkeypatch.setattr(story_mod.settings, "ENABLE_META_EXTRACTION", False)
        monkeypatch.setattr(
            story_mod,
            "identify_story_frameworks_with_chunking",
            lambda **kwargs: {
                "story_frameworks": [{"title": "ok"}, {"title": "boom"}],
                "chunking_info": {"chunk_count": 1},
            },
        )

        class _AggregateTask:
            def submit(self, story_framework, novel_id):
                from tests.test_flows.conftest import FakeFuture

                if story_framework["title"] == "ok":
                    return FakeFuture({"story": {"title": "ok"}})
                return FakeFuture(error=RuntimeError("aggregate fail"))

        monkeypatch.setattr(story_mod, "aggregate_plots_for_story_framework", _AggregateTask())
        monkeypatch.setattr(
            story_mod,
            "save_stories_with_plots",
            lambda stories, novel_id: {"saved_count": len(stories), "story_id_map": {"ok": 1}},
        )
        monkeypatch.setattr(
            story_mod,
            "handle_orphan_plots_task",
            lambda **kwargs: {
                "assigned_count": 0,
                "orphan_count": 0,
                "unassigned_count": 0,
                "skipped": True,
                "orphan_ratio": 0.0,
            },
        )
        monkeypatch.setattr(
            story_mod,
            "extract_storylines_from_stories",
            lambda stories, novel_id: {"storylines": [{"title": "line"}]},
        )
        monkeypatch.setattr(
            story_mod,
            "save_storylines_with_stories",
            lambda storylines, novel_id: {"saved_count": len(storylines)},
        )

        result = story_mod.story_aggregate_flow.fn(novel_id=3, chapter_ids=[1, 2])

        assert result["stories_count"] == 1
        assert result["storylines_count"] == 1
        assert result["failed_stories"] == ["2:boom"]
        assert result["status"] == "completed_with_errors"

    def test_flow_saves_stories_without_storylines_when_storyline_stage_disabled(self, monkeypatch):
        cp = _FakeCheckpointManager()

        monkeypatch.setattr(story_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(story_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        monkeypatch.setattr(story_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: cp)

        monkeypatch.setattr(story_mod.settings, "ENABLE_NOVEL_SYNOPSIS", False)
        monkeypatch.setattr(story_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
        monkeypatch.setattr(story_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(story_mod.settings, "ENABLE_STORY_AGGREGATION", True)
        monkeypatch.setattr(story_mod.settings, "ENABLE_STORYLINE_GENERATION", False)
        monkeypatch.setattr(story_mod.settings, "ENABLE_META_EXTRACTION", False)
        monkeypatch.setattr(
            story_mod,
            "identify_story_frameworks_with_chunking",
            lambda **kwargs: {"story_frameworks": [{"title": "ok"}], "chunking_info": {"chunk_count": 1}},
        )

        class _AggregateTask:
            def submit(self, story_framework, novel_id):
                from tests.test_flows.conftest import FakeFuture

                return FakeFuture({"story": {"title": story_framework["title"]}})

        def _forbidden(*_args, **_kwargs):
            raise AssertionError("storyline tasks must not run when the storyline stage is disabled")

        monkeypatch.setattr(story_mod, "aggregate_plots_for_story_framework", _AggregateTask())
        monkeypatch.setattr(
            story_mod,
            "save_stories_with_plots",
            lambda stories, novel_id: {"saved_count": len(stories), "story_id_map": {"ok": 1}},
        )
        monkeypatch.setattr(
            story_mod,
            "handle_orphan_plots_task",
            lambda **kwargs: {"assigned_count": 0, "skipped": True, "orphan_ratio": 0.0},
        )
        monkeypatch.setattr(story_mod, "extract_storylines_from_stories", _forbidden)
        monkeypatch.setattr(story_mod, "save_storylines_with_stories", _forbidden)

        result = story_mod.story_aggregate_flow.fn(novel_id=4, chapter_ids=[1])

        assert result["stories_count"] == 1
        assert result["storylines_count"] == 0
        assert result["status"] == "completed"
