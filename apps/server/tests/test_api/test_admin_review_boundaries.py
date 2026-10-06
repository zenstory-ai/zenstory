from datetime import timedelta
from unittest.mock import patch

import pytest

from api.admin.plans import update_plan
from api.admin.schemas import PlanUpdateRequest
from config.datetime_utils import beijing_date, utcnow
from main import app
from models import User
from models.points import CheckInRecord
from models.subscription import AdminAuditLog, SubscriptionPlan
from services.core.auth_service import get_current_superuser


@pytest.fixture
def admin_boundary(client, db_session):
    admin = User(username="review_admin", email="review-admin@example.com", hashed_password="stored-admin-hash", is_superuser=True, email_verified=True)
    target = User(username="review_writer", email="review-writer@example.com", hashed_password="stored-writer-hash", email_verified=True)
    plan = SubscriptionPlan(name="review-plan", display_name="Review", features={"max_projects": 3}, price_monthly_cents=100)
    db_session.add_all([admin, target, plan])
    db_session.commit()
    app.dependency_overrides[get_current_superuser] = lambda: admin
    yield admin, target, plan
    app.dependency_overrides.pop(get_current_superuser, None)


async def test_admin_user_crud_never_serializes_password_hash(client, admin_boundary):
    _, target, _ = admin_boundary
    responses = [
        await client.get("/api/admin/users"),
        await client.get(f"/api/admin/users/{target.id}"),
        await client.put(f"/api/admin/users/{target.id}", json={"username": "renamed_writer"}),
        await client.delete(f"/api/admin/users/{target.id}"),
    ]
    for response in responses:
        assert response.status_code == 200
        payload = response.json()
        rows = payload["items"] if "items" in payload else [payload]
        for row in rows:
            assert "hashed_password" not in row
            assert "stored-" not in response.text


@pytest.mark.parametrize("change", [{"is_active": False}, {"is_superuser": False}])
async def test_admin_cannot_deactivate_or_demote_self(client, db_session, admin_boundary, change):
    admin, _, _ = admin_boundary
    response = await client.put(f"/api/admin/users/{admin.id}", json=change)
    assert response.status_code in (400, 409)
    db_session.refresh(admin)
    assert admin.is_active and admin.is_superuser


@pytest.mark.parametrize("change", [{"username": "  "}, {"username": None}, {"email": "invalid"}, {"email": None}])
async def test_admin_user_identity_validation(client, admin_boundary, change):
    _, target, _ = admin_boundary
    response = await client.put(f"/api/admin/users/{target.id}", json=change)
    assert response.status_code == 422


async def test_admin_user_duplicate_identity_is_stable_conflict(client, admin_boundary):
    admin, target, _ = admin_boundary
    response = await client.put(f"/api/admin/users/{target.id}", json={"username": admin.username})
    assert response.status_code == 409


async def test_admin_user_email_conflict_is_case_insensitive(client, admin_boundary):
    admin, target, _ = admin_boundary
    response = await client.put(
        f"/api/admin/users/{target.id}",
        json={"email": admin.email.upper()},
    )
    assert response.status_code == 409


async def test_admin_user_email_is_stored_canonically(client, db_session, admin_boundary):
    _, target, _ = admin_boundary
    response = await client.put(
        f"/api/admin/users/{target.id}",
        json={"email": "New.Writer@Example.com"},
    )
    assert response.status_code == 200
    db_session.refresh(target)
    assert target.email == "new.writer@example.com"


@pytest.mark.parametrize("change", [
    {"price_monthly_cents": -1}, {"price_yearly_cents": -1},
    {"features": {"ai_conversations_per_day": "ten"}},
    {"features": {"max_projects": -2}},
    {"features": {"max_projects": True}},
    {"features": {"unknown_quota": 5}},
])
async def test_admin_rejects_invalid_plan_features_and_prices(client, admin_boundary, change):
    _, _, plan = admin_boundary
    response = await client.put(f"/api/admin/plans/{plan.id}", json=change)
    assert response.status_code == 422


def test_admin_plan_audit_failure_rolls_back_plan(db_session, admin_boundary):
    admin, _, plan = admin_boundary
    with patch("api.admin.plans.admin_audit_service.log_action", side_effect=RuntimeError("audit unavailable")):
        with pytest.raises(RuntimeError, match="audit unavailable"):
            update_plan(plan.id, PlanUpdateRequest(price_monthly_cents=200), http_request=None, current_user=admin, session=db_session)
    db_session.refresh(plan)
    assert plan.price_monthly_cents == 100


async def test_admin_audit_returns_actor_and_exact_filtered_total(client, db_session, admin_boundary):
    admin, _, _ = admin_boundary
    db_session.add_all([
        AdminAuditLog(admin_user_id=admin.id, action="update_user", resource_type="user", resource_id=f"user-{index}")
        for index in range(3)
    ])
    db_session.add(AdminAuditLog(admin_user_id=admin.id, action="create_plan", resource_type="plan"))
    db_session.commit()
    response = await client.get("/api/admin/audit-logs", params={"page_size": 2, "resource_type": "user", "action": "update"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert len(payload["items"]) == 2
    assert all(item["admin_id"] == admin.id and item["admin_name"] == admin.username for item in payload["items"])


async def test_admin_week_checkins_covers_seven_calendar_days(client, db_session, admin_boundary):
    admin, _, _ = admin_boundary
    now = utcnow()
    db_session.add_all([
        CheckInRecord(
            user_id=admin.id,
            check_in_date=beijing_date(now - timedelta(days=day)),
            created_at=now - timedelta(days=day),
            points_earned=1,
        )
        for day in range(8)
    ])
    db_session.commit()
    response = await client.get("/api/admin/check-in/stats")
    assert response.status_code == 200
    assert response.json()["week_total"] == 7
