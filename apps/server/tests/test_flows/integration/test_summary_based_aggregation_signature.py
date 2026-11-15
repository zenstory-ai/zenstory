from __future__ import annotations

import importlib
from unittest.mock import MagicMock

import pytest

summary_mod = importlib.import_module(
    "flows.atomic_tasks.narrative.summary_based_aggregation"
)
decorator_mod = importlib.import_module("flows.utils.decorators.prefect")
chapters_mod = importlib.import_module("services.material.chapters_service")


class _FakeSessionCtx:
    def __enter__(self):
        return object()

    def __exit__(self, exc_type, exc, tb):
        return False


@pytest.mark.integration
def test_identify_story_frameworks_accepts_novel_id_keyword(monkeypatch):
    """
    Regression:
    Prefect task invocation passes `novel_id=...`.
    The task function must accept this keyword without TypeError.
    """

    class _FakeChaptersService:
        def list_by_novel_ordered(self, _db, _novel_id, _chapter_ids=None):
            return []

    monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(summary_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(summary_mod, "get_db_session", lambda: _FakeSessionCtx())
    monkeypatch.setattr(chapters_mod, "ChaptersService", _FakeChaptersService)

    result = summary_mod.identify_story_frameworks_from_summaries.fn(
        novel_id=42,
        chapter_ids=[1, 2, 3],
    )

    assert result == {"story_frameworks": [], "novel_id": 42}


@pytest.mark.integration
def test_summary_based_aggregation_accepts_novel_id_keyword(monkeypatch):
    monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(summary_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(
        summary_mod,
        "identify_story_frameworks_from_summaries",
        lambda **kwargs: {"story_frameworks": [], "novel_id": kwargs["novel_id"]},
    )

    result = summary_mod.summary_based_story_aggregation.fn(
        novel_id=77,
        chapter_ids=[10, 11],
    )

    assert result == {
        "novel_id": 77,
        "stories_count": 0,
        "storylines_count": 0,
    }


@pytest.mark.integration
def test_merge_cross_chunk_stories_propagates_budget_and_transient_llm_errors(monkeypatch):
    from core.error_codes import ErrorCode
    from flows.utils.clients.llm import LLMResponse, MaterialLLMClient
    from flows.utils.helpers.exceptions import LLMAPIError, LLMNonRetryableError

    monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(summary_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(
        summary_mod,
        "get_material_llm_client",
        lambda: MaterialLLMClient.__new__(MaterialLLMClient),
    )

    chunk_frameworks = [
        {"title": "上篇", "source_chunk_id": 0, "chapter_ids": [1, 2, 3]},
        {"title": "下篇", "source_chunk_id": 1, "chapter_ids": [4, 5, 6]},
    ]
    chunks = [{"chunk_id": 0}, {"chunk_id": 1}]

    def raise_trial_budget(**_kwargs):
        raise LLMNonRetryableError("免费试拆的模型预算已用完", ErrorCode.MATERIAL_TRIAL_COST_LIMIT)

    monkeypatch.setattr(summary_mod, "call_material_llm", raise_trial_budget)
    with pytest.raises(LLMNonRetryableError) as budget_exc:
        summary_mod._merge_cross_chunk_stories.fn(chunk_frameworks, chunks, novel_id=9)
    assert budget_exc.value.error_code == ErrorCode.MATERIAL_TRIAL_COST_LIMIT

    def raise_transient(**_kwargs):
        raise LLMAPIError("provider 503")

    monkeypatch.setattr(summary_mod, "call_material_llm", raise_transient)
    with pytest.raises(LLMAPIError, match="provider 503"):
        summary_mod._merge_cross_chunk_stories.fn(chunk_frameworks, chunks, novel_id=9)

    monkeypatch.setattr(
        summary_mod,
        "call_material_llm",
        lambda **_kwargs: LLMResponse(
            content="not valid json at all",
            usage={},
            model="mock",
            finish_reason="length",
        ),
    )
    merged = summary_mod._merge_cross_chunk_stories.fn(chunk_frameworks, chunks, novel_id=9)
    assert merged == chunk_frameworks


@pytest.mark.integration
def test_summary_based_story_aggregation_propagates_llm_service_failure(monkeypatch):
    from core.error_codes import ErrorCode
    from flows.utils.helpers.exceptions import LLMNonRetryableError

    monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(summary_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(
        summary_mod,
        "identify_story_frameworks_from_summaries",
        lambda **kwargs: {
            "story_frameworks": [{"title": "主线开端", "chapter_ids": [1, 2]}],
            "novel_id": kwargs["novel_id"],
        },
    )

    def raise_budget(**_kwargs):
        raise LLMNonRetryableError("免费试拆的模型预算已用完", ErrorCode.MATERIAL_TRIAL_COST_LIMIT)

    monkeypatch.setattr(summary_mod, "aggregate_plots_for_story_framework", raise_budget)
    with pytest.raises(LLMNonRetryableError) as exc_info:
        summary_mod.summary_based_story_aggregation.fn(novel_id=77, chapter_ids=[1, 2])
    assert exc_info.value.error_code == ErrorCode.MATERIAL_TRIAL_COST_LIMIT
