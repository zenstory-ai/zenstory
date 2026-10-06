"""Real HTTP/ORM proof limited to the three persona handler worker boundaries."""

from __future__ import annotations

import json
import threading
import traceback
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from database import get_session
from main import app
from models import User, UserPersonaProfile
from services.core.auth_service import create_access_token

HANDLERS = {
    "get_persona_onboarding_state", "upsert_persona_onboarding",
    "get_persona_recommendations",
}
COMPLETED = datetime(2026, 3, 6, 12)
SERIAL = {
    "id": "persona_serial_streak", "title": "连载节奏建议",
    "description": "设置每天写作目标并追踪连更进度。", "action": "/dashboard",
}
PROFESSIONAL = {
    "id": "persona_professional_efficiency", "title": "效率工作流",
    "description": "突出大纲-章节-改稿的一键流转能力。", "action": "/dashboard/projects",
}
MONETIZE = {
    "id": "goal_monetize_upgrade", "title": "变现能力建议",
    "description": "查看升级权益，优先解锁增长与商业化能力。", "action": "/pricing",
}
ADVANCED = {
    "id": "level_advanced_shortcut", "title": "进阶快捷模式",
    "description": "为你优先展示批量改稿和高阶编辑能力。", "action": "/dashboard/projects",
}
PAYLOAD = {
    "selected_personas": [" serial ", "serial", "professional"],
    "selected_goals": ["finishBook", "finishBook", "monetize"],
    "experience_level": "advanced", "skipped": False,
}


@pytest.fixture
def persona_worker_probe(tmp_path, monkeypatch, record_property):
    path = tmp_path / "persona-worker.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", foreign_keys)
    ids = {name: uuid4().hex for name in ("own", "missing", "malformed", "foreign", "inactive")}
    lifecycles = []
    monkeypatch.setenv("PERSONA_ONBOARDING_ROLLOUT_AT", "2026-03-05T16:00:00Z")
    monkeypatch.setenv("PERSONA_ONBOARDING_NEW_USER_WINDOW_DAYS", "7")
    try:
        User.__table__.create(engine)
        UserPersonaProfile.__table__.create(engine)
        with Session(engine) as seed:
            for name, user_id in ids.items():
                seed.add(User(
                    id=user_id, username=f"persona-worker-{user_id}",
                    email=f"persona-worker-{user_id}@example.test",
                    hashed_password="unused-local-test-hash", email_verified=True,
                    is_active=name != "inactive", created_at=utcnow() - timedelta(days=1),
                ))
            seed.commit()
            for name, personas, goals in (
                ("own", '["serial"]', '[]'),
                ("foreign", '["studio"]', '["monetize"]'),
                ("malformed", 'not-json', '{"not":"array"}'),
            ):
                seed.add(UserPersonaProfile(
                    user_id=ids[name], selected_personas=personas, selected_goals=goals,
                    experience_level="intermediate", completed_at=COMPLETED,
                ))
            seed.commit()
            assert seed.exec(text("PRAGMA foreign_key_check")).all() == []

        def request_session():
            session = Session(engine)
            lifecycle = {
                "enter_thread": threading.get_ident(), "closed": False,
                "expire_on_commit": session.expire_on_commit,
                "initial_identity_count": len(session.identity_map),
            }
            lifecycles.append(lifecycle)
            try:
                yield session
            finally:
                session.close()
                lifecycle.update(closed=True, exit_thread=threading.get_ident())

        with monkeypatch.context() as overrides:
            overrides.setitem(app.dependency_overrides, get_session, request_session)
            yield engine, ids, lifecycles
        assert all(item["closed"] for item in lifecycles)
    finally:
        event.remove(engine, "connect", foreign_keys)
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
        record_property("owned_sqlite_cleanup", "listeners removed/engine disposed/database absent")


def _rows(engine):
    with Session(engine) as reader:
        return {
            item.user_id: item.model_dump(mode="json")
            for item in reader.exec(select(UserPersonaProfile)).all()
        }


async def _http(probe, record_property, *, method, route, actor="own", payload=None):
    engine, ids, lifecycles = probe
    operations = []
    loop_thread = threading.get_ident()
    lifecycle_start = len(lifecycles)

    def capture(operation, statement=None):
        frames = [
            {"file": f.filename, "line": f.lineno, "function": f.name}
            for f in traceback.extract_stack()
            if f.filename.endswith("api/persona.py")
            or f.filename.endswith("services/core/auth_service.py")
            or "fastapi/routing.py" in f.filename
        ]
        operations.append({
            "operation": operation, "statement": statement, "frames": frames,
            "thread": threading.get_ident(),
            "persona": any(
                f["file"].endswith("api/persona.py") and f["function"] in HANDLERS
                for f in frames
            ),
        })

    def cursor(_connection, _cursor, statement, _parameters, _context, _many):
        capture(statement.lstrip().split()[0].upper(), statement)

    def commit(_connection):
        capture("COMMIT")

    token = create_access_token({"sub": ids.get(actor, uuid4().hex)})
    if actor == "invalid-token":
        token = "invalid-local-token"
    headers = {} if actor == "no-credential" else {"Authorization": f"Bearer {token}"}
    event.listen(engine, "before_cursor_execute", cursor)
    event.listen(engine, "commit", commit)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.request(
                method, f"/api/v1/persona/{route}", headers=headers, json=payload,
            )
    finally:
        event.remove(engine, "before_cursor_execute", cursor)
        event.remove(engine, "commit", commit)
    facts = {
        "method": method, "route": route, "actor": actor, "status": response.status_code,
        "loop_thread": loop_thread, "operations": operations,
        "sessions": lifecycles[lifecycle_start:], "body": response.json(),
    }
    record_property("persona_worker_probe", json.dumps(facts, ensure_ascii=False, sort_keys=True))
    print("PERSONA_WORKER_PROBE " + json.dumps(facts, ensure_ascii=False, sort_keys=True))
    assert response.headers["content-type"] == "application/json"
    assert len(facts["sessions"]) == (0 if actor == "no-credential" else 1)
    assert all(s["closed"] and s["expire_on_commit"] and s["initial_identity_count"] == 0
               for s in facts["sessions"])
    auth_selects = [
        o for o in operations if not o["persona"] and o["operation"] == "SELECT"
        and any(f["file"].endswith("services/core/auth_service.py") for f in o["frames"])
    ]
    assert len(auth_selects) == (0 if actor in {"invalid-token", "no-credential"} else 1)
    return response, facts


def _assert_worker(facts, *, write=None):
    operations = [o for o in facts["operations"] if o["persona"]]
    assert operations, facts
    assert [o["operation"] for o in operations].count("SELECT") == (2 if write else 1)
    assert [o["operation"] for o in operations].count("COMMIT") == (1 if write else 0)
    if write:
        assert [o["operation"] for o in operations].count(write) == 1
    assert all(o["thread"] != facts["loop_thread"] for o in operations), facts


@pytest.mark.asyncio
@pytest.mark.parametrize(("route", "actor"), [
    ("onboarding", "own"), ("onboarding", "missing"), ("onboarding", "malformed"),
    ("recommendations", "own"), ("recommendations", "missing"),
])
async def test_persona_get_handler_sql_runs_in_worker(persona_worker_probe, record_property, route, actor):
    engine, _ids, _lifecycles = persona_worker_probe
    before = _rows(engine)
    response, facts = await _http(persona_worker_probe, record_property, method="GET", route=route, actor=actor)
    assert response.status_code == 200
    recommendations = [SERIAL] if actor == "own" else []
    expected = {"recommendations": recommendations}
    if route == "onboarding":
        profile = None if actor == "missing" else {
            "version": 1, "completed_at": COMPLETED.isoformat(),
            "selected_personas": ["serial"] if actor == "own" else [],
            "selected_goals": [], "experience_level": "intermediate", "skipped": False,
        }
        expected.update(required=actor == "missing", rollout_at="2026-03-05T16:00:00Z",
                        new_user_window_days=7, profile=profile)
    assert response.json() == expected
    assert _rows(engine) == before
    _assert_worker(facts)


@pytest.mark.asyncio
@pytest.mark.parametrize(("actor", "skipped"), [("missing", False), ("own", False), ("own", True)])
async def test_persona_put_handler_sql_and_commit_run_in_worker(
    persona_worker_probe, record_property, actor, skipped,
):
    engine, ids, _lifecycles = persona_worker_probe
    before = _rows(engine)
    payload = dict(PAYLOAD, skipped=skipped)
    response, facts = await _http(
        persona_worker_probe, record_property, method="PUT", route="onboarding", actor=actor, payload=payload,
    )
    assert response.status_code == 200
    after = _rows(engine)
    stored = after[ids[actor]]
    expected_profile = {
        "version": 1, "completed_at": stored["completed_at"],
        "selected_personas": [] if skipped else ["serial", "professional"],
        "selected_goals": [] if skipped else ["finishBook", "monetize"],
        "experience_level": "advanced", "skipped": skipped,
    }
    assert response.json() == {
        "required": False, "rollout_at": "2026-03-05T16:00:00Z", "new_user_window_days": 7,
        "profile": expected_profile,
        "recommendations": [ADVANCED] if skipped else [SERIAL, PROFESSIONAL, MONETIZE, ADVANCED],
    }
    assert json.loads(stored["selected_personas"]) == expected_profile["selected_personas"]
    assert json.loads(stored["selected_goals"]) == expected_profile["selected_goals"]
    assert stored["updated_at"] == stored["completed_at"]
    assert stored["completed_at"] != COMPLETED.isoformat()
    assert {k: v for k, v in after.items() if k != ids[actor]} == {
        k: v for k, v in before.items() if k != ids[actor]
    }
    assert len(after) == len(before) + (actor == "missing")
    _assert_worker(facts, write="INSERT" if actor == "missing" else "UPDATE")


@pytest.mark.asyncio
@pytest.mark.parametrize(("actor", "status", "error_code"), [
    ("inactive", 400, ErrorCode.AUTH_INACTIVE_USER),
    ("unknown-user", 401, ErrorCode.AUTH_TOKEN_INVALID),
    ("invalid-token", 401, ErrorCode.AUTH_TOKEN_INVALID),
    ("no-credential", 401, None),
])
async def test_persona_auth_rejection_has_no_handler_sql(
    persona_worker_probe, record_property, actor, status, error_code,
):
    before = _rows(persona_worker_probe[0])
    response, facts = await _http(persona_worker_probe, record_property, method="GET", route="onboarding", actor=actor)
    assert response.status_code == status
    if error_code:
        assert response.json()["error_code"] == error_code
    if status == 401:
        assert response.headers["www-authenticate"] == "Bearer"
    assert not any(o["persona"] for o in facts["operations"])
    assert _rows(persona_worker_probe[0]) == before


@pytest.mark.asyncio
@pytest.mark.parametrize(("change", "status"), [
    ({"selected_personas": []}, 400),
    ({"selected_personas": ["unknown"]}, 400),
    ({"selected_personas": ["serial", "professional", "fanfic", "studio"]}, 400),
    ({"selected_goals": ["unknown"]}, 400),
    ({"experience_level": "unknown"}, 400),
    ({"selected_personas": None}, 422),
])
async def test_persona_invalid_payload_preserves_profile(
    persona_worker_probe, record_property, change, status,
):
    before = _rows(persona_worker_probe[0])
    response, facts = await _http(
        persona_worker_probe, record_property, method="PUT", route="onboarding", payload=dict(PAYLOAD, **change),
    )
    assert response.status_code == status
    assert response.json()["error_code"] == ErrorCode.VALIDATION_ERROR
    assert not any(o["persona"] for o in facts["operations"])
    assert _rows(persona_worker_probe[0]) == before
