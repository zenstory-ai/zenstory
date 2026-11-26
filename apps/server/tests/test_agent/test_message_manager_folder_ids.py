"""Tests for legacy root folder id resolution in MessageManager.

These tests ensure that MessageManager builds prompt folder placeholders
using the *actual* root folders stored in the database for legacy projects,
instead of always assuming deterministic IDs like "{project_id}-draft-folder".
"""

from datetime import datetime, timedelta

from agent.core.message_manager import MessageManager
from models import File, Project, User


def _create_user(db_session):
    user = User(
        email="folder_ids_test@example.com",
        username="folder_ids_test",
        hashed_password="hashed_password",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_folder_ids_prefers_earlier_legacy_root_folder_deterministically(db_session):
    """When both legacy+deterministic canonical roots exist, prefer the earliest (created_at, id)
    so mid-session file creation/moves never flip folder_ids or bust prompt cache."""
    user = _create_user(db_session)
    project = Project(
        name="Legacy Root Folder Project",
        owner_id=user.id,
        project_type="screenplay",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    deterministic_character_id = f"{project.id}-character-folder"
    legacy_character_id = "legacy-character-root"
    t0 = datetime(2026, 1, 1, 12, 0, 0)

    # Legacy root folder (UUID) was created first -> selected deterministically.
    db_session.add(
        File(
            id=legacy_character_id,
            project_id=project.id,
            title="角色",
            file_type="folder",
            parent_id=None,
            order=0,
            created_at=t0,
        )
    )

    # Deterministic root was created later (e.g. by a migration/bug).
    db_session.add(
        File(
            id=deterministic_character_id,
            project_id=project.id,
            title="角色",
            file_type="folder",
            parent_id=None,
            order=0,
            created_at=t0 + timedelta(days=1),
        )
    )
    db_session.commit()

    manager = MessageManager(project_id=project.id, user_id=user.id)
    folder_ids_before = manager._get_folder_ids(  # noqa: SLF001 - intentional private-method unit test
        session=db_session,
        project_type="screenplay",
    )
    assert folder_ids_before["character"] == legacy_character_id

    # Adding many files to the later deterministic folder MUST NOT flip folder_ids mid-session.
    for idx in range(5):
        db_session.add(
            File(
                id=f"det-child-{idx}",
                project_id=project.id,
                title=f"角色{idx}",
                file_type="character",
                parent_id=deterministic_character_id,
                order=idx,
                content="测试角色",
            )
        )
    db_session.commit()

    folder_ids_after = manager._get_folder_ids(  # noqa: SLF001
        session=db_session,
        project_type="screenplay",
    )
    assert folder_ids_after["character"] == legacy_character_id


def test_folder_ids_maps_screenplay_scene_folder_to_lore(db_session):
    """Legacy screenplay projects may use a root folder titled '场景' as settings."""
    user = _create_user(db_session)
    project = Project(
        name="Legacy Scene Folder Project",
        owner_id=user.id,
        project_type="screenplay",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    legacy_scene_id = "legacy-scene-root"
    db_session.add(
        File(
            id=legacy_scene_id,
            project_id=project.id,
            title="场景",
            file_type="folder",
            parent_id=None,
            order=1,
        )
    )
    db_session.commit()

    manager = MessageManager(project_id=project.id, user_id=user.id)
    folder_ids = manager._get_folder_ids(  # noqa: SLF001
        session=db_session,
        project_type="screenplay",
    )

    assert folder_ids["lore"] == legacy_scene_id
