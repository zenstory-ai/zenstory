"""
Message manager for chat history and system prompt construction.

Handles:
- Saving user and assistant messages to chat history
- Building system prompts with project context
- Managing chat sessions
"""

import asyncio
import json as json_module
from datetime import UTC, datetime
from typing import Any

from sqlmodel import Session, select

from config.datetime_utils import normalize_datetime_to_utc, utcnow
from database import create_session
from utils.logger import get_logger, log_with_context

from ..constants import should_offload_session_work
from ..prompts import get_prompt_parts_for_project_type
from .chat_storage import (
    archive_pre_compaction_tool_calls_safe,
    display_events_have_thinking_content,
)

logger = get_logger(__name__)

# 本轮上下文块的标签：loop/turn_context.build_loop_turn_context 产出，service 拼在本轮用户消息开头。
TURN_CONTEXT_OPEN = "<turn_context>"
TURN_CONTEXT_CLOSE = "</turn_context>"
# 本次运行的系统指令（作为 agent 运行输入的最后一条消息，例如截断后的交付说明）的开头。
AGENT_DIRECTIVE_HEADER = "[本次运行的系统指令]"


def normalize_assistant_content(content: str | None) -> str:
    """规范化待落库的助手消息正文：纯空白归一为空串，非空正文仅裁去首部换行（保留缩进与内部全部原文）。"""
    if not content:
        return ""
    cleaned = content.replace("\x00", "")
    if not cleaned.strip():
        return ""
    return cleaned.lstrip("\r\n")


def normalize_assistant_display_events(
    display_events: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """清理待落库的有序展示事件：移除孤立的纯空白文本段，首个非空 content 段剥离首部换行，内部文本段逐字保留。"""
    if not display_events or not isinstance(display_events, list):
        return None
    normalized: list[dict[str, Any]] = []
    seen_content = False
    for event in display_events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type in {"content", "thinking_content"} and isinstance(event.get("content"), str):
            raw = event["content"].replace("\x00", "")
            if not raw.strip():
                continue
            if event_type == "content" and not seen_content:
                cleaned = raw.lstrip("\r\n")
                seen_content = True
            else:
                cleaned = raw
            normalized.append(event if cleaned == event["content"] else {**event, "content": cleaned})
            continue
        normalized.append(event)
    return normalized or None


def has_substantive_display_events(display_events: list[dict[str, Any]] | None) -> bool:
    """展示时间线里是否含有实质可展示内容（排除压缩标记与纯空白文本块）。"""
    if not display_events or not isinstance(display_events, list):
        return False
    for event in display_events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        if event_type == "history_compacted":
            continue
        if event_type in {"content", "thinking_content"}:
            content = event.get("content")
            if isinstance(content, str) and content.strip():
                return True
            continue
        return True
    return False

class MessageManager:
    """
    Manages chat messages and system prompt construction.

    Responsibilities:
    - Save messages to chat history
    - Build system prompts with context
    - Load chat history
    """

    def __init__(self, project_id: str, user_id: str | None = None):
        """
        Initialize message manager.

        Args:
            project_id: Project ID
            user_id: User ID (optional, for chat history)
        """
        self.project_id = project_id
        self.user_id = user_id

    async def save_messages(
        self,
        session: Session,
        session_id: str,
        user_message: str,
        assistant_message: str,
        tool_calls: list | None = None,
        reasoning_content: str | None = None,
        assistant_stop_reason: str | None = None,
        assistant_usage: dict[str, Any] | None = None,
        assistant_status_cards: list[dict[str, Any]] | None = None,
        steering_messages: list[str] | None = None,
        assistant_display_events: list[dict[str, Any]] | None = None,
        user_created_at: datetime | None = None,
        assistant_extra_metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """
        Save messages to chat history.

        Args:
            session: Database session
            session_id: Exact resolved chat session to persist into
            user_message: User's message content
            assistant_message: Assistant's response content
            tool_calls: List of tool calls made during the response
            reasoning_content: Thinking/reasoning content from the model
            assistant_stop_reason: Model stop reason from MESSAGE_END
            assistant_usage: Model usage payload from MESSAGE_END
            assistant_status_cards: Terminal workflow status cards
            steering_messages: Steering messages injected mid-run, persisted as
                their own user-role turns so they survive into later requests
            assistant_display_events: Ordered renderer timeline for the assistant
                row; omitted for legacy callers that do not provide one

        Returns:
            Persisted assistant message ID when history save succeeds
        """
        if should_offload_session_work(session):
            return await asyncio.to_thread(
                self._save_messages_sync,
                session_id,
                user_message,
                assistant_message,
                tool_calls,
                reasoning_content,
                assistant_stop_reason,
                assistant_usage,
                assistant_status_cards,
                steering_messages,
                assistant_display_events,
                user_created_at=user_created_at,
                assistant_extra_metadata=assistant_extra_metadata,
            )
        return self._save_messages_with_session(
            session,
            session_id,
            user_message,
            assistant_message,
            tool_calls,
            reasoning_content,
            assistant_stop_reason,
            assistant_usage,
            assistant_status_cards,
            steering_messages,
            assistant_display_events,
            user_created_at=user_created_at,
            assistant_extra_metadata=assistant_extra_metadata,
        )

    def _save_messages_sync(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        tool_calls: list | None = None,
        reasoning_content: str | None = None,
        assistant_stop_reason: str | None = None,
        assistant_usage: dict[str, Any] | None = None,
        assistant_status_cards: list[dict[str, Any]] | None = None,
        steering_messages: list[str] | None = None,
        assistant_display_events: list[dict[str, Any]] | None = None,
        user_created_at: datetime | None = None,
        assistant_extra_metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Persist chat history with a fresh sync DB session."""
        with create_session() as session:
            return self._save_messages_with_session(
                session,
                session_id,
                user_message,
                assistant_message,
                tool_calls,
                reasoning_content,
                assistant_stop_reason,
                assistant_usage,
                assistant_status_cards,
                steering_messages,
                assistant_display_events,
                user_created_at=user_created_at,
                assistant_extra_metadata=assistant_extra_metadata,
            )

    def _save_messages_with_session(
        self,
        session: Session,
        session_id: str,
        user_message: str,
        assistant_message: str,
        tool_calls: list | None = None,
        reasoning_content: str | None = None,
        assistant_stop_reason: str | None = None,
        assistant_usage: dict[str, Any] | None = None,
        assistant_status_cards: list[dict[str, Any]] | None = None,
        steering_messages: list[str] | None = None,
        assistant_display_events: list[dict[str, Any]] | None = None,
        persist_empty_assistant: bool = False,
        user_created_at: datetime | None = None,
        assistant_extra_metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Core chat-history persistence logic using the provided session.

        persist_empty_assistant: write the assistant row even when it has no
        payload (a stopped / disconnected round keeps its terminal state).
        assistant_extra_metadata: extra message_metadata keys of the loop engine
        (``engine``, ``prompt_tokens``, ``compaction``); never overrides the keys above.
        """
        from models import ChatMessage, ChatSession

        if not self.user_id:
            return None
        if not session_id:
            raise ValueError("session_id is required for chat history")

        try:
            # Check if project is not deleted
            from models import Project

            project = session.get(Project, self.project_id)
            if not project or project.is_deleted:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Project not found or deleted, skipping save",
                    project_id=self.project_id,
                    user_id=self.user_id,
                )
                return None

            chat_session = session.get(ChatSession, session_id)
            if chat_session is None:
                raise ValueError(f"Chat session not found: {session_id}")
            if (
                chat_session.project_id != self.project_id
                or chat_session.user_id != self.user_id
            ):
                raise ValueError(
                    f"Chat session does not belong to project/user: {session_id}"
                )

            if "\x00" in user_message:
                user_message = user_message.replace("\x00", "")
            assistant_message = normalize_assistant_content(assistant_message)
            if reasoning_content and "\x00" in reasoning_content:
                reasoning_content = reasoning_content.replace("\x00", "")
            assistant_display_events = normalize_assistant_display_events(assistant_display_events)

            # Save user message
            # 用户消息的时间是作者发出它的时间（回合开始），不是这一轮落库的时间。
            user_chat_message = ChatMessage(
                session_id=chat_session.id,
                role="user",
                content=user_message,
                **({"created_at": user_created_at} if user_created_at else {}),
            )
            session.add(user_chat_message)
            rows_added = 1

            # Persist steering messages injected mid-run as their own user-role
            # turns (between the original user message and the assistant reply),
            # so node-boundary steering survives into the next request's history
            # instead of being dropped when the stream ends.
            steering_saved = 0
            for steering_text in steering_messages or []:
                if not isinstance(steering_text, str):
                    continue
                if "\x00" in steering_text:
                    steering_text = steering_text.replace("\x00", "")
                if not steering_text.strip():
                    continue
                session.add(
                    ChatMessage(
                        session_id=chat_session.id,
                        role="user",
                        content=steering_text,
                    )
                )
                steering_saved += 1
            rows_added += steering_saved

            # Save assistant message
            # 本轮没有任何 assistant 产出时不写空行：这条消息前端永远渲染不出来，
            # 却会占掉"最近消息"窗口的一格，把更早的真实消息挤出上下文。
            # 判据与 service.py::_has_assistant_payload 一致——正文、工具调用、
            # 状态卡片、有序展示事件、思维链任一非空即视为有产出。
            assistant_chat_message: ChatMessage | None = None
            if (
                persist_empty_assistant
                or assistant_message.strip()
                or tool_calls
                or assistant_status_cards
                or has_substantive_display_events(assistant_display_events)
                or (reasoning_content or "").strip()
            ):
                tool_calls_json = self._serialize_tool_calls(tool_calls)
                assistant_metadata_json = self._serialize_message_metadata(
                    stop_reason=assistant_stop_reason,
                    usage=assistant_usage,
                    status_cards=assistant_status_cards,
                    display_events=assistant_display_events,
                    extra_metadata=assistant_extra_metadata,
                )
                persisted_reasoning_content = (
                    None
                    if display_events_have_thinking_content(assistant_display_events)
                    else reasoning_content
                )
                assistant_chat_message = ChatMessage(
                    session_id=chat_session.id,
                    role="assistant",
                    content=assistant_message,
                    tool_calls=tool_calls_json,
                    reasoning_content=persisted_reasoning_content,
                    message_metadata=assistant_metadata_json,
                )
                session.add(assistant_chat_message)
                rows_added += 1

            # 按实际写入的行数递增。写死 +2 与真实行数脱钩：steering 被跳过、
            # assistant 为空时 message_count 会越记越多，分页与"最近 N 条"全错。
            chat_session.message_count += rows_added
            chat_session.updated_at = utcnow()
            # IDs are generated on construction; reading the expired row after
            # commit would reload its entire message payload just to get the ID.
            assistant_message_id = assistant_chat_message.id if assistant_chat_message else None
            session.commit()

            # 压缩记录已安全提交后，尽力归档压缩点覆盖范围内（<= covers_through_message_id）
            # 的历史 query_files / parallel_execute 正文载荷；任何异常只记日志不阻断主保存。
            if assistant_chat_message is not None and isinstance(assistant_extra_metadata, dict):
                from ..loop.history import valid_compaction

                compaction = valid_compaction(assistant_extra_metadata)
                if compaction is not None:
                    archive_pre_compaction_tool_calls_safe(
                        session,
                        chat_session_id=session_id,
                        covers_through_message_id=compaction["covers_through_message_id"],
                    )

            log_with_context(
                logger,
                20,  # INFO
                "Messages saved to chat history",
                project_id=self.project_id,
                user_id=self.user_id,
                session_id=chat_session.id,
                message_count=chat_session.message_count,
                tool_calls_count=len(tool_calls) if tool_calls else 0,
                has_usage=assistant_usage is not None,
                stop_reason=assistant_stop_reason,
                rows_added=rows_added,
            )
            return assistant_message_id

        except Exception as e:
            session.rollback()
            log_with_context(
                logger,
                40,  # ERROR
                "Failed to save messages to chat history",
                project_id=self.project_id,
                user_id=self.user_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            raise

    def _serialize_tool_calls(self, tool_calls: list | None) -> str | None:
        """
        Serialize tool calls to JSON string.

        Args:
            tool_calls: List of tool call objects

        Returns:
            JSON string or None
        """
        if not tool_calls:
            return None

        serialized_calls = [
            {
                "id": tc.get("id", ""),
                "name": tc.get("name", ""),
                "arguments": tc.get("arguments", ""),
                "status": tc.get("status", "success"),
                "result": tc.get("result"),
                "error": tc.get("error"),
            }
            for tc in tool_calls
            if isinstance(tc, dict) and "name" in tc
        ]
        return json_module.dumps(serialized_calls, ensure_ascii=False, separators=(",", ":"))

    def _serialize_message_metadata(
        self,
        stop_reason: str | None = None,
        usage: dict[str, Any] | None = None,
        status_cards: list[dict[str, Any]] | None = None,
        display_events: list[dict[str, Any]] | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Serialize assistant metadata payload to JSON."""
        payload: dict[str, Any] = {}
        if stop_reason:
            payload["stop_reason"] = stop_reason
        if usage and isinstance(usage, dict):
            payload["usage"] = usage
        if status_cards and isinstance(status_cards, list):
            payload["status_cards"] = status_cards
        if display_events and isinstance(display_events, list):
            payload["display_events"] = display_events
        if extra_metadata and isinstance(extra_metadata, dict):
            for key, value in extra_metadata.items():
                if key not in payload and value is not None:
                    payload[key] = value

        if not payload:
            return None

        return json_module.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def build_system_prompt(
        self,
        session: Session,
        language: str | None = None,
        skill_catalog: str | None = None,
    ) -> str:
        """
        Build the single loop agent's static system prompt, shared by every turn.

        The project-type configuration, plus the loop's input layout and working rules
        (agent/loop/prompts.py). Only content that stays the same across turns goes
        here: the project's base prompt, reply-language rules, where per-turn context
        lives, and the L1 skill catalog. DeepSeek caches by exact prefix, so anything
        that varies here would make the whole replayed history miss. Per-turn content
        is placed in this turn's user message (agent/loop/turn_context.py).

        Args:
            session: Database session for querying project info
            language: Language preference (zh/en)
            skill_catalog: L1 skill catalog (name + description only)

        Returns:
            Complete system prompt string
        """
        from models import Project, User

        force_en = self._force_en(language)

        # Get project to determine type
        project = session.get(Project, self.project_id)
        if not project:
            raise ValueError(f"Project not found: {self.project_id}")

        # Check if project is soft-deleted
        if project.is_deleted:
            raise ValueError(f"Project not found: {self.project_id}")

        # Defense-in-depth: verify project ownership here as well.
        #
        # Most API entrypoints already validate access before calling the agent service,
        # but this extra check prevents accidental cross-project prompt assembly if a
        # future caller forgets to enforce access control.
        if self.user_id:
            user = session.get(User, self.user_id)
            if not user:
                raise ValueError(f"User not found: {self.user_id}")
            if not user.is_superuser and project.owner_id != user.id:
                # Do not leak project existence.
                raise ValueError(f"Project not found: {self.project_id}")

        project_type = project.project_type or "novel"

        # Build folder IDs for system prompt placeholders.
        #
        # NOTE:
        # - New projects use predictable root folder IDs (project_id-xxx-folder).
        # - Older/legacy projects may use random UUID root folders or slightly different
        #   titles (e.g. screenplay "场景" used as settings folder).
        #
        # We must resolve actual root folder IDs from DB to avoid AI creating duplicate
        # root folders and "losing" files in the UI.
        folder_ids = self._get_folder_ids(
            session=session,
            project_type=project_type,
        )

        # Strictly separate Global Static Prefix -> Project Static Suffix so
        # project/folder UUIDs sit at the very end and never fragment cross-project KV cache.
        static_prefix, project_suffix = get_prompt_parts_for_project_type(
            project_type, self.project_id, folder_ids
        )

        parts = [static_prefix]

        # 回复语言规则（跟随作者最近一条消息；界面语言只在判断不了时兜底）
        parts.extend(self._build_language_section(force_en))

        from ..loop.prompts import LOOP_INPUT_LAYOUT_SECTION, LOOP_WORKING_RULES

        parts.extend([LOOP_INPUT_LAYOUT_SECTION, "", LOOP_WORKING_RULES])

        # Add skill catalog (L1: name + description only; instructions via load_skill)
        if skill_catalog:
            parts.extend(self._build_skill_catalog_section(skill_catalog))

        if project_suffix:
            parts.extend(["", project_suffix])

        return "\n".join(parts)

    @staticmethod
    def _force_en(language: str | None) -> bool:
        lang = (language or "").strip().lower() or "zh"
        return lang.startswith("en")

    def _build_language_section(self, force_en: bool) -> list[str]:
        """回复语言规则：完全由模型按作者最近一条消息判断，界面语言只在判断不了时兜底。"""
        if force_en:
            return [
                "",
                "## Reply language",
                "- Write chat replies to the author in the exact language of the author's latest message "
                "(whether Chinese, English, Vietnamese, Japanese, or any other language). "
                "When the author switches language, switch with them immediately; earlier assistant replies "
                "and the interface language do not override the author's latest message.",
                "- When the latest message has no clear language (only an option letter, a number, or a link), "
                "keep the language the author has been using in this conversation; if that is unclear too, "
                "fall back to English (the author's interface language).",
                "- Keep each chat reply in one consistent language unless quoting text from the manuscript.",
                "- This rule covers chat replies only. File content (<file> blocks, edit_file text, "
                "create_file content) stays in the manuscript's existing language; a new file follows "
                "the language the author asked for or the language of the existing manuscript.",
            ]
        return [
            "",
            "## 回复语言",
            "- 给作者的聊天回复使用作者最近一条消息的语言（无论中文、英文、越南语、日语或其他任何语言）；"
            "作者切换语言时立刻跟着切换，不要被之前的助手回复或界面语言带偏。",
            "- 作者最近一条消息看不出语言（只有选项字母、数字、链接）时，沿用作者在这段对话里一直用的语言；"
            "还判断不了就用中文（作者的界面语言）。",
            "- 单条聊天回复保持语言一致，除非引用稿件原文。",
            "- 这条只管聊天回复。文件内容（<file> 正文、edit_file 文本、create_file 内容）保持稿件原有语言；"
            "新文件用作者要求的语言或已有稿件的语言。",
        ]

    def _build_metadata_section(
        self, metadata: dict[str, Any], force_en: bool
    ) -> list[str]:
        """Build current file metadata section."""
        parts: list[str] = []
        file_info = []

        if "current_file_id" in metadata:
            file_info.append(f"- {'File ID' if force_en else '文件 ID'}: {metadata['current_file_id']}")
        if "current_file_type" in metadata:
            file_info.append(f"- {'Type' if force_en else '类型'}: {metadata['current_file_type']}")
        if "current_file_title" in metadata:
            file_info.append(f"- {'Title' if force_en else '标题'}: {metadata['current_file_title']}")

        if file_info:
            parts.extend(
                ["", "## Current File" if force_en else "## 当前文件"] + file_info
            )

        return parts

    def _build_skill_catalog_section(self, skill_catalog: str) -> list[str]:
        """Build skill catalog section for AI-driven selection."""
        return [
            "",
            skill_catalog,
        ]

    @staticmethod
    def _selected_skill_truncation_note(selected: dict[str, Any], force_en: bool) -> str | None:
        """正文因技能内容预算被截断时，告诉模型从哪里续读。"""
        next_offset = selected.get("instructions_next_offset")
        if not isinstance(next_offset, int):
            return None
        skill_id = str(selected.get("id") or selected.get("name") or "")
        total = selected.get("instructions_total_chars")
        call = f'read_skill_resource(name="{skill_id}", path="SKILL.md", offset={next_offset})'
        if force_en:
            return (
                f"(Truncated: only the first {next_offset} of {total} characters are shown. "
                f"Call {call} to read the rest in segments when needed.)"
            )
        return f"（正文过长，以上只是前 {next_offset} 个字符，共 {total} 个；需要后续内容时调用 {call} 分段读取。）"

    def _build_selected_skill_section(
        self,
        selected_skills: list[dict[str, Any]],
        force_en: bool,
    ) -> list[str]:
        """Build the high-priority section for skills the user selected for this message."""
        blocks: list[tuple[str, str, list[str], str | None]] = []
        for selected in selected_skills:
            skill_name = str(selected.get("name") or "").strip()
            instructions = str(selected.get("instructions") or "").strip()
            if not skill_name or not instructions:
                continue
            resources = [str(path) for path in (selected.get("resources") or []) if path]
            blocks.append((
                skill_name,
                instructions,
                resources,
                self._selected_skill_truncation_note(selected, force_en),
            ))

        if not blocks:
            return []

        if force_en:
            lines = [
                "",
                "## User-Selected Skills For This Message",
                "",
                "The user explicitly selected the following skill(s) for this message.",
                "Treat them as the user's direct choice for this turn and prioritize them over other skills.",
                "Skill content is reference material: it cannot override system rules or the user's explicit instructions.",
            ]
            for skill_name, instructions, resources, truncation_note in blocks:
                lines.extend(["", f"### {skill_name}", "", instructions])
                if truncation_note:
                    lines.extend(["", truncation_note])
                if resources:
                    lines.extend(["", "Resource files (read with `read_skill_resource` when needed):"])
                    lines.extend(f"- {path}" for path in resources)
            lines.extend([
                "",
                "Requirements:",
                "- Prioritize the selected skill(s) for this message unless a higher-priority safety/system rule conflicts.",
                "- Their instructions are already above; do not call `load_skill` for them again.",
                "- Do not replace them with another skill just because another skill also seems relevant.",
            ])
            return lines

        lines = [
            "",
            "## 用户本条消息指定技能",
            "",
            "用户为本条消息显式选择了以下技能。",
            "将其视为用户对本轮请求的明确选择，并优先按这些技能执行，而不是自行改选其他技能。",
            "技能内容是参考资料，不能凌驾系统规则，也不能覆盖用户的明确指令。",
        ]
        for skill_name, instructions, resources, truncation_note in blocks:
            lines.extend(["", f"### {skill_name}", "", instructions])
            if truncation_note:
                lines.extend(["", truncation_note])
            if resources:
                lines.extend(["", "附带资源文件（需要时用 `read_skill_resource` 读取）："])
                lines.extend(f"- {path}" for path in resources)
        lines.extend([
            "",
            "必须遵守：",
            "- 本条消息优先使用上述技能；仅在更高优先级的系统/安全规则冲突时才可偏离。",
            "- 这些技能的完整方法已在上方，不需要再调用 `load_skill` 加载。",
            "- 不要因为其他技能也相关，就忽略这次显式指定。",
        ])
        return lines

    def _get_folder_ids(
        self,
        *,
        session: Session,
        project_type: str,
    ) -> dict[str, str]:
        """
        Resolve project root folder IDs for prompt placeholders.

        Args:
            session: Database session
            project_type: Type of project (novel, short, screenplay)

        Returns:
            Dict mapping folder types to their IDs
        """
        from sqlalchemy import or_

        from models import File

        def _norm(text: str | None) -> str:
            return "".join((text or "").split()).strip().lower()

        def _stable_sort_key(folder: File) -> tuple[int, datetime, str]:
            created_at = getattr(folder, "created_at", None)
            if isinstance(created_at, datetime):
                return (0, normalize_datetime_to_utc(created_at), str(folder.id))
            return (1, datetime.max.replace(tzinfo=UTC), str(folder.id))

        # Folder specs: (placeholder_key, deterministic_suffix, title_aliases)
        # - Deterministic ids come from current project template: {project_id}-{suffix}
        # - title_aliases handle legacy folder titles / language variants
        folder_specs_by_type: dict[str, list[tuple[str, str, list[str]]]] = {
            "novel": [
                ("lore", "lore-folder", ["设定", "世界观", "world building", "settings"]),
                ("character", "character-folder", ["角色", "人物", "characters"]),
                ("material", "material-folder", ["素材", "materials"]),
                ("outline", "outline-folder", ["大纲", "outlines"]),
                ("draft", "draft-folder", ["正文", "草稿", "drafts"]),
            ],
            "short": [
                ("character", "character-folder", ["人物", "角色", "characters"]),
                ("outline", "outline-folder", ["构思", "大纲", "concept", "outlines"]),
                ("material", "material-folder", ["素材", "materials"]),
                ("draft", "draft-folder", ["正文", "草稿", "drafts"]),
            ],
            "screenplay": [
                ("character", "character-folder", ["角色", "人物", "characters"]),
                # Legacy projects sometimes used "场景" as the settings folder.
                ("lore", "lore-folder", ["设定", "场景", "世界观", "world building", "settings", "scene", "scenes"]),
                ("material", "material-folder", ["素材", "materials"]),
                ("outline", "outline-folder", ["分集大纲", "大纲", "episodeoutlines", "outlines"]),
                ("script", "script-folder", ["剧本", "scripts"]),
            ],
        }

        folder_specs = folder_specs_by_type.get(project_type) or folder_specs_by_type["novel"]

        # Fetch existing root folders.
        #
        # NOTE: Some legacy imports/migrations stored root folders with parent_id=""
        # instead of NULL, so we treat both as "root" here.
        root_folders = session.exec(
            select(File).where(
                File.project_id == self.project_id,
                File.file_type == "folder",
                File.is_deleted.is_(False),
                or_(File.parent_id.is_(None), File.parent_id == ""),
            )
        ).all()

        root_ids = [f.id for f in root_folders if f.id]
        # Lazily populated only when a folder key has NO canonical root match (legacy
        # unrecognized-title fallback). Avoids a GROUP BY query on normal projects and
        # avoids mutable child counts ever influencing canonical tie-breaking.
        folders_by_child_type: dict[str, set[str]] | None = None

        def _folders_with_child_type(expected_child_type: str) -> set[str]:
            nonlocal folders_by_child_type
            if not expected_child_type or not root_ids:
                return set()
            if folders_by_child_type is None:
                folders_by_child_type = {}
                typed_rows = session.exec(
                    select(File.parent_id, File.file_type)
                    .where(
                        File.project_id == self.project_id,
                        File.is_deleted.is_(False),
                        File.parent_id.in_(root_ids),
                    )
                    .distinct()
                ).all()
                for parent_id, file_type in typed_rows:
                    if not parent_id or not file_type:
                        continue
                    folders_by_child_type.setdefault(str(file_type), set()).add(str(parent_id))
            return folders_by_child_type.get(expected_child_type, set())

        def _pick_folder_id(key: str, suffix: str, aliases: list[str]) -> str:
            deterministic_id = f"{self.project_id}-{suffix}"
            alias_norms = [_norm(a) for a in aliases if a]

            expected_child_type_by_key = {
                "character": "character",
                "lore": "lore",
                "outline": "outline",
                "draft": "draft",
                "script": "script",
                "material": "snippet",
            }
            expected_child_type = expected_child_type_by_key.get(key, "")

            def _title_matches(folder: File) -> bool:
                title_norm = _norm(getattr(folder, "title", ""))
                if not title_norm:
                    return False
                return any(alias and alias in title_norm for alias in alias_norms)

            # 稳定排序：规范目录（确定性 id 或标题命中别名）一律按不可变的
            # (created_at ASC, id ASC) 决胜，不依赖会随写入/移动波动的子文件计数，
            # 防止会话中途新建或移动文件翻转 folder_ids、击穿 system_prompt_sha256。
            canonical_candidates = [
                folder
                for folder in root_folders
                if str(folder.id) == deterministic_id or _title_matches(folder)
            ]
            if canonical_candidates:
                best = min(canonical_candidates, key=_stable_sort_key)
                resolved_by_title_only = str(best.id) != deterministic_id
            else:
                # 兜底路径：没有任何规范目录时，仍识别「装着该类型文件」的旧目录，
                # 同样按不可变的 (created_at ASC, id ASC) 稳定排序。
                matching_parents = _folders_with_child_type(expected_child_type)
                typed_candidates = [
                    folder for folder in root_folders if str(folder.id) in matching_parents
                ]
                if not typed_candidates:
                    return deterministic_id
                best = min(typed_candidates, key=_stable_sort_key)
                resolved_by_title_only = False

            resolved_id = str(best.id)
            if resolved_id != deterministic_id:
                log_with_context(
                    logger,
                    20,  # INFO
                    "Resolved legacy root folder id for prompt placeholder",
                    project_id=self.project_id,
                    user_id=self.user_id,
                    project_type=project_type,
                    folder_key=key,
                    resolved_folder_id=resolved_id,
                    deterministic_folder_id=deterministic_id,
                    resolved_title=getattr(best, "title", ""),
                    resolved_by_title_only=resolved_by_title_only,
                )

            return resolved_id

        folder_ids: dict[str, str] = {}
        for key, suffix, aliases in folder_specs:
            folder_ids[key] = _pick_folder_id(key, suffix, aliases)

        return folder_ids
