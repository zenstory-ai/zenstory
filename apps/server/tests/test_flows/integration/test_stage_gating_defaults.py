"""
End-to-end stage gating with the token-saving default flags.

Runs StageExecutor.execute_stage1/execute_stage2 with the real
chapter_extraction_flow / story_aggregate_flow bodies (``.fn``) and a real
IngestionJob object, stubbing only DB sessions and LLM-backed tasks. With the
defaults (plots / story aggregation / storylines off) no plot, story, orphan,
storyline or relationship work may run, no LLM call may leak, and the job must
finish as ``completed`` with the enabled-stage snapshot kept in stage_progress.
"""
from __future__ import annotations

import importlib
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from config.material_settings import MaterialSettings
from flows.pipelines.stages import stage_executor as se_mod
from models.material_models import IngestionJob
from tests.test_flows.conftest import FakeFuture, FakeMonitor, FakeTask

chapter_mod = importlib.import_module("flows.pipelines.subflows.chapter_extraction_flow")
story_mod = importlib.import_module("flows.pipelines.subflows.story_aggregate_flow")
llm_mod = importlib.import_module("flows.utils.clients.llm")
db_mod = importlib.import_module("flows.database_session")
entities_mod = importlib.import_module("flows.atomic_tasks.entities")

_FLAG_NAMES = (
    "ENABLE_CHAPTER_SUMMARIES",
    "ENABLE_PLOT_EXTRACTION",
    "ENABLE_CHARACTER_EXTRACTION",
    "ENABLE_META_EXTRACTION",
    "ENABLE_ENTITY_EXTRACTION",
    "ENABLE_NOVEL_SYNOPSIS",
    "ENABLE_STORY_AGGREGATION",
    "ENABLE_STORYLINE_GENERATION",
    "ENABLE_RELATIONSHIP_EXTRACTION",
)


class _DummyPublisher:
    def __init__(self, _correlation_id, _logger):
        self.events = []
        self.completions = []

    def publish(self, event_type: str, **kwargs):
        self.events.append((event_type, kwargs))

    def publish_completion(self, novel_id, chapter_ids, result):
        self.completions.append((novel_id, chapter_ids, result))


class _FakeCheckpointManager:
    def get_checkpoint(self, _stage):
        return None

    def get_pending_chapters(self, _stage, all_chapter_ids, capability="summaries"):
        return all_chapter_ids

    def get_completed_chapters(self, _stage, capability="summaries"):
        return []

    def get_failed_chapters(self, _stage, capability="summaries"):
        return []

    def mark_stage_completed(self, _stage, _data):
        return None

    def update_checkpoint(self, *_args, **_kwargs):
        return None


class _FakeSession:
    """Minimal Session stand-in so the real IngestionJobsService can mutate the job."""

    def __init__(self, job: IngestionJob):
        self.job = job

    def get(self, _model, job_id):
        return self.job if job_id == self.job.id else None

    def exec(self, statement):
        if any(table.name == "chapters" for table in statement.get_final_froms()):
            rows = [
                SimpleNamespace(id=cid, chapter_number=i + 1, title=f"c{i}", summary="s")
                for i, cid in enumerate([101, 102])
            ]
            return SimpleNamespace(all=lambda: rows)
        return SimpleNamespace(first=lambda: self.job, all=lambda: [self.job])

    def add(self, _obj):
        return None

    def flush(self):
        return None

    def commit(self):
        return None


class _Forbidden:
    """Sentinel for tasks that must not run when their stage is disabled."""

    def __init__(self, name: str, calls: list[str]):
        self.name = name
        self.calls = calls

    def __call__(self, *args, **kwargs):
        self.calls.append(self.name)
        raise AssertionError(f"{self.name} must not run")

    def submit(self, *args, **kwargs):
        return self(*args, **kwargs)


class _SummaryTask:
    def __init__(self):
        self.chapter_ids: list[int] = []

    def submit(self, chapter_id):
        self.chapter_ids.append(chapter_id)
        return FakeFuture({"chapter_id": chapter_id, "summary": f"summary {chapter_id}"})


@pytest.fixture
def default_flags(monkeypatch):
    """Apply the shipped defaults (ignoring any local .env / MATERIAL_* env vars)."""
    for name in _FLAG_NAMES:
        monkeypatch.delenv(f"MATERIAL_{name}", raising=False)
    defaults = MaterialSettings(_env_file=None)
    for name in _FLAG_NAMES:
        monkeypatch.setattr(se_mod.settings, name, getattr(defaults, name))


@pytest.mark.integration
def test_default_stages_skip_plot_and_story_work_and_complete(monkeypatch, default_flags):
    novel_id = 7
    chapter_ids = [101, 102]
    job = IngestionJob(id=1, novel_id=novel_id, source_path="novel.txt", status="processing", total_chapters=2)
    session = _FakeSession(job)

    @contextmanager
    def _session_ctx():
        yield session

    forbidden_calls: list[str] = []
    llm_calls: list[object] = []

    # --- infrastructure stubs ---
    monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
    monkeypatch.setattr(se_mod, "get_prefect_db_session", _session_ctx)
    monkeypatch.setattr(db_mod, "get_prefect_db_session", _session_ctx)

    # Any leaked LLM call (direct client or call_deepseek_api) is recorded.
    fake_client = object.__new__(llm_mod.DeepSeekClient)
    monkeypatch.setattr(llm_mod, "_deepseek_client", fake_client)
    monkeypatch.setattr(
        llm_mod.DeepSeekClient,
        "chat_completion",
        lambda self, *args, **kwargs: llm_calls.append(args) or (_ for _ in ()).throw(AssertionError("LLM called")),
    )

    # --- stage 1 (chapter_extraction_flow body) ---
    summary_task = _SummaryTask()
    mention_task = FakeTask({"chapter_id": 0, "mentions": ["A"]})
    monkeypatch.setattr(chapter_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(chapter_mod, "create_performance_monitor", lambda _name: FakeMonitor())
    monkeypatch.setattr(chapter_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: _FakeCheckpointManager())
    monkeypatch.setattr(chapter_mod, "generate_chapter_summary_task", summary_task)
    monkeypatch.setattr(chapter_mod, "update_chapter_summary_task", FakeTask(None))
    monkeypatch.setattr(chapter_mod, "extract_character_mentions_task", mention_task)
    for name in ("extract_chapter_plots_task", "validate_plots_task", "save_plots_task"):
        monkeypatch.setattr(chapter_mod, name, _Forbidden(name, forbidden_calls))
    monkeypatch.setattr(chapter_mod, "chapter_extraction_flow", chapter_mod.chapter_extraction_flow.fn)

    meta_task = FakeTask({"golden_fingers": [], "worldview": {}})
    monkeypatch.setattr(entities_mod, "extract_novel_meta_task", meta_task)

    # --- stage 2A (story_aggregate_flow body: synopsis only) ---
    synopsis_calls: list[int] = []

    monkeypatch.setattr(story_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(story_mod, "create_performance_monitor", lambda _name: FakeMonitor())
    monkeypatch.setattr(
        story_mod,
        "create_checkpoint_manager",
        lambda _novel_id, job_id=None: _FakeCheckpointManager(),
    )
    monkeypatch.setattr(story_mod, "get_db_session", _session_ctx)
    monkeypatch.setattr(
        story_mod,
        "generate_novel_synopsis_task",
        lambda novel_id, chapter_summaries: synopsis_calls.append(novel_id) or {"synopsis": "whole book"},
    )
    monkeypatch.setattr(story_mod, "update_novel_synopsis_task", lambda novel_id, synopsis: None)
    for name in (
        "identify_story_frameworks_with_chunking",
        "aggregate_plots_to_stories_task",
        "save_stories_task",
        "handle_orphan_plots_task",
        "generate_storylines_task",
        "save_storylines_task",
    ):
        monkeypatch.setattr(story_mod, name, _Forbidden(name, forbidden_calls))

    class _StoryWrapper:
        def submit(self, nid, cids, _cid, job_id=None):
            return FakeFuture(
                story_mod.story_aggregate_flow.fn(
                    novel_id=nid,
                    chapter_ids=cids,
                    job_id=job_id,
                )
            )

    character_task = FakeTask(
        {"created_count": 2, "updated_count": 0, "failed_count": 0, "failed_characters": [], "status": "completed"}
    )
    monkeypatch.setattr(se_mod, "_task_run_story_aggregate", _StoryWrapper())
    monkeypatch.setattr(se_mod, "_task_run_character_entity_build", character_task)
    monkeypatch.setattr(se_mod, "_task_run_relationship", _Forbidden("relationship_flow", forbidden_calls))

    # --- run ---
    executor = se_mod.StageExecutor(novel_id, chapter_ids, _FakeCheckpointManager(), "cid")
    monkeypatch.setattr(executor, "_save_meta_result", lambda _meta: None)

    snapshot = executor.record_enabled_stages()
    stage1 = executor.execute_stage1()
    final = executor.execute_stage2(stage1, 0.0)

    # --- assertions ---
    assert snapshot == {
        "chapter_summaries": True,
        "plots": False,
        "characters": True,
        "meta": True,
        "synopsis": True,
        "stories": False,
        "storylines": False,
        "relationships": False,
    }
    assert forbidden_calls == []
    assert llm_calls == []
    assert summary_task.chapter_ids == chapter_ids
    assert len(mention_task.submit_calls) == 2
    assert len(meta_task.submit_calls) == 1
    assert synopsis_calls == [novel_id]
    assert len(character_task.submit_calls) == 1

    assert stage1["plots_count"] == 0
    assert stage1["failed_count"] == 0
    assert final["status"] == "completed"
    assert final["plots_count"] == 0
    assert final["stories_count"] == 0
    assert final["storylines_count"] == 0

    assert job.status == "completed"
    assert job.processed_chapters == 2
    progress = json.loads(job.stage_progress)
    assert progress["enabled_stages"] == snapshot
    assert progress["completed"]["status"] == "completed"


@pytest.mark.integration
def test_record_enabled_stages_survives_missing_job(monkeypatch, default_flags):
    @contextmanager
    def _session_ctx():
        yield SimpleNamespace(exec=lambda _s: SimpleNamespace(first=lambda: None))

    monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
    monkeypatch.setattr(se_mod, "get_prefect_db_session", _session_ctx)

    executor = se_mod.StageExecutor(1, [1], _FakeCheckpointManager(), None)

    assert executor.record_enabled_stages()["plots"] is False


@pytest.mark.integration
def test_record_enabled_stages_warns_when_explicit_stage_dropped(monkeypatch, default_flags):
    """An explicitly requested stage dropped by its dependencies is logged at WARNING on every run."""
    @contextmanager
    def _session_ctx():
        yield SimpleNamespace(exec=lambda _s: SimpleNamespace(first=lambda: None))

    run_logger = MagicMock()
    monkeypatch.setattr(se_mod, "get_run_logger", lambda: run_logger)
    monkeypatch.setattr(se_mod, "ProgressPublisher", _DummyPublisher)
    monkeypatch.setattr(se_mod, "get_prefect_db_session", _session_ctx)
    # Storylines requested explicitly while story aggregation stays off (default).
    monkeypatch.setattr(se_mod.settings, "ENABLE_STORYLINE_GENERATION", True)

    executor = se_mod.StageExecutor(1, [1], _FakeCheckpointManager(), None)
    assert executor.record_enabled_stages()["storylines"] is False

    messages = [c.args[0] % c.args[1:] for c in run_logger.warning.call_args_list]
    assert any("storylines: requires stories" in m for m in messages), messages
