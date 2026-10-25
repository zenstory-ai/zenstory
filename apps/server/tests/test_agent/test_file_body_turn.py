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
    ToolContext.set_pending_empty_file("f1", "第一章", "v1")  # reply 1 ends with create_file
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


# ---------------------------------------------------------------------------
# 待补正文期间的删除只放行“取消这份待补文件”（2026-10-25 事故：模型删到了另一份已写稿件）
# ---------------------------------------------------------------------------


def _overdue(file_id: str = "f1", title: str = "第一章", version: str | None = "v1") -> None:
    """version：create_file 新建时记下的版本；复用已有剧集时为 None。"""
    ToolContext.begin_model_reply()
    ToolContext.set_pending_empty_file(file_id, title, version)  # reply 1 ends with create_file
    ToolContext.begin_model_reply()  # reply 2 calls tools instead of streaming <file>


@pytest.mark.parametrize(
    "args",
    [
        '{"id": "other-draft"}',  # 另一份稿件
        '{"id": "folder-1", "recursive": true}',  # 父目录递归删除
        '{"id": "f1", "recursive": true}',  # 待补文件本身也不递归
        '{"id": "f1", "recursive": "true"}',
        '{"title": "第一章"}',  # 没有 id，不按标题猜
        "not json at all",
    ],
)
async def test_overdue_body_refuses_every_delete_but_the_exact_pending_file(tools, args):
    by_name, executed = tools
    _overdue()

    refused = await _call(by_name["delete_file"], args)

    assert refused["error_type"] == "file_body_pending"
    assert executed == []


@pytest.mark.parametrize("args", ['{"id": "f1"}', '{"id": "f1", "recursive": false}', '{"id": "f1", "recursive": "false"}'])
async def test_overdue_body_still_lets_the_model_cancel_that_exact_file(tools, args):
    by_name, executed = tools
    _overdue()

    assert (await _call(by_name["delete_file"], args))["status"] == "success"
    assert executed == ["delete_file"]


async def test_a_cancel_that_really_deleted_the_file_settles_the_guard_for_the_rest_of_the_run(monkeypatch, tools):
    by_name, executed = tools
    _overdue()

    await _call(by_name["delete_file"], '{"id": "f1"}')
    # 假的 delete_file 没有 mutation_applied：没确认删掉，守卫不动
    assert ToolContext.overdue_file_bodies() != []

    async def really_deletes(_args):
        executed.append("delete_file")
        return {"content": [{"type": "text", "text": json.dumps({"status": "success", "mutation_applied": True})}]}

    from agent.openai_agents import tools_adapter

    monkeypatch.setitem(tools_adapter.TOOL_FUNCTIONS, "delete_file", really_deletes)
    await _call(by_name["delete_file"], '{"id": "f1"}')
    assert ToolContext.overdue_file_bodies() == []
    assert (await _call(by_name["create_file"], '{"title": "第二章"}'))["status"] == "success"
    assert executed == ["delete_file", "delete_file", "create_file"]


async def test_a_reused_existing_episode_cannot_be_deleted_as_a_cancel(tools):
    """复用的已有剧集（没有本请求的建好版本）可能已有正文：精确 id 也不放行删除。"""
    by_name, executed = tools
    _overdue(version=None)

    assert (await _call(by_name["delete_file"], '{"id": "f1"}'))["error_type"] == "file_body_pending"
    assert executed == []


async def test_overdue_body_cancel_matches_any_overdue_file_not_only_the_first(tools):
    by_name, executed = tools
    ToolContext.begin_model_reply()
    ToolContext.set_pending_empty_file("f1", "第一章", "v1")
    ToolContext.set_pending_empty_file("f2", "第二章", "v2")
    ToolContext.begin_model_reply()

    assert (await _call(by_name["delete_file"], '{"id": "f2"}'))["status"] == "success"
    assert executed == ["delete_file"]


async def test_deletes_without_an_overdue_body_are_unchanged(tools):
    by_name, executed = tools
    ToolContext.begin_model_reply()

    assert (await _call(by_name["delete_file"], '{"id": "folder-1", "recursive": true}'))["status"] == "success"
    assert executed == ["delete_file"]


async def test_overdue_refusal_is_counted_once_in_the_run_summary_without_a_tool_start():
    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker

    executed: list[str] = []

    async def fake(_args):
        executed.append("update_project")
        return {"content": [{"type": "text", "text": json.dumps({"status": "success"})}]}

    meter = AgentRunMeter()
    breaker = ToolFailureBreaker()
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"update_project": fake})
            by_name = {
                tool.name: tool
                for tool in build_agent_function_tools("writer", failure_breaker=breaker, run_meter=meter)
            }
            _overdue()
            refused = await _call(by_name["update_project"])
    finally:
        ToolContext.clear_context()

    assert refused["error_type"] == "file_body_pending"
    assert executed == []
    report = meter.timing_report()
    assert report["tool_failures"] == {
        "update_project": {"count": 1, "rolled_back_batches": 0, "error_types": {"file_body_pending": 1}}
    }
    assert report["first_tool_started_ms"] is None
    # 仍计入请求级累计失败上限
    assert breaker.total_failures == 1 and not breaker.is_open


# ---------------------------------------------------------------------------
# runner：前置拒绝后同步结束当前 SDK run，交给 graph 的待补核验与纠偏
# ---------------------------------------------------------------------------


def _result(name: str, payload: dict) -> object:
    from types import SimpleNamespace

    return SimpleNamespace(tool=SimpleNamespace(name=name), output=json.dumps(payload, ensure_ascii=False))


PENDING_REFUSAL = {"status": "error", "error_type": "file_body_pending", "error": "x"}


async def test_run_ends_after_a_structured_pending_refusal_while_the_body_is_still_overdue(tools):
    from agent.openai_agents.runner import _stop_run_on_control_flow_tool

    _overdue()
    decision = _stop_run_on_control_flow_tool(None, [_result("create_file", PENDING_REFUSAL)])

    assert decision.is_final_output is True
    assert decision.final_output == ""


async def test_run_keeps_going_without_a_trusted_overdue_body(tools):
    """只认结构化错误码 + 当前仍可信的待补状态：没有待补条目时同样的结果不结束 run。"""
    from agent.openai_agents.runner import _stop_run_on_control_flow_tool

    ToolContext.begin_model_reply()
    assert _stop_run_on_control_flow_tool(None, [_result("create_file", PENDING_REFUSAL)]).is_final_output is False
    other_error = {"status": "error", "error_type": "anchor_not_found", "error": "file_body_pending"}
    _overdue()
    assert _stop_run_on_control_flow_tool(None, [_result("edit_file", other_error)]).is_final_output is False


async def test_breaker_and_budget_stops_keep_priority_over_the_pending_stop(tools):
    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.runner import _stop_run_on_control_flow_tool
    from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker

    _overdue()
    breaker = ToolFailureBreaker(max_failures_per_request=1)
    breaker.observe("create_file", "{}", json.dumps(PENDING_REFUSAL))
    tripped = _stop_run_on_control_flow_tool(None, [_result("create_file", PENDING_REFUSAL)], failure_breaker=breaker)
    assert tripped.is_final_output is True and tripped.final_output == breaker.trip.user_message()

    meter = AgentRunMeter(max_model_calls=1)
    meter.record_model_call()
    budget = _stop_run_on_control_flow_tool(None, [_result("create_file", PENDING_REFUSAL)], run_meter=meter)
    assert budget.is_final_output is True and meter.budget_stopped is True
