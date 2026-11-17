"""
Workflow stream adapter.

Converts workflow StreamEvent objects to SSE format for frontend compatibility.
Reuses StreamProcessor for handling <file> markers in text content.
"""

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass, field
from typing import Any

from core.error_codes import ErrorCode
from models.file_model import FILE_TYPE_DRAFT, FILE_TYPE_FOLDER, FILE_TYPE_SCRIPT
from utils.logger import get_logger, log_with_context
from utils.text_metrics import count_words

from .constants import coerce_bool
from .core.author_facing_text import rewrite_not_saved_notice
from .core.events import (
    WALL_CLOCK_LIMIT,
    agent_selected_event,
    content_end_event,
    content_event,
    content_start_event,
    done_event,
    error_event,
    file_content_end_event,
    file_content_event,
    file_created_event,
    file_edit_applied_event,
    file_edit_end_event,
    file_edit_start_event,
    iteration_exhausted_event,
    skill_matched_event,
    thinking_content_event,
    tool_call_event,
    tool_result_event,
)
from .core.events import (
    StreamEvent as SSEEvent,
)
from .core.round_outcome import RUN_DEADLINE_STOP_REASON, tool_result_status
from .core.run_meter import AgentRunMeter
from .core.stream_errors import REPETITION_LOOP_STOP_REASON, error_message_for, stream_error_from_event_data
from .core.stream_processor import PREBODY_FILE_ID, StreamProcessor, StreamResult, StreamState
from .core.workflow_events import (
    CHAT_HOLD_BEGIN,
    CHAT_HOLD_DISCARD,
    FILE_BODY_RESUME_CAPTURE_ACTIVE,
    FILE_BODY_RESUME_GUARD_MISSING,
    FILE_BODY_RESUME_NOT_BOUND,
    FILE_BODY_RESUME_NOT_EMPTY,
    FILE_BODY_RESUME_UNAVAILABLE,
    FILE_BODY_RESUME_VERSION_CHANGED,
    FileBodyResume,
    StreamEventType,
)
from .core.workflow_events import StreamEvent as WorkflowStreamEvent

logger = get_logger(__name__)

# 工具结果无法解析时发给前端的 error_type（前端据此显示通用的「这一步没做成」）。
MALFORMED_TOOL_RESULT_ERROR_TYPE = "malformed_tool_result"


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


STREAM_FILE_SAVE_TIMEOUT_S = _get_positive_float_env(
    "AGENT_STREAM_FILE_SAVE_TIMEOUT_S",
    15.0,
)

# 流中止后补存 <file> 正文的后台任务：持有强引用，避免任务在完成前被 GC。
_ABORT_SAVE_TASKS: set[asyncio.Task] = set()


def is_folder_file_type(raw: Any) -> bool:
    """判断一个 file_type 值是否是文件夹（strip + lower 归一化，容忍 None/非字符串）。

    与 mcp_tools 的 pending-empty-file 守卫必须用同一套判定：一处放行、一处
    拦截会让 folder 只在其中一条路径上被排除（本次 C2 缺口的成因）。
    """
    return isinstance(raw, str) and raw.strip().lower() == FILE_TYPE_FOLDER


@dataclass
class StreamAdapterConfig:
    """Configuration for StreamAdapter."""

    project_id: str = ""
    user_id: str | None = None
    # Whether to process <file> markers in text content
    process_file_markers: bool = True
    # 流开头已发过 skill_matched 的技能（显式选择），load_skill 它们时不再重复发
    matched_skill_ids: tuple[str, ...] = ()
    # Request-level fixed-stage timing; optional for tests and non-request callers.
    run_meter: AgentRunMeter | None = None
    # UI language supplied by the request, not inferred from generated prose.
    language: str = "zh"


@dataclass
class PendingFileWrite:
    """Tracks a pending file write operation."""

    file_id: str
    file_type: str
    title: str
    # 目标文件在本次流式写入之前已有的正文长度。create_file 的幂等复用分支
    # （剧本分集重复创建）会返回一个**已经写满正文**的文件，此时任何"没等到
    # </file> 的自动补全"都不能整体覆盖它——那会用几百字残稿抹掉整集。
    original_content_length: int = 0
    # Optimistic token observed in create_file's result. A full-body finalization
    # must not overwrite any author/external/AI write committed after this point.
    baseline_updated_at: str | None = None
    # write_chapter 的整章替换：正文写完后走 FileEditor.replace_body（撤销锚点、diff 审阅），
    # 结果补发到这张卡（tool_use_id）上；card 是定位时的卡片数据。None 表示普通的新建写入。
    replace_tool_use_id: str | None = None
    replace_card: dict[str, Any] = field(default_factory=dict)


class StreamAdapter:
    """
    Adapter that converts workflow events to SSE events.

    Features:
    - Converts TextBlock to content events
    - Converts ThinkingBlock to thinking_content events
    - Converts ToolUseBlock to tool_call events
    - Converts ToolResultBlock to tool_result events
    - Reuses StreamProcessor for <file> marker handling
    - Generates file_created, file_content, file_content_end events
    - Generates file_edit_start, file_edit_applied, file_edit_end events
    """

    def __init__(self, config: StreamAdapterConfig | None = None) -> None:
        """
        Initialize the adapter.

        Args:
            config: Adapter configuration
        """
        self.config = config or StreamAdapterConfig()

        # Stream processor for file content
        self._stream_processor = StreamProcessor(
            project_id=self.config.project_id,
            user_id=self.config.user_id,
            prebody_capture=True,
        )
        # 没有待写文件时先写出、还没认领的 <file> 正文，以及它之后的对话文字（按出现顺序，
        # ("body" | "chat", 文字)）：同一回复里的 create_file 结果确认 body_in_reply 时第一段正文
        # 落进那个文件，其余在下一个边界按原顺序作为对话放出。
        self._held_reply: list[tuple[str, str]] = []
        # 扣着正文的这条回复已经到了工具结果阶段（之后再来的文字属于下一条回复）
        self._held_reply_tools_done = False

        # Pending file write (set when create_file tool returns empty file)
        self._pending_file_write: PendingFileWrite | None = None

        # Track content streaming state
        self._content_started = False

        # delete_file 调用要删的 id（按 call id）：删掉的正是在等正文的文件时，收尾这份捕获。
        self._delete_targets: dict[str, str] = {}

        # Track latest model message metadata (for persistence accuracy)
        self._last_message_stop_reason: str | None = None
        self._last_message_usage: dict[str, Any] | None = None

        # 本次流里已发过 skill_matched 的技能（load_skill 重复调用只发一次）
        self._matched_skill_ids: set[str] = set(self.config.matched_skill_ids)
        # Fatal stream error flag (e.g. file content persistence failure)
        self._fatal_stream_error = False
        # write_chapter 的整章替换没保存（版本变了 / 作者正文受保护）：模型还在同一次回复里往下写，
        # 接下来的话会当作已经换好。告诉作者之后就结束这一轮（正常 done，不是错误），不放出那些话。
        self._stop_after_refused_replace = False
        self._file_mutated = False
        self._abort_capture: tuple[str, int, asyncio.Task[bool]] | None = None
        # 停止 / 断线时还在提交的整章替换（线程里的提交不会随取消中止）：收尾时等它的真实结果。
        self._replacement_in_flight: tuple[str, str, asyncio.Future[dict[str, Any]], str | None] | None = None
        self._saved_file_word_counts: dict[str, int | None] = {}
        # 整章替换的保存回执，按 write_chapter 的 tool_use_id 记：同一章在一轮里可能先后替换多次，
        # 文件级回执只说「这个文件存过」，不能拿来证明后一次替换也存了。
        self._replacement_receipts: dict[str, int | None] = {}
        # 停止时补存的残稿属于哪一次整章替换（tool_use_id），没有则为 None。
        self._abort_capture_replace_id: str | None = None
        # 解析器已经收到 </file>、正文完整但还没开始保存的结果（解析器此时已 reset，完整正文只在这里）：
        # 发出它的帧之前登记，_complete_file_write 接手时清掉；停止 / 断线落在中间的 yield 上时
        # settle_aborted_capture 按它补存。
        self._completed_unsaved: tuple[StreamResult, PendingFileWrite | None] | None = None
        # 图发来 CHAT_HOLD begin 之后的对话文字（StreamProcessor 判定为对话的部分，
        # 正文不扣）：等图决定丢掉（discard）还是在下一个边界事件前放出。
        self._chat_hold_active = False
        self._held_chat: list[str] = []

        log_with_context(
            logger,
            20,  # INFO
            "StreamAdapter created",
            project_id=self.config.project_id,
            user_id=self.config.user_id,
        )

    # usage 键别名 → 规范键。Chat Completions 用 prompt/completion，
    # Responses/SDK 用 input/output；两族键名若同时进同一个累加字典，
    # 就只有 total_tokens 会被相加，而下游用量聚合取
    # input_tokens 时读到的是另一族的值，别名那族被静默丢弃——
    # 结果 total_tokens ≠ input + output + cache，计价口径自相矛盾。
    _USAGE_KEY_ALIASES: dict[str, str] = {
        "prompt_tokens": "input_tokens",
        "completion_tokens": "output_tokens",
    }

    @classmethod
    def _normalize_usage_keys(cls, usage: dict[str, Any]) -> dict[str, Any]:
        """把别名键折叠到规范键上（同名冲突时数值相加）。"""
        normalized: dict[str, Any] = {}
        for key, value in usage.items():
            canonical = cls._USAGE_KEY_ALIASES.get(key, key)
            previous = normalized.get(canonical)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and isinstance(previous, (int, float))
                and not isinstance(previous, bool)
            ):
                normalized[canonical] = previous + value
            else:
                normalized[canonical] = value
        return normalized

    @classmethod
    def _merge_usage(
        cls,
        accumulated: dict[str, Any] | None,
        incoming: Any,
    ) -> dict[str, Any] | None:
        """累加一次 MESSAGE_END / ROUTER_DECIDED 带来的 usage 统计。

        一轮请求里 router 与 planner/writer/quality_reviewer 各上报一次用量，
        每次只携带自己那部分 token 消耗。整轮的真实消耗是它们之和，
        直接赋值会让统计只剩最后一个 agent 的部分。

        规则：先把别名键归一（prompt_tokens→input_tokens、
        completion_tokens→output_tokens），再逐键相加；
        非数值字段以最新一次为准；incoming 不是字典时保留已累计的值。
        """
        if not isinstance(incoming, dict):
            return accumulated
        normalized_incoming = cls._normalize_usage_keys(incoming)
        if not isinstance(accumulated, dict):
            return normalized_incoming

        merged: dict[str, Any] = cls._normalize_usage_keys(accumulated)
        for key, value in normalized_incoming.items():
            previous = merged.get(key)
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and isinstance(previous, (int, float))
                and not isinstance(previous, bool)
            ):
                merged[key] = previous + value
            else:
                merged[key] = value
        return merged

    def get_last_message_metadata(self) -> dict[str, Any]:
        """Get latest model message metadata captured from MESSAGE_END events."""
        return {
            "stop_reason": self._last_message_stop_reason,
            "usage": self._last_message_usage,
        }

    def set_pending_file_write(
        self,
        file_id: str,
        file_type: str,
        title: str,
        original_content_length: int = 0,
        baseline_updated_at: str | None = None,
        *,
        replace_tool_use_id: str | None = None,
        replace_card: dict[str, Any] | None = None,
    ) -> None:
        """
        Set a pending file write operation.

        Called when create_file tool creates an empty file and
        expects content to follow with <file>...</file> markers.

        Args:
            file_id: ID of the created file
            file_type: Type of the file
            title: Title of the file
            original_content_length: 目标文件此前已有的正文长度（幂等复用分支
                会命中一个已写满的文件），>0 时禁止用截断补全的正文整体覆盖它

        Note:
            start_file_write() 会清空 StreamProcessor 的缓冲。调用方若可能在
            捕获进行中调用（正常路径就是「上一份文件还没写完又建了新档」），
            必须先用 _flush_active_capture() 把已缓冲的内容吐出去，否则那段
            叙述/正文会被静默丢弃。适配器自身的 create_file 处理已这样做。
        """
        if self.config.process_file_markers and self._stream_processor.is_active:
            log_with_context(
                logger,
                30,  # WARNING
                "Overwriting an active file capture without flushing it first",
                file_id=file_id,
                previous_state=str(self._stream_processor.state),
                previous_file_id=self._stream_processor.file_id,
            )

        self._pending_file_write = PendingFileWrite(
            file_id=file_id,
            file_type=file_type,
            title=title,
            original_content_length=max(int(original_content_length or 0), 0),
            baseline_updated_at=(
                baseline_updated_at.strip()
                if isinstance(baseline_updated_at, str) and baseline_updated_at.strip()
                else None
            ),
            replace_tool_use_id=replace_tool_use_id,
            replace_card=dict(replace_card or {}),
        )
        self._stream_processor.start_file_write(file_id)

        log_with_context(
            logger,
            20,  # INFO
            "Pending file write set",
            file_id=file_id,
            file_type=file_type,
            title=title,
            original_content_length=self._pending_file_write.original_content_length,
            has_optimistic_baseline=self._pending_file_write.baseline_updated_at is not None,
        )

    async def process_workflow_events(
        self,
        events: AsyncIterator[WorkflowStreamEvent],
    ) -> AsyncIterator[SSEEvent]:
        """
        Process workflow StreamEvent objects and yield SSE events.

        Args:
            events: AsyncIterator of workflow StreamEvent

        Yields:
            SSE StreamEvent objects for frontend consumption
        """
        try:
            async for event in events:
                if self._fatal_stream_error or self._stop_after_refused_replace:
                    break
                async for sse_event in self._process_workflow_event(event):
                    if self._stop_after_refused_replace:
                        break
                    yield sse_event
                    if self._fatal_stream_error:
                        break
                if self._fatal_stream_error or self._stop_after_refused_replace:
                    break
        except (asyncio.CancelledError, GeneratorExit):
            # 用户按停止 / 断线 / 请求级时限到期：正在 <file> 捕获中的正文只在
            # 内存缓冲里，不收尾就整段丢失，只留下 create_file 建的空文件。
            # 这里不能再 await（取消会再次打断；GeneratorExit 期间挂起会让收尾
            # 全部丢失），改为同步算出补全结果后交给后台任务落库。
            self._persist_active_capture_in_background()
            raise
        finally:
            # Deterministically close the upstream generator (e.g. when we break
            # early on a fatal stream error) so the runner's finally — which
            # cancels its background pump task — runs now instead of at GC time.
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()

        if self._stop_after_refused_replace:
            await self._roll_back_files_created_ahead()

        # Flush pending <file> state when upstream stream ends unexpectedly.
        if (
            not self._fatal_stream_error
            and self.config.process_file_markers
            and self._stream_processor.is_active
        ):
            final_result = self._stream_processor.finalize_on_stream_end()
            async for sse_event in self._emit_stream_result(final_result):
                yield sse_event
        for sse_event in self._release_held_reply():
            yield sse_event

        # An abandoned <file> write (create_file emitted an empty file but the model never
        # streamed its <file>...</file> content) leaves both the adapter tracker and the
        # ToolContext pending-empty-file guard set — finalize_on_stream_end flushes a
        # WAITING_START file as plain text without completing it, so _complete_file_write
        # (which normally clears the guard) never runs. Clear the stale state at stream end
        # so it cannot block create_file in any subsequent processing/request.
        if self._pending_file_write is not None:
            self._pending_file_write = None
            self._clear_pending_empty_file_guard()

        # 扣着的对话文字在收尾时照常放出（只有图明确 discard 的才丢）。
        for sse_event in self._release_held_chat():
            yield sse_event

        # Ensure content_end is sent if content was started
        if self._content_started:
            yield content_end_event()
            self._content_started = False

        # Fatal stream errors should not be followed by done.
        if self._fatal_stream_error:
            return

        # Emit done event
        yield done_event(file_mutated=self._file_mutated)

    async def _process_workflow_event(
        self,
        event: WorkflowStreamEvent,
    ) -> AsyncIterator[SSEEvent]:
        """
        Process a single workflow StreamEvent.

        Args:
            event: Workflow StreamEvent

        Yields:
            SSE StreamEvent objects
        """
        event_type = event.type
        data = event.data
        event_type_value = event_type.value if hasattr(event_type, "value") else str(event_type)

        if event_type == StreamEventType.CHAT_HOLD:
            for sse_event in self._handle_chat_hold(data):
                yield sse_event
            return

        if event_type == StreamEventType.FILE_BODY_RESUME:
            async for sse_event in self._resume_file_body(data.get("request")):
                yield sse_event
            return

        if event_type_value in _CHAT_HOLD_RELEASE_EVENT_TYPES:
            # 下一个 agent、交接、收尾 / 出错：被扣着的对话文字没被图丢掉，就按原顺序放出。
            for sse_event in self._release_held_chat():
                yield sse_event

        if event_type in (StreamEventType.AGENT_SELECTED, StreamEventType.MESSAGE_START):
            # A later run can fail without MESSAGE_END. Its stop reason must not
            # inherit the previous run's success; request usage remains cumulative.
            self._last_message_stop_reason = None

        if event_type == StreamEventType.TEXT:
            # Text content
            if not self._content_started:
                yield content_start_event()
                self._content_started = True
            if self._held_reply_tools_done:
                # 新的一条回复开始了：上一条回复里没被认领的正文按原顺序放出
                for sse_event in self._release_held_reply():
                    yield sse_event
            async for sse_event in self._handle_text_content(data.get("text", "")):
                yield sse_event

        elif event_type == StreamEventType.THINKING:
            # Thinking content
            thinking_text = data.get("thinking", "")
            if thinking_text:
                yield thinking_content_event(thinking_text, is_complete=False)

        elif event_type == StreamEventType.TOOL_USE:
            if data.get("status") == "complete":
                tool_id = data.get("id", "")
                tool_name = data.get("name", "")
                tool_input = data.get("input", {})
                if tool_name == "delete_file" and tool_id and isinstance(tool_input, dict):
                    target_id = tool_input.get("id")
                    if isinstance(target_id, str) and target_id:
                        self._delete_targets[tool_id] = target_id
                yield tool_call_event(tool_name, tool_input, tool_use_id=tool_id or None)

        elif event_type == StreamEventType.TOOL_RESULT:
            # Tool result from workflow nodes
            async for sse_event in self._handle_workflow_tool_result(data):
                yield sse_event

        elif event_type == StreamEventType.MESSAGE_END:
            # Message completed - capture metadata for downstream persistence
            self._last_message_stop_reason = data.get("stop_reason")
            # 多 agent 协作时每个 agent 各发一次 MESSAGE_END：usage 必须**累加**，
            # 用后来的覆盖先前的会让整轮统计只剩最后一个 agent 的消耗（计费/
            # 配额都基于它）。非字典的 usage 不清空已累计的值。
            self._last_message_usage = self._merge_usage(
                self._last_message_usage, data.get("usage")
            )

            # Agent boundary. MESSAGE_END marks the end of ONE agent's turn; the
            # same StreamAdapter/StreamProcessor is reused across the whole
            # multi-agent handoff loop. If a file write is still open here, this
            # agent created/opened a file but never closed it (no </file>), so
            # finalize it NOW — otherwise the next handed-off agent's narration
            # would keep accumulating into this agent's file and get flushed in
            # at end-of-request. finalize_on_stream_end discards control-marker
            # contaminated narration and salvages only genuine truncated prose.
            #
            # We deliberately leave the ToolContext pending-empty-file guard set:
            # it is the signal the workflow loop uses to detect an unwritten file
            # and re-run the agent to complete it (see agent/loop/engine.py). It is
            # cleared on a successful <file> completion or at full stream end.
            if data.get("stop_reason") == REPETITION_LOOP_STOP_REASON:
                # 模型在循环里被截停：正在写的正文只保留重复开始之前的部分
                self._stream_processor.trim_repeated_tail()
            if self.config.process_file_markers and self._stream_processor.is_active:
                boundary_result = self._stream_processor.finalize_on_stream_end()
                async for sse_event in self._emit_stream_result(boundary_result):
                    yield sse_event
            for sse_event in self._release_held_reply():
                yield sse_event
                # NOTE: deliberately keep self._pending_file_write and the
                # ToolContext pending-empty-file guard set. The StreamProcessor is
                # now reset (no further cross-agent capture), but the guard remains
                # the signal the workflow loop reads to re-run the writer; the
                # correction run re-arms this same capture only through the graph's
                # FILE_BODY_RESUME (see _resume_file_body). The end-of-stream
                # cleanup clears it so it cannot leak to the next request.

        elif event_type == StreamEventType.ERROR:
            # LLM 中途出错时 runner 不发 MESSAGE_END，进行中的 <file> 捕获不会在
            # agent 边界收尾：先把已串流的正文按截断补全落库（沿用覆盖保护），
            # 再以 error 帧结束，避免只留下空文件。
            async for sse_event in self._flush_before_error():
                yield sse_event
            already_failed = self._fatal_stream_error
            self._fatal_stream_error = True
            if not already_failed:
                info = stream_error_from_event_data(data)
                # 熔断的 error 文案本身就是给用户看的具体说明（已去掉 SQL 细节）；
                # 其余一律用固定文案，原始异常只在日志里。
                message = data.get("error") if info.code == ErrorCode.AGENT_TOOL_FAILURE_LIMIT else None
                # reason（如重复读取守卫的 no_progress）透传给 SSE 帧，计费据此识别失控停止。
                reason = data.get("reason") if isinstance(data, dict) else None
                yield error_event(
                    message=message if isinstance(message, str) and message else info.message,
                    code=info.code,
                    retryable=info.retryable,
                    refundable=info.refundable,
                    reason=reason if isinstance(reason, str) and reason else None,
                )

        elif event_type == StreamEventType.AGENT_SELECTED:
            # Agent selected by router
            agent_type = data.get("agent_type", "")
            agent_name = data.get("agent_name", "")
            iteration = data.get("iteration")
            max_iterations = data.get("max_iterations")
            remaining = data.get("remaining")
            yield agent_selected_event(
                agent_type, agent_name, iteration, max_iterations, remaining
            )

        elif event_type == StreamEventType.ITERATION_EXHAUSTED:
            # Iteration limit reached
            if data.get("limit") == WALL_CLOCK_LIMIT:
                # 时限暂停卡（runner 的时限收尾或图层收尾）：这一轮落可「继续」的时限原因。
                # 图层收尾时每个 run 都已 end_turn，不靠这张卡就记成正常结束、「继续」接不上。
                self._last_message_stop_reason = RUN_DEADLINE_STOP_REASON
            yield iteration_exhausted_event(
                layer=data.get("layer", ""),
                iterations_used=data.get("iterations_used", 0),
                max_iterations=data.get("max_iterations", 0),
                reason=data.get("reason", ""),
                last_agent=data.get("last_agent"),
                limit=data.get("limit"),
                unfinished_files=_card_files(data.get("unfinished_files")),
                rolled_back_files=_card_files(data.get("rolled_back_files")),
            )

        elif event_type_value in (
            StreamEventType.HANDOFF.value,
            StreamEventType.ROUTER_THINKING.value,
            StreamEventType.ROUTER_DECIDED.value,
            StreamEventType.WORKFLOW_STOPPED.value,
            StreamEventType.WORKFLOW_COMPLETE.value,
            StreamEventType.STEERING_RECEIVED.value,
            "session_started",
            "parallel_start",
            "parallel_task_start",
            "parallel_task_end",
            "parallel_end",
        ):
            if event_type_value == StreamEventType.ROUTER_DECIDED.value:
                # 路由那次独立的（非流式）LLM 调用同样烧 token，但它不发
                # MESSAGE_END，用量只能随 ROUTER_DECIDED 捎带回来。这里汇入同一个
                # 累加器，整轮统计才是完整的。
                self._last_message_usage = self._merge_usage(
                    self._last_message_usage, data.get("routing_usage")
                )
            # Pass through these events directly
            yield SSEEvent(type=event_type_value, data=data)

    async def _handle_workflow_tool_result(
        self,
        data: dict[str, Any],
    ) -> AsyncIterator[SSEEvent]:
        """
        Handle tool result from workflow nodes.

        Args:
            data: Tool result data

        Yields:
            SSE events for tool result and file operations
        """
        tool_name = ""
        tool_use_id = ""
        try:
            tool_name = data.get("name", "") if isinstance(data, dict) else ""
            tool_use_id = data.get("tool_use_id", "") if isinstance(data, dict) else ""
            raw_result = data.get("result", {}) if isinstance(data, dict) else {}

            parsed_result = self._parse_tool_result_payload(raw_result)

            # 状态口径与停止后的结果回放共用（round_outcome.tool_result_status）。
            status, result_data = tool_result_status(tool_name, parsed_result)

            error: str | None = None
            if isinstance(parsed_result, dict) and parsed_result.get("error") is not None:
                error = str(parsed_result.get("error"))

            # edit_file 全部编辑都没生效时是 error（tool_result_status 已判），这里补上人话错因。
            # 判据只看 all_failed，不看工具层 status：mcp_tools 的 _derive_edit_status 在 all_failed 时
            # 已经把 status 置成 error，若再要求 success，data 会被置 None、error 也为空，前端拿到空白失败卡。
            edit_all_failed = (
                tool_name == "edit_file"
                and isinstance(result_data, dict)
                and bool(result_data.get("all_failed"))
            )
            if edit_all_failed:
                error = error or self._summarize_edit_failures(result_data)

            # 工具失败卡片只给作者看人话：工具自带 user_message 时用它替换 error，
            # 并把 error_type / user_message 放进 data，前端按 error_type 兜底文案；
            # 原始错误（给模型看的指令、内部 id、英文异常）不进卡片。
            failure_hints = (
                self._tool_failure_hints(parsed_result, result_data) if status == "error" else {}
            )
            if failure_hints.get("user_message"):
                error = failure_hints["user_message"]

            if (
                status == "success"
                and isinstance(parsed_result, dict)
                and parsed_result.get("mutation_applied") is True
            ):
                self._file_mutated = True

            # 全失败的 edit_file 仍要把 data 发出去：failed_edits 是用户判断
            # 「哪几处没改成、为什么」的唯一依据，丢掉它卡片就是空白的。
            emit_data = result_data if (status == "success" or edit_all_failed) else None
            emit_data = self._client_safe_skill_payload(tool_name, emit_data)
            if failure_hints:
                emit_data = {**emit_data, **failure_hints} if isinstance(emit_data, dict) else failure_hints

            async for sse_event in self._end_reply_text():
                yield sse_event
            if self._held_reply:
                # 待认领的正文等同一回复里的 create_file（前面可能还有别的工具调用）；
                # 下一条回复的文字一来就不会再有人认领了（见 TEXT 分支）
                self._held_reply_tools_done = True

            # Emit tool_result event
            yield tool_result_event(
                tool_name=tool_name,
                status=status,
                data=emit_data,
                error=error,
                tool_use_id=tool_use_id or None,
            )

            if tool_name == "delete_file":
                deleted_id = self._delete_targets.pop(tool_use_id, None)
                pending = self._pending_file_write
                if (
                    deleted_id is not None
                    and pending is not None
                    and pending.file_id == deleted_id
                    and status == "success"
                    and isinstance(parsed_result, dict)
                    and parsed_result.get("mutation_applied") is True
                ):
                    # 模型取消了在等正文的那份文件：同一 run 里之后的 <file> 不能再落进已删文件。
                    # 等待中的缓冲按既有语义回到对话；待写守卫由 tools_adapter 按同一删除结果清除。
                    if self._stream_processor.file_id == deleted_id:
                        async for event in self._flush_active_capture():
                            yield event
                    self._pending_file_write = None

            # Handle file creation
            if tool_name == "create_file" and status == "success":
                async for event in self._handle_create_file_result(result_data):
                    yield event
                file_data = result_data if isinstance(result_data, dict) else {}
                if file_data:
                    file_id = file_data.get("id", "")
                    file_type = file_data.get("file_type", "")
                    title = file_data.get("title", "")
                    if file_id:
                        yield file_created_event(file_id, file_type, title)
                async for event in self._bind_reply_prebody(result_data):
                    yield event

            # write_chapter：新建一章同 create_file；替换已有的一章时等 <file> 正文，写完走替换路径。
            if tool_name == "write_chapter" and status == "success" and isinstance(result_data, dict):
                action = result_data.get("action")
                if action == "created":
                    async for event in self._handle_create_file_result(result_data):
                        yield event
                    file_id = result_data.get("id", "")
                    if file_id:
                        yield file_created_event(file_id, result_data.get("file_type", ""), result_data.get("title", ""))
                    async for event in self._bind_reply_prebody(result_data):
                        yield event
                elif action == "replaced" and result_data.get("awaiting_body") is True:
                    async for event in self._arm_chapter_replace(result_data, tool_use_id):
                        yield event
                    # 本条回复先写出的正文（body_in_reply）就是这一章的替换稿：走整章替换的落库路径。
                    async for event in self._bind_reply_prebody(result_data):
                        yield event

            # Handle file edit。即便状态被降级为 error（全部编辑失败），
            # 也要把逐条失败事件发出去，让用户看到「哪几处没改成」。
            if tool_name == "edit_file":
                async for event in self._handle_edit_file_result(result_data):
                    yield event

            # 技能用量来自 load_skill 工具调用（用量记录在工具内完成），这里只发前端事件；
            # 同一技能在一次流里只发一次。
            if tool_name == "load_skill" and status == "success" and isinstance(result_data, dict):
                skill_id = str(result_data.get("skill_id") or "")
                if skill_id and skill_id not in self._matched_skill_ids:
                    self._matched_skill_ids.add(skill_id)
                    yield skill_matched_event(
                        skill_id=skill_id,
                        skill_name=str(result_data.get("skill_name") or ""),
                        matched_trigger="load_skill",
                    )
        except Exception as exc:
            log_with_context(
                logger,
                30,  # WARNING
                "Failed to parse workflow tool result",
                tool_name=tool_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            # 解析异常只进日志；卡片拿到的是固定的 error_type，由前端给出通用说法。
            yield tool_result_event(
                tool_name=tool_name,
                status="error",
                data={"error_type": MALFORMED_TOOL_RESULT_ERROR_TYPE},
                error=MALFORMED_TOOL_RESULT_ERROR_TYPE,
                tool_use_id=tool_use_id or None,
            )

    @staticmethod
    def _tool_failure_hints(parsed_result: Any, result_data: Any) -> dict[str, str]:
        """取出工具失败结果里给作者看的 user_message 与 error_type（顶层优先，其次 data）。"""
        hints: dict[str, str] = {}
        for source in (parsed_result, result_data):
            if not isinstance(source, dict):
                continue
            for key in ("error_type", "user_message"):
                value = source.get(key)
                if key not in hints and isinstance(value, str) and value.strip():
                    hints[key] = value.strip()
        return hints

    @staticmethod
    def _client_safe_skill_payload(tool_name: str, data: Any) -> Any:
        """
        技能工具结果发给前端前瘦身：完整方法/资源正文只给模型（模型拿的是工具原始输出，
        不经过这里），浏览器卡片只需要技能名、路径和大小。
        """
        if not isinstance(data, dict):
            return data
        if tool_name == "load_skill":
            resources = data.get("resources")
            instructions = data.get("instructions")
            return {
                "skill_id": data.get("skill_id"),
                "skill_name": data.get("skill_name"),
                "source": data.get("source"),
                "instructions_chars": len(instructions) if isinstance(instructions, str) else 0,
                "resources": [
                    {"path": item.get("path"), "size": item.get("size")}
                    for item in resources
                    if isinstance(item, dict)
                ] if isinstance(resources, list) else [],
            }
        if tool_name == "read_skill_resource":
            content = data.get("content")
            return {
                "skill_name": data.get("skill_name"),
                "path": data.get("path"),
                "size": len(content.encode("utf-8")) if isinstance(content, str) else 0,
            }
        return data

    def _parse_tool_result_payload(self, raw_result: Any) -> Any:
        """Parse MCP tool_result payload with defensive fallbacks."""
        import json

        # MCP canonical format: {"content":[{"type":"text","text":"...json..."}]}
        result_text = self._extract_tool_result_text(raw_result)
        if result_text:
            try:
                return json.loads(result_text)
            except json.JSONDecodeError:
                return {"raw": result_text}

        # Tolerate non-canonical payloads from workflow adapters/mocks.
        if isinstance(raw_result, dict):
            if "status" in raw_result or "error" in raw_result or "data" in raw_result:
                return raw_result
            return {}
        if raw_result is None:
            return {}

        return {"raw": str(raw_result)}

    def _extract_tool_result_text(self, raw_result: Any) -> str:
        """Extract text payload from MCP result format safely."""
        if not isinstance(raw_result, dict):
            return ""

        content_list = raw_result.get("content")
        if not isinstance(content_list, list):
            return ""

        for item in content_list:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    return text
                if text is not None:
                    return str(text)

        return ""

    async def _handle_create_file_result(
        self,
        result_data: Any,
    ) -> AsyncIterator[SSEEvent]:
        """Set up pending file write if a content-bearing file was created empty.

        Yields SSE events for the previous capture when a new pending write
        displaces one that is still buffering.
        """
        file_data = result_data if isinstance(result_data, dict) else {}
        if not file_data:
            return

        file_id = file_data.get("id", "")
        if not file_id:
            return

        raw_content = file_data.get("content")
        content = raw_content if isinstance(raw_content, str) else ""
        # 是否进入 <file> 捕获等待，以 create_file 的显式字段 reused_existing 为准，
        # 不再依赖"content 是否为空"这种隐式信号：剧本分集的幂等复用分支命中的是
        # 一个已经写满正文的文件，它同样需要进入捕获（模型接下来会流式写新正文），
        # 但必须把"原本非空"记下来，供截断补全时的覆盖保护使用。
        reused_existing = coerce_bool(file_data.get("reused_existing"))
        if content and not reused_existing:
            return

        original_content_length = self._resolve_original_content_length(
            file_data, content
        )

        file_type = file_data.get("file_type", "")
        # 作者只要建文件的空文件同样不会有正文：不进入捕获，后面的回复文字照常发出。
        if coerce_bool(file_data.get("empty_by_request")):
            return
        # folder 是纯容器节点，永远不会收到 <file>…</file> 正文。给它开启流式
        # 捕获会把后续叙述全部扣在 WAITING_START 缓冲里（前端长时间无输出），
        # 并让流结束时的 at_eof 兜底把叙述当正文 update_file 写进文件夹行。
        # 与 mcp_tools 的 pending-empty-file 守卫保持同一套 folder 判定。
        if is_folder_file_type(file_type):
            log_with_context(
                logger,
                20,  # INFO
                "Skipping pending file write for folder node",
                file_id=file_id,
                file_type=file_type,
                title=file_data.get("title", ""),
            )
            return

        # 上一段捕获可能仍在缓冲（WAITING_START 的叙述 / WRITING 的正文）：
        # 先收尾并把内容吐出去，再切到新文件，避免 start_file_write() 清空
        # 缓冲造成静默丢失。
        async for sse_event in self._flush_active_capture():
            yield sse_event
        if self._fatal_stream_error:
            return

        self.set_pending_file_write(
            file_id,
            file_type,
            file_data.get("title", ""),
            original_content_length=original_content_length,
            baseline_updated_at=file_data.get("updated_at"),
        )

    async def _arm_chapter_replace(self, result_data: dict[str, Any], tool_use_id: str) -> AsyncIterator[SSEEvent]:
        """write_chapter 定位到已有的一章：等下一次回复的 <file> 正文，写完整章替换它。

        编辑器照常实时显示正文（file_created → file_content）；截断补全或中途停止时不覆盖原文
        （original_content_length > 0 的覆盖保护）；落库走 _complete_chapter_replace。
        """
        file_id = str(result_data.get("id") or "")
        if not file_id:
            return
        async for sse_event in self._flush_active_capture():
            yield sse_event
        if self._fatal_stream_error:
            return
        card = {
            key: value
            for key, value in result_data.items()
            if key not in ("awaiting_body", "next_step", "content", "original_content_length")
        }
        self.set_pending_file_write(
            file_id,
            str(result_data.get("file_type") or ""),
            str(result_data.get("title") or ""),
            original_content_length=self._resolve_original_content_length(result_data, ""),
            baseline_updated_at=result_data.get("updated_at"),
            replace_tool_use_id=tool_use_id or None,
            replace_card=card,
        )
        yield file_created_event(file_id, str(result_data.get("file_type") or ""), str(result_data.get("title") or ""))

    async def _roll_back_files_created_ahead(self) -> None:
        """整章替换被拒、这一轮提前结束：模型跑在前面已经建好、还没写正文的空文件撤销掉。

        这些文件的结果还没交给适配器（作者没看到它们），只回滚本请求新建、版本未变的空文件；
        复用的或之后被动过的文件保留。
        """
        from agent.core.empty_files import rollback_unfinished_empty_files
        from agent.tools.mcp_tools import ToolContext

        # 关闭与取条目是同一步：之后才建好的空文件由建它的工具自己撤销（见 _create_file_sync）。
        targets = [entry for entry in ToolContext.close_pending_body_files() if entry.get("updated_at")]
        if not targets:
            return
        rolled_back = await asyncio.to_thread(rollback_unfinished_empty_files, targets)
        for file_id in rolled_back:
            ToolContext.clear_pending_empty_file(file_id)
        log_with_context(
            logger,
            20,  # INFO
            "Rolled back empty files created ahead of a refused chapter replacement",
            rolled_back_count=len(rolled_back),
            candidate_count=len(targets),
        )

    async def _flush_before_error(self) -> AsyncIterator[SSEEvent]:
        """出错帧之前：收尾进行中的捕获与暂扣的回复；还有待写的整章替换（没写成或截断补全被拒）时，
        它不会再重试，出错帧之后适配器不再处理事件、引擎收尾的「未保存」说明到不了作者，这里如实说一次。"""
        async for sse_event in self._flush_capture_and_held_reply():
            yield sse_event
        pending = self._pending_file_write
        if not self._fatal_stream_error and pending is not None and pending.replace_card:
            for sse_event in self._rewrite_not_saved_events():
                yield sse_event

    def _rewrite_not_saved_events(self) -> list[SSEEvent]:
        """「改写没有保存、原文不变」的固定说明（与引擎作废整章替换时同一份文字）。"""
        events: list[SSEEvent] = []
        if not self._content_started:
            events.append(content_start_event())
            self._content_started = True
        events.append(content_event("\n\n" + rewrite_not_saved_notice(self.config.language.lower().startswith("en"))))
        return events

    async def _complete_chapter_replace(self, result: StreamResult, pending: PendingFileWrite) -> AsyncIterator[SSEEvent]:
        """整章替换的正文写完：用 replace_body 提交，发 diff 审阅事件，并把结果补发到同一张卡上。"""
        file_id = pending.file_id
        outcome, replaced = await self._save_chapter_replacement(
            file_id, result.final_content, pending, complete=not result.auto_completed
        )
        card = dict(pending.replace_card)
        self._pending_file_write = None
        self._clear_pending_empty_file_guard(file_id)
        if outcome == "failed":
            self._fatal_stream_error = True
            yield error_event(
                message=error_message_for(ErrorCode.AGENT_FILE_SAVE_FAILED),
                code=ErrorCode.AGENT_FILE_SAVE_FAILED,
                retryable=True,
            )
            return
        yield file_content_end_event(file_id)
        if outcome == "refused" or replaced is None:
            warning_text = "\n\n" + (
                "This chapter rewrite was not saved. The original text is unchanged. "
                "Stopping here; check the chapter and continue when ready."
                if self.config.language.lower().startswith("en")
                else "这一章的整章替换没有保存，原文保持不变。这一轮先停在这里，确认这一章后再继续。"
            )
            if not self._content_started:
                yield content_start_event()
                self._content_started = True
            yield content_event(warning_text)
            card.update({"mutation_applied": False, "body_saved": False})
            if pending.replace_tool_use_id:
                yield tool_result_event(
                    tool_name="write_chapter", status="success", data=card, tool_use_id=pending.replace_tool_use_id
                )
            log_with_context(logger, 20, "Stopping the round after a refused chapter replacement", file_id=file_id)
            self._stop_after_refused_replace = True
            return
        original = str(replaced.get("original_content") or "")
        new_content = str(replaced.get("new_content") or "")
        mutated = replaced.get("mutation_applied") is True
        title = str(replaced.get("title") or pending.title)
        file_type = str(replaced.get("file_type") or pending.file_type)
        yield file_edit_start_event(file_id, title, 1, file_type=file_type)
        yield file_edit_applied_event(
            file_id=file_id,
            edit_index=0,
            op="replace",
            old_preview=original[:50] or None,
            new_preview=new_content[:50] or None,
            success=True,
            error=None,
        )
        yield file_edit_end_event(
            file_id=file_id,
            edits_applied=1 if mutated else 0,
            new_length=int(replaced.get("new_length") or 0),
            # 原稿与新稿一起给前端：进入逐段 diff 审阅（与作者在编辑器里审 AI 改稿同一套）。
            new_content=new_content if mutated else None,
            original_content=original if mutated else None,
            file_type=file_type,
            title=title,
        )
        chapter = card.get("chapter")
        unit = "集" if file_type == FILE_TYPE_SCRIPT else "章"
        card.update(
            {
                "mutation_applied": mutated,
                "body_saved": True,
                "new_length": replaced.get("new_length"),
                "new_word_count": replaced.get("new_word_count"),
                "previous_length": replaced.get("previous_length"),
                "previous_word_count": replaced.get("previous_word_count"),
                "updated_at": replaced.get("updated_at"),
                "result": (
                    f"覆盖了已有的第{chapter}{unit}（原{replaced.get('previous_word_count')}字），"
                    f"现在{replaced.get('new_word_count')}字。"
                ),
            }
        )
        if isinstance(replaced.get("undo"), dict):
            card["undo"] = replaced["undo"]
        if pending.replace_tool_use_id:
            # 同一个 tool_use_id 再发一次结果：落库的卡片与前端的卡片都更新成真实的替换结果（含撤销锚点）。
            yield tool_result_event(
                tool_name="write_chapter", status="success", data=card, tool_use_id=pending.replace_tool_use_id
            )

    async def _save_chapter_replacement(
        self, file_id: str, content: str, pending: PendingFileWrite, *, complete: bool = False
    ) -> tuple[str, dict[str, Any] | None]:
        """("saved", 结果) / ("refused", None)：版本变了或作者正文受保护 / ("failed", None)：其他失败。"""
        from agent.tools.author_edit_guard import AuthorContentProtectionError
        from agent.tools.file_ops.crud import ConcurrentFileWriteError

        run_meter = self.config.run_meter
        timing = run_meter.measure_stage("file_save") if run_meter is not None else contextlib.nullcontext()
        save = asyncio.ensure_future(
            asyncio.wait_for(
                asyncio.to_thread(self._replace_file_body_sync, file_id, content, pending.baseline_updated_at),
                timeout=STREAM_FILE_SAVE_TIMEOUT_S,
            )
        )
        try:
            with timing:
                replaced = await asyncio.shield(save)
        except asyncio.CancelledError:
            # 停止 / 断线 / 时限取消了这一轮，但线程里的提交照常进行：记下它，
            # settle_aborted_capture 等它的真实结果写进保存回执（计费与停止说明都按回执）。
            self._replacement_in_flight = (file_id, content, save, pending.replace_tool_use_id)
            _ABORT_SAVE_TASKS.add(save)
            save.add_done_callback(_ABORT_SAVE_TASKS.discard)
            save.add_done_callback(lambda done: done.cancelled() or done.exception())
            raise
        except (ConcurrentFileWriteError, AuthorContentProtectionError) as exc:
            log_with_context(
                logger, 30, "Chapter replacement refused", file_id=file_id, error_type=type(exc).__name__
            )
            self._saved_file_word_counts.setdefault(file_id, None)
            self._note_replacement_receipt(pending.replace_tool_use_id, None)
            return "refused", None
        except Exception as exc:  # noqa: BLE001 - reported to the author as a failed save
            log_with_context(
                logger, 40, "Failed to save chapter replacement", file_id=file_id, error_type=type(exc).__name__
            )
            self._saved_file_word_counts.setdefault(file_id, None)
            self._note_replacement_receipt(pending.replace_tool_use_id, None)
            return "failed", None
        if replaced.get("mutation_applied") is True:
            self._file_mutated = True
            if run_meter is not None and content.strip():
                run_meter.mark_first_body_persisted()
        words = count_words(str(replaced.get("new_content") or content))
        self._note_body_saved(
            file_id,
            words,
            title=str(replaced.get("title") or pending.title) if complete else None,
            file_type=pending.file_type,
        )
        self._note_replacement_receipt(pending.replace_tool_use_id, words)
        from agent.tools.mcp_tools import ToolContext

        ToolContext.mark_chapter_replacement_saved(file_id)
        return "saved", replaced

    def _replace_file_body_sync(self, file_id: str, content: str, expected_updated_at: str | None) -> dict[str, Any]:
        from agent.tools.file_ops import FileToolExecutor
        from database import create_session, get_session, is_postgres

        def run(session: Any) -> dict[str, Any]:
            return FileToolExecutor(session=session, user_id=self.config.user_id).replace_body(
                file_id,
                content,
                expected_updated_at=expected_updated_at,
                normalize_quotes=True,
                protect_author_content=True,
            )

        if is_postgres:
            with create_session() as session:
                return run(session)
        session_gen = get_session()
        session = next(session_gen)
        try:
            return run(session)
        finally:
            with contextlib.suppress(StopIteration):
                next(session_gen)

    async def _resume_file_body(self, request: Any) -> AsyncIterator[SSEEvent]:
        """单循环 agent 的空文件纠偏轮开始前：为同一个文件重新等待 <file> 正文，结果写回 request。

        上一个 agent run 的 MESSAGE_END 已把还在等正文的捕获复位成 IDLE；不重新等待，
        纠偏轮写出的 <file> 正文只会落进聊天。只认本适配器自己记下的待写文件（这一请求里
        成功的 create_file 结果）：同一个 id、建的时候是空文件、图刚读到的版本仍是
        create_file 结果里的版本（之后作者或别的写入都没动过它）。落库照旧走
        _save_file_content_sync 的作者保护与版本检查。
        """
        if not isinstance(request, FileBodyResume):
            return
        pending = self._pending_file_write
        processor = self._stream_processor
        already_waiting = (
            pending is not None
            and processor.state == StreamState.WAITING_START
            and processor.file_id == pending.file_id
            and not processor.temp_buffer
        )
        refusal: str | None = None
        if not self.config.process_file_markers or self._fatal_stream_error:
            refusal = FILE_BODY_RESUME_UNAVAILABLE
        elif pending is None or pending.file_id != request.file_id:
            refusal = FILE_BODY_RESUME_NOT_BOUND
        elif pending.original_content_length > 0 and not pending.replace_tool_use_id:
            # 整章替换（write_chapter 定位到已有的一章）原本就有正文：写完走 replace_body，不受这条限制。
            refusal = FILE_BODY_RESUME_NOT_EMPTY
        elif pending.baseline_updated_at is None or pending.baseline_updated_at != request.expected_updated_at:
            refusal = FILE_BODY_RESUME_VERSION_CHANGED
        elif not already_waiting and processor.state != StreamState.IDLE:
            refusal = FILE_BODY_RESUME_CAPTURE_ACTIVE
        elif not self._rearm_pending_guard(pending.file_id):
            # 捕获与守卫要一起恢复：守卫不在（或恢复失败）时不开捕获，纠偏轮就不会被安排。
            refusal = FILE_BODY_RESUME_GUARD_MISSING
        elif not already_waiting:
            # IDLE 下暂扣的行首 / 空标签对文字先交还对话，再开始等正文。
            async for sse_event in self._flush_active_capture():
                yield sse_event
            processor.start_file_write(pending.file_id)

        if refusal is None:
            request.armed = True
            # 上一程可能已发过 file_content_end（整章替换被截断补全拒绝落库），编辑器的流式锁随之解除；
            # 纠偏轮在 <file> 之前还有思考，期间作者未保存的文字若被自动保存会推进版本，重试的整章
            # 替换就会因版本冲突作废。这里立刻重新告诉编辑器这个文件在等 AI 正文，让锁马上恢复。
            if pending is not None:
                yield file_created_event(pending.file_id, pending.file_type, pending.title)
        else:
            request.refusal = refusal
        log_with_context(
            logger,
            20 if refusal is None else 30,  # INFO / WARNING
            "Empty-file correction capture resumed" if refusal is None else "Empty-file correction capture not resumed",
            file_id=request.file_id,
            refusal=refusal,
        )

    @staticmethod
    def _rearm_pending_guard(file_id: str) -> bool:
        """恢复这个文件的待写守卫；只有确实恢复了才返回 True（异常也按失败处理）。"""
        try:
            from agent.tools.mcp_tools import ToolContext

            return ToolContext.rearm_pending_file_body(file_id) is True
        except Exception as exc:  # noqa: BLE001 - a failed guard must decline the resume
            log_with_context(logger, 30, "Pending file guard re-arm failed", file_id=file_id, error_type=type(exc).__name__)
            return False

    @staticmethod
    def _resolve_original_content_length(file_data: dict[str, Any], content: str) -> int:
        """取目标文件本次写入前的正文长度。

        优先用 create_file 返回的显式字段 original_content_length（复用分支给出），
        缺失或不合法时退回实际 content 的长度——两者都拿不到就是 0（新建空文件）。
        """
        raw = file_data.get("original_content_length")
        try:
            explicit = int(raw)
        except (TypeError, ValueError):
            explicit = 0
        return max(explicit, len(content), 0)

    def _persist_active_capture_in_background(self) -> asyncio.Task | None:
        """流被取消/关闭时，把进行中的 <file> 捕获补全落库（后台任务，不 await）。

        与 _complete_file_write 同一套语义：只落库真正的正文（finalize_on_stream_end
        已丢弃 control-marker 污染的叙述），并遵守截断覆盖保护——目标文件原本
        已有正文时不整体覆盖。落库用独立 session（_save_file_content_sync），
        不依赖随请求关闭的共享 session。
        """
        if not (self.config.process_file_markers and self._stream_processor.is_active):
            return None
        try:
            result = self._stream_processor.finalize_on_stream_end()
        except Exception as exc:  # pragma: no cover - 防御性兜底
            log_with_context(
                logger,
                30,  # WARNING
                "Failed to finalize file capture after stream abort",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        if not (result.file_complete and result.file_id and result.final_content.strip()):
            return None
        if result.file_id == PREBODY_FILE_ID:
            # 还没有文件认领的正文，没有可落库的目标
            return None
        if self._should_refuse_truncated_overwrite(result):
            log_with_context(
                logger,
                30,  # WARNING
                "Skipping abort-time save that would overwrite an existing file body",
                file_id=result.file_id,
                streamed_length=len(result.final_content),
            )
            return None

        file_id = result.file_id
        content = result.final_content
        log_with_context(
            logger,
            20,  # INFO
            "Persisting in-flight file capture after stream abort",
            file_id=file_id,
            content_length=len(content),
        )
        return self._start_abort_save(file_id, content, self._pending_file_write)

    def _start_abort_save(self, file_id: str, content: str, pending: PendingFileWrite | None) -> asyncio.Task:
        """取消收尾的补存任务：登记为 _abort_capture，settle_aborted_capture 等它的结果。"""
        file_type, baseline_updated_at = self._save_target(file_id, pending)
        task = asyncio.create_task(
            self._save_file_content(
                file_id,
                content,
                record_mutation=False,
                file_type=file_type,
                baseline_updated_at=baseline_updated_at,
            )
        )
        self._abort_capture = (file_id, count_words(content), task)
        self._abort_capture_replace_id = (
            pending.replace_tool_use_id
            if pending is not None and pending.file_id == file_id and pending.replace_card
            else None
        )
        _ABORT_SAVE_TASKS.add(task)
        task.add_done_callback(_ABORT_SAVE_TASKS.discard)
        return task

    async def _settle_completed_unsaved(self) -> None:
        """</file> 已解析、还没开始保存就被截停的正文：按正常完成的规则补存。"""
        snapshot = self._completed_unsaved
        if snapshot is None:
            return
        self._completed_unsaved = None
        result, pending = snapshot
        file_id = result.file_id
        content = result.final_content
        if not content.strip() or file_id == PREBODY_FILE_ID:
            return
        target = pending if pending is not None and pending.file_id == file_id else None
        if result.auto_completed and target is not None and target.original_content_length > 0:
            # 与 _complete_file_write 相同的截断覆盖保护：已有正文不被截断补全的残稿整体覆盖。
            log_with_context(
                logger, 30, "Skipping abort-time save that would overwrite an existing file body",
                file_id=file_id, streamed_length=len(content),
            )
            return
        if target is not None and target.replace_card:
            # 整章替换的新稿还没开始提交就被截停：不覆盖作者的原文（截停后没有 diff 审阅和撤销卡），
            # 与还在捕获中的替换一样作废；已经开始提交的那次由 _settle_replacement_in_flight 等结果。
            log_with_context(
                logger, 30, "Dropping a whole-chapter replacement stopped before its commit", file_id=file_id
            )
            return
        log_with_context(logger, 20, "Persisting a completed body cut off before its save", file_id=file_id)
        self._start_abort_save(file_id, content, target)

    async def settle_aborted_capture(self) -> dict[str, int | None]:
        """等待本轮残稿补存，返回包含未发出尾部缓冲的实际保存字数。

        service 可能在 adapter 生成器关闭之前进入取消收尾；此时在这里补全捕获。
        保存任务仍沿用原有超时与独立 session，不把历史落库当成正文已落库。
        """
        await self._settle_replacement_in_flight()
        await self._settle_completed_unsaved()
        if self._stream_processor.is_active:
            self._persist_active_capture_in_background()
        capture = self._abort_capture
        if capture is None:
            return self.get_saved_file_word_counts()
        file_id, words, task = capture
        saved = await asyncio.shield(task)
        if saved:
            self._note_body_saved(file_id, words)
        else:
            self._saved_file_word_counts.setdefault(file_id, None)
        self._note_replacement_receipt(self._abort_capture_replace_id, words if saved else None)
        return self.get_saved_file_word_counts()

    async def _settle_replacement_in_flight(self) -> None:
        """取消时还在提交的整章替换：等提交结果（受 STREAM_FILE_SAVE_TIMEOUT_S 限时），写进保存回执。"""
        in_flight = self._replacement_in_flight
        if in_flight is None:
            return
        self._replacement_in_flight = None
        file_id, content, save, replace_tool_use_id = in_flight
        try:
            replaced = await asyncio.shield(save)
        except Exception as exc:  # noqa: BLE001 - 提交失败：回执记为未保存
            log_with_context(
                logger, 30, "Chapter replacement in flight at abort did not commit",
                file_id=file_id, error_type=type(exc).__name__,
            )
            self._saved_file_word_counts.setdefault(file_id, None)
            self._note_replacement_receipt(replace_tool_use_id, None)
            return
        if replaced.get("mutation_applied") is not True:
            self._saved_file_word_counts.setdefault(file_id, None)
            self._note_replacement_receipt(replace_tool_use_id, None)
            return
        self._file_mutated = True
        words = count_words(str(replaced.get("new_content") or content))
        self._note_body_saved(file_id, words)
        self._note_replacement_receipt(replace_tool_use_id, words)
        log_with_context(logger, 20, "Chapter replacement committed after abort", file_id=file_id)

    def get_saved_file_word_counts(self) -> dict[str, int | None]:
        """保存回执；收到结束标记或正文 chunk 本身不代表落库成功。"""
        return dict(self._saved_file_word_counts)

    def _note_replacement_receipt(self, replace_tool_use_id: str | None, words: int | None) -> None:
        if replace_tool_use_id:
            self._replacement_receipts[replace_tool_use_id] = words

    def get_replacement_receipts(self) -> dict[str, int | None]:
        """整章替换的保存回执（write_chapter tool_use_id → 字数，None 表示这一次没有保存）。"""
        return dict(self._replacement_receipts)

    async def _flush_capture_and_held_reply(self) -> AsyncIterator[SSEEvent]:
        """出错收尾：进行中的捕获照常收尾，没被认领的正文作为对话放出（都在 error 帧之前）。"""
        async for sse_event in self._flush_active_capture():
            yield sse_event
        for sse_event in self._release_held_reply():
            yield sse_event

    async def _flush_active_capture(self) -> AsyncIterator[SSEEvent]:
        """收尾仍在进行中的流式捕获，并发出对应的 SSE 事件。

        复用 finalize_on_stream_end 的语义：WAITING_START 的缓冲原样回到对话，
        WRITING 的正文按截断补全（含 control-marker 污染守卫），DRAINING 的
        溢出尾部丢弃。
        """
        if not (self.config.process_file_markers and self._stream_processor.is_active):
            return

        result = self._stream_processor.finalize_on_stream_end()
        async for sse_event in self._emit_stream_result(result):
            yield sse_event

    async def _handle_text_content(self, text: str) -> AsyncIterator[SSEEvent]:
        """
        Handle text content, processing <file> markers if needed.

        Args:
            text: Text content chunk

        Yields:
            SSE events (content or file_content)
        """
        if not self.config.process_file_markers:
            # No file marker processing, just emit content
            if text:
                yield content_event(text)
            return

        # Process through StreamProcessor
        result: StreamResult = self._stream_processor.process_content(text)

        async for sse_event in self._emit_stream_result(result):
            yield sse_event

    def _handle_chat_hold(self, data: Any) -> Iterable[SSEEvent]:
        """图的 CHAT_HOLD 控制事件：begin 开始扣对话文字，discard 丢掉扣着的文字。"""
        action = data.get("action") if isinstance(data, dict) else None
        if action == CHAT_HOLD_BEGIN:
            self._chat_hold_active = True
            return []
        if action == CHAT_HOLD_DISCARD:
            dropped = "".join(self._held_chat)
            if dropped:
                log_with_context(
                    logger,
                    20,  # INFO
                    "Discarding chat text superseded by the next agent's reply",
                    dropped_length=len(dropped),
                    reason=(data.get("reason") if isinstance(data, dict) else None),
                )
            self._held_chat = []
            self._chat_hold_active = False
            return []
        return self._release_held_chat()

    def _release_held_chat(self) -> list[SSEEvent]:
        """放出扣着的对话文字并结束扣留。"""
        held = "".join(self._held_chat)
        self._held_chat = []
        self._chat_hold_active = False
        return [content_event(held)] if held else []

    def _conversation_events(self, text: str) -> list[SSEEvent]:
        if not text:
            return []
        if self._held_reply:
            # 扣着待认领正文时，之后的对话文字跟在它后面，放出时保持原顺序
            self._held_reply.append(("chat", text))
            return []
        if self._chat_hold_active:
            self._held_chat.append(text)
            return []
        return [content_event(text)]

    async def _emit_stream_result(self, result: StreamResult) -> AsyncIterator[SSEEvent]:
        """Emit SSE events mapped from StreamProcessor result."""
        if result.file_id == PREBODY_FILE_ID:
            for sse_event in self._hold_prebody_result(result):
                yield sse_event
            return
        if result.file_complete and result.file_id:
            self._completed_unsaved = (result, self._pending_file_write)
        # Emit conversation content
        for sse_event in self._conversation_events(result.conversation_content):
            yield sse_event

        # Emit file content chunk
        if result.file_content and result.file_id:
            yield file_content_event(result.file_id, result.file_content)

        # Handle file completion
        if result.file_complete and result.file_id:
            async for sse_event in self._complete_file_write(result):
                yield sse_event

        # Emit any post-file conversation content after file completion.
        for sse_event in self._conversation_events(result.conversation_content_after_file):
            yield sse_event

    def _hold_prebody_result(self, result: StreamResult) -> list[SSEEvent]:
        """待认领正文的处理结果：闭合且非空的正文先扣住，未闭合的照旧作为对话放出。"""
        events = self._conversation_events(result.conversation_content)
        if result.file_complete:
            if result.auto_completed:
                events += self._conversation_events(result.final_content)
            elif result.final_content.strip():
                log_with_context(
                    logger,
                    20,  # INFO
                    "Holding a <file> body written before its create_file",
                    content_length=len(result.final_content),
                )
                self._held_reply.append(("body", result.final_content))
        after = result.conversation_content_after_file
        # </file> 那一行的换行也是标记的一部分（与以前整行丢掉一致）
        if result.file_complete and not result.auto_completed:
            after = after.removeprefix("\r\n") if after.startswith("\r\n") else after.removeprefix("\n")
        events += self._conversation_events(after)
        return events

    def _release_held_reply(self) -> list[SSEEvent]:
        """没被 create_file 认领的正文和它之后扣着的对话文字按原顺序放出（正文与以前一样进对话）。"""
        held = self._held_reply
        self._held_reply = []
        self._held_reply_tools_done = False
        events: list[SSEEvent] = []
        for _kind, text in held:
            events += self._conversation_events(text)
        return events

    async def _end_reply_text(self) -> AsyncIterator[SSEEvent]:
        """工具结果到达：这条回复的文字结束了。没闭合的待认领正文作为对话收尾，下一条回复从行首开始。"""
        if not self.config.process_file_markers:
            return
        processor = self._stream_processor
        if processor.file_id == PREBODY_FILE_ID and processor.state != StreamState.IDLE:
            async for sse_event in self._flush_active_capture():
                yield sse_event
        for sse_event in self._conversation_events(processor.begin_reply_text()):
            yield sse_event

    async def _bind_reply_prebody(self, result_data: Any) -> AsyncIterator[SSEEvent]:
        """create_file / write_chapter 确认本条回复里先写出的正文就是它的（body_in_reply）：落进这个文件。

        正文走与 </file> 收尾相同的落库路径（覆盖保护、保存回执、清除待写守卫）。
        工具没确认或这边没有扣着的正文时什么也不做：文件照旧等下一次回复的 <file>，
        扣着的正文在下一个边界作为对话放出。
        """
        file_data = result_data if isinstance(result_data, dict) else {}
        pending = self._pending_file_write
        claimed = (
            coerce_bool(file_data.get("body_in_reply"))
            and pending is not None
            and pending.file_id == file_data.get("id")
        )
        first_body = next((i for i, (kind, _text) in enumerate(self._held_reply) if kind == "body"), None)
        if not claimed or pending is None or first_body is None:
            if claimed and pending is not None:
                # 工具认为正文已在本条回复里，这边却没扣到（两边判定不一致）：不让下一条回复的
                # <file> 落进这个文件（那会再错位一章）。文件保持空白，由图的边界核验补写或回滚。
                log_with_context(
                    logger,
                    30,  # WARNING
                    "create_file claimed a reply body the adapter did not hold; not capturing into it",
                    file_id=pending.file_id,
                    title=pending.title,
                )
                self._stream_processor.reset()
            # 没认领：扣着的正文继续等同一回复里后面的 create_file，下一条回复开始或边界时放出
            return
        _kind, body = self._held_reply.pop(first_body)
        log_with_context(
            logger,
            20,  # INFO
            "Binding a <file> body written before create_file to the new file",
            file_id=pending.file_id,
            title=pending.title,
            content_length=len(body),
        )
        self._stream_processor.reset()
        async for sse_event in self._emit_stream_result(
            StreamResult(
                file_content=body,
                file_id=pending.file_id,
                file_complete=True,
                content_length=len(body),
                final_content=body,
            )
        ):
            yield sse_event
        for sse_event in self._release_held_reply():
            yield sse_event

    async def _complete_file_write(self, result: StreamResult) -> AsyncIterator[SSEEvent]:
        """Persist finalized file content and emit file completion event."""
        self._completed_unsaved = None
        # 覆盖保护：这次完成来自"没等到 </file> 的自动补全"，而目标文件本来
        # 就有正文（create_file 幂等复用命中的已完成分集）。补全出来的正文很
        # 可能只写了个开头，_save_file_content 又是整体替换，写下去等于用残稿
        # 抹掉整集。此处拒绝落库、保留原文。诊断留在日志，作者只看保存事实；
        # 模型侧既有 create_file 结果与空文件纠偏协议不变。
        if self._should_refuse_truncated_overwrite(result):
            pending = self._pending_file_write
            original_length = pending.original_content_length if pending else 0
            title = pending.title if pending else ""
            log_with_context(
                logger,
                30,  # WARNING
                "Refusing to overwrite an existing file body with auto-completed content",
                file_id=result.file_id,
                title=title,
                original_content_length=original_length,
                streamed_length=len(result.final_content),
            )
            if pending is not None and pending.replace_card:
                # write_chapter 的整章替换：待写绑定与守卫都留着，loop 引擎在 run 边界按结构化事实
                # 决定为这一章恢复捕获重试（FILE_BODY_RESUME，核对定位时的版本），补不成就作废定位，
                # 那时由引擎发「未保存」的说明；这里不先说，重试成功时作者不会看到矛盾的两句话。
                yield file_content_end_event(result.file_id)
                return
            for sse_event in self._rewrite_not_saved_events():
                yield sse_event
            yield file_content_end_event(result.file_id)
            self._pending_file_write = None
            self._clear_pending_empty_file_guard(result.file_id)
            return

        # 空的或只有空白的 <file> 块不算写成正文：不落库、不发结束事件，待写绑定与守卫都留着，
        # 由图的边界核验按空文件处理（有界纠偏、回滚或如实报告未完成），不当作已交付。
        if not result.final_content.strip():
            log_with_context(
                logger,
                30,  # WARNING
                "Blank <file> block is not a written body; the file stays pending",
                file_id=result.file_id,
                streamed_length=len(result.final_content),
            )
            return

        pending = self._pending_file_write
        if pending is not None and pending.file_id == result.file_id and pending.replace_card:
            async for sse_event in self._complete_chapter_replace(result, pending):
                yield sse_event
            return

        # Save accumulated file content to database before emitting end event
        saved = await self._save_new_body(result, pending)
        if not saved:
            self._fatal_stream_error = True
            # Clear pending state to avoid blocking future create_file calls.
            self._pending_file_write = None
            self._clear_pending_empty_file_guard(result.file_id)
            yield error_event(
                message=error_message_for(ErrorCode.AGENT_FILE_SAVE_FAILED),
                code=ErrorCode.AGENT_FILE_SAVE_FAILED,
                retryable=True,
            )
            return

        yield file_content_end_event(result.file_id)

        # Clear pending file write
        self._pending_file_write = None

        # Also clear ToolContext pending state, allowing next create_file.
        self._clear_pending_empty_file_guard(result.file_id)

    async def _save_new_body(self, result: StreamResult, pending: PendingFileWrite | None) -> bool:
        """正常路径的新正文保存：在独立任务里跑并 shield，取消落在保存中途时交给 settle_aborted_capture 等结果。"""
        file_id = result.file_id
        content = result.final_content
        file_type, baseline_updated_at = self._save_target(file_id, pending)
        save = asyncio.ensure_future(
            self._save_file_content(
                file_id,
                content,
                file_type=file_type,
                baseline_updated_at=baseline_updated_at,
                complete_title=None if result.auto_completed else self._save_title(file_id, pending),
            )
        )
        try:
            saved = await asyncio.shield(save)
        except asyncio.CancelledError:
            self._abort_capture = (file_id, count_words(content), save)
            _ABORT_SAVE_TASKS.add(save)
            save.add_done_callback(_ABORT_SAVE_TASKS.discard)
            raise
        if saved and result.auto_completed:
            from agent.tools.mcp_tools import ToolContext

            ToolContext.mark_cut_body_saved(file_id, self._save_title(file_id, pending))
        return saved

    @staticmethod
    def _save_target(file_id: str, pending: PendingFileWrite | None) -> tuple[str, str | None]:
        """(file_type, baseline_updated_at)：保存任务创建之前定下，晚开始的任务也带着版本核对。"""
        if pending is None or pending.file_id != file_id:
            return "", None
        return pending.file_type, pending.baseline_updated_at

    @staticmethod
    def _save_title(file_id: str, pending: PendingFileWrite | None) -> str:
        """完整正文记进已保存正文时用的标题：只取同一个文件的待写记录（与 _save_target 同一口径）。"""
        return pending.title if pending is not None and pending.file_id == file_id else ""

    def _note_body_saved(
        self, file_id: str, words: int, *, title: str | None = None, file_type: str = ""
    ) -> None:
        """正文落库的唯一记账点：写保存回执（计费、交付、历史都读它）；title 不为 None 表示
        这是一份完整（非截断补全）的正文，同时记进本请求的已保存正文（续跑、任务板、write_chapter 守卫读它）。
        file_type 是保存目标的类型（知道时传），续跑只对正文类文件要求自查。"""
        self._saved_file_word_counts[file_id] = words
        if title is not None:
            from agent.tools.mcp_tools import ToolContext

            ToolContext.mark_chapter_body_saved(file_id, title, file_type)

    def _should_refuse_truncated_overwrite(self, result: StreamResult) -> bool:
        """判断这次"截断自动补全"是否会覆盖掉目标文件原有的正文。

        三个条件同时成立才拒绝：
        1) 本次完成由流结束时的自动补全产生（没有真实的 </file>）；
        2) 该文件确实是本适配器正在等待写入的那一份；
        3) 该文件在本次写入前已有正文（幂等复用命中的已完成分集）。
        """
        if not result.auto_completed or not result.file_id:
            return False
        pending = self._pending_file_write
        if pending is None or pending.file_id != result.file_id:
            return False
        return pending.original_content_length > 0

    def _clear_pending_empty_file_guard(self, file_id: str = "") -> None:
        """清除 ToolContext 的 pending-empty-file 标记。

        传入 file_id 时只清除指向该文件的标记：一份仍在缓冲的文件被新的
        create_file 顶掉时，标记可能已经指向新建的空文件，若被上一份文件的
        收尾误清，「空文件必须被补写」的信号就丢了（单循环 agent 再也不会
        安排补写）。file_id 为空表示流结束时的无条件清理，避免标记泄漏到
        下一个请求。

        实现上直接把 file_id 交给 ToolContext 做精确摘除：标记现在是集合，
        「读最近一个再无参清空」在同时存在多个待写空文件时会一次清光其余条目。
        """
        from agent.tools.mcp_tools import ToolContext

        ToolContext.clear_pending_empty_file(file_id or None)

    async def _save_file_content(
        self,
        file_id: str,
        content: str,
        *,
        record_mutation: bool = True,
        file_type: str = "",
        baseline_updated_at: str | None = None,
        complete_title: str | None = None,
    ) -> bool:
        """
        Save accumulated file content to database.

        Args:
            file_id: ID of the file to update
            content: Content to save
            file_type / baseline_updated_at: 保存目标的类型与版本令牌，调用方在创建保存任务之前
                从待写记录定下（见 _save_target），任务晚开始也不会丢掉版本核对。
            complete_title: 完整正文（非截断补全）时传标题，见 _note_body_saved。
        """
        log_with_context(
            logger,
            20,  # INFO
            "Saving file content to database",
            file_id=file_id,
            content_length=len(content),
            user_id=self.config.user_id,
        )

        resolved_file_type = file_type
        try:
            save_args: tuple[Any, ...] = (file_id, content)
            if baseline_updated_at is not None:
                save_args = (*save_args, baseline_updated_at)
            run_meter = self.config.run_meter
            timing = run_meter.measure_stage("file_save") if run_meter is not None else contextlib.nullcontext()
            with timing:
                saved, content_changed = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._save_file_content_sync,
                        *save_args,
                    ),
                    timeout=STREAM_FILE_SAVE_TIMEOUT_S,
                )
            if saved and content_changed and record_mutation:
                self._file_mutated = True
            if (
                saved
                and content_changed
                and content.strip()
                and str(resolved_file_type).strip().lower() in {FILE_TYPE_DRAFT, FILE_TYPE_SCRIPT}
                and run_meter is not None
            ):
                run_meter.mark_first_body_persisted()
            if saved:
                self._note_body_saved(
                    file_id, count_words(content), title=complete_title, file_type=resolved_file_type
                )
            else:
                self._saved_file_word_counts.setdefault(file_id, None)
            return saved
        except TimeoutError:
            self._saved_file_word_counts.setdefault(file_id, None)
            log_with_context(
                logger,
                40,  # ERROR
                "Timed out while saving streamed file content",
                file_id=file_id,
                timeout_s=STREAM_FILE_SAVE_TIMEOUT_S,
            )
            return False
        except Exception as e:
            self._saved_file_word_counts.setdefault(file_id, None)
            log_with_context(
                logger,
                40,  # ERROR
                "Failed to save file content",
                file_id=file_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    def _save_file_content_sync(
        self,
        file_id: str,
        content: str,
        expected_updated_at: str | None = None,
    ) -> tuple[bool, bool]:
        """Persist streamed file content with a fresh sync DB session.

        This is an in-app agent write, so draft/script bodies get their double
        quotes normalized to the manuscript's style (normalize_quotes=True).
        """
        from agent.tools.file_ops import FileToolExecutor
        from database import create_session, get_session, is_postgres

        if not is_postgres:
            session_gen = get_session()
            session = next(session_gen)
            try:
                executor = FileToolExecutor(
                    session=session,
                    user_id=self.config.user_id,
                )
                result = executor.update_file(
                    id=file_id,
                    content=content,
                    normalize_quotes=True,
                    protect_author_content=True,
                    expected_updated_at=expected_updated_at,
                )
            finally:
                with contextlib.suppress(StopIteration):
                    next(session_gen)
        else:
            with create_session() as session:
                executor = FileToolExecutor(
                    session=session,
                    user_id=self.config.user_id,
                )
                result = executor.update_file(
                    id=file_id,
                    content=content,
                    normalize_quotes=True,
                    protect_author_content=True,
                    expected_updated_at=expected_updated_at,
                )

        log_with_context(
            logger,
            20,  # INFO
            "File content saved successfully",
            file_id=file_id,
        )
        return True, result.get("content_changed") is True

    async def _handle_edit_file_result(
        self,
        result: Any,
    ) -> AsyncIterator[SSEEvent]:
        """
        Handle edit_file tool result, emitting edit events.

        Args:
            result: Edit file result data

        Yields:
            SSE events for file edit operations
        """
        if not isinstance(result, dict):
            return

        # FileToolExecutor returns id/title at root level, not nested in "file"
        file_id = result.get("id", "")
        title = result.get("title", "")
        file_type = result.get("file_type")
        # Edit details are in "details" field, not "edits_applied"
        edits = result.get("details", [])
        applied_edits = [e for e in edits if isinstance(e, dict)] if isinstance(edits, list) else []
        # 失败项与告警此前被整体丢弃：部分失败/全部失败的编辑在 UI 上呈现为
        # 「全部成功」，用户以为改完了就继续往下写。失败必须与成功同样上屏。
        failed_edits = self._normalize_edit_failures(result.get("failed_edits"))
        warnings = [str(w) for w in result.get("warnings") or [] if w]

        if not file_id:
            return

        # Emit edit start —— 总数必须包含失败项，否则进度条会少算
        yield file_edit_start_event(
            file_id,
            title,
            len(applied_edits) + len(failed_edits),
            file_type=file_type,
        )

        # Emit individual edit events
        for i, edit in enumerate(applied_edits):
            op = edit.get("op", "replace")
            old_preview = edit.get("old_preview", "")
            new_preview = edit.get("new_preview", "")
            # append/prepend 的 detail 没有 new_preview，只有 text_preview
            if not new_preview:
                new_preview = edit.get("text_preview", "")
            success = edit.get("success", True)
            error = edit.get("error")

            yield file_edit_applied_event(
                file_id=file_id,
                edit_index=i,
                op=op,
                old_preview=old_preview[:50] if old_preview else None,
                new_preview=new_preview[:50] if new_preview else None,
                success=success,
                error=error,
            )

        # 失败项紧随其后，edit_index 继续顺延（保证事件 key 唯一）
        for offset, failed in enumerate(failed_edits):
            yield file_edit_applied_event(
                file_id=file_id,
                edit_index=len(applied_edits) + offset,
                op=failed.get("op") or "replace",
                old_preview=None,
                new_preview=None,
                success=False,
                error=failed.get("error") or "编辑失败",
            )

        # Emit edit end - use new_length from result directly
        end_event = file_edit_end_event(
            file_id=file_id,
            edits_applied=len(applied_edits),
            new_length=result.get("new_length", 0),
            new_content=None,  # Content not included in executor result
            original_content=None,
            file_type=file_type,
            title=title,
            # 失败/部分成功/静默跳过的告警一并带上，前端与历史都能看到真实结果。
            # 这些已是 FileEditEndEventData 的一等字段，不再在工厂外补键。
            failed_count=len(failed_edits),
            partial_success=bool(
                result.get("partial_success") or (applied_edits and failed_edits)
            ),
            all_failed=bool(
                result.get("all_failed") or (failed_edits and not applied_edits)
            ),
            warnings=warnings,
        )
        yield end_event

    @staticmethod
    def _normalize_edit_failures(raw: Any) -> list[dict[str, Any]]:
        """把 edit_file 返回的 failed_edits 归一化成事件可用的字典列表。"""
        if not isinstance(raw, list):
            return []
        failures: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, dict):
                failures.append(item)
            elif item:
                failures.append({"error": str(item)})
        return failures

    @classmethod
    def _summarize_edit_failures(cls, result: dict[str, Any]) -> str:
        """把全部失败的编辑汇总成一句给模型看的错误说明。"""
        failures = cls._normalize_edit_failures(result.get("failed_edits"))
        reasons = [str(item.get("error")) for item in failures if item.get("error")]
        if not reasons:
            reasons = [str(w) for w in result.get("warnings") or [] if w]
        detail = "；".join(reasons) if reasons else "未提供原因"
        return f"全部 {len(failures)} 处编辑均未生效：{detail}"


# 这些事件之前，图没有丢掉的扣留文字照常放出（保持在本 agent 的段落里）。
# 只放 agent run 的边界事件：STEERING_RECEIVED 会在 writer run 中途（工具边界）出现，
# 那时放出并结束扣留，之后的总结就会直接流给作者，送审时又多一份审稿人的总结。
_CHAT_HOLD_RELEASE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        StreamEventType.AGENT_SELECTED.value,
        StreamEventType.HANDOFF.value,
        StreamEventType.WORKFLOW_STOPPED.value,
        StreamEventType.WORKFLOW_COMPLETE.value,
        StreamEventType.ITERATION_EXHAUSTED.value,
        StreamEventType.ERROR.value,
    }
)


def _card_files(raw: Any) -> list[dict[str, str]] | None:
    """状态卡上点名的文件：只留 id 与标题（图里的条目用 file_id）；没给就是 None。"""
    if not isinstance(raw, list):
        return None
    files: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        file_id = item.get("id") or item.get("file_id")
        title = item.get("title")
        if isinstance(file_id, str) and file_id and isinstance(title, str):
            files.append({"id": file_id, "title": title})
    return files


def create_stream_adapter(
    project_id: str = "",
    user_id: str | None = None,
    process_file_markers: bool = True,
    matched_skill_ids: Iterable[str] | None = None,
    run_meter: AgentRunMeter | None = None,
    language: str = "zh",
) -> StreamAdapter:
    """
    Factory function to create a StreamAdapter.

    Args:
        project_id: Project ID for logging
        user_id: User ID for logging
        process_file_markers: Whether to process <file> markers
        matched_skill_ids: Skills already announced via skill_matched (explicitly selected)
        run_meter: Optional request-level timing recorder
        language: Request UI language for author-facing save notices

    Returns:
        Configured StreamAdapter instance
    """
    config = StreamAdapterConfig(
        project_id=project_id,
        user_id=user_id,
        process_file_markers=process_file_markers,
        matched_skill_ids=tuple(str(item) for item in (matched_skill_ids or []) if item),
        run_meter=run_meter,
        language=language,
    )
    return StreamAdapter(config)


__all__ = [
    "StreamAdapter",
    "StreamAdapterConfig",
    "PendingFileWrite",
    "create_stream_adapter",
    "is_folder_file_type",
]
