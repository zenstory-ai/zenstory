"""Real synchronous ledger work must not execute on the ASGI event loop."""

import inspect
import threading
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine, select

from api import points, referral
from api.admin import referrals as admin_referrals
from database import get_session
from main import app
from models import User
from models.referral import InviteCode
from models.subscription import AdminAuditLog, SubscriptionPlan
from services.core.auth_service import create_access_token
from services.features import referral_service


def test_only_no_await_referral_services_change_calling_convention():
    for name in ("create_invite_code", "validate_invite_code", "get_user_referral_stats", "get_user_invite_codes"):
        assert not inspect.iscoroutinefunction(getattr(referral_service, name)), name
    for name in ("create_referral", "complete_referral_and_reward", "complete_pending_referral_for_invitee"):
        assert inspect.iscoroutinefunction(getattr(referral_service, name)), name


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "method", "path", "handler", "payload", "status"),
    [
        (points, "GET", "points/balance", "get_balance", None, 200),
        (points, "POST", "points/check-in", "check_in", None, 200),
        (points, "GET", "points/check-in/status", "get_check_in_status", None, 200),
        (points, "GET", "points/transactions", "get_transactions", None, 200),
        (points, "POST", "points/redeem", "redeem_for_pro", {"days": 7}, 402),
        (points, "GET", "points/earn-opportunities", "get_earn_opportunities", None, 200),
        (referral, "GET", "referral/rewards", "get_user_rewards", None, 200),
        (referral, "DELETE", "referral/codes/{code_id}", "deactivate_invite_code", None, 204),
        (referral, "GET", "referral/codes", "get_invite_codes", None, 200),
        (referral, "POST", "referral/codes", "create_invite_code", None, 201),
        (referral, "POST", "referral/codes/ABCD-1234/validate", "validate_invite_code", None, 200),
        (referral, "GET", "referral/stats", "get_referral_stats", None, 200),
        (admin_referrals, "POST", "admin/invites", "create_admin_invite_code", None, 201),
        (admin_referrals, "POST", "admin/invites", "create_admin_invite_code", None, 403),
    ],
)
async def test_ledger_route_sql_executes_off_loop(
    tmp_path, monkeypatch, module, method, path, handler, payload, status
):
    database = tmp_path / "ledger-worker.db"
    engine = create_engine(f"sqlite:///{database}", connect_args={"check_same_thread": False})
    user_id = uuid4().hex
    code_id = uuid4().hex
    body_threads = []
    loop_thread = threading.get_ident()

    def request_session():
        with Session(engine) as session:
            yield session

    def record_sql(_conn, _cursor, _statement, _params, _ctx, _many):
        frame = inspect.currentframe()
        try:
            while frame:
                if frame.f_code.co_filename == module.__file__ and frame.f_code.co_name == handler:
                    body_threads.append(threading.get_ident())
                    break
                frame = frame.f_back
        finally:
            del frame

    event.listen(engine, "before_cursor_execute", record_sql)
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
                    email_verified=True,
                    is_superuser=module is admin_referrals and status == 201,
                )
            )
            seed.add(SubscriptionPlan(name="free", display_name="Free", features={}))
            seed.commit()
            seed.add(InviteCode(id=code_id, code="ABCD-1234", owner_id=user_id))
            seed.commit()
        with monkeypatch.context() as context:
            context.setitem(app.dependency_overrides, get_session, request_session)
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.request(
                    method,
                    ("/api/" if module is admin_referrals else "/api/v1/") + path.format(code_id=code_id),
                    headers={}
                    if handler == "validate_invite_code"
                    else {"Authorization": "Bearer " + create_access_token({"sub": user_id})},
                    json=payload,
                )
                assert response.status_code == status, response.text
                if status == 403:
                    assert not body_threads
                    with Session(engine) as verify:
                        assert len(verify.exec(select(InviteCode)).all()) == 1
                        assert verify.exec(select(AdminAuditLog)).all() == []
                    return
                if handler == "validate_invite_code":
                    assert response.json()["valid"] is True
                if module is admin_referrals:
                    with Session(engine) as verify:
                        created = verify.get(InviteCode, response.json()["id"])
                        assert created is not None and created.owner_id == user_id
                        audit = verify.exec(select(AdminAuditLog)).one()
                        assert audit.action == "create_invite_code" and audit.resource_id == created.id
                assert body_threads, "No SQL executed by the actual route body"
                assert all(thread != loop_thread for thread in body_threads)
    finally:
        event.remove(engine, "before_cursor_execute", record_sql)
        engine.dispose()
        database.unlink(missing_ok=True)
        assert not database.exists()
