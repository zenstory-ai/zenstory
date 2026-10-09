"""Tests for the parallel-execution + steering rework.

Covers the pieces the original audit found unwired or unverified end-to-end:
- the in-band progress channel (agent.core.progress_channel)
- parallel_execute emitting live parallel_* progress events
- the runner's SDK/progress merge pump (_pump_sdk_events) and its contextvar
  propagation through run_openai_agents_streaming_agent
- steering messages being persisted to chat history (node-boundary durability)
"""

import asyncio
import json
import sys
import types
from unittest.mock import patch

import pytest

from agent.core import progress_channel
from agent.core.progress_channel import (
    emit_progress,
    reset_progress_emitter,
    set_progress_emitter,
)
from agent.tools.parallel_executor import execute_parallel


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
# parallel_execute progress emission
# ---------------------------------------------------------------------------
def _event_value(event) -> str:
    """Normalize a workflow/SSE event's type to its string value."""
    event_type = getattr(event, "type", "")
    return getattr(event_type, "value", str(event_type))


@pytest.mark.unit
class TestParallelProgressEmission:
    async def test_emits_full_progress_sequence(self):
        from agent.tools.mcp_tools import ToolContext

        captured: list = []
        token = set_progress_emitter(captured.append)
        ToolContext.set_context(None, "user1", "proj-1", "sess-1")
        try:
            with patch("agent.tools.parallel_executor.handle_query_files") as mock_query:
                mock_query.return_value = {
                    "content": [{"type": "text", "text": '{"count": 1}'}]
                }
                await execute_parallel(
                    [
                        {"type": "query_files", "description": "Q1", "params": {}},
                        {"type": "query_files", "description": "Q2", "params": {}},
                    ]
                )
        finally:
            reset_progress_emitter(token)
            ToolContext.clear_context()

        values = [_event_value(e) for e in captured]
        # One start, one end per task, one overall end.
        assert values[0] == "parallel_start"
        assert values[-1] == "parallel_end"
        assert values.count("parallel_task_start") == 2
        assert values.count("parallel_task_end") == 2

        start = captured[0]
        assert start.data["task_count"] == 2
        assert start.data["task_descriptions"] == ["Q1", "Q2"]

        end = captured[-1]
        assert end.data["total_tasks"] == 2
        assert end.data["completed"] == 2
        assert end.data["failed"] == 0

    async def test_partial_failure_reported_in_progress_and_envelope(self):
        from agent.tools.mcp_tools import ToolContext

        captured: list = []
        token = set_progress_emitter(captured.append)
        ToolContext.set_context(None, "user1", "proj-1", "sess-1")
        try:
            # One success, one error payload.
            with patch("agent.tools.parallel_executor.handle_query_files") as mock_query:
                mock_query.side_effect = [
                    {"content": [{"type": "text", "text": '{"ok": true}'}]},
                    {"content": [{"type": "text", "text": '{"status":"error","error":"boom"}'}]},
                ]
                result = await execute_parallel(
                    [
                        {"type": "query_files", "description": "good", "params": {}},
                        {"type": "query_files", "description": "bad", "params": {}},
                    ]
                )
        finally:
            reset_progress_emitter(token)
            ToolContext.clear_context()

        end = captured[-1]
        assert _event_value(end) == "parallel_end"
        assert end.data["completed"] == 1
        assert end.data["failed"] == 1

        # Envelope stays "success" so the per-task breakdown survives the stream
        # adapter, but the failure is visible in the data fields.
        parsed = json.loads(result["content"][0]["text"])
        assert parsed["status"] == "success"
        assert parsed["data"]["any_failed"] is True
        assert parsed["data"]["failed"] == 1

        # The raw error stays in the model-facing breakdown; the task_end frame
        # the author sees never carries it (no user_message was provided).
        assert parsed["data"]["tasks"][1]["error"] == "boom"
        task_ends = [e for e in captured if _event_value(e) == "parallel_task_end"]
        failed = [e for e in task_ends if e.data["status"] == "failed"]
        assert len(failed) == 1
        assert "boom" not in (failed[0].data.get("error") or "")

    async def test_failed_task_end_shows_only_the_tools_user_message(self):
        from agent.tools.mcp_tools import ToolContext

        captured: list = []
        token = set_progress_emitter(captured.append)
        ToolContext.set_context(None, "user1", "proj-1", "sess-1")
        payload = json.dumps(
            {
                "status": "error",
                "error": "Edit 0: old text not found in file id=abc",
                "user_message": "AI 没在原文里找到要改的那一段，正在重新定位。",
            },
            ensure_ascii=False,
        )
        try:
            with patch("agent.tools.parallel_executor.handle_query_files") as mock_query:
                mock_query.return_value = {"content": [{"type": "text", "text": payload}]}
                result = await execute_parallel(
                    [{"type": "query_files", "description": "bad", "params": {}}]
                )
        finally:
            reset_progress_emitter(token)
            ToolContext.clear_context()

        parsed = json.loads(result["content"][0]["text"])
        # 模型仍拿到原始错误，好据此纠正。
        assert parsed["data"]["tasks"][0]["error"] == "Edit 0: old text not found in file id=abc"
        failed = [
            e for e in captured
            if _event_value(e) == "parallel_task_end" and e.data["status"] == "failed"
        ]
        assert failed[0].data["error"] == "AI 没在原文里找到要改的那一段，正在重新定位。"

    async def test_no_emitter_does_not_break_execution(self):
        from agent.tools.mcp_tools import ToolContext

        ToolContext.set_context(None, "user1", "proj-1", "sess-1")
        try:
            with patch("agent.tools.parallel_executor.handle_query_files") as mock_query:
                mock_query.return_value = {
                    "content": [{"type": "text", "text": '{"count": 0}'}]
                }
                result = await execute_parallel(
                    [{"type": "query_files", "description": "Q", "params": {}}]
                )
            parsed = json.loads(result["content"][0]["text"])
            assert parsed["status"] == "success"
            assert parsed["data"]["total_tasks"] == 1
        finally:
            ToolContext.clear_context()


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
        from agent.core.message_manager import MessageManager
        from models import ChatMessage, Project, User
        from sqlmodel import select

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
        db_session.commit()
        db_session.refresh(project)

        manager = MessageManager(project_id=project.id, user_id=user.id)
        manager._save_messages_with_session(
            db_session,
            None,
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
        from agent.core.message_manager import MessageManager
        from models import ChatMessage, Project, User
        from sqlmodel import select

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
        db_session.commit()
        db_session.refresh(project)

        manager = MessageManager(project_id=project.id, user_id=user.id)
        manager._save_messages_with_session(
            db_session, None, "你好", "你好，有什么可以帮你", None, None
        )

        rows = db_session.exec(select(ChatMessage)).all()
        assert len(rows) == 2
        assert {r.role for r in rows} == {"user", "assistant"}
