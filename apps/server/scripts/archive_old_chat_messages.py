#!/usr/bin/env python3
"""
Gradual, copy-verify-delete archival CLI for moving >7d ``chat_message`` rows to
``chat_message_archive``.

Safety invariants:
- Processes small short-transaction batches (default 500) with inter-batch throttling.
- Copies compacted UTF-8 JSON rows into ``chat_message_archive`` with
  ``ON CONFLICT (id) DO NOTHING``, verifies every ID exists in the archive table,
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

load_dotenv()
os.environ.setdefault("DATABASE_URL", "sqlite:///./zenstory.db")
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
        type=_non_negative_int,
        default=DEFAULT_OLDER_THAN_DAYS,
        help=f"archive rows strictly older than this many days (default: {DEFAULT_OLDER_THAN_DAYS})",
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


def _format_summary_line(report: GradualArchivalReport) -> str:
    mode = "dry_run" if report.dry_run else "apply"
    oldest = (
        report.oldest_remaining_at.isoformat()
        if report.oldest_remaining_at is not None
        else "none"
    )
    return (
        f"[summary] mode={mode} batches={report.batches_run} "
        f"scanned={report.total_scanned} archived={report.total_archived} "
        f"deleted={report.total_deleted} bytes_before={report.total_bytes_before} "
        f"bytes_after={report.total_bytes_after} bytes_saved={report.bytes_saved} "
        f"has_more={report.has_more} oldest_remaining_at={oldest} "
        f"vacuum_analyzed={report.vacuum_analyzed}"
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    from database import sync_engine

    def _on_progress(
        batch_num: int,
        batch_res: ArchiveBatchResult,
        report: GradualArchivalReport,
    ) -> None:
        print(_format_batch_line(batch_num, batch_res, report), flush=True)

    report = run_gradual_archival(
        lambda: Session(sync_engine),
        older_than_days=args.older_than_days,
        batch_size=args.batch_size,
        max_batches=args.max_batches,
        sleep_ms=args.sleep_ms,
        dry_run=args.dry_run,
        vacuum_analyze=args.vacuum_analyze,
        progress_callback=_on_progress,
    )
    print(_format_summary_line(report), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
