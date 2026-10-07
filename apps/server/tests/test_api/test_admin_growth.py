"""Contract tests for the admin customer-growth dashboard."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import event
from sqlmodel import Session

from models import User
from models.llm_usage import LLMUsageEvent
from models.payment import PaymentOrder
from models.subscription import SubscriptionHistory
from services.core.auth_service import hash_password
from services.usage.admin_growth_service import get_growth_dashboard

NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)  # 16:00 Beijing


def _naive(value: datetime) -> datetime:
    return value.replace(tzinfo=None)


def _user(
    session: Session,
    name: str,
    created_at: datetime,
    *,
    admin: bool = False,
    active: bool = True,
) -> User:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_superuser=admin,
        is_active=active,
        created_at=_naive(created_at),
        updated_at=_naive(created_at),
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _usage(
    session: Session,
    user: User,
    at: datetime,
    *,
    backfilled: bool = False,
) -> None:
    session.add(
        LLMUsageEvent(
            user_id=user.id,
            source="agent",
            model="deepseek-flash",
            cache_hit_tokens=10,
            cache_miss_tokens=20,
            output_tokens=30,
            price_band="offpeak",
            pricing_version="test",
            occurred_at=_naive(at),
            is_backfilled=backfilled,
        )
    )


def _order(
    session: Session,
    user: User,
    name: str,
    at: datetime | None,
    *,
    status: str = "paid",
    amount: int = 1999,
) -> None:
    session.add(
        PaymentOrder(
            out_trade_no=name,
            user_id=user.id,
            plan_name="pro",
            plan_display_name="Pro",
            product_name="ZenStory Pro",
            cycle="monthly",
            amount_cents=amount,
            duration_days=30,
            payment_method="alipay",
            status=status,
            fulfillment_status="fulfilled" if status == "paid" else "pending",
            created_at=_naive((at or NOW) - timedelta(minutes=5)),
            paid_at=_naive(at) if at else None,
        )
    )


def _grant(session: Session, user: User, source: str, at: datetime) -> None:
    session.add(
        SubscriptionHistory(
            user_id=user.id,
            action="upgraded",
            plan_name="pro",
            start_date=_naive(at),
            end_date=_naive(at + timedelta(days=30)),
            event_metadata={"source": source},
            created_at=_naive(at),
        )
    )


async def _headers(client: AsyncClient, admin: User) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        data={"username": admin.username, "password": "password123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.integration
async def test_growth_dashboard_uses_real_cohorts_payments_and_grants(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setattr("services.usage.admin_growth_service.utcnow", lambda: NOW)
    admin = _user(db_session, "growth_admin", NOW - timedelta(days=2), admin=True)
    activated = _user(db_session, "growth_activated", NOW - timedelta(days=5))
    disabled = _user(db_session, "growth_disabled", NOW - timedelta(days=4), active=False)
    unactivated = _user(db_session, "growth_unactivated", NOW - timedelta(days=3))
    existing = _user(db_session, "growth_existing", NOW - timedelta(days=20))
    future = _user(db_session, "growth_future", NOW + timedelta(hours=1))
    previous = _user(db_session, "growth_previous", NOW - timedelta(days=12))

    # Multiple calls still represent one active/activated user. A disabled
    # customer's real historical usage remains a growth fact.
    _usage(db_session, activated, NOW - timedelta(days=4, hours=12))
    _usage(db_session, activated, NOW - timedelta(days=4))
    _usage(db_session, disabled, NOW - timedelta(days=3))
    _usage(db_session, existing, NOW - timedelta(days=1))
    _usage(db_session, unactivated, NOW - timedelta(days=2), backfilled=True)
    _usage(db_session, admin, NOW - timedelta(days=1))
    _usage(db_session, future, NOW + timedelta(hours=2))
    _usage(db_session, previous, NOW - timedelta(days=11))

    _order(db_session, activated, "growth-paid-1", NOW - timedelta(days=2), amount=1999)
    _order(db_session, activated, "growth-paid-2", NOW - timedelta(days=1), amount=500)
    _order(db_session, existing, "growth-paid-existing", NOW - timedelta(hours=2), amount=2999)
    _order(db_session, disabled, "growth-pending", None, status="pending", amount=9000)
    _order(db_session, disabled, "growth-refund", NOW - timedelta(days=1), status="refunded")
    _order(db_session, disabled, "growth-paid-no-time", None, status="paid")
    _order(db_session, admin, "growth-admin-paid", NOW - timedelta(days=1), amount=50000)
    _order(db_session, future, "growth-future-paid", NOW + timedelta(hours=2), amount=50000)
    _order(db_session, previous, "growth-prev-paid", NOW - timedelta(days=11), amount=999)

    _grant(db_session, disabled, "redemption_code", NOW - timedelta(days=2))
    _grant(db_session, disabled, "admin_update", NOW - timedelta(days=1))
    _grant(db_session, activated, "zpay", NOW - timedelta(days=1))
    _grant(db_session, admin, "admin_update", NOW - timedelta(days=1))
    _grant(db_session, future, "redemption_code", NOW + timedelta(hours=2))
    db_session.commit()

    response = await client.get(
        "/api/admin/dashboard/growth?days=7",
        headers=await _headers(client, admin),
    )
    assert response.status_code == 200
    payload = response.json()

    assert payload["days"] == 7
    assert payload["timezone"] == "Asia/Shanghai"
    assert payload["current"]["period_start"] == "2026-09-30T16:00:00+00:00"
    assert payload["current"]["period_end"] == "2026-10-07T08:00:00+00:00"
    assert payload["previous"]["period_start"] == "2026-09-23T16:00:00+00:00"
    assert payload["previous"]["period_end"] == "2026-09-30T08:00:00+00:00"

    current = payload["current"]["metrics"]
    assert current["new_users"] == 3
    assert current["ai_active_users"] == 3
    assert current["cohort_activated_users"] == 2
    assert current["cohort_activation_rate"] == 0.6667
    assert current["paid_orders"] == 3
    assert current["revenue_cents"] == 5498
    assert current["paid_users"] == 2
    assert current["cohort_paid_users"] == 1
    assert current["signup_to_paid_rate"] == 0.3333
    assert current["grant_upgrade_events"] == 2
    assert current["grant_upgrade_users"] == 1
    assert current["grant_channels"] == [
        {"channel": "admin_update", "events": 1, "users": 1},
        {"channel": "redemption_code", "events": 1, "users": 1},
    ]

    previous_metrics = payload["previous"]["metrics"]
    assert previous_metrics["new_users"] == 1
    assert previous_metrics["ai_active_users"] == 1
    assert previous_metrics["paid_orders"] == 1
    assert previous_metrics["revenue_cents"] == 999
    assert len(payload["daily"]) == 7
    assert payload["daily"][0]["date"] == "2026-10-01"
    assert payload["daily"][-1]["date"] == "2026-10-07"
    assert sum(day["new_users"] for day in payload["daily"]) == 3
    assert sum(day["cohort_activated_users"] for day in payload["daily"]) == 2


@pytest.mark.integration
async def test_growth_dashboard_null_rates_and_access_contract(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setattr("services.usage.admin_growth_service.utcnow", lambda: NOW)
    admin = _user(db_session, "growth_empty_admin", NOW - timedelta(days=100), admin=True)
    normal = _user(db_session, "growth_empty_normal", NOW - timedelta(days=100))

    admin_headers = await _headers(client, admin)
    payload = (await client.get("/api/admin/dashboard/growth?days=14", headers=admin_headers)).json()
    assert payload["current"]["metrics"]["cohort_activation_rate"] is None
    assert payload["current"]["metrics"]["signup_to_paid_rate"] is None
    assert payload["current"]["metrics"]["revenue_cents"] == 0

    invalid = await client.get("/api/admin/dashboard/growth?days=8", headers=admin_headers)
    assert invalid.status_code == 422
    forbidden = await client.get(
        "/api/admin/dashboard/growth?days=7",
        headers=await _headers(client, normal),
    )
    assert forbidden.status_code == 403


@pytest.mark.integration
def test_growth_dashboard_30_days_has_bounded_query_count(db_session: Session):
    statements = 0

    def count_statement(*_args):
        nonlocal statements
        statements += 1

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", count_statement)
    try:
        payload = get_growth_dashboard(db_session, days=30, now=NOW)
    finally:
        event.remove(bind, "before_cursor_execute", count_statement)

    assert len(payload["daily"]) == 30
    assert statements <= 20
