"""create_file / write_chapter 建了文件、正文没写成时的落库核验、恢复写入目标与回滚。

单循环 agent 的空文件纠偏与工具调用耗尽收尾、StreamAdapter 的流结束清理、create_file 的失败回滚共用。
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from utils.logger import get_logger, log_with_context

if TYPE_CHECKING:
    from agent.tools.tool_context import AgentRequestContext

logger = get_logger(__name__)

# 同一轮里为一个建好却没写正文的文件恢复正文写入、再跑一次 agent 的次数上限（空文件纠偏）。
# 有上限，模型一直写不成时不会无限循环；用完就回滚能确认的空文件并报告。
MAX_FILE_CORRECTION_ATTEMPTS = 2

# pending-empty-file 标记的落库核验结果
PENDING_BODY_EMPTY = "empty"
PENDING_BODY_WRITTEN = "written"
PENDING_BODY_GONE = "gone"
PENDING_BODY_UNVERIFIABLE = "unverifiable"


def probe_pending_file_body(
    file_id: str,
    *,
    request_context: AgentRequestContext | None = None,
) -> str:
    """核验 pending-empty-file 标记指向的文件正文当前是否真的为空。

    标记由 create_file(不带 content) 置上，只有 <file>…</file> 流式写入完成
    （或流结束）时才会清除；edit_file 把正文写进去并不会清除它。所以"标记仍在"
    只说明模型没走流式写入协议，不代表正文为空——必须落库核验，否则
    create_file(空) + edit_file(op=append) 这条常走路径会被误判成空文件。

    核验不了时（无 file_id / 无可用 session / 查询失败）返回 unverifiable，
    由调用方走不恢复写入的回滚 / 报告路径。这里用列级查询，绕开 ORM 身份映射里可能陈旧的
    实例，直接读数据库当前值。不在本请求项目里的文件按 gone 处理：不补写、不回滚。
    """
    if not file_id or request_context is None:
        return PENDING_BODY_UNVERIFIABLE

    try:
        from sqlmodel import select

        from models import File

        with request_context.session_scope() as session:
            row = session.exec(
                select(File.content, File.is_deleted, File.project_id).where(File.id == file_id)
            ).first()
    except Exception as e:
        logger.debug(f"Pending empty-file verification failed: {e}")
        return PENDING_BODY_UNVERIFIABLE

    if row is None:
        return PENDING_BODY_GONE
    content, is_deleted, row_project_id = row[0], row[1], row[2]
    project_id = request_context.project_id
    if is_deleted or (project_id and row_project_id != project_id):
        return PENDING_BODY_GONE
    return PENDING_BODY_EMPTY if not str(content or "").strip() else PENDING_BODY_WRITTEN


def resume_target_version(
    file_id: str,
    *,
    request_context: AgentRequestContext | None = None,
) -> tuple[str, str] | None:
    """空文件纠偏轮恢复正文写入前再读一次目标：返回 (updated_at.isoformat(), file_type)。

    只在同一项目、未删除、正文仍为空、不是文件夹时返回；其余情况（含读不到）返回 None，
    纠偏轮不恢复写入。StreamAdapter 再拿这个版本与 create_file 结果里的版本比较。
    """
    project_id = request_context.project_id if request_context is not None else None
    if not file_id or not project_id or request_context is None:
        return None
    try:
        from sqlmodel import select

        from models import File
        from models.file_model import FILE_TYPE_FOLDER

        with request_context.session_scope() as session:
            row = session.exec(
                select(File.content, File.is_deleted, File.project_id, File.updated_at, File.file_type).where(
                    File.id == file_id
                )
            ).first()
    except Exception:
        log_with_context(logger, 30, "Empty-file resume target read failed")
        return None
    if row is None:
        return None
    content, is_deleted, row_project_id, updated_at, file_type = row
    if (
        is_deleted
        or row_project_id != project_id
        or str(content or "").strip()
        or updated_at is None
        or str(file_type or "").strip().lower() == FILE_TYPE_FOLDER
    ):
        return None
    return updated_at.isoformat(), str(file_type or "")


def with_creation_versions(
    entries: list[dict[str, str]],
    *,
    request_context: AgentRequestContext | None = None,
) -> list[dict[str, str]]:
    """回滚候选带上各自建好时的版本（create_file 结果里的 updated_at，记在待写条目上）。"""
    targets: list[dict[str, str]] = []
    for entry in entries:
        version = (
            request_context.pending_empty_file_version(str(entry.get("file_id") or ""))
            if request_context is not None
            else None
        )
        targets.append({**entry, "updated_at": version} if version else dict(entry))
    return targets


def rollback_unfinished_empty_files(
    entries: list[dict[str, str]],
    *,
    request_context: AgentRequestContext | None = None,
) -> set[str]:
    """Soft-delete verified empty artifacts when correction cannot continue.

    每个条目要带上文件建好时的版本 ``updated_at``（见 ``with_creation_versions``）：只有库里的版本仍是它、
    正文仍为空、仍在本项目时才软删。之后被作者或别的写入动过（例如改了标题）、或拿不出建好时版本的
    文件一律保留，由调用方报告为未完成；不拿回滚时重新读到的版本冒充建好时的版本。
    """
    targets = [entry for entry in entries or [] if entry.get("file_id") and entry.get("updated_at")]
    if not targets or request_context is None:
        return set()

    try:
        with request_context.session_scope() as session:
            return _soft_delete_verified_empty_files(
                session, targets, project_id=request_context.project_id
            )
    except Exception as exc:
        logger.warning(
            "Failed to roll back unfinished empty files",
            exc_info=True,
            extra={"error": str(exc)},
        )
        return set()


def _soft_delete_verified_empty_files(
    session: Any,
    entries: list[dict[str, str]],
    *,
    project_id: str | None = None,
) -> set[str]:
    """在给定 session 上软删除仍为空、版本仍是建好时那个的文件；失败时回滚并抛出。"""
    from sqlalchemy import update
    from sqlmodel import select

    from config.datetime_utils import advance_timestamp, utcnow
    from models import File

    try:
        rolled_back: set[str] = set()
        with session.begin_nested():
            for entry in entries:
                file_id = entry["file_id"]
                # Read scalar columns rather than an ORM identity-map object,
                # then include the observed content and version in the UPDATE
                # predicate. If a user/other run writes (body, title, metadata)
                # between verification and rollback, rowcount becomes zero and
                # their write is preserved.
                row = session.exec(
                    select(File.content, File.is_deleted, File.project_id, File.updated_at).where(
                        File.id == file_id
                    )
                ).first()
                if row is None:
                    continue
                content, is_deleted, row_project_id, updated_at = row
                if (
                    row_project_id != project_id
                    or is_deleted
                    or str(content or "").strip()
                    or updated_at is None
                    or updated_at.isoformat() != entry["updated_at"]
                ):
                    continue

                now = utcnow()
                result = session.exec(
                    update(File)
                    .where(
                        File.id == file_id,
                        File.project_id == project_id,
                        File.is_deleted.is_(False),
                        File.content == content,
                        File.updated_at == updated_at,
                    )
                    .values(
                        is_deleted=True,
                        deleted_at=now,
                        updated_at=advance_timestamp(updated_at, now=now),
                    )
                )
                if result.rowcount == 1:
                    rolled_back.add(file_id)
        if rolled_back:
            session.commit()
        return rolled_back
    except Exception:
        with contextlib.suppress(Exception):
            session.rollback()
        raise


async def settle_empty_files_after_exhaustion(
    agent_type: str | None,
    english: bool = False,
    *,
    request_context: AgentRequestContext | None = None,
) -> dict[str, list[dict[str, str]]]:
    """工具调用轮数耗尽且还有 pending 空文件：核验 → 回滚空文件，返回耗尽卡上的文件事实。

    与空文件纠偏分支「配额用尽」时同一套核验与回滚，只是不再安排补写轮。返回
    ``unfinished_files``（留下来、仍没有正文的）与 ``rolled_back_files``（已撤销的空文件），
    哪个为空就不给哪个键；没有空文件时返回空字典，耗尽卡保持原样。
    """
    if request_context is None:
        return {}
    unfinished: list[dict[str, str]] = []
    for entry in request_context.get_pending_empty_files():
        file_id = str(entry.get("file_id") or entry.get("id") or "")
        title = str(entry.get("title") or ("Untitled" if english else "未命名"))
        body_state = await asyncio.to_thread(
            probe_pending_file_body, file_id, request_context=request_context
        )
        if body_state in (PENDING_BODY_WRITTEN, PENDING_BODY_GONE):
            request_context.clear_pending_empty_file(file_id)
            continue
        unfinished.append({"file_id": file_id, "title": title})
    if not unfinished:
        return {}

    rolled_back = await asyncio.to_thread(
        rollback_unfinished_empty_files,
        with_creation_versions(unfinished, request_context=request_context),
        request_context=request_context,
    )
    for entry in unfinished:
        if entry["file_id"] in rolled_back:
            request_context.clear_pending_empty_file(entry["file_id"])
    rolled_back_files = [e for e in unfinished if e["file_id"] in rolled_back]
    unresolved_files = [e for e in unfinished if e["file_id"] not in rolled_back]
    log_with_context(
        logger,
        30,  # WARNING
        "Tool-call exhaustion left empty files; rolled back what could be verified",
        agent_type=agent_type,
        unfinished_count=len(unfinished),
        rolled_back_count=len(rolled_back_files),
        unresolved_count=len(unresolved_files),
    )
    return {
        key: files
        for key, files in (("unfinished_files", unresolved_files), ("rolled_back_files", rolled_back_files))
        if files
    }
