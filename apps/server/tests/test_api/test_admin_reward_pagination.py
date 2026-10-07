"""Stable admin history pages and bounded referral owner enrichment."""

import re
from datetime import datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from database import get_session
from main import app
from models import User
from models.points import CheckInRecord, PointsTransaction
from models.referral import InviteCode, UserReward
from services.core.auth_service import create_access_token


@pytest.fixture
def history_storage(tmp_path, monkeypatch):
    path = tmp_path / "admin-history.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    tag = uuid4().hex[:16]
    admin = tag + "-admin"
    ids = [tag + "-" + suffix for suffix in ("a", "f", "b", "e", "c", "d")]
    timestamp = datetime(2026, 4, 8, 9)
    sql = []

    def record(_conn, _cursor, statement, _params, _ctx, _many):
        sql.append(statement)

    def request_session():
        with Session(engine) as session:
            yield session

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            for user in [admin, *ids]:
                seed.add(
                    User(
                        id=user,
                        username=user,
                        email=user + "@example.test",
                        hashed_password="unused",
                        is_active=True,
                        is_superuser=user == admin,
                    )
                )
            seed.commit()
            for key in ids:
                seed.add(InviteCode(id=key, code=key, owner_id=key, created_at=timestamp))
                seed.add(
                    UserReward(
                        id=key, user_id=key, reward_type="points", amount=10, source="referral", created_at=timestamp
                    )
                )
                seed.add(
                    CheckInRecord(
                        id=key, user_id=key, check_in_date=timestamp.date(), points_earned=10, created_at=timestamp
                    )
                )
                seed.add(
                    PointsTransaction(
                        id=key,
                        user_id=ids[0],
                        amount=10,
                        balance_after=10,
                        transaction_type="check_in",
                        created_at=timestamp,
                    )
                )
            seed.commit()
        event.listen(engine, "before_cursor_execute", record)
        with monkeypatch.context() as context:
            context.setitem(app.dependency_overrides, get_session, request_session)
            yield engine, admin, ids, sql
    finally:
        event.remove(engine, "before_cursor_execute", record)
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["invites", "referrals/rewards", "check-in/records", "points"])
async def test_admin_history_ties_have_stable_pages(history_storage, resource):
    _engine, admin, ids, _sql = history_storage
    route = "/api/admin/" + (f"points/{ids[0]}/transactions" if resource == "points" else resource)
    headers = {"Authorization": "Bearer " + create_access_token({"sub": admin})}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        denied = await client.get(route, headers={"Authorization": "Bearer " + create_access_token({"sub": ids[0]})})
        assert denied.status_code == 403
        observed = []
        for page in range(1, 4):
            params = {"page": page, "page_size": 2}
            response = await client.get(route, headers=headers, params=params)
            repeat = await client.get(route, headers=headers, params=params)
            assert response.status_code == repeat.status_code == 200
            payload = response.json()
            assert payload == repeat.json()
            assert payload["total"] == 6
            observed.extend(item["id"] for item in payload["items"])
        assert observed == sorted(ids, reverse=True)
        assert len(set(observed)) == 6


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["invites", "referrals/rewards"])
async def test_admin_referral_pages_do_not_load_each_user(history_storage, resource):
    _engine, admin, ids, sql = history_storage
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        sql.clear()
        response = await client.get(
            "/api/admin/" + resource,
            headers={"Authorization": "Bearer " + create_access_token({"sub": admin})},
            params={"page_size": 100},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 6
        field = "owner_name" if resource == "invites" else "username"
        assert {item["id"]: item[field] for item in payload["items"]} == {key: key for key in ids}
        if resource == "referrals/rewards":
            assert all(item["referral_id"] is None for item in payload["items"])
        user_reads = [
            s for s in sql if s.lstrip().lower().startswith("select") and re.search(r'\bfrom\s+"?user"?\b', s.lower())
        ]
        assert len(user_reads) <= 1, user_reads  # Only the existing auth lookup; never one per listed row.


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["invites", "referrals/rewards"])
async def test_admin_referral_enrichment_preserves_empty_and_missing_owners(history_storage, resource):
    engine, admin, ids, _sql = history_storage
    orphan_id = uuid4().hex
    with Session(engine) as session:
        user = session.get(User, ids[0])
        user.username = ""
        session.add(user)
        if resource == "invites":
            session.add(InviteCode(id=orphan_id, code=orphan_id, owner_id="missing-owner"))
        else:
            session.add(
                UserReward(
                    id=orphan_id,
                    user_id="missing-owner",
                    referral_id="missing-referral",
                    reward_type="points",
                    amount=1,
                    source="referral",
                )
            )
        session.commit()  # Test-owned SQLite retains legacy dangling references for compatibility coverage.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/admin/" + resource, headers={"Authorization": "Bearer " + create_access_token({"sub": admin})}
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] == 7
        by_id = {item["id"]: item for item in payload["items"]}
        field = "owner_name" if resource == "invites" else "username"
        assert by_id[ids[0]][field] == ""
        assert by_id[orphan_id][field] == "Unknown"
        if resource == "referrals/rewards":
            assert by_id[orphan_id]["referral_id"] is None


@pytest.mark.asyncio
async def test_admin_history_filters_and_counts_remain_unchanged(history_storage):
    engine, admin, ids, _sql = history_storage
    with Session(engine) as session:
        code = session.get(InviteCode, ids[0])
        code.is_active = False
        session.add(code)
        session.add(UserReward(user_id=ids[0], reward_type="points", amount=1, source="promotion"))
        session.commit()
    headers = {"Authorization": "Bearer " + create_access_token({"sub": admin})}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        inactive = await client.get("/api/admin/invites", headers=headers, params={"is_active": "false"})
        assert inactive.status_code == 200 and inactive.json()["total"] == 1
        assert [item["id"] for item in inactive.json()["items"]] == [ids[0]]
        active = await client.get("/api/admin/invites", headers=headers, params={"is_active": "true"})
        assert active.status_code == 200 and active.json()["total"] == 5
        checks = await client.get("/api/admin/check-in/records", headers=headers, params={"user_id": ids[0]})
        assert checks.status_code == 200 and checks.json()["total"] == 1
        assert checks.json()["items"][0]["user_id"] == ids[0]
        assert checks.json()["items"][0]["check_in_date"] == "2026-04-08"
        rewards = await client.get("/api/admin/referrals/rewards", headers=headers)
        assert rewards.status_code == 200 and rewards.json()["total"] == 6
        assert all(item["source"] == "referral" for item in rewards.json()["items"])
