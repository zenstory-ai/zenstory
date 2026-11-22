"""
Unit tests for strong-typed AgentRequestContext and per-tool session_scope isolation.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from agents import RunContextWrapper

from agent.core.request_context import AgentRequestContext, unwrap_request_context


class TestAgentRequestContextBasic:
    """Basic functionality tests for AgentRequestContext."""

    def test_typed_fields_and_unwrap(self):
        """验证强类型字段与 RunContextWrapper 解包"""
        req_ctx = AgentRequestContext(
            session=None,
            user_id="user-1",
            project_id="project-1",
            session_id="session-1",
        )
        assert req_ctx.project_id == "project-1"
        assert req_ctx.user_id == "user-1"
        assert req_ctx.session_id == "session-1"
        assert unwrap_request_context(req_ctx) is req_ctx
        assert unwrap_request_context(RunContextWrapper(req_ctx)) is req_ctx
        assert unwrap_request_context(None) is None

    def test_context_cleanup(self):
        """验证 clear() 正确释放请求态"""
        req_ctx = AgentRequestContext(
            session=None,
            user_id="user-1",
            project_id="project-1",
            session_id="session-1",
        )
        req_ctx.set_pending_empty_file("file-1", "test.md")
        req_ctx.record_tool_ui_payload("call-1", {"undo": {"before_version_number": 1}})

        assert req_ctx.project_id == "project-1"
        assert req_ctx.has_pending_empty_file() is True

        req_ctx.clear()

        assert req_ctx.get_pending_empty_file() is None
        assert req_ctx.has_pending_empty_file() is False
        assert req_ctx.pop_tool_ui_payload("call-1") is None


class TestAgentRequestContextPendingFile:
    """Tests for pending empty file tracking."""

    def test_set_pending_empty_file(self):
        """验证设置待写入空文件"""
        req_ctx = AgentRequestContext(session=None, user_id="user-1", project_id="project-1", session_id=None)

        assert req_ctx.has_pending_empty_file() is False

        req_ctx.set_pending_empty_file("file-123", "新章节.md")

        assert req_ctx.has_pending_empty_file() is True
        pending = req_ctx.get_pending_empty_file()
        assert pending is not None
        assert pending["file_id"] == "file-123"
        assert pending["title"] == "新章节.md"

    def test_clear_pending_empty_file(self):
        """验证清除待写入空文件"""
        req_ctx = AgentRequestContext(session=None, user_id="user-1", project_id="project-1", session_id=None)
        req_ctx.set_pending_empty_file("file-123", "test.md")

        assert req_ctx.has_pending_empty_file() is True

        req_ctx.clear_pending_empty_file()

        assert req_ctx.has_pending_empty_file() is False
        assert req_ctx.get_pending_empty_file() is None

    def test_distinct_contexts_do_not_share_pending_files(self):
        """验证两个独立请求的 AgentRequestContext 互不干扰"""
        ctx1 = AgentRequestContext(session=None, user_id="user-1", project_id="project-1", session_id=None)
        ctx1.set_pending_empty_file("file-123", "test.md")

        ctx2 = AgentRequestContext(session=None, user_id="user-2", project_id="project-2", session_id=None)

        assert ctx1.has_pending_empty_file() is True
        assert ctx2.has_pending_empty_file() is False


class TestAgentRequestContextCrossTask:
    """待写入空文件标记通过显式共享的 AgentRequestContext 实例跨 asyncio 任务可见。"""

    @pytest.mark.asyncio
    async def test_pending_flag_set_in_nested_task_visible_in_parent(self):
        """两层嵌套子任务（模拟 SDK run-loop task + tool task）里设置的标记，父上下文必须可见"""
        req_ctx = AgentRequestContext(session=None, user_id="user-1", project_id="project-1", session_id=None)
        try:

            async def tool_task():
                req_ctx.set_pending_empty_file("file-1", "第1章.md")

            async def run_loop_task():
                await asyncio.create_task(tool_task())

            await asyncio.create_task(run_loop_task())

            assert req_ctx.has_pending_empty_file() is True
            pending = req_ctx.get_pending_empty_file()
            assert pending == {"file_id": "file-1", "title": "第1章.md"}
        finally:
            req_ctx.clear()

    @pytest.mark.asyncio
    async def test_clear_in_parent_visible_in_later_child_task(self):
        """父上下文清除标记后，后续子任务（如下一次 create_file 工具调用）必须读到已清除"""
        req_ctx = AgentRequestContext(session=None, user_id="user-1", project_id="project-1", session_id=None)
        try:

            async def set_task():
                req_ctx.set_pending_empty_file("file-1", "第1章.md")

            await asyncio.create_task(set_task())
            assert req_ctx.has_pending_empty_file() is True

            req_ctx.clear_pending_empty_file()

            seen: list[bool] = []

            async def read_task():
                seen.append(req_ctx.has_pending_empty_file())

            await asyncio.create_task(read_task())
            assert seen == [False]
            assert req_ctx.has_pending_empty_file() is False
        finally:
            req_ctx.clear()

    @pytest.mark.asyncio
    async def test_pending_flag_set_in_gather_subtask_visible_to_siblings_and_parent(self):
        """asyncio.gather 子任务里设置的标记，兄弟任务与父上下文都必须可见"""
        req_ctx = AgentRequestContext(session=None, user_id="user-1", project_id="project-1", session_id=None)
        try:
            sibling_saw: list[bool] = []

            async def setter():
                req_ctx.set_pending_empty_file("file-a", "并行文件.md")

            async def reader():
                await asyncio.sleep(0.05)
                sibling_saw.append(req_ctx.has_pending_empty_file())

            await asyncio.gather(setter(), reader())

            assert sibling_saw == [True]
            assert req_ctx.has_pending_empty_file() is True
        finally:
            req_ctx.clear()


class TestAgentRequestContextSessionScope:
    """Tests for per-tool short-lived DB session scoping."""

    def test_session_scope_reuses_injected_session_without_closing(self):
        """验证测试注入的 session 不被关闭"""
        mock_session = MagicMock()
        req_ctx = AgentRequestContext(
            session=mock_session,
            user_id="user-1",
            project_id="project-1",
            session_id="session-1",
        )

        with req_ctx.session_scope() as session:
            assert session is mock_session

        mock_session.close.assert_not_called()

    def test_session_scope_creates_and_closes_fresh_session_from_factory(self):
        """验证工厂模式每次打开并关闭独立短生命周期 session"""
        s1, s2 = MagicMock(), MagicMock()
        create_func = MagicMock(side_effect=[s1, s2])

        req_ctx = AgentRequestContext(
            session=None,
            user_id="user-1",
            project_id="project-1",
            session_id="session-1",
            create_session_func=create_func,
        )

        with req_ctx.session_scope() as session:
            assert session is s1
        s1.close.assert_called_once()

        with req_ctx.session_scope() as session:
            assert session is s2
        s2.close.assert_called_once()
        assert create_func.call_count == 2

    def test_session_scope_raises_if_unavailable(self):
        """验证既无 session 也无 create_session_func 时抛出异常"""
        req_ctx = AgentRequestContext(
            session=None,
            user_id="user-1",
            project_id="project-1",
            session_id="session-1",
            create_session_func=None,
        )

        with pytest.raises(RuntimeError, match="No session available"), req_ctx.session_scope():
            pass
