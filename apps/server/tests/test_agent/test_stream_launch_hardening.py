"""上线前加固：SSE 心跳 / 请求级时限、计费判定、错误脱敏、模型调用预算、中止时补存正文、会话心跳。"""

from __future__ import annotations

import asyncio
import contextvars
import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from agent.core.run_meter import AgentRunMeter
from agent.core.sse_pump import HEARTBEAT_FRAME, SSEStreamPump, StreamDeadlineExceeded
from agent.core.stream_billing import StreamBillingTracker
from agent.core.stream_errors import (
    classify_stream_exception,
    stream_error_from_event_data,
)
from agent.core.workflow_events import StreamEvent, StreamEventType
from core.error_handler import APIException


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ------------------------------------------------------------------ SSE 心跳 / 时限


async def test_pump_emits_heartbeat_comment_frames_while_source_is_idle():
    async def slow_source():
        yield _sse("thinking", {"message": "正在思考"})
        await asyncio.sleep(0.25)
        yield _sse("done", {})

    frames = [frame async for frame in SSEStreamPump(slow_source(), heartbeat_interval_s=0.05)]

    assert frames[0].startswith("event: thinking")
    assert frames[-1].startswith("event: done")
    heartbeats = [frame for frame in frames if frame == HEARTBEAT_FRAME]
    assert len(heartbeats) >= 2
    # 心跳是 SSE 注释帧：以冒号开头、没有 event/data 行。
    assert HEARTBEAT_FRAME.startswith(":") and "data:" not in HEARTBEAT_FRAME


async def test_pump_deadline_cancels_source_and_raises():
    source_cancelled = asyncio.Event()

    async def endless_source():
        yield _sse("content", {"text": "第一段"})
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            source_cancelled.set()
            raise
        yield _sse("done", {})

    frames = []
    with pytest.raises(StreamDeadlineExceeded):
        async for frame in SSEStreamPump(
            endless_source(), heartbeat_interval_s=0.05, deadline_s=0.2
        ):
            frames.append(frame)

    assert frames[0].startswith("event: content")
    assert source_cancelled.is_set()


async def test_pump_runs_whole_source_in_one_context():
    """contextvars 在生成器各步之间保持一致（ToolContext 依赖这一点）。"""
    marker: contextvars.ContextVar[str | None] = contextvars.ContextVar("marker", default=None)

    async def source():
        marker.set("set-in-step-1")
        yield "a"
        await asyncio.sleep(0.01)
        yield marker.get() or "lost"

    frames = [f async for f in SSEStreamPump(source(), heartbeat_interval_s=1)]
    assert frames == ["a", "set-in-step-1"]


async def test_pump_propagates_source_exception():
    async def failing():
        yield "a"
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        async for _ in SSEStreamPump(failing(), heartbeat_interval_s=1):
            pass


# ------------------------------------------------------------------ 计费判定


def test_billing_refunds_internal_error_without_output():
    tracker = StreamBillingTracker()
    tracker.observe(_sse("session_started", {"session_id": "s"}))
    tracker.observe(HEARTBEAT_FRAME)
    tracker.observe(_sse("error", {"message": "x", "code": "ERR_AGENT_RUN_FAILED", "refundable": True}))

    assert tracker.decide(user_cancelled=False, unexpected_exception=False) == ("internal_error", True)


def test_billing_charges_tool_failure_circuit_even_without_output():
    tracker = StreamBillingTracker()
    tracker.observe(
        _sse("error", {"message": "熔断", "code": "ERR_AGENT_TOOL_FAILURE_LIMIT", "refundable": False})
    )

    assert tracker.decide(user_cancelled=False, unexpected_exception=False) == (
        "non_refundable_error",
        False,
    )


@pytest.mark.parametrize(
    "output_frame",
    [
        _sse("content", {"text": "第一章正文……"}),
        _sse("file_content", {"file_id": "f", "chunk": "山风吹过断崖。"}),
        _sse("tool_result", {"tool_name": "edit_file", "status": "success", "data": {}}),
    ],
)
def test_billing_charges_errors_after_substantive_output(output_frame):
    tracker = StreamBillingTracker()
    tracker.observe(output_frame)
    tracker.observe(_sse("error", {"message": "x", "code": "ERR_AGENT_UPSTREAM_UNAVAILABLE", "refundable": True}))

    assert tracker.decide(user_cancelled=False, unexpected_exception=False) == (
        "error_after_output",
        False,
    )


def test_billing_does_not_count_failed_writes_or_whitespace_as_output():
    tracker = StreamBillingTracker()
    tracker.observe(_sse("content", {"text": "  \n"}))
    tracker.observe(_sse("tool_result", {"tool_name": "edit_file", "status": "error"}))
    tracker.observe(_sse("tool_result", {"tool_name": "query_files", "status": "success"}))
    tracker.observe(_sse("error", {"message": "x", "code": "ERR_AGENT_RUN_FAILED"}))

    assert tracker.decide(user_cancelled=False, unexpected_exception=False)[1] is True


def test_billing_user_cancel_and_deadline():
    tracker = StreamBillingTracker()
    assert tracker.decide(user_cancelled=True, unexpected_exception=False) == ("user_cancelled", False)
    assert tracker.decide(
        user_cancelled=False, unexpected_exception=False, deadline_exceeded=True
    ) == ("run_deadline_exceeded", True)
    tracker.observe(_sse("content", {"text": "已经写了一段"}))
    assert tracker.decide(
        user_cancelled=False, unexpected_exception=False, deadline_exceeded=True
    ) == ("run_deadline_exceeded", False)


def test_billing_completed_run_is_charged():
    tracker = StreamBillingTracker()
    tracker.observe(_sse("content", {"text": "好的"}))
    tracker.observe(_sse("done", {}))
    assert tracker.decide(user_cancelled=False, unexpected_exception=False) == ("completed", False)


# ------------------------------------------------------------------ 错误脱敏 / 分类


def _api_status_error(cls, status: int, message: str):
    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(status, request=request)
    return cls(message, response=response, body=None)


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (_api_status_error(openai.RateLimitError, 429, "Error code: 429 - busy"), "ERR_AGENT_UPSTREAM_RATE_LIMITED", True),
        (_api_status_error(openai.InternalServerError, 503, "Error code: 503"), "ERR_AGENT_UPSTREAM_UNAVAILABLE", True),
        (openai.APITimeoutError(request=httpx.Request("POST", "https://x")), "ERR_AGENT_UPSTREAM_UNAVAILABLE", True),
        (
            _api_status_error(
                openai.BadRequestError, 400, "This model's maximum context length is 131072 tokens"
            ),
            "ERR_AGENT_CONTEXT_TOO_LONG",
            False,
        ),
        (RuntimeError("(psycopg.errors.UniqueViolation) [SQL: INSERT INTO file ...]"), "ERR_AGENT_RUN_FAILED", True),
        (
            APIException(
                error_code="ERR_SERVICE_UNAVAILABLE",
                status_code=503,
                detail={"message": "Writing configuration is unavailable."},
            ),
            "ERR_SERVICE_UNAVAILABLE",
            True,
        ),
        (APIException(error_code="ERR_PROJECT_NOT_FOUND", status_code=404), "ERR_PROJECT_NOT_FOUND", False),
    ],
)
def test_classify_stream_exception(exc, code, retryable):
    info = classify_stream_exception(exc)

    assert info.code == code
    assert info.retryable is retryable
    data = info.as_event_data(error_type=type(exc).__name__)
    # 发给前端的文案是固定的，不含原始异常（SQL、英文 SDK 报错）。
    assert str(exc) not in data["error"]
    assert "SQL" not in data["error"]


def test_tool_failure_event_data_is_non_refundable():
    from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker

    breaker = ToolFailureBreaker()
    failure = json.dumps({"status": "error", "error": "database is locked", "error_type": "db"})
    for _ in range(3):
        breaker.observe("edit_file", '{"id": "f1"}', failure)
    assert breaker.trip is not None

    info = stream_error_from_event_data(breaker.trip.as_event_data("writer"))
    assert info.code == "ERR_AGENT_TOOL_FAILURE_LIMIT"
    assert info.refundable is False
    assert info.retryable is False


async def test_stream_adapter_error_frame_is_sanitized_and_carries_flags():
    from agent.stream_adapter import StreamAdapter

    adapter = StreamAdapter()

    async def events():
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data={"error": "Error code: 503 - upstream", "code": "ERR_AGENT_UPSTREAM_UNAVAILABLE",
                  "retryable": True, "refundable": True},
        )

    frames = [frame async for frame in adapter.process_workflow_events(events())]
    error = frames[-1]
    assert error.type.value == "error"
    assert error.data["code"] == "ERR_AGENT_UPSTREAM_UNAVAILABLE"
    assert error.data["retryable"] is True
    assert error.data["refundable"] is True
    assert "503" not in error.data["message"]


async def test_runner_exception_is_sanitized(monkeypatch):
    """runner 捕获的上游异常只以错误码与固定文案发出。"""
    from agent.openai_agents import runner

    def boom(*_args, **_kwargs):
        raise _api_status_error(openai.RateLimitError, 429, "Error code: 429 - {'error': 'quota'}")

    monkeypatch.setattr(runner, "_build_agent", boom)
    events = [
        event
        async for event in runner.run_openai_agents_streaming_agent(
            {"messages": [], "user_message": "写"}, "writer", "system"
        )
    ]
    error = [event for event in events if event.type == StreamEventType.ERROR][-1]
    assert error.data["code"] == "ERR_AGENT_UPSTREAM_RATE_LIMITED"
    assert error.data["retryable"] is True
    assert "quota" not in error.data["error"]


# ------------------------------------------------------------------ 模型调用预算


def test_tool_use_behavior_stops_run_when_budget_exhausted():
    from agent.openai_agents.runner import _stop_run_on_control_flow_tool

    meter = AgentRunMeter(max_model_calls=2)
    meter.record_model_call()
    not_yet = _stop_run_on_control_flow_tool(None, [], run_meter=meter)
    assert not_yet.is_final_output is False

    meter.record_model_call()
    stopped = _stop_run_on_control_flow_tool(None, [], run_meter=meter)
    assert stopped.is_final_output is True
    assert meter.budget_stopped is True


def test_counting_filter_records_each_model_call():
    from agent.openai_agents.runner import _ModelCallCountingFilter

    meter = AgentRunMeter(max_model_calls=10)
    seen = []
    model_filter = _ModelCallCountingFilter(lambda data: seen.append(data) or data, meter)
    model_filter("call-1")
    model_filter("call-2")
    assert meter.model_calls == 2
    assert seen == ["call-1", "call-2"]


async def test_runner_refuses_to_start_when_budget_already_exhausted(monkeypatch):
    from agent.openai_agents import runner

    def must_not_build(*_args, **_kwargs):  # pragma: no cover - 断言不会被调用
        raise AssertionError("model must not be called once the budget is spent")

    monkeypatch.setattr(runner, "_build_agent", must_not_build)
    meter = AgentRunMeter(max_model_calls=3, model_calls=3)
    events = [
        event
        async for event in runner.run_openai_agents_streaming_agent(
            {"messages": [], "user_message": "写", "run_meter": meter}, "writer", "system"
        )
    ]
    assert [event.type for event in events] == [StreamEventType.ERROR]
    assert events[0].data["code"] == "ERR_AGENT_MODEL_CALL_LIMIT"


def test_runtime_budget_defaults_are_generous():
    from config import agent_runtime

    assert agent_runtime.AGENT_RUN_MAX_MODEL_CALLS >= 150
    assert agent_runtime.AGENT_RUN_WALL_CLOCK_TIMEOUT_S >= 15 * 60
    assert 5 <= agent_runtime.AGENT_SSE_HEARTBEAT_INTERVAL_S <= 30


# ------------------------------------------------------------------ 中止时补存 <file> 正文


def _adapter_with_capture(monkeypatch, saved: list[tuple[str, str]], *, original_length: int = 0):
    from agent.stream_adapter import StreamAdapter

    adapter = StreamAdapter()

    async def fake_save(file_id: str, content: str, *, record_mutation: bool = True) -> bool:
        saved.append((file_id, content))
        return True

    monkeypatch.setattr(adapter, "_save_file_content", fake_save)
    adapter.set_pending_file_write("file-5", "draft", "第5章", original_content_length=original_length)
    return adapter


async def _stream_then_hang(release: asyncio.Event):
    yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n山风吹过断崖，"})
    yield StreamEvent(type=StreamEventType.TEXT, data={"text": "少年握紧了剑。他知道，今夜之后再无退路。"})
    await release.wait()


async def test_cancelled_stream_persists_in_flight_file_body(monkeypatch):
    saved: list[tuple[str, str]] = []
    adapter = _adapter_with_capture(monkeypatch, saved)
    release = asyncio.Event()

    async def consume():
        async for _ in adapter.process_workflow_events(_stream_then_hang(release)):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)  # 后台落库任务

    assert saved, "取消时进行中的 <file> 正文必须落库"
    file_id, content = saved[0]
    assert file_id == "file-5"
    assert "少年握紧了剑" in content


async def test_cancelled_stream_never_overwrites_existing_file_body(monkeypatch):
    saved: list[tuple[str, str]] = []
    adapter = _adapter_with_capture(monkeypatch, saved, original_length=5000)
    release = asyncio.Event()

    async def consume():
        async for _ in adapter.process_workflow_events(_stream_then_hang(release)):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.05)

    assert saved == []


async def test_llm_error_mid_file_flushes_body_before_error_frame(monkeypatch):
    saved: list[tuple[str, str]] = []
    adapter = _adapter_with_capture(monkeypatch, saved)

    async def events():
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n第一段正文写到一半，"})
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第二段也写了一些。"})
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data=classify_stream_exception(TimeoutError()).as_event_data(error_type="TimeoutError"),
        )

    frames = [frame async for frame in adapter.process_workflow_events(events())]
    types = [frame.type.value for frame in frames]

    assert saved and "第二段也写了一些" in saved[0][1]
    assert "file_content_end" in types
    assert types.index("file_content_end") < types.index("error")
    error = frames[types.index("error")]
    assert error.data["code"] == "ERR_AGENT_UPSTREAM_UNAVAILABLE"
    assert "done" not in types


# ------------------------------------------------------------------ 会话持有心跳


async def test_memory_heartbeat_keeps_run_alive_and_zombie_expires(monkeypatch):
    import agent.core.steering as st

    monkeypatch.delenv("REDIS_URL", raising=False)
    st._redis_health_checked_at = 0.0
    st._redis_is_healthy = False
    session_id = "sess-heartbeat-ttl"
    await st.cleanup_steering_queue_async(session_id)
    try:
        await st.create_steering_queue_async(session_id, "u1", run_id="run-live", exclusive_run=True)
        entry = st._queue_manager._queues[session_id]

        # 心跳把持有续到当前时间：即便注册时间已经很老，也不会被判为僵尸。
        entry.active_runs["run-live"] -= st._RUN_HEARTBEAT_TTL_S + 30
        await st.heartbeat_steering_run_async(session_id, "run-live")
        with pytest.raises(st.SteeringSessionBusyError):
            await st.create_steering_queue_async(session_id, "u1", run_id="run-next", exclusive_run=True)

        # 进程被杀、心跳停止：超过 TTL（90 秒级，而不是 1 小时）后新 run 可以接管。
        entry.active_runs["run-live"] -= st._RUN_HEARTBEAT_TTL_S + 1
        await st.create_steering_queue_async(session_id, "u1", run_id="run-next", exclusive_run=True)
    finally:
        await st.cleanup_steering_queue_async(session_id)


def test_zombie_ttl_is_short_and_heartbeat_interval_fits_inside_it():
    import agent.core.steering as st

    assert 60 <= st._RUN_HEARTBEAT_TTL_S <= 120
    assert st._RUN_HEARTBEAT_INTERVAL_S * 3 <= st._RUN_HEARTBEAT_TTL_S


async def test_heartbeat_does_not_resurrect_released_run(monkeypatch):
    import agent.core.steering as st

    monkeypatch.delenv("REDIS_URL", raising=False)
    st._redis_health_checked_at = 0.0
    st._redis_is_healthy = False
    session_id = "sess-heartbeat-released"
    await st.create_steering_queue_async(session_id, "u1", run_id="run-a", exclusive_run=True)
    await st.cleanup_steering_queue_async(session_id, run_id="run-a")

    await st.heartbeat_steering_run_async(session_id, "run-a")

    assert session_id not in st._queue_manager._queues


def test_session_busy_error_code_registered():
    from core.error_codes import ERROR_MESSAGES, ErrorCode

    assert ErrorCode.SESSION_BUSY == "ERR_SESSION_BUSY"
    for lang in ("zh", "en"):
        assert ErrorCode.SESSION_BUSY in ERROR_MESSAGES[lang]


def test_run_meter_records_agent_runs():
    meter = AgentRunMeter(max_model_calls=5)
    meter.record_agent_run(120.5)
    meter.record_agent_run(80)
    assert meter.agent_runs == 2
    assert meter.llm_duration_ms == pytest.approx(200.5)
    assert SimpleNamespace(exhausted=meter.exhausted).exhausted is False
