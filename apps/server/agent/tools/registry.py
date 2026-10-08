"""
Tool registry: central source for tool schema/handler wiring.

This module provides one place to assemble:
- Tool schema lists
- MCP handler dispatch map
- Agent-type -> toolset mapping
"""

import copy
import json
from typing import Any

from agent.tools import mcp_tools
from agent.tools.mcp_tools import MCP_TOOL_HANDLERS
from agent.tools.parallel_executor import execute_parallel
from agent.tools.tool_schemas import TOOL_SCHEMAS

DEFAULT_TOOL_NAMES: list[str] = [
    "create_file",
    "edit_file",
    "delete_file",
    "query_files",
    "hybrid_search",
    "update_project",
    "handoff_to_agent",
    "request_clarification",
    "parallel_execute",
    "load_skill",
    "read_skill_resource",
]

QUALITY_REVIEWER_TOOL_NAMES: list[str] = [
    "query_files",
    "hybrid_search",
    "update_project",
    "handoff_to_agent",
    "request_clarification",
    "load_skill",
    "read_skill_resource",
]

# 会改动项目文件的工具（parallel_execute 的子任务就是 create/edit/delete，一并算写工具）。
# 两处共用：writing_graph._agent_can_write_files 据此判断 agent 是否有写权限；
# 用户明确要求「本轮不要改文件」时，tools_adapter 对这些工具的调用一律拒绝执行。
FILE_WRITE_TOOL_NAMES: frozenset[str] = frozenset({
    "create_file",
    "edit_file",
    "delete_file",
    "parallel_execute",
})

AGENT_TOOL_NAME_MAP: dict[str, list[str]] = {
    "planner": DEFAULT_TOOL_NAMES,
    "hook_designer": DEFAULT_TOOL_NAMES,
    "writer": DEFAULT_TOOL_NAMES,
    "quality_reviewer": QUALITY_REVIEWER_TOOL_NAMES,
}


async def _execute_parallel_tool(args: dict[str, Any]) -> dict[str, Any]:
    """Adapter for parallel_execute tool input contract."""
    tasks = args.get("tasks", [])
    if not isinstance(tasks, list):
        return {
            "content": [{
                "type": "text",
                "text": json.dumps({
                    "status": "error",
                    "error": "Invalid input for parallel_execute: 'tasks' must be an array.",
                }),
            }]
        }
    return await execute_parallel(tasks)

# Name -> MCP handler
TOOL_FUNCTIONS: dict[str, Any] = {
    **dict(MCP_TOOL_HANDLERS),
    "parallel_execute": _execute_parallel_tool,
}


def _build_tool_schemas(tool_names: list[str]) -> list[dict[str, Any]]:
    """Resolve Tool schema list from registry names."""
    return [TOOL_SCHEMAS[name] for name in tool_names if name in TOOL_SCHEMAS]


AGENT_TOOLS_MAP: dict[str, list[dict[str, Any]]] = {
    agent: _build_tool_schemas(tool_names)
    for agent, tool_names in AGENT_TOOL_NAME_MAP.items()
}


HYBRID_SEARCH_TOOL_NAME = "hybrid_search"


def is_hybrid_search_enabled() -> bool:
    """AGENT_TOOL_HYBRID_SEARCH_ENABLED 开关（运行时读取，测试可用 monkeypatch 切换）。"""
    return mcp_tools._is_hybrid_search_tool_enabled()


def _parallel_execute_schema_without_hybrid_search(schema: dict[str, Any]) -> dict[str, Any]:
    """检索关闭时的 parallel_execute schema：任务类型枚举与描述里去掉 hybrid_search。"""
    stripped = copy.deepcopy(schema)
    stripped["description"] = stripped["description"].replace(
        "query_files / hybrid_search", "query_files"
    )
    task_schema = stripped["input_schema"]["properties"]["tasks"]["items"]["properties"]
    type_schema = task_schema["type"]
    type_schema["enum"] = [name for name in type_schema["enum"] if name != HYBRID_SEARCH_TOOL_NAME]
    type_schema["description"] = "Task type: " + ", ".join(type_schema["enum"])
    task_schema["params"]["description"] = task_schema["params"]["description"].replace(
        "query_files / hybrid_search", "query_files"
    )
    return stripped


def get_agent_tools(agent_type: str) -> list[dict[str, Any]]:
    """Get Tool schema list by agent type.

    hybrid_search 关闭（AGENT_TOOL_HYBRID_SEARCH_ENABLED=false，生产当前如此）时，
    不把它暴露给模型：既不在工具清单里，也不在 parallel_execute 的任务类型枚举里。
    以前关闭时它仍在清单里、调用返回 success + 空结果，模型会反复换关键词重试。
    """
    tools = AGENT_TOOLS_MAP.get(agent_type, AGENT_TOOLS_MAP["writer"])

    # Defensive copy: never hand callers the cached AGENT_TOOLS_MAP list object,
    # so a caller that mutates the returned list can't corrupt the shared cache.
    if is_hybrid_search_enabled():
        return list(tools)
    return [
        _parallel_execute_schema_without_hybrid_search(tool) if tool["name"] == "parallel_execute" else tool
        for tool in tools
        if tool["name"] != HYBRID_SEARCH_TOOL_NAME
    ]


def validate_registry_alignment() -> dict[str, list[str]]:
    """
    Return alignment diagnostics between schema registry and handler registry.

    This is a lightweight helper for tests/diagnostics.
    """
    missing_schemas = [name for name in DEFAULT_TOOL_NAMES if name not in TOOL_SCHEMAS]
    missing_handlers = [name for name in DEFAULT_TOOL_NAMES if name not in TOOL_FUNCTIONS]
    quality_reviewer_missing = [
        name for name in QUALITY_REVIEWER_TOOL_NAMES if name not in DEFAULT_TOOL_NAMES
    ]
    return {
        "missing_schemas": missing_schemas,
        "missing_handlers": missing_handlers,
        "invalid_quality_reviewer_tools": quality_reviewer_missing,
    }
