"""Preserve existing successful router contracts without guessing unknown values."""
import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.graph.router import _parse_router_response, router_node


def _response(text):
    return {"content": [{"type": "text", "text": text}]}


@pytest.mark.parametrize("text,agent,workflow", [
    ('  {"agent_type":"writer","workflow_type":"quick"}  ', "writer", "quick"),
    ('```json\n{"agent_type":"planner","workflow_type":"standard"}\n```', "planner", "standard"),
    ('note {"ignored":true}\n{"agent_type":"quality_reviewer","workflow_type":"review_only"}', "quality_reviewer", "review_only"),
    ("planner\nstandard", "planner", "standard"),
    ("writer\nquick", "writer", "quick"),
    ('{"agent_type":"hook_designer"}', "hook_designer", "hook_focus"),
])
def test_existing_valid_formats_and_optional_fields_continue(text, agent, workflow):
    decision = _parse_router_response(_response(text))
    assert (decision.agent_type, decision.workflow_type) == (agent, workflow)


@pytest.mark.parametrize("read_only,write_content", [("true", "false"), (1, 0), (True, False)])
async def test_existing_known_boolean_forms_preserve_scope(read_only, write_content):
    payload = {"agent_type": "planner", "workflow_type": "full", "read_only": read_only,
               "write_content": write_content, "scope": "Only inspect the outline."}
    with patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=_response(json.dumps(payload)))):
        result = await router_node({"user_message": "Only inspect the outline."})
    assert result["workflow_agents"] == []
    metadata = result["routing_metadata"]
    assert metadata["read_only"] is True
    assert metadata["write_content"] is False
    assert metadata["scope"] == payload["scope"]
    assert metadata["requested_file_types"] == []


def test_later_valid_text_block_still_completes_without_new_model_call():
    response = {"content": [{"type": "text", "text": '{"agent_type":"unrecognized"}'},
                            {"type": "text", "text": '{"agent_type":"quality_reviewer","workflow_type":"review_only","read_only":true}'}]}
    assert _parse_router_response(response).agent_type == "quality_reviewer"


@pytest.mark.parametrize("text", [
    "{'agent_type':'planner','workflow_type':'standard','read_only':true,'write_content':false,'scope':'Inspect only',}",
    '{"agent_type":"planner","workflow_type":"standard","read_only":true,"write_content":false,"scope":"Inspect only",}',
])
def test_existing_complete_syntax_repairs_preserve_restrictions(text):
    decision = _parse_router_response(_response(text))
    assert decision.agent_type == "planner"
    assert decision.read_only is True
    assert decision.write_content is False
    assert decision.scope == "Inspect only"


def test_resume_with_malformed_recorded_agent_fails_closed():
    from agent.graph.router import RouterDecision, RouterFailure, _resolve_turn_decision
    state = {"user_message": "继续", "messages": [{"role": "assistant", "content": "Synthetic pause.",
             "stop_reason": "max_turns_exceeded", "last_agent": "unrecognized",
             "routing": {"initial_agent": "unrecognized", "workflow_type": "quick"}}]}
    with pytest.raises(RouterFailure):
        _resolve_turn_decision(state, RouterDecision(
            agent_type="writer", workflow_type="quick",
            turn_disposition="resume_interrupted_task",
        ))


def test_clarification_with_malformed_task_owner_fails_closed():
    from agent.graph.router import RouterDecision, RouterFailure, _resolve_turn_decision
    state = {"user_message": "B", "messages": [{"role": "assistant", "content": "A or B?",
             "clarification_pending": True, "routing": {"initial_agent": "unrecognized", "write_content": False}}]}
    with pytest.raises(RouterFailure):
        _resolve_turn_decision(state, RouterDecision(
            agent_type="planner", workflow_type="standard",
            turn_disposition="answer_pending_clarification",
        ))


@pytest.mark.parametrize("workflow,planned", [("quick", []), ("standard", ["writer"])])
async def test_read_only_that_contradicts_a_write_request_does_not_block_writing(workflow, planned):
    """Production (2026-10-26 10:19): 「一并扩到2000字先」 was routed read_only=true together with
    write_content=true, and the writer pasted the expanded chapter into the chat instead of saving it.
    A decision that both forbids file changes and asks for prose is not a read-only request."""
    payload = {"agent_type": "writer", "workflow_type": workflow, "read_only": True, "write_content": True,
               "scope": "第一章保持现有情节扩至2000字以上", "requested_file_types": []}
    with patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=_response(json.dumps(payload)))):
        result = await router_node({"user_message": "一并扩到2000字先"})
    metadata = result["routing_metadata"]
    assert metadata["read_only"] is False
    assert metadata["write_content"] is True
    assert result["workflow_agents"] == planned
