"""
测试文件版本历史 API

测试版本列表、创建、对比、回滚等功能
"""

import pytest
from httpx import AsyncClient

from models import File, User


@pytest.mark.integration
async def test_get_version_list_creation_baseline_and_legacy_empty(client: AsyncClient, db_session):
    """新建正文有系统基线；旧的未版本化文件仍可返回空历史。"""
    # 创建用户
    from services.core.auth_service import hash_password
    user = User(
        username="user1", email="user1@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # 登录
    login_response = await client.post("/api/auth/login", data={"username": "user1", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 创建项目
    project_resp = await client.post(
        "/api/v1/projects",
        json={"name": "Test Project", "description": "Description"},
        headers=headers,
    )
    project_id = project_resp.json()["id"]

    # 创建文件
    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "Test File",
            "content": "Initial content",
            "file_type": "draft",
        },
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 新建正文的版本1保存原始内容，不占用户历史配额。
    resp = await client.get(
        f"/api/v1/files/{file_id}/versions",
        headers=headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["file_id"] == file_id
    assert data["file_title"] == "Test File"
    assert data["total"] == 1
    assert len(data["versions"]) == 1
    baseline = data["versions"][0]
    assert baseline["version_number"] == 1
    assert baseline["is_base_version"] is True
    assert baseline["change_type"] == "create"
    assert baseline["change_source"] == "system"
    content = await client.get(f"/api/v1/files/{file_id}/versions/1/content", headers=headers)
    assert content.status_code == 200
    assert content.json()["content"] == "Initial content"

    legacy = File(project_id=project_id, title="Legacy unversioned", file_type="draft", content="Legacy content")
    db_session.add(legacy)
    db_session.commit()
    empty = await client.get(f"/api/v1/files/{legacy.id}/versions", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["total"] == 0
    assert empty.json()["versions"] == []


@pytest.mark.integration
async def test_create_version(client: AsyncClient, db_session):
    """测试创建版本"""
    from services.core.auth_service import hash_password
    user = User(
        username="user2", email="user2@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user2", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 创建项目
    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    # 创建文件
    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 创建第一个用户版本，追加在系统基线之后。
    resp = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={
            "content": "First version content",
            "change_type": "edit",
            "change_source": "user",
            "change_summary": "Initial version",
        },
        headers=headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["file_id"] == file_id
    assert data["version_number"] == 2
    assert data["is_base_version"] is False
    assert data["change_type"] == "edit"
    assert data["change_source"] == "user"
    assert data["change_summary"] == "Initial version"
    assert data["word_count"] == 3  # "First version content"
    assert data["char_count"] == 21


@pytest.mark.integration
async def test_create_multiple_versions(client: AsyncClient, db_session):
    """测试创建多个版本，版本号递增"""
    from services.core.auth_service import hash_password
    user = User(
        username="user3", email="user3@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user3", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 创建三个版本
    contents = ["Version 1", "Version 2 with more text", "Version 3 with even more content here"]
    version_numbers = []

    for content in contents:
        resp = await client.post(
            f"/api/v1/files/{file_id}/versions",
            json={"content": content, "change_type": "edit", "change_source": "user"},
            headers=headers,
        )
        assert resp.status_code == 200
        version_numbers.append(resp.json()["version_number"])

    assert version_numbers == [2, 3, 4]


@pytest.mark.integration
async def test_get_version_list(client: AsyncClient, db_session):
    """测试获取版本列表"""
    from services.core.auth_service import hash_password
    user = User(
        username="user4", email="user4@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user4", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 创建几个版本
    for i in range(3):
        await client.post(
            f"/api/v1/files/{file_id}/versions",
            json={"content": f"Version {i+1}", "change_type": "edit", "change_source": "user"},
            headers=headers,
        )

    # 获取版本列表
    resp = await client.get(f"/api/v1/files/{file_id}/versions", headers=headers)

    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 4
    assert len(data["versions"]) == 4
    # 版本应该按版本号降序排列（最新的在前）
    assert [version["version_number"] for version in data["versions"]] == [4, 3, 2, 1]
    assert data["versions"][-1]["change_source"] == "system"


@pytest.mark.integration
async def test_get_version_list_pagination(client: AsyncClient, db_session):
    """测试版本列表分页"""
    from services.core.auth_service import hash_password
    user = User(
        username="user5", email="user5@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user5", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 创建5个版本
    for i in range(5):
        await client.post(
            f"/api/v1/files/{file_id}/versions",
            json={"content": f"Version {i+1}", "change_type": "edit"},
            headers=headers,
        )

    # 获取第一页（2个）
    resp1 = await client.get(
        f"/api/v1/files/{file_id}/versions?limit=2&offset=0",
        headers=headers,
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert len(data1["versions"]) == 2
    assert data1["versions"][0]["version_number"] == 6
    assert data1["versions"][1]["version_number"] == 5

    # 获取第二页（2个）
    resp2 = await client.get(
        f"/api/v1/files/{file_id}/versions?limit=2&offset=2",
        headers=headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert len(data2["versions"]) == 2
    assert data2["versions"][0]["version_number"] == 4
    assert data2["versions"][1]["version_number"] == 3


@pytest.mark.integration
async def test_get_version_content(client: AsyncClient, db_session):
    """测试获取特定版本的内容"""
    from services.core.auth_service import hash_password
    user = User(
        username="user6", email="user6@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user6", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    # 创建文件时使用初始内容
    initial_content = "Initial content"
    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": initial_content, "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 创建第一个用户版本（系统基线是版本1）。
    content1 = "First version with some content"
    first = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": content1, "change_type": "edit"},
        headers=headers,
    )
    assert first.status_code == 200
    first_number = first.json()["version_number"]
    assert first_number == 2

    # 更新文件内容以同步
    await client.put(
        f"/api/v1/files/{file_id}",
        json={"content": content1},
        headers=headers,
    )

    # 创建第二个版本
    content2 = "Second version with different content"
    second = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": content2, "change_type": "edit"},
        headers=headers,
    )
    assert second.status_code == 200
    second_number = second.json()["version_number"]
    assert second_number == 3  # 中间的 Web 正文保存在合并窗口内并进了版本2，不另起一版。

    # 更新文件内容以同步
    await client.put(
        f"/api/v1/files/{file_id}",
        json={"content": content2},
        headers=headers,
    )

    # 获取第一个版本的内容
    resp1 = await client.get(
        f"/api/v1/files/{file_id}/versions/{first_number}/content",
        headers=headers,
    )
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["version_number"] == first_number
    assert data1["content"] == content1
    assert data1["word_count"] == 5

    # 获取第二个版本的内容
    resp2 = await client.get(
        f"/api/v1/files/{file_id}/versions/{second_number}/content",
        headers=headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["version_number"] == second_number
    assert data2["content"] == content2


@pytest.mark.integration
async def test_compare_versions(client: AsyncClient, db_session):
    """测试版本对比"""
    from services.core.auth_service import hash_password
    user = User(
        username="user7", email="user7@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user7", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 创建两个用户版本，比较它们而不是系统基线。
    first = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": "Line 1\nLine 2\nLine 3", "change_type": "edit"},
        headers=headers,
    )

    second = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": "Line 1\nLine 2 modified\nLine 3\nLine 4", "change_type": "edit"},
        headers=headers,
    )
    assert first.status_code == second.status_code == 200
    first_number = first.json()["version_number"]
    second_number = second.json()["version_number"]
    assert (first_number, second_number) == (2, 3)

    # 对比版本
    resp = await client.get(
        f"/api/v1/files/{file_id}/versions/compare?v1={first_number}&v2={second_number}",
        headers=headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["file_id"] == file_id
    assert data["version1"]["number"] == first_number
    assert data["version2"]["number"] == second_number
    assert "unified_diff" in data
    assert "html_diff" in data
    assert "stats" in data
    assert "lines_added" in data["stats"]
    assert "lines_removed" in data["stats"]
    assert "+Line 4" in data["unified_diff"]


@pytest.mark.integration
async def test_rollback_to_version(client: AsyncClient, db_session, monkeypatch):
    """测试回滚到指定版本"""
    from services.core.auth_service import hash_password
    user = User(
        username="user8", email="user8@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user8", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 创建三个版本
    v1_content = "Version 1 content"
    first = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": v1_content, "change_type": "edit"},
        headers=headers,
    )
    assert first.status_code == 200
    first_number = first.json()["version_number"]
    assert first_number == 2

    v2_content = "Version 2 content"
    await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": v2_content, "change_type": "edit"},
        headers=headers,
    )

    v3_content = "Version 3 content"
    await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": v3_content, "change_type": "edit"},
        headers=headers,
    )

    indexed: list[dict] = []
    cache_bumps: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "services.llama_index.schedule_index_upsert",
        lambda **kwargs: indexed.append(kwargs),
    )
    monkeypatch.setattr(
        "services.infra.dashboard_cache.dashboard_cache.bump_project_version",
        lambda user_id, project_id: cache_bumps.append((user_id, project_id)),
    )

    # 回滚到第一个用户版本，不是创建时的系统基线。
    resp = await client.post(
        f"/api/v1/files/{file_id}/versions/{first_number}/rollback",
        headers=headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["restored_version"] == first_number
    # 系统基线 + 三次编辑 + 恢复前备份 + 恢复历史。POST /versions 不改正文，
    # 所以正文仍是 "Content"，和最新版本不同，恢复前要先备份它。
    assert data["new_version_number"] == 6
    assert data["file_id"] == file_id
    backup = await client.get(f"/api/v1/files/{file_id}/versions/5/content", headers=headers)
    assert backup.json()["content"] == "Content"
    assert len(indexed) == 1
    assert indexed[0]["entity_id"] == file_id
    assert indexed[0]["content"] == v1_content
    assert cache_bumps == [(user.id, project_id)]

    # 验证新版本的内容是第一个用户版本的内容。
    content_resp = await client.get(
        f"/api/v1/files/{file_id}/versions/{data['new_version_number']}/content",
        headers=headers,
    )
    assert content_resp.status_code == 200
    assert content_resp.json()["content"] == v1_content


@pytest.mark.integration
async def test_rollback_is_not_blocked_by_file_version_quota(client: AsyncClient, db_session):
    """回滚不受版本配额约束。

    历史行为是「额度满 -> 回滚 402」，那正是缺陷本身：回滚是用户从 AI 改坏的
    正文里自救的唯一手段，被额度挡住就等于 AI 的破坏不可撤销。
    """
    from datetime import datetime, timedelta

    from models.subscription import SubscriptionPlan, UserSubscription
    from services.core.auth_service import hash_password

    user = User(
        username="user8_quota",
        email="user8_quota@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    plan = SubscriptionPlan(
        name=f"rollback-file-version-limit-{user.id[:8]}",
        display_name="Rollback Version Limited",
        display_name_en="Rollback Version Limited",
        price_monthly_cents=999,
        price_yearly_cents=9999,
        features={"file_versions_per_file": 1},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)

    now = datetime.utcnow()
    subscription = UserSubscription(
        user_id=user.id,
        plan_id=plan.id,
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    )
    db_session.add(subscription)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user8_quota", "password": "password123"},
    )
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    first_version = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": "Version 1 content", "change_type": "edit"},
        headers=headers,
    )
    assert first_version.status_code == 200
    first_number = first_version.json()["version_number"]
    assert first_number == 2

    rollback_response = await client.post(
        f"/api/v1/files/{file_id}/versions/{first_number}/rollback",
        headers=headers,
    )

    assert rollback_response.status_code == 200, rollback_response.text
    payload = rollback_response.json()
    assert payload["success"] is True
    assert payload["restored_version"] == first_number
    assert payload["snapshot_created"] is False
    assert payload["version_quota_exceeded"] is True
    assert payload["new_version_number"] is None

    # 正文必须真的被恢复（旧实现里 402 会让 file.content 的赋值一起丢掉）
    file_resp = await client.get(f"/api/v1/files/{file_id}", headers=headers)
    assert file_resp.json()["content"] == "Version 1 content"


@pytest.mark.integration
async def test_get_latest_version(client: AsyncClient, db_session):
    """测试获取最新版本"""
    from services.core.auth_service import hash_password
    user = User(
        username="user9", email="user9@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user9", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 创建几个版本
    await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": "V1", "change_type": "edit"},
        headers=headers,
    )

    await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": "V2", "change_type": "edit"},
        headers=headers,
    )

    # 获取最新版本
    resp = await client.get(
        f"/api/v1/files/{file_id}/versions/latest",
        headers=headers,
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["version_number"] == 3
    assert data["word_count"] == 1


@pytest.mark.integration
async def test_version_not_found(client: AsyncClient, db_session):
    """测试访问不存在的版本"""
    from services.core.auth_service import hash_password
    user = User(
        username="user10", email="user10@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user10", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 尝试获取不存在的版本
    resp = await client.get(
        f"/api/v1/files/{file_id}/versions/999/content",
        headers=headers,
    )
    assert resp.status_code == 404


@pytest.mark.integration
async def test_version_content_integrity(client: AsyncClient, db_session):
    """测试版本内容完整性"""
    from services.core.auth_service import hash_password
    user = User(
        username="user11", email="user11@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user11", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 创建包含特殊字符和换行的版本
    special_content = "Line 1\nLine 2 with 特殊字符\nLine 3 with \"quotes\"\nLine 4\twith\ttabs"
    resp = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": special_content, "change_type": "edit"},
        headers=headers,
    )

    version_num = resp.json()["version_number"]

    # 获取内容并验证
    content_resp = await client.get(
        f"/api/v1/files/{file_id}/versions/{version_num}/content",
        headers=headers,
    )

    assert content_resp.status_code == 200
    assert content_resp.json()["content"] == special_content


@pytest.mark.integration
async def test_base_version_interval(client: AsyncClient, db_session):
    """测试 base version 的创建间隔（每10个版本）"""
    from services.core.auth_service import hash_password
    user = User(
        username="user12", email="user12@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user12", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 创建11个版本
    for i in range(11):
        await client.post(
            f"/api/v1/files/{file_id}/versions",
            json={"content": f"Version {i+1}", "change_type": "edit"},
            headers=headers,
        )

    # 获取所有版本
    resp = await client.get(
        f"/api/v1/files/{file_id}/versions?limit=100",
        headers=headers,
    )

    assert resp.status_code == 200
    versions = resp.json()["versions"]

    # 版本 1, 10 应该是 base version
    base_versions = [v for v in versions if v["is_base_version"]]
    assert len(base_versions) >= 2  # 至少版本1和版本10
    assert 1 in {v["version_number"] for v in base_versions}
    assert 10 in {v["version_number"] for v in base_versions}


@pytest.mark.integration
async def test_unauthorized_access(client: AsyncClient, db_session):
    """测试未授权访问版本"""
    from services.core.auth_service import hash_password
    # 创建第一个用户
    user1 = User(
        username="user13", email="user13@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user1)
    db_session.commit()

    login_resp1 = await client.post("/api/auth/login", data={"username": "user13", "password": "password123"})
    user1_token = login_resp1.json()["access_token"]
    user1_headers = {"Authorization": f"Bearer {user1_token}"}

    project_resp = await client.post(
        "/api/v1/projects",
        json={"name": "User1 Project"},
        headers=user1_headers,
    )
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "User1 File", "content": "Content", "file_type": "draft"},
        headers=user1_headers,
    )
    file_id = file_resp.json()["id"]

    # 创建一个版本
    await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": "User1 version", "change_type": "edit"},
        headers=user1_headers,
    )

    # 创建第二个用户
    user2 = User(
        username="user14", email="user14@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user2)
    db_session.commit()

    login_resp2 = await client.post("/api/auth/login", data={"username": "user14", "password": "password123"})
    user2_token = login_resp2.json()["access_token"]
    user2_headers = {"Authorization": f"Bearer {user2_token}"}

    # user2 尝试访问 user1 的文件版本
    resp = await client.get(
        f"/api/v1/files/{file_id}/versions",
        headers=user2_headers,
    )

    assert resp.status_code == 403


@pytest.mark.integration
async def test_no_authentication(client: AsyncClient):
    """测试没有认证访问版本"""
    resp = await client.get("/api/v1/files/nonexistent/versions")
    assert resp.status_code == 401


@pytest.mark.integration
async def test_auto_save_filter(client: AsyncClient, db_session):
    """测试过滤 auto_save 版本"""
    from services.core.auth_service import hash_password
    user = User(
        username="user15", email="user15@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user15", "password": "password123"})
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_resp = await client.post("/api/v1/projects", json={"name": "Test Project"}, headers=headers)
    project_id = project_resp.json()["id"]

    file_resp = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "Test File", "content": "Content", "file_type": "draft"},
        headers=headers,
    )
    file_id = file_resp.json()["id"]

    # 创建普通版本
    await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": "Normal edit", "change_type": "edit"},
        headers=headers,
    )

    # 创建 auto_save 版本
    await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": "Auto save", "change_type": "auto_save"},
        headers=headers,
    )

    # 获取版本列表（默认不包含 auto_save）
    resp1 = await client.get(
        f"/api/v1/files/{file_id}/versions",
        headers=headers,
    )
    assert resp1.status_code == 200
    assert len(resp1.json()["versions"]) == 2
    assert {version["change_type"] for version in resp1.json()["versions"]} == {"create", "edit"}

    # 获取版本列表（包含 auto_save）
    resp2 = await client.get(
        f"/api/v1/files/{file_id}/versions?include_auto_save=true",
        headers=headers,
    )
    assert resp2.status_code == 200
    assert len(resp2.json()["versions"]) == 3
    assert {version["change_type"] for version in resp2.json()["versions"]} == {"create", "edit", "auto_save"}
