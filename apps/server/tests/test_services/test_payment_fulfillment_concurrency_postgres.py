"""Owned PostgreSQL proof of FOR UPDATE + populate_existing + CAS in payment_fulfillment."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel, select

from models import SubscriptionPlan, User
from models.payment import PaymentOrder
from models.subscription import UserSubscription
from services.subscription.payment_fulfillment import (
    record_dispute,
    record_refund,
    settle_paid_order,
)
from services.subscription.subscription_service import subscription_service

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
    reason="owned PostgreSQL URL required",
)


@pytest.fixture(scope="module")
def owned_engine():
    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert url == os.environ["DATABASE_URL"]
    engine = create_engine(
        url,
        connect_args={"options": "-c timezone=UTC -c lock_timeout=5000 -c statement_timeout=15000"},
    )
    assert engine.dialect.name == "postgresql"
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        if not session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == "pro")).first():
            session.add(
                SubscriptionPlan(
                    name="pro",
                    display_name="专业版",
                    display_name_en="Pro",
                    price_monthly_cents=4900,
                    price_yearly_cents=39900,
                    price_monthly_usd_cents=999,
                    price_yearly_usd_cents=7900,
                    features={"ai_conversations_per_day": -1, "max_projects": -1},
                    is_active=True,
                )
            )
            session.commit()
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine)
        engine.dispose()


def _seed_order(
    engine,
    *,
    status: str = "paid",
    fulfillment_status: str = "succeeded",
    amount_cents: int = 999,
    grant_pro: bool = True,
) -> tuple[str, str]:
    suffix = uuid4().hex[:12]
    with Session(engine) as session:
        user = User(
            username=f"pg_pay_{suffix}",
            email=f"pg_pay_{suffix}@example.test",
            hashed_password="unused",
            email_verified=True,
            is_active=True,
        )
        session.add(user)
        session.flush()
        order = PaymentOrder(
            provider="creem",
            currency="USD",
            out_trade_no=f"20261124{suffix}0001"[:32],
            trade_no=f"ord_pg_{suffix}",
            provider_checkout_id=f"ch_pg_{suffix}",
            user_id=user.id,
            plan_name="pro",
            plan_display_name="专业版",
            product_name="ZenStory Pro (30 Days)",
            cycle="month",
            amount_cents=amount_cents,
            paid_amount_cents=amount_cents if status == "paid" else None,
            duration_days=30,
            payment_method="card",
            status=status,
            fulfillment_status=fulfillment_status,
        )
        session.add(order)
        if grant_pro:
            subscription_service.create_user_subscription(
                session=session,
                user_id=user.id,
                plan_name="pro",
                duration_days=30,
                metadata={"source": "creem", "payment_order_id": order.id},
                commit=False,
            )
        session.commit()
        return user.id, order.id


def _wait_for_lock(engine, pid: int, timeout: float = 3.0) -> bool:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        with engine.connect() as observer:
            wait_type = observer.execute(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            ).scalar()
        if wait_type == "Lock":
            return True
        time.sleep(0.01)
    return False


def test_refund_waits_for_concurrent_settle_and_reads_fresh_succeeded_state(
    owned_engine, monkeypatch
):
    """
    Reproduces HIGH-1(a): session B loads stale order (processing) into its
    identity map before calling record_refund while session A finishes settling
    and granting Pro. FOR UPDATE + populate_existing must see 'succeeded' and
    revoke Pro.
    """
    user_id, order_id = _seed_order(
        owned_engine, status="pending", fulfillment_status="pending", grant_pro=False
    )
    with Session(owned_engine) as session:
        order = session.get(PaymentOrder, order_id)
        assert order is not None
        out_trade_no = order.out_trade_no
        trade_no = order.trade_no or "ord_pg_race"

    claim_locked = threading.Event()
    release_settle = threading.Event()
    refund_pid: list[int] = []
    refund_started = threading.Event()
    orig_create_sub = subscription_service.create_user_subscription

    def slow_create_sub(*args, **kwargs):
        result = orig_create_sub(*args, **kwargs)
        claim_locked.set()
        assert release_settle.wait(5)
        return result

    monkeypatch.setattr(subscription_service, "create_user_subscription", slow_create_sub)

    def run_settle():
        with Session(owned_engine) as s:
            return settle_paid_order(
                s,
                provider="creem",
                out_trade_no=out_trade_no,
                trade_no=trade_no,
                amount_cents=999,
                currency="USD",
                payment_method="card",
                product_name=None,
                channel="webhook",
            )

    def run_refund():
        with Session(owned_engine) as s:
            # Populate identity map BEFORE acquiring FOR UPDATE lock (matches _find_creem_order).
            stale_order = s.exec(select(PaymentOrder).where(PaymentOrder.id == order_id)).one()
            refund_pid.append(s.exec(text("SELECT pg_backend_pid()")).scalar_one())
            refund_started.set()
            return record_refund(
                s,
                order=stale_order,
                refund_id="ref_pg_race_1",
                refund_amount_cents=999,
                channel="webhook",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_settle = pool.submit(run_settle)
        assert claim_locked.wait(5)
        f_refund = pool.submit(run_refund)
        assert refund_started.wait(5)
        assert _wait_for_lock(owned_engine, refund_pid[0])
        release_settle.set()
        assert f_settle.result(timeout=10) is True
        assert f_refund.result(timeout=10) is True

    with Session(owned_engine) as s:
        refreshed = s.get(PaymentOrder, order_id)
        assert refreshed is not None
        assert refreshed.status == "refunded"
        assert refreshed.fulfillment_status == "succeeded"
        assert refreshed.refunded_at is not None
        sub = s.exec(select(UserSubscription).where(UserSubscription.user_id == user_id)).one()
        assert sub.status == "cancelled"
        assert subscription_service.get_effective_paid_plan(s, user_id) is None


def test_concurrent_partial_refunds_accumulate_without_lost_update(owned_engine):
    """
    Reproduces HIGH-1(b): two sessions both pre-load refunded_amount_cents=0 into
    their identity maps and apply partial refunds (400 + 599 = 999) concurrently.
    FOR UPDATE + populate_existing + CAS must accumulate both and revoke Pro.
    """
    user_id, order_id = _seed_order(owned_engine, amount_cents=999, grant_pro=True)
    both_loaded = threading.Barrier(2)

    def run_partial(refund_id: str, amount: int):
        with Session(owned_engine) as s:
            stale = s.exec(select(PaymentOrder).where(PaymentOrder.id == order_id)).one()
            assert stale.refunded_amount_cents == 0
            both_loaded.wait(timeout=5)
            return record_refund(
                s,
                order=stale,
                refund_id=refund_id,
                refund_amount_cents=amount,
                channel="webhook",
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(run_partial, "ref_pg_p1", 400)
        f2 = pool.submit(run_partial, "ref_pg_p2", 599)
        assert f1.result(timeout=10) is True
        assert f2.result(timeout=10) is True

    with Session(owned_engine) as s:
        refreshed = s.get(PaymentOrder, order_id)
        assert refreshed is not None
        assert refreshed.status == "refunded"
        assert refreshed.refunded_at is not None
        assert refreshed.refunded_amount_cents == 999
        assert set((refreshed.refund_ids or "").split(",")) == {"ref_pg_p1", "ref_pg_p2"}
        assert subscription_service.get_effective_paid_plan(s, user_id) is None


def test_dispute_waits_for_concurrent_refund_and_preserves_refunded_status(
    owned_engine, monkeypatch
):
    """
    Reproduces HIGH-1(c): dispute session pre-loads stale status='paid' while
    refund session holds row lock and commits status='refunded'. FOR UPDATE +
    populate_existing must keep status='refunded' and set disputed_at.
    """
    _user_id, order_id = _seed_order(owned_engine, amount_cents=999, grant_pro=True)
    refund_locked = threading.Event()
    release_refund = threading.Event()
    dispute_pid: list[int] = []
    dispute_started = threading.Event()
    orig_deduct = subscription_service.deduct_refunded_days

    def slow_deduct(*args, **kwargs):
        result = orig_deduct(*args, **kwargs)
        refund_locked.set()
        assert release_refund.wait(5)
        return result

    monkeypatch.setattr(subscription_service, "deduct_refunded_days", slow_deduct)

    def run_refund():
        with Session(owned_engine) as s:
            order = s.exec(select(PaymentOrder).where(PaymentOrder.id == order_id)).one()
            return record_refund(
                s,
                order=order,
                refund_id="ref_pg_disp_race",
                refund_amount_cents=999,
                channel="webhook",
            )

    def run_dispute():
        with Session(owned_engine) as s:
            # Pre-load stale status='paid' before acquiring FOR UPDATE lock.
            stale = s.exec(select(PaymentOrder).where(PaymentOrder.id == order_id)).one()
            assert stale.status == "paid"
            dispute_pid.append(s.exec(text("SELECT pg_backend_pid()")).scalar_one())
            dispute_started.set()
            return record_dispute(s, order=stale, dispute_id="disp_pg_race")

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_ref = pool.submit(run_refund)
        assert refund_locked.wait(5)
        f_disp = pool.submit(run_dispute)
        assert dispute_started.wait(5)
        assert _wait_for_lock(owned_engine, dispute_pid[0])
        release_refund.set()
        assert f_ref.result(timeout=10) is True
        assert f_disp.result(timeout=10) is True

    with Session(owned_engine) as s:
        refreshed = s.get(PaymentOrder, order_id)
        assert refreshed is not None
        assert refreshed.status == "refunded"
        assert refreshed.refunded_at is not None
        assert refreshed.disputed_at is not None
        assert refreshed.failure_reason == "disputed:disp_pg_race"
