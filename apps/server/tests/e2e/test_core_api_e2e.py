"""
Real end-to-end core API workflows.

These tests keep to request-driven flows so apps/server e2e covers a small but
high-value slice of auth, project/file/version, export, and access control.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import AgentArtifactLedger, ChatMessage, ChatSession, Inspiration, PublicSkill, User
from models.material_models import Chapter, Character, IngestionJob, Novel, StoryLine, WorldView
from models.points import PointsTransaction
from models.referral import InviteCode, UserReward, UserStats
from models.subscription import (
    RedemptionCode,
    SubscriptionHistory,
    SubscriptionPlan,
    UsageQuota,
    UserSubscription,
)
from services.core.auth_service import hash_password

pytestmark = pytest.mark.e2e

# Keep request-driven e2e deterministic and quiet; vector index behavior is
# covered elsewhere and should not add background noise here.
os.environ.setdefault("ASYNC_VECTOR_INDEX_ENABLED", "false")
os.environ.setdefault(
    "REDEMPTION_CODE_HMAC_SECRET",
    "test-secret-key-must-be-at-least-32-characters-long",
)


def _unique_identity(prefix: str) -> tuple[str, str]:
    suffix = uuid4().hex[:8]
    username = f"{prefix}_{suffix}"
    email = f"{username}@example.com"
    return username, email


async def _create_user(
    db_session: Session,
    *,
    prefix: str,
    password: str = "password123",
    is_superuser: bool = False,
) -> User:
    username, email = _unique_identity(prefix)
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


async def _login(client: AsyncClient, *, identifier: str, password: str = "password123") -> dict:
    response = await client.post(
        "/api/auth/login",
        data={"username": identifier, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def _auth_headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


async def _create_project(client: AsyncClient, access_token: str, *, name: str, project_type: str = "novel") -> dict:
    response = await client.post(
        "/api/v1/projects",
        json={"name": name, "project_type": project_type},
        headers=_auth_headers(access_token),
    )
    assert response.status_code == 200
    return response.json()


async def _create_file(
    client: AsyncClient,
    access_token: str,
    *,
    project_id: str,
    title: str,
    content: str,
    file_type: str = "draft",
) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": title,
            "content": content,
            "file_type": file_type,
        },
        headers=_auth_headers(access_token),
    )
    assert response.status_code == 200
    return response.json()


def _create_plan(
    db_session: Session,
    *,
    name: str,
    display_name: str,
    features: dict,
) -> SubscriptionPlan:
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


def _attach_subscription(
    db_session: Session,
    *,
    user_id: str,
    plan_id: str,
    status: str = "active",
) -> UserSubscription:
    now = datetime.utcnow()
    subscription = UserSubscription(
        user_id=user_id,
        plan_id=plan_id,
        status=status,
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    )
    db_session.add(subscription)
    db_session.commit()
    db_session.refresh(subscription)
    return subscription


def _attach_materials_access_subscription(
    db_session: Session,
    *,
    user_id: str,
) -> UserSubscription:
    plan = _create_plan(
        db_session,
        name="pro",
        display_name="Pro",
        features={
            "materials_library_access": True,
            "material_uploads": 5,
            "material_decompositions": 5,
            "max_projects": 3,
        },
    )
    return _attach_subscription(db_session, user_id=user_id, plan_id=plan.id)


def _build_valid_redemption_code(*, tier_duration: str = "PRO7M", random_part: str = "12345678") -> str:
    secret = os.environ["REDEMPTION_CODE_HMAC_SECRET"]
    signature = hmac.new(
        secret.encode(),
        f"{tier_duration}-{random_part}".encode(),
        hashlib.sha256,
    ).digest()
    checksum = signature[:4].hex().upper()[:4]
    return f"ERG-{tier_duration}-{checksum}-{random_part}"


@pytest.mark.asyncio
async def test_auth_login_refresh_and_me_roundtrip(client: AsyncClient, db_session: Session):
    user = await _create_user(db_session, prefix="auth_e2e")

    unauthenticated_me = await client.get("/api/auth/me")
    assert unauthenticated_me.status_code == 401

    login_payload = await _login(client, identifier=user.username)
    assert login_payload["token_type"] == "bearer"
    assert login_payload["refresh_token"]

    me_response = await client.get(
        "/api/auth/me",
        headers=_auth_headers(login_payload["access_token"]),
    )
    assert me_response.status_code == 200
    assert me_response.json()["id"] == user.id
    assert me_response.json()["username"] == user.username

    refresh_response = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": login_payload["refresh_token"]},
    )
    assert refresh_response.status_code == 200
    refreshed_payload = refresh_response.json()
    assert refreshed_payload["refresh_token"] != login_payload["refresh_token"]

    replay_refresh = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": login_payload["refresh_token"]},
    )
    assert replay_refresh.status_code == 401

    refreshed_me = await client.get(
        "/api/auth/me",
        headers=_auth_headers(refreshed_payload["access_token"]),
    )
    assert refreshed_me.status_code == 200
    assert refreshed_me.json()["email"] == user.email


@pytest.mark.asyncio
async def test_project_file_version_roundtrip_covers_tree_compare_and_rollback(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="core_flow")
    login_payload = await _login(client, identifier=user.email)
    access_token = login_payload["access_token"]

    project = await _create_project(client, access_token, name="Core Flow Project")
    project_id = project["id"]

    tree_response = await client.get(
        f"/api/v1/projects/{project_id}/file-tree",
        headers=_auth_headers(access_token),
    )
    assert tree_response.status_code == 200
    tree = tree_response.json()["tree"]
    assert len(tree) >= 4

    file_record = await _create_file(
        client,
        access_token,
        project_id=project_id,
        title="Chapter 1",
        content="draft zero",
    )
    file_id = file_record["id"]

    baseline = await client.get(f"/api/v1/files/{file_id}/versions/1/content", headers=_auth_headers(access_token))
    assert baseline.status_code == 200
    assert baseline.json()["content"] == "draft zero"

    version_one = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={
            "content": "Line 1\nLine 2\nLine 3",
            "change_type": "edit",
            "change_source": "user",
            "change_summary": "draft v1",
        },
        headers=_auth_headers(access_token),
    )
    assert version_one.status_code == 200
    assert version_one.json()["version_number"] == 2

    version_two = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={
            "content": "Line 1\nLine 2 revised\nLine 3\nLine 4",
            "change_type": "edit",
            "change_source": "user",
            "change_summary": "draft v2",
        },
        headers=_auth_headers(access_token),
    )
    assert version_two.status_code == 200
    assert version_two.json()["version_number"] == 3

    list_response = await client.get(
        f"/api/v1/files/{file_id}/versions",
        headers=_auth_headers(access_token),
    )
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 3
    assert [version["version_number"] for version in list_response.json()["versions"]] == [3, 2, 1]
    assert list_response.json()["versions"][-1]["change_source"] == "system"

    compare_response = await client.get(
        f"/api/v1/files/{file_id}/versions/compare?v1=2&v2=3",
        headers=_auth_headers(access_token),
    )
    assert compare_response.status_code == 200
    comparison = compare_response.json()
    assert comparison["version1"]["number"] == 2
    assert comparison["version2"]["number"] == 3
    assert comparison["stats"]["lines_added"] >= 1
    assert "Line 4" in comparison["unified_diff"]

    rollback_response = await client.post(
        f"/api/v1/files/{file_id}/versions/2/rollback",
        headers=_auth_headers(access_token),
    )
    assert rollback_response.status_code == 200
    rollback_payload = rollback_response.json()
    assert rollback_payload["restored_version"] == 2
    # POST /versions 只写历史、不改正文，所以恢复时正文仍是 "draft zero"，和最新版本 v3
    # 不同：恢复前先把它存成系统备份 v4（不占用户额度），v5 才是恢复出来的版本。
    # 响应里报告的是实际新建的 restore 版本号。
    # 见 .agents/notes/implemented/bug-fix/2026-10-09-file-restore-backs-up-current-content.md
    assert rollback_payload["new_version_number"] == 5

    backup = await client.get(
        f"/api/v1/files/{file_id}/versions/4/content",
        headers=_auth_headers(access_token),
    )
    assert backup.status_code == 200
    assert backup.json()["content"] == "draft zero"

    restored_content = await client.get(
        f"/api/v1/files/{file_id}/versions/5/content",
        headers=_auth_headers(access_token),
    )
    assert restored_content.status_code == 200
    assert restored_content.json()["content"] == "Line 1\nLine 2\nLine 3"

    history = await client.get(
        f"/api/v1/files/{file_id}/versions?limit=2",
        headers=_auth_headers(access_token),
    )
    assert [
        (item["version_number"], item["change_source"], item["change_type"])
        for item in history.json()["versions"]
    ] == [(5, "user", "restore"), (4, "system", "edit")]


@pytest.mark.asyncio
async def test_export_drafts_roundtrip_returns_merged_txt(client: AsyncClient, db_session: Session):
    user = await _create_user(db_session, prefix="export_flow")
    login_payload = await _login(client, identifier=user.username)
    access_token = login_payload["access_token"]

    project = await _create_project(client, access_token, name="Export Flow Project")
    project_id = project["id"]

    await _create_file(
        client,
        access_token,
        project_id=project_id,
        title="第一章 开始",
        content="这是第一章的内容",
    )
    await _create_file(
        client,
        access_token,
        project_id=project_id,
        title="第二章 发展",
        content="这是第二章的内容",
    )

    export_response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers=_auth_headers(access_token),
    )
    assert export_response.status_code == 200
    assert export_response.headers["content-type"] == "text/plain; charset=utf-8"

    exported_text = export_response.content.decode("utf-8-sig")
    assert "第一章 开始" in exported_text
    assert "第二章 发展" in exported_text
    assert "这是第一章的内容" in exported_text
    assert "---" in exported_text


@pytest.mark.asyncio
async def test_cross_user_cannot_read_versions_or_export_another_project(client: AsyncClient, db_session: Session):
    owner = await _create_user(db_session, prefix="owner_flow")
    intruder = await _create_user(db_session, prefix="intruder_flow")

    owner_login = await _login(client, identifier=owner.username)
    intruder_login = await _login(client, identifier=intruder.email)

    project = await _create_project(client, owner_login["access_token"], name="Private Project")
    file_record = await _create_file(
        client,
        owner_login["access_token"],
        project_id=project["id"],
        title="Private Draft",
        content="owner only",
    )

    version_response = await client.post(
        f"/api/v1/files/{file_record['id']}/versions",
        json={"content": "owner only v1", "change_type": "edit", "change_source": "user"},
        headers=_auth_headers(owner_login["access_token"]),
    )
    assert version_response.status_code == 200

    foreign_project = await client.get(
        f"/api/v1/projects/{project['id']}",
        headers=_auth_headers(intruder_login["access_token"]),
    )
    assert foreign_project.status_code == 403

    foreign_versions = await client.get(
        f"/api/v1/files/{file_record['id']}/versions",
        headers=_auth_headers(intruder_login["access_token"]),
    )
    assert foreign_versions.status_code == 403

    foreign_export = await client.get(
        f"/api/v1/projects/{project['id']}/export/drafts",
        headers=_auth_headers(intruder_login["access_token"]),
    )
    assert foreign_export.status_code == 403


@pytest.mark.asyncio
async def test_chat_session_lifecycle_covers_messages_recent_clear_and_new_session(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="chat_flow")
    login_payload = await _login(client, identifier=user.username)
    access_token = login_payload["access_token"]

    project = await _create_project(client, access_token, name="Chat Flow Project")
    project_id = project["id"]

    session_response = await client.get(
        f"/api/v1/chat/session/{project_id}",
        headers=_auth_headers(access_token),
    )
    assert session_response.status_code == 200
    chat_session_id = session_response.json()["id"]

    seeded_messages = [
        ChatMessage(session_id=chat_session_id, role="user", content="First prompt"),
        ChatMessage(session_id=chat_session_id, role="assistant", content="First answer"),
        ChatMessage(session_id=chat_session_id, role="user", content="Follow-up prompt"),
    ]
    db_session.add_all(seeded_messages)
    chat_session = db_session.get(ChatSession, chat_session_id)
    assert chat_session is not None
    chat_session.message_count = len(seeded_messages)
    db_session.add(chat_session)
    db_session.commit()

    messages_response = await client.get(
        f"/api/v1/chat/session/{project_id}/messages",
        headers=_auth_headers(access_token),
    )
    assert messages_response.status_code == 200
    assert [message["role"] for message in messages_response.json()] == ["user", "assistant", "user"]

    recent_response = await client.get(
        f"/api/v1/chat/session/{project_id}/recent?limit=2",
        headers=_auth_headers(access_token),
    )
    assert recent_response.status_code == 200
    recent_messages = recent_response.json()
    assert len(recent_messages) == 2
    assert [message["content"] for message in recent_messages] == ["First answer", "Follow-up prompt"]

    clear_response = await client.delete(
        f"/api/v1/chat/session/{project_id}",
        headers=_auth_headers(access_token),
    )
    assert clear_response.status_code == 200
    assert clear_response.json()["success"] is True
    assert "Cleared 3 messages" in clear_response.json()["message"]

    assert db_session.exec(select(ChatMessage).where(ChatMessage.session_id == chat_session_id)).all() == []
    assert db_session.exec(
        select(AgentArtifactLedger).where(AgentArtifactLedger.session_id == chat_session_id)
    ).all() == []

    new_session_response = await client.post(
        f"/api/v1/chat/session/{project_id}/new?title=Fresh+Session",
        headers=_auth_headers(access_token),
    )
    assert new_session_response.status_code == 200
    new_session = new_session_response.json()
    assert new_session["id"] != chat_session_id
    assert new_session["title"] == "Fresh Session"

    current_session_response = await client.get(
        f"/api/v1/chat/session/{project_id}",
        headers=_auth_headers(access_token),
    )
    assert current_session_response.status_code == 200
    assert current_session_response.json()["id"] == new_session["id"]


@pytest.mark.asyncio
async def test_subscription_status_quota_and_history_roundtrip(client: AsyncClient, db_session: Session):
    user = await _create_user(db_session, prefix="subscription_flow")
    plan = _create_plan(
        db_session,
        name="pro",
        display_name="Pro",
        features={
            "ai_conversations_per_day": -1,
            "max_projects": 8,
            "material_uploads": 50,
            "material_decompositions": 25,
            "custom_skills": 10,
            "inspiration_copies_monthly": 100,
        },
    )
    _attach_subscription(db_session, user_id=user.id, plan_id=plan.id)

    quota = UsageQuota(
        user_id=user.id,
        period_start=datetime.utcnow() - timedelta(days=2),
        period_end=datetime.utcnow() + timedelta(days=28),
        ai_conversations_used=7,
        material_uploads_used=2,
        material_decompositions_used=1,
        skill_creates_used=3,
        inspiration_copies_used=4,
        monthly_period_start=datetime.utcnow() - timedelta(days=2),
        monthly_period_end=datetime.utcnow() + timedelta(days=28),
    )
    history = SubscriptionHistory(
        user_id=user.id,
        action="created",
        plan_name="pro",
        start_date=datetime.utcnow() - timedelta(days=1),
        end_date=datetime.utcnow() + timedelta(days=30),
        event_metadata={"source": "e2e"},
    )
    db_session.add(quota)
    db_session.add(history)
    db_session.commit()

    login_payload = await _login(client, identifier=user.email)
    access_token = login_payload["access_token"]

    created_project = await _create_project(client, access_token, name="Quota Project")

    status_response = await client.get(
        "/api/v1/subscription/me",
        headers=_auth_headers(access_token),
    )
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["tier"] == "pro"
    assert status_payload["status"] == "active"
    assert status_payload["display_name"] == "Pro"

    quota_response = await client.get(
        "/api/v1/subscription/quota",
        headers=_auth_headers(access_token),
    )
    assert quota_response.status_code == 200
    quota_payload = quota_response.json()
    assert quota_payload["ai_conversations"]["used"] == 7
    assert quota_payload["projects"]["used"] == 1
    assert quota_payload["projects"]["limit"] == 8
    assert quota_payload["inspiration_copies"]["used"] == 4

    history_response = await client.get(
        "/api/v1/subscription/history",
        headers=_auth_headers(access_token),
    )
    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert len(history_payload) == 1
    assert history_payload[0]["action"] == "created"
    assert history_payload[0]["plan_name"] == "pro"

    project_fetch = await client.get(
        f"/api/v1/projects/{created_project['id']}",
        headers=_auth_headers(access_token),
    )
    assert project_fetch.status_code == 200


@pytest.mark.asyncio
async def test_inspirations_submit_list_detail_and_copy_roundtrip(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setenv("INSPIRATIONS_ENABLED", "true")
    admin = await _create_user(db_session, prefix="inspiration_admin", is_superuser=True)
    consumer = await _create_user(db_session, prefix="inspiration_consumer")

    pro_plan = _create_plan(
        db_session,
        name="pro",
        display_name="Pro",
        features={
            "ai_conversations_per_day": -1,
            "max_projects": 10,
            "inspiration_copies_monthly": 100,
        },
    )
    _attach_subscription(db_session, user_id=consumer.id, plan_id=pro_plan.id)

    admin_login = await _login(client, identifier=admin.username)
    consumer_login = await _login(client, identifier=consumer.email)

    source_project = await _create_project(client, admin_login["access_token"], name="Admin Inspiration Source")
    await _create_file(
        client,
        admin_login["access_token"],
        project_id=source_project["id"],
        title="Template Chapter",
        content="Template content",
    )

    submit_response = await client.post(
        "/api/v1/inspirations",
        json={
            "project_id": source_project["id"],
            "name": "Admin Approved Inspiration",
            "description": "A reusable template",
            "tags": ["mystery", "featured"],
        },
        headers=_auth_headers(admin_login["access_token"]),
    )
    assert submit_response.status_code == 201
    submit_payload = submit_response.json()
    assert submit_payload["status"] == "approved"
    inspiration_id = submit_payload["inspiration_id"]

    listing_response = await client.get("/api/v1/inspirations?search=Admin+Approved")
    assert listing_response.status_code == 200
    listing_payload = listing_response.json()
    assert listing_payload["total"] >= 1
    assert any(item["id"] == inspiration_id for item in listing_payload["inspirations"])

    detail_response = await client.get(f"/api/v1/inspirations/{inspiration_id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["name"] == "Admin Approved Inspiration"
    assert any(item["title"] == "Template Chapter" for item in detail_payload["file_preview"])

    copy_response = await client.post(
        f"/api/v1/inspirations/{inspiration_id}/copy",
        json={"project_name": "Copied Inspiration Project"},
        headers=_auth_headers(consumer_login["access_token"]),
    )
    assert copy_response.status_code == 200
    copy_payload = copy_response.json()
    assert copy_payload["success"] is True
    assert copy_payload["project_name"] == "Copied Inspiration Project"

    copied_project_response = await client.get(
        f"/api/v1/projects/{copy_payload['project_id']}",
        headers=_auth_headers(consumer_login["access_token"]),
    )
    assert copied_project_response.status_code == 200

    copied_tree_response = await client.get(
        f"/api/v1/projects/{copy_payload['project_id']}/file-tree",
        headers=_auth_headers(consumer_login["access_token"]),
    )
    assert copied_tree_response.status_code == 200
    copied_tree = copied_tree_response.json()["tree"]
    assert any(node["title"] == "Template Chapter" for node in copied_tree)

    stored_inspiration = db_session.get(Inspiration, inspiration_id)
    assert stored_inspiration is not None
    assert stored_inspiration.copy_count >= 1


@pytest.mark.asyncio
async def test_subscription_redeem_code_roundtrip_upgrades_plan_and_records_history(
    client: AsyncClient,
    db_session: Session,
):
    creator = await _create_user(db_session, prefix="redeem_creator", is_superuser=True)
    redeemer = await _create_user(db_session, prefix="redeem_user")

    pro_plan = _create_plan(
        db_session,
        name="pro",
        display_name="Pro",
        features={"ai_conversations_per_day": 9999, "max_projects": 8},
    )
    redemption_code_value = _build_valid_redemption_code()
    redemption_code = RedemptionCode(
        code=redemption_code_value,
        code_type="single_use",
        tier=pro_plan.name,
        duration_days=7,
        max_uses=1,
        current_uses=0,
        created_by=creator.id,
        is_active=True,
    )
    db_session.add(redemption_code)
    db_session.commit()

    login_payload = await _login(client, identifier=redeemer.username)
    access_token = login_payload["access_token"]

    redeem_response = await client.post(
        "/api/v1/subscription/redeem",
        json={"code": redemption_code_value, "source": "server_e2e"},
        headers=_auth_headers(access_token),
    )
    assert redeem_response.status_code == 200
    redeem_payload = redeem_response.json()
    assert redeem_payload["success"] is True
    assert redeem_payload["tier"] == "pro"
    assert redeem_payload["duration_days"] == 7

    status_response = await client.get(
        "/api/v1/subscription/me",
        headers=_auth_headers(access_token),
    )
    assert status_response.status_code == 200
    status_payload = status_response.json()
    assert status_payload["tier"] == "pro"
    assert status_payload["status"] == "active"

    history_response = await client.get(
        "/api/v1/subscription/history",
        headers=_auth_headers(access_token),
    )
    assert history_response.status_code == 200
    history_payload = history_response.json()
    assert len(history_payload) >= 1
    assert history_payload[0]["plan_name"] == "pro"


@pytest.mark.asyncio
async def test_points_and_referral_roundtrip_covers_check_in_codes_stats_and_rewards(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="points_referral")
    login_payload = await _login(client, identifier=user.email)
    access_token = login_payload["access_token"]

    check_in_status_before = await client.get(
        "/api/v1/points/check-in/status",
        headers=_auth_headers(access_token),
    )
    assert check_in_status_before.status_code == 200
    assert check_in_status_before.json()["checked_in"] is False

    check_in_response = await client.post(
        "/api/v1/points/check-in",
        headers=_auth_headers(access_token),
    )
    assert check_in_response.status_code == 200
    check_in_payload = check_in_response.json()
    assert check_in_payload["success"] is True
    assert check_in_payload["points_earned"] > 0

    balance_response = await client.get(
        "/api/v1/points/balance",
        headers=_auth_headers(access_token),
    )
    assert balance_response.status_code == 200
    assert balance_response.json()["available"] >= check_in_payload["points_earned"]

    create_code_response = await client.post(
        "/api/v1/referral/codes",
        headers=_auth_headers(access_token),
    )
    assert create_code_response.status_code == 201
    invite_code = create_code_response.json()["code"]

    validate_code_response = await client.post(f"/api/v1/referral/codes/{invite_code}/validate")
    assert validate_code_response.status_code == 200
    assert validate_code_response.json()["valid"] is True

    user_stats = UserStats(
        user_id=user.id,
        total_invites=3,
        successful_invites=2,
        total_points=80,
        available_points=55,
    )
    reward = UserReward(
        user_id=user.id,
        reward_type="points",
        amount=30,
        source="referral",
        is_used=False,
    )
    transaction = PointsTransaction(
        user_id=user.id,
        amount=25,
        balance_after=balance_response.json()["available"] + 25,
        transaction_type="referral",
        description="Referral bonus",
    )
    db_session.add(user_stats)
    db_session.add(reward)
    db_session.add(transaction)
    db_session.commit()

    stats_response = await client.get(
        "/api/v1/referral/stats",
        headers=_auth_headers(access_token),
    )
    assert stats_response.status_code == 200
    stats_payload = stats_response.json()
    assert stats_payload["total_invites"] == 3
    assert stats_payload["successful_invites"] == 2
    assert stats_payload["available_points"] >= check_in_payload["points_earned"] + 25

    rewards_response = await client.get(
        "/api/v1/referral/rewards",
        headers=_auth_headers(access_token),
    )
    assert rewards_response.status_code == 200
    rewards_payload = rewards_response.json()
    assert any(item["reward_type"] == "points" and item["amount"] == 30 for item in rewards_payload)


@pytest.mark.asyncio
async def test_admin_skill_review_roundtrip_lists_and_approves_pending_skill(
    client: AsyncClient,
    db_session: Session,
):
    admin = await _create_user(db_session, prefix="admin_skill", is_superuser=True)
    author = await _create_user(db_session, prefix="skill_author")

    pending_skill = PublicSkill(
        name="Pending Community Skill",
        description="Needs review",
        instructions="Use carefully",
        category="writing",
        source="community",
        status="pending",
        author_id=author.id,
    )
    db_session.add(pending_skill)
    db_session.commit()

    login_payload = await _login(client, identifier=admin.username)
    access_token = login_payload["access_token"]

    pending_response = await client.get(
        "/api/admin/skills/pending",
        headers=_auth_headers(access_token),
    )
    assert pending_response.status_code == 200
    pending_payload = pending_response.json()
    assert any(item["id"] == pending_skill.id for item in pending_payload)

    approve_response = await client.post(
        f"/api/admin/skills/{pending_skill.id}/approve",
        headers=_auth_headers(access_token),
    )
    assert approve_response.status_code == 200
    assert approve_response.json()["skill_id"] == pending_skill.id

    refreshed_skill = db_session.get(PublicSkill, pending_skill.id)
    assert refreshed_skill is not None
    assert refreshed_skill.status == "approved"
    assert refreshed_skill.reviewed_by == admin.id

    pending_after = await client.get(
        "/api/admin/skills/pending",
        headers=_auth_headers(access_token),
    )
    assert pending_after.status_code == 200
    assert all(item["id"] != pending_skill.id for item in pending_after.json())


@pytest.mark.asyncio
async def test_materials_library_roundtrip_covers_list_detail_and_summary(
    client: AsyncClient,
    db_session: Session,
):
    user = await _create_user(db_session, prefix="materials_flow")
    _attach_materials_access_subscription(db_session, user_id=user.id)
    login_payload = await _login(client, identifier=user.username)
    access_token = login_payload["access_token"]

    novel = Novel(
        user_id=user.id,
        title="Materials Spine Novel",
        author="E2E Author",
    )
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    job = IngestionJob(
        novel_id=novel.id,
        source_path="/tmp/materials-flow.txt",
        status="completed",
        total_chapters=2,
        processed_chapters=2,
    )
    chapter_one = Chapter(novel_id=novel.id, chapter_number=1, title="Chapter 1", content="content 1")
    chapter_two = Chapter(novel_id=novel.id, chapter_number=2, title="Chapter 2", content="content 2")
    character = Character(novel_id=novel.id, name="Hero")
    storyline = StoryLine(novel_id=novel.id, title="Main Line")
    worldview = WorldView(novel_id=novel.id, content="World setting")
    db_session.add_all([job, chapter_one, chapter_two, character, storyline, worldview])
    db_session.commit()

    list_response = await client.get(
        "/api/v1/materials",
        headers=_auth_headers(access_token),
    )
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert len(list_payload) == 1
    assert list_payload[0]["title"] == "Materials Spine Novel"
    assert list_payload[0]["status"] == "completed"
    assert list_payload[0]["chapters_count"] == 2

    detail_response = await client.get(
        f"/api/v1/materials/{novel.id}",
        headers=_auth_headers(access_token),
    )
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["chapters_count"] == 2
    assert detail_payload["characters_count"] == 1
    assert detail_payload["story_lines_count"] == 1
    assert detail_payload["has_world_view"] is True

    summary_response = await client.get(
        f"/api/v1/materials/{novel.id}/summary",
        headers=_auth_headers(access_token),
    )
    assert summary_response.status_code == 200
    summary_payload = summary_response.json()
    assert summary_payload["chapters_count"] == 2
    assert summary_payload["characters_count"] == 1
    assert summary_payload["storylines_count"] == 1
    assert summary_payload["has_worldview"] is True


@pytest.mark.asyncio
async def test_admin_redemption_codes_roundtrip_create_list_and_update(
    client: AsyncClient,
    db_session: Session,
):
    admin = await _create_user(db_session, prefix="admin_codes", is_superuser=True)
    _create_plan(
        db_session,
        name="pro",
        display_name="Pro",
        features={"ai_conversations_per_day": 9999},
    )

    login_payload = await _login(client, identifier=admin.username)
    access_token = login_payload["access_token"]

    create_response = await client.post(
        "/api/admin/codes",
        json={
            "tier": "pro",
            "duration_days": 30,
            "code_type": "single",
            "max_uses": 99,
            "notes": "launch batch",
        },
        headers=_auth_headers(access_token),
    )
    assert create_response.status_code == 200
    created_code = create_response.json()
    assert created_code["code_type"] == "single_use"
    assert created_code["max_uses"] == 1
    assert created_code["tier"] == "pro"

    list_response = await client.get(
        "/api/admin/codes?page=1&page_size=10&tier=pro&is_active=true",
        headers=_auth_headers(access_token),
    )
    assert list_response.status_code == 200
    list_payload = list_response.json()
    assert list_payload["total"] >= 1
    assert any(item["id"] == created_code["id"] for item in list_payload["items"])

    update_response = await client.put(
        f"/api/admin/codes/{created_code['id']}",
        json={"is_active": False, "notes": "disabled after e2e"},
        headers=_auth_headers(access_token),
    )
    assert update_response.status_code == 200
    update_payload = update_response.json()
    assert update_payload["is_active"] is False
    assert update_payload["notes"] == "disabled after e2e"


@pytest.mark.asyncio
async def test_admin_dashboard_stats_roundtrip_reports_commercialization_metrics(
    client: AsyncClient,
    db_session: Session,
    monkeypatch,
):
    monkeypatch.setenv("INSPIRATIONS_ENABLED", "true")
    admin = await _create_user(db_session, prefix="admin_dashboard", is_superuser=True)
    active_user = await _create_user(db_session, prefix="dashboard_user")

    project = await _create_project(
        client,
        (await _login(client, identifier=active_user.username))["access_token"],
        name="Dashboard Project",
    )
    assert project["name"] == "Dashboard Project"

    pending_inspiration = Inspiration(
        name="Pending Inspiration",
        description="pending",
        project_type="novel",
        tags="[]",
        snapshot_data='{"project_type":"novel","files":[]}',
        source="community",
        status="pending",
        author_id=active_user.id,
    )
    db_session.add(pending_inspiration)

    pro_plan = _create_plan(
        db_session,
        name="pro",
        display_name="Pro",
        features={"ai_conversations_per_day": 9999},
    )
    _attach_subscription(db_session, user_id=active_user.id, plan_id=pro_plan.id)

    points_transaction = PointsTransaction(
        user_id=active_user.id,
        amount=100,
        balance_after=100,
        transaction_type="check_in",
        is_expired=False,
    )
    invite_code = InviteCode(
        code="DASH-0001",
        owner_id=admin.id,
        max_uses=5,
        current_uses=0,
        is_active=True,
    )
    db_session.add(points_transaction)
    db_session.add(invite_code)
    db_session.commit()

    dashboard_login = await _login(client, identifier=admin.email)
    dashboard_response = await client.get(
        "/api/admin/dashboard/stats",
        headers=_auth_headers(dashboard_login["access_token"]),
    )
    assert dashboard_response.status_code == 200
    dashboard_payload = dashboard_response.json()
    assert dashboard_payload["total_users"] >= 2
    assert dashboard_payload["active_users"] >= 2
    assert dashboard_payload["total_projects"] >= 1
    assert dashboard_payload["pending_inspirations"] >= 1
    assert dashboard_payload["active_subscriptions"] >= 1
    assert dashboard_payload["pro_users"] >= 1
    assert dashboard_payload["active_invite_codes"] >= 1
    assert dashboard_payload["total_points_in_circulation"] >= 100
