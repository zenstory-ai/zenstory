"""Per-round agent latency & reliability persistence and admin stage-latency aggregation.

Writes one narrow numeric/enum/boolean row per ``/agent/stream`` round into
``agent_round_metric`` (never manuscript text, prompt text, or PII). Every
persistence helper is best-effort and runs in its own short-lived session so a
metric failure never delays or breaks SSE delivery.

Admin queries are customer-scoped (``User.is_superuser == False``) and aggregate
over Beijing calendar days, matching ``admin_usage_service`` and
``admin_growth_service``.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from models.agent_round_metric import AgentRoundMetric
from models.entities import Project, User
from models.file_model import FILE_TYPE_DRAFT, FILE_TYPE_SCRIPT, File
from models.file_version import (
    CHANGE_SOURCE_AI,
    CHANGE_SOURCE_USER,
    CHANGE_TYPE_RESTORE,
    FileVersion,
)
from services.usage.admin_usage_service import Period, trailing_period
from services.usage.pricing import BEIJING_TZ_NAME, beijing_today
from utils.logger import get_logger

logger = get_logger(__name__)

STAGE_LATENCY_DAYS = (7, 14, 30)
SLOW_TAIL_THRESHOLD_MS = 10_000
DRAFT_SURVIVAL_OBSERVATION_HOURS = 24

SessionFactory = Callable[[], Session]


def _default_session_factory() -> Session:
    from database import create_session

    return create_session()


# Tests point this at their own database (see tests/conftest.py).
_session_factory: SessionFactory = _default_session_factory


def _bounded_str(value: Any, max_len: int) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return stripped[:max_len]


def _non_negative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value):
        return None
    return max(0, int(round(value)))


def _extract_prepare_context_ms(run_report: Mapping[str, Any]) -> int | None:
    stage_timings = run_report.get("stage_timings")
    if not isinstance(stage_timings, Mapping):
        return None
    prepare = stage_timings.get("prepare_context")
    if not isinstance(prepare, Mapping):
        return None
    return _non_negative_int(prepare.get("total_ms"))


def _extract_tool_failure_counts(run_report: Mapping[str, Any]) -> tuple[int, int]:
    """Return (tool_failures_count, file_body_pending_count) from run_report['tool_failures']."""
    tool_failures = run_report.get("tool_failures")
    if not isinstance(tool_failures, Mapping):
        return 0, 0
    total_failures = 0
    pending_count = 0
    for stats in tool_failures.values():
        if not isinstance(stats, Mapping):
            continue
        count = _non_negative_int(stats.get("count"))
        if count:
            total_failures += count
        error_types = stats.get("error_types")
        if isinstance(error_types, Mapping):
            pending = _non_negative_int(error_types.get("file_body_pending"))
            if pending:
                pending_count += pending
    return total_failures, pending_count


def build_agent_round_metric(
    *,
    user_id: str,
    agent_run_id: str,
    project_id: str | None = None,
    session_id: str | None = None,
    is_mobile: bool | None = None,
    ui_action: str | None = None,
    billing_reason: str | None = None,
    stop_reason: str | None = None,
    first_output_ms: int | None = None,
    total_duration_ms: int | None = None,
    user_stopped: bool = False,
    client_disconnected: bool = False,
    deadline_exceeded: bool = False,
    write_succeeded: bool = False,
    runaway_stop: bool = False,
    unexecuted_tool_text: bool = False,
    run_report: Mapping[str, Any] | None = None,
    occurred_at: datetime | None = None,
) -> AgentRoundMetric:
    """Build a strictly structured AgentRoundMetric row from settlement state and run_report."""
    report = run_report if isinstance(run_report, Mapping) else {}
    tool_failures_count, file_body_pending_count = _extract_tool_failure_counts(report)
    leaked_from_report = _non_negative_int(report.get("leaked_tool_call_count")) or 0
    leaked_tool_call_count = max(leaked_from_report, 1 if unexecuted_tool_text else 0)
    resolved_duration = _non_negative_int(report.get("duration_ms"))
    if resolved_duration is None:
        resolved_duration = _non_negative_int(total_duration_ms)
    resolved_stop_reason = _bounded_str(report.get("stop_reason"), 64) or _bounded_str(stop_reason, 64)
    resolved_session_id = _bounded_str(report.get("session_id"), 64) or _bounded_str(session_id, 64)
    resolved_occurred_at = occurred_at if occurred_at is not None else utcnow()
    if resolved_occurred_at.tzinfo is not None:
        resolved_occurred_at = resolved_occurred_at.replace(tzinfo=None)

    return AgentRoundMetric(
        occurred_at=resolved_occurred_at,
        user_id=user_id,
        project_id=_bounded_str(project_id, 64),
        session_id=resolved_session_id,
        agent_run_id=_bounded_str(agent_run_id, 64) or agent_run_id[:64],
        is_mobile=is_mobile if isinstance(is_mobile, bool) else None,
        ui_action=_bounded_str(ui_action, 32),
        primary_model=_bounded_str(report.get("model"), 64),
        prepare_context_ms=_extract_prepare_context_ms(report),
        first_output_ms=_non_negative_int(first_output_ms),
        first_tool_started_ms=_non_negative_int(report.get("first_tool_started_ms")),
        chapter_first_prose_ms=_non_negative_int(report.get("chapter_first_prose_ms")),
        first_body_persisted_ms=_non_negative_int(report.get("first_body_persisted_ms")),
        total_duration_ms=resolved_duration,
        stop_reason=resolved_stop_reason,
        billing_reason=_bounded_str(billing_reason, 64),
        user_stopped=bool(user_stopped),
        client_disconnected=bool(client_disconnected),
        deadline_exceeded=bool(deadline_exceeded),
        write_succeeded=bool(write_succeeded),
        runaway_stop=bool(runaway_stop),
        file_body_pending_count=file_body_pending_count,
        leaked_tool_call_count=leaked_tool_call_count,
        tool_calls_count=_non_negative_int(report.get("tool_calls_count")) or 0,
        tool_failures_count=tool_failures_count,
    )


def record_agent_round_metric(
    metric: AgentRoundMetric,
    *,
    session_factory: SessionFactory | None = None,
) -> bool:
    """Persist one AgentRoundMetric row in a fresh short-lived DB session.

    Never raises: failures are logged and return False so SSE delivery and
    billing settlement are completely isolated from metric storage errors.
    """
    factory = session_factory or _session_factory
    try:
        with factory() as metric_session:
            metric_session.add(metric)
            metric_session.commit()
        return True
    except IntegrityError:
        # Duplicate agent_run_id: idempotent settlement guard.
        logger.debug("Agent round metric already persisted for agent_run_id=%s", metric.agent_run_id)
        return False
    except Exception:
        logger.warning(
            "Failed to persist agent round metric for agent_run_id=%s",
            metric.agent_run_id,
            exc_info=True,
        )
        return False


# ---------------------------------------------------------------------------
# Admin aggregation
# ---------------------------------------------------------------------------


def _percentile(sorted_values: list[int], fraction: float) -> int | None:
    if not sorted_values:
        return None
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator > 0 else None


def _basic_distribution(values: list[int]) -> dict[str, int | None]:
    ordered = sorted(values)
    return {
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "samples": len(ordered),
    }


def _slow_tail_distribution(values: list[int], *, include_p90: bool = False) -> dict[str, Any]:
    ordered = sorted(values)
    slow_count = sum(1 for item in ordered if item >= SLOW_TAIL_THRESHOLD_MS)
    payload: dict[str, Any] = {
        "p50": _percentile(ordered, 0.50),
        "p95": _percentile(ordered, 0.95),
        "samples": len(ordered),
        "slow_count": slow_count,
        "slow_rate": _rate(slow_count, len(ordered)),
    }
    if include_p90:
        payload["p90"] = _percentile(ordered, 0.90)
    return payload


def _device_slice(rows: list[AgentRoundMetric]) -> dict[str, Any]:
    total = len(rows)
    first_output = [r.first_output_ms for r in rows if r.first_output_ms is not None]
    chapter_prose = [r.chapter_first_prose_ms for r in rows if r.chapter_first_prose_ms is not None]
    disconnected = sum(1 for r in rows if r.client_disconnected)
    return {
        "rounds": total,
        "first_output_ms": _slow_tail_distribution(first_output),
        "chapter_first_prose_ms": _slow_tail_distribution(chapter_prose, include_p90=True),
        "client_disconnected_rounds": disconnected,
        "client_disconnected_rate": _rate(disconnected, total),
    }


def _compute_ai_draft_survival(
    session: Session,
    period: Period,
    now_naive_utc: datetime,
) -> dict[str, Any]:
    """Compute 24h AI draft survival / co-edit rate from file + file_version.

    Includes customer-owned ``draft`` / ``script`` files created within the
    reporting window whose age is at least 24 hours (so every counted draft has
    completed its 24h observation window) and whose initial version was written
    by AI (``change_source == 'ai'``).
    """
    utc_start, utc_end = period.utc_bounds
    cutoff_24h = now_naive_utc - timedelta(hours=DRAFT_SURVIVAL_OBSERVATION_HOURS)
    eligible_end = min(utc_end, cutoff_24h)
    if eligible_end <= utc_start:
        return {
            "eligible_drafts": 0,
            "survived_drafts": 0,
            "co_edited_drafts": 0,
            "deleted_or_reverted_drafts": 0,
            "survival_rate": None,
            "co_edit_rate": None,
            "observation_hours": DRAFT_SURVIVAL_OBSERVATION_HOURS,
        }

    file_rows = session.exec(
        select(File.id, File.is_deleted, Project.is_deleted.label("project_deleted"))
        .join(Project, Project.id == File.project_id)
        .join(User, User.id == Project.owner_id)
        .where(
            User.is_superuser.is_(False),
            File.file_type.in_((FILE_TYPE_DRAFT, FILE_TYPE_SCRIPT)),
            File.created_at >= utc_start,
            File.created_at < eligible_end,
        )
    ).all()

    if not file_rows:
        return {
            "eligible_drafts": 0,
            "survived_drafts": 0,
            "co_edited_drafts": 0,
            "deleted_or_reverted_drafts": 0,
            "survival_rate": None,
            "co_edit_rate": None,
            "observation_hours": DRAFT_SURVIVAL_OBSERVATION_HOURS,
        }

    file_ids = [row.id for row in file_rows]
    version_rows = session.exec(
        select(
            FileVersion.file_id,
            FileVersion.version_number,
            FileVersion.change_type,
            FileVersion.change_source,
            FileVersion.word_count,
        )
        .where(FileVersion.file_id.in_(file_ids))
        .order_by(FileVersion.file_id.asc(), FileVersion.version_number.asc())
    ).all()

    versions_by_file: dict[str, list[Any]] = {}
    for vrow in version_rows:
        versions_by_file.setdefault(vrow.file_id, []).append(vrow)

    eligible = 0
    survived = 0
    co_edited = 0
    deleted_or_reverted = 0

    for frow in file_rows:
        versions = versions_by_file.get(frow.id)
        if not versions:
            continue
        first_version = versions[0]
        if first_version.change_source != CHANGE_SOURCE_AI:
            continue

        eligible += 1
        latest_version = versions[-1]
        is_deleted = bool(frow.is_deleted or frow.project_deleted)
        is_reverted = (
            latest_version.change_type == CHANGE_TYPE_RESTORE
            or (int(latest_version.word_count or 0) == 0 and int(first_version.word_count or 0) > 0)
        )
        if is_deleted or is_reverted:
            deleted_or_reverted += 1
            continue

        survived += 1
        has_user_co_edit = any(
            v.change_source == CHANGE_SOURCE_USER and v.change_type != CHANGE_TYPE_RESTORE
            for v in versions[1:]
        )
        if has_user_co_edit:
            co_edited += 1

    return {
        "eligible_drafts": eligible,
        "survived_drafts": survived,
        "co_edited_drafts": co_edited,
        "deleted_or_reverted_drafts": deleted_or_reverted,
        "survival_rate": _rate(survived, eligible),
        "co_edit_rate": _rate(co_edited, eligible),
        "observation_hours": DRAFT_SURVIVAL_OBSERVATION_HOURS,
    }


def get_stage_latency_metrics(
    session: Session,
    days: int = 7,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate per-round stage latencies, slow tails, reliability rates, and 24h AI draft survival."""
    reference_now = now or utcnow()
    now_naive_utc = reference_now.replace(tzinfo=None) if reference_now.tzinfo is not None else reference_now
    period = trailing_period(days, reference_now)
    utc_start, utc_end = period.utc_bounds

    rows = list(
        session.exec(
            select(AgentRoundMetric)
            .join(User, User.id == AgentRoundMetric.user_id)
            .where(
                User.is_superuser.is_(False),
                AgentRoundMetric.occurred_at >= utc_start,
                AgentRoundMetric.occurred_at < utc_end,
            )
            .order_by(AgentRoundMetric.occurred_at.asc())
        ).all()
    )

    total_rounds = len(rows)
    prepare_context_values = [r.prepare_context_ms for r in rows if r.prepare_context_ms is not None]
    first_output_values = [r.first_output_ms for r in rows if r.first_output_ms is not None]
    chapter_first_prose_values = [r.chapter_first_prose_ms for r in rows if r.chapter_first_prose_ms is not None]
    first_body_persisted_values = [r.first_body_persisted_ms for r in rows if r.first_body_persisted_ms is not None]

    file_body_pending_rounds = sum(1 for r in rows if r.file_body_pending_count > 0)
    leaked_tool_call_rounds = sum(1 for r in rows if r.leaked_tool_call_count > 0)
    user_stopped_rounds = sum(1 for r in rows if r.user_stopped)
    client_disconnected_rounds = sum(1 for r in rows if r.client_disconnected)

    desktop_rows = [r for r in rows if r.is_mobile is False]
    mobile_rows = [r for r in rows if r.is_mobile is True]
    unknown_rows = [r for r in rows if r.is_mobile is None]

    rows_by_day: dict[str, list[AgentRoundMetric]] = {day.isoformat(): [] for day in period.days}
    for row in rows:
        day_key = beijing_today(row.occurred_at).isoformat()
        if day_key in rows_by_day:
            rows_by_day[day_key].append(row)

    daily: list[dict[str, Any]] = []
    for day in period.days:
        day_key = day.isoformat()
        day_rows = rows_by_day[day_key]
        day_total = len(day_rows)
        day_prepare = _basic_distribution([r.prepare_context_ms for r in day_rows if r.prepare_context_ms is not None])
        day_first_out = _slow_tail_distribution([r.first_output_ms for r in day_rows if r.first_output_ms is not None])
        day_chapter = _slow_tail_distribution(
            [r.chapter_first_prose_ms for r in day_rows if r.chapter_first_prose_ms is not None],
            include_p90=True,
        )
        day_pending = sum(1 for r in day_rows if r.file_body_pending_count > 0)
        day_leaked = sum(1 for r in day_rows if r.leaked_tool_call_count > 0)
        day_stopped = sum(1 for r in day_rows if r.user_stopped)
        day_disconnected = sum(1 for r in day_rows if r.client_disconnected)
        daily.append(
            {
                "date": day_key,
                "rounds": day_total,
                "prepare_context_p50_ms": day_prepare["p50"],
                "first_output_p50_ms": day_first_out["p50"],
                "first_output_p95_ms": day_first_out["p95"],
                "first_output_slow_rate": day_first_out["slow_rate"],
                "chapter_first_prose_p50_ms": day_chapter["p50"],
                "chapter_first_prose_p95_ms": day_chapter["p95"],
                "chapter_first_prose_slow_rate": day_chapter["slow_rate"],
                "file_body_pending_rate": _rate(day_pending, day_total),
                "leaked_tool_call_rate": _rate(day_leaked, day_total),
                "user_stopped_rate": _rate(day_stopped, day_total),
                "client_disconnected_rate": _rate(day_disconnected, day_total),
            }
        )

    return {
        "days": days,
        "timezone": BEIJING_TZ_NAME,
        "period_start": period.first_day.isoformat(),
        "period_end": period.last_day.isoformat(),
        "slow_threshold_ms": SLOW_TAIL_THRESHOLD_MS,
        "total_rounds": total_rounds,
        "prepare_context_ms": _basic_distribution(prepare_context_values),
        "first_output_ms": _slow_tail_distribution(first_output_values),
        "chapter_first_prose_ms": _slow_tail_distribution(chapter_first_prose_values, include_p90=True),
        "first_body_persisted_ms": _basic_distribution(first_body_persisted_values),
        "file_body_pending_rounds": file_body_pending_rounds,
        "file_body_pending_rate": _rate(file_body_pending_rounds, total_rounds),
        "leaked_tool_call_rounds": leaked_tool_call_rounds,
        "leaked_tool_call_rate": _rate(leaked_tool_call_rounds, total_rounds),
        "user_stopped_rounds": user_stopped_rounds,
        "user_stopped_rate": _rate(user_stopped_rounds, total_rounds),
        "client_disconnected_rounds": client_disconnected_rounds,
        "client_disconnected_rate": _rate(client_disconnected_rounds, total_rounds),
        "desktop_vs_mobile": {
            "desktop": _device_slice(desktop_rows),
            "mobile": _device_slice(mobile_rows),
            "unknown": _device_slice(unknown_rows),
        },
        "ai_draft_survival": _compute_ai_draft_survival(session, period, now_naive_utc),
        "daily": daily,
    }
