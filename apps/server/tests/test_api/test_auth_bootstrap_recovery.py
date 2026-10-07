"""Actual failed SQL must not invalidate a best-effort signup continuation."""

import urllib.parse
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from api.oauth import OAUTH_STATE_COOKIE_NAME, _encode_oauth_state
from config.datetime_utils import utcnow
from main import app
from models import RefreshTokenRecord, User
from models.subscription import UsageQuota


@pytest.mark.parametrize("flow", ["password", "oauth-new", "oauth-existing"])
async def test_auth_bootstrap_sql_failure_keeps_committed_user_and_continuation(
    client, db_session, monkeypatch, flow,
):
    email = f"bootstrap-fail-{flow}@example.com"
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "true")
    observed = {"poisoned": False, "mail": 0}

    async def send_mail(_email, language="zh"):
        observed["mail"] += 1
        return True, None

    def failed_bootstrap(session, user_id, source):
        now = utcnow()
        session.add_all([
            UsageQuota(user_id=user_id, period_start=now, period_end=now + timedelta(days=1)),
            UsageQuota(user_id=user_id, period_start=now, period_end=now + timedelta(days=1)),
        ])
        try:
            session.flush()
        except IntegrityError:
            observed["poisoned"] = not session.is_active
            raise
        pytest.fail("test's real unique quota violation did not occur")

    monkeypatch.setattr("api.auth.send_verification_code", send_mail)
    target = "api.auth" if flow == "password" else "api.oauth"
    monkeypatch.setattr(f"{target}._ensure_user_free_subscription_and_quota", failed_bootstrap)
    if flow == "oauth-existing":
        db_session.add(User(username="existing-bootstrap", email=email,
                            hashed_password="", email_verified=True, is_active=True))
        db_session.commit()

    class ProviderClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return ProviderResponse({"access_token": "local-provider-boundary"})

        async def get(self, *args, **kwargs):
            return ProviderResponse({"email": email, "verified_email": True,
                                     "name": "Bootstrap User", "picture": ""})

    class ProviderResponse:
        status_code = 200

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    # Retain the already-imported real HTTP client before mocking the provider namespace.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        if flow == "password":
            response = await http.post("/api/auth/register", json={
                "username": "password-bootstrap", "email": email,
                "password": "localpassword123", "language": "en",
            })
        else:
            monkeypatch.setattr("api.oauth.httpx.AsyncClient", ProviderClient)
            monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "local-client")
            monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_SECRET", "local-secret")
            monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://test/api/auth/google/callback")
            nonce = "local-bootstrap-nonce"
            state = _encode_oauth_state({"nonce": nonce})
            response = await http.get("/api/auth/google/callback",
                                      params={"code": "local-code", "state": state},
                                      headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}={nonce}"})
    assert observed["poisoned"] is True
    with Session(db_session.get_bind()) as fresh:
        user = fresh.exec(select(User).where(User.email == email)).one()
        assert fresh.exec(select(UsageQuota).where(UsageQuota.user_id == user.id)).all() == []
        refresh = fresh.exec(select(RefreshTokenRecord).where(RefreshTokenRecord.user_id == user.id)).all()
        if flow == "password":
            assert response.status_code == 200
            assert response.json()["verification_sent"] is True
            assert observed["mail"] == 1
            assert refresh == []
        else:
            assert response.status_code in [302, 307]
            fragment = urllib.parse.parse_qs(urllib.parse.urlparse(response.headers["location"]).fragment)
            assert fragment.get("access_token") and fragment.get("refresh_token")
            assert len(refresh) == 1
            assert user.email_verified is True
