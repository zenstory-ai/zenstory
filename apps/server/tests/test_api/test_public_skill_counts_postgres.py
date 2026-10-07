"""Real-transaction collection counters; optional locally, mandatory when PG URL is set."""

import asyncio
import json
import os
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlmodel import Session, create_engine, select

from api import public_skills, skills
from core.error_handler import APIException
from models import PublicSkill, User, UserAddedSkill, UserSkill

PG_URL = os.getenv("ZENSTORY_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="requires isolated PostgreSQL URL")


@pytest.fixture
def storage():
    engine = create_engine(PG_URL, pool_size=6, max_overflow=0)
    tables = [User.__table__, PublicSkill.__table__, UserAddedSkill.__table__, UserSkill.__table__]
    for table in tables:
        table.create(engine, checkfirst=True)
    tag = uuid.uuid4().hex
    users = [f"{tag}-{i}" for i in range(3)]
    skill_id = tag
    with Session(engine) as session:
        assert session.expire_on_commit
        for user_id in users:
            session.add(
                User(
                    id=user_id,
                    username=user_id,
                    email=f"{user_id}@example.invalid",
                    hashed_password="local-unused",
                    is_active=True,
                )
            )
        session.commit()
        session.add(PublicSkill(id=skill_id, name="Counter", instructions="Local fixture"))
        session.commit()
    try:
        yield engine, users, skill_id
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM user_added_skill WHERE public_skill_id=:id"), {"id": skill_id})
            connection.execute(text("DELETE FROM public_skill WHERE id=:id"), {"id": skill_id})
            for user_id in users:
                connection.execute(text('DELETE FROM "user" WHERE id=:id'), {"id": user_id})
        assert engine.pool.checkedout() == 0
        engine.dispose()


def seed(storage, actors, *, active=True, count=None):
    engine, users, skill_id = storage
    with Session(engine) as session:
        ids = []
        for actor in actors:
            row = UserAddedSkill(user_id=users[actor], public_skill_id=skill_id, is_active=active)
            ids.append(row.id)
            session.add(row)
        skill = session.get(PublicSkill, skill_id)
        skill.add_count = len(actors) if count is None else count
        session.commit()
    return ids


def snapshot(storage):
    engine, _, skill_id = storage
    with Session(engine) as session:
        skill = session.get(PublicSkill, skill_id)
        rows = session.exec(select(UserAddedSkill).where(UserAddedSkill.public_skill_id == skill_id)).all()
        return skill.add_count, [(row.id, row.user_id, row.is_active) for row in rows]


def invoke(session, user, skill_id, action, link_ids=()):
    if action == "add":
        return asyncio.run(public_skills.add_skill_to_collection(skill_id, session, user))
    if action == "remove":
        return asyncio.run(public_skills.remove_skill_from_collection(skill_id, session, user))
    return asyncio.run(
        skills.batch_update_skills(skills.BatchUpdateRequest(skill_ids=list(link_ids), action=action), session, user)
    )


def race(storage, operations, *, same_actor=False):
    """Align actual reads, then hold first UPDATE until PG proves the other writer waits."""
    engine, users, skill_id = storage
    reads = threading.Barrier(2, timeout=30)
    counters = threading.Barrier(2, timeout=30)
    holding = threading.Event()
    release = threading.Event()
    guard = threading.Lock()
    pids = {}
    trace = []
    seen_reads = set()
    seen_counters = set()
    winner = []
    wait_receipt = None

    def before(connection, cursor, statement, parameters, context, executemany):
        actor = connection.info.get("counter_actor")
        if actor is None:
            return
        sql = statement.lower().strip()
        trace.append(
            {
                "actor": actor,
                "pid": pids.get(actor),
                "sql": statement,
                "parameters": dict(parameters),
                "frames": [
                    f"{f.filename}:{f.lineno}:{f.name}"
                    for f in traceback.extract_stack()
                    if f.filename.endswith(("public_skills.py", "skills.py"))
                ],
            }
        )
        if sql.startswith("update public_skill") and actor not in seen_counters:
            seen_counters.add(actor)
            if not same_actor:
                counters.wait()

    def after(connection, cursor, statement, parameters, context, executemany):
        actor = connection.info.get("counter_actor")
        if actor is None:
            return
        sql = statement.lower().strip()
        if sql.startswith("select") and "from user_added_skill" in sql and actor not in seen_reads:
            seen_reads.add(actor)
            reads.wait()
        if sql.startswith("update public_skill"):
            with guard:
                first = not winner
                if first:
                    winner.append(actor)
            if first:
                holding.set()
                if not release.wait(30):
                    raise AssertionError("test did not release real count UPDATE")

    def work(actor, operation):
        action, user_index, link_ids = operation
        with Session(engine) as session:
            assert session.expire_on_commit
            connection = session.connection()
            connection.info["counter_actor"] = actor
            pids[actor] = session.execute(text("select pg_backend_pid()")).scalar_one()
            try:
                user = session.get(User, users[user_index])
                result = invoke(session, user, skill_id, action, link_ids)
                return result.model_dump()
            finally:
                session.rollback()
                connection.info.pop("counter_actor", None) if not connection.closed else None
                # Pool connection info survives close; clear it via its retained dict below.

    # Clear connection-local observer tags at check-in, including committed Sessions.
    def checkin(dbapi_connection, connection_record):
        connection_record.info.pop("counter_actor", None)

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    event.listen(engine, "checkin", checkin)
    try:
        with ThreadPoolExecutor(max_workers=2) as workers:
            futures = [workers.submit(work, i, op) for i, op in enumerate(operations)]
            try:
                assert holding.wait(30), "no actual count UPDATE reached"
                deadline = time.monotonic() + 30
                with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as observer:
                    while time.monotonic() < deadline:
                        if len(pids) == 2:
                            loser = 1 - winner[0]
                            row = observer.execute(
                                text(
                                    "SELECT wait_event_type, wait_event, pg_blocking_pids(pid) "
                                    "FROM pg_stat_activity WHERE pid=:pid"
                                ),
                                {"pid": pids[loser]},
                            ).one()
                            if pids[winner[0]] in row[2]:
                                wait_receipt = {
                                    "winner": pids[winner[0]],
                                    "loser": pids[loser],
                                    "wait_type": row[0],
                                    "wait_event": row[1],
                                    "blockers": row[2],
                                }
                                break
                        release.wait(0.01)
                    assert wait_receipt is not None, "no real PG blocking relationship observed"
            finally:
                release.set()
                reads.abort()
                counters.abort()
            results = [future.result(timeout=30) for future in futures]
    finally:
        release.set()
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
        event.remove(engine, "checkin", checkin)
    receipt = {
        "operations": operations,
        "wait": wait_receipt,
        "sql": trace,
        "results": results,
        "stored": snapshot(storage),
    }
    if os.getenv("M08_COUNT_EVIDENCE"):
        path = Path(os.environ["M08_COUNT_EVIDENCE"]) / f"{os.environ['M08_COUNT_RUN']}-{uuid.uuid4().hex}-race.json"
        path.write_text(json.dumps(receipt, indent=2, default=str) + "\n")
    return results


@pytest.mark.parametrize(
    "left,right", [("add", "add"), ("remove", "remove"), ("delete", "delete"), ("add", "remove"), ("add", "delete")]
)
def test_distinct_collection_writers_keep_exact_link_count(storage, left, right):
    actors = [i for i, action in enumerate((left, right)) if action != "add"]
    ids = seed(storage, actors)
    by_actor = dict(zip(actors, ids, strict=True))
    operations = [(action, i, [by_actor[i]] if i in by_actor else []) for i, action in enumerate((left, right))]
    results = race(storage, operations)
    assert all(row["success"] for row in results)
    count, rows = snapshot(storage)
    expected = sum(action == "add" for action in (left, right))
    assert len(rows) == expected
    assert count == expected, (count, rows)


@pytest.mark.parametrize("action", ["add", "remove", "delete"])
def test_same_actor_duplicate_changes_count_once(storage, action):
    ids = seed(storage, [] if action == "add" else [0, 1, 2])
    operations = [(action, 0, ids[:1]), (action, 0, ids[:1])]
    results = race(storage, operations, same_actor=True)
    count, rows = snapshot(storage)
    expected = 1 if action == "add" else 2
    assert len(rows) == count == expected
    if action == "add":
        assert sorted(row["success"] for row in results) == [False, True]
        assert results[0]["added_skill_id"] == results[1]["added_skill_id"]


def test_inactive_reenable_disable_foreign_and_nonnegative_contract(storage):
    engine, users, skill_id = storage
    ids = seed(storage, [0], active=False)
    with Session(engine) as session:
        actor = session.get(User, users[0])
        result = invoke(session, actor, skill_id, "add")
        assert result.message == "Skill re-enabled successfully"
        assert snapshot(storage)[0] == 1
        invoke(session, actor, skill_id, "disable", ids)
        assert snapshot(storage) == (1, [(ids[0], users[0], False)])
        other = session.get(User, users[1])
        result = invoke(session, other, skill_id, "delete", ids)
        assert result.updated_count == 0
        with pytest.raises(APIException) as error:
            invoke(session, other, skill_id, "remove")
        assert error.value.status_code == 404
    seed(storage, [], count=0)
    with Session(engine) as session:
        invoke(session, session.get(User, users[0]), skill_id, "remove")
    assert snapshot(storage) == (0, [])


@pytest.mark.parametrize("action", ["add", "remove", "delete"])
def test_failed_commit_rolls_back_link_and_counter(storage, monkeypatch, action):
    engine, users, skill_id = storage
    ids = seed(storage, [] if action == "add" else [0])
    before = snapshot(storage)
    with Session(engine) as session:
        user = session.get(User, users[0])

        def fail_commit():
            session.flush()
            raise RuntimeError("owned injected precommit failure")

        monkeypatch.setattr(session, "commit", fail_commit)
        with pytest.raises(RuntimeError, match="owned injected"):
            invoke(session, user, skill_id, action, ids)
        session.rollback()
    assert snapshot(storage) == before
