"""Per-round agent latency & reliability persistence and admin stage-latency aggregation.

Writes one narrow numeric/enum/boolean row per ``/agent/stream`` round into
``agent_round_metric`` (never manuscript text, prompt text, or PII). Every
persistence helper is best-effort and runs in its own short-lived session so a
metric failure never delays or breaks SSE delivery.

Admin queries are customer-scoped (``User.is_superuser == False``) and aggregate
over Beijing calendar days, matching ``admin_usage_service`` and
``admin_growth_service``. On PostgreSQL, stage-latency percentiles and slow tails
aggregate directly in SQL; 24h AI draft survival uses a single CTE query on both
PostgreSQL and SQLite instead of materializing full version histories in Python.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import and_, case, func, or_, text
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
from services.infra.admin_analytics_cache import admin_read_statement_timeout
from services.usage.admin_usage_service import Period, trailing_period
from services.usage.pricing import BEIJING_TZ_NAME, beijing_day_bucket_case, beijing_today
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


def _is_postgres(session: Session) -> bool:
    bind = session.get_bind()
    return bind is not None and bind.dialect.name == "postgresql"


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


def _empty_device_slice() -> dict[str, Any]:
    return {
        "rounds": 0,
        "first_output_ms": {
            "p50": None,
            "p95": None,
            "samples": 0,
            "slow_count": 0,
            "slow_rate": None,
        },
        "chapter_first_prose_ms": {
            "p50": None,
            "p90": None,
            "p95": None,
            "samples": 0,
            "slow_count": 0,
            "slow_rate": None,
        },
        "client_disconnected_rounds": 0,
        "client_disconnected_rate": None,
    }


def _device_slice(rows: list[Any]) -> dict[str, Any]:
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
    """Compute 24h AI draft survival / co-edit rate in a single SQL CTE query.

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

    candidate_files = (
        select(
            File.id.label("file_id"),
            File.is_deleted.label("file_deleted"),
            Project.is_deleted.label("project_deleted"),
        )
        .join(Project, Project.id == File.project_id)
        .join(User, User.id == Project.owner_id)
        .where(
            User.is_superuser.is_(False),
            File.file_type.in_((FILE_TYPE_DRAFT, FILE_TYPE_SCRIPT)),
            File.created_at >= utc_start,
            File.created_at < eligible_end,
        )
        .cte("candidate_draft_files")
    )

    # Single-pass window scan over file_version per candidate draft (replaces
    # 4 separate joins against file_version that read ~42k buffers / ~15s on 30d).
    version_ranked = (
        select(
            candidate_files.c.file_id.label("file_id"),
            candidate_files.c.file_deleted.label("file_deleted"),
            candidate_files.c.project_deleted.label("project_deleted"),
            FileVersion.change_source.label("change_source"),
            FileVersion.change_type.label("change_type"),
            func.coalesce(FileVersion.word_count, 0).label("word_count"),
            func.row_number()
            .over(
                partition_by=FileVersion.file_id,
                order_by=FileVersion.version_number.asc(),
            )
            .label("rn_asc"),
            func.row_number()
            .over(
                partition_by=FileVersion.file_id,
                order_by=FileVersion.version_number.desc(),
            )
            .label("rn_desc"),
        )
        .select_from(candidate_files)
        .join(FileVersion, FileVersion.file_id == candidate_files.c.file_id)
        .cte("draft_version_ranked")
    )

    per_file = (
        select(
            version_ranked.c.file_id.label("file_id"),
            func.max(case((version_ranked.c.file_deleted.is_(True), 1), else_=0)).label("file_deleted"),
            func.max(case((version_ranked.c.project_deleted.is_(True), 1), else_=0)).label(
                "project_deleted"
            ),
            func.max(
                case((version_ranked.c.rn_asc == 1, version_ranked.c.change_source))
            ).label("first_source"),
            func.max(
                case((version_ranked.c.rn_asc == 1, version_ranked.c.word_count))
            ).label("first_words"),
            func.max(
                case((version_ranked.c.rn_desc == 1, version_ranked.c.change_type))
            ).label("latest_change_type"),
            func.max(
                case((version_ranked.c.rn_desc == 1, version_ranked.c.word_count))
            ).label("latest_words"),
            func.max(
                case(
                    (
                        and_(
                            version_ranked.c.rn_asc > 1,
                            version_ranked.c.change_source == CHANGE_SOURCE_USER,
                            version_ranked.c.change_type != CHANGE_TYPE_RESTORE,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("has_user_coedit"),
        )
        .group_by(version_ranked.c.file_id)
        .cte("draft_per_file_summary")
    )

    is_deleted_or_reverted = or_(
        per_file.c.file_deleted == 1,
        per_file.c.project_deleted == 1,
        per_file.c.latest_change_type == CHANGE_TYPE_RESTORE,
        and_(
            func.coalesce(per_file.c.latest_words, 0) == 0,
            func.coalesce(per_file.c.first_words, 0) > 0,
        ),
    )

    row = session.exec(
        select(
            func.count(per_file.c.file_id).label("eligible"),
            func.coalesce(
                func.sum(case((is_deleted_or_reverted, 1), else_=0)),
                0,
            ).label("deleted_or_reverted"),
            func.coalesce(
                func.sum(
                    case(
                        (and_(~is_deleted_or_reverted, per_file.c.has_user_coedit == 1), 1),
                        else_=0,
                    )
                ),
                0,
            ).label("co_edited"),
        )
        .select_from(per_file)
        .where(per_file.c.first_source == CHANGE_SOURCE_AI)
    ).one()

    eligible = int(row.eligible or 0)
    deleted_or_reverted = int(row.deleted_or_reverted or 0)
    survived = max(eligible - deleted_or_reverted, 0)
    co_edited = int(row.co_edited or 0)

    return {
        "eligible_drafts": eligible,
        "survived_drafts": survived,
        "co_edited_drafts": co_edited,
        "deleted_or_reverted_drafts": deleted_or_reverted,
        "survival_rate": _rate(survived, eligible),
        "co_edit_rate": _rate(co_edited, eligible),
        "observation_hours": DRAFT_SURVIVAL_OBSERVATION_HOURS,
    }


def _int_or_none(value: Any) -> int | None:
    return int(value) if value is not None else None


def _get_stage_latency_metrics_postgres(
    session: Session,
    period: Period,
) -> dict[str, Any]:
    utc_start, utc_end = period.utc_bounds

    # 1. Overall + desktop/mobile/unknown split in one SQL scan using GROUPING SETS.
    summary_rows = session.execute(
        text(
            """
            SELECT
                GROUPING(device_key) AS is_total,
                device_key,
                COUNT(*) AS rounds,
                COUNT(m.prepare_context_ms) AS prep_samples,
                percentile_disc(0.50) WITHIN GROUP (ORDER BY m.prepare_context_ms)
                    FILTER (WHERE m.prepare_context_ms IS NOT NULL) AS prep_p50,
                percentile_disc(0.95) WITHIN GROUP (ORDER BY m.prepare_context_ms)
                    FILTER (WHERE m.prepare_context_ms IS NOT NULL) AS prep_p95,
                COUNT(m.first_output_ms) AS fo_samples,
                COUNT(*) FILTER (WHERE m.first_output_ms >= :slow_ms) AS fo_slow,
                percentile_disc(0.50) WITHIN GROUP (ORDER BY m.first_output_ms)
                    FILTER (WHERE m.first_output_ms IS NOT NULL) AS fo_p50,
                percentile_disc(0.95) WITHIN GROUP (ORDER BY m.first_output_ms)
                    FILTER (WHERE m.first_output_ms IS NOT NULL) AS fo_p95,
                COUNT(m.chapter_first_prose_ms) AS cp_samples,
                COUNT(*) FILTER (WHERE m.chapter_first_prose_ms >= :slow_ms) AS cp_slow,
                percentile_disc(0.50) WITHIN GROUP (ORDER BY m.chapter_first_prose_ms)
                    FILTER (WHERE m.chapter_first_prose_ms IS NOT NULL) AS cp_p50,
                percentile_disc(0.90) WITHIN GROUP (ORDER BY m.chapter_first_prose_ms)
                    FILTER (WHERE m.chapter_first_prose_ms IS NOT NULL) AS cp_p90,
                percentile_disc(0.95) WITHIN GROUP (ORDER BY m.chapter_first_prose_ms)
                    FILTER (WHERE m.chapter_first_prose_ms IS NOT NULL) AS cp_p95,
                COUNT(m.first_body_persisted_ms) AS bp_samples,
                percentile_disc(0.50) WITHIN GROUP (ORDER BY m.first_body_persisted_ms)
                    FILTER (WHERE m.first_body_persisted_ms IS NOT NULL) AS bp_p50,
                percentile_disc(0.95) WITHIN GROUP (ORDER BY m.first_body_persisted_ms)
                    FILTER (WHERE m.first_body_persisted_ms IS NOT NULL) AS bp_p95,
                COUNT(*) FILTER (WHERE m.file_body_pending_count > 0) AS pending_rounds,
                COUNT(*) FILTER (WHERE m.leaked_tool_call_count > 0) AS leaked_rounds,
                COUNT(*) FILTER (WHERE m.user_stopped IS TRUE) AS stopped_rounds,
                COUNT(*) FILTER (WHERE m.client_disconnected IS TRUE) AS disconnected_rounds
            FROM (
                SELECT
                    arm.*,
                    CASE
                        WHEN arm.is_mobile IS TRUE THEN 'mobile'
                        WHEN arm.is_mobile IS FALSE THEN 'desktop'
                        ELSE 'unknown'
                    END AS device_key
                FROM agent_round_metric arm
                JOIN "user" u ON u.id = arm.user_id
                WHERE u.is_superuser IS FALSE
                  AND arm.occurred_at >= :utc_start
                  AND arm.occurred_at < :utc_end
            ) m
            GROUP BY GROUPING SETS ((), (device_key))
            """
        ),
        {"utc_start": utc_start, "utc_end": utc_end, "slow_ms": SLOW_TAIL_THRESHOLD_MS},
    ).all()

    overall = None
    devices = {
        "desktop": _empty_device_slice(),
        "mobile": _empty_device_slice(),
        "unknown": _empty_device_slice(),
    }
    for row in summary_rows:
        if row.is_total == 1:
            overall = row
        elif row.device_key in devices:
            rounds_count = int(row.rounds or 0)
            fo_samples = int(row.fo_samples or 0)
            fo_slow = int(row.fo_slow or 0)
            cp_samples = int(row.cp_samples or 0)
            cp_slow = int(row.cp_slow or 0)
            disconnected = int(row.disconnected_rounds or 0)
            devices[row.device_key] = {
                "rounds": rounds_count,
                "first_output_ms": {
                    "p50": _int_or_none(row.fo_p50),
                    "p95": _int_or_none(row.fo_p95),
                    "samples": fo_samples,
                    "slow_count": fo_slow,
                    "slow_rate": _rate(fo_slow, fo_samples),
                },
                "chapter_first_prose_ms": {
                    "p50": _int_or_none(row.cp_p50),
                    "p90": _int_or_none(row.cp_p90),
                    "p95": _int_or_none(row.cp_p95),
                    "samples": cp_samples,
                    "slow_count": cp_slow,
                    "slow_rate": _rate(cp_slow, cp_samples),
                },
                "client_disconnected_rounds": disconnected,
                "client_disconnected_rate": _rate(disconnected, rounds_count),
            }

    # 2. Daily breakdown in one SQL query.
    day_expr = beijing_day_bucket_case(AgentRoundMetric.occurred_at, period.days)
    daily_rows = session.exec(
        select(
            day_expr.label("day"),
            func.count(AgentRoundMetric.id).label("rounds"),
            func.percentile_disc(0.50)
            .within_group(AgentRoundMetric.prepare_context_ms)
            .filter(AgentRoundMetric.prepare_context_ms.is_not(None))
            .label("prep_p50"),
            func.percentile_disc(0.50)
            .within_group(AgentRoundMetric.first_output_ms)
            .filter(AgentRoundMetric.first_output_ms.is_not(None))
            .label("fo_p50"),
            func.percentile_disc(0.95)
            .within_group(AgentRoundMetric.first_output_ms)
            .filter(AgentRoundMetric.first_output_ms.is_not(None))
            .label("fo_p95"),
            func.count(AgentRoundMetric.first_output_ms).label("fo_samples"),
            func.count(AgentRoundMetric.id)
            .filter(AgentRoundMetric.first_output_ms >= SLOW_TAIL_THRESHOLD_MS)
            .label("fo_slow"),
            func.percentile_disc(0.50)
            .within_group(AgentRoundMetric.chapter_first_prose_ms)
            .filter(AgentRoundMetric.chapter_first_prose_ms.is_not(None))
            .label("cp_p50"),
            func.percentile_disc(0.95)
            .within_group(AgentRoundMetric.chapter_first_prose_ms)
            .filter(AgentRoundMetric.chapter_first_prose_ms.is_not(None))
            .label("cp_p95"),
            func.count(AgentRoundMetric.chapter_first_prose_ms).label("cp_samples"),
            func.count(AgentRoundMetric.id)
            .filter(AgentRoundMetric.chapter_first_prose_ms >= SLOW_TAIL_THRESHOLD_MS)
            .label("cp_slow"),
            func.count(AgentRoundMetric.id)
            .filter(AgentRoundMetric.file_body_pending_count > 0)
            .label("pending_rounds"),
            func.count(AgentRoundMetric.id)
            .filter(AgentRoundMetric.leaked_tool_call_count > 0)
            .label("leaked_rounds"),
            func.count(AgentRoundMetric.id)
            .filter(AgentRoundMetric.user_stopped.is_(True))
            .label("stopped_rounds"),
            func.count(AgentRoundMetric.id)
            .filter(AgentRoundMetric.client_disconnected.is_(True))
            .label("disconnected_rounds"),
        )
        .join(User, User.id == AgentRoundMetric.user_id)
        .where(
            User.is_superuser.is_(False),
            AgentRoundMetric.occurred_at >= utc_start,
            AgentRoundMetric.occurred_at < utc_end,
        )
        .group_by(day_expr)
    ).all()
    by_day = {row.day: row for row in daily_rows}

    daily: list[dict[str, Any]] = []
    for day in period.days:
        day_key = day.isoformat()
        row = by_day.get(day_key)
        if row is None:
            daily.append(
                {
                    "date": day_key,
                    "rounds": 0,
                    "prepare_context_p50_ms": None,
                    "first_output_p50_ms": None,
                    "first_output_p95_ms": None,
                    "first_output_slow_rate": None,
                    "chapter_first_prose_p50_ms": None,
                    "chapter_first_prose_p95_ms": None,
                    "chapter_first_prose_slow_rate": None,
                    "file_body_pending_rate": None,
                    "leaked_tool_call_rate": None,
                    "user_stopped_rate": None,
                    "client_disconnected_rate": None,
                }
            )
            continue
        day_total = int(row.rounds or 0)
        daily.append(
            {
                "date": day_key,
                "rounds": day_total,
                "prepare_context_p50_ms": _int_or_none(row.prep_p50),
                "first_output_p50_ms": _int_or_none(row.fo_p50),
                "first_output_p95_ms": _int_or_none(row.fo_p95),
                "first_output_slow_rate": _rate(int(row.fo_slow or 0), int(row.fo_samples or 0)),
                "chapter_first_prose_p50_ms": _int_or_none(row.cp_p50),
                "chapter_first_prose_p95_ms": _int_or_none(row.cp_p95),
                "chapter_first_prose_slow_rate": _rate(int(row.cp_slow or 0), int(row.cp_samples or 0)),
                "file_body_pending_rate": _rate(int(row.pending_rounds or 0), day_total),
                "leaked_tool_call_rate": _rate(int(row.leaked_rounds or 0), day_total),
                "user_stopped_rate": _rate(int(row.stopped_rounds or 0), day_total),
                "client_disconnected_rate": _rate(int(row.disconnected_rounds or 0), day_total),
            }
        )

    total_rounds = int(overall.rounds or 0) if overall else 0
    fo_samples = int(overall.fo_samples or 0) if overall else 0
    fo_slow = int(overall.fo_slow or 0) if overall else 0
    cp_samples = int(overall.cp_samples or 0) if overall else 0
    cp_slow = int(overall.cp_slow or 0) if overall else 0
    pending_rounds = int(overall.pending_rounds or 0) if overall else 0
    leaked_rounds = int(overall.leaked_rounds or 0) if overall else 0
    stopped_rounds = int(overall.stopped_rounds or 0) if overall else 0
    disconnected_rounds = int(overall.disconnected_rounds or 0) if overall else 0

    return {
        "total_rounds": total_rounds,
        "prepare_context_ms": {
            "p50": _int_or_none(overall.prep_p50) if overall else None,
            "p95": _int_or_none(overall.prep_p95) if overall else None,
            "samples": int(overall.prep_samples or 0) if overall else 0,
        },
        "first_output_ms": {
            "p50": _int_or_none(overall.fo_p50) if overall else None,
            "p95": _int_or_none(overall.fo_p95) if overall else None,
            "samples": fo_samples,
            "slow_count": fo_slow,
            "slow_rate": _rate(fo_slow, fo_samples),
        },
        "chapter_first_prose_ms": {
            "p50": _int_or_none(overall.cp_p50) if overall else None,
            "p90": _int_or_none(overall.cp_p90) if overall else None,
            "p95": _int_or_none(overall.cp_p95) if overall else None,
            "samples": cp_samples,
            "slow_count": cp_slow,
            "slow_rate": _rate(cp_slow, cp_samples),
        },
        "first_body_persisted_ms": {
            "p50": _int_or_none(overall.bp_p50) if overall else None,
            "p95": _int_or_none(overall.bp_p95) if overall else None,
            "samples": int(overall.bp_samples or 0) if overall else 0,
        },
        "file_body_pending_rounds": pending_rounds,
        "file_body_pending_rate": _rate(pending_rounds, total_rounds),
        "leaked_tool_call_rounds": leaked_rounds,
        "leaked_tool_call_rate": _rate(leaked_rounds, total_rounds),
        "user_stopped_rounds": stopped_rounds,
        "user_stopped_rate": _rate(stopped_rounds, total_rounds),
        "client_disconnected_rounds": disconnected_rounds,
        "client_disconnected_rate": _rate(disconnected_rounds, total_rounds),
        "desktop_vs_mobile": devices,
        "daily": daily,
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

    with admin_read_statement_timeout(session):
        survival = _compute_ai_draft_survival(session, period, now_naive_utc)

        if _is_postgres(session):
            agg = _get_stage_latency_metrics_postgres(session, period)
            return {
                "days": days,
                "timezone": BEIJING_TZ_NAME,
                "period_start": period.first_day.isoformat(),
                "period_end": period.last_day.isoformat(),
                "slow_threshold_ms": SLOW_TAIL_THRESHOLD_MS,
                **agg,
                "ai_draft_survival": survival,
            }

        # Lightweight column projection (avoid hydrating full AgentRoundMetric ORM entities).
        rows = list(
            session.exec(
                select(
                    AgentRoundMetric.occurred_at,
                    AgentRoundMetric.is_mobile,
                    AgentRoundMetric.prepare_context_ms,
                    AgentRoundMetric.first_output_ms,
                    AgentRoundMetric.chapter_first_prose_ms,
                    AgentRoundMetric.first_body_persisted_ms,
                    AgentRoundMetric.file_body_pending_count,
                    AgentRoundMetric.leaked_tool_call_count,
                    AgentRoundMetric.user_stopped,
                    AgentRoundMetric.client_disconnected,
                )
                .join(User, User.id == AgentRoundMetric.user_id)
                .where(
                    User.is_superuser.is_(False),
                    AgentRoundMetric.occurred_at >= utc_start,
                    AgentRoundMetric.occurred_at < utc_end,
                )
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

    rows_by_day: dict[str, list[Any]] = {day.isoformat(): [] for day in period.days}
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
        "ai_draft_survival": survival,
        "daily": daily,
    }
