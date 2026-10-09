"""工具重复失败熔断：判定规则、工具回调接线，以及经真实 SDK run-loop 的端到端行为。"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.openai_agents.tool_failure_breaker import (
    MAX_IDENTICAL_TOOL_FAILURES,
    MAX_TOOL_FAILURES_PER_REQUEST,
    TRIP_REASON_IDENTICAL,
    TRIP_REASON_TOOL_NOT_FOUND,
    TRIP_REASON_TOTAL,
    ToolFailureBreaker,
)

EDIT_ARGS = json.dumps({"id": "file-1", "op": "replace", "old": "甲", "new": "乙"}, ensure_ascii=False)


def _error(message: str, **extra) -> str:
    return json.dumps({"status": "error", "error": message, **extra}, ensure_ascii=False)


def _ok() -> str:
    return json.dumps({"status": "success", "data": {"id": "file-1"}})


# ---------------------------------------------------------------------------
# 判定规则
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_identical_failures_trip_on_third_and_model_keeps_first_two_retries():
    breaker = ToolFailureBreaker()

    first = breaker.observe("edit_file", EDIT_ARGS, _error("database is locked"))
    assert not breaker.is_open
    # 第一次失败原样交还模型，不加任何提示
    assert first == _error("database is locked")

    second = breaker.observe("edit_file", EDIT_ARGS, _error("database is locked"))
    assert not breaker.is_open
    hinted = json.loads(second)
    assert hinted["status"] == "error"
    assert hinted["error"] == "database is locked"
    assert hinted["repeated_failures"] == 2
    assert "不要原样重试" in hinted["retry_hint"]

    breaker.observe("edit_file", EDIT_ARGS, _error("database is locked"))
    assert breaker.is_open
    trip = breaker.trip
    assert trip is not None
    assert trip.reason == TRIP_REASON_IDENTICAL
    assert trip.tool_name == "edit_file"
    assert trip.failures == MAX_IDENTICAL_TOOL_FAILURES == 3
    # 工具名与错误原文只留在 trip 上（写日志），不进给作者看的说明。
    assert trip.last_error == "database is locked"
    assert "database is locked" not in trip.user_message()
    assert "edit_file" not in trip.user_message()


@pytest.mark.unit
def test_success_of_same_call_resets_streak():
    breaker = ToolFailureBreaker()
    breaker.observe("edit_file", EDIT_ARGS, _error("database is locked"))
    breaker.observe("edit_file", EDIT_ARGS, _error("database is locked"))
    breaker.observe("edit_file", EDIT_ARGS, _ok())
    breaker.observe("edit_file", EDIT_ARGS, _error("database is locked"))
    breaker.observe("edit_file", EDIT_ARGS, _error("database is locked"))

    assert not breaker.is_open
    assert breaker.total_failures == 4


@pytest.mark.unit
def test_interleaved_successful_other_calls_do_not_hide_identical_retries():
    """模型在两次原样重试之间穿插只读调用（query_files 成功），仍应熔断。"""
    breaker = ToolFailureBreaker()
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES):
        breaker.observe("query_files", '{"query": "第一章"}', _ok())
        breaker.observe("edit_file", EDIT_ARGS, _error("database is locked"))

    assert breaker.is_open
    assert breaker.trip.reason == TRIP_REASON_IDENTICAL


@pytest.mark.unit
def test_equivalent_errors_with_varying_ids_and_numbers_count_as_same():
    breaker = ToolFailureBreaker()
    for attempt in range(MAX_IDENTICAL_TOOL_FAILURES):
        breaker.observe(
            "edit_file",
            EDIT_ARGS,
            _error(
                "执行失败: (sqlite3.OperationalError) database is locked "
                f"[SQL: UPDATE file SET updated_at=? WHERE id=?] "
                f"[parameters: ('2026-10-03 12:00:{attempt:02d}.{attempt}12345', "
                f"'1b4e28ba-2fa1-11d2-883f-00{attempt}6b4bd3d{attempt}a')]"
            ),
        )
    assert breaker.is_open
    assert breaker.trip.reason == TRIP_REASON_IDENTICAL


@pytest.mark.unit
def test_argument_key_order_does_not_matter():
    breaker = ToolFailureBreaker()
    breaker.observe("edit_file", '{"id": "a", "op": "append"}', _error("boom"))
    breaker.observe("edit_file", '{"op": "append", "id": "a"}', _error("boom"))
    breaker.observe("edit_file", '{ "op":"append",  "id":"a" }', _error("boom"))
    assert breaker.is_open


@pytest.mark.unit
def test_different_error_for_same_call_restarts_streak():
    breaker = ToolFailureBreaker()
    breaker.observe("edit_file", EDIT_ARGS, _error("database is locked"))
    breaker.observe("edit_file", EDIT_ARGS, _error("database is locked"))
    breaker.observe("edit_file", EDIT_ARGS, _error("片段不唯一，请提供更长的片段"))
    breaker.observe("edit_file", EDIT_ARGS, _error("片段不唯一，请提供更长的片段"))
    assert not breaker.is_open


@pytest.mark.unit
def test_changing_arguments_is_normal_recovery_not_identical_failure():
    breaker = ToolFailureBreaker()
    for idx in range(MAX_IDENTICAL_TOOL_FAILURES + 1):
        output = breaker.observe(
            "edit_file",
            json.dumps({"id": "file-1", "old": f"片段{idx}"}, ensure_ascii=False),
            _error("找不到片段"),
        )
        # 参数变了就不是「原样重试」，不附加重复失败提示
        assert "retry_hint" not in json.loads(output)
    assert not breaker.is_open


@pytest.mark.unit
def test_total_failure_cap_is_backstop_for_non_identical_thrash():
    breaker = ToolFailureBreaker()
    for idx in range(MAX_TOOL_FAILURES_PER_REQUEST - 1):
        breaker.observe("edit_file", json.dumps({"id": "file-1", "old": f"s{idx}"}), _error("找不到片段"))
    assert not breaker.is_open

    breaker.observe("create_file", '{"title": "x"}', _error("标题重复"))
    assert breaker.is_open
    trip = breaker.trip
    assert trip.reason == TRIP_REASON_TOTAL
    assert trip.failures == MAX_TOOL_FAILURES_PER_REQUEST
    assert trip.tool_name == "create_file"
    assert "这一轮先停下了" in trip.user_message()
    assert "create_file" not in trip.user_message()


@pytest.mark.unit
@pytest.mark.parametrize(
    "output",
    [
        json.dumps({"status": "success", "data": []}),
        json.dumps({"status": "handoff", "target_agent": "writer"}),
        json.dumps({"status": "clarification_needed", "question": "?"}),
        json.dumps({"status": "ignored", "reason": "noop"}),
        "plain text output",
        "",
    ],
)
def test_non_error_outputs_are_not_failures(output):
    breaker = ToolFailureBreaker()
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES + 1):
        assert breaker.observe("query_files", "{}", output) == output
    assert breaker.total_failures == 0
    assert not breaker.is_open


@pytest.mark.unit
def test_invalid_json_arguments_use_raw_text_as_key():
    breaker = ToolFailureBreaker()
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES):
        breaker.observe("edit_file", "[1, 2", _error("Invalid tool input JSON", error_type="invalid_tool_input_json"))
    assert breaker.is_open


@pytest.mark.unit
def test_trip_event_data_is_error_event_shape():
    breaker = ToolFailureBreaker()
    long_error = "database is locked " + "x" * 1000
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES):
        breaker.observe("edit_file", EDIT_ARGS, _error(long_error))
    data = breaker.trip.as_event_data("writer")
    # StreamAdapter 读取 data["error"] 作为前端展示的错误信息
    assert data["error"] == breaker.trip.user_message()
    assert data["error_type"] == "ToolFailureCircuitOpen"
    assert data["reason"] == TRIP_REASON_IDENTICAL
    assert data["agent_type"] == "writer"
    # 工具名与错误原文只写日志，不随 ERROR 事件走（SSE 帧也就带不出去）。
    assert "last_error" not in data
    assert "tool_name" not in data
    assert len(breaker.trip.last_error) <= 201


@pytest.mark.unit
def test_user_message_drops_sqlalchemy_statement_details():
    breaker = ToolFailureBreaker()
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES):
        breaker.observe(
            "edit_file",
            EDIT_ARGS,
            _error(
                "执行失败: (sqlite3.OperationalError) database is locked\n"
                "[SQL: UPDATE file SET content=? WHERE file.id = ?]\n"
                "[parameters: ('正文片段', 'file-1')]\n"
                "(Background on this error at: https://sqlalche.me/e/20/e3q8)"
            ),
        )
    trip = breaker.trip
    assert trip.last_error == "执行失败: (sqlite3.OperationalError) database is locked"
    assert "正文片段" not in trip.user_message()


def _parallel(tasks: list[dict], *, completed: int = 0) -> str:
    failed = sum(1 for task in tasks if task.get("status") == "failed")
    return json.dumps(
        {
            "status": "success",
            "data": {
                "total_tasks": len(tasks),
                "completed": completed,
                "failed": failed,
                "any_failed": failed > 0,
                "tasks": tasks,
            },
        },
        ensure_ascii=False,
    )


PARALLEL_ARGS = json.dumps(
    {"tasks": [{"type": "edit_file", "id": "file-1"}, {"type": "edit_file", "id": "file-2"}]}
)


@pytest.mark.unit
def test_parallel_execute_with_all_tasks_failed_counts_and_trips():
    """parallel_execute 把子任务失败藏在 success 外壳里，也必须计数并熔断。"""
    breaker = ToolFailureBreaker()
    outputs = []
    for attempt in range(MAX_IDENTICAL_TOOL_FAILURES):
        # 每次报错里的时间戳不同，归一后仍是同一种错误；子任务顺序也不影响
        tasks = [
            {"id": "t1", "type": "edit_file", "status": "failed", "error": f"database is locked @{attempt}"},
            {"id": "t2", "type": "edit_file", "status": "failed", "error": f"database is locked @{attempt}"},
        ]
        if attempt % 2:
            tasks.reverse()
        outputs.append(breaker.observe("parallel_execute", PARALLEL_ARGS, _parallel(tasks)))

    assert breaker.total_failures == MAX_IDENTICAL_TOOL_FAILURES
    assert breaker.is_open
    assert breaker.trip.reason == TRIP_REASON_IDENTICAL
    assert breaker.trip.tool_name == "parallel_execute"
    assert "database is locked" in breaker.trip.last_error
    # 第 2 次的提示附加在原始输出上：逐任务明细仍然交给模型
    hinted = json.loads(outputs[1])
    assert hinted["status"] == "success"
    assert hinted["data"]["tasks"][0]["status"] == "failed"
    assert hinted["repeated_failures"] == 2


@pytest.mark.unit
def test_parallel_execute_partial_failure_counts_but_full_success_does_not():
    breaker = ToolFailureBreaker()
    ok_task = {"id": "t1", "type": "edit_file", "status": "completed", "error": None}
    bad_task = {"id": "t2", "type": "edit_file", "status": "failed", "error": "找不到片段"}

    breaker.observe("parallel_execute", PARALLEL_ARGS, _parallel([ok_task, ok_task], completed=2))
    assert breaker.total_failures == 0

    breaker.observe("parallel_execute", PARALLEL_ARGS, _parallel([ok_task, bad_task], completed=1))
    assert breaker.total_failures == 1
    assert not breaker.is_open


@pytest.mark.unit
def test_ordering_rejection_counts_toward_cap_but_never_trips_identical_rule():
    """「先写完上一个空文件」这类顺序性拒绝：模型补完前置动作即可成功，
    不按同一调用连续失败熔断（否则空文件纠偏轮也跑不到），只受累计上限约束。"""
    breaker = ToolFailureBreaker()
    rejection = _error(
        "文件「苏晚」未创建：上一个文件「沈砚」正文还是空的",
        error_type="pending_empty_file_unwritten",
    )
    args = json.dumps({"title": "苏晚", "file_type": "character"}, ensure_ascii=False)
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES + 2):
        output = breaker.observe("create_file", args, rejection)
        # 不附加「系统侧故障请停止」的通用提示，原样交还工具自己的解除说明
        assert output == rejection
    assert not breaker.is_open
    assert breaker.total_failures == MAX_IDENTICAL_TOOL_FAILURES + 2

    for _ in range(MAX_TOOL_FAILURES_PER_REQUEST - breaker.total_failures):
        breaker.observe("create_file", args, rejection)
    assert breaker.is_open
    assert breaker.trip.reason == TRIP_REASON_TOTAL


# ---------------------------------------------------------------------------
# 接线：工具回调与 tool_use_behavior
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_function_tools_record_results_and_short_circuit_after_trip():
    from agent.openai_agents.tools_adapter import build_agent_function_tools

    calls: list[dict] = []

    async def failing_edit(args):
        calls.append(args)
        return {"content": [{"type": "text", "text": _error("database is locked")}]}

    breaker = ToolFailureBreaker()
    with patch.dict("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"edit_file": failing_edit}):
        tools = {tool.name: tool for tool in build_agent_function_tools("writer", failure_breaker=breaker)}
        edit_tool = tools["edit_file"]
        for _ in range(MAX_IDENTICAL_TOOL_FAILURES):
            await edit_tool.on_invoke_tool(None, EDIT_ARGS)
        assert breaker.is_open
        assert len(calls) == MAX_IDENTICAL_TOOL_FAILURES

        # 熔断后同一轮里剩下的调用不再执行（写工具绝不能在熔断后落库）
        short = json.loads(await edit_tool.on_invoke_tool(None, EDIT_ARGS))
        assert short["status"] == "error"
        assert short["error_type"] == "tool_failure_circuit_open"
        assert len(calls) == MAX_IDENTICAL_TOOL_FAILURES


@pytest.mark.unit
def test_tool_use_behavior_ends_run_once_breaker_is_open():
    from agent.openai_agents.runner import _stop_run_on_control_flow_tool

    breaker = ToolFailureBreaker()
    failing_result = SimpleNamespace(tool=SimpleNamespace(name="edit_file"), output=_error("boom"))

    breaker.observe("edit_file", EDIT_ARGS, _error("boom"))
    decision = _stop_run_on_control_flow_tool(None, [failing_result], failure_breaker=breaker)
    assert decision.is_final_output is False

    for _ in range(MAX_IDENTICAL_TOOL_FAILURES - 1):
        breaker.observe("edit_file", EDIT_ARGS, _error("boom"))
    decision = _stop_run_on_control_flow_tool(None, [failing_result], failure_breaker=breaker)
    assert decision.is_final_output is True

    # 不传熔断器时保持原行为
    assert _stop_run_on_control_flow_tool(None, [failing_result]).is_final_output is False


@pytest.mark.asyncio
@pytest.mark.unit
async def test_stream_adapter_ends_on_trip_error_and_closes_workflow():
    """runner 的熔断 ERROR 经 StreamAdapter 成为 SSE 终止帧：不再发 done，
    并立即关闭上游工作流生成器（计划内交接/自动质检都不会再跑）。"""
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.stream_adapter import StreamAdapter, StreamAdapterConfig

    breaker = ToolFailureBreaker()
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES):
        breaker.observe("edit_file", EDIT_ARGS, _error("database is locked"))

    upstream = {"closed": False, "resumed_after_error": False}

    async def workflow():
        try:
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={"stop_reason": "tool_failure_circuit_open", "usage": {"total_tokens": 9}},
            )
            yield StreamEvent(type=StreamEventType.ERROR, data=breaker.trip.as_event_data("writer"))
            upstream["resumed_after_error"] = True
            yield StreamEvent(type=StreamEventType.HANDOFF, data={"target_agent": "quality_reviewer"})
        finally:
            upstream["closed"] = True

    adapter = StreamAdapter(StreamAdapterConfig(process_file_markers=False))
    sse_events = [event async for event in adapter.process_workflow_events(workflow())]

    assert [event.type.value for event in sse_events][-1] == "error"
    assert "done" not in [event.type.value for event in sse_events]
    assert sse_events[-1].data["message"] == breaker.trip.user_message()
    assert upstream == {"closed": True, "resumed_after_error": False}
    # 熔断前的 usage 已计入本轮元数据，随历史落库
    assert adapter.get_last_message_metadata()["usage"] == {"total_tokens": 9}


# ---------------------------------------------------------------------------
# 端到端：本地 OpenAI 兼容端点 + 真实 SDK run-loop
# ---------------------------------------------------------------------------


def _wait_until_serving(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError:
            time.sleep(0.02)
    raise AssertionError(f"local test server never started listening on {host}:{port}")


def _chunk(delta: dict, finish_reason: str | None = None, usage: dict | None = None) -> dict:
    payload = {
        "id": "chatcmpl-local",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def _tool_call_chunks(call_id: str, name: str, arguments: str) -> list[dict]:
    return [
        _chunk({"role": "assistant", "content": ""}),
        _chunk(
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": call_id,
                        "type": "function",
                        "function": {"name": name, "arguments": arguments},
                    }
                ]
            }
        ),
        _chunk({}, "tool_calls", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}),
    ]


def _text_chunks(text: str) -> list[dict]:
    return [
        _chunk({"role": "assistant", "content": ""}),
        _chunk({"content": text}),
        _chunk({}, "stop", {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}),
    ]


async def _run_against_local_model(
    monkeypatch,
    script,
    edit_impl,
    *,
    state_extra: dict | None = None,
    tool_impls: dict | None = None,
    agent_type: str = "writer",
):
    """script(request_index) -> 该次模型调用要返回的 chunk 列表。"""
    from agent.openai_agents.model import reset_deepseek_sdk_cache
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    requests: list[dict] = []
    server_errors: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):  # noqa: A002
            return

        def do_POST(self):
            try:
                length = int(self.headers.get("content-length") or "0")
                body = json.loads(self.rfile.read(length) or b"{}")
                requests.append(body)
                chunks = script(len(requests) - 1)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                for chunk in chunks:
                    self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception as exc:  # pragma: no cover - surfaced via assertion
                server_errors.append(f"{type(exc).__name__}: {exc}")
                self.send_response(500)
                self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_serving("127.0.0.1", port)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-local-test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    for proxy_var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(proxy_var, raising=False)
    reset_deepseek_sdk_cache()

    state = {"user_message": "把第一章的甲改成乙", "messages": [], "system_prompt": "base"}
    state.update(state_extra or {})
    impls = {"edit_file": edit_impl, **(tool_impls or {})}
    try:
        with patch.dict("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", impls):
            events = [
                event
                async for event in run_openai_agents_streaming_agent(
                    state=state,
                    agent_type=agent_type,
                    system_prompt="你是测试助手",
                )
            ]
    finally:
        server.shutdown()
        server.server_close()
        await asyncio.to_thread(thread.join, 1)
        reset_deepseek_sdk_cache()

    assert server_errors == []
    return events, requests, state


def _tool_messages(request_body: dict) -> list[str]:
    return [
        str(message.get("content") or "")
        for message in request_body.get("messages", [])
        if message.get("role") == "tool"
    ]


@pytest.fixture
def _reset_metrics_and_model_cache():
    from agent.core.metrics import reset_metrics_collector
    from agent.openai_agents.model import reset_deepseek_sdk_cache

    reset_metrics_collector()
    reset_deepseek_sdk_cache()
    yield
    reset_deepseek_sdk_cache()
    reset_metrics_collector()


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_metrics_and_model_cache")
async def test_sdk_run_stops_after_identical_failures_and_ends_with_error(monkeypatch):
    from agent.core.workflow_events import StreamEventType
    from agent.openai_agents.runner import TOOL_FAILURE_STOP_REASON

    attempts: list[dict] = []

    async def locked_edit(args):
        attempts.append(args)
        # 每次报错里的时间戳都不同：等价错误也必须被识别为「相同」
        return {
            "content": [
                {
                    "type": "text",
                    "text": _error(f"执行失败: database is locked [parameters: ('12:00:{len(attempts):02d}',)]"),
                }
            ]
        }

    def script(index: int) -> list[dict]:
        # 模型「不听劝」：无论看到什么错误都原样重试
        return _tool_call_chunks(f"call-{index}", "edit_file", EDIT_ARGS)

    events, requests, state = await _run_against_local_model(monkeypatch, script, locked_edit)

    # 第 3 次相同失败后 run 立即结束：工具执行 3 次、模型只被调用 3 次，没有第 4 轮
    assert len(attempts) == MAX_IDENTICAL_TOOL_FAILURES
    assert len(requests) == MAX_IDENTICAL_TOOL_FAILURES

    # 前两次失败都交还给了模型（第 2、3 次模型调用的输入里能看到工具错误）
    assert "database is locked" in _tool_messages(requests[1])[-1]
    assert "retry_hint" in _tool_messages(requests[2])[-1]

    types = [event.type for event in events]
    assert types[0] == StreamEventType.MESSAGE_START
    assert types.count(StreamEventType.TOOL_RESULT) == MAX_IDENTICAL_TOOL_FAILURES
    assert types[-2:] == [StreamEventType.MESSAGE_END, StreamEventType.ERROR]
    assert StreamEventType.HANDOFF not in types

    message_end = events[-2].data
    assert message_end["stop_reason"] == TOOL_FAILURE_STOP_REASON
    # 熔断前已消耗的 token 仍要入账
    assert message_end["usage"]["total_tokens"] == 15 * MAX_IDENTICAL_TOOL_FAILURES

    error = events[-1].data
    assert error["reason"] == TRIP_REASON_IDENTICAL
    # 工具名与错误原文只进日志（runner 的熔断告警），不随 ERROR 事件给作者。
    assert "tool_name" not in error
    assert "database is locked" not in error["error"]

    # 已执行的工具调用照常写回会话状态，供后续回放/落库
    assistant_turn = state["messages"][-2]
    tool_uses = [block for block in assistant_turn["content"] if block["type"] == "tool_use"]
    assert len(tool_uses) == MAX_IDENTICAL_TOOL_FAILURES


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_metrics_and_model_cache")
async def test_sdk_run_recovers_when_retry_succeeds(monkeypatch):
    from agent.core.workflow_events import StreamEventType

    attempts: list[dict] = []

    async def flaky_edit(args):
        attempts.append(args)
        text = _error("database is locked") if len(attempts) <= 2 else _ok()
        return {"content": [{"type": "text", "text": text}]}

    def script(index: int) -> list[dict]:
        if index < 3:
            return _tool_call_chunks(f"call-{index}", "edit_file", EDIT_ARGS)
        return _text_chunks("已改好")

    events, requests, _state = await _run_against_local_model(monkeypatch, script, flaky_edit)

    assert len(attempts) == 3
    assert len(requests) == 4
    types = [event.type for event in events]
    assert StreamEventType.ERROR not in types
    assert types[-1] == StreamEventType.MESSAGE_END
    assert events[-1].data["stop_reason"] == "end_turn"
    assert "".join(e.data.get("text", "") for e in events if e.type == StreamEventType.TEXT) == "已改好"


@pytest.mark.unit
def test_read_only_tool_set_keeps_write_tools_but_marks_them_unavailable():
    """只读请求：写文件工具仍在工具集里（模型调用时拿到可恢复错误而非 SDK 致命错误），
    描述里标明本轮不可用；其余工具不受影响。"""
    from agent.openai_agents.tools_adapter import (
        READ_ONLY_TOOL_DESCRIPTION_PREFIX,
        build_agent_function_tools,
    )
    from agent.tools.registry import FILE_WRITE_TOOL_NAMES

    full = {tool.name: tool for tool in build_agent_function_tools("writer")}
    read_only = {tool.name: tool for tool in build_agent_function_tools("writer", read_only=True)}

    assert FILE_WRITE_TOOL_NAMES <= set(full)
    assert set(read_only) == set(full)
    for name, tool in read_only.items():
        marked = tool.description.startswith(READ_ONLY_TOOL_DESCRIPTION_PREFIX)
        assert marked is (name in FILE_WRITE_TOOL_NAMES), name


@pytest.mark.asyncio
@pytest.mark.unit
async def test_read_only_write_tools_refuse_without_executing():
    from agent.openai_agents.tools_adapter import build_agent_function_tools

    executed: list[str] = []

    async def fake_tool(args):
        executed.append("called")
        return {"content": [{"type": "text", "text": _ok()}]}

    breaker = ToolFailureBreaker()
    with patch.dict(
        "agent.openai_agents.tools_adapter.TOOL_FUNCTIONS",
        {"create_file": fake_tool, "query_files": fake_tool},
    ):
        tools = {
            tool.name: tool
            for tool in build_agent_function_tools("writer", failure_breaker=breaker, read_only=True)
        }
        refused = json.loads(await tools["create_file"].on_invoke_tool(None, '{"title": "x"}'))
        allowed = json.loads(await tools["query_files"].on_invoke_tool(None, "{}"))

    assert refused["status"] == "error"
    assert refused["error_type"] == "read_only_request"
    assert allowed["status"] == "success"
    # 只有读工具真正执行；拒绝计入熔断器的累计失败
    assert executed == ["called"]
    assert breaker.total_failures == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_uses_request_breaker_and_read_only_flag_from_state():
    """runner 从 state 取请求级熔断器与只读标记交给 _build_agent。"""
    from agent.openai_agents import runner as runner_module

    captured: dict = {}

    def fake_build_agent(agent_type, system_prompt, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after build")

    shared = ToolFailureBreaker()
    state = {
        "user_message": "只分析一下",
        "messages": [],
        "tool_failure_breaker": shared,
        "read_only": True,
    }
    with patch.object(runner_module, "_build_agent", side_effect=fake_build_agent):
        # runner 把构建异常转成 ERROR 事件；这里只关心交给 _build_agent 的参数
        _events = [
            event
            async for event in runner_module.run_openai_agents_streaming_agent(
                state=state, agent_type="writer", system_prompt="sys"
            )
        ]

    assert captured["failure_breaker"] is shared
    assert captured["read_only"] is True


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_metrics_and_model_cache")
async def test_sdk_read_only_request_write_attempt_recovers_with_answer(monkeypatch):
    """只读请求下模型照着提示词/历史调用 create_file：拿到可恢复错误后改为直接回答，
    整个 run 正常结束，没有 ERROR，写工具从未执行。"""
    from agent.core.workflow_events import StreamEventType

    executed: list[dict] = []

    async def create_impl(args):
        executed.append(args)
        return {"content": [{"type": "text", "text": _ok()}]}

    def script(index: int) -> list[dict]:
        if index == 0:
            return _tool_call_chunks("call-0", "create_file", '{"title": "节奏分析", "file_type": "draft"}')
        return _text_chunks("这一章节奏前紧后松。")

    events, requests, _state = await _run_against_local_model(
        monkeypatch,
        script,
        create_impl,
        state_extra={"read_only": True},
        tool_impls={"create_file": create_impl},
    )

    assert executed == []
    assert len(requests) == 2
    refusal = json.loads(_tool_messages(requests[1])[-1])
    assert refusal["error_type"] == "read_only_request"
    types = [event.type for event in events]
    assert StreamEventType.ERROR not in types
    assert events[-1].type == StreamEventType.MESSAGE_END
    assert events[-1].data["stop_reason"] == "end_turn"
    text = "".join(e.data.get("text", "") for e in events if e.type == StreamEventType.TEXT)
    assert text == "这一章节奏前紧后松。"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_metrics_and_model_cache")
async def test_sdk_unknown_tool_is_returned_to_model_not_fatal(monkeypatch):
    """模型调用工具集里没有的工具（审稿人照着共享历史调 edit_file）：SDK 不再抛
    ModelBehaviorError，错误交还模型，run 以回答正常结束。"""
    from agent.core.workflow_events import StreamEventType

    def script(index: int) -> list[dict]:
        if index == 0:
            return _tool_call_chunks("call-0", "edit_file", EDIT_ARGS)
        return _text_chunks("审查意见如下。")

    events, requests, _state = await _run_against_local_model(
        monkeypatch, script, None, agent_type="quality_reviewer"
    )

    assert len(requests) == 2
    not_found = json.loads(_tool_messages(requests[1])[-1])
    assert not_found["error_type"] == "tool_not_found"
    types = [event.type for event in events]
    assert StreamEventType.ERROR not in types
    assert events[-1].data["stop_reason"] == "end_turn"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.usefixtures("_reset_metrics_and_model_cache")
async def test_sdk_unknown_tool_thrash_is_bounded_by_breaker(monkeypatch):
    """模型反复调用不存在的工具：同样受熔断器约束，不会一直烧到 max_turns。"""
    from agent.core.workflow_events import StreamEventType

    def script(index: int) -> list[dict]:
        return _tool_call_chunks(f"call-{index}", "rewrite_chapter", "{}")

    events, requests, _state = await _run_against_local_model(monkeypatch, script, None)

    assert len(requests) <= MAX_IDENTICAL_TOOL_FAILURES + 1
    assert events[-1].type == StreamEventType.ERROR
    assert events[-1].data["reason"] == TRIP_REASON_TOOL_NOT_FOUND
    assert "相同参数" not in events[-1].data["error"]
    assert "rewrite_chapter" not in events[-1].data["error"]


@pytest.mark.unit
def test_unknown_tool_trip_has_its_own_reason_and_message():
    """不存在的工具被连续调用 3 次：熔断原因与文案单独一套，不说「以相同参数连续失败」
    （调用根本没执行，formatter 记账时参数是空串）。"""
    from agent.openai_agents.runner import _format_tool_error

    breaker = ToolFailureBreaker()
    args = SimpleNamespace(kind="tool_not_found", tool_name="rewrite_chapter")
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES):
        text = _format_tool_error(args, failure_breaker=breaker)
        assert json.loads(text)["error_type"] == "tool_not_found"

    trip = breaker.trip
    assert trip is not None
    assert trip.reason == TRIP_REASON_TOOL_NOT_FOUND
    assert trip.failures == MAX_IDENTICAL_TOOL_FAILURES
    message = trip.user_message()
    assert "相同参数" not in message
    assert "rewrite_chapter" not in message
    assert "用不了的功能" in message
    assert trip.as_event_data("writer")["reason"] == TRIP_REASON_TOOL_NOT_FOUND


@pytest.mark.unit
def test_real_tool_identical_failures_keep_identical_reason():
    """真实工具以相同参数重复失败仍是 TRIP_REASON_IDENTICAL。"""
    breaker = ToolFailureBreaker()
    error = json.dumps({"status": "error", "error": "database is locked"})
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES):
        breaker.observe("edit_file", '{"id": "f1"}', error)

    assert breaker.trip is not None
    assert breaker.trip.reason == TRIP_REASON_IDENTICAL
    assert "连续几次都没做成" in breaker.trip.user_message()


@pytest.mark.unit
def test_repeated_read_only_refusal_trips_as_unavailable_call():
    """只读请求下同一写调用连续被拒：调用没执行，原因与文案同「本轮不可用」，不说「相同参数」。"""
    breaker = ToolFailureBreaker()
    refusal = json.dumps(
        {"status": "error", "error_type": "read_only_request", "error": "本轮只回答，不修改文件"}
    )
    for _ in range(MAX_IDENTICAL_TOOL_FAILURES):
        breaker.observe("edit_file", '{"id": "f1"}', refusal)

    trip = breaker.trip
    assert trip is not None
    assert trip.reason == TRIP_REASON_TOOL_NOT_FOUND
    assert "相同参数" not in trip.user_message()
