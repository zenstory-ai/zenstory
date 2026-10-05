"""API-level observability checks against the real app: readiness, 422s, CORS, auth threads."""

import asyncio
import json
import logging
import threading

import pytest
from httpx import AsyncClient

from services.infra import readiness_service


@pytest.fixture(autouse=True)
def _invite_optional(monkeypatch):
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "true")


# ---------------------------------------------------------------- readiness


@pytest.mark.asyncio
async def test_health_stays_static(client: AsyncClient):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}


@pytest.mark.asyncio
async def test_ready_reports_ok_when_dependencies_answer(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")
    monkeypatch.setattr(readiness_service, "_ping_redis", lambda: None)

    response = await client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    # The database check really runs against the configured engine.
    assert body["checks"]["database"]["status"] == "ok"
    assert body["checks"]["redis"]["status"] == "ok"


@pytest.mark.asyncio
async def test_ready_skips_redis_when_not_configured(client: AsyncClient, monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)

    response = await client.get("/health/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["redis"] == {"status": "skipped"}


@pytest.mark.asyncio
async def test_ready_returns_503_on_database_error():
    def broken_database():
        raise ConnectionError("db down")

    ready, payload = await readiness_service.check_readiness(
        database_check=broken_database, redis_required=False, timeout=1
    )

    assert ready is False
    assert payload["status"] == "not_ready"
    assert payload["checks"]["database"] == {
        "status": "error",
        "duration_ms": payload["checks"]["database"]["duration_ms"],
        "error": "ConnectionError",
    }


@pytest.mark.asyncio
async def test_ready_times_out_a_hung_redis(client: AsyncClient, monkeypatch):
    release = threading.Event()

    def hung_redis():
        release.wait(5)

    monkeypatch.setenv("REDIS_URL", "redis://example:6379/0")
    monkeypatch.setenv("READINESS_CHECK_TIMEOUT_S", "0.2")
    monkeypatch.setattr(readiness_service, "_ping_redis", hung_redis)
    try:
        response = await asyncio.wait_for(client.get("/health/ready"), timeout=3)
    finally:
        release.set()

    assert response.status_code == 503
    assert response.json()["checks"]["redis"]["status"] == "timeout"


# --------------------------------------------------------- 422 validation


@pytest.mark.asyncio
async def test_validator_value_error_returns_422_not_500(client: AsyncClient):
    response = await client.post(
        "/api/auth/register",
        json={"username": "valid_name", "email": "short@example.com", "password": "abc"},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "ERR_VALIDATION_ERROR"
    for error in body["errors"]:
        assert set(error) == {"loc", "type", "msg"}
    assert any(error["loc"][-1] == "password" for error in body["errors"])


@pytest.mark.asyncio
async def test_validation_log_omits_submitted_values(client: AsyncClient, caplog):
    secret = "RealPassw0rd!"
    with caplog.at_level(logging.WARNING, logger="core.error_handler"):
        response = await client.post(
            "/api/auth/register",
            # Missing username: Pydantic's ``input`` would be the whole body.
            json={"email": "leak@example.com", "password": secret},
        )

    assert response.status_code == 422
    assert secret not in response.text
    records = [r for r in caplog.records if r.getMessage() == "Request validation failed"]
    assert records
    logged = json.dumps(records[0].custom_fields, default=str)
    assert secret not in logged
    assert "leak@example.com" not in logged
    assert "username" in logged


# ------------------------------------------------------------------ CORS


@pytest.mark.asyncio
async def test_cors_exposes_correlation_headers(client: AsyncClient):
    response = await client.get("/health", headers={"Origin": "http://localhost:5173"})

    exposed = response.headers.get("access-control-expose-headers", "").lower()
    assert "x-request-id" in exposed
    assert "x-agent-run-id" in exposed


# ------------------------------------------- blocking work off the event loop


@pytest.mark.asyncio
async def test_register_hashes_and_sends_mail_off_the_event_loop(client: AsyncClient, monkeypatch):
    import api.auth as auth_api
    import services.infra.email_client as email_client

    loop_thread = threading.get_ident()
    seen: dict[str, int] = {}
    real_hash = auth_api.hash_password

    def recording_hash(password: str) -> str:
        seen["hash"] = threading.get_ident()
        return real_hash(password)

    def recording_send(params):
        seen["send"] = threading.get_ident()
        return {"id": "email-1"}

    monkeypatch.setattr(auth_api, "hash_password", recording_hash)
    monkeypatch.setattr(email_client, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(email_client.resend.Emails, "send", recording_send)
    for name in (
        "check_resend_cooldown",
        "store_verification_code",
        "set_resend_cooldown",
        "reset_verification_attempts",
    ):
        def recording_redis(*args, _name=name, **kwargs):
            seen[_name] = threading.get_ident()
            return _name != "check_resend_cooldown"

        monkeypatch.setattr(f"services.features.verification_service.{name}", recording_redis)

    response = await client.post(
        "/api/auth/register",
        json={"username": "thread_user", "email": "thread@example.com", "password": "password123"},
    )

    assert response.status_code == 200
    assert response.json()["verification_sent"] is True
    assert set(seen) == {
        "hash",
        "send",
        "check_resend_cooldown",
        "store_verification_code",
        "set_resend_cooldown",
        "reset_verification_attempts",
    }
    for name, thread_id in seen.items():
        assert thread_id != loop_thread, f"{name} ran on the event loop thread"


@pytest.mark.unit
def test_resend_client_timeout_is_short():
    import services.infra.email_client as email_client

    assert 5 <= email_client.RESEND_TIMEOUT_SECONDS <= 10
    assert email_client.resend.default_http_client._timeout == email_client.RESEND_TIMEOUT_SECONDS
