"""Verification account policy and single-SQL-transaction failure boundaries."""
from datetime import timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, select

from api import verification
from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from main import app
from models import RefreshTokenRecord, User


def seed(session, *, active=True):
    suffix = uuid4().hex
    user = User(username=f"verification-{suffix}", email=f"{suffix}@example.com",
                hashed_password="unused", is_active=active, email_verified=False)
    session.add(user)
    session.commit()
    return user.id, user.email


async def request(email):
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                           base_url="http://test") as client:
        return await client.post("/api/auth/verify-email", json={"email": email, "code": "123456"})


@pytest.mark.asyncio
async def test_disabled_account_rejects_before_consuming_code(client, db_session, monkeypatch):
    user_id, email = seed(db_session, active=False)
    code = AsyncMock(return_value=(True, None))
    monkeypatch.setattr(verification, "verify_code", code)
    response = await request(email)
    with Session(db_session.bind) as reader:
        verified = reader.get(User, user_id).email_verified
        records = reader.exec(select(RefreshTokenRecord).where(RefreshTokenRecord.user_id == user_id)).all()
    assert response.status_code == 400
    assert response.json()["error_code"] == ErrorCode.AUTH_INACTIVE_USER
    assert code.await_count == 0
    assert verified is False and records == []


@pytest.mark.asyncio
async def test_refresh_insert_failure_does_not_commit_verification(client, db_session, monkeypatch):
    user_id, email = seed(db_session)
    collision = "owned-existing-jti"
    db_session.add(RefreshTokenRecord(user_id=user_id, token_jti=collision,
                                    family_id="old-family", expires_at=utcnow() + timedelta(days=1)))
    db_session.commit()
    monkeypatch.setattr(verification, "verify_code", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(verification, "generate_token_jti", lambda: collision)
    response = await request(email)
    assert response.status_code == 409  # Existing global real UNIQUE conflict contract.
    assert response.json()["error_code"] == ErrorCode.RESOURCE_CONFLICT
    db_session.rollback()
    with Session(db_session.bind) as reader:
        user = reader.get(User, user_id)
        records = reader.exec(select(RefreshTokenRecord).where(RefreshTokenRecord.user_id == user_id)).all()
        assert len(records) == 1 and records[0].family_id == "old-family"
        assert user.email_verified is False, "token persistence failed but verification was already committed"


@pytest.mark.asyncio
async def test_active_success_persists_verification_and_refresh_together(client, db_session, monkeypatch):
    user_id, email = seed(db_session)
    monkeypatch.setattr(verification, "verify_code", AsyncMock(return_value=(True, None)))
    response = await request(email)
    assert response.status_code == 200 and response.json()["user"]["email_verified"] is True
    with Session(db_session.bind) as reader:
        assert reader.get(User, user_id).email_verified is True
        records = reader.exec(select(RefreshTokenRecord).where(RefreshTokenRecord.user_id == user_id)).all()
        assert len(records) == 1 and records[0].revoked_at is None
