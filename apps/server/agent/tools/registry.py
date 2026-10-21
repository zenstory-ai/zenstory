"""
Tool registry: central source for tool schema/handler wiring.

This module provides one place to assemble:
- Tool schema lists
- MCP handler dispatch map
- Agent-type -> toolset mapping
"""

import json
from typing import Any

from agent.tools.mcp_tools import MCP_TOOL_HANDLERS
from agent.tools.parallel_executor import execute_parallel
from agent.tools.tool_schemas import TOOL_SCHEMAS

DEFAULT_TOOL_NAMES: list[str] = [
    "create_file",
    "edit_file",
    "delete_file",
    "rename_file",
    "query_files",
    "update_project",
    "handoff_to_agent",
    "request_clarification",
    "parallel_execute",
    "load_skill",
    "read_skill_resource",
]

QUALITY_REVIEWER_TOOL_NAMES: list[str] = [
    "query_files",
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
    "rename_file",
    "parallel_execute",
})

# 每个角色在请求里看到的工具清单都是这一份（顺序、描述都相同），前缀缓存不按角色分裂。
# 角色能执行哪些工具仍由 AGENT_TOOL_NAME_MAP 决定，越权调用在 tools_adapter 里拒绝。
EXPOSED_TOOL_NAMES: list[str] = DEFAULT_TOOL_NAMES

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


def get_agent_tools(agent_type: str) -> list[dict[str, Any]]:
    """Return the tools a role may execute, without handing out the cached list itself."""
    return list(AGENT_TOOLS_MAP.get(agent_type, AGENT_TOOLS_MAP["writer"]))


def get_exposed_tools() -> list[dict[str, Any]]:
    """The tool list every role sees in its requests (permissions are checked per call)."""
    return [TOOL_SCHEMAS[name] for name in EXPOSED_TOOL_NAMES if name in TOOL_SCHEMAS]


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
