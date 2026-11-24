"""Short-TTL Redis cache and PostgreSQL read timeout guard for admin analytics.

Design goals:
- Fail-open: when ``REDIS_URL`` is absent (local dev / unit tests) or Redis is
  temporarily unavailable, endpoints compute directly without failing.
- Stampede protection: in-process striped locks prevent concurrent requests for
  the same uncached analytics key from running duplicate heavy queries.
- Explicit bypass: callers pass ``refresh=True`` from the admin Refresh button
  to recompute and repopulate the cache.
- Read timeout safety: PostgreSQL analytics queries run with a bounded
  ``SET LOCAL statement_timeout`` so a runaway query cannot hold a pool
  connection indefinitely.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlmodel import Session

from services.infra.redis_client import get_redis_client
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

ADMIN_ANALYTICS_CACHE_PREFIX = "admin:analytics:v1:"
DEFAULT_CACHE_TTL_SECONDS = 120
LONG_WINDOW_CACHE_TTL_SECONDS = 300
DEFAULT_STATEMENT_TIMEOUT_MS = 20_000

_LOCK_STRIPES = 256
_STRIPE_LOCKS = tuple(threading.Lock() for _ in range(_LOCK_STRIPES))


def _cache_enabled() -> bool:
    flag = (os.getenv("ADMIN_ANALYTICS_CACHE_ENABLED") or "true").strip().lower()
    if flag in {"0", "false", "no", "off"}:
        return False
    return bool((os.getenv("REDIS_URL") or "").strip())


def _resolve_ttl(ttl_seconds: int) -> int:
    override = (os.getenv("ADMIN_ANALYTICS_CACHE_TTL_SECONDS") or "").strip()
    if override:
        try:
            parsed = int(override)
            if parsed > 0:
                return parsed
        except ValueError:
            pass
    return max(1, int(ttl_seconds))


def _resolve_statement_timeout_ms(timeout_ms: int | None = None) -> int:
    if timeout_ms is not None:
        return max(1_000, int(timeout_ms))
    raw = (os.getenv("ADMIN_ANALYTICS_STATEMENT_TIMEOUT_MS") or "").strip()
    if raw:
        try:
            parsed = int(raw)
            if parsed >= 1_000:
                return parsed
        except ValueError:
            pass
    return DEFAULT_STATEMENT_TIMEOUT_MS


def build_cache_key(namespace: str, **params: Any) -> str:
    """Build a deterministic Redis key for an admin analytics view."""
    serialized = json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
    return f"{ADMIN_ANALYTICS_CACHE_PREFIX}{namespace}:{digest}"


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _lock_for_key(key: str) -> threading.Lock:
    index = int.from_bytes(hashlib.blake2b(key.encode("utf-8"), digest_size=2).digest(), "big") % _LOCK_STRIPES
    return _STRIPE_LOCKS[index]


def _read_cache(key: str) -> Any | None:
    try:
        raw = get_redis_client().get(key)
    except Exception as exc:  # noqa: BLE001 - cache must fail open
        log_with_context(
            logger,
            30,
            "Admin analytics cache read failed",
            cache_key=key,
            error_type=type(exc).__name__,
        )
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def _write_cache(key: str, payload: Any, ttl_seconds: int) -> None:
    try:
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=_json_default)
        get_redis_client().setex(key, ttl_seconds, encoded)
    except Exception as exc:  # noqa: BLE001 - cache must fail open
        log_with_context(
            logger,
            30,
            "Admin analytics cache write failed",
            cache_key=key,
            error_type=type(exc).__name__,
        )


def cached_admin_analytics(
    namespace: str,
    compute: Callable[[], dict[str, Any]],
    *,
    ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    refresh: bool = False,
    **params: Any,
) -> dict[str, Any]:
    """Return a cached analytics payload or compute and populate Redis."""
    if not _cache_enabled():
        return compute()

    key = build_cache_key(namespace, **params)
    ttl = _resolve_ttl(ttl_seconds)

    if not refresh:
        cached = _read_cache(key)
        if isinstance(cached, dict):
            return cached

    with _lock_for_key(key):
        if not refresh:
            cached = _read_cache(key)
            if isinstance(cached, dict):
                return cached
        payload = compute()
        _write_cache(key, payload, ttl)
        return payload


@contextmanager
def admin_read_statement_timeout(
    session: Session,
    *,
    timeout_ms: int | None = None,
) -> Generator[None, None, None]:
    """Apply a transaction-scoped statement timeout on PostgreSQL read sessions."""
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        yield
        return

    resolved_ms = _resolve_statement_timeout_ms(timeout_ms)
    session.exec(text(f"SET LOCAL statement_timeout = '{resolved_ms}ms'"))
    yield
