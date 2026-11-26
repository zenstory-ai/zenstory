"""
Tests for OAuth-related API endpoints.
"""

import base64
import json
import urllib.parse

import pytest
from httpx import AsyncClient


def _decode_state(state: str) -> dict:
    padded = state + "=" * (-len(state) % 4)
    raw = base64.urlsafe_b64decode(padded.encode("utf-8"))
    return json.loads(raw.decode("utf-8"))


@pytest.mark.integration
async def test_google_oauth_login_includes_safe_redirect_in_state(client: AsyncClient, monkeypatch):
    """Safe redirect should be preserved in OAuth state parameter."""
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")

    redirect_target = "https://zenstory.ai/projects"
    response = await client.get("/api/auth/google", params={"redirect": redirect_target})

    assert response.status_code in [302, 307]
    location = response.headers.get("location", "")
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    state = params.get("state", [None])[0]

    assert state is not None
    payload = _decode_state(state)
    assert payload.get("redirect") == redirect_target
    assert payload.get("nonce")
    assert "oauth_google_state=" in response.headers.get("set-cookie", "")


@pytest.mark.integration
async def test_google_oauth_login_drops_unsafe_redirect(client: AsyncClient, monkeypatch):
    """Unsafe redirect should be excluded from OAuth state payload."""
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")

    response = await client.get("/api/auth/google", params={"redirect": "https://evil.example.com/cb"})

    assert response.status_code in [302, 307]
    location = response.headers.get("location", "")
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    state = params.get("state", [None])[0]
    assert state is not None
    payload = _decode_state(state)
    assert payload.get("redirect") is None
    assert payload.get("nonce")


@pytest.mark.integration
async def test_google_oauth_login_drops_redirect_with_userinfo(client: AsyncClient, monkeypatch):
    """Redirect URLs containing userinfo should be excluded from state payload."""
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")

    response = await client.get("/api/auth/google", params={"redirect": "https://user@zenstory.ai/cb"})

    assert response.status_code in [302, 307]
    location = response.headers.get("location", "")
    parsed = urllib.parse.urlparse(location)
    params = urllib.parse.parse_qs(parsed.query)
    state = params.get("state", [None])[0]
    assert state is not None
    payload = _decode_state(state)
    assert payload.get("redirect") is None


@pytest.mark.integration
async def test_google_oauth_login_not_configured_redirects_to_frontend_error(
    client: AsyncClient,
    monkeypatch,
):
    """Browser OAuth initiation failures should return users to the frontend, not JSON."""
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setattr("api.oauth.SSO_ALLOWED_REDIRECT_DOMAINS", ["localhost"])

    redirect_target = "http://localhost:5173/dashboard"
    response = await client.get(
        "/api/auth/google",
        params={"redirect": redirect_target},
    )

    assert response.status_code in [302, 307]
    parsed = urllib.parse.urlparse(response.headers.get("location", ""))
    params = urllib.parse.parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "http://localhost:5173/auth/callback"
    assert params["error_code"] == ["ERR_INTERNAL_SERVER_ERROR"]
    assert params["redirect"] == [redirect_target]
    assert response.headers.get("content-type") != "application/json"


@pytest.mark.integration
async def test_google_oauth_callback_missing_state_redirects_to_frontend_error(
    client: AsyncClient,
    monkeypatch,
):
    """Browser OAuth callback failures should return users to the frontend, not JSON."""
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")

    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "dummy-code"},
    )

    assert response.status_code in [302, 307]
    parsed = urllib.parse.urlparse(response.headers.get("location", ""))
    params = urllib.parse.parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "http://localhost:5173/auth/callback"
    assert params["error_code"] == ["ERR_AUTH_TOKEN_INVALID"]
    assert params["error_stage"] == ["missing_state"]
    assert params["request_id"] == [response.headers["x-request-id"]]
    assert "dummy-code" not in response.headers["location"]
    assert response.headers.get("content-type") != "application/json"


@pytest.mark.integration
async def test_google_oauth_callback_token_exchange_failure_redirects_to_frontend_error(
    client: AsyncClient,
    monkeypatch,
):
    """Post-nonce Google failures should also redirect to the frontend callback error UI."""
    from api.oauth import OAUTH_STATE_COOKIE_NAME, _encode_oauth_state

    class _DummyResponse:
        status_code = 500

        def json(self):
            return {"error": "temporarily_unavailable"}

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _DummyResponse()

    monkeypatch.setattr("api.oauth.httpx.AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:5173")
    monkeypatch.setattr("api.oauth.SSO_ALLOWED_REDIRECT_DOMAINS", ["localhost"])

    nonce = "oauth-token-failure-nonce"
    redirect_target = "http://localhost:5173/dashboard"
    state = _encode_oauth_state({"nonce": nonce, "redirect": redirect_target})

    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "dummy-code", "state": state},
        headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}={nonce}"},
    )

    assert response.status_code in [302, 307]
    parsed = urllib.parse.urlparse(response.headers.get("location", ""))
    params = urllib.parse.parse_qs(parsed.query)

    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == "http://localhost:5173/auth/callback"
    assert params["error_code"] == ["ERR_AUTH_TOKEN_INVALID"]
    assert params["redirect"] == [redirect_target]
    assert "oauth_google_state=" in response.headers.get("set-cookie", "")


@pytest.mark.integration
async def test_google_oauth_callback_bootstraps_subscription_and_quota(
    client: AsyncClient, db_session, monkeypatch
):
    """New OAuth users should get free subscription + quota records."""
    monkeypatch.setenv("FIRST_WORK_ONBOARDING_ROLLOUT_PERCENT", "100")
    from sqlmodel import select

    from api.oauth import OAUTH_STATE_COOKIE_NAME, _encode_oauth_state
    from models import (
        ACTIVATION_EVENT_SIGNUP_SUCCESS,
        ActivationEvent,
        RefreshTokenRecord,
        User,
    )
    from models.subscription import SubscriptionPlan, UsageQuota, UserSubscription
    from services.core.auth_service import TOKEN_TYPE_REFRESH, verify_token

    class _DummyResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _DummyResponse(200, {"access_token": "google-access-token"})

        async def get(self, *args, **kwargs):
            return _DummyResponse(200, {
                "email": "oauth_bootstrap@example.com",
                "name": "OAuth Bootstrap User",
                "picture": "https://example.com/avatar.png",
            })

    monkeypatch.setattr("api.oauth.httpx.AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")

    free_plan = db_session.exec(
        select(SubscriptionPlan).where(SubscriptionPlan.name == "free")
    ).first()
    if free_plan is None:
        free_plan = SubscriptionPlan(
            name="free",
            display_name="Free",
            display_name_en="Free",
            price_monthly_cents=0,
            price_yearly_cents=0,
            features={"ai_conversations_per_day": 10},
            is_active=True,
        )
        db_session.add(free_plan)
        db_session.commit()
        db_session.refresh(free_plan)

    nonce = "oauth-test-nonce"
    state = _encode_oauth_state({"nonce": nonce})

    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "dummy-code", "state": state},
        headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}={nonce}"},
    )
    assert response.status_code in [302, 307]

    user = db_session.exec(
        select(User).where(User.email == "oauth_bootstrap@example.com")
    ).first()
    assert user is not None
    from models import WorkspaceOnboarding

    assert db_session.get(WorkspaceOnboarding, user.id).status == "pending"

    subscription = db_session.exec(
        select(UserSubscription).where(UserSubscription.user_id == user.id)
    ).first()
    assert subscription is not None
    assert subscription.status == "active"
    assert subscription.plan_id == free_plan.id

    quota = db_session.exec(
        select(UsageQuota).where(UsageQuota.user_id == user.id)
    ).first()
    assert quota is not None

    # Refresh token should be rotation-compatible (jti + family_id) and persisted.
    location = response.headers.get("location", "")
    parsed = urllib.parse.urlparse(location)
    fragment_params = urllib.parse.parse_qs(parsed.fragment)
    refresh_token = fragment_params.get("refresh_token", [None])[0]
    assert refresh_token is not None

    payload = verify_token(refresh_token, expected_type=TOKEN_TYPE_REFRESH)
    assert payload is not None
    token_jti = payload.get("jti")
    family_id = payload.get("family_id")
    assert token_jti
    assert family_id

    record = db_session.exec(
        select(RefreshTokenRecord).where(
            RefreshTokenRecord.token_jti == token_jti,
            RefreshTokenRecord.user_id == user.id,
        )
    ).first()
    assert record is not None
    assert record.family_id == family_id
    assert record.revoked_at is None

    # New Google users count as signups: backend funnel + frontend flag.
    assert fragment_params.get("new_user") == ["1"]
    signup_event = db_session.exec(
        select(ActivationEvent).where(
            ActivationEvent.user_id == user.id,
            ActivationEvent.event_name == ACTIVATION_EVENT_SIGNUP_SUCCESS,
        )
    ).first()
    assert signup_event is not None
    assert signup_event.event_metadata["source"] == "google_oauth"


@pytest.mark.integration
async def test_google_oauth_callback_matches_existing_user_email_case_insensitively(
    client: AsyncClient, db_session, monkeypatch
):
    """OAuth login must not create a duplicate account when Google returns mixed-case email."""
    from sqlmodel import select

    from api.oauth import OAUTH_STATE_COOKIE_NAME, _encode_oauth_state
    from models import User
    from services.core.auth_service import hash_password

    class _DummyResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _DummyResponse(200, {"access_token": "google-access-token"})

        async def get(self, *args, **kwargs):
            return _DummyResponse(
                200,
                {
                    "email": "OAuthExisting@Example.COM",
                    "name": "OAuth Existing User",
                    "picture": "https://example.com/avatar.png",
                },
            )

    existing_user = User(
        username="oauth_existing_user",
        email="oauthexisting@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(existing_user)
    db_session.commit()
    db_session.refresh(existing_user)

    monkeypatch.setattr("api.oauth.httpx.AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")

    nonce = "oauth-existing-case-nonce"
    state = _encode_oauth_state({"nonce": nonce})

    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "dummy-code", "state": state},
        headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}={nonce}"},
    )

    assert response.status_code in [302, 307]

    users = db_session.exec(select(User)).all()
    assert len(users) == 1
    db_session.refresh(existing_user)
    assert existing_user.email == "oauthexisting@example.com"
    assert existing_user.avatar_url == "https://example.com/avatar.png"
    # Returning users are not signups.
    fragment = urllib.parse.parse_qs(urllib.parse.urlparse(response.headers["location"]).fragment)
    assert "new_user" not in fragment
    assert fragment.get("access_token")


@pytest.mark.integration
async def test_google_oauth_callback_rejects_inactive_existing_user_before_updates_or_tokens(
    client: AsyncClient, db_session, monkeypatch
):
    from sqlmodel import select

    from api.oauth import OAUTH_STATE_COOKIE_NAME, _encode_oauth_state
    from models import RefreshTokenRecord, User
    from services.core.auth_service import hash_password

    class _DummyResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _DummyResponse(200, {"access_token": "google-access-token"})

        async def get(self, *args, **kwargs):
            return _DummyResponse(200, {
                "email": "inactive-oauth@example.com",
                "name": "Mutated Name",
                "picture": "https://example.com/new-avatar.png",
            })

    user = User(
        username="inactive-oauth",
        email="inactive-oauth@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=False,
        avatar_url=None,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    monkeypatch.setattr("api.oauth.httpx.AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
    nonce = "inactive-oauth-nonce"
    state = _encode_oauth_state({"nonce": nonce})

    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "dummy-code", "state": state},
        headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}={nonce}"},
    )

    assert response.status_code in [302, 307]
    location = urllib.parse.urlparse(response.headers["location"])
    assert urllib.parse.parse_qs(location.query)["error_code"] == ["ERR_AUTH_INACTIVE_USER"]
    assert not urllib.parse.parse_qs(location.fragment).get("access_token")
    db_session.refresh(user)
    assert user.username == "inactive-oauth"
    assert user.avatar_url is None
    assert db_session.exec(
        select(RefreshTokenRecord).where(RefreshTokenRecord.user_id == user.id)
    ).first() is None


@pytest.mark.integration
async def test_google_oauth_callback_preserves_existing_user_email_casing(
    client: AsyncClient, db_session, monkeypatch
):
    """OAuth login must not rewrite a legacy mixed-case email to lowercase.

    Regression: lowercasing an existing account's email on login could collide
    with the case-sensitive UNIQUE(email) constraint and surface as a 500.
    """
    from sqlmodel import select

    from api.oauth import OAUTH_STATE_COOKIE_NAME, _encode_oauth_state
    from models import User
    from services.core.auth_service import hash_password

    class _DummyResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _DummyResponse(200, {"access_token": "google-access-token"})

        async def get(self, *args, **kwargs):
            return _DummyResponse(
                200,
                {
                    "email": "casing-oauth@example.com",
                    "name": "Casing OAuth User",
                    "picture": "https://example.com/avatar.png",
                },
            )

    existing_user = User(
        username="casing_oauth_user",
        email="Casing-OAuth@Example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(existing_user)
    db_session.commit()
    db_session.refresh(existing_user)
    existing_user_id = existing_user.id

    monkeypatch.setattr("api.oauth.httpx.AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr(
        "api.oauth.GOOGLE_REDIRECT_URI",
        "http://localhost:8000/api/auth/google/callback",
    )

    nonce = "oauth-casing-preserve-nonce"
    state = _encode_oauth_state({"nonce": nonce})

    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "dummy-code", "state": state},
        headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}={nonce}"},
    )

    assert response.status_code in [302, 307]

    users = db_session.exec(select(User)).all()
    assert len(users) == 1
    db_session.expire_all()
    refreshed = db_session.get(User, existing_user_id)
    # Stored casing is preserved (no destructive lowercase rewrite); the rest
    # of the existing-user update path (avatar) still runs.
    assert refreshed.email == "Casing-OAuth@Example.com"
    assert refreshed.avatar_url == "https://example.com/avatar.png"


@pytest.mark.integration
async def test_google_oauth_new_user_login_survives_activation_record_failure(
    client: AsyncClient, db_session, monkeypatch
):
    """A failing activation write must not block a new Google user's login."""
    from sqlmodel import select

    from api.oauth import OAUTH_STATE_COOKIE_NAME, _encode_oauth_state
    from models import User

    class _DummyResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _DummyResponse(200, {"access_token": "google-access-token"})

        async def get(self, *args, **kwargs):
            return _DummyResponse(200, {"email": "oauth_activation_fail@example.com", "name": "New"})

    def _boom(*args, **kwargs):
        raise RuntimeError("activation table unavailable")

    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "true")
    monkeypatch.setattr("api.oauth.httpx.AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
    monkeypatch.setattr("api.oauth.activation_event_service.record_once", _boom)

    nonce = "oauth-activation-fail-nonce"
    state = _encode_oauth_state({"nonce": nonce})
    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "dummy-code", "state": state},
        headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}={nonce}"},
    )

    assert response.status_code in [302, 307]
    fragment = urllib.parse.parse_qs(urllib.parse.urlparse(response.headers["location"]).fragment)
    assert fragment.get("access_token")
    assert fragment.get("new_user") == ["1"]
    assert db_session.exec(
        select(User).where(User.email == "oauth_activation_fail@example.com")
    ).first() is not None


@pytest.mark.integration
async def test_google_oauth_login_normalizes_lang_param_in_state(client: AsyncClient, monkeypatch):
    """Supported lang values are normalized into OAuth state; missing or invalid values are omitted."""
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")

    def _state_payload(resp) -> dict:
        assert resp.status_code in [302, 307]
        parsed = urllib.parse.urlparse(resp.headers.get("location", ""))
        state = urllib.parse.parse_qs(parsed.query).get("state", [None])[0]
        assert state is not None
        return _decode_state(state)

    en_resp = await client.get("/api/auth/google", params={"lang": "en-US"})
    assert _state_payload(en_resp).get("lang") == "en"

    zh_resp = await client.get("/api/auth/google", params={"lang": "zh-CN"})
    assert _state_payload(zh_resp).get("lang") == "zh"

    invalid_resp = await client.get("/api/auth/google", params={"lang": "fr"})
    assert "lang" not in _state_payload(invalid_resp)

    omitted_resp = await client.get("/api/auth/google")
    assert "lang" not in _state_payload(omitted_resp)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("state_extra", "email", "expected_lang"),
    [
        ({"lang": "en-US"}, "oauth_lang_en@example.com", "en"),
        ({}, "oauth_lang_none@example.com", None),
        ({"lang": "ja"}, "oauth_lang_invalid@example.com", None),
    ],
)
async def test_google_oauth_callback_persists_ui_language_for_new_user(
    client: AsyncClient,
    db_session,
    monkeypatch,
    state_extra: dict[str, str],
    email: str,
    expected_lang: str | None,
):
    """New OAuth users persist normalized ui_language when provided; missing/invalid values are ignored."""
    from sqlmodel import select

    from api.oauth import OAUTH_STATE_COOKIE_NAME, _encode_oauth_state
    from models import User
    from services.features.user_preference_service import get_ui_language

    class _DummyResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _DummyResponse(200, {"access_token": "google-access-token"})

        async def get(self, *args, **kwargs):
            return _DummyResponse(200, {"email": email, "name": "OAuth Lang User"})

    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "true")
    monkeypatch.setattr("api.oauth.httpx.AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")

    nonce = f"oauth-lang-nonce-{email}"
    state = _encode_oauth_state({"nonce": nonce, **state_extra})
    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "dummy-code", "state": state},
        headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}={nonce}"},
    )
    assert response.status_code in [302, 307]
    fragment = urllib.parse.parse_qs(urllib.parse.urlparse(response.headers["location"]).fragment)
    assert fragment.get("new_user") == ["1"]

    user = db_session.exec(select(User).where(User.email == email)).first()
    assert user is not None
    assert get_ui_language(db_session, user.id) == expected_lang


@pytest.mark.integration
async def test_google_oauth_callback_does_not_overwrite_existing_user_ui_language(
    client: AsyncClient, db_session, monkeypatch
):
    """Returning OAuth users keep their saved ui_language regardless of OAuth state lang."""
    from api.oauth import OAUTH_STATE_COOKIE_NAME, _encode_oauth_state
    from models import User
    from services.core.auth_service import hash_password
    from services.features.user_preference_service import get_ui_language, set_ui_language

    existing = User(
        username="oauth_existing_lang_user",
        email="oauth_existing_lang@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(existing)
    db_session.commit()
    db_session.refresh(existing)
    set_ui_language(db_session, existing.id, "zh")
    db_session.commit()

    class _DummyResponse:
        def __init__(self, status_code: int, payload: dict):
            self.status_code = status_code
            self._payload = payload

        def json(self):
            return self._payload

    class _DummyAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _DummyResponse(200, {"access_token": "google-access-token"})

        async def get(self, *args, **kwargs):
            return _DummyResponse(
                200,
                {"email": "oauth_existing_lang@example.com", "name": "Existing User"},
            )

    monkeypatch.setattr("api.oauth.httpx.AsyncClient", _DummyAsyncClient)
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setattr("api.oauth.GOOGLE_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setattr("api.oauth.GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")

    nonce = "oauth-existing-lang-nonce"
    state = _encode_oauth_state({"nonce": nonce, "lang": "en"})
    response = await client.get(
        "/api/auth/google/callback",
        params={"code": "dummy-code", "state": state},
        headers={"Cookie": f"{OAUTH_STATE_COOKIE_NAME}={nonce}"},
    )
    assert response.status_code in [302, 307]
    db_session.expire_all()
    assert get_ui_language(db_session, existing.id) == "zh"
