"""停止 / 断线时什么算「这一轮有产出」（StreamBillingTracker）与空白占位文件候选。

见 .agents/notes/implemented/architecture/2026-10-09-stop-output-definition-and-round-outcome.md。
"""

import json

import pytest

from agent.core.stream_billing import PROSE_MIN_CHARS, StreamBillingTracker

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


@pytest.mark.parametrize("decide", [_stop, _disconnect])
def test_parallel_chapter_writes_are_output_for_stop_and_disconnect(decide):
    """P2-20：只有 parallel_execute 写入成功、没有任何旁白的一轮，停止或断线都计费。"""
    tracker = _tracker(
        _sse(
            "tool_result",
            {
                "tool_name": "parallel_execute",
                "status": "success",
                "data": {
                    "tasks": [
                        {"type": "write_chapter", "status": "completed"},
                        {"type": "write_chapter", "status": "completed"},
                    ]
                },
            },
        )
    )
    assert tracker.produced_output is True
    assert decide(tracker)[1] is False


def test_parallel_lookups_or_failed_writes_are_not_output():
    tracker = _tracker(
        _sse(
            "tool_result",
            {
                "tool_name": "parallel_execute",
                "status": "success",
                "data": {
                    "tasks": [
                        {"type": "query_files", "status": "completed"},
                        {"type": "write_chapter", "status": "failed"},
                    ]
                },
            },
        )
    )
    assert tracker.produced_output is False
    assert _stop(tracker) == ("user_stopped_no_output", True)


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


def test_deadline_after_only_reads_is_refunded_but_after_a_reply_is_charged():
    reads = _tracker(
        _sse("tool_call", {"tool_name": "query_files"}),
        _sse("tool_result", {"tool_name": "query_files", "status": "success"}),
        _error(),
    )
    assert _deadline(reads) == ("run_deadline_exceeded", True)

    replied = _tracker(_sse("content", {"text": _SHORT_ANSWER}), _error())
    assert _deadline(replied) == ("run_deadline_exceeded", False)


def test_short_reply_does_not_make_a_stop_charged():
    """作者停止 / 断线仍只看实质产出（P1-2）：不到 60 字的回复照样退还。"""
    tracker = _tracker(
        _sse("content", {"text": _SHORT_ANSWER}),
        _sse("workflow_stopped", {"reason": "user_stopped"}),
    )
    assert _stop(tracker) == ("user_stopped_no_output", True)


# --------------------------------------------- 被打断的并行批次里已完成的写入子任务（stop.nb[5]）


def _task_start(task_id: str, task_type: str) -> str:
    return _sse(
        "parallel_task_start",
        {"execution_id": "e1", "task_id": task_id, "task_type": task_type, "description": task_id},
    )


def _task_end(task_id: str, status: str = "completed") -> str:
    return _sse("parallel_task_end", {"execution_id": "e1", "task_id": task_id, "status": status})


@pytest.mark.parametrize("decide", [_stop, _disconnect])
def test_completed_write_subtask_of_an_interrupted_parallel_batch_is_output(decide):
    """停在并行批次中途：第 1 章已写完（parallel_task_end），整批的 tool_result 还没来。"""
    tracker = _tracker(
        _sse("parallel_start", {"execution_id": "e1", "task_count": 2, "task_descriptions": []}),
        _task_start("t1", "write_chapter"),
        _task_start("t2", "write_chapter"),
        _task_end("t1"),
    )
    assert tracker.produced_output is True
    assert tracker.write_succeeded is True
    assert decide(tracker)[1] is False


def test_parallel_lookups_or_failed_write_subtasks_are_not_output():
    tracker = _tracker(
        _task_start("t1", "query_files"),
        _task_start("t2", "write_chapter"),
        _task_end("t1"),
        _task_end("t2", status="failed"),
        # 没有 start 帧的子任务不知道类型，不算。
        _task_end("t3"),
    )
    assert tracker.produced_output is False
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
