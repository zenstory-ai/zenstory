"""Admin cleanup: Beijing day windows, quota view, error codes, filters, UTC output."""

import sys
from datetime import UTC, date, datetime, timedelta

import pytest
from httpx import AsyncClient
from pydantic import BaseModel
from sqlmodel import Session, select

from config.datetime_utils import UTCDateTime
from models import User
from models.payment import PaymentOrder
from models.points import CheckInRecord
from models.referral import InviteCode, Referral
from models.skill import UserSkill
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.core.auth_service import hash_password
from services.features.activation_event_service import activation_event_service
from services.features.upgrade_funnel_event_service import upgrade_funnel_event_service
from services.quota_service import quota_service

# 2026-10-05 17:30 UTC is 2026-10-06 01:30 in Beijing.
FIXED_NOW = datetime(2026, 10, 5, 17, 30, tzinfo=UTC)


def make_user(db_session: Session, name: str, *, admin: bool = False, **fields) -> User:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        is_superuser=admin,
        **fields,
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


def make_plan(db_session: Session, name: str, features: dict) -> SubscriptionPlan:
    plan = db_session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == name)).first()
    if plan:
        plan.features = features
    else:
        plan = SubscriptionPlan(
            name=name,
            display_name=name.title(),
            display_name_en=name.title(),
            price_monthly_cents=0 if name == "free" else 2900,
            price_yearly_cents=0,
            features=features,
            is_active=True,
        )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


# ==================== datetime_utils ====================


def test_utc_datetime_serializes_with_offset():
    class Payload(BaseModel):
        at: UTCDateTime
        maybe: UTCDateTime | None = None

    naive = Payload(at=datetime(2026, 10, 5, 12, 0))
    assert naive.model_dump(mode="json") == {"at": "2026-10-05T12:00:00+00:00", "maybe": None}
    shanghai = datetime(2026, 10, 5, 20, 0, tzinfo=UTC) + timedelta(0)
    assert Payload(at=shanghai).model_dump(mode="json")["at"] == "2026-10-05T20:00:00+00:00"
    # Python-mode dumps keep the datetime object for internal callers.
    assert isinstance(naive.model_dump()["at"], datetime)


# ==================== Beijing-day admin counters ====================


@pytest.mark.integration
async def test_dashboard_today_and_week_use_beijing_days(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("api.admin.dashboard.utcnow", lambda: FIXED_NOW)
    today_start = datetime(2026, 10, 5, 16, 0)  # 00:00 Beijing on 10-06
    admin = make_user(db_session, "bj_dash_admin", admin=True, created_at=today_start - timedelta(days=30))
    # 00:10 Beijing today counts; 23:50 Beijing yesterday does not.
    make_user(db_session, "bj_dash_new", created_at=today_start + timedelta(minutes=10))
    inviter = make_user(db_session, "bj_dash_old", created_at=today_start - timedelta(minutes=10))
    db_session.add_all([
        CheckInRecord(
            user_id=inviter.id, check_in_date=date(2026, 10, 5), points_earned=1,
            created_at=today_start + timedelta(minutes=5),
        ),
        CheckInRecord(
            user_id=admin.id, check_in_date=date(2026, 10, 5), points_earned=1,
            created_at=today_start - timedelta(minutes=5),
        ),
    ])
    code = InviteCode(code="BJWK-0001", owner_id=inviter.id, max_uses=10)
    db_session.add(code)
    db_session.commit()
    # Inside the 7 Beijing days (6 days before today, 00:30 Beijing) vs. just outside.
    for invitee_name, created_at in (
        ("bj_ref_in", today_start - timedelta(days=6) + timedelta(minutes=30)),
        ("bj_ref_out", today_start - timedelta(days=6) - timedelta(minutes=30)),
    ):
        invitee = make_user(db_session, invitee_name, created_at=today_start - timedelta(days=20))
        db_session.add(Referral(
            inviter_id=inviter.id, invitee_id=invitee.id, invite_code_id=code.id,
            created_at=created_at,
        ))
    db_session.commit()

    response = await client.get("/api/admin/dashboard/stats", headers=await login(client, admin.username))

    assert response.status_code == 200
    data = response.json()
    assert data["new_users_today"] == 1
    assert data["today_check_ins"] == 1
    assert data["week_referrals"] == 1


def test_funnel_windows_start_at_beijing_midnight(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    # The package re-exports the service objects under the module names.
    for module_name in (
        "services.features.activation_event_service",
        "services.features.upgrade_funnel_event_service",
    ):
        monkeypatch.setattr(sys.modules[module_name], "utcnow", lambda: FIXED_NOW)

    activation = activation_event_service.get_funnel_stats(db_session, days=7)
    upgrade = upgrade_funnel_event_service.get_funnel_stats(db_session, days=7)

    for stats in (activation, upgrade):
        assert stats["period_start"] == "2026-09-29T16:00:00+00:00"
        assert stats["period_end"] == FIXED_NOW.isoformat()


@pytest.mark.integration
async def test_upgrade_conversion_window_starts_at_beijing_midnight(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("api.admin.dashboard.utcnow", lambda: FIXED_NOW)
    admin = make_user(db_session, "bj_conv_admin", admin=True)

    response = await client.get(
        "/api/admin/dashboard/upgrade-conversion?days=1", headers=await login(client, admin.username)
    )

    assert response.status_code == 200
    assert response.json()["period_start"] == "2026-10-05T16:00:00+00:00"


# ==================== Quota view ====================


def test_admin_quota_view_uses_effective_plan_and_resets_ended_periods(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("services.quota_service.utcnow", lambda: FIXED_NOW)
    make_plan(db_session, "free", {})
    pro = make_plan(db_session, "pro", {"material_decompositions": 9})
    user = make_user(db_session, "quota_view_lapsed")
    now = FIXED_NOW.replace(tzinfo=None)
    db_session.add(UserSubscription(
        user_id=user.id, plan_id=pro.id, status="active",
        current_period_start=now - timedelta(days=40), current_period_end=now - timedelta(days=1),
    ))
    db_session.add(UsageQuota(
        user_id=user.id, period_start=now - timedelta(days=2), period_end=now - timedelta(days=1),
        last_reset_at=now - timedelta(days=2), ai_conversations_used=17,
        material_decompositions_used=3, inspiration_copies_used=5,
        monthly_period_start=now - timedelta(days=60), monthly_period_end=now - timedelta(days=30),
    ))
    db_session.add(UserSkill(user_id=user.id, name="one", instructions="x"))
    db_session.commit()

    view = quota_service.get_admin_quota_view(db_session, user.id)

    # Lapsed Pro falls back to free, with the free preset limits; ended day and
    # month windows are reset exactly as the next enforced request would.
    next_beijing_midnight = datetime(2026, 10, 6, 16, tzinfo=UTC)
    next_beijing_month = datetime(2026, 10, 31, 16, tzinfo=UTC)
    assert view["plan_name"] == "free"
    assert view["ai_conversations"] == {"used": 0, "limit": 20, "reset_at": next_beijing_midnight}
    assert view["material_decompositions"] == {"used": 0, "limit": 0, "reset_at": next_beijing_month}
    assert view["inspiration_copies"]["used"] == 0
    assert view["custom_skills"] == {"used": 1, "limit": 3, "reset_at": None}


def test_admin_quota_view_keeps_usage_inside_current_beijing_periods(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr("services.quota_service.utcnow", lambda: FIXED_NOW)
    pro = make_plan(db_session, "pro", {})
    user = make_user(db_session, "quota_view_active")
    bare = make_user(db_session, "quota_view_no_row")
    now = FIXED_NOW.replace(tzinfo=None)
    day_start, day_end = datetime(2026, 10, 5, 16), datetime(2026, 10, 6, 16)
    month_start, month_end = datetime(2026, 9, 30, 16), datetime(2026, 10, 31, 16)
    db_session.add(UserSubscription(
        user_id=user.id, plan_id=pro.id, status="active",
        current_period_start=now - timedelta(days=1), current_period_end=now + timedelta(days=29),
    ))
    db_session.add(UsageQuota(
        user_id=user.id, period_start=day_start, period_end=day_end,
        last_reset_at=now - timedelta(hours=1), ai_conversations_used=4,
        material_decompositions_used=2, inspiration_copies_used=1,
        monthly_period_start=month_start, monthly_period_end=month_end,
    ))
    db_session.commit()

    view = quota_service.get_admin_quota_view(db_session, user.id)

    assert view["plan_name"] == "pro"
    assert view["ai_conversations"] == {
        "used": 4, "limit": -1, "reset_at": day_end.replace(tzinfo=UTC),
    }
    assert view["material_decompositions"] == {
        "used": 2, "limit": 5, "reset_at": month_end.replace(tzinfo=UTC),
    }
    assert view["inspiration_copies"]["used"] == 1

    bare_view = quota_service.get_admin_quota_view(db_session, bare.id)
    assert bare_view["ai_conversations"]["used"] == 0
    assert bare_view["material_decompositions"]["used"] == 0


@pytest.mark.parametrize(
    ("plan_name", "features"),
    [("pro", {}), ("pro", {"ai_conversations_per_day": 80}), ("free", {"ai_conversations_per_day": 7})],
)
def test_admin_quota_view_ai_limit_is_the_enforced_limit(
    db_session: Session, plan_name: str, features: dict
):
    plan = make_plan(db_session, plan_name, features)
    user = make_user(db_session, f"quota_view_ai_{plan_name}_{len(features)}")
    now = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(UserSubscription(
        user_id=user.id, plan_id=plan.id, status="active",
        current_period_start=now - timedelta(days=1), current_period_end=now + timedelta(days=29),
    ))
    db_session.commit()

    _, _, enforced_limit = quota_service.check_ai_conversation_quota(db_session, user.id)
    view = quota_service.get_admin_quota_view(db_session, user.id)

    assert view["ai_conversations"]["limit"] == enforced_limit
    assert quota_service.get_quota_snapshot(db_session, user.id)["ai_conversations"]["limit"] == enforced_limit


@pytest.mark.integration
async def test_quota_usage_totals_skip_stale_periods(client: AsyncClient, db_session: Session):
    admin = make_user(db_session, "quota_totals_admin", admin=True)
    current = make_user(db_session, "quota_totals_current")
    stale = make_user(db_session, "quota_totals_stale")
    now = datetime.now(UTC).replace(tzinfo=None)
    month_start = quota_service._get_month_start(now)
    next_month = quota_service._get_next_month_start(now)
    db_session.add_all([
        UsageQuota(
            user_id=current.id, period_start=now, period_end=now,
            material_decompositions_used=2, inspiration_copies_used=3, skill_creates_used=4,
            material_uploads_used=50,
            monthly_period_start=month_start, monthly_period_end=next_month,
        ),
        UsageQuota(
            user_id=stale.id, period_start=now, period_end=now,
            material_decompositions_used=40, inspiration_copies_used=40, skill_creates_used=40,
            monthly_period_start=month_start - timedelta(days=31), monthly_period_end=month_start,
        ),
    ])
    db_session.commit()

    response = await client.get("/api/admin/quota/usage", headers=await login(client, admin.username))

    assert response.status_code == 200
    assert response.json() == {
        "period_start": month_start.replace(tzinfo=UTC).isoformat(),
        "period_end": next_month.replace(tzinfo=UTC).isoformat(),
        "material_decompositions": 2,
        "inspiration_copies": 3,
        "skills_created": 4,
    }


# ==================== Error codes, filters, UTC output ====================


@pytest.mark.integration
@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/admin/users/missing-user", None),
        ("PUT", "/api/admin/users/missing-user", {"username": "whoever"}),
        ("DELETE", "/api/admin/users/missing-user", None),
        ("GET", "/api/admin/prompts/missing-type", None),
        ("DELETE", "/api/admin/prompts/missing-type", None),
        ("POST", "/api/admin/payment-orders/missing-order/sync", None),
    ],
)
async def test_missing_admin_resources_return_not_found_code(
    client: AsyncClient, db_session: Session, method: str, path: str, body: dict | None
):
    admin = make_user(db_session, "missing_res_admin", admin=True)
    headers = await login(client, admin.username)

    response = await client.request(method, path, headers=headers, json=body)

    assert response.status_code == 404
    assert response.json()["error_code"] == "ERR_NOT_FOUND"


@pytest.mark.integration
async def test_deactivating_self_is_rejected(client: AsyncClient, db_session: Session):
    admin = make_user(db_session, "self_deactivate_admin", admin=True)
    headers = await login(client, admin.username)

    response = await client.delete(f"/api/admin/users/{admin.id}", headers=headers)

    assert response.status_code == 400
    db_session.refresh(admin)
    assert admin.is_active is True


@pytest.mark.integration
async def test_admin_user_timestamps_are_utc_with_offset(client: AsyncClient, db_session: Session):
    admin = make_user(db_session, "utc_out_admin", admin=True, created_at=datetime(2026, 10, 5, 16, 30))
    headers = await login(client, admin.username)

    response = await client.get(f"/api/admin/users/{admin.id}", headers=headers)

    assert response.status_code == 200
    assert response.json()["created_at"] == "2026-10-05T16:30:00+00:00"
    listed = await client.get("/api/admin/users?search=utc_out_admin", headers=headers)
    assert listed.json()["items"][0]["created_at"].endswith("+00:00")


@pytest.mark.integration
async def test_payment_orders_filter_by_user(client: AsyncClient, db_session: Session):
    admin = make_user(db_session, "orders_filter_admin", admin=True)
    buyer = make_user(db_session, "orders_filter_buyer")
    other = make_user(db_session, "orders_filter_other")
    for index, owner in enumerate((buyer, buyer, other)):
        db_session.add(PaymentOrder(
            out_trade_no=f"2026100600000000000{index}", user_id=owner.id, plan_name="pro",
            plan_display_name="专业版", product_name="Pro", cycle="month", amount_cents=2900,
            duration_days=30, payment_method="alipay",
        ))
    db_session.commit()

    response = await client.get(
        f"/api/admin/payment-orders?user_id={buyer.id}", headers=await login(client, admin.username)
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert {item["user_id"] for item in data["items"]} == {buyer.id}


@pytest.mark.integration
async def test_subscriptions_search_by_username_or_email(client: AsyncClient, db_session: Session):
    admin = make_user(db_session, "subs_search_admin", admin=True)
    make_user(db_session, "subs_search_alice")
    make_user(db_session, "subs_search_bob")
    headers = await login(client, admin.username)

    by_name = await client.get("/api/admin/subscriptions?search=ALICE", headers=headers)
    by_email = await client.get(
        "/api/admin/subscriptions?search=subs_search_bob@example", headers=headers
    )

    assert by_name.status_code == 200
    assert [item["username"] for item in by_name.json()["items"]] == ["subs_search_alice"]
    assert by_name.json()["total"] == 1
    assert [item["username"] for item in by_email.json()["items"]] == ["subs_search_bob"]
