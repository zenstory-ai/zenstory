"""Mixed outline-and-draft requests keep outline delivery evidence before planned handoff."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

from agent.core.workflow_events import StreamEvent, StreamEventType


def _route(payload: dict[str, object]) -> AsyncMock:
    return AsyncMock(
        return_value={"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
    )


def _mixed_route(**overrides: object) -> dict[str, object]:
    return {
        "agent_type": "planner",
        "workflow_type": "standard",
        "write_content": True,
        "requested_file_types": ["outline", "draft"],
        "scope": "先交付大纲，再写第一章",
        **overrides,
    }


async def _run(fake_agent, *, route_payload=None, messages=None):
    from agent.graph.writing_graph import run_writing_workflow_streaming

    route = _route(route_payload or _mixed_route())
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
                    "messages": list(messages or []),
                    "system_prompt": "",
                },
                thread_id="outline-plan-correction-mixed-request",
                max_iterations=5,
                auto_review_threshold=50,
            )
        ]
    route.assert_called_once()
    return events


def _trusted_planner_followup(*, clarification: bool) -> dict[str, object]:
    previous: dict[str, object] = {
        "role": "assistant",
        "content": "上一轮处理中途暂停。",
        "routing": {
            "initial_agent": "planner",
            "last_agent": "planner",
            "owner_agent": "planner",
            "workflow_type": "standard",
            "read_only": False,
            "write_content": True,
            "scope": "先交付大纲，再写第一章",
            "scope_constraints": [],
            "requested_file_types": ["outline", "draft"],
        },
    }
    if clarification:
        previous["clarification_pending"] = True
    else:
        previous["stop_reason"] = "no_progress"
    return previous


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


async def test_trusted_resume_does_not_recheck_outline_written_in_previous_turn():
    calls: list[str] = []

    async def fake_agent(_state, agent_type, **_kwargs):
        calls.append(agent_type)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"{agent_type} finished."})

    with patch("agent.graph.writing_graph._written_outline_contents", return_value=[]) as read:
        events = await _run(
            fake_agent,
            route_payload=_mixed_route(
                agent_type="writer",
                workflow_type="quick",
                turn_disposition="resume_interrupted_task",
                scope="只补本轮尚未完成的部分",
            ),
            messages=[_trusted_planner_followup(clarification=False)],
        )

    assert calls == ["planner", "writer"]
    read.assert_not_called()
    decided = next(event for event in events if event.type == StreamEventType.ROUTER_DECIDED)
    assert "followup_resolved" not in decided.data
    assert "_followup_resolved" not in decided.data["routing_metadata"]


async def test_trusted_clarification_answer_does_not_recheck_previous_outline():
    calls: list[str] = []

    async def fake_agent(_state, agent_type, **_kwargs):
        calls.append(agent_type)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"{agent_type} finished."})

    with patch("agent.graph.writing_graph._written_outline_contents", return_value=[]) as read:
        await _run(
            fake_agent,
            route_payload=_mixed_route(
                agent_type="writer",
                workflow_type="quick",
                turn_disposition="answer_pending_clarification",
                scope="人物关系按师徒处理",
            ),
            messages=[_trusted_planner_followup(clarification=True)],
        )

    assert calls == ["planner", "writer"]
    read.assert_not_called()


async def test_untrusted_followup_self_report_cannot_bypass_new_outline_delivery():
    calls: list[str] = []

    async def fake_agent(_state, agent_type, **_kwargs):
        calls.append(agent_type)
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"{agent_type} finished."})

    with patch("agent.graph.writing_graph._written_outline_contents", return_value=[]) as read:
        await _run(
            fake_agent,
            route_payload=_mixed_route(turn_disposition="resume_interrupted_task"),
        )

    assert calls == ["planner", "planner", "writer"]
    read.assert_called_once()
