"""
Dashboard cache helpers (Redis-first with in-process TTL fallback).

Used to cache high-frequency dashboard endpoints (e.g. project stats) without
requiring database schema changes.

Design:
- Cache keys are versioned per (user_id, project_id). Write paths bump the
  version so readers don't need Redis SCAN to invalidate older keys.
- Backend selection:
  - DASHBOARD_CACHE_BACKEND=auto|redis|memory|none (default: auto)
  - auto: use Redis when REDIS_URL is configured, otherwise memory
"""

from __future__ import annotations

import json as json_module
import logging
import os
import time
from threading import RLock
from typing import Any

from services.infra.redis_client import get_redis_client
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

_DEFAULT_REDIS_PREFIX = "dashboard_cache"

# In-process fallback cache: key -> (expires_at_monotonic, json_value)
_memory_cache: dict[str, tuple[float, str]] = {}
# In-process version store: version_key -> version int
_memory_versions: dict[str, int] = {}
_memory_lock = RLock()

_memory_last_prune_monotonic: float = 0.0
_MEMORY_PRUNE_INTERVAL_SECONDS = 60.0
_MEMORY_MAX_ENTRIES = 5000

_redis_retry_after_monotonic: float = 0.0


def _parse_positive_int_env(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    raw = raw_value.strip()
    if not raw:
        return default
    try:
        return max(0, int(raw))
    except ValueError:
        return default


def _get_backend() -> str:
    return os.getenv("DASHBOARD_CACHE_BACKEND", "auto").strip().lower()


def _get_redis_error_cooldown_seconds() -> int:
    return _parse_positive_int_env("DASHBOARD_CACHE_REDIS_ERROR_COOLDOWN_SECONDS", 30) or 30


def _get_redis_prefix() -> str:
    prefix = os.getenv("DASHBOARD_CACHE_REDIS_PREFIX", _DEFAULT_REDIS_PREFIX)
    prefix = (prefix or "").strip()
    return prefix or _DEFAULT_REDIS_PREFIX


def _build_redis_key(key: str) -> str:
    return f"{_get_redis_prefix()}:{key}"


def _should_try_redis() -> bool:
    global _redis_retry_after_monotonic

    backend = _get_backend()
    if backend == "none":
        return False
    if backend == "memory":
        return False
    if backend == "redis":
        return True

    # auto mode
    if not os.getenv("REDIS_URL"):
        return False

    return time.monotonic() >= _redis_retry_after_monotonic


def _record_redis_failure(error: Exception, *, operation: str) -> None:
    global _redis_retry_after_monotonic

    retry_after = _get_redis_error_cooldown_seconds()
    _redis_retry_after_monotonic = time.monotonic() + retry_after
    log_with_context(
        logger,
        logging.WARNING,
        "Dashboard cache Redis backend unavailable; falling back to in-memory cache",
        operation=operation,
        error=str(error),
        error_type=type(error).__name__,
        retry_after_seconds=retry_after,
    )


def _memory_get(key: str) -> str | None:
    with _memory_lock:
        record = _memory_cache.get(key)
        if not record:
            return None
        expires_at, payload = record
        if time.monotonic() >= expires_at:
            _memory_cache.pop(key, None)
            return None
        return payload


def _memory_set(key: str, payload: str, ttl_seconds: int) -> None:
    global _memory_last_prune_monotonic

    with _memory_lock:
        now = time.monotonic()

        if ttl_seconds <= 0:
            _memory_cache.pop(key, None)
            return

        _memory_cache[key] = (now + ttl_seconds, payload)

        # Best-effort pruning to avoid unbounded growth when running without Redis.
        should_prune = False
        if len(_memory_cache) > _MEMORY_MAX_ENTRIES or (now - _memory_last_prune_monotonic) >= _MEMORY_PRUNE_INTERVAL_SECONDS:
            should_prune = True

        if not should_prune:
            return

        _memory_last_prune_monotonic = now
        expired_keys = [k for k, (expires_at, _v) in _memory_cache.items() if expires_at <= now]
        for expired in expired_keys:
            _memory_cache.pop(expired, None)

        # Hard cap: if still too large (e.g. many active keys), evict arbitrary
        # entries (FIFO-ish because dict preserves insertion order).
        while len(_memory_cache) > _MEMORY_MAX_ENTRIES:
            _memory_cache.pop(next(iter(_memory_cache)), None)


def get_json(key: str) -> dict[str, Any] | list[Any] | None:
    """
    Get cached JSON payload.

    Returns None when cache is disabled or missing/expired.
    """
    backend = _get_backend()
    if backend == "none":
        return None

    if backend != "memory":
        redis_payload = _redis_get(key)
        if redis_payload is not None:
            try:
                parsed = json_module.loads(redis_payload)
            except (TypeError, ValueError):
                return None
            if isinstance(parsed, (dict, list)):
                return parsed
            return None

    payload = _memory_get(key)
    if payload is None:
        return None
    try:
        parsed = json_module.loads(payload)
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, (dict, list)):
        return parsed
    return None


def set_json(key: str, payload: Any, ttl_seconds: int) -> None:
    """
    Set cached JSON payload.

    Best-effort: failures fall back to in-process cache when available.
    """
    backend = _get_backend()
    if backend == "none" or ttl_seconds <= 0:
        return

    serialized = json_module.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    if backend != "memory":
        ok = _redis_set(key, serialized, ttl_seconds)
        if ok:
            return

    _memory_set(key, serialized, ttl_seconds)


def _redis_get(key: str) -> str | None:
    if not _should_try_redis():
        return None

    try:
        client = get_redis_client()
        redis_key = _build_redis_key(key)
        value = client.get(redis_key)
        if value is None:
            return None
        return str(value)
    except Exception as exc:  # pragma: no cover - infra dependent
        _record_redis_failure(exc, operation="get")
        return None


def _redis_set(key: str, serialized: str, ttl_seconds: int) -> bool:
    if not _should_try_redis():
        return False

    try:
        client = get_redis_client()
        redis_key = _build_redis_key(key)
        client.setex(redis_key, int(ttl_seconds), serialized)
        return True
    except Exception as exc:  # pragma: no cover - infra dependent
        _record_redis_failure(exc, operation="set")
        return False


def _project_version_key(user_id: str, project_id: str) -> str:
    return f"project_ver:v1:{user_id}:{project_id}"


def get_project_version(user_id: str, project_id: str) -> int:
    """
    Get current cache-busting version for a project scope.

    Defaults to 1 when absent.
    """
    backend = _get_backend()
    if backend == "none":
        return 1

    version_key = _project_version_key(user_id, project_id)

    if backend != "memory":
        redis_val = _redis_get(version_key)
        if redis_val is not None:
            try:
                return max(1, int(redis_val))
            except ValueError:
                return 1

    with _memory_lock:
        return max(1, int(_memory_versions.get(version_key, 1)))


def bump_project_version(user_id: str, project_id: str) -> int:
    """
    Bump cache-busting version for a project scope.

    Returns the new version.
    """
    backend = _get_backend()
    if backend == "none":
        return 1

    version_key = _project_version_key(user_id, project_id)

    if backend != "memory" and _should_try_redis():
        try:
            client = get_redis_client()
            redis_key = _build_redis_key(version_key)
            new_version = int(client.incr(redis_key))
            if new_version <= 0:
                # Defensive: reset to 1 if Redis data was corrupt.
                client.set(redis_key, "1")
                new_version = 1
            return new_version
        except Exception as exc:  # pragma: no cover - infra dependent
            _record_redis_failure(exc, operation="bump_version")

    with _memory_lock:
        new_version = int(_memory_versions.get(version_key, 1)) + 1
        _memory_versions[version_key] = new_version
        return new_version


__all__ = [
    "dashboard_cache",
    "get_json",
    "set_json",
    "get_project_version",
    "bump_project_version",
]


class DashboardCache:
    """Facade object for backward-compatible call sites."""

    def get_json(self, key: str) -> dict[str, Any] | list[Any] | None:
        return get_json(key)

    def set_json(self, key: str, payload: Any, *, ttl_seconds: int) -> None:
        set_json(key, payload, ttl_seconds)

    def get_project_version(self, user_id: str, project_id: str) -> int:
        return get_project_version(user_id, project_id)

    def bump_project_version(self, user_id: str, project_id: str) -> int:
        return bump_project_version(user_id, project_id)


dashboard_cache = DashboardCache()
