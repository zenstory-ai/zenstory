"""Two-reply file protocol: the reply after create_file streams the body before any other tool."""

import json

import pytest

from agent.openai_agents.tools_adapter import build_agent_function_tools
from agent.tools.mcp_tools import ToolContext

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
def tools(monkeypatch):
    executed: list[str] = []

    def fake(name):
        async def _tool(_args):
            executed.append(name)
            return {"content": [{"type": "text", "text": json.dumps({"status": "success"})}]}

        return _tool

    monkeypatch.setattr(
        "agent.openai_agents.tools_adapter.TOOL_FUNCTIONS",
        {name: fake(name) for name in ("create_file", "update_project", "edit_file", "delete_file", "query_files")},
    )
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        yield {tool.name: tool for tool in build_agent_function_tools("writer")}, executed
    finally:
        ToolContext.clear_context()


async def _call(tool, args="{}") -> dict:
    return json.loads(await tool.on_invoke_tool(None, args))


async def test_reply_that_skips_the_body_cannot_call_other_tools(tools):
    by_name, executed = tools
    ToolContext.begin_model_reply()
    ToolContext.set_pending_empty_file("f1", "第一章")  # reply 1 ends with create_file
    ToolContext.begin_model_reply()  # reply 2 calls tools instead of streaming <file>

    refused = await _call(by_name["update_project"])
    edit = await _call(by_name["edit_file"], '{"id": "f1", "edits": []}')
    cleanup = await _call(by_name["delete_file"], '{"id": "f1"}')

    assert refused["error_type"] == "file_body_pending" and "第一章" in refused["error"]
    assert edit["error_type"] == "file_body_pending"
    assert executed == ["delete_file"]


async def test_body_then_next_create_file_in_the_same_reply_keeps_the_run_going(tools):
    """Bulk writing chains <file>chapter N</file> + create_file(chapter N+1) in one reply."""
    by_name, executed = tools
    ToolContext.begin_model_reply()
    ToolContext.set_pending_empty_file("f1", "第一章")
    ToolContext.begin_model_reply()
    ToolContext.mark_latest_pending_body_streamed()  # on_llm_end saw the closed <file> block

    result = await _call(by_name["create_file"], '{"title": "第二章"}')

    assert result["status"] == "success"
    assert executed == ["create_file"]


async def test_tools_in_the_reply_that_creates_the_file_still_run(tools):
    by_name, executed = tools
    ToolContext.begin_model_reply()
    ToolContext.set_pending_empty_file("f1", "第一章")

    assert (await _call(by_name["update_project"]))["status"] == "success"
    assert executed == ["update_project"]
