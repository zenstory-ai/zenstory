"""
Integration tests for the writing agent.

Tests the agent system including:
- MCP tool definitions and execution (async)
- Stream adapter SSE conversion for workflow events
"""

import json

import pytest
from sqlmodel import Session

from models import File, Project, User
from services.core.auth_service import hash_password

# ========== Fixtures ==========

@pytest.fixture
def test_user_with_project(db_session: Session):
    """Create a test user with project for integration testing."""
    # Create user
    user = User(
        email="agent_integration_test@example.com",
        username="agentintegrationtest",
        hashed_password=hash_password("password123"),
        name="Agent Integration Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Create project
    project = Project(
        name="Agent Integration Test Project",
        description="A test project for writing agent integration",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create a draft file
    draft_file = File(
        title="测试章节",
        content="这是测试章节的内容。主角走进了森林。",
        file_type="draft",
        project_id=project.id,
        user_id=user.id,
    )
    db_session.add(draft_file)
    db_session.commit()
    db_session.refresh(draft_file)

    return {
        "user": user,
        "project": project,
        "draft_file": draft_file,
    }


# ========== MCP Tools Tests ==========

@pytest.mark.integration
class TestMCPTools:
    """Tests for MCP tool definitions (async)."""


    async def test_create_file_tool(self, db_session: Session, test_user_with_project):
        """Test create_file tool execution (async)."""
        from agent.tools.mcp_tools import ToolContext, create_file

        project = test_user_with_project["project"]
        user = test_user_with_project["user"]
        draft_file = test_user_with_project["draft_file"]

        # Set up tool context
        ToolContext.set_context(
            session=db_session,
            user_id=str(user.id),
            project_id=str(project.id),
            session_id=None,
        )

        # Execute create_file tool with dict argument (async)
        result = await create_file({
            "title": "新建章节",
            "file_type": "draft",
            "parent_id": str(draft_file.id),
            "content": "这是新建章节的内容。",
        })

        # Result is now a dict with MCP format
        assert "content" in result
        content_list = result["content"]
        assert len(content_list) > 0
        result_str = content_list[0].get("text", "")
        parsed = json.loads(result_str)
        assert parsed["status"] == "success"
        assert parsed["data"]["title"] == "新建章节"

    async def test_query_files_tool(self, db_session: Session, test_user_with_project):
        """Test query_files tool execution (async)."""
        from agent.tools.mcp_tools import ToolContext, query_files

        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        # Set up tool context
        ToolContext.set_context(
            session=db_session,
            user_id=str(user.id),
            project_id=str(project.id),
            session_id=None,
        )

        # Execute query_files tool with dict argument (async)
        result = await query_files({
            "file_type": "draft",
            "limit": 10,
        })

        # Result is now a dict with MCP format
        assert "content" in result
        content_list = result["content"]
        result_str = content_list[0].get("text", "")
        parsed = json.loads(result_str)
        assert parsed["status"] == "success"
        assert isinstance(parsed["data"], list)
        assert len(parsed["data"]) >= 1

    async def test_edit_file_tool(self, db_session: Session, test_user_with_project):
        """Test edit_file tool execution (async)."""
        from agent.tools.mcp_tools import ToolContext, edit_file

        draft_file = test_user_with_project["draft_file"]
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        # Set up tool context
        ToolContext.set_context(
            session=db_session,
            user_id=str(user.id),
            project_id=str(project.id),
            session_id=None,
        )

        # Execute edit_file tool with dict argument (async)
        result = await edit_file({
            "id": str(draft_file.id),
            "edits": [
                {
                    "op": "replace",
                    "old": "主角走进了森林",
                    "new": "主角勇敢地走进了黑暗的森林",
                }
            ],
        })

        # Result is now a dict with MCP format
        assert "content" in result
        content_list = result["content"]
        result_str = content_list[0].get("text", "")
        parsed = json.loads(result_str)
        assert parsed["status"] == "success"
        assert parsed["data"]["edits_applied"] == 1


# ========== Stream Adapter Tests ==========

@pytest.mark.integration
class TestStreamAdapter:
    """Tests for stream adapter SSE conversion."""

    def test_stream_adapter_creation(self):
        """Test stream adapter creation."""
        from agent.stream_adapter import create_stream_adapter

        adapter = create_stream_adapter(
            project_id="test-project",
            user_id="test-user",
            process_file_markers=True,
        )

        assert adapter is not None
        assert adapter.config.project_id == "test-project"
        assert adapter.config.user_id == "test-user"
        assert adapter.config.process_file_markers is True

    def test_stream_adapter_reset(self):
        """Test stream adapter reset."""
        from agent.stream_adapter import create_stream_adapter

        adapter = create_stream_adapter()
        adapter._content_started = True
        adapter._current_tool_calls["test"] = {"name": "test"}

        adapter.reset()

        assert adapter._content_started is False
        assert len(adapter._current_tool_calls) == 0

    def test_pending_file_write(self):
        """Test pending file write tracking."""
        from agent.stream_adapter import create_stream_adapter

        adapter = create_stream_adapter()

        adapter.set_pending_file_write(
            file_id="file-123",
            file_type="draft",
            title="Test File",
        )

        assert adapter._pending_file_write is not None
        assert adapter._pending_file_write.file_id == "file-123"
        assert adapter._pending_file_write.file_type == "draft"
        assert adapter._pending_file_write.title == "Test File"


@pytest.mark.integration
class TestStreamAdapterWorkflowEvents:
    """Tests for StreamAdapter processing workflow events."""

    async def test_process_text_event(self):
        """Test processing TEXT event from the workflow."""
        from agent.core.workflow_events import StreamEvent, StreamEventType
        from agent.stream_adapter import create_stream_adapter

        adapter = create_stream_adapter()

        async def mock_events():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Hello"})
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": " World"})

        events = []
        async for event in adapter.process_workflow_events(mock_events()):
            events.append(event)

        # Should have content_start, content events, content_end, and done
        event_types = [e.type.value for e in events]
        assert "content_start" in event_types
        assert "content" in event_types
        assert "done" in event_types

    async def test_process_tool_use_event(self):
        """Test processing TOOL_USE event from the workflow."""
        from agent.core.workflow_events import StreamEvent, StreamEventType
        from agent.stream_adapter import create_stream_adapter

        adapter = create_stream_adapter()

        async def mock_events():
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={"id": "tool-1", "name": "create_file", "status": "start"},
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={"name": "create_file", "input": {"title": "Test"}, "status": "stop"},
            )

        events = []
        async for event in adapter.process_workflow_events(mock_events()):
            events.append(event)

        # Should have tool_call event
        event_types = [e.type.value for e in events]
        assert "tool_call" in event_types

    async def test_process_tool_result_event(self):
        """Test processing TOOL_RESULT event from the workflow."""
        from agent.core.workflow_events import StreamEvent, StreamEventType
        from agent.stream_adapter import create_stream_adapter

        adapter = create_stream_adapter()

        async def mock_events():
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "tool_use_id": "tool-1",
                    "name": "create_file",
                    "result": {
                        "content": [{"type": "text", "text": '{"status": "success", "data": {"id": "file-1"}}'}]
                    },
                },
            )

        events = []
        async for event in adapter.process_workflow_events(mock_events()):
            events.append(event)

        # Should have tool_result event
        event_types = [e.type.value for e in events]
        assert "tool_result" in event_types

    async def test_process_thinking_event(self):
        """Test processing THINKING event from the workflow."""
        from agent.core.workflow_events import StreamEvent, StreamEventType
        from agent.stream_adapter import create_stream_adapter

        adapter = create_stream_adapter()

        async def mock_events():
            yield StreamEvent(
                type=StreamEventType.THINKING,
                data={"thinking": "Let me think about this..."},
            )

        events = []
        async for event in adapter.process_workflow_events(mock_events()):
            events.append(event)

        # Should have thinking_content event
        event_types = [e.type.value for e in events]
        assert "thinking_content" in event_types

    async def test_process_error_event(self):
        """Test processing ERROR event from the workflow."""
        from agent.core.workflow_events import StreamEvent, StreamEventType
        from agent.stream_adapter import create_stream_adapter

        adapter = create_stream_adapter()

        async def mock_events():
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data={"error": "Something went wrong"},
            )

        events = []
        async for event in adapter.process_workflow_events(mock_events()):
            events.append(event)

        # Should have error event
        event_types = [e.type.value for e in events]
        assert "error" in event_types
