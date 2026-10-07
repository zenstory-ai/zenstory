"""PostgreSQL proof that concurrent admin removals cannot remove every admin."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import Request
from sqlalchemy import create_engine, func
from sqlmodel import Session, select

import api.admin.users as admin_users
from api.admin.schemas import UserUpdateRequest
from core.error_handler import APIException
from models import User
from models.subscription import AdminAuditLog

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
    reason="isolated PostgreSQL URL not configured",
)

TABLES = [User.__table__, AdminAuditLog.__table__]


@pytest.fixture(scope="module")
def pg_engine():
    engine = create_engine(os.environ["ZENSTORY_TEST_POSTGRES_URL"], pool_pre_ping=True)
    for table in reversed(TABLES):
        table.drop(engine, checkfirst=True)
    for table in TABLES:
        table.create(engine, checkfirst=True)
    try:
        yield engine
    finally:
        for table in reversed(TABLES):
            table.drop(engine, checkfirst=True)
        engine.dispose()


def _admin(session: Session, suffix: str) -> User:
    admin = User(
        username=f"roles-pg-{suffix}",
        email=f"roles-pg-{suffix}@example.test",
        hashed_password="hashed",
        email_verified=True,
        is_active=True,
        is_superuser=True,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


@pytest.mark.parametrize(
    ("operation", "expected_action"),
    [
        ("demote", "update_user"),
        ("deactivate", "update_user"),
        ("delete", "delete_user"),
    ],
)
def test_concurrent_cross_removals_preserve_an_active_admin(
    pg_engine, monkeypatch, operation: str, expected_action: str
):
    with Session(pg_engine) as setup:
        admin_a = _admin(setup, f"{operation}-a")
        admin_b = _admin(setup, f"{operation}-b")
        admin_ids = (admin_a.id, admin_b.id)

    barrier = threading.Barrier(2)
    attempt_guard = threading.Lock()
    lock_attempts = []
    real_lock_admin_access = admin_users._lock_admin_access

    def track_lock_attempt(session: Session, caller_id: str, target_id: str) -> None:
        with attempt_guard:
            lock_attempts.append((caller_id, target_id))
        real_lock_admin_access(session, caller_id, target_id)

    monkeypatch.setattr(admin_users, "_lock_admin_access", track_lock_attempt)

    def remove_other(pair: tuple[str, str]) -> int:
        caller_id, target_id = pair
        http_request = Request({
            "type": "http",
            "http_version": "1.1",
            "method": "DELETE" if operation == "delete" else "PUT",
            "scheme": "http",
            "path": f"/api/admin/users/{target_id}",
            "query_string": b"",
            "headers": [(b"user-agent", b"local-pg-admin-role-proof")],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
        })
        with Session(pg_engine) as session:
            # Deliberately preload both callers before either request proceeds. The
            # loser therefore carries a stale in-memory superuser object and must
            # be rejected by the locked database recheck, not by dependency auth.
            current_user = session.get(User, caller_id)
            assert current_user is not None
            assert current_user.is_active is True
            assert current_user.is_superuser is True
            barrier.wait()
            try:
                if operation == "demote":
                    admin_users.update_user(
                        target_id,
                        UserUpdateRequest(is_superuser=False),
                        http_request=http_request,
                        current_user=current_user,
                        session=session,
                    )
                elif operation == "deactivate":
                    admin_users.update_user(
                        target_id,
                        UserUpdateRequest(is_active=False),
                        http_request=http_request,
                        current_user=current_user,
                        session=session,
                    )
                else:
                    admin_users.delete_user(
                        target_id, http_request=http_request, current_user=current_user, session=session
                    )
                return 200
            except APIException as exc:
                return exc.status_code

    attempts = [(admin_ids[0], admin_ids[1]), (admin_ids[1], admin_ids[0])]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(remove_other, attempts))

    assert outcomes.count(200) == 1
    assert next(outcome for outcome in outcomes if outcome != 200) in {403, 409}
    assert sorted(lock_attempts) == sorted(attempts)

    with Session(pg_engine) as verify:
        active_admins = verify.exec(
            select(User).where(
                User.id.in_(admin_ids),
                User.is_active.is_(True),
                User.is_superuser.is_(True),
            )
        ).all()
        assert len(active_admins) == 1

        audits = verify.exec(
            select(AdminAuditLog).where(
                AdminAuditLog.resource_type == "user",
                AdminAuditLog.resource_id.in_(admin_ids),
            )
        ).all()
        assert len(audits) == 1
        assert audits[0].action == expected_action
        assert audits[0].admin_user_id == active_admins[0].id
        assert audits[0].resource_id != active_admins[0].id

        assert verify.exec(
            select(func.count()).select_from(User).where(
                User.is_active.is_(True),
                User.is_superuser.is_(True),
            )
        ).one() >= 1
