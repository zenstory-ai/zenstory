"""Real PostgreSQL tree-writer interleavings; no provider or production calls."""

import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Event

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import event, text
from sqlmodel import Session, select

from agent.tools.file_ops import crud as crud_module
from agent.tools.file_ops.crud import FileCRUD
from api import agent_api
from api import files as files_api
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import File, Project, User
from models.agent_api_key import AgentApiKey
from models.file_version import FileVersion
from services.features.file_version_service import FileVersionService
from tests.test_services.test_snapshot_concurrency_postgres import pg_engine as pg_engine

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="isolated PostgreSQL URL not configured"
)
STAMP = datetime(2026, 10, 6, 8, 0, 0, 123456)


@pytest.fixture(autouse=True)
def isolate_side_effects(monkeypatch):
    import database
    import services.llama_index as indexing

    monkeypatch.setattr(database, "is_postgres", True)
    monkeypatch.setattr(files_api, "utcnow", lambda: STAMP)
    monkeypatch.setattr(agent_api, "utcnow", lambda: STAMP)
    monkeypatch.setattr(crud_module, "utcnow", lambda: STAMP)
    monkeypatch.setattr(indexing, "schedule_index_upsert", lambda **_kw: None)
    monkeypatch.setattr(indexing, "schedule_index_delete", lambda **_kw: None)
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileCRUD, "_schedule_index_delete", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    monkeypatch.setattr(files_api.activation_event_service, "record_once", lambda *_a, **_kw: None)


def _seed(engine, suffix, *, screenplay=False):
    with Session(engine) as session:
        user = User(
            username=f"tree-writer-{suffix}", email=f"tree-writer-{suffix}@example.test",
            hashed_password="unused", email_verified=True,
        )
        session.add(user)
        session.flush()
        project = Project(
            name=f"Tree {suffix}", owner_id=user.id,
            project_type="screenplay" if screenplay else "novel",
        )
        session.add(project)
        session.flush()
        a = File(project_id=project.id, title="A", file_type="folder", updated_at=STAMP)
        if screenplay:
            a.id = f"{project.id}-script-folder"
        b = File(project_id=project.id, title="B", file_type="folder", updated_at=STAMP)
        session.add_all([a, b])
        session.flush()
        c = File(
            project_id=project.id, title="C", file_type="folder", parent_id=a.id,
            updated_at=STAMP,
        )
        key = AgentApiKey(
            user_id=user.id, key_prefix=suffix[:8], key_hash=f"tree-writer-{suffix}",
            name="Tree writer", scopes=["read", "write"],
        )
        session.add_all([c, key])
        session.commit()
        return user.id, project.id, a.id, b.id, c.id, key.id


def _move(session, kind, user_id, key_id, file_id, parent_id):
    owner = session.get(User, user_id)
    try:
        if kind == "web-move":
            files_api.move_file(
                file_id, files_api.MoveFileRequest(target_parent_id=parent_id),
                BackgroundTasks(), current_user=owner, session=session,
            )
        elif kind == "web-update":
            files_api.update_file(
                file_id, files_api.FileUpdate(parent_id=parent_id), BackgroundTasks(),
                current_user=owner, session=session,
            )
        elif kind == "tool-update":
            FileCRUD(session, user_id).update_file(file_id, parent_id=parent_id)
        else:
            key = session.get(AgentApiKey, key_id)
            agent_api.move_file(
                file_id, agent_api.FileMove(parent_id=parent_id), BackgroundTasks(),
                _rate_limit=0, context=(session, user_id, key),
            )
        return 200
    except APIException as exc:
        return exc.status_code
    except ValueError:
        return 400


def _observe_wait(engine, pid: int, future: Future) -> bool:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if future.done():
            return False
        with engine.connect() as connection:
            waiting = connection.execute(
                text("SELECT wait_event_type = 'Lock' FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": pid},
            ).scalar()
        if waiting:
            return True
        time.sleep(0.01)
    return False


@pytest.mark.parametrize("second_kind", ["web-move", "web-update", "tool-update", "agent-move"])
@pytest.mark.parametrize("deeper_parent", [False, True])
def test_reciprocal_parent_writers_cannot_commit_a_cycle(pg_engine, monkeypatch, second_kind, deeper_parent):
    user_id, project_id, a_id, b_id, c_id, key_id = _seed(
        pg_engine, f"cycle-{second_kind}-{deeper_parent}"
    )
    first_validated, release, second_started = Event(), Event(), Event()
    second_pid = []
    original = files_api._validate_parent_assignment

    def pause_first(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("moving_file_id") == a_id:
            first_validated.set()
            assert release.wait(8), "first move was not released"
        return result

    monkeypatch.setattr(files_api, "_validate_parent_assignment", pause_first)

    def first():
        with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
            return _move(session, "web-move", user_id, key_id, a_id, b_id)

    def second():
        with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
            # Keep stale ancestor objects alive across the first writer's commit.
            cached = [session.get(File, item) for item in [a_id, b_id, c_id]]
            assert cached[0].parent_id is None
            second_pid.append(session.exec(text("SELECT pg_backend_pid()")).scalar())
            second_started.set()
            return _move(session, second_kind, user_id, key_id, b_id, c_id if deeper_parent else a_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        try:
            assert first_validated.wait(5)
            second_future = pool.submit(second)
            assert second_started.wait(5)
            waited = _observe_wait(pg_engine, second_pid[0], second_future)
        finally:
            release.set()
        outcomes = [first_future.result(10), second_future.result(10)]

    assert outcomes == [200, 400], f"writers committed incompatible topology: {outcomes}"
    assert waited, "second structural writer did not wait for the first transaction"
    with Session(pg_engine) as verify:
        rows = verify.exec(select(File).where(File.project_id == project_id)).all()
        parents = {row.id: row.parent_id for row in rows}
        assert parents == {a_id: b_id, b_id: None, c_id: a_id}


@pytest.mark.parametrize("creator", ["web", "tool", "agent"])
def test_recursive_delete_excludes_concurrent_child_creation(pg_engine, monkeypatch, creator):
    user_id, project_id, a_id, _, c_id, key_id = _seed(pg_engine, f"delete-create-{creator}")
    deleted, release, started = Event(), Event(), Event()
    pid = []
    original = files_api._delete_recursive

    def pause_deleted(session, file):
        result = original(session, file)
        if file.id == a_id:
            deleted.set()
            assert release.wait(8), "delete was not released"
        return result

    monkeypatch.setattr(files_api, "_delete_recursive", pause_deleted)

    def delete():
        with Session(pg_engine) as session:
            return files_api.delete_file(
                a_id, BackgroundTasks(), recursive=True,
                current_user=session.get(User, user_id), session=session,
            )

    def create():
        with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
            cached_parent = session.get(File, a_id)
            assert not cached_parent.is_deleted
            pid.append(session.exec(text("SELECT pg_backend_pid()")).scalar())
            started.set()
            try:
                if creator == "web":
                    files_api.create_file(
                        project_id, files_api.FileCreate(title="New child", parent_id=a_id),
                        BackgroundTasks(), current_user=session.get(User, user_id), session=session,
                    )
                elif creator == "tool":
                    FileCRUD(session, user_id).create_file(project_id, "New child", parent_id=a_id)
                else:
                    agent_api.create_file(
                        project_id, agent_api.FileCreate(title="New child", parent_id=a_id),
                        BackgroundTasks(), _rate_limit=0,
                        context=(session, user_id, session.get(AgentApiKey, key_id)),
                    )
                return 200
            except APIException as exc:
                return exc.status_code
            except ValueError:
                return 404

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_future = pool.submit(delete)
        try:
            assert deleted.wait(5)
            create_future = pool.submit(create)
            assert started.wait(5)
            waited = _observe_wait(pg_engine, pid[0], create_future)
        finally:
            release.set()
        assert delete_future.result(10)["message"] == "File deleted successfully"
        outcome = create_future.result(10)

    expected = 404 if creator == "tool" else 400
    assert outcome == expected, f"child was created under a deleted parent: {outcome}"
    assert waited
    with Session(pg_engine) as verify:
        assert verify.get(File, a_id).is_deleted
        assert verify.get(File, c_id).is_deleted
        assert not verify.exec(select(File).where(
            File.project_id == project_id, File.title == "New child", File.is_deleted.is_(False),
        )).all()


def test_reorder_uses_committed_title_and_timestamp_not_cached_identity(pg_engine):
    user_id, project_id, a_id, _, _, _ = _seed(pg_engine, "reorder-fresh")
    future = STAMP + timedelta(days=365)
    with Session(pg_engine, expire_on_commit=False) as stale, Session(pg_engine) as winner:
        cached = stale.get(File, a_id)
        owner = stale.get(User, user_id)
        fresh = winner.get(File, a_id)
        fresh.title = "Chapter 7"
        fresh.file_type = "draft"
        fresh.content = "Winner body"
        fresh.updated_at = future
        winner.add(fresh)
        winner.commit()
        assert cached.title == "A"
        files_api.reorder_files(
            project_id, files_api.ReorderFilesRequest(ordered_ids=[a_id]),
            current_user=owner, session=stale,
        )
    with Session(pg_engine) as verify:
        file = verify.get(File, a_id)
        assert file.order == 7
        assert file.updated_at > future
        assert (file.title, file.content) == ("Chapter 7", "Winner body")


@pytest.mark.parametrize("needs_promotion", [False, True])
def test_screenplay_reuse_returns_and_mutates_fresh_candidate(pg_engine, needs_promotion):
    user_id, project_id, a_id, _, _, _ = _seed(
        pg_engine, f"reuse-fresh-{needs_promotion}", screenplay=True,
    )
    future = STAMP + timedelta(days=365)
    with Session(pg_engine) as setup:
        episode = File(
            project_id=project_id, parent_id=a_id, title="第3集：复用", file_type="draft",
            order=0, content="Old body", updated_at=STAMP,
        )
        setup.add(episode)
        setup.commit()
        episode_id = episode.id
    with Session(pg_engine, expire_on_commit=False) as stale, Session(pg_engine) as winner:
        cached = stale.get(File, episode_id)
        fresh = winner.get(File, episode_id)
        fresh.file_type = "draft" if needs_promotion else "script"
        fresh.order = 9
        fresh.content = "Newest committed body"
        fresh.updated_at = future
        winner.add(fresh)
        winner.commit()
        assert cached.content == "Old body"
        result = FileCRUD(stale, user_id).create_file(
            project_id, "第3集：复用", file_type="script", parent_id=a_id,
        )
        assert result["id"] == episode_id
        assert result["content"] == "Newest committed body"
        assert result["original_content_length"] == len("Newest committed body")
        assert result["mutation_applied"] is needs_promotion
    with Session(pg_engine) as verify:
        episode = verify.get(File, episode_id)
        assert (episode.file_type, episode.order, episode.content) == (
            "script", 9, "Newest committed body",
        )
        assert episode.updated_at > future if needs_promotion else episode.updated_at == future


@pytest.mark.parametrize("deleter", ["web", "tool", "agent"])
def test_all_delete_entrypoints_exclude_reparent_and_advance_fresh_tokens(pg_engine, deleter):
    user_id, project_id, a_id, b_id, c_id, key_id = _seed(pg_engine, f"delete-move-{deleter}")
    future = STAMP + timedelta(days=365)
    with Session(pg_engine) as setup:
        for item in [a_id, c_id]:
            row = setup.get(File, item)
            row.updated_at = future
            setup.add(row)
        setup.commit()
    paused, release, started = Event(), Event(), Event()
    pid = []

    def pause_delete(session):
        if session.info.pop("pause_tree_delete", False):
            paused.set()
            assert release.wait(8), "delete commit was not released"

    def delete():
        with Session(pg_engine, autoflush=False) as session:
            session.info["pause_tree_delete"] = True
            owner = session.get(User, user_id)
            if deleter == "web":
                return files_api.delete_file(a_id, BackgroundTasks(), recursive=True, current_user=owner, session=session)
            if deleter == "tool":
                return FileCRUD(session, user_id).delete_file(a_id, recursive=True)
            return agent_api.delete_file(
                a_id, BackgroundTasks(), _rate_limit=0,
                context=(session, user_id, session.get(AgentApiKey, key_id)),
            )

    def reparent():
        with Session(pg_engine, expire_on_commit=False, autoflush=False) as session:
            cached_parent = session.get(File, a_id)
            assert not cached_parent.is_deleted
            pid.append(session.exec(text("SELECT pg_backend_pid()")).scalar())
            started.set()
            return _move(session, "web-move", user_id, key_id, b_id, a_id)

    event.listen(Session, "before_commit", pause_delete)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(delete)
            try:
                assert paused.wait(5)
                second = pool.submit(reparent)
                assert started.wait(5)
                waited = _observe_wait(pg_engine, pid[0], second)
            finally:
                release.set()
            result = first.result(10)
            outcome = second.result(10)
    finally:
        event.remove(Session, "before_commit", pause_delete)

    assert result is True if deleter == "tool" else result["message"] == "File deleted successfully"
    assert outcome == 400, f"reparent accepted a deleted target: {outcome}"
    assert waited
    with Session(pg_engine) as verify:
        a, b, c = [verify.get(File, item) for item in [a_id, b_id, c_id]]
        assert a.is_deleted and a.updated_at > future
        assert b.parent_id is None and not b.is_deleted
        assert c.is_deleted is (deleter != "agent")
        assert c.updated_at > future if deleter != "agent" else c.updated_at == future


def test_reorder_rejects_foreign_file_without_taking_its_write_lock(pg_engine):
    user_id, project_id, _, _, _, _ = _seed(pg_engine, "reorder-owned")
    _, _, foreign_id, _, _, _ = _seed(pg_engine, "reorder-foreign")
    with Session(pg_engine, autoflush=False) as actor:
        with pytest.raises(APIException) as rejected:
            files_api.reorder_files(
                project_id, files_api.ReorderFilesRequest(ordered_ids=[foreign_id]),
                current_user=actor.get(User, user_id), session=actor,
            )
        assert rejected.value.status_code == 400
        assert rejected.value.error_code == ErrorCode.VALIDATION_ERROR
        # Keep the rejected caller's transaction alive; another project's owner
        # must still be able to lock their row immediately, not wait for cleanup.
        with Session(pg_engine) as other:
            row = other.exec(select(File).where(File.id == foreign_id).with_for_update(
                key_share=True, nowait=True,
            )).one()
            assert row.id == foreign_id


@pytest.mark.parametrize("writer", ["same-project-content", "other-project-move"])
def test_tree_gate_does_not_serialize_unrelated_work(pg_engine, monkeypatch, writer):
    user_id, _, a_id, b_id, c_id, key_id = _seed(pg_engine, f"unrelated-{writer}")
    other_user, _, other_a, other_b, _, other_key = _seed(pg_engine, f"other-{writer}")
    paused, release = Event(), Event()
    original = files_api._validate_parent_assignment

    def pause_first(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get("moving_file_id") == a_id:
            paused.set()
            assert release.wait(8)
        return result

    monkeypatch.setattr(files_api, "_validate_parent_assignment", pause_first)

    def structural():
        with Session(pg_engine) as session:
            return _move(session, "web-move", user_id, key_id, a_id, b_id)

    def unrelated():
        with Session(pg_engine) as session:
            if writer == "other-project-move":
                return _move(session, "web-move", other_user, other_key, other_a, other_b)
            file = files_api.update_file(
                c_id, files_api.FileUpdate(content="Independent content"), BackgroundTasks(),
                current_user=session.get(User, user_id), session=session,
            )
            return file.content

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(structural)
        try:
            assert paused.wait(5)
            second = pool.submit(unrelated)
            result = second.result(3)  # Must finish before the first Project gate releases.
        finally:
            release.set()
        assert first.result(10) == 200
    assert result == (200 if writer == "other-project-move" else "Independent content")
    if writer == "same-project-content":
        with Session(pg_engine) as verify:
            versions = verify.exec(select(FileVersion).where(FileVersion.file_id == c_id)).all()
            assert len(versions) == 1 and versions[0].content == "Independent content"


@pytest.mark.parametrize("deleter", ["web", "tool"])
def test_recursive_delete_refreshes_descendant_after_content_writer_lock(pg_engine, deleter):
    user_id, _, a_id, _, c_id, _ = _seed(pg_engine, f"descendant-lock-{deleter}")
    future = STAMP + timedelta(days=365)
    writer_locked, release, delete_started = Event(), Event(), Event()
    pid = []

    def content_writer():
        with Session(pg_engine) as session:
            child = files_api._load_file_for_write(session, c_id)
            child.content = "Committed while delete waits"
            child.updated_at = future
            session.add(child)
            session.flush()
            writer_locked.set()
            assert release.wait(8)
            session.commit()

    def delete():
        with Session(pg_engine, autoflush=False, expire_on_commit=False) as session:
            cached_child = session.get(File, c_id)
            assert cached_child.updated_at == STAMP
            pid.append(session.exec(text("SELECT pg_backend_pid()")).scalar())
            delete_started.set()
            if deleter == "tool":
                return FileCRUD(session, user_id).delete_file(a_id, recursive=True)
            return files_api.delete_file(
                a_id, BackgroundTasks(), recursive=True,
                current_user=session.get(User, user_id), session=session,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(content_writer)
        try:
            assert writer_locked.wait(5)
            second = pool.submit(delete)
            assert delete_started.wait(5)
            waited = _observe_wait(pg_engine, pid[0], second)
        finally:
            release.set()
        first.result(10)
        result = second.result(10)
    assert waited
    assert result is True if deleter == "tool" else result["message"] == "File deleted successfully"
    with Session(pg_engine) as verify:
        child = verify.get(File, c_id)
        assert child.is_deleted and child.updated_at > future
        assert child.content == "Committed while delete waits"


def test_combined_parent_content_update_commits_history_under_strong_gate(pg_engine):
    user_id, _, a_id, b_id, c_id, _ = _seed(pg_engine, "parent-content")
    with Session(pg_engine) as session:
        updated = files_api.update_file(
            c_id, files_api.FileUpdate(parent_id=b_id, content="Moved and saved"),
            BackgroundTasks(), current_user=session.get(User, user_id), session=session,
        )
        assert updated.parent_id == b_id and updated.content == "Moved and saved"
    with Session(pg_engine) as verify:
        versions = verify.exec(select(FileVersion).where(FileVersion.file_id == c_id)).all()
        assert len(versions) == 1 and versions[0].content == "Moved and saved"
        assert verify.get(File, a_id).parent_id is None
