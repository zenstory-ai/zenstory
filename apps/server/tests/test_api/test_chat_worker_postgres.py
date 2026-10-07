"""Actual PostgreSQL Chat new-session worker and dedicated-Session contract."""

import json
import os
import threading
import traceback
from contextlib import contextmanager
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlmodel import Session, SQLModel, select

import api.chat as chat_api
import database
from database import get_session
from main import app
from models import ChatMessage, ChatSession, Project, User
from services.core.auth_service import create_access_token

pytestmark = pytest.mark.skipif(not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
                               reason="explicit owned PostgreSQL URL required")


@pytest.mark.asyncio
async def test_chat_new_session_uses_independent_worker_session(monkeypatch, record_property):
    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert url == os.environ["DATABASE_URL"] and database.is_postgres
    engine = create_engine(url, connect_args={"options": "-c timezone=UTC -c statement_timeout=15000"})
    tables = [User.__table__, Project.__table__, ChatSession.__table__, ChatMessage.__table__]
    loop_thread = threading.get_ident()
    operations = []
    request_lifecycle = []
    helper_lifecycle = []
    original_factory = chat_api.create_session
    ids = {name: uuid4().hex for name in ("user", "project", "chat")}

    def cursor(_conn, _cursor, statement, _params, _context, _many):
        frames = [f for f in traceback.extract_stack() if f.filename.endswith(("api/chat.py", "utils/permission.py"))]
        if frames:
            operations.append({"statement": statement, "thread": threading.get_ident(),
                               "frames": [f.name for f in frames]})

    def request_session():
        with Session(engine) as session:
            lifecycle = {"id": id(session), "cold": not session.identity_map,
                         "expire_on_commit": session.expire_on_commit, "closed": False}
            request_lifecycle.append(lifecycle)
            try:
                yield session
            finally:
                session.close()
                lifecycle["closed"] = True

    @contextmanager
    def observed_real_helper_session():
        with original_factory() as session:
            lifecycle = {"id": id(session), "cold": not session.identity_map,
                         "expire_on_commit": session.expire_on_commit, "closed": False,
                         "same_database": str(session.get_bind().url) == str(database.sync_engine.url)}
            helper_lifecycle.append(lifecycle)
            try:
                yield session
            finally:
                session.close()
                lifecycle["closed"] = True

    try:
        SQLModel.metadata.create_all(engine, tables=tables)
        with engine.begin() as connection:
            # Exact index contract of 20260307_230000 migration, no production schema change.
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_session_user_project_active "
                                    "ON chat_session (user_id, project_id) WHERE is_active = true"))
        with Session(engine) as seed:
            seed.add(User(id=ids["user"], username="pgchat-" + ids["user"],
                          email=ids["user"] + "@example.test", hashed_password="unused-local-hash",
                          is_active=True, email_verified=True))
            seed.flush()
            seed.add(Project(id=ids["project"], owner_id=ids["user"], name="Owned PG chat"))
            seed.flush()
            seed.add(ChatSession(id=ids["chat"], user_id=ids["user"], project_id=ids["project"],
                                 title="Previous conversation", is_active=True, message_count=0))
            seed.commit()
        event.listen(engine, "before_cursor_execute", cursor)
        event.listen(database.sync_engine, "before_cursor_execute", cursor)
        try:
            with monkeypatch.context() as overrides:
                overrides.setitem(app.dependency_overrides, get_session, request_session)
                # Observe only Session lifecycle around the original factory; ORM/factory/SQL stay real.
                overrides.setattr(chat_api, "create_session", observed_real_helper_session)
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post(f"/api/v1/chat/session/{ids['project']}/new",
                        headers={"Authorization": "Bearer " + create_access_token({"sub": ids["user"]})})
        finally:
            event.remove(engine, "before_cursor_execute", cursor)
            event.remove(database.sync_engine, "before_cursor_execute", cursor)
        facts = {"status": response.status_code, "body": response.json(), "sql": operations,
                 "loop_thread": loop_thread, "request": request_lifecycle, "helper": helper_lifecycle}
        record_property("chat_pg_worker", json.dumps(facts, sort_keys=True))
        assert response.status_code == 200, facts
        body = response.json()
        with Session(engine) as reader:
            old = reader.get(ChatSession, ids["chat"])
            new = reader.get(ChatSession, body["id"])
            assert not old.is_active and new.is_active and new.message_count == 0
            assert new.user_id == ids["user"] and new.project_id == ids["project"]
            assert len(reader.exec(select(ChatSession).where(ChatSession.project_id == ids["project"])).all()) == 2
        assert len(request_lifecycle) == len(helper_lifecycle) == 1
        assert request_lifecycle[0]["id"] != helper_lifecycle[0]["id"]
        assert all(row["cold"] and row["expire_on_commit"] and row["closed"]
                   for row in request_lifecycle + helper_lifecycle)
        assert helper_lifecycle[0]["same_database"]
        assert any("_create_new_session_sync" in row["frames"] for row in operations)
        assert any("verify_project_access" in row["frames"] or "verify_project_access_sync" in row["frames"]
                   for row in operations)
        assert all(row["thread"] != loop_thread for row in operations), facts
    finally:
        SQLModel.metadata.drop_all(engine, tables=tables)
        engine.dispose()
        record_property("owned_cleanup", "both real Sessions closed; both listeners/override/factory restored; owned tables removed; engine disposed")
