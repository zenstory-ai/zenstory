"""
Agent Service tests.

Unit tests for the AgentService business logic with mocked dependencies.
Tests core functionality without making real LLM API calls.

Updated for LangGraph architecture.
"""

import asyncio
import json
import logging
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, desc, select

from models import (
    ChatMessage,
    ChatSession,
    File,
    Project,
    PublicSkill,
    SkillResource,
    SkillUsage,
    User,
    UserAddedSkill,
    UserSkill,
)
from services.core.auth_service import hash_password

pytestmark = pytest.mark.usefixtures("writing_prompt_configs")


@pytest.fixture
def test_user_with_project(db_session: Session):
    """Create a test user with project for agent service testing."""
    # Create user
    user = User(
        email="agent_service_test@example.com",
        username="agentservicetest",
        hashed_password=hash_password("password123"),
        name="Agent Service Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Create project
    project = Project(
        name="Agent Service Test Project",
        description="A test project for agent service",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create a draft file
    draft_file = File(
        title="第一章",
        content="这是第一章的内容。",
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


@pytest.fixture
def mock_langgraph_workflow():
    """Mock the LangGraph workflow for testing."""
    from agent.core.workflow_events import StreamEvent, StreamEventType

    async def mock_stream():
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Hello"})
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": " World"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    with patch("agent.service.run_writing_workflow_streaming") as mock:
        mock.return_value = mock_stream()
        yield mock


@pytest.fixture
def mock_agent_service():
    """Create an AgentService instance with mocked dependencies."""
    with patch("agent.service.get_context_assembler") as mock_get_context:
        mock_context_assembler = MagicMock()
        mock_get_context.return_value = mock_context_assembler

        # Mock context data
        from agent.schemas.context import ContextData
        mock_context_assembler.assemble.return_value = ContextData(
            items=[],
            context="",
            token_estimate=0,
        )

        from agent.service import AgentService
        service = AgentService(
            context_assembler=mock_context_assembler,
        )

        yield service, mock_context_assembler


@pytest.mark.unit
class TestAgentServiceInit:
    """Tests for AgentService initialization."""

    def test_agent_service_init_default(self):
        """Test AgentService initialization with default dependencies."""
        with patch("agent.service.get_context_assembler") as mock_get_context:
            from agent.service import AgentService

            mock_context_assembler = MagicMock()
            mock_get_context.return_value = mock_context_assembler

            service = AgentService()

            assert service.context_assembler == mock_context_assembler
            mock_get_context.assert_called_once()

    def test_agent_service_init_with_dependencies(self):
        """Test AgentService initialization with provided dependencies."""
        mock_context_assembler = MagicMock()

        from agent.service import AgentService
        service = AgentService(
            context_assembler=mock_context_assembler,
        )

        assert service.context_assembler == mock_context_assembler




@pytest.mark.integration
class TestAgentServiceProcessStream:
    """Integration tests for process_stream method with mocked LangGraph workflow."""

    @pytest.fixture
    def mock_workflow_stream(self):
        """Create a mock LangGraph workflow stream."""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        async def mock_stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Hello"})
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": " World"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return mock_stream

    async def test_process_stream_simple_response(
        self, mock_agent_service, test_user_with_project, db_session: Session, mock_workflow_stream
    ):
        """Test processing a simple user message with text response."""
        service, mock_context_assembler = mock_agent_service

        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_workflow_stream()

            # Collect events
            events = []
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="Hello",
                session=db_session,
            ):
                events.append(event)

            # Verify we got events
            assert len(events) > 0

            # Should get at least thinking and done events
            event_types = set()
            for event in events:
                if "event: thinking" in event:
                    event_types.add("thinking")
                elif "event: content" in event:
                    event_types.add("content")
                elif "event: done" in event:
                    event_types.add("done")

            assert len(event_types) > 0

    async def test_process_stream_with_chat_history(
        self, mock_agent_service, test_user_with_project, db_session: Session, mock_workflow_stream
    ):
        """Test processing message with existing chat history."""
        service, mock_context_assembler = mock_agent_service

        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        # Create existing chat session
        chat_session = ChatSession(
            user_id=str(user.id),
            project_id=str(project.id),
            title="Test Chat",
            is_active=True,
            message_count=2,
        )
        db_session.add(chat_session)
        db_session.commit()
        db_session.refresh(chat_session)

        # Add existing messages
        db_session.add(ChatMessage(
            session_id=chat_session.id,
            role="user",
            content="Previous message",
        ))
        db_session.add(ChatMessage(
            session_id=chat_session.id,
            role="assistant",
            content="Previous response",
        ))
        db_session.commit()

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_workflow_stream()

            # Process stream
            events = []
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="New message",
                session=db_session,
            ):
                events.append(event)

            # Verify events received
            assert len(events) > 0

    async def test_process_stream_with_selected_text(
        self, mock_agent_service, test_user_with_project, db_session: Session, mock_workflow_stream
    ):
        """Test processing message with selected text context."""
        service, mock_context_assembler = mock_agent_service

        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_workflow_stream()

            selected_text = "This is the selected text"
            events = []
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="Explain this",
                session=db_session,
                selected_text=selected_text,
            ):
                events.append(event)

            assert len(events) > 0

    @staticmethod
    def _skill_matched_payloads(events: list[str]) -> list[dict]:
        payloads = []
        for event in events:
            if "event: skill_matched" in event:
                data_line = next(line for line in event.splitlines() if line.startswith("data:"))
                payloads.append(json.loads(data_line[len("data:"):].strip()))
        return payloads

    async def test_process_stream_injects_selected_skills_and_records_usage(
        self,
        mock_agent_service,
        test_user_with_project,
        db_session: Session,
        mock_workflow_stream,
    ):
        """selected_skill_ids 注入完整方法 + 资源清单，记录用量并在流开头发 skill_matched。"""
        service, _ = mock_agent_service

        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        skill = UserSkill(
            user_id=user.id,
            name="悬念大师",
            description="增强钩子和悬念",
            triggers=json.dumps(["悬念大师"]),
            instructions="先强化钩子，再收紧悬念。",
            is_active=True,
        )
        db_session.add(skill)
        db_session.commit()
        db_session.add(SkillResource(
            user_skill_id=skill.id, path="references/hooks.md", content="钩子清单", size=12,
        ))
        db_session.commit()

        raw_message = "帮我把第一段写得更有钩子"

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_workflow_stream()

            events = [
                event
                async for event in service.process_stream(
                    project_id=str(project.id),
                    user_id=str(user.id),
                    message=raw_message,
                    session=db_session,
                    selected_skill_ids=[skill.id],
                )
            ]

        writing_state = mock_workflow.call_args.args[0]
        assert writing_state["router_message"] == raw_message
        assert writing_state["user_message"] == raw_message
        prompt = writing_state["system_prompt"]
        assert "## 用户本条消息指定技能" in prompt
        assert "### 悬念大师" in prompt
        assert "先强化钩子，再收紧悬念。" in prompt
        assert "- references/hooks.md" in prompt
        assert "使用技能" not in prompt

        assert self._skill_matched_payloads(events) == [
            {"skill_id": skill.id, "skill_name": "悬念大师", "matched_trigger": "selected"},
        ]

        usages = db_session.exec(select(SkillUsage).where(SkillUsage.project_id == project.id)).all()
        assert [(u.skill_id, u.skill_source, u.matched_trigger) for u in usages] == [
            (skill.id, "user", "selected"),
        ]

    async def test_selected_skill_is_not_recorded_or_announced_again_by_load_skill(
        self,
        mock_agent_service,
        test_user_with_project,
        db_session: Session,
    ):
        """显式选择的技能：ToolContext 与适配器都预先登记，模型再 load_skill 它时不重复记用量/发事件。"""
        from agent.core.workflow_events import StreamEvent, StreamEventType
        from agent.tools.mcp_tools import ToolContext

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        skill = UserSkill(user_id=user.id, name="悬念大师", instructions="先强化钩子。", is_active=True)
        db_session.add(skill)
        db_session.commit()

        claims: list[bool] = []

        async def workflow_stream():
            claims.append(ToolContext.claim_skill_usage(skill.id))
            payload = {
                "status": "success",
                "data": {"skill_id": skill.id, "skill_name": "悬念大师", "source": "user",
                         "instructions": "先强化钩子。", "resources": []},
            }
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "tool_use_id": "call-1",
                    "name": "load_skill",
                    "result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]},
                },
            )
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = workflow_stream()
            events = [
                event
                async for event in service.process_stream(
                    project_id=str(project.id),
                    user_id=str(user.id),
                    message="写得更有钩子",
                    session=db_session,
                    selected_skill_ids=[skill.id],
                )
            ]

        assert claims == [False]
        assert self._skill_matched_payloads(events) == [
            {"skill_id": skill.id, "skill_name": "悬念大师", "matched_trigger": "selected"},
        ]

    async def test_selected_skill_usage_failure_does_not_roll_back_request_session(
        self,
        mock_agent_service,
        test_user_with_project,
        db_session: Session,
    ):
        """记录 selected 用量失败时，只回滚独立 session；请求 session 上未提交的对象保持不动。"""
        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        skill = UserSkill(user_id=user.id, name="悬念大师", instructions="先强化钩子。", is_active=True)
        db_session.add(skill)
        db_session.commit()
        pending = UserSkill(user_id=user.id, name="未提交的技能", instructions="x")
        db_session.add(pending)

        with patch(
            "services.skill_usage_service.record_skill_usage",
            side_effect=RuntimeError("usage table unavailable"),
        ):
            selected = service._resolve_selected_skills(
                db_session,
                project_id=str(project.id),
                user_id=str(user.id),
                selected_skill_ids=[skill.id],
                message="m",
            )

        assert [item["id"] for item in selected] == [skill.id]
        assert pending in db_session.new

    async def test_selected_skills_are_clipped_to_the_skill_token_budget(
        self,
        mock_agent_service,
        test_user_with_project,
        db_session: Session,
    ):
        """3 个超长技能全选时，注入 system prompt 的正文合计不超过显式选择预算，并提示分段续读。"""
        from agent.core.message_manager import MessageManager
        from agent.skills.content_budget import SELECTED_SKILLS_TOKEN_BUDGET
        from agent.utils.token_utils import estimate_text_tokens

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        long_text = ("人物动机必须清楚，冲突要逐步升级。" * 4000)[:50_000]
        skills = [
            UserSkill(user_id=user.id, name=f"长技能{index}", instructions=long_text, is_active=True)
            for index in range(3)
        ]
        short = UserSkill(user_id=user.id, name="短技能", instructions="只写对白。", is_active=True)
        db_session.add_all([*skills, short])
        db_session.commit()

        selected = service._resolve_selected_skills(
            db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            selected_skill_ids=[short.id, skills[0].id, skills[1].id],
            message="m",
        )

        assert [item["id"] for item in selected] == [short.id, skills[0].id, skills[1].id]
        assert selected[0]["instructions"] == "只写对白。"
        assert selected[0]["instructions_next_offset"] is None
        total_tokens = sum(estimate_text_tokens(item["instructions"]) for item in selected)
        assert total_tokens <= SELECTED_SKILLS_TOKEN_BUDGET
        assert sum(item["instructions_tokens"] for item in selected) <= SELECTED_SKILLS_TOKEN_BUDGET
        for item in selected[1:]:
            assert item["instructions_next_offset"] == len(item["instructions"])
            assert item["instructions_total_chars"] == 50_000

        section = "\n".join(
            MessageManager(project_id=str(project.id), user_id=str(user.id))._build_selected_skill_section(
                selected, False
            )
        )
        assert (
            f'read_skill_resource(name="{skills[0].id}", path="SKILL.md", '
            f'offset={selected[1]["instructions_next_offset"]})'
        ) in section

    async def test_process_stream_ignores_foreign_and_inactive_selected_skill_ids(
        self,
        mock_agent_service,
        test_user_with_project,
        db_session: Session,
        mock_workflow_stream,
    ):
        """别人的技能、停用技能、不存在的 ID 一律静默忽略。"""
        service, _ = mock_agent_service

        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        other = User(
            email="agent_service_other@example.com",
            username="agentserviceother",
            hashed_password=hash_password("password123"),
            email_verified=True,
            is_active=True,
        )
        db_session.add(other)
        db_session.commit()

        foreign = UserSkill(user_id=other.id, name="别人的技能", instructions="偷看", is_active=True)
        inactive = UserSkill(user_id=user.id, name="停用技能", instructions="停用", is_active=False)
        db_session.add(foreign)
        db_session.add(inactive)
        db_session.commit()

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_workflow_stream()

            events = [
                event
                async for event in service.process_stream(
                    project_id=str(project.id),
                    user_id=str(user.id),
                    message="帮我处理这一段",
                    session=db_session,
                    selected_skill_ids=[foreign.id, inactive.id, "no-such-id"],
                )
            ]

        prompt = mock_workflow.call_args.args[0]["system_prompt"]
        assert "## 用户本条消息指定技能" not in prompt
        assert "偷看" not in prompt
        assert self._skill_matched_payloads(events) == []
        assert db_session.exec(select(SkillUsage)).all() == []

    async def test_process_stream_resolves_added_skill_by_added_id(
        self,
        mock_agent_service,
        test_user_with_project,
        db_session: Session,
        mock_workflow_stream,
    ):
        """已添加的公共技能用 GET /skills 返回的 UserAddedSkill.id 选择，按自定义名称注入。"""
        service, _ = mock_agent_service

        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        public_skill = PublicSkill(
            name="氛围渲染器",
            description="强化氛围和情绪",
            instructions="优先写环境与感官细节。",
            tags="[]",
            status="approved",
        )
        db_session.add(public_skill)
        db_session.commit()
        db_session.refresh(public_skill)

        added = UserAddedSkill(
            user_id=user.id,
            public_skill_id=public_skill.id,
            custom_name="阴影编织者",
            is_active=True,
        )
        db_session.add(added)
        db_session.commit()

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_workflow_stream()

            events = [
                event
                async for event in service.process_stream(
                    project_id=str(project.id),
                    user_id=str(user.id),
                    message="帮我把这场戏写得更阴冷",
                    session=db_session,
                    selected_skill_ids=[added.id],
                )
            ]

        prompt = mock_workflow.call_args.args[0]["system_prompt"]
        assert "## 用户本条消息指定技能" in prompt
        assert "### 阴影编织者" in prompt
        assert "优先写环境与感官细节。" in prompt
        assert self._skill_matched_payloads(events) == [
            {"skill_id": public_skill.id, "skill_name": "阴影编织者", "matched_trigger": "selected"},
        ]

    async def test_process_stream_without_user_id(
        self, mock_agent_service, test_user_with_project, db_session: Session, mock_workflow_stream
    ):
        """Test processing message without user_id (no history saved)."""
        service, mock_context_assembler = mock_agent_service

        project = test_user_with_project["project"]

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_workflow_stream()

            events = []
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=None,  # No user_id
                message="Hello from anonymous",
                session=db_session,
            ):
                events.append(event)

            # Should still get response
            assert len(events) > 0

    async def test_process_stream_with_tool_calls(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """Tool results should be associated by tool_use_id instead of append-order."""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, mock_context_assembler = mock_agent_service

        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        async def mock_stream_with_tools():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Creating file..."})
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={
                    "id": "tool-1",
                    "name": "create_file",
                    "status": "complete",
                    "input": {"title": "第一章"},
                },
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={
                    "id": "tool-2",
                    "name": "query_files",
                    "status": "complete",
                    "input": {"query": "大纲"},
                },
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "tool_use_id": "tool-2",
                    "name": "query_files",
                    "result": {"content": [{"type": "text", "text": '{"status": "success"}'}]},
                },
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={
                    "tool_use_id": "tool-1",
                    "name": "create_file",
                    "result": {"content": [{"type": "text", "text": '{"status": "success"}'}]},
                },
            )
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_stream_with_tools()

            events = []
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="Create a new chapter",
                session=db_session,
            ):
                events.append(event)

            assert len(events) > 0

        chat_session = db_session.exec(
            select(ChatSession)
            .where(ChatSession.project_id == str(project.id), ChatSession.user_id == str(user.id))
            .order_by(desc(ChatSession.updated_at), desc(ChatSession.created_at), desc(ChatSession.id))
        ).first()
        assert chat_session is not None

        latest_assistant = db_session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == chat_session.id, ChatMessage.role == "assistant")
            .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        ).first()
        assert latest_assistant is not None
        assert latest_assistant.tool_calls is not None
        tool_calls = json.loads(latest_assistant.tool_calls)
        assert len(tool_calls) == 2

        call_by_id = {call["id"]: call for call in tool_calls}
        assert call_by_id["tool-1"]["name"] == "create_file"
        assert call_by_id["tool-1"]["status"] == "success"
        assert call_by_id["tool-2"]["name"] == "query_files"
        assert call_by_id["tool-2"]["status"] == "success"

    async def test_process_stream_persists_ordered_display_events(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """Persist cross-type display order without duplicating tool payloads."""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        async def ordered_stream():
            yield StreamEvent(
                type=StreamEventType.ROUTER_THINKING,
                data={"message": "Choosing the best agent"},
            )
            yield StreamEvent(
                type=StreamEventType.ROUTER_DECIDED,
                data={"agent_type": "planner", "reason": "Outline first"},
            )
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "before "})
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "tool"})
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={
                    "id": "tool-1",
                    "name": "query_files",
                    "input": {"query": "first"},
                    "status": "complete",
                },
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={"tool_use_id": "tool-1", "name": "query_files", "result": {"ok": True}},
            )
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": " after"})
            yield StreamEvent(
                type=StreamEventType.HANDOFF,
                data={"target_agent": "writer", "reason": "draft", "context": "draft it"},
            )
            yield StreamEvent(
                type=StreamEventType.AGENT_SELECTED,
                data={"agent_type": "writer", "agent_name": "Writer", "iteration": 2},
            )
            yield StreamEvent(type=StreamEventType.THINKING, data={"thinking": "plan "})
            yield StreamEvent(type=StreamEventType.THINKING, data={"thinking": "details"})
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "final"})
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={
                    "id": "tool-2",
                    "name": "query_files",
                    "input": {"query": "outline"},
                    "status": "complete",
                },
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={
                    "id": "tool-3",
                    "name": "query_files",
                    "input": {"query": "chapter"},
                    "status": "complete",
                },
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={"tool_use_id": "tool-3", "name": "query_files", "result": {"ok": True}},
            )
            yield StreamEvent(
                type=StreamEventType.ITERATION_EXHAUSTED,
                data={
                    "layer": "collaboration",
                    "iterations_used": 3,
                    "max_iterations": 3,
                    "reason": "limit reached",
                    "last_agent": "writer",
                },
            )
            yield StreamEvent(
                type=StreamEventType.WORKFLOW_COMPLETE,
                data={"reason": "completed", "agent_type": "writer"},
            )
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        with patch("agent.service.run_writing_workflow_streaming", return_value=ordered_stream()):
            async for _ in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="keep the visible order",
                session=db_session,
            ):
                pass

        latest_assistant = db_session.exec(
            select(ChatMessage)
            .where(ChatMessage.role == "assistant")
            .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        ).first()
        assert latest_assistant is not None
        assert latest_assistant.message_metadata is not None
        metadata = json.loads(latest_assistant.message_metadata)
        assert metadata["display_events"] == [
            {"type": "router_thinking", "data": {"message": "Choosing the best agent"}},
            {
                "type": "router_decided",
                "data": {"agent_type": "planner", "reason": "Outline first"},
            },
            {"type": "content", "content": "before tool"},
            {"type": "tool_call", "tool_call_index": 0},
            {"type": "content", "content": " after"},
            {
                "type": "handoff",
                "data": {"target_agent": "writer", "reason": "draft", "context": "draft it"},
            },
            {
                "type": "agent_selected",
                "data": {
                    "agent_type": "writer",
                    "agent_name": "Writer",
                    "iteration": 2,
                    "max_iterations": None,
                    "remaining": None,
                },
            },
            {"type": "thinking_content", "content": "plan details"},
            {"type": "content", "content": "final"},
            {"type": "tool_call", "tool_call_index": 1},
            {"type": "tool_call", "tool_call_index": 2},
            {
                "type": "iteration_exhausted",
                "data": {
                    "layer": "collaboration",
                    "iterations_used": 3,
                    "max_iterations": 3,
                    "reason": "limit reached",
                    "last_agent": "writer",
                },
            },
            {"type": "workflow_complete", "data": {"reason": "completed", "agent_type": "writer"}},
        ]
        assert [event for event in metadata["display_events"] if event["type"] == "tool_call"] == [
            {"type": "tool_call", "tool_call_index": 0},
            {"type": "tool_call", "tool_call_index": 1},
            {"type": "tool_call", "tool_call_index": 2},
        ]
        assert all("arguments" not in event and "result" not in event for event in metadata["display_events"])
        serialized_tool_calls = json.loads(latest_assistant.tool_calls or "[]")
        assert len(serialized_tool_calls) == 3
        assert serialized_tool_calls[0]["status"] == "success"
        assert serialized_tool_calls[1]["status"] == "pending"
        assert serialized_tool_calls[2]["status"] == "success"

    async def test_process_stream_persists_control_only_display_timeline(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """A meaningful control timeline must not be discarded as an empty assistant row."""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        async def control_only_stream():
            yield StreamEvent(
                type=StreamEventType.AGENT_SELECTED,
                data={
                    "agent_type": "writer",
                    "agent_name": "Writer",
                    "iteration": 1,
                    "max_iterations": 3,
                    "remaining": 2,
                },
            )
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        with patch(
            "agent.service.run_writing_workflow_streaming", return_value=control_only_stream()
        ):
            async for _ in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="route only",
                session=db_session,
            ):
                pass

        latest_assistant = db_session.exec(
            select(ChatMessage)
            .where(ChatMessage.role == "assistant")
            .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        ).first()
        assert latest_assistant is not None
        assert latest_assistant.content == ""
        assert json.loads(latest_assistant.message_metadata or "{}")["display_events"] == [
            {
                "type": "agent_selected",
                "data": {
                    "agent_type": "writer",
                    "agent_name": "Writer",
                    "iteration": 1,
                    "max_iterations": 3,
                    "remaining": 2,
                },
            }
        ]

    async def test_process_stream_with_custom_session_id(
        self, mock_agent_service, test_user_with_project, db_session: Session, mock_workflow_stream
    ):
        """Test process_stream can reuse caller-provided session ID."""
        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]
        custom_session_id = "test-session-id-123"

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_workflow_stream()

            events = []
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="Hello",
                session=db_session,
                session_id=custom_session_id,
            ):
                events.append(event)

        session_started_events = [event for event in events if "event: session_started" in event]
        assert session_started_events, "Expected session_started event"
        assert custom_session_id in session_started_events[0]

    async def test_process_stream_saves_to_resolved_session_even_if_it_becomes_inactive(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """Stream completion should persist to the resolved session, not whichever session is active later."""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        target_session = ChatSession(
            id="resolved-session-id",
            user_id=str(user.id),
            project_id=str(project.id),
            title="Resolved session",
            is_active=True,
            message_count=0,
        )
        newer_session = ChatSession(
            user_id=str(user.id),
            project_id=str(project.id),
            title="New active session",
            is_active=False,
            message_count=0,
        )
        db_session.add(target_session)
        db_session.add(newer_session)
        db_session.commit()
        db_session.refresh(target_session)
        db_session.refresh(newer_session)

        async def mock_stream_switching_active_session():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Draft reply"})
            target_session.is_active = False
            newer_session.is_active = True
            db_session.add(target_session)
            db_session.add(newer_session)
            db_session.commit()
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_stream_switching_active_session()

            async for _ in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="Persist to resolved session",
                session=db_session,
                session_id=target_session.id,
            ):
                pass

        saved_messages = db_session.exec(
            select(ChatMessage)
            .where(ChatMessage.session_id == target_session.id)
            .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        ).all()
        assert len(saved_messages) == 2
        assert {message.role for message in saved_messages} == {"user", "assistant"}
        assert any(message.role == "assistant" and message.content == "Draft reply" for message in saved_messages)

        newer_session_messages = db_session.exec(
            select(ChatMessage).where(ChatMessage.session_id == newer_session.id)
        ).all()
        assert newer_session_messages == []

    async def test_process_stream_passes_runtime_workflow_config(
        self, mock_agent_service, test_user_with_project, db_session: Session, mock_workflow_stream
    ):
        """Test process_stream passes centralized workflow config to writing graph."""
        from config.agent_runtime import (
            AGENT_AUTO_REVIEW_THRESHOLD_CHARS,
            AGENT_COLLABORATION_MAX_ITERATIONS,
        )

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_workflow_stream()

            async for _ in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="Hello",
                session=db_session,
            ):
                pass

        assert mock_workflow.call_args is not None
        _, kwargs = mock_workflow.call_args
        assert kwargs["max_iterations"] == AGENT_COLLABORATION_MAX_ITERATIONS
        assert kwargs["auto_review_threshold"] == AGENT_AUTO_REVIEW_THRESHOLD_CHARS

    async def test_process_stream_persists_stop_reason_and_usage_metadata(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """Assistant chat message should persist model stop_reason/usage metadata."""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        async def mock_stream_with_usage():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Answer"})
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={
                    "stop_reason": "end_turn",
                    "usage": {
                        "input_tokens": 111,
                        "output_tokens": 222,
                    },
                },
            )

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_stream_with_usage()

            async for _ in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="hello",
                session=db_session,
            ):
                pass

        stmt = (
            select(ChatMessage)
            .join(ChatSession, ChatMessage.session_id == ChatSession.id)
            .where(ChatSession.project_id == str(project.id))
            .where(ChatMessage.role == "assistant")
            .order_by(desc(ChatMessage.created_at))
        )
        latest_assistant = db_session.exec(stmt).first()
        assert latest_assistant is not None
        assert latest_assistant.message_metadata is not None

        metadata = json.loads(latest_assistant.message_metadata)
        assert metadata["stop_reason"] == "end_turn"
        assert metadata["usage"]["input_tokens"] == 111
        assert metadata["usage"]["output_tokens"] == 222

    @pytest.mark.parametrize("mutation", [None, False, True])
    async def test_process_stream_keeps_confirmed_mutation_after_history_save(
        self, mock_agent_service, test_user_with_project, db_session: Session, mutation
    ):
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        async def stream():
            result = {"status": "success", "data": {"count": 1}}
            if mutation is not None:
                result["mutation_applied"] = mutation
            yield StreamEvent(type=StreamEventType.TOOL_RESULT, data={"name": "parallel_execute", "result": result})
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Completed"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        with patch("agent.service.run_writing_workflow_streaming", return_value=stream()):
            events = [event async for event in service.process_stream(
                project_id=project.id, user_id=user.id, message="Continue", session=db_session,
            )]

        done = [json.loads(event.split("data:", 1)[1]) for event in events if "event: done" in event]
        assert len(done) == 1
        assert done[0]["file_mutated"] is (mutation is True)
        assert db_session.get(ChatMessage, done[0]["assistant_message_id"]) is not None

    @pytest.mark.parametrize("has_assistant_payload", [False, True])
    async def test_confirmed_mutation_does_not_emit_done_when_history_save_fails(
        self, mock_agent_service, test_user_with_project, db_session: Session,
        has_assistant_payload: bool,
    ):
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        async def stream():
            yield StreamEvent(type=StreamEventType.TOOL_RESULT, data={"name": "parallel_execute", "result": {"status": "success", "mutation_applied": True}})
            if has_assistant_payload:
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Completed"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        # Empty-assistant runs append user-only history through an independent
        # Session; nonempty runs use the async MessageManager save boundary.
        failure_target = (
            "agent.service.MessageManager.save_messages"
            if has_assistant_payload else "agent.service.create_session"
        )
        failure = (
            AsyncMock(side_effect=RuntimeError("db unavailable"))
            if has_assistant_payload else MagicMock(side_effect=RuntimeError("db unavailable"))
        )
        with patch("agent.service.run_writing_workflow_streaming", return_value=stream()), patch(
            failure_target, new=failure,
        ):
            events = [event async for event in service.process_stream(
                project_id=project.id, user_id=user.id, message="Continue", session=db_session,
            )]

        failure.assert_called()
        assert any("event: error" in event for event in events)
        assert not any("event: done" in event for event in events)

    async def test_process_stream_emits_done_with_persisted_assistant_message_id(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """Done event should be delayed until history save succeeds and include the assistant message ID."""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        async def mock_stream_with_clarification_stop():
            yield StreamEvent(
                type=StreamEventType.WORKFLOW_STOPPED,
                data={
                    "reason": "clarification_needed",
                    "question": "请确认主角姓名",
                },
            )
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_stream_with_clarification_stop()

            events: list[str] = []
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="需要澄清",
                session=db_session,
            ):
                events.append(event)

        done_events = [event for event in events if "event: done" in event]
        assert len(done_events) == 1
        done_payload = json.loads(done_events[0].split("data:", 1)[1].strip())
        assert done_payload["assistant_message_id"]

        saved_assistant = db_session.get(ChatMessage, done_payload["assistant_message_id"])
        assert saved_assistant is not None
        assert saved_assistant.message_metadata is not None
        metadata = json.loads(saved_assistant.message_metadata)
        assert metadata["status_cards"][0]["reason"] == "clarification_needed"
        assert metadata["display_events"] == [
            {
                "type": "workflow_stopped",
                "data": {"reason": "clarification_needed", "question": "请确认主角姓名"},
            }
        ]

    async def test_process_stream_pg_offload_branch_initializes_message_manager_before_save(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """PostgreSQL offload branch should still save history successfully."""
        from agent.core.session_loader import SessionData
        from agent.core.workflow_events import StreamEvent, StreamEventType
        from agent.schemas.context import ContextData

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        async def mock_stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "hello"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        session_data = SessionData(
            chat_session=None,
            session_id="sess-offload",
            history_messages=[],
            context_data=ContextData(items=[], context="", token_estimate=0),
        )

        with (
            patch.object(service, "_should_offload_session_work", return_value=True),
            patch.object(service, "_resolve_or_create_chat_session_id_sync", return_value="sess-offload"),
            patch("agent.service.SessionLoader.load_session_with_compaction", new=AsyncMock(return_value=session_data)),
            patch.object(
                service,
                "_prepare_prompt_artifacts_sync",
                return_value=([], "system prompt"),
            ),
            patch("agent.service.run_writing_workflow_streaming", return_value=mock_stream()),
            patch("agent.service.MessageManager.save_messages", new=AsyncMock(return_value="assistant-msg-offload")),
        ):
            events: list[str] = []
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="hello",
                session=db_session,
            ):
                events.append(event)

        assert any("event: done" in event for event in events)
        assert not any("event: error" in event for event in events)

    async def test_process_stream_emits_error_without_done_when_history_save_fails(
        self, mock_agent_service, test_user_with_project, db_session: Session, mock_workflow_stream
    ):
        """A failed history save must not emit done before the error reaches the client."""
        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow, patch(
            "agent.service.MessageManager.save_messages",
            new=AsyncMock(side_effect=RuntimeError("db unavailable")),
        ):
            mock_workflow.return_value = mock_workflow_stream()

            events: list[str] = []
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="hello",
                session=db_session,
            ):
                events.append(event)

        assert any("event: error" in event for event in events)
        assert not any("event: done" in event for event in events)

    async def test_process_stream_saves_partial_history_on_workflow_error(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """Stream error events should persist partial history for recovery, but NOT emit done."""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        async def mock_error_stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "partial reply"})
            yield StreamEvent(type=StreamEventType.ERROR, data={"error": "workflow failed"})

        with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
            mock_workflow.return_value = mock_error_stream()

            events: list[str] = []
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="hello",
                session=db_session,
            ):
                events.append(event)

        assert any("event: error" in event for event in events)
        assert not any("event: done" in event for event in events)

        session_started_events = [event for event in events if "event: session_started" in event]
        assert session_started_events, "Expected session_started event"
        session_started_payload = json.loads(session_started_events[0].split("data:", 1)[1].strip())
        persisted_messages = db_session.exec(
            select(ChatMessage).where(ChatMessage.session_id == session_started_payload["session_id"])
        ).all()
        # Partial history is saved for recovery on next turn
        assert len(persisted_messages) == 2
        assert any(m.role == "user" and m.content == "hello" for m in persisted_messages)
        assert any(m.role == "assistant" and m.content == "partial reply" for m in persisted_messages)
        partial_assistant = next(m for m in persisted_messages if m.role == "assistant")
        assert json.loads(partial_assistant.message_metadata or "{}")["display_events"] == [
            {"type": "content", "content": "partial reply"}
        ]

    async def test_process_stream_schedules_background_cleanup_on_cancellation(
        self, mock_agent_service, test_user_with_project, db_session: Session, caplog
    ):
        """Cancellation should schedule steering cleanup instead of awaiting it inline."""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        started = asyncio.Event()

        async def mock_slow_stream():
            started.set()
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "partial"})
            await asyncio.sleep(3600)

        async def consume():
            async for _event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="cancel me",
                session=db_session,
            ):
                pass

        with (
            patch("agent.service.run_writing_workflow_streaming", return_value=mock_slow_stream()),
            patch("agent.service.cleanup_steering_queue_async", new=AsyncMock()) as mock_cleanup,
            patch.object(service, "_schedule_background_cleanup") as mock_schedule_cleanup,
        ):
            def _consume_cleanup_coro(coro, **_kwargs):
                coro.close()

            mock_schedule_cleanup.side_effect = _consume_cleanup_coro
            with caplog.at_level(logging.INFO, logger="agent.service"):
                task = asyncio.create_task(consume())
                await started.wait()
                await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        mock_cleanup.assert_not_awaited()
        descriptions = [
            kwargs["description"] for _args, kwargs in mock_schedule_cleanup.call_args_list
        ]
        assert "cleanup_steering_queue_async" in descriptions
        # Cancellation also schedules the partial-history save in the background.
        assert "save_partial_history_after_cancellation" in descriptions
        completion_logs = [
            record for record in caplog.records
            if record.name == "agent.service" and record.message == "Agent process_stream cancelled"
        ]
        assert len(completion_logs) == 1
        assert completion_logs[0].levelno == logging.INFO

    async def test_process_stream_persists_partial_history_on_cancellation(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """Cancellation must still persist the user message + partial assistant reply."""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        started = asyncio.Event()

        routing_usage = {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15}

        async def mock_slow_stream():
            yield StreamEvent(
                type=StreamEventType.ROUTER_DECIDED,
                data={"agent_type": "writer", "routing_usage": routing_usage},
            )
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "partial reply"})
            started.set()
            await asyncio.sleep(3600)

        events: list[str] = []

        async def consume():
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="cancel me please",
                session=db_session,
            ):
                events.append(event)

        def _fresh_session():
            return Session(db_session.get_bind())

        with (
            patch("agent.service.run_writing_workflow_streaming", return_value=mock_slow_stream()),
            patch("agent.service.create_session", side_effect=_fresh_session),
        ):
            task = asyncio.create_task(consume())
            await started.wait()
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            session_started_events = [e for e in events if "event: session_started" in e]
            assert session_started_events, "Expected session_started event"
            session_id = json.loads(
                session_started_events[0].split("data:", 1)[1].strip()
            )["session_id"]

            # The save runs as a background task with an independent session;
            # poll until it lands (rollback releases this session's read txn).
            saved_messages: list[ChatMessage] = []
            for _ in range(100):
                await asyncio.sleep(0.05)
                db_session.rollback()
                saved_messages = db_session.exec(
                    select(ChatMessage).where(ChatMessage.session_id == session_id)
                ).all()
                if len(saved_messages) >= 2:
                    break

        assert len(saved_messages) == 2
        assert any(
            m.role == "user" and m.content == "cancel me please" for m in saved_messages
        )
        assert any(
            m.role == "assistant" and m.content == "partial reply" for m in saved_messages
        )
        partial_assistant = next(m for m in saved_messages if m.role == "assistant")
        display_events = json.loads(partial_assistant.message_metadata or "{}")["display_events"]
        assert display_events[-1] == {"type": "content", "content": "partial reply"}
        # Usage already accumulated before the cancel (here: routing) is kept.
        assert json.loads(partial_assistant.message_metadata or "{}")["usage"] == routing_usage

    async def test_process_stream_prompt_ledger_counts_embedded_sections_once(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """reserved_prompt_tokens must charge the final system prompt exactly once."""
        from agent.context.budget import compute_history_token_budget as real_compute
        from agent.core.workflow_events import StreamEvent, StreamEventType
        from agent.schemas.context import ContextData
        from agent.utils.token_utils import estimate_text_tokens
        from config.agent_runtime import AGENT_CHAT_HISTORY_TOKEN_BUDGET

        service, mock_context_assembler = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        assembled_context = "主角在北境雪原遇到了会说话的狐狸。" * 200
        mock_context_assembler.assemble.return_value = ContextData(
            items=[],
            context=assembled_context,
            token_estimate=estimate_text_tokens(assembled_context),
        )

        captured: dict[str, int] = {}

        def fake_compute(*, configured_history_budget, reserved_prompt_tokens):
            captured["reserved"] = reserved_prompt_tokens
            result = real_compute(
                configured_history_budget=configured_history_budget,
                reserved_prompt_tokens=reserved_prompt_tokens,
            )
            captured["effective"] = result
            return result

        async def mock_stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "ok"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        with (
            patch("agent.service.run_writing_workflow_streaming") as mock_workflow,
            patch("agent.service.compute_history_token_budget", side_effect=fake_compute),
        ):
            mock_workflow.return_value = mock_stream()

            async for _ in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="预算测试",
                session=db_session,
            ):
                pass

        writing_state = mock_workflow.call_args[0][0]
        system_prompt = writing_state["system_prompt"]
        assert assembled_context[:200] in system_prompt  # context 确实内嵌于 system_prompt

        # Ledger charges the final prompt once — no re-adding of skill
        # catalog/reference/context that build_system_prompt already embedded.
        assert captured["reserved"] == estimate_text_tokens(system_prompt)

        legacy_reserved = captured["reserved"] + estimate_text_tokens(assembled_context)
        assert captured["reserved"] < legacy_reserved
        assert captured["effective"] == real_compute(
            configured_history_budget=AGENT_CHAT_HISTORY_TOKEN_BUDGET,
            reserved_prompt_tokens=captured["reserved"],
        )
        assert captured["effective"] >= real_compute(
            configured_history_budget=AGENT_CHAT_HISTORY_TOKEN_BUDGET,
            reserved_prompt_tokens=legacy_reserved,
        )

    async def test_process_stream_persists_unconsumed_steering_before_cleanup(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """流结束时队列里仍未消费的 steering 必须随本轮历史落库，
        不能被 cleanup 静默删除（POST /steer 已经向用户确认 queued）。"""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        def fake_workflow(writing_state, **_kwargs):
            async def _stream():
                # 模拟用户在生成期间通过 POST /agent/steer 入队，而工作流
                # 全程没有任何消费点取走它（单迭代 quick 工作流的真实形态）
                from agent.core.steering import get_steering_queue_for_user_async

                queue = await get_steering_queue_for_user_async(
                    writing_state["session_id"], str(user.id)
                )
                await queue.add("生成中途的引导：改成第一人称")
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "正文"})
                yield StreamEvent(
                    type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"}
                )

            return _stream()

        with patch(
            "agent.service.run_writing_workflow_streaming", side_effect=fake_workflow
        ):
            events: list[str] = []
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="写一段",
                session=db_session,
            ):
                events.append(event)

        session_started_events = [e for e in events if "event: session_started" in e]
        assert session_started_events, "Expected session_started event"
        session_id = json.loads(
            session_started_events[0].split("data:", 1)[1].strip()
        )["session_id"]

        db_session.rollback()
        persisted = db_session.exec(
            select(ChatMessage).where(ChatMessage.session_id == session_id)
        ).all()
        assert any(
            m.role == "user" and m.content == "生成中途的引导：改成第一人称"
            for m in persisted
        ), "未消费的 steering 消息必须持久化为用户消息"

    async def test_process_stream_cancellation_persists_queued_steering(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """取消路径同样不能丢弃已入队未消费的 steering：后台保存任务先 drain
        队列再落库，cleanup 在保存完成后才删除队列。"""
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        started = asyncio.Event()

        def fake_workflow(writing_state, **_kwargs):
            async def _stream():
                from agent.core.steering import get_steering_queue_for_user_async

                queue = await get_steering_queue_for_user_async(
                    writing_state["session_id"], str(user.id)
                )
                await queue.add("取消前入队的引导")
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": "部分内容"})
                started.set()
                await asyncio.sleep(3600)

            return _stream()

        events: list[str] = []

        async def consume():
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="取消我",
                session=db_session,
            ):
                events.append(event)

        def _fresh_session():
            return Session(db_session.get_bind())

        with (
            patch("agent.service.run_writing_workflow_streaming", side_effect=fake_workflow),
            patch("agent.service.create_session", side_effect=_fresh_session),
        ):
            task = asyncio.create_task(consume())
            await started.wait()
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            session_started_events = [e for e in events if "event: session_started" in e]
            assert session_started_events, "Expected session_started event"
            session_id = json.loads(
                session_started_events[0].split("data:", 1)[1].strip()
            )["session_id"]

            steering_rows: list[ChatMessage] = []
            for _ in range(100):
                await asyncio.sleep(0.05)
                db_session.rollback()
                steering_rows = [
                    m
                    for m in db_session.exec(
                        select(ChatMessage).where(ChatMessage.session_id == session_id)
                    ).all()
                    if m.role == "user" and m.content == "取消前入队的引导"
                ]
                if steering_rows:
                    break

        assert steering_rows, "取消时已入队未消费的 steering 必须先落库再清理队列"

    async def test_cancellation_during_history_save_does_not_duplicate_history(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """取消恰好落在 save_messages 的工作线程期间：线程照样提交完整历史，
        补偿保存必须等它的真实结果，不能把同一轮再写一遍。"""
        from agent.core.message_manager import MessageManager
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        bind = db_session.get_bind()
        worker_started = threading.Event()
        allow_commit = threading.Event()
        real_save_with_session = MessageManager._save_messages_with_session

        async def mock_stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "完整回复"})
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"}
            )

        async def fake_save_messages(self, session, *args, **kwargs):
            # 复刻 PG 路径：save_messages 走 asyncio.to_thread，工作线程一旦
            # 启动就无法取消，awaiting 侧抛 CancelledError 也照样提交。
            def _commit_in_worker():
                worker_started.set()
                allow_commit.wait(5)
                with Session(bind) as worker_session:
                    return real_save_with_session(self, worker_session, *args, **kwargs)

            return await asyncio.to_thread(_commit_in_worker)

        events: list[str] = []

        async def consume():
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="保存中途取消",
                session=db_session,
            ):
                events.append(event)

        scheduled: list[asyncio.Task] = []
        real_schedule = service._schedule_background_cleanup

        def _tracking_schedule(coro, **kwargs):
            task = real_schedule(coro, **kwargs)
            scheduled.append(task)
            return task

        with (
            patch("agent.service.run_writing_workflow_streaming", return_value=mock_stream()),
            patch("agent.service.create_session", side_effect=lambda: Session(bind)),
            patch.object(MessageManager, "save_messages", new=fake_save_messages),
            patch.object(
                service, "_schedule_background_cleanup", side_effect=_tracking_schedule
            ),
        ):
            task = asyncio.create_task(consume())
            for _ in range(500):
                if worker_started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert worker_started.is_set(), "落库工作线程未启动，测试前置条件不成立"

            task.cancel()
            allow_commit.set()
            with pytest.raises(asyncio.CancelledError):
                await task

            # 等所有后台任务（补偿保存 + 队列清理）跑完再断言，避免竞态假绿
            if scheduled:
                await asyncio.gather(*scheduled, return_exceptions=True)

            session_started_events = [e for e in events if "event: session_started" in e]
            assert session_started_events, "Expected session_started event"
            session_id = json.loads(
                session_started_events[0].split("data:", 1)[1].strip()
            )["session_id"]

            db_session.rollback()
            # 测试 session 是 expire_on_commit=False，process_stream 在进入工作流
            # 前会提交请求级 session；之后若没有新事务，rollback() 是空操作、不会
            # 让 identity map 失效，get(ChatSession) 可能拿到 message_count=0 的旧对象。
            db_session.expire_all()
            persisted = db_session.exec(
                select(ChatMessage).where(ChatMessage.session_id == session_id)
            ).all()

        user_rows = [m for m in persisted if m.role == "user"]
        assistant_rows = [m for m in persisted if m.role == "assistant"]
        assert len(user_rows) == 1, (
            f"整轮历史必须恰好落库一次（不重复、不丢失）: {[m.content for m in user_rows]}"
        )
        assert len(assistant_rows) == 1, "assistant 消息必须恰好落库一次"
        assert assistant_rows[0].content == "完整回复"

        chat_session = db_session.get(ChatSession, session_id)
        assert chat_session is not None
        assert chat_session.message_count == 2, "message_count 被重复累加"

    async def test_cancellation_falls_back_to_partial_save_when_history_save_fails(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        """落库任务自身失败时，取消路径仍要补偿保存，不能因为「已尝试保存」就丢历史。"""
        from agent.core.message_manager import MessageManager
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]

        bind = db_session.get_bind()
        worker_started = threading.Event()
        allow_fail = threading.Event()

        async def mock_stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "部分回复"})
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"}
            )

        async def failing_save_messages(self, session, *args, **kwargs):
            def _fail_in_worker():
                worker_started.set()
                allow_fail.wait(5)
                raise RuntimeError("db unavailable")

            return await asyncio.to_thread(_fail_in_worker)

        events: list[str] = []

        async def consume():
            async for event in service.process_stream(
                project_id=str(project.id),
                user_id=str(user.id),
                message="落库失败后取消",
                session=db_session,
            ):
                events.append(event)

        scheduled: list[asyncio.Task] = []
        real_schedule = service._schedule_background_cleanup

        def _tracking_schedule(coro, **kwargs):
            task = real_schedule(coro, **kwargs)
            scheduled.append(task)
            return task

        with (
            patch("agent.service.run_writing_workflow_streaming", return_value=mock_stream()),
            patch("agent.service.create_session", side_effect=lambda: Session(bind)),
            patch.object(MessageManager, "save_messages", new=failing_save_messages),
            patch.object(
                service, "_schedule_background_cleanup", side_effect=_tracking_schedule
            ),
        ):
            task = asyncio.create_task(consume())
            for _ in range(500):
                if worker_started.is_set():
                    break
                await asyncio.sleep(0.01)
            assert worker_started.is_set(), "落库工作线程未启动，测试前置条件不成立"

            task.cancel()
            allow_fail.set()
            with pytest.raises(asyncio.CancelledError):
                await task

            if scheduled:
                await asyncio.gather(*scheduled, return_exceptions=True)

            session_started_events = [e for e in events if "event: session_started" in e]
            assert session_started_events, "Expected session_started event"
            session_id = json.loads(
                session_started_events[0].split("data:", 1)[1].strip()
            )["session_id"]

            db_session.rollback()
            persisted = db_session.exec(
                select(ChatMessage).where(ChatMessage.session_id == session_id)
            ).all()

        assert any(
            m.role == "user" and m.content == "落库失败后取消" for m in persisted
        ), "落库失败 + 取消时必须补偿保存用户消息"
        assert any(
            m.role == "assistant" and m.content == "部分回复" for m in persisted
        ), "落库失败 + 取消时必须补偿保存已生成内容"


@pytest.mark.unit
class TestAgentServiceHelpers:
    """Tests for helper methods and utilities."""

    def test_get_agent_service_singleton(self):
        """Test that get_agent_service returns singleton instance."""
        with patch("agent.service.get_context_assembler"):
            import agent.service as service_module
            from agent.service import get_agent_service

            # Reset singleton
            service_module._service = None

            service1 = get_agent_service()
            service2 = get_agent_service()

            assert service1 is service2  # Same instance

            # Clean up
            service_module._service = None

    def test_agent_max_iterations_constant(self):
        """service re-exports the configured iteration budget (a positive int)."""
        from agent.service import AGENT_MAX_ITERATIONS
        from config.agent_runtime import AGENT_MAX_ITERATIONS as CONFIGURED

        assert AGENT_MAX_ITERATIONS == CONFIGURED
        assert AGENT_MAX_ITERATIONS > 0


@pytest.mark.integration
class TestProcessStreamRunHeartbeatAndReport:
    """生成期间周期续期会话持有；结束后停止心跳；运行摘要写入 run_report。"""

    async def test_heartbeat_runs_during_generation_and_stops_after(
        self, mock_agent_service, test_user_with_project, db_session: Session
    ):
        from agent.core.workflow_events import StreamEvent, StreamEventType

        service, _ = mock_agent_service
        project = test_user_with_project["project"]
        user = test_user_with_project["user"]
        beats: list[tuple[str, str]] = []

        async def fake_heartbeat(session_id, run_id):
            beats.append((session_id, run_id))

        async def slow_workflow():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第一段"})
            await asyncio.sleep(0.2)
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={"stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5}},
            )

        run_report: dict = {}
        with (
            patch("agent.service.run_writing_workflow_streaming", return_value=slow_workflow()),
            patch("agent.service._RUN_HEARTBEAT_INTERVAL_S", 0.03),
            patch("agent.service.heartbeat_steering_run_async", side_effect=fake_heartbeat),
        ):
            events = [
                event
                async for event in service.process_stream(
                    project_id=str(project.id),
                    user_id=str(user.id),
                    message="写第一段",
                    session=db_session,
                    run_report=run_report,
                )
            ]
            beats_at_end = len(beats)
            await asyncio.sleep(0.1)

        assert any("event: done" in event for event in events)
        assert beats_at_end >= 2
        assert len({run_id for _, run_id in beats}) == 1
        # 生成结束、持有释放后心跳不再继续。
        assert len(beats) == beats_at_end
        assert run_report["model"]
        assert run_report["input_tokens"] == 10
        assert run_report["output_tokens"] == 5
        assert run_report["stop_reason"] == "end_turn"
        assert "model_calls" in run_report and "llm_duration_ms" in run_report
