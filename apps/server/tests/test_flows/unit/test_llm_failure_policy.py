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


def _client_raising(error: Exception) -> llm_mod.DeepSeekClient:
    client = llm_mod.DeepSeekClient.__new__(llm_mod.DeepSeekClient)

    def _raise(*_args, **_kwargs):
        raise error

    client._call_deepseek = _raise
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
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    client = llm_mod.DeepSeekClient()
    assert client.client.timeout == llm_mod.settings.LLM_REQUEST_TIMEOUT_SECONDS
    assert client.client.max_retries == llm_mod.settings.LLM_SDK_MAX_RETRIES


@pytest.mark.unit
def test_missing_api_key_is_an_account_failure(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(LLMNonRetryableError) as raised:
        llm_mod.DeepSeekClient()
    assert raised.value.error_code == ErrorCode.MATERIAL_LLM_UNAVAILABLE


@pytest.mark.unit
def test_json_with_literal_newlines_in_strings_is_parsed():
    client = llm_mod.DeepSeekClient.__new__(llm_mod.DeepSeekClient)
    content = '{"synopsis": "第一段：少年入山。\n\n第二段：宗门试炼。"}'
    response = llm_mod.LLMResponse(content=content, usage={}, model="m", finish_reason="stop")

    data = client.extract_json_from_response(response)

    assert data["synopsis"] == "第一段：少年入山。\n\n第二段：宗门试炼。"


@pytest.mark.unit
def test_unparsable_output_is_not_retried():
    client = llm_mod.DeepSeekClient.__new__(llm_mod.DeepSeekClient)
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
def test_llm_account_failure_aborts_extraction_with_code(monkeypatch):
    _patch_stage1(monkeypatch, summaries=True, mentions=False)
    account_error = LLMNonRetryableError("402 balance", ErrorCode.MATERIAL_LLM_UNAVAILABLE)

    class _SummaryTask:
        def submit(self, chapter_id):
            if chapter_id == 10:
                return FakeFuture({"chapter_id": 10, "summary": "ok"})
            return FakeFuture(error=account_error)

    writes = FakeTask({})
    monkeypatch.setattr(chapter_mod, "generate_chapter_summary_task", _SummaryTask())
    monkeypatch.setattr(chapter_mod, "update_chapter_summary_task", writes)

    with pytest.raises(MaterialPipelineError) as raised:
        chapter_mod.chapter_extraction_flow.fn(novel_id=1, chapter_ids=[10, 20, 30], job_id=1)

    assert raised.value.error_code == ErrorCode.MATERIAL_LLM_UNAVAILABLE


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
