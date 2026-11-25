"""Regression tests for sanitized per-chapter error_details persistence and safe failure classification."""

from __future__ import annotations

import importlib
import json
import pickle
from unittest.mock import MagicMock

import httpx
import openai
import pytest
from prefect.states import Failed

from agent.core import llm_failover, llm_providers
from core.error_codes import ErrorCode
from flows.pipelines import novel_ingestion_v3_flow as flow_mod
from flows.pipelines.stages import stage_executor as se_mod
from flows.utils.clients import llm as llm_mod
from flows.utils.decorators.prefect import retry_only_transient_failures
from flows.utils.helpers.exceptions import (
    LLMAPIError,
    LLMNonRetryableError,
    LLMOutputError,
    ValidationError,
)
from models.material_models import IngestionJob, Novel
from services.material.job_errors import (
    MAX_PERSISTED_CHAPTER_FAILURES,
    MaterialPipelineError,
    build_partial_completion_error_details,
    classify_material_exception,
)
from tests.test_flows.conftest import FakeFuture, FakeMonitor, FakeTask

chapter_mod = importlib.import_module("flows.pipelines.subflows.chapter_extraction_flow")

SECRET_SENTINEL = "SECRET_TOKEN_sk-live-987654321"
MANUSCRIPT_SENTINEL = "PRIVATE_MANUSCRIPT_EXCERPT_第一章隐秘剧情"
PATH_SENTINEL = "/tmp/private-upload/secret_book.txt"


class _SessionCtx:
    def __init__(self, session):
        self.session = session

    def __enter__(self):
        return self.session

    def __exit__(self, *_exc):
        return False


def _status_error(
    cls: type[openai.APIStatusError],
    status_code: int,
    *,
    message: str = "upstream error",
    body: object | None = None,
) -> openai.APIStatusError:
    response = httpx.Response(
        status_code,
        request=httpx.Request("POST", "https://llm.test/v1/chat/completions"),
    )
    return cls(message, response=response, body=body)


def _client_raising(error: Exception) -> llm_mod.MaterialLLMClient:
    client = llm_mod.MaterialLLMClient.__new__(llm_mod.MaterialLLMClient)

    def _raise(*_args, **_kwargs):
        raise error

    client._call_model = _raise
    return client


@pytest.mark.unit
@pytest.mark.parametrize(
    ("body", "message", "expected_code"),
    [
        (
            {
                "message": f"output new_sensitive (1027) {MANUSCRIPT_SENTINEL}",
                "type": "image_content_policy_violation",
                "code": "1027",
            },
            "Error code: 422 - output new_sensitive (1027)",
            1027,
        ),
        (
            {
                "base_resp": {
                    "status_code": 1027,
                    "status_msg": f"output new_sensitive {SECRET_SENTINEL}",
                }
            },
            "Error code: 422 - output new_sensitive (1027)",
            1027,
        ),
        (
            {"message": "output new_sensitive (1027)"},
            "Error code: 422 - output new_sensitive (1027)",
            1027,
        ),
        (
            {"base_resp": {"status_code": 1026, "status_msg": "input new_sensitive"}},
            "Error code: 422 - input new_sensitive (1026)",
            1026,
        ),
    ],
)
def test_minimax_422_content_filter_stays_non_retryable_no_failover_and_classifies_safely(
    monkeypatch,
    body,
    message,
    expected_code,
):
    """MiniMax 422 content-safety rejections must NOT fail over to DeepSeek, must stay
    non-retryable, and must classify as sanitized ``content_filtered`` metadata."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-minimax")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek")
    llm_failover.reset_failover_state()

    calls = {"minimax": 0, "deepseek": 0}
    err = _status_error(openai.UnprocessableEntityError, 422, message=message, body=body)

    def fake_sync_client(provider, **_kwargs):
        def create(**_req):
            calls[provider.name] += 1
            if provider.name == "minimax":
                raise err
            raise AssertionError("backup provider must NOT be called on content-policy 422")

        return MagicMock(chat=MagicMock(completions=MagicMock(create=create)))

    monkeypatch.setattr(llm_providers, "get_sync_client", fake_sync_client)

    client = llm_mod.MaterialLLMClient.__new__(llm_mod.MaterialLLMClient)
    client.max_tokens = 1024
    client.temperature = 0.2

    with pytest.raises(LLMNonRetryableError) as raised:
        client.chat_completion(messages=[{"role": "user", "content": "synthetic"}])

    exc = raised.value
    assert calls == {"minimax": 1, "deepseek": 0}
    assert exc.error_code is None
    assert retry_only_transient_failures(None, None, Failed(data=exc)) is False
    assert exc.failure_reason == "content_filtered"
    assert exc.http_status == 422
    assert exc.provider_error_code == expected_code

    classified = classify_material_exception(exc)
    assert classified == {
        "reason": "content_filtered",
        "error_type": "LLMNonRetryableError",
        "http_status": 422,
        "provider_error_code": expected_code,
    }
    serialized = json.dumps(classified, ensure_ascii=False)
    assert SECRET_SENTINEL not in serialized
    assert MANUSCRIPT_SENTINEL not in serialized


@pytest.mark.unit
def test_non_content_policy_422_and_other_errors_classify_accurately_and_safely():
    # Non-content-policy 422 stays non-retryable and is NOT marked content_filtered.
    schema_422 = _status_error(
        openai.UnprocessableEntityError,
        422,
        message=f"Error code: 422 - invalid params {SECRET_SENTINEL}",
        body={"base_resp": {"status_code": 2013, "status_msg": SECRET_SENTINEL}},
    )
    with pytest.raises(LLMNonRetryableError) as raised_422:
        _client_raising(schema_422).chat_completion(messages=[])
    assert retry_only_transient_failures(None, None, Failed(data=raised_422.value)) is False
    assert classify_material_exception(raised_422.value) == {
        "reason": "request_rejected",
        "error_type": "LLMNonRetryableError",
        "http_status": 422,
        "provider_error_code": 2013,
    }

    # Transient timeout
    timeout_err = openai.APITimeoutError(request=httpx.Request("POST", "https://llm.test"))
    with pytest.raises(LLMAPIError) as raised_timeout:
        _client_raising(timeout_err).chat_completion(messages=[])
    assert not isinstance(raised_timeout.value, LLMNonRetryableError)
    assert classify_material_exception(raised_timeout.value) == {
        "reason": "timeout",
        "error_type": "LLMAPIError",
    }

    # Transient 503
    err_503 = _status_error(openai.InternalServerError, 503, message=SECRET_SENTINEL)
    with pytest.raises(LLMAPIError) as raised_503:
        _client_raising(err_503).chat_completion(messages=[])
    assert classify_material_exception(raised_503.value) == {
        "reason": "upstream_server_error",
        "error_type": "LLMAPIError",
        "http_status": 503,
    }

    # JSON output error & validation error
    assert classify_material_exception(LLMOutputError("bad json")) == {
        "reason": "invalid_llm_output",
        "error_type": "LLMOutputError",
    }
    assert classify_material_exception(ValidationError(f"bad field {MANUSCRIPT_SENTINEL}")) == {
        "reason": "validation_error",
        "error_type": "ValidationError",
    }

    # Unknown exception with dangerous custom properties must never invoke them or leak text
    class _GuardedError(Exception):
        @property
        def body(self):
            raise AssertionError("must not invoke custom body property")

        @property
        def status_code(self):
            raise AssertionError("must not invoke custom status_code property")

    guarded = _GuardedError(f"{SECRET_SENTINEL} {PATH_SENTINEL}")
    classified = classify_material_exception(guarded)
    assert classified == {"reason": "unexpected_error", "error_type": "_GuardedError"}
    assert SECRET_SENTINEL not in json.dumps(classified)
    assert PATH_SENTINEL not in json.dumps(classified)


@pytest.mark.unit
def test_llm_exceptions_pickle_roundtrip_preserves_sanitized_metadata():
    err = LLMNonRetryableError(
        "api failed",
        None,
        failure_reason="content_filtered",
        http_status=422,
        provider_error_code=1027,
    )
    restored = pickle.loads(pickle.dumps(err))
    assert isinstance(restored, LLMNonRetryableError)
    assert restored.failure_reason == "content_filtered"
    assert restored.http_status == 422
    assert restored.provider_error_code == 1027

    out_err = LLMOutputError("invalid json")
    restored_out = pickle.loads(pickle.dumps(out_err))
    assert isinstance(restored_out, LLMOutputError)
    assert restored_out.failure_reason == "invalid_llm_output"

    pipe_err = MaterialPipelineError(
        ErrorCode.MATERIAL_EXTRACTION_FAILED,
        "all failed",
        details={"capability": "mentions", "failed_count": 2},
    )
    restored_pipe = pickle.loads(pickle.dumps(pipe_err))
    assert isinstance(restored_pipe, MaterialPipelineError)
    assert restored_pipe.details == {"capability": "mentions", "failed_count": 2}


class _MemoryCheckpointManager:
    def __init__(self) -> None:
        self.checkpoints: dict[str, MagicMock] = {}

    def get_checkpoint(self, stage: str):
        return self.checkpoints.get(stage)

    def get_pending_chapters(self, _stage: str, all_chapter_ids: list[int], capability: str = "summaries"):
        return list(all_chapter_ids)

    def get_completed_chapters(self, _stage: str, capability: str = "summaries"):
        return []

    def get_failed_chapters(self, _stage: str, capability: str = "summaries"):
        return []

    def update_checkpoint(self, stage: str, status=None, data=None, error=None):
        existing = self.checkpoints.get(stage)
        merged = dict(existing.checkpoint_data) if existing else {}
        if isinstance(data, dict):
            merged.update(data)
        self.checkpoints[stage] = MagicMock(
            stage=stage,
            stage_status=status or "processing",
            checkpoint_data=merged,
            error_message=error,
        )

    def mark_stage_completed(self, stage: str, data=None):
        self.update_checkpoint(stage, status="completed", data=data)


@pytest.mark.integration
def test_partial_completion_persists_sanitized_per_chapter_error_details_to_db(monkeypatch, db_session):
    """Audit #6/#7 regression: partial chapter failures (e.g. MiniMax 422 1027 & timeout)
    must persist structured, sanitized per-chapter details in ``ingestion_jobs.error_details``."""
    novel = Novel(user_id="u-err-details", title="Partial Novel")
    db_session.add(novel)
    db_session.commit()

    job = IngestionJob(
        novel_id=novel.id,
        source_path=PATH_SENTINEL,
        status="processing",
        total_chapters=3,
    )
    db_session.add(job)
    db_session.commit()

    cp = _MemoryCheckpointManager()
    monkeypatch.setattr(chapter_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(chapter_mod, "create_performance_monitor", lambda _n: FakeMonitor())
    monkeypatch.setattr(chapter_mod, "create_checkpoint_manager", lambda _nid, job_id=None: cp)
    monkeypatch.setattr(chapter_mod, "chapter_extraction_flow", chapter_mod.chapter_extraction_flow.fn)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_PLOT_EXTRACTION", False)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHARACTER_EXTRACTION", True)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_META_EXTRACTION", False)
    monkeypatch.setattr(chapter_mod.settings, "MAX_CONCURRENT_CHAPTERS", 3)

    import flows.database_session as dbs

    monkeypatch.setattr(dbs, "get_prefect_db_session", lambda: _SessionCtx(db_session))
    monkeypatch.setattr(se_mod, "get_prefect_db_session", lambda: _SessionCtx(db_session))
    monkeypatch.setattr(se_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(se_mod, "ProgressPublisher", MagicMock())

    # All 3 summaries succeed; mentions fail on ch#3770 (422 1027) and ch#3780 (timeout).
    monkeypatch.setattr(
        chapter_mod,
        "generate_chapter_summary_task",
        type(
            "_SumTask",
            (),
            {"submit": staticmethod(lambda chapter_id: FakeFuture({"chapter_id": chapter_id, "summary": "ok"}))},
        )(),
    )
    monkeypatch.setattr(chapter_mod, "update_chapter_summary_task", FakeTask({}))

    content_filter_err = LLMNonRetryableError(
        f"API 调用失败: UnprocessableEntityError: 422 {SECRET_SENTINEL} {MANUSCRIPT_SENTINEL}",
        None,
        failure_reason="content_filtered",
        http_status=422,
        provider_error_code=1027,
    )
    timeout_err = LLMAPIError(
        f"API 调用失败: APITimeoutError: {PATH_SENTINEL}",
        failure_reason="timeout",
    )

    class _MentionTask:
        def submit(self, chapter_id: int):
            if chapter_id == 3760:
                return FakeFuture({"chapter_id": 3760, "characters_count": 2})
            if chapter_id == 3770:
                return FakeFuture(error=content_filter_err)
            return FakeFuture(error=timeout_err)

    monkeypatch.setattr(chapter_mod, "extract_character_mentions_task", _MentionTask())

    chapter_ids = [3760, 3770, 3780]
    executor = se_mod.StageExecutor(
        novel_id=novel.id,
        chapter_ids=chapter_ids,
        checkpoint_manager=cp,
        correlation_id="corr-partial",
        job_id=job.id,
    )
    stage1_result = executor.execute_stage1()

    # Interim stage1 error_details is already persisted before stage2 finishes.
    db_session.refresh(job)
    assert job.error_details is not None
    interim_details = json.loads(job.error_details)
    assert interim_details["stage"] == "stage1"
    assert len(interim_details["chapter_failures"]) == 2

    # Stage 2 runs (stubbed clean) and writes terminal completed_with_errors + error_details.
    monkeypatch.setattr(executor, "_check_stage2_completion", lambda: (True, True, True))
    monkeypatch.setattr(
        executor,
        "_execute_parallel_stages",
        lambda *_a: (
            {"stories_count": 1, "failed_stories": [], "status": "completed"},
            {"created_count": 1, "updated_count": 0, "failed_count": 0, "failed_characters": [], "status": "completed"},
        ),
    )
    monkeypatch.setattr(
        executor,
        "_execute_relationship_stage",
        lambda *_a: {"relationships_count": 0, "status": "completed"},
    )
    monkeypatch.setattr(executor, "_update_stage2_checkpoints", lambda *_a: None)

    final_result = executor.execute_stage2(stage1_result=stage1_result, flow_start=0.0)
    assert final_result["status"] == "completed_with_errors"

    db_session.refresh(job)
    assert job.status == "completed_with_errors"
    assert job.error_message == ErrorCode.MATERIAL_PARTIALLY_COMPLETED
    assert job.error_details is not None
    assert SECRET_SENTINEL not in job.error_details
    assert MANUSCRIPT_SENTINEL not in job.error_details
    assert PATH_SENTINEL not in job.error_details

    parsed = json.loads(job.error_details)
    assert parsed == {
        "stage": "completed",
        "error_code": ErrorCode.MATERIAL_PARTIALLY_COMPLETED,
        "failed_count": 2,
        "reason_counts": {"content_filtered": 1, "timeout": 1},
        "chapter_failures": [
            {
                "chapter_id": 3770,
                "chapter_index": 2,
                "stage": "stage1.mentions",
                "capability": "mentions",
                "reason": "content_filtered",
                "error_type": "LLMNonRetryableError",
                "http_status": 422,
                "provider_error_code": 1027,
            },
            {
                "chapter_id": 3780,
                "chapter_index": 3,
                "stage": "stage1.mentions",
                "capability": "mentions",
                "reason": "timeout",
                "error_type": "LLMAPIError",
            },
        ],
    }

    # Verify resuming Stage 2 from checkpoint (stage1_result=None) preserves chapter_failures.
    resumed_job = IngestionJob(
        novel_id=novel.id,
        source_path=PATH_SENTINEL,
        status="processing",
        total_chapters=3,
    )
    db_session.add(resumed_job)
    db_session.commit()
    resumed_executor = se_mod.StageExecutor(
        novel_id=novel.id,
        chapter_ids=chapter_ids,
        checkpoint_manager=cp,
        correlation_id="corr-resume",
        job_id=resumed_job.id,
    )
    monkeypatch.setattr(resumed_executor, "_check_stage2_completion", lambda: (True, True, True))
    monkeypatch.setattr(
        resumed_executor,
        "_execute_parallel_stages",
        lambda *_a: (
            {"stories_count": 1, "failed_stories": [], "status": "completed"},
            {"created_count": 1, "updated_count": 0, "failed_count": 0, "failed_characters": [], "status": "completed"},
        ),
    )
    monkeypatch.setattr(
        resumed_executor,
        "_execute_relationship_stage",
        lambda *_a: {"relationships_count": 0, "status": "completed"},
    )
    monkeypatch.setattr(resumed_executor, "_update_stage2_checkpoints", lambda *_a: None)

    resumed_executor.execute_stage2(stage1_result=None, flow_start=0.0)
    db_session.refresh(resumed_job)
    assert resumed_job.status == "completed_with_errors"
    assert json.loads(resumed_job.error_details or "{}") == parsed


@pytest.mark.integration
def test_all_chapters_failing_persists_sanitized_chapter_failures_on_failed_job(monkeypatch, db_session):
    """When 100% of chapters fail with 422 1027, the failed job's error_details still
    records the sanitized chapter failure breakdown without leaking raw messages."""
    novel = Novel(user_id="u-all-fail", title="All Fail Novel")
    db_session.add(novel)
    db_session.commit()
    job = IngestionJob(
        novel_id=novel.id,
        source_path=PATH_SENTINEL,
        status="processing",
        total_chapters=2,
    )
    db_session.add(job)
    db_session.commit()

    monkeypatch.setattr(chapter_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(chapter_mod, "create_performance_monitor", lambda _n: FakeMonitor())
    monkeypatch.setattr(
        chapter_mod,
        "create_checkpoint_manager",
        lambda _nid, job_id=None: _MemoryCheckpointManager(),
    )
    monkeypatch.setattr(chapter_mod, "_sync_stage1_job_progress", lambda *_a, **_k: None)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHAPTER_SUMMARIES", True)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_PLOT_EXTRACTION", False)
    monkeypatch.setattr(chapter_mod.settings, "ENABLE_CHARACTER_EXTRACTION", False)

    err = LLMNonRetryableError(
        f"API 调用失败: {SECRET_SENTINEL} {MANUSCRIPT_SENTINEL}",
        None,
        failure_reason="content_filtered",
        http_status=422,
        provider_error_code=1027,
    )
    monkeypatch.setattr(chapter_mod, "generate_chapter_summary_task", FakeTask(error=err))
    monkeypatch.setattr(chapter_mod, "update_chapter_summary_task", FakeTask({}))

    with pytest.raises(MaterialPipelineError) as raised:
        chapter_mod.chapter_extraction_flow.fn(novel_id=novel.id, chapter_ids=[101, 102], job_id=job.id)

    assert raised.value.error_code == ErrorCode.MATERIAL_EXTRACTION_FAILED
    assert isinstance(raised.value.details, dict)
    assert raised.value.details["reason_counts"] == {"content_filtered": 2}
    assert len(raised.value.details["chapter_failures"]) == 2

    monkeypatch.setattr(flow_mod, "get_prefect_db_session", lambda: _SessionCtx(db_session))
    monkeypatch.setattr(flow_mod, "create_checkpoint_manager", lambda *_a, **_k: MagicMock())
    flow_mod._mark_job_as_failed(
        novel_id=novel.id,
        job_id=job.id,
        error_code=raised.value.error_code,
        exception_type=type(raised.value).__name__,
        flow_start=0.0,
        logger=MagicMock(),
        publisher=MagicMock(),
        error_details=raised.value.details,
    )

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error_message == ErrorCode.MATERIAL_EXTRACTION_FAILED
    assert job.error_details is not None
    assert SECRET_SENTINEL not in job.error_details
    assert MANUSCRIPT_SENTINEL not in job.error_details
    assert PATH_SENTINEL not in job.error_details
    parsed = json.loads(job.error_details)
    assert "message" not in parsed
    assert parsed["capability"] == "summaries"
    assert parsed["reason_counts"] == {"content_filtered": 2}
    assert [c["chapter_id"] for c in parsed["chapter_failures"]] == [101, 102]
    assert [c["chapter_index"] for c in parsed["chapter_failures"]] == [1, 2]


@pytest.mark.unit
def test_partial_completion_error_details_caps_size_and_sanitizes_untrusted_entries():
    huge_failures = [
        {
            "chapter_id": cid,
            "chapter_index": idx,
            "stage": "stage1.mentions",
            "capability": "mentions",
            "reason": "content_filtered",
            "error_type": "LLMNonRetryableError",
            "http_status": 422,
            "provider_error_code": 1027,
            "raw_message": f"MUST_BE_DROPPED {SECRET_SENTINEL}",
            "chapter_text": MANUSCRIPT_SENTINEL,
        }
        for idx, cid in enumerate(range(1, MAX_PERSISTED_CHAPTER_FAILURES + 25), start=1)
    ]

    details = build_partial_completion_error_details(
        chapter_ids=list(range(1, MAX_PERSISTED_CHAPTER_FAILURES + 25)),
        stage1_result={
            "failed_count": len(huge_failures),
            "chapter_failures": huge_failures,
            "meta_extraction": {
                "executed": True,
                "succeeded": False,
                "error": "Metadata extraction failed",
                "error_code": "ERR_MATERIAL_META_EXTRACTION_FAILED",
            },
        },
        story_result={
            "failed_stories": [f"1:{MANUSCRIPT_SENTINEL}"],
            "story_failure_reasons": {"invalid_llm_output": 1},
        },
        character_entity_result={
            "failed_count": 1,
            "failed_characters": [MANUSCRIPT_SENTINEL],
            "character_failure_reasons": {"timeout": 1},
        },
        meta_failure_detail={"reason": "upstream_server_error", "error_type": "LLMAPIError", "http_status": 502},
    )

    serialized = json.dumps(details, ensure_ascii=False)
    assert SECRET_SENTINEL not in serialized
    assert MANUSCRIPT_SENTINEL not in serialized
    assert "MUST_BE_DROPPED" not in serialized
    assert len(details["chapter_failures"]) == MAX_PERSISTED_CHAPTER_FAILURES
    assert details["chapter_failures_truncated"] is True
    assert details["total_chapter_failures"] == MAX_PERSISTED_CHAPTER_FAILURES + 24
    assert details["reason_counts"] == {
        "content_filtered": MAX_PERSISTED_CHAPTER_FAILURES + 24,
        "invalid_llm_output": 1,
        "timeout": 1,
        "upstream_server_error": 1,
    }
    assert [sf["stage"] for sf in details["stage_failures"]] == [
        "stage1.meta",
        "stage2.stories",
        "stage2.characters",
    ]
