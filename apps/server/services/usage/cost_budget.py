"""Cross-process ¥5 free-user budget checked before every real model call.

Reserve an upper bound before sending; reconcile only when usage is known.
Unknown/cancelled calls retain their reservation. The Beijing day and price band
are fixed at call start, including for calls that finish across midnight. Live
usage records use that same timestamp. No process-local or Redis fallback can
bypass this durable budget.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, text
from sqlmodel import select

from config.datetime_utils import utcnow
from core.error_handler import APIException
from models.ai_cost_budget import AICostDailyBudget, AICostReservation
from models.llm_usage import LLMUsageEvent
from models.subscription import SubscriptionPlan, UserSubscription
from services.usage.pricing import beijing_range_utc, beijing_today, cost_units, naive_utc, price_band, units_to_cny

DAILY_LIMIT_UNITS = 500_000_000
COST_LIMIT_CODE = "ERR_QUOTA_AI_DAILY_COST_EXCEEDED"
UNAVAILABLE_CODE = "ERR_AI_COST_BUDGET_UNAVAILABLE"
_attribution: ContextVar[Any] = ContextVar("ai_cost_attribution", default=None)
_call_started_at: ContextVar[datetime | None] = ContextVar("ai_cost_call_started_at", default=None)
_call_user_id: ContextVar[str | None] = ContextVar("ai_cost_call_user_id", default=None)
KNOWN_FLASH_MODELS = {"deepseek-flash"}
KNOWN_PRICING_VERSIONS = {"deepseek-flash-2026-10", "deepseek-flash-2026-10-holiday"}


def call_started_at(user_id: str | None = None) -> datetime | None:
    return _call_started_at.get() if user_id == _call_user_id.get() else None


def bind_call_start(reservation: Reservation | None) -> None:
    _call_started_at.set(reservation.started_at if reservation else None)
    _call_user_id.set(current_user_id() if reservation else None)


@contextmanager
def budget_attribution(attribution: Any):
    token = _attribution.set(attribution)
    try:
        yield
    finally:
        _attribution.reset(token)


def current_user_id() -> str | None:
    explicit = _attribution.get()
    if explicit is not None:
        return explicit.user_id
    from agent.tools.mcp_tools import ToolContext

    return ToolContext.get_user_id()


def _default_session_factory():
    from database import create_session

    return create_session()


_session_factory = _default_session_factory


def _factory():
    return _session_factory()


def _error(code: str, status: int) -> APIException:
    return APIException(error_code=code, status_code=status)


def _log_budget(message: str, *, user_id: str, day: Any, charged_units: int, **extra: Any) -> None:
    from utils.logger import get_logger, log_with_context

    attribution = _attribution.get()
    log_with_context(
        get_logger(__name__),
        20,
        message,
        user_id=user_id,
        usage_source=getattr(attribution, "source", "agent"),
        budget_day=str(day),
        charged_units=charged_units,
        charged_cny=str(units_to_cny(charged_units)),
        daily_limit_units=DAILY_LIMIT_UNITS,
        daily_limit_cny="5",
        **extra,
    )


def _begin_locked(session: Any) -> None:
    if session.get_bind().dialect.name == "sqlite":
        session.execute(text("BEGIN IMMEDIATE"))


def _has_non_free_entitlement(session: Any, user_id: str, now: datetime) -> bool:
    """Active non-free entitlement, including granted/trial Pro subscriptions."""
    subscription = session.exec(
        select(UserSubscription, SubscriptionPlan)
        .join(SubscriptionPlan, SubscriptionPlan.id == UserSubscription.plan_id)
        .where(UserSubscription.user_id == user_id)
    ).first()
    if subscription is None:
        return False
    sub, plan = subscription
    if sub.status != "active" or plan.name == "free":
        return False
    if sub.current_period_end is None:
        # The existing entitlement contract requires a period end (grants also
        # have one). An invalid entitlement must not silently become free.
        raise _error(UNAVAILABLE_CODE, 503)
    return naive_utc(sub.current_period_end) >= naive_utc(now)


def _input_upper_bound(payload: dict[str, Any]) -> int:
    # A text token cannot contain less than one UTF-8 byte. Count the entire
    # serialized request (including tools and framing), plus per-message framing.
    # Multimodal inputs have provider-specific accounting; fail closed for free.
    messages = payload.get("messages", [])
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, list) and any(
            item.get("type") not in {"text", "input_text"} for item in content if isinstance(item, dict)
        ):
            raise _error(UNAVAILABLE_CODE, 503)
    return len(json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")) + 128 * (len(messages) + 1)


@dataclass(frozen=True)
class Reservation:
    id: str
    started_at: datetime


def reserve_model_call(
    payload: dict[str, Any], *, user_id: str | None = None, now: datetime | None = None
) -> Reservation | None:
    """Atomically reserve worst-case call cost; paid users bypass the free cap."""
    started = naive_utc(now or utcnow())
    user_id = user_id or current_user_id()
    _call_started_at.set(started if user_id else None)
    _call_user_id.set(user_id)
    if not user_id:
        return None  # Infrastructure calls without a user are not free-user calls.
    try:
        with _factory() as session:
            _begin_locked(session)
            if _has_non_free_entitlement(session, user_id, started):
                session.rollback()
                return None
            if payload.get("model") not in KNOWN_FLASH_MODELS:
                raise _error(UNAVAILABLE_CODE, 503)
            if payload.get("n", 1) != 1:
                raise _error(UNAVAILABLE_CODE, 503)
            maximum = payload.get("max_completion_tokens", payload.get("max_tokens"))
            if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum <= 0:
                # Set explicit output bound; DeepSeek thinking default can be 64K.
                maximum = 65_536
                payload["max_tokens"] = maximum
            upper = cost_units("peak", 0, _input_upper_bound(payload), maximum)
            day = beijing_today(started)
            # Portable upsert, then lock this user's day. SQLite has its writer
            # lock already; PostgreSQL locks the inserted/conflicting row.
            session.execute(
                text(
                    "INSERT INTO ai_cost_daily_budget (user_id, day, charged_units) "
                    "VALUES (:user_id, :day, -1) ON CONFLICT (user_id, day) DO NOTHING"
                ),
                {"user_id": user_id, "day": day},
            )
            bucket = session.exec(
                select(AICostDailyBudget)
                .where(AICostDailyBudget.user_id == user_id, AICostDailyBudget.day == day)
                .with_for_update()
            ).one()
            initialized = bucket.charged_units < 0
            if initialized:
                start, end = beijing_range_utc(day, day)
                historical = session.exec(
                    select(LLMUsageEvent).where(
                        LLMUsageEvent.user_id == user_id,
                        LLMUsageEvent.occurred_at >= start,
                        LLMUsageEvent.occurred_at < end,
                    )
                ).all()
                if any(
                    event.model not in KNOWN_FLASH_MODELS or event.pricing_version not in KNOWN_PRICING_VERSIONS
                    for event in historical
                ):
                    # We cannot prove a remaining budget from unpriced history.
                    raise _error(UNAVAILABLE_CODE, 503)
                bucket.charged_units = sum(
                    cost_units(
                        price_band(event.occurred_at),
                        event.cache_hit_tokens,
                        event.cache_miss_tokens,
                        event.output_tokens,
                    )
                    for event in historical
                )
                seed_units = bucket.charged_units
            if bucket.charged_units + upper > DAILY_LIMIT_UNITS:
                pending = session.exec(
                    select(func.coalesce(func.sum(AICostReservation.reserved_units), 0)).where(
                        AICostReservation.user_id == user_id,
                        AICostReservation.day == day,
                        AICostReservation.settled_units.is_(None),
                    )
                ).one()
                # Persist historical initialization even when already over cap.
                session.add(bucket)
                session.commit()
                if initialized:
                    _log_budget(
                        "AI free daily cost budget initialized",
                        user_id=user_id,
                        day=day,
                        charged_units=seed_units,
                        historical_rows=len(historical),
                    )
                _log_budget(
                    "AI free daily cost limit reached",
                    user_id=user_id,
                    day=day,
                    charged_units=bucket.charged_units,
                    requested_reservation_units=upper,
                    pending_reserved_units=int(pending),
                    error_code=COST_LIMIT_CODE,
                )
                raise _error(COST_LIMIT_CODE, 402)
            reservation = AICostReservation(
                id=uuid.uuid4().hex, user_id=user_id, day=day, reserved_units=upper, started_at=started
            )
            bucket.charged_units += upper
            session.add(bucket)
            session.add(reservation)
            session.commit()
            if initialized:
                _log_budget(
                    "AI free daily cost budget initialized",
                    user_id=user_id,
                    day=day,
                    charged_units=seed_units,
                    historical_rows=len(historical),
                )
            return Reservation(reservation.id, started)
    except APIException:
        raise
    except Exception as exc:
        raise _error(UNAVAILABLE_CODE, 503) from exc


def settle_model_call(reservation: Reservation | None, usage: Any) -> None:
    """Reconcile exactly once; missing usage/failure keeps the upper-bound charge."""
    if reservation is None or usage is None:
        return
    from services.usage.llm_usage_service import _int_field

    prompt = _int_field(usage, "prompt_tokens")
    if prompt is None:
        prompt = _int_field(usage, "input_tokens")
    output = _int_field(usage, "completion_tokens")
    if output is None:
        output = _int_field(usage, "output_tokens")
    if prompt is None or output is None:
        return  # An incomplete usage object is not a proven zero-cost call.
    from services.usage.llm_usage_service import extract_usage_tokens

    tokens = extract_usage_tokens(usage)
    hit = min(prompt, tokens.cache_hit)
    # An absent/inconsistent cache split must not discount unaccounted input.
    miss = max(tokens.cache_miss, prompt - hit)
    actual = cost_units(price_band(reservation.started_at), hit, miss, tokens.output)
    try:
        with _factory() as session:
            _begin_locked(session)
            record = session.exec(
                select(AICostReservation).where(AICostReservation.id == reservation.id).with_for_update()
            ).one()
            bucket = session.exec(
                select(AICostDailyBudget)
                .where(AICostDailyBudget.user_id == record.user_id, AICostDailyBudget.day == record.day)
                .with_for_update()
            ).one()
            if record.settled_units is not None:
                return
            # A provider violating the upper bound must never refund overspend.
            bucket.charged_units += actual - record.reserved_units
            record.settled_units = actual
            session.add(bucket)
            session.add(record)
            session.commit()
    except Exception:
        # Reservation remains fully charged if reconciliation cannot persist.
        from utils.logger import get_logger, log_with_context

        log_with_context(
            get_logger(__name__), 40, "AI cost reservation settlement failed", reservation_id=reservation.id
        )
