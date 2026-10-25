"""
Regression tests for AgentService session-id resolution.

These cover the hotfix that ensures the session_id used by ToolContext / artifact
ledger is always backed by a real `chat_session.id` row.
"""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlmodel import Session, SQLModel, select

from agent.service import AgentService
from config.datetime_utils import utcnow
from models import ChatSession, Project, User
from services.core.auth_service import hash_password

pytestmark = pytest.mark.usefixtures("writing_prompt_configs")


def _create_user_and_project(db_session: Session) -> tuple[User, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        email=f"agent-session-id-{suffix}@example.com",
        username=f"agent_session_id_{suffix}",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"Agent session id project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    return user, project


@pytest.mark.integration
class TestAgentServiceSessionIdResolution:
    def test_falls_back_to_existing_active_session_when_requested_missing(self, db_session: Session):
        user, project = _create_user_and_project(db_session)

        existing = ChatSession(
            id="existing-session",
            user_id=user.id,
            project_id=project.id,
            title="Existing",
            is_active=True,
            message_count=0,
        )
        db_session.add(existing)
        db_session.commit()

        service = AgentService(context_assembler=MagicMock())
        resolved = service._resolve_or_create_chat_session_id(
            db_session,
            project_id=project.id,
            user_id=user.id,
            requested_session_id="runtime-session-id",
        )

        assert resolved == existing.id
        assert db_session.get(ChatSession, "runtime-session-id") is None

    def test_creates_requested_session_id_when_no_active_session_exists(self, db_session: Session):
        user, project = _create_user_and_project(db_session)

        service = AgentService(context_assembler=MagicMock())
        resolved = service._resolve_or_create_chat_session_id(
            db_session,
            project_id=project.id,
            user_id=user.id,
            requested_session_id="runtime-session-id",
        )

        assert resolved == "runtime-session-id"
        created = db_session.get(ChatSession, "runtime-session-id")
        assert created is not None
        assert created.user_id == user.id
        assert created.project_id == project.id
        assert created.is_active is True

    def test_deactivates_stale_active_sessions_when_multiple_exist(self, db_session: Session):
        user, project = _create_user_and_project(db_session)

        old = ChatSession(
            id="old-session",
            user_id=user.id,
            project_id=project.id,
            title="Old",
            is_active=True,
            message_count=0,
            created_at=utcnow() - timedelta(minutes=10),
            updated_at=utcnow() - timedelta(minutes=10),
        )
        new = ChatSession(
            id="new-session",
            user_id=user.id,
            project_id=project.id,
            title="New",
            is_active=True,
            message_count=0,
            created_at=utcnow() - timedelta(minutes=1),
            updated_at=utcnow() - timedelta(minutes=1),
        )
        db_session.add_all([old, new])
        db_session.commit()

        service = AgentService(context_assembler=MagicMock())
        resolved = service._resolve_or_create_chat_session_id(
            db_session,
            project_id=project.id,
            user_id=user.id,
            requested_session_id=None,
        )

        assert resolved == new.id

        db_session.refresh(old)
        db_session.refresh(new)
        assert new.is_active is True
        assert old.is_active is False

    def test_ignores_requested_session_id_from_other_user_project(self, db_session: Session):
        user1, project1 = _create_user_and_project(db_session)
        user2, project2 = _create_user_and_project(db_session)

        foreign = ChatSession(
            id="shared-session",
            user_id=user2.id,
            project_id=project2.id,
            title="Foreign",
            is_active=True,
            message_count=0,
        )
        db_session.add(foreign)
        db_session.commit()

        service = AgentService(context_assembler=MagicMock())
        resolved = service._resolve_or_create_chat_session_id(
            db_session,
            project_id=project1.id,
            user_id=user1.id,
            requested_session_id="shared-session",
        )

        assert resolved != "shared-session"

        resolved_row = db_session.get(ChatSession, resolved)
        assert resolved_row is not None
        assert resolved_row.user_id == user1.id
        assert resolved_row.project_id == project1.id

        # Foreign session remains untouched.
        foreign_row = db_session.get(ChatSession, "shared-session")
        assert foreign_row is not None
        assert foreign_row.user_id == user2.id
        assert foreign_row.project_id == project2.id

    def test_reactivates_requested_session_under_partial_unique_index(self, tmp_path):
        """
        Postgres enforces uq_chat_session_user_project_active (partial unique on
        user_id+project_id WHERE is_active). Reactivating a candidate whose
        primary key sorts BEFORE the currently-active session must not trip the
        index: the stale deactivation has to reach the DB before the activation.
        """
        engine = create_engine(
            f"sqlite:///{tmp_path / 'partial_unique.db'}",
            connect_args={"check_same_thread": False},
        )
        SQLModel.metadata.create_all(engine)
        with engine.begin() as conn:
            # 与 database.py POSTGRES_PERFORMANCE_INDEX_SQL 中的定义保持一致
            conn.execute(
                text(
                    "CREATE UNIQUE INDEX uq_chat_session_user_project_active "
                    "ON chat_session (user_id, project_id) WHERE is_active = true"
                )
            )

        with Session(engine) as session:
            candidate = ChatSession(
                id="aaaa-candidate",
                user_id="user-partial-idx",
                project_id="proj-partial-idx",
                title="Old inactive",
                is_active=False,
                message_count=0,
                created_at=utcnow() - timedelta(minutes=10),
                updated_at=utcnow() - timedelta(minutes=10),
            )
            current = ChatSession(
                id="zzzz-current",
                user_id="user-partial-idx",
                project_id="proj-partial-idx",
                title="Current active",
                is_active=True,
                message_count=0,
            )
            session.add_all([candidate, current])
            session.commit()

            service = AgentService(context_assembler=MagicMock())
            resolved = service._resolve_or_create_chat_session_id(
                session,
                project_id="proj-partial-idx",
                user_id="user-partial-idx",
                requested_session_id="aaaa-candidate",
            )

            assert resolved == "aaaa-candidate"

            actives = session.exec(
                select(ChatSession).where(ChatSession.is_active)
            ).all()
            assert [s.id for s in actives] == ["aaaa-candidate"]


# ---------------------------------------------------------------------------
# SQLite 写锁回归：同一对话的下一轮请求不能让请求级 session 一直攥着写锁
# ---------------------------------------------------------------------------

# 测试里把 busy timeout 压到很短：修复前第二个连接的写入会在这段时间后
# 报 "database is locked"，而不是像生产配置那样干等 30 秒。
_SHORT_BUSY_TIMEOUT_MS = 200


def _file_sqlite_engine(db_path):
    """与 database.py 的 SQLite 配置一致（WAL + busy_timeout），仅超时更短。"""
    from sqlalchemy import event

    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={
            "check_same_thread": False,
            "timeout": _SHORT_BUSY_TIMEOUT_MS / 1000,
        },
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={_SHORT_BUSY_TIMEOUT_MS}")
        cursor.close()

    return engine


def _write_from_other_connection(db_path, session_id: str, title: str) -> None:
    """模拟工具在独立连接上写库（ToolContext 每个工作线程自建 session）。"""
    other_engine = _file_sqlite_engine(db_path)
    try:
        with other_engine.begin() as conn:
            conn.execute(
                text("UPDATE chat_session SET title = :title WHERE id = :id"),
                {"title": title, "id": session_id},
            )
    finally:
        other_engine.dispose()


@pytest.mark.integration
class TestResolveSessionIdReleasesSqliteWriteLock:
    @pytest.fixture
    def sqlite_file_db(self, tmp_path):
        db_path = tmp_path / "agent_session_lock.db"
        engine = _file_sqlite_engine(db_path)
        SQLModel.metadata.create_all(engine)
        yield engine, db_path
        engine.dispose()

    @staticmethod
    def _seed_active_session(engine, *, session_id: str, updated_at) -> None:
        with Session(engine) as seed:
            seed.add(
                ChatSession(
                    id=session_id,
                    user_id="user-lock",
                    project_id="proj-lock",
                    title="Active",
                    is_active=True,
                    message_count=2,
                    created_at=updated_at,
                    updated_at=updated_at,
                )
            )
            seed.commit()

    @pytest.mark.parametrize(
        "requested_session_id",
        [
            # 前端带着当前会话 id 继续下一轮（触发 bug 的主路径）
            "active-session",
            # 没带 id：取最新活跃会话
            None,
            # 旧客户端的运行时 UUID：回落到现有活跃会话
            "legacy-runtime-uuid",
        ],
    )
    def test_other_connection_can_write_after_resolving_existing_session(
        self, sqlite_file_db, requested_session_id
    ):
        engine, db_path = sqlite_file_db
        self._seed_active_session(
            engine,
            session_id="active-session",
            updated_at=utcnow() - timedelta(minutes=5),
        )

        # 应用里的请求级 session 是默认配置（autoflush=True）；conftest 的
        # db_session 关了 autoflush，覆盖不到这个问题，所以这里自建。
        with Session(engine) as request_session:
            service = AgentService(context_assembler=MagicMock())
            resolved = service._resolve_or_create_chat_session_id(
                request_session,
                project_id="proj-lock",
                user_id="user-lock",
                requested_session_id=requested_session_id,
            )
            assert resolved == "active-session"

            # process_stream 随后在同一个请求级 session 上做读查询
            # （SessionLoader / 技能目录）；autoflush 不能借机下发写语句。
            request_session.exec(select(ChatSession)).all()

            # 工作流运行期间工具在独立连接写库，必须拿得到写锁。
            _write_from_other_connection(db_path, "active-session", "tool wrote")
            assert not request_session.dirty

        with Session(engine) as verify:
            row = verify.get(ChatSession, "active-session")
            assert row is not None
            assert row.title == "tool wrote"

    def test_reusing_active_session_persists_updated_at_bump(self, sqlite_file_db):
        """updated_at 的刷新必须真正提交，而不是留在 session 里等别人顺手 flush。

        PostgreSQL 分支用 `with create_session()` 包住解析逻辑，未提交的修改在
        close 时被直接回滚——旧实现下这次刷新在生产上从未落库。
        """
        engine, _ = sqlite_file_db
        stale_updated_at = utcnow() - timedelta(minutes=5)
        self._seed_active_session(
            engine, session_id="active-session", updated_at=stale_updated_at
        )

        with Session(engine) as request_session:
            service = AgentService(context_assembler=MagicMock())
            service._resolve_or_create_chat_session_id(
                request_session,
                project_id="proj-lock",
                user_id="user-lock",
                requested_session_id="active-session",
            )
            # 模拟 PG 分支：不再碰这个 session，直接关闭。

        with Session(engine) as verify:
            row = verify.get(ChatSession, "active-session")
            assert row is not None
            # SQLite 读回的是 naive datetime，统一去掉时区再比较
            assert row.updated_at.replace(tzinfo=None) > stale_updated_at.replace(
                tzinfo=None
            )


@pytest.mark.integration
class TestProcessStreamReleasesSqliteWriteLock:
    """端到端：同一对话第二轮，工作流里工具在独立连接写库不能被请求级 session 锁住。"""

    @pytest.fixture
    def sqlite_file_db(self, tmp_path):
        db_path = tmp_path / "agent_stream_lock.db"
        engine = _file_sqlite_engine(db_path)
        SQLModel.metadata.create_all(engine)
        yield engine, db_path
        engine.dispose()

    @staticmethod
    def _service() -> AgentService:
        from agent.schemas.context import ContextData

        assembler = MagicMock()
        assembler.assemble.return_value = ContextData(items=[], context="", token_estimate=0)
        return AgentService(context_assembler=assembler)

    async def _run_second_turn(self, engine, db_path, service: AgentService):
        from unittest.mock import patch

        from agent.core.workflow_events import StreamEvent, StreamEventType

        with Session(engine) as seed:
            user, project = _create_user_and_project(seed)
            user_id, project_id = user.id, project.id
            seed.add(
                ChatSession(
                    id="second-turn-session",
                    user_id=user_id,
                    project_id=project_id,
                    title="Active",
                    is_active=True,
                    message_count=2,
                    created_at=utcnow() - timedelta(minutes=5),
                    updated_at=utcnow() - timedelta(minutes=5),
                )
            )
            seed.commit()

        tool_write_errors: list[BaseException] = []

        async def fake_workflow(*_args, **_kwargs):
            # 相当于工具（create_file / edit_file 等）在独立连接上写库
            try:
                _write_from_other_connection(db_path, "second-turn-session", "tool wrote")
            except Exception as exc:  # 记录下来由断言给出清晰的失败原因
                tool_write_errors.append(exc)
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "ok"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        events: list[str] = []
        with (
            Session(engine) as request_session,
            patch("agent.service.run_writing_workflow_streaming", side_effect=fake_workflow),
            patch("agent.service.create_session", side_effect=lambda: Session(engine)),
        ):
            async for event in service.process_stream(
                project_id=project_id,
                user_id=user_id,
                message="继续下一轮",
                session=request_session,
                session_id="second-turn-session",
            ):
                events.append(event)

        return events, tool_write_errors

    async def test_tool_write_succeeds_on_second_turn(self, sqlite_file_db):
        engine, db_path = sqlite_file_db

        events, tool_write_errors = await self._run_second_turn(
            engine, db_path, self._service()
        )

        assert tool_write_errors == []
        assert any("event: done" in event for event in events)
        with Session(engine) as verify:
            row = verify.get(ChatSession, "second-turn-session")
            assert row is not None
            assert row.title == "tool wrote"

    async def test_pre_workflow_flushed_write_is_committed_before_tools_run(self, sqlite_file_db):
        """兜底：工作流前的任何步骤在请求级 session 上留下已 flush 的写，也要先提交。"""
        from unittest.mock import patch

        from models import Project

        engine, db_path = sqlite_file_db
        service = self._service()
        original_prepare = service._prepare_prompt_artifacts

        def prepare_and_leave_flushed_write(session, **kwargs):
            result = original_prepare(session, **kwargs)
            project = session.get(Project, kwargs["project_id"])
            project.description = "touched before workflow"
            session.add(project)
            session.flush()  # 开启 SQLite 写事务，但不提交
            return result

        with patch.object(
            service, "_prepare_prompt_artifacts", side_effect=prepare_and_leave_flushed_write
        ):
            events, tool_write_errors = await self._run_second_turn(engine, db_path, service)

        assert tool_write_errors == []
        assert any("event: done" in event for event in events)


@pytest.mark.integration
class TestProcessStreamLoadsResolvedSession:
    """两个分页对同一项目的不同会话几乎同时发送：每一轮只读写自己解析出的会话。

    时序（确定性）：A 解析出会话 A 后停在领 generation slot 处；B 解析出会话 B
    （把 A 置为非活跃）并完整跑完；再放行 A。A 这一轮的历史、跨轮工作集和存档
    都必须属于会话 A，而不是此刻「最新的活跃会话」B。
    """

    @pytest.fixture
    def sqlite_file_db(self, tmp_path):
        db_path = tmp_path / "agent_two_tabs.db"
        engine = _file_sqlite_engine(db_path)
        SQLModel.metadata.create_all(engine)
        yield engine
        engine.dispose()

    async def test_interleaved_tabs_keep_their_own_session(self, sqlite_file_db):
        import asyncio
        import json
        from unittest.mock import patch

        from agent.core.workflow_events import StreamEvent, StreamEventType
        from agent.schemas.context import ContextData
        from models import ChatMessage, File

        engine = sqlite_file_db
        base = utcnow() - timedelta(hours=1)
        with Session(engine) as seed:
            user, project = _create_user_and_project(seed)
            user_id, project_id = user.id, project.id
            file_ids: dict[str, str] = {}
            for tab in ("A", "B"):
                file = File(
                    project_id=project_id,
                    title=f"{tab}卷纲",
                    content=f"{tab}卷纲正文",
                    file_type="outline",
                )
                seed.add(file)
                seed.commit()
                seed.refresh(file)
                file_ids[tab] = file.id
            for index, tab in enumerate(("A", "B")):
                seed.add(
                    ChatSession(
                        id=f"session-{tab}",
                        user_id=user_id,
                        project_id=project_id,
                        title=tab,
                        # 起始时 A 是活跃会话；B 是作者在另一个分页打开的旧会话
                        is_active=tab == "A",
                        message_count=2,
                        created_at=base + timedelta(minutes=index),
                        updated_at=base + timedelta(minutes=index),
                    )
                )
                seed.add(
                    ChatMessage(
                        session_id=f"session-{tab}",
                        role="user",
                        content=f"{tab}会话的旧问题",
                        created_at=base + timedelta(minutes=index),
                    )
                )
                seed.add(
                    ChatMessage(
                        session_id=f"session-{tab}",
                        role="assistant",
                        content=f"{tab}会话的旧回答",
                        tool_calls=json.dumps(
                            [
                                {
                                    "id": f"call_{tab}",
                                    "name": "query_files",
                                    "arguments": {"id": file_ids[tab]},
                                    "status": "success",
                                    "result": [{"id": file_ids[tab], "title": f"{tab}卷纲"}],
                                    "error": None,
                                }
                            ],
                            ensure_ascii=False,
                        ),
                        created_at=base + timedelta(minutes=index, seconds=1),
                    )
                )
            seed.commit()

        assembler = MagicMock()
        assembler.assemble.return_value = ContextData(items=[], context="", token_estimate=0)
        service = AgentService(context_assembler=assembler)

        seen_messages: dict[str, str] = {}

        async def fake_workflow(state, *_args, **_kwargs):
            seen_messages[state["session_id"]] = json.dumps(state["messages"], ensure_ascii=False)
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "好的"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        from agent import service as service_module

        real_claim = service_module.create_steering_queue_async
        a_resolved = asyncio.Event()
        release_a = asyncio.Event()

        async def claim_with_pause(session_id, *args, **kwargs):
            if session_id == "session-A":
                a_resolved.set()
                await release_a.wait()
            return await real_claim(session_id, *args, **kwargs)

        async def run_tab(tab: str) -> list[str]:
            events: list[str] = []
            with Session(engine) as request_session:
                async for event in service.process_stream(
                    project_id=project_id,
                    user_id=user_id,
                    message=f"{tab}分页的新消息",
                    session=request_session,
                    session_id=f"session-{tab}",
                ):
                    events.append(event)
            return events

        with (
            patch("agent.service.run_writing_workflow_streaming", side_effect=fake_workflow),
            patch("agent.service.create_steering_queue_async", side_effect=claim_with_pause),
            patch("agent.service.create_session", side_effect=lambda: Session(engine)),
        ):
            tab_a = asyncio.create_task(run_tab("A"))
            await asyncio.wait_for(a_resolved.wait(), timeout=5)
            events_b = await asyncio.wait_for(run_tab("B"), timeout=10)
            release_a.set()
            events_a = await asyncio.wait_for(tab_a, timeout=10)

        assert any("event: done" in event for event in events_a)
        assert any("event: done" in event for event in events_b)

        # 历史与跨轮工作集各自绑定自己的会话
        a_seen = seen_messages["session-A"]
        assert "A会话的旧回答" in a_seen
        assert f"《A卷纲》 (id={file_ids['A']})" in a_seen
        assert "B会话" not in a_seen
        assert file_ids["B"] not in a_seen
        b_seen = seen_messages["session-B"]
        assert "B会话的旧回答" in b_seen
        assert f"《B卷纲》 (id={file_ids['B']})" in b_seen
        assert "A会话" not in b_seen
        assert file_ids["A"] not in b_seen

        # 存档写回各自的会话
        with Session(engine) as verify:
            for tab, other in (("A", "B"), ("B", "A")):
                saved = [
                    row.content
                    for row in verify.exec(
                        select(ChatMessage).where(ChatMessage.session_id == f"session-{tab}")
                    ).all()
                ]
                assert f"{tab}分页的新消息" in saved
                assert f"{other}分页的新消息" not in saved
