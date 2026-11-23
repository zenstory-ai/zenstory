#!/usr/bin/env python3
"""
Zero-lock batched backfill for shrinking ``chat_message`` storage.

Performs two safe, idempotent cleanups on historical ``chat_message`` rows:
1. Clears duplicate ``reasoning_content`` ONLY on assistant rows whose
   ``message_metadata.display_events`` already contain non-empty ``thinking_content``.
2. Archives heavy query payloads (``content``, ``content_preview``, ``matches``,
   ``author_edits``, ``file_metadata``, ``content_range``) inside ``query_files``
   and legacy ``parallel_execute`` query subtasks ONLY on assistant rows that are:
   - at or before their session's latest valid ``compaction.covers_through_message_id``
     boundary (matching ``loop/history.py`` ``(created_at, id)`` ordering), OR
   - in a session whose latest message is older than ``--idle-days`` (default 7).

Safety invariants:
- Dry-run by default; pass ``--apply`` to write.
- Keyset pagination + short per-batch transactions + ``FOR UPDATE SKIP LOCKED``
  on PostgreSQL (striped row lock on SQLite) so live undo/restore card writes
  are never blocked or overwritten.
- Never modifies ``edit_file``, ``write_chapter``, ``delete_file``, ``create_file``,
  ``update_project``, or any undo/restore card metadata.
- Never runs ``VACUUM FULL``.
- Prints numeric-only progress and bytes saved (never prints message text or PII).

Usage:
    python scripts/compact_historical_chat_messages.py --dry-run
    python scripts/compact_historical_chat_messages.py --apply --batch-size 200 --sleep-ms 100 --idle-days 7
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("DATABASE_URL", "sqlite:///./zenstory.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import and_, desc, func, or_  # noqa: E402
from sqlalchemy.orm import load_only  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from agent.core.chat_storage import (  # noqa: E402
    archive_historical_tool_calls_json,
    metadata_has_thinking_display_events,
)
from agent.loop.history import valid_compaction  # noqa: E402
from config.datetime_utils import normalize_datetime_to_utc, utcnow  # noqa: E402
from models.entities import ChatMessage  # noqa: E402
from utils.file_write_lock import acquire_file_write_lock  # noqa: E402

DEFAULT_BATCH_SIZE = 200
DEFAULT_SLEEP_MS = 100
DEFAULT_IDLE_DAYS = 7


@dataclass
class CompactionBackfillReport:
    """Numeric-only summary of a backfill run."""

    dry_run: bool = True
    batches: int = 0
    scanned: int = 0
    rows_updated: int = 0
    reasoning_cleared: int = 0
    tool_calls_archived: int = 0
    skipped_locked: int = 0
    reasoning_bytes_saved: int = 0
    tool_calls_bytes_saved: int = 0

    @property
    def total_bytes_saved(self) -> int:
        return self.reasoning_bytes_saved + self.tool_calls_bytes_saved


def _utf8_len(value: str | None) -> int:
    return len(value.encode("utf-8")) if isinstance(value, str) else 0


def _parse_metadata_dict(raw: str | None) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _is_postgres_session(session: Session) -> bool:
    bind = session.get_bind() if hasattr(session, "get_bind") else None
    return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"


def _resolve_session_archival_policy(
    session: Session,
    session_id: str,
    idle_cutoff_utc: datetime,
) -> tuple[bool, tuple[datetime, str] | None]:
    """Return ``(is_idle, compaction_anchor)`` for ``session_id``.

    ``compaction_anchor`` is ``(anchor_created_at_utc, anchor_message_id)`` when
    the session's latest valid compaction record points to an existing message in
    the same session; otherwise ``None``.
    """
    latest_at = session.exec(
        select(func.max(ChatMessage.created_at)).where(ChatMessage.session_id == session_id)
    ).one()
    if latest_at is not None and normalize_datetime_to_utc(latest_at) < idle_cutoff_utc:
        return True, None

    compaction_rows = session.exec(
        select(ChatMessage.message_metadata)
        .where(
            ChatMessage.session_id == session_id,
            ChatMessage.role.in_(("user", "assistant")),  # type: ignore[attr-defined]
            ChatMessage.message_metadata.is_not(None),  # type: ignore[union-attr]
            ChatMessage.message_metadata.contains('"compaction"'),  # type: ignore[union-attr]
        )
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
    ).all()

    for raw_meta in compaction_rows:
        compaction = valid_compaction(_parse_metadata_dict(raw_meta))
        if compaction is None:
            continue
        anchor = session.exec(
            select(ChatMessage.created_at, ChatMessage.id).where(
                ChatMessage.id == compaction["covers_through_message_id"],
                ChatMessage.session_id == session_id,
            )
        ).first()
        if anchor is None:
            # Matches loop/history.py: missing anchor falls back to full replay.
            return False, None
        anchor_created_at, anchor_id = anchor
        return False, (normalize_datetime_to_utc(anchor_created_at), str(anchor_id))

    return False, None


def _row_eligible_for_tool_archival(
    row_created_at: datetime,
    row_id: str,
    *,
    is_idle: bool,
    compaction_anchor: tuple[datetime, str] | None,
) -> bool:
    if is_idle:
        return True
    if compaction_anchor is None:
        return False
    anchor_created_at_utc, anchor_id = compaction_anchor
    row_created_at_utc = normalize_datetime_to_utc(row_created_at)
    return row_created_at_utc < anchor_created_at_utc or (
        row_created_at_utc == anchor_created_at_utc and row_id <= anchor_id
    )


def compact_historical_chat_messages(
    session_factory: Callable[[], Session],
    *,
    dry_run: bool = True,
    batch_size: int = DEFAULT_BATCH_SIZE,
    sleep_ms: int = DEFAULT_SLEEP_MS,
    idle_days: int = DEFAULT_IDLE_DAYS,
    now: datetime | None = None,
    progress_callback: Callable[[CompactionBackfillReport], None] | None = None,
) -> CompactionBackfillReport:
    """Scan and optionally slim historical ``chat_message`` rows in small batches."""
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if sleep_ms < 0:
        raise ValueError("sleep_ms must be >= 0")
    if idle_days < 0:
        raise ValueError("idle_days must be >= 0")

    report = CompactionBackfillReport(dry_run=dry_run)
    now_utc = normalize_datetime_to_utc(now if now is not None else utcnow())
    idle_cutoff_utc = now_utc - timedelta(days=idle_days)

    policy_cache: dict[str, tuple[bool, tuple[datetime, str] | None]] = {}
    cursor: tuple[datetime, str] | None = None

    while True:
        with session_factory() as session:
            is_pg = _is_postgres_session(session)
            stmt = (
                select(
                    ChatMessage.id,
                    ChatMessage.session_id,
                    ChatMessage.created_at,
                    ChatMessage.reasoning_content,
                    ChatMessage.tool_calls,
                    ChatMessage.message_metadata,
                )
                .where(
                    ChatMessage.role == "assistant",
                    or_(
                        ChatMessage.reasoning_content.is_not(None),  # type: ignore[union-attr]
                        ChatMessage.tool_calls.is_not(None),  # type: ignore[union-attr]
                    ),
                )
                .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())  # type: ignore[union-attr]
                .limit(batch_size)
            )
            if cursor is not None:
                cursor_created_at, cursor_id = cursor
                stmt = stmt.where(
                    or_(
                        ChatMessage.created_at > cursor_created_at,
                        and_(
                            ChatMessage.created_at == cursor_created_at,
                            ChatMessage.id > cursor_id,
                        ),
                    )
                )

            batch = session.exec(stmt).all()
            if not batch:
                break

            report.batches += 1
            report.scanned += len(batch)

            pending_actions: list[tuple[str, bool, bool]] = []

            for row_id, sess_id, created_at, reasoning_content, tool_calls, raw_metadata in batch:
                can_clear_reasoning = (
                    reasoning_content is not None
                    and metadata_has_thinking_display_events(raw_metadata)
                )
                can_archive_tools = False
                archived_tools_preview: str | None = None

                if isinstance(tool_calls, str) and (
                    '"query_files"' in tool_calls or '"parallel_execute"' in tool_calls
                ):
                    policy = policy_cache.get(sess_id)
                    if policy is None:
                        policy = _resolve_session_archival_policy(session, sess_id, idle_cutoff_utc)
                        policy_cache[sess_id] = policy
                    is_idle, compaction_anchor = policy
                    if _row_eligible_for_tool_archival(
                        created_at,
                        row_id,
                        is_idle=is_idle,
                        compaction_anchor=compaction_anchor,
                    ):
                        archived_tools_preview = archive_historical_tool_calls_json(tool_calls)
                        can_archive_tools = archived_tools_preview != tool_calls

                if not can_clear_reasoning and not can_archive_tools:
                    continue

                if dry_run:
                    report.rows_updated += 1
                    if can_clear_reasoning:
                        report.reasoning_cleared += 1
                        report.reasoning_bytes_saved += _utf8_len(reasoning_content)
                    if can_archive_tools:
                        report.tool_calls_archived += 1
                        report.tool_calls_bytes_saved += max(
                            0,
                            _utf8_len(tool_calls) - _utf8_len(archived_tools_preview),
                        )
                else:
                    pending_actions.append((row_id, can_clear_reasoning, can_archive_tools))

            batch_committed_rows = 0
            if not dry_run and pending_actions:
                for row_id, want_clear_reasoning, want_archive_tools in pending_actions:
                    lock_ctx = contextlib.nullcontext() if is_pg else acquire_file_write_lock(row_id)
                    with lock_ctx:
                        lock_stmt = (
                            select(ChatMessage)
                            .options(
                                load_only(
                                    ChatMessage.id,
                                    ChatMessage.reasoning_content,
                                    ChatMessage.tool_calls,
                                    ChatMessage.message_metadata,
                                )
                            )
                            .where(ChatMessage.id == row_id)
                            .execution_options(populate_existing=True)
                        )
                        if is_pg:
                            lock_stmt = lock_stmt.with_for_update(skip_locked=True)
                        locked = session.exec(lock_stmt).first()
                        if locked is None:
                            report.skipped_locked += 1
                            continue

                        row_changed = False
                        if (
                            want_clear_reasoning
                            and locked.reasoning_content is not None
                            and metadata_has_thinking_display_events(locked.message_metadata)
                        ):
                            report.reasoning_bytes_saved += _utf8_len(locked.reasoning_content)
                            report.reasoning_cleared += 1
                            locked.reasoning_content = None
                            row_changed = True

                        if want_archive_tools and locked.tool_calls:
                            slimmed_tools = archive_historical_tool_calls_json(locked.tool_calls)
                            if slimmed_tools != locked.tool_calls:
                                report.tool_calls_bytes_saved += max(
                                    0,
                                    _utf8_len(locked.tool_calls) - _utf8_len(slimmed_tools),
                                )
                                report.tool_calls_archived += 1
                                locked.tool_calls = slimmed_tools
                                row_changed = True

                        if row_changed:
                            session.add(locked)
                            report.rows_updated += 1
                            batch_committed_rows += 1

                if batch_committed_rows:
                    session.commit()

            last_row = batch[-1]
            cursor = (last_row[2], last_row[0])

            if progress_callback is not None:
                progress_callback(report)

            batch_len = len(batch)

        if not dry_run and batch_committed_rows and sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

        if batch_len < batch_size:
            break

    return report


def _format_progress_line(report: CompactionBackfillReport, *, final: bool = False) -> str:
    mode = "dry_run" if report.dry_run else "apply"
    tag = "summary" if final else "progress"
    return (
        f"[{tag}] mode={mode} batches={report.batches} scanned={report.scanned} "
        f"rows_updated={report.rows_updated} reasoning_cleared={report.reasoning_cleared} "
        f"tool_calls_archived={report.tool_calls_archived} skipped_locked={report.skipped_locked} "
        f"reasoning_bytes_saved={report.reasoning_bytes_saved} "
        f"tool_calls_bytes_saved={report.tool_calls_bytes_saved} "
        f"total_bytes_saved={report.total_bytes_saved}"
    )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be >= 1")
    return parsed


def _non_negative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Zero-lock batched backfill for chat_message reasoning_content dedup and historical tool_calls archival."
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        help="report counts and estimated bytes saved without mutating rows (default)",
    )
    mode_group.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="apply batched updates to chat_message",
    )
    parser.set_defaults(dry_run=True)
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_BATCH_SIZE,
        help=f"assistant rows per keyset batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--sleep-ms",
        type=_non_negative_int,
        default=DEFAULT_SLEEP_MS,
        help=f"milliseconds to sleep between committed batches in --apply mode (default: {DEFAULT_SLEEP_MS})",
    )
    parser.add_argument(
        "--idle-days",
        type=_non_negative_int,
        default=DEFAULT_IDLE_DAYS,
        help=f"archive tool_calls on sessions whose latest message is older than this many days (default: {DEFAULT_IDLE_DAYS})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from database import sync_engine

    def _on_progress(report: CompactionBackfillReport) -> None:
        print(_format_progress_line(report, final=False), flush=True)

    report = compact_historical_chat_messages(
        lambda: Session(sync_engine),
        dry_run=args.dry_run,
        batch_size=args.batch_size,
        sleep_ms=args.sleep_ms,
        idle_days=args.idle_days,
        progress_callback=_on_progress,
    )
    print(_format_progress_line(report, final=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
