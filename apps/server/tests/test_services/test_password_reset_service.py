"""Password reset code storage and consumption against a real (owned) Redis."""
import asyncio
import hashlib
import threading
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from sqlmodel import select

from core.error_handler import APIException
from models import User
from services.core.auth_service import hash_password, verify_password
from services.features import password_reset_service as service
from services.infra import redis_client as storage
from tests.test_services.test_verification_consumption import owned_redis  # noqa: F401


@pytest.fixture
def reset_user(owned_redis, db_session, monkeypatch):  # noqa: F811
    monkeypatch.setattr(storage, "get_redis_client", lambda: owned_redis)
    monkeypatch.setattr(service, "PASSWORD_RESET_REQUEST_MIN_SECONDS", 0)
    sent: list[str] = []

    async def fake_send(_recipient, code, _expiry, language="zh", purpose="registration"):
        assert purpose == "password_reset"
        sent.append(code)
        return True

    monkeypatch.setattr(service, "send_verification_email", fake_send)
    email = f"reset-{uuid4().hex}@example.com"
    user = User(
        username=f"u{uuid4().hex[:12]}",
        email=email,
        hashed_password=hash_password("old-secret-1"),
        email_verified=True,
    )
    db_session.add(user)
    db_session.commit()
    yield email, sent, owned_redis
    owned_redis.delete(
        *(storage.password_reset_key(kind, email) for kind in ("code", "attempts", "cooldown", "fails")),
    )


async def _issue(db_session, email: str, sent: list[str]) -> str:
    tasks = BackgroundTasks()
    await service.request_reset(db_session, email, "zh", tasks)
    await tasks()
    return sent[-1]


@pytest.mark.asyncio
async def test_only_the_code_hash_is_stored(db_session, reset_user):
    email, sent, redis = reset_user

    code = await _issue(db_session, email, sent)

    stored = redis.get(storage.password_reset_key("code", email))
    assert stored == hashlib.sha256(code.encode()).hexdigest()
    assert code not in stored
    assert 590 <= redis.ttl(storage.password_reset_key("code", email)) <= service.PASSWORD_RESET_CODE_TTL
    assert redis.get(f"verification:{email}") is None


def test_consumption_is_single_use(reset_user):
    email, _sent, redis = reset_user
    code_hash = service.hash_reset_code("123456")
    assert storage.store_password_reset_code(email, code_hash, 600)
    assert storage.record_password_reset_failure(email, 5, 600, 10, 86400) == 1

    assert storage.consume_password_reset_code(email, service.hash_reset_code("654321")) is False
    assert storage.consume_password_reset_code(email, code_hash) is True
    assert storage.consume_password_reset_code(email, code_hash) is False
    assert redis.get(storage.password_reset_key("code", email)) is None
    assert redis.get(storage.password_reset_key("attempts", email)) is None


@pytest.mark.asyncio
async def test_two_concurrent_confirms_succeed_once(db_session, reset_user, monkeypatch):
    email, sent, _redis = reset_user
    code = await _issue(db_session, email, sent)
    original = storage.get_password_reset_code_hash
    readers = threading.Barrier(2)

    def read_together(identity):
        result = original(identity)
        readers.wait(timeout=5)  # Both confirms hold a matching hash before either consumes.
        return result

    monkeypatch.setattr(storage, "get_password_reset_code_hash", read_together)
    results = await asyncio.gather(
        service.confirm_reset(db_session, email, code, "first-new-1"),
        service.confirm_reset(db_session, email, code, "second-new-2"),
        return_exceptions=True,
    )

    failures = [result for result in results if isinstance(result, APIException)]
    assert sum(result is None for result in results) == 1, results
    assert len(failures) == 1
    assert failures[0].error_code == "ERR_AUTH_PASSWORD_RESET_CODE_INVALID"
    db_session.expire_all()
    user = db_session.exec(select(User).where(User.email == email)).one()
    winner = "first-new-1" if results[0] is None else "second-new-2"
    assert verify_password(winner, user.hashed_password)


@pytest.mark.asyncio
async def test_redis_failure_on_consume_cannot_reset(db_session, reset_user, monkeypatch):
    email, sent, _redis = reset_user
    code = await _issue(db_session, email, sent)
    monkeypatch.setattr(storage, "consume_password_reset_code", lambda *_args: False)

    with pytest.raises(APIException) as raised:
        await service.confirm_reset(db_session, email, code, "brand-new-3")

    assert raised.value.error_code == "ERR_AUTH_PASSWORD_RESET_CODE_INVALID"
    db_session.expire_all()
    user = db_session.exec(select(User).where(User.email == email)).one()
    assert verify_password("old-secret-1", user.hashed_password)


@pytest.mark.asyncio
async def test_failed_email_delivery_allows_an_immediate_retry(db_session, reset_user, monkeypatch):
    email, sent, redis = reset_user

    async def failing_send(*_args, **_kwargs):
        return False

    monkeypatch.setattr(service, "send_verification_email", failing_send)
    tasks = BackgroundTasks()
    await service.request_reset(db_session, email, "zh", tasks)
    await tasks()

    assert redis.get(storage.password_reset_key("code", email)) is None
    assert redis.get(storage.password_reset_key("cooldown", email)) is None


async def _confirm_fails(db_session, email: str, code: str, new_password: str = "brand-new-9") -> str:
    with pytest.raises(APIException) as raised:
        await service.confirm_reset(db_session, email, code, new_password)
    return raised.value.error_code


def _wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


@pytest.mark.asyncio
async def test_new_codes_do_not_restore_the_daily_wrong_code_budget(db_session, reset_user):
    email, sent, redis = reset_user
    cap = service.PASSWORD_RESET_MAX_DAILY_FAILURES
    per_code = service.PASSWORD_RESET_MAX_ATTEMPTS - 1  # Stay under the per-code budget.

    failures = 0
    code = ""
    while failures < cap:
        redis.delete(storage.password_reset_key("cooldown", email))
        code = await _issue(db_session, email, sent)
        for _ in range(min(per_code, cap - failures)):
            assert await _confirm_fails(db_session, email, _wrong(code)) == "ERR_AUTH_PASSWORD_RESET_CODE_INVALID"
            failures += 1

    fails_key = storage.password_reset_key("fails", email)
    assert int(redis.get(fails_key)) == cap
    assert 0 < redis.ttl(fails_key) <= service.PASSWORD_RESET_FAILURE_WINDOW_SECONDS
    # The failure that reached the cap removed the live code; even the right code fails.
    assert redis.get(storage.password_reset_key("code", email)) is None
    assert await _confirm_fails(db_session, email, code) == "ERR_AUTH_PASSWORD_RESET_CODE_INVALID"

    # While capped, request answers as usual but issues no new code.
    issued = len(sent)
    redis.delete(storage.password_reset_key("cooldown", email))
    tasks = BackgroundTasks()
    await service.request_reset(db_session, email, "zh", tasks)
    await tasks()
    assert len(sent) == issued
    assert redis.get(storage.password_reset_key("code", email)) is None

    db_session.expire_all()
    user = db_session.exec(select(User).where(User.email == email)).one()
    assert verify_password("old-secret-1", user.hashed_password)


@pytest.mark.asyncio
async def test_capped_address_rejects_a_correct_code_with_the_generic_error(db_session, reset_user):
    email, sent, redis = reset_user
    code = await _issue(db_session, email, sent)
    redis.set(storage.password_reset_key("fails", email), service.PASSWORD_RESET_MAX_DAILY_FAILURES, ex=60)

    assert await _confirm_fails(db_session, email, code) == "ERR_AUTH_PASSWORD_RESET_CODE_INVALID"
    db_session.expire_all()
    user = db_session.exec(select(User).where(User.email == email)).one()
    assert verify_password("old-secret-1", user.hashed_password)


@pytest.mark.asyncio
async def test_unknown_address_gets_the_same_error_and_no_failure_counter(db_session, reset_user):
    _email, _sent, redis = reset_user
    unknown = f"nobody-{uuid4().hex}@example.com"
    try:
        for _ in range(service.PASSWORD_RESET_MAX_DAILY_FAILURES + 1):
            assert await _confirm_fails(db_session, unknown, "123456") == "ERR_AUTH_PASSWORD_RESET_CODE_INVALID"
        assert redis.get(storage.password_reset_key("fails", unknown)) is None
    finally:
        redis.delete(storage.password_reset_key("fails", unknown))


def test_failure_cap_check_fails_closed_when_redis_errors(monkeypatch):
    def broken():
        raise ConnectionError("redis down")

    monkeypatch.setattr(storage, "get_redis_client", broken)
    assert storage.password_reset_failures_capped("someone@example.com", 10) is True
