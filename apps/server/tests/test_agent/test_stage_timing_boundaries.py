"""Regression tests for request-level timing boundaries.

The tests use a tiny recorder instead of wall-clock assertions.  They verify
which operations are eligible for timing and for the first-success milestone;
they do not exercise a model provider or persist real author content.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest


class RecordingMeter:
    def __init__(self) -> None:
        self.stages: list[str] = []
        self.first_tool_marks = 0
        self.first_body_marks = 0

    @contextmanager
    def measure_stage(self, stage: str):
        self.stages.append(stage)
        yield

    def mark_first_tool_started(self) -> None:
        self.first_tool_marks += 1

    def mark_first_body_persisted(self) -> None:
        self.first_body_marks += 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_timing_starts_only_after_guards_allow_execution():
    from agent.openai_agents.tools_adapter import build_agent_function_tools

    meter = RecordingMeter()
    executed: list[dict] = []

    async def fake_tool(args):
        executed.append(args)
        return {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps({"status": "success"}),
                }
            ]
        }

    with patch.dict(
        "agent.openai_agents.tools_adapter.TOOL_FUNCTIONS",
        {"edit_file": fake_tool, "query_files": fake_tool},
    ):
        reviewer_tools = {
            tool.name: tool
            for tool in build_agent_function_tools(
                "quality_reviewer",
                run_meter=meter,
            )
        }
        refused = await reviewer_tools["edit_file"].on_invoke_tool(
            None,
            '{"id":"draft-1","edits":[]}',
        )
        allowed = await reviewer_tools["query_files"].on_invoke_tool(
            None,
            '{"ids":["draft-1"]}',
        )

    assert json.loads(refused)["status"] == "error"
    assert json.loads(allowed)["status"] == "success"
    assert executed == [{"ids": ["draft-1"]}]
    assert meter.first_tool_marks == 1
    assert meter.stages == ["tool"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_timing_closes_when_execution_raises_internally():
    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.tools_adapter import invoke_project_tool

    meter = AgentRunMeter()

    async def failing_tool(_args):
        raise RuntimeError("synthetic failure")

    with patch.dict(
        "agent.openai_agents.tools_adapter.TOOL_FUNCTIONS",
        {"synthetic_tool": failing_tool},
        clear=True,
    ):
        result = await invoke_project_tool(
            "synthetic_tool",
            "{}",
            run_meter=meter,
        )

    assert json.loads(result)["status"] == "error"
    assert meter.first_tool_started_ms is not None
    assert meter.timing_report()["stage_timings"]["tool"]["error_count"] == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_invalid_tool_input_and_unknown_handler_do_not_start_tool_timing():
    from agent.openai_agents.tools_adapter import invoke_project_tool

    meter = RecordingMeter()
    executed: list[dict] = []

    async def fake_tool(args):
        executed.append(args)
        return {"status": "success"}

    with patch.dict(
        "agent.openai_agents.tools_adapter.TOOL_FUNCTIONS",
        {"synthetic_tool": fake_tool},
        clear=True,
    ):
        malformed = await invoke_project_tool(
            "synthetic_tool",
            "not json",
            run_meter=meter,
        )
        unknown = await invoke_project_tool(
            "missing_tool",
            "{}",
            run_meter=meter,
        )

    assert json.loads(malformed)["error_type"] == "invalid_tool_input_json"
    assert json.loads(unknown)["status"] == "error"
    assert executed == []
    assert meter.first_tool_marks == 0
    assert meter.stages == []


@pytest.mark.asyncio
@pytest.mark.unit
async def test_repaired_tool_input_starts_timing_and_executes_handler():
    from agent.openai_agents.tools_adapter import invoke_project_tool

    meter = RecordingMeter()
    executed: list[dict] = []

    async def fake_tool(args):
        executed.append(args)
        return {"status": "success"}

    with patch.dict(
        "agent.openai_agents.tools_adapter.TOOL_FUNCTIONS",
        {"synthetic_tool": fake_tool},
        clear=True,
    ):
        result = await invoke_project_tool(
            "synthetic_tool",
            "{value: 7}",
            run_meter=meter,
        )

    assert json.loads(result)["status"] == "success"
    assert executed == [{"value": 7}]
    assert meter.first_tool_marks == 1
    assert meter.stages == ["tool"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_timing_ignores_circuit_and_read_guard_short_circuits():
    from agent.openai_agents.tools_adapter import build_agent_function_tools

    class OpenBreaker:
        is_open = True

        @staticmethod
        def short_circuit_text(_tool_name):
            return json.dumps({"status": "error", "error_type": "circuit_open"})

    class BlockingReadGuard:
        is_open = False

        @staticmethod
        def plan(_tool_name, _arguments):
            return SimpleNamespace(
                arguments="{}",
                blocked_output=json.dumps({"status": "error", "error_type": "repeated_read"}),
            )

    meter = RecordingMeter()
    breaker_tools = {
        tool.name: tool
        for tool in build_agent_function_tools(
            "writer",
            failure_breaker=OpenBreaker(),
            run_meter=meter,
        )
    }
    guard_tools = {
        tool.name: tool
        for tool in build_agent_function_tools(
            "writer",
            read_guard=BlockingReadGuard(),
            run_meter=meter,
        )
    }

    assert json.loads(await breaker_tools["query_files"].on_invoke_tool(None, "{}"))["status"] == "error"
    assert json.loads(await guard_tools["query_files"].on_invoke_tool(None, "{}"))["status"] == "error"
    assert meter.first_tool_marks == 0
    assert meter.stages == []


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("file_type", "content", "save_result", "expected_mark"),
    [
        ("draft", "第一章正文", (True, True), 1),
        ("script", "第一集正文", (True, True), 1),
        ("outline", "第一章规划", (True, True), 0),
        ("draft", "   ", (True, True), 0),
        ("draft", "没有变化", (True, False), 0),
        ("draft", "保存失败", (False, True), 0),
    ],
)
async def test_stream_save_marks_only_changed_nonempty_body_files(
    monkeypatch,
    file_type,
    content,
    save_result,
    expected_mark,
):
    from agent.stream_adapter import create_stream_adapter

    meter = RecordingMeter()
    adapter = create_stream_adapter(run_meter=meter)
    monkeypatch.setattr(
        adapter,
        "_save_file_content_sync",
        lambda _file_id, _content: save_result,
    )

    saved = await adapter._save_file_content(
        "synthetic-file",
        content,
        file_type=file_type,
    )

    assert saved is save_result[0]
    assert meter.stages == ["file_save"]
    assert meter.first_body_marks == expected_mark


@pytest.mark.asyncio
@pytest.mark.unit
async def test_aborted_body_save_keeps_file_type_for_first_persist_mark(monkeypatch):
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.stream_adapter import create_stream_adapter

    meter = RecordingMeter()
    adapter = create_stream_adapter(run_meter=meter)
    monkeypatch.setattr(
        adapter,
        "_save_file_content_sync",
        lambda _file_id, _content: (True, True),
    )

    create_result = {
        "name": "create_file",
        "result": {
            "status": "success",
            "data": {
                "id": "draft-1",
                "file_type": "draft",
                "title": "第一章",
                "content": "",
            },
        },
    }
    async for _ in adapter._process_workflow_event(
        StreamEvent(type=StreamEventType.TOOL_RESULT, data=create_result)
    ):
        pass
    async for _ in adapter._process_workflow_event(
        StreamEvent(
            type=StreamEventType.TEXT,
            data={"text": "<file>尚未闭合但应补存的正文"},
        )
    ):
        pass

    task = adapter._persist_active_capture_in_background()
    assert task is not None
    adapter.reset()
    assert await task is True
    assert meter.stages == ["file_save"]
    assert meter.first_body_marks == 1


@pytest.mark.asyncio
@pytest.mark.unit
async def test_file_save_timing_closes_on_cancellation(monkeypatch):
    from agent.stream_adapter import create_stream_adapter

    meter = RecordingMeter()
    adapter = create_stream_adapter(run_meter=meter)
    started = asyncio.Event()
    never_finishes = asyncio.Event()

    async def blocked_to_thread(*_args, **_kwargs):
        started.set()
        await never_finishes.wait()

    monkeypatch.setattr(asyncio, "to_thread", blocked_to_thread)

    task = asyncio.create_task(
        adapter._save_file_content(
            "draft-1",
            "正文",
            file_type="draft",
        )
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert meter.stages == ["file_save"]
    assert meter.first_body_marks == 0


@pytest.mark.asyncio
@pytest.mark.unit
async def test_executed_tool_business_failures_reach_the_run_summary_without_content():
    """edit_file failures (a whole batch rolled back by default) are counted, not their text."""
    from agent.core.run_meter import AgentRunMeter
    from agent.openai_agents.tools_adapter import invoke_project_tool

    meter = AgentRunMeter()
    results = iter([
        {"status": "error", "error_type": "old_text_not_found", "error": "private-old-text-must-not-appear"},
        {"status": "error", "error_type": "stale_content", "error": "private"},
        {"status": "success", "data": {"new_word_count": 10}},
        {"status": "error", "error_type": "Not An Identifier!", "error": "private"},
        {"status": "partial", "data": {"failed_edits": [{"old": "private"}]}},
        {"status": "error", "error_type": None, "error": "private"},
    ])

    async def fake_edit(_args):
        return next(results)

    with patch.dict("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"edit_file": fake_edit}, clear=True):
        batch = json.dumps({"id": "draft-1", "edits": [{"op": "replace", "old": "private-a", "new": "b"}] * 23})
        single = json.dumps({"id": "draft-1", "edits": [{"op": "replace", "old": "private-a", "new": "b"}]})
        keep_going = json.dumps({
            "id": "draft-1", "continue_on_error": True, "edits": [{"op": "replace", "old": "private-a", "new": "b"}] * 3,
        })
        for arguments in (batch, single, single, single, keep_going, keep_going):
            await invoke_project_tool("edit_file", arguments, run_meter=meter)

    report = meter.timing_report()
    assert report["tool_failures"] == {
        "edit_file": {
            "count": 5,
            # continue_on_error batches are not rolled back as a whole.
            "rolled_back_batches": 1,
            "error_types": {"old_text_not_found": 1, "stale_content": 1, "other": 2, "partial": 1},
        }
    }
    # Returned failures are not exceptions: the tool stage error_count stays for raised errors.
    assert report["stage_timings"]["tool"]["error_count"] == 0
    assert "private" not in json.dumps(report)


@pytest.mark.unit
def test_tool_failure_error_types_stay_bounded():
    from agent.core.run_meter import AgentRunMeter

    meter = AgentRunMeter()
    for index in range(20):
        meter.record_tool_failure("edit_file", f"type_{index}", rolled_back_batch=False)
    error_types = meter.timing_report()["tool_failures"]["edit_file"]["error_types"]
    assert len(error_types) == 9  # eight distinct ids plus "other"
    assert error_types["other"] == 12
