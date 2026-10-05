"""What paid users are sold matches what the backend enforces and reports."""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from config.datetime_utils import utcnow
from models import User
from models.subscription import SubscriptionHistory, SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password

# Production pro features before the 20261005_180100 backfill.
PRODUCTION_PRO_FEATURES = {
    "max_projects": -1,
    "custom_prompts": True,
    "export_formats": ["txt"],
    "material_uploads": 5,
    "context_window_tokens": 16384,
    "file_versions_per_file": 100,
    "material_decompositions": 5,
    "ai_conversations_per_day": -1,
    "materials_library_access": True,
}


def _user(db_session: Session, name: str, *, admin: bool = False) -> User:
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


async def _headers(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login", data={"username": username, "password": "password123"}
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _plan(db_session: Session, name: str, price: int, features: dict) -> SubscriptionPlan:
    plan = SubscriptionPlan(
        name=name,
        display_name=name,
        display_name_en=name,
        price_monthly_cents=price,
        price_yearly_cents=price * 10,
        features=features,
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


def _subscribe(db_session: Session, user: User, plan: SubscriptionPlan, *, days: int) -> None:
    now = utcnow()
    db_session.add(
        UserSubscription(
            user_id=user.id,
            plan_id=plan.id,
            status="active",
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=days),
        )
    )
    db_session.commit()


@pytest.mark.integration
async def test_pro_quota_limits_match_catalog_entitlements(
    client: AsyncClient, db_session: Session
):
    user = _user(db_session, "pro_consistency")
    _plan(db_session, "free", 0, {})
    pro = _plan(db_session, "pro", 4900, dict(PRODUCTION_PRO_FEATURES))
    _subscribe(db_session, user, pro, days=30)
    headers = await _headers(client, user.username)

    catalog = (await client.get("/api/v1/subscription/catalog")).json()
    entitlements = next(t for t in catalog["tiers"] if t["name"] == "pro")["entitlements"]
    quota = (await client.get("/api/v1/subscription/quota", headers=headers)).json()

    assert quota["skill_creates"]["limit"] == entitlements["custom_skills_limit"] == 20
    assert quota["inspiration_copies"]["limit"] == entitlements["inspiration_copies_monthly"] == 100
    assert quota["material_uploads"]["limit"] == entitlements["material_uploads_monthly"]
    assert (
        quota["material_decompositions"]["limit"]
        == entitlements["material_decompositions_monthly"]
    )
    assert quota["projects"]["limit"] == entitlements["active_projects_limit"] == -1
    assert quota["ai_conversations"]["limit"] == -1
    assert entitlements["agent_runs_monthly"] == -1
    # Unimplemented perks only appear as deprecated neutral values.
    assert entitlements["context_tokens_limit"] == 0
    assert entitlements["priority_queue_level"] == "standard"


@pytest.mark.integration
async def test_custom_skill_limit_counts_owned_skills(client: AsyncClient, db_session: Session):
    user = _user(db_session, "skill_slots")
    _plan(db_session, "free", 0, {"custom_skills": 2})
    headers = await _headers(client, user.username)
    body = {"name": "s", "description": "d", "triggers": ["t"], "instructions": "do it"}

    first = await client.post("/api/v1/skills", headers=headers, json=body)
    second = await client.post("/api/v1/skills", headers=headers, json=body)
    assert first.status_code == second.status_code == 200
    blocked = await client.post("/api/v1/skills", headers=headers, json=body)
    assert blocked.status_code == 402

    quota = (await client.get("/api/v1/subscription/quota", headers=headers)).json()
    assert quota["skill_creates"]["used"] == 2
    assert quota["skill_creates"]["limit"] == 2

    deleted = await client.delete(f"/api/v1/skills/{first.json()['id']}", headers=headers)
    assert deleted.status_code == 200
    again = await client.post("/api/v1/skills", headers=headers, json=body)
    assert again.status_code == 200


@pytest.mark.integration
async def test_admin_cannot_create_free_tier_codes(
    client: AsyncClient, db_session: Session, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("REDEMPTION_CODE_HMAC_SECRET", "c" * 32)
    admin = _user(db_session, "code_admin_free", admin=True)
    _plan(db_session, "free", 0, {})
    headers = await _headers(client, admin.username)

    for path, payload in (
        ("/api/admin/codes", {"tier": "free", "duration_days": 7, "code_type": "single_use"}),
        (
            "/api/admin/codes/batch",
            {"tier": "free", "duration_days": 7, "count": 2, "code_type": "single_use"},
        ),
    ):
        response = await client.post(path, headers=headers, json=payload)
        assert response.status_code == 400, path
        assert response.json()["detail"] == "Redemption codes cannot grant the free tier"


@pytest.mark.integration
async def test_dashboard_counts_only_running_paid_subscriptions(
    client: AsyncClient, db_session: Session
):
    admin = _user(db_session, "dash_admin", admin=True)
    free = _plan(db_session, "free", 0, {})
    pro = _plan(db_session, "pro", 4900, {})
    for index in range(3):
        _subscribe(db_session, _user(db_session, f"dash_free_{index}"), free, days=36500)
    _subscribe(db_session, _user(db_session, "dash_pro_live"), pro, days=10)
    _subscribe(db_session, _user(db_session, "dash_pro_lapsed"), pro, days=-2)
    headers = await _headers(client, admin.username)

    stats = (await client.get("/api/admin/dashboard/stats", headers=headers)).json()
    assert stats["active_subscriptions"] == 1
    assert stats["pro_users"] == 1


@pytest.mark.integration
async def test_upgrade_conversion_separates_paid_from_granted(
    client: AsyncClient, db_session: Session
):
    admin = _user(db_session, "conv_admin", admin=True)
    customer = _user(db_session, "conv_customer")
    now = utcnow()
    for source, upgrade_source in (
        ("zpay", "pricing_page"),
        ("zpay", None),
        ("admin_update", None),
        ("points_redemption", None),
        ("redemption_code", "billing_header_upgrade"),
    ):
        metadata = {"source": source}
        if upgrade_source:
            metadata["upgrade_source"] = upgrade_source
        db_session.add(
            SubscriptionHistory(
                user_id=customer.id,
                action="upgraded",
                plan_name="pro",
                start_date=now,
                end_date=now + timedelta(days=30),
                event_metadata=metadata,
            )
        )
    db_session.commit()
    headers = await _headers(client, admin.username)

    payload = (
        await client.get("/api/admin/dashboard/upgrade-conversion", headers=headers)
    ).json()
    assert payload["total_conversions"] == 5
    assert payload["paid_conversions"] == 2
    channels = {item["channel"]: item for item in payload["channels"]}
    assert channels["zpay"] == {"channel": "zpay", "conversions": 2, "paid": True}
    assert channels["admin_update"]["paid"] is False
    assert channels["points_redemption"]["conversions"] == 1
    assert channels["redemption_code"]["paid"] is False
