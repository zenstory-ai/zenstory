#!/usr/bin/env python3
"""Move DB-referenced uploads to private object storage without deleting local files."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Keep the documented direct script entry point working outside pytest, which
# adds the server root to sys.path itself.
if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlmodel import Session, select  # noqa: E402

from config.datetime_utils import utcnow  # noqa: E402
from config.material_settings import material_settings  # noqa: E402
from database import create_session  # noqa: E402
from models import UserFeedback  # noqa: E402
from models.material_models import IngestionJob, Novel  # noqa: E402
from services.infra.upload_storage import (  # noqa: E402
    S3UploadStorage,
    StoredObject,
    UploadStorageError,
    get_upload_storage,
)

RUNNING_JOB_STATUSES = {"pending", "processing"}


@dataclass
class MigrationCounts:
    material_candidates: int = 0
    feedback_candidates: int = 0
    migrated: int = 0
    skipped_running: int = 0
    skipped_unsafe: int = 0
    skipped_remote: int = 0
    failed: int = 0


class MigrationJournal:
    """Crash-safe write-ahead record for reversible reference changes."""

    def __init__(self, path: Path):
        self.path = path
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("version") != 1 or not isinstance(payload.get("entries"), list):
                raise ValueError("invalid migration manifest")
            self.payload = payload
        else:
            self.payload = {"version": 1, "generated_at": utcnow().isoformat(), "entries": []}
            self._save()

    @property
    def entries(self) -> list[dict[str, Any]]:
        return self.payload["entries"]

    def prepare(self, entry: dict[str, Any]) -> None:
        existing = next(
            (
                item
                for item in self.entries
                if item.get("kind") == entry.get("kind")
                and item.get("old_reference") == entry.get("old_reference")
                and item.get("new_reference") == entry.get("new_reference")
            ),
            None,
        )
        if existing is None:
            self.entries.append({**entry, "status": "prepared"})
            self._save()

    def committed(self, entry: dict[str, Any]) -> None:
        for item in self.entries:
            if (
                item.get("kind") == entry.get("kind")
                and item.get("old_reference") == entry.get("old_reference")
                and item.get("new_reference") == entry.get("new_reference")
            ):
                item["status"] = "committed"
                self._save()
                return
        raise ValueError("migration entry was not prepared")

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(self.payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)
        os.chmod(self.path, 0o600)
        directory_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)


def _trusted_file(raw_path: str, root: Path) -> Path | None:
    """Resolve a regular file under root while rejecting every symlink component."""
    if not raw_path or raw_path.startswith("s3://"):
        return None
    trusted_root = root.expanduser().resolve(strict=True)
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = trusted_root / candidate
    try:
        lexical_relative = candidate.relative_to(trusted_root)
    except ValueError:
        return None
    cursor = trusted_root
    for component in lexical_relative.parts:
        cursor = cursor / component
        try:
            if stat.S_ISLNK(cursor.lstat().st_mode):
                return None
        except OSError:
            return None
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(trusted_root)
    except (OSError, ValueError):
        return None
    return resolved if resolved.is_file() else None


def _read_source_meta(novel: Novel) -> dict[str, Any]:
    if not novel.source_meta:
        return {}
    try:
        value = json.loads(novel.source_meta)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _verified_put_material(storage, *, owner_id: str, path: Path) -> StoredObject:
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    suffix = path.suffix.lower()
    stored = storage.put_material(
        owner_id=owner_id,
        timestamp="migration",
        original_name=f"source{suffix}",
        object_name=f"{digest[:32]}{suffix}",
        content=content,
    )
    downloaded = storage.read_material_reference(stored.reference, owner_id=owner_id)
    if len(downloaded) != len(content) or hashlib.sha256(downloaded).hexdigest() != digest:
        raise UploadStorageError("material verification failed")
    return stored


def _verified_put_feedback(storage, *, path: Path) -> StoredObject:
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    suffix = path.suffix.lower()
    stored = storage.put_feedback(
        suffix=suffix,
        object_name=f"{digest[:32]}{suffix}",
        content=content,
    )
    downloaded = storage.read_feedback(stored.reference)
    if len(downloaded) != len(content) or hashlib.sha256(downloaded).hexdigest() != digest:
        raise UploadStorageError("feedback verification failed")
    return stored


def migrate_references(
    session: Session,
    storage,
    *,
    material_root: Path,
    feedback_root: Path,
    apply: bool,
    journal: MigrationJournal | None = None,
    user_id: str | None = None,
) -> tuple[MigrationCounts, list[dict[str, Any]]]:
    """Plan or apply reference changes; local source files are always retained."""
    counts = MigrationCounts()
    manifest: list[dict[str, Any]] = []

    novel_query = select(Novel)
    feedback_query = select(UserFeedback)
    if user_id is not None:
        novel_query = novel_query.where(Novel.user_id == user_id)
        feedback_query = feedback_query.where(UserFeedback.user_id == user_id)

    for novel in session.exec(novel_query).all():
        meta = _read_source_meta(novel)
        old_reference = meta.get("file_path")
        if not isinstance(old_reference, str):
            continue
        if old_reference.startswith("s3://"):
            counts.skipped_remote += 1
            continue
        path = _trusted_file(old_reference, material_root)
        if path is None:
            counts.skipped_unsafe += 1
            continue
        jobs = session.exec(select(IngestionJob).where(IngestionJob.novel_id == novel.id)).all()
        if any(job.status in RUNNING_JOB_STATUSES for job in jobs):
            counts.skipped_running += 1
            continue
        counts.material_candidates += 1
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not apply:
            continue
        novel_id = novel.id
        owner_id = novel.user_id
        job_snapshot = {job.id: (job.status, job.source_path) for job in jobs}
        stored: StoredObject | None = None
        commit_attempted = False
        try:
            stored = _verified_put_material(storage, owner_id=owner_id, path=path)
            session.expire_all()
            locked_novel = session.exec(
                select(Novel).where(Novel.id == novel_id).with_for_update()
            ).one()
            locked_jobs = session.exec(
                select(IngestionJob).where(IngestionJob.novel_id == novel_id).with_for_update()
            ).all()
            current_meta = _read_source_meta(locked_novel)
            if current_meta.get("file_path") != old_reference:
                session.rollback()
                counts.failed += 1
                if stored.created:
                    with contextlib.suppress(Exception):
                        storage.delete(stored.reference, kind="material", owner_id=owner_id)
                continue
            if any(job.status in RUNNING_JOB_STATUSES for job in locked_jobs) or any(
                job.id in job_snapshot and job_snapshot[job.id] != (job.status, job.source_path)
                for job in locked_jobs
            ):
                session.rollback()
                counts.skipped_running += 1
                if stored.created:
                    with contextlib.suppress(Exception):
                        storage.delete(stored.reference, kind="material", owner_id=owner_id)
                continue
            current_meta["file_path"] = stored.reference
            locked_novel.source_meta = json.dumps(current_meta, ensure_ascii=False)
            updated_jobs: list[int] = []
            for job in locked_jobs:
                if job.source_path == old_reference:
                    job.source_path = stored.reference
                    if job.id is not None:
                        updated_jobs.append(job.id)
            entry = {
                "kind": "material",
                "novel_id": novel_id,
                "job_ids": updated_jobs,
                "old_reference": old_reference,
                "new_reference": stored.reference,
                "sha256": digest,
                "size": path.stat().st_size,
            }
            if journal is not None:
                journal.prepare(entry)
            commit_attempted = True
            session.commit()
            counts.migrated += 1
            manifest.append(entry)
            if journal is not None:
                journal.committed(entry)
        except Exception:
            session.rollback()
            counts.failed += 1
            if stored is not None and stored.created and not commit_attempted:
                with contextlib.suppress(Exception):
                    storage.delete(stored.reference, kind="material", owner_id=owner_id)

    for feedback in session.exec(feedback_query).all():
        old_reference = feedback.screenshot_path
        if not old_reference:
            continue
        if old_reference.startswith("s3://"):
            counts.skipped_remote += 1
            continue
        path = _trusted_file(old_reference, feedback_root)
        if path is None:
            counts.skipped_unsafe += 1
            continue
        counts.feedback_candidates += 1
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if not apply:
            continue
        feedback_id = feedback.id
        stored = None
        commit_attempted = False
        try:
            stored = _verified_put_feedback(storage, path=path)
            session.expire_all()
            locked_feedback = session.exec(
                select(UserFeedback).where(UserFeedback.id == feedback_id).with_for_update()
            ).one()
            if locked_feedback.screenshot_path != old_reference:
                session.rollback()
                counts.failed += 1
                if stored.created:
                    with contextlib.suppress(Exception):
                        storage.delete(stored.reference, kind="feedback")
                continue
            locked_feedback.screenshot_path = stored.reference
            entry = {
                "kind": "feedback",
                "feedback_id": feedback_id,
                "old_reference": old_reference,
                "new_reference": stored.reference,
                "sha256": digest,
                "size": path.stat().st_size,
            }
            if journal is not None:
                journal.prepare(entry)
            commit_attempted = True
            session.commit()
            counts.migrated += 1
            manifest.append(entry)
            if journal is not None:
                journal.committed(entry)
        except Exception:
            session.rollback()
            counts.failed += 1
            if stored is not None and stored.created and not commit_attempted:
                with contextlib.suppress(Exception):
                    storage.delete(stored.reference, kind="feedback")
    return counts, manifest


def restore_references(
    session: Session,
    manifest: list[dict[str, Any]],
    *,
    material_root: Path,
    feedback_root: Path,
) -> MigrationCounts:
    """Restore old references only when the DB still points at the migrated object."""
    counts = MigrationCounts()
    for entry in manifest:
        kind = entry.get("kind")
        root = material_root if kind == "material" else feedback_root
        path = _trusted_file(str(entry.get("old_reference", "")), root)
        if path is None:
            counts.skipped_unsafe += 1
            continue
        content = path.read_bytes()
        if (
            len(content) != entry.get("size")
            or hashlib.sha256(content).hexdigest() != entry.get("sha256")
        ):
            counts.failed += 1
            continue
        if kind == "material":
            novel = session.get(Novel, entry.get("novel_id"))
            if novel is None:
                counts.failed += 1
                continue
            meta = _read_source_meta(novel)
            if meta.get("file_path") != entry.get("new_reference"):
                counts.skipped_remote += 1
                continue
            meta["file_path"] = entry["old_reference"]
            novel.source_meta = json.dumps(meta, ensure_ascii=False)
            for job_id in entry.get("job_ids", []):
                job = session.get(IngestionJob, job_id)
                if job is not None and job.source_path == entry.get("new_reference"):
                    job.source_path = entry["old_reference"]
        elif kind == "feedback":
            feedback = session.get(UserFeedback, entry.get("feedback_id"))
            if feedback is None or feedback.screenshot_path != entry.get("new_reference"):
                counts.skipped_remote += 1
                continue
            feedback.screenshot_path = entry["old_reference"]
        else:
            counts.failed += 1
            continue
        session.commit()
        counts.migrated += 1
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--material-root", type=Path, default=Path(material_settings.UPLOAD_FOLDER))
    parser.add_argument(
        "--feedback-root", type=Path, default=Path(os.getenv("FEEDBACK_UPLOAD_DIR", "uploads/feedback"))
    )
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--user-id", help="Restrict migration to one exact user id")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--restore", action="store_true")
    args = parser.parse_args()

    session = create_session()
    storage = None
    try:
        if args.restore:
            payload = json.loads(args.manifest.read_text(encoding="utf-8"))
            counts = restore_references(
                session,
                payload.get("entries", []),
                material_root=args.material_root,
                feedback_root=args.feedback_root,
            )
        else:
            storage = get_upload_storage(
                material_root=args.material_root,
                feedback_root=args.feedback_root,
            )
            if args.apply and not isinstance(storage, S3UploadStorage):
                parser.error("--apply requires UPLOAD_STORAGE_BACKEND=s3")
            journal = MigrationJournal(args.manifest) if args.apply else None
            counts, entries = migrate_references(
                session,
                storage,
                material_root=args.material_root,
                feedback_root=args.feedback_root,
                apply=args.apply,
                journal=journal,
                user_id=args.user_id,
            )
        print(json.dumps(asdict(counts), sort_keys=True))
        return 1 if counts.failed else 0
    finally:
        if storage is not None:
            storage.close()
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
