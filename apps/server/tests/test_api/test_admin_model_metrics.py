"""Admin per-model call metrics: success rate, latency, time to first token, decode speed.

Synthetic ledger rows only; no model calls. One row is one provider attempt.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import User
from services.core.auth_service import hash_password
from services.usage import admin_usage_service
from services.usage.admin_growth_service import get_growth_dashboard
from services.usage.llm_usage_service import CallTiming, UsageTokens, build_usage_event

# Wednesday 2026-10-07 10:00 Beijing; "today" starts 2026-10-06 16:00 UTC.
NOW = datetime(2026, 10, 7, 2, 0, tzinfo=UTC)
TODAY = datetime(2026, 10, 7, 1, 0)


@pytest.fixture(autouse=True)
def frozen_now(monkeypatch):
    monkeypatch.setattr(admin_usage_service, "utcnow", lambda: NOW)


def _user(db_session: Session, name: str, *, admin: bool = False) -> User:
    user = User(
        username=name,
        email=f"{name}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_superuser=admin,
        created_at=datetime(2026, 10, 1),
        updated_at=datetime(2026, 10, 1),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


async def _headers(client: AsyncClient, username: str) -> dict[str, str]:
    response = await client.post("/api/auth/login", data={"username": username, "password": "password123"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _attempt(
    db_session: Session,
    user: User,
    *,
    outcome: str | None,
    duration: int | None = None,
    ttft: int | None = None,
    out: int = 0,
    miss: int = 0,
    model: str = "deepseek-flash",
    source: str = "agent",
    at: datetime = TODAY,
) -> None:
    timing = CallTiming(outcome, duration or 0, ttft) if outcome is not None else None
    db_session.add(
        build_usage_event(
            user_id=user.id,
            source=source,
            model=model,
            tokens=UsageTokens(cache_miss=miss, output=out),
            occurred_at=at,
            timing=timing,
        )
    )
    db_session.commit()


@pytest.fixture
def ledger(db_session: Session):
    admin = _user(db_session, "metrics_admin", admin=True)
    alice = _user(db_session, "metrics_alice")
    dora = _user(db_session, "metrics_dora")  # only ever had a failed attempt
    # Streaming agent calls on deepseek-flash.
    _attempt(db_session, alice, outcome="success", duration=10_000, ttft=2_000, out=800, miss=100)
    _attempt(db_session, alice, outcome="success", duration=20_000, ttft=4_000, out=1_600, miss=100)
    _attempt(db_session, alice, outcome="success", duration=3_000, out=100, miss=100)  # no first-token time
    _attempt(db_session, alice, outcome="success", duration=5_000, ttft=1_000)  # provider sent no usage
    _attempt(db_session, alice, outcome="error", duration=45_000)
    _attempt(db_session, alice, outcome="timeout", duration=120_000)
    _attempt(db_session, alice, outcome="cancelled", duration=7_000, ttft=500)
    _attempt(db_session, dora, outcome="error", duration=1_000)
    # Written before the metric columns existed: tokens are real, the outcome is unknown.
    _attempt(db_session, alice, outcome=None, out=500, miss=100)
    # A routing call that failed over: deepseek attempt failed, MiniMax answered.
    _attempt(db_session, alice, outcome="error", duration=2_000, source="router")
    _attempt(db_session, alice, outcome="success", duration=900, out=50, miss=10, model="MiniMax-M3.1-Flash", source="router")
    # Yesterday: outside "today".
    _attempt(db_session, alice, outcome="error", duration=1_000, at=TODAY - timedelta(days=1))
    return admin


@pytest.mark.integration
async def test_model_metrics_formulas_and_samples(client: AsyncClient, db_session: Session, ledger):
    response = await client.get("/api/admin/usage/models?window=today", headers=await _headers(client, ledger.username))
    assert response.status_code == 200
    payload = response.json()
    assert payload["source"] is None
    deepseek, minimax = payload["items"]
    assert deepseek["model"] == "deepseek-flash"
    # Attempts, not logical requests: the failed-over routing attempt counts for deepseek.
    assert {key: deepseek[key] for key in ("attempts", "success", "error", "timeout", "cancelled", "not_recorded")} == {
        "attempts": 10, "success": 4, "error": 3, "timeout": 1, "cancelled": 1, "not_recorded": 1,
    }
    # 4 / (4 + 3 + 1): cancelled and not-recorded attempts are not in the denominator.
    assert deepseek["success_rate"] == 0.5
    # Answered attempts only, nearest rank over [3000, 5000, 10000, 20000].
    assert deepseek["latency_ms"] == {"p50": 5_000, "p95": 20_000, "samples": 4}
    # Only streaming attempts with a first output delta: [1000, 2000, 4000].
    assert deepseek["ttft_ms"] == {"p50": 2_000, "p95": 4_000, "samples": 3}
    # (800 + 1600) output tokens / (8 s + 16 s) generation; the no-usage call is excluded.
    assert deepseek["decode_tps"] == {"value": 100.0, "samples": 2}
    assert deepseek["output_tokens"] == 3_000
    assert deepseek["cache_miss_tokens"] == 400

    assert minimax["model"] == "MiniMax-M3.1-Flash"
    assert minimax["success_rate"] == 1.0
    assert minimax["latency_ms"] == {"p50": 900, "p95": 900, "samples": 1}
    # Non-streaming: no first token, no decode speed (N/A, not zero).
    assert minimax["ttft_ms"] == {"p50": None, "p95": None, "samples": 0}
    assert minimax["decode_tps"] == {"value": None, "samples": 0}

    totals = payload["totals"]
    assert totals["attempts"] == 11
    # Pooled counts (5 / 9), not an average of the per-model rates.
    assert totals["success_rate"] == round(5 / 9, 4)
    # Pooled samples [900, 3000, 5000, 10000, 20000], not an average of percentiles.
    assert totals["latency_ms"] == {"p50": 5_000, "p95": 20_000, "samples": 5}


@pytest.mark.integration
async def test_model_metrics_source_filter_and_empty_window(client: AsyncClient, db_session: Session, ledger):
    headers = await _headers(client, ledger.username)
    agent_only = (await client.get("/api/admin/usage/models?window=today&source=agent", headers=headers)).json()
    assert agent_only["source"] == "agent"
    assert [item["model"] for item in agent_only["items"]] == ["deepseek-flash"]
    assert agent_only["items"][0]["error"] == 2
    assert agent_only["items"][0]["success_rate"] == round(4 / 7, 4)

    # Nothing recorded: no rows, and the totals say N/A instead of 0 % or 0 ms.
    empty = (await client.get("/api/admin/usage/models?window=today&source=polish", headers=headers)).json()
    assert empty["items"] == []
    assert empty["totals"]["attempts"] == 0
    assert empty["totals"]["success_rate"] is None
    assert empty["totals"]["latency_ms"] == {"p50": None, "p95": None, "samples": 0}

    assert (await client.get("/api/admin/usage/models?source=unknown", headers=headers)).status_code == 422


@pytest.mark.integration
async def test_model_metrics_require_superuser(client: AsyncClient, db_session: Session, ledger):
    assert (await client.get("/api/admin/usage/models")).status_code == 401
    regular = await _headers(client, "metrics_alice")
    assert (await client.get("/api/admin/usage/models", headers=regular)).status_code == 403


@pytest.mark.integration
async def test_attempt_rows_without_tokens_leave_usage_cost_and_growth_unchanged(
    client: AsyncClient, db_session: Session, ledger
):
    headers = await _headers(client, ledger.username)
    summary = (await client.get("/api/admin/usage/summary?window=today", headers=headers)).json()
    # Rows with tokens only (3 answered with usage, the historical row, MiniMax), as before.
    assert summary["totals"]["calls"] == 5
    assert summary["totals"]["users"] == 1
    users = (await client.get("/api/admin/usage/users?window=today", headers=headers)).json()
    assert [row["username"] for row in users["items"]] == ["metrics_alice"]

    growth = get_growth_dashboard(db_session, days=7, now=NOW)
    # Dora only had a failed attempt: not an AI-active or activated author.
    assert growth["current"]["metrics"]["ai_active_users"] == 1
    assert growth["current"]["metrics"]["cohort_activated_users"] == 1
