"""Public, aggregate model-call observations (not end-to-end or time-based uptime)."""

from datetime import datetime, timedelta
from threading import Lock
from time import monotonic
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import and_, case, func
from sqlmodel import Session, select

from config.datetime_utils import UTCDateTime, normalize_datetime_to_utc, utcnow
from models.llm_usage import (
    CALL_OUTCOME_ERROR,
    CALL_OUTCOME_SUCCESS,
    CALL_OUTCOME_TIMEOUT,
    LLM_USAGE_SOURCE_AGENT,
    LLM_USAGE_SOURCE_ROUTER,
    LLMUsageEvent,
)
from services.usage.pricing import BEIJING_TZ_NAME, beijing_day_start_utc, beijing_today

ObservationStatus = Literal["operational", "degraded", "unknown"]
HISTORY_DAYS = 30
WINDOW_MINUTES = 60
CACHE_SECONDS = 30
FRESH_SECONDS = 120


class DailyObservation(BaseModel):
    date: str
    status: ObservationStatus
    success_rate: float | None


class ServiceStatus(BaseModel):
    generated_at: UTCDateTime
    expires_at: UTCDateTime
    window_start: UTCDateTime
    window_minutes: int = WINDOW_MINUTES
    timezone: str = BEIJING_TZ_NAME
    status: ObservationStatus
    success_rate: float | None
    history: list[DailyObservation]


def _observation(successes: int, attempts: int) -> tuple[ObservationStatus, float | None]:
    if not attempts:
        return "unknown", None
    return ("operational" if successes == attempts else "degraded"), successes / attempts


def get_service_status(session: Session, *, now: datetime | None = None) -> ServiceStatus:
    """One bounded SQL aggregate; never loads user/model/correlation IDs or raw calls."""
    now = normalize_datetime_to_utc(now or utcnow())
    today = beijing_today(now)
    days = [today - timedelta(days=offset) for offset in reversed(range(HISTORY_DAYS))]
    window_start = now - timedelta(minutes=WINDOW_MINUTES)
    events = LLMUsageEvent.__table__.c
    # Python UTC boundaries keep the query identical on PostgreSQL and test SQLite.
    day_key = case(
        *[
            (
                and_(
                    events.occurred_at >= beijing_day_start_utc(day),
                    events.occurred_at < beijing_day_start_utc(day + timedelta(days=1)),
                ),
                day.isoformat(),
            )
            for day in days
        ]
    )
    successful = events.outcome == CALL_OUTCOME_SUCCESS
    recent = events.occurred_at >= window_start.replace(tzinfo=None)
    rows = session.exec(
        select(
            day_key.label("day"),
            func.count().label("attempts"),
            func.sum(case((successful, 1), else_=0)).label("successes"),
            func.sum(case((recent, 1), else_=0)).label("recent_attempts"),
            func.sum(case((and_(recent, successful), 1), else_=0)).label("recent_successes"),
        )
        .where(
            events.occurred_at >= beijing_day_start_utc(days[0]),
            events.occurred_at < now.replace(tzinfo=None),
            events.source.in_((LLM_USAGE_SOURCE_AGENT, LLM_USAGE_SOURCE_ROUTER)),
            events.outcome.in_((CALL_OUTCOME_SUCCESS, CALL_OUTCOME_ERROR, CALL_OUTCOME_TIMEOUT)),
            events.is_backfilled.is_(False),
        )
        .group_by(day_key)
    ).all()
    by_day = {row.day: row for row in rows}
    state, rate = _observation(
        sum(row.recent_successes for row in rows),
        sum(row.recent_attempts for row in rows),
    )
    history = []
    for day in days:
        row = by_day.get(day.isoformat())
        day_state, day_rate = _observation(row.successes, row.attempts) if row else ("unknown", None)
        history.append(DailyObservation(date=day.isoformat(), status=day_state, success_rate=day_rate))
    return ServiceStatus(
        generated_at=now,
        expires_at=now + timedelta(seconds=FRESH_SECONDS),
        window_start=window_start,
        status=state,
        success_rate=rate,
        history=history,
    )


class StatusCache:
    """Short per-process cache; a failed refresh never serves a cached green snapshot."""

    def __init__(self):
        self._lock = Lock()
        self._snapshot: ServiceStatus | None = None
        self._until = 0.0

    def get(self, session: Session) -> ServiceStatus:
        with self._lock:
            if self._snapshot is not None and monotonic() < self._until:
                return self._snapshot
            snapshot = get_service_status(session)
            self._snapshot = snapshot
            self._until = monotonic() + CACHE_SECONDS
            return snapshot
