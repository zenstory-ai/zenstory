"""A body whose </file> was already parsed is never lost to a stop / disconnect before it is saved."""

import asyncio
import threading

import pytest

from agent.core.sse_pump import SSEStreamPump
from agent.core.stream_billing import StreamBillingTracker
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.stream_adapter import StreamAdapter
from utils.text_metrics import count_words

BODY = "　　正文完整到达，但尚未保存。"


async def _stop_while_last_frame_is_blocked(adapter: StreamAdapter, tracker: StreamBillingTracker) -> None:
    """Fill the default 32-item SSE queue so the producer blocks on the frame carrying the body, then stop."""
    observed_body = asyncio.Event()

    def observe(frame):
        tracker.observe(frame)
        if frame.startswith("event: file_content\n"):
            observed_body.set()

    async def workflow():
        for _ in range(31):
            yield StreamEvent(StreamEventType.THINKING, {"thinking": "x"})
        yield StreamEvent(StreamEventType.TEXT, {"text": f"<file>{BODY}</file>"})
        yield StreamEvent(StreamEventType.MESSAGE_END, {"stop_reason": "end_turn"})

    async def source():
        events = adapter.process_workflow_events(workflow())
        try:
            async for event in events:
                yield event.to_sse()
        finally:
            await events.aclose()

    pump = SSEStreamPump(source(), heartbeat_interval_s=1, on_item=observe)
    pump._task = asyncio.create_task(pump._produce())
    await asyncio.wait_for(observed_body.wait(), 2)
    assert pump._queue.qsize() == 32
    pump.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pump._task


@pytest.mark.asyncio
async def test_new_chapter_body_parsed_before_a_stop_is_saved_at_settle():
    adapter = StreamAdapter()
    adapter.set_pending_file_write("chapter-1", "draft", "第1章")
    saves = []

    async def fake_save(file_id, content, **kwargs):
        saves.append((file_id, content, kwargs.get("baseline_updated_at")))
        return True

    adapter._save_file_content = fake_save
    tracker = StreamBillingTracker()

    await _stop_while_last_frame_is_blocked(adapter, tracker)
    receipts = await adapter.settle_aborted_capture()

    assert saves == [("chapter-1", BODY, None)]
    assert receipts == {"chapter-1": count_words(BODY)}


@pytest.mark.asyncio
async def test_whole_chapter_replacement_parsed_before_a_stop_keeps_the_original():
    """新稿已解析、还没开始提交就停止：不覆盖原文（截停后没有 diff 审阅和撤销卡），不留保存回执（计费按整章替换没落库处理）。"""
    adapter = StreamAdapter()
    adapter.set_pending_file_write(
        "chapter-2",
        "draft",
        "第2章",
        original_content_length=40,
        baseline_updated_at="2026-11-17T00:00:00+00:00",
        replace_tool_use_id="call-2",
        replace_card={"id": "chapter-2", "action": "replaced", "chapter": 2},
    )
    calls = []

    def fake_replace(file_id, content, expected_updated_at):
        calls.append((file_id, content, expected_updated_at))
        return {"mutation_applied": True, "new_content": content, "original_content": "旧稿"}

    adapter._replace_file_body_sync = fake_replace
    tracker = StreamBillingTracker()

    await _stop_while_last_frame_is_blocked(adapter, tracker)
    receipts = await adapter.settle_aborted_capture()

    assert calls == []
    assert "chapter-2" not in receipts


@pytest.mark.asyncio
async def test_new_chapter_save_cancelled_mid_commit_still_records_its_receipt():
    adapter = StreamAdapter()
    adapter.set_pending_file_write("chapter-3", "draft", "第3章", baseline_updated_at="2026-11-17T01:00:00+00:00")
    started = threading.Event()
    release = threading.Event()
    seen = []

    def slow_save(file_id, content, expected_updated_at=None):
        seen.append(expected_updated_at)
        started.set()
        release.wait(5)
        return True, True

    adapter._save_file_content_sync = slow_save

    async def workflow():
        yield StreamEvent(StreamEventType.TEXT, {"text": f"<file>{BODY}</file>"})
        yield StreamEvent(StreamEventType.MESSAGE_END, {"stop_reason": "end_turn"})

    async def consume():
        async for _event in adapter.process_workflow_events(workflow()):
            pass

    task = asyncio.create_task(consume())
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    receipts = await adapter.settle_aborted_capture()

    assert seen == ["2026-11-17T01:00:00+00:00"]
    assert receipts == {"chapter-3": count_words(BODY)}


@pytest.mark.asyncio
async def test_new_body_save_only_takes_the_title_of_its_own_pending_write():
    """待写记录指向别的文件时，不把那个文件的标题记到这份正文上（与版本令牌同一口径）。"""
    from agent.core.stream_processor import StreamResult
    from agent.stream_adapter import PendingFileWrite

    adapter = StreamAdapter()
    seen = []

    async def fake_save(file_id, content, **kwargs):
        seen.append((file_id, kwargs.get("complete_title"), kwargs.get("baseline_updated_at")))
        return True

    adapter._save_file_content = fake_save
    other = PendingFileWrite(file_id="chapter-9", file_type="draft", title="第9章", baseline_updated_at="v9")
    result = StreamResult(file_id="chapter-1", file_complete=True, final_content=BODY)

    assert await adapter._save_new_body(result, other) is True
    assert await adapter._save_new_body(result, PendingFileWrite("chapter-1", "draft", "第1章")) is True
    assert seen == [("chapter-1", "", None), ("chapter-1", "第1章", None)]


def _replace_card(file_id: str, original_length: int) -> dict:
    return {
        "id": file_id,
        "action": "replaced",
        "chapter": 1,
        "title": "第1章",
        "awaiting_body": True,
        "original_content_length": original_length,
    }


def _stamped_replace(card: dict, receipts: dict, replacement_receipts: dict) -> tuple[dict, dict]:
    import json

    from agent.loop.history import _tool_output_text
    from agent.service import _stamp_body_receipts

    calls = [{"name": "write_chapter", "status": "success", "result": dict(card), "id": "call-1"}]
    _stamp_body_receipts(calls, receipts, replacement_receipts)
    return calls[0]["result"], json.loads(_tool_output_text(calls[0]))["data"]


@pytest.mark.asyncio
async def test_empty_chapter_replacement_stopped_mid_stream_is_recorded_as_saved():
    """空白的已有章节按整章替换写正文，写到一半停止：残稿补存进了这一章，卡片与历史都说已保存。"""
    adapter = StreamAdapter()
    card = _replace_card("chapter-0", 0)
    adapter.set_pending_file_write(
        "chapter-0", "draft", "第1章", original_content_length=0,
        baseline_updated_at="v0", replace_tool_use_id="call-1", replace_card=card,
    )
    saved = []

    def fake_sync(file_id, content, expected=None):
        saved.append((file_id, content))
        return True, True

    adapter._save_file_content_sync = fake_sync

    async def workflow():
        yield StreamEvent(StreamEventType.TEXT, {"text": "<file>" + BODY * 5})
        await asyncio.Event().wait()

    async def consume():
        async for _ in adapter.process_workflow_events(workflow()):
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    receipts = await adapter.settle_aborted_capture()

    assert saved and isinstance(receipts.get("chapter-0"), int)
    assert adapter.get_replacement_receipts() == {"call-1": receipts["chapter-0"]}
    stored, replay = _stamped_replace(card, receipts, adapter.get_replacement_receipts())
    assert stored["body_saved"] is True and stored["mutation_applied"] is True
    assert stored["new_word_count"] == receipts["chapter-0"]
    assert replay["body_saved"] is True


@pytest.mark.asyncio
async def test_replacement_committing_during_a_stop_is_recorded_as_saved():
    """整章替换在停止时已经在提交：提交成功的回执写进落库的卡片，历史回放已保存。"""
    adapter = StreamAdapter()
    card = _replace_card("chapter-1", 40)
    adapter.set_pending_file_write(
        "chapter-1", "draft", "第1章", original_content_length=40,
        baseline_updated_at="v1", replace_tool_use_id="call-1", replace_card=card,
    )
    started, release = threading.Event(), threading.Event()

    def slow_replace(file_id, content, expected):
        started.set()
        release.wait(5)
        return {
            "mutation_applied": True, "new_content": content, "original_content": "旧稿" * 20,
            "new_word_count": count_words(content), "undo": {"kind": "version"},
        }

    adapter._replace_file_body_sync = slow_replace

    async def workflow():
        yield StreamEvent(StreamEventType.TEXT, {"text": f"<file>{BODY}</file>"})
        yield StreamEvent(StreamEventType.MESSAGE_END, {"stop_reason": "end_turn"})

    async def consume():
        async for _ in adapter.process_workflow_events(workflow()):
            pass

    task = asyncio.create_task(consume())
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    release.set()
    receipts = await adapter.settle_aborted_capture()

    assert receipts == {"chapter-1": count_words(BODY)}
    assert adapter.get_replacement_receipts() == {"call-1": count_words(BODY)}
    stored, replay = _stamped_replace(card, receipts, adapter.get_replacement_receipts())
    assert stored["body_saved"] is True and stored["new_word_count"] == count_words(BODY)
    assert replay["body_saved"] is True


def test_unsaved_replacement_receipt_keeps_the_original_wording():
    from agent.loop.history import _REPLACE_NOT_SAVED_NOTE

    stored, replay = _stamped_replace(_replace_card("chapter-2", 40), {"chapter-2": None}, {"call-1": None})
    assert stored["body_saved"] is False and stored["mutation_applied"] is False
    assert "new_word_count" not in stored
    assert replay["body_saved"] is False and replay["result"] == _REPLACE_NOT_SAVED_NOTE


def test_replacement_card_already_carrying_its_result_is_left_alone():
    card = {**_replace_card("chapter-3", 40), "body_saved": True, "new_word_count": 7, "undo": {"kind": "version"}}
    stored, _replay = _stamped_replace(card, {"chapter-3": 99}, {"call-1": 99})
    assert stored == card


def test_second_replacement_of_a_saved_chapter_is_not_stamped_from_the_first_receipt():
    """同一章先替换成功、同一轮又定位替换一次但没写成：后一张卡不拿文件级的旧回执当成已保存。"""
    from agent.service import _stamp_body_receipts

    first_card = {**_replace_card("chapter-3", 40), "body_saved": True, "new_word_count": 3000}
    second_card = _replace_card("chapter-3", 3000)
    calls = [
        {"name": "write_chapter", "status": "success", "result": dict(first_card), "id": "call-1"},
        {"name": "write_chapter", "status": "success", "result": dict(second_card), "id": "call-2"},
    ]
    _stamp_body_receipts(calls, {"chapter-3": 3000}, {"call-1": 3000})

    assert calls[0]["result"] == first_card
    second = calls[1]["result"]
    assert second["body_saved"] is False and second["mutation_applied"] is False
    assert "new_word_count" not in second


@pytest.mark.asyncio
async def test_refused_replacement_receipt_is_keyed_by_its_own_tool_use_id():
    """被拒的整章替换只给自己的 tool_use_id 记未保存，不覆盖同一章更早一次的成功回执。"""
    from agent.tools.file_ops.crud import ConcurrentFileWriteError

    adapter = StreamAdapter()
    adapter._replacement_receipts["call-1"] = 3000
    adapter._saved_file_word_counts["chapter-3"] = 3000
    card = _replace_card("chapter-3", 3000)
    adapter.set_pending_file_write(
        "chapter-3", "draft", "第3章", original_content_length=3000,
        baseline_updated_at="v2", replace_tool_use_id="call-2", replace_card=card,
    )

    def refuse(file_id, content, expected):
        raise ConcurrentFileWriteError("changed")

    adapter._replace_file_body_sync = refuse
    outcome, _ = await adapter._save_chapter_replacement("chapter-3", BODY, adapter._pending_file_write)

    assert outcome == "refused"
    assert adapter.get_replacement_receipts() == {"call-1": 3000, "call-2": None}
    assert adapter.get_saved_file_word_counts() == {"chapter-3": 3000}


@pytest.mark.asyncio
async def test_stop_settles_body_saves_and_write_tool_threads_together():
    """截停收尾同时等正文补存与线程里的写工具：两者都记进 run_outcome，总等待不是两段相加。"""
    import time

    from agent.core.round_outcome import RunOutcome
    from agent.service import _settle_stopped_writes

    class SlowAdapter:
        async def settle_aborted_capture(self):
            await asyncio.sleep(0.3)
            return {"chapter-1": 120}

    outcome = RunOutcome()
    loop = asyncio.get_running_loop()
    tool_future = loop.run_in_executor(None, time.sleep, 0.3)
    outcome.inflight_tool_writes.append(("edit_file", tool_future))

    started = time.monotonic()
    await _settle_stopped_writes(outcome, SlowAdapter())
    elapsed = time.monotonic() - started

    assert outcome.saved_file_word_counts == {"chapter-1": 120}
    assert tool_future.done() and outcome.inflight_tool_writes == []
    assert elapsed < 0.55


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tool_name", "payload"),
    [
        ("edit_file", {"status": "success", "data": {"id": "f1", "mutation_applied": True}}),
        ("edit_file", {"status": "error", "error": "conflict"}),
        ("edit_file", {"status": "success", "data": {"id": "f1", "all_failed": True, "failed_edits": []}}),
        ("delete_file", {"error": "legacy failure"}),
        ("rename_file", {"status": "FAILED"}),
        ("create_file", {"id": "f2", "title": "第1章"}),
    ],
)
async def test_replayed_tool_result_has_the_live_frame_status(tool_name, payload):
    """停止后回放的工具结果与实时 tool_result 帧用同一套状态口径。"""
    import json

    from agent.core.round_outcome import committed_tool_result

    raw = {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
    frames = [
        frame
        async for frame in StreamAdapter()._handle_workflow_tool_result(
            {"name": tool_name, "tool_use_id": "call-1", "result": raw}
        )
        if frame.type.value == "tool_result"
    ]

    replayed = committed_tool_result(tool_name, raw)
    assert replayed is not None and frames
    assert replayed["status"] == frames[0].data["status"]
