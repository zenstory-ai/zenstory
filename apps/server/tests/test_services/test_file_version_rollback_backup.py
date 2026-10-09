"""恢复历史版本前，先把「还没进历史」的当前正文备份成一个系统版本。

新用户审计 #9/#10：手动把「三秒」改成「两秒」后点「恢复到此版本」，两秒那一版
从未形成版本（额度已满 / 旧客户端跳过小改动），一恢复就永久丢了。
"""

from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import File, FileVersion, Project, User
from models.file_version import CHANGE_SOURCE_SYSTEM
from models.subscription import SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password
from services.features.file_version_service import FileVersionService

ORIGINAL = "　　他在门口等了三秒。\n　　雨停了。\n"
EDITED = "　　他在门口等了两秒。\n　　雨停了。\n"


async def _login(client: AsyncClient, db_session: Session, username: str) -> tuple[User, dict]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    resp = await client.post("/api/auth/login", data={"username": username, "password": "password123"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _bind_plan(db_session: Session, user: User, max_versions: int) -> None:
    plan = SubscriptionPlan(
        name=f"rollback-backup-{user.id[:8]}",
        display_name="Rollback backup",
        display_name_en="Rollback backup",
        price_monthly_cents=999,
        price_yearly_cents=9999,
        features={"file_versions_per_file": max_versions},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)
    now = datetime.utcnow()
    db_session.add(
        UserSubscription(
            user_id=user.id,
            plan_id=plan.id,
            status="active",
            current_period_start=now - timedelta(days=1),
            current_period_end=now + timedelta(days=30),
            cancel_at_period_end=False,
        )
    )
    db_session.commit()


async def _create_draft(client: AsyncClient, headers: dict, content: str = ORIGINAL) -> str:
    project = await client.post("/api/v1/projects", json={"name": "恢复备份"}, headers=headers)
    assert project.status_code == 200, project.text
    created = await client.post(
        f"/api/v1/projects/{project.json()['id']}/files",
        json={"title": "第一章", "content": content, "file_type": "draft"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return created.json()["id"]


def _versions(db_session: Session, file_id: str) -> list[FileVersion]:
    db_session.expire_all()
    return list(
        db_session.exec(
            select(FileVersion)
            .where(FileVersion.file_id == file_id)
            .order_by(FileVersion.version_number)
        ).all()
    )


def _age_versions(db_session: Session, file_id: str, minutes: int = 30) -> None:
    """把已有版本挪出合并时间窗，下一次保存只能新建版本（或因额度被拒）。"""
    for version in _versions(db_session, file_id):
        version.created_at = version.created_at - timedelta(minutes=minutes)
        db_session.add(version)
    db_session.commit()


async def _content_of(client: AsyncClient, headers: dict, file_id: str, number: int) -> str:
    resp = await client.get(f"/api/v1/files/{file_id}/versions/{number}/content", headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["content"]


@pytest.mark.integration
async def test_restore_backs_up_unversioned_edit_even_when_quota_is_full(
    client: AsyncClient, db_session: Session
):
    user, headers = await _login(client, db_session, "rb_backup")
    _bind_plan(db_session, user, max_versions=1)
    file_id = await _create_draft(client, headers)

    # 用掉唯一的用户版本额度，再离开合并窗口：之后「三秒 -> 两秒」这次保存进不了历史。
    first = await client.put(
        f"/api/v1/files/{file_id}", json={"content": ORIGINAL + "　　他走了进去。\n"}, headers=headers
    )
    assert first.status_code == 200, first.text
    _age_versions(db_session, file_id)
    saved = await client.put(f"/api/v1/files/{file_id}", json={"content": EDITED}, headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version_quota_exceeded"] is True
    for version in _versions(db_session, file_id):
        assert "两秒" not in await _content_of(client, headers, file_id, version.version_number)

    restored = await client.post(f"/api/v1/files/{file_id}/versions/1/rollback", headers=headers)
    assert restored.status_code == 200, restored.text
    db_session.expire_all()
    assert db_session.get(File, file_id).content == ORIGINAL

    backups = [v for v in _versions(db_session, file_id) if v.change_summary == "Before restoring version 1"]
    assert len(backups) == 1
    backup = backups[0]
    assert backup.change_source == CHANGE_SOURCE_SYSTEM
    assert backup.is_base_version is True
    assert await _content_of(client, headers, file_id, backup.version_number) == EDITED

    # 备份本身就是一个可恢复的版本：能把「两秒」找回来。
    back = await client.post(
        f"/api/v1/files/{file_id}/versions/{backup.version_number}/rollback", headers=headers
    )
    assert back.status_code == 200, back.text
    db_session.expire_all()
    assert db_session.get(File, file_id).content == EDITED


@pytest.mark.integration
async def test_repeated_restore_without_edits_adds_no_backup(client: AsyncClient, db_session: Session):
    _user, headers = await _login(client, db_session, "rb_repeat")
    file_id = await _create_draft(client, headers)
    _age_versions(db_session, file_id)
    edited = await client.put(f"/api/v1/files/{file_id}", json={"content": EDITED}, headers=headers)
    assert edited.status_code == 200, edited.text

    for _ in range(2):
        resp = await client.post(f"/api/v1/files/{file_id}/versions/1/rollback", headers=headers)
        assert resp.status_code == 200, resp.text

    versions = _versions(db_session, file_id)
    assert [v.change_summary for v in versions if (v.change_summary or "").startswith("Before restoring")] == []
    assert db_session.get(File, file_id).content == ORIGINAL


@pytest.mark.integration
async def test_backup_failure_aborts_restore_and_keeps_content(
    client: AsyncClient, db_session: Session, monkeypatch
):
    _user, headers = await _login(client, db_session, "rb_fail")
    file_id = await _create_draft(client, headers)
    # 模拟一份没进历史的正文（例如旧客户端跳过了版本）。
    file = db_session.get(File, file_id)
    file.content = EDITED
    db_session.add(file)
    db_session.commit()
    before = len(_versions(db_session, file_id))

    real_create_version = FileVersionService.create_version

    def failing_backup(self, *args, **kwargs):
        if (kwargs.get("change_summary") or "").startswith("Before restoring"):
            raise RuntimeError("version storage unavailable")
        return real_create_version(self, *args, **kwargs)

    monkeypatch.setattr(FileVersionService, "create_version", failing_backup)

    resp = await client.post(f"/api/v1/files/{file_id}/versions/1/rollback", headers=headers)
    assert resp.status_code == 500, resp.text
    assert resp.json()["error_code"] == "ERR_VERSION_RESTORE_FAILED"

    db_session.expire_all()
    assert db_session.get(File, file_id).content == EDITED
    assert len(_versions(db_session, file_id)) == before


@pytest.mark.unit
def test_service_backup_runs_for_conditional_undo_path(db_session: Session):
    """AI 修改卡片的「撤销」也走 rollback_to_version，同样先备份没进历史的正文。"""
    user = User(username="rb_undo", email="rb_undo@example.com", hashed_password="x", is_active=True)
    db_session.add(user)
    db_session.commit()
    project = Project(name="撤销", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    file = File(project_id=project.id, title="第一章", file_type="draft", content=ORIGINAL)
    db_session.add(file)
    db_session.commit()
    service = FileVersionService()
    service.create_initial_version(db_session, file)
    db_session.commit()
    file.content = EDITED
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    restored, _new_version, _quota = service.rollback_to_version(
        db_session, file.id, 1, user_id=user.id, expected_updated_at=file.updated_at
    )

    assert restored.content == ORIGINAL
    backup = service.get_version_by_number(db_session, file.id, 2)
    assert backup is not None and backup.change_summary == "Before restoring version 1"
    assert service.get_content_at_version(db_session, file.id, 2) == EDITED
