"""Prompt admin validation and optimistic concurrency regressions."""

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import SystemPromptConfig, User
from models.subscription import AdminAuditLog
from services.core.auth_service import hash_password


async def _create_admin(db_session: Session) -> User:
    user = User(
        username="prompt_lifecycle_admin",
        email="prompt-lifecycle-admin@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        is_superuser=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _headers(client: AsyncClient, db_session: Session) -> dict[str, str]:
    admin = await _create_admin(db_session)
    response = await client.post(
        "/api/auth/login",
        data={"username": admin.username, "password": "password123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.mark.integration
@pytest.mark.parametrize("field", ["role_definition", "capabilities"])
async def test_prompt_required_text_rejects_whitespace(
    client: AsyncClient,
    db_session: Session,
    field: str,
):
    headers = await _headers(client, db_session)
    payload = {"role_definition": "Writer", "capabilities": "Write"}
    payload[field] = "   \n\t"

    response = await client.put("/api/admin/prompts/novel", headers=headers, json=payload)

    assert response.status_code == 422


@pytest.mark.integration
async def test_prompt_update_uses_expected_version_compare_and_swap(
    client: AsyncClient,
    db_session: Session,
):
    headers = await _headers(client, db_session)
    prompt = SystemPromptConfig(
        project_type="novel",
        role_definition="Original role",
        capabilities="Original capabilities",
        version=1,
    )
    db_session.add(prompt)
    db_session.commit()

    first = await client.put(
        "/api/admin/prompts/novel",
        headers=headers,
        json={
            "role_definition": "First role",
            "capabilities": "First capabilities",
            "expected_version": 1,
        },
    )
    stale = await client.put(
        "/api/admin/prompts/novel",
        headers=headers,
        json={
            "role_definition": "Stale role",
            "capabilities": "Stale capabilities",
            "expected_version": 1,
        },
    )

    assert first.status_code == 200
    assert first.json()["version"] == 2
    assert stale.status_code == 409
    assert stale.json()["error_detail"]["current_version"] == 2
    db_session.expire_all()
    stored = db_session.exec(
        select(SystemPromptConfig).where(SystemPromptConfig.project_type == "novel")
    ).one()
    assert stored.role_definition == "First role"


@pytest.mark.integration
async def test_inactive_prompt_remains_visible_in_admin_history(
    client: AsyncClient,
    db_session: Session,
):
    headers = await _headers(client, db_session)
    db_session.add(
        SystemPromptConfig(
            project_type="screenplay",
            role_definition="Archived role",
            capabilities="Archived capabilities",
            is_active=False,
        )
    )
    db_session.commit()

    response = await client.get("/api/admin/prompts/screenplay", headers=headers)

    assert response.status_code == 200
    assert response.json()["is_active"] is False


@pytest.mark.integration
async def test_prompt_mutations_create_audit_records(
    client: AsyncClient,
    db_session: Session,
):
    headers = await _headers(client, db_session)

    created = await client.put(
        "/api/admin/prompts/short",
        headers=headers,
        json={"role_definition": "Short role", "capabilities": "Short capabilities"},
    )
    assert created.status_code == 200
    updated = await client.put(
        "/api/admin/prompts/short",
        headers=headers,
        json={
            "role_definition": "Updated short role",
            "capabilities": "Updated short capabilities",
            "expected_version": 1,
        },
    )
    assert updated.status_code == 200
    deleted = await client.delete("/api/admin/prompts/short", headers=headers)
    assert deleted.status_code == 200

    db_session.expire_all()
    actions = db_session.exec(
        select(AdminAuditLog.action)
        .where(AdminAuditLog.resource_type == "system_prompt")
        .order_by(AdminAuditLog.created_at)
    ).all()
    assert actions == ["create_prompt", "update_prompt", "delete_prompt"]


@pytest.mark.integration
async def test_prompt_create_rolls_back_when_audit_write_fails(
    client: AsyncClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    headers = await _headers(client, db_session)

    def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(
        "api.admin.prompts.admin_audit_service.log_action",
        fail_audit,
    )

    response = await client.put(
        "/api/admin/prompts/screenplay",
        headers=headers,
        json={
            "role_definition": "Screen role",
            "capabilities": "Screen capabilities",
        },
    )
    assert response.status_code == 500
    db_session.expire_all()
    stored = db_session.exec(
        select(SystemPromptConfig).where(
            SystemPromptConfig.project_type == "screenplay"
        )
    ).first()
    assert stored is None
