"""PostgreSQL concurrency & durability proof for the ¥15 free daily cost backstop.

Verifies the real PostgreSQL ``INSERT ... ON CONFLICT (user_id, day) DO NOTHING``
+ ``SELECT ... FOR UPDATE`` locking path in ``services/usage/cost_budget.py``:
- Concurrent reservations across independent connections cannot exceed the ¥15 cap.
- Concurrent cold-start initialization seeds historical ``llm_usage_event`` rows once.
- Exact settlement (``settle_model_call``) and cancelled-stream settlement
  (``settle_cancelled_stream_call``) reconcile headroom idempotently under row locks.
- Exhaustion returns 402 ``ERR_QUOTA_AI_DAILY_COST_EXCEEDED`` without leaking the ¥15
  cap, percentage, or any internal cost/budget wording.
- Beijing calendar-day scoping isolates consecutive days across 16:00 UTC midnight.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

import database
from core.error_codes import get_error_message
from core.error_handler import APIException
from models import LLMUsageEvent, SubscriptionPlan, User, UserSubscription
from models.ai_cost_budget import AICostDailyBudget, AICostReservation
from services.usage import cost_budget as budget

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
    reason="explicitly owned disposable PostgreSQL URL required",
)

NOW = datetime(2026, 10, 7, 2, 0, 0, tzinfo=UTC)  # 2026-10-07 10:00 Beijing
TABLES = [
    User.__table__,
    SubscriptionPlan.__table__,
    UserSubscription.__table__,
    LLMUsageEvent.__table__,
    AICostDailyBudget.__table__,
    AICostReservation.__table__,
]


def _payload(max_tokens: int = 1_000) -> dict:
    return {
        "model": "deepseek-flash",
        "messages": [{"role": "user", "content": "继续写下一段"}],
        "max_tokens": max_tokens,
    }


def _seed_free_user(engine) -> str:
    suffix = uuid4().hex[:10]
    with Session(engine) as session:
        account = User(
            username=f"cost_pg_{suffix}",
            email=f"cost_pg_{suffix}@example.test",
            hashed_password="unused",
            email_verified=True,
            is_active=True,
        )
        session.add(account)
        session.commit()
        session.refresh(account)
        return account.id


@pytest.fixture
def cost_budget_pg_engine(monkeypatch):
    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert url == os.environ["DATABASE_URL"] and database.is_postgres is True
    engine = create_engine(
        url,
        pool_size=20,
        max_overflow=10,
        connect_args={
            "options": "-c timezone=UTC -c statement_timeout=30000 -c lock_timeout=15000",
        },
    )
    assert engine.dialect.name == "postgresql"
    SQLModel.metadata.create_all(engine, tables=TABLES)
    monkeypatch.setattr(budget, "_session_factory", lambda: Session(engine))
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine, tables=TABLES)
        engine.dispose()


def test_postgres_concurrent_reservations_respect_daily_cap_under_for_update(cost_budget_pg_engine):
    """Concurrent ON CONFLICT + FOR UPDATE reservations across connections never exceed ¥15."""
    user_id = _seed_free_user(cost_budget_pg_engine)
    workers = 12
    barrier = Barrier(workers)

    def _attempt(_index: int):
        barrier.wait(timeout=15)
        try:
            return budget.reserve_model_call(_payload(375_000), user_id=user_id, now=NOW)
        except APIException as exc:
            assert exc.status_code == 402
            assert exc.error_code == budget.COST_LIMIT_CODE
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_attempt, range(workers)))

    succeeded = [res for res in results if res is not None]
    assert len(succeeded) == 4

    with Session(cost_budget_pg_engine) as session:
        buckets = session.exec(
            select(AICostDailyBudget).where(AICostDailyBudget.user_id == user_id)
        ).all()
        assert len(buckets) == 1
        assert buckets[0].charged_units <= budget.FREE_DAILY_BUDGET_UNITS
        reservations = session.exec(
            select(AICostReservation).where(AICostReservation.user_id == user_id)
        ).all()
        assert len(reservations) == 4
        assert sum(r.reserved_units for r in reservations) == buckets[0].charged_units


def test_postgres_concurrent_cold_start_backfills_history_once(cost_budget_pg_engine):
    """Historical LLMUsageEvent backfill initializes the PG row once without double counting."""
    user_id = _seed_free_user(cost_budget_pg_engine)
    # Seed 2,500,000 output tokens on holiday off-peak => 2,500,000 * 400 = 1,000,000,000 units (¥10).
    with Session(cost_budget_pg_engine) as session:
        session.add(
            LLMUsageEvent(
                user_id=user_id,
                source="agent",
                model="deepseek-flash",
                occurred_at=NOW.replace(tzinfo=None),
                price_band="peak",
                pricing_version="deepseek-flash-2026-10",
                output_tokens=2_500_000,
            )
        )
        session.commit()

    workers = 6
    barrier = Barrier(workers)

    def _attempt(_index: int):
        barrier.wait(timeout=15)
        try:
            # Each reservation asks for ~3.0+ CNY peak upper bound, so at most 1 fits in remaining ¥5.
            return budget.reserve_model_call(_payload(375_000), user_id=user_id, now=NOW)
        except APIException as exc:
            assert exc.status_code == 402
            assert exc.error_code == budget.COST_LIMIT_CODE
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_attempt, range(workers)))

    succeeded = [res for res in results if res is not None]
    assert len(succeeded) == 1

    with Session(cost_budget_pg_engine) as session:
        bucket = session.exec(
            select(AICostDailyBudget).where(AICostDailyBudget.user_id == user_id)
        ).one()
        reservation = session.exec(
            select(AICostReservation).where(AICostReservation.user_id == user_id)
        ).one()
        assert bucket.charged_units == 1_000_000_000 + reservation.reserved_units
        assert bucket.charged_units <= budget.FREE_DAILY_BUDGET_UNITS


def test_postgres_settlement_and_cancelled_stream_reconcile_idempotently(cost_budget_pg_engine):
    """Exact and cancelled-stream settlements release unused headroom exactly once."""
    user_id = _seed_free_user(cost_budget_pg_engine)

    # Reserve nearly the whole day's budget, then reconcile to tiny actual usage.
    first = budget.reserve_model_call(_payload(1_500_000), user_id=user_id, now=NOW)
    assert first is not None
    budget.settle_model_call(
        first,
        {
            "prompt_tokens": 200,
            "prompt_cache_hit_tokens": 100,
            "prompt_cache_miss_tokens": 100,
            "completion_tokens": 50,
        },
    )
    # Second settlement call must be a strict no-op.
    budget.settle_model_call(first, {"prompt_tokens": 999_999, "completion_tokens": 999_999})

    with Session(cost_budget_pg_engine) as session:
        bucket_after_exact = session.exec(
            select(AICostDailyBudget).where(AICostDailyBudget.user_id == user_id)
        ).one()
        first_row = session.get(AICostReservation, first.id)
        assert first_row is not None
        assert first_row.settled_units is not None
        assert bucket_after_exact.charged_units == first_row.settled_units
        assert bucket_after_exact.charged_units < 1_000_000  # Headroom released.

    # Headroom is free again: reserve another large call and settle via cancelled-stream path.
    second = budget.reserve_model_call(_payload(1_500_000), user_id=user_id, now=NOW)
    assert second is not None
    with Session(cost_budget_pg_engine) as session:
        before_cancel_charged = session.exec(
            select(AICostDailyBudget).where(AICostDailyBudget.user_id == user_id)
        ).one().charged_units

    budget.settle_cancelled_stream_call(second, streamed_output_bytes=512)
    # Repeat cancelled settlement must be idempotent.
    with Session(cost_budget_pg_engine) as session:
        after_first_cancel = session.exec(
            select(AICostDailyBudget).where(AICostDailyBudget.user_id == user_id)
        ).one().charged_units
    budget.settle_cancelled_stream_call(second, streamed_output_bytes=0)

    with Session(cost_budget_pg_engine) as session:
        after_second_cancel = session.exec(
            select(AICostDailyBudget).where(AICostDailyBudget.user_id == user_id)
        ).one().charged_units
        second_row = session.get(AICostReservation, second.id)
        assert second_row is not None
        assert second_row.settled_units is not None
        assert after_first_cancel < before_cancel_charged
        assert after_second_cancel == after_first_cancel


def test_postgres_beijing_calendar_day_scoping_and_exhaustion_copy_hides_cap(cost_budget_pg_engine):
    """Beijing midnight splits daily buckets cleanly and 402 refusal never exposes ¥15/cost details."""
    user_id = _seed_free_user(cost_budget_pg_engine)
    before_midnight_utc = datetime(2026, 10, 7, 15, 59, 59, tzinfo=UTC)  # 2026-10-07 23:59:59 CST
    after_midnight_utc = before_midnight_utc + timedelta(seconds=2)      # 2026-10-08 00:00:01 CST

    # Exhaust 2026-10-07 via a reservation near the cap, then confirm refusal on same Beijing day.
    r1 = budget.reserve_model_call(_payload(1_500_000), user_id=user_id, now=before_midnight_utc)
    assert r1 is not None

    with pytest.raises(APIException) as exc_info:
        budget.reserve_model_call(_payload(1_500_000), user_id=user_id, now=before_midnight_utc)

    assert exc_info.value.status_code == 402
    assert exc_info.value.error_code == budget.COST_LIMIT_CODE
    for lang in ("zh", "en"):
        user_msg = get_error_message(exc_info.value.error_code, lang).lower()
        for forbidden in ("15", "¥", "cny", "rmb", "budget", "cost", "%", "元"):
            assert forbidden not in user_msg, f"Leaked internal cap token {forbidden!r} in {lang}: {user_msg}"

    # Next Beijing calendar day gets a fresh bucket immediately.
    r2 = budget.reserve_model_call(_payload(100_000), user_id=user_id, now=after_midnight_utc)
    assert r2 is not None

    # Settling r1 after midnight still reconciles against its start day (2026-10-07).
    budget.settle_model_call(r1, {"prompt_tokens": 0, "completion_tokens": 100})
    budget.settle_model_call(r2, {"prompt_tokens": 0, "completion_tokens": 200})

    with Session(cost_budget_pg_engine) as session:
        rows = session.exec(
            select(AICostDailyBudget)
            .where(AICostDailyBudget.user_id == user_id)
            .order_by(AICostDailyBudget.day)
        ).all()
        assert [str(row.day) for row in rows] == ["2026-10-07", "2026-10-08"]
        assert rows[0].charged_units == 40_000
        assert rows[1].charged_units == 80_000
