"""空文件的落库核验与回滚（agent/core/empty_files.py）：读数据库当前值、用完即关 session。

回归：这些一次性查库曾在事件循环上同步调用，经旧的全局上下文懒建的 session
查完既不 commit 也不 close，PostgreSQL 上这条连接以 idle in transaction 挂到整轮 SSE 结束。
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from sqlmodel import Session

from agent.core import empty_files
from agent.core.request_context import AgentRequestContext
from models import File, Project, User
from services.core.auth_service import hash_password

pytestmark = pytest.mark.asyncio


def _seed(db_session, *, content: str = "", is_deleted: bool = False) -> tuple[User, Project, File]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"empty-files-{suffix}@example.com",
        username=f"empty_files_{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Empty files", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    file = File(project_id=project.id, title="空章节", file_type="outline", content=content, is_deleted=is_deleted)
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)
    return user, project, file


@pytest.fixture
def tracked_session_factory():
    from tests.conftest import TestSessionLocal

    created = []

    def factory():
        session = TestSessionLocal()
        created.append(session)
        return session

    return factory, created


def _make_context(user, project, factory) -> AgentRequestContext:
    return AgentRequestContext(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-empty-files",
        create_session_func=factory,
    )


async def test_probe_and_rollback_use_short_lived_sessions(db_session, tracked_session_factory):
    user, project, empty = _seed(db_session)
    factory, created = tracked_session_factory
    req_ctx = _make_context(user, project, factory)
    try:
        state = await asyncio.to_thread(empty_files.probe_pending_file_body, empty.id, request_context=req_ctx)
        rolled_back = await asyncio.to_thread(
            empty_files.rollback_unfinished_empty_files,
            # 建好时的版本：只回滚之后没被动过的空产物
            [{"file_id": empty.id, "title": empty.title, "updated_at": empty.updated_at.isoformat()}],
            request_context=req_ctx,
        )

        assert state == empty_files.PENDING_BODY_EMPTY
        assert rolled_back == {empty.id}
        assert len(created) == 2
        assert all(session.in_transaction() is False for session in created)
        db_session.expire_all()
        assert db_session.get(File, empty.id).is_deleted is True
    finally:
        req_ctx.clear()


async def test_short_lived_session_reuses_shared_session_without_closing(db_session):
    req_ctx = AgentRequestContext(session=db_session, user_id="u", project_id="p", session_id="s")
    try:
        with req_ctx.session_scope() as session:
            assert session is db_session
        # 共享 session 归请求所有，这里不能关掉它。
        assert db_session.is_active
    finally:
        req_ctx.clear()


@pytest.mark.parametrize(
    ("content", "is_deleted", "expected"),
    [
        ("   \n  ", False, empty_files.PENDING_BODY_EMPTY),
        ("第一章正文" * 50, False, empty_files.PENDING_BODY_WRITTEN),
        ("", True, empty_files.PENDING_BODY_GONE),
    ],
)
async def test_probe_reads_the_stored_body(db_session, tracked_session_factory, content, is_deleted, expected):
    user, project, file = _seed(db_session, content=content, is_deleted=is_deleted)
    factory, _created = tracked_session_factory
    req_ctx = _make_context(user, project, factory)
    try:
        assert empty_files.probe_pending_file_body(file.id, request_context=req_ctx) == expected
        assert empty_files.probe_pending_file_body("file-not-in-db", request_context=req_ctx) == empty_files.PENDING_BODY_GONE
        assert empty_files.probe_pending_file_body("", request_context=req_ctx) == empty_files.PENDING_BODY_UNVERIFIABLE
    finally:
        req_ctx.clear()


async def test_probe_sees_a_body_written_in_another_session(db_session):
    """正文由工具自己的 session 写入时，核验读数据库最新值，不读身份映射里缓存的旧实例。"""
    user, project, file = _seed(db_session)
    assert db_session.get(File, file.id).content == ""
    with Session(db_session.get_bind()) as tool_session:
        target = tool_session.get(File, file.id)
        target.content = "第一章正文" * 50
        tool_session.add(target)
        tool_session.commit()

    req_ctx = AgentRequestContext(session=db_session, user_id=user.id, project_id=project.id, session_id="s")
    try:
        assert empty_files.probe_pending_file_body(file.id, request_context=req_ctx) == empty_files.PENDING_BODY_WRITTEN
    finally:
        req_ctx.clear()


async def test_probe_treats_a_file_of_another_project_as_gone(db_session, tracked_session_factory):
    user, project, _ = _seed(db_session)
    _other_user, _other_project, foreign = _seed(db_session)
    factory, _created = tracked_session_factory
    req_ctx = _make_context(user, project, factory)
    try:
        assert empty_files.probe_pending_file_body(foreign.id, request_context=req_ctx) == empty_files.PENDING_BODY_GONE
    finally:
        req_ctx.clear()
