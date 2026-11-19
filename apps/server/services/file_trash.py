"""Recoverable file-tree deletion shared by the REST API and writing agent."""

from __future__ import annotations

import contextlib
import hashlib
import json
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from sqlmodel import Session, col, func, select

from agent.tools.file_ops.edit import acquire_file_write_lock, file_write_lock
from config.datetime_utils import advance_timestamp, normalize_datetime_to_utc, utcnow
from database import is_postgres
from models import File, Project
from models.file_deletion_batch import FileDeletionBatch
from models.file_model import FILE_TYPE_FOLDER
from services.features.file_version_service import content_sha256
from services.file_tree_rules import load_live_subtree_postorder, lock_project_for_files

DeletionSource = Literal["agent_direct", "agent_confirmed", "agent_api", "file_menu"]


class FileTrashError(ValueError):
    """A recoverable deletion request is invalid for the current tree state."""


class FileTrashConflictError(FileTrashError):
    """The preview or deletion generation no longer matches; nothing changed."""


@dataclass(frozen=True)
class DeletionPreview:
    root_ids: list[str]
    recursive: bool
    targets: list[dict[str, Any]]
    count: int
    target_hash: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "root_ids": self.root_ids,
            "recursive": self.recursive,
            "targets": self.targets,
            "count": self.count,
            "target_hash": self.target_hash,
        }


def _timestamp(value: datetime | None) -> str | None:
    return normalize_datetime_to_utc(value).isoformat() if value is not None else None


def _canonical_root_ids(root_ids: list[str]) -> list[str]:
    ids = sorted({value.strip() for value in root_ids if isinstance(value, str) and value.strip()})
    if not ids:
        raise FileTrashError("至少需要一个文件")
    if len(ids) > 100:
        raise FileTrashError("一次最多指定 100 个文件 ID")
    return ids


def _target_state(file: File) -> dict[str, Any]:
    return {
        "id": file.id,
        "title": file.title,
        "file_type": file.file_type,
        "parent_id": file.parent_id,
        "order": file.order,
        "updated_at": _timestamp(file.updated_at),
        "content_sha256": content_sha256(file.content),
        "file_metadata_sha256": content_sha256(file.file_metadata),
    }


def _public_target(state: dict[str, Any]) -> dict[str, Any]:
    return {key: state[key] for key in ("id", "title", "file_type", "parent_id", "order", "updated_at")}


def _target_hash(root_ids: list[str], recursive: bool, states: list[dict[str, Any]]) -> str:
    canonical = json.dumps(
        {"root_ids": root_ids, "recursive": recursive, "targets": states},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@contextlib.contextmanager
def _acquire_file_locks(file_ids: list[str]):
    """Acquire each striped SQLite lock once in a stable order."""
    representatives: dict[int, str] = {}
    for file_id in file_ids:
        lock = file_write_lock(file_id)
        representatives.setdefault(id(lock), file_id)
    with ExitStack() as stack:
        for stripe_id in sorted(representatives):
            stack.enter_context(acquire_file_write_lock(representatives[stripe_id]))
        yield


class FileTrashService:
    """Delete and restore exact file sets while holding the project tree gate."""

    def __init__(self, session: Session, *, user_id: str, project_id: str) -> None:
        self.session = session
        self.user_id = user_id
        self.project_id = project_id

    def _lock_project(self) -> Project:
        project = lock_project_for_files(self.session, self.project_id, exclusive=True)
        if project is None or project.is_deleted or project.owner_id != self.user_id:
            raise FileTrashError("项目不存在或无权访问")
        return project

    def _load_targets(self, root_ids: list[str], recursive: bool) -> tuple[list[str], bool, list[File]]:
        ids = _canonical_root_ids(root_ids)
        roots_query = (
            select(File)
            .where(
                col(File.id).in_(ids),
                File.project_id == self.project_id,
                File.is_deleted.is_(False),
            )
            .order_by(File.id)
            .execution_options(populate_existing=True)
        )
        if is_postgres:
            # Body writers lock File without the tree gate. Hold these rows
            # while computing the preview and recording its deletion batch.
            roots_query = roots_query.with_for_update(key_share=True)
        roots = list(self.session.exec(roots_query).all())
        if len(roots) != len(ids):
            raise FileTrashConflictError("文件不存在、已删除或不属于当前项目")
        # A folder is one tree object in the product. Deleting only its row
        # would strand live children outside the visible tree, so folders are
        # always treated as recursive regardless of a legacy false flag.
        recursive = recursive or any(root.file_type == FILE_TYPE_FOLDER for root in roots)
        by_id: dict[str, File] = {}
        for root in roots:
            rows = load_live_subtree_postorder(self.session, root) if recursive else [root]
            for row in rows:
                if not row.is_deleted:
                    by_id[row.id] = row
        return ids, recursive, [by_id[file_id] for file_id in sorted(by_id)]

    def _preview_locked(self, root_ids: list[str], recursive: bool) -> tuple[DeletionPreview, list[File], list[dict[str, Any]]]:
        ids, recursive, targets = self._load_targets(root_ids, recursive)
        states = [_target_state(file) for file in targets]
        preview = DeletionPreview(
            root_ids=ids,
            recursive=recursive,
            targets=[_public_target(state) for state in states],
            count=len(states),
            target_hash=_target_hash(ids, recursive, states),
        )
        return preview, targets, states

    def preview(self, root_ids: list[str], *, recursive: bool) -> dict[str, Any]:
        self._lock_project()
        preview, _, _ = self._preview_locked(root_ids, recursive)
        self.session.rollback()
        return preview.as_dict()

    def delete(
        self,
        root_ids: list[str],
        *,
        recursive: bool,
        source: DeletionSource,
        expected_target_hash: str | None = None,
    ) -> dict[str, Any]:
        self._lock_project()
        preliminary, preliminary_targets, _ = self._preview_locked(root_ids, recursive)
        candidate_ids = [file.id for file in preliminary_targets]

        lock_stack = contextlib.nullcontext()
        if not is_postgres:
            # End the discovery read transaction before acquiring stripes, or
            # a SQLite snapshot could hide a child committed in between.
            self.session.rollback()
            lock_stack = _acquire_file_locks(candidate_ids)

        with lock_stack:
            if not is_postgres:
                self._lock_project()
            preview, targets, states = self._preview_locked(root_ids, recursive)
            if set(candidate_ids) != {file.id for file in targets}:
                self.session.rollback()
                raise FileTrashConflictError("删除范围已经变化，请重新确认")
            if expected_target_hash is not None and preview.target_hash != expected_target_hash:
                self.session.rollback()
                raise FileTrashConflictError("删除范围已经变化，请重新确认")

            try:
                deleted_items: list[dict[str, Any]] = []
                now = utcnow()
                for file, state in zip(targets, states, strict=True):
                    file.is_deleted = True
                    file.deleted_at = now
                    file.updated_at = advance_timestamp(file.updated_at, now=now)
                    self.session.add(file)
                    deleted_items.append(
                        {
                            **{
                                key: state[key]
                                for key in ("id", "title", "file_type", "parent_id", "order")
                            },
                            "predelete_updated_at": state["updated_at"],
                            "deleted_updated_at": _timestamp(file.updated_at),
                            "content_sha256": state["content_sha256"],
                        }
                    )
                batch = FileDeletionBatch(
                    project_id=self.project_id,
                    user_id=self.user_id,
                    source=source,
                    root_ids_json=json.dumps(preview.root_ids),
                    items_json=json.dumps(deleted_items, ensure_ascii=False, sort_keys=True),
                    item_count=len(deleted_items),
                    target_hash=preview.target_hash,
                )
                self.session.add(batch)
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise

        return {
            "batch_id": batch.id,
            "root_ids": preview.root_ids,
            "targets": preview.targets,
            "count": preview.count,
            "restored": False,
            "mutation_applied": True,
        }

    def _verify_project_access(self) -> Project:
        project = self.session.get(Project, self.project_id)
        if project is None or project.is_deleted or project.owner_id != self.user_id:
            raise FileTrashError("项目不存在或无权访问")
        return project

    def list_batches(self, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        self._verify_project_access()
        where = (
            FileDeletionBatch.project_id == self.project_id,
            FileDeletionBatch.user_id == self.user_id,
            FileDeletionBatch.restored_at.is_(None),
        )
        total = int(self.session.exec(select(func.count()).select_from(FileDeletionBatch).where(*where)).one())
        rows = self.session.exec(
            select(FileDeletionBatch)
            .where(*where)
            .order_by(col(FileDeletionBatch.created_at).desc(), col(FileDeletionBatch.id).desc())
            .offset(offset)
            .limit(limit)
        ).all()
        batches = []
        for row in rows:
            items = json.loads(row.items_json)
            batches.append(
                {
                    "batch_id": row.id,
                    "root_ids": json.loads(row.root_ids_json),
                    "titles": [item["title"] for item in items[:5]],
                    "count": row.item_count,
                    "source": row.source,
                    "created_at": _timestamp(row.created_at),
                    "restored": False,
                }
            )
        next_offset = offset + len(batches)
        return {
            "batches": batches,
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_offset": next_offset if next_offset < total else None,
        }

    def restore(self, batch_id: str) -> dict[str, Any]:
        self._lock_project()
        query = select(FileDeletionBatch).where(
            FileDeletionBatch.id == batch_id,
            FileDeletionBatch.project_id == self.project_id,
            FileDeletionBatch.user_id == self.user_id,
        )
        batch = self.session.exec(query.execution_options(populate_existing=True)).first()
        if batch is None:
            self.session.rollback()
            raise FileTrashError("回收记录不存在")
        if batch.restored_at is not None:
            self.session.rollback()
            return {"batch_id": batch.id, "file_ids": [], "restored": True, "already_restored": True}

        items = json.loads(batch.items_json)
        file_ids = sorted(item["id"] for item in items)
        lock_stack = contextlib.nullcontext()
        if not is_postgres:
            self.session.rollback()
            lock_stack = _acquire_file_locks(file_ids)
        with lock_stack:
            if not is_postgres:
                self._lock_project()
            file_query = (
                select(File)
                .where(col(File.id).in_(file_ids), File.project_id == self.project_id)
                .order_by(File.id)
            )
            if is_postgres:
                file_query = file_query.with_for_update(key_share=True)
            files = list(
                self.session.exec(file_query.execution_options(populate_existing=True)).all()
            )
            if len(files) != len(file_ids):
                self.session.rollback()
                raise FileTrashConflictError("部分文件已不存在，无法恢复这个批次")
            # Lock order is project -> files -> batch. Re-read the batch after
            # the file locks so concurrent duplicate restore stays idempotent.
            locked_batch_query = query.with_for_update() if is_postgres else query
            batch = self.session.exec(
                locked_batch_query.execution_options(populate_existing=True)
            ).first()
            if batch is None:
                self.session.rollback()
                raise FileTrashError("回收记录不存在")
            if batch.restored_at is not None:
                self.session.rollback()
                return {
                    "batch_id": batch.id,
                    "file_ids": [],
                    "restored": True,
                    "already_restored": True,
                }
            by_id = {file.id: file for file in files}
            batch_ids = set(file_ids)
            for item in items:
                file = by_id[item["id"]]
                if (
                    not file.is_deleted
                    or _timestamp(file.updated_at) != item["deleted_updated_at"]
                    or content_sha256(file.content) != item["content_sha256"]
                    or file.title != item["title"]
                    or file.parent_id != item["parent_id"]
                    or file.order != item["order"]
                    or file.file_type != item["file_type"]
                ):
                    self.session.rollback()
                    raise FileTrashConflictError("文件删除状态已经变化，无法恢复这个批次")
                parent_id = item["parent_id"]
                if parent_id and parent_id not in batch_ids:
                    parent = self.session.get(File, parent_id, populate_existing=True)
                    if (
                        parent is None
                        or parent.project_id != self.project_id
                        or parent.is_deleted
                        or parent.file_type != FILE_TYPE_FOLDER
                    ):
                        self.session.rollback()
                        raise FileTrashConflictError("原目录已不存在，无法恢复这个批次")
                clash = self.session.exec(
                    select(File.id).where(
                        File.project_id == self.project_id,
                        File.parent_id == parent_id,
                        File.title == item["title"],
                        File.is_deleted.is_(False),
                        File.id != file.id,
                    )
                ).first()
                if clash is not None:
                    self.session.rollback()
                    raise FileTrashConflictError("原目录已有同名文件，无法恢复这个批次")

            try:
                now = utcnow()
                for item in items:
                    file = by_id[item["id"]]
                    file.is_deleted = False
                    file.deleted_at = None
                    file.parent_id = item["parent_id"]
                    file.order = item["order"]
                    file.updated_at = advance_timestamp(file.updated_at, now=now)
                    self.session.add(file)
                batch.restored_at = now
                self.session.add(batch)
                self.session.commit()
            except Exception:
                self.session.rollback()
                raise
        return {"batch_id": batch.id, "file_ids": file_ids, "restored": True, "already_restored": False}
