"""聊天里 AI 修改卡与删除卡的当前状态回写。

状态写回原助手消息 tool_calls 里那一张卡（``result.undo.undone`` 或 ``result.restored``），
不改助手原话，不新建表或第二份变更账本；版本撤销本身仍是既有的条件回滚
（file_version_service.rollback_to_version），删除恢复走文件软删除恢复接口。
"""

import contextlib
import json
from collections.abc import Callable, Collection
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from config.datetime_utils import normalize_datetime_to_utc
from models import ChatMessage, ChatMessageArchive, ChatSession
from models.chat_message_archive import has_chat_message_archive_table
from services.infra.redis_client import delete_agent_wire_prefix_cache
from utils.file_write_lock import acquire_file_write_lock
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


# 带撤销锚点的 AI 修改卡：edit_file、batch_replace_files（逐文件 undo），以及单循环 agent 的整章替换（write_chapter）。
_UNDOABLE_CARD_TOOLS = frozenset({"edit_file", "write_chapter", "batch_replace_files"})
_DELETE_CARD_TOOLS = frozenset({"delete_file"})
_FAILED_CARD_STATUSES = frozenset({"error", "failed", "pending"})


def _card_undo(call: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """一张修改卡的结果数据与撤销锚点（结果可能带一层 data）。"""
    result = call.get("result")
    data = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else result
    if not isinstance(data, dict):
        return None, None
    undo = data.get("undo")
    return data, undo if isinstance(undo, dict) else None


def _parse_card_args(call: dict[str, Any]) -> dict[str, Any]:
    for key in ("arguments", "input"):
        raw = call.get(key)
        if isinstance(raw, dict):
            return raw
        if isinstance(raw, str) and raw.strip():
            try:
                parsed = json.loads(raw)
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def _parse_iso_utc(stored: Any) -> datetime | None:
    if not isinstance(stored, str) or not stored.strip():
        return None
    try:
        parsed = datetime.fromisoformat(stored.replace("Z", "+00:00"))
    except ValueError:
        return None
    return normalize_datetime_to_utc(parsed)


def _same_token(stored: Any, expected: datetime) -> bool:
    parsed = _parse_iso_utc(stored)
    return parsed is not None and parsed == normalize_datetime_to_utc(expected)


def _verify_assistant_message(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    message_id: str,
) -> ChatMessage | ChatMessageArchive | None:
    message: ChatMessage | ChatMessageArchive | None = session.get(ChatMessage, message_id)
    if message is None and has_chat_message_archive_table(session.connection()):
        message = session.get(ChatMessageArchive, message_id)
    if message is None or message.role != "assistant" or not message.tool_calls:
        return None
    chat = session.get(ChatSession, message.session_id)
    if chat is None or chat.user_id != user_id or chat.project_id != project_id:
        return None
    return message


def _load_verified_calls(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    message_id: str,
) -> list[Any] | None:
    message = _verify_assistant_message(
        session, user_id=user_id, project_id=project_id, message_id=message_id
    )
    if message is None or not message.tool_calls:
        return None
    try:
        calls = json.loads(message.tool_calls)
    except ValueError:
        return None
    return calls if isinstance(calls, list) else None


def _find_card(
    calls: list[Any],
    *,
    tool_use_id: str,
    tool_names: Collection[str],
) -> dict[str, Any] | None:
    for call in calls:
        if isinstance(call, dict) and call.get("id") == tool_use_id and call.get("name") in tool_names:
            return call
    return None


def _match_edit_anchor(
    call: dict[str, Any],
    *,
    file_id: str,
    before_version_number: int,
    expected_after_updated_at: datetime,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    data, undo = _card_undo(call)
    if data is None:
        return None
    if call.get("name") == "batch_replace_files":
        if data.get("dry_run") is True:
            return None
        entries = data.get("files") if isinstance(data.get("files"), list) else (
            data.get("modified_files") if isinstance(data.get("modified_files"), list) else []
        )
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("id") != file_id:
                continue
            entry_undo = entry.get("undo")
            if (
                isinstance(entry_undo, dict)
                and type(entry_undo.get("before_version_number")) is int
                and entry_undo["before_version_number"] == before_version_number
                and _same_token(entry_undo.get("expected_after_updated_at"), expected_after_updated_at)
            ):
                return entry, entry_undo
        return None
    if (
        undo is None
        or data.get("id") != file_id
        or type(undo.get("before_version_number")) is not int
        or undo["before_version_number"] != before_version_number
        or not _same_token(undo.get("expected_after_updated_at"), expected_after_updated_at)
    ):
        return None
    return data, undo


def _mutate_locked_card(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    file_id: str,
    message_id: str,
    tool_use_id: str,
    tool_names: Collection[str],
    log_message: str,
    mutate: Callable[[dict[str, Any]], bool],
) -> bool:
    """锁住消息行后从最新值改写匹配的工具卡（populate_existing：不用身份映射里的旧对象）。"""
    from database import is_postgres

    try:
        verified = _verify_assistant_message(
            session, user_id=user_id, project_id=project_id, message_id=message_id
        )
        if verified is None:
            return False
        lock_ctx = contextlib.nullcontext() if is_postgres else acquire_file_write_lock(message_id)
        with lock_ctx:
            locked: ChatMessage | ChatMessageArchive | None = None
            if isinstance(verified, ChatMessage):
                locked = session.exec(
                    select(ChatMessage)
                    .where(ChatMessage.id == message_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).first()
            if locked is None and has_chat_message_archive_table(session.connection()):
                locked = session.exec(
                    select(ChatMessageArchive)
                    .where(ChatMessageArchive.id == message_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                ).first()
            if locked is None or not locked.tool_calls:
                session.rollback()
                return False
            calls = json.loads(locked.tool_calls)
            if not isinstance(calls, list):
                session.rollback()
                return False
            card = _find_card(calls, tool_use_id=tool_use_id, tool_names=tool_names)
            if card is not None and mutate(card):
                chat_session_id = locked.session_id
                locked.tool_calls = json.dumps(calls, ensure_ascii=False)
                session.add(locked)
                session.commit()
                delete_agent_wire_prefix_cache(chat_session_id)
                return True
            session.rollback()  # 没有可标的卡：释放行锁
            return False
    except Exception as exc:  # noqa: BLE001 - the file mutation itself already committed
        session.rollback()
        log_with_context(
            logger, 30, log_message, file_id=file_id, error_type=type(exc).__name__
        )
        return False


def read_delete_card_deleted_at(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    file_id: str,
    message_id: str,
    tool_use_id: str,
) -> datetime | None:
    """读取指定 delete_file 卡片上记录的 deleted_at 时间戳（用于根目录已自愈时的子树恢复核对）。"""
    calls = _load_verified_calls(
        session, user_id=user_id, project_id=project_id, message_id=message_id
    )
    if calls is None:
        return None
    card = _find_card(calls, tool_use_id=tool_use_id, tool_names=_DELETE_CARD_TOOLS)
    if card is None or str(card.get("status") or "").strip().lower() in _FAILED_CARD_STATUSES:
        return None
    data, _ = _card_undo(card)
    args = _parse_card_args(card)
    card_id = data.get("id") if isinstance(data, dict) else None
    if (
        not isinstance(data, dict)
        or data.get("mutation_applied") is not True
        or (card_id or args.get("id")) != file_id
    ):
        return None
    return _parse_iso_utc(data.get("deleted_at"))


def read_edit_card_before_title(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    file_id: str,
    message_id: str,
    tool_use_id: str,
    before_version_number: int,
    expected_after_updated_at: datetime,
) -> tuple[str | None, str | None]:
    """读取指定修改卡（write_chapter 整章替换）上记录的改名前标题与替换后标题。"""
    calls = _load_verified_calls(
        session, user_id=user_id, project_id=project_id, message_id=message_id
    )
    if calls is None:
        return None, None
    card = _find_card(calls, tool_use_id=tool_use_id, tool_names=_UNDOABLE_CARD_TOOLS)
    if card is None or str(card.get("status") or "").strip().lower() in _FAILED_CARD_STATUSES:
        return None, None
    matched = _match_edit_anchor(
        card,
        file_id=file_id,
        before_version_number=before_version_number,
        expected_after_updated_at=expected_after_updated_at,
    )
    if matched is None:
        return None, None
    data, undo = matched
    if undo.get("undone") is True:
        return None, None
    before_title = undo.get("before_title")
    if not isinstance(before_title, str) or not before_title.strip():
        return None, None
    after_title = data.get("title")
    return before_title.strip(), (
        after_title.strip() if isinstance(after_title, str) and after_title.strip() else None
    )


def record_delete_card_restored(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    file_id: str,
    message_id: str,
    tool_use_id: str,
    restored_deleted_at: datetime | None = None,
) -> bool:
    """一次删除恢复成功之后，把它对应的 delete_file 卡标成已恢复；返回是否标上。"""

    def _mark_restored(call: dict[str, Any]) -> bool:
        if str(call.get("status") or "").strip().lower() in _FAILED_CARD_STATUSES or call.get("error"):
            return False
        args = _parse_card_args(call)
        if call.get("result") is True and args.get("id") == file_id:
            call["result"] = {"id": file_id, "restored": True, "mutation_applied": True}
            return True
        data, _ = _card_undo(call)
        card_id = data.get("id") if isinstance(data, dict) else None
        if (
            not isinstance(data, dict)
            or "error" in data
            or data.get("mutation_applied") is not True
            or (card_id or args.get("id")) != file_id
        ):
            return False
        if (
            restored_deleted_at is not None
            and isinstance(data.get("deleted_at"), str)
            and not _same_token(data.get("deleted_at"), restored_deleted_at)
        ):
            return False
        data["restored"] = True
        return True

    return _mutate_locked_card(
        session,
        user_id=user_id,
        project_id=project_id,
        file_id=file_id,
        message_id=message_id,
        tool_use_id=tool_use_id,
        tool_names=_DELETE_CARD_TOOLS,
        log_message="Failed to record a restored delete card",
        mutate=_mark_restored,
    )


def record_edit_card_undone(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    file_id: str,
    message_id: str,
    tool_use_id: str,
    before_version_number: int,
    expected_after_updated_at: datetime,
) -> bool:
    """一次条件撤销成功之后，把它对应的那张 edit_file 卡标成已撤销；返回是否标上。

    只标调用方指明、并能证明来自这次撤销的那一张：消息是当前用户在同一项目里的助手消息，
    卡的工具调用 id 一致，撤销锚点（文件、修改前版本、修改后版本 token）与这次撤销完全相同。
    任何一项对不上就不标。撤销已经提交，标记失败只记日志。

    同一条消息里的几张卡可能同时撤销：读改写整份 tool_calls 时先锁住这一行（PostgreSQL 行锁；
    SQLite 用与文件写入同一套进程内条带锁），在锁下重新读最新内容，后提交的不会覆盖先提交的标记。
    """

    def _mark_undone(call: dict[str, Any]) -> bool:
        matched = _match_edit_anchor(
            call,
            file_id=file_id,
            before_version_number=before_version_number,
            expected_after_updated_at=expected_after_updated_at,
        )
        if matched is None:
            return False
        _, undo = matched
        undo["undone"] = True
        return True

    return _mutate_locked_card(
        session,
        user_id=user_id,
        project_id=project_id,
        file_id=file_id,
        message_id=message_id,
        tool_use_id=tool_use_id,
        tool_names=_UNDOABLE_CARD_TOOLS,
        log_message="Failed to record an undone edit card",
        mutate=_mark_undone,
    )
