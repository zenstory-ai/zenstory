"""Cross-layer timing regressions with synthetic SDK/tool events only.

These tests exercise the production runner and writing graph boundaries without
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
from agent.graph.router import RouterFailure


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
                agent_type="writer",
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
    assert report["agent_writer"]["count"] == 1


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
                agent_type="writer",
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
    assert report["agent_writer"]["error_count"] == 1


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
            agent_type="writer",
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
    assert report["agent_writer"]["interrupted_count"] == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_graph_times_router_success_without_changing_scope_or_call_count():
    from agent.graph.writing_graph import run_writing_workflow_streaming

    meter = AgentRunMeter()
    routing_metadata = {
        "agent_type": "quality_reviewer",
        "workflow_type": "review_only",
        "reason": "synthetic",
        "confidence": 1.0,
        "read_only": True,
        "write_content": False,
        "scope": "review chapter three only",
    }
    router = AsyncMock(
        return_value={
            "current_agent": "quality_reviewer",
            "workflow_plan": "review_only",
            "workflow_agents": [],
            "routing_metadata": routing_metadata,
        }
    )

    async def fake_agent(*_args, **_kwargs):
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "review complete [TASK_COMPLETE]"})

    state = {
        "user_message": "synthetic request",
        "messages": [],
        "system_prompt": "",
        "run_meter": meter,
    }
    with (
        patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "llm"}),
        patch("agent.graph.writing_graph.router_node", router),
        patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
    ):
        events = [
            event
            async for event in run_writing_workflow_streaming(
                state=state,
                thread_id="timing-router-success",
            )
        ]

    router.assert_awaited_once()
    decided = next(event for event in events if event.type == StreamEventType.ROUTER_DECIDED)
    assert decided.data["routing_metadata"] == routing_metadata
    assert state["read_only"] is True
    assert "review chapter three only" in state["scope_directive"]
    timing = meter.timing_report()["stage_timings"]["router"]
    assert timing["count"] == 1
    assert timing["error_count"] == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_graph_times_router_format_failure_without_authorizing_scope_or_agent():
    from agent.graph.writing_graph import run_writing_workflow_streaming

    meter = AgentRunMeter()
    usage = {"input_tokens": 7, "output_tokens": 2, "total_tokens": 9}
    router = AsyncMock(
        side_effect=RouterFailure(
            "invalid_enum",
            usage=usage,
            failed_fields=("agent_type",),
            parse_path="json",
        )
    )
    run_agent = AsyncMock()
    state = {
        "user_message": "synthetic request",
        "messages": [],
        "system_prompt": "",
        "run_meter": meter,
    }

    with (
        patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "llm"}),
        patch("agent.graph.writing_graph.router_node", router),
        patch("agent.graph.writing_graph.run_streaming_agent", run_agent),
    ):
        events = [
            event
            async for event in run_writing_workflow_streaming(
                state=state,
                thread_id="timing-router-failure",
            )
        ]

    router.assert_awaited_once()
    run_agent.assert_not_awaited()
    assert [event.type for event in events] == [
        StreamEventType.ROUTER_THINKING,
        StreamEventType.MESSAGE_END,
        StreamEventType.ERROR,
    ]
    assert events[1].data["usage"] == usage
    assert events[2].data["reason"] == "router_invalid_enum"
    assert "read_only" not in state
    assert "scope_directive" not in state
    timing = meter.timing_report()["stage_timings"]["router"]
    assert timing["error_count"] == 1


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


def _edit_events(
    *,
    file_type="draft",
    status="success",
    new_word_count=None,
    mutation_applied=True,
):
    call_id = "edit-1"
    data = {"id": "file-1", "title": "Synthetic", "file_type": file_type}
    if new_word_count is not None:
        data["new_word_count"] = new_word_count
    payload = (
        {"status": status, "data": data}
        if status in {"success", "partial"}
        else {"status": "error", "error_type": "synthetic_failure"}
    )
    if mutation_applied is not None and status in {"success", "partial"}:
        payload["mutation_applied"] = mutation_applied
    return [
        StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={
                "id": call_id,
                "name": "edit_file",
                "status": "complete",
                "input": {
                    "id": "file-1",
                    "edits": [{"op": "append", "text": "synthetic body"}],
                },
            },
        ),
        _tool_result(call_id, "edit_file", payload),
    ]


def _parallel_events(
    *,
    content="synthetic body",
    task_status="completed",
    file_type="draft",
    mutation_applied=True,
):
    call_id = "parallel-1"
    task = {
        "type": "write_chapter",
        "params": {"id": "file-1", "title": "Synthetic", "content": content},
    }
    task_result = {
        "data": {"id": "file-1", "title": "Synthetic", "file_type": file_type},
    }
    if mutation_applied is not None:
        task_result["mutation_applied"] = mutation_applied
    result_task = {
        "type": "write_chapter",
        "status": task_status,
        "result": task_result,
    }
    return [
        StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={
                "id": call_id,
                "name": "parallel_execute",
                "status": "complete",
                "input": {"tasks": [task]},
            },
        ),
        _tool_result(
            call_id,
            "parallel_execute",
            {
                "status": "success",
                "mutation_applied": mutation_applied is not False,
                "data": {"tasks": [result_task]},
            },
        ),
    ]


def _parallel_body_noop_with_outline_change_events():
    call_id = "parallel-mixed"
    tasks = [
        {
            "type": "write_chapter",
            "params": {
                "id": "draft-1",
                "title": "Synthetic chapter",
                "content": "synthetic body",
            },
        },
        {
            "type": "edit_file",
            "params": {
                "id": "outline-1",
                "edits": [{"op": "append", "text": "synthetic outline"}],
            },
        },
    ]
    results = [
        {
            "type": "write_chapter",
            "status": "completed",
            "result": {
                "mutation_applied": False,
                "data": {
                    "id": "draft-1",
                    "title": "Synthetic chapter",
                    "file_type": "draft",
                },
            },
        },
        {
            "type": "edit_file",
            "status": "completed",
            "result": {
                "mutation_applied": True,
                "data": {
                    "id": "outline-1",
                    "title": "Synthetic outline",
                    "file_type": "outline",
                },
            },
        },
    ]
    return [
        StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={
                "id": call_id,
                "name": "parallel_execute",
                "status": "complete",
                "input": {"tasks": tasks},
            },
        ),
        _tool_result(
            call_id,
            "parallel_execute",
            {
                "status": "success",
                "mutation_applied": True,
                "data": {"tasks": results},
            },
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool_events", "expected_mark"),
    [
        pytest.param(_edit_events(new_word_count=12), True, id="edit-success"),
        pytest.param(
            _edit_events(new_word_count=12, mutation_applied=False),
            False,
            id="edit-noop-with-existing-body",
        ),
        pytest.param(
            _edit_events(new_word_count=12, mutation_applied=None),
            False,
            id="edit-missing-mutation-fact",
        ),
        pytest.param(
            _edit_events(status="partial", new_word_count=0),
            False,
            id="edit-partial-post-write-empty",
        ),
        pytest.param(_edit_events(status="error"), False, id="edit-failure"),
        pytest.param(_edit_events(file_type="outline", new_word_count=12), False, id="edit-non-body"),
        pytest.param(_parallel_events(), True, id="parallel-success"),
        pytest.param(_parallel_events(content="   "), False, id="parallel-empty"),
        pytest.param(_parallel_events(task_status="failed"), False, id="parallel-failure"),
        pytest.param(_parallel_events(file_type="outline"), False, id="parallel-non-body"),
        pytest.param(
            _parallel_events(mutation_applied=None),
            False,
            id="parallel-body-missing-mutation-fact",
        ),
        pytest.param(
            _parallel_body_noop_with_outline_change_events(),
            False,
            id="parallel-top-level-success-body-noop",
        ),
    ],
)
async def test_graph_marks_first_body_only_for_confirmed_nonempty_body_persistence(
    tool_events,
    expected_mark,
):
    from agent.graph.writing_graph import run_writing_workflow_streaming

    meter = AgentRunMeter()
    router = AsyncMock(
        return_value={
            "current_agent": "writer",
            "workflow_plan": "quick",
            "workflow_agents": [],
            "routing_metadata": {
                "agent_type": "writer",
                "workflow_type": "quick",
                "reason": "synthetic",
                "confidence": 1.0,
                "write_content": True,
            },
        }
    )

    async def fake_agent(*_args, **_kwargs):
        for event in tool_events:
            yield event
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "done [TASK_COMPLETE]"})

    with (
        patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "llm"}),
        patch("agent.graph.writing_graph.router_node", router),
        patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
    ):
        _ = [
            event
            async for event in run_writing_workflow_streaming(
                state={
                    "user_message": "synthetic request",
                    "messages": [],
                    "system_prompt": "",
                    "run_meter": meter,
                },
                thread_id="timing-body-persisted",
            )
        ]

    assert (meter.first_body_persisted_ms is not None) is expected_mark
