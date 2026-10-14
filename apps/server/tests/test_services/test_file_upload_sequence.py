"""SQLite regressions for draft-upload sequence inference and fallback order."""

import json
from io import BytesIO

import pytest
from fastapi import UploadFile
from sqlmodel import select

from agent.tools.task_progress import ArtifactEvidence, artifact_units
from api import files as files_api
from core.error_codes import ErrorCode
from models import File, Project, User

MAX_ORDER = 2_147_483_647


def _upload(filename: str, content: str) -> UploadFile:
    return UploadFile(filename=filename, file=BytesIO(content.encode("utf-8")))


def _owned_project(db_session, suffix: str) -> tuple[User, Project]:
    user = User(
        username=f"upload-sequence-{suffix}",
        email=f"upload-sequence-{suffix}@example.test",
        hashed_password="unused",
        email_verified=True,
    )
    project = Project(name="Upload sequence", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    return user, project


@pytest.mark.parametrize("overflow_source", ["filename", "second-chapter"])
def test_oversized_inferred_order_rejects_whole_input_without_consuming_append_slot(
    db_session,
    overflow_source,
):
    user, project = _owned_project(db_session, overflow_source)
    invalid = (
        _upload("Chapter 2147483648.txt", "invalid body")
        if overflow_source == "filename"
        else _upload(
            "novel.txt",
            "Chapter 1 Valid\nfirst body\n\n"
            "Chapter 2147483648 Invalid\nsecond body",
        )
    )

    result = files_api.upload_drafts(
        project.id,
        [_upload("before.txt", "before"), invalid, _upload("after.txt", "after")],
        parent_id=None,
        current_user=user,
        session=db_session,
    )

    assert len(result.errors) == 1
    assert str(ErrorCode.VALIDATION_ERROR) in result.errors[0]
    assert [(row.content, row.order) for row in result.files] == [
        ("before", 0),
        ("after", 1),
    ]
    stored = db_session.exec(
        select(File)
        .where(File.project_id == project.id, File.file_type == "draft")
        .order_by(File.order)
    ).all()
    assert [(row.content, row.order) for row in stored] == [("before", 0), ("after", 1)]


def test_append_overflow_does_not_hide_valid_inferred_heading_boundary(db_session):
    user, project = _owned_project(db_session, "boundary")
    folder = File(
        id=f"{project.id}-draft-folder",
        project_id=project.id,
        title="Drafts",
        file_type="folder",
    )
    db_session.add(folder)
    db_session.flush()
    db_session.add(
        File(
            project_id=project.id,
            parent_id=folder.id,
            title="Existing",
            file_type="draft",
            content="existing",
            order=MAX_ORDER,
        )
    )
    db_session.commit()

    result = files_api.upload_drafts(
        project.id,
        [_upload("unnumbered.txt", "rejected"), _upload(f"Chapter {MAX_ORDER}.txt", "boundary")],
        parent_id=None,
        current_user=user,
        session=db_session,
    )

    assert result.total == 1
    assert len(result.errors) == 1
    assert (result.files[0].content, result.files[0].order) == ("boundary", MAX_ORDER)


def test_split_upload_keeps_heading_sequence_as_typed_metadata(db_session):
    user, project = _owned_project(db_session, "split-metadata")

    result = files_api.upload_drafts(
        project.id,
        [
            _upload(
                "novel.txt",
                "Chapter 1 Opening\nfirst body\n\nChapter 12 Return\ntwelfth body",
            )
        ],
        parent_id=None,
        current_user=user,
        session=db_session,
    )

    assert result.errors == []
    assert [(row.title, row.order) for row in result.files] == [
        ("novel - Chapter 1 Opening", 1),
        ("novel - Chapter 12 Return", 12),
    ]
    stored = db_session.exec(
        select(File)
        .where(File.project_id == project.id, File.file_type == "draft")
        .order_by(File.order)
    ).all()
    assert [json.loads(row.file_metadata)["chapter_number"] for row in stored] == [1, 12]
    evidence = [
        ArtifactEvidence(
            id=row.id,
            title=row.title,
            file_type=row.file_type,
            metadata=row.get_metadata(),
        )
        for row in stored
    ]
    assert artifact_units(evidence, "章", "draft") == {1, 12}


@pytest.mark.parametrize(
    ("filename", "content", "expected_orders"),
    [
        ("2026-03-20日志.txt", "daily notes", [2026]),
        ("第12集分镜.txt", "episode notes", [12]),
        ("第一卷.txt", "volume notes", [1]),
        ("卷一.txt", "volume notes", [1]),
        ("novel.txt", "第1节 开端\nfirst\n\n第2节 发展\nsecond", [1, 2]),
    ],
)
def test_nonchapter_ordinals_sort_without_claiming_chapter_evidence(
    db_session,
    filename,
    content,
    expected_orders,
):
    user, project = _owned_project(db_session, str(expected_orders[-1]))

    result = files_api.upload_drafts(
        project.id,
        [_upload(filename, content)],
        parent_id=None,
        current_user=user,
        session=db_session,
    )

    assert result.errors == []
    assert [row.order for row in result.files] == expected_orders
    stored = db_session.exec(
        select(File).where(File.project_id == project.id, File.file_type == "draft")
    ).all()
    evidence = [
        ArtifactEvidence(
            id=row.id,
            title=row.title,
            file_type=row.file_type,
            metadata=row.get_metadata(),
        )
        for row in stored
    ]
    assert all("chapter_number" not in row.get_metadata() for row in stored)
    assert artifact_units(evidence, "章", "draft") == set()
