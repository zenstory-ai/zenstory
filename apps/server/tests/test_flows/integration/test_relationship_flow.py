from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

from tests.test_flows.conftest import FakeMonitor

rel_mod = importlib.import_module("flows.pipelines.subflows.relationship_flow")


class _FakeCheckpointManager:
    def __init__(self):
        self.update_calls = []

    def update_checkpoint(self, stage, status=None, data=None):
        self.update_calls.append((stage, status, data))


@pytest.mark.integration
class TestRelationshipFlow:
    def test_flow_with_features_disabled(self, monkeypatch):
        cp = _FakeCheckpointManager()

        monkeypatch.setattr(rel_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(rel_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        checkpoint_args = []
        monkeypatch.setattr(
            rel_mod,
            "create_checkpoint_manager",
            lambda novel_id, job_id=None: checkpoint_args.append((novel_id, job_id)) or cp,
        )

        monkeypatch.setattr(rel_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", False)

        result = rel_mod.relationship_flow.fn(novel_id=1, chapter_ids=[1, 2], job_id=88)

        assert result["relationships_count"] == 0
        assert result["status"] == "completed"
        assert checkpoint_args == [(1, 88)]
        assert cp.update_calls[-1][0] == "stage2b"

    def test_flow_extracts_and_builds_relationships(self, monkeypatch):
        cp = _FakeCheckpointManager()

        monkeypatch.setattr(rel_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(rel_mod, "create_performance_monitor", lambda _name: FakeMonitor())
        checkpoint_args = []
        monkeypatch.setattr(
            rel_mod,
            "create_checkpoint_manager",
            lambda novel_id, job_id=None: checkpoint_args.append((novel_id, job_id)) or cp,
        )

        monkeypatch.setattr(rel_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", True)
        monkeypatch.setattr(rel_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(rel_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)

        monkeypatch.setattr(
            rel_mod,
            "extract_character_relationships_task",
            lambda novel_id, chapter_ids, batch_size: {
                "relationships": [{"character_a_id": 1, "character_b_id": 2}],
                "batches_processed": 1,
            },
        )
        monkeypatch.setattr(
            rel_mod,
            "build_character_relationships_task",
            lambda novel_id, relationships_data: {"saved_count": 1},
        )

        result = rel_mod.relationship_flow.fn(novel_id=9, chapter_ids=[1, 2, 3])

        assert result["relationships_count"] == 1
        assert result["status"] == "completed"
        assert checkpoint_args == [(9, None)]
