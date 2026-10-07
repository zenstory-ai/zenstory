"""
Suggestion service for generating intelligent next-step suggestions.

Uses the same context assembly as the main agent to provide
context-aware suggestions based on project state and conversation history.
"""

import asyncio
import json
import logging
import os
from typing import Any

from sqlalchemy import desc
from sqlmodel import Session, and_, select

from database import create_session
from models.llm_usage import LLM_USAGE_SOURCE_SUGGEST
from services.usage.llm_usage_service import LLMUsageAttribution
from utils.logger import get_logger, log_with_context

from .context import get_context_assembler
from .core.llm_client import get_llm_client
from .prompts import get_suggestion_prompt

logger = get_logger(__name__)

# =============================================================================
# Configuration Constants
# =============================================================================

DEFAULT_SUGGESTION_COUNT = 3
CONTEXT_MAX_TOKENS = 3000
RESPONSE_MAX_TOKENS = 150
TEMPERATURE = 0.8
CHAT_HISTORY_LIMIT = 5
MESSAGE_TRUNCATE_LENGTH = 200
MIN_SUGGESTION_LENGTH = 3


def _get_positive_float_env(name: str, default: float) -> float:
    """Read a positive float env var with safe fallback."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


SUGGEST_LLM_TIMEOUT_S = _get_positive_float_env("SUGGEST_LLM_TIMEOUT_S", 25.0)

# Fallback suggestions for different languages
FALLBACK_SUGGESTIONS_ZH = [
    "写下一章的情节发展",
    "完善角色的人物动机",
    "设计一个情节转折点",
    "描写一个关键场景的细节",
    "增加角色之间的互动",
    "补充世界观设定的细节",
    "构思下一章的开场方式",
    "回顾并修改前文的伏笔",
]

FALLBACK_SUGGESTIONS_EN = [
    "Develop the next chapter's plot",
    "Refine character motivations",
    "Design a plot twist",
    "Describe a key scene in detail",
    "Add character interactions",
    "Supplement world-building details",
    "Plan the next chapter's opening",
    "Review and fix foreshadowing",
]


# =============================================================================
# SuggestService Class
# =============================================================================

class SuggestService:
    """
    Generate intelligent next-step suggestions for the user.

    Features:
    - Reuses ContextAssembler for full project context
    - Considers recent conversation history
    - Uses JSON format with repair for robustness
    """

    def __init__(self):
        self.context_assembler = get_context_assembler()
        try:
            self.llm = get_llm_client()
        except Exception as exc:
            # In local/dev/e2e environments we may not have an API key configured.
            # Suggestions should degrade gracefully instead of crashing the endpoint.
            log_with_context(
                logger,
                logging.WARNING,
                "LLM client unavailable for suggestions; falling back to canned suggestions",
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            self.llm = None

    async def generate_suggestions(
        self,
        session: Session,
        project_id: str,
        user_id: str | None = None,
        recent_messages: list[dict[str, str]] | None = None,
        count: int = DEFAULT_SUGGESTION_COUNT,
        language: str | None = None,
        project_type: str = "novel",
    ) -> list[str]:
        """
        Generate multiple suggestions based on project context and conversation history.

        Args:
            session: Database session
            project_id: Project ID
            user_id: User ID for ownership verification (None to skip verification)
            recent_messages: Optional list of recent messages from frontend
            count: Number of suggestions to generate
            language: Language preference (zh, en, etc.)
            project_type: Type of project (novel, short, screenplay)

        Returns:
            List of suggestion strings
        """
        if self.llm is None:
            return self._get_fallback_suggestions(count, language)

        try:
            # Step 1: Assemble project context
            if self._should_offload_session_work(session):
                context_data = await asyncio.to_thread(
                    self._load_context_data,
                    project_id,
                    user_id,
                )
            else:
                context_data = self.context_assembler.assemble(
                    session=session,
                    project_id=project_id,
                    user_id=user_id,
                    query=None,
                    max_tokens=CONTEXT_MAX_TOKENS,
                    include_characters=True,
                    include_lores=True,
                )
            log_with_context(
                logger,
                logging.DEBUG,
                "Context assembled for suggestions",
                context_length=len(context_data.context),
                max_tokens=CONTEXT_MAX_TOKENS,
            )

            # Step 2: Get recent chat history
            if not recent_messages:
                if self._should_offload_session_work(session):
                    recent_messages = await asyncio.to_thread(
                        self._load_recent_chat_history,
                        project_id,
                        user_id,
                    )
                else:
                    recent_messages = self._get_recent_chat_history(
                        session,
                        project_id,
                        user_id,
                    )
            log_with_context(
                logger,
                logging.DEBUG,
                "Recent chat history retrieved",
                message_count=len(recent_messages) if recent_messages else 0,
            )

            # Step 3: Get prompt configuration for project type
            prompt_config = get_suggestion_prompt(project_type)

            # Step 4: Build complete prompt
            full_prompt = self._build_prompt(
                system_prompt=prompt_config["system_prompt"],
                context=context_data.context,
                recent_messages=recent_messages,
                _count=count,
                language=language,
            )
            log_with_context(
                logger,
                logging.DEBUG,
                "Built suggestion prompt",
                prompt_length=len(full_prompt),
                project_type=project_type,
                count=count,
                language=language,
            )

            # Step 5: Call LLM
            result = await asyncio.wait_for(
                self.llm.acomplete(
                    messages=[{"role": "user", "content": full_prompt}],
                    max_tokens=RESPONSE_MAX_TOKENS,
                    temperature=TEMPERATURE,
                    thinking_enabled=False,
                    usage_attribution=LLMUsageAttribution(
                        user_id=user_id,
                        source=LLM_USAGE_SOURCE_SUGGEST,
                        project_id=project_id,
                    ),
                ),
                timeout=SUGGEST_LLM_TIMEOUT_S,
            )
            log_with_context(
                logger,
                logging.INFO,
                "Received LLM response for suggestions",
                response=result[:200] if result else "",  # Limit log length
                response_length=len(result) if result else 0,
            )

            # Step 6: Parse and validate suggestions
            suggestions = self._parse_json_suggestions(result)

            if not suggestions:
                log_with_context(
                    logger,
                    logging.WARNING,
                    "Failed to parse suggestions from LLM response",
                    raw_response=result[:200],
                    falling_back=True,
                )
                return self._get_fallback_suggestions(count, language)

            # Step 7: Ensure we return exactly count suggestions
            if len(suggestions) < count:
                fallbacks = self._get_fallback_suggestions(count - len(suggestions), language)
                suggestions.extend(fallbacks)

            return suggestions[:count]

        except Exception as e:
            log_with_context(
                logger,
                logging.ERROR,
                "Error generating suggestions",
                error_type=type(e).__name__,
                error_message=str(e),
                falling_back=True,
            )
            return self._get_fallback_suggestions(count, language)

    # -------------------------------------------------------------------------
    # Private Helper Methods
    # -------------------------------------------------------------------------

    def _should_offload_session_work(self, session: Session) -> bool:
        """Only offload when running against PostgreSQL production-style sessions."""
        bind = session.get_bind() if hasattr(session, "get_bind") else None
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        return dialect_name == "postgresql"

    def _load_context_data(
        self,
        project_id: str,
        user_id: str | None,
    ):
        """Load context with a fresh sync DB session in a worker thread."""
        with create_session() as read_session:
            return self.context_assembler.assemble(
                session=read_session,
                project_id=project_id,
                user_id=user_id,
                query=None,
                max_tokens=CONTEXT_MAX_TOKENS,
                include_characters=True,
                include_lores=True,
            )

    def _load_recent_chat_history(
        self,
        project_id: str,
        user_id: str | None,
    ) -> list[dict[str, str]]:
        """Load recent chat history with a fresh sync DB session in a worker thread."""
        with create_session() as read_session:
            return self._get_recent_chat_history(read_session, project_id, user_id)

    def _get_fallback_suggestions(
        self,
        _count: int,
        language: str | None = None,
    ) -> list[str]:
        """Get fallback suggestions when LLM fails."""
        is_english = self._is_english(language)
        fallbacks = FALLBACK_SUGGESTIONS_EN if is_english else FALLBACK_SUGGESTIONS_ZH
        return fallbacks[:_count]

    def _get_recent_chat_history(
        self,
        session: Session,
        project_id: str,
        user_id: str | None = None,
    ) -> list[dict[str, str]]:
        """
        Get recent chat history from the active session.

        Args:
            session: Database session
            project_id: Project ID
            user_id: User ID for ownership verification (None to skip verification)

        Returns:
            List of message dicts with role and content
        """
        from models import ChatMessage, ChatSession, Project

        # Validate project
        project = session.get(Project, project_id)
        if not project or project.is_deleted:
            return []

        # Validate project ownership (follows SessionLoader pattern)
        if user_id and project.owner_id != user_id:
            log_with_context(
                logger,
                30,  # WARNING
                "Chat history access denied: user does not own project",
                project_id=project_id,
                user_id=user_id,
                owner_id=project.owner_id,
            )
            return []

        # Find active session deterministically (newest first).
        session_stmt = (
            select(ChatSession)
            .where(
                and_(
                    ChatSession.project_id == project_id,
                    ChatSession.is_active,
                )
            )
            .order_by(
                desc(ChatSession.updated_at),
                desc(ChatSession.created_at),
                desc(ChatSession.id),
            )
        )
        if user_id:
            session_stmt = session_stmt.where(ChatSession.user_id == user_id)

        active_sessions = session.exec(session_stmt).all()
        chat_session = active_sessions[0] if active_sessions else None

        if not chat_session:
            return []

        # Repair stale multi-active states defensively.
        if len(active_sessions) > 1:
            stale_count = 0
            for stale in active_sessions[1:]:
                if stale.is_active:
                    stale.is_active = False
                    session.add(stale)
                    stale_count += 1
            if stale_count > 0:
                session.commit()
                log_with_context(
                    logger,
                    logging.WARNING,
                    "Suggest service detected multiple active chat sessions; stale sessions deactivated",
                    project_id=project_id,
                    user_id=user_id,
                    kept_session_id=chat_session.id,
                    stale_count=stale_count,
                )

        # Get recent messages
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == chat_session.id)
            .order_by(ChatMessage.created_at.desc(), desc(ChatMessage.id))  # type: ignore[attr-defined]
            .limit(CHAT_HISTORY_LIMIT)
        )
        messages = list(reversed(session.exec(stmt).all()))

        return [
            {"role": msg.role, "content": msg.content}
            for msg in messages
            if msg.role in ("user", "assistant")
        ]

    @staticmethod
    def _coerce_suggestions(data: Any) -> list:
        """Extract the suggestion list from either an object or a bare array."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            got = data.get("suggestions", [])
            return got if isinstance(got, list) else []
        return []

    @staticmethod
    def _extract_json_blob(response: str) -> str | None:
        """Locate the first JSON object or array blob in a (possibly noisy) response.

        Prefers an object when present (``{"suggestions": [...]}``); otherwise
        falls back to a bare top-level array (``[...]``).
        """
        obj_start = response.find("{")
        obj_end = response.rfind("}")
        if obj_start >= 0 and obj_end > obj_start:
            return response[obj_start : obj_end + 1]

        arr_start = response.find("[")
        arr_end = response.rfind("]")
        if arr_start >= 0 and arr_end > arr_start:
            return response[arr_start : arr_end + 1]

        return None

    def _parse_json_suggestions(self, response: str) -> list[str]:
        """
        Parse suggestions from LLM JSON response with repair.

        Args:
            response: Raw LLM response text

        Returns:
            List of parsed suggestions (empty if parsing fails)
        """
        if not response or not response.strip():
            return []

        # Try standard JSON parsing first
        try:
            # Extract JSON from response (may be surrounded by text). Accept both
            # an object ({"suggestions": [...]}) and a bare top-level array
            # ([...]) — the prompt's example invites the array shape, which was
            # previously discarded (list has no .get -> canned fallback).
            json_str = self._extract_json_blob(response)
            if json_str:
                data = json.loads(json_str)
                suggestions = self._coerce_suggestions(data)
                valid = self._validate_suggestions(suggestions)
                if valid:
                    log_with_context(
                        logger,
                        logging.INFO,
                        "Successfully parsed suggestions from JSON",
                        count=len(valid),
                    )
                    return valid
        except json.JSONDecodeError as e:
            log_with_context(
                logger,
                logging.WARNING,
                "JSON decode error, attempting repair",
                error=str(e),
            )

        # If standard parsing failed, try JSON repair
        try:
            from json_repair import repair_json

            repaired = repair_json(response)
            data = json.loads(repaired)
            suggestions = self._coerce_suggestions(data)
            valid = self._validate_suggestions(suggestions)
            if valid:
                log_with_context(
                    logger,
                    logging.INFO,
                    "Successfully parsed suggestions after JSON repair",
                    count=len(valid),
                )
                return valid
        except Exception as e:
            log_with_context(
                logger,
                logging.WARNING,
                "JSON repair also failed",
                error=str(e),
            )

        return []

    def _validate_suggestions(self, suggestions: Any) -> list[str]:
        """
        Validate and filter suggestions.

        Args:
            suggestions: Parsed suggestions from JSON

        Returns:
            List of valid suggestion strings
        """
        if not isinstance(suggestions, list):
            return []

        return [
            s
            for s in suggestions
            if isinstance(s, str) and len(s.strip()) >= MIN_SUGGESTION_LENGTH
        ]

    def _build_prompt(
        self,
        system_prompt: str,
        context: str,
        recent_messages: list[dict[str, str]],
        _count: int,
        language: str | None = None,
    ) -> str:
        """
        Build the complete prompt for suggestions generation.

        Args:
            system_prompt: System prompt from prompt config
            context: Assembled project context
            recent_messages: List of recent messages
            count: Number of suggestions to generate
            language: Language preference

        Returns:
            Complete prompt string
        """
        is_english = self._is_english(language)

        # Format conversation history
        chat_str = self._format_chat_history(recent_messages, is_english)

        # Build prompt sections
        context_label = "项目信息:" if not is_english else "Project info:"
        conversation_label = "最近对话:" if not is_english else "Recent conversation:"
        no_context_text = "(暂无项目信息)" if not is_english else "(No project context)"

        prompt_parts = [
            system_prompt,
            "",
            context_label,
            context or no_context_text,
            "",
            conversation_label,
            chat_str,
        ]

        return "\n".join(prompt_parts)

    def _is_english(self, language: str | None) -> bool:
        """
        Determine if the language is English.

        Args:
            language: Language string (e.g., "en", "zh", "en-US")

        Returns:
            True if language is English, False otherwise
        """
        if not language:
            return False

        # Normalize language string: handle "en-US", "zh-CN" formats
        normalized = language.split(",")[0].split("-")[0].strip().lower()
        return normalized.startswith("en")

    def _format_chat_history(
        self,
        messages: list[dict[str, str]],
        is_english: bool,
    ) -> str:
        """
        Format chat history for prompt.

        Args:
            messages: List of message dicts with role and content
            is_english: Whether to use English labels

        Returns:
            Formatted chat history string
        """
        if not messages:
            return "(No conversation yet)" if is_english else "(暂无对话)"

        lines = []
        for msg in messages[-CHAT_HISTORY_LIMIT:]:
            role = self._get_role_label(msg["role"], is_english)
            content = msg["content"][:MESSAGE_TRUNCATE_LENGTH]
            lines.append(f"{role}: {content}")

        return "\n".join(lines)

    def _get_role_label(self, role: str, is_english: bool) -> str:
        """
        Get localized role label.

        Args:
            role: Role name ("user" or "assistant")
            is_english: Whether to use English labels

        Returns:
            Localized role label
        """
        if is_english:
            return "User" if role == "user" else "Assistant"
        else:
            return "用户" if role == "user" else "助手"


# =============================================================================
# Singleton
# =============================================================================

_service: SuggestService | None = None


def get_suggest_service() -> SuggestService:
    """Get or create singleton suggest service."""
    global _service
    if _service is None:
        _service = SuggestService()
    return _service
