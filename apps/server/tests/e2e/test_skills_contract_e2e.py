"""
Focused E2E contracts for user/custom skills.

These tests pin the backend contracts used by the nightly skills web suites:
- custom skill CRUD with quota consumption
- quota-exceeded response when custom skill creation is exhausted
- my-skills aggregation of user + added public skills
- share-to-public submission flow
- project skill usage stats
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import PublicSkill, UserAddedSkill
from models.entities import Project
from models.skill import UserSkill
from models.skill_usage import SkillUsage
from models.subscription import SubscriptionPlan, UsageQuota
from services.subscription.defaults import clone_default_free_features
from .test_core_api_e2e import _attach_subscription, _auth_headers, _create_user, _login

pytestmark = pytest.mark.e2e


def _ensure_plan(
    db_session: Session,
    *,
    name: str,
    display_name: str,
    features: dict,
) -> SubscriptionPlan:
    existing = db_session.exec(select(SubscriptionPlan).where(SubscriptionPlan.name == name)).first()
    if existing:
        return existing

    plan = SubscriptionPlan(
        name=name,
        display_name=display_name,
        display_name_en=display_name,
        price_monthly_cents=2900 if name != "free" else 0,
        price_yearly_cents=29000 if name != "free" else 0,
        features=features,
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    return plan


@pytest.mark.asyncio
async def test_skills_contract_supports_custom_skill_crud_and_consumes_quota(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="skills_contract_crud")
    free_plan = _ensure_plan(
        db_session,
        name="free",
        display_name="Free",
        features={**clone_default_free_features(), "custom_skills": 3},
    )
    _attach_subscription(db_session, user_id=user.id, plan_id=free_plan.id)

    login_payload = await _login(client, identifier=user.email)
    headers = _auth_headers(login_payload["access_token"])

    create_response = await client.post(
        "/api/v1/skills",
        json={
            "name": "剧情推进器",
            "description": "帮助推进场景节奏",
            "triggers": ["/plot", "推进剧情"],
            "instructions": "Always suggest escalating plot beats.",
        },
        headers=headers,
    )
    assert create_response.status_code == 200
    created_skill = create_response.json()
    assert created_skill["name"] == "剧情推进器"
    assert created_skill["triggers"] == ["/plot", "推进剧情"]
    assert created_skill["source"] == "user"

    quota_response = await client.get("/api/v1/subscription/quota", headers=headers)
    assert quota_response.status_code == 200
    assert quota_response.json()["skill_creates"]["used"] == 1

    update_response = await client.put(
        f"/api/v1/skills/{created_skill['id']}",
        json={
            "name": "剧情推进器·强化版",
            "triggers": ["/plot+", "强化推进"],
            "instructions": "Escalate plot beats and show the cost of each choice.",
            "is_active": False,
        },
        headers=headers,
    )
    assert update_response.status_code == 200
    updated_skill = update_response.json()
    assert updated_skill["name"] == "剧情推进器·强化版"
    assert updated_skill["triggers"] == ["/plot+", "强化推进"]
    assert updated_skill["is_active"] is False

    list_response = await client.get("/api/v1/skills?search=强化", headers=headers)
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["total"] == 1
    assert list_payload["skills"][0]["id"] == created_skill["id"]

    delete_response = await client.delete(f"/api/v1/skills/{created_skill['id']}", headers=headers)
    assert delete_response.status_code == 200
    assert delete_response.json()["success"] is True

    after_delete = await client.get("/api/v1/skills", headers=headers)
    assert after_delete.status_code == 200
    assert after_delete.json()["total"] == 0


@pytest.mark.asyncio
async def test_skills_contract_rejects_custom_skill_creation_when_quota_exhausted(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="skills_contract_quota")
    free_plan = _ensure_plan(
        db_session,
        name="free",
        display_name="Free",
        features={**clone_default_free_features(), "custom_skills": 1},
    )
    _attach_subscription(db_session, user_id=user.id, plan_id=free_plan.id)

    quota = UsageQuota(
        user_id=user.id,
        period_start=datetime.utcnow() - timedelta(days=1),
        period_end=datetime.utcnow() + timedelta(days=1),
        ai_conversations_used=0,
        skill_creates_used=0,
        monthly_period_start=datetime.utcnow() - timedelta(days=1),
        monthly_period_end=datetime.utcnow() + timedelta(days=29),
        last_reset_at=datetime.utcnow(),
    )
    db_session.add(quota)
    # The cap counts skills the user owns right now.
    db_session.add(UserSkill(user_id=user.id, name="已有技能", instructions="existing"))
    db_session.commit()

    login_payload = await _login(client, identifier=user.username)
    headers = _auth_headers(login_payload["access_token"])

    create_response = await client.post(
        "/api/v1/skills",
        json={
            "name": "超额技能",
            "description": "不应创建成功",
            "triggers": ["/overflow"],
            "instructions": "This should fail on quota.",
        },
        headers=headers,
    )
    assert create_response.status_code == 402
    payload = create_response.json()
    detail = payload.get("error_detail", payload.get("detail"))
    assert detail["feature_type"] == "skill_create"
    assert detail["used"] == 1
    assert detail["limit"] == 1


@pytest.mark.asyncio
async def test_skills_contract_my_skills_aggregates_custom_and_added_public_entries(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="skills_contract_aggregate")
    public_skill = PublicSkill(
        name="角色对白润色",
        description="帮助调整角色对白风格",
        instructions="Keep dialogue sharp and voice-specific.",
        category="character",
        tags=json.dumps(["dialogue", "character"]),
        source="official",
        status="approved",
        add_count=1,
    )
    inactive_public_skill = PublicSkill(
        name="不活跃公共技能",
        description="不应出现在 my-skills",
        instructions="inactive",
        category="plot",
        tags=json.dumps(["inactive"]),
        source="official",
        status="approved",
        add_count=0,
    )
    db_session.add_all([public_skill, inactive_public_skill])
    db_session.commit()
    db_session.refresh(public_skill)
    db_session.refresh(inactive_public_skill)

    db_session.add(
        UserSkill(
            user_id=user.id,
            name="我的场景节奏",
            description="自定义技能",
            triggers=json.dumps(["/pace", "节奏"]),
            instructions="Tighten the pacing of each scene.",
            is_active=True,
        )
    )
    db_session.add(
        UserAddedSkill(
            user_id=user.id,
            public_skill_id=public_skill.id,
            custom_name="我的角色对白助手",
            is_active=True,
        )
    )
    db_session.add(
        UserAddedSkill(
            user_id=user.id,
            public_skill_id=inactive_public_skill.id,
            custom_name="已停用技能",
            is_active=False,
        )
    )
    db_session.commit()

    login_payload = await _login(client, identifier=user.email)
    headers = _auth_headers(login_payload["access_token"])

    my_skills_response = await client.get("/api/v1/skills/my-skills", headers=headers)
    assert my_skills_response.status_code == 200
    my_skills_payload = my_skills_response.json()
    assert my_skills_payload["total"] == 2
    assert len(my_skills_payload["user_skills"]) == 1
    assert len(my_skills_payload["added_skills"]) == 1
    assert my_skills_payload["user_skills"][0]["triggers"] == ["/pace", "节奏"]
    assert my_skills_payload["added_skills"][0]["name"] == "我的角色对白助手"
    assert my_skills_payload["added_skills"][0]["public_skill_id"] == public_skill.id

    flattened_response = await client.get("/api/v1/skills?search=对白", headers=headers)
    assert flattened_response.status_code == 200
    flattened_payload = flattened_response.json()
    assert flattened_payload["total"] == 1
    assert flattened_payload["skills"][0]["source"] == "added"
    assert flattened_payload["skills"][0]["name"] == "我的角色对白助手"
    assert flattened_payload["skills"][0]["triggers"] == ["dialogue", "character"]


@pytest.mark.asyncio
async def test_skills_contract_share_flow_supports_submit_and_rejected_reshare(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="skills_contract_share")
    user_skill = UserSkill(
        user_id=user.id,
        name="待共享技能",
        description="准备共享到公共技能库",
        triggers=json.dumps(["/share", "共享"]),
        instructions="Share this skill to the public library.",
        is_active=True,
        is_shared=False,
    )
    db_session.add(user_skill)
    db_session.commit()
    db_session.refresh(user_skill)

    login_payload = await _login(client, identifier=user.email)
    headers = _auth_headers(login_payload["access_token"])

    share_response = await client.post(
        f"/api/v1/skills/{user_skill.id}/share",
        json={"category": "writing"},
        headers=headers,
    )
    assert share_response.status_code == 200
    share_payload = share_response.json()
    assert share_payload["success"] is True
    assert share_payload["message"] == "Skill submitted for review"
    assert share_payload["public_skill_id"]

    db_session.refresh(user_skill)
    assert user_skill.is_shared is True
    assert user_skill.shared_skill_id == share_payload["public_skill_id"]

    public_skill = db_session.get(PublicSkill, share_payload["public_skill_id"])
    assert public_skill is not None
    assert public_skill.status == "pending"
    assert public_skill.source == "community"
    assert public_skill.author_id == user.id

    share_again = await client.post(
        f"/api/v1/skills/{user_skill.id}/share",
        json={"category": "writing"},
        headers=headers,
    )
    assert share_again.status_code == 200
    assert share_again.json() == {
        "success": False,
        "message": "Skill already shared",
        "public_skill_id": share_payload["public_skill_id"],
    }

    public_skill.status = "rejected"
    db_session.add(public_skill)
    db_session.commit()

    reshare_response = await client.post(
        f"/api/v1/skills/{user_skill.id}/share",
        json={"category": "plot"},
        headers=headers,
    )
    assert reshare_response.status_code == 200
    reshare_payload = reshare_response.json()
    assert reshare_payload["success"] is True
    assert reshare_payload["public_skill_id"] != share_payload["public_skill_id"]

    db_session.refresh(user_skill)
    assert user_skill.shared_skill_id == reshare_payload["public_skill_id"]

    resubmitted_public = db_session.get(PublicSkill, reshare_payload["public_skill_id"])
    assert resubmitted_public is not None
    assert resubmitted_public.category == "plot"
    assert resubmitted_public.status == "pending"


@pytest.mark.asyncio
async def test_skills_contract_stats_endpoint_returns_project_usage_rollups(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="skills_contract_stats")
    project = Project(name="Skill Stats Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    now = datetime.utcnow()
    db_session.add_all(
        [
            SkillUsage(
                user_id=user.id,
                project_id=project.id,
                skill_id="builtin-outline",
                skill_name="Builtin Outline",
                skill_source="builtin",
                matched_trigger="/outline",
                confidence=0.7,
                created_at=now - timedelta(days=1),
            ),
            SkillUsage(
                user_id=user.id,
                project_id=project.id,
                skill_id="user-pacing",
                skill_name="User Pacing",
                skill_source="user",
                matched_trigger="/pace",
                confidence=0.9,
                created_at=now,
            ),
            SkillUsage(
                user_id=user.id,
                project_id=project.id,
                skill_id="added-dialogue",
                skill_name="Added Dialogue",
                skill_source="added",
                matched_trigger="对白",
                confidence=0.8,
                created_at=now,
            ),
        ]
    )
    db_session.commit()

    login_payload = await _login(client, identifier=user.username)
    headers = _auth_headers(login_payload["access_token"])

    response = await client.get(f"/api/v1/skills/stats/{project.id}?days=7", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_triggers"] == 3
    assert payload["builtin_count"] == 1
    assert payload["user_count"] == 2
    assert "avg_confidence" not in payload
    assert payload["top_skills"][0]["count"] == 1
    assert {item["skill_source"] for item in payload["top_skills"]} == {"builtin", "user", "added"}
    assert len(payload["daily_usage"]) == 7
    assert sum(item["count"] for item in payload["daily_usage"]) == 3
