"""Regression coverage for usage-ledger task context and call-start semantics."""

from __future__ import annotations

import asyncio
import gc
import logging
import threading
import weakref
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any

import pytest

from agent.tools.mcp_tools import ToolContext
from services.usage import cost_budget
from services.usage import llm_usage_service as svc
from services.usage.llm_usage_service import LLMUsageAttribution, UsageTokens
from services.usage.pricing import beijing_today, price_band
from utils.request_context import (
    bind_request_context,
    get_log_context,
    reset_request_context,
)

LOG_CONTEXT = {
    "request_id": "usage-request",
    "trace_id": "usage-trace",
    "agent_run_id": "usage-run",
}


class _TrackedText(str):
    """Weak-referenceable text representative of a large request snapshot."""


async def _wait_for(event: threading.Event) -> None:
    for _ in range(1_000):
        if event.is_set():
            return
        await asyncio.sleep(0.005)
    raise TimeoutError("background ledger recorder did not start")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pending_ledger_task_does_not_retain_request_snapshot(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    captured: list[dict[str, Any]] = []

    def blocking_recorder(
        attribution: LLMUsageAttribution,
        *,
        model: str,
        tokens: UsageTokens,
        occurred_at: datetime,
        **_kwargs: Any,
    ) -> bool:
        captured.append(
            {
                "attribution": asdict(attribution),
                "model": model,
                "tokens": asdict(tokens),
                "occurred_at": occurred_at,
                "log_context": get_log_context(),
            }
        )
        started.set()
        if not release.wait(5):
            raise TimeoutError("test did not release ledger recorder")
        return True

    call_start = datetime(2026, 10, 16, 1, 0, tzinfo=UTC)
    monkeypatch.setattr(svc, "record_llm_usage", blocking_recorder)
    monkeypatch.setattr(cost_budget, "call_started_at", lambda _user_id=None: call_start)
    request_tokens = bind_request_context(**LOG_CONTEXT)
    snapshot = _TrackedText("x" * 8_000_000)
    snapshot_ref = weakref.ref(snapshot)
    task = None
    try:
        ToolContext.set_context(
            session=None,
            user_id="user-1",
            project_id="project-1",
            session_id="session-1",
            read_snapshots={"file-1": snapshot},
        )
        attribution = LLMUsageAttribution(
            user_id="user-1",
            source="agent",
            project_id="project-1",
            correlation_id="usage-run",
        )
        usage_tokens = UsageTokens(cache_hit=800, cache_miss=200, output=70)

        task = svc.schedule_llm_usage_record(
            attribution,
            model="deepseek-flash",
            tokens=usage_tokens,
        )
        assert task is not None
        assert task in svc._pending_records
        del snapshot
        await _wait_for(started)

        ToolContext.clear_context()
        gc.collect()
        assert snapshot_ref() is None
        assert not task.done()

        release.set()
        await svc.drain_pending_usage_records()
        await asyncio.sleep(0)
        assert task.result() is True
        assert task not in svc._pending_records
        assert captured == [
            {
                "attribution": asdict(attribution),
                "model": "deepseek-flash",
                "tokens": asdict(usage_tokens),
                "occurred_at": call_start,
                "log_context": LOG_CONTEXT,
            }
        ]
    finally:
        release.set()
        ToolContext.clear_context()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await svc.drain_pending_usage_records()
        reset_request_context(request_tokens)


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("call_start", "expected_band", "expected_beijing_day"),
    [
        (datetime(2026, 10, 15, 3, 59, 59, tzinfo=UTC), "peak", date(2026, 10, 15)),
        (datetime(2026, 10, 15, 4, 0, tzinfo=UTC), "offpeak", date(2026, 10, 15)),
        (datetime(2026, 10, 15, 15, 59, 59, tzinfo=UTC), "offpeak", date(2026, 10, 15)),
        (datetime(2026, 10, 15, 16, 0, tzinfo=UTC), "offpeak", date(2026, 10, 16)),
    ],
)
async def test_schedule_freezes_billing_inputs_at_call_start(
    monkeypatch,
    call_start: datetime,
    expected_band: str,
    expected_beijing_day: date,
):
    release = threading.Event()
    captured: list[dict[str, Any]] = []
    attribution = LLMUsageAttribution(
        user_id="user-2",
        source="router",
        project_id="project-2",
        correlation_id="route-2",
    )
    usage_tokens = UsageTokens(cache_hit=11, cache_miss=22, output=33)

    def recorder(
        recorded_attribution: LLMUsageAttribution,
        *,
        model: str,
        tokens: UsageTokens,
        occurred_at: datetime,
        **_kwargs: Any,
    ) -> bool:
        release.wait(5)
        captured.append(
            {
                "attribution": recorded_attribution,
                "model": model,
                "tokens": tokens,
                "occurred_at": occurred_at,
            }
        )
        return True

    monkeypatch.setattr(svc, "record_llm_usage", recorder)
    monkeypatch.setattr(cost_budget, "call_started_at", lambda _user_id=None: call_start)

    task = svc.schedule_llm_usage_record(
        attribution,
        model="deepseek-flash",
        tokens=usage_tokens,
    )
    assert task is not None
    assert not task.done()
    release.set()
    await svc.drain_pending_usage_records()

    assert captured == [
        {
            "attribution": attribution,
            "model": "deepseek-flash",
            "tokens": usage_tokens,
            "occurred_at": call_start,
        }
    ]
    assert price_band(captured[0]["occurred_at"]) == expected_band
    assert beijing_today(captured[0]["occurred_at"]) == expected_beijing_day


@pytest.mark.unit
@pytest.mark.asyncio
async def test_worker_failure_log_keeps_log_correlation(monkeypatch, caplog):
    def broken_factory():
        raise RuntimeError("db down")

    monkeypatch.setattr(svc, "_session_factory", broken_factory)
    request_tokens = bind_request_context(**LOG_CONTEXT)
    try:
        with caplog.at_level(logging.ERROR, logger=svc.logger.name):
            task = svc.schedule_llm_usage_record(
                LLMUsageAttribution(user_id="user-3", source="agent"),
                model="deepseek-flash",
                tokens=UsageTokens(output=1),
            )
            assert task is not None
            await svc.drain_pending_usage_records()
            await asyncio.sleep(0)

        [record] = [
            record
            for record in caplog.records
            if record.getMessage() == "LLM usage metering failed"
        ]
        assert record.custom_fields["request_id"] == LOG_CONTEXT["request_id"]
        assert record.custom_fields["trace_id"] == LOG_CONTEXT["trace_id"]
        assert record.custom_fields["agent_run_id"] == LOG_CONTEXT["agent_run_id"]
        assert record.custom_fields["error_type"] == "RuntimeError"
        assert task.result() is False
        assert task not in svc._pending_records
    finally:
        reset_request_context(request_tokens)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_failed_task_callback_keeps_log_correlation(monkeypatch, caplog):
    def broken_recorder(*_args: Any, **_kwargs: Any) -> bool:
        raise RuntimeError("thread blew up")

    monkeypatch.setattr(svc, "record_llm_usage", broken_recorder)
    request_tokens = bind_request_context(**LOG_CONTEXT)
    try:
        with caplog.at_level(logging.ERROR, logger=svc.logger.name):
            task = svc.schedule_llm_usage_record(
                LLMUsageAttribution(user_id="user-3", source="agent"),
                model="deepseek-flash",
                tokens=UsageTokens(output=1),
            )
            assert task is not None
            await svc.drain_pending_usage_records()
            await asyncio.sleep(0)

        [record] = [
            record
            for record in caplog.records
            if record.getMessage() == "LLM usage metering failed"
        ]
        assert record.custom_fields["request_id"] == LOG_CONTEXT["request_id"]
        assert record.custom_fields["trace_id"] == LOG_CONTEXT["trace_id"]
        assert record.custom_fields["agent_run_id"] == LOG_CONTEXT["agent_run_id"]
        assert record.custom_fields["error_type"] == "RuntimeError"
        assert task not in svc._pending_records
    finally:
        reset_request_context(request_tokens)
