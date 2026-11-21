"""
Tool execution functions for zenstory Agent.

Defines tool functions that can be called by the writing-agent adapter.
"""

import asyncio
import copy
import json
import os
import re
from collections.abc import Callable, Sequence
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlmodel import Session, and_, select

from agent.constants import CONTENT_FILE_TYPES, coerce_bool
from agent.tools.file_ops.content_range import apply_content_range, wants_range
from agent.tools.file_ops.edit import (
    EDIT_ERROR_FILE_BUSY,
    EDIT_ERROR_FILE_NOT_FOUND,
    EDIT_ERROR_GENERIC,
    EDIT_ERROR_INVALID_EDIT,
    EDIT_ERROR_PERMISSION_DENIED,
    EditFileError,
    FileWriteBusyError,
    PreWriteHistoryUnavailableError,
    StaleContentError,
    edit_error_user_message,
    extract_edit_file_ui_payload,
    slim_edit_file_result_for_llm,
)
from agent.tools.file_ops.serialization import (
    QUERY_FILES_DEFAULT_LIMIT,
    QUERY_FILES_FULL_MODE_DEFAULT_LIMIT,
    QUERY_FILES_RESPONSE_MODE_FULL,
    WRITE_FILE_OMITTED_FIELDS,
    serialize_file,
)
from agent.tools.permissions import ForbiddenError, NotFoundError
from agent.tools.tool_context import (  # noqa: F401 - re-exported for backward compatibility
    SESSION_ISOLATION_KEY,
    ToolContext,
    _owned_session_var,
    _PendingEmptyFileState,
    _ReadSnapshots,
    _tool_context_var,
)
from config.project_notes import merge_project_notes
from services.file_tree_rules import has_sibling_title_conflict
from utils.logger import get_logger, log_with_context
from utils.title_sequence import (
    chapter_title_has_subtitle as _chapter_title_has_subtitle,
)
from utils.title_sequence import (
    extract_chapter_like_sequence_number,
    extract_chapter_sequence_in_text,
)
from utils.title_sequence import (
    prefixed_chapter_heading as _prefixed_chapter_heading,
)
from utils.title_sequence import (
    split_prefixed_chapter_title as _split_prefixed_chapter_title,
)

logger = get_logger(__name__)

DEFAULT_TOOL_RESULT_MAX_CHARS = 200_000
MIN_TOOL_RESULT_MAX_CHARS = 512

PROJECT_STATUS_FIELD_ALIASES: dict[str, str] = {
    "currentPhase": "current_phase",
    "writingStyle": "writing_style",
    "projectSummary": "summary",
}


def _run_sync_tool_with_owned_session_cleanup(
    sync_func: Callable[[dict[str, Any]], dict[str, Any]],
    args: dict[str, Any],
    *extra_args: Any,
) -> dict[str, Any]:
    """
    Run a sync tool helper and deterministically close any ToolContext-owned session.

    When PostgreSQL-mode tool calls are offloaded with ``asyncio.to_thread()``,
    ``ToolContext.get_session()`` may lazily create a session inside the worker
    thread. That worker context is separate from the main request context, so the
    normal ``ToolContext.clear_context()`` in the caller does not guarantee the
    thread-owned session is closed immediately. Close it here to avoid relying on
    GC timing for connection release.

    进线程后必须先把 ``_owned_session_var`` 清空：``asyncio.to_thread`` 拷贝的是
    调用方的 contextvars 快照，事件循环侧此前懒建的 Session 会被原样带进工作
    线程。若不清空，(1) 同一个 Session 会被事件循环线程与工作线程交替使用
    （SQLAlchemy Session 非线程安全），(2) 下面 finally 里的 ``_cleanup_owned_session``
    会把这个**属于事件循环上下文**的 Session 直接 close 掉，而事件循环那边的
    ContextVar 仍指向它（线程里的 set 对调用方不可见），后续调用拿到的是一个
    已关闭的 Session。清空后，本线程按需自建、用完即关，生命周期完全自洽。

    同理，``context["session"]``（调用方显式塞进来的**请求级** Session）也不能
    在工作线程里复用：``get_session()`` 的第一分支就是它，绕不过 _owned_session_var
    与 SESSION_ISOLATION_KEY。SDK 同一 turn 里的多个 tool_call 会并发成多个
    asyncio Task，各自 ``to_thread`` 出一个工作线程，一起在同一个 Session 上
    flush/commit/refresh。因此这里把上下文浅拷贝一份、抹掉 session 并打上隔离
    标记，让本线程按需自建。只有在拿得到 ``create_session_func`` 时才这么做——
    否则自建无从谈起，只能沿用调用方给的 session（测试里的典型用法）。
    """
    _owned_session_var.set(None)

    context = _tool_context_var.get()
    if (
        isinstance(context, dict)
        and context.get("session") is not None
        and context.get("create_session_func")
    ):
        isolated_context = dict(context)
        isolated_context["session"] = None
        isolated_context[SESSION_ISOLATION_KEY] = True
        # 这是工作线程私有的 contextvars 快照，set 不会影响事件循环侧。
        _tool_context_var.set(isolated_context)

    try:
        return sync_func(args, *extra_args)
    finally:
        ToolContext._cleanup_owned_session()


async def _offload_write_tool(
    tool_name: str, sync_func: Callable[[dict[str, Any]], dict[str, Any]], args: dict[str, Any]
) -> dict[str, Any]:
    """写工具在线程里执行；停止 / 断线 / 时限取消了等待时线程照常跑完，结果留给收尾结算。

    线程的 future 先登记进请求级的在途列表（``ToolContext.inflight_tool_writes``，属于
    ``RunOutcome``，比 ``clear_context()`` 活得长），正常返回或出错时移除；被取消时留着，
    由 service 的截停收尾等它的真实结果（``RunOutcome.settle_inflight_tool_writes``）。
    """
    future = asyncio.ensure_future(
        asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, sync_func, args)
    )
    # 取消之后没人再取结果时，先把异常取走（不留 "exception was never retrieved"）。
    future.add_done_callback(lambda done: done.cancelled() or done.exception())
    registry = ToolContext.inflight_tool_writes()
    entry = (tool_name, future)
    if registry is not None:
        registry.append(entry)
    try:
        result = await asyncio.shield(future)
    except asyncio.CancelledError:
        raise
    except BaseException:
        _discard_inflight(registry, entry)
        raise
    _discard_inflight(registry, entry)
    return result


def _discard_inflight(registry: list[tuple[str, Any]] | None, entry: tuple[str, Any]) -> None:
    if registry is not None and entry in registry:
        registry.remove(entry)


def _get_tool_result_max_chars() -> int:
    """Read tool-result max chars limit from env with safe fallback."""
    raw = os.getenv("AGENT_TOOL_RESULT_MAX_CHARS")
    if raw is None:
        return DEFAULT_TOOL_RESULT_MAX_CHARS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TOOL_RESULT_MAX_CHARS
    return value if value >= MIN_TOOL_RESULT_MAX_CHARS else DEFAULT_TOOL_RESULT_MAX_CHARS


TOOL_RESULT_MAX_CHARS = _get_tool_result_max_chars()

def _make_result(data: Any, *, tool_name: str | None = None) -> dict[str, Any]:
    """Create a tool result in MCP format."""
    return _make_mcp_payload(data, tool_name=tool_name)


def _elide_reused_file_content(result: Any) -> Any:
    """剧本分集幂等复用分支的 tool result 省略正文，只留长度元信息。

    复用分支返回的 content 是目标文件**已有的整集正文**（实测一集 8000 字）。
    它只服务于服务端判据（StreamAdapter 的截断覆盖保护），模型一个字都用不到；
    原样序列化进回给模型的 tool result 里，等于每次分集复用都白烧几千 token。

    这里只对复用分支下手，判据是 crud 复用分支才会置的 reused_existing。
    用户显式带 content 调 create_file 建新文件时回显 content 是既有行为，不受影响。

    显式字段 reused_existing / original_content_length 必须原样保留：
    StreamAdapter._resolve_original_content_length 优先读它们判定
    「目标文件原本非空」，不依赖 content 本身，所以省略正文不削弱覆盖保护。
    """
    if not isinstance(result, dict) or not result.get("reused_existing"):
        return result

    elided = dict(result)
    original_content = elided.get("content")
    if isinstance(original_content, str) and original_content:
        elided["content"] = ""
        elided["content_elided"] = True
        # crud 层正常会给出 original_content_length；这里兜底补齐，
        # 避免上游漏填时把「原本非空」丢成 0。
        if not isinstance(elided.get("original_content_length"), int):
            elided["original_content_length"] = len(original_content)
    return elided


def _make_error(error: str, *, tool_name: str | None = None) -> dict[str, Any]:
    """Create an error result in MCP format."""
    return _make_mcp_payload({"status": "error", "error": error}, tool_name=tool_name)


def _make_structured_error(
    error: str,
    *,
    error_type: str,
    tool_name: str,
    mutation_applied: bool = False,
    **fields: Any,
) -> dict[str, Any]:
    """Create a structured error result with ``error_type`` and ``mutation_applied`` in MCP format."""
    return _make_mcp_payload(
        {
            "status": "error",
            "error": error,
            "error_type": error_type,
            "mutation_applied": mutation_applied,
            **fields,
        },
        tool_name=tool_name,
    )


def _make_mcp_payload(payload: Any, *, tool_name: str | None = None) -> dict[str, Any]:  # noqa: ARG001
    """Create MCP payload with unified size guardrail.

    ``tool_name`` only labels the call site; the serialized result does not carry it.
    """
    text = _serialize_tool_payload(payload)
    return {
        "content": [{
            "type": "text",
            "text": text,
        }]
    }


def _normalize_payload_status(payload: Any) -> str:
    """Infer normalized status from tool payload."""
    status = "success"
    if isinstance(payload, dict):
        raw_status = payload.get("status")
        if isinstance(raw_status, str) and raw_status:
            status = raw_status
        elif "error" in payload:
            status = "error"
    return status


# 截断长字符串时永不改写的键：这些是模型定位文件、判断成败所需的结构化信号。
_BOUND_PROTECTED_KEYS: frozenset[str] = frozenset({
    "id",
    "file_id",
    "pending_file_id",
    "parent_id",
    "entity_id",
    "entity_type",
    "title",
    "file_type",
    "type",
    "status",
    "error",
    "error_type",
    "description",
    "execution_id",
    "note",
    "warning",
    "truncation_note",
})
# 截断后每个长字符串至少保留的开头字符数（第一轮）；放不下时第二轮降到 0。
_BOUND_MIN_KEEP_CHARS = 200
_BOUND_LIST_TRUNCATED_SUFFIX = "…[已截断]"

TOOL_RESULT_OVERSIZE_NOTE = (
    "结果超过单次工具返回上限，各条目的 id/title/file_type/status 均已保留，"
    "content 等长文本字段已截断（content_truncated=true，content_length 为原长）。"
    '需要某个文件的完整正文时，用 query_files(id=…, response_mode="full") 单独读取该文件，'
    "不要重复整批读取。"
)


def _dumps_compact(value: Any, *, default: Any = None) -> str:
    if default is not None:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=default)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _slim_write_file_data(data: dict[str, Any]) -> dict[str, Any]:
    """去掉 create_file / write_chapter 返回里模型与前端都不读的 4 个库字段（保留 updated_at）。"""
    return {k: v for k, v in data.items() if k not in WRITE_FILE_OMITTED_FIELDS}


def _json_chars(value: Any) -> int:
    return len(_dumps_compact(value, default=str))


def _collect_long_string_leaves(
    value: Any, min_chars: int, out: list[tuple[Any, Any, str]]
) -> None:
    """收集可截断的长字符串叶子 (container, key, original)。受保护键不收集。"""
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str):
                if key not in _BOUND_PROTECTED_KEYS and len(child) > min_chars:
                    out.append((value, key, child))
            else:
                _collect_long_string_leaves(child, min_chars, out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str):
                if len(child) > min_chars:
                    out.append((value, index, child))
            else:
                _collect_long_string_leaves(child, min_chars, out)


def bound_payload_strings(
    value: Any, max_chars: int, *, min_keep: int = _BOUND_MIN_KEEP_CHARS
) -> tuple[Any, bool]:
    """把 JSON 载荷里的长字符串**就地截短**，使整体序列化不超过 ``max_chars``。

    与「把 json.dumps 结果切一刀」不同，这里保持结构：每个字典条目的 id/title/
    file_type/status/error 等原样保留，只截 content 一类的长文本字段，并在旁边写上
    ``<字段>_length``（content 为 ``content_length``）与 ``<字段>_truncated: true``，
    模型能看出这是截断预览而不是短文件。各长字段按「注水」方式均分预算（统一上限 L），
    不会出现一个条目被砍光、其他条目原封不动。

    返回 (新载荷, 是否发生截断)。放不下时返回尽力截短后的结果，由调用方检查大小再降级。
    原载荷不被修改。
    """
    if value is None or _json_chars(value) <= max_chars:
        return value, False

    # 在深拷贝上定位叶子：容器对象必须是拷贝里的那个，原载荷不被修改。
    work = copy.deepcopy(value)
    leaves: list[tuple[Any, Any, str]] = []
    _collect_long_string_leaves(work, min_keep, leaves)
    if not leaves:
        return value, False
    lengths = [len(original) for _, _, original in leaves]

    def apply(cap: int) -> None:
        for container, key, original in leaves:
            if len(original) <= cap:
                container[key] = original
                continue
            if isinstance(container, dict):
                container[key] = original[:cap]
                if key == "content":
                    if not isinstance(container.get("content_length"), int):
                        container["content_length"] = len(original)
                    container["content_truncated"] = True
                else:
                    container.setdefault(f"{key}_length", len(original))
                    container[f"{key}_truncated"] = True
            else:
                container[key] = original[:cap] + _BOUND_LIST_TRUNCATED_SUFFIX

    # 每个被截字段额外写入 *_length/*_truncated，按 64 字符/字段预留；转义导致的
    # 偏差由外层循环再补一次。
    reduction = _json_chars(value) - max_chars + 64 * len(leaves)
    for _ in range(6):
        low, high = min_keep, max(lengths)
        # 找最大的 cap，使 sum(max(0, len - cap)) >= reduction。
        while low < high:
            mid = (low + high + 1) // 2
            if sum(max(0, length - mid) for length in lengths) >= reduction:
                low = mid
            else:
                high = mid - 1
        apply(low)
        size = _json_chars(work)
        if size <= max_chars or low <= min_keep:
            break
        reduction += size - max_chars + 64
    return work, True


def _compact_oversized_payload(
    payload: Any,
    *,
    original_length: int,
) -> str | None:
    """超限的成功结果：保结构截长文本，而不是把整个 data 换成一个占位符。

    返回可直接发给模型的 JSON；连骨架都放不下时返回 None，由调用方走最小占位。
    """
    if not isinstance(payload, dict) or "data" not in payload:
        return None
    compact = dict(payload)
    compact["truncated"] = True
    compact["max_chars"] = TOOL_RESULT_MAX_CHARS
    compact["original_length"] = original_length
    compact["note"] = TOOL_RESULT_OVERSIZE_NOTE
    for min_keep in (_BOUND_MIN_KEEP_CHARS, 0):
        bounded, _ = bound_payload_strings(compact, TOOL_RESULT_MAX_CHARS, min_keep=min_keep)
        encoded = _dumps_compact(bounded)
        if len(encoded) <= TOOL_RESULT_MAX_CHARS:
            return encoded
    return None


def _serialize_tool_payload(payload: Any) -> str:
    """Serialize tool payload and truncate oversized results safely."""
    serialized = _dumps_compact(payload)
    if len(serialized) <= TOOL_RESULT_MAX_CHARS:
        return serialized

    original_length = len(serialized)
    status = _normalize_payload_status(payload)

    if status != "error":
        structured = _compact_oversized_payload(payload, original_length=original_length)
        if structured is not None:
            return structured

    truncated_payload: dict[str, Any] = {
        "status": status,
        "truncated": True,
        "max_chars": TOOL_RESULT_MAX_CHARS,
        "original_length": original_length,
    }
    # Commit-owned signal must survive content elision; statuses/previews are not proof.
    if isinstance(payload, dict) and isinstance(payload.get("mutation_applied"), bool):
        truncated_payload["mutation_applied"] = payload["mutation_applied"]

    if status == "error":
        error_message = ""
        if isinstance(payload, dict):
            raw_error = payload.get("error")
            if raw_error is not None:
                error_message = str(raw_error)
        truncated_payload["error"] = error_message or "Tool result exceeded max size and was truncated"
    else:
        truncated_payload["data"] = {
            "truncated": True,
            "max_chars": TOOL_RESULT_MAX_CHARS,
            "original_length": original_length,
        }

    encoded_truncated = _dumps_compact(truncated_payload)
    if len(encoded_truncated) <= TOOL_RESULT_MAX_CHARS:
        return encoded_truncated

    if status == "error" and "error" in truncated_payload:
        compact_payload = dict(truncated_payload)
        compact_payload["error"] = str(compact_payload["error"])
        encoded_truncated = _dumps_compact(compact_payload)
        while len(encoded_truncated) > TOOL_RESULT_MAX_CHARS and compact_payload["error"]:
            overflow = len(encoded_truncated) - TOOL_RESULT_MAX_CHARS
            trim_count = max(1, overflow)
            compact_payload["error"] = compact_payload["error"][:-trim_count]
            encoded_truncated = _dumps_compact(compact_payload)
        if len(encoded_truncated) <= TOOL_RESULT_MAX_CHARS:
            return encoded_truncated

    # Final safety fallback: always return a small valid payload.
    minimal_payload = {
        "status": status,
        "truncated": True,
        "max_chars": TOOL_RESULT_MAX_CHARS,
        "original_length": original_length,
    }
    if "mutation_applied" in truncated_payload:
        minimal_payload["mutation_applied"] = truncated_payload["mutation_applied"]
    if status != "error":
        minimal_payload["data"] = {"truncated": True}
    else:
        minimal_payload["error"] = "Tool result truncated"
    return _dumps_compact(minimal_payload)


def _is_folder_file_type(result: dict[str, Any], args: dict[str, Any]) -> bool:
    """判断刚创建的节点是否是文件夹（以执行器落库后的类型为准，回退到入参）。"""
    raw = result.get("file_type") if isinstance(result, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raw = args.get("file_type") if isinstance(args, dict) else None
    return isinstance(raw, str) and raw.strip().lower() == "folder"


def _run_session_probe(session: Session, fn: Callable[[Session], Any]) -> Any:
    """在 session 上跑只读探测查询；失败时回滚 savepoint 或事务，不把异常状态留在连接上。"""
    in_tx = bool(hasattr(session, "in_transaction") and session.in_transaction())
    if in_tx and hasattr(session, "begin_nested"):
        with session.begin_nested():
            return fn(session)
    try:
        return fn(session)
    except Exception:
        if hasattr(session, "rollback"):
            session.rollback()
        raise


def _clear_settled_pending_empty_files() -> bool:
    """落库核验待写入空文件标记，清掉正文已写入或文件已不存在的条目。

    标记只在 <file>…</file> 流式写入收尾时清除；模型改用 edit_file(op=append)
    写完正文时标记会一直留着，后续每次不带 content 的 create_file 都会被它挡回。
    用列级查询读数据库当前值（绕开 ORM 身份映射里可能陈旧的实例）；查询失败时
    保守地不清除任何条目。返回是否清掉了至少一个条目。

    整章替换的条目在它的 <file> 正文落库前一直待写，即使文件已有旧正文：由 adapter
    （保存或拒绝）和 engine（运行边界）清除，这里跳过（同 engine 的 _settle_pending_empty_files）。
    """
    from models import File

    cleared = False
    for entry in ToolContext.get_pending_empty_files():
        file_id = str(entry.get("file_id") or "")
        if not file_id or ToolContext.pending_chapter_replacement(file_id) is not None:
            continue
        try:
            row = _run_session_probe(
                ToolContext.get_session(),
                lambda s, fid=file_id: s.exec(
                    select(File.content, File.is_deleted).where(File.id == fid)
                ).first(),
            )
        except Exception as e:
            logger.debug(f"Pending empty-file probe failed: {e}")
            return cleared
        if row is None or row[1] or str(row[0] or "").strip():
            ToolContext.clear_pending_empty_file(file_id)
            cleared = True
    return cleared


def _pending_empty_file_rejection(
    blocking_title: str | None, title: str, *, tool_name: str = "create_file"
) -> dict[str, Any]:
    """create_file 被「上一个空文件还没写正文」挡回时的错误，点名解除方法。

    error_type 让工具失败熔断器把它识别为顺序性拒绝（先做前置动作即可成功），
    不按「同一调用连续失败」熔断。解除方法与 create_file 的 next_step、正文待写守卫
    （tools_adapter.file_body_pending_text）一致：下一次回复以 <file> 写正文；
    不建议 edit_file——下一次回复里它会被守卫拒绝。
    """
    blocking_name = blocking_title or "未知"
    blocking_id = ""
    for entry in ToolContext.get_pending_empty_files():
        if entry.get("title") == blocking_title:
            blocking_id = str(entry.get("file_id") or "")
    id_part = f"（id={blocking_id}）" if blocking_id else ""
    payload: dict[str, Any] = {
        "status": "error",
        "error_type": "pending_empty_file_unwritten",
        "error": (
            f"文件「{title}」未创建：上一个文件「{blocking_name}」{id_part}正文还是空的，"
            "一次只能流式写入一个文件。"
            f"下一步：下一次回复直接以 <file> 开头写完「{blocking_name}」的正文、用 </file> 结束；"
            f"写完后再调用 {tool_name} 写「{title}」。在此之前重复调用 {tool_name} 只会再次被拒绝。"
        ),
    }
    if blocking_id:
        payload["pending_file_id"] = blocking_id
    return _make_mcp_payload(payload, tool_name=tool_name)


def _body_in_reply_next_step(title: str, tool_name: str) -> str:
    """create_file / write_chapter 认领了本条回复里先写出的正文（body_in_reply）时给模型的下一步。"""
    return f"「{title}」的正文就是这条回复里 {tool_name} 之前写的那段 <file> 正文，已存进这个文件，不要再写一遍。"


def _awaiting_body_data(data: dict[str, Any], title: str) -> dict[str, Any]:
    """create_file 建好了空文件：告诉模型下一次回复就写正文。

    下一次回复没写正文就调用的工具会被拒绝（``tools_adapter.file_body_pending_text``）。
    """
    return {
        **data,
        "awaiting_body": True,
        "next_step": (
            f"「{title}」已建好，正文还是空的。下一次回复直接以 <file> 开头写它的正文，"
            "写完用 </file> 结束；不要先调用其他工具。"
        ),
    }


def _repeat_of_pending_empty_file(args: dict[str, Any], project_id: str) -> dict[str, Any] | None:
    """create_file 被挡住时，判断它是不是在重复创建正挡路的那个空文件；是就把那个文件交回去。

    模型常见的做法是：上一次回复 create_file(X) 成功，下一次回复本该以 <file> 开头写
    X 的正文，却又调用一次 create_file(X)。这次调用要的就是已经建好的那个空文件，
    按「一次只能流式写一个文件」拒绝它只会多烧一轮、给作者留一张失败卡。

    只在以下条件同时成立时认作重复：没有建库途中的占坑、恰好只有一个尚未写出正文的
    条目、标题相同，且库里那一行仍在本项目、未删除、正文为空、父目录与类型都对得上。
    不新增条目、不新建文件；返回的 content 为空且不带 reused_existing，
    StreamAdapter 会照常为同一个 file_id 重新等待 <file> 正文。
    """
    from models import File

    state = ToolContext._get_pending_empty_file_state()
    if state is None:
        return None
    entry = state.sole_unwritten_entry()
    if entry is None:
        return None

    title = str(args.get("title") or "").strip()
    if not title or str(entry.get("title") or "").strip() != title:
        return None

    file_id = str(entry.get("file_id") or "")
    if not file_id:
        return None
    try:
        row = _run_session_probe(
            ToolContext.get_session(),
            lambda s: s.exec(
                select(File).where(File.id == file_id).execution_options(populate_existing=True)
            ).first(),
        )
    except Exception as e:
        logger.debug(f"Pending empty-file repeat probe failed: {e}")
        return None
    if (
        row is None
        or row.is_deleted
        or row.project_id != project_id
        or str(row.content or "").strip()
        or str(row.title or "").strip() != title
    ):
        return None

    requested_parent = args.get("parent_id") or None
    if requested_parent != (row.parent_id or None):
        return None
    requested_type = str(args.get("file_type") or "draft").strip().lower()
    row_type = str(row.file_type or "").strip().lower()
    # crud 会把剧本目录下分集的 draft/document 规范成 script，重复调用照原参数传来时也算同一个文件
    if requested_type != row_type and not (
        row_type == "script" and requested_type in {"draft", "document"}
    ):
        return None

    log_with_context(
        logger,
        20,  # INFO
        "agent.create_file.same_title_repeat",
        project_id=project_id,
        file_id=file_id,
        title=title,
        file_type=row_type,
    )
    data = serialize_file(row, omit_fields=WRITE_FILE_OMITTED_FIELDS)
    data["content"] = ""
    data = _awaiting_body_data(data, title)
    data["next_step"] = "不要再调用 create_file。" + data["next_step"]
    return _make_result(
        {"status": "success", "mutation_applied": False, "data": data},
        tool_name="create_file",
    )


# 单循环 agent 的正文（draft / script）只经 write_chapter 按章号 / 集号写入：create_file 不建正文，
# edit_file 不整份替换正文（见 FileEditor 的 body_guards）。
LOOP_BODY_CREATE_ERROR_TYPE = "chapter_body_requires_write_chapter"
WRITE_CHAPTER_TOOL_NAME = "write_chapter"
WRITE_CHAPTER_ACTION_CREATED = "created"
WRITE_CHAPTER_ACTION_REPLACED = "replaced"
WRITE_CHAPTER_AMBIGUOUS_ERROR_TYPE = "chapter_target_ambiguous"
WRITE_CHAPTER_INVALID_ERROR_TYPE = "invalid_chapter"
WRITE_CHAPTER_REPLACE_PENDING_ERROR_TYPE = "chapter_replace_pending"
WRITE_CHAPTER_WRITTEN_THIS_TURN_ERROR_TYPE = "chapter_written_this_turn"
# file_type → 标题里的序号单位（有界的标识符检查，只比对标题开头的「第N章 / 第N集 / Chapter N / Episode N」）。
_CHAPTER_UNIT_BY_TYPE = {"draft": "章", "script": "集"}
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SOURCE_COPY_FILE_TYPES = frozenset({"outline", "character", "lore", "snippet", "document"})
SOURCE_TEXT_CONTEXT_MISSING_ERROR_TYPE = "source_text_context_missing"
SOURCE_TEXT_NOT_FOUND_ERROR_TYPE = "source_text_not_in_author_message"
SOURCE_TEXT_INVALID_ERROR_TYPE = "invalid_source_text"
SOURCE_TEXT_CONFLICT_ERROR_TYPE = "source_text_conflict"
SOURCE_TEXT_FILE_TYPE_ERROR_TYPE = "invalid_source_text_file_type"
SOURCE_TEXT_EXISTING_MISMATCH_ERROR_TYPE = "source_text_existing_mismatch"


def _loop_agent_type() -> str:
    from agent.tools.registry import LOOP_AGENT_TYPE

    return LOOP_AGENT_TYPE


_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")
_CANONICAL_LINE_SEP = r"[ \t]*(?:\r\n|\r|\n)(?:[ \t]*(?:\r\n|\r|\n))*"


def _find_unique_line_match(author_message: str, candidate: str) -> str | None:
    """在作者原文中按行精确查找唯一片段（仅兼容 CRLF/CR、行尾空白与连续空行个数差异）。"""
    lines = [line.rstrip(" \t") for line in _LINE_BREAK_RE.split(candidate) if line.strip(" \t")]
    if not lines:
        return None
    pattern = re.compile(_CANONICAL_LINE_SEP.join(re.escape(line) for line in lines))
    matched_slices: set[str] = set()
    pos = 0
    while (match := pattern.search(author_message, pos)) is not None:
        matched_slices.add(match.group(0).replace("\r\n", "\n").replace("\r", "\n"))
        if len(matched_slices) > 1:
            return None
        pos = match.start() + 1
    return next(iter(matched_slices)) if len(matched_slices) == 1 else None


def _match_verbatim_author_span(author_message: str, source_text: str) -> str | None:
    """在作者本轮原始消息中核验并截取连续原文片段（兼容 CRLF 与行尾多余空白，禁止改写或跳段拼接）。"""
    if source_text in author_message:
        return source_text
    stripped = source_text.strip()
    if not stripped:
        return None

    # 先用保留首行缩进（含中文全角「　　」或列表前导空格）的 source_text 匹配，避免 strip() 吞掉第一段段首缩进。
    preserved_indent_match = _find_unique_line_match(author_message, source_text)
    if preserved_indent_match is not None:
        return preserved_indent_match

    if stripped in author_message:
        return stripped
    return _find_unique_line_match(author_message, stripped)


def _source_copy_content(args: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Validate a model-selected exact span from this request's raw author message.

    The model chooses what the material means and which span belongs in a file.  This
    guard only proves that the bytes being saved came from the current request; it does
    not infer intent or expand the selected range from natural language.
    """
    if "source_text" not in args:
        return None, None

    source_text = args.get("source_text")
    if not isinstance(source_text, str) or not source_text.strip():
        return None, _make_structured_error(
            "source_text 必须是作者本轮消息中的非空连续原文；没有新建文件。",
            error_type=SOURCE_TEXT_INVALID_ERROR_TYPE,
            tool_name="create_file",
        )
    if "content" in args or coerce_bool(args.get("empty")):
        return None, _make_structured_error(
            "source_text 不能与 content 或 empty 同用；没有新建文件。",
            error_type=SOURCE_TEXT_CONFLICT_ERROR_TYPE,
            tool_name="create_file",
        )

    file_type = str(args.get("file_type") or "").strip().lower()
    if file_type not in _SOURCE_COPY_FILE_TYPES:
        return None, _make_structured_error(
            "source_text 只用于大纲、人物、设定、素材或其他资料文件；没有新建文件。",
            error_type=SOURCE_TEXT_FILE_TYPE_ERROR_TYPE,
            tool_name="create_file",
        )

    author_message = ToolContext._get_context().get("author_message")
    if not isinstance(author_message, str):
        return None, _make_structured_error(
            "当前调用没有可校验的作者原始消息，不能复制 source_text；没有新建文件。",
            error_type=SOURCE_TEXT_CONTEXT_MISSING_ERROR_TYPE,
            tool_name="create_file",
        )
    matched_span = _match_verbatim_author_span(author_message, source_text)
    if matched_span is None:
        return None, _make_structured_error(
            "source_text 不是作者本轮消息中的逐字连续原文；不要改写、拼接或补字段。没有新建文件。",
            error_type=SOURCE_TEXT_NOT_FOUND_ERROR_TYPE,
            tool_name="create_file",
        )
    return matched_span, None


def _loop_body_create_refusal(args: dict[str, Any]) -> dict[str, Any] | None:
    """单循环 agent 用 create_file 建正文类文件时拒绝，指向 write_chapter；其余情况返回 None。"""
    from agent.tools.registry import LOOP_AGENT_TYPE

    if ToolContext.get_current_agent() != LOOP_AGENT_TYPE:
        return None
    requested_type = str(args.get("file_type") or "draft").strip().lower()
    file_type = requested_type
    if file_type == "document":
        # create_file 会把剧本分集目录下带集号的 document 归一成 script（见 resolve_create_target）：
        # 按实际会建的类型判断，不让正文绕过 write_chapter。
        target = _resolve_chapter_target(
            args.get("parent_id") or None,
            title=str(args.get("title") or ""),
            file_type=file_type,
            metadata=args.get("metadata"),
        )
        file_type = target[1] if target is not None else file_type
    if file_type not in CONTENT_FILE_TYPES:
        return None
    return _make_structured_error(
        (
            f"create_file 不建正文（file_type={file_type}），没有新建文件。"
            + (
                f"（剧本分集目录下标题带集号的 {requested_type} 文件会被当作分集剧本；要另存一版请放进素材等资料目录，"
                "标题不带集号。）"
                if requested_type != file_type
                else ""
            )
            + "新写一章或整章重写都用 "
            "write_chapter(chapter=章号, title=章名, file_type=draft 或 script)，它按章号定位：这一章不存在就新建，"
            "已存在就替换这一章；调用后下一次回复用 <file>…</file> 写正文。大纲、人物、设定等资料文件用 create_file 并写明 file_type。"
        ),
        error_type=LOOP_BODY_CREATE_ERROR_TYPE,
        tool_name="create_file",
    )


def _resolve_chapter_target(
    parent_id: str | None,
    *,
    title: str,
    file_type: str,
    metadata: Any = None,
) -> tuple[str | None, str] | None:
    """create_file 实际会用的 (parent_id, file_type)（同 crud 的归一）；读不到项目时为 None。"""
    from agent.tools.file_ops.crud import resolve_create_target
    from models import Project

    project_id = ToolContext._get_context().get("project_id")
    if project_id is None:
        return None
    try:
        return _run_session_probe(
            ToolContext.get_session(),
            lambda s: resolve_create_target(
                s,
                project_id,
                getattr(s.get(Project, project_id), "project_type", None),
                parent_id,
                title=title,
                file_type=file_type,
                metadata=metadata if isinstance(metadata, dict) else None,
            ),
        )
    except Exception as e:  # noqa: BLE001 - 解析不了时按原参数处理，create_file 自己校验父节点
        logger.debug(f"Resolving the create target failed: {type(e).__name__}")
        return None


def _chapter_title(title: str, number: int, unit: str) -> str:
    """标题不带章号时补上（展示格式）：中文标题补「第N章 」，其余补「Chapter N: 」。"""
    text = title.strip()
    if _CJK_RE.search(text) or not text:
        return f"第{number}{unit} {text}".strip()
    label = "Chapter" if unit == "章" else "Episode"
    return f"{label} {number}: {text}"


def _row_chapter_number(row: Any, *, unit: str, expected_file_type: str) -> int | None:
    """按标题开头的章/集号解析；无前缀章号时（如导入拆章「书名 - 第N章 …」）回退读取 file_metadata。"""
    from collections.abc import Mapping

    from utils.title_sequence import extract_sequence_number, extract_sequence_number_from_metadata

    if getattr(row, "file_type", None) != expected_file_type:
        return None
    seq = extract_sequence_number(row.title, unit=unit)
    if seq is not None:
        return seq
    prefixed = _prefixed_chapter_heading(str(getattr(row, "title", "") or ""), unit)
    raw_meta = getattr(row, "file_metadata", None)
    if raw_meta:
        meta: Mapping[str, Any] | None = None
        if isinstance(raw_meta, Mapping):
            meta = raw_meta
        elif isinstance(raw_meta, str):
            try:
                parsed = json.loads(raw_meta)
            except (json.JSONDecodeError, TypeError):
                parsed = None
            meta = parsed if isinstance(parsed, Mapping) else None
        if meta is not None:
            meta_seq = extract_sequence_number_from_metadata(meta, file_type=expected_file_type)
            if meta_seq is not None:
                return meta_seq
    return prefixed[0] if prefixed is not None else None


def _write_chapter_error(error: str, *, error_type: str, **fields: Any) -> dict[str, Any]:
    return _make_structured_error(
        error,
        error_type=error_type,
        tool_name=WRITE_CHAPTER_TOOL_NAME,
        **fields,
    )


async def write_chapter(args: dict[str, Any]) -> dict[str, Any]:
    """按章号 / 集号写一章正文：不存在就新建，已存在就替换这一章（正文随后以 <file> 写入）。"""
    return await _offload_write_tool(WRITE_CHAPTER_TOOL_NAME, _write_chapter_sync, args)


def _write_chapter_sync(args: dict[str, Any]) -> dict[str, Any]:
    """write_chapter 的同步实现。

    目标只按编号解析：本项目未删除、与请求同类型的正文文件里，标题开头的章号（draft）或集号
    （script）等于 ``chapter`` 的那一个（给了 parent_id 只看那个目录）。

    - 没有：按 create_file 的路径新建空文件（标题不带章号时补上），正文在下一次回复用 <file> 写入；
      两次回复协议、待写守卫、空文件纠偏与回滚、退还时移除空白占位都照旧。
    - 恰好一个：把这一章定为整章替换目标（不改数据库），记下当时的版本，同样等下一次回复的 <file>；
      正文写完后 StreamAdapter 用 ``FileEditor.replace_body`` 提交（版本核对、作者正文保护、AI 版本、
      撤销锚点、diff 审阅），结果补发到同一张卡上。作者保存过正文的章节在这里就拒绝。
    - 多个（重复的章节文件，或没给 parent_id 而多个目录都有这一章）：不写，列出候选让模型确认。
    """
    from models import File
    from utils.title_sequence import extract_sequence_number

    tool_name = WRITE_CHAPTER_TOOL_NAME
    project_id = ToolContext._get_context().get("project_id")
    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    raw_number = args.get("chapter")
    try:
        number = int(raw_number) if not isinstance(raw_number, bool) else 0
    except (TypeError, ValueError):
        number = 0
    if number <= 0 or (isinstance(raw_number, float) and not raw_number.is_integer()):
        return _write_chapter_error(
            "write_chapter 需要 chapter（正整数章号；剧本是集号），没有写入。",
            error_type=WRITE_CHAPTER_INVALID_ERROR_TYPE,
        )
    file_type = str(args.get("file_type") or "draft").strip().lower()
    unit = _CHAPTER_UNIT_BY_TYPE.get(file_type)
    if unit is None:
        return _write_chapter_error(
            "write_chapter 只写正文：file_type 是 draft（小说章节）或 script（剧本分集），没有写入。"
            "大纲、人物、设定等资料文件用 create_file。",
            error_type=WRITE_CHAPTER_INVALID_ERROR_TYPE,
        )
    raw_title = str(args.get("title") or "").strip()
    titled_number = extract_sequence_number(raw_title, unit=unit)
    prefixed_heading = _prefixed_chapter_heading(raw_title, unit) if titled_number is None else None
    if prefixed_heading is not None:
        titled_number = prefixed_heading[0]
    if titled_number is not None and titled_number != number:
        return _write_chapter_error(
            f"标题「{raw_title}」写的是第{titled_number}{unit}，chapter 却是 {number}，没有写入。"
            f"确认要写第几{unit}后让两者一致（title 可以只写章名）。",
            error_type=WRITE_CHAPTER_INVALID_ERROR_TYPE,
        )
    title = raw_title if titled_number is not None else _chapter_title(raw_title, number, unit)
    requested_parent_id = args.get("parent_id") or None
    parent_id = requested_parent_id
    if requested_parent_id is not None:
        # 与 create_file 同一套父节点归一（传成上一章的文件 id、剧本项目传了 -draft-folder）：
        # 查找与新建落在同一个目录，不会在已有这一章的目录旁边再建一个。
        target = _resolve_chapter_target(requested_parent_id, title=title, file_type=file_type)
        if target is not None:
            parent_id = target[0]

    try:
        rows = _run_session_probe(
            ToolContext.get_session(),
            lambda s: s.exec(
                select(File.id, File.title, File.parent_id, File.file_type, File.file_metadata).where(
                    File.project_id == project_id,
                    File.is_deleted.is_(False),
                    File.file_type == file_type,
                )
            ).all(),
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"读取章节列表失败，没有写入：{e}", tool_name=tool_name)
    matches = [
        row
        for row in rows
        if _row_chapter_number(row, unit=unit, expected_file_type=file_type) == number
        and (parent_id is None or (row.parent_id or None) == parent_id)
    ]
    if len(matches) > 1:
        listed = "；".join(f"《{row.title}》(id={row.id}, parent_id={row.parent_id})" for row in matches[:6])
        return _write_chapter_error(
            f"第{number}{unit}对应不止一个文件：{listed}。没有写入。"
            f"分卷时传 parent_id 指明是哪一卷；同一目录里重复的第{number}{unit}文件请告诉作者，"
            "由作者决定保留哪一个，不要自己删除或合并。",
            error_type=WRITE_CHAPTER_AMBIGUOUS_ERROR_TYPE,
            candidates=[{"id": row.id, "title": row.title, "parent_id": row.parent_id} for row in matches[:6]],
        )
    empty = coerce_bool(args.get("empty"))
    if matches and requested_parent_id is None and not empty:
        body_folders = {
            row.parent_id or None
            for row in rows
            if _row_chapter_number(row, unit=unit, expected_file_type=file_type) is not None
        }
        if len(body_folders) > 1:
            # 正文分在几个目录（分卷常各自从第1章编号）而没指明目录：唯一的同号章节不一定是要写的那一卷，
            # 不把它当替换目标。
            existing = matches[0]
            return _write_chapter_error(
                f"正文分在 {len(body_folders)} 个目录里，没给 parent_id 时不确定要写哪一个目录的第{number}{unit}"
                f"（已有的是《{existing.title}》，parent_id={existing.parent_id}）。没有写入。"
                "传 parent_id 指明目录：要替换它就传它所在的目录，要在别的卷新写就传那一卷的目录。",
                error_type=WRITE_CHAPTER_AMBIGUOUS_ERROR_TYPE,
                candidates=[{"id": existing.id, "title": existing.title, "parent_id": existing.parent_id}],
            )
    if not matches:
        if parent_id is None:
            parent_id = _default_chapter_folder(project_id, file_type, rows)
        return _write_chapter_create(
            args, title=title, number=number, unit=unit, file_type=file_type, parent_id=parent_id, empty=empty
        )
    if empty:
        # 只建空章节：这一章已经有文件，什么也不做。
        existing = matches[0]
        return _make_result(
            {
                "status": "success",
                "mutation_applied": False,
                "data": {
                    "id": existing.id,
                    "title": existing.title,
                    "file_type": existing.file_type,
                    "chapter": number,
                    "action": "exists",
                    "result": f"第{number}{unit}《{existing.title}》已经有文件，没有改动。",
                },
            },
            tool_name=tool_name,
        )
    existing_title = (matches[0].title or "").strip()
    if (raw_title and raw_title == existing_title) or not _chapter_title_has_subtitle(raw_title, unit):
        requested_replace_title = None
    else:
        candidate_title = title
        existing_prefixed = _split_prefixed_chapter_title(existing_title, unit)
        if existing_prefixed is not None and _split_prefixed_chapter_title(raw_title, unit) is None:
            candidate_title = f"{existing_prefixed[0]} - {title}"
        requested_replace_title = None if candidate_title == existing_title else candidate_title
    return _write_chapter_replace(
        matches[0].id,
        number=number,
        unit=unit,
        project_id=project_id,
        requested_title=requested_replace_title,
    )


def _default_chapter_folder(project_id: str, file_type: str, rows: list[Any]) -> str | None:
    """没给 parent_id 的新章节放哪：同类型章节都在同一个目录就用它，否则用项目的默认正文目录；都没有返回 None。"""
    from models import File

    parents = {row.parent_id or None for row in rows if row.file_type == file_type}
    if len(parents) == 1:
        (only,) = parents
        if only is not None:
            return only
    default_ids = [f"{project_id}-draft-folder", f"{project_id}-script-folder"]
    if file_type == "script":
        default_ids = default_ids[1:]
    try:
        found = _run_session_probe(
            ToolContext.get_session(),
            lambda s: s.exec(
                select(File.id).where(
                    File.id.in_(default_ids),
                    File.project_id == project_id,
                    File.is_deleted.is_(False),
                    File.file_type == "folder",
                )
            ).all(),
        )
    except Exception as e:  # noqa: BLE001 - 查不到默认目录时保持原行为
        logger.debug(f"Looking up the default chapter folder failed: {type(e).__name__}")
        return None
    return next((fid for fid in default_ids if fid in set(found)), None)


def _write_chapter_create(
    args: dict[str, Any],
    *,
    title: str,
    number: int,
    unit: str,
    file_type: str,
    parent_id: str | None,
    empty: bool,
) -> dict[str, Any]:
    create_args: dict[str, Any] = {"title": title, "file_type": file_type, "content": "", "empty": empty}
    if parent_id is not None:
        create_args["parent_id"] = parent_id
    if args.get("metadata") is not None:
        create_args["metadata"] = args.get("metadata")
    raw = _create_file_sync(create_args, tool_name=WRITE_CHAPTER_TOOL_NAME)
    payload = _payload_of(raw)
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return raw
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    created_title = str(data.get("title") or title)
    data = {
        **data,
        "chapter": number,
        "action": WRITE_CHAPTER_ACTION_CREATED,
        "result": f"新建了第{number}{unit}《{created_title}》。",
    }
    return _make_result({**payload, "data": data}, tool_name=WRITE_CHAPTER_TOOL_NAME)


def _write_chapter_replace(
    file_id: str,
    *,
    number: int,
    unit: str,
    project_id: str,
    requested_title: str | None = None,
) -> dict[str, Any]:
    from models import File
    from models.file_model import validate_file_title
    from utils.text_metrics import count_words

    tool_name = WRITE_CHAPTER_TOOL_NAME
    pending_ids = {str(entry.get("file_id") or "") for entry in ToolContext.get_pending_empty_files()}
    session = ToolContext.get_session()
    try:
        row = _run_session_probe(
            session,
            lambda s: s.exec(
                select(File).where(File.id == file_id).execution_options(populate_existing=True)
            ).first(),
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"读取第{number}{unit}失败，没有写入：{e}", tool_name=tool_name)
    if row is None or row.is_deleted or row.project_id != project_id:
        return _write_chapter_error(
            f"第{number}{unit}的文件已不存在，没有写入。", error_type=EDIT_ERROR_FILE_NOT_FOUND
        )
    content = row.content or ""
    data = serialize_file(row, omit_fields=WRITE_FILE_OMITTED_FIELDS)
    data.pop("content", None)
    data["chapter"] = number
    if file_id in pending_ids:
        armed = ToolContext.pending_chapter_replacement(file_id)
        if armed is not None:
            # 已有的这一章已经定为整章替换、在等正文（同一回复里重复或并行调用）：不当成新建，
            # 也不重新定位；第一次调用的结果与卡片照旧有效。
            return _write_chapter_error(
                f"第{number}{unit}《{row.title}》已经定为整章替换（原{armed.get('previous_word_count')}字），正在等正文，"
                "这次调用没有改动。不要再调用 write_chapter；下一次回复直接以 <file> 开头写这一章完整的新正文。",
                error_type=WRITE_CHAPTER_REPLACE_PENDING_ERROR_TYPE,
                id=file_id,
                chapter=number,
            )
        # 这一章就是本请求刚建、还在等正文的那个文件：交回它，下一次回复照常写正文。
        data = _awaiting_body_data(data, str(row.title or ""))
        data["action"] = WRITE_CHAPTER_ACTION_CREATED
        if content and ToolContext.pending_empty_file_version(file_id) is None:
            # 复用的已有文件（不是本请求建的）已经有正文：带上原长度，截断补全时不整体覆盖它。
            data["reused_existing"] = True
            data["original_content_length"] = len(content)
        data["next_step"] = "不要再调用 write_chapter。" + data["next_step"]
        return _make_result({"status": "success", "mutation_applied": False, "data": data}, tool_name=tool_name)

    if ToolContext.chapter_body_saved_this_request(file_id):
        # 这一章的正文这一轮已经由 write_chapter 写完并保存：不再整章重写（自查发现的问题用 edit_file 局部改）。
        # 看的是本请求里确认落库的结构化记录，不看模型的文字。
        return _write_chapter_error(
            f"第{number}{unit}《{row.title}》的正文这一轮已经用 write_chapter 写好并保存了，不再整章重写，没有写入。"
            "要改其中几处用 edit_file 局部修改；没有要改的就继续作者这一轮要求的下一步或回复作者。",
            error_type=WRITE_CHAPTER_WRITTEN_THIS_TURN_ERROR_TYPE,
            id=file_id,
            chapter=number,
        )

    new_title: str | None = None
    if isinstance(requested_title, str):
        cleaned_title = requested_title.replace("\x00", "").strip()
        if cleaned_title and cleaned_title != (row.title or ""):
            try:
                validate_file_title(cleaned_title)
            except ValueError as e:
                return _write_chapter_error(str(e), error_type=WRITE_CHAPTER_INVALID_ERROR_TYPE, id=file_id)
            clash = _run_session_probe(
                session,
                lambda s: has_sibling_title_conflict(
                    s,
                    project_id=project_id,
                    parent_id=row.parent_id,
                    title=cleaned_title,
                    exclude_file_id=file_id,
                ),
            )
            if clash:
                return _write_chapter_error(
                    f"同一文件夹里已有名为《{cleaned_title}》的文件，没有写入；请换一个标题或先向作者确认。",
                    error_type=WRITE_CHAPTER_INVALID_ERROR_TYPE,
                    id=file_id,
                )
            new_title = cleaned_title

    reservation = f"reserve-{uuid4().hex}"
    acquired, blocking_title = ToolContext.try_reserve_pending_empty_file(reservation, str(new_title or row.title or ""))
    if not acquired and _clear_settled_pending_empty_files():
        acquired, blocking_title = ToolContext.try_reserve_pending_empty_file(reservation, str(new_title or row.title or ""))
    if not acquired:
        return _pending_empty_file_rejection(blocking_title, str(new_title or row.title or ""), tool_name=tool_name)
    previous_words = count_words(content)
    title_clause = (
        f"保存时标题一并更新为《{new_title}》。"
        if new_title
        else "标题沿用（写完正文前不要对这一章调用 rename_file）。"
    )
    data.update(
        {
            "action": WRITE_CHAPTER_ACTION_REPLACED,
            "awaiting_body": True,
            "previous_length": len(content),
            "previous_word_count": previous_words,
            "original_content_length": len(content),
            **({"new_title": new_title} if new_title else {}),
            "result": (
                f"覆盖已有的第{number}{unit}《{row.title}》（原{previous_words}字）：下一次回复写的正文会整章替换它，"
                f"{title_clause}"
            ),
            "next_step": (
                "下一次回复直接以 <file> 开头写这一章完整的新正文（不是改动片段），写完用 </file> 结束；"
                "不要先调用其他工具。保存后原稿可在历史版本里找回（也可在聊天修改卡撤销）。"
            ),
        }
    )
    # 不是本轮新建的文件：不记建好时的版本（补不成时不回滚、不算可以取消的空文件），记下替换的定位结果。
    ToolContext.bind_pending_empty_file(
        reservation, file_id, str(new_title or row.title or ""), None, replacement=data
    )
    if ToolContext.take_reply_prebody():
        # 本条回复在这次调用之前已写出正文：它就是这一章的替换稿，由 StreamAdapter 走整章替换落库。
        ToolContext.mark_pending_body_streamed(file_id)
        data = {
            **data,
            "body_in_reply": True,
            "next_step": _body_in_reply_next_step(str(row.title or ""), tool_name),
        }
    log_with_context(
        logger,
        20,  # INFO
        "agent.write_chapter.replace_armed",
        project_id=project_id,
        file_id=file_id,
        unit=unit,
        number=number,
        previous_length=len(content),
    )
    return _make_result({"status": "success", "mutation_applied": False, "data": data}, tool_name=tool_name)


def _payload_of(result: Any) -> Any:
    """MCP 结果里那段 JSON 文本解析回字典（解析不了返回 None）。"""
    try:
        return json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None


async def create_file(args: dict[str, Any]) -> dict[str, Any]:
    """创建新文件。"""
    # asyncio.to_thread 运行在拷贝上下文中，但 pending-empty-file 标记通过
    # 请求上下文 dict 里的共享持有者原地修改，_create_file_sync 在工作线程
    # 里设置后对主上下文同样可见，无需在此回填。
    return await _offload_write_tool("create_file", _create_file_sync, args)


def _create_file_sync(args: dict[str, Any], *, tool_name: str = "create_file") -> dict[str, Any]:
    """Synchronous create_file implementation (write_chapter 新建一章时也走这里)."""
    project_id = ToolContext._get_context().get("project_id")

    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    title = args.get("title", "")
    if tool_name == "create_file":
        body_refusal = _loop_body_create_refusal(args)
        if body_refusal is not None:
            return body_refusal
        source_content, source_error = _source_copy_content(args)
        if source_error is not None:
            return source_error
    else:
        source_content = None
    content = source_content if source_content is not None else args.get("content", "")
    executor = ToolContext.get_executor()
    # 作者明确只要建文件（「只建标题，不写内容」）：这个空文件就是交付物，不等正文。
    # 只在路由判断作者本轮不要正文时生效，作者要正文时模型传了也照常等正文。
    empty_requested = coerce_bool(args.get("empty"))
    empty_by_request = empty_requested and not content and ToolContext.author_wants_no_body()

    # 检查是否已有待写入的空文件（防止连续创建空文件导致内容丢失）。
    # 检查与置位必须原子完成：两者之间隔着 DB INSERT（还跨 asyncio.to_thread
    # 的线程边界），先查后置会让并发的两次空文件创建同时通过检查，各自建出一个
    # 空文件，后置位的把先置位的覆盖掉——先建的空文件从此没有任何指针。
    # 因此这里先 try_reserve 占坑，落库后再 bind 真实 file_id。
    reservation: str | None = None
    if not content and not empty_by_request:
        reservation = f"reserve-{uuid4().hex}"
        acquired, blocking_title = ToolContext.try_reserve_pending_empty_file(
            reservation, title
        )
        if not acquired and _clear_settled_pending_empty_files():
            # 挡路的标记已陈旧（正文已经用 edit_file 等方式写进去了，或文件已删）：
            # 清掉后再占一次坑，不让模型为一个早已完成的文件反复撞墙。
            acquired, blocking_title = ToolContext.try_reserve_pending_empty_file(
                reservation, title
            )
        if not acquired:
            # 挡路的正是本次要建的这个空文件（同标题、同父目录、同类型）：交回它，
            # 让模型直接写正文；挡路的是别的文件时照旧拒绝。
            repeat = _repeat_of_pending_empty_file(args, project_id)
            if repeat is not None:
                return repeat
            return _pending_empty_file_rejection(blocking_title, title, tool_name=tool_name)

    try:
        order_value = args.get("order") if "order" in args else None
        if order_value is not None:
            try:
                order_value = int(order_value)
            except (TypeError, ValueError):
                order_value = None

        result = executor.create_file(
            project_id=project_id,
            title=title,
            file_type=args.get("file_type", "draft"),
            content=content,
            parent_id=args.get("parent_id"),
            order=order_value,
            metadata=args.get("metadata"),
            # 应用内 agent 写正文：draft/script 的双引号按稿件体例规范化。
            normalize_quotes=True,
        )

        # 如果创建的是空文件，把占坑换成真实 file_id
        # folder 例外：文件夹是纯容器节点，永远不会收到 <file>…</file> 正文，
        # 给它置标记后无人清除（标记跨子任务可见），会硬阻断本轮后续所有建档，
        # 并让纠偏分支把章节正文追加到文件夹节点上。
        awaiting_body = False
        body_in_reply = False
        if reservation is not None:
            file_id = result.get("id", "")
            if file_id and not _is_folder_file_type(result, args):
                # 只有本轮新建的文件记下建好时的版本（之后补不成可以回滚）；复用的既有空文件
                # 照样等正文，但不是本轮产物，不记版本，回滚不删它。
                created_version = None if result.get("reused_existing") is True else result.get("updated_at")
                ToolContext.bind_pending_empty_file(
                    reservation, file_id, title, created_version if isinstance(created_version, str) else None
                )
                reservation = None
                if ToolContext.pending_body_files_closed():
                    # 这一轮已经提前结束（StreamAdapter 不会再处理这个结果）：撤销刚建的空文件。
                    return _withdraw_file_created_after_close(file_id, title, result, tool_name=tool_name)
                awaiting_body = True
                # 本条回复在建它之前已写出正文：正文属于这个新文件，由 StreamAdapter 落库；
                # 守卫视同正文已写出，下一次回复不再要求先写它。复用的既有文件不认领。
                if result.get("reused_existing") is not True and ToolContext.take_reply_prebody():
                    ToolContext.mark_pending_body_streamed(file_id)
                    body_in_reply = True
            else:
                ToolContext.release_pending_empty_file(reservation)
            reservation = None


        if source_content is not None:
            if result.get("reused_existing") is True and result.get("content") != source_content:
                return _make_structured_error(
                    "同名文件已存在且内容与作者原文不同；现有文件保持原样，没有复制 source_text。",
                    error_type=SOURCE_TEXT_EXISTING_MISMATCH_ERROR_TYPE,
                    tool_name=tool_name,
                )
            result = {**result, "source_copied": True, "body_saved": True}
        data = _slim_write_file_data(_elide_reused_file_content(result))
        if body_in_reply:
            data = {**data, "body_in_reply": True, "next_step": _body_in_reply_next_step(title, tool_name)}
        elif awaiting_body:
            data = _awaiting_body_data(data, title)
        elif empty_by_request:
            data = {**data, "empty_by_request": True}
        return _make_result(
            {
                "status": "success",
                "mutation_applied": result.get("mutation_applied") is True,
                "data": data,
            },
            tool_name=tool_name,
        )
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)
    finally:
        # 创建失败（异常或提前 return）时必须回滚占坑，否则这个永远不会被
        # bind 的票据会把本请求后续所有空文件创建全部挡在门外。
        if reservation is not None:
            ToolContext.release_pending_empty_file(reservation)


def _withdraw_file_created_after_close(
    file_id: str, title: str, result: dict[str, Any], *, tool_name: str
) -> dict[str, Any]:
    """这一轮被提前结束之后才建好的空文件：只撤销本请求新建、版本未变的那个，然后报告没有建成。"""
    from agent.core.empty_files import rollback_unfinished_empty_files

    version = result.get("updated_at") if result.get("reused_existing") is not True else None
    if isinstance(version, str) and version:
        rollback_unfinished_empty_files([{"file_id": file_id, "title": title, "updated_at": version}])
    ToolContext.clear_pending_empty_file(file_id)
    return _make_structured_error(
        "这一轮已经结束，没有新建文件。",
        error_type="round_closed",
        tool_name=tool_name,
    )


async def edit_file(args: dict[str, Any]) -> dict[str, Any]:
    """精确编辑文件内容。"""
    return await _offload_write_tool("edit_file", _edit_file_sync, args)


def _edit_file_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous edit_file implementation."""
    tool_name = "edit_file"
    executor = ToolContext.get_executor()
    project_id = ToolContext._get_context().get("project_id")

    # 写工具必须运行在绑定了项目的上下文中；执行器层会进一步校验目标文件
    # 属于该项目（check_file_access_in_tool_context）。
    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    read_ticket: int | None = None
    try:
        file_id_raw = args.get("id") or args.get("file_id") or args.get("fileId")
        if isinstance(file_id_raw, str):
            file_id = file_id_raw.strip()
        elif file_id_raw is None:
            file_id = ""
        else:
            # Do not silently accept non-string IDs; keep tool inputs strict to
            # avoid noisy "file not found" logs when the caller passes wrong types.
            return _make_edit_error(
                "edit_file: invalid param 'id' (must be a string).",
                error_type=EDIT_ERROR_INVALID_EDIT,
            )
        if not file_id:
            return _make_edit_error(
                "edit_file: missing required param 'id' (alias: file_id). "
                "Please query_files to get the correct id, or use the provided 当前文件 ID.",
                error_type=EDIT_ERROR_INVALID_EDIT,
            )

        edits = args.get("edits")
        if edits is None:
            edits = args.get("operations", [])
        if not isinstance(edits, list):
            return _make_edit_error(
                "edit_file: invalid param 'edits' (must be an array).",
                error_type=EDIT_ERROR_INVALID_EDIT,
            )

        if ToolContext.pending_chapter_replacement(file_id) is not None or (
            ToolContext.get_current_agent() == _loop_agent_type()
            and ToolContext.pending_empty_file_for(file_id) is not None
        ):
            pending_entry = ToolContext.pending_empty_file_for(file_id) or {"title": file_id}
            return _make_edit_error(
                f"《{pending_entry['title']}》已定为待写入正文（等待 <file>…</file>），edit_file 未执行（没有改动）。"
                "请先在下一次回复用 <file>…</file> 写完它的正文，保存之后确需局部修改再调用 edit_file。",
                error_type=WRITE_CHAPTER_REPLACE_PENDING_ERROR_TYPE,
                id=file_id,
            )

        # 作者手改过的文件不再整份拒绝：作者改了什么见 <author_edits>，由模型保留或按作者的新要求改；
        # 这里只核对事实。模型本请求里没看过这个文件的全文时，old / anchor 只按逐字匹配，
        # 不拿可能过时的记忆去模糊匹配作者刚改过的字。有快照（看过或自己写过）就做陈旧检查。
        has_snapshot = ToolContext.read_snapshot(file_id) is not None
        has_seen = ToolContext.has_seen_file(file_id)
        seen_spans = None if has_seen else ToolContext.seen_file_spans(file_id)
        read_ticket = ToolContext.read_ticket()

        edit_kwargs: dict[str, Any] = {
            "id": file_id,
            "edits": edits,
            # 必须走 coerce_bool：strict_json_schema=False 让 schema 里的
            # "type": "boolean" 在运行时毫无约束力，模型常把它序列化成
            # "false"/"0"，而 bool("false") is True——一次编辑失败后本该中止的
            # 批量编辑会继续往下做，且整体仍被报成 success。
            "continue_on_error": coerce_bool(args.get("continue_on_error")),
            # 只规范化各 edit 的新文本，未编辑的段落不动。
            "normalize_quotes": True,
            # 在锁内取快照：同一请求里先写的那一步已经把快照更新为它写下的正文。
            "read_snapshot_fn": (lambda: ToolContext.read_snapshot(file_id)) if has_snapshot else None,
            "exact_only": not has_seen,
            "report_author_edits": True,
            # 单循环 agent 改正文：不整份替换（走 write_chapter）、不新添逐字重复的段落。
            "body_guards": ToolContext.get_current_agent() == _loop_agent_type(),
        }
        if seen_spans:
            edit_kwargs["seen_spans"] = seen_spans
        result = executor.edit_file(**edit_kwargs)
        # status 必须按实际结果降级：continue_on_error 下部分/全部 edit 失败时，
        # 恒返回 "success" 会让模型以为改动已经落地、继续往下写，
        # 失败的 edit 只藏在 data.failed_edits 里没人看。
        status = _derive_edit_status(result)
        if isinstance(result, dict):
            ui_overlay = extract_edit_file_ui_payload(result)
            if ui_overlay:
                ToolContext.record_tool_ui_payload(
                    ToolContext.current_tool_call_id(),
                    ui_overlay,
                    fallback_key=(tool_name, str(result.get("id") or file_id)),
                )
            llm_data: Any = slim_edit_file_result_for_llm(result)
        else:
            llm_data = result
        payload: dict[str, Any] = {
            "status": status,
            "mutation_applied": result.get("mutation_applied") is True,
            "data": llm_data,
        }
        if status == "error":
            # 全部失败（continue_on_error=true）：与整批回滚一样带上稳定分类和给作者的短句。
            failed = result.get("failed_edits") if isinstance(result, dict) else None
            first = failed[0] if isinstance(failed, list) and failed and isinstance(failed[0], dict) else {}
            error_type = str(first.get("error_type") or EDIT_ERROR_GENERIC)
            payload["error_type"] = error_type
            payload["user_message"] = edit_error_user_message(error_type)
        return _make_result(payload, tool_name=tool_name)
    except StaleContentError as e:
        # 新改动已经完整列给模型：它看到这条结果之后（下一次模型调用起）基于现在的正文改即可；
        # 同一条回复里排在后面的修改仍按旧快照判陈旧。列不全时要模型先读全文。
        if e.complete:
            ToolContext.record_read_snapshot(file_id, e.current_content, ticket=read_ticket)
        return _make_edit_error(str(e), **e.payload_fields())
    except EditFileError as e:
        # error 原样给模型（候选片段、occurrence 提示、全部失败项都在里面）；
        # error_type / user_message 给界面；edits_applied=0、mutation_applied=false
        # 明确告诉模型这次调用一处都没生效。
        return _make_edit_error(str(e), **e.payload_fields())
    except FileWriteBusyError as e:
        return _make_edit_error(str(e), error_type=EDIT_ERROR_FILE_BUSY)
    except PreWriteHistoryUnavailableError as e:
        # 原稿没能先进历史：提交前已回滚，一处都没改。
        return _make_edit_error(str(e), error_type=e.error_type, user_message=str(e))
    except NotFoundError as e:
        # 文件不在当前项目里与文件不存在对用户是同一回事（文案已统一为「文件不存在或已删除」）。
        return _make_edit_error(str(e), error_type=EDIT_ERROR_FILE_NOT_FOUND)
    except ForbiddenError as e:
        # 工具层自己的权限拒绝（不是内建的 PermissionError/OSError），都发生在写入之前。
        return _make_edit_error(str(e), error_type=EDIT_ERROR_PERMISSION_DENIED)
    except Exception as e:
        # 未分类异常可能发生在提交之后（例如台账记录），不能断言「一处都没生效」。
        return _make_edit_error(str(e), error_type=EDIT_ERROR_GENERIC, known_unapplied=False)


def _make_edit_error(
    error: str,
    *,
    error_type: str,
    known_unapplied: bool = True,
    **fields: Any,
) -> dict[str, Any]:
    """edit_file 的错误结果：模型用的 error + 稳定 error_type + 作者看的 user_message。

    known_unapplied=True 表示错误发生在写入之前，附上 edits_applied=0 /
    mutation_applied=false 让模型确知本次调用一处都没生效。
    """
    payload: dict[str, Any] = {
        "status": "error",
        "error": error,
        "error_type": error_type,
        "user_message": fields.pop("user_message", None) or edit_error_user_message(error_type),
    }
    if known_unapplied:
        payload["edits_applied"] = 0
        payload["mutation_applied"] = False
    payload.update(fields)
    return _make_mcp_payload(payload, tool_name="edit_file")


def _derive_edit_status(result: dict[str, Any]) -> str:
    """由 edit_file 的执行结果推导返回给 LLM 的 status。

    - 全部 edit 失败 → "error"（模型必须重新定位再试）
    - 部分成功部分失败 → "partial"（模型需要针对失败项补做）
    - 其余 → "success"
    """
    if not isinstance(result, dict):
        return "success"
    if coerce_bool(result.get("all_failed")):
        return "error"
    if coerce_bool(result.get("partial_success")):
        return "partial"
    # 兜底：即便上游没给汇总标志，只要有 failed_edits 也不能报纯 success
    failed_edits = result.get("failed_edits")
    if isinstance(failed_edits, list) and failed_edits:
        applied = result.get("edits_applied")
        applied_count = applied if isinstance(applied, int) else len(applied or [])
        return "partial" if applied_count else "error"
    return "success"


async def delete_file(args: dict[str, Any]) -> dict[str, Any]:
    """删除文件。"""
    return await _offload_write_tool("delete_file", _delete_file_sync, args)


def _delete_file_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous delete_file implementation."""
    tool_name = "delete_file"
    executor = ToolContext.get_executor()
    project_id = ToolContext._get_context().get("project_id")

    # 删除工具必须运行在绑定了项目的上下文中；执行器层会进一步校验目标文件
    # 属于该项目（check_file_access_in_tool_context）。
    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    raw_id = args.get("id", "")
    file_id = raw_id.strip() if isinstance(raw_id, str) else ""
    recursive = coerce_bool(args.get("recursive"))
    if (pending := ToolContext.pending_empty_file_for(file_id)) is not None and (
        not pending.get("created_here") or recursive
    ):
        return _make_structured_error(
            (
                f"《{pending['title']}》正在等待 <file>…</file> 写入替换正文，delete_file 未执行（没有改动）。"
                "若本轮不再重写而改为删除，请先结束当前待写状态或在下一次回复说明。"
            ),
            error_type=WRITE_CHAPTER_REPLACE_PENDING_ERROR_TYPE,
            tool_name=tool_name,
            id=file_id,
        )
    try:
        # recursive 是本项目破坏性最强的布尔参数：判真会软删除整棵子树。
        # 模型传来的 "false"/"0"/被截断修复出来的 "fals" 在朴素真值判断下全是
        # True，历史上把"删一个文件"变成"删掉整个文件夹的所有章节"。
        meta = executor.delete_file_with_meta(
            id=file_id,
            recursive=recursive,
        )
        if not isinstance(meta, dict):
            return _make_result(
                {"status": "success", "mutation_applied": False, "data": False},
                tool_name=tool_name,
            )
        if pending is not None:
            ToolContext.clear_pending_empty_file(file_id)
        data: dict[str, Any] = {
            "id": file_id,
            "restored": False,
            "mutation_applied": True,
        }
        if meta.get("id") == file_id:
            for key in ("title", "file_type", "deleted_at"):
                if isinstance(meta.get(key), str):
                    data[key] = meta[key]
        return _make_result(
            {"status": "success", "mutation_applied": True, "data": data},
            tool_name=tool_name,
        )
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


async def rename_file(args: dict[str, Any]) -> dict[str, Any]:
    """只改文件标题（#273）：不碰正文、不新建、不删除。"""
    return await _offload_write_tool("rename_file", _rename_file_sync, args)


def _rename_file_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous rename_file implementation: a title-only FileCRUD.update_file."""
    tool_name = "rename_file"
    executor = ToolContext.get_executor()
    project_id = ToolContext._get_context().get("project_id")
    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    file_id = args.get("id")
    title = args.get("title")
    if not isinstance(file_id, str) or not file_id.strip():
        return _make_error("rename_file 需要文件 id", tool_name=tool_name)
    if not isinstance(title, str) or not title.strip():
        return _make_error("rename_file 需要非空的新标题", tool_name=tool_name)
    file_id, title = file_id.strip(), title.strip()
    if (pending := ToolContext.pending_empty_file_for(file_id)) is not None:
        return _make_structured_error(
            (
                f"《{pending['title']}》正在等待 <file>…</file> 写入正文，rename_file 未执行（没有改动）。"
                "请先在下一次回复用 <file>…</file> 写完正文（write_chapter 替换时若已传入新章名，保存正文时会自动更新标题）；"
                "保存之后若仍需单独改文件标题，再调用 rename_file。"
            ),
            error_type=WRITE_CHAPTER_REPLACE_PENDING_ERROR_TYPE,
            tool_name=tool_name,
            id=file_id,
        )

    try:
        from models import File

        session = ToolContext.get_session()
        current = session.get(File, file_id)
        if current is None or current.is_deleted or current.project_id != project_id:
            return _make_error("文件不存在或已删除", tool_name=tool_name)
        previous_title = current.title
        if title == previous_title:
            data = {"id": file_id, "title": title, "previous_title": previous_title, "mutation_applied": False}
            return _make_result(
                {"status": "success", "mutation_applied": False, "data": data},
                tool_name=tool_name,
            )
        clash = session.exec(
            select(File.id).where(
                File.project_id == project_id,
                File.parent_id == current.parent_id,
                File.title == title,
                File.is_deleted.is_(False),
                File.id != file_id,
            )
        ).first()
        if clash is not None:
            return _make_error(
                f"同一文件夹里已有名为《{title}》的文件，换一个标题或先问作者",
                tool_name=tool_name,
            )

        result = executor.update_file(id=file_id, title=title)
        data = {
            "id": result.get("id", file_id),
            "title": result.get("title", title),
            "previous_title": previous_title,
            "file_type": result.get("file_type"),
            "parent_id": result.get("parent_id"),
            # SSE 的 tool_result 只转发 data：计费和前端进度从这里读是否真的改了。
            "mutation_applied": True,
        }
        return _make_result(
            {"status": "success", "mutation_applied": True, "data": data},
            tool_name=tool_name,
        )
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


async def query_files(args: dict[str, Any]) -> dict[str, Any]:
    """查询和搜索项目中的文件。"""
    return await asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, _query_files_sync, args)


def _query_files_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous query_files implementation."""
    tool_name = "query_files"
    executor = ToolContext.get_executor()
    project_id = ToolContext._get_context().get("project_id")

    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    try:
        # 不按 id、却要全文时（列表 × 全文），未显式给 limit 就收紧到 10 条：
        # 默认 50 条全文很容易顶到 TOOL_RESULT_MAX_CHARS，被截断后模型只能再读一遍。
        raw_id = args.get("id")
        has_id = isinstance(raw_id, str) and bool(raw_id.strip())
        raw_include = args.get("include_content")
        wants_full = str(args.get("response_mode") or "").strip().lower() == QUERY_FILES_RESPONSE_MODE_FULL or (
            raw_include is not None and coerce_bool(raw_include)
        )
        default_limit = (
            QUERY_FILES_FULL_MODE_DEFAULT_LIMIT if wants_full and not has_id else QUERY_FILES_DEFAULT_LIMIT
        )
        query_kwargs: dict[str, Any] = {
            "project_id": project_id,
            "query": args.get("query"),
            "file_type": args.get("file_type"),
            "file_types": args.get("file_types"),
            "parent_id": args.get("parent_id"),
            "limit": args.get("limit", default_limit),
            "offset": args.get("offset", 0),
        }

        optional_keys = ("id", "response_mode", "content_preview_chars", "include_content")
        for key in optional_keys:
            if key in args:
                value = args.get(key)
                if key == "include_content" and value is not None:
                    # 三值语义：None 表示"未指定，按 response_mode 决定"，必须原样透传；
                    # 显式传值时才强转——下游用的是 `include_content is True`，
                    # 模型传字符串 "true" 会被判成"不含正文"，语义直接反了。
                    value = coerce_bool(value)
                query_kwargs[key] = value

        range_read = has_id and wants_range(args)
        if range_read:
            # 范围读取先取全文再在这里切：切之前的整份正文用作陈旧检查的快照。
            query_kwargs["response_mode"] = QUERY_FILES_RESPONSE_MODE_FULL
            query_kwargs.pop("include_content", None)
            query_kwargs.pop("content_preview_chars", None)

        read_ticket = ToolContext.read_ticket() if has_id else None
        result = executor.query_files(**query_kwargs)
        keyword = args.get("query")
        if (
            not range_read
            and not wants_full
            and isinstance(keyword, str)
            and keyword.strip()
            and isinstance(result, list)
        ):
            _attach_query_matches(executor.session, result, keyword.strip())

        if range_read:
            rows = result if isinstance(result, list) else []
            if len(rows) == 1 and isinstance(rows[0], dict) and isinstance(rows[0].get("content"), str):
                full_row = rows[0]
                sliced = apply_content_range(full_row, args)
                result = [sliced]
                if sliced.get("content_partial"):
                    content_range = sliced.get("content_range")
                    span = (
                        (int(content_range["start"]), int(content_range["end"]))
                        if isinstance(content_range, dict)
                        and isinstance(content_range.get("start"), int)
                        and isinstance(content_range.get("end"), int)
                        else None
                    )
                    ToolContext.record_range_read_snapshot(
                        str(full_row.get("id") or ""),
                        full_row["content"],
                        span=span,
                        ticket=read_ticket,
                    )
                    return _make_result({"status": "success", "data": result}, tool_name=tool_name)

        payload = {"status": "success", "data": result}
        if has_id and len(_dumps_compact(payload)) <= TOOL_RESULT_MAX_CHARS:
            # 结果超过上限会被截断，模型拿不到全文：那时不记快照。
            _note_file_read(result, ticket=read_ticket)
        return _make_result(payload, tool_name=tool_name)
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


# 关键词查询的命中位置：每个文件最多列这么多处，每处前后各带这么多字原文。
QUERY_MATCHES_PER_FILE = 8
QUERY_MATCH_CONTEXT_CHARS = 30


def _attach_query_matches(session: Session, rows: list[Any], keyword: str) -> None:
    """给关键词查询的每个文件附上正文里的命中数和命中处原文（逐字，可直接作 edit_file 的 old/anchor）。

    以前只返回文件名和开头预览，改一个词要把每个命中文件读一遍全文。
    """
    from models import File

    ids = [row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)]
    if not ids:
        return
    contents = dict(session.exec(select(File.id, File.content).where(File.id.in_(ids))).all())
    for row in rows:
        if not isinstance(row, dict):
            continue
        content = contents.get(row.get("id")) or ""
        positions: list[int] = []
        start = content.find(keyword)
        while start >= 0:
            positions.append(start)
            start = content.find(keyword, start + len(keyword))
        row["match_count"] = len(positions)
        row["matches"] = [
            content[max(0, pos - QUERY_MATCH_CONTEXT_CHARS) : pos + len(keyword) + QUERY_MATCH_CONTEXT_CHARS]
            for pos in positions[:QUERY_MATCHES_PER_FILE]
        ]
        # summary 搜索行已有 matches[] 命中上下文，省略开头 200 字的 content_preview；
        # 仅标题命中（match_count == 0）仍保留 content_preview 帮助判断。
        if positions and "content" not in row:
            row.pop("content_preview", None)


def _note_file_read(result: Any, *, ticket: int | None = None) -> None:
    """按 id 读到全文时记下模型看到的正文（edit_file 的陈旧检查）。作者手改差异由 crud 附在同一行里。"""
    rows = result if isinstance(result, list) else []
    if len(rows) != 1 or not isinstance(rows[0], dict):
        return
    row = rows[0]
    file_id = str(row.get("id") or "")
    content = row.get("content")
    if file_id and isinstance(content, str) and not row.get("content_truncated"):
        ToolContext.record_read_snapshot(file_id, content, ticket=ticket)


def _normalize_update_project_args(args: dict[str, Any]) -> dict[str, Any]:
    """Normalize update_project args with backward-compatible alias mapping."""
    normalized = dict(args)
    for alias, canonical in PROJECT_STATUS_FIELD_ALIASES.items():
        if canonical in normalized or alias not in normalized:
            continue
        normalized[canonical] = normalized[alias]
    return normalized


_PHASE_BODY_TRIM_CHARS = " \t\r\n\u3000"


def _suggest_auto_current_phase_from_drafts(project_id: str) -> str | None:
    """
    Suggest monotonic chapter phase text from content files.

    Rules:
    - Only infer from content-file titles with explicit sequence numbers.
    - Only count files that have a non-blank body.
    - If existing current_phase has chapter number >= inferred, keep unchanged.
    - If existing current_phase is non-empty but not chapter-like, do not override.

    注意这里必须覆盖 CONTENT_FILE_TYPES 全部类型（draft + script），
    否则短剧项目（正文是 script）永远推不出 current_phase。
    """
    if not project_id:
        return None

    session = ToolContext.get_session()
    from models import File, Project
    from models.entities import PROJECT_TYPE_SCREENPLAY

    project = session.get(Project, project_id)
    if not project:
        return None

    content_titles = session.exec(
        select(File.title).where(
            and_(
                File.project_id == project_id,
                File.file_type.in_(CONTENT_FILE_TYPES),
                File.is_deleted.is_(False),
                # 只算写了正文的章：空标题壳不能把进度推成「已写至第20章」（F-10）。
                func.length(func.trim(File.content, _PHASE_BODY_TRIM_CHARS)) > 0,
            )
        )
    ).all()
    if not content_titles:
        return None

    inferred_latest = max(
        (
            seq
            for title in content_titles
            if (seq := extract_chapter_like_sequence_number(title)) is not None
        ),
        default=None,
    )
    if inferred_latest is None or inferred_latest <= 0:
        return None

    current_phase = (project.current_phase or "").strip()
    current_phase_chapter = extract_chapter_sequence_in_text(current_phase)
    if current_phase_chapter is not None and current_phase_chapter >= inferred_latest:
        return None
    if current_phase and current_phase_chapter is None:
        return None

    # live run 2 场景 A：短剧项目被写成「已写至第3章」。短篇也是按章写，只有剧本用「集」。
    unit = "集" if project.project_type == PROJECT_TYPE_SCREENPLAY else "章"
    return f"已写至第{inferred_latest}{unit}"


async def update_project(
    args: dict[str, Any],
    *,
    written_artifact_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """更新项目信息和任务计划。"""
    if written_artifact_ids is None:
        return await _offload_write_tool("update_project", _update_project_sync, args)
    return await asyncio.to_thread(
        _run_sync_tool_with_owned_session_cleanup,
        _update_project_sync,
        args,
        written_artifact_ids,
    )


NOTES_FULL_NOTE = "写作约定已接近 4000 字上限，这次没有记入；请作者在 AI 记忆里精简后再记。"


def _update_project_sync(
    args: dict[str, Any],
    written_artifact_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Synchronous update_project implementation."""
    tool_name = "update_project"
    executor = ToolContext.get_executor()
    project_id = ToolContext.get_project_id()
    session_id = ToolContext.get_session_id()
    user_id = ToolContext.get_user_id()

    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    try:
        normalized_args = _normalize_update_project_args(args)
        result = {}
        from_chat = isinstance(ToolContext._get_context().get("author_message"), str)
        if (
            "notes" in normalized_args
            and from_chat
            and (
                ToolContext._get_context().get("notes_append_allowed") is not True
                # Consumed steering is shared across SDK task/context snapshots.
                # Only subsequent tool calls narrow; completed writes are not undone.
                or bool(ToolContext._get_context().get("author_steering"))
            )
        ):
            normalized_args.pop("notes")
            result["notes_skipped_reason"] = "本轮未获追加许可或已有新的作者插话；不要重做历史记住请求。"

        # 应用内对话的 notes 只追加：已有作者内容逐字节保留，新条目作为普通备注追加，
        # 结果的 notes_added 列出存下的原文，结果卡可打开统一编辑面板。
        notes_added: list[str] = []
        notes_over_limit = False
        notes_transform: Callable[[str | None], str | None] | None = None
        notes_arg = normalized_args.get("notes")
        if isinstance(notes_arg, str):
            def transform_notes(existing_notes: str | None) -> str | None:
                nonlocal notes_added, notes_over_limit
                merged = merge_project_notes(existing_notes, notes_arg, from_chat=from_chat)
                notes_added = merged.added
                notes_over_limit = merged.over_limit
                # Unchanged notes are not written, so they do not count as an updated field (no
                # billable output).  A chat call that appends nothing does not write either, even
                # to drop a legacy proposal block: every read already hides it.
                if from_chat and not merged.added:
                    return None
                return None if merged.text == (existing_notes or "") else merged.text

            notes_transform = transform_notes

        # 更新项目状态（支持空字符串清空字段）
        status_keys = ["summary", "current_phase", "writing_style", "notes"]
        has_status_update_args = (
            any(k in normalized_args for k in status_keys)
            or notes_transform is not None
        )
        # App chat/model calls never carry human rename authority.  Direct human project APIs do
        # not use this tool and keep their existing behavior.
        title_arg = normalized_args.get("title")
        has_title_arg = title_arg is not None
        title_kwargs: dict[str, Any] = {}
        if has_title_arg:
            title_kwargs["title"] = title_arg
            title_kwargs["author_requested"] = False
        if has_status_update_args or has_title_arg:
            status_kwargs: dict[str, Any] = {
                "project_id": project_id,
                "summary": normalized_args.get("summary"),
                "current_phase": normalized_args.get("current_phase"),
                "writing_style": normalized_args.get("writing_style"),
                "notes": None if notes_transform is not None else normalized_args.get("notes"),
                **title_kwargs,
            }
            if notes_transform is not None:
                status_kwargs["notes_transform"] = notes_transform
            status_result = executor.update_project_status(
                **status_kwargs,
            )
            if notes_added or notes_over_limit:
                log_with_context(
                    logger,
                    20,
                    "update_project appended chat notes entries",
                    project_id=project_id,
                    added_count=len(notes_added),
                    over_limit=notes_over_limit,
                )
            result["project_status"] = status_result
            # Backward compatibility: keep lightweight summary fields at top level;
            # current_status already lives inside project_status and is not duplicated.
            result["project_id"] = status_result.get("project_id")
            result["updated_fields"] = status_result.get("updated_fields", [])
            # Also on a partial plan rejection: the other fields were still saved.
            if notes_added and "notes" in result["updated_fields"]:
                result["notes_added"] = notes_added
            if notes_over_limit:
                result["notes_note"] = NOTES_FULL_NOTE
            for key in (
                "project_name_updated",
                "project_name",
                "title_skipped",
                "title_error",
                "title_note",
            ):
                if key in status_result:
                    result[key] = status_result[key]

        # 更新任务计划（允许传空数组以清空任务板）
        plan_rejected = False
        if "tasks" in normalized_args and session_id:
            plan_kwargs: dict[str, Any] = {
                "session_id": session_id,
                "tasks": normalized_args.get("tasks", []),
                "user_id": user_id,
                "project_id": project_id,
            }
            if written_artifact_ids is not None:
                plan_kwargs["written_artifact_ids"] = written_artifact_ids
            plan_result = executor.execute_update_plan(
                **plan_kwargs,
            )
            result["plan"] = plan_result
            plan_rejected = (
                isinstance(plan_result, dict)
                and str(plan_result.get("status") or "").lower() in {"ignored", "error"}
            )

        # 任务板-only 更新是正常路径（prompt 和 workflow completion hook 都会这样调用）。
        # 这里仅做信息性记录，并尝试自动前推 current_phase（单调不回退）。
        if "tasks" in normalized_args and not has_status_update_args and not plan_rejected:
            tasks_arg = normalized_args.get("tasks", [])
            task_count = len(tasks_arg) if isinstance(tasks_arg, list) else None
            log_with_context(
                logger,
                20,  # INFO
                "update_project received task-board-only payload",
                project_id=project_id,
                session_id=session_id,
                task_count=task_count,
                arg_keys=sorted(normalized_args.keys()),
            )

            try:
                suggested_phase = _suggest_auto_current_phase_from_drafts(project_id)
                if suggested_phase:
                    status_result = executor.update_project_status(
                        project_id=project_id,
                        summary=None,
                        current_phase=suggested_phase,
                        writing_style=None,
                        notes=None,
                    )
                    result["project_status"] = status_result
                    result["project_id"] = status_result.get("project_id")
                    result["updated_fields"] = status_result.get("updated_fields", [])
                    log_with_context(
                        logger,
                        20,  # INFO
                        "Auto-synced project current_phase from draft progress",
                        project_id=project_id,
                        current_phase=suggested_phase,
                    )
            except Exception as exc:
                if hasattr(executor.session, "rollback"):
                    executor.session.rollback()
                log_with_context(
                    logger,
                    40,  # ERROR
                    "Failed to auto-sync current_phase from drafts",
                    project_id=project_id,
                    session_id=session_id,
                    error=str(exc),
                )

        if plan_rejected:
            project_mutated = bool(result.get("updated_fields")) or coerce_bool(
                result.get("project_name_updated")
            )
            plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
            return _make_result(
                {
                    "status": "partial" if project_mutated else "error",
                    "error_type": "update_plan_rejected",
                    "error": str(
                        plan.get("error")
                        or plan.get("message")
                        or "任务计划板未更新"
                    ),
                    "mutation_applied": project_mutated,
                    "data": result,
                },
                tool_name=tool_name,
            )

        return _make_result({"status": "success", "data": result}, tool_name=tool_name)
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


async def load_skill(args: dict[str, Any]) -> dict[str, Any]:
    """按名称加载一个已启用技能的完整方法与资源清单（技能 L2）。"""
    return await asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, _load_skill_sync, args)


def _resolve_skill_for_tool(session: Session, tool_name: str, name: str):
    """Resolve an active skill for the current user; returns (skill, same-name skills, error_result)."""
    from agent.skills.active_skills import (
        find_active_skill,
        load_active_skills,
        same_name_skills,
        suggest_skill_names,
    )

    user_id = ToolContext.get_user_id()
    if not user_id:
        return None, [], _make_error("user_id not set", tool_name=tool_name)
    if not name:
        return None, [], _make_error("name is required", tool_name=tool_name)

    skills = load_active_skills(session, user_id)
    skill = find_active_skill(skills, name)
    if skill is None:
        available = suggest_skill_names(skills, name)
        hint = f"可用技能：{'、'.join(available)}" if available else "当前用户没有启用中的技能"
        return None, [], _make_error(f"未找到已启用的技能「{name}」。{hint}", tool_name=tool_name)
    # 按 id 查到的就是指定的那个；按名称查到时把同名的其他技能告诉模型，需要时改传 id
    others = [] if name.strip() in (skill.id, skill.added_id) else same_name_skills(skills, skill)
    return skill, others, None


def _same_name_payload(others: list) -> dict[str, Any]:
    if not others:
        return {}
    return {
        "same_name_skills": [
            {"id": other.id, "name": other.name, "description": other.description[:200]}
            for other in others
        ],
        "same_name_note": "还有同名技能；如果要用的是其中某一个，用它的 id 作为 name 重新调用。",
    }


def _skill_budget_exhausted_error(tool_name: str) -> dict[str, Any]:
    from agent.skills.content_budget import SKILL_CONTENT_TOKEN_BUDGET

    return _make_error(
        f"本轮读取的技能内容已达上限（约 {SKILL_CONTENT_TOKEN_BUDGET} tokens）。"
        "请基于已加载的技能内容完成任务；还需要更多内容时，请用户在下一条消息中继续。",
        tool_name=tool_name,
    )


def _skill_limit_error(tool_name: str, skill_name: str) -> dict[str, Any]:
    from agent.skills.content_budget import MAX_LOADED_SKILLS_PER_REQUEST

    return _make_error(
        f"本轮最多加载 {MAX_LOADED_SKILLS_PER_REQUEST} 个不同的技能，已达上限，没有加载「{skill_name}」。"
        "请用已加载的技能完成任务；还需要其他技能时，请用户在下一条消息中继续。",
        tool_name=tool_name,
    )


def _segment_fields(segment, *, skill_id: str, path: str) -> dict[str, Any]:
    """截断时告诉模型如何续读（read_skill_resource + offset）。"""
    if not segment.truncated:
        return {"truncated": False}
    return {
        "truncated": True,
        "offset": segment.offset,
        "next_offset": segment.next_offset,
        "total_chars": segment.total_chars,
        "continue_hint": (
            f"内容过长，本次只返回了第 {segment.offset}–{segment.next_offset} 个字符（共 {segment.total_chars}）。"
            f"需要后续内容时调用 read_skill_resource(name=\"{skill_id}\", path=\"{path}\", "
            f"offset={segment.next_offset}) 分段读取。"
        ),
    }


def _load_skill_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous load_skill implementation."""
    from agent.skills.active_skills import list_active_skill_resources
    from agent.skills.content_budget import SKILL_BODY_PATH

    tool_name = "load_skill"
    name = str(args.get("name") or "").strip()
    try:
        session = ToolContext.get_session()
        skill, others, error = _resolve_skill_for_tool(session, tool_name, name)
        if error is not None:
            return error

        budget = ToolContext.get_skill_content_budget()
        if not budget.admit_skill(skill.id):
            return _skill_limit_error(tool_name, skill.name)
        if budget.remaining <= 0:
            return _skill_budget_exhausted_error(tool_name)
        segment = budget.take(skill.instructions)

        resources = [
            {"path": resource.path, "size": resource.size}
            for resource in list_active_skill_resources(session, skill)
        ]

        return _make_result({
            "status": "success",
            "data": {
                "skill_id": skill.id,
                "skill_name": skill.name,
                "source": skill.source,
                "instructions": segment.text,
                **_segment_fields(segment, skill_id=skill.id, path=SKILL_BODY_PATH),
                "resources": resources,
                **_same_name_payload(others),
            },
        }, tool_name=tool_name)
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


async def read_skill_resource(args: dict[str, Any]) -> dict[str, Any]:
    """读取已启用技能的一个参考文件（技能 L3）。"""
    return await asyncio.to_thread(
        _run_sync_tool_with_owned_session_cleanup, _read_skill_resource_sync, args
    )


def _read_skill_resource_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous read_skill_resource implementation."""
    from agent.skills.active_skills import get_skill_resource, list_active_skill_resources
    from agent.skills.content_budget import SKILL_BODY_PATH
    from agent.skills.package import normalize_resource_path

    tool_name = "read_skill_resource"
    name = str(args.get("name") or "").strip()
    # 与存储时一致做 NFC 归一化，模型给出的分解形式路径也能命中
    path = normalize_resource_path(str(args.get("path") or ""))
    try:
        offset = max(int(args.get("offset") or 0), 0)
    except (TypeError, ValueError):
        return _make_error("offset must be a non-negative integer", tool_name=tool_name)
    try:
        session = ToolContext.get_session()
        skill, _others, error = _resolve_skill_for_tool(session, tool_name, name)
        if error is not None:
            return error
        if not path:
            return _make_error("path is required", tool_name=tool_name)

        if path == SKILL_BODY_PATH:
            # 技能正文（load_skill 截断后按 offset 续读）
            content = skill.instructions
            resolved_path = SKILL_BODY_PATH
        else:
            resource = get_skill_resource(
                session,
                path,
                user_skill_id=skill.user_skill_id,
                public_skill_id=skill.public_skill_id,
            )
            if resource is None:
                available = [item.path for item in list_active_skill_resources(session, skill)]
                hint = f"可用资源：{'、'.join(available)}" if available else "该技能没有资源文件"
                return _make_error(f"技能「{skill.name}」中没有资源 {path}。{hint}", tool_name=tool_name)
            content = resource.content
            resolved_path = resource.path

        if offset > len(content):
            return _make_error(
                f"offset {offset} 超出内容长度（共 {len(content)} 个字符）", tool_name=tool_name
            )
        budget = ToolContext.get_skill_content_budget()
        if not budget.admit_skill(skill.id):
            return _skill_limit_error(tool_name, skill.name)
        if budget.remaining <= 0 and offset < len(content):
            return _skill_budget_exhausted_error(tool_name)
        segment = budget.take(content, offset)

        return _make_result({
            "status": "success",
            "data": {
                "skill_id": skill.id,
                "skill_name": skill.name,
                "path": resolved_path,
                "content": segment.text,
                **_segment_fields(segment, skill_id=skill.id, path=resolved_path),
            },
        }, tool_name=tool_name)
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


# Export all tools as a list for easy registration
MCP_TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "create_file": create_file,
    WRITE_CHAPTER_TOOL_NAME: write_chapter,
    "edit_file": edit_file,
    "delete_file": delete_file,
    "rename_file": rename_file,
    "query_files": query_files,
    "update_project": update_project,
    "load_skill": load_skill,
    "read_skill_resource": read_skill_resource,
}

