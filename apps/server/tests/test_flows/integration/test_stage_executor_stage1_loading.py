from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from flows.pipelines.stages import stage_executor as se_mod


class _DummyPublisher:
    def __init__(self, _correlation_id, _logger):
        pass


class _FakeSessionCtx:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.integration
class TestLoadStage1Result:
    def test_loads_from_checkpoint_and_backfills_mentions(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)

        cp = MagicMock()
        cp.get_checkpoint.return_value = SimpleNamespace(
            checkpoint_data={
                "summaries_count": 2,
                "plots_count": 6,
                "mentions_extracted": True,
            }
        )

        executor = se_mod.StageExecutor(novel_id=1, chapter_ids=[1], checkpoint_manager=cp, correlation_id=None)
        result = executor._load_stage1_result()

        assert result["summaries_count"] == 2
        assert result["plots_count"] == 6
        assert result["mentions_extracted"] is True
        cp.get_checkpoint.assert_called_once_with("stage1")

    def test_loads_from_stats_service_when_checkpoint_missing(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)

        cp = MagicMock()
        cp.get_checkpoint.return_value = None

        import services.material.stats_service as stats_mod

        class FakeStatsService:
            def count_stage1(self, _session, _novel_id):
                return {"summaries_count": 3, "plots_count": 9}

        monkeypatch.setattr(stats_mod, "StatsService", FakeStatsService)
        monkeypatch.setattr(se_mod, "get_prefect_db_session", lambda: _FakeSessionCtx(MagicMock()))

        executor = se_mod.StageExecutor(novel_id=7, chapter_ids=[1, 2], checkpoint_manager=cp, correlation_id=None)
        result = executor._load_stage1_result()

        assert result["summaries_count"] == 3
        assert result["plots_count"] == 9
        assert result["mentions_extracted"] is False
        cp.get_checkpoint.assert_called_once_with("stage1")
