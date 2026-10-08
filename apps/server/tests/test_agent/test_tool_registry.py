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
def test_registry_contains_hybrid_search_handler():
    # The handler stays registered even when the tool is hidden from agents, so a
    # stray call (history, parallel sub-task) gets the recoverable tool_disabled error.
    from agent.tools.registry import TOOL_FUNCTIONS

    assert "hybrid_search" in TOOL_FUNCTIONS
    assert callable(TOOL_FUNCTIONS["hybrid_search"])


@pytest.mark.unit
def test_registry_contains_parallel_execute_handler():
    from agent.tools.registry import TOOL_FUNCTIONS

    assert "parallel_execute" in TOOL_FUNCTIONS
    assert callable(TOOL_FUNCTIONS["parallel_execute"])


def _parallel_task_types(tools):
    parallel = next(tool for tool in tools if tool["name"] == "parallel_execute")
    return parallel["input_schema"]["properties"]["tasks"]["items"]["properties"]["type"]["enum"]


@pytest.mark.unit
@pytest.mark.parametrize("agent_type", ["writer", "quality_reviewer"])
def test_toolset_exposes_hybrid_search_only_when_enabled(monkeypatch, agent_type):
    from agent.tools.registry import get_agent_tools

    monkeypatch.setenv("AGENT_TOOL_HYBRID_SEARCH_ENABLED", "true")
    tool_names = [tool["name"] for tool in get_agent_tools(agent_type)]
    assert "hybrid_search" in tool_names
    assert "semantic_search" not in tool_names


@pytest.mark.unit
def test_parallel_execute_offers_hybrid_search_task_when_enabled(monkeypatch):
    from agent.tools.registry import get_agent_tools

    monkeypatch.setenv("AGENT_TOOL_HYBRID_SEARCH_ENABLED", "true")
    assert "hybrid_search" in _parallel_task_types(get_agent_tools("writer"))


@pytest.mark.unit
@pytest.mark.parametrize("agent_type", ["writer", "planner", "hook_designer", "quality_reviewer"])
def test_hybrid_search_hidden_from_agents_when_disabled(monkeypatch, agent_type):
    from agent.tools.parallel_executor import PARALLEL_EXECUTE_TOOL
    from agent.tools.registry import get_agent_tools

    monkeypatch.setenv("AGENT_TOOL_HYBRID_SEARCH_ENABLED", "false")
    tools = get_agent_tools(agent_type)
    names = [tool["name"] for tool in tools]

    assert "hybrid_search" not in names
    assert "query_files" in names
    if "parallel_execute" in names:
        assert "hybrid_search" not in _parallel_task_types(tools)
        assert "query_files" in _parallel_task_types(tools)
        parallel = next(tool for tool in tools if tool["name"] == "parallel_execute")
        assert "hybrid_search" not in json.dumps(parallel, ensure_ascii=False)
    # The disabled view never mutates the shared schema object.
    shared_enum = PARALLEL_EXECUTE_TOOL["input_schema"]["properties"]["tasks"]["items"]["properties"]["type"]["enum"]
    assert "hybrid_search" in shared_enum
