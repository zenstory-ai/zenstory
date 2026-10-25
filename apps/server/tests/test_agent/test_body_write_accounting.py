"""自动质检门的字数只算真正落进正文的写入。

- edit_file 部分成功：失败的那几处编辑没写进文件，字数不算。
"""

from __future__ import annotations

import json

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.body_writes import BodyWriteTracker, body_words_written
from utils.text_metrics import count_words

pytestmark = pytest.mark.unit

THRESHOLD = 500


def _use(call_id: str, name: str, tool_input: dict) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.TOOL_USE,
        data={"id": call_id, "name": name, "status": "complete", "input": tool_input},
    )


def _result(call_id: str, name: str, payload: dict) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.TOOL_RESULT,
        data={
            "tool_use_id": call_id,
            "name": name,
            "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]},
        },
    )


def _partial_edit_payload() -> dict:
    return {
        "status": "partial",
        "mutation_applied": True,
        "data": {
            "id": "draft-1",
            "title": "第一章",
            "file_type": "draft",
            "mutation_applied": True,
            "edits_applied": 1,
            "new_word_count": 110,
            "details": [{"index": 0, "op": "append"}],
            "failed_edits": [{"index": 1, "op": "replace", "error": "anchor not found"}],
            "partial_success": True,
            "all_failed": False,
        },
    }


SUCCESS_TEXT = "实" * 10
FAILED_TEXT = "虚" * 600
PARTIAL_EDITS = [
    {"op": "append", "text": SUCCESS_TEXT},
    {"op": "replace", "old": "不存在", "new": FAILED_TEXT},
]


def test_partial_edit_counts_only_applied_edits():
    tracker = BodyWriteTracker()
    tracker.observe(
        _use("edit-1", "edit_file", {"id": "draft-1", "edits": PARTIAL_EDITS, "continue_on_error": True}),
        text_length=0,
    )
    tracker.observe(_result("edit-1", "edit_file", _partial_edit_payload()), text_length=0)

    words = body_words_written(tracker.finish(""))

    assert words == count_words(SUCCESS_TEXT)
    assert words < THRESHOLD


def test_parallel_partial_edit_counts_only_applied_edits():
    tracker = BodyWriteTracker()
    parallel_input = {"tasks": [{"type": "edit_file", "params": {"id": "draft-1", "edits": PARTIAL_EDITS}}]}
    parallel_result = {
        "status": "success",
        "data": {"tasks": [{"type": "edit_file", "status": "completed", "result": _partial_edit_payload()}]},
    }
    tracker.observe(_use("p1", "parallel_execute", parallel_input), text_length=0)
    tracker.observe(_result("p1", "parallel_execute", parallel_result), text_length=0)

    words = body_words_written(tracker.finish(""))

    assert words == count_words(SUCCESS_TEXT)
