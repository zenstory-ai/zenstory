"""Admin write endpoints that previously skipped AdminAuditLog."""

import json

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import File, Inspiration, Project, User, UserFeedback
from models.subscription import AdminAuditLog
from services.core.auth_service import hash_password


@pytest.fixture(autouse=True)
def _enable_inspirations(monkeypatch):
    monkeypatch.setenv("INSPIRATIONS_ENABLED", "true")


def _user(db_session: Session, username: str, *, is_superuser: bool = False) -> User:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
        is_superuser=is_superuser,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _admin_headers(client: AsyncClient, db_session: Session, username: str) -> tuple[User, dict]:
    admin = _user(db_session, username, is_superuser=True)
    response = await client.post("/api/auth/login", data={"username": username, "password": "password123"})
    assert response.status_code == 200
    return admin, {"Authorization": f"Bearer {response.json()['access_token']}"}


def _audit_rows(db_session: Session, action: str) -> list[AdminAuditLog]:
    return list(db_session.exec(select(AdminAuditLog).where(AdminAuditLog.action == action)).all())


def _inspiration(db_session: Session, author_id: str, status: str = "pending") -> Inspiration:
    inspiration = Inspiration(
        name="Audit Target",
        project_type="novel",
        tags="[]",
        snapshot_data=json.dumps({"project_type": "novel", "files": []}),
        source="community",
        status=status,
        author_id=author_id,
    )
    db_session.add(inspiration)
    db_session.commit()
    db_session.refresh(inspiration)
    return inspiration


@pytest.mark.integration
async def test_publishing_another_users_project_is_audited(client: AsyncClient, db_session: Session):
    admin, headers = await _admin_headers(client, db_session, "audit_admin_create")
    author = _user(db_session, "audit_project_owner")
    project = Project(name="Someone else's novel", owner_id=author.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    db_session.add(File(project_id=project.id, title="第一章", content="正文", file_type="draft", order=0))
    db_session.commit()

    response = await client.post(
        "/api/admin/inspirations",
        headers=headers,
        json={"project_id": project.id, "source": "official", "name": "Official pick"},
    )

    assert response.status_code == 201
    rows = _audit_rows(db_session, "create_inspiration")
    assert len(rows) == 1
    row = rows[0]
    assert row.admin_user_id == admin.id
    assert row.resource_id == response.json()["id"]
    assert row.new_value["project_id"] == project.id
    assert row.new_value["project_owner_id"] == author.id
    assert row.new_value["status"] == "approved"
    assert row.new_value["file_count"] == 1


@pytest.mark.integration
async def test_inspiration_update_review_and_delete_are_audited(client: AsyncClient, db_session: Session):
    admin, headers = await _admin_headers(client, db_session, "audit_admin_lifecycle")
    inspiration = _inspiration(db_session, author_id=admin.id)

    patched = await client.patch(
        f"/api/admin/inspirations/{inspiration.id}", headers=headers, json={"is_featured": True}
    )
    assert patched.status_code == 200
    update_row = _audit_rows(db_session, "update_inspiration")[0]
    assert update_row.old_value["is_featured"] is False
    assert update_row.new_value["is_featured"] is True

    reviewed = await client.post(
        f"/api/admin/inspirations/{inspiration.id}/review",
        headers=headers,
        json={"approve": False, "rejection_reason": "duplicate"},
    )
    assert reviewed.status_code == 200
    review_row = _audit_rows(db_session, "reject_inspiration")[0]
    assert review_row.old_value["status"] == "pending"
    assert review_row.new_value["status"] == "rejected"
    assert review_row.new_value["rejection_reason"] == "duplicate"

    deleted = await client.delete(f"/api/admin/inspirations/{inspiration.id}", headers=headers)
    assert deleted.status_code == 200
    delete_row = _audit_rows(db_session, "delete_inspiration")[0]
    assert delete_row.resource_id == inspiration.id
    assert delete_row.old_value["name"] == "Audit Target"


@pytest.mark.integration
async def test_feedback_status_change_is_audited(client: AsyncClient, db_session: Session):
    admin, headers = await _admin_headers(client, db_session, "audit_admin_feedback")
    reporter = _user(db_session, "audit_feedback_reporter")
    feedback = UserFeedback(user_id=reporter.id, source_page="editor", issue_text="bug", status="open")
    db_session.add(feedback)
    db_session.commit()
    db_session.refresh(feedback)

    response = await client.patch(
        f"/api/admin/feedback/{feedback.id}/status", headers=headers, json={"status": "resolved"}
    )

    assert response.status_code == 200
    row = _audit_rows(db_session, "update_feedback_status")[0]
    assert row.admin_user_id == admin.id
    assert row.old_value == {"status": "open"}
    assert row.new_value == {"status": "resolved"}


@pytest.mark.integration
async def test_admin_invite_code_creation_is_audited(client: AsyncClient, db_session: Session):
    admin, headers = await _admin_headers(client, db_session, "audit_admin_invites")

    response = await client.post("/api/admin/invites", headers=headers)

    assert response.status_code == 201
    row = _audit_rows(db_session, "create_invite_code")[0]
    assert row.admin_user_id == admin.id
    assert row.resource_id == response.json()["id"]
    assert row.new_value["code"] == response.json()["code"]
    assert row.new_value["ignore_max_limit"] is True
