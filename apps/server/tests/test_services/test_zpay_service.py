"""Focused Zpay signing and transactional fulfillment tests."""

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier, Event, Lock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import Session, SQLModel, select

from config.payment_settings import PaymentSettings
from models import User
from models.payment import PaymentOrder
from models.subscription import SubscriptionHistory, SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password
from services.subscription.subscription_service import subscription_service
from services.subscription.zpay_service import (
    CallbackError,
    PaymentSyncError,
    canonical_signing_string,
    sign_params,
    zpay_service,
)

SETTINGS = PaymentSettings(
    enabled=True,
    pid="pid-1",
    key="secret-key",
    notify_url="https://api.example.com/api/v1/payments/zpay/notify",
    return_url="https://app.example.com/dashboard/billing/payment-return",
    cid=None,
)


def test_signing_sorts_unicode_and_ignores_empty_and_signature_fields():
    params = {
        "name": "中文 商品",
        "b": "two",
        "a": "one",
        "empty": "",
        "none": None,
        "sign": "ignored",
        "sign_type": "MD5",
    }
    assert canonical_signing_string(params) == "a=one&b=two&name=中文 商品"
    assert sign_params(params, "key") == "87c6243d0229292e14e90a15cd92547d"


def _seed(engine):
    SQLModel.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, class_=Session, expire_on_commit=False)
    with factory() as session:
        user = User(
            username="concurrent_user",
            email="concurrent@example.com",
            hashed_password=hash_password("password123"),
            email_verified=True,
        )
        plan = SubscriptionPlan(
            name="pro",
            display_name="专业版",
            price_monthly_cents=3000,
            price_yearly_cents=30000,
            features={},
            is_active=True,
        )
        session.add_all([user, plan])
        session.commit()
        order = PaymentOrder(
            out_trade_no="202610051234567890123456789001",
            user_id=user.id,
            plan_name="pro",
            plan_display_name="专业版",
            product_name="ZenStory 专业版 月度会员（30天）",
            cycle="month",
            amount_cents=3000,
            duration_days=30,
            payment_method="alipay",
        )
        session.add(order)
        session.commit()
        return factory, user.id, order


def _params(order):
    params = {
        "pid": SETTINGS.pid,
        "name": order.product_name,
        "money": "30.00",
        "out_trade_no": order.out_trade_no,
        "trade_no": "provider-001",
        "trade_status": "TRADE_SUCCESS",
        "type": "alipay",
        "sign_type": "MD5",
    }
    params["sign"] = sign_params(params, SETTINGS.key)
    return params


@pytest.mark.integration
def test_concurrent_callbacks_grant_exactly_once(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'concurrent.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    factory, user_id, order = _seed(engine)
    params = _params(order)
    barrier = Barrier(2)

    def invoke():
        with factory() as session:
            barrier.wait(timeout=5)
            return zpay_service.fulfill_callback(session, params, SETTINGS)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: invoke(), range(2)))
    assert results == [True, True]

    with factory() as session:
        stored = session.exec(
            select(PaymentOrder).where(PaymentOrder.id == order.id)
        ).one()
        histories = session.exec(
            select(SubscriptionHistory).where(SubscriptionHistory.user_id == user_id)
        ).all()
        subscriptions = session.exec(
            select(UserSubscription).where(UserSubscription.user_id == user_id)
        ).all()
        assert stored.status == "paid"
        assert stored.fulfillment_status == "succeeded"
        assert len(histories) == 1
        assert len(subscriptions) == 1


@pytest.mark.integration
def test_concurrent_distinct_orders_extend_same_user_twice(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'two-orders.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    factory, user_id, first = _seed(engine)
    with factory() as session:
        second = PaymentOrder(
            out_trade_no="202610051234567890123456789002",
            user_id=user_id,
            plan_name="pro",
            plan_display_name="专业版",
            product_name="ZenStory Pro 月度会员（30天）",
            cycle="month",
            amount_cents=3000,
            duration_days=30,
            payment_method="alipay",
        )
        session.add(second)
        session.commit()
    first_params = _params(first)
    second_params = _params(second)
    second_params["trade_no"] = "provider-002"
    second_params["sign"] = sign_params(second_params, SETTINGS.key)
    barrier = Barrier(2)

    def invoke(params):
        with factory() as session:
            barrier.wait(timeout=5)
            return zpay_service.fulfill_callback(session, params, SETTINGS)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, (first_params, second_params)))
    assert results == [True, True]

    with factory() as session:
        histories = session.exec(
            select(SubscriptionHistory).where(SubscriptionHistory.user_id == user_id)
        ).all()
        subscription = session.exec(
            select(UserSubscription).where(UserSubscription.user_id == user_id)
        ).one()
        assert len(histories) == 2
        assert subscription.current_period_end - subscription.current_period_start >= timedelta(
            days=59, hours=23
        )


@pytest.mark.integration
def test_fulfillment_failure_keeps_payment_fact_and_can_retry(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'retry.db'}", connect_args={"check_same_thread": False}
    )
    factory, user_id, order = _seed(engine)
    params = _params(order)
    original = subscription_service.create_user_subscription

    def fail_once(*_args, **_kwargs):
        raise RuntimeError("sensitive internal failure")

    monkeypatch.setattr(subscription_service, "create_user_subscription", fail_once)
    with factory() as session, pytest.raises(CallbackError):
        zpay_service.fulfill_callback(session, params, SETTINGS)
    with factory() as session:
        stored = session.get(PaymentOrder, order.id)
        assert stored is not None
        # Money arrived: the payment fact survives the failed grant.
        assert stored.status == "paid"
        assert stored.trade_no == params["trade_no"]
        assert stored.paid_at is not None
        assert stored.fulfilled_at is None
        assert stored.fulfillment_status == "failed"
        assert stored.failure_reason == "subscription_fulfillment_failed"
        assert not session.exec(
            select(UserSubscription).where(UserSubscription.user_id == user_id)
        ).first()

    monkeypatch.setattr(subscription_service, "create_user_subscription", original)
    with factory() as session:
        assert zpay_service.fulfill_callback(session, params, SETTINGS)
    # Zpay keeps retrying until it reads "success"; later retries must not regrant.
    with factory() as session:
        assert zpay_service.fulfill_callback(session, params, SETTINGS)
    with factory() as session:
        stored = session.get(PaymentOrder, order.id)
        assert stored is not None
        assert stored.status == "paid"
        assert stored.fulfillment_status == "succeeded"
        assert stored.failure_reason is None
        histories = session.exec(
            select(SubscriptionHistory).where(SubscriptionHistory.user_id == user_id)
        ).all()
        assert len(histories) == 1


@pytest.mark.integration
def test_late_failure_record_cannot_overwrite_concurrent_success(
    tmp_path, monkeypatch: pytest.MonkeyPatch
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'failure-race.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    factory, user_id, order = _seed(engine)
    params = _params(order)
    original_create = subscription_service.create_user_subscription
    original_record = zpay_service._record_fulfillment_failure
    failure_ready = Event()
    release_failure = Event()
    call_lock = Lock()
    call_count = 0

    def fail_first(*args, **kwargs):
        nonlocal call_count
        with call_lock:
            call_count += 1
            should_fail = call_count == 1
        if should_fail:
            raise RuntimeError("first attempt fails")
        return original_create(*args, **kwargs)

    def paused_record(session, order_id):
        failure_ready.set()
        assert release_failure.wait(timeout=10)
        return original_record(session, order_id)

    monkeypatch.setattr(subscription_service, "create_user_subscription", fail_first)
    monkeypatch.setattr(zpay_service, "_record_fulfillment_failure", paused_record)

    def first_attempt():
        with factory() as session, pytest.raises(CallbackError):
            zpay_service.fulfill_callback(session, params, SETTINGS)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first_attempt)
        assert failure_ready.wait(timeout=10)
        with factory() as session:
            assert zpay_service.fulfill_callback(session, params, SETTINGS)
        release_failure.set()
        first_future.result(timeout=10)

    with factory() as session:
        stored = session.get(PaymentOrder, order.id)
        histories = session.exec(
            select(SubscriptionHistory).where(SubscriptionHistory.user_id == user_id)
        ).all()
        assert stored is not None
        assert stored.status == "paid"
        assert stored.fulfillment_status == "succeeded"
        assert stored.failure_reason is None
        assert len(histories) == 1


@pytest.mark.integration
def test_provider_trade_number_cannot_fulfill_two_orders(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'trade-number.db'}",
        connect_args={"check_same_thread": False},
    )
    factory, user_id, first = _seed(engine)
    with factory() as session:
        second = PaymentOrder(
            out_trade_no="202610051234567890123456789003",
            user_id=user_id,
            plan_name="pro",
            plan_display_name="专业版",
            product_name="ZenStory Pro 月度会员（30天）",
            cycle="month",
            amount_cents=3000,
            duration_days=30,
            payment_method="alipay",
        )
        session.add(second)
        session.commit()

    with factory() as session:
        assert zpay_service.fulfill_callback(session, _params(first), SETTINGS)
    second_params = _params(second)  # Intentionally reuses provider-001.
    with factory() as session, pytest.raises(CallbackError):
        zpay_service.fulfill_callback(session, second_params, SETTINGS)

    with factory() as session:
        histories = session.exec(
            select(SubscriptionHistory).where(SubscriptionHistory.user_id == user_id)
        ).all()
        stored_second = session.get(PaymentOrder, second.id)
        assert len(histories) == 1
        assert stored_second is not None
        assert stored_second.status == "pending"
        assert stored_second.fulfillment_status == "failed"


# ---------------------------------------------------------------------------
# Order query compensation (act=order)
# ---------------------------------------------------------------------------


def _provider_response(order, **overrides):
    payload = {
        "code": 1,
        "msg": "查询订单号成功！",
        "trade_no": "provider-001",
        "out_trade_no": order.out_trade_no,
        "type": "alipay",
        "pid": SETTINGS.pid,
        "name": order.product_name,
        "money": "30.00",
        "status": 1,
    }
    payload.update(overrides)
    return payload


def _mock_provider(monkeypatch, payload, seen=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if isinstance(payload, Exception):
            raise payload
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(zpay_service, "http_transport", httpx.MockTransport(handler))


def _file_engine(tmp_path, name):
    return create_engine(
        f"sqlite:///{tmp_path / name}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )


@pytest.mark.integration
def test_sync_queries_provider_and_grants_paid_order_once(tmp_path, monkeypatch):
    factory, user_id, order = _seed(_file_engine(tmp_path, "sync.db"))
    with factory() as session:
        stored = session.get(PaymentOrder, order.id)
        stored.upgrade_source = "pricing_page"
        session.add(stored)
        session.commit()
    seen: list[httpx.Request] = []
    _mock_provider(monkeypatch, _provider_response(order), seen)

    with factory() as session:
        stored = session.get(PaymentOrder, order.id)
        assert zpay_service.sync_order(session, stored, SETTINGS) == "fulfilled"

    request = seen[0]
    assert f"{request.url.scheme}://{request.url.host}{request.url.path}" == (
        "https://zpayz.cn/api.php"
    )
    assert dict(request.url.params) == {
        "act": "order",
        "pid": SETTINGS.pid,
        "key": SETTINGS.key,
        "out_trade_no": order.out_trade_no,
    }

    # The notify that arrives afterwards takes the same idempotent path.
    with factory() as session:
        assert zpay_service.fulfill_callback(session, _params(order), SETTINGS)
    with factory() as session:
        stored = session.get(PaymentOrder, order.id)
        assert zpay_service.sync_order(session, stored, SETTINGS) == "already_fulfilled"
    assert len(seen) == 1

    with factory() as session:
        stored = session.get(PaymentOrder, order.id)
        histories = session.exec(
            select(SubscriptionHistory).where(SubscriptionHistory.user_id == user_id)
        ).all()
        assert stored.status == "paid"
        assert stored.trade_no == "provider-001"
        assert stored.fulfillment_status == "succeeded"
        assert len(histories) == 1
        assert histories[0].event_metadata["settled_via"] == "query"
        assert histories[0].event_metadata["upgrade_source"] == "pricing_page"
        assert histories[0].event_metadata["payment_order_id"] == order.id


@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "outcome"),
    [
        ({"status": 0}, "unpaid"),
        ({"code": -1, "msg": "订单不存在"}, "not_found"),
    ],
)
def test_sync_leaves_unpaid_orders_untouched(tmp_path, monkeypatch, overrides, outcome):
    factory, user_id, order = _seed(_file_engine(tmp_path, "sync-unpaid.db"))
    _mock_provider(monkeypatch, _provider_response(order, **overrides))
    with factory() as session:
        stored = session.get(PaymentOrder, order.id)
        assert zpay_service.sync_order(session, stored, SETTINGS) == outcome
    with factory() as session:
        stored = session.get(PaymentOrder, order.id)
        assert stored.status == "pending"
        assert stored.trade_no is None
        assert stored.fulfillment_status == "pending"
        assert not session.exec(
            select(UserSubscription).where(UserSubscription.user_id == user_id)
        ).first()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"money": "0.01"}, "order_mismatch"),
        ({"type": "wxpay"}, "order_mismatch"),
        ({"out_trade_no": "999"}, "order_mismatch"),
        ({"pid": "other-merchant"}, "order_mismatch"),
        ({"trade_no": ""}, "invalid_trade_number"),
    ],
)
def test_sync_rejects_provider_answers_that_do_not_match_the_order(
    tmp_path, monkeypatch, overrides, reason
):
    factory, user_id, order = _seed(_file_engine(tmp_path, "sync-mismatch.db"))
    _mock_provider(monkeypatch, _provider_response(order, **overrides))
    with factory() as session, pytest.raises(PaymentSyncError) as raised:
        zpay_service.sync_order(session, session.get(PaymentOrder, order.id), SETTINGS)
    assert raised.value.reason == reason
    with factory() as session:
        stored = session.get(PaymentOrder, order.id)
        assert stored.status == "pending"
        assert stored.fulfillment_status == "pending"


@pytest.mark.integration
def test_sync_transport_failure_never_exposes_the_merchant_key(
    tmp_path, monkeypatch, caplog
):
    factory, _user_id, order = _seed(_file_engine(tmp_path, "sync-down.db"))
    _mock_provider(
        monkeypatch,
        httpx.ConnectError(f"connect failed for https://zpayz.cn/api.php?key={SETTINGS.key}"),
    )
    with factory() as session, pytest.raises(PaymentSyncError) as raised:
        zpay_service.sync_order(session, session.get(PaymentOrder, order.id), SETTINGS)
    assert raised.value.reason == "provider_unavailable"
    assert raised.value.__cause__ is None
    assert SETTINGS.key not in str(raised.value)
    assert SETTINGS.key not in caplog.text


@pytest.mark.integration
def test_sync_racing_notify_grants_exactly_once(tmp_path, monkeypatch):
    factory, user_id, order = _seed(_file_engine(tmp_path, "sync-race.db"))
    _mock_provider(monkeypatch, _provider_response(order))
    barrier = Barrier(2)

    def notify():
        with factory() as session:
            barrier.wait(timeout=5)
            return zpay_service.fulfill_callback(session, _params(order), SETTINGS)

    def sync():
        with factory() as session:
            stored = session.get(PaymentOrder, order.id)
            barrier.wait(timeout=5)
            try:
                return zpay_service.sync_order(session, stored, SETTINGS)
            except PaymentSyncError as exc:
                # Losing the claim race while the winner commits is reported as
                # "processing"; the order is still granted exactly once below.
                return exc.reason

    with ThreadPoolExecutor(max_workers=2) as pool:
        notify_future = pool.submit(notify)
        sync_future = pool.submit(sync)
        assert notify_future.result(timeout=30) is True
        assert sync_future.result(timeout=30) in {"fulfilled", "already_fulfilled", "processing"}

    with factory() as session:
        histories = session.exec(
            select(SubscriptionHistory).where(SubscriptionHistory.user_id == user_id)
        ).all()
        stored = session.get(PaymentOrder, order.id)
        assert len(histories) == 1
        assert stored.fulfillment_status == "succeeded"


def test_settings_reject_notify_url_on_wrong_path_or_frontend_host():
    base = {
        "enabled": True,
        "pid": "pid",
        "key": "key",
        "return_url": "https://zenstory.ai/dashboard/billing/payment-return",
        "cid": None,
        "frontend_url": "https://zenstory.ai",
    }
    good = PaymentSettings(notify_url="https://api.zenstory.ai/api/v1/payments/zpay/notify", **base)
    assert good.checkout_enabled
    assert good.configuration_problems() == []

    wrong_path = PaymentSettings(notify_url="https://api.zenstory.ai/api/v1/payments/zpay/notify/", **base)
    assert not wrong_path.checkout_enabled
    assert any("path" in problem for problem in wrong_path.configuration_problems())

    frontend_host = PaymentSettings(notify_url="https://zenstory.ai/api/v1/payments/zpay/notify", **base)
    assert not frontend_host.checkout_enabled
    assert any("FRONTEND_URL" in problem for problem in frontend_host.configuration_problems())
    # Existing orders stay fulfillable: callbacks only need credentials.
    assert frontend_host.credentials_configured


def test_enabled_but_misconfigured_checkout_logs_error(caplog):
    broken = PaymentSettings(
        enabled=True, pid="pid", key="", notify_url="", return_url="", cid=None
    )
    with caplog.at_level(logging.ERROR):
        zpay_service.log_configuration_status(broken)
    records = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert records
    problems = records[0].custom_fields["problems"]
    assert "ZPAY_KEY is missing" in problems
    assert any("ZPAY_NOTIFY_URL" in problem for problem in problems)

    caplog.clear()
    zpay_service.log_configuration_status(
        PaymentSettings(enabled=False, pid="", key="", notify_url="", return_url="", cid=None)
    )
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
