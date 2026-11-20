"""文件树结构不变量的唯一权威实现。

这里集中放"父节点赋值是否合法"这类跨入口的规则。之所以要单独成模块：
REST 层（api/files.py）与 Agent 工具层（agent/tools/file_ops/crud.py）都要
做同一套校验，历史上各自写了一份，写法还不一致——Agent 那份漏了「parent
必须是 folder」，于是 AI 把章节挂到了普通文件下，而 GET /file-tree 只对
folder 递归子节点，导致刚写好的章节在文件树里彻底不可见。

放在 services/ 而不是 agent/ 下，是为了让 api/ 不必反向依赖 agent 包。
两个入口的差别只剩「异常怎么翻译」：本模块统一抛 ValueError，
REST 层捕获后转成带 error_code 的 APIException，Agent 工具层转成给模型看的
错误文本。规则本身只有这一份。
"""

import contextlib
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import SessionTransaction
from sqlmodel import Session, col, select

from config.datetime_utils import advance_timestamp, normalize_datetime_to_utc, utcnow
from models import File, Project
from models.file_model import FILE_TYPE_FOLDER
from utils.file_write_lock import file_write_lock
from utils.title_sequence import (
    MAX_FILE_ORDER as MAX_FILE_ORDER,
)
from utils.title_sequence import (
    extract_sequence_number_with_metadata,
    resolve_persisted_sequence_order,
    validate_persisted_file_order,
)

__all__ = [
    "FileRestoreConflictError",
    "FileRestoreNotFoundError",
    "MAX_FILE_ORDER",
    "ParentNotFoundError",
    "apply_file_title_and_order",
    "begin_file_creation_savepoint",
    "has_sibling_title_conflict",
    "is_descendant_of",
    "load_file_for_write",
    "load_live_subtree_postorder",
    "lock_project_for_files",
    "resolve_new_file_order",
    "restore_deleted_file",
    "validate_parent_assignment",
]


def begin_file_creation_savepoint(session: Session) -> SessionTransaction:
    """Keep canonical-folder repair inside the caller's real transaction.

    Legacy sqlite3 does not BEGIN for reads or SAVEPOINT. Releasing a first-write
    savepoint would therefore commit the folder before the enclosing creation.
    Do not change the engine's global transaction policy or restart an existing
    transaction; PostgreSQL uses the normal Session savepoint unchanged.
    """
    if session.get_bind().dialect.name == "sqlite":
        connection = session.connection()
        if not connection.connection.driver_connection.in_transaction:
            connection.exec_driver_sql("BEGIN")
    return session.begin_nested()


def lock_project_for_files(
    session: Session, project_id: str, *, exclusive: bool = False
) -> Project | None:
    """Hold the file-tree gate until the caller ends its transaction.

    Creators/reorder share this gate; graph/deletion writers exclude them but
    remain compatible with ordinary version writers' FK KEY SHARE.
    Acquire before validation or mutations. This does not provide a SQLite
    cross-process project lock, nor replace the caller's authorization checks.
    """
    from database import is_postgres

    query = select(Project).where(Project.id == project_id)
    if is_postgres:
        query = query.with_for_update(read=not exclusive, key_share=exclusive)
    return session.exec(query.execution_options(populate_existing=True)).first()


def load_live_subtree_postorder(session: Session, root: File) -> list[File]:
    """Load and lock the subtree under the caller's exclusive Project gate.

    The caller supplies a freshly loaded root and owns the transaction. Include
    that root even when deleted (Web re-delete compatibility), but descend only
    through active same-project children. UNION deduplicates IDs to terminate
    legacy cycles; the explicit DFS returns stable child-ID postorder once each.
    """
    from database import is_postgres

    subtree = select(File.id).where(
        File.id == root.id, File.project_id == root.project_id,
    ).cte("live_subtree", recursive=True)
    subtree = subtree.union(
        select(File.id).join(subtree, File.parent_id == subtree.c.id).where(
            File.project_id == root.project_id, File.is_deleted.is_(False),
        )
    )
    query = select(File).join(subtree, File.id == subtree.c.id).order_by(File.id)
    if is_postgres:
        query = query.with_for_update(key_share=True, of=File)
    rows = session.exec(query.execution_options(populate_existing=True)).all()

    by_id = {row.id: row for row in rows}
    children: dict[str, list[str]] = {}
    for row in rows:
        if row.parent_id is not None:
            children.setdefault(row.parent_id, []).append(row.id)

    postorder: list[File] = []
    visited: set[str] = set()
    stack = [(root.id, False)] if rows else []
    while stack:
        file_id, expanded = stack.pop()
        if expanded:
            postorder.append(by_id[file_id])
        elif file_id not in visited:
            visited.add(file_id)
            stack.append((file_id, True))
            stack.extend((child_id, False) for child_id in reversed(children.get(file_id, [])))
    return postorder


class ParentNotFoundError(ValueError):
    """父节点不存在/跨项目/已删除。

    继承 ValueError，Agent 工具层沿用原有的 `except ValueError` 捕获即可；
    REST 层用它区分出 FILE_NOT_FOUND，其余不变量违例仍是 VALIDATION_ERROR，
    保持既有 REST 契约不变。
    """


def is_descendant_of(
    session: Session,
    file_id: str,
    candidate_parent_id: str | None,
    *,
    refresh_nodes: bool = False,
) -> bool:
    """判断 candidate_parent_id 是否落在 file_id 的后代链上（含 candidate 就是 file_id 本身）。

    从候选父节点沿 parent_id 一路向上走，若途中撞见 file_id，说明把 file_id
    挂到候选父节点下会成环。visited 集合保证历史脏数据里已存在的环不会把
    这里打成死循环。
    """
    if not candidate_parent_id:
        return False

    visited: set[str] = set()
    current_id: str | None = candidate_parent_id

    while current_id:
        if current_id in visited:
            # 已有环：继续走下去也不会再遇到新节点
            return False
        if current_id == file_id:
            return True
        visited.add(current_id)

        current = session.get(File, current_id, populate_existing=refresh_nodes)
        if current is None:
            return False
        current_id = current.parent_id

    return False


def validate_parent_assignment(
    session: Session,
    project_id: str,
    parent_id: str | None,
    *,
    moving_file_id: str | None = None,
    refresh_parent: bool = False,
) -> str | None:
    """校验父节点赋值的全部不变量。

    - parent 必须存在、未删除、属于同一项目
    - parent 必须是 folder
    - 移动文件时不能把它挂到自己或自己的后代下（成环）

    Returns:
        校验通过的 parent_id（parent_id 为 None 时原样返回 None）。

    Raises:
        ValueError: 任一不变量不满足。第一项（不存在/跨项目/已删除）与后两项
            在 REST 层对应不同的 error_code，调用方靠 `not_found` 属性区分，
            见 ParentNotFoundError。
    """
    if parent_id is None:
        return None

    parent = session.get(File, parent_id, populate_existing=refresh_parent)
    if not parent or parent.is_deleted or parent.project_id != project_id:
        raise ParentNotFoundError(
            f"Parent file {parent_id} not found in project {project_id}"
        )

    if parent.file_type != FILE_TYPE_FOLDER:
        raise ValueError(
            f"Parent file {parent_id} is not a folder "
            f"(file_type={parent.file_type}); 文件只能挂在文件夹下"
        )

    if moving_file_id and is_descendant_of(
        session, moving_file_id, parent_id, refresh_nodes=refresh_parent,
    ):
        raise ValueError("不能把文件移动到它自己或它的子节点下")

    return parent_id


def resolve_new_file_order(
    session: Session,
    project_id: str,
    parent_id: str | None,
    *,
    title: str,
    metadata: Mapping[str, Any] | None,
    file_type: str,
    requested_order: int | None,
) -> int:
    """新建文件时要落库的 order。Web 与 Agent API 的建文件入口共用这一份。

    - 非零显式 order 是调用方提供的结构化事实，原样保留（含负数）。
    - order 未给或为零时，先用类型兼容的 metadata，再用标题开头的序号。
    - 都没有：排到同一父节点下现有兄弟的末尾。
    """
    if requested_order not in (None, 0):
        return resolve_persisted_sequence_order(
            requested_order,
            title=title,
            metadata=metadata,
            file_type=file_type,
        )

    sequence_number = extract_sequence_number_with_metadata(
        title, metadata, file_type=file_type,
    )
    resolved_order: int
    if sequence_number is not None:
        resolved_order = validate_persisted_file_order(sequence_number)
        return resolved_order

    max_order = session.exec(
        select(func.max(File.order)).where(
            File.project_id == project_id,
            File.parent_id == parent_id,
            File.is_deleted.is_(False),
        )
    ).one()
    resolved_order = validate_persisted_file_order(0 if max_order is None else int(max_order) + 1)
    return resolved_order


def has_sibling_title_conflict(
    session: Session,
    *,
    project_id: str,
    parent_id: str | None,
    title: str,
    exclude_file_id: str | None = None,
) -> bool:
    """检查同一目录下是否存在同名的未删除兄弟文件。"""
    query = select(File.id).where(
        File.project_id == project_id,
        File.parent_id == parent_id,
        File.title == title,
        File.is_deleted.is_(False),
    )
    if exclude_file_id is not None:
        query = query.where(File.id != exclude_file_id)
    return session.exec(query).first() is not None


def apply_file_title_and_order(file: File, new_title: str) -> None:
    """更新文件标题并按既有规则同步序号排序。"""
    file.title = new_title
    file.order = resolve_persisted_sequence_order(
        file.order,
        title=new_title,
        metadata=file.get_metadata(),
        file_type=file.file_type,
    )


class FileRestoreNotFoundError(LookupError):
    """Target file for restore does not exist."""


class FileRestoreConflictError(ValueError):
    """Restore cannot complete due to missing live parent or sibling title conflict."""


def load_file_for_write(session: Session, file_id: str) -> File | None:
    """读取待写入的文件行（PG 用 FOR NO KEY UPDATE 行锁，SQLite 强制重新 SELECT）。"""
    from database import is_postgres

    if is_postgres:
        return session.exec(
            select(File)
            .where(File.id == file_id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        ).first()
    return session.get(File, file_id, populate_existing=True)


def _collect_deleted_descendants(
    session: Session,
    project_id: str,
    root_id: str,
    target_stamp: datetime,
    *,
    is_postgres: bool,
) -> list[File]:
    descendants: list[File] = []
    frontier = [root_id]
    seen = {root_id}
    while frontier:
        query = select(File).where(
            File.project_id == project_id,
            col(File.parent_id).in_(frontier),
            File.is_deleted.is_(True),
        )
        if is_postgres:
            query = query.with_for_update(key_share=True)
        rows = session.exec(query.execution_options(populate_existing=True)).all()
        frontier = []
        for row in rows:
            if (
                row.id not in seen
                and row.deleted_at is not None
                and normalize_datetime_to_utc(row.deleted_at) == target_stamp
            ):
                seen.add(row.id)
                descendants.append(row)
                if row.file_type == FILE_TYPE_FOLDER:
                    frontier.append(row.id)
    return descendants


def _ensure_no_live_title_conflicts(session: Session, project_id: str, items: list[File]) -> None:
    for item in items:
        parent_clause = (
            File.parent_id.is_(None)
            if item.parent_id is None
            else File.parent_id == item.parent_id
        )
        clash = session.exec(
            select(File.id).where(
                File.project_id == project_id,
                parent_clause,
                File.title == item.title,
                File.is_deleted.is_(False),
                File.id != item.id,
            )
        ).first()
        if clash is not None:
            raise FileRestoreConflictError(
                f"原位置已存在同名文件《{item.title}》，请先改名后再恢复"
            )


def restore_deleted_file(
    session: Session,
    *,
    file_id: str,
    user_id: str,
    verify_ownership: Callable[[str], None],
    chat_message_id: str | None = None,
    tool_use_id: str | None = None,
) -> dict[str, Any]:
    """Restore a soft-deleted file (and descendants stamped with the same deletion timestamp)."""
    from database import is_postgres
    from services.chat_tool_cards import read_delete_card_deleted_at, record_delete_card_restored

    file = session.get(File, file_id)
    if not file:
        raise FileRestoreNotFoundError(file_id)
    verify_ownership(file.project_id)
    project_id = file.project_id

    card_deleted_at = (
        read_delete_card_deleted_at(
            session,
            user_id=user_id,
            project_id=project_id,
            file_id=file_id,
            message_id=chat_message_id,
            tool_use_id=tool_use_id,
        )
        if chat_message_id and tool_use_id
        else None
    )

    lock_project_for_files(session, project_id, exclusive=True)
    lock_ctx = contextlib.nullcontext() if is_postgres else file_write_lock(file_id)
    with lock_ctx:
        file = load_file_for_write(session, file_id)
        if not file:
            raise FileRestoreNotFoundError(file_id)
        verify_ownership(file.project_id)

        if not file.is_deleted:
            # Canonical root folders (e.g. `{project_id}-draft-folder`) may be auto-repaired live
            # before the user clicks Restore on the folder's delete card; still recover descendants
            # stamped with that card's exact `deleted_at`.
            restored_stamp = card_deleted_at
            targets = (
                _collect_deleted_descendants(
                    session, project_id, file.id, card_deleted_at, is_postgres=is_postgres
                )
                if file.file_type == FILE_TYPE_FOLDER and card_deleted_at is not None
                else []
            )
        else:
            if file.parent_id is not None:
                parent = session.get(File, file.parent_id, populate_existing=True)
                if (
                    not parent
                    or parent.is_deleted
                    or parent.project_id != project_id
                    or parent.file_type != FILE_TYPE_FOLDER
                ):
                    raise FileRestoreConflictError("上级文件夹不存在或已删除，无法恢复到原位置")
            restored_stamp = (
                normalize_datetime_to_utc(file.deleted_at) if file.deleted_at is not None else None
            )
            targets = [file]
            if file.file_type == FILE_TYPE_FOLDER and restored_stamp is not None:
                targets.extend(
                    _collect_deleted_descendants(
                        session, project_id, file.id, restored_stamp, is_postgres=is_postgres
                    )
                )

        restored_title = file.title
        if targets:
            _ensure_no_live_title_conflicts(session, project_id, targets)
            now = utcnow()
            for item in targets:
                item.is_deleted = False
                item.deleted_at = None
                item.updated_at = advance_timestamp(item.updated_at, now=now)
                session.add(item)
            restored_ids = [item.id for item in targets]
            already_restored = False
            session.commit()
        else:
            restored_ids = []
            already_restored = True

    if chat_message_id and tool_use_id:
        record_delete_card_restored(
            session,
            user_id=user_id,
            project_id=project_id,
            file_id=file_id,
            message_id=chat_message_id,
            tool_use_id=tool_use_id,
            restored_deleted_at=restored_stamp,
        )

    return {
        "id": file_id,
        "title": restored_title,
        "restored": True,
        "already_restored": already_restored,
        "restored_count": len(restored_ids),
        "file_ids": restored_ids,
    }
