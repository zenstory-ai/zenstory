"""Actual SSE pump: finite paused-consumer characterization and compatibility."""

import asyncio
import json

import pytest

from agent.core.sse_pump import SSEStreamPump, StreamDeadlineExceeded


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [32, 256])
async def test_pump_paused_consumer_characterization(count, record_property):
    produced = 0
    reached = asyncio.Event()
    finished = asyncio.Event()

    async def source():
        nonlocal produced
        try:
            for index in range(count):
                produced += 1
                if produced == min(count, 34):
                    reached.set()
                yield f"frame-{index}"
        finally:
            finished.set()

    pump = SSEStreamPump(source(), heartbeat_interval_s=1)
    iterator = pump.__aiter__()
    frames = []
    try:
        frames.append(await iterator.__anext__())
        await asyncio.wait_for(reached.wait(), timeout=2)
        snapshot = {"count": count, "produced": produced, "consumed": len(frames),
                    "queued": pump._queue.qsize(), "producer_done": pump._task.done()}
        record_property("paused_queue", json.dumps(snapshot, sort_keys=True))
        assert snapshot["queued"] <= 32
        if count == 256:
            assert snapshot["queued"] == 32 and not snapshot["producer_done"]
        frames.extend([frame async for frame in iterator])
        assert frames == [f"frame-{index}" for index in range(count)]
        assert finished.is_set()
        await pump._task
    finally:
        await iterator.aclose()
        pump.cancel()
        if pump._task:
            await asyncio.gather(pump._task, return_exceptions=True)
        assert pump._task is None or pump._task.done()
        record_property("owned_cleanup", "actual producer finished; generator closed; no owned tasks remain")


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["done", "error"])
async def test_full_queue_delivers_terminal_fifo(ending):
    source_finished = asyncio.Event()
    original_error = RuntimeError("owned source failure")

    async def source():
        try:
            for index in range(33):
                yield str(index)
            if ending == "error":
                raise original_error
        finally:
            source_finished.set()

    pump = SSEStreamPump(source(), heartbeat_interval_s=1)
    iterator = pump.__aiter__()
    frames = []
    try:
        frames.append(await iterator.__anext__())
        await asyncio.wait_for(source_finished.wait(), timeout=2)
        assert pump._queue.qsize() == 32 and not pump._task.done()
        try:
            async for frame in iterator:
                frames.append(frame)
        except RuntimeError as error:
            assert ending == "error" and error is original_error
        else:
            assert ending == "done"
        assert frames == [str(index) for index in range(33)]
        await pump._task
    finally:
        await iterator.aclose()
        pump.cancel()
        if pump._task:
            await asyncio.gather(pump._task, return_exceptions=True)
        assert pump._task.done()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["close", "deadline", "close-error"])
async def test_blocked_producer_closes_source_and_preserves_cancellation(ending):
    reached = asyncio.Event()
    source_closed = asyncio.Event()

    async def source():
        try:
            for index in range(256):
                if index == 33:
                    reached.set()
                yield str(index)
        finally:
            source_closed.set()
            if ending == "close-error":
                raise RuntimeError("local close error must not replace cancellation")

    pump = SSEStreamPump(source(), heartbeat_interval_s=1,
                         deadline_s=0.03 if ending == "deadline" else None)
    iterator = pump.__aiter__()
    try:
        assert await iterator.__anext__() == "0"
        await asyncio.wait_for(reached.wait(), timeout=2)
        assert pump._queue.qsize() == 32 and not pump._task.done()
        assert not source_closed.is_set()
        if ending == "deadline":
            # Clock wait only after real queue-full barrier, not a throughput oracle.
            await asyncio.sleep(0.04)
            with pytest.raises(StreamDeadlineExceeded):
                await iterator.__anext__()
        else:
            await iterator.aclose()
        with pytest.raises(asyncio.CancelledError):
            await pump._task
        assert source_closed.is_set()
    finally:
        await iterator.aclose()
        pump.cancel()
        if pump._task:
            await asyncio.gather(pump._task, return_exceptions=True)
        assert pump._task.done()
