"""
Tests for Skills API.

Tests skill management operations:
- GET /api/v1/skills - List user's skills
- POST /api/v1/skills - Create skill
- PUT /api/v1/skills/{id} - Update skill
- DELETE /api/v1/skills/{id} - Delete skill
- GET /api/v1/skills/stats/{project_id} - Get skill stats
- GET /api/v1/skills/my-skills - Get my skills
- POST /api/v1/skills/{id}/share - Share skill
- POST /api/v1/skills/batch-update - Batch update skills
"""

import json

import pytest
from httpx import AsyncClient

from models import Project, PublicSkill, SkillResource, User, UserAddedSkill, UserSkill


async def create_user_and_login(client: AsyncClient, db_session, username: str, email: str):
    """Helper to create a user and return user info with auth token."""
    from services.core.auth_service import hash_password

    user = User(
        username=username,
        email=email,
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login", data={"username": username, "password": "password123"}
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    return {"user": user, "token": token}


# ==================== List Skills Tests ====================


@pytest.mark.integration
async def test_list_skills_empty(client: AsyncClient, db_session):
    """Test listing skills when user has none."""
    setup = await create_user_and_login(client, db_session, "skilluser1", "skilluser1@example.com")
    token = setup["token"]

    response = await client.get(
        "/api/v1/skills", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "skills" in data
    assert "total" in data
    assert data["total"] == 0
    assert len(data["skills"]) == 0


@pytest.mark.integration
async def test_list_skills_with_user_skills(client: AsyncClient, db_session):
    """Test listing skills when user has custom skills."""
    setup = await create_user_and_login(client, db_session, "skilluser2", "skilluser2@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create user skills
    skill1 = UserSkill(
        user_id=user.id,
        name="Plot Helper",
        description="Helps with plot development",
        triggers=json.dumps(["plot", "storyline"]),
        instructions="Focus on plot structure and pacing.",
    )
    skill2 = UserSkill(
        user_id=user.id,
        name="Character Builder",
        description="Helps with character creation",
        triggers=json.dumps(["character", "persona"]),
        instructions="Focus on character depth and motivation.",
    )
    db_session.add(skill1)
    db_session.add(skill2)
    db_session.commit()

    response = await client.get(
        "/api/v1/skills", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["skills"]) == 2

    skill_names = [s["name"] for s in data["skills"]]
    assert "Plot Helper" in skill_names
    assert "Character Builder" in skill_names


@pytest.mark.integration
async def test_list_skills_with_added_public_skills(client: AsyncClient, db_session):
    """Test listing skills includes added public skills."""
    setup = await create_user_and_login(client, db_session, "skilluser3", "skilluser3@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create a public skill
    public_skill = PublicSkill(
        name="Writing Style Guide",
        description="Official writing style helper",
        instructions="Follow consistent style guidelines.",
        category="style",
        tags=json.dumps(["style", "writing"]),
        source="official",
        status="approved",
    )
    db_session.add(public_skill)
    db_session.commit()

    # Add public skill to user
    added_skill = UserAddedSkill(
        user_id=user.id,
        public_skill_id=public_skill.id,
        is_active=True,
    )
    db_session.add(added_skill)
    db_session.commit()

    response = await client.get(
        "/api/v1/skills", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["skills"][0]["name"] == "Writing Style Guide"
    assert data["skills"][0]["source"] == "added"


@pytest.mark.integration
async def test_list_skills_with_search(client: AsyncClient, db_session):
    """Test listing skills with search filter."""
    setup = await create_user_and_login(client, db_session, "skilluser4", "skilluser4@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create user skills
    skill1 = UserSkill(
        user_id=user.id,
        name="Plot Helper",
        description="Helps with plot development",
        triggers=json.dumps(["plot"]),
        instructions="Focus on plot structure.",
    )
    skill2 = UserSkill(
        user_id=user.id,
        name="Character Builder",
        description="Helps with character creation",
        triggers=json.dumps(["character"]),
        instructions="Focus on character depth.",
    )
    db_session.add(skill1)
    db_session.add(skill2)
    db_session.commit()

    # Search for "plot"
    response = await client.get(
        "/api/v1/skills?search=plot",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["skills"][0]["name"] == "Plot Helper"


@pytest.mark.integration
async def test_list_skills_invalid_json_fields_fallback_empty_array(client: AsyncClient, db_session):
    """Test malformed triggers/tags JSON does not crash list endpoint."""
    setup = await create_user_and_login(client, db_session, "skilluser4b", "skilluser4b@example.com")
    user = setup["user"]
    token = setup["token"]

    user_skill = UserSkill(
        user_id=user.id,
        name="Broken User Skill",
        description="Has invalid triggers JSON",
        triggers="{invalid-json",
        instructions="Ignore bad triggers",
    )
    public_skill = PublicSkill(
        name="Broken Public Skill",
        description="Has invalid tags JSON",
        instructions="Ignore bad tags",
        category="writing",
        tags="{invalid-json",
        source="official",
        status="approved",
    )
    db_session.add(user_skill)
    db_session.add(public_skill)
    db_session.commit()

    added_skill = UserAddedSkill(
        user_id=user.id,
        public_skill_id=public_skill.id,
        is_active=True,
    )
    db_session.add(added_skill)
    db_session.commit()

    response = await client.get(
        "/api/v1/skills", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    data = response.json()
    skill_by_name = {skill["name"]: skill for skill in data["skills"]}
    assert skill_by_name["Broken User Skill"]["triggers"] == []
    assert skill_by_name["Broken Public Skill"]["triggers"] == []


# ==================== Create Skill Tests ====================


@pytest.mark.integration
async def test_create_skill_success(client: AsyncClient, db_session):
    """Test creating a skill successfully."""
    setup = await create_user_and_login(client, db_session, "skilluser5", "skilluser5@example.com")
    token = setup["token"]

    response = await client.post(
        "/api/v1/skills",
        json={
            "name": "World Builder",
            "description": "Helps build immersive worlds",
            "triggers": ["world", "setting", "environment"],
            "instructions": "Focus on world-building details.",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "World Builder"
    assert data["description"] == "Helps build immersive worlds"
    assert data["triggers"] == ["world", "setting", "environment"]
    assert data["instructions"] == "Focus on world-building details."
    assert data["source"] == "user"
    assert data["is_active"] is True


@pytest.mark.integration
async def test_create_skill_without_auth(client: AsyncClient, db_session):
    """Test creating a skill without authentication returns 401."""
    response = await client.post(
        "/api/v1/skills",
        json={
            "name": "Unauthorized Skill",
            "triggers": ["test"],
            "instructions": "Test instructions",
        },
    )
    assert response.status_code == 401


# ==================== Update Skill Tests ====================


@pytest.mark.integration
async def test_update_skill_success(client: AsyncClient, db_session):
    """Test updating a skill successfully."""
    setup = await create_user_and_login(client, db_session, "skilluser6", "skilluser6@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create skill
    skill = UserSkill(
        user_id=user.id,
        name="Old Name",
        description="Old description",
        triggers=json.dumps(["old"]),
        instructions="Old instructions",
    )
    db_session.add(skill)
    db_session.commit()

    response = await client.put(
        f"/api/v1/skills/{skill.id}",
        json={
            "name": "New Name",
            "description": "New description",
            "triggers": ["new", "updated"],
            "instructions": "New instructions",
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "New Name"
    assert data["description"] == "New description"
    assert data["triggers"] == ["new", "updated"]
    assert data["instructions"] == "New instructions"


@pytest.mark.integration
async def test_update_skill_partial(client: AsyncClient, db_session):
    """Test partially updating a skill."""
    setup = await create_user_and_login(client, db_session, "skilluser7", "skilluser7@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create skill
    skill = UserSkill(
        user_id=user.id,
        name="Original Name",
        description="Original description",
        triggers=json.dumps(["original"]),
        instructions="Original instructions",
    )
    db_session.add(skill)
    db_session.commit()

    # Update only name
    response = await client.put(
        f"/api/v1/skills/{skill.id}",
        json={"name": "Updated Name"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["description"] == "Original description"


@pytest.mark.integration
async def test_update_skill_not_found(client: AsyncClient, db_session):
    """Test updating a nonexistent skill returns 404."""
    setup = await create_user_and_login(client, db_session, "skilluser8", "skilluser8@example.com")
    token = setup["token"]

    response = await client.put(
        "/api/v1/skills/00000000-0000-0000-0000-000000000000",
        json={"name": "New Name"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_update_skill_other_user(client: AsyncClient, db_session):
    """Test updating another user's skill returns 404."""
    setup1 = await create_user_and_login(client, db_session, "skilluser9", "skilluser9@example.com")
    user1 = setup1["user"]

    setup2 = await create_user_and_login(client, db_session, "skilluser10", "skilluser10@example.com")
    token2 = setup2["token"]

    # Create skill for user1
    skill = UserSkill(
        user_id=user1.id,
        name="User1 Skill",
        triggers=json.dumps(["test"]),
        instructions="Test instructions",
    )
    db_session.add(skill)
    db_session.commit()

    # Try to update as user2
    response = await client.put(
        f"/api/v1/skills/{skill.id}",
        json={"name": "Hacked Name"},
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_update_skill_toggle_active(client: AsyncClient, db_session):
    """Test toggling skill active status."""
    setup = await create_user_and_login(client, db_session, "skilluser11", "skilluser11@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create skill
    skill = UserSkill(
        user_id=user.id,
        name="Toggle Skill",
        triggers=json.dumps(["toggle"]),
        instructions="Toggle instructions",
        is_active=True,
    )
    db_session.add(skill)
    db_session.commit()

    # Deactivate
    response = await client.put(
        f"/api/v1/skills/{skill.id}",
        json={"is_active": False},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["is_active"] is False


# ==================== Delete Skill Tests ====================


@pytest.mark.integration
async def test_delete_skill_success(client: AsyncClient, db_session):
    """Test deleting a skill successfully."""
    setup = await create_user_and_login(client, db_session, "skilluser12", "skilluser12@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create skill
    skill = UserSkill(
        user_id=user.id,
        name="To Delete",
        triggers=json.dumps(["delete"]),
        instructions="Delete me",
    )
    db_session.add(skill)
    db_session.commit()
    skill_id = skill.id

    response = await client.delete(
        f"/api/v1/skills/{skill_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True

    # Verify skill is deleted
    deleted_skill = db_session.get(UserSkill, skill_id)
    assert deleted_skill is None


@pytest.mark.integration
async def test_delete_skill_not_found(client: AsyncClient, db_session):
    """Test deleting a nonexistent skill returns 404."""
    setup = await create_user_and_login(client, db_session, "skilluser13", "skilluser13@example.com")
    token = setup["token"]

    response = await client.delete(
        "/api/v1/skills/00000000-0000-0000-0000-000000000000",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_delete_skill_other_user(client: AsyncClient, db_session):
    """Test deleting another user's skill returns 404."""
    setup1 = await create_user_and_login(client, db_session, "skilluser14", "skilluser14@example.com")
    user1 = setup1["user"]

    setup2 = await create_user_and_login(client, db_session, "skilluser15", "skilluser15@example.com")
    token2 = setup2["token"]

    # Create skill for user1
    skill = UserSkill(
        user_id=user1.id,
        name="User1 Skill",
        triggers=json.dumps(["test"]),
        instructions="Test instructions",
    )
    db_session.add(skill)
    db_session.commit()

    # Try to delete as user2
    response = await client.delete(
        f"/api/v1/skills/{skill.id}",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert response.status_code == 404

    # Verify skill still exists
    existing_skill = db_session.get(UserSkill, skill.id)
    assert existing_skill is not None


# ==================== Skill Stats Tests ====================


@pytest.mark.integration
async def test_get_skill_stats_success(client: AsyncClient, db_session):
    """Test getting skill stats for a project."""
    setup = await create_user_and_login(client, db_session, "skilluser16", "skilluser16@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create project
    project = Project(name="Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    response = await client.get(
        f"/api/v1/skills/stats/{project.id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "total_triggers" in data
    assert "builtin_count" in data
    assert "user_count" in data
    assert "avg_confidence" not in data
    assert "top_skills" in data
    assert "daily_usage" in data


@pytest.mark.integration
async def test_get_skill_stats_unauthorized_project(client: AsyncClient, db_session):
    """Test getting stats for another user's project returns 403."""
    setup1 = await create_user_and_login(client, db_session, "skilluser17", "skilluser17@example.com")
    user1 = setup1["user"]

    setup2 = await create_user_and_login(client, db_session, "skilluser18", "skilluser18@example.com")
    token2 = setup2["token"]

    # Create project for user1
    project = Project(name="User1 Project", owner_id=user1.id)
    db_session.add(project)
    db_session.commit()

    # Try to get stats as user2
    response = await client.get(
        f"/api/v1/skills/stats/{project.id}",
        headers={"Authorization": f"Bearer {token2}"},
    )
    assert response.status_code == 403


# ==================== My Skills Tests ====================


@pytest.mark.integration
async def test_get_my_skills_empty(client: AsyncClient, db_session):
    """Test getting my skills when user has none."""
    setup = await create_user_and_login(client, db_session, "skilluser19", "skilluser19@example.com")
    token = setup["token"]

    response = await client.get(
        "/api/v1/skills/my-skills",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert "user_skills" in data
    assert "added_skills" in data
    assert "total" in data
    assert data["total"] == 0


@pytest.mark.integration
async def test_get_my_skills_with_skills(client: AsyncClient, db_session):
    """Test getting my skills with both custom and added skills."""
    setup = await create_user_and_login(client, db_session, "skilluser20", "skilluser20@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create user skill
    user_skill = UserSkill(
        user_id=user.id,
        name="My Custom Skill",
        triggers=json.dumps(["custom"]),
        instructions="Custom instructions",
    )
    db_session.add(user_skill)

    # Create and add public skill
    public_skill = PublicSkill(
        name="Public Skill",
        description="A public skill",
        instructions="Public instructions",
        category="writing",
        tags=json.dumps(["public"]),
        source="official",
        status="approved",
    )
    db_session.add(public_skill)
    db_session.commit()

    added_skill = UserAddedSkill(
        user_id=user.id,
        public_skill_id=public_skill.id,
        is_active=True,
    )
    db_session.add(added_skill)
    db_session.commit()

    response = await client.get(
        "/api/v1/skills/my-skills",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["user_skills"]) == 1
    assert len(data["added_skills"]) == 1
    assert data["user_skills"][0]["name"] == "My Custom Skill"
    assert data["added_skills"][0]["name"] == "Public Skill"


@pytest.mark.integration
async def test_get_my_skills_excludes_inactive_added_skills(client: AsyncClient, db_session):
    """Test my-skills endpoint only includes active added skills."""
    setup = await create_user_and_login(client, db_session, "skilluser20b", "skilluser20b@example.com")
    user = setup["user"]
    token = setup["token"]

    public_skill = PublicSkill(
        name="Public Skill Inactive",
        description="A public skill",
        instructions="Public instructions",
        category="writing",
        tags=json.dumps(["public"]),
        source="official",
        status="approved",
    )
    db_session.add(public_skill)
    db_session.commit()

    inactive_added = UserAddedSkill(
        user_id=user.id,
        public_skill_id=public_skill.id,
        is_active=False,
    )
    db_session.add(inactive_added)
    db_session.commit()

    response = await client.get(
        "/api/v1/skills/my-skills",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["added_skills"] == []
    assert data["total"] == 0


@pytest.mark.integration
async def test_get_my_skills_with_search(client: AsyncClient, db_session):
    """Test getting my skills with search filter."""
    setup = await create_user_and_login(client, db_session, "skilluser21", "skilluser21@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create user skills
    skill1 = UserSkill(
        user_id=user.id,
        name="World Building",
        description="Helps with worlds",
        triggers=json.dumps(["world"]),
        instructions="World instructions",
    )
    skill2 = UserSkill(
        user_id=user.id,
        name="Character Guide",
        description="Helps with characters",
        triggers=json.dumps(["character"]),
        instructions="Character instructions",
    )
    db_session.add(skill1)
    db_session.add(skill2)
    db_session.commit()

    response = await client.get(
        "/api/v1/skills/my-skills?search=world",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["user_skills"][0]["name"] == "World Building"


# ==================== Share Skill Tests ====================


@pytest.mark.integration
async def test_share_skill_success(client: AsyncClient, db_session):
    """Test sharing a skill to the public library."""
    setup = await create_user_and_login(client, db_session, "skilluser22", "skilluser22@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create skill
    skill = UserSkill(
        user_id=user.id,
        name="Shareable Skill",
        description="A skill to share",
        triggers=json.dumps(["share"]),
        instructions="Shareable instructions",
        is_shared=False,
    )
    db_session.add(skill)
    db_session.commit()

    response = await client.post(
        f"/api/v1/skills/{skill.id}/share",
        json={"category": "writing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["message"] == "Skill submitted for review"
    assert data["public_skill_id"] is not None

    # Verify skill is marked as shared
    db_session.refresh(skill)
    assert skill.is_shared is True
    assert skill.shared_skill_id == data["public_skill_id"]


@pytest.mark.integration
async def test_share_skill_rolls_back_public_copy_when_user_link_staging_fails(
    client: AsyncClient, db_session, monkeypatch
):
    setup = await create_user_and_login(
        client, db_session, "skillshareatomic", "skillshareatomic@example.com"
    )
    user = setup["user"]
    token = setup["token"]
    skill = UserSkill(
        user_id=user.id,
        name="Atomic share",
        triggers="[]",
        instructions="Atomic instructions",
    )
    db_session.add(skill)
    db_session.commit()
    db_session.add(
        SkillResource(
            user_skill_id=skill.id,
            path="references/atomic.md",
            content="atomic",
            size=6,
        )
    )
    db_session.commit()

    session_type = type(db_session)
    original_add = session_type.add

    def fail_when_link_is_staged(session, instance, *args, **kwargs):
        if isinstance(instance, UserSkill) and instance.id == skill.id and instance.is_shared:
            raise RuntimeError("injected user-skill link failure")
        return original_add(session, instance, *args, **kwargs)

    monkeypatch.setattr(session_type, "add", fail_when_link_is_staged)
    failed = await client.post(
        f"/api/v1/skills/{skill.id}/share",
        json={"category": "writing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert failed.status_code == 500

    db_session.rollback()
    assert db_session.query(PublicSkill).filter(PublicSkill.author_id == user.id).count() == 0
    assert db_session.query(SkillResource).filter(SkillResource.public_skill_id.is_not(None)).count() == 0
    db_session.refresh(skill)
    assert skill.is_shared is False
    assert skill.shared_skill_id is None

    monkeypatch.undo()
    retry = await client.post(
        f"/api/v1/skills/{skill.id}/share",
        json={"category": "writing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert retry.status_code == 200
    public_skill_id = retry.json()["public_skill_id"]
    assert db_session.query(PublicSkill).filter(PublicSkill.author_id == user.id).count() == 1
    assert (
        db_session.query(SkillResource)
        .filter(SkillResource.public_skill_id == public_skill_id)
        .count()
        == 1
    )


@pytest.mark.integration
async def test_share_skill_preserves_triggers_for_public_added_copy(client: AsyncClient, db_session):
    """Shared community skills should keep triggers when another user adds them."""
    author_setup = await create_user_and_login(
        client,
        db_session,
        "skilluser22b_author",
        "skilluser22b_author@example.com",
    )
    author = author_setup["user"]
    author_token = author_setup["token"]

    skill = UserSkill(
        user_id=author.id,
        name="Shareable Trigger Skill",
        description="A skill with explicit triggers",
        triggers=json.dumps(["/share-trigger", "共享触发"]),
        instructions="Use the shared trigger instructions.",
        is_shared=False,
    )
    db_session.add(skill)
    db_session.commit()

    share_response = await client.post(
        f"/api/v1/skills/{skill.id}/share",
        json={"category": "writing"},
        headers={"Authorization": f"Bearer {author_token}"},
    )
    assert share_response.status_code == 200
    public_skill_id = share_response.json()["public_skill_id"]

    public_skill = db_session.get(PublicSkill, public_skill_id)
    assert public_skill is not None
    assert json.loads(public_skill.tags) == ["/share-trigger", "共享触发"]

    public_skill.status = "approved"
    db_session.add(public_skill)
    db_session.commit()

    reader_setup = await create_user_and_login(
        client,
        db_session,
        "skilluser22b_reader",
        "skilluser22b_reader@example.com",
    )
    reader_token = reader_setup["token"]

    add_response = await client.post(
        f"/api/v1/public-skills/{public_skill_id}/add",
        headers={"Authorization": f"Bearer {reader_token}"},
    )
    assert add_response.status_code == 200

    list_response = await client.get(
        "/api/v1/skills?search=Shareable",
        headers={"Authorization": f"Bearer {reader_token}"},
    )
    assert list_response.status_code == 200
    listed_skill = list_response.json()["skills"][0]
    assert listed_skill["source"] == "added"
    assert listed_skill["triggers"] == ["/share-trigger", "共享触发"]


@pytest.mark.integration
async def test_share_skill_already_shared(client: AsyncClient, db_session):
    """Test sharing an already shared skill returns appropriate message."""
    setup = await create_user_and_login(client, db_session, "skilluser23", "skilluser23@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create already shared skill
    public_skill = PublicSkill(
        name="Already Shared",
        instructions="Already shared instructions",
        category="writing",
        tags="[]",
        source="community",
        status="pending",
        author_id=user.id,
    )
    db_session.add(public_skill)
    db_session.commit()

    skill = UserSkill(
        user_id=user.id,
        name="Already Shared",
        triggers=json.dumps(["shared"]),
        instructions="Already shared instructions",
        is_shared=True,
        shared_skill_id=public_skill.id,
    )
    db_session.add(skill)
    db_session.commit()

    response = await client.post(
        f"/api/v1/skills/{skill.id}/share",
        json={"category": "writing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["message"] == "Skill already shared"


@pytest.mark.integration
async def test_share_skill_not_found(client: AsyncClient, db_session):
    """Test sharing a nonexistent skill returns 404."""
    setup = await create_user_and_login(client, db_session, "skilluser24", "skilluser24@example.com")
    token = setup["token"]

    response = await client.post(
        "/api/v1/skills/00000000-0000-0000-0000-000000000000/share",
        json={"category": "writing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_share_skill_can_reshare_after_rejected(client: AsyncClient, db_session):
    """Test rejected shared skill can be resubmitted."""
    setup = await create_user_and_login(client, db_session, "skilluser24b", "skilluser24b@example.com")
    user = setup["user"]
    token = setup["token"]

    rejected_public = PublicSkill(
        name="Rejected Skill",
        instructions="Rejected instructions",
        category="writing",
        tags="[]",
        source="community",
        status="rejected",
        author_id=user.id,
    )
    db_session.add(rejected_public)
    db_session.commit()

    skill = UserSkill(
        user_id=user.id,
        name="Rejected Skill",
        triggers=json.dumps(["share"]),
        instructions="Rejected instructions",
        is_shared=True,
        shared_skill_id=rejected_public.id,
    )
    db_session.add(skill)
    db_session.commit()

    response = await client.post(
        f"/api/v1/skills/{skill.id}/share",
        json={"category": "writing"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["public_skill_id"] is not None
    assert data["public_skill_id"] != rejected_public.id

    db_session.refresh(skill)
    assert skill.is_shared is True
    assert skill.shared_skill_id == data["public_skill_id"]


# ==================== Batch Update Tests ====================


@pytest.mark.integration
async def test_batch_update_enable(client: AsyncClient, db_session):
    """Test batch enabling skills."""
    setup = await create_user_and_login(client, db_session, "skilluser25", "skilluser25@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create skills
    skill1 = UserSkill(
        user_id=user.id,
        name="Skill 1",
        triggers=json.dumps(["1"]),
        instructions="Instructions 1",
        is_active=False,
    )
    skill2 = UserSkill(
        user_id=user.id,
        name="Skill 2",
        triggers=json.dumps(["2"]),
        instructions="Instructions 2",
        is_active=False,
    )
    db_session.add(skill1)
    db_session.add(skill2)
    db_session.commit()

    response = await client.post(
        "/api/v1/skills/batch-update",
        json={"skill_ids": [skill1.id, skill2.id], "action": "enable"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["updated_count"] == 2

    # Verify skills are enabled
    db_session.refresh(skill1)
    db_session.refresh(skill2)
    assert skill1.is_active is True
    assert skill2.is_active is True


@pytest.mark.integration
async def test_batch_update_disable(client: AsyncClient, db_session):
    """Test batch disabling skills."""
    setup = await create_user_and_login(client, db_session, "skilluser26", "skilluser26@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create skills
    skill1 = UserSkill(
        user_id=user.id,
        name="Skill 1",
        triggers=json.dumps(["1"]),
        instructions="Instructions 1",
        is_active=True,
    )
    skill2 = UserSkill(
        user_id=user.id,
        name="Skill 2",
        triggers=json.dumps(["2"]),
        instructions="Instructions 2",
        is_active=True,
    )
    db_session.add(skill1)
    db_session.add(skill2)
    db_session.commit()

    response = await client.post(
        "/api/v1/skills/batch-update",
        json={"skill_ids": [skill1.id, skill2.id], "action": "disable"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["updated_count"] == 2

    # Verify skills are disabled
    db_session.refresh(skill1)
    db_session.refresh(skill2)
    assert skill1.is_active is False
    assert skill2.is_active is False


@pytest.mark.integration
async def test_batch_update_delete(client: AsyncClient, db_session):
    """Test batch deleting skills."""
    setup = await create_user_and_login(client, db_session, "skilluser27", "skilluser27@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create skills
    skill1 = UserSkill(
        user_id=user.id,
        name="Skill 1",
        triggers=json.dumps(["1"]),
        instructions="Instructions 1",
    )
    skill2 = UserSkill(
        user_id=user.id,
        name="Skill 2",
        triggers=json.dumps(["2"]),
        instructions="Instructions 2",
    )
    db_session.add(skill1)
    db_session.add(skill2)
    db_session.commit()

    skill1_id = skill1.id
    skill2_id = skill2.id

    response = await client.post(
        "/api/v1/skills/batch-update",
        json={"skill_ids": [skill1_id, skill2_id], "action": "delete"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["updated_count"] == 2

    # Verify skills are deleted
    assert db_session.get(UserSkill, skill1_id) is None
    assert db_session.get(UserSkill, skill2_id) is None


@pytest.mark.integration
async def test_batch_update_empty_list(client: AsyncClient, db_session):
    """Test batch update with empty skill list."""
    setup = await create_user_and_login(client, db_session, "skilluser28", "skilluser28@example.com")
    token = setup["token"]

    response = await client.post(
        "/api/v1/skills/batch-update",
        json={"skill_ids": [], "action": "enable"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["updated_count"] == 0


@pytest.mark.integration
async def test_batch_update_invalid_action(client: AsyncClient, db_session):
    """Test batch update with invalid action returns 400."""
    setup = await create_user_and_login(client, db_session, "skilluser29", "skilluser29@example.com")
    token = setup["token"]

    response = await client.post(
        "/api/v1/skills/batch-update",
        json={"skill_ids": ["some-id"], "action": "invalid"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


@pytest.mark.integration
async def test_batch_update_with_added_skills(client: AsyncClient, db_session):
    """Test batch update includes added public skills."""
    setup = await create_user_and_login(client, db_session, "skilluser30", "skilluser30@example.com")
    user = setup["user"]
    token = setup["token"]

    # Create and add public skill
    public_skill = PublicSkill(
        name="Public Skill",
        instructions="Public instructions",
        category="writing",
        tags="[]",
        source="official",
        status="approved",
    )
    db_session.add(public_skill)
    db_session.commit()

    added_skill = UserAddedSkill(
        user_id=user.id,
        public_skill_id=public_skill.id,
        is_active=True,
    )
    db_session.add(added_skill)
    db_session.commit()

    response = await client.post(
        "/api/v1/skills/batch-update",
        json={"skill_ids": [added_skill.id], "action": "disable"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["updated_count"] == 1

    # Verify added skill is disabled
    db_session.refresh(added_skill)
    assert added_skill.is_active is False


@pytest.mark.integration
async def test_batch_delete_added_skills_decrements_public_add_count(client: AsyncClient, db_session):
    """Test batch delete keeps public add_count in sync for added skills."""
    setup = await create_user_and_login(client, db_session, "skilluser31", "skilluser31@example.com")
    user = setup["user"]
    token = setup["token"]

    public_skill = PublicSkill(
        name="Popular Skill",
        instructions="Popular instructions",
        category="writing",
        tags="[]",
        source="official",
        status="approved",
        add_count=1,
    )
    db_session.add(public_skill)
    db_session.commit()

    added_skill = UserAddedSkill(
        user_id=user.id,
        public_skill_id=public_skill.id,
        is_active=True,
    )
    db_session.add(added_skill)
    db_session.commit()

    response = await client.post(
        "/api/v1/skills/batch-update",
        json={"skill_ids": [added_skill.id], "action": "delete"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["updated_count"] == 1

    assert db_session.get(UserAddedSkill, added_skill.id) is None
    db_session.refresh(public_skill)
    assert public_skill.add_count == 0


# ==================== Auth Required Tests ====================


@pytest.mark.integration
async def test_list_skills_without_auth(client: AsyncClient, db_session):
    """Test listing skills without authentication returns 401."""
    response = await client.get("/api/v1/skills")
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_my_skills_without_auth(client: AsyncClient, db_session):
    """Test getting my skills without authentication returns 401."""
    response = await client.get("/api/v1/skills/my-skills")
    assert response.status_code == 401


@pytest.mark.integration
async def test_get_skill_stats_without_auth(client: AsyncClient, db_session):
    """Test getting skill stats without authentication returns 401."""
    response = await client.get(
        "/api/v1/skills/stats/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_delete_skill_without_auth(client: AsyncClient, db_session):
    """Test deleting a skill without authentication returns 401."""
    response = await client.delete(
        "/api/v1/skills/00000000-0000-0000-0000-000000000000"
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_share_skill_without_auth(client: AsyncClient, db_session):
    """Test sharing a skill without authentication returns 401."""
    response = await client.post(
        "/api/v1/skills/00000000-0000-0000-0000-000000000000/share",
        json={"category": "writing"},
    )
    assert response.status_code == 401


@pytest.mark.integration
async def test_batch_update_without_auth(client: AsyncClient, db_session):
    """Test batch update without authentication returns 401."""
    response = await client.post(
        "/api/v1/skills/batch-update",
        json={"skill_ids": [], "action": "enable"},
    )
    assert response.status_code == 401
