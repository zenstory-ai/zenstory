"""
Agent API endpoints.

Provides FastAPI router for agent endpoints:
- POST /api/v1/agent/stream - Stream AI response with Function Calling
- GET /api/v1/agent/health - Health check
- POST /api/v1/agent/suggest - Generate intelligent next-step suggestions
- POST /api/v1/agent/steer - Inject steering message into a running session
- POST /api/v1/agent/stop - Author stops a running /stream (graceful stop + billing)

计费/限流约定：所有会触发 LLM 的端点都必须同时具备「鉴权 + 项目权限 + 成本上限 + 按用户限流」，
新增端点时请照此对齐，不要只做鉴权。/stream 的成本上限是 AI 对话额度；/suggest 由前端
自动触发，不占对话额度，改用独立的每日上限；/steer 归属所在 /stream 的那次额度；/stop 不触发 LLM，只按用户限流。
"""

import asyncio
import contextlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError
from services.auth import get_current_active_user
from sqlmodel import Session

from agent.core.events import EventType, StreamEvent, done_event, error_event
from agent.core.round_outcome import (
    ROUND_KIND_COMPLETED,
    RUN_DEADLINE_STOP_REASON,
    STOP_KIND_CLIENT_DISCONNECTED,
    STOP_KIND_USER_STOPPED,
    RoundWrittenFiles,
    RunOutcome,
    build_stop_outcome,
    record_round_outcome,
    remove_empty_placeholders,
)
from agent.core.server_drain import add_drain_listener, is_draining
from agent.core.sse_pump import SSEStreamPump, StreamDeadlineExceeded, StreamStoppedByUser
from agent.core.steering import SteeringSessionBusyError
from agent.core.stream_billing import StreamBillingTracker, _parse_sse, quota_refunded_frame
from agent.core.stream_errors import (
    classify_stream_exception,
    log_stream_exception,
    run_timeout_error,
    server_restarting_error,
)
from agent.service import get_agent_service
from config.agent_runtime import (
    AGENT_DRAIN_STOP_AFTER_S,
    AGENT_RUN_WALL_CLOCK_TIMEOUT_S,
    AGENT_SSE_HEARTBEAT_INTERVAL_S,
)
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import create_session, get_session
from middleware.rate_limit import (
    require_user_beijing_daily_rate_limit,
    require_user_rate_limit,
)
from models import (
    ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT,
    ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED,
    User,
)
from services.features.activation_event_service import activation_event_service
from services.quota_service import quota_service
from utils.logger import get_logger, log_with_context
from utils.permission import verify_project_access
from utils.request_context import bind_request_context, reset_request_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/agent", tags=["Agent"])


# ==================== 限流配置 ====================
# 本 router 下所有已登录端点都会触发真实的远端 LLM 调用：
# - /stream、/suggest 直接调用 LLM；
# - /steer 向运行中的 agent 循环注入消息，间接触发额外的 LLM 轮次。
# 成本按账号结算，因此限流主体必须是 user_id：middleware.rate_limit.require_rate_limit
# 默认按客户端 IP 计数，换 IP / 走代理即可绕过，对「单账号刷接口」这类滥用无效。
STREAM_RATE_LIMIT_MAX_REQUESTS = 60
STREAM_RATE_LIMIT_WINDOW_SECONDS = 3600
SUGGEST_RATE_LIMIT_MAX_REQUESTS = 30
SUGGEST_RATE_LIMIT_WINDOW_SECONDS = 3600
# /suggest 不扣 AI 对话额度，改用独立的北京时间自然日上限兜住成本（与小时
# 限流同一套 Redis/内存后端），每日 00:00 Asia/Shanghai 切换 bucket。
# 前端每轮对话结束最多自动请求一次。
SUGGEST_DAILY_MAX_REQUESTS = 100
STEER_RATE_LIMIT_MAX_REQUESTS = 120
STEER_RATE_LIMIT_WINDOW_SECONDS = 3600
STOP_RATE_LIMIT_MAX_REQUESTS = 240
STOP_RATE_LIMIT_WINDOW_SECONDS = 3600

# 作者主动停止后，服务端在仍然打开的连接上发出的停止卡片（workflow_stopped）。
USER_STOPPED_REASON = "user_stopped"
USER_STOPPED_MESSAGE = "已停止生成。"

# 停止路径上，done 帧最多等这么久拿这一轮助手消息的 id（取消后部分历史在后台落库，
# 通常几百毫秒内完成）；等不到就不带 id，前端不会把这一轮错配到别的消息上。
STOP_MESSAGE_ID_WAIT_S = 3.0
# 停止 / 断线的结果写回助手消息前，后台任务等这一轮助手消息落库的上限。
ROUND_OUTCOME_MESSAGE_ID_WAIT_S = 30.0

# 断线路径上不能挂起：要退还的额度交给后台任务，这里持有引用防止被回收。
_detached_refund_tasks: set[asyncio.Task[Any]] = set()
# 停止 / 断线一轮的收尾（移除空白占位文件、终态写回助手消息）在后台进行。
_round_outcome_tasks: set[asyncio.Task[Any]] = set()


@dataclass
class _RunningStream:
    pump: SSEStreamPump
    outcome: RunOutcome


# 进行中的 /stream：(user_id, agent_run_id) → 该请求的 SSE 泵与收尾信息，供 /stop 找到并停止。
# 按用户命名，拿到别人的 run id 也停不了。生产只有一个 API 进程（WEB_CONCURRENCY=1）；
# 停止请求落到别的进程（如发布交接期间）时找不到运行，前端随即断开，按断线结算。
_running_streams: dict[tuple[str, str], _RunningStream] = {}


def _end_running_streams_for_drain() -> None:
    # 发布交接：进行中的生成在关机宽限内收尾（见 agent.core.server_drain）。
    for running in list(_running_streams.values()):
        running.pump.end_within(AGENT_DRAIN_STOP_AFTER_S)
    log_with_context(
        logger, 30, "Server draining: ending running agent streams before shutdown",
        running_streams=len(_running_streams), stop_after_s=AGENT_DRAIN_STOP_AFTER_S,
    )


add_drain_listener(_end_running_streams_for_drain)


async def wait_for_stream_cleanup(timeout_s: float) -> None:
    """关机前等后台的退还、终态写回与部分历史保存落定（有上限）。"""
    from agent.service import AgentService

    tasks = {
        task
        for task in (*_detached_refund_tasks, *_round_outcome_tasks, *AgentService.background_cleanup_tasks)
        if not task.done()
    }
    if tasks and timeout_s > 0:
        await asyncio.wait(tasks, timeout=timeout_s)

# 认识停止约定（/stop、quota_refunded.removed_files、断线终态）的前端在 /stream 请求上带
# 这个头。发布前打开的旧标签页不带：它断线时照旧只补存部分历史，不移除空白文件、
# 不写空的助手消息——它收不到 removed_files，文件树里会留着已删除的章节。
CLIENT_STOP_CONTRACT_HEADER = "X-Client-Stop-Contract"


# 「按登录用户限流」的依赖构造器现已下沉到 middleware.rate_limit，
# 供本 router 与 api/editor.py（/natural-polish 同样直连 LLM）共用同一份实现。
# 这里保留同名再导出，既有引用与静态自检用例无需改动。


def _should_offload_session_work(session: Session) -> bool:
    """Only offload when running against PostgreSQL production-style sessions."""
    bind = session.get_bind() if hasattr(session, "get_bind") else None
    dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
    return dialect_name == "postgresql"


def _check_ai_conversation_quota_sync(user_id: str) -> tuple[bool, int, int]:
    """Check quota using a fresh sync DB session."""
    with create_session() as quota_session:
        return quota_service.check_ai_conversation_quota(quota_session, user_id)


def _record_first_work_ai_sync(user_id: str, project_id: str) -> None:
    from services.first_work_service import observe_first_work_ai_request

    with create_session() as action_session:
        observe_first_work_ai_request(action_session, user_id, project_id)


def _activation_entry(metadata: dict[str, Any] | None) -> str:
    """作者从哪里发出这条消息（前端的 metadata.entry），只留一个短标识。"""
    entry = metadata.get("entry") if isinstance(metadata, dict) else None
    return entry[:32] if isinstance(entry, str) and entry else "typed"


def _record_activation_sync(user_id: str, project_id: str, event_name: str, entry: str) -> None:
    """新用户漏斗的观测点：独立短 session，任何失败只记日志，不影响这一轮。"""
    try:
        with create_session() as activation_session:
            activation_event_service.record_once_best_effort(
                activation_session,
                user_id=user_id,
                event_name=event_name,
                project_id=project_id,
                event_metadata={"entry": entry},
            )
    except Exception:
        logger.warning("Could not record activation event %s", event_name, exc_info=True)


def _record_activation(session: Session, user_id: str, project_id: str, event_name: str, entry: str) -> None:
    """PostgreSQL 上放进后台线程（不占请求连接、不挂起流）；SQLite（测试 / 本地）直接用请求 session。

    观测点：任何失败（包括事件循环关闭时建不了任务、回滚本身出错）都只记日志，
    不能打断流的收尾（计费日志、请求上下文复位）。
    """
    try:
        if _should_offload_session_work(session):
            task = asyncio.create_task(
                asyncio.to_thread(_record_activation_sync, user_id, project_id, event_name, entry)
            )
            _detached_refund_tasks.add(task)
            task.add_done_callback(_detached_refund_tasks.discard)
            return
        activation_event_service.record_once_best_effort(
            session,
            user_id=user_id,
            event_name=event_name,
            project_id=project_id,
            event_metadata={"entry": entry},
        )
    except Exception:
        logger.warning("Could not record activation event %s", event_name, exc_info=True)


def _reserve_ai_conversation_sync(user_id: str) -> datetime | None:
    """Reserve quota using a fresh sync DB session."""
    with create_session() as quota_session:
        return quota_service.reserve_ai_conversation(quota_session, user_id)


def _release_ai_conversation_sync(user_id: str, period_start: datetime) -> bool:
    """Release quota using a fresh sync DB session."""
    with create_session() as quota_session:
        return quota_service.release_ai_conversation(
            quota_session,
            user_id,
            period_start=period_start,
        )


async def _refund_quota(
    session: Session,
    user_id: str,
    period_start: datetime,
    **log_fields: Any,
) -> bool:
    """退还一次已预扣的 AI 对话额度；失败只记日志，返回是否真的退了。"""
    try:
        if _should_offload_session_work(session):
            return await asyncio.to_thread(
                _release_ai_conversation_sync,
                user_id,
                period_start,
            )
        # The shared request session may be in a failed transaction state from
        # the error that aborted the stream. Reset it before the compensating
        # refund; otherwise release_ai_conversation's refresh/commit raises
        # PendingRollbackError, the refund silently no-ops, and the user is
        # over-charged for a run that failed internally.
        with contextlib.suppress(Exception):
            session.rollback()
        return quota_service.release_ai_conversation(
            session,
            user_id,
            period_start=period_start,
        )
    except Exception as refund_error:
        log_with_context(
            logger,
            30,  # WARNING
            "Failed to refund AI conversation quota after stream error",
            user_id=user_id,
            error=str(refund_error),
            error_type=type(refund_error).__name__,
            **log_fields,
        )
        return False



def _schedule_detached_refund(
    session: Session,
    user_id: str,
    period_start: datetime,
    **log_fields: Any,
) -> asyncio.Task[bool] | None:
    """在后台退还额度（断线路径不能 await）；返回排上的任务（没排上为 None）。"""
    try:
        task = asyncio.get_running_loop().create_task(
            _refund_quota(session, user_id, period_start, **log_fields)
        )
    except RuntimeError as exc:  # 没有运行中的事件循环（生成器被回收时）
        log_with_context(
            logger,
            30,  # WARNING
            "Could not schedule quota refund after disconnect",
            user_id=user_id,
            error=str(exc),
            **log_fields,
        )
        return None
    _detached_refund_tasks.add(task)
    task.add_done_callback(_detached_refund_tasks.discard)
    return task


def _session_busy_exception() -> APIException:
    return APIException(
        error_code=ErrorCode.SESSION_BUSY,
        status_code=409,
        detail=(
            "This chat session already has an active generation. Stop it or wait for it to finish before sending again."
        ),
    )


# ==================== Request Models ====================


# 请求体大小上限：message / selected_text 会原样拼进 router 与每一轮 agent 的输入，
# 不设上限时一次请求就能塞进十万级 token。前端 src/lib/agentLimits.ts 的
# MAX_AGENT_MESSAGE_CHARS 必须与 AGENT_MESSAGE_MAX_CHARS 保持一致。
AGENT_MESSAGE_MAX_CHARS = 20000
AGENT_SELECTED_TEXT_MAX_CHARS = 50000
# metadata 里的列表型附件（附加文件、素材库条目、引用片段）每类最多条数。
AGENT_METADATA_MAX_LIST_ITEMS = 20
# metadata 整体序列化后的字符上限（引用片段会带正文）。
AGENT_METADATA_MAX_CHARS = 100000
_AGENT_METADATA_LIST_KEYS = (
    "attached_file_ids",
    "attached_library_materials",
    "text_quotes",
)


class AgentRequest(BaseModel):
    """Request body for agent processing."""

    project_id: str = Field(..., description="Project ID (UUID)")
    message: str = Field(
        ...,
        max_length=AGENT_MESSAGE_MAX_CHARS,
        description="User message",
    )
    session_id: str | None = Field(
        default=None,
        description="Optional session ID for steering continuity",
    )
    selected_text: str | None = Field(
        default=None,
        max_length=AGENT_SELECTED_TEXT_MAX_CHARS,
        description="Selected text",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, description="Additional metadata")
    selected_skill_ids: list[str] = Field(
        default_factory=list,
        max_length=3,
        description=(
            "Skills the user explicitly selected for this message "
            "(UserSkill.id / UserAddedSkill.id); their full instructions are injected"
        ),
    )

    @field_validator("metadata")
    @classmethod
    def _bound_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        for key in _AGENT_METADATA_LIST_KEYS:
            items = value.get(key)
            if isinstance(items, list) and len(items) > AGENT_METADATA_MAX_LIST_ITEMS:
                raise PydanticCustomError(
                    "metadata_too_many_items",
                    "metadata.{key} allows at most {limit} items",
                    {"key": key, "limit": AGENT_METADATA_MAX_LIST_ITEMS},
                )
        try:
            serialized_length = len(json.dumps(value, ensure_ascii=False, default=str))
        except (TypeError, ValueError) as exc:
            raise PydanticCustomError("metadata_not_serializable", "metadata must be JSON serializable") from exc
        if serialized_length > AGENT_METADATA_MAX_CHARS:
            # PydanticCustomError 而非 ValueError：后者会把异常对象放进 422 的
            # errors[].ctx，JSON 序列化失败后整个响应变成 500。
            raise PydanticCustomError(
                "metadata_too_large",
                "metadata exceeds {limit} characters",
                {"limit": AGENT_METADATA_MAX_CHARS},
            )
        return value


class SuggestRequest(BaseModel):
    """Request body for suggestion generation."""

    project_id: str = Field(..., description="Project ID (UUID)")
    recent_messages: list | None = Field(default=None, description="Recent conversation messages")
    count: int = Field(default=3, ge=1, le=5, description="Number of suggestions to generate")


class SuggestResponse(BaseModel):
    """Response body for suggestion generation."""

    suggestions: list[str] = Field(..., description="Generated suggestion texts")


class SteeringRequest(BaseModel):
    """Request body for steering message."""

    session_id: str = Field(..., description="Active session ID")
    message: str = Field(..., description="Steering message content")


class SteeringResponse(BaseModel):
    """Response for steering message."""

    message_id: str
    queued: bool


class StopRequest(BaseModel):
    """Request body for stopping a running /stream."""

    agent_run_id: str = Field(
        ...,
        pattern=r"^[0-9a-f]{32}$",
        description="X-Agent-Run-ID of the /stream response to stop",
    )
    keep_file_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description=(
            "Files the author's editor still holds unsaved text for (the save before "
            "stopping failed or timed out); a refunded round does not remove them as "
            "blank placeholders"
        ),
    )

    @field_validator("keep_file_ids")
    @classmethod
    def _bounded_ids(cls, value: list[str]) -> list[str]:
        return [item for item in value if isinstance(item, str) and 0 < len(item) <= 128]


class StopResponse(BaseModel):
    """Whether the run was found and asked to stop (it then ends on its own stream)."""

    stop_requested: bool


# ==================== Endpoints ====================


@router.post("/stream")
async def stream_request(
    body: AgentRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
    accept_language: str | None = Header(None, alias="Accept-Language"),
    client_stop_contract: str | None = Header(None, alias=CLIENT_STOP_CONTRACT_HEADER),
    _rate_limit: int = Depends(
        require_user_rate_limit(
            "agent_stream",
            STREAM_RATE_LIMIT_MAX_REQUESTS,
            STREAM_RATE_LIMIT_WINDOW_SECONDS,
        )
    ),
):
    """
    Process request with streaming SSE output.

    Returns Server-Sent Events:
    - thinking: Status updates
    - tool_call: AI is calling a tool
    - tool_result: Tool execution result
    - content: Generated content chunks
    - done: Processing complete
    - error: Error occurred
    """
    service = get_agent_service()
    user_id = current_user.id
    knows_stop_contract = isinstance(client_stop_contract, str) and client_stop_contract.strip() == "1"
    message_preview = body.message[:100] + "..." if len(body.message) > 100 else body.message

    # Verify project access first to avoid charging quota for unauthorized/invalid projects
    await verify_project_access(body.project_id, session, current_user)

    # If a runtime session_id is provided, ensure it is either:
    # - an existing queue owned by current user, or
    # - a brand-new queue id (KeyError -> allowed and created later).
    if body.session_id:
        from agent.core.steering import (
            get_steering_queue_for_user_async,
            has_active_runs_async,
        )

        try:
            await get_steering_queue_for_user_async(body.session_id, current_user.id)
            if await has_active_runs_async(body.session_id):
                raise _session_busy_exception()
        except KeyError:
            # New runtime session id - allow creation in service layer.
            pass
        except PermissionError as exc:
            raise APIException(
                error_code=ErrorCode.NOT_AUTHORIZED,
                status_code=403,
                detail="Not authorized to reuse this runtime session",
            ) from exc

    # Check AI conversation quota (pre-flight check for better UX).
    if _should_offload_session_work(session):
        allowed, used, limit = await asyncio.to_thread(
            _check_ai_conversation_quota_sync,
            current_user.id,
        )
    else:
        allowed, used, limit = quota_service.check_ai_conversation_quota(session, current_user.id)
    if not allowed:
        raise APIException(
            error_code=ErrorCode.QUOTA_AI_CONVERSATIONS_EXCEEDED,
            status_code=402,
            detail=f"AI conversation quota exceeded ({used}/{limit}). Please upgrade your plan.",
        )

    agent_run_id = uuid4().hex

    log_with_context(
        logger,
        20,  # INFO
        "stream_request received",
        agent_run_id=agent_run_id,
        project_id=body.project_id,
        user_id=user_id,
        message_length=len(body.message),
        message_preview=message_preview,
        has_selected_text=body.selected_text is not None,
        language=accept_language,
    )

    lang = (accept_language or "").split(",")[0].split("-")[0].strip().lower() or "zh"

    # Reserve one quota unit before streaming to avoid concurrent overrun.
    # We may compensate (refund) in finally when the stream fails internally.
    if _should_offload_session_work(session):
        charged_period_start = await asyncio.to_thread(
            _reserve_ai_conversation_sync,
            current_user.id,
        )
    else:
        charged_period_start = quota_service.reserve_ai_conversation(
            session,
            current_user.id,
        )
    if charged_period_start is None:
        raise APIException(
            error_code=ErrorCode.QUOTA_AI_CONVERSATIONS_EXCEEDED,
            status_code=402,
            detail=f"AI conversation quota exceeded ({used}/{limit}). Please upgrade your plan.",
        )

    # Only an explicitly submitted, quota-accepted task ends static onboarding.
    # An observation failure must not turn an already reserved request into an
    # unrefunded pre-stream failure; the existing stream settlement still owns it.
    try:
        if _should_offload_session_work(session):
            await asyncio.to_thread(_record_first_work_ai_sync, user_id, body.project_id)
        else:
            from services.first_work_service import observe_first_work_ai_request

            observe_first_work_ai_request(session, user_id, body.project_id)
    except Exception:
        session.rollback()
        logger.warning("Could not record first-work AI request", exc_info=True)
    # 新用户漏斗：作者主动发出的第一条 AI 消息（预扣成功）。每个账号只记一次。
    activation_entry = _activation_entry(body.metadata)
    _record_activation(
        session, user_id, body.project_id, ACTIVATION_EVENT_FIRST_AI_MESSAGE_SENT, activation_entry
    )

    # SSE 可能持续数分钟，而鉴权/权限校验的 SELECT 会让请求级 session 一直
    # 保持事务打开（Postgres 上表现为 idle in transaction，连接被整个流式
    # 期间占用）。此处主动结束事务把连接还回连接池；后续对该 session 的
    # 使用（如 finally 中的 refund）会惰性开启新事务。rollback 会 expire
    # ORM 实例，流式期间只能使用已提前取出的标量（user_id），不要再触碰
    # current_user。
    session.rollback()

    run_report: dict[str, Any] = {}
    # 停止 / 断线时路由层在取消运行前写入 stop_kind；process_stream 落库后交回助手消息 id。
    run_outcome = RunOutcome()
    stream = service.process_stream(
        project_id=body.project_id,
        user_id=user_id,
        message=body.message,
        session_id=body.session_id,
        session=session,
        selected_text=body.selected_text,
        metadata=body.metadata,
        language=lang,
        selected_skill_ids=body.selected_skill_ids,
        run_report=run_report,
        run_outcome=run_outcome,
    )

    # 先在路由里取出第一个事件：process_stream 在发出 session_started 之前
    # 解析聊天会话并原子地抢占该会话唯一的生成槽。槽被占用（上一轮仍在生成或
    # 收尾）时必须在发送 200 响应头之前转成 409 ERR_SESSION_BUSY 并退还刚预扣
    # 的额度；其余预启动异常保持原行为，由 event_generator 发终止帧并退款。
    first_event: str | None = None
    prestart_error: Exception | None = None
    prime_ctx_tokens = bind_request_context(agent_run_id=agent_run_id)
    try:
        first_event = await stream.__anext__()
    except StopAsyncIteration:
        first_event = None
    except SteeringSessionBusyError as exc:
        await _refund_quota(session, user_id, charged_period_start)
        log_with_context(
            logger,
            20,  # INFO
            "Agent stream rejected: chat session busy",
            user_id=user_id,
            project_id=body.project_id,
            agent_run_id=agent_run_id,
        )
        raise _session_busy_exception() from exc
    except Exception as exc:
        prestart_error = exc
    finally:
        reset_request_context(prime_ctx_tokens)

    async def _primed_stream():
        try:
            if prestart_error is not None:
                raise prestart_error
            if first_event is not None:
                yield first_event
            async for event in stream:
                yield event
        finally:
            await stream.aclose()

    async def event_generator():
        agent_ctx_tokens = bind_request_context(agent_run_id=agent_run_id)
        tracker = StreamBillingTracker()
        client_disconnected = False
        user_stopped = False
        unexpected_exception = False
        deadline_exceeded = False
        refund_scheduled = False
        refund_task: asyncio.Task[bool] | None = None
        # 作者停止、这一轮被退还时移除的空白占位文件（停止路径当场移除并告诉前端）。
        removed_files: list[dict[str, str]] = []
        # (billing_reason, should_refund, refund_applied)；整个请求只结算一次。
        settlement: tuple[str, bool, bool] | None = None
        # 本轮写进了正文的文件：停止 / 断线的终态列给作者（见 round_outcome.RoundWrittenFiles）。
        written_files = RoundWrittenFiles()
        error_delivery: dict[str, Any] = {}
        partial_delivery: dict[str, Any] = {}
        completed_delivery: dict[str, Any] = {}

        def _observe_frame(frame: Any) -> None:
            tracker.observe(frame)
            written_files.observe(frame)

        def _saved_output(delivery: dict[str, Any]) -> bool:
            return written_files.saved_output(
                delivery,
                final_word_counts=run_outcome.saved_file_word_counts,
                any_write_succeeded=tracker.write_succeeded,
            )

        def _confirmed_file_mutation(
            delivery: dict[str, Any], *, original: bool = False
        ) -> bool:
            # Preserve a mutation already confirmed by StreamAdapter. Late
            # cancellation saves can additionally prove one through the same
            # delivery receipt used by stop/disconnect/timeout outcomes. Project
            # metadata writes are saved output, but are not file mutations.
            return original or written_files.saved_output(
                delivery,
                final_word_counts=run_outcome.saved_file_word_counts,
                any_write_succeeded=tracker.file_written,
            )

        def _with_delivery(frame: str) -> str:
            nonlocal error_delivery, partial_delivery, completed_delivery
            if deadline_exceeded or not frame.startswith(("event: error\n", "event: done\n")):
                return frame
            event, data = _parse_sse(frame)
            # A deadline must be delivered before asynchronous capture cleanup.
            # Never classify its still-pending file writes as unfinished here.
            if not isinstance(data, dict):
                return frame
            delivery = written_files.delivery(final_word_counts=run_outcome.saved_file_word_counts)
            if event == "done":
                data["file_mutated"] = _confirmed_file_mutation(
                    delivery, original=data.get("file_mutated") is True
                )
                if delivery.get("unwritten_files"):
                    partial_delivery = delivery
                    return StreamEvent(type=EventType.DONE, data={**data, **delivery}).to_sse()
                # 正常收尾也带写入回执：作者对照它核对回复里说的「已修改」。
                receipt = written_files.completion_receipt(
                    final_word_counts=run_outcome.saved_file_word_counts,
                    any_write_succeeded=tracker.write_succeeded,
                )
                if not receipt:
                    return frame
                completed_delivery = receipt
                return StreamEvent(type=EventType.DONE, data={**data, **receipt}).to_sse()
            error_delivery = delivery
            return StreamEvent(type=EventType.ERROR, data={**data, **delivery}).to_sse()

        def _deadline_error():
            return server_restarting_error() if pump.deadline_moved_up else run_timeout_error()

        def _deadline_terminal_frame() -> str | None:
            nonlocal deadline_exceeded
            deadline_exceeded = True
            # 泵已请求取消、取消还没送达生成器：先交给 process_stream 真实原因，残稿补存
            # 落库可「继续」的 stop_reason（发布交接提前的时限也一样）。
            run_outcome.deadline_stop_reason = RUN_DEADLINE_STOP_REASON
            if tracker.delivered_terminal_event:
                return None
            info = _deadline_error()
            return error_event(
                info.message, code=info.code, retryable=info.retryable, refundable=info.refundable
            ).to_sse()

        # 计费看这次运行实际产出了什么：源生成器的每一帧都在入队前记账，
        # 断线时还没送到客户端的帧也算数。
        pump = SSEStreamPump(
            _primed_stream(),
            heartbeat_interval_s=AGENT_SSE_HEARTBEAT_INTERVAL_S,
            deadline_s=AGENT_RUN_WALL_CLOCK_TIMEOUT_S,
            on_item=_observe_frame,
            on_deadline=_deadline_terminal_frame,
        )
        run_key = (user_id, agent_run_id)

        async def _settle_billing(*, detached: bool = False) -> tuple[str, bool, bool]:
            # 结算规则见 stream_billing；这里只负责「只结算一次」。断线路径
            # （detached）不能挂起：要退还时交给后台任务，refund_applied 记为 False。
            nonlocal settlement, refund_scheduled, refund_task
            if settlement is None:
                billing_reason, should_refund = tracker.decide(
                    client_disconnected=client_disconnected,
                    unexpected_exception=unexpected_exception,
                    deadline_exceeded=deadline_exceeded,
                    user_stopped=user_stopped,
                    server_restarting=pump.deadline_moved_up,
                )
                refund_applied = False
                refund_fields = {
                    "project_id": body.project_id,
                    "agent_run_id": agent_run_id,
                    "billing_reason": billing_reason,
                }
                if should_refund:
                    # 退还一律放进后台任务：断线路径不能挂起；连接还开着时用 shield 等它，
                    # 等待途中请求被取消也不会把排队中的退还一起取消。
                    refund_task = _schedule_detached_refund(
                        session, user_id, charged_period_start, **refund_fields
                    )
                    refund_scheduled = refund_task is not None
                    if refund_task is not None and not detached:
                        # 先记下「已结算」：等待途中被取消时，finally 不会再退第二次；
                        # 停止 / 断线的终态由 _finalize_stopped_round 等这个任务的真实结果。
                        settlement = (billing_reason, should_refund, False)
                        refund_applied = await asyncio.shield(refund_task)
                        refund_scheduled = False
                    elif refund_task is None and not detached:
                        refund_applied = await _refund_quota(
                            session, user_id, charged_period_start, **refund_fields
                        )
                settlement = (billing_reason, should_refund, refund_applied)
            return settlement

        async def _offload(fn: Any, *args: Any, **kwargs: Any) -> Any:
            if _should_offload_session_work(session):
                return await asyncio.to_thread(fn, *args, **kwargs)
            return fn(*args, **kwargs)

        async def _finalize_stopped_round(
            stop_kind: str,
            charged_now: bool,
            pending_refund: asyncio.Task[bool] | None,
            removed: list[dict[str, str]],
            delivery: dict[str, Any] | None = None,
        ) -> None:
            # 停止 / 断线一轮的收尾：断线时等后台退还落定，退还了才移除空白占位文件；
            # 然后把终态写回这一轮的助手消息，刷新、重进项目后作者看得到。
            charged = charged_now
            remove_placeholders = False
            if pending_refund is not None:
                refunded = bool(await pending_refund)
                charged = not refunded
                remove_placeholders = refunded
            # service 补存完残稿（解析器尾缓冲、未闭合围栏里的整章）才交回消息 id：移除空白
            # 占位前先等它。等不到（超时 / 不会落库）就是补存结果未知，保留占位——宁可留一个
            # 空章节，也不让补存撞上已删除的文件而丢稿。
            message_id = await run_outcome.wait_message_id(ROUND_OUTCOME_MESSAGE_ID_WAIT_S)
            if remove_placeholders and message_id:
                removed = await _offload(
                    remove_empty_placeholders,
                    body.project_id,
                    run_outcome.removable(tracker.removable_placeholders()),
                )
            if not message_id:
                return
            receipts = run_outcome.saved_file_word_counts
            if delivery is None:
                delivery = written_files.stopped_delivery(
                    final_word_counts=receipts, removed_ids={item.get("id", "") for item in removed}
                )
            await _offload(
                record_round_outcome,
                message_id,
                stop_kind=stop_kind,
                outcome=build_stop_outcome(
                    stop_kind=stop_kind,
                    charged=charged,
                    # 作者看到的「已保存」只认确认的保存回执；计费仍按 tracker 的帧口径。
                    saved_output=_saved_output(delivery),
                    removed_files=removed,
                    **delivery,
                ),
            )

        def _schedule_round_outcome(refund_applied: bool, *, stop_kind: str | None = None,
                                    delivery: dict[str, Any] | None = None) -> None:
            # 不能挂起（断线路径在 GeneratorExit 里调用）：只排后台任务。
            stop_kind = stop_kind or (STOP_KIND_USER_STOPPED if user_stopped else STOP_KIND_CLIENT_DISCONNECTED)
            charged_now = not refund_applied
            # 退还已在连接上落定时不再等它（也不再移除一次占位文件）；还没落定的
            # （断线、或等待途中被取消）由收尾任务等真实结果。
            pending_refund = None if refund_applied else refund_task
            try:
                task = asyncio.get_running_loop().create_task(
                    _finalize_stopped_round(
                        stop_kind, charged_now, pending_refund, list(removed_files), delivery,
                    )
                )
            except RuntimeError:
                return
            _round_outcome_tasks.add(task)
            task.add_done_callback(_round_outcome_tasks.discard)

        _running_streams[run_key] = _RunningStream(pump=pump, outcome=run_outcome)
        if is_draining():
            pump.end_within(AGENT_DRAIN_STOP_AFTER_S)
        try:
            async for event in pump:
                event = _with_delivery(event)
                tracker.observe_delivery(event)
                yield event
            # 正常收尾：先结算，退还真正落库后才告诉前端「不计入」。
            billing_reason, _, refund_applied = await _settle_billing()
            if refund_applied:
                yield quota_refunded_frame(billing_reason)
        except StreamStoppedByUser:
            # 作者点了「停止生成」：运行已按取消路径收尾（后台落库部分历史）。
            # 连接还开着，补发停止卡片与 done（带这一轮助手消息的 id），结算后
            # 再告诉前端是否计入、移除了哪些空白文件。
            user_stopped = True
            run_outcome.stop_kind = STOP_KIND_USER_STOPPED
            log_with_context(
                logger,
                20,  # INFO
                "Agent stream stopped by author",
                user_id=user_id,
                project_id=body.project_id,
                agent_run_id=agent_run_id,
            )
            try:
                stop_frame = StreamEvent(
                    type=EventType.WORKFLOW_STOPPED,
                    data={"reason": USER_STOPPED_REASON, "message": USER_STOPPED_MESSAGE},
                ).to_sse()
                tracker.observe(stop_frame)
                tracker.observe_delivery(stop_frame)
                yield stop_frame
                assistant_message_id = await run_outcome.wait_message_id(STOP_MESSAGE_ID_WAIT_S)
                delivery = written_files.stopped_delivery(final_word_counts=run_outcome.saved_file_word_counts)
                if not assistant_message_id:
                    for item in delivery.get("written_files", []):
                        item.pop("words", None)
                done_frame = done_event(
                    assistant_message_id=assistant_message_id,
                    session_id=tracker.session_id,
                    stop_reason=USER_STOPPED_REASON,
                    produced_output=tracker.produced_output,
                    file_mutated=_confirmed_file_mutation(delivery),
                    # 与之后落库的 stop_outcome.written_files 同一份：停止当场的说明
                    # 就能写明保存了哪些文件，不必等刷新。
                    **delivery,
                ).to_sse()
                tracker.observe(done_frame)
                tracker.observe_delivery(done_frame)
                yield done_frame
                billing_reason, _, refund_applied = await _settle_billing()
                if refund_applied:
                    # 消息 id 已交回说明残稿补存已落定，可以当场移除。当场等不到就是补存结果未知：
                    # 保留占位，也不留给收尾任务在后台删（作者当场收到的说明不会提到它）。
                    if assistant_message_id:
                        removed_files = await _offload(
                            remove_empty_placeholders,
                            body.project_id,
                            run_outcome.removable(tracker.removable_placeholders()),
                        )
                    yield quota_refunded_frame(billing_reason, removed_files)
            except (asyncio.CancelledError, GeneratorExit):
                # 收尾途中客户端断开：照断线路径结算（不能再挂起）。
                client_disconnected = True
                raise
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断线有两种到达方式：任务被取消（CancelledError）与生成器被
            # aclose（GeneratorExit）。GeneratorExit 路径上不能 yield，也不做任何
            # 会挂起的 await。作者先点了停止、前端等不到收尾才断开时仍算主动停止。
            client_disconnected = True
            user_stopped = pump.stop_requested and not deadline_exceeded
            if user_stopped:
                run_outcome.stop_kind = STOP_KIND_USER_STOPPED
            elif not tracker.saw_terminal_event and knows_stop_contract:
                run_outcome.stop_kind = STOP_KIND_CLIENT_DISCONNECTED
            pump.cancel()
            raise
        except StreamDeadlineExceeded:
            deadline_exceeded = True
            info = _deadline_error()
            log_with_context(
                logger,
                30,  # WARNING
                "Agent stream ended for server drain"
                if pump.deadline_moved_up
                else "Agent stream exceeded wall-clock budget",
                user_id=user_id,
                project_id=body.project_id,
                agent_run_id=agent_run_id,
                timeout_s=AGENT_RUN_WALL_CLOCK_TIMEOUT_S,
            )
            if not tracker.delivered_terminal_event:
                frame = error_event(
                    info.message,
                    code=info.code,
                    retryable=info.retryable,
                    refundable=info.refundable,
                ).to_sse()
                tracker.observe(frame)
                tracker.observe_delivery(frame)
                with contextlib.suppress(Exception):
                    yield _with_delivery(frame)
            try:
                billing_reason, _, refund_applied = await _settle_billing()
                if refund_applied:
                    with contextlib.suppress(Exception):
                        yield quota_refunded_frame(billing_reason)
            except (asyncio.CancelledError, GeneratorExit):
                client_disconnected = True
                raise
        except Exception as exc:
            unexpected_exception = True
            # An exception escaping process_stream (e.g. a pre-stream setup
            # failure resolving the chat session or a Redis/DB outage) would
            # otherwise tear down the SSE connection with no terminal frame,
            # leaving the client's stream consumer hung on a stuck spinner.
            # Emit a terminal error frame first (only if none was sent yet)
            # so the frontend always receives a definitive end-of-stream.
            info = classify_stream_exception(exc)
            log_stream_exception(
                logger,
                "Agent stream failed outside the workflow",
                exc,
                info,
                user_id=user_id,
                project_id=body.project_id,
            )
            if not tracker.delivered_terminal_event:
                frame = error_event(
                    info.message,
                    code=info.code,
                    retryable=info.retryable,
                    refundable=info.refundable,
                ).to_sse()
                tracker.observe(frame)
                tracker.observe_delivery(frame)
                with contextlib.suppress(Exception):
                    yield _with_delivery(frame)
            billing_reason, _, refund_applied = await _settle_billing()
            if refund_applied:
                with contextlib.suppress(Exception):
                    yield quota_refunded_frame(billing_reason)
            raise
        finally:
            running = _running_streams.get(run_key)
            if running is not None and running.pump is pump:
                del _running_streams[run_key]
            # done 等正常终止帧已经产生之后才到的时限没有截断这一轮（与 tracker.decide 同一口径）：
            # 按结束方式结算、记终态，不当成截停。
            deadline_cut = deadline_exceeded and not (
                tracker.saw_terminal_event and not tracker.saw_error_event
            )
            settlement_pending = (
                deadline_cut and client_disconnected and not pump.producer_stopped
            )
            if settlement_pending:
                # The timeout already reached the author. Keep cancellation/lease
                # cleanup alive; settle from its final output rather than refund early.
                async def _settle_after_deadline_cleanup() -> None:
                    await pump.wait_stopped()
                    reason, should, applied = await _settle_billing(detached=True)
                    log_with_context(
                        logger, 20, "Agent deadline billing settled after disconnect",
                        agent_run_id=agent_run_id, billing_reason=reason,
                        charged=not should, refunded=applied, refund_scheduled=refund_scheduled,
                    )

                task = asyncio.create_task(_settle_after_deadline_cleanup())
                _detached_refund_tasks.add(task)
                task.add_done_callback(_detached_refund_tasks.discard)
                billing_reason, should_refund, refund_applied = (
                    "server_restarting" if pump.deadline_moved_up else "run_deadline_exceeded"
                ), None, False
            else:
                billing_reason, should_refund, refund_applied = await _settle_billing(
                    detached=client_disconnected
                )
            # 终止帧（done 等）之后才断开的一轮已经正常结束：不写「已中断」终态。
            # 旧前端（不带停止约定头）断线：照旧只补存部分历史，不写终态、不移除文件。
            if user_stopped or (
                client_disconnected and not tracker.saw_terminal_event and knows_stop_contract
            ):
                _schedule_round_outcome(refund_applied)
            elif error_delivery and knows_stop_contract:
                _schedule_round_outcome(refund_applied, stop_kind="error", delivery=error_delivery)
            elif deadline_cut and not settlement_pending and knows_stop_contract:
                # 时限截停（含审稿后返修被打断）：超时终态已先发出，这里等残稿补存交回消息 id 后
                # 再按保存回执记「已中断」，刷新后不会像正常结束一样。stop_reason 保留原值，「继续」照常恢复。
                _schedule_round_outcome(refund_applied, stop_kind="error")
            elif partial_delivery and knows_stop_contract:
                _schedule_round_outcome(refund_applied, stop_kind="incomplete", delivery=partial_delivery)
            elif completed_delivery and knows_stop_contract:
                _schedule_round_outcome(
                    refund_applied, stop_kind=ROUND_KIND_COMPLETED, delivery=completed_delivery
                )

            if billing_reason == "completed":
                # 新用户漏斗：第一次有一轮 AI 回复正常结束（计费）。
                _record_activation(
                    session,
                    user_id,
                    body.project_id,
                    ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED,
                    activation_entry,
                )

            # 每次 run 一行结构化摘要：模型、token、调用次数、LLM 耗时、结束原因、计费。
            # 取消/时限路径上 process_stream 的收尾在后台进行，摘要字段可能不全。
            log_with_context(
                logger,
                20,
                "Agent stream billing evaluated",
                user_id=user_id,
                project_id=body.project_id,
                agent_run_id=agent_run_id,
                charged=None if should_refund is None else not should_refund,
                settlement_pending=settlement_pending,
                refunded=refund_applied,
                refund_scheduled=refund_scheduled,
                billing_reason=billing_reason,
                saw_any_event=tracker.saw_any_event,
                saw_terminal_event=tracker.saw_terminal_event,
                delivered_terminal_event=tracker.delivered_terminal_event,
                saw_internal_error_event=tracker.saw_error_event,
                error_refundable=tracker.error_refundable,
                produced_output=tracker.produced_output,
                write_succeeded=tracker.write_succeeded,
                runaway_stop=tracker.runaway_stop,
                user_stopped=user_stopped,
                client_disconnected=client_disconnected,
                client_stop_contract=knows_stop_contract,
                first_output_ms=tracker.first_output_ms,
                unexpected_exception=unexpected_exception,
                deadline_exceeded=deadline_exceeded,
                **{f"run_{key}": value for key, value in run_report.items()},
            )
            reset_request_context(agent_ctx_tokens)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Agent-Run-ID": agent_run_id,
        },
    )


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy", "service": "agent"}


@router.post("/suggest", response_model=SuggestResponse)
async def suggest_next_action(
    body: SuggestRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
    accept_language: str | None = Header(None, alias="Accept-Language"),
    _rate_limit: int = Depends(
        require_user_rate_limit(
            "agent_suggest",
            SUGGEST_RATE_LIMIT_MAX_REQUESTS,
            SUGGEST_RATE_LIMIT_WINDOW_SECONDS,
        )
    ),
    _daily_limit: int = Depends(
        require_user_beijing_daily_rate_limit(
            "agent_suggest_daily",
            SUGGEST_DAILY_MAX_REQUESTS,
        )
    ),
):
    """
    Generate intelligent next-step suggestions.

    Returns multiple short suggestions (~15 characters each) based on:
    - Project context (outlines, characters, lores)
    - Recent conversation history

    计费：建议由前端自动触发，不占用户的 AI 对话额度（ai_conversations）；
    成本由按用户的小时限流 + 北京时间自然日上限兜住，超限返回 429。
    """
    user_id = current_user.id
    from agent.suggest_service import get_suggest_service

    # Keep authorization behavior consistent with chat/stream endpoints
    project = await verify_project_access(body.project_id, session, current_user)

    log_with_context(
        logger,
        20,  # INFO
        "suggest_next_action called",
        project_id=body.project_id,
        user_id=user_id,
        count=body.count,
        has_recent_messages=body.recent_messages is not None,
        message_count=len(body.recent_messages) if body.recent_messages else 0,
    )

    from services.first_work_service import get_first_work_state

    first_work = get_first_work_state(session, user_id)
    if first_work.project_id == body.project_id and first_work.static_guidance:
        return SuggestResponse(suggestions=[])

    service = get_suggest_service()
    lang = (accept_language or "").split(",")[0].split("-")[0].strip().lower() or "zh"

    suggestions = await service.generate_suggestions(
        session=session,
        project_id=body.project_id,
        user_id=user_id,
        recent_messages=body.recent_messages,
        count=body.count,
        language=lang,
        project_type=project.project_type,
    )

    log_with_context(
        logger,
        20,  # INFO
        "suggest_next_action completed",
        project_id=body.project_id,
        suggestion_count=len(suggestions),
    )

    return SuggestResponse(suggestions=suggestions)


@router.post("/steer", response_model=SteeringResponse)
async def inject_steering(
    body: SteeringRequest,
    current_user: User = Depends(get_current_active_user),
    _rate_limit: int = Depends(
        require_user_rate_limit(
            "agent_steer",
            STEER_RATE_LIMIT_MAX_REQUESTS,
            STEER_RATE_LIMIT_WINDOW_SECONDS,
        )
    ),
):
    """
    Inject a steering message into an active agent session.

    Steering messages allow users to provide mid-execution guidance
    to the running agent loop without interrupting the conversation.

    注：steering 消息本身不单独计 AI 对话额度（所属的 /stream 运行已扣过一次），
    但每条注入都会让运行中的 agent 循环多跑若干轮 LLM，所以仍需按用户限流。
    """
    from agent.core.steering import enqueue_steering_for_user_async

    log_with_context(
        logger,
        20,  # INFO
        "inject_steering called",
        session_id=body.session_id,
        user_id=current_user.id,
        message_length=len(body.message),
    )

    # owner 校验、「仍有会消费它的 run」与入队是一个原子操作：本轮已做完最后一次
    # drain 或已经结束时回 404，前端据此把这句话当作下一条普通消息发送，
    # 不会出现「回了 queued 却没有任何一轮读到」。
    try:
        msg = await enqueue_steering_for_user_async(
            body.session_id, current_user.id, body.message
        )
    except ValueError as exc:
        raise APIException(
            error_code=ErrorCode.BAD_REQUEST,
            status_code=400,
            detail=str(exc),
        ) from exc
    except KeyError as exc:
        raise APIException(
            error_code=ErrorCode.CHAT_SESSION_NOT_FOUND,
            status_code=404,
            detail="Agent session not found",
        ) from exc
    except PermissionError as exc:
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=403,
            detail="Not authorized to steer this session",
        ) from exc

    log_with_context(
        logger,
        20,  # INFO
        "inject_steering completed",
        session_id=body.session_id,
        message_id=msg.id,
    )

    return SteeringResponse(message_id=msg.id, queued=True)


@router.post("/stop", response_model=StopResponse)
async def stop_stream(
    body: StopRequest,
    current_user: User = Depends(get_current_active_user),
    _rate_limit: int = Depends(
        require_user_rate_limit(
            "agent_stop",
            STOP_RATE_LIMIT_MAX_REQUESTS,
            STOP_RATE_LIMIT_WINDOW_SECONDS,
        )
    ),
):
    """
    作者点了「停止生成」：运行在自己的 /stream 连接上收尾。

    找到时前端继续读流，收到 workflow_stopped(reason=user_stopped) + done，以及
    （本轮没有任何产出时）quota_refunded(kind=stopped)；找不到（已结束、别的进程、
    别人的运行）返回 stop_requested=false，前端直接断开连接。
    """
    running = _running_streams.get((current_user.id, body.agent_run_id))
    if running is not None:
        # 先记下停止原因再取消：process_stream 补存部分历史时据此落库 stop_reason。
        running.outcome.stop_kind = STOP_KIND_USER_STOPPED
        running.outcome.keep_file_ids.update(body.keep_file_ids)
        running.pump.request_stop()
    log_with_context(
        logger,
        20,  # INFO
        "Agent stop requested",
        user_id=current_user.id,
        agent_run_id=body.agent_run_id,
        found=running is not None,
    )
    return StopResponse(stop_requested=running is not None)
