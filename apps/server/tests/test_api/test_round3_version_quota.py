"""
Round3 G7（version-quota）API 层回归测试。

覆盖：
- 缺陷 #5：AI 版本吃掉用户 per-file 版本额度后，用户的保存与回滚被永久锁死；
  且「正文已 commit 却返回 402」是误导性失败。
- 缺陷 #11：PUT /api/v1/files/{id} 既不取写锁也不做乐观并发校验，
  编辑器防抖自动保存的陈旧整篇快照会静默回退 agent 的 edit_file 结果。
"""

import asyncio
import threading
import time
from datetime import datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

import database
from agent.tools.file_ops import FileEditor, FileToolExecutor
from core.error_codes import ErrorCode
from models import File, FileVersion, Project, User
from models.file_version import CHANGE_SOURCE_USER
from models.subscription import SubscriptionPlan, UserSubscription
from services.core.auth_service import hash_password

BASE_CONTENT = "第三章 雨夜\n林风推开门。\n远处传来钟声。\n"


@pytest.fixture
def agent_sessions_on_test_db(db_session: Session, monkeypatch):
    """
    agent 侧建版本用的是独立 session（database.create_session），
    生产里连的是真引擎；测试里指到同一个临时 sqlite，才能观察到写入结果。
    """
    engine = db_session.get_bind()
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    return engine


async def _setup_user(client: AsyncClient, db_session: Session, username: str):
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

    resp = await client.post(
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    project = Project(name="Round3 G7 Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    return user, project, headers


def _make_draft(db_session: Session, project: Project, content: str = BASE_CONTENT) -> File:
    file = File(
        project_id=project.id,
        title="第三章",
        file_type="draft",
        content=content,
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)
    return file


def _bind_plan(db_session: Session, user: User, max_versions: int) -> None:
    plan = SubscriptionPlan(
        name=f"round3-g7-{user.id[:8]}",
        display_name="Round3 G7",
        display_name_en="Round3 G7",
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


# ==================== 缺陷 #5 ====================

@pytest.mark.integration
async def test_ai_edits_do_not_lock_out_user_save_and_rollback(
    client: AsyncClient, db_session: Session, agent_sessions_on_test_db
):
    """AI 连续编辑之后，用户的手动保存与版本回滚都必须照常工作。"""
    user, project, headers = await _setup_user(client, db_session, "r3g7_lockout")
    _bind_plan(db_session, user, max_versions=2)
    file = _make_draft(db_session, project, content="原始正文：好好的一章。")
    file_id = file.id

    # 用户自己先建一个版本（回滚的目标）
    first = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": "原始正文：好好的一章。", "change_type": "edit"},
        headers=headers,
    )
    assert first.status_code == 200, first.text

    # AI 通过真实的 agent 工具入口连写 5 次，远超 2 的额度
    executor = FileToolExecutor(db_session, user.id)
    for i in range(5):
        result = executor.edit_file(
            id=file_id,
            edits=[{"op": "append", "text": f"\nAI 把正文改坏了 {i}。"}],
        )
        assert result["edits_applied"] == 1

    db_session.expire_all()

    # 1) 用户的手动保存不该被 AI 耗掉的额度挡住
    saved = await client.put(
        f"/api/v1/files/{file_id}",
        json={"content": "用户手动修改：我要改掉 AI 写坏的段落。"},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version_quota_exceeded"] is False

    db_session.expire_all()
    assert db_session.get(File, file_id).content.startswith("用户手动修改")

    user_versions = db_session.exec(
        select(FileVersion).where(
            FileVersion.file_id == file_id,
            FileVersion.change_source == CHANGE_SOURCE_USER,
        )
    ).all()
    assert len(user_versions) == 2, "用户的两个额度都应真实可用"

    # 2) 回滚永远不该被额度挡住（这是从 AI 破坏中自救的唯一手段）
    rollback = await client.post(
        f"/api/v1/files/{file_id}/versions/1/rollback",
        headers=headers,
    )
    assert rollback.status_code == 200, rollback.text

    db_session.expire_all()
    restored = db_session.get(File, file_id).content
    assert restored == "原始正文：好好的一章。"
    assert "AI 把正文改坏了" not in restored


@pytest.mark.integration
async def test_put_saves_content_when_user_version_quota_is_exhausted(
    client: AsyncClient, db_session: Session
):
    """用户自己写满额度后：正文照常保存，只是不再生成版本快照，且不再谎报失败。"""
    user, project, headers = await _setup_user(client, db_session, "r3g7_exhaust")
    _bind_plan(db_session, user, max_versions=1)
    file = _make_draft(db_session, project)
    file_id = file.id

    first = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": BASE_CONTENT, "change_type": "edit"},
        headers=headers,
    )
    assert first.status_code == 200, first.text
    # 离开合并窗口：窗口内的保存会合并进已有版本，不新占额度。
    first_version = db_session.get(FileVersion, first.json()["id"])
    first_version.created_at -= timedelta(minutes=30)
    db_session.add(first_version)
    db_session.commit()

    resp = await client.put(
        f"/api/v1/files/{file_id}",
        json={"content": BASE_CONTENT + "用户写的新一段。\n"},
        headers=headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["version_quota_exceeded"] is True
    assert resp.json()["content"] == BASE_CONTENT + "用户写的新一段。\n"

    db_session.expire_all()
    assert db_session.get(File, file_id).content == BASE_CONTENT + "用户写的新一段。\n"

    versions = db_session.exec(
        select(FileVersion).where(FileVersion.file_id == file_id)
    ).all()
    assert len(versions) == 1, "额度满时不应再生成版本"


# ==================== 缺陷 #11 ====================

@pytest.mark.integration
async def test_stale_autosave_is_rejected_with_409(
    client: AsyncClient, db_session: Session, agent_sessions_on_test_db
):
    """编辑器带着加载时的 updated_at 提交陈旧整篇快照 -> 409，AI 的编辑不被覆盖。"""
    user, project, headers = await _setup_user(client, db_session, "r3g7_stale")
    file = _make_draft(db_session, project)
    file_id = file.id

    loaded = await client.get(f"/api/v1/files/{file_id}", headers=headers)
    assert loaded.status_code == 200
    base_updated_at = loaded.json()["updated_at"]
    stale_snapshot = loaded.json()["content"] + "用户手打的一句。\n"

    # agent 在另一个 session 里改同一份文件
    time.sleep(0.01)
    with Session(agent_sessions_on_test_db) as agent_session:
        editor = FileEditor(agent_session, user_id=user.id)
        result = editor.edit_file(
            file_id,
            [{"op": "replace", "old": "远处传来钟声。", "new": "远处传来钟声，像谁在数着他的心跳。"}],
        )
        assert result["edits_applied"] == 1

    db_session.expire_all()

    resp = await client.put(
        f"/api/v1/files/{file_id}",
        json={
            "title": "第三章",
            "content": stale_snapshot,
            "skip_version": True,
            "base_updated_at": base_updated_at,
        },
        headers=headers,
    )
    assert resp.status_code == 409, resp.text
    payload = resp.json()
    assert payload["error_code"] == ErrorCode.RESOURCE_CONFLICT
    # 409 要把服务端当前正文回带，前端才能刷新/合并
    assert "像谁在数着他的心跳" in payload["error_detail"]["current_content"]

    db_session.expire_all()
    final = db_session.get(File, file_id).content
    assert "像谁在数着他的心跳" in final, "AI 的编辑被陈旧自动保存静默回退了"
    assert "用户手打的一句" not in final


@pytest.mark.integration
async def test_fresh_base_updated_at_and_missing_token_still_save(
    client: AsyncClient, db_session: Session
):
    """令牌新鲜时正常保存；不带令牌的老客户端保持向后兼容。"""
    user, project, headers = await _setup_user(client, db_session, "r3g7_fresh")
    file = _make_draft(db_session, project)
    file_id = file.id

    loaded = await client.get(f"/api/v1/files/{file_id}", headers=headers)
    fresh_token = loaded.json()["updated_at"]

    resp = await client.put(
        f"/api/v1/files/{file_id}",
        json={
            "content": BASE_CONTENT + "第一次保存。\n",
            "skip_version": True,
            "base_updated_at": fresh_token,
        },
        headers=headers,
    )
    assert resp.status_code == 200, resp.text

    # 不带令牌 -> 不做乐观并发校验（老客户端）
    resp2 = await client.put(
        f"/api/v1/files/{file_id}",
        json={"content": BASE_CONTENT + "第二次保存。\n", "skip_version": True},
        headers=headers,
    )
    assert resp2.status_code == 200, resp2.text

    db_session.expire_all()
    assert db_session.get(File, file_id).content == BASE_CONTENT + "第二次保存。\n"


async def _assert_blocked_by_agent_write_lock(file_id: str, request_coro_factory):
    """
    在 agent 的条带写锁被持有期间发起用户侧写请求，断言它被挡住。

    返回请求最终的响应，供调用方继续断言。
    """
    from agent.tools.file_ops.edit import file_write_lock

    holding = threading.Event()
    release = threading.Event()

    def hold_lock():
        with file_write_lock(file_id):
            holding.set()
            release.wait(timeout=15)

    holder = threading.Thread(target=hold_lock, name="fake-agent-edit", daemon=True)
    holder.start()
    task = None
    try:
        assert holding.wait(timeout=5)

        task = asyncio.ensure_future(request_coro_factory())
        await asyncio.sleep(0.5)
        assert not task.done(), "用户侧写请求没有被 agent 写锁挡住 -> 两条写路径无互斥"
    finally:
        release.set()
        holder.join(timeout=5)

    return await asyncio.wait_for(task, timeout=10)


@pytest.mark.integration
@pytest.mark.skipif(
    database.is_postgres,
    reason="PG 上的互斥由行锁提供，进程内条带锁不参与",
)
async def test_put_participates_in_agent_file_write_lock(
    client: AsyncClient, db_session: Session
):
    """PUT 必须和 agent 的 edit_file 走同一把条带写锁，而不是各写各的。"""
    user, project, headers = await _setup_user(client, db_session, "r3g7_lock")
    file = _make_draft(db_session, project)
    file_id = file.id

    resp = await _assert_blocked_by_agent_write_lock(
        file_id,
        lambda: client.put(
            f"/api/v1/files/{file_id}",
            json={"content": BASE_CONTENT + "用户写的。\n", "skip_version": True},
            headers=headers,
        ),
    )
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    assert db_session.get(File, file_id).content == BASE_CONTENT + "用户写的。\n"


@pytest.mark.integration
@pytest.mark.skipif(
    database.is_postgres,
    reason="PG 上的互斥由行锁提供，进程内条带锁不参与",
)
async def test_rollback_participates_in_agent_file_write_lock(
    client: AsyncClient, db_session: Session
):
    """回滚是另一条用户侧「整篇覆盖正文」的写路径，同样必须参与串行化。"""
    user, project, headers = await _setup_user(client, db_session, "r3g7_rblock")
    file = _make_draft(db_session, project)
    file_id = file.id

    first = await client.post(
        f"/api/v1/files/{file_id}/versions",
        json={"content": BASE_CONTENT, "change_type": "edit"},
        headers=headers,
    )
    assert first.status_code == 200, first.text

    resp = await _assert_blocked_by_agent_write_lock(
        file_id,
        lambda: client.post(
            f"/api/v1/files/{file_id}/versions/1/rollback",
            headers=headers,
        ),
    )
    assert resp.status_code == 200, resp.text

    db_session.expire_all()
    assert db_session.get(File, file_id).content == BASE_CONTENT
