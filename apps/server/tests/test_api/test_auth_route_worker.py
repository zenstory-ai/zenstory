"""Actual no-await auth route transaction placement and HTTP compatibility."""

from __future__ import annotations

import json
import threading
import traceback
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlmodel import Session, select

from core.error_codes import ErrorCode
from main import app
from models import User
from models.refresh_token import RefreshTokenRecord
from services.core.auth_service import (
    create_access_token,
    create_refresh_token,
    get_refresh_token_expires_at,
    verify_token,
)
from tests.test_api.test_auth_dependency_worker import auth_worker_probe as auth_worker_probe


@pytest.fixture
def auth_route_probe(auth_worker_probe):
    engine, profiles, lifecycles = auth_worker_probe
    RefreshTokenRecord.__table__.create(engine)
    actor = profiles["active"]
    jti, family = uuid4().hex, uuid4().hex
    record = RefreshTokenRecord(
        user_id=actor["id"], token_jti=jti, family_id=family,
        expires_at=get_refresh_token_expires_at(),
    )
    with Session(engine) as session:
        session.add(record)
        session.commit()
    return engine, profiles, lifecycles, jti, create_refresh_token({
        "sub": actor["id"], "jti": jti, "family_id": family,
    })


async def _call(probe, operation, record_property, *, collision=False):
    engine, profiles, lifecycles, _jti, refresh = probe
    loop_thread = threading.get_ident()
    sql = []
    target = {"refresh": "refresh_token", "logout": "logout", "profile": "update_current_user"}[operation]

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        frames = [
            {"file": frame.filename, "function": frame.name}
            for frame in traceback.extract_stack()
            if frame.filename.endswith("/api/auth.py")
        ]
        if any(frame["function"] == target for frame in frames):
            sql.append({"thread": threading.get_ident(), "sql": statement, "frames": frames})

    event.listen(engine, "before_cursor_execute", observe)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            access = create_access_token({"sub": profiles["active"]["id"]})
            headers = {"Authorization": f"Bearer {access}"}
            if operation == "refresh":
                response = await client.post("/api/auth/refresh", json={"refresh_token": refresh})
            elif operation == "logout":
                response = await client.post("/api/auth/logout", headers=headers)
            else:
                username = profiles["inactive"]["username"] if collision else "new-" + uuid4().hex
                response = await client.put("/api/auth/me", json={"username": username}, headers=headers)
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    facts = {"operation": operation, "loop_thread": loop_thread, "status": response.status_code, "route_sql": sql}
    record_property("auth_route_worker", json.dumps(facts, sort_keys=True))
    print("AUTH_ROUTE_WORKER " + json.dumps(facts, sort_keys=True))
    assert all(lifecycle["closed"] and lifecycle["expire_on_commit"] for lifecycle in lifecycles)
    return response, facts


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["refresh", "logout", "profile"])
async def test_no_await_auth_transaction_is_off_request_loop(auth_route_probe, operation, record_property):
    response, facts = await _call(auth_route_probe, operation, record_property)
    assert response.status_code == 200, response.text
    engine, profiles, _lifecycles, jti, _refresh = auth_route_probe
    with Session(engine) as fresh:
        old = fresh.exec(select(RefreshTokenRecord).where(RefreshTokenRecord.token_jti == jti)).one()
        if operation == "refresh":
            assert old.revoke_reason == "rotated"
            payload = verify_token(response.json()["refresh_token"])
            assert payload is not None and payload["jti"] == old.replaced_by_jti
            child = fresh.exec(select(RefreshTokenRecord).where(RefreshTokenRecord.token_jti == payload["jti"])).one()
            assert child.revoked_at is None and child.user_id == profiles["active"]["id"]
        elif operation == "logout":
            assert old.revoked_at is not None and old.revoke_reason == "logout"
        else:
            assert old.revoked_at is None
            assert fresh.get(User, profiles["active"]["id"]).username == response.json()["username"]
    assert facts["route_sql"], facts
    assert all(item["thread"] != facts["loop_thread"] for item in facts["route_sql"]), facts


@pytest.mark.asyncio
async def test_profile_duplicate_username_does_not_commit_partial_change(auth_route_probe, record_property):
    response, _facts = await _call(auth_route_probe, "profile", record_property, collision=True)
    assert response.status_code == 400
    assert response.json()["error_code"] == ErrorCode.AUTH_USERNAME_EXISTS
    with Session(auth_route_probe[0]) as fresh:
        assert fresh.get(User, auth_route_probe[1]["active"]["id"]).username == auth_route_probe[1]["active"]["username"]


@pytest.mark.asyncio
async def test_invalid_refresh_keeps_owned_session_record(auth_route_probe):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/auth/refresh", json={"refresh_token": "invalid-local"})
    assert response.status_code == 401
    assert response.json()["error_code"] == ErrorCode.AUTH_TOKEN_INVALID
    with Session(auth_route_probe[0]) as fresh:
        assert fresh.exec(select(RefreshTokenRecord)).one().revoked_at is None
