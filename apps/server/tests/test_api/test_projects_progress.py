"""GET /api/v1/projects/progress：作品卡片上的写作进度。"""

from uuid import uuid4

import pytest
from httpx import AsyncClient

from models import File, Project, User
from services.core.auth_service import hash_password


async def _login(client: AsyncClient, db_session) -> tuple[str, User]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"progress_{suffix}",
        email=f"progress_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    login = await client.post("/api/auth/login", data={"username": user.username, "password": "password123"})
    return login.json()["access_token"], user


def _project(db_session, owner: User, name: str, files: list[tuple[str, str]]) -> Project:
    project = Project(name=name, owner_id=owner.id)
    db_session.add(project)
    db_session.commit()
    for file_type, content in files:
        file = File(project_id=project.id, title=file_type, file_type=file_type, content=content)
        if file_type == "draft":
            # A cache left by a path that never refreshed it after AI writes.
            file.set_metadata({"word_count": 999})
        db_session.add(file)
    db_session.commit()
    return project


@pytest.mark.integration
async def test_progress_counts_written_chapters_and_real_word_counts(client: AsyncClient, db_session):
    token, user = await _login(client, db_session)
    _other_token, other = await _login(client, db_session)
    framework_only = _project(db_session, user, "只有框架", [("outline", "总纲"), ("draft", "")])
    writing = _project(
        db_session,
        user,
        "已开写",
        [("outline", "总纲"), ("draft", "山风吹过断崖"), ("draft", "他回头 look")],
    )
    empty = _project(db_session, user, "空", [])
    _project(db_session, other, "别人的", [("draft", "不该出现")])

    response = await client.get("/api/v1/projects/progress", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    by_id = {item["project_id"]: item for item in response.json()}
    assert set(by_id) == {framework_only.id, writing.id, empty.id}
    assert by_id[framework_only.id] == {
        "project_id": framework_only.id,
        "written_units": 0,
        "word_count": 0,
        "framework_ready": True,
    }
    # 6 Chinese characters + (3 Chinese characters + 1 Latin word), not the stale 999s.
    assert by_id[writing.id]["written_units"] == 2
    assert by_id[writing.id]["word_count"] == 10
    assert by_id[writing.id]["framework_ready"] is False
    assert by_id[empty.id]["framework_ready"] is False
