"""Tests for centralized tool registry wiring."""

import json

import pytest


@pytest.mark.unit
def test_registry_alignment_has_no_missing_handlers():
    from agent.tools.registry import validate_registry_alignment

    diagnostics = validate_registry_alignment()
    assert diagnostics["missing_schemas"] == []
    assert diagnostics["missing_handlers"] == []


@pytest.mark.unit
def test_registry_has_no_retired_search_handler():
    from agent.tools.registry import TOOL_FUNCTIONS

    assert "hybrid_search" not in TOOL_FUNCTIONS


@pytest.mark.unit
def test_registry_has_no_graph_control_flow_tools():
    from agent.tools.registry import TOOL_FUNCTIONS
    from agent.tools.tool_schemas import TOOL_SCHEMAS

    for name in ("handoff_to_agent", "request_clarification", "parallel_execute"):
        assert name not in TOOL_FUNCTIONS
        assert name not in TOOL_SCHEMAS


@pytest.mark.unit
@pytest.mark.parametrize("legacy_flag", ["true", "false"])
def test_retired_search_is_absent_even_with_legacy_flag(monkeypatch, legacy_flag):
    from agent.tools.registry import get_exposed_tools

    monkeypatch.setenv("AGENT_TOOL_HYBRID_SEARCH_ENABLED", legacy_flag)
    tools = get_exposed_tools()
    names = [tool["name"] for tool in tools]

    assert "hybrid_search" not in names
    assert "query_files" in names
    assert "hybrid_search" not in json.dumps(tools, ensure_ascii=False)
