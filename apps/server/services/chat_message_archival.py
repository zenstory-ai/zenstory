"""Safe, throttled copy-verify-delete archival from hot ``chat_message`` to cold ``chat_message_archive``.

Design invariants:
1. Copy-and-verify-before-delete: a hot row is NEVER deleted until its exact ``id`` is
   verified present in ``chat_message_archive``.
2. Authoritative upsert: inserts use ``ON CONFLICT (id) DO UPDATE`` so interrupted or
   retried batches are idempotent AND newer hot edits (e.g. feedback or tool-card undo)
   on dual-resident rows can never be silently dropped by a stale cold row.
3. Compacted cold payload: before writing to ``chat_message_archive``, ``tool_calls``
   runs through ``archive_historical_tool_calls_json`` + compact UTF-8 JSON serialization,
   ``message_metadata`` is re-serialized as compact UTF-8 JSON, and duplicate
   ``reasoning_content`` already stored in ``display_events`` is cleared.
4. Short transactions + throttling: processes small batches (default 500) with optional
   sleep between batches so live PostgreSQL traffic is never blocked.
5. Zero PII / content logging: logs only numeric counters and byte counts.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, or_, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlmodel import Session, col, select

from agent.core.chat_storage import (
    archive_historical_tool_calls_json,
    metadata_has_thinking_display_events,
)
from config.datetime_utils import normalize_datetime_to_utc, utcnow
from models.chat_message_archive import ChatMessageArchive, has_chat_message_archive_table
from models.chat_message_ux_summary import (
    ChatMessageUxSummary,
    build_chat_message_ux_summary_row,
    has_chat_message_ux_summary_table,
)
from models.entities import ChatMessage
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

DEFAULT_OLDER_THAN_DAYS = 7
DEFAULT_BATCH_SIZE = 500
DEFAULT_MAX_BATCHES = 20
DEFAULT_SLEEP_MS = 100

_ARCHIVE_TABLE = ChatMessageArchive.__table__
_SUMMARY_TABLE = ChatMessageUxSummary.__table__
_INSERT_CHUNK_SIZE = 100
_IN_CLAUSE_CHUNK_SIZE = 500


@dataclass
class ArchiveBatchResult:
    """Structured numeric result for one archival batch."""

    scanned: int = 0
    archived: int = 0
    deleted: int = 0
    bytes_before: int = 0
    bytes_after: int = 0
    has_more: bool = False
    oldest_remaining_at: datetime | None = None
    last_cursor: tuple[datetime, str] | None = None
    verification_failed: bool = False

    @property
    def bytes_saved(self) -> int:
        return max(0, self.bytes_before - self.bytes_after)


@dataclass
class GradualArchivalReport:
    """Structured numeric summary across all batches in a gradual archival run."""

    dry_run: bool = False
    batches_run: int = 0
    total_scanned: int = 0
    total_archived: int = 0
    total_deleted: int = 0
    total_bytes_before: int = 0
    total_bytes_after: int = 0
    has_more: bool = False
    oldest_remaining_at: datetime | None = None
    vacuum_analyzed: bool = False
    aborted: bool = False
    verification_failed: bool = False

    @property
    def bytes_saved(self) -> int:
        return max(0, self.total_bytes_before - self.total_bytes_after)


def _utf8_len(value: str | None) -> int:
    return len(value.encode("utf-8")) if isinstance(value, str) else 0


def _compact_json_utf8(raw: str | None) -> str | None:
    """Re-serialize valid JSON strings into compact UTF-8 (no ASCII escaping, no spaces)."""
    if not isinstance(raw, str) or not raw:
        return raw
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def compact_archived_tool_calls(raw_tool_calls: str | None) -> str | None:
    """Slim historical query payloads and compact JSON into UTF-8 for cold storage."""
    if not isinstance(raw_tool_calls, str) or not raw_tool_calls:
        return raw_tool_calls
    slimmed = archive_historical_tool_calls_json(raw_tool_calls)
    return _compact_json_utf8(slimmed)


def compact_archived_metadata(raw_metadata: str | None) -> str | None:
    """Compact ``message_metadata`` into UTF-8 JSON without altering semantics."""
    return _compact_json_utf8(raw_metadata)


def compact_archived_reasoning(
    raw_reasoning: str | None,
    raw_metadata: str | None,
) -> str | None:
    """Clear duplicate ``reasoning_content`` when ``display_events`` already holds it."""
    if raw_reasoning is not None and metadata_has_thinking_display_events(raw_metadata):
        return None
    return raw_reasoning


def _resolve_cutoff_naive(
    *,
    older_than_days: int,
    cutoff: datetime | None,
) -> datetime:
    if cutoff is not None:
        if cutoff.tzinfo is not None:
            return normalize_datetime_to_utc(cutoff).replace(tzinfo=None)
        return cutoff
    return utcnow().replace(tzinfo=None) - timedelta(days=older_than_days)


def _dialect_name(session: Session) -> str:
    bind = session.get_bind() if hasattr(session, "get_bind") else None
    return getattr(getattr(bind, "dialect", None), "name", "")


def _row_payload_bytes(
    *,
    content: str | None,
    tool_calls: str | None,
    reasoning_content: str | None,
    message_metadata: str | None,
) -> int:
    return (
        _utf8_len(content)
        + _utf8_len(tool_calls)
        + _utf8_len(reasoning_content)
        + _utf8_len(message_metadata)
    )


_ARCHIVE_UPSERT_COLUMNS: tuple[str, ...] = (
    "session_id",
    "role",
    "content",
    "tool_calls",
    "tool_call_id",
    "reasoning_content",
    "message_metadata",
    "created_at",
    "archived_at",
)


def _upsert_archive_rows(
    session: Session,
    archive_rows: list[dict[str, Any]],
) -> tuple[int, int]:
    """Upsert compacted hot rows into ``chat_message_archive`` via ``ON CONFLICT (id) DO UPDATE``.

    Hot rows are always authoritative when an ``id`` pre-exists in ``chat_message_archive``,
    preventing newer hot edits (e.g. feedback or undo) from being silently discarded.

    Returns ``(written_count, overwritten_count)``.
    """
    if not archive_rows:
        return 0, 0
    dialect = _dialect_name(session)
    existing_ids = _verify_archived_ids(session, [row["id"] for row in archive_rows])
    overwritten_count = len(existing_ids)
    written_total = 0

    for idx in range(0, len(archive_rows), _INSERT_CHUNK_SIZE):
        chunk = archive_rows[idx : idx + _INSERT_CHUNK_SIZE]
        if dialect == "postgresql":
            base_stmt = pg_insert(_ARCHIVE_TABLE).values(chunk)
            stmt = base_stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    col_name: getattr(base_stmt.excluded, col_name)
                    for col_name in _ARCHIVE_UPSERT_COLUMNS
                },
            )
            res = session.exec(stmt)  # type: ignore[arg-type]
            if res.rowcount is not None and res.rowcount >= 0:
                written_total += int(res.rowcount)
            else:
                written_total += len(chunk)
        elif dialect == "sqlite":
            base_stmt = sqlite_insert(_ARCHIVE_TABLE).values(chunk)
            stmt = base_stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    col_name: getattr(base_stmt.excluded, col_name)
                    for col_name in _ARCHIVE_UPSERT_COLUMNS
                },
            )
            res = session.exec(stmt)  # type: ignore[arg-type]
            if res.rowcount is not None and res.rowcount >= 0:
                written_total += int(res.rowcount)
            else:
                written_total += len(chunk)
        else:
            missing: list[dict[str, Any]] = []
            for row in chunk:
                if row["id"] in existing_ids:
                    update_values = {col_name: row[col_name] for col_name in _ARCHIVE_UPSERT_COLUMNS}
                    session.exec(
                        _ARCHIVE_TABLE.update()
                        .where(_ARCHIVE_TABLE.c.id == row["id"])
                        .values(**update_values)  # type: ignore[arg-type]
                    )
                    written_total += 1
                else:
                    missing.append(row)
            if missing:
                session.exec(_ARCHIVE_TABLE.insert().values(missing))  # type: ignore[arg-type]
                written_total += len(missing)

    return written_total, overwritten_count


def _verify_archived_ids(session: Session, message_ids: list[str]) -> set[str]:
    """Verify exact ``id`` presence in ``chat_message_archive`` before any hot delete."""
    verified: set[str] = set()
    for idx in range(0, len(message_ids), _IN_CLAUSE_CHUNK_SIZE):
        chunk = message_ids[idx : idx + _IN_CLAUSE_CHUNK_SIZE]
        found = session.exec(
            select(ChatMessageArchive.id).where(col(ChatMessageArchive.id).in_(chunk))
        ).all()
        verified.update(str(row_id) for row_id in found)
    return verified


def _assert_ux_summary_fk_removed(connection: Any) -> None:
    """Fail closed if ``chat_message_ux_summary`` still has any FK referencing ``chat_message``.

    Prevents running archival on a database where ``init_db()`` ``create_all()``
    created ``chat_message_archive`` without running ``alembic upgrade head`` to
    drop the legacy ``ON DELETE CASCADE`` FK on ``chat_message_ux_summary``.
    """
    inspector = sa_inspect(connection)
    if not inspector.has_table(ChatMessageUxSummary.__tablename__):
        return
    blocking_fks = [
        fk
        for fk in inspector.get_foreign_keys(ChatMessageUxSummary.__tablename__)
        if fk.get("referred_table") == ChatMessage.__tablename__
    ]
    if blocking_fks:
        fk_names = sorted(str(fk.get("name") or "<unnamed>") for fk in blocking_fks)
        raise RuntimeError(
            "chat_message_ux_summary still has foreign key(s) referencing chat_message "
            f"({', '.join(fk_names)}); run alembic upgrade head before archiving"
        )


def _verify_ux_summary_ids(session: Session, message_ids: list[str]) -> set[str]:
    """Return the subset of ``message_ids`` already present in ``chat_message_ux_summary``."""
    verified: set[str] = set()
    for idx in range(0, len(message_ids), _IN_CLAUSE_CHUNK_SIZE):
        chunk = message_ids[idx : idx + _IN_CLAUSE_CHUNK_SIZE]
        found = session.exec(
            select(ChatMessageUxSummary.message_id).where(
                col(ChatMessageUxSummary.message_id).in_(chunk)
            )
        ).all()
        verified.update(str(row_id) for row_id in found)
    return verified


def _insert_ux_summary_rows_idempotent(
    session: Session,
    summary_rows: list[dict[str, Any]],
) -> int:
    """Backfill missing ``chat_message_ux_summary`` rows with ``ON CONFLICT DO NOTHING``."""
    if not summary_rows:
        return 0
    dialect = _dialect_name(session)
    inserted_total = 0

    for idx in range(0, len(summary_rows), _INSERT_CHUNK_SIZE):
        chunk = summary_rows[idx : idx + _INSERT_CHUNK_SIZE]
        if dialect == "postgresql":
            stmt = (
                pg_insert(_SUMMARY_TABLE)
                .values(chunk)
                .on_conflict_do_nothing(index_elements=["message_id"])
            )
            res = session.exec(stmt)  # type: ignore[arg-type]
            if res.rowcount is not None and res.rowcount >= 0:
                inserted_total += int(res.rowcount)
        elif dialect == "sqlite":
            stmt = (
                sqlite_insert(_SUMMARY_TABLE)
                .values(chunk)
                .on_conflict_do_nothing(index_elements=["message_id"])
            )
            res = session.exec(stmt)  # type: ignore[arg-type]
            if res.rowcount is not None and res.rowcount >= 0:
                inserted_total += int(res.rowcount)
        else:
            chunk_ids = [row["message_id"] for row in chunk]
            existing = set(
                session.exec(
                    select(ChatMessageUxSummary.message_id).where(
                        col(ChatMessageUxSummary.message_id).in_(chunk_ids)
                    )
                ).all()
            )
            missing = [row for row in chunk if row["message_id"] not in existing]
            if missing:
                session.exec(_SUMMARY_TABLE.insert().values(missing))  # type: ignore[arg-type]
                inserted_total += len(missing)

    return inserted_total


def _ensure_assistant_ux_summaries(
    session: Session,
    batch: list[ChatMessage],
) -> tuple[bool, int, int]:
    """Ensure every assistant row in ``batch`` has a ``chat_message_ux_summary`` row.

    Backfills any missing summary rows inside the same transaction and re-verifies
    presence before hot deletion is permitted.

    Returns ``(all_verified, backfilled_count, missing_count)``.
    """
    if not has_chat_message_ux_summary_table(session.connection()):
        return True, 0, 0

    assistant_rows = [row for row in batch if row.role == "assistant"]
    if not assistant_rows:
        return True, 0, 0

    assistant_ids = [row.id for row in assistant_rows]
    existing_ids = _verify_ux_summary_ids(session, assistant_ids)
    missing_rows = [row for row in assistant_rows if row.id not in existing_ids]

    backfilled = 0
    if missing_rows:
        summary_payloads = [
            build_chat_message_ux_summary_row(
                message_id=row.id,
                session_id=row.session_id,
                created_at=row.created_at,
                raw_metadata=row.message_metadata,
                raw_tool_calls=row.tool_calls,
            )
            for row in missing_rows
        ]
        backfilled = _insert_ux_summary_rows_idempotent(session, summary_payloads)
        verified_ids = _verify_ux_summary_ids(session, assistant_ids)
    else:
        verified_ids = existing_ids

    missing_count = len(set(assistant_ids) - verified_ids)
    return missing_count == 0, backfilled, missing_count


def _delete_hot_rows(session: Session, message_ids: list[str]) -> int:
    """Delete verified rows from hot ``chat_message``."""
    deleted_total = 0
    for idx in range(0, len(message_ids), _IN_CLAUSE_CHUNK_SIZE):
        chunk = message_ids[idx : idx + _IN_CLAUSE_CHUNK_SIZE]
        res = session.exec(
            delete(ChatMessage).where(col(ChatMessage.id).in_(chunk))  # type: ignore[arg-type]
        )
        if res.rowcount is not None and res.rowcount >= 0:
            deleted_total += int(res.rowcount)
        else:
            deleted_total += len(chunk)
    return deleted_total


def _has_more_candidates(
    session: Session,
    *,
    cutoff_naive: datetime,
    after_cursor: tuple[datetime, str] | None,
) -> bool:
    stmt = select(ChatMessage.id).where(ChatMessage.created_at < cutoff_naive)
    if after_cursor is not None:
        cursor_created_at, cursor_id = after_cursor
        stmt = stmt.where(
            or_(
                ChatMessage.created_at > cursor_created_at,
                and_(
                    ChatMessage.created_at == cursor_created_at,
                    ChatMessage.id > cursor_id,
                ),
            )
        )
    return session.exec(stmt.limit(1)).first() is not None


def _oldest_remaining_created_at(session: Session) -> datetime | None:
    return session.exec(select(func.min(ChatMessage.created_at))).one()


def archive_chat_messages_batch(
    session: Session,
    *,
    older_than_days: int = DEFAULT_OLDER_THAN_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    dry_run: bool = False,
    cutoff: datetime | None = None,
    _after_cursor: tuple[datetime, str] | None = None,
) -> ArchiveBatchResult:
    """Archive one oldest batch of ``chat_message`` rows older than the retention cutoff.

    In ``dry_run=True`` mode, makes zero database writes and reports estimated compaction
    byte counts. In live mode, upserts with ``ON CONFLICT (id) DO UPDATE`` so authoritative
    hot edits never get silently dropped, verifies every batch ID exists in
    ``chat_message_archive``, and ONLY then deletes from ``chat_message``.
    """
    if older_than_days < 0:
        raise ValueError("older_than_days must be >= 0")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    conn = session.connection()
    if not has_chat_message_archive_table(conn):
        raise RuntimeError(
            "chat_message_archive table does not exist; run alembic upgrade head first"
        )
    # Fail closed if chat_message_ux_summary still has an ON DELETE CASCADE FK to
    # chat_message (e.g. init_db() create_all ran without alembic upgrade head).
    _assert_ux_summary_fk_removed(conn)

    cutoff_naive = _resolve_cutoff_naive(older_than_days=older_than_days, cutoff=cutoff)
    is_pg = _dialect_name(session) == "postgresql"

    stmt = (
        select(ChatMessage)
        .where(ChatMessage.created_at < cutoff_naive)
        .order_by(ChatMessage.created_at.asc(), ChatMessage.id.asc())  # type: ignore[union-attr]
    )
    if _after_cursor is not None:
        cursor_created_at, cursor_id = _after_cursor
        stmt = stmt.where(
            or_(
                ChatMessage.created_at > cursor_created_at,
                and_(
                    ChatMessage.created_at == cursor_created_at,
                    ChatMessage.id > cursor_id,
                ),
            )
        )

    if dry_run:
        rows = list(session.exec(stmt.limit(batch_size + 1)).all())
        has_more = len(rows) > batch_size
        batch = rows[:batch_size]
    else:
        live_stmt = stmt.limit(batch_size)
        if is_pg:
            live_stmt = live_stmt.with_for_update(skip_locked=True)
        batch = list(session.exec(live_stmt).all())
        has_more = False

    if not batch:
        return ArchiveBatchResult(
            scanned=0,
            archived=0,
            deleted=0,
            bytes_before=0,
            bytes_after=0,
            has_more=False,
            oldest_remaining_at=_oldest_remaining_created_at(session),
            last_cursor=_after_cursor,
        )

    archived_at = utcnow().replace(tzinfo=None)
    archive_payloads: list[dict[str, Any]] = []
    batch_ids: list[str] = []
    bytes_before = 0
    bytes_after = 0

    for row in batch:
        batch_ids.append(row.id)
        compacted_tools = compact_archived_tool_calls(row.tool_calls)
        compacted_meta = compact_archived_metadata(row.message_metadata)
        compacted_reasoning = compact_archived_reasoning(
            row.reasoning_content,
            compacted_meta,
        )
        bytes_before += _row_payload_bytes(
            content=row.content,
            tool_calls=row.tool_calls,
            reasoning_content=row.reasoning_content,
            message_metadata=row.message_metadata,
        )
        bytes_after += _row_payload_bytes(
            content=row.content,
            tool_calls=compacted_tools,
            reasoning_content=compacted_reasoning,
            message_metadata=compacted_meta,
        )
        archive_payloads.append(
            {
                "id": row.id,
                "session_id": row.session_id,
                "role": row.role,
                "content": row.content,
                "tool_calls": compacted_tools,
                "tool_call_id": row.tool_call_id,
                "reasoning_content": compacted_reasoning,
                "message_metadata": compacted_meta,
                "created_at": row.created_at,
                "archived_at": archived_at,
            }
        )

    last_row = batch[-1]
    last_cursor = (last_row.created_at, last_row.id)

    if dry_run:
        return ArchiveBatchResult(
            scanned=len(batch),
            archived=0,
            deleted=0,
            bytes_before=bytes_before,
            bytes_after=bytes_after,
            has_more=has_more,
            oldest_remaining_at=_oldest_remaining_created_at(session),
            last_cursor=last_cursor,
        )

    # Phase 1: Authoritative upsert into cold table (overwriting any stale dual-resident row).
    written, overwritten = _upsert_archive_rows(session, archive_payloads)
    if overwritten > 0:
        log_with_context(
            logger,
            30,  # WARNING
            "Archive upsert overwrote pre-existing cold row(s) via ON CONFLICT DO UPDATE",
            scanned=len(batch),
            written=written,
            overwritten=overwritten,
        )

    # Phase 2a: Verify EVERY batch id exists in chat_message_archive before deleting anything.
    verified_ids = _verify_archived_ids(session, batch_ids)
    expected_ids = set(batch_ids)
    if verified_ids != expected_ids:
        missing_count = len(expected_ids - verified_ids)
        session.rollback()
        log_with_context(
            logger,
            30,  # WARNING
            "Aborted hot chat_message delete because archive verification was incomplete",
            scanned=len(batch),
            verified=len(verified_ids),
            missing=missing_count,
        )
        return ArchiveBatchResult(
            scanned=len(batch),
            archived=0,
            deleted=0,
            bytes_before=bytes_before,
            bytes_after=bytes_after,
            has_more=_has_more_candidates(
                session, cutoff_naive=cutoff_naive, after_cursor=last_cursor
            ),
            oldest_remaining_at=_oldest_remaining_created_at(session),
            last_cursor=last_cursor,
            verification_failed=True,
        )

    # Phase 2b: Ensure every assistant row in this batch has a persisted
    # chat_message_ux_summary row (backfilling missing rows in the same
    # transaction) before deleting hot rows, so un-backfilled metrics are never orphaned.
    summaries_ok, backfilled_summaries, missing_summaries = _ensure_assistant_ux_summaries(
        session, batch
    )
    if backfilled_summaries > 0:
        log_with_context(
            logger,
            30,  # WARNING
            "Backfilled missing chat_message_ux_summary row(s) before hot delete",
            scanned=len(batch),
            backfilled=backfilled_summaries,
        )
    if not summaries_ok:
        session.rollback()
        log_with_context(
            logger,
            30,  # WARNING
            "Aborted hot chat_message delete because assistant ux_summary verification was incomplete",
            scanned=len(batch),
            missing=missing_summaries,
        )
        return ArchiveBatchResult(
            scanned=len(batch),
            archived=0,
            deleted=0,
            bytes_before=bytes_before,
            bytes_after=bytes_after,
            has_more=_has_more_candidates(
                session, cutoff_naive=cutoff_naive, after_cursor=last_cursor
            ),
            oldest_remaining_at=_oldest_remaining_created_at(session),
            last_cursor=last_cursor,
            verification_failed=True,
        )

    # Phase 3: Delete verified rows from hot table and commit the short transaction.
    deleted = _delete_hot_rows(session, batch_ids)
    session.commit()

    has_more = _has_more_candidates(
        session,
        cutoff_naive=cutoff_naive,
        after_cursor=_after_cursor,
    )
    oldest_remaining = _oldest_remaining_created_at(session)

    return ArchiveBatchResult(
        scanned=len(batch),
        archived=written,
        deleted=deleted,
        bytes_before=bytes_before,
        bytes_after=bytes_after,
        has_more=has_more,
        oldest_remaining_at=oldest_remaining,
        last_cursor=last_cursor,
    )


def _run_postgres_vacuum_analyze(session_or_factory: Session | Callable[[], Session]) -> bool:
    """Run non-blocking ``VACUUM ANALYZE chat_message`` and ``ANALYZE chat_message_archive`` on PostgreSQL only."""
    ctx = (
        contextlib.nullcontext(session_or_factory)
        if isinstance(session_or_factory, Session)
        else session_or_factory()
    )
    with ctx as session:
        bind = session.get_bind() if hasattr(session, "get_bind") else None
        dialect_name = getattr(getattr(bind, "dialect", None), "name", "")
        if dialect_name != "postgresql" or bind is None:
            return False
        engine = getattr(bind, "engine", bind)
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            conn.execute(text("VACUUM ANALYZE chat_message"))
            conn.execute(text("ANALYZE chat_message_archive"))
        return True


def run_gradual_archival(
    session_or_factory: Session | Callable[[], Session],
    *,
    older_than_days: int = DEFAULT_OLDER_THAN_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = DEFAULT_MAX_BATCHES,
    sleep_ms: int = DEFAULT_SLEEP_MS,
    dry_run: bool = False,
    cutoff: datetime | None = None,
    vacuum_analyze: bool = False,
    progress_callback: Callable[[int, ArchiveBatchResult, GradualArchivalReport], None] | None = None,
) -> GradualArchivalReport:
    """Archive historical ``chat_message`` rows gradually in bounded, throttled batches."""
    if older_than_days < 0:
        raise ValueError("older_than_days must be >= 0")
    if batch_size < 1:
        raise ValueError("batch_size must be >= 1")
    if max_batches < 1:
        raise ValueError("max_batches must be >= 1")
    if sleep_ms < 0:
        raise ValueError("sleep_ms must be >= 0")

    effective_cutoff = _resolve_cutoff_naive(older_than_days=older_than_days, cutoff=cutoff)
    report = GradualArchivalReport(dry_run=dry_run)
    cursor: tuple[datetime, str] | None = None

    for batch_num in range(1, max_batches + 1):
        ctx = (
            contextlib.nullcontext(session_or_factory)
            if isinstance(session_or_factory, Session)
            else session_or_factory()
        )
        with ctx as session:
            batch_res = archive_chat_messages_batch(
                session,
                older_than_days=older_than_days,
                batch_size=batch_size,
                dry_run=dry_run,
                cutoff=effective_cutoff,
                _after_cursor=cursor,
            )

        report.batches_run = batch_num
        report.total_scanned += batch_res.scanned
        report.total_archived += batch_res.archived
        report.total_deleted += batch_res.deleted
        report.total_bytes_before += batch_res.bytes_before
        report.total_bytes_after += batch_res.bytes_after
        report.has_more = batch_res.has_more
        report.oldest_remaining_at = batch_res.oldest_remaining_at

        log_with_context(
            logger,
            20,  # INFO
            "Chat message archival batch completed",
            dry_run=dry_run,
            batch=batch_num,
            scanned=batch_res.scanned,
            archived=batch_res.archived,
            deleted=batch_res.deleted,
            bytes_before=batch_res.bytes_before,
            bytes_after=batch_res.bytes_after,
            has_more=batch_res.has_more,
        )

        if progress_callback is not None:
            progress_callback(batch_num, batch_res, report)

        if not dry_run and (
            batch_res.verification_failed
            or (batch_res.scanned > 0 and batch_res.deleted == 0)
        ):
            report.aborted = True
            report.verification_failed = True
            log_with_context(
                logger,
                40,  # ERROR
                "Aborting gradual chat message archival after batch verification failure",
                batch=batch_num,
                scanned=batch_res.scanned,
            )
            break

        if batch_res.scanned == 0 or not batch_res.has_more:
            break

        # Advance cursor in dry-run mode (rows are not deleted) so we scan distinct batches.
        if dry_run:
            cursor = batch_res.last_cursor

        if batch_num < max_batches and not dry_run and sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

    if vacuum_analyze and not dry_run and not report.aborted and report.total_deleted > 0:
        report.vacuum_analyzed = _run_postgres_vacuum_analyze(session_or_factory)
        log_with_context(
            logger,
            20,  # INFO
            "Post-archival VACUUM ANALYZE check completed",
            vacuum_analyzed=report.vacuum_analyzed,
        )

    return report
