from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest

import api.oauth as oauth_module


def test_sanitize_invite_code_normalizes_and_truncates():
    assert oauth_module._sanitize_invite_code(" abcd1234 ") == "ABCD-1234"
    assert oauth_module._sanitize_invite_code("邀请码: abcd-1234 !!!") == "ABCD-1234"
    assert oauth_module._sanitize_invite_code("") is None
    assert oauth_module._sanitize_invite_code("x" * 64) == ("X" * 32)


def test_decode_state_and_redirect_allowlist(monkeypatch):
    encoded = oauth_module._encode_oauth_state({"redirect": "https://zenstory.ai/app", "code": "ABCD-1234"})

    monkeypatch.setattr(oauth_module, "SSO_ALLOWED_REDIRECT_DOMAINS", ["zenstory.ai"])

    assert oauth_module._decode_oauth_state(encoded) == {"redirect": "https://zenstory.ai/app", "code": "ABCD-1234"}
    assert oauth_module._decode_oauth_state("not-valid-base64") is None
    assert oauth_module._is_allowed_redirect_url("https://zenstory.ai/app") is True
    assert oauth_module._is_allowed_redirect_url("https://user:pass@zenstory.ai/evil") is False
    assert oauth_module._is_allowed_redirect_url("javascript:alert(1)") is False


def test_redirect_to_frontend_register_keeps_only_safe_redirect(monkeypatch):
    monkeypatch.setenv("FRONTEND_URL", "https://frontend.example")
    monkeypatch.setattr(oauth_module, "GOOGLE_REDIRECT_URI", "https://backend.example/auth/callback")
    monkeypatch.setattr(oauth_module, "SSO_ALLOWED_REDIRECT_DOMAINS", ["frontend.example"])

    response = oauth_module._redirect_to_frontend_register(
        error_code="ERR_OAUTH_STATE_INVALID",
        redirect="https://frontend.example/dashboard",
        invite_code="ABCD-1234",
    )

    parsed = urlparse(response.headers["location"])
    params = parse_qs(parsed.query)

    assert parsed.scheme == "https"
    assert parsed.netloc == "frontend.example"
    assert params["error_code"] == ["ERR_OAUTH_STATE_INVALID"]
    assert params["code"] == ["ABCD-1234"]
    assert params["redirect"] == ["https://frontend.example/dashboard"]


def test_decode_state_drops_non_string_values():
    decoded = oauth_module._decode_oauth_state(
        oauth_module._encode_oauth_state(  # type: ignore[arg-type]
            {"redirect": "https://zenstory.ai/app", "nonce": "abc", "count": 3}
        )
    )

    assert decoded == {"redirect": "https://zenstory.ai/app", "nonce": "abc"}


def test_should_use_secure_cookie_depends_on_redirect_uri(monkeypatch):
    monkeypatch.setattr(oauth_module, "GOOGLE_REDIRECT_URI", "https://backend.example/api/auth/google/callback")
    assert oauth_module._should_use_secure_cookie() is True

    monkeypatch.setattr(oauth_module, "GOOGLE_REDIRECT_URI", "http://localhost:8000/api/auth/google/callback")
    assert oauth_module._should_use_secure_cookie() is False


@pytest.mark.parametrize(("url", "allowed", "expected"), [
    ("https://zenstory.ai/app", ["zenstory.ai"], True),
    ("https://app.zenstory.ai/app", ["zenstory.ai"], True),
    ("http://zenstory.ai/app", ["zenstory.ai"], False),
    ("http://app.zenstory.ai/app", ["zenstory.ai"], False),
    ("https://zenstory.ai:invalid/app", ["zenstory.ai"], False),
    ("https://zenstory.ai:99999/app", ["zenstory.ai"], False),
    ("http://localhost:8000/app", ["localhost"], True),
    ("http://127.0.0.1:8000/app", ["127.0.0.1"], True),
    ("http://localhost:8000/app", ["zenstory.ai"], False),
    ("http://localhost:8001/app", ["localhost:8000"], False),
])
def test_redirect_transport_and_port_allowlist(monkeypatch, url, allowed, expected):
    monkeypatch.setattr(oauth_module, "SSO_ALLOWED_REDIRECT_DOMAINS", allowed)
    assert oauth_module._is_allowed_redirect_url(url) is expected
