"""Creation-baseline regressions for material imports."""

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
from models.material_models import Character, Novel
from services.features.file_version_service import FileVersionService


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
