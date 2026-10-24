"""聊天里 AI 修改卡的当前状态：作者撤销了这次修改之后，卡片不再显示「修改完成 / 撤销」。

状态写回原助手消息 tool_calls 里那一张卡（``result.undo.undone``），不改助手原话，
不新建表或第二份变更账本；撤销本身仍是既有的条件回滚（file_version_service.rollback_to_version）。
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
        if not isinstance(call, dict) or call.get("id") != tool_use_id or call.get("name") != "edit_file":
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
