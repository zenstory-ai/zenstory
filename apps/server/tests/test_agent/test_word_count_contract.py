"""Agent 读到的字数与编辑器一致（全部 mock，不调用 DeepSeek）。

- serialize_file 给出 word_count = utils.text_metrics.count_words（与前端 countWords 同一定义）：
  full 模式总给；summary 模式只在预览就是全文时给；file_metadata 里的旧 word_count 被覆盖或删除。
- 自动送审的交接文案和交接包不再带字符数（content_length），审稿人不会把它当字数转述。
"""

import json
import re
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from models import File
from utils.text_metrics import count_words

# 2234 字（编辑器口径），字符数明显更多：标点、全角空格、换行都不算字。
_SENTENCE = "　　“你来了？”林晚推开门，雨水顺着伞骨往下淌。\n"
SAMPLE_2234 = "第三章　归来 Chapter Three\n" + _SENTENCE * 131


def _file(content: str, metadata: dict | None = None) -> File:
    return File(
        id="f3",
        project_id="p",
        title="第三章",
        file_type="draft",
        content=content,
        file_metadata=json.dumps(metadata) if metadata is not None else None,
    )


@pytest.mark.unit
def test_sample_counts_differ_by_definition():
    assert count_words(SAMPLE_2234) == 2234
    assert len(SAMPLE_2234) != 2234


@pytest.mark.unit
def test_full_mode_returns_editor_word_count():
    from agent.tools.file_ops.serialization import serialize_file, serialize_query_file

    data = serialize_file(_file(SAMPLE_2234), include_content=True)
    assert data["word_count"] == 2234

    queried = serialize_query_file(_file(SAMPLE_2234), response_mode="full")
    assert queried["word_count"] == 2234


@pytest.mark.unit
def test_summary_mode_counts_words_only_when_preview_is_whole_content():
    from agent.tools.file_ops.serialization import serialize_file

    short = "林晚推开门。\n雨停了。"
    whole = serialize_file(_file(short), include_content=False, content_preview_chars=200)
    assert whole["content_truncated"] is False
    assert whole["word_count"] == count_words(short) == 8

    truncated = serialize_file(_file(SAMPLE_2234), include_content=False, content_preview_chars=200)
    assert truncated["content_truncated"] is True
    assert "word_count" not in truncated, "预览算出的字数不是全文字数，不能给"
    assert truncated["content_length"] == len(SAMPLE_2234)


@pytest.mark.unit
def test_summary_projection_path_follows_the_same_rule():
    from agent.tools.file_ops.serialization import serialize_file

    preview = SAMPLE_2234[:200]
    projected = serialize_file(
        _file(""),
        include_content=False,
        content_preview_chars=200,
        preloaded_content_preview=preview,
        preloaded_content_length=len(SAMPLE_2234),
    )
    assert projected["content_truncated"] is True
    assert "word_count" not in projected

    short = "雨停了。"
    projected_short = serialize_file(
        _file(""),
        include_content=False,
        content_preview_chars=200,
        preloaded_content_preview=short,
        preloaded_content_length=len(short),
    )
    assert projected_short["word_count"] == 3


@pytest.mark.unit
def test_stale_metadata_word_count_is_overwritten_or_dropped_in_the_copy_only():
    from agent.tools.file_ops.serialization import serialize_file

    stale = {"word_count": 999, "word_count_target": 3000}
    file = _file(SAMPLE_2234, stale)
    original_metadata = file.file_metadata

    full = serialize_file(file, include_content=True)
    assert json.loads(full["file_metadata"]) == {"word_count": 2234, "word_count_target": 3000}

    truncated = serialize_file(file, include_content=False, content_preview_chars=200)
    assert json.loads(truncated["file_metadata"]) == {"word_count_target": 3000}

    assert file.file_metadata == original_metadata, "只改返回副本，不改模型实例"

    untouched = serialize_file(_file("雨停了。", {"word_count_target": 3000}), include_content=True)
    assert json.loads(untouched["file_metadata"]) == {"word_count_target": 3000}


# ------------------------------------------------------------- 自动送审的交接


def _text(text: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.TEXT, data={"text": text})


def _write_done(call_id: str) -> list[StreamEvent]:
    return [
        StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={"id": call_id, "name": "edit_file", "status": "complete"},
        ),
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": call_id,
                "name": "edit_file",
                "result": {"content": [{"type": "text", "text": '{"status": "success"}'}]},
            },
        ),
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_auto_review_handoff_carries_no_character_count(monkeypatch):
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext

    monkeypatch.setenv("AGENT_ENABLE_GRAPH_AUTO_REVIEW", "true")
    reviewer_messages: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        if agent_type == "writer":
            for event in _write_done("w1"):
                yield event
            yield _text("第三章写好了。")
            yield _text("林晚推开门。" * 60)
            return
        reviewer_messages.append(state["user_message"])
        yield _text("审查通过。[TASK_COMPLETE]")

    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    with (
        patch(
            "agent.graph.writing_graph.router_node",
            AsyncMock(return_value={"current_agent": "writer", "workflow_plan": "quick", "workflow_agents": []}),
        ),
        patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
        patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
        patch(
            "agent.graph.writing_graph._auto_finalize_task_board_on_completion",
            AsyncMock(return_value=[]),
        ),
    ):
        events = [
            event
            async for event in run_writing_workflow_streaming(
                state={"user_message": "写第三章", "messages": [], "system_prompt": ""},
                thread_id="t",
                auto_review_threshold=100,
            )
        ]

    handoff = next(
        e for e in events if e.type == StreamEventType.HANDOFF and e.data["target_agent"] == "quality_reviewer"
    )
    assert handoff.data["reason"] == "自动质量门控"
    assert handoff.data["context"] == "正文已写完，自动进入质量检查"
    assert not re.search(r"\d", handoff.data["context"])
    packet = handoff.data["handoff_packet"]
    assert not re.search(r"\d", packet["context"])
    assert all("content_length" not in item for item in packet["evidence"])

    assert reviewer_messages, "审稿人照常运行"
    assert "内容长度" not in reviewer_messages[0]
    assert "content_length" not in reviewer_messages[0]
