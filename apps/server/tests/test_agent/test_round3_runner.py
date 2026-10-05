"""Round-3 回归测试：openai-agents 适配层（runner）。

覆盖两条确认缺陷：
- #9  控制流工具（request_clarification / handoff_to_agent）之后 SDK 仍多跑一整轮
      模型调用并真实执行其工具，「工作流停下」的语义被破坏。
- #30 DeepSeek 流式请求缺 stream_options.include_usage，MESSAGE_END 的 usage 恒为空。

大部分用例跑真实的 openai-agents run-loop + 真实 AsyncOpenAI 客户端，只是把
base_url 指向本地 SSE 桩服务器，并把会落库的工具换成 spy。
"""

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _reset_metrics_and_model_cache():
    from agent.core.metrics import reset_metrics_collector
    from agent.openai_agents.model import reset_deepseek_sdk_cache

    reset_metrics_collector()
    reset_deepseek_sdk_cache()
    yield
    reset_deepseek_sdk_cache()
    reset_metrics_collector()


# ---------------------------------------------------------------------------
# 本地 OpenAI 兼容 SSE 桩
# ---------------------------------------------------------------------------


def _chunk(delta=None, finish=None, usage=None):
    payload = {
        "id": "chatcmpl-local",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": "deepseek-flash",
        "choices": [{"index": 0, "delta": delta or {}, "finish_reason": finish}],
    }
    if usage is not None:
        payload["usage"] = usage
    return payload


def _text_turn(text: str, usage=None):
    return [
        _chunk(delta={"role": "assistant", "content": ""}),
        _chunk(delta={"content": text}),
        _chunk(finish="stop", usage=usage),
    ]


def _tool_turn(call_id: str, name: str, arguments: dict, *, prefix_text: str | None = None, usage=None):
    chunks = [_chunk(delta={"role": "assistant", "content": ""})]
    if prefix_text:
        chunks.append(_chunk(delta={"content": prefix_text}))
    chunks.append(
        _chunk(
            delta={
                "tool_calls": [
                    {
                        "index": 0,
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": json.dumps(arguments, ensure_ascii=False),
                        },
                    }
                ]
            }
        )
    )
    chunks.append(_chunk(finish="tool_calls", usage=usage))
    return chunks


def _wait_until_serving(host: str, port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError as exc:
            last_err = exc
            time.sleep(0.02)
    raise AssertionError(f"本地桩服务器未能启动: {last_err}")


class _ChatCompletionsStub:
    """按脚本逐次返回 turn 的 /chat/completions 桩，并记录收到的请求体。"""

    def __init__(self, turns, fallback, *, usage_only_when_requested=None):
        self.turns = turns
        self.fallback = fallback
        # 传入 usage dict 时，模拟 DeepSeek 官方行为：只有请求带
        # stream_options.include_usage=true 才在末尾 chunk 补发 usage。
        self.usage_only_when_requested = usage_only_when_requested
        self.requests: list[dict] = []
        self.errors: list[str] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format, *args):  # noqa: A002
                return

            def do_POST(self):
                try:
                    length = int(self.headers.get("content-length") or "0")
                    body = json.loads(self.rfile.read(length) or b"{}")
                    with outer._lock:
                        index = len(outer.requests)
                        outer.requests.append(body)
                    chunks = outer.turns[index] if index < len(outer.turns) else outer.fallback
                    chunks = [dict(chunk) for chunk in chunks]
                    if outer.usage_only_when_requested is not None:
                        include_usage = bool(
                            (body.get("stream_options") or {}).get("include_usage")
                        )
                        if include_usage:
                            chunks.append(
                                {
                                    "id": "chatcmpl-local",
                                    "object": "chat.completion.chunk",
                                    "created": 1,
                                    "model": "deepseek-flash",
                                    "choices": [],
                                    "usage": outer.usage_only_when_requested,
                                }
                            )
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for chunk in chunks:
                        self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                        self.wfile.flush()
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except Exception as exc:  # pragma: no cover - 通过断言暴露
                    outer.errors.append(f"{type(exc).__name__}: {exc}")
                    try:
                        self.send_response(500)
                        self.end_headers()
                    except Exception:
                        pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self):
        self._thread.start()
        _wait_until_serving("127.0.0.1", self.port)
        return self

    def __exit__(self, *exc_info):
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(2)
        return False


def _point_sdk_at_stub(monkeypatch, port: int) -> None:
    from agent.openai_agents.model import reset_deepseek_sdk_cache

    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-local-test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", f"http://127.0.0.1:{port}")
    # 本地回环请求不能走代理，否则 SDK 连不到桩服务器。
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    for var in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(var, raising=False)
    reset_deepseek_sdk_cache()


def _install_tool_spy(monkeypatch, tool_name: str, calls: list) -> None:
    from agent.tools import registry

    async def spy(args):
        calls.append(args)
        return {
            "content": [
                {"type": "text", "text": json.dumps({"status": "success", "id": "f-spy"})}
            ]
        }

    monkeypatch.setitem(registry.TOOL_FUNCTIONS, tool_name, spy)


async def _run_agent(agent_type: str = "writer"):
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    state = {"user_message": "帮我写点东西", "messages": [], "system_prompt": "base"}
    events = [
        event
        async for event in run_openai_agents_streaming_agent(
            state=state, agent_type=agent_type, system_prompt="你是测试助手"
        )
    ]
    return state, events


def _event_names(events):
    return [getattr(event.type, "value", str(event.type)) for event in events]


def _joined_text(events):
    return "".join(
        event.data.get("text", "")
        for event in events
        if getattr(event.type, "value", "") == "text"
    )


# ---------------------------------------------------------------------------
# #9 控制流工具必须立刻停下整个 SDK run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool_name", "arguments", "stopped_event"),
    [
        (
            "request_clarification",
            {"question": "你想写第几章？"},
            "workflow_stopped",
        ),
        (
            "handoff_to_agent",
            {"target_agent": "quality_reviewer", "reason": "初稿完成", "context": "请审稿"},
            "handoff",
        ),
    ],
)
async def test_control_flow_tool_stops_run_before_next_model_turn(
    monkeypatch, tool_name, arguments, stopped_event
):
    """控制流工具返回后，SDK 不得再发起下一次模型调用、更不得执行那一轮的工具。

    修复前：cancel(mode="after_turn") 异步落地，模型会多跑一整轮，
    EXTRA_TURN_TEXT 被推给前端，create_file 被真实执行。
    """
    create_calls: list = []
    stub = _ChatCompletionsStub(
        turns=[
            _tool_turn("call-control", tool_name, arguments),
            _tool_turn(
                "call-extra",
                "create_file",
                {"file_type": "draft", "title": "暂停后偷偷创建的文件"},
                prefix_text="EXTRA_TURN_TEXT",
            ),
        ],
        fallback=_text_turn("THIRD_TURN_TEXT"),
    )
    with stub:
        _point_sdk_at_stub(monkeypatch, stub.port)
        _install_tool_spy(monkeypatch, "create_file", create_calls)
        state, events = await _run_agent()

    assert stub.errors == []
    assert len(stub.requests) == 1, "控制流工具之后不允许再发起模型调用"
    assert create_calls == [], "暂停/交接之后不允许再真实执行任何工具"

    names = _event_names(events)
    assert names == [
        "message_start",
        "tool_use",
        "tool_result",
        stopped_event,
        "message_end",
    ]
    assert "EXTRA_TURN_TEXT" not in _joined_text(events)

    serialized_state = json.dumps(state["messages"], ensure_ascii=False)
    assert "EXTRA_TURN_TEXT" not in serialized_state
    assert "暂停后偷偷创建的文件" not in serialized_state


@pytest.mark.asyncio
@pytest.mark.unit
async def test_control_flow_tool_error_keeps_run_alive(monkeypatch):
    """控制流工具报错时不能截断 run——模型必须能看到错误并在同一个 run 内纠正。

    这是对「按工具名一刀切（StopAtTools）」的防回归锁：request_clarification
    缺少 question 会返回 error，此时不算「工作流要停下」。
    """
    stub = _ChatCompletionsStub(
        turns=[
            _tool_turn("call-bad", "request_clarification", {"question": ""}),
            _text_turn("RECOVERED_TEXT"),
        ],
        fallback=_text_turn("UNEXPECTED_EXTRA"),
    )
    with stub:
        _point_sdk_at_stub(monkeypatch, stub.port)
        _, events = await _run_agent()

    assert stub.errors == []
    assert len(stub.requests) == 2, "工具报错后模型应当还能继续本次 run"
    assert "RECOVERED_TEXT" in _joined_text(events)
    assert "workflow_stopped" not in _event_names(events)


@pytest.mark.unit
def test_stop_callback_only_fires_on_effective_control_flow_result():
    """tool_use_behavior 回调的判定矩阵（成功控制流 / 报错控制流 / 普通工具）。"""
    from agent.openai_agents.runner import _stop_run_on_control_flow_tool

    def _result(name, payload):
        return SimpleNamespace(
            tool=SimpleNamespace(name=name),
            output=json.dumps(payload, ensure_ascii=False),
        )

    handoff_ok = _stop_run_on_control_flow_tool(
        None, [_result("handoff_to_agent", {"status": "handoff", "target_agent": "writer"})]
    )
    clarify_ok = _stop_run_on_control_flow_tool(
        None, [_result("request_clarification", {"status": "clarification_needed", "question": "?"})]
    )
    handoff_error = _stop_run_on_control_flow_tool(
        None, [_result("handoff_to_agent", {"status": "error", "error": "Invalid target_agent"})]
    )
    normal_tool = _stop_run_on_control_flow_tool(
        None, [_result("create_file", {"status": "success"})]
    )
    non_json = _stop_run_on_control_flow_tool(
        None, [SimpleNamespace(tool=SimpleNamespace(name="handoff_to_agent"), output="not json")]
    )

    assert handoff_ok.is_final_output is True
    assert clarify_ok.is_final_output is True
    assert handoff_error.is_final_output is False
    assert normal_tool.is_final_output is False
    assert non_json.is_final_output is False
    assert _stop_run_on_control_flow_tool(None, []).is_final_output is False


@pytest.mark.unit
def test_build_agent_wires_control_flow_stop_and_include_usage(monkeypatch):
    """Agent 构造必须同时带上「控制流截断」和「回传 usage」两项设置。"""
    from agent.openai_agents.runner import _build_agent, _stop_run_on_control_flow_tool

    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-local-test-key")

    from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker

    breaker = ToolFailureBreaker()
    agent = _build_agent("writer", "system", failure_breaker=breaker)

    # tool_use_behavior 绑定了本次 run 的工具失败熔断器（同一个出口截断 run）
    assert agent.tool_use_behavior.func is _stop_run_on_control_flow_tool
    assert agent.tool_use_behavior.keywords == {"failure_breaker": breaker, "run_meter": None}
    assert agent.model_settings.include_usage is True


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_truncates_extra_turn_if_sdk_keeps_running():
    """兜底护栏：SDK 侧截断失效时，runner 自己截断消费循环。

    这里用一个「不理会 cancel、继续吐下一轮事件」的假 result 模拟 SDK 截断失效，
    断言暂停之后的正文与工具调用既不进 SSE 流，也不写回 state.messages。
    """
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    clarification = json.dumps(
        {"status": "clarification_needed", "question": "你想写第几章？"}, ensure_ascii=False
    )

    class StubbornResult:
        raw_responses = []

        def __init__(self):
            self.cancel_modes: list[str] = []

        def cancel(self, mode="immediate"):
            self.cancel_modes.append(mode)

        async def stream_events(self):
            yield SimpleNamespace(
                type="run_item_stream_event",
                name="tool_called",
                item=SimpleNamespace(
                    raw_item={
                        "name": "request_clarification",
                        "call_id": "call-1",
                        "arguments": json.dumps({"question": "你想写第几章？"}, ensure_ascii=False),
                    }
                ),
            )
            yield SimpleNamespace(
                type="run_item_stream_event",
                name="tool_output",
                item=SimpleNamespace(raw_item={"call_id": "call-1"}, output=clarification),
            )
            # 以下都属于「暂停之后不该存在」的下一轮
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta="EXTRA_TURN_TEXT"),
            )
            yield SimpleNamespace(
                type="run_item_stream_event",
                name="tool_called",
                item=SimpleNamespace(
                    raw_item={
                        "name": "create_file",
                        "call_id": "call-2",
                        "arguments": json.dumps({"title": "偷偷创建"}, ensure_ascii=False),
                    }
                ),
            )

    fake_result = StubbornResult()
    state = {"user_message": "写一章", "messages": [], "system_prompt": "base"}

    with (
        patch("agent.openai_agents.runner._build_agent", return_value=object()),
        patch("agents.Runner.run_streamed", return_value=fake_result),
    ):
        events = [
            event
            async for event in run_openai_agents_streaming_agent(
                state=state, agent_type="writer", system_prompt="system"
            )
        ]

    assert _event_names(events) == [
        "message_start",
        "tool_use",
        "tool_result",
        "workflow_stopped",
        "message_end",
    ]
    assert "EXTRA_TURN_TEXT" not in _joined_text(events)
    assert "immediate" in fake_result.cancel_modes, "截断失效时必须硬取消 SDK run"
    serialized_state = json.dumps(state["messages"], ensure_ascii=False)
    assert "EXTRA_TURN_TEXT" not in serialized_state
    assert "create_file" not in serialized_state


# ---------------------------------------------------------------------------
# #30 stream_options.include_usage 与 usage 解析
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.unit
async def test_request_carries_stream_options_include_usage(monkeypatch):
    """请求体必须带 stream_options.include_usage，否则 DeepSeek 不回发 usage。

    桩服务器模拟 DeepSeek 官方行为：只有 include_usage=true 才补发带 usage 的 chunk。
    """
    from agent.core.workflow_events import StreamEventType

    stub = _ChatCompletionsStub(
        turns=[_text_turn("本地smoke")],
        fallback=_text_turn("UNEXPECTED_EXTRA"),
        usage_only_when_requested={
            "prompt_tokens": 120,
            "completion_tokens": 30,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 100},
        },
    )
    with stub:
        _point_sdk_at_stub(monkeypatch, stub.port)
        _, events = await _run_agent()

    assert stub.errors == []
    assert stub.requests, "本地 OpenAI 兼容端点未被调用"
    assert stub.requests[0].get("stream_options") == {"include_usage": True}

    message_end = [event for event in events if event.type == StreamEventType.MESSAGE_END]
    assert len(message_end) == 1
    usage = message_end[0].data["usage"]
    assert usage, "MESSAGE_END 必须带上真实 usage"
    # 缓存命中部分从 input 里拆出来，避免按输入价 + 缓存价重复计费
    assert usage["input_tokens"] == 20
    assert usage["cache_read_tokens"] == 100
    assert usage["output_tokens"] == 30
    assert usage["total_tokens"] == 150


@pytest.mark.unit
def test_usage_dict_accumulates_and_splits_cached_tokens():
    """多次模型响应的 usage 累加；cached_tokens 拆成 cache_read_tokens。"""
    from agent.openai_agents.runner import _usage_dict_from_result

    def _response(input_tokens, output_tokens, total_tokens, cached):
        return SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                input_tokens_details=SimpleNamespace(cached_tokens=cached),
            )
        )

    result = SimpleNamespace(
        raw_responses=[
            _response(100, 10, 110, 64),
            _response(50, 5, 55, 0),
        ]
    )

    assert _usage_dict_from_result(result) == {
        "input_tokens": 86,  # (100-64) + 50
        "output_tokens": 15,
        "total_tokens": 165,
        "cache_read_tokens": 64,
    }


@pytest.mark.unit
def test_usage_dict_is_empty_without_usage_payload():
    """没有 usage 时仍返回空 dict —— 下游用非空判断是否拿到真实用量。"""
    from agent.openai_agents.runner import _usage_dict_from_result

    assert _usage_dict_from_result(SimpleNamespace(raw_responses=[])) == {}
    assert _usage_dict_from_result(SimpleNamespace(raw_responses=[SimpleNamespace(usage=None)])) == {}
    assert _usage_dict_from_result(SimpleNamespace()) == {}
    # cached_tokens 大于 input_tokens 属于异常上游数据，不得让 input 变成负数
    weird = SimpleNamespace(
        raw_responses=[
            SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=10,
                    output_tokens=1,
                    total_tokens=11,
                    input_tokens_details=SimpleNamespace(cached_tokens=999),
                )
            )
        ]
    )
    # 归零后的 input_tokens 按既有契约不落键，但绝不能是负数
    assert _usage_dict_from_result(weird) == {
        "output_tokens": 1,
        "total_tokens": 11,
        "cache_read_tokens": 10,
    }
