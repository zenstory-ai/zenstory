"""
Agent service - AI writing assistant with multi-agent workflow.

Features:
- Open-ended conversation (no fixed intents)
- File operations via Function Calling (create, update, delete, query)
- Chat history with memory
- Streaming SSE output
- Intelligent context assembly with priority-based selection
- Multi-agent routing (planner/writer/quality_reviewer)
- Steering message support

Powered by LangGraph workflow orchestration + openai-agents-python.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import desc
from sqlmodel import Session, and_, select

from config.agent_runtime import (
    AGENT_AUTO_REVIEW_THRESHOLD_CHARS,
    AGENT_CHAT_HISTORY_TOKEN_BUDGET,
    AGENT_COLLABORATION_MAX_ITERATIONS,
    AGENT_CONTEXT_TOKEN_BUDGET,
    AGENT_RUN_WALL_CLOCK_TIMEOUT_S,
)
from config.datetime_utils import utcnow
from database import create_session
from utils.logger import get_logger, log_with_context

from .context import ContextAssembler, get_context_assembler
from .context.budget import compute_history_token_budget
from .core.events import (
    context_event,
    done_event,
    error_event,
    session_started_event,
    skill_matched_event,
    thinking_event,
)
from .core.message_manager import MessageManager
from .core.metrics import (
    AGENT_REQUESTS_DURATION_MS,
    AGENT_REQUESTS_ERRORS,
    AGENT_REQUESTS_TOTAL,
    CONTEXT_ITEMS_COUNT,
    CONTEXT_TOKENS_TOTAL,
    get_metrics_collector,
)
from .core.run_meter import AgentRunMeter
from .core.session_loader import SessionLoader
from .core.steering import (
    _RUN_HEARTBEAT_INTERVAL_S,
    cleanup_steering_queue_async,
    create_steering_queue_async,
    heartbeat_steering_run_async,
    requeue_steering_if_other_active_async,
)
from .core.stream_errors import classify_stream_exception, log_stream_exception
from .graph.state import WritingState
from .graph.writing_graph import run_writing_workflow_streaming
from .openai_agents.model import DEEPSEEK_WRITING_MODEL
from .skills import get_skill_context_injector, resolve_selected_skills
from .skills.active_skills import list_active_skill_resources
from .skills.content_budget import split_selected_budget
from .stream_adapter import create_stream_adapter
from .tools.author_edit_guard import (
    CONFIRM_FILE_IDS_ROUTING_KEY,
    MAX_CONFIRM_FILE_IDS,
    AuthorEditRefusals,
    confirmed_file_ids_from_history,
    referenced_file_ids_from_metadata,
)
from .tools.mcp_tools import ToolContext, _should_offload_tool_execution

logger = get_logger(__name__)

# 取消路径补存部分历史时写进 message_metadata.stop_reason 的值。墙钟时限到期
# 也是以取消的方式到达 process_stream（sse_pump 取消后台 task），按已用时长区分。
PARTIAL_SAVE_CANCELLED_STOP_REASON = "cancelled"
PARTIAL_SAVE_DEADLINE_STOP_REASON = "run_deadline_exceeded"


def partial_save_stop_reason(elapsed_s: float, deadline_s: float | None) -> str:
    """取消路径的 stop_reason：已用时长达到墙钟时限就是时限截停，否则是用户停止。

    process_stream 先于 SSE 泵开始计时，所以泵按时限取消时这里的已用时长一定
    不小于时限；用户在时限前主动停止时一定小于时限。
    """
    if deadline_s and deadline_s > 0 and elapsed_s >= deadline_s:
        return PARTIAL_SAVE_DEADLINE_STOP_REASON
    return PARTIAL_SAVE_CANCELLED_STOP_REASON


def routing_from_router_decided(data: dict[str, Any]) -> dict[str, Any]:
    """把 ROUTER_DECIDED 事件转成落库的 message_metadata.routing。

    下一轮的「继续」直达与回答提问时的路由沿用（graph/router.py）从这里读回
    上一轮的 agent 和用户范围；只留这几个字段，不存 reason / confidence。
    """
    routing_metadata = data.get("routing_metadata")
    if not isinstance(routing_metadata, dict):
        routing_metadata = {}
    initial_agent = str(data.get("initial_agent") or routing_metadata.get("agent_type") or "")
    write_content = routing_metadata.get("write_content")
    scope = routing_metadata.get("scope")
    routing = {
        "initial_agent": initial_agent,
        "workflow_type": str(
            data.get("workflow_plan") or routing_metadata.get("workflow_type") or ""
        ),
        "read_only": routing_metadata.get("read_only") is True,
        "write_content": write_content if isinstance(write_content, bool) else None,
        "scope": scope.strip() if isinstance(scope, str) else "",
        "last_agent": initial_agent,
    }
    if routing_metadata.get("clarify_first") is True:
        # 这一轮是为笼统要求先问清楚：下一轮不再问第二次（graph/writing_graph._should_clarify_first）。
        routing["clarify_first"] = True
    return routing


# 工作台首页「输入想法 → 新建作品」后自动发出的第一条消息带 metadata.entry=dashboard_idea。
# 前端发的是作者原话；服务端只在发给模型的本轮内容里附一句隐藏提示，落库仍是原话。
DASHBOARD_IDEA_ENTRY = "dashboard_idea"

_PROJECT_TYPE_LABELS_ZH = {"novel": "长篇小说", "short": "短篇小说", "screenplay": "短剧剧本"}
_PROJECT_TYPE_LABELS_EN = {"novel": "Novel", "short": "Short Story", "screenplay": "Short Drama Script"}


def dashboard_kickoff_hint(project_type: str | None, force_en: bool) -> str:
    """工作台首条想法的隐藏提示（只进模型输入，不落库、不显示）。"""
    if force_en:
        label = _PROJECT_TYPE_LABELS_EN.get(project_type or "", "project")
        return (
            f"This is the author's first message after creating a new \"{label}\" from the dashboard: "
            "if the author has clearly asked for the manuscript, an outline or a script, do exactly that; "
            "if the idea is already concrete, give the main characters, setting, core conflict and overall arc; "
            "if it is still vague, ask two or three key questions first."
        )
    label = _PROJECT_TYPE_LABELS_ZH.get(project_type or "", "作品")
    return (
        f"这是作者在工作台新建「{label}」后发来的第一条消息："
        "作者已经明确要求写正文/大纲/剧本时，直接按要求做；"
        "想法已经具体时，给出主要人物、背景、核心冲突和大致走向；"
        "还很模糊时，先问两三个关键问题。"
    )


def _load_project_type_sync(project_id: str) -> str | None:
    """用独立短 session 查作品类型（PG offload 分支，不碰请求级 session）。"""
    from models import Project

    with create_session() as read_session:
        project = read_session.get(Project, project_id)
        return getattr(project, "project_type", None) if project else None


class AgentService:
    """
    AI writing assistant service powered by workflow orchestration + openai-agents-python.

    Features:
    - Multi-agent routing (planner/writer/quality_reviewer)
    - Open-ended conversation with DeepSeek via openai-agents-python
    - File CRUD via tool calling
    - Chat history persistence
    - Streaming responses
    - Intelligent context assembly
    """

    def __init__(
        self,
        context_assembler: ContextAssembler | None = None,
    ):
        """Initialize agent service."""
        self.context_assembler = context_assembler or get_context_assembler()
        log_with_context(
            logger,
            20,  # INFO
            "AgentService initialized with workflow orchestration",
        )

    @staticmethod
    def _schedule_background_cleanup(
        coro: Any, *, description: str, session_id: str | None
    ) -> asyncio.Task:
        """Run async cleanup out of band when generator cancellation makes awaiting risky."""
        task = asyncio.create_task(coro)

        def _log_task_result(done_task: asyncio.Task) -> None:
            try:
                done_task.result()
            except Exception as exc:  # pragma: no cover - depends on runtime cancellation timing
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Background cleanup task failed",
                    description=description,
                    session_id=session_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        task.add_done_callback(_log_task_result)
        return task

    @staticmethod
    def _should_offload_session_work(session: Session) -> bool:
        """Only offload when running against PostgreSQL production-style sessions."""
        bind = session.get_bind() if hasattr(session, "get_bind") else None
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        return dialect_name == "postgresql"

    def _resolve_or_create_chat_session_id(
        self,
        session: Session,
        *,
        project_id: str,
        user_id: str,
        requested_session_id: str | None,
    ) -> str:
        """
        Resolve the `session_id` used by:
        - SSE `session_started` event (frontend steering + continuity)
        - ToolContext (task board + artifact ledger)

        IMPORTANT: This value must exist in `chat_session.id` because
        `agent_artifact_ledger.session_id` has a FK to `chat_session.id`.
        """
        from models import ChatSession

        normalized_requested = (requested_session_id or "").strip()

        # 1) If caller provided a runtime session id, we REQUIRE it to exist in
        #    `chat_session.id` because agent_artifact_ledger has an FK to it.
        #    - If it exists and belongs to this user+project, reuse it.
        #    - If it doesn't exist, create it with that exact id (so we can echo
        #      it back in `session_started` for steering continuity).
        #    - If it exists but belongs to someone else, ignore it and fall back
        #      to the latest active session (defense-in-depth).
        if normalized_requested:
            candidate = session.get(ChatSession, normalized_requested)

            if candidate is not None:
                if candidate.user_id != user_id or candidate.project_id != project_id:
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Requested session_id does not belong to user/project; ignoring",
                        project_id=project_id,
                        user_id=user_id,
                        requested_session_id=normalized_requested,
                        candidate_user_id=candidate.user_id,
                        candidate_project_id=candidate.project_id,
                    )
                else:
                    other_actives = session.exec(
                        select(ChatSession)
                        .where(
                            and_(
                                ChatSession.project_id == project_id,
                                ChatSession.user_id == user_id,
                                ChatSession.is_active,
                                ChatSession.id != candidate.id,
                            )
                        )
                        .order_by(
                            desc(ChatSession.updated_at),
                            desc(ChatSession.created_at),
                            desc(ChatSession.id),
                        )
                    ).all()

                    deactivated = False
                    for stale in other_actives:
                        if stale.is_active:
                            stale.is_active = False
                            session.add(stale)
                            deactivated = True

                    if not candidate.is_active:
                        # 部分唯一索引 (user_id, project_id) WHERE is_active=true
                        # 非延迟约束，而 SQLAlchemy 对同一 mapper 的 UPDATE 按主键
                        # 排序下发；必须先把旧活跃会话的去激活刷到数据库，再激活
                        # candidate，否则 candidate 主键较小时会瞬时出现两行
                        # active 触发 IntegrityError。
                        if deactivated:
                            session.flush()
                        candidate.is_active = True

                    candidate.updated_at = utcnow()
                    session.add(candidate)

                    # 即使没有去激活/重新激活，也必须立即提交 updated_at 的刷新。
                    # SQLite 分支传入的是请求级 session（autoflush=True）：脏
                    # UPDATE 若留在 session 里，会被随后 SessionLoader 的第一条
                    # 读查询顺手 flush，pysqlite 在 DML 前隐式 BEGIN，于是这个
                    # 写事务连同 RESERVED 锁一直挂到整轮 SSE 结束才随历史保存
                    # 提交；期间工具在独立连接上的每次写入都会等满 busy_timeout
                    # 后报 "database is locked"。PostgreSQL 分支用
                    # `with create_session()` 包住本函数，未提交的修改在 close
                    # 时被直接丢弃，刷新从未落库。
                    resolved_id = candidate.id
                    session.commit()
                    return resolved_id

            else:
                # Compatibility note:
                # - Before artifact-ledger FK enforcement, the frontend/session layer
                #   used a runtime UUID that was NOT persisted to `chat_session.id`.
                #   Those legacy clients may still send such a session_id after a
                #   backend deploy while an active chat_session (with history) exists.
                #   In that case, we must NOT create a new chat_session (which would
                #   effectively "lose" the current active history in UI).
                existing_actives = session.exec(
                    select(ChatSession)
                    .where(
                        and_(
                            ChatSession.project_id == project_id,
                            ChatSession.user_id == user_id,
                            ChatSession.is_active,
                        )
                    )
                    .order_by(
                        desc(ChatSession.updated_at),
                        desc(ChatSession.created_at),
                        desc(ChatSession.id),
                    )
                ).all()

                if existing_actives:
                    log_with_context(
                        logger,
                        20,  # INFO
                        "Requested session_id not found; falling back to active chat session",
                        project_id=project_id,
                        user_id=user_id,
                        requested_session_id=normalized_requested,
                        resolved_session_id=existing_actives[0].id,
                    )

                    # Repair stale multi-active states defensively (newest wins).
                    if len(existing_actives) > 1:
                        for stale in existing_actives[1:]:
                            if stale.is_active:
                                stale.is_active = False
                                session.add(stale)
                        session.commit()
                        session.refresh(existing_actives[0])

                    return existing_actives[0].id

                chat_session = ChatSession(
                    id=normalized_requested,
                    user_id=user_id,
                    project_id=project_id,
                    title="AI 助手对话",
                    is_active=True,
                    message_count=0,
                )
                session.add(chat_session)
                session.commit()
                session.refresh(chat_session)
                return chat_session.id

        # 2) Otherwise, use the newest active session (if any).
        active_sessions = session.exec(
            select(ChatSession)
            .where(
                and_(
                    ChatSession.project_id == project_id,
                    ChatSession.user_id == user_id,
                    ChatSession.is_active,
                )
            )
            .order_by(
                desc(ChatSession.updated_at),
                desc(ChatSession.created_at),
                desc(ChatSession.id),
            )
        ).all()
        chat_session = active_sessions[0] if active_sessions else None

        # Repair stale multi-active states defensively (newest wins).
        if chat_session is not None and len(active_sessions) > 1:
            for stale in active_sessions[1:]:
                if stale.is_active:
                    stale.is_active = False
                    session.add(stale)
            session.commit()
            session.refresh(chat_session)

        if chat_session is not None:
            return chat_session.id

        # 3) If no active session exists yet, create one now so tools can safely
        #    write agent_artifact_ledger rows during streaming.
        chat_session = ChatSession(
            user_id=user_id,
            project_id=project_id,
            title="AI 助手对话",
            is_active=True,
            message_count=0,
        )
        session.add(chat_session)
        session.commit()
        session.refresh(chat_session)
        return chat_session.id

    def _resolve_or_create_chat_session_id_sync(
        self,
        *,
        project_id: str,
        user_id: str,
        requested_session_id: str | None,
    ) -> str:
        """Resolve/create chat session id using a fresh sync DB session."""
        with create_session() as sync_session:
            return self._resolve_or_create_chat_session_id(
                sync_session,
                project_id=project_id,
                user_id=user_id,
                requested_session_id=requested_session_id,
            )

    def _resolve_selected_skills(
        self,
        session: Session,
        *,
        project_id: str,
        user_id: str | None,
        selected_skill_ids: list[str] | None,
        message: str,
    ) -> list[dict[str, Any]]:
        """
        Resolve skills the user explicitly selected for this message and record their usage.

        只接受属于当前用户且启用中的技能，其余 ID 静默忽略。
        """
        if not user_id or not selected_skill_ids:
            return []

        from services.skill_usage_service import record_skill_usage

        selected: list[dict[str, Any]] = []
        skills = resolve_selected_skills(session, user_id, selected_skill_ids)
        # 显式选择的正文进 system prompt，会被每轮迭代重复发送：合计不超过技能内容预算的
        # 显式选择份额，超出部分截断，并提示模型用 read_skill_resource 分段续读。
        segments = split_selected_budget([skill.instructions for skill in skills])
        for skill, segment in zip(skills, segments, strict=True):
            selected.append({
                "id": skill.id,
                "name": skill.name,
                "instructions": segment.text,
                "instructions_tokens": segment.tokens,
                "instructions_next_offset": segment.next_offset,
                "instructions_total_chars": segment.total_chars,
                "source": skill.source,
                "resources": [
                    resource.path for resource in list_active_skill_resources(session, skill)
                ],
            })
            # 用独立 session（同一个 engine）记录用量：record_skill_usage 失败时要回滚，
            # 若用请求 session 回滚，会连带丢掉它上面尚未提交的状态并让已加载对象全部过期。
            try:
                with Session(session.get_bind()) as usage_session:
                    record_skill_usage(
                        session=usage_session,
                        project_id=project_id,
                        skill_id=skill.id,
                        skill_name=skill.name,
                        skill_source=skill.source,
                        matched_trigger="selected",
                        confidence=1.0,
                        user_id=user_id,
                        user_message=message,
                    )
            except Exception as exc:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Failed to record selected skill usage",
                    skill_id=skill.id,
                    project_id=project_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        return selected

    def _prepare_prompt_artifacts(
        self,
        session: Session,
        *,
        project_id: str,
        user_id: str | None,
        selected_skill_ids: list[str] | None,
        message: str,
        session_id: str | None,
        metadata: dict[str, Any] | None,
        language: str,
        assembled_context: str | None,
        context_items: list[dict[str, Any]] | None,
    ) -> tuple[list[dict[str, Any]], str]:
        """
        Build the skill catalog, resolve selected skills and build the system prompt.

        Returns:
            (selected skills, system prompt)
        """
        skill_injector = get_skill_context_injector()
        skill_catalog = skill_injector.build_skill_catalog(session, user_id)

        message_manager = MessageManager(
            project_id=project_id,
            user_id=user_id,
        )
        selected_skills = self._resolve_selected_skills(
            session,
            project_id=project_id,
            user_id=user_id,
            selected_skill_ids=selected_skill_ids,
            message=message,
        )
        system_prompt = message_manager.build_system_prompt(
            session=session,
            session_id=session_id,
            metadata=metadata,
            assembled_context=assembled_context,
            context_items=context_items,
            language=language,
            skill_catalog=skill_catalog,
            selected_skills=selected_skills or None,
        )
        return selected_skills, system_prompt

    async def _lookup_project_type(self, session: Session, project_id: str) -> str | None:
        """作品类型只用于首条想法提示的类型名；查不到时提示里用通用说法。"""
        try:
            if self._should_offload_session_work(session):
                return await asyncio.to_thread(_load_project_type_sync, project_id)
            from models import Project

            project = session.get(Project, project_id)
            return getattr(project, "project_type", None) if project else None
        except Exception as exc:
            log_with_context(
                logger,
                30,  # WARNING
                "Failed to load project type for dashboard kickoff hint",
                project_id=project_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    def _prepare_prompt_artifacts_sync(self, **kwargs: Any) -> tuple[list[dict[str, Any]], str]:
        """Same as _prepare_prompt_artifacts, using a fresh sync DB session."""
        with create_session() as sync_session:
            return self._prepare_prompt_artifacts(sync_session, **kwargs)

    async def process_stream(
        self,
        project_id: str,
        user_id: str | None,
        message: str,
        session: Session,
        session_id: str | None = None,
        selected_text: str | None = None,
        metadata: dict[str, Any] | None = None,
        language: str | None = None,
        selected_skill_ids: list[str] | None = None,
        run_report: dict[str, Any] | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        Process user message with streaming response.

        Args:
            project_id: Project ID (UUID)
            user_id: User ID (UUID, for chat history)
            message: User's message
            session: Database session
            selected_text: Optional selected text for context
            metadata: Optional metadata (current_file_id, etc.)
            language: Language preference (zh/en)
            selected_skill_ids: Skills the user explicitly selected for this message
            run_report: Optional dict the caller owns; filled at the end of the run
                with model, token usage, model calls, LLM time and stop reason so the
                API layer can write one summary log line together with billing.

        Yields:
            SSE event strings
        """
        start_time = utcnow()
        run_started_monotonic = time.monotonic()
        metrics = get_metrics_collector()
        metrics.increment_counter(AGENT_REQUESTS_TOTAL)
        message_preview = message[:100] + "..." if len(message) > 100 else message

        log_with_context(
            logger,
            20,  # INFO
            "Agent process_stream started (workflow)",
            project_id=project_id,
            user_id=user_id,
            message_length=len(message),
            message_preview=message_preview,
            has_selected_text=selected_text is not None,
            language=language,
            current_file_id=metadata.get("current_file_id") if metadata else None,
        )

        # Resolve chat-session id for continuity + artifact-ledger FK safety.
        # (Historically this was a standalone runtime UUID; but artifact ledger
        # requires `session_id` to exist in `chat_session.id`.)
        requested_session_id = session_id
        if user_id:
            if self._should_offload_session_work(session):
                session_id = await asyncio.to_thread(
                    self._resolve_or_create_chat_session_id_sync,
                    project_id=project_id,
                    user_id=user_id,
                    requested_session_id=requested_session_id,
                )
            else:
                session_id = self._resolve_or_create_chat_session_id(
                    session,
                    project_id=project_id,
                    user_id=user_id,
                    requested_session_id=requested_session_id,
                )
        else:
            session_id = requested_session_id or str(uuid.uuid4())

        # Claim the session's single generation slot atomically. Chat history
        # rows have no run/turn sequence and message_count is updated per run,
        # so concurrent writers to one session would interleave history and
        # lose increments. Different chat sessions still run concurrently.
        steering_run_id = uuid.uuid4().hex
        steering_queue = await create_steering_queue_async(
            session_id,
            user_id,
            run_id=steering_run_id,
            exclusive_run=True,
        )

        # 周期心跳：独立 task 每隔 _RUN_HEARTBEAT_INTERVAL_S 秒续期本 run 的持有，
        # 长时间的模型调用 / 工具执行期间也不会被当成僵尸回收；进程被杀时心跳
        # 随之停止，残留持有在 _RUN_HEARTBEAT_TTL_S 后自动失效。
        async def _run_heartbeat_loop() -> None:
            while True:
                await asyncio.sleep(_RUN_HEARTBEAT_INTERVAL_S)
                try:
                    await heartbeat_steering_run_async(session_id, steering_run_id)
                except Exception as exc:
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Steering run heartbeat failed",
                        session_id=session_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )

        run_heartbeat_task = asyncio.create_task(_run_heartbeat_loop())

        def _stop_run_heartbeat() -> None:
            if not run_heartbeat_task.done():
                run_heartbeat_task.cancel()

        run_meter = AgentRunMeter()

        # Initialize tracking variables before try block for exception safety
        all_tool_calls: list[dict[str, Any]] = []
        tool_call_index_by_id: dict[str, int] = {}
        consumed_steering: list[str] = []
        assistant_response = ""
        reasoning_content = ""
        assistant_stop_reason: str | None = None
        assistant_usage: dict[str, Any] | None = None
        assistant_status_cards: list[dict[str, Any]] = []
        assistant_display_events: list[dict[str, Any]] = []
        # 本轮路由（ROUTER_DECIDED + 最后一个 AGENT_SELECTED），随 assistant 消息落库
        assistant_routing: dict[str, Any] = {}
        # 本轮因为作者手改过而没改成的文件：随 routing 落库，下一轮作者回答时放行。
        author_edit_refusals = AuthorEditRefusals()

        def _routing_for_save() -> dict[str, Any] | None:
            refused = author_edit_refusals.file_ids()[:MAX_CONFIRM_FILE_IDS]
            if not refused:
                return assistant_routing or None
            return {**assistant_routing, CONFIRM_FILE_IDS_ROUTING_KEY: refused}

        display_text_run_type: str | None = None
        pending_done_payload: dict[str, Any] | None = None
        had_stream_error = False
        request_failed = False
        stream_cancelled = False
        history_saved = False
        history_save_task: asyncio.Task | None = None
        cancellation_save_task: asyncio.Task | None = None

        # Steering callback for the agent loop. 定义在 try 之前：取消/失败
        # 路径的兜底 drain 也依赖它，不能等到工作流启动处才可用。
        async def get_steering_messages():
            """Get pending steering messages for agent loop."""
            steering_pending = await steering_queue.get_pending()
            payload = [{"id": m.id, "content": m.content} for m in steering_pending]
            # Record what the run actually consumed so it can be persisted to
            # chat history (otherwise node-boundary steering is lost on the
            # next request).
            consumed_steering.extend(
                str(item["content"]) for item in payload if item.get("content")
            )
            return payload

        def _has_assistant_payload() -> bool:
            """本轮是否产生过值得写成一条 assistant 消息的内容。

            取消/失败若发生在模型吐出第一个 token 之前，assistant_response 仍是
            初始空串：此时落库只会得到一条前端永远不会渲染的空消息，却把
            ChatSession.message_count 多加 2，并占掉「最近消息」窗口的一格，
            把更早的真实消息挤出去。
            """
            return bool(
                assistant_response.strip()
                or all_tool_calls
                or assistant_status_cards
                or assistant_display_events
                or (reasoning_content or "").strip()
            )

        def _append_display_text(event_type: str, content: Any) -> None:
            """Append one display text chunk, coalescing only an uninterrupted run."""
            nonlocal display_text_run_type
            if not isinstance(content, str) or not content:
                return
            if (
                display_text_run_type == event_type
                and assistant_display_events
                and assistant_display_events[-1].get("type") == event_type
            ):
                assistant_display_events[-1]["content"] += content
            else:
                assistant_display_events.append({"type": event_type, "content": content})
            display_text_run_type = event_type

        def _append_user_messages_sync(texts: list[str]) -> int:
            """把若干条文本作为 user 行追加进当前会话（不重写整轮历史）。

            两个用途：
            1. 整轮历史已经落库之后才 drain 出来的 steering —— 重走整轮保存会把
               user/assistant 正文写第二遍，只能单独追加；
            2. 完全没有 assistant 内容的取消/失败路径 —— 只保留用户消息与已消费
               的 steering。
            message_count 按实际写入的行数递增，避免计数漂移。
            """
            from models import ChatMessage, ChatSession

            cleaned = [text for text in texts if isinstance(text, str) and text.strip()]
            if not cleaned or not session_id:
                return 0

            with create_session() as append_session:
                chat_session = append_session.get(ChatSession, session_id)
                if chat_session is None:
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Chat session not found; skip appending user messages",
                        session_id=session_id,
                        project_id=project_id,
                        user_id=user_id,
                    )
                    return 0
                if (
                    chat_session.user_id != user_id
                    or chat_session.project_id != project_id
                ):
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Chat session does not belong to project/user; skip appending",
                        session_id=session_id,
                        project_id=project_id,
                        user_id=user_id,
                    )
                    return 0

                for text in cleaned:
                    append_session.add(
                        ChatMessage(
                            session_id=chat_session.id,
                            role="user",
                            content=text,
                        )
                    )
                chat_session.message_count += len(cleaned)
                chat_session.updated_at = utcnow()
                append_session.commit()

            return len(cleaned)

        async def _hand_back_steering(pending: list[str]) -> bool:
            """本 run 兜底 drain 出来的 steering，若还有并发 run 在生成就交还。

            队列只按 chat session 寻址（POST /agent/steer 的请求体没有 run_id），
            无法把消息定向投给某个 run；能保证的不变量是「已经结束的 run 不得
            吞掉本该由仍在生成的 run 消费的引导」。因此这里原子地确认除本 run
            之外仍有活跃持有者并把消息放回队列，避免最后一个并发 run 恰好在
            探测与回填之间退出、把消息写进已删除队列。
            返回 True 表示已交还，调用方不再把它们写进本轮历史。

            保存历史前与收尾保存前都在本 run 仍持有队列时调用；
            探测按「除自己以外」计数，历史真正写完后才释放当前 run。
            """
            if not pending or not user_id:
                return False

            try:
                return await requeue_steering_if_other_active_async(
                    session_id,
                    steering_run_id,
                    user_id,
                    pending,
                )
            except Exception as exc:
                # 原子回填失败时消息仍由本轮历史兜底，绝不静默丢弃。
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Failed to requeue steering messages for concurrent run",
                    session_id=session_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return False

        async def _finish_history_write(write: Any, *args: Any) -> None:
            """Keep finalization ownership until an uncancellable SQL worker finishes."""
            write_task = asyncio.create_task(asyncio.to_thread(write, *args))
            try:
                await asyncio.shield(write_task)
            except asyncio.CancelledError:
                # Cancelling the awaiter cannot stop to_thread. Repeated cancellation
                # must not release ownership while that worker can still commit.
                while not write_task.done():
                    try:
                        await asyncio.wait({write_task})
                    except asyncio.CancelledError:
                        continue
                if not write_task.cancelled():
                    write_task.exception()
                raise

        # 取消/失败路径补存历史时，带上 stream adapter 已累计的用量（已完成的
        # agent run 与路由），否则这部分用量在对话展示里直接丢失。
        # 计费以 llm_usage_event 账本为准，这里只影响对话内的用量展示。
        usage_source: dict[str, Any] = {}

        def _accumulated_usage() -> dict[str, Any] | None:
            adapter = usage_source.get("adapter")
            if adapter is None:
                return None
            usage = adapter.get_last_message_metadata().get("usage")
            return usage if isinstance(usage, dict) and usage else None

        def _save_partial_history_sync(stop_reason: str | None = None) -> None:
            """用独立 session 落库部分历史（取消/失败路径，绕开共享 session）。"""
            if not _has_assistant_payload():
                # 空 assistant 不落库，只保留用户消息与已消费的 steering。
                _append_user_messages_sync([message, *consumed_steering])
                return

            recovery_manager = MessageManager(
                project_id=project_id,
                user_id=user_id,
            )
            with create_session() as recovery_session:
                recovery_manager._save_messages_with_session(
                    recovery_session,
                    session_id,
                    message,
                    assistant_response,
                    all_tool_calls if all_tool_calls else None,
                    reasoning_content if reasoning_content else None,
                    assistant_status_cards=assistant_status_cards or None,
                    steering_messages=consumed_steering or None,
                    assistant_display_events=assistant_display_events or None,
                    assistant_usage=_accumulated_usage(),
                    assistant_stop_reason=stop_reason,
                    assistant_routing=_routing_for_save(),
                )

        try:
            lang = (language or "").strip().lower() or "zh"
            force_en = lang.startswith("en")

            # Emit session_started event FIRST
            yield session_started_event(session_id).to_sse()

            # Extract metadata
            focus_file_id = metadata.get("current_file_id") if metadata else None
            attached_file_ids = metadata.get("attached_file_ids") if metadata else None
            attached_library_materials = metadata.get("attached_library_materials") if metadata else None
            text_quotes = metadata.get("text_quotes") if metadata else None
            generation_mode_raw = metadata.get("generation_mode") if metadata else None
            generation_mode = (
                str(generation_mode_raw).strip().lower()
                if generation_mode_raw is not None
                else None
            )
            if generation_mode not in {"fast", "quality"}:
                generation_mode = None

            # Assemble intelligent context
            yield thinking_event(
                "Assembling context..." if force_en else "正在组装上下文..."
            ).to_sse()

            session_loader = SessionLoader(project_id, user_id)
            session_data = await session_loader.load_session_with_compaction(
                session=session,
                context_assembler=self.context_assembler,
                query=message,
                focus_file_id=focus_file_id,
                attached_file_ids=attached_file_ids,
                attached_library_materials=attached_library_materials,
                text_quotes=text_quotes,
                max_tokens=AGENT_CONTEXT_TOKEN_BUDGET,
            )

            context_data = session_data.context_data
            history_messages = session_data.history_messages
            token_count = max(0, int(getattr(context_data, "token_estimate", 0) or 0))
            item_count = len(getattr(context_data, "items", []) or [])
            metrics.increment_counter(CONTEXT_TOKENS_TOTAL, amount=token_count)
            metrics.increment_counter(CONTEXT_ITEMS_COUNT, amount=item_count)

            # Emit context event to frontend
            if context_data and context_data.items:
                yield context_event(
                    items=context_data.items,
                    token_count=context_data.token_estimate,
                ).to_sse()

            message_manager = MessageManager(
                project_id=project_id,
                user_id=user_id,
            )

            prompt_kwargs: dict[str, Any] = {
                "project_id": project_id,
                "user_id": user_id,
                "selected_skill_ids": selected_skill_ids,
                "message": message,
                "session_id": session_id,
                "metadata": metadata,
                "language": lang,
                "assembled_context": context_data.context if context_data.context else None,
                "context_items": context_data.items if context_data.items else None,
            }
            if self._should_offload_session_work(session):
                selected_skills, system_prompt = await asyncio.to_thread(
                    self._prepare_prompt_artifacts_sync,
                    **prompt_kwargs,
                )
            else:
                selected_skills, system_prompt = self._prepare_prompt_artifacts(
                    session,
                    **prompt_kwargs,
                )

            # 显式选择的技能已注入完整方法，流开头告知前端
            for selected in selected_skills:
                yield skill_matched_event(
                    skill_id=selected["id"],
                    skill_name=selected["name"],
                    matched_trigger="selected",
                ).to_sse()

            # Build current user message
            user_content = message
            if selected_text:
                user_content += f"\n\n{'Selected text' if force_en else '选中的文本'}:\n{selected_text}"

            if metadata:
                context_parts = []
                if "current_file_id" in metadata:
                    context_parts.append(
                        f"{'Current file ID' if force_en else '当前文件 ID'}: {metadata['current_file_id']}"
                    )
                if "current_file_type" in metadata:
                    context_parts.append(
                        f"{'File type' if force_en else '文件类型'}: {metadata['current_file_type']}"
                    )
                if metadata.get("entry") == DASHBOARD_IDEA_ENTRY:
                    context_parts.append(
                        dashboard_kickoff_hint(
                            await self._lookup_project_type(session, project_id),
                            force_en,
                        )
                    )
                if context_parts:
                    user_content += f"\n\n{'Context' if force_en else '上下文'}:\n" + "\n".join(context_parts)

            # 跨轮工作集（上两轮读/写过的文件的当前全文）拼在本轮用户消息之前：
            # 系统提示与历史保持不变，前缀缓存可以一直命中到上一轮。
            working_set_context = getattr(session_data, "working_set_context", "")
            if not isinstance(working_set_context, str):
                working_set_context = ""
            if working_set_context:
                user_content = SessionLoader.attach_working_set_to_user_content(
                    user_content, working_set_context
                )

            # Unified prompt-token ledger.
            #
            # Assembled context and chat history used to be budgeted
            # independently and both injected. Now they compete
            # within ONE shared ceiling: subtract the already-known prompt cost
            # from the ledger ceiling, then shrink the history window to whatever
            # room remains. The DB-history persistence contract and the configured
            # dual budgets are unchanged — this only trims what is *loaded*.
            #
            # system_prompt 已由 build_system_prompt 内嵌 skill catalog /
            # selected skills / assembled context，台账只按最终 prompt 计费一次，
            # 避免同一段文本重复扣减历史预算。
            from agent.utils.token_utils import estimate_text_tokens

            reserved_prompt_tokens = estimate_text_tokens(system_prompt or "") + (
                estimate_text_tokens(working_set_context) if working_set_context else 0
            )
            effective_history_budget = compute_history_token_budget(
                configured_history_budget=AGENT_CHAT_HISTORY_TOKEN_BUDGET,
                reserved_prompt_tokens=reserved_prompt_tokens,
            )
            history_messages = session_loader._trim_history_to_token_budget(
                history_messages,
                effective_history_budget,
            )

            log_with_context(
                logger,
                20,  # INFO
                "Prompt token ledger allocation",
                project_id=project_id,
                user_id=user_id,
                reserved_prompt_tokens=reserved_prompt_tokens,
                configured_history_budget=AGENT_CHAT_HISTORY_TOKEN_BUDGET,
                effective_history_budget=effective_history_budget,
                history_message_count=len(history_messages),
            )

            # Combine messages (without system message - it is passed separately to the model)
            messages = history_messages + [{"role": "user", "content": user_content}]

            yield thinking_event(
                "Thinking..." if force_en else "正在思考..."
            ).to_sse()

            log_with_context(
                logger,
                20,  # INFO
                "Starting workflow streaming",
                project_id=project_id,
                user_id=user_id,
                total_messages=len(messages),
            )

            # 进入工作流前结束请求级 session 上的事务。SQLite 分支前面的会话
            # 解析、历史加载、技能目录都跑在它上面，而工具在独立连接上写库：
            # 只要这里残留一个已 flush 的写事务，SQLite 的库级写锁就会被攥满
            # 整轮 SSE，工具写入全部 "database is locked"。只读时 commit 不向
            # 数据库发任何语句；PostgreSQL 分支此时请求级 session 没有事务
            # （路由层已 rollback，前置步骤都 offload 到独立 session），不进分支。
            if session.in_transaction():
                session.commit()

            # Set tool context for tool execution
            # 工具上下文里放不放共享 session，必须与「工具是否 offload 到线程池」
            # 同源判定，否则两个开关会在 SQLite 上错配：
            # _should_offload_tool_execution() 已恒为 True，所有 *_sync 工具都跑在
            # asyncio.to_thread 的工作线程里；而 _should_offload_session_work 只对
            # PostgreSQL 为真，于是 SQLite 部署下这里传的是**请求级 Session**，
            # ToolContext.get_session() 第一分支就把它交给工作线程——
            # SDK 同一 turn 里的多个 tool_call 是并发 asyncio Task，两个以上工作
            # 线程会同时在同一个 SQLAlchemy Session 上 flush/commit/refresh
            # （Session 非线程安全；check_same_thread=False 只是让 sqlite3 不报错）。
            # 传 None + create_session_func，让每个工作线程自建 session。
            ToolContext.set_context(
                session=None if _should_offload_tool_execution() else session,
                user_id=user_id,
                project_id=project_id,
                session_id=session_id,
                create_session_func=create_session,
                # 显式选择的技能已记过 selected 用量，模型再 load_skill 它时不重复记
                recorded_skill_ids=[selected["id"] for selected in selected_skills],
                # 显式选择的正文已占用的技能内容预算
                skill_tokens_used=sum(
                    int(selected.get("instructions_tokens") or 0) for selected in selected_skills
                ),
                # 作者原话：update_project 据此确认 author_requested 真是作者要求的改名
                author_message=message,
                author_steering=consumed_steering,
                # 作者手改过的文件：作者本轮没点名时 AI 不改（agent/tools/author_edit_guard.py）。
                focus_file_id=focus_file_id if isinstance(focus_file_id, str) else None,
                referenced_file_ids=referenced_file_ids_from_metadata(metadata),
                author_confirmed_file_ids=confirmed_file_ids_from_history(history_messages),
                author_edit_refusals=author_edit_refusals,
            )

            # Build WritingState for workflow execution
            writing_state: WritingState = {
                "user_message": user_content,
                # Router only needs the raw user message (exclude selected_text / metadata decorations)
                "router_message": message,
                "project_id": project_id,
                "user_id": user_id or "",
                "session_id": session_id,
                "generation_mode": generation_mode,
                "system_prompt": system_prompt,
                "context_data": {
                    "context": context_data.context if context_data.context else "",
                    "items": context_data.items,
                },
                "messages": messages,
                "tool_calls": [],
                "run_meter": run_meter,
            }

            # Create stream adapter for SSE conversion
            stream_adapter = create_stream_adapter(
                project_id=project_id,
                user_id=user_id,
                process_file_markers=True,
                # 流开头已为显式选择的技能发过 skill_matched，load_skill 它们时不再重复发
                matched_skill_ids=[selected["id"] for selected in selected_skills],
            )
            usage_source["adapter"] = stream_adapter

            try:
                # Process through workflow stream
                # Use session_id as thread_id for trace correlation
                thread_id = f"{project_id}:{session_id}" if session_id else project_id
                async for event in stream_adapter.process_workflow_events(
                    run_writing_workflow_streaming(
                        writing_state,
                        thread_id=thread_id,
                        max_iterations=AGENT_COLLABORATION_MAX_ITERATIONS,
                        auto_review_threshold=AGENT_AUTO_REVIEW_THRESHOLD_CHARS,
                        get_steering_messages=get_steering_messages,
                    )
                ):
                    event_type = event.type.value
                    if event_type == "done":
                        pending_done_payload = event.data if isinstance(event.data, dict) else {}
                        continue

                    # Yield SSE event
                    yield event.to_sse()

                    if event_type not in {"content", "thinking_content"}:
                        # Even non-persisted stream markers (notably content_start /
                        # content_end) delimit separately rendered text segments.
                        display_text_run_type = None

                    if event_type in {
                        "agent_selected",
                        "router_thinking",
                        "router_decided",
                        "handoff",
                        "iteration_exhausted",
                        "workflow_stopped",
                        "workflow_complete",
                    }:
                        assistant_display_events.append(
                            {
                                "type": event_type,
                                "data": dict(event.data),
                            }
                        )
                        if event_type == "router_decided":
                            assistant_routing = routing_from_router_decided(event.data)
                        elif event_type == "agent_selected" and assistant_routing:
                            selected_agent = str(event.data.get("agent_type") or "").strip()
                            if selected_agent:
                                assistant_routing["last_agent"] = selected_agent

                    # Track content for history
                    if event_type == "content":
                        text_chunk = event.data.get("text", "")
                        assistant_response += text_chunk
                        _append_display_text("content", text_chunk)
                    elif event_type == "thinking_content":
                        thinking_chunk = event.data.get("content", "")
                        reasoning_content += thinking_chunk
                        _append_display_text("thinking_content", thinking_chunk)
                    elif event_type == "tool_call":
                        tool_use_id = str(event.data.get("tool_use_id") or "").strip()
                        tool_call_record = {
                            "id": tool_use_id,
                            "name": event.data.get("tool_name", ""),
                            "arguments": event.data.get("arguments", {}),
                            "status": "pending",
                        }
                        if tool_use_id:
                            existing_index = tool_call_index_by_id.get(tool_use_id)
                            if existing_index is None:
                                tool_call_index = len(all_tool_calls)
                                tool_call_index_by_id[tool_use_id] = tool_call_index
                                all_tool_calls.append(tool_call_record)
                                assistant_display_events.append(
                                    {"type": "tool_call", "tool_call_index": tool_call_index}
                                )
                            else:
                                all_tool_calls[existing_index].update(tool_call_record)
                        else:
                            tool_call_index = len(all_tool_calls)
                            all_tool_calls.append(tool_call_record)
                            assistant_display_events.append(
                                {"type": "tool_call", "tool_call_index": tool_call_index}
                            )
                    elif event_type == "tool_result":
                        tool_use_id = str(event.data.get("tool_use_id") or "").strip()
                        target_call: dict[str, Any] | None = None

                        if tool_use_id:
                            existing_index = tool_call_index_by_id.get(tool_use_id)
                            if existing_index is None:
                                target_call = {
                                    "id": tool_use_id,
                                    "name": event.data.get("tool_name", ""),
                                    "arguments": {},
                                    "status": "pending",
                                }
                                tool_call_index = len(all_tool_calls)
                                tool_call_index_by_id[tool_use_id] = tool_call_index
                                all_tool_calls.append(target_call)
                                assistant_display_events.append(
                                    {"type": "tool_call", "tool_call_index": tool_call_index}
                                )
                            else:
                                target_call = all_tool_calls[existing_index]
                        elif all_tool_calls:
                            target_call = all_tool_calls[-1]

                        if target_call is not None:
                            target_call["status"] = event.data.get("status", "success")
                            target_call["result"] = event.data.get("data")
                            target_call["error"] = event.data.get("error")
                    elif event_type == "workflow_stopped":
                        assistant_status_cards.append(
                            {
                                "type": "workflow_stopped",
                                "reason": event.data.get("reason"),
                                "agentType": event.data.get("agent_type"),
                                "message": event.data.get("message"),
                                "question": event.data.get("question"),
                                "context": event.data.get("context"),
                                "details": event.data.get("details"),
                                "confidence": event.data.get("confidence"),
                                "evaluation": event.data.get("evaluation"),
                            }
                        )
                    elif event_type == "iteration_exhausted":
                        assistant_status_cards.append(
                            {
                                "type": "iteration_exhausted",
                                "layer": event.data.get("layer"),
                                "iterationsUsed": event.data.get("iterations_used"),
                                "maxIterations": event.data.get("max_iterations"),
                                "reason": event.data.get("reason"),
                                "lastAgent": event.data.get("last_agent"),
                            }
                        )
                    elif event_type == "error":
                        had_stream_error = True

                model_metadata = stream_adapter.get_last_message_metadata()
                assistant_stop_reason = model_metadata.get("stop_reason")
                usage_candidate = model_metadata.get("usage")
                assistant_usage = usage_candidate if isinstance(usage_candidate, dict) else None
                if had_stream_error:
                    # Still persist partial history so the user and AI can
                    # recover context on the next turn instead of losing
                    # everything (user message + completed tool calls).
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Stream emitted an error event — saving partial history for recovery",
                        project_id=project_id,
                        user_id=user_id,
                        response_length=len(assistant_response),
                        tool_calls=len(all_tool_calls) if all_tool_calls else 0,
                    )

                # 工作流结束后队列里可能仍有未被任何消费点取走的 steering
                # （协作轮数耗尽、或最后一轮 agent 收尾窗口才到达的消息）。
                # 保存历史前统一取出，保证 /agent/steer 已确认 queued 的输入
                # 随本轮落库，而不是被随后的 cleanup 静默删除。
                # 这段 drain 覆盖的是「工作流结束 → 保存历史」这个窗口，它比
                # finally 里那段更宽：本 run 已经不会再把消息喂给模型了，若同一
                # chat session 还有别的 run 正在流式生成，这些消息本该由它消费，
                # 必须交还回队列，而不是写成本轮历史里一条挂在已结束轮次上的
                # 用户消息（那样仍在生成的 run 永远收不到这条引导）。
                already_consumed_pre_save = len(consumed_steering)
                try:
                    await get_steering_messages()
                except Exception as exc:
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Failed to drain remaining steering messages before history save",
                        session_id=session_id,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                pre_save_late_steering = list(
                    consumed_steering[already_consumed_pre_save:]
                )
                if pre_save_late_steering and await _hand_back_steering(
                    pre_save_late_steering
                ):
                    del consumed_steering[already_consumed_pre_save:]

                # Save to history。落库放进独立任务并 shield：PG 下
                # save_messages 内部走 asyncio.to_thread，工作线程一旦启动就
                # 无法取消（awaiting 侧抛 CancelledError，线程照样 commit）。
                # shield 保证取消不会打断这次落库，且 history_saved 只在真正
                # 写完后置位，取消分支据此跳过补偿保存，避免整轮历史写两遍。
                async def _save_history_once() -> str | None:
                    nonlocal history_saved
                    if not _has_assistant_payload():
                        # 与补偿保存同一不变量：本轮一个 token 都没产出（典型是
                        # 首次模型调用就报错、只发了 error 事件）时不写空
                        # assistant 行，只保留用户消息与已消费的 steering。
                        await asyncio.to_thread(
                            _append_user_messages_sync,
                            [message, *consumed_steering],
                        )
                        history_saved = True
                        return None
                    saved_id = await message_manager.save_messages(
                        session,
                        session_id,
                        message,
                        assistant_response,
                        all_tool_calls if all_tool_calls else None,
                        reasoning_content if reasoning_content else None,
                        assistant_stop_reason=assistant_stop_reason,
                        assistant_usage=assistant_usage,
                        assistant_status_cards=assistant_status_cards or None,
                        steering_messages=consumed_steering or None,
                        assistant_display_events=assistant_display_events or None,
                        assistant_routing=_routing_for_save(),
                    )
                    history_saved = True
                    return saved_id

                def _consume_history_save_error(done_task: asyncio.Task) -> None:
                    # 取消路径下可能没人再取这个任务的结果，先消费掉异常避免
                    # "exception was never retrieved"；失败仍会由下面的 await
                    # 传播成 error 事件。
                    if not done_task.cancelled():
                        done_task.exception()

                history_save_task = asyncio.create_task(_save_history_once())
                history_save_task.add_done_callback(_consume_history_save_error)
                assistant_message_id = await asyncio.shield(history_save_task)
                if pending_done_payload is not None:
                    refs_candidate = pending_done_payload.get("refs")
                    refs = refs_candidate if isinstance(refs_candidate, list) else None
                    yield done_event(
                        apply_action=(
                            str(pending_done_payload.get("apply_action"))
                            if pending_done_payload.get("apply_action") is not None
                            else None
                        ),
                        refs=refs,
                        intent=(
                            str(pending_done_payload.get("intent"))
                            if pending_done_payload.get("intent") is not None
                            else None
                        ),
                        assistant_message_id=assistant_message_id,
                        session_id=session_id,
                        file_mutated=pending_done_payload.get("file_mutated") is True,
                    ).to_sse()
            finally:
                # Clean up tool context
                ToolContext.clear_context()

        except (asyncio.CancelledError, GeneratorExit):
            stream_cancelled = True
            # 客户端断连有两种到达方式：任务被取消（CancelledError）与生成器被
            # aclose（GeneratorExit，Starlette 关闭 StreamingResponse 时触发）。
            # GeneratorExit 期间一旦在 finally 里 await 挂起，解释器会抛
            # RuntimeError("async generator ignored GeneratorExit")，收尾动作
            # 全部丢失；两者必须走同一条「后台任务收尾 + 原样上抛」的路径。
            # 前端取消/断连时事件循环正在退栈，此处任何 await 都会被同一个
            # cancel 再次打断，且共享 session 会随请求关闭；因此与
            # had_stream_error 分支同样保留部分历史（用户消息 + 已生成的
            # assistant 内容 + 已完成 tool_calls + 已消费 steering），但改用
            # 后台任务 + 独立 session 落库，随后原样向上传播取消。取消瞬间
            # 队列里可能还有已确认 queued 但未消费的 steering，落库前先在
            # 后台任务里 drain 进 consumed_steering，避免它们随队列清理丢失。
            # 注意这里不能再用 `not history_saved` 预筛：整轮历史已落库、只是
            # done 事件还没发出去时被取消，队列里仍可能有已确认 queued 的
            # steering，跳过后台任务就等于让随后的 cleanup 把它删掉。是否重写
            # 整轮由任务内部按 history_saved 判定。
            # 在取消到达的这一刻判定（后台补存还要等落库任务和 drain，时长会继续走）。
            cancel_stop_reason = partial_save_stop_reason(
                time.monotonic() - run_started_monotonic,
                AGENT_RUN_WALL_CLOCK_TIMEOUT_S,
            )
            if user_id:

                async def _drain_then_save_partial_history() -> None:
                    # 取消可能恰好落在 save_messages 期间：它被 shield 保护会
                    # 继续跑完，所以补偿保存前必须等它的真实结果。已落库时
                    # 不能整轮重写（user/steering/assistant 会写两遍），但仍要
                    # 处理这段窗口里新入队的 steering，否则它们会随队列清理
                    # 一起消失。
                    if history_save_task is not None:
                        await asyncio.wait({history_save_task})

                    already_consumed = len(consumed_steering)
                    try:
                        await get_steering_messages()
                    except Exception as exc:
                        log_with_context(
                            logger,
                            30,  # WARNING
                            "Failed to drain steering queue before cancellation save",
                            session_id=session_id,
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
                    late_steering = list(consumed_steering[already_consumed:])
                    if late_steering and await _hand_back_steering(
                        late_steering
                    ):
                        del consumed_steering[already_consumed:]
                        late_steering = []

                    if history_saved:
                        if late_steering:
                            await _finish_history_write(
                                _append_user_messages_sync, late_steering
                            )
                        return
                    await _finish_history_write(
                        _save_partial_history_sync, cancel_stop_reason
                    )

                cancellation_save_task = self._schedule_background_cleanup(
                    _drain_then_save_partial_history(),
                    description="save_partial_history_after_cancellation",
                    session_id=session_id,
                )
            raise
        except Exception as e:
            request_failed = True
            metrics.increment_counter(AGENT_REQUESTS_ERRORS)
            error_info = classify_stream_exception(e)
            log_stream_exception(
                logger,
                "Agent process_stream failed",
                e,
                error_info,
                project_id=project_id,
                user_id=user_id,
            )
            yield error_event(
                error_info.message,
                code=error_info.code,
                retryable=error_info.retryable,
                refundable=error_info.refundable,
            ).to_sse()

        finally:
            # Cleanup steering queue（只释放本 run 的持有，并发 run 不受影响）
            if stream_cancelled:

                async def _cleanup_after_cancellation_save() -> None:
                    # 先等部分历史落库（其中包含 drain 出的 steering）再释放
                    # 队列，避免清理任务抢先删掉尚未落库的消息。asyncio.wait
                    # 不会向外抛保存任务的异常/取消，失败已由其 done callback
                    # 记录，这里无论如何都要继续清理。
                    try:
                        if isinstance(cancellation_save_task, asyncio.Task):
                            await asyncio.wait({cancellation_save_task})
                        await cleanup_steering_queue_async(session_id, run_id=steering_run_id)
                    finally:
                        _stop_run_heartbeat()

                self._schedule_background_cleanup(
                    _cleanup_after_cancellation_save(),
                    description="cleanup_steering_queue_async",
                    session_id=session_id,
                )
                log_with_context(
                    logger,
                    20,
                    "Scheduled steering queue cleanup in background after stream cancellation",
                    session_id=session_id,
                )
            else:
                try:
                    # cleanup 会连消息一起删除队列：先兜底取出仍未消费的
                    # steering（正常路径在保存历史前已 drain 过，这里覆盖请求失败
                    # 未走到保存点、以及「历史刚落库到队列被删」这段窗口里新入队
                    # 的消息），保证已确认 queued 的输入不会凭空消失。
                    already_consumed = len(consumed_steering)
                    try:
                        await get_steering_messages()
                    except Exception as exc:
                        log_with_context(
                            logger,
                            30,  # WARNING
                            "Failed to drain steering queue before cleanup",
                            session_id=session_id,
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
                    late_steering = list(consumed_steering[already_consumed:])

                    # Legacy concurrent holders still receive their steering, while
                    # this run remains registered through its own final history write.
                    if late_steering and await _hand_back_steering(late_steering):
                        del consumed_steering[already_consumed:]
                        late_steering = []

                    if user_id and not history_saved:
                        # 与取消路径对等的补偿保存：本轮只要产生过任何值得保留的
                        # 内容就用独立 session 补存。旧实现额外要求
                        # consumed_steering 非空，而绝大多数请求根本没有 steering，
                        # 于是一次瞬时 DB 冲突就让整轮历史（用户消息 + 已经流式吐
                        # 给用户看的正文 + 工具调用记录）彻底消失。
                        if (
                            message.strip()
                            or consumed_steering
                            or _has_assistant_payload()
                        ):
                            try:
                                await _finish_history_write(
                                    _save_partial_history_sync, assistant_stop_reason
                                )
                            except Exception as exc:
                                log_with_context(
                                    logger,
                                    30,  # WARNING
                                    "Failed to persist partial history after stream failure",
                                    session_id=session_id,
                                    error=str(exc),
                                    error_type=type(exc).__name__,
                                )
                    elif user_id and late_steering:
                        # 整轮历史已经落库，之后才 drain 出来的 steering 单独追加为
                        # user 行：/agent/steer 已经回过 queued=True，既不能重写整轮，
                        # 更不能随队列一起删掉。
                        try:
                            await _finish_history_write(
                                _append_user_messages_sync, late_steering
                            )
                        except Exception as exc:
                            log_with_context(
                                logger,
                                30,  # WARNING
                                "Failed to persist late steering messages after history save",
                                session_id=session_id,
                                error=str(exc),
                                error_type=type(exc).__name__,
                            )
                finally:
                    try:
                        await cleanup_steering_queue_async(
                            session_id, run_id=steering_run_id
                        )
                    finally:
                        _stop_run_heartbeat()

            total_duration = int((utcnow() - start_time).total_seconds() * 1000)
            if run_report is not None:
                usage_summary = assistant_usage if isinstance(assistant_usage, dict) else {}
                run_report.update(
                    {
                        "model": DEEPSEEK_WRITING_MODEL,
                        "input_tokens": usage_summary.get("input_tokens", 0),
                        "output_tokens": usage_summary.get("output_tokens", 0),
                        "cache_read_tokens": usage_summary.get("cache_read_tokens", 0),
                        "usage_reported": bool(usage_summary),
                        "model_calls": run_meter.model_calls,
                        "max_model_calls": run_meter.max_model_calls,
                        "agent_runs": run_meter.agent_runs,
                        "llm_duration_ms": round(run_meter.llm_duration_ms, 1),
                        "duration_ms": total_duration,
                        "stop_reason": assistant_stop_reason,
                        "tool_calls_count": len(all_tool_calls),
                        "response_length": len(assistant_response),
                        "session_id": session_id,
                    }
                )
            metrics.observe_histogram(AGENT_REQUESTS_DURATION_MS, total_duration)
            completed_with_errors = request_failed or had_stream_error
            if completed_with_errors:
                completion_level = 30  # WARNING
                completion_message = "Agent process_stream completed with errors"
            elif stream_cancelled:
                completion_level = 20  # INFO: client cancellation is a normal terminal state
                completion_message = "Agent process_stream cancelled"
            else:
                completion_level = 20  # INFO
                completion_message = "Agent process_stream completed successfully (workflow)"
            log_with_context(
                logger,
                completion_level,
                completion_message,
                project_id=project_id,
                user_id=user_id,
                tool_calls_count=len(all_tool_calls),
                response_length=len(assistant_response),
                duration_ms=total_duration,
                request_failed=request_failed,
                stream_error=had_stream_error,
                stream_cancelled=stream_cancelled,
            )

# Singleton
_service: AgentService | None = None


def get_agent_service() -> AgentService:
    """Get singleton agent service."""
    global _service
    if _service is None:
        _service = AgentService()
    return _service
