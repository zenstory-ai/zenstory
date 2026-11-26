"""Two-reply file protocol: the reply after create_file streams the body before any other tool."""

import json
from types import SimpleNamespace

import pytest
from agents import RunContextWrapper

from agent.openai_agents.tools_adapter import build_agent_function_tools
from agent.tools.tool_context import AgentRequestContext

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
        {
            name: fake(name)
            for name in (
                "create_file",
                "write_chapter",
                "update_project",
                "edit_file",
                "delete_file",
                "rename_file",
                "query_files",
                "load_skill",
                "read_skill_resource",
            )
        },
    )
    req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    try:
        yield {tool.name: tool for tool in build_agent_function_tools()}, executed, req_ctx
    finally:
        req_ctx.clear()


async def _call(tool, req_ctx: AgentRequestContext, args="{}") -> dict:
    tool_ctx = SimpleNamespace(context=req_ctx, tool_call_id="call-test")
    return json.loads(await tool.on_invoke_tool(tool_ctx, args))


async def test_reply_that_skips_the_body_cannot_call_other_tools(tools):
    by_name, executed, req_ctx = tools
    req_ctx.begin_model_reply()
    req_ctx.set_pending_empty_file("f1", "第一章", "v1")  # reply 1 ends with create_file
    req_ctx.begin_model_reply()  # reply 2 calls tools instead of streaming <file>

    refused_rename = await _call(by_name["rename_file"], req_ctx, '{"id": "f1", "new_title": "新标题"}')
    edit = await _call(by_name["edit_file"], req_ctx, '{"id": "f1", "edits": []}')
    await _call(by_name["delete_file"], req_ctx, '{"id": "f1"}')

    assert refused_rename["error_type"] == "file_body_pending" and "第一章" in refused_rename["error"]
    assert edit["error_type"] == "file_body_pending"
    assert executed == ["delete_file"]


async def test_body_then_next_create_file_in_the_same_reply_keeps_the_run_going(tools):
    """Bulk writing chains <file>chapter N</file> + create_file(chapter N+1) in one reply."""
    by_name, executed, req_ctx = tools
    req_ctx.begin_model_reply()
    req_ctx.set_pending_empty_file("f1", "第一章")
    req_ctx.begin_model_reply()
    req_ctx.mark_latest_pending_body_streamed()  # on_llm_end saw the closed <file> block

    result = await _call(by_name["create_file"], req_ctx, '{"title": "第二章"}')

    assert result["status"] == "success"
    assert executed == ["create_file"]


async def test_tools_in_the_reply_that_creates_the_file_still_run(tools):
    by_name, executed, req_ctx = tools
    req_ctx.begin_model_reply()
    req_ctx.set_pending_empty_file("f1", "第一章")

    assert (await _call(by_name["update_project"], req_ctx))["status"] == "success"
    assert executed == ["update_project"]


# ---------------------------------------------------------------------------
# 待补正文期间的删除只放行“取消这份待补文件”（2026-10-25 事故：模型删到了另一份已写稿件）
# ---------------------------------------------------------------------------


def _overdue(
    req_ctx: AgentRequestContext,
    file_id: str = "f1",
    title: str = "第一章",
    version: str | None = "v1",
    *,
    file_type: str | None = None,
) -> None:
    """version：create_file 新建时记下的版本；复用已有剧集时为 None。"""
    req_ctx.begin_model_reply()
    req_ctx.set_pending_empty_file(file_id, title, version, file_type=file_type)  # reply 1 ends with create_file
    req_ctx.begin_model_reply()  # reply 2 calls tools instead of streaming <file>


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
    by_name, executed, req_ctx = tools
    _overdue(req_ctx)

    refused = await _call(by_name["delete_file"], req_ctx, args)

    assert refused["error_type"] == "file_body_pending"
    assert executed == []


@pytest.mark.parametrize("args", ['{"id": "f1"}', '{"id": "f1", "recursive": false}', '{"id": "f1", "recursive": "false"}'])
async def test_overdue_body_still_lets_the_model_cancel_that_exact_file(tools, args):
    by_name, executed, req_ctx = tools
    _overdue(req_ctx)

    assert (await _call(by_name["delete_file"], req_ctx, args))["status"] == "success"
    assert executed == ["delete_file"]


async def test_a_cancel_that_really_deleted_the_file_settles_the_guard_for_the_rest_of_the_run(monkeypatch, tools):
    by_name, executed, req_ctx = tools
    _overdue(req_ctx)

    await _call(by_name["delete_file"], req_ctx, '{"id": "f1"}')
    # 假的 delete_file 没有 mutation_applied：没确认删掉，守卫不动
    assert req_ctx.overdue_file_bodies() != []

    async def really_deletes(_args):
        executed.append("delete_file")
        return {"content": [{"type": "text", "text": json.dumps({"status": "success", "mutation_applied": True})}]}

    from agent.openai_agents import tools_adapter

    monkeypatch.setitem(tools_adapter.TOOL_FUNCTIONS, "delete_file", really_deletes)
    await _call(by_name["delete_file"], req_ctx, '{"id": "f1"}')
    assert req_ctx.overdue_file_bodies() == []
    assert (await _call(by_name["create_file"], req_ctx, '{"title": "第二章"}'))["status"] == "success"
    assert executed == ["delete_file", "delete_file", "create_file"]


async def test_a_reused_existing_episode_cannot_be_deleted_as_a_cancel(tools):
    """复用的已有剧集（没有本请求的建好版本）可能已有正文：精确 id 也不放行删除。"""
    by_name, executed, req_ctx = tools
    _overdue(req_ctx, version=None)

    assert (await _call(by_name["delete_file"], req_ctx, '{"id": "f1"}'))["error_type"] == "file_body_pending"
    assert executed == []


async def test_overdue_body_cancel_matches_any_overdue_file_not_only_the_first(tools):
    by_name, executed, req_ctx = tools
    req_ctx.begin_model_reply()
    req_ctx.set_pending_empty_file("f1", "第一章", "v1")
    req_ctx.set_pending_empty_file("f2", "第二章", "v2")
    req_ctx.begin_model_reply()

    assert (await _call(by_name["delete_file"], req_ctx, '{"id": "f2"}'))["status"] == "success"
    assert executed == ["delete_file"]


async def test_deletes_without_an_overdue_body_are_unchanged(tools):
    by_name, executed, req_ctx = tools
    req_ctx.begin_model_reply()

    assert (await _call(by_name["delete_file"], req_ctx, '{"id": "folder-1", "recursive": true}'))["status"] == "success"
    assert executed == ["delete_file"]


async def test_overdue_refusal_is_counted_once_in_the_run_summary_without_a_tool_start():
    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker

    executed: list[str] = []

    async def fake(_args):
        executed.append("edit_file")
        return {"content": [{"type": "text", "text": json.dumps({"status": "success"})}]}

    meter = AgentRunMeter()
    breaker = ToolFailureBreaker()
    req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with pytest.MonkeyPatch.context() as patcher:
            patcher.setattr("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"edit_file": fake})
            by_name = {
                tool.name: tool
                for tool in build_agent_function_tools(failure_breaker=breaker, run_meter=meter)
            }
            _overdue(req_ctx)
            refused = await _call(by_name["edit_file"], req_ctx, '{"id": "f1", "edits": []}')
    finally:
        req_ctx.clear()

    assert refused["error_type"] == "file_body_pending"
    assert executed == []
    report = meter.timing_report()
    assert report["tool_failures"] == {
        "edit_file": {"count": 1, "rolled_back_batches": 0, "error_types": {"file_body_pending": 1}}
    }
    assert report["first_tool_started_ms"] is None
    # 仍计入请求级累计失败上限
    assert breaker.total_failures == 1 and not breaker.is_open


# ---------------------------------------------------------------------------
# runner：前置拒绝后同步结束当前 SDK run，交给 graph 的待补核验与纠偏
# ---------------------------------------------------------------------------


def _result(name: str, payload: dict) -> object:
    return SimpleNamespace(tool=SimpleNamespace(name=name), output=json.dumps(payload, ensure_ascii=False))


PENDING_REFUSAL = {"status": "error", "error_type": "file_body_pending", "error": "x"}


async def test_run_ends_after_a_structured_pending_refusal_while_the_body_is_still_overdue(tools):
    from agent.openai_agents.runner import _stop_run_after_tools

    _by_name, _executed, req_ctx = tools
    _overdue(req_ctx)
    decision = _stop_run_after_tools(RunContextWrapper(req_ctx), [_result("create_file", PENDING_REFUSAL)])

    assert decision.is_final_output is True
    assert decision.final_output == ""


async def test_run_keeps_going_without_a_trusted_overdue_body(tools):
    """只认结构化错误码 + 当前仍可信的待补状态：没有待补条目时同样的结果不结束 run。"""
    from agent.openai_agents.runner import _stop_run_after_tools

    _by_name, _executed, req_ctx = tools
    req_ctx.begin_model_reply()
    assert _stop_run_after_tools(RunContextWrapper(req_ctx), [_result("create_file", PENDING_REFUSAL)]).is_final_output is False
    other_error = {"status": "error", "error_type": "anchor_not_found", "error": "file_body_pending"}
    _overdue(req_ctx)
    assert _stop_run_after_tools(RunContextWrapper(req_ctx), [_result("edit_file", other_error)]).is_final_output is False


async def test_breaker_and_budget_stops_keep_priority_over_the_pending_stop(tools):
    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.runner import _stop_run_after_tools
    from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker

    _by_name, _executed, req_ctx = tools
    _overdue(req_ctx)
    breaker = ToolFailureBreaker(max_failures_per_request=1)
    breaker.observe("create_file", "{}", json.dumps(PENDING_REFUSAL))
    tripped = _stop_run_after_tools(RunContextWrapper(req_ctx), [_result("create_file", PENDING_REFUSAL)], failure_breaker=breaker)
    assert tripped.is_final_output is True and tripped.final_output == breaker.trip.user_message()

    meter = AgentRunMeter(max_model_calls=1)
    meter.record_model_call()
    budget = _stop_run_after_tools(RunContextWrapper(req_ctx), [_result("create_file", PENDING_REFUSAL)], run_meter=meter)
    assert budget.is_final_output is True and meter.budget_stopped is True


# ---------------------------------------------------------------------------
# 非冲突只读/元数据工具放行（附 pending_file_reminder）+ 同目标重复写吸收
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("query_files", '{"query": "前文"}'),
        ("update_project", '{"current_phase": "正文写作"}'),
        ("load_skill", '{"name": "去AI味"}'),
        ("read_skill_resource", '{"name": "去AI味", "path": "references/表达.md"}'),
        ("create_file", '{"title": "第一卷", "file_type": "folder"}'),
        ("create_file", '{"title": "占位大纲", "file_type": "outline", "empty": true}'),
        ("create_file", '{"title": "设定原稿", "file_type": "lore", "source_text": "作者粘贴的设定原文"}'),
    ],
)
async def test_overdue_allows_non_conflicting_tools_with_reminder_and_no_runner_teardown(monkeypatch, tool_name, args):
    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.runner import _stop_run_after_tools
    from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker

    executed: list[str] = []

    async def fake(_args):
        executed.append(tool_name)
        return {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": {}})}]}

    meter = AgentRunMeter()
    breaker = ToolFailureBreaker()
    req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    try:
        monkeypatch.setattr("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {tool_name: fake})
        by_name = {
            tool.name: tool
            for tool in build_agent_function_tools(failure_breaker=breaker, run_meter=meter)
        }
        _overdue(req_ctx, file_id="f1", title="第1章 启程", version="v1")

        out = await _call(by_name[tool_name], req_ctx, args)
        assert out["status"] == "success"
        assert "error_type" not in out
        assert "第1章 启程" in out["pending_file_reminder"]
        assert "<file>" in out["pending_file_reminder"]
        assert executed == [tool_name]
        assert meter.timing_report().get("tool_failures") in (None, {})
        assert breaker.total_failures == 0

        # runner._stop_run_after_tools must NOT tear down the SDK run.
        decision = _stop_run_after_tools(RunContextWrapper(req_ctx), [_result(tool_name, out)])
        assert decision.is_final_output is False
    finally:
        req_ctx.clear()


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("edit_file", '{"id": "other-id", "edits": []}'),
        ("rename_file", '{"id": "f1", "new_title": "改名"}'),
        ("delete_file", '{"id": "other-id"}'),
        ("create_file", '{"title": "第二章", "file_type": "outline"}'),
        ("write_chapter", '{"chapter": 2, "title": "风暴", "file_type": "draft"}'),
    ],
)
async def test_overdue_keeps_hard_refusal_for_conflicting_mutations(tools, tool_name, args):
    from agent.openai_agents.runner import _stop_run_after_tools

    by_name, executed, req_ctx = tools
    _overdue(req_ctx, file_id="f1", title="第1章 启程", version="v1")

    refused = await _call(by_name[tool_name], req_ctx, args)
    assert refused["status"] == "error"
    assert refused["error_type"] == "file_body_pending"
    assert executed == []

    decision = _stop_run_after_tools(RunContextWrapper(req_ctx), [_result(tool_name, refused)])
    assert decision.is_final_output is True
    assert decision.final_output == ""


@pytest.mark.parametrize(
    ("tool_name", "pending_title", "args"),
    [
        ("create_file", "角色档案", '{"title": "角色档案", "file_type": "character"}'),
        ("write_chapter", "第3章 雨夜", '{"chapter": 3, "title": "雨夜", "file_type": "draft"}'),
        ("write_chapter", "第三章 雨夜", '{"chapter": 3, "title": "第三章 雨夜", "file_type": "draft"}'),
        ("write_chapter", "第2集 交锋", '{"chapter": 2, "title": "交锋", "file_type": "script"}'),
    ],
)
async def test_overdue_passes_same_target_writer_repeat_without_file_body_pending(
    tools, tool_name, pending_title, args
):
    from agent.openai_agents.runner import _stop_run_after_tools

    by_name, executed, req_ctx = tools
    _overdue(req_ctx, file_id="f1", title=pending_title, version="v1")

    out = await _call(by_name[tool_name], req_ctx, args)
    assert out["status"] == "success"
    assert out.get("error_type") != "file_body_pending"
    assert executed == [tool_name]
    assert _stop_run_after_tools(RunContextWrapper(req_ctx), [_result(tool_name, out)]).is_final_output is False


async def test_overdue_passes_armed_chapter_replacement_same_chapter_repeat(tools):
    from agent.openai_agents.runner import _stop_run_after_tools

    by_name, executed, req_ctx = tools
    req_ctx.begin_model_reply()
    req_ctx.try_reserve_pending_empty_file("r1", "楔子")
    req_ctx.bind_pending_empty_file(
        "r1",
        "f-replace",
        "楔子",
        None,
        replacement={"id": "f-replace", "chapter": 5, "file_type": "draft", "previous_word_count": 2000},
    )
    req_ctx.begin_model_reply()

    # Same chapter (5) passes through to mcp_tools; different chapter (6) is hard-refused.
    same = await _call(by_name["write_chapter"], req_ctx, '{"chapter": 5, "title": "楔子", "file_type": "draft"}')
    diff = await _call(by_name["write_chapter"], req_ctx, '{"chapter": 6, "title": "新章", "file_type": "draft"}')

    assert same["status"] == "success"
    assert executed == ["write_chapter"]
    assert _stop_run_after_tools(RunContextWrapper(req_ctx), [_result("write_chapter", same)]).is_final_output is False
    assert diff["error_type"] == "file_body_pending"


async def test_wall_clock_wrap_up_still_blocks_non_conflicting_tools_when_overdue(monkeypatch):
    from agent.core.run_meter import AgentRunMeter

    executed: list[str] = []

    async def fake(_args):
        executed.append("query_files")
        return {"content": [{"type": "text", "text": json.dumps({"status": "success"})}]}

    meter = AgentRunMeter()
    meter.record_wall_clock_landing_call()
    req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    try:
        monkeypatch.setattr("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"query_files": fake})
        by_name = {tool.name: tool for tool in build_agent_function_tools(run_meter=meter)}
        _overdue(req_ctx)

        out = await _call(by_name["query_files"], req_ctx, '{"query": "大纲"}')
        assert out["status"] == "error"
        assert out["error_type"] == "wall_clock_wrap_up"
        assert "pending_file_reminder" not in out
        assert executed == []
    finally:
        req_ctx.clear()


@pytest.mark.parametrize(
    ("tool_name", "pending_title", "pending_type", "args"),
    [
        ("write_chapter", "第1章 细纲", "outline", '{"chapter": 1, "title": "起势", "file_type": "draft"}'),
        ("write_chapter", "第1章 细纲", "outline", '{"chapter": 1, "title": "起势", "file_type": "draft", "empty": true}'),
        ("write_chapter", "第1章 启程", "draft", '{"chapter": 1, "title": "启程", "file_type": "draft", "empty": true}'),
        ("write_chapter", "第2集 交锋", "script", '{"chapter": 2, "title": "交锋", "file_type": "draft"}'),
        ("create_file", "第1章 细纲", "outline", '{"title": "第1章 细纲", "file_type": "character"}'),
    ],
)
async def test_overdue_blocks_cross_type_or_empty_writer_repeat_as_file_body_pending(
    tools, tool_name, pending_title, pending_type, args
):
    """Overdue file_type must match: e.g. an overdue outline '第1章 细纲' must NOT match write_chapter(draft)."""
    from agent.openai_agents.runner import _stop_run_after_tools

    by_name, executed, req_ctx = tools
    _overdue(req_ctx, file_id="f1", title=pending_title, version="v1", file_type=pending_type)

    refused = await _call(by_name[tool_name], req_ctx, args)
    assert refused["status"] == "error"
    assert refused["error_type"] == "file_body_pending"
    assert executed == []
    assert _stop_run_after_tools(RunContextWrapper(req_ctx), [_result(tool_name, refused)]).is_final_output is True


@pytest.mark.parametrize(
    ("tool_name", "pending_title", "pending_type", "args"),
    [
        ("create_file", "角色档案", "character", '{"title": "角色档案", "file_type": "character"}'),
        ("write_chapter", "第3章 雨夜", "draft", '{"chapter": 3, "title": "雨夜", "file_type": "draft"}'),
    ],
)
async def test_overdue_caps_same_target_writer_repeat_at_one_before_file_body_pending(
    tools, tool_name, pending_title, pending_type, args
):
    """At most 1 same-target repeat is absorbed per pending file_id; the 2nd repeat falls back to file_body_pending."""
    from agent.openai_agents.runner import _stop_run_after_tools

    by_name, executed, req_ctx = tools
    _overdue(req_ctx, file_id="f1", title=pending_title, version="v1", file_type=pending_type)

    first = await _call(by_name[tool_name], req_ctx, args)
    assert first["status"] == "success"
    assert executed == [tool_name]
    assert _stop_run_after_tools(RunContextWrapper(req_ctx), [_result(tool_name, first)]).is_final_output is False

    second = await _call(by_name[tool_name], req_ctx, args)
    assert second["status"] == "error"
    assert second["error_type"] == "file_body_pending"
    assert executed == [tool_name]
    assert _stop_run_after_tools(RunContextWrapper(req_ctx), [_result(tool_name, second)]).is_final_output is True


async def test_invoke_project_tool_records_pending_file_type_from_create_file_output(monkeypatch):
    """When create_file binds a pending entry without passing file_type, invoke_project_tool stamps it from output."""
    from agent.openai_agents.tools_adapter import invoke_project_tool

    req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")

    async def fake_create(_args, *, request_context=None):
        assert request_context is req_ctx
        request_context.try_reserve_pending_empty_file("r1", "第1章 细纲")
        request_context.bind_pending_empty_file("r1", "outline-1", "第1章 细纲", "v1")
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {
                            "status": "success",
                            "mutation_applied": True,
                            "data": {
                                "id": "outline-1",
                                "title": "第1章 细纲",
                                "file_type": "outline",
                                "awaiting_body": True,
                            },
                        },
                        ensure_ascii=False,
                    ),
                }
            ]
        }

    try:
        monkeypatch.setattr("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"create_file": fake_create})
        req_ctx.begin_model_reply()
        await invoke_project_tool(
            "create_file",
            '{"title": "第1章 细纲", "file_type": "outline"}',
            request_context=req_ctx,
        )
        req_ctx.begin_model_reply()
        overdue = req_ctx.overdue_file_bodies()
        assert overdue == [
            {"id": "outline-1", "title": "第1章 细纲", "created_here": True, "file_type": "outline"}
        ]
    finally:
        req_ctx.clear()


async def test_rolled_back_batch_metric_aligns_with_effective_edit_file_continue_on_error(monkeypatch):
    """Multi-edit calls default continue_on_error=True in EditFileInput; only explicit False counts as rolled back."""
    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.tools_adapter import invoke_project_tool

    meter = AgentRunMeter()

    async def fake_edit(_args):
        return {"status": "error", "error_type": "old_text_not_found", "error": "not found"}

    monkeypatch.setattr("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"edit_file": fake_edit})
    multi_default = json.dumps(
        {
            "id": "f1",
            "edits": [
                {"op": "replace", "old": "a", "new": "b"},
                {"op": "replace", "old": "c", "new": "d"},
            ],
        }
    )
    multi_atomic = json.dumps(
        {
            "id": "f1",
            "continue_on_error": False,
            "edits": [
                {"op": "replace", "old": "a", "new": "b"},
                {"op": "replace", "old": "c", "new": "d"},
            ],
        }
    )

    await invoke_project_tool("edit_file", multi_default, run_meter=meter)
    assert meter.timing_report()["tool_failures"]["edit_file"]["rolled_back_batches"] == 0

    await invoke_project_tool("edit_file", multi_atomic, run_meter=meter)
    assert meter.timing_report()["tool_failures"]["edit_file"]["rolled_back_batches"] == 1
