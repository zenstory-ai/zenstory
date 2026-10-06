"""Actual Agent key management contracts, independent of key-authenticated tools."""

from datetime import datetime
from uuid import uuid4

import pytest
from sqlmodel import select

from models import User
from models.agent_api_key import AgentApiKey
from services.core.auth_service import create_access_token


def seed_owner(session):
    user_id = uuid4().hex[:16]
    user = User(
        id=user_id,
        username=user_id,
        email=user_id + "@example.test",
        hashed_password="unused",
        email_verified=True,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def seed_key(session, user_id, key_id, *, active=True):
    key = AgentApiKey(
        id=key_id,
        user_id=user_id,
        name="contract-key",
        key_prefix="zs_test",
        key_hash=key_id + "-hash",
        scopes=["read"],
        is_active=active,
        description="original",
        project_ids=["project-a"],
        created_at=datetime(2026, 4, 8, 9),
        updated_at=datetime(2026, 4, 8, 9),
    )
    session.add(key)
    return key


@pytest.mark.asyncio
@pytest.mark.parametrize("active_only", [False, True])
async def test_key_offset_pages_have_total_order_for_timestamp_ties(client, db_session, active_only):
    owner = seed_owner(db_session)
    ids = [owner.id + "-" + suffix for suffix in ("a", "f", "b", "e", "c", "d")]
    for key_id in ids:
        seed_key(db_session, owner.id, key_id, active=not key_id.endswith("-c"))
    db_session.commit()
    expected = sorted([key_id for key_id in ids if not active_only or not key_id.endswith("-c")], reverse=True)
    headers = {"Authorization": "Bearer " + create_access_token({"sub": owner.id})}
    observed = []
    for offset in range(0, len(expected), 2):
        params: dict[str, int | str] = {"offset": offset, "limit": 2}
        if active_only:
            params["is_active"] = "true"
        response = await client.get("/api/v1/agent-api-keys", headers=headers, params=params)
        assert response.status_code == 200
        assert response.json()["total"] == len(expected)
        observed.extend(item["id"] for item in response.json()["keys"])
    assert observed == expected
    assert len(set(observed)) == len(expected)
    repeat = await client.get(
        "/api/v1/agent-api-keys",
        headers=headers,
        params={"offset": 0, "limit": 2, **({"is_active": "true"} if active_only else {})},
    )
    assert [item["id"] for item in repeat.json()["keys"]] == expected[:2]


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["name", "scopes", "is_active"])
async def test_patch_rejects_explicit_null_for_nonnullable_fields(client, db_session, field):
    owner = seed_owner(db_session)
    key = seed_key(db_session, owner.id, owner.id + "-null")
    db_session.commit()
    original = key.model_dump()
    response = await client.put(
        "/api/v1/agent-api-keys/" + key.id,
        headers={"Authorization": "Bearer " + create_access_token({"sub": owner.id})},
        json={field: None, "description": "must-not-be-committed"},
    )
    assert response.status_code == 422
    db_session.expire_all()
    assert db_session.exec(select(AgentApiKey).where(AgentApiKey.id == key.id)).one().model_dump() == original


@pytest.mark.asyncio
async def test_patch_preserves_nullable_clears_and_omitted_fields(client, db_session):
    owner = seed_owner(db_session)
    key = seed_key(db_session, owner.id, owner.id + "-clear")
    db_session.commit()
    response = await client.put(
        "/api/v1/agent-api-keys/" + key.id,
        headers={"Authorization": "Bearer " + create_access_token({"sub": owner.id})},
        json={"description": None, "project_ids": None},
    )
    assert response.status_code == 200
    assert response.json()["description"] is None
    assert response.json()["project_ids"] is None
    assert response.json()["name"] == "contract-key"
    assert response.json()["scopes"] == ["read"]
    assert response.json()["is_active"] is True
