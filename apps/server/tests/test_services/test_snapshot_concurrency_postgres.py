"""Real PostgreSQL evidence for rollback/writer serialization and fresh reads."""

import json
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta
from io import BytesIO
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select
from starlette.datastructures import UploadFile

from agent.tools.file_ops import crud as crud_module
from agent.tools.file_ops.crud import FileCRUD
from api import agent_api
from api import files as files_api
from api import snapshots as snapshots_api
from api.materials import import_ as material_import
from api.materials.schemas import MaterialImportRequest
from core.error_handler import APIException
from models import File, FileVersion, Project, Snapshot, User
from models.agent_api_key import AgentApiKey
from services.features.file_version_service import FileVersionService, get_file_version_service
from services.features.snapshot_service import VersionService

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="isolated PostgreSQL URL not configured"
)
TABLES = [User.__table__, Project.__table__, File.__table__, Snapshot.__table__, FileVersion.__table__, AgentApiKey.__table__]
STAMP = datetime(2026, 10, 6, 8, 0, 0, 123456)


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(
        os.environ["ZENSTORY_TEST_POSTGRES_URL"],
        connect_args={"options": "-c timezone=UTC -c statement_timeout=10000 -c lock_timeout=8000"},
    )
    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine, tables=TABLES)
        engine.dispose()


@pytest.fixture(autouse=True)
def isolated_dependencies(monkeypatch):
    import database
    import services.llama_index as indexing

    monkeypatch.setattr(database, "is_postgres", True)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    monkeypatch.setattr(indexing, "schedule_index_upsert", lambda **_kw: None)
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_a, **_kw: None)
    monkeypatch.setattr(files_api.activation_event_service, "record_once", lambda *_a, **_kw: None)
    # Gate tests isolate unrelated material-library decoding/ownership. The
    # authentic preview/import transaction is covered by the SQLite regressions.
    monkeypatch.setattr(material_import, "_get_novel_or_404", lambda *_a, **_kw: None)
    monkeypatch.setattr(material_import, "get_material_preview", lambda **_kw: SimpleNamespace(
        markdown="Created after rollback", suggested_file_name="Concurrent",
        suggested_file_type="snippet", suggested_folder_name="Materials",
    ))


def _seed(engine, suffix, scoped=False, with_extra_folder=False):
    with Session(engine) as session:
        user = User(username=f"snapshot-race-{suffix}", email=f"snapshot-race-{suffix}@example.test", hashed_password="unused", email_verified=True)
        session.add(user)
        session.flush()
        project = Project(name=f"Snapshot race {suffix}", owner_id=user.id)
        session.add(project)
        session.flush()
        file = File(project_id=project.id, title="Original", file_type="draft", content="Original", updated_at=STAMP)
        key = AgentApiKey(user_id=user.id, key_prefix=suffix[:8], key_hash=f"snapshot-race-{suffix}", name="Snapshot race", scopes=["read", "write"])
        session.add_all([file, key])
        session.flush()
        get_file_version_service().create_initial_version(session, file)
        session.commit()
        snapshot = VersionService().create_snapshot(session, project.id, file_id=file.id if scoped else None)
        folder_id = None
        if with_extra_folder:
            folder = File(project_id=project.id, title="Later folder", file_type="folder")
            session.add(folder)
            session.commit()
            folder_id = folder.id
        return user.id, project.id, file.id, snapshot.id, key.id, folder_id


def _write_new_content(engine, file_id, content, stamp):
    with Session(engine) as session:
        file = session.get(File, file_id)
        file.content = content
        file.updated_at = stamp
        session.add(file)
        session.flush()
        get_file_version_service().create_version(session, file_id, content, change_source="system", commit=False)
        session.commit()


def test_scoped_safety_snapshot_reads_committed_content_not_cached_orm(pg_engine):
    _, project_id, file_id, snapshot_id, _, _ = _seed(pg_engine, "fresh-safety", scoped=True)
    with Session(pg_engine) as session:
        cached_file = session.get(File, file_id)  # Strong identity reference matters.
        assert cached_file.content == "Original"
        _write_new_content(pg_engine, file_id, "Latest committed", STAMP + timedelta(seconds=1))
        result = VersionService().rollback_to_snapshot(session, snapshot_id)
        safety = session.get(Snapshot, result["pre_rollback_snapshot_id"])
        ref = json.loads(safety.data)["file_versions"][0]
        assert get_file_version_service().get_content_at_version(session, file_id, ref["version_number"]) == "Latest committed"
        assert safety.project_id == project_id


@pytest.mark.parametrize("scoped", [False, True])
def test_restore_advances_actual_persisted_token_with_stale_identity(pg_engine, scoped):
    _, _, file_id, snapshot_id, _, _ = _seed(pg_engine, f"fresh-token-{scoped}", scoped=scoped)
    future_token = datetime(2040, 1, 1)
    with Session(pg_engine) as session:
        cached_file = session.get(File, file_id)
        assert cached_file.updated_at == STAMP
        _write_new_content(pg_engine, file_id, "Latest committed", future_token)
        VersionService().rollback_to_snapshot(session, snapshot_id)
    with Session(pg_engine) as reader:
        restored = reader.get(File, file_id)
        assert restored.content == "Original"
        assert restored.updated_at > future_token


def _pause_safety_snapshot(monkeypatch):
    paused, release = Event(), Event()
    original = VersionService.create_snapshot

    def gated(self, *args, **kwargs):
        if kwargs.get("snapshot_type") == "pre_rollback":
            paused.set()
            assert release.wait(timeout=8), "rollback safety barrier was not released"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(VersionService, "create_snapshot", gated)
    return paused, release


def _observed_lock_wait(engine, pid, future: Future):
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


def _rollback(engine, snapshot_id):
    with Session(engine) as session:
        return VersionService().rollback_to_snapshot(session, snapshot_id)


def _create_via(session, entrypoint, user_id, project_id, key_id):
    user = session.get(User, user_id)
    content = "Created after rollback"
    if entrypoint == "web":
        return files_api.create_file(project_id, files_api.FileCreate(title="Concurrent", content=content), BackgroundTasks(), current_user=user, session=session).id
    if entrypoint == "agent":
        key = session.get(AgentApiKey, key_id)
        return agent_api.create_file(project_id, agent_api.FileCreate(title="Concurrent", content=content), BackgroundTasks(), _rate_limit=0, context=(session, user_id, key)).id
    if entrypoint in {"tool", "tool-folder"}:
        kwargs = {} if entrypoint == "tool" else {"file_type": "draft", "parent_id": f"{project_id}-draft-folder"}
        return FileCRUD(session, user_id).create_file(project_id, "Concurrent", content=content, **kwargs)["id"]
    if entrypoint == "import":
        return material_import.import_material(MaterialImportRequest(
            project_id=project_id, novel_id=1, entity_type="characters", entity_id=1,
        ), current_user=user, session=session, accept_language="en").file_id
    upload = UploadFile(filename="concurrent.txt", file=BytesIO(content.encode()))
    if entrypoint == "material":
        return files_api.upload_material(project_id, upload, current_user=user, session=session).id
    result = files_api.upload_drafts(project_id, [upload], parent_id=None, current_user=user, session=session, background_tasks=BackgroundTasks())
    assert result.errors == []
    return result.files[0].id


def test_writer_holding_file_can_finish_version_before_waiting_rollback(pg_engine):
    _, _, file_id, snapshot_id, _, _ = _seed(pg_engine, "writer-first")
    file_locked, finish_write, rollback_started = Event(), Event(), Event()
    pid_box = []

    def write():
        with Session(pg_engine) as session:
            file = session.exec(
                select(File).where(File.id == file_id).with_for_update(key_share=True)
            ).one()
            file.content = "Committed before rollback"
            file.updated_at = STAMP + timedelta(seconds=1)
            session.add(file)
            session.flush()
            file_locked.set()
            assert finish_write.wait(timeout=8)
            get_file_version_service().create_version(
                session, file_id, file.content, change_source="system", commit=False
            )
            session.commit()

    def restore():
        with Session(pg_engine) as session:
            pid_box.append(session.exec(text("SELECT pg_backend_pid()")).scalar_one())
            rollback_started.set()
            return VersionService().rollback_to_snapshot(session, snapshot_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(write)
        try:
            assert file_locked.wait(timeout=5)
            rollback = pool.submit(restore)
            assert rollback_started.wait(timeout=5)
            blocked = _observed_lock_wait(pg_engine, pid_box[0], rollback)
        finally:
            finish_write.set()
        writer.result(timeout=10)  # A Project/File FK lock cycle must not abort it.
        result = rollback.result(timeout=10)
    assert blocked
    with Session(pg_engine) as reader:
        assert reader.get(File, file_id).content == "Original"
        safety = reader.get(Snapshot, result["pre_rollback_snapshot_id"])
        ref = json.loads(safety.data)["file_versions"][0]
        assert get_file_version_service().get_content_at_version(
            reader, file_id, ref["version_number"]
        ) == "Committed before rollback"


def test_rollback_locks_before_safety_snapshot_and_stale_writer_rejects(pg_engine, monkeypatch):
    user_id, _, file_id, snapshot_id, _, _ = _seed(pg_engine, "stale-writer")
    paused, release = _pause_safety_snapshot(monkeypatch)
    writer_started, pid_box = Event(), []

    def write():
        with Session(pg_engine) as session:
            user = session.get(User, user_id)
            cached_file = session.get(File, file_id)
            assert cached_file.updated_at == STAMP
            pid_box.append(session.exec(text("SELECT pg_backend_pid()")).scalar_one())
            writer_started.set()
            try:
                files_api.update_file(file_id, files_api.FileUpdate(content="Concurrent edit", base_updated_at=STAMP), BackgroundTasks(), current_user=user, session=session)
                return 200
            except APIException as exc:
                session.rollback()
                return exc.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        restore = pool.submit(_rollback, pg_engine, snapshot_id)
        try:
            assert paused.wait(timeout=5)
            writer = pool.submit(write)
            assert writer_started.wait(timeout=5)
            blocked = _observed_lock_wait(pg_engine, pid_box[0], writer)
        finally:
            release.set()
        restore.result(timeout=10)
        outcome = writer.result(timeout=10)
    assert blocked, "writer committed while rollback was paused before its safety snapshot"
    assert outcome == 409
    with Session(pg_engine) as reader:
        assert reader.get(File, file_id).content == "Original"


@pytest.mark.parametrize("entrypoint", ["web", "agent", "tool", "material", "draft", "import", "tool-folder"])
def test_create_waits_for_rollback_then_remains_active(pg_engine, monkeypatch, entrypoint):
    user_id, project_id, _, snapshot_id, key_id, _ = _seed(pg_engine, f"create-{entrypoint}")
    paused, release = _pause_safety_snapshot(monkeypatch)
    creator_started, pid_box = Event(), []

    def create():
        with Session(pg_engine) as session:
            pid_box.append(session.exec(text("SELECT pg_backend_pid()")).scalar_one())
            creator_started.set()
            return _create_via(session, entrypoint, user_id, project_id, key_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        restore = pool.submit(_rollback, pg_engine, snapshot_id)
        try:
            assert paused.wait(timeout=5)
            creator = pool.submit(create)
            assert creator_started.wait(timeout=5)
            blocked = _observed_lock_wait(pg_engine, pid_box[0], creator)
        finally:
            release.set()
        restore.result(timeout=10)
        file_id = creator.result(timeout=10)
    assert blocked, "creation committed before the paused rollback's safety snapshot"
    with Session(pg_engine) as reader:
        file = reader.get(File, file_id)
        assert file.is_deleted is False
        assert file.content == "Created after rollback"


@pytest.mark.parametrize("entrypoint", ["material", "draft", "tool-folder"])
def test_canonical_folder_repair_holds_gate_until_content_commit(pg_engine, monkeypatch, entrypoint):
    user_id, project_id, _, snapshot_id, key_id, _ = _seed(pg_engine, f"gate-{entrypoint}")
    folder_ready, finish_create, rollback_started = Event(), Event(), Event()
    pid_box = []

    def gate_after_folder(original):
        def wrapped(*args, **kwargs):
            result = original(*args, **kwargs)
            folder_ready.set()
            assert finish_create.wait(timeout=8)
            return result
        return wrapped

    if entrypoint == "tool-folder":
        monkeypatch.setattr(FileCRUD, "_normalize_parent_to_folder", gate_after_folder(FileCRUD._normalize_parent_to_folder))
    else:
        name = "_ensure_material_folder" if entrypoint == "material" else "_ensure_draft_folder"
        monkeypatch.setattr(files_api, name, gate_after_folder(getattr(files_api, name)))

    def create():
        with Session(pg_engine) as session:
            return _create_via(session, entrypoint, user_id, project_id, key_id)

    def restore():
        with Session(pg_engine) as session:
            pid_box.append(session.exec(text("SELECT pg_backend_pid()")).scalar_one())
            rollback_started.set()
            return VersionService().rollback_to_snapshot(session, snapshot_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        creator = pool.submit(create)
        try:
            assert folder_ready.wait(timeout=5)
            rollback = pool.submit(restore)
            assert rollback_started.wait(timeout=5)
            blocked = _observed_lock_wait(pg_engine, pid_box[0], rollback)
        finally:
            finish_create.set()
        created_id = creator.result(timeout=10)
        result = rollback.result(timeout=10)
    assert blocked, "folder repair committed and released the logical creation gate"
    with Session(pg_engine) as reader:
        file = reader.get(File, created_id)
        # This creation finished first, so rollback intentionally hides it. Its
        # complete content must exist in the safety snapshot for recovery.
        assert file.is_deleted is True
        safety = reader.get(Snapshot, result["pre_rollback_snapshot_id"])
        refs = {ref["file_id"]: ref for ref in json.loads(safety.data)["file_versions"]}
        assert created_id in refs
        assert get_file_version_service().get_content_at_version(
            reader, created_id, refs[created_id]["version_number"]
        ) == "Created after rollback"


def test_create_revalidates_cached_parent_deleted_by_rollback(pg_engine, monkeypatch):
    user_id, project_id, _, snapshot_id, _, parent_id = _seed(pg_engine, "deleted-parent", with_extra_folder=True)
    paused, release = _pause_safety_snapshot(monkeypatch)
    creator_started, pid_box = Event(), []

    def create():
        with Session(pg_engine) as session:
            user = session.get(User, user_id)
            cached_parent = session.get(File, parent_id)
            assert cached_parent.is_deleted is False
            pid_box.append(session.exec(text("SELECT pg_backend_pid()")).scalar_one())
            creator_started.set()
            try:
                created = files_api.create_file(project_id, files_api.FileCreate(title="Concurrent child", parent_id=parent_id, content="Child"), BackgroundTasks(), current_user=user, session=session)
                return 200, created.id
            except APIException as exc:
                session.rollback()
                return exc.status_code, None

    with ThreadPoolExecutor(max_workers=2) as pool:
        restore = pool.submit(_rollback, pg_engine, snapshot_id)
        try:
            assert paused.wait(timeout=5)
            creator = pool.submit(create)
            assert creator_started.wait(timeout=5)
            blocked = _observed_lock_wait(pg_engine, pid_box[0], creator)
        finally:
            release.set()
        restore.result(timeout=10)
        outcome, _ = creator.result(timeout=10)
    assert blocked
    assert outcome == 400  # Preserve the existing typed FILE_NOT_FOUND contract.
    with Session(pg_engine) as reader:
        assert reader.get(File, parent_id).is_deleted is True
        assert reader.exec(select(File).where(File.project_id == project_id, File.title == "Concurrent child")).all() == []


def test_api_reconciliation_includes_creator_committed_while_rollback_waits(pg_engine, monkeypatch):
    user_id, project_id, _, snapshot_id, key_id, _ = _seed(pg_engine, "reconcile-wait")
    creator_ready, finish_create, rollback_started = Event(), Event(), Event()
    pid_box, reconciled = [], []
    original_initial = FileVersionService.create_initial_version

    def pause_after_baseline(self, *args, **kwargs):
        result = original_initial(self, *args, **kwargs)
        creator_ready.set()
        assert finish_create.wait(timeout=8)
        return result

    monkeypatch.setattr(FileVersionService, "create_initial_version", pause_after_baseline)
    monkeypatch.setattr(snapshots_api, "_schedule_snapshot_rollback_reconciliation", lambda **kwargs: reconciled.append(kwargs["file_ids"]))

    def create():
        with Session(pg_engine) as session:
            return _create_via(session, "web", user_id, project_id, key_id)

    def restore():
        with Session(pg_engine) as session:
            user = session.get(User, user_id)
            pid_box.append(session.exec(text("SELECT pg_backend_pid()")).scalar_one())
            rollback_started.set()
            return snapshots_api.rollback_to_snapshot(snapshot_id, BackgroundTasks(), current_user=user, session=session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        creator = pool.submit(create)
        try:
            assert creator_ready.wait(timeout=5)
            rollback = pool.submit(restore)
            assert rollback_started.wait(timeout=5)
            blocked = _observed_lock_wait(pg_engine, pid_box[0], rollback)
        finally:
            finish_create.set()
        created_id = creator.result(timeout=10)
        result = rollback.result(timeout=10)
    assert blocked
    assert len(reconciled) == 1
    assert created_id in reconciled[0]
    assert set(result) == {"snapshot_id", "pre_rollback_snapshot_id", "restored"}
    with Session(pg_engine) as reader:
        assert reader.get(File, created_id).is_deleted is True


@pytest.mark.parametrize("action", ["create", "rollback"])
@pytest.mark.parametrize("project_change", ["deleted", "new-owner"])
def test_snapshot_api_rechecks_project_access_after_gate(pg_engine, action, project_change):
    suffix = f"access-{action}-{project_change}"
    user_id, project_id, file_id, snapshot_id, _, _ = _seed(pg_engine, suffix)
    _write_new_content(pg_engine, file_id, "Keep latest", STAMP + timedelta(seconds=1))
    with Session(pg_engine) as session:
        recipient = User(username=f"recipient-{suffix}", email=f"recipient-{suffix}@example.test", hashed_password="unused")
        session.add(recipient)
        session.commit()
        recipient_id = recipient.id
        before_count = len(session.exec(select(Snapshot).where(Snapshot.project_id == project_id)).all())
    changed, finish_change, requested = Event(), Event(), Event()
    pid_box = []

    def change_project():
        with Session(pg_engine) as session:
            project = session.exec(select(Project).where(Project.id == project_id).with_for_update()).one()
            if project_change == "deleted":
                project.is_deleted = True
            else:
                project.owner_id = recipient_id
            session.add(project)
            session.flush()
            changed.set()
            assert finish_change.wait(timeout=8)
            session.commit()

    def snapshot_request():
        with Session(pg_engine) as session:
            cached_project = session.get(Project, project_id)
            assert not cached_project.is_deleted and cached_project.owner_id == user_id
            user = session.get(User, user_id)
            pid_box.append(session.exec(text("SELECT pg_backend_pid()")).scalar_one())
            requested.set()
            try:
                if action == "create":
                    snapshots_api.create_snapshot(project_id, snapshots_api.CreateSnapshotRequest(file_id=file_id), current_user=user, session=session)
                else:
                    snapshots_api.rollback_to_snapshot(snapshot_id, BackgroundTasks(), current_user=user, session=session)
            except APIException as exc:
                session.rollback()
                return exc.status_code
            return 200

    with ThreadPoolExecutor(max_workers=2) as pool:
        changer = pool.submit(change_project)
        try:
            assert changed.wait(timeout=5)
            request = pool.submit(snapshot_request)
            assert requested.wait(timeout=5)
            blocked = _observed_lock_wait(pg_engine, pid_box[0], request)
        finally:
            finish_change.set()
        changer.result(timeout=10)
        outcome = request.result(timeout=10)
    assert blocked
    assert outcome == (404 if project_change == "deleted" else 403)
    with Session(pg_engine) as reader:
        assert reader.get(File, file_id).content == "Keep latest"
        assert len(reader.exec(select(Snapshot).where(Snapshot.project_id == project_id)).all()) == before_count


@pytest.mark.parametrize("target", ["restored", "deleted"])
def test_scoped_snapshot_validates_and_gathers_after_rollback(pg_engine, monkeypatch, target):
    user_id, project_id, file_id, snapshot_id, _, folder_id = _seed(pg_engine, f"scoped-{target}", with_extra_folder=True)
    _write_new_content(pg_engine, file_id, "Before rollback", STAMP + timedelta(seconds=1))
    target_id = file_id if target == "restored" else folder_id
    cached, start_request, requested = Event(), Event(), Event()
    paused, release = _pause_safety_snapshot(monkeypatch)
    pid_box = []

    def create():
        with Session(pg_engine) as session:
            user = session.get(User, user_id)
            cached_file = session.get(File, target_id)
            assert not cached_file.is_deleted
            cached.set()
            assert start_request.wait(timeout=8)
            pid_box.append(session.exec(text("SELECT pg_backend_pid()")).scalar_one())
            requested.set()
            try:
                snapshot = snapshots_api.create_snapshot(project_id, snapshots_api.CreateSnapshotRequest(file_id=target_id), current_user=user, session=session)
            except APIException as exc:
                session.rollback()
                return exc.status_code, None
            return 200, snapshot.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        creator = pool.submit(create)
        try:
            assert cached.wait(timeout=5)
            rollback = pool.submit(_rollback, pg_engine, snapshot_id)
            assert paused.wait(timeout=5)
            start_request.set()
            assert requested.wait(timeout=5)
            blocked = _observed_lock_wait(pg_engine, pid_box[0], creator)
        finally:
            start_request.set()
            release.set()
        rollback.result(timeout=10)
        outcome, created_id = creator.result(timeout=10)
    assert blocked
    if target == "deleted":
        assert outcome == 400
    else:
        assert outcome == 200
        with Session(pg_engine) as reader:
            data = json.loads(reader.get(Snapshot, created_id).data)
            ref = data["file_versions"][0]
            assert get_file_version_service().get_content_at_version(reader, target_id, ref["version_number"]) == "Original"
            assert reader.get(File, target_id).content == "Original"


@pytest.mark.parametrize("entrypoint", ["material", "draft", "tool-folder"])
def test_concurrent_canonical_folder_duplicate_preserves_creation_gate(pg_engine, monkeypatch, entrypoint):
    user_id, project_id, _, snapshot_id, key_id, _ = _seed(pg_engine, f"duplicate-{entrypoint}")
    both_missing = Barrier(2)
    duplicate_ready, finish_duplicate, rollback_started = Event(), Event(), Event()
    pid_box = []
    module = crud_module if entrypoint == "tool-folder" else files_api
    original_savepoint = module.begin_file_creation_savepoint

    @contextmanager
    def synchronized_savepoint(session):
        both_missing.wait(timeout=5)
        try:
            with original_savepoint(session):
                yield
        except IntegrityError:
            session.info["canonical_duplicate"] = True
            raise

    monkeypatch.setattr(module, "begin_file_creation_savepoint", synchronized_savepoint)

    def pause_duplicate(session):
        if session.info.get("canonical_duplicate"):
            duplicate_ready.set()
            assert finish_duplicate.wait(timeout=8)

    if entrypoint == "tool-folder":
        original_version = FileCRUD._create_version

        def pause_tool_version(self, *args, **kwargs):
            result = original_version(self, *args, **kwargs)
            pause_duplicate(self.session)
            return result

        monkeypatch.setattr(FileCRUD, "_create_version", pause_tool_version)
    else:
        original_initial = FileVersionService.create_initial_version

        def pause_initial(self, session, *args, **kwargs):
            result = original_initial(self, session, *args, **kwargs)
            pause_duplicate(session)
            return result

        monkeypatch.setattr(FileVersionService, "create_initial_version", pause_initial)

    def create():
        with Session(pg_engine) as session:
            return _create_via(session, entrypoint, user_id, project_id, key_id)

    def restore():
        with Session(pg_engine) as session:
            pid_box.append(session.exec(text("SELECT pg_backend_pid()")).scalar_one())
            rollback_started.set()
            return VersionService().rollback_to_snapshot(session, snapshot_id)

    with ThreadPoolExecutor(max_workers=3) as pool:
        creators = [pool.submit(create) for _ in range(2)]
        try:
            assert duplicate_ready.wait(timeout=5), "duplicate insert did not reach savepoint recovery"
            rollback = pool.submit(restore)
            assert rollback_started.wait(timeout=5)
            blocked = _observed_lock_wait(pg_engine, pid_box[0], rollback)
        finally:
            finish_duplicate.set()
        created_ids = {future.result(timeout=10) for future in creators}
        result = rollback.result(timeout=10)
    assert blocked, "duplicate recovery released its enclosing Project SHARE gate"
    assert len(created_ids) == 2
    with Session(pg_engine) as reader:
        folders = reader.exec(select(File).where(File.project_id == project_id, File.file_type == "folder")).all()
        assert len(folders) == 1
        assert all(reader.get(File, file_id).is_deleted for file_id in created_ids)
        safety = json.loads(reader.get(Snapshot, result["pre_rollback_snapshot_id"]).data)
        assert created_ids <= {ref["file_id"] for ref in safety["file_versions"]}


def test_scoped_rollback_does_not_block_unrelated_web_writer(pg_engine, monkeypatch):
    user_id, project_id, _, snapshot_id, _, _ = _seed(pg_engine, "unrelated-writer", scoped=True)
    with Session(pg_engine) as session:
        unrelated = File(project_id=project_id, title="Unrelated", file_type="draft", content="Unrelated", updated_at=STAMP)
        session.add(unrelated)
        session.flush()
        get_file_version_service().create_initial_version(session, unrelated)
        session.commit()
        unrelated_id = unrelated.id
    paused, release = _pause_safety_snapshot(monkeypatch)

    def update():
        with Session(pg_engine) as session:
            user = session.get(User, user_id)
            return files_api.update_file(unrelated_id, files_api.FileUpdate(content="Independent edit", base_updated_at=STAMP), BackgroundTasks(), current_user=user, session=session).content

    with ThreadPoolExecutor(max_workers=2) as pool:
        rollback = pool.submit(_rollback, pg_engine, snapshot_id)
        try:
            assert paused.wait(timeout=5)
            writer = pool.submit(update)
            assert writer.result(timeout=4) == "Independent edit"
        finally:
            release.set()
        rollback.result(timeout=10)
    with Session(pg_engine) as reader:
        assert reader.get(File, unrelated_id).content == "Independent edit"
        assert reader.get(File, unrelated_id).is_deleted is False
