"""Registered admin offset pages retain a total order at equal timestamps."""

from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlmodel import Session, SQLModel, create_engine

from database import get_session
from main import app
from models import User
from models.payment import PaymentOrder
from models.subscription import RedemptionCode, SubscriptionPlan
from services.core.auth_service import create_access_token


@pytest.mark.asyncio
@pytest.mark.parametrize("resource", ["codes", "subscriptions", "payment-orders"])
async def test_admin_timestamp_ties_have_complete_repeatable_pages(tmp_path, monkeypatch, resource):
    path = tmp_path / "admin-pagination.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    tag = uuid4().hex[:16]
    admin_id = tag + "-admin"
    ids = [tag + "-" + suffix for suffix in ("a", "f", "b", "e", "c", "d")]
    timestamp = datetime(2026, 4, 8, 9)

    def request_session():
        with Session(engine) as session:
            yield session

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            seed.add(SubscriptionPlan(name="free", display_name="Free", features={}))
            seed.add(SubscriptionPlan(name="pro", display_name="Pro", features={}))
            seed.add(
                User(
                    id=admin_id,
                    username=admin_id,
                    email=admin_id + "@example.test",
                    hashed_password="unused",
                    is_superuser=True,
                    is_active=True,
                    created_at=timestamp - timedelta(days=1),
                )
            )
            for user_id in ids:
                seed.add(
                    User(
                        id=user_id,
                        username=user_id,
                        email=user_id + "@example.test",
                        hashed_password="unused",
                        is_active=True,
                        created_at=timestamp,
                    )
                )
            seed.commit()
            if resource == "codes":
                seed.add_all(
                    [
                        RedemptionCode(
                            id=key, code=key, tier="pro", duration_days=30, created_by=admin_id, created_at=timestamp
                        )
                        for key in ids
                    ]
                )
            elif resource == "payment-orders":
                seed.add_all(
                    [
                        PaymentOrder(
                            id=key,
                            out_trade_no=key,
                            user_id=ids[0],
                            plan_name="pro",
                            plan_display_name="Pro",
                            product_name="Zenstory Pro",
                            cycle="monthly",
                            amount_cents=100,
                            duration_days=30,
                            payment_method="alipay",
                            created_at=timestamp,
                        )
                        for key in ids
                    ]
                )
            seed.commit()
        with monkeypatch.context() as context:
            context.setitem(app.dependency_overrides, get_session, request_session)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                denied = await client.get(
                    "/api/admin/" + resource,
                    headers={
                        "Authorization": "Bearer " + create_access_token({"sub": ids[0]}),
                    },
                )
                assert denied.status_code == 403
                expected = sorted(ids, reverse=True)
                if resource == "subscriptions":
                    expected.append(admin_id)  # The virtual free row is part of the existing contract.
                headers = {"Authorization": "Bearer " + create_access_token({"sub": admin_id})}
                observed = []
                for page in range(1, (len(expected) + 1) // 2 + 1):
                    params = {"page": page, "page_size": 2}
                    first = await client.get("/api/admin/" + resource, headers=headers, params=params)
                    repeat = await client.get("/api/admin/" + resource, headers=headers, params=params)
                    assert first.status_code == repeat.status_code == 200
                    payload = first.json()
                    assert payload["total"] == len(expected)
                    assert payload == repeat.json()
                    field = "user_id" if resource == "subscriptions" else "id"
                    observed.extend(item[field] for item in payload["items"])
                    if resource == "subscriptions":
                        assert all(not item["has_subscription_record"] for item in payload["items"])
                assert observed == expected
                assert len(set(observed)) == len(expected)
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)
        assert not path.exists()
