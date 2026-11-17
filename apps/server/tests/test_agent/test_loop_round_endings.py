"""单循环 agent 一轮的收尾与续跑：工具调用轮数耗尽时的空文件回滚、请求时间快用完时的收尾说明、
写成文字没执行的工具调用、文件流续写的边界。

这些行为原先由多 agent 图的用例覆盖；图删除后在单循环 agent 上逐项保留。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent.core.run_meter import AgentRunMeter
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.loop import run_support
from agent.tools.mcp_tools import ToolContext

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.fixture(autouse=True)
def tool_context():
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    yield
    ToolContext.clear_context()


def _runner(runs: list[list[StreamEvent]], before=None):
    pending = list(runs)

    async def fake(*, state, agent_type, system_prompt, get_steering_messages=None, **_kwargs):
        if before is not None:
            before()
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": agent_type})
        for event in pending.pop(0) if pending else []:
            yield event
        state["native_run_holder"].update(agent_type=agent_type, items=[{"role": "user", "content": "x"}])
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    return fake


async def _collect(state, runner):
    from agent.loop.engine import run_loop_agent_streaming

    with patch("agent.loop.engine.run_openai_agents_streaming_agent", runner):
        return [event async for event in run_loop_agent_streaming(state)]


def _exhausted() -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.ITERATION_EXHAUSTED,
        data={"layer": "tool_call", "iterations_used": 60, "max_iterations": 60},
    )


def _state(**extra):
    return {"user_message": "写第六章", "messages": [], "system_prompt": "系统", **extra}


async def test_tool_call_exhaustion_rolls_back_pending_empty_file():
    rollback = MagicMock(return_value={"f6"})
    with (
        patch("agent.core.empty_files.probe_pending_file_body", return_value="empty"),
        patch("agent.core.empty_files.rollback_unfinished_empty_files", rollback),
    ):
        events = await _collect(
            _state(), _runner([[_exhausted()]], before=lambda: ToolContext.set_pending_empty_file("f6", "第六章"))
        )

    rollback.assert_called_once_with([{"file_id": "f6", "title": "第六章"}])
    # 回滚之后才发这一轮唯一的耗尽卡，层不变，带上已撤销的文件；不再另发文字说明。
    [card] = [e.data for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card["layer"] == "tool_call" and card["iterations_used"] == 60
    assert card["rolled_back_files"] == [{"file_id": "f6", "title": "第六章"}]
    assert "unfinished_files" not in card
    assert not any(e.type == StreamEventType.TEXT for e in events)
    assert not ToolContext.has_pending_empty_file()


async def test_tool_call_exhaustion_keeps_files_whose_body_was_written():
    rollback = MagicMock(return_value=set())
    with (
        patch("agent.core.empty_files.probe_pending_file_body", return_value="written"),
        patch("agent.core.empty_files.rollback_unfinished_empty_files", rollback),
    ):
        events = await _collect(
            _state(), _runner([[_exhausted()]], before=lambda: ToolContext.set_pending_empty_file("f6", "第六章"))
        )

    rollback.assert_not_called()
    [card] = [e.data for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card == {"layer": "tool_call", "iterations_used": 60, "max_iterations": 60}, "没有空文件时卡片不变"


async def test_tool_call_exhaustion_reports_an_empty_file_that_could_not_be_rolled_back():
    with (
        patch("agent.core.empty_files.probe_pending_file_body", return_value="empty"),
        patch("agent.core.empty_files.rollback_unfinished_empty_files", MagicMock(return_value=set())),
    ):
        events = await _collect(
            _state(), _runner([[_exhausted()]], before=lambda: ToolContext.set_pending_empty_file("f6", "第六章"))
        )

    [card] = [e.data for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card["unfinished_files"] == [{"file_id": "f6", "title": "第六章"}]
    assert "rolled_back_files" not in card


@pytest.mark.parametrize(
    ("messages_left", "english", "expected"),
    [
        (True, False, run_support.WRAP_UP_TEXT),
        (None, False, run_support.WRAP_UP_TEXT),
        (False, False, run_support.WRAP_UP_TEXT_NO_MESSAGES_LEFT),
        (True, True, run_support.WRAP_UP_TEXT_EN),
        (False, True, run_support.WRAP_UP_TEXT_NO_MESSAGES_LEFT_EN),
    ],
)
async def test_wrap_up_does_not_say_reply_continue_once_today_is_used_up(messages_left, english, expected):
    """最后一条免费消息撞上收尾：回复「继续」发不出去，不再这样说；读不到额度时保持原说明。"""
    meter = AgentRunMeter(wrap_up_after_s=0)
    steering = iter([[{"id": "st-1", "content": "再加一段"}]])

    async def get_steering_messages(**_kwargs):
        return next(steering, [])

    from agent.loop.engine import run_loop_agent_streaming

    state = _state(run_meter=meter, language="en" if english else "zh")
    with (
        patch("agent.loop.engine.run_openai_agents_streaming_agent", _runner([[]])),
        patch("agent.loop.run_support._ai_messages_left_today", return_value=messages_left),
    ):
        events = [e async for e in run_loop_agent_streaming(state, get_steering_messages=get_steering_messages)]

    texts = "".join(str(e.data.get("text") or "") for e in events if e.type == StreamEventType.TEXT)
    assert texts.endswith(expected)
    [card] = [e.data for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card["reason"] == (run_support.WRAP_UP_CARD_REASON_EN if english else run_support.WRAP_UP_CARD_REASON)


# 回复里写成文字、没有执行的工具调用：只按协议结构识别，如实记为未完成，文字原样流出。
_TEXTUAL_CALL = (
    "好的，我来建第七章。\n<tool_call>]<]minimax[>[<invoke name=\"create_file\">]<]minimax[>["
    "<title>第七章</title>]<]minimax[>[</invoke>]<]minimax[>[</tool_call>"
)


def _deltas(text: str, size: int = 7) -> list[StreamEvent]:
    return [StreamEvent(type=StreamEventType.TEXT, data={"text": text[i:i + size]}) for i in range(0, len(text), size)]


async def test_textual_tool_call_after_the_last_tool_event_is_flagged_once_and_text_is_unchanged():
    events = await _collect(_state(user_message="建第七章"), _runner([_deltas(_TEXTUAL_CALL)]))

    flagged = [e for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert len(flagged) == 1
    assert flagged[0].data["layer"] == "unexecuted_tool_text"
    assert flagged[0].data["last_agent"] is None
    assert "limit" not in flagged[0].data
    texts = "".join(e.data.get("text", "") for e in events if e.type == StreamEventType.TEXT)
    assert _TEXTUAL_CALL in texts  # 文字逐字节原样流出，不改写、不删除
    assert not any(e.type == StreamEventType.TOOL_USE for e in events)  # 不把文字当工具执行


async def test_textual_call_followed_by_a_real_tool_event_is_not_flagged():
    real_after = [
        *_deltas(_TEXTUAL_CALL),
        StreamEvent(type=StreamEventType.TOOL_USE, data={"id": "q1", "name": "query_files", "status": "complete", "input": {}}),
        StreamEvent(type=StreamEventType.TOOL_RESULT, data={"tool_use_id": "q1", "name": "query_files", "result": {}}),
        StreamEvent(type=StreamEventType.TEXT, data={"text": "看过了。"}),
    ]
    events = await _collect(_state(user_message="建第七章"), _runner([real_after]))

    assert not [e for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]


# 文件流续写：只有这次 run 建过文件、回复停在写完的 </file>（或新正文的 EOF）上才接着跑一次。
def _create_events() -> list[StreamEvent]:
    payload = {"content": [{"type": "text", "text": json.dumps({"status": "success"})}]}
    return [
        StreamEvent(type=StreamEventType.TOOL_USE, data={"id": "c1", "name": "create_file", "status": "complete", "input": {}}),
        StreamEvent(type=StreamEventType.TOOL_RESULT, data={"tool_use_id": "c1", "name": "create_file", "result": payload}),
    ]


def _counting_runner(runs: list[list[StreamEvent]], calls: list[str]):
    inner = _runner(runs)

    async def fake(*, state, **kwargs):
        calls.append(str(state.get("user_message") or ""))
        async for event in inner(state=state, **kwargs):
            yield event

    return fake


_BODY = StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n第二集正文。\n</file>\n"})
_DONE = StreamEvent(type=StreamEventType.TEXT, data={"text": "第2集写好了。"})


def _saving_runner(runs: list[list[StreamEvent]], saves: list[list[tuple[str, str]]], calls: list[str]):
    """每次 run 开始时按 saves 记下这次 run 确认落库的正文（StreamAdapter 保存点记的同一份结构化事实）。"""
    pending_saves = list(saves)

    def before():
        for file_id, title in pending_saves.pop(0) if pending_saves else []:
            ToolContext.mark_chapter_body_saved(file_id, title)

    inner = _runner(runs, before=before)

    async def fake(*, state, **kwargs):
        calls.append(str(state.get("user_message") or ""))
        async for event in inner(state=state, **kwargs):
            yield event

    return fake


async def test_reply_with_summary_after_a_saved_body_is_self_checked():
    """正文保存后模型多写了一句总结：是否续跑看保存事实，不看 </file> 之后有没有文字。"""
    calls: list[str] = []
    await _collect(
        _state(user_message="写第1到3集"),
        _saving_runner([[*_create_events(), _BODY, _DONE], [_DONE]], [[("ep2", "第2集")]], calls),
    )

    assert len(calls) == 2
    assert calls[1].startswith("[系统提醒] 上一次回复写完的正文已保存")
    assert "《第2集》" in calls[1]


async def test_resaving_an_already_self_checked_body_with_a_summary_is_not_continued():
    calls: list[str] = []
    await _collect(
        _state(user_message="写第2集"),
        _saving_runner(
            [[*_create_events(), _BODY], [_DONE], [_DONE]],
            [[("ep2", "第2集")], [("ep2", "第2集")]],
            calls,
        ),
    )

    assert len(calls) == 2


async def test_summary_after_a_file_block_that_was_not_saved_is_not_continued():
    calls: list[str] = []
    await _collect(_state(user_message="写第2集"), _counting_runner([[*_create_events(), _BODY, _DONE]], calls))

    assert len(calls) == 1


async def test_file_block_without_a_create_in_the_run_is_not_continued():
    calls: list[str] = []
    await _collect(_state(user_message="写方案"), _counting_runner([[_BODY]], calls))

    assert len(calls) == 1


async def test_new_body_at_an_unclosed_eof_continues_once():
    calls: list[str] = []
    unclosed = StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n第二集正文。\n"})
    await _collect(_state(user_message="写第2集"), _counting_runner([[*_create_events(), unclosed], [_DONE]], calls))

    assert len(calls) == 2
    assert calls[1].startswith("[系统提醒] 上一次回复写完的正文已保存")


async def test_last_allowed_run_does_not_schedule_a_continuation():
    from agent.loop.engine import run_loop_agent_streaming

    calls: list[str] = []
    runner = _counting_runner([[*_create_events(), _BODY], [_DONE]], calls)
    with patch("agent.loop.engine.run_openai_agents_streaming_agent", runner):
        _ = [event async for event in run_loop_agent_streaming(_state(user_message="写第2集"), max_runs=1)]

    assert len(calls) == 1
