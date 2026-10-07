"""Canonical recovery tokens, no-op preservation and transaction ownership."""

from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks, UploadFile
from sqlmodel import Session, select

from agent.tools.file_ops import crud as crud_module
from agent.tools.file_ops.crud import FileCRUD
from api import files as files_api
from config.datetime_utils import advance_timestamp, normalize_datetime_to_utc
from models import File, Project, User
from models.file_version import FileVersion
from services.features.file_version_service import get_file_version_service
from services.file_tree_rules import lock_project_for_files

NOW = datetime(2026, 10, 6, 12, 0, 0, 123456, tzinfo=UTC)
KINDS = ["material", "draft", "script"]
OFFSETS = [timedelta(seconds=-1), timedelta(0), timedelta(seconds=1)]


def _seed(session, kind, *, deleted=True, offset=timedelta(0), nested=False):
    user = User(username="canonical-owner", email="canonical@example.test", hashed_password="unused")
    project = Project(
        name="Canonical recovery", owner_id=user.id,
        project_type="screenplay" if kind == "script" else "novel",
    )
    ancestor = File(project_id=project.id, title="Ancestor", file_type="folder")
    folder = File(
        id=f"{project.id}-{kind}-folder", project_id=project.id,
        title="Preserved localized title", file_type="folder", content="Preserved folder body",
        order=71, parent_id=ancestor.id if nested else None,
        updated_at=NOW + offset, is_deleted=deleted,
        deleted_at=NOW - timedelta(days=1) if deleted else None,
    )
    session.add_all([user, project, ancestor, folder])
    session.commit()
    return user, project, folder


def _ensure(session, project_id, kind, *, commit):
    helper = files_api._ensure_material_folder if kind == "material" else files_api._ensure_draft_folder
    if not commit:
        lock_project_for_files(session, project_id)
    return helper(session, project_id, commit=commit)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("commit", [False, True])
@pytest.mark.parametrize("offset", OFFSETS)
def test_upload_recovery_advances_token_and_clears_deleted_at(
    db_session, monkeypatch, kind, commit, offset,
):
    _, project, folder = _seed(db_session, kind, offset=offset)
    old = folder.updated_at
    monkeypatch.setattr(files_api, "utcnow", lambda: NOW)

    restored = _ensure(db_session, project.id, kind, commit=commit)

    assert restored.id == folder.id
    assert restored.is_deleted is False
    assert restored.deleted_at is None
    assert normalize_datetime_to_utc(restored.updated_at) == advance_timestamp(old, now=NOW)
    assert (restored.title, restored.content, restored.order, restored.parent_id) == (
        "Preserved localized title", "Preserved folder body", 71, None,
    )
    if not commit:
        db_session.commit()
    with Session(db_session.get_bind()) as reader:
        persisted = reader.get(File, folder.id)
        assert persisted.is_deleted is False
        assert persisted.deleted_at is None
        assert normalize_datetime_to_utc(persisted.updated_at) == advance_timestamp(old, now=NOW)


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("commit", [False, True])
def test_live_upload_folder_is_an_unlocked_token_preserving_noop(db_session, monkeypatch, kind, commit):
    _, project, folder = _seed(db_session, kind, deleted=False)
    old = normalize_datetime_to_utc(folder.updated_at)
    monkeypatch.setattr(files_api, "_load_file_for_write", Mock(side_effect=AssertionError("no-op locked File")))
    if commit:
        monkeypatch.setattr(files_api, "lock_project_for_files", Mock(side_effect=AssertionError("no-op locked Project")))
    commits = Mock(wraps=db_session.commit)
    monkeypatch.setattr(db_session, "commit", commits)

    restored = _ensure(db_session, project.id, kind, commit=commit)

    assert restored.id == folder.id
    assert normalize_datetime_to_utc(restored.updated_at) == old
    assert restored.content == "Preserved folder body"
    commits.assert_not_called()


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("offset", OFFSETS)
def test_tool_recovery_advances_fresh_token_and_restores_canonical_parent(
    db_session, monkeypatch, kind, nested, offset,
):
    user, project, folder = _seed(db_session, kind, offset=offset, nested=nested)
    old = folder.updated_at
    monkeypatch.setattr(crud_module, "utcnow", lambda: NOW)
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_a, **_kw: None)
    # Screenplay's legacy draft parent must still fall back to canonical script.
    requested_parent = f"{project.id}-draft-folder" if kind == "script" else folder.id

    result = FileCRUD(db_session, user.id).create_file(
        project.id, "Empty child", "draft", parent_id=requested_parent,
    )

    db_session.refresh(folder)
    assert folder.is_deleted is False
    assert folder.deleted_at is None
    assert folder.parent_id is None
    assert normalize_datetime_to_utc(folder.updated_at) == advance_timestamp(old, now=NOW)
    assert (folder.title, folder.content, folder.order) == (
        "Preserved localized title", "Preserved folder body", 71,
    )
    assert result["parent_id"] == folder.id


@pytest.mark.parametrize("kind", KINDS)
def test_caller_owned_recovery_never_commits_and_rolls_back_with_new_child(db_session, monkeypatch, kind):
    _, project, folder = _seed(db_session, kind)
    folder_id = folder.id
    commits = Mock(wraps=db_session.commit)
    monkeypatch.setattr(db_session, "commit", commits)
    monkeypatch.setattr(files_api, "utcnow", lambda: NOW)
    restored = _ensure(db_session, project.id, kind, commit=False)
    child = File(project_id=project.id, title="Uncommitted child", file_type="draft", parent_id=restored.id)
    child_id = child.id
    db_session.add(child)
    db_session.flush()
    commits.assert_not_called()
    db_session.rollback()

    with Session(db_session.get_bind()) as reader:
        persisted = reader.get(File, folder_id)
        assert persisted.is_deleted is True
        assert persisted.deleted_at is not None
        assert normalize_datetime_to_utc(persisted.updated_at) == NOW
        assert reader.get(File, child_id) is None


@pytest.mark.parametrize("kind", KINDS)
async def test_strict_upload_baseline_failure_rolls_back_existing_folder_recovery(
    db_session, monkeypatch, kind,
):
    user, project, folder = _seed(db_session, kind)
    folder_id, project_id = folder.id, project.id
    service = get_file_version_service()
    failure = Mock(side_effect=RuntimeError("strict initial baseline unavailable"))
    monkeypatch.setattr(type(service), "create_version", failure)
    upload = UploadFile(filename="original.txt", file=BytesIO(b"Original body"))
    with pytest.raises(RuntimeError, match="strict initial baseline unavailable"):
        if kind == "material":
            files_api.upload_material(
                project_id, upload, current_user=user, session=db_session,
            )
        else:
            files_api.upload_drafts(
                project_id, [upload], parent_id=None, current_user=user, session=db_session,
                background_tasks=BackgroundTasks(),
            )
    failure.assert_called_once()
    db_session.rollback()  # Request dependency cleanup owns the outer transaction.
    with Session(db_session.get_bind()) as reader:
        persisted = reader.get(File, folder_id)
        assert persisted.is_deleted is True
        assert persisted.deleted_at is not None
        assert normalize_datetime_to_utc(persisted.updated_at) == NOW
        assert reader.exec(select(File).where(File.project_id == project_id, File.file_type != "folder")).all() == []
        assert reader.exec(select(FileVersion).where(FileVersion.project_id == project_id)).all() == []
