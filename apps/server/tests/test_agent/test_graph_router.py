"""
Tests for agent/graph/router.py

Tests the router node for the writing workflow.
"""

from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.unit
class TestGetNextNode:
    """Tests for get_next_node function."""

    def test_get_next_node_planner(self):
        """Test routing to planner."""
        from agent.graph.router import get_next_node

        state = {"current_agent": "planner"}
        result = get_next_node(state)

        assert result == "planner"

    def test_get_next_node_writer(self):
        """Test routing to writer."""
        from agent.graph.router import get_next_node

        state = {"current_agent": "writer"}
        result = get_next_node(state)

        assert result == "writer"

    def test_get_next_node_legacy_reviewer_is_rejected(self):
        """Legacy reviewer alias should no longer be accepted."""
        from agent.graph.router import get_next_node

        state = {"current_agent": "reviewer"}
        with pytest.raises(ValueError):
            get_next_node(state)

    def test_get_next_node_default(self):
        """An unknown agent is not authorization to run writer."""
        from agent.graph.router import get_next_node

        state = {"current_agent": "unknown"}
        with pytest.raises(ValueError):
            get_next_node(state)

    def test_get_next_node_missing_key(self):
        """A missing agent is not authorization to run writer."""
        from agent.graph.router import get_next_node

        state = {}
        with pytest.raises(ValueError):
            get_next_node(state)


@pytest.mark.integration
class TestRouterNode:
    """Tests for router_node async function."""

    async def test_router_node_empty_message(self):
        """Test router defaults to writer for empty message."""
        from agent.graph.router import router_node

        state = {"user_message": ""}
        with pytest.raises(ValueError):
            await router_node(state)

    async def test_router_node_with_message(self):
        """Test router with valid message."""
        from agent.graph.router import router_node

        route_response = {
            "content": [
                {
                    "type": "text",
                    "text": (
                        '{"agent_type":"planner","workflow_type":"standard",'
                        '"reason":"需要先规划","confidence":0.95}'
                    ),
                }
            ]
        }
        with patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=route_response)):
            state = {"user_message": "Plan a story outline"}
            result = await router_node(state)

            assert result["current_agent"] == "planner"
            assert result["workflow_plan"] == "standard"
            assert result["routing_metadata"]["reason"] == "需要先规划"

    async def test_router_node_error_is_rejected(self):
        """Router errors must not expand author scope."""
        from agent.graph.router import router_node

        with patch("agent.graph.router._route_with_chat_model", AsyncMock(side_effect=ValueError("API Error"))):
            state = {"user_message": "Some message"}
            with pytest.raises(ValueError):
                await router_node(state)


@pytest.mark.unit
class TestParseRouterResponse:
    """Tests for structured router response parsing."""

    def test_parse_router_response_supports_legacy_two_line_output(self):
        """Keep the existing exact-token legacy contract without guessing unknown enums."""
        from agent.graph.router import _parse_router_response

        response = {"content": [{"type": "text", "text": "planner\nstandard"}]}
        decision = _parse_router_response(response)
        assert decision.agent_type == "planner"
        assert decision.workflow_type == "standard"

    def test_parse_router_response_rejects_legacy_reviewer_alias(self):
        """Invalid agent tokens are rejected rather than substring/alias coerced."""
        from agent.graph.router import _parse_router_response

        response = {"content": [{"type": "text", "text": '{"agent":"reviewer","workflow":"review_only"}'}]}
        with pytest.raises(ValueError):
            _parse_router_response(response)

    def test_parse_router_response_extracts_relevant_json_when_multiple_objects_present(self):
        """Should prefer JSON object containing routing keys over unrelated objects."""
        from agent.graph.router import _parse_router_response

        response = {
            "content": [{
                "type": "text",
                "text": (
                    'note {"foo":"bar"}\n'
                    '{"agent_type":"planner","workflow_type":"standard","reason":"需要规划","confidence":0.9,'
                    '"write_content":false,"read_only":false,"scope":"","requested_file_types":[]}'
                ),
            }],
        }

        decision = _parse_router_response(response)
        assert decision.agent_type == "planner"
        assert decision.workflow_type == "standard"
        assert decision.reason == "需要规划"

    def test_parse_router_response_rejects_when_only_unrelated_json_is_present(self):
        """Unrelated JSON cannot authorize writer/quick."""
        from agent.graph.router import _parse_router_response

        response = {
            "content": [{"type": "text", "text": '{"foo":"bar","confidence":"oops"}'}],
        }

        with pytest.raises(ValueError):
            _parse_router_response(response)


@pytest.mark.unit
class TestNormalizeRouterPayload:
    def test_normalize_router_payload_maps_aliases_and_clamps_confidence(self):
        from agent.graph.router import _normalize_router_payload

        normalized = _normalize_router_payload(
            {
                "target_agent": "quality_reviewer",
                "workflow_plan": "review_only",
                "reason": "needs review",
                "confidence": 4,
            }
        )

        assert normalized["agent_type"] == "quality_reviewer"
        assert normalized["workflow_type"] == "review_only"
        assert normalized["reason"] == "needs review"
        assert normalized["confidence"] == 1.0

    def test_normalize_router_payload_infers_hook_focus_for_hook_designer(self):
        from agent.graph.router import _normalize_router_payload

        normalized = _normalize_router_payload({"agent": "hook_designer"})

        assert normalized["agent_type"] == "hook_designer"
        assert normalized["workflow_type"] == "hook_focus"

    def test_normalize_router_payload_does_not_salvage_unknown_workflow_token(self):
        from agent.graph.router import _normalize_router_payload

        normalized = _normalize_router_payload(
            {"agent_type": "planner", "workflow_type": "mystery", "confidence": "nope"}
        )

        assert normalized["agent_type"] == "planner"
        assert normalized["workflow_type"] == "mystery"
        assert normalized["confidence"] == 0.0


@pytest.mark.unit
class TestExtractRouterPayloadUnifiedJsonRepair:
    """5.2: verify unified JSON-repair path handles malformed/markdown-wrapped outputs."""

    def test_markdown_fenced_json_is_extracted(self):
        """JSON inside a markdown code fence should be parsed correctly."""
        from agent.graph.router import _extract_router_payload

        text = '```json\n{"agent_type":"writer","workflow_type":"quick","reason":"simple","confidence":0.8}\n```'
        payload = _extract_router_payload(text)

        assert payload.get("agent_type") == "writer"
        assert payload.get("workflow_type") == "quick"

    def test_multi_object_text_prefers_routing_keyed_object(self):
        """When text contains multiple JSON objects, prefer the one with routing keys."""
        from agent.graph.router import _extract_router_payload

        text = (
            'note {"foo":"bar"}\n'
            '{"agent_type":"planner","workflow_type":"standard","reason":"需要规划","confidence":0.9}'
        )
        payload = _extract_router_payload(text)

        assert payload.get("agent_type") == "planner"
        assert payload.get("workflow_type") == "standard"

    def test_unrelated_json_returns_fallback_object(self):
        """A JSON object without routing keys is still returned as best-effort fallback."""
        from agent.graph.router import _extract_router_payload

        payload = _extract_router_payload('{"foo":"bar"}')

        # Must return the dict (not empty), normalization handles defaults.
        assert isinstance(payload, dict)

    def test_legacy_two_line_still_works(self):
        """Non-JSON text with agent/workflow on separate lines still parses."""
        from agent.graph.router import _extract_router_payload

        payload = _extract_router_payload("writer\nquick")

        assert payload.get("agent_type") == "writer"
        assert payload.get("workflow_type") == "quick"


@pytest.mark.unit
class TestRouterUserScope:
    """用户范围字段（write_content / read_only / scope）与计划交接序列的裁剪。"""

    def _decision(self, **overrides):
        from agent.graph.router import RouterDecision

        fields = {"agent_type": "planner", "workflow_type": "standard"}
        fields.update(overrides)
        return RouterDecision(**fields)

    def test_planning_only_request_drops_planned_writer(self):
        """只要大纲/人设（write_content=false）时，standard 不再计划 writer。"""
        from agent.graph.router import plan_workflow_agents

        assert plan_workflow_agents(self._decision(write_content=False)) == []

    def test_plan_then_write_keeps_planned_writer(self):
        from agent.graph.router import plan_workflow_agents

        assert plan_workflow_agents(self._decision(write_content=True)) == ["writer"]

    def test_missing_write_content_keeps_workflow_sequence(self):
        """旧格式输出没有 write_content（None）时沿用 workflow_type 的原始序列。"""
        from agent.graph.router import plan_workflow_agents

        assert plan_workflow_agents(self._decision()) == ["writer"]

    def test_full_workflow_without_content_keeps_non_writer_stages(self):
        from agent.graph.router import plan_workflow_agents

        decision = self._decision(workflow_type="full", write_content=False)
        assert plan_workflow_agents(decision) == ["hook_designer"]

    def test_hook_ideas_only_drops_planned_writer(self):
        from agent.graph.router import plan_workflow_agents

        decision = self._decision(
            agent_type="hook_designer", workflow_type="hook_focus", write_content=False
        )
        assert plan_workflow_agents(decision) == []

    @pytest.mark.parametrize("workflow_type", ["standard", "full", "hook_focus", "quick"])
    def test_read_only_clears_every_planned_agent(self, workflow_type):
        from agent.graph.router import plan_workflow_agents

        decision = self._decision(workflow_type=workflow_type, write_content=True, read_only=True)
        assert plan_workflow_agents(decision) == []

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (True, True),
            (False, False),
            ("true", True),
            ("False", False),
            ("是", True),
            ("否", False),
            (1, True),
            (0, False),
            (None, None),
            ("maybe", None),
            (7, None),
        ],
    )
    def test_write_content_is_parsed_leniently(self, raw, expected):
        from agent.graph.router import _normalize_router_payload

        normalized = _normalize_router_payload(
            {"agent_type": "planner", "workflow_type": "standard", "write_content": raw}
        )
        assert normalized["write_content"] is expected

    def test_read_only_requires_explicit_true(self):
        from agent.graph.router import _normalize_router_payload

        base = {"agent_type": "writer", "workflow_type": "quick"}
        assert _normalize_router_payload({**base, "read_only": "true"})["read_only"] is True
        assert _normalize_router_payload({**base, "read_only": "unknown"})["read_only"] is False
        assert _normalize_router_payload(base)["read_only"] is False

    def test_scope_is_trimmed_and_capped(self):
        from agent.graph.router import MAX_SCOPE_CHARS, _normalize_router_payload

        normalized = _normalize_router_payload(
            {"agent_type": "writer", "workflow_type": "quick", "scope": "  只写第1章  "}
        )
        assert normalized["scope"] == "只写第1章"

        long_scope = _normalize_router_payload(
            {"agent_type": "writer", "workflow_type": "quick", "scope": "章" * 500}
        )
        assert len(long_scope["scope"]) == MAX_SCOPE_CHARS

        non_string = _normalize_router_payload(
            {"agent_type": "writer", "workflow_type": "quick", "scope": ["x"]}
        )
        assert non_string["scope"] == ""

    async def test_router_node_outline_only_request_plans_no_writer(self):
        from agent.graph.router import router_node

        route_response = {
            "content": [
                {
                    "type": "text",
                    "text": (
                        '{"agent_type":"planner","workflow_type":"standard",'
                        '"write_content":false,"read_only":false,'
                        '"scope":"只要故事大纲","reason":"只要大纲","confidence":0.9}'
                    ),
                }
            ]
        }
        with patch(
            "agent.graph.router._route_with_chat_model",
            AsyncMock(return_value=route_response),
        ):
            result = await router_node({"user_message": "先给我故事大纲"})

        assert result["current_agent"] == "planner"
        assert result["workflow_agents"] == []
        assert result["routing_metadata"]["write_content"] is False
        assert result["routing_metadata"]["scope"] == "只要故事大纲"

    def test_router_prompt_asks_for_scope_fields(self):
        from agent.prompts.subagents import ROUTER_PROMPT

        for field in ("write_content", "read_only", "scope"):
            assert field in ROUTER_PROMPT
