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

from collections.abc import Mapping
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import SessionTransaction
from sqlmodel import Session, select

from models import File, Project
from models.file_model import FILE_TYPE_FOLDER
from utils.title_sequence import (
    MAX_FILE_ORDER as MAX_FILE_ORDER,
)
from utils.title_sequence import (
    extract_title_first_sequence_number,
    resolve_persisted_sequence_order,
    validate_persisted_file_order,
)

__all__ = [
    "MAX_FILE_ORDER",
    "ParentNotFoundError",
    "begin_file_creation_savepoint",
    "is_descendant_of",
    "load_live_subtree_postorder",
    "lock_project_for_files",
    "resolve_new_file_order",
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
    session: Session, project_id: str, *, rollback: bool = False, exclusive: bool = False
) -> Project | None:
    """Hold the tree/snapshot gate until the caller ends its transaction.

    Creators/reorder share this gate; rollback and graph/deletion writers exclude
    them but remain compatible with ordinary version writers' FK KEY SHARE.
    Acquire before validation or mutations. This does not provide a SQLite
    cross-process project lock, nor replace the caller's authorization checks.
    """
    from database import is_postgres

    query = select(Project).where(Project.id == project_id)
    if is_postgres:
        strong = rollback or exclusive
        query = query.with_for_update(read=not strong, key_share=strong)
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

    - 调用方显式给了 order：按 resolve_persisted_sequence_order 归一化——
      「第N章 / Chapter N」这类章节式标题以标题序号为准，覆盖显式值。
    - 没给 order 但标题/metadata 带序号：用该序号。
    - 都没有：排到同一父节点下现有兄弟的末尾。
    """
    if requested_order is not None:
        return resolve_persisted_sequence_order(
            requested_order,
            title=title,
            metadata=metadata,
            file_type=file_type,
        )

    sequence_number = extract_title_first_sequence_number(title, metadata)
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
