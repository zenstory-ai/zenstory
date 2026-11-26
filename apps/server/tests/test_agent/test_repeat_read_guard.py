"""重复读取守卫、上限软着陆、失控停止退款与结构化续接的回归测试。

线上事故：付费用户的 agent 反复 query_files 同一批文件直到单 agent 100 轮上限，
连续两次；「继续」又从头规划、再读一遍。熔断器只数失败，成功的重复读取看起来像进展。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.openai_agents.repeat_read_guard import (
    MAX_BLOCKED_READS_PER_REQUEST,
    NO_PROGRESS_STOP_REASON,
    NO_PROGRESS_USER_MESSAGE,
    READ_BLOCK_AT,
    REPEATED_READ_ERROR_TYPE,
    RepeatReadGuard,
)
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker
from tests.test_agent.test_tool_failure_breaker import (
    _run_against_local_model,
    _text_chunks,
    _tool_call_chunks,
    _tool_messages,
)

FULL_READ = json.dumps({"id": "f1", "response_mode": "full"})


def _read_ok(file_id: str = "f1", title: str = "第一章") -> str:
    return json.dumps(
        {"status": "success", "data": [{"id": file_id, "title": title, "content": "正文……"}]},
        ensure_ascii=False,
    )


def _guarded_read(guard: RepeatReadGuard, raw_args: str = FULL_READ, output: str | None = None) -> str:
    """模拟 tools_adapter：plan → （未拦下则执行）→ observe。"""
    plan = guard.plan("query_files", raw_args)
    if plan.blocked_output is not None:
        return plan.blocked_output
    return guard.observe("query_files", plan, output or _read_ok())


# ---------------------------------------------------------------- 守卫本身


@pytest.mark.unit
def test_identical_reads_hint_then_warn_then_block_then_stop():
    guard = RepeatReadGuard()

    first = json.loads(_guarded_read(guard))
    assert "read_hint" not in first

    second = json.loads(_guarded_read(guard))
    assert second["read_hint"].startswith("该文件全文本次运行中已读取过")
    assert second["data"][0]["title"] == "第一章", "第 2 次照常返回内容"

    third = json.loads(_guarded_read(guard))
    assert third["read_hint"].startswith("警告")
    assert third["repeated_reads"] == 3

    for blocked_index in range(MAX_BLOCKED_READS_PER_REQUEST):
        blocked = json.loads(_guarded_read(guard))
        assert blocked["status"] == "error"
        assert blocked["error_type"] == REPEATED_READ_ERROR_TYPE
        assert "《第一章》(id=f1)" in blocked["error"]
        # 给作者看的说明：不带 id，也不带写给模型的指令
        assert blocked["user_message"] == "《第一章》刚才已经读过，AI 直接用已读内容继续。"
        if blocked_index < MAX_BLOCKED_READS_PER_REQUEST - 1:
            assert not guard.is_open

    assert guard.is_open
    assert guard.trip is not None and guard.trip.blocked_reads == MAX_BLOCKED_READS_PER_REQUEST
    # 第 2、3 次成功读取 + 3 次被拦下
    assert guard.duplicate_reads == 2 + MAX_BLOCKED_READS_PER_REQUEST
    assert json.loads(guard.short_circuit_text("edit_file"))["error_type"] == "no_progress_stop"


@pytest.mark.unit
def test_read_mode_is_part_of_the_key_and_failed_reads_do_not_count():
    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(guard)
    # 摘要读取与全文读取是不同的键；include_content=true 等同全文
    summary = json.loads(_guarded_read(guard, json.dumps({"id": "f1", "response_mode": "summary"})))
    assert summary["status"] == "success" and "read_hint" not in summary
    blocked = json.loads(_guarded_read(guard, json.dumps({"id": "f1", "include_content": "true"})))
    assert blocked["error_type"] == REPEATED_READ_ERROR_TYPE


@pytest.mark.unit
def test_id_read_without_response_mode_counts_as_full_read():
    """query_files 按 id 读取默认返回全文；守卫必须把它和 response_mode=full 算成同一个键。"""
    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(guard, json.dumps({"id": "f1"}))
    blocked = json.loads(_guarded_read(guard, FULL_READ))
    assert blocked["error_type"] == REPEATED_READ_ERROR_TYPE

    other = RepeatReadGuard()
    # 文件不存在（空列表）/ 报错都不算读过
    for output in ('{"status": "success", "data": []}', '{"status": "error", "error": "x"}'):
        for _ in range(READ_BLOCK_AT):
            assert "repeated_read" not in _guarded_read(other, output=output)
    assert other.duplicate_reads == 0


@pytest.mark.unit
def test_successful_write_resets_that_file_only():
    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(guard)
        _guarded_read(guard, json.dumps({"id": "f2", "response_mode": "full"}), _read_ok("f2", "第二章"))

    edit_args = json.dumps({"id": "f1", "edits": []})
    plan = guard.plan("edit_file", edit_args)
    guard.observe(
        "edit_file",
        plan,
        json.dumps({"status": "success", "data": {"id": "f1", "title": "第一章"}}, ensure_ascii=False),
    )

    assert json.loads(_guarded_read(guard))["status"] == "success", "改过的文件可以重新读"
    blocked = json.loads(
        _guarded_read(guard, json.dumps({"id": "f2", "response_mode": "full"}), _read_ok("f2", "第二章"))
    )
    assert blocked["error_type"] == REPEATED_READ_ERROR_TYPE, "没改过的文件仍按重复读取拦下"
    assert guard.write_succeeded is True
    assert guard.files_written == {"f1": "第一章"}

    # 失败的写入（或 edit_file 全部未生效）不清零
    failed = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(failed)
    for output in (
        '{"status": "error", "error": "locked"}',
        json.dumps({"status": "success", "data": {"id": "f1", "all_failed": True}}),
    ):
        failed.observe("edit_file", failed.plan("edit_file", edit_args), output)
    assert json.loads(_guarded_read(failed))["error_type"] == REPEATED_READ_ERROR_TYPE
    assert failed.write_succeeded is False


@pytest.mark.unit
def test_breaker_does_not_count_guard_blocks_as_failures():
    """拦下的重复读取不能让熔断器以「相同参数连续失败」抢先熔断。"""
    guard = RepeatReadGuard()
    breaker = ToolFailureBreaker()
    for _ in range(READ_BLOCK_AT - 1 + MAX_BLOCKED_READS_PER_REQUEST):
        breaker.observe("query_files", FULL_READ, _guarded_read(guard))
    assert breaker.trip is None
    assert breaker.total_failures == 0
    assert guard.is_open


# ------------------------------------------------ 读取计数按 agent run 隔离（F1）

_THREE_FILES = (("f1", "卷纲"), ("f2", "第四章"), ("f3", "主角卡"))


def _read_file(guard: RepeatReadGuard, file_id: str, title: str) -> dict:
    return json.loads(
        _guarded_read(guard, json.dumps({"id": file_id, "response_mode": "full"}), _read_ok(file_id, title))
    )


@pytest.mark.unit
def test_each_agent_run_can_read_reference_files_once_without_hints_or_blocks():
    """full 流程 planner → hook_designer → writer → 审稿人：工具结果不跨 agent 回放，

    每个 agent 都得自己读一次卷纲、上一章、主角卡。旧实现按请求计数，第 4 个 agent
    的读取被拦下，拦满 3 次就整轮判为无进展。
    """
    guard = RepeatReadGuard()
    for _agent_run in range(4):
        guard.begin_agent_run()
        for file_id, title in _THREE_FILES:
            result = _read_file(guard, file_id, title)
            assert result["status"] == "success"
            assert "read_hint" not in result, "本次运行第一次读，不能说「已读过」"

    assert guard.blocked_reads == 0
    assert guard.duplicate_reads == 0
    assert not guard.is_open
    # 读写台账仍按整个请求累计，交接摘要照常列出
    assert list(guard.files_read) == ["f1", "f2", "f3"]


@pytest.mark.unit
def test_repeats_inside_one_run_are_still_blocked_and_blocks_add_up_across_runs():
    guard = RepeatReadGuard()
    guard.begin_agent_run()
    for _ in range(READ_BLOCK_AT - 1):
        _read_file(guard, "f1", "卷纲")
    blocked = _read_file(guard, "f1", "卷纲")
    assert blocked["error_type"] == REPEATED_READ_ERROR_TYPE, "同一 run 内第 4 次仍拦下"
    assert "本次运行中已读取" in blocked["error"]
    assert guard.blocked_reads == 1

    # 下一个 agent run：读取计数清零，但被拦下的次数按请求累计，无进展判定照常生效
    guard.begin_agent_run()
    assert _read_file(guard, "f1", "卷纲")["status"] == "success"
    for _ in range(READ_BLOCK_AT - 2):
        _read_file(guard, "f1", "卷纲")
    for _ in range(MAX_BLOCKED_READS_PER_REQUEST - 1):
        assert _read_file(guard, "f1", "卷纲")["error_type"] == REPEATED_READ_ERROR_TYPE
    assert guard.is_open
    assert guard.trip is not None and guard.trip.blocked_reads == MAX_BLOCKED_READS_PER_REQUEST


@pytest.mark.unit
def test_partial_edit_that_changed_the_file_counts_as_a_write():
    """edit_file 部分成功（status=partial、mutation_applied=true）也改了文件：

    读取计数要清零（否则「之后未被修改」是假话），也要记为写入（否则无进展停止时
    说「不扣费」，计费却按写入扣了）。
    """
    edit_args = json.dumps({"id": "f1", "edits": []})
    partial = {
        "status": "partial",
        "mutation_applied": True,
        "data": {"id": "f1", "title": "第一章", "partial_success": True, "failed_edits": [{"index": 1}]},
    }

    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(guard)
    guard.observe("edit_file", guard.plan("edit_file", edit_args), json.dumps(partial, ensure_ascii=False))

    assert json.loads(_guarded_read(guard))["status"] == "success", "改过的文件可以重新读"
    assert guard.files_written == {"f1": "第一章"}
    assert guard.write_succeeded is True

    # partial 但实际没有改动（mutation_applied=false）：不算写入
    untouched = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(untouched)
    untouched.observe(
        "edit_file",
        untouched.plan("edit_file", edit_args),
        json.dumps({**partial, "mutation_applied": False}, ensure_ascii=False),
    )
    assert json.loads(_guarded_read(untouched))["error_type"] == REPEATED_READ_ERROR_TYPE
    assert untouched.write_succeeded is False


# ------------------------------------------------------- tools_adapter 接线


@pytest.mark.asyncio
@pytest.mark.unit
async def test_function_tool_does_not_execute_blocked_reads():
    from agent.openai_agents.tools_adapter import build_agent_function_tools

    executed: list[dict] = []

    async def fake_query(args):
        executed.append(args)
        return {"content": [{"type": "text", "text": _read_ok()}]}

    guard = RepeatReadGuard()
    with patch.dict("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"query_files": fake_query}):
        tools = {tool.name: tool for tool in build_agent_function_tools(read_guard=guard)}
        outputs = [
            await tools["query_files"].on_invoke_tool(None, FULL_READ)
            for _ in range(READ_BLOCK_AT - 1 + MAX_BLOCKED_READS_PER_REQUEST)
        ]
        # 判定无进展后，同一轮里剩下的调用（包括写工具）都不执行
        after_trip = await tools["edit_file"].on_invoke_tool(None, json.dumps({"id": "f1"}))

    assert len(executed) == READ_BLOCK_AT - 1
    assert json.loads(outputs[-1])["error_type"] == REPEATED_READ_ERROR_TYPE
    assert json.loads(after_trip)["error_type"] == "no_progress_stop"


@pytest.mark.unit
def test_tool_use_behavior_stops_run_when_guard_trips():
    from agent.openai_agents.runner import _stop_run_after_tools

    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1 + MAX_BLOCKED_READS_PER_REQUEST):
        _guarded_read(guard)
    result = _stop_run_after_tools(None, [], read_guard=guard)
    assert result.is_final_output is True
    assert result.final_output == NO_PROGRESS_USER_MESSAGE


# ------------------------------------------------------------ 软着陆过滤器


@pytest.mark.unit
def test_counting_filter_appends_landing_reminder_for_last_turns():
    from agents.run_config import ModelInputData

    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.runner import SOFT_LANDING_TURNS, _ModelCallCountingFilter

    meter = AgentRunMeter()
    max_turns = 6
    turn_filter = _ModelCallCountingFilter(lambda data: data.model_data, meter, max_turns=max_turns)
    original = [{"role": "user", "content": "写第一章"}]

    sent = [
        turn_filter(SimpleNamespace(model_data=ModelInputData(input=list(original), instructions="sys")))
        for _ in range(max_turns)
    ]

    plain_turns = max_turns - SOFT_LANDING_TURNS
    for model_data in sent[:plain_turns]:
        assert model_data.input == original
    for model_data in sent[plain_turns:]:
        assert model_data.input[:-1] == original
        assert "停止调用任何工具" in model_data.input[-1]["content"]
        assert model_data.instructions == "sys", "提醒不改系统提示（保住前缀缓存）"
    assert turn_filter.landing_injected is True
    assert turn_filter.calls == max_turns
    assert meter.model_calls == max_turns


@pytest.mark.unit
def test_landing_reminder_fires_when_request_budget_is_nearly_spent():
    """前面的 agent 已用掉大半请求级预算：本次 run 才第 1 次调用，也要开始收尾。"""
    from agents.run_config import ModelInputData

    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.runner import LANDING_LIMIT_MODEL_CALL_BUDGET, _ModelCallCountingFilter

    original = [{"role": "user", "content": "写第五章"}]

    def call(turn_filter):
        return turn_filter(SimpleNamespace(model_data=ModelInputData(input=list(original), instructions="sys")))

    roomy = _ModelCallCountingFilter(lambda data: data.model_data, AgentRunMeter(max_model_calls=10), max_turns=60)
    assert call(roomy).input == original
    assert roomy.landing_limit is None

    meter = AgentRunMeter(max_model_calls=10, model_calls=7)  # 还剩 3 次
    turn_filter = _ModelCallCountingFilter(lambda data: data.model_data, meter, max_turns=60)
    sent = call(turn_filter)
    assert turn_filter.calls == 1
    assert "停止调用任何工具" in sent.input[-1]["content"]
    assert "最多还能调用模型 2 次" in sent.input[-1]["content"]
    assert turn_filter.landing_limit == LANDING_LIMIT_MODEL_CALL_BUDGET


# ------------------------------------------------- 用真实 SDK run-loop 端到端


@pytest.fixture
def _reset_model_cache():
    from agent.openai_agents.model import reset_sdk_model_cache

    reset_sdk_model_cache()
    yield
    reset_sdk_model_cache()


async def _unused_edit(_args):  # pragma: no cover - 脚本里不调用 edit_file
    raise AssertionError("edit_file should not be called")


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_model_cache")
async def test_sdk_run_stops_as_no_progress_when_model_keeps_rereading(monkeypatch):
    executed: list[dict] = []

    async def fake_query(args):
        executed.append(args)
        return {"content": [{"type": "text", "text": _read_ok()}]}

    def script(index: int) -> list[dict]:
        return _tool_call_chunks(f"call-{index}", "query_files", FULL_READ)

    events, requests, _state = await _run_against_local_model(
        monkeypatch, script, _unused_edit, tool_impls={"query_files": fake_query}
    )

    total_calls = READ_BLOCK_AT - 1 + MAX_BLOCKED_READS_PER_REQUEST
    assert len(executed) == READ_BLOCK_AT - 1, "第 4 次起不再真正执行读取"
    assert len(requests) == total_calls, "第 3 次被拦下后立即结束，不再发起模型调用"
    assert "read_hint" in _tool_messages(requests[2])[-1]
    assert REPEATED_READ_ERROR_TYPE in _tool_messages(requests[READ_BLOCK_AT])[-1]

    types = [event.type for event in events]
    assert types[-2:] == [StreamEventType.MESSAGE_END, StreamEventType.ERROR]
    assert events[-2].data["stop_reason"] == NO_PROGRESS_STOP_REASON
    error = events[-1].data
    assert error["reason"] == NO_PROGRESS_STOP_REASON
    assert error["code"] == "ERR_AGENT_NO_PROGRESS"
    assert error["error"] == NO_PROGRESS_USER_MESSAGE
    assert error["refundable"] is False, "已进入模型执行的无进展护栏停止照常计费"
    assert error["blocked_reads"] == MAX_BLOCKED_READS_PER_REQUEST


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_model_cache")
async def test_sdk_run_hard_cap_reports_actual_turns_and_stop_reason(monkeypatch):
    monkeypatch.setattr("agent.openai_agents.runner.AGENT_TOOL_CALL_MAX_ITERATIONS", 5)

    async def fake_query(args):
        return {"content": [{"type": "text", "text": _read_ok(args.get("id"), f"文件{args.get('id')}")}]}

    def script(index: int) -> list[dict]:
        # 每次读不同的文件：守卫不拦，模型无视收尾提醒一直调用工具
        return _tool_call_chunks(f"call-{index}", "query_files", json.dumps({"id": f"f{index}"}))

    events, requests, _state = await _run_against_local_model(
        monkeypatch, script, _unused_edit, tool_impls={"query_files": fake_query}
    )

    assert len(requests) == 5
    last_user = [m for m in requests[-1]["messages"] if m.get("role") == "user"][-1]
    assert "停止调用任何工具" in str(last_user.get("content"))
    first_users = [m for m in requests[0]["messages"] if m.get("role") == "user"]
    assert all("停止调用任何工具" not in str(m.get("content")) for m in first_users)

    exhausted = [e for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert len(exhausted) == 1
    assert exhausted[0].data["layer"] == "tool_call"
    assert exhausted[0].data["iterations_used"] == 5
    assert exhausted[0].data["max_iterations"] == 5
    assert events[-1].type == StreamEventType.MESSAGE_END
    assert events[-1].data["stop_reason"] == "max_turns_exceeded"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_model_cache")
async def test_sdk_run_soft_landing_summary_still_ends_as_exhausted(monkeypatch):
    monkeypatch.setattr("agent.openai_agents.runner.AGENT_TOOL_CALL_MAX_ITERATIONS", 5)

    async def fake_query(args):
        return {"content": [{"type": "text", "text": _read_ok(args.get("id"), "某文件")}]}

    def script(index: int) -> list[dict]:
        if index < 2:
            return _tool_call_chunks(f"call-{index}", "query_files", json.dumps({"id": f"f{index}"}))
        # 第 3 次调用收到收尾提醒，照做：写阶段总结
        return _text_chunks("已完成：读了两个文件。剩余：写正文。文件：f0、f1。")

    events, requests, _state = await _run_against_local_model(
        monkeypatch, script, _unused_edit, tool_impls={"query_files": fake_query}
    )

    assert len(requests) == 3
    exhausted = [e for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert len(exhausted) == 1 and exhausted[0].data["iterations_used"] == 3
    assert events[-1].data["stop_reason"] == "max_turns_exceeded"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_model_cache")
async def test_sdk_run_soft_lands_on_request_budget_instead_of_hard_error(monkeypatch):
    from agent.core.run_meter import AgentRunMeter

    meter = AgentRunMeter(max_model_calls=10, model_calls=7)

    def script(index: int) -> list[dict]:
        # 第 1 次调用就收到收尾提醒，照做：写阶段总结
        return _text_chunks("已完成：第五章前半。剩余：后半章。文件：第五章(id=f5)。")

    events, requests, _state = await _run_against_local_model(
        monkeypatch, script, _unused_edit, state_extra={"run_meter": meter}
    )

    assert len(requests) == 1
    reminder = [m for m in requests[0]["messages"] if m.get("role") == "user"][-1]
    assert "停止调用任何工具" in str(reminder.get("content"))
    assert "<file>" in str(reminder.get("content")), "提醒先写完刚建文件的正文"
    assert not any(e.type == StreamEventType.ERROR for e in events), "不再硬停成 ERROR"
    exhausted = [e for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert len(exhausted) == 1
    assert exhausted[0].data["layer"] == "tool_call"
    assert exhausted[0].data["iterations_used"] == 8
    assert exhausted[0].data["max_iterations"] == 10
    assert events[-1].type == StreamEventType.MESSAGE_END
    assert events[-1].data["stop_reason"] == "model_call_budget_exhausted"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_model_cache")
async def test_sdk_run_soft_lands_before_the_wall_clock_limit(monkeypatch):
    """Two production runs (50+ model calls) were cut at 780s mid-work: near the limit the
    agent is told to finish the open file and summarize, and the turn ends resumable."""
    import time

    from agent.core.run_meter import AgentRunMeter

    meter = AgentRunMeter(max_model_calls=120, started_at=time.monotonic() - 700, wrap_up_after_s=600)

    def script(index: int) -> list[dict]:
        return _text_chunks("已完成：第五章前半。剩余：后半章。")

    from agent.openai_agents import runner as runner_module

    logged = MagicMock(wraps=runner_module.log_with_context)
    monkeypatch.setattr(runner_module, "log_with_context", logged)
    events, requests, _state = await _run_against_local_model(
        monkeypatch, script, _unused_edit, state_extra={"run_meter": meter}
    )

    assert len(requests) == 1
    # 时限收尾单独一条告警（带已用时长），不和工具调用轮数上限混在一起统计。
    messages = [c.args[2] for c in logged.call_args_list if len(c.args) > 2]
    assert "Agent run soft-landed near the wall-clock limit" in messages
    assert "Agent run reached the tool-call turn cap" not in messages
    landing_log = next(
        c for c in logged.call_args_list
        if len(c.args) > 2 and c.args[2] == "Agent run soft-landed near the wall-clock limit"
    )
    assert landing_log.kwargs["elapsed_s"] >= 700
    reminder = [m for m in requests[0]["messages"] if m.get("role") == "user"][-1]
    assert "时间快用完了" in str(reminder.get("content"))
    assert "</file>" in str(reminder.get("content")), "an open <file> body is finished first"
    assert not any(e.type == StreamEventType.ERROR for e in events)
    exhausted = [e for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert len(exhausted) == 1 and "时间快用完了" in exhausted[0].data["reason"]
    # The card tells the author it stopped near the time limit (the work may already be done).
    assert exhausted[0].data["limit"] == "wall_clock"
    # The router already treats this stop reason as "not finished": 「继续」 resumes it.
    assert events[-1].data["stop_reason"] == "run_deadline_exceeded"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_model_cache")
async def test_sdk_run_resets_read_counts_left_by_the_previous_agent(monkeypatch):
    """上一个 agent 已把 f1 读了 3 次：新 run 第一次读 f1 要真正执行、不带「已读过」提示。"""
    executed: list[dict] = []

    async def fake_query(args):
        executed.append(args)
        return {"content": [{"type": "text", "text": _read_ok()}]}

    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(guard)

    def script(index: int) -> list[dict]:
        if index == 0:
            return _tool_call_chunks("call-0", "query_files", FULL_READ)
        return _text_chunks("审查完成。")

    events, requests, _state = await _run_against_local_model(
        monkeypatch,
        script,
        _unused_edit,
        state_extra={"repeat_read_guard": guard},
        tool_impls={"query_files": fake_query},
    )

    assert len(executed) == 1, "新 run 的第一次读取照常执行"
    tool_output = _tool_messages(requests[1])[-1]
    assert REPEATED_READ_ERROR_TYPE not in tool_output
    assert "read_hint" not in tool_output
    assert guard.blocked_reads == 0
    assert events[-1].data["stop_reason"] == "end_turn"


# ------------------------------------------------------------------ 计费


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _decide(
    frames: list[str], *, provider_attempted: bool = True, terminal_model_failure: bool = False
) -> tuple[str, bool]:
    from agent.core.stream_billing import StreamBillingTracker

    tracker = StreamBillingTracker()
    for frame in frames:
        tracker.observe(frame)
    return tracker.decide(
        client_disconnected=False,
        unexpected_exception=False,
        provider_attempted=provider_attempted,
        terminal_model_failure=terminal_model_failure,
    )


@pytest.mark.unit
def test_tool_call_cap_without_writes_is_charged_after_model_start():
    frames = [
        _sse("content", {"text": "让我再读一下第一章……"}),
        _sse("tool_result", {"tool_name": "query_files", "status": "success"}),
        _sse("iteration_exhausted", {"layer": "tool_call", "iterations_used": 60, "max_iterations": 60}),
        _sse("done", {}),
    ]
    assert _decide(frames) == ("completed", False)


@pytest.mark.unit
def test_tool_call_cap_after_a_write_is_charged():
    frames = [
        _sse("tool_result", {"tool_name": "edit_file", "status": "success"}),
        _sse("iteration_exhausted", {"layer": "tool_call", "iterations_used": 60, "max_iterations": 60}),
        _sse("done", {}),
    ]
    assert _decide(frames) == ("completed", False)


@pytest.mark.unit
def test_no_progress_and_model_call_limit_errors_are_charged_after_model_start():
    no_progress = [
        _sse("content", {"text": "我再看看"}),
        _sse(
            "error",
            {"message": "x", "code": "ERR_AGENT_NO_PROGRESS", "refundable": False, "reason": "no_progress"},
        ),
    ]
    assert _decide(no_progress) == ("non_model_error", False)

    model_limit = [
        _sse("content", {"text": "继续分析"}),
        _sse("error", {"message": "x", "code": "ERR_AGENT_MODEL_CALL_LIMIT", "refundable": False}),
    ]
    assert _decide(model_limit) == ("non_model_error", False)

    # 熔断同样不退款；协作轮数耗尽以 done 结束照常计费
    breaker = [_sse("error", {"message": "x", "code": "ERR_AGENT_TOOL_FAILURE_LIMIT", "refundable": False})]
    assert _decide(breaker) == ("non_model_error", False)
    collaboration = [_sse("iteration_exhausted", {"layer": "collaboration"}), _sse("done", {})]
    assert _decide(collaboration) == ("completed", False)


@pytest.mark.unit
def test_stream_adapter_passes_error_reason_to_sse_frame():
    import asyncio

    from agent.stream_adapter import StreamAdapter

    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1 + MAX_BLOCKED_READS_PER_REQUEST):
        _guarded_read(guard)
    assert guard.trip is not None

    async def collect():
        adapter = StreamAdapter()
        return [
            event
            async for event in adapter._process_workflow_event(
                StreamEvent(
                    type=StreamEventType.ERROR,
                    data=guard.trip.as_event_data("writer"),
                )
            )
        ]

    frames = asyncio.run(collect())
    error_frames = [frame for frame in frames if frame.type == "error"]
    frame = error_frames[-1].data
    assert frame["code"] == "ERR_AGENT_NO_PROGRESS", "不是「工具失败」，用专用错误码"
    assert frame["message"] == NO_PROGRESS_USER_MESSAGE
    assert frame["reason"] == NO_PROGRESS_STOP_REASON
    assert frame["refundable"] is False


# --------------------------------------------------------- 陈旧内容与按范围读取


def test_stale_content_lets_the_file_be_read_again_without_the_unchanged_hint():
    """edit_file 说文件在读过之后被作者改了：再读是在看新内容，不能提示「之后未被修改」，也不能拦下。"""
    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(guard)
    edit_args = json.dumps({"id": "f1", "edits": [{"op": "replace", "old": "a", "new": "b"}]})
    guard.observe(
        "edit_file",
        guard.plan("edit_file", edit_args),
        json.dumps({"status": "error", "error_type": "stale_content", "error": "…"}, ensure_ascii=False),
    )
    reread = json.loads(_guarded_read(guard))
    assert reread["status"] == "success" and "read_hint" not in reread
    assert guard.write_succeeded is False


# ------------------------------------------------------- 范围读取（F-11）


def _range_read_ok(args: dict, body: str = "甲" * 1000, file_id: str = "f1", title: str = "长篇合集") -> str:
    from agent.tools.file_ops.content_range import apply_content_range

    row = apply_content_range({"id": file_id, "title": title, "content": body}, args)
    return json.dumps({"status": "success", "data": [row]}, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_distinct_range_windows_all_execute_and_are_not_recorded_as_full_text():
    """不同字符窗口是不同的读取：第 4 个新窗口照常执行，第 2、3 个不收到重复提示，也不记成读过全文。"""
    from agent.openai_agents.tools_adapter import build_agent_function_tools

    executed: list[dict] = []
    long_body = "甲" * 15000

    async def fake_query(args):
        executed.append(dict(args))
        return {"content": [{"type": "text", "text": _range_read_ok(args, body=long_body)}]}

    windows = [{"id": "f1", "start_char": start, "max_chars": 100} for start in (0, 100, 200, 300)]
    guard = RepeatReadGuard()
    with patch.dict("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"query_files": fake_query}):
        tools = {tool.name: tool for tool in build_agent_function_tools(read_guard=guard)}
        outputs = [
            json.loads(await tools["query_files"].on_invoke_tool(None, json.dumps(args))) for args in windows
        ]

    assert executed == windows
    assert [item["status"] for item in outputs] == ["success"] * 4
    assert all("read_hint" not in item for item in outputs)
    assert guard.duplicate_reads == 0 and guard.blocked_reads == 0
    assert guard.files_read == {}


@pytest.mark.unit
def test_same_range_window_repeats_are_hinted_then_blocked():
    guard = RepeatReadGuard()
    window = {"id": "f1", "start_char": 100, "max_chars": 100}
    # 同一窗口的等价写法（字符串数字）是同一次读取
    long_body = "甲" * 15000
    spellings = [window, {"id": "f1", "start_char": "100", "max_chars": "100"}, window]
    outputs = [
        json.loads(_guarded_read(guard, json.dumps(args), _range_read_ok(window, body=long_body)))
        for args in spellings
    ]
    assert "read_hint" not in outputs[0]
    assert outputs[1]["repeated_reads"] == 2 and "这一段内容" in outputs[1]["read_hint"]
    assert "全文" not in outputs[1]["read_hint"]
    assert outputs[2]["repeated_reads"] == 3

    blocked = json.loads(_guarded_read(guard, json.dumps(window), _range_read_ok(window, body=long_body)))
    assert blocked["error_type"] == REPEATED_READ_ERROR_TYPE
    assert guard.blocked_reads == 1


@pytest.mark.unit
def test_full_and_tail_reads_do_not_count_against_each_other():
    guard = RepeatReadGuard()
    tail = {"id": "f1", "tail_chars": 200}
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(guard)  # 全文读三次
    tail_out = json.loads(_guarded_read(guard, json.dumps(tail), _range_read_ok(tail)))
    assert tail_out["status"] == "success" and "read_hint" not in tail_out

    other = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(other, json.dumps(tail), _range_read_ok(tail))
    assert other.files_read == {}, "只读了结尾，不算读过全文"
    full_out = json.loads(_guarded_read(other))
    assert full_out["status"] == "success" and "read_hint" not in full_out
    assert other.files_read == {"f1": "第一章"}


@pytest.mark.unit
def test_range_read_covering_the_whole_file_counts_as_full_text():
    guard = RepeatReadGuard()
    tail = {"id": "f1", "tail_chars": 5000}
    out = json.loads(_guarded_read(guard, json.dumps(tail), _range_read_ok(tail)))
    assert out["data"][0]["content_partial"] is False
    assert guard.files_read == {"f1": "长篇合集"}


def _write_chapter_target(guard: RepeatReadGuard, file_id: str = "f1", title: str = "第一章") -> None:
    """write_chapter 把已有章节定为整章替换：成功但 mutation_applied=false，新正文之后由 StreamAdapter 落库。"""
    args = json.dumps({"chapter": 1})
    output = json.dumps(
        {"status": "success", "mutation_applied": False, "data": {"id": file_id, "title": title, "action": "replace"}},
        ensure_ascii=False,
    )
    guard.observe("write_chapter", guard.plan("write_chapter", args), output)


@pytest.mark.unit
def test_whole_chapter_replacement_target_resets_reads_of_that_chapter():
    """定为整章替换之后，这一章会被新正文换掉：再读不能提示「之后未被修改」，也不能被拦下。"""
    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(guard)
    _write_chapter_target(guard)

    reread = json.loads(_guarded_read(guard))
    assert reread["status"] == "success" and "read_hint" not in reread
    assert guard.files_targeted == {"f1": "第一章"}
    assert guard.files_written == {}, "定位不是写入"

    # 其他「成功但没改」的写工具结果（同名 rename 等）不清零
    renamed = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(renamed)
    renamed.observe(
        "rename_file",
        renamed.plan("rename_file", json.dumps({"id": "f1", "title": "第一章"})),
        json.dumps({"status": "success", "mutation_applied": False, "data": {"id": "f1", "title": "第一章"}}),
    )
    assert json.loads(_guarded_read(renamed))["error_type"] == REPEATED_READ_ERROR_TYPE


@pytest.mark.unit
def test_body_saved_by_the_stream_resets_reads_made_before_the_save():
    """同一批里定位后又读过一次、正文随后落库：落库之后再读是在看新正文，不算重复。"""
    from agent.tools.tool_context import AgentRequestContext

    req_ctx = AgentRequestContext(session=None, user_id="u", project_id="p", session_id="s")
    try:
        guard = RepeatReadGuard(request_context=req_ctx)
        _write_chapter_target(guard)
        _guarded_read(guard)
        assert "read_hint" in json.loads(_guarded_read(guard)), "落库之前的重复读取照常提示"

        req_ctx.mark_chapter_body_saved("f1", "第一章")
        reread = json.loads(_guarded_read(guard))
        assert reread["status"] == "success" and "read_hint" not in reread
        assert "read_hint" in json.loads(_guarded_read(guard)), "落库之后的重复读取重新计数"
    finally:
        req_ctx.clear()
