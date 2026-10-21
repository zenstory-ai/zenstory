"""Tests for centralized tool registry wiring."""

import json

import pytest


@pytest.mark.unit
def test_registry_alignment_has_no_missing_handlers():
    from agent.tools.registry import validate_registry_alignment

    diagnostics = validate_registry_alignment()
    assert diagnostics["missing_schemas"] == []
    assert diagnostics["missing_handlers"] == []
    assert diagnostics["invalid_quality_reviewer_tools"] == []


@pytest.mark.unit
def test_registry_has_no_retired_search_handler():
    from agent.tools.registry import TOOL_FUNCTIONS

    assert "hybrid_search" not in TOOL_FUNCTIONS


@pytest.mark.unit
def test_registry_contains_parallel_execute_handler():
    from agent.tools.registry import TOOL_FUNCTIONS

    assert "parallel_execute" in TOOL_FUNCTIONS
    assert callable(TOOL_FUNCTIONS["parallel_execute"])


def _parallel_task_types(tools):
    parallel = next(tool for tool in tools if tool["name"] == "parallel_execute")
    return parallel["input_schema"]["properties"]["tasks"]["items"]["properties"]["type"]["enum"]


@pytest.mark.unit
@pytest.mark.parametrize("legacy_flag", ["true", "false"])
@pytest.mark.parametrize("agent_type", ["writer", "planner", "hook_designer", "quality_reviewer"])
def test_retired_search_is_absent_even_with_legacy_flag(monkeypatch, agent_type, legacy_flag):
    from agent.tools.registry import get_agent_tools
    from agent.tools.tool_schemas import PARALLEL_EXECUTE_TOOL

    monkeypatch.setenv("AGENT_TOOL_HYBRID_SEARCH_ENABLED", legacy_flag)
    tools = get_agent_tools(agent_type)
    names = [tool["name"] for tool in tools]

    assert "hybrid_search" not in names
    assert "query_files" in names
    if "parallel_execute" in names:
        assert "hybrid_search" not in _parallel_task_types(tools)
        assert "query_files" in _parallel_task_types(tools)
        parallel = next(tool for tool in tools if tool["name"] == "parallel_execute")
        assert "hybrid_search" not in json.dumps(parallel, ensure_ascii=False)
    # The shared schema also contains only supported tasks.
    shared_enum = PARALLEL_EXECUTE_TOOL["input_schema"]["properties"]["tasks"]["items"]["properties"]["type"]["enum"]
    assert "hybrid_search" not in shared_enum
