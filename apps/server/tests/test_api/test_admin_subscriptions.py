"""
Tests for admin subscription management endpoints.
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from api.admin.subscriptions import _normalize_status, _resolve_effective_status
from config.datetime_utils import utcnow
from core.error_handler import APIException
from models import User
from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
from services.core.auth_service import hash_password


async def create_user(
    db_session: Session,
    username: str,
    email: str,
    password: str = "password123",
    is_superuser: bool = False,
) -> User:
    """Create a user in the database."""
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
    """Login and return access token."""
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    """Return authorization headers."""
    return {"Authorization": f"Bearer {token}"}


def get_or_create_plan(db_session: Session, name: str) -> SubscriptionPlan:
    """Get existing plan or create one for tests."""
    plan = db_session.exec(
        select(SubscriptionPlan).where(SubscriptionPlan.name == name)
    ).first()
    if plan:
        return plan

    plan = SubscriptionPlan(
        name=name,
        display_name=name.title(),
        display_name_en=name.title(),
        price_monthly_cents=0,
        price_yearly_cents=0,
        features={"ai_conversations_per_day": 10, "max_projects": 3},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [
        ("active", "active"),
        ("past_due", "expired"),
        ("canceled", "cancelled"),
        ("CANCELLED", "cancelled"),
    ],
)
def test_normalize_status_aliases(raw_status: str, expected: str):
    assert _normalize_status(raw_status) == expected


@pytest.mark.unit
def test_normalize_status_rejects_unknown_value():
    with pytest.raises(APIException) as exc_info:
        _normalize_status("paused")

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid subscription status: paused"


@pytest.mark.unit
def test_resolve_effective_status_defaults_virtual_subscription_to_active():
    assert _resolve_effective_status(None) == "active"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_status", "expected"),
    [("canceled", "cancelled"), ("past_due", "expired")],
)
def test_resolve_effective_status_preserves_non_active_terminal_status(raw_status: str, expected: str):
    now = utcnow()
    subscription = UserSubscription(
        user_id="user-sub-status-1",
        plan_id="plan-sub-status-1",
        status=raw_status,
        current_period_start=now - timedelta(days=30),
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=True,
    )

    assert _resolve_effective_status(subscription) == expected


@pytest.mark.unit
def test_resolve_effective_status_treats_naive_expired_subscription_as_expired():
    now = utcnow()
    subscription = UserSubscription(
        user_id="user-sub-status-2",
        plan_id="plan-sub-status-2",
        status="active",
        current_period_start=now - timedelta(days=30),
        current_period_end=now.replace(tzinfo=None) - timedelta(minutes=1),
        cancel_at_period_end=False,
    )

    assert _resolve_effective_status(subscription) == "expired"


@pytest.mark.integration
async def test_admin_update_subscription_applies_status_with_plan_change(
    client: AsyncClient, db_session: Session
):
    """Admin update should apply status even when plan+duration are included."""
    admin = await create_user(db_session, "admin_sub_1", "admin_sub_1@example.com", is_superuser=True)
    target = await create_user(db_session, "user_sub_1", "user_sub_1@example.com")

    free_plan = get_or_create_plan(db_session, "free")
    pro_plan = get_or_create_plan(db_session, "pro")
    now = utcnow()
    subscription = UserSubscription(
        user_id=target.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    )
    db_session.add(subscription)
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.put(
        f"/api/admin/subscriptions/{target.id}",
        headers=auth_headers(token),
        json={"plan_name": "pro", "duration_days": 30, "status": "cancelled"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True

    refreshed = db_session.exec(
        select(UserSubscription).where(UserSubscription.user_id == target.id)
    ).first()
    assert refreshed is not None
    assert refreshed.plan_id == pro_plan.id
    assert refreshed.status == "cancelled"


@pytest.mark.integration
async def test_admin_update_subscription_rejects_partial_plan_fields(
    client: AsyncClient, db_session: Session
):
    """Admin update should reject payloads with only plan_name or only duration_days."""
    admin = await create_user(db_session, "admin_sub_2", "admin_sub_2@example.com", is_superuser=True)
    target = await create_user(db_session, "user_sub_2", "user_sub_2@example.com")

    free_plan = get_or_create_plan(db_session, "free")
    now = utcnow()
    subscription = UserSubscription(
        user_id=target.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    )
    db_session.add(subscription)
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.put(
        f"/api/admin/subscriptions/{target.id}",
        headers=auth_headers(token),
        json={"plan_name": "pro"},
    )

    assert response.status_code == 400


@pytest.mark.integration
async def test_admin_list_subscriptions_includes_users_without_subscription_record(
    client: AsyncClient, db_session: Session
):
    """Admin subscriptions list should include users even if they have no subscription row."""
    admin = await create_user(db_session, "admin_sub_5", "admin_sub_5@example.com", is_superuser=True)
    with_record = await create_user(db_session, "user_sub_3", "user_sub_3@example.com")
    without_record = await create_user(db_session, "user_sub_4", "user_sub_4@example.com")

    free_plan = get_or_create_plan(db_session, "free")
    now = utcnow()
    db_session.add(UserSubscription(
        user_id=with_record.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    ))
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get(
        "/api/admin/subscriptions?page=1&page_size=100",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    payload = response.json()
    items = payload["items"]
    users_by_id = {item["user_id"]: item for item in items}

    assert with_record.id in users_by_id
    assert without_record.id in users_by_id
    assert users_by_id[without_record.id]["has_subscription_record"] is False
    assert users_by_id[without_record.id]["plan_name"] == "free"
    assert users_by_id[without_record.id]["status"] == "active"
    assert users_by_id[without_record.id]["effective_plan_name"] == "free"
    assert users_by_id[without_record.id]["effective_status"] == "active"


@pytest.mark.integration
async def test_admin_update_subscription_bootstraps_missing_record(
    client: AsyncClient, db_session: Session
):
    """Admin update should bootstrap missing user_subscription before applying changes."""
    admin = await create_user(db_session, "admin_sub_6", "admin_sub_6@example.com", is_superuser=True)
    target = await create_user(db_session, "user_sub_5", "user_sub_5@example.com")
    get_or_create_plan(db_session, "free")

    token = await login_user(client, admin.username)
    response = await client.put(
        f"/api/admin/subscriptions/{target.id}",
        headers=auth_headers(token),
        json={"status": "cancelled"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True

    refreshed = db_session.exec(
        select(UserSubscription).where(UserSubscription.user_id == target.id)
    ).first()
    assert refreshed is not None
    assert refreshed.status == "cancelled"


@pytest.mark.integration
async def test_admin_get_code_not_found_returns_404(
    client: AsyncClient, db_session: Session
):
    """Admin get code should return 404 instead of internal error when code does not exist."""
    admin = await create_user(db_session, "admin_sub_3", "admin_sub_3@example.com", is_superuser=True)
    token = await login_user(client, admin.username)

    response = await client.get(
        "/api/admin/codes/non-existent-code-id",
        headers=auth_headers(token),
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_admin_update_plan_not_found_returns_404(
    client: AsyncClient, db_session: Session
):
    """Admin update plan should return 404 instead of internal error when plan does not exist."""
    admin = await create_user(db_session, "admin_sub_4", "admin_sub_4@example.com", is_superuser=True)
    token = await login_user(client, admin.username)

    response = await client.put(
        "/api/admin/plans/non-existent-plan-id",
        headers=auth_headers(token),
        json={"display_name": "Pro+"},
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_admin_create_code_returns_503_when_hmac_secret_invalid(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    """Admin create code should surface config error when HMAC secret is invalid."""
    monkeypatch.setenv("REDEMPTION_CODE_HMAC_SECRET", "too-short-secret")

    admin = await create_user(db_session, "admin_sub_5", "admin_sub_5@example.com", is_superuser=True)
    get_or_create_plan(db_session, "pro")
    token = await login_user(client, admin.username)

    response = await client.post(
        "/api/admin/codes",
        headers=auth_headers(token),
        json={"tier": "pro", "duration_days": 30, "code_type": "single_use"},
    )

    assert response.status_code == 503
    data = response.json()
    assert data["detail"] == "ERR_SERVICE_UNAVAILABLE"
    assert data["error_code"] == "ERR_SERVICE_UNAVAILABLE"
    assert "REDEMPTION_CODE_HMAC_SECRET" in data["error_detail"]


@pytest.mark.integration
async def test_admin_create_code_succeeds_when_hmac_secret_is_valid(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    """Admin create code should succeed when HMAC secret is configured correctly."""
    monkeypatch.setenv("REDEMPTION_CODE_HMAC_SECRET", "a" * 32)

    admin = await create_user(db_session, "admin_sub_6", "admin_sub_6@example.com", is_superuser=True)
    get_or_create_plan(db_session, "pro")
    token = await login_user(client, admin.username)

    response = await client.post(
        "/api/admin/codes",
        headers=auth_headers(token),
        json={"tier": "pro", "duration_days": 30, "code_type": "single_use"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["code"].startswith("ERG-")
    assert data["tier"] == "pro"
    assert data["code_type"] == "single_use"


@pytest.mark.integration
async def test_admin_create_codes_batch_returns_503_when_hmac_secret_invalid(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    """Batch create should also surface config error when HMAC secret is invalid."""
    monkeypatch.setenv("REDEMPTION_CODE_HMAC_SECRET", "short")

    admin = await create_user(db_session, "admin_sub_7", "admin_sub_7@example.com", is_superuser=True)
    get_or_create_plan(db_session, "pro")
    token = await login_user(client, admin.username)

    response = await client.post(
        "/api/admin/codes/batch",
        headers=auth_headers(token),
        json={
            "tier": "pro",
            "duration_days": 30,
            "count": 2,
            "code_type": "single_use",
        },
    )

    assert response.status_code == 503
    data = response.json()
    assert data["detail"] == "ERR_SERVICE_UNAVAILABLE"
    assert data["error_code"] == "ERR_SERVICE_UNAVAILABLE"
    assert "REDEMPTION_CODE_HMAC_SECRET" in data["error_detail"]


@pytest.mark.integration
async def test_admin_list_subscriptions_status_filter_active_only_returns_active_or_virtual(
    client: AsyncClient, db_session: Session
):
    admin = await create_user(db_session, "admin_sub_filter_1", "admin_sub_filter_1@example.com", is_superuser=True)
    active_user = await create_user(db_session, "active_user_1", "active_user_1@example.com")
    expired_user = await create_user(db_session, "expired_user_1", "expired_user_1@example.com")
    cancelled_user = await create_user(db_session, "cancelled_user_1", "cancelled_user_1@example.com")
    virtual_user = await create_user(db_session, "virtual_user_1", "virtual_user_1@example.com")

    free_plan = get_or_create_plan(db_session, "free")
    now = utcnow()
    db_session.add(UserSubscription(
        user_id=active_user.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    ))
    db_session.add(UserSubscription(
        user_id=expired_user.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now - timedelta(days=40),
        current_period_end=now - timedelta(days=1),
        cancel_at_period_end=False,
    ))
    db_session.add(UserSubscription(
        user_id=cancelled_user.id,
        plan_id=free_plan.id,
        status="cancelled",
        current_period_start=now - timedelta(days=10),
        current_period_end=now + timedelta(days=20),
        cancel_at_period_end=True,
    ))
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get(
        "/api/admin/subscriptions?status=active&page=1&page_size=100",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    users_by_id = {item["user_id"]: item for item in response.json()["items"]}

    assert active_user.id in users_by_id
    assert users_by_id[active_user.id]["effective_status"] == "active"

    assert virtual_user.id in users_by_id
    assert users_by_id[virtual_user.id]["has_subscription_record"] is False
    assert users_by_id[virtual_user.id]["effective_status"] == "active"

    assert expired_user.id not in users_by_id
    assert cancelled_user.id not in users_by_id


@pytest.mark.integration
async def test_admin_list_subscriptions_status_filter_expired_normalizes_past_due(
    client: AsyncClient, db_session: Session
):
    admin = await create_user(db_session, "admin_sub_filter_2", "admin_sub_filter_2@example.com", is_superuser=True)
    expired_by_period_user = await create_user(db_session, "expired_by_period_1", "expired_by_period_1@example.com")
    past_due_user = await create_user(db_session, "past_due_user_1", "past_due_user_1@example.com")
    expired_status_user = await create_user(db_session, "expired_status_user_1", "expired_status_user_1@example.com")
    active_user = await create_user(db_session, "active_user_2", "active_user_2@example.com")

    free_plan = get_or_create_plan(db_session, "free")
    now = utcnow()
    db_session.add(UserSubscription(
        user_id=expired_by_period_user.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now - timedelta(days=45),
        current_period_end=now - timedelta(days=1),
        cancel_at_period_end=False,
    ))
    db_session.add(UserSubscription(
        user_id=past_due_user.id,
        plan_id=free_plan.id,
        status="past_due",
        current_period_start=now - timedelta(days=15),
        current_period_end=now + timedelta(days=15),
        cancel_at_period_end=False,
    ))
    db_session.add(UserSubscription(
        user_id=expired_status_user.id,
        plan_id=free_plan.id,
        status="expired",
        current_period_start=now - timedelta(days=60),
        current_period_end=now - timedelta(days=30),
        cancel_at_period_end=False,
    ))
    db_session.add(UserSubscription(
        user_id=active_user.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=15),
        cancel_at_period_end=False,
    ))
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get(
        "/api/admin/subscriptions?status=expired&page=1&page_size=100",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    users_by_id = {item["user_id"]: item for item in response.json()["items"]}

    assert expired_by_period_user.id in users_by_id
    assert users_by_id[expired_by_period_user.id]["effective_status"] == "expired"

    assert past_due_user.id in users_by_id
    assert users_by_id[past_due_user.id]["status"] == "expired"
    assert users_by_id[past_due_user.id]["effective_status"] == "expired"

    assert expired_status_user.id in users_by_id
    assert users_by_id[expired_status_user.id]["status"] == "expired"

    assert active_user.id not in users_by_id


@pytest.mark.integration
async def test_admin_list_subscriptions_status_filter_cancelled_accepts_canceled_alias(
    client: AsyncClient, db_session: Session
):
    admin = await create_user(db_session, "admin_sub_filter_3", "admin_sub_filter_3@example.com", is_superuser=True)
    cancelled_user = await create_user(db_session, "cancelled_user_2", "cancelled_user_2@example.com")
    canceled_user = await create_user(db_session, "canceled_user_1", "canceled_user_1@example.com")
    active_user = await create_user(db_session, "active_user_3", "active_user_3@example.com")

    free_plan = get_or_create_plan(db_session, "free")
    now = utcnow()
    db_session.add(UserSubscription(
        user_id=cancelled_user.id,
        plan_id=free_plan.id,
        status="cancelled",
        current_period_start=now - timedelta(days=5),
        current_period_end=now + timedelta(days=20),
        cancel_at_period_end=True,
    ))
    db_session.add(UserSubscription(
        user_id=canceled_user.id,
        plan_id=free_plan.id,
        status="canceled",
        current_period_start=now - timedelta(days=5),
        current_period_end=now + timedelta(days=20),
        cancel_at_period_end=True,
    ))
    db_session.add(UserSubscription(
        user_id=active_user.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=20),
        cancel_at_period_end=False,
    ))
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get(
        "/api/admin/subscriptions?status=cancelled&page=1&page_size=100",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    users_by_id = {item["user_id"]: item for item in response.json()["items"]}

    assert cancelled_user.id in users_by_id
    assert users_by_id[cancelled_user.id]["status"] == "cancelled"
    assert users_by_id[cancelled_user.id]["effective_status"] == "cancelled"

    assert canceled_user.id in users_by_id
    assert users_by_id[canceled_user.id]["status"] == "cancelled"
    assert users_by_id[canceled_user.id]["effective_status"] == "cancelled"

    assert active_user.id not in users_by_id


@pytest.mark.integration
async def test_admin_get_user_subscription_not_found_returns_404(
    client: AsyncClient, db_session: Session
):
    admin = await create_user(db_session, "admin_sub_filter_4", "admin_sub_filter_4@example.com", is_superuser=True)
    target = await create_user(db_session, "user_without_sub_1", "user_without_sub_1@example.com")
    token = await login_user(client, admin.username)

    response = await client.get(
        f"/api/admin/subscriptions/{target.id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_admin_list_subscriptions_marks_test_accounts_using_username_or_email(
    client: AsyncClient, db_session: Session
):
    admin = await create_user(db_session, "admin_sub_filter_5", "admin_sub_filter_5@example.com", is_superuser=True)
    demo_user = await create_user(db_session, "demo_writer_1", "demo_writer_1@zenstory.ai")
    normal_user = await create_user(db_session, "normal_writer_1", "normal_writer_1@zenstory.ai")

    token = await login_user(client, admin.username)
    response = await client.get(
        "/api/admin/subscriptions?page=1&page_size=100",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    users_by_id = {item["user_id"]: item for item in response.json()["items"]}

    assert users_by_id[demo_user.id]["is_test_account"] is True
    assert users_by_id[normal_user.id]["is_test_account"] is False


@pytest.mark.integration
async def test_admin_list_subscriptions_invalid_status_returns_400(
    client: AsyncClient, db_session: Session
):
    admin = await create_user(db_session, "admin_sub_filter_6", "admin_sub_filter_6@example.com", is_superuser=True)
    token = await login_user(client, admin.username)

    response = await client.get(
        "/api/admin/subscriptions?status=unknown&page=1&page_size=20",
        headers=auth_headers(token),
    )

    assert response.status_code == 400


@pytest.mark.integration
async def test_admin_list_subscriptions_status_alias_past_due_matches_expired_filter(
    client: AsyncClient, db_session: Session
):
    admin = await create_user(db_session, "admin_sub_filter_7", "admin_sub_filter_7@example.com", is_superuser=True)
    past_due_user = await create_user(db_session, "past_due_user_2", "past_due_user_2@example.com")
    active_user = await create_user(db_session, "active_user_4", "active_user_4@example.com")

    free_plan = get_or_create_plan(db_session, "free")
    now = utcnow()
    db_session.add(UserSubscription(
        user_id=past_due_user.id,
        plan_id=free_plan.id,
        status="past_due",
        current_period_start=now - timedelta(days=10),
        current_period_end=now + timedelta(days=10),
        cancel_at_period_end=False,
    ))
    db_session.add(UserSubscription(
        user_id=active_user.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=10),
        cancel_at_period_end=False,
    ))
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get(
        "/api/admin/subscriptions?status=past_due&page=1&page_size=100",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    users_by_id = {item["user_id"]: item for item in response.json()["items"]}
    assert past_due_user.id in users_by_id
    assert users_by_id[past_due_user.id]["status"] == "expired"
    assert active_user.id not in users_by_id


@pytest.mark.integration
async def test_admin_get_user_subscription_returns_plan_and_quota_when_present(
    client: AsyncClient, db_session: Session
):
    admin = await create_user(db_session, "admin_sub_filter_8", "admin_sub_filter_8@example.com", is_superuser=True)
    target = await create_user(db_session, "user_with_sub_1", "user_with_sub_1@example.com")
    free_plan = get_or_create_plan(db_session, "free")
    now = utcnow()

    db_session.add(UserSubscription(
        user_id=target.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    ))
    db_session.add(
        UsageQuota(
            user_id=target.id,
            period_start=now - timedelta(days=1),
            period_end=now + timedelta(days=29),
            ai_conversations_used=3,
            material_uploads_used=1,
            material_decompositions_used=0,
            custom_skills_used=0,
            inspiration_copies_used=0,
            last_reset_at=now - timedelta(days=1),
            metadata={},
        )
    )
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get(
        f"/api/admin/subscriptions/{target.id}",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["subscription"]["user_id"] == target.id
    assert payload["plan"]["name"] == "free"
    assert payload["quota"]["user_id"] == target.id


@pytest.mark.integration
async def test_admin_update_subscription_user_not_found_returns_404(
    client: AsyncClient, db_session: Session
):
    admin = await create_user(db_session, "admin_sub_filter_9", "admin_sub_filter_9@example.com", is_superuser=True)
    token = await login_user(client, admin.username)

    response = await client.put(
        "/api/admin/subscriptions/non-existent-user-id",
        headers=auth_headers(token),
        json={"status": "cancelled"},
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_admin_update_subscription_without_any_changes_returns_400(
    client: AsyncClient, db_session: Session
):
    admin = await create_user(db_session, "admin_sub_filter_10", "admin_sub_filter_10@example.com", is_superuser=True)
    target = await create_user(db_session, "user_no_change_1", "user_no_change_1@example.com")
    free_plan = get_or_create_plan(db_session, "free")
    now = utcnow()
    db_session.add(UserSubscription(
        user_id=target.id,
        plan_id=free_plan.id,
        status="active",
        current_period_start=now,
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    ))
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.put(
        f"/api/admin/subscriptions/{target.id}",
        headers=auth_headers(token),
        json={},
    )

    assert response.status_code == 400
