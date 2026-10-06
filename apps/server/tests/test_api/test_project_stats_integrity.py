"""Actual HTTP regressions for project dashboard/stat persistence contracts."""

from __future__ import annotations

import importlib
import json
from datetime import date, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlmodel import Session, SQLModel, select

from api import stats as stats_api
from database import get_session
from main import app
from models import ChatMessage, ChatSession, File, Project, User
from models.writing_stats import WritingStats, WritingStreak
from services.core.auth_service import create_access_token
from services.infra.dashboard_cache import dashboard_cache

stats_module = importlib.import_module("services.features.writing_stats_service")
TABLES = [User.__table__, Project.__table__, File.__table__, WritingStats.__table__,
          WritingStreak.__table__, ChatSession.__table__, ChatMessage.__table__]
TODAY = date(2026, 10, 6)


@pytest.fixture
def stats_probe(tmp_path, monkeypatch, record_property):
    """Real JWT/auth/ownership, own FK-valid SQLite, cold production-like sessions."""
    path = tmp_path / "project-stats-integrity.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", foreign_keys)
    lifecycles = []
    effects = []
    saved_overrides = dict(app.dependency_overrides)
    try:
        SQLModel.metadata.create_all(engine, tables=TABLES)
        with engine.begin() as connection:
            connection.execute(text("CREATE UNIQUE INDEX ix_writing_stats_user_project_date "
                                    "ON writing_stats (user_id, project_id, stats_date)"))
            connection.execute(text("CREATE UNIQUE INDEX ix_writing_streak_user_project "
                                    "ON writing_streak (user_id, project_id)"))
        with Session(engine) as seed:
            suffix = uuid4().hex
            user = User(username=f"stats-{suffix}", email=f"stats-{suffix}@example.test",
                        hashed_password="unused-local-test", is_active=True)
            other = User(username=f"other-{suffix}", email=f"other-{suffix}@example.test",
                         hashed_password="unused-local-test", is_active=True)
            seed.add_all([user, other])
            seed.flush()
            project = Project(name="Owned writing project", owner_id=user.id,
                              description="Original description")
            foreign = Project(name="Foreign writing project", owner_id=other.id)
            seed.add_all([project, foreign])
            seed.flush()
            ids = {"user": user.id, "project": project.id, "foreign": foreign.id}
            seed.commit()
            assert seed.exec(text("PRAGMA foreign_key_check")).all() == []

        def cold_session():
            session = Session(engine)
            lifecycle = {"cold": not session.identity_map, "expire_on_commit": session.expire_on_commit,
                         "closed": False}
            lifecycles.append(lifecycle)
            try:
                yield session
            finally:
                session.close()
                lifecycle["closed"] = True

        # Only cache/infrastructure boundary is local; no auth/ORM/service replacement.
        monkeypatch.setattr(dashboard_cache, "get_json", lambda *_a, **_kw: None)
        monkeypatch.setattr(dashboard_cache, "set_json", lambda *_a, **_kw: None)
        monkeypatch.setattr(dashboard_cache, "get_project_version", lambda *_a, **_kw: 1)
        monkeypatch.setattr(dashboard_cache, "bump_project_version",
                            lambda *a, **kw: effects.append((a, kw)))
        monkeypatch.setattr(stats_api, "utcnow", lambda: datetime(2026, 10, 6, 12))
        monkeypatch.setattr(stats_module, "utcnow", lambda: datetime(2026, 10, 6, 12))
        app.dependency_overrides[get_session] = cold_session
        headers = {"Authorization": f"Bearer {create_access_token(data={'sub': ids['user']})}"}
        yield engine, ids, headers, lifecycles, effects
        assert all(item == {"cold": True, "expire_on_commit": True, "closed": True}
                   for item in lifecycles)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved_overrides)
        event.remove(engine, "connect", foreign_keys)
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
        record_property("owned_sqlite_cleanup", "disposed/unlinked; request sessions closed")


async def _http(probe, method, suffix, *, payload=None, params=None):
    _, ids, headers, _, _ = probe
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://local-test") as client:
        return await client.request(method, f"/api/v1/projects/{ids['project']}{suffix}",
                                    headers=headers, json=payload, params=params)


def _fact(record_property, finding, **facts):
    value = json.dumps({"finding": finding, **facts}, sort_keys=True, default=str)
    print(f"STATS_INTEGRITY {value}")
    record_property("stats_integrity", value)


def _project(engine, ids):
    with Session(engine) as reader:
        return reader.get(Project, ids["project"]).model_dump(mode="json")


def _streak(engine, ids):
    with Session(engine) as reader:
        row = reader.exec(select(WritingStreak).where(
            WritingStreak.user_id == ids["user"], WritingStreak.project_id == ids["project"],
        )).one()
        return row.model_dump(mode="json")


@pytest.mark.parametrize(("project_type", "file_type"), [("novel", "draft"), ("screenplay", "script")])
async def test_dashboard_counts_supported_writing_content(stats_probe, project_type, file_type, record_property):
    engine, ids, _, _, _ = stats_probe
    with Session(engine) as seed:
        project = seed.get(Project, ids["project"])
        project.project_type = project_type
        writing = File(project_id=project.id, title="Opening", file_type=file_type,
                       content="hello " * 60, file_metadata=json.dumps({"word_count": 60}))
        seed.add_all([writing,
                      File(project_id=project.id, title="Deleted private body", file_type=file_type,
                           is_deleted=True, content="hidden", file_metadata='{"word_count":900}'),
                      File(project_id=ids["foreign"], title="Foreign private body", file_type=file_type,
                           content="foreign", file_metadata='{"word_count":800}')])
        seed.commit()
        writing_id = writing.id
    response = await _http(stats_probe, "GET", "/stats", params={"client_date": TODAY.isoformat()})
    assert response.status_code == 200, response.text
    body = response.json()
    _fact(record_property, "F1", project_type=project_type, file_type=file_type,
          total=body["total_word_count"], chapters=body["chapter_completion"])
    assert body["project_id"] == ids["project"]
    assert body["project_name"] == "Owned writing project"
    assert body["total_word_count"] == 60
    assert body["chapter_completion"] == {
        "total_chapters": 1, "completed_chapters": 1, "in_progress_chapters": 0,
        "not_started_chapters": 0, "completion_percentage": 100,
        "chapter_details": [{"outline_id": writing_id, "draft_id": writing_id, "title": "Opening",
                             "word_count": 60, "target_word_count": None, "status": "complete",
                             "completion_percentage": 100}],
    }
    assert "private body" not in response.text


async def _record(probe, day, *, activity=10):
    payload = {"word_count": 100, "words_added": activity, "words_deleted": 0,
               "edit_time_seconds": 5}
    if day is not None:
        payload["stats_date"] = day.isoformat()
    response = await _http(probe, "POST", "/stats/record", payload=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def test_backdated_record_preserves_streak_then_next_day_advances_once(stats_probe, record_property):
    engine, ids, _, _, _ = stats_probe
    await _record(stats_probe, TODAY)
    before = _streak(engine, ids)
    historical = await _record(stats_probe, TODAY - timedelta(days=1))
    after_history = _streak(engine, ids)
    next_day = await _record(stats_probe, TODAY + timedelta(days=1))
    after_next = _streak(engine, ids)
    with Session(engine) as reader:
        rows = reader.exec(select(WritingStats).where(WritingStats.project_id == ids["project"])
                           .order_by(WritingStats.stats_date)).all()
        daily = [row.model_dump(mode="json") for row in rows]
    _fact(record_property, "F2", before=before, after_history=after_history,
          after_next=after_next, historical_response=historical, next_response=next_day, daily=daily)
    assert [row["stats_date"] for row in daily] == [
        (TODAY - timedelta(days=1)).isoformat(), TODAY.isoformat(), (TODAY + timedelta(days=1)).isoformat()]
    assert all(row["words_added"] == 10 and row["edit_sessions"] == 1 for row in daily)
    assert after_history == before
    assert historical["streak_updated"] is False
    assert after_next["current_streak"] == 2
    assert after_next["longest_streak"] == 2
    assert after_next["streak_recovery_count"] == 0
    assert after_next["last_writing_date"] == (TODAY + timedelta(days=1)).isoformat()


@pytest.mark.parametrize(("offset", "activity", "expected_count", "recoveries"), [
    (0, 10, 1, 0), (1, 10, 2, 0), (2, 10, 2, 1), (1, 9, 1, 0), (None, 10, 1, 0),
])
async def test_existing_streak_date_threshold_and_grace_controls(
    stats_probe, offset, activity, expected_count, recoveries, record_property,
):
    engine, ids, _, _, _ = stats_probe
    await _record(stats_probe, TODAY)
    day = None if offset is None else TODAY + timedelta(days=offset)
    response = await _record(stats_probe, day, activity=activity)
    actual = _streak(engine, ids)
    _fact(record_property, "F2-control", offset=offset, activity=activity, row=actual, response=response)
    assert actual["current_streak"] == expected_count
    assert actual["longest_streak"] == expected_count
    assert actual["streak_recovery_count"] == recoveries
    expected_day = day if expected_count == 2 else TODAY
    assert actual["last_writing_date"] == expected_day.isoformat()
    assert response["stats_date"] == (day or TODAY).isoformat()


@pytest.mark.parametrize("field", ["name", "project_type"])
async def test_required_project_update_null_is_validation_error_without_mutation(stats_probe, field, record_property):
    engine, ids, _, _, _ = stats_probe
    before = _project(engine, ids)
    response = await _http(stats_probe, "PUT", "", payload={field: None})
    after = _project(engine, ids)
    _fact(record_property, "F7", field=field, status=response.status_code, body=response.json(),
          before=before, after=after)
    assert after == before
    assert response.status_code == 422, response.text


@pytest.mark.parametrize(("payload", "status"), [
    ({"description": None}, 200), ({"owner_id": "forbidden"}, 422), ({"project_type": "invalid"}, 422),
])
async def test_project_update_nullable_and_protected_controls(stats_probe, payload, status, record_property):
    engine, ids, _, _, _ = stats_probe
    before = _project(engine, ids)
    response = await _http(stats_probe, "PUT", "", payload=payload)
    after = _project(engine, ids)
    _fact(record_property, "F7-control", payload=payload, status=response.status_code,
          body=response.json(), before=before, after=after)
    assert response.status_code == status, response.text
    if status == 422:
        assert after == before
    else:
        assert after["description"] is None
        assert {k: v for k, v in after.items() if k not in {"description", "updated_at"}} == {
            k: v for k, v in before.items() if k not in {"description", "updated_at"}
        }
        assert response.json()["description"] is None


@pytest.mark.parametrize(("client_date", "utc_day", "word_periods", "ai_periods"), [
    ("2026-05-31", "2026-06-01", (11, 11, 11), ("prior", "prior", "prior")),
    ("2026-06-01", "2026-06-01", (0, 0, 0), ("next", "next", "next")),
    (None, "2026-06-01", (0, 0, 0), ("next", "next", "next")),
    ("2026-06-02", "2026-06-03", (11, 11, 11), ("prior", "prior", "prior")),
    ("2026-06-03", "2026-06-03", (0, 11, 11), ("next", "both", "both")),
])
async def test_dashboard_ai_periods_follow_supported_client_date(
    stats_probe, monkeypatch, client_date, utc_day, word_periods, ai_periods, record_property,
):
    engine, ids, _, _, _ = stats_probe
    today = date.fromisoformat(utc_day)
    prior = today - timedelta(days=1)
    # Date-only contract: both Sunday/month-end and normal same-week/month boundaries.
    now = datetime.combine(today, datetime.min.time()) + timedelta(minutes=30)
    monkeypatch.setattr(stats_api, "utcnow", lambda: now)
    monkeypatch.setattr(stats_module, "utcnow", lambda: now)
    with Session(engine) as seed:
        chat = ChatSession(project_id=ids["project"], user_id=ids["user"], title="Owned chat")
        seed.add(chat)
        seed.flush()
        seed.add_all([
            ChatMessage(session_id=chat.id, role="assistant", content="Prior response",
                        created_at=datetime.combine(prior, datetime.min.time()) + timedelta(hours=23, minutes=50),
                        message_metadata='{"usage":{"input_tokens":7,"output_tokens":3}}'),
            ChatMessage(session_id=chat.id, role="assistant", content="Next response",
                        created_at=datetime.combine(today, datetime.min.time()) + timedelta(minutes=10),
                        message_metadata='{"usage":{"input_tokens":13,"output_tokens":5}}'),
            WritingStats(user_id=ids["user"], project_id=ids["project"], stats_date=prior,
                         word_count=11, words_added=11, edit_sessions=1),
        ])
        seed.commit()
    params = {} if client_date is None else {"client_date": client_date}
    response = await _http(stats_probe, "GET", "/stats", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    _fact(record_property, "F6", client_date=client_date, utc_day=utc_day, response=body)
    assert body["ai_usage"]["current"]["total_messages"] == 2
    assert body["ai_usage"]["current"]["total_tokens"] == 28
    for name, expected in zip(["words_today", "words_this_week", "words_this_month"], word_periods, strict=True):
        assert body[name] == expected
    expected_metrics = {"prior": (1, 7, 3), "next": (1, 13, 5), "both": (2, 20, 8)}
    for name, expectation in zip(["today", "this_week", "this_month"], ai_periods, strict=True):
        count, input_tokens, output_tokens = expected_metrics[expectation]
        period = body["ai_usage"][name]
        assert period["total"] == period["ai"] == count
        assert period["user"] == 0
        assert period["input_tokens"] == input_tokens
        assert period["output_tokens"] == output_tokens
        assert period["total_tokens"] == input_tokens + output_tokens
