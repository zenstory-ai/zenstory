"""Real partial-index races across every active-chat creator on an owned local PG DB."""

import asyncio
import os
from threading import Barrier, BrokenBarrierError
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlmodel import Session, SQLModel, select

import database
from agent.core.message_manager import MessageManager
from agent.service import AgentService
from database import get_session
from main import app
from models import ChatMessage, ChatSession, Project, User
from services.core.auth_service import create_access_token

pytestmark = pytest.mark.skipif(not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="explicit owned local PostgreSQL URL required")


@pytest.mark.asyncio
@pytest.mark.parametrize("left,right,existing", [
    ("new", "new", True), ("new", "new", False),
    ("get", "get", False), ("agent", "agent", False), ("message", "message", False),
    ("new", "get", False), ("new", "agent", False), ("new", "message", False), ("agent", "message", False),
    ("new", "requested", True), ("requested", "requested", False),
    ("new", "requested-new", False), ("new", "explicit-message", True),
])
async def test_all_active_chat_creators_serialize_without_losing_history(monkeypatch, left, right, existing):
    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert url == os.environ["DATABASE_URL"] and database.is_postgres
    engine = create_engine(url, connect_args={"options": "-c timezone=UTC -c statement_timeout=15000 -c lock_timeout=10000"})
    tables = [User.__table__, Project.__table__, ChatSession.__table__, ChatMessage.__table__]
    SQLModel.metadata.create_all(engine, tables=tables)
    with engine.begin() as connection:
        connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_session_user_project_active ON chat_session (user_id, project_id) WHERE is_active = true"))
    user_id, project_id = uuid4().hex, uuid4().hex
    requested_id, old_id = uuid4().hex, None
    with Session(engine) as seed:
        seed.add(User(id=user_id, username="race-" + user_id, email=user_id + "@example.test", hashed_password="synthetic", is_active=True, email_verified=True))
        seed.flush()
        seed.add(Project(id=project_id, owner_id=user_id, name="Owned concurrency proof"))
        seed.flush()
        if "requested" in (left, right):
            seed.add(ChatSession(id=requested_id, user_id=user_id, project_id=project_id, title="Requested history", is_active=False))
        if existing:
            old = ChatSession(user_id=user_id, project_id=project_id, title="Original", is_active=True)
            seed.add(old)
            seed.flush()
            old_id = old.id
            seed.add(ChatMessage(session_id=old.id, role="user", content="Original history must survive"))
        seed.commit()
    token = create_access_token({"sub": user_id})
    barrier = Barrier(2)

    def overlap_reads(_connection, _cursor, statement, _parameters, _context, _many):
        lowered = statement.lower()
        # The server has already returned each active-session read. In the old
        # implementation both callers can therefore observe the same absence.
        # With serialization the first reader times out here, commits, and the
        # second reads the new state. No mock replaces SQL or the DB transaction.
        if lowered.lstrip().startswith("select") and "from chat_session" in lowered and "is_active" in lowered.split("where", 1)[-1]:
            try:
                barrier.wait(timeout=0.5)
            except BrokenBarrierError:
                pass

    def request_session():
        with Session(engine) as session:
            yield session

    def internal_create(kind):
        with Session(engine) as session:
            if kind in {"agent", "requested", "requested-new"}:
                return AgentService(context_assembler=MagicMock())._resolve_or_create_chat_session_id(session, project_id=project_id, user_id=user_id, requested_session_id=requested_id if kind != "agent" else None)
            # Production resolves the target before persisting this round. Exercise
            # that complete path rather than the removed implicit-save creator.
            chat_id = old_id if kind == "explicit-message" else AgentService(
                context_assembler=MagicMock()
            )._resolve_or_create_chat_session_id(
                session, project_id=project_id, user_id=user_id, requested_session_id=None
            )
            return MessageManager(project_id=project_id, user_id=user_id)._save_messages_with_session(
                session, chat_id, "Owned user " + uuid4().hex, "Owned assistant"
            )

    async def invoke(client, kind):
        if kind in {"new", "get"}:
            endpoint = f"/api/v1/chat/session/{project_id}" + ("/new" if kind == "new" else "")
            response = await client.request("POST" if kind == "new" else "GET", endpoint, headers={"Authorization": "Bearer " + token})
            assert 200 <= response.status_code < 300, response.text
            return response.json()["id"]
        return await asyncio.to_thread(internal_create, kind)

    event.listen(engine, "after_cursor_execute", overlap_reads)
    event.listen(database.sync_engine, "after_cursor_execute", overlap_reads)
    try:
        with monkeypatch.context() as overrides:
            overrides.setitem(app.dependency_overrides, get_session, request_session)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                results = await asyncio.gather(invoke(client, left), invoke(client, right), return_exceptions=True)
        assert not any(isinstance(result, BaseException) for result in results), results
        assert all(results)
        with Session(engine) as reader:
            active = reader.exec(select(ChatSession).where(ChatSession.user_id == user_id, ChatSession.project_id == project_id, ChatSession.is_active)).all()
            assert len(active) == 1
            messages = reader.exec(select(ChatMessage).join(ChatSession).where(ChatSession.project_id == project_id)).all()
            if existing:
                assert any(message.content == "Original history must survive" for message in messages)
            for kind, result in zip((left, right), results, strict=True):
                if kind in {"message", "explicit-message"}:
                    saved = reader.get(ChatMessage, result)
                    assert saved and saved.content == "Owned assistant"
    finally:
        event.remove(engine, "after_cursor_execute", overlap_reads)
        event.remove(database.sync_engine, "after_cursor_execute", overlap_reads)
        engine.dispose()
