"""重复读取守卫、上限软着陆、失控停止退款与「继续」直达的回归测试。

线上事故：付费用户的 agent 反复 query_files 同一批文件直到单 agent 100 轮上限，
连续两次；「继续」又从头规划、再读一遍。熔断器只数失败，成功的重复读取看起来像进展。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.openai_agents.repeat_read_guard import (
    MAX_BLOCKED_READS_PER_REQUEST,
    NO_PROGRESS_STOP_REASON,
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
    assert second["read_hint"] == "该文件全文本轮已读取过且之后未被修改，请直接使用上文内容。"
    assert second["data"][0]["title"] == "第一章", "第 2 次照常返回内容"

    third = json.loads(_guarded_read(guard))
    assert third["read_hint"].startswith("警告")
    assert third["repeated_reads"] == 3

    for blocked_index in range(MAX_BLOCKED_READS_PER_REQUEST):
        blocked = json.loads(_guarded_read(guard))
        assert blocked["status"] == "error"
        assert blocked["error_type"] == REPEATED_READ_ERROR_TYPE
        assert "《第一章》(id=f1)" in blocked["error"]
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


def _parallel_output(tasks: list[dict]) -> str:
    return json.dumps(
        {"status": "success", "data": {"any_failed": False, "tasks": tasks}}, ensure_ascii=False
    )


def _parallel_read_result(file_id: str, title: str) -> dict:
    return {
        "type": "query_files",
        "status": "completed",
        "description": f"读 {title}",
        "result": {"status": "success", "data": [{"id": file_id, "title": title}]},
    }


@pytest.mark.unit
def test_parallel_read_subtasks_share_keys_and_blocked_ones_are_stripped():
    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(guard)  # 直接读 f1 三次

    raw = json.dumps(
        {
            "tasks": [
                {"type": "query_files", "description": "再读第一章", "params": {"id": "f1", "response_mode": "full"}},
                {"type": "query_files", "description": "读第二章", "params": {"id": "f2", "response_mode": "full"}},
            ]
        },
        ensure_ascii=False,
    )
    plan = guard.plan("parallel_execute", raw)
    assert plan.blocked_output is None
    executed = json.loads(plan.arguments)["tasks"]
    assert [task["params"]["id"] for task in executed] == ["f2"], "被拦下的子任务不执行"

    output = json.loads(
        guard.observe("parallel_execute", plan, _parallel_output([_parallel_read_result("f2", "第二章")]))
    )
    assert len(output["repeated_read_blocked"]) == 1
    assert "《第一章》(id=f1)" in output["repeated_read_blocked"][0]
    assert guard.blocked_reads == 1

    # 并行里读过的文件与直接读取同一个键：再直接读 f2 就是第 2 次
    assert "read_hint" in json.loads(_guarded_read(guard, json.dumps({"id": "f2", "response_mode": "full"}), _read_ok("f2", "第二章")))

    # 同一批全部被拦下：整次不执行
    only_blocked = guard.plan(
        "parallel_execute",
        json.dumps({"tasks": [{"type": "query_files", "params": {"id": "f1", "response_mode": "full"}}]}),
    )
    assert json.loads(only_blocked.blocked_output)["error_type"] == REPEATED_READ_ERROR_TYPE


@pytest.mark.unit
def test_duplicate_reads_within_one_parallel_batch_count():
    guard = RepeatReadGuard()
    task = {"type": "query_files", "params": {"id": "f1", "response_mode": "full"}}
    plan = guard.plan("parallel_execute", json.dumps({"tasks": [task] * 5}))
    # 同批第 4、5 个已经是第 4、5 次读取
    assert len(json.loads(plan.arguments)["tasks"]) == READ_BLOCK_AT - 1
    assert guard.blocked_reads == 5 - (READ_BLOCK_AT - 1)


@pytest.mark.unit
def test_parallel_write_subtask_resets_reads_and_is_recorded():
    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1):
        _guarded_read(guard)
    raw = json.dumps({"tasks": [{"type": "edit_file", "params": {"id": "f1", "edits": []}}]})
    plan = guard.plan("parallel_execute", raw)
    guard.observe(
        "parallel_execute",
        plan,
        _parallel_output(
            [{"type": "edit_file", "status": "completed", "result": {"status": "success", "data": {"id": "f1"}}}]
        ),
    )
    assert json.loads(_guarded_read(guard))["status"] == "success"
    assert "f1" in guard.files_written
    assert guard.written_file_ids() == ["f1"]


@pytest.mark.unit
def test_handoff_summary_lists_titles_and_ids_without_content():
    guard = RepeatReadGuard()
    _guarded_read(guard)
    _guarded_read(guard, json.dumps({"id": "f9", "response_mode": "summary"}), _read_ok("f9", "摘要读过的"))  # 摘要不算全文
    guard.observe(
        "create_file",
        guard.plan("create_file", json.dumps({"title": "第二章"}, ensure_ascii=False)),
        json.dumps({"status": "success", "data": {"id": "f2", "title": "第二章"}}, ensure_ascii=False),
    )
    summary = guard.handoff_summary()
    assert "本请求已读取全文：《第一章》(id=f1)" in summary
    assert "本请求已修改：《第二章》(id=f2)" in summary
    assert "f9" not in summary
    assert "正文……" not in summary
    assert guard.completed_items() == ["已修改《第二章》(id=f2)"]
    assert RepeatReadGuard().handoff_summary() == ""


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
        tools = {tool.name: tool for tool in build_agent_function_tools("writer", read_guard=guard)}
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
    from agent.openai_agents.runner import _stop_run_on_control_flow_tool

    guard = RepeatReadGuard()
    for _ in range(READ_BLOCK_AT - 1 + MAX_BLOCKED_READS_PER_REQUEST):
        _guarded_read(guard)
    result = _stop_run_on_control_flow_tool(None, [], read_guard=guard)
    assert result.is_final_output is True
    assert "反复读取" in result.final_output


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


# ------------------------------------------------- 用真实 SDK run-loop 端到端


@pytest.fixture
def _reset_metrics_and_model_cache():
    from agent.core.metrics import reset_metrics_collector
    from agent.openai_agents.model import reset_deepseek_sdk_cache

    reset_metrics_collector()
    reset_deepseek_sdk_cache()
    yield
    reset_deepseek_sdk_cache()
    reset_metrics_collector()


async def _unused_edit(_args):  # pragma: no cover - 脚本里不调用 edit_file
    raise AssertionError("edit_file should not be called")


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_metrics_and_model_cache")
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
    assert error["refundable"] is True, "没有任何写入：可以退还额度"
    assert error["blocked_reads"] == MAX_BLOCKED_READS_PER_REQUEST


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_metrics_and_model_cache")
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
@pytest.mark.usefixtures("_reset_metrics_and_model_cache")
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


# ------------------------------------------------------------------ 计费


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _decide(frames: list[str]) -> tuple[str, bool]:
    from agent.core.stream_billing import StreamBillingTracker

    tracker = StreamBillingTracker()
    for frame in frames:
        tracker.observe(frame)
    return tracker.decide(user_cancelled=False, unexpected_exception=False)


@pytest.mark.unit
def test_tool_call_cap_without_writes_is_refunded_even_with_streamed_text():
    frames = [
        _sse("content", {"text": "让我再读一下第一章……"}),
        _sse("tool_result", {"tool_name": "query_files", "status": "success"}),
        _sse("iteration_exhausted", {"layer": "tool_call", "iterations_used": 60, "max_iterations": 60}),
        _sse("done", {}),
    ]
    assert _decide(frames) == ("runaway_no_progress", True)


@pytest.mark.unit
def test_tool_call_cap_after_a_write_is_charged():
    frames = [
        _sse("tool_result", {"tool_name": "edit_file", "status": "success"}),
        _sse("iteration_exhausted", {"layer": "tool_call", "iterations_used": 60, "max_iterations": 60}),
        _sse("done", {}),
    ]
    assert _decide(frames) == ("completed", False)

    parallel_write = [
        _sse(
            "tool_result",
            {
                "tool_name": "parallel_execute",
                "status": "success",
                "data": {"tasks": [{"type": "write_chapter", "status": "completed"}]},
            },
        ),
        _sse("iteration_exhausted", {"layer": "tool_call"}),
        _sse("done", {}),
    ]
    assert _decide(parallel_write) == ("completed", False)


@pytest.mark.unit
def test_no_progress_and_model_call_limit_errors_refund_without_writes():
    no_progress = [
        _sse("content", {"text": "我再看看"}),
        _sse(
            "error",
            {"message": "x", "code": "ERR_AGENT_TOOL_FAILURE_LIMIT", "refundable": True, "reason": "no_progress"},
        ),
    ]
    assert _decide(no_progress) == ("runaway_no_progress", True)

    model_limit = [
        _sse("content", {"text": "继续分析"}),
        _sse("error", {"message": "x", "code": "ERR_AGENT_MODEL_CALL_LIMIT", "refundable": True}),
    ]
    assert _decide(model_limit) == ("runaway_no_progress", True)

    # 熔断（不是失控）仍然不退款；协作轮数耗尽也不算失控
    breaker = [_sse("error", {"message": "x", "code": "ERR_AGENT_TOOL_FAILURE_LIMIT", "refundable": False})]
    assert _decide(breaker) == ("non_refundable_error", False)
    collaboration = [_sse("iteration_exhausted", {"layer": "collaboration"}), _sse("done", {})]
    assert _decide(collaboration) == ("completed", False)


@pytest.mark.unit
def test_stream_adapter_passes_error_reason_to_sse_frame():
    import asyncio

    from agent.stream_adapter import StreamAdapter

    async def collect():
        adapter = StreamAdapter()
        return [
            event
            async for event in adapter._process_workflow_event(
                StreamEvent(
                    type=StreamEventType.ERROR,
                    data={
                        "error": "AI 反复读取……",
                        "code": "ERR_AGENT_TOOL_FAILURE_LIMIT",
                        "retryable": False,
                        "refundable": True,
                        "reason": NO_PROGRESS_STOP_REASON,
                    },
                )
            )
        ]

    frames = asyncio.run(collect())
    error_frames = [frame for frame in frames if frame.type == "error"]
    assert error_frames[-1].data["reason"] == NO_PROGRESS_STOP_REASON
    assert error_frames[-1].data["refundable"] is True


# --------------------------------------------------------- 「继续」直达


def _state_after(stop_reason: str | None, user_message: str = "继续", content: str = "阶段总结……"):
    previous = {"role": "assistant", "content": content}
    if stop_reason:
        previous["stop_reason"] = stop_reason
    return {
        "user_message": user_message,
        "router_message": user_message,
        "messages": [
            {"role": "user", "content": "把第一到三章都改一遍"},
            previous,
            {"role": "user", "content": user_message},
        ],
    }


@pytest.mark.unit
@pytest.mark.parametrize("message", ["继续", "继续。", "  请继续！", "请基于上一步结果继续完成剩余任务，优先最关键目标。"])
def test_bare_continue_after_exhaustion_resumes_previous_agent(message):
    from agent.graph.router import resume_route_after_exhaustion

    result = resume_route_after_exhaustion(_state_after("max_turns_exceeded", message))
    assert result is not None
    assert result["current_agent"] == "writer"
    assert result["workflow_plan"] == "quick"
    assert result["workflow_agents"] == []


@pytest.mark.unit
def test_continue_uses_last_agent_from_status_card_text():
    from agent.graph.router import resume_route_after_exhaustion

    card_text = "[iteration_exhausted]\nlayer: tool_call\niterations: 100/100\nlast_agent: planner"
    result = resume_route_after_exhaustion(_state_after(None, content=card_text))
    assert result is not None and result["current_agent"] == "planner"


@pytest.mark.unit
def test_continue_routes_normally_otherwise():
    from agent.graph.router import resume_route_after_exhaustion

    assert resume_route_after_exhaustion(_state_after("end_turn")) is None
    assert resume_route_after_exhaustion(_state_after("max_turns_exceeded", "继续写第四章，加一段打斗")) is None
    assert resume_route_after_exhaustion({"user_message": "继续", "messages": []}) is None


# ------------------------------------------------ writing_graph：审查上限与交接台账

LONG_TEXT = "第五章的正文。" * 200


def _text(text: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.TEXT, data={"text": text})


def _write_done(call_id: str) -> list[StreamEvent]:
    return [
        StreamEvent(type=StreamEventType.TOOL_USE, data={"id": call_id, "name": "edit_file", "status": "complete"}),
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": call_id,
                "name": "edit_file",
                "result": {"content": [{"type": "text", "text": '{"status": "success"}'}]},
            },
        ),
    ]


def _handoff(target: str, context: str) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.HANDOFF,
        data={
            "target_agent": target,
            "reason": "返修",
            "context": context,
            "handoff_packet": {
                "target_agent": target,
                "reason": "返修",
                "context": context,
                "completed": [],
                "todo": ["收紧第三段节奏"],
                "evidence": [],
            },
        },
    )


async def _run_graph(fake_agent, *, initial_agent="writer", router_result=None, auto_review_threshold=100):
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext

    state = {"user_message": "写第五章", "messages": [], "system_prompt": ""}
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=router_result or {})),
            patch("agent.graph.writing_graph.get_next_node", return_value=initial_agent),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
            patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
            patch(
                "agent.graph.writing_graph._auto_finalize_task_board_on_completion",
                AsyncMock(return_value=[]),
            ),
        ):
            return [
                event
                async for event in run_writing_workflow_streaming(
                    state=state, thread_id="t", auto_review_threshold=auto_review_threshold
                )
            ]
    finally:
        ToolContext.clear_context()


@pytest.mark.asyncio
@pytest.mark.unit
async def test_review_rounds_are_capped_and_reviewer_notes_are_surfaced(monkeypatch):
    from agent.graph.writing_graph import MAX_REVIEW_ROUNDS

    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    calls: list[str] = []
    reviewer_prompts: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "writer":
            for event in _write_done(f"w{len(calls)}"):
                yield event
            yield _text(LONG_TEXT)
            if len(calls) > 1:
                # 返工稿不再被自动质检门送审；模型无视提示词显式送审时才会有第 2 轮。
                yield _handoff("quality_reviewer", "返工完成，请复审")
            return
        reviewer_prompts.append(state["user_message"])
        yield _text("问题：第三段节奏拖沓。")
        yield _handoff("writer", "第三段节奏拖沓，需要压缩")

    events = await _run_graph(fake_agent)

    assert calls == ["writer", "quality_reviewer"] * MAX_REVIEW_ROUNDS, "第 2 轮审查后不再交回 writer"
    complete = [e for e in events if e.type == StreamEventType.WORKFLOW_COMPLETE]
    assert complete and complete[-1].data["reason"] == "review_round_limit"
    assert "第三段节奏拖沓" in complete[-1].data["review_notes"]
    surfaced = "".join(e.data.get("text", "") for e in events if e.type == StreamEventType.TEXT)
    assert "收紧第三段节奏" in surfaced
    assert all("追更指数" not in prompt for prompt in reviewer_prompts)
    assert "最后一轮" in reviewer_prompts[-1]
    assert not any(e.type == StreamEventType.ITERATION_EXHAUSTED for e in events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_handoffs_carry_request_read_and_write_log(monkeypatch):
    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    seen_messages: dict[str, str] = {}

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        seen_messages[agent_type] = state["user_message"]
        guard = state["repeat_read_guard"]
        if agent_type == "planner":
            _guarded_read(guard, FULL_READ, _read_ok("f1", "大纲"))
            yield _text("规划完成。")
            return
        if agent_type == "writer":
            guard.observe(
                "edit_file",
                guard.plan("edit_file", json.dumps({"id": "f5"})),
                json.dumps({"status": "success", "data": {"id": "f5", "title": "第五章"}}, ensure_ascii=False),
            )
            for event in _write_done("w1"):
                yield event
            yield _text(LONG_TEXT)
            return
        yield _text("审查通过。[TASK_COMPLETE]")

    events = await _run_graph(
        fake_agent,
        initial_agent="planner",
        router_result={"workflow_plan": "standard", "workflow_agents": ["writer"]},
    )

    assert "本请求已读取全文：《大纲》(id=f1)" in seen_messages["writer"]
    assert "本请求已修改：《第五章》(id=f5)" in seen_messages["quality_reviewer"]
    assert "正文……" not in seen_messages["writer"], "交接只带标题与 id，不带正文"

    handoffs = [e for e in events if e.type == StreamEventType.HANDOFF]
    review_packet = next(
        e.data["handoff_packet"] for e in handoffs if e.data["target_agent"] == "quality_reviewer"
    )
    assert review_packet["artifact_refs"] == ["f5"]
    assert review_packet["completed"] == ["已修改《第五章》(id=f5)"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_continue_after_exhaustion_skips_llm_router():
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext

    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        yield _text("接着完成。")

    state = _state_after("max_turns_exceeded")
    state["system_prompt"] = ""
    router = AsyncMock(return_value={"current_agent": "planner", "workflow_agents": ["writer"]})
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch("agent.graph.writing_graph.router_node", router),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
        ):
            events = [e async for e in run_writing_workflow_streaming(state=state, thread_id="t")]
    finally:
        ToolContext.clear_context()

    router.assert_not_called()
    assert calls == ["writer"]
    decided = next(e for e in events if e.type == StreamEventType.ROUTER_DECIDED)
    assert decided.data["routing_metadata"]["reason"] == "resume_after_exhaustion"
