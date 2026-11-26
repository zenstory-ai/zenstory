"""ChatMessage 存储去重与压缩前历史 tool_calls 归档辅助。

职责：
1. 判断 ``display_events`` / ``message_metadata`` 是否已记录非空 ``thinking_content``，
   避免新轮次及历史回填把同一段思考过程双写进 ``chat_message.reasoning_content``。
2. 仅对 ``query_files`` 与旧版 ``parallel_execute`` 里的只读查询子任务剥离大体积正文载荷
   （``content`` / ``content_preview`` / ``matches`` / ``author_edits`` /
   ``file_metadata`` / ``content_range``），保留卡片展示与面包屑所需字段并附加简短
   ``archived_note``；绝不改动 ``edit_file`` / ``write_chapter`` / ``delete_file`` /
   ``create_file`` / ``update_project`` 或任何撤销 / 恢复卡元数据。
3. 在压缩点落库后，按与 ``loop/history.py`` 及 ``session_loader.py`` 严格一致的
   ``(created_at, id)`` 边界，对同会话内 ``<= covers_through_message_id`` 的历史助手行
   做尽力而为（fail-open）的批量归档；压缩点之后的活跃行保持原样。
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.orm import load_only
from sqlmodel import Session, select

from utils.file_write_lock import acquire_file_write_lock
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

ARCHIVED_QUERY_CONTENT_NOTE = "历史查询正文已归档，需要时请重新用 query_files 读取"
# 落库前对超大 query_files 载荷直接瘦身的阈值（字符）：短设定/角色卡原样落库供冷回放使用，
# 整章或多章大体积正文剥离正文并保留 content_sha256 + updated_at（供 session_read_snapshots 核验）。
PERSISTED_QUERY_FILES_MAX_CHARS = 4_000

# 仅剥离查询结果里的大体积正文/片段载荷，保留 id/title/file_type/status/error/error_type/
# user_message/count/match_count/word_count/content_length 等轻量字段。
_HEAVY_QUERY_FILE_KEYS: frozenset[str] = frozenset(
    {
        "content",
        "content_preview",
        "matches",
        "author_edits",
        "file_metadata",
        "content_range",
    }
)
# query_files 结果可能直接是文件 dict 列表，也可能是带容器键的外壳 dict。
_QUERY_CONTAINER_KEYS: tuple[str, ...] = ("data", "results", "files")
# legacy parallel_execute 里允许归档的只读查询子任务类型；写工具子任务一律原样保留。
_ARCHIVABLE_PARALLEL_TASK_TYPES: frozenset[str] = frozenset({"query_files", "hybrid_search"})
_ARCHIVE_BATCH_SIZE = 200


def display_events_have_thinking_content(display_events: Any) -> bool:
    """展示时间线列表是否已包含非空 ``thinking_content`` 块。"""
    if not isinstance(display_events, list):
        return False
    return any(
        isinstance(event, dict)
        and event.get("type") == "thinking_content"
        and isinstance(event.get("content"), str)
        and bool(event["content"].strip())
        for event in display_events
    )


def metadata_has_thinking_display_events(raw_metadata: str | None) -> bool:
    """持久化 ``message_metadata`` JSON 字符串是否已在 ``display_events`` 中保留非空思考正文。"""
    if not isinstance(raw_metadata, str) or '"thinking_content"' not in raw_metadata:
        return False
    try:
        payload = json.loads(raw_metadata)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False
    return display_events_have_thinking_content(payload.get("display_events"))


def _slim_query_file_dict(item: dict[str, Any], *, keep_content_sha256: bool = False) -> tuple[dict[str, Any], bool]:
    """剥离单个查询结果 dict 中的重载荷键并附加 ``archived_note``；无重载荷时原样返回。"""
    if not any(key in item for key in _HEAVY_QUERY_FILE_KEYS):
        return item, False
    slimmed = {key: value for key, value in item.items() if key not in _HEAVY_QUERY_FILE_KEYS}
    if keep_content_sha256:
        content = item.get("content")
        if (
            isinstance(content, str)
            and content
            and item.get("file_type") != "folder"
            and not item.get("content_truncated")
            and not item.get("content_partial")
        ):
            from services.features.file_version_service import content_sha256

            slimmed["content_sha256"] = content_sha256(content)
            slimmed.setdefault("content_length", len(content))
    slimmed["archived_note"] = ARCHIVED_QUERY_CONTENT_NOTE
    return slimmed, True


def _heavy_query_payload_chars(payload: Any, *, depth: int = 0) -> int:
    """Estimate serialized character length of heavy fields inside a query_files payload."""
    if depth > 4:
        return 0
    if isinstance(payload, list):
        return sum(_heavy_query_payload_chars(elem, depth=depth + 1) for elem in payload)
    if isinstance(payload, dict):
        total = 0
        for key, value in payload.items():
            if key in _HEAVY_QUERY_FILE_KEYS and value is not None:
                if isinstance(value, str):
                    total += len(value)
                else:
                    try:
                        total += len(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
                    except (TypeError, ValueError):
                        total += len(str(value))
            elif key in _QUERY_CONTAINER_KEYS and isinstance(value, list | dict):
                total += _heavy_query_payload_chars(value, depth=depth + 1)
        return total
    return 0


def _slim_query_result_payload(
    payload: Any, *, depth: int = 0, keep_content_sha256: bool = False
) -> tuple[Any, bool]:
    """归档 ``query_files``（或只读查询子任务）的 result 载荷，保持原有 list/dict 外形不变。"""
    if depth > 4:
        return payload, False
    if isinstance(payload, list):
        changed = False
        new_items: list[Any] = []
        for element in payload:
            if isinstance(element, dict):
                slimmed, item_changed = _slim_query_file_dict(
                    element, keep_content_sha256=keep_content_sha256
                )
                if item_changed:
                    changed = True
                new_items.append(slimmed)
            else:
                new_items.append(element)
        return (new_items, True) if changed else (payload, False)

    if isinstance(payload, dict):
        changed = False
        updated = dict(payload)
        for container_key in _QUERY_CONTAINER_KEYS:
            nested = updated.get(container_key)
            if isinstance(nested, list | dict):
                slimmed_nested, nested_changed = _slim_query_result_payload(
                    nested, depth=depth + 1, keep_content_sha256=keep_content_sha256
                )
                if nested_changed:
                    updated[container_key] = slimmed_nested
                    changed = True

        if any(key in updated for key in _HEAVY_QUERY_FILE_KEYS):
            slimmed_self, _ = _slim_query_file_dict(updated, keep_content_sha256=keep_content_sha256)
            updated = slimmed_self
            changed = True

        return (updated, True) if changed else (payload, False)

    return payload, False


def slim_query_files_result_for_persistence(
    payload: Any,
    *,
    max_chars: int = PERSISTED_QUERY_FILES_MAX_CHARS,
) -> Any:
    """落库前瘦身超大 ``query_files`` 结果，防止整章全文永久膨胀 PostgreSQL TOAST。

    - 重载荷总字符数 <= ``max_chars`` 时原样返回（短设定/小卡片在冷回放时仍可直接复用）；
    - 超出阈值时剥离 ``content`` / ``content_preview`` / ``matches`` 等大字段，保留卡片字段、
      ``updated_at`` 与全文 ``content_sha256``（供 ``SessionLoader.session_read_snapshots``
      在不落库正文的情况下仍能精确核验文件是否未变更）。
    """
    if payload is None or max_chars < 0:
        return payload
    if _heavy_query_payload_chars(payload) <= max_chars:
        return payload
    slimmed, changed = _slim_query_result_payload(payload, keep_content_sha256=True)
    return slimmed if changed else payload


def _slim_parallel_tasks_list(tasks: list[Any]) -> tuple[list[Any], bool]:
    """仅归档 ``parallel_execute`` 结果里只读查询子任务的 result，写类子任务原样保留。"""
    changed = False
    new_tasks: list[Any] = []
    for task in tasks:
        if not isinstance(task, dict):
            new_tasks.append(task)
            continue
        task_type = str(task.get("type") or "").strip()
        if task_type not in _ARCHIVABLE_PARALLEL_TASK_TYPES:
            new_tasks.append(task)
            continue
        slimmed_result, result_changed = _slim_query_result_payload(task.get("result"))
        if result_changed:
            changed = True
            new_task = dict(task)
            new_task["result"] = slimmed_result
            new_tasks.append(new_task)
        else:
            new_tasks.append(task)
    return (new_tasks, True) if changed else (tasks, False)


def _slim_parallel_execute_result(result: Any) -> tuple[Any, bool]:
    """归档旧版 ``parallel_execute`` 工具结果中的查询子任务载荷。"""
    if not isinstance(result, dict):
        return result, False

    tasks = result.get("tasks")
    if isinstance(tasks, list):
        slimmed_tasks, tasks_changed = _slim_parallel_tasks_list(tasks)
        if not tasks_changed:
            return result, False
        updated = dict(result)
        updated["tasks"] = slimmed_tasks
        return updated, True

    nested_data = result.get("data")
    if isinstance(nested_data, dict) and isinstance(nested_data.get("tasks"), list):
        slimmed_tasks, tasks_changed = _slim_parallel_tasks_list(nested_data["tasks"])
        if not tasks_changed:
            return result, False
        updated_data = dict(nested_data)
        updated_data["tasks"] = slimmed_tasks
        updated = dict(result)
        updated["data"] = updated_data
        return updated, True

    return result, False


def archive_historical_tool_calls_json(raw_tool_calls: str | None) -> str | None:
    """将持久化 ``tool_calls`` JSON 中的历史查询正文载荷归档瘦身。

    - 仅处理 ``query_files`` 与旧版 ``parallel_execute`` 的只读查询子任务结果；
    - 绝不修改 ``edit_file`` / ``write_chapter`` / ``delete_file`` / ``create_file`` /
      ``update_project`` 或任意撤销/恢复卡元数据；
    - 幂等：若无可归档重载荷，原样返回同一个 ``raw_tool_calls`` 字符串。
    """
    if (
        not isinstance(raw_tool_calls, str)
        or not raw_tool_calls
        or ('"query_files"' not in raw_tool_calls and '"parallel_execute"' not in raw_tool_calls)
    ):
        return raw_tool_calls

    try:
        calls = json.loads(raw_tool_calls)
    except (TypeError, ValueError):
        return raw_tool_calls

    if not isinstance(calls, list):
        return raw_tool_calls

    changed = False
    archived_calls: list[Any] = []
    for call in calls:
        if not isinstance(call, dict):
            archived_calls.append(call)
            continue
        name = str(call.get("name") or "").strip()
        if name == "query_files":
            slimmed_result, call_changed = _slim_query_result_payload(call.get("result"))
            if call_changed:
                changed = True
                archived_calls.append({**call, "result": slimmed_result})
            else:
                archived_calls.append(call)
        elif name == "parallel_execute":
            slimmed_result, call_changed = _slim_parallel_execute_result(call.get("result"))
            if call_changed:
                changed = True
                archived_calls.append({**call, "result": slimmed_result})
            else:
                archived_calls.append(call)
        else:
            archived_calls.append(call)

    if not changed:
        return raw_tool_calls

    return json.dumps(archived_calls, ensure_ascii=False, separators=(",", ":"))


def archive_pre_compaction_tool_calls(
    session: Session,
    *,
    chat_session_id: str,
    covers_through_message_id: str,
    batch_size: int = _ARCHIVE_BATCH_SIZE,
) -> int:
    """归档同会话内 ``<= covers_through_message_id`` 的助手行 ``tool_calls`` 查询载荷。

    - 严格按 ``(created_at, id)`` 边界匹配 ``loop/history.py`` 与 ``session_loader.py``；
    - 若锚点行在同会话中不存在（已被删），不改动任何行直接返回 0；
    - 逐行短锁（PostgreSQL ``FOR UPDATE SKIP LOCKED`` / SQLite 进程内条带锁）并重新读取最新
      ``tool_calls`` 后再归档，避免与并发撤销/恢复卡片回写互相覆盖，且永不阻塞用户请求。
    """
    from database import is_postgres
    from models import ChatMessage

    if not chat_session_id or not covers_through_message_id:
        return 0

    anchor = session.exec(
        select(ChatMessage.created_at, ChatMessage.id).where(
            ChatMessage.id == covers_through_message_id,
            ChatMessage.session_id == chat_session_id,
        )
    ).first()
    if anchor is None:
        return 0
    anchor_created_at, anchor_id = anchor

    archived_rows = 0
    cursor: tuple[Any, str] | None = None
    effective_batch = max(1, batch_size)

    while True:
        stmt = (
            select(ChatMessage.id, ChatMessage.created_at, ChatMessage.tool_calls)
            .where(
                and_(
                    ChatMessage.session_id == chat_session_id,
                    ChatMessage.role == "assistant",
                    ChatMessage.tool_calls.is_not(None),  # type: ignore[union-attr]
                    or_(
                        ChatMessage.tool_calls.contains('"query_files"'),  # type: ignore[union-attr]
                        ChatMessage.tool_calls.contains('"parallel_execute"'),  # type: ignore[union-attr]
                    ),
                    or_(
                        ChatMessage.created_at < anchor_created_at,
                        and_(
                            ChatMessage.created_at == anchor_created_at,
                            ChatMessage.id <= anchor_id,
                        ),
                    ),
                )
            )
            .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())  # type: ignore[union-attr]
            .limit(effective_batch)
        )
        if cursor is not None:
            cursor_created_at, cursor_id = cursor
            stmt = stmt.where(
                or_(
                    ChatMessage.created_at > cursor_created_at,
                    and_(
                        ChatMessage.created_at == cursor_created_at,
                        ChatMessage.id > cursor_id,
                    ),
                )
            )

        page = session.exec(stmt).all()
        if not page:
            break

        candidate_ids = [
            row_id
            for row_id, _created_at, raw_tool_calls in page
            if archive_historical_tool_calls_json(raw_tool_calls) != raw_tool_calls
        ]

        if candidate_ids:
            batch_updated = 0
            for row_id in candidate_ids:
                lock_ctx = contextlib.nullcontext() if is_postgres else acquire_file_write_lock(row_id)
                with lock_ctx:
                    row_stmt = (
                        select(ChatMessage)
                        .options(load_only(ChatMessage.id, ChatMessage.tool_calls))
                        .where(ChatMessage.id == row_id)
                        .execution_options(populate_existing=True)
                    )
                    if is_postgres:
                        row_stmt = row_stmt.with_for_update(skip_locked=True)
                    locked = session.exec(row_stmt).first()
                    if locked is None or not locked.tool_calls:
                        continue
                    slimmed = archive_historical_tool_calls_json(locked.tool_calls)
                    if slimmed != locked.tool_calls:
                        locked.tool_calls = slimmed
                        session.add(locked)
                        batch_updated += 1
            if batch_updated:
                session.commit()
                archived_rows += batch_updated

        last_id, last_created_at, _ = page[-1]
        cursor = (last_created_at, last_id)
        if len(page) < effective_batch:
            break

    return archived_rows


def archive_pre_compaction_tool_calls_safe(
    session: Session,
    *,
    chat_session_id: str,
    covers_through_message_id: str,
    batch_size: int = _ARCHIVE_BATCH_SIZE,
) -> int:
    """尽力而为地归档压缩边界之前的历史 ``tool_calls``；异常记日志并回滚，绝不抛出。"""
    try:
        archived_rows = archive_pre_compaction_tool_calls(
            session,
            chat_session_id=chat_session_id,
            covers_through_message_id=covers_through_message_id,
            batch_size=batch_size,
        )
        if archived_rows:
            log_with_context(
                logger,
                20,  # INFO
                "Archived pre-compaction historical tool_calls",
                session_id=chat_session_id,
                archived_rows=archived_rows,
            )
        return archived_rows
    except Exception as exc:  # noqa: BLE001 - fail open; main history save already committed
        with contextlib.suppress(Exception):
            session.rollback()
        log_with_context(
            logger,
            30,  # WARNING
            "Failed to archive pre-compaction historical tool_calls",
            session_id=chat_session_id,
            error_type=type(exc).__name__,
        )
        return 0
