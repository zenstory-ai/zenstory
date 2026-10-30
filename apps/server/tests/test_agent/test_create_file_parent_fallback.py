"""Tests for create_file parent_id fallback behavior in screenplay projects."""

import pytest
from sqlmodel import Session

from agent.tools.file_ops.crud import FileCRUD
from agent.tools.tool_schemas import CREATE_FILE_TOOL
from models import File, Project, User


@pytest.fixture
def test_user(db_session: Session) -> User:
    user = User(
        email="fallback_test@example.com",
        username="fallbacktest",
        hashed_password="hashed_password",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def screenplay_project(db_session: Session, test_user: User) -> Project:
    project = Project(
        name="Test Screenplay",
        owner_id=test_user.id,
        project_type="screenplay",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create the expected screenplay root folders we depend on.
    script_folder = File(
        id=f"{project.id}-script-folder",
        project_id=project.id,
        title="剧本",
        file_type="folder",
        order=0,
        parent_id=None,
    )
    db_session.add(script_folder)
    db_session.commit()
    return project


@pytest.mark.unit
def test_create_file_tool_schema_includes_script_and_snippet():
    enums = CREATE_FILE_TOOL["input_schema"]["properties"]["file_type"]["enum"]
    assert "script" in enums
    assert "snippet" in enums


@pytest.mark.unit
def test_create_file_falls_back_to_script_folder_for_screenplay(db_session: Session, test_user: User, screenplay_project: Project):
    crud = FileCRUD(db_session, user_id=test_user.id)

    result = crud.create_file(
        project_id=screenplay_project.id,
        title="第1集",
        file_type="draft",
        parent_id=f"{screenplay_project.id}-draft-folder",  # nonexistent in screenplay projects
    )

    assert result["parent_id"] == f"{screenplay_project.id}-script-folder"


@pytest.mark.unit
def test_create_file_does_not_fallback_for_non_draft_types(db_session: Session, test_user: User, screenplay_project: Project):
    crud = FileCRUD(db_session, user_id=test_user.id)

    with pytest.raises(ValueError, match="parent_id"):
        crud.create_file(
            project_id=screenplay_project.id,
            title="分集大纲 1",
            file_type="outline",
            parent_id=f"{screenplay_project.id}-draft-folder",
        )


@pytest.mark.unit
def test_create_file_does_not_fallback_for_non_screenplay_projects(db_session: Session, test_user: User):
    project = Project(
        name="Test Novel",
        owner_id=test_user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    crud = FileCRUD(db_session, user_id=test_user.id)

    result = crud.create_file(
        project_id=project.id,
        title="第1章",
        file_type="draft",
        parent_id=f"{project.id}-draft-folder",
    )

    # Should auto-create the missing root folder and proceed.
    assert result["parent_id"] == f"{project.id}-draft-folder"

    folder = db_session.get(File, f"{project.id}-draft-folder")
    assert folder is not None
    assert folder.is_deleted is False
    assert folder.file_type == "folder"


@pytest.mark.unit
def test_create_file_still_raises_for_unknown_parent_id(db_session: Session, test_user: User):
    project = Project(
        name="Test Novel Unknown Parent",
        owner_id=test_user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    crud = FileCRUD(db_session, user_id=test_user.id)

    with pytest.raises(ValueError, match="parent_id"):
        crud.create_file(
            project_id=project.id,
            title="第1章",
            file_type="draft",
            parent_id=f"{project.id}-unknown-folder",
        )


@pytest.mark.unit
def test_project_id_as_parent_tells_the_agent_which_folder_ids_exist(db_session: Session, test_user: User):
    """Production (2026-10-25/26): the writer passed the project id as parent_id three times and was told
    only "Parent file <project id> not found in project <project id>"."""
    project = Project(name="Novel", owner_id=test_user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    crud = FileCRUD(db_session, user_id=test_user.id)

    with pytest.raises(ValueError) as refused:
        crud.create_file(project_id=project.id, title="第一章", file_type="draft", parent_id=project.id)

    message = str(refused.value)
    assert "项目 id" in message
    assert f"正文（{project.id}-draft-folder）" in message
    assert db_session.query(File).filter(File.project_id == project.id, File.title == "第一章").count() == 0
