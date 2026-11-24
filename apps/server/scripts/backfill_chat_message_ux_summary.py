#!/usr/bin/env python3
"""Idempotent CLI backfill for ``chat_message_ux_summary``.

Usage:
    python scripts/backfill_chat_message_ux_summary.py --dry-run
    python scripts/backfill_chat_message_ux_summary.py --batch-size 500
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

SERVER_DIR = Path(__file__).resolve().parents[1]
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

from database import sync_engine  # noqa: E402
from services.usage.chat_message_ux_summary_backfill import (  # noqa: E402
    DEFAULT_BACKFILL_BATCH_SIZE,
    backfill_chat_message_ux_summaries,
    count_pending_chat_message_ux_summaries,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill chat_message_ux_summary rows.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BACKFILL_BATCH_SIZE,
        help="Rows per committed batch (default: 500).",
    )
    parser.add_argument(
        "--max-batches",
        type=int,
        default=None,
        help="Optional cap on number of batches to process in this run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only report how many assistant chat_message rows are pending backfill.",
    )
    args = parser.parse_args(argv)

    with sync_engine.connect() as conn:
        pending = count_pending_chat_message_ux_summaries(conn)
        print(f"Pending assistant chat_message rows: {pending}")
        if args.dry_run or pending == 0:
            return 0

        t0 = time.perf_counter()
        processed = backfill_chat_message_ux_summaries(
            conn,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            commit_each_batch=True,
        )
        elapsed = time.perf_counter() - t0
        remaining = count_pending_chat_message_ux_summaries(conn)
        print(
            f"Backfilled {processed} rows in {elapsed:.2f}s (remaining: {remaining})."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
