"""
Tool registry: central source for tool schema/handler wiring.

This module provides one place to assemble:
- the single loop agent's tool list (the schemas its requests carry)
- MCP handler dispatch map
"""

from typing import Any

from agent.tools.mcp_tools import MCP_TOOL_HANDLERS
from agent.tools.tool_schemas import TOOL_SCHEMAS

# 单循环 agent（agent/loop）的工具集。正文按章号写：write_chapter 紧跟 create_file，create_file 不建正文。
# 顺序就是请求里工具清单的顺序：工具清单排在对话前面，换顺序会让整段前缀缓存失效。
LOOP_AGENT_TYPE = "loop"
LOOP_TOOL_NAMES: list[str] = [
    "create_file",
    "write_chapter",
    "edit_file",
    "delete_file",
    "rename_file",
    "query_files",
    "update_project",
    "load_skill",
    "read_skill_resource",
]

# Name -> MCP handler
TOOL_FUNCTIONS: dict[str, Any] = dict(MCP_TOOL_HANDLERS)


def get_exposed_tools() -> list[dict[str, Any]]:
    """The tool list the loop agent sees in its requests."""
    return [TOOL_SCHEMAS[name] for name in LOOP_TOOL_NAMES if name in TOOL_SCHEMAS]


def validate_registry_alignment() -> dict[str, list[str]]:
    """
    Return alignment diagnostics between schema registry and handler registry.

    This is a lightweight helper for tests/diagnostics.
    """
    return {
        "missing_schemas": [name for name in LOOP_TOOL_NAMES if name not in TOOL_SCHEMAS],
        "missing_handlers": [name for name in LOOP_TOOL_NAMES if name not in TOOL_FUNCTIONS],
    }
