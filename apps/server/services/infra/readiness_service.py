"""
Readiness checks for ``/health/ready``.

``/health`` stays a static liveness answer for the Railway deploy
healthcheck; readiness actually touches the database and Redis, each bounded
by a short timeout so a hung dependency cannot hang the probe.
"""

import asyncio
import logging
import os
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import text

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

DEFAULT_READINESS_TIMEOUT_SECONDS = 2.0


def _readiness_timeout_seconds() -> float:
    raw = os.getenv("READINESS_CHECK_TIMEOUT_S", "").strip()
    try:
        value = float(raw) if raw else DEFAULT_READINESS_TIMEOUT_SECONDS
    except ValueError:
        return DEFAULT_READINESS_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_READINESS_TIMEOUT_SECONDS


def _ping_database() -> None:
    from database import sync_engine

    with sync_engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def _ping_redis() -> None:
    from services.infra.redis_client import get_redis_client

    get_redis_client().ping()


def _redis_configured() -> bool:
    # Without REDIS_URL the app falls back to in-memory behavior, so Redis is
    # not a readiness dependency.
    return bool(os.getenv("REDIS_URL", "").strip())


async def _run_check(name: str, check: Callable[[], None], timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        await asyncio.wait_for(asyncio.to_thread(check), timeout=timeout)
    except TimeoutError:
        status, error = "timeout", f"exceeded {timeout:g}s"
    except Exception as exc:  # noqa: BLE001 - any failure means "not ready"
        status, error = "error", type(exc).__name__
    else:
        status, error = "ok", None

    result: dict[str, Any] = {
        "status": status,
        "duration_ms": round((time.perf_counter() - started) * 1000, 2),
    }
    if error:
        result["error"] = error
        log_with_context(
            logger,
            logging.WARNING,
            "Readiness check failed",
            exc_info=False,
            check=name,
            status=status,
            error=error,
        )
    return result


async def check_readiness(
    *,
    database_check: Callable[[], None] | None = None,
    redis_check: Callable[[], None] | None = None,
    redis_required: bool | None = None,
    timeout: float | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Run dependency checks; returns ``(ready, payload)``."""
    database_check = database_check or _ping_database
    redis_check = redis_check or _ping_redis
    timeout = timeout if timeout is not None else _readiness_timeout_seconds()
    if redis_required is None:
        redis_required = _redis_configured()

    checks: dict[str, Any] = {"database": await _run_check("database", database_check, timeout)}
    if redis_required:
        checks["redis"] = await _run_check("redis", redis_check, timeout)
    else:
        checks["redis"] = {"status": "skipped"}

    ready = all(check["status"] in {"ok", "skipped"} for check in checks.values())
    return ready, {"status": "ready" if ready else "not_ready", "checks": checks}
