"""模型在一次调用里把一大段原样再写一遍时，截停这一轮，正文只留重复之前的部分。

生产 10-26（MiniMax，要求「8000 字左右的短篇」）：前一万二千字正常推进，之后一段约 5,012 字
几乎逐字重复了九遍（相邻两遍相似度 99.9%），一直写到 780 秒的单轮时限才被截断，
文件存下 61,430 个字符，作者只能要求删掉重复。
"""

import random
from unittest.mock import AsyncMock

import pytest

from agent.core.events import EventType
from agent.core.stream_errors import REPETITION_LOOP_STOP_REASON
from agent.core.stream_processor import find_repetition_start
from agent.core.workflow_events import StreamEvent as WorkflowStreamEvent
from agent.core.workflow_events import StreamEventType

_CHARS = "天地玄黄宇宙洪荒日月盈昃辰宿列张寒来暑往秋收冬藏闰余成岁律吕调阳云腾致雨露结为霜金生丽水玉出昆冈"


def _prose(length: int, seed: int) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice(_CHARS) for _ in range(length))


@pytest.mark.unit
def test_a_repeated_passage_is_found_where_the_repeat_starts():
    opening, cycle = _prose(3000, 1), _prose(5000, 2)
    # 连续第三遍才算循环；检查晚一点也一样截在第二遍开头，只留一遍
    for written in (800, 3000, 12000):
        assert find_repetition_start(opening + cycle * 2 + (cycle * 3)[:written]) == len(opening + cycle)
    assert find_repetition_start(opening + cycle) is None
    assert find_repetition_start(opening + cycle + cycle[:4999]) is None
    # 「修改前 / 修改后」对照里原样出现两遍的大段文字不算
    middle = _prose(1200, 9)
    assert find_repetition_start("修改前：甲" + middle + "乙\n修改后：丙" + middle + "丁") is None
    # 分隔线、空行这类只由少数几个字符组成的重复不算
    assert find_repetition_start("——\n\n" * 3000) is None


@pytest.mark.asyncio
@pytest.mark.unit
async def test_adapter_saves_only_the_text_before_the_repeat():
    from agent.stream_adapter import create_stream_adapter
    from agent.tools.mcp_tools import ToolContext

    opening, cycle = _prose(3000, 3), _prose(2000, 4)
    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)
    ToolContext.set_pending_empty_file("story", "订婚宴前的账本")
    adapter.set_pending_file_write("story", "draft", "订婚宴前的账本")

    async def events():
        yield WorkflowStreamEvent(type=StreamEventType.TEXT, data={"text": "<file>\n" + opening + cycle})
        yield WorkflowStreamEvent(type=StreamEventType.TEXT, data={"text": cycle + cycle[:900]})
        yield WorkflowStreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": REPETITION_LOOP_STOP_REASON})
        yield WorkflowStreamEvent(
            type=StreamEventType.ERROR,
            data={"code": "ERR_AGENT_REPETITION_LOOP", "retryable": False, "refundable": True,
                  "reason": REPETITION_LOOP_STOP_REASON},
        )

    out = [e async for e in adapter.process_workflow_events(events())]

    adapter._save_file_content.assert_awaited_once()
    assert adapter._save_file_content.await_args.args[:2] == ("story", opening + cycle)
    error = next(e for e in out if e.type == EventType.ERROR)
    assert error.data["code"] == "ERR_AGENT_REPETITION_LOOP"
    assert error.data["reason"] == REPETITION_LOOP_STOP_REASON


@pytest.mark.asyncio
@pytest.mark.unit
async def test_sdk_run_stops_as_soon_as_the_model_repeats_a_long_passage(monkeypatch):
    from agent.openai_agents.model import reset_sdk_model_cache
    from tests.test_agent.test_tool_failure_breaker import _chunk, _run_against_local_model

    opening, cycle = _prose(1500, 5), _prose(1200, 6)
    pieces = [opening[i:i + 100] for i in range(0, len(opening), 100)]
    pieces += [cycle[i:i + 100] for i in range(0, len(cycle), 100)] * 6

    def script(_index: int) -> list[dict]:
        return [_chunk({"role": "assistant", "content": ""}), *[_chunk({"content": p}) for p in pieces],
                _chunk({}, "stop", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})]

    async def _unused_edit(_args):  # pragma: no cover - the script calls no tools
        raise AssertionError("edit_file should not be called")

    reset_sdk_model_cache()
    try:
        events, requests, _state = await _run_against_local_model(
            monkeypatch, script, _unused_edit, client_may_disconnect=True
        )
    finally:
        reset_sdk_model_cache()

    text = "".join(e.data["text"] for e in events if e.type == StreamEventType.TEXT)
    assert len(text) < len(opening) + len(cycle) * 3  # stopped during the third copy, not after six
    assert len(requests) == 1
    assert [e.type for e in events][-2:] == [StreamEventType.MESSAGE_END, StreamEventType.ERROR]
    assert events[-2].data["stop_reason"] == REPETITION_LOOP_STOP_REASON
    error = events[-1].data
    assert error["code"] == "ERR_AGENT_REPETITION_LOOP"
    assert error["reason"] == REPETITION_LOOP_STOP_REASON
    assert error["retryable"] is False


@pytest.mark.unit
def test_a_loop_that_wrote_nothing_is_refunded_and_one_that_saved_a_body_is_charged():
    from tests.test_agent.test_repeat_read_guard import _decide, _sse

    error = _sse("error", {"message": "x", "code": "ERR_AGENT_REPETITION_LOOP", "refundable": True,
                           "reason": REPETITION_LOOP_STOP_REASON})
    chat_only = [_sse("content", {"text": _prose(1600, 7)}), error]
    assert _decide(chat_only) == ("runaway_no_progress", True)

    saved = [_sse("file_content", {"file_id": "story", "chunk": "正文"}),
             _sse("file_content_end", {"file_id": "story"}), error]
    assert _decide(saved)[1] is False
