"""Per-item author restrictions survive routing, persistence and interrupted resumes."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.graph.router import RouterFailure, router_node


def _response(**updates):
    payload = {
        "agent_type": "writer",
        "workflow_type": "quick",
        "turn_disposition": "new_or_changed_request",
        "read_only": False,
        "write_content": True,
        "scope": "推演关系线并修改第二章日期",
        "scope_constraints": ["关系线仅推演，未经确认不写入任何文件", "仅修改第二章已确认的日期"],
        "requested_file_types": ["draft"],
        **updates,
    }
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "usage": {"input_tokens": 15, "output_tokens": 10, "total_tokens": 25},
    }


async def _route(response, previous=None):
    state = {"user_message": "第一项只推演，第二项按已确认日期修改。", "messages": []}
    if previous:
        state["messages"] = [{"role": "assistant", **previous}]
    with patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=response)) as model:
        result = await router_node(state)
    model.assert_awaited_once()
    return result


async def test_initial_mixed_request_keeps_item_restrictions_and_allowed_write():
    from agent.graph.writing_graph import _build_scope_directive

    result = await _route(_response())
    route = result["routing_metadata"]
    assert route["read_only"] is False
    assert route["write_content"] is True
    assert route["scope_constraints"] == [
        "推演关系线并修改第二章日期",
        "关系线仅推演，未经确认不写入任何文件",
        "仅修改第二章已确认的日期",
    ]
    directive = _build_scope_directive(route, agent_type="writer")
    assert "关系线仅推演，未经确认不写入任何文件" in directive
    assert "仅修改第二章已确认的日期" in directive


@pytest.mark.parametrize("disposition", ["resume_interrupted_task", "answer_pending_clarification"])
async def test_mixed_restrictions_survive_persisted_history_and_narrower_followup(disposition):
    from agent.core.session_loader import SessionLoader
    from agent.graph.router import build_router_user_message
    from agent.service import routing_from_router_decided

    result = await _route(_response())
    for _ in range(2):
        routing = routing_from_router_decided({
            "initial_agent": result["current_agent"],
            "workflow_plan": result["workflow_plan"],
            "routing_metadata": result["routing_metadata"],
        })
        previous = SessionLoader("project", "user")._format_chat_message_for_history(SimpleNamespace(
            id="synthetic-turn", role="assistant", content="部分日期修改尚未完成。", tool_calls=None,
            reasoning_content=None, message_metadata=json.dumps({
                "routing": routing, "stop_reason": "run_deadline_exceeded",
                "status_cards": [{"type": "workflow_stopped", "reason": "clarification_needed",
                                  "question": "日期按哪个时区？"}],
            }),
        ))
        router_input = build_router_user_message({"user_message": "接着做", "messages": [previous]})
        assert "关系线仅推演，未经确认不写入任何文件" in router_input
        result = await _route(_response(
            turn_disposition=disposition, scope="",
            scope_constraints=["保留第二章现有段落与人物称谓"],
        ), previous)
        restrictions = result["routing_metadata"]["scope_constraints"]
        assert "关系线仅推演，未经确认不写入任何文件" in restrictions
        assert "仅修改第二章已确认的日期" in restrictions
        assert restrictions.count("保留第二章现有段落与人物称谓") == 1
        assert result["routing_metadata"]["read_only"] is False


@pytest.mark.parametrize("constraints", [None, "只讨论", {}, [1], [""], ["x" * 81], [str(i) for i in range(9)]])
async def test_invalid_explicit_item_restrictions_fail_closed_without_losing_usage(constraints):
    response = _response(scope_constraints=constraints)
    with pytest.raises(RouterFailure) as caught:
        await _route(response)
    assert caught.value.category == "schema"
    assert caught.value.failed_fields == ("scope_constraints",)
    assert caught.value.usage == response["usage"]


async def test_accepted_proposal_keeps_its_own_item_limits_not_previous_read_only_scope():
    previous = {
        "content": "是否只执行已确认的第二项？", "clarification_pending": True,
        "routing": {"initial_agent": "quality_reviewer", "last_agent": "quality_reviewer",
                    "workflow_type": "review_only", "read_only": True, "write_content": False,
                    "scope": "只讨论全部问题", "scope_constraints": ["本轮所有文件只读"]},
    }
    result = await _route(_response(turn_disposition="answer_pending_clarification",
                                   accepts_previous_proposal=True), previous)
    restrictions = result["routing_metadata"]["scope_constraints"]
    assert "本轮所有文件只读" not in restrictions
    assert "关系线仅推演，未经确认不写入任何文件" in restrictions
    assert result["routing_metadata"]["read_only"] is False


async def test_unmatched_followup_keeps_current_item_limits():
    result = await _route(_response(turn_disposition="resume_interrupted_task"))
    assert "关系线仅推演，未经确认不写入任何文件" in result["routing_metadata"]["scope_constraints"]


async def test_summary_and_item_limits_share_one_bounded_budget_without_dropping_any_item():
    with pytest.raises(RouterFailure) as caught:
        await _route(_response(scope_constraints=[f"独立限制{i}" for i in range(8)]))
    assert caught.value.failed_fields == ("scope_constraints",)


async def test_new_task_does_not_inherit_restrictions_from_completed_previous_task():
    result = await _route(_response(), {
        "content": "此前任务已经结束。", "stop_reason": "end_turn",
        "routing": {"read_only": True, "scope_constraints": ["旧任务全部只读"]},
    })
    assert "旧任务全部只读" not in result["routing_metadata"]["scope_constraints"]
    assert "关系线仅推演，未经确认不写入任何文件" in result["routing_metadata"]["scope_constraints"]


async def test_legacy_route_without_item_constraints_remains_compatible():
    response = _response()
    payload = json.loads(response["content"][0]["text"])
    del payload["scope_constraints"]
    response["content"][0]["text"] = json.dumps(payload)
    result = await _route(response)
    assert result["routing_metadata"]["scope_constraints"] == ["推演关系线并修改第二章日期"]


@pytest.mark.parametrize("explicit_handoff", [False, True])
async def test_graph_keeps_item_limits_for_planner_and_handed_off_writer(explicit_handoff):
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.graph.nodes import build_agent_directive
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext

    captured = []

    async def fake_agent(state, agent_type, **_kwargs):
        captured.append((agent_type, build_agent_directive(state, agent_type)))
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "只按获准的日期调整交付。"})
        if explicit_handoff and agent_type == "planner":
            yield StreamEvent(type=StreamEventType.HANDOFF, data={
                "target_agent": "writer", "reason": "执行第二项", "context": "只改第二章日期",
            })

    ToolContext.set_context(session=None, user_id="user", project_id="project", session_id="mixed-scope")
    try:
        with (
            patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=_response(
                agent_type="planner", workflow_type="standard",
            ))),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
        ):
            events = [event async for event in run_writing_workflow_streaming(
                state={"user_message": "推演第一项，修改第二项日期", "messages": [],
                       "system_prompt": "", "generation_mode": "fast"},
                thread_id="mixed-scope",
            )]
    finally:
        ToolContext.clear_context()

    assert [agent for agent, _ in captured] == ["planner", "writer"]
    for _, directive in captured:
        assert "关系线仅推演，未经确认不写入任何文件" in directive
        assert "仅修改第二章已确认的日期" in directive
    route = next(event for event in events if event.type == StreamEventType.ROUTER_DECIDED)
    assert route.data["routing_metadata"]["read_only"] is False
    assert not any(event.type == StreamEventType.ERROR for event in events)
