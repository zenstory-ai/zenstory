"""Synthetic monotonic-clock tests; no model requests or author content."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.core.run_meter import AgentRunMeter
from agent.openai_agents.usage_hooks import build_agent_run_hooks


def test_stage_totals_max_and_interrupted_outcomes(monkeypatch):
    clock = iter([10.0, 12.0, 13.0, 16.0, 20.0, 24.0])
    monkeypatch.setattr("agent.core.run_meter.time.monotonic", lambda: next(clock))
    meter = AgentRunMeter(started_at=0.0)
    with meter.measure_stage("router"):
        pass
    with pytest.raises(ValueError), meter.measure_stage("router"):
        raise ValueError("synthetic-private-exception-must-not-appear")
    with pytest.raises(asyncio.CancelledError), meter.measure_stage("router"):
        raise asyncio.CancelledError
    timing = meter.timing_report()["stage_timings"]["router"]
    assert timing == {
        "count": 3,
        "total_ms": 9000.0,
        "max_ms": 4000.0,
        "error_count": 1,
        "interrupted_count": 1,
    }
    assert "private" not in str(meter.timing_report())


def test_first_boundaries_are_idempotent_and_not_budget_start(monkeypatch):
    monkeypatch.setattr("agent.core.run_meter.time.monotonic", lambda: 12.0)
    meter = AgentRunMeter(started_at=10.0, timing_origin=8.0)
    assert meter.timing_report()["first_body_persisted_ms"] is None
    meter.mark_first_tool_started()
    meter.mark_first_body_persisted()
    monkeypatch.setattr("agent.core.run_meter.time.monotonic", lambda: 20.0)
    meter.mark_first_tool_started()
    meter.mark_first_body_persisted()
    assert meter.first_tool_started_ms == 4000.0
    assert meter.first_body_persisted_ms == 4000.0
    assert meter.elapsed_s() == 10.0  # Existing wrap-up budget unchanged.


@pytest.mark.asyncio
async def test_model_hooks_exclude_tool_gap_and_keep_real_usage(monkeypatch):
    now = [10.0]
    monkeypatch.setattr("agent.openai_agents.usage_hooks.time.monotonic", lambda: now[0])
    meter = AgentRunMeter(started_at=0.0)
    hooks = build_agent_run_hooks("synthetic-model", run_meter=meter)
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=2, output_tokens=3))
    with patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record") as schedule:
        await hooks.on_llm_start(None, None, "private-input", [])
        now[0] = 12.0
        await hooks.on_llm_end(None, None, response)
        now[0] = 90.0  # Tool execution is not model time.
        await hooks.on_llm_start(None, None, "private-input", [])
        now[0] = 93.0
        hooks.finish_timing("interrupted")
        hooks.finish_timing("interrupted")  # Close is idempotent.
    assert schedule.call_count == 1
    assert schedule.call_args.kwargs["usage"] is response.usage
    assert meter.timing_report()["stage_timings"]["model"] == {
        "count": 2,
        "total_ms": 5000.0,
        "max_ms": 3000.0,
        "error_count": 0,
        "interrupted_count": 1,
    }


def test_role_timing_keeps_legacy_sdk_run_counter():
    meter = AgentRunMeter()
    meter.record_agent_run(123.0, agent_type="planner")
    meter.record_agent_run(300.0, agent_type="writer", outcome="interrupted")
    assert (meter.agent_runs, meter.llm_duration_ms) == (2, 423.0)
    report = meter.timing_report()
    assert report["stage_timings"]["agent_planner"]["total_ms"] == 123.0
    assert report["stage_timings"]["agent_writer"]["interrupted_count"] == 1
