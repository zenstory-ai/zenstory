"""Tests for the openai-agents-python writing-agent adapter."""

import asyncio
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


@pytest.mark.unit
def test_deepseek_client_requires_deepseek_api_key(monkeypatch):
    from agent.openai_agents.model import get_deepseek_client

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(ValueError, match="DEEPSEEK_API_KEY"):
        get_deepseek_client()


@pytest.mark.unit
def test_build_agent_function_tools_uses_registry_names():
    from agent.openai_agents.tools_adapter import build_agent_function_tools
    from agent.tools.registry import get_agent_tools

    tools = build_agent_function_tools("quality_reviewer")

    assert [tool.name for tool in tools] == [tool["name"] for tool in get_agent_tools("quality_reviewer")]
    assert all(tool.strict_json_schema is False for tool in tools)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_invoke_project_tool_returns_mcp_text_and_metrics():
    from agent.core.metrics import TOOL_CALLS_ERRORS, TOOL_CALLS_TOTAL, get_metrics_collector
    from agent.openai_agents.tools_adapter import invoke_project_tool

    async def fake_tool(args):
        return {"content": [{"type": "text", "text": json.dumps({"status": "success", "args": args})}]}

    with patch.dict("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"demo_tool": fake_tool}, clear=True):
        result_text = await invoke_project_tool("demo_tool", '{"x": 1}')

    assert json.loads(result_text) == {"status": "success", "args": {"x": 1}}
    metrics = get_metrics_collector().get_all_metrics()
    assert metrics["counters"][TOOL_CALLS_TOTAL]["value"] == 1
    assert TOOL_CALLS_ERRORS not in metrics["counters"]


@pytest.mark.unit
def test_normalize_messages_for_openai_agents_omits_thinking_blocks():
    from agent.openai_agents.runner import normalize_messages_for_openai_agents

    messages = normalize_messages_for_openai_agents(
        [
            {"role": "user", "content": "写一章"},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "internal"},
                    {"type": "text", "text": "正文"},
                ],
                "usage": {"input_tokens": 1},
            },
            {"role": "tool", "content": "ignored"},
        ]
    )

    assert messages == [
        {"role": "user", "content": "写一章"},
        {"role": "assistant", "content": "正文"},
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_maps_sdk_text_tool_handoff_and_message_end():
    from agent.core.workflow_events import StreamEventType
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    result_text = json.dumps(
        {
            "status": "handoff",
            "target_agent": "quality_reviewer",
            "reason": "请审查",
            "context": "已完成初稿",
            "completed": ["初稿"],
        },
        ensure_ascii=False,
    )

    class FakeResult:
        raw_responses = []

        def __init__(self):
            self.cancel_mode = None

        def cancel(self, mode="immediate"):
            self.cancel_mode = mode

        async def stream_events(self):
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta="正文"),
            )
            yield SimpleNamespace(
                type="run_item_stream_event",
                name="tool_called",
                item=SimpleNamespace(
                    raw_item={
                        "name": "handoff_to_agent",
                        "call_id": "call-1",
                        "arguments": json.dumps({"target_agent": "quality_reviewer"}, ensure_ascii=False),
                    }
                ),
            )
            yield SimpleNamespace(
                type="run_item_stream_event",
                name="tool_output",
                item=SimpleNamespace(
                    raw_item={"call_id": "call-1"},
                    output=result_text,
                ),
            )

    fake_result = FakeResult()
    state = {"user_message": "写一章", "messages": [], "system_prompt": "base"}

    with (
        patch("agent.openai_agents.runner._build_agent", return_value=object()),
        patch("agents.Runner.run_streamed", return_value=fake_result) as mock_run,
    ):
        events = [
            event async for event in run_openai_agents_streaming_agent(
                state=state,
                agent_type="writer",
                system_prompt="system",
            )
        ]

    assert mock_run.call_args.kwargs["max_turns"] > 0
    assert fake_result.cancel_mode == "after_turn"
    assert [event.type for event in events] == [
        StreamEventType.MESSAGE_START,
        StreamEventType.TEXT,
        StreamEventType.TOOL_USE,
        StreamEventType.TOOL_RESULT,
        StreamEventType.HANDOFF,
        StreamEventType.MESSAGE_END,
    ]
    assert events[1].data["text"] == "正文"
    assert events[2].data["name"] == "handoff_to_agent"
    assert events[4].data["handoff_packet"]["completed"] == ["初稿"]
    assert state["messages"][-2]["role"] == "assistant"
    assert state["messages"][-2]["content"][0]["type"] == "text"
    assert state["messages"][-2]["content"][0]["text"] == "正文"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_consumes_steering_at_tool_output_boundary():
    """SDK run 进行中无法注入消息，工具输出边界必须消费 steering：
    向前端发确认事件，并把内容作为用户消息写回 state 供下一次迭代使用。"""
    from agent.core.workflow_events import StreamEventType
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    class FakeResult:
        raw_responses = []

        async def stream_events(self):
            yield SimpleNamespace(
                type="run_item_stream_event",
                name="tool_called",
                item=SimpleNamespace(
                    raw_item={
                        "name": "query_files",
                        "call_id": "call-1",
                        "arguments": json.dumps({"query": "第一章"}, ensure_ascii=False),
                    }
                ),
            )
            yield SimpleNamespace(
                type="run_item_stream_event",
                name="tool_output",
                item=SimpleNamespace(
                    raw_item={"call_id": "call-1"},
                    output=json.dumps({"status": "success", "data": []}, ensure_ascii=False),
                ),
            )
            yield SimpleNamespace(
                type="raw_response_event",
                data=SimpleNamespace(type="response.output_text.delta", delta="正文"),
            )

    steering_calls = {"n": 0}

    async def fake_get_steering_messages():
        steering_calls["n"] += 1
        # 第 1 次：run 开始前的初始注入（队列为空）；
        # 第 2 次：工具输出边界（用户在生成期间发来了引导）。
        if steering_calls["n"] == 2:
            return [{"id": "steer-1", "content": "改成第一人称"}]
        return []

    state = {"user_message": "写一章", "messages": [], "system_prompt": "base"}

    with (
        patch("agent.openai_agents.runner._build_agent", return_value=object()),
        patch("agents.Runner.run_streamed", return_value=FakeResult()),
    ):
        events = [
            event
            async for event in run_openai_agents_streaming_agent(
                state=state,
                agent_type="writer",
                system_prompt="system",
                get_steering_messages=fake_get_steering_messages,
            )
        ]

    assert steering_calls["n"] >= 2, "工具输出边界必须轮询 steering 队列"

    type_values = [getattr(event.type, "value", "") for event in events]
    message_start_idx = type_values.index(StreamEventType.MESSAGE_START.value)
    steering_indexes = [
        idx for idx, value in enumerate(type_values) if value == "steering_received"
    ]
    assert steering_indexes, "消费到的 steering 必须向前端发确认事件"
    assert all(idx > message_start_idx for idx in steering_indexes)
    assert events[steering_indexes[0]].data["preview"] == "改成第一人称"

    # run 结束后引导内容进入会话消息，供 graph 的下一次迭代注入模型
    assert state["messages"][-1] == {"role": "user", "content": "改成第一人称"}


def _wait_until_serving(host: str, port: int, timeout: float = 5.0) -> None:
    """Block until the local server accepts a TCP connection (or timeout)."""
    deadline = time.monotonic() + timeout
    last_err: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError as exc:  # not listening yet
            last_err = exc
            time.sleep(0.02)
    raise AssertionError(f"local test server never started listening on {host}:{port}: {last_err}")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_runner_streams_through_openai_compatible_http_endpoint(monkeypatch):
    """Exercise SDK + AsyncOpenAI + SSE wiring without calling an external LLM."""
    from agent.core.workflow_events import StreamEventType
    from agent.openai_agents.model import reset_deepseek_sdk_cache
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    requests: list[dict] = []
    server_errors: list[str] = []

    class LocalChatCompletionsHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format, *args):  # noqa: A002
            return

        def do_POST(self):
            try:
                request_length = int(self.headers.get("content-length") or "0")
                body = json.loads(self.rfile.read(request_length) or b"{}")
                requests.append(body)

                if self.path != "/chat/completions":
                    raise AssertionError(f"unexpected path: {self.path}")
                if body.get("model") != "deepseek-flash":
                    raise AssertionError(f"unexpected model: {body.get('model')}")
                if body.get("stream") is not True:
                    raise AssertionError("expected streaming request")

                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()

                chunks = [
                    {
                        "id": "chatcmpl-local",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "deepseek-flash",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": ""},
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": "chatcmpl-local",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "deepseek-flash",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "本地"},
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": "chatcmpl-local",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "deepseek-flash",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": "smoke"},
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        "id": "chatcmpl-local",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "deepseek-flash",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 3,
                            "completion_tokens": 2,
                            "total_tokens": 5,
                        },
                    },
                ]
                for chunk in chunks:
                    self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except Exception as exc:  # pragma: no cover - surfaced via assertion below
                server_errors.append(f"{type(exc).__name__}: {exc}")
                self.send_response(500)
                self.end_headers()

    # Bind directly to an OS-assigned port instead of probing a free port and
    # re-binding it — the probe/rebind gap is a TOCTOU race where another process
    # can grab the port in between, a plausible source of CI flakiness ("endpoint
    # was not called"). Then wait until the accept loop is actually serving before
    # the SDK connects.
    server = ThreadingHTTPServer(("127.0.0.1", 0), LocalChatCompletionsHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_until_serving("127.0.0.1", port)

    monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy-local-test-key")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", f"http://127.0.0.1:{port}")
    # The OpenAI SDK's httpx client trusts proxy env vars. If CI has an HTTP(S)
    # proxy configured, the loopback request to our local test server would be
    # routed through it and never arrive ("endpoint was not called"), even though
    # the server is up. Exempt loopback so the SDK connects directly.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.delenv("ALL_PROXY", raising=False)
    monkeypatch.delenv("all_proxy", raising=False)
    reset_deepseek_sdk_cache()

    state = {"user_message": "请输出 smoke", "messages": [], "system_prompt": "base"}
    try:
        events = [
            event async for event in run_openai_agents_streaming_agent(
                state=state,
                agent_type="writer",
                system_prompt="你是测试助手",
            )
        ]
    finally:
        server.shutdown()
        server.server_close()
        await asyncio.to_thread(thread.join, 1)
        reset_deepseek_sdk_cache()

    assert server_errors == []
    assert requests, (
        "local OpenAI-compatible endpoint was not called. "
        f"event_types={[getattr(e, 'type', None) for e in events]}; "
        f"event_data={[getattr(e, 'data', None) for e in events][:6]}"
    )
    assert requests[0]["model"] == "deepseek-flash"
    assert [event.type for event in events] == [
        StreamEventType.MESSAGE_START,
        StreamEventType.TEXT,
        StreamEventType.TEXT,
        StreamEventType.MESSAGE_END,
    ]
    assert events[0].data == {"model": "deepseek-flash", "agent_type": "writer"}
    assert "".join(event.data.get("text", "") for event in events) == "本地smoke"
    assert state["messages"][-1]["content"][0]["text"] == "本地smoke"


@pytest.mark.unit
def test_normalize_omits_tool_use_and_tool_result_blocks():
    """Replayed history must not leak raw tool JSON as prose/user messages (review #1)."""
    from agent.openai_agents.runner import normalize_messages_for_openai_agents

    messages = normalize_messages_for_openai_agents(
        [
            {"role": "user", "content": "写第一章"},
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "我来创建章节文件。"},
                    {
                        "type": "tool_use",
                        "id": "call_1",
                        "name": "create_file",
                        "input": {"title": "第一章", "content": "secret"},
                    },
                ],
            },
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": '{"id":"f1"}'},
                ],
            },
        ]
    )

    # Only the user request and the assistant's prose survive; tool calls/results are dropped.
    assert messages == [
        {"role": "user", "content": "写第一章"},
        {"role": "assistant", "content": "我来创建章节文件。"},
    ]
    blob = str(messages)
    assert "create_file" not in blob and "secret" not in blob
    assert "工具调用" not in blob and "工具结果" not in blob


def _called(name, call_id):
    return SimpleNamespace(
        type="run_item_stream_event",
        name="tool_called",
        item=SimpleNamespace(
            raw_item={"name": name, "call_id": call_id, "arguments": "{}"}
        ),
    )


def _output(call_id, text="ok"):
    return SimpleNamespace(
        type="run_item_stream_event",
        name="tool_output",
        item=SimpleNamespace(raw_item={"call_id": call_id}, output=text),
    )


async def test_runner_wires_intra_run_trimmer_into_run_config():
    """The intra-run tool-output trimmer is attached as the model-input filter."""
    from agent.openai_agents.intra_run_trimmer import IntraRunToolOutputTrimmer
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    class FakeResult:
        raw_responses = []

        def cancel(self, mode="immediate"):
            pass

        async def stream_events(self):
            if False:  # empty stream
                yield

    state = {"user_message": "hi", "messages": [], "system_prompt": "base"}
    with (
        patch("agent.openai_agents.runner._build_agent", return_value=object()),
        patch("agents.Runner.run_streamed", return_value=FakeResult()) as mock_run,
    ):
        _ = [
            event
            async for event in run_openai_agents_streaming_agent(
                state=state, agent_type="writer", system_prompt="system"
            )
        ]

    run_config = mock_run.call_args.kwargs["run_config"]
    # 外层是请求级模型调用计数器，内层仍是 IntraRunToolOutputTrimmer。
    assert isinstance(run_config.call_model_input_filter._inner, IntraRunToolOutputTrimmer)
    assert run_config.tool_execution.max_function_tool_concurrency == 1


async def test_readonly_cocall_metric_counts_each_turn_once():
    """Regression lock: the read-only co-call metric counts each turn once, not per output."""
    from agent.core.metrics import (
        TOOL_READONLY_COCALL_TOTAL,
        TOOL_READONLY_TURNS_TOTAL,
        get_metrics_collector,
    )
    from agent.openai_agents.runner import run_openai_agents_streaming_agent

    class FakeResult:
        raw_responses = []

        def cancel(self, mode="immediate"):
            pass

        async def stream_events(self):
            # Turn 1: two read-only calls then their two outputs -> ONE turn, ONE co-call.
            yield _called("hybrid_search", "c1")
            yield _called("query_files", "c2")
            yield _output("c1")
            yield _output("c2")
            # Turn 2: a single non-read-only call -> ONE turn, NO co-call.
            yield _called("create_file", "c3")
            yield _output("c3")

    state = {"user_message": "x", "messages": [], "system_prompt": "base"}
    with (
        patch("agent.openai_agents.runner._build_agent", return_value=object()),
        patch("agents.Runner.run_streamed", return_value=FakeResult()),
    ):
        _ = [
            event
            async for event in run_openai_agents_streaming_agent(
                state=state, agent_type="writer", system_prompt="system"
            )
        ]

    counters = get_metrics_collector().get_all_metrics()["counters"]
    # Pre-fix bug: TURNS counted every tool_output (would be 3). Correct is 2 turns.
    assert counters[TOOL_READONLY_TURNS_TOTAL]["value"] == 2
    assert counters[TOOL_READONLY_COCALL_TOTAL]["value"] == 1
