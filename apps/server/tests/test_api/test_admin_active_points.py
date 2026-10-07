"""Active points users are current spendable wallets, not historical grants."""

from datetime import UTC, datetime, timedelta
from importlib import import_module
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

from database import get_session
from main import app
from models import User
from models.points import PointsTransaction
from services.core.auth_service import create_access_token
from services.features.points_service import points_service


@pytest.mark.asyncio
@pytest.mark.parametrize("scenario", ["spent_all", "unmarked_expiry", "healthy"])
async def test_admin_active_points_users_matches_available_wallets(tmp_path, monkeypatch, scenario):
    path = tmp_path / "active-points.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    now = datetime(2026, 4, 8, 9, tzinfo=UTC)
    admin_id, owner_id, other_id = [uuid4().hex for _ in range(3)]

    def request_session():
        with Session(engine) as session:
            yield session

    def transaction(user, amount, days, expires_at=None):
        return PointsTransaction(
            user_id=user,
            amount=amount,
            balance_after=max(amount, 0),
            transaction_type="check_in" if amount > 0 else "redeem_pro",
            created_at=(now - timedelta(days=days)).replace(tzinfo=None),
            expires_at=expires_at.replace(tzinfo=None) if expires_at else None,
        )

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            for user in (admin_id, owner_id, other_id):
                seed.add(
                    User(
                        id=user,
                        username=user,
                        email=user + "@example.test",
                        hashed_password="unused",
                        is_active=True,
                        is_superuser=user == admin_id,
                    )
                )
            seed.flush()
            seed.add(transaction(other_id, 50, 1))
            seed.add(
                transaction(
                    owner_id, 100, 3, expires_at=now - timedelta(days=1) if scenario == "unmarked_expiry" else None
                )
            )
            if scenario == "spent_all":
                seed.add(transaction(owner_id, -100, 2))
            seed.commit()
        with monkeypatch.context() as context:
            context.setattr(import_module("services.features.points_service"), "utcnow", lambda: now)
            context.setitem(app.dependency_overrides, get_session, request_session)
            with Session(engine) as read:
                balances = [points_service.get_balance(read, user)["available"] for user in (owner_id, other_id)]
                assert balances == ([100, 50] if scenario == "healthy" else [0, 50])
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                denied = await client.get(
                    "/api/admin/points/stats",
                    headers={
                        "Authorization": "Bearer " + create_access_token({"sub": owner_id}),
                    },
                )
                assert denied.status_code == 403
                response = await client.get(
                    "/api/admin/points/stats",
                    headers={
                        "Authorization": "Bearer " + create_access_token({"sub": admin_id}),
                    },
                )
                assert response.status_code == 200
                assert response.json()["total_points_issued"] == 150
                assert response.json()["total_points_spent"] == (100 if scenario == "spent_all" else 0)
                assert response.json()["total_points_expired"] == 0  # Existing marked-expiry metric unchanged.
                assert response.json()["active_users_with_points"] == (2 if scenario == "healthy" else 1)
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
