"""
Agent API endpoints.

Provides FastAPI router for agent endpoints:
- POST /api/v1/agent/stream - Stream AI response with Function Calling
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
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError
from services.auth import get_current_active_user
from sqlmodel import Session

from agent.constants import should_offload_session_work
from agent.core.account_run_limit import (
    AccountRunLimitExceeded,
    acquire_account_run_slot,
)
from agent.core.events import (
    WALL_CLOCK_LIMIT,
    EventType,
    StreamEvent,
    done_event,
    error_event,
    iteration_exhausted_event,
)
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
    AGENT_COLLABORATION_MAX_ITERATIONS,
    AGENT_DRAIN_STOP_AFTER_S,
    AGENT_RUN_WALL_CLOCK_TIMEOUT_S,
    AGENT_SSE_HEARTBEAT_INTERVAL_S,
)
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import create_session, get_session
from middleware.rate_limit import (
    get_client_ip,
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
from services.usage.cost_budget import bind_model_transport_start, reset_model_transport_start
from services.usage.free_ai_network_quota import (
    FreeNetworkQuotaExceeded,
    free_network_quota_exception,
    require_canonical_request_path,
)
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
# 墙钟硬超时、本轮已经写进文件时的暂停卡（与图层 / runner 时限收尾同一张卡，前端给「继续完成」按钮）。
# reason 进历史状态卡摘要供下一轮的模型与路由看；作者看到的文案由前端 i18n 给出。
DEADLINE_PAUSE_REASON = "这一轮的时间用完了，已经写进文件的内容都保存了。任务可能还没做完，回复「继续」就能接着做。"
DEADLINE_PAUSE_REASON_EN = (
    "This round ran out of time; everything already written to files is saved. "
    "The task may not be finished; reply \"continue\" to pick it up."
)
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
    tracker: StreamBillingTracker | None = None


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
        if should_offload_session_work(session):
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


def _reserve_ai_conversation_sync(
    user_id: str, client_ip: str, reservation_id: str
) -> datetime | None:
    """Reserve quota using a fresh sync DB session."""
    with create_session() as quota_session:
        return quota_service.reserve_ai_conversation(
            quota_session,
            user_id,
            client_ip=client_ip,
            reservation_id=reservation_id,
        )


def _release_ai_conversation_sync(user_id: str, period_start: datetime, reservation_id: str) -> bool:
    """Release quota using a fresh sync DB session."""
    with create_session() as quota_session:
        return quota_service.release_ai_conversation(
            quota_session,
            user_id,
            period_start=period_start,
            reservation_id=reservation_id,
        )


async def _refund_quota(
    session: Session,
    user_id: str,
    period_start: datetime,
    reservation_id: str,
    **log_fields: Any,
) -> bool:
    """退还一次已预扣的 AI 对话额度；失败只记日志，返回是否真的退了。"""
    try:
        if should_offload_session_work(session):
            return await asyncio.to_thread(
                _release_ai_conversation_sync,
                user_id,
                period_start,
                reservation_id,
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
            reservation_id=reservation_id,
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
    reservation_id: str,
    **log_fields: Any,
) -> asyncio.Task[bool] | None:
    """在后台退还额度（断线路径不能 await）；返回排上的任务（没排上为 None）。"""
    try:
        task = asyncio.get_running_loop().create_task(
            _refund_quota(session, user_id, period_start, reservation_id, **log_fields)
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


@dataclass
class _AgentStreamSettlement:
    """Per-request SSE observation, billing settlement, and round-outcome lifecycle for ``/agent/stream``.

    Extracted from ``stream_request`` so the route handler stays readable while keeping
    every module-level hook in ``api.agent`` (quota refund, placeholder removal, drain
    timers, and monkeypatch targets in tests) unchanged.
    """

    session: Session
    user_id: str
    project_id: str
    agent_run_id: str
    charged_period_start: datetime
    lang: str
    knows_stop_contract: bool
    activation_entry: str
    run_outcome: RunOutcome
    tracker: StreamBillingTracker = field(default_factory=StreamBillingTracker)
    written_files: RoundWrittenFiles = field(default_factory=RoundWrittenFiles)
    pump: SSEStreamPump | None = None
    client_disconnected: bool = False
    user_stopped: bool = False
    unexpected_exception: bool = False
    deadline_exceeded: bool = False
    refund_scheduled: bool = False
    refund_task: asyncio.Task[bool] | None = None
    # 作者停止、这一轮被退还时移除的空白占位文件（停止路径当场移除并告诉前端）。
    removed_files: list[dict[str, str]] = field(default_factory=list)
    # (billing_reason, should_refund, refund_applied)；整个请求只结算一次。
    settlement: tuple[str, bool, bool] | None = None
    error_delivery: dict[str, Any] = field(default_factory=dict)
    partial_delivery: dict[str, Any] = field(default_factory=dict)
    completed_delivery: dict[str, Any] = field(default_factory=dict)
    known_incomplete: bool = False
    source_done: bool = False
    # 墙钟硬超时时本轮已经写进文件：按「未完成」收尾（暂停卡 + done，不是通用超时错误卡）。
    deadline_paused: bool = False
    # 这一轮已经流出过墙钟暂停卡（图层 / runner 的时限收尾）：硬超时不再补第二张。
    wall_clock_card_seen: bool = False
    # 硬超时补发的暂停卡事件数据；已经流出过墙钟暂停卡时为 None（历史里不再追加）。
    deadline_pause_card: dict[str, Any] | None = None
    # 随暂停卡一起发出的 done：它由时限本身产生，不交给计费记账（计费仍按墙钟超时判定）。
    deadline_pause_done: str | None = None
    _stop_wait_timed_out: bool = False
    _committed_seen: int = 0

    def attach_pump(self, pump: SSEStreamPump) -> None:
        self.pump = pump

    @property
    def _deadline_moved_up(self) -> bool:
        return bool(self.pump is not None and self.pump.deadline_moved_up)

    def observe_frame(self, frame: Any) -> None:
        if self.deadline_pause_done is not None and frame is self.deadline_pause_done:
            return
        self.tracker.observe(frame)
        self.written_files.observe(frame)
        if isinstance(frame, str) and frame.startswith("event: done\n"):
            self.source_done = True
        elif isinstance(frame, str) and frame.startswith("event: iteration_exhausted\n"):
            _, card_data = _parse_sse(frame)
            if isinstance(card_data, dict) and card_data.get("limit") == WALL_CLOCK_LIMIT:
                self.wall_clock_card_seen = True

    def saved_output(self, delivery: dict[str, Any]) -> bool:
        return self.written_files.saved_output(
            delivery,
            final_word_counts=self.run_outcome.saved_file_word_counts,
            any_write_succeeded=self.tracker.write_succeeded,
        )

    def confirmed_file_mutation(
        self, delivery: dict[str, Any], *, original: bool = False
    ) -> bool:
        # Preserve a mutation already confirmed by StreamAdapter. Late
        # cancellation saves can additionally prove one through the same
        # delivery receipt used by stop/disconnect/timeout outcomes. Project
        # metadata writes are saved output, but are not file mutations.
        return original or self.written_files.saved_output(
            delivery,
            final_word_counts=self.run_outcome.saved_file_word_counts,
            any_write_succeeded=self.tracker.file_written,
        )

    def with_delivery(self, frame: str) -> str:
        if self.deadline_exceeded or not frame.startswith(("event: error\n", "event: done\n")):
            return frame
        event, data = _parse_sse(frame)
        # A deadline must be delivered before asynchronous capture cleanup.
        # Never classify its still-pending file writes as unfinished here.
        if not isinstance(data, dict):
            return frame
        delivery = self.written_files.delivery(final_word_counts=self.run_outcome.saved_file_word_counts)
        if event == "done":
            data["file_mutated"] = self.confirmed_file_mutation(
                delivery, original=data.get("file_mutated") is True
            )
            self.known_incomplete = (
                self.known_incomplete
                or self.written_files.known_incomplete
                or bool(delivery.get("unwritten_files"))
            )
            if self.known_incomplete:
                self.partial_delivery = delivery
                return StreamEvent(
                    type=EventType.DONE,
                    data={
                        **data,
                        **delivery,
                        "delivery_incomplete": True,
                        "saved_output": self.saved_output(delivery),
                        **({"unexecuted_tool_text": True} if self.written_files.unexecuted_tool_text else {}),
                    },
                ).to_sse()
            # 正常收尾也带写入回执：作者对照它核对回复里说的「已修改」。
            receipt = self.written_files.completion_receipt(
                final_word_counts=self.run_outcome.saved_file_word_counts,
            )
            if not receipt:
                return frame
            self.completed_delivery = receipt
            return StreamEvent(type=EventType.DONE, data={**data, **receipt}).to_sse()
        self.error_delivery = delivery
        return StreamEvent(type=EventType.ERROR, data={**data, **delivery}).to_sse()

    def deadline_error(self) -> Any:
        return server_restarting_error() if self._deadline_moved_up else run_timeout_error()

    def deadline_terminal_frame(self) -> str | list[str] | None:
        self.deadline_exceeded = True
        # 泵已请求取消、取消还没送达生成器：先交给 process_stream 真实原因，残稿补存
        # 落库可「继续」的 stop_reason（发布交接提前的时限也一样）。
        self.run_outcome.deadline_stop_reason = RUN_DEADLINE_STOP_REASON
        if self.tracker.delivered_terminal_event:
            return None
        if not self._deadline_moved_up and self.tracker.file_written:
            # 墙钟时限、本轮已经写进文件：不是失败，是没做完。发与时限收尾同一张暂停卡，紧跟 done，
            # 都在等收尾之前（收尾可能越过平台断连）。done 只列已确认写入的文件：残稿补存还没落定，
            # 不在这里把它们说成未保存；刷新后的 stop_outcome 按最终保存回执列出。
            # 计费照旧按墙钟超时判定：暂停卡不是终止帧，这条 done 也不交给计费记账。
            # 图层 / runner 的时限收尾已经流出过暂停卡（卡发出后、done 之前撞上硬超时）时只补 done，
            # 不再发第二张卡，历史里也不追加。消费方按 limit=wall_clock 认这张卡，layer 沿用图层收尾的
            # collaboration（计费不把它当失控停止），iterations 是本轮已开始的 agent run 数。
            self.deadline_paused = True
            frames: list[str] = []
            if not self.wall_clock_card_seen:
                english = self.lang == "en"
                card = iteration_exhausted_event(
                    layer="collaboration",
                    iterations_used=max(1, self.run_outcome.runs_started),
                    max_iterations=AGENT_COLLABORATION_MAX_ITERATIONS,
                    reason=DEADLINE_PAUSE_REASON_EN if english else DEADLINE_PAUSE_REASON,
                    last_agent=None,
                    limit=WALL_CLOCK_LIMIT,
                )
                self.deadline_pause_card = dict(card.data)
                frames.append(card.to_sse())
            delivery = self.written_files.delivery(final_word_counts=self.run_outcome.saved_file_word_counts)
            delivery.pop("unwritten_files", None)
            self.deadline_pause_done = StreamEvent(
                type=EventType.DONE,
                data={
                    **done_event(
                        session_id=self.tracker.session_id,
                        file_mutated=self.confirmed_file_mutation(delivery),
                        **delivery,
                    ).data,
                    "delivery_incomplete": True,
                    "saved_output": self.saved_output(delivery),
                },
            ).to_sse()
            return [*frames, self.deadline_pause_done]
        info = self.deadline_error()
        return error_event(
            info.message,
            code=info.code,
            retryable=info.retryable,
            refundable=not self.run_outcome.provider_attempted,
        ).to_sse()

    async def wait_stop_message_id(self) -> str | None:
        # 截停后等这一轮的助手消息 id（service 写好保存回执、落库之后交回），上限 STOP_MESSAGE_ID_WAIT_S。
        # 一个请求里等过一次没等到就不再等：停止卡、结算、移除占位不叠加等待（断线路径的后台等待不走这里）。
        if self._stop_wait_timed_out:
            return await self.run_outcome.wait_message_id(0)
        message_id = await self.run_outcome.wait_message_id(STOP_MESSAGE_ID_WAIT_S)
        if message_id is None and not self.run_outcome.message_id_settled():
            self._stop_wait_timed_out = True
        return message_id

    def sync_late_write_receipts(self) -> None:
        self.tracker.note_saved_receipts(self.run_outcome.saved_file_word_counts)
        if self._committed_seen < len(self.run_outcome.committed_tool_results):
            new_results = self.run_outcome.committed_tool_results[self._committed_seen:]
            self._committed_seen = len(self.run_outcome.committed_tool_results)
            self.tracker.note_committed_tool_results(new_results)
            self.written_files.note_committed_tool_results(new_results)

    async def settle_billing(self, *, detached: bool = False) -> tuple[str, bool, bool]:
        # 结算规则见 stream_billing；这里只负责「只结算一次」。断线路径
        # （detached）不能挂起：要退还时交给后台任务，refund_applied 记为 False。
        if self.settlement is None:
            self.sync_late_write_receipts()
            billing_reason, should_refund = self.tracker.decide(
                # 暂停卡和 done 已经送到之后的断开（例如点「继续完成」中止旧连接）不是中断：按时限结算。
                client_disconnected=self.client_disconnected and not self.deadline_paused,
                unexpected_exception=self.unexpected_exception,
                deadline_exceeded=self.deadline_exceeded,
                user_stopped=self.user_stopped,
                server_restarting=self._deadline_moved_up,
                provider_attempted=self.run_outcome.provider_attempted,
                terminal_model_failure=self.run_outcome.terminal_model_failure,
            )
            refund_applied = False
            refund_fields = {
                "project_id": self.project_id,
                "agent_run_id": self.agent_run_id,
                "billing_reason": billing_reason,
            }
            if should_refund:
                # 退还一律放进后台任务：断线路径不能挂起；连接还开着时用 shield 等它，
                # 等待途中请求被取消也不会把排队中的退还一起取消。
                self.refund_task = _schedule_detached_refund(
                    self.session,
                    self.user_id,
                    self.charged_period_start,
                    self.agent_run_id,
                    **refund_fields,
                )
                self.refund_scheduled = self.refund_task is not None
                if self.refund_task is not None and not detached:
                    # 先记下「已结算」：等待途中被取消时，finally 不会再退第二次；
                    # 停止 / 断线的终态由 _finalize_stopped_round 等这个任务的真实结果。
                    self.settlement = (billing_reason, should_refund, False)
                    refund_applied = await asyncio.shield(self.refund_task)
                    self.refund_scheduled = False
                elif self.refund_task is None and not detached:
                    refund_applied = await _refund_quota(
                        self.session,
                        self.user_id,
                        self.charged_period_start,
                        self.agent_run_id,
                        **refund_fields,
                    )
            self.settlement = (billing_reason, should_refund, refund_applied)
        return self.settlement

    async def _offload(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        if should_offload_session_work(self.session):
            return await asyncio.to_thread(fn, *args, **kwargs)
        return fn(*args, **kwargs)

    async def _finalize_stopped_round(
        self,
        stop_kind: str,
        charged_now: bool,
        pending_refund: asyncio.Task[bool] | None,
        removed: list[dict[str, str]],
        delivery: dict[str, Any] | None = None,
        pause_card: dict[str, Any] | None = None,
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
        message_id = await self.run_outcome.wait_message_id(ROUND_OUTCOME_MESSAGE_ID_WAIT_S)
        self.sync_late_write_receipts()
        if remove_placeholders and message_id:
            removed = await self._offload(
                remove_empty_placeholders,
                self.project_id,
                self.run_outcome.removable(self.tracker.removable_placeholders()),
            )
        if not message_id:
            return
        receipts = self.run_outcome.saved_file_word_counts
        removed_ids = {item.get("id", "") for item in removed}
        if delivery is None:
            delivery = self.written_files.stopped_delivery(
                final_word_counts=receipts, removed_ids=removed_ids
            )
        elif removed_ids and delivery.get("unwritten_files"):
            # 当场帧（error 等）的回执是移除之前取的：已移除的文件不再列为「未确认保存」。
            unwritten = [item for item in delivery["unwritten_files"] if item.get("id") not in removed_ids]
            delivery = {key: value for key, value in delivery.items() if key != "unwritten_files"}
            if unwritten:
                delivery["unwritten_files"] = unwritten
        await self._offload(
            record_round_outcome,
            message_id,
            stop_kind=stop_kind,
            outcome=build_stop_outcome(
                stop_kind=stop_kind,
                charged=charged,
                # 作者看到的「已保存」只认确认的保存回执；计费仍按 tracker 的帧口径。
                saved_output=self.saved_output(delivery),
                removed_files=removed,
                unexecuted_tool_text=self.written_files.unexecuted_tool_text,
                **delivery,
            ),
            pause_card=pause_card,
        )

    def schedule_round_outcome(
        self,
        refund_applied: bool,
        *,
        stop_kind: str | None = None,
        delivery: dict[str, Any] | None = None,
        pause_card: dict[str, Any] | None = None,
    ) -> None:
        # 不能挂起（断线路径在 GeneratorExit 里调用）：只排后台任务。
        resolved_stop_kind = stop_kind or (
            STOP_KIND_USER_STOPPED if self.user_stopped else STOP_KIND_CLIENT_DISCONNECTED
        )
        charged_now = not refund_applied
        # 退还已在连接上落定时不再等它（也不再移除一次占位文件）；还没落定的
        # （断线、或等待途中被取消）由收尾任务等真实结果。
        pending_refund = None if refund_applied else self.refund_task
        try:
            task = asyncio.get_running_loop().create_task(
                self._finalize_stopped_round(
                    resolved_stop_kind,
                    charged_now,
                    pending_refund,
                    list(self.removed_files),
                    delivery,
                    pause_card,
                )
            )
        except RuntimeError:
            return
        _round_outcome_tasks.add(task)
        task.add_done_callback(_round_outcome_tasks.discard)

    async def remove_refunded_placeholders(self) -> list[dict[str, str]]:
        # 出错 / 失控 / 时限被退还、连接还开着的一轮同停止路径：这一轮建好还空着的文件一并移除
        # （残稿补存落定、交回消息 id 之后才删；等不到就保留）。旧标签页收不到 removed_files，不移除。
        candidates = self.run_outcome.removable(self.tracker.removable_placeholders())
        if (
            candidates
            and self.knows_stop_contract
            and await self.wait_stop_message_id()
        ):
            return await self._offload(remove_empty_placeholders, self.project_id, candidates)
        return []

    def mark_user_stopped(self) -> None:
        self.user_stopped = True
        self.run_outcome.stop_kind = STOP_KIND_USER_STOPPED
        log_with_context(
            logger,
            20,  # INFO
            "Agent stream stopped by author",
            user_id=self.user_id,
            project_id=self.project_id,
            agent_run_id=self.agent_run_id,
        )

    def make_user_stop_frame(self) -> str:
        stop_frame = StreamEvent(
            type=EventType.WORKFLOW_STOPPED,
            data={"reason": USER_STOPPED_REASON, "message": USER_STOPPED_MESSAGE},
        ).to_sse()
        self.tracker.observe(stop_frame)
        self.tracker.observe_delivery(stop_frame)
        return stop_frame

    async def settle_user_stop(self) -> tuple[str, str | None]:
        """Settle billing before building ``done(charged=...)`` for ``StreamStoppedByUser``."""
        assistant_message_id = await self.wait_stop_message_id()
        self.sync_late_write_receipts()
        billing_reason, _, refund_applied = await self.settle_billing()
        if refund_applied and assistant_message_id:
            # 消息 id 已交回说明残稿补存已落定，可以当场移除。当场等不到就是补存结果未知：
            # 保留占位，也不留给收尾任务在后台删（作者当场收到的说明不会提到它）。
            try:
                self.removed_files = await self._offload(
                    remove_empty_placeholders,
                    self.project_id,
                    self.run_outcome.removable(self.tracker.removable_placeholders()),
                )
            except Exception as exc:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Failed to remove empty placeholder files after a refunded stop",
                    project_id=self.project_id,
                    agent_run_id=self.agent_run_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                self.removed_files = []
        removed_ids = {item.get("id", "") for item in self.removed_files}
        delivery = self.written_files.stopped_delivery(
            final_word_counts=self.run_outcome.saved_file_word_counts,
            removed_ids=removed_ids,
        )
        if not assistant_message_id:
            for item in delivery.get("written_files", []):
                item.pop("words", None)
        done_frame = done_event(
            assistant_message_id=assistant_message_id,
            session_id=self.tracker.session_id,
            stop_reason=USER_STOPPED_REASON,
            charged=not refund_applied,
            produced_output=self.tracker.produced_output,
            file_mutated=self.confirmed_file_mutation(delivery),
            # 与之后落库的 stop_outcome.written_files 同一份：停止当场的说明
            # 就能写明保存了哪些文件，不必等刷新。
            **delivery,
        ).to_sse()
        self.tracker.observe(done_frame)
        self.tracker.observe_delivery(done_frame)
        refund_frame = quota_refunded_frame(billing_reason, self.removed_files) if refund_applied else None
        return done_frame, refund_frame

    def make_fallback_error_frame(self, info: Any, *, refundable: bool) -> str | None:
        if self.tracker.delivered_terminal_event:
            return None
        frame = error_event(
            info.message,
            code=info.code,
            retryable=info.retryable,
            refundable=refundable,
        ).to_sse()
        self.tracker.observe(frame)
        self.tracker.observe_delivery(frame)
        return self.with_delivery(frame)

    async def finish(self, run_key: tuple[str, str], run_report: dict[str, Any]) -> None:
        """Run ``finally`` settlement, round-outcome scheduling, activation, and billing summary log."""
        running = _running_streams.get(run_key)
        if running is not None and running.pump is self.pump:
            del _running_streams[run_key]
        # SSEStreamPump 在入队前观察源帧；客户端可能在 done 已产生、但尚未出队时断开。
        # 用源端正常终态重新核对请求级未完成事实和最新保存回执，不能依赖消费端
        # _with_delivery 是否运行过。停止 / 错误 / 时限分支仍在下方优先处理。
        if self.source_done:
            source_delivery = self.written_files.delivery(
                final_word_counts=self.run_outcome.saved_file_word_counts
            )
            source_known_incomplete = (
                self.written_files.known_incomplete
                or bool(source_delivery.get("unwritten_files"))
            )
            if source_known_incomplete:
                self.known_incomplete = True
                self.partial_delivery = source_delivery
        # done 等正常终止帧已经产生之后才到的时限没有截断这一轮（与 tracker.decide 同一口径）：
        # 按结束方式结算、记终态，不当成截停。
        deadline_cut = self.deadline_exceeded and not (
            self.tracker.saw_terminal_event and not self.tracker.saw_error_event
        )
        settlement_pending = (
            deadline_cut
            and self.client_disconnected
            and self.pump is not None
            and not self.pump.producer_stopped
        )
        if settlement_pending:
            assert self.pump is not None
            pump = self.pump

            # The timeout already reached the author. Keep cancellation/lease
            # cleanup alive; settle from its final output rather than refund early.
            async def _settle_after_deadline_cleanup() -> None:
                await pump.wait_stopped()
                reason, should, applied = await self.settle_billing(detached=True)
                log_with_context(
                    logger,
                    20,
                    "Agent deadline billing settled after disconnect",
                    agent_run_id=self.agent_run_id,
                    billing_reason=reason,
                    charged=not should,
                    refunded=applied,
                    refund_scheduled=self.refund_scheduled,
                )
                if self.deadline_paused and self.knows_stop_contract:
                    # 暂停卡已经送到、收尾中才断开：同样记「未完成」并把暂停卡补进历史。
                    self.schedule_round_outcome(
                        applied, stop_kind="incomplete", pause_card=self.deadline_pause_card
                    )

            task = asyncio.create_task(_settle_after_deadline_cleanup())
            _detached_refund_tasks.add(task)
            task.add_done_callback(_detached_refund_tasks.discard)
            billing_reason, refund_applied = (
                "server_restarting" if self._deadline_moved_up else "run_deadline_exceeded"
            ), False
        else:
            billing_reason, _, refund_applied = await self.settle_billing(
                detached=self.client_disconnected
            )
        # 终止帧（done 等）之后才断开的一轮已经正常结束：不写「已中断」终态。
        # 旧前端（不带停止约定头）断线：照旧只补存部分历史，不写终态、不移除文件。
        if self.user_stopped or (
            self.client_disconnected
            and not self.tracker.saw_terminal_event
            and not self.deadline_paused
            and self.knows_stop_contract
        ):
            self.schedule_round_outcome(refund_applied)
        elif self.error_delivery and self.knows_stop_contract:
            self.schedule_round_outcome(refund_applied, stop_kind="error", delivery=self.error_delivery)
        elif deadline_cut and not settlement_pending and self.knows_stop_contract:
            # 时限截停（含审稿后返修被打断）：超时终态已先发出，这里等残稿补存交回消息 id 后
            # 再按保存回执记「已中断」，刷新后不会像正常结束一样。stop_reason 保留原值，「继续」照常恢复。
            # 已经写进文件、发的是暂停卡时记「未完成」，并把暂停卡补进历史（刷新后同一张卡与继续按钮）。
            if self.deadline_paused:
                self.schedule_round_outcome(
                    refund_applied, stop_kind="incomplete", pause_card=self.deadline_pause_card
                )
            else:
                self.schedule_round_outcome(refund_applied, stop_kind="error")
        elif self.known_incomplete and self.knows_stop_contract:
            self.schedule_round_outcome(
                refund_applied, stop_kind="incomplete", delivery=self.partial_delivery
            )
        elif self.completed_delivery and self.knows_stop_contract:
            self.schedule_round_outcome(
                refund_applied, stop_kind=ROUND_KIND_COMPLETED, delivery=self.completed_delivery
            )

        if billing_reason == "completed":
            # 新用户漏斗：第一次有一轮 AI 回复正常结束（计费）。
            _record_activation(
                self.session,
                self.user_id,
                self.project_id,
                ACTIVATION_EVENT_FIRST_AI_REPLY_SUCCEEDED,
                self.activation_entry,
            )

        # 每次 run 一行结构化摘要：模型、token、调用次数、LLM 耗时、结束原因、计费。
        # 取消/时限路径上 process_stream 的收尾在后台进行，摘要字段可能不全。
        log_with_context(
            logger,
            20,
            "Agent stream billing evaluated",
            user_id=self.user_id,
            project_id=self.project_id,
            agent_run_id=self.agent_run_id,
            charged=None if (settlement_pending or self.refund_scheduled) else not refund_applied,
            settlement_pending=settlement_pending,
            refunded=refund_applied,
            refund_scheduled=self.refund_scheduled,
            billing_reason=billing_reason,
            provider_attempted=self.run_outcome.provider_attempted,
            terminal_model_failure=self.run_outcome.terminal_model_failure,
            saw_any_event=self.tracker.saw_any_event,
            saw_terminal_event=self.tracker.saw_terminal_event,
            delivered_terminal_event=self.tracker.delivered_terminal_event,
            saw_internal_error_event=self.tracker.saw_error_event,
            produced_output=self.tracker.produced_output,
            write_succeeded=self.tracker.write_succeeded,
            user_stopped=self.user_stopped,
            client_disconnected=self.client_disconnected,
            client_stop_contract=self.knows_stop_contract,
            first_output_ms=self.tracker.first_output_ms,
            unexpected_exception=self.unexpected_exception,
            deadline_exceeded=self.deadline_exceeded,
            **{f"run_{key}": value for key, value in run_report.items()},
        )


async def _acquire_account_slot(user_id: str, project_id: str):
    """这个账号的任务名额；拒绝时什么都还没发生：没有预扣额度，也没有模型调用。"""
    try:
        return await acquire_account_run_slot(user_id)
    except AccountRunLimitExceeded as exc:
        log_with_context(
            logger, 20, "Agent stream rejected: account task limit reached",
            user_id=user_id, project_id=project_id,
        )
        raise APIException(
            error_code=ErrorCode.ACCOUNT_RUNS_BUSY,
            status_code=409,
            detail="This account already has its maximum number of running tasks.",
        ) from exc


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


@router.post("/stream", dependencies=[Depends(require_canonical_request_path)])
async def stream_request(
    request: Request,
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
    if should_offload_session_work(session):
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

    # 预扣额度前检查账号任务名额；明确满额才拒绝，Redis 故障由名额层放行。
    account_slot = await _acquire_account_slot(user_id, body.project_id)
    try:
        # Reserve one quota unit before streaming to avoid concurrent overrun.
        # We may compensate (refund) in finally when the stream fails internally.
        # A run id names one request, so it is also this charge's reservation id.
        client_ip = get_client_ip(request)
        try:
            if should_offload_session_work(session):
                charged_period_start = await asyncio.to_thread(
                    _reserve_ai_conversation_sync,
                    current_user.id,
                    client_ip,
                    agent_run_id,
                )
            else:
                charged_period_start = quota_service.reserve_ai_conversation(
                    session,
                    current_user.id,
                    client_ip=client_ip,
                    reservation_id=agent_run_id,
                )
        except FreeNetworkQuotaExceeded as exc:
            raise free_network_quota_exception() from exc
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
            if should_offload_session_work(session):
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
            account_run_slot=account_slot,
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
            await account_slot.release()
        except SteeringSessionBusyError as exc:
            await _refund_quota(session, user_id, charged_period_start, agent_run_id)
            await account_slot.release()
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
            await account_slot.release()
        finally:
            reset_request_context(prime_ctx_tokens)
    except BaseException:
        # 还没交给响应流：process_stream 没接管（或已自己收尾）时由这里放回名额。
        await account_slot.release()
        raise

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
            # process_stream 接管后由它的收尾释放（取消路径上在残稿补存之后）；
            # 没接管时生成器已经退出，这里放回。
            if not account_slot.adopted:
                await account_slot.release()

    async def event_generator():
        agent_ctx_tokens = bind_request_context(agent_run_id=agent_run_id)
        transport_hook_token = bind_model_transport_start(run_outcome.mark_provider_attempted)
        stream_state = _AgentStreamSettlement(
            session=session,
            user_id=user_id,
            project_id=body.project_id,
            agent_run_id=agent_run_id,
            charged_period_start=charged_period_start,
            lang=lang,
            knows_stop_contract=knows_stop_contract,
            activation_entry=activation_entry,
            run_outcome=run_outcome,
        )
        # 计费看这次运行实际产出了什么：源生成器的每一帧都在入队前记账，
        # 断线时还没送到客户端的帧也算数。
        pump = SSEStreamPump(
            _primed_stream(),
            heartbeat_interval_s=AGENT_SSE_HEARTBEAT_INTERVAL_S,
            deadline_s=AGENT_RUN_WALL_CLOCK_TIMEOUT_S,
            on_item=stream_state.observe_frame,
            on_deadline=stream_state.deadline_terminal_frame,
        )
        stream_state.attach_pump(pump)
        run_key = (user_id, agent_run_id)

        _running_streams[run_key] = _RunningStream(
            pump=pump, outcome=run_outcome, tracker=stream_state.tracker
        )
        if is_draining():
            pump.end_within(AGENT_DRAIN_STOP_AFTER_S)
        try:
            async for event in pump:
                event = stream_state.with_delivery(event)
                stream_state.tracker.observe_delivery(event)
                yield event
            # 正常收尾：先结算，退还真正落库后才告诉前端「不计入」。
            billing_reason, _, refund_applied = await stream_state.settle_billing()
            if refund_applied:
                stream_state.removed_files = await stream_state.remove_refunded_placeholders()
                yield quota_refunded_frame(billing_reason, stream_state.removed_files)
        except StreamStoppedByUser:
            # 作者点了「停止生成」：运行已按取消路径收尾（后台落库部分历史）。
            # 连接还开着，补发停止卡片与 done（带这一轮助手消息的 id），结算后
            # 再告诉前端是否计入、移除了哪些空白文件。
            stream_state.mark_user_stopped()
            try:
                yield stream_state.make_user_stop_frame()
                done_frame, refund_frame = await stream_state.settle_user_stop()
                yield done_frame
                if refund_frame is not None:
                    yield refund_frame
            except (asyncio.CancelledError, GeneratorExit):
                # 收尾途中客户端断开：照断线路径结算（不能再挂起）。
                stream_state.client_disconnected = True
                raise
        except (asyncio.CancelledError, GeneratorExit):
            # 客户端断线有两种到达方式：任务被取消（CancelledError）与生成器被
            # aclose（GeneratorExit）。GeneratorExit 路径上不能 yield，也不做任何
            # 会挂起的 await。作者先点了停止、前端等不到收尾才断开时仍算主动停止。
            stream_state.client_disconnected = True
            stream_state.user_stopped = pump.stop_requested and not stream_state.deadline_exceeded
            if stream_state.user_stopped:
                run_outcome.stop_kind = STOP_KIND_USER_STOPPED
            elif (
                not (stream_state.tracker.saw_terminal_event or stream_state.deadline_paused)
                and knows_stop_contract
            ):
                # 暂停卡和 done 已经送到：残稿补存保留可「继续」的时限原因，不记成断线。
                run_outcome.stop_kind = STOP_KIND_CLIENT_DISCONNECTED
            pump.cancel()
            raise
        except StreamDeadlineExceeded:
            stream_state.deadline_exceeded = True
            info = stream_state.deadline_error()
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
                paused_with_saved_output=stream_state.deadline_paused,
            )
            fallback_frame = stream_state.make_fallback_error_frame(
                info, refundable=not run_outcome.provider_attempted
            )
            if fallback_frame is not None:
                with contextlib.suppress(Exception):
                    yield fallback_frame
            try:
                billing_reason, _, refund_applied = await stream_state.settle_billing()
                if refund_applied:
                    stream_state.removed_files = await stream_state.remove_refunded_placeholders()
                    with contextlib.suppress(Exception):
                        yield quota_refunded_frame(billing_reason, stream_state.removed_files)
            except (asyncio.CancelledError, GeneratorExit):
                stream_state.client_disconnected = True
                raise
        except Exception as exc:
            stream_state.unexpected_exception = True
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
            fallback_frame = stream_state.make_fallback_error_frame(
                info,
                refundable=run_outcome.terminal_model_failure or not run_outcome.provider_attempted,
            )
            if fallback_frame is not None:
                with contextlib.suppress(Exception):
                    yield fallback_frame
            billing_reason, _, refund_applied = await stream_state.settle_billing()
            if refund_applied:
                with contextlib.suppress(Exception):
                    yield quota_refunded_frame(billing_reason)
            raise
        finally:
            await stream_state.finish(run_key, run_report)
            reset_model_transport_start(transport_hook_token)
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
    stop_accepted = running is not None and not (
        running.tracker is not None and running.tracker.saw_terminal_event
    )
    if stop_accepted and running is not None:
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
        found=stop_accepted,
    )
    return StopResponse(stop_requested=stop_accepted)
