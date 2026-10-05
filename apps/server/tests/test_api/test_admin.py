"""
Tests for Admin API.

Tests admin endpoints for user and system prompt management:
- User CRUD operations (requires superuser privileges)
- System prompt configuration management
- Permission control (non-superuser should get 403)
"""

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import SystemPromptConfig, User
from models.referral import InviteCode
from services.core.auth_service import hash_password
from services.features.referral_service import MAX_INVITE_CODES_PER_USER
from sqlmodel import select


# ============================================
# Helper Functions
# ============================================


async def create_user(
    db_session: Session,
    username: str,
    email: str,
    password: str = "password123",
    is_superuser: bool = False,
    email_verified: bool = True,
    is_active: bool = True,
) -> User:
    """Create a user in the database."""
    user = User(
        username=username,
        email=email,
        hashed_password=hash_password(password),
        email_verified=email_verified,
        is_active=is_active,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def login_user(client: AsyncClient, username: str, password: str = "password123") -> str:
    """Login and return access token."""
    response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": password}
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_headers(token: str) -> dict:
    """Return authorization headers."""
    return {"Authorization": f"Bearer {token}"}


# ============================================
# Permission Tests - Non-superuser Access
# ============================================


@pytest.mark.integration
async def test_get_users_forbidden_for_non_superuser(client: AsyncClient, db_session: Session):
    """Test that non-superuser cannot access user list."""
    user = await create_user(db_session, "normaluser", "normal@example.com")
    token = await login_user(client, "normaluser")

    response = await client.get("/api/admin/users", headers=auth_headers(token))

    assert response.status_code == 403


@pytest.mark.integration
async def test_get_user_forbidden_for_non_superuser(client: AsyncClient, db_session: Session):
    """Test that non-superuser cannot access user details."""
    user = await create_user(db_session, "normaluser", "normal@example.com")
    target_user = await create_user(db_session, "targetuser", "target@example.com")
    token = await login_user(client, "normaluser")

    response = await client.get(f"/api/admin/users/{target_user.id}", headers=auth_headers(token))

    assert response.status_code == 403


@pytest.mark.integration
async def test_update_user_forbidden_for_non_superuser(client: AsyncClient, db_session: Session):
    """Test that non-superuser cannot update users."""
    user = await create_user(db_session, "normaluser", "normal@example.com")
    target_user = await create_user(db_session, "targetuser", "target@example.com")
    token = await login_user(client, "normaluser")

    response = await client.put(
        f"/api/admin/users/{target_user.id}",
        headers=auth_headers(token),
        json={"username": "updated"}
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_delete_user_forbidden_for_non_superuser(client: AsyncClient, db_session: Session):
    """Test that non-superuser cannot delete users."""
    user = await create_user(db_session, "normaluser", "normal@example.com")
    target_user = await create_user(db_session, "targetuser", "target@example.com")
    token = await login_user(client, "normaluser")

    response = await client.delete(f"/api/admin/users/{target_user.id}", headers=auth_headers(token))

    assert response.status_code == 403


@pytest.mark.integration
async def test_get_prompts_forbidden_for_non_superuser(client: AsyncClient, db_session: Session):
    """Test that non-superuser cannot access prompt list."""
    user = await create_user(db_session, "normaluser", "normal@example.com")
    token = await login_user(client, "normaluser")

    response = await client.get("/api/admin/prompts", headers=auth_headers(token))

    assert response.status_code == 403


@pytest.mark.integration
async def test_get_prompt_forbidden_for_non_superuser(client: AsyncClient, db_session: Session):
    """Test that non-superuser cannot access prompt details."""
    user = await create_user(db_session, "normaluser", "normal@example.com")
    token = await login_user(client, "normaluser")

    response = await client.get("/api/admin/prompts/novel", headers=auth_headers(token))

    assert response.status_code == 403


@pytest.mark.integration
async def test_update_prompt_forbidden_for_non_superuser(client: AsyncClient, db_session: Session):
    """Test that non-superuser cannot update prompts."""
    user = await create_user(db_session, "normaluser", "normal@example.com")
    token = await login_user(client, "normaluser")

    response = await client.put(
        "/api/admin/prompts/novel",
        headers=auth_headers(token),
        json={
            "role_definition": "Test role",
            "capabilities": "Test capabilities"
        }
    )

    assert response.status_code == 403


@pytest.mark.integration
async def test_create_invite_code_forbidden_for_non_superuser(client: AsyncClient, db_session: Session):
    """Test that non-superuser cannot create admin invite codes."""
    await create_user(db_session, "normaluser", "normal@example.com")
    token = await login_user(client, "normaluser")

    response = await client.post("/api/admin/invites", headers=auth_headers(token))

    assert response.status_code == 403


# ============================================
# User Management Tests - Superuser Access
# ============================================


@pytest.mark.integration
async def test_get_users_success(client: AsyncClient, db_session: Session):
    """Test superuser can get user list."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    user1 = await create_user(db_session, "user1", "user1@example.com")
    user2 = await create_user(db_session, "user2", "user2@example.com")
    token = await login_user(client, "admin")

    response = await client.get("/api/admin/users", headers=auth_headers(token))

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) >= 3  # At least 3 users created
    assert data["total"] == len(data["items"])


@pytest.mark.integration
async def test_create_invite_code_success_for_superuser(client: AsyncClient, db_session: Session):
    """Test superuser can create invite codes from admin referrals page."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    response = await client.post("/api/admin/invites", headers=auth_headers(token))

    assert response.status_code == 201
    payload = response.json()
    assert payload["owner_id"] == superuser.id
    assert payload["owner_name"] == superuser.username
    assert isinstance(payload["code"], str) and len(payload["code"]) >= 9

    db_code = db_session.exec(
        select(InviteCode).where(InviteCode.id == payload["id"])
    ).first()
    assert db_code is not None
    assert db_code.owner_id == superuser.id


@pytest.mark.integration
async def test_create_invite_code_unlimited_for_superuser(client: AsyncClient, db_session: Session):
    """Test superuser can create more than MAX_INVITE_CODES_PER_USER invite codes via admin endpoint."""
    superuser = await create_user(db_session, "admin_unlimited", "admin_unlimited@example.com", is_superuser=True)
    token = await login_user(client, "admin_unlimited")

    create_count = MAX_INVITE_CODES_PER_USER + 2
    created_ids: set[str] = set()

    for _ in range(create_count):
        response = await client.post("/api/admin/invites", headers=auth_headers(token))
        assert response.status_code == 201
        payload = response.json()
        created_ids.add(payload["id"])

    assert len(created_ids) == create_count

    db_codes = db_session.exec(
        select(InviteCode)
        .where(InviteCode.owner_id == superuser.id)
        .where(InviteCode.is_active == True)
    ).all()

    assert len(db_codes) == create_count


@pytest.mark.integration
async def test_get_users_pagination(client: AsyncClient, db_session: Session):
    """Test user list pagination."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    # Create multiple users
    for i in range(5):
        await create_user(db_session, f"user{i}", f"user{i}@example.com")
    token = await login_user(client, "admin")

    # Test skip and limit
    response = await client.get(
        "/api/admin/users?skip=0&limit=2",
        headers=auth_headers(token)
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert data["total"] == 6


@pytest.mark.integration
async def test_get_users_search(client: AsyncClient, db_session: Session):
    """Test user list search by username/email."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    user1 = await create_user(db_session, "alice", "alice@example.com")
    user2 = await create_user(db_session, "bob", "bob@example.com")
    token = await login_user(client, "admin")

    response = await client.get(
        "/api/admin/users?search=alice",
        headers=auth_headers(token)
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["username"] == "alice"


@pytest.mark.integration
async def test_get_users_total_is_stable_across_pages_and_follows_search(
    client: AsyncClient, db_session: Session
):
    """total counts every matching user, not the rows seen so far (regression)."""
    await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    shared_hash = hash_password("password123")
    for i in range(24):
        prefix = "reader" if i < 7 else "writer"
        db_session.add(
            User(
                username=f"{prefix}{i:02d}",
                email=f"{prefix}{i:02d}@example.com",
                hashed_password=shared_hash,
                email_verified=True,
            )
        )
    db_session.commit()
    token = await login_user(client, "admin")

    pages = []
    for skip in (0, 10, 20):
        response = await client.get(
            f"/api/admin/users?skip={skip}&limit=10", headers=auth_headers(token)
        )
        assert response.status_code == 200
        pages.append(response.json())

    assert [page["total"] for page in pages] == [25, 25, 25]
    assert [len(page["items"]) for page in pages] == [10, 10, 5]
    ids = [item["id"] for page in pages for item in page["items"]]
    assert len(set(ids)) == 25

    filtered = []
    for skip in (0, 5):
        response = await client.get(
            f"/api/admin/users?skip={skip}&limit=5&search=reader",
            headers=auth_headers(token),
        )
        assert response.status_code == 200
        filtered.append(response.json())

    assert [page["total"] for page in filtered] == [7, 7]
    assert [len(page["items"]) for page in filtered] == [5, 2]
    filtered_names = {item["username"] for page in filtered for item in page["items"]}
    assert filtered_names == {f"reader{i:02d}" for i in range(7)}


@pytest.mark.integration
async def test_get_user_success(client: AsyncClient, db_session: Session):
    """Test superuser can get user details."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    target_user = await create_user(db_session, "targetuser", "target@example.com")
    token = await login_user(client, "admin")

    response = await client.get(
        f"/api/admin/users/{target_user.id}",
        headers=auth_headers(token)
    )

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == target_user.id
    assert data["username"] == "targetuser"
    assert data["email"] == "target@example.com"


@pytest.mark.integration
async def test_get_user_not_found(client: AsyncClient, db_session: Session):
    """Test getting non-existent user returns 404."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    response = await client.get(
        "/api/admin/users/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(token)
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_update_user_success(client: AsyncClient, db_session: Session):
    """Test superuser can update user."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    target_user = await create_user(db_session, "targetuser", "target@example.com")
    token = await login_user(client, "admin")

    response = await client.put(
        f"/api/admin/users/{target_user.id}",
        headers=auth_headers(token),
        json={"username": "updateduser", "is_active": False}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["username"] == "updateduser"
    assert data["is_active"] is False


@pytest.mark.integration
async def test_update_user_not_found(client: AsyncClient, db_session: Session):
    """Test updating non-existent user returns 404."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    response = await client.put(
        "/api/admin/users/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(token),
        json={"username": "updated"}
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_delete_user_success(client: AsyncClient, db_session: Session):
    """Test superuser can soft delete user."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    target_user = await create_user(db_session, "targetuser", "target@example.com")
    token = await login_user(client, "admin")

    response = await client.delete(
        f"/api/admin/users/{target_user.id}",
        headers=auth_headers(token)
    )

    assert response.status_code == 200
    data = response.json()
    assert data["is_active"] is False


@pytest.mark.integration
async def test_delete_user_not_found(client: AsyncClient, db_session: Session):
    """Test deleting non-existent user returns 404."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    response = await client.delete(
        "/api/admin/users/00000000-0000-0000-0000-000000000000",
        headers=auth_headers(token)
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_delete_self_forbidden(client: AsyncClient, db_session: Session):
    """Test superuser cannot delete themselves."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    response = await client.delete(
        f"/api/admin/users/{superuser.id}",
        headers=auth_headers(token)
    )

    assert response.status_code == 400


# ============================================
# System Prompt Management Tests
# ============================================


@pytest.mark.integration
async def test_get_prompts_empty(client: AsyncClient, db_session: Session):
    """Test getting prompts when none exist."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    response = await client.get("/api/admin/prompts", headers=auth_headers(token))

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


@pytest.mark.integration
async def test_get_prompts_with_data(client: AsyncClient, db_session: Session):
    """Test getting prompts when some exist."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    # Create a prompt config
    prompt = SystemPromptConfig(
        project_type="novel",
        role_definition="Novel writing assistant",
        capabilities="Help with novel writing",
        created_by=superuser.id,
        updated_by=superuser.id,
    )
    db_session.add(prompt)
    db_session.commit()

    response = await client.get("/api/admin/prompts", headers=auth_headers(token))

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["project_type"] == "novel"


@pytest.mark.integration
async def test_get_prompt_success(client: AsyncClient, db_session: Session):
    """Test getting a specific prompt by project type."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    # Create a prompt config
    prompt = SystemPromptConfig(
        project_type="novel",
        role_definition="Novel writing assistant",
        capabilities="Help with novel writing",
        created_by=superuser.id,
        updated_by=superuser.id,
    )
    db_session.add(prompt)
    db_session.commit()

    response = await client.get("/api/admin/prompts/novel", headers=auth_headers(token))

    assert response.status_code == 200
    data = response.json()
    assert data["project_type"] == "novel"
    assert data["role_definition"] == "Novel writing assistant"


@pytest.mark.integration
async def test_get_prompt_not_found(client: AsyncClient, db_session: Session):
    """Test getting non-existent prompt returns 404."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    response = await client.get("/api/admin/prompts/nonexistent", headers=auth_headers(token))

    assert response.status_code == 404


@pytest.mark.integration
async def test_create_prompt_success(client: AsyncClient, db_session: Session):
    """Test creating a new prompt configuration."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    response = await client.put(
        "/api/admin/prompts/novel",
        headers=auth_headers(token),
        json={
            "role_definition": "Novel writing assistant",
            "capabilities": "Help with plot, characters, and world-building",
            "directory_structure": "chapters/, characters/, world/",
            "content_structure": "Introduction, Rising Action, Climax, Resolution",
            "file_types": "outline, draft, character, lore",
            "writing_guidelines": "Show don't tell, consistent POV",
            "include_dialogue_guidelines": True,
            "primary_content_type": "prose",
            "is_active": True
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["project_type"] == "novel"
    assert data["role_definition"] == "Novel writing assistant"
    assert data["version"] == 1
    assert data["created_by"] == superuser.id


@pytest.mark.integration
async def test_update_existing_prompt(client: AsyncClient, db_session: Session):
    """Test updating an existing prompt configuration."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    # Create initial prompt
    prompt = SystemPromptConfig(
        project_type="novel",
        role_definition="Original role",
        capabilities="Original capabilities",
        version=1,
        created_by=superuser.id,
        updated_by=superuser.id,
    )
    db_session.add(prompt)
    db_session.commit()

    response = await client.put(
        "/api/admin/prompts/novel",
        headers=auth_headers(token),
        json={
            "role_definition": "Updated role",
            "capabilities": "Updated capabilities",
            "expected_version": 1,
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["role_definition"] == "Updated role"
    assert data["capabilities"] == "Updated capabilities"
    assert data["version"] == 2  # Version incremented


@pytest.mark.integration
async def test_delete_prompt_success(client: AsyncClient, db_session: Session):
    """Test deleting a prompt configuration."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    # Create a prompt
    prompt = SystemPromptConfig(
        project_type="novel",
        role_definition="Test role",
        capabilities="Test capabilities",
        created_by=superuser.id,
        updated_by=superuser.id,
    )
    db_session.add(prompt)
    db_session.commit()

    response = await client.delete("/api/admin/prompts/novel", headers=auth_headers(token))

    assert response.status_code == 200
    data = response.json()
    assert "message" in data


@pytest.mark.integration
async def test_delete_prompt_not_found(client: AsyncClient, db_session: Session):
    """Test deleting non-existent prompt returns 404."""
    superuser = await create_user(db_session, "admin", "admin@example.com", is_superuser=True)
    token = await login_user(client, "admin")

    response = await client.delete("/api/admin/prompts/nonexistent", headers=auth_headers(token))

    assert response.status_code == 404


# ============================================
# Unauthenticated Access Tests
# ============================================


@pytest.mark.integration
async def test_admin_endpoints_require_auth(client: AsyncClient):
    """Test that all admin endpoints require authentication."""
    endpoints = [
        ("GET", "/api/admin/users"),
        ("GET", "/api/admin/users/some-id"),
        ("PUT", "/api/admin/users/some-id"),
        ("DELETE", "/api/admin/users/some-id"),
        ("GET", "/api/admin/prompts"),
        ("GET", "/api/admin/prompts/novel"),
        ("PUT", "/api/admin/prompts/novel"),
        ("DELETE", "/api/admin/prompts/novel"),
    ]

    for method, endpoint in endpoints:
        if method == "GET":
            response = await client.get(endpoint)
        elif method == "PUT":
            response = await client.put(endpoint, json={})
        elif method == "DELETE":
            response = await client.delete(endpoint)

        assert response.status_code == 401, f"{method} {endpoint} should require auth"
