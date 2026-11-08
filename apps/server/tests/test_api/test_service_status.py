"""Public Agent status uses synthetic provider attempts, never user payloads."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError

from models import User
from models.llm_usage import LLMUsageEvent
from services.infra import service_status

NOW = datetime(2026, 11, 7, 16, 15, tzinfo=UTC)  # Beijing Nov 8, 00:15


@pytest.fixture(autouse=True)
def fresh_status(monkeypatch):
    from api import status

    monkeypatch.setattr(service_status, "utcnow", lambda: NOW)
    monkeypatch.setattr(status, "_status_cache", service_status.StatusCache())


@pytest.fixture
def attempt(db_session):
    user = User(username="status_synthetic", email="status@example.com", hashed_password="unused")
    db_session.add(user)
    db_session.commit()

    def add(outcome="success", *, source="agent", at=None, backfilled=False, **fields):
        event = LLMUsageEvent(
            user_id=user.id,
            project_id="private-project",
            correlation_id="private-run",
            source=source,
            model="private-provider-model",
            price_band="peak",
            pricing_version="synthetic",
            outcome=outcome,
            occurred_at=(at or NOW - timedelta(minutes=5)).replace(tzinfo=None),
            is_backfilled=backfilled,
            **fields,
        )
        db_session.add(event)
        db_session.commit()

    return add


async def test_anonymous_empty_history_is_unknown_not_uptime(client):
    response = await client.get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unknown"
    assert data["timezone"] == "Asia/Shanghai"
    assert data["window_minutes"] == 60
    assert datetime.fromisoformat(data["window_start"]) == NOW - timedelta(hours=1)
    assert datetime.fromisoformat(data["expires_at"]) == NOW + timedelta(seconds=120)
    assert response.headers["cache-control"] == "public, max-age=30"
    assert data["success_rate"] is None
    assert len(data["history"]) == 30
    assert data["history"][0]["date"] == "2026-10-10"
    assert data["history"][-1]["date"] == "2026-11-08"
    assert all(day["status"] == "unknown" and day["success_rate"] is None for day in data["history"])


async def test_call_failover_and_exclusions_do_not_become_user_failure_rate(client, attempt):
    attempt("error", source="router")  # Same run can fail over and finish successfully.
    attempt("success", source="router")
    attempt("success")
    attempt("timeout")
    attempt("success", source="polish")
    attempt("error", source="polish")
    attempt("timeout", source="polish")
    attempt("cancelled")
    attempt(None)
    attempt("error", backfilled=True)
    attempt("error", source="suggest")
    attempt("error", source="material")
    attempt("error", at=NOW + timedelta(seconds=1))
    response = await client.get("/api/status")
    data = response.json()
    assert data["status"] == "degraded"
    assert data["success_rate"] == 0.5
    assert data["history"][-1]["success_rate"] == 0.5
    # Public DTO is deliberately allowlisted; no traffic counts or identifying details.
    assert set(data) == {
        "generated_at", "expires_at", "window_start", "window_minutes", "timezone",
        "status", "success_rate", "history",
    }
    assert set(data["history"][-1]) == {"date", "status", "success_rate"}
    assert "private-" not in response.text
    assert "status@example.com" not in response.text


def test_beijing_boundaries_recent_window_and_retention(db_session, attempt):
    attempt("success", at=NOW - timedelta(hours=1))  # Inclusive boundary, previous Beijing day.
    attempt("error", at=NOW - timedelta(hours=1, seconds=1))  # History only.
    attempt("success", at=datetime(2026, 11, 7, 16, tzinfo=UTC))  # Beijing midnight.
    attempt("error", at=datetime(2026, 10, 9, 16, tzinfo=UTC))  # First included day.
    attempt("error", at=datetime(2026, 10, 9, 15, 59, 59, tzinfo=UTC))  # Outside retention.
    data = service_status.get_service_status(db_session)
    assert data.status == "operational"
    assert data.success_rate == 1
    assert data.history[-2].status == "degraded"
    assert data.history[-2].success_rate == 0.5
    assert data.history[-1].status == "operational"
    assert data.history[0].status == "degraded"


def test_only_cancelled_or_unrecorded_calls_remain_unknown(db_session, attempt):
    attempt("cancelled")
    attempt(None)
    attempt("success", backfilled=True)
    attempt("success", at=NOW - timedelta(hours=2))
    data = service_status.get_service_status(db_session)
    assert data.status == "unknown"
    assert data.success_rate is None
    assert data.history[-2].status == "operational"


@pytest.mark.parametrize("outcome", ["success", "error", "timeout"])
def test_polish_alone_cannot_supply_agent_current_or_history_status(db_session, attempt, outcome):
    attempt(outcome, source="polish")
    data = service_status.get_service_status(db_session)
    assert data.status == "unknown"
    assert data.success_rate is None
    assert all(day.status == "unknown" and day.success_rate is None for day in data.history)


def test_agent_success_stays_operational_with_no_polish_or_failed_polish(db_session, attempt):
    attempt()
    before = service_status.get_service_status(db_session)
    assert before.status == "operational"
    attempt("error", source="polish")
    attempt("timeout", source="polish", at=NOW - timedelta(days=1))
    assert service_status.get_service_status(db_session) == before


def test_cache_reuses_snapshot_then_refreshes(db_session, attempt, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(service_status, "monotonic", lambda: clock[0])
    cache = service_status.StatusCache()
    first = cache.get(db_session)
    attempt()
    clock[0] = 39.9
    assert cache.get(db_session) is first
    clock[0] = 40.0
    assert cache.get(db_session).status == "operational"


async def test_failed_refresh_returns_503_without_serving_old_green_or_error_details(client, attempt, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(service_status, "monotonic", lambda: clock[0])
    attempt()
    assert (await client.get("/api/status")).json()["status"] == "operational"

    def unavailable(_session):
        raise OperationalError("SELECT secret", {}, Exception("private database address"))

    monkeypatch.setattr(service_status, "get_service_status", unavailable)
    clock[0] = 31.0
    response = await client.get("/api/status")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "secret" not in response.text
    assert "private" not in response.text
    assert "operational" not in response.text
