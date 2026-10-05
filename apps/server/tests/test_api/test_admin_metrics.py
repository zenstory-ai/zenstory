"""
Tests for admin quota/check-in/referral metric endpoints.
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from config.datetime_utils import utcnow
from models import UpgradeFunnelEvent, User
from models.points import CheckInRecord
from models.referral import (
    REFERRAL_STATUS_COMPLETED,
    REFERRAL_STATUS_PENDING,
    REFERRAL_STATUS_REWARDED,
    InviteCode,
    Referral,
    UserReward,
)
from models.skill import UserSkill
from models.subscription import SubscriptionHistory, SubscriptionPlan, UsageQuota, UserSubscription
from services.core.auth_service import hash_password


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
            monthly_period_start=now,
            monthly_period_end=now + timedelta(days=30),
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
    assert usage_data["material_uploads"] >= 7
    assert usage_data["material_decomposes"] >= 4
    assert usage_data["skill_creates"] >= 3
    assert usage_data["inspiration_copies"] >= 6

    detail_by_username = await client.get(
        f"/api/admin/quota/{target.username}",
        headers=auth_headers(token),
    )
    assert detail_by_username.status_code == 200
    detail_data = detail_by_username.json()
    assert detail_data["user_id"] == target.id
    assert detail_data["username"] == target.username
    assert detail_data["plan_name"] == plan.name
    assert detail_data["ai_conversations_used"] == 9
    assert detail_data["ai_conversations_limit"] == 80
    assert detail_data["material_upload_used"] == 7
    assert detail_data["material_upload_limit"] == 30
    assert detail_data["skill_create_used"] == 3
    assert detail_data["skill_create_limit"] == 12
    assert detail_data["inspiration_copy_used"] == 6
    assert detail_data["inspiration_copy_limit"] == 50

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
async def test_admin_check_in_stats_and_records_filter(client: AsyncClient, db_session: Session):
    admin = await create_user(db_session, "admin_checkin_metrics", "admin_checkin_metrics@example.com", is_superuser=True)
    user_a = await create_user(db_session, "checkin_user_a", "checkin_user_a@example.com")
    user_b = await create_user(db_session, "checkin_user_b", "checkin_user_b@example.com")

    today = utcnow().date()
    yesterday = today - timedelta(days=1)

    db_session.add(
        CheckInRecord(
            user_id=user_a.id,
            check_in_date=today,
            streak_days=9,
            points_earned=10,
        )
    )
    db_session.add(
        CheckInRecord(
            user_id=user_b.id,
            check_in_date=today,
            streak_days=2,
            points_earned=5,
        )
    )
    db_session.add(
        CheckInRecord(
            user_id=user_a.id,
            check_in_date=yesterday,
            streak_days=8,
            points_earned=8,
        )
    )
    db_session.commit()

    token = await login_user(client, admin.username)

    stats_response = await client.get(
        "/api/admin/check-in/stats",
        headers=auth_headers(token),
    )
    assert stats_response.status_code == 200
    stats_data = stats_response.json()
    assert stats_data["today_count"] == 2
    assert stats_data["yesterday_count"] == 1
    assert stats_data["week_total"] >= 3
    assert stats_data["streak_distribution"].get("7", 0) >= 1

    records_response = await client.get(
        "/api/admin/check-in/records",
        headers=auth_headers(token),
        params={"user_id": user_a.id, "page": 1, "page_size": 10},
    )
    assert records_response.status_code == 200
    records_data = records_response.json()
    assert records_data["total"] == 2
    assert len(records_data["items"]) == 2
    assert all(item["user_id"] == user_a.id for item in records_data["items"])
    assert all(item["username"] == user_a.username for item in records_data["items"])


@pytest.mark.integration
async def test_admin_referral_stats_and_invite_filtering(client: AsyncClient, db_session: Session):
    admin = await create_user(db_session, "admin_referral_metrics", "admin_referral_metrics@example.com", is_superuser=True)
    inviter = await create_user(db_session, "ref_inviter", "ref_inviter@example.com")
    invitee = await create_user(db_session, "ref_invitee", "ref_invitee@example.com")

    active_code = InviteCode(code="ACTV-1001", owner_id=inviter.id, max_uses=5, current_uses=1, is_active=True)
    inactive_code = InviteCode(code="INAC-1001", owner_id=inviter.id, max_uses=5, current_uses=0, is_active=False)
    db_session.add(active_code)
    db_session.add(inactive_code)
    db_session.commit()
    db_session.refresh(active_code)

    pending_referral = Referral(
        inviter_id=inviter.id,
        invitee_id=invitee.id,
        invite_code_id=active_code.id,
        status=REFERRAL_STATUS_PENDING,
        inviter_rewarded=False,
    )
    completed_referral = Referral(
        inviter_id=inviter.id,
        invitee_id=admin.id,
        invite_code_id=active_code.id,
        status=REFERRAL_STATUS_COMPLETED,
        inviter_rewarded=False,
    )
    rewarded_referral = Referral(
        inviter_id=inviter.id,
        invitee_id=inviter.id,
        invite_code_id=active_code.id,
        status=REFERRAL_STATUS_REWARDED,
        inviter_rewarded=True,
    )
    db_session.add(pending_referral)
    db_session.add(completed_referral)
    db_session.add(rewarded_referral)
    db_session.commit()
    db_session.refresh(completed_referral)

    db_session.add(
        UserReward(
            user_id=inviter.id,
            reward_type="points",
            amount=25,
            source="referral",
            referral_id=completed_referral.id,
            is_used=False,
        )
    )
    db_session.commit()

    token = await login_user(client, admin.username)

    stats_response = await client.get(
        "/api/admin/referrals/stats",
        headers=auth_headers(token),
    )
    assert stats_response.status_code == 200
    stats_data = stats_response.json()
    assert stats_data["total_codes"] == 2
    assert stats_data["active_codes"] == 1
    assert stats_data["total_referrals"] == 3
    assert stats_data["successful_referrals"] == 2
    assert stats_data["pending_rewards"] == 1
    assert stats_data["total_points_awarded"] == 25

    invites_response = await client.get(
        "/api/admin/invites",
        headers=auth_headers(token),
        params={"is_active": "true"},
    )
    assert invites_response.status_code == 200
    invites_data = invites_response.json()
    assert invites_data["total"] == 1
    assert len(invites_data["items"]) == 1
    assert invites_data["items"][0]["code"] == "ACTV-1001"
    assert invites_data["items"][0]["owner_name"] == inviter.username


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
        "/api/admin/check-in/stats",
        "/api/admin/referrals/stats",
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
