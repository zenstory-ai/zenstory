"""单循环 agent 的跨轮历史：按会话里的消息行重建模型输入（不按 token 预算开窗，只在压缩点之后截断）。

- 单循环 agent 写下的 assistant 行（``message_metadata.engine == "loop"``）按 ``display_events``
  的顺序还原成 SDK 输入项：回复文字是 assistant 消息，工具调用是成对的 function_call /
  function_call_output（每个调用都有输出，不会留下孤立的 tool_call_id）。思考内容只在一次
  run 内回放，这里不带。
- 旧格式历史行没有 engine 标记时，回退为简洁的纯文本 ``row.content`` 摘要。
- 压缩点（某条 assistant 行的 ``message_metadata.compaction``）：它覆盖到
  ``covers_through_message_id`` 那一行为止。重建结果是 压缩点之前最近的作者原话（预算内原文）
  → 一条以「【上下文已压缩】」开头的 user 消息（交接摘要）→ 那一行之后的全部行。
  压缩点记了 ``kept_author_messages``（压缩那一轮实际发出的作者原话）时原样回放它们，
  不再按行重新截断：前缀跨轮逐字不变；没有这个字段的旧压缩点照旧从行里按预算重算。

只按结构化字段（行角色、metadata 键、行 id）组装，不读自然语言判断语义。
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import desc, or_
from sqlmodel import Session, and_, select

from agent.core.output_truncation import OUTPUT_TRUNCATED_REASON
from agent.core.protocol_filter import contains_leaked_tool_protocol
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
# create_file / write_chapter 的结果里只对当时那一步或前端卡片有意义、回放时会误导/膨胀上下文的字段。
_CREATE_FILE_TRANSIENT_KEYS = ("awaiting_body", "next_step", "content", "body_in_reply", "undo")
_REPLACE_NOT_SAVED_NOTE = (
    "这次整章替换的新正文没有保存，这一章保持原样；当时的写入定位已失效。"
    "若本轮作者仍要求完成整章写作，先重新调用 write_chapter 建立本轮目标，"
    "下一次回复再用 <file>…</file> 写入，不先把整章发到聊天，"
    "也不用未保存的新稿作 edit_file 锚点。若本轮改为只读或其他任务，不要自动恢复旧写入。"
)
_CREATE_NOT_SAVED_NOTE = "这次新建的正文没有确认保存；需要时先用 read_files 查这个 id 的现状。"
# 落库的工具结果是前端卡片口径，不是模型当时拿到的原文：技能正文不落库，失败只留给作者看的说明。
# 回放时如实说明，模型不会以为自己手里还有这些内容。
_SKILL_CONTENT_NOT_KEPT = {
    "load_skill": "技能方法正文没有保留在历史里；这一轮要按这个技能做，先重新调用 load_skill 读取。",
    "read_skill_resource": "资源正文没有保留在历史里；这一轮要用它，先重新调用 read_skill_resource 读取。",
}
# 冷回放仅对最近 N 个用户轮次内的技能工具调用重新水合正文，避免过早的轮次膨胀冷回放输入。
SKILL_REPLAY_RECENT_TURNS = 3
_SkillRehydrator = Callable[[str, Any, Any], dict[str, Any] | None]
_FAILED_CALL_NOTE = "这是当时给作者看的失败说明；工具给你的具体原因与候选没有保留，重试前先重新读取相关文件。"

# Stage 1 零 LLM 确定性归档压缩（tool_archive）的共享常量与收据构造。
# compaction.py 与 history.py 共用同一纯函数，确保实时压缩与冷库回放逐字节一致。
COMPACTION_MODE_TOOL_ARCHIVE = "tool_archive"
_FILE_READ_TOOL_NAMES: frozenset[str] = frozenset({"query_files", "read_files"})
_FILE_QUERY_TOOL_NAMES: frozenset[str] = frozenset({"query_files", "read_files", "search_files"})
ARCHIVABLE_READ_TOOLS: frozenset[str] = frozenset(
    {"query_files", "read_files", "search_files", "read_skill_resource", "load_skill"}
)
_TOOL_ARCHIVE_NOTES: dict[str, str] = {
    "query_files": (
        "该只读查询结果已在历史归档中折叠以节省上下文；"
        "如需原文细节，请按文件 id 重新调用 read_files(ids=[...]) 读取。"
    ),
    "read_files": (
        "该文件读取结果已在历史归档中折叠以节省上下文；"
        "如需原文细节，请按文件 id 重新调用 read_files(ids=[...]) 读取。"
    ),
    "search_files": (
        "该文件检索结果已在历史归档中折叠以节省上下文；"
        "如需匹配明细请重新调用 search_files，需读全文请调用 read_files(ids=[...])。"
    ),
    "load_skill": (
        "该技能正文已在历史归档中折叠以节省上下文；"
        "如需按该技能执行，请重新调用 load_skill 读取。"
    ),
    "read_skill_resource": (
        "该技能参考资源已在历史归档中折叠以节省上下文；"
        "如需原文，请重新调用 read_skill_resource 读取。"
    ),
}
_MAX_ARCHIVE_FILE_REFS = 50


@dataclass
class LoopHistory:
    """重建好的跨轮历史与压缩所需的事实。"""

    items: list[dict[str, Any]] = field(default_factory=list)
    # 热缓存（wire_prefix_cache）命中的上一轮最后一次模型调用实测输入 token（含缓存、系统提示与工具 schema）；
    # 冷库回放（load_loop_history）时始终为 0，避免继承落库瘦身前失真的热缓存旧读数。
    last_prompt_tokens: int = 0
    # 会话里最新一行的 id：本轮若压缩，压缩覆盖到这一行为止。
    last_message_id: str | None = None
    # 是否用到了已有的 LLM 摘要压缩点。
    compacted: bool = False
    # 历史里作者的原话（按时间先后，不含系统插入的摘要消息）：本轮压缩时从中保留最近的几条。
    author_messages: list[str] = field(default_factory=list)
    # 最近一次没压缩成时的实测输入 token 与原因；之后有成功的压缩点时清零。
    compaction_skipped_at_tokens: int = 0
    compaction_skipped_reason: str | None = None
    # 冷库回放时，最近一次 compaction_skipped 所在轮次之前的历史项估算 token（不含该轮与静态前缀）；
    # 仅冷库回放且存在退避记录时填充，热缓存命中或无退避记录时为 None。
    compaction_skipped_cold_items_tokens: int | None = None
    # 会话末尾连续几轮停在输出上限（stop_reason=output_truncated）。
    trailing_output_truncations: int = 0
    # 最近一次摘要压缩点之后由 Stage 1 tool_archive 归档的 call_id 集合。
    archived_call_ids: set[str] = field(default_factory=set)
    # 被 Stage 1 tool_archive 归档且后续未被非归档调用重新读/写的文件 id 集合。
    archived_file_ids: set[str] = field(default_factory=set)


def bounded_items(items: list[dict[str, Any]], max_tokens: int) -> list[dict[str, Any]]:
    """输入项的上限：按供应商同量纲估算从最旧的项开始丢，丢完后开头不留没有调用的工具输出或悬空助手轮。"""
    sizes = [estimate_provider_items_tokens([item]) for item in items]
    total = sum(sizes)
    start = 0
    while len(items) - start > 1 and total > max_tokens:
        total -= sizes[start]
        start += 1
    while start < len(items) and items[start].get("type") == "function_call_output":
        start += 1
    if start > 0 and start < len(items) and items[start].get("role") != "user":
        first_user = next(
            (idx for idx in range(start, len(items)) if items[idx].get("role") == "user"),
            None,
        )
        if first_user is None:
            return []
        start = first_user
    return list(items[start:])


def estimate_provider_text_tokens(text: str) -> int:
    """按 DeepSeek / MiniMax 等 1M 中文模型的分词量纲粗估 token 数。

    cl100k_base 对汉字按字节拆成约 1.5~2.2 token/字，比国产模型实测（约 0.55~0.70 token/字）
    虚高 2.5~3 倍；无实测读数或评估超窗降级时必须使用供应商同量纲估算，避免在 ~30 万真实 token
    处误触发滑窗截断。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if ord(ch) >= 0x2E80)
    other = len(text) - cjk
    return max(1, int(cjk * 0.65 + other * 0.30))


def _provider_content_tokens(value: Any) -> int:
    """估算字符串或 SDK 结构化内容块列表（output_text / input_text / summary_text / reasoning_text）的供应商 token 数。"""
    if isinstance(value, str):
        return estimate_provider_text_tokens(value) if value else 0
    if not isinstance(value, list):
        return 0
    total = 0
    for part in value:
        if isinstance(part, str) and part:
            total += estimate_provider_text_tokens(part)
        elif isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str) and text:
                total += estimate_provider_text_tokens(text)
    return total


def estimate_provider_items_tokens(items: list[dict[str, Any]]) -> int:
    """按供应商分词量纲粗估输入项 token 数（兼容 DB 重建项与 SDK wire-format 项）。"""
    total = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        for key in ("content", "summary", "arguments", "output"):
            total += _provider_content_tokens(item.get(key))
        total += 4
    return total


def estimate_tools_schema_tokens(tools: Any | None = None) -> int:
    """按供应商分词量纲估算暴露给模型的工具 schema 固定 token 开销（冷库回放无实测读数时补足固定前缀）。"""
    if tools is None:
        from agent.tools.registry import get_exposed_tools

        tools = get_exposed_tools()
    if not tools:
        return 0
    serialized = json.dumps(tools, ensure_ascii=False, separators=(",", ":"))
    return estimate_provider_text_tokens(serialized)


def effective_history_provider_tokens(
    history: LoopHistory,
    items: list[dict[str, Any]] | None = None,
) -> int:
    """评估当前输入项对应的供应商 token 量级。

    对未改动的原始历史取 ``max(last_prompt_tokens, 同量纲估算)``（热缓存命中时使用实测值，
    冷库回放时 ``last_prompt_tokens`` 为 0，自然回落到实际重建项的同量纲估算）；
    对已折叠或裁剪后的副本直接取该副本的同量纲估算。
    """
    target_items = history.items if items is None else items
    if not target_items:
        return 0
    estimated = estimate_provider_items_tokens(target_items)
    if items is not None and target_items is not history.items:
        return estimated
    return max(history.last_prompt_tokens, estimated)


def bound_history_for_provider(
    history: LoopHistory,
    items: list[dict[str, Any]],
    max_provider_tokens: int,
) -> list[dict[str, Any]]:
    """仅当供应商实测或同量纲估算超过硬上限时，按供应商 token 比例滑窗丢弃最旧项。

    常态由轮间压缩控制上下文长度，绝不在真实窗口以下因 cl100k_base 虚高而提前砍旧消息；
    当存量老会话未折叠历史或压缩连续失败后的体积逼近供应商 1M 窗口时，作为最后防线降级。
    """
    if len(items) <= 1:
        return list(items)
    estimated = estimate_provider_items_tokens(items)
    provider_tokens = (
        estimated if items is not history.items else max(history.last_prompt_tokens, estimated)
    )
    if provider_tokens <= max_provider_tokens or estimated <= 0:
        return list(items)
    target_tokens = max(1, int(estimated * (max_provider_tokens / provider_tokens)))
    return bounded_items(items, target_tokens)


def _parse_json(value: Any) -> Any:
    if isinstance(value, str) and value:
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return None
    return value


def parse_tool_json(value: Any) -> Any:
    """解析工具输出 JSON 字符串；解析失败返回 None。"""
    return _parse_json(value)


def row_metadata(row: Any) -> dict[str, Any]:
    payload = _parse_json(getattr(row, "message_metadata", None))
    return payload if isinstance(payload, dict) else {}


def is_tool_archive_compaction(record: Any) -> bool:
    """记录是否为 Stage 1 零 LLM ``tool_archive`` 压缩记录。"""
    return isinstance(record, dict) and record.get("mode") == COMPACTION_MODE_TOOL_ARCHIVE


def valid_tool_archive_compaction(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """assistant / user 行上可用的 Stage 1 ``tool_archive`` 压缩记录。"""
    compaction = metadata.get("compaction")
    if not isinstance(compaction, dict):
        return None
    if compaction.get("mode") != COMPACTION_MODE_TOOL_ARCHIVE:
        return None
    archived_through = compaction.get("archived_through_message_id")
    call_ids = compaction.get("archived_call_ids")
    if (
        not isinstance(archived_through, str)
        or not archived_through
        or not isinstance(call_ids, list)
        or not call_ids
        or not all(isinstance(cid, str) and cid for cid in call_ids)
    ):
        return None
    return compaction


def is_successful_archivable_payload(parsed: Any) -> bool:
    """判定已解析的工具结果 JSON 是否为可安全归档的成功只读载荷（绝不归档错误或已归档结果）。"""
    if not isinstance(parsed, dict):
        return False
    if parsed.get("archived") is True:
        return False
    status = parsed.get("status")
    if not isinstance(status, str) or status.strip().lower() != "success":
        return False
    if parsed.get("error") or parsed.get("error_type"):
        return False
    data = parsed.get("data")
    if isinstance(data, dict):
        nested_status = data.get("status")
        if isinstance(nested_status, str) and nested_status.strip().lower() in {
            "error",
            "failed",
            "failure",
            "unknown",
        }:
            return False
        if data.get("error") or data.get("error_type"):
            return False
    return True


def extract_archived_file_refs(parsed: Any) -> list[dict[str, str]]:
    """从 ``query_files``（或常见文件结果结构）提取紧凑确定性的 ``{id, title}`` 列表。"""
    refs: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _visit(node: Any, depth: int = 0) -> None:
        if depth > 4 or len(refs) >= _MAX_ARCHIVE_FILE_REFS:
            return
        if isinstance(node, list):
            for elem in node:
                _visit(elem, depth + 1)
        elif isinstance(node, dict):
            fid = node.get("id") or node.get("file_id")
            title = node.get("title")
            fid_str = fid.strip() if isinstance(fid, str) and fid.strip() else ""
            title_str = title.strip() if isinstance(title, str) and title.strip() else ""
            if fid_str or title_str:
                key = (fid_str, title_str)
                if key not in seen:
                    seen.add(key)
                    entry: dict[str, str] = {}
                    if fid_str:
                        entry["id"] = fid_str
                    if title_str:
                        entry["title"] = title_str
                    refs.append(entry)
            for container_key in ("data", "files", "items", "results"):
                nested = node.get(container_key)
                if isinstance(nested, (list, dict)):
                    _visit(nested, depth + 1)

    _visit(parsed)
    return refs


def build_tool_archive_receipt(
    tool_name: str,
    output_str: str,
    parsed: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """构造紧凑、确定性的只读工具归档回执 JSON，返回 ``(receipt_json, file_ids)``。

    ``compaction.py`` 与 ``history.py`` 共用本函数：给定同一 ``(tool_name, output_str)`` 时，
    实时 Stage 1 归档与冷库回放输出逐字节一致。
    """
    payload_obj = parsed if isinstance(parsed, dict) else _parse_json(output_str)
    receipt: dict[str, Any] = {
        "status": "success",
        "archived": True,
        "tool_name": tool_name,
    }
    file_ids: list[str] = []
    if tool_name in _FILE_QUERY_TOOL_NAMES:
        refs = extract_archived_file_refs(payload_obj)
        if refs:
            receipt["files"] = refs
            if tool_name in _FILE_READ_TOOL_NAMES:
                file_ids = [ref["id"] for ref in refs if ref.get("id")]
    elif tool_name in ("load_skill", "read_skill_resource"):
        data = payload_obj.get("data") if isinstance(payload_obj, dict) else None
        if isinstance(data, dict):
            skill_id = data.get("skill_id")
            skill_name = data.get("skill_name")
            path = data.get("path")
            if isinstance(skill_id, str) and skill_id.strip():
                receipt["skill_id"] = skill_id.strip()
            if isinstance(skill_name, str) and skill_name.strip():
                receipt["skill_name"] = skill_name.strip()
            if tool_name == "read_skill_resource" and isinstance(path, str) and path.strip():
                receipt["path"] = path.strip()
    receipt["original_chars"] = len(output_str)
    receipt["note"] = _TOOL_ARCHIVE_NOTES.get(
        tool_name,
        "该只读工具结果已在历史归档中折叠以节省上下文；如需原文请重新调用该工具读取。",
    )
    return json.dumps(receipt, ensure_ascii=False, separators=(",", ":")), file_ids


def valid_compaction(metadata: dict[str, Any]) -> dict[str, Any] | None:
    """assistant 行上可用的 LLM 摘要压缩点：摘要非空、不含泄漏工具协议、覆盖到的行 id 非空。"""
    compaction = metadata.get("compaction")
    if not isinstance(compaction, dict):
        return None
    summary = compaction.get("summary")
    covers = compaction.get("covers_through_message_id")
    if (
        not isinstance(summary, str)
        or not summary.strip()
        or contains_leaked_tool_protocol(summary)
        or not isinstance(covers, str)
        or not covers
    ):
        return None
    return compaction


def stored_kept_messages(compaction: dict[str, Any]) -> list[str] | None:
    """压缩点记下的作者原话（压缩那一轮实际发出的文字）；没有或格式不对时为 None，按行重算。"""
    kept = compaction.get("kept_author_messages")
    if not isinstance(kept, list) or not all(isinstance(content, str) for content in kept):
        return None
    return kept


_CLIENT_QUERY_FILE_ITEM_KEYS = (
    "id",
    "title",
    "file_type",
    "parent_id",
    "order",
    "word_count",
    "content_length",
    "content_truncated",
    "match_count",
    "status",
    "error",
    "error_type",
    "user_message",
)
_CLIENT_HEAVY_RESULT_KEYS = frozenset(
    {
        "content",
        "content_preview",
        "matches",
        "author_edits",
        "file_metadata",
        "content_range",
        "downgrade_note",
    }
)
_CLIENT_PARALLEL_HEAVY_RESULT_KEYS = _CLIENT_HEAVY_RESULT_KEYS | frozenset(
    {"original_content", "new_content", "instructions"}
)


def _slim_query_file_item(item: Any) -> tuple[Any, bool]:
    if not isinstance(item, dict):
        return item, False
    slimmed = {key: item[key] for key in _CLIENT_QUERY_FILE_ITEM_KEYS if key in item}
    if len(slimmed) == len(item):
        return item, False
    return slimmed, True


def _slim_query_files_payload(data: Any) -> tuple[Any, bool]:
    if isinstance(data, list):
        changed = False
        projected: list[Any] = []
        for item in data:
            slim_item, item_changed = _slim_query_file_item(item)
            projected.append(slim_item)
            changed = changed or item_changed
        return (projected, True) if changed else (data, False)

    if isinstance(data, dict):
        changed = False
        projected_dict: dict[str, Any] = {}
        for key, value in data.items():
            if key in _CLIENT_HEAVY_RESULT_KEYS:
                changed = True
                continue
            if key in ("data", "items", "files") and (
                isinstance(value, list) or (key == "data" and isinstance(value, dict))
            ):
                slim_nested, nested_changed = _slim_query_files_payload(value)
                projected_dict[key] = slim_nested
                changed = changed or nested_changed
            else:
                projected_dict[key] = value
        return (projected_dict, True) if changed else (data, False)

    return data, False


def _slim_parallel_task_result(task_type: Any, result: Any) -> tuple[Any, bool]:
    if task_type == "query_files" or isinstance(result, list):
        return _slim_query_files_payload(result)
    if not isinstance(result, dict):
        return result, False

    changed = False
    projected: dict[str, Any] = {}
    for key, value in result.items():
        if key in _CLIENT_PARALLEL_HEAVY_RESULT_KEYS:
            changed = True
            continue
        if key in ("data", "items", "files") and isinstance(value, list):
            slim_nested, nested_changed = _slim_query_files_payload(value)
            projected[key] = slim_nested
            changed = changed or nested_changed
        elif key == "data" and isinstance(value, dict):
            slim_nested, nested_changed = _slim_parallel_task_result(task_type, value)
            projected[key] = slim_nested
            changed = changed or nested_changed
        else:
            projected[key] = value
    return (projected, True) if changed else (result, False)


def _slim_parallel_execute_payload(data: Any) -> tuple[Any, bool]:
    if not isinstance(data, dict):
        return data, False

    changed = False
    projected: dict[str, Any] = {}
    for key, value in data.items():
        if key == "tasks" and isinstance(value, list):
            tasks_changed = False
            slim_tasks: list[Any] = []
            for task in value:
                if isinstance(task, dict) and "result" in task:
                    slim_res, res_changed = _slim_parallel_task_result(task.get("type"), task["result"])
                    if res_changed:
                        slim_task = dict(task)
                        slim_task["result"] = slim_res
                        slim_tasks.append(slim_task)
                        tasks_changed = True
                    else:
                        slim_tasks.append(task)
                else:
                    slim_tasks.append(task)
            projected[key] = slim_tasks if tasks_changed else value
            changed = changed or tasks_changed
        elif key == "data" and isinstance(value, dict) and "tasks" in value:
            slim_nested, nested_changed = _slim_parallel_execute_payload(value)
            projected[key] = slim_nested
            changed = changed or nested_changed
        else:
            projected[key] = value
    return (projected, True) if changed else (data, False)


def slim_client_tool_result(tool_name: str, data: Any) -> Any:
    """发给浏览器的工具结果瘦身：去掉 read_files / search_files / query_files / parallel_execute 的大段正文与预览。

    只影响发往前端的 SSE 帧与聊天历史 HTTP 响应，不改动落库内容与模型历史回放；
    edit_file / write_chapter / delete_file / create_file / update_project 等其余工具原样保留。
    """
    if tool_name in _FILE_QUERY_TOOL_NAMES:
        slimmed, changed = _slim_query_files_payload(data)
        return slimmed if changed else data
    if tool_name == "parallel_execute":
        slimmed, changed = _slim_parallel_execute_payload(data)
        return slimmed if changed else data
    return data


def client_tool_calls(raw: str | None) -> str | None:
    """发给浏览器的 tool_calls：去掉 read_files / search_files / query_files 与 parallel_execute 中前端不展示的正文载荷。

    未命中目标工具或无需瘦身时原样返回同一个字符串，不改写数据库记录。
    """
    if not raw or (
        '"read_files"' not in raw
        and '"search_files"' not in raw
        and '"query_files"' not in raw
        and '"parallel_execute"' not in raw
    ):
        return raw
    calls = _parse_json(raw)
    if not isinstance(calls, list):
        return raw

    changed = False
    projected: list[Any] = []
    for call in calls:
        if (
            isinstance(call, dict)
            and call.get("name") in (*_FILE_QUERY_TOOL_NAMES, "parallel_execute")
            and "result" in call
        ):
            original_result = call["result"]
            slim_result = slim_client_tool_result(str(call["name"]), original_result)
            if slim_result is not original_result:
                slim_call = dict(call)
                slim_call["result"] = slim_result
                projected.append(slim_call)
                changed = True
                continue
        projected.append(call)

    if not changed:
        return raw
    return json.dumps(projected, ensure_ascii=False)


def client_message_metadata(raw: str | None) -> str | None:
    """发给浏览器的 message_metadata：
    - LLM 摘要压缩点：去掉只给模型回放的作者原话（``kept_author_messages``，可达上万 token，前端不用）；
    - Stage 1 ``tool_archive`` 压缩记录：仅用于服务端跨轮回放，无前端展示摘要，移除 ``compaction`` 键；
    其余行原样返回同一个字符串。
    """
    if not raw or (
        '"kept_author_messages"' not in raw
        and f'"{COMPACTION_MODE_TOOL_ARCHIVE}"' not in raw
    ):
        return raw
    payload = _parse_json(raw)
    compaction = payload.get("compaction") if isinstance(payload, dict) else None
    if not isinstance(compaction, dict):
        return raw
    if compaction.get("mode") == COMPACTION_MODE_TOOL_ARCHIVE:
        cleaned = {key: value for key, value in payload.items() if key != "compaction"}
        return json.dumps(cleaned, ensure_ascii=False)
    if "kept_author_messages" not in compaction:
        return raw
    payload["compaction"] = {key: value for key, value in compaction.items() if key != "kept_author_messages"}
    return json.dumps(payload, ensure_ascii=False)


def summary_message(summary: str) -> dict[str, Any]:
    return {"role": "user", "content": f"{SUMMARY_PREFIX}{summary.strip()}"}


def _tool_output_text(
    call: dict[str, Any],
    *,
    skill_rehydrator: _SkillRehydrator | None = None,
) -> str:
    """持久化的工具结果（前端卡片口径）还原成交给模型的结果文字。"""
    status = str(call.get("status") or "")
    name = str(call.get("name") or "")
    result = call.get("result")
    error = call.get("error")
    if status == "pending" and result is None and error is None:
        return json.dumps(
            {"status": "unknown", "note": "这次调用的结果没有记录下来"},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    rehydrated_skill = False
    if name == "edit_file" and isinstance(result, dict):
        from agent.tools.file_ops.edit import slim_edit_file_result_for_llm

        result = slim_edit_file_result_for_llm(result)
    elif name == "batch_replace_files" and isinstance(result, dict):
        from agent.tools.file_ops.edit import slim_batch_replace_result_for_llm

        result = slim_batch_replace_result_for_llm(result)
    elif name in ("create_file", "write_chapter") and status == "success" and isinstance(result, dict):
        replaced = name == "write_chapter" and result.get("action") == "replaced"
        streamed = result.get("awaiting_body") is True or result.get("body_in_reply") is True
        saved = result.get("body_saved") is True
        has_receipt = "body_saved" in result
        result = {key: value for key, value in result.items() if key not in _CREATE_FILE_TRANSIENT_KEYS}
        # 历史回执只说明过去的保存事实；捕获目标与版本绑定只在原请求里有效。
        result["write_target_active"] = False
        if replaced:
            # 整章替换只有落库后补发的结果才带 body_saved；只有定位结果说明新正文没有存下。
            result["body_saved"] = saved
            if not saved:
                result["result"] = _REPLACE_NOT_SAVED_NOTE
        elif streamed:
            # 新建的正文回执由 service 落库时按保存回执写进卡片（_stamp_body_receipts）；
            # 修复前写下的行没有这个键，沿用原来的推断。
            result["body_saved"] = saved if has_receipt else True
            if has_receipt and not saved:
                result["result"] = _CREATE_NOT_SAVED_NOTE
    elif (
        skill_rehydrator is not None
        and (status or "success") == "success"
        and name in _SKILL_CONTENT_NOT_KEPT
        and isinstance(result, dict)
    ):
        hydrated = skill_rehydrator(name, result, call.get("arguments"))
        if hydrated is not None:
            result = hydrated
            rehydrated_skill = True
    payload: dict[str, Any] = {"status": status or "success"}
    if result is not None:
        payload["data"] = result
    if error:
        payload["error"] = error
    if status == "error":
        payload["note"] = _FAILED_CALL_NOTE
    elif name in _SKILL_CONTENT_NOT_KEPT and not rehydrated_skill:
        payload["note"] = _SKILL_CONTENT_NOT_KEPT[name]
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def query_result_full_file_ids(result: Any, *, include_slimmed_sha256: bool = False) -> list[str]:
    """提取非归档 ``query_files`` 结果里实际携带完整非空 ``content`` 的非文件夹文件 id。

    严格排除范围读取（``content_partial=True``）、超长截断（``content_truncated=True``）
    与文件夹条目，口径与 ``SessionLoader.session_read_snapshots`` 保持一致。
    ``include_slimmed_sha256=True`` 时同时识别落库瘦身保留了 ``content_sha256`` 的全文读结果。
    """
    found: list[str] = []

    def _visit(node: Any, depth: int = 0) -> None:
        if depth > 4:
            return
        if isinstance(node, list):
            for elem in node:
                _visit(elem, depth + 1)
        elif isinstance(node, dict):
            fid = node.get("id") or node.get("file_id")
            content = node.get("content")
            sha = (node.get("content_sha256") or node.get("raw_content_sha256")) if include_slimmed_sha256 else None
            has_full_body = (isinstance(content, str) and bool(content)) or (
                isinstance(sha, str) and bool(sha.strip())
            )
            if (
                isinstance(fid, str)
                and fid.strip()
                and has_full_body
                and not node.get("content_truncated")
                and not node.get("content_partial")
                and node.get("file_type") != "folder"
            ):
                found.append(fid.strip())
            for container_key in ("data", "files", "items", "results"):
                nested = node.get(container_key)
                if isinstance(nested, (list, dict)):
                    _visit(nested, depth + 1)

    _visit(result)
    return found


def _is_persist_slimmed_query_payload(payload: Any, *, depth: int = 0) -> bool:
    """判定 ``query_files`` 结果是否已在落库期被瘦身（存在 ``archived_note`` 且无完整正文 ``content``）。"""
    if depth > 4:
        return False
    if isinstance(payload, list):
        return any(_is_persist_slimmed_query_payload(elem, depth=depth + 1) for elem in payload)
    if isinstance(payload, dict):
        has_note = isinstance(payload.get("archived_note"), str) and bool(payload["archived_note"].strip())
        has_content = isinstance(payload.get("content"), str) and bool(payload["content"])
        if has_note and not has_content:
            return True
        for container_key in ("data", "files", "items", "results"):
            nested = payload.get(container_key)
            if isinstance(nested, (list, dict)) and _is_persist_slimmed_query_payload(nested, depth=depth + 1):
                return True
    return False


def _loop_row_items(
    row: Any,
    metadata: dict[str, Any],
    *,
    archived_call_ids: set[str] | None = None,
    archived_file_ids: set[str] | None = None,
    archive_matcher: Any | None = None,
    skill_rehydrator: _SkillRehydrator | None = None,
) -> list[dict[str, Any]]:
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
        orig_call_id = str(call.get("id") or "").strip()
        tool_name = str(call.get("name") or "")
        arguments = call.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False, separators=(",", ":"))
        if archive_matcher is not None:
            should_archive = archive_matcher(call_id, orig_call_id, tool_name)
        else:
            should_archive = bool(
                archived_call_ids
                and tool_name in ARCHIVABLE_READ_TOOLS
                and (call_id in archived_call_ids or (orig_call_id and orig_call_id in archived_call_ids))
            )
        # 已归档的技能调用跳过冷回放水合，避免无谓查询 DB 后再被归档丢弃。
        output_text = _tool_output_text(
            call,
            skill_rehydrator=None if should_archive else skill_rehydrator,
        )
        was_archived = False
        if should_archive:
            parsed_output = _parse_json(output_text)
            if is_successful_archivable_payload(parsed_output):
                receipt_text, fids = build_tool_archive_receipt(tool_name, output_text, parsed_output)
                already_persist_slimmed = (
                    tool_name in _FILE_READ_TOOL_NAMES and _is_persist_slimmed_query_payload(parsed_output)
                )
                if len(receipt_text) < len(output_text):
                    output_text = receipt_text
                    was_archived = True
                    if archived_file_ids is not None:
                        archived_file_ids.update(fids)
                elif already_persist_slimmed:
                    # 落库期已被瘦身为 ~220 字符 archived_note 的条目，即使回执长度未更短，
                    # 仍必须无条件登记 archived_file_ids 以撤销跨轮快照结转。
                    was_archived = True
                    if archived_file_ids is not None:
                        archived_file_ids.update(fids)
                elif tool_name in _SKILL_CONTENT_NOT_KEPT:
                    # 落库的技能工具卡片不含正文且已跳过水合，统一转为归档回执。
                    output_text = receipt_text
                    was_archived = True
        if not was_archived and archived_file_ids is not None and tool_name in _FILE_READ_TOOL_NAMES:
            # 只有后续非归档的 read_files / query_files 再次返回了完整、未截断、非范围读取的正文，该文件才重新进入模型视野；
            # edit_file / search_files / summary / partial / truncated 查询不提供全文，不能清除已归档标记。
            status = str(call.get("status") or "").strip().lower()
            if status == "success":
                for fid in query_result_full_file_ids(call.get("result")):
                    archived_file_ids.discard(fid)
        items.append({
            "type": "function_call",
            "call_id": call_id,
            "name": tool_name,
            "arguments": arguments,
        })
        items.append({"type": "function_call_output", "call_id": call_id, "output": output_text})

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


def _legacy_row_items(row: Any) -> list[dict[str, Any]]:
    """旧版非单循环助手行的精简纯文本回放兜底。"""
    content = str(getattr(row, "content", "") or "").strip()
    return [{"role": "assistant", "content": content}] if content else []


# 这些 stop_reason 已有「这一轮没做完」的说明，同一原因的工具轮数卡（layer=tool_call）不再重复。
_NOTED_TOOL_CALL_STOPS = frozenset({"max_turns_exceeded", "run_deadline_exceeded", "model_call_budget_exhausted"})


def _status_cards_to_replay(status_cards: list[Any], stop_reason: Any, *, has_text: bool) -> list[Any]:
    if not has_text or stop_reason not in _NOTED_TOOL_CALL_STOPS:
        return status_cards
    return [
        card for card in status_cards
        if not (isinstance(card, dict) and card.get("type") == "iteration_exhausted" and card.get("layer") == "tool_call")
    ]


def _row_items(
    loader: Any,
    row: Any,
    *,
    archived_call_ids: set[str] | None = None,
    archived_file_ids: set[str] | None = None,
    archive_matcher: Any | None = None,
    skill_rehydrator: _SkillRehydrator | None = None,
) -> list[dict[str, Any]]:
    content = str(getattr(row, "content", "") or "")
    if row.role == "user":
        return [{"role": "user", "content": content}] if content.strip() else []
    metadata = row_metadata(row)
    if metadata.get("engine") != LOOP_ENGINE_METADATA_VALUE:
        return _legacy_row_items(row)
    items = _loop_row_items(
        row,
        metadata,
        archived_call_ids=archived_call_ids,
        archived_file_ids=archived_file_ids,
        archive_matcher=archive_matcher,
        skill_rehydrator=skill_rehydrator,
    )
    stop_reason = metadata.get("stop_reason")
    status_cards = metadata.get("status_cards")
    if isinstance(status_cards, list):
        # 程序确认的未完成状态（轮数耗尽、写成文字没执行的工具调用……）不管这一轮有没有文字都进历史，
        # 模型「继续」时才分得清做完和没做完的；已有停止说明的同一原因不重复。
        has_text = any(item.get("role") == "assistant" for item in items)
        summary = loader._build_status_cards_history_content(
            _status_cards_to_replay(status_cards, stop_reason, has_text=has_text)
        )
        if summary:
            items.append({"role": "assistant", "content": summary})
    if stop_reason == "output_truncated":
        from agent.core.output_truncation import OUTPUT_TRUNCATED_HISTORY

        items.append({"role": "assistant", "content": OUTPUT_TRUNCATED_HISTORY})
    elif isinstance(stop_reason, str) and stop_reason in _INTERRUPTED_ROUND_NOTES:
        items.append({"role": "assistant", "content": _INTERRUPTED_ROUND_NOTES[stop_reason]})
    return items


def _recent_skill_replay_row_ids(
    after_rows: list[Any],
    *,
    recent_turns: int = SKILL_REPLAY_RECENT_TURNS,
) -> set[Any]:
    """选出最近 ``recent_turns`` 个用户轮次内的 assistant 行 id（``after_rows`` 按新→旧排列）。

    同一轮内由 steering 产生的连续多条 user 行合并计作 1 个用户轮次。
    """
    if recent_turns <= 0:
        return set()
    eligible: set[Any] = set()
    user_turns_seen = 0
    prev_was_user = False
    for row in after_rows:
        if row.role == "user":
            if not prev_was_user:
                user_turns_seen += 1
                prev_was_user = True
            continue
        prev_was_user = False
        if row.role == "assistant":
            if user_turns_seen >= recent_turns:
                break
            eligible.add(row.id)
    return eligible


def kept_author_messages(author_messages: list[str], budget_tokens: int) -> list[dict[str, Any]]:
    """压缩点之前最近的作者原话（按时间先后传入）：按供应商同量纲 token 预算原样保留，超出的那条截断后停下。"""
    kept: list[dict[str, Any]] = []
    remaining = budget_tokens
    for content in reversed(author_messages):
        if not content.strip():
            continue
        tokens = estimate_provider_text_tokens(content)
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


def _fetch_message_page_desc(
    session: Session,
    model: Any,
    *,
    chat_session_id: str,
    cursor: tuple[Any, Any] | None,
) -> list[Any]:
    # 显式 6 列投影（排除未用的 reasoning_content 列，避免 PostgreSQL TOAST 解压开销）。
    stmt = select(
        model.id,
        model.role,
        model.content,
        model.tool_calls,
        model.message_metadata,
        model.created_at,
    ).where(
        and_(
            model.session_id == chat_session_id,
            model.role.in_(("user", "assistant")),
        )
    )
    if cursor is not None:
        created_at, row_id = cursor
        stmt = stmt.where(
            or_(
                model.created_at < created_at,
                and_(model.created_at == created_at, model.id < row_id),
            )
        )
    return list(
        session.exec(
            stmt.order_by(desc(model.created_at), desc(model.id)).limit(_PAGE_SIZE)
        ).all()
    )


def _fetch_cold_watermark(
    session: Session,
    *,
    chat_session_id: str,
) -> tuple[Any, Any] | None:
    """Index-only probe on ``(session_id, created_at, id)`` for the newest archived row.

    Omits the ``role`` filter so PostgreSQL can satisfy this with a 1-row Index Only Scan
    on ``ix_chat_message_archive_session_created_at_id`` without heap or TOAST fetches.
    """
    from models import ChatMessageArchive

    row = session.exec(
        select(ChatMessageArchive.created_at, ChatMessageArchive.id)
        .where(ChatMessageArchive.session_id == chat_session_id)
        .order_by(desc(ChatMessageArchive.created_at), desc(ChatMessageArchive.id))
        .limit(1)
    ).first()
    if row is None:
        return None
    return (row[0], row[1])


def _iter_session_messages_desc(
    session: Session,
    *,
    chat_session_id: str,
):
    """Yield session user/assistant rows in strict descending ``(created_at, id)`` order.

    Performs a lazy 2-way descending merge across hot ``ChatMessage`` and cold
    ``ChatMessageArchive`` (when present), deduplicating by ``id`` (preferring hot).

    Cold full-row queries are deferred behind a 1-row index-only watermark check on
    ``(session_id, created_at, id)``: hot-only sessions and active multi-week sessions
    that stop at a hot compaction anchor strictly newer than the cold watermark never
    execute ``SELECT * FROM chat_message_archive`` (zero cold TOAST detoasting), while
    chronological order remains exact even if ``FOR UPDATE SKIP LOCKED`` transiently
    leaves hot and cold rows interleaved.
    """
    from models import ChatMessage, ChatMessageArchive
    from models.chat_message_archive import has_chat_message_archive_table

    hot_buf: list[Any] = []
    hot_idx = 0
    hot_cursor: tuple[Any, Any] | None = None
    hot_exhausted = False
    hot_pages_fetched = 0

    has_archive_table = has_chat_message_archive_table(session)
    cold_buf: list[Any] = []
    cold_idx = 0
    cold_cursor: tuple[Any, Any] | None = None
    cold_exhausted = not has_archive_table
    cold_watermark: tuple[Any, Any] | None = None
    cold_watermark_checked = False
    cold_active = False
    cold_reprobed = False

    seen_ids: set[str] = set()

    def _peek_hot() -> Any | None:
        nonlocal hot_buf, hot_idx, hot_cursor, hot_exhausted, hot_pages_fetched
        if hot_idx >= len(hot_buf) and not hot_exhausted:
            hot_pages_fetched += 1
            hot_buf = _fetch_message_page_desc(
                session, ChatMessage, chat_session_id=chat_session_id, cursor=hot_cursor
            )
            hot_idx = 0
            if hot_buf:
                hot_cursor = (hot_buf[-1].created_at, hot_buf[-1].id)
            if len(hot_buf) < _PAGE_SIZE:
                hot_exhausted = True
        return hot_buf[hot_idx] if hot_idx < len(hot_buf) else None

    def _ensure_cold_watermark() -> tuple[Any, Any] | None:
        nonlocal cold_watermark, cold_watermark_checked, cold_exhausted
        if not cold_watermark_checked:
            cold_watermark_checked = True
            if cold_exhausted:
                return None
            cold_watermark = _fetch_cold_watermark(
                session, chat_session_id=chat_session_id
            )
            if cold_watermark is None:
                cold_exhausted = True
        return cold_watermark

    def _peek_cold() -> Any | None:
        nonlocal cold_buf, cold_idx, cold_cursor, cold_exhausted
        if cold_idx >= len(cold_buf) and not cold_exhausted:
            cold_buf = _fetch_message_page_desc(
                session, ChatMessageArchive, chat_session_id=chat_session_id, cursor=cold_cursor
            )
            cold_idx = 0
            if cold_buf:
                cold_cursor = (cold_buf[-1].created_at, cold_buf[-1].id)
            if len(cold_buf) < _PAGE_SIZE:
                cold_exhausted = True
        return cold_buf[cold_idx] if cold_idx < len(cold_buf) else None

    while True:
        hot_row = _peek_hot()
        if cold_active:
            cold_row = _peek_cold()
        elif cold_exhausted:
            cold_row = None
        else:
            watermark = _ensure_cold_watermark()
            if watermark is None:
                cold_row = None
            elif hot_row is None or (hot_row.created_at, hot_row.id) < watermark:
                cold_active = True
                cold_row = _peek_cold()
            else:
                # hot_row >= newest cold row: strictly newer (or identical id, hot wins),
                # so defer full-row cold fetching until hot exhausts or reaches the watermark.
                cold_row = None

        if (
            hot_row is None
            and cold_row is None
            and has_archive_table
            and cold_watermark_checked
            and cold_watermark is None
            and not cold_reprobed
            and hot_pages_fetched > 1
        ):
            # Re-probe cold once after multi-page hot exhaustion to catch rows archived mid-iteration.
            cold_reprobed = True
            reprobed = _fetch_cold_watermark(session, chat_session_id=chat_session_id)
            if reprobed is not None:
                cold_watermark = reprobed
                cold_exhausted = False
                cold_active = True
                cold_row = _peek_cold()

        if hot_row is None and cold_row is None:
            break
        if cold_row is None:
            chosen = hot_row
            hot_idx += 1
        elif hot_row is None:
            chosen = cold_row
            cold_idx += 1
        elif (hot_row.created_at, hot_row.id) >= (cold_row.created_at, cold_row.id):
            chosen = hot_row
            hot_idx += 1
        else:
            chosen = cold_row
            cold_idx += 1

        if chosen.id in seen_ids:
            continue
        seen_ids.add(chosen.id)
        yield chosen


def load_loop_history(
    session: Session,
    *,
    project_id: str,
    user_id: str | None,
    chat_session_id: str,
    keep_user_tokens: int | None = None,
    carried_metadata: dict[str, Any] | None = None,
) -> LoopHistory:
    """按会话重建单循环 agent 的历史（调用方已核对会话归属）。

    由新到旧分页读：遇到最近的压缩点后读到它覆盖的那一行为止；压缩点没记保留的作者原话（旧记录）时
    再往前只取作者原话直到用完预算；没有压缩点时读完整个会话。

    carried_metadata：重试时删掉的那条没得到回复的作者消息上的压缩记录，当作比会话里所有行都新的一行读，
    重试不再重付一次摘要。
    """
    from agent.core.session_loader import SessionLoader

    if keep_user_tokens is None:
        keep_user_tokens = AGENT_LOOP_COMPACT_KEEP_USER_TOKENS
    loader = SessionLoader(project_id, user_id)
    result = LoopHistory()
    after_rows: list[Any] = []  # 新 → 旧
    before_rows: list[Any] = []  # 压缩点覆盖范围内的行（新 → 旧），只取作者原话
    compaction: dict[str, Any] | None = None
    stored_kept: list[str] | None = None
    archived_call_ids: set[str] = set()
    # 每条 tool_archive 记录按其 archived_through_message_id 限定作用范围，避免未来轮次同名 call_id 误伤。
    # 元素为 (record_row_id_or_None_if_carried, archived_through_message_id, call_ids_tuple, occ_map)。
    tool_archive_scopes: list[tuple[str | None, str, tuple[str, ...], dict[str, frozenset[int]]]] = []
    covered_reached = False
    kept_tokens_seen = 0
    skip_seen = False
    skip_found = False
    skip_row_id: str | None = None
    truncation_streak_open = True

    def note_compaction(metadata: dict[str, Any], *, row_id: str | None = None) -> None:
        nonlocal compaction, stored_kept, skip_seen, skip_found, skip_row_id
        if compaction is not None:
            return
        tool_archive = valid_tool_archive_compaction(metadata)
        if tool_archive is not None:
            call_tuple = tuple(tool_archive["archived_call_ids"])
            raw_occ = tool_archive.get("archived_call_occurrences")
            occ_map: dict[str, frozenset[int]] = {}
            if isinstance(raw_occ, dict):
                for cid, indices in raw_occ.items():
                    if isinstance(cid, str) and cid and isinstance(indices, list):
                        valid_indices = frozenset(i for i in indices if isinstance(i, int) and not isinstance(i, bool) and i >= 0)
                        if valid_indices:
                            occ_map[cid] = valid_indices
            archived_call_ids.update(call_tuple)
            tool_archive_scopes.append((row_id, str(tool_archive["archived_through_message_id"]), call_tuple, occ_map))
            # 成功的 Stage 1 归档压缩覆盖其之前的退避记录，但不会截断会话行，继续往前扫描。
            skip_seen = True
            return
        if not skip_seen:
            skipped = metadata.get("compaction_skipped")
            if isinstance(skipped, dict) and isinstance(skipped.get("at_prompt_tokens"), int):
                skip_seen = True
                skip_found = True
                skip_row_id = row_id
                result.compaction_skipped_at_tokens = max(0, skipped["at_prompt_tokens"])
                raw_reason = skipped.get("reason")
                result.compaction_skipped_reason = (
                    raw_reason if isinstance(raw_reason, str) and raw_reason else None
                )
        compaction = valid_compaction(metadata)
        if compaction is not None:
            stored_kept = stored_kept_messages(compaction)
            skip_seen = True

    if carried_metadata:
        note_compaction(carried_metadata, row_id=None)

    for row in _iter_session_messages_desc(session, chat_session_id=chat_session_id):
        if result.last_message_id is None:
            result.last_message_id = row.id
        if covered_reached:
            before_rows.append(row)
            if row.role == "user":
                kept_tokens_seen += estimate_provider_text_tokens(str(row.content or ""))
                if kept_tokens_seen >= keep_user_tokens:
                    break
            continue
        if compaction is not None and row.id == compaction["covers_through_message_id"]:
            covered_reached = True
            if stored_kept is not None:
                # 保留的作者原话已记在压缩点上，不必再往前读。
                break
            before_rows.append(row)
            if row.role == "user":
                kept_tokens_seen += estimate_provider_text_tokens(str(row.content or ""))
            continue
        after_rows.append(row)
        metadata = row_metadata(row)
        if row.role == "assistant" and truncation_streak_open:
            if metadata.get("stop_reason") == OUTPUT_TRUNCATED_REASON:
                result.trailing_output_truncations += 1
            else:
                truncation_streak_open = False
        # 冷库回放不继承 DB metadata["prompt_tokens"]（那是落库瘦身、去 <turn_context> 与去推理前的热内存读数），
        # 保持 result.last_prompt_tokens=0，由实际重建项 + 静态前缀估算决定水位。
        # 压缩点 / 没压缩成的记录通常在本轮 assistant 行上；这一轮没有 assistant 行时记在作者那条消息上。
        note_compaction(metadata, row_id=row.id)

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
    result.archived_call_ids = archived_call_ids
    chronological_rows = list(reversed(after_rows))
    row_positions = {row.id: pos for pos, row in enumerate(chronological_rows)}
    skip_cutoff_pos: int | None = None
    if skip_found:
        if skip_row_id is None:
            skip_cutoff_pos = len(chronological_rows)
        elif skip_row_id in row_positions:
            pos = row_positions[skip_row_id]
            if chronological_rows[pos].role == "assistant":
                while pos > 0 and chronological_rows[pos - 1].role == "user":
                    pos -= 1
            skip_cutoff_pos = pos
    # (cutoff_pos, call_set, remaining_orig_counts, occ_map)
    scoped_archives: list[tuple[int, frozenset[str], Counter[str], dict[str, frozenset[int]]]] = []
    for record_row_id, anchor_id, call_tuple, occ_map in tool_archive_scopes:
        anchor_pos = row_positions.get(anchor_id)
        if anchor_pos is not None:
            cutoff_pos = anchor_pos
        elif record_row_id is not None and record_row_id in row_positions:
            # 锚点行被删时仍严格限定在记录行之前，绝不向后误伤新轮次。
            cutoff_pos = row_positions[record_row_id] - 1
        elif record_row_id is None:
            cutoff_pos = len(chronological_rows) - 1
        else:
            continue
        if cutoff_pos >= 0:
            scoped_archives.append((cutoff_pos, frozenset(call_tuple), Counter(call_tuple), occ_map))

    # 跨行按时间序追踪每个 orig_call_id 的 0-based 出现序号，确保复用 call_id（如 call_0）在冷回放中
    # 精准命中被归档的那次调用，绝不误归档保护窗口内同名的后续读调用或写工具。
    orig_call_occurrence_counts: dict[str, int] = {}

    recent_skill_row_ids = _recent_skill_replay_row_ids(after_rows) if user_id else set()
    active_skills_by_id: dict[str, Any] | None = None

    def rehydrate_skill_call(
        tool_name: str,
        result_data: Any,
        call_arguments: Any,
    ) -> dict[str, Any] | None:
        nonlocal active_skills_by_id
        if not user_id:
            return None
        if active_skills_by_id is None:
            from agent.skills.active_skills import load_active_skills
            from agent.skills.replay import index_active_skills

            active_skills_by_id = index_active_skills(load_active_skills(session, user_id))
        from agent.skills.replay import rehydrate_skill_tool_result

        return rehydrate_skill_tool_result(
            active_skills_by_id,
            session,
            tool_name,
            result_data,
            call_arguments=call_arguments,
        )

    for pos, row in enumerate(chronological_rows):
        if skip_cutoff_pos is not None and pos == skip_cutoff_pos:
            result.compaction_skipped_cold_items_tokens = estimate_provider_items_tokens(items)
        active_scopes = [scope for scope in scoped_archives if pos <= scope[0]] if scoped_archives else []

        def _match_archive(
            call_id: str,
            orig_call_id: str,
            tool_name: str,
            *,
            _scopes: list[tuple[int, frozenset[str], Counter[str], dict[str, frozenset[int]]]] = active_scopes,
        ) -> bool:
            occ_idx: int | None = None
            if orig_call_id:
                occ_idx = orig_call_occurrence_counts.get(orig_call_id, 0)
                orig_call_occurrence_counts[orig_call_id] = occ_idx + 1
            if not _scopes or tool_name not in ARCHIVABLE_READ_TOOLS:
                return False
            matched = False
            for _cutoff, call_set, remaining_counts, occ_map in _scopes:
                if call_id in call_set:
                    matched = True
                elif orig_call_id:
                    if orig_call_id in occ_map:
                        if occ_idx is not None and occ_idx in occ_map[orig_call_id]:
                            matched = True
                    elif orig_call_id in call_set and remaining_counts[orig_call_id] > 0:
                        remaining_counts[orig_call_id] -= 1
                        matched = True
            return matched

        rehydrator = rehydrate_skill_call if row.id in recent_skill_row_ids else None
        items.extend(
            _row_items(
                loader,
                row,
                archived_file_ids=result.archived_file_ids,
                archive_matcher=_match_archive if scoped_archives else None,
                skill_rehydrator=rehydrator,
            )
        )
        if row.role == "user" and str(row.content or "").strip():
            result.author_messages.append(str(row.content))
    if skip_cutoff_pos is not None and skip_cutoff_pos == len(chronological_rows):
        result.compaction_skipped_cold_items_tokens = estimate_provider_items_tokens(items)
    result.items = items
    return result
