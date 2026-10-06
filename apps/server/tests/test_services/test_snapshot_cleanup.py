"""Snapshot retention must preserve versions and detach nullable links atomically."""

import json
import os
from datetime import timedelta

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from config.datetime_utils import utcnow
from models import File, FileVersion, Project, Snapshot, User
from services.features.file_version_service import get_file_version_service
from services.features.snapshot_service import VersionService

TABLES = [User.__table__, Project.__table__, File.__table__, Snapshot.__table__, FileVersion.__table__]


@pytest.fixture(params=["sqlite", "postgres"])
def cleanup_session(request, tmp_path):
    if request.param == "postgres":
        url = os.getenv("ZENSTORY_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Requires an isolated ZENSTORY_TEST_POSTGRES_URL")
    else:
        url = f"sqlite:///{tmp_path / 'snapshot-cleanup.db'}"
    engine = create_engine(url)
    if request.param == "sqlite":
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        with Session(engine) as session:
            yield session
    finally:
        SQLModel.metadata.drop_all(engine, tables=TABLES)
        engine.dispose()


def seed(session):
    user = User(username="snapshot-cleanup", email="cleanup@example.com", hashed_password="local-test")
    session.add(user)
    session.flush()
    project = Project(name="cleanup", owner_id=user.id)
    session.add(project)
    session.flush()
    file = File(project_id=project.id, title="retained", content="B", file_type="draft")
    session.add(file)
    session.flush()
    old = Snapshot(project_id=project.id, created_at=utcnow() - timedelta(days=60), data=json.dumps({"file_versions": [{"file_id": file.id, "version_number": 1}]}))
    recent = Snapshot(project_id=project.id, data=json.dumps({"file_versions": [{"file_id": file.id, "version_number": 2}]}))
    session.add(old)
    session.add(recent)
    session.flush()
    versions = [
        FileVersion(file_id=file.id, project_id=project.id, version_number=1, is_base_version=True, content="A", snapshot_id=old.id),
        FileVersion(file_id=file.id, project_id=project.id, version_number=2, is_base_version=True, content="B", snapshot_id=recent.id),
    ]
    session.add_all(versions)
    session.commit()
    return project.id, file.id, old.id, recent.id, [version.id for version in versions]


def test_cleanup_detaches_only_deleted_snapshot_links_and_preserves_versions(cleanup_session):
    session = cleanup_session
    if session.bind.dialect.name == "sqlite":
        assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
    project_id, file_id, old_id, recent_id, version_ids = seed(session)
    deleted = VersionService().cleanup_old_snapshots(session, project_id, keep_recent=0, keep_days=30)
    assert deleted == 1
    session.expire_all()
    assert session.get(Snapshot, old_id) is None
    assert session.get(Snapshot, recent_id) is not None
    assert session.get(FileVersion, version_ids[0]).snapshot_id is None
    assert session.get(FileVersion, version_ids[1]).snapshot_id == recent_id
    assert len(session.exec(select(FileVersion)).all()) == 2
    service = get_file_version_service()
    assert service.get_content_at_version(session, file_id, 1) == "A"
    assert service.get_content_at_version(session, file_id, 2) == "B"


def test_cleanup_failure_rolls_back_detach_and_delete(cleanup_session, monkeypatch):
    session = cleanup_session
    project_id, _file_id, old_id, recent_id, version_ids = seed(session)

    def fail_commit():
        session.flush()
        raise RuntimeError("injected cleanup commit failure")

    monkeypatch.setattr(session, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="injected cleanup commit failure"):
        VersionService().cleanup_old_snapshots(session, project_id, keep_recent=0, keep_days=30)
    assert not session.in_transaction()
    with Session(session.bind) as reader:
        assert reader.get(Snapshot, old_id) is not None
        assert reader.get(Snapshot, recent_id) is not None
        assert reader.get(FileVersion, version_ids[0]).snapshot_id == old_id
        assert len(reader.exec(select(FileVersion)).all()) == 2
