"""Configured email domains cannot become new accounts through any entry; existing accounts keep working."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlmodel import select

from core.error_codes import ERROR_MESSAGES, ErrorCode
from models import User
from services.core.auth_service import hash_password
from services.core.registration_email_policy import is_registration_email_blocked

CODE = "ERR_AUTH_EMAIL_DOMAIN_NOT_ALLOWED"
BLOCKED = "blocked-mail.com"


@pytest.fixture(autouse=True)
def _policy(monkeypatch):
    monkeypatch.setenv("REGISTRATION_BLOCKED_EMAIL_DOMAINS", f"{BLOCKED}, Second-Block.ORG")
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "true")


@pytest.fixture
def mailer(monkeypatch):
    send = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("api.auth.send_verification_code", send)
    monkeypatch.setattr("api.verification.send_verification_code", send)
    monkeypatch.setattr("services.features.registration_recovery_service.verification_service.send_verification_code", send)
    monkeypatch.setattr("api.auth.clear_registration_verification", AsyncMock())
    return send


def _user(db_session, email: str, *, verified: bool, password: str = "password123") -> User:
    user = User(
        username=f"denylist_{uuid4().hex[:8]}", email=email, hashed_password=hash_password(password),
        email_verified=verified, is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _register_body(email: str) -> dict:
    return {"email": email, "password": "Password123!", "language": "zh"}


# ---- matching ----


@pytest.mark.parametrize("email", [
    f"writer@{BLOCKED}",
    f"writer@{BLOCKED.upper()}",
    "WRITER@Blocked-Mail.Com",
    f"writer@a.{BLOCKED}",
    f"writer@deep.sub.{BLOCKED}",
    "writer@second-block.org",
    "writer@sub.SECOND-BLOCK.org",
    "writer@ｂｌｏｃｋｅｄ-ｍａｉｌ.ｃｏｍ",
    f"  writer@{BLOCKED}  ",
])
def test_configured_domains_and_their_subdomains_match(email):
    assert is_registration_email_blocked(email) is True


@pytest.mark.parametrize("email", [
    f"writer@not{BLOCKED}",
    f"writer@{BLOCKED}.example.org",
    f"writer@x-{BLOCKED}",
    f"{BLOCKED}@gmail.com",
    f"writer+{BLOCKED}@gmail.com",
    "writer@blocked-mail.co",
    "writer@second-block.org.cn",
    "not-an-address",
    "",
])
def test_lookalikes_and_other_parts_of_the_address_do_not_match(email):
    assert is_registration_email_blocked(email) is False


@pytest.mark.parametrize("config", ["", "   ", ",", " , ,", "@", "no spaces allowed.com x"])
def test_empty_or_malformed_config_blocks_nothing(monkeypatch, config):
    monkeypatch.setenv("REGISTRATION_BLOCKED_EMAIL_DOMAINS", config)
    assert is_registration_email_blocked(f"writer@{BLOCKED}") is False


def test_unset_config_blocks_nothing(monkeypatch):
    monkeypatch.delenv("REGISTRATION_BLOCKED_EMAIL_DOMAINS")
    assert is_registration_email_blocked(f"writer@{BLOCKED}") is False


@pytest.mark.parametrize("entry", [f"@{BLOCKED}", f"*.{BLOCKED}", f".{BLOCKED}", f"{BLOCKED}.", f"  {BLOCKED.upper()}  "])
def test_common_ways_of_writing_an_entry_are_normalized(monkeypatch, entry):
    monkeypatch.setenv("REGISTRATION_BLOCKED_EMAIL_DOMAINS", f"other.org,{entry}")
    assert is_registration_email_blocked(f"writer@sub.{BLOCKED}") is True
    assert is_registration_email_blocked(f"writer@not{BLOCKED}") is False


def test_copy_is_neutral():
    assert ERROR_MESSAGES["zh"][ErrorCode.AUTH_EMAIL_DOMAIN_NOT_ALLOWED] == "暂不支持使用此邮箱注册，请更换邮箱"
    assert "different email" in ERROR_MESSAGES["en"][ErrorCode.AUTH_EMAIL_DOMAIN_NOT_ALLOWED]


# ---- every entry that can make a new account ----


@pytest.mark.integration
@pytest.mark.parametrize("email", [f"new@{BLOCKED}", f"New@SUB.{BLOCKED.upper()}"])
async def test_register_refuses_before_creating_or_mailing(client: AsyncClient, db_session, mailer, email):
    response = await client.post("/api/auth/register", json=_register_body(email))
    assert response.status_code == 400
    assert response.json()["error_code"] == CODE
    assert BLOCKED not in response.text.lower()
    mailer.assert_not_awaited()
    assert db_session.exec(select(User)).all() == []


@pytest.mark.integration
async def test_register_still_accepts_a_lookalike_domain(client: AsyncClient, db_session, mailer):
    response = await client.post("/api/auth/register", json=_register_body(f"new@not{BLOCKED}"))
    assert response.status_code == 200
    mailer.assert_awaited_once()
    assert len(db_session.exec(select(User)).all()) == 1


@pytest.mark.integration
async def test_register_works_as_before_with_no_config(client: AsyncClient, db_session, mailer, monkeypatch):
    monkeypatch.delenv("REGISTRATION_BLOCKED_EMAIL_DOMAINS")
    response = await client.post("/api/auth/register", json=_register_body(f"new@{BLOCKED}"))
    assert response.status_code == 200


@pytest.mark.integration
async def test_register_gives_the_same_answer_for_a_taken_address(client: AsyncClient, db_session, mailer):
    """An existing account on the domain is not revealed by a different error."""
    _user(db_session, f"taken@{BLOCKED}", verified=True)
    taken = await client.post("/api/auth/register", json=_register_body(f"taken@{BLOCKED}"))
    free = await client.post("/api/auth/register", json=_register_body(f"free@{BLOCKED}"))
    assert taken.status_code == free.status_code == 400
    assert taken.json()["error_code"] == free.json()["error_code"] == CODE


@pytest.mark.integration
async def test_pending_signup_cannot_move_to_a_blocked_address(client: AsyncClient, db_session, mailer):
    user = _user(db_session, f"pending_{uuid4().hex[:6]}@gmail.com", verified=False)
    original = user.email
    response = await client.post("/api/auth/registration-email", json={
        "email": original, "password": "password123", "new_email": f"moved@{BLOCKED}",
    })
    assert response.status_code == 400
    assert response.json()["error_code"] == CODE
    mailer.assert_not_awaited()
    db_session.expire_all()
    assert db_session.get(User, user.id).email == original


@pytest.mark.integration
async def test_oauth_refuses_a_new_account_without_creating_it(client: AsyncClient, db_session, monkeypatch):
    location = await _oauth_callback(client, monkeypatch, f"new@SUB.{BLOCKED}")
    assert location.startswith("http://localhost:5173/register")
    assert f"error_code={CODE}" in location
    assert db_session.exec(select(User)).all() == []


# ---- signups that started before the rule, and old codes ----


@pytest.mark.integration
async def test_old_code_cannot_finish_a_pending_signup(client: AsyncClient, db_session, mailer, monkeypatch):
    user = _user(db_session, f"pending@{BLOCKED}", verified=False)
    verify = AsyncMock(return_value=(True, None))
    monkeypatch.setattr("api.verification.verify_code", verify)
    response = await client.post("/api/auth/verify-email", json={"email": user.email, "code": "123456"})
    assert response.status_code == 400
    assert response.json()["error_code"] == CODE
    assert "access_token" not in response.text
    verify.assert_not_awaited()
    db_session.expire_all()
    assert db_session.get(User, user.id).email_verified is False


@pytest.mark.integration
async def test_pending_signup_gets_no_new_code_and_the_answer_reveals_nothing(client: AsyncClient, db_session, mailer):
    pending = _user(db_session, f"pending@{BLOCKED}", verified=False)
    known = await client.post("/api/auth/resend-verification", json={"email": pending.email})
    unknown = await client.post("/api/auth/resend-verification", json={"email": f"nobody@{BLOCKED}"})
    assert known.status_code == unknown.status_code == 400
    assert known.json()["error_code"] == unknown.json()["error_code"] == CODE
    mailer.assert_not_awaited()


@pytest.mark.integration
async def test_active_account_cannot_change_to_a_blocked_address(client: AsyncClient, db_session):
    user = _user(db_session, f"writer_{uuid4().hex[:6]}@gmail.com", verified=True)
    original = user.email
    token = await _login(client, original)
    response = await client.put(
        "/api/auth/me", json={"email": f"moved@{BLOCKED}"}, headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400
    assert response.json()["error_code"] == CODE
    db_session.expire_all()
    stored = db_session.get(User, user.id)
    assert stored.email == original and stored.email_verified is True


# ---- existing accounts are untouched ----


async def _login(client: AsyncClient, identifier: str) -> str:
    response = await client.post("/api/auth/login", data={"username": identifier, "password": "password123"})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


@pytest.mark.integration
async def test_existing_account_logs_in_reads_and_edits_its_profile(client: AsyncClient, db_session):
    user = _user(db_session, f"existing@{BLOCKED}", verified=True)
    token = await _login(client, user.email)
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
    assert (await client.get("/api/v1/projects", headers=headers)).status_code == 200
    # Saving a profile that repeats its own address is not a new use of the domain.
    resaved = await client.put("/api/auth/me", json={"email": user.email, "avatar_url": "https://x.test/a.png"}, headers=headers)
    assert resaved.status_code == 200
    db_session.expire_all()
    assert db_session.get(User, user.id).email_verified is True
    assert db_session.get(User, user.id).is_active is True


@pytest.mark.integration
async def test_existing_account_can_still_reset_its_password(client: AsyncClient, db_session, monkeypatch):
    user = _user(db_session, f"existing@{BLOCKED}", verified=True)
    request_reset = AsyncMock()
    confirm_reset = AsyncMock()
    monkeypatch.setattr("api.auth.password_reset_service.request_reset", request_reset)
    monkeypatch.setattr("api.auth.password_reset_service.confirm_reset", confirm_reset)
    asked = await client.post("/api/auth/password-reset/request", json={"email": user.email})
    confirmed = await client.post("/api/auth/password-reset/confirm", json={
        "email": user.email, "code": "123456", "new_password": "NewPassword123!",
    })
    assert asked.status_code == 200
    assert confirmed.status_code == 200
    request_reset.assert_awaited_once()
    confirm_reset.assert_awaited_once()


@pytest.mark.integration
async def test_existing_account_signs_in_with_google(client: AsyncClient, db_session, monkeypatch):
    user = _user(db_session, f"existing@{BLOCKED}", verified=True)
    location = await _oauth_callback(client, monkeypatch, user.email)
    assert CODE not in location
    assert len(db_session.exec(select(User)).all()) == 1


@pytest.mark.integration
async def test_deactivated_account_on_the_domain_gets_no_way_back_in(client: AsyncClient, db_session, monkeypatch):
    """The compatibility above is for active accounts; it never overrides is_active."""
    user = _user(db_session, f"inactive@{BLOCKED}", verified=True)
    user.is_active = False
    db_session.commit()
    email = user.email
    login = await client.post("/api/auth/login", data={"username": email, "password": "password123"})
    assert login.status_code in (400, 401, 403)
    assert "access_token" not in login.text
    location = await _oauth_callback(client, monkeypatch, email)
    assert "error_code=ERR_AUTH_INACTIVE_USER" in location
    assert "access_token" not in location
    db_session.expire_all()
    assert db_session.get(User, user.id).is_active is False


async def _oauth_callback(client: AsyncClient, monkeypatch, email: str) -> str:
    from api.oauth import OAUTH_STATE_COOKIE_NAME, _encode_oauth_state

    class _Response:
        def __init__(self, payload: dict):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _Response({"access_token": "synthetic"})

        async def get(self, *args, **kwargs):
            return _Response({"email": email, "name": "Synthetic"})

    monkeypatch.setattr("api.oauth.httpx.AsyncClient", _Client)
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    nonce = uuid4().hex
    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "synthetic", "state": _encode_oauth_state({"nonce": nonce})},
        headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}={nonce}"},
    )
    assert response.status_code in (302, 307)
    return response.headers["location"]


def test_every_user_creation_site_is_covered():
    """A new place that creates an account must add the check here too."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    sites = set()
    for folder in ("api", "services", "agent", "core", "flows"):
        for path in (root / folder).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "User":
                    sites.add(path.relative_to(root).as_posix())
    assert sites == {"api/auth.py", "api/oauth.py"}
