"""
Message manager for chat history and system prompt construction.

Handles:
- Saving user and assistant messages to chat history
- Building system prompts with project context
- Managing chat sessions
"""

import asyncio
import json as json_module
import re
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from config.datetime_utils import utcnow
from database import create_session
from utils.logger import get_logger, log_with_context

from ..prompts import get_prompt_for_project_type
from ..utils.world_model_visibility import (
    extract_item_metadata,
    resolve_world_model_channels,
)

_HAN_CHAR_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_WORD_RE = re.compile(r"[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)


def detect_author_language(text: str | None) -> str | None:
    """作者这条消息用的是中文还是英文；看不出来（选项字母、数字、链接）时返回 None。

    汉字和英文词混写时按量比：一个英文词大致抵两个汉字（「把 POV 改成第一人称」是中文，
    「Write chapter 3 where 苏晚 meets 沈砚」是英文）。
    """
    cleaned = _URL_RE.sub(" ", text or "")
    han = len(_HAN_CHAR_RE.findall(cleaned))
    words = len(_LATIN_WORD_RE.findall(cleaned))
    if han == 0:
        return "en" if words >= 2 else None
    if words == 0:
        return "zh"
    return "zh" if han >= words * 2 else "en"


logger = get_logger(__name__)

# 本轮上下文块的标签：MessageManager.build_turn_context 产出，service 拼在本轮用户消息开头。
TURN_CONTEXT_OPEN = "<turn_context>"
TURN_CONTEXT_CLOSE = "</turn_context>"
# 每次 agent 运行输入的最后一条消息（nodes.build_agent_directive）的开头。
AGENT_DIRECTIVE_HEADER = "[本次运行的系统指令]"

_CONTEXT_MAX_CONSTRAINTS = 12
_CONTEXT_MAX_WORLD_KNOWLEDGE = 24
_CONTEXT_MAX_WORKING_SET = 18
_CONTEXT_ITEM_TEXT_CHAR_LIMIT = 220
# 原始项目上下文的兜底字符上限。真正的预算由 ContextAssembler 按 token 控制
# （AGENT_CONTEXT_TOKEN_BUDGET，默认 32k token ≈ 6 万中文字），这里只防异常超长，
# 必须明显高于组装预算，否则会把已按预算选好的全文条目再拦腰截断。
_CONTEXT_RAW_CHAR_LIMIT = 200000
# world_model 预览条目的节选标记：明确这是摘要而不是原文，避免被当成「被压缩的全文」。
_CONTEXT_PREVIEW_SUFFIX = "…（节选）"
_CONTEXT_RAW_TRUNCATION_NOTICE = (
    "[项目上下文超出长度上限，以下内容被截断；末尾条目可能不完整，"
    "未标注[全文]的条目需要时用 query_files(id=…) 读取]"
)


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
        assistant_routing: dict[str, Any] | None = None,
        user_created_at: datetime | None = None,
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
            assistant_routing: This turn's routing (initial/last agent, workflow,
                read_only, write_content, scope); read back by the next turn's
                resume / clarification-reply routing

        Returns:
            Persisted assistant message ID when history save succeeds
        """
        if self._should_offload_session_work(session):
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
                assistant_routing,
                user_created_at=user_created_at,
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
            assistant_routing,
            user_created_at=user_created_at,
        )

    def _should_offload_session_work(self, session: Session) -> bool:
        """Only offload when running against PostgreSQL production-style sessions."""
        bind = session.get_bind() if hasattr(session, "get_bind") else None
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        return dialect_name == "postgresql"

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
        assistant_routing: dict[str, Any] | None = None,
        user_created_at: datetime | None = None,
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
                assistant_routing,
                user_created_at=user_created_at,
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
        assistant_routing: dict[str, Any] | None = None,
        persist_empty_assistant: bool = False,
        user_created_at: datetime | None = None,
    ) -> str | None:
        """Core chat-history persistence logic using the provided session.

        persist_empty_assistant: write the assistant row even when it has no
        payload (a stopped / disconnected round keeps its terminal state).
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
                if not isinstance(steering_text, str) or not steering_text.strip():
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
            tool_calls_json = self._serialize_tool_calls(tool_calls)
            assistant_metadata_json = self._serialize_message_metadata(
                stop_reason=assistant_stop_reason,
                usage=assistant_usage,
                status_cards=assistant_status_cards,
                display_events=assistant_display_events,
                routing=assistant_routing,
            )

            # 本轮没有任何 assistant 产出时不写空行：这条消息前端永远渲染不出来，
            # 却会占掉"最近消息"窗口的一格，把更早的真实消息挤出上下文。
            # 判据与 service.py::_has_assistant_payload 一致——正文、工具调用、
            # 状态卡片、有序展示事件、思维链任一非空即视为有产出。
            assistant_chat_message: ChatMessage | None = None
            if (
                persist_empty_assistant
                or (assistant_message or "").strip()
                or tool_calls
                or assistant_status_cards
                or assistant_display_events
                or (reasoning_content or "").strip()
            ):
                assistant_chat_message = ChatMessage(
                    session_id=chat_session.id,
                    role="assistant",
                    content=assistant_message,
                    tool_calls=tool_calls_json,
                    reasoning_content=reasoning_content,
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

        serialized_calls = []
        for tc in tool_calls:
            if isinstance(tc, dict):
                # New format with result info
                if "name" in tc:
                    serialized_calls.append({
                        "id": tc.get("id", ""),
                        "name": tc.get("name", ""),
                        "arguments": tc.get("arguments", ""),
                        "status": tc.get("status", "success"),
                        "result": tc.get("result"),
                        "error": tc.get("error"),
                    })
                elif "function" in tc:
                    # Old format with function key
                    serialized_calls.append({
                        "id": tc.get("id", ""),
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": tc.get("function", {}).get("arguments", ""),
                        "status": "success",
                        "result": None,
                        "error": None,
                    })
            else:
                # Legacy OpenAI object format (for backwards compatibility)
                serialized_calls.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                    "status": "success",
                    "result": None,
                    "error": None,
                })

        return json_module.dumps(serialized_calls)

    def _serialize_message_metadata(
        self,
        stop_reason: str | None = None,
        usage: dict[str, Any] | None = None,
        status_cards: list[dict[str, Any]] | None = None,
        display_events: list[dict[str, Any]] | None = None,
        routing: dict[str, Any] | None = None,
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
        if routing and isinstance(routing, dict):
            payload["routing"] = routing

        if not payload:
            return None

        return json_module.dumps(payload)

    def build_system_prompt(
        self,
        session: Session,
        language: str | None = None,
        skill_catalog: str | None = None,
    ) -> str:
        """
        Build the static system prompt shared by every turn and every agent role.

        Only content that stays the same across turns and roles goes here: the
        project's base prompt, reply-language rules, where per-turn context and
        per-run instructions live, and the L1 skill catalog. DeepSeek caches by
        exact prefix, so anything that varies here would make the whole replayed
        history miss. Per-turn content comes from build_turn_context (placed in
        this turn's user message); per-agent content (role, scope directive)
        comes from nodes.build_agent_directive (the last input message of a run).

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

        project_type = getattr(project, "project_type", None) or "novel"

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

        # Get prompt from template
        prompt = get_prompt_for_project_type(project_type, self.project_id, folder_ids)

        parts = [prompt]

        # 回复语言规则（跟随作者最近一条消息；界面语言只在判断不了时兜底）
        parts.extend(self._build_language_section(force_en))

        # 本轮上下文与本次运行指令放在哪里（固定说明，保证它们的效力）
        parts.extend(self._build_input_layout_section(force_en))

        # Add skill catalog (L1: name + description only; instructions via load_skill)
        if skill_catalog:
            parts.extend(self._build_skill_catalog_section(skill_catalog, force_en))

        return "\n".join(parts)

    def build_turn_context(
        self,
        *,
        session_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        assembled_context: str | None = None,
        context_items: list[dict[str, Any]] | None = None,
        language: str | None = None,
        selected_skills: list[dict[str, Any]] | None = None,
        author_message: str | None = None,
        full_assembled_context: str | None = None,
    ) -> str:
        """
        Build this turn's context block for the current user message.

        With a context snapshot (agent/context/turn_snapshot.py) ``assembled_context``
        holds only the blocks that changed since the snapshot; ``full_assembled_context``
        is the whole assembled context, which decides whether it is complete.

        It holds everything that changes from turn to turn: narrative constraints
        taken from this turn's context items, skills the author selected for this
        message, the assembled project context, current-file metadata, the task
        board snapshot and this turn's reply language. Putting it in the current
        user message (never in the system prompt or persisted history) keeps
        [system prompt][history] byte-identical across turns and agents.

        Args:
            session_id: Chat session ID for loading task board
            metadata: Optional metadata (current_file_id, etc.)
            assembled_context: Pre-assembled context from ContextAssembler
            context_items: Structured context items from ContextAssembler
            language: Language preference (zh/en)
            selected_skills: Skills the user explicitly selected for this message
                (dicts with name / instructions / resources); their full
                instructions are injected
            author_message: 作者本轮的原话；用来写明本轮给作者的回复用哪种语言

        Returns:
            The <turn_context> block, or "" when there is nothing to add
        """
        force_en = self._force_en(language)
        parts: list[str] = []

        raw_context_complete = self._raw_context_is_complete(
            full_assembled_context if full_assembled_context is not None else assembled_context
        )
        constraints, world_truth, world_surface = self._extract_structured_context(
            context_items=context_items,
            force_en=force_en,
            raw_context_complete=raw_context_complete,
        )
        parts.extend(
            self._build_narrative_constraints_section(
                constraints=constraints,
                force_en=force_en,
            )
        )

        if selected_skills:
            parts.extend(self._build_selected_skill_section(selected_skills, force_en))

        # Add assembled context / world model visibility contract
        if assembled_context or world_truth or world_surface:
            parts.extend(
                self._build_context_section(
                    assembled_context=assembled_context or "",
                    context_items=context_items,
                    force_en=force_en,
                    world_truth=world_truth,
                    world_surface=world_surface,
                )
            )

        # Add current file metadata
        if metadata:
            parts.extend(self._build_metadata_section(metadata, force_en))

        # Add task board state
        parts.extend(self._build_task_board_section(session_id, force_en))

        # 本轮作者消息的语言：所有角色共用这段上下文，审稿人收到的是系统拼的任务消息，
        # 只能从这里知道作者用的是什么语言
        parts.extend(self._build_author_language_line(author_message))

        if not parts:
            return ""
        return "\n".join([TURN_CONTEXT_OPEN, self._turn_context_preamble(force_en), *parts, TURN_CONTEXT_CLOSE])

    @staticmethod
    def _force_en(language: str | None) -> bool:
        lang = (language or "").strip().lower() or "zh"
        return lang.startswith("en")

    @staticmethod
    def _turn_context_preamble(force_en: bool) -> str:
        if force_en:
            return (
                "The system attached this block at the start of this turn (the author did not write it). "
                "Its project facts, constraints and task board are authoritative: where they differ from "
                "earlier conversation, follow this block."
            )
        return (
            "这一段由系统在本轮开始时附上（不是作者写的）。其中的项目事实、叙事约束和任务计划板是权威信息；"
            "与之前的对话不一致时，以这一段为准。"
        )

    @staticmethod
    def _build_input_layout_section(force_en: bool) -> list[str]:
        """说明本轮上下文与本次运行指令在输入里的位置和效力（固定文字，不随轮次变化）。"""
        if force_en:
            return [
                "",
                "## Turn context and run instructions",
                f"- The {TURN_CONTEXT_OPEN} block at the start of this turn's user message is attached by the "
                "system: project context, narrative constraints, the current file, the task board and this "
                "turn's reply language. Treat it with the authority of this system prompt; its project facts "
                "are canonical.",
                f"- The last input message, starting with {AGENT_DIRECTIVE_HEADER}, is also attached by the "
                "system for this run: your current role and this turn's scope constraints. Follow it with the "
                "authority of this system prompt; a scope constraint marked [highest priority] overrides every "
                "other rule.",
            ]
        return [
            "",
            "## 本轮上下文与本次运行指令",
            f"- 本轮用户消息开头的 {TURN_CONTEXT_OPEN} 由系统附上：项目上下文、叙事约束、当前文件、任务计划板和本轮回复语言。"
            "它与本系统提示同等效力，其中的项目事实是权威信息。",
            f"- 输入最后一条以 {AGENT_DIRECTIVE_HEADER} 开头的消息也由系统附上，只对这次运行有效：你当前的角色和本轮用户范围约束。"
            "按本系统提示的效力执行；标注 [最高优先级] 的范围约束高于其他所有规则。",
        ]

    def _build_language_section(self, force_en: bool) -> list[str]:
        """回复语言规则：跟随作者最近一条消息，看不出来时才按界面语言。"""
        if force_en:
            return [
                "",
                "## Reply language",
                "- Write chat replies to the author in the language of the author's latest message: "
                "Chinese when the author writes Chinese, English when the author writes English. "
                "When the author switches language, switch with them; earlier replies do not decide it.",
                "- When the latest message has no clear language (only an option letter, a number or a link), "
                "keep the language the author has been using in this conversation; if that is unclear too, "
                "fall back to English (the author's interface language).",
                "- Do not mix Chinese and English in one chat reply.",
                "- This rule covers chat replies only. File content (<file> blocks, edit_file text, "
                "create_file content) stays in the manuscript's existing language; a new file follows "
                "the language the author asked for or the language of the existing manuscript.",
            ]
        return [
            "",
            "## 回复语言",
            "- 给作者的聊天回复用作者最近一条消息的语言：作者写中文就用中文，写英文就用英文；"
            "作者换了语言就跟着换，之前的回复用的什么语言不算数。",
            "- 作者最近一条消息看不出语言（只有选项字母、数字、链接）时，沿用作者在这段对话里一直用的语言；"
            "还判断不了就用中文（作者的界面语言）。",
            "- 这条只管聊天回复。文件内容（<file> 正文、edit_file 文本、create_file 内容）保持稿件原有语言；"
            "新文件用作者要求的语言或已有稿件的语言。",
        ]

    @staticmethod
    def _build_author_language_line(author_message: str | None) -> list[str]:
        """写明本轮作者消息的语言（判断不了时不写，交给上面的兜底规则）。"""
        detected = detect_author_language(author_message)
        if detected == "zh":
            return [
                "",
                "## 本轮回复语言",
                "本轮作者用中文写消息：这一轮给作者的所有回复都用中文，每个角色都一样，包括审稿后的总结。",
            ]
        if detected == "en":
            return [
                "",
                "## Reply language for this turn",
                "The author wrote this turn's message in English: every reply to the author this turn is in English, "
                "for every role, including the review summary.",
            ]
        return []

    def _build_task_board_section(
        self, session_id: str, force_en: bool
    ) -> list[str]:
        """Build task board section for system prompt."""
        parts: list[str] = []

        if session_id:
            from services.infra.task_board_service import task_board_service

            tasks = (
                task_board_service.get_tasks(
                    session_id,
                    user_id=self.user_id,
                    project_id=self.project_id,
                )
                if self.user_id
                else None
            )
            if tasks:
                # Format tasks with emoji status icons
                task_lines = []
                for t in tasks:
                    status = t.get("status", "pending")
                    task_desc = t.get("task", "")
                    if status == "in_progress":
                        icon = "🔄"
                    elif status == "done":
                        icon = "✅"
                    else:  # pending
                        icon = "⏳"
                    # 工作流按实际写出的文件记下的进度（「已写：第2集；待写：第3–5集」）。
                    progress = str(t.get("progress") or "").strip()
                    if progress and status != "done":
                        task_desc = f"{task_desc}（{progress}）"
                    task_lines.append(f"{icon} {task_desc}")

                parts.extend([
                    "",
                    "## Current Task Board" if force_en else "## 当前任务计划板",
                    "",
                    "\n".join(task_lines),
                    "",
                    "**Rule:** Update the task board only when planning starts and when all tasks are done; "
                    "batch any intermediate status change into the same reply as other tool calls instead of a standalone call."
                    if force_en
                    else "**规则:** 任务计划板只在开始规划时和全部完成时调用 update_project(tasks=[...]) 更新；"
                    "中间状态变化与其他工具调用放在同一条回复里批量更新，不单独占一轮。",
                ])
            else:
                parts.extend([
                    "",
                    "## Current Task Board" if force_en else "## 当前任务计划板",
                    "",
                    "No active tasks" if force_en else "当前无活跃任务",
                ])

        return parts

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

    def _build_context_section(
        self,
        assembled_context: str,
        context_items: list[dict[str, Any]] | None,
        force_en: bool,
        world_truth: list[str] | None = None,
        world_surface: list[str] | None = None,
    ) -> list[str]:
        """Build world-model truth/surface context section (no legacy aliases)."""
        if world_truth is None or world_surface is None:
            _, world_truth, world_surface = self._extract_structured_context(
                context_items=context_items,
                force_en=force_en,
                raw_context_complete=self._raw_context_is_complete(assembled_context),
            )

        truth_items = world_truth or []
        surface_items = world_surface or []
        raw_context = self._cap_raw_context(assembled_context, _CONTEXT_RAW_CHAR_LIMIT)

        parts: list[str] = [
            "",
            "## World Model Visibility Contract"
            if force_en
            else "## 世界模型可见性合同",
            "",
            "Use world_model_truth as canonical facts; treat world_model_surface as scene-level cues."
            if force_en
            else "将 world_model_truth 视为权威事实；world_model_surface 仅作为场景线索。",
            "",
        ]

        if truth_items:
            parts.append("<world_model_truth>")
            parts.extend([f"- {entry}" for entry in truth_items])
            parts.append("</world_model_truth>")
            parts.append("")

        if surface_items:
            parts.append("<world_model_surface>")
            parts.extend([f"- {entry}" for entry in surface_items])
            parts.append("</world_model_surface>")
            parts.append("")

        if raw_context:
            parts.append("<project_context_raw>")
            parts.append(raw_context)
            parts.append("</project_context_raw>")
        return parts

    def _build_narrative_constraints_section(
        self,
        *,
        constraints: list[str],
        force_en: bool,
    ) -> list[str]:
        """Inject narrative constraints as an independent prompt block."""
        if not constraints:
            return []

        return [
            "",
            "## Narrative Constraints" if force_en else "## 叙事约束",
            "",
            "<narrative_constraints>",
            *[f"{idx}. {rule}" for idx, rule in enumerate(constraints, start=1)],
            "</narrative_constraints>",
        ]

    def _extract_structured_context(
        self,
        *,
        context_items: list[dict[str, Any]] | None,
        force_en: bool,
        raw_context_complete: bool = False,
    ) -> tuple[list[str], list[str], list[str]]:
        """拆出叙事约束与 world_model truth/surface 条目。

        raw_context_complete=True 表示 <project_context_raw> 未被兜底截断：
        此时未被预算截断的条目已在原始上下文里以 [全文] 出现，truth/surface 里只
        保留「标题 (id=…)」用于可见性分类，不再重复一份 220 字预览——
        重复的短预览会让模型以为文件被压缩过，转而 query_files 重读。
        """
        constraints: list[str] = []
        world_truth: list[str] = []
        world_surface: list[str] = []
        seen_constraints: set[str] = set()
        seen_truth: set[str] = set()
        seen_surface: set[str] = set()

        for raw_item in context_items or []:
            if not isinstance(raw_item, dict):
                continue
            item_type = str(raw_item.get("type") or "").strip().lower()
            priority = str(raw_item.get("priority") or "").strip().lower()
            metadata = extract_item_metadata(raw_item)
            inject_truth, inject_surface = resolve_world_model_channels(
                item_type=item_type,
                metadata=metadata,
            )
            if not inject_truth and not inject_surface:
                continue

            title = self._normalize_text(str(raw_item.get("title") or ""))
            full_in_raw = (
                raw_context_complete
                and bool(title)
                and not metadata.get("truncated")
                and not metadata.get("retrieved")
            )
            if full_in_raw:
                item_id = str(raw_item.get("id") or "").strip()
                has_file_id = bool(metadata.get("file_type")) and not metadata.get(
                    "library_material"
                )
                ref_suffix = (
                    " (full text in project context)" if force_en else "（全文见项目上下文）"
                )
                id_part = f" (id={item_id})" if has_file_id and item_id else ""
                item_entry = f"{title}{id_part}{ref_suffix}"
            else:
                content = self._normalize_text(str(raw_item.get("content") or ""))
                snippet = self._truncate_text(content, _CONTEXT_ITEM_TEXT_CHAR_LIMIT)
                item_entry = self._build_item_entry(
                    title=title,
                    snippet=snippet,
                    force_en=force_en,
                )

            if item_entry and inject_truth and item_entry not in seen_truth:
                seen_truth.add(item_entry)
                world_truth.append(item_entry)
            if item_entry and inject_surface and item_entry not in seen_surface:
                seen_surface.add(item_entry)
                world_surface.append(item_entry)

            if item_type in {"character", "lore"} and inject_truth:
                if priority in {"critical", "constraint"}:
                    rule = self._build_consistency_rule(
                        item_type=item_type,
                        title=title,
                        force_en=force_en,
                    )
                    if rule and rule not in seen_constraints:
                        seen_constraints.add(rule)
                        constraints.append(rule)
                continue

            if item_type == "quote" and inject_truth:
                quote_rule = (
                    "Do not contradict the user-quoted text."
                    if force_en
                    else "不得与用户引用文本冲突。"
                )
                if quote_rule not in seen_constraints:
                    seen_constraints.add(quote_rule)
                    constraints.append(quote_rule)
                continue

        return (
            constraints[:_CONTEXT_MAX_CONSTRAINTS],
            world_truth[:_CONTEXT_MAX_WORLD_KNOWLEDGE],
            world_surface[:_CONTEXT_MAX_WORKING_SET],
        )

    def _build_consistency_rule(self, *, item_type: str, title: str, force_en: bool) -> str:
        safe_title = title or ("this profile" if force_en else "该档案")
        if item_type == "character":
            return (
                f"Character consistency: keep behavior and voice aligned with {safe_title}."
                if force_en
                else f"角色一致性：{safe_title} 的行为、语气、动机需前后一致。"
            )
        return (
            f"World consistency: keep rules in {safe_title} unchanged unless user requests."
            if force_en
            else f"设定一致性：遵守 {safe_title} 中的世界规则，除非用户明确要求修改。"
        )

    def _build_item_entry(self, *, title: str, snippet: str, force_en: bool) -> str:
        if title and snippet:
            return f"{title}: {snippet}" if force_en else f"{title}：{snippet}"
        return title or snippet

    def _normalize_text(self, text: str) -> str:
        """压成单行：只用于 world_model 列表里的标题/节选，不能用于原始上下文。"""
        return " ".join((text or "").split()).strip()

    def _truncate_text(self, text: str, max_chars: int) -> str:
        """把节选压成单行并裁到 max_chars，裁过时以「…（节选）」显式收尾。"""
        normalized = self._normalize_text(text)
        if max_chars <= 0 or len(normalized) <= max_chars:
            return normalized

        clipped = normalized[:max_chars]
        for sep in ("。", ".", "！", "!", "？", "?", "\n", "；", ";", "，", ",", " "):
            idx = clipped.rfind(sep)
            if idx > max_chars * 0.6:
                clipped = clipped[: idx + 1]
                break
        return clipped.rstrip() + _CONTEXT_PREVIEW_SUFFIX

    @staticmethod
    def _raw_context_is_complete(assembled_context: str | None) -> bool:
        """原始项目上下文能否原样放进本轮上下文（未触发兜底截断）。"""
        text = (assembled_context or "").strip()
        return bool(text) and len(text) <= _CONTEXT_RAW_CHAR_LIMIT

    @staticmethod
    def _cap_raw_context(text: str, max_chars: int) -> str:
        """原始项目上下文只做 strip + 兜底封顶，保留换行与段落。

        历史缺陷：这里曾复用 _truncate_text，先 `" ".join(text.split())` 把整段
        上下文压成一行，章节正文、大纲层级全部糊在一起，模型看到的像是「压缩版」，
        于是坚持用 query_files 重读全文。
        """
        stripped = (text or "").strip()
        if max_chars <= 0 or len(stripped) <= max_chars:
            return stripped

        clipped = stripped[:max_chars]
        # 尽量在段落边界处截断
        idx = clipped.rfind("\n")
        if idx > max_chars * 0.8:
            clipped = clipped[:idx]
        return clipped.rstrip() + "\n" + _CONTEXT_RAW_TRUNCATION_NOTICE

    def _build_skill_catalog_section(
        self, skill_catalog: str, _force_en: bool
    ) -> list[str]:
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
        from sqlalchemy import func, or_

        from models import File

        def _norm(text: str | None) -> str:
            return "".join((text or "").split()).strip().lower()

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

        root_ids = [f.id for f in root_folders if getattr(f, "id", None)]
        child_counts: dict[str, int] = {}
        child_type_counts: dict[str, dict[str, int]] = {}
        if root_ids:
            typed_rows = session.exec(
                select(File.parent_id, File.file_type, func.count(File.id))
                .where(
                    File.project_id == self.project_id,
                    File.is_deleted.is_(False),
                    File.parent_id.in_(root_ids),
                )
                .group_by(File.parent_id, File.file_type)
            ).all()
            for parent_id, file_type, count in typed_rows:
                if not parent_id:
                    continue
                parent_key = str(parent_id)
                child_counts[parent_key] = child_counts.get(parent_key, 0) + int(count or 0)
                if not file_type:
                    continue
                bucket = child_type_counts.setdefault(parent_key, {})
                bucket[str(file_type)] = int(count or 0)

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

            # 统一打分：**先看这个根目录是不是本类型的规范目录**（id 命中模板
            # 确定性 id，或标题命中别名），子文件类型计数只用来做平局裁决。
            #
            # 历史缺陷：类型计数原本是一票否决式的第一优先级，且
            # `typed_count <= 0` 的目录直接被 continue 掉——只要任意别的根目录里
            # 躺着 1 个该类型的散落文件，空的规范目录（计数 0）就必败，
            # 系统提示里的 {character_folder_id} 等占位符被永久劫持，
            # AI 一直把新文件建到那个错误目录里；错误目录的计数只增不减，
            # 形成自我强化闭环（用户手动把文件拖回去也没用，只要错误目录里
            # 还剩一个同类型文件，下一轮又会建回去）。
            # 现在规范目录即便是空的，也稳压只含杂散文件的非规范目录。
            best: File | None = None
            best_score: tuple[int, int, int, int, str] | None = None
            for folder in root_folders:
                folder_id = str(folder.id)
                is_deterministic = folder_id == deterministic_id
                # 确定性 id 与标题别名同级：两者都命中时由下面的子文件计数裁决，
                # 从而保留「模板目录空着、旧项目的同名目录才是真正在用的那个」的行为。
                canonical_match = 1 if (is_deterministic or _title_matches(folder)) else 0
                typed_count = (
                    child_type_counts.get(folder_id, {}).get(expected_child_type, 0)
                    if expected_child_type
                    else 0
                )
                total_children = child_counts.get(folder_id, 0)
                score = (
                    canonical_match,
                    typed_count,
                    total_children,
                    1 if is_deterministic else 0,
                    folder_id,
                )
                if best_score is None or score > best_score:
                    best_score = score
                    best = folder

            # 既不是规范目录、也不含任何该类型的子文件 => 没有可信信号，
            # 回退到模板的确定性 id（该目录可能尚未创建）。
            # 注意这里保留了「标题完全不可识别、但确实堆着该类型文件」的旧项目
            # 兜底路径：canonical_match=0 但 typed_count>0 时仍然认它。
            if best is None or (best_score[0] == 0 and best_score[1] == 0):
                return deterministic_id

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
                    resolved_child_count=child_counts.get(resolved_id, 0),
                    resolved_typed_child_count=best_score[1],
                    resolved_by_title_only=best_score[0] == 1 and best_score[3] == 0,
                )

            return resolved_id

        folder_ids: dict[str, str] = {}
        for key, suffix, aliases in folder_specs:
            folder_ids[key] = _pick_folder_id(key, suffix, aliases)

        return folder_ids
