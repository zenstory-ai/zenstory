#!/usr/bin/env python3
"""
Gradual, copy-verify-delete archival CLI for moving >7d ``chat_message`` rows to
``chat_message_archive``.

Safety invariants:
- Processes small short-transaction batches (default 500) with inter-batch throttling.
- Copies compacted UTF-8 JSON rows into ``chat_message_archive`` with
  ``ON CONFLICT (id) DO UPDATE``, verifies every ID exists in the archive table,
  and ONLY then deletes those rows from hot ``chat_message``.
- Never runs ``VACUUM FULL``; optional ``--vacuum-analyze`` (off by default) runs
  non-blocking ``VACUUM ANALYZE chat_message`` on PostgreSQL only.
- Prints numeric-only progress and byte counts (never prints message text or PII).

Usage:
    python scripts/archive_old_chat_messages.py --dry-run
    python scripts/archive_old_chat_messages.py --older-than-days 7 --batch-size 500 --max-batches 20 --sleep-ms 100
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session  # noqa: E402

from services.chat_message_archival import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_BATCHES,
    DEFAULT_OLDER_THAN_DAYS,
    DEFAULT_SLEEP_MS,
    ArchiveBatchResult,
    GradualArchivalReport,
    run_gradual_archival,
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
        description=(
            "Archive historical chat_message rows older than --older-than-days into "
            "chat_message_archive using safe copy-verify-delete batches."
        )
    )
    parser.add_argument(
        "--older-than-days",
        type=_positive_int,
        default=DEFAULT_OLDER_THAN_DAYS,
        help=f"archive rows strictly older than this many days (>= 1, default: {DEFAULT_OLDER_THAN_DAYS})",
    )
    parser.add_argument(
        "--batch-size",
        type=_positive_int,
        default=DEFAULT_BATCH_SIZE,
        help=f"maximum rows per short transaction batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--max-batches",
        type=_positive_int,
        default=DEFAULT_MAX_BATCHES,
        help=f"maximum number of batches to run in this invocation (default: {DEFAULT_MAX_BATCHES})",
    )
    parser.add_argument(
        "--sleep-ms",
        type=_non_negative_int,
        default=DEFAULT_SLEEP_MS,
        help=f"milliseconds to sleep between committed batches (default: {DEFAULT_SLEEP_MS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="scan and report estimated bytes saved without inserting or deleting rows",
    )
    parser.add_argument(
        "--vacuum-analyze",
        action="store_true",
        default=False,
        help="run non-blocking VACUUM ANALYZE chat_message on PostgreSQL after deleting rows (off by default)",
    )
    return parser


def _format_batch_line(
    batch_num: int,
    batch_res: ArchiveBatchResult,
    report: GradualArchivalReport,
) -> str:
    mode = "dry_run" if report.dry_run else "apply"
    return (
        f"[batch={batch_num}] mode={mode} scanned={batch_res.scanned} "
        f"archived={batch_res.archived} deleted={batch_res.deleted} "
        f"bytes_before={batch_res.bytes_before} bytes_after={batch_res.bytes_after} "
        f"bytes_saved={batch_res.bytes_saved} has_more={batch_res.has_more}"
    )


def _resolve_postgres_target(raw_url: str | None) -> tuple[URL, str, str]:
    """Validate an explicit PostgreSQL DATABASE_URL and return ``(sync_url, dialect, redacted_host)``.

    Fails closed (raises ``ValueError`` without echoing credentials) when ``DATABASE_URL`` is unset,
    unparseable, or targets a non-PostgreSQL dialect such as SQLite.
    """
    value = (raw_url or "").strip()
    if not value:
        raise ValueError("DATABASE_URL is required and must point to a PostgreSQL database.")
    try:
        parsed = make_url(value)
    except Exception as exc:  # noqa: BLE001 - never echo raw DATABASE_URL or credentials
        raise ValueError("DATABASE_URL could not be parsed as a valid PostgreSQL URL.") from exc
    dialect = parsed.get_backend_name()
    if dialect != "postgresql":
        raise ValueError(
            f"Refusing non-PostgreSQL DATABASE_URL (dialect={dialect}); this CLI only runs against PostgreSQL."
        )
    query_host = parsed.query.get("host")
    host = parsed.host or (query_host if isinstance(query_host, str) and query_host else "local")
    sync_url = parsed.set(drivername="postgresql+psycopg")
    return sync_url, dialect, host


def _build_postgres_sync_engine(sync_url: URL):
    existing_options = sync_url.query.get("options", "")
    if isinstance(existing_options, tuple):
        existing_options = " ".join(existing_options)
    sync_options = " ".join(part for part in (existing_options.strip(), "-c timezone=UTC") if part)
    return create_engine(
        sync_url,
        connect_args={"options": sync_options},
        pool_pre_ping=True,
    )


def _format_summary_line(
    report: GradualArchivalReport,
    *,
    dialect: str = "unknown",
    host: str = "unknown",
) -> str:
    mode = "dry_run" if report.dry_run else "apply"
    oldest = (
        report.oldest_remaining_at.isoformat()
        if report.oldest_remaining_at is not None
        else "none"
    )
    return (
        f"[summary] dialect={dialect} host={host} mode={mode} batches={report.batches_run} "
        f"scanned={report.total_scanned} archived={report.total_archived} "
        f"deleted={report.total_deleted} bytes_before={report.total_bytes_before} "
        f"bytes_after={report.total_bytes_after} bytes_saved={report.bytes_saved} "
        f"has_more={report.has_more} oldest_remaining_at={oldest} "
        f"vacuum_analyzed={report.vacuum_analyzed} aborted={report.aborted}"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        sync_url, dialect, host = _resolve_postgres_target(os.getenv("DATABASE_URL"))
    except ValueError as exc:
        print(f"[error] {exc}", file=sys.stderr, flush=True)
        return 2

    engine = _build_postgres_sync_engine(sync_url)

    def _on_progress(
        batch_num: int,
        batch_res: ArchiveBatchResult,
        report: GradualArchivalReport,
    ) -> None:
        print(_format_batch_line(batch_num, batch_res, report), flush=True)

    try:
        report = run_gradual_archival(
            lambda: Session(engine),
            older_than_days=args.older_than_days,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            sleep_ms=args.sleep_ms,
            dry_run=args.dry_run,
            vacuum_analyze=args.vacuum_analyze,
            progress_callback=_on_progress,
        )
    finally:
        engine.dispose()
    print(_format_summary_line(report, dialect=dialect, host=host), flush=True)
    return 1 if report.aborted else 0


if __name__ == "__main__":
    raise SystemExit(main())
