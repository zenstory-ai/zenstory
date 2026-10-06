"""Browser callback errors before persistence must retain nonce/error contracts."""

import urllib.parse

import pytest
from httpx import ASGITransport, AsyncClient, Response, TimeoutException
from sqlmodel import select

from api.oauth import OAUTH_STATE_COOKIE_NAME, _encode_oauth_state
from core.error_codes import ErrorCode
from main import app
from models import RefreshTokenRecord, User


@pytest.mark.parametrize("stage", ["token", "userinfo"])
@pytest.mark.parametrize("failure", ["timeout", "bad-json", "non-object", "non-200"])
async def test_oauth_provider_failure_redirects_without_persistence(
    client, db_session, monkeypatch, stage, failure,
):
    before_users = db_session.exec(select(User)).all()
    before_refresh = db_session.exec(select(RefreshTokenRecord)).all()
    requests = []

    def result(current):
        requests.append(current)
        if current == stage:
            if failure == "timeout":
                raise TimeoutException("local provider timeout")
            if failure == "bad-json":
                return Response(200, content=b"{invalid", headers={"content-type": "application/json"})
            if failure == "non-object":
                return Response(200, json=["unexpected"])
            return Response(503, json={"error": "local-provider-error"})
        return Response(200, json={"access_token": "local-token"})

    class ProviderClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return result("token")

        async def get(self, *args, **kwargs):
            return result("userinfo")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        monkeypatch.setattr("api.oauth.httpx.AsyncClient", ProviderClient)
        monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "local-client")
        monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_SECRET", "local-secret")
        monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://test/api/auth/google/callback")
        monkeypatch.setenv("FRONTEND_URL", "http://frontend.example")
        monkeypatch.setattr("api.oauth.SSO_ALLOWED_REDIRECT_DOMAINS", ["zenstory.ai"])
        target = "https://zenstory.ai/dashboard"
        nonce = "local-provider-error-nonce"
        state = _encode_oauth_state({"nonce": nonce, "redirect": target})
        response = await http.get("/api/auth/google/callback",
                                  params={"state": state, "code": "local-code"},
                                  headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}={nonce}"})
    assert requests == (["token"] if stage == "token" else ["token", "userinfo"])
    assert db_session.exec(select(User)).all() == before_users
    assert db_session.exec(select(RefreshTokenRecord)).all() == before_refresh
    assert response.status_code in [302, 307]
    location = urllib.parse.urlparse(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == "http://frontend.example/auth/callback"
    query = urllib.parse.parse_qs(location.query)
    assert query["error_code"] == [ErrorCode.AUTH_TOKEN_INVALID]
    assert query["redirect"] == [target]
    assert location.fragment == ""
    cookie = response.headers["set-cookie"].lower()
    assert f"{OAUTH_STATE_COOKIE_NAME}=" in cookie and "max-age=0" in cookie
