from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from flows.pipelines.stages import stage_executor as se_mod


class _DummyPublisher:
    def __init__(self, _correlation_id, _logger):
        self.events = []
        self.completions = []

    def publish(self, event_type: str, **kwargs):
        self.events.append((event_type, kwargs))

    def publish_completion(self, novel_id, chapter_ids, result):
        self.completions.append((novel_id, chapter_ids, result))


class _FakeCheckpointManager:
    def __init__(self):
        self.completed_calls = []

    def get_checkpoint(self, _stage: str):
        return None

    def mark_stage_completed(self, stage: str, data):
        self.completed_calls.append((stage, data))

    def update_checkpoint(self, *args, **kwargs):
        return None


@pytest.mark.integration
class TestStageExecutorIntegration:
    def test_execute_stage1_publishes_and_writes_checkpoint(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)

        chapter_flow_mod = importlib.import_module("flows.pipelines.subflows.chapter_extraction_flow")

        monkeypatch.setattr(
            chapter_flow_mod,
            "chapter_extraction_flow",
            lambda novel_id, chapter_ids, job_id=None: {
                "summaries_count": len(chapter_ids),
                "plots_count": len(chapter_ids) * 2,
                "mentions_extracted": True,
                "failed_count": 0,
                "failed_chapters": [],
                "status": "completed",
            },
        )

        cp = _FakeCheckpointManager()
        executor = se_mod.StageExecutor(novel_id=7, chapter_ids=[1, 2], checkpoint_manager=cp, correlation_id="cid")
        monkeypatch.setattr(executor, "_handle_meta_extraction", lambda: None)

        result = executor.execute_stage1()

        assert result["summaries_count"] == 2
        assert cp.completed_calls[0][0] == "stage1"
        assert cp.completed_calls[0][1]["mentions_extracted"] is True
        event_types = [e for e, _ in executor.publisher.events]
        assert "stage1_started" in event_types
        assert "stage1_completed" in event_types

    def test_execute_stage2_builds_final_result_and_publishes_completion(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)

        cp = _FakeCheckpointManager()
        executor = se_mod.StageExecutor(novel_id=9, chapter_ids=[11, 12], checkpoint_manager=cp, correlation_id="cid")

        monkeypatch.setattr(executor, "_check_stage2_completion", lambda: (False, False, False))
        monkeypatch.setattr(
            executor,
            "_execute_parallel_stages",
            lambda _a, _b: (
                {"synopsis_generated": True, "stories_count": 3, "storylines_count": 1, "failed_stories": [], "status": "completed"},
                {"created_count": 2, "updated_count": 1, "failed_count": 0, "failed_characters": [], "status": "completed"},
            ),
        )
        monkeypatch.setattr(
            executor,
            "_execute_relationship_stage",
            lambda _done: {"relationships_count": 4, "status": "completed"},
        )
        monkeypatch.setattr(executor, "_update_stage2_checkpoints", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor, "_update_job_status", lambda *args, **kwargs: None)
        monkeypatch.setattr(executor, "_get_job_id", lambda: 222)
        monkeypatch.setattr(executor, "_save_final_checkpoint", lambda *args, **kwargs: None)

        result = executor.execute_stage2(
            stage1_result={"summaries_count": 2, "plots_count": 5, "mentions_extracted": True},
            flow_start=0.0,
        )

        assert result["novel_id"] == 9
        assert result["job_id"] == 222
        assert result["stories_count"] == 3
        assert result["relationships_count"] == 4
        assert result["status"] == "completed"
        assert len(executor.publisher.completions) == 1
