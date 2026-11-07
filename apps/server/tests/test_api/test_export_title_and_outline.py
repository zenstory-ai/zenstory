"""导出 TXT：第一行是作品名，只含正文和剧本，大纲和角色都不进去。"""

from urllib.parse import quote
from uuid import uuid4

import pytest
from httpx import AsyncClient
from services.export_service import export_drafts_to_txt

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
async def test_export_starts_with_work_title_and_leaves_outlines_out(client: AsyncClient, db_session):
    headers, project = await _drama(client, db_session)

    response = await client.get(f"/api/v1/projects/{project.id}/export/drafts", headers=headers)

    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert text.startswith("晚风知我意\n\n第1集 三周年\n\n")
    assert text.index("第1集 三周年") < text.index("第2集 离婚协议")
    assert "核心大纲" not in text
    assert "【大纲】" not in text


@pytest.mark.integration
async def test_a_work_with_only_outlines_has_nothing_to_export(client: AsyncClient, db_session):
    headers, project = await _drama(client, db_session)
    for file in db_session.query(File).filter(File.project_id == project.id, File.file_type == "script"):
        file.is_deleted = True
        db_session.add(file)
    db_session.commit()

    response = await client.get(f"/api/v1/projects/{project.id}/export/drafts", headers=headers)

    assert response.status_code == 404


@pytest.mark.integration
async def test_export_response_matches_the_service_text_and_names_the_file(client: AsyncClient, db_session):
    headers, project = await _drama(client, db_session)

    response = await client.get(f"/api/v1/projects/{project.id}/export/drafts", headers=headers)

    drafts_text = "第1集 三周年\n\n【场1】客厅，蛋糕。\n\n---\n\n第2集 离婚协议\n\n【场1】律所。"
    expected = "晚风知我意\n\n" + drafts_text
    expected_bytes = ("\ufeff" + expected).encode("utf-8")
    assert response.status_code == 200
    assert response.content == expected_bytes
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["content-length"] == str(len(expected_bytes))
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''" + quote("晚风知我意_正文.txt")
    assert export_drafts_to_txt(db_session, project.id, title=project.name) == expected
    assert export_drafts_to_txt(db_session, project.id) == drafts_text
