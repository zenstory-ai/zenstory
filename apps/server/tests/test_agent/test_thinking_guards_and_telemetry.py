"""Regression tests for MiniMax thinking/duration stream guards, trimmer wrap-up safety,
max_prompt_tokens & draining telemetry, RepeatReadGuard cross-mode caps, runaway_stop
classification, and LOOP_WORKING_RULES batch-pacing guidance.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import time
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from agents import ModelSettings
from agents.run_config import CallModelData, ModelInputData

from agent.core import llm_failover
from agent.core.events import WALL_CLOCK_LIMIT
from agent.core.round_outcome import RunOutcome
from agent.core.run_meter import AgentRunMeter
from agent.core.workflow_events import StreamEventType
from agent.loop.prompts import LOOP_WORKING_RULES
from agent.openai_agents import model as sdk_model
from agent.openai_agents.intra_run_trimmer import COMPACTION_REASON, IntraRunToolOutputTrimmer
from agent.openai_agents.repeat_read_guard import (
    FILE_TOTAL_READ_BLOCK_AT,
    FILE_TOTAL_READ_WARN_AT,
    MAX_BLOCKED_READS_PER_REQUEST,
    REPEATED_READ_ERROR_TYPE,
    RepeatReadGuard,
)
from agent.openai_agents.usage_hooks import build_agent_run_hooks
from api.agent import _AgentStreamSettlement
from config import agent_runtime
from core.error_codes import ErrorCode
from models.llm_usage import CALL_OUTCOME_TIMEOUT

# ---------------------------------------------------------------------------
# 1. Runtime configuration defaults & env overrides
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_agent_runtime_thinking_guard_and_compaction_defaults(monkeypatch):
    for var in (
        "AGENT_PRE_ANSWER_THINKING_MAX_S",
        "AGENT_PRE_ANSWER_THINKING_MAX_CHARS",
        "AGENT_MODEL_CALL_MAX_DURATION_S",
        "AGENT_LOOP_COMPACT_AT_TOKENS",
    ):
        monkeypatch.delenv(var, raising=False)

    reloaded = importlib.reload(agent_runtime)
    try:
        assert reloaded.AGENT_PRE_ANSWER_THINKING_MAX_S == 180.0
        assert reloaded.AGENT_PRE_ANSWER_THINKING_MAX_CHARS == 64_000
        assert reloaded.AGENT_MODEL_CALL_MAX_DURATION_S == 300.0
        assert reloaded.AGENT_LOOP_COMPACT_AT_TOKENS == 240_000
        # Explicitly omitted pending risk audit on wrap-up output cap.
        assert not hasattr(reloaded, "AGENT_WALL_CLOCK_WRAP_UP_MAX_OUTPUT_TOKENS")

        # Setting 0 disables guards cleanly; negative/invalid falls back to defaults.
        monkeypatch.setenv("AGENT_PRE_ANSWER_THINKING_MAX_S", "0")
        monkeypatch.setenv("AGENT_PRE_ANSWER_THINKING_MAX_CHARS", "0")
        monkeypatch.setenv("AGENT_MODEL_CALL_MAX_DURATION_S", "-5")
        monkeypatch.setenv("AGENT_LOOP_COMPACT_AT_TOKENS", "260000")
        reloaded = importlib.reload(agent_runtime)
        assert reloaded.AGENT_PRE_ANSWER_THINKING_MAX_S == 0.0
        assert reloaded.AGENT_PRE_ANSWER_THINKING_MAX_CHARS == 0
        assert reloaded.AGENT_MODEL_CALL_MAX_DURATION_S == 300.0
        assert reloaded.AGENT_LOOP_COMPACT_AT_TOKENS == 260_000
    finally:
        for var in (
            "AGENT_PRE_ANSWER_THINKING_MAX_S",
            "AGENT_PRE_ANSWER_THINKING_MAX_CHARS",
            "AGENT_MODEL_CALL_MAX_DURATION_S",
            "AGENT_LOOP_COMPACT_AT_TOKENS",
        ):
            monkeypatch.delenv(var, raising=False)
        importlib.reload(agent_runtime)


# ---------------------------------------------------------------------------
# 2. Structural pre-answer thinking cutoff & per-call wall-clock cap
# ---------------------------------------------------------------------------


@pytest.fixture
def _dual_provider_env(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-minimax-test")
    monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-test")
    monkeypatch.delenv("MINIMAX_API_SURFACE", raising=False)
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()
    yield
    sdk_model.reset_sdk_model_cache()
    llm_failover.reset_failover_state()


class _ScriptedStreamModel:
    def __init__(self, event_factory):
        self.calls = 0
        self.closed = False
        self._event_factory = event_factory

    async def stream_response(self, *_args, **_kwargs):
        self.calls += 1
        try:
            async for ev in self._event_factory():
                yield ev
        finally:
            self.closed = True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pre_answer_thinking_char_cutoff_aborts_cleanly_without_failover(
    _dual_provider_env, monkeypatch
):
    """Exceeding AGENT_PRE_ANSWER_THINKING_MAX_CHARS before any answer output closes
    the upstream stream, emits a synthetic capped ResponseCompletedEvent(output=[]),
    does NOT fail over to DeepSeek, and records CALL_OUTCOME_TIMEOUT."""
    monkeypatch.setattr(agent_runtime, "AGENT_PRE_ANSWER_THINKING_MAX_CHARS", 100)
    monkeypatch.setattr(agent_runtime, "AGENT_PRE_ANSWER_THINKING_MAX_S", 90.0)
    monkeypatch.setattr(agent_runtime, "AGENT_MODEL_CALL_MAX_DURATION_S", 210.0)

    async def mm_stream():
        yield SimpleNamespace(type="response.created", sequence_number=0)
        for idx in range(1, 20):
            yield SimpleNamespace(
                type="response.reasoning_text.delta",
                delta="思" * 40,
                sequence_number=idx,
            )
            await asyncio.sleep(0)
        yield SimpleNamespace(type="response.output_text.delta", delta="不应到达的正文", sequence_number=21)
        yield SimpleNamespace(type="response.completed", sequence_number=22)

    async def ds_stream():
        yield SimpleNamespace(type="response.output_text.delta", delta="备源不应被调用", sequence_number=1)
        yield SimpleNamespace(type="response.completed", sequence_number=2)

    mm_model = _ScriptedStreamModel(mm_stream)
    ds_model = _ScriptedStreamModel(ds_stream)
    monkeypatch.setattr(sdk_model, "_sdk_model_for", lambda p: mm_model if p.name == "minimax" else ds_model)

    model = sdk_model.build_failover_chat_model("sess-think-chars")
    events = [
        ev
        async for ev in model.stream_response(
            "sys", [], ModelSettings(), [], None, [], None
        )
    ]

    assert mm_model.closed is True
    assert mm_model.calls == 1
    assert ds_model.calls == 0, "thinking cutoff must NOT fail over to backup provider"
    assert model.unrecovered_provider_error is None
    assert model.last_response_output_capped is True
    assert model.last_stream_cutoff_reason == sdk_model.STREAM_CUTOFF_PRE_ANSWER_THINKING_CHARS
    timing = model.take_call_timing()
    assert timing is not None and timing.outcome == CALL_OUTCOME_TIMEOUT
    assert events[-1].type == "response.completed"
    assert events[-1].response.output == []
    assert not any(getattr(ev, "delta", None) == "不应到达的正文" for ev in events)
    # Session remains on primary provider (no failover pinning).
    assert llm_failover.ordered_providers("sess-think-chars")[0].name == "minimax"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pre_answer_thinking_time_cutoff_fires_even_with_continuous_reasoning_deltas(
    _dual_provider_env, monkeypatch
):
    """Continuous reasoning deltas reset the idle timer, but pre-answer thinking max duration
    still aborts cleanly without failing over."""
    monkeypatch.setattr(agent_runtime, "AGENT_PRE_ANSWER_THINKING_MAX_S", 0.08)
    monkeypatch.setattr(agent_runtime, "AGENT_PRE_ANSWER_THINKING_MAX_CHARS", 100_000)
    monkeypatch.setattr(agent_runtime, "AGENT_MODEL_CALL_MAX_DURATION_S", 210.0)
    monkeypatch.setenv("MINIMAX_CLIENT_TIMEOUT_S", "5.0")

    async def mm_stream():
        yield SimpleNamespace(type="response.created", sequence_number=0)
        for idx in range(1, 30):
            yield SimpleNamespace(
                type="response.reasoning_text.delta",
                delta="想",
                sequence_number=idx,
            )
            await asyncio.sleep(0.025)
        yield SimpleNamespace(type="response.output_text.delta", delta="太晚了", sequence_number=31)
        yield SimpleNamespace(type="response.completed", sequence_number=32)

    mm_model = _ScriptedStreamModel(mm_stream)
    ds_model = _ScriptedStreamModel(mm_stream)
    monkeypatch.setattr(sdk_model, "_sdk_model_for", lambda p: mm_model if p.name == "minimax" else ds_model)

    model = sdk_model.build_failover_chat_model("sess-think-time")
    events = [
        ev
        async for ev in model.stream_response(
            "sys", [], ModelSettings(), [], None, [], None
        )
    ]

    assert mm_model.closed is True
    assert ds_model.calls == 0
    assert model.last_response_output_capped is True
    assert model.last_stream_cutoff_reason == sdk_model.STREAM_CUTOFF_PRE_ANSWER_THINKING_TIME
    assert events[-1].type == "response.completed"
    assert events[-1].response.output == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pre_answer_thinking_guard_does_not_abort_healthy_answer_streaming(
    _dual_provider_env, monkeypatch
):
    """Once answer output starts, pre-answer thinking caps stop applying; only the
    per-call wall-clock cap applies."""
    monkeypatch.setattr(agent_runtime, "AGENT_PRE_ANSWER_THINKING_MAX_S", 0.04)
    monkeypatch.setattr(agent_runtime, "AGENT_PRE_ANSWER_THINKING_MAX_CHARS", 50)
    monkeypatch.setattr(agent_runtime, "AGENT_MODEL_CALL_MAX_DURATION_S", 5.0)

    async def mm_stream():
        yield SimpleNamespace(type="response.created", sequence_number=0)
        yield SimpleNamespace(type="response.reasoning_text.delta", delta="简短思考", sequence_number=1)
        yield SimpleNamespace(type="response.output_text.delta", delta="第一章正文开始", sequence_number=2)
        await asyncio.sleep(0.08)  # longer than pre-answer 0.04s, well under 5.0s call cap
        yield SimpleNamespace(type="response.output_text.delta", delta="，继续流畅输出。", sequence_number=3)
        yield SimpleNamespace(type="response.completed", sequence_number=4)

    mm_model = _ScriptedStreamModel(mm_stream)
    monkeypatch.setattr(sdk_model, "_sdk_model_for", lambda _p: mm_model)

    model = sdk_model.build_failover_chat_model("sess-healthy-answer")
    events = [
        ev
        async for ev in model.stream_response(
            "sys", [], ModelSettings(), [], None, [], None
        )
    ]

    assert model.last_response_output_capped is False
    assert model.last_stream_cutoff_reason is None
    deltas = [getattr(ev, "delta", "") for ev in events if getattr(ev, "type", None) == "response.output_text.delta"]
    assert "".join(deltas) == "第一章正文开始，继续流畅输出。"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runner_integrates_pre_answer_thinking_cutoff_as_output_truncated(
    _dual_provider_env, monkeypatch
):
    """End-to-end with existing runner.py (unmodified): pre-answer thinking cutoff produces
    MESSAGE_END(stop_reason='output_truncated') + ERROR(ERR_AGENT_OUTPUT_TRUNCATED, refundable=False)
    and logs the cutoff reason in thinking telemetry without error_source='model'."""
    from agent.openai_agents.runner import (
        OUTPUT_TRUNCATED_STOP_REASON,
        run_openai_agents_streaming_agent,
    )

    monkeypatch.setattr(agent_runtime, "AGENT_PRE_ANSWER_THINKING_MAX_CHARS", 80)
    monkeypatch.setattr(agent_runtime, "AGENT_PRE_ANSWER_THINKING_MAX_S", 90.0)
    monkeypatch.setattr(agent_runtime, "AGENT_MODEL_CALL_MAX_DURATION_S", 210.0)

    async def mm_stream():
        yield SimpleNamespace(type="response.created", sequence_number=0)
        for idx in range(1, 10):
            yield SimpleNamespace(
                type="response.reasoning_text.delta",
                delta="反复推演" * 10,
                sequence_number=idx,
            )
            await asyncio.sleep(0)

    mm_model = _ScriptedStreamModel(mm_stream)
    monkeypatch.setattr(sdk_model, "_sdk_model_for", lambda _p: mm_model)

    logged_outcomes: list[str] = []
    orig_log = sdk_model.log_thinking_before_answer

    def spy_log(*args, **kwargs):
        logged_outcomes.append(kwargs.get("outcome", ""))
        return orig_log(*args, **kwargs)

    monkeypatch.setattr(sdk_model, "log_thinking_before_answer", spy_log)

    state: dict[str, Any] = {"user_message": "写第一章", "session_id": "sess-runner-cutoff"}
    events = [ev async for ev in run_openai_agents_streaming_agent(state, "loop", "系统提示")]  # type: ignore[arg-type]

    assert mm_model.closed is True
    assert [ev.type for ev in events[-2:]] == [StreamEventType.MESSAGE_END, StreamEventType.ERROR]
    assert events[-2].data["stop_reason"] == OUTPUT_TRUNCATED_STOP_REASON
    err = events[-1].data
    assert err["code"] == ErrorCode.AGENT_OUTPUT_TRUNCATED
    assert err["refundable"] is False
    assert err["retryable"] is False
    assert "error_source" not in err
    assert logged_outcomes == [sdk_model.STREAM_CUTOFF_PRE_ANSWER_THINKING_CHARS]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_runner_integrates_per_call_duration_cutoff_after_prose_as_output_capped(
    _dual_provider_env, monkeypatch
):
    """When per-call max duration fires AFTER prose started streaming, runner.py emits
    MESSAGE_END(stop_reason='end_turn', output_capped=True) with streamed text preserved
    and no fatal ERROR, so loop/engine can save and resume partial <file> bodies."""
    from agent.openai_agents.runner import OUTPUT_CAPPED_KEY, run_openai_agents_streaming_agent

    monkeypatch.setattr(agent_runtime, "AGENT_PRE_ANSWER_THINKING_MAX_S", 90.0)
    monkeypatch.setattr(agent_runtime, "AGENT_PRE_ANSWER_THINKING_MAX_CHARS", 100_000)
    monkeypatch.setattr(agent_runtime, "AGENT_MODEL_CALL_MAX_DURATION_S", 0.08)

    async def mm_stream():
        yield SimpleNamespace(type="response.created", sequence_number=0)
        yield SimpleNamespace(type="response.output_text.delta", delta="已写出的半段正文", sequence_number=1)
        await asyncio.sleep(0.5)
        yield SimpleNamespace(type="response.output_text.delta", delta="永远到不了", sequence_number=2)

    mm_model = _ScriptedStreamModel(mm_stream)
    monkeypatch.setattr(sdk_model, "_sdk_model_for", lambda _p: mm_model)

    state: dict[str, Any] = {"user_message": "继续写", "session_id": "sess-runner-call-cap"}
    events = [ev async for ev in run_openai_agents_streaming_agent(state, "loop", "系统提示")]  # type: ignore[arg-type]

    assert mm_model.closed is True
    assert not any(ev.type == StreamEventType.ERROR for ev in events)
    text = "".join(ev.data["text"] for ev in events if ev.type == StreamEventType.TEXT)
    assert text == "已写出的半段正文"
    assert events[-1].type == StreamEventType.MESSAGE_END
    assert events[-1].data["stop_reason"] == "end_turn"
    assert events[-1].data.get(OUTPUT_CAPPED_KEY) is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_stream_cutoff_settles_budgeted_stream_cost_reservation(_dual_provider_env, monkeypatch):
    """Closing a budgeted stream on structural cutoff deterministically triggers cancelled-stream
    cost reservation settlement (no leaked cost reservation)."""
    from services.usage.model_call_guard import _BudgetedAsyncStream, drain_pending_cost_settlements

    monkeypatch.setattr(agent_runtime, "AGENT_PRE_ANSWER_THINKING_MAX_CHARS", 60)

    settled: list[tuple[Any, int]] = []

    def fake_settle_cancelled(reservation, *, streamed_output_bytes):
        settled.append((reservation, streamed_output_bytes))

    monkeypatch.setattr("services.usage.model_call_guard.settle_cancelled_stream_call", fake_settle_cancelled)

    class _Inner:
        def __aiter__(self):
            async def gen():
                yield SimpleNamespace(type="response.created", sequence_number=0)
                for idx in range(1, 10):
                    yield SimpleNamespace(
                        type="response.reasoning_text.delta",
                        delta="思考" * 20,
                        sequence_number=idx,
                    )
                    await asyncio.sleep(0)

            return gen()

        async def close(self):
            return None

    budgeted = _BudgetedAsyncStream(_Inner(), reservation="res-cutoff-1")

    class _WrappedSdkModel:
        async def stream_response(self, *_args, **_kwargs):
            async with budgeted as stream:
                async for chunk in stream:
                    yield chunk

    monkeypatch.setattr(sdk_model, "_sdk_model_for", lambda _p: _WrappedSdkModel())

    model = sdk_model.build_failover_chat_model("sess-budget-settle")
    _ = [
        ev
        async for ev in model.stream_response(
            "sys", [], ModelSettings(), [], None, [], None
        )
    ]
    await drain_pending_cost_settlements()

    assert len(settled) == 1
    assert settled[0][0] == "res-cutoff-1"
    assert settled[0][1] > 0


# ---------------------------------------------------------------------------
# 3. IntraRunToolOutputTrimmer wrap-up skip + AgentRunMeter peak & draining
# ---------------------------------------------------------------------------


def _big_read_items() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = [{"role": "user", "content": "整理大纲"}]
    for idx in range(4):
        items.append({"type": "function_call", "call_id": f"r{idx}", "name": "query_files", "arguments": "{}"})
        items.append(
            {
                "type": "function_call_output",
                "call_id": f"r{idx}",
                "output": json.dumps(
                    {"status": "success", "data": [{"id": f"f{idx}", "title": f"第{idx}章", "content": "字" * 8_000}]},
                    ensure_ascii=False,
                ),
            }
        )
    return items


@pytest.mark.unit
def test_intra_run_trimmer_skips_trimming_once_past_wrap_up_or_draining(monkeypatch):
    items = _big_read_items()
    budget = 10_000  # well below items size so trimming WOULD fire normally

    # Before wrap-up: trims normally
    active_meter = AgentRunMeter(started_at=time.monotonic(), wrap_up_after_s=540)
    trimmer_active = IntraRunToolOutputTrimmer(max_input_chars=budget, run_meter=active_meter)
    trimmed = trimmer_active.trim_items(items)
    assert trimmed is not items
    assert any(COMPACTION_REASON in str(it.get("output", "")) for it in trimmed)

    # Past wrap-up (wall-clock): strictly skipped, returns exact same ModelInputData & items!
    wrap_up_meter = AgentRunMeter(started_at=time.monotonic() - 600, wrap_up_after_s=540)
    trimmer_wrap = IntraRunToolOutputTrimmer(max_input_chars=budget, run_meter=wrap_up_meter)
    md = ModelInputData(input=items, instructions="系统提示")
    out = trimmer_wrap(CallModelData(model_data=md, agent=None, context=None))
    assert out is md
    assert out.input is items

    # Wall-clock landing already injected (even before wrap_up_after_s): strictly skipped!
    injected_meter = AgentRunMeter(started_at=time.monotonic(), wrap_up_after_s=540)
    injected_meter.record_wall_clock_landing_call()
    trimmer_injected = IntraRunToolOutputTrimmer(max_input_chars=budget, run_meter=injected_meter)
    assert trimmer_injected.trim_items(items) is items

    # Server draining: strictly skipped!
    monkeypatch.setattr("agent.core.run_meter.is_draining", lambda: True)
    drain_meter = AgentRunMeter(started_at=time.monotonic(), wrap_up_after_s=540)
    assert drain_meter.server_draining is True
    assert drain_meter.is_draining_stop is True
    trimmer_drain = IntraRunToolOutputTrimmer(max_input_chars=budget, run_meter=drain_meter)
    assert trimmer_drain.trim_items(items) is items


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_meter_tracks_untrimmed_max_prompt_tokens_and_preserves_served_callback(monkeypatch):
    served_models: list[str] = []
    meter = AgentRunMeter(
        started_at=time.monotonic(),
        wrap_up_after_s=540,
        on_served_model=served_models.append,
    )
    meter.record_served_model("MiniMax-M3.1-Flash")
    assert served_models == ["MiniMax-M3.1-Flash"]
    assert meter.last_served_model == "MiniMax-M3.1-Flash"

    # Simulate calls where a later trimmed call drops from 425k -> 239k.
    hooks = build_agent_run_hooks("MiniMax-M3.1-Flash", run_meter=meter)
    fake_agent = SimpleNamespace(
        model=SimpleNamespace(
            served_model="MiniMax-M3.1-Flash",
            served_key_slot="primary",
            take_call_timing=lambda: None,
            take_thinking_before_answer=lambda: None,
        )
    )
    with patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record"):
        await hooks.on_llm_end(None, fake_agent, SimpleNamespace(usage=SimpleNamespace(input_tokens=425_000, output_tokens=1_200)))
        await hooks.on_llm_end(None, fake_agent, SimpleNamespace(usage=SimpleNamespace(input_tokens=239_000, output_tokens=300)))

    assert meter.last_prompt_tokens == 239_000
    assert meter.max_prompt_tokens == 425_000
    assert meter.effective_prompt_tokens == 425_000

    # Draining stop vs wall-clock wrap-up distinction.
    monkeypatch.setattr("agent.core.run_meter.is_draining", lambda: False)
    assert meter.server_draining is False
    assert meter.is_draining_stop is False

    overdue = AgentRunMeter(started_at=time.monotonic() - 600, wrap_up_after_s=540)
    monkeypatch.setattr("agent.core.run_meter.is_draining", lambda: True)
    assert overdue.server_draining is True
    assert overdue.is_draining_stop is False


# ---------------------------------------------------------------------------
# 4. RepeatReadGuard cross-mode per-file cap
# ---------------------------------------------------------------------------


def _read_ok(file_id: str = "f1", title: str = "长篇总纲") -> str:
    return json.dumps(
        {"status": "success", "data": [{"id": file_id, "title": title, "content": "内容……"}]},
        ensure_ascii=False,
    )


def _guarded_read(guard: RepeatReadGuard, args: dict[str, Any], output: str | None = None) -> dict[str, Any]:
    raw = json.dumps(args, ensure_ascii=False)
    plan = guard.plan("query_files", raw)
    if plan.blocked_output is not None:
        return json.loads(plan.blocked_output)
    return json.loads(guard.observe("query_files", plan, output or _read_ok(str(args.get("id") or "f1"))))


@pytest.mark.unit
def test_repeat_read_guard_blocks_cross_mode_range_section_query_bypass():
    """Mixing full / section / start_char / tail_chars / query / summary on one unmodified
    file cannot bypass RepeatReadGuard to loop 11 reads: warns at 5, blocks at 6, trips no_progress."""
    assert FILE_TOTAL_READ_WARN_AT == 5
    assert FILE_TOTAL_READ_BLOCK_AT == 6

    guard = RepeatReadGuard()
    mixed_calls = [
        {"id": "f1", "response_mode": "full"},
        {"id": "f1", "section": "第一卷"},
        {"id": "f1", "start_char": 0, "max_chars": 2000},
        {"id": "f1", "tail_chars": 1500},
        {"id": "f1", "query": "青玉佩"},  # 5th total read -> warn
    ]
    outputs = [_guarded_read(guard, call) for call in mixed_calls]
    assert all(out["status"] == "success" for out in outputs)
    # Reads 1–4 across distinct modes remain quiet (no false positives on legitimate multi-window reads).
    assert all("read_hint" not in out for out in outputs[:4])
    # Read 5 warns clearly about cumulative cross-mode reads.
    assert outputs[4]["read_hint"].startswith("警告")
    assert "累计读取 5 次" in outputs[4]["read_hint"]
    assert outputs[4]["repeated_reads"] == 5

    # Reads 6, 7, 8 with brand-new ranges/queries are ALL blocked and trip NoProgressTrip.
    extra_bypass_attempts = [
        {"id": "f1", "start_char": 2000, "max_chars": 2000},
        {"id": "f1", "query": "新关键词"},
        {"id": "f1", "response_mode": "summary", "content_preview_chars": 500},
    ]
    for idx, call in enumerate(extra_bypass_attempts):
        blocked = _guarded_read(guard, call)
        assert blocked["status"] == "error"
        assert blocked["error_type"] == REPEATED_READ_ERROR_TYPE
        assert "累计读取 5 次" in blocked["error"]
        if idx < MAX_BLOCKED_READS_PER_REQUEST - 1:
            assert not guard.is_open
    assert guard.is_open
    assert guard.trip is not None and guard.trip.blocked_reads == MAX_BLOCKED_READS_PER_REQUEST


@pytest.mark.unit
def test_repeat_read_guard_cross_mode_cap_resets_only_for_modified_file_and_new_run():
    guard = RepeatReadGuard()
    for idx in range(FILE_TOTAL_READ_BLOCK_AT - 1):
        _guarded_read(guard, {"id": "f1", "start_char": idx * 500, "max_chars": 500})
        _guarded_read(guard, {"id": "f2", "start_char": idx * 500, "max_chars": 500}, _read_ok("f2", "角色表"))

    # Edit f1 -> resets f1's cross-mode total ONLY; f2 stays blocked.
    edit_args = json.dumps({"id": "f1", "edits": []})
    guard.observe(
        "edit_file",
        guard.plan("edit_file", edit_args),
        json.dumps({"status": "success", "data": {"id": "f1", "title": "长篇总纲"}}, ensure_ascii=False),
    )
    resumed_f1 = _guarded_read(guard, {"id": "f1", "query": "修改后复核"})
    assert resumed_f1["status"] == "success" and "read_hint" not in resumed_f1

    blocked_f2 = _guarded_read(guard, {"id": "f2", "query": "换关键词"}, _read_ok("f2", "角色表"))
    assert blocked_f2["error_type"] == REPEATED_READ_ERROR_TYPE

    # Partial batch query_files(ids=[f2, f3]) blocks only f2 and executes f3.
    batch_plan = guard.plan("query_files", json.dumps({"ids": ["f2", "f3"]}))
    assert batch_plan.blocked_output is None
    assert json.loads(batch_plan.arguments)["ids"] == ["f3"]
    assert [key[1] for key in batch_plan.blocked_keys] == ["f2"]

    # New agent run resets all read totals.
    guard.begin_agent_run()
    fresh_f2 = _guarded_read(guard, {"id": "f2", "query": "新run"}, _read_ok("f2", "角色表"))
    assert fresh_f2["status"] == "success" and "read_hint" not in fresh_f2


# ---------------------------------------------------------------------------
# 5. Runaway telemetry classification in api/agent.py
# ---------------------------------------------------------------------------


def _make_settlement() -> _AgentStreamSettlement:
    return _AgentStreamSettlement(
        session=None,  # type: ignore[arg-type]
        user_id="u-1",
        project_id="p-1",
        agent_run_id="run-1",
        charged_period_start=datetime.now(UTC),
        lang="zh",
        knows_stop_contract=True,
        activation_entry="workbench",
        run_outcome=RunOutcome(),
    )


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("frame", "expected_runaway", "expected_wall_clock_seen"),
    [
        # Clean wall-clock soft-landing -> NOT a runaway!
        (
            _sse("iteration_exhausted", {"layer": "tool_call", "limit": WALL_CLOCK_LIMIT, "iterations_used": 11}),
            False,
            True,
        ),
        (
            _sse(
                "iteration_exhausted",
                {
                    "layer": "tool_call",
                    "limit": WALL_CLOCK_LIMIT,
                    "wall_clock_landing_exhausted": False,
                },
            ),
            False,
            True,
        ),
        # Planned deploy drain -> NOT a runaway!
        (
            _sse("iteration_exhausted", {"layer": "tool_call", "limit": "server_draining"}),
            False,
            True,
        ),
        (
            _sse(
                "iteration_exhausted",
                {"layer": "tool_call", "limit": WALL_CLOCK_LIMIT, "server_draining": True},
            ),
            False,
            True,
        ),
        # Collaboration pause card -> NOT a runaway!
        (
            _sse("iteration_exhausted", {"layer": "collaboration", "limit": WALL_CLOCK_LIMIT}),
            False,
            True,
        ),
        # Genuine runaways -> runaway_stop=True!
        (
            _sse(
                "iteration_exhausted",
                {
                    "layer": "tool_call",
                    "limit": WALL_CLOCK_LIMIT,
                    "wall_clock_landing_exhausted": True,
                },
            ),
            True,
            True,
        ),
        (
            _sse("iteration_exhausted", {"layer": "tool_call", "limit": "max_turns"}),
            True,
            False,
        ),
        (
            _sse("iteration_exhausted", {"layer": "tool_call", "limit": "model_call_budget"}),
            True,
            False,
        ),
        (
            _sse("error", {"reason": "no_progress", "code": ErrorCode.AGENT_NO_PROGRESS}),
            True,
            False,
        ),
        (
            _sse("error", {"reason": "repetition_loop", "code": ErrorCode.AGENT_REPETITION_LOOP}),
            True,
            False,
        ),
        (
            _sse("error", {"reason": "tool_failure_circuit_open", "code": ErrorCode.AGENT_TOOL_FAILURE_LIMIT}),
            True,
            False,
        ),
        (
            _sse("error", {"code": ErrorCode.AGENT_MODEL_CALL_LIMIT}),
            True,
            False,
        ),
        # Non-runaway error (e.g. upstream unavailable) -> NOT a runaway!
        (
            _sse("error", {"code": ErrorCode.AGENT_UPSTREAM_UNAVAILABLE}),
            False,
            False,
        ),
    ],
)
def test_agent_stream_settlement_runaway_stop_classification(
    frame: str, expected_runaway: bool, expected_wall_clock_seen: bool
):
    settlement = _make_settlement()
    settlement.observe_frame(frame)
    assert settlement.runaway_stop is expected_runaway
    assert settlement.wall_clock_card_seen is expected_wall_clock_seen


# ---------------------------------------------------------------------------
# 6. LOOP_WORKING_RULES batch-pacing & mechanical-reasoning rule
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_loop_working_rules_includes_concise_batch_pacing_and_brief_mechanical_reasoning():
    assert "超过 3 份文件" in LOOP_WORKING_RULES
    assert "2–3 份文件" in LOOP_WORKING_RULES
    assert "query_files / edit_file" in LOOP_WORKING_RULES
    assert "不在思考过程里预先起草整章正文" in LOOP_WORKING_RULES
