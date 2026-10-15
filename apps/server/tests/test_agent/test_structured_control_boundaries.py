"""Authority/state must not be inferred from presentation prose."""

import pytest

from agent.graph.router import RouterDecision, _normalize_router_payload, inherit_routing_after_clarification
from agent.graph.writing_graph import _build_completion_task_payload
from agent.tools.task_progress import ArtifactEvidence


def test_legacy_range_title_cannot_prove_body_delivery():
    task = {"task": "写第1-10章正文", "status": "in_progress"}

    assert _build_completion_task_payload([task], ["第1-10章大纲"]) is None


def test_wrong_typed_artifact_cannot_complete_a_body_range():
    task = {
        "task": "写第1-10章正文",
        "status": "in_progress",
        "artifact_file_type": "draft",
        "unit_spec": {"unit": "章", "numbers": list(range(1, 11))},
    }
    outline = ArtifactEvidence(id="outline-1", title="第1-10章大纲", file_type="outline", metadata={})

    assert _build_completion_task_payload([task], [outline]) is None


def test_bare_question_does_not_skip_routing_for_a_short_reply():
    state = {
        "user_message": "可以",
        "messages": [{
            "role": "assistant",
            "content": "要我继续写第二章吗？",
            "routing": {
                "initial_agent": "writer", "final_agent": "writer",
                "workflow_type": "quick", "write_content": True, "read_only": False,
            },
        }],
    }

    assert inherit_routing_after_clarification(state) is None


def test_structured_clarification_preserves_routing_and_scope():
    state = {
        "user_message": "隐忍一点",
        "messages": [{
            "role": "assistant", "content": "请选择性格。", "clarification_pending": True,
            "routing": {
                "initial_agent": "planner", "final_agent": "planner",
                "workflow_type": "standard", "write_content": True, "read_only": False,
                "scope": "只写第1章", "requested_file_types": ["draft"],
            },
        }],
    }

    result = inherit_routing_after_clarification(state)

    assert result is not None
    assert result["routing_metadata"]["scope"] == "只写第1章"
    assert result["routing_metadata"]["requested_file_types"] == ["draft"]


@pytest.mark.parametrize("value", ["not_planner_but_writer", "planner_writer", "untrusted_writer"])
def test_router_does_not_substring_coerce_agent_tokens(value):
    with pytest.raises(ValueError):
        RouterDecision.model_validate(_normalize_router_payload({
            "agent_type": value, "workflow_type": "quick", "read_only": False,
        }))


def test_router_deliverables_are_exact_typed_values():
    result = RouterDecision.model_validate(_normalize_router_payload({
        "agent_type": "planner", "workflow_type": "standard", "read_only": False,
        "requested_file_types": ["outline", "draft"],
    }))

    assert result.requested_file_types == ["outline", "draft"]


@pytest.mark.parametrize("value", [["not_outline"], "outline", ["outline", 3]])
def test_invalid_router_deliverables_never_trigger_correction(value):
    with pytest.raises(ValueError):
        RouterDecision.model_validate(_normalize_router_payload({
            "agent_type": "planner", "workflow_type": "standard", "read_only": False,
            "requested_file_types": value,
        }))
