"""Restore physically missing folder trees without depending on row/hash order."""

import json
import os
from datetime import datetime
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel, select

from models import File, Project, Snapshot, User
from services.features.snapshot_service import VersionService
from tests.test_services.test_snapshot_concurrency_postgres import TABLES
from tests.test_services.test_snapshot_concurrency_postgres import pg_engine as pg_engine

STAMP = datetime(2026, 10, 6, 14, 0, 0, 123456)


@pytest.fixture
def hierarchy_engine():
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        yield engine
    finally:
        engine.dispose()


def _seed(session, metadata_order="child_first", existing_root=False, scope=None):
    suffix = uuid4().hex
    user = User(username=suffix, email=f"{suffix}@example.test", hashed_password="unused")
    session.add(user)
    session.flush()
    project = Project(name="Missing hierarchy", owner_id=user.id)
    session.add(project)
    session.flush()
    # Derive roles from the old set iteration: the regression is deterministic
    # across Python hash seeds instead of hoping a named child happens to be first.
    leaf_id, branch_id, root_id = list({f"{suffix}-{i}" for i in range(3)})
    metadata = {
        root_id: {"id": root_id, "title": "Root", "file_type": "folder", "parent_id": None, "order": 3},
        branch_id: {"id": branch_id, "title": "Branch", "file_type": "folder", "parent_id": root_id, "order": 2},
        leaf_id: {"id": leaf_id, "title": "Leaf", "file_type": "folder", "parent_id": branch_id, "order": 1},
    }
    if existing_root or scope == "existing_parent":
        session.add(File(id=root_id, project_id=project.id, title="Existing root", file_type="folder", is_deleted=existing_root, updated_at=STAMP))
        session.flush()
    if scope == "existing_parent":
        session.add(File(id=branch_id, project_id=project.id, title="Existing branch", file_type="folder", parent_id=root_id, updated_at=STAMP))
        session.flush()
    if scope:
        # Snapshot.file_id has a real FK. Scoped restore targets an existing row;
        # only the historical parent may be absent without violating that FK.
        session.add(File(id=leaf_id, project_id=project.id, title="Existing leaf", file_type="folder", parent_id=None, updated_at=STAMP))
        session.flush()
    ids = [leaf_id, branch_id, root_id] if metadata_order == "child_first" else [root_id, branch_id, leaf_id]
    snapshot = Snapshot(
        project_id=project.id,
        file_id=leaf_id if scope else None,
        snapshot_type="manual",
        data=json.dumps({"version": 3, "files_metadata": [metadata[file_id] for file_id in ids], "file_versions": []}),
    )
    session.add(snapshot)
    session.commit()
    return project.id, snapshot.id, root_id, branch_id, leaf_id


def _assert_hierarchy(session, ids, expected_recreated, expected_undeleted):
    project_id, snapshot_id, root_id, branch_id, leaf_id = ids
    result = VersionService().rollback_to_snapshot(session, snapshot_id)
    session.expire_all()
    assert session.get(File, root_id).parent_id is None
    assert session.get(File, branch_id).parent_id == root_id
    assert session.get(File, leaf_id).parent_id == branch_id
    assert all(session.get(File, file_id).project_id == project_id for file_id in [root_id, branch_id, leaf_id])
    assert result["restored"] == {
        "files": 0, "recreated_files": expected_recreated, "undeleted_files": expected_undeleted,
        "deleted_extra_files": 0, "restore_versions": 0,
    }
    return result


@pytest.mark.parametrize("autoflush", [False, True])
@pytest.mark.parametrize("metadata_order", ["child_first", "parent_first"])
def test_hard_missing_three_level_hierarchy_is_restored(hierarchy_engine, monkeypatch, autoflush, metadata_order):
    monkeypatch.setattr("database.is_postgres", False)
    monkeypatch.setattr("services.features.snapshot_service.utcnow", lambda: STAMP)
    with Session(hierarchy_engine, autoflush=autoflush, expire_on_commit=False) as session:
        ids = _seed(session, metadata_order=metadata_order)
        _assert_hierarchy(session, ids, 3, 0)
        for file_id in ids[2:]:
            file = session.get(File, file_id)
            assert file.created_at == file.updated_at == STAMP


@pytest.mark.parametrize("autoflush", [False, True])
def test_missing_children_link_to_undeleted_existing_parent(hierarchy_engine, monkeypatch, autoflush):
    monkeypatch.setattr("database.is_postgres", False)
    monkeypatch.setattr("services.features.snapshot_service.utcnow", lambda: STAMP)
    with Session(hierarchy_engine, autoflush=autoflush, expire_on_commit=False) as session:
        ids = _seed(session, existing_root=True)
        _assert_hierarchy(session, ids, 2, 1)
        root = session.get(File, ids[2])
        assert root.title == "Root"
        assert root.is_deleted is False
        assert root.updated_at > STAMP


@pytest.mark.parametrize("scope", ["existing_parent", "missing_parent"])
def test_scoped_recreation_preserves_parent_scope_policy(hierarchy_engine, monkeypatch, scope):
    monkeypatch.setattr("database.is_postgres", False)
    with Session(hierarchy_engine, autoflush=False, expire_on_commit=False) as session:
        _, snapshot_id, root_id, branch_id, leaf_id = _seed(session, scope=scope)
        result = VersionService().rollback_to_snapshot(session, snapshot_id)
        session.expire_all()
        assert session.get(File, leaf_id).parent_id == (branch_id if scope == "existing_parent" else None)
        assert result["restored"]["recreated_files"] == 0
        if scope == "existing_parent":
            root, branch = session.get(File, root_id), session.get(File, branch_id)
            assert root.title == "Existing root" and root.updated_at == STAMP
            assert branch.title == "Existing branch" and branch.updated_at == STAMP
        else:
            assert session.get(File, root_id) is None
            assert session.get(File, branch_id) is None


def _assert_atomic_phase_two_failure(session, ids):
    project_id, snapshot_id, _, branch_id, _ = ids
    reached = []

    def fail_parent_assignment(_mapper, connection, file):
        if file.id == branch_id and file.parent_id:
            reached.append(connection.execute(select(File.id).where(File.project_id == project_id)).all())
            raise RuntimeError("parent assignment failed")

    event.listen(File, "before_update", fail_parent_assignment)
    try:
        with pytest.raises(RuntimeError, match="parent assignment failed"):
            VersionService().rollback_to_snapshot(session, snapshot_id)
    finally:
        event.remove(File, "before_update", fail_parent_assignment)
    assert len(reached) == 1 and len(reached[0]) == 3
    assert session.exec(select(File).where(File.project_id == project_id)).all() == []
    snapshots = session.exec(select(Snapshot).where(Snapshot.project_id == project_id)).all()
    assert len(snapshots) == 1 and snapshots[0].id == snapshot_id


@pytest.mark.parametrize("autoflush", [False, True])
def test_parent_assignment_failure_rolls_back_all_recreated_rows(hierarchy_engine, monkeypatch, autoflush):
    monkeypatch.setattr("database.is_postgres", False)
    with Session(hierarchy_engine, autoflush=autoflush, expire_on_commit=False) as session:
        _assert_atomic_phase_two_failure(session, _seed(session))


@pytest.mark.skipif(not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="isolated PostgreSQL URL not configured")
@pytest.mark.parametrize("autoflush", [False, True])
def test_hard_missing_hierarchy_with_postgres_foreign_keys(pg_engine, monkeypatch, autoflush):
    monkeypatch.setattr("database.is_postgres", True)
    with Session(pg_engine, autoflush=autoflush, expire_on_commit=False) as session:
        _assert_hierarchy(session, _seed(session), 3, 0)


@pytest.mark.skipif(not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="isolated PostgreSQL URL not configured")
@pytest.mark.parametrize("autoflush", [False, True])
def test_postgres_phase_two_failure_is_atomic(pg_engine, monkeypatch, autoflush):
    monkeypatch.setattr("database.is_postgres", True)
    with Session(pg_engine, autoflush=autoflush, expire_on_commit=False) as session:
        _assert_atomic_phase_two_failure(session, _seed(session))
