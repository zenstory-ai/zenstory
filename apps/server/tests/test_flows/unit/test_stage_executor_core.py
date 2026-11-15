from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from flows.pipelines.stages import stage_executor as se_mod
from tests.test_flows.conftest import FakeTask


class _DummyPublisher:
    def __init__(self, _correlation_id, _logger):
        self.events = []
        self.completions = []

    def publish(self, event_type: str, **kwargs):
        self.events.append((event_type, kwargs))

    def publish_completion(self, novel_id, chapter_ids, result):
        self.completions.append((novel_id, chapter_ids, result))


class _FakeCheckpointManager:
    def __init__(self, checkpoints=None):
        self.checkpoints = checkpoints or {}
        self.completed_calls = []
        self.update_calls = []

    def get_checkpoint(self, stage: str):
        return self.checkpoints.get(stage)

    def mark_stage_completed(self, stage: str, data):
        self.completed_calls.append((stage, data))

    def update_checkpoint(self, stage: str, status=None, data=None, error=None):
        self.update_calls.append((stage, status, data, error))


class TestStageExecutorCore:
    def test_meta_failure_payloads_never_expose_raw_exception(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        monkeypatch.setattr(se_mod.settings, "ENABLE_META_EXTRACTION", True)
        cp = _FakeCheckpointManager()
        executor = se_mod.StageExecutor(1, [1], cp, None)
        raw_error = "SQL password=private-token provider-secret"

        class _FailedFuture:
            def result(self):
                raise RuntimeError(raw_error)

        monkeypatch.setattr(executor, "_handle_meta_extraction", lambda **_kwargs: _FailedFuture())
        chapter_module = SimpleNamespace(
            chapter_extraction_flow=lambda **_kwargs: {
                "summaries_count": 1,
                "failed_count": 0,
                "status": "completed",
            }
        )
        monkeypatch.setitem(
            __import__("sys").modules,
            "flows.pipelines.subflows.chapter_extraction_flow",
            chapter_module,
        )
        job_payloads = []
        monkeypatch.setattr(
            executor,
            "_sync_job_stage",
            lambda *_args, **kwargs: job_payloads.append(kwargs.get("payload")),
        )

        stage1 = executor.execute_stage1()
        result = se_mod.ResultBuilder.build_final_result(
            novel_id=1,
            job_id=None,
            chapter_ids=[1],
            stage1_result=stage1,
            story_result={},
            relationship_result={},
            character_entity_result={},
            status="completed_with_errors",
            elapsed_ms=0,
        )
        executor._save_final_checkpoint(stage1, {}, {}, {})
        executor.publisher.publish_completion(1, [1], result)
        serialized = json.dumps(
            {
                "stage1": stage1,
                "checkpoint": cp.completed_calls,
                "job": job_payloads,
                "completion": executor.publisher.completions,
            }
        )

        assert raw_error not in serialized
        assert stage1["meta_extraction"]["error"] == "Metadata extraction failed"
        assert stage1["meta_extraction"]["error_code"] == "ERR_MATERIAL_META_EXTRACTION_FAILED"

    def test_meta_persistence_checkpoint_uses_sanitized_error(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        executor = se_mod.StageExecutor(1, [1], _FakeCheckpointManager(), None)
        raw_error = "SQL password=private-token provider-secret"

        class _SessionContext:
            def __enter__(self):
                return SimpleNamespace(
                    exec=lambda _statement: SimpleNamespace(
                        first=lambda: (_ for _ in ()).throw(RuntimeError(raw_error))
                    )
                )

            def __exit__(self, *_args):
                return False

        monkeypatch.setattr(se_mod, "get_prefect_db_session", _SessionContext)

        assert executor._save_meta_result({}) is False
        payload = executor.checkpoint_manager.update_calls[-1][2]
        assert raw_error not in json.dumps(payload)
        assert payload == {
            "meta_extracted": False,
            "meta_error": "Metadata extraction failed",
            "meta_error_code": "ERR_MATERIAL_META_EXTRACTION_FAILED",
        }

    @pytest.mark.parametrize("failure_point", ["provider", "persistence"])
    def test_meta_failure_is_explicit_and_makes_final_status_retryable(
        self, monkeypatch, failure_point
    ):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        monkeypatch.setattr(se_mod.settings, "ENABLE_META_EXTRACTION", True)
        cp = _FakeCheckpointManager()
        executor = se_mod.StageExecutor(1, [1], cp, None)

        if failure_point == "provider":
            class _FailedFuture:
                def result(self):
                    raise RuntimeError("provider unavailable")

            monkeypatch.setattr(executor, "_handle_meta_extraction", lambda **_kwargs: _FailedFuture())
        else:
            class _SuccessfulFuture:
                def result(self):
                    return {"golden_fingers": [], "worldview": {}}

            monkeypatch.setattr(executor, "_handle_meta_extraction", lambda **_kwargs: _SuccessfulFuture())
            monkeypatch.setattr(executor, "_save_meta_result", lambda _result: False)

        chapter_module = SimpleNamespace(
            chapter_extraction_flow=lambda **_kwargs: {
                "summaries_count": 1,
                "plots_count": 0,
                "failed_count": 0,
                "status": "completed",
            }
        )
        monkeypatch.setitem(
            __import__("sys").modules,
            "flows.pipelines.subflows.chapter_extraction_flow",
            chapter_module,
        )
        monkeypatch.setattr(executor, "_sync_job_stage", lambda *_args, **_kwargs: None)

        stage1 = executor.execute_stage1()

        assert stage1["meta_extraction"]["executed"] is True
        assert stage1["meta_extraction"]["succeeded"] is False
        assert stage1["meta_extraction"]["error"]
        assert stage1["meta_extraction"]["retry_available"] is True
        assert executor._derive_final_status(stage1, {}, {}, {}) == "completed_with_errors"

    def test_valid_empty_meta_result_is_success(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        monkeypatch.setattr(se_mod.settings, "ENABLE_META_EXTRACTION", True)
        executor = se_mod.StageExecutor(1, [1], _FakeCheckpointManager(), None)
        executor._meta_extraction = {
            "executed": True,
            "succeeded": None,
            "error": None,
            "error_code": None,
            "retry_available": False,
        }
        monkeypatch.setattr(executor, "_save_meta_result", lambda _result: True)

        executor._complete_meta_extraction({})

        assert executor._meta_extraction == {
            "executed": True,
            "succeeded": True,
            "error": None,
            "error_code": None,
            "retry_available": False,
        }

    def test_execute_stage2_publishes_real_partial_completion_payload(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        fake_client = MagicMock()
        monkeypatch.setattr(
            se_mod.ProgressPublisher,
            "_create_redis_client",
            lambda self: fake_client,
        )
        monkeypatch.setattr(
            se_mod.ProgressPublisher,
            "_build_novel_summary",
            lambda self, novel_id, chapters_count: {
                "id": novel_id,
                "chapters_count": chapters_count,
            },
        )

        executor = se_mod.StageExecutor(7, [1, 2], _FakeCheckpointManager(), "cid", job_id=33)
        monkeypatch.setattr(executor, "_check_stage2_completion", lambda: (False, False, False))
        monkeypatch.setattr(
            executor,
            "_execute_parallel_stages",
            lambda _stage2a_done, _stage2c_done: (
                {"stories_count": 3, "storylines_count": 1, "failed_stories": []},
                {"created_count": 2, "updated_count": 0, "failed_count": 0},
            ),
        )
        monkeypatch.setattr(
            executor,
            "_execute_relationship_stage",
            lambda _stage2b_done: {
                "relationships_count": 5,
            },
        )
        monkeypatch.setattr(executor, "_update_stage2_checkpoints", lambda *_args: None)
        monkeypatch.setattr(executor, "_sync_job_stage", lambda *_args, **_kwargs: None)
        monkeypatch.setattr(executor, "_update_job_status", lambda *_args: None)
        monkeypatch.setattr(executor, "_get_job_id", lambda: 33)
        monkeypatch.setattr(executor, "_save_final_checkpoint", lambda *_args: None)

        result = executor.execute_stage2(
            stage1_result={
                "summaries_count": 2,
                "plots_count": 4,
                "failed_count": 1,
                "failed_chapters": [2],
                "meta_extraction": {
                    "executed": True,
                    "succeeded": False,
                    "error": "Metadata extraction failed",
                    "error_code": "ERR_MATERIAL_META_EXTRACTION_FAILED",
                    "retry_available": True,
                },
            }
        )

        channel, serialized = fake_client.publish.call_args_list[-1].args
        payload = json.loads(serialized)
        assert result["status"] == "completed_with_errors"
        assert channel == "ingestion:cid"
        assert payload["type"] == "completed"
        assert payload["status"] == "completed_with_errors"
        assert payload["job_id"] == 33
        assert payload["summaries_count"] == 2
        assert payload["relationships_count"] == 5
        assert payload["novel_summary"] == {"id": 7, "chapters_count": 2}
        assert result["meta_extraction"] == {
            "executed": True,
            "succeeded": False,
            "error": "Metadata extraction failed",
            "error_code": "ERR_MATERIAL_META_EXTRACTION_FAILED",
            "retry_available": True,
        }

    def test_parse_cp_data_handles_variants(self):
        assert se_mod._parse_cp_data(None) == {}
        assert se_mod._parse_cp_data(SimpleNamespace(checkpoint_data={"a": 1})) == {"a": 1}
        assert se_mod._parse_cp_data(SimpleNamespace(checkpoint_data='{"a": 1}')) == {"a": 1}
        assert se_mod._parse_cp_data(SimpleNamespace(checkpoint_data="bad-json")) == {}

    def test_check_stage2_completion_with_feature_flags(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)

        checkpoints = {
            "stage2a": SimpleNamespace(stage_status="completed", checkpoint_data={"executed_capabilities": ["synopsis", "stories", "storylines"], "synopsis_generated": True, "stories_count": 2, "storylines_count": 1}),
            "stage2b": SimpleNamespace(stage_status="completed", checkpoint_data={"executed_capabilities": ["relationships"], "relationships_count": 2}),
            "stage2c": SimpleNamespace(stage_status="completed", checkpoint_data={"executed_capabilities": ["characters"], "characters_built": True}),
        }
        cp = _FakeCheckpointManager(checkpoints)

        monkeypatch.setattr(se_mod.settings, "ENABLE_NOVEL_SYNOPSIS", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORY_AGGREGATION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORYLINE_GENERATION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", True)

        executor = se_mod.StageExecutor(1, [1, 2], cp, None)
        assert executor._check_stage2_completion() == (True, True, True)

    def test_check_stage2_completion_allows_zero_count_completed_checkpoints(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)

        checkpoints = {
            "stage2a": SimpleNamespace(stage_status="completed", checkpoint_data={"executed_capabilities": ["stories", "storylines"], "stories_count": 0, "storylines_count": 0}),
            "stage2b": SimpleNamespace(stage_status="completed", checkpoint_data={"executed_capabilities": ["relationships"], "relationships_count": 0}),
            "stage2c": SimpleNamespace(stage_status="completed", checkpoint_data={"executed_capabilities": ["characters"], "characters_built": True}),
        }
        cp = _FakeCheckpointManager(checkpoints)

        monkeypatch.setattr(se_mod.settings, "ENABLE_NOVEL_SYNOPSIS", False)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORY_AGGREGATION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORYLINE_GENERATION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", True)

        executor = se_mod.StageExecutor(1, [1, 2], cp, None)
        assert executor._check_stage2_completion() == (True, True, True)

    def test_markerless_legacy_zero_count_checkpoint_is_not_done(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        checkpoints = {
            "stage2a": SimpleNamespace(
                stage_status="completed",
                checkpoint_data={
                    "synopsis_generated": False,
                    "stories_count": 0,
                    "storylines_count": 0,
                },
            ),
            "stage2b": SimpleNamespace(
                stage_status="completed",
                checkpoint_data={
                    "relationships_count": 0,
                },
            ),
            "stage2c": SimpleNamespace(
                stage_status="completed",
                checkpoint_data={"characters_built": True},
            ),
        }
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_NOVEL_SYNOPSIS", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORY_AGGREGATION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORYLINE_GENERATION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", True)

        executor = se_mod.StageExecutor(1, [1], _FakeCheckpointManager(checkpoints), None)
        assert executor._check_stage2_completion() == (False, False, False)

    def test_check_stage2_completion_requires_current_executed_capabilities(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        cp = _FakeCheckpointManager({
            "stage2a": SimpleNamespace(stage_status="completed", checkpoint_data={"executed_capabilities": ["synopsis"]}),
            "stage2b": SimpleNamespace(stage_status="completed", checkpoint_data={"disabled": True}),
            "stage2c": SimpleNamespace(stage_status="completed", checkpoint_data={"executed_capabilities": []}),
        })
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_NOVEL_SYNOPSIS", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORY_AGGREGATION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORYLINE_GENERATION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", True)

        executor = se_mod.StageExecutor(1, [1], cp, None, job_id=44)
        assert executor._check_stage2_completion() == (False, False, False)

    @pytest.mark.parametrize(
        ("stage", "payload"),
        [
            (
                "stage2a",
                {
                    "status": "completed_with_errors",
                    "executed_capabilities": ["synopsis", "stories"],
                    "failed_stories": ["story-1"],
                },
            ),
            (
                "stage2b",
                {
                    "status": "completed_with_errors",
                    "executed_capabilities": ["relationships"],
                },
            ),
            (
                "stage2c",
                {
                    "status": "completed_with_errors",
                    "executed_capabilities": ["characters"],
                    "failed_count": 1,
                },
            ),
        ],
    )
    def test_partial_stage2_checkpoint_is_not_done(self, monkeypatch, stage, payload):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_NOVEL_SYNOPSIS", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORY_AGGREGATION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORYLINE_GENERATION", False)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", True)
        complete = {
            "stage2a": {"executed_capabilities": ["synopsis", "stories"]},
            "stage2b": {"executed_capabilities": ["relationships"]},
            "stage2c": {"executed_capabilities": ["characters"]},
        }
        complete[stage] = payload
        cp = _FakeCheckpointManager(
            {
                name: SimpleNamespace(stage_status="completed", checkpoint_data=data)
                for name, data in complete.items()
            }
        )

        done = se_mod.StageExecutor(1, [1], cp, None)._check_stage2_completion()
        index = {"stage2a": 0, "stage2b": 1, "stage2c": 2}[stage]
        assert done[index] is False

    def test_partial_stage2_results_are_not_marked_completed(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_NOVEL_SYNOPSIS", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORY_AGGREGATION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", True)
        cp = _FakeCheckpointManager()
        executor = se_mod.StageExecutor(1, [1], cp, None)

        executor._update_stage2_checkpoints(
            {
                "status": "completed_with_errors",
                "failed_stories": ["story-1"],
                "executed_capabilities": ["stories"],
                "_executed": True,
            },
            {
                "status": "completed_with_errors",
                "executed_capabilities": ["relationships"],
                "_executed": True,
            },
            {
                "status": "completed_with_errors",
                "failed_count": 1,
                "failed_characters": ["A"],
                "executed_capabilities": ["characters"],
                "_executed": True,
            },
        )

        assert cp.completed_calls == []
        assert [(stage, status) for stage, status, *_ in cp.update_calls] == [
            ("stage2a", "failed"),
            ("stage2b", "failed"),
            ("stage2c", "failed"),
            ("stage2", "failed"),
        ]

    def test_execute_parallel_stages_runs_subflows_when_not_done(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)

        story_task = FakeTask({"stories_count": 2, "storylines_count": 1, "synopsis_generated": True})
        character_task = FakeTask({"created_count": 4, "updated_count": 1, "failed_count": 0})
        monkeypatch.setattr(se_mod, "_task_run_story_aggregate", story_task)
        monkeypatch.setattr(se_mod, "_task_run_character_entity_build", character_task)

        executor = se_mod.StageExecutor(9, [11, 12], _FakeCheckpointManager(), "cid")
        story_result, character_result = executor._execute_parallel_stages(False, False)

        assert story_result["stories_count"] == 2
        assert character_result["created_count"] == 4
        assert len(story_task.submit_calls) == 1
        assert len(character_task.submit_calls) == 1

    def test_execute_parallel_stages_uses_checkpoint_when_done(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)

        checkpoints = {
            "stage2a": SimpleNamespace(stage_status="completed", checkpoint_data={"synopsis_generated": True, "stories_count": 3, "storylines_count": 5, "status": "completed"}),
            "stage2c": SimpleNamespace(stage_status="completed", checkpoint_data={"created_count": 1, "updated_count": 2, "failed_count": 0, "failed_characters": [], "status": "completed"}),
        }
        executor = se_mod.StageExecutor(1, [1], _FakeCheckpointManager(checkpoints), None)

        story_result, character_result = executor._execute_parallel_stages(True, True)

        assert story_result["stories_count"] == 3
        assert character_result["updated_count"] == 2

    def test_execute_relationship_stage_uses_subflow_or_checkpoint(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        monkeypatch.setattr(se_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", True)

        relation_task = FakeTask({"relationships_count": 6, "status": "completed"})
        monkeypatch.setattr(se_mod, "_task_run_relationship", relation_task)

        checkpoints = {
            "stage2b": SimpleNamespace(stage_status="completed", checkpoint_data={"relationships_count": 9, "status": "completed_with_errors"})
        }
        executor = se_mod.StageExecutor(1, [1, 2], _FakeCheckpointManager(checkpoints), None)

        fresh = executor._execute_relationship_stage(False)
        resumed = executor._execute_relationship_stage(True)

        assert fresh["relationships_count"] == 6
        assert resumed["relationships_count"] == 9

    def test_disabled_stage2_ignores_historic_partial_failures(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        monkeypatch.setattr(se_mod.settings, "ENABLE_NOVEL_SYNOPSIS", False)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORY_AGGREGATION", False)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORYLINE_GENERATION", False)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHARACTER_EXTRACTION", False)
        monkeypatch.setattr(se_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", False)
        cp = _FakeCheckpointManager(
            {
                "stage2a": SimpleNamespace(
                    stage_status="failed",
                    checkpoint_data={"failed_stories": ["old"], "status": "completed_with_errors"},
                ),
                "stage2b": SimpleNamespace(
                    stage_status="failed",
                    checkpoint_data={"status": "completed_with_errors"},
                ),
                "stage2c": SimpleNamespace(
                    stage_status="failed",
                    checkpoint_data={"failed_count": 1, "failed_characters": ["old"], "status": "completed_with_errors"},
                ),
            }
        )
        executor = se_mod.StageExecutor(1, [1], cp, None)

        story, characters = executor._execute_parallel_stages(True, True)
        relationships = executor._execute_relationship_stage(True)

        assert story == {"status": "skipped", "_executed": False, "executed_capabilities": []}
        assert characters == {"status": "skipped", "_executed": False, "executed_capabilities": []}
        assert relationships == {"status": "skipped", "_executed": False, "executed_capabilities": []}
        assert executor._derive_final_status({}, story, relationships, characters) == "completed"

    def test_update_stage2_checkpoints_writes_all_stages(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_NOVEL_SYNOPSIS", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_STORY_AGGREGATION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)
        monkeypatch.setattr(se_mod.settings, "ENABLE_RELATIONSHIP_EXTRACTION", True)

        cp = _FakeCheckpointManager()
        executor = se_mod.StageExecutor(1, [1, 2], cp, None)
        executor._update_stage2_checkpoints(
            {"synopsis_generated": True, "stories_count": 2, "storylines_count": 3, "failed_stories": [], "status": "completed"},
            {"relationships_count": 4, "status": "completed_with_errors"},
            {"created_count": 5, "updated_count": 1, "failed_count": 0, "failed_characters": [], "status": "completed"},
        )

        stages = [s for s, _ in cp.completed_calls]
        assert stages == ["stage2a", "stage2c"]
        assert [(stage, status) for stage, status, *_ in cp.update_calls] == [
            ("stage2b", "failed"),
            ("stage2", "failed"),
        ]

    def test_save_final_checkpoint_collects_final_metrics(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)

        cp = _FakeCheckpointManager()
        executor = se_mod.StageExecutor(3, [10, 20], cp, None)
        executor._save_final_checkpoint(
            stage1_result={"summaries_count": 2, "plots_count": 7, "mentions_extracted": True, "failed_count": 1, "failed_chapters": [9], "failed_mention_chapters": [10]},
            story_result={"synopsis_generated": True, "stories_count": 2, "storylines_count": 1, "failed_stories": ["s1"]},
            relationship_result={"relationships_count": 4},
            character_entity_result={"created_count": 6, "updated_count": 2, "failed_count": 1, "failed_characters": ["赵四"]},
        )

        assert cp.completed_calls
        stage, payload = cp.completed_calls[0]
        assert stage == "completed"
        assert payload["novel_id"] == 3
        assert payload["chapters_count"] == 2
        assert payload["characters_created"] == 6
        assert payload["failed_chapters"] == [9]
        assert payload["relationships_count"] == 4

    def test_derive_final_status_marks_completed_with_errors(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)

        executor = se_mod.StageExecutor(3, [10, 20], _FakeCheckpointManager(), None)
        status = executor._derive_final_status(
            {"failed_count": 1},
            {"failed_stories": []},
            {"status": "completed"},
            {"failed_count": 0},
        )

        assert status == "completed_with_errors"

    def test_update_job_status_uses_completed_with_errors(self, monkeypatch):
        monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
        monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)

        captured = {}

        class _Svc:
            def get_latest_by_novel(self, _session, _novel_id):
                return SimpleNamespace(id=42)

            def update_processed(self, _session, job_id, **kwargs):
                captured["job_id"] = job_id
                captured["kwargs"] = kwargs

        class _SessionCtx:
            def __enter__(self):
                return SimpleNamespace(commit=lambda: None)

            def __exit__(self, exc_type, exc, tb):
                return False

        monkeypatch.setattr(se_mod, "get_prefect_db_session", lambda: _SessionCtx())
        monkeypatch.setitem(__import__("sys").modules, "services.material.ingestion_jobs_service", SimpleNamespace(IngestionJobsService=_Svc))

        executor = se_mod.StageExecutor(3, [10, 20, 30], _FakeCheckpointManager(), None)
        executor._update_job_status(
            "completed_with_errors",
            {
                "processed_chapters": 2,
                "failed_count": 2,
                "failed_chapters": [1],
                "failed_mention_chapters": [2],
                "meta_extraction": {
                    "executed": True,
                    "succeeded": False,
                    "error": "Metadata extraction failed",
                    "error_code": "ERR_MATERIAL_META_EXTRACTION_FAILED",
                    "retry_available": True,
                },
            },
            {"failed_stories": ["story-1"]},
            {"failed_count": 1, "failed_characters": ["赵四"]},
        )

        assert captured["job_id"] == 42
        assert captured["kwargs"]["status"] == "completed_with_errors"
        assert captured["kwargs"]["processed_chapters"] == 2
        assert captured["kwargs"]["stage_data"]["meta_extraction"]["error_code"] == (
            "ERR_MATERIAL_META_EXTRACTION_FAILED"
        )
