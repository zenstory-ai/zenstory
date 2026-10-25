"""自动质检门的字数只算真正落进正文的写入（F-16 / F-17）。

- edit_file 部分成功：失败的那几处编辑没写进文件，字数不算；
- create_file 复用已有正文的文件：没收到 </file> 的流式正文会被 StreamAdapter 拒存，
  字数不算；完整闭合的覆写照常算。
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


def _reused_create_events(call_id: str = "create-1") -> list[StreamEvent]:
    payload = {
        "status": "success",
        "mutation_applied": False,
        "data": {
            "id": "episode-3",
            "title": "第3集",
            "file_type": "script",
            "content": "",
            "content_elided": True,
            "reused_existing": True,
            "original_content_length": 8000,
            "mutation_applied": False,
        },
    }
    return [
        _use(call_id, "create_file", {"title": "第3集", "file_type": "script"}),
        _result(call_id, "create_file", payload),
    ]


PROSE = "残" * 600


def test_reused_non_empty_file_with_unclosed_block_is_not_counted():
    """StreamAdapter 拒绝用未闭合的残稿覆盖已有正文：没保存的字数不能触发送审。"""
    tracker = BodyWriteTracker()
    for event in _reused_create_events():
        tracker.observe(event, text_length=0)

    words = body_words_written(tracker.finish(f"<file>{PROSE}"))

    assert words == 0


def test_reused_non_empty_file_with_closed_block_is_counted():
    """完整以 </file> 结尾的覆写会落库，照常计入。"""
    tracker = BodyWriteTracker()
    for event in _reused_create_events():
        tracker.observe(event, text_length=0)

    words = body_words_written(tracker.finish(f"<file>{PROSE}</file>\n\n第3集写好了。"))

    assert words == count_words(PROSE)


def test_new_file_with_unclosed_block_is_still_counted():
    """新建的空文件：适配器会把截断的正文补全落库，计数口径不变。"""
    tracker = BodyWriteTracker()
    tracker.observe(_use("create-2", "create_file", {"title": "第五章", "file_type": "draft"}), text_length=0)
    tracker.observe(
        _result(
            "create-2",
            "create_file",
            {"status": "success", "data": {"id": "draft-5", "title": "第五章", "file_type": "draft", "content": ""}},
        ),
        text_length=0,
    )

    words = body_words_written(tracker.finish(f"<file>{PROSE}"))

    assert words == count_words(PROSE)


UNCHANGED_TEXT = "同" * 600


def _edit_payload_with_unchanged(*, applied: bool) -> dict:
    return {
        "status": "success",
        "mutation_applied": applied,
        "data": {
            "id": "draft-1",
            "title": "第一章",
            "file_type": "draft",
            "mutation_applied": applied,
            "edits_applied": 1 if applied else 0,
            "new_word_count": 110,
            "details": [{"index": 0, "op": "append"}] if applied else [],
            "failed_edits": [],
            "unchanged_edits": [{"index": 1 if applied else 0, "op": "replace", "reason": "already_matches"}],
            "partial_success": False,
            "all_failed": False,
        },
    }


@pytest.mark.parametrize("applied", [True, False])
def test_already_matching_identical_replace_is_not_counted(applied):
    """old 与 new 相同、原文已是这样的编辑没有写入任何字（unchanged_edits），不能让质检门多送审。"""
    identical = {"op": "replace", "old": UNCHANGED_TEXT, "new": UNCHANGED_TEXT}
    edits = [{"op": "append", "text": SUCCESS_TEXT}, identical] if applied else [identical]
    tracker = BodyWriteTracker()
    tracker.observe(_use("edit-u", "edit_file", {"id": "draft-1", "edits": edits}), text_length=0)
    tracker.observe(_result("edit-u", "edit_file", _edit_payload_with_unchanged(applied=applied)), text_length=0)

    words = body_words_written(tracker.finish(""))

    assert words == (count_words(SUCCESS_TEXT) if applied else 0)
