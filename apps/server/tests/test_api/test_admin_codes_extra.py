"""
Additional tests for admin redemption code endpoints.
"""

from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import User
from models.subscription import AdminAuditLog, RedemptionCode, SubscriptionPlan
from services.core.auth_service import hash_password
from services.subscription.redemption_service import redemption_service


async def create_user(
    db_session: Session,
    username: str,
    email: str,
    password: str = "password123",
    is_superuser: bool = False,
) -> User:
    """Create and persist a user for tests."""
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
    """Login and return an access token."""
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_headers(token: str) -> dict[str, str]:
    """Authorization headers helper."""
    return {"Authorization": f"Bearer {token}"}


def get_or_create_plan(db_session: Session, name: str) -> SubscriptionPlan:
    """Get or create subscription plan for tests."""
    plan = db_session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == name)).first()
    if plan:
        return plan

    plan = SubscriptionPlan(
        name=name,
        display_name=name.title(),
        display_name_en=name.title(),
        price_monthly_cents=0,
        price_yearly_cents=0,
        features={"ai_conversations_per_day": 10},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.integration
async def test_create_code_normalizes_single_alias_and_forces_max_uses_one(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    """single alias should map to single_use and max_uses should be 1."""
    monkeypatch.setenv("REDEMPTION_CODE_HMAC_SECRET", "a" * 32)
    admin = await create_user(db_session, "admin_code_extra_1", "admin_code_extra_1@example.com", is_superuser=True)
    get_or_create_plan(db_session, "pro")

    token = await login_user(client, admin.username)
    response = await client.post(
        "/api/admin/codes",
        headers=auth_headers(token),
        json={
            "tier": "pro",
            "duration_days": 30,
            "code_type": "single",
            "max_uses": 99,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["code_type"] == "single_use"
    assert data["max_uses"] == 1

    created = db_session.exec(select(RedemptionCode).where(RedemptionCode.id == data["id"])).first()
    assert created is not None
    assert created.code_type == "single_use"
    assert created.max_uses == 1


@pytest.mark.integration
async def test_create_codes_batch_normalizes_multi_alias(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    """multi alias should map to multi_use in batch creation."""
    monkeypatch.setenv("REDEMPTION_CODE_HMAC_SECRET", "b" * 32)
    admin = await create_user(db_session, "admin_code_extra_2", "admin_code_extra_2@example.com", is_superuser=True)
    get_or_create_plan(db_session, "pro")

    token = await login_user(client, admin.username)
    response = await client.post(
        "/api/admin/codes/batch",
        headers=auth_headers(token),
        json={
            "tier": "pro",
            "duration_days": 90,
            "count": 2,
            "code_type": "multi",
            "max_uses": 5,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["created"] == 2
    assert len(data["codes"]) == 2
    assert data["code_type"] == "multi_use"
    assert data["max_uses"] == 5

    rows = db_session.exec(select(RedemptionCode).where(RedemptionCode.created_by == admin.id)).all()
    assert len(rows) == 2
    assert {row.code_type for row in rows} == {"multi_use"}
    assert all(row.max_uses == 5 for row in rows)

    audit = db_session.exec(
        select(AdminAuditLog).where(AdminAuditLog.action == "create_codes_batch")
    ).one()
    assert audit.new_value["count"] == 2
    assert audit.new_value["max_uses"] == 5
    assert sorted(audit.new_value["code_ids"]) == sorted(row.id for row in rows)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/admin/codes", {"tier": "pro", "duration_days": 30, "code_type": "multi_use"}),
        ("/api/admin/codes", {"tier": "pro", "duration_days": 30, "code_type": "multi"}),
        ("/api/admin/codes/batch", {"tier": "pro", "duration_days": 30, "count": 3, "code_type": "multi_use"}),
        (
            "/api/admin/codes/batch",
            {"tier": "pro", "duration_days": 30, "count": 3, "code_type": "multi_use", "max_uses": None},
        ),
    ],
)
async def test_multi_use_codes_require_max_uses(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    payload: dict,
):
    """A multi-use code without a use limit would be redeemable by anyone, forever."""
    monkeypatch.setenv("REDEMPTION_CODE_HMAC_SECRET", "c" * 32)
    admin = await create_user(db_session, "admin_code_multi_limit", "admin_code_multi_limit@example.com", is_superuser=True)
    get_or_create_plan(db_session, "pro")
    token = await login_user(client, admin.username)

    response = await client.post(path, headers=auth_headers(token), json=payload)

    assert response.status_code == 422
    assert db_session.exec(select(RedemptionCode)).all() == []


@pytest.mark.integration
async def test_batch_single_use_codes_get_one_use_and_ignore_max_uses(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("REDEMPTION_CODE_HMAC_SECRET", "d" * 32)
    admin = await create_user(db_session, "admin_code_batch_single", "admin_code_batch_single@example.com", is_superuser=True)
    get_or_create_plan(db_session, "pro")
    token = await login_user(client, admin.username)

    response = await client.post(
        "/api/admin/codes/batch",
        headers=auth_headers(token),
        json={"tier": "pro", "duration_days": 30, "count": 2, "code_type": "single_use", "max_uses": 50},
    )

    assert response.status_code == 200
    assert response.json()["max_uses"] == 1
    rows = db_session.exec(select(RedemptionCode)).all()
    assert [row.max_uses for row in rows] == [1, 1]


def test_resolve_max_uses_rejects_unlimited_multi_use():
    """The service guard backs up the schema for callers that bypass it."""
    assert redemption_service.resolve_max_uses("single_use", None) == 1
    assert redemption_service.resolve_max_uses("single_use", 40) == 1
    assert redemption_service.resolve_max_uses("multi_use", 7) == 7
    for bad in (None, 0):
        with pytest.raises(APIException) as exc_info:
            redemption_service.resolve_max_uses("multi_use", bad)
        assert exc_info.value.status_code == 400
        assert exc_info.value.error_code == ErrorCode.VALIDATION_ERROR


@pytest.mark.integration
async def test_list_codes_filters_by_tier_and_active_with_pagination(
    client: AsyncClient,
    db_session: Session,
):
    """List endpoint should respect tier/is_active filters and pagination."""
    admin = await create_user(db_session, "admin_code_extra_3", "admin_code_extra_3@example.com", is_superuser=True)
    get_or_create_plan(db_session, "pro")
    get_or_create_plan(db_session, "free")

    older = RedemptionCode(
        code="ERG-TEST-OLDER-001",
        code_type="single_use",
        tier="pro",
        duration_days=30,
        max_uses=1,
        created_by=admin.id,
        is_active=True,
        created_at=utcnow() - timedelta(days=2),
    )
    newer = RedemptionCode(
        code="ERG-TEST-NEWER-001",
        code_type="single_use",
        tier="pro",
        duration_days=30,
        max_uses=1,
        created_by=admin.id,
        is_active=True,
        created_at=utcnow() - timedelta(days=1),
    )
    inactive = RedemptionCode(
        code="ERG-TEST-INACTIVE-001",
        code_type="single_use",
        tier="pro",
        duration_days=30,
        max_uses=1,
        created_by=admin.id,
        is_active=False,
    )
    other_tier = RedemptionCode(
        code="ERG-TEST-FREE-001",
        code_type="single_use",
        tier="free",
        duration_days=30,
        max_uses=1,
        created_by=admin.id,
        is_active=True,
    )
    db_session.add(older)
    db_session.add(newer)
    db_session.add(inactive)
    db_session.add(other_tier)
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get(
        "/api/admin/codes?page=1&page_size=1&tier=pro&is_active=true",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert data["page"] == 1
    assert data["page_size"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["id"] == newer.id


@pytest.mark.integration
async def test_get_code_details_success(client: AsyncClient, db_session: Session):
    """Superuser can fetch code details by id."""
    admin = await create_user(db_session, "admin_code_extra_4", "admin_code_extra_4@example.com", is_superuser=True)
    get_or_create_plan(db_session, "pro")
    code = RedemptionCode(
        code="ERG-DETAIL-001",
        code_type="single_use",
        tier="pro",
        duration_days=30,
        max_uses=1,
        created_by=admin.id,
        is_active=True,
    )
    db_session.add(code)
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.get(f"/api/admin/codes/{code.id}", headers=auth_headers(token))

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == code.id
    assert data["code"] == "ERG-DETAIL-001"


@pytest.mark.integration
async def test_update_code_updates_active_and_notes(client: AsyncClient, db_session: Session):
    """Update endpoint should persist is_active and notes fields."""
    admin = await create_user(db_session, "admin_code_extra_5", "admin_code_extra_5@example.com", is_superuser=True)
    get_or_create_plan(db_session, "pro")
    code = RedemptionCode(
        code="ERG-UPDATE-001",
        code_type="single_use",
        tier="pro",
        duration_days=30,
        max_uses=1,
        created_by=admin.id,
        is_active=True,
        notes=None,
    )
    db_session.add(code)
    db_session.commit()

    token = await login_user(client, admin.username)
    response = await client.put(
        f"/api/admin/codes/{code.id}",
        headers=auth_headers(token),
        json={"is_active": False, "notes": "disable after campaign"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["is_active"] is False
    assert payload["notes"] == "disable after campaign"

    db_session.refresh(code)
    assert code.is_active is False
    assert code.notes == "disable after campaign"


@pytest.mark.integration
async def test_create_code_returns_429_when_rate_limited(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    """Rate-limit branch should return 429."""
    monkeypatch.setattr("api.admin.codes.check_rate_limit", lambda *_args, **_kwargs: (False, 0))

    admin = await create_user(db_session, "admin_code_extra_6", "admin_code_extra_6@example.com", is_superuser=True)
    get_or_create_plan(db_session, "pro")

    token = await login_user(client, admin.username)
    response = await client.post(
        "/api/admin/codes",
        headers=auth_headers(token),
        json={"tier": "pro", "duration_days": 30, "code_type": "single_use"},
    )

    assert response.status_code == 429
    assert response.json()["error_code"] == "ERR_AUTH_RATE_LIMIT_EXCEEDED"
    assert response.json()["error_detail"] == "Rate limit exceeded"


@pytest.mark.integration
async def test_create_code_returns_400_for_invalid_tier(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    """create_code should validate tier existence."""
    monkeypatch.setenv("REDEMPTION_CODE_HMAC_SECRET", "c" * 32)
    admin = await create_user(db_session, "admin_code_extra_7", "admin_code_extra_7@example.com", is_superuser=True)
    token = await login_user(client, admin.username)

    response = await client.post(
        "/api/admin/codes",
        headers=auth_headers(token),
        json={"tier": "ghost-tier", "duration_days": 30, "code_type": "single_use"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "ERR_VALIDATION_ERROR"
    assert response.json()["error_detail"] == "Invalid tier: ghost-tier"


@pytest.mark.integration
@pytest.mark.parametrize(
    "method,path_template,payload",
    [
        ("GET", "/api/admin/codes", None),
        ("GET", "/api/admin/codes/{code_id}", None),
        ("PUT", "/api/admin/codes/{code_id}", {"notes": "forbidden"}),
        (
            "POST",
            "/api/admin/codes",
            {"tier": "pro", "duration_days": 30, "code_type": "single_use"},
        ),
    ],
)
async def test_admin_code_endpoints_forbidden_for_non_superuser(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path_template: str,
    payload: dict | None,
):
    """Admin code endpoints should reject non-superusers."""
    monkeypatch.setenv("REDEMPTION_CODE_HMAC_SECRET", "d" * 32)

    admin = await create_user(db_session, "admin_code_extra_8", "admin_code_extra_8@example.com", is_superuser=True)
    normal_user = await create_user(db_session, "normal_code_extra_1", "normal_code_extra_1@example.com")
    get_or_create_plan(db_session, "pro")

    code = RedemptionCode(
        code="ERG-FORBIDDEN-001",
        code_type="single_use",
        tier="pro",
        duration_days=30,
        max_uses=1,
        created_by=admin.id,
        is_active=True,
    )
    db_session.add(code)
    db_session.commit()

    token = await login_user(client, normal_user.username)
    path = path_template.format(code_id=code.id)
    response = await client.request(method, path, headers=auth_headers(token), json=payload)

    assert response.status_code == 403
    assert response.json()["detail"] == "ERR_NOT_AUTHORIZED"
