"""HTTP proof of the shared access dependency's real User SELECT thread."""

from __future__ import annotations

import json
import re
import threading
import traceback
from datetime import datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlmodel import Session

from core.error_codes import ErrorCode
from database import get_session
from main import app
from models import User
from services.core.auth_service import create_access_token, get_current_user


@pytest.fixture
def auth_worker_probe(tmp_path, monkeypatch, record_property):
    """Own actors/storage while preserving the real request auth dependency."""
    database_path = tmp_path / "auth-dependency-worker.db"
    engine = create_engine(
        f"sqlite:///{database_path}", connect_args={"check_same_thread": False},
    )

    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", enable_foreign_keys)
    profiles = {}
    lifecycles = []
    try:
        User.__table__.create(engine)
        with Session(engine) as seed:
            for name, active in (("active", True), ("inactive", False)):
                suffix = uuid4().hex
                profile = {
                    "id": suffix,
                    "username": f"auth-worker-{suffix}",
                    "email": f"auth-worker-{suffix}@example.test",
                    "email_verified": True,
                    "avatar_url": None,
                    "is_active": active,
                    "is_superuser": False,
                    "created_at": "2026-10-06T12:00:00+00:00",
                    "updated_at": "2026-10-06T12:00:00+00:00",
                }
                profiles[name] = profile
                seed.add(User(
                    **{key: value for key, value in profile.items()
                       if key not in {"created_at", "updated_at"}},
                    hashed_password="unused-local-test-hash",
                    created_at=datetime(2026, 10, 6, 12),
                    updated_at=datetime(2026, 10, 6, 12),
                ))
            seed.commit()
            assert seed.exec(text("PRAGMA foreign_key_check")).all() == []

        def request_session():
            session = Session(engine)
            lifecycle = {
                "enter_thread": threading.get_ident(),
                "expire_on_commit": session.expire_on_commit,
                "initial_identity_count": len(session.identity_map),
                "closed": False,
            }
            lifecycles.append(lifecycle)
            try:
                yield session
            finally:
                session.close()
                lifecycle["closed"] = True
                lifecycle["exit_thread"] = threading.get_ident()

        assert get_current_user not in app.dependency_overrides
        with monkeypatch.context() as overrides:
            overrides.setitem(app.dependency_overrides, get_session, request_session)
            yield engine, profiles, lifecycles
        assert all(item["closed"] for item in lifecycles)
    finally:
        event.remove(engine, "connect", enable_foreign_keys)
        engine.dispose()
        database_path.unlink(missing_ok=True)
        assert not database_path.exists()
        record_property("sqlite_fixture_cleanup", "owned engine disposed/database removed")


async def _request(probe, case, record_property):
    engine, profiles, lifecycles = probe
    loop_thread = threading.get_ident()
    selects = []
    lifecycle_start = len(lifecycles)

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = statement.lower().replace('"', "")
        if normalized.lstrip().startswith(("select", "with")) and re.search(
            rf"\b(?:from|join)\s+{re.escape(User.__tablename__)}\b", normalized,
        ):
            selects.append({
                "thread": threading.get_ident(),
                "statement": normalized,
                "frames": [
                    {"file": frame.filename, "line": frame.lineno,
                     "function": frame.name}
                    for frame in traceback.extract_stack()
                    if "fastapi/dependencies/utils.py" in frame.filename
                    or frame.filename.endswith("services/core/auth_service.py")
                ],
            })

    if case in profiles:
        token = create_access_token({"sub": profiles[case]["id"]})
    elif case == "unknown-user":
        token = create_access_token({"sub": uuid4().hex})
    else:
        token = "invalid-local-token"
    headers = {} if case == "no-credential" else {"Authorization": f"Bearer {token}"}
    event.listen(engine, "before_cursor_execute", observe)
    try:
        # ASGITransport does not enter the app lifespan; /me has no provider work.
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test",
        ) as client:
            response = await client.get("/api/auth/me", headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    facts = {
        "case": case, "loop_thread": loop_thread,
        "status": response.status_code, "user_select_count": len(selects),
        "user_selects": selects, "sessions": lifecycles[lifecycle_start:],
    }
    record_property("auth_dependency_probe", json.dumps(facts, sort_keys=True))
    print(f"AUTH_DEPENDENCY_PROBE {json.dumps(facts, sort_keys=True)}")
    assert all(item["closed"] for item in facts["sessions"])
    assert all(item["expire_on_commit"] for item in facts["sessions"])
    assert all(item["initial_identity_count"] == 0 for item in facts["sessions"])
    return response, facts


@pytest.mark.asyncio
async def test_shared_auth_user_lookup_runs_outside_request_loop(
    auth_worker_probe, record_property,
):
    response, facts = await _request(auth_worker_probe, "active", record_property)
    assert response.status_code == 200
    assert response.json() == auth_worker_probe[1]["active"]
    assert facts["user_select_count"] == 1, facts
    assert facts["user_selects"][0]["thread"] != facts["loop_thread"], facts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "status", "select_count", "error_code"),
    [
        ("active", 200, 1, None),
        ("invalid-token", 401, 0, ErrorCode.AUTH_TOKEN_INVALID),
        ("no-credential", 401, 0, None),
        ("unknown-user", 401, 1, ErrorCode.AUTH_TOKEN_INVALID),
        ("inactive", 400, 1, ErrorCode.AUTH_INACTIVE_USER),
    ],
)
async def test_shared_auth_http_controls(
    auth_worker_probe, record_property, case, status, select_count, error_code,
):
    response, facts = await _request(auth_worker_probe, case, record_property)
    assert response.status_code == status
    assert facts["user_select_count"] == select_count, facts
    if status == 200:
        assert response.json() == auth_worker_probe[1]["active"]
    elif error_code is not None:
        assert response.json()["error_code"] == error_code
        assert response.json()["detail"] == error_code
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
