"""单循环 agent 的跨轮历史：按会话里的消息行重建模型输入（不按 token 预算开窗，只在压缩点之后截断）。

- 单循环 agent 写下的 assistant 行（``message_metadata.engine == "loop"``）按 ``display_events``
  的顺序还原成 SDK 输入项：回复文字是 assistant 消息，工具调用是成对的 function_call /
  function_call_output（每个调用都有输出，不会留下孤立的 tool_call_id）。思考内容只在一次
  run 内回放，这里不带。
- 多 agent 图写下的旧行没有 engine 标记，照旧按 ``SessionLoader._format_chat_message_for_history``
  折叠成文字（正文 + 状态卡摘要 + 工具面包屑），旧会话照常续写。
- 压缩点（某条 assistant 行的 ``message_metadata.compaction``）：它覆盖到
  ``covers_through_message_id`` 那一行为止。重建结果是 压缩点之前最近的作者原话（预算内原文）
  → 一条以「【上下文已压缩】」开头的 user 消息（交接摘要）→ 那一行之后的全部行。
  压缩点记了 ``kept_author_messages``（压缩那一轮实际发出的作者原话）时原样回放它们，
  不再按行重新截断：前缀跨轮逐字不变；没有这个字段的旧压缩点照旧从行里按预算重算。

只按结构化字段（行角色、metadata 键、行 id）组装，不读自然语言判断语义。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import desc, or_
from sqlmodel import Session, and_, select

from agent.core.output_truncation import OUTPUT_TRUNCATED_REASON
from agent.utils.token_utils import estimate_text_tokens
from config.agent_runtime import AGENT_LOOP_COMPACT_KEEP_USER_TOKENS
from utils.logger import get_logger, log_with_context

from .prompts import KEPT_USER_TRUNCATED_SUFFIX, SUMMARY_PREFIX

logger = get_logger(__name__)

# message_metadata 里标记单循环 agent 写下的行。
LOOP_ENGINE_METADATA_VALUE = "loop"
_PAGE_SIZE = 100
# 没做完就结束的一轮（落库的 stop_reason）：回放时补一句，模型知道「继续」要接着什么做。
_INTERRUPTED_ROUND_NOTES = {
    "user_stopped": "（这一轮没做完：作者停止了生成。）",
    "client_disconnected": "（这一轮没做完：连接中断了。）",
    "cancelled": "（这一轮没做完：连接中断了。）",
    "run_deadline_exceeded": "（这一轮没做完：时间用完了。）",
    "max_turns_exceeded": "（这一轮没做完：工具调用轮数到了上限。）",
    "model_call_budget_exhausted": "（这一轮没做完：模型调用次数到了上限。）",
}
# create_file 的结果里只对当时那一步有意义、回放时会误导模型的字段。
_CREATE_FILE_TRANSIENT_KEYS = ("awaiting_body", "next_step", "content", "body_in_reply")
_REPLACE_NOT_SAVED_NOTE = (
    "这次整章替换的新正文没有保存，这一章保持原样；当时的写入定位已失效。"
    "若本轮作者仍要求完成整章写作，先重新调用 write_chapter 建立本轮目标，"
    "下一次回复再用 <file>…</file> 写入，不先把整章发到聊天，"
    "也不用未保存的新稿作 edit_file 锚点。若本轮改为只读或其他任务，不要自动恢复旧写入。"
)
# 落库的工具结果是前端卡片口径，不是模型当时拿到的原文：技能正文不落库，失败只留给作者看的说明。
# 回放时如实说明，模型不会以为自己手里还有这些内容。
_SKILL_CONTENT_NOT_KEPT = {
    "load_skill": "技能方法正文没有保留在历史里；这一轮要按这个技能做，先重新调用 load_skill 读取。",
    "read_skill_resource": "资源正文没有保留在历史里；这一轮要用它，先重新调用 read_skill_resource 读取。",
}
_FAILED_CALL_NOTE = "这是当时给作者看的失败说明；工具给你的具体原因与候选没有保留，重试前先重新读取相关文件。"


@dataclass
class LoopHistory:
    """重建好的跨轮历史与压缩所需的事实。"""

    items: list[dict[str, Any]] = field(default_factory=list)
    # 上一条单循环 assistant 行记下的最后一次模型调用输入 token（含缓存）；没有时为 0。
    last_prompt_tokens: int = 0
    # 会话里最新一行的 id：本轮若压缩，压缩覆盖到这一行为止。
    last_message_id: str | None = None
    # 是否用到了已有的压缩点。
    compacted: bool = False
    # 历史里作者的原话（按时间先后，不含系统插入的摘要消息）：本轮压缩时从中保留最近的几条。
    author_messages: list[str] = field(default_factory=list)
    # 最近一次没压缩成（摘要不可用）时的实测输入 token；之后有成功的压缩点时为 0。
    compaction_skipped_at_tokens: int = 0
    # 会话末尾连续几轮停在输出上限（stop_reason=output_truncated）。
    trailing_output_truncations: int = 0

    @property
    def estimated_tokens(self) -> int:
        return estimate_items_tokens(self.items)


def bounded_items(items: list[dict[str, Any]], max_tokens: int) -> list[dict[str, Any]]:
    """输入项的上限：从最旧的项开始丢，丢完后开头不留没有调用的工具输出。"""
    sizes = [estimate_items_tokens([item]) for item in items]
    total = sum(sizes)
    start = 0
    while len(items) - start > 1 and total > max_tokens:
        total -= sizes[start]
        start += 1
    while start < len(items) and items[start].get("type") == "function_call_output":
        start += 1
    return list(items[start:])


def estimate_items_tokens(items: list[dict[str, Any]]) -> int:
    """输入项的粗略 token 数（只用于压缩触发的兜底与压缩输入的上限）。"""
    total = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("content", "arguments", "output"):
            value = item.get(key)
            if isinstance(value, str) and value:
                total += estimate_text_tokens(value)
        total += 4
    return total


def _parse_json(value: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def row_metadata(row: Any) -> dict[str, Any]:
    payload = _parse_json(getattr(row, "message_metadata", None))
    return payload if isinstance(payload, dict) else {}


def valid_compaction(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """assistant 行上可用的压缩点：摘要非空、覆盖到的行 id 非空。"""
    compaction = metadata.get("compaction")
    if not isinstance(compaction, dict):
        return None
    summary = compaction.get("summary")
    covers = compaction.get("covers_through_message_id")
    if not isinstance(summary, str) or not summary.strip() or not isinstance(covers, str) or not covers:
        return None
    return compaction


def stored_kept_messages(compaction: dict[str, Any]) -> list[str] | None:
    """压缩点记下的作者原话（压缩那一轮实际发出的文字）；没有或格式不对时为 None，按行重算。"""
    kept = compaction.get("kept_author_messages")
    if not isinstance(kept, list) or not all(isinstance(content, str) for content in kept):
        return None
    return kept


def summary_message(summary: str) -> dict[str, Any]:
    return {"role": "user", "content": f"{SUMMARY_PREFIX}{summary.strip()}"}


def _tool_output_text(call: dict[str, Any]) -> str:
    """持久化的工具结果（前端卡片口径）还原成交给模型的结果文字。"""
    status = str(call.get("status") or "")
    name = str(call.get("name") or "")
    result = call.get("result")
    error = call.get("error")
    if status == "pending" and result is None and error is None:
        return json.dumps({"status": "unknown", "note": "这次调用的结果没有记录下来"}, ensure_ascii=False)
    if name in ("create_file", "write_chapter") and status == "success" and isinstance(result, dict):
        replaced = name == "write_chapter" and result.get("action") == "replaced"
        streamed = result.get("awaiting_body") is True or result.get("body_in_reply") is True
        saved = result.get("body_saved") is True
        result = {key: value for key, value in result.items() if key not in _CREATE_FILE_TRANSIENT_KEYS}
        # 历史回执只说明过去的保存事实；捕获目标与版本绑定只在原请求里有效。
        result["write_target_active"] = False
        if replaced:
            # 整章替换只有落库后补发的结果才带 body_saved；只有定位结果说明新正文没有存下。
            result["body_saved"] = saved
            if not saved:
                result["result"] = _REPLACE_NOT_SAVED_NOTE
        elif streamed:
            result["body_saved"] = True
    payload: dict[str, Any] = {"status": status or "success"}
    if result is not None:
        payload["data"] = result
    if error:
        payload["error"] = error
    if status == "error":
        payload["note"] = _FAILED_CALL_NOTE
    elif name in _SKILL_CONTENT_NOT_KEPT:
        payload["note"] = _SKILL_CONTENT_NOT_KEPT[name]
    return json.dumps(payload, ensure_ascii=False)


def _loop_row_items(row: Any, metadata: dict[str, Any]) -> list[dict[str, Any]]:
    """一条单循环 assistant 行 → SDK 输入项（文字与成对的工具调用，按显示顺序）。"""
    raw_calls = _parse_json(getattr(row, "tool_calls", None))
    calls = [call for call in raw_calls if isinstance(call, dict)] if isinstance(raw_calls, list) else []
    events = metadata.get("display_events")
    items: list[dict[str, Any]] = []
    text_parts: list[str] = []
    used: set[int] = set()

    def flush_text() -> None:
        text = "".join(text_parts).strip()
        text_parts.clear()
        if text:
            items.append({"role": "assistant", "content": text})

    def add_call(index: int) -> None:
        if index in used or not 0 <= index < len(calls):
            return
        used.add(index)
        call = calls[index]
        call_id = f"h{row.id}_{index}"
        arguments = call.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False)
        items.append({
            "type": "function_call",
            "call_id": call_id,
            "name": str(call.get("name") or ""),
            "arguments": arguments,
        })
        items.append({"type": "function_call_output", "call_id": call_id, "output": _tool_output_text(call)})

    if isinstance(events, list) and events:
        for event in events:
            if not isinstance(event, dict):
                continue
            event_type = event.get("type")
            if event_type == "content" and isinstance(event.get("content"), str):
                text_parts.append(event["content"])
            elif event_type == "tool_call" and isinstance(event.get("tool_call_index"), int):
                flush_text()
                add_call(event["tool_call_index"])
        flush_text()
        # 显示时间线没覆盖到的调用（不该出现）照样成对补上，不丢工具事实。
        for index in range(len(calls)):
            add_call(index)
    else:
        for index in range(len(calls)):
            add_call(index)
        text_parts.append(str(getattr(row, "content", "") or ""))
        flush_text()
    return items


def _legacy_row_items(loader: Any, row: Any) -> list[dict[str, Any]]:
    """多 agent 图写下的行：沿用原有的文字折叠（正文 + 状态卡摘要 + 工具面包屑）。"""
    from agent.openai_agents.runner import normalize_messages_for_openai_agents

    return normalize_messages_for_openai_agents([loader._format_chat_message_for_history(row)])


def _row_items(loader: Any, row: Any) -> list[dict[str, Any]]:
    content = str(getattr(row, "content", "") or "")
    if row.role == "user":
        return [{"role": "user", "content": content}] if content.strip() else []
    metadata = row_metadata(row)
    if metadata.get("engine") != LOOP_ENGINE_METADATA_VALUE:
        return _legacy_row_items(loader, row)
    items = _loop_row_items(row, metadata)
    if not any(item.get("role") == "assistant" for item in items):
        # 只有状态卡、没有文字的一轮（例如轮数耗尽）：补一段状态摘要，模型知道这一轮停在哪。
        status_cards = metadata.get("status_cards")
        if isinstance(status_cards, list):
            summary = loader._build_status_cards_history_content(status_cards)
            if summary:
                items.append({"role": "assistant", "content": summary})
    stop_reason = metadata.get("stop_reason")
    if stop_reason == "output_truncated":
        from agent.core.output_truncation import OUTPUT_TRUNCATED_HISTORY

        items.append({"role": "assistant", "content": OUTPUT_TRUNCATED_HISTORY})
    elif isinstance(stop_reason, str) and stop_reason in _INTERRUPTED_ROUND_NOTES:
        items.append({"role": "assistant", "content": _INTERRUPTED_ROUND_NOTES[stop_reason]})
    return items


def kept_author_messages(author_messages: list[str], budget_tokens: int) -> list[dict[str, Any]]:
    """压缩点之前最近的作者原话（按时间先后传入）：按 token 预算原样保留，超出的那条截断后停下。"""
    kept: list[dict[str, Any]] = []
    remaining = budget_tokens
    for content in reversed(author_messages):
        if not content.strip():
            continue
        tokens = estimate_text_tokens(content)
        if tokens > remaining:
            # 剩余预算按字符比例截一段；剩得太少就不留半句话。
            keep_chars = int(len(content) * remaining / max(tokens, 1))
            if keep_chars > 200:
                kept.append({"role": "user", "content": content[:keep_chars] + KEPT_USER_TRUNCATED_SUFFIX})
            break
        kept.append({"role": "user", "content": content})
        remaining -= tokens
    kept.reverse()
    return kept


def load_loop_history(
    session: Session,
    *,
    project_id: str,
    user_id: str | None,
    chat_session_id: str,
    keep_user_tokens: int | None = None,
) -> LoopHistory:
    """按会话重建单循环 agent 的历史（调用方已核对会话归属）。

    由新到旧分页读：遇到最近的压缩点后读到它覆盖的那一行为止；压缩点没记保留的作者原话（旧记录）时
    再往前只取作者原话直到用完预算；没有压缩点时读完整个会话。
    """
    from agent.core.session_loader import SessionLoader
    from models import ChatMessage

    if keep_user_tokens is None:
        keep_user_tokens = AGENT_LOOP_COMPACT_KEEP_USER_TOKENS
    loader = SessionLoader(project_id, user_id)
    result = LoopHistory()
    after_rows: list[Any] = []  # 新 → 旧
    before_rows: list[Any] = []  # 压缩点覆盖范围内的行（新 → 旧），只取作者原话
    compaction: dict[str, Any] | None = None
    stored_kept: list[str] | None = None
    covered_reached = False
    kept_tokens_seen = 0
    measured_seen = False
    skip_seen = False
    truncation_streak_open = True
    cursor: tuple[Any, Any] | None = None

    while True:
        stmt = select(ChatMessage).where(
            and_(
                ChatMessage.session_id == chat_session_id,
                ChatMessage.role.in_(("user", "assistant")),
            )
        )
        if cursor is not None:
            created_at, row_id = cursor
            stmt = stmt.where(
                or_(
                    ChatMessage.created_at < created_at,
                    and_(ChatMessage.created_at == created_at, ChatMessage.id < row_id),
                )
            )
        page = session.exec(
            stmt.order_by(desc(ChatMessage.created_at), desc(ChatMessage.id)).limit(_PAGE_SIZE)
        ).all()
        if not page:
            break
        stop = False
        for row in page:
            if result.last_message_id is None:
                result.last_message_id = row.id
            if covered_reached:
                before_rows.append(row)
                if row.role == "user":
                    kept_tokens_seen += estimate_text_tokens(str(row.content or ""))
                    if kept_tokens_seen >= keep_user_tokens:
                        stop = True
                        break
                continue
            if compaction is not None and row.id == compaction["covers_through_message_id"]:
                covered_reached = True
                if stored_kept is not None:
                    # 保留的作者原话已记在压缩点上，不必再往前读。
                    stop = True
                    break
                before_rows.append(row)
                if row.role == "user":
                    kept_tokens_seen += estimate_text_tokens(str(row.content or ""))
                continue
            after_rows.append(row)
            metadata = row_metadata(row)
            if row.role == "assistant":
                if truncation_streak_open:
                    if metadata.get("stop_reason") == OUTPUT_TRUNCATED_REASON:
                        result.trailing_output_truncations += 1
                    else:
                        truncation_streak_open = False
                if not measured_seen and metadata.get("engine") == LOOP_ENGINE_METADATA_VALUE:
                    measured_seen = True
                    tokens = metadata.get("prompt_tokens")
                    if isinstance(tokens, int) and tokens > 0:
                        result.last_prompt_tokens = tokens
            # 压缩点 / 没压缩成的记录通常在本轮 assistant 行上；这一轮没有 assistant 行时记在作者那条消息上。
            if compaction is None:
                if not skip_seen:
                    skipped = metadata.get("compaction_skipped")
                    if isinstance(skipped, dict) and isinstance(skipped.get("at_prompt_tokens"), int):
                        skip_seen = True
                        result.compaction_skipped_at_tokens = max(0, skipped["at_prompt_tokens"])
                compaction = valid_compaction(metadata)
                if compaction is not None:
                    stored_kept = stored_kept_messages(compaction)
        if stop:
            break
        last = page[-1]
        cursor = (last.created_at, last.id)

    items: list[dict[str, Any]] = []
    if compaction is not None and covered_reached:
        result.compacted = True
        if stored_kept is not None:
            kept = [{"role": "user", "content": content} for content in stored_kept]
        else:
            kept = kept_author_messages(
                [str(row.content or "") for row in reversed(before_rows) if row.role == "user"], keep_user_tokens
            )
        items.extend(kept)
        result.author_messages.extend(str(item["content"]) for item in kept)
        items.append(summary_message(compaction["summary"]))
    elif compaction is not None:
        # 压缩点覆盖到的行找不到了（被删除）：退回完整历史，不凭摘要丢掉现存的行。
        log_with_context(
            logger,
            30,  # WARNING
            "Loop history compaction anchor not found; replaying the full session",
            chat_session_id=chat_session_id,
        )
    for row in reversed(after_rows):
        items.extend(_row_items(loader, row))
        if row.role == "user" and str(row.content or "").strip():
            result.author_messages.append(str(row.content))
    result.items = items
    return result
