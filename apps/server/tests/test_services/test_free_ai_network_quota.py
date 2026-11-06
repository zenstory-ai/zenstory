"""免费账号按可信客户端网络共享每日额度：账号与网络在同一事务里扣减，退还按预约幂等。"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel, select

from config.datetime_utils import beijing_date, beijing_day_bounds
from models import User
from models.free_ai_network_quota import FreeAINetworkReservation
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.quota_service import quota_service
from services.usage import free_ai_network_quota as network_quota
from services.usage.free_ai_network_quota import FreeNetworkQuotaExceeded, network_key

NOW = datetime(2026, 11, 5, 4, tzinfo=UTC)
NEXT_DAY = NOW + timedelta(days=1)
IP = "93.184.216.34"
OTHER_IP = "93.184.216.35"


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch):
    monkeypatch.setattr("services.quota_service.utcnow", lambda: NOW)
    monkeypatch.setattr(network_quota, "utcnow", lambda: NOW)
    monkeypatch.setattr(network_quota, "_last_purge_day", None)


def _free_plan(session: Session) -> SubscriptionPlan:
    plan = session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == "free")).first()
    if plan is None:
        plan = SubscriptionPlan(
            name="free", display_name="Free", display_name_en="Free",
            price_monthly_cents=0, price_yearly_cents=0,
            features={"ai_conversations_per_day": 3},
        )
        session.add(plan)
        session.commit()
    return plan


def _account(session: Session, *, pro: bool = False) -> str:
    _free_plan(session)
    suffix = uuid4().hex[:10]
    user = User(
        username=f"network-{suffix}", email=f"network-{suffix}@example.com",
        hashed_password="synthetic", email_verified=True,
    )
    session.add(user)
    session.commit()
    period_start, period_end = beijing_day_bounds(NOW)
    session.add(UsageQuota(
        user_id=user.id, period_start=period_start, period_end=period_end,
        last_reset_at=NOW, ai_conversations_used=0,
        monthly_period_start=period_start, monthly_period_end=period_end + timedelta(days=28),
    ))
    if pro:
        plan = session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == "pro")).first()
        if plan is None:
            plan = SubscriptionPlan(
                name="pro", display_name="Pro", display_name_en="Pro",
                price_monthly_cents=4900, price_yearly_cents=39900,
                features={"ai_conversations_per_day": -1},
            )
            session.add(plan)
            session.commit()
        session.add(UserSubscription(
            user_id=user.id, plan_id=plan.id, status="active",
            current_period_start=NOW - timedelta(days=1), current_period_end=NOW + timedelta(days=30),
        ))
    session.commit()
    return user.id


def _reserve(session: Session, user_id: str, ip: str | None = IP) -> tuple[datetime | None, str]:
    reservation_id = uuid4().hex
    period = quota_service.reserve_ai_conversation(
        session, user_id, client_ip=ip, reservation_id=reservation_id
    )
    return period, reservation_id


def _used(session: Session, user_id: str) -> int:
    session.expire_all()
    return session.exec(select(UsageQuota).where(UsageQuota.user_id == user_id)).one().ai_conversations_used


def _active_rows(session: Session) -> list[FreeAINetworkReservation]:
    session.expire_all()
    return list(session.exec(
        select(FreeAINetworkReservation).where(FreeAINetworkReservation.released_at.is_(None))
    ).all())


def _fill_three_accounts(session: Session) -> list[str]:
    accounts = [_account(session) for _ in range(3)]
    for account in accounts:
        assert _reserve(session, account)[0] is not None
    return accounts


# ---- 3 个账号名额 ----


def test_fourth_account_is_refused_and_nothing_is_charged(db_session):
    _fill_three_accounts(db_session)
    fourth = _account(db_session)
    with pytest.raises(FreeNetworkQuotaExceeded) as refused:
        _reserve(db_session, fourth)
    assert refused.value.reason == "accounts"
    assert _used(db_session, fourth) == 0
    assert all(row.user_id != fourth for row in _active_rows(db_session))


def test_seated_accounts_keep_working_within_the_total_when_seats_are_gone(db_session):
    first, second, third = _fill_three_accounts(db_session)
    with pytest.raises(FreeNetworkQuotaExceeded):
        _reserve(db_session, _account(db_session))
    for account in (first, second, third):
        assert _reserve(db_session, account)[0] is not None
        assert _reserve(db_session, account)[0] is not None
    assert len(_active_rows(db_session)) == 9


def test_fourth_account_can_still_use_ai_from_another_network(db_session):
    _fill_three_accounts(db_session)
    fourth = _account(db_session)
    assert _reserve(db_session, fourth, OTHER_IP)[0] is not None
    assert _used(db_session, fourth) == 1


# ---- 9 条合计 ----


def test_network_total_stops_at_nine_even_when_an_account_counter_has_room(db_session):
    """账号计数被后台调整后仍有余量时，网络合计是独立的上限，第 10 条不放行。"""
    accounts = _fill_three_accounts(db_session)
    for account in accounts:
        _reserve(db_session, account)
        _reserve(db_session, account)
    assert len(_active_rows(db_session)) == 9

    quota = db_session.exec(select(UsageQuota).where(UsageQuota.user_id == accounts[0])).one()
    quota.ai_conversations_used = 0
    db_session.commit()

    with pytest.raises(FreeNetworkQuotaExceeded) as refused:
        _reserve(db_session, accounts[0])
    assert refused.value.reason == "messages"
    assert _used(db_session, accounts[0]) == 0


# ---- 账号自身 3 条 ----


def test_account_daily_three_still_applies_and_takes_no_network_share(db_session):
    account = _account(db_session)
    for _ in range(3):
        assert _reserve(db_session, account)[0] is not None
    assert _reserve(db_session, account)[0] is None
    assert _used(db_session, account) == 3
    assert len(_active_rows(db_session)) == 3


def test_exhausted_account_reports_its_own_limit_on_a_full_network(db_session):
    """账号已用完时走标准额度提示，不提网络。"""
    accounts = _fill_three_accounts(db_session)
    for account in accounts:
        _reserve(db_session, account)
        _reserve(db_session, account)
    assert _reserve(db_session, accounts[0])[0] is None


# ---- Pro ----


def test_pro_is_exempt_and_leaves_no_trace_on_the_network(db_session):
    accounts = _fill_three_accounts(db_session)
    for account in accounts:
        _reserve(db_session, account)
        _reserve(db_session, account)
    before = len(_active_rows(db_session))
    pro = _account(db_session, pro=True)
    for _ in range(12):
        assert _reserve(db_session, pro)[0] is not None
    assert len(_active_rows(db_session)) == before


def test_lapsed_pro_is_counted_as_free(db_session):
    """沿用既有判定：过期的 Pro 回到免费，受网络规则约束。"""
    _fill_three_accounts(db_session)
    lapsed = _account(db_session, pro=True)
    subscription = db_session.exec(select(UserSubscription).where(UserSubscription.user_id == lapsed)).one()
    subscription.current_period_end = (NOW - timedelta(minutes=1)).replace(tzinfo=None)
    db_session.commit()
    db_session.expire_all()
    with pytest.raises(FreeNetworkQuotaExceeded):
        _reserve(db_session, lapsed)


# ---- 退还 ----


def test_refund_returns_both_counts_exactly_once(db_session):
    account = _account(db_session)
    period, reservation_id = _reserve(db_session, account)
    assert _used(db_session, account) == 1

    assert quota_service.release_ai_conversation(
        db_session, account, period_start=period, reservation_id=reservation_id
    ) is True
    assert _used(db_session, account) == 0
    assert _active_rows(db_session) == []

    # 重复结算：不多退，也不把账号计数减成别的消息的。
    _reserve(db_session, account)
    assert quota_service.release_ai_conversation(
        db_session, account, period_start=period, reservation_id=reservation_id
    ) is False
    assert _used(db_session, account) == 1


def test_refund_of_an_accounts_only_message_gives_its_seat_back(db_session):
    first, *_ = _fill_three_accounts(db_session)
    first_row = next(row for row in _active_rows(db_session) if row.user_id == first)
    period = beijing_day_bounds(NOW)[0]
    assert quota_service.release_ai_conversation(
        db_session, first, period_start=period, reservation_id=first_row.id
    ) is True

    newcomer = _account(db_session)
    assert _reserve(db_session, newcomer)[0] is not None


def test_refund_keeps_the_seat_while_the_account_has_other_messages(db_session):
    first, *_ = _fill_three_accounts(db_session)
    period, reservation_id = _reserve(db_session, first)
    assert quota_service.release_ai_conversation(
        db_session, first, period_start=period, reservation_id=reservation_id
    ) is True
    with pytest.raises(FreeNetworkQuotaExceeded):
        _reserve(db_session, _account(db_session))


def test_another_account_cannot_release_a_reservation(db_session):
    owner = _account(db_session)
    intruder = _account(db_session)
    period, reservation_id = _reserve(db_session, owner)
    _reserve(db_session, intruder)

    assert quota_service.release_ai_conversation(
        db_session, intruder, period_start=period, reservation_id=reservation_id
    ) is False
    assert _used(db_session, owner) == 1
    assert _used(db_session, intruder) == 1
    assert len(_active_rows(db_session)) == 2


def test_refused_reservation_leaves_nothing_to_refund(db_session):
    _fill_three_accounts(db_session)
    fourth = _account(db_session)
    reservation_id = uuid4().hex
    with pytest.raises(FreeNetworkQuotaExceeded):
        quota_service.reserve_ai_conversation(db_session, fourth, client_ip=IP, reservation_id=reservation_id)
    assert db_session.get(FreeAINetworkReservation, reservation_id) is None


def test_interrupted_run_keeps_both_charges_until_the_day_ends(db_session, monkeypatch):
    """进程被杀、没有走到结算：与账号额度相同，当天不自动退，跨天一起归零。"""
    _fill_three_accounts(db_session)
    fourth = _account(db_session)
    with pytest.raises(FreeNetworkQuotaExceeded):
        _reserve(db_session, fourth)

    monkeypatch.setattr("services.quota_service.utcnow", lambda: NEXT_DAY)
    monkeypatch.setattr(network_quota, "utcnow", lambda: NEXT_DAY)
    assert _reserve(db_session, fourth)[0] is not None


# ---- 北京时间跨日 ----


def test_new_beijing_day_opens_new_messages_and_seats(db_session, monkeypatch):
    accounts = _fill_three_accounts(db_session)
    late_evening = datetime(2026, 11, 5, 15, 59, 59, tzinfo=UTC)
    after_midnight = datetime(2026, 11, 5, 16, 0, 0, tzinfo=UTC)
    monkeypatch.setattr("services.quota_service.utcnow", lambda: late_evening)
    monkeypatch.setattr(network_quota, "utcnow", lambda: late_evening)
    with pytest.raises(FreeNetworkQuotaExceeded):
        _reserve(db_session, _account(db_session))

    monkeypatch.setattr("services.quota_service.utcnow", lambda: after_midnight)
    monkeypatch.setattr(network_quota, "utcnow", lambda: after_midnight)
    newcomer = _account(db_session)
    assert _reserve(db_session, newcomer)[0] is not None
    assert _reserve(db_session, accounts[0])[0] is not None
    assert _used(db_session, accounts[0]) == 1


def test_late_refund_after_midnight_touches_neither_new_day_count(db_session, monkeypatch):
    account = _account(db_session)
    old_period, old_reservation = _reserve(db_session, account)

    monkeypatch.setattr("services.quota_service.utcnow", lambda: NEXT_DAY)
    monkeypatch.setattr(network_quota, "utcnow", lambda: NEXT_DAY)
    new_period, _new_reservation = _reserve(db_session, account)
    assert new_period != old_period

    assert quota_service.release_ai_conversation(
        db_session, account, period_start=old_period, reservation_id=old_reservation
    ) is False
    assert _used(db_session, account) == 1
    new_day = beijing_date(NEXT_DAY)
    active_today = [row for row in _active_rows(db_session) if row.day == new_day]
    assert len(active_today) == 1
    # 同一笔再结算一次仍然什么都不做。
    assert quota_service.release_ai_conversation(
        db_session, account, period_start=old_period, reservation_id=old_reservation
    ) is False
    assert _used(db_session, account) == 1


def test_old_days_are_purged_without_touching_recent_reservations(db_session, monkeypatch):
    account = _account(db_session)
    _reserve(db_session, account)
    later = NOW + timedelta(days=network_quota.RESERVATION_RETENTION_DAYS + 1)
    monkeypatch.setattr("services.quota_service.utcnow", lambda: later)
    monkeypatch.setattr(network_quota, "utcnow", lambda: later)
    _reserve(db_session, account)
    db_session.expire_all()
    days = {row.day for row in db_session.exec(select(FreeAINetworkReservation)).all()}
    assert days == {beijing_date(later)}


# ---- 客户端网络标识 ----


def test_addresses_of_one_network_share_a_key():
    assert network_key("2606:4700:1::1") == network_key("2606:4700:1:0:ffff::2")
    assert network_key("2606:4700:1::1") != network_key("2606:4700:2::1")
    assert network_key("::ffff:93.184.216.34") == network_key(IP)
    assert network_key(IP) != network_key(OTHER_IP)


@pytest.mark.parametrize("value", [None, "", "unknown", "not-an-ip", "127.0.0.1", "10.1.2.3", "192.168.0.8", "::1", "fd00::1", "169.254.1.1"])
def test_values_that_do_not_identify_a_client_network_have_no_key(value):
    assert network_key(value) is None


def test_stored_key_is_not_the_address(db_session):
    account = _account(db_session)
    _reserve(db_session, account)
    row = _active_rows(db_session)[0]
    assert IP not in row.ip_key
    assert row.ip_key == network_key(IP)


@pytest.mark.parametrize("ip", ["unknown", "10.0.0.1", None])
def test_missing_ip_keeps_the_account_limit_logs_and_refunds_once(db_session, caplog, ip):
    """拿不到客户端网络时不把所有人并进一个桶：只按账号限制，记 WARNING，退还仍然幂等。"""
    account = _account(db_session)
    with caplog.at_level(logging.WARNING):
        period, reservation_id = _reserve(db_session, account, ip)
    assert period is not None
    assert "free_ai_network_limit_skipped" in caplog.text
    assert _active_rows(db_session)[0].ip_key is None

    for _ in range(2):
        assert _reserve(db_session, account, ip)[0] is not None
    assert _reserve(db_session, account, ip)[0] is None

    assert quota_service.release_ai_conversation(
        db_session, account, period_start=period, reservation_id=reservation_id
    ) is True
    assert quota_service.release_ai_conversation(
        db_session, account, period_start=period, reservation_id=reservation_id
    ) is False
    assert _used(db_session, account) == 2


def test_refusal_is_logged_without_the_raw_address(db_session, caplog):
    _fill_three_accounts(db_session)
    with caplog.at_level(logging.INFO), pytest.raises(FreeNetworkQuotaExceeded):
        _reserve(db_session, _account(db_session))
    assert "free_ai_network_limit_reached" in caplog.text
    assert IP not in caplog.text


# ---- 并发（SQLite 文件库；PostgreSQL 见 test_free_ai_network_quota_postgres.py）----


def test_concurrent_accounts_cannot_pass_the_shared_limit(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'network.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        accounts = [_account(session) for _ in range(5)]
    attempts = [account for account in accounts for _ in range(4)]
    barrier = Barrier(len(attempts))

    def attempt(account: str) -> tuple[str, str]:
        with Session(engine) as session:
            barrier.wait(timeout=20)
            try:
                period, _ = _reserve(session, account)
            except FreeNetworkQuotaExceeded:
                return account, "network"
            return account, "ok" if period else "account"

    try:
        with ThreadPoolExecutor(max_workers=len(attempts)) as pool:
            results = list(pool.map(attempt, attempts))
        winners = [account for account, outcome in results if outcome == "ok"]
        assert len(winners) == 9
        assert len(set(winners)) == 3
        with Session(engine) as session:
            for account in accounts:
                assert _used(session, account) == winners.count(account)
    finally:
        engine.dispose()
