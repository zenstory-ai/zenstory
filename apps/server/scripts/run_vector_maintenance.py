#!/usr/bin/env python3
"""Run one bounded, offline stale-vector cleanup before the API starts.

This wrapper generates a fresh plan, checks fixed safety ceilings, applies the
plan in a child Python process, removes only the resulting explicitly selected
orphan HNSW directories, and runs the Chroma CLI vacuum when disk space allows.
A private success marker makes the operation idempotent.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

OPERATION_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
DEFAULT_PERSIST_DIR = Path(os.getenv("CHROMA_PERSIST_DIR", "/app/chroma_data"))
DEFAULT_VACUUM_TIMEOUT_SECONDS = 60
MAINTENANCE_INACTIVE_MONTHS = 6
BACKUP_RECEIPT_SCHEMA_VERSION = 1
BACKUP_RECEIPT_MAX_AGE = timedelta(hours=24)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
HNSW_SEGMENT_TYPE = "urn:chroma:segment/vector/hnsw-local-persisted"


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    finally:
        if temporary.exists():
            temporary.unlink()


def _marker_path(persist_dir: Path, operation_id: str) -> Path:
    if not OPERATION_ID_PATTERN.fullmatch(operation_id):
        raise ValueError("operation id must be a lowercase filesystem-safe token")
    return persist_dir / ".maintenance" / f"{operation_id}.json"


def _chroma_executable() -> Path:
    executable = Path(sys.executable).resolve().with_name("chroma")
    if not executable.is_file():
        raise FileNotFoundError(f"version-matched Chroma CLI not found: {executable}")
    return executable


def _validate_backup_receipt(
    receipt_path: Path, *, operation_id: str, now: datetime
) -> int:
    """Validate an operator assertion about an already verified offsite backup.

    The offsite path intentionally is not opened: it refers to storage outside
    this container.  This validates only the signed-off receipt contract and
    must never be described as independent archive verification.
    """
    if not receipt_path.is_file() or receipt_path.is_symlink():
        raise FileNotFoundError("a regular backup receipt file is required")
    if receipt_path.stat().st_mode & 0o077:
        raise ValueError("backup receipt must not be accessible by group or other users")
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("backup receipt is not valid JSON") from exc
    if not isinstance(receipt, dict):
        raise ValueError("backup receipt must be a JSON object")

    required_fields = {
        "schema_version",
        "backup_operation_id",
        "archive_sha256",
        "archive_bytes",
        "verified_sha256",
        "sqlite_integrity",
        "collection_count",
        "offsite_archive_path",
        "generated_at",
    }
    if set(receipt) != required_fields:
        missing = sorted(required_fields - set(receipt))
        unexpected = sorted(set(receipt) - required_fields)
        raise ValueError(
            f"backup receipt fields mismatch; missing={missing}, unexpected={unexpected}"
        )
    if (
        isinstance(receipt["schema_version"], bool)
        or not isinstance(receipt["schema_version"], int)
        or receipt["schema_version"] != BACKUP_RECEIPT_SCHEMA_VERSION
    ):
        raise ValueError("unsupported backup receipt schema")
    if not isinstance(receipt["backup_operation_id"], str) or receipt["backup_operation_id"] != operation_id:
        raise ValueError("backup receipt operation id mismatch")
    if not isinstance(receipt["archive_sha256"], str) or not SHA256_PATTERN.fullmatch(
        receipt["archive_sha256"]
    ):
        raise ValueError("backup receipt archive_sha256 must be 64 lowercase hex characters")
    if (
        isinstance(receipt["archive_bytes"], bool)
        or not isinstance(receipt["archive_bytes"], int)
        or receipt["archive_bytes"] <= 0
    ):
        raise ValueError("backup receipt archive_bytes must be a positive integer")
    if receipt["verified_sha256"] is not True:
        raise ValueError("backup receipt must assert verified_sha256=true")
    if receipt["sqlite_integrity"] != "ok":
        raise ValueError("backup receipt must assert sqlite_integrity='ok'")
    if (
        isinstance(receipt["collection_count"], bool)
        or not isinstance(receipt["collection_count"], int)
        or receipt["collection_count"] <= 0
    ):
        raise ValueError("backup receipt collection_count must be a positive integer")
    offsite_path = receipt["offsite_archive_path"]
    if (
        not isinstance(offsite_path, str)
        or not offsite_path.strip()
        or not Path(offsite_path).is_absolute()
    ):
        raise ValueError("backup receipt offsite_archive_path must be a non-empty absolute path")
    if not isinstance(receipt["generated_at"], str):
        raise ValueError("backup receipt generated_at must be an ISO-8601 timestamp")
    try:
        generated_at = datetime.fromisoformat(receipt["generated_at"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("backup receipt generated_at must be an ISO-8601 timestamp") from exc
    if generated_at.tzinfo is None:
        raise ValueError("backup receipt generated_at must include a timezone")
    generated_at = generated_at.astimezone(UTC)
    now = now.astimezone(UTC) if now.tzinfo is not None else now.replace(tzinfo=UTC)
    if generated_at > now or now - generated_at > BACKUP_RECEIPT_MAX_AGE:
        raise ValueError("backup receipt is expired or dated in the future")
    return receipt["collection_count"]


def _run_checked(
    command: list[str], *, timeout: int, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )


def _read_hnsw_segment_ids(catalog_path: Path, collection_names: list[str]) -> list[str]:
    if len(collection_names) != len(set(collection_names)):
        raise ValueError("maintenance candidate collection names must be unique")
    if not collection_names:
        return []
    placeholders = ",".join("?" for _ in collection_names)
    catalog_uri = f"{catalog_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(catalog_uri, uri=True) as connection:
        rows = connection.execute(
            f"""
            SELECT collections.name, segments.id
            FROM collections
            JOIN segments ON segments.collection = collections.id
            WHERE collections.name IN ({placeholders})
              AND segments.scope = 'VECTOR'
              AND segments.type = ?
            ORDER BY collections.name, segments.id
            """,
            [*collection_names, HNSW_SEGMENT_TYPE],
        ).fetchall()

    segments_by_collection: dict[str, list[str]] = {name: [] for name in collection_names}
    for collection_name, segment_id in rows:
        if not isinstance(segment_id, str):
            raise ValueError("HNSW segment id must be text")
        try:
            parsed_id = uuid.UUID(segment_id)
        except ValueError as exc:
            raise ValueError("HNSW segment id must be a canonical UUID") from exc
        if str(parsed_id) != segment_id:
            raise ValueError("HNSW segment id must be a canonical UUID")
        segments_by_collection[collection_name].append(segment_id)

    if any(len(segment_ids) > 1 for segment_ids in segments_by_collection.values()):
        raise RuntimeError("a candidate collection has multiple persistent HNSW segments")
    segment_ids = [segment_id for ids in segments_by_collection.values() for segment_id in ids]
    if len(segment_ids) != len(set(segment_ids)):
        raise RuntimeError("an HNSW segment is shared by multiple candidate collections")
    return sorted(segment_ids)


def _directory_size_without_symlinks(path: Path) -> int:
    total = 0
    for entry in path.rglob("*"):
        if entry.is_symlink():
            raise RuntimeError(f"refusing symlink inside HNSW segment directory: {path.name}")
        if entry.is_file():
            total += entry.stat().st_size
    return total


def _delete_orphaned_hnsw_segments(
    persist_dir: Path, segment_ids: list[str]
) -> tuple[int, int]:
    if not segment_ids:
        return 0, 0
    placeholders = ",".join("?" for _ in segment_ids)
    catalog_path = persist_dir / "chroma.sqlite3"
    catalog_uri = f"{catalog_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(catalog_uri, uri=True) as connection:
        referenced = connection.execute(
            f"SELECT id FROM segments WHERE id IN ({placeholders})",
            segment_ids,
        ).fetchall()
    if referenced:
        raise RuntimeError("refusing to delete an HNSW segment still referenced by the catalog")

    root = persist_dir.resolve(strict=True)
    deletions: list[tuple[Path, int]] = []
    for segment_id in segment_ids:
        segment_path = persist_dir / segment_id
        if not segment_path.exists() and not segment_path.is_symlink():
            continue
        if segment_path.is_symlink() or not segment_path.is_dir():
            raise RuntimeError(f"refusing unsafe HNSW segment path: {segment_id}")
        resolved_path = segment_path.resolve(strict=True)
        if resolved_path.parent != root or resolved_path.name != segment_id:
            raise RuntimeError(f"HNSW segment path escapes persist directory: {segment_id}")
        deletions.append((segment_path, _directory_size_without_symlinks(segment_path)))

    released_bytes = 0
    for segment_path, segment_bytes in deletions:
        shutil.rmtree(segment_path)
        if segment_path.exists() or segment_path.is_symlink():
            raise RuntimeError(f"HNSW segment directory was not removed: {segment_path.name}")
        released_bytes += segment_bytes
    return len(deletions), released_bytes


def run_maintenance(
    *,
    operation_id: str,
    persist_dir: Path,
    max_candidates: int,
    max_documents: int,
    backup_path: Path | None = None,
    backup_receipt_path: Path | None = None,
    vacuum_timeout_seconds: int = DEFAULT_VACUUM_TIMEOUT_SECONDS,
    runner: Callable[..., subprocess.CompletedProcess[str]] = _run_checked,
    chroma_executable: Path | None = None,
) -> dict[str, Any]:
    marker = _marker_path(persist_dir, operation_id)
    if marker.is_file():
        marker_payload = json.loads(marker.read_text(encoding="utf-8"))
        if marker_payload.get("status") != "success" or marker_payload.get("operation_id") != operation_id:
            raise RuntimeError("maintenance marker exists but is not a valid success marker")
        return {
            "status": "noop",
            "candidate_count": int(marker_payload.get("candidate_count", 0)),
            "document_count": int(marker_payload.get("document_count", 0)),
        }

    if max_candidates < 0 or max_documents < 0:
        raise ValueError("maintenance safety ceilings cannot be negative")
    if (backup_path is None) == (backup_receipt_path is None):
        raise ValueError("exactly one backup proof mode is required")
    receipt_collection_count: int | None = None
    if backup_path is not None:
        if not backup_path.is_file() or backup_path.stat().st_size <= 0:
            raise FileNotFoundError("a non-empty maintenance backup is required")
    else:
        assert backup_receipt_path is not None
        receipt_collection_count = _validate_backup_receipt(
            backup_receipt_path,
            operation_id=operation_id,
            now=datetime.now(UTC),
        )
    if not (persist_dir / "chroma.sqlite3").is_file():
        raise FileNotFoundError("Chroma persistent catalog is missing")

    maintenance_script = Path(__file__).with_name("prune_stale_vector_indexes.py")
    child_environment = os.environ.copy()
    child_environment["VECTOR_EMBEDDINGS_ENABLED"] = "false"
    child_environment["ASYNC_VECTOR_INDEX_ENABLED"] = "false"

    with tempfile.TemporaryDirectory(prefix="zenstory-vector-maintenance-") as temporary_dir:
        plan_path = Path(temporary_dir) / "plan.json"
        runner(
            [
                sys.executable,
                str(maintenance_script),
                "--persist-dir",
                str(persist_dir),
                "--inactive-months",
                str(MAINTENANCE_INACTIVE_MONTHS),
                "--output-plan",
                str(plan_path),
            ],
            timeout=120,
            env=child_environment,
        )
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        candidates = plan.get("candidates")
        if not isinstance(candidates, list):
            raise ValueError("fresh maintenance plan has no candidate list")
        candidate_count = int(plan.get("candidate_count", -1))
        if candidate_count != len(candidates):
            raise ValueError("fresh maintenance plan candidate count is inconsistent")
        document_count = sum(int(item.get("document_count") or 0) for item in candidates)
        if receipt_collection_count is not None:
            indexed_project_count = plan.get("indexed_project_count")
            if (
                isinstance(indexed_project_count, bool)
                or not isinstance(indexed_project_count, int)
                or indexed_project_count < 0
            ):
                raise ValueError("fresh maintenance plan indexed project count is invalid")
            if indexed_project_count > receipt_collection_count:
                raise RuntimeError(
                    "fresh collection count exceeds the offsite backup snapshot; "
                    "create and verify a new backup"
                )
        if candidate_count > max_candidates:
            raise RuntimeError(
                f"candidate safety ceiling exceeded: {candidate_count} > {max_candidates}"
            )
        if document_count > max_documents:
            raise RuntimeError(
                f"document safety ceiling exceeded: {document_count} > {max_documents}"
            )

        collection_names = []
        for candidate in candidates:
            collection_name = candidate.get("collection_name")
            if not isinstance(collection_name, str) or not collection_name:
                raise ValueError("maintenance candidate has no collection name")
            collection_names.append(collection_name)
        hnsw_segment_ids = _read_hnsw_segment_ids(
            persist_dir / "chroma.sqlite3", collection_names
        )

        plan_checksum = str(plan.get("plan_sha256") or "")
        if not plan_checksum:
            raise ValueError("fresh maintenance plan has no checksum")
        runner(
            [
                sys.executable,
                str(maintenance_script),
                "--apply-plan",
                str(plan_path),
                "--confirm-plan-sha256",
                plan_checksum,
                "--confirm-application-stopped",
            ],
            timeout=300,
            env=child_environment,
        )

    released_hnsw_dirs, released_hnsw_bytes = _delete_orphaned_hnsw_segments(
        persist_dir, hnsw_segment_ids
    )

    # SQLite VACUUM can require twice the catalog size in additional space.
    # On a tight volume, retain reusable SQLite pages instead of risking a
    # disk-full startup after the exact orphan HNSW directories are removed.
    required_vacuum_bytes = 2 * (persist_dir / "chroma.sqlite3").stat().st_size
    vacuum_completed = False
    if shutil.disk_usage(persist_dir).free >= required_vacuum_bytes:
        chroma_cli = chroma_executable or _chroma_executable()
        runner(
            [
                str(chroma_cli),
                "vacuum",
                "--path",
                str(persist_dir),
                "--force",
                "--timeout",
                str(vacuum_timeout_seconds),
            ],
            timeout=vacuum_timeout_seconds + 30,
            env=child_environment,
        )
        vacuum_completed = True

    marker_payload = {
        "schema_version": 1,
        "operation_id": operation_id,
        "status": "success",
        "completed_at": datetime.now(UTC).isoformat(),
        "candidate_count": candidate_count,
        "document_count": document_count,
        "released_hnsw_dirs": released_hnsw_dirs,
        "released_hnsw_bytes": released_hnsw_bytes,
        "vacuum_completed": vacuum_completed,
        "vacuum_skipped_reason": None if vacuum_completed else "insufficient_disk_space",
    }
    _write_private_json(marker, marker_payload)
    return {
        "status": "success",
        "candidate_count": candidate_count,
        "document_count": document_count,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--operation-id", required=True)
    parser.add_argument("--persist-dir", type=Path, default=DEFAULT_PERSIST_DIR)
    backup_group = parser.add_mutually_exclusive_group(required=True)
    backup_group.add_argument("--require-backup", type=Path)
    backup_group.add_argument("--backup-receipt", type=Path)
    parser.add_argument("--max-candidates", type=int, required=True)
    parser.add_argument("--max-documents", type=int, required=True)
    parser.add_argument("--vacuum-timeout", type=int, default=DEFAULT_VACUUM_TIMEOUT_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    result = run_maintenance(
        operation_id=args.operation_id,
        persist_dir=args.persist_dir,
        backup_path=args.require_backup,
        backup_receipt_path=args.backup_receipt,
        max_candidates=args.max_candidates,
        max_documents=args.max_documents,
        vacuum_timeout_seconds=args.vacuum_timeout,
    )
    print(
        f"Vector maintenance {result['status']}: "
        f"{result['candidate_count']} collection(s), {result['document_count']} document(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
