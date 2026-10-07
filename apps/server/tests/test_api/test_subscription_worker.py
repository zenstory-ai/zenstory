"""Synchronous subscription bodies use FastAPI's ordinary worker path."""

import inspect
import threading
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from api import subscription
from database import get_session
from main import app
from models import User
from models.subscription import SubscriptionPlan
from services.core.auth_service import create_access_token


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "path", "handler", "payload", "status"),
    [
        ("GET", "me", "get_subscription_status", None, 200),
        ("GET", "quota", "get_quota", None, 200),
        ("GET", "plans", "list_plans", None, 200),
        ("GET", "catalog", "get_subscription_catalog", None, 200),
        ("POST", "redeem", "redeem_code", {"code": "ERG-PRO7M-ABCD-12345678"}, 400),
        ("GET", "history", "get_history", None, 200),
        (
            "POST",
            "upgrade-funnel-events",
            "track_upgrade_funnel_event",
            {"action": "expose", "source": "billing", "surface": "page"},
            201,
        ),
    ],
)
async def test_subscription_body_executes_off_loop(tmp_path, monkeypatch, method, path, handler, payload, status):
    database = tmp_path / "subscription-worker.db"
    engine = create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False})
    user_id = uuid4().hex
    body_threads = []
    loop_thread = threading.get_ident()

    def request_session():
        with Session(engine) as session:
            yield session

    def record_sql(_conn, _cursor, _statement, _params, _ctx, _many):
        frame = inspect.currentframe()
        try:
            while frame:
                if frame.f_code.co_filename == subscription.__file__ and frame.f_code.co_name == handler:
                    body_threads.append(threading.get_ident())
                    break
                frame = frame.f_back
        finally:
            del frame

    original_redeem = subscription.redemption_service.redeem_code

    def record_redeem(*args, **kwargs):
        body_threads.append(threading.get_ident())
        return original_redeem(*args, **kwargs)

    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as seed:
            seed.add(
                User(
                    id=user_id,
                    username=user_id,
                    email=user_id + "@example.test",
                    hashed_password="unused",
                    is_active=True,
                )
            )
            seed.add(SubscriptionPlan(name="free", display_name="Free", features={}))
            seed.commit()
        event.listen(engine, "before_cursor_execute", record_sql)
        with monkeypatch.context() as context:
            context.setitem(app.dependency_overrides, get_session, request_session)
            if path == "redeem":
                context.setenv("REDEMPTION_CODE_HMAC_SECRET", "local-test-only-unused-checksum-secret")
                context.setattr(subscription.redemption_service, "redeem_code", record_redeem)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.request(
                    method,
                    "/api/v1/subscription/" + path,
                    headers={"Authorization": "Bearer " + create_access_token({"sub": user_id})},
                    json=payload,
                )
                assert response.status_code == status, response.text
                assert body_threads, "No actual body/service marker observed"
                assert all(thread != loop_thread for thread in body_threads)
    finally:
        event.remove(engine, "before_cursor_execute", record_sql)
        engine.dispose()
        database.unlink(missing_ok=True)
        assert not database.exists()
