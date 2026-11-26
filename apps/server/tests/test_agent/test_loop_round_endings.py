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
from agent.tools.tool_context import AgentRequestContext

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


def _runner(runs: list[list[StreamEvent]], before=None):
    pending = list(runs)

    async def fake(*, state, agent_type, system_prompt, get_steering_messages=None, **_kwargs):
        if before is not None:
            before(state["request_context"])
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": agent_type})
        for event in pending.pop(0) if pending else []:
            yield event
        state["run_items"] = [{"role": "user", "content": "x"}]
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
    req_ctx = extra.pop("request_context", None) or AgentRequestContext(
        session=None, user_id="u", project_id="p", session_id="s"
    )
    return {"user_message": "写第六章", "messages": [], "system_prompt": "系统", "request_context": req_ctx, **extra}


async def test_tool_call_exhaustion_rolls_back_pending_empty_file():
    rollback = MagicMock(return_value={"f6"})
    state = _state()
    req_ctx = state["request_context"]
    with (
        patch("agent.core.empty_files.probe_pending_file_body", return_value="empty"),
        patch("agent.core.empty_files.rollback_unfinished_empty_files", rollback),
    ):
        events = await _collect(
            state, _runner([[_exhausted()]], before=lambda ctx: ctx.set_pending_empty_file("f6", "第六章"))
        )

    rollback.assert_called_once_with([{"file_id": "f6", "title": "第六章"}], request_context=req_ctx)
    # 回滚之后才发这一轮唯一的耗尽卡，层不变，带上已撤销的文件；不再另发文字说明。
    [card] = [e.data for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card["layer"] == "tool_call" and card["iterations_used"] == 60
    assert card["rolled_back_files"] == [{"file_id": "f6", "title": "第六章"}]
    assert "unfinished_files" not in card
    assert not any(e.type == StreamEventType.TEXT for e in events)
    assert not req_ctx.has_pending_empty_file()


async def test_tool_call_exhaustion_keeps_files_whose_body_was_written():
    rollback = MagicMock(return_value=set())
    with (
        patch("agent.core.empty_files.probe_pending_file_body", return_value="written"),
        patch("agent.core.empty_files.rollback_unfinished_empty_files", rollback),
    ):
        events = await _collect(
            _state(), _runner([[_exhausted()]], before=lambda ctx: ctx.set_pending_empty_file("f6", "第六章"))
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
            _state(), _runner([[_exhausted()]], before=lambda ctx: ctx.set_pending_empty_file("f6", "第六章"))
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
    assert flagged[0].data.get("last_agent") is None
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


def _saving_runner(runs: list[list[StreamEvent]], saves: list[list[tuple[str, ...]]], calls: list[str]):
    """每次 run 开始时按 saves 记下这次 run 确认落库的正文（StreamAdapter 保存点记的同一份结构化事实）。

    每项是 (file_id, title) 或 (file_id, title, file_type)。"""
    pending_saves = list(saves)

    def before(req_ctx: AgentRequestContext):
        for file_id, title, *file_type in pending_saves.pop(0) if pending_saves else []:
            req_ctx.mark_chapter_body_saved(file_id, title, *file_type)

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
    # 作者已经看到「第2集写好了。」：续跑只自查，不再要求重复一段总结。
    assert "你也已经回复过作者" in calls[1] and "不要重复刚才给作者的总结" in calls[1]
    assert "全部完成后用一到三句话回复作者" not in calls[1]


async def test_saved_body_ending_on_its_file_block_asks_for_the_one_summary():
    """停在 </file> 上（作者还没看到任何话）：续跑说明照旧要求自查后回复作者。"""
    calls: list[str] = []
    await _collect(
        _state(user_message="写第2集"),
        _saving_runner([[*_create_events(), _BODY], [_DONE]], [[("ep2", "第2集", "script")]], calls),
    )

    assert len(calls) == 2
    assert "全部完成后用一到三句话回复作者" in calls[1]
    assert "你也已经回复过作者" not in calls[1]


async def test_saved_notes_body_followed_by_a_summary_is_not_continued():
    """只保存了大纲 / 人物等资料文件、之后已经回复作者：不为它续跑自查（照旧只在停在 </file> 上时续跑）。"""
    calls: list[str] = []
    await _collect(
        _state(user_message="写大纲"),
        _saving_runner([[*_create_events(), _BODY, _DONE], [_DONE]], [[("o1", "大纲", "outline")]], calls),
    )

    assert len(calls) == 1


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


async def test_self_check_hitting_wrap_up_after_saved_body_with_summary_finishes_cleanly():
    """正文已落库且模型已写过总结，紧接着的自查尾巴撞上收尾时间时正常完成：不发 wall_clock 暂停卡。"""
    from agent.loop.engine import run_loop_agent_streaming

    meter = AgentRunMeter(wrap_up_after_s=0)
    calls: list[str] = []
    runner = _saving_runner(
        [[*_create_events(), _BODY, _DONE], [_DONE]],
        [[("ep2", "第2集", "script")]],
        calls,
    )
    with patch("agent.loop.engine.run_openai_agents_streaming_agent", runner):
        events = [event async for event in run_loop_agent_streaming(_state(user_message="写第2集", run_meter=meter))]

    assert len(calls) == 1
    assert not [e for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    texts = "".join(str(e.data.get("text") or "") for e in events if e.type == StreamEventType.TEXT)
    assert "回复「继续」" not in texts


@pytest.mark.parametrize(
    ("english", "title", "expected_snippet", "forbidden_snippet"),
    [
        (False, "第2集 暗涌", "《第2集 暗涌》", "回复「继续」"),
        (True, "Episode 2: Undertow", '"Episode 2: Undertow"', 'Reply "continue"'),
    ],
)
async def test_self_check_hitting_wrap_up_right_after_file_block_emits_saved_note_without_pause_card(
    english: bool,
    title: str,
    expected_snippet: str,
    forbidden_snippet: str,
):
    """正文已落库、回复停在 </file> 上还没给作者写总结，自查尾巴撞上收尾时间：补一句确定性的已保存说明并正常收尾，不发暂停卡。"""
    from agent.loop.engine import run_loop_agent_streaming

    meter = AgentRunMeter(wrap_up_after_s=0)
    calls: list[str] = []
    runner = _saving_runner(
        [[*_create_events(), _BODY], [_DONE]],
        [[("ep2", title, "script")]],
        calls,
    )
    state = _state(user_message="写第2集", run_meter=meter, language="en" if english else "zh")
    with patch("agent.loop.engine.run_openai_agents_streaming_agent", runner):
        events = [event async for event in run_loop_agent_streaming(state)]

    assert len(calls) == 1
    assert not [e for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    texts = "".join(str(e.data.get("text") or "") for e in events if e.type == StreamEventType.TEXT)
    assert expected_snippet in texts
    assert forbidden_snippet not in texts


@pytest.mark.parametrize(
    ("english", "expected"),
    [
        (False, "正文已保存"),
        (True, "The text is saved"),
    ],
)
async def test_self_check_wrap_up_without_a_title_uses_the_saved_body_fallback(english: bool, expected: str):
    """已落库的正文没有可用标题时，收尾仍明确告知作者正文已保存。"""
    from agent.loop.run_support import wrap_up_self_check_skipped_text

    assert expected in wrap_up_self_check_skipped_text(english, ["", "   "])


async def test_cut_body_hitting_wrap_up_still_emits_wall_clock_pause_card():
    """正文写到输出上限被中途截断（cut_body）时撞上收尾时间：章节确实没写完，照旧发 wall_clock 暂停卡。"""
    from agent.loop.engine import run_loop_agent_streaming
    from agent.openai_agents.runner import OUTPUT_CAPPED_KEY

    meter = AgentRunMeter(wrap_up_after_s=0)

    async def capped_runner(*, state, agent_type, system_prompt, get_steering_messages=None, **_kwargs):
        state["request_context"].mark_cut_body_saved("ch3", "第3章 灯塔")
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": agent_type})
        for ev in _create_events():
            yield ev
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n半截正文没闭合"})
        state["run_items"] = [{"role": "user", "content": "x"}]
        yield StreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={"stop_reason": "end_turn", OUTPUT_CAPPED_KEY: True},
        )

    with (
        patch("agent.loop.engine.run_openai_agents_streaming_agent", capped_runner),
        patch("agent.loop.run_support.read_body_tail", return_value=("第3章 灯塔", "半截正文没闭合")),
        patch("agent.loop.run_support._ai_messages_left_today", return_value=True),
    ):
        events = [event async for event in run_loop_agent_streaming(_state(user_message="写第3章", run_meter=meter))]

    [card] = [e.data for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card["layer"] == "collaboration"
    assert card["limit"] == "wall_clock"


async def test_outline_ending_on_file_block_hitting_wrap_up_still_emits_wall_clock_pause_card():
    """资料文件（outline）落库并停在 </file> 上时撞上收尾时间：不是章节写后自查（可能刚开始搭设定还没写正文），照旧发 wall_clock 暂停卡。"""
    from agent.loop.engine import run_loop_agent_streaming

    meter = AgentRunMeter(wrap_up_after_s=0)
    calls: list[str] = []
    runner = _saving_runner(
        [[*_create_events(), _BODY], [_DONE]],
        [[("o1", "分章大纲", "outline")]],
        calls,
    )
    with (
        patch("agent.loop.engine.run_openai_agents_streaming_agent", runner),
        patch("agent.loop.run_support._ai_messages_left_today", return_value=True),
    ):
        events = [event async for event in run_loop_agent_streaming(_state(user_message="先建大纲再写第1章", run_meter=meter))]

    assert len(calls) == 1
    [card] = [e.data for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card["layer"] == "collaboration"
    assert card["limit"] == "wall_clock"


async def test_non_output_capped_cut_body_hitting_wrap_up_still_emits_wall_clock_pause_card():
    """cut_bodies 非空但 output_capped=False（如中途被其他机制截断补全）时撞上收尾时间：章节未完整闭合，不得误判为 self_check 正常结束。"""
    from agent.loop.engine import run_loop_agent_streaming

    meter = AgentRunMeter(wrap_up_after_s=0)

    async def cut_without_cap_runner(*, state, agent_type, system_prompt, get_steering_messages=None, **_kwargs):
        state["request_context"].mark_chapter_body_saved("ch1", "第1章 起航", "draft")
        state["request_context"].mark_cut_body_saved("ch2", "第2章 暗礁")
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": agent_type})
        for ev in _create_events():
            yield ev
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n半截第二章"})
        state["run_items"] = [{"role": "user", "content": "x"}]
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    with (
        patch("agent.loop.engine.run_openai_agents_streaming_agent", cut_without_cap_runner),
        patch("agent.loop.run_support._ai_messages_left_today", return_value=True),
    ):
        events = [event async for event in run_loop_agent_streaming(_state(user_message="写第1章和第2章", run_meter=meter))]

    [card] = [e.data for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card["layer"] == "collaboration"
    assert card["limit"] == "wall_clock"


async def test_self_check_run_soft_landing_on_wall_clock_finishes_cleanly_without_pause_card():
    """第 1 次 run 已把章节落库，第 2 次自查 run 经 RunExecutionPolicy 延迟 wall_clock 软着陆：
    无待写空文件/残稿时直接按 end_turn 正常收尾，不发暂停卡。"""
    from agent.loop.engine import run_loop_agent_streaming

    meter = AgentRunMeter(wrap_up_after_s=600)
    run_count = 0
    seen_defer_flags: list[bool] = []

    async def two_runs(*, state, agent_type, system_prompt, get_steering_messages=None, run_policy=None, **_kwargs):
        nonlocal run_count
        run_count += 1
        seen_defer_flags.append(bool(run_policy and run_policy.defer_wall_clock_soft_landing))
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": agent_type})
        if run_count == 1:
            state["request_context"].mark_chapter_body_saved("ch4", "第4章 死册与名额", "draft")
            for ev in _create_events():
                yield ev
            yield _BODY
            state["run_items"] = [{"role": "user", "content": "x"}]
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})
            return
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "《第4章 死册与名额》已写完并存入正文目录，3560 字。"})
        assert run_policy is not None and run_policy.defer_wall_clock_soft_landing is True
        run_policy.deferred_wall_clock_exhausted = {
            "layer": "tool_call",
            "limit": "wall_clock",
            "reason": "时间快用完了",
            "last_agent": None,
        }
        yield StreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={"stop_reason": "end_turn", "usage": {"total_tokens": 120}},
        )

    with patch("agent.loop.engine.run_openai_agents_streaming_agent", two_runs):
        events = [event async for event in run_loop_agent_streaming(_state(user_message="写第4章", run_meter=meter))]

    assert run_count == 2
    assert seen_defer_flags == [False, True]
    assert not [e for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    msg_ends = [e.data for e in events if e.type == StreamEventType.MESSAGE_END]
    assert [m["stop_reason"] for m in msg_ends] == ["end_turn", "end_turn"]


async def test_empty_file_correction_after_output_cap_gives_concise_thinking_guidance():
    """create_file 后因思考过长撞上 output_capped 仍为空文件时，纠偏说明明确指出输出上限并要求简短思考、首行直接输出 <file>。"""
    from agent.loop.engine import _settle_pending_empty_files

    req_ctx = AgentRequestContext(session=None, user_id="u1", project_id="p1", session_id="s1")
    ok, _ = req_ctx.try_reserve_pending_empty_file("res-1", "排布总表")
    assert ok
    req_ctx.bind_pending_empty_file("res-1", "f-table", "排布总表", "2026-11-19T00:00:00Z")

    with (
        patch("agent.core.empty_files.probe_pending_file_body", return_value="unwritten"),
        patch("agent.core.empty_files.resume_target_version", return_value=("2026-11-19T00:00:00Z", 0)),
    ):
        context, resume_event, notices, settle_events = await _settle_pending_empty_files(
            req_ctx,
            can_correct=True,
            english=False,
            body_cut=False,
            output_capped=True,
        )

    assert resume_event is not None
    assert notices == [] and settle_events == []
    assert "单次输出上限" in (context or "")
    assert "思考尽量简短" in (context or "")
    assert "第一行直接输出 <file>" in (context or "")


@pytest.mark.parametrize("reason", ["started_new_file", "output_capped"])
async def test_self_check_soft_landing_keeps_wall_clock_pause_card_when_new_file_started_or_output_capped(reason: str):
    """第 2 次 run 虽以 self_check 进入，但若期间又新建了文件（如第2章细纲）或总结撞上 output_capped，
    不得静默吞掉 wall_clock 暂停卡。"""
    from agent.loop.engine import run_loop_agent_streaming
    from agent.openai_agents.runner import OUTPUT_CAPPED_KEY

    meter = AgentRunMeter(wrap_up_after_s=600)
    run_count = 0

    async def two_runs(*, state, agent_type, system_prompt, get_steering_messages=None, run_policy=None, **_kwargs):
        nonlocal run_count
        run_count += 1
        yield StreamEvent(type=StreamEventType.MESSAGE_START, data={"agent_type": agent_type})
        if run_count == 1:
            state["request_context"].mark_chapter_body_saved("ch1", "第1章 起航", "draft")
            for ev in _create_events():
                yield ev
            yield _BODY
            state["run_items"] = [{"role": "user", "content": "x"}]
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})
            return
        if reason == "started_new_file":
            for ev in _create_events():
                yield ev
            yield _BODY
        else:
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "半句被截断的总结"})
        assert run_policy is not None and run_policy.defer_wall_clock_soft_landing is True
        run_policy.deferred_wall_clock_exhausted = {
            "layer": "tool_call",
            "limit": "wall_clock",
            "reason": "时间快用完了",
            "last_agent": None,
        }
        msg_end_data: dict = {"stop_reason": "end_turn"}
        if reason == "output_capped":
            msg_end_data[OUTPUT_CAPPED_KEY] = True
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data=msg_end_data)

    with patch("agent.loop.engine.run_openai_agents_streaming_agent", two_runs):
        events = [event async for event in run_loop_agent_streaming(_state(user_message="写第1章和第2章", run_meter=meter))]

    [card] = [e.data for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card["limit"] == "wall_clock"


async def test_runner_and_engine_end_to_end_deferred_wall_clock_policy():
    """真实 runner + engine + StreamAdapter 联调 RunExecutionPolicy(defer_wall_clock_soft_landing=True)：
    runner 不再发伪造的 run_deadline_exceeded 等 engine 改写；无未完待写文件时直接按 end_turn 收尾。"""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from agents.run_config import ModelInputData

    from agent.loop.engine import run_loop_agent_streaming
    from agent.stream_adapter import StreamAdapter

    meter = AgentRunMeter(wrap_up_after_s=600)
    sdk_call_inputs: list[list] = []

    class FakeSDKResult:
        raw_responses: list = []

        def __init__(self, run_idx: int, run_input: list, run_config, req_ctx):
            self._run_idx = run_idx
            self._input = list(run_input)
            self._run_config = run_config
            self._req_ctx = req_ctx

        def cancel(self, mode: str = "immediate") -> None:
            del mode

        def to_input_list(self) -> list:
            reply = "<file>\n第1章正文。\n</file>" if self._run_idx == 1 else "第1章已保存。"
            return [*self._input, {"role": "assistant", "content": reply}]

        async def stream_events(self):
            # 触发 _ModelCallCountingFilter，模拟真实 SDK 发起模型调用前的过滤。
            if self._run_idx == 2:
                # 第 2 次（self_check）run 触发墙钟软着陆
                meter.wrap_up_after_s = 0
            self._run_config.call_model_input_filter(
                SimpleNamespace(model_data=ModelInputData(input=self._input, instructions="sys"), context=self._req_ctx)
            )
            if self._run_idx == 1:
                self._req_ctx.mark_chapter_body_saved("ch1", "第1章", "draft")
                yield SimpleNamespace(
                    type="run_item_stream_event",
                    name="tool_called",
                    item=SimpleNamespace(raw_item={"name": "create_file", "call_id": "c1", "arguments": "{}"}),
                )
                yield SimpleNamespace(
                    type="run_item_stream_event",
                    name="tool_output",
                    item=SimpleNamespace(raw_item={"call_id": "c1"}, output='{"status":"success"}'),
                )
                yield SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(type="response.output_text.delta", delta="<file>\n第1章正文。\n</file>"),
                )
            else:
                yield SimpleNamespace(
                    type="raw_response_event",
                    data=SimpleNamespace(type="response.output_text.delta", delta="第1章已保存。"),
                )

    run_idx = 0

    def fake_run_streamed(agent, input, max_turns, run_config, hooks=None, context=None):  # noqa: A002
        nonlocal run_idx
        run_idx += 1
        sdk_call_inputs.append(list(input))
        return FakeSDKResult(run_idx, input, run_config, context)

    adapter = StreamAdapter()
    state = _state(user_message="写第1章", run_meter=meter)
    with (
        patch("agent.openai_agents.runner._build_agent", return_value=object()),
        patch("agent.openai_agents.runner._preferred_model_name", AsyncMock(return_value="mock-model")),
        patch("agents.Runner.run_streamed", side_effect=fake_run_streamed),
    ):
        sse_events = [
            sse async for sse in adapter.process_workflow_events(run_loop_agent_streaming(state))
        ]

    assert run_idx == 2
    # 单一历史续跑：第 2 次 run 在第 1 次 run_items 后严格只追加
    assert sdk_call_inputs[1][: len(sdk_call_inputs[0])] == sdk_call_inputs[0]
    exhausted = [e for e in sse_events if getattr(e.type, "value", "") == "iteration_exhausted"]
    assert exhausted == []
    assert adapter.get_last_message_metadata()["stop_reason"] == "end_turn"



