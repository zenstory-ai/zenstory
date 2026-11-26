"""Real PostgreSQL integration tests for chat_message_archive cold storage and archival.

Covers:
1. ``FOR UPDATE SKIP LOCKED`` archival skipping locked hot rows without blocking, and
   interleaved hot/cold reads in ``load_loop_history`` & ``load_active_session_recent_messages``.
2. ``ON CONFLICT (id) DO UPDATE`` overwriting stale pre-existing cold rows with authoritative
   hot edits (feedback + tool-card undo), plus ``VACUUM ANALYZE`` on PostgreSQL.
3. Concurrent feedback submission, tool-card undo, and ``clear_session`` vs in-flight archival
   on real PostgreSQL (row locking, ``ChatMessageArchive`` ``after_update`` UX summary sync,
   and zero ghost resurrection).
4. Real PostgreSQL execution of ``_pg_projected_feedback_sql("chat_message_archive")`` through
   ``list_chat_feedback_votes`` (hot + cold merge, hot-wins dedup, no DataError fallback).
5. Real PostgreSQL Alembic ``20261126_000000`` migration: FK drop on ``chat_message_ux_summary``,
   partial feedback index creation, non-cascading hot deletes, non-empty downgrade refusal,
   and clean empty downgrade round-trip.

Each test suite runs inside its own disposable PostgreSQL schema and drops it on exit so it
never interferes with other serial PG regression files sharing ``ZENSTORY_TEST_POSTGRES_URL``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlmodel import Session, SQLModel, select

import models  # noqa: F401
from agent.loop.history import LOOP_ENGINE_METADATA_VALUE, load_loop_history
from api.chat import clear_session, load_active_session_recent_messages
from models import (
    ChatMessage,
    ChatMessageArchive,
    ChatMessageUxSummary,
    ChatSession,
    Project,
    User,
)
from services.chat_feedback_service import chat_feedback_service
from services.chat_message_archival import (
    archive_chat_messages_batch,
    run_gradual_archival,
)
from services.chat_tool_cards import record_edit_card_undone
from services.usage.admin_ux_metrics_service import list_chat_feedback_votes

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
    reason="explicitly owned PostgreSQL URL required",
)

SERVER_DIR = Path(__file__).resolve().parents[2]
ARCHIVE_REVISION = "20261126_000000"
PREV_REVISION = "20261124_180000"
PRE_SUMMARY_REVISION = "20261123_000000"

TABLES = [
    User.__table__,
    Project.__table__,
    ChatSession.__table__,
    ChatMessage.__table__,
    ChatMessageArchive.__table__,
    ChatMessageUxSummary.__table__,
]


def _schema_engine(base_url: str, schema_name: str):
    """Create a sync SQLAlchemy engine pinned to ``schema_name``."""
    return create_engine(
        base_url,
        connect_args={
            "options": f"-c search_path={schema_name} -c timezone=UTC -c statement_timeout=15000 -c lock_timeout=10000",
        },
        pool_pre_ping=True,
    )


@pytest.fixture(scope="module")
def archive_pg_engine():
    """Provision an isolated PostgreSQL schema with chat + archive + UX summary tables."""
    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    schema_name = f"zs_arch_pg_{uuid4().hex[:10]}"

    admin_engine = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema_name}"))

    engine = _schema_engine(url, schema_name)
    assert engine.dialect.name == "postgresql"
    SQLModel.metadata.create_all(engine, tables=TABLES)
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_chat_message_archive_assistant_feedback_created_at "
                "ON chat_message_archive (created_at) "
                "WHERE role = 'assistant' AND message_metadata IS NOT NULL "
                "AND strpos(message_metadata, '\"feedback\"') > 0"
            )
        )
    try:
        yield engine
    finally:
        engine.dispose()
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
        admin_engine.dispose()


def _seed_pg_session(engine) -> dict[str, str]:
    suffix = uuid4().hex[:8]
    with Session(engine) as session:
        user = User(
            username=f"arch-pg-{suffix}",
            email=f"arch-pg-{suffix}@example.test",
            hashed_password="unused",
            is_active=True,
            email_verified=True,
            is_superuser=False,
        )
        session.add(user)
        session.flush()
        project = Project(name=f"Archive PG {suffix}", owner_id=user.id, project_type="novel")
        session.add(project)
        session.flush()
        chat = ChatSession(user_id=user.id, project_id=project.id, is_active=True, message_count=0)
        session.add(chat)
        session.commit()
        return {"user_id": user.id, "project_id": project.id, "chat_id": chat.id}


def test_pg_for_update_skip_locked_and_interleaved_reads(archive_pg_engine) -> None:
    """Real PG FOR UPDATE SKIP LOCKED skips locked hot rows without blocking and reads stay ordered."""
    ids = _seed_pg_session(archive_pg_engine)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)
    t1 = cutoff - timedelta(days=5)
    t2 = t1 + timedelta(minutes=1)
    t3 = cutoff + timedelta(hours=1)

    m1_id = f"pg-skip-1-{uuid4().hex[:6]}"
    m2_id = f"pg-skip-2-{uuid4().hex[:6]}"
    m3_id = f"pg-skip-3-{uuid4().hex[:6]}"

    with Session(archive_pg_engine) as seed:
        seed.add_all(
            [
                ChatMessage(
                    id=m1_id,
                    session_id=ids["chat_id"],
                    role="assistant",
                    content="第一条被锁住的旧回复",
                    message_metadata=json.dumps(
                        {
                            "engine": LOOP_ENGINE_METADATA_VALUE,
                            "stop_reason": "end_turn",
                            "display_events": [{"type": "content", "content": "第一条被锁住的旧回复"}],
                        },
                        ensure_ascii=False,
                    ),
                    created_at=t1,
                ),
                ChatMessage(
                    id=m2_id,
                    session_id=ids["chat_id"],
                    role="assistant",
                    content="第二条未加锁的旧回复",
                    message_metadata=json.dumps(
                        {
                            "engine": LOOP_ENGINE_METADATA_VALUE,
                            "stop_reason": "end_turn",
                            "display_events": [{"type": "content", "content": "第二条未加锁的旧回复"}],
                        },
                        ensure_ascii=False,
                    ),
                    created_at=t2,
                ),
                ChatMessage(
                    id=m3_id,
                    session_id=ids["chat_id"],
                    role="user",
                    content="近七天内热消息",
                    created_at=t3,
                ),
            ]
        )
        seed.commit()

    # Connection A holds a row lock on m1 (simulating in-flight feedback/undo).
    with Session(archive_pg_engine) as lock_sess:
        locked_m1 = lock_sess.exec(
            select(ChatMessage).where(ChatMessage.id == m1_id).with_for_update()
        ).one()
        assert locked_m1.id == m1_id

        # Connection B runs archival: SKIP LOCKED must skip m1 and immediately archive m2.
        with Session(archive_pg_engine) as arch_sess:
            batch1 = archive_chat_messages_batch(
                arch_sess, cutoff=cutoff, batch_size=10, dry_run=False
            )
        assert batch1.scanned == 1
        assert batch1.archived == 1
        assert batch1.deleted == 1

        # While m1 (t1, hot) is transiently older than m2 (t2, cold), BOTH load_loop_history
        # and load_active_session_recent_messages must preserve strict chronological order [m1, m2, m3]!
        with Session(archive_pg_engine) as read_sess:
            assert read_sess.get(ChatMessage, m1_id) is not None
            assert read_sess.get(ChatMessage, m2_id) is None
            assert read_sess.get(ChatMessageArchive, m2_id) is not None

            history = load_loop_history(
                read_sess,
                project_id=ids["project_id"],
                user_id=ids["user_id"],
                chat_session_id=ids["chat_id"],
            )
            assert [item["content"] for item in history.items] == [
                "第一条被锁住的旧回复",
                "第二条未加锁的旧回复",
                "近七天内热消息",
            ]

            # M3 check with limit=2: hot has [m3(t3), m1(t1)], cold has [m2(t2)] -> must return [m2, m3]!
            _, recent_2, has_more_2 = load_active_session_recent_messages(
                read_sess,
                project_id=ids["project_id"],
                user_id=ids["user_id"],
                limit=2,
            )
            assert [m.id for m in recent_2] == [m2_id, m3_id]
            assert has_more_2 is True

        # Connection A updates m1 with feedback and commits, releasing the lock.
        meta = json.loads(locked_m1.message_metadata or "{}")
        meta["feedback"] = {"vote": "up", "preset": None, "comment": "锁内反馈"}
        locked_m1.message_metadata = json.dumps(meta, ensure_ascii=False)
        lock_sess.add(locked_m1)
        lock_sess.commit()

    # Next archival batch picks up m1 and preserves the committed feedback in chat_message_archive.
    with Session(archive_pg_engine) as arch_sess2:
        batch2 = archive_chat_messages_batch(
            arch_sess2, cutoff=cutoff, batch_size=10, dry_run=False
        )
    assert batch2.scanned == 1
    assert batch2.deleted == 1

    with Session(archive_pg_engine) as check_sess:
        archived_m1 = check_sess.get(ChatMessageArchive, m1_id)
        assert archived_m1 is not None
        assert json.loads(archived_m1.message_metadata or "{}")["feedback"]["comment"] == "锁内反馈"


def test_pg_on_conflict_do_update_preserves_newer_hot_edits_and_vacuum_analyze(
    archive_pg_engine,
) -> None:
    """Real PG ON CONFLICT (id) DO UPDATE overwrites stale archive row + VACUUM ANALYZE succeeds."""
    ids = _seed_pg_session(archive_pg_engine)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)
    old_ts = cutoff - timedelta(days=4)
    msg_id = f"pg-m4-{uuid4().hex[:6]}"

    with Session(archive_pg_engine) as seed:
        seed.add(
            ChatMessage(
                id=msg_id,
                session_id=ids["chat_id"],
                role="assistant",
                content="热表最新内容",
                tool_calls=json.dumps(
                    [
                        {
                            "id": "call-m4",
                            "name": "edit_file",
                            "status": "success",
                            "result": {
                                "id": "f1",
                                "undo": {"before_version_number": 1, "undone": True},
                            },
                        }
                    ],
                    ensure_ascii=False,
                ),
                message_metadata=json.dumps(
                    {
                        "stop_reason": "end_turn",
                        "feedback": {"vote": "down", "preset": "off_topic", "comment": "PG新反馈"},
                    },
                    ensure_ascii=False,
                ),
                created_at=old_ts,
            )
        )
        seed.add(
            ChatMessageArchive(
                id=msg_id,
                session_id=ids["chat_id"],
                role="assistant",
                content="陈旧冷副本",
                tool_calls=json.dumps(
                    [
                        {
                            "id": "call-m4",
                            "name": "edit_file",
                            "status": "success",
                            "result": {
                                "id": "f1",
                                "undo": {"before_version_number": 1, "undone": False},
                            },
                        }
                    ],
                    ensure_ascii=False,
                ),
                message_metadata=json.dumps({"stop_reason": "end_turn"}, ensure_ascii=False),
                created_at=old_ts,
                archived_at=cutoff,
            )
        )
        seed.commit()

    report = run_gradual_archival(
        lambda: Session(archive_pg_engine),
        cutoff=cutoff,
        batch_size=10,
        max_batches=5,
        sleep_ms=0,
        dry_run=False,
        vacuum_analyze=True,
    )
    assert report.aborted is False
    assert report.total_archived == 1
    assert report.total_deleted == 1
    assert report.vacuum_analyzed is True

    with Session(archive_pg_engine) as verify:
        assert verify.get(ChatMessage, msg_id) is None
        cold = verify.get(ChatMessageArchive, msg_id)
        assert cold is not None
        assert cold.content == "热表最新内容"
        assert json.loads(cold.message_metadata or "{}")["feedback"]["comment"] == "PG新反馈"
        assert json.loads(cold.tool_calls or "[]")[0]["result"]["undo"]["undone"] is True


def test_pg_concurrent_feedback_undo_and_clear_session_with_archival(
    archive_pg_engine,
) -> None:
    """Real PG feedback, tool-card undo on cold rows, and concurrent clear_session vs archival."""
    ids = _seed_pg_session(archive_pg_engine)
    cutoff = datetime(2026, 11, 19, 0, 0, 0)
    old_ts = cutoff - timedelta(days=5)
    token = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)

    cold_card_id = f"pg-card-{uuid4().hex[:6]}"
    race_hot_id = f"pg-race-{uuid4().hex[:6]}"

    with Session(archive_pg_engine) as seed:
        seed.add(
            ChatMessage(
                id=cold_card_id,
                session_id=ids["chat_id"],
                role="assistant",
                content="包含修改卡的旧回复",
                tool_calls=json.dumps(
                    [
                        {
                            "id": "call-pg-edit",
                            "name": "edit_file",
                            "arguments": {"id": "file-pg-1"},
                            "status": "success",
                            "result": {
                                "id": "file-pg-1",
                                "undo": {
                                    "before_version_number": 1,
                                    "expected_after_updated_at": token.isoformat(),
                                },
                            },
                        }
                    ],
                    ensure_ascii=False,
                ),
                message_metadata=json.dumps({"stop_reason": "end_turn"}, ensure_ascii=False),
                created_at=old_ts,
            )
        )
        seed.commit()

    # Archive cold_card_id first.
    with Session(archive_pg_engine) as arch_sess:
        res = archive_chat_messages_batch(arch_sess, cutoff=cutoff, batch_size=10, dry_run=False)
        assert res.deleted == 1

    # 1) Feedback + undo concurrently on the archived row on real PG.
    fb_errors: list[BaseException] = []
    undo_errors: list[BaseException] = []
    undo_results: list[bool] = []
    start_barrier = threading.Barrier(2)

    def _run_feedback() -> None:
        try:
            with Session(archive_pg_engine) as s:
                user = s.get(User, ids["user_id"])
                assert user is not None
                start_barrier.wait(timeout=5)
                chat_feedback_service.submit_feedback(
                    session=s,
                    current_user=user,
                    message_id=cold_card_id,
                    vote="down",
                    preset="off_topic",
                    comment="冷表并发反馈",
                )
        except BaseException as exc:  # noqa: BLE001
            fb_errors.append(exc)

    def _run_undo() -> None:
        try:
            with Session(archive_pg_engine) as s:
                start_barrier.wait(timeout=5)
                ok = record_edit_card_undone(
                    s,
                    user_id=ids["user_id"],
                    project_id=ids["project_id"],
                    file_id="file-pg-1",
                    message_id=cold_card_id,
                    tool_use_id="call-pg-edit",
                    before_version_number=1,
                    expected_after_updated_at=token,
                )
                undo_results.append(ok)
        except BaseException as exc:  # noqa: BLE001
            undo_errors.append(exc)

    t_fb = threading.Thread(target=_run_feedback)
    t_undo = threading.Thread(target=_run_undo)
    t_fb.start()
    t_undo.start()
    t_fb.join(timeout=15)
    t_undo.join(timeout=15)

    assert fb_errors == []
    assert undo_errors == []
    assert undo_results == [True]

    with Session(archive_pg_engine) as verify:
        archived = verify.get(ChatMessageArchive, cold_card_id)
        assert archived is not None
        assert json.loads(archived.message_metadata or "{}")["feedback"]["vote"] == "down"
        assert json.loads(archived.tool_calls or "[]")[0]["result"]["undo"]["undone"] is True
        summary = verify.get(ChatMessageUxSummary, cold_card_id)
        assert summary is not None
        assert summary.feedback_vote == "down"
        assert summary.ai_edit_undone == 1

    # 2) Concurrent clear_session vs in-flight archival holding lock on a hot row -> ZERO ghosts!
    with Session(archive_pg_engine) as seed:
        seed.add(
            ChatMessage(
                id=race_hot_id,
                session_id=ids["chat_id"],
                role="assistant",
                content="待归档与清空竞态消息",
                message_metadata=json.dumps({"stop_reason": "end_turn"}, ensure_ascii=False),
                created_at=old_ts + timedelta(minutes=2),
            )
        )
        seed.commit()

    clear_errors: list[BaseException] = []
    clear_started = threading.Event()

    def _run_clear() -> None:
        try:
            with Session(archive_pg_engine) as s:
                user = s.get(User, ids["user_id"])
                assert user is not None
                clear_started.set()
                clear_session(project_id=ids["project_id"], session=s, current_user=user)
        except BaseException as exc:  # noqa: BLE001
            clear_errors.append(exc)

    # Simulate an in-flight archival transaction that has locked race_hot_id, copied it to
    # chat_message_archive, and is about to delete from chat_message + commit when clear_session arrives.
    with Session(archive_pg_engine) as arch_tx:
        locked = arch_tx.exec(
            select(ChatMessage).where(ChatMessage.id == race_hot_id).with_for_update()
        ).one()
        arch_tx.add(
            ChatMessageArchive(
                id=locked.id,
                session_id=locked.session_id,
                role=locked.role,
                content=locked.content,
                message_metadata=locked.message_metadata,
                created_at=locked.created_at,
                archived_at=cutoff,
            )
        )
        arch_tx.flush()

        t_clear = threading.Thread(target=_run_clear)
        t_clear.start()
        assert clear_started.wait(timeout=5)
        # Give clear_session a brief moment to block on the hot chat_message lock,
        # then finish archival delete + commit. Because clear_session deletes hot BEFORE
        # cold, it cannot sweep cold until archival has already committed race_hot_id into cold!
        arch_tx.delete(locked)
        arch_tx.commit()

    t_clear.join(timeout=15)
    assert clear_errors == []

    with Session(archive_pg_engine) as check:
        assert check.exec(select(ChatMessage).where(ChatMessage.session_id == ids["chat_id"])).all() == []
        assert (
            check.exec(
                select(ChatMessageArchive).where(ChatMessageArchive.session_id == ids["chat_id"])
            ).all()
            == []
        )
        assert (
            check.exec(
                select(ChatMessageUxSummary).where(ChatMessageUxSummary.session_id == ids["chat_id"])
            ).all()
            == []
        )


def test_pg_projected_feedback_sql_on_chat_message_archive(archive_pg_engine) -> None:
    """Real PG list_chat_feedback_votes executes _pg_projected_feedback_sql('chat_message_archive')."""
    ids = _seed_pg_session(archive_pg_engine)
    now_utc = datetime(2026, 11, 26, 8, 0, tzinfo=UTC)
    now_naive = now_utc.replace(tzinfo=None)
    old_ts = now_naive - timedelta(days=14)
    recent_ts = now_naive - timedelta(days=2)

    cold_vote_id = f"pg-proj-cold-{uuid4().hex[:6]}"
    hot_vote_id = f"pg-proj-hot-{uuid4().hex[:6]}"

    with Session(archive_pg_engine) as seed:
        seed.add_all(
            [
                ChatMessageArchive(
                    id=cold_vote_id,
                    session_id=ids["chat_id"],
                    role="assistant",
                    content="十四天前的归档回复",
                    message_metadata=json.dumps(
                        {
                            "stop_reason": "end_turn",
                            "feedback": {
                                "vote": "down",
                                "preset": "off_topic",
                                "comment": "冷表PG投影点踩",
                                "created_at": old_ts.isoformat() + "+00:00",
                                "updated_at": old_ts.isoformat() + "+00:00",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    created_at=old_ts,
                    archived_at=now_naive - timedelta(days=5),
                ),
                ChatMessage(
                    id=hot_vote_id,
                    session_id=ids["chat_id"],
                    role="assistant",
                    content="两天前的热表回复",
                    message_metadata=json.dumps(
                        {
                            "stop_reason": "end_turn",
                            "feedback": {
                                "vote": "up",
                                "preset": None,
                                "comment": "热表PG投影点赞",
                                "created_at": recent_ts.isoformat() + "+00:00",
                                "updated_at": recent_ts.isoformat() + "+00:00",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    created_at=recent_ts,
                ),
                # Duplicate ID in cold with stale vote: hot MUST win!
                ChatMessageArchive(
                    id=hot_vote_id,
                    session_id=ids["chat_id"],
                    role="assistant",
                    content="两天前的热表回复",
                    message_metadata=json.dumps(
                        {
                            "stop_reason": "end_turn",
                            "feedback": {
                                "vote": "down",
                                "preset": "stale_preset",
                                "comment": "冷表陈旧副本",
                                "created_at": recent_ts.isoformat() + "+00:00",
                                "updated_at": recent_ts.isoformat() + "+00:00",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    created_at=recent_ts,
                    archived_at=now_naive,
                ),
            ]
        )
        seed.commit()

    executed_sql: list[str] = []

    def _capture(_conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        executed_sql.append(statement)

    event.listen(archive_pg_engine, "before_cursor_execute", _capture)
    try:
        with Session(archive_pg_engine) as read_sess:
            votes = list_chat_feedback_votes(read_sess, days=30, now=now_utc)
    finally:
        event.remove(archive_pg_engine, "before_cursor_execute", _capture)

    # Confirm the real PG JSON projection ran on chat_message_archive (without DataError fallback).
    assert any(
        "(chat_message_archive.message_metadata::json)->'feedback'" in sql
        for sql in executed_sql
    )
    assert not any(
        "chat_message_archive.message_metadata," in sql
        and "::json" not in sql
        for sql in executed_sql
    )
    by_id = {item["message_id"]: item for item in votes["items"]}
    assert cold_vote_id in by_id
    assert by_id[cold_vote_id]["vote"] == "down"
    assert by_id[cold_vote_id]["comment"] == "冷表PG投影点踩"
    assert hot_vote_id in by_id
    assert by_id[hot_vote_id]["vote"] == "up"
    assert by_id[hot_vote_id]["comment"] == "热表PG投影点赞"


def _alembic_executable() -> str:
    candidate = Path(sys.executable).parent / "alembic"
    if candidate.exists():
        return str(candidate)
    found = shutil.which("alembic")
    assert found, "alembic CLI not found"
    return found


def test_pg_alembic_fk_drop_partial_index_and_downgrade_safety() -> None:
    """Real PG Alembic 20261126_000000 migration drops UX FK, creates partial index, and guards downgrade."""
    base_url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    schema_name = f"zs_arch_mig_{uuid4().hex[:10]}"

    admin_engine = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin_engine.connect() as conn:
        conn.execute(text(f"CREATE SCHEMA {schema_name}"))

    def _run_alembic(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            [_alembic_executable(), *args],
            cwd=SERVER_DIR,
            env={
                **os.environ,
                "DATABASE_URL": base_url,
                "PGOPTIONS": f"-c search_path={schema_name}",
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
        if check:
            assert completed.returncode == 0, completed.stdout + completed.stderr
        return completed

    try:
        # Seed baseline tables inside the isolated PG schema.
        seed_engine = _schema_engine(base_url, schema_name)
        try:
            SQLModel.metadata.create_all(seed_engine)
        finally:
            seed_engine.dispose()

        # Rewind before 20261124_000000 and upgrade to PREV_REVISION so chat_message_ux_summary
        # has the real PostgreSQL CASCADE FK created by 20261124_000000.
        _run_alembic("stamp", "head")
        _run_alembic("downgrade", PRE_SUMMARY_REVISION)
        _run_alembic("upgrade", PREV_REVISION)

        mig_engine = _schema_engine(base_url, schema_name)
        try:
            insp = inspect(mig_engine)
            assert not insp.has_table("chat_message_archive")
            pre_fks = insp.get_foreign_keys("chat_message_ux_summary")
            assert any(fk.get("referred_table") == "chat_message" for fk in pre_fks)

            now = datetime(2026, 11, 20, 12, 0, 0)
            with mig_engine.begin() as conn:
                conn.execute(
                    text(
                        'INSERT INTO "user" (id, username, email, email_verified, hashed_password, is_active, is_superuser, created_at, updated_at) '
                        "VALUES ('u-pg-mig', 'pg_mig_user', 'pgmig@example.test', true, 'hash', true, false, :now, :now)"
                    ),
                    {"now": now},
                )
                conn.execute(
                    text(
                        "INSERT INTO project (id, name, owner_id, created_at, updated_at, is_deleted, project_type) "
                        "VALUES ('p-pg-mig', 'PG Mig Project', 'u-pg-mig', :now, :now, false, 'novel')"
                    ),
                    {"now": now},
                )
                conn.execute(
                    text(
                        "INSERT INTO chat_session (id, user_id, project_id, title, is_active, message_count, created_at, updated_at) "
                        "VALUES ('s-pg-mig', 'u-pg-mig', 'p-pg-mig', 'PG Mig Session', true, 1, :now, :now)"
                    ),
                    {"now": now},
                )
                conn.execute(
                    text(
                        "INSERT INTO chat_message (id, session_id, role, content, created_at) "
                        "VALUES ('m-pg-mig', 's-pg-mig', 'assistant', 'hello pg', :now)"
                    ),
                    {"now": now},
                )
                conn.execute(
                    text(
                        "INSERT INTO chat_message_ux_summary "
                        "(message_id, session_id, created_at, round_category, ai_edit_cards, ai_edit_undone, ai_delete_cards, ai_delete_restored) "
                        "VALUES ('m-pg-mig', 's-pg-mig', :now, 'healthy', 1, 0, 0, 0)"
                    ),
                    {"now": now},
                )
        finally:
            mig_engine.dispose()

        # Upgrade to 20261126_000000 on real PG.
        _run_alembic("upgrade", ARCHIVE_REVISION)

        mig_engine = _schema_engine(base_url, schema_name)
        try:
            insp = inspect(mig_engine)
            assert insp.has_table("chat_message_archive")
            assert insp.get_foreign_keys("chat_message_ux_summary") == []
            archive_indexes = {idx["name"] for idx in insp.get_indexes("chat_message_archive")}
            assert "ix_chat_message_archive_session_created_at_id" in archive_indexes
            assert "ix_chat_message_archive_archived_at" in archive_indexes
            assert "ix_chat_message_archive_assistant_feedback_created_at" in archive_indexes

            # Deleting hot chat_message MUST NOT cascade-delete chat_message_ux_summary on real PG!
            with mig_engine.begin() as conn:
                conn.execute(text("DELETE FROM chat_message WHERE id = 'm-pg-mig'"))
                surviving = conn.execute(
                    text("SELECT COUNT(*) FROM chat_message_ux_summary WHERE message_id = 'm-pg-mig'")
                ).scalar_one()
                assert surviving == 1

                # Insert an archived row so downgrade MUST refuse.
                conn.execute(
                    text(
                        "INSERT INTO chat_message_archive "
                        "(id, session_id, role, content, created_at, archived_at) "
                        "VALUES ('m-pg-cold', 's-pg-mig', 'assistant', 'archived pg', :now, :now)"
                    ),
                    {"now": now},
                )
        finally:
            mig_engine.dispose()

        blocked = _run_alembic("downgrade", PREV_REVISION, check=False)
        assert blocked.returncode != 0
        assert "Refusing to drop non-empty chat_message_archive" in (blocked.stdout + blocked.stderr)

        # Empty archive table -> downgrade succeeds cleanly (even with orphan UX summary row).
        mig_engine = _schema_engine(base_url, schema_name)
        try:
            with mig_engine.begin() as conn:
                conn.execute(text("DELETE FROM chat_message_archive"))
        finally:
            mig_engine.dispose()

        _run_alembic("downgrade", PREV_REVISION)

        mig_engine = _schema_engine(base_url, schema_name)
        try:
            insp = inspect(mig_engine)
            assert not insp.has_table("chat_message_archive")
            # Orphan summary row kept FK restoration skipped safely.
            assert insp.get_foreign_keys("chat_message_ux_summary") == []
        finally:
            mig_engine.dispose()

        # Re-upgrade cleanly back to head (idempotency check).
        _run_alembic("upgrade", ARCHIVE_REVISION)
    finally:
        with admin_engine.connect() as conn:
            conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
        admin_engine.dispose()
