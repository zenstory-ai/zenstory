"""
Suggestion service for generating intelligent next-step suggestions.

Uses the same context assembly as the main agent to provide
context-aware suggestions based on project state and conversation history.
"""

import asyncio
import json
import logging
from typing import Any

from sqlalchemy import desc
from sqlmodel import Session, col, select

from config.env import get_positive_float_env
from core.error_handler import APIException
from database import create_session
from models.llm_usage import LLM_USAGE_SOURCE_SUGGEST
from services.chat_session_service import latest_active_chat_session
from services.usage.llm_usage_service import LLMUsageAttribution
from utils.logger import get_logger, log_with_context

from .constants import should_offload_session_work
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

SUGGEST_LLM_TIMEOUT_S = get_positive_float_env("SUGGEST_LLM_TIMEOUT_S", 25.0)

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


# 建议提示词是中文；英文界面要单独说清输出语言，否则模型照着中文示例写中文建议。
ENGLISH_OUTPUT_RULE = (
    "Output language: the author uses the English interface. Write every suggestion in English, "
    "in the author's own voice (for example \"Write the full story from plan A\"), "
    "at most 10 words each. Keep the JSON format described above."
)


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
            # 异常文本可能带着配置或供应商回显：只记固定原因与类型。
            log_with_context(
                logger,
                logging.WARNING,
                "LLM client unavailable for suggestions; falling back to canned suggestions",
                stage="init",
                failure="llm_unavailable",
                error_type=type(exc).__name__,
                exc_info=False,
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
        count = min(count, DEFAULT_SUGGESTION_COUNT)
        if self.llm is None:
            return self._get_fallback_suggestions(count, language)

        offloaded = should_offload_session_work(session)
        try:
            # Step 1: Assemble project context
            if offloaded:
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
                if offloaded:
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

            # Step 4: Build cache-stable system + dynamic user messages
            messages = self._build_messages(
                system_prompt=prompt_config["system_prompt"],
                context=context_data.context,
                recent_messages=recent_messages,
                language=language,
            )
            log_with_context(
                logger,
                logging.DEBUG,
                "Built suggestion prompt",
                system_prompt_length=len(messages[0]["content"]),
                user_prompt_length=len(messages[1]["content"]),
                prompt_length=len(messages[0]["content"]) + len(messages[1]["content"]),
                project_type=project_type,
                count=count,
                language=language,
            )

            # Step 5: Call LLM
            result = await asyncio.wait_for(
                self.llm.acomplete(
                    messages=messages,
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
            # 日志只记长度与固定分类：回复里可能复述作品正文，异常文本也可能带着回复原文。
            log_with_context(
                logger,
                logging.INFO,
                "Received LLM response for suggestions",
                stage="llm_call",
                response_length=len(result) if result else 0,
            )

            # Step 6: Parse and validate suggestions
            suggestions = self._parse_json_suggestions(result)

            if suggestions is None:
                log_with_context(
                    logger,
                    logging.WARNING,
                    "Failed to parse suggestions from LLM response",
                    stage="parse",
                    failure="parse_failed",
                    response_length=len(result) if result else 0,
                    falling_back=True,
                )
                return self._get_fallback_suggestions(count, language)

            return suggestions[:count]

        except APIException:
            raise
        except Exception as e:
            if not offloaded and hasattr(session, "rollback"):
                session.rollback()
            log_with_context(
                logger,
                logging.ERROR,
                "Error generating suggestions",
                failure="timeout" if isinstance(e, TimeoutError) else "exception",
                error_type=type(e).__name__,
                falling_back=True,
                exc_info=False,
            )
            return self._get_fallback_suggestions(count, language)

    # -------------------------------------------------------------------------
    # Private Helper Methods
    # -------------------------------------------------------------------------

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
        from models import ChatMessage, ChatMessageArchive, Project
        from models.chat_message_archive import has_chat_message_archive_table

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

        chat_session = latest_active_chat_session(
            session, project_id=project_id, user_id=user_id
        )
        if not chat_session:
            return []

        # Suggestions need only role/content, not reasoning or tool/UI payloads.
        stmt = (
            select(ChatMessage.id, ChatMessage.role, ChatMessage.content, ChatMessage.created_at)
            .where(ChatMessage.session_id == chat_session.id)
            .order_by(ChatMessage.created_at.desc(), desc(ChatMessage.id))  # type: ignore[attr-defined]
            .limit(CHAT_HISTORY_LIMIT)
        )
        hot_rows = list(session.exec(stmt).all())
        messages = list(reversed(hot_rows))

        if has_chat_message_archive_table(session.connection()):
            watermark_row = session.exec(
                select(ChatMessageArchive.created_at, ChatMessageArchive.id)
                .where(ChatMessageArchive.session_id == chat_session.id)
                .order_by(ChatMessageArchive.created_at.desc(), desc(ChatMessageArchive.id))  # type: ignore[attr-defined]
                .limit(1)
            ).first()
            if watermark_row is not None:
                cold_watermark = (watermark_row[0], watermark_row[1])
                oldest_hot_key = (hot_rows[-1].created_at, hot_rows[-1].id) if hot_rows else None
                need_cold = len(hot_rows) < CHAT_HISTORY_LIMIT or (
                    oldest_hot_key is not None and cold_watermark > oldest_hot_key
                )
                if need_cold:
                    cold_stmt = select(
                        ChatMessageArchive.id,
                        ChatMessageArchive.role,
                        ChatMessageArchive.content,
                        ChatMessageArchive.created_at,
                    ).where(ChatMessageArchive.session_id == chat_session.id)
                    hot_ids = [row.id for row in hot_rows]
                    if hot_ids:
                        cold_stmt = cold_stmt.where(col(ChatMessageArchive.id).notin_(hot_ids))
                    cold_rows = list(
                        session.exec(
                            cold_stmt.order_by(
                                ChatMessageArchive.created_at.desc(), desc(ChatMessageArchive.id)  # type: ignore[attr-defined]
                            ).limit(CHAT_HISTORY_LIMIT)
                        ).all()
                    )
                    if cold_rows:
                        by_id = {row.id: row for row in cold_rows}
                        for row in hot_rows:
                            by_id[row.id] = row
                        messages = sorted(
                            by_id.values(), key=lambda row: (row.created_at, row.id)
                        )[-CHAT_HISTORY_LIMIT:]

        return [
            {"role": msg.role, "content": msg.content}
            for msg in messages
            if msg.role in ("user", "assistant")
        ]

    @staticmethod
    def _coerce_suggestions(data: Any) -> list | None:
        """Extract the suggestion list from either an object or a bare array."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            got = data.get("suggestions")
            return got if isinstance(got, list) else None
        return None

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

    def _parse_json_suggestions(self, response: str) -> list[str] | None:
        """
        Parse suggestions from LLM JSON response with repair.

        Args:
            response: Raw LLM response text

        Returns:
            List of parsed suggestions (empty is valid); None on parse/validation failure
        """
        if not response or not response.strip():
            return None

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
                if suggestions is not None and (not suggestions or valid):
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
                stage="parse",
                error_type=type(e).__name__,
            )

        # If standard parsing failed, try JSON repair
        try:
            from json_repair import repair_json

            repaired = repair_json(response)
            data = json.loads(repaired)
            suggestions = self._coerce_suggestions(data)
            valid = self._validate_suggestions(suggestions)
            if suggestions is not None and (not suggestions or valid):
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
                stage="parse",
                error_type=type(e).__name__,
            )

        return None

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
            if isinstance(s, str)
            and len(s.strip()) >= MIN_SUGGESTION_LENGTH
        ]

    def _build_system_prompt(
        self,
        system_prompt: str,
        language: str | None = None,
    ) -> str:
        """Build the static, cache-stable system prompt for suggestion generation."""
        if self._is_english(language):
            return f"{system_prompt}\n\n{ENGLISH_OUTPUT_RULE}"
        return system_prompt

    def _build_user_prompt(
        self,
        context: str,
        recent_messages: list[dict[str, str]],
        language: str | None = None,
    ) -> str:
        """Build the dynamic user prompt with slower-changing context before recent chat."""
        is_english = self._is_english(language)
        chat_str = self._format_chat_history(recent_messages, is_english)

        context_label = "项目信息:" if not is_english else "Project info:"
        conversation_label = "最近对话:" if not is_english else "Recent conversation:"
        no_context_text = "(暂无项目信息)" if not is_english else "(No project context)"

        return "\n".join([
            context_label,
            context or no_context_text,
            "",
            conversation_label,
            chat_str,
        ])

    def _build_messages(
        self,
        system_prompt: str,
        context: str,
        recent_messages: list[dict[str, str]],
        language: str | None = None,
    ) -> list[dict[str, str]]:
        """Build `[system, user]` messages so the static instructions stay prefix-cacheable."""
        return [
            {
                "role": "system",
                "content": self._build_system_prompt(system_prompt, language=language),
            },
            {
                "role": "user",
                "content": self._build_user_prompt(
                    context=context,
                    recent_messages=recent_messages,
                    language=language,
                ),
            },
        ]

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
