"""Creation-baseline regressions for material imports and inspiration copies."""

import json

import pytest
from sqlmodel import Session, select

from api.materials.import_ import import_material
from api.materials.schemas import MaterialImportRequest
from models.entities import Project, User
from models.file_model import File
from models.file_version import (
    CHANGE_SOURCE_SYSTEM,
    CHANGE_TYPE_CREATE,
    FileVersion,
)
from models.inspiration import Inspiration
from models.material_models import Character, Novel
from services.features.file_version_service import FileVersionService
from services.inspiration_service import copy_inspiration_to_project


def _seed_user(session: Session, suffix: str) -> User:
    user = User(
        username=f"baseline-{suffix}",
        email=f"baseline-{suffix}@example.com",
        hashed_password="not-used-by-these-service-tests",
        email_verified=True,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _seed_material_import(session: Session) -> tuple[User, Project, Novel, Character]:
    user = _seed_user(session, "material")
    project = Project(
        name="Material baseline destination",
        owner_id=user.id,
        project_type="novel",
    )
    novel = Novel(
        user_id=user.id,
        title="Source material novel",
        author="Fixture Author",
    )
    session.add_all([project, novel])
    session.commit()
    session.refresh(project)
    session.refresh(novel)

    character = Character(
        novel_id=novel.id,
        name="Lin",
        description="A fully seeded character used by the authentic preview path.",
        archetype="protagonist",
    )
    session.add(character)
    session.commit()
    session.refresh(character)
    return user, project, novel, character


def _seed_inspiration(session: Session) -> tuple[User, Inspiration]:
    user = _seed_user(session, "inspiration")
    source_project = Project(
        name="Source inspiration project",
        owner_id=user.id,
        project_type="novel",
    )
    session.add(source_project)
    session.commit()
    session.refresh(source_project)

    snapshot = {
        "project_description": "Copied project fixture",
        "files": [
            {
                "id": "old-folder",
                "title": "Chapters",
                "content": "",
                "file_type": "folder",
                "parent_id": None,
                "order": 1,
            },
            {
                "id": "old-chapter",
                "title": "Chapter One",
                "content": "The complete opening chapter.",
                "file_type": "draft",
                "parent_id": "old-folder",
                "order": 2,
            },
            {
                "id": "old-empty-note",
                "title": "Empty note",
                "content": "",
                "file_type": "document",
                "parent_id": "old-folder",
                "order": 3,
            },
        ],
    }
    inspiration = Inspiration(
        name="Baseline inspiration",
        description="Copy baseline fixture",
        project_type="novel",
        snapshot_data=json.dumps(snapshot),
        source="official",
        status="approved",
        original_project_id=source_project.id,
    )
    session.add(inspiration)
    session.commit()
    session.refresh(inspiration)
    return user, inspiration


def _assert_creation_baseline(version: FileVersion, file: File) -> None:
    assert version.version_number == 1
    assert version.content == file.content
    assert version.is_base_version is True
    assert version.change_type == CHANGE_TYPE_CREATE
    assert version.change_source == CHANGE_SOURCE_SYSTEM


@pytest.mark.integration
def test_import_material_creates_one_quota_free_baseline_in_outer_transaction(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user, project, novel, character = _seed_material_import(db_session)
    original_create_version = FileVersionService.create_version
    calls: list[dict[str, object]] = []

    def observe_create_version(self, session, file_id, new_content, **kwargs):
        with Session(db_session.get_bind()) as isolated_session:
            externally_visible_files = isolated_session.exec(
                select(File).where(File.project_id == project.id)
            ).all()
        calls.append(
            {
                "file_id": file_id,
                "new_content": new_content,
                "kwargs": kwargs,
                "externally_visible_files": list(externally_visible_files),
            }
        )
        return original_create_version(
            self,
            session,
            file_id,
            new_content,
            **kwargs,
        )

    monkeypatch.setattr(FileVersionService, "create_version", observe_create_version)

    response = import_material(
        MaterialImportRequest(
            project_id=project.id,
            novel_id=novel.id,
            entity_type="characters",
            entity_id=character.id,
        ),
        current_user=user,
        session=db_session,
        accept_language="en",
    )

    imported_file = db_session.get(File, response.file_id)
    assert imported_file is not None
    folder = db_session.get(File, imported_file.parent_id)
    assert folder is not None
    assert folder.file_type == "folder"

    versions = list(
        db_session.exec(
            select(FileVersion).where(FileVersion.project_id == project.id)
        ).all()
    )
    assert len(versions) == 1
    _assert_creation_baseline(versions[0], imported_file)
    assert versions[0].file_id == imported_file.id
    assert calls == [
        {
            "file_id": imported_file.id,
            "new_content": imported_file.content,
            "kwargs": {
                "change_type": CHANGE_TYPE_CREATE,
                "change_source": CHANGE_SOURCE_SYSTEM,
                "change_summary": "Initial version",
                "force_base": True,
                "skip_quota": True,
                "commit": False,
            },
            "externally_visible_files": [],
        }
    ]


@pytest.mark.integration
def test_import_material_rolls_back_folder_file_and_baseline_on_version_failure(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user, project, novel, character = _seed_material_import(db_session)

    def fail_create_version(*args, **kwargs):
        raise RuntimeError("injected baseline failure")

    monkeypatch.setattr(FileVersionService, "create_version", fail_create_version)

    with pytest.raises(RuntimeError, match="injected baseline failure"):
        import_material(
            MaterialImportRequest(
                project_id=project.id,
                novel_id=novel.id,
                entity_type="characters",
                entity_id=character.id,
            ),
            current_user=user,
            session=db_session,
            accept_language="en",
        )
    db_session.rollback()

    with Session(db_session.get_bind()) as isolated_session:
        assert isolated_session.exec(
            select(File).where(File.project_id == project.id)
        ).all() == []
        assert isolated_session.exec(
            select(FileVersion).where(FileVersion.project_id == project.id)
        ).all() == []


@pytest.mark.integration
def test_copy_inspiration_commit_false_preserves_tree_and_hides_all_writes(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user, inspiration = _seed_inspiration(db_session)
    initial_copy_count = inspiration.copy_count
    original_create_version = FileVersionService.create_version
    calls: list[dict[str, object]] = []

    def observe_create_version(self, session, file_id, new_content, **kwargs):
        calls.append(
            {
                "file_id": file_id,
                "new_content": new_content,
                "kwargs": kwargs,
            }
        )
        return original_create_version(
            self,
            session,
            file_id,
            new_content,
            **kwargs,
        )

    monkeypatch.setattr(FileVersionService, "create_version", observe_create_version)

    copied_project = copy_inspiration_to_project(
        session=db_session,
        inspiration=inspiration,
        user=user,
        project_name="Uncommitted inspiration copy",
        commit=False,
    )

    copied_files = list(
        db_session.exec(
            select(File).where(File.project_id == copied_project.id)
        ).all()
    )
    by_title = {file.title: file for file in copied_files}
    assert by_title["Chapter One"].parent_id == by_title["Chapters"].id
    assert by_title["Empty note"].parent_id == by_title["Chapters"].id

    versions = list(
        db_session.exec(
            select(FileVersion).where(FileVersion.project_id == copied_project.id)
        ).all()
    )
    assert len(versions) == 1
    _assert_creation_baseline(versions[0], by_title["Chapter One"])
    assert calls == [
        {
            "file_id": by_title["Chapter One"].id,
            "new_content": by_title["Chapter One"].content,
            "kwargs": {
                "change_type": CHANGE_TYPE_CREATE,
                "change_source": CHANGE_SOURCE_SYSTEM,
                "change_summary": "Initial version",
                "force_base": True,
                "skip_quota": True,
                "commit": False,
            },
        }
    ]

    with Session(db_session.get_bind()) as isolated_session:
        assert isolated_session.get(Project, copied_project.id) is None
        assert isolated_session.exec(
            select(File).where(File.project_id == copied_project.id)
        ).all() == []
        assert isolated_session.exec(
            select(FileVersion).where(FileVersion.project_id == copied_project.id)
        ).all() == []
        persisted_inspiration = isolated_session.get(Inspiration, inspiration.id)
        assert persisted_inspiration is not None
        assert persisted_inspiration.copy_count == initial_copy_count

    db_session.rollback()


@pytest.mark.integration
def test_copy_inspiration_version_failure_is_fully_rollback_safe(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
):
    user, inspiration = _seed_inspiration(db_session)
    inspiration_id = inspiration.id
    initial_copy_count = inspiration.copy_count

    def fail_create_version(*args, **kwargs):
        raise RuntimeError("injected baseline failure")

    monkeypatch.setattr(FileVersionService, "create_version", fail_create_version)

    with pytest.raises(RuntimeError, match="injected baseline failure"):
        copy_inspiration_to_project(
            session=db_session,
            inspiration=inspiration,
            user=user,
        )
    db_session.rollback()

    with Session(db_session.get_bind()) as isolated_session:
        copied_projects = isolated_session.exec(
            select(Project).where(Project.name == inspiration.name)
        ).all()
        assert copied_projects == []
        persisted_inspiration = isolated_session.get(Inspiration, inspiration_id)
        assert persisted_inspiration is not None
        assert persisted_inspiration.copy_count == initial_copy_count
        assert isolated_session.exec(select(FileVersion)).all() == []
