"""
Tests for Projects API.

Tests project CRUD operations:
- GET /api/v1/projects - List projects
- POST /api/v1/projects - Create project
- GET /api/v1/projects/{id} - Get project details
- PUT /api/v1/projects/{id} - Update project
- DELETE /api/v1/projects/{id} - Delete project (soft delete)
- PATCH /api/v1/projects/{id} - Partially update project
"""

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from config.project_status import PROJECT_STATUS_MAX_LENGTHS
from models import File, Project, User


@pytest.mark.integration
async def test_get_projects_empty_list(client: AsyncClient, db_session):
    """Test getting projects when user has no projects."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user1", email="user1@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user1", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    response = await client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.integration
async def test_get_projects_with_projects(client: AsyncClient, db_session):
    """Test getting projects when user has multiple projects."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user2", email="user2@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user2", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create projects
    project1 = Project(name="Project 1", owner_id=user.id)
    project2 = Project(name="Project 2", owner_id=user.id)
    db_session.add(project1)
    db_session.add(project2)
    db_session.commit()

    # Get projects
    response = await client.get("/api/v1/projects", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert any(p["name"] == "Project 1" for p in data)
    assert any(p["name"] == "Project 2" for p in data)


@pytest.mark.integration
async def test_create_project_success(client: AsyncClient, db_session):
    """Test creating a new project successfully."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user3", email="user3@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user3", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    response = await client.post(
        "/api/v1/projects",
        json={"name": "My Novel", "project_type": "novel"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["name"] == "My Novel"
    assert data["project_type"] == "novel"
    assert data["owner_id"] == user.id


@pytest.mark.integration
async def test_create_project_initializes_folder_structure_for_screenplay(client: AsyncClient, db_session):
    """Test screenplay project creates expected root folders."""
    from services.core.auth_service import hash_password
    user = User(
        username="user3_screenplay", email="user3_screenplay@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user3_screenplay", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    response = await client.post(
        "/api/v1/projects",
        json={"name": "My Screenplay", "project_type": "screenplay"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    project_id = response.json()["id"]

    folders = db_session.exec(
        select(File)
        .where(File.project_id == project_id, File.file_type == "folder")
        .order_by(File.order)
    ).all()
    assert [folder.title for folder in folders] == ["角色", "设定", "素材", "分集大纲", "剧本"]


@pytest.mark.integration
async def test_create_project_initializes_folder_structure_for_short(client: AsyncClient, db_session):
    """Test short story project creates expected root folders."""
    from services.core.auth_service import hash_password

    user = User(
        username="user3_short",
        email="user3_short@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user3_short", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    response = await client.post(
        "/api/v1/projects",
        json={"name": "My Short Story", "project_type": "short"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    project_id = response.json()["id"]

    folders = db_session.exec(
        select(File)
        .where(File.project_id == project_id, File.file_type == "folder")
        .order_by(File.order)
    ).all()
    assert [folder.title for folder in folders] == ["人物", "构思", "素材", "正文"]


@pytest.mark.integration
async def test_create_project_rejects_invalid_project_type(client: AsyncClient, db_session):
    """Test create project rejects unsupported project_type."""
    from services.core.auth_service import hash_password
    user = User(
        username="user3_invalid_type", email="user3_invalid_type@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user3_invalid_type", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    response = await client.post(
        "/api/v1/projects",
        json={"name": "Bad Type Project", "project_type": "essay"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_create_project_rolls_back_when_folder_init_fails(client: AsyncClient, db_session, monkeypatch):
    """Test project is not persisted when folder initialization fails."""
    from services.core.auth_service import hash_password
    user = User(
        username="user3_rollback", email="user3_rollback@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user3_rollback", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    def broken_folders(*_args, **_kwargs):
        raise RuntimeError("template failure")

    monkeypatch.setattr("services.project_service.get_folders_for_type", broken_folders)

    response = await client.post(
        "/api/v1/projects",
        json={"name": "Rollback Project", "project_type": "novel"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 500

    projects = db_session.exec(
        select(Project).where(
            Project.owner_id == user.id,
            Project.name == "Rollback Project",
        )
    ).all()
    assert projects == []


@pytest.mark.integration
async def test_get_project_success(client: AsyncClient, db_session):
    """Test getting a specific project successfully."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user4", email="user4@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user4", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Get project
    response = await client.get(f"/api/v1/projects/{project.id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == project.id
    assert data["name"] == "Test Project"


@pytest.mark.integration
async def test_admin_can_get_other_user_project(client: AsyncClient, db_session):
    """Superuser/admin should be able to access projects owned by other users."""
    from services.core.auth_service import hash_password

    owner = User(
        username="proj_owner_1",
        email="proj_owner_1@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    admin = User(
        username="admin_proj_access",
        email="admin_proj_access@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        is_superuser=True,
    )
    db_session.add(owner)
    db_session.add(admin)
    db_session.commit()

    project = Project(name="Owner Project", owner_id=owner.id)
    db_session.add(project)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "admin_proj_access", "password": "password123"},
    )
    assert login_response.status_code == 200
    admin_token = login_response.json()["access_token"]

    response = await client.get(
        f"/api/v1/projects/{project.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json()["id"] == project.id


@pytest.mark.integration
async def test_get_project_not_found(client: AsyncClient, db_session):
    """Test getting a nonexistent project returns 404."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user5", email="user5@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user5", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Try to get nonexistent project
    response = await client.get(
        "/api/v1/projects/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_update_project_success(client: AsyncClient, db_session):
    """Test updating a project successfully."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user6", email="user6@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user6", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="Old Name", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Update project
    response = await client.put(
        f"/api/v1/projects/{project.id}",
        json={"name": "New Name", "description": "Updated description"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "New Name"
    assert data["description"] == "Updated description"


@pytest.mark.integration
async def test_update_project_rejects_invalid_project_type(client: AsyncClient, db_session):
    """Test update project rejects unsupported project_type."""
    from services.core.auth_service import hash_password
    user = User(
        username="user6_invalid_type", email="user6_invalid_type@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user6_invalid_type", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Type Test", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()

    response = await client.put(
        f"/api/v1/projects/{project.id}",
        json={"project_type": "essay"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_update_project_rejects_protected_fields(client: AsyncClient, db_session):
    """Test update endpoint rejects unknown/protected project fields."""
    from services.core.auth_service import hash_password
    user = User(
        username="user6_protected", email="user6_protected@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    other_user = User(
        username="user6_other", email="user6_other@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.add(other_user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user6_protected", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Protected", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    response = await client.put(
        f"/api/v1/projects/{project.id}",
        json={
            "name": "Updated Name",
            "owner_id": other_user.id,
            "is_deleted": True,
            "deleted_at": "2026-02-20T00:00:00Z",
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422

    db_session.refresh(project)
    assert project.name == "Protected"
    assert project.owner_id == user.id
    assert project.is_deleted is False


@pytest.mark.integration
async def test_delete_project_success(client: AsyncClient, db_session):
    """Test deleting a project successfully (soft delete)."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user7", email="user7@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user7", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="To Delete", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Delete project
    response = await client.delete(f"/api/v1/projects/{project.id}", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200

    # Verify project was soft-deleted
    db_session.refresh(project)
    assert project.is_deleted is True
    assert project.deleted_at is not None


@pytest.mark.integration
async def test_patch_project_metadata(client: AsyncClient, db_session):
    """Test partially updating project metadata fields."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user8", email="user8@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user8", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Patch project
    response = await client.patch(
        f"/api/v1/projects/{project.id}",
        json={
            "summary": "A summary for AI context",
            "current_phase": "Outline phase",
            "writing_style": "Descriptive and poetic",
            "notes": "Additional notes for AI"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == "A summary for AI context"
    assert data["current_phase"] == "Outline phase"
    assert data["writing_style"] == "Descriptive and poetic"
    assert data["notes"] == "Additional notes for AI"
    assert data["name"] == "Test Project"


@pytest.mark.integration
async def test_patch_project_metadata_trims_and_supports_clear(client: AsyncClient, db_session):
    """Test patch trims values and preserves empty-string clear."""
    from services.core.auth_service import hash_password

    user = User(
        username="user8_trim",
        email="user8_trim@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login", data={"username": "user8_trim", "password": "password123"}
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Trim Test", owner_id=user.id, notes="Existing notes")
    db_session.add(project)
    db_session.commit()

    response = await client.patch(
        f"/api/v1/projects/{project.id}",
        json={
            "summary": "  Keep this summary  ",
            "notes": "   ",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["summary"] == "Keep this summary"
    assert data["notes"] == ""


@pytest.mark.integration
async def test_patch_project_metadata_rejects_non_ai_memory_fields(client: AsyncClient, db_session):
    """Test patch rejects non-AI-memory fields such as name/description."""
    from services.core.auth_service import hash_password

    user = User(
        username="user8_forbid",
        email="user8_forbid@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login", data={"username": "user8_forbid", "password": "password123"}
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Forbid Test", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    response = await client.patch(
        f"/api/v1/projects/{project.id}",
        json={"name": "Should be rejected"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


@pytest.mark.integration
async def test_patch_project_metadata_rejects_too_long_fields(client: AsyncClient, db_session):
    """Test patch rejects project status field over max length."""
    from services.core.auth_service import hash_password

    user = User(
        username="user8_limit",
        email="user8_limit@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login", data={"username": "user8_limit", "password": "password123"}
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Limit Test", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    too_long_summary = "a" * (PROJECT_STATUS_MAX_LENGTHS["summary"] + 1)
    response = await client.patch(
        f"/api/v1/projects/{project.id}",
        json={"summary": too_long_summary},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize("competing_writer", ["ai_proposal", "manual_patch"])
def test_notes_patch_rejects_stale_baseline_after_another_writer(db_session, competing_writer):
    """A stale manual draft cannot erase a committed AI proposal or another author's edit."""
    from agent.tools.file_ops.project import ProjectOperations
    from api.projects import PatchProjectRequest, patch_project
    from config.project_notes import CONFIRMED_HEADER, merge_project_notes
    from core.error_codes import ErrorCode
    from core.error_handler import APIException

    owner = User(username="notes-race", email="notes-race@example.com", hashed_password="unused")
    db_session.add(owner)
    db_session.commit()
    baseline = f"{CONFIRMED_HEADER}\n旧事实"
    project = Project(
        name="Notes race", owner_id=owner.id, notes=baseline,
        summary="Original summary", current_phase="Original phase", writing_style="Original style",
    )
    db_session.add(project)
    db_session.commit()
    project_id, owner_id = project.id, owner.id
    request = PatchProjectRequest(
        expected_notes=baseline, notes=f"{CONFIRMED_HEADER}\n旧事实\n我的草稿",
        summary="Stale summary", current_phase="Stale phase", writing_style="Stale style",
    )

    with Session(db_session.get_bind()) as competing_session:
        if competing_writer == "ai_proposal":
            ProjectOperations(competing_session, owner_id).update_project_status(
                project_id,
                notes_transform=lambda existing: merge_project_notes(
                    existing, notes=None, proposals="刚到达的 AI 提议",
                    author_quotes=None, author_messages=["继续写"],
                ).text,
            )
        else:
            patch_project(
                project_id,
                PatchProjectRequest(expected_notes=baseline, notes="另一端刚保存的作者约定"),
                current_user=competing_session.get(User, owner_id),
                session=competing_session,
            )
        winner = competing_session.get(Project, project_id)
        winner_notes, winner_updated_at = winner.notes, winner.updated_at

    assert project.notes == baseline  # The request session still holds its original ORM snapshot.
    with pytest.raises(APIException) as exc:
        patch_project(project_id, request, current_user=owner, session=db_session)
    assert exc.value.status_code == 409
    assert exc.value.error_code == ErrorCode.RESOURCE_CONFLICT

    with Session(db_session.get_bind()) as reader:
        persisted = reader.get(Project, project_id)
        assert persisted.notes == winner_notes
        assert persisted.summary == "Original summary"
        assert persisted.current_phase == "Original phase"
        assert persisted.writing_style == "Original style"
        assert persisted.updated_at == winner_updated_at
    assert project.notes == winner_notes  # Compare against the fresh, locked row.
    assert project.summary == "Original summary"  # No dirty partial update remains in this session.


@pytest.mark.integration
@pytest.mark.parametrize(
    ("stored_notes", "expected_notes", "next_notes", "saved_notes"),
    [
        (None, "", "New memory", "New memory"),
        ("", None, "New memory", "New memory"),
        ("Old memory", "Old memory", "", ""),
        ("Old memory", "Old memory", None, None),
        ("  Legacy memory  ", "  Legacy memory  ", "  New memory  ", "New memory"),
    ],
)
async def test_notes_patch_matching_baseline_saves_and_clears(
    client: AsyncClient, db_session, stored_notes, expected_notes, next_notes, saved_notes,
):
    """The condition uses the exact draft origin; null and empty both represent no notes."""
    from services.core.auth_service import hash_password

    owner = User(
        username="notes-matching", email="notes-matching@example.com",
        hashed_password=hash_password("password123"), email_verified=True, is_active=True,
    )
    db_session.add(owner)
    db_session.commit()
    project = Project(name="Matching notes", owner_id=owner.id, notes=stored_notes)
    db_session.add(project)
    db_session.commit()
    login = await client.post(
        "/api/auth/login", data={"username": owner.username, "password": "password123"},
    )
    assert login.status_code == 200

    response = await client.patch(
        f"/api/v1/projects/{project.id}",
        json={"notes": next_notes, "expected_notes": expected_notes, "summary": "  New summary  "},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert response.status_code == 200
    assert response.json()["notes"] == saved_notes
    assert response.json()["summary"] == "New summary"
    assert "expected_notes" not in response.json()
    db_session.refresh(project)
    assert project.notes == saved_notes
    assert not hasattr(project, "expected_notes")

    stale_response = await client.patch(
        f"/api/v1/projects/{project.id}",
        json={"notes": "Stale replacement", "expected_notes": expected_notes, "summary": "Stale summary"},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    assert stale_response.status_code == 409
    assert stale_response.json()["error_code"] == "ERR_RESOURCE_CONFLICT"
    db_session.refresh(project)
    assert project.notes == saved_notes
    assert project.summary == "New summary"


@pytest.mark.parametrize("expected_notes", ["", None, "  Current memory  "])
def test_notes_patch_empty_or_trimmed_baseline_does_not_match_nonempty_notes(db_session, expected_notes):
    """Falsy conditions are still conditions, and comparing them must not trim the stored text."""
    from api.projects import PatchProjectRequest, patch_project
    from core.error_handler import APIException

    owner = User(username="notes-empty", email="notes-empty@example.com", hashed_password="unused")
    db_session.add(owner)
    db_session.commit()
    project = Project(name="Nonempty notes", owner_id=owner.id, notes="Current memory", summary="Before")
    db_session.add(project)
    db_session.commit()

    with pytest.raises(APIException) as exc:
        patch_project(
            project.id, PatchProjectRequest(expected_notes=expected_notes, notes="", summary="After"),
            current_user=owner, session=db_session,
        )
    assert exc.value.status_code == 409
    db_session.refresh(project)
    assert project.notes == "Current memory"
    assert project.summary == "Before"


def test_notes_patch_condition_alone_does_not_block_other_metadata(db_session):
    """A notes baseline has no effect when the request does not write notes."""
    from api.projects import PatchProjectRequest, patch_project

    owner = User(username="notes-only", email="notes-only@example.com", hashed_password="unused")
    db_session.add(owner)
    db_session.commit()
    project = Project(name="Other metadata", owner_id=owner.id, notes="Current memory")
    db_session.add(project)
    db_session.commit()

    result = patch_project(
        project.id, PatchProjectRequest(expected_notes="Stale memory", summary="New summary"),
        current_user=owner, session=db_session,
    )

    assert result.summary == "New summary"
    assert result.notes == "Current memory"
    assert not hasattr(result, "expected_notes")


@pytest.mark.integration
async def test_notes_patch_checks_ownership_before_reporting_conflict(client: AsyncClient, db_session):
    """A foreign project cannot be written or probed through its baseline condition."""
    from services.core.auth_service import hash_password

    owner = User(username="notes-owner", email="notes-owner@example.com", hashed_password="unused")
    other_user = User(
        username="notes-other", email="notes-other@example.com",
        hashed_password=hash_password("password123"), email_verified=True, is_active=True,
    )
    db_session.add_all([owner, other_user])
    db_session.commit()
    project = Project(name="Private notes", owner_id=owner.id, notes="Private memory")
    db_session.add(project)
    db_session.commit()
    login = await client.post(
        "/api/auth/login", data={"username": other_user.username, "password": "password123"},
    )
    assert login.status_code == 200

    response = await client.patch(
        f"/api/v1/projects/{project.id}",
        json={"notes": "Foreign change", "expected_notes": ""},
        headers={"Authorization": f"Bearer {login.json()['access_token']}"},
    )

    assert response.status_code == 403
    db_session.refresh(project)
    assert project.notes == "Private memory"


@pytest.mark.integration
async def test_get_projects_without_auth(client: AsyncClient):
    """Test getting projects without authentication returns 401."""
    response = await client.get("/api/v1/projects")
    assert response.status_code == 401


@pytest.mark.integration
async def test_create_project_without_auth(client: AsyncClient):
    """Test creating project without authentication returns 401."""
    response = await client.post("/api/v1/projects", json={"name": "Unauthorized Project"})
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_project_templates_has_required_types(client: AsyncClient):
    """Test project templates endpoint returns all supported types."""
    response = await client.get("/api/v1/project-templates")
    assert response.status_code == 200
    data = response.json()
    assert set(data.keys()) == {"novel", "short", "screenplay"}


def test_project_update_omission_keeps_defaults_out_of_patch():
    """Explicit-null validation must not validate the optional omitted defaults."""
    from api.projects import UpdateProjectRequest

    request = UpdateProjectRequest()
    assert request.name is None
    assert request.project_type is None
    assert request.model_dump(exclude_unset=True) == {}
    assert UpdateProjectRequest(description=None).model_dump(exclude_unset=True) == {"description": None}
