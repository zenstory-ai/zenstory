"""停止 / 断线时什么算「这一轮有产出」（StreamBillingTracker）与空白占位文件候选。

见 .agents/notes/implemented/architecture/2026-10-09-stop-output-definition-and-round-outcome.md。
"""

import json

import pytest

from agent.core.stream_billing import PROSE_MIN_CHARS, StreamBillingTracker, prose_units

pytestmark = pytest.mark.unit

_PROSE = "第三章的节奏偏慢：前两节都在交代旧书店的来历，主角直到第三节才第一次碰到那本没有书名的旧账本，读者等得太久，建议把账本提前到开头。"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _create_file(file_id: str, *, content: str = "", **extra) -> str:
    data = {"id": file_id, "title": f"《{file_id}》", "content": content, "file_type": "draft", **extra}
    return _sse("tool_result", {"tool_name": "create_file", "status": "success", "data": data})


def _tracker(*frames: str) -> StreamBillingTracker:
    tracker = StreamBillingTracker()
    tracker.observe(_sse("session_started", {"session_id": "s1"}))
    for frame in frames:
        tracker.observe(frame)
    return tracker


def _stop(tracker: StreamBillingTracker) -> tuple[str, bool]:
    return tracker.decide(client_disconnected=False, unexpected_exception=False, user_stopped=True)


def _disconnect(tracker: StreamBillingTracker) -> tuple[str, bool]:
    return tracker.decide(client_disconnected=True, unexpected_exception=False)


def test_prose_threshold_sits_above_transitional_narration():
    for narration in ("我先看一遍全书大纲。", "正在查看已有的全书大纲，确认现有设定后再梳理主线。"):
        assert len(narration) < PROSE_MIN_CHARS
    assert len(_PROSE) >= PROSE_MIN_CHARS


def test_narration_before_a_tool_call_is_not_output():
    tracker = _tracker(
        _sse("content", {"text": "我先看一遍全书大纲。"}),
        _sse("tool_call", {"tool_name": "query_files", "arguments": {}}),
        _sse("tool_result", {"tool_name": "query_files", "status": "success"}),
        _sse("content", {"text": "我先确认第1章的文风和人物口吻，再动笔。"}),
        _sse("tool_call", {"tool_name": "query_files", "arguments": {}}),
    )
    assert tracker.produced_output is False
    assert _stop(tracker) == ("user_stopped_no_output", True)
    assert _disconnect(tracker) == ("client_disconnected_no_output", True)


def test_short_narration_segments_do_not_add_up_across_tool_calls():
    narration = "我先看一下这一章。"
    frames = []
    for _ in range(10):
        frames += [_sse("content", {"text": narration}), _sse("tool_call", {"tool_name": "query_files"})]
    tracker = _tracker(*frames)
    assert tracker.produced_output is False


def test_real_prose_streamed_in_chunks_is_output():
    chunks = [_PROSE[i : i + 7] for i in range(0, len(_PROSE), 7)]
    tracker = _tracker(*[_sse("content", {"text": chunk}) for chunk in chunks])
    assert tracker.produced_output is True
    assert tracker.first_output_ms is not None
    assert _stop(tracker) == ("user_stopped", False)


def test_empty_create_file_is_not_output_and_is_a_placeholder_candidate():
    tracker = _tracker(_create_file("ch1"))
    assert tracker.produced_output is False
    assert tracker.write_succeeded is False
    assert tracker.removable_placeholders() == [{"id": "ch1", "title": "《ch1》"}]
    assert _stop(tracker) == ("user_stopped_no_output", True)


def test_body_streamed_into_the_new_chapter_is_output_and_keeps_it():
    tracker = _tracker(
        _create_file("ch2"),
        _sse("file_content", {"file_id": "ch2", "chunk": "雨还在下。"}),
        _create_file("ch3"),
    )
    assert tracker.produced_output is True
    assert tracker.write_succeeded is True
    # 第 2 章有正文，不是候选；第 3 章刚建好还是空的。
    assert tracker.removable_placeholders() == [{"id": "ch3", "title": "《ch3》"}]
    assert _stop(tracker) == ("user_stopped", False)


def test_create_file_with_content_is_output_but_a_reused_file_or_folder_is_not():
    assert _tracker(_create_file("a", content="第一章正文")).produced_output is True

    reused = _tracker(_create_file("b", content="早就写好的正文", reused_existing=True))
    assert reused.produced_output is False
    assert reused.removable_placeholders() == []

    folder = _tracker(_create_file("c", file_type="folder"))
    assert folder.produced_output is False
    assert folder.removable_placeholders() == []


@pytest.mark.parametrize(
    ("data", "is_output"),
    [
        ({"mutation_applied": True, "edits_applied": 2}, True),
        ({"mutation_applied": False, "edits_applied": 1}, False),
        ({"edits_applied": 0}, False),
        ({}, True),  # 旧格式：成功即算
    ],
)
def test_edit_counts_only_when_text_changed(data, is_output):
    tracker = _tracker(_sse("tool_result", {"tool_name": "edit_file", "status": "success", "data": data}))
    assert tracker.produced_output is is_output


def test_thinking_and_reads_are_not_output():
    tracker = _tracker(
        _sse("thinking_content", {"content": _PROSE}),
        _sse("tool_result", {"tool_name": "query_files", "status": "success", "data": {"content": _PROSE}}),
    )
    assert tracker.produced_output is False


def test_session_id_is_captured_for_the_stop_done_frame():
    assert _tracker().session_id == "s1"


# ------------------------------------------------------------- 终止帧之后才断开（stop.nb[0]）

_SHORT_ANSWER = "第三章叫《雪夜》。"


def test_disconnect_after_done_settles_as_completed_even_for_a_short_answer():
    """答完（done 已产生）之后面板卸载断开：不是中断，短回答也照常计费，不退还。"""
    tracker = _tracker(_sse("content", {"text": _SHORT_ANSWER}), _sse("done", {}))
    assert tracker.produced_output is False  # 不到 60 字
    assert _disconnect(tracker) == ("completed", False)


def test_disconnect_after_a_terminal_error_follows_the_error_rules():
    no_output = _tracker(
        _sse("error", {"message": "x", "code": "ERR_AGENT_UPSTREAM_UNAVAILABLE", "refundable": True})
    )
    assert _disconnect(no_output) == ("internal_error", True)

    circuit = _tracker(
        _sse("tool_result", {"tool_name": "query_files", "status": "success"}),
        _sse("error", {"message": "x", "code": "ERR_AGENT_TOOL_FAILURES", "refundable": False}),
    )
    assert _disconnect(circuit) == ("non_refundable_error", False)


def test_disconnect_before_the_round_ends_still_uses_the_stop_rules():
    tracker = _tracker(_sse("content", {"text": _SHORT_ANSWER}))
    assert _disconnect(tracker) == ("client_disconnected_no_output", True)


def test_author_stop_after_done_still_uses_the_stop_rules():
    tracker = _tracker(_sse("content", {"text": _PROSE}), _sse("done", {}))
    assert tracker.decide(
        client_disconnected=True, unexpected_exception=False, user_stopped=True
    ) == ("user_stopped", False)


# ------------------------------------------------------------- 出错 / 时限路径（stop.nb[3]）


def _error(refundable: bool = True) -> str:
    return _sse(
        "error",
        {"message": "x", "code": "ERR_AGENT_UPSTREAM_UNAVAILABLE", "retryable": True, "refundable": refundable},
    )


def _fail(tracker: StreamBillingTracker) -> tuple[str, bool]:
    return tracker.decide(client_disconnected=False, unexpected_exception=False)


def _deadline(tracker: StreamBillingTracker) -> tuple[str, bool]:
    return tracker.decide(client_disconnected=False, unexpected_exception=False, deadline_exceeded=True)


def test_error_right_after_a_short_complete_answer_is_charged_as_before():
    """作者已经读到了一句完整的短回答，之后才出错：照旧计费（不到 60 字也算）。"""
    tracker = _tracker(_sse("content", {"text": _SHORT_ANSWER}), _error())
    assert tracker.ended_on_reply is True
    assert _fail(tracker) == ("error_after_output", False)


def test_error_after_narration_and_reads_is_refunded():
    """旁白之后去读文件、然后平台出错：作者什么也没拿到，退还。"""
    tracker = _tracker(
        _sse("content", {"text": "我先看一遍全书大纲。"}),
        _sse("tool_call", {"tool_name": "query_files"}),
        _sse("tool_result", {"tool_name": "query_files", "status": "success"}),
        _error(),
    )
    assert tracker.ended_on_reply is False
    assert _fail(tracker) == ("internal_error", True)


_PREAMBLE = "好的，我来为这个设定写下完整的内容，先从世界观的"
_LONG_PREAMBLE = (
    "好的，我已经仔细核对了前三章的时间线、角色关系与伏笔安排，接下来马上为你把这一章完整写出来，"
    "重点交代雨夜渡口的第一次正面交锋、旧账本暗藏的线索以及两位当面人物各自的底牌。"
)


def _write_chapter_replaced(file_id: str) -> str:
    data = {"id": file_id, "title": "第1章", "action": "replaced", "original_content_length": 3000}
    return _sse("tool_result", {"tool_name": "write_chapter", "status": "success", "data": data})


@pytest.mark.parametrize("preamble", [_PREAMBLE, _LONG_PREAMBLE], ids=["short_preamble", "long_80char_preamble"])
@pytest.mark.parametrize(
    "target",
    [_create_file("lore"), _write_chapter_replaced("ch1")],
    ids=["new_empty_file", "whole_chapter_replacement"],
)
def test_error_while_a_file_body_is_pending_is_refunded_even_after_its_preamble(target, preamble):
    """文件建好（或定位了整章替换）、正文还没写进去时出错 / 停止 / 到时：适配器扣着的 ``<file>`` 引子
    （即使超过 60 字）在终态前才放出，作者没有先读到一段回复，这一轮什么也没交付，一律退还。"""
    assert prose_units(_LONG_PREAMBLE) >= PROSE_MIN_CHARS and len(_LONG_PREAMBLE) >= 80
    tracker = _tracker(
        _sse("tool_call", {"tool_name": "create_file", "arguments": {}}),
        target,
        _sse("content", {"text": preamble}),
    )
    assert tracker.prose_output is False
    assert tracker.produced_output is False
    assert _stop(tracker) == ("user_stopped_no_output", True)

    tracker.observe(_error())
    assert tracker.prose_output is False
    assert tracker.produced_output is False
    assert tracker.ended_on_reply is False
    assert _fail(tracker) == ("internal_error", True)
    assert _deadline(tracker) == ("run_deadline_exceeded", True)


def test_long_reply_after_saved_file_body_is_still_charged_and_unsaved_replace_clears_pending():
    """反例：正文落库之后的 ≥80 字回复照常算 prose_output 计费；被拒存（body_saved=False）的整章替换不再滞留在 unconfirmed。"""
    after_new_file = _tracker(
        _create_file("ch1"),
        _sse("file_content", {"file_id": "ch1", "chunk": "雨还在下。"}),
        _sse("file_content_end", {"file_id": "ch1"}),
        _sse("content", {"text": _LONG_PREAMBLE}),
    )
    assert after_new_file.prose_output is True
    assert _stop(after_new_file) == ("user_stopped", False)
    assert _deadline(after_new_file) == ("run_deadline_exceeded", False)

    after_saved_replace = _tracker(
        _write_chapter_replaced("ch1"),
        _sse(
            "tool_result",
            {
                "tool_name": "write_chapter",
                "status": "success",
                "data": {"id": "ch1", "action": "replaced", "body_saved": True, "mutation_applied": True},
            },
        ),
        _sse("content", {"text": _LONG_PREAMBLE}),
    )
    assert after_saved_replace.prose_output is True
    assert _stop(after_saved_replace) == ("user_stopped", False)

    settled_unsaved = _tracker(
        _write_chapter_replaced("ch1"),
        _sse(
            "tool_result",
            {
                "tool_name": "write_chapter",
                "status": "success",
                "data": {"id": "ch1", "action": "replaced", "body_saved": False, "mutation_applied": False},
            },
        ),
    )
    assert settled_unsaved.unconfirmed_replacement_ids() == set()
    assert settled_unsaved.file_body_pending() is False
    settled_unsaved.observe(_sse("content", {"text": _LONG_PREAMBLE}))
    assert settled_unsaved.prose_output is True


def test_short_answer_after_the_file_body_landed_is_still_a_reply():
    """正文已经写进新文件之后再给作者一句短回答、然后出错：照旧按回复计费。"""
    tracker = _tracker(
        _create_file("ch1"),
        _sse("file_content", {"file_id": "ch1", "chunk": "雨还在下。"}),
        _sse("tool_result", {"tool_name": "query_files", "status": "success"}),
        _sse("content", {"text": _SHORT_ANSWER}),
        _error(),
    )
    assert tracker.ended_on_reply is True
    assert _fail(tracker) == ("error_after_output", False)


def test_deadline_after_only_reads_is_refunded_but_after_a_reply_is_charged():
    reads = _tracker(
        _sse("tool_call", {"tool_name": "query_files"}),
        _sse("tool_result", {"tool_name": "query_files", "status": "success"}),
        _error(),
    )
    assert _deadline(reads) == ("run_deadline_exceeded", True)

    replied = _tracker(_sse("content", {"text": _SHORT_ANSWER}), _error())
    assert _deadline(replied) == ("run_deadline_exceeded", False)


@pytest.mark.parametrize("server_restarting", [False, True])
def test_deadline_that_fires_after_done_settles_as_completed(server_restarting):
    """done 帧已经产生之后才到的时限（或发布交接）：这一轮已经正常结束，按完成结算，不退还。"""
    tracker = _tracker(_sse("done", {}))

    assert tracker.decide(
        client_disconnected=False,
        unexpected_exception=False,
        deadline_exceeded=True,
        server_restarting=server_restarting,
    ) == ("completed", False)


def test_deadline_after_a_terminal_error_still_follows_the_deadline_rules():
    tracker = _tracker(_error())

    assert _deadline(tracker) == ("run_deadline_exceeded", True)


def test_english_narration_is_not_prose_but_an_english_answer_is():
    """英文一句过渡旁白有六七十个字母，按字母数会被当成回复；按单词折算后与中文同一口径。"""
    narration = (
        "Locking the design now, then handing the manuscript to the writer "
        "so it comes back as a finished ~3,000-word story."
    )
    answer = (
        "Chapter three drags: the first two scenes explain the bookshop's history, and the hero "
        "only finds the untitled ledger in scene three. Readers wait too long, so move the ledger "
        "to the opening page and let the history surface while she reads it."
    )
    assert prose_units(narration) < PROSE_MIN_CHARS <= prose_units(answer)
    # 流式分片把单词拆开也不重复计数。
    split = _tracker(*(_sse("content", {"text": narration[i : i + 3]}) for i in range(0, len(narration), 3)))
    assert split.produced_output is False


def test_deadline_after_only_a_memory_update_and_narration_is_refunded():
    """时限到期前只改了 AI 记忆、说了一句过渡旁白（正文还在思考里）：作者要的东西没写出来，退还。"""
    tracker = _tracker(
        _sse("content", {"text": "Locking the design now, then handing the manuscript to the writer."}),
        _sse(
            "tool_result",
            {"tool_name": "update_project", "status": "success", "data": {"updated_fields": ["summary", "notes"]}},
        ),
        _sse("tool_call", {"tool_name": "query_files"}),
        _error(),
    )
    assert tracker.produced_output is True  # 停止 / 断线口径不变：改了项目信息算产出
    assert _deadline(tracker) == ("run_deadline_exceeded", True)

    wrote = _tracker(_create_file("c1", content="The bottle was green."), _error())
    assert _deadline(wrote) == ("run_deadline_exceeded", False)


def test_short_reply_does_not_make_a_stop_charged():
    """作者停止 / 断线仍只看实质产出（P1-2）：不到 60 字的回复照样退还。"""
    tracker = _tracker(
        _sse("content", {"text": _SHORT_ANSWER}),
        _sse("workflow_stopped", {"reason": "user_stopped"}),
    )
    assert _stop(tracker) == ("user_stopped_no_output", True)


# ------------------------------------------------------- update_project 改了项目信息（stop.nb[11]）


def _update_project(data: dict, status: str = "success") -> str:
    return _sse("tool_result", {"tool_name": "update_project", "status": status, "data": data})


@pytest.mark.parametrize(
    ("data", "is_output"),
    [
        ({"updated_fields": ["summary"]}, True),
        ({"updated_fields": ["writing_style", "current_phase"]}, True),
        ({"updated_fields": [], "project_name_updated": True}, True),
        # 只更新任务板 / 当前进度、改名被拒：不是作者看得见的项目信息改动。
        ({"plan": {"tasks": []}}, False),
        ({"updated_fields": ["current_phase"], "plan": {"tasks": []}}, False),
        ({"updated_fields": [], "project_name_updated": False, "title_skipped": "author_named"}, False),
    ],
)
def test_update_project_counts_only_when_project_info_changed(data, is_output):
    tracker = _tracker(_update_project(data))
    assert tracker.produced_output is is_output
    assert tracker.write_succeeded is is_output
    assert (_stop(tracker) == ("user_stopped", False)) is is_output


def test_failed_update_project_is_not_output():
    tracker = _tracker(_update_project({"updated_fields": ["summary"]}, status="error"))
    assert tracker.produced_output is False



def test_a_file_the_author_asked_to_keep_empty_is_not_a_placeholder():
    tracker = _tracker(_create_file("ch10", empty_by_request=True), _create_file("ch11"))
    assert tracker.removable_placeholders() == [{"id": "ch11", "title": "《ch11》"}]


def _write_chapter_created(file_id: str) -> str:
    data = {"id": file_id, "title": "第1章", "content": "", "file_type": "draft", "action": "created", "awaiting_body": True}
    return _sse("tool_result", {"tool_name": "write_chapter", "status": "success", "data": data})


def test_saved_receipt_for_a_created_file_counts_as_output():
    """短正文只在解析器尾缓冲里、停止时才补存：没有 file_content 帧，靠保存回执确认写入。"""
    tracker = _tracker(_write_chapter_created("ch1"))
    assert tracker.file_body_pending() is True

    tracker.note_saved_receipts({"ch1": 3})

    assert _stop(tracker) == ("user_stopped", False)
    assert tracker.file_body_pending() is False
    assert tracker.removable_placeholders() == []


def test_failed_receipt_for_a_created_file_is_still_refunded():
    tracker = _tracker(_write_chapter_created("ch1"))

    tracker.note_saved_receipts({"ch1": None})

    assert _stop(tracker) == ("user_stopped_no_output", True)
    assert [item["id"] for item in tracker.removable_placeholders()] == ["ch1"]
