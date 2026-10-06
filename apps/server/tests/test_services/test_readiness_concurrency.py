"""Independent readiness probes share a deadline, not a sequential critical path."""

import threading

import pytest

from services.infra.readiness_service import check_readiness


@pytest.mark.asyncio
async def test_required_probes_start_concurrently():
    started = threading.Barrier(2, timeout=1)

    def probe():
        # No duration threshold: both probes must start before either finishes.
        started.wait()

    ready, payload = await check_readiness(
        database_check=probe, redis_check=probe, redis_required=True, timeout=2
    )

    assert ready is True
    assert list(payload["checks"]) == ["database", "redis"]
    assert payload["checks"]["database"]["status"] == "ok"
    assert payload["checks"]["redis"]["status"] == "ok"


@pytest.mark.asyncio
async def test_database_timeout_preserves_redis_result():
    release = threading.Event()

    def blocked_database():
        release.wait(5)

    try:
        ready, payload = await check_readiness(
            database_check=blocked_database,
            redis_check=lambda: None,
            redis_required=True,
            timeout=0.2,
        )
    finally:
        release.set()

    assert ready is False
    assert payload["status"] == "not_ready"
    assert payload["checks"]["database"]["status"] == "timeout"
    assert payload["checks"]["redis"]["status"] == "ok"


@pytest.mark.asyncio
async def test_optional_redis_is_not_called():
    def unexpected_redis():
        pytest.fail("Redis is not configured and must not be probed")

    ready, payload = await check_readiness(
        database_check=lambda: None,
        redis_check=unexpected_redis,
        redis_required=False,
    )

    assert ready is True
    assert payload["checks"]["database"]["status"] == "ok"
    assert payload["checks"]["redis"] == {"status": "skipped"}
