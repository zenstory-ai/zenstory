"""项目列表的 updated_at 反映最近一次文件编辑（新用户审计 #33）。

改章节正文不会动 project 行，项目卡因此一直显示「3 天前」，排序也不跟着变。
"""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from config.datetime_utils import normalize_datetime_to_utc
from models import File, Project, User
from services.core.auth_service import hash_password


def _parse(value: str) -> datetime:
    return normalize_datetime_to_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


@pytest.mark.integration
async def test_file_edit_moves_project_activity_time_without_writing_project_row(
    client: AsyncClient, db_session: Session
):
    user = User(
        username="pa_time", email="pa_time@example.com",
        hashed_password=hash_password("password123"), email_verified=True, is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    login = await client.post("/api/auth/login", data={"username": "pa_time", "password": "password123"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    stale = datetime.utcnow() - timedelta(days=3)
    edited = Project(name="三天前建的", owner_id=user.id, created_at=stale, updated_at=stale)
    untouched = Project(name="没动过", owner_id=user.id, created_at=stale, updated_at=stale - timedelta(hours=1))
    db_session.add_all([edited, untouched])
    db_session.commit()
    chapter = File(project_id=edited.id, title="第一章", file_type="draft", content="旧", updated_at=stale)
    deleted = File(
        project_id=untouched.id, title="已删", file_type="draft", content="x",
        is_deleted=True, updated_at=datetime.utcnow(),
    )
    db_session.add_all([chapter, deleted])
    db_session.commit()
    edited_id, untouched_id, chapter_id = edited.id, untouched.id, chapter.id

    saved = await client.put(f"/api/v1/files/{chapter_id}", json={"content": "　　新写的一段。"}, headers=headers)
    assert saved.status_code == 200, saved.text
    file_edit_time = _parse(saved.json()["updated_at"])

    listed = await client.get("/api/v1/projects", headers=headers)
    assert listed.status_code == 200, listed.text
    by_id = {project["id"]: project for project in listed.json()}
    assert _parse(by_id[edited_id]["updated_at"]) == file_edit_time
    # 已删除文件不算活动
    assert _parse(by_id[untouched_id]["updated_at"]) == normalize_datetime_to_utc(stale - timedelta(hours=1))
    # 最近有活动的项目排在前面（前端按 updated_at 排序）
    assert _parse(by_id[edited_id]["updated_at"]) > _parse(by_id[untouched_id]["updated_at"])

    db_session.expire_all()
    assert normalize_datetime_to_utc(db_session.get(Project, edited_id).updated_at) == normalize_datetime_to_utc(stale)
