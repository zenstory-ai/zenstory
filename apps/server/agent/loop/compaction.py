"""单循环 agent 的两阶段历史压缩（轮与轮之间，从不在一次工具循环中途）。

热缓存（``wire_prefix_cache``）命中时用上一轮最后一次模型调用的实测输入 token 加上本轮新增输入，
冷库回放（``load_loop_history``，``last_prompt_tokens == 0``）时用实际重建项 + 静态系统提示与工具 schema +
本轮新增输入的供应商同量纲估算之和；超过 ``AGENT_LOOP_COMPACT_AT_TOKENS`` 时，本轮开始前按两阶段执行压缩：

- **Stage 1（零 LLM 确定性 ``tool_archive``）**：在 ``AGENT_LOOP_TOOL_ARCHIVE_ENABLED`` 开启时，
  仅针对早于最近 ``AGENT_LOOP_TOOL_ARCHIVE_KEEP_RECENT_TURNS`` 个用户轮次的大体积（``>= 2000`` 字符）
  可重读只读工具输出（``query_files`` / ``read_skill_resource`` / ``load_skill``，绝不触碰写工具或错误结果）
  替换为紧凑确定性归档回执。若节省量 ``>= AGENT_LOOP_TOOL_ARCHIVE_MIN_SAVINGS_TOKENS`` 且归档后总量
  ``+ turn_input_tokens <= AGENT_LOOP_COMPACT_AT_TOKENS``，直接完成压缩（0 次模型调用、0 延迟、100% 无损对话）。
- **Stage 2（LLM 交接摘要兜底）**：若 Stage 1 未达准入条件，仍基于原始 ``history.items`` 回落到同模型交接摘要，
  以保住上游提示词前缀缓存（不改写中段历史）。校验非空、未截断且未混入工具调用标签，
  失败时附带精炼重试说明再试一次，仍失败就不压缩、照常跑这一轮。

正常轮次（未超过 ``AGENT_LOOP_COMPACT_AT_TOKENS``）从不触发 Stage 1，前缀逐字节稳定以保住 KV 缓存。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agent.core.message_manager import AGENT_DIRECTIVE_HEADER, AGENT_DIRECTIVE_HEADER_EN
from agent.core.usage import extract_response_usage, merge_usage_dicts
from config import agent_runtime
from config.agent_runtime import (
    AGENT_LOOP_COMPACT_INPUT_MAX_TOKENS,
    AGENT_LOOP_COMPACT_KEEP_USER_TOKENS,
    AGENT_LOOP_COMPACT_MAX_OUTPUT_TOKENS,
    AGENT_LOOP_COMPACT_RETRY_GROWTH_TOKENS,
)
from config.datetime_utils import utcnow
from utils.logger import get_logger, log_with_context

from .history import (
    ARCHIVABLE_READ_TOOLS,
    COMPACTION_MODE_TOOL_ARCHIVE,
    LoopHistory,
    bound_history_for_provider,
    build_tool_archive_receipt,
    contains_leaked_tool_protocol,
    effective_history_provider_tokens,
    estimate_provider_items_tokens,
    estimate_provider_text_tokens,
    estimate_tools_schema_tokens,
    is_successful_archivable_payload,
    kept_author_messages,
    parse_tool_json,
    query_result_full_file_ids,
    summary_message,
)
from .prompts import COMPACT_PROMPT, COMPACT_PROMPT_EN, SUMMARY_PREFIX, SUMMARY_PREFIX_EN

if TYPE_CHECKING:
    from agent.core.run_meter import AgentRunMeter
    from agent.tools.tool_context import AgentRequestContext

logger = get_logger(__name__)

COMPACTION_CALL_SITE = "agent_loop_compaction"
# 始终保持模型默认思考强度（不降低 reasoning_effort），最多尝试两次。
_MAX_COMPACTION_ATTEMPTS = 2
_COMPACT_RETRY_NOTE = (
    "\n\n[重试提醒] 上一次摘要输出为空、超出单次长度被截断或混入了工具调用协议标签。"
    "本次请不在思考中逐章复述原文或预写全文，只抓关键骨架，直接输出完整纯文本交接摘要，"
    "严禁调用工具或输出任何 <tool_call>、<invoke>、<parameter> 标签。"
)
_COMPACT_RETRY_NOTE_EN = (
    "\n\n[Retry reminder] The previous summary was empty, truncated at the output limit, or contained leaked tool-call protocol tags. "
    "Keep thinking minimal — do not recount chapters or draft full text in thinking; output ONLY the complete plain-text handoff summary "
    "in the author's / manuscript's language, and do NOT call tools or emit any <tool_call>, <invoke>, or <parameter> tags."
)
SKIPPED_EMPTY_OR_TRUNCATED = "empty_or_truncated_summary"
SKIPPED_PROVIDER_ERROR = "provider_error"


@dataclass
class ToolArchiveResult:
    """Stage 1 零 LLM 确定性只读工具输出归档的结果。"""

    items: list[dict[str, Any]]
    archived_call_ids: list[str] = field(default_factory=list)
    archived_file_ids: set[str] = field(default_factory=set)
    # 仅当归档了未能提取出具体 file_id 的 query_files 输出时为 True，供 service 保守撤销全部会话结转快照。
    archived_unidentified_query_files: bool = False
    archived_count: int = 0
    before_tokens: int = 0
    after_tokens: int = 0
    projected_after_tokens: int = 0
    saved_tokens: int = 0
    admitted: bool = False
    record: dict[str, Any] | None = None


@dataclass
class CompactionOutcome:
    """压缩结果：``record`` 写进本轮 assistant 行，``items`` 是压缩后的历史。"""

    record: dict[str, Any] | None = None
    items: list[dict[str, Any]] | None = None
    usage: dict[str, int] | None = None
    skipped: str | None = None
    archived_file_ids: set[str] | None = None
    archived_unidentified_query_files: bool = False


# 单轮内系统注入到 SDK input 里的合成 user 项前缀（精确协议前缀，不作自然语言语义猜测）：
# 压缩摘要、本轮系统指令、写后自查/空文件纠偏/输出截断续写/收尾提醒等。
_SYNTHETIC_USER_PREFIXES: tuple[str, ...] = (
    SUMMARY_PREFIX,
    SUMMARY_PREFIX_EN,
    AGENT_DIRECTIVE_HEADER,
    AGENT_DIRECTIVE_HEADER_EN,
    "[系统提醒]",
    "[System note]",
)


def _leading_user_text(content: Any) -> str:
    """提取 user 项（纯字符串或 SDK 内容块列表）的首段非空文字，供精确前缀比对。"""
    if isinstance(content, str):
        return content.lstrip()
    if isinstance(content, list):
        for part in content:
            if isinstance(part, str) and part.strip():
                return part.lstrip()
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.lstrip()
    return ""


def _is_user_turn_item(item: Any) -> bool:
    """是否为真实作者轮次（排除摘要消息与单轮内系统注入的续跑/收尾/指令 user 项）。"""
    if not isinstance(item, dict) or item.get("role") != "user":
        return False
    leading = _leading_user_text(item.get("content"))
    return not leading.startswith(_SYNTHETIC_USER_PREFIXES)


def archive_old_tool_results(
    history: LoopHistory,
    *,
    turn_input_tokens: int = 0,
    keep_recent_turns: int | None = None,
    min_output_chars: int | None = None,
    min_savings_tokens: int | None = None,
    compact_at_tokens: int | None = None,
    english: bool = False,
) -> ToolArchiveResult:
    """Stage 1：零 LLM 确定性归档较早轮次的大体积只读工具输出。

    规则：
    - 最近 ``keep_recent_turns`` 个真实用户轮次完整保留，逐字节不碰（排除单轮内系统注入的
      ``[系统提醒]`` / ``[System note]`` / ``[本次运行的系统指令]`` / 摘要等合成 user 项）；
    - 仅归档早于保护窗口、且属于 ``ARCHIVABLE_READ_TOOLS``（``query_files`` /
      ``read_skill_resource`` / ``load_skill``）、长度 ``>= min_output_chars``、
      且为成功 JSON 结果的 ``function_call_output``；绝不触碰写工具、错误结果或已归档回执；
    - 用供应商同量纲估算 ``estimate_provider_items_tokens`` 计算归档前后体积与节省量，
      并结合实测 ``history.last_prompt_tokens`` 投射归档后真实提示词水位
      ``projected_after = max(after_tokens, max(0, last_prompt_tokens - saved_tokens))``；
    - 仅当 ``archived_count > 0``、``saved_tokens >= min_savings_tokens`` 且
      ``projected_after + turn_input_tokens <= compact_at_tokens`` 时准入（``admitted=True``）。
    """
    keep_turns = (
        agent_runtime.AGENT_LOOP_TOOL_ARCHIVE_KEEP_RECENT_TURNS
        if keep_recent_turns is None
        else max(0, keep_recent_turns)
    )
    min_chars = (
        agent_runtime.AGENT_LOOP_TOOL_ARCHIVE_MIN_OUTPUT_CHARS
        if min_output_chars is None
        else max(1, min_output_chars)
    )
    min_savings = (
        agent_runtime.AGENT_LOOP_TOOL_ARCHIVE_MIN_SAVINGS_TOKENS
        if min_savings_tokens is None
        else max(1, min_savings_tokens)
    )
    compact_limit = (
        agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS
        if compact_at_tokens is None
        else compact_at_tokens
    )

    if not history.items:
        return ToolArchiveResult(items=[])

    before_tokens = estimate_provider_items_tokens(history.items)
    user_indices = [idx for idx, item in enumerate(history.items) if _is_user_turn_item(item)]
    if keep_turns == 0:
        archive_cutoff_index = len(history.items)
    elif len(user_indices) <= keep_turns:
        archive_cutoff_index = 0
    else:
        archive_cutoff_index = user_indices[-keep_turns]

    if archive_cutoff_index <= 0:
        return ToolArchiveResult(
            items=list(history.items),
            before_tokens=before_tokens,
            after_tokens=before_tokens,
            projected_after_tokens=max(before_tokens, history.last_prompt_tokens),
        )

    # 按序匹配最近未消费的 function_call（支持上游/代理跨轮复用 call_0 等非全局唯一 call_id，
    # 绝不发生后轮 query_files(call_0) 覆盖前轮 edit_file(call_0) 导致误归档写工具，或保护窗覆盖旧读工具）。
    pending_calls_by_id: dict[str, deque[tuple[int, str, int]]] = {}
    matched_call_by_output_idx: dict[int, tuple[int, str, int]] = {}
    call_id_counts: dict[str, int] = {}
    for idx, item in enumerate(history.items):
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        call_id = item.get("call_id") or item.get("id")
        if not isinstance(call_id, str) or not call_id:
            continue
        if item_type == "function_call":
            tool_name = item.get("name")
            if isinstance(tool_name, str) and tool_name:
                occ_idx = call_id_counts.get(call_id, 0)
                call_id_counts[call_id] = occ_idx + 1
                pending_calls_by_id.setdefault(call_id, deque()).append((idx, tool_name, occ_idx))
        elif item_type == "function_call_output":
            queue = pending_calls_by_id.get(call_id)
            if queue:
                matched_call_by_output_idx[idx] = queue.popleft()

    new_items: list[dict[str, Any]] = list(history.items)
    archived_call_ids: list[str] = []
    archived_call_occurrences: dict[str, list[int]] = {}
    archived_file_ids: set[str] = set()
    archived_unidentified_query_files = False
    archived_count = 0

    for idx in range(archive_cutoff_index):
        item = history.items[idx]
        if not isinstance(item, dict) or item.get("type") != "function_call_output":
            continue
        call_id = item.get("call_id") or item.get("id")
        if not isinstance(call_id, str) or not call_id:
            continue
        info = matched_call_by_output_idx.get(idx)
        if info is None:
            continue
        call_idx, tool_name, occ_idx = info
        if call_idx >= archive_cutoff_index or tool_name not in ARCHIVABLE_READ_TOOLS:
            continue
        output = item.get("output")
        if not isinstance(output, str) or len(output) < min_chars:
            continue
        parsed = parse_tool_json(output)
        if not is_successful_archivable_payload(parsed):
            continue
        receipt_json, file_ids = build_tool_archive_receipt(tool_name, output, parsed, english=english)
        if len(receipt_json) >= len(output):
            continue
        new_items[idx] = {**item, "output": receipt_json}
        archived_call_ids.append(call_id)
        if call_id_counts.get(call_id, 0) > 1:
            archived_call_occurrences.setdefault(call_id, []).append(occ_idx)
        if tool_name in ("query_files", "read_files"):
            if file_ids:
                archived_file_ids.update(file_ids)
            else:
                archived_unidentified_query_files = True
        archived_count += 1

    if archived_count == 0:
        return ToolArchiveResult(
            items=list(history.items),
            before_tokens=before_tokens,
            after_tokens=before_tokens,
            projected_after_tokens=max(before_tokens, history.last_prompt_tokens),
        )

    # 若被归档的文件在受保护的最近轮次中又被完整重读，该文件全文仍在模型视野中，不撤销其快照。
    if archived_file_ids:
        for idx in range(archive_cutoff_index, len(history.items)):
            item = history.items[idx]
            if not isinstance(item, dict) or item.get("type") != "function_call_output":
                continue
            info = matched_call_by_output_idx.get(idx)
            if info is None or info[1] not in ("query_files", "read_files"):
                continue
            output = item.get("output")
            if not isinstance(output, str) or not output:
                continue
            parsed = parse_tool_json(output)
            if is_successful_archivable_payload(parsed):
                for fid in query_result_full_file_ids(parsed):
                    archived_file_ids.discard(fid)

    after_tokens = estimate_provider_items_tokens(new_items)
    saved_tokens = max(0, before_tokens - after_tokens)
    projected_after_tokens = (
        max(after_tokens, max(0, history.last_prompt_tokens - saved_tokens))
        if history.last_prompt_tokens > 0
        else after_tokens
    )
    admitted = (
        archived_count > 0
        and saved_tokens >= min_savings
        and (projected_after_tokens + max(0, turn_input_tokens)) <= compact_limit
    )
    record: dict[str, Any] | None = None
    if admitted:
        record = {
            "mode": COMPACTION_MODE_TOOL_ARCHIVE,
            "archived_through_message_id": history.last_message_id or "",
            "archived_call_ids": archived_call_ids,
            "archived_count": archived_count,
            "saved_tokens": saved_tokens,
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "projected_after_tokens": projected_after_tokens,
            "created_at": utcnow().isoformat(),
        }
        if archived_call_occurrences:
            record["archived_call_occurrences"] = archived_call_occurrences
    return ToolArchiveResult(
        items=new_items,
        archived_call_ids=archived_call_ids,
        archived_file_ids=archived_file_ids,
        archived_unidentified_query_files=archived_unidentified_query_files,
        archived_count=archived_count,
        before_tokens=before_tokens,
        after_tokens=after_tokens,
        projected_after_tokens=projected_after_tokens,
        saved_tokens=saved_tokens,
        admitted=admitted,
        record=record,
    )


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
    limit = agent_runtime.AGENT_LOOP_COMPACT_AT_TOKENS if threshold is None else threshold
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


def skipped_record(
    history: LoopHistory,
    reason: str,
    *,
    extra_tokens: int = 0,
) -> dict[str, Any]:
    """没压缩成时随本轮落库的记录（``message_metadata.compaction_skipped``）。

    记录跳过时历史本身的实测/同量纲输入 token（冷库回放时通过 ``extra_tokens`` 补齐静态系统提示
    与工具 schema 开销，与热缓存 ``last_prompt_tokens`` 口径一致）：模型输出不可用时下一轮据此退避，
    瞬时 ``provider_error`` 仍保留审计记录但不触发退避。
    """
    measured = effective_history_provider_tokens(history) + max(0, extra_tokens)
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
    english: bool = False,
) -> list[dict[str, Any]]:
    """构造 Responses API 压缩输入（清理 fake id / provider_data，末尾追加压缩指令）。"""
    from agents.items import ItemHelpers
    from agents.models.fake_id import FAKE_RESPONSES_ID
    from agents.util._json import _to_dump_compatible

    base_prompt = COMPACT_PROMPT_EN if english else COMPACT_PROMPT
    retry_note = _COMPACT_RETRY_NOTE_EN if english else _COMPACT_RETRY_NOTE
    prompt = f"{base_prompt}{retry_note}" if retry_prompt else base_prompt
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
    english: bool = False,
) -> list[dict[str, Any]]:
    from agents.models.chatcmpl_converter import Converter

    from agent.core.llm_providers import primary_model_name
    from agent.openai_agents.model import (
        _replay_reasoning_content,
        bridge_responses_reasoning_for_chat_completions,
    )

    base_prompt = COMPACT_PROMPT_EN if english else COMPACT_PROMPT
    retry_note = _COMPACT_RETRY_NOTE_EN if english else _COMPACT_RETRY_NOTE
    prompt = f"{base_prompt}{retry_note}" if retry_prompt else base_prompt
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


def compacted_items(
    author_messages: list[str],
    summary: str,
    keep_user_tokens: int,
    *,
    english: bool = False,
) -> list[dict[str, Any]]:
    """压缩后的历史：最近的作者原话（预算内原文）+ 交接摘要。与 history 下一轮从库里重建的形状相同。"""
    return [
        *kept_author_messages(author_messages, keep_user_tokens, english=english),
        summary_message(summary, english=english),
    ]


async def compact_history(
    history: LoopHistory,
    *,
    system_prompt: str,
    session_id: str | None,
    keep_user_tokens: int | None = None,
    run_meter: AgentRunMeter | None = None,
    request_context: AgentRequestContext | None = None,
    turn_input_tokens: int = 0,
    compact_at_tokens: int | None = None,
    pre_archive: ToolArchiveResult | None = None,
    english: bool = False,
) -> CompactionOutcome:
    """两阶段压缩历史：先尝试 Stage 1 零 LLM ``tool_archive``，未达准入则回落到 Stage 2 交接摘要。

    若 Stage 1 未达准入条件，Stage 2 直接基于原始 ``history.items`` 构造摘要请求以命中上游前缀缓存；
    摘要仍不可用时返回 skipped，历史保持原样。

    每次 Stage 2 摘要调用都记进 ``run_meter``（请求级模型调用预算）。次数由
    ``_MAX_COMPACTION_ATTEMPTS`` 限定、发生在这一轮开始前，所以这里不看预算是否用完。
    """
    from agent.core import llm_failover
    from agent.openai_agents.intra_run_trimmer import IntraRunToolOutputTrimmer
    from agent.openai_agents.usage_hooks import agent_usage_attribution
    from core.error_handler import APIException
    from services.usage.cost_budget import budget_attribution

    source_items = history.items
    if agent_runtime.AGENT_LOOP_TOOL_ARCHIVE_ENABLED:
        archive_result = (
            pre_archive
            if pre_archive is not None
            else archive_old_tool_results(
                history,
                turn_input_tokens=turn_input_tokens,
                compact_at_tokens=compact_at_tokens,
                english=english,
            )
        )
        if archive_result.admitted and archive_result.record is not None:
            log_with_context(
                logger,
                20,  # INFO
                "Loop history compacted via Stage 1 tool_archive (zero LLM calls)",
                archived_count=archive_result.archived_count,
                before_tokens=archive_result.before_tokens,
                after_tokens=archive_result.after_tokens,
                projected_after_tokens=archive_result.projected_after_tokens,
                saved_tokens=archive_result.saved_tokens,
                turn_input_tokens=turn_input_tokens,
                history_items=len(history.items),
            )
            return CompactionOutcome(
                record=archive_result.record,
                items=archive_result.items,
                usage=None,
                archived_file_ids=set(archive_result.archived_file_ids),
                archived_unidentified_query_files=archive_result.archived_unidentified_query_files,
            )
        if archive_result.archived_count > 0:
            log_with_context(
                logger,
                20,  # INFO
                "Stage 1 tool_archive did not meet admission; falling back to Stage 2 LLM summary with original history",
                archived_count=archive_result.archived_count,
                before_tokens=archive_result.before_tokens,
                after_tokens=archive_result.after_tokens,
                saved_tokens=archive_result.saved_tokens,
                turn_input_tokens=turn_input_tokens,
            )

    attribution = agent_usage_attribution(request_context)
    input_budget = AGENT_LOOP_COMPACT_INPUT_MAX_TOKENS
    cold_fixed_tokens = (
        estimate_provider_text_tokens(system_prompt) + estimate_tools_schema_tokens()
        if history.last_prompt_tokens <= 0
        else 0
    )
    source_effective_tokens = effective_history_provider_tokens(history) + cold_fixed_tokens

    def _trim_for_budget(budget_tokens: int, *, force_trim: bool = False) -> list[dict[str, Any]]:
        if not force_trim and source_effective_tokens <= budget_tokens:
            return source_items
        # 仅当实测/估算已超输入预算（或 400/413/422 超窗减半重试）时才折叠旧工具载荷，
        # 避免在 300k–600k 正常压缩区因原始 JSON / 密文字符数误伤热 KV 前缀缓存。
        trimmer_max_chars = max(200_000, int(budget_tokens * 1.5))
        return IntraRunToolOutputTrimmer(max_input_chars=trimmer_max_chars).trim_items(
            source_items, instructions_chars=len(system_prompt)
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
                    resp_input = _responses_input(
                        bounded, retry_prompt=retry_after_invalid_output, english=english
                    )

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
                                english=english,
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
                        english=english,
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
                trimmed_items = _trim_for_budget(input_budget, force_trim=True)
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
        english=english,
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
        effective_history_tokens=effective_history_provider_tokens(history),
        turn_input_tokens=turn_input_tokens,
        duration_ms=duration_ms,
    )
    return CompactionOutcome(record=record, items=items, usage=usage or None)
