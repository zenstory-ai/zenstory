"""第三轮 deep review 回归测试：工具核心（mcp_tools）。

覆盖缺陷：
- rank 1  工作线程不得复用 / 关闭调用方的 Session
- rank 14 空内容建档的待写标记 TOCTOU（孤儿空文件）
- rank 10 应用侧：recursive / continue_on_error 的字符串布尔
- rank 6/28 应用侧：offload 判据与数据库类型解耦
- rank 29 应用侧：pending-empty-file 守卫不得永久硬拒后续建档
"""

import asyncio
import json
import threading
import time
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from agent.tools import mcp_tools
from agent.tools.mcp_tools import ToolContext
from models import File, Project, User

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- helpers
class _FakeSession:
    """只记录"是谁、有没有被关掉"的假 Session。"""

    def __init__(self, index: int) -> None:
        self.index = index
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _ok_mcp_result(payload: dict | None = None) -> dict:
    return {
        "content": [
            {"type": "text", "text": json.dumps(payload or {"status": "success"})}
        ]
    }


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _make_user_project(db_session: Session, tag: str) -> tuple[User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"r3-{tag}-{suffix}@example.com",
        username=f"r3_{tag}_{suffix}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(name=f"R3 {tag} {suffix}", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


@pytest.mark.asyncio
async def test_offloaded_tool_does_not_close_caller_owned_session(monkeypatch):
    """rank 1（同一不变量的另一入口）：asyncio.to_thread 的线程副本不得复用/关闭调用方的 Session。

    缺陷版里工作线程继承了事件循环侧懒建的 Session，用完在 finally 里 close()，
    而事件循环那边的 ContextVar 仍指向它 —— 下一次工具调用拿到的是已关闭的 Session。
    """
    created: list[_FakeSession] = []

    def factory() -> _FakeSession:
        session = _FakeSession(len(created))
        created.append(session)
        return session

    ToolContext.set_context(
        session=None,
        user_id="u1",
        project_id="p1",
        session_id="s1",
        create_session_func=factory,
    )
    try:
        caller_session = ToolContext.get_session()

        thread_sessions: list[_FakeSession] = []

        def fake_edit_file_sync(args):
            thread_sessions.append(ToolContext.get_session())
            return _ok_mcp_result()

        monkeypatch.setattr(mcp_tools, "_edit_file_sync", fake_edit_file_sync)

        await mcp_tools.edit_file({"id": "file-1", "edits": []})

        assert thread_sessions[0] is not caller_session
        assert caller_session.closed is False
        assert thread_sessions[0].closed is True
        # 事件循环侧再取，仍是那个没被关掉的 Session
        assert ToolContext.get_session() is caller_session
    finally:
        ToolContext.clear_context()


@pytest.mark.asyncio
async def test_concurrent_empty_create_file_is_atomic(db_session, monkeypatch):
    """rank 14：并发的两次空文件 create_file 只能成功一个（检查+置位必须原子）。

    缺陷版里"查 pending"与"置 pending"之间隔着 DB INSERT（还跨线程边界），
    两个调用同时通过检查，各自建出空文件，后置位的把先置位的覆盖掉 ——
    先建的那个空文件从此没有任何指针。
    """
    from agent.tools.file_ops.executor import FileToolExecutor

    user, project = _make_user_project(db_session, "atomic")

    original_create = FileToolExecutor.create_file

    def slow_create_file(self, *args, **kwargs):
        time.sleep(0.05)  # 放大 TOCTOU 窗口
        return original_create(self, *args, **kwargs)

    monkeypatch.setattr(FileToolExecutor, "create_file", slow_create_file)

    from tests.conftest import TestSessionLocal

    ToolContext.set_context(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-atomic",
        create_session_func=TestSessionLocal,
    )
    try:
        # create_file 在 await 处让出事件循环，两次调用的"检查"与"置位"因此真正交错。
        results = await asyncio.gather(
            mcp_tools.create_file(
                {"title": "第一章", "file_type": "draft", "content": ""}
            ),
            mcp_tools.create_file(
                {"title": "第二章", "file_type": "draft", "content": ""}
            ),
        )
        statuses = sorted(_payload(r)["status"] for r in results)
        assert statuses == ["error", "success"]

        db_session.expire_all()
        drafts = db_session.exec(
            select(File).where(File.project_id == project.id, File.file_type == "draft")
        ).all()
        # 只允许落一个空文件；两个空文件里必然有一个成为孤儿
        assert len(drafts) == 1

        pending = ToolContext.get_pending_empty_files()
        assert len(pending) == 1
        assert pending[0]["file_id"] == drafts[0].id
    finally:
        ToolContext.clear_context()


def test_pending_empty_file_marker_is_a_set_not_a_slot():
    """rank 14：标记必须是按 file_id 的集合，后写者不得静默覆盖先写者。"""
    ToolContext.set_context(None, "u1", "p1", "s1")
    try:
        ToolContext.set_pending_empty_file("file-1", "第一章")
        ToolContext.set_pending_empty_file("file-2", "第二章")

        assert ToolContext.get_pending_empty_files() == [
            {"file_id": "file-1", "title": "第一章"},
            {"file_id": "file-2", "title": "第二章"},
        ]
        # 单槽语义保持兼容：get_pending_empty_file 返回最近一个
        assert ToolContext.get_pending_empty_file() == {
            "file_id": "file-2",
            "title": "第二章",
        }

        # 精确清除：收尾 file-2 不能把 file-1 的补写信号一起抹掉
        ToolContext.clear_pending_empty_file("file-2")
        assert ToolContext.get_pending_empty_files() == [
            {"file_id": "file-1", "title": "第一章"}
        ]

        ToolContext.clear_pending_empty_file()
        assert ToolContext.get_pending_empty_files() == []
        assert ToolContext.has_pending_empty_file() is False
    finally:
        ToolContext.clear_context()


# --------------------------------------------------------------------------- rank 29
@pytest.mark.asyncio
async def test_pending_guard_does_not_forget_unfinished_empty_file(db_session):
    """Repeated invalid creates must not erase evidence of an unfinished file."""
    user, project = _make_user_project(db_session, "selfheal")

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-selfheal",
    )
    try:
        statuses = []
        for index in range(4):
            result = await mcp_tools.create_file(
                {"title": f"第{index + 1}章", "file_type": "draft", "content": ""}
            )
            statuses.append(_payload(result)["status"])

        assert statuses == ["success", "error", "error", "error"]

        pending = ToolContext.get_pending_empty_files()
        assert [item["title"] for item in pending] == ["第1章"]
    finally:
        ToolContext.clear_pending_empty_file()
        ToolContext.clear_context()


def test_correction_exhaustion_rolls_back_verified_empty_artifact(db_session):
    """A verified empty artifact is soft-deleted before its guard is cleared."""
    from agent.core.empty_files import rollback_unfinished_empty_files

    user, project = _make_user_project(db_session, "empty-rollback")
    file = File(
        project_id=project.id,
        title="未完成章节",
        file_type="draft",
        content="",
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-empty-rollback",
    )
    try:
        rolled_back = rollback_unfinished_empty_files(
            [{"file_id": file.id, "title": file.title, "updated_at": file.updated_at.isoformat()}]
        )
        assert rolled_back == {file.id}
        db_session.expire_all()
        persisted = db_session.get(File, file.id)
        assert persisted is not None
        assert persisted.is_deleted is True
        assert persisted.deleted_at is not None
    finally:
        ToolContext.clear_context()


@pytest.mark.asyncio
async def test_delete_file_tool_string_false_does_not_cascade(db_session):
    """rank 10：单体 delete_file 工具入口传 recursive="false" 不得级联删除子树。"""
    user, project = _make_user_project(db_session, "recursive")

    folder = File(project_id=project.id, title="旧草稿", file_type="folder")
    db_session.add(folder)
    db_session.commit()
    db_session.refresh(folder)

    child_ids = []
    for index in range(3):
        child = File(
            project_id=project.id,
            title=f"第{index + 1}章",
            file_type="draft",
            parent_id=folder.id,
            content="正文",
        )
        db_session.add(child)
        db_session.commit()
        db_session.refresh(child)
        child_ids.append(child.id)

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-recursive",
    )
    try:
        result = await mcp_tools.delete_file({"id": folder.id, "recursive": "false"})
        assert _payload(result)["status"] == "success"

        db_session.expire_all()
        assert db_session.get(File, folder.id).is_deleted is True
        assert [bool(db_session.get(File, cid).is_deleted) for cid in child_ids] == [
            False,
            False,
            False,
        ]
    finally:
        ToolContext.clear_context()


@pytest.mark.asyncio
async def test_edit_file_tool_string_false_stops_on_first_failure(db_session):
    """rank 10：单体 edit_file 工具入口传 continue_on_error="false" 必须"失败即停"。"""
    user, project = _make_user_project(db_session, "coe")

    target = File(
        project_id=project.id,
        title="待编辑",
        file_type="draft",
        content="AAA\nBBB\nCCC",
    )
    db_session.add(target)
    db_session.commit()
    db_session.refresh(target)

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-coe",
    )
    try:
        result = await mcp_tools.edit_file({
            "id": target.id,
            "edits": [
                {"op": "replace", "old": "ZZZ_NOT_PRESENT", "new": "X"},
                {"op": "replace", "old": "CCC", "new": "CCC_EDITED"},
            ],
            "continue_on_error": "false",
        })

        db_session.expire_all()
        content = db_session.get(File, target.id).content
        # 第一条编辑失败即中止：第二条不得生效
        assert "CCC_EDITED" not in (content or "")
        assert _payload(result)["status"] == "error"
    finally:
        ToolContext.clear_context()


# --------------------------------------------------------------------------- rank 6 / 28 应用侧
@pytest.mark.asyncio
async def test_write_tools_run_off_event_loop_thread_on_sqlite(db_session, monkeypatch):
    """rank 28：SQLite 模式下写工具的同步实现不得跑在事件循环线程上。"""
    import database

    monkeypatch.setattr(database, "is_postgres", False, raising=False)

    loop_thread = threading.current_thread().name
    observed: list[str] = []

    def fake_edit_file_sync(args):
        observed.append(threading.current_thread().name)
        return _ok_mcp_result()

    monkeypatch.setattr(mcp_tools, "_edit_file_sync", fake_edit_file_sync)

    ToolContext.set_context(None, "u1", "p1", "s1")
    try:
        await mcp_tools.edit_file({"id": "file-1", "edits": []})
        assert observed and observed[0] != loop_thread
    finally:
        ToolContext.clear_context()
