"""Short-TTL Redis cache of the exact SDK wire-format prefix for active loop sessions.

Why this exists:
- DB chat persistence intentionally normalizes Turn N for UI rendering: ``<turn_context>``
  is not persisted, ``call_id``s are synthesized as ``h{row.id}_{i}``, tool outputs are
  reshaped into UI cards, ``<file>`` bodies are stripped, and ``reasoning_content`` plus
  intra-round continuation notes are omitted.
- When Turn N+1 arrives within the provider's KV-cache window (~15 min on MiniMax),
  replaying from DB diverges at the very first token of Turn N, wasting 100% of Turn N's
  cached tokens (and breaking pre-run compaction prefix reuse).
- Caching the exact ``completed_items`` from ``Runner.run_streamed`` in Redis for a short
  TTL preserves a byte-identical prefix across consecutive turns while failing open cleanly
  to ``load_loop_history`` whenever anything changes (new/deleted/mutated DB rows, system
  prompt change, provider/model/key switch, TTL expiry, size cap, or Redis outage).

Privacy: raw ``items`` payloads are never logged.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from typing import Any

from config import agent_runtime
from services.infra.redis_client import (
    agent_wire_prefix_cache_key,
    get_redis_client,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

WIRE_PREFIX_CACHE_SCHEMA_VERSION = 1

_REDIS_RETRY_AFTER_S = 30.0
_redis_down_until = 0.0


def system_prompt_sha256(system_prompt: str) -> str:
    """Deterministic SHA-256 hex digest of the static system prompt."""
    return hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()


def provider_cache_key(provider: Any) -> str | None:
    """Canonical ``provider:model:key_slot`` identity for cache-affinity validation."""
    name = getattr(provider, "name", None)
    model = getattr(provider, "model", None)
    key_slot = getattr(provider, "key_slot", None)
    if not isinstance(name, str) or not name or not isinstance(model, str) or not model:
        return None
    slot = key_slot if isinstance(key_slot, str) and key_slot else "primary"
    return f"{name}:{model}:{slot}"


def wire_prefix_cache_key(session_id: str) -> str:
    """Redis key for ``session_id``'s wire-prefix envelope."""
    return agent_wire_prefix_cache_key(session_id)


def _redis_available() -> bool:
    return bool(os.getenv("REDIS_URL")) and time.monotonic() >= _redis_down_until


def _mark_redis_down(operation: str, session_id: str, exc: BaseException) -> None:
    global _redis_down_until
    _redis_down_until = time.monotonic() + _REDIS_RETRY_AFTER_S
    log_with_context(
        logger,
        30,  # WARNING
        "Agent wire prefix cache: Redis unavailable, failing open",
        operation=operation,
        session_id=session_id,
        error_type=type(exc).__name__,
    )


def _effective_ttl(ttl_seconds: int | None) -> int:
    return agent_runtime.AGENT_WIRE_PREFIX_CACHE_TTL_SECONDS if ttl_seconds is None else ttl_seconds


def _effective_max_bytes(max_bytes: int | None) -> int:
    return agent_runtime.AGENT_WIRE_PREFIX_CACHE_MAX_BYTES if max_bytes is None else max_bytes


def is_well_formed_wire_prefix(items: Any) -> bool:
    """Verify SDK wire items are structurally safe to replay as a Turn N+1 prefix.

    Checks that ``items`` is a non-empty list of dicts, every ``function_call`` has a
    non-empty ``call_id`` answered by a matching ``function_call_output`` before any
    subsequent non-tool item or end of list, no orphan ``function_call_output`` appears,
    and the tail is not a dangling ``function_call`` or ``reasoning`` item.
    """
    if not isinstance(items, list) or not items:
        return False
    pending_call_ids: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            return False
        item_type = item.get("type")
        if item_type == "function_call":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id:
                return False
            pending_call_ids.add(call_id)
        elif item_type == "function_call_output":
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or not call_id or call_id not in pending_call_ids:
                return False
            pending_call_ids.remove(call_id)
        elif pending_call_ids:
            return False
    if pending_call_ids:
        return False
    return items[-1].get("type") not in ("function_call", "reasoning")


def load_wire_prefix(
    *,
    session_id: str,
    project_id: str,
    user_id: str,
    last_message_id: str,
    system_prompt: str,
    provider_key: str,
    ttl_seconds: int | None = None,
) -> list[dict[str, Any]] | None:
    """Load and strictly validate the cached SDK wire-prefix items for ``session_id``.

    Returns ``None`` on any miss, validation mismatch, disabled config, or Redis error.
    """
    if _effective_ttl(ttl_seconds) <= 0:
        return None
    if not (session_id and project_id and user_id and last_message_id and provider_key):
        return None
    if not _redis_available():
        return None

    try:
        raw = get_redis_client().get(wire_prefix_cache_key(session_id))
    except Exception as exc:  # noqa: BLE001 - fail open to DB history replay
        _mark_redis_down("get", session_id, exc)
        return None

    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(raw, str) or not raw:
        return None

    try:
        envelope = json.loads(raw)
    except ValueError:
        log_with_context(
            logger,
            30,  # WARNING
            "Agent wire prefix cache entry corrupted; ignoring",
            session_id=session_id,
        )
        return None

    if not isinstance(envelope, dict):
        return None

    mismatch: str | None = None
    if type(envelope.get("schema_version")) is not int or envelope["schema_version"] != WIRE_PREFIX_CACHE_SCHEMA_VERSION:
        mismatch = "schema_version"
    elif envelope.get("project_id") != project_id or envelope.get("user_id") != user_id:
        mismatch = "scope"
    elif envelope.get("last_assistant_message_id") != last_message_id:
        mismatch = "last_message_id"
    elif envelope.get("system_prompt_sha256") != system_prompt_sha256(system_prompt):
        mismatch = "system_prompt"
    elif envelope.get("provider_key") != provider_key:
        mismatch = "provider_key"

    if mismatch is not None:
        log_with_context(
            logger,
            20,  # INFO
            "Agent wire prefix cache miss due to validation mismatch",
            session_id=session_id,
            mismatch=mismatch,
        )
        return None

    items = envelope.get("items")
    if not is_well_formed_wire_prefix(items):
        return None

    log_with_context(
        logger,
        20,  # INFO
        "Agent wire prefix cache hit",
        session_id=session_id,
        item_count=len(items),
        payload_bytes=len(raw.encode("utf-8")),
    )
    return items


def store_wire_prefix(
    *,
    session_id: str,
    project_id: str,
    user_id: str,
    last_assistant_message_id: str,
    system_prompt: str,
    provider_key: str,
    items: list[Any],
    ttl_seconds: int | None = None,
    max_bytes: int | None = None,
) -> bool:
    """Store validated SDK wire-prefix items in Redis with a short TTL and byte cap."""
    ttl = _effective_ttl(ttl_seconds)
    if ttl <= 0:
        return False
    if not (session_id and project_id and user_id and last_assistant_message_id and provider_key):
        return False
    if not is_well_formed_wire_prefix(items):
        invalidate_wire_prefix(session_id)
        return False
    if not _redis_available():
        return False

    envelope = {
        "schema_version": WIRE_PREFIX_CACHE_SCHEMA_VERSION,
        "project_id": project_id,
        "user_id": user_id,
        "last_assistant_message_id": last_assistant_message_id,
        "system_prompt_sha256": system_prompt_sha256(system_prompt),
        "provider_key": provider_key,
        "items": items,
    }
    try:
        serialized = json.dumps(envelope, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        log_with_context(
            logger,
            30,  # WARNING
            "Agent wire prefix cache skipped: items not JSON-serializable",
            session_id=session_id,
            error_type=type(exc).__name__,
        )
        invalidate_wire_prefix(session_id)
        return False

    payload_bytes = len(serialized.encode("utf-8"))
    cap = _effective_max_bytes(max_bytes)
    if payload_bytes > cap:
        log_with_context(
            logger,
            20,  # INFO
            "Agent wire prefix cache skipped: payload exceeds max bytes",
            session_id=session_id,
            payload_bytes=payload_bytes,
            max_bytes=cap,
        )
        invalidate_wire_prefix(session_id)
        return False

    try:
        get_redis_client().setex(wire_prefix_cache_key(session_id), ttl, serialized)
    except Exception as exc:  # noqa: BLE001 - fail open
        _mark_redis_down("setex", session_id, exc)
        return False

    log_with_context(
        logger,
        20,  # INFO
        "Agent wire prefix cache stored",
        session_id=session_id,
        item_count=len(items),
        payload_bytes=payload_bytes,
        ttl_seconds=ttl,
    )
    return True


def invalidate_wire_prefix(session_id: str | None) -> bool:
    """Delete ``session_id``'s wire-prefix cache entry (fail open)."""
    if not session_id or not _redis_available():
        return False
    try:
        get_redis_client().delete(wire_prefix_cache_key(session_id))
        return True
    except Exception as exc:  # noqa: BLE001 - fail open
        _mark_redis_down("delete", session_id, exc)
        return False


async def aload_wire_prefix(**kwargs: Any) -> list[dict[str, Any]] | None:
    """Async wrapper around ``load_wire_prefix`` offloaded from the event loop."""
    return await asyncio.to_thread(load_wire_prefix, **kwargs)


async def astore_wire_prefix(**kwargs: Any) -> bool:
    """Async wrapper around ``store_wire_prefix`` offloaded from the event loop."""
    return await asyncio.to_thread(store_wire_prefix, **kwargs)


async def ainvalidate_wire_prefix(session_id: str | None) -> bool:
    """Async wrapper around ``invalidate_wire_prefix`` offloaded from the event loop."""
    return await asyncio.to_thread(invalidate_wire_prefix, session_id)


def reset_wire_prefix_cache_state() -> None:
    """Reset in-process Redis circuit cooldown; intended for tests."""
    global _redis_down_until
    _redis_down_until = 0.0
