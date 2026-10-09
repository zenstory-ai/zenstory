"""导出 TXT：第一行是作品名；勾选「包含大纲」时大纲排在剧本前面。"""

from urllib.parse import quote
from uuid import uuid4

import pytest
from httpx import AsyncClient

from models import File, Project, User
from services.core.auth_service import hash_password


async def _drama(client: AsyncClient, db_session) -> tuple[dict[str, str], Project]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"export_{suffix}",
        email=f"export_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    login = await client.post("/api/auth/login", data={"username": user.username, "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    project = Project(name="晚风知我意", owner_id=user.id, project_type="screenplay")
    db_session.add(project)
    db_session.commit()
    files = [
        ("第2集 离婚协议", "script", "【场1】律所。", 2),
        ("第1集 三周年", "script", "【场1】客厅，蛋糕。", 1),
        ("核心大纲", "outline", "女主撞破真相后离开。", 0),
        ("分集大纲（全60集）", "outline", "第1集：三周年。第2集：离婚。", 1),
        ("空白细纲", "outline", "   ", 2),
        ("林晚", "character", "女主，二十八岁。", 0),
    ]
    for title, file_type, content, order in files:
        db_session.add(File(project_id=project.id, title=title, file_type=file_type, content=content, order=order))
    db_session.commit()
    return headers, project


@pytest.mark.integration
async def test_export_starts_with_work_title_and_leaves_outline_out_by_default(client: AsyncClient, db_session):
    headers, project = await _drama(client, db_session)

    response = await client.get(f"/api/v1/projects/{project.id}/export/drafts", headers=headers)

    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert text.startswith("晚风知我意\n\n第1集 三周年\n\n")
    assert text.index("第1集 三周年") < text.index("第2集 离婚协议")
    assert "核心大纲" not in text
    assert "【大纲】" not in text


@pytest.mark.integration
async def test_export_with_outline_puts_outlines_before_episodes(client: AsyncClient, db_session):
    headers, project = await _drama(client, db_session)

    response = await client.get(
        f"/api/v1/projects/{project.id}/export/drafts",
        params={"include_outline": "true"},
        headers=headers,
    )

    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert text.startswith("晚风知我意\n\n【大纲】\n\n核心大纲\n\n女主撞破真相后离开。")
    assert text.index("分集大纲（全60集）") < text.index("【剧本】") < text.index("第1集 三周年")
    # Empty outlines and character cards are not exported.
    assert "空白细纲" not in text
    assert "林晚" not in text
    assert response.headers["content-disposition"].endswith(quote("晚风知我意_大纲和正文.txt"))


@pytest.mark.integration
async def test_export_with_outline_works_before_any_episode_is_written(client: AsyncClient, db_session):
    headers, project = await _drama(client, db_session)
    for file in db_session.query(File).filter(File.project_id == project.id, File.file_type == "script"):
        file.is_deleted = True
        db_session.add(file)
    db_session.commit()

    without_outline = await client.get(f"/api/v1/projects/{project.id}/export/drafts", headers=headers)
    with_outline = await client.get(
        f"/api/v1/projects/{project.id}/export/drafts",
        params={"include_outline": "true"},
        headers=headers,
    )

    assert without_outline.status_code == 404
    assert with_outline.status_code == 200
    text = with_outline.content.decode("utf-8-sig")
    assert "【剧本】" not in text
    assert "分集大纲（全60集）" in text
