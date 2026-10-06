"""Real Chat HTTP/SQL placement with cold, default-expiring owned Sessions."""

import json
import threading
import traceback
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel, select

from database import get_session
from main import app
from models import ChatMessage, ChatSession, Project, User
from services.core.auth_service import create_access_token


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["session", "messages", "recent", "clear", "new", "feedback"])
async def test_chat_orm_uses_worker(tmp_path, monkeypatch, record_property, operation):
    await _exercise(tmp_path, monkeypatch, record_property, operation)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["session", "messages", "recent", "clear", "new", "feedback"])
async def test_chat_foreign_project_unchanged(tmp_path, monkeypatch, record_property, operation):
    await _exercise(tmp_path, monkeypatch, record_property, operation, foreign=True)


async def _exercise(tmp_path, monkeypatch, record_property, operation, *, foreign=False):
    path = tmp_path / "chat-worker.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    loop_thread = threading.get_ident()
    operations = []
    lifecycles = []
    user_id, project_id, chat_id, message_id = [uuid4().hex for _ in range(4)]

    def cursor(_conn, _cursor, statement, _params, _context, _many):
        frames = [f for f in traceback.extract_stack() if f.filename.endswith((
            "api/chat.py", "services/chat_feedback_service.py", "utils/permission.py"))]
        if frames:
            operations.append({"statement": statement, "thread": threading.get_ident(),
                               "frames": [{"file": f.filename, "name": f.name,
                                           "line": f.lineno} for f in frames]})

    def request_session():
        session = Session(engine)
        lifecycle = {"cold": len(session.identity_map) == 0,
                     "expire_on_commit": session.expire_on_commit, "closed": False}
        lifecycles.append(lifecycle)
        try:
            yield session
        finally:
            session.close()
            lifecycle["closed"] = True

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            seed.add(User(id=user_id, username="chat-" + user_id,
                          email=user_id + "@example.test", hashed_password="unused-local-hash",
                          is_active=True, email_verified=True))
            owner_id = user_id
            if foreign:
                owner_id = uuid4().hex
                seed.add(User(id=owner_id, username="owner-" + owner_id,
                              email=owner_id + "@example.test", hashed_password="unused-local-hash",
                              is_active=True, email_verified=True))
            seed.add(Project(id=project_id, owner_id=owner_id, name="Chat worker fixture"))
            seed.add(ChatSession(id=chat_id, user_id=owner_id, project_id=project_id,
                                 title="Existing conversation", message_count=1, is_active=True))
            seed.add(ChatMessage(id=message_id, session_id=chat_id, role="assistant",
                                 content="Retained exact body", message_metadata='{"existing":"keep"}'))
            seed.commit()
            before = {"session": seed.get(ChatSession, chat_id).model_dump(mode="json"),
                      "message": seed.get(ChatMessage, message_id).model_dump(mode="json")}
        event.listen(engine, "before_cursor_execute", cursor)
        try:
            with monkeypatch.context() as overrides:
                overrides.setitem(app.dependency_overrides, get_session, request_session)
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    method = {"clear": "DELETE", "new": "POST", "feedback": "POST"}.get(operation, "GET")
                    url = f"/api/v1/chat/session/{project_id}"
                    if operation in ("messages", "recent", "new"):
                        url += "/" + operation
                    payload = {}
                    if operation == "feedback":
                        url = f"/api/v1/chat/messages/{message_id}/feedback"
                        payload["json"] = {"vote": "up", "preset": "  useful  ", "comment": "  retained  "}
                    response = await client.request(method, url, **payload, headers={
                        "Authorization": "Bearer " + create_access_token({"sub": user_id}),
                    })
        finally:
            event.remove(engine, "before_cursor_execute", cursor)
        facts = {"operation": operation, "foreign": foreign, "loop_thread": loop_thread,
                 "sql": operations, "sessions": lifecycles, "status": response.status_code,
                 "body": response.json()}
        record_property("chat_worker", json.dumps(facts, ensure_ascii=False, sort_keys=True))
        assert lifecycles and all(row == {"cold": True, "expire_on_commit": True,
                                          "closed": True} for row in lifecycles)
        with Session(engine) as reader:
            saved_session = reader.get(ChatSession, chat_id)
            saved_message = reader.get(ChatMessage, message_id)
            if foreign:
                assert response.status_code == 403
                assert saved_session.model_dump(mode="json") == before["session"]
                assert saved_message.model_dump(mode="json") == before["message"]
            else:
                assert response.status_code == 200, facts
                body = response.json()
                if operation == "session":
                    assert body == before["session"]
                elif operation in ("messages", "recent"):
                    assert body == [{"id": message_id, "session_id": chat_id, "role": "assistant",
                                     "content": "Retained exact body", "tool_calls": None,
                                     "metadata": '{"existing":"keep"}',
                                     "created_at": before["message"]["created_at"]}]
                elif operation == "clear":
                    assert body == {"success": True, "message": "Cleared 1 messages"}
                    assert saved_message is None and saved_session.message_count == 0
                elif operation == "new":
                    assert body["id"] != chat_id and body["is_active"] and body["message_count"] == 0
                    assert body["project_id"] == project_id and body["user_id"] == user_id
                    assert not saved_session.is_active
                    assert len(reader.exec(select(ChatSession)).all()) == 2
                    assert saved_message.model_dump(mode="json") == before["message"]
                else:
                    assert body["message_id"] == message_id
                    assert body["feedback"]["vote"] == "up"
                    assert body["feedback"]["preset"] == "useful"
                    assert body["feedback"]["comment"] == "retained"
                    metadata = json.loads(saved_message.message_metadata)
                    assert metadata["existing"] == "keep"
                    assert metadata["feedback"]["vote"] == "up"
        assert operations, facts
        assert all(row["thread"] != loop_thread for row in operations), facts
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
        record_property("owned_cleanup", "listener/override restored; engine disposed; SQLite absent")
