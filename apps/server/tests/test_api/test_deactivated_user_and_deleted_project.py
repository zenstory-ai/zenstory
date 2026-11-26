"""Cross-entry rejection regressions for deactivated users and soft-deleted projects.

Verifies two security/data-hygiene invariants across representative protected API entries:
1. After an account is deactivated (via ``DELETE /api/admin/users/{user_id}`` or
   ``is_active=False``), previously issued Access Tokens, Refresh Tokens, and
   ``X-Agent-API-Key`` credentials are immediately rejected across projects, files,
   chat, agent stream, export, and Agent API endpoints without mutating data.
2. After a project is soft-deleted (via ``DELETE /api/v1/projects/{project_id}``),
   every protected entry targeting the project or its child files/chat/export/stream
   consistently returns 404 without mutating child records.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import ChatSession, File, Project, User
from models.agent_api_key import AgentApiKey
from services.agent_auth_service import generate_api_key, hash_api_key
from services.core.auth_service import hash_password


def _seed_user_project_file_key(
    db_session: Session,
    *,
    prefix: str,
    is_superuser: bool = False,
) -> dict:
    suffix = uuid4().hex[:8]
    email = f"{prefix}_{suffix}@example.com"
    password = "StrongPassword123!"
    user = User(
        username=f"{prefix}_{suffix}",
        email=email,
        hashed_password=hash_password(password),
        email_verified=True,
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    draft = File(
        project_id=project.id,
        title="第一章 开端",
        file_type="draft",
        content="原始正文内容不可被篡改。",
    )
    db_session.add(draft)
    chat = ChatSession(
        user_id=user.id,
        project_id=project.id,
        title="默认会话",
        is_active=True,
    )
    db_session.add(chat)

    plain_key = generate_api_key()
    api_key = AgentApiKey(
        user_id=user.id,
        key_prefix=plain_key[:8],
        key_hash=hash_api_key(plain_key),
        name=f"Key {suffix}",
        scopes=["read", "write", "chat"],
        is_active=True,
        request_count=0,
    )
    db_session.add(api_key)
    db_session.commit()
    db_session.refresh(draft)
    db_session.refresh(chat)
    db_session.refresh(api_key)

    return {
        "user": user,
        "email": email,
        "password": password,
        "project": project,
        "file": draft,
        "chat": chat,
        "api_key": api_key,
        "plain_key": plain_key,
    }


async def _login(client: AsyncClient, email: str, password: str) -> tuple[str, str]:
    resp = await client.post(
        "/api/auth/login",
        data={"username": email, "password": password},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return body["access_token"], body["refresh_token"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_deactivated_user_rejected_across_protected_entries(
    client: AsyncClient, db_session: Session
):
    """After admin deactivates a user, existing JWT, refresh token, and Agent API key all fail."""
    admin = _seed_user_project_file_key(db_session, prefix="deact_admin", is_superuser=True)
    target = _seed_user_project_file_key(db_session, prefix="deact_target", is_superuser=False)

    admin_token, _ = await _login(client, admin["email"], admin["password"])
    access_token, refresh_token = await _login(client, target["email"], target["password"])

    project_id = target["project"].id
    file_id = target["file"].id
    user_id = target["user"].id
    api_key_id = target["api_key"].id
    bearer = {"Authorization": f"Bearer {access_token}"}
    agent_headers = {"X-Agent-API-Key": target["plain_key"]}

    # Sanity check: user can read their project while active.
    active_resp = await client.get(f"/api/v1/projects/{project_id}", headers=bearer)
    assert active_resp.status_code == 200

    # Admin deactivates target user via the real admin endpoint.
    deact_resp = await client.delete(
        f"/api/admin/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert deact_resp.status_code == 200
    assert deact_resp.json()["is_active"] is False

    # 1. Projects endpoints reject deactivated user (get_current_active_user -> 400 ERR_AUTH_INACTIVE_USER).
    for resp in (
        await client.get("/api/v1/projects", headers=bearer),
        await client.get(f"/api/v1/projects/{project_id}", headers=bearer),
    ):
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "ERR_AUTH_INACTIVE_USER"

    # 2. Files endpoints reject deactivated user without mutating content.
    for resp in (
        await client.get(f"/api/v1/projects/{project_id}/files", headers=bearer),
        await client.get(f"/api/v1/files/{file_id}", headers=bearer),
        await client.put(
            f"/api/v1/files/{file_id}",
            headers=bearer,
            json={"content": "被停用账号不应写入"},
        ),
    ):
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "ERR_AUTH_INACTIVE_USER"

    # 3. Chat & Agent stream reject deactivated user.
    chat_resp = await client.get(f"/api/v1/chat/session/{project_id}", headers=bearer)
    assert chat_resp.status_code == 400
    assert chat_resp.json()["error_code"] == "ERR_AUTH_INACTIVE_USER"

    stream_resp = await client.post(
        "/api/v1/agent/stream",
        headers=bearer,
        json={"project_id": project_id, "message": "继续写"},
    )
    assert stream_resp.status_code == 400
    assert stream_resp.json()["error_code"] == "ERR_AUTH_INACTIVE_USER"

    # 4. Export rejects deactivated user.
    export_resp = await client.get(f"/api/v1/projects/{project_id}/export/drafts", headers=bearer)
    assert export_resp.status_code == 400
    assert export_resp.json()["error_code"] == "ERR_AUTH_INACTIVE_USER"

    # 5. Previously issued refresh token cannot mint a new access token.
    refresh_resp = await client.post(
        "/api/auth/refresh",
        json={"refresh_token": refresh_token},
    )
    assert refresh_resp.status_code in (400, 401, 403)
    assert "access_token" not in refresh_resp.text

    # 6. Previously issued Agent API key is rejected (403 ERR_AUTH_INACTIVE_USER) and request_count stays 0.
    agent_api_resp = await client.get("/api/v1/agent/projects", headers=agent_headers)
    assert agent_api_resp.status_code == 403
    assert agent_api_resp.json()["error_code"] == "ERR_AUTH_INACTIVE_USER"

    db_session.expire_all()
    assert db_session.get(File, file_id).content == "原始正文内容不可被篡改。"
    assert db_session.get(AgentApiKey, api_key_id).request_count == 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_soft_deleted_project_rejected_across_protected_entries(
    client: AsyncClient, db_session: Session
):
    """After soft-deleting a project, all project/file/chat/stream/export/agent-API entries return 404."""
    seeded = _seed_user_project_file_key(db_session, prefix="softdel", is_superuser=False)
    access_token, _ = await _login(client, seeded["email"], seeded["password"])
    bearer = {"Authorization": f"Bearer {access_token}"}
    agent_headers = {"X-Agent-API-Key": seeded["plain_key"]}

    project_id = seeded["project"].id
    file_id = seeded["file"].id

    # Soft-delete the project through the real API.
    del_resp = await client.delete(f"/api/v1/projects/{project_id}", headers=bearer)
    assert del_resp.status_code == 200

    db_session.expire_all()
    project_row = db_session.get(Project, project_id)
    assert project_row is not None
    assert project_row.is_deleted is True
    assert project_row.deleted_at is not None

    # 1. Project read/update endpoints return 404.
    assert (await client.get(f"/api/v1/projects/{project_id}", headers=bearer)).status_code == 404
    assert (
        await client.put(
            f"/api/v1/projects/{project_id}",
            headers=bearer,
            json={"name": "不应改名"},
        )
    ).status_code == 404

    # 2. File tree, creation, read, update, and delete on child files return 404.
    assert (await client.get(f"/api/v1/projects/{project_id}/files", headers=bearer)).status_code == 404
    assert (
        await client.post(
            f"/api/v1/projects/{project_id}/files",
            headers=bearer,
            json={"title": "不应创建", "file_type": "draft", "content": "x"},
        )
    ).status_code == 404
    assert (await client.get(f"/api/v1/files/{file_id}", headers=bearer)).status_code == 404
    assert (
        await client.put(
            f"/api/v1/files/{file_id}",
            headers=bearer,
            json={"content": "已删项目不应允许改文件"},
        )
    ).status_code == 404
    assert (await client.delete(f"/api/v1/files/{file_id}", headers=bearer)).status_code == 404

    # 3. Chat session endpoints return 404.
    assert (await client.get(f"/api/v1/chat/session/{project_id}", headers=bearer)).status_code == 404
    assert (await client.post(f"/api/v1/chat/session/{project_id}/new", headers=bearer)).status_code == 404
    assert (await client.delete(f"/api/v1/chat/session/{project_id}", headers=bearer)).status_code == 404

    # 4. Agent stream rejects soft-deleted project with 404.
    stream_resp = await client.post(
        "/api/v1/agent/stream",
        headers=bearer,
        json={"project_id": project_id, "message": "继续写"},
    )
    assert stream_resp.status_code == 404

    # 5. Export rejects soft-deleted project with 404.
    export_resp = await client.get(f"/api/v1/projects/{project_id}/export/drafts", headers=bearer)
    assert export_resp.status_code == 404

    # 6. External Agent API rejects soft-deleted project with 404.
    assert (
        await client.get(f"/api/v1/agent/projects/{project_id}", headers=agent_headers)
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/agent/projects/{project_id}/files", headers=agent_headers)
    ).status_code == 404

    # Verify child file remained untouched (no content overwrite, no accidental delete).
    db_session.expire_all()
    file_row = db_session.get(File, file_id)
    assert file_row is not None
    assert file_row.content == "原始正文内容不可被篡改。"
    assert file_row.is_deleted is False
