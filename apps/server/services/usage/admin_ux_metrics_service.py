"""Read-only, customer-level user experience health metrics for the admin dashboard.

Reads existing PostgreSQL/SQLite tables without introducing new event ingestion:
- ``llm_usage_event``: provider TTFT, >=10s slow-tail rates, prompt-cache hit rate
- ``chat_message``: healthy round completion, abnormal stops, user stop vs disconnect,
  AI edit undo / delete restore rates, explicit chat thumbs up/down feedback
- ``activation_event``, ``user``, ``file``, ``file_version``: TTFV p50, W1 returning
  writers, chapters & net words written, mobile vs desktop split

Performance design:
- Current and prior periods are combined into single range queries where possible.
- Assistant round classification, feedback vote/preset, and edit/delete card counts
  are read from the narrow, non-TOAST ``chat_message_ux_summary`` projection table
  via SQL ``GROUP BY`` (never detoasting multi-GB ``chat_message.message_metadata``
  or ``chat_message.tool_calls`` blobs).
- Any un-backfilled legacy assistant rows (detected by a lightweight anti-join
  against ``chat_message_ux_summary`` that touches zero TOAST columns) fall back
  safely to bounded ``yield_per`` streaming with PostgreSQL JSON projection and
  ``DataError`` handling.
- ``file_version`` net words/chapters and W1 writer retention aggregate in SQL CTEs
  returning 1 row per period instead of shipping raw version/user rows to Python.
- On PostgreSQL, latency percentiles and device splits push down to SQL.

All database timestamps are naive UTC. Reporting windows are half-open and use
Beijing calendar boundaries, matching ``admin_growth_service.py``.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, exists, literal, literal_column, or_, text, union
from sqlalchemy.exc import DataError
from sqlmodel import Session, col, func, select

from config.datetime_utils import (
    BEIJING_TIMEZONE,
    beijing_day_bounds,
    normalize_datetime_to_utc,
    utcnow,
)
from models.activation_event import (
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
    ActivationEvent,
)
from models.chat_message_ux_summary import (
    ChatMessageUxSummary,
    classify_round,
    count_tool_cards,
    has_chat_message_ux_summary_table,
    parse_json_dict,
    parse_json_list,
)
from models.entities import ChatMessage, ChatSession, Project, User
from models.file_model import CONTENT_FILE_TYPES, File
from models.file_version import FileVersion
from models.llm_usage import (
    CALL_OUTCOME_SUCCESS,
    LLM_USAGE_SOURCE_SUGGEST,
    LLMUsageEvent,
    has_usage_tokens,
)
from services.infra.admin_analytics_cache import admin_read_statement_timeout
from services.usage.pricing import beijing_day_bucket_case

UX_HEALTH_DAYS = (7, 14, 30)
CHAT_VOTE_FILTERS = ("all", "up", "down")
SLOW_TAIL_THRESHOLD_MS = 10_000
TOP_DISLIKE_PRESET_LIMIT = 5

_TOOL_CARD_MARKERS = ('"edit_file"', '"write_chapter"', '"batch_replace_files"', '"delete_file"')
_STREAM_CHAT_BATCH_SIZE = 50
_STREAM_PG_PROJECTED_BATCH_SIZE = 100
_STREAM_SCALAR_BATCH_SIZE = 500
_MISSING_ID_CHUNK_SIZE = 500

# PostgreSQL 15+ JSON extraction expressions (compatible with PG 15/16/17; uses
# ``json`` rather than ``jsonb`` so legacy ``\u0000`` escapes do not fail and no
# binary key-sorting tree is allocated). Any malformed JSON raises DataError and
# transparently falls back to the bounded ``yield_per`` Python streaming path.
_PG_PROJECTED_METADATA_SQL = r"""
CASE
    WHEN chat_message.message_metadata IS NOT NULL
     AND left(ltrim(chat_message.message_metadata, E' \t\r\n'), 1) = '{'
    THEN json_build_object(
        'stop_reason', (chat_message.message_metadata::json)->'stop_reason',
        'stop_outcome', (chat_message.message_metadata::json)->'stop_outcome',
        'runaway_stop', (chat_message.message_metadata::json)->'runaway_stop',
        'status_cards', (chat_message.message_metadata::json)->'status_cards',
        'feedback', (chat_message.message_metadata::json)->'feedback'
    )::text
    ELSE NULL
END
"""

_PG_PROJECTED_FEEDBACK_SQL = r"""
CASE
    WHEN chat_message.message_metadata IS NOT NULL
     AND left(ltrim(chat_message.message_metadata, E' \t\r\n'), 1) = '{'
    THEN json_build_object(
        'feedback', (chat_message.message_metadata::json)->'feedback'
    )::text
    ELSE NULL
END
"""

_TTFV_EVENTS = (
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
)


@dataclass(frozen=True)
class UxPeriod:
    start: datetime
    end: datetime

    @property
    def naive_bounds(self) -> tuple[datetime, datetime]:
        return self.start.replace(tzinfo=None), self.end.replace(tzinfo=None)


def _is_postgres(session: Session) -> bool:
    bind = session.get_bind() if hasattr(session, "get_bind") else None
    return getattr(getattr(bind, "dialect", None), "name", "") == "postgresql"


def _is_postgres_session(session: Session) -> bool:
    return _is_postgres(session)


def _periods(days: int, now: datetime) -> tuple[UxPeriod, UxPeriod]:
    current_start = beijing_day_bounds(now - timedelta(days=days - 1))[0]
    current = UxPeriod(current_start, normalize_datetime_to_utc(now))
    previous = UxPeriod(
        current.start - timedelta(days=days),
        current.end - timedelta(days=days),
    )
    return current, previous


def _two_period_condition(
    column: Any,
    curr_start: datetime,
    curr_end: datetime,
    prev_start: datetime,
    prev_end: datetime,
) -> Any:
    return or_(
        and_(column >= prev_start, column < prev_end),
        and_(column >= curr_start, column < curr_end),
    )


def _period_key_for_ts(
    ts: datetime,
    curr_start: datetime,
    curr_end: datetime,
    prev_start: datetime,
    prev_end: datetime,
) -> str | None:
    naive = ts.replace(tzinfo=None) if ts.tzinfo is not None else ts
    if curr_start <= naive < curr_end:
        return "current"
    if prev_start <= naive < prev_end:
        return "previous"
    return None


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator > 0 else None


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1]


def _median_float(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    median = ordered[mid] if len(ordered) % 2 == 1 else (ordered[mid - 1] + ordered[mid]) / 2.0
    return round(median, 1)


def _delta_float(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None:
        return None
    return round(current - previous, 4)


def _delta_int(current: int | None, previous: int | None) -> int | None:
    if current is None or previous is None:
        return None
    return current - previous


_parse_json_dict = parse_json_dict
_parse_json_list = parse_json_list


def _parse_iso_utc(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return normalize_datetime_to_utc(parsed)


def _beijing_day_iso(ts: datetime) -> str:
    return normalize_datetime_to_utc(ts).astimezone(BEIJING_TIMEZONE).date().isoformat()


_classify_round = classify_round
_count_tool_cards = count_tool_cards


def _extract_feedback(metadata: dict[str, Any]) -> dict[str, Any] | None:
    feedback = metadata.get("feedback")
    if not isinstance(feedback, dict):
        return None
    vote = feedback.get("vote")
    if vote not in ("up", "down"):
        return None
    raw_preset = feedback.get("preset")
    preset = raw_preset.strip() if isinstance(raw_preset, str) and raw_preset.strip() else None
    raw_comment = feedback.get("comment")
    comment = raw_comment.strip() if isinstance(raw_comment, str) and raw_comment.strip() else None
    return {
        "vote": vote,
        "preset": preset,
        "comment": comment,
        "created_at": _parse_iso_utc(feedback.get("created_at")),
        "updated_at": _parse_iso_utc(feedback.get("updated_at")),
    }


def _build_daily_skeleton(period: UxPeriod, *, days: int) -> tuple[list[date], dict[str, dict[str, Any]]]:
    first_day = normalize_datetime_to_utc(period.start).astimezone(BEIJING_TIMEZONE).date()
    day_list = [
        first_day + timedelta(days=offset)
        for offset in range(days)
        if period.start + timedelta(days=offset) < period.end
    ]
    buckets: dict[str, dict[str, Any]] = {
        day.isoformat(): {
            "date": day,
            "rounds": 0,
            "healthy_rounds": 0,
            "edit_cards": 0,
            "edit_undone": 0,
            "ttft_p95_ms": None,
            "ttft_values": [],
            "feedback_up": 0,
            "feedback_total": 0,
        }
        for day in day_list
    }
    return day_list, buckets


# ---------------------------------------------------------------------------
# Sub-aggregations (SQLite + PostgreSQL optimized)
# ---------------------------------------------------------------------------


def _empty_llm_stats() -> dict[str, Any]:
    return {
        "ttft_p50_ms": None,
        "ttft_p95_ms": None,
        "ttft_samples": 0,
        "ttft_slow_tail_count": 0,
        "duration_samples": 0,
        "duration_slow_tail_count": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
    }


def _query_llm_metrics(
    session: Session,
    current: UxPeriod,
    previous: UxPeriod,
    day_list: list[date],
    daily_buckets: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    curr_start, curr_end = current.naive_bounds
    prev_start, prev_end = previous.naive_bounds
    results = {"current": _empty_llm_stats(), "previous": _empty_llm_stats()}

    if _is_postgres(session):
        period_rows = session.execute(
            text(
                """
                SELECT
                    CASE
                        WHEN l.occurred_at >= :curr_start AND l.occurred_at < :curr_end THEN 'current'
                        ELSE 'previous'
                    END AS period_key,
                    COALESCE(SUM(l.cache_hit_tokens), 0) AS cache_hit_tokens,
                    COALESCE(SUM(l.cache_miss_tokens), 0) AS cache_miss_tokens,
                    COUNT(l.ttft_ms) FILTER (WHERE l.outcome = :success AND l.ttft_ms IS NOT NULL) AS ttft_samples,
                    COUNT(*) FILTER (WHERE l.outcome = :success AND l.ttft_ms >= :slow_ms) AS ttft_slow_tail_count,
                    percentile_disc(0.5) WITHIN GROUP (ORDER BY l.ttft_ms)
                        FILTER (WHERE l.outcome = :success AND l.ttft_ms IS NOT NULL) AS ttft_p50_ms,
                    percentile_disc(0.95) WITHIN GROUP (ORDER BY l.ttft_ms)
                        FILTER (WHERE l.outcome = :success AND l.ttft_ms IS NOT NULL) AS ttft_p95_ms,
                    COUNT(l.duration_ms) FILTER (WHERE l.outcome = :success AND l.duration_ms IS NOT NULL) AS duration_samples,
                    COUNT(*) FILTER (WHERE l.outcome = :success AND l.duration_ms >= :slow_ms) AS duration_slow_tail_count
                FROM llm_usage_event l
                JOIN "user" u ON u.id = l.user_id
                WHERE u.is_superuser IS FALSE
                  AND l.is_backfilled IS FALSE
                  AND (
                      (l.occurred_at >= :prev_start AND l.occurred_at < :prev_end)
                      OR (l.occurred_at >= :curr_start AND l.occurred_at < :curr_end)
                  )
                GROUP BY 1
                """
            ),
            {
                "curr_start": curr_start,
                "curr_end": curr_end,
                "prev_start": prev_start,
                "prev_end": prev_end,
                "success": CALL_OUTCOME_SUCCESS,
                "slow_ms": SLOW_TAIL_THRESHOLD_MS,
            },
        ).all()
        for row in period_rows:
            results[row.period_key] = {
                "ttft_p50_ms": int(row.ttft_p50_ms) if row.ttft_p50_ms is not None else None,
                "ttft_p95_ms": int(row.ttft_p95_ms) if row.ttft_p95_ms is not None else None,
                "ttft_samples": int(row.ttft_samples or 0),
                "ttft_slow_tail_count": int(row.ttft_slow_tail_count or 0),
                "duration_samples": int(row.duration_samples or 0),
                "duration_slow_tail_count": int(row.duration_slow_tail_count or 0),
                "cache_hit_tokens": int(row.cache_hit_tokens or 0),
                "cache_miss_tokens": int(row.cache_miss_tokens or 0),
            }

        if day_list:
            day_expr = beijing_day_bucket_case(LLMUsageEvent.occurred_at, day_list, end_utc=current.end)
            daily_ttft_rows = session.exec(
                select(
                    day_expr.label("day"),
                    func.percentile_disc(0.95)
                    .within_group(LLMUsageEvent.ttft_ms)
                    .label("ttft_p95_ms"),
                )
                .join(User, User.id == LLMUsageEvent.user_id)
                .where(
                    User.is_superuser.is_(False),
                    LLMUsageEvent.is_backfilled.is_(False),
                    LLMUsageEvent.outcome == CALL_OUTCOME_SUCCESS,
                    LLMUsageEvent.ttft_ms.is_not(None),
                    LLMUsageEvent.occurred_at >= curr_start,
                    LLMUsageEvent.occurred_at < curr_end,
                )
                .group_by(day_expr)
            ).all()
            for day_key, p95 in daily_ttft_rows:
                bucket = daily_buckets.get(day_key)
                if bucket is not None and p95 is not None:
                    bucket["ttft_p95_ms"] = int(p95)
        return results

    usage_rows = session.exec(
        select(
            LLMUsageEvent.occurred_at,
            LLMUsageEvent.outcome,
            LLMUsageEvent.ttft_ms,
            LLMUsageEvent.duration_ms,
            LLMUsageEvent.cache_hit_tokens,
            LLMUsageEvent.cache_miss_tokens,
        )
        .join(User, User.id == LLMUsageEvent.user_id)
        .where(
            User.is_superuser.is_(False),
            LLMUsageEvent.is_backfilled.is_(False),
            _two_period_condition(LLMUsageEvent.occurred_at, curr_start, curr_end, prev_start, prev_end),
        ),
        execution_options={"yield_per": _STREAM_SCALAR_BATCH_SIZE},
    )

    ttft_lists: dict[str, list[int]] = {"current": [], "previous": []}
    try:
        for occurred_at, outcome, ttft_ms, duration_ms, hit_tokens, miss_tokens in usage_rows:
            period_key = _period_key_for_ts(occurred_at, curr_start, curr_end, prev_start, prev_end)
            if period_key is None:
                continue
            target = results[period_key]
            target["cache_hit_tokens"] += int(hit_tokens or 0)
            target["cache_miss_tokens"] += int(miss_tokens or 0)
            if outcome == CALL_OUTCOME_SUCCESS:
                if ttft_ms is not None:
                    ttft_int = int(ttft_ms)
                    ttft_lists[period_key].append(ttft_int)
                    if ttft_int >= SLOW_TAIL_THRESHOLD_MS:
                        target["ttft_slow_tail_count"] += 1
                    if period_key == "current":
                        bucket = daily_buckets.get(_beijing_day_iso(occurred_at))
                        if bucket is not None:
                            bucket["ttft_values"].append(ttft_int)
                if duration_ms is not None:
                    target["duration_samples"] += 1
                    if int(duration_ms) >= SLOW_TAIL_THRESHOLD_MS:
                        target["duration_slow_tail_count"] += 1
    finally:
        usage_rows.close()

    for key in ("current", "previous"):
        values = ttft_lists[key]
        results[key]["ttft_samples"] = len(values)
        results[key]["ttft_p50_ms"] = _percentile(values, 0.5)
        results[key]["ttft_p95_ms"] = _percentile(values, 0.95)
    for bucket in daily_buckets.values():
        if bucket["ttft_values"]:
            bucket["ttft_p95_ms"] = _percentile(bucket["ttft_values"], 0.95)
    return results


def _empty_round_stats() -> dict[str, Any]:
    return {
        "total_rounds": 0,
        "healthy_rounds": 0,
        "user_stopped_rounds": 0,
        "client_disconnected_rounds": 0,
        "abnormal_stop_rounds": 0,
        "abnormal_reasons": Counter(),
        "ai_edit_cards": 0,
        "ai_edit_undone": 0,
        "ai_delete_cards": 0,
        "ai_delete_restored": 0,
        "feedback_up": 0,
        "feedback_down": 0,
        "dislike_presets": Counter(),
    }


def _query_tool_card_metrics_postgres_for_ids(
    session: Session,
    current: UxPeriod,
    message_ids: list[str],
    round_stats: dict[str, dict[str, Any]],
    daily_buckets: dict[str, dict[str, Any]],
) -> bool:
    """Aggregate AI edit undo & delete restore cards in PostgreSQL for un-backfilled IDs."""
    curr_start, curr_end = current.naive_bounds
    try:
        with session.begin_nested():
            rows = session.execute(
                text(
                    r"""
                    WITH candidate_msgs AS (
                        SELECT
                            CASE
                                WHEN cm.created_at >= :curr_start AND cm.created_at < :curr_end THEN 'current'
                                ELSE 'previous'
                            END AS period_key,
                            to_char((cm.created_at AT TIME ZONE 'UTC') AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM-DD') AS bj_day,
                            cm.tool_calls::json AS calls
                        FROM chat_message cm
                        WHERE cm.id = ANY(:ids)
                          AND cm.tool_calls IS NOT NULL
                          AND left(ltrim(cm.tool_calls, E' \t\r\n'), 1) = '['
                          AND (
                              strpos(cm.tool_calls, '"edit_file"') > 0
                              OR strpos(cm.tool_calls, '"write_chapter"') > 0
                              OR strpos(cm.tool_calls, '"batch_replace_files"') > 0
                              OR strpos(cm.tool_calls, '"delete_file"') > 0
                          )
                    ),
                    expanded AS (
                        SELECT
                            c.period_key,
                            c.bj_day,
                            elem,
                            CASE
                                WHEN json_typeof(elem->'result') = 'object'
                                     AND json_typeof(elem->'result'->'data') = 'object'
                                THEN elem->'result'->'data'
                                ELSE elem->'result'
                            END AS data
                        FROM candidate_msgs c
                        CROSS JOIN LATERAL json_array_elements(c.calls) AS elem
                        WHERE json_typeof(c.calls) = 'array'
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
                    )
                    SELECT
                        period_key,
                        CASE WHEN period_key = 'current' THEN bj_day ELSE NULL END AS bj_day,
                        COALESCE(SUM(ec.edit_cards), 0) AS edit_cards,
                        COALESCE(SUM(ec.edit_undone), 0) AS edit_undone,
                        COUNT(*) FILTER (
                            WHERE json_typeof(elem->'name') = 'string'
                              AND (elem->>'name') = 'delete_file'
                              AND (
                                  (json_typeof(elem->'result') = 'boolean' AND (elem->>'result') = 'true')
                                  OR (
                                      json_typeof(data) = 'object'
                                      AND (data->'error') IS NULL
                                      AND (
                                          (json_typeof(data->'mutation_applied') = 'boolean' AND (data->>'mutation_applied') = 'true')
                                          OR (json_typeof(data->'restored') = 'boolean' AND (data->>'restored') = 'true')
                                          OR ((data->'deleted_at') IS NOT NULL AND json_typeof(data->'deleted_at') <> 'null')
                                      )
                                  )
                              )
                        ) AS delete_cards,
                        COUNT(*) FILTER (
                            WHERE json_typeof(elem->'name') = 'string'
                              AND (elem->>'name') = 'delete_file'
                              AND json_typeof(data) = 'object'
                              AND (data->'error') IS NULL
                              AND json_typeof(data->'restored') = 'boolean'
                              AND (data->>'restored') = 'true'
                        ) AS delete_restored
                    FROM expanded
                    CROSS JOIN LATERAL (
                        SELECT
                            CASE
                                WHEN json_typeof(elem->'name') = 'string'
                                     AND (elem->>'name') IN ('edit_file', 'write_chapter')
                                     AND json_typeof(data) = 'object'
                                     AND json_typeof(data->'undo') = 'object'
                                THEN 1
                                WHEN json_typeof(elem->'name') = 'string'
                                     AND (elem->>'name') = 'batch_replace_files'
                                     AND json_typeof(data) = 'object'
                                     AND NOT (json_typeof(data->'dry_run') = 'boolean' AND (data->>'dry_run') = 'true')
                                     AND NOT (json_typeof(data->'modified_files') = 'number' AND (data->>'modified_files')::numeric = 0)
                                     AND json_typeof(data->'files') = 'array'
                                THEN (
                                    SELECT COUNT(*)::int
                                    FROM json_array_elements(data->'files') AS f
                                    WHERE json_typeof(f) = 'object'
                                      AND json_typeof(f->'undo') = 'object'
                                )
                                ELSE 0
                            END AS edit_cards,
                            CASE
                                WHEN json_typeof(elem->'name') = 'string'
                                     AND (elem->>'name') IN ('edit_file', 'write_chapter')
                                     AND json_typeof(data) = 'object'
                                     AND json_typeof(data->'undo') = 'object'
                                     AND json_typeof(data->'undo'->'undone') = 'boolean'
                                     AND (data->'undo'->>'undone') = 'true'
                                THEN 1
                                WHEN json_typeof(elem->'name') = 'string'
                                     AND (elem->>'name') = 'batch_replace_files'
                                     AND json_typeof(data) = 'object'
                                     AND NOT (json_typeof(data->'dry_run') = 'boolean' AND (data->>'dry_run') = 'true')
                                     AND NOT (json_typeof(data->'modified_files') = 'number' AND (data->>'modified_files')::numeric = 0)
                                     AND json_typeof(data->'files') = 'array'
                                THEN (
                                    SELECT COUNT(*)::int
                                    FROM json_array_elements(data->'files') AS f
                                    WHERE json_typeof(f) = 'object'
                                      AND json_typeof(f->'undo') = 'object'
                                      AND json_typeof(f->'undo'->'undone') = 'boolean'
                                      AND (f->'undo'->>'undone') = 'true'
                                )
                                ELSE 0
                            END AS edit_undone
                    ) ec
                    GROUP BY 1, 2
                    """
                ),
                {
                    "curr_start": curr_start,
                    "curr_end": curr_end,
                    "ids": message_ids,
                },
            ).all()
    except DataError:
        return False

    for period_key, bj_day, edit_cards, edit_undone, delete_cards, delete_restored in rows:
        target = round_stats[period_key]
        m_edit = int(edit_cards or 0)
        m_undone = int(edit_undone or 0)
        m_del = int(delete_cards or 0)
        m_restored = int(delete_restored or 0)
        target["ai_edit_cards"] += m_edit
        target["ai_edit_undone"] += m_undone
        target["ai_delete_cards"] += m_del
        target["ai_delete_restored"] += m_restored
        if period_key == "current" and bj_day:
            bucket = daily_buckets.get(bj_day)
            if bucket is not None:
                bucket["edit_cards"] += m_edit
                bucket["edit_undone"] += m_undone
    return True


def _merge_missing_round_and_tool_metrics(
    session: Session,
    current: UxPeriod,
    previous: UxPeriod,
    missing_ids: list[str],
    stats: dict[str, dict[str, Any]],
    daily_buckets: dict[str, dict[str, Any]],
) -> None:
    """Fallback scan strictly for assistant messages not yet present in ``chat_message_ux_summary``."""
    curr_start, curr_end = current.naive_bounds
    prev_start, prev_end = previous.naive_bounds

    def _run_metadata_scan(
        stmt: Any, *, yield_per: int
    ) -> tuple[dict[str, dict[str, Any]], dict[str, list[int]]]:
        scanned = {"current": _empty_round_stats(), "previous": _empty_round_stats()}
        day_deltas: dict[str, list[int]] = {}
        rows = session.exec(stmt, execution_options={"yield_per": yield_per})
        try:
            for created_at, raw_metadata in rows:
                period_key = _period_key_for_ts(
                    created_at, curr_start, curr_end, prev_start, prev_end
                )
                if period_key is None:
                    continue
                target = scanned[period_key]
                target["total_rounds"] += 1
                metadata = _parse_json_dict(raw_metadata)
                category, abnormal_reason = _classify_round(metadata)
                if category == "healthy":
                    target["healthy_rounds"] += 1
                elif category == "user_stopped":
                    target["user_stopped_rounds"] += 1
                elif category == "client_disconnected":
                    target["client_disconnected_rounds"] += 1
                else:
                    target["abnormal_stop_rounds"] += 1
                    if abnormal_reason:
                        target["abnormal_reasons"][abnormal_reason] += 1

                fb = _extract_feedback(metadata)
                if fb is not None:
                    if fb["vote"] == "up":
                        target["feedback_up"] += 1
                    else:
                        target["feedback_down"] += 1
                        if fb["preset"]:
                            target["dislike_presets"][fb["preset"]] += 1

                if period_key == "current":
                    day_key = _beijing_day_iso(created_at)
                    if day_key in daily_buckets:
                        delta = day_deltas.get(day_key)
                        if delta is None:
                            delta = [0, 0, 0, 0]
                            day_deltas[day_key] = delta
                        delta[0] += 1
                        if category == "healthy":
                            delta[1] += 1
                        if fb is not None:
                            delta[2] += 1
                            if fb["vote"] == "up":
                                delta[3] += 1
        finally:
            rows.close()
        return scanned, day_deltas

    for offset in range(0, len(missing_ids), _MISSING_ID_CHUNK_SIZE):
        id_chunk = missing_ids[offset : offset + _MISSING_ID_CHUNK_SIZE]

        # Metadata scan (never touches tool_calls).
        meta_result: tuple[dict[str, dict[str, Any]], dict[str, list[int]]] | None = None
        if _is_postgres_session(session):
            pg_meta_stmt = select(
                ChatMessage.created_at,
                literal_column(_PG_PROJECTED_METADATA_SQL).label("projected_metadata"),
            ).where(col(ChatMessage.id).in_(id_chunk))
            try:
                with session.begin_nested():
                    meta_result = _run_metadata_scan(
                        pg_meta_stmt, yield_per=_STREAM_PG_PROJECTED_BATCH_SIZE
                    )
            except DataError:
                meta_result = None

        if meta_result is None:
            portable_meta_stmt = select(
                ChatMessage.created_at,
                ChatMessage.message_metadata,
            ).where(col(ChatMessage.id).in_(id_chunk))
            meta_result = _run_metadata_scan(portable_meta_stmt, yield_per=_STREAM_CHAT_BATCH_SIZE)

        chunk_stats, day_deltas = meta_result
        for key in ("current", "previous"):
            dst = stats[key]
            src = chunk_stats[key]
            dst["total_rounds"] += src["total_rounds"]
            dst["healthy_rounds"] += src["healthy_rounds"]
            dst["user_stopped_rounds"] += src["user_stopped_rounds"]
            dst["client_disconnected_rounds"] += src["client_disconnected_rounds"]
            dst["abnormal_stop_rounds"] += src["abnormal_stop_rounds"]
            dst["abnormal_reasons"].update(src["abnormal_reasons"])
            dst["feedback_up"] += src["feedback_up"]
            dst["feedback_down"] += src["feedback_down"]
            dst["dislike_presets"].update(src["dislike_presets"])

        for day_key, delta in day_deltas.items():
            bucket = daily_buckets[day_key]
            bucket["rounds"] += delta[0]
            bucket["healthy_rounds"] += delta[1]
            bucket["feedback_total"] += delta[2]
            bucket["feedback_up"] += delta[3]

        # Tool cards scan (strictly decoupled from message_metadata).
        if _is_postgres(session) and _query_tool_card_metrics_postgres_for_ids(
            session, current, id_chunk, stats, daily_buckets
        ):
            continue

        tool_rows = session.exec(
            select(
                ChatMessage.created_at,
                ChatMessage.tool_calls,
            ).where(
                col(ChatMessage.id).in_(id_chunk),
                ChatMessage.tool_calls.is_not(None),
                or_(*(col(ChatMessage.tool_calls).contains(marker) for marker in _TOOL_CARD_MARKERS)),
            ),
            execution_options={"yield_per": _STREAM_CHAT_BATCH_SIZE},
        )
        try:
            for created_at, raw_tool_calls in tool_rows:
                period_key = _period_key_for_ts(
                    created_at, curr_start, curr_end, prev_start, prev_end
                )
                if period_key is None:
                    continue
                calls = _parse_json_list(raw_tool_calls)
                m_edit, m_undone, m_del, m_restored = _count_tool_cards(calls)
                target = stats[period_key]
                target["ai_edit_cards"] += m_edit
                target["ai_edit_undone"] += m_undone
                target["ai_delete_cards"] += m_del
                target["ai_delete_restored"] += m_restored
                if period_key == "current" and (m_edit or m_undone):
                    bucket = daily_buckets.get(_beijing_day_iso(created_at))
                    if bucket is not None:
                        bucket["edit_cards"] += m_edit
                        bucket["edit_undone"] += m_undone
        finally:
            tool_rows.close()


def _query_round_and_tool_metrics(
    session: Session,
    current: UxPeriod,
    previous: UxPeriod,
    day_list: list[date],
    daily_buckets: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    curr_start, curr_end = current.naive_bounds
    prev_start, prev_end = previous.naive_bounds
    stats = {"current": _empty_round_stats(), "previous": _empty_round_stats()}

    if not has_chat_message_ux_summary_table(session.connection()):
        missing_ids = list(
            session.exec(
                select(ChatMessage.id)
                .join(ChatSession, ChatSession.id == ChatMessage.session_id)
                .join(User, User.id == ChatSession.user_id)
                .where(
                    User.is_superuser.is_(False),
                    ChatMessage.role == "assistant",
                    _two_period_condition(
                        ChatMessage.created_at, curr_start, curr_end, prev_start, prev_end
                    ),
                )
            ).all()
        )
        if missing_ids:
            _merge_missing_round_and_tool_metrics(
                session, current, previous, missing_ids, stats, daily_buckets
            )
        return stats

    summary_window = _two_period_condition(
        ChatMessageUxSummary.created_at, curr_start, curr_end, prev_start, prev_end
    )
    period_expr = case(
        (
            and_(
                ChatMessageUxSummary.created_at >= curr_start,
                ChatMessageUxSummary.created_at < curr_end,
            ),
            literal("current"),
        ),
        else_=literal("previous"),
    )
    day_expr = (
        case(
            (
                and_(
                    ChatMessageUxSummary.created_at >= curr_start,
                    ChatMessageUxSummary.created_at < curr_end,
                ),
                beijing_day_bucket_case(
                    ChatMessageUxSummary.created_at, day_list, end_utc=current.end
                ),
            ),
            else_=literal(None),
        )
        if day_list
        else literal(None)
    )

    # Query 2a: Zero-TOAST SQL aggregation over chat_message_ux_summary.
    summary_rows = session.exec(
        select(
            period_expr.label("period_key"),
            day_expr.label("bj_day"),
            func.count(ChatMessageUxSummary.message_id).label("total_rounds"),
            func.coalesce(
                func.sum(case((ChatMessageUxSummary.round_category == "healthy", 1), else_=0)), 0
            ).label("healthy_rounds"),
            func.coalesce(
                func.sum(case((ChatMessageUxSummary.round_category == "user_stopped", 1), else_=0)), 0
            ).label("user_stopped_rounds"),
            func.coalesce(
                func.sum(
                    case((ChatMessageUxSummary.round_category == "client_disconnected", 1), else_=0)
                ),
                0,
            ).label("client_disconnected_rounds"),
            func.coalesce(
                func.sum(case((ChatMessageUxSummary.round_category == "abnormal", 1), else_=0)), 0
            ).label("abnormal_stop_rounds"),
            func.coalesce(func.sum(ChatMessageUxSummary.ai_edit_cards), 0).label("ai_edit_cards"),
            func.coalesce(func.sum(ChatMessageUxSummary.ai_edit_undone), 0).label("ai_edit_undone"),
            func.coalesce(func.sum(ChatMessageUxSummary.ai_delete_cards), 0).label("ai_delete_cards"),
            func.coalesce(func.sum(ChatMessageUxSummary.ai_delete_restored), 0).label(
                "ai_delete_restored"
            ),
            func.coalesce(
                func.sum(case((ChatMessageUxSummary.feedback_vote == "up", 1), else_=0)), 0
            ).label("feedback_up"),
            func.coalesce(
                func.sum(case((ChatMessageUxSummary.feedback_vote == "down", 1), else_=0)), 0
            ).label("feedback_down"),
        )
        .join(ChatSession, ChatSession.id == ChatMessageUxSummary.session_id)
        .join(User, User.id == ChatSession.user_id)
        .where(
            User.is_superuser.is_(False),
            summary_window,
        )
        .group_by(period_expr, day_expr)
    ).all()

    has_abnormal_or_dislike = False
    for row in summary_rows:
        target = stats[row.period_key]
        total_r = int(row.total_rounds or 0)
        healthy_r = int(row.healthy_rounds or 0)
        user_stopped_r = int(row.user_stopped_rounds or 0)
        disconnected_r = int(row.client_disconnected_rounds or 0)
        abnormal_r = int(row.abnormal_stop_rounds or 0)
        edit_c = int(row.ai_edit_cards or 0)
        edit_u = int(row.ai_edit_undone or 0)
        del_c = int(row.ai_delete_cards or 0)
        del_r = int(row.ai_delete_restored or 0)
        fb_up = int(row.feedback_up or 0)
        fb_down = int(row.feedback_down or 0)

        target["total_rounds"] += total_r
        target["healthy_rounds"] += healthy_r
        target["user_stopped_rounds"] += user_stopped_r
        target["client_disconnected_rounds"] += disconnected_r
        target["abnormal_stop_rounds"] += abnormal_r
        target["ai_edit_cards"] += edit_c
        target["ai_edit_undone"] += edit_u
        target["ai_delete_cards"] += del_c
        target["ai_delete_restored"] += del_r
        target["feedback_up"] += fb_up
        target["feedback_down"] += fb_down
        if abnormal_r > 0 or fb_down > 0:
            has_abnormal_or_dislike = True

        if row.period_key == "current" and row.bj_day:
            bucket = daily_buckets.get(row.bj_day)
            if bucket is not None:
                bucket["rounds"] += total_r
                bucket["healthy_rounds"] += healthy_r
                bucket["edit_cards"] += edit_c
                bucket["edit_undone"] += edit_u
                bucket["feedback_up"] += fb_up
                bucket["feedback_total"] += fb_up + fb_down

    # Query 2b: Abnormal stop reasons & dislike presets (only when present).
    if has_abnormal_or_dislike:
        breakdown_rows = session.exec(
            select(
                period_expr.label("period_key"),
                ChatMessageUxSummary.round_category,
                ChatMessageUxSummary.abnormal_reason,
                ChatMessageUxSummary.feedback_vote,
                ChatMessageUxSummary.feedback_preset,
                func.count(ChatMessageUxSummary.message_id).label("cnt"),
            )
            .join(ChatSession, ChatSession.id == ChatMessageUxSummary.session_id)
            .join(User, User.id == ChatSession.user_id)
            .where(
                User.is_superuser.is_(False),
                summary_window,
                or_(
                    and_(
                        ChatMessageUxSummary.round_category == "abnormal",
                        ChatMessageUxSummary.abnormal_reason.is_not(None),
                    ),
                    and_(
                        ChatMessageUxSummary.feedback_vote == "down",
                        ChatMessageUxSummary.feedback_preset.is_not(None),
                    ),
                ),
            )
            .group_by(
                period_expr,
                ChatMessageUxSummary.round_category,
                ChatMessageUxSummary.abnormal_reason,
                ChatMessageUxSummary.feedback_vote,
                ChatMessageUxSummary.feedback_preset,
            )
        ).all()
        for period_key, round_category, abnormal_reason, feedback_vote, feedback_preset, cnt in breakdown_rows:
            count_int = int(cnt or 0)
            target = stats[period_key]
            if round_category == "abnormal" and abnormal_reason:
                target["abnormal_reasons"][abnormal_reason] += count_int
            if feedback_vote == "down" and feedback_preset:
                target["dislike_presets"][feedback_preset] += count_int

    # Query 2c: Lightweight anti-join check for any un-backfilled legacy assistant rows
    # (touches zero TOAST columns; returns 0 rows once backfilled).
    missing_ids = list(
        session.exec(
            select(ChatMessage.id)
            .join(ChatSession, ChatSession.id == ChatMessage.session_id)
            .join(User, User.id == ChatSession.user_id)
            .where(
                User.is_superuser.is_(False),
                ChatMessage.role == "assistant",
                _two_period_condition(
                    ChatMessage.created_at, curr_start, curr_end, prev_start, prev_end
                ),
                ~exists(
                    select(literal(1)).where(ChatMessageUxSummary.message_id == ChatMessage.id)
                ),
            )
        ).all()
    )
    if missing_ids:
        _merge_missing_round_and_tool_metrics(
            session, current, previous, missing_ids, stats, daily_buckets
        )

    return stats


def _query_ttfv_minutes(
    session: Session,
    current: UxPeriod,
    previous: UxPeriod,
) -> dict[str, list[float]]:
    curr_start, curr_end = current.naive_bounds
    prev_start, prev_end = previous.naive_bounds

    period_expr = case(
        (and_(User.created_at >= curr_start, User.created_at < curr_end), literal("current")),
        else_=literal("previous"),
    )
    event_end_bound = case(
        (and_(User.created_at >= curr_start, User.created_at < curr_end), literal(curr_end)),
        else_=literal(prev_end),
    )
    rows = session.exec(
        select(
            period_expr.label("period_key"),
            User.created_at,
            func.min(ActivationEvent.created_at).label("first_value_at"),
        )
        .join(ActivationEvent, ActivationEvent.user_id == User.id)
        .where(
            User.is_superuser.is_(False),
            _two_period_condition(User.created_at, curr_start, curr_end, prev_start, prev_end),
            col(ActivationEvent.event_name).in_(_TTFV_EVENTS),
            ActivationEvent.created_at >= User.created_at,
            ActivationEvent.created_at < event_end_bound,
        )
        .group_by(period_expr, User.id, User.created_at)
    ).all()

    out: dict[str, list[float]] = {"current": [], "previous": []}
    for period_key, signup_at, first_value_at in rows:
        if signup_at is None or first_value_at is None:
            continue
        delta_minutes = max((first_value_at - signup_at).total_seconds() / 60.0, 0.0)
        out[period_key].append(delta_minutes)
    return out


def _active_writers_subquery(start_naive: datetime, end_naive: datetime, name: str) -> Any:
    ai_q = (
        select(LLMUsageEvent.user_id.label("uid"))
        .join(User, User.id == LLMUsageEvent.user_id)
        .where(
            User.is_superuser.is_(False),
            LLMUsageEvent.source != LLM_USAGE_SOURCE_SUGGEST,
            LLMUsageEvent.is_backfilled.is_(False),
            has_usage_tokens(LLMUsageEvent),
            LLMUsageEvent.occurred_at >= start_naive,
            LLMUsageEvent.occurred_at < end_naive,
        )
    )
    file_q = (
        select(Project.owner_id.label("uid"))
        .join(File, File.project_id == Project.id)
        .join(FileVersion, FileVersion.file_id == File.id)
        .join(User, User.id == Project.owner_id)
        .where(
            User.is_superuser.is_(False),
            Project.owner_id.is_not(None),
            Project.is_deleted.is_(False),
            File.is_deleted.is_(False),
            col(File.file_type).in_(CONTENT_FILE_TYPES),
            FileVersion.created_at >= start_naive,
            FileVersion.created_at < end_naive,
        )
    )
    return union(ai_q, file_q).cte(name)


def _query_w1_retention(session: Session, period: UxPeriod) -> tuple[int, int]:
    """Compute (eligible_writers, returning_writers) in one SQL round trip."""
    _, end = period.naive_bounds
    current_week_start = beijing_day_bounds(period.end - timedelta(days=6))[0]
    prior_week_start = current_week_start - timedelta(days=7)

    prior_cte = _active_writers_subquery(
        prior_week_start.replace(tzinfo=None),
        current_week_start.replace(tzinfo=None),
        "prior_writers",
    )
    recent_cte = _active_writers_subquery(
        current_week_start.replace(tzinfo=None),
        end,
        "recent_writers",
    )
    row = session.exec(
        select(
            func.count(prior_cte.c.uid).label("eligible_count"),
            func.count(recent_cte.c.uid).label("returning_count"),
        )
        .select_from(prior_cte)
        .outerjoin(recent_cte, recent_cte.c.uid == prior_cte.c.uid)
    ).one()
    return int(row.eligible_count or 0), int(row.returning_count or 0)


def _query_output_metrics(session: Session, period: UxPeriod) -> tuple[int, int]:
    """Compute (chapters_written, net_words_written) in one SQL CTE query."""
    start, end = period.naive_bounds
    in_window = (
        select(
            FileVersion.file_id.label("file_id"),
            func.max(FileVersion.version_number).label("latest_ver"),
            func.max(func.coalesce(FileVersion.word_count, 0)).label("max_words"),
        )
        .join(File, File.id == FileVersion.file_id)
        .join(Project, Project.id == File.project_id)
        .join(User, User.id == Project.owner_id)
        .where(
            User.is_superuser.is_(False),
            Project.owner_id.is_not(None),
            Project.is_deleted.is_(False),
            File.is_deleted.is_(False),
            col(File.file_type).in_(CONTENT_FILE_TYPES),
            FileVersion.created_at >= start,
            FileVersion.created_at < end,
        )
        .group_by(FileVersion.file_id)
        .cte("in_window_files")
    )
    prior_ver = (
        select(
            FileVersion.file_id.label("file_id"),
            func.max(FileVersion.version_number).label("baseline_ver"),
        )
        .join(in_window, in_window.c.file_id == FileVersion.file_id)
        .where(FileVersion.created_at < start)
        .group_by(FileVersion.file_id)
        .cte("prior_file_versions")
    )

    latest_fv = FileVersion.__table__.alias("latest_fv")
    base_fv = FileVersion.__table__.alias("base_fv")

    row = session.exec(
        select(
            func.coalesce(func.sum(case((in_window.c.max_words > 0, 1), else_=0)), 0).label("chapters"),
            func.coalesce(
                func.sum(
                    func.coalesce(latest_fv.c.word_count, 0) - func.coalesce(base_fv.c.word_count, 0)
                ),
                0,
            ).label("net_words"),
        )
        .select_from(in_window)
        .join(
            latest_fv,
            and_(
                latest_fv.c.file_id == in_window.c.file_id,
                latest_fv.c.version_number == in_window.c.latest_ver,
            ),
        )
        .outerjoin(prior_ver, prior_ver.c.file_id == in_window.c.file_id)
        .outerjoin(
            base_fv,
            and_(
                base_fv.c.file_id == prior_ver.c.file_id,
                base_fv.c.version_number == prior_ver.c.baseline_ver,
            ),
        )
    ).one()
    return int(row.chapters or 0), int(row.net_words or 0)


def _query_device_split(
    session: Session,
    current: UxPeriod,
    previous: UxPeriod,
) -> dict[str, tuple[int, int]]:
    """Return {period_key: (mobile_users, desktop_users)} in one query."""
    curr_start, curr_end = current.naive_bounds
    prev_start, prev_end = previous.naive_bounds
    result = {"current": (0, 0), "previous": (0, 0)}

    if _is_postgres(session):
        rows = session.execute(
            text(
                """
                WITH latest_device AS (
                    SELECT DISTINCT ON (period_key, ae.user_id)
                        CASE
                            WHEN ae.created_at >= :curr_start AND ae.created_at < :curr_end THEN 'current'
                            ELSE 'previous'
                        END AS period_key,
                        ae.user_id,
                        (ae.event_metadata::jsonb ->> 'is_mobile')::boolean AS is_mobile
                    FROM activation_event ae
                    JOIN "user" u ON u.id = ae.user_id
                    WHERE u.is_superuser IS FALSE
                      AND (
                          (ae.created_at >= :prev_start AND ae.created_at < :prev_end)
                          OR (ae.created_at >= :curr_start AND ae.created_at < :curr_end)
                      )
                      AND ae.event_metadata IS NOT NULL
                      AND jsonb_typeof(ae.event_metadata::jsonb -> 'is_mobile') = 'boolean'
                    ORDER BY period_key, ae.user_id, ae.created_at DESC, ae.id DESC
                )
                SELECT
                    period_key,
                    COUNT(*) FILTER (WHERE is_mobile IS TRUE) AS mobile_users,
                    COUNT(*) FILTER (WHERE is_mobile IS FALSE) AS desktop_users
                FROM latest_device
                GROUP BY period_key
                """
            ),
            {
                "curr_start": curr_start,
                "curr_end": curr_end,
                "prev_start": prev_start,
                "prev_end": prev_end,
            },
        ).all()
        for row in rows:
            result[row.period_key] = (int(row.mobile_users or 0), int(row.desktop_users or 0))
        return result

    device_rows = session.exec(
        select(
            ActivationEvent.created_at,
            ActivationEvent.user_id,
            ActivationEvent.event_metadata,
        )
        .join(User, User.id == ActivationEvent.user_id)
        .where(
            User.is_superuser.is_(False),
            _two_period_condition(ActivationEvent.created_at, curr_start, curr_end, prev_start, prev_end),
        )
        .order_by(col(ActivationEvent.created_at).asc(), col(ActivationEvent.id).asc()),
        execution_options={"yield_per": _STREAM_SCALAR_BATCH_SIZE},
    )

    by_period: dict[str, dict[str, bool]] = {"current": {}, "previous": {}}
    try:
        for created_at, user_id, event_metadata in device_rows:
            period_key = _period_key_for_ts(created_at, curr_start, curr_end, prev_start, prev_end)
            if period_key is None:
                continue
            if isinstance(event_metadata, dict) and isinstance(event_metadata.get("is_mobile"), bool):
                by_period[period_key][user_id] = event_metadata["is_mobile"]
    finally:
        device_rows.close()

    for key in ("current", "previous"):
        mapping = by_period[key]
        mobile = sum(1 for is_mobile in mapping.values() if is_mobile)
        desktop = len(mapping) - mobile
        result[key] = (mobile, desktop)
    return result


def _assemble_period_metrics(
    llm: dict[str, Any],
    rounds: dict[str, Any],
    ttfv_minutes: list[float],
    w1_eligible: int,
    w1_returning: int,
    chapters_written: int,
    net_words_written: int,
    mobile_users: int,
    desktop_users: int,
) -> dict[str, Any]:
    cache_hit_tokens = llm["cache_hit_tokens"]
    cache_miss_tokens = llm["cache_miss_tokens"]
    prompt_cache_hit_rate = _rate(cache_hit_tokens, cache_hit_tokens + cache_miss_tokens)

    total_rounds = rounds["total_rounds"]
    healthy_rounds = rounds["healthy_rounds"]
    user_stopped_rounds = rounds["user_stopped_rounds"]
    client_disconnected_rounds = rounds["client_disconnected_rounds"]
    abnormal_stop_rounds = rounds["abnormal_stop_rounds"]
    abnormal_stops = [
        {
            "reason": reason,
            "count": count,
            "rate": _rate(count, total_rounds),
        }
        for reason, count in sorted(rounds["abnormal_reasons"].items(), key=lambda item: (-item[1], item[0]))
    ]
    top_dislike_presets = [
        {"preset": preset, "count": count}
        for preset, count in sorted(rounds["dislike_presets"].items(), key=lambda item: (-item[1], item[0]))[
            :TOP_DISLIKE_PRESET_LIMIT
        ]
    ]
    feedback_up = rounds["feedback_up"]
    feedback_down = rounds["feedback_down"]
    feedback_total = feedback_up + feedback_down
    device_known_users = mobile_users + desktop_users

    return {
        "ttft_p50_ms": llm["ttft_p50_ms"],
        "ttft_p95_ms": llm["ttft_p95_ms"],
        "ttft_samples": llm["ttft_samples"],
        "ttft_slow_tail_count": llm["ttft_slow_tail_count"],
        "ttft_slow_tail_rate": _rate(llm["ttft_slow_tail_count"], llm["ttft_samples"]),
        "duration_samples": llm["duration_samples"],
        "duration_slow_tail_count": llm["duration_slow_tail_count"],
        "duration_slow_tail_rate": _rate(llm["duration_slow_tail_count"], llm["duration_samples"]),
        "cache_hit_tokens": cache_hit_tokens,
        "cache_miss_tokens": cache_miss_tokens,
        "prompt_cache_hit_rate": prompt_cache_hit_rate,
        "total_rounds": total_rounds,
        "healthy_rounds": healthy_rounds,
        "healthy_round_rate": _rate(healthy_rounds, total_rounds),
        "user_stopped_rounds": user_stopped_rounds,
        "user_stopped_rate": _rate(user_stopped_rounds, total_rounds),
        "client_disconnected_rounds": client_disconnected_rounds,
        "client_disconnected_rate": _rate(client_disconnected_rounds, total_rounds),
        "abnormal_stop_rounds": abnormal_stop_rounds,
        "abnormal_stop_rate": _rate(abnormal_stop_rounds, total_rounds),
        "abnormal_stops": abnormal_stops,
        "ai_edit_cards": rounds["ai_edit_cards"],
        "ai_edit_undone": rounds["ai_edit_undone"],
        "ai_edit_undo_rate": _rate(rounds["ai_edit_undone"], rounds["ai_edit_cards"]),
        "ai_delete_cards": rounds["ai_delete_cards"],
        "ai_delete_restored": rounds["ai_delete_restored"],
        "ai_delete_restore_rate": _rate(rounds["ai_delete_restored"], rounds["ai_delete_cards"]),
        "feedback_up": feedback_up,
        "feedback_down": feedback_down,
        "feedback_total": feedback_total,
        "thumbs_up_rate": _rate(feedback_up, feedback_total),
        "top_dislike_presets": top_dislike_presets,
        "ttfv_p50_minutes": _median_float(ttfv_minutes),
        "ttfv_samples": len(ttfv_minutes),
        "w1_returning_writers": w1_returning,
        "w1_eligible_writers": w1_eligible,
        "w1_retention_rate": _rate(w1_returning, w1_eligible),
        "chapters_written": chapters_written,
        "net_words_written": net_words_written,
        "mobile_users": mobile_users,
        "desktop_users": desktop_users,
        "mobile_share": _rate(mobile_users, device_known_users),
    }


def _compute_delta(current: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    return {
        "ttft_p50_ms": _delta_int(current["ttft_p50_ms"], previous["ttft_p50_ms"]),
        "ttft_p95_ms": _delta_int(current["ttft_p95_ms"], previous["ttft_p95_ms"]),
        "ttft_slow_tail_rate": _delta_float(
            current["ttft_slow_tail_rate"], previous["ttft_slow_tail_rate"]
        ),
        "duration_slow_tail_rate": _delta_float(
            current["duration_slow_tail_rate"], previous["duration_slow_tail_rate"]
        ),
        "prompt_cache_hit_rate": _delta_float(
            current["prompt_cache_hit_rate"], previous["prompt_cache_hit_rate"]
        ),
        "healthy_round_rate": _delta_float(
            current["healthy_round_rate"], previous["healthy_round_rate"]
        ),
        "user_stopped_rate": _delta_float(
            current["user_stopped_rate"], previous["user_stopped_rate"]
        ),
        "client_disconnected_rate": _delta_float(
            current["client_disconnected_rate"], previous["client_disconnected_rate"]
        ),
        "abnormal_stop_rate": _delta_float(
            current["abnormal_stop_rate"], previous["abnormal_stop_rate"]
        ),
        "ai_edit_undo_rate": _delta_float(
            current["ai_edit_undo_rate"], previous["ai_edit_undo_rate"]
        ),
        "ai_delete_restore_rate": _delta_float(
            current["ai_delete_restore_rate"], previous["ai_delete_restore_rate"]
        ),
        "thumbs_up_rate": _delta_float(current["thumbs_up_rate"], previous["thumbs_up_rate"]),
        "ttfv_p50_minutes": _delta_float(
            current["ttfv_p50_minutes"], previous["ttfv_p50_minutes"]
        ),
        "w1_retention_rate": _delta_float(
            current["w1_retention_rate"], previous["w1_retention_rate"]
        ),
        "chapters_written": _delta_int(
            current["chapters_written"], previous["chapters_written"]
        ),
        "net_words_written": _delta_int(
            current["net_words_written"], previous["net_words_written"]
        ),
        "mobile_share": _delta_float(current["mobile_share"], previous["mobile_share"]),
    }


UX_HEALTH_DEFINITIONS = {
    "window": "Half-open Beijing calendar window ending at the request cutoff; prior period shifts back by the same number of days.",
    "customer_scope": "All metrics exclude superuser accounts and backfilled LLM usage rows.",
    "provider_ttft": "Provider-side time to first token (ttft_ms) and >=10s slow-tail rates across successful non-backfilled LLM attempts.",
    "prompt_cache_hit_rate": "Weighted cache_hit_tokens / (cache_hit_tokens + cache_miss_tokens) over non-backfilled customer calls.",
    "healthy_round_rate": "Assistant rounds with stop_reason=end_turn and no unexecuted_tool_text, unwritten_files, or runaway_stop flags.",
    "user_stop_vs_disconnect": "Mid-stream user_stopped separated from client_disconnected.",
    "ai_edit_undo_rate": "Undone edit_file/write_chapter cards (result.undo.undone=true) divided by undoable edit cards.",
    "ai_delete_restore_rate": "Restored delete_file cards (result.restored=true) divided by applied delete cards.",
    "thumbs_up_rate": "Explicit up / (up + down) votes on assistant messages created in the window.",
    "ttfv_p50_minutes": "Median minutes from signup to earliest first_ai_action_accepted or first_file_saved for signups in the window.",
    "w1_retention_rate": "Distinct writers active in the prior 7 Beijing days who were active again in the trailing 7 Beijing days.",
    "output": "Active draft/script chapters saved and net word delta versus the latest pre-window version.",
    "device_split": "Mobile vs desktop share among distinct customers with is_mobile metadata on activation events in the window.",
}


def get_ux_health_dashboard(
    session: Session,
    *,
    days: int = 7,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the Phase-1 UX health dashboard payload for a supported window."""
    if days not in UX_HEALTH_DAYS:
        raise ValueError(f"unsupported ux health window: {days}")
    report_now = normalize_datetime_to_utc(now or utcnow())
    current, previous = _periods(days, report_now)
    day_list, daily_buckets = _build_daily_skeleton(current, days=days)

    with admin_read_statement_timeout(session):
        llm_by_period = _query_llm_metrics(session, current, previous, day_list, daily_buckets)
        rounds_by_period = _query_round_and_tool_metrics(
            session, current, previous, day_list, daily_buckets
        )
        ttfv_by_period = _query_ttfv_minutes(session, current, previous)
        curr_w1_eligible, curr_w1_returning = _query_w1_retention(session, current)
        prev_w1_eligible, prev_w1_returning = _query_w1_retention(session, previous)
        curr_chapters, curr_net_words = _query_output_metrics(session, current)
        prev_chapters, prev_net_words = _query_output_metrics(session, previous)
        device_by_period = _query_device_split(session, current, previous)

    current_metrics = _assemble_period_metrics(
        llm_by_period["current"],
        rounds_by_period["current"],
        ttfv_by_period["current"],
        curr_w1_eligible,
        curr_w1_returning,
        curr_chapters,
        curr_net_words,
        *device_by_period["current"],
    )
    previous_metrics = _assemble_period_metrics(
        llm_by_period["previous"],
        rounds_by_period["previous"],
        ttfv_by_period["previous"],
        prev_w1_eligible,
        prev_w1_returning,
        prev_chapters,
        prev_net_words,
        *device_by_period["previous"],
    )

    daily = [
        {
            "date": day,
            "rounds": daily_buckets[day.isoformat()]["rounds"],
            "healthy_rate": _rate(
                daily_buckets[day.isoformat()]["healthy_rounds"],
                daily_buckets[day.isoformat()]["rounds"],
            ),
            "undo_rate": _rate(
                daily_buckets[day.isoformat()]["edit_undone"],
                daily_buckets[day.isoformat()]["edit_cards"],
            ),
            "ttft_p95_ms": daily_buckets[day.isoformat()]["ttft_p95_ms"],
            "thumbs_up_rate": _rate(
                daily_buckets[day.isoformat()]["feedback_up"],
                daily_buckets[day.isoformat()]["feedback_total"],
            ),
        }
        for day in day_list
    ]

    return {
        "days": days,
        "timezone": "Asia/Shanghai",
        "current": {
            "period_start": current.start,
            "period_end": current.end,
            "metrics": current_metrics,
        },
        "previous": {
            "period_start": previous.start,
            "period_end": previous.end,
            "metrics": previous_metrics,
        },
        "delta": _compute_delta(current_metrics, previous_metrics),
        "daily": daily,
        "definitions": UX_HEALTH_DEFINITIONS,
    }


def list_chat_feedback_votes(
    session: Session,
    *,
    days: int = 7,
    vote: str = "all",
    page: int = 1,
    page_size: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """List explicit assistant chat thumbs-up/down votes without leaking manuscript or prompt text."""
    if days not in UX_HEALTH_DAYS:
        raise ValueError(f"unsupported chat votes window: {days}")
    if vote not in CHAT_VOTE_FILTERS:
        raise ValueError(f"unsupported chat vote filter: {vote}")

    report_now = normalize_datetime_to_utc(now or utcnow())
    current, _ = _periods(days, report_now)
    start, end = current.naive_bounds

    feedback_match = (
        func.strpos(ChatMessage.message_metadata, '"feedback"') > 0
        if _is_postgres(session)
        else col(ChatMessage.message_metadata).contains('"feedback"')
    )

    base_filters = (
        User.is_superuser.is_(False),
        ChatMessage.role == "assistant",
        ChatMessage.created_at >= start,
        ChatMessage.created_at < end,
        ChatMessage.message_metadata.is_not(None),
        feedback_match,
    )

    def _scan_votes(
        stmt: Any, *, yield_per: int
    ) -> tuple[int, int, Counter[str], list[dict[str, Any]]]:
        up_acc = 0
        down_acc = 0
        presets_acc: Counter[str] = Counter()
        entries_acc: list[dict[str, Any]] = []

        rows = session.exec(stmt, execution_options={"yield_per": yield_per})
        try:
            for message_id, message_created_at, raw_metadata, user_id, username in rows:
                metadata = _parse_json_dict(raw_metadata)
                fb = _extract_feedback(metadata)
                if fb is None:
                    continue

                if fb["vote"] == "up":
                    up_acc += 1
                else:
                    down_acc += 1
                    if fb["preset"]:
                        presets_acc[fb["preset"]] += 1

                if vote != "all" and fb["vote"] != vote:
                    continue

                feedback_time = (
                    fb["updated_at"]
                    or fb["created_at"]
                    or normalize_datetime_to_utc(message_created_at)
                )
                entries_acc.append(
                    {
                        "message_id": message_id,
                        "user_id": user_id,
                        "username": username,
                        "vote": fb["vote"],
                        "preset": fb["preset"],
                        "comment": fb["comment"],
                        "created_at": feedback_time,
                    }
                )
        finally:
            rows.close()
        return up_acc, down_acc, presets_acc, entries_acc

    scanned: tuple[int, int, Counter[str], list[dict[str, Any]]] | None = None
    with admin_read_statement_timeout(session):
        if _is_postgres_session(session):
            pg_stmt = (
                select(
                    ChatMessage.id,
                    ChatMessage.created_at,
                    literal_column(_PG_PROJECTED_FEEDBACK_SQL).label("projected_feedback"),
                    User.id,
                    User.username,
                )
                .join(ChatSession, ChatSession.id == ChatMessage.session_id)
                .join(User, User.id == ChatSession.user_id)
                .where(*base_filters)
            )
            try:
                with session.begin_nested():
                    scanned = _scan_votes(pg_stmt, yield_per=_STREAM_PG_PROJECTED_BATCH_SIZE)
            except DataError:
                scanned = None

        if scanned is None:
            portable_stmt = (
                select(
                    ChatMessage.id,
                    ChatMessage.created_at,
                    ChatMessage.message_metadata,
                    User.id,
                    User.username,
                )
                .join(ChatSession, ChatSession.id == ChatMessage.session_id)
                .join(User, User.id == ChatSession.user_id)
                .where(*base_filters)
            )
            scanned = _scan_votes(portable_stmt, yield_per=_STREAM_CHAT_BATCH_SIZE)

    up_count, down_count, preset_counter, entries = scanned

    entries.sort(
        key=lambda item: (item["created_at"], item["message_id"]),
        reverse=True,
    )

    total_votes = up_count + down_count
    filtered_total = len(entries)
    offset = (page - 1) * page_size
    page_items = entries[offset : offset + page_size]

    preset_counts = [
        {"preset": preset, "count": count}
        for preset, count in sorted(preset_counter.items(), key=lambda item: (-item[1], item[0]))
    ]

    return {
        "days": days,
        "timezone": "Asia/Shanghai",
        "period_start": current.start,
        "period_end": current.end,
        "summary": {
            "up": up_count,
            "down": down_count,
            "total_votes": total_votes,
            "up_rate": _rate(up_count, total_votes),
            "preset_counts": preset_counts,
        },
        "items": page_items,
        "total": filtered_total,
        "page": page,
        "page_size": page_size,
    }
