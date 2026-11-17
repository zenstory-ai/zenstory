"""作者看到的回复文字（I03 回归，全部 mock，不调用模型）。

- IDLE 下独占一行的 <file> / </file> 不进对话（StreamProcessor）
- 同一个 agent 隔着工具调用的两段文字之间补空行、紧接工具调用的英文过程句不发（AgentTextShaper）

测试文本都是自己构造的，不来自真实作品。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from agent.core.events import EventType
from agent.core.workflow_events import StreamEvent, StreamEventType
from tests.test_agent.body_write_events import create_file_events


def _text(text: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.TEXT, data={"text": text})



@pytest.mark.asyncio
@pytest.mark.unit
async def test_lone_close_tag_after_summary_does_not_reach_chat():
    """U082 的形态：正文收尾后又读了一次文件，总结末尾单独一行「</file>」。"""
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)
    events = [
        *create_file_events("draft-3", "第三章"),
        _text("<file>正文。</file>\n\n第三章写好了，我再通读一遍。"),
        StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={"id": "q1", "name": "query_files", "status": "complete", "input": {"id": "draft-3"}},
        ),
        _text("第三章按倒叙写完。\n\n接着可以回：\n- 写第四章\n</fi"),
        _text("le>\n\n我先读取第三章当前全文。"),
        StreamEvent(type=StreamEventType.MESSAGE_END, data={}),
    ]

    async def _events():
        for event in events:
            yield event

    chat = "".join(
        [sse.data.get("text", "") async for sse in adapter.process_workflow_events(_events()) if sse.type == EventType.CONTENT]
    )

    assert "</file>" not in chat
    assert "- 写第四章\n\n我先读取第三章当前全文。" in chat



@pytest.mark.unit
@pytest.mark.parametrize(
    "chunks, expected",
    [
        (["示例：\n```\n</file>\n```\n"], "示例：\n```\n</file>\n```\n"),
        (["写成 `</file>` 就行\n"], "写成 `</file>` 就行\n"),
        (["正文要用 <file> 包起来\n"], "正文要用 <file> 包起来\n"),
        (["结尾\n<file>\n第二段\n"], "结尾\n第二段\n"),
    ],
)
def test_lone_tag_rule_keeps_literal_tags(chunks, expected):
    from agent.core.stream_processor import StreamProcessor

    proc = StreamProcessor()
    out = "".join(proc.process_content(chunk).conversation_content for chunk in chunks)
    out += proc.finalize_on_stream_end().conversation_content
    assert out == expected



@pytest.mark.unit
def test_segments_of_one_agent_separated_by_tool_calls_are_not_glued():
    from agent.core.author_facing_text import AgentTextShaper

    shaper = AgentTextShaper(previous_text="", drop_english_narration=True)
    out = shaper.feed("正在读取第三章。")
    out += shaper.end_segment(before_tool_call=True)
    out += shaper.feed("正在核对细纲。")
    out += shaper.end_segment(before_tool_call=True)
    out += shaper.feed("<file>正文</file>")
    out += shaper.end_segment(before_tool_call=False)

    assert out == "正在读取第三章。\n\n正在核对细纲。<file>正文</file>"



@pytest.mark.unit
def test_text_shaper_keeps_chinese_and_file_bodies_streaming():
    from agent.core.author_facing_text import AgentTextShaper, is_english_process_narration

    shaper = AgentTextShaper(previous_text="", drop_english_narration=True)
    # 中文立刻发出去，不等到工具调用。
    assert shaper.feed("第三章写好了，") == "第三章写好了，"
    assert shaper.end_segment(before_tool_call=True) == ""
    # <file> 正文不攒。
    assert shaper.feed("<file>\nHe said") == "<file>\nHe said"
    assert shaper.end_segment(before_tool_call=False) == ""
    # 最后的英文总结（不是紧接着工具调用）照常发。
    assert shaper.feed("OK") == ""
    assert shaper.end_segment(before_tool_call=False) == "OK"
    assert is_english_process_narration("Now create the 分集大纲 file.") is True
    assert is_english_process_narration("OK，第三章写好了") is False
