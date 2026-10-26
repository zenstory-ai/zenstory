"""
Tests for InspirationService.

Unit tests for the inspiration management service, covering:
- Creating inspirations from projects
- Copying inspirations to user workspaces
- Listing and filtering inspirations
- Getting featured inspirations
- Copy count tracking
"""

import json

import pytest
from sqlmodel import Session

from models.entities import Project, User
from models.file_model import File
from models.inspiration import Inspiration
from services.inspiration_service import (
    copy_inspiration_to_project,
    create_inspiration_from_project,
    get_featured_inspirations,
    get_inspiration_detail,
    increment_copy_count,
    list_inspirations,
)


@pytest.fixture
def test_user(db_session: Session):
    """Create a test user."""
    user = User(
        email="test@example.com",
        username="testuser",
        hashed_password="hashed_password",
        name="Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def test_project_with_files(db_session: Session, test_user: User):
    """Create a test project with files."""
    # Create project
    project = Project(
        name="Test Novel Project",
        description="A test novel project",
        owner_id=test_user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    # Create files
    file1 = File(
        title="Chapter 1",
        content="Content of chapter 1",
        file_type="draft",
        project_id=project.id,
        order=1,
    )
    file2 = File(
        title="Main Character",
        content="Character profile",
        file_type="character",
        project_id=project.id,
        order=2,
    )
    db_session.add(file1)
    db_session.add(file2)
    db_session.commit()
    db_session.refresh(file1)
    db_session.refresh(file2)

    return project, [file1, file2]


@pytest.fixture
def test_inspiration(db_session: Session, test_project_with_files):
    """Create a test inspiration."""
    project, files = test_project_with_files

    inspiration = create_inspiration_from_project(
        session=db_session,
        project=project,
        files=files,
        source="official",
        name="Test Inspiration",
        description="A test inspiration template",
        tags=["fantasy", "adventure"],
        is_featured=True,
    )
    db_session.add(inspiration)
    db_session.commit()
    db_session.refresh(inspiration)

    return inspiration


@pytest.fixture
def multiple_inspirations(db_session: Session, test_user: User):
    """Create multiple inspirations with various attributes."""
    inspirations = []

    # Create 3 projects with files
    for i in range(3):
        project = Project(
            name=f"Project {i}",
            description=f"Description {i}",
            owner_id=test_user.id,
            project_type=["novel", "short", "screenplay"][i],
        )
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        file = File(
            title=f"File {i}",
            content=f"Content {i}",
            file_type="draft",
            project_id=project.id,
        )
        db_session.add(file)
        db_session.commit()
        db_session.refresh(file)

        inspiration = create_inspiration_from_project(
            session=db_session,
            project=project,
            files=[file],
            source="official",
            name=f"Inspiration {i}",
            description=f"Description for inspiration {i}",
            tags=["tag1", "tag2"],
            is_featured=(i == 0),  # First one is featured
        )
        inspiration.status = "approved"
        inspiration.copy_count = i * 10  # Varying copy counts
        db_session.add(inspiration)
        db_session.commit()
        db_session.refresh(inspiration)
        inspirations.append(inspiration)

    return inspirations


@pytest.mark.unit
class TestCreateInspirationFromProject:
    """Tests for create_inspiration_from_project function."""

    def test_create_inspiration_from_project(self, db_session: Session, test_project_with_files):
        """Test creating inspiration from project files."""
        project, files = test_project_with_files

        inspiration = create_inspiration_from_project(
            session=db_session,
            project=project,
            files=files,
            source="official",
            name="My Inspiration",
            description="Test description",
            tags=["fantasy", "adventure"],
            is_featured=True,
        )

        assert inspiration.name == "My Inspiration"
        assert inspiration.description == "Test description"
        assert inspiration.project_type == "novel"
        assert inspiration.source == "official"
        assert inspiration.is_featured is True

        # Check tags are serialized
        tags = json.loads(inspiration.tags)
        assert tags == ["fantasy", "adventure"]

        # Check snapshot data
        snapshot = json.loads(inspiration.snapshot_data)
        assert snapshot["project_name"] == "Test Novel Project"
        assert snapshot["project_description"] == "A test novel project"
        assert snapshot["project_type"] == "novel"
        assert len(snapshot["files"]) == 2
        assert snapshot["files"][0]["title"] == "Chapter 1"
        assert snapshot["files"][1]["title"] == "Main Character"

    def test_create_inspiration_defaults_to_project_name(self, db_session: Session, test_project_with_files):
        """Test that inspiration name defaults to project name."""
        project, files = test_project_with_files

        inspiration = create_inspiration_from_project(
            session=db_session,
            project=project,
            files=files,
            source="community",
        )

        assert inspiration.name == project.name
        assert inspiration.description == project.description

    def test_create_inspiration_with_author(self, db_session: Session, test_project_with_files, test_user):
        """Test creating community inspiration with author."""
        project, files = test_project_with_files

        inspiration = create_inspiration_from_project(
            session=db_session,
            project=project,
            files=files,
            source="community",
            author=test_user,
        )

        assert inspiration.author_id == test_user.id
        assert inspiration.source == "community"
        assert inspiration.status == "pending"  # Community inspirations are pending by default

    def test_create_official_inspiration_auto_approved(self, db_session: Session, test_project_with_files):
        """Test that official inspirations are automatically approved."""
        project, files = test_project_with_files

        inspiration = create_inspiration_from_project(
            session=db_session,
            project=project,
            files=files,
            source="official",
        )

        assert inspiration.status == "approved"

    def test_create_inspiration_includes_project_status_fields(
        self, db_session: Session, test_project_with_files
    ):
        """Snapshot should include project status fields for fork continuity."""
        project, files = test_project_with_files
        project.summary = "Project summary"
        project.current_phase = "Drafting"
        project.writing_style = "First person, concise"
        project.notes = "Keep emotional arc consistent"
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        inspiration = create_inspiration_from_project(
            session=db_session,
            project=project,
            files=files,
            source="official",
        )

        snapshot = json.loads(inspiration.snapshot_data)
        assert snapshot["project_summary"] == "Project summary"
        assert snapshot["project_current_phase"] == "Drafting"
        assert snapshot["project_writing_style"] == "First person, concise"
        assert snapshot["project_notes"] == "Keep emotional arc consistent"


@pytest.mark.unit
class TestCopyInspirationToProject:
    """Tests for copy_inspiration_to_project function."""

    def test_copy_inspiration_to_project(self, db_session: Session, test_inspiration, test_user):
        """Test copying inspiration to user's workspace."""
        project = copy_inspiration_to_project(
            session=db_session,
            inspiration=test_inspiration,
            user=test_user,
        )

        assert project is not None
        assert project.name == test_inspiration.name
        assert project.owner_id == test_user.id
        assert project.project_type == test_inspiration.project_type

    def test_copy_drops_legacy_proposal_block_from_old_snapshot(
        self, db_session: Session, test_inspiration, test_user
    ):
        """Snapshots taken before the proposal removal may still carry the legacy block."""
        snapshot = json.loads(test_inspiration.snapshot_data)
        snapshot["project_notes"] = "作者约定\n\n【AI 提议·待作者确认】\n旧提议"
        test_inspiration.snapshot_data = json.dumps(snapshot, ensure_ascii=False)
        db_session.add(test_inspiration)
        db_session.commit()

        project = copy_inspiration_to_project(
            session=db_session,
            inspiration=test_inspiration,
            user=test_user,
        )

        assert project.notes == "作者约定"

    def test_copy_inspiration_with_custom_name(self, db_session: Session, test_inspiration, test_user):
        """Test copying inspiration with custom project name."""
        custom_name = "My Custom Project"
        project = copy_inspiration_to_project(
            session=db_session,
            inspiration=test_inspiration,
            user=test_user,
            project_name=custom_name,
        )

        assert project.name == custom_name

    def test_copy_increments_count(self, db_session: Session, test_inspiration, test_user):
        """Test that copying increments the copy_count."""
        initial_count = test_inspiration.copy_count

        copy_inspiration_to_project(
            session=db_session,
            inspiration=test_inspiration,
            user=test_user,
        )

        db_session.refresh(test_inspiration)
        assert test_inspiration.copy_count == initial_count + 1

    def test_copy_creates_files_from_snapshot(self, db_session: Session, test_inspiration, test_user):
        """Test that files are created from snapshot data."""
        project = copy_inspiration_to_project(
            session=db_session,
            inspiration=test_inspiration,
            user=test_user,
        )

        # Get all files for the new project
        files = db_session.exec(
            File.__table__.select().where(File.project_id == project.id)
        ).all()

        assert len(files) == 2
        file_titles = [f.title for f in files]
        assert "Chapter 1" in file_titles
        assert "Main Character" in file_titles

    def test_copy_preserves_file_content(self, db_session: Session, test_inspiration, test_user):
        """Test that file content is preserved in copy."""
        project = copy_inspiration_to_project(
            session=db_session,
            inspiration=test_inspiration,
            user=test_user,
        )

        files = db_session.exec(
            File.__table__.select().where(File.project_id == project.id)
        ).all()

        chapter_file = next(f for f in files if f.title == "Chapter 1")
        assert chapter_file.content == "Content of chapter 1"

    def test_copy_with_invalid_snapshot_data(self, db_session: Session, test_user):
        """Test copying inspiration with invalid snapshot data raises ValueError."""
        # Create inspiration with invalid snapshot
        inspiration = Inspiration(
            name="Invalid Inspiration",
            snapshot_data="not valid json",
            project_type="novel",
            status="approved",
        )
        db_session.add(inspiration)
        db_session.commit()
        db_session.refresh(inspiration)

        with pytest.raises(ValueError, match="Invalid inspiration data"):
            copy_inspiration_to_project(
                session=db_session,
                inspiration=inspiration,
                user=test_user,
            )

    def test_copy_restores_project_status_fields(
        self, db_session: Session, test_project_with_files, test_user
    ):
        """Copy should restore project-level status context from snapshot."""
        project, files = test_project_with_files
        project.summary = "Story summary"
        project.current_phase = "Revision"
        project.writing_style = "Lyrical"
        project.notes = "Track foreshadowing"
        db_session.add(project)
        db_session.commit()
        db_session.refresh(project)

        inspiration = create_inspiration_from_project(
            session=db_session,
            project=project,
            files=files,
            source="official",
        )
        db_session.add(inspiration)
        db_session.commit()
        db_session.refresh(inspiration)

        copied_project = copy_inspiration_to_project(
            session=db_session,
            inspiration=inspiration,
            user=test_user,
        )

        assert copied_project.summary == "Story summary"
        assert copied_project.current_phase == "Revision"
        assert copied_project.writing_style == "Lyrical"
        assert copied_project.notes == "Track foreshadowing"


@pytest.mark.unit
class TestListInspirations:
    """Tests for list_inspirations function."""

    def test_list_inspirations_pagination(self, db_session: Session, multiple_inspirations):
        """Test pagination works correctly."""
        # Get first page
        page1, total = list_inspirations(
            session=db_session,
            page=1,
            page_size=2,
        )

        assert len(page1) == 2
        assert total == 3

        # Get second page
        page2, total = list_inspirations(
            session=db_session,
            page=2,
            page_size=2,
        )

        assert len(page2) == 1
        assert total == 3

    def test_list_inspirations_filter_by_project_type(self, db_session: Session, multiple_inspirations):
        """Test filtering by project type."""
        novel_inspirations, total = list_inspirations(
            session=db_session,
            project_type="novel",
        )

        assert len(novel_inspirations) == 1
        assert novel_inspirations[0].project_type == "novel"

        short_inspirations, total = list_inspirations(
            session=db_session,
            project_type="short",
        )

        assert len(short_inspirations) == 1
        assert short_inspirations[0].project_type == "short"

    def test_list_inspirations_search(self, db_session: Session, multiple_inspirations):
        """Test search in name/description."""
        results, total = list_inspirations(
            session=db_session,
            search="Inspiration 1",
        )

        assert len(results) == 1
        assert results[0].name == "Inspiration 1"

    def test_list_inspirations_search_description(self, db_session: Session, multiple_inspirations):
        """Test search in description."""
        results, total = list_inspirations(
            session=db_session,
            search="Description for inspiration",
        )

        assert len(results) == 3  # All match

    def test_list_inspirations_featured_only(self, db_session: Session, multiple_inspirations):
        """Test featured filter."""
        featured, total = list_inspirations(
            session=db_session,
            featured_only=True,
        )

        assert len(featured) == 1
        assert featured[0].is_featured is True

    def test_list_inspirations_excludes_pending(self, db_session: Session, test_project_with_files):
        """Test that pending inspirations are excluded."""
        project, files = test_project_with_files

        # Create pending inspiration
        pending = create_inspiration_from_project(
            session=db_session,
            project=project,
            files=files,
            source="community",
        )
        pending.status = "pending"
        db_session.add(pending)
        db_session.commit()

        results, total = list_inspirations(session=db_session)
        assert all(i.status == "approved" for i in results)

    def test_list_inspirations_ordering(self, db_session: Session, multiple_inspirations):
        """Test that inspirations are ordered by featured, copy_count, created_at."""
        results, _ = list_inspirations(session=db_session)

        # First should be featured
        assert results[0].is_featured is True

        # Others should be ordered by copy_count (descending)
        assert results[1].copy_count >= results[2].copy_count if len(results) > 2 else True


@pytest.mark.unit
class TestGetFeaturedInspirations:
    """Tests for get_featured_inspirations function."""

    def test_get_featured_inspirations(self, db_session: Session, multiple_inspirations):
        """Test getting featured list."""
        featured = get_featured_inspirations(session=db_session)

        assert len(featured) == 1
        assert featured[0].is_featured is True

    def test_get_featured_inspirations_limit(self, db_session: Session, test_project_with_files):
        """Test limit parameter."""
        project, files = test_project_with_files

        # Create multiple featured inspirations
        for i in range(10):
            inspiration = create_inspiration_from_project(
                session=db_session,
                project=project,
                files=files,
                source="official",
                name=f"Featured {i}",
                is_featured=True,
            )
            inspiration.status = "approved"
            db_session.add(inspiration)
        db_session.commit()

        featured = get_featured_inspirations(session=db_session, limit=5)
        assert len(featured) <= 5

    def test_get_featured_excludes_non_approved(self, db_session: Session, test_project_with_files):
        """Test that non-approved inspirations are excluded."""
        project, files = test_project_with_files

        # Create featured but pending inspiration
        pending = create_inspiration_from_project(
            session=db_session,
            project=project,
            files=files,
            source="community",
            name="Pending Featured",
            is_featured=True,
        )
        pending.status = "pending"
        db_session.add(pending)
        db_session.commit()

        featured = get_featured_inspirations(session=db_session)
        assert all(f.status == "approved" for f in featured)


@pytest.mark.unit
class TestGetInspirationDetail:
    """Tests for get_inspiration_detail function."""

    def test_get_inspiration_detail(self, db_session: Session, test_inspiration):
        """Test getting single inspiration."""
        result = get_inspiration_detail(
            session=db_session,
            inspiration_id=test_inspiration.id,
        )

        assert result is not None
        assert result.id == test_inspiration.id
        assert result.name == test_inspiration.name

    def test_get_inspiration_detail_not_found(self, db_session: Session):
        """Test getting non-existent inspiration returns None."""
        result = get_inspiration_detail(
            session=db_session,
            inspiration_id="non-existent-id",
        )

        assert result is None

    def test_get_inspiration_detail_excludes_pending(self, db_session: Session, test_project_with_files):
        """Test that pending inspirations are not returned."""
        project, files = test_project_with_files

        pending = create_inspiration_from_project(
            session=db_session,
            project=project,
            files=files,
            source="community",
        )
        pending.status = "pending"
        db_session.add(pending)
        db_session.commit()
        db_session.refresh(pending)

        result = get_inspiration_detail(
            session=db_session,
            inspiration_id=pending.id,
        )

        assert result is None


@pytest.mark.unit
class TestIncrementCopyCount:
    """Tests for increment_copy_count function."""

    def test_increment_copy_count(self, db_session: Session, test_inspiration):
        """Test that copy count is incremented."""
        initial_count = test_inspiration.copy_count

        increment_copy_count(
            session=db_session,
            inspiration_id=test_inspiration.id,
        )

        db_session.refresh(test_inspiration)
        assert test_inspiration.copy_count == initial_count + 1

    def test_increment_copy_count_multiple_times(self, db_session: Session, test_inspiration):
        """Test incrementing copy count multiple times."""
        initial_count = test_inspiration.copy_count

        for _ in range(5):
            increment_copy_count(
                session=db_session,
                inspiration_id=test_inspiration.id,
            )
            db_session.refresh(test_inspiration)

        assert test_inspiration.copy_count == initial_count + 5

    def test_increment_copy_count_non_existent(self, db_session: Session):
        """Test that incrementing non-existent inspiration doesn't raise error."""
        # Should not raise an error
        increment_copy_count(
            session=db_session,
            inspiration_id="non-existent-id",
        )
