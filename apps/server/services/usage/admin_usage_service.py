"""Admin "Usage & cost" aggregation over the ``llm_usage_event`` ledger.

Days are Beijing calendar days. Window bounds are computed in Python as
half-open naive-UTC instants and per-day buckets are a portable SQL CASE over
those bounds, so the same queries run on SQLite (tests) and PostgreSQL (prod).
Cost is aggregated in SQL as integer 1e-8 CNY units (exact) and converted to
Decimal only for the response.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import BigInteger, String, and_, case, cast, func, literal, or_
from sqlmodel import Session, select

from config.datetime_utils import normalize_datetime_to_utc, utcnow
from models.entities import User
from models.llm_usage import PRICE_BAND_OFFPEAK, PRICE_BAND_PEAK, LLMUsageEvent
from services.usage.pricing import (
    BEIJING_TZ_NAME,
    CHINA_PUBLIC_HOLIDAY_RANGES,
    COST_WEIGHTS,
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
WINDOW_DAYS = {"today": 1, "yesterday": 1, "7d": 7}
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


def _band_units(c: Any, band: str) -> Any:
    weights = COST_WEIGHTS[band]
    return (
        _wide(c.cache_hit_tokens) * weights["cache_hit"]
        + _wide(c.cache_miss_tokens) * weights["cache_miss"]
        + _wide(c.output_tokens) * weights["output"]
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


def _in_period(period: Period) -> list[Any]:
    start, end = period.utc_bounds
    return [EVENTS.occurred_at >= start, EVENTS.occurred_at < end]


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
