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
