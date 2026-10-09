"""版本列表的 current_version_number：只有内容与正文完全一致的那一版才算「当前内容」。

新用户审计 #24：列表第一行固定写着「最新保存的版本」，哪怕正文里还有没进历史的改动，
用户据此判断「最新的已经存了」然后点恢复，丢了稿。
"""

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import File, User
from services.core.auth_service import hash_password


async def _setup(client: AsyncClient, db_session: Session, username: str) -> tuple[dict, str]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    login = await client.post("/api/auth/login", data={"username": username, "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    project = await client.post("/api/v1/projects", json={"name": "当前内容"}, headers=headers)
    created = await client.post(
        f"/api/v1/projects/{project.json()['id']}/files",
        json={"title": "第一章", "content": "　　第一句。\n", "file_type": "draft"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return headers, created.json()["id"]


@pytest.mark.integration
async def test_current_marker_points_at_version_matching_live_content(client: AsyncClient, db_session: Session):
    headers, file_id = await _setup(client, db_session, "cm_match")
    saved = await client.put(
        f"/api/v1/files/{file_id}", json={"content": "　　第一句。\n　　第二句。\n"}, headers=headers
    )
    assert saved.status_code == 200, saved.text

    listed = await client.get(f"/api/v1/files/{file_id}/versions", headers=headers)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["current_version_number"] == body["versions"][0]["version_number"] == 2

    page_two = await client.get(f"/api/v1/files/{file_id}/versions?offset=1", headers=headers)
    assert page_two.json()["current_version_number"] is None


@pytest.mark.integration
async def test_current_marker_is_null_when_live_content_is_not_in_history(client: AsyncClient, db_session: Session):
    headers, file_id = await _setup(client, db_session, "cm_unsaved")
    # 正文已变化但没有任何版本记录它（例如额度已满时的保存）。
    file = db_session.get(File, file_id)
    file.content = "　　第一句，改过了。\n"
    db_session.add(file)
    db_session.commit()

    listed = await client.get(f"/api/v1/files/{file_id}/versions", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["versions"][0]["version_number"] == 1
    assert listed.json()["current_version_number"] is None
