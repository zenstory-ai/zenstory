"""
Tests for File model (file_model.py).

Tests the unified File model which represents all file types:
- outline, draft, character, lore, snippet, script, folder
- Parent-child relationships
- Metadata JSON serialization
"""


import pytest
from sqlalchemy import select
from sqlmodel import Session

from models.entities import Project, User
from models.file_model import (
    FILE_TYPE_CHARACTER,
    FILE_TYPE_DRAFT,
    FILE_TYPE_FOLDER,
    FILE_TYPE_LORE,
    FILE_TYPE_METADATA_SCHEMA,
    FILE_TYPE_OUTLINE,
    FILE_TYPE_SCRIPT,
    FILE_TYPE_SNIPPET,
    File,
    cached_word_count,
)


@pytest.mark.unit
def test_file_type_constants():
    """Test that file type constants are properly defined."""
    assert FILE_TYPE_OUTLINE == "outline"
    assert FILE_TYPE_DRAFT == "draft"
    assert FILE_TYPE_CHARACTER == "character"
    assert FILE_TYPE_LORE == "lore"
    assert FILE_TYPE_SNIPPET == "snippet"
    assert FILE_TYPE_SCRIPT == "script"
    assert FILE_TYPE_FOLDER == "folder"


@pytest.mark.unit
def test_file_metadata_schema():
    """Test that FILE_TYPE_METADATA_SCHEMA is properly defined."""
    assert isinstance(FILE_TYPE_METADATA_SCHEMA, dict)
    assert FILE_TYPE_OUTLINE in FILE_TYPE_METADATA_SCHEMA
    assert FILE_TYPE_DRAFT in FILE_TYPE_METADATA_SCHEMA
    assert FILE_TYPE_CHARACTER in FILE_TYPE_METADATA_SCHEMA
    assert FILE_TYPE_LORE in FILE_TYPE_METADATA_SCHEMA

    # Check schema structure
    outline_schema = FILE_TYPE_METADATA_SCHEMA[FILE_TYPE_OUTLINE]
    assert "description" in outline_schema
    assert "optional_fields" in outline_schema


@pytest.mark.unit
def test_file_outline_type(db_session: Session):
    """Test File with outline type."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create outline file with metadata
    file = File(
        project_id=project.id,
        title="Chapter 1 Outline",
        content="Chapter summary",
        file_type=FILE_TYPE_OUTLINE
    )
    file.set_metadata({
        "chapter_number": 1,
        "status": "draft",
        "word_count_target": 3000
    })

    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    assert file.file_type == FILE_TYPE_OUTLINE
    metadata = file.get_metadata()
    assert metadata["chapter_number"] == 1
    assert metadata["status"] == "draft"
    assert metadata["word_count_target"] == 3000


@pytest.mark.unit
def test_file_draft_type(db_session: Session):
    """Test File with draft type."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create draft file with metadata
    file = File(
        project_id=project.id,
        title="Chapter 1 Draft",
        content="The story begins...",
        file_type=FILE_TYPE_DRAFT
    )
    file.set_metadata({
        "version": 2,
        "is_current": True,
        "word_count": 1500
    })

    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    assert file.file_type == FILE_TYPE_DRAFT
    metadata = file.get_metadata()
    assert metadata["version"] == 2
    assert metadata["is_current"] is True
    assert metadata["word_count"] == 1500


@pytest.mark.unit
def test_file_character_type(db_session: Session):
    """Test File with character type."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create character file with metadata
    file = File(
        project_id=project.id,
        title="Protagonist",
        content="A brave hero with a mysterious past.",
        file_type=FILE_TYPE_CHARACTER
    )
    file.set_metadata({
        "age": 25,
        "gender": "male",
        "role": "protagonist",
        "personality": "brave, curious",
        "appearance": "tall, dark hair"
    })

    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    assert file.file_type == FILE_TYPE_CHARACTER
    metadata = file.get_metadata()
    assert metadata["age"] == 25
    assert metadata["role"] == "protagonist"
    assert metadata["personality"] == "brave, curious"


@pytest.mark.unit
def test_file_lore_type(db_session: Session):
    """Test File with lore type."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create lore file with metadata
    file = File(
        project_id=project.id,
        title="Magic System",
        content="Description of the magic system",
        file_type=FILE_TYPE_LORE
    )
    file.set_metadata({
        "category": "magic",
        "importance": "high",
        "tags": ["magic", "system", "rules"]
    })

    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    assert file.file_type == FILE_TYPE_LORE
    metadata = file.get_metadata()
    assert metadata["category"] == "magic"
    assert metadata["importance"] == "high"
    assert isinstance(metadata["tags"], list)


@pytest.mark.unit
def test_file_snippet_type(db_session: Session):
    """Test File with snippet type."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create snippet file with metadata
    file = File(
        project_id=project.id,
        title="Dialogue Ideas",
        content="Interesting dialogue snippets",
        file_type=FILE_TYPE_SNIPPET
    )
    file.set_metadata({
        "source": "inspiration",
        "tags": ["dialogue", "ideas"],
        "importance": "medium"
    })

    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    assert file.file_type == FILE_TYPE_SNIPPET
    assert file.get_metadata_field("source") == "inspiration"


@pytest.mark.unit
def test_file_script_type(db_session: Session):
    """Test File with script type (for screenplay)."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Screenplay", owner_id=user.id, project_type="screenplay")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create script file with metadata
    file = File(
        project_id=project.id,
        title="Episode 1",
        content="Script content",
        file_type=FILE_TYPE_SCRIPT
    )
    file.set_metadata({
        "episode_number": 1,
        "scene_count": 5,
        "duration": "45min"
    })

    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    assert file.file_type == FILE_TYPE_SCRIPT
    metadata = file.get_metadata()
    assert metadata["episode_number"] == 1
    assert metadata["scene_count"] == 5


@pytest.mark.unit
def test_file_folder_type(db_session: Session):
    """Test File with folder type."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create folder file
    folder = File(
        project_id=project.id,
        title="Act 1",
        content="",
        file_type=FILE_TYPE_FOLDER
    )

    db_session.add(folder)
    db_session.commit()
    db_session.refresh(folder)

    assert folder.file_type == FILE_TYPE_FOLDER
    assert folder.content == ""


@pytest.mark.unit
def test_file_parent_relationship(db_session: Session):
    """Test File parent-child relationships."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create parent folder
    parent = File(
        project_id=project.id,
        title="Chapter 1",
        file_type=FILE_TYPE_OUTLINE
    )
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)

    # Create children
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

    # Query children
    stmt = select(File).where(File.parent_id == parent.id).order_by(File.order)
    result = db_session.execute(stmt)
    children = result.scalars().all()

    assert len(children) == 2
    assert children[0].title == "Scene 1"
    assert children[1].title == "Scene 2"


@pytest.mark.unit
def test_file_ordering(db_session: Session):
    """Test File order field for sibling ordering."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create parent
    parent = File(
        project_id=project.id,
        title="Chapter 1",
        file_type=FILE_TYPE_OUTLINE
    )
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)

    # Create children with specific order
    files = [
        File(project_id=project.id, title="Scene 3", file_type=FILE_TYPE_DRAFT, parent_id=parent.id, order=3),
        File(project_id=project.id, title="Scene 1", file_type=FILE_TYPE_DRAFT, parent_id=parent.id, order=1),
        File(project_id=project.id, title="Scene 2", file_type=FILE_TYPE_DRAFT, parent_id=parent.id, order=2),
    ]
    db_session.add_all(files)
    db_session.commit()

    # Query ordered by order field
    stmt = select(File).where(File.parent_id == parent.id).order_by(File.order)
    result = db_session.execute(stmt)
    children = result.scalars().all()

    assert children[0].title == "Scene 1"
    assert children[1].title == "Scene 2"
    assert children[2].title == "Scene 3"


@pytest.mark.unit
def test_file_metadata_json_serialization(db_session: Session):
    """Test File metadata JSON serialization and deserialization."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create file with complex metadata
    file = File(
        project_id=project.id,
        title="Character",
        file_type=FILE_TYPE_CHARACTER
    )

    complex_metadata = {
        "age": 30,
        "attributes": {
            "strength": 8,
            "intelligence": 10
        },
        "skills": ["sword", "magic"],
        "history": [
            {"year": 1000, "event": "born"},
            {"year": 1020, "event": "became hero"}
        ]
    }
    file.set_metadata(complex_metadata)

    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    # Verify metadata is correctly serialized and deserialized
    retrieved_metadata = file.get_metadata()
    assert retrieved_metadata == complex_metadata
    assert retrieved_metadata["attributes"]["strength"] == 8
    assert "sword" in retrieved_metadata["skills"]
    assert len(retrieved_metadata["history"]) == 2


@pytest.mark.unit
def test_file_metadata_get_field(db_session: Session):
    """Test File get_metadata_field method."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    file = File(
        project_id=project.id,
        title="Character",
        file_type=FILE_TYPE_CHARACTER
    )
    file.set_metadata({
        "age": 25,
        "name": "Hero"
    })

    db_session.add(file)
    db_session.commit()

    # Test get_metadata_field
    assert file.get_metadata_field("age") == 25
    assert file.get_metadata_field("name") == "Hero"
    assert file.get_metadata_field("nonexistent") is None
    assert file.get_metadata_field("nonexistent", default="N/A") == "N/A"


@pytest.mark.unit
def test_file_metadata_set_field(db_session: Session):
    """Test File set_metadata_field method."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    file = File(
        project_id=project.id,
        title="Character",
        file_type=FILE_TYPE_CHARACTER
    )
    file.set_metadata({"age": 25})

    db_session.add(file)
    db_session.commit()
    db_session.refresh(file)

    # Update single field
    file.set_metadata_field("age", 26)
    file.set_metadata_field("name", "Hero")
    db_session.commit()
    db_session.refresh(file)

    metadata = file.get_metadata()
    assert metadata["age"] == 26
    assert metadata["name"] == "Hero"


@pytest.mark.unit
def test_file_metadata_invalid_json():
    """Test File metadata handling with invalid JSON."""
    file = File(
        project_id="test-project-id",
        title="Test",
        file_type=FILE_TYPE_DRAFT
    )

    # Set invalid JSON directly
    file.file_metadata = "invalid json {"

    # get_metadata should return empty dict on error
    metadata = file.get_metadata()
    assert metadata == {}


@pytest.mark.unit
def test_file_metadata_none(db_session: Session):
    """Test File metadata when file_metadata is None."""
    user = User(username="testuser", email="test@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()

    project = Project(name="Test Novel", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    file = File(
        project_id=project.id,
        title="Test",
        file_type=FILE_TYPE_DRAFT
    )
    # file_metadata is None by default

    db_session.add(file)
    db_session.commit()

    # get_metadata should return empty dict
    metadata = file.get_metadata()
    assert metadata == {}

    # get_metadata_field should return default
    assert file.get_metadata_field("test", default="default") == "default"


@pytest.mark.unit
def test_draft_word_count_follows_every_content_write(db_session: Session):
    """AI 编辑/流式写入只改 content：缓存字数也必须跟着变，不能停在旧值。"""
    user = User(username="wc_writer", email="wc_writer@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()
    project = Project(name="字数", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    draft = File(project_id=project.id, title="第一章", content="山风", file_type=FILE_TYPE_DRAFT)
    # A cache written by an older code path (no revision) is not trusted.
    draft.set_metadata({"word_count": 2, "chapter_number": 1})
    db_session.add(draft)
    db_session.commit()
    assert cached_word_count(draft.file_metadata) is None

    draft.content = "山风吹过断崖，他回头看了一眼 village。"
    db_session.add(draft)
    db_session.commit()
    db_session.refresh(draft)

    assert cached_word_count(draft.file_metadata) == 14
    assert draft.get_metadata_field("chapter_number") == 1


@pytest.mark.unit
def test_metadata_only_updates_and_non_prose_files_keep_their_metadata(db_session: Session):
    user = User(username="wc_keep", email="wc_keep@example.com", hashed_password="pass")
    db_session.add(user)
    db_session.commit()
    project = Project(name="字数", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    outline = File(project_id=project.id, title="总纲", content="开端", file_type=FILE_TYPE_OUTLINE)
    db_session.add(outline)
    db_session.commit()
    outline.content = "开端、发展"
    db_session.add(outline)
    db_session.commit()
    assert outline.file_metadata is None

    draft = File(project_id=project.id, title="第一章", content="山风", file_type=FILE_TYPE_DRAFT)
    db_session.add(draft)
    db_session.commit()
    draft.title = "第一章 断崖"
    db_session.add(draft)
    db_session.commit()
    assert draft.file_metadata is None
