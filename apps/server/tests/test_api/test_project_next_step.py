"""GET /api/v1/projects/{id}/next-step：框架已就绪、还没有正文时提议写第一章。"""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from models import File, Project, User
from services.core.auth_service import hash_password


async def _login_with_project(client: AsyncClient, db_session, project_type: str):
    suffix = uuid4().hex[:8]
    user = User(
        username=f"next_step_{suffix}",
        email=f"next_step_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="下一步", owner_id=user.id, project_type=project_type)
    db_session.add(project)
    db_session.commit()
    login = await client.post(
        "/api/auth/login", data={"username": user.username, "password": "password123"}
    )
    return login.json()["access_token"], project


def _add_file(db_session, project, file_type: str, content: str) -> None:
    db_session.add(File(project_id=project.id, title=file_type, file_type=file_type, content=content))
    db_session.commit()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("project_type", "files", "expected_message"),
    [
        ("novel", [], None),
        ("novel", [("outline", "第一卷：主角入城")], "按大纲写第一章正文"),
        ("screenplay", [("character", "沈晚：假千金")], "按分集大纲写第 1 集剧本"),
        ("short", [("lore", "灵气复苏")], "按大纲开始写正文"),
        # 只建了标题、还没写内容的正文文件不算已经开写
        ("novel", [("outline", "总纲"), ("draft", "")], "按大纲写第一章正文"),
        ("novel", [("outline", "总纲"), ("draft", "第一章 雨夜")], None),
        ("screenplay", [("outline", "分集大纲"), ("script", "第1集 △雨夜")], None),
    ],
)
async def test_next_step_offers_first_chapter_only_when_framework_exists_without_prose(
    client: AsyncClient, db_session, project_type, files, expected_message
):
    token, project = await _login_with_project(client, db_session, project_type)
    for file_type, content in files:
        _add_file(db_session, project, file_type, content)

    response = await client.get(
        f"/api/v1/projects/{project.id}/next-step",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    next_step = response.json()["next_step"]
    if expected_message is None:
        assert next_step is None
    else:
        assert next_step["kind"] == "write_first_chapter"
        assert next_step["message"] == expected_message


@pytest.mark.integration
async def test_next_step_is_private_to_the_project_owner(client: AsyncClient, db_session):
    _token, project = await _login_with_project(client, db_session, "novel")
    other_token, _other = await _login_with_project(client, db_session, "novel")

    response = await client.get(
        f"/api/v1/projects/{project.id}/next-step",
        headers={"Authorization": f"Bearer {other_token}"},
    )

    assert response.status_code in (403, 404)
