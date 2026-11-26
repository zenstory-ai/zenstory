"""
Tests for database entity models.

Tests User, Project, and other entity models for:
- Field validation
- Relationships
- Soft delete
- Unique constraints
"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from models.entities import ChatMessage, ChatSession, Project, Snapshot, SystemPromptConfig, User
from models.file_model import FILE_TYPE_CHARACTER, FILE_TYPE_DRAFT, FILE_TYPE_LORE, FILE_TYPE_OUTLINE, File


@pytest.mark.unit
def test_user_model_fields(db_session: Session):
    """Test User model field validation."""
    # Create a user
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password_123",
        avatar_url="https://example.com/avatar.jpg",
        is_active=True,
        is_superuser=False,
        email_verified=True
    )

    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Verify all fields are set correctly
    assert user.id is not None
    assert user.username == "testuser"
    assert user.email == "test@example.com"
    assert user.hashed_password == "hashed_password_123"
    assert user.avatar_url == "https://example.com/avatar.jpg"
    assert user.is_active is True
    assert user.is_superuser is False
    assert user.email_verified is True
    assert user.created_at is not None
    assert user.updated_at is not None


@pytest.mark.unit
def test_user_unique_email(db_session: Session):
    """Test User email unique constraint."""
    # Create first user
    user1 = User(
        username="user1",
        email="duplicate@example.com",
        hashed_password="password1"
    )
    db_session.add(user1)
    db_session.commit()

    # Try to create second user with same email
    user2 = User(
        username="user2",
        email="duplicate@example.com",  # Same email
        hashed_password="password2"
    )
    db_session.add(user2)

    # Should raise an integrity error
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.unit
def test_user_unique_username(db_session: Session):
    """Test User username unique constraint."""
    # Create first user
    user1 = User(
        username="duplicate_user",
        email="user1@example.com",
        hashed_password="password1"
    )
    db_session.add(user1)
    db_session.commit()

    # Try to create second user with same username
    user2 = User(
        username="duplicate_user",  # Same username
        email="user2@example.com",
        hashed_password="password2"
    )
    db_session.add(user2)

    # Should raise an integrity error
    with pytest.raises(IntegrityError):
        db_session.commit()


@pytest.mark.unit
def test_project_model_fields(db_session: Session):
    """Test Project model field validation."""
    # Create a user first
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password"
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Create a project
    project = Project(
        name="Test Novel",
        description="A test novel project",
        owner_id=user.id,
        project_type="novel",
        summary="Test summary",
        current_phase="outline",
        writing_style="Third person",
        notes="Test notes"
    )

    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Verify all fields are set correctly
    assert project.id is not None
    assert project.name == "Test Novel"
    assert project.description == "A test novel project"
    assert project.owner_id == user.id
    assert project.project_type == "novel"
    assert project.summary == "Test summary"
    assert project.current_phase == "outline"
    assert project.writing_style == "Third person"
    assert project.notes == "Test notes"
    assert project.is_deleted is False
    assert project.deleted_at is None
    assert project.created_at is not None
    assert project.updated_at is not None


@pytest.mark.unit
def test_project_soft_delete(db_session: Session):
    """Test Project soft delete functionality."""
    # Create a user and project
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password"
    )
    db_session.add(user)
    db_session.commit()

    project = Project(
        name="Test Novel",
        owner_id=user.id
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Verify project is not deleted
    assert project.is_deleted is False
    assert project.deleted_at is None

    # Soft delete the project
    from datetime import datetime
    project.is_deleted = True
    project.deleted_at = datetime.utcnow()
    db_session.commit()
    db_session.refresh(project)

    # Verify soft delete worked
    assert project.is_deleted is True
    assert project.deleted_at is not None


@pytest.mark.unit
def test_user_project_relationship(db_session: Session):
    """Test User-Project foreign key relationship."""
    # Create a user
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password"
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Create multiple projects for the user
    project1 = Project(name="Project 1", owner_id=user.id)
    project2 = Project(name="Project 2", owner_id=user.id)
    db_session.add_all([project1, project2])
    db_session.commit()

    # Query projects by user
    stmt = select(Project).where(Project.owner_id == user.id)
    result = db_session.execute(stmt)
    projects = result.scalars().all()

    assert len(projects) == 2
    assert all(p.owner_id == user.id for p in projects)


@pytest.mark.unit
def test_snapshot_model_fields(db_session: Session):
    """Test Snapshot model field validation."""
    # Create a user and project
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password"
    )
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create a snapshot
    snapshot = Snapshot(
        project_id=project.id,
        file_id=None,
        data='{"test": "data"}',
        description="Test snapshot",
        snapshot_type="manual",
        version=2
    )

    db_session.add(snapshot)
    db_session.commit()
    db_session.refresh(snapshot)

    # Verify all fields
    assert snapshot.id is not None
    assert snapshot.project_id == project.id
    assert snapshot.data == '{"test": "data"}'
    assert snapshot.description == "Test snapshot"
    assert snapshot.snapshot_type == "manual"
    assert snapshot.version == 2
    assert snapshot.created_at is not None


@pytest.mark.unit
def test_chat_session_and_message_relationship(db_session: Session):
    """Test ChatSession and ChatMessage relationship."""
    # Create a user and project
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password"
    )
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create a chat session
    session = ChatSession(
        user_id=user.id,
        project_id=project.id,
        title="Test Chat",
        is_active=True
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    # Create chat messages
    msg1 = ChatMessage(
        session_id=session.id,
        role="user",
        content="Hello, AI!"
    )
    msg2 = ChatMessage(
        session_id=session.id,
        role="assistant",
        content="Hello! How can I help?"
    )
    db_session.add_all([msg1, msg2])
    db_session.commit()

    # Verify relationship
    stmt = select(ChatMessage).where(ChatMessage.session_id == session.id)
    result = db_session.execute(stmt)
    messages = result.scalars().all()

    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[1].role == "assistant"


@pytest.mark.unit
def test_system_prompt_config_model(db_session: Session):
    """Test SystemPromptConfig model field validation."""
    # Create a config
    config = SystemPromptConfig(
        project_type="novel",
        role_definition="AI novel writing assistant",
        capabilities="Help with plot, characters, and dialogue",
        directory_structure="Standard novel structure",
        content_structure="Chapters and scenes",
        file_types="outline, draft, character, lore",
        writing_guidelines="Third person, past tense",
        include_dialogue_guidelines=True,
        primary_content_type="chapter",
        is_active=True,
        version=1
    )

    db_session.add(config)
    db_session.commit()
    db_session.refresh(config)

    # Verify all fields
    assert config.id is not None
    assert config.project_type == "novel"
    assert config.role_definition == "AI novel writing assistant"
    assert config.capabilities == "Help with plot, characters, and dialogue"
    assert config.include_dialogue_guidelines is True
    assert config.is_active is True
    assert config.version == 1
    assert config.created_at is not None


@pytest.mark.unit
def test_project_type_validation(db_session: Session):
    """Test Project project_type field accepts valid types."""
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password"
    )
    db_session.add(user)
    db_session.commit()

    # Test different project types
    valid_types = ["novel", "short", "screenplay"]
    for project_type in valid_types:
        project = Project(
            name=f"Test {project_type}",
            owner_id=user.id,
            project_type=project_type
        )
        db_session.add(project)
        db_session.commit()

    # Verify all projects were created
    stmt = select(Project).where(Project.owner_id == user.id)
    result = db_session.execute(stmt)
    projects = result.scalars().all()

    assert len(projects) == len(valid_types)


@pytest.mark.unit
def test_chat_message_tool_calls(db_session: Session):
    """Test ChatMessage tool call fields."""
    # Create user, project, and session
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password"
    )
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    session = ChatSession(
        user_id=user.id,
        project_id=project.id
    )
    db_session.add(session)
    db_session.commit()
    db_session.refresh(session)

    # Create a message with tool calls
    msg = ChatMessage(
        session_id=session.id,
        role="assistant",
        content="I'll help you create that file.",
        tool_calls='[{"name": "create_file", "arguments": {"title": "test"}}]',
        tool_call_id="call_123",
        message_metadata='{"tokens": 50}'
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    # Verify tool call fields
    assert msg.tool_calls is not None
    assert msg.tool_call_id == "call_123"
    assert msg.message_metadata is not None


@pytest.mark.unit
def test_multiple_users_with_projects(db_session: Session):
    """Test multiple users with their own projects."""
    # Create multiple users
    user1 = User(username="user1", email="user1@example.com", hashed_password="pass1")
    user2 = User(username="user2", email="user2@example.com", hashed_password="pass2")
    db_session.add_all([user1, user2])
    db_session.commit()

    # Create projects for each user
    project1 = Project(name="User1 Project", owner_id=user1.id)
    project2 = Project(name="User2 Project", owner_id=user2.id)
    db_session.add_all([project1, project2])
    db_session.commit()

    # Query user1's projects
    stmt1 = select(Project).where(Project.owner_id == user1.id)
    result1 = db_session.execute(stmt1)
    user1_projects = result1.scalars().all()

    # Query user2's projects
    stmt2 = select(Project).where(Project.owner_id == user2.id)
    result2 = db_session.execute(stmt2)
    user2_projects = result2.scalars().all()

    assert len(user1_projects) == 1
    assert user1_projects[0].name == "User1 Project"
    assert len(user2_projects) == 1
    assert user2_projects[0].name == "User2 Project"


@pytest.mark.unit
def test_model_timestamps_auto_generation(db_session: Session):
    """Test that created_at and updated_at are automatically generated and persist updates."""
    from datetime import timedelta

    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password",
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.created_at is not None
    assert user.updated_at is not None
    original_created_at = user.created_at
    newer_timestamp = user.updated_at + timedelta(seconds=5)

    user.username = "updated_user"
    user.updated_at = newer_timestamp
    db_session.commit()
    db_session.refresh(user)

    assert user.created_at == original_created_at
    assert user.updated_at == newer_timestamp
    assert user.updated_at > user.created_at


@pytest.mark.unit
def test_file_model_fields(db_session: Session):
    """Test File model field validation."""
    # Create a user and project first
    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password="hashed_password"
    )
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create a file
    file = File(
        project_id=project.id,
        title="Chapter 1",
        content="This is the content of chapter 1.",
        file_type=FILE_TYPE_DRAFT,
        parent_id=None,
        order=0
    )

    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    # Verify all fields
    assert file.id is not None
    assert file.project_id == project.id
    assert file.title == "Chapter 1"
    assert file.content == "This is the content of chapter 1."
    assert file.file_type == FILE_TYPE_DRAFT
    assert file.parent_id is None
    assert file.order == 0
    assert file.is_deleted is False
    assert file.deleted_at is None
    assert file.created_at is not None
    assert file.updated_at is not None


@pytest.mark.unit
def test_file_type_validation(db_session: Session):
    """Test File file_type field accepts valid types."""
    # Create user and project
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Test different file types
    valid_types = [FILE_TYPE_OUTLINE, FILE_TYPE_DRAFT, FILE_TYPE_CHARACTER, FILE_TYPE_LORE]
    for file_type in valid_types:
        file = File(
            project_id=project.id,
            title=f"Test {file_type}",
            file_type=file_type
        )
        db_session.add(file)
        db_session.commit()

    # Verify all files were created
    stmt = select(File).where(File.project_id == project.id)
    result = db_session.execute(stmt)
    files = result.scalars().all()

    assert len(files) == len(valid_types)


@pytest.mark.unit
def test_file_parent_child_relationship(db_session: Session):
    """Test File parent-child relationship (folder structure)."""
    # Create user and project
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create parent file (folder)
    parent = File(
        project_id=project.id,
        title="Chapter 1",
        file_type=FILE_TYPE_OUTLINE
    )
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)

    # Create child files
    child1 = File(
        project_id=project.id,
        title="Scene 1",
        file_type=FILE_TYPE_DRAFT,
        parent_id=parent.id,
        order=1
    )
    child2 = File(
        project_id=project.id,
        title="Scene 2",
        file_type=FILE_TYPE_DRAFT,
        parent_id=parent.id,
        order=2
    )
    db_session.add_all([child1, child2])
    db_session.commit()

    # Query children by parent
    stmt = select(File).where(File.parent_id == parent.id)
    result = db_session.execute(stmt)
    children = result.scalars().all()

    assert len(children) == 2
    assert all(c.parent_id == parent.id for c in children)


@pytest.mark.unit
def test_file_soft_delete(db_session: Session):
    """Test File soft delete functionality."""
    # Create user and project
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create a file
    file = File(
        project_id=project.id,
        title="Test File",
        file_type=FILE_TYPE_DRAFT
    )
    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    # Verify file is not deleted
    assert file.is_deleted is False
    assert file.deleted_at is None

    # Soft delete the file
    from datetime import datetime
    file.is_deleted = True
    file.deleted_at = datetime.utcnow()
    db_session.commit()
    db_session.refresh(file)

    # Verify soft delete worked
    assert file.is_deleted is True
    assert file.deleted_at is not None


@pytest.mark.unit
def test_file_metadata(db_session: Session):
    """Test File metadata field and helper methods."""
    # Create user and project
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create a file with metadata
    file = File(
        project_id=project.id,
        title="Character Profile",
        file_type=FILE_TYPE_CHARACTER,
        content="A brave hero"
    )
    file.set_metadata({
        "age": 25,
        "gender": "male",
        "role": "protagonist"
    })

    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    # Test get_metadata
    metadata = file.get_metadata()
    assert metadata["age"] == 25
    assert metadata["gender"] == "male"
    assert metadata["role"] == "protagonist"
    assert metadata.get("nonexistent", "default_value") == "default_value"

    # Test updating metadata via set_metadata
    metadata["age"] = 26
    file.set_metadata(metadata)
    db_session.commit()
    db_session.refresh(file)
    assert file.get_metadata()["age"] == 26
