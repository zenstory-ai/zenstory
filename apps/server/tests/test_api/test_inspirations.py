"""
Tests for Inspirations API.

Tests inspiration submission, discovery and copying endpoints:
- POST /api/v1/inspirations - Submit project to inspiration library
- GET /api/v1/inspirations - List inspirations (public, with filtering and pagination)
- GET /api/v1/inspirations/featured - Get featured inspirations
- GET /api/v1/inspirations/{id} - Get inspiration detail
- POST /api/v1/inspirations/{id}/copy - Copy inspiration to user's workspace (requires auth)
"""

import json

import pytest
from httpx import AsyncClient

from models import Inspiration, Project, User
from models.file_model import File


@pytest.fixture(autouse=True)
def _enable_inspirations_for_existing_feature_tests(monkeypatch):
    """These tests exercise the module's behavior when explicitly enabled."""
    monkeypatch.setenv("INSPIRATIONS_ENABLED", "true")


@pytest.fixture
async def auth_client(client: AsyncClient, db_session) -> tuple[AsyncClient, User, str]:
    """
    Create an authenticated client with a verified user.

    Returns:
        Tuple of (client, user, access_token)
    """
    from services.core.auth_service import hash_password

    user = User(
        username="testuser",
        email="test@example.com",
        hashed_password=hash_password("testpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    # Login to get token
    login_response = await client.post(
        "/api/auth/login",
        data={"username": "testuser", "password": "testpassword123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    return client, user, token


@pytest.fixture
async def admin_auth_client(client: AsyncClient, db_session) -> tuple[AsyncClient, User, str]:
    """
    Create an authenticated superuser client.

    Returns:
        Tuple of (client, admin_user, access_token)
    """
    from services.core.auth_service import hash_password

    admin_user = User(
        username="admin_user",
        email="admin@example.com",
        hashed_password=hash_password("adminpassword123"),
        email_verified=True,
        is_active=True,
        is_superuser=True,
    )
    db_session.add(admin_user)
    db_session.commit()
    db_session.refresh(admin_user)

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "admin_user", "password": "adminpassword123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    return client, admin_user, token


def _create_project_with_file(db_session, owner_id: str, name: str = "Submit Source") -> Project:
    """Create a project with one file for submission tests."""
    project = Project(
        name=name,
        description="Source project for inspiration submission",
        owner_id=owner_id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    file = File(
        title="Chapter 1",
        content="Submission content",
        file_type="draft",
        project_id=project.id,
        order=0,
    )
    db_session.add(file)
    db_session.commit()

    return project


@pytest.fixture
def sample_inspiration(db_session) -> Inspiration:
    """
    Create a sample inspiration for testing.

    Returns:
        Inspiration instance
    """
    snapshot_data = {
        "project_name": "Sample Novel",
        "project_description": "A sample novel template",
        "project_type": "novel",
        "files": [
            {
                "id": "file-1",
                "title": "Chapter 1",
                "content": "Once upon a time...",
                "file_type": "draft",
                "parent_id": None,
                "order": 0,
                "file_metadata": None,
            },
            {
                "id": "file-2",
                "title": "Character Profile",
                "content": "Main character description",
                "file_type": "character",
                "parent_id": None,
                "order": 1,
                "file_metadata": None,
            },
        ],
    }

    inspiration = Inspiration(
        name="Sample Novel Template",
        description="A template for writing novels",
        cover_image="https://example.com/cover.jpg",
        project_type="novel",
        tags=json.dumps(["fiction", "adventure"], ensure_ascii=False),
        snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
        source="official",
        author_id=None,
        original_project_id=None,
        status="approved",
        copy_count=10,
        is_featured=True,
        sort_order=1,
    )
    db_session.add(inspiration)
    db_session.commit()
    db_session.refresh(inspiration)
    return inspiration


@pytest.fixture
def multiple_inspirations(db_session) -> list[Inspiration]:
    """
    Create multiple inspirations for pagination and filtering tests.

    Returns:
        List of Inspiration instances
    """
    inspirations = []

    # Create 15 inspirations with different types
    for i in range(15):
        project_type = "novel" if i < 5 else ("short" if i < 10 else "screenplay")
        is_featured = i % 3 == 0  # Every third one is featured

        snapshot_data = {
            "project_name": f"Project {i}",
            "project_description": f"Description {i}",
            "project_type": project_type,
            "files": [],
        }

        inspiration = Inspiration(
            name=f"Inspiration {i:02d}",
            description=f"Description for inspiration {i}",
            cover_image=None,
            project_type=project_type,
            tags=json.dumps([project_type, f"tag-{i % 3}"], ensure_ascii=False),
            snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
            source="official",
            status="approved",
            copy_count=i * 10,
            is_featured=is_featured,
            sort_order=i,
        )
        db_session.add(inspiration)
        inspirations.append(inspiration)

    db_session.commit()
    for insp in inspirations:
        db_session.refresh(insp)

    return inspirations


# ============================================
# Submit Inspiration Tests
# ============================================


@pytest.mark.integration
async def test_submit_inspiration_requires_auth(client: AsyncClient):
    """Submitting inspiration should require authentication."""
    response = await client.post(
        "/api/v1/inspirations",
        json={"project_id": "fake-project-id"},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_submit_inspiration_pending_for_regular_user(auth_client, db_session):
    """Regular user submission should create pending community inspiration."""
    client, user, token = auth_client
    project = _create_project_with_file(db_session, owner_id=user.id)

    response = await client.post(
        "/api/v1/inspirations",
        json={
            "project_id": project.id,
            "name": "My Community Inspiration",
            "tags": ["mystery", "urban"],
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["status"] == "pending"
    assert data["inspiration_id"]

    inspiration = db_session.get(Inspiration, data["inspiration_id"])
    assert inspiration is not None
    assert inspiration.author_id == user.id
    assert inspiration.source == "community"
    assert inspiration.status == "pending"


@pytest.mark.integration
async def test_submit_inspiration_superuser_auto_approved(admin_auth_client, db_session):
    """Superuser submission should bypass review and be approved immediately."""
    client, admin_user, token = admin_auth_client
    project = _create_project_with_file(db_session, owner_id=admin_user.id, name="Admin Source")

    response = await client.post(
        "/api/v1/inspirations",
        json={"project_id": project.id, "name": "Admin Published Inspiration"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    data = response.json()
    assert data["success"] is True
    assert data["status"] == "approved"

    inspiration = db_session.get(Inspiration, data["inspiration_id"])
    assert inspiration is not None
    assert inspiration.status == "approved"
    assert inspiration.reviewed_by == admin_user.id
    assert inspiration.reviewed_at is not None


@pytest.mark.integration
async def test_submit_inspiration_must_own_project(auth_client, db_session):
    """Users should not submit projects they do not own."""
    client, user, token = auth_client
    other = User(
        username="other_user",
        email="other@example.com",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
    )
    db_session.add(other)
    db_session.commit()
    db_session.refresh(other)

    project = _create_project_with_file(db_session, owner_id=other.id, name="Other Project")

    response = await client.post(
        "/api/v1/inspirations",
        json={"project_id": project.id},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_submit_inspiration_requires_project_files(auth_client, db_session):
    """Submitting inspiration from an empty project should fail with clear reason."""
    client, user, token = auth_client

    project = Project(
        name="Empty Project",
        description="No files here",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    response = await client.post(
        "/api/v1/inspirations",
        json={"project_id": project.id},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"] == "ERR_VALIDATION_ERROR"
    assert payload["error_detail"] == "Project has no files to include in inspiration"


# ============================================
# List Inspirations Tests
# ============================================


@pytest.mark.integration
async def test_list_inspirations_unauthenticated(client: AsyncClient, multiple_inspirations):
    """Test public access to list inspirations without authentication."""
    response = await client.get("/api/v1/inspirations")

    assert response.status_code == 200
    data = response.json()

    assert "inspirations" in data
    assert "total" in data
    assert "page" in data
    assert "page_size" in data

    assert data["total"] == 15
    assert data["page"] == 1
    assert data["page_size"] == 12  # Default page size
    assert len(data["inspirations"]) == 12  # First page


@pytest.mark.integration
async def test_get_my_submissions_requires_auth(client: AsyncClient):
    """Listing my submissions should require authentication."""
    response = await client.get("/api/v1/inspirations/my-submissions")
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_my_submissions_returns_current_user_only(auth_client, db_session):
    """Current user should only see their own submissions with mixed review status."""
    client, user, token = auth_client
    from services.core.auth_service import hash_password

    other_user = User(
        username="other_submitter",
        email="other_submitter@example.com",
        hashed_password=hash_password("otherpassword123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(other_user)
    db_session.commit()
    db_session.refresh(other_user)

    def add_submission(author_id: str, name: str, status: str, rejection_reason: str | None = None):
        db_session.add(
            Inspiration(
                name=name,
                description=f"{name} desc",
                project_type="novel",
                tags=json.dumps(["tag-x"], ensure_ascii=False),
                snapshot_data=json.dumps({"project_type": "novel", "files": []}, ensure_ascii=False),
                source="community",
                author_id=author_id,
                status=status,
                rejection_reason=rejection_reason,
            )
        )

    add_submission(user.id, "Mine Pending", "pending")
    add_submission(user.id, "Mine Approved", "approved")
    add_submission(user.id, "Mine Rejected", "rejected", "not enough quality")
    add_submission(other_user.id, "Other Pending", "pending")
    db_session.commit()

    response = await client.get(
        "/api/v1/inspirations/my-submissions?page=1&page_size=10",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 3
    assert payload["page"] == 1
    assert payload["page_size"] == 10
    assert len(payload["items"]) == 3
    assert all(item["name"] != "Other Pending" for item in payload["items"])
    assert {item["status"] for item in payload["items"]} == {"pending", "approved", "rejected"}
    rejected = next(item for item in payload["items"] if item["status"] == "rejected")
    assert rejected["rejection_reason"] == "not enough quality"


@pytest.mark.integration
async def test_list_inspirations_with_auth(auth_client):
    """Test authenticated access to list inspirations."""
    client, user, token = auth_client

    response = await client.get(
        "/api/v1/inspirations",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()

    assert "inspirations" in data
    assert isinstance(data["inspirations"], list)


@pytest.mark.integration
async def test_list_inspirations_pagination(client: AsyncClient, multiple_inspirations):
    """Test pagination parameters for listing inspirations."""
    # First page
    response = await client.get("/api/v1/inspirations?page=1&page_size=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data["inspirations"]) == 5
    assert data["total"] == 15
    assert data["page"] == 1

    # Second page
    response = await client.get("/api/v1/inspirations?page=2&page_size=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data["inspirations"]) == 5
    assert data["page"] == 2

    # Last page
    response = await client.get("/api/v1/inspirations?page=3&page_size=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data["inspirations"]) == 5

    # Beyond total pages (should return empty)
    response = await client.get("/api/v1/inspirations?page=10&page_size=5")
    assert response.status_code == 200
    data = response.json()
    assert len(data["inspirations"]) == 0


@pytest.mark.integration
async def test_list_inspirations_filter_project_type(client: AsyncClient, multiple_inspirations):
    """Test filtering inspirations by project type."""
    # Filter by novel
    response = await client.get("/api/v1/inspirations?project_type=novel")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    for insp in data["inspirations"]:
        assert insp["project_type"] == "novel"

    # Filter by short
    response = await client.get("/api/v1/inspirations?project_type=short")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    for insp in data["inspirations"]:
        assert insp["project_type"] == "short"

    # Filter by screenplay
    response = await client.get("/api/v1/inspirations?project_type=screenplay")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 5
    for insp in data["inspirations"]:
        assert insp["project_type"] == "screenplay"


@pytest.mark.integration
async def test_list_inspirations_search(client: AsyncClient, db_session):
    """Test searching inspirations by name and description."""
    # Create inspirations with specific names
    for name in ["Adventure Story", "Romance Tale", "Mystery Novel", "Sci-Fi Adventure"]:
        snapshot_data = {
            "project_name": name,
            "project_type": "novel",
            "files": [],
        }
        inspiration = Inspiration(
            name=name,
            description=f"Description for {name}",
            project_type="novel",
            tags="[]",
            snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
            source="official",
            status="approved",
        )
        db_session.add(inspiration)
    db_session.commit()

    # Search for "Adventure"
    response = await client.get("/api/v1/inspirations?search=Adventure")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2  # "Adventure Story" and "Sci-Fi Adventure"

    # Search for "Mystery"
    response = await client.get("/api/v1/inspirations?search=Mystery")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["inspirations"][0]["name"] == "Mystery Novel"

    # Search with no results
    response = await client.get("/api/v1/inspirations?search=NonexistentTerm")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0


@pytest.mark.integration
async def test_list_inspirations_featured_only(client: AsyncClient, multiple_inspirations):
    """Test filtering inspirations by featured status."""
    response = await client.get("/api/v1/inspirations?featured_only=true")
    assert response.status_code == 200
    data = response.json()

    # Should only return featured inspirations (every 3rd one from fixture = 5 total)
    assert data["total"] == 5
    for insp in data["inspirations"]:
        assert insp["is_featured"] is True


# ============================================
# Featured Inspirations Tests
# ============================================


@pytest.mark.integration
async def test_get_featured_inspirations(client: AsyncClient, db_session):
    """Test getting featured inspirations for homepage."""
    # Create featured and non-featured inspirations
    for i in range(10):
        snapshot_data = {"project_type": "novel", "files": []}
        inspiration = Inspiration(
            name=f"Featured {i}" if i < 3 else f"Regular {i}",
            project_type="novel",
            tags="[]",
            snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
            source="official",
            status="approved",
            is_featured=i < 3,  # First 3 are featured
            sort_order=i,
            copy_count=100 - i * 10,
        )
        db_session.add(inspiration)
    db_session.commit()

    response = await client.get("/api/v1/inspirations/featured?limit=6")

    assert response.status_code == 200
    data = response.json()

    assert isinstance(data, list)
    assert len(data) == 3  # Only 3 featured inspirations

    # All should be featured
    for insp in data:
        assert insp["is_featured"] is True


@pytest.mark.integration
async def test_get_featured_inspirations_limit(client: AsyncClient, db_session):
    """Test limit parameter for featured inspirations."""
    # Create 10 featured inspirations
    for i in range(10):
        snapshot_data = {"project_type": "novel", "files": []}
        inspiration = Inspiration(
            name=f"Featured {i}",
            project_type="novel",
            tags="[]",
            snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
            source="official",
            status="approved",
            is_featured=True,
            sort_order=i,
        )
        db_session.add(inspiration)
    db_session.commit()

    # Default limit
    response = await client.get("/api/v1/inspirations/featured")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 6  # Default limit is 6

    # Custom limit
    response = await client.get("/api/v1/inspirations/featured?limit=3")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 3


# ============================================
# Get Inspiration Detail Tests
# ============================================


@pytest.mark.integration
async def test_get_inspiration_detail(client: AsyncClient, sample_inspiration):
    """Test getting single inspiration detail."""
    response = await client.get(f"/api/v1/inspirations/{sample_inspiration.id}")

    assert response.status_code == 200
    data = response.json()

    assert data["id"] == sample_inspiration.id
    assert data["name"] == "Sample Novel Template"
    assert data["description"] == "A template for writing novels"
    assert data["project_type"] == "novel"
    assert data["tags"] == ["fiction", "adventure"]
    assert data["source"] == "official"
    assert data["copy_count"] == 10
    assert data["is_featured"] is True

    # Check file preview
    assert "file_preview" in data
    assert isinstance(data["file_preview"], list)
    assert len(data["file_preview"]) == 2


@pytest.mark.integration
async def test_get_inspiration_not_found(client: AsyncClient):
    """Test getting non-existent inspiration returns 404."""
    response = await client.get("/api/v1/inspirations/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    data = response.json()
    assert "detail" in data or "error" in data or "message" in data


@pytest.mark.integration
async def test_public_inspiration_endpoints_hide_internal_ids(auth_client, db_session):
    """Public inspiration endpoints should not expose author/project IDs."""
    client, user, _ = auth_client
    project = _create_project_with_file(db_session, owner_id=user.id, name="Source For Public")

    snapshot_data = {
        "project_name": project.name,
        "project_description": project.description,
        "project_type": project.project_type,
        "files": [],
    }
    inspiration = Inspiration(
        name="Public Safe Inspiration",
        description="Used to verify public payload redaction",
        project_type="novel",
        tags=json.dumps(["safe"], ensure_ascii=False),
        snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
        source="community",
        author_id=user.id,
        original_project_id=project.id,
        status="approved",
    )
    db_session.add(inspiration)
    db_session.commit()
    db_session.refresh(inspiration)

    list_response = await client.get("/api/v1/inspirations")
    assert list_response.status_code == 200
    listed = next(item for item in list_response.json()["inspirations"] if item["id"] == inspiration.id)
    assert listed["author_id"] is None
    assert listed["original_project_id"] is None

    detail_response = await client.get(f"/api/v1/inspirations/{inspiration.id}")
    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["author_id"] is None
    assert detail_payload["original_project_id"] is None


@pytest.mark.integration
async def test_get_inspiration_unapproved(client: AsyncClient, db_session):
    """Test getting unapproved inspiration returns 404."""
    snapshot_data = {"project_type": "novel", "files": []}
    inspiration = Inspiration(
        name="Unapproved Inspiration",
        project_type="novel",
        tags="[]",
        snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
        source="community",
        status="pending",  # Not approved
    )
    db_session.add(inspiration)
    db_session.commit()
    db_session.refresh(inspiration)

    response = await client.get(f"/api/v1/inspirations/{inspiration.id}")

    assert response.status_code == 404


# ============================================
# Copy Inspiration Tests
# ============================================


@pytest.mark.integration
async def test_copy_inspiration_requires_auth(client: AsyncClient, sample_inspiration):
    """Test that copying inspiration requires authentication."""
    response = await client.post(
        f"/api/v1/inspirations/{sample_inspiration.id}/copy",
        json={},
    )

    assert response.status_code == 401


@pytest.mark.integration
async def test_copy_inspiration_success(auth_client, sample_inspiration, db_session):
    """Test successful copy of inspiration to user's workspace."""
    client, user, token = auth_client

    # Get initial copy count
    initial_copy_count = sample_inspiration.copy_count

    response = await client.post(
        f"/api/v1/inspirations/{sample_inspiration.id}/copy",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["message"] == "Inspiration copied successfully"
    assert data["project_id"] is not None
    assert data["project_name"] == sample_inspiration.name

    # Verify project was created
    from sqlmodel import select

    stmt = select(Project).where(Project.id == data["project_id"])
    project = db_session.exec(stmt).first()
    assert project is not None
    assert project.name == sample_inspiration.name
    assert project.owner_id == user.id
    assert project.project_type == sample_inspiration.project_type

    # Verify files were created
    stmt = select(File).where(File.project_id == project.id)
    files = db_session.exec(stmt).all()
    assert len(files) == 2

    # Verify copy count was incremented
    db_session.refresh(sample_inspiration)
    assert sample_inspiration.copy_count == initial_copy_count + 1


@pytest.mark.integration
async def test_copy_inspiration_custom_name(auth_client, sample_inspiration, db_session):
    """Test copying inspiration with custom project name."""
    client, user, token = auth_client

    custom_name = "My Custom Novel Name"

    response = await client.post(
        f"/api/v1/inspirations/{sample_inspiration.id}/copy",
        json={"project_name": custom_name},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()

    assert data["success"] is True
    assert data["project_name"] == custom_name

    # Verify project has custom name
    from sqlmodel import select

    stmt = select(Project).where(Project.id == data["project_id"])
    project = db_session.exec(stmt).first()
    assert project.name == custom_name


@pytest.mark.integration
async def test_copy_inspiration_respects_project_limit(auth_client, sample_inspiration, db_session):
    """Copying an inspiration should not bypass the active project limit."""
    client, user, token = auth_client

    # Free plan defaults to max 3 projects. Create 3 active projects first.
    for i in range(3):
        db_session.add(Project(name=f"Existing Project {i}", owner_id=user.id, project_type="novel"))
    db_session.commit()

    response = await client.post(
        f"/api/v1/inspirations/{sample_inspiration.id}/copy",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 402
    payload = response.json()
    assert payload["detail"] == "ERR_QUOTA_PROJECTS_EXCEEDED"
    assert payload["error_code"] == "ERR_QUOTA_PROJECTS_EXCEEDED"

    # Verify no new project is created
    from sqlmodel import select

    projects = db_session.exec(
        select(Project).where(Project.owner_id == user.id, Project.is_deleted.is_(False))
    ).all()
    assert len(projects) == 3

    # Verify inspiration copy_count not incremented
    db_session.refresh(sample_inspiration)
    assert sample_inspiration.copy_count == 10


@pytest.mark.integration
async def test_copy_inspiration_not_found(auth_client):
    """Test copying non-existent inspiration returns 404."""
    client, user, token = auth_client

    response = await client.post(
        "/api/v1/inspirations/00000000-0000-0000-0000-000000000000/copy",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_copy_inspiration_with_file_hierarchy(auth_client, db_session):
    """Test copying inspiration with hierarchical file structure."""
    client, user, token = auth_client

    # Create inspiration with parent-child file relationship
    snapshot_data = {
        "project_type": "novel",
        "files": [
            {
                "id": "parent-file",
                "title": "Parent Folder",
                "content": "",
                "file_type": "outline",
                "parent_id": None,
                "order": 0,
                "file_metadata": None,
            },
            {
                "id": "child-file",
                "title": "Child Document",
                "content": "Content of child",
                "file_type": "draft",
                "parent_id": "parent-file",
                "order": 0,
                "file_metadata": None,
            },
        ],
    }

    inspiration = Inspiration(
        name="Hierarchical Template",
        project_type="novel",
        tags="[]",
        snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
        source="official",
        status="approved",
    )
    db_session.add(inspiration)
    db_session.commit()
    db_session.refresh(inspiration)

    response = await client.post(
        f"/api/v1/inspirations/{inspiration.id}/copy",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()

    # Verify files were created with hierarchy
    from sqlmodel import select

    stmt = select(File).where(File.project_id == data["project_id"])
    files = db_session.exec(stmt).all()
    assert len(files) == 2

    # Find parent and child
    parent = next((f for f in files if f.title == "Parent Folder"), None)
    child = next((f for f in files if f.title == "Child Document"), None)

    assert parent is not None
    assert child is not None
    assert child.parent_id == parent.id


# ============================================
# Admin Inspirations Tests
# ============================================


@pytest.mark.integration
async def test_admin_inspirations_list_returns_items_total(admin_auth_client, db_session):
    """Admin list endpoint should return {items, total} with parsed tag arrays."""
    client, admin_user, token = admin_auth_client
    snapshot_data = {"project_type": "novel", "files": []}

    db_session.add(
        Inspiration(
            name="Pending Inspiration",
            project_type="novel",
            tags=json.dumps(["tag-a", "tag-b"], ensure_ascii=False),
            snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
            source="community",
            status="pending",
            author_id=admin_user.id,
        )
    )
    db_session.add(
        Inspiration(
            name="Approved Inspiration",
            project_type="novel",
            tags=json.dumps(["tag-c"], ensure_ascii=False),
            snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
            source="official",
            status="approved",
            author_id=admin_user.id,
        )
    )
    db_session.commit()

    response = await client.get(
        "/api/admin/inspirations?status=pending",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "items" in payload
    assert "total" in payload
    assert payload["total"] == 1
    assert isinstance(payload["items"], list)
    assert payload["items"][0]["name"] == "Pending Inspiration"
    assert payload["items"][0]["tags"] == ["tag-a", "tag-b"]


@pytest.mark.integration
async def test_admin_review_reject_requires_reason(admin_auth_client, db_session):
    """Rejecting an inspiration should require a non-empty reason."""
    client, admin_user, token = admin_auth_client
    snapshot_data = {"project_type": "novel", "files": []}
    inspiration = Inspiration(
        name="Pending Without Reason",
        project_type="novel",
        tags="[]",
        snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
        source="community",
        status="pending",
        author_id=admin_user.id,
    )
    db_session.add(inspiration)
    db_session.commit()
    db_session.refresh(inspiration)

    response = await client.post(
        f"/api/admin/inspirations/{inspiration.id}/review",
        json={"approve": False},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"] == "ERR_VALIDATION_ERROR"
    assert payload["error_detail"] == "Rejection reason is required when rejecting inspiration"


@pytest.mark.integration
async def test_copy_inspiration_rolls_back_when_quota_consume_fails(
    auth_client,
    sample_inspiration,
    db_session,
    monkeypatch,
):
    """Copy should be rolled back if quota consumption loses a concurrency race."""
    client, user, token = auth_client

    monkeypatch.setattr(
        "api.inspirations.quota_service.check_feature_quota",
        lambda _session, _user_id, _feature: (True, 0, 1),
    )
    monkeypatch.setattr(
        "api.inspirations.quota_service.reserve_feature_quota",
        lambda _session, _user_id, _feature, *, commit: None,
    )

    response = await client.post(
        f"/api/v1/inspirations/{sample_inspiration.id}/copy",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 402

    from sqlmodel import select

    projects = db_session.exec(select(Project).where(Project.owner_id == user.id)).all()
    assert len(projects) == 0

    db_session.refresh(sample_inspiration)
    assert sample_inspiration.copy_count == 10


@pytest.mark.integration
async def test_admin_create_inspiration_excludes_soft_deleted_files(admin_auth_client, db_session):
    """Admin-created inspiration snapshots should not include soft-deleted files."""
    client, admin_user, token = admin_auth_client

    project = Project(
        name="Admin Source Project",
        description="Project for admin inspiration creation",
        owner_id=admin_user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    db_session.add(
        File(
            project_id=project.id,
            title="Active File",
            content="keep me",
            file_type="draft",
            order=0,
            is_deleted=False,
        )
    )
    db_session.add(
        File(
            project_id=project.id,
            title="Deleted File",
            content="should not be exported",
            file_type="draft",
            order=1,
            is_deleted=True,
        )
    )
    db_session.commit()

    response = await client.post(
        "/api/admin/inspirations",
        json={"project_id": project.id, "source": "official"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 201
    inspiration_id = response.json()["id"]
    inspiration = db_session.get(Inspiration, inspiration_id)
    snapshot = json.loads(inspiration.snapshot_data)
    assert [f["title"] for f in snapshot["files"]] == ["Active File"]


@pytest.mark.integration
async def test_admin_patch_inspiration_rejects_status_updates(admin_auth_client, db_session):
    """Status should not be mutable via generic patch endpoint."""
    client, admin_user, token = admin_auth_client
    snapshot_data = {"project_type": "novel", "files": []}
    inspiration = Inspiration(
        name="Pending Item",
        project_type="novel",
        tags="[]",
        snapshot_data=json.dumps(snapshot_data, ensure_ascii=False),
        source="community",
        status="pending",
        author_id=admin_user.id,
    )
    db_session.add(inspiration)
    db_session.commit()
    db_session.refresh(inspiration)

    response = await client.patch(
        f"/api/admin/inspirations/{inspiration.id}",
        json={"status": "approved"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
    db_session.refresh(inspiration)
    assert inspiration.status == "pending"
