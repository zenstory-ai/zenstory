"""
Tests for admin quota/check-in/referral metric endpoints.
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from config.datetime_utils import utcnow
from models import UpgradeFunnelEvent, User
from models.skill import UserSkill
from models.subscription import SubscriptionHistory, SubscriptionPlan, UsageQuota, UserSubscription
from services.core.auth_service import hash_password
from services.quota_service import quota_service


async def create_user(
    db_session: Session,
    username: str,
    email: str,
    password: str = "password123",
    *,
    is_superuser: bool = False,
) -> User:
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        email_verified=True,
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def login_user(client: AsyncClient, username: str, password: str = "password123") -> str:
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.integration
async def test_admin_quota_usage_and_user_quota_detail(client: AsyncClient, db_session: Session):
    admin = await create_user(db_session, "admin_quota_metrics", "admin_quota_metrics@example.com", is_superuser=True)
    target = await create_user(db_session, "quota_target_user", "quota_target_user@example.com")

    plan = SubscriptionPlan(
        name="pro-quota-metrics",
        display_name="专业版",
        display_name_en="Pro",
        price_monthly_cents=1999,
        price_yearly_cents=19999,
        features={
            "ai_conversations_per_day": 80,
            "material_uploads": 30,
            "material_decompositions": 15,
            "custom_skills": 12,
            "inspiration_copies_monthly": 50,
        },
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)

    now = utcnow()
    db_session.add(
        UserSubscription(
            user_id=target.id,
            plan_id=plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
            cancel_at_period_end=False,
        )
    )
    db_session.add(
        UsageQuota(
            user_id=target.id,
            period_start=now,
            period_end=now + timedelta(days=30),
            ai_conversations_used=9,
            material_uploads_used=7,
            material_decompositions_used=4,
            skill_creates_used=3,
            inspiration_copies_used=6,
            # The current Beijing month, stored as naive UTC like real rows.
            monthly_period_start=quota_service._get_month_start(now).replace(tzinfo=None),
            monthly_period_end=quota_service._get_next_month_start(now).replace(tzinfo=None),
        )
    )
    # The custom-skill cap counts skills the user owns, not monthly creations.
    db_session.add_all(
        [UserSkill(user_id=target.id, name=f"skill {i}", instructions="x") for i in range(3)]
    )
    db_session.commit()

    token = await login_user(client, admin.username)

    usage_response = await client.get(
        "/api/admin/quota/usage",
        headers=auth_headers(token),
    )
    assert usage_response.status_code == 200
    usage_data = usage_response.json()
    assert usage_data["material_decompositions"] == 4
    assert usage_data["skills_created"] == 3
    assert usage_data["inspiration_copies"] == 6
    assert "material_uploads" not in usage_data
    assert usage_data["period_start"].endswith("+00:00")

    detail_by_username = await client.get(
        f"/api/admin/quota/{target.username}",
        headers=auth_headers(token),
    )
    assert detail_by_username.status_code == 200
    detail_data = detail_by_username.json()
    assert detail_data["user_id"] == target.id
    assert detail_data["username"] == target.username
    assert detail_data["email"] == target.email
    assert detail_data["plan_name"] == plan.name
    assert detail_data["plan_display_name"] == "专业版"
    assert detail_data["ai_conversations"]["used"] == 9
    assert detail_data["ai_conversations"]["limit"] == 80
    assert detail_data["material_decompositions"] == {
        "used": 4,
        "limit": 15,
        "reset_at": detail_data["material_decompositions"]["reset_at"],
    }
    assert detail_data["custom_skills"]["used"] == 3
    assert detail_data["custom_skills"]["limit"] == 12
    assert detail_data["custom_skills"]["reset_at"] is None
    assert detail_data["inspiration_copies"]["used"] == 6
    assert detail_data["inspiration_copies"]["limit"] == 50

    detail_by_email = await client.get(
        f"/api/admin/quota/{target.email}",
        headers=auth_headers(token),
    )
    assert detail_by_email.status_code == 200
    assert detail_by_email.json()["user_id"] == target.id

    detail_by_id = await client.get(
        f"/api/admin/quota/{target.id}",
        headers=auth_headers(token),
    )
    assert detail_by_id.status_code == 200
    assert detail_by_id.json()["user_id"] == target.id

    missing_user_response = await client.get(
        "/api/admin/quota/user-not-found-id",
        headers=auth_headers(token),
    )
    assert missing_user_response.status_code == 404
    assert missing_user_response.json()["error_detail"] == "User not found"


@pytest.mark.integration
async def test_admin_upgrade_conversion_stats_by_source(client: AsyncClient, db_session: Session):
    admin = await create_user(db_session, "admin_upgrade_metrics", "admin_upgrade_metrics@example.com", is_superuser=True)
    user_a = await create_user(db_session, "upgrade_user_a", "upgrade_user_a@example.com")
    user_b = await create_user(db_session, "upgrade_user_b", "upgrade_user_b@example.com")

    now = utcnow()
    period_end = now + timedelta(days=30)

    db_session.add(
        SubscriptionHistory(
            user_id=user_a.id,
            action="upgraded",
            plan_name="pro",
            start_date=now,
            end_date=period_end,
            event_metadata={"source": "redemption_code", "upgrade_source": "chat_quota_blocked"},
            created_at=now,
        )
    )
    db_session.add(
        SubscriptionHistory(
            user_id=user_b.id,
            action="created",
            plan_name="pro",
            start_date=now,
            end_date=period_end,
            event_metadata={"source": "redemption_code", "upgrade_source": "settings_subscription_upgrade"},
            created_at=now,
        )
    )
    db_session.add(
        SubscriptionHistory(
            user_id=user_b.id,
            action="renewed",
            plan_name="pro",
            start_date=now,
            end_date=period_end,
            event_metadata={"source": "redemption_code"},
            created_at=now,
        )
    )
    db_session.add(
        SubscriptionHistory(
            user_id=user_b.id,
            action="upgraded",
            plan_name="free",
            start_date=now,
            end_date=period_end,
            event_metadata={"source": "redemption_code", "upgrade_source": "chat_quota_blocked"},
            created_at=now,
        )
    )
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get(
        "/api/admin/dashboard/upgrade-conversion?days=30",
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()

    assert data["window_days"] == 30
    assert data["total_conversions"] == 3
    assert data["unattributed_conversions"] == 1
    assert len(data["sources"]) == 2
    assert data["sources"][0]["source"] == "chat_quota_blocked"
    assert data["sources"][0]["conversions"] == 1
    assert data["sources"][0]["share"] == pytest.approx(1 / 3, rel=1e-3)


@pytest.mark.integration
async def test_admin_upgrade_conversion_counts_authors_who_hit_the_ai_quota_wall_first(
    client: AsyncClient, db_session: Session
):
    """付费归因只记最后一次点击；撞过每日 AI 消息上限、之后才付费的转化要单独数出来。"""
    admin = await create_user(db_session, "admin_wall_metrics", "admin_wall_metrics@example.com", is_superuser=True)
    walled_paid = await create_user(db_session, "wall_paid", "wall_paid@example.com")
    walled_code = await create_user(db_session, "wall_code", "wall_code@example.com")
    walled_later = await create_user(db_session, "wall_later", "wall_later@example.com")
    never_walled = await create_user(db_session, "wall_never", "wall_never@example.com")

    now = utcnow()
    for user, source in (
        (walled_paid, "zpay"),
        (walled_code, "redemption_code"),
        (walled_later, "zpay"),
        (never_walled, "zpay"),
    ):
        db_session.add(
            SubscriptionHistory(
                user_id=user.id,
                action="upgraded",
                plan_name="pro",
                start_date=now,
                end_date=now + timedelta(days=30),
                # The last click before paying, not the wall.
                event_metadata={"source": source, "upgrade_source": "billing_header_upgrade"},
                created_at=now,
            )
        )
    for user, source, occurred_at in (
        (walled_paid, "chat_quota_blocked", now - timedelta(days=3)),
        (walled_code, "settings_subscription_upgrade:blocked", now - timedelta(hours=2)),
        # Hit the wall only after converting: does not explain the conversion.
        (walled_later, "chat_quota_blocked", now + timedelta(hours=1)),
        # A different limit is not the AI message wall.
        (never_walled, "file_version_quota_blocked", now - timedelta(days=1)),
    ):
        db_session.add(
            UpgradeFunnelEvent(
                user_id=user.id,
                event_name="upgrade_entry_expose",
                action="expose",
                source=source,
                surface="modal",
                occurred_at=occurred_at,
                created_at=occurred_at,
            )
        )
    db_session.commit()

    token = await login_user(client, admin.username)
    data = (
        await client.get("/api/admin/dashboard/upgrade-conversion?days=7", headers=auth_headers(token))
    ).json()

    assert data["total_conversions"] == 4
    assert data["after_ai_quota_wall_conversions"] == 2
    assert data["paid_after_ai_quota_wall_conversions"] == 1


@pytest.mark.integration
async def test_admin_quota_wall_conversions_skip_renewals_and_use_server_time(
    client: AsyncClient, db_session: Session
):
    """续费不算撞墙后转化；撞墙时间按服务端写入时间，不信客户端传来的 occurred_at。"""
    admin = await create_user(db_session, "admin_wall_renew", "admin_wall_renew@example.com", is_superuser=True)
    renewing_pro = await create_user(db_session, "wall_renew", "wall_renew@example.com")
    backdated = await create_user(db_session, "wall_backdated", "wall_backdated@example.com")

    now = utcnow()
    for user, action in ((renewing_pro, "renewed"), (backdated, "upgraded")):
        db_session.add(
            SubscriptionHistory(
                user_id=user.id,
                action=action,
                plan_name="pro",
                start_date=now,
                end_date=now + timedelta(days=30),
                event_metadata={"source": "zpay", "upgrade_source": "billing_header_renew"},
                created_at=now,
            )
        )
    # A Pro author hit some wall before renewing.
    db_session.add(
        UpgradeFunnelEvent(
            user_id=renewing_pro.id,
            event_name="upgrade_entry_expose",
            action="expose",
            source="chat_quota_blocked",
            surface="modal",
            occurred_at=now - timedelta(days=2),
            created_at=now - timedelta(days=2),
        )
    )
    # Reported after paying, with a client clock claiming it happened days earlier.
    db_session.add(
        UpgradeFunnelEvent(
            user_id=backdated.id,
            event_name="upgrade_entry_expose",
            action="expose",
            source="chat_quota_blocked",
            surface="modal",
            occurred_at=now - timedelta(days=2),
            created_at=now + timedelta(minutes=5),
        )
    )
    db_session.commit()

    token = await login_user(client, admin.username)
    data = (
        await client.get("/api/admin/dashboard/upgrade-conversion?days=7", headers=auth_headers(token))
    ).json()

    assert data["total_conversions"] == 2
    assert data["after_ai_quota_wall_conversions"] == 0
    assert data["paid_after_ai_quota_wall_conversions"] == 0


@pytest.mark.integration
async def test_admin_upgrade_funnel_stats_by_source(client: AsyncClient, db_session: Session):
    admin = await create_user(db_session, "admin_upgrade_funnel", "admin_upgrade_funnel@example.com", is_superuser=True)
    user_a = await create_user(db_session, "upgrade_funnel_a", "upgrade_funnel_a@example.com")

    now = utcnow()
    old = now - timedelta(days=45)

    db_session.add_all(
        [
            UpgradeFunnelEvent(
                user_id=user_a.id,
                event_name="upgrade_entry_expose",
                action="expose",
                source="chat_quota_blocked",
                surface="modal",
                occurred_at=now,
            ),
            UpgradeFunnelEvent(
                user_id=user_a.id,
                event_name="upgrade_entry_expose",
                action="expose",
                source="chat_quota_blocked",
                surface="modal",
                occurred_at=now,
            ),
            UpgradeFunnelEvent(
                user_id=user_a.id,
                event_name="upgrade_entry_expose",
                action="expose",
                source="chat_quota_blocked",
                surface="modal",
                occurred_at=now,
            ),
            UpgradeFunnelEvent(
                user_id=user_a.id,
                event_name="upgrade_entry_click",
                action="click",
                source="chat_quota_blocked",
                surface="modal",
                cta="primary",
                destination="billing",
                occurred_at=now,
            ),
            UpgradeFunnelEvent(
                user_id=user_a.id,
                event_name="upgrade_entry_click",
                action="click",
                source="chat_quota_blocked",
                surface="modal",
                cta="primary",
                destination="billing",
                occurred_at=now,
            ),
            UpgradeFunnelEvent(
                user_id=user_a.id,
                event_name="upgrade_entry_conversion",
                action="conversion",
                source="chat_quota_blocked",
                surface="page",
                destination="billing",
                occurred_at=now,
            ),
            UpgradeFunnelEvent(
                user_id=user_a.id,
                event_name="upgrade_entry_expose",
                action="expose",
                source="settings_subscription_upgrade",
                surface="page",
                occurred_at=now,
            ),
            UpgradeFunnelEvent(
                user_id=user_a.id,
                event_name="upgrade_entry_expose",
                action="expose",
                source="settings_subscription_upgrade",
                surface="page",
                occurred_at=now,
            ),
            UpgradeFunnelEvent(
                user_id=user_a.id,
                event_name="upgrade_entry_click",
                action="click",
                source="settings_subscription_upgrade",
                surface="page",
                cta="direct",
                destination="pricing",
                occurred_at=now,
            ),
            # outside lookback window
            UpgradeFunnelEvent(
                user_id=user_a.id,
                event_name="upgrade_entry_expose",
                action="expose",
                source="chat_quota_blocked",
                surface="modal",
                occurred_at=old,
            ),
        ]
    )
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get(
        "/api/admin/dashboard/upgrade-funnel?days=30",
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()

    assert data["window_days"] == 30
    assert data["totals"] == {"expose": 5, "click": 3, "conversion": 1}
    assert len(data["sources"]) == 2

    first = data["sources"][0]
    assert first["source"] == "chat_quota_blocked"
    assert first["exposes"] == 3
    assert first["clicks"] == 2
    assert first["conversions"] == 1
    assert first["click_through_rate"] == pytest.approx(2 / 3, rel=1e-3)
    assert first["conversion_rate_from_click"] == pytest.approx(0.5, rel=1e-3)


@pytest.mark.integration
@pytest.mark.parametrize(
    "endpoint",
    [
        "/api/admin/quota/usage",
        "/api/admin/dashboard/upgrade-conversion",
        "/api/admin/dashboard/upgrade-funnel",
    ],
)
async def test_admin_metrics_endpoints_forbidden_for_non_superuser(
    endpoint: str, client: AsyncClient, db_session: Session
):
    suffix = endpoint.replace("/", "_").replace("-", "_")
    user = await create_user(
        db_session,
        f"normal_metrics_user{suffix}",
        f"normal_metrics_user{suffix}@example.com",
    )
    token = await login_user(client, user.username)

    response = await client.get(endpoint, headers=auth_headers(token))
    assert response.status_code == 403
