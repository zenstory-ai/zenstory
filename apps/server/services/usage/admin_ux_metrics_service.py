"""Read-only, customer-level user experience health metrics for the admin dashboard.

Reads existing PostgreSQL/SQLite tables without introducing new event ingestion or
database migrations:
- ``llm_usage_event``: provider TTFT, >=10s slow-tail rates, prompt-cache hit rate
- ``chat_message``: healthy round completion, abnormal stops, user stop vs disconnect,
  AI edit undo / delete restore rates, explicit chat thumbs up/down feedback
- ``activation_event``, ``user``, ``file``, ``file_version``: TTFV p50, W1 returning
  writers, chapters & net words written, mobile vs desktop split

All database timestamps are naive UTC. Reporting windows are half-open and use
Beijing calendar boundaries, matching ``admin_growth_service.py``.
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import and_
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
from models.entities import ChatMessage, ChatSession, Project, User
from models.file_model import CONTENT_FILE_TYPES, File
from models.file_version import FileVersion
from models.llm_usage import (
    CALL_OUTCOME_SUCCESS,
    LLM_USAGE_SOURCE_SUGGEST,
    LLMUsageEvent,
    has_usage_tokens,
)

UX_HEALTH_DAYS = (7, 14, 30)
CHAT_VOTE_FILTERS = ("all", "up", "down")
SLOW_TAIL_THRESHOLD_MS = 10_000
TOP_DISLIKE_PRESET_LIMIT = 5

_UNDOABLE_CARD_TOOLS = frozenset({"edit_file", "write_chapter"})
_DELETE_CARD_TOOLS = frozenset({"delete_file"})
_FAILED_CARD_STATUSES = frozenset({"error", "failed", "pending"})
_TTFV_EVENTS = (
    ACTIVATION_EVENT_FIRST_AI_ACTION_ACCEPTED,
    ACTIVATION_EVENT_FIRST_FILE_SAVED,
)
_RUNAWAY_STOP_REASONS = frozenset({"repetition_loop", "runaway_stop"})


@dataclass(frozen=True)
class UxPeriod:
    start: datetime
    end: datetime

    @property
    def naive_bounds(self) -> tuple[datetime, datetime]:
        return self.start.replace(tzinfo=None), self.end.replace(tzinfo=None)


def _periods(days: int, now: datetime) -> tuple[UxPeriod, UxPeriod]:
    current_start = beijing_day_bounds(now - timedelta(days=days - 1))[0]
    current = UxPeriod(current_start, normalize_datetime_to_utc(now))
    previous = UxPeriod(
        current.start - timedelta(days=days),
        current.end - timedelta(days=days),
    )
    return current, previous


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


def _parse_json_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_json_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


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


def _classify_round(metadata: dict[str, Any]) -> tuple[str, str | None]:
    """Classify one assistant round from ``message_metadata``.

    Returns ``(category, abnormal_reason)`` where ``category`` is one of
    ``healthy``, ``user_stopped``, ``client_disconnected``, or ``abnormal``.
    """
    raw_stop_reason = metadata.get("stop_reason")
    stop_reason = raw_stop_reason.strip() if isinstance(raw_stop_reason, str) else ""

    stop_outcome = metadata.get("stop_outcome")
    stop_outcome = stop_outcome if isinstance(stop_outcome, dict) else {}
    raw_outcome_reason = stop_outcome.get("reason")
    outcome_reason = raw_outcome_reason.strip() if isinstance(raw_outcome_reason, str) else ""

    effective_reason = stop_reason or outcome_reason or "unknown"

    if effective_reason == "user_stopped" or outcome_reason == "user_stopped":
        return "user_stopped", None
    if effective_reason == "client_disconnected" or outcome_reason == "client_disconnected":
        return "client_disconnected", None

    has_unexecuted_tool_text = bool(stop_outcome.get("unexecuted_tool_text"))
    has_unwritten_files = bool(stop_outcome.get("unwritten_files"))
    has_runaway_stop = (
        bool(metadata.get("runaway_stop"))
        or bool(stop_outcome.get("runaway_stop"))
        or effective_reason in _RUNAWAY_STOP_REASONS
    )

    if (
        effective_reason == "end_turn"
        and not has_unexecuted_tool_text
        and not has_unwritten_files
        and not has_runaway_stop
    ):
        return "healthy", None

    if effective_reason == "end_turn":
        if has_unexecuted_tool_text:
            return "abnormal", "unexecuted_tool_text"
        if has_runaway_stop:
            return "abnormal", "runaway_stop"
        return "abnormal", "unwritten_files"

    return "abnormal", effective_reason


def _count_tool_cards(calls: list[Any]) -> tuple[int, int, int, int]:
    """Count (edit_cards, edit_undone, delete_cards, delete_restored) for one message."""
    edit_cards = 0
    edit_undone = 0
    delete_cards = 0
    delete_restored = 0

    for call in calls:
        if not isinstance(call, dict):
            continue
        status = str(call.get("status") or "").strip().lower()
        if status in _FAILED_CARD_STATUSES or call.get("error"):
            continue
        tool_name = call.get("name")
        result = call.get("result")
        data = (
            result.get("data")
            if isinstance(result, dict) and isinstance(result.get("data"), dict)
            else result
        )

        if tool_name in _UNDOABLE_CARD_TOOLS and isinstance(data, dict):
            undo = data.get("undo")
            if isinstance(undo, dict):
                edit_cards += 1
                if undo.get("undone") is True:
                    edit_undone += 1
        elif tool_name in _DELETE_CARD_TOOLS:
            if result is True:
                delete_cards += 1
            elif isinstance(data, dict) and "error" not in data:
                applied = (
                    data.get("mutation_applied") is True
                    or data.get("restored") is True
                    or data.get("deleted_at") is not None
                )
                if applied:
                    delete_cards += 1
                    if data.get("restored") is True:
                        delete_restored += 1

    return edit_cards, edit_undone, delete_cards, delete_restored


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


def _active_writer_ids(
    session: Session,
    start_naive: datetime,
    end_naive: datetime,
) -> set[str]:
    """Distinct non-superusers active via live author-initiated AI usage or content saves."""
    customer = User.is_superuser.is_(False)

    ai_users = session.exec(
        select(func.distinct(LLMUsageEvent.user_id))
        .join(User, User.id == LLMUsageEvent.user_id)
        .where(
            customer,
            LLMUsageEvent.source != LLM_USAGE_SOURCE_SUGGEST,
            LLMUsageEvent.is_backfilled.is_(False),
            has_usage_tokens(LLMUsageEvent),
            LLMUsageEvent.occurred_at >= start_naive,
            LLMUsageEvent.occurred_at < end_naive,
        )
    ).all()

    file_users = session.exec(
        select(func.distinct(Project.owner_id))
        .join(File, File.project_id == Project.id)
        .join(FileVersion, FileVersion.file_id == File.id)
        .join(User, User.id == Project.owner_id)
        .where(
            customer,
            Project.owner_id.is_not(None),
            Project.is_deleted.is_(False),
            File.is_deleted.is_(False),
            col(File.file_type).in_(CONTENT_FILE_TYPES),
            FileVersion.created_at >= start_naive,
            FileVersion.created_at < end_naive,
        )
    ).all()

    return {str(uid) for uid in ai_users if uid} | {str(uid) for uid in file_users if uid}


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
            "ttft_values": [],
            "feedback_up": 0,
            "feedback_total": 0,
        }
        for day in day_list
    }
    return day_list, buckets


def _period_metrics(
    session: Session,
    period: UxPeriod,
    *,
    days: int,
    include_daily: bool = False,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    start, end = period.naive_bounds
    customer = User.is_superuser.is_(False)

    day_list: list[date] = []
    daily_buckets: dict[str, dict[str, Any]] = {}
    if include_daily:
        day_list, daily_buckets = _build_daily_skeleton(period, days=days)

    # 1. Provider latency, slow-tail & prompt cache (llm_usage_event)
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
            customer,
            LLMUsageEvent.is_backfilled.is_(False),
            LLMUsageEvent.occurred_at >= start,
            LLMUsageEvent.occurred_at < end,
        )
    ).all()

    ttft_values: list[int] = []
    ttft_slow_tail_count = 0
    duration_samples = 0
    duration_slow_tail_count = 0
    cache_hit_tokens = 0
    cache_miss_tokens = 0

    for occurred_at, outcome, ttft_ms, duration_ms, hit_tokens, miss_tokens in usage_rows:
        cache_hit_tokens += int(hit_tokens or 0)
        cache_miss_tokens += int(miss_tokens or 0)
        if outcome == CALL_OUTCOME_SUCCESS:
            if ttft_ms is not None:
                ttft_int = int(ttft_ms)
                ttft_values.append(ttft_int)
                if ttft_int >= SLOW_TAIL_THRESHOLD_MS:
                    ttft_slow_tail_count += 1
                if include_daily:
                    bucket = daily_buckets.get(_beijing_day_iso(occurred_at))
                    if bucket is not None:
                        bucket["ttft_values"].append(ttft_int)
            if duration_ms is not None:
                duration_samples += 1
                if int(duration_ms) >= SLOW_TAIL_THRESHOLD_MS:
                    duration_slow_tail_count += 1

    prompt_input_tokens = cache_hit_tokens + cache_miss_tokens
    prompt_cache_hit_rate = _rate(cache_hit_tokens, prompt_input_tokens)

    # 2. Assistant rounds, abnormal stops, undo/restore, explicit feedback (chat_message)
    message_rows = session.exec(
        select(
            ChatMessage.created_at,
            ChatMessage.message_metadata,
            ChatMessage.tool_calls,
        )
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .join(User, User.id == ChatSession.user_id)
        .where(
            customer,
            ChatMessage.role == "assistant",
            ChatMessage.created_at >= start,
            ChatMessage.created_at < end,
        )
    ).all()

    total_rounds = 0
    healthy_rounds = 0
    user_stopped_rounds = 0
    client_disconnected_rounds = 0
    abnormal_stop_rounds = 0
    abnormal_reasons: Counter[str] = Counter()

    ai_edit_cards = 0
    ai_edit_undone = 0
    ai_delete_cards = 0
    ai_delete_restored = 0

    feedback_up = 0
    feedback_down = 0
    dislike_presets: Counter[str] = Counter()

    for created_at, raw_metadata, raw_tool_calls in message_rows:
        total_rounds += 1
        metadata = _parse_json_dict(raw_metadata)
        category, abnormal_reason = _classify_round(metadata)
        if category == "healthy":
            healthy_rounds += 1
        elif category == "user_stopped":
            user_stopped_rounds += 1
        elif category == "client_disconnected":
            client_disconnected_rounds += 1
        else:
            abnormal_stop_rounds += 1
            if abnormal_reason:
                abnormal_reasons[abnormal_reason] += 1

        calls = _parse_json_list(raw_tool_calls)
        m_edit, m_undone, m_del, m_restored = _count_tool_cards(calls)
        ai_edit_cards += m_edit
        ai_edit_undone += m_undone
        ai_delete_cards += m_del
        ai_delete_restored += m_restored

        fb = _extract_feedback(metadata)
        if fb is not None:
            if fb["vote"] == "up":
                feedback_up += 1
            else:
                feedback_down += 1
                if fb["preset"]:
                    dislike_presets[fb["preset"]] += 1

        if include_daily:
            bucket = daily_buckets.get(_beijing_day_iso(created_at))
            if bucket is not None:
                bucket["rounds"] += 1
                if category == "healthy":
                    bucket["healthy_rounds"] += 1
                bucket["edit_cards"] += m_edit
                bucket["edit_undone"] += m_undone
                if fb is not None:
                    bucket["feedback_total"] += 1
                    if fb["vote"] == "up":
                        bucket["feedback_up"] += 1

    feedback_total = feedback_up + feedback_down
    abnormal_stops = [
        {
            "reason": reason,
            "count": count,
            "rate": _rate(count, total_rounds),
        }
        for reason, count in sorted(abnormal_reasons.items(), key=lambda item: (-item[1], item[0]))
    ]
    top_dislike_presets = [
        {"preset": preset, "count": count}
        for preset, count in sorted(dislike_presets.items(), key=lambda item: (-item[1], item[0]))[
            :TOP_DISLIKE_PRESET_LIMIT
        ]
    ]

    # 3. TTFV p50 (signup -> earliest first_ai_action_accepted / first_file_saved)
    ttfv_rows = session.exec(
        select(
            User.id,
            User.created_at,
            func.min(ActivationEvent.created_at).label("first_value_at"),
        )
        .join(ActivationEvent, ActivationEvent.user_id == User.id)
        .where(
            customer,
            User.created_at >= start,
            User.created_at < end,
            col(ActivationEvent.event_name).in_(_TTFV_EVENTS),
            ActivationEvent.created_at >= User.created_at,
            ActivationEvent.created_at < end,
        )
        .group_by(User.id, User.created_at)
    ).all()

    ttfv_minutes: list[float] = []
    for _uid, signup_at, first_value_at in ttfv_rows:
        if signup_at is None or first_value_at is None:
            continue
        delta_minutes = max((first_value_at - signup_at).total_seconds() / 60.0, 0.0)
        ttfv_minutes.append(delta_minutes)

    # 4. W1 returning writers (trailing 7 Beijing days vs preceding 7 Beijing days)
    current_week_start = beijing_day_bounds(period.end - timedelta(days=6))[0]
    prior_week_start = current_week_start - timedelta(days=7)
    prior_writers = _active_writer_ids(
        session,
        prior_week_start.replace(tzinfo=None),
        current_week_start.replace(tzinfo=None),
    )
    if prior_writers:
        recent_writers = _active_writer_ids(
            session,
            current_week_start.replace(tzinfo=None),
            end,
        )
        returning_writers = prior_writers & recent_writers
    else:
        returning_writers = set()

    # 5. Chapters & net words written (draft / script live files)
    version_rows = session.exec(
        select(
            FileVersion.file_id,
            FileVersion.version_number,
            FileVersion.word_count,
        )
        .join(File, File.id == FileVersion.file_id)
        .join(Project, Project.id == File.project_id)
        .join(User, User.id == Project.owner_id)
        .where(
            customer,
            Project.owner_id.is_not(None),
            Project.is_deleted.is_(False),
            File.is_deleted.is_(False),
            col(File.file_type).in_(CONTENT_FILE_TYPES),
            FileVersion.created_at >= start,
            FileVersion.created_at < end,
        )
    ).all()

    latest_by_file: dict[str, tuple[int, int]] = {}
    max_words_by_file: dict[str, int] = {}
    for file_id, version_number, word_count in version_rows:
        ver_num = int(version_number or 0)
        words = int(word_count or 0)
        prev = latest_by_file.get(file_id)
        if prev is None or ver_num >= prev[0]:
            latest_by_file[file_id] = (ver_num, words)
        if words > max_words_by_file.get(file_id, 0):
            max_words_by_file[file_id] = words

    chapters_written = sum(1 for words in max_words_by_file.values() if words > 0)
    baseline_words: dict[str, int] = {}
    if latest_by_file:
        touched_ids = list(latest_by_file.keys())
        prior_max_sq = (
            select(
                FileVersion.file_id.label("file_id"),
                func.max(FileVersion.version_number).label("max_ver"),
            )
            .where(
                col(FileVersion.file_id).in_(touched_ids),
                FileVersion.created_at < start,
            )
            .group_by(FileVersion.file_id)
            .subquery()
        )
        baseline_rows = session.exec(
            select(FileVersion.file_id, FileVersion.word_count).join(
                prior_max_sq,
                and_(
                    FileVersion.file_id == prior_max_sq.c.file_id,
                    FileVersion.version_number == prior_max_sq.c.max_ver,
                ),
            )
        ).all()
        baseline_words = {file_id: int(word_count or 0) for file_id, word_count in baseline_rows}

    net_words_written = sum(
        latest_words - baseline_words.get(file_id, 0)
        for file_id, (_ver, latest_words) in latest_by_file.items()
    )

    # 6. Mobile vs Desktop split (activation_event.event_metadata["is_mobile"])
    device_rows = session.exec(
        select(
            ActivationEvent.user_id,
            ActivationEvent.event_metadata,
        )
        .join(User, User.id == ActivationEvent.user_id)
        .where(
            customer,
            ActivationEvent.created_at >= start,
            ActivationEvent.created_at < end,
        )
        .order_by(col(ActivationEvent.created_at).asc(), col(ActivationEvent.id).asc())
    ).all()

    device_by_user: dict[str, bool] = {}
    for user_id, event_metadata in device_rows:
        if isinstance(event_metadata, dict) and isinstance(event_metadata.get("is_mobile"), bool):
            device_by_user[user_id] = event_metadata["is_mobile"]

    mobile_users = sum(1 for is_mobile in device_by_user.values() if is_mobile)
    desktop_users = sum(1 for is_mobile in device_by_user.values() if not is_mobile)
    device_known_users = mobile_users + desktop_users

    metrics: dict[str, Any] = {
        "ttft_p50_ms": _percentile(ttft_values, 0.5),
        "ttft_p95_ms": _percentile(ttft_values, 0.95),
        "ttft_samples": len(ttft_values),
        "ttft_slow_tail_count": ttft_slow_tail_count,
        "ttft_slow_tail_rate": _rate(ttft_slow_tail_count, len(ttft_values)),
        "duration_samples": duration_samples,
        "duration_slow_tail_count": duration_slow_tail_count,
        "duration_slow_tail_rate": _rate(duration_slow_tail_count, duration_samples),
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
        "ai_edit_cards": ai_edit_cards,
        "ai_edit_undone": ai_edit_undone,
        "ai_edit_undo_rate": _rate(ai_edit_undone, ai_edit_cards),
        "ai_delete_cards": ai_delete_cards,
        "ai_delete_restored": ai_delete_restored,
        "ai_delete_restore_rate": _rate(ai_delete_restored, ai_delete_cards),
        "feedback_up": feedback_up,
        "feedback_down": feedback_down,
        "feedback_total": feedback_total,
        "thumbs_up_rate": _rate(feedback_up, feedback_total),
        "top_dislike_presets": top_dislike_presets,
        "ttfv_p50_minutes": _median_float(ttfv_minutes),
        "ttfv_samples": len(ttfv_minutes),
        "w1_returning_writers": len(returning_writers),
        "w1_eligible_writers": len(prior_writers),
        "w1_retention_rate": _rate(len(returning_writers), len(prior_writers)),
        "chapters_written": chapters_written,
        "net_words_written": net_words_written,
        "mobile_users": mobile_users,
        "desktop_users": desktop_users,
        "mobile_share": _rate(mobile_users, device_known_users),
    }

    daily: list[dict[str, Any]] = []
    if include_daily:
        for day in day_list:
            bucket = daily_buckets[day.isoformat()]
            daily.append(
                {
                    "date": day,
                    "rounds": bucket["rounds"],
                    "healthy_rate": _rate(bucket["healthy_rounds"], bucket["rounds"]),
                    "undo_rate": _rate(bucket["edit_undone"], bucket["edit_cards"]),
                    "ttft_p95_ms": _percentile(bucket["ttft_values"], 0.95),
                    "thumbs_up_rate": _rate(bucket["feedback_up"], bucket["feedback_total"]),
                }
            )

    return metrics, daily


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
    current_metrics, daily = _period_metrics(session, current, days=days, include_daily=True)
    previous_metrics, _ = _period_metrics(session, previous, days=days, include_daily=False)
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

    rows = session.exec(
        select(
            ChatMessage.id,
            ChatMessage.created_at,
            ChatMessage.message_metadata,
            User.id,
            User.username,
        )
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .join(User, User.id == ChatSession.user_id)
        .where(
            User.is_superuser.is_(False),
            ChatMessage.role == "assistant",
            ChatMessage.created_at >= start,
            ChatMessage.created_at < end,
            ChatMessage.message_metadata.is_not(None),
            col(ChatMessage.message_metadata).contains('"feedback"'),
        )
    ).all()

    up_count = 0
    down_count = 0
    preset_counter: Counter[str] = Counter()
    entries: list[dict[str, Any]] = []

    for message_id, message_created_at, raw_metadata, user_id, username in rows:
        metadata = _parse_json_dict(raw_metadata)
        fb = _extract_feedback(metadata)
        if fb is None:
            continue

        if fb["vote"] == "up":
            up_count += 1
        else:
            down_count += 1
            if fb["preset"]:
                preset_counter[fb["preset"]] += 1

        if vote != "all" and fb["vote"] != vote:
            continue

        feedback_time = (
            fb["updated_at"]
            or fb["created_at"]
            or normalize_datetime_to_utc(message_created_at)
        )
        entries.append(
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
