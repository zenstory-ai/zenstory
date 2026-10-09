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

        # Verify short suggestions filtered, fallback added to reach count
        assert len(suggestions) == 3
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
    async def test_get_recent_chat_history_prefers_latest_active_and_deactivates_stale(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """Should use latest active session and deactivate stale active sessions."""
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
        assert active_ids == [latest_session.id]


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
        """Test that invalid JSON attempts repair."""
        # Missing closing brace
        response = '{"suggestions": ["建议1", "建议2"'

        with patch("json_repair.repair_json") as mock_repair:
            mock_repair.return_value = '{"suggestions": ["建议1", "建议2"]}'

            suggestions = suggest_service._parse_json_suggestions(response)

            # Should attempt repair
            mock_repair.assert_called_once()
            assert len(suggestions) == 2

    def test_parse_empty_response(self, suggest_service):
        """Test parsing empty response."""
        suggestions = suggest_service._parse_json_suggestions("")

        assert suggestions == []

    def test_parse_missing_suggestions_key(self, suggest_service):
        """Test parsing JSON without suggestions key."""
        response = '{"other_key": "value"}'

        suggestions = suggest_service._parse_json_suggestions(response)

        assert suggestions == []

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

    def test_validate_suggestions_drops_instructions_aimed_at_the_author(self, suggest_service):
        """建议是作者点一下就发给 AI 的话，指向作者本人的指令要丢掉。"""
        suggestions = suggest_service._validate_suggestions([
            "请用户拍板开篇方案",
            "按方案 A 写全文",
            "请作者确认主角年龄",
            "Ask the user which ending",
            "开头方案请你拍板",
            "把第二章结尾改得更紧",
        ])

        assert suggestions == ["按方案 A 写全文", "把第二章结尾改得更紧"]

    @pytest.mark.asyncio
    async def test_author_directed_suggestions_are_replaced_by_fallbacks(
        self, suggest_service, test_user_with_project, db_session: Session
    ):
        """过滤后不足 3 条时，用现有 fallback 补齐，且不重复。"""
        from agent.suggest_service import FALLBACK_SUGGESTIONS_ZH

        user = test_user_with_project["user"]
        project = test_user_with_project["project"]
        suggest_service.llm.acomplete.return_value = json.dumps({
            "suggestions": ["请用户拍板开篇方案", "按方案 A 写全文", "等用户确认后再写"]
        })

        suggestions = await suggest_service.generate_suggestions(
            session=db_session,
            project_id=str(project.id),
            user_id=str(user.id),
            count=3,
        )

        assert len(suggestions) == 3
        assert suggestions[0] == "按方案 A 写全文"
        assert all(s in FALLBACK_SUGGESTIONS_ZH for s in suggestions[1:])
        assert len(set(suggestions)) == 3
        assert not any("用户" in s or "拍板" in s for s in suggestions)


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

        # Verify prompt includes English labels
        call_args = suggest_service.llm.acomplete.call_args
        # acomplete is called with messages as keyword arg
        messages = call_args[1]["messages"]  # Get keyword arg 'messages'
        prompt = messages[0]["content"]  # First message content

        # Should contain English labels
        assert "Project info:" in prompt or "Recent conversation:" in prompt


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

        # Should pad with fallback suggestions
        assert len(suggestions) == 5
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

    def test_build_prompt_structure(self, suggest_service):
        """Test prompt structure includes all sections."""
        prompt = suggest_service._build_prompt(
            system_prompt="You are a helpful assistant",
            context="项目上下文",
            recent_messages=[{"role": "user", "content": "Hello"}],
            _count=3,
            language="zh",
        )

        assert "You are a helpful assistant" in prompt
        assert "项目信息:" in prompt
        assert "项目上下文" in prompt
        assert "最近对话:" in prompt

    def test_build_prompt_no_context(self, suggest_service):
        """Test prompt with no context shows placeholder."""
        prompt = suggest_service._build_prompt(
            system_prompt="System",
            context="",
            recent_messages=[],
            _count=3,
            language="zh",
        )

        assert "(暂无项目信息)" in prompt


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
