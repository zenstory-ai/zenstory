"""Admin write endpoints that previously skipped AdminAuditLog."""

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import User, UserFeedback
from models.subscription import AdminAuditLog
from services.core.auth_service import hash_password


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
