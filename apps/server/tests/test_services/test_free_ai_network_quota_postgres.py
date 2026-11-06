"""真实 PostgreSQL 并发：只在明确指定的一次性数据库上运行。"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

import database
from config.datetime_utils import beijing_date
from models import User
from models.free_ai_network_quota import FreeAINetworkReservation
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.quota_service import quota_service
from services.usage.free_ai_network_quota import FreeNetworkQuotaExceeded, claim_network_share
from tests.test_services.test_free_ai_network_quota import (  # noqa: F401 - autouse clock fixture
    IP,
    _account,
    _frozen_clock,
    _used,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="explicitly owned disposable PostgreSQL URL required",
)
TABLES = [
    User.__table__, SubscriptionPlan.__table__, UserSubscription.__table__,
    UsageQuota.__table__, FreeAINetworkReservation.__table__,
]


@pytest.fixture
def engine():
    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert url == os.environ["DATABASE_URL"] and database.is_postgres is True
    engine = create_engine(
        url,
        pool_size=30,
        connect_args={"options": "-c timezone=UTC -c statement_timeout=60000 -c lock_timeout=45000"},
    )
    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine, tables=TABLES)
        engine.dispose()


def test_claim_waits_for_an_uncommitted_claim_on_the_same_network(engine):
    """确定性重现竞争：前一笔还没提交时，后一笔必须等它，再按提交后的结果判断。"""
    with Session(engine) as session:
        seated = [_account(session) for _ in range(2)]
        last_seat, late = _account(session), _account(session)
        for account in seated:
            quota_service.reserve_ai_conversation(session, account, client_ip=IP, reservation_id=uuid4().hex)

    with Session(engine) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        claim_network_share(
            holder, reservation_id=uuid4().hex, user_id=last_seat, client_ip=IP,
            day=beijing_date(datetime(2026, 11, 5, 4, tzinfo=UTC)),
        )

        def reserve_late():
            with Session(engine) as session:
                return quota_service.reserve_ai_conversation(
                    session, late, client_ip=IP, reservation_id=uuid4().hex
                )

        future = pool.submit(reserve_late)
        time.sleep(0.5)
        assert not future.done(), "the second claim did not wait for the first one"
        holder.commit()
        with pytest.raises(FreeNetworkQuotaExceeded):
            future.result(timeout=20)
    with Session(engine) as session:
        assert _used(session, late) == 0


def test_reservation_does_not_wait_on_a_locked_user_row(engine):
    """别的请求锁着 user 行（例如验证码流程）时，预扣不能等它，否则网络锁也会被一起占住。"""
    with Session(engine) as session:
        account = _account(session)
    with Session(engine) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.exec(select(User).where(User.id == account).with_for_update()).one()

        def reserve():
            with Session(engine) as session:
                return quota_service.reserve_ai_conversation(
                    session, account, client_ip=IP, reservation_id=uuid4().hex
                )

        assert pool.submit(reserve).result(timeout=10) is not None
        holder.rollback()


def test_concurrent_accounts_get_exactly_nine_messages_on_three_seats(engine):
    with Session(engine) as session:
        accounts = [_account(session) for _ in range(6)]
    attempts = [account for account in accounts for _ in range(4)]
    # 连接先建好，避免建连耗时把线程错开，让竞争窗口真的重叠。
    warm = [engine.connect() for _ in attempts]
    for connection in warm:
        connection.close()
    barrier = Barrier(len(attempts))

    def attempt(account: str) -> tuple[str, str]:
        with Session(engine) as session:
            barrier.wait(timeout=20)
            try:
                period = quota_service.reserve_ai_conversation(
                    session, account, client_ip=IP, reservation_id=uuid4().hex
                )
            except FreeNetworkQuotaExceeded:
                return account, "network"
            return account, "ok" if period else "account"

    with ThreadPoolExecutor(max_workers=len(attempts)) as pool:
        results = list(pool.map(attempt, attempts))

    winners = [account for account, outcome in results if outcome == "ok"]
    assert len(winners) == 9
    assert len(set(winners)) == 3
    with Session(engine) as session:
        for account in accounts:
            assert _used(session, account) == winners.count(account)
        rows = session.exec(select(FreeAINetworkReservation)).all()
        assert len(rows) == 9
        assert {row.user_id for row in rows} == set(winners)


def test_concurrent_refunds_of_one_reservation_return_one_unit(engine):
    with Session(engine) as session:
        account = _account(session)
        reservation_id = uuid4().hex
        period = quota_service.reserve_ai_conversation(
            session, account, client_ip=IP, reservation_id=reservation_id
        )
        quota_service.reserve_ai_conversation(session, account, client_ip=IP, reservation_id=uuid4().hex)
    barrier = Barrier(6)

    def refund(_):
        with Session(engine) as session:
            barrier.wait(timeout=20)
            return quota_service.release_ai_conversation(
                session, account, period_start=period, reservation_id=reservation_id
            )

    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(refund, range(6)))
    assert outcomes.count(True) == 1
    with Session(engine) as session:
        assert _used(session, account) == 1


def test_interleaved_reserve_and_refund_do_not_deadlock_or_drift(engine):
    """同账号和同网络上的预扣、退还交错执行：不死锁，计数与预约记录一致。"""
    with Session(engine) as session:
        accounts = [_account(session) for _ in range(3)]
    barrier = Barrier(18)

    def cycle(account: str) -> None:
        with Session(engine) as session:
            barrier.wait(timeout=20)
            reservation_id = uuid4().hex
            try:
                period = quota_service.reserve_ai_conversation(
                    session, account, client_ip=IP, reservation_id=reservation_id
                )
            except FreeNetworkQuotaExceeded:
                return
            if period is not None:
                assert quota_service.release_ai_conversation(
                    session, account, period_start=period, reservation_id=reservation_id
                ) is True

    with ThreadPoolExecutor(max_workers=18) as pool:
        list(pool.map(cycle, [account for account in accounts for _ in range(6)]))
    with Session(engine) as session:
        for account in accounts:
            assert _used(session, account) == 0
        active = session.exec(
            select(FreeAINetworkReservation).where(FreeAINetworkReservation.released_at.is_(None))
        ).all()
        assert active == []
