"""Durable AI edit operations and selective undo."""

from __future__ import annotations

import contextlib
from datetime import datetime
from typing import Any, Literal

from sqlmodel import Session, select

from config.datetime_utils import advance_timestamp, normalize_datetime_to_utc, utcnow
from models import File, FileVersion, Project
from models.file_edit_operation import EDIT_OPERATION_KINDS, FileEditOperation
from models.file_version import (
    CHANGE_SOURCE_AI,
    CHANGE_SOURCE_SYSTEM,
    CHANGE_TYPE_AI_EDIT,
    CHANGE_TYPE_EDIT,
    CHANGE_TYPE_RESTORE,
)
from services.features.file_version_service import FileVersionService, content_sha256
from services.file_tree_rules import lock_project_for_files


class EditOperationNotFoundError(ValueError):
    pass


class EditOperationConflictError(ValueError):
    def __init__(self, reason: str, detail: dict[str, Any] | None = None) -> None:
        self.reason = reason
        self.detail = detail or {}
        super().__init__(reason)


def _ensure_exact_version(
    session: Session,
    *,
    file: File,
    content: str,
    change_summary: str,
) -> FileVersion:
    """Return an exact latest anchor, creating a quota-free backup version if needed."""
    service = FileVersionService()
    latest = service.get_latest_version(session, file.id)
    if latest is not None and (
        service.get_content_at_version(session, file.id, latest.version_number) == content
    ):
        return latest
    return service.create_version(
        session=session,
        file_id=file.id,
        new_content=content,
        change_type=CHANGE_TYPE_EDIT,
        change_source=CHANGE_SOURCE_SYSTEM,
        change_summary=change_summary,
        force_base=True,
        skip_quota=True,
        commit=False,
    )


def create_edit_operation(
    session: Session,
    *,
    user_id: str,
    project_id: str,
    file_id: str,
    kind: str,
    before_version: FileVersion,
    after_version: FileVersion,
    before_title: str,
    after_title: str,
    before_order: int,
    after_order: int,
    after_updated_at: datetime,
    after_content: str,
) -> FileEditOperation:
    """Stage a required operation row in the caller's file transaction."""
    if kind not in EDIT_OPERATION_KINDS:
        raise ValueError(f"Unsupported edit operation kind: {kind}")
    if before_version.file_id != file_id or after_version.file_id != file_id:
        raise ValueError("Edit operation versions must belong to the target file")
    operation = FileEditOperation(
        user_id=user_id,
        project_id=project_id,
        file_id=file_id,
        kind=kind,
        before_version_id=before_version.id,
        after_version_id=after_version.id,
        before_title=before_title,
        after_title=after_title,
        before_order=before_order,
        after_order=after_order,
        after_updated_at=normalize_datetime_to_utc(after_updated_at),
        after_content_sha256=content_sha256(after_content),
    )
    session.add(operation)
    session.flush()
    return operation


def stage_recoverable_file_mutation(
    session: Session,
    *,
    user_id: str,
    file: File,
    before_content: str,
    after_content: str,
    before_title: str,
    after_title: str,
    before_order: int,
    after_order: int,
    kind: str,
    change_summary: str,
) -> dict[str, Any]:
    """Stage file fields, exact history anchors and an operation; never commit.

    The caller owns the file/project lock and transaction.  Any failure raises
    ``FileWriteRecoveryError`` after rolling back the transaction, so no caller
    can persist an AI mutation without its durable recovery record.
    """
    from agent.tools.write_errors import (
        FileWriteRecoveryError,
        PreWriteHistoryUnavailableError,
    )

    try:
        before_version = _ensure_exact_version(
            session,
            file=file,
            content=before_content,
            change_summary="Before AI edit",
        )
    except PreWriteHistoryUnavailableError:
        session.rollback()
        raise
    except Exception as exc:
        session.rollback()
        raise PreWriteHistoryUnavailableError(file.id, action=kind) from exc

    try:
        file.content = after_content
        file.title = after_title
        file.order = after_order
        file.updated_at = advance_timestamp(file.updated_at, now=utcnow())
        session.add(file)
        if after_content == before_content:
            after_version = before_version
        else:
            after_version = FileVersionService().create_version(
                session=session,
                file_id=file.id,
                new_content=after_content,
                change_type=CHANGE_TYPE_AI_EDIT,
                change_source=CHANGE_SOURCE_AI,
                change_summary=change_summary,
                skip_quota=True,
                commit=False,
            )
        operation = create_edit_operation(
            session,
            user_id=user_id,
            project_id=file.project_id,
            file_id=file.id,
            kind=kind,
            before_version=before_version,
            after_version=after_version,
            before_title=before_title,
            after_title=after_title,
            before_order=before_order,
            after_order=after_order,
            after_updated_at=file.updated_at,
            after_content=after_content,
        )
    except Exception as exc:
        session.rollback()
        raise FileWriteRecoveryError(file.id, action=kind) from exc

    return {
        "operation_id": operation.id,
        "before_version_number": before_version.version_number,
        "expected_after_updated_at": normalize_datetime_to_utc(file.updated_at).isoformat(),
        "expected_after_content_sha256": operation.after_content_sha256,
    }


def _snapshots(
    session: Session, operation: FileEditOperation, file: File
) -> dict[str, dict[str, Any]]:
    service = FileVersionService()
    before = session.get(FileVersion, operation.before_version_id)
    after = session.get(FileVersion, operation.after_version_id)
    if before is None or after is None or before.file_id != file.id or after.file_id != file.id:
        raise EditOperationConflictError("operation_history_unavailable")
    before_content = service.get_content_at_version(session, file.id, before.version_number)
    after_content = (
        before_content
        if after.id == before.id
        else service.get_content_at_version(session, file.id, after.version_number)
    )
    return {
        "before": {
            "content": before_content,
            "title": operation.before_title,
        },
        "after": {
            "content": after_content,
            "title": operation.after_title,
        },
        "current": {
            "content": file.content or "",
            "title": file.title,
            "updated_at": normalize_datetime_to_utc(file.updated_at).isoformat(),
            "content_sha256": content_sha256(file.content),
        },
    }


def _title_target(
    operation: FileEditOperation,
    file: File,
    requested: Literal["preserve", "restore"] | None,
) -> tuple[str, int, bool]:
    if operation.before_title == operation.after_title or file.title == operation.before_title:
        return file.title, file.order, False
    if file.title == operation.after_title or requested == "restore":
        order = operation.before_order if file.order == operation.after_order else file.order
        return operation.before_title, order, False
    if requested == "preserve":
        return file.title, file.order, False
    return file.title, file.order, True


def undo_edit_operation(
    session: Session,
    *,
    operation_id: str,
    file_id: str,
    user_id: str,
    expected_updated_at: datetime | None = None,
    expected_after_content_sha256: str | None = None,
    resolved_content: str | None = None,
    metadata_resolution: Literal["preserve", "restore"] | None = None,
) -> dict[str, Any]:
    operation = session.get(FileEditOperation, operation_id)
    if operation is None or operation.file_id != file_id or operation.user_id != user_id:
        raise EditOperationNotFoundError(operation_id)
    project = session.get(Project, operation.project_id)
    if project is None or project.owner_id != user_id or project.is_deleted:
        raise EditOperationNotFoundError(operation_id)
    lock_project_for_files(session, operation.project_id, exclusive=True)

    from agent.tools.file_ops.edit import file_write_lock
    from database import is_postgres

    lock_ctx = contextlib.nullcontext() if is_postgres else file_write_lock(file_id)
    with lock_ctx:
        if is_postgres:
            file = session.exec(
                select(File).where(File.id == file_id).with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            ).first()
            operation = session.exec(
                select(FileEditOperation).where(FileEditOperation.id == operation_id).with_for_update()
                .execution_options(populate_existing=True)
            ).first()
        else:
            operation = session.get(FileEditOperation, operation_id, populate_existing=True)
            file = session.get(File, file_id, populate_existing=True)
        if operation is None or file is None or file.is_deleted or operation.user_id != user_id:
            raise EditOperationNotFoundError(operation_id)
        if operation.undone_at is not None:
            return {
                "status": "undone", "operation_id": operation.id, "file_id": file.id,
                "updated_at": normalize_datetime_to_utc(file.updated_at).isoformat(),
                "undo_recorded": True, "already_undone": True,
            }

        snapshots = _snapshots(session, operation, file)
        before_content = str(snapshots["before"]["content"])
        after_content = str(snapshots["after"]["content"])
        current_content = file.content or ""
        content_changed = before_content != after_content
        title, order, metadata_conflict = _title_target(operation, file, metadata_resolution)
        content_conflict = content_changed and current_content not in (before_content, after_content)

        resolution_attempt = resolved_content is not None or metadata_resolution is not None
        if resolution_attempt:
            if expected_updated_at is None or expected_after_content_sha256 is None:
                raise EditOperationConflictError("missing_current_token")
            current_time = normalize_datetime_to_utc(file.updated_at)
            if current_time != normalize_datetime_to_utc(expected_updated_at) or (
                content_sha256(current_content) != expected_after_content_sha256
            ):
                raise EditOperationConflictError(
                    "stale_write",
                    {"current_updated_at": current_time.isoformat(),
                     "current_content_sha256": content_sha256(current_content)},
                )

        if (content_conflict and resolved_content is None) or metadata_conflict:
            return {
                "status": "needs_merge", "operation_id": operation.id, "file_id": file.id,
                **snapshots, "content_conflict": content_conflict,
                "metadata_conflict": metadata_conflict,
            }

        if content_conflict and resolved_content is not None:
            target_content = resolved_content
        elif not content_changed or current_content == before_content:
            target_content = current_content
        else:
            target_content = before_content

        if title != file.title:
            clash = session.exec(
                select(File.id).where(
                    File.project_id == file.project_id, File.parent_id == file.parent_id,
                    File.title == title, File.is_deleted.is_(False), File.id != file.id,
                )
            ).first()
            if clash is not None:
                raise EditOperationConflictError("title_conflict")

        before_undo_version = _ensure_exact_version(
            session,
            file=file,
            content=current_content,
            change_summary="Before undoing AI edit",
        )
        file.content = target_content
        file.title = title
        file.order = order
        file.updated_at = advance_timestamp(file.updated_at, now=utcnow())
        session.add(file)
        if target_content == current_content:
            undo_version = before_undo_version
        else:
            undo_version = FileVersionService().create_version(
                session=session, file_id=file.id, new_content=target_content,
                change_type=CHANGE_TYPE_RESTORE, change_source=CHANGE_SOURCE_SYSTEM,
                change_summary=f"Undid AI {operation.kind}",
                skip_quota=True, commit=False,
            )
        operation.undone_at = utcnow()
        operation.undo_version_id = undo_version.id
        session.add(operation)
        session.commit()
        session.refresh(file)
        return {
            "status": "undone", "operation_id": operation.id, "file_id": file.id,
            "updated_at": normalize_datetime_to_utc(file.updated_at).isoformat(),
            "undo_recorded": True, "already_undone": False,
        }


__all__ = [
    "EditOperationConflictError",
    "EditOperationNotFoundError",
    "create_edit_operation",
    "stage_recoverable_file_mutation",
    "undo_edit_operation",
]
