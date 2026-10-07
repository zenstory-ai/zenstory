"""
Tests for Export API.

Tests export endpoint:
- GET /api/v1/projects/{project_id}/export/drafts
"""

import logging
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient

from core.error_codes import ErrorCode
from models import User
from models.subscription import SubscriptionPlan, UserSubscription


@pytest.mark.integration
async def test_export_drafts_success(client: AsyncClient, db_session):
    """Test successful export of project drafts."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user1", email="user1@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user1", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create a project
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "测试项目", "description": "测试描述"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    # Create draft files
    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "第一章 开始",
            "content": "这是第一章的内容",
            "file_type": "draft"
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "第二章 发展",
            "content": "这是第二章的内容",
            "file_type": "draft"
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    # Export drafts
    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    # Check for Content-Disposition header (case-insensitive)
    content_disposition = response.headers.get("content-disposition", "")
    assert len(content_disposition) > 0

    # Check content
    content = response.content.decode("utf-8-sig")  # UTF-8 BOM handling
    assert "第一章 开始" in content
    assert "这是第一章的内容" in content
    assert "第二章 发展" in content
    assert "这是第二章的内容" in content
    assert "---" in content  # Chapter separator


@pytest.mark.integration
async def test_export_drafts_includes_screenplay_scripts(client: AsyncClient, db_session):
    """Screenplay projects store episodes as scripts; export should include them."""
    from services.core.auth_service import hash_password

    user = User(
        username="user1_screenplay",
        email="user1_screenplay@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user1_screenplay", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "短剧项目", "description": "测试短剧导出", "project_type": "screenplay"},
        headers=headers,
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["id"]

    script_folder_id = f"{project_id}-script-folder"

    # Create two script episodes and one legacy draft episode.
    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "第10集：测试",
            "content": "这是第十集的内容",
            "file_type": "script",
            "parent_id": script_folder_id,
        },
        headers=headers,
    )

    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "第1集：测试",
            "content": "这是第一集的内容",
            "file_type": "script",
            "parent_id": script_folder_id,
        },
        headers=headers,
    )

    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "第9集：旧数据",
            "content": "这是第九集的内容",
            "file_type": "draft",
            "parent_id": script_folder_id,
        },
        headers=headers,
    )

    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers=headers,
    )

    assert response.status_code == 200
    content = response.content.decode("utf-8-sig")

    assert "第1集：测试" in content
    assert "这是第一集的内容" in content
    assert "第9集：旧数据" in content
    assert "这是第九集的内容" in content
    assert "第10集：测试" in content
    assert "这是第十集的内容" in content

    # Ensure ordering uses order/sequence numbers (1 < 9 < 10).
    pos1 = content.find("第1集：测试")
    pos9 = content.find("第9集：旧数据")
    pos10 = content.find("第10集：测试")
    assert pos1 < pos9 < pos10


@pytest.mark.integration
async def test_export_drafts_chinese_chapter_ordering(client: AsyncClient, db_session):
    """Test that Chinese chapter numbers are sorted correctly."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user2", email="user2@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user2", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create a project
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "排序测试项目", "description": "测试章节排序"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    # Create drafts with Chinese chapter numbers in random order
    drafts_data = [
        {"title": "第三章 后续", "content": "第三章内容"},
        {"title": "第一章 开始", "content": "第一章内容"},
        {"title": "第二章 中间", "content": "第二章内容"},
    ]

    for draft in drafts_data:
        await client.post(
            f"/api/v1/projects/{project_id}/files",
            json={
                "title": draft["title"],
                "content": draft["content"],
                "file_type": "draft"
            },
            headers={"Authorization": f"Bearer {token}"}
        )

    # Export drafts
    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    content = response.content.decode("utf-8-sig")

    # Check that chapters are in correct order
    first_chapter_pos = content.find("第一章 开始")
    second_chapter_pos = content.find("第二章 中间")
    third_chapter_pos = content.find("第三章 后续")

    assert first_chapter_pos < second_chapter_pos < third_chapter_pos


@pytest.mark.integration
async def test_export_drafts_arabic_chapter_ordering(client: AsyncClient, db_session):
    """Test that Arabic chapter numbers are sorted correctly."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user3", email="user3@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user3", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create a project
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "阿拉伯数字排序测试", "description": "测试阿拉伯数字章节排序"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    # Create drafts with Arabic chapter numbers in random order
    drafts_data = [
        {"title": "第3章 结尾", "content": "第三章内容"},
        {"title": "第1章 开头", "content": "第一章内容"},
        {"title": "第2章 中间", "content": "第二章内容"},
    ]

    for draft in drafts_data:
        await client.post(
            f"/api/v1/projects/{project_id}/files",
            json={
                "title": draft["title"],
                "content": draft["content"],
                "file_type": "draft"
            },
            headers={"Authorization": f"Bearer {token}"}
        )

    # Export drafts
    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    content = response.content.decode("utf-8-sig")

    # Check that chapters are in correct order
    first_chapter_pos = content.find("第1章 开头")
    second_chapter_pos = content.find("第2章 中间")
    third_chapter_pos = content.find("第3章 结尾")

    assert first_chapter_pos < second_chapter_pos < third_chapter_pos


@pytest.mark.integration
async def test_export_drafts_by_order_field(client: AsyncClient, db_session):
    """Test that order field takes precedence in sorting."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user4", email="user4@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user4", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create a project
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "order字段排序测试", "description": "测试order字段优先级"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    # Create drafts with explicit order field
    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "第二章",
            "content": "第二章内容",
            "file_type": "draft",
            "order": 2
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "第一章",
            "content": "第一章内容",
            "file_type": "draft",
            "order": 1
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    # Export drafts
    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    content = response.content.decode("utf-8-sig")

    # Check that order field takes precedence
    first_chapter_pos = content.find("第一章")
    second_chapter_pos = content.find("第二章")

    assert first_chapter_pos < second_chapter_pos


@pytest.mark.integration
async def test_export_drafts_no_drafts(client: AsyncClient, db_session, caplog):
    """Test exporting a project with no drafts returns 404."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user5", email="user5@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user5", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create a project
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "空项目", "description": "没有草稿的项目"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    # Try to export drafts
    with caplog.at_level(logging.INFO, logger="api.export"):
        response = await client.get(
            f"/api/v1/projects/{project_id}/export/drafts",
            headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 404
    data = response.json()
    assert data["error_code"] == ErrorCode.EXPORT_NO_DRAFTS
    empty_export_logs = [
        record for record in caplog.records
        if record.name == "api.export" and record.message == "export_project_drafts: No drafts found"
    ]
    assert len(empty_export_logs) == 1
    assert empty_export_logs[0].levelno == logging.INFO


@pytest.mark.integration
async def test_export_drafts_project_not_found(client: AsyncClient, db_session):
    """Test exporting a non-existent project returns 404."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user6", email="user6@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user6", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    response = await client.get(
        "/api/v1/projects/nonexistent-id/export/drafts",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 404
    data = response.json()
    assert data["error_code"] == ErrorCode.PROJECT_NOT_FOUND


@pytest.mark.integration
async def test_export_drafts_unauthorized(client: AsyncClient, db_session):
    """Test exporting another user's project returns 403."""
    # Create user1
    from services.core.auth_service import hash_password
    user1 = User(
        username="user7", email="user7@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user1)
    db_session.commit()

    # Login user1
    login_response1 = await client.post("/api/auth/login", data={"username": "user7", "password": "password123"})
    assert login_response1.status_code == 200
    user1_token = login_response1.json()["access_token"]

    # Create a project with user1
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "用户1的项目", "description": "属于用户1"},
        headers={"Authorization": f"Bearer {user1_token}"}
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    # Create and login as user2
    user2 = User(
        username="user8", email="user8@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user2)
    db_session.commit()

    login_response2 = await client.post("/api/auth/login", data={"username": "user8", "password": "password123"})
    assert login_response2.status_code == 200
    user2_token = login_response2.json()["access_token"]

    # Try to export user1's project as user2
    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers={"Authorization": f"Bearer {user2_token}"}
    )

    assert response.status_code == 403
    data = response.json()
    assert data["error_code"] == ErrorCode.NOT_AUTHORIZED_TO_EXPORT


@pytest.mark.integration
async def test_export_drafts_soft_deleted_project(client: AsyncClient, db_session):
    """Test exporting a soft-deleted project returns 404."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user9", email="user9@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user9", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create a project
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "已删除项目", "description": "将被删除"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    # Soft delete the project
    await client.delete(
        f"/api/v1/projects/{project_id}",
        headers={"Authorization": f"Bearer {token}"}
    )

    # Try to export drafts
    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 404
    data = response.json()
    assert data["error_code"] == ErrorCode.PROJECT_NOT_FOUND


@pytest.mark.integration
async def test_export_drafts_format_restricted_by_plan(client: AsyncClient, db_session):
    """Test exporting drafts with unsupported format returns 402."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user14", email="user14@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Create a restrictive plan that excludes txt export
    restricted_plan = SubscriptionPlan(
        name=f"export-restricted-{user.id[:8]}",
        display_name="Export Restricted",
        display_name_en="Export Restricted",
        price_monthly_cents=999,
        price_yearly_cents=9999,
        features={"export_formats": ["md"]},
        is_active=True,
    )
    db_session.add(restricted_plan)
    db_session.commit()
    db_session.refresh(restricted_plan)

    # Assign active subscription on restrictive plan
    now = datetime.utcnow()
    subscription = UserSubscription(
        user_id=user.id,
        plan_id=restricted_plan.id,
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    )
    db_session.add(subscription)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user14", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create a project and one draft
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "格式受限项目", "description": "测试导出格式限制"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert project_response.status_code == 200
    project_id = project_response.json()["id"]

    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "第一章",
            "content": "测试内容",
            "file_type": "draft"
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    # Export should be blocked by plan format restrictions
    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 402
    data = response.json()
    assert data["error_code"] == ErrorCode.QUOTA_EXPORT_FORMAT_RESTRICTED


@pytest.mark.integration
async def test_export_drafts_no_authentication(client: AsyncClient, db_session):
    """Test exporting without authentication returns 401."""
    response = await client.get("/api/v1/projects/some-id/export/drafts")

    assert response.status_code == 401


@pytest.mark.integration
async def test_export_drafts_only_draft_files(client: AsyncClient, db_session):
    """Test that only draft type files are exported, not other file types."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user10", email="user10@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user10", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create a project
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "混合文件项目", "description": "包含多种文件类型"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    # Create different file types
    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "大纲",
            "content": "这是大纲内容",
            "file_type": "outline"
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "第一章",
            "content": "这是草稿内容",
            "file_type": "draft"
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    # Export drafts
    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    content = response.content.decode("utf-8-sig")

    # Only draft content should be included
    assert "这是草稿内容" in content
    assert "这是大纲内容" not in content
    assert "第一章" in content
    assert "大纲" not in content


@pytest.mark.integration
async def test_export_drafts_content_with_special_characters(client: AsyncClient, db_session):
    """Test exporting drafts with special characters and formatting."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user11", email="user11@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user11", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create a project
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "特殊字符测试", "description": "测试特殊字符"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    # Create draft with special characters
    special_content = """这是带有特殊字符的内容：
    - 换行符测试

    - 制表符	测试

    - 引号"测试"

    - 中文标点：，。！？

    - 英文标点:,.!?

    - 数字123456

    - 特殊符号@#$%^&*()
    """

    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "特殊章节",
            "content": special_content,
            "file_type": "draft"
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    # Export drafts
    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200
    content = response.content.decode("utf-8-sig")

    # Check that special characters are preserved
    assert "换行符测试" in content
    assert "制表符\t测试" in content
    assert '引号"测试"' in content
    assert "中文标点：，。！？" in content
    assert "英文标点:,.!?" in content
    assert "数字123456" in content
    assert "特殊符号@#$%^&*()" in content


@pytest.mark.integration
async def test_export_drafts_utf8_bom(client: AsyncClient, db_session):
    """Test that exported file includes UTF-8 BOM for Windows compatibility."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user12", email="user12@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user12", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create a project
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "BOM测试", "description": "测试UTF-8 BOM"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    # Create a draft
    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "测试章",
            "content": "测试内容",
            "file_type": "draft"
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    # Export drafts
    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200

    # Check for UTF-8 BOM (EF BB BF)
    content_bytes = response.content
    assert content_bytes[:3] == b'\xef\xbb\xbf'


@pytest.mark.integration
async def test_export_drafts_filename_encoding(client: AsyncClient, db_session):
    """Test that filename is properly encoded for Chinese characters."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user13", email="user13@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user13", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create a project with Chinese name
    project_response = await client.post(
        "/api/v1/projects",
        json={"name": "中文项目名", "description": "测试文件名编码"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert project_response.status_code == 200
    project = project_response.json()
    project_id = project["id"]

    # Create a draft
    await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={
            "title": "第一章",
            "content": "内容",
            "file_type": "draft"
        },
        headers={"Authorization": f"Bearer {token}"}
    )

    # Export drafts
    response = await client.get(
        f"/api/v1/projects/{project_id}/export/drafts",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert response.status_code == 200

    # Check Content-Disposition header
    from urllib.parse import unquote
    content_disposition = response.headers.get("content-disposition", "")
    assert "attachment" in content_disposition
    assert "filename" in content_disposition
    # Decode the URL-encoded filename to check for Chinese characters
    # Format: filename*=UTF-8''%E4%B8%AD%E6%96%87%E9%A1%B9%E7%9B%AE%E5%90%8D_%E6%AD%A3%E6%96%87.txt
    assert "UTF-8''" in content_disposition
    # Extract and decode the filename
    if "UTF-8''" in content_disposition:
        encoded_filename = content_disposition.split("UTF-8''")[1].split(";")[0].strip()
        decoded_filename = unquote(encoded_filename)
        assert "_正文.txt" in decoded_filename or "%E6%AD%A3%E6%96%87.txt" in encoded_filename
