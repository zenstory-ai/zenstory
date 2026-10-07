"""The admin circulation metric must agree with current spendable FIFO balances."""

from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from database import get_session
from main import app
from models import User
from models.points import PointsTransaction
from services.core.auth_service import create_access_token
from services.features.points_service import points_service


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["unmarked_expiry", "spent_expired_then_new", "active_spend"])
async def test_admin_circulation_matches_spendable_balances(tmp_path, monkeypatch, scenario):
    path = tmp_path / "points-circulation.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    now = datetime(2026, 4, 8, 9, tzinfo=UTC)
    admin_id, owner_id, other_id = [uuid4().hex for _ in range(3)]

    def request_session():
        with Session(engine) as session:
            assert not session.identity_map and session.expire_on_commit
            yield session

    def transaction(user_id, amount, days_ago, *, expired=False, expires_at=None):
        return PointsTransaction(
            user_id=user_id, amount=amount, balance_after=max(amount, 0),
            transaction_type="check_in" if amount > 0 else "redeem_pro",
            created_at=(now - timedelta(days=days_ago)).replace(tzinfo=None),
            expires_at=expires_at.replace(tzinfo=None) if expires_at else None,
            is_expired=expired,
        )

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            for user_id in (admin_id, owner_id, other_id):
                seed.add(User(id=user_id, username="circulation-" + user_id,
                              email=user_id + "@example.test", hashed_password="unused",
                              email_verified=True, is_active=True, is_superuser=user_id == admin_id))
            seed.flush()
            seed.add(transaction(other_id, 50, 0.5))
            if scenario == "unmarked_expiry":
                seed.add(transaction(owner_id, 100, 3, expires_at=now - timedelta(days=1)))
                expected_balances = [0, 50]
            elif scenario == "spent_expired_then_new":
                seed.add_all([
                    transaction(owner_id, 100, 3, expired=True, expires_at=now - timedelta(days=1)),
                    transaction(owner_id, -40, 2),
                    transaction(owner_id, 30, 0.5, expires_at=now + timedelta(days=10)),
                ])
                expected_balances = [30, 50]
            else:
                seed.add_all([
                    transaction(owner_id, 100, 3, expires_at=now + timedelta(days=10)),
                    transaction(owner_id, -40, 2),
                ])
                expected_balances = [60, 50]
            seed.commit()
        with monkeypatch.context() as settings:
            settings.setattr("api.admin.dashboard.utcnow", lambda: now)
            settings.setattr(import_module("services.features.points_service"), "utcnow", lambda: now)
            settings.setenv("INSPIRATIONS_ENABLED", "false")
            settings.setitem(app.dependency_overrides, get_session, request_session)
            with Session(engine) as read:
                # Literal wallet expectations precede the admin assertion, using the real ledger replay.
                balances = [points_service.get_balance(read, user_id)["available"]
                            for user_id in (owner_id, other_id)]
                assert balances == expected_balances
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                denied = await client.get("/api/admin/dashboard/stats", headers={
                    "Authorization": "Bearer " + create_access_token({"sub": owner_id}),
                })
                assert denied.status_code == 403
                response = await client.get("/api/admin/dashboard/stats", headers={
                    "Authorization": "Bearer " + create_access_token({"sub": admin_id}),
                })
                assert response.status_code == 200
                assert response.json()["total_points_in_circulation"] == sum(expected_balances)
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
