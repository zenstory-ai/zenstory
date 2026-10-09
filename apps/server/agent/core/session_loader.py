"""
Session loader for the agent module.

Encapsulates context assembly and chat session loading logic,
reducing complexity in the main service module.
"""

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import desc, or_
from sqlmodel import Session, and_, select

from agent.utils.token_utils import estimate_message_tokens
from config.agent_runtime import (
    AGENT_CHAT_HISTORY_TOKEN_BUDGET,
    AGENT_CONTEXT_TOKEN_BUDGET,
    AGENT_WORKING_SET_MAX_CHARS,
    AGENT_WORKING_SET_MAX_FILES,
)
from database import create_session
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# 跨轮工作集取最近几条 assistant 回复里的文件读写记录
WORKING_SET_RECENT_TURNS = 2

WORKING_SET_HEADER = (
    "以下为上一轮读取/修改过的文件当前全文（完整，非预览，截至本轮开始）；"
    "无需再次 query_files："
)
WORKING_SET_USER_MESSAGE_LABEL = "【本轮用户消息】"

# 工具名 → 面包屑里的动作（读取类只认按 id 精确读取的 query_files）
_BREADCRUMB_VERBS_ZH = {
    "create_file": "已创建文件",
    "edit_file": "已编辑文件",
    "delete_file": "已删除文件",
    "query_files": "已读取文件",
}
_BREADCRUMB_VERBS_EN = {
    "create_file": "Created file",
    "edit_file": "Edited file",
    "delete_file": "Deleted file",
    "query_files": "Read file",
}
_FAILED_TOOL_STATUSES = {"error", "failed", "failure"}


@dataclass
class SessionData:
    """Data loaded for a chat session."""

    chat_session: Any | None = None
    session_id: str | None = None
    history_messages: list[dict[str, Any]] = field(default_factory=list)
    context_data: Any | None = None
    # 最近两轮 assistant 读/写过的文件（新 → 旧，已去重），每项 {file_id, title, action}
    working_set_refs: list[dict[str, Any]] = field(default_factory=list)
    # 按当前库内容渲染的跨轮工作集纯文本块；没有可注入内容时为空串
    working_set_context: str = ""


class SessionLoader:
    """
    Loader for chat session and context data.

    Handles:
    - Context assembly using ContextAssembler
    - Chat session loading from database
    - Message history retrieval
    """

    def __init__(
        self,
        project_id: str,
        user_id: str | None,
    ):
        """
        Initialize session loader.

        Args:
            project_id: Current project ID
            user_id: Current user ID
        """
        self.project_id = project_id
        self.user_id = user_id

    def assemble_context(
        self,
        session: Session,
        context_assembler,
        query: str,
        focus_file_id: str | None = None,
        attached_file_ids: list[str] | None = None,
        attached_library_materials: list[dict[str, int]] | None = None,
        text_quotes: list[dict[str, str]] | None = None,
        max_tokens: int = AGENT_CONTEXT_TOKEN_BUDGET,
    ):
        """
        Assemble context using the context assembler.

        Args:
            session: Database session
            context_assembler: ContextAssembler instance
            query: User query for context retrieval
            focus_file_id: Currently focused file ID
            attached_file_ids: List of attached file IDs
            attached_library_materials: List of library material references
            text_quotes: List of user-selected text quotes
            max_tokens: Maximum tokens for context

        Returns:
            Assembled context data
        """
        started = time.perf_counter()
        threshold_raw = (os.getenv("AGENT_CONTEXT_ASSEMBLY_LOG_THRESHOLD_MS") or "").strip()
        try:
            threshold_ms = int(threshold_raw) if threshold_raw else 200
        except ValueError:
            threshold_ms = 200

        force_log = (os.getenv("AGENT_CONTEXT_ASSEMBLY_LOG_ALWAYS") or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "y",
            "on",
        }

        log_with_context(
            logger,
            logging.INFO if force_log else logging.DEBUG,
            "Starting context assembly",
            project_id=self.project_id,
            user_id=self.user_id,
            focus_file_id=focus_file_id,
            attached_file_count=len(attached_file_ids) if attached_file_ids else 0,
            attached_library_count=len(attached_library_materials) if attached_library_materials else 0,
        )

        context_data = context_assembler.assemble(
            session=session,
            project_id=self.project_id,
            user_id=self.user_id,
            query=query,
            focus_file_id=focus_file_id,
            attached_file_ids=attached_file_ids,
            attached_library_materials=attached_library_materials,
            text_quotes=text_quotes,
            max_tokens=max_tokens,
            include_characters=True,
            include_lores=True,
        )

        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        log_level = (
            logging.INFO
            if force_log or duration_ms >= threshold_ms
            else logging.DEBUG
        )
        log_with_context(
            logger,
            log_level,
            "Context assembly completed",
            project_id=self.project_id,
            user_id=self.user_id,
            item_count=len(context_data.items),
            token_estimate=context_data.token_estimate,
            duration_ms=duration_ms,
            threshold_ms=threshold_ms,
            forced=force_log,
        )

        return context_data

    def load_chat_session(self, session: Session) -> SessionData:
        """
        Load chat session and message history.

        Args:
            session: Database session

        Returns:
            SessionData with chat session and history
        """
        from models import ChatSession, Project, User

        result = SessionData()

        if not self.user_id:
            return result

        # Check if project exists and is not deleted
        project = session.get(Project, self.project_id)
        if not project or project.is_deleted:
            return result

        # Defense in depth: verify user owns the project (superuser can access all projects)
        user = session.get(User, self.user_id)
        if not user:
            log_with_context(
                logger,
                30,  # WARNING level
                "Session load blocked: user not found",
                project_id=self.project_id,
                user_id=self.user_id,
            )
            return result

        if not user.is_superuser and project.owner_id != self.user_id:
            log_with_context(
                logger,
                30,  # WARNING level
                "Session load blocked: user does not own project",
                project_id=self.project_id,
                user_id=self.user_id,
                owner_id=project.owner_id,
            )
            return result
        if user.is_superuser and project.owner_id != self.user_id:
            log_with_context(
                logger,
                20,  # INFO
                "Session load allowed for superuser across project ownership boundary",
                project_id=self.project_id,
                user_id=self.user_id,
                owner_id=project.owner_id,
            )

        # Load active chat session deterministically (newest first)
        stmt = (
            select(ChatSession)
            .where(
                and_(
                    ChatSession.project_id == self.project_id,
                    ChatSession.user_id == self.user_id,
                    ChatSession.is_active,
                )
            )
            .order_by(
                desc(ChatSession.updated_at),
                desc(ChatSession.created_at),
                desc(ChatSession.id),
            )
        )
        active_sessions = session.exec(stmt).all()
        chat_session = active_sessions[0] if active_sessions else None

        # Repair stale multi-active states defensively.
        if len(active_sessions) > 1 and chat_session is not None:
            stale_count = 0
            for stale in active_sessions[1:]:
                if stale.is_active:
                    stale.is_active = False
                    session.add(stale)
                    stale_count += 1
            if stale_count > 0:
                session.commit()
                session.refresh(chat_session)
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Session loader detected multiple active chat sessions; stale sessions deactivated",
                    project_id=self.project_id,
                    user_id=self.user_id,
                    kept_session_id=chat_session.id,
                    stale_count=stale_count,
                )

        if not chat_session:
            return result

        result.chat_session = chat_session
        result.session_id = chat_session.id

        # Load recent user/assistant messages under token budget.
        #
        # IMPORTANT: Long sessions may contain thousands of chat_message rows.
        # Loading them all on every request can be slow and can cause the writing
        # assistant to "hang" on the frontend while context is assembled.
        #
        # We therefore paginate from newest -> oldest and stop as soon as the
        # token budget window is filled, then reverse to preserve chronology.
        result.history_messages = self._load_history_window_under_token_budget(
            session=session,
            chat_session_id=chat_session.id,
            token_budget=AGENT_CHAT_HISTORY_TOKEN_BUDGET,
        )

        log_with_context(
            logger,
            20,
            "Chat history loaded",
            project_id=self.project_id,
            user_id=self.user_id,
            session_id=chat_session.id,
            total_history_message_count=getattr(chat_session, "message_count", None),
            history_message_count=len(result.history_messages),
            history_token_budget=AGENT_CHAT_HISTORY_TOKEN_BUDGET,
        )

        # 跨轮工作集只在上面的项目归属校验通过后才收集
        result.working_set_refs = self._load_recent_working_set_refs(
            session=session,
            chat_session_id=chat_session.id,
        )

        return result

    # ------------------------------------------------------------------
    # 跨轮工作集
    # ------------------------------------------------------------------
    #
    # 生产事故：runner 回放历史时丢弃工具结果，面包屑又只记创建/编辑/删除，
    # 新一轮里模型完全不知道上一轮读过什么——「继续」从零开始，同一份卷纲
    # 每轮重读 4 次以上。这里从最近两轮 assistant 持久化的 tool_calls 里取出
    # 读过（query_files(id=…)，含 parallel_execute 的 query_files 子任务）或
    # 写过的文件，**按当前库内容**重新加载全文，作为纯文本块注入本轮。
    # 仍然是纯文本：不回放原始 tool_use/tool_result 块（孤立 tool_call_id 风险，
    # 见 openai_agents/runner.py:extract_text_from_message_content）。

    def _load_recent_working_set_refs(
        self,
        *,
        session: Session,
        chat_session_id: str,
    ) -> list[dict[str, Any]]:
        """取最近 WORKING_SET_RECENT_TURNS 条 assistant 回复里读/写过的文件（新 → 旧）。"""
        from models import ChatMessage

        rows = session.exec(
            select(ChatMessage)
            .where(
                and_(
                    ChatMessage.session_id == chat_session_id,
                    ChatMessage.role == "assistant",
                )
            )
            .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
            .limit(WORKING_SET_RECENT_TURNS)
        ).all()

        refs: list[dict[str, Any]] = []
        seen: set[str] = set()
        deleted: set[str] = set()
        for row in rows:  # 新 → 旧
            actions = self._extract_file_actions(getattr(row, "tool_calls", None))
            # 同一轮里先找删除：被删掉的文件不再进工作集
            for action in actions:
                if action["name"] == "delete_file" and action["file_id"]:
                    deleted.add(action["file_id"])
            for action in actions:
                file_id = action["file_id"]
                if not file_id or action["name"] == "delete_file":
                    continue
                if file_id in deleted or file_id in seen:
                    continue
                seen.add(file_id)
                refs.append(
                    {
                        "file_id": file_id,
                        "title": action["title"],
                        "action": "read" if action["name"] == "query_files" else "write",
                    }
                )
        return refs

    def attach_working_set(self, session: Session, result: SessionData) -> None:
        """按当前库内容渲染跨轮工作集，写入 result.working_set_context。

        已在组装上下文里以 [全文] 出现的文件不再重复附全文，只列一行指向它。
        """
        refs = result.working_set_refs
        if not refs or not self.user_id:
            result.working_set_context = ""
            return
        try:
            result.working_set_context = self._build_working_set_context(
                session=session,
                refs=refs,
                full_in_context_ids=self._full_text_context_ids(result.context_data),
            )
        except Exception as exc:  # 工作集是增益信息，失败不影响本轮请求
            log_with_context(
                logger,
                30,  # WARNING
                "Working set build failed",
                project_id=self.project_id,
                user_id=self.user_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            result.working_set_context = ""

    @staticmethod
    def _full_text_context_ids(context_data: Any) -> set[str]:
        """组装上下文里未被截断（渲染为 [全文]）的项目文件 id。"""
        from agent.core.message_manager import MessageManager

        if context_data is None:
            return set()
        if not MessageManager._raw_context_is_complete(getattr(context_data, "context", "")):
            return set()

        ids: set[str] = set()
        for item in getattr(context_data, "items", None) or []:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if (
                not metadata.get("file_type")
                or metadata.get("truncated")
                or metadata.get("retrieved")
                or metadata.get("library_material")
            ):
                continue
            file_id = str(item.get("id") or "").strip()
            if file_id:
                ids.add(file_id)
        return ids

    def _build_working_set_context(
        self,
        *,
        session: Session,
        refs: list[dict[str, Any]],
        full_in_context_ids: set[str],
        max_files: int | None = None,
        max_chars: int | None = None,
    ) -> str:
        """渲染工作集文本块：最多 max_files 份全文、合计不超过 max_chars 字。"""
        from models import File

        max_files = AGENT_WORKING_SET_MAX_FILES if max_files is None else max_files
        max_chars = AGENT_WORKING_SET_MAX_CHARS if max_chars is None else max_chars

        ids = [ref["file_id"] for ref in refs]
        files = session.exec(
            select(File).where(
                File.id.in_(ids),
                File.project_id == self.project_id,
                File.is_deleted.is_(False),
            )
        ).all()
        files_by_id = {str(f.id): f for f in files if getattr(f, "file_type", None) != "folder"}

        full_blocks: list[str] = []
        omitted: list[str] = []
        in_context: list[str] = []
        used_chars = 0
        for ref in refs:
            file = files_by_id.get(ref["file_id"])
            if file is None:
                # 已删除 / 不属于本项目 / 文件夹：不注入
                continue
            title = str(file.title or ref.get("title") or "").strip() or "（未命名）"
            label = f"《{title}》 (id={file.id})"
            if str(file.id) in full_in_context_ids:
                in_context.append(f"- {label}")
                continue

            content = file.content or ""
            if len(full_blocks) < max_files and used_chars + len(content) <= max_chars:
                action = "上一轮已修改" if ref["action"] == "write" else "上一轮已读取"
                full_blocks.append(
                    f"### {label} [全文，{len(content)} 字；{action}]\n{content}".rstrip()
                )
                used_chars += len(content)
            else:
                omitted.append(f"- {label}（{len(content)} 字）")

        if not full_blocks and not omitted and not in_context:
            return ""

        lines = ["<previous_turn_working_set>", WORKING_SET_HEADER, ""]
        for block in full_blocks:
            lines.extend([block, ""])
        if in_context:
            lines.append("已在系统提示的项目上下文中标注[全文]（直接使用，不必重读）：")
            lines.extend(in_context)
            lines.append("")
        if omitted:
            lines.append("未附全文（超出工作集预算；确需全文时再用 query_files(id=…) 读取一次）：")
            lines.extend(omitted)
            lines.append("")
        lines.append("</previous_turn_working_set>")
        return "\n".join(lines)

    @staticmethod
    def attach_working_set_to_user_content(user_content: str, working_set_context: str) -> str:
        """把工作集块放在本轮用户消息之前（同一条 user 消息里）。

        放在这里而不是系统提示里：系统提示与历史消息保持不变，前缀缓存可以
        一直命中到上一轮为止；工作集每轮都会变，只能放在最末尾。也不单独插一条
        user 消息，避免连续两条 user 消息。
        """
        if not working_set_context:
            return user_content
        return f"{working_set_context}\n\n{WORKING_SET_USER_MESSAGE_LABEL}\n{user_content}"

    def _load_session_and_context_sync(
        self,
        context_assembler,
        query: str,
        focus_file_id: str | None = None,
        attached_file_ids: list[str] | None = None,
        attached_library_materials: list[dict[str, int]] | None = None,
        text_quotes: list[dict[str, str]] | None = None,
        max_tokens: int = AGENT_CONTEXT_TOKEN_BUDGET,
    ) -> SessionData:
        """
        Load session and context using a fresh sync DB session.

        This method is intended to run in a worker thread so read-heavy sync ORM
        work does not block the async request event loop.
        """
        with create_session() as read_session:
            result = self.load_chat_session(read_session)
            result.context_data = self.assemble_context(
                read_session,
                context_assembler,
                query,
                focus_file_id,
                attached_file_ids,
                attached_library_materials,
                text_quotes,
                max_tokens,
            )
            self.attach_working_set(read_session, result)
            return result

    def _should_offload_session_work(self, session: Session) -> bool:
        """Only offload when running against PostgreSQL production-style sessions."""
        bind = session.get_bind() if hasattr(session, "get_bind") else None
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        return dialect_name == "postgresql"

    def _load_history_window_under_token_budget(
        self,
        *,
        session: Session,
        chat_session_id: str,
        token_budget: int,
        page_size: int = 200,
    ) -> list[dict[str, Any]]:
        """
        Load a newest-first sliding window under token budget without reading all rows.

        This avoids O(total_messages) memory/time behavior for long chat sessions.
        """
        from models import ChatMessage

        # Keep the newest single message even if budget is non-positive.
        if token_budget <= 0:
            msg_stmt = (
                select(ChatMessage)
                .where(
                    and_(
                        ChatMessage.session_id == chat_session_id,
                        ChatMessage.role.in_(("user", "assistant")),
                    )
                )
                .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
                .limit(1)
            )
            msg = session.exec(msg_stmt).first()
            if not msg:
                return []
            return [self._format_chat_message_for_history(msg)]

        selected_reversed: list[dict[str, Any]] = []
        used_tokens = 0
        cursor_created_at = None
        cursor_id = None

        while True:
            msg_stmt = select(ChatMessage).where(
                and_(
                    ChatMessage.session_id == chat_session_id,
                    ChatMessage.role.in_(("user", "assistant")),
                )
            )

            if cursor_created_at is not None and cursor_id is not None:
                msg_stmt = msg_stmt.where(
                    or_(
                        ChatMessage.created_at < cursor_created_at,
                        and_(
                            ChatMessage.created_at == cursor_created_at,
                            ChatMessage.id < cursor_id,
                        ),
                    )
                )

            msg_stmt = msg_stmt.order_by(desc(ChatMessage.created_at), desc(ChatMessage.id)).limit(
                max(1, int(page_size or 200))
            )
            page = session.exec(msg_stmt).all()
            if not page:
                break

            stop = False
            for msg in page:
                msg_data = self._format_chat_message_for_history(msg)
                msg_tokens = max(1, estimate_message_tokens(msg_data))

                if selected_reversed and used_tokens + msg_tokens > token_budget:
                    stop = True
                    break

                selected_reversed.append(msg_data)
                used_tokens += msg_tokens

                if used_tokens >= token_budget:
                    stop = True
                    break

            if stop:
                break

            last = page[-1]
            cursor_created_at = last.created_at
            cursor_id = last.id

            # Safety: stop if we somehow keep fetching but never make progress.
            if len(page) == 0:
                break

        return list(reversed(selected_reversed))

    def _format_chat_message_for_history(self, msg: Any) -> dict[str, Any]:
        """Convert ChatMessage ORM row to dict for agent history + token math."""
        msg_data: dict[str, Any] = {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
        }

        # Include persisted assistant metadata for token estimation
        #
        # 顺序至关重要：状态卡的正文合成必须发生在下面的 reasoning 分支**之前**。
        # reasoning 分支会把 content 从 str 变成内容块 list；一旦先执行它，
        # 这里"正文是否为空"的判断就会看到一个非空 list 而恒假，于是
        # request_clarification / iteration_exhausted 这类「只有状态卡、没有正文」
        # 的轮次拿不到合成文本。而回放侧（openai_agents.runner）会丢弃 thinking 块，
        # 该 assistant 消息随即因文本为空被整条剔除——AI 忘了自己刚问过什么，
        # 表现为反复追问或把用户的回答当成无上下文的孤立指令。
        # 判空也统一走 _extract_content_text，即便将来顺序再被调整也不会退化。
        if msg.role == "assistant" and getattr(msg, "message_metadata", None):
            try:
                metadata = json.loads(msg.message_metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}

            if isinstance(metadata, dict):
                stop_reason = metadata.get("stop_reason")
                usage = metadata.get("usage")
                status_cards = metadata.get("status_cards")
                if isinstance(stop_reason, str) and stop_reason:
                    msg_data["stop_reason"] = stop_reason
                if isinstance(usage, dict):
                    msg_data["usage"] = usage
                # 跨轮路由延续（graph/router.py）：上一轮落库的路由、状态卡里的
                # lastAgent、是否以澄清卡收尾。只供路由判断，不进回放文本。
                routing = metadata.get("routing")
                if isinstance(routing, dict):
                    msg_data["routing"] = routing
                if isinstance(status_cards, list):
                    msg_data.update(self._status_card_routing_hints(status_cards))
                    synthesized_content = self._build_status_cards_history_content(status_cards)
                    if synthesized_content and not self._extract_content_text(
                        msg_data.get("content")
                    ).strip():
                        self._append_text_to_content(msg_data, synthesized_content)

        # Include reasoning_content for assistant messages
        # The workflow message format keeps thinking inside the content array, not top-level
        if msg.role == "assistant" and getattr(msg, "reasoning_content", None):
            reasoning = msg.reasoning_content
            # Reconstruct content as array with thinking block first
            if isinstance(msg_data.get("content"), str):
                parts: list[dict[str, Any]] = [
                    {"type": "thinking", "thinking": reasoning},
                ]
                if msg_data["content"]:
                    parts.append({"type": "text", "text": msg_data["content"]})
                msg_data["content"] = parts

        # Tool-turn breadcrumbs (cross-request tool memory).
        #
        # ChatMessage.tool_calls persists what the assistant DID last turn
        # (file create/edit/delete, exact-id query_files reads), but on a new
        # request the agent otherwise only sees the prose reply — it has no
        # record that it created/edited/read a file. We synthesize a COMPACT
        # assistant TEXT breadcrumb here so that memory survives across requests.
        # 文件的当前全文不放在这里（会被历史窗口按 token 预算裁掉），而是由
        # attach_working_set 另行渲染、拼到本轮用户消息前。
        #
        # CRITICAL: this is plain TEXT, never raw tool_use/tool_result blocks.
        # Re-emitting structured tool blocks from a prior turn risks orphaned
        # tool_call_id errors in the SDK (see the comment in
        # openai_agents/runner.py:extract_text_from_message_content). Mirrors the
        # text-synthesis pattern used for status cards above.
        if msg.role == "assistant" and getattr(msg, "tool_calls", None):
            breadcrumb = self._build_tool_calls_history_content(msg.tool_calls)
            if breadcrumb:
                self._append_text_to_content(msg_data, breadcrumb)

        return msg_data

    @staticmethod
    def _extract_content_text(content: Any) -> str:
        """提取内容里真正会被回放给模型的纯文本。

        content 既可能是 str，也可能是
        `[{"type": "thinking", ...}, {"type": "text", ...}]` 这样的内容块列表。
        回放侧（openai_agents.runner.extract_text_from_message_content）只取 text 块、
        丢弃 thinking/tool_use/tool_result 块，因此判断「这条消息对模型而言是否为空」
        必须用同一套规则：`str(content)` 会把 thinking 块的 repr 当成正文，
        使「只有状态卡」的轮次被误判为有正文。
        """
        if isinstance(content, str):
            return content

        if not isinstance(content, list):
            return "" if content is None else str(content)

        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                if block.strip():
                    parts.append(block)
                continue
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    parts.append(text)

        return "\n".join(parts)

    def _append_text_to_content(
        self,
        msg_data: dict[str, Any],
        text: str,
    ) -> None:
        """把一段合成文本（工具面包屑 / 状态卡摘要）追加为 assistant 的纯文本内容。

        content 已是内容块 list 时追加一个 text 块，否则按字符串拼接；
        原本为空则直接替换。两条路径都保证文本能被回放侧提取到。
        """
        content = msg_data.get("content")

        if isinstance(content, list):
            # Reasoning/structured content already present — append a text block.
            content.append({"type": "text", "text": text})
            return

        existing_text = str(content or "").strip()
        if existing_text:
            msg_data["content"] = f"{content}\n\n{text}"
        else:
            msg_data["content"] = text

    @staticmethod
    def _parse_json_value(value: Any) -> Any:
        """tool_calls / arguments 可能以 JSON 字符串持久化，统一解析成 Python 值。"""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return None
        return value

    @staticmethod
    def _find_title_for_id(payload: Any, file_id: str, depth: int = 0) -> str | None:
        """在 query_files 等工具结果里找到 id 对应的标题（结果可能是 dict / list / 嵌套 data）。"""
        if depth > 4 or payload is None:
            return None
        if isinstance(payload, dict):
            for key in ("id", "file_id", "fileId"):
                if str(payload.get(key) or "").strip() == file_id:
                    title = payload.get("title")
                    if isinstance(title, str) and title.strip():
                        return title.strip()
            for value in payload.values():
                if isinstance(value, dict | list):
                    found = SessionLoader._find_title_for_id(value, file_id, depth + 1)
                    if found:
                        return found
            return None
        if isinstance(payload, list):
            for value in payload:
                found = SessionLoader._find_title_for_id(value, file_id, depth + 1)
                if found:
                    return found
        return None

    @staticmethod
    def _first_id_in(payload: Any, keys: tuple[str, ...] = ("id", "file_id", "fileId")) -> str | None:
        """从 dict（或其 data 字段）里取第一个非空 id。"""
        for candidate in (payload, payload.get("data") if isinstance(payload, dict) else None):
            if not isinstance(candidate, dict):
                continue
            for key in keys:
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _extract_file_actions(self, tool_calls_raw: Any) -> list[dict[str, Any]]:
        """把持久化的 tool_calls 归一成文件动作列表（按调用顺序）。

        每项 {name, file_id, title}，name ∈ create_file/edit_file/delete_file/query_files。
        - 失败的调用一律忽略；
        - query_files 只认按 id 精确读取（关键词搜索只是预览，不算读过全文）；
        - parallel_execute 展开其中的 query_files / edit_file / delete_file /
          write_chapter 子任务（write_chapter 视为创建）。
        """
        tool_calls = self._parse_json_value(tool_calls_raw)
        if not isinstance(tool_calls, list):
            return []

        actions: list[dict[str, Any]] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue

            name = str(tc.get("name") or "").strip()
            status = str(tc.get("status") or "").strip().lower()
            if status in _FAILED_TOOL_STATUSES or tc.get("error"):
                continue

            if name in {"create_file", "edit_file", "delete_file"}:
                actions.append(
                    {
                        "name": name,
                        "file_id": self._extract_breadcrumb_file_id(tc),
                        "title": self._extract_breadcrumb_title(tc),
                    }
                )
                continue

            if name == "query_files":
                args = self._parse_json_value(tc.get("arguments"))
                file_id = str(args.get("id") or "").strip() if isinstance(args, dict) else ""
                if file_id:
                    actions.append(
                        {
                            "name": "query_files",
                            "file_id": file_id,
                            "title": self._find_title_for_id(tc.get("result"), file_id),
                        }
                    )
                continue

            if name == "parallel_execute":
                actions.extend(self._extract_parallel_file_actions(tc))

        return actions

    def _extract_parallel_file_actions(self, tc: dict[str, Any]) -> list[dict[str, Any]]:
        """展开 parallel_execute 里与文件相关的子任务。"""
        args = self._parse_json_value(tc.get("arguments"))
        tasks = args.get("tasks") if isinstance(args, dict) else None
        if not isinstance(tasks, list):
            return []

        result = self._parse_json_value(tc.get("result"))
        task_results = result.get("tasks") if isinstance(result, dict) else None
        if not isinstance(task_results, list):
            task_results = []

        actions: list[dict[str, Any]] = []
        # 落库的 arguments 是模型发出的原始子任务；读取守卫会把被拦下的
        # query_files 子任务从实际执行的列表里剔除，result.tasks 因此比 arguments
        # 短，按下标配对会把后面子任务的结果（标题、失败状态）安到前一个子任务上。
        # 用游标顺序配对：类型一致才配对；还有被剔除的名额时，结果里找不到该文件
        # id 的 query_files 视为被拦下。配不上的子任务按「没有结果」处理。
        dropped_reads = max(0, len(tasks) - len(task_results))
        cursor = 0
        for task in tasks:
            if not isinstance(task, dict):
                continue
            task_type = str(task.get("type") or "").strip()
            params = self._parse_json_value(task.get("params"))
            params = params if isinstance(params, dict) else {}

            candidate = task_results[cursor] if cursor < len(task_results) else None
            read_id = str(params.get("id") or "").strip() if task_type == "query_files" else ""
            if dropped_reads and read_id and not self._result_mentions_id(candidate, read_id):
                # 被守卫拦下、没有执行的重复读取：不记面包屑（之前的读取已经记过）。
                dropped_reads -= 1
                continue
            task_result: dict[str, Any] | None = None
            if isinstance(candidate, dict) and str(candidate.get("type") or "").strip() == task_type:
                task_result = candidate
                cursor += 1
            if isinstance(task_result, dict):
                task_status = str(task_result.get("status") or "").strip().lower()
                if task_status in _FAILED_TOOL_STATUSES or task_result.get("error"):
                    continue
            task_payload = task_result.get("result") if isinstance(task_result, dict) else None

            if task_type == "query_files":
                file_id = str(params.get("id") or "").strip()
                if file_id:
                    actions.append(
                        {
                            "name": "query_files",
                            "file_id": file_id,
                            "title": self._find_title_for_id(task_payload, file_id),
                        }
                    )
            elif task_type in {"edit_file", "delete_file"}:
                file_id = self._first_id_in(params) or self._first_id_in(task_payload)
                if file_id:
                    actions.append(
                        {
                            "name": task_type,
                            "file_id": file_id,
                            "title": self._find_title_for_id(task_payload, file_id),
                        }
                    )
            elif task_type == "write_chapter":
                file_id = self._first_id_in(task_payload)
                title = params.get("title") if isinstance(params.get("title"), str) else None
                if file_id or title:
                    actions.append({"name": "create_file", "file_id": file_id, "title": title})
        return actions

    @staticmethod
    def _result_mentions_id(task_result: Any, file_id: str) -> bool:
        """parallel 子任务结果里是否出现了这个文件 id（结果被截断时也能在预览里找到）。"""
        if not isinstance(task_result, dict) or not file_id:
            return False
        try:
            serialized = json.dumps(task_result.get("result"), ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            return False
        return file_id in serialized

    def _build_tool_calls_history_content(self, tool_calls_raw: Any) -> str:
        """
        Synthesize a compact text breadcrumb from persisted ChatMessage.tool_calls.

        Summarizes file create/edit/delete operations and exact-id reads by
        title + id so the agent remembers what it did and read on prior turns.
        Tool arguments and full results are intentionally NOT dumped — only a
        one-line summary per file operation (duplicates collapsed).
        Returns an empty string when there is nothing worth recording.
        """
        force_en = (os.getenv("AGENT_HISTORY_BREADCRUMB_LANG") or "").strip().lower().startswith("en")

        lines: list[str] = []
        seen: set[str] = set()
        for action in self._extract_file_actions(tool_calls_raw):
            line = self._format_breadcrumb_line(
                action["name"], action["title"], action["file_id"], force_en=force_en
            )
            if line and line not in seen:
                seen.add(line)
                lines.append(line)

        if not lines:
            return ""

        header = "[Previous tool actions]" if force_en else "[此前的工具操作]"
        return header + "\n" + "\n".join(lines)

    @staticmethod
    def _extract_breadcrumb_file_id(tc: dict[str, Any]) -> str | None:
        """Resolve the file id from a persisted tool-call record (result first, then args)."""
        result = tc.get("result")
        if isinstance(result, dict):
            for key in ("id", "file_id", "fileId"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        args = tc.get("arguments")
        if isinstance(args, dict):
            for key in ("id", "file_id", "fileId"):
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    @staticmethod
    def _extract_breadcrumb_title(tc: dict[str, Any]) -> str | None:
        """Resolve a human-readable file title from a persisted tool-call record."""
        result = tc.get("result")
        if isinstance(result, dict):
            value = result.get("title")
            if isinstance(value, str) and value.strip():
                return value.strip()

        args = tc.get("arguments")
        if isinstance(args, dict):
            value = args.get("title")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _format_breadcrumb_line(
        name: str,
        title: str | None,
        file_id: str | None,
        *,
        force_en: bool,
    ) -> str:
        """Format a single compact breadcrumb line for a file operation."""
        title_part = f"《{title}》" if title else ("(untitled)" if force_en else "（未命名）")
        id_part = f" (id={file_id})" if file_id else ""

        verbs = _BREADCRUMB_VERBS_EN if force_en else _BREADCRUMB_VERBS_ZH
        verb = verbs.get(name)
        if not verb:
            return ""
        return f"- {verb} {title_part}{id_part}"

    @staticmethod
    def _status_card_routing_hints(status_cards: list[Any]) -> dict[str, Any]:
        """从状态卡里取路由延续要用的信号：最后一张卡的 lastAgent、是否以澄清卡收尾。"""
        hints: dict[str, Any] = {}
        cards = [card for card in status_cards if isinstance(card, dict)]
        for card in reversed(cards):
            last_agent = str(card.get("lastAgent") or "").strip()
            if last_agent:
                hints["last_agent"] = last_agent
                break
        if cards:
            last_card = cards[-1]
            hints["clarification_pending"] = (
                str(last_card.get("type") or "") == "workflow_stopped"
                and str(last_card.get("reason") or "") == "clarification_needed"
            )
        return hints

    def _build_status_cards_history_content(
        self,
        status_cards: list[Any],
    ) -> str:
        """Build a lightweight text summary so status-only turns remain visible to the model."""
        summaries: list[str] = []

        for card in status_cards:
            if not isinstance(card, dict):
                continue

            card_type = str(card.get("type") or "").strip()
            if card_type == "workflow_stopped":
                parts = ["[workflow_stopped]"]
                reason = str(card.get("reason") or "").strip()
                question = str(card.get("question") or card.get("message") or "").strip()
                context = str(card.get("context") or "").strip()
                details = card.get("details")

                if reason:
                    parts.append(f"reason: {reason}")
                if question:
                    parts.append(f"question: {question}")
                if context:
                    parts.append(f"context: {context}")
                if isinstance(details, list):
                    normalized_details = [
                        str(detail).strip()
                        for detail in details
                        if detail is not None and str(detail).strip()
                    ]
                    if normalized_details:
                        parts.append("details: " + "; ".join(normalized_details))

                summaries.append("\n".join(parts))
                continue

            if card_type == "iteration_exhausted":
                parts = ["[iteration_exhausted]"]
                layer = str(card.get("layer") or "").strip()
                iterations_used = card.get("iterationsUsed")
                max_iterations = card.get("maxIterations")
                reason = str(card.get("reason") or "").strip()
                last_agent = str(card.get("lastAgent") or "").strip()

                if layer:
                    parts.append(f"layer: {layer}")
                if iterations_used is not None or max_iterations is not None:
                    parts.append(
                        "iterations: "
                        f"{iterations_used if iterations_used is not None else '?'}"
                        "/"
                        f"{max_iterations if max_iterations is not None else '?'}"
                    )
                if reason:
                    parts.append(f"reason: {reason}")
                if last_agent:
                    parts.append(f"last_agent: {last_agent}")

                summaries.append("\n".join(parts))

        return "\n\n".join(summary for summary in summaries if summary)

    def _trim_history_to_token_budget(
        self,
        messages: list[dict[str, Any]],
        token_budget: int,
    ) -> list[dict[str, Any]]:
        """
        Keep the newest messages within a token budget while preserving chronology.

        The window slides from newest -> oldest and stops when adding an older
        message would exceed the budget. At least one newest message is retained
        when history exists.
        """
        if not messages:
            return []

        if token_budget <= 0:
            return [messages[-1]]

        selected_reversed: list[dict[str, Any]] = []
        used_tokens = 0

        for message in reversed(messages):
            message_tokens = max(1, estimate_message_tokens(message))
            if selected_reversed and used_tokens + message_tokens > token_budget:
                break

            selected_reversed.append(message)
            used_tokens += message_tokens

            if used_tokens >= token_budget:
                break

        return list(reversed(selected_reversed))

    async def load_session_with_compaction(
        self,
        session: Session,
        context_assembler,
        query: str,
        focus_file_id: str | None = None,
        attached_file_ids: list[str] | None = None,
        attached_library_materials: list[dict[str, int]] | None = None,
        text_quotes: list[dict[str, str]] | None = None,
        max_tokens: int = AGENT_CONTEXT_TOKEN_BUDGET,
    ) -> "SessionData":
        """
        Load chat session, assemble context and build the cross-turn working set.

        名字是历史遗留：这里**不做**任何会话压缩/总结（从来没有 compaction 模块），
        历史只是按 token 预算取最近的滑动窗口。为不破坏调用方与测试桩保留原名。

        This is the primary method for new code. It combines:
        1. Chat session loading (newest-first history window + working set refs)
        2. Context assembly
        3. Working set rendering (current DB content of files read/written in
           the last two assistant turns, deduped against full-text context items)

        Args:
            session: Database session
            context_assembler: ContextAssembler instance
            query: User query for context retrieval
            focus_file_id: Currently focused file ID
            attached_file_ids: List of attached file IDs
            attached_library_materials: List of library material references
            text_quotes: List of user-selected text quotes
            max_tokens: Maximum tokens for context

        Returns:
            SessionData with chat session and assembled context
        """
        if self._should_offload_session_work(session):
            # 1 & 2. Load chat session + assemble context in a worker thread with a
            # fresh sync DB session so async request handling stays responsive.
            result = await asyncio.to_thread(
                self._load_session_and_context_sync,
                context_assembler,
                query,
                focus_file_id,
                attached_file_ids,
                attached_library_materials,
                text_quotes,
                max_tokens,
            )
        else:
            # Keep sqlite/test execution on the caller session so existing test
            # fixtures and in-memory DB behavior remain deterministic.
            result = self.load_chat_session(session)
            result.context_data = self.assemble_context(
                session,
                context_assembler,
                query,
                focus_file_id,
                attached_file_ids,
                attached_library_materials,
                text_quotes,
                max_tokens,
            )
            self.attach_working_set(session, result)

        return result
