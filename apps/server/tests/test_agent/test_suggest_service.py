"""
Suggestion service tests.

Unit tests for the SuggestService business logic with mocked dependencies.
Tests AI-powered content suggestion generation without making real API calls.
"""

import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, select

from agent.schemas.context import ContextData
from models import ChatMessage, ChatSession, File, Project, User
from services.core.auth_service import hash_password


@pytest.fixture
def test_user_with_project(db_session: Session):
    """Create a test user with project for suggestion service testing."""
    # Create user
    user = User(
        email="suggest_test@example.com",
        username="suggesttest",
        hashed_password=hash_password("password123"),
        name="Suggestion Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Create project
    project = Project(
        name="Suggestion Test Project",
        description="A test project for suggestions",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create sample files
    outline = File(
        title="大纲",
        content="# 第一章\n主角出场\n\n# 第二章\n冒险开始",
        file_type="outline",
        project_id=project.id,
        user_id=user.id,
    )
    character = File(
        title="主角",
        content="姓名：张三\n性格：勇敢、善良",
        file_type="character",
        project_id=project.id,
        user_id=user.id,
    )
    draft = File(
        title="第一章",
        content="在一个阳光明媚的早晨，张三开始了他的冒险...",
        file_type="draft",
        project_id=project.id,
        user_id=user.id,
    )
    db_session.add(outline)
    db_session.add(character)
    db_session.add(draft)
    db_session.commit()

    return {
        "user": user,
        "project": project,
        "outline": outline,
        "character": character,
        "draft": draft,
    }


@pytest.fixture
def mock_context_assembler():
    """Mock ContextAssembler for testing."""
    mock_assembler = MagicMock()

    # Default: return empty context
    mock_assembler.assemble.return_value = ContextData(
        items=[],
        context="",
        token_estimate=0,
    )

    return mock_assembler


@pytest.fixture
def mock_llm_client():
    """Mock LLMClient for testing."""
    mock_client = MagicMock()
    mock_client.acomplete = AsyncMock()
    return mock_client


@pytest.fixture
def suggest_service(mock_context_assembler, mock_llm_client):
    """Create SuggestService instance with mocked dependencies."""
    from agent.suggest_service import SuggestService

    # Mock get_llm_client before creating SuggestService
    with patch('agent.suggest_service.get_llm_client', return_value=mock_llm_client):
        service = SuggestService()
        service.context_assembler = mock_context_assembler

    return service


# =============================================================================
# Generate Suggestions Tests
# =============================================================================


@pytest.mark.unit
class TestGenerateSuggestions:
    """Tests for generate_suggestions method."""

    @pytest.mark.asyncio
    async def test_generate_suggestions_valid_context(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test generating suggestions with valid project context."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        # Mock context assembly
        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="项目：测试小说\n大纲：第一章 主角出场",
            token_estimate=50,
        )

        # Mock LLM response
        mock_response = json.dumps({
            "suggestions": [
                "完善主角的背景故事",
                "设计一个情节转折点",
                "添加更多角色互动",
            ]
        })
        suggest_service.llm.acomplete.return_value = mock_response

        # Generate suggestions
        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            count=3,
            language="zh",
        )

        # Verify
        assert len(suggestions) == 3
        assert "完善主角的背景故事" in suggestions
        assert "设计一个情节转折点" in suggestions

        # Verify context assembler was called
        suggest_service.context_assembler.assemble.assert_called_once()

        # Verify LLM was called
        suggest_service.llm.acomplete.assert_called_once()
        call_args = suggest_service.llm.acomplete.call_args
        assert call_args[1]["max_tokens"] == 150
        assert call_args[1]["temperature"] == 0.8
        # Billed to the requesting user and project in the usage ledger.
        from services.usage.llm_usage_service import LLMUsageAttribution

        assert call_args[1]["usage_attribution"] == LLMUsageAttribution(
            user_id=str(user.id), source="suggest", project_id=str(project.id)
        )

    @pytest.mark.asyncio
    async def test_generate_suggestions_empty_context(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test generating suggestions with empty project context."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        # Mock empty context
        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="",
            token_estimate=0,
        )

        # Mock LLM response
        mock_response = json.dumps({
            "suggestions": [
                "创建第一个大纲",
                "添加主要角色",
                "构思故事背景",
            ]
        })
        suggest_service.llm.acomplete.return_value = mock_response

        # Generate suggestions
        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            count=3,
        )

        # Verify
        assert len(suggestions) == 3
        assert "创建第一个大纲" in suggestions

    @pytest.mark.asyncio
    async def test_generate_suggestions_large_context_budget_limit(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test generating suggestions with large context respects token budget."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        # Mock large context (should be limited to CONTEXT_MAX_TOKENS = 3000)
        large_content = "项目内容 " * 10000  # Very large content
        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context=large_content,
            token_estimate=50000,  # Over limit
        )

        # Mock LLM response
        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["suggestion1", "suggestion2"]
        })

        # Generate suggestions
        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
        )

        # Verify context assembler was called with max_tokens limit
        call_args = suggest_service.context_assembler.assemble.call_args
        assert call_args[1]["max_tokens"] == 3000

        # Verify suggestions returned
        assert len(suggestions) >= 2

    @pytest.mark.asyncio
    async def test_generate_suggestions_api_failure_fallback(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test handling API failure gracefully with fallback suggestions."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        # Mock context
        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="项目上下文",
            token_estimate=50,
        )

        # Mock API failure
        suggest_service.llm.acomplete.side_effect = Exception("API Error")

        # Generate suggestions
        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            count=3,
            language="zh",
        )

        # Verify fallback suggestions returned
        assert len(suggestions) == 3
        assert isinstance(suggestions[0], str)
        assert len(suggestions[0]) > 0

    @pytest.mark.asyncio
    async def test_generate_suggestions_timeout_handling(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test timeout handling during suggestion generation."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        # Mock context
        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="项目上下文",
            token_estimate=50,
        )

        # Mock timeout
        suggest_service.llm.acomplete.side_effect = TimeoutError("Request timeout")

        # Generate suggestions
        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            count=3,
        )

        # Verify fallback suggestions returned
        assert len(suggestions) == 3
        assert all(isinstance(s, str) for s in suggestions)


# =============================================================================
# Suggestion Prioritization Tests
# =============================================================================


@pytest.mark.unit
class TestSuggestionPrioritization:
    """Tests for suggestion ranking and filtering."""

    @pytest.mark.asyncio
    async def test_rank_suggestions_by_relevance(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test that suggestions are returned in order received from LLM."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        # Mock LLM returns suggestions in specific order
        suggestions_ordered = [
            "最相关的建议",
            "中等相关的建议",
            "不太相关的建议",
        ]
        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": suggestions_ordered
        })

        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            count=3,
        )

        # Verify order preserved
        assert suggestions == suggestions_ordered

    @pytest.mark.asyncio
    async def test_filter_low_quality_suggestions(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test filtering of low-quality suggestions (too short)."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        # Mock LLM returns mixed quality suggestions
        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": [
                "这是一个好建议",  # Valid
                "短",  # Too short (< 3 chars)
                "ab",  # Too short
                "另一个好建议",  # Valid
            ]
        })

        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            count=3,
        )

        # Valid suggestions survive without inventing extra actions
        assert len(suggestions) == 2
        # Valid suggestions should be present
        assert "这是一个好建议" in suggestions or "另一个好建议" in suggestions

    @pytest.mark.asyncio
    async def test_handle_duplicate_suggestions(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test handling of duplicate suggestions from LLM."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        # Mock LLM returns duplicates
        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": [
                "写下一章",
                "写下一章",  # Duplicate
                "添加角色",
                "添加角色",  # Duplicate
            ]
        })

        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            count=3,
        )

        # Duplicates should be present (service doesn't dedupe)
        # but count should be respected
        assert len(suggestions) <= 4


# =============================================================================
# Context Assembly Tests
# =============================================================================


@pytest.mark.unit
class TestContextAssembly:
    """Tests for context assembly in suggestion generation."""

    @pytest.mark.asyncio
    async def test_include_current_file_content(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test that current file content is included in context."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]
        draft = test_user_with_project["draft"]

        # Mock context with file content
        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context=f"草稿：{draft.content}",
            token_estimate=100,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["继续写下一章"]
        })

        await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
        )

        # Verify assembler called with include flags
        call_args = suggest_service.context_assembler.assemble.call_args
        assert call_args[1]["include_characters"] is True
        assert call_args[1]["include_lores"] is True

    @pytest.mark.asyncio
    async def test_include_related_files(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test that related files (characters, lores) are included."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="大纲：第一章\n角色：张三\n设定：世界观",
            token_estimate=100,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["完善角色设定"]
        })

        await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
        )

        # Verify assembler called with include flags
        call_args = suggest_service.context_assembler.assemble.call_args
        assert call_args[1]["include_characters"] is True
        assert call_args[1]["include_lores"] is True

    @pytest.mark.asyncio
    async def test_respect_token_budget(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test that context respects token budget (3000 tokens)."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=100,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["建议1"]
        })

        await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
        )

        # Verify max_tokens parameter
        call_args = suggest_service.context_assembler.assemble.call_args
        assert call_args[1]["max_tokens"] == 3000

    @pytest.mark.asyncio
    async def test_handle_missing_files(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test handling when project has no files."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        # Delete all files
        for key in ["outline", "character", "draft"]:
            if key in test_user_with_project:
                db_session.delete(test_user_with_project[key])
        db_session.commit()

        # Mock empty context (no files)
        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="",
            token_estimate=0,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["创建第一个文件"]
        })

        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
        )

        # Should still return suggestions
        assert len(suggestions) > 0


# =============================================================================
# Chat History Tests
# =============================================================================


@pytest.mark.unit
class TestChatHistory:
    """Tests for chat history integration."""

    @pytest.mark.asyncio
    async def test_get_recent_chat_history(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test retrieving recent chat history for context."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        # Create chat session with messages
        chat_session = ChatSession(
            user_id=str(user.id),
            project_id=str(project.id),
            title="Test Chat",
            is_active=True,
            message_count=3,
        )
        db_session.add(chat_session)
        db_session.commit()
        db_session.refresh(chat_session)

        # Add messages
        messages = [
            ChatMessage(session_id=chat_session.id, role="user", content="Help me write chapter 1"),
            ChatMessage(session_id=chat_session.id, role="assistant", content="Sure, let's start with the outline"),
            ChatMessage(session_id=chat_session.id, role="user", content="Add more details to the character"),
        ]
        for msg in messages:
            db_session.add(msg)
        db_session.commit()

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["继续角色开发"]
        })

        # Generate with chat history
        await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
        )

        # Verify LLM was called (chat history should be in prompt)
        assert suggest_service.llm.acomplete.called

    @pytest.mark.asyncio
    async def test_use_frontend_recent_messages(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test using recent messages passed from frontend."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["基于对话的建议"]
        })

        # Pass recent messages from frontend
        recent_messages = [
            {"role": "user", "content": "Help me with the plot"},
            {"role": "assistant", "content": "What kind of plot twist?"},
        ]

        await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            recent_messages=recent_messages,
        )

        # Should use frontend messages instead of querying DB
        assert suggest_service.llm.acomplete.called

    @pytest.mark.asyncio
    async def test_get_recent_chat_history_selects_latest_without_mutating_legacy_sessions(
        self, suggest_service, test_user_with_project, db_session: Session,
        legacy_db_without_active_chat_index,
    ):
        """A legacy database remains read-only when suggestions load its latest history."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        stale_session = ChatSession(
            user_id=str(user.id),
            project_id=str(project.id),
            title="Stale active",
            is_active=True,
            message_count=1,
            created_at=datetime.utcnow() - timedelta(hours=1),
            updated_at=datetime.utcnow() - timedelta(hours=1),
        )
        latest_session = ChatSession(
            user_id=str(user.id),
            project_id=str(project.id),
            title="Latest active",
            is_active=True,
            message_count=1,
            created_at=datetime.utcnow() - timedelta(minutes=5),
            updated_at=datetime.utcnow(),
        )
        db_session.add(stale_session)
        db_session.add(latest_session)
        db_session.commit()
        db_session.refresh(stale_session)
        db_session.refresh(latest_session)

        db_session.add(ChatMessage(session_id=stale_session.id, role="user", content="stale-history"))
        db_session.add(ChatMessage(session_id=latest_session.id, role="user", content="latest-history"))
        db_session.commit()

        history = suggest_service._get_recent_chat_history(
            db_session,
            str(project.id),
            str(user.id),
        )

        assert [msg["content"] for msg in history] == ["latest-history"]

        sessions = db_session.exec(
            select(ChatSession).where(
                ChatSession.project_id == str(project.id),
                ChatSession.user_id == str(user.id),
            )
        ).all()
        active_ids = [row.id for row in sessions if row.is_active]
        assert set(active_ids) == {stale_session.id, latest_session.id}


# =============================================================================
# JSON Parsing Tests
# =============================================================================


@pytest.mark.unit
class TestJSONParsing:
    """Tests for JSON parsing and validation."""

    def test_parse_valid_json_suggestions(self, suggest_service):
        """Test parsing valid JSON suggestions."""
        response = '{"suggestions": ["建议1", "建议2", "建议3"]}'

        suggestions = suggest_service._parse_json_suggestions(response)

        assert len(suggestions) == 3
        assert suggestions == ["建议1", "建议2", "建议3"]

    def test_parse_json_with_surrounding_text(self, suggest_service):
        """Test parsing JSON surrounded by other text."""
        response = 'Here are the suggestions:\n{"suggestions": ["建议1", "建议2"]}\nHope this helps!'

        suggestions = suggest_service._parse_json_suggestions(response)

        assert len(suggestions) == 2
        assert suggestions == ["建议1", "建议2"]

    def test_parse_invalid_json_attempts_repair(self, suggest_service):
        """Test that truncated JSON is actually repaired by json_repair."""
        # Missing closing bracket and brace
        response = '{"suggestions": ["建议1", "建议2"'

        suggestions = suggest_service._parse_json_suggestions(response)

        assert suggestions == ["建议1", "建议2"]

    def test_parse_empty_response(self, suggest_service):
        """Test parsing empty response."""
        suggestions = suggest_service._parse_json_suggestions("")

        assert suggestions is None

    def test_parse_missing_suggestions_key(self, suggest_service):
        """Test parsing JSON without suggestions key."""
        response = '{"other_key": "value"}'

        suggestions = suggest_service._parse_json_suggestions(response)

        assert suggestions is None

    def test_validate_suggestions_filters_non_list(self, suggest_service):
        """Test validation filters non-list suggestions."""
        suggestions = suggest_service._validate_suggestions("not a list")

        assert suggestions == []

    def test_validate_suggestions_filters_short_strings(self, suggest_service):
        """Test validation filters strings shorter than MIN_SUGGESTION_LENGTH (3)."""
        suggestions = suggest_service._validate_suggestions([
            "valid suggestion",
            "ab",  # Too short
            "x",   # Too short
            "another valid",
        ])

        assert len(suggestions) == 2
        assert "valid suggestion" in suggestions
        assert "another valid" in suggestions

    def test_validate_suggestions_does_not_guess_semantics_from_words(self, suggest_service):
        """合法故事内容不能因为碰巧出现「用户」或「拍板」而被误删。"""
        suggestions = suggest_service._validate_suggestions([
            "描写用户代表拍板的瞬间",
            "让角色质疑用户至上",
            "刻画董事长拍板后的沉默",
        ])

        assert suggestions == [
            "描写用户代表拍板的瞬间",
            "让角色质疑用户至上",
            "刻画董事长拍板后的沉默",
        ]

    @pytest.mark.asyncio
    async def test_generated_suggestions_are_not_semantically_reclassified_after_generation(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """生成模型负责建议语义，服务层只校验响应结构和基本字符串约束。"""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]
        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["描写用户代表拍板的瞬间", "按方案 A 写全文", "等用户确认后再写"]
        })

        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            count=3,
        )

        assert suggestions == ["描写用户代表拍板的瞬间", "按方案 A 写全文", "等用户确认后再写"]
        prompt = suggest_service.llm.acomplete.call_args.kwargs["messages"][0]["content"]
        assert "点击后应能直接作为作者发给 AI 的下一步请求" in prompt
        assert "不要写成 AI 对作者的追问" in prompt


# =============================================================================
# Fallback Suggestions Tests
# =============================================================================


@pytest.mark.unit
class TestFallbackSuggestions:
    """Tests for fallback suggestion behavior."""

    def test_get_fallback_suggestions_chinese(self, suggest_service):
        """Test getting Chinese fallback suggestions."""
        suggestions = suggest_service._get_fallback_suggestions(3, language="zh")

        assert len(suggestions) == 3
        assert all(isinstance(s, str) for s in suggestions)
        # Should contain Chinese fallback suggestions
        assert any("写" in s or "角色" in s for s in suggestions)

    def test_get_fallback_suggestions_english(self, suggest_service):
        """Test getting English fallback suggestions."""
        suggestions = suggest_service._get_fallback_suggestions(3, language="en")

        assert len(suggestions) == 3
        assert all(isinstance(s, str) for s in suggestions)
        # Should contain English fallback suggestions
        assert any("chapter" in s.lower() or "character" in s.lower() for s in suggestions)

    def test_get_fallback_suggestions_count(self, suggest_service):
        """Test fallback respects count parameter."""
        for count in [1, 3, 5]:
            suggestions = suggest_service._get_fallback_suggestions(count, language="zh")
            assert len(suggestions) == count

    @pytest.mark.asyncio
    async def test_fallback_on_parse_failure(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test that fallback suggestions are returned when parsing fails."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        # Mock LLM returns unparseable response
        suggest_service.llm.acomplete.return_value = "This is not valid JSON at all"

        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            count=3,
            language="zh",
        )

        # Should return fallback suggestions
        assert len(suggestions) == 3
        assert all(isinstance(s, str) for s in suggestions)


# =============================================================================
# Language Detection Tests
# =============================================================================


@pytest.mark.unit
class TestLanguageDetection:
    """Tests for language detection and localization."""

    def test_is_english_true(self, suggest_service):
        """Test detecting English language."""
        assert suggest_service._is_english("en") is True
        assert suggest_service._is_english("en-US") is True
        assert suggest_service._is_english("en-GB") is True

    def test_is_english_false(self, suggest_service):
        """Test detecting non-English language."""
        assert suggest_service._is_english("zh") is False
        assert suggest_service._is_english("zh-CN") is False
        assert suggest_service._is_english("ja") is False

    def test_is_english_none(self, suggest_service):
        """Test handling None language."""
        assert suggest_service._is_english(None) is False

    @pytest.mark.asyncio
    async def test_language_affects_prompt_language(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test that language parameter affects prompt language."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["English suggestion"]
        })

        # Generate with English language
        await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            language="en",
        )

        # Verify LLM was called
        assert suggest_service.llm.acomplete.called

        # Verify prompt uses static English system message and dynamic English user message
        from agent.suggest_service import ENGLISH_OUTPUT_RULE

        call_args = suggest_service.llm.acomplete.call_args
        messages = call_args[1]["messages"]
        assert [m["role"] for m in messages] == ["system", "user"]
        assert ENGLISH_OUTPUT_RULE in messages[0]["content"]
        assert ENGLISH_OUTPUT_RULE not in messages[1]["content"]
        assert "Project info:" in messages[1]["content"]
        assert "Recent conversation:" in messages[1]["content"]


# =============================================================================
# Project Type Tests
# =============================================================================


@pytest.mark.unit
class TestProjectType:
    """Tests for different project types."""

    @pytest.mark.asyncio
    async def test_project_type_novel(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test suggestion generation for novel project type."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["小说建议"]
        })

        await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            project_type="novel",
        )

        # Verify LLM was called
        assert suggest_service.llm.acomplete.called

    @pytest.mark.asyncio
    async def test_project_type_short_story(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test suggestion generation for short story project type."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["短篇建议"]
        })

        await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            project_type="short_story",
        )

        assert suggest_service.llm.acomplete.called

    @pytest.mark.asyncio
    async def test_project_type_screenplay(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test suggestion generation for screenplay project type."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["剧本建议"]
        })

        await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            project_type="screenplay",
        )

        assert suggest_service.llm.acomplete.called


# =============================================================================
# Edge Cases Tests
# =============================================================================


@pytest.mark.unit
class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_generate_suggestions_no_user_id(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test generating suggestions without user_id (skip ownership check)."""
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["建议1", "建议2"]
        })

        # No user_id
        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=None,
        )

        assert len(suggestions) > 0

    @pytest.mark.asyncio
    async def test_generate_suggestions_insufficient_suggestions(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test when LLM returns fewer suggestions than requested."""
        user = test_user_with_project["user"]
        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        # LLM returns only 1 suggestion
        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["唯一的建议"]
        })

        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            count=5,  # Request 5
        )

        # One relevant suggestion is a valid complete result
        assert len(suggestions) == 1
        assert "唯一的建议" in suggestions

    @pytest.mark.asyncio
    async def test_generate_suggestions_wrong_project_owner(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Test accessing chat history of project owned by another user."""
        # Create another user
        other_user = User(
            email="other@example.com",
            username="otheruser",
            hashed_password=hash_password("password123"),
            name="Other User",
            email_verified=True,
            is_active=True,
        )
        db_session.add(other_user)
        db_session.commit()
        db_session.refresh(other_user)

        project = test_user_with_project["project"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="上下文",
            token_estimate=50,
        )

        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["建议"]
        })

        # Try with wrong user_id
        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(other_user.id),  # Wrong owner
        )

        # Should still work (ownership check only affects chat history)
        assert len(suggestions) > 0


# =============================================================================
# Singleton Tests
# =============================================================================


@pytest.mark.unit
class TestSingleton:
    """Tests for singleton pattern."""

    def test_get_suggest_service_singleton(self):
        """Test that get_suggest_service returns singleton instance."""
        with patch("agent.suggest_service.get_context_assembler"):
            with patch("agent.suggest_service.get_llm_client"):
                import agent.suggest_service as service_module
                from agent.suggest_service import get_suggest_service

                # Reset singleton
                service_module._service = None

                service1 = get_suggest_service()
                service2 = get_suggest_service()

                assert service1 is service2  # Same instance

                # Clean up
                service_module._service = None


# =============================================================================
# Helper Methods Tests
# =============================================================================


@pytest.mark.unit
class TestHelperMethods:
    """Tests for private helper methods."""

    def test_format_chat_history_empty(self, suggest_service):
        """Test formatting empty chat history."""
        result = suggest_service._format_chat_history([], is_english=False)

        assert result == "(暂无对话)"

    def test_format_chat_history_with_messages(self, suggest_service):
        """Test formatting chat history with messages."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
        ]

        result = suggest_service._format_chat_history(messages, is_english=False)

        assert "用户: Hello" in result
        assert "助手: Hi there!" in result

    def test_format_chat_history_truncation(self, suggest_service):
        """Test that long messages are truncated."""
        long_content = "A" * 500
        messages = [
            {"role": "user", "content": long_content},
        ]

        result = suggest_service._format_chat_history(messages, is_english=True)

        # Should be truncated to MESSAGE_TRUNCATE_LENGTH (200)
        assert len(result) < len(long_content)

    def test_get_role_label_chinese(self, suggest_service):
        """Test getting Chinese role labels."""
        assert suggest_service._get_role_label("user", is_english=False) == "用户"
        assert suggest_service._get_role_label("assistant", is_english=False) == "助手"

    def test_get_role_label_english(self, suggest_service):
        """Test getting English role labels."""
        assert suggest_service._get_role_label("user", is_english=True) == "User"
        assert suggest_service._get_role_label("assistant", is_english=True) == "Assistant"

    def test_build_messages_structure(self, suggest_service):
        """Test message structure separates static system prompt from dynamic user context."""
        messages = suggest_service._build_messages(
            system_prompt="You are a helpful assistant",
            context="项目上下文",
            recent_messages=[{"role": "user", "content": "Hello"}],
            language="zh",
        )

        assert messages == [
            {"role": "system", "content": "You are a helpful assistant"},
            {
                "role": "user",
                "content": "项目信息:\n项目上下文\n\n最近对话:\n用户: Hello",
            },
        ]

    def test_build_messages_no_context(self, suggest_service):
        """Test user message shows placeholder when context is empty."""
        messages = suggest_service._build_messages(
            system_prompt="System",
            context="",
            recent_messages=[],
            language="zh",
        )

        assert messages[0] == {"role": "system", "content": "System"}
        assert "(暂无项目信息)" in messages[1]["content"]
        assert "(暂无对话)" in messages[1]["content"]

    def test_english_interface_asks_for_english_suggestions(self, suggest_service):
        """英文界面的建议提示词把英文规则放在静态 system 消息里；中文界面不变。"""
        from agent.suggest_service import ENGLISH_OUTPUT_RULE

        kwargs = {"system_prompt": "System", "context": "Project A", "recent_messages": [{"role": "user", "content": "Hi"}]}
        en_messages = suggest_service._build_messages(**kwargs, language="en-US")
        zh_messages = suggest_service._build_messages(**kwargs, language="zh")

        assert en_messages[0]["role"] == "system"
        assert en_messages[0]["content"] == f"System\n\n{ENGLISH_OUTPUT_RULE}"
        assert ENGLISH_OUTPUT_RULE not in en_messages[1]["content"]
        assert ENGLISH_OUTPUT_RULE not in zh_messages[0]["content"]
        assert ENGLISH_OUTPUT_RULE not in zh_messages[1]["content"]


# =============================================================================
# Prompt Cache Stability Tests
# =============================================================================


@pytest.mark.unit
class TestSuggestPromptCacheStability:
    """Verify cache-stable `[system, user]` message layout and provider compatibility."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("project_type", "language"),
        [
            ("novel", "zh"),
            ("short", "zh"),
            ("screenplay", "zh"),
            ("novel", "en-US"),
            ("screenplay", "en"),
        ],
    )
    async def test_system_message_is_byte_identical_across_dynamic_requests(
        self, suggest_service, project_type, language
    ):
        """Two calls with different context and history must share a byte-identical system message."""
        from agent.prompts import get_suggestion_prompt
        from agent.suggest_service import ENGLISH_OUTPUT_RULE

        suggest_service.llm.acomplete.return_value = json.dumps({"suggestions": ["建议一", "建议二"]})

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="项目：雾港档案\n主角：温梨",
            token_estimate=20,
        )
        await suggest_service.generate_suggestions(
            session=MagicMock(),
            project_id="proj-a",
            user_id=None,
            recent_messages=[{"role": "user", "content": "帮我写第一章开场"}],
            language=language,
            project_type=project_type,
        )
        first_messages = suggest_service.llm.acomplete.call_args.kwargs["messages"]

        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="项目：星际列车\n设定：曲速跳跃规则",
            token_estimate=25,
        )
        await suggest_service.generate_suggestions(
            session=MagicMock(),
            project_id="proj-b",
            user_id=None,
            recent_messages=[
                {"role": "user", "content": "设计一个反转"},
                {"role": "assistant", "content": "可以让列车长是卧底。"},
            ],
            language=language,
            project_type=project_type,
        )
        second_messages = suggest_service.llm.acomplete.call_args.kwargs["messages"]

        assert [m["role"] for m in first_messages] == ["system", "user"]
        assert [m["role"] for m in second_messages] == ["system", "user"]
        assert first_messages[0] == second_messages[0]
        assert first_messages[0]["content"].encode("utf-8") == second_messages[0]["content"].encode("utf-8")

        base_system = get_suggestion_prompt(project_type)["system_prompt"]
        if suggest_service._is_english(language):
            assert first_messages[0]["content"] == f"{base_system}\n\n{ENGLISH_OUTPUT_RULE}"
            assert ENGLISH_OUTPUT_RULE not in first_messages[1]["content"]
            assert ENGLISH_OUTPUT_RULE not in second_messages[1]["content"]
        else:
            assert first_messages[0]["content"] == base_system
            assert ENGLISH_OUTPUT_RULE not in first_messages[0]["content"]

        # Dynamic project context must stay strictly in user message, before recent conversation.
        assert "雾港档案" not in first_messages[0]["content"]
        assert "雾港档案" in first_messages[1]["content"]
        assert "星际列车" in second_messages[1]["content"]
        conv_marker = "Recent conversation:" if suggest_service._is_english(language) else "最近对话:"
        assert first_messages[1]["content"].index("雾港档案") < first_messages[1]["content"].index(conv_marker)
        assert second_messages[1]["content"].index("星际列车") < second_messages[1]["content"].index(conv_marker)

    @pytest.mark.asyncio
    async def test_unchanged_project_context_stays_prefix_stable_before_recent_messages(
        self, suggest_service
    ):
        """When project context is unchanged across turns, user message preserves its leading prefix."""
        suggest_service.llm.acomplete.return_value = json.dumps({"suggestions": ["写第二章"]})
        suggest_service.context_assembler.assemble.return_value = ContextData(
            items=[],
            context="项目：长安夜行\n角色：沈墨、林霜",
            token_estimate=30,
        )

        await suggest_service.generate_suggestions(
            session=MagicMock(),
            project_id="proj-stable",
            user_id=None,
            recent_messages=[{"role": "user", "content": "第一轮对话"}],
            language="zh",
        )
        turn1_messages = suggest_service.llm.acomplete.call_args.kwargs["messages"]

        await suggest_service.generate_suggestions(
            session=MagicMock(),
            project_id="proj-stable",
            user_id=None,
            recent_messages=[
                {"role": "user", "content": "第一轮对话"},
                {"role": "assistant", "content": "已完成第一章"},
                {"role": "user", "content": "第二轮新问题"},
            ],
            language="zh",
        )
        turn2_messages = suggest_service.llm.acomplete.call_args.kwargs["messages"]

        assert turn1_messages[0] == turn2_messages[0]
        expected_prefix = "项目信息:\n项目：长安夜行\n角色：沈墨、林霜\n\n最近对话:\n"
        assert turn1_messages[1]["content"].startswith(expected_prefix)
        assert turn2_messages[1]["content"].startswith(expected_prefix)

    @pytest.mark.asyncio
    async def test_acomplete_and_failover_handle_system_user_messages_on_deepseek_and_minimax(
        self, monkeypatch
    ):
        """`[system, user]` works through LLMClient.acomplete + llm_failover + cost guard on DeepSeek and MiniMax."""
        from types import SimpleNamespace

        import httpx
        import openai

        from agent.core import llm_failover, llm_providers
        from agent.core.llm_client import LLMClient
        from agent.suggest_service import RESPONSE_MAX_TOKENS, SuggestService
        from services.usage import llm_usage_service
        from services.usage.model_call_guard import install_async_cost_guard

        monkeypatch.delenv("REDIS_URL", raising=False)
        monkeypatch.setenv("LLM_PRIMARY_PROVIDER", "minimax")
        monkeypatch.setenv("MINIMAX_API_KEY", "synthetic-minimax")
        monkeypatch.setenv("LLM_BACKUP_PROVIDER", "deepseek")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "synthetic-deepseek")
        for name in ("LLM_PRIMARY_MODEL", "LLM_BACKUP_MODEL", "MINIMAX_REASONING_EFFORT"):
            monkeypatch.delenv(name, raising=False)

        recorded: dict[str, list[dict]] = {"deepseek": [], "minimax": []}
        scripts: dict[str, list[object]] = {"deepseek": [], "minimax": []}
        metered: list[tuple[str, str, str | None]] = []

        def _fake_client(provider):
            async def create(**kwargs):
                recorded[provider.name].append(kwargs)
                outcome = scripts[provider.name].pop(0)
                if isinstance(outcome, BaseException):
                    raise outcome
                return outcome

            raw_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
            return install_async_cost_guard(raw_client)

        monkeypatch.setattr(llm_providers, "get_async_client", _fake_client)
        monkeypatch.setattr("services.usage.model_call_guard.reserve_model_call", lambda _kw: None)
        monkeypatch.setattr("services.usage.model_call_guard.settle_model_call", lambda *_a: None)
        monkeypatch.setattr(
            llm_usage_service,
            "schedule_llm_usage_record",
            lambda attr, *, model, timing, **_kw: metered.append((attr.source, model, timing.outcome)),
        )
        llm_failover.reset_failover_state()

        try:
            with patch("agent.suggest_service.get_llm_client", return_value=LLMClient()):
                svc = SuggestService()
            svc.context_assembler = MagicMock()
            svc.context_assembler.assemble.return_value = ContextData(
                items=[], context="项目：双供应商测试", token_estimate=12
            )

            ok_resp = SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"suggestions": ["推进下一章", "完善主角动机"]}'),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=80, completion_tokens=20, total_tokens=100),
            )
            scripts["deepseek"] = [ok_resp]
            suggestions = await svc.generate_suggestions(
                session=MagicMock(),
                project_id="proj-1",
                user_id="user-1",
                recent_messages=[{"role": "user", "content": "继续"}],
                language="zh",
            )
            assert suggestions == ["推进下一章", "完善主角动机"]
            assert len(recorded["deepseek"]) == 1
            ds_call = recorded["deepseek"][0]
            assert [m["role"] for m in ds_call["messages"]] == ["system", "user"]
            assert ds_call["extra_body"] == {"thinking": {"type": "disabled"}}
            assert ds_call["max_tokens"] == RESPONSE_MAX_TOKENS

            # DeepSeek 503 fails over cleanly to MiniMax with the exact `[system, user]` payload intact.
            err_503 = openai.InternalServerError(
                "503",
                response=httpx.Response(503, request=httpx.Request("POST", "https://llm.test")),
                body=None,
            )
            scripts["deepseek"] = [err_503]
            scripts["minimax"] = [ok_resp]
            suggestions_mm = await svc.generate_suggestions(
                session=MagicMock(),
                project_id="proj-1",
                user_id="user-1",
                recent_messages=[{"role": "user", "content": "继续"}],
                language="en",
            )
            assert suggestions_mm == ["推进下一章", "完善主角动机"]
            assert len(recorded["minimax"]) == 1
            mm_call = recorded["minimax"][0]
            assert [m["role"] for m in mm_call["messages"]] == ["system", "user"]
            assert "extra_body" not in mm_call
            assert mm_call["reasoning_effort"] == "low"
            assert mm_call["max_tokens"] == RESPONSE_MAX_TOKENS + llm_failover.THINKING_HEADROOM_TOKENS
            assert metered == [
                ("suggest", "deepseek-flash", "success"),
                ("suggest", "deepseek-flash", "error"),
                ("suggest", "MiniMax-M3.1-Flash", "success"),
            ]
        finally:
            llm_failover.reset_failover_state()


# =============================================================================
# Configuration Constants Tests
# =============================================================================


@pytest.mark.unit
class TestConfigurationConstants:
    """Tests for configuration constants."""

    def test_default_suggestion_count(self):
        """Test DEFAULT_SUGGESTION_COUNT constant."""
        from agent.suggest_service import DEFAULT_SUGGESTION_COUNT

        assert DEFAULT_SUGGESTION_COUNT == 3

    def test_context_max_tokens(self):
        """Test CONTEXT_MAX_TOKENS constant."""
        from agent.suggest_service import CONTEXT_MAX_TOKENS

        assert CONTEXT_MAX_TOKENS == 3000

    def test_response_max_tokens(self):
        """Test RESPONSE_MAX_TOKENS constant."""
        from agent.suggest_service import RESPONSE_MAX_TOKENS

        assert RESPONSE_MAX_TOKENS == 150

    def test_temperature(self):
        """Test TEMPERATURE constant."""
        from agent.suggest_service import TEMPERATURE

        assert TEMPERATURE == 0.8

    def test_min_suggestion_length(self):
        """Test MIN_SUGGESTION_LENGTH constant."""
        from agent.suggest_service import MIN_SUGGESTION_LENGTH

        assert MIN_SUGGESTION_LENGTH == 3


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("response, expected", [
    ('{"suggestions": []}', []), ('[]', []),
    ('```json\n{"suggestions": []}\n```', []),
    ('{"suggestions": ["只改第二段"]}', ["只改第二段"]),
])
async def test_valid_zero_to_three_results_are_not_padded(suggest_service, response, expected):
    suggest_service.llm.acomplete.return_value = response
    result = await suggest_service.generate_suggestions(
        session=MagicMock(), project_id="synthetic-project", count=3,
        recent_messages=[{"role": "user", "content": "三项修改做完即结束"}],
    )
    assert result == expected
    assert suggest_service.llm.acomplete.await_count == 1


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("response", ['not json', '{}', '{"suggestions": "bad"}'])
async def test_invalid_result_keeps_failure_fallback(suggest_service, response):
    suggest_service.llm.acomplete.return_value = response
    result = await suggest_service.generate_suggestions(
        session=MagicMock(), project_id="synthetic-project",
        recent_messages=[{"role": "user", "content": "先给三项建议，不要改文件"}],
    )
    assert result == suggest_service._get_fallback_suggestions(3)
