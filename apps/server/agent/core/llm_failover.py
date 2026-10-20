"""Primary/backup failover for chat model calls.

With only a primary configured this module is a pass-through: no state is read
or written. With a backup (see ``agent/core/llm_providers.py``):

- A call goes to its session's slot: the slot the session is pinned to, else
  the primary. A slot whose circuit is open is tried second while the other
  one's circuit is closed.
- A provider-side failure (connection error, timeout, 429, 5xx, or an account
  error 401/402/403/404) counts towards that slot's circuit and pins the
  session to the other slot for ``LLM_SESSION_PIN_TTL_S``, so later model calls
  in that chat window, including the author's retry, use the other provider.
- Only a failure before any output is answered by calling the other slot in
  the same model call; it produced nothing, so nothing is generated twice.
  After output has started the error propagates as before. Request errors
  (400/413/422, context too long), the free-user cost budget and
  cancellation never fail over.
- A successful call on a slot other than the session's pin re-pins the session
  to it, so a session does not bounce between providers (prefix cache).

State lives in Redis when ``REDIS_URL`` is configured, shared by API workers
and Prefect workers, and falls back to process memory when Redis is not
configured or fails.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from agent.core import llm_providers
from agent.core.llm_providers import ModelProvider
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name) or default)
    except ValueError:
        return default
    return value if value > 0 else default


LLM_SESSION_PIN_TTL_S = _env_int("LLM_SESSION_PIN_TTL_S", 1800)
LLM_CIRCUIT_FAILURE_THRESHOLD = _env_int("LLM_CIRCUIT_FAILURE_THRESHOLD", 3)
LLM_CIRCUIT_FAILURE_WINDOW_S = _env_int("LLM_CIRCUIT_FAILURE_WINDOW_S", 60)
LLM_CIRCUIT_OPEN_S = _env_int("LLM_CIRCUIT_OPEN_S", 120)

# A model that cannot turn reasoning off spends part of a short budget on it.
THINKING_HEADROOM_TOKENS = 2048

_ACCOUNT_FAILURE_STATUSES = frozenset({401, 402, 403, 404})


def _status_code_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def is_provider_unavailable(exc: BaseException) -> bool:
    """Whether ``exc`` says the provider, not the request, is the problem."""
    from core.error_handler import APIException

    if isinstance(exc, APIException):
        return False  # our own refusal (free-user cost budget), not the provider's
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    try:
        import openai

        if isinstance(exc, openai.APIConnectionError):  # includes APITimeoutError
            return True
    except ImportError:  # pragma: no cover - openai is a hard dependency
        pass
    try:
        import httpx

        if isinstance(exc, httpx.TimeoutException | httpx.TransportError):
            return True
    except ImportError:  # pragma: no cover
        pass
    status = _status_code_of(exc)
    if status is None:
        return False
    return status == 429 or status >= 500 or status in _ACCOUNT_FAILURE_STATUSES


class _MemoryStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, tuple[str, float]] = {}
        self._failures: dict[str, list[float]] = {}

    def _get(self, key: str, now: float) -> str | None:
        item = self._values.get(key)
        if item is None:
            return None
        if item[1] <= now:
            self._values.pop(key, None)
            return None
        return item[0]

    def get_many(self, keys: list[str]) -> list[str | None]:
        now = time.monotonic()
        with self._lock:
            return [self._get(key, now) for key in keys]

    def set(self, key: str, value: str, ttl_s: int) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._values) > 10_000:
                self._values = {k: v for k, v in self._values.items() if v[1] > now}
            self._values[key] = (value, now + ttl_s)

    def count_failure(self, key: str, window_s: int) -> int:
        now = time.monotonic()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if t > now - window_s]
            recent.append(now)
            self._failures[key] = recent
            return len(recent)

    def clear(self) -> None:
        with self._lock:
            self._values.clear()
            self._failures.clear()


class _RedisStore:
    def _client(self) -> Any:
        from services.infra.redis_client import get_redis_client

        return get_redis_client()

    def get_many(self, keys: list[str]) -> list[str | None]:
        values = self._client().mget(keys)
        return [v.decode() if isinstance(v, bytes) else v for v in values]

    def set(self, key: str, value: str, ttl_s: int) -> None:
        self._client().set(key, value, ex=ttl_s)

    def count_failure(self, key: str, window_s: int) -> int:
        client = self._client()
        pipe = client.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()
        if ttl is None or int(ttl) < 0:  # first failure of the window
            client.expire(key, window_s)
        return int(count)


_memory = _MemoryStore()
_redis = _RedisStore()
_REDIS_RETRY_AFTER_S = 30.0
_redis_down_until = 0.0


def _with_store(operation: Callable[[Any], Any]) -> Any:
    """Run ``operation`` on Redis when configured and healthy, else on memory."""
    global _redis_down_until
    if os.getenv("REDIS_URL") and time.monotonic() >= _redis_down_until:
        try:
            return operation(_redis)
        except Exception as exc:  # noqa: BLE001 - failover state is best effort
            _redis_down_until = time.monotonic() + _REDIS_RETRY_AFTER_S
            log_with_context(
                logger,
                30,  # WARNING
                "LLM failover state: Redis unavailable, using process memory",
                error_type=type(exc).__name__,
            )
    return operation(_memory)


def _pin_key(session_id: str) -> str:
    return f"llm:session_slot:{session_id}"


def _open_key(slot: str) -> str:
    return f"llm:circuit_open:{slot}"


def _failures_key(slot: str) -> str:
    return f"llm:circuit_failures:{slot}"


def ordered_providers(session_id: str | None = None, *, thinking_enabled: bool = True) -> list[ModelProvider]:
    """Providers in the order this call should try them."""
    providers = llm_providers.configured_providers()
    if len(providers) == 1:
        return providers
    primary, backup = providers
    keys = [_open_key(primary.slot), _open_key(backup.slot)]
    if session_id:
        keys.append(_pin_key(session_id))
    values = _with_store(lambda store: store.get_many(keys))
    open_slots = {slot for slot, value in zip((primary.slot, backup.slot), values[:2], strict=True) if value}
    pinned = values[2] if session_id else None
    first, second = (backup, primary) if pinned == backup.slot else (primary, backup)
    if first.slot in open_slots and second.slot not in open_slots:
        first, second = second, first
    ordered = [first, second]
    if not thinking_enabled:
        # Short no-reasoning calls (suggestions, polish) go to a provider that
        # can turn reasoning off; the other one stays as the fallback.
        ordered.sort(key=lambda p: not p.kind.can_disable_thinking)
    return ordered


def record_failure(provider: ModelProvider, session_id: str | None, exc: BaseException) -> None:
    """Count a provider failure and pin the session to the other slot."""
    providers = llm_providers.configured_providers()
    if len(providers) == 1:
        return
    other = next(p for p in providers if p.slot != provider.slot)

    def write(store: Any) -> int:
        failures = store.count_failure(_failures_key(provider.slot), LLM_CIRCUIT_FAILURE_WINDOW_S)
        if failures >= LLM_CIRCUIT_FAILURE_THRESHOLD:
            store.set(_open_key(provider.slot), "1", LLM_CIRCUIT_OPEN_S)
        if session_id:
            store.set(_pin_key(session_id), other.slot, LLM_SESSION_PIN_TTL_S)
        return failures

    failures = _with_store(write)
    log_with_context(
        logger,
        30,  # WARNING
        "LLM provider unavailable",
        slot=provider.slot,
        provider=provider.name,
        model=provider.model,
        error_type=type(exc).__name__,
        status_code=_status_code_of(exc),
        recent_failures=failures,
        circuit_opened=failures >= LLM_CIRCUIT_FAILURE_THRESHOLD,
        session_pinned_to=other.slot if session_id else None,
    )


def record_success(provider: ModelProvider, session_id: str | None, ordered: list[ModelProvider]) -> None:
    """Pin the session to the slot that served it when that differs from its order."""
    if not session_id or len(ordered) == 1 or provider.slot == ordered[0].slot:
        return
    _with_store(lambda store: store.set(_pin_key(session_id), provider.slot, LLM_SESSION_PIN_TTL_S))


async def aordered_providers(session_id: str | None = None, *, thinking_enabled: bool = True) -> list[ModelProvider]:
    if len(llm_providers.configured_providers()) == 1:
        return llm_providers.configured_providers()
    return await asyncio.to_thread(ordered_providers, session_id, thinking_enabled=thinking_enabled)


async def arecord_failure(provider: ModelProvider, session_id: str | None, exc: BaseException) -> None:
    if len(llm_providers.configured_providers()) > 1:
        await asyncio.to_thread(record_failure, provider, session_id, exc)


async def arecord_success(provider: ModelProvider, session_id: str | None, ordered: list[ModelProvider]) -> None:
    if session_id and len(ordered) > 1 and provider.slot != ordered[0].slot:
        await asyncio.to_thread(record_success, provider, session_id, ordered)


def log_failover(failed: ModelProvider, next_provider: ModelProvider, *, call_site: str) -> None:
    log_with_context(
        logger,
        30,  # WARNING
        "LLM call failing over to the other provider",
        call_site=call_site,
        from_slot=failed.slot,
        from_provider=failed.name,
        to_slot=next_provider.slot,
        to_provider=next_provider.name,
        to_model=next_provider.model,
    )


def _call_kwargs(
    provider: ModelProvider,
    kwargs: dict[str, Any],
    *,
    thinking_enabled: bool,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    call = dict(kwargs)
    options = provider.request_options(thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort)
    if "extra_body" in options:
        call["extra_body"] = {**(call.get("extra_body") or {}), **options.pop("extra_body")}
    call.update(options)
    if not thinking_enabled and not provider.kind.can_disable_thinking:
        for budget_key in ("max_tokens", "max_completion_tokens"):
            if isinstance(call.get(budget_key), int):
                call[budget_key] += THINKING_HEADROOM_TOKENS
    call["model"] = provider.model
    return call


async def acreate_chat_completion(
    *,
    call_site: str,
    session_id: str | None = None,
    thinking_enabled: bool = True,
    reasoning_effort: str | None = None,
    **kwargs: Any,
) -> tuple[Any, ModelProvider]:
    """``chat.completions.create`` on the first available provider.

    ``kwargs`` are the request without ``model``. Returns the response and the
    provider that served it. Streaming responses fail over only when opening
    the stream fails.
    """
    ordered = await aordered_providers(session_id, thinking_enabled=thinking_enabled)
    for index, provider in enumerate(ordered):
        client = llm_providers.get_async_client(provider)
        try:
            response = await client.chat.completions.create(
                **_call_kwargs(provider, kwargs, thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort)
            )
        except Exception as exc:
            if not is_provider_unavailable(exc):
                raise
            await arecord_failure(provider, session_id, exc)
            if index + 1 >= len(ordered):
                raise
            log_failover(provider, ordered[index + 1], call_site=call_site)
            continue
        await arecord_success(provider, session_id, ordered)
        return response, provider
    raise AssertionError("unreachable: configured_providers() is never empty")


def create_chat_completion(
    *,
    call_site: str,
    session_id: str | None = None,
    thinking_enabled: bool = True,
    reasoning_effort: str | None = None,
    timeout: float | None = None,
    max_retries: int | None = None,
    **kwargs: Any,
) -> tuple[Any, ModelProvider]:
    """Sync ``acreate_chat_completion`` for Prefect material tasks."""
    ordered = ordered_providers(session_id, thinking_enabled=thinking_enabled)
    for index, provider in enumerate(ordered):
        client = llm_providers.get_sync_client(provider, timeout=timeout, max_retries=max_retries)
        try:
            response = client.chat.completions.create(
                **_call_kwargs(provider, kwargs, thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort)
            )
        except Exception as exc:
            if not is_provider_unavailable(exc):
                raise
            record_failure(provider, session_id, exc)
            if index + 1 >= len(ordered):
                raise
            log_failover(provider, ordered[index + 1], call_site=call_site)
            continue
        record_success(provider, session_id, ordered)
        return response, provider
    raise AssertionError("unreachable: configured_providers() is never empty")


def reset_failover_state() -> None:
    """Clear in-process failover state; intended for tests."""
    global _redis_down_until
    _memory.clear()
    _redis_down_until = 0.0
