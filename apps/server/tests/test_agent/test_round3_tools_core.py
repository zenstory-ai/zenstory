"""第三轮 deep review 回归测试：工具核心（mcp_tools + parallel_executor）。

覆盖缺陷：
- rank 1  parallel_execute 子任务通过 _owned_session_var 共用父上下文的 Session
- rank 14 write_chapter 空内容任务的待写标记 TOCTOU（孤儿空文件）
- rank 32 parallel_execute 静默丢弃第 6 个及以后的任务却报 all_completed=true
- rank 10 应用侧：recursive / continue_on_error 的字符串布尔
- rank 2  应用侧：refresh_file_inventory 的类型桶漏掉 script / document
- rank 6/28 应用侧：offload 判据与数据库类型解耦
- rank 29 应用侧：pending-empty-file 守卫不得永久硬拒后续建档
"""

import asyncio
import json
import threading
import time
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from agent.tools import mcp_tools
from agent.tools.mcp_tools import ToolContext
from agent.tools.parallel_executor import (
    MAX_PARALLEL_TASKS,
    execute_parallel,
    handle_delete_file,
    handle_edit_file,
)
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


# --------------------------------------------------------------------------- rank 1
@pytest.mark.asyncio
async def test_parallel_tasks_never_share_parent_owned_session(monkeypatch):
    """rank 1：handoff 之后（父上下文已懒建 Session）再并行写，子任务必须各自建 Session。

    缺陷版里 execute_task 只把 task_ctx["session"] 置 None，而 get_session() 的
    第二顺位是 _owned_session_var —— 子任务通过 contextvars 快照原样继承父上下文
    已有的 Session，于是两个真实 OS 线程共用同一个 SQLAlchemy Session，先结束的
    那个还会在 finally 里把它 close 掉。
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
        # 等价于 handoff 后 writing_graph 调 refresh_file_inventory() 懒建的那次
        parent_session = ToolContext.get_session()
        assert parent_session is created[0]

        seen: list[tuple[int, str]] = []

        def fake_edit_file_sync(args):
            session = ToolContext.get_session()
            seen.append((id(session), threading.current_thread().name))
            time.sleep(0.08)  # 让两个任务的执行窗口真实重叠
            return _ok_mcp_result()

        monkeypatch.setattr(mcp_tools, "_edit_file_sync", fake_edit_file_sync)

        tasks = [
            {
                "type": "edit_file",
                "description": f"edit-{i}",
                "params": {"id": f"file-{i}", "edits": []},
            }
            for i in range(2)
        ]
        result = await execute_parallel(tasks)

        assert _payload(result)["data"]["all_completed"] is True
        # 核心断言：两个子任务拿到的是两个不同的 Session
        assert len({session_id for session_id, _ in seen}) == 2
        # 且确实跑在两个不同的线程上（证明并发窗口真实重叠）
        assert len({thread_name for _, thread_name in seen}) == 2
        # 父上下文的 Session 不能被子任务顺手关掉
        assert parent_session.closed is False
        # 子任务自建的 Session 必须各自关闭，不许泄漏连接
        assert [s.closed for s in created[1:]] == [True] * (len(created) - 1)
    finally:
        ToolContext.clear_context()


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


# --------------------------------------------------------------------------- rank 14
@pytest.mark.asyncio
async def test_parallel_write_chapter_requires_inline_content():
    """rank 14：并行 write_chapter 不带 content 必须直接失败，绝不落一个空文件。

    并行分支的 tool_result 工具名是 parallel_execute，StreamAdapter 只在
    create_file 的结果上进入 <file> 捕获，因此空文件永远等不到正文。
    """
    ToolContext.set_context(None, "u1", "p1", "s1")
    try:
        tasks = [
            {"type": "write_chapter", "description": "写第三章", "params": {"title": "第三章"}},
            {"type": "write_chapter", "description": "写第四章", "params": {"title": "第四章"}},
        ]
        data = _payload(await execute_parallel(tasks))["data"]

        assert data["failed"] == 2
        assert data["completed"] == 0
        assert data["all_completed"] is False
        assert data["any_failed"] is True
        for task in data["tasks"]:
            assert "content" in (task["error"] or "")
        # 没有任何空文件被创建，也就没有待补写的孤儿标记
        assert ToolContext.has_pending_empty_file() is False
    finally:
        ToolContext.clear_context()


@pytest.mark.asyncio
async def test_parallel_write_chapter_with_content_still_works():
    """rank 14 的对照：带 content 的并行 write_chapter 必须照常成功（不能误伤正常路径）。"""
    ToolContext.set_context(None, "u1", "p1", "s1")
    seen_args: list[dict] = []

    async def fake_create_file(args):
        seen_args.append(args)
        return _ok_mcp_result({"status": "success", "data": {"id": args["title"]}})

    try:
        with patch("agent.tools.mcp_tools.create_file", new=fake_create_file):
            tasks = [
                {
                    "type": "write_chapter",
                    "description": "写第三章",
                    "params": {"title": "第三章", "content": "第三章正文" * 20},
                },
                {
                    "type": "write_chapter",
                    "description": "写第四章",
                    "params": {"title": "第四章", "content": "第四章正文" * 20},
                },
            ]
            data = _payload(await execute_parallel(tasks))["data"]

        assert data["all_completed"] is True
        assert data["failed"] == 0
        assert len(seen_args) == 2
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
        # 强制走 offload 分支（生产 PG 路径）：create_file 在 await 处让出事件循环，
        # 两次调用的"检查"与"置位"因此真正交错。
        with patch(
            "agent.tools.mcp_tools._should_offload_tool_execution", return_value=True
        ):
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
    from agent.graph.writing_graph import _rollback_unfinished_empty_files

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
        rolled_back = _rollback_unfinished_empty_files(
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


# --------------------------------------------------------------------------- rank 32
@pytest.mark.asyncio
async def test_parallel_execute_reports_dropped_tasks():
    """rank 32：超过上限的任务被丢弃时，返回给 LLM 的 payload 必须显式说明，且不得报全部完成。"""
    ToolContext.set_context(None, "u1", "p1", "s1")
    try:
        tasks = [
            {"type": "query_files", "description": f"查询 {i}", "params": {}}
            for i in range(MAX_PARALLEL_TASKS + 2)
        ]
        with patch("agent.tools.parallel_executor.handle_query_files") as mock_query:
            mock_query.return_value = _ok_mcp_result({"count": 0})
            data = _payload(await execute_parallel(tasks))["data"]

        assert data["requested_tasks"] == MAX_PARALLEL_TASKS + 2
        assert data["total_tasks"] == MAX_PARALLEL_TASKS
        assert data["truncated"] is True
        assert data["dropped"] == 2
        assert [item["index"] for item in data["dropped_tasks"]] == [
            MAX_PARALLEL_TASKS,
            MAX_PARALLEL_TASKS + 1,
        ]
        assert [item["description"] for item in data["dropped_tasks"]] == [
            f"查询 {MAX_PARALLEL_TASKS}",
            f"查询 {MAX_PARALLEL_TASKS + 1}",
        ]
        # 最关键：有任务没执行就不许报"全部完成"
        assert data["all_completed"] is False
        assert "warning" in data
    finally:
        ToolContext.clear_context()


@pytest.mark.asyncio
async def test_parallel_execute_not_truncated_keeps_all_completed():
    """rank 32 的对照：没有丢弃任务时 all_completed 仍为 True。"""
    ToolContext.set_context(None, "u1", "p1", "s1")
    try:
        tasks = [
            {"type": "query_files", "description": f"查询 {i}", "params": {}}
            for i in range(MAX_PARALLEL_TASKS)
        ]
        with patch("agent.tools.parallel_executor.handle_query_files") as mock_query:
            mock_query.return_value = _ok_mcp_result({"count": 0})
            data = _payload(await execute_parallel(tasks))["data"]

        assert data["truncated"] is False
        assert data["dropped"] == 0
        assert data["dropped_tasks"] == []
        assert data["all_completed"] is True
        assert "warning" not in data
    finally:
        ToolContext.clear_context()


# --------------------------------------------------------------------------- rank 10 应用侧
@pytest.mark.asyncio
@pytest.mark.parametrize("raw_value", ["false", "0", "", "off", "no", None])
async def test_parallel_delete_file_coerces_recursive(raw_value):
    """rank 10：并行 delete_file 的 recursive 必须走 coerce_bool（bool("false") is True）。"""
    seen: list[dict] = []

    async def fake_delete_file(args):
        seen.append(args)
        return _ok_mcp_result()

    params = {"id": "file-1"}
    if raw_value is not None:
        params["recursive"] = raw_value

    with patch("agent.tools.mcp_tools.delete_file", new=fake_delete_file):
        await handle_delete_file(params)

    assert seen[0]["recursive"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_value", ["true", "1", "yes", True])
async def test_parallel_delete_file_keeps_real_recursive(raw_value):
    """rank 10 的对照：真正的真值必须仍然被识别为 True。"""
    seen: list[dict] = []

    async def fake_delete_file(args):
        seen.append(args)
        return _ok_mcp_result()

    with patch("agent.tools.mcp_tools.delete_file", new=fake_delete_file):
        await handle_delete_file({"id": "file-1", "recursive": raw_value})

    assert seen[0]["recursive"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_value", ["false", "0", ""])
async def test_parallel_edit_file_coerces_continue_on_error(raw_value):
    """rank 10：continue_on_error 同构缺陷 —— 传 "false" 反而变成"失败继续"。"""
    seen: list[dict] = []

    async def fake_edit_file(args):
        seen.append(args)
        return _ok_mcp_result()

    with patch("agent.tools.mcp_tools.edit_file", new=fake_edit_file):
        await handle_edit_file(
            {"id": "file-1", "edits": [], "continue_on_error": raw_value}
        )

    assert seen[0]["continue_on_error"] is False


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


# --------------------------------------------------------------------------- rank 2 应用侧
def test_refresh_file_inventory_covers_all_entity_types(db_session):
    """rank 2：交接时的文件清单必须覆盖除 folder 外的全部类型（历史漏了 script / document）。"""
    from agent.constants import INVENTORY_FILE_TYPES

    user, project = _make_user_project(db_session, "inventory")

    for file_type in INVENTORY_FILE_TYPES:
        db_session.add(
            File(
                project_id=project.id,
                title=f"{file_type}-文件",
                file_type=file_type,
                content="x",
            )
        )
    db_session.add(
        File(project_id=project.id, title="容器", file_type="folder")
    )
    db_session.commit()

    ToolContext.set_context(
        session=db_session,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-inventory",
    )
    try:
        inventory = ToolContext.refresh_file_inventory()
        assert inventory is not None
        assert set(inventory.keys()) == set(INVENTORY_FILE_TYPES)
        for file_type in INVENTORY_FILE_TYPES:
            titles = [item["title"] for item in inventory[file_type]]
            assert titles == [f"{file_type}-文件"], f"{file_type} 被清单漏掉了"
        assert "folder" not in inventory
    finally:
        ToolContext.clear_context()


# --------------------------------------------------------------------------- rank 6 / 28 应用侧
def test_offload_decision_is_decoupled_from_database_type(monkeypatch):
    """rank 6/28：是否 offload 取决于"这段代码 CPU/IO 密集"，与数据库类型无关。

    绑定 is_postgres 会让 SQLite 部署（项目默认）把 difflib/近似匹配扫描和阻塞式
    threading.Lock 获取全都放在事件循环线程上，实测停顿 21.93 秒。
    """
    import database

    monkeypatch.setattr(database, "is_postgres", False, raising=False)
    assert mcp_tools._should_offload_tool_execution() is True

    monkeypatch.setattr(database, "is_postgres", True, raising=False)
    assert mcp_tools._should_offload_tool_execution() is True


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
