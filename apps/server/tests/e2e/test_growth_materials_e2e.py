"""
Request-driven growth and materials e2e workflows.

These tests extend the server e2e skeleton with points/referral and materials
library flows while staying deterministic and provider-free.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import File, User
from models.material_models import Character, IngestionJob, Novel, WorldView
from models.referral import UserReward, UserStats
from models.subscription import SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password
from services.features.points_service import POINTS_CHECK_IN, POINTS_REFERRAL

pytestmark = pytest.mark.e2e


def _identity(prefix: str) -> tuple[str, str]:
    suffix = uuid4().hex[:8]
    username = f"{prefix}_{suffix}"
    return username, f"{username}@example.com"


async def _create_user(
    db_session: Session,
    *,
    prefix: str,
    password: str = "password123",
) -> User:
    username, email = _identity(prefix)
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _login(client: AsyncClient, *, identifier: str, password: str = "password123") -> dict:
    response = await client.post(
        "/api/auth/login",
        data={"username": identifier, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _attach_materials_access_subscription(db_session: Session, *, user_id: str) -> None:
    plan = SubscriptionPlan(
        name="pro",
        display_name="Pro",
        display_name_en="Pro",
        price_monthly_cents=4900,
        price_yearly_cents=39900,
        features={
            "materials_library_access": True,
            "material_uploads": 5,
            "material_decompositions": 5,
            "max_projects": 3,
        },
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)

    now = datetime.utcnow()
    db_session.add(
        UserSubscription(
            user_id=user_id,
            plan_id=plan.id,
            status="active",
            current_period_start=now,
            current_period_end=now + timedelta(days=30),
        )
    )
    db_session.commit()


async def _create_project(client: AsyncClient, access_token: str, *, name: str) -> dict:
    response = await client.post(
        "/api/v1/projects",
        json={"name": name, "project_type": "novel"},
        headers=_auth_headers(access_token),
    )
    assert response.status_code == 200
    return response.json()


@pytest.mark.asyncio
async def test_points_and_referral_roundtrip(client: AsyncClient, db_session: Session):
    user = await _create_user(db_session, prefix="growth_flow")
    login_payload = await _login(client, identifier=user.username)
    headers = _auth_headers(login_payload["access_token"])

    config_response = await client.get("/api/v1/points/config")
    assert config_response.status_code == 200
    assert config_response.json()["check_in"] == POINTS_CHECK_IN
    assert config_response.json()["referral"] == POINTS_REFERRAL

    status_before = await client.get("/api/v1/points/check-in/status", headers=headers)
    assert status_before.status_code == 200
    assert status_before.json()["checked_in"] is False

    check_in_response = await client.post("/api/v1/points/check-in", headers=headers)
    assert check_in_response.status_code == 200
    assert check_in_response.json()["points_earned"] == POINTS_CHECK_IN
    assert check_in_response.json()["streak_days"] == 1

    balance_response = await client.get("/api/v1/points/balance", headers=headers)
    assert balance_response.status_code == 200
    assert balance_response.json()["available"] == POINTS_CHECK_IN

    transactions_response = await client.get("/api/v1/points/transactions", headers=headers)
    assert transactions_response.status_code == 200
    transactions = transactions_response.json()["transactions"]
    assert len(transactions) == 1
    assert transactions[0]["transaction_type"] == "check_in"

    create_code_response = await client.post("/api/v1/referral/codes", headers=headers)
    assert create_code_response.status_code == 201
    invite_code = create_code_response.json()
    assert invite_code["is_active"] is True

    validate_response = await client.post(f"/api/v1/referral/codes/{invite_code['code']}/validate")
    assert validate_response.status_code == 200
    assert validate_response.json()["valid"] is True

    reward = UserReward(
        user_id=user.id,
        reward_type="points",
        amount=POINTS_REFERRAL,
        source="referral",
        is_used=False,
    )
    stats = UserStats(
        user_id=user.id,
        total_invites=1,
        successful_invites=1,
        total_points=POINTS_REFERRAL,
        available_points=POINTS_REFERRAL,
    )
    db_session.add(reward)
    db_session.add(stats)
    db_session.commit()

    referral_stats_response = await client.get("/api/v1/referral/stats", headers=headers)
    assert referral_stats_response.status_code == 200
    stats_payload = referral_stats_response.json()
    assert stats_payload["total_invites"] == 1
    assert stats_payload["successful_invites"] == 1
    assert stats_payload["total_points"] == POINTS_REFERRAL
    # Referral stats endpoint exposes unified wallet balance.
    assert stats_payload["available_points"] == POINTS_CHECK_IN

    rewards_response = await client.get("/api/v1/referral/rewards", headers=headers)
    assert rewards_response.status_code == 200
    rewards_payload = rewards_response.json()
    assert len(rewards_payload) == 1
    assert rewards_payload[0]["reward_type"] == "points"
    assert rewards_payload[0]["amount"] == POINTS_REFERRAL

    deactivate_response = await client.delete(
        f"/api/v1/referral/codes/{invite_code['id']}",
        headers=headers,
    )
    assert deactivate_response.status_code == 204

    validate_after_deactivate = await client.post(f"/api/v1/referral/codes/{invite_code['code']}/validate")
    assert validate_after_deactivate.status_code == 200
    assert validate_after_deactivate.json()["valid"] is False


@pytest.mark.asyncio
async def test_materials_library_search_preview_and_import_roundtrip(client: AsyncClient, db_session: Session):
    user = await _create_user(db_session, prefix="materials_flow")
    _attach_materials_access_subscription(db_session, user_id=user.id)
    login_payload = await _login(client, identifier=user.email)
    headers = _auth_headers(login_payload["access_token"])

    novel = Novel(
        user_id=user.id,
        title="Materials Novel",
        author="Tester",
        synopsis="A decomposed material library",
        source_meta=json.dumps({"original_filename": "materials.txt"}, ensure_ascii=False),
    )
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/materials.txt",
        status="completed",
        total_chapters=2,
        processed_chapters=2,
    )
    character = Character(
        novel_id=novel.id,
        name="Hero Zhang",
        description="Main protagonist",
    )
    worldview = WorldView(
        novel_id=novel.id,
        power_system="Qi",
    )
    db_session.add(job)
    db_session.add(character)
    db_session.add(worldview)
    db_session.commit()
    db_session.refresh(character)

    materials_response = await client.get("/api/v1/materials/list", headers=headers)
    assert materials_response.status_code == 200
    materials_payload = materials_response.json()
    assert len(materials_payload) == 1
    assert materials_payload[0]["title"] == "Materials Novel"
    assert materials_payload[0]["status"] == "completed"

    detail_response = await client.get(f"/api/v1/materials/{novel.id}", headers=headers)
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["characters_count"] == 1
    assert detail_payload["has_world_view"] is True

    summary_response = await client.get("/api/v1/materials/library-summary", headers=headers)
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert len(summary_payload) == 1
    assert summary_payload[0]["counts"]["characters"] == 1
    assert summary_payload[0]["counts"]["worldview"] == 1

    search_response = await client.get(
        "/api/v1/materials/search",
        params={"q": "Hero"},
        headers=headers,
    )
    assert search_response.status_code == 200
    search_payload = search_response.json()
    assert any(
        item["entity_type"] == "characters" and item["entity_id"] == character.id
        for item in search_payload
    )

    preview_response = await client.get(
        f"/api/v1/materials/{novel.id}/characters/{character.id}/preview",
        headers=headers,
    )
    assert preview_response.status_code == 200
    preview_payload = preview_response.json()
    assert preview_payload["title"] == "Hero Zhang"
    assert preview_payload["suggested_file_type"] == "character"
    assert "Hero Zhang" in preview_payload["markdown"]

    project = await _create_project(client, login_payload["access_token"], name="Imported Materials Project")
    import_response = await client.post(
        "/api/v1/materials/import",
        json={
            "project_id": project["id"],
            "novel_id": novel.id,
            "entity_type": "characters",
            "entity_id": character.id,
        },
        headers=headers,
    )
    assert import_response.status_code == 200
    import_payload = import_response.json()
    assert import_payload["file_type"] == "character"

    imported_file = db_session.get(File, import_payload["file_id"])
    assert imported_file is not None
    assert imported_file.project_id == project["id"]
    assert imported_file.parent_id is not None
    assert imported_file.title == import_payload["title"]
    assert "Hero Zhang" in (imported_file.content or "")

    file_tree_response = await client.get(
        f"/api/v1/projects/{project['id']}/file-tree",
        headers=headers,
    )
    assert file_tree_response.status_code == 200
    tree_payload = file_tree_response.json()["tree"]

    def _contains_title(nodes: list[dict], expected: str) -> bool:
        for node in nodes:
            if node.get("title") == expected:
                return True
            children = node.get("children") or []
            if _contains_title(children, expected):
                return True
        return False

    assert _contains_title(tree_payload, import_payload["title"])
