"""Tests for the in-band progress channel + steering rework.

Covers the pieces the original audit found unwired or unverified end-to-end:
- the in-band progress channel (agent.core.progress_channel)
- the runner's SDK/progress merge pump (_pump_sdk_events) and its contextvar
  propagation through run_openai_agents_streaming_agent
- steering messages being persisted to chat history (node-boundary durability)
"""

import asyncio
import sys
import types
from unittest.mock import patch

import pytest

from agent.core.progress_channel import (
    emit_progress,
    reset_progress_emitter,
    set_progress_emitter,
)


# ---------------------------------------------------------------------------
# progress_channel
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestProgressChannel:
    def test_emit_without_channel_is_noop(self):
        # No emitter installed -> returns False, never raises.
        assert emit_progress({"any": "event"}) is False

    def test_emit_with_channel_delivers_then_resets(self):
        captured: list = []
        token = set_progress_emitter(captured.append)
        try:
            assert emit_progress("e1") is True
            assert emit_progress("e2") is True
        finally:
            reset_progress_emitter(token)
        assert captured == ["e1", "e2"]
        # After reset the channel is gone again.
        assert emit_progress("e3") is False
        assert captured == ["e1", "e2"]

    def test_emit_swallows_emitter_errors(self):
        def boom(_event):
            raise RuntimeError("emitter failed")

        token = set_progress_emitter(boom)
        try:
            # Must not propagate the emitter's error to the caller.
            assert emit_progress("x") is False
        finally:
            reset_progress_emitter(token)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _event_value(event) -> str:
    """Normalize a workflow/SSE event's type to its string value."""
    event_type = getattr(event, "type", "")
    return getattr(event_type, "value", str(event_type))


# ---------------------------------------------------------------------------
# runner pump
# ---------------------------------------------------------------------------
def _drain(queue: asyncio.Queue) -> list:
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


class _FakeStream:
    def __init__(self, events, *, raise_after=None):
        self._events = events
        self._raise_after = raise_after

    async def stream_events(self):
        for event in self._events:
            await asyncio.sleep(0)
            yield event
        if self._raise_after is not None:
            raise self._raise_after


@pytest.mark.unit
class TestRunnerPump:
    async def test_forwards_events_then_done(self):
        from agent.openai_agents.runner import _pump_sdk_events

        queue: asyncio.Queue = asyncio.Queue()
        await _pump_sdk_events(_FakeStream(["a", "b"]), queue)
        assert _drain(queue) == [("sdk", "a"), ("sdk", "b"), ("done", None)]

    async def test_forwards_stream_exception_then_done(self):
        from agent.openai_agents.runner import _pump_sdk_events

        boom = RuntimeError("stream failed")
        queue: asyncio.Queue = asyncio.Queue()
        await _pump_sdk_events(_FakeStream(["a"], raise_after=boom), queue)
        items = _drain(queue)
        assert items[0] == ("sdk", "a")
        assert items[1][0] == "error" and items[1][1] is boom
        assert items[-1] == ("done", None)


# ---------------------------------------------------------------------------
# runner integration: SDK + live progress merge + steering injection
# ---------------------------------------------------------------------------
class _RawDelta:
    """Mimics a raw_response_event payload carrying a text delta."""

    def __init__(self, delta: str):
        self.type = "response.output_text.delta"
        self.delta = delta


class _RawResponseEvent:
    def __init__(self, delta: str):
        self.type = "raw_response_event"
        self.data = _RawDelta(delta)


class _FakeRunResult:
    def __init__(self, events):
        self._events = events
        self.raw_responses = []

    async def stream_events(self):
        for event in self._events:
            await asyncio.sleep(0)
            yield event

    def cancel(self, mode=None):  # pragma: no cover - not exercised here
        pass


def _install_fake_agents(monkeypatch, *, emit_events):
    """Install a fake openai-agents SDK that emits given progress events mid-run."""
    fake_agents = types.ModuleType("agents")

    class _Runner:
        @staticmethod
        def run_streamed(agent, input, max_turns, run_config, hooks=None):  # noqa: A002 - mirror SDK kwarg
            # Created while the runner has the progress emitter installed, so the
            # task's context copy can deliver progress events — exactly the
            # contextvar propagation the production runner relies on.
            async def _emit():
                await asyncio.sleep(0)
                for event in emit_events:
                    emit_progress(event)

            asyncio.create_task(_emit())
            return _FakeRunResult([_RawResponseEvent("hello "), _RawResponseEvent("world")])

    def _run_config(**kwargs):
        return {"run_config": kwargs}

    def _tool_execution_config(**kwargs):
        return {"tool_execution": kwargs}

    class _RunHooks:
        def __class_getitem__(cls, _item):
            return cls

    fake_agents.Runner = _Runner
    fake_agents.RunHooks = _RunHooks
    fake_agents.RunConfig = _run_config
    fake_agents.ToolExecutionConfig = _tool_execution_config

    fake_exceptions = types.ModuleType("agents.exceptions")

    class _MaxTurnsExceeded(Exception):
        pass

    fake_exceptions.MaxTurnsExceeded = _MaxTurnsExceeded

    monkeypatch.setitem(sys.modules, "agents", fake_agents)
    monkeypatch.setitem(sys.modules, "agents.exceptions", fake_exceptions)


@pytest.mark.unit
class TestRunnerIntegration:
    async def test_progress_events_interleave_with_sdk_text(self, monkeypatch):
        from agent.core.events import (
            parallel_end_event,
            parallel_start_event,
        )
        from agent.openai_agents.runner import run_openai_agents_streaming_agent

        _install_fake_agents(
            monkeypatch,
            emit_events=[
                parallel_start_event(execution_id="x", task_count=1, task_descriptions=["d"]),
                parallel_end_event(
                    execution_id="x", total_tasks=1, completed=1, failed=0, duration_ms=5
                ),
            ],
        )

        with patch("agent.openai_agents.runner._build_agent", return_value=object()):
            state = {"messages": [], "user_message": "hi"}
            collected = [
                event
                async for event in run_openai_agents_streaming_agent(state, "writer", "sys")
            ]

        values = [_event_value(e) for e in collected]
        # Normal SDK lifecycle still intact.
        assert "message_start" in values
        assert "message_end" in values
        assert "text" in values
        # Live in-tool progress surfaced through the merge.
        assert "parallel_start" in values
        assert "parallel_end" in values
        # Assistant text accumulated from the two deltas.
        text = "".join(
            e.data.get("text", "") for e in collected if _event_value(e) == "text"
        )
        assert text == "hello world"

    async def test_steering_injected_before_message_start(self, monkeypatch):
        from agent.openai_agents.runner import run_openai_agents_streaming_agent

        _install_fake_agents(monkeypatch, emit_events=[])

        async def get_steering_messages():
            return [{"id": "s1", "content": "把主角改名为林川"}]

        with patch("agent.openai_agents.runner._build_agent", return_value=object()):
            state = {"messages": [], "user_message": "继续写"}
            collected = [
                event
                async for event in run_openai_agents_streaming_agent(
                    state, "writer", "sys", get_steering_messages=get_steering_messages
                )
            ]

        values = [_event_value(e) for e in collected]
        assert "steering_received" in values
        # Steering is injected before the model run starts.
        assert values.index("steering_received") < values.index("message_start")


# ---------------------------------------------------------------------------
# steering persistence
# ---------------------------------------------------------------------------
@pytest.mark.unit
class TestSteeringPersistence:
    def test_steering_messages_saved_as_user_turns(self, db_session):
        from sqlmodel import select

        from agent.core.message_manager import MessageManager
        from models import ChatMessage, ChatSession, Project, User

        user = User(
            email="steer-persist@example.com",
            username="steer_persist",
            hashed_password="hashed_password",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        project = Project(name="Steer Project", owner_id=user.id, project_type="novel")
        db_session.add(project)
        db_session.flush()
        chat_session = ChatSession(project_id=project.id, user_id=user.id)
        db_session.add(chat_session)
        db_session.commit()

        manager = MessageManager(project_id=project.id, user_id=user.id)
        manager._save_messages_with_session(
            db_session,
            chat_session.id,
            "原始用户消息",
            "助手最终回复",
            None,
            None,
            steering_messages=["把主角改名为林川", "   ", "再加一条线索"],
        )

        rows = db_session.exec(select(ChatMessage)).all()
        contents = [r.content for r in rows]
        roles = [r.role for r in rows]

        # user + 2 non-empty steering + assistant; the whitespace-only one is dropped.
        assert len(rows) == 4
        assert roles.count("user") == 3
        assert roles.count("assistant") == 1
        assert "原始用户消息" in contents
        assert "把主角改名为林川" in contents
        assert "再加一条线索" in contents
        assert "助手最终回复" in contents
        assert "   " not in contents

        chat_session_id = rows[0].session_id
        from models import ChatSession

        chat_session = db_session.get(ChatSession, chat_session_id)
        # 2 base messages + 2 persisted steering turns.
        assert chat_session.message_count == 4

    def test_no_steering_messages_keeps_two_message_turns(self, db_session):
        from sqlmodel import select

        from agent.core.message_manager import MessageManager
        from models import ChatMessage, ChatSession, Project, User

        user = User(
            email="steer-none@example.com",
            username="steer_none",
            hashed_password="hashed_password",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)

        project = Project(name="No Steer Project", owner_id=user.id, project_type="novel")
        db_session.add(project)
        db_session.flush()
        chat_session = ChatSession(project_id=project.id, user_id=user.id)
        db_session.add(chat_session)
        db_session.commit()

        manager = MessageManager(project_id=project.id, user_id=user.id)
        manager._save_messages_with_session(
            db_session, chat_session.id, "你好", "你好，有什么可以帮你", None, None
        )

        rows = db_session.exec(select(ChatMessage)).all()
        assert len(rows) == 2
        assert {r.role for r in rows} == {"user", "assistant"}
