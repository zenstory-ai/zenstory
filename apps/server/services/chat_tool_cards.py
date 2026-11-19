"""聊天恢复卡的显示状态。

新卡从持久 operation/deletion batch 校准状态，聊天 JSON 不作为恢复事实。
没有 operation_id 的历史卡仍保留原条件回滚与 JSON 撤销标记兼容。
"""

import contextlib
import json
from datetime import datetime
from typing import Any

from sqlmodel import Session, select

from config.datetime_utils import normalize_datetime_to_utc
from models import ChatMessage, ChatSession
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


# 带撤销锚点的 AI 修改卡：edit_file，以及单循环 agent 的整章替换（write_chapter）。
_UNDOABLE_CARD_TOOLS = frozenset({"create_file", "edit_file", "write_chapter", "rename_file"})


def _card_undo(call: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """一张 edit_file 卡的结果数据与撤销锚点（结果可能带一层 data）。"""
    result = call.get("result")
    data = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else result
    if not isinstance(data, dict):
        return None, None
    undo = data.get("undo")
    return data, undo if isinstance(undo, dict) else None


def _same_token(stored: Any, expected: datetime) -> bool:
    if not isinstance(stored, str):
        return False
    try:
        parsed = datetime.fromisoformat(stored.replace("Z", "+00:00"))
    except ValueError:
        return False
    return normalize_datetime_to_utc(parsed) == normalize_datetime_to_utc(expected)


def hydrate_recovery_card_states(
    session: Session,
    calls: list[dict[str, Any]],
    *,
    user_id: str,
    project_id: str,
) -> list[dict[str, Any]]:
    """Overlay durable edit/deletion state onto decoded chat cards in place.

    Chat JSON is a presentation cache.  New cards carry ``operation_id`` and
    use ``FileEditOperation.undone_at`` as the source of truth after refresh.
    Old cards without an operation keep their historical JSON behavior.
    """
    from models.file_deletion_batch import FileDeletionBatch
    from models.file_edit_operation import FileEditOperation

    operation_ids: set[str] = set()
    batch_ids: set[str] = set()
    preview_hashes: set[str] = set()
    for call in calls:
        if not isinstance(call, dict):
            continue
        _data, undo = _card_undo(call)
        operation_id = undo.get("operation_id") if isinstance(undo, dict) else None
        if isinstance(operation_id, str) and operation_id:
            operation_ids.add(operation_id)
        batch_id = _data.get("batch_id") if isinstance(_data, dict) else None
        if isinstance(batch_id, str) and batch_id:
            batch_ids.add(batch_id)
        preview = _data.get("preview") if isinstance(_data, dict) else None
        target_hash = preview.get("target_hash") if isinstance(preview, dict) else None
        if isinstance(target_hash, str) and target_hash:
            preview_hashes.add(target_hash)
    states = (
        {
            row.id: row.undone_at is not None
            for row in session.exec(
                select(FileEditOperation).where(
                    FileEditOperation.id.in_(operation_ids),  # type: ignore[attr-defined]
                    FileEditOperation.user_id == user_id,
                    FileEditOperation.project_id == project_id,
                )
            ).all()
        }
        if operation_ids
        else {}
    )
    restored_states = (
        {
            row.id: row.restored_at is not None
            for row in session.exec(
                select(FileDeletionBatch).where(
                    FileDeletionBatch.id.in_(batch_ids),  # type: ignore[attr-defined]
                    FileDeletionBatch.user_id == user_id,
                    FileDeletionBatch.project_id == project_id,
                )
            ).all()
        }
        if batch_ids
        else {}
    )
    confirmed_by_hash = (
        {
            row.target_hash: row
            for row in session.exec(
                select(FileDeletionBatch)
                .where(
                    FileDeletionBatch.target_hash.in_(preview_hashes),  # type: ignore[attr-defined]
                    FileDeletionBatch.user_id == user_id,
                    FileDeletionBatch.project_id == project_id,
                    FileDeletionBatch.source == "agent_confirmed",
                )
                .order_by(FileDeletionBatch.created_at.desc())  # type: ignore[attr-defined]
            ).all()
        }
        if preview_hashes
        else {}
    )
    for call in calls:
        if not isinstance(call, dict):
            continue
        _data, undo = _card_undo(call)
        operation_id = undo.get("operation_id") if isinstance(undo, dict) else None
        if isinstance(operation_id, str) and operation_id in states:
            undo["undone"] = states[operation_id]
        batch_id = _data.get("batch_id") if isinstance(_data, dict) else None
        if isinstance(batch_id, str) and batch_id in restored_states:
            _data["restored"] = restored_states[batch_id]
        preview = _data.get("preview") if isinstance(_data, dict) else None
        target_hash = preview.get("target_hash") if isinstance(preview, dict) else None
        confirmed = confirmed_by_hash.get(target_hash) if isinstance(target_hash, str) else None
        if confirmed is not None:
            items = json.loads(confirmed.items_json)
            _data.update(
                {
                    "batch_id": confirmed.id,
                    "targets": [
                        {key: item.get(key) for key in ("id", "title", "file_type", "parent_id", "order")}
                        for item in items
                    ],
                    "count": confirmed.item_count,
                    "restored": confirmed.restored_at is not None,
                    "confirmation_required": False,
                    "mutation_applied": True,
                }
            )
    return calls


# Backward-compatible name for callers landed during this feature branch.
hydrate_edit_operation_states = hydrate_recovery_card_states


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
    from agent.tools.file_ops.edit import acquire_file_write_lock
    from database import is_postgres

    try:
        message = session.get(ChatMessage, message_id)
        if message is None or message.role != "assistant" or not message.tool_calls:
            return False
        chat = session.get(ChatSession, message.session_id)
        if chat is None or chat.user_id != user_id or chat.project_id != project_id:
            return False
        lock_ctx = contextlib.nullcontext() if is_postgres else acquire_file_write_lock(message_id)
        with lock_ctx:
            return _mark_locked_card(
                session, message_id, file_id, tool_use_id, before_version_number, expected_after_updated_at
            )
    except Exception as exc:  # noqa: BLE001 - the undo itself already committed
        session.rollback()
        log_with_context(
            logger, 30, "Failed to record an undone edit card", file_id=file_id, error_type=type(exc).__name__
        )
        return False


def _mark_locked_card(
    session: Session,
    message_id: str,
    file_id: str,
    tool_use_id: str,
    before_version_number: int,
    expected_after_updated_at: datetime,
) -> bool:
    """锁住消息行后从最新值改写那一张卡（populate_existing：不用身份映射里的旧对象）。"""
    message = session.exec(
        select(ChatMessage)
        .where(ChatMessage.id == message_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).first()
    if message is None or not message.tool_calls:
        session.rollback()
        return False
    calls = json.loads(message.tool_calls)
    if not isinstance(calls, list):
        session.rollback()
        return False
    for call in calls:
        if not isinstance(call, dict) or call.get("id") != tool_use_id or call.get("name") not in _UNDOABLE_CARD_TOOLS:
            continue
        data, undo = _card_undo(call)
        if (
            data is None
            or undo is None
            or data.get("id") != file_id
            or type(undo.get("before_version_number")) is not int
            or undo["before_version_number"] != before_version_number
            or not _same_token(undo.get("expected_after_updated_at"), expected_after_updated_at)
        ):
            break
        undo["undone"] = True
        message.tool_calls = json.dumps(calls, ensure_ascii=False)
        session.add(message)
        session.commit()
        return True
    session.rollback()  # 没有可标的卡：释放行锁
    return False
