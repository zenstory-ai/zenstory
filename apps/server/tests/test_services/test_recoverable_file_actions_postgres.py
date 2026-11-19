"""PostgreSQL transaction and lock regressions for recoverable file actions.

This module intentionally runs only in the owned, serial PostgreSQL lane.  The
behaviours below depend on real row locks and cannot be proved with SQLite.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock
from time import monotonic, sleep
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlmodel import Session, SQLModel, select

import database
from agent.tools.file_ops.crud import FileCRUD
from agent.tools.file_ops.edit import FileEditor
from agent.tools.write_errors import FileWriteRecoveryError
from api import agent_api
from api import files as files_api
from config.datetime_utils import normalize_datetime_to_utc
from models import File, Project, Snapshot, User
from models.agent_api_key import AgentApiKey
from models.file_deletion_batch import FileDeletionBatch
from models.file_edit_operation import FileEditOperation
from models.file_version import (
    CHANGE_SOURCE_USER,
    CHANGE_TYPE_EDIT,
    CHANGE_TYPE_RESTORE,
    FileVersion,
)
from services import file_trash as file_trash_module
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService
from services.file_edit_operations import EditOperationConflictError, undo_edit_operation
from services.file_trash import FileTrashConflictError, FileTrashService

PG_URL = os.getenv("ZENSTORY_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not PG_URL or PG_URL != os.getenv("DATABASE_URL"),
    reason="explicitly owned serial PostgreSQL URL required",
)

TABLES = [
    User.__table__,
    Project.__table__,
    File.__table__,
    Snapshot.__table__,
    FileVersion.__table__,
    AgentApiKey.__table__,
    FileEditOperation.__table__,
    FileDeletionBatch.__table__,
]
STAMP = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def recovery_pg_engine():
    assert PG_URL is not None
    assert make_url(PG_URL).get_backend_name() == "postgresql"
    assert database.is_postgres is True
    engine = create_engine(
        PG_URL,
        pool_pre_ping=True,
        connect_args={"options": "-c timezone=UTC -c statement_timeout=15000 -c lock_timeout=10000"},
    )
    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine, tables=TABLES)
        engine.dispose()


@pytest.fixture(autouse=True)
def postgres_branches_and_stable_side_effects(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(database, "is_postgres", True)
    monkeypatch.setattr(
        activation_event_service,
        "record_ai_write_accepted",
        lambda *_args, **_kwargs: None,
    )


def _seed_file(engine, *, with_folders: bool = False) -> dict[str, str]:
    suffix = uuid4().hex
    with Session(engine) as session:
        user = User(
            username=f"recovery-pg-{suffix}",
            email=f"recovery-pg-{suffix}@example.test",
            hashed_password="unused",
            email_verified=True,
        )
        session.add(user)
        session.flush()
        project = Project(name="Recoverable PG", owner_id=user.id)
        session.add(project)
        session.flush()
        folder_a = None
        folder_b = None
        if with_folders:
            folder_a = File(
                project_id=project.id,
                title="Folder A",
                file_type="folder",
                updated_at=STAMP,
            )
            folder_b = File(
                project_id=project.id,
                title="Folder B",
                file_type="folder",
                updated_at=STAMP,
            )
            session.add_all([folder_a, folder_b])
            session.flush()
        file = File(
            project_id=project.id,
            parent_id=folder_a.id if folder_a else None,
            title="Original title",
            content="Original body",
            file_type="draft",
            order=1,
            updated_at=STAMP,
        )
        session.add(file)
        session.flush()
        FileVersionService().create_version(
            session,
            file.id,
            file.content,
            change_type=CHANGE_TYPE_EDIT,
            change_source=CHANGE_SOURCE_USER,
            skip_quota=True,
            commit=False,
        )
        session.commit()
        return {
            "user": user.id,
            "project": project.id,
            "file": file.id,
            "folder_a": folder_a.id if folder_a else "",
            "folder_b": folder_b.id if folder_b else "",
        }


def _service(session: Session, ids: dict[str, str]) -> FileTrashService:
    return FileTrashService(session, user_id=ids["user"], project_id=ids["project"])


def _assert_all_live(engine, *file_ids: str) -> None:
    with Session(engine) as session:
        files = [session.get(File, file_id) for file_id in file_ids]
        assert all(file is not None and file.is_deleted is False for file in files)


def _wait_for_backend_lock(engine, backend_pid: int, future) -> bool:
    deadline = monotonic() + 3
    while monotonic() < deadline:
        if future.done():
            return False
        with Session(engine) as monitor:
            row = monitor.exec(
                text(f"select wait_event_type from pg_stat_activity where pid = {backend_pid}")
            ).one_or_none()
        if row is not None and row[0] == "Lock":
            return True
        sleep(0.02)
    return False


def _seed_deleted_file_with_live_sibling(engine) -> dict[str, str]:
    suffix = uuid4().hex
    with Session(engine) as session:
        user = User(
            username=f"recovery-gate-{suffix}",
            email=f"recovery-gate-{suffix}@example.test",
            hashed_password="unused",
            email_verified=True,
        )
        session.add(user)
        session.flush()
        project = Project(name="Recovery gate", owner_id=user.id)
        session.add(project)
        session.flush()
        deleted = File(
            project_id=project.id,
            title="Recovered title",
            content="Deleted body",
            file_type="draft",
            order=1,
            updated_at=STAMP,
        )
        sibling = File(
            project_id=project.id,
            title="Sibling title",
            content="Sibling body",
            file_type="draft",
            order=2,
            updated_at=STAMP,
        )
        api_key = AgentApiKey(
            user_id=user.id,
            key_prefix=suffix[:8],
            key_hash=f"recovery-gate-{suffix}",
            name="Recovery gate writer",
            scopes=["read", "write"],
            project_ids=[project.id],
        )
        session.add_all([deleted, sibling, api_key])
        session.commit()
        deletion = FileTrashService(session, user_id=user.id, project_id=project.id).delete(
            [deleted.id], recursive=False, source="file_menu"
        )
        return {
            "user": user.id,
            "project": project.id,
            "deleted": deleted.id,
            "sibling": sibling.id,
            "key": api_key.id,
            "batch": deletion["batch_id"],
        }


def _seed_undoable_title_with_live_sibling(engine) -> dict[str, str]:
    ids = _seed_file(engine)
    with Session(engine) as session:
        target = session.get(File, ids["file"])
        assert target is not None
        target.title = "Recovered title"
        session.add(target)
        sibling = File(
            project_id=ids["project"],
            title="Sibling title",
            content="Sibling body",
            file_type="draft",
            order=2,
            updated_at=STAMP,
        )
        session.add(sibling)
        session.commit()
        edited = FileEditor(session, ids["user"]).replace_body(
            ids["file"],
            "AI body",
            expected_updated_at=None,
            title="AI title",
        )
        return {
            **ids,
            "target": ids["file"],
            "sibling": sibling.id,
            "operation": edited["undo"]["operation_id"],
        }


def test_confirmed_folder_delete_rejects_child_created_after_preview(recovery_pg_engine):
    ids = _seed_file(recovery_pg_engine, with_folders=True)
    with Session(recovery_pg_engine) as session:
        preview = _service(session, ids).preview([ids["folder_a"]], recursive=True)
    with Session(recovery_pg_engine) as session:
        child = File(
            project_id=ids["project"],
            parent_id=ids["folder_a"],
            title="Late child",
            content="Created after preview",
            file_type="draft",
            updated_at=STAMP + timedelta(seconds=1),
        )
        session.add(child)
        session.commit()
        child_id = child.id

    with Session(recovery_pg_engine) as session:
        with pytest.raises(FileTrashConflictError, match="删除范围已经变化"):
            _service(session, ids).delete(
                [ids["folder_a"]],
                recursive=True,
                source="agent_confirmed",
                expected_target_hash=preview["target_hash"],
            )

    _assert_all_live(recovery_pg_engine, ids["folder_a"], ids["file"], child_id)


def test_confirmed_folder_delete_rejects_child_moved_after_preview(recovery_pg_engine):
    ids = _seed_file(recovery_pg_engine, with_folders=True)
    with Session(recovery_pg_engine) as session:
        preview = _service(session, ids).preview([ids["folder_a"]], recursive=True)
    with Session(recovery_pg_engine) as session:
        file = session.get(File, ids["file"])
        assert file is not None
        file.parent_id = ids["folder_b"]
        file.updated_at = STAMP + timedelta(seconds=1)
        session.add(file)
        session.commit()

    with Session(recovery_pg_engine) as session:
        with pytest.raises(FileTrashConflictError, match="删除范围已经变化"):
            _service(session, ids).delete(
                [ids["folder_a"]],
                recursive=True,
                source="agent_confirmed",
                expected_target_hash=preview["target_hash"],
            )

    _assert_all_live(recovery_pg_engine, ids["folder_a"], ids["folder_b"], ids["file"])


def test_confirmed_delete_rejects_body_written_after_preview(recovery_pg_engine):
    ids = _seed_file(recovery_pg_engine)
    with Session(recovery_pg_engine) as session:
        preview = _service(session, ids).preview([ids["file"]], recursive=False)
    with Session(recovery_pg_engine) as session:
        file = session.get(File, ids["file"])
        assert file is not None
        file.content = "Author changed this after preview"
        file.updated_at = STAMP + timedelta(seconds=1)
        session.add(file)
        session.commit()

    with Session(recovery_pg_engine) as session:
        with pytest.raises(FileTrashConflictError, match="删除范围已经变化"):
            _service(session, ids).delete(
                [ids["file"]],
                recursive=False,
                source="agent_confirmed",
                expected_target_hash=preview["target_hash"],
            )

    _assert_all_live(recovery_pg_engine, ids["file"])


@pytest.mark.parametrize("writer", ["web", "agent_api", "tool"])
@pytest.mark.parametrize("mutation", ["title", "order"])
def test_sibling_structural_update_serializes_before_restore_conflict_check(
    recovery_pg_engine, writer: str, mutation: str
):
    ids = _seed_deleted_file_with_live_sibling(recovery_pg_engine)
    writer_staged = Event()
    release_writer = Event()
    restore_connected = Event()
    restore_backend_pid: list[int] = []

    def update_sibling() -> None:
        with Session(recovery_pg_engine) as session:
            original_commit = session.commit
            commit_calls = 0

            def flush_pause_then_commit() -> None:
                nonlocal commit_calls
                commit_calls += 1
                session.flush()
                if commit_calls == 1:
                    writer_staged.set()
                    assert release_writer.wait(timeout=8)
                original_commit()

            session.commit = flush_pause_then_commit  # type: ignore[method-assign]
            payload = {mutation: "Recovered title" if mutation == "title" else 99}
            if writer == "web":
                owner = session.get(User, ids["user"])
                assert owner is not None
                files_api.update_file(
                    ids["sibling"],
                    files_api.FileUpdate(**payload),
                    current_user=owner,
                    session=session,
                )
            elif writer == "agent_api":
                api_key = session.get(AgentApiKey, ids["key"])
                assert api_key is not None
                agent_api.update_file(
                    ids["sibling"],
                    agent_api.FileUpdate(**payload),
                    _rate_limit=0,
                    context=(session, ids["user"], api_key),
                )
            else:
                FileCRUD(session, ids["user"]).update_file(ids["sibling"], **payload)

    def restore_deleted() -> dict:
        with Session(recovery_pg_engine) as session:
            restore_backend_pid.append(int(session.exec(text("select pg_backend_pid()")).one()[0]))
            restore_connected.set()
            try:
                result = FileTrashService(session, user_id=ids["user"], project_id=ids["project"]).restore(ids["batch"])
                return {"status": "restored", "result": result}
            except FileTrashConflictError as exc:
                return {"status": "conflict", "error": str(exc)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer_future = pool.submit(update_sibling)
        assert writer_staged.wait(timeout=5)
        # The rename/order update is flushed but not committed yet.
        with Session(recovery_pg_engine) as observer:
            sibling = observer.get(File, ids["sibling"])
            assert sibling is not None
            assert (sibling.title, sibling.order) == ("Sibling title", 2)
        restore_future = pool.submit(restore_deleted)
        try:
            assert restore_connected.wait(timeout=5)
            assert _wait_for_backend_lock(recovery_pg_engine, restore_backend_pid[0], restore_future)
        finally:
            release_writer.set()
        writer_future.result(timeout=20)
        restored = restore_future.result(timeout=20)

    with Session(recovery_pg_engine) as verify:
        deleted = verify.get(File, ids["deleted"])
        sibling = verify.get(File, ids["sibling"])
        batch = verify.get(FileDeletionBatch, ids["batch"])
        assert deleted is not None and sibling is not None and batch is not None
        assert (deleted.title, deleted.content) == ("Recovered title", "Deleted body")
        if mutation == "title":
            assert restored["status"] == "conflict"
            assert "同名" in restored["error"]
            assert deleted.is_deleted is True
            assert batch.restored_at is None
            assert (sibling.title, sibling.order) == ("Recovered title", 2)
        else:
            assert restored["status"] == "restored"
            assert restored["result"]["already_restored"] is False
            assert deleted.is_deleted is False
            assert batch.restored_at is not None
            assert (sibling.title, sibling.order) == ("Sibling title", 99)


@pytest.mark.parametrize("recovery", ["restore", "undo"])
def test_replace_body_title_waits_before_recovery_checks_sibling_conflict(recovery_pg_engine, recovery: str):
    if recovery == "restore":
        ids = _seed_deleted_file_with_live_sibling(recovery_pg_engine)
        ids["target"] = ids["deleted"]
    else:
        ids = _seed_undoable_title_with_live_sibling(recovery_pg_engine)
    writer_staged = Event()
    release_writer = Event()
    recovery_connected = Event()
    recovery_backend_pid: list[int] = []

    def replace_sibling_title() -> None:
        with Session(recovery_pg_engine) as session:
            original_commit = session.commit

            def flush_pause_then_commit() -> None:
                session.flush()
                writer_staged.set()
                assert release_writer.wait(timeout=8)
                original_commit()

            session.commit = flush_pause_then_commit  # type: ignore[method-assign]
            FileEditor(session, ids["user"]).replace_body(
                ids["sibling"],
                "Sibling rewritten body",
                expected_updated_at=None,
                title="Recovered title",
            )

    def recover_target() -> dict:
        with Session(recovery_pg_engine) as session:
            recovery_backend_pid.append(int(session.exec(text("select pg_backend_pid()")).one()[0]))
            recovery_connected.set()
            try:
                if recovery == "restore":
                    FileTrashService(session, user_id=ids["user"], project_id=ids["project"]).restore(ids["batch"])
                else:
                    undo_edit_operation(
                        session,
                        operation_id=ids["operation"],
                        file_id=ids["target"],
                        user_id=ids["user"],
                    )
                return {"status": "applied"}
            except (FileTrashConflictError, EditOperationConflictError) as exc:
                return {"status": "conflict", "error": str(exc)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer_future = pool.submit(replace_sibling_title)
        assert writer_staged.wait(timeout=5)
        with Session(recovery_pg_engine) as observer:
            sibling = observer.get(File, ids["sibling"])
            assert sibling is not None and sibling.title == "Sibling title"
        recovery_future = pool.submit(recover_target)
        try:
            assert recovery_connected.wait(timeout=5)
            assert _wait_for_backend_lock(recovery_pg_engine, recovery_backend_pid[0], recovery_future)
        finally:
            release_writer.set()
        writer_future.result(timeout=20)
        recovered = recovery_future.result(timeout=20)

    assert recovered["status"] == "conflict"
    with Session(recovery_pg_engine) as verify:
        target = verify.get(File, ids["target"])
        sibling = verify.get(File, ids["sibling"])
        assert target is not None and sibling is not None
        assert (sibling.title, sibling.content) == (
            "Recovered title",
            "Sibling rewritten body",
        )
        if recovery == "restore":
            batch = verify.get(FileDeletionBatch, ids["batch"])
            assert batch is not None and batch.restored_at is None
            assert (target.is_deleted, target.title, target.content) == (
                True,
                "Recovered title",
                "Deleted body",
            )
        else:
            operation = verify.get(FileEditOperation, ids["operation"])
            assert operation is not None and operation.undone_at is None
            assert (target.is_deleted, target.title, target.content) == (
                False,
                "AI title",
                "AI body",
            )


def _race_delete_with_mutation(engine, ids: dict[str, str], mutation: str) -> tuple[dict, dict]:
    barrier = Barrier(2)

    def delete() -> dict:
        with Session(engine) as session:
            barrier.wait(timeout=5)
            return _service(session, ids).delete([ids["file"]], recursive=False, source="file_menu")

    def mutate() -> dict:
        with Session(engine) as session:
            barrier.wait(timeout=5)
            try:
                if mutation == "body":
                    result = FileCRUD(session, ids["user"]).update_file(ids["file"], content="Concurrent body")
                else:
                    result = FileEditor(session, ids["user"]).rename_with_undo(ids["file"], "Concurrent title")
                return {"succeeded": True, "result": result}
            except ValueError as exc:
                return {"succeeded": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_future = pool.submit(delete)
        mutate_future = pool.submit(mutate)
        deleted = delete_future.result(timeout=20)
        mutated = mutate_future.result(timeout=20)
    return deleted, mutated


def test_delete_and_body_update_serialize_into_a_restorable_generation(recovery_pg_engine):
    ids = _seed_file(recovery_pg_engine)
    deleted, mutated = _race_delete_with_mutation(recovery_pg_engine, ids, "body")

    with Session(recovery_pg_engine) as session:
        restored = _service(session, ids).restore(deleted["batch_id"])
    assert restored["already_restored"] is False
    with Session(recovery_pg_engine) as session:
        file = session.get(File, ids["file"])
        assert file is not None and file.is_deleted is False
        assert file.content == ("Concurrent body" if mutated["succeeded"] else "Original body")


def test_leaf_delete_holds_row_lock_from_state_capture_through_batch_commit(
    recovery_pg_engine, monkeypatch: pytest.MonkeyPatch
):
    """An edit cannot commit after delete captured the generation it records."""
    ids = _seed_file(recovery_pg_engine)
    delete_captured_state = Event()
    release_delete = Event()
    edit_connected = Event()
    edit_backend_pid: list[int] = []
    call_lock = Lock()
    target_state_calls = 0
    original_target_state = file_trash_module._target_state

    def pause_after_second_state_capture(file: File) -> dict:
        nonlocal target_state_calls
        state = original_target_state(file)
        if file.id != ids["file"]:
            return state
        with call_lock:
            target_state_calls += 1
            should_pause = target_state_calls == 2
        if should_pause:
            delete_captured_state.set()
            assert release_delete.wait(timeout=5)
        return state

    monkeypatch.setattr(file_trash_module, "_target_state", pause_after_second_state_capture)

    def delete() -> dict:
        with Session(recovery_pg_engine) as session:
            return _service(session, ids).delete([ids["file"]], recursive=False, source="file_menu")

    def edit() -> dict:
        try:
            with Session(recovery_pg_engine) as session:
                edit_backend_pid.append(int(session.exec(text("select pg_backend_pid()")).one()[0]))
                edit_connected.set()
                FileCRUD(session, ids["user"]).update_file(
                    ids["file"], content="Must not slip into the deletion generation"
                )
            return {"succeeded": True}
        except ValueError as exc:
            return {"succeeded": False, "error": str(exc)}

    with ThreadPoolExecutor(max_workers=2) as pool:
        delete_future = pool.submit(delete)
        assert delete_captured_state.wait(timeout=5)
        edit_future = pool.submit(edit)
        try:
            assert edit_connected.wait(timeout=5)
            deadline = monotonic() + 2
            wait_event_type = None
            while monotonic() < deadline:
                with Session(recovery_pg_engine) as monitor:
                    row = monitor.exec(
                        text(f"select wait_event_type from pg_stat_activity where pid = {edit_backend_pid[0]}")
                    ).one_or_none()
                wait_event_type = row[0] if row is not None else None
                if wait_event_type == "Lock":
                    break
                sleep(0.02)
            assert wait_event_type == "Lock"
        finally:
            release_delete.set()
        deleted = delete_future.result(timeout=20)
        edited = edit_future.result(timeout=20)

    assert edited["succeeded"] is False
    with Session(recovery_pg_engine) as session:
        restored = _service(session, ids).restore(deleted["batch_id"])
    assert restored["already_restored"] is False
    with Session(recovery_pg_engine) as session:
        file = session.get(File, ids["file"])
        assert file is not None
        assert (file.is_deleted, file.content) == (False, "Original body")


def test_delete_and_rename_serialize_into_a_restorable_generation(recovery_pg_engine):
    ids = _seed_file(recovery_pg_engine)
    deleted, mutated = _race_delete_with_mutation(recovery_pg_engine, ids, "title")

    with Session(recovery_pg_engine) as session:
        restored = _service(session, ids).restore(deleted["batch_id"])
    assert restored["already_restored"] is False
    with Session(recovery_pg_engine) as session:
        file = session.get(File, ids["file"])
        assert file is not None and file.is_deleted is False
        assert file.title == ("Concurrent title" if mutated["succeeded"] else "Original title")


def test_concurrent_double_restore_is_idempotent_under_row_locks(recovery_pg_engine):
    ids = _seed_file(recovery_pg_engine)
    with Session(recovery_pg_engine) as session:
        deleted = _service(session, ids).delete([ids["file"]], recursive=False, source="file_menu")
    barrier = Barrier(2)

    def restore() -> dict:
        with Session(recovery_pg_engine) as session:
            barrier.wait(timeout=5)
            return _service(session, ids).restore(deleted["batch_id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: restore(), range(2)))

    assert sorted(result["already_restored"] for result in outcomes) == [False, True]
    _assert_all_live(recovery_pg_engine, ids["file"])


def test_concurrent_double_undo_creates_one_restore_version(recovery_pg_engine):
    ids = _seed_file(recovery_pg_engine)
    with Session(recovery_pg_engine) as session:
        edited = FileEditor(session, ids["user"]).replace_body(
            ids["file"], "AI body", expected_updated_at=None, title="AI title"
        )
        operation_id = edited["undo"]["operation_id"]
    barrier = Barrier(2)

    def undo() -> dict:
        with Session(recovery_pg_engine) as session:
            barrier.wait(timeout=5)
            return undo_edit_operation(
                session,
                operation_id=operation_id,
                file_id=ids["file"],
                user_id=ids["user"],
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: undo(), range(2)))

    assert sorted(result["already_undone"] for result in outcomes) == [False, True]
    with Session(recovery_pg_engine) as session:
        file = session.get(File, ids["file"])
        operation = session.get(FileEditOperation, operation_id)
        restore_versions = session.exec(
            select(FileVersion).where(
                FileVersion.file_id == ids["file"],
                FileVersion.change_type == CHANGE_TYPE_RESTORE,
            )
        ).all()
        assert file is not None and (file.content, file.title) == ("Original body", "Original title")
        assert len(restore_versions) == 1
        assert operation is not None and operation.undo_version_id == restore_versions[0].id


def test_stale_undo_resolution_never_overwrites_later_author_body_or_title(recovery_pg_engine):
    ids = _seed_file(recovery_pg_engine)
    with Session(recovery_pg_engine) as session:
        edited = FileEditor(session, ids["user"]).replace_body(
            ids["file"], "AI body", expected_updated_at=None, title="AI title"
        )
        operation_id = edited["undo"]["operation_id"]
    with Session(recovery_pg_engine) as session:
        file = session.get(File, ids["file"])
        assert file is not None
        file.content = "Author body one"
        file.title = "Author title one"
        file.updated_at = STAMP + timedelta(seconds=10)
        session.add(file)
        session.commit()
    with Session(recovery_pg_engine) as session:
        preview = undo_edit_operation(
            session,
            operation_id=operation_id,
            file_id=ids["file"],
            user_id=ids["user"],
        )
    with Session(recovery_pg_engine) as session:
        file = session.get(File, ids["file"])
        assert file is not None
        file.content = "Author body two"
        file.title = "Author title two"
        file.updated_at = STAMP + timedelta(seconds=20)
        session.add(file)
        session.commit()

    with Session(recovery_pg_engine) as session:
        with pytest.raises(EditOperationConflictError, match="stale_write"):
            undo_edit_operation(
                session,
                operation_id=operation_id,
                file_id=ids["file"],
                user_id=ids["user"],
                expected_updated_at=datetime.fromisoformat(preview["current"]["updated_at"]),
                expected_after_content_sha256=preview["current"]["content_sha256"],
                resolved_content="Resolved from stale preview",
                metadata_resolution="preserve",
            )
    with Session(recovery_pg_engine) as session:
        file = session.get(File, ids["file"])
        assert file is not None
        assert (file.content, file.title) == ("Author body two", "Author title two")


def test_required_operation_failure_rolls_back_file_and_after_version(
    recovery_pg_engine, monkeypatch: pytest.MonkeyPatch
):
    ids = _seed_file(recovery_pg_engine)
    from services import file_edit_operations

    def fail_required_operation(*_args, **_kwargs):
        raise RuntimeError("operation insert unavailable")

    monkeypatch.setattr(file_edit_operations, "create_edit_operation", fail_required_operation)
    with Session(recovery_pg_engine) as session:
        with pytest.raises(FileWriteRecoveryError):
            FileEditor(session, ids["user"]).replace_body(
                ids["file"], "AI body", expected_updated_at=None, title="AI title"
            )

    with Session(recovery_pg_engine) as session:
        file = session.get(File, ids["file"])
        versions = session.exec(select(FileVersion).where(FileVersion.file_id == ids["file"])).all()
        operations = session.exec(select(FileEditOperation).where(FileEditOperation.file_id == ids["file"])).all()
        assert file is not None
        assert (file.content, file.title) == ("Original body", "Original title")
        assert normalize_datetime_to_utc(file.updated_at) == STAMP
        assert len(versions) == 1
        assert operations == []
