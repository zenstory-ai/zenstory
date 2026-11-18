"""Cross-layer timing regressions with synthetic SDK/tool events only.

These tests exercise the production runner boundaries without
calling a model provider or persisting author content.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.run_meter import AgentRunMeter
from agent.core.workflow_events import StreamEvent, StreamEventType


class _SyntheticResult:
    def __init__(self, stream, *, raw_responses=None) -> None:
        self._stream = stream
        self.raw_responses = list(raw_responses or [])
        self.cancel_mode: str | None = None

    def cancel(self, mode="immediate") -> None:
        self.cancel_mode = mode

    def stream_events(self):
        return self._stream()

    def to_input_list(self):
        return []


def _response_usage(*, input_tokens: int, output_tokens: int):
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_hooks_record_completed_model_and_preserve_usage_terminal():
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    meter = AgentRunMeter()
    hook_holder = {}
    response = SimpleNamespace(usage=_response_usage(input_tokens=2, output_tokens=3))
    raw_response = SimpleNamespace(usage=response.usage, output=[])

    async def stream():
        hooks = hook_holder["hooks"]
        await hooks.on_llm_start(None, SimpleNamespace(model=None), "synthetic", [])
        await hooks.on_llm_end(None, SimpleNamespace(model=None), response)
        yield SimpleNamespace(
            type="raw_response_event",
            data=SimpleNamespace(type="response.output_text.delta", delta="synthetic reply"),
        )

    result = _SyntheticResult(stream, raw_responses=[raw_response])

    def run_streamed(*_args, **kwargs):
        hook_holder["hooks"] = kwargs["hooks"]
        return result

    with (
        patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="synthetic-model")),
        patch("agent.openai_agents.runner._build_agent", return_value=object()),
        patch("agents.Runner.run_streamed", side_effect=run_streamed),
        patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record") as schedule_usage,
    ):
        events = [
            event
            async for event in run_openai_agents_streaming_agent(
                state={
                    "user_message": "synthetic request",
                    "messages": [],
                    "system_prompt": "synthetic system",
                    "run_meter": meter,
                },
                agent_type="loop",
                system_prompt="synthetic system",
            )
        ]

    assert [event.type for event in events] == [
        StreamEventType.MESSAGE_START,
        StreamEventType.TEXT,
        StreamEventType.MESSAGE_END,
    ]
    assert events[-1].data == {
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
    }
    schedule_usage.assert_called_once()
    report = meter.timing_report()["stage_timings"]
    assert report["model"]["count"] == 1
    assert report["model"]["error_count"] == 0
    assert report["model"]["interrupted_count"] == 0
    assert report["agent_loop"]["count"] == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_closes_pending_model_timing_as_error_without_fabricating_usage():
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    meter = AgentRunMeter()
    hook_holder = {}

    async def stream():
        await hook_holder["hooks"].on_llm_start(
            None, SimpleNamespace(model=None), "synthetic", []
        )
        raise RuntimeError("synthetic provider failure")
        yield  # pragma: no cover - keeps this an async generator

    def run_streamed(*_args, **kwargs):
        hook_holder["hooks"] = kwargs["hooks"]
        return _SyntheticResult(stream)

    with (
        patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="synthetic-model")),
        patch("agent.openai_agents.runner._build_agent", return_value=object()),
        patch("agents.Runner.run_streamed", side_effect=run_streamed),
        patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record") as schedule_usage,
    ):
        events = [
            event
            async for event in run_openai_agents_streaming_agent(
                state={
                    "user_message": "synthetic request",
                    "messages": [],
                    "system_prompt": "synthetic system",
                    "run_meter": meter,
                },
                agent_type="loop",
                system_prompt="synthetic system",
            )
        ]

    assert [event.type for event in events] == [
        StreamEventType.MESSAGE_START,
        StreamEventType.ERROR,
    ]
    schedule_usage.assert_not_called()
    report = meter.timing_report()["stage_timings"]
    assert report["model"]["error_count"] == 1
    assert report["agent_loop"]["error_count"] == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_closes_pending_model_timing_as_interrupted_on_cancellation():
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    meter = AgentRunMeter()
    hook_holder = {}
    model_started = asyncio.Event()
    never_finishes = asyncio.Event()

    async def stream():
        await hook_holder["hooks"].on_llm_start(
            None, SimpleNamespace(model=None), "synthetic", []
        )
        model_started.set()
        await never_finishes.wait()
        yield  # pragma: no cover - cancellation ends the stream first

    def run_streamed(*_args, **kwargs):
        hook_holder["hooks"] = kwargs["hooks"]
        return _SyntheticResult(stream)

    with (
        patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="synthetic-model")),
        patch("agent.openai_agents.runner._build_agent", return_value=object()),
        patch("agents.Runner.run_streamed", side_effect=run_streamed),
        patch("agent.openai_agents.usage_hooks.schedule_llm_usage_record") as schedule_usage,
    ):
        event_stream = run_openai_agents_streaming_agent(
            state={
                "user_message": "synthetic request",
                "messages": [],
                "system_prompt": "synthetic system",
                "run_meter": meter,
            },
            agent_type="loop",
            system_prompt="synthetic system",
        )
        first = await event_stream.__anext__()
        assert first.type == StreamEventType.MESSAGE_START
        pending = asyncio.create_task(event_stream.__anext__())
        await model_started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        await event_stream.aclose()

    schedule_usage.assert_not_called()
    report = meter.timing_report()["stage_timings"]
    assert report["model"]["interrupted_count"] == 1
    assert report["agent_loop"]["interrupted_count"] == 1


def _tool_result(call_id: str, name: str, payload: dict) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.TOOL_RESULT,
        data={
            "tool_use_id": call_id,
            "name": name,
            "result": {
                "content": [
                    {"type": "text", "text": json.dumps(payload, ensure_ascii=False)}
                ]
            },
        },
    )
