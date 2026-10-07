"""Migration-shaped PostgreSQL proof of actual daily counter update integrity."""

from __future__ import annotations

import json
import os
import queue
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlmodel import Session, SQLModel, select

import database
from models import Project, User
from models.writing_stats import WritingStats, WritingStreak
from services.features.writing_stats_service import writing_stats_service

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="explicitly owned PostgreSQL URL required",
)
DAY = date(2026, 10, 6)
TABLES = [User.__table__, Project.__table__, WritingStats.__table__, WritingStreak.__table__]


@pytest.fixture(scope="module")
def stats_pg_engine():
    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert url == os.environ["DATABASE_URL"]
    assert database.is_postgres is True
    engine = create_engine(url, connect_args={
        "options": "-c timezone=UTC -c statement_timeout=15000 -c lock_timeout=10000",
    })
    assert engine.dialect.name == "postgresql"
    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE UNIQUE INDEX ix_writing_stats_user_project_date "
                                    "ON writing_stats (user_id, project_id, stats_date)"))
            connection.execute(text("CREATE UNIQUE INDEX ix_writing_streak_user_project "
                                    "ON writing_streak (user_id, project_id)"))
            encoding = connection.execute(text("SHOW server_encoding")).scalar_one()
            isolation = connection.execute(text("SHOW transaction_isolation")).scalar_one()
            assert encoding == "UTF8" and isolation == "read committed"
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine, tables=TABLES)
        engine.dispose()


def _seed(engine):
    with Session(engine) as seed:
        suffix = uuid4().hex
        user = User(username=f"daily-{suffix}", email=f"daily-{suffix}@example.test",
                    hashed_password="unused-local-test")
        seed.add(user)
        seed.flush()
        projects = [Project(name=f"Counters {i}", owner_id=user.id) for i in range(2)]
        seed.add_all(projects)
        seed.flush()
        rows = [WritingStats(user_id=user.id, project_id=p.id, stats_date=DAY,
                             word_count=100, words_added=4, words_deleted=2,
                             edit_sessions=3, total_edit_time_seconds=6) for p in projects]
        seed.add_all(rows)
        seed.flush()
        ids = {"user": user.id, "projects": [p.id for p in projects], "rows": [r.id for r in rows]}
        seed.commit()
        return ids


def _row(engine, row_id):
    with Session(engine) as reader:
        return reader.get(WritingStats, row_id).model_dump(mode="json")


def _increment(session, ids, index, worker):
    return writing_stats_service.record_word_count(
        session=session, user_id=ids["user"], project_id=ids["projects"][index], stats_date=DAY,
        word_count=120 if worker == "A" else 140,
        words_added=10 if worker == "A" else 20,
        words_deleted=3 if worker == "A" else 5,
        edit_time_seconds=7 if worker == "A" else 11,
    )


def _facts(record_property, **facts):
    value = json.dumps(facts, sort_keys=True, default=str)
    print(f"DAILY_CONCURRENCY {value}")
    record_property("daily_concurrency", value)


def _check_counters(row, *, added, deleted, sessions, seconds, snapshot):
    assert {key: row[key] for key in ["words_added", "words_deleted", "edit_sessions",
                                     "total_edit_time_seconds", "word_count"]} == {
        "words_added": added, "words_deleted": deleted, "edit_sessions": sessions,
        "total_edit_time_seconds": seconds, "word_count": snapshot,
    }


@pytest.mark.parametrize("same_project", [True, False], ids=["same-row-regression", "different-project-control"])
def test_actual_existing_row_updates_retain_all_activity(stats_pg_engine, same_project, record_property):
    engine = stats_pg_engine
    ids = _seed(engine)
    ready = {worker: threading.Event() for worker in ["A", "B"]}
    release = {worker: threading.Event() for worker in ["A", "B"]}
    flushed = {worker: threading.Event() for worker in ["A", "B"]}
    commit = {worker: threading.Event() for worker in ["A", "B"]}
    b_update_started = threading.Event()
    initial = {}
    retained = {}
    pids = {}
    lifecycle = {}
    observations = queue.Queue()
    thread_worker = threading.local()
    before = [_row(engine, row_id) for row_id in ids["rows"]]

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        worker = getattr(thread_worker, "name", None)
        if worker and WritingStats.__tablename__ in statement.lower():
            if worker == "B" and statement.lstrip().upper().startswith("UPDATE"):
                b_update_started.set()
            observations.put({
                "worker": worker, "statement": statement,
                "frames": [{"file": frame.filename, "line": frame.lineno, "function": frame.name}
                           for frame in traceback.extract_stack()
                           if frame.filename.endswith("writing_stats_service.py")],
            })

    def hold_real_update(_connection, _cursor, statement, _parameters, _context, _many):
        worker = getattr(thread_worker, "name", None)
        if worker and statement.lstrip().upper().startswith("UPDATE") and WritingStats.__tablename__ in statement.lower():
            # Direct arithmetic UPDATE has completed; hold its actual row lock before commit.
            flushed[worker].set()
            if not commit[worker].wait(15):
                raise AssertionError(f"main did not release writer {worker} after actual UPDATE")

    def writer(worker):
        thread_worker.name = worker
        index = 0 if worker == "A" or same_project else 1
        session = Session(engine)
        lifecycle[worker] = {"initial_identity_count": len(session.identity_map),
                             "expire_on_commit": session.expire_on_commit, "closed": False}

        def loaded(_session, instance):
            if isinstance(instance, WritingStats) and instance.id == ids["rows"][index] and worker not in retained:
                retained[worker] = instance  # retain actual ORM object, not a synthetic stale snapshot
                initial[worker] = instance.model_dump(mode="json")
                ready[worker].set()
                if not release[worker].wait(15):
                    raise AssertionError(f"main did not release writer {worker} after real row load")

        event.listen(session, "loaded_as_persistent", loaded)
        try:
            pids[worker] = session.exec(text("SELECT pg_backend_pid()")).scalar_one()
            return _increment(session, ids, index, worker).model_dump(mode="json")
        finally:
            event.remove(session, "loaded_as_persistent", loaded)
            session.rollback()
            session.close()
            lifecycle[worker]["closed"] = True
            del thread_worker.name

    event.listen(engine, "before_cursor_execute", observe)
    event.listen(engine, "after_cursor_execute", hold_real_update)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {worker: pool.submit(writer, worker) for worker in ["A", "B"]}
            try:
                assert all(ready[w].wait(10) for w in ["A", "B"]), "both actual existing rows must load"
                assert pids["A"] != pids["B"]
                assert initial["A"] == before[0]
                assert initial["B"] == before[0 if same_project else 1]
                # Both PostgreSQL transactions really exist and have completed SELECTs.
                with engine.connect() as observer:
                    states = observer.execute(text(
                        "SELECT pid, state, wait_event_type, xact_start IS NOT NULL AS in_transaction "
                        "FROM pg_stat_activity WHERE datname=current_database() AND pid IN (:a,:b)"
                    ), {"a": pids["A"], "b": pids["B"]}).mappings().all()
                assert len(states) == 2
                assert all(s["state"] == "idle in transaction" and s["in_transaction"] for s in states)
                # B's retained ORM row is genuinely old before mutation.
                assert retained["B"].model_dump(mode="json") == initial["B"]
                release["A"].set()
                assert flushed["A"].wait(10), "A must have executed UPDATE before B writes"
                release["B"].set()
                assert b_update_started.wait(10), "B must reach its real SQL UPDATE"
                if same_project:
                    # Observe real DB lock wait, rather than treating an Event/sleep as proof.
                    deadline = time.monotonic() + 8
                    blocked = None
                    with engine.connect() as observer:
                        while time.monotonic() < deadline:
                            state = observer.execute(text(
                                "SELECT pid, wait_event_type, wait_event, pg_blocking_pids(pid) AS blockers "
                                "FROM pg_stat_activity WHERE datname=current_database() AND pid=:b"
                            ), {"b": pids["B"]}).mappings().one()
                            if state["wait_event_type"] == "Lock" and pids["A"] in state["blockers"]:
                                blocked = dict(state)
                                break
                            observer.commit()  # refresh pg_stat_activity transaction snapshot
                            threading.Event().wait(0.01)
                    assert blocked is not None, "B must really wait on A's PostgreSQL row UPDATE lock"
                else:
                    assert flushed["B"].wait(10), "unrelated project update must finish while A holds lock"
                    blocked = None
                commit["A"].set()
                a_result = futures["A"].result(timeout=12)
                assert flushed["B"].wait(10)
                a_committed = _row(engine, ids["rows"][0])
                _check_counters(a_committed, added=14, deleted=5, sessions=4, seconds=13, snapshot=120)
                commit["B"].set()
                b_result = futures["B"].result(timeout=12)
            finally:
                for worker in ["A", "B"]:
                    release[worker].set()
                    commit[worker].set()
    finally:
        event.remove(engine, "before_cursor_execute", observe)
        event.remove(engine, "after_cursor_execute", hold_real_update)
    after = [_row(engine, row_id) for row_id in ids["rows"]]
    sql = list(observations.queue)
    _facts(record_property, same_project=same_project, pids=pids, states=[dict(s) for s in states],
           before=before, initial=initial, a_committed=a_committed, after=after,
           results={"A": a_result, "B": b_result}, lifecycle=lifecycle, blocked_update=blocked, sql=sql)
    assert all(item == {"initial_identity_count": 0, "expire_on_commit": True, "closed": True}
               for item in lifecycle.values())
    assert all(any(entry["worker"] == worker and entry["statement"].lstrip().upper().startswith("SELECT")
                   and any(f["function"] == "get_or_create_daily_stats" for f in entry["frames"])
                   for entry in sql) for worker in ["A", "B"])
    if same_project:
        _check_counters(after[0], added=34, deleted=10, sessions=5, seconds=24, snapshot=140)
        assert after[1] == before[1]
    else:
        _check_counters(after[0], added=14, deleted=5, sessions=4, seconds=13, snapshot=120)
        _check_counters(after[1], added=24, deleted=7, sessions=4, seconds=17, snapshot=140)


def test_sequential_existing_row_accumulates_activity(stats_pg_engine, record_property):
    engine = stats_pg_engine
    ids = _seed(engine)
    for worker in ["A", "B"]:
        with Session(engine) as session:
            assert session.expire_on_commit is True and not session.identity_map
            _increment(session, ids, 0, worker)
    actual = _row(engine, ids["rows"][0])
    with Session(engine) as reader:
        rows = reader.exec(select(WritingStats).where(WritingStats.project_id == ids["projects"][0])).all()
        assert len(rows) == 1
    _facts(record_property, control="sequential", row=actual)
    _check_counters(actual, added=34, deleted=10, sessions=5, seconds=24, snapshot=140)
