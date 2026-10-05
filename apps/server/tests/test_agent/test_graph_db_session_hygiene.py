"""工作流在 agent 边界的一次性查库：放进工作线程、用完即关 session。

回归：writing_graph 曾在事件循环上同步调用 refresh_file_inventory /
_probe_pending_file_body / _rollback_unfinished_empty_files，它们经
ToolContext.get_session() 懒建一个 session 塞进 _owned_session_var，查完既不
commit 也不 close，PostgreSQL 上这条连接以 idle in transaction 挂到整轮 SSE
结束。
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.tools.mcp_tools import ToolContext, _owned_session_var
from models import File, Project, User
from services.core.auth_service import hash_password


def _seed(db_session) -> tuple[User, Project, File]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"graph-hygiene-{suffix}@example.com",
        username=f"graph_hygiene_{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Hygiene", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    empty = File(project_id=project.id, title="空章节", file_type="draft", content="")
    db_session.add(empty)
    db_session.commit()
    db_session.refresh(empty)
    return user, project, empty


@pytest.fixture
def tracked_session_factory():
    from tests.conftest import TestSessionLocal

    created = []

    def factory():
        session = TestSessionLocal()
        created.append(session)
        return session

    return factory, created


def _set_context(user, project, factory):
    ToolContext.set_context(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id="sess-hygiene",
        create_session_func=factory,
    )


async def test_refresh_file_inventory_in_thread_closes_its_session(
    db_session, tracked_session_factory
):
    user, project, _ = _seed(db_session)
    factory, created = tracked_session_factory
    _set_context(user, project, factory)
    try:
        inventory = await asyncio.to_thread(ToolContext.refresh_file_inventory)

        assert inventory is not None
        assert [item["title"] for item in inventory["draft"]] == ["空章节"]
        assert len(created) == 1
        # 用完即关：没有遗留事务，也没有挂进请求上下文的自有 session。
        assert created[0].in_transaction() is False
        assert _owned_session_var.get() is None
    finally:
        ToolContext.clear_context()


async def test_probe_and_rollback_use_short_lived_sessions(db_session, tracked_session_factory):
    from agent.graph.writing_graph import (
        _PENDING_BODY_EMPTY,
        _probe_pending_file_body,
        _rollback_unfinished_empty_files,
    )

    user, project, empty = _seed(db_session)
    factory, created = tracked_session_factory
    _set_context(user, project, factory)
    try:
        state = await asyncio.to_thread(_probe_pending_file_body, empty.id)
        rolled_back = await asyncio.to_thread(
            _rollback_unfinished_empty_files, [{"file_id": empty.id, "title": empty.title}]
        )

        assert state == _PENDING_BODY_EMPTY
        assert rolled_back == {empty.id}
        assert len(created) == 2
        assert all(session.in_transaction() is False for session in created)
        assert _owned_session_var.get() is None
        db_session.expire_all()
        assert db_session.get(File, empty.id).is_deleted is True
    finally:
        ToolContext.clear_context()


def test_short_lived_session_reuses_shared_session_without_closing(db_session):
    ToolContext.set_context(session=db_session, user_id="u", project_id="p", session_id="s")
    try:
        with ToolContext.short_lived_session() as session:
            assert session is db_session
        # 共享 session 归请求所有，这里不能关掉它。
        assert db_session.is_active
    finally:
        ToolContext.clear_context()


async def test_workflow_refreshes_inventory_off_the_event_loop():
    from agent.graph.writing_graph import run_writing_workflow_streaming

    seen_threads: list[threading.Thread] = []

    def fake_refresh():
        seen_threads.append(threading.current_thread())
        return {"draft": []}

    calls: list[str] = []

    async def fake_agent(_state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if len(calls) == 1:
            yield StreamEvent(
                type=StreamEventType.HANDOFF,
                data={"target_agent": "quality_reviewer", "reason": "送审", "context": "请审查"},
            )
        else:
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "审查完毕。[TASK_COMPLETE]"})

    loop_thread = threading.current_thread()
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch(
                "agent.graph.writing_graph.router_node",
                AsyncMock(return_value={"current_agent": "writer", "workflow_agents": []}),
            ),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
            patch.object(ToolContext, "refresh_file_inventory", side_effect=fake_refresh),
        ):
            _ = [
                event
                async for event in run_writing_workflow_streaming(
                    state={"user_message": "写第五章", "messages": [], "system_prompt": ""},
                    thread_id="t",
                )
            ]
    finally:
        ToolContext.clear_context()

    assert calls == ["writer", "quality_reviewer"]
    assert seen_threads, "handoff 后应刷新文件清单"
    assert all(thread is not loop_thread for thread in seen_threads)
