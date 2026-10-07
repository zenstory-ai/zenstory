"""Payment checkout, callback, ownership, and admin order tests."""

import logging
from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import User
from models.payment import PaymentOrder
from models.subscription import SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password
from services.subscription.zpay_service import sign_params


@pytest.fixture(autouse=True)
def zpay_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ZPAY_ENABLED", "true")
    monkeypatch.setenv("ZPAY_PID", "test-pid")
    monkeypatch.setenv("ZPAY_KEY", "test-key")
    monkeypatch.setenv(
        "ZPAY_NOTIFY_URL", "https://api.example.com/api/v1/payments/zpay/notify"
    )
    monkeypatch.setenv(
        "ZPAY_RETURN_URL", "https://app.example.com/dashboard/billing/payment-return"
    )


def make_user(db_session: Session, name: str, *, admin: bool = False) -> User:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        is_superuser=admin,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def login(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", data={"username": username, "password": "password123"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def make_plan(db_session: Session, *, active: bool = True) -> SubscriptionPlan:
    plan = SubscriptionPlan(
        name="pro",
        display_name="专业版",
        display_name_en="Pro",
        price_monthly_cents=2900,
        price_yearly_cents=29000,
        features={},
        is_active=active,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.integration
async def test_long_admin_display_name_does_not_break_fixed_product_name(
    client: AsyncClient, db_session: Session
):
    user = make_user(db_session, "pay_long_name")
    plan = make_plan(db_session)
    plan.display_name = "专业" * 100
    db_session.add(plan)
    db_session.commit()
    headers = await login(client, user.username)

    response = await client.post(
        "/api/v1/payments/orders",
        headers=headers,
        json={"plan_name": "pro", "cycle": "year", "payment_method": "alipay"},
    )
    assert response.status_code == 200
    assert response.json()["order"]["plan_display_name"] == plan.display_name
    assert response.json()["checkout"]["fields"]["name"] == "ZenStory Pro 年度会员（365天）"


def callback_params(order: PaymentOrder, *, trade_no: str = "zpay-123") -> dict[str, str]:
    params = {
        "pid": "test-pid",
        "name": order.product_name,
        "money": f"{order.amount_cents / 100:.2f}",
        "out_trade_no": order.out_trade_no,
        "trade_no": trade_no,
        "trade_status": "TRADE_SUCCESS",
        "type": "alipay",
        "sign_type": "MD5",
    }
    params["sign"] = sign_params(params, "test-key")
    return params


@pytest.mark.integration
async def test_checkout_uses_server_price_and_owner_isolation(
    client: AsyncClient, db_session: Session
):
    owner = make_user(db_session, "pay_owner")
    other = make_user(db_session, "pay_other")
    make_plan(db_session)
    owner_headers = await login(client, owner.username)
    other_headers = await login(client, other.username)

    response = await client.post(
        "/api/v1/payments/orders",
        headers=owner_headers,
        json={"plan_name": "pro", "cycle": "month", "payment_method": "alipay"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["order"]["amount_cents"] == 2900
    assert payload["checkout"]["action"] == "https://zpayz.cn/submit.php"
    assert payload["checkout"]["method"] == "POST"
    assert payload["checkout"]["fields"]["money"] == "29.00"
    assert payload["checkout"]["fields"]["type"] == "alipay"
    assert payload["order"]["out_trade_no"].isdigit()
    assert len(payload["order"]["out_trade_no"]) <= 32

    forbidden = await client.get(
        f"/api/v1/payments/orders/{payload['order']['out_trade_no']}",
        headers=other_headers,
    )
    assert forbidden.status_code == 404

    injected = await client.post(
        "/api/v1/payments/orders",
        headers=owner_headers,
        json={
            "plan_name": "pro",
            "cycle": "year",
            "payment_method": "alipay",
            "amount_cents": 1,
        },
    )
    # Unknown fields are ignored, never trusted: the price still comes from the plan.
    assert injected.status_code == 200
    assert injected.json()["order"]["amount_cents"] == 29000
    assert injected.json()["checkout"]["fields"]["money"] == "290.00"


@pytest.mark.integration
async def test_disabled_options_and_order_rejection(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    user = make_user(db_session, "pay_disabled")
    make_plan(db_session)
    headers = await login(client, user.username)
    monkeypatch.setenv("ZPAY_ENABLED", "false")

    options = await client.get("/api/v1/payments/options")
    assert options.json() == {"enabled": False, "payment_methods": []}
    response = await client.post(
        "/api/v1/payments/orders",
        headers=headers,
        json={"plan_name": "pro", "cycle": "month", "payment_method": "alipay"},
    )
    assert response.status_code == 503


@pytest.mark.integration
async def test_malformed_callback_url_disables_options_without_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("ZPAY_NOTIFY_URL", "https://[invalid/")
    response = await client.get("/api/v1/payments/options")
    assert response.status_code == 200
    assert response.json() == {"enabled": False, "payment_methods": []}


@pytest.mark.integration
async def test_valid_callback_fulfills_once_even_when_checkout_disabled(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    user = make_user(db_session, "pay_callback")
    plan = make_plan(db_session)
    order = PaymentOrder(
        out_trade_no="20261005123456000001",
        user_id=user.id,
        plan_name=plan.name,
        plan_display_name=plan.display_name,
        product_name="ZenStory 专业版 月度会员（30天）",
        cycle="month",
        amount_cents=2900,
        duration_days=30,
        payment_method="alipay",
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    params = callback_params(order)
    monkeypatch.setenv("ZPAY_ENABLED", "false")
    monkeypatch.setenv("ZPAY_NOTIFY_URL", "http://invalid.example.com/notify?bad=1")
    monkeypatch.setenv("ZPAY_RETURN_URL", "not-a-url")

    first = await client.get("/api/v1/payments/zpay/notify", params=params)
    assert first.status_code == 200
    assert first.text == "success"
    db_session.expire_all()
    from sqlmodel import select

    subscription = db_session.exec(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    ).first()
    assert subscription is not None
    assert subscription.plan_id == plan.id
    original_end = subscription.current_period_end

    duplicate = await client.get("/api/v1/payments/zpay/notify", params=params)
    assert duplicate.text == "success"
    db_session.expire_all()
    subscription = db_session.exec(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    ).first()
    assert subscription is not None
    assert subscription.current_period_end == original_end

    conflict = callback_params(order, trade_no="different-trade")
    assert (await client.get("/api/v1/payments/zpay/notify", params=conflict)).text == "fail"

    duplicate_query = await client.get(
        "/api/v1/payments/zpay/notify",
        params=[*params.items(), ("money", params["money"])],
    )
    assert duplicate_query.text == "fail"


@pytest.mark.integration
@pytest.mark.parametrize("tamper", ["pid", "name", "money", "type", "trade_status"])
async def test_invalid_callback_never_fulfills(
    client: AsyncClient, db_session: Session, tamper: str
):
    user = make_user(db_session, f"bad_callback_{tamper}")
    make_plan(db_session)
    order = PaymentOrder(
        out_trade_no=f"20261005{len(tamper):024d}",
        user_id=user.id,
        plan_name="pro",
        plan_display_name="专业版",
        product_name="ZenStory 专业版 年度会员（365天）",
        cycle="year",
        amount_cents=29000,
        duration_days=365,
        payment_method="alipay",
    )
    db_session.add(order)
    db_session.commit()
    params = callback_params(order)
    replacements = {
        "pid": "other",
        "name": "different product",
        "money": "1.00",
        "type": "wxpay",
        "trade_status": "WAIT_BUYER_PAY",
    }
    params[tamper] = replacements[tamper]
    params["sign"] = sign_params(params, "test-key")

    response = await client.get("/api/v1/payments/zpay/notify", params=params)
    assert response.text == "fail"
    db_session.refresh(order)
    assert order.status == "pending"
    assert order.fulfillment_status == "pending"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("field", "value", "resign"),
    [
        ("sign", "not-a-real-signature", False),
        ("sign", "中文", False),
        ("money", "29.001", True),
    ],
)
async def test_malformed_signature_or_money_returns_generic_fail(
    client: AsyncClient,
    db_session: Session,
    field: str,
    value: str,
    resign: bool,
):
    user = make_user(db_session, f"malformed_{field}_{len(value)}")
    make_plan(db_session)
    order = PaymentOrder(
        out_trade_no=f"20261006{len(value):024d}",
        user_id=user.id,
        plan_name="pro",
        plan_display_name="专业版",
        product_name="ZenStory Pro 月度会员（30天）",
        cycle="month",
        amount_cents=2900,
        duration_days=30,
        payment_method="alipay",
    )
    db_session.add(order)
    db_session.commit()
    params = callback_params(order)
    params[field] = value
    if resign:
        params["sign"] = sign_params(params, "test-key")

    response = await client.get("/api/v1/payments/zpay/notify", params=params)
    assert response.status_code == 200
    assert response.text == "fail"
    db_session.refresh(order)
    assert order.status == "pending"


@pytest.mark.integration
async def test_admin_payment_orders_permissions_filters_and_pagination(
    client: AsyncClient, db_session: Session
):
    admin = make_user(db_session, "payment_admin", admin=True)
    user = make_user(db_session, "payment_customer")
    plan = make_plan(db_session)
    db_session.add_all(
        [
            PaymentOrder(
                out_trade_no=f"2026100511111111111{i}",
                user_id=user.id,
                plan_name="pro",
                plan_display_name=plan.display_name,
                product_name=f"Order {i}",
                cycle="month",
                amount_cents=2900,
                duration_days=30,
                payment_method="alipay",
                status="paid" if i == 1 else "pending",
                fulfillment_status="succeeded" if i == 1 else "pending",
                paid_at=datetime.utcnow() if i == 1 else None,
                fulfilled_at=datetime.utcnow() if i == 1 else None,
            )
            for i in range(2)
        ]
    )
    db_session.commit()
    user_headers = await login(client, user.username)
    admin_headers = await login(client, admin.username)

    assert (
        await client.get("/api/admin/payment-orders", headers=user_headers)
    ).status_code == 403
    response = await client.get(
        "/api/admin/payment-orders",
        headers=admin_headers,
        params={"status": "pending", "search": "payment_customer", "page_size": 1},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["page"] == 1
    assert payload["page_size"] == 1
    assert payload["items"][0]["username"] == user.username
    assert "sign" not in payload["items"][0]


def make_order(db_session: Session, user: User, out_trade_no: str, **overrides) -> PaymentOrder:
    values = {
        "out_trade_no": out_trade_no,
        "user_id": user.id,
        "plan_name": "pro",
        "plan_display_name": "专业版",
        "product_name": "ZenStory Pro 月度会员（30天）",
        "cycle": "month",
        "amount_cents": 2900,
        "duration_days": 30,
        "payment_method": "alipay",
    }
    values.update(overrides)
    order = PaymentOrder(**values)
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def mock_zpay_query(monkeypatch: pytest.MonkeyPatch, order: PaymentOrder, **overrides):
    import httpx

    from services.subscription.zpay_service import zpay_service

    calls: list[httpx.Request] = []
    payload = {
        "code": 1,
        "trade_no": "zpay-query-1",
        "out_trade_no": order.out_trade_no,
        "type": "alipay",
        "pid": "test-pid",
        "money": f"{order.amount_cents / 100:.2f}",
        "status": 1,
    }
    payload.update(overrides)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if overrides.get("unreachable"):
            raise httpx.ConnectError("down")
        return httpx.Response(200, json=payload)

    monkeypatch.setattr(zpay_service, "http_transport", httpx.MockTransport(handler))
    return calls


def _payment_log_records(caplog: pytest.LogCaptureFixture, message: str):
    return [record for record in caplog.records if record.getMessage() == message]


@pytest.mark.integration
async def test_rejected_callback_logs_reason_and_ids_without_secrets(
    client: AsyncClient, db_session: Session, caplog: pytest.LogCaptureFixture
):
    user = make_user(db_session, "pay_log_reject")
    make_plan(db_session)
    order = make_order(db_session, user, "20261007000000000001")
    params = callback_params(order)
    params["sign"] = "0" * 32

    with caplog.at_level(logging.WARNING):
        response = await client.get("/api/v1/payments/zpay/notify", params=params)
    assert response.text == "fail"

    records = _payment_log_records(caplog, "Zpay callback rejected")
    assert len(records) == 1
    record = records[0]
    assert record.levelno == logging.ERROR
    fields = record.custom_fields
    assert fields["reason"] == "invalid_signature"
    assert fields["out_trade_no"] == order.out_trade_no
    assert fields["trade_no"] == "zpay-123"
    assert fields["money"] == "29.00"
    assert "sign" not in fields
    assert "0" * 32 not in caplog.text
    assert "test-key" not in caplog.text


@pytest.mark.integration
async def test_fulfillment_failure_is_logged_with_stack_and_keeps_payment_fact(
    client: AsyncClient,
    db_session: Session,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
):
    from services.subscription.subscription_service import subscription_service

    user = make_user(db_session, "pay_log_fulfill")
    make_plan(db_session)
    order = make_order(db_session, user, "20261007000000000002")

    def boom(*_args, **_kwargs):
        raise RuntimeError("db pool exhausted")

    monkeypatch.setattr(subscription_service, "create_user_subscription", boom)
    with caplog.at_level(logging.WARNING):
        response = await client.get(
            "/api/v1/payments/zpay/notify", params=callback_params(order)
        )
    assert response.text == "fail"

    records = _payment_log_records(caplog, "Zpay callback could not grant the subscription")
    assert len(records) == 1
    assert records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None
    assert "db pool exhausted" in caplog.text
    assert records[0].custom_fields["reason"] == "fulfillment_failed"

    db_session.expire_all()
    stored = db_session.get(PaymentOrder, order.id)
    assert stored.status == "paid"
    assert stored.trade_no == "zpay-123"
    assert stored.fulfillment_status == "failed"


@pytest.mark.integration
async def test_user_sync_settles_own_order_and_is_rate_limited(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    from sqlmodel import select

    owner = make_user(db_session, "pay_sync_owner")
    other = make_user(db_session, "pay_sync_other")
    make_plan(db_session)
    order = make_order(db_session, owner, "20261007000000000003")
    calls = mock_zpay_query(monkeypatch, order)
    owner_headers = await login(client, owner.username)
    other_headers = await login(client, other.username)
    url = f"/api/v1/payments/orders/{order.out_trade_no}/sync"

    assert (await client.post(url, headers=other_headers)).status_code == 404
    assert calls == []

    response = await client.post(url, headers=owner_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "paid"
    assert body["fulfillment_status"] == "succeeded"
    assert body["trade_no"] == "zpay-query-1"
    assert "key=" not in str(body)
    db_session.expire_all()
    assert db_session.exec(
        select(UserSubscription).where(UserSubscription.user_id == owner.id)
    ).first()

    statuses = [(await client.post(url, headers=owner_headers)).status_code for _ in range(5)]
    assert statuses[:4] == [200, 200, 200, 200]
    assert statuses[4] == 429
    assert len(calls) == 1  # Fulfilled orders are answered locally.


@pytest.mark.integration
async def test_admin_sync_grants_and_writes_audit_log(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    from sqlmodel import select

    from models.subscription import AdminAuditLog

    admin = make_user(db_session, "pay_sync_admin", admin=True)
    customer = make_user(db_session, "pay_sync_customer")
    make_plan(db_session)
    order = make_order(db_session, customer, "20261007000000000004")
    admin_headers = await login(client, admin.username)
    customer_headers = await login(client, customer.username)
    url = f"/api/admin/payment-orders/{order.id}/sync"

    assert (await client.post(url, headers=customer_headers)).status_code == 403

    mock_zpay_query(monkeypatch, order, unreachable=True)
    down = await client.post(url, headers=admin_headers)
    assert down.status_code == 502
    assert down.json()["error_code"] == "ERR_PAYMENT_SYNC_FAILED"
    assert down.json()["error_detail"] == "sync_failed:provider_unavailable"

    mock_zpay_query(monkeypatch, order)
    response = await client.post(url, headers=admin_headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome"] == "fulfilled"
    assert payload["order"]["fulfillment_status"] == "succeeded"
    assert payload["order"]["username"] == customer.username

    db_session.expire_all()
    audits = db_session.exec(
        select(AdminAuditLog)
        .where(AdminAuditLog.action == "sync_payment_order")
        .order_by(AdminAuditLog.created_at)
    ).all()
    assert [audit.admin_user_id for audit in audits] == [admin.id, admin.id]
    assert audits[0].new_value["error"] == "provider_unavailable"
    assert audits[1].resource_id == order.id
    assert audits[1].old_value == {"status": "pending", "fulfillment_status": "pending"}
    assert audits[1].new_value["outcome"] == "fulfilled"

    # A late Zpay notify for the same payment cannot grant a second time.
    params = callback_params(order, trade_no="zpay-query-1")
    assert (await client.get("/api/v1/payments/zpay/notify", params=params)).text == "success"
    from models.subscription import SubscriptionHistory

    assert len(
        db_session.exec(
            select(SubscriptionHistory).where(SubscriptionHistory.user_id == customer.id)
        ).all()
    ) == 1


@pytest.mark.integration
async def test_admin_list_filters_fulfillment_and_needs_attention(
    client: AsyncClient, db_session: Session
):
    admin = make_user(db_session, "pay_filter_admin", admin=True)
    user = make_user(db_session, "pay_filter_user")
    make_plan(db_session)
    make_order(db_session, user, "20261008000000000001")  # abandoned checkout
    make_order(
        db_session, user, "20261008000000000002",
        status="paid", trade_no="t-2", fulfillment_status="failed", paid_at=datetime.utcnow(),
    )
    make_order(
        db_session, user, "20261008000000000003",
        status="paid", trade_no="t-3", fulfillment_status="succeeded",
        paid_at=datetime.utcnow(), fulfilled_at=datetime.utcnow(),
    )
    # Legacy row: fulfillment failed before payment facts were persisted.
    make_order(db_session, user, "20261008000000000004", fulfillment_status="failed")
    headers = await login(client, admin.username)

    attention = await client.get(
        "/api/admin/payment-orders", headers=headers, params={"needs_attention": "true"}
    )
    assert attention.status_code == 200
    numbers = {item["out_trade_no"] for item in attention.json()["items"]}
    assert numbers == {"20261008000000000002", "20261008000000000004"}
    assert attention.json()["needs_attention_total"] == 2

    failed = await client.get(
        "/api/admin/payment-orders",
        headers=headers,
        params={"fulfillment_status": "failed", "status": "paid"},
    )
    assert [item["out_trade_no"] for item in failed.json()["items"]] == ["20261008000000000002"]

    succeeded = await client.get(
        "/api/admin/payment-orders", headers=headers, params={"fulfillment_status": "succeeded"}
    )
    assert succeeded.json()["total"] == 1


@pytest.mark.integration
async def test_unknown_order_states_do_not_break_listing_or_lookup(
    client: AsyncClient, db_session: Session
):
    admin = make_user(db_session, "pay_unknown_admin", admin=True)
    user = make_user(db_session, "pay_unknown_user")
    make_plan(db_session)
    order = make_order(
        db_session, user, "20261009000000000001",
        status="refunded", cycle="quarter", payment_method="wxpay",
        fulfillment_status="reverted",
    )
    admin_headers = await login(client, admin.username)
    user_headers = await login(client, user.username)

    listing = await client.get("/api/admin/payment-orders", headers=admin_headers)
    assert listing.status_code == 200
    assert listing.json()["items"][0]["status"] == "refunded"

    lookup = await client.get(
        f"/api/v1/payments/orders/{order.out_trade_no}", headers=user_headers
    )
    assert lookup.status_code == 200
    assert lookup.json()["cycle"] == "quarter"


@pytest.mark.integration
async def test_create_order_records_upgrade_source_and_returns_error_codes(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    user = make_user(db_session, "pay_source")
    make_plan(db_session)
    headers = await login(client, user.username)

    response = await client.post(
        "/api/v1/payments/orders",
        headers=headers,
        json={
            "plan_name": "pro",
            "cycle": "month",
            "payment_method": "alipay",
            "upgrade_source": "pricing_page_primary",
        },
    )
    assert response.status_code == 200
    assert response.json()["order"]["upgrade_source"] == "pricing_page_primary"

    rejected = await client.post(
        "/api/v1/payments/orders",
        headers=headers,
        json={
            "plan_name": "pro",
            "cycle": "month",
            "payment_method": "alipay",
            "upgrade_source": "<script>",
        },
    )
    assert rejected.status_code == 422

    monkeypatch.setenv("ZPAY_ENABLED", "false")
    disabled = await client.post(
        "/api/v1/payments/orders",
        headers=headers,
        json={"plan_name": "pro", "cycle": "month", "payment_method": "alipay"},
    )
    assert disabled.status_code == 503
    assert disabled.json()["detail"] == "ERR_PAYMENT_UNAVAILABLE"


@pytest.mark.integration
async def test_create_order_ignores_unknown_fields_but_validates_known_ones(
    client: AsyncClient, db_session: Session
):
    """A web build deployed ahead of the API may send fields this API does not know."""
    user = make_user(db_session, "pay_newer_client")
    make_plan(db_session)
    headers = await login(client, user.username)
    base = {"plan_name": "pro", "cycle": "month", "payment_method": "alipay"}

    accepted = await client.post(
        "/api/v1/payments/orders",
        headers=headers,
        json={**base, "upgrade_source": "pricing_page", "client_build": "2026.10.06"},
    )
    assert accepted.status_code == 200
    order = accepted.json()["order"]
    assert order["amount_cents"] == 2900
    assert order["upgrade_source"] == "pricing_page"
    stored = db_session.exec(
        select(PaymentOrder).where(PaymentOrder.out_trade_no == order["out_trade_no"])
    ).one()
    assert stored.user_id == user.id

    for invalid in (
        {"plan_name": "enterprise"},
        {"cycle": "week"},
        {"payment_method": "wechat"},
        {"upgrade_source": "bad source!"},
    ):
        rejected = await client.post(
            "/api/v1/payments/orders",
            headers=headers,
            json={**base, **invalid, "client_build": "2026.10.06"},
        )
        assert rejected.status_code == 422, invalid
    missing = await client.post(
        "/api/v1/payments/orders",
        headers=headers,
        json={"plan_name": "pro", "payment_method": "alipay", "client_build": "x"},
    )
    assert missing.status_code == 422
