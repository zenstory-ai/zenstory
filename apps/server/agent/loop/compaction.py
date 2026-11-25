"""单循环 agent 的历史压缩（轮与轮之间，从不在一次工具循环中途）。

上一轮最后一次模型调用的实测输入 token（``message_metadata.prompt_tokens``）超过
``AGENT_LOOP_COMPACT_AT_TOKENS`` 时，本轮开始前用同一个模型（会话固定的供应商，保持默认思考强度）
写一份交接摘要：校验非空、没有被输出上限截断且未混入工具调用标签，失败时附带精炼重试说明再试一次，
仍失败就不压缩、照常跑这一轮。摘要随本轮的 assistant 行写进 ``message_metadata.compaction``（不改表结构），
下一轮起 ``history.load_loop_history`` 据此重建历史。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent.core.usage import extract_response_usage, merge_usage_dicts
from config.agent_runtime import (
    AGENT_LOOP_COMPACT_AT_TOKENS,
    AGENT_LOOP_COMPACT_INPUT_MAX_TOKENS,
    AGENT_LOOP_COMPACT_KEEP_USER_TOKENS,
    AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS,
    AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS,
)
from config.datetime_utils import utcnow
from utils.logger import get_logger, log_with_context

from .history import (
    LoopHistory,
    bound_history_for_provider,
    contains_leaked_tool_protocol,
    effective_history_provider_tokens,
    kept_author_messages,
    summary_message,
)
from .prompts import COMPACT_PROMPT

if TYPE_CHECKING:
    from agent.core.request_context import AgentRequestContext
    from agent.core.run_meter import AgentRunMeter

logger = get_logger(__name__)

COMPACTION_CALL_SITE = "agent_loop_compaction"
# 始终保持模型默认思考强度（不降低 reasoning_effort），最多尝试两次。
_MAX_COMPACTION_ATTEMPTS = 2
_COMPACT_RETRY_NOTE = (
    "\n\n[重试提醒] 上一次摘要输出为空、超出单次长度被截断或混入了工具调用协议标签。"
    "本次请不在思考中逐章复述原文或预写全文，只抓关键骨架，直接输出完整纯文本交接摘要，"
    "严禁调用工具或输出任何 <tool_call>、<invoke>、<parameter> 标签。"
)
SKIPPED_EMPTY_OR_TRUNCATED = "empty_or_truncated_summary"
SKIPPED_PROVIDER_ERROR = "provider_error"


@dataclass
class CompactionOutcome:
    """压缩结果：``record`` 写进本轮 assistant 行，``items`` 是压缩后的历史。"""

    record: dict[str, Any] | None = None
    items: list[dict[str, Any]] | None = None
    usage: dict[str, int] | None = None
    skipped: str | None = None


@dataclass
class PostTurnCompactionResult:
    """轮后后台压缩任务的完成结果（用于下一轮到达时直接复用在途任务）。"""

    outcome: CompactionOutcome
    persisted: bool = False


@dataclass
class InFlightCompaction:
    """进程内同一会话正在运行的轮后后台压缩句柄。"""

    session_id: str
    covers_through_message_id: str
    task: asyncio.Task[PostTurnCompactionResult]


# 按 session_id 追踪正在运行的轮后异步压缩任务，防止同一会话并发触发多次压缩。
_INFLIGHT_COMPACTIONS: dict[str, InFlightCompaction] = {}


def get_inflight_compaction(session_id: str | None) -> InFlightCompaction | None:
    """返回指定会话仍在运行中的轮后后台压缩任务，没有或已结束则返回 None。"""
    if not session_id:
        return None
    entry = _INFLIGHT_COMPACTIONS.get(session_id)
    if entry is None:
        return None
    if entry.task.done():
        _INFLIGHT_COMPACTIONS.pop(session_id, None)
        return None
    return entry


def register_inflight_compaction(
    session_id: str,
    covers_through_message_id: str,
    task: asyncio.Task[PostTurnCompactionResult],
) -> InFlightCompaction | None:
    """登记会话级在途后台压缩；若该会话已有未完成的压缩任务则拒绝登记并返回 None。"""
    if get_inflight_compaction(session_id) is not None:
        return None
    entry = InFlightCompaction(
        session_id=session_id,
        covers_through_message_id=covers_through_message_id,
        task=task,
    )
    _INFLIGHT_COMPACTIONS[session_id] = entry

    def _cleanup(_done: asyncio.Task[Any]) -> None:
        if _INFLIGHT_COMPACTIONS.get(session_id) is entry:
            _INFLIGHT_COMPACTIONS.pop(session_id, None)

    task.add_done_callback(_cleanup)
    return entry


def reset_inflight_compactions() -> None:
    """清空进程内后台压缩追踪表（仅供测试隔离使用）。"""
    _INFLIGHT_COMPACTIONS.clear()


def should_compact_post_turn(
    *,
    last_prompt_tokens: int,
    threshold: int | None = None,
    retry_growth: int | None = None,
    skipped_at_tokens: int = 0,
    skipped_reason: str | None = None,
    reasoning_tokens: int = 0,
    reasoning_threshold: int | None = None,
) -> bool:
    """判断刚结束的轮次是否应在 ``done`` 发出后于后台预压缩历史。

    满足以下任一条件且未处于模型输出不可用的退避区间内时返回 True（瞬时 ``provider_error`` 不触发退避）：
    1. 本轮实测输入 token（含峰值）达到 ``AGENT_LOOP_COMPACT_AT_TOKENS``（``>=``）；
    2. 本轮 ``completed_wire_items`` 中累计 ``reasoning`` 项估算 token 达到
       ``AGENT_LOOP_COMPACT_REASONING_TOKENS``（即使总 prompt_tokens 尚未达到 240k，
       也主动在后台压掉死思考，避免在 ``wire_prefix_cache`` 中缓存 20 分钟）。
    """
    from config import agent_runtime

    limit = agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS if threshold is None else threshold
    r_limit = (
        agent_runtime.AGENT_LOOP_COMPACT_REASONING_TOKENS
        if reasoning_threshold is None
        else reasoning_threshold
    )
    prompt_exceeded = last_prompt_tokens > 0 and last_prompt_tokens >= limit
    reasoning_exceeded = r_limit > 0 and reasoning_tokens >= r_limit
    if not prompt_exceeded and not reasoning_exceeded:
        return False
    if skipped_at_tokens > 0 and skipped_reason != SKIPPED_PROVIDER_ERROR:
        growth = agent_runtime.AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS if retry_growth is None else retry_growth
        retry_at = min(skipped_at_tokens + growth, _MAX_SKIPPED_RETRY_CEILING_TOKENS)
        return last_prompt_tokens >= retry_at
    return True


# 退避目标的硬封顶：防止存量老会话在 ~96 万 token 处记下 skipped 后要求涨到 >100 万（物理不可能）才重试。
_MAX_SKIPPED_RETRY_CEILING_TOKENS = 950_000
_REJECTED_FINISH_REASONS = frozenset({"length", "tool_calls", "function_call", "content_filter"})


def _extract_valid_summary(choice: Any) -> tuple[str, bool]:
    """提取合法的纯文本交接摘要（有界机器协议结构校验，不判断自然语言语义）。

    压缩请求保留与主循环一致的工具清单和默认 ``tool_choice`` 以复用前缀缓存，依靠提示词约束纯文本输出；
    若模型仍返回结构化 ``tool_calls`` 或将内部 ``]<]minimax[>[<tool_call>…`` / ``<invoke>``
    协议标签吐进正文并中断生成，此类响应不是完整摘要，返回 ``("", True)`` 触发带提示重试或安全跳过压缩。
    代码块与行内反引号内引用的字面标签原样保留。
    """
    if choice is None:
        return "", False
    finish_reason = getattr(choice, "finish_reason", None)
    message = getattr(choice, "message", None)
    has_structured_tool_call = finish_reason in {"tool_calls", "function_call"} or bool(
        getattr(message, "tool_calls", None) or getattr(message, "function_call", None)
    )
    if finish_reason in _REJECTED_FINISH_REASONS or has_structured_tool_call:
        return "", has_structured_tool_call
    raw = str(getattr(message, "content", "") or "").strip()
    if not raw:
        return "", False
    if contains_leaked_tool_protocol(raw):
        return "", True
    return raw, False


def _extract_valid_response_summary(response: Any) -> tuple[str, bool, str, str | None]:
    """从 Responses API ``Response`` 提取合法的纯文本交接摘要，返回 ``(summary, leaked_tool, raw_text, status)``。"""
    if response is None:
        return "", False, "", None
    status = getattr(response, "status", None)
    output_items = getattr(response, "output", None) or []
    parts: list[str] = []
    has_structured_tool_call = False
    for item in output_items:
        item_type = item.get("type") if isinstance(item, dict) else getattr(item, "type", None)
        if item_type in {"function_call", "tool_call"}:
            has_structured_tool_call = True
            continue
        if item_type != "message":
            continue
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        for block in content or []:
            block_type = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            if block_type == "output_text":
                text = block.get("text") if isinstance(block, dict) else getattr(block, "text", "")
                if isinstance(text, str):
                    parts.append(text)
    raw = "".join(parts).strip()
    if has_structured_tool_call:
        return "", True, raw, status
    if status in {"incomplete", "failed", "cancelled"}:
        return "", False, raw, status
    if not raw:
        return "", False, "", status
    if contains_leaked_tool_protocol(raw):
        return "", True, raw, status
    return raw, False, raw, status


def _is_request_rejected_error(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in {400, 413, 422}


def should_compact(
    history: LoopHistory,
    threshold: int | None = None,
    retry_growth: int | None = None,
    *,
    extra_tokens: int = 0,
) -> bool:
    """按历史实测/同量纲估算与本轮新增输入（``<turn_context>`` + 用户消息）之和判断。

    仅在上次因模型输出不可用（如 ``empty_or_truncated_summary``）没压缩成时退避：
    输入比那次至少再长 ``retry_growth`` 才再试（封顶 ``_MAX_SKIPPED_RETRY_CEILING_TOKENS``，
    避免逼近 1M 窗口的老会话死锁），不让每一轮都多付两次整段历史的摘要调用。
    瞬时 ``provider_error`` 不触发退避，下一轮立刻重试压缩，避免把长会话一路拖到 950k。
    """
    if not history.items or not history.last_message_id:
        return False
    limit = AGENT_LOOP_COMPACT_AT_TOKENS if threshold is None else threshold
    measured = effective_history_provider_tokens(history) + max(0, extra_tokens)
    if measured <= limit:
        return False
    if (
        history.compaction_skipped_at_tokens > 0
        and history.compaction_skipped_reason != SKIPPED_PROVIDER_ERROR
    ):
        growth = AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS if retry_growth is None else retry_growth
        retry_at = min(history.compaction_skipped_at_tokens + growth, _MAX_SKIPPED_RETRY_CEILING_TOKENS)
        return measured >= retry_at
    return True


def skipped_record(history: LoopHistory, reason: str) -> dict[str, Any]:
    """没压缩成时随本轮落库的记录（``message_metadata.compaction_skipped``）。

    记录跳过时历史本身的实测/同量纲输入 token：模型输出不可用时下一轮据此退避，
    瞬时 ``provider_error`` 仍保留审计记录但不触发退避。
    """
    measured = effective_history_provider_tokens(history)
    return {"reason": reason, "at_prompt_tokens": int(measured), "created_at": utcnow().isoformat()}



def _chat_tools() -> list[dict[str, Any]]:
    """压缩请求带上与 SDK 完全一致的工具清单与默认 tool_choice：历史里的工具调用合法，且保持前缀缓存一致。"""
    from agents.models.chatcmpl_converter import Converter

    from agent.openai_agents.tools_adapter import build_agent_function_tools

    return [dict(Converter.tool_to_openai(tool)) for tool in build_agent_function_tools()]


def _responses_tools() -> list[dict[str, Any]]:
    """Responses API 压缩请求的工具清单：与 ``OpenAIResponsesModel`` 序列化结果逐字节一致以复用前缀缓存。"""
    from agents.models.openai_responses import (
        Converter as ResponsesConverter,
    )
    from agents.models.openai_responses import (
        _materialize_responses_tool_params,
    )

    from agent.openai_agents.tools_adapter import build_agent_function_tools

    converted = ResponsesConverter.convert_tools(build_agent_function_tools(), [])
    return [dict(tool) for tool in _materialize_responses_tool_params(converted.tools)]


def _responses_input(
    items: list[dict[str, Any]],
    *,
    retry_prompt: bool = False,
) -> list[dict[str, Any]]:
    """构造 Responses API 压缩输入（清理 fake id / provider_data，末尾追加压缩指令）。"""
    from agents.items import ItemHelpers
    from agents.models.fake_id import FAKE_RESPONSES_ID
    from agents.util._json import _to_dump_compatible

    prompt = f"{COMPACT_PROMPT}{_COMPACT_RETRY_NOTE}" if retry_prompt else COMPACT_PROMPT
    raw_list = _to_dump_compatible(ItemHelpers.input_to_new_input_list(items))  # type: ignore[arg-type]
    cleaned: list[dict[str, Any]] = []
    for item in raw_list:
        if not isinstance(item, dict):
            cleaned.append(item)
            continue
        if item.get("type") == "reasoning" and item.get("provider_data"):
            continue
        if item.get("id") == FAKE_RESPONSES_ID or "provider_data" in item:
            item = {
                k: v
                for k, v in item.items()
                if k != "provider_data" and not (k == "id" and v == FAKE_RESPONSES_ID)
            }
        cleaned.append(item)
    cleaned.append({"role": "user", "content": prompt})
    return cleaned


def _request_messages(
    system_prompt: str,
    items: list[dict[str, Any]],
    *,
    retry_prompt: bool = False,
    model: str | None = None,
) -> list[dict[str, Any]]:
    from agents.models.chatcmpl_converter import Converter

    from agent.core.llm_providers import primary_model_name
    from agent.openai_agents.model import (
        _replay_reasoning_content,
        bridge_responses_reasoning_for_chat_completions,
    )

    prompt = f"{COMPACT_PROMPT}{_COMPACT_RETRY_NOTE}" if retry_prompt else COMPACT_PROMPT
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    # Converter.items_to_messages only invokes should_replay_reasoning_content when ``model`` is non-None.
    effective_model = model or primary_model_name() or "agent"
    bridged_items = bridge_responses_reasoning_for_chat_completions(items)
    messages.extend(
        Converter.items_to_messages(
            bridged_items,  # type: ignore[arg-type]
            model=effective_model,
            should_replay_reasoning_content=_replay_reasoning_content,
        )
    )
    messages.append({"role": "user", "content": prompt})
    return messages


def compacted_items(author_messages: list[str], summary: str, keep_user_tokens: int) -> list[dict[str, Any]]:
    """压缩后的历史：最近的作者原话（预算内原文）+ 交接摘要。与 history 下一轮从库里重建的形状相同。"""
    return [*kept_author_messages(author_messages, keep_user_tokens), summary_message(summary)]


async def compact_history(
    history: LoopHistory,
    *,
    system_prompt: str,
    session_id: str | None,
    keep_user_tokens: int | None = None,
    run_meter: AgentRunMeter | None = None,
    request_context: AgentRequestContext | None = None,
) -> CompactionOutcome:
    """写交接摘要并给出压缩后的历史；摘要不可用时返回 skipped，历史保持原样。

    每次摘要调用都记进 ``run_meter``（请求级模型调用预算）。次数由 ``_MAX_COMPACTION_ATTEMPTS`` 限定、
    发生在这一轮开始前，所以这里不看预算是否用完。
    """
    from agent.core import llm_failover
    from agent.openai_agents.intra_run_trimmer import IntraRunToolOutputTrimmer
    from agent.openai_agents.usage_hooks import agent_usage_attribution
    from core.error_handler import APIException
    from services.usage.cost_budget import budget_attribution

    attribution = agent_usage_attribution(request_context)
    input_budget = AGENT_LOOP_COMPACT_INPUT_MAX_TOKENS

    def _trim_for_budget(budget_tokens: int) -> list[dict[str, Any]]:
        # 按 1.5 倍供应商 token 量纲折算字符上限，折叠超预算旧工具载荷以尽量保住全部轮次的起止脉络与文件 id 摘要。
        trimmer_max_chars = max(200_000, int(budget_tokens * 1.5))
        return IntraRunToolOutputTrimmer(max_input_chars=trimmer_max_chars).trim_items(
            history.items, instructions_chars=len(system_prompt)
        )

    target_model: str | None = None
    use_responses = False
    try:
        ordered_providers = await llm_failover.aordered_providers(session_id)
        if ordered_providers:
            target_model = ordered_providers[0].model
            use_responses = bool(getattr(ordered_providers[0], "uses_responses_api", False))
    except Exception:  # noqa: BLE001 - fall back to primary_model_name() in _request_messages
        target_model = None
        use_responses = False

    trimmed_items = _trim_for_budget(input_budget)
    usage: dict[str, int] = {}
    summary = ""
    retry_after_invalid_output = False
    started = time.monotonic()
    for attempt_index in range(_MAX_COMPACTION_ATTEMPTS):
        bounded = bound_history_for_provider(history, trimmed_items, input_budget)
        if not bounded:
            log_with_context(
                logger,
                30,  # WARNING
                "Loop history compaction slice empty after bounding; skipping compaction",
                input_budget=input_budget,
            )
            break
        if run_meter is not None:
            run_meter.record_model_call()
        try:
            with budget_attribution(attribution):
                if use_responses:
                    resp_input = _responses_input(bounded, retry_prompt=retry_after_invalid_output)

                    def _build_chat_fallback(
                        fallback_provider: Any,
                        _bounded: list[dict[str, Any]] = bounded,
                        _retry: bool = retry_after_invalid_output,
                    ) -> dict[str, Any]:
                        return {
                            "messages": _request_messages(
                                system_prompt,
                                _bounded,
                                retry_prompt=_retry,
                                model=getattr(fallback_provider, "model", None) or target_model,
                            ),
                            "tools": _chat_tools(),
                            "max_tokens": AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS,
                            "temperature": 1.0,
                            "top_p": 0.95,
                        }

                    response, _provider = await llm_failover.acreate_response(
                        call_site=COMPACTION_CALL_SITE,
                        session_id=session_id,
                        reasoning_effort=None,
                        usage_attribution=attribution,
                        max_retries=0,
                        chat_kwargs_builder=_build_chat_fallback,
                        stream_collect=True,
                        instructions=system_prompt,
                        input=resp_input,
                        tools=_responses_tools(),
                        max_output_tokens=AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS,
                        temperature=1.0,
                        top_p=0.95,
                    )
                else:
                    messages = _request_messages(
                        system_prompt,
                        bounded,
                        retry_prompt=retry_after_invalid_output,
                        model=target_model,
                    )
                    response, _provider = await llm_failover.acreate_chat_completion(
                        call_site=COMPACTION_CALL_SITE,
                        session_id=session_id,
                        reasoning_effort=None,
                        usage_attribution=attribution,
                        max_retries=0,
                        messages=messages,
                        tools=_chat_tools(),
                        max_tokens=AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS,
                        temperature=1.0,
                        top_p=0.95,
                    )
        except APIException:
            # 平台自身的配额/成本限制（如免费用户 ¥15 成本兜底 402）必须原样上抛，不降级成退避也不重撞主循环。
            raise
        except Exception as exc:  # noqa: BLE001 - 压缩失败不影响这一轮照常运行
            has_next_attempt = attempt_index + 1 < _MAX_COMPACTION_ATTEMPTS
            if has_next_attempt and len(bounded) > 1 and _is_request_rejected_error(exc):
                current_tokens = effective_history_provider_tokens(history, bounded)
                input_budget = max(100_000, min(input_budget, current_tokens) // 2)
                trimmed_items = _trim_for_budget(input_budget)
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Loop history compaction rejected (400/413/422); retrying with halved input budget",
                    error_type=type(exc).__name__,
                    next_input_budget=input_budget,
                )
                continue
            log_with_context(
                logger,
                30,  # WARNING
                "Loop history compaction call failed; continuing without compaction",
                error_type=type(exc).__name__,
                attempt=attempt_index + 1,
            )
            return CompactionOutcome(usage=usage or None, skipped=SKIPPED_PROVIDER_ERROR)
        usage = merge_usage_dicts(usage, extract_response_usage(getattr(response, "usage", None))) or {}
        choices = getattr(response, "choices", None)
        if choices is not None:
            choice = choices[0] if choices else None
            raw_text = str(getattr(getattr(choice, "message", None), "content", "") or "").strip()
            finish_reason = getattr(choice, "finish_reason", None)
            candidate, leaked_tool_protocol = _extract_valid_summary(choice)
        else:
            candidate, leaked_tool_protocol, raw_text, finish_reason = _extract_valid_response_summary(response)
        if candidate:
            summary = candidate
            break
        retry_after_invalid_output = True
        log_with_context(
            logger,
            30,  # WARNING
            "Loop history compaction summary empty, truncated, or contained tool protocol",
            attempt=attempt_index + 1,
            finish_reason=finish_reason,
            summary_chars=len(raw_text),
            leaked_tool_protocol=leaked_tool_protocol,
        )
    duration_ms = round((time.monotonic() - started) * 1000, 1)
    if not summary:
        return CompactionOutcome(usage=usage or None, skipped=SKIPPED_EMPTY_OR_TRUNCATED)

    items = compacted_items(
        history.author_messages,
        summary,
        AGENT_LOOP_COMPACT_KEEP_USER_TOKENS if keep_user_tokens is None else keep_user_tokens,
    )
    record = {
        "summary": summary,
        "covers_through_message_id": history.last_message_id,
        # 这次实际发出的作者原话（含截断后缀）：之后的轮次原样回放，前缀逐字不变（不按行重新截断）。
        "kept_author_messages": [str(item["content"]) for item in items[:-1]],
        "created_at": utcnow().isoformat(),
    }
    log_with_context(
        logger,
        20,  # INFO
        "Loop history compacted",
        summary_chars=len(summary),
        kept_user_messages=len(items) - 1,
        history_items=len(history.items),
        measured_prompt_tokens=history.last_prompt_tokens,
        duration_ms=duration_ms,
    )
    return CompactionOutcome(record=record, items=items, usage=usage or None)


def persist_post_turn_compaction_sync(
    *,
    session_id: str,
    project_id: str,
    user_id: str,
    assistant_message_id: str,
    record: dict[str, Any] | None = None,
    skipped: dict[str, Any] | None = None,
    session_factory: Any = None,
) -> bool:
    """将轮后异步压缩结果写回刚结束的 assistant 行，并归档压缩前查询载荷、失效旧 wire prefix。

    幂等保护：目标行已存在有效 ``compaction`` 时不覆盖；仅当目标行仍是会话最新行时才
    失效 Redis wire-prefix 缓存，避免误删后续新轮次写入的前缀缓存。
    """
    from sqlalchemy import desc
    from sqlmodel import select

    from agent.core.chat_storage import archive_pre_compaction_tool_calls_safe
    from agent.loop.history import row_metadata, valid_compaction
    from agent.loop.wire_prefix_cache import invalidate_wire_prefix
    from database import create_session
    from models import ChatMessage, ChatSession

    if not (session_id and project_id and user_id and assistant_message_id):
        return False
    if record is None and skipped is None:
        return False

    make_session = session_factory or create_session
    with make_session() as db:
        chat_session = db.get(ChatSession, session_id)
        if (
            chat_session is None
            or chat_session.user_id != user_id
            or chat_session.project_id != project_id
        ):
            return False
        row = db.get(ChatMessage, assistant_message_id)
        if row is None or row.session_id != session_id or row.role != "assistant":
            return False

        meta = row_metadata(row)
        if valid_compaction(meta) is not None:
            return False

        if record is not None:
            meta["compaction"] = record
            meta.pop("compaction_skipped", None)
            display_events = meta.get("display_events")
            if isinstance(display_events, list):
                if (
                    not display_events
                    or not isinstance(display_events[-1], dict)
                    or display_events[-1].get("type") != "history_compacted"
                ):
                    display_events.append({"type": "history_compacted", "data": {}})
            else:
                meta["display_events"] = [{"type": "history_compacted", "data": {}}]
        elif skipped is not None:
            if isinstance(meta.get("compaction_skipped"), dict):
                return False
            meta["compaction_skipped"] = skipped

        row.message_metadata = json.dumps(meta, ensure_ascii=False)
        db.add(row)
        db.commit()

        if record is not None:
            covers_id = str(record.get("covers_through_message_id") or assistant_message_id)
            archive_pre_compaction_tool_calls_safe(
                db,
                chat_session_id=session_id,
                covers_through_message_id=covers_id,
            )
            latest_id = db.exec(
                select(ChatMessage.id)
                .where(
                    ChatMessage.session_id == session_id,
                    ChatMessage.role.in_(("user", "assistant")),
                )
                .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
                .limit(1)
            ).first()
            if latest_id == assistant_message_id:
                invalidate_wire_prefix(session_id)
            return True
        return False
