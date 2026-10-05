"""
Tests for agent/graph/writing_graph.py
"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.tools.mcp_tools import ToolContext


@pytest.mark.unit
class TestWritingGraphCompletionHooks:
    """Tests for workflow completion side effects."""

    @pytest.mark.asyncio
    async def test_task_complete_triggers_update_project_for_in_progress_tasks(self):
        """Workflow completion should auto-call update_project to close in_progress tasks."""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        async def fake_run_streaming_agent(*_args, **_kwargs):
            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "任务完成 [TASK_COMPLETE]"},
            )

        mock_update_project = AsyncMock(
            return_value={
                "content": [{
                    "type": "text",
                    "text": json.dumps(
                        {"status": "success", "data": {"plan": {"status": "success"}}},
                        ensure_ascii=False,
                    ),
                }]
            }
        )

        state = {
            "user_message": "写一段内容",
            "messages": [],
            "system_prompt": "",
        }

        ToolContext.set_context(
            session=None,
            user_id="user-1",
            project_id="project-1",
            session_id="session-1",
        )
        try:
            with (
                patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
                patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
                patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
                patch(
                    "services.infra.task_board_service.task_board_service.get_tasks",
                    return_value=[
                        {"task": "step-1", "status": "done"},
                        {"task": "step-2", "status": "in_progress"},
                        {"task": "step-3", "status": "pending"},
                    ],
                ),
                patch("agent.graph.writing_graph.update_project", mock_update_project),
            ):
                events = [
                    event async for event in run_writing_workflow_streaming(
                        state=state,
                        thread_id="thread-1",
                    )
                ]
        finally:
            ToolContext.clear_context()

        mock_update_project.assert_awaited_once()
        updated_tasks = mock_update_project.await_args.args[0]["tasks"]
        assert updated_tasks[0]["status"] == "done"
        assert updated_tasks[1]["status"] == "done"
        assert updated_tasks[2]["status"] == "pending"

        assert any(
            event.type == StreamEventType.TOOL_USE
            and event.data.get("name") == "update_project"
            for event in events
        )
        assert any(
            event.type == StreamEventType.TOOL_RESULT
            and event.data.get("name") == "update_project"
            for event in events
        )
        assert any(event.type == StreamEventType.WORKFLOW_COMPLETE for event in events)

    @pytest.mark.asyncio
    async def test_task_complete_without_in_progress_tasks_skips_auto_update(self):
        """No in_progress task means no auto update_project call."""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        async def fake_run_streaming_agent(*_args, **_kwargs):
            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "任务完成 [TASK_COMPLETE]"},
            )

        mock_update_project = AsyncMock()
        state = {
            "user_message": "写一段内容",
            "messages": [],
            "system_prompt": "",
        }

        ToolContext.set_context(
            session=None,
            user_id="user-1",
            project_id="project-1",
            session_id="session-2",
        )
        try:
            with (
                patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
                patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
                patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
                patch(
                    "services.infra.task_board_service.task_board_service.get_tasks",
                    return_value=[
                        {"task": "step-1", "status": "done"},
                        {"task": "step-2", "status": "pending"},
                    ],
                ),
                patch("agent.graph.writing_graph.update_project", mock_update_project),
            ):
                events = [
                    event async for event in run_writing_workflow_streaming(
                        state=state,
                        thread_id="thread-2",
                    )
                ]
        finally:
            ToolContext.clear_context()

        assert mock_update_project.await_count == 0
        assert not any(
            event.type == StreamEventType.TOOL_RESULT
            and event.data.get("name") == "update_project"
            for event in events
        )
        assert any(event.type == StreamEventType.WORKFLOW_COMPLETE for event in events)


@pytest.mark.unit
class TestWritingGraphRouterFallback:
    """Tests for router fallback defaults in writing workflow."""

    @staticmethod
    async def _fake_run_streaming_agent(*_args, **_kwargs):
        yield StreamEvent(
            type=StreamEventType.TEXT,
            data={"text": "fallback done [TASK_COMPLETE]"},
        )

    @pytest.mark.asyncio
    async def test_validation_error_fallback_uses_quick_workflow_plan(self):
        """ValueError/KeyError fallback should use quick workflow naming."""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        with (
            patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "llm"}),
            patch("agent.graph.writing_graph.router_node", AsyncMock(side_effect=ValueError("invalid payload"))),
            patch("agent.graph.writing_graph.run_streaming_agent", new=self._fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state={"user_message": "测试", "messages": [], "system_prompt": ""},
                    thread_id="router-validation-fallback",
                )
            ]

        decided_event = next(event for event in events if event.type == StreamEventType.ROUTER_DECIDED)
        assert decided_event.data["initial_agent"] == "writer"
        assert decided_event.data["workflow_plan"] == "quick"
        assert decided_event.data["routing_metadata"]["workflow_type"] == "quick"
        assert any(event.type == StreamEventType.WORKFLOW_COMPLETE for event in events)

    @pytest.mark.asyncio
    async def test_exception_fallback_uses_quick_workflow_plan(self):
        """Generic router exception fallback should use quick workflow naming."""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        with (
            patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "llm"}),
            patch("agent.graph.writing_graph.router_node", AsyncMock(side_effect=RuntimeError("router boom"))),
            patch("agent.graph.writing_graph.run_streaming_agent", new=self._fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state={"user_message": "测试", "messages": [], "system_prompt": ""},
                    thread_id="router-exception-fallback",
                )
            ]

        decided_event = next(event for event in events if event.type == StreamEventType.ROUTER_DECIDED)
        assert decided_event.data["initial_agent"] == "writer"
        assert decided_event.data["workflow_plan"] == "quick"
        assert decided_event.data["routing_metadata"]["workflow_type"] == "quick"
        assert any(event.type == StreamEventType.WORKFLOW_COMPLETE for event in events)


@pytest.mark.unit
class TestWritingGraphGenerationModeOverrides:
    """Tests for per-request generation_mode overrides (fast/quality)."""

    @staticmethod
    async def _fake_run_streaming_agent(*_args, **_kwargs):
        yield StreamEvent(
            type=StreamEventType.TEXT,
            data={"text": "done [TASK_COMPLETE]"},
        )

    @pytest.mark.asyncio
    async def test_generation_mode_fast_skips_router_llm(self):
        from agent.graph.writing_graph import run_writing_workflow_streaming

        mock_router = AsyncMock(return_value={})

        with (
            patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "llm"}),
            patch("agent.graph.writing_graph.router_node", mock_router),
            patch("agent.graph.writing_graph.run_streaming_agent", new=self._fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state={
                        "user_message": "测试",
                        "messages": [],
                        "system_prompt": "",
                        "generation_mode": "fast",
                    },
                    thread_id="generation-mode-fast",
                )
            ]

        assert mock_router.await_count == 0

        decided_event = next(event for event in events if event.type == StreamEventType.ROUTER_DECIDED)
        assert decided_event.data["initial_agent"] == "writer"
        assert decided_event.data["workflow_plan"] == "quick"
        assert decided_event.data["routing_metadata"]["reason"] == "generation_mode_fast"

    @pytest.mark.asyncio
    async def test_generation_mode_quality_forces_router_llm(self):
        from agent.graph.writing_graph import run_writing_workflow_streaming

        mock_router = AsyncMock(
            return_value={
                "current_agent": "writer",
                "workflow_plan": "quick",
                "workflow_agents": [],
                "routing_metadata": {
                    "agent_type": "writer",
                    "workflow_type": "quick",
                    "reason": "router_test",
                    "confidence": 1.0,
                },
            }
        )

        with (
            patch.dict("os.environ", {"AGENT_ROUTER_STRATEGY": "off"}),
            patch("agent.graph.writing_graph.router_node", mock_router),
            patch("agent.graph.writing_graph.run_streaming_agent", new=self._fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state={
                        "user_message": "测试",
                        "messages": [],
                        "system_prompt": "",
                        "generation_mode": "quality",
                    },
                    thread_id="generation-mode-quality",
                )
            ]

        assert mock_router.await_count == 1

        decided_event = next(event for event in events if event.type == StreamEventType.ROUTER_DECIDED)
        assert decided_event.data["initial_agent"] == "writer"
        assert decided_event.data["workflow_plan"] == "quick"
        assert decided_event.data["routing_metadata"]["reason"] == "router_test"


@pytest.mark.unit
class TestWritingGraphHandoffPriority:
    """Tests for stop/complete priority when explicit handoff exists."""

    @staticmethod
    def _router_result() -> dict[str, object]:
        return {
            "current_agent": "writer",
            "workflow_plan": "quick",
            "workflow_agents": [],
            "routing_metadata": {
                "agent_type": "writer",
                "workflow_type": "quick",
                "reason": "test",
                "confidence": 1.0,
            },
        }

    @pytest.mark.asyncio
    async def test_explicit_handoff_overrides_explicit_complete_marker(self):
        """Explicit handoff should win over [TASK_COMPLETE] in the same turn."""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        async def fake_run_streaming_agent(_state, agent_type, **_kwargs):
            if agent_type == "writer":
                yield StreamEvent(
                    type=StreamEventType.TEXT,
                    data={"text": "完成初稿，交接审稿 [TASK_COMPLETE]"},
                )
                yield StreamEvent(
                    type=StreamEventType.HANDOFF,
                    data={
                        "target_agent": "quality_reviewer",
                        "reason": "继续质量审查",
                        "context": "请检查逻辑一致性",
                    },
                )
                return

            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "审稿阶段处理中"},
            )

        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=self._router_result())),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state={"user_message": "测试", "messages": [], "system_prompt": ""},
                    thread_id="handoff-complete-priority",
                    max_iterations=3,
                )
            ]

        selected_agents = [
            event.data["agent_type"]
            for event in events
            if event.type == StreamEventType.AGENT_SELECTED
        ]
        assert selected_agents[:2] == ["writer", "quality_reviewer"]
        assert any(event.type == StreamEventType.HANDOFF for event in events)
        assert not any(event.type == StreamEventType.WORKFLOW_COMPLETE for event in events)
        assert not any(event.type == StreamEventType.WORKFLOW_STOPPED for event in events)

    @pytest.mark.asyncio
    async def test_structured_clarification_stops_planned_handoff(self):
        """Structured WORKFLOW_STOPPED clarification should block planned handoff."""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        async def fake_run_streaming_agent(_state, agent_type, **_kwargs):
            if agent_type == "writer":
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "需要补充信息"})
                yield StreamEvent(
                    type=StreamEventType.WORKFLOW_STOPPED,
                    data={
                        "reason": "clarification_needed",
                        "agent_type": "writer",
                        "message": "请确认主角姓名",
                    },
                )
                return

            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "审稿阶段处理中"},
            )

        router_result = self._router_result()
        router_result["workflow_agents"] = ["quality_reviewer"]

        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=router_result)),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state={"user_message": "测试", "messages": [], "system_prompt": ""},
                    thread_id="structured-clarify-stop",
                    max_iterations=3,
                )
            ]

        selected_agents = [
            event.data["agent_type"]
            for event in events
            if event.type == StreamEventType.AGENT_SELECTED
        ]
        assert selected_agents == ["writer"]
        assert any(event.type == StreamEventType.WORKFLOW_STOPPED for event in events)
        assert not any(event.type == StreamEventType.HANDOFF for event in events)
        assert not any(event.type == StreamEventType.WORKFLOW_COMPLETE for event in events)

    @pytest.mark.asyncio
    async def test_structured_clarification_stops_explicit_handoff_same_turn(self):
        """Structured clarification must suppress explicit handoff emitted in same turn."""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        async def fake_run_streaming_agent(_state, agent_type, **_kwargs):
            if agent_type == "writer":
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "处理中"})
                yield StreamEvent(
                    type=StreamEventType.HANDOFF,
                    data={
                        "target_agent": "quality_reviewer",
                        "reason": "先审查",
                        "context": "draft done",
                        "handoff_packet": {
                            "target_agent": "quality_reviewer",
                            "reason": "先审查",
                            "context": "draft done",
                            "completed": ["初稿完成"],
                            "todo": ["审查逻辑"],
                            "evidence": ["draft/ch1.md"],
                        },
                    },
                )
                yield StreamEvent(
                    type=StreamEventType.WORKFLOW_STOPPED,
                    data={
                        "reason": "clarification_needed",
                        "agent_type": "writer",
                        "message": "请补充世界观年代",
                    },
                )
                return

            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "should not run"})

        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=self._router_result())),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state={"user_message": "测试", "messages": [], "system_prompt": ""},
                    thread_id="explicit-handoff-clarify-stop",
                    max_iterations=3,
                )
            ]

        selected_agents = [
            event.data["agent_type"]
            for event in events
            if event.type == StreamEventType.AGENT_SELECTED
        ]
        assert selected_agents == ["writer"]
        assert any(
            event.type == StreamEventType.WORKFLOW_STOPPED
            and event.data.get("reason") == "clarification_needed"
            for event in events
        )
        assert not any(event.type == StreamEventType.HANDOFF for event in events)

    @pytest.mark.asyncio
    async def test_tool_call_exhausted_stops_planned_handoff(self):
        """Tool-call exhaustion must block any planned/auto handoff."""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        async def fake_run_streaming_agent(_state, agent_type, **_kwargs):
            if agent_type == "writer":
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "处理中"})
                yield StreamEvent(
                    type=StreamEventType.ITERATION_EXHAUSTED,
                    data={
                        "layer": "tool_call",
                        "iterations_used": 10,
                        "max_iterations": 10,
                        "reason": "tool call exhausted",
                        "last_agent": "writer",
                    },
                )
                return

            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "should not run"})

        router_result = self._router_result()
        router_result["workflow_agents"] = ["quality_reviewer"]

        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=router_result)),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state={"user_message": "测试", "messages": [], "system_prompt": ""},
                    thread_id="tool-call-exhausted-stop",
                    max_iterations=3,
                )
            ]

        selected_agents = [
            event.data["agent_type"]
            for event in events
            if event.type == StreamEventType.AGENT_SELECTED
        ]
        assert selected_agents == ["writer"]
        assert any(
            event.type == StreamEventType.ITERATION_EXHAUSTED
            and event.data.get("layer") == "tool_call"
            for event in events
        )
        assert not any(event.type == StreamEventType.HANDOFF for event in events)

    @pytest.mark.asyncio
    async def test_self_handoff_stops_workflow(self):
        """Self handoff should be rejected to avoid collaboration loops."""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        async def fake_run_streaming_agent(_state, agent_type, **_kwargs):
            if agent_type == "writer":
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "修改中"})
                yield StreamEvent(
                    type=StreamEventType.HANDOFF,
                    data={
                        "target_agent": "writer",
                        "reason": "继续修改",
                        "context": "self",
                    },
                )
                return

            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "should not run"})

        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=self._router_result())),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state={"user_message": "测试", "messages": [], "system_prompt": ""},
                    thread_id="self-handoff-stop",
                    max_iterations=3,
                )
            ]

        selected_agents = [
            event.data["agent_type"]
            for event in events
            if event.type == StreamEventType.AGENT_SELECTED
        ]
        assert selected_agents == ["writer"]
        assert any(
            event.type == StreamEventType.WORKFLOW_STOPPED
            and event.data.get("reason") == "invalid_handoff"
            for event in events
        )
        assert not any(event.type == StreamEventType.HANDOFF for event in events)

    @pytest.mark.asyncio
    async def test_task_complete_without_session_id_skips_auto_update(self):
        """Missing session_id should skip auto task-board finalize safely."""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        async def fake_run_streaming_agent(*_args, **_kwargs):
            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "任务完成 [TASK_COMPLETE]"},
            )

        mock_update_project = AsyncMock()
        state = {
            "user_message": "写一段内容",
            "messages": [],
            "system_prompt": "",
        }

        ToolContext.set_context(
            session=None,
            user_id="user-1",
            project_id="project-1",
            session_id=None,
        )
        try:
            with (
                patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
                patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
                patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
                patch("agent.graph.writing_graph.update_project", mock_update_project),
            ):
                events = [
                    event async for event in run_writing_workflow_streaming(
                        state=state,
                        thread_id="thread-3",
                    )
                ]
        finally:
            ToolContext.clear_context()

        assert mock_update_project.await_count == 0
        assert any(event.type == StreamEventType.WORKFLOW_COMPLETE for event in events)


@pytest.mark.unit
class TestWritingGraphAutoReviewGate:
    """Ensure graph-level auto-review does not hijack long non-writing replies."""

    @staticmethod
    def _router_result() -> dict[str, object]:
        return {
            "current_agent": "writer",
            "workflow_plan": "quick",
            "workflow_agents": [],
            "routing_metadata": {
                "agent_type": "writer",
                "workflow_type": "quick",
                "reason": "test",
                "confidence": 1.0,
            },
        }

    @pytest.mark.asyncio
    async def test_auto_review_skipped_for_non_writing_request(self):
        from agent.graph.writing_graph import run_writing_workflow_streaming

        async def fake_run_streaming_agent(*_args, **_kwargs):
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "x" * 200})

        with (
            patch.dict("os.environ", {"AGENT_ENABLE_GRAPH_AUTO_REVIEW": "true"}),
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=self._router_result())),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state={
                        "user_message": "请解释一下函数式编程的优缺点",
                        "router_message": "请解释一下函数式编程的优缺点",
                        "messages": [],
                        "system_prompt": "",
                    },
                    thread_id="auto-review-skip-non-writing",
                    max_iterations=2,
                    auto_review_threshold=50,
                )
            ]

        assert not any(event.type == StreamEventType.HANDOFF for event in events)

    @pytest.mark.asyncio
    async def test_auto_review_includes_writer_content_for_reviewer_context(self):
        from agent.graph.writing_graph import run_writing_workflow_streaming

        captured_review_task: dict[str, str] = {}

        async def fake_run_streaming_agent(state, agent_type, **_kwargs):
            if agent_type == "writer":
                # Simulate writer producing a deliverable draft via <file> streaming protocol.
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"<file>{'a' * 200}</file>"})
                return
            if agent_type == "quality_reviewer":
                captured_review_task["user_message"] = str(state.get("user_message") or "")
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "review ok [TASK_COMPLETE]"})
                return

            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "noop"})

        with (
            patch.dict("os.environ", {"AGENT_ENABLE_GRAPH_AUTO_REVIEW": "true"}),
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=self._router_result())),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state={
                        "user_message": "帮我写一段小说开头",
                        "router_message": "帮我写一段小说开头",
                        "messages": [],
                        "system_prompt": "",
                    },
                    thread_id="auto-review-context-includes-draft",
                    max_iterations=3,
                    auto_review_threshold=50,
                )
            ]

        handoff_event = next(event for event in events if event.type == StreamEventType.HANDOFF)
        assert "[待审查内容]" not in handoff_event.data.get("context", "")

        review_user_message = captured_review_task.get("user_message", "")
        assert "[待审查内容]" in review_user_message
        assert "a" * 50 in review_user_message
        assert any(event.type == StreamEventType.WORKFLOW_COMPLETE for event in events)

    @pytest.mark.asyncio
    async def test_task_complete_update_project_failure_does_not_block_workflow(self):
        """Auto finalize failures should not block WORKFLOW_COMPLETE."""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        async def fake_run_streaming_agent(*_args, **_kwargs):
            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "任务完成 [TASK_COMPLETE]"},
            )

        mock_update_project = AsyncMock(side_effect=RuntimeError("update_project failed"))
        state = {
            "user_message": "写一段内容",
            "messages": [],
            "system_prompt": "",
        }

        ToolContext.set_context(
            session=None,
            user_id="user-1",
            project_id="project-1",
            session_id="session-4",
        )
        try:
            with (
                patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
                patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
                patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
                patch(
                    "services.infra.task_board_service.task_board_service.get_tasks",
                    return_value=[{"task": "step-2", "status": "in_progress"}],
                ),
                patch("agent.graph.writing_graph.update_project", mock_update_project),
            ):
                events = [
                    event async for event in run_writing_workflow_streaming(
                        state=state,
                        thread_id="thread-4",
                    )
                ]
        finally:
            ToolContext.clear_context()

        mock_update_project.assert_awaited_once()
        # failure path should not emit tool_result, but workflow must still complete
        assert not any(
            event.type == StreamEventType.TOOL_RESULT
            and event.data.get("name") == "update_project"
            for event in events
        )
        assert any(event.type == StreamEventType.WORKFLOW_COMPLETE for event in events)


@pytest.mark.unit
class TestWritingGraphFileCorrection:
    """The workflow must re-run the writer when it leaves a created file empty."""

    @pytest.mark.asyncio
    async def test_empty_file_triggers_writer_rerun_with_correction(self):
        from agent.graph.writing_graph import run_writing_workflow_streaming

        calls: list[dict] = []

        async def fake_run_streaming_agent(state, agent_type, *_args, **_kwargs):
            calls.append({"agent": agent_type, "user_message": state.get("user_message", "")})
            if len(calls) == 1:
                # Writer created an empty file (guard set) but only narrated and
                # marked complete — it never streamed the <file> body.
                ToolContext.set_pending_empty_file("file-X", "第51章")
                yield StreamEvent(
                    type=StreamEventType.TEXT,
                    data={"text": "正文文件已创建，需直接写入内容。[TASK_COMPLETE]"},
                )
            else:
                # Corrective re-run finishes the file (clears the guard).
                ToolContext.clear_pending_empty_file()
                yield StreamEvent(
                    type=StreamEventType.TEXT,
                    data={"text": "已用 edit_file 补全正文。[TASK_COMPLETE]"},
                )

        state = {"user_message": "写第51章", "messages": [], "system_prompt": ""}

        ToolContext.set_context(
            session=None, user_id="u", project_id="p", session_id="s",
        )
        try:
            with (
                patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
                patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
                patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
            ):
                events = [
                    event async for event in run_writing_workflow_streaming(
                        state=state, thread_id="t",
                    )
                ]
        finally:
            ToolContext.clear_context()

        # The writer must have run twice: the original turn + one correction.
        assert len(calls) == 2
        assert calls[0]["agent"] == "writer"
        assert calls[1]["agent"] == "writer"
        # The correction turn must carry the "file still empty / finish it" reminder.
        assert "正文仍为空" in calls[1]["user_message"]
        assert "edit_file" in calls[1]["user_message"]
        # And the workflow still completes after the correction.
        assert any(event.type == StreamEventType.WORKFLOW_COMPLETE for event in events)

    @pytest.mark.asyncio
    async def test_empty_file_flag_set_in_tool_subtask_still_triggers_correction(self):
        """标记在 SDK 包的工具子任务里设置时，图循环（父上下文）仍必须触发纠偏重跑。

        openai-agents SDK 为每次 function tool 调用包一层 asyncio.create_task，
        create_file 的 pending 标记就是在那个子任务里设置的。
        """
        from agent.graph.writing_graph import run_writing_workflow_streaming

        calls: list[str] = []

        async def fake_run_streaming_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            if len(calls) == 1:
                # 模拟 SDK 在独立子任务里执行 create_file 工具并设置标记
                async def tool_task():
                    ToolContext.set_pending_empty_file("file-X", "第51章")

                await asyncio.create_task(tool_task())
                yield StreamEvent(
                    type=StreamEventType.TEXT,
                    data={"text": "正文文件已创建，需直接写入内容。[TASK_COMPLETE]"},
                )
            else:
                ToolContext.clear_pending_empty_file()
                yield StreamEvent(
                    type=StreamEventType.TEXT,
                    data={"text": "已用 edit_file 补全正文。[TASK_COMPLETE]"},
                )

        state = {"user_message": "写第51章", "messages": [], "system_prompt": ""}

        ToolContext.set_context(
            session=None, user_id="u", project_id="p", session_id="s",
        )
        try:
            with (
                patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
                patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
                patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
            ):
                _ = [
                    event async for event in run_writing_workflow_streaming(
                        state=state, thread_id="t",
                    )
                ]
        finally:
            ToolContext.clear_context()

        # 原始一轮 + 一次纠偏重跑
        assert calls == ["writer", "writer"]

    @pytest.mark.asyncio
    async def test_correction_is_bounded_and_does_not_loop_forever(self):
        from agent.graph.writing_graph import MAX_FILE_CORRECTION_ATTEMPTS, run_writing_workflow_streaming

        calls: list[str] = []

        async def fake_run_streaming_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            # Writer keeps creating empty files and never finishes one.
            ToolContext.set_pending_empty_file("file-Y", "第52章")
            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "又创建了一个空文件。[TASK_COMPLETE]"},
            )

        state = {"user_message": "写第52章", "messages": [], "system_prompt": ""}

        ToolContext.set_context(
            session=None, user_id="u", project_id="p", session_id="s",
        )
        try:
            with (
                patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
                patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
                patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
            ):
                _ = [
                    event async for event in run_writing_workflow_streaming(
                        state=state, thread_id="t",
                    )
                ]
        finally:
            ToolContext.clear_context()

        # Initial turn + at most MAX_FILE_CORRECTION_ATTEMPTS corrective re-runs.
        assert len(calls) <= 1 + MAX_FILE_CORRECTION_ATTEMPTS


@pytest.mark.unit
class TestWritingGraphFileCorrectionDbVerification:
    """纠偏必须先落库核验正文，避免 edit_file 已写完正文时被误判为空文件。

    pending-empty-file 标记只表示"没走 <file>…</file> 流式写入"，edit_file
    写正文不会清除它，所以标记仍在不等于正文为空。
    """

    @staticmethod
    def _make_file(db_session, *, content: str, is_deleted: bool = False):
        from models import File

        file = File(
            project_id="project-empty-guard",
            title="第一章",
            content=content,
            file_type="draft",
            is_deleted=is_deleted,
        )
        db_session.add(file)
        db_session.commit()
        db_session.refresh(file)
        return file

    @staticmethod
    async def _run(db_session, file_id: str, title: str) -> list[dict]:
        """跑一轮 writer：工具阶段置上 pending 标记，正文由 edit_file 侧写库。"""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        calls: list[dict] = []

        async def fake_run_streaming_agent(state, agent_type, *_args, **_kwargs):
            calls.append({"agent": agent_type, "user_message": state.get("user_message", "")})
            if len(calls) == 1:
                # 只有第一轮调用了 create_file(空)，标记因此只置一次
                ToolContext.set_pending_empty_file(file_id, title)
            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "已写入正文。[TASK_COMPLETE]"},
            )

        state = {"user_message": "写第一章", "messages": [], "system_prompt": ""}

        ToolContext.set_context(
            session=db_session, user_id="u", project_id="project-empty-guard", session_id="s",
        )
        try:
            with (
                patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
                patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
                patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
            ):
                _ = [
                    event async for event in run_writing_workflow_streaming(
                        state=state, thread_id="t",
                    )
                ]
        finally:
            ToolContext.clear_context()

        return calls

    @pytest.mark.asyncio
    async def test_body_written_by_edit_file_does_not_trigger_rerun(self, db_session):
        """create_file(空) + edit_file(op=append) 写完正文后，绝不能再跑一轮纠偏。

        否则纠偏轮会按"正文仍为空"的指令再 append 一遍完整正文，文件里出现两份正文。
        """
        file = self._make_file(db_session, content="第一章正文" * 50)

        calls = await self._run(db_session, file.id, file.title)

        assert len(calls) == 1, "正文已落库时不得触发纠偏重跑"

    @pytest.mark.asyncio
    async def test_still_empty_body_exhausts_corrections_then_rolls_back(
        self, db_session
    ):
        """正文一直为空时，尝试补写到上限后必须回滚空产物。"""
        from agent.graph.writing_graph import MAX_FILE_CORRECTION_ATTEMPTS

        file = self._make_file(db_session, content="   \n  ")

        calls = await self._run(db_session, file.id, file.title)

        assert len(calls) == 1 + MAX_FILE_CORRECTION_ATTEMPTS
        assert "正文仍为空" in calls[1]["user_message"]
        assert file.id in calls[1]["user_message"]
        db_session.expire_all()
        assert db_session.get(type(file), file.id).is_deleted is True

    @pytest.mark.asyncio
    async def test_deleted_file_does_not_trigger_rerun(self, db_session):
        """文件已被删除时无正文可补，不应白烧一轮 writer。"""
        file = self._make_file(db_session, content="", is_deleted=True)

        calls = await self._run(db_session, file.id, file.title)

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_missing_file_does_not_trigger_rerun(self, db_session):
        calls = await self._run(db_session, "file-not-in-db", "第一章")

        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_body_written_in_other_session_is_seen(self, db_session):
        """正文由工具自己的 session 写入时，核验必须读数据库最新值。

        offload 路径下 edit_file 用独立 session 提交，图上下文的 session 里可能
        还缓存着 content="" 的旧实例；按缓存判断会把已写完的文件误判为空文件。
        """
        from sqlmodel import Session

        from agent.graph.writing_graph import run_writing_workflow_streaming
        from models import File

        file = self._make_file(db_session, content="")
        # 让图上下文的 session 先缓存一份 content="" 的实例
        assert db_session.get(File, file.id).content == ""

        engine = db_session.get_bind()
        calls: list[str] = []

        async def fake_run_streaming_agent(state, agent_type, *_args, **_kwargs):
            calls.append(agent_type)
            if len(calls) == 1:
                ToolContext.set_pending_empty_file(file.id, file.title)
                with Session(engine) as tool_session:
                    target = tool_session.get(File, file.id)
                    target.content = "第一章正文" * 50
                    tool_session.add(target)
                    tool_session.commit()
            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "已写入正文。[TASK_COMPLETE]"},
            )

        state = {"user_message": "写第一章", "messages": [], "system_prompt": ""}

        ToolContext.set_context(
            session=db_session, user_id="u", project_id="project-empty-guard", session_id="s",
        )
        try:
            with (
                patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
                patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
                patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
            ):
                _ = [
                    event async for event in run_writing_workflow_streaming(
                        state=state, thread_id="t",
                    )
                ]
        finally:
            ToolContext.clear_context()

        assert calls == ["writer"]


@pytest.mark.unit
class TestWritingGraphSteeringFollowup:
    """运行期间到达的 steering 必须在本次请求内触发追加轮，而不是被丢弃。"""

    @pytest.mark.asyncio
    async def test_mid_run_steering_triggers_writer_followup(self):
        """runner 在工具边界消费到的 steering（steering_received 事件）应让
        graph 在工作流收尾前追加一轮 writer。"""
        from agent.core.events import steering_received_event
        from agent.graph.writing_graph import run_writing_workflow_streaming

        calls: list[dict] = []

        async def fake_run_streaming_agent(state, agent_type, get_steering_messages=None):
            calls.append({"agent_type": agent_type, "state": dict(state)})
            if len(calls) == 1:
                yield StreamEvent(type=StreamEventType.MESSAGE_START, data={})
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第一章内容"})
                # 模拟 runner 在工具输出边界消费到 steering：发确认事件并把
                # 内容作为用户消息写回 state（与真实 runner 行为一致）。
                yield steering_received_event(message_id="steer-1", preview="改成第一人称")
                state["messages"] = list(state.get("messages") or []) + [
                    {"role": "user", "content": "改成第一人称"}
                ]
            else:
                yield StreamEvent(type=StreamEventType.MESSAGE_START, data={})
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "已按引导调整"})

        async def get_steering_messages():
            # 队列已被 runner 在边界消费，graph 边界轮询拿不到新消息
            return []

        state = {"user_message": "写第一章", "messages": [], "system_prompt": ""}

        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state=state,
                    thread_id="t",
                    get_steering_messages=get_steering_messages,
                )
            ]

        assert len(calls) == 2, "运行中消费到 steering 后必须追加一轮 writer"
        assert calls[1]["agent_type"] == "writer"
        followup_message = calls[1]["state"]["user_message"]
        assert "引导" in followup_message
        # 引导内容已在会话历史中，追加轮能看到
        assert {"role": "user", "content": "改成第一人称"} in calls[1]["state"]["messages"]
        assert not any(
            event.type == StreamEventType.ITERATION_EXHAUSTED for event in events
        )

    @pytest.mark.asyncio
    async def test_boundary_steering_consumed_before_workflow_stops(self):
        """纯文本生成期间到达、run 内没有工具边界可消费的 steering，必须在
        工作流收尾前由 graph 消费并触发追加轮（而不是留给 cleanup 删除）。"""
        from agent.graph.writing_graph import run_writing_workflow_streaming

        calls: list[dict] = []

        async def fake_run_streaming_agent(state, agent_type, get_steering_messages=None):
            calls.append({"agent_type": agent_type, "state": dict(state)})
            yield StreamEvent(type=StreamEventType.MESSAGE_START, data={})
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "一段生成内容"})

        steering_polls = {"n": 0}

        async def get_steering_messages():
            steering_polls["n"] += 1
            if steering_polls["n"] == 1:
                return [{"id": "steer-2", "content": "结尾加一个反转"}]
            return []

        state = {"user_message": "写一段", "messages": [], "system_prompt": ""}

        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
        ):
            events = [
                event async for event in run_writing_workflow_streaming(
                    state=state,
                    thread_id="t",
                    get_steering_messages=get_steering_messages,
                )
            ]

        assert steering_polls["n"] >= 1, "graph 必须在 agent 边界轮询 steering 队列"
        assert len(calls) == 2, "边界消费到 steering 后必须追加一轮 writer"
        # 消费到的消息要向前端确认，并作为用户消息进入追加轮的会话历史
        assert any(
            getattr(event.type, "value", "") == "steering_received" for event in events
        )
        assert {"role": "user", "content": "结尾加一个反转"} in calls[1]["state"]["messages"]

    @pytest.mark.asyncio
    async def test_steering_followup_skipped_when_planned_agent_remains(self):
        """已有计划中的下一个 agent 时不追加 writer 轮：steering 会经由下一个
        agent 的起始注入/会话历史生效，避免打乱计划工作流。"""
        from agent.core.events import steering_received_event
        from agent.graph.writing_graph import run_writing_workflow_streaming

        calls: list[str] = []

        async def fake_run_streaming_agent(state, agent_type, get_steering_messages=None):
            calls.append(agent_type)
            yield StreamEvent(type=StreamEventType.MESSAGE_START, data={})
            if agent_type == "planner":
                yield steering_received_event(message_id="steer-3", preview="主角改名林川")
                state["messages"] = list(state.get("messages") or []) + [
                    {"role": "user", "content": "主角改名林川"}
                ]
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"{agent_type} 输出"})

        async def get_steering_messages():
            return []

        router_result = {
            "current_agent": "planner",
            "workflow_plan": "standard",
            "workflow_agents": ["writer"],
            "routing_metadata": {},
        }

        state = {"user_message": "规划并写作", "messages": [], "system_prompt": ""}

        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=router_result)),
            patch("agent.graph.writing_graph.get_next_node", return_value="planner"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_run_streaming_agent),
        ):
            _ = [
                event async for event in run_writing_workflow_streaming(
                    state=state,
                    thread_id="t",
                    get_steering_messages=get_steering_messages,
                )
            ]

        # planner -> writer（计划交接），writer 收尾后无新 steering，不再追加
        assert calls == ["planner", "writer"]


@pytest.mark.unit
class TestWritingGraphUserScope:
    """Agent 只做用户要求的事：计划交接/自动质检门不能越过用户的范围。

    这里走真实的 router_node（只 mock 路由那次 LLM 调用），覆盖
    「路由判定 → 计划序列裁剪 → 图的交接决策」整条链路。
    """

    @staticmethod
    def _route(payload: dict[str, object]):
        return AsyncMock(
            return_value={
                "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]
            }
        )

    @staticmethod
    async def _run(
        route_mock,
        fake_agent,
        *,
        message: str,
        max_iterations: int = 6,
        env=None,
        generation_mode: str | None = None,
        get_steering_messages=None,
    ):
        from agent.graph.writing_graph import run_writing_workflow_streaming

        environ = {"AGENT_ROUTER_STRATEGY": "llm", "AGENT_ENABLE_GRAPH_AUTO_REVIEW": "false"}
        environ.update(env or {})
        with (
            patch.dict("os.environ", environ),
            patch("agent.graph.router._route_with_deepseek_chat", route_mock),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
        ):
            return [
                event async for event in run_writing_workflow_streaming(
                    state={
                        "user_message": message,
                        "router_message": message,
                        "messages": [],
                        "system_prompt": "",
                        **({"generation_mode": generation_mode} if generation_mode else {}),
                    },
                    thread_id="user-scope",
                    max_iterations=max_iterations,
                    auto_review_threshold=50,
                    get_steering_messages=get_steering_messages,
                )
            ]

    @staticmethod
    def _assert_notice_closes_the_run(events) -> StreamEvent:
        """只读提示卡片只发一次，且是收尾时的最后一个事件（之后至多跟一个
        WORKFLOW_COMPLETE / ITERATION_EXHAUSTED / ERROR 终止事件）。"""
        stopped = [e for e in events if e.type == StreamEventType.WORKFLOW_STOPPED]
        assert len(stopped) == 1
        notice = stopped[0]
        assert notice.data["reason"] == "read_only_handoff_blocked"
        after = events[events.index(notice) + 1:]
        assert len(after) <= 1, [e.type for e in after]
        assert all(
            e.type in (
                StreamEventType.WORKFLOW_COMPLETE,
                StreamEventType.ITERATION_EXHAUSTED,
                StreamEventType.ERROR,
            )
            for e in after
        )
        return notice

    @staticmethod
    def _selected(events) -> list[str]:
        return [
            event.data["agent_type"]
            for event in events
            if event.type == StreamEventType.AGENT_SELECTED
        ]

    @pytest.mark.asyncio
    async def test_outline_only_request_stays_with_planner(self):
        """「先给我故事大纲」：planner 完成后不再被计划交接给 writer 写正文。"""
        captured: dict[str, str] = {}

        async def fake_agent(state, agent_type, **_kwargs):
            captured[agent_type] = str(state.get("scope_directive") or "")
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={"id": "t1", "name": "create_file", "status": "complete", "input": {}},
            )
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "故事大纲已创建。"})

        events = await self._run(
            self._route({
                "agent_type": "planner",
                "workflow_type": "standard",
                "write_content": False,
                "read_only": False,
                "scope": "只要故事大纲",
            }),
            fake_agent,
            message="帮我构思一个都市异能小说，先给我故事大纲",
        )

        assert self._selected(events) == ["planner"]
        assert not any(event.type == StreamEventType.HANDOFF for event in events)
        decided = next(e for e in events if e.type == StreamEventType.ROUTER_DECIDED)
        assert decided.data["workflow_agents"] == []
        # planner 自己也收到了「不要交接 writer 写正文」的范围约束
        assert "不要撰写或续写章节正文" in captured["planner"]
        assert "只要故事大纲" in captured["planner"]

    @pytest.mark.asyncio
    async def test_plan_then_write_hands_off_with_user_scope(self):
        """「规划后只写第1章」仍然 planner -> writer，且交接包/系统提示都带上范围。"""
        captured: dict[str, str] = {}

        async def fake_agent(state, agent_type, **_kwargs):
            captured[agent_type] = str(state.get("scope_directive") or "")
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"{agent_type} 完成。"})

        events = await self._run(
            self._route({
                "agent_type": "planner",
                "workflow_type": "standard",
                "write_content": True,
                "scope": "大纲+只写第1章",
            }),
            fake_agent,
            message="帮我规划整本书的大纲，然后只写第1章",
        )

        assert self._selected(events) == ["planner", "writer"]
        handoff = next(e for e in events if e.type == StreamEventType.HANDOFF)
        assert handoff.data["target_agent"] == "writer"
        assert "大纲+只写第1章" in handoff.data["context"]
        assert handoff.data["handoff_packet"]["todo"] == ["按用户要求的范围完成：大纲+只写第1章"]
        assert "大纲+只写第1章" in captured["writer"]

    @pytest.mark.asyncio
    async def test_question_to_user_stops_planned_handoff(self):
        """planner 以提问收尾时等用户回答，不能按计划自动交给 writer。"""
        calls: list[str] = []

        async def fake_agent(_state, agent_type, **_kwargs):
            calls.append(agent_type)
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "我先列了三个方向。"})
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={"id": "t1", "name": "query_files", "status": "complete", "input": {}},
            )
            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "你更倾向哪个方向？\n\n1. 热血升级\n2. 悬疑探案"},
            )

        events = await self._run(
            self._route({"agent_type": "planner", "workflow_type": "standard", "write_content": True}),
            fake_agent,
            message="规划一下然后写第一章",
        )

        assert calls == ["planner"]
        assert not any(event.type == StreamEventType.HANDOFF for event in events)
        assert not any(event.type == StreamEventType.ITERATION_EXHAUSTED for event in events)

    @pytest.mark.asyncio
    async def test_question_before_last_tool_call_does_not_block_handoff(self):
        """只看最后一次工具调用之后的收尾文本：中途自问自答不算在等用户。"""
        calls: list[str] = []

        async def fake_agent(_state, agent_type, **_kwargs):
            calls.append(agent_type)
            if agent_type == "planner":
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "现有大纲有哪些？"})
                yield StreamEvent(
                    type=StreamEventType.TOOL_RESULT,
                    data={"tool_use_id": "t1", "name": "query_files", "result": {}},
                )
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "大纲已更新。"})
                return
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第一章写好了。"})

        await self._run(
            self._route({"agent_type": "planner", "workflow_type": "standard", "write_content": True}),
            fake_agent,
            message="规划一下然后写第一章",
        )

        assert calls == ["planner", "writer"]

    @pytest.mark.asyncio
    async def test_explicit_handoff_wins_over_question_ending(self):
        """显式 handoff 是 agent 自己的决定，即便收尾带问号也照常交接。"""
        calls: list[str] = []

        async def fake_agent(_state, agent_type, **_kwargs):
            calls.append(agent_type)
            if agent_type == "planner":
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "大纲好了，开写？"})
                yield StreamEvent(
                    type=StreamEventType.HANDOFF,
                    data={"target_agent": "writer", "reason": "开始写", "context": "写第1章"},
                )
                return
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第一章写好了。"})

        await self._run(
            self._route({"agent_type": "planner", "workflow_type": "standard", "write_content": False}),
            fake_agent,
            message="规划一下",
        )

        assert calls == ["planner", "writer"]

    @pytest.mark.asyncio
    async def test_read_only_request_blocks_handoff_to_writer(self):
        """「只分析，不要改文件」：审稿人的显式交接 writer 也必须被拦下。"""
        calls: list[str] = []
        captured: dict[str, str] = {}

        async def fake_agent(state, agent_type, **_kwargs):
            calls.append(agent_type)
            captured[agent_type] = str(state.get("scope_directive") or "")
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第三章节奏偏慢。"})
            yield StreamEvent(
                type=StreamEventType.HANDOFF,
                data={"target_agent": "writer", "reason": "修正问题", "context": "改节奏"},
            )

        events = await self._run(
            self._route({
                "agent_type": "quality_reviewer",
                "workflow_type": "review_only",
                "write_content": False,
                "read_only": True,
            }),
            fake_agent,
            message="帮我看看第三章有什么问题，不要改文件，只分析",
        )

        assert calls == ["quality_reviewer"]
        assert not any(event.type == StreamEventType.HANDOFF for event in events)
        assert "禁止调用 create_file" in captured["quality_reviewer"]
        # 用户能看到交接为什么没发生：一条非澄清原因的 workflow_stopped 提示卡片，
        # 收尾时才发（前端收到 workflow_stopped 就结束流式状态）。
        notice = self._assert_notice_closes_the_run(events)
        assert notice.data["target_agent"] == "writer"
        assert "不改文件" in notice.data["message"]
        assert not any(event.type == StreamEventType.ERROR for event in events)

    @pytest.mark.asyncio
    async def test_read_only_notice_waits_for_steering_followup_round(self):
        """拦下写交接后还有一轮 steering 追加轮：提示卡片不能在两轮之间发出，
        必须等追加轮跑完、紧挨着收尾才发。"""
        calls: list[str] = []
        polls = {"n": 0}

        async def get_steering_messages():
            polls["n"] += 1
            if polls["n"] == 1:
                return [{"id": "steer-ro", "content": "顺便看看第四章"}]
            return []

        async def fake_agent(_state, agent_type, **_kwargs):
            calls.append(agent_type)
            yield StreamEvent(type=StreamEventType.MESSAGE_START, data={})
            if len(calls) == 1:
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第三章节奏偏慢。"})
                yield StreamEvent(
                    type=StreamEventType.HANDOFF,
                    data={"target_agent": "writer", "reason": "修正问题", "context": "改节奏"},
                )
                return
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第四章也有同样问题。"})

        events = await self._run(
            self._route({
                "agent_type": "quality_reviewer",
                "workflow_type": "review_only",
                "write_content": False,
                "read_only": True,
            }),
            fake_agent,
            message="帮我看看第三章有什么问题，不要改文件，只分析",
            get_steering_messages=get_steering_messages,
        )

        # 追加轮沿用只读的审稿人，不会升级成 writer
        assert calls == ["quality_reviewer", "quality_reviewer"]
        notice = self._assert_notice_closes_the_run(events)
        notice_index = events.index(notice)
        followup_text_index = max(
            i for i, e in enumerate(events)
            if e.type == StreamEventType.TEXT and "第四章" in e.data.get("text", "")
        )
        assert notice_index > followup_text_index
        assert notice_index > max(
            i for i, e in enumerate(events) if e.type == StreamEventType.AGENT_SELECTED
        )

    @pytest.mark.asyncio
    async def test_read_only_notice_precedes_error_when_followup_round_raises(self):
        """拦下写交接后，后面的轮次抛异常：提示卡片赶在 ERROR 之前发出，
        ERROR 仍是最后一个事件（终止判定交给 ERROR，而不是提示卡片）。"""
        calls: list[str] = []
        polls = {"n": 0}

        async def get_steering_messages():
            polls["n"] += 1
            if polls["n"] == 1:
                return [{"id": "steer-ro-2", "content": "再看看第四章"}]
            return []

        async def fake_agent(_state, agent_type, **_kwargs):
            calls.append(agent_type)
            yield StreamEvent(type=StreamEventType.MESSAGE_START, data={})
            if len(calls) == 1:
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第三章节奏偏慢。"})
                yield StreamEvent(
                    type=StreamEventType.HANDOFF,
                    data={"target_agent": "writer", "reason": "修正问题", "context": "改节奏"},
                )
                return
            raise RuntimeError("boom")

        events = await self._run(
            self._route({
                "agent_type": "quality_reviewer",
                "workflow_type": "review_only",
                "write_content": False,
                "read_only": True,
            }),
            fake_agent,
            message="帮我看看第三章有什么问题，不要改文件，只分析",
            get_steering_messages=get_steering_messages,
        )

        self._assert_notice_closes_the_run(events)
        assert events[-1].type == StreamEventType.ERROR
        # 原始异常只进日志，前端拿到的是固定文案与错误码。
        assert events[-1].data["code"] == "ERR_AGENT_RUN_FAILED"
        assert "boom" not in events[-1].data["error"]

    @pytest.mark.asyncio
    async def test_read_only_request_still_allows_handoff_to_read_only_agent(self):
        calls: list[str] = []

        async def fake_agent(_state, agent_type, **_kwargs):
            calls.append(agent_type)
            if agent_type == "writer":
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "我的看法如下。"})
                yield StreamEvent(
                    type=StreamEventType.HANDOFF,
                    data={"target_agent": "quality_reviewer", "reason": "评估", "context": "评估设定"},
                )
                return
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "设定合理。[TASK_COMPLETE]"})

        await self._run(
            self._route({"agent_type": "writer", "workflow_type": "quick", "read_only": True}),
            fake_agent,
            message="这个设定合理吗？只回答，别动我的文件",
        )

        assert calls == ["writer", "quality_reviewer"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("env", "generation_mode"),
        [({"AGENT_ENABLE_GRAPH_AUTO_REVIEW": "true"}, None), (None, "quality")],
        ids=["env-auto-review", "generation-mode-quality"],
    )
    async def test_auto_review_gate_fires_even_when_writer_ends_with_question(
        self, env, generation_mode
    ):
        """writer 写完后习惯性问“要继续写第2章吗？”：提问只拦计划交接，
        用户开了自动质检（含高质量模式）时已写完的稿子照常送审。"""
        calls: list[str] = []

        async def fake_agent(_state, agent_type, **_kwargs):
            calls.append(agent_type)
            if agent_type == "writer":
                yield StreamEvent(
                    type=StreamEventType.TEXT,
                    data={"text": f"<file>{'正' * 200}</file>\n\n第1章写好了，需要我继续写第2章吗？"},
                )
                return
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "通过 [TASK_COMPLETE]"})

        events = await self._run(
            self._route({"agent_type": "writer", "workflow_type": "quick", "write_content": True}),
            fake_agent,
            message="写第1章",
            env=env,
            generation_mode=generation_mode,
        )

        assert calls == ["writer", "quality_reviewer"]
        handoff = next(e for e in events if e.type == StreamEventType.HANDOFF)
        assert handoff.data["target_agent"] == "quality_reviewer"

    @pytest.mark.asyncio
    async def test_auto_review_gate_still_fires_for_finished_draft(self):
        """回归保护：正常写完（不提问）时自动质检门照旧触发。"""
        calls: list[str] = []

        async def fake_agent(_state, agent_type, **_kwargs):
            calls.append(agent_type)
            if agent_type == "writer":
                yield StreamEvent(
                    type=StreamEventType.TEXT,
                    data={"text": f"<file>{'正' * 200}</file>\n\n第1章已完成。"},
                )
                return
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "通过 [TASK_COMPLETE]"})

        await self._run(
            self._route({"agent_type": "writer", "workflow_type": "quick", "write_content": True}),
            fake_agent,
            message="写第1章",
            env={"AGENT_ENABLE_GRAPH_AUTO_REVIEW": "true"},
        )

        assert calls == ["writer", "quality_reviewer"]


@pytest.mark.unit
class TestScopeDirectiveBuilder:
    def test_read_only_takes_precedence_over_no_content(self):
        from agent.graph.writing_graph import (
            SCOPE_DIRECTIVE_NO_CONTENT,
            SCOPE_DIRECTIVE_READ_ONLY,
            _build_scope_directive,
        )

        directive = _build_scope_directive({"read_only": True, "write_content": False})
        assert SCOPE_DIRECTIVE_READ_ONLY in directive
        assert SCOPE_DIRECTIVE_NO_CONTENT not in directive

    def test_missing_metadata_yields_no_directive(self):
        from agent.graph.writing_graph import _build_scope_directive

        assert _build_scope_directive(None) == ""
        assert _build_scope_directive({}) == ""
        # write_content=None（旧格式）不等于「不要正文」
        assert _build_scope_directive({"write_content": None, "scope": ""}) == ""

    def test_read_only_steering_followup_uses_read_only_wording(self):
        from agent.graph.writing_graph import (
            STEERING_FOLLOWUP_CONTEXT_READONLY,
            _steering_followup_context,
        )

        assert _steering_followup_context("writer", read_only=True) == STEERING_FOLLOWUP_CONTEXT_READONLY
