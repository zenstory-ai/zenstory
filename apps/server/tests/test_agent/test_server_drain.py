"""发布交接收尾：SIGTERM 挂钩、收尾提醒、泵时限提前。"""

import asyncio
import os
import signal

import pytest

from agent.core import server_drain
from agent.core.run_meter import AgentRunMeter
from agent.core.sse_pump import SSEStreamPump, StreamDeadlineExceeded


@pytest.fixture(autouse=True)
def _not_draining(monkeypatch):
    monkeypatch.setattr(server_drain, "_draining_since", None)
    monkeypatch.setattr(server_drain, "_listeners", [])


def test_sigterm_starts_the_drain_and_still_reaches_the_previous_handler():
    previous_calls = []
    scheduled = []
    original = signal.signal(signal.SIGTERM, lambda signum, frame: previous_calls.append(signum))
    try:
        server_drain.install_sigterm_drain(lambda callback: scheduled.append(callback))
        os.kill(os.getpid(), signal.SIGTERM)
    finally:
        signal.signal(signal.SIGTERM, original)

    assert previous_calls == [signal.SIGTERM]
    assert scheduled == [server_drain.begin_drain]


def test_drain_notifies_listeners_once_and_asks_runs_to_wrap_up():
    calls = []
    server_drain.add_drain_listener(lambda: calls.append("ended"))
    meter = AgentRunMeter(wrap_up_after_s=600)
    assert meter.past_wrap_up is False

    server_drain.begin_drain()
    server_drain.begin_drain()

    assert calls == ["ended"]
    assert meter.past_wrap_up is True
    assert 0 < server_drain.shutdown_budget_remaining_s() <= 290


@pytest.mark.asyncio
async def test_pump_deadline_can_only_move_up_and_ends_through_the_deadline_path():
    async def source():
        yield "event: session_started\ndata: {}\n\n"
        await asyncio.sleep(30)

    terminal = "event: error\ndata: {}\n\n"
    pump = SSEStreamPump(source(), heartbeat_interval_s=0.02, deadline_s=60, on_deadline=lambda: terminal)
    frames = []
    with pytest.raises(StreamDeadlineExceeded):
        async for frame in pump:
            frames.append(frame)
            if len(frames) == 1:
                pump.end_within(0.05)
                pump.end_within(100)  # never pushes the deadline back out

    assert pump.deadline_moved_up is True
    assert frames[0].startswith("event: session_started")
    assert frames[-1] == terminal
