"""
Tests for agent/graph/state.py

Tests the state definitions for the writing workflow.
"""

import pytest


@pytest.mark.unit
class TestMergeToolCalls:
    """Tests for merge_tool_calls function."""

    def test_merge_empty_left(self):
        """Test merging when left list is empty."""
        from agent.graph.state import merge_tool_calls

        left = []
        right = [{"id": "1", "name": "tool1"}]

        result = merge_tool_calls(left, right)

        assert result == right

    def test_merge_empty_right(self):
        """Test merging when right list is empty."""
        from agent.graph.state import merge_tool_calls

        left = [{"id": "1", "name": "tool1"}]
        right = []

        result = merge_tool_calls(left, right)

        assert result == left

    def test_merge_both_empty(self):
        """Test merging when both lists are empty."""
        from agent.graph.state import merge_tool_calls

        result = merge_tool_calls([], [])

        assert result == []

    def test_merge_both_non_empty(self):
        """Test merging when both lists have items."""
        from agent.graph.state import merge_tool_calls

        left = [{"id": "1", "name": "tool1"}]
        right = [{"id": "2", "name": "tool2"}]

        result = merge_tool_calls(left, right)

        assert len(result) == 2
        assert result[0] == {"id": "1", "name": "tool1"}
        assert result[1] == {"id": "2", "name": "tool2"}


@pytest.mark.unit
class TestToolCall:
    """Tests for ToolCall TypedDict."""

    def test_tool_call_creation(self):
        """Test creating a ToolCall dict."""
        from agent.graph.state import ToolCall

        tool_call: ToolCall = {
            "id": "tool-123",
            "name": "create_file",
            "input": {"title": "Test"},
            "result": '{"status": "success"}',
        }

        assert tool_call["id"] == "tool-123"
        assert tool_call["name"] == "create_file"
        assert tool_call["input"] == {"title": "Test"}
        assert tool_call["result"] == '{"status": "success"}'

    def test_tool_call_partial(self):
        """Test creating a partial ToolCall (total=False)."""
        from agent.graph.state import ToolCall

        # ToolCall has total=False, so all fields are optional
        tool_call: ToolCall = {
            "name": "query_files",
        }

        assert tool_call["name"] == "query_files"
        assert "id" not in tool_call


@pytest.mark.unit
class TestAgentOutput:
    """Tests for AgentOutput TypedDict."""

    def test_agent_output_creation(self):
        """Test creating an AgentOutput dict."""
        from agent.graph.state import AgentOutput

        output: AgentOutput = {
            "content": "Hello, world!",
            "thinking": "Let me think...",
            "tool_calls": [{"name": "test"}],
        }

        assert output["content"] == "Hello, world!"
        assert output["thinking"] == "Let me think..."
        assert len(output["tool_calls"]) == 1

    def test_agent_output_minimal(self):
        """Test creating a minimal AgentOutput."""
        from agent.graph.state import AgentOutput

        output: AgentOutput = {
            "content": "Response text",
        }

        assert output["content"] == "Response text"


@pytest.mark.unit
class TestHandoffPacket:
    """Tests for HandoffPacket TypedDict."""

    def test_handoff_packet_supports_artifact_refs(self):
        from agent.graph.state import HandoffPacket

        packet: HandoffPacket = {
            "target_agent": "quality_reviewer",
            "reason": "请审查",
            "artifact_refs": ["file-1", "project:proj-1"],
        }

        assert packet["target_agent"] == "quality_reviewer"
        assert packet["artifact_refs"] == ["file-1", "project:proj-1"]


@pytest.mark.unit
class TestWritingState:
    """Tests for WritingState TypedDict."""

    def test_writing_state_creation(self):
        """Test creating a WritingState dict."""
        from agent.graph.state import WritingState

        state: WritingState = {
            "user_message": "Write a story",
            "project_id": "proj-123",
            "user_id": "user-456",
            "session_id": "sess-789",
            "system_prompt": "You are a writer.",
            "context_data": {"context": "Some context"},
            "current_agent": "writer",
            "agent_output": {"content": "Once upon a time..."},
            "tool_calls": [],
            "messages": [],
        }

        assert state["user_message"] == "Write a story"
        assert state["project_id"] == "proj-123"
        assert state["current_agent"] == "writer"

    def test_writing_state_minimal(self):
        """Test creating a minimal WritingState."""
        from agent.graph.state import WritingState

        state: WritingState = {
            "user_message": "Hello",
            "project_id": "proj-1",
        }

        assert state["user_message"] == "Hello"
        assert state["project_id"] == "proj-1"

    def test_writing_state_with_messages(self):
        """Test WritingState with message history."""
        from agent.graph.state import WritingState

        state: WritingState = {
            "user_message": "Continue the story",
            "project_id": "proj-1",
            "messages": [
                {"role": "user", "content": "Start a story"},
                {"role": "assistant", "content": "Once upon a time..."},
            ],
        }

        assert len(state["messages"]) == 2
        assert state["messages"][0]["role"] == "user"
