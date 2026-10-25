"""Admin "Usage & cost" aggregation over the ``llm_usage_event`` ledger.

Days are Beijing calendar days. Window bounds are computed in Python as
half-open naive-UTC instants and per-day buckets are a portable SQL CASE over
those bounds, so the same queries run on SQLite (tests) and PostgreSQL (prod).
Cost is aggregated in SQL as integer 1e-8 CNY units (exact) and converted to
Decimal only for the response.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import BigInteger, String, and_, case, cast, func, literal, or_
from sqlmodel import Session, select

from config.datetime_utils import normalize_datetime_to_utc, utcnow
from models.entities import User
from models.llm_usage import (
    CALL_OUTCOME_CANCELLED,
    CALL_OUTCOME_ERROR,
    CALL_OUTCOME_SUCCESS,
    CALL_OUTCOME_TIMEOUT,
    PRICE_BAND_OFFPEAK,
    PRICE_BAND_PEAK,
    LLMUsageEvent,
    has_usage_tokens,
)
from services.usage.pricing import (
    BEIJING_TZ_NAME,
    CHINA_PUBLIC_HOLIDAY_RANGES,
    COST_WEIGHTS,
    COST_WEIGHTS_BY_VERSION,
    PRICING_VERSION,
    beijing_day_start_utc,
    beijing_range_utc,
    beijing_today,
    format_cny,
    price_table,
    units_to_cny,
)

UsageWindow = Literal["today", "yesterday", "7d"]
UserSort = Literal["cost", "calls", "tokens"]
LEGACY_PRICING_VERSION_WITHOUT_HOLIDAYS = "deepseek-flash-2026-10"
LEGACY_HOLIDAY_CORRECTION_MODEL = "deepseek-flash"


@dataclass(frozen=True)
class Period:
    first_day: date
    last_day: date

    @property
    def days(self) -> list[date]:
        count = (self.last_day - self.first_day).days + 1
        return [self.first_day + timedelta(days=offset) for offset in range(count)]

    @property
    def utc_bounds(self) -> tuple[datetime, datetime]:
        return beijing_range_utc(self.first_day, self.last_day)


def window_period(window: UsageWindow, now: datetime | None = None) -> Period:
    today = beijing_today(now or utcnow())
    if window == "today":
        return Period(today, today)
    if window == "yesterday":
        yesterday = today - timedelta(days=1)
        return Period(yesterday, yesterday)
    if window == "7d":
        return Period(today - timedelta(days=6), today)
    raise ValueError(f"unknown window: {window}")


def trailing_period(days: int, now: datetime | None = None) -> Period:
    today = beijing_today(now or utcnow())
    return Period(today - timedelta(days=days - 1), today)


# ---------------------------------------------------------------------------
# SQL building blocks
# ---------------------------------------------------------------------------

# Column namespace of the ledger table; aggregate helpers also accept a
# subquery's ``.c`` so per-day grouping can group by a plain column.
EVENTS = LLMUsageEvent.__table__.c  # type: ignore[attr-defined]


def _wide(column: Any) -> Any:
    """Widen an INTEGER token column before multiplying by a cost weight.

    On PostgreSQL ``int4 * int4`` stays int4, so a single row with a few
    million output tokens times the output weight raises "integer out of
    range". BIGINT holds any realistic product.
    """
    return cast(column, BigInteger)


def _weighted_units(c: Any, weights: dict[str, int]) -> Any:
    return (
        _wide(c.cache_hit_tokens) * weights["cache_hit"]
        + _wide(c.cache_miss_tokens) * weights["cache_miss"]
        + _wide(c.output_tokens) * weights["output"]
    )


def _band_units(c: Any, band: str) -> Any:
    """Cost of a row in ``band`` by its own pricing version (unknown: DeepSeek)."""
    default = COST_WEIGHTS[band]
    others = {
        version: bands[band]
        for version, bands in COST_WEIGHTS_BY_VERSION.items()
        if bands[band] != default
    }
    if not others:
        return _weighted_units(c, default)
    return case(
        *((c.pricing_version == version, _weighted_units(c, weights)) for version, weights in others.items()),
        else_=_weighted_units(c, default),
    )


def _legacy_holiday_peak_mislabel(c: Any) -> Any:
    """Match the known legacy rows whose stored peak band ignored holidays.

    This is a query-time correction only. The append-only ledger remains
    unchanged, and unknown models or pricing versions retain their stored band.
    """
    holiday_windows = []
    for ranges in CHINA_PUBLIC_HOLIDAY_RANGES.values():
        for first_day, end_day in ranges:
            start = beijing_day_start_utc(first_day)
            end = beijing_day_start_utc(end_day)
            holiday_windows.append(and_(c.occurred_at >= start, c.occurred_at < end))
    return and_(
        c.price_band == PRICE_BAND_PEAK,
        c.model == LEGACY_HOLIDAY_CORRECTION_MODEL,
        c.pricing_version == LEGACY_PRICING_VERSION_WITHOUT_HOLIDAYS,
        or_(*holiday_windows),
    )


def _effective_peak(c: Any) -> Any:
    return and_(c.price_band == PRICE_BAND_PEAK, ~_legacy_holiday_peak_mislabel(c))


def _peak_units_expr(c: Any) -> Any:
    return func.coalesce(
        func.sum(case((_effective_peak(c), _band_units(c, PRICE_BAND_PEAK)), else_=0)),
        0,
    )


def _offpeak_units_expr(c: Any) -> Any:
    # Anything not effectively peak is estimated at the off-peak price.
    return func.coalesce(
        func.sum(case((_effective_peak(c), 0), else_=_band_units(c, PRICE_BAND_OFFPEAK))),
        0,
    )


def _token_sum(column: Any) -> Any:
    return func.coalesce(func.sum(column), 0)


def _aggregate_columns(c: Any = EVENTS) -> list[Any]:
    return [
        func.count(c.id).label("calls"),
        _token_sum(c.cache_hit_tokens).label("cache_hit_tokens"),
        _token_sum(c.cache_miss_tokens).label("cache_miss_tokens"),
        _token_sum(c.output_tokens).label("output_tokens"),
        _peak_units_expr(c).label("peak_units"),
        _offpeak_units_expr(c).label("offpeak_units"),
    ]


def _period_bounds(period: Period) -> list[Any]:
    start, end = period.utc_bounds
    return [EVENTS.occurred_at >= start, EVENTS.occurred_at < end]


def _in_period(period: Period) -> list[Any]:
    """Usage and cost rows of the period: failed or cancelled attempts without tokens are left out."""
    return [*_period_bounds(period), has_usage_tokens(EVENTS)]


def _day_bucket(period: Period) -> Any:
    """CASE expression labelling each row with its Beijing date (ISO string)."""
    whens = []
    for day in period.days:
        start = beijing_day_start_utc(day)
        end = beijing_day_start_utc(day + timedelta(days=1))
        whens.append(
            (
                (EVENTS.occurred_at >= start) & (EVENTS.occurred_at < end),
                literal(day.isoformat()),
            )
        )
    return cast(case(*whens, else_=literal("")), String)


def _metrics(row: Any) -> dict[str, Any]:
    peak = units_to_cny(row.peak_units)
    offpeak = units_to_cny(row.offpeak_units)
    return {
        "calls": int(row.calls or 0),
        "cache_hit_tokens": int(row.cache_hit_tokens or 0),
        "cache_miss_tokens": int(row.cache_miss_tokens or 0),
        "output_tokens": int(row.output_tokens or 0),
        "cost_cny": format_cny(peak + offpeak),
        "peak_cost_cny": format_cny(peak),
        "offpeak_cost_cny": format_cny(offpeak),
    }


def _zero_metrics() -> dict[str, Any]:
    return {
        "calls": 0,
        "cache_hit_tokens": 0,
        "cache_miss_tokens": 0,
        "output_tokens": 0,
        "cost_cny": format_cny(units_to_cny(0)),
        "peak_cost_cny": format_cny(units_to_cny(0)),
        "offpeak_cost_cny": format_cny(units_to_cny(0)),
    }


def _daily_rows(
    session: Session,
    period: Period,
    *,
    user_id: str | None = None,
    with_users: bool = False,
) -> list[dict[str, Any]]:
    inner = select(
        _day_bucket(period).label("day"),
        EVENTS.id,
        EVENTS.user_id,
        EVENTS.cache_hit_tokens,
        EVENTS.cache_miss_tokens,
        EVENTS.output_tokens,
        EVENTS.price_band,
        EVENTS.model,
        EVENTS.pricing_version,
        EVENTS.occurred_at,
    ).where(*_in_period(period))
    if user_id is not None:
        inner = inner.where(EVENTS.user_id == user_id)
    rows_source = inner.subquery("bucketed")
    columns = [rows_source.c.day, *_aggregate_columns(rows_source.c)]
    if with_users:
        columns.append(func.count(func.distinct(rows_source.c.user_id)).label("users"))
    query = select(*columns).group_by(rows_source.c.day)
    by_day = {row.day: row for row in session.exec(query).all()}
    result = []
    for day in period.days:
        row = by_day.get(day.isoformat())
        entry: dict[str, Any] = {"date": day.isoformat()}
        entry.update(_metrics(row) if row is not None else _zero_metrics())
        if with_users:
            entry["users"] = int(row.users or 0) if row is not None else 0
        result.append(entry)
    return result


def _by_source(session: Session, period: Period, *, user_id: str | None = None) -> list[dict[str, Any]]:
    query = select(EVENTS.source, *_aggregate_columns()).where(*_in_period(period))
    if user_id is not None:
        query = query.where(EVENTS.user_id == user_id)
    rows = session.exec(query.group_by(EVENTS.source)).all()
    items = [{"source": row.source, **_metrics(row)} for row in rows]
    return sorted(items, key=lambda item: (-float(item["cost_cny"]), item["source"]))


def _totals(session: Session, period: Period, *, user_id: str | None = None) -> dict[str, Any]:
    query = select(
        *_aggregate_columns(),
        func.count(func.distinct(EVENTS.user_id)).label("users"),
    ).where(*_in_period(period))
    if user_id is not None:
        query = query.where(EVENTS.user_id == user_id)
    row = session.exec(query).one()
    return {"users": int(row.users or 0), **_metrics(row)}


def _period_payload(period: Period) -> dict[str, Any]:
    return {
        "timezone": BEIJING_TZ_NAME,
        "period_start": period.first_day.isoformat(),
        "period_end": period.last_day.isoformat(),
        "pricing_version": PRICING_VERSION,
        "prices": price_table(),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_usage_summary(
    session: Session, window: UsageWindow, *, now: datetime | None = None
) -> dict[str, Any]:
    period = window_period(window, now)
    return {
        "window": window,
        **_period_payload(period),
        "totals": _totals(session, period),
        "by_source": _by_source(session, period),
        "daily": _daily_rows(session, period, with_users=True),
    }


def list_user_usage(
    session: Session,
    window: UsageWindow,
    *,
    search: str | None = None,
    sort: UserSort = "cost",
    page: int = 1,
    page_size: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    period = window_period(window, now)
    calls = func.count(EVENTS.id)
    cost_units = (_peak_units_expr(EVENTS) + _offpeak_units_expr(EVENTS)).label("cost_units")
    tokens = (
        _token_sum(EVENTS.cache_hit_tokens)
        + _token_sum(EVENTS.cache_miss_tokens)
        + _token_sum(EVENTS.output_tokens)
    ).label("total_tokens")
    last_used = func.max(EVENTS.occurred_at).label("last_used_at")

    conditions = list(_in_period(period))
    if search and search.strip():
        term = search.strip()
        pattern = f"%{term}%"
        conditions.append(
            or_(User.username.ilike(pattern), User.email.ilike(pattern), User.id == term)
        )

    base = (
        select(
            User.id.label("user_id"),
            User.username,
            User.email,
            *_aggregate_columns(),
            cost_units,
            tokens,
            last_used,
        )
        .join(User, User.id == EVENTS.user_id)
        .where(*conditions)
        .group_by(User.id, User.username, User.email)
    )
    total = session.exec(select(func.count()).select_from(base.subquery())).one()

    if sort == "calls":
        order = [calls.desc(), cost_units.desc()]
    elif sort == "tokens":
        order = [tokens.desc(), cost_units.desc()]
    else:
        order = [cost_units.desc(), calls.desc()]
    # Deterministic tie-break so pages never overlap.
    order.append(User.id.asc())

    rows = session.exec(
        base.order_by(*order).offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [
        {
            "user_id": row.user_id,
            "username": row.username,
            "email": row.email,
            **_metrics(row),
            "last_used_at": normalize_datetime_to_utc(row.last_used_at) if row.last_used_at else None,
        }
        for row in rows
    ]
    return {
        "window": window,
        **_period_payload(period),
        "items": items,
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
    }


def get_user_daily_usage(
    session: Session,
    user: User,
    days: int,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    period = trailing_period(days, now)
    totals = _totals(session, period, user_id=user.id)
    totals.pop("users", None)
    return {
        "user_id": user.id,
        "username": user.username,
        "email": user.email,
        "days": days,
        **_period_payload(period),
        "totals": totals,
        "by_source": _by_source(session, period, user_id=user.id),
        "daily": _daily_rows(session, period, user_id=user.id),
    }


# ---------------------------------------------------------------------------
# Model metrics
# ---------------------------------------------------------------------------
#
# One ledger row is one provider attempt (a failover writes a failed row for the
# first provider and an answered row for the second; the OpenAI client's own HTTP
# retries stay inside one attempt). Rows written before the metric columns, and
# call sites that do not report them (material flows), have outcome NULL:
# counted as "not recorded", never as success or zero.


def _percentile(sorted_values: list[int], fraction: float) -> int | None:
    """Nearest-rank percentile of an ascending list (None when empty)."""
    if not sorted_values:
        return None
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[rank - 1]


def _distribution(values: list[int]) -> dict[str, int | None]:
    ordered = sorted(values)
    return {"p50": _percentile(ordered, 0.5), "p95": _percentile(ordered, 0.95), "samples": len(ordered)}


@dataclass
class _ModelTimings:
    latency: list[int]
    ttft: list[int]
    decode_output_tokens: int = 0
    decode_ms: int = 0
    decode_samples: int = 0

    def add(self, duration_ms: int, ttft_ms: int | None, output_tokens: int) -> None:
        self.latency.append(duration_ms)
        if ttft_ms is None:
            return
        self.ttft.append(ttft_ms)
        # Generation phase = first output delta -> stream end. Calls whose provider
        # reported no output tokens (no usage) cannot give a speed.
        generation_ms = duration_ms - ttft_ms
        if output_tokens > 0 and generation_ms > 0:
            self.decode_output_tokens += output_tokens
            self.decode_ms += generation_ms
            self.decode_samples += 1

    def payload(self) -> dict[str, Any]:
        # Ratio of sums over the window, not a mean of per-call speeds.
        speed = round(self.decode_output_tokens * 1000 / self.decode_ms, 1) if self.decode_ms > 0 else None
        return {
            "latency_ms": _distribution(self.latency),
            "ttft_ms": _distribution(self.ttft),
            "decode_tps": {"value": speed, "samples": self.decode_samples},
        }


def _outcome_count(outcome: str) -> Any:
    return func.coalesce(func.sum(case((EVENTS.outcome == outcome, 1), else_=0)), 0)


def _model_metrics_row(counts: Any, timings: _ModelTimings) -> dict[str, Any]:
    success = int(counts.success or 0)
    finished = success + int(counts.error or 0) + int(counts.timeout or 0)
    peak = units_to_cny(counts.peak_units)
    offpeak = units_to_cny(counts.offpeak_units)
    return {
        "attempts": int(counts.calls or 0),
        "success": success,
        "error": int(counts.error or 0),
        "timeout": int(counts.timeout or 0),
        "cancelled": int(counts.cancelled or 0),
        "not_recorded": int(counts.not_recorded or 0),
        # success / (success + error + timeout); cancelled and not-recorded attempts are left out.
        "success_rate": round(success / finished, 4) if finished else None,
        **timings.payload(),
        "cache_hit_tokens": int(counts.cache_hit_tokens or 0),
        "cache_miss_tokens": int(counts.cache_miss_tokens or 0),
        "output_tokens": int(counts.output_tokens or 0),
        "cost_cny": format_cny(peak + offpeak),
    }


def get_model_metrics(
    session: Session,
    window: UsageWindow,
    *,
    source: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Per-model success rate, latency, time to first token, decode speed, tokens and cost."""
    period = window_period(window, now)
    conditions = _period_bounds(period)
    if source is not None:
        conditions.append(EVENTS.source == source)

    count_columns = [
        *_aggregate_columns(),
        _outcome_count(CALL_OUTCOME_SUCCESS).label("success"),
        _outcome_count(CALL_OUTCOME_ERROR).label("error"),
        _outcome_count(CALL_OUTCOME_TIMEOUT).label("timeout"),
        _outcome_count(CALL_OUTCOME_CANCELLED).label("cancelled"),
        func.coalesce(func.sum(case((EVENTS.outcome.is_(None), 1), else_=0)), 0).label("not_recorded"),
    ]
    by_model = {
        row.model: row
        for row in session.exec(select(EVENTS.model, *count_columns).where(*conditions).group_by(EVENTS.model)).all()
    }
    totals = session.exec(select(*count_columns).where(*conditions)).one()

    # Percentiles need the individual answered attempts: two small integers per row of the window.
    timings: dict[str, _ModelTimings] = {model: _ModelTimings([], []) for model in by_model}
    pooled = _ModelTimings([], [])
    timing_rows = session.exec(
        select(EVENTS.model, EVENTS.duration_ms, EVENTS.ttft_ms, EVENTS.output_tokens).where(
            *conditions,
            EVENTS.outcome == CALL_OUTCOME_SUCCESS,
            EVENTS.duration_ms.is_not(None),
        )
    ).all()
    for row in timing_rows:
        duration = int(row.duration_ms)
        ttft = int(row.ttft_ms) if row.ttft_ms is not None else None
        output = int(row.output_tokens or 0)
        # The three reads are separate statements: a row committed in between may name a new model.
        timings.setdefault(row.model, _ModelTimings([], [])).add(duration, ttft, output)
        pooled.add(duration, ttft, output)

    items = [{"model": model, **_model_metrics_row(row, timings[model])} for model, row in by_model.items()]
    items.sort(key=lambda item: (-item["attempts"], item["model"]))
    return {
        "window": window,
        **_period_payload(period),
        "source": source,
        "items": items,
        "totals": _model_metrics_row(totals, pooled),
    }
