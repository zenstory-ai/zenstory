"""连续的手动保存在时间窗内合并进同一个用户版本（新用户审计 #23）。

旧行为：≤10 字的改动由前端发 skip_version，永远不进历史；超过 10 字的每次防抖
保存都新建一个版本，免费用户 10 个额度几分钟就被自动保存刷光。
"""

import json
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from agent.tools.file_ops.edit import FileEditor
from models import File, FileVersion, Snapshot, User
from models.file_version import (
    CHANGE_SOURCE_SYSTEM,
    CHANGE_SOURCE_USER,
    CHANGE_TYPE_EDIT,
    VERSION_BASE_INTERVAL,
)
from models.subscription import SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password
from services.features.file_version_service import FileVersionService

PARAGRAPH = "　　夜里起了风，檐下的灯笼晃个不停。\n"


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


async def _create_draft(client: AsyncClient, headers: dict, content: str = PARAGRAPH) -> tuple[str, str]:
    project = await client.post("/api/v1/projects", json={"name": "合并"}, headers=headers)
    assert project.status_code == 200, project.text
    project_id = project.json()["id"]
    created = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "第一章", "content": content, "file_type": "draft"},
        headers=headers,
    )
    assert created.status_code == 200, created.text
    return project_id, created.json()["id"]


async def _save(client: AsyncClient, headers: dict, file_id: str, content: str, **extra) -> dict:
    resp = await client.put(f"/api/v1/files/{file_id}", json={"content": content, **extra}, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _versions(db_session: Session, file_id: str) -> list[FileVersion]:
    db_session.expire_all()
    return list(
        db_session.exec(
            select(FileVersion).where(FileVersion.file_id == file_id).order_by(FileVersion.version_number)
        ).all()
    )


def _user_versions(db_session: Session, file_id: str) -> list[FileVersion]:
    return [v for v in _versions(db_session, file_id) if v.change_source == CHANGE_SOURCE_USER]


def _age(db_session: Session, version: FileVersion, minutes: int) -> None:
    version.created_at = version.created_at - timedelta(minutes=minutes)
    db_session.add(version)
    db_session.commit()


def _content(db_session: Session, file_id: str, number: int) -> str:
    return FileVersionService().get_content_at_version(db_session, file_id, number)


def _bind_plan(db_session: Session, user: User, max_versions: int) -> None:
    plan = SubscriptionPlan(
        name=f"coalesce-{user.id[:8]}",
        display_name="Coalesce",
        display_name_en="Coalesce",
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


@pytest.mark.integration
async def test_five_saves_within_window_become_one_version_with_last_content(
    client: AsyncClient, db_session: Session
):
    _user, headers = await _login(client, db_session, "co_five")
    _project_id, file_id = await _create_draft(client, headers)

    drafts = [
        PARAGRAPH + "　　他推开门，走进了雨里。\n",
        PARAGRAPH + "　　他推开门，走进了雨里，没有回头。\n",
        PARAGRAPH + "　　他推开门，走进了雨里，没有回头。\n　　巷子很深。\n",
        PARAGRAPH + "　　他推开门，走进了雨里，没有回头。\n　　巷子很长。\n",  # 1 字改动
        PARAGRAPH + "　　他推开门，走进了夜雨里，没有回头。\n　　巷子很长。\n",
    ]
    for draft in drafts:
        await _save(client, headers, file_id, draft)

    user_versions = _user_versions(db_session, file_id)
    assert len(user_versions) == 1
    merged = user_versions[0]
    assert merged.change_type == CHANGE_TYPE_EDIT
    assert _content(db_session, file_id, merged.version_number) == drafts[-1]
    assert merged.char_count == len(drafts[-1])
    # v1 是新建文件的系统基线，不被合并改写。
    assert _content(db_session, file_id, 1) == PARAGRAPH


@pytest.mark.integration
async def test_save_after_window_creates_new_version(client: AsyncClient, db_session: Session):
    _user, headers = await _login(client, db_session, "co_window")
    _project_id, file_id = await _create_draft(client, headers)
    await _save(client, headers, file_id, PARAGRAPH + "第一段。\n")
    _age(db_session, _user_versions(db_session, file_id)[0], minutes=11)

    await _save(client, headers, file_id, PARAGRAPH + "第一段。\n第二段。\n")

    user_versions = _user_versions(db_session, file_id)
    assert len(user_versions) == 2
    assert _content(db_session, file_id, user_versions[0].version_number) == PARAGRAPH + "第一段。\n"
    assert _content(db_session, file_id, user_versions[1].version_number) == PARAGRAPH + "第一段。\n第二段。\n"


@pytest.mark.integration
async def test_ai_edit_version_in_between_is_not_merged(client: AsyncClient, db_session: Session):
    _user, headers = await _login(client, db_session, "co_ai")
    _project_id, file_id = await _create_draft(client, headers)
    await _save(client, headers, file_id, PARAGRAPH + "用户写的。\n")
    await _save(
        client, headers, file_id, PARAGRAPH + "用户写的。\nAI 补的。\n",
        change_type="ai_edit", change_summary="AI edit (reviewed)",
    )
    await _save(client, headers, file_id, PARAGRAPH + "用户写的。\nAI 补的。\n用户又改了。\n")

    versions = _versions(db_session, file_id)
    assert [v.change_type for v in versions] == ["create", "edit", "ai_edit", "edit"]
    assert _content(db_session, file_id, 3) == PARAGRAPH + "用户写的。\nAI 补的。\n"


@pytest.mark.integration
async def test_restore_version_in_between_is_not_merged(client: AsyncClient, db_session: Session):
    _user, headers = await _login(client, db_session, "co_restore")
    _project_id, file_id = await _create_draft(client, headers)
    await _save(client, headers, file_id, PARAGRAPH + "改动。\n")
    restored = await client.post(f"/api/v1/files/{file_id}/versions/1/rollback", headers=headers)
    assert restored.status_code == 200, restored.text
    await _save(client, headers, file_id, PARAGRAPH + "恢复后又写。\n")

    versions = _versions(db_session, file_id)
    assert [v.change_type for v in versions] == ["create", "edit", "restore", "edit"]
    assert _content(db_session, file_id, 3) == PARAGRAPH


@pytest.mark.integration
async def test_snapshot_after_version_blocks_merge_and_keeps_snapshot_content(
    client: AsyncClient, db_session: Session
):
    _user, headers = await _login(client, db_session, "co_snapshot")
    project_id, file_id = await _create_draft(client, headers)
    await _save(client, headers, file_id, PARAGRAPH + "拍快照前。\n")
    referenced = _user_versions(db_session, file_id)[0]
    # 历史快照按 version_id 引用版本。这里直接写一行引用最新用户版本的快照，
    # 确认合并不会改写它。
    db_session.add(
        Snapshot(
            project_id=project_id,
            data=json.dumps({"file_versions": [{
                "file_id": file_id,
                "version_number": referenced.version_number,
                "version_id": referenced.id,
            }]}),
            description="手动快照",
            snapshot_type="manual",
            created_at=datetime.utcnow(),
        )
    )
    db_session.commit()

    await _save(client, headers, file_id, PARAGRAPH + "拍快照后。\n")

    user_versions = _user_versions(db_session, file_id)
    assert len(user_versions) == 2
    assert _content(db_session, file_id, referenced.version_number) == PARAGRAPH + "拍快照前。\n"


@pytest.mark.integration
async def test_version_pinned_by_snapshot_id_is_not_merged(client: AsyncClient, db_session: Session):
    """旧数据里 snapshot_id 钉住的版本不被改写，即使项目里没有比它更晚的快照。"""
    _user, headers = await _login(client, db_session, "co_pinned")
    project_id, file_id = await _create_draft(client, headers)
    # 这行快照早于版本，later_snapshot 查询不会命中，能挡住合并的只有版本上的 snapshot_id。
    earlier = Snapshot(
        project_id=project_id,
        data="{}",
        description="旧快照",
        snapshot_type="manual",
        created_at=datetime.utcnow() - timedelta(hours=1),
    )
    db_session.add(earlier)
    db_session.commit()
    await _save(client, headers, file_id, PARAGRAPH + "钉住前。\n")
    pinned = _user_versions(db_session, file_id)[0]
    assert earlier.created_at < pinned.created_at
    pinned.snapshot_id = earlier.id
    db_session.add(pinned)
    db_session.commit()

    await _save(client, headers, file_id, PARAGRAPH + "钉住后。\n")

    user_versions = _user_versions(db_session, file_id)
    assert len(user_versions) == 2
    assert _content(db_session, file_id, pinned.version_number) == PARAGRAPH + "钉住前。\n"
    assert _content(db_session, file_id, user_versions[1].version_number) == PARAGRAPH + "钉住后。\n"


@pytest.mark.integration
async def test_ai_round_on_another_file_without_snapshot_row_keeps_merging(
    client: AsyncClient, db_session: Session
):
    """AI 改的是别的文件、又没有新快照行时，这个文件窗口内的手动保存照常合并。"""
    user, headers = await _login(client, db_session, "co_other_file")
    project_id, file_id = await _create_draft(client, headers)
    other = await client.post(
        f"/api/v1/projects/{project_id}/files",
        json={"title": "第二章", "content": PARAGRAPH, "file_type": "draft"},
        headers=headers,
    )
    assert other.status_code == 200, other.text
    other_id = other.json()["id"]
    await _save(client, headers, file_id, PARAGRAPH + "先写的。\n")

    FileEditor(db_session, user.id).edit_file(other_id, [{"op": "append", "text": "AI 写的。\n"}])

    assert _versions(db_session, other_id)[-1].change_type == "ai_edit"
    assert db_session.exec(select(Snapshot).where(Snapshot.project_id == project_id)).all() == []

    await _save(client, headers, file_id, PARAGRAPH + "先写的。\n又改了。\n")

    user_versions = _user_versions(db_session, file_id)
    assert len(user_versions) == 1
    assert _content(db_session, file_id, user_versions[0].version_number) == PARAGRAPH + "先写的。\n又改了。\n"


@pytest.mark.integration
async def test_merge_still_works_when_quota_is_full(client: AsyncClient, db_session: Session):
    user, headers = await _login(client, db_session, "co_quota")
    _bind_plan(db_session, user, max_versions=1)
    _project_id, file_id = await _create_draft(client, headers)
    first = await _save(client, headers, file_id, PARAGRAPH + "一。\n")
    assert first["version_quota_exceeded"] is False

    second = await _save(client, headers, file_id, PARAGRAPH + "一。\n二。\n")

    assert second["version_quota_exceeded"] is False
    user_versions = _user_versions(db_session, file_id)
    assert len(user_versions) == 1
    assert _content(db_session, file_id, user_versions[0].version_number) == PARAGRAPH + "一。\n二。\n"


@pytest.mark.integration
async def test_save_after_quota_blocked_restore_does_not_rewrite_pre_restore_version(
    client: AsyncClient, db_session: Session
):
    """额度已满时恢复不生成 restore 版本，最新版本还是恢复前那一版用户编辑。

    之后窗口内的保存如果并进它，恢复前的正文就从历史里消失了。合并只在最新版本
    仍等于这次保存要替换的正文时进行，这里正文已经是恢复出来的内容，所以不合并。
    """
    user, headers = await _login(client, db_session, "co_restore_quota")
    _bind_plan(db_session, user, max_versions=1)
    _project_id, file_id = await _create_draft(client, headers)
    await _save(client, headers, file_id, PARAGRAPH + "恢复前写的。\n")
    pre_restore = _user_versions(db_session, file_id)[0]

    restored = await client.post(f"/api/v1/files/{file_id}/versions/1/rollback", headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["version_quota_exceeded"] is True
    assert restored.json()["new_version_number"] is None

    after = await _save(client, headers, file_id, PARAGRAPH + "恢复后又写。\n")

    assert after["version_quota_exceeded"] is True
    assert _content(db_session, file_id, pre_restore.version_number) == PARAGRAPH + "恢复前写的。\n"
    assert [v.version_number for v in _versions(db_session, file_id)] == [1, pre_restore.version_number]


@pytest.mark.integration
async def test_amended_delta_and_base_versions_replay_correctly(client: AsyncClient, db_session: Session):
    """delta 版本相对上一版重算 diff；base 版本存全文。改写后整条链都能还原。"""
    _user, headers = await _login(client, db_session, "co_replay")
    _project_id, file_id = await _create_draft(client, headers)

    expected: dict[int, str] = {1: PARAGRAPH}
    # 每次离开时间窗再保存，铺出 v2..v(BASE_INTERVAL)，最后一版正好是 base。
    for number in range(2, VERSION_BASE_INTERVAL + 1):
        latest = _versions(db_session, file_id)[-1]
        if latest.change_source == CHANGE_SOURCE_USER:
            _age(db_session, latest, minutes=11)
        content = PARAGRAPH + "".join(f"第{i}行。\n" for i in range(2, number + 1))
        await _save(client, headers, file_id, content)
        expected[number] = content
        if number == VERSION_BASE_INTERVAL - 1:
            # 先在 delta 版本上合并一次
            amended = content.replace(f"第{number}行。", f"第{number}行，改过。")
            await _save(client, headers, file_id, amended)
            expected[number] = amended

    base_version = _versions(db_session, file_id)[-1]
    assert base_version.version_number == VERSION_BASE_INTERVAL
    assert base_version.is_base_version is True
    delta_version = _versions(db_session, file_id)[-2]
    assert delta_version.is_base_version is False

    amended_base = expected[VERSION_BASE_INTERVAL] + "　　base 上的合并。\n"
    await _save(client, headers, file_id, amended_base)
    expected[VERSION_BASE_INTERVAL] = amended_base

    versions = _versions(db_session, file_id)
    assert len(versions) == VERSION_BASE_INTERVAL
    for number, content in expected.items():
        assert _content(db_session, file_id, number) == content, number
    assert versions[-1].content == amended_base


@pytest.mark.integration
async def test_skip_version_saves_are_coalesced_not_dropped(client: AsyncClient, db_session: Session):
    """旧前端对 ≤10 字的改动发 skip_version=true；这些改动现在也会进历史。"""
    _user, headers = await _login(client, db_session, "co_skip")
    _project_id, file_id = await _create_draft(client, headers, content="　　他等了三秒。\n")

    await _save(client, headers, file_id, "　　他等了两秒。\n", skip_version=True)
    await _save(client, headers, file_id, "　　他等了一秒。\n", skip_version=True)

    user_versions = _user_versions(db_session, file_id)
    assert len(user_versions) == 1
    assert _content(db_session, file_id, user_versions[0].version_number) == "　　他等了一秒。\n"
    assert [v.change_source for v in _versions(db_session, file_id)] == [CHANGE_SOURCE_SYSTEM, CHANGE_SOURCE_USER]
    db_session.expire_all()
    assert db_session.get(File, file_id).content == "　　他等了一秒。\n"
