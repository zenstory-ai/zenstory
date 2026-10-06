"""Actual HTTP first-row races against migration-shaped PostgreSQL indexes."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlmodel import Session, select

from database import get_session
from main import app
from models import Project, User
from models.writing_stats import WritingStats, WritingStreak
from services.core.auth_service import create_access_token
from tests.test_services.test_writing_stats_concurrency_postgres import DAY
from tests.test_services.test_writing_stats_concurrency_postgres import stats_pg_engine as stats_pg_engine

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="explicitly owned PostgreSQL URL required",
)


@pytest.fixture
def first_row_http(stats_pg_engine, record_property):
    engine = stats_pg_engine
    overrides = dict(app.dependency_overrides)
    lifecycle = []
    with Session(engine) as seed:
        suffix = uuid4().hex
        user = User(username=f"first-stats-{suffix}", email=f"first-stats-{suffix}@example.test",
                    hashed_password="unused-local-test", is_active=True)
        seed.add(user)
        seed.flush()
        project = Project(name="Owned first-row HTTP stats", owner_id=user.id)
        seed.add(project)
        seed.flush()
        ids = {"user": user.id, "project": project.id}
        seed.commit()

    def cold_session(request: Request):
        session = Session(engine)
        worker = request.headers["x-owned-stats-writer"]
        item = {"worker": worker, "cold": not session.identity_map,
                "expire_on_commit": session.expire_on_commit, "closed": False}
        lifecycle.append(item)

        def tag_connection(_session, _transaction, connection):
            # Tag only this transaction for observation, without changing its SQL.
            connection.execution_options(owned_stats_writer=worker)

        event.listen(session, "after_begin", tag_connection)
        try:
            yield session
        finally:
            event.remove(session, "after_begin", tag_connection)
            session.close()
            item["closed"] = True

    app.dependency_overrides[get_session] = cold_session
    headers = {"Authorization": f"Bearer {create_access_token(data={'sub': ids['user']})}"}
    try:
        yield engine, ids, headers, lifecycle
        assert len(lifecycle) == 2
        assert all(row["cold"] and row["expire_on_commit"] and row["closed"] for row in lifecycle)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(overrides)
        # Only generated owned cache keys, never a global cache clear.
        cache = importlib.import_module("services.infra.dashboard_cache")

        scope = f":{ids['user']}:{ids['project']}"
        with cache._memory_lock:
            for key in [key for key in cache._memory_cache if scope in key]:
                del cache._memory_cache[key]
            cache._memory_versions.pop(f"project_ver:v1{scope}", None)
        record_property("owned_http_cleanup", "request sessions closed; overrides restored; owned cache removed")


@pytest.mark.parametrize("collision", ["daily", "streak"])
def test_first_row_race_keeps_outer_transaction_usable(first_row_http, collision, record_property):
    engine, ids, headers, lifecycle = first_row_http
    target = WritingStats if collision == "daily" else WritingStreak
    expected_function = "get_or_create_daily_stats" if collision == "daily" else "get_or_create_streak"
    if collision == "streak":
        # Different daily rows prevent their UPDATE lock from hiding streak allocation.
        with Session(engine) as seed:
            seed.add_all([WritingStats(user_id=ids["user"], project_id=ids["project"],
                                       stats_date=DAY + timedelta(days=offset)) for offset in (0, 1)])
            seed.commit()
    else:
        with Session(engine) as seed:
            seed.add(WritingStreak(user_id=ids["user"], project_id=ids["project"],
                                   current_streak=1, longest_streak=1, last_writing_date=DAY,
                                   streak_start_date=DAY))
            seed.commit()
    ready = {name: threading.Event() for name in ("A", "B")}
    release = {name: threading.Event() for name in ("A", "B")}
    absence = {}
    unique_conflicts = []

    def observe_absence(connection, cursor, statement, _parameters, _context, _many):
        worker = connection.get_execution_options().get("owned_stats_writer")
        if (worker not in ready or worker in absence or not statement.lstrip().upper().startswith("SELECT")
                or target.__tablename__ not in statement.lower()):
            return
        if not any(frame.name == expected_function for frame in traceback.extract_stack()):
            return
        assert cursor.rowcount == 0, "both actual allocation SELECTs must observe absence"
        absence[worker] = {"statement": statement, "rows": cursor.rowcount,
                           "pid": connection.connection.driver_connection.get_backend_pid()}
        ready[worker].set()
        assert release[worker].wait(15), "owned first-row barrier must be released"

    def observe_conflict(context):
        if getattr(context.original_exception, "pgcode", None) == "23505":
            unique_conflicts.append({"worker": context.connection.get_execution_options().get("owned_stats_writer"),
                                     "statement": context.statement})

    async def post(worker):
        day = DAY + timedelta(days=1 if collision == "streak" and worker == "B" else 0)
        payload = {"stats_date": day.isoformat(), "word_count": 100,
                   "words_added": 10 if worker == "A" else 20, "words_deleted": 0,
                   "edit_time_seconds": 5}
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://local-test") as client:
            return await client.post(f"/api/v1/projects/{ids['project']}/stats/record", json=payload,
                                     headers={**headers, "x-owned-stats-writer": worker})

    event.listen(engine, "after_cursor_execute", observe_absence)
    event.listen(engine, "handle_error", observe_conflict)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {worker: pool.submit(asyncio.run, post(worker)) for worker in ("A", "B")}
            try:
                assert all(ready[worker].wait(10) for worker in ("A", "B")), "both real absence reads required"
                assert absence["A"]["pid"] != absence["B"]["pid"]
                release["A"].set()
                a = futures["A"].result(timeout=12)
                assert a.status_code == 201, a.text
                release["B"].set()
                b = futures["B"].result(timeout=12)
            finally:
                for signal in release.values():
                    signal.set()
    finally:
        event.remove(engine, "after_cursor_execute", observe_absence)
        event.remove(engine, "handle_error", observe_conflict)
    with Session(engine) as reader:
        daily = [row.model_dump(mode="json") for row in reader.exec(select(WritingStats).where(
            WritingStats.project_id == ids["project"],
        ).order_by(WritingStats.stats_date)).all()]
        streaks = [row.model_dump(mode="json") for row in reader.exec(select(WritingStreak).where(
            WritingStreak.project_id == ids["project"],
        )).all()]
    facts = json.dumps({"collision": collision, "absence": absence, "unique_conflicts": unique_conflicts,
                        "statuses": [a.status_code, b.status_code], "daily": daily,
                        "streaks": streaks, "lifecycle": lifecycle}, sort_keys=True)
    print(f"STATS_FIRST_ROWS {facts}")
    record_property("stats_first_rows", facts)
    assert b.status_code == 201, b.text
    assert len(unique_conflicts) == 1
    assert unique_conflicts[0]["worker"] == "B"
    if collision == "daily":
        assert len(daily) == 1 and len(streaks) == 1
        assert daily[0]["words_added"] == 30
        assert daily[0]["edit_sessions"] == 2
        assert daily[0]["total_edit_time_seconds"] == 10
        assert streaks[0]["current_streak"] == 1
    else:
        assert len(daily) == 2
        assert [row["words_added"] for row in daily] == [10, 20]
        assert all(row["edit_sessions"] == 1 for row in daily)
        assert len(streaks) == 1
        assert streaks[0]["current_streak"] == 2
        assert streaks[0]["last_writing_date"] == (DAY + timedelta(days=1)).isoformat()
