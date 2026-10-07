#!/usr/bin/env python3
"""Plan and apply conservative cleanup of stale project vector indexes.

The default mode is read-only.  It inventories project-scoped Chroma
collections, calculates the latest known project activity from PostgreSQL,
and writes an explicit, checksummed plan.  Applying a plan requires the
checksum and revalidates every project immediately before deleting only its
Chroma collection.  Project rows, files, snapshots, and chat history are
never modified.

Examples::

    python scripts/prune_stale_vector_indexes.py \
      --persist-dir /app/chroma_data \
      --output-plan /tmp/stale-vector-plan.json

    python scripts/prune_stale_vector_indexes.py \
      --apply-plan /tmp/stale-vector-plan.json \
      --confirm-plan-sha256 <sha256-from-dry-run> \
      --confirm-application-stopped

Collection deletion and physical compaction are deliberately separate.
After a successful apply, use the Chroma version-matched ``chroma vacuum``
tool during a backed-up maintenance window with the application stopped.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import os
import sqlite3
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy import func, select
from sqlmodel import Session

load_dotenv()
os.environ.setdefault("DATABASE_URL", "sqlite:///./zenstory.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import sync_engine  # noqa: E402
from models import (  # noqa: E402
    ActivationEvent,
    AgentArtifactLedger,
    ChatMessage,
    ChatSession,
    File,
    FileVersion,
    Inspiration,
    LLMUsageEvent,
    Project,
    SkillUsage,
    Snapshot,
    User,
    UserFeedback,
    WritingStats,
    WritingStreak,
)

PLAN_SCHEMA_VERSION = 1
COLLECTION_PREFIX = "zenstory_project_"
DEFAULT_INACTIVE_MONTHS = 6
MAX_PLAN_AGE = timedelta(hours=24)


@dataclass(frozen=True)
class ActivitySnapshot:
    project_id: str
    owner_id: str
    last_activity_at: datetime


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(UTC).replace(tzinfo=None)
    return value


def _iso(value: datetime) -> str:
    return f"{_as_utc_naive(value).isoformat(timespec='microseconds')}Z"


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.removesuffix("Z"))
    return _as_utc_naive(parsed)


def subtract_calendar_months(value: datetime, months: int) -> datetime:
    """Subtract whole calendar months, clamping to the target month's end."""
    if months < 1:
        raise ValueError("inactive months must be positive")
    month_index = value.year * 12 + (value.month - 1) - months
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def plan_sha256(payload: dict[str, Any]) -> str:
    unsigned = {key: value for key, value in payload.items() if key != "plan_sha256"}
    return hashlib.sha256(_canonical_bytes(unsigned)).hexdigest()


def inventory_project_collections(persist_dir: Path) -> dict[str, int | None]:
    """Read collection names/counts directly from Chroma's SQLite catalog.

    Opening SQLite in read-only mode keeps dry-run genuinely non-mutating and
    avoids triggering a Chroma migration merely by constructing a client.
    """
    db_path = persist_dir / "chroma.sqlite3"
    if not db_path.is_file():
        raise FileNotFoundError(f"Chroma catalog not found: {db_path}")

    uri = f"file:{db_path.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        rows = connection.execute(
            """
            SELECT c.name, COUNT(e.id)
            FROM collections AS c
            LEFT JOIN segments AS s ON s.collection = c.id
            LEFT JOIN embeddings AS e ON e.segment_id = s.id
            WHERE c.name LIKE ?
            GROUP BY c.id, c.name
            """,
            (f"{COLLECTION_PREFIX}%",),
        ).fetchall()

    return {
        name.removeprefix(COLLECTION_PREFIX): int(count)
        for name, count in rows
        if name.startswith(COLLECTION_PREFIX) and name.removeprefix(COLLECTION_PREFIX)
    }


def _merge_max(target: dict[str, datetime], rows: Iterable[tuple[str, Any]]) -> None:
    for project_id, raw_value in rows:
        if not project_id or raw_value is None:
            continue
        if isinstance(raw_value, date) and not isinstance(raw_value, datetime):
            value = datetime.combine(raw_value, datetime.min.time())
        else:
            value = _as_utc_naive(raw_value)
        current = target.get(project_id)
        if current is None or value > current:
            target[project_id] = value


def load_activity_snapshots(session: Session, project_ids: Sequence[str]) -> dict[str, ActivitySnapshot]:
    """Return latest known activity for owned projects in ``project_ids``.

    Besides project/file/chat/model-use clocks, this includes every current
    project-linked timestamp that can represent writing or agent activity.
    This intentionally prefers false negatives (keep an index) over deleting
    an index whose project might still be in use.
    """
    if not project_ids:
        return {}

    projects = session.exec(
        select(Project.id, Project.owner_id, Project.created_at, Project.updated_at, Project.deleted_at)
        .join(User, User.id == Project.owner_id)
        .where(Project.id.in_(project_ids), Project.owner_id.is_not(None))
    ).all()
    last_activity: dict[str, datetime] = {}
    owners: dict[str, str] = {}
    for project_id, owner_id, created_at, updated_at, deleted_at in projects:
        owners[project_id] = owner_id
        _merge_max(last_activity, [(project_id, created_at), (project_id, updated_at), (project_id, deleted_at)])

    known_ids = tuple(owners)
    if not known_ids:
        return {}

    direct_sources = (
        (File, File.project_id, (File.created_at, File.updated_at, File.deleted_at)),
        (FileVersion, FileVersion.project_id, (FileVersion.created_at,)),
        (Snapshot, Snapshot.project_id, (Snapshot.created_at,)),
        (ChatSession, ChatSession.project_id, (ChatSession.created_at, ChatSession.updated_at)),
        (LLMUsageEvent, LLMUsageEvent.project_id, (LLMUsageEvent.occurred_at,)),
        (AgentArtifactLedger, AgentArtifactLedger.project_id, (AgentArtifactLedger.created_at,)),
        (ActivationEvent, ActivationEvent.project_id, (ActivationEvent.created_at,)),
        (SkillUsage, SkillUsage.project_id, (SkillUsage.created_at,)),
        (WritingStats, WritingStats.project_id, (WritingStats.created_at, WritingStats.updated_at, WritingStats.stats_date)),
        (WritingStreak, WritingStreak.project_id, (WritingStreak.created_at, WritingStreak.updated_at, WritingStreak.last_writing_date)),
        (UserFeedback, UserFeedback.project_id, (UserFeedback.created_at, UserFeedback.updated_at)),
        (Inspiration, Inspiration.original_project_id, (Inspiration.created_at, Inspiration.reviewed_at)),
    )
    for _model, project_column, timestamp_columns in direct_sources:
        for timestamp_column in timestamp_columns:
            rows = session.exec(
                select(project_column, func.max(timestamp_column))
                .where(project_column.in_(known_ids))
                .group_by(project_column)
            ).all()
            _merge_max(last_activity, rows)

    message_rows = session.exec(
        select(ChatSession.project_id, func.max(ChatMessage.created_at))
        .join(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.project_id.in_(known_ids))
        .group_by(ChatSession.project_id)
    ).all()
    _merge_max(last_activity, message_rows)

    return {
        project_id: ActivitySnapshot(project_id, owners[project_id], last_activity[project_id])
        for project_id in known_ids
    }


def build_plan(
    session: Session,
    persist_dir: Path,
    *,
    now: datetime,
    inactive_months: int,
) -> dict[str, Any]:
    now = _as_utc_naive(now)
    cutoff = subtract_calendar_months(now, inactive_months)
    collections = inventory_project_collections(persist_dir)
    snapshots = load_activity_snapshots(session, tuple(collections))

    candidates = []
    for project_id, document_count in sorted(collections.items()):
        snapshot = snapshots.get(project_id)
        if snapshot is None or snapshot.last_activity_at > cutoff:
            continue
        candidates.append(
            {
                "project_id": project_id,
                "collection_name": f"{COLLECTION_PREFIX}{project_id}",
                "document_count": document_count,
                "owner_id_at_plan": snapshot.owner_id,
                "last_activity_at": _iso(snapshot.last_activity_at),
            }
        )

    payload: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "generated_at": _iso(now),
        "cutoff": _iso(cutoff),
        "inactive_months": inactive_months,
        "persist_dir": str(persist_dir.resolve()),
        "indexed_project_count": len(collections),
        "candidate_count": len(candidates),
        "excluded_unknown_or_unowned_count": sum(project_id not in snapshots for project_id in collections),
        "candidates": candidates,
    }
    payload["plan_sha256"] = plan_sha256(payload)
    return payload


def write_plan(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def validate_plan_for_apply(
    session: Session,
    payload: dict[str, Any],
    *,
    confirmation: str,
    now: datetime,
    collection_inventory: dict[str, int | None],
) -> list[dict[str, Any]]:
    expected_sha = plan_sha256(payload)
    if payload.get("plan_sha256") != expected_sha or confirmation != expected_sha:
        raise ValueError("plan checksum mismatch")
    if payload.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ValueError("unsupported plan schema")

    generated_at = _parse_iso(payload["generated_at"])
    now = _as_utc_naive(now)
    if now < generated_at or now - generated_at > MAX_PLAN_AGE:
        raise ValueError("plan is expired; run a new dry-run")
    cutoff = subtract_calendar_months(now, int(payload["inactive_months"]))

    candidates = payload.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("invalid candidate list")
    project_ids = [item.get("project_id") for item in candidates if isinstance(item, dict)]
    if len(project_ids) != len(candidates) or len(set(project_ids)) != len(project_ids):
        raise ValueError("candidate IDs must be present and unique")

    snapshots = load_activity_snapshots(session, project_ids)
    validated: list[dict[str, Any]] = []
    for item in candidates:
        project_id = item["project_id"]
        expected_collection = f"{COLLECTION_PREFIX}{project_id}"
        snapshot = snapshots.get(project_id)
        if item.get("collection_name") != expected_collection:
            raise ValueError(f"invalid collection name for project {project_id}")
        if snapshot is None or snapshot.owner_id != item.get("owner_id_at_plan"):
            raise ValueError(f"project ownership is missing or changed: {project_id}")
        if _iso(snapshot.last_activity_at) != item.get("last_activity_at"):
            raise ValueError(f"project activity changed after dry-run: {project_id}")
        if snapshot.last_activity_at > cutoff:
            raise ValueError(f"project is no longer stale: {project_id}")
        if project_id not in collection_inventory:
            raise ValueError(f"collection disappeared after dry-run: {project_id}")
        planned_count = item.get("document_count")
        current_count = collection_inventory[project_id]
        if planned_count is not None and current_count is not None and planned_count != current_count:
            raise ValueError(f"collection contents changed after dry-run: {project_id}")
        validated.append(item)
    return validated


def apply_plan(
    session: Session,
    payload: dict[str, Any],
    *,
    confirmation: str,
    now: datetime,
    application_stopped: bool,
    client_factory: Callable[[str], Any] | None = None,
) -> int:
    if not application_stopped:
        raise RuntimeError("application must be stopped before applying cleanup")
    for flag in ("VECTOR_EMBEDDINGS_ENABLED", "ASYNC_VECTOR_INDEX_ENABLED"):
        if os.getenv(flag, "").strip().lower() not in {"0", "false", "no", "off"}:
            raise RuntimeError(f"{flag}=false is required before applying cleanup")

    persist_dir = Path(payload["persist_dir"])
    inventory = inventory_project_collections(persist_dir)
    candidates = validate_plan_for_apply(
        session,
        payload,
        confirmation=confirmation,
        now=now,
        collection_inventory=inventory,
    )
    if client_factory is None:
        import chromadb

        def client_factory(path: str) -> Any:
            return chromadb.PersistentClient(path=path)
    client = client_factory(str(persist_dir))
    for item in candidates:
        client.delete_collection(item["collection_name"])
    return len(candidates)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--persist-dir", default=os.getenv("CHROMA_PERSIST_DIR", "./chroma_data"))
    parser.add_argument("--inactive-months", type=int, default=DEFAULT_INACTIVE_MONTHS)
    parser.add_argument("--output-plan", type=Path)
    parser.add_argument("--apply-plan", type=Path)
    parser.add_argument("--confirm-plan-sha256")
    parser.add_argument("--confirm-application-stopped", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    now = datetime.utcnow()
    with Session(sync_engine) as session:
        if args.apply_plan:
            if args.output_plan:
                raise SystemExit("--output-plan cannot be combined with --apply-plan")
            if not args.confirm_plan_sha256:
                raise SystemExit("--confirm-plan-sha256 is required with --apply-plan")
            payload = json.loads(args.apply_plan.read_text(encoding="utf-8"))
            deleted = apply_plan(
                session,
                payload,
                confirmation=args.confirm_plan_sha256,
                now=now,
                application_stopped=args.confirm_application_stopped,
            )
            print(f"Deleted {deleted} stale project vector collection(s).")
            print("Project content was not modified. Run offline Chroma vacuum separately.")
            return 0

        payload = build_plan(
            session,
            Path(args.persist_dir),
            now=now,
            inactive_months=args.inactive_months,
        )
        if args.output_plan:
            write_plan(args.output_plan, payload)
        print(
            f"Dry-run: {payload['candidate_count']} of {payload['indexed_project_count']} "
            f"indexed project(s) are stale as of {payload['cutoff']}."
        )
        print(f"Plan SHA-256: {payload['plan_sha256']}")
        if args.output_plan:
            print(f"Explicit candidate list written to {args.output_plan} (mode 0600).")
        else:
            print("No plan file written; pass --output-plan to make an applyable plan.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
