"""Idempotent, batched backfill for ``chat_message_ux_summary``.

Processes assistant ``chat_message`` rows newest-first (so active 7d/30d/60d
analytics windows converge first) and never mutates ``chat_message``.

On PostgreSQL, extraction runs server-side in single-pass ``::json`` SQL batches
with zero TOAST egress over the network; any batch containing malformed JSON
automatically falls back to Python extraction for just those rows.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DataError

from models.chat_message_ux_summary import (
    ChatMessageUxSummary,
    build_chat_message_ux_summary_row,
)

logger = logging.getLogger(__name__)

DEFAULT_BACKFILL_BATCH_SIZE = 500
_SUMMARY_TABLE = ChatMessageUxSummary.__table__

_PG_SELECT_PENDING_IDS_SQL = text(
    """
    SELECT cm.id
    FROM chat_message cm
    WHERE cm.role = 'assistant'
      AND NOT EXISTS (
          SELECT 1
          FROM chat_message_ux_summary s
          WHERE s.message_id = cm.id
      )
    ORDER BY cm.created_at DESC, cm.id DESC
    LIMIT :batch_size
    """
)

# Single-pass server-side JSON extraction (uses ``json`` rather than ``jsonb``
# to avoid binary key-sorting trees and legacy ``\u0000`` failures). Parity
# verified against Python's ``build_chat_message_ux_summary_row``.
_PG_INSERT_BATCH_SQL = text(
    r"""
    WITH batch AS (
        SELECT
            cm.id,
            cm.session_id,
            cm.created_at,
            CASE
                WHEN cm.message_metadata IS NOT NULL
                 AND left(ltrim(cm.message_metadata, E' \t\r\n'), 1) = '{'
                THEN cm.message_metadata::json
                ELSE NULL
            END AS meta,
            CASE
                WHEN cm.tool_calls IS NOT NULL
                 AND left(ltrim(cm.tool_calls, E' \t\r\n'), 1) = '['
                 AND (
                     strpos(cm.tool_calls, '"edit_file"') > 0
                     OR strpos(cm.tool_calls, '"write_chapter"') > 0
                     OR strpos(cm.tool_calls, '"batch_replace_files"') > 0
                     OR strpos(cm.tool_calls, '"delete_file"') > 0
                 )
                THEN cm.tool_calls::json
                ELSE NULL
            END AS calls
        FROM chat_message cm
        WHERE cm.id = ANY(:ids)
    ),
    meta_fields AS (
        SELECT
            b.id,
            b.session_id,
            b.created_at,
            CASE
                WHEN b.meta IS NOT NULL AND json_typeof(b.meta) = 'object'
                     AND json_typeof(b.meta->'stop_reason') = 'string'
                THEN btrim(b.meta->>'stop_reason')
                ELSE ''
            END AS stop_reason,
            CASE
                WHEN b.meta IS NOT NULL AND json_typeof(b.meta) = 'object'
                     AND json_typeof(b.meta->'stop_outcome') = 'object'
                THEN b.meta->'stop_outcome'
                ELSE NULL
            END AS stop_outcome,
            CASE
                WHEN b.meta IS NOT NULL AND json_typeof(b.meta) = 'object'
                     AND (b.meta->'runaway_stop') IS NOT NULL
                     AND json_typeof(b.meta->'runaway_stop') <> 'null'
                     AND NOT (json_typeof(b.meta->'runaway_stop') = 'boolean' AND (b.meta->>'runaway_stop') = 'false')
                     AND NOT (json_typeof(b.meta->'runaway_stop') = 'string' AND (b.meta->>'runaway_stop') = '')
                     AND NOT (json_typeof(b.meta->'runaway_stop') = 'number' AND (b.meta->>'runaway_stop')::numeric = 0)
                     AND NOT (json_typeof(b.meta->'runaway_stop') = 'array' AND json_array_length(b.meta->'runaway_stop') = 0)
                     AND NOT (json_typeof(b.meta->'runaway_stop') = 'object' AND (b.meta->'runaway_stop')::text = '{}')
                THEN TRUE
                ELSE FALSE
            END AS meta_runaway_stop,
            CASE
                WHEN b.meta IS NOT NULL AND json_typeof(b.meta) = 'object'
                     AND json_typeof(b.meta->'status_cards') = 'array'
                THEN (
                    SELECT
                        CASE MIN(
                            CASE
                                WHEN json_typeof(card->'limit') = 'string'
                                     AND btrim(card->>'limit') = 'wall_clock' THEN 0
                                WHEN json_typeof(card->'layer') = 'string'
                                     AND btrim(card->>'layer') = 'unexecuted_tool_text' THEN 1
                                WHEN json_typeof(card->'layer') = 'string'
                                     AND btrim(card->>'layer') = 'empty_file_correction' THEN 2
                                WHEN json_typeof(card->'layer') = 'string'
                                     AND btrim(card->>'layer') = 'tool_call' THEN 3
                                WHEN json_typeof(card->'layer') = 'string'
                                     AND btrim(card->>'layer') = 'collaboration' THEN 4
                                ELSE 5
                            END
                        )
                            WHEN 0 THEN 'run_deadline_exceeded'
                            WHEN 1 THEN 'unexecuted_tool_text'
                            WHEN 2 THEN 'empty_file_correction'
                            WHEN 3 THEN 'max_turns_exceeded'
                            WHEN 4 THEN 'iteration_exhausted'
                            WHEN 5 THEN 'iteration_exhausted'
                            ELSE NULL
                        END
                    FROM json_array_elements(b.meta->'status_cards') AS card
                    WHERE json_typeof(card) = 'object'
                      AND json_typeof(card->'type') = 'string'
                      AND (card->>'type') = 'iteration_exhausted'
                )
                ELSE NULL
            END AS iteration_exhausted_reason,
            CASE
                WHEN b.meta IS NOT NULL AND json_typeof(b.meta) = 'object'
                     AND json_typeof(b.meta->'feedback') = 'object'
                     AND json_typeof(b.meta->'feedback'->'vote') = 'string'
                     AND (b.meta->'feedback'->>'vote') IN ('up', 'down')
                THEN b.meta->'feedback'->>'vote'
                ELSE NULL
            END AS feedback_vote,
            CASE
                WHEN b.meta IS NOT NULL AND json_typeof(b.meta) = 'object'
                     AND json_typeof(b.meta->'feedback') = 'object'
                     AND json_typeof(b.meta->'feedback'->'vote') = 'string'
                     AND (b.meta->'feedback'->>'vote') IN ('up', 'down')
                     AND json_typeof(b.meta->'feedback'->'preset') = 'string'
                     AND NULLIF(btrim(b.meta->'feedback'->>'preset'), '') IS NOT NULL
                THEN left(btrim(b.meta->'feedback'->>'preset'), 128)
                ELSE NULL
            END AS feedback_preset
        FROM batch b
    ),
    round_eval AS (
        SELECT
            mf.*,
            CASE
                WHEN mf.stop_outcome IS NOT NULL AND json_typeof(mf.stop_outcome->'reason') = 'string'
                THEN btrim(mf.stop_outcome->>'reason')
                ELSE ''
            END AS outcome_reason,
            CASE
                WHEN mf.stop_outcome IS NOT NULL AND (mf.stop_outcome->'unexecuted_tool_text') IS NOT NULL
                     AND json_typeof(mf.stop_outcome->'unexecuted_tool_text') <> 'null'
                     AND NOT (json_typeof(mf.stop_outcome->'unexecuted_tool_text') = 'boolean' AND (mf.stop_outcome->>'unexecuted_tool_text') = 'false')
                     AND NOT (json_typeof(mf.stop_outcome->'unexecuted_tool_text') = 'string' AND (mf.stop_outcome->>'unexecuted_tool_text') = '')
                     AND NOT (json_typeof(mf.stop_outcome->'unexecuted_tool_text') = 'number' AND (mf.stop_outcome->>'unexecuted_tool_text')::numeric = 0)
                     AND NOT (json_typeof(mf.stop_outcome->'unexecuted_tool_text') = 'array' AND json_array_length(mf.stop_outcome->'unexecuted_tool_text') = 0)
                     AND NOT (json_typeof(mf.stop_outcome->'unexecuted_tool_text') = 'object' AND (mf.stop_outcome->'unexecuted_tool_text')::text = '{}')
                THEN TRUE ELSE FALSE
            END AS has_unexecuted_tool_text,
            CASE
                WHEN mf.stop_outcome IS NOT NULL AND (mf.stop_outcome->'unwritten_files') IS NOT NULL
                     AND json_typeof(mf.stop_outcome->'unwritten_files') <> 'null'
                     AND NOT (json_typeof(mf.stop_outcome->'unwritten_files') = 'boolean' AND (mf.stop_outcome->>'unwritten_files') = 'false')
                     AND NOT (json_typeof(mf.stop_outcome->'unwritten_files') = 'string' AND (mf.stop_outcome->>'unwritten_files') = '')
                     AND NOT (json_typeof(mf.stop_outcome->'unwritten_files') = 'number' AND (mf.stop_outcome->>'unwritten_files')::numeric = 0)
                     AND NOT (json_typeof(mf.stop_outcome->'unwritten_files') = 'array' AND json_array_length(mf.stop_outcome->'unwritten_files') = 0)
                     AND NOT (json_typeof(mf.stop_outcome->'unwritten_files') = 'object' AND (mf.stop_outcome->'unwritten_files')::text = '{}')
                THEN TRUE ELSE FALSE
            END AS has_unwritten_files,
            CASE
                WHEN mf.stop_outcome IS NOT NULL AND (mf.stop_outcome->'runaway_stop') IS NOT NULL
                     AND json_typeof(mf.stop_outcome->'runaway_stop') <> 'null'
                     AND NOT (json_typeof(mf.stop_outcome->'runaway_stop') = 'boolean' AND (mf.stop_outcome->>'runaway_stop') = 'false')
                     AND NOT (json_typeof(mf.stop_outcome->'runaway_stop') = 'string' AND (mf.stop_outcome->>'runaway_stop') = '')
                     AND NOT (json_typeof(mf.stop_outcome->'runaway_stop') = 'number' AND (mf.stop_outcome->>'runaway_stop')::numeric = 0)
                     AND NOT (json_typeof(mf.stop_outcome->'runaway_stop') = 'array' AND json_array_length(mf.stop_outcome->'runaway_stop') = 0)
                     AND NOT (json_typeof(mf.stop_outcome->'runaway_stop') = 'object' AND (mf.stop_outcome->'runaway_stop')::text = '{}')
                THEN TRUE ELSE FALSE
            END AS outcome_runaway_stop
        FROM meta_fields mf
    ),
    round_classified AS (
        SELECT
            re.id,
            re.session_id,
            re.created_at,
            re.feedback_vote,
            re.feedback_preset,
            CASE
                WHEN eff.effective_reason = 'user_stopped' OR re.outcome_reason = 'user_stopped' THEN 'user_stopped'
                WHEN eff.effective_reason = 'client_disconnected' OR re.outcome_reason = 'client_disconnected' THEN 'client_disconnected'
                WHEN eff.effective_reason = 'end_turn'
                     AND NOT re.has_unexecuted_tool_text
                     AND NOT re.has_unwritten_files
                     AND NOT eff.has_runaway_stop
                     AND re.iteration_exhausted_reason IS NULL
                     AND re.outcome_reason <> 'incomplete'
                THEN 'healthy'
                ELSE 'abnormal'
            END AS round_category,
            CASE
                WHEN eff.effective_reason = 'user_stopped' OR re.outcome_reason = 'user_stopped' THEN NULL
                WHEN eff.effective_reason = 'client_disconnected' OR re.outcome_reason = 'client_disconnected' THEN NULL
                WHEN eff.effective_reason = 'end_turn'
                     AND NOT re.has_unexecuted_tool_text
                     AND NOT re.has_unwritten_files
                     AND NOT eff.has_runaway_stop
                     AND re.iteration_exhausted_reason IS NULL
                     AND re.outcome_reason <> 'incomplete'
                THEN NULL
                WHEN eff.effective_reason = 'end_turn' THEN
                    CASE
                        WHEN re.has_unexecuted_tool_text THEN 'unexecuted_tool_text'
                        WHEN eff.has_runaway_stop THEN 'runaway_stop'
                        WHEN re.iteration_exhausted_reason IS NOT NULL THEN re.iteration_exhausted_reason
                        WHEN re.has_unwritten_files THEN 'unwritten_files'
                        ELSE 'incomplete'
                    END
                ELSE left(eff.effective_reason, 128)
            END AS abnormal_reason
        FROM round_eval re
        CROSS JOIN LATERAL (
            SELECT
                COALESCE(NULLIF(re.stop_reason, ''), NULLIF(re.outcome_reason, ''), 'unknown') AS effective_reason,
                (
                    re.meta_runaway_stop
                    OR re.outcome_runaway_stop
                    OR COALESCE(NULLIF(re.stop_reason, ''), NULLIF(re.outcome_reason, ''), 'unknown') IN ('repetition_loop', 'runaway_stop')
                ) AS has_runaway_stop
        ) eff
    ),
    card_counts AS (
        SELECT
            b.id,
            COALESCE(SUM(ec.edit_cards), 0) AS edit_cards,
            COALESCE(SUM(ec.edit_undone), 0) AS edit_undone,
            COUNT(*) FILTER (
                WHERE json_typeof(elem->'name') = 'string'
                  AND (elem->>'name') = 'delete_file'
                  AND (
                      (json_typeof(elem->'result') = 'boolean' AND (elem->>'result') = 'true')
                      OR (
                          json_typeof(d.data) = 'object'
                          AND (d.data->'error') IS NULL
                          AND (
                              (json_typeof(d.data->'mutation_applied') = 'boolean' AND (d.data->>'mutation_applied') = 'true')
                              OR (json_typeof(d.data->'restored') = 'boolean' AND (d.data->>'restored') = 'true')
                              OR ((d.data->'deleted_at') IS NOT NULL AND json_typeof(d.data->'deleted_at') <> 'null')
                          )
                      )
                  )
            ) AS delete_cards,
            COUNT(*) FILTER (
                WHERE json_typeof(elem->'name') = 'string'
                  AND (elem->>'name') = 'delete_file'
                  AND json_typeof(d.data) = 'object'
                  AND (d.data->'error') IS NULL
                  AND json_typeof(d.data->'restored') = 'boolean'
                  AND (d.data->>'restored') = 'true'
            ) AS delete_restored
        FROM batch b
        CROSS JOIN LATERAL json_array_elements(b.calls) AS elem
        CROSS JOIN LATERAL (
            SELECT CASE
                WHEN json_typeof(elem->'result') = 'object'
                     AND json_typeof(elem->'result'->'data') = 'object'
                THEN elem->'result'->'data'
                ELSE elem->'result'
            END AS data
        ) d
        CROSS JOIN LATERAL (
            SELECT
                CASE
                    WHEN json_typeof(elem->'name') = 'string'
                         AND (elem->>'name') IN ('edit_file', 'write_chapter')
                         AND json_typeof(d.data) = 'object'
                         AND json_typeof(d.data->'undo') = 'object'
                    THEN 1
                    WHEN json_typeof(elem->'name') = 'string'
                         AND (elem->>'name') = 'batch_replace_files'
                         AND json_typeof(d.data) = 'object'
                         AND NOT COALESCE(
                             json_typeof(d.data->'dry_run') = 'boolean' AND (d.data->>'dry_run') = 'true',
                             FALSE
                         )
                         AND (
                             CASE
                                 WHEN json_typeof(d.data->'modified_files') = 'number'
                                 THEN (d.data->>'modified_files')::numeric <> 0
                                 ELSE TRUE
                             END
                         )
                         AND json_typeof(d.data->'files') = 'array'
                    THEN (
                        SELECT COUNT(*)::int
                        FROM json_array_elements(d.data->'files') AS f
                        WHERE json_typeof(f) = 'object'
                          AND json_typeof(f->'undo') = 'object'
                    )
                    ELSE 0
                END AS edit_cards,
                CASE
                    WHEN json_typeof(elem->'name') = 'string'
                         AND (elem->>'name') IN ('edit_file', 'write_chapter')
                         AND json_typeof(d.data) = 'object'
                         AND json_typeof(d.data->'undo') = 'object'
                         AND json_typeof(d.data->'undo'->'undone') = 'boolean'
                         AND (d.data->'undo'->>'undone') = 'true'
                    THEN 1
                    WHEN json_typeof(elem->'name') = 'string'
                         AND (elem->>'name') = 'batch_replace_files'
                         AND json_typeof(d.data) = 'object'
                         AND NOT COALESCE(
                             json_typeof(d.data->'dry_run') = 'boolean' AND (d.data->>'dry_run') = 'true',
                             FALSE
                         )
                         AND (
                             CASE
                                 WHEN json_typeof(d.data->'modified_files') = 'number'
                                 THEN (d.data->>'modified_files')::numeric <> 0
                                 ELSE TRUE
                             END
                         )
                         AND json_typeof(d.data->'files') = 'array'
                    THEN (
                        SELECT COUNT(*)::int
                        FROM json_array_elements(d.data->'files') AS f
                        WHERE json_typeof(f) = 'object'
                          AND json_typeof(f->'undo') = 'object'
                          AND json_typeof(f->'undo'->'undone') = 'boolean'
                          AND (f->'undo'->>'undone') = 'true'
                    )
                    ELSE 0
                END AS edit_undone
        ) ec
        WHERE b.calls IS NOT NULL
          AND json_typeof(b.calls) = 'array'
          AND json_typeof(elem) = 'object'
          AND lower(btrim(COALESCE(elem->>'status', ''))) NOT IN ('error', 'failed', 'pending')
          AND (
              (elem->'error') IS NULL
              OR json_typeof(elem->'error') = 'null'
              OR (json_typeof(elem->'error') = 'boolean' AND (elem->>'error') = 'false')
              OR (json_typeof(elem->'error') = 'string' AND (elem->>'error') = '')
              OR (json_typeof(elem->'error') = 'number' AND (elem->>'error')::numeric = 0)
              OR (json_typeof(elem->'error') = 'array' AND json_array_length(elem->'error') = 0)
              OR (json_typeof(elem->'error') = 'object' AND (elem->'error')::text = '{}')
          )
        GROUP BY b.id
    )
    INSERT INTO chat_message_ux_summary (
        message_id,
        session_id,
        created_at,
        round_category,
        abnormal_reason,
        feedback_vote,
        feedback_preset,
        ai_edit_cards,
        ai_edit_undone,
        ai_delete_cards,
        ai_delete_restored
    )
    SELECT
        rc.id,
        rc.session_id,
        rc.created_at,
        rc.round_category,
        rc.abnormal_reason,
        rc.feedback_vote,
        rc.feedback_preset,
        COALESCE(cc.edit_cards, 0)::int,
        COALESCE(cc.edit_undone, 0)::int,
        COALESCE(cc.delete_cards, 0)::int,
        COALESCE(cc.delete_restored, 0)::int
    FROM round_classified rc
    LEFT JOIN card_counts cc ON cc.id = rc.id
    ON CONFLICT (message_id) DO NOTHING
    """
)


def _python_backfill_batch(connection: Connection, ids: list[str] | None, batch_size: int) -> int:
    if ids is not None:
        if not ids:
            return 0
        dialect = connection.dialect.name
        if dialect == "postgresql":
            rows = connection.execute(
                text(
                    """
                    SELECT id, session_id, created_at, message_metadata, tool_calls
                    FROM chat_message
                    WHERE id = ANY(:ids)
                    """
                ),
                {"ids": ids},
            ).all()
        else:
            placeholders = ", ".join(f":id_{idx}" for idx in range(len(ids)))
            params = {f"id_{idx}": mid for idx, mid in enumerate(ids)}
            rows = connection.execute(
                text(
                    f"""
                    SELECT id, session_id, created_at, message_metadata, tool_calls
                    FROM chat_message
                    WHERE id IN ({placeholders})
                    """
                ),
                params,
            ).all()
    else:
        rows = connection.execute(
            text(
                """
                SELECT cm.id, cm.session_id, cm.created_at, cm.message_metadata, cm.tool_calls
                FROM chat_message cm
                WHERE cm.role = 'assistant'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM chat_message_ux_summary s
                      WHERE s.message_id = cm.id
                  )
                ORDER BY cm.created_at DESC, cm.id DESC
                LIMIT :batch_size
                """
            ),
            {"batch_size": batch_size},
        ).all()

    if not rows:
        return 0

    payloads: list[dict[str, Any]] = []
    for msg_id, session_id, created_at, raw_metadata, raw_tool_calls in rows:
        if created_at is None:
            continue
        if isinstance(created_at, str):
            from datetime import datetime

            created_at = datetime.fromisoformat(created_at)
        payloads.append(
            build_chat_message_ux_summary_row(
                message_id=msg_id,
                session_id=session_id,
                created_at=created_at,
                raw_metadata=raw_metadata,
                raw_tool_calls=raw_tool_calls,
            )
        )

    if not payloads:
        return 0

    dialect = connection.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(_SUMMARY_TABLE).values(payloads).on_conflict_do_nothing(index_elements=["message_id"])
    elif dialect == "sqlite":
        stmt = sqlite_insert(_SUMMARY_TABLE).values(payloads).on_conflict_do_nothing(index_elements=["message_id"])
    else:
        stmt = _SUMMARY_TABLE.insert().values(payloads)
    connection.execute(stmt)
    return len(payloads)


def count_pending_chat_message_ux_summaries(connection: Connection) -> int:
    """Count assistant ``chat_message`` rows that do not yet have a summary row."""
    result = connection.execute(
        text(
            """
            SELECT COUNT(*)
            FROM chat_message cm
            WHERE cm.role = 'assistant'
              AND NOT EXISTS (
                  SELECT 1
                  FROM chat_message_ux_summary s
                  WHERE s.message_id = cm.id
              )
            """
        )
    ).scalar_one()
    return int(result or 0)


def _is_autocommit(connection: Connection) -> bool:
    """Return True when the connection runs outside a SQL transaction block."""
    if connection.get_execution_options().get("isolation_level") == "AUTOCOMMIT":
        return True
    try:
        return connection.get_isolation_level() == "AUTOCOMMIT"
    except Exception:
        return False


def backfill_chat_message_ux_summaries(
    connection: Connection,
    *,
    batch_size: int = DEFAULT_BACKFILL_BATCH_SIZE,
    max_batches: int | None = None,
    commit_each_batch: bool = False,
) -> int:
    """Backfill missing ``chat_message_ux_summary`` rows in bounded batches.

    Returns the total number of assistant rows processed.
    """
    effective_batch = max(1, int(batch_size))
    is_pg = connection.dialect.name == "postgresql"
    autocommit = _is_autocommit(connection) if is_pg else False
    total_processed = 0
    batches = 0

    while max_batches is None or batches < max_batches:
        if is_pg:
            ids = [
                row[0]
                for row in connection.execute(
                    _PG_SELECT_PENDING_IDS_SQL,
                    {"batch_size": effective_batch},
                ).all()
            ]
            if not ids:
                break
            try:
                if commit_each_batch or autocommit or not connection.in_transaction():
                    connection.execute(_PG_INSERT_BATCH_SQL, {"ids": ids})
                else:
                    with connection.begin_nested():
                        connection.execute(_PG_INSERT_BATCH_SQL, {"ids": ids})
                batch_count = len(ids)
            except DataError:
                if commit_each_batch and not autocommit and connection.in_transaction():
                    connection.rollback()
                batch_count = _python_backfill_batch(connection, ids, effective_batch)
        else:
            batch_count = _python_backfill_batch(connection, None, effective_batch)
            if batch_count == 0:
                break

        if commit_each_batch and not autocommit and connection.in_transaction():
            connection.commit()

        total_processed += batch_count
        batches += 1
        if batch_count < effective_batch:
            break

    return total_processed
