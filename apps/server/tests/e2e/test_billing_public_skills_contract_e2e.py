"""
Focused E2E contracts for billing catalog and public skills discovery.

These tests pin the backend contracts that the stable web lanes rely on:
- billing/pricing catalog normalization
- public skill discovery, filtering, and add/remove collection flows
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import PublicSkill, UserAddedSkill
from models.subscription import SubscriptionPlan

from .test_core_api_e2e import _auth_headers, _create_user, _login

pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_subscription_catalog_contract_supports_billing_page_entitlements(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="billing_catalog_contract")

    db_session.add_all(
        [
            SubscriptionPlan(
                name="free",
                display_name="Free",
                display_name_en="Free",
                price_monthly_cents=0,
                price_yearly_cents=0,
                features={
                    "ai_conversations_per_day": 10,
                    "max_projects": 1,
                    "export_formats": ["txt", "pdf"],
                },
                is_active=True,
            ),
            SubscriptionPlan(
                name="pro",
                display_name="Pro",
                display_name_en="Pro",
                price_monthly_cents=2900,
                price_yearly_cents=29000,
                features={
                    "ai_conversations_per_day": 15,
                    "max_projects": 6,
                    "context_window_tokens": "8192",
                    "material_uploads": 12,
                    "material_decompositions": 8,
                    "custom_skills": 9,
                    "inspiration_copies_monthly": "33",
                    "priority_support": True,
                    "export_formats": ["txt", "md", "pdf"],
                },
                is_active=True,
            ),
            SubscriptionPlan(
                name="max",
                display_name="Max",
                display_name_en="Max",
                price_monthly_cents=4900,
                price_yearly_cents=49000,
                features={
                    "ai_conversations_per_day": -1,
                    "max_projects": 20,
                    "export_formats": ["txt", "md"],
                },
                is_active=True,
            ),
        ]
    )
    db_session.commit()

    login_payload = await _login(client, identifier=user.email)
    response = await client.get(
        "/api/v1/subscription/catalog",
        headers=_auth_headers(login_payload["access_token"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["version"] == "2026-03"
    assert payload["comparison_mode"] == "task_outcome"
    assert payload["pricing_anchor_monthly_cents"] == 4900
    assert [tier["name"] for tier in payload["tiers"]] == ["free", "pro"]

    free_tier, pro_tier = payload["tiers"]
    assert free_tier["recommended"] is False
    assert free_tier["entitlements"]["export_formats"] == ["txt"]

    assert pro_tier["recommended"] is True
    assert pro_tier["summary_key"] == "creator"
    assert pro_tier["target_user_key"] == "daily_writer"
    assert pro_tier["entitlements"] == {
        "ai_conversations_per_day": 15,
        "writing_credits_monthly": 0,
        "agent_runs_monthly": 0,
        "active_projects_limit": 6,
        "materials_library_access": True,
        "material_uploads_monthly": 12,
        "material_decompositions_monthly": 8,
        "custom_skills_limit": 9,
        "inspiration_copies_monthly": 33,
        "export_formats": ["txt"],
        "context_tokens_limit": 0,
        "priority_queue_level": "standard",
    }


@pytest.mark.asyncio
async def test_public_skills_contract_supports_discovery_and_collection_roundtrip(
    client: AsyncClient,
    db_session: Session,
):
    author = await _create_user(db_session, prefix="public_skill_author")
    viewer = await _create_user(db_session, prefix="public_skill_viewer")

    official_skill = PublicSkill(
        name="剧情加速器",
        description="帮助梳理剧情推进节奏",
        instructions="Always propose plot beats with escalating stakes.",
        category="plot",
        tags=json.dumps(["plot", "structure"]),
        source="official",
        status="approved",
        add_count=5,
    )
    community_skill = PublicSkill(
        name="角色心声",
        description="提炼角色心理活动与对白风格",
        instructions="Focus on internal monologue and voice consistency.",
        category="character",
        tags=json.dumps(["character", "dialogue"]),
        source="community",
        author_id=author.id,
        status="approved",
        add_count=2,
    )
    pending_skill = PublicSkill(
        name="待审核技能",
        description="不应出现在公开列表中",
        instructions="Pending instructions",
        category="plot",
        tags=json.dumps(["pending"]),
        source="community",
        author_id=author.id,
        status="pending",
        add_count=99,
    )
    db_session.add_all([official_skill, community_skill, pending_skill])
    db_session.commit()
    db_session.refresh(official_skill)
    db_session.refresh(community_skill)
    db_session.refresh(pending_skill)

    login_payload = await _login(client, identifier=viewer.username)
    headers = _auth_headers(login_payload["access_token"])

    categories_response = await client.get("/api/v1/public-skills/categories", headers=headers)
    assert categories_response.status_code == 200
    categories_payload = categories_response.json()["categories"]
    counts_by_name = {item["name"]: item["count"] for item in categories_payload}
    assert counts_by_name["plot"] == 1
    assert counts_by_name["character"] == 1

    list_response = await client.get(
        "/api/v1/public-skills?source=community&search=角色&page=1&page_size=10",
        headers=headers,
    )
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["total"] == 1
    assert list_payload["skills"][0]["id"] == community_skill.id
    assert list_payload["skills"][0]["author_name"] == author.username
    assert list_payload["skills"][0]["tags"] == ["character", "dialogue"]
    assert list_payload["skills"][0]["is_added"] is False

    detail_before_add = await client.get(f"/api/v1/public-skills/{community_skill.id}", headers=headers)
    assert detail_before_add.status_code == 200
    assert detail_before_add.json()["is_added"] is False

    add_response = await client.post(f"/api/v1/public-skills/{community_skill.id}/add", headers=headers)
    assert add_response.status_code == 200
    add_payload = add_response.json()
    assert add_payload["success"] is True
    assert add_payload["added_skill_id"]

    db_session.refresh(community_skill)
    assert community_skill.add_count == 3

    active_link = db_session.exec(
        select(UserAddedSkill).where(
            UserAddedSkill.user_id == viewer.id,
            UserAddedSkill.public_skill_id == community_skill.id,
            UserAddedSkill.is_active.is_(True),
        )
    ).one()
    assert active_link.custom_name is None

    detail_after_add = await client.get(f"/api/v1/public-skills/{community_skill.id}", headers=headers)
    assert detail_after_add.status_code == 200
    assert detail_after_add.json()["is_added"] is True

    remove_response = await client.delete(f"/api/v1/public-skills/{community_skill.id}/remove", headers=headers)
    assert remove_response.status_code == 200
    assert remove_response.json()["success"] is True

    db_session.refresh(community_skill)
    assert community_skill.add_count == 2

    detail_after_remove = await client.get(f"/api/v1/public-skills/{community_skill.id}", headers=headers)
    assert detail_after_remove.status_code == 200
    assert detail_after_remove.json()["is_added"] is False

    hidden_response = await client.get(f"/api/v1/public-skills/{pending_skill.id}", headers=headers)
    assert hidden_response.status_code == 404
