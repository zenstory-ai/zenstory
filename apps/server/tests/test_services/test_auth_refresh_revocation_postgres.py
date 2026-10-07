"""Real HTTP/PG refresh rotation versus logout-all revocation interleaving."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, delete, event, text
from sqlmodel import Session, SQLModel, select

import database
from core.error_codes import ErrorCode
from database import get_session
from main import app
from models import User
from models.refresh_token import RefreshTokenRecord
from services.core.auth_service import (
    TOKEN_TYPE_REFRESH,
    create_access_token,
    create_refresh_token,
    get_refresh_token_expires_at,
    hash_password,
    verify_password,
    verify_token,
)

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="owned PostgreSQL URL required",
)
TABLES = [User.__table__, RefreshTokenRecord.__table__]


@pytest.fixture(scope="module")
def revocation_engine():
    assert database.is_postgres, "Set real PostgreSQL DATABASE_URL before app import"
    engine = create_engine(os.environ["ZENSTORY_TEST_POSTGRES_URL"], connect_args={
        "options": "-c timezone=UTC -c statement_timeout=20000 -c lock_timeout=15000",
    })
    assert engine.dialect.name == "postgresql"
    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine, tables=TABLES)
        engine.dispose()


class RevocationProbe:
    def __init__(self, engine):
        self.engine = engine
        self.actors = {}
        self.sessions = {}
        self.sql = []
        self.retained_records = {}
        self.locked = threading.Event()
        self.release = threading.Event()
        self.revoke_selected = threading.Event()
        self.update_started = threading.Event()
        self.user_gate_started = threading.Event()
        self.pause_rotation = False

    def seed(self, name, *, password=False):
        suffix = uuid4().hex
        actor = {
            "user": suffix, "old_id": uuid4().hex,
            "jti": uuid4().hex, "family": uuid4().hex,
        }
        actor["access"] = create_access_token({"sub": actor["user"]})
        actor["refresh"] = create_refresh_token({
            "sub": actor["user"], "jti": actor["jti"],
            "family_id": actor["family"],
        })
        with Session(self.engine) as session:
            session.add(User(
                id=actor["user"], username=f"revocation-{suffix}",
                email=f"revocation-{suffix}@example.test", hashed_password=hash_password("OwnedOld1!") if password else "unused",
                email_verified=True, is_active=True,
            ))
            session.flush()
            session.add(RefreshTokenRecord(
                id=actor["old_id"], user_id=actor["user"], token_jti=actor["jti"],
                family_id=actor["family"], expires_at=get_refresh_token_expires_at(),
            ))
            session.commit()
        self.actors[name] = actor
        return actor

    def before_sql(self, connection, _cursor, statement, _parameters, _context, _many):
        label = connection.info.get("revocation_label")
        sql = statement.lower().replace('"', "")
        if not label:
            return
        if label == "B" and (
            (sql.lstrip().startswith("select") and "from user" in sql and "for update" in sql)
            or sql.lstrip().startswith("update user")
        ):
            self.user_gate_started.set()
        if RefreshTokenRecord.__tablename__ not in sql and "user" not in sql:
            return
        self.sql.append({
            "label": label, "pid": connection.info["revocation_pid"],
            "thread": threading.get_ident(), "sql": sql,
            "frames": [
                {"file": frame.filename, "line": frame.lineno,
                 "function": frame.name}
                for frame in traceback.extract_stack()
                if frame.filename.endswith("api/auth.py")
            ],
        })
        if label == "B" and sql.lstrip().startswith("update") and RefreshTokenRecord.__tablename__ in sql:
            session = self.sessions[label]["session"]
            self.retained_records[label] = [
                obj for obj in session.identity_map.values()
                if isinstance(obj, RefreshTokenRecord)
            ]
            self.sessions[label]["update_record_ids"] = [
                obj.id for obj in self.retained_records[label]
            ]
            self.update_started.set()

    def after_sql(self, connection, _cursor, statement, _parameters, _context, _many):
        label = connection.info.get("revocation_label")
        sql = statement.lower().replace('"', "")
        if label == "A" and "from user" in sql and "for update" in sql and self.pause_rotation:
            self.locked.set()  # The real DB cursor executed the User-locking SELECT.
            assert self.release.wait(12), "owned rotation lock was not released"
        if RefreshTokenRecord.__tablename__ not in sql:
            return
        if label == "B" and sql.lstrip().startswith("select") and "revoked_at is null" in sql:
            self.revoke_selected.set()

    def request(self, label, path, actor, *, refresh=None):
        async def send():
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test",
            ) as client:
                headers = {"X-Revocation-Probe": label}
                if path == "/api/auth/refresh":
                    return await client.post(path, json={"refresh_token": refresh or actor["refresh"]}, headers=headers)
                headers["Authorization"] = f"Bearer {actor['access']}"
                if path == "/api/auth/me":
                    return await client.get(path, headers=headers)
                if path == "/api/auth/change-password":
                    return await client.post(path, json={
                        "old_password": "OwnedOld1!", "new_password": "OwnedNew2!",
                    }, headers=headers)
                return await client.post(path, headers=headers)

        return asyncio.run(send())

    def snapshot(self, actor):
        with Session(self.engine) as reader:
            rows = reader.exec(select(RefreshTokenRecord).where(
                RefreshTokenRecord.user_id == actor["user"],
            ).order_by(RefreshTokenRecord.token_jti)).all()
            return [row.model_dump(mode="json") for row in rows]

    def observe_blocked_user_gate(self, *, password=False):
        blocker = self.sessions["A"]["pid"]
        waiting = self.sessions["B"]["pid"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            with self.engine.connect() as observer:
                row = observer.execute(text(
                    "SELECT pid, datname, wait_event_type, wait_event, query, "
                    "pg_blocking_pids(pid) AS blockers FROM pg_stat_activity "
                    "WHERE datname = current_database() AND pid = :pid"
                ), {"pid": waiting}).mappings().one()
            if row["wait_event_type"] == "Lock" and blocker in row["blockers"]:
                query = row["query"].lower().replace('"', "")
                if password:
                    assert query.lstrip().startswith("update user")
                else:
                    assert query.lstrip().startswith("select") and "from user" in query and "for update" in query
                return dict(row)
        raise AssertionError("B User gate never showed an own-database wait on A")


@pytest.fixture
def revocation_probe(revocation_engine, monkeypatch, record_property):
    probe = RevocationProbe(revocation_engine)

    def clear_connection_labels(_connection, connection_record):
        connection_record.info.pop("revocation_label", None)
        connection_record.info.pop("revocation_pid", None)

    def request_session(request: Request):
        label = request.headers["X-Revocation-Probe"]
        session = Session(revocation_engine)
        connection = session.connection()
        pid = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
        connection.info["revocation_label"] = label
        connection.info["revocation_pid"] = pid
        probe.sessions[label] = {
            "session": session, "pid": pid, "closed": False,
            "expire_on_commit": session.expire_on_commit,
            "initial_identity_count": len(session.identity_map),
        }
        try:
            yield session
        finally:
            # Connection may have returned to pool at commit; don't re-check it out.
            session.close()
            probe.sessions[label]["closed"] = True

    event.listen(revocation_engine, "before_cursor_execute", probe.before_sql)
    event.listen(revocation_engine, "after_cursor_execute", probe.after_sql)
    event.listen(revocation_engine, "checkin", clear_connection_labels)
    with monkeypatch.context() as overrides:
        overrides.setitem(app.dependency_overrides, get_session, request_session)
        try:
            yield probe
        finally:
            probe.release.set()
            event.remove(revocation_engine, "before_cursor_execute", probe.before_sql)
            event.remove(revocation_engine, "after_cursor_execute", probe.after_sql)
            event.remove(revocation_engine, "checkin", clear_connection_labels)
            assert all(item["closed"] for item in probe.sessions.values())
            assert all(item["expire_on_commit"] for item in probe.sessions.values())
            assert all(item["initial_identity_count"] == 0 for item in probe.sessions.values())
            with revocation_engine.begin() as cleanup:
                user_ids = [actor["user"] for actor in probe.actors.values()]
                cleanup.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.user_id.in_(user_ids)))
                cleanup.execute(delete(User).where(User.id.in_(user_ids)))
            probe.retained_records.clear()
            record_property("owned_resources_cleanup", "sessions closed/listeners removed/actor rows deleted")


def _record(record_property, facts):
    value = json.dumps(facts, sort_keys=True, default=str)
    record_property("refresh_revocation_probe", value)
    print(f"REFRESH_REVOCATION_PROBE {value}")


@pytest.mark.parametrize("password", [False, True], ids=["logout", "password-change"])
def test_concurrent_revocation_revokes_rotation_descendant(revocation_probe, record_property, password):
    probe = revocation_probe
    actor = probe.seed("owner", password=password)
    probe.pause_rotation = True
    with ThreadPoolExecutor(max_workers=2) as writers:
        rotation = writers.submit(probe.request, "A", "/api/auth/refresh", actor)
        try:
            assert probe.locked.wait(5), "A did not execute real User FOR UPDATE"
            endpoint = "/api/auth/change-password" if password else "/api/auth/logout"
            logout = writers.submit(probe.request, "B", endpoint, actor)
            assert probe.user_gate_started.wait(5), "B did not reach the real User gate"
            blocked = probe.observe_blocked_user_gate(password=password)
            assert not probe.revoke_selected.is_set(), "B enumerated tokens before owning User"
            assert not probe.update_started.is_set()
            assert not rotation.done() and not logout.done()
        finally:
            probe.release.set()
        rotated = rotation.result(10)
        logged_out = logout.result(10)
    assert rotated.status_code == logged_out.status_code == 200
    new_token = rotated.json()["refresh_token"]
    payload = verify_token(new_token, expected_type=TOKEN_TYPE_REFRESH)
    assert payload and payload["sub"] == actor["user"] and payload["family_id"] == actor["family"]
    before_child_request = probe.snapshot(actor)
    assert len(before_child_request) == 2
    old = next(row for row in before_child_request if row["token_jti"] == actor["jti"])
    assert old["revoked_at"] and old["revoke_reason"] == "rotated"
    assert old["replaced_by_jti"] == payload["jti"]
    descendant = next(row for row in before_child_request if row["token_jti"] == payload["jti"])
    assert descendant["revoked_at"] and descendant["revoke_reason"] == (
        "password_changed" if password else "logout"
    )
    assert descendant["revoked_at"] >= descendant["issued_at"]
    assert probe.sessions["B"]["update_record_ids"] == [descendant["id"]]
    if password:
        with Session(probe.engine) as reader:
            current = reader.get(User, actor["user"])
            assert verify_password("OwnedNew2!", current.hashed_password)
            assert not verify_password("OwnedOld1!", current.hashed_password)
    child = probe.request("C", "/api/auth/refresh", actor, refresh=new_token)
    _record(record_property, {
        "case": "concurrent-password" if password else "concurrent-logout", "wait": blocked, "old_id": actor["old_id"],
        "selected_by_logout": probe.sessions["B"]["update_record_ids"],
        "rotation_status": rotated.status_code, "logout_status": logged_out.status_code,
        "new_child_jti": payload["jti"], "before_child_request": before_child_request,
        "child_refresh_status": child.status_code, "after_child_request": probe.snapshot(actor),
        "sql": probe.sql,
    })
    assert child.status_code == 401, "logout-all completed, but concurrent descendant still refreshes"
    assert child.json()["error_code"] == ErrorCode.AUTH_TOKEN_INVALID


@pytest.mark.parametrize("order", ["refresh-then-logout", "logout-then-refresh"])
def test_sequential_revocation_controls(revocation_probe, record_property, order):
    probe = revocation_probe
    actor = probe.seed("owner")
    token = actor["refresh"]
    if order == "refresh-then-logout":
        rotated = probe.request("rotate", "/api/auth/refresh", actor)
        assert rotated.status_code == 200
        token = rotated.json()["refresh_token"]
    assert probe.request("logout", "/api/auth/logout", actor).status_code == 200
    response = probe.request("rejected", "/api/auth/refresh", actor, refresh=token)
    _record(record_property, {"case": order, "status": response.status_code, "rows": probe.snapshot(actor)})
    assert response.status_code == 401
    assert response.json()["error_code"] == ErrorCode.AUTH_TOKEN_INVALID
    assert all(row["revoked_at"] for row in probe.snapshot(actor))


def test_logout_keeps_other_user_and_stateless_access_contract(revocation_probe, record_property):
    probe = revocation_probe
    actor, other = probe.seed("owner"), probe.seed("other")
    assert probe.request("logout", "/api/auth/logout", actor).status_code == 200
    me = probe.request("me", "/api/auth/me", actor)
    other_refresh = probe.request("other", "/api/auth/refresh", other)
    _record(record_property, {
        "case": "other-user-and-stateless-access", "access_status": me.status_code,
        "other_refresh_status": other_refresh.status_code,
        "owner_rows": probe.snapshot(actor), "other_rows": probe.snapshot(other),
    })
    assert me.status_code == 200 and me.json()["id"] == actor["user"]
    assert other_refresh.status_code == 200 and other_refresh.json()["user"]["id"] == other["user"]
    assert all(row["revoked_at"] for row in probe.snapshot(actor))


def test_empty_token_logout_releases_user_gate(revocation_probe, record_property):
    probe = revocation_probe
    actor = probe.seed("owner")
    with probe.engine.begin() as setup:
        setup.execute(delete(RefreshTokenRecord).where(RefreshTokenRecord.user_id == actor["user"]))
    assert probe.request("empty", "/api/auth/logout", actor).status_code == 200
    assert probe.sessions["empty"]["closed"]
    assert not any(row["sql"].lstrip().startswith(("update refresh_token_record", "delete from refresh_token_record"))
                   for row in probe.sql if row["label"] == "empty")
    with Session(probe.engine) as writer:
        assert writer.exec(select(User.id).where(User.id == actor["user"]).with_for_update(nowait=True)).one() == actor["user"]
    _record(record_property, {"case": "empty-token-logout", "sessions_closed": True, "subsequent_nowait_lock": True})
