"""Regression tests for Creem USD checkout, webhook security, refunds/disputes, sync, and multi-currency admin reporting."""

from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from models import User
from models.payment import PaymentOrder
from models.subscription import SubscriptionHistory, SubscriptionPlan, UserSubscription
from scripts.release_preflight_check import creem_preflight_problems
from services.core.auth_service import hash_password
from services.subscription.creem_service import creem_service, sign_creem_payload
from services.subscription.subscription_service import subscription_service
from services.usage.admin_growth_service import get_growth_dashboard


def _configure_creem_env(monkeypatch: pytest.MonkeyPatch, *, test_mode: bool = True) -> dict[str, str]:
    api_key = "creem_test_secretkey1234567890" if test_mode else "creem_live_secretkey1234567890"
    secret = "whsec_test_signing_secret_123456"
    monkeypatch.setenv("CREEM_ENABLED", "true")
    monkeypatch.setenv("CREEM_TEST_MODE", "true" if test_mode else "false")
    monkeypatch.setenv("CREEM_API_KEY", api_key)
    monkeypatch.setenv("CREEM_WEBHOOK_SECRET", secret)
    monkeypatch.setenv("CREEM_PRODUCT_ID_PRO_MONTH", "prod_month_test_123")
    monkeypatch.setenv("CREEM_PRODUCT_ID_PRO_YEAR", "prod_year_test_456")
    monkeypatch.setenv("CREEM_RETURN_URL", "https://app-preview.zenstory.ai/dashboard/billing/payment-return")
    monkeypatch.setenv("ZPAY_ENABLED", "false")
    return {"api_key": api_key, "secret": secret}


def _make_user(db_session: Session, name: str, *, admin: bool = False) -> User:
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


async def _login(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", data={"username": username, "password": "password123"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _ensure_pro_plan_usd(db_session: Session) -> SubscriptionPlan:
    plan = db_session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == "pro")).first()
    if not plan:
        plan = SubscriptionPlan(
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
        db_session.add(plan)
    else:
        plan.price_monthly_usd_cents = 999
        plan.price_yearly_usd_cents = 7900
        db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.integration
async def test_payment_options_exposes_creem_card_and_hides_misconfigured(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
):
    _configure_creem_env(monkeypatch)
    resp = await client.get("/api/v1/payments/options")
    assert resp.status_code == 200
    assert resp.json() == {"enabled": True, "payment_methods": ["card"]}

    # Misconfigured test/live key mismatch hides Creem checkout safely.
    monkeypatch.setenv("CREEM_TEST_MODE", "false")
    resp2 = await client.get("/api/v1/payments/options")
    assert resp2.status_code == 200
    assert resp2.json() == {"enabled": False, "payment_methods": []}


@pytest.mark.integration
async def test_creem_checkout_webhook_fulfillment_idempotency_and_refund_revocation(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    cfg = _configure_creem_env(monkeypatch, test_mode=True)
    _ensure_pro_plan_usd(db_session)
    user = _make_user(db_session, "creem_buyer_1")
    auth_headers = await _login(client, user.username)
    captured_requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("x-api-key") == cfg["api_key"]
        body = json.loads(request.content.decode("utf-8"))
        captured_requests.append(body)
        return httpx.Response(
            200,
            json={
                "id": "ch_test_checkout_001",
                "object": "checkout",
                "product": body["product_id"],
                "units": 1,
                "status": "pending",
                "checkout_url": "https://creem.io/test/checkout/prod_month_test_123/ch_test_checkout_001",
                "success_url": body["success_url"],
                "mode": "test",
            },
        )

    monkeypatch.setattr(creem_service, "http_transport", httpx.MockTransport(handler))

    create_resp = await client.post(
        "/api/v1/payments/orders",
        headers=auth_headers,
        json={"plan_name": "pro", "cycle": "month", "payment_method": "card", "upgrade_source": "pricing_cta"},
    )
    assert create_resp.status_code == 200
    data = create_resp.json()
    order = data["order"]
    assert order["provider"] == "creem"
    assert order["currency"] == "USD"
    assert order["payment_method"] == "card"
    assert order["amount_cents"] == 999
    assert data["checkout"] == {
        "action": "https://creem.io/test/checkout/prod_month_test_123/ch_test_checkout_001",
        "method": "GET",
        "fields": {},
    }
    assert captured_requests[0]["product_id"] == "prod_month_test_123"
    assert captured_requests[0]["request_id"] == order["out_trade_no"]
    assert f"out_trade_no={order['out_trade_no']}" in captured_requests[0]["success_url"]

    # Invalid signature is rejected with 401.
    webhook_payload = {
        "id": "evt_checkout_001",
        "eventType": "checkout.completed",
        "created_at": 1750941205659,
        "object": {
            "id": "ch_test_checkout_001",
            "status": "completed",
            "request_id": order["out_trade_no"],
            "mode": "test",
            "product": {"id": "prod_month_test_123", "name": "ZenStory Pro (30 Days)"},
            "order": {
                "id": "ord_creem_001",
                "status": "paid",
                "amount": 999,
                "currency": "USD",
                "product": "prod_month_test_123",
            },
        },
    }
    raw_body = json.dumps(webhook_payload).encode("utf-8")
    bad_sig_resp = await client.post(
        "/api/v1/payments/creem/webhook",
        content=raw_body,
        headers={"creem-signature": "0" * 64, "Content-Type": "application/json"},
    )
    assert bad_sig_resp.status_code == 401

    # Tampered amount is rejected with 400.
    tampered = json.loads(raw_body)
    tampered["object"]["order"]["amount"] = 100
    tampered_bytes = json.dumps(tampered).encode("utf-8")
    tampered_resp = await client.post(
        "/api/v1/payments/creem/webhook",
        content=tampered_bytes,
        headers={
            "creem-signature": sign_creem_payload(tampered_bytes, cfg["secret"]),
            "Content-Type": "application/json",
        },
    )
    assert tampered_resp.status_code == 400

    # Valid checkout.completed fulfills Pro once; replay is idempotent.
    sig = sign_creem_payload(raw_body, cfg["secret"])
    ok_resp = await client.post(
        "/api/v1/payments/creem/webhook",
        content=raw_body,
        headers={"creem-signature": sig, "Content-Type": "application/json"},
    )
    assert ok_resp.status_code == 200
    replay_resp = await client.post(
        "/api/v1/payments/creem/webhook",
        content=raw_body,
        headers={"creem-signature": sig, "Content-Type": "application/json"},
    )
    assert replay_resp.status_code == 200

    db_session.expire_all()
    db_order = db_session.exec(
        select(PaymentOrder).where(PaymentOrder.out_trade_no == order["out_trade_no"])
    ).one()
    assert db_order.status == "paid"
    assert db_order.fulfillment_status == "succeeded"
    assert db_order.trade_no == "ord_creem_001"

    sub = db_session.exec(select(UserSubscription).where(UserSubscription.user_id == user.id)).one()
    assert sub.status == "active"
    remaining_days = (sub.current_period_end - utcnow().replace(tzinfo=None)).days
    assert 29 <= remaining_days <= 30

    history_rows = db_session.exec(
        select(SubscriptionHistory).where(SubscriptionHistory.user_id == user.id)
    ).all()
    creem_events = [h for h in history_rows if h.event_metadata.get("source") == "creem"]
    assert len(creem_events) == 1
    assert creem_events[0].event_metadata.get("upgrade_source") == "pricing_cta"

    # Refund webhook marks order refunded and revokes the 30 days cleanly (clamped to now).
    refund_payload = {
        "id": "evt_refund_001",
        "eventType": "refund.created",
        "created_at": 1750944000000,
        "object": {
            "id": "ref_creem_001",
            "status": "succeeded",
            "mode": "test",
            "order": {"id": "ord_creem_001", "status": "refunded"},
        },
    }
    refund_bytes = json.dumps(refund_payload).encode("utf-8")
    refund_sig = sign_creem_payload(refund_bytes, cfg["secret"])
    ref_resp = await client.post(
        "/api/v1/payments/creem/webhook",
        content=refund_bytes,
        headers={"creem-signature": refund_sig, "Content-Type": "application/json"},
    )
    assert ref_resp.status_code == 200
    ref_replay = await client.post(
        "/api/v1/payments/creem/webhook",
        content=refund_bytes,
        headers={"creem-signature": refund_sig, "Content-Type": "application/json"},
    )
    assert ref_replay.status_code == 200

    db_session.expire_all()
    db_order = db_session.exec(
        select(PaymentOrder).where(PaymentOrder.out_trade_no == order["out_trade_no"])
    ).one()
    assert db_order.status == "refunded"
    sub = db_session.exec(select(UserSubscription).where(UserSubscription.user_id == user.id)).one()
    assert sub.status == "cancelled"
    assert subscription_service.get_effective_paid_plan(db_session, user.id) is None


@pytest.mark.integration
async def test_creem_sync_compensation_and_dispute_needs_attention(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    cfg = _configure_creem_env(monkeypatch, test_mode=True)
    _ensure_pro_plan_usd(db_session)
    user = _make_user(db_session, "creem_sync_user")
    admin = _make_user(db_session, "creem_admin", admin=True)
    auth_headers = await _login(client, user.username)
    admin_headers = await _login(client, admin.username)
    remote_state = {"completed": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            body = json.loads(request.content.decode("utf-8"))
            return httpx.Response(
                200,
                json={
                    "id": "ch_sync_001",
                    "object": "checkout",
                    "product": body["product_id"],
                    "status": "pending",
                    "checkout_url": "https://creem.io/test/checkout/prod_year_test_456/ch_sync_001",
                    "mode": "test",
                },
            )
        if request.method == "GET":
            assert request.url.params.get("checkout_id") == "ch_sync_001"
            if not remote_state["completed"]:
                return httpx.Response(
                    200,
                    json={
                        "id": "ch_sync_001",
                        "object": "checkout",
                        "product": "prod_year_test_456",
                        "status": "pending",
                        "mode": "test",
                    },
                )
            return httpx.Response(
                200,
                json={
                    "id": "ch_sync_001",
                    "object": "checkout",
                    "product": "prod_year_test_456",
                    "status": "completed",
                    "order": "ord_creem_sync_001",
                    "mode": "test",
                },
            )
        raise AssertionError(f"unexpected {request.method}")

    monkeypatch.setattr(creem_service, "http_transport", httpx.MockTransport(handler))

    create_resp = await client.post(
        "/api/v1/payments/orders",
        headers=auth_headers,
        json={"plan_name": "pro", "cycle": "year", "payment_method": "card"},
    )
    assert create_resp.status_code == 200
    out_trade_no = create_resp.json()["order"]["out_trade_no"]

    sync1 = await client.post(f"/api/v1/payments/orders/{out_trade_no}/sync", headers=auth_headers)
    assert sync1.status_code == 200
    assert sync1.json()["status"] == "pending"

    remote_state["completed"] = True
    sync2 = await client.post(f"/api/v1/payments/orders/{out_trade_no}/sync", headers=auth_headers)
    assert sync2.status_code == 200
    assert sync2.json()["status"] == "paid"
    assert sync2.json()["fulfillment_status"] == "succeeded"
    assert sync2.json()["trade_no"] == "ord_creem_sync_001"

    dispute_payload = {
        "id": "evt_disp_001",
        "eventType": "dispute.created",
        "created_at": 1750945000000,
        "object": {
            "id": "disp_001",
            "mode": "test",
            "order": {"id": "ord_creem_sync_001"},
        },
    }
    dispute_bytes = json.dumps(dispute_payload).encode("utf-8")
    disp_resp = await client.post(
        "/api/v1/payments/creem/webhook",
        content=dispute_bytes,
        headers={
            "creem-signature": sign_creem_payload(dispute_bytes, cfg["secret"]),
            "Content-Type": "application/json",
        },
    )
    assert disp_resp.status_code == 200

    admin_list = await client.get(
        "/api/admin/payment-orders",
        headers=admin_headers,
        params={"needs_attention": "true"},
    )
    assert admin_list.status_code == 200
    assert admin_list.json()["needs_attention_total"] >= 1
    assert any(
        item["out_trade_no"] == out_trade_no and item["status"] == "disputed"
        for item in admin_list.json()["items"]
    )


@pytest.mark.integration
def test_growth_dashboard_splits_cny_and_usd_revenue_without_mixing(db_session: Session):
    user = _make_user(db_session, "growth_multi_curr")
    now = utcnow().replace(tzinfo=None)
    db_session.add(
        PaymentOrder(
            provider="zpay",
            currency="CNY",
            out_trade_no="202611240000010000000000000001",
            trade_no="zpay_trade_1",
            user_id=user.id,
            plan_name="pro",
            plan_display_name="专业版",
            product_name="ZenStory Pro 月度会员（30天）",
            cycle="month",
            amount_cents=4900,
            duration_days=30,
            payment_method="alipay",
            status="paid",
            fulfillment_status="succeeded",
            paid_at=now - timedelta(hours=1),
            fulfilled_at=now - timedelta(hours=1),
        )
    )
    db_session.add(
        PaymentOrder(
            provider="creem",
            currency="USD",
            out_trade_no="202611240000010000000000000002",
            trade_no="ord_usd_1",
            user_id=user.id,
            plan_name="pro",
            plan_display_name="专业版",
            product_name="ZenStory Pro (30 Days)",
            cycle="month",
            amount_cents=999,
            duration_days=30,
            payment_method="card",
            status="paid",
            fulfillment_status="succeeded",
            paid_at=now - timedelta(minutes=30),
            fulfilled_at=now - timedelta(minutes=30),
        )
    )
    db_session.add(
        PaymentOrder(
            provider="creem",
            currency="USD",
            out_trade_no="202611240000010000000000000003",
            trade_no="ord_usd_refunded",
            user_id=user.id,
            plan_name="pro",
            plan_display_name="专业版",
            product_name="ZenStory Pro (30 Days)",
            cycle="month",
            amount_cents=999,
            duration_days=30,
            payment_method="card",
            status="refunded",
            fulfillment_status="succeeded",
            paid_at=now - timedelta(minutes=20),
            fulfilled_at=now - timedelta(minutes=20),
        )
    )
    db_session.commit()

    payload = get_growth_dashboard(db_session, days=7, now=utcnow())
    metrics = payload["current"]["metrics"]
    assert metrics["paid_orders"] == 2
    assert metrics["revenue_cents"] == 4900
    assert metrics["revenue_usd_cents"] == 999
    assert metrics["revenue_by_currency"] == {"CNY": 4900, "USD": 999}


def test_creem_preflight_blocks_test_mode_in_production(monkeypatch: pytest.MonkeyPatch):
    _configure_creem_env(monkeypatch, test_mode=True)
    monkeypatch.setenv("ENVIRONMENT", "production")
    problems = creem_preflight_problems()
    assert problems is not None
    assert any("CREEM_TEST_MODE must be false in production" in p for p in problems)

    monkeypatch.setenv("ENVIRONMENT", "staging")
    assert creem_preflight_problems() == []


def test_creem_default_return_url_uses_payment_return_route(monkeypatch: pytest.MonkeyPatch):
    from config.payment_settings import PAYMENT_RETURN_PATH, get_creem_settings

    monkeypatch.delenv("CREEM_RETURN_URL", raising=False)
    monkeypatch.delenv("ZPAY_RETURN_URL", raising=False)
    monkeypatch.setenv("FRONTEND_URL", "https://app.zenstory.ai/")

    settings = get_creem_settings()
    assert settings.return_url == f"https://app.zenstory.ai{PAYMENT_RETURN_PATH}"
    assert settings.return_url == "https://app.zenstory.ai/dashboard/billing/payment-return"
    assert settings.return_url_valid is True

    monkeypatch.setenv("ZPAY_RETURN_URL", "https://app-preview.zenstory.ai/dashboard/billing/payment-return")
    assert get_creem_settings().return_url == "https://app-preview.zenstory.ai/dashboard/billing/payment-return"

    monkeypatch.setenv("CREEM_RETURN_URL", "https://custom.zenstory.ai/dashboard/billing/payment-return")
    assert get_creem_settings().return_url == "https://custom.zenstory.ai/dashboard/billing/payment-return"


def test_creem_preflight_rejects_wrong_return_url_path(monkeypatch: pytest.MonkeyPatch):
    from config.payment_settings import get_creem_settings

    _configure_creem_env(monkeypatch, test_mode=False)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("CREEM_RETURN_URL", "https://app.zenstory.ai/dashboard/billing/return")

    settings = get_creem_settings()
    assert settings.return_url_valid is False
    assert settings.checkout_configured is False
    assert settings.checkout_enabled is False
    problems = creem_preflight_problems()
    assert problems is not None
    assert any(
        "CREEM_RETURN_URL path must be exactly /dashboard/billing/payment-return" in p
        for p in problems
    )

    monkeypatch.setenv("CREEM_RETURN_URL", "https://app.zenstory.ai/dashboard/billing/payment-return")
    assert creem_preflight_problems() == []


@pytest.mark.integration
async def test_creem_refund_dispute_refund_sequence_does_not_double_deduct(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    cfg = _configure_creem_env(monkeypatch, test_mode=True)
    _ensure_pro_plan_usd(db_session)
    user = _make_user(db_session, "creem_double_deduct_guard")
    admin = _make_user(db_session, "creem_double_deduct_admin", admin=True)
    auth_headers = await _login(client, user.username)
    admin_headers = await _login(client, admin.username)
    seq = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seq["n"] += 1
        body = json.loads(request.content.decode("utf-8"))
        cid = f"ch_dd_{seq['n']}"
        return httpx.Response(
            200,
            json={
                "id": cid,
                "object": "checkout",
                "product": body["product_id"],
                "status": "pending",
                "checkout_url": f"https://checkout.creem.io/test/checkout/{body['product_id']}/{cid}",
                "mode": "test",
            },
        )

    monkeypatch.setattr(creem_service, "http_transport", httpx.MockTransport(handler))

    # Buy two monthly Pro orders so the user has ~60 days of Pro.
    orders = []
    for i in (1, 2):
        resp = await client.post(
            "/api/v1/payments/orders",
            headers=auth_headers,
            json={"plan_name": "pro", "cycle": "month", "payment_method": "card"},
        )
        assert resp.status_code == 200
        # Also proves checkout.creem.io host is accepted by backend validation.
        assert resp.json()["checkout"]["action"].startswith("https://checkout.creem.io/")
        out_trade_no = resp.json()["order"]["out_trade_no"]
        orders.append((out_trade_no, f"ch_dd_{i}", f"ord_dd_{i}"))
        wh = {
            "id": f"evt_dd_paid_{i}",
            "eventType": "checkout.completed",
            "object": {
                "id": f"ch_dd_{i}",
                "status": "completed",
                "request_id": out_trade_no,
                "mode": "test",
                "product": {"id": "prod_month_test_123"},
                "order": {
                    "id": f"ord_dd_{i}",
                    "status": "paid",
                    "amount": 999,
                    "currency": "USD",
                    "product": "prod_month_test_123",
                },
            },
        }
        raw = json.dumps(wh).encode("utf-8")
        r = await client.post(
            "/api/v1/payments/creem/webhook",
            content=raw,
            headers={"creem-signature": sign_creem_payload(raw, cfg["secret"]), "Content-Type": "application/json"},
        )
        assert r.status_code == 200

    db_session.expire_all()
    sub = db_session.exec(select(UserSubscription).where(UserSubscription.user_id == user.id)).one()
    assert 59 <= (sub.current_period_end - utcnow().replace(tzinfo=None)).days <= 60

    # 1. Refund order 1 -> deducts 30 days once (~30 days remain).
    ref1 = json.dumps({
        "id": "evt_dd_ref_1",
        "eventType": "refund.created",
        "object": {
            "id": "ref_dd_1",
            "status": "succeeded",
            "mode": "test",
            "refund_amount": 999,
            "order": {"id": "ord_dd_1", "status": "refunded"},
        },
    }).encode("utf-8")
    assert (
        await client.post(
            "/api/v1/payments/creem/webhook",
            content=ref1,
            headers={"creem-signature": sign_creem_payload(ref1, cfg["secret"]), "Content-Type": "application/json"},
        )
    ).status_code == 200

    # 2. Dispute arrives for already-refunded order 1 -> keeps status='refunded', sets disputed_at & needs_attention.
    disp = json.dumps({
        "id": "evt_dd_disp_1",
        "eventType": "dispute.created",
        "object": {
            "id": "disp_dd_1",
            "mode": "test",
            "order": {"id": "ord_dd_1"},
        },
    }).encode("utf-8")
    assert (
        await client.post(
            "/api/v1/payments/creem/webhook",
            content=disp,
            headers={"creem-signature": sign_creem_payload(disp, cfg["secret"]), "Content-Type": "application/json"},
        )
    ).status_code == 200

    db_session.expire_all()
    order1 = db_session.exec(select(PaymentOrder).where(PaymentOrder.out_trade_no == orders[0][0])).one()
    assert order1.status == "refunded"
    assert order1.refunded_at is not None
    assert order1.disputed_at is not None
    assert order1.failure_reason == "disputed:disp_dd_1"

    attn = await client.get("/api/admin/payment-orders", headers=admin_headers, params={"needs_attention": "true"})
    assert attn.status_code == 200
    assert any(item["out_trade_no"] == orders[0][0] for item in attn.json()["items"])

    # 3. Second refund.created (new refund ID!) after dispute -> must NOT deduct another 30 days.
    ref2 = json.dumps({
        "id": "evt_dd_ref_2",
        "eventType": "refund.created",
        "object": {
            "id": "ref_dd_2",
            "status": "succeeded",
            "mode": "test",
            "refund_amount": 999,
            "order": {"id": "ord_dd_1", "status": "refunded"},
        },
    }).encode("utf-8")
    assert (
        await client.post(
            "/api/v1/payments/creem/webhook",
            content=ref2,
            headers={"creem-signature": sign_creem_payload(ref2, cfg["secret"]), "Content-Type": "application/json"},
        )
    ).status_code == 200

    db_session.expire_all()
    sub_after = db_session.exec(select(UserSubscription).where(UserSubscription.user_id == user.id)).one()
    remaining_days = (sub_after.current_period_end - utcnow().replace(tzinfo=None)).days
    assert sub_after.status == "active"
    assert 29 <= remaining_days <= 30


@pytest.mark.integration
async def test_creem_amount_mismatch_persists_paid_failed_and_refund_clears_needs_attention(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    cfg = _configure_creem_env(monkeypatch, test_mode=True)
    _ensure_pro_plan_usd(db_session)
    user = _make_user(db_session, "creem_mismatch_user")
    admin = _make_user(db_session, "creem_mismatch_admin", admin=True)
    auth_headers = await _login(client, user.username)
    admin_headers = await _login(client, admin.username)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "ch_mm_001",
                "object": "checkout",
                "product": body["product_id"],
                "status": "pending",
                "checkout_url": "https://www.creem.io/test/checkout/prod_month_test_123/ch_mm_001",
                "mode": "test",
            },
        )

    monkeypatch.setattr(creem_service, "http_transport", httpx.MockTransport(handler))
    create_resp = await client.post(
        "/api/v1/payments/orders",
        headers=auth_headers,
        json={"plan_name": "pro", "cycle": "month", "payment_method": "card"},
    )
    assert create_resp.status_code == 200
    out_trade_no = create_resp.json()["order"]["out_trade_no"]

    # Valid signature & product_id, but Creem charged $12.99 instead of DB $9.99.
    mismatch_raw = json.dumps({
        "id": "evt_mm_001",
        "eventType": "checkout.completed",
        "object": {
            "id": "ch_mm_001",
            "status": "completed",
            "request_id": out_trade_no,
            "mode": "test",
            "product": {"id": "prod_month_test_123"},
            "order": {
                "id": "ord_mm_001",
                "status": "paid",
                "amount": 1299,
                "currency": "USD",
                "product": "prod_month_test_123",
            },
        },
    }).encode("utf-8")
    resp = await client.post(
        "/api/v1/payments/creem/webhook",
        content=mismatch_raw,
        headers={"creem-signature": sign_creem_payload(mismatch_raw, cfg["secret"]), "Content-Type": "application/json"},
    )
    assert resp.status_code == 400

    db_session.expire_all()
    order = db_session.exec(select(PaymentOrder).where(PaymentOrder.out_trade_no == out_trade_no)).one()
    assert order.status == "paid"
    assert order.trade_no == "ord_mm_001"
    assert order.paid_at is not None
    assert order.fulfillment_status == "failed"
    assert order.failure_reason == "amount_mismatch"
    assert subscription_service.get_effective_paid_plan(db_session, user.id) is None

    attn_before = await client.get("/api/admin/payment-orders", headers=admin_headers, params={"needs_attention": "true"})
    assert any(item["out_trade_no"] == out_trade_no for item in attn_before.json()["items"])

    # Refunding a failed-fulfillment paid order clears it from needs_attention.
    ref_raw = json.dumps({
        "id": "evt_mm_ref_001",
        "eventType": "refund.created",
        "object": {
            "id": "ref_mm_001",
            "status": "succeeded",
            "mode": "test",
            "refund_amount": 1299,
            "order": {"id": "ord_mm_001", "status": "refunded"},
        },
    }).encode("utf-8")
    assert (
        await client.post(
            "/api/v1/payments/creem/webhook",
            content=ref_raw,
            headers={"creem-signature": sign_creem_payload(ref_raw, cfg["secret"]), "Content-Type": "application/json"},
        )
    ).status_code == 200

    db_session.expire_all()
    order = db_session.exec(select(PaymentOrder).where(PaymentOrder.out_trade_no == out_trade_no)).one()
    assert order.status == "refunded"
    assert order.refunded_at is not None
    attn_after = await client.get("/api/admin/payment-orders", headers=admin_headers, params={"needs_attention": "true"})
    assert not any(item["out_trade_no"] == out_trade_no for item in attn_after.json()["items"])


@pytest.mark.integration
async def test_creem_partial_refund_waits_for_full_cumulative_amount_before_revoking(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    cfg = _configure_creem_env(monkeypatch, test_mode=True)
    _ensure_pro_plan_usd(db_session)
    user = _make_user(db_session, "creem_partial_user")
    admin = _make_user(db_session, "creem_partial_admin", admin=True)
    auth_headers = await _login(client, user.username)
    admin_headers = await _login(client, admin.username)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "id": "ch_part_001",
                "object": "checkout",
                "product": body["product_id"],
                "status": "pending",
                "checkout_url": "https://www.creem.io/test/checkout/prod_month_test_123/ch_part_001",
                "mode": "test",
            },
        )

    monkeypatch.setattr(creem_service, "http_transport", httpx.MockTransport(handler))
    create_resp = await client.post(
        "/api/v1/payments/orders",
        headers=auth_headers,
        json={"plan_name": "pro", "cycle": "month", "payment_method": "card"},
    )
    out_trade_no = create_resp.json()["order"]["out_trade_no"]

    paid_raw = json.dumps({
        "id": "evt_part_paid",
        "eventType": "checkout.completed",
        "object": {
            "id": "ch_part_001",
            "status": "completed",
            "request_id": out_trade_no,
            "mode": "test",
            "product": {"id": "prod_month_test_123"},
            "order": {"id": "ord_part_001", "status": "paid", "amount": 999, "currency": "USD", "product": "prod_month_test_123"},
        },
    }).encode("utf-8")
    assert (
        await client.post(
            "/api/v1/payments/creem/webhook",
            content=paid_raw,
            headers={"creem-signature": sign_creem_payload(paid_raw, cfg["secret"]), "Content-Type": "application/json"},
        )
    ).status_code == 200

    # Partial refund #1 ($4.00 of $9.99): does NOT revoke Pro, surfaces in needs_attention.
    part1_raw = json.dumps({
        "id": "evt_part_ref_1",
        "eventType": "refund.created",
        "object": {
            "id": "ref_part_1",
            "status": "succeeded",
            "mode": "test",
            "refund_amount": 400,
            "order": {"id": "ord_part_001", "amount": 999},
        },
    }).encode("utf-8")
    part1_sig = sign_creem_payload(part1_raw, cfg["secret"])
    assert (
        await client.post(
            "/api/v1/payments/creem/webhook",
            content=part1_raw,
            headers={"creem-signature": part1_sig, "Content-Type": "application/json"},
        )
    ).status_code == 200
    # Replaying the same partial refund ID must not double-count.
    assert (
        await client.post(
            "/api/v1/payments/creem/webhook",
            content=part1_raw,
            headers={"creem-signature": part1_sig, "Content-Type": "application/json"},
        )
    ).status_code == 200

    db_session.expire_all()
    order = db_session.exec(select(PaymentOrder).where(PaymentOrder.out_trade_no == out_trade_no)).one()
    assert order.status == "paid"
    assert order.refunded_at is None
    assert order.refunded_amount_cents == 400
    assert order.failure_reason == "partial_refund:400/999"
    assert subscription_service.get_effective_paid_plan(db_session, user.id) is not None

    attn_partial = await client.get("/api/admin/payment-orders", headers=admin_headers, params={"needs_attention": "true"})
    assert any(item["out_trade_no"] == out_trade_no for item in attn_partial.json()["items"])

    # Partial refund #2 ($5.99 -> cumulative $9.99): revokes Pro and clears needs_attention.
    part2_raw = json.dumps({
        "id": "evt_part_ref_2",
        "eventType": "refund.created",
        "object": {
            "id": "ref_part_2",
            "status": "succeeded",
            "mode": "test",
            "refund_amount": 599,
            "order": {"id": "ord_part_001", "amount": 999},
        },
    }).encode("utf-8")
    assert (
        await client.post(
            "/api/v1/payments/creem/webhook",
            content=part2_raw,
            headers={"creem-signature": sign_creem_payload(part2_raw, cfg["secret"]), "Content-Type": "application/json"},
        )
    ).status_code == 200

    db_session.expire_all()
    order = db_session.exec(select(PaymentOrder).where(PaymentOrder.out_trade_no == out_trade_no)).one()
    assert order.status == "refunded"
    assert order.refunded_at is not None
    assert order.refunded_amount_cents == 999
    assert subscription_service.get_effective_paid_plan(db_session, user.id) is None

    attn_done = await client.get("/api/admin/payment-orders", headers=admin_headers, params={"needs_attention": "true"})
    assert not any(item["out_trade_no"] == out_trade_no for item in attn_done.json()["items"])


@pytest.mark.integration
async def test_creem_early_refund_or_dispute_on_pending_order_blocks_late_settlement(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    cfg = _configure_creem_env(monkeypatch, test_mode=True)
    _ensure_pro_plan_usd(db_session)
    user = _make_user(db_session, "creem_early_event_user")
    auth_headers = await _login(client, user.username)
    seq = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        seq["n"] += 1
        body = json.loads(request.content.decode("utf-8"))
        cid = f"ch_early_{seq['n']}"
        return httpx.Response(
            200,
            json={
                "id": cid,
                "object": "checkout",
                "product": body["product_id"],
                "status": "pending",
                "checkout_url": f"https://www.creem.io/test/checkout/{body['product_id']}/{cid}",
                "mode": "test",
            },
        )

    monkeypatch.setattr(creem_service, "http_transport", httpx.MockTransport(handler))

    # Case 1: refund.created arrives before checkout.completed.
    r1 = await client.post(
        "/api/v1/payments/orders",
        headers=auth_headers,
        json={"plan_name": "pro", "cycle": "month", "payment_method": "card"},
    )
    otn1 = r1.json()["order"]["out_trade_no"]
    early_ref = json.dumps({
        "id": "evt_early_ref",
        "eventType": "refund.created",
        "object": {
            "id": "ref_early_1",
            "status": "succeeded",
            "mode": "test",
            "refund_amount": 999,
            "checkout": {"id": "ch_early_1"},
            "order": {"id": "ord_early_1"},
        },
    }).encode("utf-8")
    assert (
        await client.post(
            "/api/v1/payments/creem/webhook",
            content=early_ref,
            headers={"creem-signature": sign_creem_payload(early_ref, cfg["secret"]), "Content-Type": "application/json"},
        )
    ).status_code == 200

    late_paid1 = json.dumps({
        "id": "evt_late_paid_1",
        "eventType": "checkout.completed",
        "object": {
            "id": "ch_early_1",
            "status": "completed",
            "request_id": otn1,
            "mode": "test",
            "product": {"id": "prod_month_test_123"},
            "order": {"id": "ord_early_1", "status": "paid", "amount": 999, "currency": "USD", "product": "prod_month_test_123"},
        },
    }).encode("utf-8")
    late_resp1 = await client.post(
        "/api/v1/payments/creem/webhook",
        content=late_paid1,
        headers={"creem-signature": sign_creem_payload(late_paid1, cfg["secret"]), "Content-Type": "application/json"},
    )
    assert late_resp1.status_code == 400
    db_session.expire_all()
    assert subscription_service.get_effective_paid_plan(db_session, user.id) is None

    # Case 2: dispute.created arrives before checkout.completed.
    r2 = await client.post(
        "/api/v1/payments/orders",
        headers=auth_headers,
        json={"plan_name": "pro", "cycle": "month", "payment_method": "card"},
    )
    otn2 = r2.json()["order"]["out_trade_no"]
    early_disp = json.dumps({
        "id": "evt_early_disp",
        "eventType": "dispute.created",
        "object": {
            "id": "disp_early_1",
            "mode": "test",
            "checkout": {"id": "ch_early_2"},
            "order": {"id": "ord_early_2"},
        },
    }).encode("utf-8")
    assert (
        await client.post(
            "/api/v1/payments/creem/webhook",
            content=early_disp,
            headers={"creem-signature": sign_creem_payload(early_disp, cfg["secret"]), "Content-Type": "application/json"},
        )
    ).status_code == 200

    late_paid2 = json.dumps({
        "id": "evt_late_paid_2",
        "eventType": "checkout.completed",
        "object": {
            "id": "ch_early_2",
            "status": "completed",
            "request_id": otn2,
            "mode": "test",
            "product": {"id": "prod_month_test_123"},
            "order": {"id": "ord_early_2", "status": "paid", "amount": 999, "currency": "USD", "product": "prod_month_test_123"},
        },
    }).encode("utf-8")
    late_resp2 = await client.post(
        "/api/v1/payments/creem/webhook",
        content=late_paid2,
        headers={"creem-signature": sign_creem_payload(late_paid2, cfg["secret"]), "Content-Type": "application/json"},
    )
    assert late_resp2.status_code == 400
    db_session.expire_all()
    assert subscription_service.get_effective_paid_plan(db_session, user.id) is None


@pytest.mark.integration
def test_creem_preflight_verifies_products_against_db_usd_prices(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    _configure_creem_env(monkeypatch, test_mode=True)
    monkeypatch.setenv("ENVIRONMENT", "staging")
    _ensure_pro_plan_usd(db_session)

    prices = {"prod_month_test_123": (999, "USD"), "prod_year_test_456": (7900, "USD")}

    def handler(request: httpx.Request) -> httpx.Response:
        pid = request.url.params.get("product_id", "")
        price, currency = prices[pid]
        return httpx.Response(200, json={"id": pid, "price": price, "currency": currency, "status": "active"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http_client:
        assert creem_preflight_problems(verify_products=True, http_client=http_client, session=db_session) == []

        prices["prod_month_test_123"] = (1299, "USD")
        problems = creem_preflight_problems(verify_products=True, http_client=http_client, session=db_session)
        assert problems is not None
        assert any("prod_month_test_123" in p and "1299" in p and "999" in p for p in problems)


@pytest.mark.integration
async def test_admin_updating_pro_usd_price_returns_creem_sync_warning(
    client: AsyncClient, db_session: Session
):
    plan = _ensure_pro_plan_usd(db_session)
    admin = _make_user(db_session, "creem_plan_warn_admin", admin=True)
    admin_headers = await _login(client, admin.username)

    resp = await client.put(
        f"/api/admin/plans/{plan.id}",
        headers=admin_headers,
        json={"price_monthly_usd_cents": 1299},
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["price_monthly_usd_cents"] == 1299
    assert any("Creem" in w and "amount_mismatch" in w for w in payload["warnings"])

    # Updating a non-USD field emits no Creem warning.
    resp2 = await client.put(
        f"/api/admin/plans/{plan.id}",
        headers=admin_headers,
        json={"display_name": "专业版 Pro"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["warnings"] == []
