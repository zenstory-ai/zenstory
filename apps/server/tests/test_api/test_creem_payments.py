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
    monkeypatch.setenv("CREEM_RETURN_URL", "https://app-preview.zenstory.ai/dashboard/billing/return")
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
