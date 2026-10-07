"""One real registered SSO route: SQL placement, DTO and no-write contract."""

import json
import threading
import traceback
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event
from sqlmodel import Session

from database import get_session
from main import app
from models import User
from services.core.auth_service import create_access_token


@pytest.mark.asyncio
async def test_sso_validate_user_sql_uses_worker(tmp_path, monkeypatch, record_property):
    path = tmp_path / "sso-validate-worker.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    operations = []
    sessions = []
    loop_thread = threading.get_ident()
    user_id = uuid4().hex

    def cursor(_conn, _cursor, statement, _params, _context, _many):
        frames = [f for f in traceback.extract_stack()
                  if f.filename.endswith("api/oauth.py") and f.name == "validate_token"]
        operations.append({"statement": statement, "thread": threading.get_ident(),
                           "handler": bool(frames), "lines": [f.lineno for f in frames]})

    def request_session():
        session = Session(engine)
        lifecycle = {"closed": False, "expire_on_commit": session.expire_on_commit,
                     "identity_size": len(session.identity_map)}
        sessions.append(lifecycle)
        try:
            yield session
        finally:
            session.close()
            lifecycle["closed"] = True

    try:
        User.__table__.create(engine)
        with Session(engine) as seed:
            user = User(id=user_id, username="owned-sso-" + user_id,
                        email=user_id + "@example.test", hashed_password="unused-local-hash",
                        is_active=True, email_verified=False)
            seed.add(user)
            seed.commit()
            seed.refresh(user)
            before = user.model_dump(mode="json")
        event.listen(engine, "before_cursor_execute", cursor)
        try:
            with monkeypatch.context() as overrides:
                overrides.setitem(app.dependency_overrides, get_session, request_session)
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.get("/api/auth/validate-token", params={
                        "token": create_access_token({"sub": user_id}),
                    })
        finally:
            event.remove(engine, "before_cursor_execute", cursor)
        with Session(engine) as reader:
            assert reader.get(User, user_id).model_dump(mode="json") == before
        facts = {"loop_thread": loop_thread, "operations": operations, "sessions": sessions,
                 "status": response.status_code, "body": response.json()}
        record_property("sso_validate_worker", json.dumps(facts, sort_keys=True))
        assert response.status_code == 200
        assert response.json() == {field: before[field] for field in ("id", "username", "email")}
        assert sessions == [{"closed": True, "expire_on_commit": True, "identity_size": 0}]
        assert len(operations) == 1 and operations[0]["handler"]
        assert operations[0]["statement"].lstrip().upper().startswith("SELECT")
        assert operations[0]["thread"] != loop_thread, facts
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
        record_property("owned_cleanup", "listener/override restored; engine disposed; SQLite absent")
