"""Real User-row locking prevents an old code verifying a corrected address."""

import asyncio
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import Request
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, text
from sqlmodel import Session, SQLModel, select

import database
from database import get_session
from main import app
from models import RefreshTokenRecord, User
from services.core.auth_service import hash_password
from services.features import verification_service

pytestmark = pytest.mark.skipif(not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="owned PostgreSQL URL required")


@pytest.mark.parametrize("first,second", [
    ("recover", "verify"), ("verify", "recover"), ("recover", "resend"), ("resend", "recover"),
])
def test_old_email_operations_share_a_real_account_gate(monkeypatch, first, second):
    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert database.is_postgres and url == os.environ["DATABASE_URL"]
    engine = create_engine(url, connect_args={"options": "-c lock_timeout=12000 -c statement_timeout=15000"})
    SQLModel.metadata.create_all(engine, tables=[User.__table__, RefreshTokenRecord.__table__])
    with Session(engine) as session:
        user = User(username="recovery-lock-writer", email="old@example.com", email_verified=False,
                    hashed_password=hash_password("OwnedPassword1!"))
        session.add(user)
        session.commit()
        user_id = user.id
    locked, release, second_started = threading.Event(), threading.Event(), threading.Event()
    pids = {}
    codes = {"old@example.com": "original-code"}

    async def send_code(email, **_kwargs):
        if first == "resend" and email == "old@example.com":
            locked.set()
            assert await asyncio.to_thread(release.wait, 10), "sending gate was not released"
        codes[email] = f"{email}-code"
        return True, None

    verify = AsyncMock(return_value=(True, None))
    sender = AsyncMock(side_effect=send_code)
    monkeypatch.setattr("api.verification.verify_code", verify)
    monkeypatch.setattr(verification_service, "send_verification_code", sender)
    monkeypatch.setattr("api.verification.send_verification_code", sender)
    monkeypatch.setattr(verification_service, "delete_verification_code", MagicMock(side_effect=lambda email: codes.pop(email, None) is not None))
    monkeypatch.setattr(verification_service, "delete_resend_cooldown", MagicMock(return_value=True))
    monkeypatch.setenv("AUTH_RATE_LIMIT_ENABLED", "false")

    def session_for_request(request: Request):
        with Session(engine) as session:
            connection = session.connection()
            actor = request.headers["X-Recovery-Actor"]
            connection.info["recovery_actor"] = actor
            pids[actor] = connection.execute(text("SELECT pg_backend_pid()")).scalar_one()
            if actor == second:
                session.info["prefetched_user"] = session.get(User, user_id)
            yield session

    def before(connection, _cursor, statement, _parameters, _context, _many):
        if ('FOR UPDATE' in statement or statement.startswith('UPDATE "user"')) and connection.info.get("recovery_actor") == second:
            second_started.set()

    def after(connection, _cursor, statement, _parameters, _context, _many):
        if first != 'resend' and 'FOR UPDATE' in statement and connection.info.get("recovery_actor") == first:
            locked.set()
            assert release.wait(10), "owned lock was not released"

    def request(actor):
        async def send():
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                if actor == "verify":
                    path, body = "/api/auth/verify-email", {"email": "old@example.com", "code": "123456"}
                elif actor == "resend":
                    path, body = "/api/auth/resend-verification", {"email": "old@example.com"}
                else:
                    path, body = "/api/auth/registration-email", {
                        "email": "old@example.com", "password": "OwnedPassword1!", "new_email": "new@example.com",
                    }
                return await client.post(path, json=body, headers={"X-Recovery-Actor": actor})
        return asyncio.run(send())

    event.listen(engine, "before_cursor_execute", before)
    event.listen(engine, "after_cursor_execute", after)
    prior = app.dependency_overrides.get(get_session)
    app.dependency_overrides[get_session] = session_for_request
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            a = executor.submit(request, first)
            assert locked.wait(5)
            b = executor.submit(request, second)
            try:
                assert second_started.wait(5)
                deadline = time.monotonic() + 5
                blocked = False
                while time.monotonic() < deadline:
                    with engine.connect() as observer:
                        row = observer.execute(text("SELECT wait_event_type, pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid=:pid"), {"pid": pids[second]}).one()
                    if row[0] == "Lock" and pids[first] in row[1]:
                        blocked = True
                        break
                    time.sleep(0.02)
                assert blocked, "the second real PostgreSQL backend never waited for the account row"
            finally:
                release.set()
            responses = {first: a.result(10), second: b.result(10)}
        with Session(engine) as reader:
            stored = reader.get(User, user_id)
            if "resend" in (first, second):
                assert responses["recover"].status_code == responses["resend"].status_code == 200
                assert (stored.email, stored.email_verified) == ("new@example.com", False)
                assert "old@example.com" not in codes
                expected = ["old@example.com", "new@example.com"] if first == "resend" else ["new@example.com"]
                assert [call.args[0] for call in sender.await_args_list] == expected
            elif first == "recover":
                assert responses["recover"].status_code == 200
                assert responses["verify"].status_code == 400
                assert (stored.email, stored.email_verified) == ("new@example.com", False)
                verify.assert_not_awaited()
                assert reader.exec(select(RefreshTokenRecord)).all() == []
            else:
                assert responses["verify"].status_code == 200
                assert responses["recover"].status_code == 401
                assert (stored.email, stored.email_verified) == ("old@example.com", True)
                sender.assert_not_awaited()
    finally:
        release.set()
        if prior is None:
            app.dependency_overrides.pop(get_session, None)
        else:
            app.dependency_overrides[get_session] = prior
        event.remove(engine, "before_cursor_execute", before)
        event.remove(engine, "after_cursor_execute", after)
        SQLModel.metadata.drop_all(engine, tables=[RefreshTokenRecord.__table__, User.__table__])
        engine.dispose()


def test_old_address_remains_reserved_until_its_code_is_invalidated(monkeypatch):
    from services.features.registration_recovery_service import correct_registration_email

    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert database.is_postgres and url == os.environ["DATABASE_URL"]
    engine = create_engine(url, connect_args={"options": "-c lock_timeout=12000 -c statement_timeout=15000"})
    SQLModel.metadata.create_all(engine, tables=[User.__table__])
    with Session(engine) as session:
        session.add(User(username="recovery-original", email="old@example.com", email_verified=False,
                         hashed_password=hash_password("OwnedPassword1!")))
        session.commit()
    cleaning, release, claiming = threading.Event(), threading.Event(), threading.Event()
    pids = {}
    codes = {"old@example.com": "original-code"}

    async def send_code(email, **_kwargs):
        codes[email] = "corrected-code"
        return True, None

    def delete_code(email):
        assert email == "old@example.com"
        cleaning.set()
        assert release.wait(10), "cleanup gate was not released"
        codes.pop(email, None)
        return True

    def correct():
        with Session(engine) as session:
            pids["correct"] = session.connection().execute(text("SELECT pg_backend_pid()")).scalar_one()
            return asyncio.run(correct_registration_email(session, email="old@example.com",
                password="OwnedPassword1!", new_email="new@example.com", language="en"))

    def claim():
        with Session(engine) as session:
            pids["claim"] = session.connection().execute(text("SELECT pg_backend_pid()")).scalar_one()
            session.add(User(username="subsequent-registration", email="old@example.com",
                             hashed_password="unused", email_verified=False))
            claiming.set()
            session.flush()
            codes["old@example.com"] = "subsequent-registration-code"
            session.commit()

    monkeypatch.setattr(verification_service, "send_verification_code", send_code)
    monkeypatch.setattr(verification_service, "delete_verification_code", delete_code)
    monkeypatch.setattr(verification_service, "delete_resend_cooldown", MagicMock())
    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            correction = executor.submit(correct)
            assert cleaning.wait(5)
            claimant = executor.submit(claim)
            try:
                assert claiming.wait(5)
                deadline = time.monotonic() + 5
                blocked = False
                while time.monotonic() < deadline and not claimant.done():
                    with engine.connect() as observer:
                        row = observer.execute(text("SELECT wait_event_type, pg_blocking_pids(pid) FROM pg_stat_activity WHERE pid=:pid"), {"pid": pids["claim"]}).one()
                    if row[0] == "Lock" and pids["correct"] in row[1]:
                        blocked = True
                        break
                    time.sleep(0.02)
                assert blocked, "old address was released before its verification code was invalidated"
            finally:
                release.set()
            assert correction.result(10) == "new@example.com"
            claimant.result(10)
        assert codes["old@example.com"] == "subsequent-registration-code"
        with Session(engine) as session:
            assert {user.email for user in session.exec(select(User)).all()} == {"old@example.com", "new@example.com"}
    finally:
        release.set()
        SQLModel.metadata.drop_all(engine, tables=[User.__table__])
        engine.dispose()


def test_get_ui_language_failure_under_row_lock_preserves_for_update_lock():
    """A failing preference read inside a locked transaction must roll back only its savepoint."""
    from sqlalchemy.exc import OperationalError

    from services.features.user_preference_service import get_ui_language

    url = os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert database.is_postgres and url == os.environ["DATABASE_URL"]
    engine = create_engine(url, connect_args={"options": "-c lock_timeout=12000 -c statement_timeout=15000"})
    # Deliberately omit UserPreference table so get_ui_language raises SQLAlchemyError.
    SQLModel.metadata.create_all(engine, tables=[User.__table__])
    try:
        with Session(engine) as seed:
            user = User(
                username="pref-lock-writer",
                email="pref-lock@example.com",
                email_verified=False,
                hashed_password=hash_password("OwnedPassword1!"),
            )
            seed.add(user)
            seed.commit()
            user_id = user.id

        with Session(engine) as holder, Session(engine) as contender:
            locked_user = holder.exec(
                select(User).where(User.id == user_id).with_for_update()
            ).one()
            assert locked_user.id == user_id

            # Must swallow the missing-table error via savepoint without releasing the outer FOR UPDATE lock.
            assert get_ui_language(holder, user_id) is None

            # Verify the outer transaction is still healthy and still owns the row lock.
            with pytest.raises(OperationalError):
                contender.exec(
                    select(User).where(User.id == user_id).with_for_update(nowait=True)
                ).one()
            contender.rollback()

            # Holder's outer transaction remains usable (not in PostgreSQL aborted state).
            assert holder.exec(select(User.email).where(User.id == user_id)).one() == "pref-lock@example.com"
            holder.rollback()
    finally:
        SQLModel.metadata.drop_all(engine, tables=[User.__table__])
        engine.dispose()
