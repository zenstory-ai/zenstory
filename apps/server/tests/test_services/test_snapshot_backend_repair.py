"""Regression locks for M06 snapshot fidelity, atomicity, and metadata diffs."""

import json
from datetime import datetime, timedelta

import pytest
from sqlmodel import Session, func, select

from config.datetime_utils import utcnow
from models import File, FileVersion, Project, Snapshot, User
from models.file_version import CHANGE_TYPE_AUTO_SAVE, CHANGE_TYPE_RESTORE
from services.features import snapshot_service as snapshot_module
from services.features.file_version_service import FileVersionService, get_file_version_service
from services.features.snapshot_service import VersionService


def _project_with_files(db_session: Session, contents: tuple[str, ...]) -> tuple[Project, list[File]]:
    user = User(
        email=f"m06-{len(contents)}@example.com",
        username=f"m06-{len(contents)}",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    project = Project(name="M06", owner_id=user.id)
    db_session.add(project)
    db_session.flush()

    files: list[File] = []
    for index, content in enumerate(contents):
        file = File(
            title=f"File {index}",
            content=content,
            file_type="draft",
            project_id=project.id,
            user_id=user.id,
            order=index,
        )
        db_session.add(file)
        db_session.flush()
        db_session.add(
            FileVersion(
                file_id=file.id,
                project_id=project.id,
                version_number=1,
                content=content,
                word_count=1,
                char_count=len(content),
                is_base_version=True,
            )
        )
        files.append(file)
    db_session.commit()
    return project, files


@pytest.mark.unit
def test_snapshot_pins_live_content_when_latest_version_is_stale(db_session: Session):
    service = VersionService()
    project, files = _project_with_files(db_session, ("A",))
    file = files[0]

    file.content = "B"
    db_session.add(file)
    db_session.commit()

    snapshot = service.create_snapshot(db_session, project.id)
    snapshot_data = json.loads(snapshot.data)
    reference = snapshot_data["file_versions"][0]
    assert get_file_version_service().get_content_at_version(
        db_session, file.id, reference["version_number"]
    ) == "B"

    file.content = "C"
    db_session.add(file)
    db_session.commit()
    service.rollback_to_snapshot(db_session, snapshot.id)
    db_session.refresh(file)
    assert file.content == "B"


def test_scoped_snapshot_gather_refreshes_cached_deleted_file(db_session):
    project, files = _project_with_files(db_session, ("Original",))
    cached = files[0]
    with Session(db_session.get_bind()) as writer:
        current = writer.get(File, cached.id)
        current.is_deleted = True
        writer.add(current)
        writer.commit()
    assert cached.is_deleted is False
    snapshot = VersionService().create_snapshot(db_session, project.id, file_id=cached.id)
    data = json.loads(snapshot.data)
    assert data["files_metadata"] == []
    assert data["file_versions"] == []


def test_aged_auto_save_delta_chain_stays_replayable_and_snapshot_pinned(db_session: Session):
    project, files = _project_with_files(db_session, ("A\nB\nC",))
    file = files[0]
    file_service = get_file_version_service()
    contents = ["A\nB\nC", "A\nB2\nC", "A\nB2\nC3"]
    versions = [file_service.get_latest_version(db_session, file.id)]
    versions[0].change_type = CHANGE_TYPE_AUTO_SAVE
    versions[0].created_at = utcnow() - timedelta(days=60)
    db_session.add(versions[0])
    db_session.commit()
    for content in contents[1:]:
        version = file_service.create_version(db_session, file.id, content, change_type=CHANGE_TYPE_AUTO_SAVE)
        version.created_at = utcnow() - timedelta(days=60)
        versions.append(version)
        db_session.add(version)
        db_session.commit()
    assert [version.is_base_version for version in versions] == [True, False, False]
    file.content = contents[-1]
    db_session.add(file)
    db_session.commit()
    service = VersionService()
    snapshot = service.create_snapshot(db_session, project.id)
    reference = json.loads(snapshot.data)["file_versions"][0]
    assert reference["version_number"] == 3
    assert file_service.get_version_count(db_session, file.id) == 3
    assert [file_service.get_content_at_version(db_session, file.id, number) for number in range(1, 4)] == contents
    file.content = "later content"
    db_session.add(file)
    db_session.commit()
    service.rollback_to_snapshot(db_session, snapshot.id)
    db_session.refresh(file)
    assert file.content == contents[-1]
    assert [file_service.get_content_at_version(db_session, file.id, number) for number in range(1, 4)] == contents


@pytest.mark.unit
def test_project_rollback_failure_is_fully_atomic(db_session: Session, monkeypatch):
    service = VersionService()
    project, files = _project_with_files(db_session, ("A1", "A2"))
    snapshot = service.create_snapshot(db_session, project.id)

    for index, file in enumerate(files, start=1):
        file.content = f"B{index}"
        db_session.add(file)
    db_session.commit()

    version_count_before = db_session.exec(select(func.count(FileVersion.id))).one()
    snapshot_count_before = db_session.exec(select(func.count(Snapshot.id))).one()
    original_create_version = FileVersionService.create_version
    restore_calls = 0

    def fail_second_restore(self, *args, **kwargs):
        nonlocal restore_calls
        if kwargs.get("change_type") == CHANGE_TYPE_RESTORE:
            restore_calls += 1
            if restore_calls == 2:
                raise RuntimeError("injected second-file restore failure")
        return original_create_version(self, *args, **kwargs)

    monkeypatch.setattr(FileVersionService, "create_version", fail_second_restore)

    with pytest.raises(RuntimeError, match="injected second-file restore failure"):
        service.rollback_to_snapshot(db_session, snapshot.id)

    db_session.expire_all()
    assert [db_session.get(File, file.id).content for file in files] == ["B1", "B2"]
    assert db_session.exec(select(func.count(FileVersion.id))).one() == version_count_before
    assert db_session.exec(select(func.count(Snapshot.id))).one() == snapshot_count_before


@pytest.mark.parametrize("mutation", ["content", "undelete", "metadata", "soft_delete"])
def test_snapshot_rollback_advances_future_file_tokens(db_session: Session, monkeypatch, mutation):
    service = VersionService()
    project, files = _project_with_files(db_session, ("original 1", "original 2"))
    folder = File(project_id=project.id, title="Original folder", file_type="folder")
    db_session.add(folder)
    db_session.commit()
    target = service.create_snapshot(db_session, project.id)
    frozen = datetime(2040, 1, 1)
    files[0].content = "later content"
    files[1].is_deleted = True
    files[1].deleted_at = frozen
    folder.title = "Renamed folder"
    extra = File(project_id=project.id, title="Later file", content="extra", file_type="draft")
    changed_files = dict(zip(["content", "undelete", "metadata", "soft_delete"], [*files, folder, extra], strict=True))
    for index, file in enumerate(changed_files.values()):
        file.updated_at = frozen + timedelta(days=index + 1)
        db_session.add(file)
    db_session.commit()
    selected = changed_files[mutation]
    previous_token = selected.updated_at
    monkeypatch.setattr(snapshot_module, "utcnow", lambda: frozen)

    service.rollback_to_snapshot(db_session, target.id)
    for file in changed_files.values():
        db_session.refresh(file)
    assert files[0].content == "original 1"
    assert files[1].is_deleted is False
    assert folder.title == "Original folder"
    assert extra.is_deleted is True
    assert selected.updated_at > previous_token


@pytest.mark.unit
def test_compare_snapshots_includes_metadata_and_folder_changes(db_session: Session):
    service = VersionService()
    project, files = _project_with_files(db_session, ("same",))
    file = files[0]
    old = Snapshot(
        project_id=project.id,
        version=3,
        created_at=utcnow() - timedelta(seconds=1),
        data=json.dumps(
            {
                "version": 3,
                "file_versions": [{"file_id": file.id, "version_number": 1}],
                "files_metadata": [{"id": file.id, "title": "Old", "file_type": "draft", "parent_id": None, "order": 0}],
            }
        ),
    )
    folder_id = "folder-added"
    new = Snapshot(
        project_id=project.id,
        version=3,
        created_at=utcnow(),
        data=json.dumps(
            {
                "version": 3,
                "file_versions": [{"file_id": file.id, "version_number": 1}],
                "files_metadata": [
                    {"id": file.id, "title": "Renamed", "file_type": "script", "parent_id": folder_id, "order": 2},
                    {"id": folder_id, "title": "Folder", "file_type": "folder", "parent_id": None, "order": 0},
                ],
            }
        ),
    )
    db_session.add(old)
    db_session.add(new)
    db_session.commit()

    changes = service.compare_snapshots(db_session, snapshot1=old, snapshot2=new)["changes"]

    assert [entry["file_id"] for entry in changes["added"]] == [folder_id]
    modified = next(entry for entry in changes["modified"] if entry["file_id"] == file.id)
    assert set(modified["metadata_changes"]) == {"title", "file_type", "parent_id", "order"}
    assert modified["old_title"] == "Old"
    assert modified["new_title"] == "Renamed"
    assert modified["old_file_type"] == "draft"
    assert modified["new_file_type"] == "script"


@pytest.mark.parametrize("has_metadata", [True, False])
def test_compare_content_changes_carry_historical_metadata_without_live_reads(db_session, monkeypatch, has_metadata):
    data = {"file_versions": [{"file_id": "historical-file", "version_number": 1}]}
    if has_metadata:
        data["files_metadata"] = [{"id": "historical-file", "title": "Historical title", "file_type": "draft"}]
    old = Snapshot(project_id="project", created_at=utcnow() - timedelta(seconds=1), data=json.dumps(data))
    data["file_versions"][0]["version_number"] = 2
    new = Snapshot(project_id="project", created_at=utcnow(), data=json.dumps(data))

    def reject_live_read(*args, **kwargs):
        pytest.fail("A comparison with supplied snapshots must not read live file metadata")

    monkeypatch.setattr(db_session, "get", reject_live_read)
    modified = VersionService().compare_snapshots(db_session, snapshot1=new, snapshot2=old)["changes"]["modified"][0]
    assert modified["old_version"] == 1
    assert modified["new_version"] == 2
    assert modified["old_title"] == modified["new_title"] == ("Historical title" if has_metadata else None)
    assert modified["old_file_type"] == modified["new_file_type"] == ("draft" if has_metadata else None)
    assert "metadata_changes" not in modified
