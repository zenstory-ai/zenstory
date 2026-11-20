"""单循环 agent 的历史压缩（轮与轮之间，从不在一次工具循环中途）。

上一轮最后一次模型调用的实测输入 token（``message_metadata.prompt_tokens``）超过
``AGENT_LOOP_COMPACT_AT_TOKENS`` 时，本轮开始前用同一个模型（会话固定的供应商，保持默认思考强度）
写一份交接摘要：校验非空、没有被输出上限截断且未混入工具调用标签，失败时附带精炼重试说明再试一次，
仍失败就不压缩、照常跑这一轮。摘要随本轮的 assistant 行写进 ``message_metadata.compaction``（不改表结构），
下一轮起 ``history.load_loop_history`` 据此重建历史。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

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


# 退避目标的硬封顶：防止存量老会话在 ~96 万 token 处记下 skipped 后要求涨到 >100 万（物理不可能）才重试。
_MAX_SKIPPED_RETRY_CEILING_TOKENS = 950_000
_REJECTED_FINISH_REASONS = frozenset({"length", "tool_calls", "function_call", "content_filter"})


def _extract_valid_summary(choice: Any) -> tuple[str, bool]:
    """提取合法的纯文本交接摘要（有界机器协议结构校验，不判断自然语言语义）。

    供应商偶发无视 ``tool_choice="none"`` 输出结构化 ``tool_calls`` 或将内部
    ``]<]minimax[>[<tool_call>…`` / ``<invoke>`` 协议标签直接吐进正文并中断生成；
    此类响应不是完整摘要，返回 ``("", True)`` 触发带提示重试或安全跳过压缩。
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


def _is_request_rejected_error(exc: BaseException) -> bool:
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(exc, "response", None), "status_code", None)
    return status in {400, 413, 422}


def should_compact(
    history: LoopHistory,
    threshold: int | None = None,
    retry_growth: int | None = None,
) -> bool:
    """按实测输入与未折叠历史同量纲估算的较大值判断。

    上次没压缩成（摘要不可用）时退避：输入比那次至少再长 ``retry_growth`` 才再试
    （封顶 ``_MAX_SKIPPED_RETRY_CEILING_TOKENS``，避免逼近 1M 窗口的老会话死锁），
    不让每一轮都多付两次整段历史的摘要调用。
    """
    if not history.items or not history.last_message_id:
        return False
    limit = AGENT_LOOP_COMPACT_AT_TOKENS if threshold is None else threshold
    measured = effective_history_provider_tokens(history)
    if measured <= limit:
        return False
    if history.compaction_skipped_at_tokens > 0:
        growth = AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS if retry_growth is None else retry_growth
        retry_at = min(history.compaction_skipped_at_tokens + growth, _MAX_SKIPPED_RETRY_CEILING_TOKENS)
        return measured >= retry_at
    return True


def skipped_record(history: LoopHistory, reason: str) -> dict[str, Any]:
    """没压缩成时随本轮落库的记录（``message_metadata.compaction_skipped``），下一轮据此退避。"""
    measured = effective_history_provider_tokens(history)
    return {"reason": reason, "at_prompt_tokens": int(measured), "created_at": utcnow().isoformat()}



def _chat_tools() -> list[dict[str, Any]]:
    """压缩请求带上与 SDK 完全一致的工具清单（tool_choice=none）：历史里的工具调用合法，且不打断前缀缓存。"""
    from agents.models.chatcmpl_converter import Converter

    from agent.openai_agents.tools_adapter import build_agent_function_tools

    return [dict(Converter.tool_to_openai(tool)) for tool in build_agent_function_tools()]


def _request_messages(system_prompt: str, items: list[dict[str, Any]], *, retry_prompt: bool = False) -> list[dict[str, Any]]:
    from agents.models.chatcmpl_converter import Converter

    prompt = f"{COMPACT_PROMPT}{_COMPACT_RETRY_NOTE}" if retry_prompt else COMPACT_PROMPT
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(Converter.items_to_messages(items))  # type: ignore[arg-type]
    messages.append({"role": "user", "content": prompt})
    return messages


def compacted_items(author_messages: list[str], summary: str, keep_user_tokens: int) -> list[dict[str, Any]]:
    """压缩后的历史：最近的作者原话（预算内原文）+ 交接摘要。与 history 下一轮从库里重建的形状相同。"""
    return [*kept_author_messages(author_messages, keep_user_tokens), summary_message(summary)]


def _usage_of(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    prompt = int(getattr(usage, "prompt_tokens", 0) or 0)
    cached = int(getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", 0) or 0)
    cached = max(0, min(cached, prompt))
    totals = {
        "input_tokens": prompt - cached,
        "cache_read_tokens": cached,
        "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }
    return {key: value for key, value in totals.items() if value}


def _merge_usage(total: dict[str, int], usage: dict[str, int]) -> dict[str, int]:
    for key, value in usage.items():
        total[key] = total.get(key, 0) + value
    return total


async def compact_history(
    history: LoopHistory,
    *,
    system_prompt: str,
    session_id: str | None,
    keep_user_tokens: int | None = None,
    run_meter: AgentRunMeter | None = None,
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

    attribution = agent_usage_attribution()
    input_budget = AGENT_LOOP_COMPACT_INPUT_MAX_TOKENS

    def _trim_for_budget(budget_tokens: int) -> list[dict[str, Any]]:
        # 按 1.5 倍供应商 token 量纲折算字符上限，折叠超预算旧工具载荷以尽量保住全部轮次的起止脉络与文件 id 摘要。
        trimmer_max_chars = max(200_000, int(budget_tokens * 1.5))
        return IntraRunToolOutputTrimmer(max_input_chars=trimmer_max_chars).trim_items(
            history.items, instructions_chars=len(system_prompt)
        )

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
        messages = _request_messages(system_prompt, bounded, retry_prompt=retry_after_invalid_output)
        if run_meter is not None:
            run_meter.record_model_call()
        try:
            with budget_attribution(attribution):
                response, _provider = await llm_failover.acreate_chat_completion(
                    call_site=COMPACTION_CALL_SITE,
                    session_id=session_id,
                    reasoning_effort=None,
                    usage_attribution=attribution,
                    max_retries=0,
                    messages=messages,
                    tools=_chat_tools(),
                    tool_choice="none",
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
        _merge_usage(usage, _usage_of(response))
        choice = response.choices[0] if getattr(response, "choices", None) else None
        raw_text = str(getattr(getattr(choice, "message", None), "content", "") or "").strip()
        finish_reason = getattr(choice, "finish_reason", None)
        candidate, leaked_tool_protocol = _extract_valid_summary(choice)
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
