"""Regression coverage for file-tree writers and screenplay episode reuse tokens."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import BackgroundTasks

from agent.tools.file_ops.crud import FileCRUD
from api import files as files_api
from api.files import MoveFileRequest, ReorderFilesRequest
from config.datetime_utils import normalize_datetime_to_utc
from models import File, Project, User

FROZEN_NOW = datetime(2026, 10, 6, 12, 0, 0, 123456, tzinfo=UTC)


@pytest.fixture
def owned_tree(db_session):
    user = User(
        username="tree-writer-owner",
        email="tree-writer-owner@example.test",
        hashed_password="unused",
        email_verified=True,
    )
    project = Project(name="Tree writer timestamps", owner_id=user.id)
    source = File(project_id=project.id, title="Source", file_type="folder")
    target = File(project_id=project.id, title="Target", file_type="folder")
    db_session.add_all([user, project, source, target])
    db_session.commit()
    return user, project, source, target


@pytest.mark.parametrize("persisted_offset", [timedelta(seconds=-1), timedelta(0), timedelta(seconds=1)])
def test_move_strictly_advances_normalized_token_without_changing_body(
    db_session, owned_tree, monkeypatch, persisted_offset
):
    user, project, source, target = owned_tree
    previous = FROZEN_NOW + persisted_offset
    file = File(
        project_id=project.id,
        title="Movable",
        file_type="draft",
        content="Original body",
        parent_id=source.id,
        updated_at=previous,
    )
    db_session.add(file)
    db_session.commit()
    monkeypatch.setattr(files_api, "utcnow", lambda: FROZEN_NOW)

    files_api.move_file(
        file.id,
        MoveFileRequest(target_parent_id=target.id),
        BackgroundTasks(),
        current_user=user,
        session=db_session,
    )

    db_session.refresh(file)
    assert file.content == "Original body"
    assert normalize_datetime_to_utc(file.updated_at) > normalize_datetime_to_utc(previous)


@pytest.mark.parametrize("persisted_offset", [timedelta(seconds=-1), timedelta(0), timedelta(seconds=1)])
def test_partial_reorder_advances_selected_tokens_and_leaves_unlisted_sibling_untouched(
    db_session, owned_tree, monkeypatch, persisted_offset
):
    user, project, source, _ = owned_tree
    previous = FROZEN_NOW + persisted_offset
    first = File(
        project_id=project.id,
        title="Alpha",
        file_type="draft",
        content="Alpha body",
        parent_id=source.id,
        order=20,
        updated_at=previous,
    )
    second = File(
        project_id=project.id,
        title="Beta",
        file_type="draft",
        content="Beta body",
        parent_id=source.id,
        order=10,
        updated_at=previous,
    )
    unlisted = File(
        project_id=project.id,
        title="Gamma",
        file_type="draft",
        content="Gamma body",
        parent_id=source.id,
        order=77,
        updated_at=previous,
    )
    db_session.add_all([first, second, unlisted])
    db_session.commit()
    monkeypatch.setattr(files_api, "utcnow", lambda: FROZEN_NOW)

    result = files_api.reorder_files(
        project.id,
        ReorderFilesRequest(ordered_ids=[second.id, first.id]),
        current_user=user,
        session=db_session,
    )

    assert result["count"] == 2
    for file, expected_order, expected_body in (
        (second, 0, "Beta body"),
        (first, 1, "Alpha body"),
    ):
        db_session.refresh(file)
        assert file.order == expected_order
        assert file.content == expected_body
        assert normalize_datetime_to_utc(file.updated_at) > normalize_datetime_to_utc(previous)
    db_session.refresh(unlisted)
    assert unlisted.order == 77
    assert unlisted.content == "Gamma body"
    assert normalize_datetime_to_utc(unlisted.updated_at) == normalize_datetime_to_utc(previous)


@pytest.fixture
def screenplay_tree(db_session):
    user = User(
        username="screenplay-tree-writer",
        email="screenplay-tree-writer@example.test",
        hashed_password="unused",
        email_verified=True,
    )
    project = Project(name="Screenplay writer timestamps", owner_id=user.id, project_type="screenplay")
    folder = File(
        id=f"{project.id}-script-folder",
        project_id=project.id,
        title="剧本",
        file_type="folder",
    )
    db_session.add_all([user, project, folder])
    db_session.commit()
    return user, project, folder


@pytest.mark.parametrize("persisted_offset", [timedelta(seconds=-1), timedelta(0), timedelta(seconds=1)])
@pytest.mark.parametrize(
    ("existing_type", "existing_order", "title", "expected_order"),
    [
        ("draft", 4, "第4集：晋级", 4),
        ("script", 0, "第5集：补序", 5),
    ],
)
def test_screenplay_reuse_mutation_advances_token_without_changing_body(
    db_session,
    screenplay_tree,
    monkeypatch,
    persisted_offset,
    existing_type,
    existing_order,
    title,
    expected_order,
):
    from agent.tools.file_ops import crud as crud_module

    user, project, folder = screenplay_tree
    previous = FROZEN_NOW + persisted_offset
    episode = File(
        project_id=project.id,
        title=title,
        file_type=existing_type,
        content="Existing screenplay body",
        parent_id=folder.id,
        order=existing_order,
        updated_at=previous,
    )
    db_session.add(episode)
    db_session.commit()
    monkeypatch.setattr(crud_module, "utcnow", lambda: FROZEN_NOW)
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_args, **_kwargs: None)

    result = FileCRUD(db_session, user_id=user.id).create_file(
        project_id=project.id,
        title=title,
        file_type="script",
        parent_id=folder.id,
        content="",
    )

    db_session.refresh(episode)
    assert result["id"] == episode.id
    assert result["mutation_applied"] is True
    assert episode.file_type == "script"
    assert episode.order == expected_order
    assert episode.content == "Existing screenplay body"
    assert result["content"] == "Existing screenplay body"
    assert normalize_datetime_to_utc(episode.updated_at) > normalize_datetime_to_utc(previous)


def test_unchanged_screenplay_reuse_reports_no_mutation_and_preserves_token(db_session, screenplay_tree, monkeypatch):
    user, project, folder = screenplay_tree
    episode = File(
        project_id=project.id,
        title="第6集：不变",
        file_type="script",
        content="Existing screenplay body",
        parent_id=folder.id,
        order=6,
        updated_at=FROZEN_NOW,
    )
    db_session.add(episode)
    db_session.commit()
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_args, **_kwargs: None)

    result = FileCRUD(db_session, user_id=user.id).create_file(
        project_id=project.id,
        title=episode.title,
        file_type="script",
        parent_id=folder.id,
        content="",
    )

    db_session.refresh(episode)
    assert result["reused_existing"] is True
    assert result["mutation_applied"] is False
    assert episode.content == "Existing screenplay body"
    assert normalize_datetime_to_utc(episode.updated_at) == FROZEN_NOW
