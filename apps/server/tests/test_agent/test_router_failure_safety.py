"""Synthetic router failures must never grant file-writing authority (#265)."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.router import _parse_router_response, router_node
from agent.graph.writing_graph import run_writing_workflow_streaming
from agent.stream_adapter import StreamAdapter

USAGE = {"input_tokens": 17, "output_tokens": 3, "total_tokens": 20}
SENSITIVE = "synthetic-private-prose-do-not-log"


def _response(payload, **extra):
    text = json.dumps(payload) if isinstance(payload, dict) else payload
    return {"content": [{"type": "text", "text": text}], "usage": USAGE, **extra}


@pytest.mark.parametrize("payload", [
    {"agent_type": "writer-like", "workflow_type": "quick"},
    {"agent_type": "writer", "workflow_type": "almost-quick"},
    {"agent_type": "writer|planner", "workflow_type": "quick|full"},
    {"reason": "missing both required route fields"},
    {"agent_type": "writer", "workflow_type": None},
    {"agent_type": None, "workflow_type": "quick"},
    "",
    '{"agent_type":"writer","workflow_type":"quick","read_only":',
])
async def test_untrusted_route_never_returns_a_writer_default(payload):
    route = AsyncMock(return_value=_response(payload))
    with patch("agent.graph.router._route_with_deepseek_chat", route):
        with pytest.raises(ValueError):
            await router_node({"user_message": "Only inspect the synthetic outline; do not edit."})
    route.assert_awaited_once()


@pytest.mark.parametrize("payload", [
    {"agent_type": "unavailable", "workflow_type": "full", "read_only": True},
    {"agent_type": "planner", "workflow_type": "unknown", "write_content": False,
     "scope": "only outline", "requested_file_types": ["outline"]},
    {"agent_type": "reviewer-like", "workflow_type": "review-like", "read_only": True,
     "write_content": False, "scope": "review only"},
])
async def test_full_graph_stops_before_any_agent_or_tool_and_keeps_usage(payload):
    calls = []

    async def forbidden_agent(state, agent_type, **_kwargs):
        calls.append(agent_type)
        yield StreamEvent(StreamEventType.TEXT, {"text": "Would have expanded author scope."})

    route = AsyncMock(return_value=_response(payload))
    state = {"user_message": "Inspect the synthetic material without expanding scope.",
             "messages": [], "system_prompt": "", "read_only": True, "generation_mode": "quality"}
    adapter = StreamAdapter()
    with (
        patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "llm"}),
        patch("agent.graph.router._route_with_deepseek_chat", route),
        patch("agent.graph.writing_graph.run_streaming_agent", forbidden_agent),
    ):
        events = [event async for event in run_writing_workflow_streaming(
            state=state, thread_id="synthetic-router-failure",
        )]

        async def source():
            for event in events:
                yield event

        frames = [frame async for frame in adapter.process_workflow_events(source())]

    assert calls == []
    assert state["read_only"] is True
    route.assert_awaited_once()
    assert not any(event.type in {
        StreamEventType.AGENT_SELECTED, StreamEventType.TOOL_USE, StreamEventType.HANDOFF,
        StreamEventType.ROUTER_DECIDED, StreamEventType.WORKFLOW_COMPLETE,
    } for event in events)
    [error] = [event for event in events if event.type == StreamEventType.ERROR]
    assert error.data["refundable"] is True
    assert error.data["retryable"] is True
    assert error.data["code"] == "ERR_AGENT_ROUTING_FAILED"
    assert error.data["error"] == "这次没能处理你的要求，作品没有改动。可以稍后重试。"
    names = [getattr(frame.type, "value", frame.type) for frame in frames]
    assert names.count("error") == 1
    assert "done" not in names
    assert adapter.get_last_message_metadata()["usage"] == USAGE


async def test_router_validation_logs_only_fixed_categories(caplog):
    route = AsyncMock(return_value=_response({
        "agent_type": SENSITIVE, "workflow_type": "quick", "read_only": True,
    }, finish_reason="stop"))
    with patch("agent.graph.router._route_with_deepseek_chat", route):
        with pytest.raises(ValueError) as error:
            await router_node({"user_message": SENSITIVE})
    assert SENSITIVE not in str(error.value)
    assert SENSITIVE not in caplog.text
    assert "input_value" not in caplog.text
    [record] = [r for r in caplog.records if r.getMessage() == "Router decision failed; stopping before agent execution"]
    fields = record.custom_fields
    assert fields["category"] == "invalid_enum"
    assert fields["failed_fields"] == ("agent_type",)
    assert fields["parse_path"] == "strict_json"
    assert fields["finish_reason"] == "stop"
    assert fields["has_usage"] is True
    assert SENSITIVE not in repr(fields)


async def test_provider_exception_is_safe_and_not_retried(caplog):
    route = AsyncMock(side_effect=ConnectionError(SENSITIVE))
    with patch("agent.graph.router._route_with_deepseek_chat", route):
        with pytest.raises(ValueError) as error:
            await router_node({"user_message": "Only inspect the synthetic outline."})
    route.assert_awaited_once()
    assert error.value.category == "provider_failure"
    assert SENSITIVE not in caplog.text
    assert not any(record.exc_info for record in caplog.records)


async def test_truncated_response_is_not_a_valid_route_even_if_json_is_complete():
    response = _response({"agent_type": "writer", "workflow_type": "quick"}, finish_reason="length")
    with patch("agent.graph.router._route_with_deepseek_chat", AsyncMock(return_value=response)):
        with pytest.raises(ValueError):
            await router_node({"user_message": "Inspect only."})


def test_router_prompt_example_is_an_actual_valid_decision():
    from agent.prompts.subagents import ROUTER_PROMPT

    decoder = json.JSONDecoder()
    start = ROUTER_PROMPT.index('{\n  "agent_type"')
    payload, _ = decoder.raw_decode(ROUTER_PROMPT[start:])
    decision = _parse_router_response(_response(payload))
    assert decision.agent_type in {"planner", "hook_designer", "writer", "quality_reviewer"}


@pytest.mark.parametrize("field,value", [
    ("write_content", None), ("read_only", None), ("scope", None),
    ("requested_file_types", None), ("write_content", "unknown"),
    ("read_only", "unknown"), ("scope", []), ("requested_file_types", "outline"),
])
def test_explicit_ill_typed_json_never_defaults_authority(field, value):
    payload = {
        "agent_type": "writer", "workflow_type": "quick",
        "write_content": False, "read_only": True,
        "scope": "Inspect only.", "requested_file_types": [],
    }
    payload[field] = value
    with pytest.raises(ValueError, match="schema"):
        _parse_router_response(_response(payload))


@pytest.mark.parametrize("value", ["tru", "fals", "t"])
def test_syntax_repair_does_not_complete_unknown_permission_values(value):
    response = _response('{"agent_type":"writer","workflow_type":"quick","read_only":' + value + '}')
    with pytest.raises(ValueError):
        _parse_router_response(response)


def test_syntax_repair_does_not_finish_an_unclosed_scope_value():
    response = _response('{"agent_type":"writer","workflow_type":"quick","scope":"Inspect only}')
    with pytest.raises(ValueError):
        _parse_router_response(response)


# Live replays of the production invalid-enum turns: the router model refused in prose.
# A two-line refusal used to be read as two invalid enum values.
_TWO_LINE_REFUSAL = (
    "Sorry, I can't help continue this synthetic passage.\n"
    "If you are writing fiction, I can help with plot, pacing or character motivation instead."
)
_ONE_LINE_REFUSAL = "Sorry, I can't help with this request."


@pytest.mark.parametrize("text", [_TWO_LINE_REFUSAL, _ONE_LINE_REFUSAL])
def test_prose_reply_is_a_declined_request_not_an_enum_error(text):
    with pytest.raises(ValueError) as error:
        _parse_router_response(_response(text, finish_reason="stop"))

    assert error.value.category == "declined"
    assert error.value.error_info.code == "ERR_AGENT_REQUEST_DECLINED"
    assert error.value.error_info.retryable is False
    assert error.value.error_info.refundable is True


def test_exact_two_line_enum_reply_still_routes():
    decision = _parse_router_response(_response("Writer\nquick"))

    assert (decision.agent_type, decision.workflow_type) == ("writer", "quick")


async def test_declined_route_ends_the_round_without_retry_or_any_agent():
    calls = []

    async def forbidden_agent(state, agent_type, **_kwargs):
        calls.append(agent_type)
        yield StreamEvent(StreamEventType.TEXT, {"text": "should not run"})

    route = AsyncMock(return_value=_response(_TWO_LINE_REFUSAL, finish_reason="stop"))
    state = {"user_message": "Continue the synthetic passage.", "messages": [],
             "system_prompt": "", "generation_mode": "quality"}
    with (
        patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "llm"}),
        patch("agent.graph.router._route_with_deepseek_chat", route),
        patch("agent.graph.writing_graph.run_streaming_agent", forbidden_agent),
    ):
        events = [event async for event in run_writing_workflow_streaming(
            state=state, thread_id="synthetic-router-declined",
        )]

    assert calls == []
    route.assert_awaited_once()
    [error] = [event for event in events if event.type == StreamEventType.ERROR]
    assert error.data["code"] == "ERR_AGENT_REQUEST_DECLINED"
    assert error.data["retryable"] is False
    assert error.data["refundable"] is True
    assert error.data["reason"] == "router_declined"
