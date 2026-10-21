"""Signup acquisition: what is kept from the web app, and the admin cohort view."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import ACTIVATION_EVENT_SIGNUP_SUCCESS, ActivationEvent, User
from services.usage.acquisition_service import classify_channel, normalize_acquisition
from tests.test_api.test_admin_growth import _headers, _naive, _order, _usage, _user

NOW = datetime(2026, 10, 7, 8, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "channel"),
    [
        ({"referrer_host": "www.google.com"}, "search"),
        ({"referrer_host": "chatgpt.com"}, "ai"),
        ({"referrer_host": "www.zhihu.com"}, "social"),
        ({"referrer_host": "zenstory.ai", "source": "org_header"}, "org_site"),
        ({"source": "org_closing"}, "org_site"),
        ({"utm_source": "newsletter", "utm_medium": "email"}, "campaign"),
        ({"utm_source": "baidu", "utm_medium": "cpc", "referrer_host": "www.baidu.com"}, "paid"),
        ({"referrer_host": "blog.example.com"}, "referral"),
        ({}, "direct"),
    ],
)
def test_channel_follows_campaign_then_referrer(raw, channel):
    assert classify_channel(normalize_acquisition(raw)) == channel


def test_only_codes_hosts_and_paths_are_kept():
    normalized = normalize_acquisition({
        "source": "Org_Header",
        "utm_source": "has spaces",
        "utm_campaign": "x" * 200,
        "referrer_host": "WWW.Google.COM.",
        "landing_path": "/register?email=a@b.c#x",
        "email": "writer@example.com",
    })
    assert normalized == {
        "source": "org_header",
        "referrer_host": "www.google.com",
        "landing_path": "/register",
        "channel": "search",
    }
    assert normalize_acquisition("not a dict") is None


def _signup(session: Session, user: User, acquisition: dict | None) -> None:
    session.add(ActivationEvent(
        user_id=user.id,
        event_name=ACTIVATION_EVENT_SIGNUP_SUCCESS,
        event_metadata={"source": "register_api", **({"acquisition": acquisition} if acquisition else {})},
        created_at=user.created_at,
    ))


@pytest.mark.integration
async def test_acquisition_view_counts_the_signup_cohort_by_channel_and_source(
    client: AsyncClient, db_session: Session, monkeypatch
):
    monkeypatch.setattr("services.usage.acquisition_service.utcnow", lambda: NOW)
    admin = _user(db_session, "acq_admin", NOW - timedelta(days=100), admin=True)
    searcher = _user(db_session, "acq_search", NOW - timedelta(days=2))
    searcher_2 = _user(db_session, "acq_search_2", NOW - timedelta(days=1))
    from_ai = _user(db_session, "acq_ai", NOW - timedelta(hours=3))
    legacy = _user(db_session, "acq_legacy", NOW - timedelta(days=3))
    old = _user(db_session, "acq_old", NOW - timedelta(days=40))
    google = normalize_acquisition({"referrer_host": "www.google.com"})
    _signup(db_session, searcher, google)
    _signup(db_session, searcher_2, google)
    _signup(db_session, from_ai, normalize_acquisition({"referrer_host": "chatgpt.com"}))
    _signup(db_session, legacy, None)
    _signup(db_session, old, google)
    _usage(db_session, searcher, NOW - timedelta(days=1))
    _order(db_session, searcher, "acq-order-1", NOW - timedelta(hours=5))
    db_session.commit()

    response = await client.get("/api/admin/dashboard/acquisition?days=30", headers=await _headers(client, admin))

    assert response.status_code == 200
    payload = response.json()
    assert payload["total_signups"] == 4
    assert payload["attributed_signups"] == 3
    channels = {row["channel"]: row for row in payload["channels"]}
    assert channels["search"] == {
        "channel": "search", "signups": 2, "activated_users": 1, "activation_rate": 0.5,
        "paid_users": 1, "paid_rate": 0.5,
    }
    assert channels["ai"]["signups"] == 1
    assert channels["unknown"]["signups"] == 1
    sources = {(row["channel"], row["source"]): row["signups"] for row in payload["sources"]}
    assert sources == {("search", "www.google.com"): 2, ("ai", "chatgpt.com"): 1, ("unknown", ""): 1}

    invalid = await client.get("/api/admin/dashboard/acquisition?days=8", headers=await _headers(client, admin))
    assert invalid.status_code == 422
    forbidden = await client.get("/api/admin/dashboard/acquisition?days=7", headers=await _headers(client, searcher))
    assert forbidden.status_code == 403


@pytest.mark.integration
async def test_email_signup_records_where_the_account_came_from(client: AsyncClient, db_session: Session, monkeypatch):
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "true")
    monkeypatch.setattr("api.auth.clear_registration_verification", AsyncMock())
    monkeypatch.setattr("api.auth.send_verification_code", AsyncMock(return_value=(True, None)))

    response = await client.post("/api/auth/register", json={
        "email": "acq-writer@example.com",
        "password": "testpassword123",
        "acquisition": {"utm_source": "newsletter", "utm_medium": "email", "landing_path": "/"},
    })

    assert response.status_code == 200, response.text
    user = db_session.exec(select(User).where(User.email == "acq-writer@example.com")).one()
    event = db_session.exec(select(ActivationEvent).where(
        ActivationEvent.user_id == user.id, ActivationEvent.event_name == ACTIVATION_EVENT_SIGNUP_SUCCESS,
    )).one()
    assert event.event_metadata["acquisition"] == {
        "utm_source": "newsletter", "utm_medium": "email", "landing_path": "/", "channel": "campaign",
    }


@pytest.mark.integration
async def test_google_sign_in_carries_acquisition_in_the_oauth_state(client: AsyncClient, monkeypatch):
    import api.oauth as oauth

    monkeypatch.setattr(oauth, "GOOGLE_CLIENT_ID", "client")
    monkeypatch.setattr(oauth, "GOOGLE_REDIRECT_URI", "https://api.example.com/api/auth/google/callback")

    response = await client.get(
        "/api/auth/google",
        params={"acq": json.dumps({"referrer_host": "chatgpt.com", "landing_path": "/register"})},
        follow_redirects=False,
    )

    from urllib.parse import parse_qs, urlparse

    state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    payload = oauth._decode_oauth_state(state)
    assert oauth._parse_acquisition_param(payload[oauth.OAUTH_STATE_ACQUISITION_KEY]) == {
        "referrer_host": "chatgpt.com", "landing_path": "/register", "channel": "ai",
    }
    assert oauth._parse_acquisition_param("{not json") is None
