"""一次 agent run 只想、没写就撞上输出上限：按可重试错误结束，不当成正常完成。

根因追查（2026-10-09，loop-rootcause.md）：openai-agents 的流式 handler 不处理
finish_reason，被 max_tokens 截断的响应与正常结束无从区分。一次 run 若只有思考、
没有正文、没有工具调用就撞上上限，graph 里 agent_content 为空，工作流静默结束，作者只
看到一大段思考，没有任何结果或提示。这里只补「状态正确」：发 ERROR（可重试），
是否退还额度交给 stream_billing 的既有规则。
"""

import json

import pytest

from agent.core.stream_billing import StreamBillingTracker
from agent.core.workflow_events import StreamEventType
from core.error_codes import ErrorCode, get_error_message
from tests.test_agent.test_tool_failure_breaker import (
    _chunk,
    _run_against_local_model,
    _tool_call_chunks,
)

THINKING = "Let me plan the edits. "


def _max_tokens() -> int:
    from agent.openai_agents import runner

    return runner.AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS


def _reasoning_chunks(
    completion_tokens: int,
    *,
    text: str = "",
    finish_reason: str = "length",
) -> list[dict]:
    chunks = [
        _chunk({"role": "assistant", "content": ""}),
        _chunk({"reasoning_content": THINKING}),
        _chunk({"reasoning_content": THINKING}),
    ]
    if text:
        chunks.append(_chunk({"content": text}))
    chunks.append(
        _chunk(
            {},
            finish_reason,
            {
                "prompt_tokens": 100,
                "completion_tokens": completion_tokens,
                "total_tokens": 100 + completion_tokens,
            },
        )
    )
    return chunks


async def _unused_edit(_args):  # pragma: no cover - 这些场景不调用 edit_file
    raise AssertionError("edit_file should not be called")


@pytest.fixture(autouse=True)
def _reset_metrics_and_model_cache():
    from agent.core.metrics import reset_metrics_collector
    from agent.openai_agents.model import reset_deepseek_sdk_cache

    reset_metrics_collector()
    reset_deepseek_sdk_cache()
    yield
    reset_deepseek_sdk_cache()
    reset_metrics_collector()


@pytest.fixture
def warnings(monkeypatch):
    from agent.openai_agents import runner

    captured: list[tuple[str, dict]] = []
    original = runner.log_with_context

    def spy(logger, level, message, **fields):
        if level == 30:
            captured.append((message, fields))
        return original(logger, level, message, **fields)

    monkeypatch.setattr(runner, "log_with_context", spy)
    return captured


def _types(events) -> list[StreamEventType]:
    return [event.type for event in events]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_reasoning_only_run_at_output_cap_ends_with_continue_error(monkeypatch, warnings):
    from agent.openai_agents.runner import OUTPUT_TRUNCATED_STOP_REASON

    events, requests, state = await _run_against_local_model(
        monkeypatch,
        lambda _index: _reasoning_chunks(_max_tokens()),
        _unused_edit,
    )

    assert len(requests) == 1
    types = _types(events)
    assert StreamEventType.THINKING in types
    assert StreamEventType.TEXT not in types
    assert types[-2:] == [StreamEventType.MESSAGE_END, StreamEventType.ERROR]

    message_end = events[-2].data
    assert message_end["stop_reason"] == OUTPUT_TRUNCATED_STOP_REASON
    # 本次 run 的用量照常入账。
    assert message_end["usage"]["output_tokens"] == _max_tokens()

    error = events[-1].data
    assert error["code"] == ErrorCode.AGENT_OUTPUT_TRUNCATED
    # 与同类「回复继续」的停止一致：不给重试按钮（重试会重发原请求、从头再做）。
    assert error["retryable"] is False
    assert error["refundable"] is True
    assert error["error"] == get_error_message(ErrorCode.AGENT_OUTPUT_TRUNCATED, "zh")
    # 思考原文不进 ERROR 事件。
    assert THINKING.strip() not in json.dumps(error, ensure_ascii=False)

    truncated_logs = [fields for message, fields in warnings if "output cap" in message]
    assert len(truncated_logs) == 1
    fields = truncated_logs[0]
    assert fields["agent_type"] == "writer"
    assert fields["output_tokens"] == _max_tokens()
    assert fields["max_output_tokens"] == _max_tokens()
    assert fields["thinking_chars"] == len(THINKING) * 2
    assert fields["stop_basis"] == "usage_output_tokens_at_cap"

    # 思考仍写回会话状态（与其他结束方式一致）。
    assistant_turn = state["messages"][-1]
    assert assistant_turn["role"] == "assistant"
    assert assistant_turn["content"][0]["type"] == "thinking"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_reasoning_only_run_below_cap_is_not_an_error(monkeypatch, warnings):
    events, _requests, _state = await _run_against_local_model(
        monkeypatch,
        lambda _index: _reasoning_chunks(1000, finish_reason="stop"),
        _unused_edit,
    )

    types = _types(events)
    assert StreamEventType.ERROR not in types
    assert types[-1] == StreamEventType.MESSAGE_END
    assert events[-1].data["stop_reason"] == "end_turn"
    assert not [message for message, _ in warnings if "output cap" in message]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_run_with_reply_text_at_cap_is_not_an_error(monkeypatch):
    events, _requests, _state = await _run_against_local_model(
        monkeypatch,
        lambda _index: _reasoning_chunks(_max_tokens(), text="第三章写好了。"),
        _unused_edit,
    )

    types = _types(events)
    assert StreamEventType.TEXT in types
    assert StreamEventType.ERROR not in types
    assert events[-1].data["stop_reason"] == "end_turn"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_call_then_thinking_only_truncated_turn_ends_as_truncated(monkeypatch):
    """交接后的 writer 常先调工具重读稿件，最后那次响应只剩思考并撞上输出上限：
    只看最后一次响应，这种情况同样不能当作正常结束。"""

    async def query_files(_args):
        return {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": []})}]}

    def script(index: int) -> list[dict]:
        if index == 0:
            return _tool_call_chunks("call-0", "query_files", json.dumps({"query": "第三章"}))
        return _reasoning_chunks(_max_tokens())

    events, requests, _state = await _run_against_local_model(
        monkeypatch, script, _unused_edit, tool_impls={"query_files": query_files}
    )

    assert len(requests) == 2
    types = _types(events)
    assert StreamEventType.TOOL_USE in types
    assert StreamEventType.ERROR in types
    error = next(e for e in events if e.type == StreamEventType.ERROR).data
    assert error["code"] == ErrorCode.AGENT_OUTPUT_TRUNCATED
    end = [e for e in events if e.type == StreamEventType.MESSAGE_END][-1]
    assert end.data["stop_reason"] == "output_truncated"


# ------------------------------------------------------------------ 计费：沿用既有规则


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _truncated_error_frame() -> str:
    return _sse(
        "error",
        {
            "message": get_error_message(ErrorCode.AGENT_OUTPUT_TRUNCATED, "zh"),
            "code": ErrorCode.AGENT_OUTPUT_TRUNCATED,
            "retryable": False,
            "refundable": True,
        },
    )


@pytest.mark.unit
def test_truncated_round_without_output_is_refunded_by_existing_rule():
    tracker = StreamBillingTracker()
    tracker.observe(_sse("thinking", {"message": THINKING}))
    tracker.observe(_truncated_error_frame())

    assert tracker.decide(client_disconnected=False, unexpected_exception=False) == (
        "internal_error",
        True,
    )


@pytest.mark.unit
def test_truncated_round_after_output_is_billed_by_existing_rule():
    tracker = StreamBillingTracker()
    # 同一请求里前一个 agent 已经写进了正文（writer → 审稿人 → writer 第二次截断）。
    tracker.observe(
        _sse(
            "tool_result",
            {
                "tool_name": "edit_file",
                "status": "success",
                "data": {"id": "f1", "mutation_applied": True},
            },
        )
    )
    tracker.observe(_sse("agent_selected", {"agent_type": "writer"}))
    tracker.observe(_sse("thinking", {"message": THINKING}))
    tracker.observe(_truncated_error_frame())

    assert tracker.decide(client_disconnected=False, unexpected_exception=False) == (
        "error_after_output",
        False,
    )
