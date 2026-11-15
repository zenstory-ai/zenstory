from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from tests.test_flows.conftest import FakeFuture, FakeMonitor, FakeTask

chapter_mod = importlib.import_module("flows.pipelines.subflows.chapter_extraction_flow")


class _FakeCheckpointManager:
    def __init__(self, pending=None):
        self.update_calls = []
        self.pending = pending or {}

    def get_pending_chapters(self, _stage: str, all_chapter_ids: list[int], capability="summaries"):
        return self.pending.get(capability, all_chapter_ids)

    def get_checkpoint(self, _stage):
        return None

    def get_completed_chapters(self, _stage, capability="summaries"):
        return []

    def get_failed_chapters(self, _stage, capability="summaries"):
        return []

    def update_checkpoint(self, stage, status=None, data=None):
        self.update_calls.append((stage, status, data))


@pytest.mark.integration
class TestChapterExtractionFlow:
    def test_flow_with_all_feature_flags_disabled(self, monkeypatch):
        monkeypatch.setattr(chapter_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(chapter_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        monkeypatch.setattr(chapter_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: _FakeCheckpointManager())

        monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHAPTER_SUMMARIES", False)
        monkeypatch.setattr(chapter_mod.settings, "ENABLE_PLOT_EXTRACTION", False)
        monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHARACTER_EXTRACTION", False)

        result = chapter_mod.chapter_extraction_flow.fn(novel_id=1, chapter_ids=[10, 20], job_id=1)

        assert result["novel_id"] == 1
        assert result["summaries_count"] == 0
        assert result["plots_count"] == 0
        assert result["mentions_extracted"] is False
        assert result["status"] == "completed"

    def test_flow_extracts_mentions_when_enabled(self, monkeypatch):
        monkeypatch.setattr(chapter_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(chapter_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        monkeypatch.setattr(chapter_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: _FakeCheckpointManager())

        monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHAPTER_SUMMARIES", False)
        monkeypatch.setattr(chapter_mod.settings, "ENABLE_PLOT_EXTRACTION", False)
        monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)
        monkeypatch.setattr(chapter_mod.settings, "MAX_CONCURRENT_CHAPTERS", 3)

        mention_task = FakeTask({"chapter_id": 10, "mentions": ["A"]})
        monkeypatch.setattr(chapter_mod, "extract_character_mentions_task", mention_task)

        result = chapter_mod.chapter_extraction_flow.fn(novel_id=2, chapter_ids=[10, 20], job_id=2)

        assert result["mentions_extracted"] is True
        assert result["failed_count"] == 0
        assert len(mention_task.submit_calls) == 2

    def test_capabilities_submit_their_own_pending_sets(self, monkeypatch):
        monkeypatch.setattr(chapter_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(chapter_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        cp = _FakeCheckpointManager({"summaries": [], "plots": [20, 30], "mentions": [10, 30]})
        monkeypatch.setattr(chapter_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: cp)
        monkeypatch.setattr(chapter_mod, "_sync_stage1_job_progress", lambda *a, **k: None)
        monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
        monkeypatch.setattr(chapter_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)

        summary = FakeTask({"chapter_id": 0, "summary": "ok"})
        plots = FakeTask({"chapter_id": 0, "plots": []})
        save = FakeTask({"saved_count": 0})
        mentions = FakeTask({"chapter_id": 0, "mentions": []})
        monkeypatch.setattr(chapter_mod, "generate_chapter_summary_task", summary)
        monkeypatch.setattr(chapter_mod, "update_chapter_summary_task", FakeTask({}))
        monkeypatch.setattr(chapter_mod, "extract_chapter_plots_task", plots)
        monkeypatch.setattr(chapter_mod, "save_plots_task", save)
        monkeypatch.setattr(chapter_mod, "extract_character_mentions_task", mentions)

        chapter_mod.chapter_extraction_flow.fn(novel_id=2, chapter_ids=[10, 20, 30], job_id=55)

        assert summary.submit_calls == []
        assert [c[1]["chapter_id"] for c in plots.submit_calls] == [20, 30]
        assert [c[1]["chapter_id"] for c in mentions.submit_calls] == [10, 30]

    def test_flow_counts_failed_mentions_in_failed_count(self, monkeypatch):
        monkeypatch.setattr(chapter_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(chapter_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        monkeypatch.setattr(chapter_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: _FakeCheckpointManager())

        monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHAPTER_SUMMARIES", False)
        monkeypatch.setattr(chapter_mod.settings, "ENABLE_PLOT_EXTRACTION", False)
        monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)
        monkeypatch.setattr(chapter_mod.settings, "MAX_CONCURRENT_CHAPTERS", 3)

        class _MentionTask:
            def submit(self, chapter_id):
                if chapter_id == 10:
                    return FakeFuture({"chapter_id": chapter_id, "mentions": ["A"]})
                return FakeFuture(error=RuntimeError("mention fail"))

        monkeypatch.setattr(chapter_mod, "extract_character_mentions_task", _MentionTask())

        result = chapter_mod.chapter_extraction_flow.fn(novel_id=3, chapter_ids=[10, 20], job_id=3)

        assert result["mentions_extracted"] is True
        assert result["failed_count"] == 1
        assert result["failed_mention_chapters"] == [20]
        assert result["status"] == "completed_with_errors"
