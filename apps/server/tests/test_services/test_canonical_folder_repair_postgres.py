"""Real PostgreSQL canonical recovery row locks and transaction ownership."""

import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import text
from sqlmodel import Session, select

from agent.tools.file_ops import crud as crud_module
from agent.tools.file_ops.crud import FileCRUD
from api import files as files_api
from config.datetime_utils import normalize_datetime_to_utc
from models import File, Project, User
from models.file_version import FileVersion
from services.features.file_version_service import FileVersionService, get_file_version_service
from services.file_tree_rules import lock_project_for_files
from tests.test_services.test_snapshot_concurrency_postgres import pg_engine as pg_engine

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="isolated PostgreSQL URL not configured",
)
STAMP = datetime(2026, 10, 6, 12, 0, 0, 123456, tzinfo=UTC)
KINDS = ["web-material", "web-draft", "web-script", "tool-material", "tool-draft", "tool-script"]


@pytest.fixture(autouse=True)
def isolate_dependencies(monkeypatch):
    import database

    monkeypatch.setattr(database, "is_postgres", True)
    # 普通写入现在总会走版本（合并或新建），额度检查要查 user_subscription；
    # 这里的 PG schema 不建订阅表，和 test_snapshot_concurrency_postgres 一样把额度桩掉。
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    monkeypatch.setattr(files_api, "utcnow", lambda: STAMP)
    monkeypatch.setattr(crud_module, "utcnow", lambda: STAMP)
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_a, **_kw: None)
    monkeypatch.setattr(files_api.activation_event_service, "record_once", lambda *_a, **_kw: None)


def _seed(engine, kind, suffix):
    folder_kind = kind.split("-")[1]
    with Session(engine) as session:
        user = User(username=f"canonical-pg-{suffix}", email=f"canonical-pg-{suffix}@example.test", hashed_password="unused")
        session.add(user)
        session.flush()
        project = Project(name="Canonical PG", owner_id=user.id, project_type="screenplay" if folder_kind == "script" else "novel")
        session.add(project)
        session.flush()
        folder = File(
            id=f"{project.id}-{folder_kind}-folder", project_id=project.id,
            title="Preserved root", file_type="folder", content="Initial folder body", order=71,
            updated_at=STAMP, is_deleted=True, deleted_at=STAMP - timedelta(days=1),
        )
        other = File(project_id=project.id, title="Independent content", file_type="draft", content="Old body", updated_at=STAMP)
        session.add_all([folder, other])
        session.commit()
        return user.id, project.id, folder.id, other.id


def _helper(session, project_id, kind, *, commit=False):
    helper = files_api._ensure_material_folder if kind.endswith("material") else files_api._ensure_draft_folder
    return helper(session, project_id, commit=commit)


def _recover_and_create(session, user_id, project_id, folder_id, kind, title):
    if kind.startswith("tool"):
        parent = f"{project_id}-draft-folder" if kind.endswith("script") else folder_id
        return FileCRUD(session, user_id).create_file(project_id, title, "draft", parent_id=parent)["id"]
    lock_project_for_files(session, project_id)
    folder = _helper(session, project_id, kind)
    child = File(project_id=project_id, parent_id=folder.id, title=title, file_type="draft", content="Strict initial body")
    session.add(child)
    session.flush()
    get_file_version_service().create_initial_version(session, child)
    session.commit()
    return child.id


def _observed_lock_wait(engine, pid: int, future: Future) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if future.done():
            return False
        with engine.connect() as connection:
            waiting = connection.execute(
                text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"), {"pid": pid},
            ).scalar()
        if waiting:
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("kind", KINDS)
def test_same_root_repair_waits_and_rereads_without_double_bump(pg_engine, monkeypatch, kind):
    user_id, project_id, folder_id, other_id = _seed(pg_engine, kind, f"same-{kind}")
    first_changed, release, second_cached, start_second, second_started = (Event() for _ in range(5))
    first_sessions, second_pid = [], []
    original_flush = Session.flush

    def pause_first_before_flush(session, *args, **kwargs):
        if first_sessions and session is first_sessions[0] and not first_changed.is_set():
            if any(isinstance(row, File) and row.id == folder_id and not row.is_deleted for row in session.dirty):
                first_changed.set()
                assert release.wait(8), "first recovery not released"
        return original_flush(session, *args, **kwargs)

    monkeypatch.setattr(Session, "flush", pause_first_before_flush)

    def first():
        with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
            first_sessions.append(session)
            return _recover_and_create(session, user_id, project_id, folder_id, kind, "First child")

    def second():
        with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
            stale = session.get(File, folder_id)  # Retained identity before the first recovery.
            assert stale.is_deleted
            second_pid.append(session.exec(text("SELECT pg_backend_pid()")).one()[0])
            second_cached.set()
            assert start_second.wait(8)
            second_started.set()
            result = _recover_and_create(session, user_id, project_id, folder_id, kind, "Second child")
            assert stale.is_deleted is False
            return result

    with ThreadPoolExecutor(max_workers=2) as pool:
        waiting = pool.submit(second)
        assert second_cached.wait(3)
        leader = pool.submit(first)
        try:
            assert first_changed.wait(3)
            start_second.set()
            assert second_started.wait(3)
            assert _observed_lock_wait(pg_engine, second_pid[0], waiting), "second repair bypassed the fresh File row lock"
            # A real same-project ordinary writer must complete while recovery is paused.
            with Session(pg_engine) as content_writer:
                files_api.update_file(
                    other_id, files_api.FileUpdate(content="Independent updated body"),
                    BackgroundTasks(), current_user=content_writer.get(User, user_id), session=content_writer,
                )
        finally:
            start_second.set()
            release.set()
        child_ids = [leader.result(timeout=8), waiting.result(timeout=8)]
    with Session(pg_engine) as reader:
        folder = reader.get(File, folder_id)
        assert folder.is_deleted is False and folder.deleted_at is None
        assert normalize_datetime_to_utc(folder.updated_at) == STAMP + timedelta(microseconds=1)
        assert (folder.content, folder.title, folder.order) == ("Initial folder body", "Preserved root", 71)
        assert len(set(child_ids)) == 2
        assert all(reader.get(File, child_id).parent_id == folder_id for child_id in child_ids)
        assert reader.get(File, other_id).content == "Independent updated body"
        if kind.startswith("web"):
            versions = reader.exec(select(FileVersion).where(FileVersion.file_id.in_(child_ids))).all()
            assert len(versions) == 2
            assert all(version.content == "Strict initial body" and version.is_base_version for version in versions)


@pytest.mark.parametrize("kind", KINDS)
def test_repair_uses_token_after_same_row_content_writer_commits(pg_engine, kind):
    user_id, project_id, folder_id, _ = _seed(pg_engine, kind, f"content-{kind}")
    ready, begin_repair = Event(), Event()
    waiter_pid = []
    future_token = STAMP + timedelta(days=365)

    def recover():
        with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
            stale = session.get(File, folder_id)
            assert normalize_datetime_to_utc(stale.updated_at) == STAMP
            waiter_pid.append(session.exec(text("SELECT pg_backend_pid()")).one()[0])
            ready.set()
            assert begin_repair.wait(8)
            return _recover_and_create(session, user_id, project_id, folder_id, kind, "Recovered child")

    with Session(pg_engine) as writer, ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(recover)
        assert ready.wait(3)
        row = writer.exec(select(File).where(File.id == folder_id).with_for_update(key_share=True)).one()
        row.content = "Committed later content"
        row.title = "Committed later title"
        row.updated_at = future_token
        writer.add(row)
        writer.flush()
        try:
            begin_repair.set()
            assert _observed_lock_wait(pg_engine, waiter_pid[0], future)
        finally:
            writer.commit()
            begin_repair.set()
        future.result(timeout=8)
    with Session(pg_engine) as reader:
        row = reader.get(File, folder_id)
        assert row.content == "Committed later content"
        assert row.title == "Committed later title"
        assert row.is_deleted is False and row.deleted_at is None
        assert normalize_datetime_to_utc(row.updated_at) == future_token + timedelta(microseconds=1)


@pytest.mark.parametrize("kind", ["web-material", "web-draft", "web-script"])
def test_helper_owned_recovery_commits_and_releases_locks_with_session_still_open(pg_engine, kind):
    _, project_id, folder_id, _ = _seed(pg_engine, kind, f"owned-{kind}")
    with Session(pg_engine, expire_on_commit=False) as helper_session:
        restored = _helper(helper_session, project_id, kind, commit=True)
        assert restored.is_deleted is False
        with Session(pg_engine) as observer:
            # No row or Project SHARE gate may remain after helper-owned commit.
            row = observer.exec(select(File).where(File.id == folder_id).with_for_update(nowait=True)).one()
            observer.exec(select(Project).where(Project.id == project_id).with_for_update(key_share=True, nowait=True)).one()
            assert row.is_deleted is False and row.deleted_at is None
            assert normalize_datetime_to_utc(row.updated_at) == STAMP + timedelta(microseconds=1)


@pytest.mark.parametrize("kind", ["web-material", "web-draft", "web-script"])
def test_helper_owned_waiter_commits_even_when_fresh_row_is_already_live(pg_engine, monkeypatch, kind):
    _, project_id, folder_id, _ = _seed(pg_engine, kind, f"owned-waiter-{kind}")
    cached, start = Event(), Event()
    waiter_pid = []
    original_get = Session.get

    with Session(pg_engine) as blocker:
        blocker.exec(select(File).where(File.id == folder_id).with_for_update(key_share=True)).one()

        def pause_initial_get(session, entity, ident, *args, **kwargs):
            result = original_get(session, entity, ident, *args, **kwargs)
            if entity is File and ident == folder_id and not cached.is_set():
                cached.set()
                assert start.wait(8)
            return result

        monkeypatch.setattr(Session, "get", pause_initial_get)
        # Keep the helper session open after it returns, to test lock release.
        helper_session = Session(pg_engine, autoflush=False, expire_on_commit=False)
        try:
            waiter_pid.append(helper_session.exec(text("SELECT pg_backend_pid()")).one()[0])
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(_helper, helper_session, project_id, kind, commit=True)
                try:
                    assert cached.wait(3)
                    start.set()
                    assert _observed_lock_wait(pg_engine, waiter_pid[0], future)
                    blocker.exec(text(
                        'UPDATE file SET is_deleted = false, deleted_at = NULL, updated_at = :stamp WHERE id = :id'
                    ), params={"stamp": STAMP + timedelta(microseconds=1), "id": folder_id})
                finally:
                    blocker.commit()
                    start.set()
                result = future.result(timeout=8)
                assert normalize_datetime_to_utc(result.updated_at) == STAMP + timedelta(microseconds=1)
            with Session(pg_engine) as observer:
                observer.exec(select(File).where(File.id == folder_id).with_for_update(nowait=True)).one()
                observer.exec(select(Project).where(Project.id == project_id).with_for_update(key_share=True, nowait=True)).one()
        finally:
            helper_session.close()


@pytest.mark.parametrize("kind", ["web-material", "web-draft", "web-script"])
def test_caller_owned_recovery_rolls_back_with_strict_child_baseline(pg_engine, monkeypatch, kind):
    _, project_id, folder_id, _ = _seed(pg_engine, kind, f"rollback-{kind}")
    failure = RuntimeError("strict child baseline unavailable")
    service = get_file_version_service()
    monkeypatch.setattr(type(service), "create_version", lambda *_a, **_kw: (_ for _ in ()).throw(failure))
    with Session(pg_engine) as caller:
        lock_project_for_files(caller, project_id)
        folder = _helper(caller, project_id, kind, commit=False)
        child = File(project_id=project_id, parent_id=folder.id, title="Failed child", content="Body", file_type="draft")
        caller.add(child)
        caller.flush()
        child_id = child.id
        with pytest.raises(RuntimeError, match="strict child baseline unavailable"):
            service.create_initial_version(caller, child)
        caller.rollback()
        with Session(pg_engine) as observer:
            root = observer.exec(select(File).where(File.id == folder_id).with_for_update(nowait=True)).one()
            observer.exec(select(Project).where(Project.id == project_id).with_for_update(key_share=True, nowait=True)).one()
            assert root.is_deleted is True and root.deleted_at is not None
            assert normalize_datetime_to_utc(root.updated_at) == STAMP
            assert observer.get(File, child_id) is None
            assert observer.exec(select(FileVersion).where(FileVersion.file_id == child_id)).all() == []


@pytest.mark.parametrize("kind", ["web-material", "web-draft"])
def test_helper_owned_missing_folder_unique_collision_releases_project_gate(pg_engine, kind):
    from threading import get_ident

    from sqlalchemy import event

    _, project_id, folder_id, _ = _seed(pg_engine, kind, f"missing-collision-{kind}")
    with Session(pg_engine) as seed_cleanup:
        seed_cleanup.delete(seed_cleanup.get(File, folder_id))
        seed_cleanup.commit()
    first_insert, release = Event(), Event()
    first_thread = []
    sql = []
    helper_session = Session(pg_engine, autoflush=False, expire_on_commit=False)

    def before_sql(_conn, _cursor, statement, _params, _context, _many):
        if first_thread and get_ident() == first_thread[0]:
            sql.append(statement.lower())
            if statement.lower().startswith("insert into file") and not first_insert.is_set():
                first_insert.set()
                assert release.wait(8)

    event.listen(pg_engine, "before_cursor_execute", before_sql)
    try:
        def first():
            first_thread.append(get_ident())
            return _helper(helper_session, project_id, kind, commit=True)

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(first)
            try:
                assert first_insert.wait(3)
                with Session(pg_engine) as winner_session:
                    winner = _helper(winner_session, project_id, kind, commit=True)
                    winning_token = normalize_datetime_to_utc(winner.updated_at)
            finally:
                release.set()
            restored = future.result(timeout=8)
        assert restored.id == folder_id
        assert restored.is_deleted is False and restored.deleted_at is None
        assert normalize_datetime_to_utc(restored.updated_at) == winning_token
        first_gate = next(index for index, query in enumerate(sql) if "from project" in query and "for share" in query)
        first_write = next(index for index, query in enumerate(sql) if query.startswith("insert into file"))
        assert first_gate < first_write
        with Session(pg_engine) as observer:
            observer.exec(select(File).where(File.id == folder_id).with_for_update(nowait=True)).one()
            observer.exec(select(Project).where(Project.id == project_id).with_for_update(key_share=True, nowait=True)).one()
            assert len(observer.exec(select(File).where(File.id == folder_id)).all()) == 1
    finally:
        event.remove(pg_engine, "before_cursor_execute", before_sql)
        helper_session.close()
