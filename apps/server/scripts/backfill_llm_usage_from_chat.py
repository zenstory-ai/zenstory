#!/usr/bin/env python3
"""
One-off backfill: copy agent chat usage into the ``llm_usage_event`` ledger.

Reads assistant ``chat_message`` rows whose ``message_metadata.usage`` uses the
canonical keys written since 2026-10-03 (``input_tokens`` = cache-miss part,
``cache_read_tokens`` = cache-hit part, ``output_tokens``, ``total_tokens``;
zero-valued keys are omitted by the writer). Each becomes one ledger row with
``source='agent'``, ``is_backfilled=true`` and
``correlation_id='chat_message:<id>'``, billed to the chat session's user and
priced by the message's ``created_at`` (one row per turn, so the band is an
approximation for turns that straddle a band edge).

Safe to re-run: messages that already have a backfilled row are skipped.
Messages created at or after the first live-metered agent/router row are
skipped too, because those turns are already in the ledger per call; pass
``--before`` to override that cutoff.

Usage:
    python scripts/backfill_llm_usage_from_chat.py --dry-run
    python scripts/backfill_llm_usage_from_chat.py

Honors ``DATABASE_URL`` (falls back to the local SQLite dev database).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("DATABASE_URL", "sqlite:///./zenstory.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func  # noqa: E402
from sqlmodel import Session, select  # noqa: E402

from models.entities import ChatMessage, ChatSession  # noqa: E402
from models.llm_usage import (  # noqa: E402
    LLM_USAGE_SOURCE_AGENT,
    LLM_USAGE_SOURCE_ROUTER,
    LLMUsageEvent,
)
from services.usage.llm_usage_service import UsageTokens, build_usage_event  # noqa: E402

BACKFILL_PREFIX = "chat_message:"
BACKFILL_MODEL = "deepseek-flash"
CANONICAL_KEYS = frozenset({"cache_read_tokens", "input_tokens", "output_tokens", "total_tokens"})
# Keys of the pre-2026-06 Anthropic-format usage; such rows are not backfilled.
LEGACY_KEYS = frozenset({"cache_read_input_tokens", "cache_creation_input_tokens"})
BATCH_SIZE = 500


@dataclass
class BackfillReport:
    scanned: int = 0
    inserted: int = 0
    already_backfilled: int = 0
    skipped_format: int = 0
    cutoff: datetime | None = None


def _int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return max(0, int(value))


def tokens_from_metadata(raw: str | None) -> UsageTokens | None:
    """Usage tokens of one assistant message, or None when not in canonical format."""
    if not raw:
        return None
    try:
        metadata = json.loads(raw)
    except (TypeError, ValueError):
        return None
    usage = metadata.get("usage") if isinstance(metadata, dict) else None
    if not isinstance(usage, dict):
        return None
    keys = set(usage)
    # The canonical writer always sets total_tokens; the legacy Anthropic format never did.
    if keys & LEGACY_KEYS or "total_tokens" not in keys or not keys & (CANONICAL_KEYS - {"total_tokens"}):
        return None
    tokens = UsageTokens(
        cache_hit=_int(usage.get("cache_read_tokens")),
        cache_miss=_int(usage.get("input_tokens")),
        output=_int(usage.get("output_tokens")),
    )
    return tokens if tokens.total > 0 else None


def live_metering_start(session: Session) -> datetime | None:
    """First agent/router row written by live metering, if any."""
    return session.exec(
        select(func.min(LLMUsageEvent.occurred_at)).where(
            LLMUsageEvent.is_backfilled.is_(False),  # type: ignore[union-attr]
            LLMUsageEvent.source.in_((LLM_USAGE_SOURCE_AGENT, LLM_USAGE_SOURCE_ROUTER)),  # type: ignore[attr-defined]
        )
    ).one()


def _already_backfilled(session: Session) -> set[str]:
    rows = session.exec(
        select(LLMUsageEvent.correlation_id).where(LLMUsageEvent.is_backfilled.is_(True))  # type: ignore[union-attr]
    ).all()
    return {row for row in rows if row}


def _candidates(session: Session, cutoff: datetime | None) -> Iterator[tuple[ChatMessage, ChatSession]]:
    query = (
        select(ChatMessage, ChatSession)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .where(
            ChatMessage.role == "assistant",
            ChatMessage.message_metadata.is_not(None),  # type: ignore[union-attr]
            ChatMessage.message_metadata.contains('"usage"'),  # type: ignore[union-attr]
        )
        .order_by(ChatMessage.created_at, ChatMessage.id)
    )
    if cutoff is not None:
        query = query.where(ChatMessage.created_at < cutoff)
    offset = 0
    while True:
        batch = session.exec(query.offset(offset).limit(BATCH_SIZE)).all()
        if not batch:
            return
        yield from batch
        offset += len(batch)


def backfill(
    session_factory: Callable[[], Session],
    *,
    dry_run: bool = False,
    before: datetime | None = None,
) -> BackfillReport:
    report = BackfillReport()
    with session_factory() as session:
        report.cutoff = before or live_metering_start(session)
        done = _already_backfilled(session)
        pending: list[LLMUsageEvent] = []
        for message, chat_session in _candidates(session, report.cutoff):
            report.scanned += 1
            correlation_id = f"{BACKFILL_PREFIX}{message.id}"
            if correlation_id in done:
                report.already_backfilled += 1
                continue
            tokens = tokens_from_metadata(message.message_metadata)
            if tokens is None:
                report.skipped_format += 1
                continue
            pending.append(
                build_usage_event(
                    user_id=chat_session.user_id,
                    source=LLM_USAGE_SOURCE_AGENT,
                    model=BACKFILL_MODEL,
                    tokens=tokens,
                    project_id=chat_session.project_id,
                    correlation_id=correlation_id,
                    occurred_at=message.created_at,
                    is_backfilled=True,
                )
            )
            done.add(correlation_id)
        report.inserted = len(pending)
        if not dry_run and pending:
            session.add_all(pending)
            session.commit()
    return report


def _parse_before(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        from services.usage.pricing import naive_utc

        parsed = naive_utc(parsed)
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--dry-run", action="store_true", help="count rows without writing")
    parser.add_argument(
        "--before",
        type=_parse_before,
        default=None,
        help="only messages created before this UTC time (ISO 8601); "
        "defaults to the first live-metered agent/router row",
    )
    args = parser.parse_args(argv)

    from database import sync_engine

    report = backfill(lambda: Session(sync_engine), dry_run=args.dry_run, before=args.before)
    verb = "Would insert" if args.dry_run else "Inserted"
    print(
        f"{verb} {report.inserted} row(s); scanned {report.scanned}, "
        f"already backfilled {report.already_backfilled}, "
        f"skipped (other usage format) {report.skipped_format}; "
        f"cutoff {report.cutoff.isoformat() if report.cutoff else 'none'}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
