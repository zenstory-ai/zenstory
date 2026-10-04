"""
Tests for Files API - Basic CRUD operations.

Tests file CRUD operations:
- GET /api/v1/projects/{id}/files - List files
- POST /api/v1/projects/{id}/files - Create file
- GET /api/v1/files/{id} - Get file details
- PUT /api/v1/files/{id} - Update file
- DELETE /api/v1/files/{id} - Delete file
"""

import pytest
from httpx import AsyncClient
from sqlmodel import select

from api.files import MATERIAL_MAX_BYTES, MATERIAL_MAX_CHARS
from core.error_codes import ErrorCode
from models import File, FileVersion, Project, User

# ==================== Move and Tree Tests ====================

@pytest.mark.integration
async def test_move_file_to_new_parent(client: AsyncClient, db_session):
    """Test moving a file to a new parent folder."""
    from services.core.auth_service import hash_password
    user = User(
        username="user21", email="user21@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user21", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and files
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder1 = File(project_id=project.id, title="Folder 1", file_type="folder")
    folder2 = File(project_id=project.id, title="Folder 2", file_type="folder")
    file1 = File(project_id=project.id, title="File 1", file_type="draft", content="Content", parent_id=folder1.id)
    db_session.add_all([folder1, folder2, file1])
    db_session.commit()

    # Move file1 from folder1 to folder2
    response = await client.put(
        f"/api/v1/files/{file1.id}",
        json={"parent_id": folder2.id},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["parent_id"] == folder2.id


@pytest.mark.integration
async def test_move_file_to_root(client: AsyncClient, db_session):
    """Test moving a file from a folder to root level."""
    from services.core.auth_service import hash_password
    user = User(
        username="user22", email="user22@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user22", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and files
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="Folder", file_type="folder")
    file1 = File(project_id=project.id, title="File 1", file_type="draft", content="Content", parent_id=folder.id)
    db_session.add_all([folder, file1])
    db_session.commit()

    # Move file1 to root
    response = await client.put(
        f"/api/v1/files/{file1.id}",
        json={"parent_id": None},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["parent_id"] is None


@pytest.mark.integration
async def test_move_file_invalid_parent(client: AsyncClient, db_session):
    """Test moving a file to an invalid parent returns 400."""
    from services.core.auth_service import hash_password
    user = User(
        username="user23", email="user23@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user23", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and file
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file1 = File(project_id=project.id, title="File 1", file_type="draft", content="Content")
    db_session.add(file1)
    db_session.commit()

    # Try to move file1 to non-existent parent
    response = await client.put(
        f"/api/v1/files/{file1.id}",
        json={"parent_id": "00000000-0000-0000-0000-000000000000"},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_move_file_to_deleted_parent(client: AsyncClient, db_session):
    """Test moving a file to a soft-deleted parent returns 400."""
    from services.core.auth_service import hash_password
    user = User(
        username="user24", email="user24@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user24", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and files
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="Folder", file_type="folder")
    file1 = File(project_id=project.id, title="File 1", file_type="draft", content="Content")
    db_session.add_all([folder, file1])
    db_session.commit()

    # Soft delete the folder
    folder.is_deleted = True
    folder.deleted_at = folder.updated_at
    db_session.commit()

    # Try to move file1 to deleted folder
    response = await client.put(
        f"/api/v1/files/{file1.id}",
        json={"parent_id": folder.id},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_move_file_endpoint_prevents_folder_cycle(client: AsyncClient, db_session):
    """Test POST /move prevents moving folder into its own descendant."""
    from services.core.auth_service import hash_password
    user = User(
        username="user24b", email="user24b@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user24b", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    root_folder = File(project_id=project.id, title="Root", file_type="folder")
    child_folder = File(project_id=project.id, title="Child", file_type="folder", parent_id=root_folder.id)
    db_session.add_all([root_folder, child_folder])
    db_session.commit()

    response = await client.post(
        f"/api/v1/files/{root_folder.id}/move",
        json={"target_parent_id": child_folder.id},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_get_file_tree_empty(client: AsyncClient, db_session):
    """Test getting file tree when project has no files."""
    from services.core.auth_service import hash_password
    user = User(
        username="user25", email="user25@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user25", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Get file tree
    response = await client.get(
        f"/api/v1/projects/{project.id}/file-tree",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "tree" in data
    assert isinstance(data["tree"], list)
    assert len(data["tree"]) == 0


@pytest.mark.integration
async def test_get_file_tree_flat_structure(client: AsyncClient, db_session):
    """Test getting file tree with flat structure (no nesting)."""
    from services.core.auth_service import hash_password
    user = User(
        username="user26", email="user26@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user26", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and files
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file1 = File(project_id=project.id, title="File 1", file_type="outline", content="Content 1")
    file2 = File(project_id=project.id, title="File 2", file_type="draft", content="Content 2")
    db_session.add_all([file1, file2])
    db_session.commit()

    # Get file tree
    response = await client.get(
        f"/api/v1/projects/{project.id}/file-tree",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "tree" in data
    assert len(data["tree"]) == 2
    # Verify no children (flat structure)
    assert all(len(item.get("children", [])) == 0 for item in data["tree"])


@pytest.mark.integration
async def test_get_file_tree_content_flag(client: AsyncClient, db_session):
    """Test file-tree omits content by default and can include it on demand."""
    from services.core.auth_service import hash_password

    user = User(
        username="user26b", email="user26b@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user26b", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file1 = File(project_id=project.id, title="File 1", file_type="outline", content="heavy content")
    db_session.add(file1)
    db_session.commit()

    response_default = await client.get(
        f"/api/v1/projects/{project.id}/file-tree",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response_default.status_code == 200
    default_tree = response_default.json()["tree"]
    assert default_tree[0]["content"] == ""

    response_with_content = await client.get(
        f"/api/v1/projects/{project.id}/file-tree?include_content=true",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response_with_content.status_code == 200
    tree_with_content = response_with_content.json()["tree"]
    assert tree_with_content[0]["content"] == "heavy content"


@pytest.mark.integration
async def test_get_file_tree_nested_structure(client: AsyncClient, db_session):
    """Test getting file tree with nested folders."""
    from services.core.auth_service import hash_password
    user = User(
        username="user27", email="user27@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user27", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and files
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="Chapter 1", file_type="folder", order=1)
    file1 = File(project_id=project.id, title="Scene 1", file_type="outline", content="Scene content", parent_id=folder.id)
    file2 = File(project_id=project.id, title="Scene 2", file_type="draft", content="Draft content", parent_id=folder.id)
    root_file = File(project_id=project.id, title="Root File", file_type="character", content="Character info")
    db_session.add_all([folder, file1, file2, root_file])
    db_session.commit()

    # Get file tree
    response = await client.get(
        f"/api/v1/projects/{project.id}/file-tree",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "tree" in data
    # Should have folder and root_file at root level
    assert len(data["tree"]) == 2

    # Find the folder in tree
    folder_item = next((item for item in data["tree"] if item["title"] == "Chapter 1"), None)
    assert folder_item is not None
    assert len(folder_item["children"]) == 2
    assert any(child["title"] == "Scene 1" for child in folder_item["children"])
    assert any(child["title"] == "Scene 2" for child in folder_item["children"])

    # Verify root_file has no children
    root_item = next((item for item in data["tree"] if item["title"] == "Root File"), None)
    assert root_item is not None
    assert len(root_item.get("children", [])) == 0


@pytest.mark.integration
async def test_get_file_tree_multiple_levels(client: AsyncClient, db_session):
    """Test getting file tree with multiple levels of nesting."""
    from services.core.auth_service import hash_password
    user = User(
        username="user28", email="user28@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user28", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and files
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Create nested structure: Folder > SubFolder > File
    folder = File(project_id=project.id, title="Part 1", file_type="folder", order=1)
    subfolder = File(project_id=project.id, title="Chapter 1", file_type="folder", order=1, parent_id=folder.id)
    file1 = File(project_id=project.id, title="Scene 1", file_type="outline", content="Content", parent_id=subfolder.id)
    db_session.add_all([folder, subfolder, file1])
    db_session.commit()

    # Get file tree
    response = await client.get(
        f"/api/v1/projects/{project.id}/file-tree",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "tree" in data
    assert len(data["tree"]) == 1

    # Verify nesting
    folder_item = data["tree"][0]
    assert folder_item["title"] == "Part 1"
    assert len(folder_item["children"]) == 1

    subfolder_item = folder_item["children"][0]
    assert subfolder_item["title"] == "Chapter 1"
    assert len(subfolder_item["children"]) == 1

    file_item = subfolder_item["children"][0]
    assert file_item["title"] == "Scene 1"
    assert len(file_item.get("children", [])) == 0


@pytest.mark.integration
async def test_get_file_tree_sorts_episode_numbers_when_order_missing(client: AsyncClient, db_session):
    """File tree should sort titles like 第1集/第2集/第10集 by numeric episode number when order ties."""
    from services.core.auth_service import hash_password

    user = User(
        username="user28b",
        email="user28b@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user28b", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="分集大纲", file_type="folder", order=0)
    file1 = File(project_id=project.id, title="第2集：测试", file_type="outline", parent_id=folder.id, order=0)
    file2 = File(project_id=project.id, title="第10集：测试", file_type="outline", parent_id=folder.id, order=0)
    file3 = File(project_id=project.id, title="第1集：测试", file_type="outline", parent_id=folder.id, order=0)
    db_session.add_all([folder, file1, file2, file3])
    db_session.commit()

    response = await client.get(
        f"/api/v1/projects/{project.id}/file-tree",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    tree = response.json()["tree"]

    folder_item = next((item for item in tree if item["id"] == folder.id), None)
    assert folder_item is not None

    titles = [child["title"] for child in folder_item["children"]]
    assert titles == ["第1集：测试", "第2集：测试", "第10集：测试"]


@pytest.mark.integration
async def test_get_file_tree_treats_zero_order_as_missing_for_episode_titles(client: AsyncClient, db_session):
    """
    Legacy agent-created files often have order=0.

    If some siblings already have a non-zero order, we should still sort the
    order=0 items by their episode number instead of pushing them ahead of
    properly ordered siblings.
    """
    from services.core.auth_service import hash_password

    user = User(
        username="user28c",
        email="user28c@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user28c", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="分集大纲", file_type="folder", order=0)
    file1 = File(project_id=project.id, title="第2集：测试", file_type="outline", parent_id=folder.id, order=0)
    file2 = File(project_id=project.id, title="第10集：测试", file_type="outline", parent_id=folder.id, order=0)
    file3 = File(project_id=project.id, title="第8集：测试", file_type="outline", parent_id=folder.id, order=8)
    file4 = File(project_id=project.id, title="第1集：测试", file_type="outline", parent_id=folder.id, order=0)
    db_session.add_all([folder, file1, file2, file3, file4])
    db_session.commit()

    response = await client.get(
        f"/api/v1/projects/{project.id}/file-tree",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    tree = response.json()["tree"]

    folder_item = next((item for item in tree if item["id"] == folder.id), None)
    assert folder_item is not None

    titles = [child["title"] for child in folder_item["children"]]
    assert titles == ["第1集：测试", "第2集：测试", "第8集：测试", "第10集：测试"]


@pytest.mark.integration
async def test_get_file_tree_normalizes_trailing_zero_order_typos(client: AsyncClient, db_session):
    """File tree should recover when bad data stores 第58章 with order=580."""
    from services.core.auth_service import hash_password

    user = User(
        username="user28d",
        email="user28d@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user28d", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="正文", file_type="folder", order=0)
    file1 = File(project_id=project.id, title="第57章 魔君", file_type="draft", parent_id=folder.id, order=57)
    file2 = File(project_id=project.id, title="第58章 真相", file_type="draft", parent_id=folder.id, order=580)
    file3 = File(project_id=project.id, title="第59章 潜入", file_type="draft", parent_id=folder.id, order=59)
    db_session.add_all([folder, file1, file2, file3])
    db_session.commit()

    response = await client.get(
        f"/api/v1/projects/{project.id}/file-tree",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    tree = response.json()["tree"]

    folder_item = next((item for item in tree if item["id"] == folder.id), None)
    assert folder_item is not None

    titles = [child["title"] for child in folder_item["children"]]
    assert titles == ["第57章 魔君", "第58章 真相", "第59章 潜入"]


@pytest.mark.integration
async def test_get_file_tree_prefers_title_sequence_for_chapter_like_writing_files(client: AsyncClient, db_session):
    """Chapter-like draft/outline/script files should always follow title sequence."""
    from services.core.auth_service import hash_password

    user = User(
        username="user28e",
        email="user28e@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user28e", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="正文", file_type="folder", order=0)
    file1 = File(project_id=project.id, title="第57章 魔君", file_type="draft", parent_id=folder.id, order=57)
    file2 = File(project_id=project.id, title="第58章 真相", file_type="draft", parent_id=folder.id, order=1)
    file3 = File(project_id=project.id, title="第59章 潜入", file_type="draft", parent_id=folder.id, order=59)
    db_session.add_all([folder, file1, file2, file3])
    db_session.commit()

    response = await client.get(
        f"/api/v1/projects/{project.id}/file-tree",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    tree = response.json()["tree"]

    folder_item = next((item for item in tree if item["id"] == folder.id), None)
    assert folder_item is not None

    titles = [child["title"] for child in folder_item["children"]]
    assert titles == ["第57章 魔君", "第58章 真相", "第59章 潜入"]


@pytest.mark.integration
async def test_delete_folder_with_recursive_flag(client: AsyncClient, db_session):
    """Test deleting a folder with recursive flag deletes all children."""
    from services.core.auth_service import hash_password
    user = User(
        username="user29", email="user29@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user29", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and files
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="Folder", file_type="folder")
    file1 = File(project_id=project.id, title="File 1", file_type="draft", content="Content 1", parent_id=folder.id)
    file2 = File(project_id=project.id, title="File 2", file_type="outline", content="Content 2", parent_id=folder.id)
    db_session.add_all([folder, file1, file2])
    db_session.commit()

    # Delete folder with recursive flag
    response = await client.delete(
        f"/api/v1/files/{folder.id}?recursive=true",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200

    # Verify all files are soft-deleted
    db_session.refresh(folder)
    db_session.refresh(file1)
    db_session.refresh(file2)
    assert folder.is_deleted is True
    assert file1.is_deleted is True
    assert file2.is_deleted is True


@pytest.mark.integration
async def test_delete_folder_without_recursive_flag(client: AsyncClient, db_session):
    """Test deleting a folder without recursive flag only deletes the folder."""
    from services.core.auth_service import hash_password
    user = User(
        username="user30", email="user30@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user30", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and files
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="Folder", file_type="folder")
    file1 = File(project_id=project.id, title="File 1", file_type="draft", content="Content 1", parent_id=folder.id)
    file2 = File(project_id=project.id, title="File 2", file_type="outline", content="Content 2", parent_id=folder.id)
    db_session.add_all([folder, file1, file2])
    db_session.commit()

    # Delete folder without recursive flag (default)
    response = await client.delete(
        f"/api/v1/files/{folder.id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200

    # Verify only folder is deleted, children remain
    db_session.refresh(folder)
    db_session.refresh(file1)
    db_session.refresh(file2)
    assert folder.is_deleted is True
    assert file1.is_deleted is False  # Child files should remain
    assert file2.is_deleted is False


@pytest.mark.integration
async def test_delete_folder_recursive_multiple_levels(client: AsyncClient, db_session):
    """Test recursive deletion through multiple levels of nesting."""
    from services.core.auth_service import hash_password
    user = User(
        username="user31", email="user31@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user31", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and files
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Create nested structure: Folder > SubFolder > File
    folder = File(project_id=project.id, title="Folder", file_type="folder")
    subfolder = File(project_id=project.id, title="SubFolder", file_type="folder", parent_id=folder.id)
    file1 = File(project_id=project.id, title="File 1", file_type="draft", content="Content", parent_id=subfolder.id)
    file2 = File(project_id=project.id, title="File 2", file_type="outline", content="Content", parent_id=folder.id)
    db_session.add_all([folder, subfolder, file1, file2])
    db_session.commit()

    # Delete folder with recursive flag
    response = await client.delete(
        f"/api/v1/files/{folder.id}?recursive=true",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200

    # Verify all files are soft-deleted (all levels)
    db_session.refresh(folder)
    db_session.refresh(subfolder)
    db_session.refresh(file1)
    db_session.refresh(file2)
    assert folder.is_deleted is True
    assert subfolder.is_deleted is True
    assert file1.is_deleted is True
    assert file2.is_deleted is True


@pytest.mark.integration
async def test_get_file_tree_excludes_deleted_files(client: AsyncClient, db_session):
    """Test that file tree excludes soft-deleted files."""
    from services.core.auth_service import hash_password
    user = User(
        username="user32", email="user32@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user32", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and files
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="Folder", file_type="folder")
    file1 = File(project_id=project.id, title="File 1", file_type="draft", content="Content 1")
    file2 = File(project_id=project.id, title="File 2", file_type="draft", content="Content 2", parent_id=folder.id)
    db_session.add_all([folder, file1, file2])
    db_session.commit()

    # Soft delete file1
    file1.is_deleted = True
    file1.deleted_at = file1.updated_at
    db_session.commit()

    # Get file tree
    response = await client.get(
        f"/api/v1/projects/{project.id}/file-tree",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "tree" in data

    # Should only have folder (file1 is deleted)
    assert len(data["tree"]) == 1
    assert data["tree"][0]["title"] == "Folder"
    assert len(data["tree"][0]["children"]) == 1  # Only file2


@pytest.mark.integration
async def test_get_file_tree_unauthorized_project(client: AsyncClient, db_session):
    """Test getting file tree from another user's project returns 403."""
    from services.core.auth_service import hash_password
    user1 = User(
        username="user33", email="user33@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    user2 = User(
        username="user34", email="user34@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add_all([user1, user2])
    db_session.commit()

    # Login as user1
    login_response = await client.post("/api/auth/login", data={"username": "user33", "password": "password123"})
    assert login_response.status_code == 200
    token1 = login_response.json()["access_token"]

    # Create project for user2
    project2 = Project(name="User2 Project", owner_id=user2.id)
    db_session.add(project2)
    db_session.commit()

    # Try to get file tree as user1
    response = await client.get(
        f"/api/v1/projects/{project2.id}/file-tree",
        headers={"Authorization": f"Bearer {token1}"}
    )
    assert response.status_code == 403


@pytest.mark.integration
async def test_move_file_unauthorized_parent(client: AsyncClient, db_session):
    """Test moving a file to a parent in another project returns 400."""
    from services.core.auth_service import hash_password
    user1 = User(
        username="user35", email="user35@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    user2 = User(
        username="user36", email="user36@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add_all([user1, user2])
    db_session.commit()

    # Login as user1
    login_response = await client.post("/api/auth/login", data={"username": "user35", "password": "password123"})
    assert login_response.status_code == 200
    token1 = login_response.json()["access_token"]

    # Create projects and files
    project1 = Project(name="Project 1", owner_id=user1.id)
    project2 = Project(name="Project 2", owner_id=user2.id)
    db_session.add_all([project1, project2])
    db_session.commit()

    file1 = File(project_id=project1.id, title="File 1", file_type="draft", content="Content")
    folder2 = File(project_id=project2.id, title="Folder 2", file_type="folder")
    db_session.add_all([file1, folder2])
    db_session.commit()

    # Try to move file1 to folder2 (different project)
    response = await client.put(
        f"/api/v1/files/{file1.id}",
        json={"parent_id": folder2.id},
        headers={"Authorization": f"Bearer {token1}"}
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_upload_material_success(client: AsyncClient, db_session):
    """Test uploading a txt material creates a snippet under material folder."""
    from services.core.auth_service import hash_password

    user = User(
        username="upload_user1", email="upload_user1@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "upload_user1", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Upload Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    material_folder = File(
        id=f"{project.id}-material-folder",
        project_id=project.id,
        title="素材",
        file_type="folder",
        order=0,
    )
    db_session.add(material_folder)
    db_session.commit()

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload",
        files={"file": ("test_material.txt", b"line1\nline2", "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["file_type"] == "snippet"
    assert data["parent_id"] == material_folder.id
    assert data["content"] == "line1\nline2"
    assert data["title"] == "test_material"


@pytest.mark.integration
async def test_upload_material_auto_splits_large_content(client: AsyncClient, db_session):
    """Test long material uploads are auto-split into multiple snippets."""
    from services.core.auth_service import hash_password

    user = User(
        username="upload_user_split", email="upload_user_split@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "upload_user_split", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Upload Split Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    material_folder = File(
        id=f"{project.id}-material-folder",
        project_id=project.id,
        title="素材",
        file_type="folder",
        order=0,
    )
    db_session.add(material_folder)
    db_session.commit()

    long_content = (
        ("第1章 起始\n" + ("甲" * 13000) + "\n\n")
        + ("第2章 发展\n" + ("乙" * 13000) + "\n\n")
        + ("第3章 收束\n" + ("丙" * 13000))
    )

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload",
        files={"file": ("long_material.txt", long_content.encode("utf-8"), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["file_type"] == "snippet"
    assert payload["parent_id"] == material_folder.id

    created_snippets = db_session.exec(
        select(File).where(
            File.project_id == project.id,
            File.parent_id == material_folder.id,
            File.file_type == "snippet",
            File.is_deleted.is_(False),
        )
    ).all()

    assert len(created_snippets) >= 2
    assert all(len(item.content or "") <= 20_000 for item in created_snippets)
    assert any("split" in (item.file_metadata or "") for item in created_snippets)


@pytest.mark.integration
async def test_upload_material_invalid_extension(client: AsyncClient, db_session):
    """Test uploading non-txt file is rejected."""
    from services.core.auth_service import hash_password

    user = User(
        username="upload_user2", email="upload_user2@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "upload_user2", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Upload Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    material_folder = File(
        id=f"{project.id}-material-folder",
        project_id=project.id,
        title="素材",
        file_type="folder",
        order=0,
    )
    db_session.add(material_folder)
    db_session.commit()

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload",
        files={"file": ("test_material.md", b"# markdown", "text/markdown")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_upload_material_over_char_limit(client: AsyncClient, db_session):
    """Test uploading txt over char limit is rejected."""
    from services.core.auth_service import hash_password

    user = User(
        username="upload_user3", email="upload_user3@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "upload_user3", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Upload Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    material_folder = File(
        id=f"{project.id}-material-folder",
        project_id=project.id,
        title="素材",
        file_type="folder",
        order=0,
    )
    db_session.add(material_folder)
    db_session.commit()

    oversized_text = "a" * (MATERIAL_MAX_CHARS + 1)
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload",
        files={"file": ("test_material.txt", oversized_text.encode("utf-8"), "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"] == ErrorCode.FILE_CONTENT_TOO_LONG


@pytest.mark.integration
async def test_upload_material_over_byte_limit(client: AsyncClient, db_session):
    """Test uploading payload over hard byte cap is rejected early."""
    from services.core.auth_service import hash_password

    user = User(
        username="upload_user4", email="upload_user4@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "upload_user4", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Upload Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    material_folder = File(
        id=f"{project.id}-material-folder",
        project_id=project.id,
        title="素材",
        file_type="folder",
        order=0,
    )
    db_session.add(material_folder)
    db_session.commit()

    oversized_bytes = b"x" * (MATERIAL_MAX_BYTES + 1)
    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload",
        files={"file": ("test_material.txt", oversized_bytes, "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    payload = response.json()
    assert payload["detail"] == ErrorCode.FILE_TOO_LARGE


@pytest.mark.integration
async def test_upload_material_auto_creates_missing_material_folder(client: AsyncClient, db_session):
    """Test upload works for projects without pre-created material folder."""
    from services.core.auth_service import hash_password

    user = User(
        username="upload_user5", email="upload_user5@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "upload_user5", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Upload Project", owner_id=user.id, project_type="short")
    db_session.add(project)
    db_session.commit()

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/upload",
        files={"file": ("auto_material.txt", b"auto folder content", "text/plain")},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["file_type"] == "snippet"
    assert data["parent_id"] == f"{project.id}-material-folder"

    material_folder = db_session.get(File, f"{project.id}-material-folder")
    assert material_folder is not None
    assert material_folder.file_type == "folder"
    assert material_folder.project_id == project.id
    assert material_folder.is_deleted is False


@pytest.mark.integration
async def test_get_files_empty_list(client: AsyncClient, db_session):
    """Test getting files when project has no files."""
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

    # Create project
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Get files
    response = await client.get(
        f"/api/v1/projects/{project.id}/files",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 0


@pytest.mark.integration
async def test_get_files_with_files(client: AsyncClient, db_session):
    """Test getting files when project has multiple files."""
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

    # Create project
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Create files
    file1 = File(project_id=project.id, title="Outline", file_type="outline", content="Chapter 1 outline")
    file2 = File(project_id=project.id, title="Draft", file_type="draft", content="Chapter 1 draft")
    db_session.add(file1)
    db_session.add(file2)
    db_session.commit()

    # Get files
    response = await client.get(
        f"/api/v1/projects/{project.id}/files",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    assert any(f["title"] == "Outline" for f in data)
    assert any(f["title"] == "Draft" for f in data)


@pytest.mark.integration
async def test_get_files_filter_by_type(client: AsyncClient, db_session):
    """Test filtering files by file_type."""
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
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Create files with different types
    file1 = File(project_id=project.id, title="Outline", file_type="outline", content="Chapter 1 outline")
    file2 = File(project_id=project.id, title="Draft", file_type="draft", content="Chapter 1 draft")
    file3 = File(project_id=project.id, title="Character", file_type="character", content="Hero profile")
    db_session.add(file1)
    db_session.add(file2)
    db_session.add(file3)
    db_session.commit()

    # Filter by file_type
    response = await client.get(
        f"/api/v1/projects/{project.id}/files?file_type=outline",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["title"] == "Outline"
    assert data[0]["file_type"] == "outline"


@pytest.mark.integration
async def test_create_file_success(client: AsyncClient, db_session):
    """Test creating a new file successfully."""
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

    # Create file
    response = await client.post(
        f"/api/v1/projects/{project.id}/files",
        json={
            "title": "Chapter 1",
            "file_type": "outline",
            "content": "This is the outline for chapter 1",
            "order": 1
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["title"] == "Chapter 1"
    assert data["file_type"] == "outline"
    assert data["content"] == "This is the outline for chapter 1"
    assert data["project_id"] == project.id


@pytest.mark.integration
async def test_file_endpoints_reject_embedded_nul_as_request_validation(
    client: AsyncClient,
    db_session,
):
    """PostgreSQL-incompatible NUL text must fail at the request boundary."""
    from services.core.auth_service import hash_password

    user = User(
        username="file_nul_user",
        email="file-nul-user@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": user.username, "password": "password123"},
    )
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project = Project(name="NUL validation project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    file = File(
        project_id=project.id,
        title="Original title",
        file_type="draft",
        content="Original content",
    )
    db_session.add(file)
    db_session.commit()

    create_url = f"/api/v1/projects/{project.id}/files"
    update_url = f"/api/v1/files/{file.id}"
    requests = [
        ("post", create_url, {"title": "bad\x00title"}),
        ("post", create_url, {"title": "Valid title", "content": "bad\x00content"}),
        ("put", update_url, {"title": "bad\x00title"}),
        ("put", update_url, {"content": "bad\x00content"}),
    ]

    for method, url, payload in requests:
        response = await client.request(method, url, json=payload, headers=headers)
        assert response.status_code == 422

    db_session.refresh(file)
    assert file.title == "Original title"
    assert file.content == "Original content"


@pytest.mark.integration
async def test_create_file_with_metadata(client: AsyncClient, db_session):
    """Test creating a file with metadata."""
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

    # Create project
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Create file with metadata
    metadata = {"chapter_number": 1, "status": "in_progress", "word_count_target": 3000}
    response = await client.post(
        f"/api/v1/projects/{project.id}/files",
        json={
            "title": "Chapter 1",
            "file_type": "outline",
            "content": "Outline content",
            "metadata": metadata
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["title"] == "Chapter 1"
    # metadata is stored as JSON string in file_metadata field
    assert data["file_metadata"] is not None


@pytest.mark.integration
async def test_create_file_infers_order_from_episode_title_when_omitted(client: AsyncClient, db_session):
    """When order is omitted, backend should infer order from titles like 第7集:xxx."""
    from services.core.auth_service import hash_password

    user = User(
        username="user5b",
        email="user5b@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user5b", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="分集大纲", file_type="folder", order=0)
    db_session.add(folder)
    db_session.commit()

    response = await client.post(
        f"/api/v1/projects/{project.id}/files",
        json={
            "title": "第7集：测试",
            "file_type": "outline",
            "content": "Outline content",
            "parent_id": folder.id,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["order"] == 7


@pytest.mark.integration
async def test_create_file_normalizes_suspicious_explicit_order_from_title(client: AsyncClient, db_session):
    """Explicit chapter order like 580 should normalize back to 58 on create."""
    from services.core.auth_service import hash_password

    user = User(
        username="user5c",
        email="user5c@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user5c", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="正文", file_type="folder", order=0)
    db_session.add(folder)
    db_session.commit()

    response = await client.post(
        f"/api/v1/projects/{project.id}/files",
        json={
            "title": "第58章 真相",
            "file_type": "draft",
            "content": "Draft content",
            "parent_id": folder.id,
            "order": 580,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "第58章 真相"
    assert data["order"] == 58


@pytest.mark.integration
async def test_create_file_prefers_title_sequence_over_explicit_order_for_chapter_files(client: AsyncClient, db_session):
    """Chapter-like writing files should persist title-derived order even when caller disagrees."""
    from services.core.auth_service import hash_password

    user = User(
        username="user5d",
        email="user5d@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user5d", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="正文", file_type="folder", order=0)
    db_session.add(folder)
    db_session.commit()

    response = await client.post(
        f"/api/v1/projects/{project.id}/files",
        json={
            "title": "第58章 真相",
            "file_type": "draft",
            "content": "Draft content",
            "parent_id": folder.id,
            "order": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "第58章 真相"
    assert data["order"] == 58


@pytest.mark.integration
async def test_create_file_with_parent(client: AsyncClient, db_session):
    """Test creating a file with a parent (folder structure)."""
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
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Create parent folder
    parent = File(project_id=project.id, title="Chapter 1", file_type="folder")
    db_session.add(parent)
    db_session.commit()

    # Create child file
    response = await client.post(
        f"/api/v1/projects/{project.id}/files",
        json={
            "title": "Scene 1",
            "file_type": "outline",
            "content": "Scene 1 content",
            "parent_id": parent.id
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["parent_id"] == parent.id


@pytest.mark.integration
async def test_create_file_invalid_parent(client: AsyncClient, db_session):
    """Test creating a file with invalid parent_id returns 400."""
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
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Create file with invalid parent_id
    response = await client.post(
        f"/api/v1/projects/{project.id}/files",
        json={
            "title": "Scene 1",
            "file_type": "outline",
            "content": "Scene 1 content",
            "parent_id": "00000000-0000-0000-0000-000000000000"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_create_file_rejects_non_folder_parent(client: AsyncClient, db_session):
    """Test creating a file under a non-folder parent returns 400."""
    from services.core.auth_service import hash_password
    user = User(
        username="user7b", email="user7b@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user7b", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    non_folder_parent = File(project_id=project.id, title="Draft Parent", file_type="draft", content="text")
    db_session.add(non_folder_parent)
    db_session.commit()

    response = await client.post(
        f"/api/v1/projects/{project.id}/files",
        json={
            "title": "Scene 2",
            "file_type": "outline",
            "content": "Scene 2 content",
            "parent_id": non_folder_parent.id
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_get_file_success(client: AsyncClient, db_session):
    """Test getting a specific file successfully."""
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

    # Create project and file
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file = File(project_id=project.id, title="Test File", file_type="draft", content="Test content")
    db_session.add(file)
    db_session.commit()

    # Get file
    response = await client.get(
        f"/api/v1/files/{file.id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == file.id
    assert data["title"] == "Test File"
    assert data["content"] == "Test content"


@pytest.mark.integration
async def test_get_file_not_found(client: AsyncClient, db_session):
    """Test getting a nonexistent file returns 404."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user9", email="user9@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user9", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Try to get nonexistent file
    response = await client.get(
        "/api/v1/files/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_update_file_success(client: AsyncClient, db_session):
    """Test updating a file successfully."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user10", email="user10@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user10", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and file
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file = File(project_id=project.id, title="Old Title", file_type="draft", content="Old content")
    db_session.add(file)
    db_session.commit()

    # Update file
    response = await client.put(
        f"/api/v1/files/{file.id}",
        json={
            "title": "New Title",
            "content": "New content"
        },
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "New Title"
    assert data["content"] == "New content"


@pytest.mark.integration
async def test_update_file_prefers_title_sequence_over_explicit_order_for_chapter_files(client: AsyncClient, db_session):
    """Updating a chapter-like writing file should keep order aligned with title sequence."""
    from services.core.auth_service import hash_password

    user = User(
        username="user10c",
        email="user10c@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user10c", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file = File(project_id=project.id, title="第57章 魔君", file_type="draft", content="Old content", order=57)
    db_session.add(file)
    db_session.commit()

    response = await client.put(
        f"/api/v1/files/{file.id}",
        json={
            "title": "第58章 真相",
            "order": 1,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "第58章 真相"
    assert data["order"] == 58


@pytest.mark.integration
async def test_reorder_files_keeps_chapter_like_writing_files_aligned_to_title_sequence(client: AsyncClient, db_session):
    """Reorder endpoint should not persist custom order for chapter-like writing files."""
    from services.core.auth_service import hash_password

    user = User(
        username="user10d",
        email="user10d@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user10d", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="正文", file_type="folder", order=0)
    file57 = File(project_id=project.id, title="第57章 魔君", file_type="draft", parent_id=folder.id, order=57)
    file58 = File(project_id=project.id, title="第58章 真相", file_type="draft", parent_id=folder.id, order=58)
    file59 = File(project_id=project.id, title="第59章 潜入", file_type="draft", parent_id=folder.id, order=59)
    db_session.add_all([folder, file57, file58, file59])
    db_session.commit()

    response = await client.post(
        f"/api/v1/projects/{project.id}/files/reorder",
        json={"ordered_ids": [file59.id, file58.id, file57.id]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    db_session.refresh(file57)
    db_session.refresh(file58)
    db_session.refresh(file59)
    assert (file57.order, file58.order, file59.order) == (57, 58, 59)


@pytest.mark.integration
async def test_update_file_creates_version_with_default_intent(client: AsyncClient, db_session):
    """Test content updates create a version with default change intent."""
    from services.core.auth_service import hash_password

    user = User(
        username="user10b", email="user10b@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user10b", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file = File(project_id=project.id, title="File", file_type="draft", content="Old content")
    db_session.add(file)
    db_session.commit()

    response = await client.put(
        f"/api/v1/files/{file.id}",
        json={"content": "New content"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    versions = db_session.exec(
        select(FileVersion).where(FileVersion.file_id == file.id)
    ).all()
    assert len(versions) == 1
    assert versions[0].change_type == "edit"
    assert versions[0].change_source == "user"
    assert versions[0].change_summary == "File updated"


@pytest.mark.integration
async def test_update_file_respects_version_intent_fields(client: AsyncClient, db_session):
    """Custom change intent is preserved without trusting the client as version author."""
    from services.core.auth_service import hash_password

    user = User(
        username="user10c", email="user10c@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user10c", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file = File(project_id=project.id, title="File", file_type="draft", content="Old content")
    db_session.add(file)
    db_session.commit()

    response = await client.put(
        f"/api/v1/files/{file.id}",
        json={
            "content": "New content",
            "change_type": "auto_save",
            "change_source": "ai",
            "change_summary": "Large document auto-save",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    version = db_session.exec(
        select(FileVersion)
        .where(FileVersion.file_id == file.id)
        .order_by(FileVersion.version_number.desc())
    ).first()
    assert version is not None
    assert version.change_type == "auto_save"
    assert version.change_source == "user"
    assert version.change_summary == "Large document auto-save"


@pytest.mark.integration
async def test_update_file_respects_ai_review_version_intent(client: AsyncClient, db_session):
    """AI review saves preserve ai_edit intent but remain authenticated user writes."""
    from services.core.auth_service import hash_password

    user = User(
        username="user10c_ai", email="user10c_ai@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user10c_ai", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file = File(project_id=project.id, title="File", file_type="draft", content="Old content")
    db_session.add(file)
    db_session.commit()

    response = await client.put(
        f"/api/v1/files/{file.id}",
        json={
            "content": "AI reviewed content",
            "change_type": "ai_edit",
            "change_source": "ai",
            "change_summary": "AI edit (reviewed)",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    version = db_session.exec(
        select(FileVersion)
        .where(FileVersion.file_id == file.id)
        .order_by(FileVersion.version_number.desc())
    ).first()
    assert version is not None
    assert version.change_type == "ai_edit"
    assert version.change_source == "user"
    assert version.change_summary == "AI edit (reviewed)"


@pytest.mark.integration
async def test_update_file_skip_version_does_not_create_version(client: AsyncClient, db_session):
    """Test skip_version updates content but does not create file versions."""
    from services.core.auth_service import hash_password

    user = User(
        username="user10d", email="user10d@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user10d", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file = File(project_id=project.id, title="File", file_type="draft", content="Old content")
    db_session.add(file)
    db_session.commit()

    response = await client.put(
        f"/api/v1/files/{file.id}",
        json={"content": "New content", "skip_version": True},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    versions = db_session.exec(
        select(FileVersion).where(FileVersion.file_id == file.id)
    ).all()
    assert versions == []


@pytest.mark.integration
async def test_update_file_saves_content_when_file_version_quota_exceeded(client: AsyncClient, db_session):
    """版本额度满时，正文保存仍然成功，只是不再生成版本快照。

    历史行为是「先 commit 正文、再 create_version -> 402 原样上抛」，
    前端显示「保存失败」而正文其实已经落库，属于误导性失败；
    额度限制的是版本历史深度，不应该把用户的正文保存整体判负。
    """
    from datetime import datetime, timedelta

    from models.subscription import SubscriptionPlan, UserSubscription
    from services.core.auth_service import hash_password

    user = User(
        username="user10e",
        email="user10e@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    plan = SubscriptionPlan(
        name=f"file-version-limit-{user.id[:8]}",
        display_name="File Version Limited",
        display_name_en="File Version Limited",
        price_monthly_cents=999,
        price_yearly_cents=9999,
        features={"file_versions_per_file": 1},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()
    db_session.refresh(plan)

    now = datetime.utcnow()
    subscription = UserSubscription(
        user_id=user.id,
        plan_id=plan.id,
        status="active",
        current_period_start=now - timedelta(days=1),
        current_period_end=now + timedelta(days=30),
        cancel_at_period_end=False,
    )
    db_session.add(subscription)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user10e", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file = File(project_id=project.id, title="File", file_type="draft", content="Old content")
    db_session.add(file)
    db_session.commit()

    first_version_response = await client.post(
        f"/api/v1/files/{file.id}/versions",
        json={"content": "Old content", "change_type": "edit"},
        headers=headers,
    )
    assert first_version_response.status_code == 200

    response = await client.put(
        f"/api/v1/files/{file.id}",
        json={"content": "New content"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["content"] == "New content"
    assert payload["version_quota_exceeded"] is True

    db_session.expire_all()
    assert db_session.get(File, file.id).content == "New content"

    versions = db_session.exec(
        select(FileVersion).where(FileVersion.file_id == file.id)
    ).all()
    assert len(versions) == 1

@pytest.mark.integration
async def test_update_file_metadata(client: AsyncClient, db_session):
    """Test updating file metadata."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user11", email="user11@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user11", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and file
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file = File(project_id=project.id, title="Character", file_type="character", content="Hero")
    db_session.add(file)
    db_session.commit()

    # Update file with metadata
    new_metadata = {"age": 30, "gender": "male", "role": "protagonist"}
    response = await client.put(
        f"/api/v1/files/{file.id}",
        json={"metadata": new_metadata},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["file_metadata"] is not None


@pytest.mark.integration
async def test_update_file_persists_word_count_metadata(client: AsyncClient, db_session):
    """Draft/script content updates should persist word_count into file_metadata."""
    import json as json_module

    from services.core.auth_service import hash_password
    from utils.text_metrics import count_words

    user = User(
        username="user11b",
        email="user11b@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "user11b", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Word Count Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    draft = File(
        project_id=project.id,
        title="Draft",
        file_type="draft",
        content="Old content",
    )
    db_session.add(draft)
    db_session.commit()

    new_content = "Hello world 你好世界"
    expected_word_count = count_words(new_content)

    response = await client.put(
        f"/api/v1/files/{draft.id}",
        json={
            "content": new_content,
            "word_count": expected_word_count,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    metadata = json_module.loads(payload["file_metadata"])
    assert metadata.get("word_count") == expected_word_count

    db_draft = db_session.get(File, draft.id)
    assert db_draft is not None
    db_metadata = json_module.loads(db_draft.file_metadata or "{}")
    assert db_metadata.get("word_count") == expected_word_count


@pytest.mark.integration
async def test_update_file_move_to_root(client: AsyncClient, db_session):
    """Test updating file to move it to root (no parent)."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user12", email="user12@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user12", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and folder
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    folder = File(project_id=project.id, title="Folder", file_type="folder")
    db_session.add(folder)
    db_session.commit()

    file = File(project_id=project.id, title="File", file_type="draft", content="Content", parent_id=folder.id)
    db_session.add(file)
    db_session.commit()

    # Move file to root
    response = await client.put(
        f"/api/v1/files/{file.id}",
        json={"parent_id": None},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["parent_id"] is None


@pytest.mark.integration
async def test_update_file_rejects_non_folder_parent(client: AsyncClient, db_session):
    """Test update_file rejects assigning a non-folder parent."""
    from services.core.auth_service import hash_password
    user = User(
        username="user12b", email="user12b@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user12b", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    non_folder_parent = File(project_id=project.id, title="Draft Parent", file_type="draft", content="text")
    moving_file = File(project_id=project.id, title="File", file_type="draft", content="Content")
    db_session.add_all([non_folder_parent, moving_file])
    db_session.commit()

    response = await client.put(
        f"/api/v1/files/{moving_file.id}",
        json={"parent_id": non_folder_parent.id},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_update_file_rejects_folder_cycle(client: AsyncClient, db_session):
    """Test update_file prevents folder cycle through parent_id changes."""
    from services.core.auth_service import hash_password
    user = User(
        username="user12c", email="user12c@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post("/api/auth/login", data={"username": "user12c", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    root_folder = File(project_id=project.id, title="Root", file_type="folder")
    child_folder = File(project_id=project.id, title="Child", file_type="folder", parent_id=root_folder.id)
    db_session.add_all([root_folder, child_folder])
    db_session.commit()

    response = await client.put(
        f"/api/v1/files/{root_folder.id}",
        json={"parent_id": child_folder.id},
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_delete_file_success(client: AsyncClient, db_session):
    """Test deleting a file successfully (soft delete)."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user13", email="user13@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user13", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project and file
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    file = File(project_id=project.id, title="To Delete", file_type="draft", content="Content")
    db_session.add(file)
    db_session.commit()

    # Delete file
    response = await client.delete(
        f"/api/v1/files/{file.id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200

    # Verify file was soft-deleted
    db_session.refresh(file)
    assert file.is_deleted is True
    assert file.deleted_at is not None


@pytest.mark.integration
async def test_delete_file_not_found(client: AsyncClient, db_session):
    """Test deleting a nonexistent file returns 404."""
    # Create user
    from services.core.auth_service import hash_password
    user = User(
        username="user14", email="user14@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "user14", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Try to delete nonexistent file
    response = await client.delete(
        "/api/v1/files/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_get_files_without_auth(client: AsyncClient, db_session):
    """Test getting files without authentication returns 401."""
    response = await client.get("/api/v1/projects/fake-project-id/files")
    assert response.status_code == 401


@pytest.mark.integration
async def test_create_file_without_auth(client: AsyncClient, db_session):
    """Test creating file without authentication returns 401."""
    response = await client.post(
        "/api/v1/projects/fake-project-id/files",
        json={"title": "Unauthorized File"}
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_file_unauthorized_project(client: AsyncClient, db_session):
    """Test getting file from another user's project returns 403."""
    # Create two users
    from services.core.auth_service import hash_password
    user1 = User(
        username="user15", email="user15@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    user2 = User(
        username="user16", email="user16@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user1)
    db_session.add(user2)
    db_session.commit()

    # Login as user1
    login_response = await client.post("/api/auth/login", data={"username": "user15", "password": "password123"})
    assert login_response.status_code == 200
    token1 = login_response.json()["access_token"]

    # Create project and file for user2
    project2 = Project(name="User2 Project", owner_id=user2.id)
    db_session.add(project2)
    db_session.commit()

    file2 = File(project_id=project2.id, title="User2 File", file_type="draft", content="Content")
    db_session.add(file2)
    db_session.commit()

    # Try to get user2's file as user1
    response = await client.get(
        f"/api/v1/files/{file2.id}",
        headers={"Authorization": f"Bearer {token1}"}
    )
    assert response.status_code == 403


@pytest.mark.integration
async def test_update_file_unauthorized_project(client: AsyncClient, db_session):
    """Test updating file from another user's project returns 403."""
    # Create two users
    from services.core.auth_service import hash_password
    user1 = User(
        username="user17", email="user17@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    user2 = User(
        username="user18", email="user18@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user1)
    db_session.add(user2)
    db_session.commit()

    # Login as user1
    login_response = await client.post("/api/auth/login", data={"username": "user17", "password": "password123"})
    assert login_response.status_code == 200
    token1 = login_response.json()["access_token"]

    # Create project and file for user2
    project2 = Project(name="User2 Project", owner_id=user2.id)
    db_session.add(project2)
    db_session.commit()

    file2 = File(project_id=project2.id, title="User2 File", file_type="draft", content="Content")
    db_session.add(file2)
    db_session.commit()

    # Try to update user2's file as user1
    response = await client.put(
        f"/api/v1/files/{file2.id}",
        json={"title": "Hacked Title"},
        headers={"Authorization": f"Bearer {token1}"}
    )
    assert response.status_code == 403


@pytest.mark.integration
async def test_delete_file_unauthorized_project(client: AsyncClient, db_session):
    """Test deleting file from another user's project returns 403."""
    # Create two users
    from services.core.auth_service import hash_password
    user1 = User(
        username="user19", email="user19@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    user2 = User(
        username="user20", email="user20@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True, is_active=True
    )
    db_session.add(user1)
    db_session.add(user2)
    db_session.commit()

    # Login as user1
    login_response = await client.post("/api/auth/login", data={"username": "user19", "password": "password123"})
    assert login_response.status_code == 200
    token1 = login_response.json()["access_token"]

    # Create project and file for user2
    project2 = Project(name="User2 Project", owner_id=user2.id)
    db_session.add(project2)
    db_session.commit()

    file2 = File(project_id=project2.id, title="User2 File", file_type="draft", content="Content")
    db_session.add(file2)
    db_session.commit()

    # Try to delete user2's file as user1
    response = await client.delete(
        f"/api/v1/files/{file2.id}",
        headers={"Authorization": f"Bearer {token1}"}
    )
    assert response.status_code == 403
