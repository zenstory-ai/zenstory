"""Mixed outline-and-draft requests keep outline delivery evidence before planned handoff."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from agent.core.workflow_events import StreamEvent, StreamEventType


def _route(payload: dict[str, object]) -> AsyncMock:
    return AsyncMock(
        return_value={"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
    )


async def _run(fake_agent):
    from agent.graph.writing_graph import run_writing_workflow_streaming

    route = _route(
        {
            "agent_type": "planner",
            "workflow_type": "standard",
            "write_content": True,
            "requested_file_types": ["outline", "draft"],
            "scope": "先交付大纲，再写第一章",
        }
    )
    with (
        patch.dict(
            "os.environ",
            {"AGENT_ROUTER_STRATEGY": "llm", "AGENT_ENABLE_GRAPH_AUTO_REVIEW": "false"},
        ),
        patch("agent.graph.router._route_with_chat_model", route),
        patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
    ):
        events = [
            event
            async for event in run_writing_workflow_streaming(
                state={
                    "user_message": "先写故事大纲，然后直接写第一章",
                    "router_message": "先写故事大纲，然后直接写第一章",
                    "messages": [],
                    "system_prompt": "",
                },
                thread_id="outline-plan-correction-mixed-request",
                max_iterations=5,
                auto_review_threshold=50,
            )
        ]
    route.assert_called_once()
    return events


async def test_mixed_request_corrects_missing_outline_once_before_planned_writer():
    calls: list[str] = []
    messages: list[str] = []

    async def fake_agent(state, agent_type, **_kwargs):
        calls.append(agent_type)
        messages.append(str(state.get("user_message") or ""))
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"{agent_type} finished."})

    with patch("agent.graph.writing_graph._written_outline_contents", return_value=[]) as read:
        await _run(fake_agent)

    assert calls == ["planner", "planner", "writer"]
    assert "尚未写进" in messages[1]
    assert read.call_count == 1


async def test_mixed_request_with_nonempty_outline_runs_planned_writer_without_correction():
    calls: list[str] = []

    async def fake_agent(_state, agent_type, **_kwargs):
        calls.append(agent_type)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"{agent_type} finished."})

    with patch(
        "agent.graph.writing_graph._written_outline_contents",
        return_value=["已落库的非空故事大纲"],
    ) as read:
        await _run(fake_agent)

    assert calls == ["planner", "writer"]
    read.assert_called_once()


async def test_mixed_request_outline_read_failure_does_not_schedule_blind_correction():
    calls: list[str] = []

    async def fake_agent(_state, agent_type, **_kwargs):
        calls.append(agent_type)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"{agent_type} finished."})

    with patch(
        "agent.graph.writing_graph._written_outline_contents",
        side_effect=RuntimeError("synthetic read failure"),
    ) as read:
        await _run(fake_agent)

    assert calls == ["planner", "writer"]
    read.assert_called_once()


async def test_explicit_writer_handoff_still_precedes_missing_outline_correction():
    calls: list[str] = []

    async def fake_agent(_state, agent_type, **_kwargs):
        calls.append(agent_type)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"{agent_type} finished."})
        if agent_type == "planner":
            yield StreamEvent(
                type=StreamEventType.HANDOFF,
                data={
                    "target_agent": "writer",
                    "reason": "按显式交接继续正文",
                    "context": "继续写第一章",
                },
            )

    with patch("agent.graph.writing_graph._written_outline_contents", return_value=[]) as read:
        await _run(fake_agent)

    assert calls == ["planner", "writer"]
    read.assert_not_called()
