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


def test_resume_without_a_valid_recorded_agent_routes_normally():
    from agent.graph.router import resume_route_after_exhaustion
    state = {"user_message": "继续", "messages": [{"role": "assistant", "content": "Synthetic pause.",
             "stop_reason": "max_turns_exceeded", "last_agent": "unrecognized"}]}
    assert resume_route_after_exhaustion(state) is None


def test_clarification_without_a_valid_task_owner_routes_normally():
    from agent.graph.router import inherit_routing_after_clarification
    state = {"user_message": "B", "messages": [{"role": "assistant", "content": "A or B?",
             "clarification_pending": True, "routing": {"initial_agent": "unrecognized", "write_content": False}}]}
    assert inherit_routing_after_clarification(state) is None
