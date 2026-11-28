"""Streaming runner that adapts openai-agents-python to ZenStory workflow events."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import functools
import inspect
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from agent.core.events import WALL_CLOCK_LIMIT
from agent.core.message_manager import is_non_zh_language
from agent.core.output_truncation import OUTPUT_TRUNCATED_HISTORY
from agent.core.run_meter import AgentRunMeter, TimingOutcome
from agent.core.server_drain import is_draining
from agent.core.stream_errors import (
    MODEL_CALL_LIMIT_ERROR_TYPE,
    REPETITION_LOOP_STOP_REASON,
    classify_stream_exception,
    log_stream_exception,
    model_call_limit_error,
    output_truncated_error,
    repetition_loop_error,
)
from agent.core.stream_processor import (
    FILE_START_PATTERN,
    LEAKED_TOOL_CALL_STOP_REASON,
    could_be_leaked_tool_call_prefix,
    find_repetition_start,
    starts_with_leaked_tool_call_marker,
)
from agent.core.usage import sum_response_usages
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.core.writing_state import WritingState
from agent.openai_agents.events import (
    mcp_text_result,
    parse_json_object,
    tool_error_text,
)
from agent.openai_agents.model import build_failover_chat_model
from agent.openai_agents.repeat_edit_guard import RepeatEditGuard
from agent.openai_agents.repeat_read_guard import (
    NO_PROGRESS_STOP_REASON,
    RepeatReadGuard,
)
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker, _parse_error_payload
from agent.openai_agents.tools_adapter import build_agent_function_tools
from agent.tools.tool_context import AgentRequestContext, unwrap_request_context
from config import agent_runtime
from config.agent_runtime import (
    AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS,
    AGENT_TOOL_CALL_MAX_ITERATIONS,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

MessageList = list[dict[str, Any]]
_BACKGROUND_ACLOSE_TIMEOUT_S = 2.0
_background_aclose_tasks: set[asyncio.Task[Any]] = set()


def _schedule_background_aclose(
    aclose: Callable[[], Any],
    *,
    timeout_s: float = _BACKGROUND_ACLOSE_TIMEOUT_S,
) -> None:
    """Schedule bounded async-generator cleanup without awaiting inside a ``GeneratorExit`` finally block."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    async def _run_close() -> None:
        with contextlib.suppress(Exception):
            res = aclose()
            if inspect.isawaitable(res):
                await asyncio.wait_for(res, timeout=max(0.0, timeout_s))

    task = loop.create_task(_run_close())
    _background_aclose_tasks.add(task)
    task.add_done_callback(_background_aclose_tasks.discard)

# 工具失败熔断后 MESSAGE_END 的 stop_reason（随 assistant 消息元数据落库）。
TOOL_FAILURE_STOP_REASON = "tool_failure_circuit_open"
# 请求级模型调用预算用尽、截停 SDK run 后 MESSAGE_END 的 stop_reason；预算快用完时
# 由收尾提醒软着陆（模型写了阶段总结后结束）也用它，随 assistant 消息落库。
MODEL_CALL_BUDGET_STOP_REASON = "model_call_budget_exhausted"
# 单个 agent run 的工具调用轮数（SDK max_turns）用尽，或已进入收尾提醒后结束时的
# stop_reason。随 assistant 消息落库，下一轮历史回放里注明这一轮没做完。
MAX_TURNS_STOP_REASON = "max_turns_exceeded"
# 接近请求墙钟时限、按提醒收尾（与 service 在时限取消时落库的 stop_reason 相同）。
RUN_DEADLINE_STOP_REASON = "run_deadline_exceeded"
# 重复读取守卫判定无进展、结束本轮时的 stop_reason（与 ERROR 事件 data.reason 相同）。
NO_PROGRESS_STOP = NO_PROGRESS_STOP_REASON
# 多少新字符检查一次重复（find_repetition_start 每次扫描整次调用的回复）。
_REPETITION_CHECK_EVERY_CHARS = 256
# 最后一次模型响应只有思考、没有正文和工具调用就撞上输出上限（max_tokens）时
# MESSAGE_END 的 stop_reason。
OUTPUT_TRUNCATED_STOP_REASON = "output_truncated"
# 最后一次模型响应的 output_tokens 达到 max_tokens 的这个比例即视为被输出上限截断。
# openai-agents 0.21.x 的 Chat Completions 流式 handler 不处理 finish_reason
# （ResponseCompletedEvent 的 status 恒为 completed），只能看 usage 判断。
OUTPUT_CAP_RATIO = 0.98
# 正常结束的 run 最后一次响应也撞上了输出上限时，MESSAGE_END data 里带的结构化标记（值为 True）。
OUTPUT_CAPPED_KEY = "output_capped"

# 流式工具参数生成期间的轻量进度提示（经 StreamAdapter 转为瞬态 SSE `thinking` 事件，不落库）。
TOOL_PREPARING_STEP = "tool_preparing"
TOOL_ARG_PROGRESS_INTERVAL_S = 2.0
TOOL_ARG_PROGRESS_BYTES = 1_500
_TOOL_PREPARING_LABELS_ZH: dict[str, str] = {
    "edit_file": "正在准备编辑文件…",
    "batch_replace_files": "正在准备批量替换…",
    "write_chapter": "正在准备写入章节…",
    "create_file": "正在准备创建文件…",
    "read_files": "正在准备读取文件…",
    "search_files": "正在准备检索文件…",
    "query_files": "正在准备检索文件…",
    "update_project": "正在准备更新项目…",
    "delete_file": "正在准备删除文件…",
    "rename_file": "正在准备重命名文件…",
    "load_skill": "正在加载写作技能…",
    "read_skill_resource": "正在读取技能参考资料…",
}
_TOOL_PREPARING_LABELS_EN: dict[str, str] = {
    "edit_file": "Preparing file edits…",
    "batch_replace_files": "Preparing batch replace…",
    "write_chapter": "Preparing chapter write…",
    "create_file": "Preparing new file…",
    "read_files": "Preparing file read…",
    "search_files": "Preparing file search…",
    "query_files": "Preparing file lookup…",
    "update_project": "Preparing project update…",
    "delete_file": "Preparing file deletion…",
    "rename_file": "Preparing file rename…",
    "load_skill": "Loading writing skill…",
    "read_skill_resource": "Reading skill reference…",
}
_TOOL_ARG_PROGRESS_LABELS_ZH: dict[str, str] = {
    "edit_file": "正在生成文件修改内容（约 {chars} 字符）…",
    "batch_replace_files": "正在准备批量替换参数（约 {chars} 字符）…",
    "write_chapter": "正在准备写入章节（约 {chars} 字符）…",
    "create_file": "正在准备创建文件（约 {chars} 字符）…",
    "update_project": "正在准备更新项目（约 {chars} 字符）…",
}
_TOOL_ARG_PROGRESS_LABELS_EN: dict[str, str] = {
    "edit_file": "Generating file edits (~{chars} chars)…",
    "batch_replace_files": "Preparing batch replace (~{chars} chars)…",
    "write_chapter": "Preparing chapter write (~{chars} chars)…",
    "create_file": "Preparing new file (~{chars} chars)…",
    "update_project": "Preparing project update (~{chars} chars)…",
}


@dataclasses.dataclass
class _ToolArgStreamState:
    """单次流式函数调用参数的节流进度跟踪。"""

    tool_name: str
    total_bytes: int = 0
    total_chars: int = 0
    last_emit_bytes: int = 0
    last_emit_at: float = 0.0


def _tool_preparing_message(tool_name: str, *, english: bool) -> str:
    labels = _TOOL_PREPARING_LABELS_EN if english else _TOOL_PREPARING_LABELS_ZH
    default_label = "Preparing tool call…" if english else "正在准备调用工具…"
    return labels.get(tool_name, default_label)


def _tool_arg_progress_message(tool_name: str, chars: int, *, english: bool) -> str:
    labels = _TOOL_ARG_PROGRESS_LABELS_EN if english else _TOOL_ARG_PROGRESS_LABELS_ZH
    template = labels.get(
        tool_name,
        "Preparing tool arguments (~{chars} chars)…" if english else "正在准备工具参数（约 {chars} 字符）…",
    )
    return template.format(chars=chars)


_CACHED_FAKE_RESPONSES_ID: str | None = None


def _fake_responses_id() -> str:
    global _CACHED_FAKE_RESPONSES_ID
    if _CACHED_FAKE_RESPONSES_ID is None:
        from agents.models.fake_id import FAKE_RESPONSES_ID

        _CACHED_FAKE_RESPONSES_ID = FAKE_RESPONSES_ID
    return _CACHED_FAKE_RESPONSES_ID


def _real_raw_item_id(value: Any) -> str:
    """Return a provider item id only when it is a real unique id (never Chat Completions' FAKE_RESPONSES_ID)."""
    raw_id = str(value or "")
    return "" if not raw_id or raw_id == _fake_responses_id() else raw_id

# 距离上限还剩多少次模型调用时开始收尾：在这几次调用的输入末尾追加提醒，
# 要求模型停止调用工具、写阶段总结（已完成 / 剩余 / 用到的文件 id）。上限取
# 本次 run 的 max_turns 与请求级模型调用预算（AgentRunMeter）中先到的那个。
SOFT_LANDING_TURNS = 3
# 收尾提醒由哪个上限触发（_ModelCallCountingFilter.landing_limit）。
LANDING_LIMIT_MAX_TURNS = "max_turns"
LANDING_LIMIT_MODEL_CALL_BUDGET = "model_call_budget"
LANDING_LIMIT_WALL_CLOCK = WALL_CLOCK_LIMIT
# 收尾提醒：正文用 write_chapter（资料文件用 create_file）建好后等 <file>。
LOOP_SOFT_LANDING_PROMPT = (
    "[系统提醒] 本轮工具调用即将达到上限（本次之后最多还能调用模型 {remaining} 次）。"
    "请立即停止调用任何工具（包括 read_files / search_files / edit_file）。"
    "如果刚调用 write_chapter（或 create_file）还没写 <file> 正文，先在本次回复里写完它的"
    " <file> 正文，再做总结。"
    "然后直接用文字给出阶段总结：\n"
    "1. 已完成的工作；\n"
    "2. 尚未完成、需要下一轮继续的工作；\n"
    "3. 本轮用到或改动过的文件（标题 + id）。\n"
    "用户回复「继续」后会从这里接着完成剩余部分。"
)
LOOP_SOFT_LANDING_PROMPT_EN = (
    "[System note] This round is approaching its tool-call limit (at most {remaining} model call(s) left after this one). "
    "Stop calling any tools immediately (including read_files / search_files / edit_file). "
    "If you just called write_chapter (or create_file) and have not written its <file> body yet, finish writing that "
    "<file>...</file> body in this reply first, then summarize. "
    "Provide a concise progress summary in the author's language:\n"
    "1. Work already completed;\n"
    "2. Remaining work to finish in the next turn;\n"
    "3. Files used or modified this round (title + id).\n"
    "When the author replies \"continue\", you will pick up from here."
)
LOOP_TIME_LANDING_PROMPT = (
    "[系统提醒] 这次请求的时间快用完了。请立即停止调用任何工具（包括 read_files / search_files / edit_file / "
    "write_chapter / create_file / update_project），思考尽量简短。"
    "正在写的 <file> 正文先写完并用 </file> 结束；刚调用 write_chapter（或 create_file）还没写正文的，"
    "在本次回复里写完它的 <file> 正文并用 </file> 结束。然后直接用文字给作者收尾：\n"
    "- 如果作者本轮要求的内容已经全部保存完成，直接用一到三句话总结已完成的文件与字数即可，不要编造待续工作；\n"
    "- 只有作者本轮明确要求、但还没来得及完成的部分，才如实写明已完成的工作与尚未完成的部分（用户回复「继续」后会接着完成剩余部分）。"
)
LOOP_TIME_LANDING_PROMPT_EN = (
    "[System note] This request is nearly out of time. Stop calling ALL tools immediately (including read_files / search_files / "
    "edit_file / write_chapter / create_file / update_project) and keep thinking minimal. "
    "If a <file> body is in progress or pending after write_chapter/create_file, finish writing it and close with </file>. "
    "Then wrap up directly in the author's language:\n"
    "- If everything the author requested this turn is already saved, summarize the saved files and word counts in 1–3 sentences without inventing remaining tasks;\n"
    "- Only if part of the author's explicit request could not be finished in time, state plainly what is done and what remains (the author can reply \"continue\" to finish the rest)."
)


@dataclasses.dataclass
class RunExecutionPolicy:
    """外层单循环交给 runner 的单次 run 收尾策略。

    ``defer_wall_clock_soft_landing=True``（例如保存后自查 run）：
    触发墙钟软着陆时不直接发 ``ITERATION_EXHAUSTED`` 与 ``stop_reason=run_deadline_exceeded``，
    而是把暂停卡载荷记在 ``deferred_wall_clock_exhausted`` 上、照常发 ``MESSAGE_END(stop_reason="end_turn")``，
    由外层循环核验结构化完成事实后决定正常结束还是补发暂停卡。
    """

    defer_wall_clock_soft_landing: bool = False
    deferred_wall_clock_exhausted: dict[str, Any] | None = None


def _stop_run_after_tools(
    ctx: Any,
    tool_results: list[Any],
    *,
    failure_breaker: ToolFailureBreaker | None = None,
    run_meter: AgentRunMeter | None = None,
    read_guard: RepeatReadGuard | None = None,
) -> Any:
    """SDK 的 tool_use_behavior 回调：本轮工具跑完后，需要停下时立即结束当前 run。

    为什么不能只靠 result.cancel(mode="after_turn")：cancel 是异步落地的——
    SDK run-loop 把 tool_output 放进自己的 _event_queue，本模块的消费循环读到后才
    cancel；等标志位落地时 run-loop 早已发起了
    下一次模型调用，那一轮的正文会被推给前端、工具会被真实执行。
    这里用 SDK 在 turn 内同步检查的 tool_use_behavior（turn_resolution.py
    check_for_final_output_from_tools），run-loop 不会再发起下一次模型调用。

    工具失败熔断（failure_breaker）：熔断器在工具回调里同步记账，本轮工具一跑完这里
    就能看到，run-loop 不会再为「原样重试」发起下一次模型调用。重复读取守卫（read_guard）
    判定无进展、请求级模型调用预算用尽、待写正文被前置守卫挡回时同样走这个出口。
    """
    from agents.agent import ToolsToFinalOutputResult

    if failure_breaker is not None and failure_breaker.trip is not None:
        return ToolsToFinalOutputResult(
            is_final_output=True,
            final_output=failure_breaker.trip.user_message(),
        )
    if read_guard is not None and read_guard.trip is not None:
        return ToolsToFinalOutputResult(
            is_final_output=True,
            final_output=read_guard.trip.user_message(),
        )

    # 请求级模型调用预算：本轮工具已经执行完，预算用尽就不再发起下一次模型调用（与熔断同一个同步出口）。
    if run_meter is not None and run_meter.exhausted:
        run_meter.budget_stopped = True
        return ToolsToFinalOutputResult(is_final_output=True, final_output="")

    # 建文件后的下一次回复没写正文就调了工具、被前置守卫挡回（结构化 file_body_pending），且这份
    # 文件此刻仍在等正文：不让模型在本 run 里接着盲试，正常结束这次 run（MESSAGE_END 照常带用量），
    # 交给单循环 agent 落库核验，再经 FILE_BODY_RESUME 为同一个文件安排纠偏轮。以上终态都优先于它。
    if any(_is_file_body_pending(tool_result) for tool_result in tool_results or []):
        req_ctx = unwrap_request_context(ctx)
        if req_ctx is not None and req_ctx.overdue_file_bodies():
            return ToolsToFinalOutputResult(is_final_output=True, final_output="")

    # 墙钟收尾提醒与 wall_clock_wrap_up 挡回后模型仍连续多次只调工具不写总结：
    # 同步结束当前 run，按既有墙钟软着陆收尾，避免一路拖到 780s 墙钟硬超时被截断。
    if run_meter is not None and run_meter.wall_clock_landing_exhausted:
        return ToolsToFinalOutputResult(is_final_output=True, final_output="")

    return ToolsToFinalOutputResult(is_final_output=False, final_output=None)


def _is_file_body_pending(tool_result: Any) -> bool:
    output = getattr(tool_result, "output", "")
    payload = _parse_error_payload(output if isinstance(output, str) else str(output))
    return payload is not None and payload.get("error_type") == "file_body_pending"


_LANDING_WRITE_FACTS_MAX_FILES = 20


def _format_landing_file_label(file_id: str, title: str, *, english: bool) -> str:
    clean_title = title.strip()
    if clean_title:
        return f'"{clean_title}" (id={file_id})' if english else f"《{clean_title}》(id={file_id})"
    return f"id={file_id}"


def _landing_write_facts(
    read_guard: RepeatReadGuard | None,
    request_context: AgentRequestContext | None,
    *,
    english: bool = False,
) -> str:
    """生成收尾提醒附带的本轮真实写入事实清单（基于结构化执行台账，绝不从文本推测）。"""
    if read_guard is None and request_context is None:
        return ""
    pending_entries = request_context.get_pending_empty_files() if request_context is not None else []
    pending_by_id: dict[str, str] = {}
    for entry in pending_entries:
        fid = str(entry.get("file_id") or entry.get("id") or "").strip()
        if fid:
            pending_by_id[fid] = str(entry.get("title") or "").strip()

    unsaved_replacements = (
        request_context.unsaved_chapter_replacement_ids() if request_context is not None else set()
    )
    saved_bodies = request_context.saved_bodies_this_request() if request_context is not None else {}
    deleted_ids = set(read_guard.files_deleted) if read_guard is not None else set()
    written: dict[str, str] = {}
    deleted: dict[str, str] = {}
    if read_guard is not None:
        for fid, title in read_guard.files_written.items():
            clean_id = str(fid or "").strip()
            if not clean_id:
                continue
            clean_title = str(title or "").strip()
            if clean_id in deleted_ids:
                deleted[clean_id] = clean_title
            elif clean_id not in pending_by_id and clean_id not in unsaved_replacements:
                written[clean_id] = clean_title
    for fid, title in saved_bodies.items():
        clean_id = str(fid or "").strip()
        if clean_id and clean_id not in deleted_ids and clean_id not in unsaved_replacements:
            written[clean_id] = str(title or "").strip() or written.get(clean_id, "")

    unidentified_write = bool(
        not written
        and not deleted
        and read_guard is not None
        and read_guard.write_succeeded
        and not pending_by_id
    )

    parts: list[str] = []
    if written:
        items = [
            _format_landing_file_label(fid, title, english=english)
            for fid, title in list(written.items())[:_LANDING_WRITE_FACTS_MAX_FILES]
        ]
        if english:
            listed = ", ".join(items)
            if len(written) > _LANDING_WRITE_FACTS_MAX_FILES:
                listed += f" (and {len(written) - _LANDING_WRITE_FACTS_MAX_FILES} more)"
            parts.append(
                f"[Actual writes this round] Files actually written or modified in this round are strictly limited to: "
                f"{listed}. No other files were changed; do NOT claim edits to files outside this list."
            )
        else:
            listed = "、".join(items)
            if len(written) > _LANDING_WRITE_FACTS_MAX_FILES:
                listed += f"等共 {len(written)} 份文件"
            parts.append(
                f"[本轮实际写入事实] 本轮实际已写入/修改的文件仅限：{listed}。"
                "未在此清单中的文件均未改动，严禁宣称清单外的文件已修改或已保存。"
            )
    elif deleted:
        del_items = [
            _format_landing_file_label(fid, title, english=english)
            for fid, title in list(deleted.items())[:_LANDING_WRITE_FACTS_MAX_FILES]
        ]
        if english:
            parts.append(
                f"[Actual writes this round] No file contents were saved; files deleted in this round: "
                f"{', '.join(del_items)}. Do NOT claim any other file was modified or saved."
            )
        else:
            parts.append(
                f"[本轮实际写入事实] 本轮未保存新正文，仅删除了以下文件：{'、'.join(del_items)}。"
                "严禁宣称其他文件已修改或已保存。"
            )
    elif unidentified_write:
        if english:
            parts.append(
                "[Actual writes this round] A write succeeded in this round without full file details; "
                "report only edits directly confirmed by tool results and do NOT claim unexecuted changes."
            )
        else:
            parts.append(
                "[本轮实际写入事实] 本轮仅有工具结果明确确认的写入生效，严禁宣称未执行或失败的改动已保存。"
            )
    elif pending_by_id:
        pending_items = [
            _format_landing_file_label(fid, title, english=english)
            for fid, title in list(pending_by_id.items())[:_LANDING_WRITE_FACTS_MAX_FILES]
        ]
        if english:
            parts.append(
                f"[Actual writes this round] No file body has been saved yet in this round; "
                f"files still awaiting <file> body content: {', '.join(pending_items)} (not yet saved). "
                "Finish writing the pending <file>…</file> body first as instructed above; "
                "do NOT claim any other file was modified or saved."
            )
        else:
            parts.append(
                f"[本轮实际写入事实] 本轮目前尚无已完成保存的文件正文；仍在等待 <file> 正文写入的文件："
                f"{'、'.join(pending_items)}（正文尚未保存）。请先按上方提醒写完它的 <file>…</file> 正文，"
                "严禁宣称其他文件已修改或已保存。"
            )
        return "\n" + "\n".join(parts)
    else:
        if english:
            parts.append(
                "[Actual writes this round] No project files have been modified or saved in this round. "
                "State accurately that no project files were changed; "
                "do NOT claim any file was edited, updated, or saved."
            )
        else:
            parts.append(
                "[本轮实际写入事实] 本轮尚未修改或保存任何项目文件。"
                "总结时请如实说明未改动项目文件，严禁宣称任何文件「已修改」「已统一」或「已保存」。"
            )

    if pending_by_id:
        pending_items = [
            _format_landing_file_label(fid, title, english=english)
            for fid, title in list(pending_by_id.items())[:_LANDING_WRITE_FACTS_MAX_FILES]
        ]
        if english:
            parts.append(
                f"Files still awaiting <file> body content (not yet saved): {', '.join(pending_items)}."
            )
        else:
            parts.append(
                f"仍在等待 <file> 正文写入的文件：{'、'.join(pending_items)}（正文尚未保存）。"
            )

    return "\n" + "\n".join(parts)


class _ModelCallCountingFilter:
    """call_model_input_filter：每次模型调用前计数，再交给内层过滤器处理输入。

    同时负责「到上限前软着陆」：本次之后还能发起的模型调用数——取本次 run 的
    max_turns 余量与请求级预算（run_meter）余量中较小的那个——少于
    SOFT_LANDING_TURNS 时，在内层过滤器的结果末尾追加一条收尾提醒
    （LOOP_SOFT_LANDING_PROMPT），让模型停止调用工具、写阶段总结，而不是读到上限被
    SDK 截断 / 被预算硬停、什么也没留下。
    提醒追加在 input 末尾而不是改 instructions：系统提示是 DeepSeek 前缀缓存的开头，
    改它会让最后几次调用整段缓存失效。提醒只进本次调用，不写回 run 的历史。
    """

    def __init__(
        self,
        inner: Callable[[Any], Any],
        run_meter: AgentRunMeter,
        *,
        max_turns: int | None = None,
        landing_turns: int = SOFT_LANDING_TURNS,
        read_guard: RepeatReadGuard | None = None,
        request_context: AgentRequestContext | None = None,
        english: bool = False,
    ) -> None:
        self._inner = inner
        self._run_meter = run_meter
        self._max_turns = max_turns
        self._landing_turns = landing_turns
        self._read_guard = read_guard
        self._request_context = request_context
        self._english = english
        # 本次 SDK run 已发起的模型调用数（区别于 run_meter 的请求级累计）。
        self.calls = 0
        self.landing_injected = False
        # 最近一次收尾提醒是哪个上限触发的（LANDING_LIMIT_*）；没有提醒时为 None。
        self.landing_limit: str | None = None

    def _remaining_calls(self) -> tuple[int, str]:
        """本次之后还能发起的模型调用数，以及先到的那个上限。"""
        remaining = self._run_meter.max_model_calls - self._run_meter.model_calls
        limit = LANDING_LIMIT_MODEL_CALL_BUDGET
        if self._max_turns:
            per_run = self._max_turns - self.calls
            if per_run <= remaining:
                remaining, limit = per_run, LANDING_LIMIT_MAX_TURNS
        return remaining, limit

    def _landing_remaining(self) -> int | None:
        """进入收尾区间时返回本次之后还剩的调用次数，否则 None。

        余量 = min(max_turns - calls, run_meter.max_model_calls - run_meter.model_calls)。
        """
        if self._landing_turns <= 0:
            return None
        remaining, _limit = self._remaining_calls()
        if remaining >= self._landing_turns:
            return None
        return max(remaining, 0)

    def _with_landing(self, model_data: Any) -> Any:
        remaining = self._landing_remaining()
        if remaining is None and not self._run_meter.past_wrap_up:
            return model_data
        from agents.run_config import ModelInputData

        self.landing_injected = True
        facts = _landing_write_facts(
            self._read_guard,
            self._request_context,
            english=self._english,
        )
        if remaining is not None:
            self.landing_limit = self._remaining_calls()[1]
            if self._run_meter.past_wrap_up:
                self._run_meter.record_wall_clock_landing_call()
            prompt = LOOP_SOFT_LANDING_PROMPT_EN if self._english else LOOP_SOFT_LANDING_PROMPT
            content = prompt.format(remaining=remaining) + facts
        else:
            self.landing_limit = LANDING_LIMIT_WALL_CLOCK
            self._run_meter.record_wall_clock_landing_call()
            prompt = LOOP_TIME_LANDING_PROMPT_EN if self._english else LOOP_TIME_LANDING_PROMPT
            content = prompt + facts
        reminder = {"role": "user", "content": content}
        return ModelInputData(
            input=[*list(model_data.input or []), reminder],
            instructions=model_data.instructions,
        )

    def __call__(self, data: Any) -> Any:
        self._run_meter.record_model_call()
        self.calls += 1
        result = self._inner(data)
        if inspect.isawaitable(result):

            async def _await_then_land() -> Any:
                return self._with_landing(await result)

            return _await_then_land()
        return self._with_landing(result)


def _is_new_model_turn_event(sdk_event: Any) -> bool:
    """事件是否意味着「模型又开了新的一轮」。

    单个 turn 内，模型响应流（raw_response_event）先跑完，SDK 才执行工具并发出
    tool_output；因此终态（熔断等）那次 tool_output 之后再出现任何 raw_response_event，
    或再出现 tool_called，都只能来自新的一轮模型调用。
    """
    event_type = str(getattr(sdk_event, "type", "") or "")
    if event_type == "raw_response_event":
        return True
    return event_type == "run_item_stream_event" and getattr(sdk_event, "name", "") == "tool_called"


def _safe_cancel(result: Any, mode: str) -> None:
    """调用 SDK 的 cancel，忽略实现差异/已结束 run 带来的异常。"""
    cancel = getattr(result, "cancel", None)
    if not callable(cancel):
        return
    with contextlib.suppress(Exception):
        cancel(mode=mode)


def _sanitized_early_stop_items(result: Any) -> list[Any] | None:
    """提取早停前已完成的 SDK 输入项，供同轮 FILE_BODY_RESUME 复用前缀缓存。

    早停发生在当前模型回复的流式阶段（尚未形成完整 ModelResponse），因此
    ``to_input_list()`` 只含此前已完成的轮次（如刚返回工具结果的 write_chapter）。
    防御性剔除任何末尾悬空的 reasoning 项或未配对的工具调用尾段，优先保留最长成对前缀，
    避免整表退回 None 清空 ``state["run_items"]``、导致纠偏轮丢失本轮作者原始消息。
    """
    raw_items: list[Any] = []
    with contextlib.suppress(Exception):
        raw_items = list(result.to_input_list())
    if not raw_items:
        return None
    pending_call_ids: set[str] = set()
    last_balanced_len = 0
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            break
        item_type = item.get("type")
        if item_type == "function_call":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id or call_id in pending_call_ids:
                break
            pending_call_ids.add(call_id)
        elif item_type == "function_call_output":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id or call_id not in pending_call_ids:
                break
            pending_call_ids.remove(call_id)
            if not pending_call_ids:
                last_balanced_len = idx + 1
        elif pending_call_ids:
            break
        else:
            last_balanced_len = idx + 1
    if last_balanced_len <= 0:
        return None
    trimmed = raw_items[:last_balanced_len]
    while trimmed and isinstance(trimmed[-1], dict) and trimmed[-1].get("type") == "reasoning":
        trimmed.pop()
    return trimmed or None


def extract_text_from_message_content(content: Any) -> str:
    """Convert persisted mixed content blocks to plain text for Chat Completions input."""
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

        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        # tool_use / tool_result blocks are intentionally omitted from replay. The SDK owns
        # the live tool-call loop within a single agent run; re-serializing a prior turn's
        # tool calls/results as plain prose would inject raw tool JSON (file ids, arguments)
        # into the next run as if the *user* had said it, degrading instruction-following.
        # The loop agent replays prior tool calls as paired SDK items (agent/loop/history.py),
        # which also avoids any orphaned-tool_call_id risk from replaying partial tool turns.
        # thinking/reasoning blocks are likewise omitted (persisted for UI/history only).

    return "\n".join(part for part in parts if part.strip())


def normalize_messages_for_openai_agents(messages: MessageList) -> MessageList:
    """Normalize project chat history to SDK easy-input messages."""
    normalized: MessageList = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system", "developer"}:
            continue

        text = extract_text_from_message_content(message.get("content"))
        if role == "assistant" and message.get("stop_reason") == OUTPUT_TRUNCATED_STOP_REASON:
            text = f"{text}\n\n{OUTPUT_TRUNCATED_HISTORY}".strip()
        if not text.strip():
            continue

        normalized.append({"role": role, "content": text})

    return normalized


def build_run_input(
    base_items: list[Any],
    *,
    pending_steering: list[str] | None = None,
    user_message: str = "",
    initial_steering: MessageList | None = None,
    agent_directive: str = "",
) -> list[Any]:
    """组装这次 SDK run 的输入，只在末尾追加以保住前缀缓存。

    - 首次 run：``base_items`` 为 ``state["history_items"]``（跨轮历史），末尾追加本轮用户消息、
      初始插话与运行指令。
    - 续跑 run：``base_items`` 为 ``state["run_items"]``（上一轮完整的 ``result.to_input_list()``，
      已包含上一轮的工具调用与结果），末尾追加上一轮未消费的插话、本次续跑说明（``state["user_message"]``）
      与新的初始插话。
    - ``agent_directive`` 用 user 角色放在末尾：DeepSeek 会把所有 system 消息合并到最前面，
      放在末尾的 system 消息会打断前缀缓存。
    """
    items: list[Any] = list(base_items)
    for content in pending_steering or ():
        if content:
            items.append({"role": "user", "content": content})
    if user_message:
        items.append({"role": "user", "content": user_message})
    if initial_steering:
        items.extend(initial_steering)
    if agent_directive:
        items.append({"role": "user", "content": agent_directive})
    return items


def _take_pending_steering(state: WritingState) -> list[str]:
    """取出并清空 state["pending_steering"] 中尚未喂进 SDK 的插话文本。"""
    raw = state.pop("pending_steering", None)
    if not isinstance(raw, list):
        return []
    return [str(content) for content in raw if isinstance(content, str) and content]


def append_pending_steering(state: WritingState, messages: list[str]) -> None:
    """把运行中/边界消费到、尚未喂进下一次 SDK run 的插话文本记进 state["pending_steering"]。"""
    non_empty = [content for content in messages if content]
    if not non_empty:
        return
    pending = list(state.get("pending_steering") or [])
    pending.extend(non_empty)
    state["pending_steering"] = pending


def _raw_event_type(raw_event: Any) -> str:
    return str(getattr(raw_event, "type", "") or "")


def _raw_event_delta(raw_event: Any) -> str:
    delta = getattr(raw_event, "delta", "")
    return delta if isinstance(delta, str) else str(delta or "")


def _raw_item_value(raw_item: Any, key: str, default: Any = None) -> Any:
    if isinstance(raw_item, dict):
        return raw_item.get(key, default)
    return getattr(raw_item, key, default)


def _tool_call_payload(item: Any) -> tuple[str, str, dict[str, Any]]:
    tool_name = getattr(item, "tool_name", None) or _raw_item_value(getattr(item, "raw_item", None), "name", "")
    call_id = getattr(item, "call_id", None) or _raw_item_value(getattr(item, "raw_item", None), "call_id", "")
    raw_arguments = _raw_item_value(getattr(item, "raw_item", None), "arguments", "")
    if raw_arguments is None:
        raw_arguments = ""
    parsed, parse_error, _metadata = parse_json_object(str(raw_arguments), tool_name=str(tool_name or ""))
    if parse_error is not None:
        parsed = {}
    return str(tool_name or ""), str(call_id or ""), parsed


def _tool_output_payload(item: Any) -> tuple[str, str]:
    call_id = getattr(item, "call_id", None) or _raw_item_value(getattr(item, "raw_item", None), "call_id", "")
    output = getattr(item, "output", "")
    return str(call_id or ""), output if isinstance(output, str) else str(output)


def _emit_terminal_error(
    result: Any,
    *,
    stop_reason: str,
    error_data: dict[str, Any],
) -> tuple[StreamEvent, StreamEvent]:
    """Build the ordered terminal MESSAGE_END + ERROR pair so usage is always recorded before stopping."""
    return (
        StreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={
                "stop_reason": stop_reason,
                "usage": _usage_dict_from_result(result),
            },
        ),
        StreamEvent(
            type=StreamEventType.ERROR,
            data=error_data,
        ),
    )


def _int_attr(source: Any, name: str) -> int:
    """读取 usage 上的整数字段，缺失/None/非法一律按 0 处理。"""
    value = getattr(source, name, 0)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _last_response_output_tokens(result: Any) -> int:
    """本次 SDK run 最后一次模型响应的 output_tokens（DeepSeek 的 completion_tokens 含思考）。"""
    raw_responses = getattr(result, "raw_responses", None)
    if not isinstance(raw_responses, list) or not raw_responses:
        return 0
    return _int_attr(getattr(raw_responses[-1], "usage", None), "output_tokens")


def _last_response_is_thinking_only(result: Any) -> bool:
    """最后一次模型响应里只有思考：没有正文，也没有工具调用。

    只看最后一次响应，而不是整个 run：续跑的 run 常先调工具（例如重读稿件），
    之后那次响应才只剩思考并撞上输出上限；按整个 run 判断会漏掉这种情况。
    """
    raw_responses = getattr(result, "raw_responses", None)
    if not isinstance(raw_responses, list) or not raw_responses:
        return False
    for item in getattr(raw_responses[-1], "output", None) or []:
        item_type = _raw_item_value(item, "type", "")
        if item_type == "function_call":
            return False
        if item_type == "message":
            for block in _raw_item_value(item, "content", None) or []:
                text = _raw_item_value(block, "text", "")
                if isinstance(text, str) and text.strip():
                    return False
    return True


def _usage_dict_from_result(result: Any) -> dict[str, int]:
    """汇总本次 SDK run 全部模型响应的规范化 token 用量（委托给 agent.core.usage）。"""
    raw_responses = getattr(result, "raw_responses", None)
    if not isinstance(raw_responses, list):
        return {}
    return sum_response_usages(getattr(raw_response, "usage", None) for raw_response in raw_responses)


def _format_tool_error(
    args: Any,
    *,
    failure_breaker: ToolFailureBreaker | None = None,
    english: bool = False,
) -> str | None:
    """RunConfig.tool_error_formatter：把「工具不存在」改写成项目统一的结构化错误。

    只处理 kind == "tool_not_found"（其余返回 None 走 SDK 默认文案）。
    不存在或已停用的工具名不会计入 ToolFailureBreaker 熔断，只返回清晰的结构化引导，
    让模型直接改用当前可用的工具清单。
    """
    if getattr(args, "kind", None) != "tool_not_found":
        return None
    tool_name = str(getattr(args, "tool_name", "") or "")
    message = (
        f"Tool `{tool_name}` is not in the currently available tool list; the call was not executed. "
        "Use only tools provided in the current tool list and do not call retired or nonexistent tool names."
        if english
        else f"工具 {tool_name} 不在当前可用的工具清单里，调用未执行。"
        "请只使用当前已提供的可用工具完成任务，不要再调用已停用或不存在的工具名。"
    )
    text = tool_error_text(
        message,
        error_type="tool_not_found",
        tool_name=tool_name,
    )
    if failure_breaker is not None:
        text = failure_breaker.observe(tool_name, "", text)
    return text


async def _preferred_model_name(session_id: str | None = None) -> str:
    """The model this run's first call will try (MESSAGE_START, ledger fallback)."""
    from agent.core import llm_failover, llm_providers

    try:
        return (await llm_failover.aordered_providers(session_id))[0].model
    except Exception:  # noqa: BLE001 - a missing key surfaces from the model call itself
        return llm_providers.primary_model_name()


def _build_agent(
    agent_type: str,
    system_prompt: str,
    *,
    failure_breaker: ToolFailureBreaker | None = None,
    run_meter: AgentRunMeter | None = None,
    read_guard: RepeatReadGuard | None = None,
    edit_guard: RepeatEditGuard | None = None,
    request_context: AgentRequestContext | None = None,
) -> Any:
    # NOTE — Agent.as_tool was evaluated and rejected.
    # Agent.as_tool wraps an agent as a callable tool for a parent agent, which
    # would collapse each sub-agent's SSE events into a single opaque tool result
    # and eliminate the streaming visibility the UI depends on.  It would also merge
    # the two independent iteration budgets (AGENT_TOOL_CALL_MAX_ITERATIONS per run,
    # AGENT_COLLABORATION_MAX_ITERATIONS runs per turn) into one.
    #
    # NOTE — reasoning lever (verified, intentionally not set for DeepSeek).
    # MiniMax gets MINIMAX_REASONING_EFFORT (model._settings_for) because its own default is max.
    # deepseek-flash's Chat Completions endpoint accepts two reasoning controls (live probe
    # 2026-10-03): ``reasoning_effort`` (none|minimal|low|medium|high|xhigh|ultra|max;
    # unknown values return 422) and ``extra_body={"thinking": {"type": "disabled"}}``.
    # ModelSettings exposes them as ``reasoning={"effort": ...}`` / ``extra_body``.
    # The writing agent deliberately runs at the model's default effort (high): lowering it
    # trades writing/tool-planning quality for latency and tokens. The controlled comparison
    # (2026-10-19) covered deepseek-flash only; MiniMax has just a small latency sample with
    # no quality conclusion (2026-10-21 primary-model-slow-turns).  LLMClient.acomplete(thinking_enabled=False) already uses the
    # ``thinking`` toggle for short single-shot calls (suggestions, polish).
    # #278: AGENT_REASONING_EFFORT exists only for controlled comparisons; it is unset in
    # production (see .agents/notes/.../2026-10-19-reasoning-effort-evaluation.md).
    from agents import Agent, ModelSettings, set_tracing_disabled
    from openai.types.shared import Reasoning

    # 与 RunConfig(tracing_disabled=True) 双保险：在任何 run 开始前全局关掉 SDK tracing。
    set_tracing_disabled(True)

    from agent.openai_agents.usage_hooks import agent_usage_attribution

    effort = agent_runtime.AGENT_REASONING_EFFORT
    session_id = request_context.session_id if request_context is not None else None

    return Agent(
        name=agent_type,
        instructions=system_prompt,
        model=build_failover_chat_model(
            session_id,
            on_served=run_meter.record_served_model if run_meter is not None else None,
            on_served_provider=run_meter.record_served_provider if run_meter is not None else None,
            # Failed and cancelled attempts get a zero-token ledger row (admin model metrics).
            usage_attribution=agent_usage_attribution(request_context),
        ),
        model_settings=ModelSettings(
            temperature=1.0,
            top_p=0.95,
            max_tokens=AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS,
            # include_usage 决定请求体里是否带 stream_options={"include_usage": true}。
            # SDK 的默认值只在 client 指向 api.openai.com 时才是 True
            # （chatcmpl_helpers.get_stream_options_param），而本项目走 DeepSeek / MiniMax，
            # 不显式打开的话 OpenAI 兼容流式接口不会补发带 usage 的末尾 chunk，
            # MESSAGE_END 的 usage 恒为空，用量统计只能退化成「字符数/4」的估算。
            include_usage=True,
            **({"reasoning": Reasoning(effort=effort)} if effort else {}),  # type: ignore[arg-type]
        ),
        tools=build_agent_function_tools(
            failure_breaker=failure_breaker,
            read_guard=read_guard,
            edit_guard=edit_guard,
            run_meter=run_meter,
        ),
        # 工具失败熔断等终态时即结束 run，避免 SDK 再跑一整轮模型调用并真实执行其工具
        # （详见 _stop_run_after_tools）。
        tool_use_behavior=functools.partial(
            _stop_run_after_tools,
            failure_breaker=failure_breaker,
            run_meter=run_meter,
            read_guard=read_guard,
        ),
    )


async def _inject_initial_steering(
    api_messages: MessageList,
    get_steering_messages: Callable[[], Any] | None,
) -> AsyncIterator[StreamEvent]:
    if get_steering_messages is None:
        return

    try:
        steering_msgs = await get_steering_messages()
        for msg in steering_msgs or []:
            content = str(msg.get("content") or "") if isinstance(msg, dict) else ""
            if not content:
                continue
            api_messages.append({"role": "user", "content": content})
            from agent.core.events import steering_received_event

            yield steering_received_event(
                message_id=str(msg.get("id") or "") if isinstance(msg, dict) else "",
                preview=content[:50],
            )
    except Exception as exc:
        log_with_context(
            logger,
            40,  # ERROR
            "Failed to retrieve steering messages for OpenAI Agents run",
            error=str(exc),
            error_type=type(exc).__name__,
        )


async def _consume_boundary_steering(
    collected: list[str],
    get_steering_messages: Callable[[], Any] | None,
) -> AsyncIterator[StreamEvent]:
    """Consume steering that arrived while the SDK run is executing.

    openai-agents 不支持向进行中的 run 注入消息，工具输出边界只做消费与
    前端确认；内容暂存 collected，run 结束后写进 state["pending_steering"]，由单循环 agent
    决定是否追加一次 run 让引导在本次请求内生效。
    """
    if get_steering_messages is None:
        return
    try:
        steering_msgs = await get_steering_messages()
    except Exception as exc:
        log_with_context(
            logger,
            40,  # ERROR
            "Failed to retrieve steering messages at tool boundary",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return
    for msg in steering_msgs or []:
        content = str(msg.get("content") or "") if isinstance(msg, dict) else ""
        if not content:
            continue
        collected.append(content)
        from agent.core.events import steering_received_event

        yield steering_received_event(
            message_id=str(msg.get("id") or "") if isinstance(msg, dict) else "",
            preview=content[:50],
        )


async def run_openai_agents_streaming_agent(
    state: WritingState,
    agent_type: str,
    system_prompt: str,
    get_steering_messages: Callable[[], Any] | None = None,
    failure_breaker: ToolFailureBreaker | None = None,
    agent_directive: str = "",
    run_policy: RunExecutionPolicy | None = None,
    request_context: AgentRequestContext | None = None,
) -> AsyncIterator[StreamEvent]:
    """Run one writing agent via openai-agents-python and yield workflow events.

    agent_directive：这次运行的系统指令（例如单循环 agent 的截断后交付说明），作为输入最后一条消息
    （见 build_run_input）。

    failure_breaker：工具失败熔断器。不传时取 state["tool_failure_breaker"]
    （单循环 agent 每个请求建一个、跨 run 共享）；都没有时本次 run 自建一个。
    重复读取守卫同理取 state["repeat_read_guard"]。
    """
    state.setdefault("previous_output_truncations", 0)
    req_ctx = (
        request_context
        if isinstance(request_context, AgentRequestContext)
        else state.get("request_context")
        if isinstance(state.get("request_context"), AgentRequestContext)
        else AgentRequestContext(
            project_id=state.get("project_id") or None,
            user_id=state.get("user_id") or None,
            session_id=state.get("session_id"),
        )
    )
    response_length = 0
    thinking_chars = 0
    tool_calls_count = 0
    tool_names_by_call_id: dict[str, str] = {}
    mid_run_steering: list[str] = []
    # 工具失败熔断器按请求共享：同一请求的多次 run 不能让「同一调用连续失败」的计数归零。
    # 消费循环在 tool_output 处看到它已熔断后不再接受新的模型轮次。
    if failure_breaker is None:
        shared_breaker = state.get("tool_failure_breaker")
        failure_breaker = (
            shared_breaker
            if isinstance(shared_breaker, ToolFailureBreaker)
            else ToolFailureBreaker()
        )
    failure_stopped = False
    # 重复读取守卫按请求共享读写台账与拦截次数；首次 run 清零读取计数，续跑直接接在
    # 上一轮完整的 SDK 输入项（state["run_items"]）后面，读到的内容仍在上下文里，不清零。
    shared_guard = state.get("repeat_read_guard")
    read_guard = (
        shared_guard
        if isinstance(shared_guard, RepeatReadGuard)
        else RepeatReadGuard(request_context=req_ctx)
    )
    if read_guard.request_context is None:
        read_guard.request_context = req_ctx
    shared_edit_guard = state.get("repeat_edit_guard")
    edit_guard = (
        shared_edit_guard
        if isinstance(shared_edit_guard, RepeatEditGuard)
        else RepeatEditGuard(request_context=req_ctx)
    )
    if edit_guard.request_context is None:
        edit_guard.request_context = req_ctx
    previous_items = state.get("run_items")
    continuing = isinstance(previous_items, list) and bool(previous_items)
    if not continuing:
        read_guard.begin_agent_run()
    no_progress_stopped = False
    # 模型在一次调用里把一大段原样再写一遍（循环）：立即停下这次 run（见 find_repetition_start）。
    repetition_stopped = False
    # 等 <file> 正文时模型开头直接吐出泄漏的 tool_call 协议头：立即早停并转入 FILE_BODY_RESUME 纠偏。
    leaked_tool_call_stopped = False
    call_text: list[str] = []
    call_text_length = 0
    repetition_checked_at = 0
    call_head_checked = False
    english = is_non_zh_language(state.get("language"))
    active_tool_arg_streams: dict[str, _ToolArgStreamState] = {}
    last_tool_arg_stream_key: str | None = None
    max_turns_exhausted = False
    turn_filter: _ModelCallCountingFilter | None = None
    # 请求级模型调用预算（单循环 agent 建一个跨 run 共享；单独调用 runner
    # 时自建一个默认预算）。
    shared_meter = state.get("run_meter")
    run_meter = shared_meter if isinstance(shared_meter, AgentRunMeter) else AgentRunMeter()
    budget_stopped = False
    run_started_at = time.monotonic()
    # 这次 run 正常结束时它完整的 SDK 输入（原输入 + 模型输出与工具结果），供同轮续跑与 wire_prefix_cache 复用。
    completed_items: list[Any] | None = None

    if run_meter.exhausted:
        # 上一个 agent run 已经把预算用完：不再发起任何模型调用，直接以预算用尽结束整条工作流。
        log_with_context(
            logger,
            30,  # WARNING
            "Model call budget exhausted before agent run",
            agent_type=agent_type,
            model_calls=run_meter.model_calls,
            max_model_calls=run_meter.max_model_calls,
        )
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data=model_call_limit_error().as_event_data(
                error_type=MODEL_CALL_LIMIT_ERROR_TYPE, agent_type=agent_type
            ),
        )
        return

    initial_steering: MessageList = []
    async for steering_event in _inject_initial_steering(initial_steering, get_steering_messages):
        yield steering_event
    pending_steering = _take_pending_steering(state)
    base_items: list[Any] = (
        previous_items
        if continuing and isinstance(previous_items, list)
        else list(state.get("history_items") or [])
        if isinstance(state.get("history_items"), list)
        else []
    )
    run_input = build_run_input(
        base_items,
        pending_steering=pending_steering,
        user_message=str(state.get("user_message") or ""),
        initial_steering=initial_steering,
        agent_directive=agent_directive,
    )

    start_model = await _preferred_model_name(req_ctx.session_id)
    yield StreamEvent(
        type=StreamEventType.MESSAGE_START,
        data={"model": start_model, "agent_type": agent_type},
    )

    result: Any = None
    sdk_agent: Any = None
    sdk_stream_exhausted = False
    run_hooks: Any = None
    timing_outcome: TimingOutcome = "completed"
    try:
        from agents import RunConfig, Runner, ToolExecutionConfig
        from agents.exceptions import MaxTurnsExceeded

        from services.usage.cost_budget import on_model_transport_start

        from .intra_run_trimmer import IntraRunToolOutputTrimmer
        from .usage_hooks import build_agent_run_hooks

        sdk_agent = _build_agent(
            agent_type,
            system_prompt,
            failure_breaker=failure_breaker,
            run_meter=run_meter,
            read_guard=read_guard,
            edit_guard=edit_guard,
            request_context=req_ctx,
        )
        # 外层计数器给请求级模型调用预算记账（每次模型调用前调用一次），并在
        # 接近 max_turns 时追加收尾提醒（软着陆）。
        turn_filter = _ModelCallCountingFilter(
            IntraRunToolOutputTrimmer(),
            run_meter,
            max_turns=AGENT_TOOL_CALL_MAX_ITERATIONS,
            read_guard=read_guard,
            request_context=req_ctx,
            english=english,
        )
        run_hooks = build_agent_run_hooks(start_model, run_meter=run_meter, request_context=req_ctx)
        run_outcome = state.get("run_outcome")
        mark_attempted = getattr(run_outcome, "mark_provider_attempted", None)
        transport_scope = (
            on_model_transport_start(mark_attempted)
            if callable(mark_attempted)
            else contextlib.nullcontext()
        )
        with transport_scope:
            result = Runner.run_streamed(
                sdk_agent,
                input=run_input,
                context=req_ctx,
                max_turns=AGENT_TOOL_CALL_MAX_ITERATIONS,
                # 每次模型调用结束写一行用量账本（llm_usage_event），按调用自身时间计价；
                # 之后 run 报错/取消也不会丢掉已完成调用的用量。同一钩子在执行本轮工具前
                # 告诉空文件守卫"上一份正文已在本条回复里写完"（见 usage_hooks）。
                hooks=run_hooks,
                # Keep sequential tool execution until the Postgres concurrency gate is
                # separately enabled; each tool now uses its own short-lived DB Session
                # via AgentRequestContext.session_scope().
                run_config=RunConfig(
                    # 不向 OpenAI 的 trace 后端导出任何内容（作者的稿件与对话）。模型客户端在第一次
                    # 模型调用时才建，那时 SDK 已经为这次 run 开了 trace，所以这里按 run 关掉。
                    tracing_disabled=True,
                    trace_include_sensitive_data=False,
                    tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                    # IntraRunToolOutputTrimmer 只是上下文安全阀：输入未超 max_input_chars 时原样
                    # 返回、不改写任何历史（保住前缀缓存，也不让模型「忘掉」读过的文件而重读）；
                    # 超限才从最旧的读/写载荷开始折叠。见 intra_run_trimmer。
                    # 计数 + 软着陆见上方 turn_filter。
                    call_model_input_filter=turn_filter,
                    # 模型调用了工具集里没有的工具（例如照着旧会话历史调已停用的工具、
                    # 或幻觉出的工具名）：SDK 默认抛 ModelBehaviorError，整轮
                    # 以致命 ERROR 结束、原始异常文本直接展示给用户。改为把结构化错误
                    # 交还模型让它自行改用可用工具，不计入 ToolFailureBreaker 熔断。
                    tool_not_found_behavior="return_error_to_model",
                    tool_error_formatter=functools.partial(
                        _format_tool_error, failure_breaker=failure_breaker, english=english
                    ),
                ),
            )

        sdk_events = result.stream_events()
        exiting_via_generator_exit = False
        try:
            async for sdk_event in sdk_events:
                if (
                    failure_stopped or budget_stopped or no_progress_stopped
                    or repetition_stopped or leaked_tool_call_stopped
                ) and _is_new_model_turn_event(sdk_event):
                    # 兜底护栏：正常情况下 tool_use_behavior 已经让 run-loop 在终态
                    # 出现的那一刻结束，走不到这里。一旦走到（工具改名、SDK 行为
                    # 变化等），说明 SDK 又开了新的一轮——立即硬取消并截断消费循环，
                    # 绝不把暂停之后的正文/工具调用透给前端或写回 state.messages。
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Control-flow/failure stop did not end the SDK run; truncating extra turn",
                        agent_type=agent_type,
                        sdk_event_type=str(getattr(sdk_event, "type", "") or ""),
                    )
                    _safe_cancel(result, "immediate")
                    break
                event_type = getattr(sdk_event, "type", "")

                if event_type == "raw_response_event":
                    raw = getattr(sdk_event, "data", None)
                    raw_type = _raw_event_type(raw)
                    if raw_type == "response.created":
                        call_text, call_text_length, repetition_checked_at, call_head_checked = [], 0, 0, False
                        active_tool_arg_streams.clear()
                        last_tool_arg_stream_key = None
                    if raw_type == "response.output_text.delta":
                        text = _raw_event_delta(raw)
                        if text:
                            response_length += len(text)
                            yield StreamEvent(type=StreamEventType.TEXT, data={"text": text})
                            call_text.append(text)
                            call_text_length += len(text)
                            if not call_head_checked and req_ctx.overdue_file_bodies():
                                joined_head = "".join(call_text)
                                if FILE_START_PATTERN.search(joined_head) is not None:
                                    call_head_checked = True
                                elif starts_with_leaked_tool_call_marker(joined_head):
                                    log_with_context(
                                        logger,
                                        30,  # WARNING
                                        "Model emitted leaked tool_call protocol while awaiting <file> body; stopping run early",
                                        agent_type=agent_type,
                                        call_text_length=call_text_length,
                                    )
                                    leaked_tool_call_stopped = True
                                    _safe_cancel(result, "immediate")
                                    break
                                elif not could_be_leaked_tool_call_prefix(joined_head):
                                    call_head_checked = True
                            if call_text_length - repetition_checked_at >= _REPETITION_CHECK_EVERY_CHARS:
                                repetition_checked_at = call_text_length
                                if find_repetition_start("".join(call_text)) is not None:
                                    log_with_context(
                                        logger,
                                        30,  # WARNING
                                        "Model output repeats a long passage; stopping agent run",
                                        agent_type=agent_type,
                                        call_text_length=call_text_length,
                                    )
                                    repetition_stopped = True
                                    _safe_cancel(result, "immediate")
                                    break
                    elif raw_type in {
                        "response.reasoning_text.delta",
                        "response.reasoning_summary_text.delta",
                    }:
                        thinking = _raw_event_delta(raw)
                        if thinking:
                            thinking_chars += len(thinking)
                            yield StreamEvent(type=StreamEventType.THINKING, data={"thinking": thinking})
                    elif raw_type == "response.output_item.added":
                        added_item = getattr(raw, "item", None)
                        if _raw_item_value(added_item, "type", "") == "function_call":
                            tool_name = str(_raw_item_value(added_item, "name", "") or "")
                            raw_item_id = _real_raw_item_id(_raw_item_value(added_item, "id", ""))
                            output_index = getattr(raw, "output_index", None)
                            if raw_item_id:
                                stream_key = raw_item_id
                            elif output_index is not None:
                                stream_key = f"idx:{output_index}"
                            else:
                                stream_key = (
                                    _real_raw_item_id(_raw_item_value(added_item, "call_id", ""))
                                    or f"seq:{len(active_tool_arg_streams)}"
                                )
                            active_tool_arg_streams[stream_key] = _ToolArgStreamState(
                                tool_name=tool_name,
                                last_emit_at=time.monotonic(),
                            )
                            last_tool_arg_stream_key = stream_key
                            yield StreamEvent(
                                type=StreamEventType.THINKING,
                                data={
                                    "message": _tool_preparing_message(tool_name, english=english),
                                    "step": TOOL_PREPARING_STEP,
                                    "tool_name": tool_name,
                                },
                            )
                    elif raw_type == "response.function_call_arguments.delta":
                        arg_delta = _raw_event_delta(raw)
                        if arg_delta:
                            raw_item_id = _real_raw_item_id(getattr(raw, "item_id", ""))
                            output_index = getattr(raw, "output_index", None)
                            tracker = (
                                active_tool_arg_streams.get(raw_item_id)
                                if raw_item_id
                                else None
                            )
                            if tracker is None and output_index is not None:
                                tracker = active_tool_arg_streams.get(f"idx:{output_index}")
                            if (
                                tracker is None
                                and not raw_item_id
                                and output_index is None
                                and last_tool_arg_stream_key is not None
                            ):
                                tracker = active_tool_arg_streams.get(last_tool_arg_stream_key)
                            if tracker is not None:
                                tracker.total_bytes += len(arg_delta.encode("utf-8"))
                                tracker.total_chars += len(arg_delta)
                                bytes_since_emit = tracker.total_bytes - tracker.last_emit_bytes
                                now = time.monotonic()
                                tracks_progress = (
                                    tracker.tool_name in _TOOL_ARG_PROGRESS_LABELS_ZH
                                    or tracker.total_bytes >= TOOL_ARG_PROGRESS_BYTES
                                )
                                if tracks_progress and (
                                    bytes_since_emit >= TOOL_ARG_PROGRESS_BYTES
                                    or (
                                        bytes_since_emit > 0
                                        and now - tracker.last_emit_at >= TOOL_ARG_PROGRESS_INTERVAL_S
                                    )
                                ):
                                    tracker.last_emit_bytes = tracker.total_bytes
                                    tracker.last_emit_at = now
                                    yield StreamEvent(
                                        type=StreamEventType.THINKING,
                                        data={
                                            "message": _tool_arg_progress_message(
                                                tracker.tool_name,
                                                tracker.total_chars,
                                                english=english,
                                            ),
                                            "step": TOOL_PREPARING_STEP,
                                            "tool_name": tracker.tool_name,
                                        },
                                    )
                    elif raw_type in {"response.function_call_arguments.done", "response.output_item.done"}:
                        done_item = getattr(raw, "item", None)
                        done_keys: list[str] = []
                        for candidate in (
                            _real_raw_item_id(getattr(raw, "item_id", "")),
                            _real_raw_item_id(_raw_item_value(done_item, "id", "")),
                            _real_raw_item_id(_raw_item_value(done_item, "call_id", "")),
                        ):
                            if candidate and candidate not in done_keys:
                                done_keys.append(candidate)
                        output_index = getattr(raw, "output_index", None)
                        if output_index is not None:
                            done_keys.append(f"idx:{output_index}")
                        for key in done_keys:
                            if active_tool_arg_streams.pop(key, None) is not None and last_tool_arg_stream_key == key:
                                last_tool_arg_stream_key = None
                    continue

                if event_type != "run_item_stream_event":
                    continue

                event_name = getattr(sdk_event, "name", "")
                item = getattr(sdk_event, "item", None)
                if event_name == "tool_called":
                    tool_name, call_id, tool_input = _tool_call_payload(item)
                    if call_id:
                        tool_names_by_call_id[call_id] = tool_name
                    tool_calls_count += 1
                    yield StreamEvent(
                        type=StreamEventType.TOOL_USE,
                        data={
                            "id": call_id,
                            "name": tool_name,
                            "status": "complete",
                            "input": tool_input,
                        },
                    )
                elif event_name == "tool_output":
                    # 工具执行完毕后下一次 output_text 必然属于新一轮模型回复（兼容不发 response.created 的流/替身）。
                    call_text, call_text_length, repetition_checked_at, call_head_checked = [], 0, 0, False
                    active_tool_arg_streams.clear()
                    last_tool_arg_stream_key = None
                    call_id, result_text = _tool_output_payload(item)
                    tool_name = tool_names_by_call_id.get(call_id, "")
                    result_payload = mcp_text_result(result_text)

                    yield StreamEvent(
                        type=StreamEventType.TOOL_RESULT,
                        data={
                            "tool_use_id": call_id,
                            "name": tool_name,
                            "result": result_payload,
                        },
                    )

                    if failure_breaker.is_open and not failure_stopped:
                        # 熔断由 tool_use_behavior 在 SDK turn 内同步生效；这里同样只是
                        # 第二道保险，并让上面的护栏截断任何越界的新一轮。
                        failure_stopped = True
                        _safe_cancel(result, "after_turn")

                    if read_guard.is_open and not no_progress_stopped:
                        # 无进展停止同样由 tool_use_behavior 同步生效，这里是第二道保险。
                        no_progress_stopped = True
                        _safe_cancel(result, "after_turn")

                    if run_meter.exhausted and not budget_stopped:
                        # 预算同样由 tool_use_behavior 同步截停；「工具不存在」这类没有
                        # FunctionTool 结果的轮次不会经过它，这里兜底。
                        budget_stopped = True
                        run_meter.budget_stopped = True
                        _safe_cancel(result, "after_turn")

                    # 工具输出边界是 run 内唯一能消费 steering 的时机（SDK 事件
                    # 消费循环的其余位置都在等模型流式输出）。
                    async for steering_event in _consume_boundary_steering(
                        mid_run_steering, get_steering_messages
                    ):
                        yield steering_event
            else:
                sdk_stream_exhausted = True
        except MaxTurnsExceeded:
            # 轮数耗尽的对外事件放到熔断/无进展判定之后统一发出（见下方）。
            max_turns_exhausted = True
        except GeneratorExit:
            exiting_via_generator_exit = True
            _safe_cancel(result, "immediate")
            raise
        except BaseException:
            _safe_cancel(result, "immediate")
            raise
        finally:
            aclose = getattr(sdk_events, "aclose", None)
            if callable(aclose):
                if exiting_via_generator_exit:
                    _schedule_background_aclose(aclose)
                else:
                    await aclose()

        if repetition_stopped:
            # 与无进展停止同一个收尾：先让 usage 入账，再以 ERROR 结束整条工作流。StreamAdapter 收到
            # stop_reason=repetition_loop 时把正在写的正文截到重复开始之前再落库；stream_billing 按
            # 失控停止结算（本轮没有写入成功就退还），回复「继续」从这里接着做。
            for event in _emit_terminal_error(
                result,
                stop_reason=REPETITION_LOOP_STOP_REASON,
                error_data=repetition_loop_error().as_event_data(
                    reason=REPETITION_LOOP_STOP_REASON, agent_type=agent_type
                ),
            ):
                yield event
            return

        if leaked_tool_call_stopped:
            # 等待 <file> 正文时模型开头直接吐出泄漏 tool_call 协议头：不发 ERROR，先结算已完成调用的
            # usage 并发 MESSAGE_END（StreamAdapter 清空 WAITING_START 缓冲并保留待写守卫），
            # 交给单循环 agent 立即经 FILE_BODY_RESUME 纠偏重跑。
            usage = _usage_dict_from_result(result)
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={"stop_reason": LEAKED_TOOL_CALL_STOP_REASON, "usage": usage},
            )
            log_with_context(
                logger,
                20,  # INFO
                "OpenAI Agents streaming run stopped early on leaked tool_call protocol",
                agent_type=agent_type,
                model=(run_meter.last_served_model if run_meter is not None else None) or start_model,
                tool_calls=tool_calls_count,
                response_length=response_length,
                model_turns=len(getattr(result, "raw_responses", None) or []),
                stop_reason=LEAKED_TOOL_CALL_STOP_REASON,
                duration_ms=round((time.monotonic() - run_started_at) * 1000, 1),
                input_tokens=usage.get("input_tokens", 0),
                output_tokens=usage.get("output_tokens", 0),
                cache_read_tokens=usage.get("cache_read_tokens", 0),
                continued_previous_run=continuing,
            )
            completed_items = _sanitized_early_stop_items(result) or list(run_input)
            return

        failure_trip = failure_breaker.trip
        if failure_trip is not None:
            # 工具重复失败熔断：先发 MESSAGE_END 让本 run 已消耗的 usage 照常入账，
            # 再以 ERROR 结束——StreamAdapter 把 ERROR 当作致命错误：中断并关闭整个
            # 工作流生成器（不会再续跑），并以 error 帧作为 SSE
            # 终止帧；api/agent.py 据此按 internal_error 退还本次对话额度。
            log_with_context(
                logger,
                30,  # WARNING
                "Tool failure circuit breaker opened; stopping agent run",
                agent_type=agent_type,
                trip_reason=failure_trip.reason,
                tool_name=failure_trip.tool_name,
                failures=failure_trip.failures,
                total_failures=failure_breaker.total_failures,
                last_error=failure_trip.last_error,
            )
            for event in _emit_terminal_error(
                result,
                stop_reason=TOOL_FAILURE_STOP_REASON,
                error_data=failure_trip.as_event_data(agent_type),
            ):
                yield event
            return

        no_progress_trip = read_guard.trip
        if no_progress_trip is not None:
            # 重复读取无进展：与熔断同一个收尾——先让 usage 入账，再以 ERROR 结束整条
            # 工作流（不再交接/送审）。本请求没有任何写入成功时 refundable=True，
            # stream_billing 据 reason=no_progress 退还额度（billing_reason=runaway_no_progress）。
            log_with_context(
                logger,
                30,  # WARNING
                "Repeated reads without progress; stopping agent run",
                agent_type=agent_type,
                blocked_reads=read_guard.blocked_reads,
                duplicate_reads=read_guard.duplicate_reads,
                files_read=len(read_guard.files_read),
                files_written=len(read_guard.files_written),
                write_succeeded=read_guard.write_succeeded,
                model_turns=turn_filter.calls if turn_filter is not None else 0,
            )
            for event in _emit_terminal_error(
                result,
                stop_reason=NO_PROGRESS_STOP,
                error_data=no_progress_trip.as_event_data(agent_type),
            ):
                yield event
            return

        if run_meter.budget_stopped:
            # 请求级模型调用预算用尽：与熔断一样先让本 run 的 usage 入账，再以
            # ERROR 结束整条工作流（StreamAdapter 会关闭工作流生成器）。
            log_with_context(
                logger,
                30,  # WARNING
                "Model call budget exhausted; stopping agent run",
                agent_type=agent_type,
                model_calls=run_meter.model_calls,
                max_model_calls=run_meter.max_model_calls,
            )
            for event in _emit_terminal_error(
                result,
                stop_reason=MODEL_CALL_BUDGET_STOP_REASON,
                error_data=model_call_limit_error().as_event_data(
                    error_type=MODEL_CALL_LIMIT_ERROR_TYPE, agent_type=agent_type
                ),
            ):
                yield event
            return

        last_output_tokens = _last_response_output_tokens(result)
        sdk_model_capped = bool(
            getattr(getattr(sdk_agent, "model", None), "last_response_output_capped", False)
        )
        output_at_cap = sdk_model_capped or (
            AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS > 0
            and last_output_tokens >= AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS * OUTPUT_CAP_RATIO
        )
        stop_basis = (
            "response_incomplete_max_output_tokens"
            if sdk_model_capped
            else "usage_output_tokens_at_cap"
        )
        if (
            not max_turns_exhausted
            and output_at_cap
            and _last_response_is_thinking_only(result)
        ):
            # 最后一次响应只有思考（没有正文、没有工具调用），又撞上了输出上限，本 run
            # 也没有交接/澄清——这是被截断，不是正常结束。之前按 end_turn 收尾，最后这
            # 一步什么都没交付，工作流静默结束，作者只看到一段思考、没有任何结果或提示。
            # 先让 usage 入账，再以不可重试、可回复「继续」的 ERROR 结束；是否退还额度由 stream_billing 的
            # 既有规则决定（本轮无产出退还，已有产出照常计费）。只修状态，不限制思考。
            log_with_context(
                logger,
                30,  # WARNING
                "Agent run hit the output cap with thinking only; ending as truncated",
                agent_type=agent_type,
                output_tokens=last_output_tokens,
                max_output_tokens=AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS,
                thinking_chars=thinking_chars,
                stop_basis=stop_basis,
                model_turns=len(getattr(result, "raw_responses", None) or []),
            )
            truncation_count = state["previous_output_truncations"] + 1
            for event in _emit_terminal_error(
                result,
                stop_reason=OUTPUT_TRUNCATED_STOP_REASON,
                error_data=output_truncated_error(repeated=truncation_count > 1).as_event_data(
                    agent_type=agent_type, output_truncation_count=truncation_count
                ),
            ):
                yield event
            return

        model_turns = turn_filter.calls if turn_filter is not None else 0
        landed = turn_filter is not None and turn_filter.landing_injected
        # 真正撞上 max_turns，或已进入收尾提醒后结束（模型照提醒写了阶段总结）：都按
        # 「工具调用轮数用尽」收尾——发状态卡（前端给「继续」按钮并落库），不再续跑；
        # stop_reason 随 assistant 消息落库。
        turns_exhausted = max_turns_exhausted or landed
        # 收尾提醒是请求级模型调用预算触发的（预算比本次 run 的 max_turns 先到）：
        # 同样按耗尽收尾（状态卡 + 「继续」入口），但 stop_reason 记为预算用尽，
        # 卡片里的次数也换成请求级的计数，而不是硬停成 ERROR。
        budget_landed = (
            not max_turns_exhausted
            and landed
            and turn_filter is not None
            and turn_filter.landing_limit == LANDING_LIMIT_MODEL_CALL_BUDGET
        )
        time_landed = (
            not max_turns_exhausted
            and landed
            and turn_filter is not None
            and turn_filter.landing_limit == LANDING_LIMIT_WALL_CLOCK
        )
        exhausted_stop_reason = (
            MODEL_CALL_BUDGET_STOP_REASON
            if budget_landed
            else RUN_DEADLINE_STOP_REASON
            if time_landed
            else MAX_TURNS_STOP_REASON
        )
        defer_wall_clock = (
            time_landed
            and run_policy is not None
            and run_policy.defer_wall_clock_soft_landing
        )
        if turns_exhausted:
            # 三种收尾分开记：「tool-call turn cap」里多数是时限收尾，混在一起看不出真正撞上
            # max_turns 的有多少。时限收尾带已用时长和是否在为发布交接收尾。
            if time_landed:
                exhausted_log = "Agent run soft-landed near the wall-clock limit"
                limit_fields: dict[str, Any] = {
                    "elapsed_s": round(run_meter.elapsed_s(), 1),
                    "server_draining": is_draining(),
                }
            elif budget_landed:
                exhausted_log = "Agent run soft-landed at the request model-call budget"
                limit_fields = {}
            else:
                exhausted_log = "Agent run reached the tool-call turn cap"
                limit_fields = {}
            log_with_context(
                logger,
                30,  # WARNING
                exhausted_log,
                agent_type=agent_type,
                model_turns=model_turns,
                max_turns=AGENT_TOOL_CALL_MAX_ITERATIONS,
                hard_exhausted=max_turns_exhausted,
                soft_landed=bool(turn_filter is not None and turn_filter.landing_injected),
                landing_limit=turn_filter.landing_limit if turn_filter is not None else None,
                model_calls=run_meter.model_calls,
                max_model_calls=run_meter.max_model_calls,
                duplicate_reads=read_guard.duplicate_reads,
                blocked_reads=read_guard.blocked_reads,
                **limit_fields,
            )
            if time_landed:
                exhausted_data: dict[str, Any] = {
                    "layer": "tool_call",
                    "iterations_used": run_meter.model_calls,
                    "max_iterations": run_meter.max_model_calls,
                    "reason": (
                        "This round nearly ran out of time, so the AI wrapped up with a progress summary. "
                        "The task may not be finished; reply \"continue\" to pick it up."
                        if english
                        else "这一轮的时间快用完了，AI 先写下了阶段总结。"
                        "任务可能还没做完，回复「继续」就能接着做。"
                    ),
                }
            elif budget_landed:
                exhausted_data = {
                    "layer": "tool_call",
                    "iterations_used": run_meter.model_calls,
                    "max_iterations": run_meter.max_model_calls,
                    "reason": (
                        f"This round approached its model-call limit ({run_meter.max_model_calls}), "
                        "so the AI wrapped up with a progress summary. Reply \"continue\" to finish the rest."
                        if english
                        else f"这一轮的模型调用次数快到上限（{run_meter.max_model_calls} 次），"
                        "AI 先写下了阶段总结。任务可能还没做完，回复「继续」就能接着做。"
                    ),
                }
            else:
                exhausted_data = {
                    "layer": "tool_call",
                    "iterations_used": model_turns or AGENT_TOOL_CALL_MAX_ITERATIONS,
                    "max_iterations": AGENT_TOOL_CALL_MAX_ITERATIONS,
                    "reason": (
                        f"This round reached the tool-call turn limit ({AGENT_TOOL_CALL_MAX_ITERATIONS}). "
                        "The task may not be finished; reply \"continue\" to let the AI finish the rest."
                        if english
                        else f"这一轮的工具调用轮数已达上限（{AGENT_TOOL_CALL_MAX_ITERATIONS} 轮）。"
                        "当前任务可能未完全完成，您可以回复「继续」让 AI 接着完成剩余工作。"
                    ),
                }
            # 前端据此选卡片文案：时限收尾时模型可能已经做完，只是收尾提醒让它写了总结。
            exhausted_data["limit"] = (
                LANDING_LIMIT_WALL_CLOCK
                if time_landed
                else LANDING_LIMIT_MODEL_CALL_BUDGET
                if budget_landed
                else "max_turns"
            )
            if defer_wall_clock:
                assert run_policy is not None
                run_policy.deferred_wall_clock_exhausted = exhausted_data
            else:
                yield StreamEvent(type=StreamEventType.ITERATION_EXHAUSTED, data=exhausted_data)

        usage = _usage_dict_from_result(result)
        run_stop_reason = (
            "end_turn"
            if (not turns_exhausted or defer_wall_clock)
            else exhausted_stop_reason
        )
        message_end: dict[str, Any] = {"stop_reason": run_stop_reason, "usage": usage}
        if (
            (not turns_exhausted or time_landed)
            and output_at_cap
        ):
            # 最后一次响应写了正文或工具调用，同时撞上输出上限：run 照常结束（stop_reason 不变），
            # 但它的文字可能停在半句话上。loop 引擎据此区分「<file> 正文被输出上限截断」与「正文写完」。
            message_end[OUTPUT_CAPPED_KEY] = True
            log_with_context(
                logger,
                30,  # WARNING
                "Agent run hit the output cap after writing output",
                agent_type=agent_type,
                output_tokens=last_output_tokens,
                max_output_tokens=AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS,
                stop_basis=stop_basis,
            )
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data=message_end)

        log_with_context(
            logger,
            20,  # INFO
            "OpenAI Agents streaming run completed",
            agent_type=agent_type,
            model=(run_meter.last_served_model if run_meter is not None else None) or start_model,
            tool_calls=tool_calls_count,
            response_length=response_length,
            model_turns=len(getattr(result, "raw_responses", None) or []),
            stop_reason=run_stop_reason,
            duplicate_reads=read_guard.duplicate_reads,
            blocked_reads=read_guard.blocked_reads,
            blocked_edits=edit_guard.blocked_edits,
            guarded_self_reads=edit_guard.guarded_self_reads,
            duration_ms=round((time.monotonic() - run_started_at) * 1000, 1),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            continued_previous_run=continuing,
        )
        if not max_turns_exhausted:
            with contextlib.suppress(Exception):
                completed_items = list(result.to_input_list())
            if not completed_items:
                completed_items = list(run_input)

    except (asyncio.CancelledError, GeneratorExit):
        timing_outcome = "interrupted"
        if result is not None:
            _safe_cancel(result, "immediate")
        raise
    except Exception as exc:
        timing_outcome = "error"
        model_error = getattr(getattr(sdk_agent, "model", None), "unrecovered_provider_error", None)
        is_model_error = model_error is not None and (exc is model_error or exc.__cause__ is model_error)
        error_info = classify_stream_exception(exc)
        run_outcome = state.get("run_outcome")
        provider_attempted = bool(getattr(run_outcome, "provider_attempted", False))
        error_info = dataclasses.replace(
            error_info,
            refundable=is_model_error or not provider_attempted,
        )
        log_stream_exception(
            logger,
            "OpenAI Agents streaming run failed",
            exc,
            error_info,
            agent_type=agent_type,
            model=(run_meter.last_served_model if run_meter is not None else None) or start_model,
            duration_ms=round((time.monotonic() - run_started_at) * 1000, 1),
        )
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data=error_info.as_event_data(
                error_type=type(exc).__name__,
                **({"error_source": "model"} if is_model_error else {}),
            ),
        )
    finally:
        if (
            result is not None
            and not sdk_stream_exhausted
            and not getattr(result, "is_complete", False)
        ):
            _safe_cancel(result, "immediate")
        if run_hooks is not None:
            # A call lacking on_llm_end is unfinished, even if the consumer ended normally.
            run_hooks.finish_timing("error" if timing_outcome == "error" else "interrupted")
        run_meter.record_agent_run(
            (time.monotonic() - run_started_at) * 1000,
            agent_type=agent_type, outcome=timing_outcome,
        )
        if completed_items:
            state["run_items"] = completed_items
        else:
            state.pop("run_items", None)
        if mid_run_steering:
            # 工具边界消费的 steering 记入待消费列表，供同轮下一次 run 注入，
            # 同时告知 service 本轮存在未消费插话（跳过 wire_prefix_cache）。
            append_pending_steering(state, mid_run_steering)
