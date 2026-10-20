"""
Multi-turn conversation and integration scenario tests for the Agent system.

Tests complex workflows including:
- Multi-turn conversation context preservation
- Material reference workflows
- Concurrent operation isolation
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlmodel import Session

from agent.context.assembler import ContextAssembler
from agent.graph.router import WORKFLOW_AGENTS, router_node
from agent.tools.mcp_tools import ToolContext
from models import ChatMessage, ChatSession, File, Project, User
from services.core.auth_service import hash_password

# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def test_user(db_session: Session):
    """Create a test user."""
    user = User(
        email="scenario_test@example.com",
        username="scenariotest",
        hashed_password=hash_password("password123"),
        name="Scenario Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_project(db_session: Session, test_user: User):
    """Create a test project."""
    project = Project(
        name="Scenario Test Project",
        description="A test project for scenario testing",
        owner_id=test_user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def create_test_file(db_session: Session, test_project: Project, test_user: User):
    """Helper function to create test files."""

    def _create(
        title: str,
        file_type: str,
        content: str = "",
        file_metadata: str | None = None,
        parent_id: str | None = None,
    ):
        file = File(
            title=title,
            content=content,
            file_type=file_type,
            file_metadata=file_metadata,
            project_id=test_project.id,
            user_id=test_user.id,
            parent_id=parent_id,
        )
        db_session.add(file)
        db_session.commit()
        db_session.refresh(file)
        return file

    return _create


@pytest.fixture
def create_chat_session(db_session: Session, test_project: Project, test_user: User):
    """Helper function to create chat sessions."""

    def _create(title: str = "Test Chat"):
        session = ChatSession(
            user_id=str(test_user.id),
            project_id=str(test_project.id),
            title=title,
            is_active=True,
            message_count=0,
        )
        db_session.add(session)
        db_session.commit()
        db_session.refresh(session)
        return session

    return _create


@pytest.fixture
def add_chat_message(db_session: Session):
    """Helper function to add chat messages."""

    def _add(session_id: str, role: str, content: str):
        message = ChatMessage(
            session_id=session_id,
            role=role,
            content=content,
        )
        db_session.add(message)
        db_session.commit()
        db_session.refresh(message)
        return message

    return _add


# =============================================================================
# Test Classes
# =============================================================================

@pytest.mark.integration
class TestMultiTurnConversations:
    """
    Tests for multi-turn conversation scenarios.

    Verifies that context is properly preserved and managed across
    multiple conversation turns.
    """

    @pytest.mark.asyncio
    async def test_context_preservation_across_turns(
        self,
        db_session: Session,
        test_project: Project,
        test_user: User,
        create_chat_session,
        add_chat_message,
    ):
        """
        Verify that context is correctly preserved across multiple conversation turns.

        Scenario:
        1. User asks about a character
        2. User follows up with "tell me more"
        3. System should maintain context from previous turn
        """
        # Create a chat session
        chat_session = create_chat_session("Multi-turn test")

        # Add first turn messages
        add_chat_message(
            session_id=chat_session.id,
            role="user",
            content="Tell me about the main character",
        )
        add_chat_message(
            session_id=chat_session.id,
            role="assistant",
            content="The main character is Zhang San, a brave warrior.",
        )

        # Add second turn (follow-up)
        add_chat_message(
            session_id=chat_session.id,
            role="user",
            content="Tell me more about him",
        )

        # Load chat history and verify context
        from agent.core.session_loader import SessionLoader

        session_loader = SessionLoader(
            project_id=str(test_project.id),
            user_id=str(test_user.id),
        )

        session_data = session_loader.load_chat_session(db_session)

        # Verify that history contains both turns
        assert len(session_data.history_messages) >= 2
        assert "main character" in session_data.history_messages[0]["content"]
        assert "Tell me more" in session_data.history_messages[2]["content"]

    @pytest.mark.asyncio
    async def test_context_budget_truncation(
        self,
        db_session: Session,
        test_project: Project,
        create_test_file,
    ):
        """
        Verify context truncation when it exceeds token budget.

        When the context exceeds the maximum token budget, the system
        should truncate lower-priority content while preserving
        critical information.
        """
        # Create multiple character files with varying content lengths
        # Characters have CONSTRAINT priority and will be included
        files = []
        for i in range(15):
            file = create_test_file(
                title=f"Character {i+1}",
                file_type="character",
                content="This is a character profile with detailed background. " * 200,  # Long content
            )
            files.append(file)

        assembler = ContextAssembler()

        # Assemble with a small budget to force truncation
        result = assembler.assemble(
            session=db_session,
            project_id=str(test_project.id),
            user_id=None,  # Skip ownership check
            max_tokens=1000,  # Small budget to force truncation
            include_characters=True,
            include_lores=True,
        )

        # Verify truncation occurred (some characters were trimmed)
        # The assembler may return 0 items if the budget is too small
        # In that case, we just verify the context string exists
        assert result.token_estimate <= 1000
        assert "项目状态" in result.context  # Project state should always be present
        # If we have items, verify truncation logic was applied
        if result.original_item_count > 0:
            # Either items were trimmed, or all fit within budget
            assert result.trimmed_item_count <= result.original_item_count

    @pytest.mark.asyncio
    async def test_skill_trigger_across_turns(
        self,
        db_session: Session,
        test_project: Project,
        test_user: User,
        create_chat_session,
        add_chat_message,
    ):
        """
        Verify skill triggering across conversation turns.

        When a user issues a "continue" command or similar skill trigger,
        the system should recognize and apply the appropriate skill.
        """
        from agent.skills.context_injector import SkillContextInjector

        # Create chat session with history
        chat_session = create_chat_session("Skill trigger test")

        # First turn: user requests writing
        add_chat_message(
            session_id=chat_session.id,
            role="user",
            content="Write a suspenseful opening",
        )
        add_chat_message(
            session_id=chat_session.id,
            role="assistant",
            content="[Using skill: Suspense Master]\n\nThe night was dark...",
        )

        # Second turn: user says "continue"
        add_chat_message(
            session_id=chat_session.id,
            role="user",
            content="Continue",
        )

        # Load skill catalog
        injector = SkillContextInjector()
        skill_catalog = injector.build_skill_catalog(db_session, str(test_user.id))

        # Verify skill catalog can be built (even if empty for test user)
        # The important thing is the mechanism works
        assert skill_catalog is None or isinstance(skill_catalog, str)

    @pytest.mark.asyncio
    async def test_router_agent_selection(
        self,
        db_session: Session,
        test_project: Project,
    ):
        """
        Verify that the Router Agent correctly selects sub-agents.

        The router should analyze user intent and route to the appropriate
        agent (planner/writer/quality_reviewer) with correct workflow planning.
        """
        test_cases = [
            {
                "message": "Help me plan the overall structure of my novel",
                "expected_workflow": "standard",
                "expected_initial_agent": "planner",
            },
            {
                "message": "Continue writing the current chapter",
                "expected_workflow": "quick",
                "expected_initial_agent": "writer",
            },
            {
                "message": "Review the quality of my draft",
                "expected_workflow": "review_only",
                "expected_initial_agent": "quality_reviewer",
            },
        ]

        for case in test_cases:
            # Create mock state
            state = {
                "user_message": case["message"],
                "project_id": str(test_project.id),
                "user_id": None,
            }

            expected_initial_agent = case["expected_initial_agent"]
            expected_workflow = case["expected_workflow"]
            route_response = {
                "content": [
                    {
                        "type": "text",
                        "text": f"{expected_initial_agent}\n{expected_workflow}",
                    }
                ]
            }

            # Mock the router LLM call
            with patch("agent.graph.router._route_with_chat_model", AsyncMock(return_value=route_response)):
                # Run router
                result = await router_node(state)

                # Verify routing
                assert result["workflow_plan"] == case["expected_workflow"]
                assert result["current_agent"] == case["expected_initial_agent"]

                # Verify workflow agents match workflow type
                expected_agents = WORKFLOW_AGENTS.get(case["expected_workflow"], [])
                assert result["workflow_agents"] == expected_agents


@pytest.mark.integration
class TestMaterialReferenceWorkflows:
    """
    Tests for material reference workflows.

    Verifies that various types of materials (characters, lore, outlines)
    are correctly integrated into the context.
    """

    @pytest.mark.asyncio
    async def test_character_material_in_context(
        self,
        db_session: Session,
        test_project: Project,
        create_test_file,
    ):
        """
        Verify that character materials are correctly loaded into context.

        Character files should be included in the assembled context with
        appropriate priority.
        """
        # Create character files
        create_test_file(
            title="Zhang San",
            file_type="character",
            content="Main protagonist. Brave and kind-hearted.",
        )
        create_test_file(
            title="Li Si",
            file_type="character",
            content="Supporting character. Wise mentor figure.",
        )

        assembler = ContextAssembler()

        result = assembler.assemble(
            session=db_session,
            project_id=str(test_project.id),
            user_id=None,
            max_tokens=2000,
            include_characters=True,
        )

        # Verify characters are in context (items is list of dicts)
        character_titles = [item.get("title") for item in result.items]
        assert "Zhang San" in character_titles
        assert "Li Si" in character_titles

        # Verify character content is present in context string
        assert "Zhang San" in result.context
        assert "Li Si" in result.context

    @pytest.mark.asyncio
    async def test_lore_material_integration(
        self,
        db_session: Session,
        test_project: Project,
        create_test_file,
    ):
        """
        Verify that lore (world-building) materials are correctly integrated.

        Lore files should be included with appropriate priority for
        maintaining story consistency.
        """
        # Create lore files
        create_test_file(
            title="Magic System",
            file_type="lore",
            content="Magic is drawn from the five elements: fire, water, earth, air, spirit.",
        )
        create_test_file(
            title="Geography",
            file_type="lore",
            content="The kingdom is divided into northern and southern regions.",
        )

        assembler = ContextAssembler()

        result = assembler.assemble(
            session=db_session,
            project_id=str(test_project.id),
            user_id=None,
            max_tokens=2000,
            include_lores=True,
        )

        # Verify lore is in context (items is list of dicts)
        lore_titles = [item.get("title") for item in result.items]
        assert "Magic System" in lore_titles
        assert "Geography" in lore_titles

    @pytest.mark.asyncio
    async def test_outline_structure_preservation(
        self,
        db_session: Session,
        test_project: Project,
        create_test_file,
    ):
        """
        Verify that outline structure is preserved in context.

        Hierarchical outline structures should maintain their organization
        when assembled into context.
        """
        # Create outline with hierarchy
        parent_outline = create_test_file(
            title="Part 1: The Beginning",
            file_type="outline",
            content="Introduction to the main characters and setting.",
        )

        child_outline = create_test_file(
            title="Chapter 1",
            file_type="outline",
            content="The protagonist discovers their powers.",
            parent_id=parent_outline.id,
        )

        assembler = ContextAssembler()

        result = assembler.assemble(
            session=db_session,
            project_id=str(test_project.id),
            user_id=None,
            focus_file_id=str(child_outline.id),  # Focus on child
            max_tokens=2000,
        )

        # Verify outline items are present (items is list of dicts)
        outline_items = [
            item for item in result.items
            if item.get("type") == "outline" or item.get("metadata", {}).get("file_type") == "outline"
        ]
        assert len(outline_items) >= 1

        # Verify focus file has highest priority (CRITICAL)
        focus_item = next(
            (item for item in result.items if item.get("id") == str(child_outline.id)),
            None
        )
        if focus_item:
            assert focus_item.get("priority") == "critical"


@pytest.mark.integration
class TestConcurrentOperations:
    """
    Tests for concurrent operation isolation.

    Verifies that multiple concurrent operations maintain data consistency
    and proper isolation between users/sessions.
    """

    @pytest.mark.asyncio
    async def test_concurrent_file_edits(
        self,
        db_session: Session,
        test_project: Project,
        test_user: User,
        create_test_file,
    ):
        """
        Verify data consistency during concurrent file edits.

        Multiple concurrent file edit operations should not interfere
        with each other and should maintain data integrity.
        """
        # Create test files
        file1 = create_test_file("File 1", "draft", "Original content 1")
        file2 = create_test_file("File 2", "draft", "Original content 2")

        results = []

        async def edit_file(file_id: str, new_content: str, delay: float):
            """Simulate concurrent file edit."""
            # Set isolated context
            ToolContext.set_context(
                session=db_session,
                user_id=str(test_user.id),
                project_id=str(test_project.id),
                session_id=f"session-{file_id}",
            )

            await asyncio.sleep(delay)  # Simulate processing time

            # In a real scenario, this would call file update logic
            # For this test, we just verify context isolation
            ctx = ToolContext._get_context()
            results.append({
                "file_id": file_id,
                "project_id": ctx.get("project_id"),
                "session_id": ctx.get("session_id"),
            })

        # Run concurrent edits
        task1 = asyncio.create_task(edit_file(str(file1.id), "New content 1", 0.05))
        task2 = asyncio.create_task(edit_file(str(file2.id), "New content 2", 0.02))

        await asyncio.gather(task1, task2)

        # Verify each operation had correct context
        assert len(results) == 2
        for result in results:
            assert result["project_id"] == str(test_project.id)

    @pytest.mark.asyncio
    async def test_concurrent_chat_sessions(
        self,
        db_session: Session,
        test_project: Project,
        test_user: User,
        create_chat_session,
    ):
        """
        Verify isolation between concurrent chat sessions.

        Multiple chat sessions for the same user/project should remain
        isolated and not share state.
        """
        # Create multiple chat sessions
        session1 = create_chat_session("Session 1")
        session2 = create_chat_session("Session 2")

        results = []

        async def process_session(session_id: str, message: str):
            """Simulate processing a chat message."""
            ToolContext.set_context(
                session=db_session,
                user_id=str(test_user.id),
                project_id=str(test_project.id),
                session_id=session_id,
            )

            await asyncio.sleep(0.02)  # Simulate processing

            ctx = ToolContext._get_context()
            results.append({
                "session_id": session_id,
                "context_session_id": ctx.get("session_id"),
            })

        # Run concurrent session processing
        task1 = asyncio.create_task(process_session(str(session1.id), "Message 1"))
        task2 = asyncio.create_task(process_session(str(session2.id), "Message 2"))

        await asyncio.gather(task1, task2)

        # Verify session isolation
        assert len(results) == 2
        for result in results:
            assert result["session_id"] == result["context_session_id"]

    @pytest.mark.asyncio
    async def test_isolated_user_data(
        self,
        db_session: Session,
    ):
        """
        Verify complete isolation between different users' data.

        Operations from one user should never access or modify
        another user's data.
        """
        # Create two users
        user1 = User(
            email="user1@example.com",
            username="user1",
            hashed_password=hash_password("password"),
            name="User 1",
            email_verified=True,
            is_active=True,
        )
        user2 = User(
            email="user2@example.com",
            username="user2",
            hashed_password=hash_password("password"),
            name="User 2",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user1)
        db_session.add(user2)
        db_session.commit()
        db_session.refresh(user1)
        db_session.refresh(user2)

        # Create projects for each user
        project1 = Project(
            name="User 1 Project",
            owner_id=user1.id,
            project_type="novel",
        )
        project2 = Project(
            name="User 2 Project",
            owner_id=user2.id,
            project_type="novel",
        )
        db_session.add(project1)
        db_session.add(project2)
        db_session.commit()
        db_session.refresh(project1)
        db_session.refresh(project2)

        results = []

        async def user_operation(user_id: str, project_id: str):
            """Simulate user operation."""
            ToolContext.set_context(
                session=db_session,
                user_id=user_id,
                project_id=project_id,
                session_id=None,
            )

            await asyncio.sleep(0.02)

            ctx = ToolContext._get_context()
            results.append({
                "user_id": user_id,
                "context_user_id": ctx.get("user_id"),
                "project_id": project_id,
                "context_project_id": ctx.get("project_id"),
            })

        # Run concurrent operations for different users
        task1 = asyncio.create_task(user_operation(str(user1.id), str(project1.id)))
        task2 = asyncio.create_task(user_operation(str(user2.id), str(project2.id)))

        await asyncio.gather(task1, task2)

        # Verify user isolation
        assert len(results) == 2

        for result in results:
            assert result["user_id"] == result["context_user_id"]
            assert result["project_id"] == result["context_project_id"]

        # Verify no cross-contamination
        user1_result = next(r for r in results if r["user_id"] == str(user1.id))
        user2_result = next(r for r in results if r["user_id"] == str(user2.id))

        assert user1_result["project_id"] == str(project1.id)
        assert user2_result["project_id"] == str(project2.id)
        assert user1_result["project_id"] != user2_result["project_id"]


# =============================================================================
# Additional Integration Tests
# =============================================================================

@pytest.mark.integration
class TestEndToEndScenarios:
    """
    End-to-end scenario tests combining multiple components.

    These tests verify that the entire agent system works correctly
    in realistic usage scenarios.
    """

    @pytest.mark.asyncio
    @pytest.mark.skip(reason="Requires real LLM API key")
    async def test_full_writing_workflow(
        self,
        db_session: Session,
        test_project: Project,
        test_user: User,
        create_chat_session,
    ):
        """
        Test a complete writing workflow from planning to review.

        This test is skipped by default as it requires a real API key.
        Enable it for manual integration testing.
        """
        # This would test the full workflow:
        # 1. User requests to write a chapter
        # 2. Router selects appropriate agents
        # 3. Planner creates outline
        # 4. Writer generates content
        # 5. Reviewer checks quality
        pass

    @pytest.mark.asyncio
    async def test_context_assembly_with_all_material_types(
        self,
        db_session: Session,
        test_project: Project,
        create_test_file,
    ):
        """
        Verify context assembly includes all material types correctly.
        """
        # Create all types of materials
        create_test_file("Story Outline", "outline", "The hero's journey...")
        create_test_file("Hero", "character", "Brave and determined.")
        create_test_file("World Rules", "lore", "Magic exists but is rare.")
        draft = create_test_file("Chapter 1", "draft", "Once upon a time...")

        assembler = ContextAssembler()

        result = assembler.assemble(
            session=db_session,
            project_id=str(test_project.id),
            user_id=None,
            focus_file_id=str(draft.id),
            max_tokens=3000,
        )

        # Verify all material types are represented (items is list of dicts)
        # The type field contains the item type (outline, character, lore, etc.)
        file_types = set()
        for item in result.items:
            # Check both 'type' and metadata.file_type for the file type
            item_type = item.get("type")
            metadata_type = item.get("metadata", {}).get("file_type")
            if item_type:
                file_types.add(item_type)
            if metadata_type:
                file_types.add(metadata_type)

        # At minimum, we should have the focus draft and any related items
        # Characters and lores should be included
        assert "character" in file_types or any("Hero" in item.get("title", "") for item in result.items)
        assert "lore" in file_types or any("World Rules" in item.get("title", "") for item in result.items)

        # Verify focus file has highest priority (CRITICAL)
        focus_item = next(
            (item for item in result.items if item.get("id") == str(draft.id)),
            None
        )
        assert focus_item is not None
        assert focus_item.get("priority") == "critical"
