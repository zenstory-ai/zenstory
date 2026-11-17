"""单循环 agent 的历史压缩（轮与轮之间，从不在一次工具循环中途）。

上一轮最后一次模型调用的实测输入 token（``message_metadata.prompt_tokens``）超过
``AGENT_LOOP_COMPACT_AT_TOKENS`` 时，本轮开始前用同一个模型（会话固定的供应商）写一份交接摘要：
校验非空且没有被输出上限截断，失败时降低思考强度重试一次，仍失败就不压缩、照常跑这一轮。
摘要随本轮的 assistant 行写进 ``message_metadata.compaction``（不改表结构），下一轮起
``history.load_loop_history`` 据此重建历史。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from config.agent_runtime import (
    AGENT_LOOP_COMPACT_AT_TOKENS,
    AGENT_LOOP_COMPACT_INPUT_MAX_TOKENS,
    AGENT_LOOP_COMPACT_KEEP_USER_TOKENS,
    AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS,
    AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS,
)
from config.datetime_utils import utcnow
from utils.logger import get_logger, log_with_context

from .history import LoopHistory, bounded_items, kept_author_messages, summary_message
from .prompts import COMPACT_PROMPT

logger = get_logger(__name__)

COMPACTION_CALL_SITE = "agent_loop_compaction"
# 第一次按模型默认思考强度；摘要为空或被截断时降一档再试一次。
_ATTEMPT_EFFORTS: tuple[str | None, ...] = (None, "low")
SKIPPED_EMPTY_OR_TRUNCATED = "empty_or_truncated_summary"
SKIPPED_PROVIDER_ERROR = "provider_error"


@dataclass
class CompactionOutcome:
    """压缩结果：``record`` 写进本轮 assistant 行，``items`` 是压缩后的历史。"""

    record: dict[str, Any] | None = None
    items: list[dict[str, Any]] | None = None
    usage: dict[str, int] | None = None
    skipped: str | None = None


def should_compact(
    history: LoopHistory,
    threshold: int | None = None,
    retry_growth: int | None = None,
) -> bool:
    """按实测输入 token 判断；没有实测值的旧会话（多 agent 图写下的行）用历史的估算兜底。

    上次没压缩成（摘要不可用）时退避：输入比那次至少再长 ``retry_growth`` 才再试，
    不让每一轮都多付两次整段历史的摘要调用。
    """
    if not history.items or not history.last_message_id:
        return False
    limit = AGENT_LOOP_COMPACT_AT_TOKENS if threshold is None else threshold
    measured = history.last_prompt_tokens if history.last_prompt_tokens > 0 else history.estimated_tokens
    if measured <= limit:
        return False
    if history.compaction_skipped_at_tokens > 0:
        growth = AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS if retry_growth is None else retry_growth
        return measured >= history.compaction_skipped_at_tokens + growth
    return True


def skipped_record(history: LoopHistory, reason: str) -> dict[str, Any]:
    """没压缩成时随本轮落库的记录（``message_metadata.compaction_skipped``），下一轮据此退避。"""
    measured = history.last_prompt_tokens if history.last_prompt_tokens > 0 else history.estimated_tokens
    return {"reason": reason, "at_prompt_tokens": int(measured), "created_at": utcnow().isoformat()}



def _chat_tools() -> list[dict[str, Any]]:
    """压缩请求带上同一份工具清单（tool_choice=none）：历史里的工具调用对供应商才合法。"""
    from agent.tools.registry import get_exposed_tools

    tools = []
    for schema in get_exposed_tools():
        parameters = schema.get("input_schema")
        tools.append({
            "type": "function",
            "function": {
                "name": schema.get("name"),
                "description": schema.get("description") or "",
                "parameters": parameters if isinstance(parameters, dict) else {"type": "object", "properties": {}},
            },
        })
    return tools


def _request_messages(system_prompt: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from agents.models.chatcmpl_converter import Converter

    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    messages.extend(Converter.items_to_messages(items))  # type: ignore[arg-type]
    messages.append({"role": "user", "content": COMPACT_PROMPT})
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
) -> CompactionOutcome:
    """写交接摘要并给出压缩后的历史；摘要不可用时返回 skipped，历史保持原样。"""
    from agent.core import llm_failover
    from agent.openai_agents.usage_hooks import agent_usage_attribution
    from services.usage.cost_budget import budget_attribution

    attribution = agent_usage_attribution()
    messages = _request_messages(system_prompt, bounded_items(history.items, AGENT_LOOP_COMPACT_INPUT_MAX_TOKENS))
    usage: dict[str, int] = {}
    summary = ""
    started = time.monotonic()
    for effort in _ATTEMPT_EFFORTS:
        sink: dict[str, Any] = {}
        try:
            with budget_attribution(attribution):
                response, _provider = await llm_failover.acreate_chat_completion(
                    call_site=COMPACTION_CALL_SITE,
                    session_id=session_id,
                    reasoning_effort=effort,
                    usage_attribution=attribution,
                    attempt_sink=sink,
                    messages=messages,
                    tools=_chat_tools(),
                    tool_choice="none",
                    max_tokens=AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS,
                    temperature=1.0,
                    top_p=0.95,
                )
        except Exception as exc:  # noqa: BLE001 - 压缩失败不影响这一轮照常运行
            log_with_context(
                logger,
                30,  # WARNING
                "Loop history compaction call failed; continuing without compaction",
                error_type=type(exc).__name__,
                effort=effort or "default",
            )
            return CompactionOutcome(usage=usage or None, skipped=SKIPPED_PROVIDER_ERROR)
        _merge_usage(usage, _usage_of(response))
        choice = response.choices[0] if getattr(response, "choices", None) else None
        text = str(getattr(getattr(choice, "message", None), "content", "") or "").strip()
        finish_reason = getattr(choice, "finish_reason", None)
        if text and finish_reason != "length":
            summary = text
            break
        log_with_context(
            logger,
            30,  # WARNING
            "Loop history compaction summary empty or truncated",
            effort=effort or "default",
            finish_reason=finish_reason,
            summary_chars=len(text),
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
