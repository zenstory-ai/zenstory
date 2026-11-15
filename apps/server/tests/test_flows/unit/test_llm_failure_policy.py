"""Failure policy for material decomposition: LLM errors, retries, codes and refunds."""
from __future__ import annotations

import importlib
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import httpx
import openai
import pytest
from prefect.states import Failed
from sqlmodel import select

from core.error_codes import ErrorCode
from flows import database_session as dbs
from flows.atomic_tasks.entities.character_tasks_v2 import extract_character_mentions_task
from flows.atomic_tasks.summaries.chapter_summary_tasks import generate_chapter_summary_task
from flows.utils.clients import llm as llm_mod
from flows.utils.decorators.prefect import retry_only_transient_failures
from flows.utils.helpers.exceptions import (
    LLMAPIError,
    LLMNonRetryableError,
    LLMOutputError,
    ValidationError,
)
from services.material.job_errors import MaterialPipelineError
from tests.test_flows.conftest import FakeFuture, FakeMonitor, FakeTask

chapter_mod = importlib.import_module("flows.pipelines.subflows.chapter_extraction_flow")
flow_mod = importlib.import_module("flows.pipelines.novel_ingestion_v3_flow")


def _status_error(cls, status_code: int):
    response = httpx.Response(status_code, request=httpx.Request("POST", "https://llm.test"))
    return cls(f"status {status_code}", response=response, body=None)


def _client_raising(error: Exception) -> llm_mod.MaterialLLMClient:
    client = llm_mod.MaterialLLMClient.__new__(llm_mod.MaterialLLMClient)

    def _raise(*_args, **_kwargs):
        raise error

    client._call_model = _raise
    return client


# ---------------------------------------------------------------- LLM client


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (_status_error(openai.AuthenticationError, 401), ErrorCode.MATERIAL_LLM_UNAVAILABLE),
        (_status_error(openai.APIStatusError, 402), ErrorCode.MATERIAL_LLM_UNAVAILABLE),
        (_status_error(openai.PermissionDeniedError, 403), ErrorCode.MATERIAL_LLM_UNAVAILABLE),
        (_status_error(openai.BadRequestError, 400), None),  # e.g. context too long
    ],
)
def test_non_retryable_llm_statuses_are_classified(error, expected_code):
    with pytest.raises(LLMNonRetryableError) as raised:
        _client_raising(error).chat_completion(messages=[])
    assert raised.value.error_code == expected_code


@pytest.mark.unit
@pytest.mark.parametrize(
    "error",
    [
        _status_error(openai.InternalServerError, 503),
        _status_error(openai.RateLimitError, 429),
        openai.APIConnectionError(request=httpx.Request("POST", "https://llm.test")),
    ],
)
def test_transient_llm_failures_stay_retryable(error):
    with pytest.raises(LLMAPIError) as raised:
        _client_raising(error).chat_completion(messages=[])
    assert not isinstance(raised.value, LLMNonRetryableError)
    assert retry_only_transient_failures(None, None, Failed(data=raised.value)) is True


@pytest.mark.unit
def test_client_sets_explicit_timeout_and_sdk_retries(monkeypatch):
    from agent.core import llm_providers

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    llm_providers.reset_provider_client_cache()
    try:
        sdk_client = llm_providers.get_sync_client(
            llm_providers.primary_provider(),
            timeout=llm_mod.settings.LLM_REQUEST_TIMEOUT_SECONDS,
            max_retries=llm_mod.settings.LLM_SDK_MAX_RETRIES,
        )
        assert sdk_client.timeout == llm_mod.settings.LLM_REQUEST_TIMEOUT_SECONDS
        assert sdk_client.max_retries == llm_mod.settings.LLM_SDK_MAX_RETRIES
    finally:
        llm_providers.reset_provider_client_cache()


@pytest.mark.unit
def test_missing_api_key_is_an_account_failure(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(LLMNonRetryableError) as raised:
        llm_mod.MaterialLLMClient()
    assert raised.value.error_code == ErrorCode.MATERIAL_LLM_UNAVAILABLE


@pytest.mark.unit
def test_json_with_literal_newlines_in_strings_is_parsed():
    client = llm_mod.MaterialLLMClient.__new__(llm_mod.MaterialLLMClient)
    content = '{"synopsis": "第一段：少年入山。\n\n第二段：宗门试炼。"}'
    response = llm_mod.LLMResponse(content=content, usage={}, model="m", finish_reason="stop")

    data = client.extract_json_from_response(response)

    assert data["synopsis"] == "第一段：少年入山。\n\n第二段：宗门试炼。"


@pytest.mark.unit
def test_unparsable_output_is_not_retried():
    client = llm_mod.MaterialLLMClient.__new__(llm_mod.MaterialLLMClient)
    response = llm_mod.LLMResponse(content="not json", usage={}, model="m", finish_reason="length")

    with pytest.raises(LLMOutputError) as raised:
        client.extract_json_from_response(response)

    assert retry_only_transient_failures(None, None, Failed(data=raised.value)) is False


@pytest.mark.unit
def test_synopsis_prompt_asks_for_escaped_newlines():
    from pathlib import Path

    templates = Path(__file__).resolve().parents[3] / "prompts" / "templates"
    for name in ("web_long/novel_synopsis.j2", "web_short/novel_synopsis.j2"):
        text = (templates / name).read_text(encoding="utf-8")
        assert "解析端已兼容" not in text
        assert "\\n 表示换行" in text


# ---------------------------------------------------------------- task retries


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "should_retry"),
    [
        (LLMNonRetryableError("402", ErrorCode.MATERIAL_LLM_UNAVAILABLE), False),
        (LLMNonRetryableError("context too long"), False),
        (ValidationError("摘要过短"), False),
        (MaterialPipelineError(ErrorCode.MATERIAL_NO_CHAPTERS), False),
        (LLMAPIError("timeout"), True),
        (RuntimeError("db hiccup"), True),
    ],
)
def test_retry_condition(error, should_retry):
    assert retry_only_transient_failures(None, None, Failed(data=error)) is should_retry


@pytest.mark.unit
def test_llm_tasks_use_the_retry_condition():
    for task in (generate_chapter_summary_task, extract_character_mentions_task):
        assert task.retry_condition_fn is retry_only_transient_failures


# ---------------------------------------------------------------- stage 1


class _Checkpoint:
    def get_pending_chapters(self, _stage, all_chapter_ids, capability="summaries"):
        return all_chapter_ids

    def get_completed_chapters(self, _stage, capability="summaries"):
        return []

    def get_failed_chapters(self, _stage, capability="summaries"):
        return []

    def update_checkpoint(self, *_args, **_kwargs):
        return None


def _patch_stage1(monkeypatch, *, summaries: bool, mentions: bool):
    monkeypatch.setattr(chapter_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(chapter_mod, "create_performance_monitor", lambda _name: FakeMonitor())
    monkeypatch.setattr(
        chapter_mod, "create_checkpoint_manager", lambda _novel_id, job_id=None: _Checkpoint()
    )
    monkeypatch.setattr(chapter_mod, "_sync_stage1_job_progress", lambda *a, **k: None)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHAPTER_SUMMARIES", summaries)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_PLOT_EXTRACTION", False)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHARACTER_EXTRACTION", mentions)


@pytest.mark.unit
@pytest.mark.parametrize(
    "fatal_code",
    [
        ErrorCode.MATERIAL_LLM_UNAVAILABLE,
        ErrorCode.MATERIAL_TRIAL_COST_LIMIT,
        ErrorCode.QUOTA_AI_DAILY_COST_EXCEEDED,
        ErrorCode.AI_COST_BUDGET_UNAVAILABLE,
        ErrorCode.MATERIAL_TRIAL_LIMIT_INVALID,
    ],
)
def test_llm_fatal_stop_aborts_extraction_preserving_original_error(monkeypatch, fatal_code):
    from flows.utils.helpers.exceptions import is_llm_account_failure

    _patch_stage1(monkeypatch, summaries=True, mentions=False)
    fatal_error = LLMNonRetryableError("fatal stop", fatal_code)
    assert is_llm_account_failure(fatal_error) is True
    assert is_llm_account_failure(LLMNonRetryableError("400 bad request", None)) is False
    assert is_llm_account_failure(LLMOutputError("bad json")) is False

    monkeypatch.setattr(chapter_mod, "generate_chapter_summary_task", FakeTask(error=fatal_error))
    writes = FakeTask({})
    monkeypatch.setattr(chapter_mod, "update_chapter_summary_task", writes)

    with pytest.raises(LLMNonRetryableError) as raised:
        chapter_mod.chapter_extraction_flow.fn(novel_id=1, chapter_ids=[10, 20, 30], job_id=1)

    assert raised.value is fatal_error
    assert raised.value.error_code == fatal_code
    assert writes.submit_calls == []


@pytest.mark.unit
def test_story_aggregate_flow_aborts_on_fatal_stop_and_total_failure(monkeypatch):
    story_mod = importlib.import_module("flows.pipelines.subflows.story_aggregate_flow")
    monkeypatch.setattr(story_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(story_mod, "create_performance_monitor", lambda _name: FakeMonitor())
    monkeypatch.setattr(
        story_mod, "create_checkpoint_manager", lambda *_a, **_k: MagicMock()
    )
    monkeypatch.setattr(story_mod.settings, "ENABLE_NOVEL_SYNOPSIS", False)
    monkeypatch.setattr(story_mod.settings, "ENABLE_PLOT_EXTRACTION", True)
    monkeypatch.setattr(story_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
    monkeypatch.setattr(story_mod.settings, "ENABLE_STORY_AGGREGATION", True)
    monkeypatch.setattr(story_mod.settings, "ENABLE_STORYLINE_GENERATION", False)
    monkeypatch.setattr(story_mod.settings, "MAX_CONCURRENT_WORKFLOWS", 1)
    monkeypatch.setattr(
        story_mod,
        "identify_story_frameworks_with_chunking",
        lambda **_k: {
            "story_frameworks": [{"title": "S1"}, {"title": "S2"}],
            "chunking_info": {"chunking_strategy": "single", "chunk_count": 1},
        },
    )

    fatal_err = LLMNonRetryableError("trial cap", ErrorCode.MATERIAL_TRIAL_COST_LIMIT)
    submitted: list[str] = []

    class _AggTaskFatal:
        def submit(self, *, story_framework, novel_id):
            submitted.append(story_framework["title"])
            return FakeFuture(error=fatal_err)

    # Support both old alias and direct task attribute name
    agg_attr = (
        "aggregate_plots_for_story_framework"
        if hasattr(story_mod, "aggregate_plots_for_story_framework")
        else "aggregate_plots_to_stories_task"
    )
    monkeypatch.setattr(story_mod, agg_attr, _AggTaskFatal())

    with pytest.raises(LLMNonRetryableError) as raised:
        story_mod.story_aggregate_flow.fn(novel_id=1, chapter_ids=[1, 2], job_id=1)

    assert raised.value is fatal_err
    assert submitted == ["S1"]  # batch 2 was NOT submitted after fatal stop

    # Total non-fatal failure across all story frameworks must raise instead of completed_with_errors
    monkeypatch.setattr(
        story_mod, agg_attr, FakeTask(error=LLMOutputError("malformed story json"))
    )
    with pytest.raises(MaterialPipelineError) as total_raised:
        story_mod.story_aggregate_flow.fn(novel_id=1, chapter_ids=[1, 2], job_id=1)
    assert total_raised.value.error_code == ErrorCode.MATERIAL_EXTRACTION_FAILED


@pytest.mark.unit
def test_character_entity_build_flow_aborts_on_fatal_stop_and_total_failure(monkeypatch):
    from types import SimpleNamespace

    char_mod = importlib.import_module("flows.pipelines.subflows.character_entity_build_flow")
    import services.material.character_mentions_service as mentions_mod
    import services.material.ingestion_jobs_service as jobs_mod

    monkeypatch.setattr(char_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(char_mod, "create_performance_monitor", lambda _name: FakeMonitor())
    monkeypatch.setattr(dbs, "get_db_session", lambda: _SessionCtx(MagicMock()))
    monkeypatch.setattr(jobs_mod, "executing_trial_job", lambda _db, _nid: None)

    class _FakeMentionsSvc:
        def get_by_novel(self, _db, _nid):
            return [
                SimpleNamespace(character_name="林雷", importance="major"),
                SimpleNamespace(character_name="贝贝", importance="supporting"),
            ]

    monkeypatch.setattr(mentions_mod, "CharacterMentionsService", _FakeMentionsSvc)

    fatal_err = LLMNonRetryableError("daily cap", ErrorCode.QUOTA_AI_DAILY_COST_EXCEEDED)
    monkeypatch.setattr(char_mod, "build_character_entity_task", FakeTask(error=fatal_err))

    with pytest.raises(LLMNonRetryableError) as raised:
        char_mod.character_entity_build_flow.fn(novel_id=1)
    assert raised.value is fatal_err

    # 100% failure with non-fatal output error must raise instead of completed_with_errors
    monkeypatch.setattr(
        char_mod, "build_character_entity_task", FakeTask(error=LLMOutputError("bad char json"))
    )
    with pytest.raises(MaterialPipelineError) as total_raised:
        char_mod.character_entity_build_flow.fn(novel_id=1)
    assert total_raised.value.error_code == ErrorCode.MATERIAL_EXTRACTION_FAILED


@pytest.mark.integration
def test_stage_executor_propagates_fatal_meta_terminal_db_and_ownership_errors(
    monkeypatch, db_session
):
    from flows.pipelines.stages import stage_executor as se_mod
    from models.material_models import IngestionJob, Novel

    novel = Novel(user_id="u-se", title="Mine")
    other = Novel(user_id="u-se-2", title="Other")
    db_session.add_all([novel, other])
    db_session.commit()
    foreign_job = IngestionJob(novel_id=other.id, source_path="/tmp/other.txt", status="processing")
    db_session.add(foreign_job)
    db_session.commit()

    monkeypatch.setattr(se_mod, "get_prefect_db_session", lambda: _SessionCtx(db_session))
    monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
    pub = MagicMock()
    monkeypatch.setattr(se_mod, "ProgressPublisher", lambda *_a, **_k: pub)
    cp = MagicMock()
    cp.get_checkpoint.return_value = None

    # 1. Cross-novel job ownership ValueError must NOT be swallowed by _get_job_id / _sync_job_stage
    executor_foreign = se_mod.StageExecutor(
        novel_id=novel.id,
        chapter_ids=[1],
        checkpoint_manager=cp,
        correlation_id="c1",
        job_id=foreign_job.id,
    )
    with pytest.raises(ValueError, match="不属于"):
        executor_foreign._get_job_id()
    with pytest.raises(ValueError, match="不属于"):
        executor_foreign._sync_job_stage("stage1", "processing")

    # 2. Fatal budget LLMNonRetryableError in meta extraction must NOT be overwritten
    own_job = IngestionJob(novel_id=novel.id, source_path="/tmp/mine.txt", status="processing")
    db_session.add(own_job)
    db_session.commit()

    executor = se_mod.StageExecutor(
        novel_id=novel.id,
        chapter_ids=[1],
        checkpoint_manager=cp,
        correlation_id="c2",
        job_id=own_job.id,
    )
    fatal_meta = LLMNonRetryableError("budget stop", ErrorCode.QUOTA_AI_DAILY_COST_EXCEEDED)
    monkeypatch.setattr(executor, "_handle_meta_extraction", lambda *_a, **_k: FakeFuture(error=fatal_meta))
    monkeypatch.setattr(
        chapter_mod,
        "chapter_extraction_flow",
        lambda **_k: {"summaries_count": 1, "plots_count": 1, "failed_count": 0, "status": "completed"},
    )
    with pytest.raises(LLMNonRetryableError) as meta_raised:
        executor.execute_stage1()
    assert meta_raised.value is fatal_meta

    # 3. Terminal _update_job_status DB failure must raise and NEVER publish 100% completion SSE
    monkeypatch.setattr(executor, "_check_stage2_completion", lambda: (True, True, True))
    monkeypatch.setattr(
        executor,
        "_execute_parallel_stages",
        lambda *_a: ({"stories_count": 1}, {"created_count": 1, "updated_count": 0}),
    )
    monkeypatch.setattr(
        executor, "_execute_relationship_stage", lambda *_a: {"relationships_count": 0, "status": "completed"}
    )
    monkeypatch.setattr(executor, "_sync_job_stage", lambda *_a, **_k: None)

    def _broken_db():
        raise RuntimeError("db write failed during terminal status commit")

    monkeypatch.setattr(se_mod, "get_prefect_db_session", _broken_db)
    with pytest.raises(RuntimeError, match="db write failed"):
        executor.execute_stage2(stage1_result={"summaries_count": 1, "plots_count": 1, "failed_count": 0})
    pub.publish_completion.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (LLMAPIError("503 after retries"), ErrorCode.MATERIAL_LLM_UNAVAILABLE),
        (ValidationError("摘要过短"), ErrorCode.MATERIAL_EXTRACTION_FAILED),
    ],
)
def test_all_chapters_failing_marks_extraction_failed(monkeypatch, error, expected_code):
    _patch_stage1(monkeypatch, summaries=True, mentions=False)
    writes = FakeTask({})
    monkeypatch.setattr(chapter_mod, "generate_chapter_summary_task", FakeTask(error=error))
    monkeypatch.setattr(chapter_mod, "update_chapter_summary_task", writes)

    with pytest.raises(MaterialPipelineError) as raised:
        chapter_mod.chapter_extraction_flow.fn(novel_id=1, chapter_ids=[10, 20], job_id=1)

    assert raised.value.error_code == expected_code
    assert writes.submit_calls == []


@pytest.mark.unit
def test_failed_summary_is_not_replaced_by_original_text(monkeypatch):
    _patch_stage1(monkeypatch, summaries=True, mentions=False)

    class _SummaryTask:
        def submit(self, chapter_id):
            if chapter_id == 10:
                return FakeFuture({"chapter_id": 10, "summary": "真实摘要"})
            return FakeFuture(error=LLMAPIError("timeout"))

    writes = FakeTask({})
    monkeypatch.setattr(chapter_mod, "generate_chapter_summary_task", _SummaryTask())
    monkeypatch.setattr(chapter_mod, "update_chapter_summary_task", writes)

    result = chapter_mod.chapter_extraction_flow.fn(novel_id=1, chapter_ids=[10, 20], job_id=1)

    assert [call[1] for call in writes.submit_calls] == [{"chapter_id": 10, "summary": "真实摘要"}]
    assert result["summaries_count"] == 1
    assert result["status"] == "completed_with_errors"


# ---------------------------------------------------------------- stage 0 and job failure


@pytest.mark.unit
@pytest.mark.parametrize(
    ("chapter_count", "expected_code"),
    [(0, ErrorCode.MATERIAL_NO_CHAPTERS), (3, ErrorCode.MATERIAL_TOO_MANY_CHAPTERS)],
)
def test_stage0_rejects_unusable_chapter_counts(monkeypatch, chapter_count, expected_code):
    monkeypatch.setattr(flow_mod.settings, "MAX_CHAPTERS_PER_NOVEL", 2)
    monkeypatch.setattr(
        flow_mod,
        "parse_novel_chapters",
        lambda _path, _encoding: {
            "chapters": [
                {"chapter_number": i, "title": f"c{i}", "content": "x" * 200}
                for i in range(chapter_count)
            ],
            "novel_title": "t",
        },
    )
    monkeypatch.setattr(
        flow_mod,
        "get_prefect_db_session",
        MagicMock(side_effect=AssertionError("no rows may be written")),
    )

    with pytest.raises(MaterialPipelineError) as raised:
        flow_mod._execute_stage0(
            file_path="/tmp/x.txt",
            novel_title=None,
            author=None,
            user_id="u1",
            content_hash="h",
            encoding="utf-8",
            file_size=10,
            correlation_id=None,
            logger=MagicMock(),
            publisher=MagicMock(),
            existing_novel_id=1,
            job_id=1,
        )
    assert raised.value.error_code == expected_code


class _SessionCtx:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *_exc):
        return False


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error_code", "refunded"),
    [
        (ErrorCode.MATERIAL_LLM_UNAVAILABLE, True),
        (ErrorCode.MATERIAL_EXTRACTION_FAILED, False),
    ],
)
def test_flow_failure_stores_code_and_refunds_platform_failures(
    monkeypatch, db_session, error_code, refunded
):
    from models import User
    from models.material_models import IngestionJob, Novel
    from models.subscription import UsageQuota
    from services.material.ingestion_jobs_service import IngestionJobsService

    user = User(
        username=f"flowfail{int(refunded)}",
        email=f"flowfail{int(refunded)}@example.com",
        hashed_password="x",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    now = datetime.utcnow()
    db_session.add(
        UsageQuota(
            user_id=user.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            material_decompositions_used=1,
            monthly_period_start=now - timedelta(days=1),
            monthly_period_end=now + timedelta(days=30),
            last_reset_at=now,
        )
    )
    novel = Novel(user_id=user.id, title="flow failure")
    db_session.add(novel)
    db_session.commit()
    job = IngestionJob(novel_id=novel.id, source_path="/tmp/x.txt", status="processing")
    IngestionJobsService.set_billing(job, quota_charged=True, quota_refunded=False)
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _SessionCtx(db_session))
    monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda *_a, **_k: MagicMock())

    flow_mod._mark_job_as_failed(
        novel_id=novel.id,
        job_id=job.id,
        error_code=error_code,
        exception_type="SomeError",
        flow_start=0.0,
        logger=MagicMock(),
        publisher=MagicMock(),
    )

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_message == error_code
    assert "/tmp" not in (job.error_details or "")
    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).one()
    db_session.refresh(quota)
    assert quota.material_decompositions_used == (0 if refunded else 1)


# ---------------------------------------------------------------- worker DB pool


@pytest.mark.unit
def test_worker_postgres_engine_pings_and_recycles_connections():
    engine = dbs._build_engine("postgresql://user:pass@localhost:5432/db")
    try:
        assert engine.pool._pre_ping is True
        assert engine.pool._recycle == dbs.POSTGRES_POOL_RECYCLE_SECONDS
    finally:
        engine.dispose()


def test_cross_chunk_merge_judgment_keeps_stories_apart_on_provider_rejection(monkeypatch):
    agg_mod = importlib.import_module("flows.atomic_tasks.narrative.summary_based_aggregation")
    story1 = {"title": "a", "chapter_ids": [1, 2]}
    story2 = {"title": "b", "chapter_ids": [3, 4]}

    monkeypatch.setattr(
        agg_mod, "call_material_llm", MagicMock(side_effect=LLMNonRetryableError("400 bad request"))
    )
    assert agg_mod._llm_judge_merge(story1, story2, novel_id=1)["should_merge"] is False

    fatal = LLMNonRetryableError("trial cap", ErrorCode.MATERIAL_TRIAL_COST_LIMIT)
    monkeypatch.setattr(agg_mod, "call_material_llm", MagicMock(side_effect=fatal))
    with pytest.raises(LLMNonRetryableError) as raised:
        agg_mod._llm_judge_merge(story1, story2, novel_id=1)
    assert raised.value is fatal


def test_meta_response_skips_null_golden_finger_entries():
    from flows.utils.validators import validate_meta_response

    valid = {"name": "戒指", "type": "artifact", "description": "盘龙戒指中寄居着一位魔法师的灵魂。" * 15}
    result = validate_meta_response({"golden_fingers": [None, valid]})
    assert result["golden_fingers"] == [valid]
