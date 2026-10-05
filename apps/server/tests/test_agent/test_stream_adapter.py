"""
Tests for StreamAdapter - SSE streaming adapter for LangGraph events.

Comprehensive tests covering:
- SSE event formatting
- Streaming lifecycle
- Error handling
- Event types conversion
- File marker processing
- Tool result handling
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.core.events import (
    EventType,
    parallel_end_event,
    session_started_event,
    steering_received_event,
)
from agent.core.stream_processor import StreamState, normalize_file_markers
from agent.core.workflow_events import StreamEvent as LangGraphStreamEvent
from agent.core.workflow_events import StreamEventType
from agent.stream_adapter import (
    PendingFileWrite,
    StreamAdapter,
    StreamAdapterConfig,
    create_stream_adapter,
)


@pytest.fixture
def adapter_config():
    """Create test adapter configuration."""
    return StreamAdapterConfig(
        project_id="test-project-id",
        user_id="test-user-id",
        process_file_markers=True,
    )


@pytest.fixture
def adapter(adapter_config):
    """Create test stream adapter."""
    return StreamAdapter(adapter_config)


@pytest.fixture
def adapter_no_file_markers():
    """Create adapter with file marker processing disabled."""
    config = StreamAdapterConfig(
        project_id="test-project-id",
        user_id="test-user-id",
        process_file_markers=False,
    )
    return StreamAdapter(config)


class TestStreamAdapterConfig:
    """Tests for StreamAdapterConfig."""

    def test_default_config(self):
        """Test default configuration values."""
        config = StreamAdapterConfig()
        assert config.project_id == ""
        assert config.user_id is None
        assert config.process_file_markers is True

    def test_custom_config(self):
        """Test custom configuration values."""
        config = StreamAdapterConfig(
            project_id="project-123",
            user_id="user-456",
            process_file_markers=False,
        )
        assert config.project_id == "project-123"
        assert config.user_id == "user-456"
        assert config.process_file_markers is False


class TestPendingFileWrite:
    """Tests for PendingFileWrite dataclass."""

    def test_pending_file_write_creation(self):
        """Test creating a pending file write."""
        pending = PendingFileWrite(
            file_id="file-123",
            file_type="draft",
            title="Chapter 1",
        )
        assert pending.file_id == "file-123"
        assert pending.file_type == "draft"
        assert pending.title == "Chapter 1"


class TestStreamAdapterInit:
    """Tests for StreamAdapter initialization."""

    def test_init_with_config(self, adapter_config):
        """Test initialization with custom config."""
        adapter = StreamAdapter(adapter_config)
        assert adapter.config.project_id == "test-project-id"
        assert adapter.config.user_id == "test-user-id"
        assert adapter._pending_file_write is None
        assert adapter._content_started is False
        assert adapter._current_tool_calls == {}
        assert adapter._last_message_stop_reason is None
        assert adapter._last_message_usage is None
        assert adapter._matched_skill_ids == set()

    def test_init_without_config(self):
        """Test initialization with default config."""
        adapter = StreamAdapter()
        assert adapter.config.project_id == ""
        assert adapter.config.user_id is None

    def test_reset(self, adapter):
        """Test reset clears all state."""
        # Set some state
        adapter._content_started = True
        adapter._matched_skill_ids.add("skill-1")
        adapter._current_tool_calls["tool-1"] = {"name": "test"}
        adapter._last_message_stop_reason = "end_turn"
        adapter._last_message_usage = {"input_tokens": 1}
        adapter._pending_file_write = PendingFileWrite(
            file_id="file-1", file_type="draft", title="Test"
        )

        # Reset
        adapter.reset()

        # Verify all state cleared
        assert adapter._content_started is False
        assert adapter._matched_skill_ids == set()
        assert adapter._current_tool_calls == {}
        assert adapter._last_message_stop_reason is None
        assert adapter._last_message_usage is None
        assert adapter._pending_file_write is None
        assert adapter._stream_processor.state == StreamState.IDLE

    def test_get_last_message_metadata_defaults(self, adapter):
        """Should return empty metadata values before any message_end."""
        metadata = adapter.get_last_message_metadata()
        assert metadata["stop_reason"] is None
        assert metadata["usage"] is None


class TestSetPendingFileWrite:
    """Tests for set_pending_file_write method."""

    def test_set_pending_file_write(self, adapter):
        """Test setting pending file write."""
        adapter.set_pending_file_write(
            file_id="file-123",
            file_type="draft",
            title="Chapter 1",
        )

        assert adapter._pending_file_write is not None
        assert adapter._pending_file_write.file_id == "file-123"
        assert adapter._pending_file_write.file_type == "draft"
        assert adapter._pending_file_write.title == "Chapter 1"

        # Verify stream processor is in waiting state
        assert adapter._stream_processor.state == StreamState.WAITING_START
        assert adapter._stream_processor.file_id == "file-123"


class TestProcessEventText:
    """Tests for processing TEXT events."""

    @pytest.mark.asyncio
    async def test_text_event_starts_content(self, adapter):
        """Test text event triggers content_start."""
        event = LangGraphStreamEvent(
            type=StreamEventType.TEXT,
            data={"text": "Hello world"},
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        # Should emit content_start and content events
        assert len(events) == 2
        assert events[0].type == EventType.CONTENT_START
        assert events[1].type == EventType.CONTENT
        assert events[1].data["text"] == "Hello world"

    @pytest.mark.asyncio
    async def test_multiple_text_events(self, adapter):
        """Test multiple text events only emit one content_start."""
        event1 = LangGraphStreamEvent(
            type=StreamEventType.TEXT,
            data={"text": "Hello"},
        )
        event2 = LangGraphStreamEvent(
            type=StreamEventType.TEXT,
            data={"text": " world"},
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event1):
            events.append(sse_event)
        async for sse_event in adapter._process_langgraph_event(event2):
            events.append(sse_event)

        # Should have 3 events: content_start, content, content
        assert len(events) == 3
        assert events[0].type == EventType.CONTENT_START
        assert events[1].type == EventType.CONTENT
        assert events[1].data["text"] == "Hello"
        assert events[2].type == EventType.CONTENT
        assert events[2].data["text"] == " world"


class TestProcessEventThinking:
    """Tests for processing THINKING events."""

    @pytest.mark.asyncio
    async def test_thinking_event(self, adapter):
        """Test thinking event emits thinking_content."""
        event = LangGraphStreamEvent(
            type=StreamEventType.THINKING,
            data={"thinking": "Let me think about this..."},
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.THINKING_CONTENT
        assert events[0].data["content"] == "Let me think about this..."
        assert events[0].data["is_complete"] is False

    @pytest.mark.asyncio
    async def test_empty_thinking_event(self, adapter):
        """Test empty thinking event emits nothing."""
        event = LangGraphStreamEvent(
            type=StreamEventType.THINKING,
            data={"thinking": ""},
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 0


class TestProcessEventMessageEnd:
    """Tests for processing MESSAGE_END events metadata capture."""

    @pytest.mark.asyncio
    async def test_message_end_captures_metadata(self, adapter):
        """MESSAGE_END should update adapter metadata for persistence."""
        event = LangGraphStreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert events == []
        metadata = adapter.get_last_message_metadata()
        assert metadata["stop_reason"] == "tool_use"
        assert metadata["usage"]["input_tokens"] == 10


class TestProcessEventToolUse:
    """Tests for processing TOOL_USE events."""

    @pytest.mark.asyncio
    async def test_tool_use_start(self, adapter):
        """Test tool use start event."""
        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_USE,
            data={
                "status": "start",
                "id": "tool-123",
                "name": "create_file",
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        # Start event doesn't emit SSE event, just tracks state
        assert len(events) == 0
        assert "tool-123" in adapter._current_tool_calls
        assert adapter._current_tool_calls["tool-123"]["name"] == "create_file"

    @pytest.mark.asyncio
    async def test_tool_use_delta(self, adapter):
        """Test tool use delta accumulates JSON."""
        # Start first
        adapter._current_tool_calls["tool-1"] = {
            "name": "edit_file",
            "input_json": "",
        }

        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_USE,
            data={
                "status": "delta",
                "partial_json": '{"file_id": "f1"',
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        # Delta doesn't emit SSE event
        assert len(events) == 0
        assert adapter._current_tool_calls["tool-1"]["input_json"] == '{"file_id": "f1"'

    @pytest.mark.asyncio
    async def test_tool_use_stop_with_input(self, adapter):
        """Test tool use stop emits tool_call event with input."""
        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_USE,
            data={
                "status": "stop",
                "id": "tool-1",
                "name": "create_file",
                "input": {"title": "Chapter 1", "file_type": "draft"},
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.TOOL_CALL
        assert events[0].data["tool_use_id"] == "tool-1"
        assert events[0].data["tool_name"] == "create_file"
        assert events[0].data["arguments"]["title"] == "Chapter 1"

    @pytest.mark.asyncio
    async def test_tool_use_stop_with_accumulated_json(self, adapter):
        """Test tool use stop with accumulated JSON."""
        # Set up accumulated JSON
        adapter._current_tool_calls["tool-1"] = {
            "name": "edit_file",
            "input_json": '{"file_id": "f123", "edits": []}',
        }

        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_USE,
            data={
                "status": "stop",
                "id": "tool-1",
                "name": "edit_file",
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.TOOL_CALL
        assert events[0].data["tool_use_id"] == "tool-1"
        assert events[0].data["tool_name"] == "edit_file"
        assert events[0].data["arguments"]["file_id"] == "f123"

        # Should clean up tracked tool call
        assert "tool-1" not in adapter._current_tool_calls

    @pytest.mark.asyncio
    async def test_tool_use_complete(self, adapter):
        """Test tool use complete event."""
        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_USE,
            data={
                "status": "complete",
                "name": "query_files",
                "input": {"query": "chapter"},
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.TOOL_CALL
        assert events[0].data["tool_use_id"] is None
        assert events[0].data["tool_name"] == "query_files"


class TestProcessEventToolResult:
    """Tests for processing TOOL_RESULT events."""

    @pytest.mark.asyncio
    async def test_tool_result_success(self, adapter):
        """Test successful tool result."""
        result_json = json.dumps({
            "status": "success",
            "data": {"id": "f1", "title": "Test"}
        })
        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "create_file",
                "result": {
                    "content": [{"text": result_json}]
                },
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) >= 1
        assert events[0].type == EventType.TOOL_RESULT
        assert events[0].data["tool_use_id"] is None
        assert events[0].data["tool_name"] == "create_file"
        assert events[0].data["status"] == "success"

    @pytest.mark.asyncio
    async def test_tool_result_error(self, adapter):
        """Test error tool result."""
        result_json = json.dumps({
            "status": "error",
            "error": "File not found"
        })
        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "update_file",
                "result": {
                    "content": [{"text": result_json}]
                },
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) >= 1
        assert events[0].type == EventType.TOOL_RESULT
        assert events[0].data["status"] == "error"
        assert events[0].data["error"] == "File not found"

    @pytest.mark.asyncio
    async def test_tool_result_error_without_status_field(self, adapter):
        """Test backward-compatible error parsing when payload only has error field."""
        result_json = json.dumps({
            "error": "Unknown tool: bad_tool"
        })
        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "bad_tool",
                "result": {
                    "content": [{"text": result_json}]
                },
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) >= 1
        assert events[0].type == EventType.TOOL_RESULT
        assert events[0].data["status"] == "error"
        assert events[0].data["error"] == "Unknown tool: bad_tool"

    @pytest.mark.asyncio
    async def test_tool_result_with_invalid_json(self, adapter):
        """Test tool result with invalid JSON."""
        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "query_files",
                "result": {
                    "content": [{"text": "not valid json"}]
                },
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        # Should handle gracefully
        assert len(events) >= 1
        assert events[0].type == EventType.TOOL_RESULT

    @pytest.mark.asyncio
    async def test_tool_result_with_non_dict_result_shape(self, adapter):
        """Non-MCP result shapes should not break tool_result SSE emission."""
        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "query_files",
                "result": "plain text payload",
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.TOOL_RESULT
        assert events[0].data["status"] == "success"
        assert events[0].data["data"]["raw"] == "plain text payload"

    @pytest.mark.asyncio
    async def test_tool_result_handoff_status_is_normalized_to_success(self, adapter):
        """Control-tool statuses should map to frontend-compatible success/error."""
        result_json = json.dumps({
            "status": "handoff",
            "target_agent": "quality_reviewer",
            "reason": "质量审查",
        })
        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "handoff_to_agent",
                "result": {"content": [{"text": result_json}]},
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.TOOL_RESULT
        assert events[0].data["status"] == "success"

    @pytest.mark.asyncio
    async def test_tool_result_edit_file_with_non_dict_data_is_tolerated(self, adapter):
        """edit_file extra events should be skipped (not crashed) when data is truncated/non-dict."""
        result_json = json.dumps({
            "status": "success",
            "data": "truncated",
            "truncated": True,
            "max_chars": 100,
            "original_length": 999,
        })
        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "edit_file",
                "result": {"content": [{"text": result_json}]},
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        # Only tool_result should be emitted; edit detail events are skipped safely.
        assert len(events) == 1
        assert events[0].type == EventType.TOOL_RESULT
        assert events[0].data["status"] == "success"

    @pytest.mark.asyncio
    async def test_stream_continues_after_malformed_tool_result(self, adapter):
        """Malformed tool_result parsing should not interrupt subsequent SSE events."""

        async def mock_events():
            yield LangGraphStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "name": "query_files",
                    "result": {
                        "content": ["bad-shape"],  # invalid MCP content item shape
                    },
                },
            )
            yield LangGraphStreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "still running"},
            )

        events = []
        async for sse_event in adapter.process_langgraph_events(mock_events()):
            events.append(sse_event)

        event_types = [event.type for event in events]
        assert EventType.TOOL_RESULT in event_types
        assert EventType.CONTENT in event_types
        assert EventType.DONE in event_types


class TestProcessEventAgentSelected:
    """Tests for processing AGENT_SELECTED events."""

    @pytest.mark.asyncio
    async def test_agent_selected_event(self, adapter):
        """Test agent selected event."""
        event = LangGraphStreamEvent(
            type=StreamEventType.AGENT_SELECTED,
            data={
                "agent_type": "planner",
                "agent_name": "大纲规划师",
                "iteration": 1,
                "max_iterations": 5,
                "remaining": 4,
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.AGENT_SELECTED
        assert events[0].data["agent_type"] == "planner"
        assert events[0].data["agent_name"] == "大纲规划师"
        assert events[0].data["iteration"] == 1
        assert events[0].data["max_iterations"] == 5
        assert events[0].data["remaining"] == 4


class TestProcessEventIterationExhausted:
    """Tests for processing ITERATION_EXHAUSTED events."""

    @pytest.mark.asyncio
    async def test_iteration_exhausted_event(self, adapter):
        """Test iteration exhausted event."""
        event = LangGraphStreamEvent(
            type=StreamEventType.ITERATION_EXHAUSTED,
            data={
                "layer": "tool_call",
                "iterations_used": 10,
                "max_iterations": 10,
                "reason": "Maximum tool call iterations reached",
                "last_agent": "writer",
            },
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.ITERATION_EXHAUSTED
        assert events[0].data["layer"] == "tool_call"
        assert events[0].data["iterations_used"] == 10
        assert events[0].data["last_agent"] == "writer"


class TestProcessEventPassthrough:
    """Tests for passthrough LangGraph events."""

    @pytest.mark.asyncio
    async def test_handoff_event_passthrough(self, adapter):
        """Test handoff event is mapped to a valid SSE event type."""
        event = LangGraphStreamEvent(
            type=StreamEventType.HANDOFF,
            data={"from_agent": "planner", "to_agent": "writer"},
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.HANDOFF
        assert events[0].data["from_agent"] == "planner"
        assert events[0].data["to_agent"] == "writer"

    @pytest.mark.asyncio
    async def test_core_steering_received_event_passthrough(self, adapter):
        """Test core steering_received event is passed through by value."""
        event = steering_received_event(
            message_id="steer-1",
            preview="请聚焦第二章",
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.STEERING_RECEIVED
        assert events[0].data["message_id"] == "steer-1"

    @pytest.mark.asyncio
    async def test_core_session_started_event_passthrough(self, adapter):
        """Test core session_started event is passed through by value."""
        event = session_started_event("session-123")

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.SESSION_STARTED
        assert events[0].data["session_id"] == "session-123"

    @pytest.mark.asyncio
    async def test_core_parallel_end_event_passthrough(self, adapter):
        """Test core parallel_end event is passed through by value."""
        event = parallel_end_event(
            execution_id="exec-1",
            total_tasks=4,
            completed=3,
            failed=1,
            duration_ms=1200,
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.PARALLEL_END
        assert events[0].data["execution_id"] == "exec-1"
        assert events[0].data["total_tasks"] == 4


class TestProcessEventError:
    """Tests for processing ERROR events."""

    @pytest.mark.asyncio
    async def test_error_event(self, adapter):
        """Test error event."""
        event = LangGraphStreamEvent(
            type=StreamEventType.ERROR,
            data={"error": "API rate limit exceeded"},
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.ERROR
        # 原始异常文本不再透给前端：固定文案 + ERR_ 错误码。
        assert events[0].data["message"] == "生成回复时发生错误，请重试"
        assert events[0].data["code"] == "ERR_AGENT_RUN_FAILED"
        assert "rate limit" not in events[0].data["message"]

    @pytest.mark.asyncio
    async def test_error_event_with_default_message(self, adapter):
        """Test error event with default message."""
        event = LangGraphStreamEvent(
            type=StreamEventType.ERROR,
            data={},
        )

        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].data["message"] == "生成回复时发生错误，请重试"
        assert events[0].data["code"] == "ERR_AGENT_RUN_FAILED"


class TestProcessLangGraphEvents:
    """Tests for the main process_langgraph_events method."""

    @pytest.mark.asyncio
    async def test_stream_lifecycle(self, adapter):
        """Test complete streaming lifecycle."""
        async def mock_events():
            yield LangGraphStreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "Hello"},
            )
            yield LangGraphStreamEvent(
                type=StreamEventType.TEXT,
                data={"text": " world"},
            )
            yield LangGraphStreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={},
            )

        events = []
        async for sse_event in adapter.process_langgraph_events(mock_events()):
            events.append(sse_event)

        # Should have: content_start, content, content, content_end, done
        assert len(events) == 5
        assert events[0].type == EventType.CONTENT_START
        assert events[1].type == EventType.CONTENT
        assert events[2].type == EventType.CONTENT
        assert events[3].type == EventType.CONTENT_END
        assert events[4].type == EventType.DONE

    @pytest.mark.asyncio
    async def test_stream_ensures_content_end(self, adapter):
        """Test content_end is emitted even if not in events."""
        async def mock_events():
            yield LangGraphStreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "Text"},
            )
            # No MESSAGE_END, just stop

        events = []
        async for sse_event in adapter.process_langgraph_events(mock_events()):
            events.append(sse_event)

        # Should still have content_end and done
        event_types = [e.type for e in events]
        assert EventType.CONTENT_END in event_types
        assert EventType.DONE in event_types

    @pytest.mark.asyncio
    async def test_stream_without_content(self, adapter):
        """Test stream without any content events."""
        async def mock_events():
            yield LangGraphStreamEvent(
                type=StreamEventType.AGENT_SELECTED,
                data={"agent_type": "writer"},
            )
            yield LangGraphStreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={},
            )

        events = []
        async for sse_event in adapter.process_langgraph_events(mock_events()):
            events.append(sse_event)

        # Should have agent_selected and done (no content_end)
        event_types = [e.type for e in events]
        assert EventType.AGENT_SELECTED in event_types
        assert EventType.DONE in event_types
        assert EventType.CONTENT_END not in event_types

    @pytest.mark.asyncio
    async def test_stream_auto_completes_file_when_end_marker_missing(self, adapter):
        """Test stream end auto-completes file write when </file> is missing."""
        adapter.set_pending_file_write("file-1", "draft", "Chapter 1")
        adapter._save_file_content = AsyncMock()

        async def mock_events():
            yield LangGraphStreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "<file>Hello world"},
            )
            yield LangGraphStreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={},
            )

        events = []
        async for sse_event in adapter.process_langgraph_events(mock_events()):
            events.append(sse_event)

        event_types = [e.type for e in events]
        assert EventType.CONTENT_START in event_types
        assert EventType.FILE_CONTENT in event_types
        assert EventType.FILE_CONTENT_END in event_types
        assert EventType.CONTENT_END in event_types
        assert EventType.DONE in event_types
        adapter._save_file_content.assert_awaited_once_with("file-1", "Hello world")

    @pytest.mark.asyncio
    async def test_stream_save_failure_emits_error_without_done(self, adapter):
        """Persist failure should emit error and skip file_content_end/done."""
        adapter.set_pending_file_write("file-1", "draft", "Chapter 1")
        adapter._save_file_content = AsyncMock(return_value=False)

        async def mock_events():
            yield LangGraphStreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "<file>Hello</file>"},
            )
            yield LangGraphStreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={},
            )

        events = []
        async for sse_event in adapter.process_langgraph_events(mock_events()):
            events.append(sse_event)

        event_types = [e.type for e in events]
        assert EventType.ERROR in event_types
        assert EventType.FILE_CONTENT_END not in event_types
        assert EventType.DONE not in event_types

    @pytest.mark.asyncio
    async def test_stream_error_event_emits_content_end_without_done(self, adapter):
        """Workflow error events should terminate the stream without emitting done."""

        async def mock_events():
            yield LangGraphStreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "Partial response"},
            )
            yield LangGraphStreamEvent(
                type=StreamEventType.ERROR,
                data={"error": "Workflow exploded"},
            )

        events = []
        async for sse_event in adapter.process_langgraph_events(mock_events()):
            events.append(sse_event)

        event_types = [e.type for e in events]
        assert EventType.ERROR in event_types
        assert EventType.CONTENT_END in event_types
        assert EventType.DONE not in event_types


class TestHandleTextContent:
    """Tests for _handle_text_content method."""

    @pytest.mark.asyncio
    async def test_text_without_file_markers(self, adapter_no_file_markers):
        """Test text content without file marker processing."""
        events = []
        async for sse_event in adapter_no_file_markers._handle_text_content("Hello"):
            events.append(sse_event)

        assert len(events) == 1
        assert events[0].type == EventType.CONTENT
        assert events[0].data["text"] == "Hello"

    @pytest.mark.asyncio
    async def test_text_with_file_markers(self, adapter):
        """Test text content with file markers."""
        # Set up pending file write
        adapter.set_pending_file_write("file-1", "draft", "Chapter 1")

        events = []
        # First chunk before marker
        async for sse_event in adapter._handle_text_content("Before "):
            events.append(sse_event)

        # Send file marker and content
        async for sse_event in adapter._handle_text_content("<file>Content"):
            events.append(sse_event)

        # Should have conversation content before marker
        assert any(e.type == EventType.CONTENT for e in events)

    @pytest.mark.asyncio
    async def test_file_markers_in_single_chunk(self, adapter):
        """Test processing <file>...</file> within a single text chunk."""
        adapter.set_pending_file_write("file-1", "draft", "Chapter 1")
        adapter._save_file_content = AsyncMock()

        events = []
        async for sse_event in adapter._handle_text_content("<file>Hello</file>after"):
            events.append(sse_event)

        assert len(events) == 3
        assert events[0].type == EventType.FILE_CONTENT
        assert events[0].data["chunk"] == "Hello"
        assert events[1].type == EventType.FILE_CONTENT_END
        assert events[2].type == EventType.CONTENT
        assert events[2].data["text"] == "after"
        adapter._save_file_content.assert_awaited_once_with("file-1", "Hello")


class TestHandleCreateFileResult:
    """Tests for _handle_create_file_result method."""

    @pytest.mark.asyncio
    async def test_create_file_with_content(self, adapter):
        """Test create file result with content doesn't set pending."""
        result_data = {
            "id": "file-1",
            "file_type": "draft",
            "title": "Chapter 1",
            "content": "Some content",
        }

        events = [e async for e in adapter._handle_create_file_result(result_data)]

        # Should NOT set pending file write because content exists
        assert events == []
        assert adapter._pending_file_write is None

    @pytest.mark.asyncio
    async def test_create_file_empty(self, adapter):
        """Test create file result with empty content sets pending."""
        result_data = {
            "id": "file-1",
            "file_type": "draft",
            "title": "Chapter 1",
            "content": "",
        }

        events = [e async for e in adapter._handle_create_file_result(result_data)]

        # Should set pending file write
        assert events == []
        assert adapter._pending_file_write is not None
        assert adapter._pending_file_write.file_id == "file-1"

    @pytest.mark.asyncio
    async def test_create_file_empty_folder(self, adapter):
        """folder 建档不得进入待写正文状态（它永远不会有 <file> 正文）。"""
        result_data = {
            "id": "folder-1",
            "file_type": "folder",
            "title": "第一卷",
            "content": "",
        }

        events = [e async for e in adapter._handle_create_file_result(result_data)]

        assert events == []
        assert adapter._pending_file_write is None
        assert adapter._stream_processor.state == StreamState.IDLE


class TestHandleEditFileResult:
    """Tests for _handle_edit_file_result method."""

    @pytest.mark.asyncio
    async def test_edit_file_result(self, adapter):
        """Test edit file result emits proper events."""
        result = {
            "id": "file-1",
            "title": "Chapter 1",
            "details": [
                {
                    "op": "replace",
                    "old_preview": "old text here",
                    "new_preview": "new text here",
                    "success": True,
                }
            ],
            "new_length": 1000,
        }

        events = []
        async for sse_event in adapter._handle_edit_file_result(result):
            events.append(sse_event)

        # Should emit: edit_start, edit_applied, edit_end
        assert len(events) == 3
        assert events[0].type == EventType.FILE_EDIT_START
        assert events[0].data["file_id"] == "file-1"
        assert events[0].data["total_edits"] == 1

        assert events[1].type == EventType.FILE_EDIT_APPLIED
        assert events[1].data["op"] == "replace"
        assert events[1].data["success"] is True

        assert events[2].type == EventType.FILE_EDIT_END
        assert events[2].data["edits_applied"] == 1
        assert events[2].data["new_length"] == 1000

    @pytest.mark.asyncio
    async def test_edit_file_result_propagates_file_metadata(self, adapter):
        """file_edit_start/end should include file metadata when available."""
        result = {
            "id": "file-1",
            "title": "Chapter 1",
            "file_type": "outline",
            "details": [],
            "new_length": 42,
        }

        events = []
        async for sse_event in adapter._handle_edit_file_result(result):
            events.append(sse_event)

        assert len(events) == 2
        assert events[0].type == EventType.FILE_EDIT_START
        assert events[0].data["file_type"] == "outline"
        assert events[1].type == EventType.FILE_EDIT_END
        assert events[1].data["file_type"] == "outline"
        assert events[1].data["title"] == "Chapter 1"

    @pytest.mark.asyncio
    async def test_edit_file_multiple_edits(self, adapter):
        """Test edit file result with multiple edits."""
        result = {
            "id": "file-1",
            "title": "Chapter 1",
            "details": [
                {"op": "replace", "old_preview": "a", "new_preview": "b", "success": True},
                {"op": "insert_after", "old_preview": "c", "new_preview": "d", "success": True},
            ],
            "new_length": 2000,
        }

        events = []
        async for sse_event in adapter._handle_edit_file_result(result):
            events.append(sse_event)

        # Should emit: start, 2 applied, end
        assert len(events) == 4
        assert events[0].type == EventType.FILE_EDIT_START
        assert events[0].data["total_edits"] == 2

        assert events[1].type == EventType.FILE_EDIT_APPLIED
        assert events[1].data["edit_index"] == 0

        assert events[2].type == EventType.FILE_EDIT_APPLIED
        assert events[2].data["edit_index"] == 1

        assert events[3].type == EventType.FILE_EDIT_END

    @pytest.mark.asyncio
    async def test_edit_file_failed_edit(self, adapter):
        """Test edit file result with failed edit."""
        result = {
            "id": "file-1",
            "title": "Chapter 1",
            "details": [
                {
                    "op": "replace",
                    "old_preview": "old",
                    "new_preview": "new",
                    "success": False,
                    "error": "Pattern not found",
                }
            ],
            "new_length": 500,
        }

        events = []
        async for sse_event in adapter._handle_edit_file_result(result):
            events.append(sse_event)

        assert len(events) == 3
        assert events[1].type == EventType.FILE_EDIT_APPLIED
        assert events[1].data["success"] is False
        assert events[1].data["error"] == "Pattern not found"

    @pytest.mark.asyncio
    async def test_edit_file_no_file_id(self, adapter):
        """Test edit file result without file ID."""
        result = {
            "id": "",
            "title": "Chapter 1",
            "details": [],
        }

        events = []
        async for sse_event in adapter._handle_edit_file_result(result):
            events.append(sse_event)

        # Should emit nothing
        assert len(events) == 0


class TestSaveFileContent:
    """Tests for _save_file_content method."""

    @pytest.mark.asyncio
    async def test_save_file_content_success(self, adapter):
        """Test successful file content save."""
        with patch("database.get_session") as mock_get_session, \
             patch("agent.tools.file_ops.FileToolExecutor") as mock_executor_class:

            # Mock session
            mock_session = MagicMock()
            mock_gen = MagicMock()
            mock_gen.__next__ = MagicMock(return_value=mock_session)
            mock_gen.__iter__ = MagicMock(return_value=iter([mock_session, None]))
            mock_get_session.return_value = mock_gen

            # Mock executor
            mock_executor = MagicMock()
            mock_executor.update_file = MagicMock()
            mock_executor_class.return_value = mock_executor

            saved = await adapter._save_file_content("file-1", "Test content")

            # Should call update_file
            mock_executor.update_file.assert_called_once_with(
                id="file-1",
                content="Test content"
            )
            assert saved is True

    @pytest.mark.asyncio
    async def test_save_file_content_error(self, adapter):
        """Test file content save with error."""
        with patch("database.get_session") as mock_get_session:
            # Mock session that raises error
            mock_get_session.side_effect = Exception("DB connection failed")

            # Should not raise, just log error and return False
            saved = await adapter._save_file_content("file-1", "Test content")
            assert saved is False


class TestGetFileContent:
    """Tests for get_file_content and get_history_buffer methods."""

    def test_get_file_content(self, adapter):
        """Test getting file content."""
        adapter._stream_processor.content_buffer = "Test content"
        assert adapter.get_file_content() == "Test content"

    def test_get_history_buffer(self, adapter):
        """Test getting history buffer."""
        adapter._stream_processor.history_buffer = "History content"
        assert adapter.get_history_buffer() == "History content"


class TestCreateStreamAdapter:
    """Tests for create_stream_adapter factory function."""

    def test_create_stream_adapter_defaults(self):
        """Test creating adapter with defaults."""
        adapter = create_stream_adapter()
        assert adapter.config.project_id == ""
        assert adapter.config.process_file_markers is True

    def test_create_stream_adapter_custom(self):
        """Test creating adapter with custom config."""
        adapter = create_stream_adapter(
            project_id="proj-1",
            user_id="user-1",
            process_file_markers=False,
        )
        assert adapter.config.project_id == "proj-1"
        assert adapter.config.user_id == "user-1"
        assert adapter.config.process_file_markers is False


class TestSSEFormat:
    """Tests for SSE format compliance."""

    def test_sse_event_format(self):
        """Test SSE event format structure."""
        from agent.core.events import content_event

        event = content_event("Test content")
        sse_str = event.to_sse()

        # Should have proper SSE format
        assert "event: content\n" in sse_str
        assert "data:" in sse_str
        assert sse_str.endswith("\n\n")

    def test_sse_event_json_encoding(self):
        """Test SSE event JSON encoding."""
        from agent.core.events import tool_result_event

        event = tool_result_event(
            tool_name="create_file",
            status="success",
            data={"id": "f1", "title": "测试文件"},
        )
        sse_str = event.to_sse()

        # Should handle non-ASCII characters
        assert "测试文件" in sse_str

    def test_sse_event_datetime_encoding(self):
        """Test SSE event with datetime encoding."""
        from datetime import datetime

        from agent.core.events import EventType, StreamEvent

        event = StreamEvent(
            type=EventType.CONTENT,
            data={"timestamp": datetime(2024, 1, 1, 12, 0, 0)},
        )
        sse_str = event.to_sse()

        # Should encode datetime as ISO format
        assert "2024-01-01T12:00:00" in sse_str


class TestFileMarkerNormalization:
    """Tests for safe marker normalization behavior."""

    def test_does_not_normalize_non_file_tags(self):
        """Tags like <filex> or <filename> should remain unchanged."""
        assert normalize_file_markers("<filex>abc") == "<filex>abc"
        assert normalize_file_markers("<filename>abc") == "<filename>abc"

    def test_does_not_normalize_in_code_block(self):
        """Code block content should be preserved as-is."""
        content = "```\n<filex>\n```"
        assert normalize_file_markers(content) == content


class TestErrorHandling:
    """Tests for error handling scenarios."""

    @pytest.mark.asyncio
    async def test_handle_encoding_errors(self, adapter):
        """Test handling encoding errors in text content."""
        # Text with potential encoding issues
        event = LangGraphStreamEvent(
            type=StreamEventType.TEXT,
            data={"text": "Test with emoji 🎉 and unicode \u0000"},
        )

        # Should not raise
        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) >= 1

    @pytest.mark.asyncio
    async def test_handle_malformed_tool_result(self, adapter):
        """Test handling malformed tool result."""
        event = LangGraphStreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "name": "test_tool",
                "result": {},  # Missing content
            },
        )

        # Should handle gracefully
        events = []
        async for sse_event in adapter._process_langgraph_event(event):
            events.append(sse_event)

        assert len(events) >= 1


class TestIntegration:
    """Integration tests combining multiple features."""

    @pytest.mark.asyncio
    async def test_full_conversation_flow(self, adapter):
        """Test complete conversation flow with multiple event types."""
        async def mock_events():
            yield LangGraphStreamEvent(
                type=StreamEventType.AGENT_SELECTED,
                data={"agent_type": "writer", "agent_name": "内容创作者"},
            )
            yield LangGraphStreamEvent(
                type=StreamEventType.THINKING,
                data={"thinking": "Planning the content..."},
            )
            yield LangGraphStreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "Here is the content:"},
            )
            yield LangGraphStreamEvent(
                type=StreamEventType.TEXT,
                data={"text": " Chapter 1"},
            )
            yield LangGraphStreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={},
            )

        events = []
        async for sse_event in adapter.process_langgraph_events(mock_events()):
            events.append(sse_event)

        # Should have all event types
        event_types = [e.type for e in events]
        assert EventType.AGENT_SELECTED in event_types
        assert EventType.THINKING_CONTENT in event_types
        assert EventType.CONTENT_START in event_types
        assert EventType.CONTENT in event_types
        assert EventType.CONTENT_END in event_types
        assert EventType.DONE in event_types

    @pytest.mark.asyncio
    async def test_tool_workflow(self, adapter):
        """Test complete tool calling workflow."""
        async def mock_events():
            # Agent selection
            yield LangGraphStreamEvent(
                type=StreamEventType.AGENT_SELECTED,
                data={"agent_type": "writer"},
            )
            # Tool call
            yield LangGraphStreamEvent(
                type=StreamEventType.TOOL_USE,
                data={
                    "status": "complete",
                    "name": "create_file",
                    "input": {"title": "Chapter 1", "file_type": "draft"},
                },
            )
            # Tool result
            result_json = json.dumps({
                "status": "success",
                "data": {"id": "f1", "title": "Chapter 1", "file_type": "draft"}
            })
            yield LangGraphStreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "name": "create_file",
                    "result": {"content": [{"text": result_json}]},
                },
            )
            # End
            yield LangGraphStreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={},
            )

        events = []
        async for sse_event in adapter.process_langgraph_events(mock_events()):
            events.append(sse_event)

        # Should have tool call and result
        tool_call_events = [e for e in events if e.type == EventType.TOOL_CALL]
        tool_result_events = [e for e in events if e.type == EventType.TOOL_RESULT]

        assert len(tool_call_events) == 1
        assert len(tool_result_events) == 1
        assert EventType.DONE in [e.type for e in events]


@pytest.mark.unit
async def test_abandoned_file_write_clears_pending_empty_file_guard():
    """An abandoned <file> write must not leave ToolContext's pending guard set.

    Regression: create_file emits an empty file (pending-empty-file guard + adapter
    tracker set), but the model never streams the <file>...</file> content. The stream
    must clear the stale guard on finalize so later create_file calls are not blocked.
    """
    from agent.stream_adapter import create_stream_adapter
    from agent.tools.mcp_tools import ToolContext

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    ToolContext.set_pending_empty_file("file-A", "角色A")
    adapter.set_pending_file_write("file-A", "character", "角色A")
    assert ToolContext.has_pending_empty_file() is True

    async def mock_events():
        yield LangGraphStreamEvent(type=StreamEventType.TEXT, data={"text": "好的，我已创建角色A。"})
        yield LangGraphStreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    _ = [e async for e in adapter.process_workflow_events(mock_events())]

    assert ToolContext.has_pending_empty_file() is False
    assert adapter._pending_file_write is None


@pytest.mark.unit
async def test_unterminated_file_with_control_marker_is_not_persisted():
    """An unterminated <file> whose body carries a turn-control marker is chat
    narration, not prose: it must NEVER be saved as the file's content.

    This is the production corruption: the model opened <file>, streamed handoff
    narration ending in [TASK_COMPLETE], and never closed it; the buffer must be
    surfaced as conversation and the file left untouched.
    """
    from agent.stream_adapter import create_stream_adapter
    from agent.tools.mcp_tools import ToolContext

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)
    ToolContext.set_pending_empty_file("file-1", "第51章")
    adapter.set_pending_file_write("file-1", "draft", "第51章")

    async def mock_events():
        yield LangGraphStreamEvent(
            type=StreamEventType.TEXT,
            data={"text": "<file>正文文件已创建，需直接写入内容。\n\n"},
        )
        yield LangGraphStreamEvent(
            type=StreamEventType.TEXT,
            data={"text": "### 交接审查完成，已交接给 writer。[TASK_COMPLETE]"},
        )
        yield LangGraphStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    events = [e async for e in adapter.process_workflow_events(mock_events())]
    event_types = [e.type for e in events]

    # The narration must not be persisted as file content...
    adapter._save_file_content.assert_not_awaited()
    assert EventType.FILE_CONTENT_END not in event_types


@pytest.mark.unit
async def test_truncated_prose_without_control_marker_is_still_saved():
    """Genuinely truncated prose (no </file>, no control markers) must still be
    salvaged and saved — the contamination guard must not over-discard."""
    from agent.stream_adapter import create_stream_adapter
    from agent.tools.mcp_tools import ToolContext

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)
    ToolContext.set_pending_empty_file("file-2", "第52章")
    adapter.set_pending_file_write("file-2", "draft", "第52章")

    async def mock_events():
        yield LangGraphStreamEvent(
            type=StreamEventType.TEXT,
            data={"text": "<file>夜色沉沉，林川推开门，看见了那封信。"},
        )
        yield LangGraphStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    _ = [e async for e in adapter.process_workflow_events(mock_events())]

    adapter._save_file_content.assert_awaited_once()
    saved_args = adapter._save_file_content.await_args.args
    assert saved_args[0] == "file-2"
    assert "夜色沉沉" in saved_args[1]
    assert "[TASK_COMPLETE]" not in saved_args[1]


@pytest.mark.unit
async def test_fence_wrapped_file_block_is_saved_not_dumped_into_chat():
    """模型把整块 <file>…</file> 包在 ``` 围栏里输出（提示词范例即这种写法）时，
    正文必须落库；不能因为围栏保护把带原始标记的整章正文当成聊天消息、文件留空。"""
    from agent.stream_adapter import create_stream_adapter
    from agent.tools.mcp_tools import ToolContext

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)
    ToolContext.set_pending_empty_file("file-3", "第53章")
    adapter.set_pending_file_write("file-3", "draft", "第53章")

    async def mock_events():
        yield LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "```markdown\n<file>"}
        )
        yield LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "夜色沉沉，林川推开门。"}
        )
        yield LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "</file>\n```"}
        )
        yield LangGraphStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    events = [e async for e in adapter.process_workflow_events(mock_events())]

    adapter._save_file_content.assert_awaited_once()
    saved_args = adapter._save_file_content.await_args.args
    assert saved_args[0] == "file-3"
    assert saved_args[1] == "夜色沉沉，林川推开门。"

    chat_text = "".join(
        e.data.get("text", "") for e in events if e.type == EventType.CONTENT
    )
    assert "夜色沉沉" not in chat_text
    assert "<file>" not in chat_text


@pytest.mark.unit
async def test_unclosed_fence_narration_then_file_block_is_saved():
    """叙述里出现一个未闭合的 ``` 后再输出真实 <file> 正文：叙述进聊天、正文落库。

    回归：开始标记因围栏可能在后续 chunk 闭合而全程判为歧义，若流结束时不复扫，
    整章正文会连 <file>/</file> 原始标记一起被持久化成 assistant 消息。
    """
    from agent.stream_adapter import create_stream_adapter
    from agent.tools.mcp_tools import ToolContext

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)
    ToolContext.set_pending_empty_file("file-4", "第54章")
    adapter.set_pending_file_write("file-4", "draft", "第54章")

    async def mock_events():
        yield LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "好的，输出格式```\n"}
        )
        yield LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "<file>雨停了，屋檐还在滴水。"}
        )
        yield LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "</file>已完成。"}
        )
        yield LangGraphStreamEvent(type=StreamEventType.MESSAGE_END, data={})

    events = [e async for e in adapter.process_workflow_events(mock_events())]

    adapter._save_file_content.assert_awaited_once()
    saved_args = adapter._save_file_content.await_args.args
    assert saved_args[0] == "file-4"
    assert saved_args[1] == "雨停了，屋檐还在滴水。"

    chat_text = "".join(
        e.data.get("text", "") for e in events if e.type == EventType.CONTENT
    )
    assert chat_text == "好的，输出格式```\n已完成。"


# ---------------------------------------------------------------------------
# folder 是纯容器节点，永远不会收到 <file>…</file> 正文：
# create_file(file_type="folder") 不得开启流式捕获（C2 回归）
# ---------------------------------------------------------------------------


def _create_file_result_event(
    file_id: str,
    file_type: str,
    title: str,
    content: str = "",
) -> LangGraphStreamEvent:
    """构造一个 create_file 的 TOOL_RESULT 事件（MCP 规范格式）。"""
    payload = {
        "status": "success",
        "data": {
            "id": file_id,
            "title": title,
            "file_type": file_type,
            "content": content,
        },
    }
    return LangGraphStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        data={
            "name": "create_file",
            "tool_use_id": f"tu-{file_id}",
            "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
        },
    )


async def _drive_per_event(adapter, events) -> list[list]:
    """逐个事件喂给 adapter，返回「每个输入事件当轮产出的 SSE 事件」列表。

    必须按轮记录：folder 被当成待写正文文件时，叙述会被扣在 WAITING_START
    缓冲里直到 MESSAGE_END 才一次性吐出（前端表现为长时间无输出），只看事件
    总集合无法区分这种时序差异。
    """
    return [
        [sse async for sse in adapter._process_workflow_event(event)]
        for event in events
    ]


@pytest.mark.unit
async def test_empty_folder_does_not_start_file_capture():
    """创建空文件夹后，叙述必须在各自 TEXT 事件当轮就流出，处理器保持 IDLE。

    回归：_handle_create_file_result 只看 content 为空就开启流式捕获，folder
    同样命中，导致后续叙述全被缓冲到 MESSAGE_END 才吐出。
    """
    from agent.stream_adapter import create_stream_adapter
    from agent.tools.mcp_tools import ToolContext

    ToolContext.clear_pending_empty_file()
    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)

    rounds = await _drive_per_event(adapter, [
        _create_file_result_event("folder-1", "folder", "第一卷"),
        LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "已为你建好「第一卷」文件夹，"}
        ),
        LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "接下来我会按大纲逐章写作。"}
        ),
        LangGraphStreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"}),
    ])

    # folder 不是待写正文的目标：既不置 adapter 的 pending，也不让处理器进入捕获
    assert adapter._pending_file_write is None
    assert adapter._stream_processor.state == StreamState.IDLE

    # file_created 事件仍要照常发出（前端据此刷新文件树）
    assert [e.type for e in rounds[0]] == [EventType.TOOL_RESULT, EventType.FILE_CREATED]

    # 叙述在当轮就流出，MESSAGE_END 不再补吐任何内容
    assert [
        e.data["text"] for e in rounds[1] if e.type == EventType.CONTENT
    ] == ["已为你建好「第一卷」文件夹，"]
    assert [
        e.data["text"] for e in rounds[2] if e.type == EventType.CONTENT
    ] == ["接下来我会按大纲逐章写作。"]
    assert rounds[3] == []

    adapter._save_file_content.assert_not_awaited()
    assert ToolContext.has_pending_empty_file() is False


@pytest.mark.unit
async def test_empty_folder_file_type_variants_do_not_start_capture():
    """folder 判定需归一化（大小写/空白），否则变体仍会开启流式捕获。"""
    from agent.stream_adapter import create_stream_adapter

    for raw_type in ("folder", "Folder", " FOLDER ", "folder\n"):
        adapter = create_stream_adapter(
            project_id="p", user_id="u", process_file_markers=True
        )
        await _drive_per_event(adapter, [
            _create_file_result_event("folder-x", raw_type, "第一卷"),
        ])
        assert adapter._pending_file_write is None, raw_type
        assert adapter._stream_processor.state == StreamState.IDLE, raw_type


@pytest.mark.unit
@pytest.mark.parametrize(
    "file_type",
    ["draft", "script", "outline", "character", "lore", "snippet", ""],
)
async def test_empty_content_bearing_file_still_starts_capture(file_type):
    """承载正文的类型（含剧本分集复用返回的空 content）必须继续进入 WAITING_START。"""
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    await _drive_per_event(adapter, [
        _create_file_result_event("file-1", file_type, "第一章"),
    ])

    assert adapter._pending_file_write is not None
    assert adapter._pending_file_write.file_id == "file-1"
    assert adapter._stream_processor.state == StreamState.WAITING_START


@pytest.mark.unit
async def test_folder_narration_with_fenced_file_example_is_not_written_to_folder():
    """folder 后的叙述里出现 ``` 围栏包裹的成对 <file>…</file> 范例时，
    范例必须原样进聊天，不得被流结束的 at_eof 兜底当成正文写进文件夹行。"""
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)

    rounds = await _drive_per_event(adapter, [
        _create_file_result_event("folder-2", "folder", "第一卷"),
        LangGraphStreamEvent(
            type=StreamEventType.TEXT,
            data={
                "text": "文件夹已创建。写作协议是这样的：\n\n"
                        "```markdown\n<file>\n这里是正文示例，比如第一章的开头。\n</file>\n```\n\n"
            },
        ),
        LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "明白了吗？我现在开始写第一章。"}
        ),
        LangGraphStreamEvent(type=StreamEventType.MESSAGE_END, data={}),
    ])
    emitted = [e for round_events in rounds for e in round_events]

    adapter._save_file_content.assert_not_awaited()
    assert not [
        e for e in emitted
        if e.type in (EventType.FILE_CONTENT, EventType.FILE_CONTENT_END)
    ]

    chat_text = "".join(
        e.data.get("text", "") for e in emitted if e.type == EventType.CONTENT
    )
    assert "这里是正文示例，比如第一章的开头。" in chat_text
    assert "<file>" in chat_text
    assert chat_text.endswith("明白了吗？我现在开始写第一章。")


# ---------------------------------------------------------------------------
# 新的空文件建档顶掉上一段流式捕获时，不得静默丢弃已缓冲的内容
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_new_pending_write_flushes_buffered_narration():
    """WAITING_START 中缓冲的叙述必须在被下一次建档顶掉前吐出。

    回归：set_pending_file_write -> start_file_write() 直接清空 temp_buffer，
    夹在两次建档之间的叙述永久消失（0 个 content 事件）。
    """
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)

    rounds = await _drive_per_event(adapter, [
        _create_file_result_event("draft-A", "draft", "第一章"),
        LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "先说明一下：这一章我会写得克制些。"}
        ),
        _create_file_result_event("draft-B", "draft", "第二章"),
        LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "<file>第二章正文。</file>"}
        ),
        LangGraphStreamEvent(type=StreamEventType.MESSAGE_END, data={}),
    ])

    # 被顶掉的缓冲在「顶掉它的那一轮」就作为 content 吐出
    assert [
        e.data["text"] for e in rounds[2] if e.type == EventType.CONTENT
    ] == ["先说明一下：这一章我会写得克制些。"]

    # 新文件的正文正常落库，且不含那段叙述
    adapter._save_file_content.assert_awaited_once()
    saved_args = adapter._save_file_content.await_args.args
    assert saved_args[0] == "draft-B"
    assert saved_args[1] == "第二章正文。"


@pytest.mark.unit
async def test_new_pending_write_completes_displaced_unterminated_file():
    """WRITING 中被顶掉时，上一份文件已缓冲的正文必须落库而不是被清空。"""
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)

    await _drive_per_event(adapter, [
        _create_file_result_event("draft-C", "draft", "第三章"),
        LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "<file>第三章开头，尚未闭合。"}
        ),
        _create_file_result_event("draft-D", "draft", "第四章"),
        LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "<file>第四章正文。</file>"}
        ),
        LangGraphStreamEvent(type=StreamEventType.MESSAGE_END, data={}),
    ])

    saved = [call.args for call in adapter._save_file_content.await_args_list]
    assert saved == [
        ("draft-C", "第三章开头，尚未闭合。"),
        ("draft-D", "第四章正文。"),
    ]


@pytest.mark.unit
async def test_displaced_completion_keeps_new_files_pending_empty_guard():
    """收尾被顶掉的文件时，不能清掉已指向新文件的「空文件待补写」标记。

    该标记是 writing_graph 判断「空文件必须被补写」的唯一信号，误清会让新建
    的空文件永远无人补写。
    """
    from agent.stream_adapter import create_stream_adapter
    from agent.tools.mcp_tools import ToolContext

    ToolContext.clear_pending_empty_file()
    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)

    # draft-E 走的是「执行器复用/幂等路径返回 content=''」，入参带内容因此
    # mcp_tools 没有为它置标记；随后新建的 draft-F 才是被标记的空文件。
    await _drive_per_event(adapter, [
        _create_file_result_event("draft-E", "draft", "第五章"),
        LangGraphStreamEvent(
            type=StreamEventType.TEXT, data={"text": "<file>第五章开头。"}
        ),
    ])
    ToolContext.set_pending_empty_file("draft-F", "第六章")

    await _drive_per_event(adapter, [
        _create_file_result_event("draft-F", "draft", "第六章"),
    ])

    adapter._save_file_content.assert_awaited_once()
    assert adapter._save_file_content.await_args.args == ("draft-E", "第五章开头。")
    pending = ToolContext.get_pending_empty_file()
    assert pending is not None and pending["file_id"] == "draft-F"

    ToolContext.clear_pending_empty_file()
