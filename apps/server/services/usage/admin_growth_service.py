"""Read-only, customer-level growth metrics for the admin dashboard.

All database timestamps are naive UTC. Reporting windows are half-open and use
Beijing calendar boundaries; the prior window is shifted by exactly ``days`` so
it has the same elapsed portion of its final day as the current window.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import String, case, cast, literal
from sqlmodel import Session, func, select

from config.datetime_utils import (
    BEIJING_TIMEZONE,
    beijing_day_bounds,
    normalize_datetime_to_utc,
    utcnow,
)
from models.entities import User
from models.llm_usage import LLMUsageEvent
from models.payment import PaymentOrder
from models.subscription import SubscriptionHistory

GROWTH_DAYS = (7, 14, 30)
GRANT_ACTIONS = ("created", "upgraded", "renewed")
PAID_SOURCE = "zpay"


@dataclass(frozen=True)
class GrowthPeriod:
    start: datetime
    end: datetime

    @property
    def naive_bounds(self) -> tuple[datetime, datetime]:
        return self.start.replace(tzinfo=None), self.end.replace(tzinfo=None)


def _periods(days: int, now: datetime) -> tuple[GrowthPeriod, GrowthPeriod]:
    current_start = beijing_day_bounds(now - timedelta(days=days - 1))[0]
    current = GrowthPeriod(current_start, normalize_datetime_to_utc(now))
    # Shift both bounds by the requested number of Beijing calendar days. This
    # compares the same weekday/time-of-day slice and keeps durations identical.
    previous = GrowthPeriod(
        current.start - timedelta(days=days),
        current.end - timedelta(days=days),
    )
    return current, previous


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _grant_source(record: SubscriptionHistory) -> str:
    metadata = record.event_metadata if isinstance(record.event_metadata, dict) else {}
    source = metadata.get("source")
    if not isinstance(source, str) or not source.strip():
        return "other"
    return source.strip()


def _metrics(
    session: Session,
    period: GrowthPeriod,
    *,
    cohort_observation_end: datetime | None = None,
) -> dict[str, Any]:
    start, end = period.naive_bounds
    observation_end = (cohort_observation_end or period.end).replace(tzinfo=None)
    customer = User.is_superuser.is_(False)

    new_users = int(
        session.exec(
            select(func.count(User.id)).where(
                customer,
                User.created_at >= start,
                User.created_at < end,
            )
        ).one()
        or 0
    )

    usage_conditions = (
        customer,
        LLMUsageEvent.is_backfilled.is_(False),
        LLMUsageEvent.occurred_at >= start,
        LLMUsageEvent.occurred_at < end,
    )
    ai_active_users = int(
        session.exec(
            select(func.count(func.distinct(LLMUsageEvent.user_id)))
            .join(User, User.id == LLMUsageEvent.user_id)
            .where(*usage_conditions)
        ).one()
        or 0
    )

    cohort_usage = (
        select(func.count(func.distinct(User.id)))
        .join(LLMUsageEvent, LLMUsageEvent.user_id == User.id)
        .where(
            customer,
            User.created_at >= start,
            User.created_at < end,
            LLMUsageEvent.is_backfilled.is_(False),
            LLMUsageEvent.occurred_at >= User.created_at,
            LLMUsageEvent.occurred_at < observation_end,
        )
    )
    cohort_activated_users = int(session.exec(cohort_usage).one() or 0)

    paid_conditions = (
        customer,
        PaymentOrder.status == "paid",
        PaymentOrder.paid_at.is_not(None),
        PaymentOrder.paid_at >= start,
        PaymentOrder.paid_at < end,
    )
    paid_row = session.exec(
        select(
            func.count(PaymentOrder.id).label("orders"),
            func.coalesce(func.sum(PaymentOrder.amount_cents), 0).label("revenue_cents"),
            func.count(func.distinct(PaymentOrder.user_id)).label("users"),
        )
        .join(User, User.id == PaymentOrder.user_id)
        .where(*paid_conditions)
    ).one()

    cohort_paid_users = int(
        session.exec(
            select(func.count(func.distinct(User.id)))
            .join(PaymentOrder, PaymentOrder.user_id == User.id)
            .where(
                customer,
                User.created_at >= start,
                User.created_at < end,
                PaymentOrder.status == "paid",
                PaymentOrder.paid_at.is_not(None),
                PaymentOrder.paid_at >= User.created_at,
                PaymentOrder.paid_at < observation_end,
            )
        ).one()
        or 0
    )

    grant_rows = session.exec(
        select(SubscriptionHistory)
        .join(User, User.id == SubscriptionHistory.user_id)
        .where(
            customer,
            SubscriptionHistory.created_at >= start,
            SubscriptionHistory.created_at < end,
            SubscriptionHistory.action.in_(GRANT_ACTIONS),
            SubscriptionHistory.plan_name != "free",
        )
    ).all()
    grant_by_channel: dict[str, dict[str, Any]] = {}
    grant_user_ids: set[str] = set()
    grant_events = 0
    for record in grant_rows:
        channel = _grant_source(record)
        if channel == PAID_SOURCE:
            continue
        grant_events += 1
        grant_user_ids.add(record.user_id)
        bucket = grant_by_channel.setdefault(channel, {"events": 0, "user_ids": set()})
        bucket["events"] += 1
        bucket["user_ids"].add(record.user_id)

    grant_channels = [
        {"channel": channel, "events": values["events"], "users": len(values["user_ids"])}
        for channel, values in grant_by_channel.items()
    ]
    grant_channels.sort(key=lambda item: (-item["events"], item["channel"]))

    return {
        "new_users": new_users,
        "ai_active_users": ai_active_users,
        "cohort_activated_users": cohort_activated_users,
        "cohort_activation_rate": _rate(cohort_activated_users, new_users),
        "paid_orders": int(paid_row.orders or 0),
        "revenue_cents": int(paid_row.revenue_cents or 0),
        "paid_users": int(paid_row.users or 0),
        "cohort_paid_users": cohort_paid_users,
        "signup_to_paid_rate": _rate(cohort_paid_users, new_users),
        "grant_upgrade_events": grant_events,
        "grant_upgrade_users": len(grant_user_ids),
        "grant_channels": grant_channels,
    }


def _day_bucket(column: Any, current: GrowthPeriod, *, days: int) -> Any:
    """Portable CASE bucket for Beijing days within ``current``."""
    first_day = normalize_datetime_to_utc(current.start).astimezone(BEIJING_TIMEZONE).date()
    whens = []
    for offset in range(days):
        day_start = (current.start + timedelta(days=offset)).replace(tzinfo=None)
        day_end = min(current.start + timedelta(days=offset + 1), current.end).replace(tzinfo=None)
        if day_start >= current.end.replace(tzinfo=None):
            break
        whens.append(
            (
                (column >= day_start) & (column < day_end),
                literal((first_day + timedelta(days=offset)).isoformat()),
            )
        )
    return cast(case(*whens, else_=literal("")), String)


def _empty_daily(day: date) -> dict[str, Any]:
    return {
        "date": day,
        "new_users": 0,
        "ai_active_users": 0,
        "cohort_activated_users": 0,
        "cohort_activation_rate": None,
        "paid_orders": 0,
        "revenue_cents": 0,
        "paid_users": 0,
        "cohort_paid_users": 0,
        "signup_to_paid_rate": None,
        "grant_upgrade_events": 0,
        "grant_upgrade_users": 0,
        "grant_channels": [],
    }


def _daily(session: Session, current: GrowthPeriod, *, days: int) -> list[dict[str, Any]]:
    """Build all daily rows with six bounded aggregate/projection queries."""
    first_day = normalize_datetime_to_utc(current.start).astimezone(BEIJING_TIMEZONE).date()
    start, end = current.naive_bounds
    entries = [
        _empty_daily(first_day + timedelta(days=offset))
        for offset in range(days)
        if current.start + timedelta(days=offset) < current.end
    ]
    by_day = {entry["date"].isoformat(): entry for entry in entries}
    customer = User.is_superuser.is_(False)

    signup_day = _day_bucket(User.created_at, current, days=days)
    new_user_rows = session.exec(
        select(signup_day.label("day"), func.count(User.id).label("users"))
        .where(customer, User.created_at >= start, User.created_at < end)
        .group_by(signup_day)
    ).all()
    for row in new_user_rows:
        if row.day in by_day:
            by_day[row.day]["new_users"] = int(row.users or 0)

    usage_day = _day_bucket(LLMUsageEvent.occurred_at, current, days=days)
    active_rows = session.exec(
        select(
            usage_day.label("day"),
            func.count(func.distinct(LLMUsageEvent.user_id)).label("users"),
        )
        .join(User, User.id == LLMUsageEvent.user_id)
        .where(
            customer,
            LLMUsageEvent.is_backfilled.is_(False),
            LLMUsageEvent.occurred_at >= start,
            LLMUsageEvent.occurred_at < end,
        )
        .group_by(usage_day)
    ).all()
    for row in active_rows:
        if row.day in by_day:
            by_day[row.day]["ai_active_users"] = int(row.users or 0)

    cohort_activation_rows = session.exec(
        select(
            signup_day.label("day"),
            func.count(func.distinct(User.id)).label("users"),
        )
        .join(LLMUsageEvent, LLMUsageEvent.user_id == User.id)
        .where(
            customer,
            User.created_at >= start,
            User.created_at < end,
            LLMUsageEvent.is_backfilled.is_(False),
            LLMUsageEvent.occurred_at >= User.created_at,
            LLMUsageEvent.occurred_at < end,
        )
        .group_by(signup_day)
    ).all()
    for row in cohort_activation_rows:
        if row.day in by_day:
            by_day[row.day]["cohort_activated_users"] = int(row.users or 0)

    paid_day = _day_bucket(PaymentOrder.paid_at, current, days=days)
    payment_rows = session.exec(
        select(
            paid_day.label("day"),
            func.count(PaymentOrder.id).label("orders"),
            func.coalesce(func.sum(PaymentOrder.amount_cents), 0).label("revenue_cents"),
            func.count(func.distinct(PaymentOrder.user_id)).label("users"),
        )
        .join(User, User.id == PaymentOrder.user_id)
        .where(
            customer,
            PaymentOrder.status == "paid",
            PaymentOrder.paid_at.is_not(None),
            PaymentOrder.paid_at >= start,
            PaymentOrder.paid_at < end,
        )
        .group_by(paid_day)
    ).all()
    for row in payment_rows:
        if row.day in by_day:
            entry = by_day[row.day]
            entry["paid_orders"] = int(row.orders or 0)
            entry["revenue_cents"] = int(row.revenue_cents or 0)
            entry["paid_users"] = int(row.users or 0)

    cohort_payment_rows = session.exec(
        select(
            signup_day.label("day"),
            func.count(func.distinct(User.id)).label("users"),
        )
        .join(PaymentOrder, PaymentOrder.user_id == User.id)
        .where(
            customer,
            User.created_at >= start,
            User.created_at < end,
            PaymentOrder.status == "paid",
            PaymentOrder.paid_at.is_not(None),
            PaymentOrder.paid_at >= User.created_at,
            PaymentOrder.paid_at < end,
        )
        .group_by(signup_day)
    ).all()
    for row in cohort_payment_rows:
        if row.day in by_day:
            by_day[row.day]["cohort_paid_users"] = int(row.users or 0)

    grant_rows = session.exec(
        select(
            SubscriptionHistory.created_at,
            SubscriptionHistory.user_id,
            SubscriptionHistory.event_metadata,
        )
        .join(User, User.id == SubscriptionHistory.user_id)
        .where(
            customer,
            SubscriptionHistory.created_at >= start,
            SubscriptionHistory.created_at < end,
            SubscriptionHistory.action.in_(GRANT_ACTIONS),
            SubscriptionHistory.plan_name != "free",
        )
    ).all()
    grant_users: dict[str, set[str]] = {day: set() for day in by_day}
    grant_channels: dict[str, dict[str, dict[str, Any]]] = {day: {} for day in by_day}
    for created_at, user_id, event_metadata in grant_rows:
        local_day = normalize_datetime_to_utc(created_at).astimezone(BEIJING_TIMEZONE).date().isoformat()
        entry = by_day.get(local_day)
        if entry is None:
            continue
        source = event_metadata.get("source") if isinstance(event_metadata, dict) else None
        channel = source.strip() if isinstance(source, str) and source.strip() else "other"
        if channel == PAID_SOURCE:
            continue
        entry["grant_upgrade_events"] += 1
        grant_users[local_day].add(user_id)
        bucket = grant_channels[local_day].setdefault(channel, {"events": 0, "user_ids": set()})
        bucket["events"] += 1
        bucket["user_ids"].add(user_id)

    for day, entry in by_day.items():
        entry["grant_upgrade_users"] = len(grant_users[day])
        entry["grant_channels"] = sorted(
            (
                {"channel": channel, "events": values["events"], "users": len(values["user_ids"])}
                for channel, values in grant_channels[day].items()
            ),
            key=lambda item: (-item["events"], item["channel"]),
        )
        entry["cohort_activation_rate"] = _rate(entry["cohort_activated_users"], entry["new_users"])
        entry["signup_to_paid_rate"] = _rate(entry["cohort_paid_users"], entry["new_users"])

    return entries


DEFINITIONS = {
    "new_users": "Non-superuser accounts created in the half-open period.",
    "ai_active_users": "Distinct non-superusers with a non-backfilled LLM usage event in the period.",
    "cohort_activation_rate": "New-user cohort with a non-backfilled LLM usage event after signup, divided by new users; null when there are no signups.",
    "paid": "Payment orders with status=paid and paid_at in the period; pending, refunded, missing paid_at, and plan grants are excluded.",
    "signup_to_paid_rate": "Distinct new-user cohort members with a confirmed payment after signup, divided by new users; null when there are no signups.",
    "grants": "Non-free subscription history events whose source is not zpay; shown separately from revenue.",
}


def get_growth_dashboard(
    session: Session,
    *,
    days: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build the growth dashboard payload for a supported window."""
    if days not in GROWTH_DAYS:
        raise ValueError(f"unsupported growth window: {days}")
    report_now = normalize_datetime_to_utc(now or utcnow())
    current, previous = _periods(days, report_now)
    return {
        "days": days,
        "timezone": "Asia/Shanghai",
        "current": {
            "period_start": current.start,
            "period_end": current.end,
            "metrics": _metrics(session, current),
        },
        "previous": {
            "period_start": previous.start,
            "period_end": previous.end,
            "metrics": _metrics(session, previous),
        },
        "daily": _daily(session, current, days=days),
        "definitions": DEFINITIONS,
    }
