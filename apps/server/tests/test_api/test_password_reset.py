"""Self-service password reset over HTTP, against a real (owned) Redis.

POST /api/auth/password-reset/request and /api/auth/password-reset/confirm.
"""
import asyncio
import logging
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import select

from models import RefreshTokenRecord, User
from services.core.auth_service import hash_password
from services.features import password_reset_service
from services.infra import email_client
from services.infra import redis_client as storage
from tests.test_services.test_verification_consumption import owned_redis  # noqa: F401

OLD_PASSWORD = "old-secret-1"
NEW_PASSWORD = "new-secret-2"
REQUEST = "/api/auth/password-reset/request"
CONFIRM = "/api/auth/password-reset/confirm"


@pytest.fixture
def reset_env(owned_redis, monkeypatch):  # noqa: F811
    """Point storage at the owned Redis, capture outgoing mail, clean own keys."""
    monkeypatch.setattr(storage, "get_redis_client", lambda: owned_redis)
    monkeypatch.setattr(password_reset_service, "PASSWORD_RESET_REQUEST_MIN_SECONDS", 0)
    sent: list[dict] = []

    async def fake_send(recipient, code, expiry_minutes, language="zh", purpose="registration"):
        sent.append({
            "to": recipient, "code": code, "expiry": expiry_minutes,
            "language": language, "purpose": purpose,
        })
        return True

    monkeypatch.setattr(password_reset_service, "send_verification_email", fake_send)
    emails: list[str] = []

    def new_email() -> str:
        email = f"reset-{uuid4().hex}@example.com"
        emails.append(email)
        return email

    yield sent, new_email, owned_redis
    for email in emails:
        owned_redis.delete(
            *(storage.password_reset_key(kind, email) for kind in ("code", "attempts", "cooldown", "fails")),
            f"verification:{email}",
        )


def _make_user(db_session, email: str, *, verified: bool = True, active: bool = True) -> User:
    user = User(
        username=f"u{uuid4().hex[:12]}",
        email=email,
        hashed_password=hash_password(OLD_PASSWORD),
        email_verified=verified,
        is_active=active,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _login(client: AsyncClient, email: str, password: str):
    return await client.post("/api/auth/login", data={"username": email, "password": password})


async def _request_code(client: AsyncClient, email: str, sent: list[dict], **kwargs) -> str:
    response = await client.post(REQUEST, json={"email": email, "language": "zh"}, **kwargs)
    assert response.status_code == 200, response.text
    return sent[-1]["code"]


@pytest.mark.integration
async def test_registered_and_unknown_emails_get_identical_responses(client, db_session, reset_env):
    sent, new_email, _redis = reset_env
    known = new_email()
    _make_user(db_session, known)

    known_response = await client.post(REQUEST, json={"email": known, "language": "en"})
    unknown_response = await client.post(REQUEST, json={"email": new_email(), "language": "en"})

    assert known_response.status_code == unknown_response.status_code == 200
    assert known_response.json() == unknown_response.json()
    assert [mail["to"] for mail in sent] == [known]
    assert sent[0]["purpose"] == "password_reset"
    assert sent[0]["language"] == "en"
    assert sent[0]["expiry"] == 10


@pytest.mark.integration
async def test_request_waits_the_same_floor_for_every_address(client, db_session, reset_env, monkeypatch):
    _sent, new_email, _redis = reset_env
    known = new_email()
    _make_user(db_session, known)
    monkeypatch.setattr(password_reset_service, "PASSWORD_RESET_REQUEST_MIN_SECONDS", 0.2)
    loop = asyncio.get_running_loop()

    for email in (known, new_email()):
        started = loop.time()
        response = await client.post(REQUEST, json={"email": email})
        assert response.status_code == 200
        assert loop.time() - started >= 0.2


@pytest.mark.integration
@pytest.mark.parametrize("verified,active", [(False, True), (True, False)])
async def test_unverified_or_disabled_accounts_get_no_email(client, db_session, reset_env, verified, active):
    sent, new_email, redis = reset_env
    email = new_email()
    _make_user(db_session, email, verified=verified, active=active)

    response = await client.post(REQUEST, json={"email": email})

    assert response.status_code == 200
    assert sent == []
    assert redis.get(storage.password_reset_key("code", email)) is None


@pytest.mark.integration
async def test_cooldown_skips_a_second_email(client, db_session, reset_env):
    sent, new_email, _redis = reset_env
    email = new_email()
    _make_user(db_session, email)

    first = await client.post(REQUEST, json={"email": email})
    second = await client.post(REQUEST, json={"email": email.upper()})

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(sent) == 1


@pytest.mark.integration
async def test_request_rate_limit_per_ip(client, reset_env):
    _sent, new_email, _redis = reset_env
    for _ in range(5):
        assert (await client.post(REQUEST, json={"email": new_email()})).status_code == 200

    blocked = await client.post(REQUEST, json={"email": new_email()})

    assert blocked.status_code == 429
    assert blocked.json()["error_code"] == "ERR_AUTH_PASSWORD_RESET_TOO_MANY_REQUESTS"


@pytest.mark.integration
async def test_request_rate_limit_per_email_across_ips(client, reset_env):
    _sent, new_email, _redis = reset_env
    email = new_email()
    for index in range(3):
        response = await client.post(
            REQUEST, json={"email": email}, headers={"X-Forwarded-For": f"203.0.113.{index}"},
        )
        assert response.status_code == 200

    blocked = await client.post(
        REQUEST, json={"email": email}, headers={"X-Forwarded-For": "203.0.113.99"},
    )

    assert blocked.status_code == 429
    assert blocked.json()["error_code"] == "ERR_AUTH_PASSWORD_RESET_TOO_MANY_REQUESTS"


@pytest.mark.integration
async def test_confirm_rate_limit_per_ip(client, reset_env):
    _sent, new_email, _redis = reset_env
    email = new_email()
    body = {"email": email, "code": "000000", "new_password": NEW_PASSWORD}
    for _ in range(20):
        assert (await client.post(CONFIRM, json=body)).status_code == 400

    blocked = await client.post(CONFIRM, json=body)

    assert blocked.status_code == 429


@pytest.mark.integration
async def test_unknown_account_and_wrong_code_share_one_error(client, db_session, reset_env):
    sent, new_email, _redis = reset_env
    email = new_email()
    _make_user(db_session, email)
    code = await _request_code(client, email, sent)
    wrong = "0" * 6 if code != "0" * 6 else "1" * 6

    wrong_code = await client.post(CONFIRM, json={"email": email, "code": wrong, "new_password": NEW_PASSWORD})
    no_account = await client.post(
        CONFIRM, json={"email": new_email(), "code": code, "new_password": NEW_PASSWORD},
    )

    assert wrong_code.status_code == no_account.status_code == 400
    assert wrong_code.json() == no_account.json()
    assert wrong_code.json()["error_code"] == "ERR_AUTH_PASSWORD_RESET_CODE_INVALID"


@pytest.mark.integration
async def test_five_wrong_codes_burn_the_real_one(client, db_session, reset_env):
    sent, new_email, redis = reset_env
    email = new_email()
    _make_user(db_session, email)
    code = await _request_code(client, email, sent)
    wrong = "0" * 6 if code != "0" * 6 else "1" * 6

    for _ in range(5):
        response = await client.post(CONFIRM, json={"email": email, "code": wrong, "new_password": NEW_PASSWORD})
        assert response.json()["error_code"] == "ERR_AUTH_PASSWORD_RESET_CODE_INVALID"
    assert redis.get(storage.password_reset_key("code", email)) is None

    late = await client.post(CONFIRM, json={"email": email, "code": code, "new_password": NEW_PASSWORD})

    assert late.status_code == 400
    assert late.json()["error_code"] == "ERR_AUTH_PASSWORD_RESET_CODE_INVALID"
    assert (await _login(client, email, OLD_PASSWORD)).status_code == 200


@pytest.mark.integration
async def test_expired_code_is_rejected(client, db_session, reset_env):
    sent, new_email, redis = reset_env
    email = new_email()
    _make_user(db_session, email)
    code = await _request_code(client, email, sent)
    key = storage.password_reset_key("code", email)
    assert 590 <= redis.ttl(key) <= 600

    redis.pexpire(key, 20)
    await asyncio.sleep(0.1)
    response = await client.post(CONFIRM, json={"email": email, "code": code, "new_password": NEW_PASSWORD})

    assert response.status_code == 400
    assert response.json()["error_code"] == "ERR_AUTH_PASSWORD_RESET_CODE_INVALID"


@pytest.mark.integration
async def test_new_password_follows_registration_rules(client, db_session, reset_env):
    sent, new_email, redis = reset_env
    email = new_email()
    _make_user(db_session, email)
    code = await _request_code(client, email, sent)

    response = await client.post(CONFIRM, json={"email": email, "code": code, "new_password": "short"})

    assert response.status_code == 422
    # A rejected password does not spend the code.
    assert redis.get(storage.password_reset_key("code", email)) is not None


@pytest.mark.integration
async def test_successful_reset_signs_out_every_session(client, db_session, reset_env):
    sent, new_email, redis = reset_env
    email = new_email()
    user = _make_user(db_session, email)
    sessions = [(await _login(client, email, OLD_PASSWORD)).json() for _ in range(2)]

    code = await _request_code(client, email, sent)
    response = await client.post(
        CONFIRM, json={"email": email.upper(), "code": code, "new_password": NEW_PASSWORD},
    )

    assert response.status_code == 200
    assert "access_token" not in response.json()  # No automatic login.
    assert redis.get(storage.password_reset_key("code", email)) is None
    assert (await _login(client, email, OLD_PASSWORD)).status_code == 401
    fresh = await _login(client, email, NEW_PASSWORD)
    assert fresh.status_code == 200
    for old in sessions:
        me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {old['access_token']}"})
        assert me.status_code == 401
        refreshed = await client.post("/api/auth/refresh", json={"refresh_token": old["refresh_token"]})
        assert refreshed.status_code == 401
    me = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {fresh.json()['access_token']}"})
    assert me.status_code == 200

    db_session.expire_all()
    reasons = {
        record.revoke_reason
        for record in db_session.exec(
            select(RefreshTokenRecord).where(RefreshTokenRecord.user_id == user.id)
        ).all()
        if record.revoked_at is not None
    }
    assert "password_reset" in reasons

    replay = await client.post(CONFIRM, json={"email": email, "code": code, "new_password": OLD_PASSWORD})
    assert replay.status_code == 400


@pytest.mark.integration
async def test_registration_code_cannot_reset_a_password(client, db_session, reset_env):
    _sent, new_email, redis = reset_env
    email = new_email()
    _make_user(db_session, email)
    assert storage.store_verification_code(email, "123456", 300)

    response = await client.post(CONFIRM, json={"email": email, "code": "123456", "new_password": NEW_PASSWORD})

    assert response.status_code == 400
    assert response.json()["error_code"] == "ERR_AUTH_PASSWORD_RESET_CODE_INVALID"
    assert redis.get(f"verification:{email}") == "123456"
    assert (await _login(client, email, OLD_PASSWORD)).status_code == 200


@pytest.mark.integration
async def test_codes_never_reach_the_logs(client, db_session, owned_redis, monkeypatch, caplog):  # noqa: F811
    """Run the real email client (Resend stubbed) and inspect every log record."""
    monkeypatch.setattr(storage, "get_redis_client", lambda: owned_redis)
    monkeypatch.setattr(password_reset_service, "PASSWORD_RESET_REQUEST_MIN_SECONDS", 0)
    monkeypatch.setattr(email_client, "RESEND_API_KEY", "test-key")
    outgoing: list[dict] = []

    def fake_resend_send(params):
        outgoing.append(params)
        return {"id": "email-1"}

    monkeypatch.setattr(email_client.resend.Emails, "send", fake_resend_send)
    issued: list[str] = []
    generate = password_reset_service.generate_reset_code

    def recording_generate() -> str:
        issued.append(generate())
        return issued[-1]

    monkeypatch.setattr(password_reset_service, "generate_reset_code", recording_generate)
    email = f"reset-{uuid4().hex}@example.com"
    _make_user(db_session, email)
    caplog.set_level(logging.DEBUG)

    try:
        assert (await client.post(REQUEST, json={"email": email})).status_code == 200
        assert len(outgoing) == 1
        subject = outgoing[0]["subject"]
        assert subject == "重设你的 ZenStory 密码"
        assert "如果不是你本人操作，忽略这封邮件即可" in outgoing[0]["html"]
        [code] = issued
        assert code in outgoing[0]["html"]
        wrong = "0" * 6 if code != "0" * 6 else "1" * 6
        await client.post(CONFIRM, json={"email": email, "code": wrong, "new_password": NEW_PASSWORD})
        await client.post(CONFIRM, json={"email": email, "code": code, "new_password": NEW_PASSWORD})
    finally:
        owned_redis.delete(
            *(storage.password_reset_key(kind, email) for kind in ("code", "attempts", "cooldown", "fails")),
        )

    assert caplog.records
    for record in caplog.records:
        rendered = f"{record.getMessage()} {record.__dict__.get('custom_fields', {})}"
        assert code not in rendered
        assert wrong not in rendered
