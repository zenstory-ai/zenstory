"""
Tests for Public Skills API endpoints.

Tests public skill discovery and collection management:
- GET /public-skills/categories - Get category list
- GET /public-skills - List public skills with filtering
- GET /public-skills/{skill_id} - Get skill details
- POST /public-skills/{skill_id}/add - Add skill to user's collection
- DELETE /public-skills/{skill_id}/remove - Remove skill from user's collection
"""

import json

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import PublicSkill, User, UserAddedSkill
from services.core.auth_service import hash_password


# ============================================
# Test Fixtures
# ============================================


async def create_test_user(db_session: Session) -> tuple[User, str]:
    """Create a test user and return (user, access_token)."""
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

    return user


async def get_auth_headers(client: AsyncClient) -> dict[str, str]:
    """Login and return authorization headers."""
    login_response = await client.post(
        "/api/auth/login",
        data={"username": "testuser", "password": "testpassword123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def create_test_skill(
    db_session: Session,
    name: str = "Test Skill",
    category: str = "writing",
    source: str = "official",
    status: str = "approved",
    add_count: int = 0,
    tags: list[str] | None = None,
    author_id: str | None = None,
) -> PublicSkill:
    """Create a test public skill."""
    skill = PublicSkill(
        name=name,
        description=f"Description for {name}",
        instructions=f"Instructions for {name}",
        category=category,
        tags=json.dumps(tags or ["test"]),
        source=source,
        status=status,
        add_count=add_count,
        author_id=author_id,
    )
    db_session.add(skill)
    db_session.commit()
    db_session.refresh(skill)
    return skill


# ============================================
# GET /public-skills/categories Tests
# ============================================


@pytest.mark.integration
async def test_get_categories_empty(client: AsyncClient, db_session: Session):
    """Test getting categories when no skills exist."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    response = await client.get("/api/v1/public-skills/categories", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert "categories" in data
    assert data["categories"] == []


@pytest.mark.integration
async def test_get_categories_with_skills(client: AsyncClient, db_session: Session):
    """Test getting categories with skills grouped by count."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    # Create skills in different categories
    create_test_skill(db_session, name="Skill 1", category="writing")
    create_test_skill(db_session, name="Skill 2", category="writing")
    create_test_skill(db_session, name="Skill 3", category="character")
    create_test_skill(db_session, name="Skill 4", category="plot")

    response = await client.get("/api/v1/public-skills/categories", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert "categories" in data
    categories = data["categories"]

    # Should be ordered by count descending
    assert len(categories) == 3
    assert categories[0]["name"] == "writing"
    assert categories[0]["count"] == 2
    # character and plot both have count 1, order may vary
    assert {categories[1]["name"], categories[2]["name"]} == {"character", "plot"}
    assert categories[1]["count"] == 1
    assert categories[2]["count"] == 1


@pytest.mark.integration
async def test_get_categories_only_approved_skills(client: AsyncClient, db_session: Session):
    """Test that categories only include approved skills."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    # Create skills with different statuses
    create_test_skill(db_session, name="Approved Skill", category="writing", status="approved")
    create_test_skill(db_session, name="Pending Skill", category="writing", status="pending")
    create_test_skill(db_session, name="Rejected Skill", category="writing", status="rejected")

    response = await client.get("/api/v1/public-skills/categories", headers=headers)

    assert response.status_code == 200
    data = response.json()
    categories = data["categories"]

    # Only approved skill should be counted
    assert len(categories) == 1
    assert categories[0]["name"] == "writing"
    assert categories[0]["count"] == 1


@pytest.mark.integration
async def test_get_categories_requires_auth(client: AsyncClient, db_session: Session):
    """Test that getting categories requires authentication."""
    response = await client.get("/api/v1/public-skills/categories")
    assert response.status_code == 401


# ============================================
# GET /public-skills Tests
# ============================================


@pytest.mark.integration
async def test_list_public_skills_empty(client: AsyncClient, db_session: Session):
    """Test listing skills when none exist."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    response = await client.get("/api/v1/public-skills", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["skills"] == []
    assert data["total"] == 0
    assert data["page"] == 1
    assert data["page_size"] == 20


@pytest.mark.integration
async def test_list_public_skills_with_data(client: AsyncClient, db_session: Session):
    """Test listing skills with data."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    # Create multiple skills
    skill1 = create_test_skill(db_session, name="Writing Helper", category="writing", add_count=10)
    skill2 = create_test_skill(db_session, name="Character Builder", category="character", add_count=5)

    response = await client.get("/api/v1/public-skills", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["skills"]) == 2

    # Should be ordered by add_count descending
    assert data["skills"][0]["name"] == "Writing Helper"
    assert data["skills"][0]["add_count"] == 10
    assert data["skills"][1]["name"] == "Character Builder"
    assert data["skills"][1]["add_count"] == 5


@pytest.mark.integration
async def test_list_public_skills_pagination(client: AsyncClient, db_session: Session):
    """Test pagination of skills list."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    # Create 25 skills
    for i in range(25):
        create_test_skill(db_session, name=f"Skill {i:02d}", category="writing")

    # Test first page
    response = await client.get("/api/v1/public-skills?page=1&page_size=10", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 25
    assert len(data["skills"]) == 10
    assert data["page"] == 1
    assert data["page_size"] == 10

    # Test second page
    response = await client.get("/api/v1/public-skills?page=2&page_size=10", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["skills"]) == 10
    assert data["page"] == 2

    # Test third page (only 5 items)
    response = await client.get("/api/v1/public-skills?page=3&page_size=10", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert len(data["skills"]) == 5
    assert data["page"] == 3


@pytest.mark.integration
async def test_list_public_skills_filter_by_category(client: AsyncClient, db_session: Session):
    """Test filtering skills by category."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    create_test_skill(db_session, name="Writing Skill 1", category="writing")
    create_test_skill(db_session, name="Writing Skill 2", category="writing")
    create_test_skill(db_session, name="Character Skill", category="character")
    create_test_skill(db_session, name="Plot Skill", category="plot")

    response = await client.get("/api/v1/public-skills?category=writing", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["skills"]) == 2
    assert all(s["category"] == "writing" for s in data["skills"])


@pytest.mark.integration
async def test_list_public_skills_filter_by_source(client: AsyncClient, db_session: Session):
    """Test filtering skills by source."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    create_test_skill(db_session, name="Official Skill 1", source="official")
    create_test_skill(db_session, name="Official Skill 2", source="official")
    create_test_skill(db_session, name="Community Skill", source="community")

    response = await client.get("/api/v1/public-skills?source=official", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["skills"]) == 2
    assert all(s["source"] == "official" for s in data["skills"])


@pytest.mark.integration
async def test_list_public_skills_search_by_name(client: AsyncClient, db_session: Session):
    """Test searching skills by name."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    create_test_skill(db_session, name="Character Development Helper")
    create_test_skill(db_session, name="World Building Guide")
    create_test_skill(db_session, name="Character Name Generator")

    response = await client.get("/api/v1/public-skills?search=character", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["skills"]) == 2
    names = [s["name"] for s in data["skills"]]
    assert "Character Development Helper" in names
    assert "Character Name Generator" in names
    assert "World Building Guide" not in names


@pytest.mark.integration
async def test_list_public_skills_search_by_description(client: AsyncClient, db_session: Session):
    """Test searching skills by description."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill1 = create_test_skill(db_session, name="Skill A")
    skill1.description = "Helps with creative writing prompts"
    db_session.add(skill1)

    skill2 = create_test_skill(db_session, name="Skill B")
    skill2.description = "Builds character profiles"
    db_session.add(skill2)

    skill3 = create_test_skill(db_session, name="Skill C")
    skill3.description = "Generates plot ideas"
    db_session.add(skill3)

    db_session.commit()

    response = await client.get("/api/v1/public-skills?search=creative", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["skills"][0]["name"] == "Skill A"


@pytest.mark.integration
async def test_list_public_skills_combined_filters(client: AsyncClient, db_session: Session):
    """Test combining multiple filters."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    create_test_skill(
        db_session, name="Writing Helper", category="writing", source="official"
    )
    create_test_skill(
        db_session, name="Writing Guide", category="writing", source="community"
    )
    create_test_skill(
        db_session, name="Character Helper", category="character", source="official"
    )

    response = await client.get(
        "/api/v1/public-skills?category=writing&source=official", headers=headers
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["skills"][0]["name"] == "Writing Helper"


@pytest.mark.integration
async def test_list_public_skills_shows_is_added(client: AsyncClient, db_session: Session):
    """Test that is_added flag is correctly set for user's added skills."""
    user = await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill1 = create_test_skill(db_session, name="Added Skill")
    skill2 = create_test_skill(db_session, name="Not Added Skill")

    # Add skill1 to user's collection
    added = UserAddedSkill(user_id=user.id, public_skill_id=skill1.id)
    db_session.add(added)
    db_session.commit()

    response = await client.get("/api/v1/public-skills", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2

    # Find each skill and check is_added
    added_skill = next(s for s in data["skills"] if s["name"] == "Added Skill")
    not_added_skill = next(s for s in data["skills"] if s["name"] == "Not Added Skill")

    assert added_skill["is_added"] is True
    assert not_added_skill["is_added"] is False


@pytest.mark.integration
async def test_list_public_skills_inactive_added_link_is_not_marked_added(client: AsyncClient, db_session: Session):
    """Test inactive added records are not treated as currently added."""
    user = await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Inactive Link Skill")
    inactive_link = UserAddedSkill(
        user_id=user.id,
        public_skill_id=skill.id,
        is_active=False,
    )
    db_session.add(inactive_link)
    db_session.commit()

    response = await client.get("/api/v1/public-skills", headers=headers)
    assert response.status_code == 200
    data = response.json()
    listed = next(s for s in data["skills"] if s["id"] == skill.id)
    assert listed["is_added"] is False


@pytest.mark.integration
async def test_list_public_skills_only_approved(client: AsyncClient, db_session: Session):
    """Test that only approved skills are listed."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    create_test_skill(db_session, name="Approved Skill", status="approved")
    create_test_skill(db_session, name="Pending Skill", status="pending")
    create_test_skill(db_session, name="Rejected Skill", status="rejected")

    response = await client.get("/api/v1/public-skills", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["skills"][0]["name"] == "Approved Skill"


@pytest.mark.integration
async def test_list_public_skills_requires_auth(client: AsyncClient, db_session: Session):
    """Test that listing skills requires authentication."""
    response = await client.get("/api/v1/public-skills")
    assert response.status_code == 401


# ============================================
# GET /public-skills/{skill_id} Tests
# ============================================


@pytest.mark.integration
async def test_get_skill_by_id_success(client: AsyncClient, db_session: Session):
    """Test getting a skill by ID."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Test Skill", tags=["writing", "helper"])

    response = await client.get(f"/api/v1/public-skills/{skill.id}", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == skill.id
    assert data["name"] == "Test Skill"
    assert data["description"] == "Description for Test Skill"
    assert data["instructions"] == "Instructions for Test Skill"
    assert data["category"] == "writing"
    assert data["tags"] == ["writing", "helper"]
    assert data["source"] == "official"
    assert data["status"] == "approved"
    assert data["is_added"] is False


@pytest.mark.integration
async def test_get_skill_by_id_not_found(client: AsyncClient, db_session: Session):
    """Test getting a non-existent skill."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    response = await client.get(
        "/api/v1/public-skills/00000000-0000-0000-0000-000000000000", headers=headers
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_get_skill_by_id_pending_skill(client: AsyncClient, db_session: Session):
    """Test getting a pending skill returns 404."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Pending Skill", status="pending")

    response = await client.get(f"/api/v1/public-skills/{skill.id}", headers=headers)

    assert response.status_code == 404


@pytest.mark.integration
async def test_get_skill_by_id_shows_is_added(client: AsyncClient, db_session: Session):
    """Test that is_added flag is correctly set when getting a skill."""
    user = await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Test Skill")

    # Add skill to user's collection
    added = UserAddedSkill(user_id=user.id, public_skill_id=skill.id)
    db_session.add(added)
    db_session.commit()

    response = await client.get(f"/api/v1/public-skills/{skill.id}", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["is_added"] is True


@pytest.mark.integration
async def test_get_skill_by_id_inactive_added_link_is_not_marked_added(client: AsyncClient, db_session: Session):
    """Test public skill detail treats inactive added link as not added."""
    user = await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Inactive Detail Skill")
    inactive_link = UserAddedSkill(user_id=user.id, public_skill_id=skill.id, is_active=False)
    db_session.add(inactive_link)
    db_session.commit()

    response = await client.get(f"/api/v1/public-skills/{skill.id}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["is_added"] is False


@pytest.mark.integration
async def test_get_skill_requires_auth(client: AsyncClient, db_session: Session):
    """Test that getting a skill requires authentication."""
    skill = create_test_skill(db_session, name="Test Skill")

    response = await client.get(f"/api/v1/public-skills/{skill.id}")
    assert response.status_code == 401


# ============================================
# POST /public-skills/{skill_id}/add Tests
# ============================================


@pytest.mark.integration
async def test_add_skill_success(client: AsyncClient, db_session: Session):
    """Test successfully adding a skill to user's collection."""
    user = await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Test Skill", add_count=5)

    response = await client.post(f"/api/v1/public-skills/{skill.id}/add", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["message"] == "Skill added successfully"
    assert data["added_skill_id"] is not None

    # Verify skill was added
    added = db_session.exec(
        UserAddedSkill.__table__.select().where(
            UserAddedSkill.user_id == user.id,
            UserAddedSkill.public_skill_id == skill.id,
        )
    ).first()
    assert added is not None

    # Verify add_count was incremented
    db_session.refresh(skill)
    assert skill.add_count == 6


@pytest.mark.integration
async def test_add_skill_already_added(client: AsyncClient, db_session: Session):
    """Test adding a skill that's already in user's collection."""
    user = await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Test Skill", add_count=5)

    # Add skill first time
    await client.post(f"/api/v1/public-skills/{skill.id}/add", headers=headers)

    # Try to add again
    response = await client.post(f"/api/v1/public-skills/{skill.id}/add", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert data["message"] == "Skill already added"
    assert data["added_skill_id"] is not None

    # Verify add_count was NOT incremented twice
    db_session.refresh(skill)
    assert skill.add_count == 6  # Only incremented once


@pytest.mark.integration
async def test_add_skill_reactivates_inactive_link(client: AsyncClient, db_session: Session):
    """Test add endpoint re-activates an existing inactive link."""
    user = await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Reactivation Skill", add_count=5)
    existing = UserAddedSkill(
        user_id=user.id,
        public_skill_id=skill.id,
        is_active=False,
    )
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)

    response = await client.post(f"/api/v1/public-skills/{skill.id}/add", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["message"] == "Skill re-enabled successfully"
    assert data["added_skill_id"] == existing.id

    db_session.refresh(existing)
    assert existing.is_active is True
    db_session.refresh(skill)
    assert skill.add_count == 5


@pytest.mark.integration
async def test_add_skill_not_found(client: AsyncClient, db_session: Session):
    """Test adding a non-existent skill."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    response = await client.post(
        "/api/v1/public-skills/00000000-0000-0000-0000-000000000000/add", headers=headers
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_add_skill_pending_skill(client: AsyncClient, db_session: Session):
    """Test adding a pending skill returns 404."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Pending Skill", status="pending")

    response = await client.post(f"/api/v1/public-skills/{skill.id}/add", headers=headers)

    assert response.status_code == 404


@pytest.mark.integration
async def test_add_skill_requires_auth(client: AsyncClient, db_session: Session):
    """Test that adding a skill requires authentication."""
    skill = create_test_skill(db_session, name="Test Skill")

    response = await client.post(f"/api/v1/public-skills/{skill.id}/add")
    assert response.status_code == 401


# ============================================
# DELETE /public-skills/{skill_id}/remove Tests
# ============================================


@pytest.mark.integration
async def test_remove_skill_success(client: AsyncClient, db_session: Session):
    """Test successfully removing a skill from user's collection."""
    user = await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Test Skill", add_count=5)

    # Add skill first
    added = UserAddedSkill(user_id=user.id, public_skill_id=skill.id)
    db_session.add(added)
    db_session.commit()

    response = await client.delete(f"/api/v1/public-skills/{skill.id}/remove", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["message"] == "Skill removed successfully"

    # Verify skill was removed
    added = db_session.exec(
        UserAddedSkill.__table__.select().where(
            UserAddedSkill.user_id == user.id,
            UserAddedSkill.public_skill_id == skill.id,
        )
    ).first()
    assert added is None

    # Verify add_count was decremented
    db_session.refresh(skill)
    assert skill.add_count == 4


@pytest.mark.integration
async def test_remove_skill_not_in_collection(client: AsyncClient, db_session: Session):
    """Test removing a skill that's not in user's collection."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Test Skill")

    response = await client.delete(f"/api/v1/public-skills/{skill.id}/remove", headers=headers)

    assert response.status_code == 404


@pytest.mark.integration
async def test_remove_skill_decrements_to_zero(client: AsyncClient, db_session: Session):
    """Test that add_count doesn't go below zero."""
    user = await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(db_session, name="Test Skill", add_count=1)

    # Add skill
    added = UserAddedSkill(user_id=user.id, public_skill_id=skill.id)
    db_session.add(added)
    db_session.commit()

    # Remove skill
    response = await client.delete(f"/api/v1/public-skills/{skill.id}/remove", headers=headers)

    assert response.status_code == 200

    # Verify add_count is 0, not -1
    db_session.refresh(skill)
    assert skill.add_count == 0


@pytest.mark.integration
async def test_remove_skill_requires_auth(client: AsyncClient, db_session: Session):
    """Test that removing a skill requires authentication."""
    skill = create_test_skill(db_session, name="Test Skill")

    response = await client.delete(f"/api/v1/public-skills/{skill.id}/remove")
    assert response.status_code == 401


# ============================================
# Edge Cases and Error Handling
# ============================================


@pytest.mark.integration
async def test_list_skills_with_invalid_page(client: AsyncClient, db_session: Session):
    """Test that invalid page number returns validation error."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    response = await client.get("/api/v1/public-skills?page=0", headers=headers)
    assert response.status_code == 422

    response = await client.get("/api/v1/public-skills?page=-1", headers=headers)
    assert response.status_code == 422


@pytest.mark.integration
async def test_list_skills_with_invalid_page_size(client: AsyncClient, db_session: Session):
    """Test that invalid page_size returns validation error."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    response = await client.get("/api/v1/public-skills?page_size=0", headers=headers)
    assert response.status_code == 422

    response = await client.get("/api/v1/public-skills?page_size=101", headers=headers)
    assert response.status_code == 422


@pytest.mark.integration
async def test_list_skills_case_insensitive_search(client: AsyncClient, db_session: Session):
    """Test that search is case-insensitive."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    create_test_skill(db_session, name="CHARACTER Helper")

    response = await client.get("/api/v1/public-skills?search=character", headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["skills"][0]["name"] == "CHARACTER Helper"


@pytest.mark.integration
async def test_skill_response_includes_all_fields(client: AsyncClient, db_session: Session):
    """Test that skill response includes all expected fields."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(
        db_session,
        name="Complete Skill",
        category="worldbuilding",
        tags=["world", "building"],
        source="community",
    )

    response = await client.get(f"/api/v1/public-skills/{skill.id}", headers=headers)

    assert response.status_code == 200
    data = response.json()

    # Check all expected fields are present
    expected_fields = [
        "id",
        "name",
        "description",
        "instructions",
        "category",
        "tags",
        "source",
        "author_id",
        "author_name",
        "status",
        "add_count",
        "created_at",
        "is_added",
    ]
    for field in expected_fields:
        assert field in data, f"Missing field: {field}"


@pytest.mark.integration
async def test_public_skill_response_handles_invalid_tags_json(client: AsyncClient, db_session: Session):
    """Test malformed tags JSON falls back to empty tags list."""
    await create_test_user(db_session)
    headers = await get_auth_headers(client)

    broken = create_test_skill(db_session, name="Broken Tags Skill")
    broken.tags = "{invalid-json"
    db_session.add(broken)
    db_session.commit()

    list_resp = await client.get("/api/v1/public-skills", headers=headers)
    assert list_resp.status_code == 200
    list_data = list_resp.json()
    listed = next(s for s in list_data["skills"] if s["id"] == broken.id)
    assert listed["tags"] == []

    detail_resp = await client.get(f"/api/v1/public-skills/{broken.id}", headers=headers)
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()
    assert detail_data["tags"] == []


@pytest.mark.integration
async def test_list_public_skills_includes_author_name(client: AsyncClient, db_session: Session):
    """Test public skill list returns author_name for community skills."""
    author = await create_test_user(db_session)
    headers = await get_auth_headers(client)

    create_test_skill(
        db_session,
        name="Community Skill With Author",
        source="community",
        author_id=author.id,
    )

    response = await client.get("/api/v1/public-skills", headers=headers)
    assert response.status_code == 200
    data = response.json()

    skill = next(s for s in data["skills"] if s["name"] == "Community Skill With Author")
    assert skill["author_name"] == author.username


@pytest.mark.integration
async def test_get_public_skill_includes_author_name(client: AsyncClient, db_session: Session):
    """Test public skill detail returns author_name for community skills."""
    author = await create_test_user(db_session)
    headers = await get_auth_headers(client)

    skill = create_test_skill(
        db_session,
        name="Detail Author Skill",
        source="community",
        author_id=author.id,
    )

    response = await client.get(f"/api/v1/public-skills/{skill.id}", headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["author_name"] == author.username


@pytest.mark.integration
async def test_list_returns_instruction_preview_and_detail_returns_full_text(
    client: AsyncClient, db_session: Session
):
    """列表只回传正文预览（每页 20 个技能 × 5 万字太重），详情接口回传完整正文。"""
    from api.public_skills import LIST_INSTRUCTIONS_PREVIEW_CHARS

    await create_test_user(db_session)
    headers = await get_auth_headers(client)
    long_skill = create_test_skill(db_session, name="Long Skill")
    long_skill.instructions = "长" * (LIST_INSTRUCTIONS_PREVIEW_CHARS + 100)
    db_session.add(long_skill)
    db_session.commit()
    short_skill = create_test_skill(db_session, name="Short Skill")

    listed = await client.get("/api/v1/public-skills", headers=headers)
    detail = await client.get(f"/api/v1/public-skills/{long_skill.id}", headers=headers)

    by_id = {item["id"]: item for item in listed.json()["skills"]}
    assert by_id[long_skill.id]["instructions"] == "长" * LIST_INSTRUCTIONS_PREVIEW_CHARS
    assert by_id[long_skill.id]["instructions_truncated"] is True
    assert by_id[short_skill.id]["instructions"] == "Instructions for Short Skill"
    assert by_id[short_skill.id]["instructions_truncated"] is False
    assert detail.json()["instructions"] == long_skill.instructions
    assert detail.json()["instructions_truncated"] is False
