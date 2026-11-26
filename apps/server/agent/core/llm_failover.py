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
- A successful call on a slot other than the session's order pins the session
  to it, and subsequent successes on a pinned session refresh its sliding TTL
  so an active chat window does not bounce between providers (prefix cache).
- With two credentials on a provider, a rate limit or account error is that key's
  problem: only that key cools for ``LLM_KEY_COOLDOWN_S`` and later calls use the
  other key, with no circuit count or session pin. The failing call itself still goes
  to the other provider, never to the other key. Both keys cooling is a provider failure.

State lives in Redis when ``REDIS_URL`` is configured, shared by API workers
and Prefect workers, and falls back to process memory when Redis is not
configured or fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import os
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from agent.core import llm_providers
from agent.core.llm_providers import ModelProvider
from config.env import get_int_env
from utils.logger import get_logger, log_with_context

if TYPE_CHECKING:
    from services.usage.llm_usage_service import LLMUsageAttribution

logger = get_logger(__name__)

LLM_SESSION_PIN_TTL_S = get_int_env("LLM_SESSION_PIN_TTL_S", 1200)
LLM_CIRCUIT_FAILURE_THRESHOLD = get_int_env("LLM_CIRCUIT_FAILURE_THRESHOLD", 3)
LLM_CIRCUIT_FAILURE_WINDOW_S = get_int_env("LLM_CIRCUIT_FAILURE_WINDOW_S", 60)
LLM_CIRCUIT_OPEN_S = get_int_env("LLM_CIRCUIT_OPEN_S", 120)
LLM_KEY_COOLDOWN_S = get_int_env("LLM_KEY_COOLDOWN_S", 300)

# A model that cannot turn reasoning off spends part of a short budget on it.
THINKING_HEADROOM_TOKENS = 2048

_ACCOUNT_FAILURE_STATUSES = frozenset({401, 402, 403, 404})
_KEY_SCOPED_FAILURE_STATUSES = frozenset({401, 402, 403, 429})
_DOCUMENTED_JSON_OBJECT_MODELS = frozenset({("deepseek", "deepseek-flash")})

# Official contracts, checked 2026-10-25. HTTP and MiniMax business codes are
# different namespaces; no reason is inferred from exception/response prose.
# https://api-docs.deepseek.com/quick_start/error_codes/
# https://platform.minimax.io/docs/api-reference/errorcode
_DEEPSEEK_FAILURE_REASONS = {
    400: "invalid_format", 401: "authentication_failed", 402: "insufficient_balance",
    422: "invalid_parameters", 429: "rate_limited", 500: "server_error", 503: "server_overloaded",
}
_MINIMAX_FAILURE_REASONS = {
    1000: "unknown", 1001: "timeout", 1002: "rate_limited", 1004: "authentication_failed",
    1008: "insufficient_balance", 1024: "server_error", 1026: "input_rejected", 1027: "output_rejected",
    1033: "server_error", 1039: "token_limit", 1041: "connection_limit", 1042: "invalid_characters",
    1046: "identity_policy_rejected", 2013: "invalid_parameters", 2014: "empty_input", 2022: "model_unavailable",
    2045: "rate_growth_limit", 2046: "token_growth_limit", 2047: "connection_growth_limit",
    2049: "authentication_failed", 2053: "insufficient_balance", 2056: "usage_limit",
    2057: "subscription_key_required", 2061: "model_access_denied", 2062: "subscription_rate_limit",
    2064: "server_overloaded", 2066: "timeout", 2067: "usage_limit", 2068: "model_access_denied",
    2069: "model_unavailable",
}
_FAILURE_CALL_SITES = frozenset({"agent", "agent_loop_compaction", "suggest", "polish", "material", "llm_client"})
_RESPONSES_STREAM_ERROR_CODES: dict[str, tuple[str, str]] = {
    "internal_error": ("server_error", "server_error"),
    "server_error": ("server_error", "server_error"),
    "rate_limit_exceeded": ("rate_limited", "rate_limited"),
    "invalid_prompt": ("request_rejected", "input_rejected"),
}
_FAILURE_PARAMETER_FIELDS = frozenset({
    "model", "messages", "max_tokens", "max_completion_tokens", "temperature", "top_p",
    "tools", "tool_choice", "response_format", "stream", "stream_options", "thinking", "reasoning_effort",
    "input", "instructions", "max_output_tokens", "prompt_cache_key", "store", "reasoning",
})


def log_provider_failure(
    provider: ModelProvider,
    exc: BaseException,
    *,
    call_site: str,
    phase: str = "request",
) -> None:
    """One safe diagnostic per failed attempt, including request errors such as 422.

    Inspect only structured codes, never message/status_msg, body text, headers or
    exception repr. Cancellation and our own pre-transport refusal are not failures
    of the provider. This does not control retry, failover, billing or UI behavior.
    """
    import httpx
    import httpx2
    import openai

    from core.error_handler import APIException

    if isinstance(exc, APIException | asyncio.CancelledError | GeneratorExit):
        return
    # SDK exceptions store these as instance data. Do not invoke arbitrary
    # exception properties while diagnosing an error (or replace that error).
    fields = vars(exc)
    status = fields.get("status_code")
    status = status if type(status) is int and 100 <= status <= 599 else None
    if isinstance(exc, TimeoutError | httpx.TimeoutException | httpx2.TimeoutException | openai.APITimeoutError):
        category = "timeout"
    elif isinstance(exc, ConnectionError | httpx.TransportError | httpx2.TransportError | openai.APIConnectionError):
        category = "connection_error"
    elif isinstance(exc, openai.APIResponseValidationError):
        category = "response_validation"
    elif status == 429:
        category = "rate_limited"
    elif status is not None and status >= 500:
        category = "server_error"
    elif status in {400, 413, 422}:
        category = "request_rejected"
    elif status in {401, 403}:
        category = "access_denied"
    elif status == 402:
        category = "payment_required"
    else:
        category = "http_error" if status is not None else "unknown"

    reason = "unknown"
    provider_code = None
    body = fields.get("body") if isinstance(exc, openai.APIError) else None
    base_resp = body.get("base_resp") if isinstance(body, dict) else None
    responses_err = body.get("error") if isinstance(body, dict) else None
    responses_err_code = responses_err.get("code") if isinstance(responses_err, dict) else None
    if isinstance(responses_err_code, str) and responses_err_code in _RESPONSES_STREAM_ERROR_CODES:
        mapped_category, mapped_reason = _RESPONSES_STREAM_ERROR_CODES[responses_err_code]
        if category == "unknown":
            category = mapped_category
        reason = mapped_reason
    elif provider.name == "deepseek":
        reason = _DEEPSEEK_FAILURE_REASONS.get(status, "unknown")
    elif provider.name == "minimax":
        code = base_resp.get("status_code") if isinstance(base_resp, dict) else None
        if type(code) is int and code in _MINIMAX_FAILURE_REASONS:
            provider_code = code
            reason = _MINIMAX_FAILURE_REASONS[code]
    param = fields.get("param") if isinstance(exc, openai.APIError) else None
    log_with_context(
        logger,
        30,  # WARNING: a failed attempt may still be served by the existing backup.
        "LLM provider attempt failed",
        exc_info=False,
        provider=provider.name,
        model=provider.model,
        slot=provider.slot,
        key_slot=provider.key_slot,
        call_site=call_site if call_site in _FAILURE_CALL_SITES else "other",
        phase=phase if phase in {"request", "stream_start", "stream"} else "other",
        status_code=status,
        failure_category=category,
        provider_error_format=(
            "base_resp" if isinstance(base_resp, dict) else "object" if isinstance(body, dict)
            else "missing" if body is None else "non_object"
        ),
        provider_reason=reason,
        provider_error_code=provider_code,
        failed_field=param if isinstance(param, str) and param in _FAILURE_PARAMETER_FIELDS else None,
    )


class JSONResponseFormatUnavailableError(RuntimeError):
    """No configured sync provider has the required documented JSON mode."""


def _requires_json_object(kwargs: dict[str, Any]) -> bool:
    response_format = kwargs.get("response_format")
    return isinstance(response_format, dict) and response_format.get("type") == "json_object"


def _supports_json_object(provider: ModelProvider) -> bool:
    return (provider.name, provider.model) in _DOCUMENTED_JSON_OBJECT_MODELS


def _status_code_of(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def is_upstream_provider_error(exc: BaseException) -> bool:
    """Whether ``exc`` is an upstream model/HTTP/transport failure (not our own refusal or cancellation).

    Covers ``openai.APIError`` (including 4xx request rejections such as context-too-long
    ``BadRequestError``, 429, 5xx, validation, connection/timeout, and in-stream errors),
    ``httpx`` / ``httpx2`` / ``httpcore`` transport and protocol errors, and stdlib
    ``TimeoutError`` / ``ConnectionError``. Excludes platform ``APIException`` (such as
    the hidden daily cost backstop) and cancellation.
    """
    from core.error_handler import APIException

    if isinstance(exc, APIException | asyncio.CancelledError | GeneratorExit):
        return False
    if isinstance(exc, TimeoutError | ConnectionError):
        return True
    try:
        import openai

        if isinstance(exc, openai.APIError):
            return True
    except ImportError:  # pragma: no cover - openai is a hard dependency
        pass
    for mod_name in ("httpx", "httpx2"):
        try:
            mod = __import__(mod_name)
        except ImportError:  # pragma: no cover
            continue
        http_error = getattr(mod, "HTTPError", None)
        if isinstance(http_error, type) and isinstance(exc, http_error):
            return True
    try:
        import httpcore

        if isinstance(
            exc,
            httpcore.NetworkError
            | httpcore.ProtocolError
            | httpcore.TimeoutException
            | httpcore.ProxyError
            | httpcore.UnsupportedProtocol
            | httpcore.ConnectionNotAvailable,
        ):
            return True
    except ImportError:  # pragma: no cover
        pass
    return False


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
    for mod_name in ("httpx", "httpx2"):
        try:
            mod = __import__(mod_name)
        except ImportError:  # pragma: no cover
            continue
        timeout_exc = getattr(mod, "TimeoutException", None)
        transport_exc = getattr(mod, "TransportError", None)
        if isinstance(timeout_exc, type) and isinstance(exc, timeout_exc):
            return True
        if isinstance(transport_exc, type) and isinstance(exc, transport_exc):
            return True
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

    def set_if_exists(self, key: str, value: str, ttl_s: int) -> bool:
        now = time.monotonic()
        with self._lock:
            if self._get(key, now) is None:
                return False
            self._values[key] = (value, now + ttl_s)
            return True

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

    def set_if_exists(self, key: str, value: str, ttl_s: int) -> bool:
        return bool(self._client().set(key, value, ex=ttl_s, xx=True))

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


def _cooldown_key(provider: ModelProvider) -> str:
    return f"llm:key_cooldown:{provider.name}:{provider.key_slot}"


def _cooled_with_alternative(provider: ModelProvider) -> ModelProvider | None:
    """The provider on its other key when this key is cooling and the other one is not."""
    alternative = llm_providers.other_key(provider)
    if alternative is None:
        return None
    keys = [_cooldown_key(provider), _cooldown_key(alternative)]
    cooling, other_cooling = _with_store(lambda store: store.get_many(keys))
    return alternative if cooling and not other_cooling else None


def _apply_key_cooldowns(providers: list[ModelProvider]) -> list[ModelProvider]:
    return [_cooled_with_alternative(provider) or provider for provider in providers]


def _cool_key(provider: ModelProvider, exc: BaseException) -> bool:
    """Cool ``provider``'s key for a key-scoped failure; False when the other key can't take over."""
    if _status_code_of(exc) not in _KEY_SCOPED_FAILURE_STATUSES:
        return False
    alternative = llm_providers.other_key(provider)
    if alternative is None:
        return False

    def write(store: Any) -> bool:
        store.set(_cooldown_key(provider), "1", LLM_KEY_COOLDOWN_S)
        return not store.get_many([_cooldown_key(alternative)])[0]

    taken_over = _with_store(write)
    log_with_context(
        logger,
        30,  # WARNING
        "LLM credential cooling down",
        provider=provider.name,
        key_slot=provider.key_slot,
        status_code=_status_code_of(exc),
        cooldown_s=LLM_KEY_COOLDOWN_S,
        other_key_available=taken_over,
    )
    return taken_over


def is_in_stream_error(exc: BaseException) -> bool:
    """An error event inside an HTTP 200 stream (``openai.APIError`` without a status).

    MiniMax reports "unknown error ... (1000)" and "Internal server error. Please retry"
    this way, but possibly also content rejections; nothing in the event reliably tells
    them apart. Such a call may be answered by the other provider and the session pinned
    there, but it never counts towards the provider's circuit: one author's content must
    not switch every author to the backup.
    """
    try:
        import openai
    except ImportError:  # pragma: no cover - openai is a hard dependency
        return False
    return (
        isinstance(exc, openai.APIError)
        and not isinstance(exc, openai.APIConnectionError)
        and _status_code_of(exc) is None
    )


def ordered_providers(session_id: str | None = None, *, thinking_enabled: bool = True) -> list[ModelProvider]:
    """Providers in the order this call should try them."""
    providers = llm_providers.configured_providers(routing_key=session_id, rotate_if_unkeyed=True)
    return _order_configured_providers(providers, session_id, thinking_enabled=thinking_enabled)


def _order_configured_providers(
    providers: list[ModelProvider],
    session_id: str | None,
    *,
    thinking_enabled: bool,
) -> list[ModelProvider]:
    providers = _apply_key_cooldowns(providers)
    if len(providers) == 1:
        return providers
    primary, backup = providers
    keys = [_open_key(primary.slot), _open_key(backup.slot)]
    if session_id:
        keys.append(_pin_key(session_id))
    values = _with_store(lambda store: store.get_many(keys))
    open_slots = {slot for slot, value in zip((primary.slot, backup.slot), values[:2], strict=True) if value}
    pinned = values[2] if session_id else None
    first, second = primary, backup
    if not thinking_enabled and not first.kind.can_disable_thinking and second.kind.can_disable_thinking:
        # Short no-reasoning calls (suggestions, polish) prefer a provider that can
        # turn reasoning off. Only a preference: a pin or an open circuit still wins.
        first, second = second, first
    if pinned in (primary.slot, backup.slot) and pinned != first.slot:
        first, second = second, first
    if first.slot in open_slots and second.slot not in open_slots:
        first, second = second, first
    return [first, second]


def record_failure(provider: ModelProvider, session_id: str | None, exc: BaseException) -> bool:
    """Cool a key for its own failure; otherwise count a provider failure and pin the session.

    True when only the key cooled: the caller must not pin the session to whichever
    provider answers this call (see ``record_success``), however long that call takes.
    """
    if _cool_key(provider, exc):
        return True
    providers = llm_providers.configured_providers()
    if len(providers) == 1:
        return False
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
    return False


def _refresh_existing_pin(store: Any, key: str, slot: str, ttl_s: int) -> bool:
    set_if_exists = getattr(store, "set_if_exists", None)
    if callable(set_if_exists):
        return bool(set_if_exists(key, slot, ttl_s))
    if store.get_many([key])[0] is None:
        return False
    store.set(key, slot, ttl_s)
    return True


def record_success(
    provider: ModelProvider,
    session_id: str | None,
    ordered: list[ModelProvider],
    *,
    thinking_enabled: bool = True,
) -> None:
    """Pin the session on failover, or refresh an existing pin's sliding TTL.

    Also pins a ``thinking_enabled=True`` session to a non-primary slot when the
    primary's circuit is open so the conversation does not bounce back to the
    primary mid-session when the short circuit window expires.
    """
    if not session_id or len(ordered) == 1:
        return
    key = _pin_key(session_id)
    if provider.slot != ordered[0].slot:
        _with_store(lambda store: store.set(key, provider.slot, LLM_SESSION_PIN_TTL_S))
        return

    def _refresh_or_pin(store: Any) -> None:
        if _refresh_existing_pin(store, key, provider.slot, LLM_SESSION_PIN_TTL_S):
            return
        if thinking_enabled and provider.slot != "primary" and store.get_many([_open_key("primary")])[0]:
            store.set(key, provider.slot, LLM_SESSION_PIN_TTL_S)

    _with_store(_refresh_or_pin)


async def aordered_providers(session_id: str | None = None, *, thinking_enabled: bool = True) -> list[ModelProvider]:
    """Async provider ordering offloaded from the event loop (Redis sticky routing + cooldowns)."""
    return await asyncio.to_thread(
        ordered_providers,
        session_id,
        thinking_enabled=thinking_enabled,
    )


async def arecord_failure(provider: ModelProvider, session_id: str | None, exc: BaseException) -> bool:
    return await asyncio.to_thread(record_failure, provider, session_id, exc)


def pin_session_to_other(provider: ModelProvider, session_id: str | None) -> None:
    """Pin the session to the slot other than ``provider`` without counting a circuit failure."""
    providers = llm_providers.configured_providers()
    if len(providers) == 1 or not session_id:
        return
    other = next(p for p in providers if p.slot != provider.slot)
    _with_store(lambda store: store.set(_pin_key(session_id), other.slot, LLM_SESSION_PIN_TTL_S))
    log_with_context(
        logger,
        30,  # WARNING
        "LLM session pinned to the other provider after an in-stream error",
        slot=provider.slot,
        provider=provider.name,
        model=provider.model,
        session_pinned_to=other.slot,
    )


async def apin_session_to_other(provider: ModelProvider, session_id: str | None) -> None:
    if session_id:
        await asyncio.to_thread(pin_session_to_other, provider, session_id)


async def arecord_success(
    provider: ModelProvider,
    session_id: str | None,
    ordered: list[ModelProvider],
    *,
    thinking_enabled: bool = True,
) -> None:
    if session_id and len(ordered) > 1:
        await asyncio.to_thread(
            record_success,
            provider,
            session_id,
            ordered,
            thinking_enabled=thinking_enabled,
        )


def log_failover(
    failed: ModelProvider,
    next_provider: ModelProvider,
    *,
    call_site: str,
    after_reasoning: bool = False,
    reason: str = "unavailable",
) -> None:
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
        after_reasoning=after_reasoning,
        reason=reason,
    )


def log_failover_skipped(failed: ModelProvider, next_provider: ModelProvider, *, call_site: str) -> None:
    """The other provider would reject this input, so the call ends with the original error."""
    log_with_context(
        logger,
        30,  # WARNING
        "LLM failover skipped: the other provider would reject this input",
        call_site=call_site,
        from_provider=failed.name,
        to_provider=next_provider.name,
        reason="missing_tool_turn_reasoning",
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


def _meter_attempt(
    attribution: LLMUsageAttribution | None,
    provider: ModelProvider,
    started: float,
    *,
    usage: Any = None,
    exc: BaseException | None = None,
) -> None:
    """One ledger row per provider attempt: tokens on success, zero tokens on failure (never raises).

    Our own refusal before the request (the cost backstop) is not an attempt and writes nothing.
    """
    if attribution is None:
        return
    from models.llm_usage import CALL_OUTCOME_SUCCESS
    from services.usage.llm_usage_service import (
        CallTiming,
        UsageTokens,
        elapsed_ms,
        failure_outcome,
        schedule_llm_usage_record,
    )

    outcome = CALL_OUTCOME_SUCCESS if exc is None else failure_outcome(exc)
    if outcome is None:
        return
    # Fire-and-forget: callers (suggest under asyncio.wait_for, routing before the
    # first token) must not wait on the database.
    schedule_llm_usage_record(
        attribution,
        model=provider.model,
        usage=usage,
        tokens=UsageTokens() if exc is not None else None,
        timing=CallTiming(outcome, elapsed_ms(started)),
    )


async def acreate_chat_completion(
    *,
    call_site: str,
    session_id: str | None = None,
    thinking_enabled: bool = True,
    reasoning_effort: str | None = None,
    usage_attribution: LLMUsageAttribution | None = None,
    attempt_sink: dict[str, Any] | None = None,
    max_retries: int | None = None,
    **kwargs: Any,
) -> tuple[Any, ModelProvider]:
    """``chat.completions.create`` on the first available provider.

    ``kwargs`` are the request without ``model``. Returns the response and the
    provider that served it. Streaming responses fail over only when opening
    the stream fails. With ``usage_attribution`` every attempt (failed,
    cancelled or answered) is written to the usage ledger; streaming requests
    are not metered here (their usage and end arrive after this returns).
    ``attempt_sink``, when given, holds the ``"provider"`` and ``"model"`` of the
    latest attempt, so a caller can attribute a call that raised.
    """
    if kwargs.get("stream"):
        usage_attribution = None
    ordered = await aordered_providers(session_id, thinking_enabled=thinking_enabled)
    key_cooled = False
    for index, provider in enumerate(ordered):
        client = llm_providers.get_async_client(provider)
        if max_retries is not None and callable(getattr(client, "with_options", None)):
            client = client.with_options(max_retries=max_retries)
        if attempt_sink is not None:
            attempt_sink["provider"] = provider.name
            attempt_sink["model"] = provider.model
        attempt_started = time.monotonic()
        try:
            response = await client.chat.completions.create(
                **_call_kwargs(provider, kwargs, thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort)
            )
        except asyncio.CancelledError as exc:
            _meter_attempt(usage_attribution, provider, attempt_started, exc=exc)
            raise
        except Exception as exc:
            _meter_attempt(usage_attribution, provider, attempt_started, exc=exc)
            log_provider_failure(provider, exc, call_site=call_site)
            if not is_provider_unavailable(exc):
                raise
            key_cooled = await arecord_failure(provider, session_id, exc) or key_cooled
            if index + 1 >= len(ordered):
                raise
            log_failover(provider, ordered[index + 1], call_site=call_site)
            continue
        _meter_attempt(usage_attribution, provider, attempt_started, usage=getattr(response, "usage", None))
        if not key_cooled:
            await arecord_success(provider, session_id, ordered, thinking_enabled=thinking_enabled)
        return response, provider
    raise AssertionError("unreachable: configured_providers() is never empty")


def _responses_call_kwargs(
    provider: ModelProvider,
    kwargs: dict[str, Any],
    *,
    thinking_enabled: bool,
    reasoning_effort: str | None,
    session_id: str | None,
) -> dict[str, Any]:
    call = dict(kwargs)
    options = provider.responses_request_options(
        thinking_enabled=thinking_enabled,
        reasoning_effort=reasoning_effort,
        session_id=session_id,
    )
    for key, value in options.items():
        call.setdefault(key, value)
    if (
        not thinking_enabled
        and not provider.kind.can_disable_thinking
        and isinstance(call.get("max_output_tokens"), int)
    ):
        call["max_output_tokens"] += THINKING_HEADROOM_TOKENS
    call["model"] = provider.model
    return call


_STREAM_COLLECT_PROGRESS_EVENTS = frozenset({
    "response.output_text.delta",
    "response.reasoning_text.delta",
    "response.reasoning_summary_text.delta",
    "response.refusal.delta",
    "response.function_call_arguments.delta",
    "response.completed",
    "response.incomplete",
    "response.failed",
    "error",
})
_STREAM_COLLECT_REASONING_EVENTS = frozenset({
    "response.reasoning_text.delta",
    "response.reasoning_summary_text.delta",
})
_STREAM_COLLECT_ANSWER_EVENTS = frozenset({
    "response.output_text.delta",
    "response.refusal.delta",
    "response.function_call_arguments.delta",
})


def _responses_failed_event_error(event: Any, provider: ModelProvider) -> Exception:
    import httpx
    import openai

    event_type = getattr(event, "type", None)
    # ResponseErrorEvent carries code/message directly on the event;
    # ResponseFailedEvent nests them under event.response.error.
    err = event if event_type == "error" else getattr(getattr(event, "response", None), "error", None)
    code: str | None = None
    message: str | None = None
    if isinstance(err, dict):
        raw_code, raw_msg = err.get("code"), err.get("message")
        code = raw_code if isinstance(raw_code, str) and raw_code.strip() else None
        message = raw_msg if isinstance(raw_msg, str) and raw_msg.strip() else None
    elif err is not None:
        raw_code, raw_msg = getattr(err, "code", None), getattr(err, "message", None)
        code = raw_code if isinstance(raw_code, str) and raw_code.strip() else None
        message = raw_msg if isinstance(raw_msg, str) and raw_msg.strip() else None
    err_payload: dict[str, str] = {}
    if code is not None:
        err_payload["code"] = code
    if message is not None:
        err_payload["message"] = message
    body: dict[str, Any] | None = {"error": err_payload} if err_payload else None
    url = f"{provider.base_url.rstrip('/')}/responses" if provider.base_url else "https://responses.local/v1/responses"
    exc = openai.APIError(
        message=f"Responses stream failed ({code or 'unknown'}): {message or 'upstream error'}",
        request=httpx.Request("POST", url),
        body=body,
    )
    if code is not None:
        exc.code = code
    return exc


class _StreamCollectError(Exception):
    """Carries stream progress flags across a failed streaming collection."""

    def __init__(self, exc: BaseException, *, answer_started: bool, reasoning_started: bool = False) -> None:
        super().__init__(str(exc))
        self.exc = exc
        self.answer_started = answer_started
        self.reasoning_started = reasoning_started


async def _collect_responses_stream(raw_stream: Any, provider: ModelProvider) -> Any:
    """Drain a streaming ``responses.create(stream=True)`` call into its terminal ``Response``.

    Avoids non-streaming read timeouts on long cached-prefill generations (such as
    300k-token loop compaction) while enforcing the provider's idle-delta timeout and
    preserving pre-answer failover semantics.
    """
    if not hasattr(raw_stream, "__aiter__"):
        return raw_stream
    import httpx
    import openai

    idle_timeout_s = provider.kind.read_timeout_s()
    idle_deadline = time.monotonic() + idle_timeout_s
    answer_started = False
    reasoning_started = False
    final_response: Any = None
    aiter = raw_stream.__aiter__()
    try:
        while True:
            remaining = idle_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{provider.name} stream produced no output for {idle_timeout_s:g}s")
            cm = asyncio.timeout(remaining)
            try:
                async with cm:
                    event = await aiter.__anext__()
            except StopAsyncIteration:
                break
            except TimeoutError:
                if cm.expired():
                    raise TimeoutError(
                        f"{provider.name} stream produced no output for {idle_timeout_s:g}s"
                    ) from None
                raise
            event_type = getattr(event, "type", None)
            delta = getattr(event, "delta", None)
            if event_type in _STREAM_COLLECT_PROGRESS_EVENTS and (
                event_type in {"response.completed", "response.incomplete", "response.failed", "error"}
                or delta != ""
            ):
                idle_deadline = time.monotonic() + idle_timeout_s
            if event_type in _STREAM_COLLECT_REASONING_EVENTS and delta != "":
                reasoning_started = True
            if event_type in _STREAM_COLLECT_ANSWER_EVENTS and delta != "":
                answer_started = True
            if event_type in {"response.failed", "error"}:
                raise _responses_failed_event_error(event, provider)
            if event_type in {"response.completed", "response.incomplete"}:
                final_response = getattr(event, "response", None)
                break
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        raise _StreamCollectError(
            exc, answer_started=answer_started, reasoning_started=reasoning_started
        ) from exc
    finally:
        aclose = getattr(aiter, "aclose", None)
        if callable(aclose):
            with contextlib.suppress(Exception):
                res = aclose()
                if inspect.isawaitable(res):
                    await res
        close = getattr(raw_stream, "close", None)
        if callable(close):
            with contextlib.suppress(Exception):
                res = close()
                if inspect.isawaitable(res):
                    await res
    if final_response is None:
        url = f"{provider.base_url.rstrip('/')}/responses" if provider.base_url else "https://responses.local/v1/responses"
        raise _StreamCollectError(
            openai.APIConnectionError(
                message=f"{provider.name} Responses stream ended before a terminal response event",
                request=httpx.Request("POST", url),
            ),
            answer_started=answer_started,
            reasoning_started=reasoning_started,
        )
    return final_response


async def acreate_response(
    *,
    call_site: str,
    session_id: str | None = None,
    thinking_enabled: bool = True,
    reasoning_effort: str | None = None,
    usage_attribution: LLMUsageAttribution | None = None,
    attempt_sink: dict[str, Any] | None = None,
    max_retries: int | None = None,
    chat_kwargs_builder: Callable[[ModelProvider], dict[str, Any]] | None = None,
    stream_collect: bool = False,
    **kwargs: Any,
) -> tuple[Any, ModelProvider]:
    """``responses.create`` on Responses providers, falling back to ``chat.completions.create``.

    Used by ``agent/loop/compaction.py`` so MiniMax compaction stays on ``/v1/responses`` with
    the session's ``prompt_cache_key`` while mid-call failover to a Chat Completions backup
    (e.g. DeepSeek) builds Chat Completions kwargs via ``chat_kwargs_builder``. When
    ``stream_collect=True``, Responses providers stream SSE tokens and return the terminal
    ``Response`` object (with full usage metering and pre-answer failover) to avoid hitting
    the non-streaming read timeout on long generations.
    """
    if kwargs.get("stream") and not stream_collect:
        usage_attribution = None
    ordered = await aordered_providers(session_id, thinking_enabled=thinking_enabled)
    key_cooled = False
    for index, provider in enumerate(ordered):
        client = llm_providers.get_async_client(provider)
        if max_retries is not None and callable(getattr(client, "with_options", None)):
            client = client.with_options(max_retries=max_retries)
        if attempt_sink is not None:
            attempt_sink["provider"] = provider.name
            attempt_sink["model"] = provider.model
        attempt_started = time.monotonic()
        answer_started = False
        reasoning_started = False
        used_stream_collect = bool(stream_collect and provider.uses_responses_api)
        try:
            if provider.uses_responses_api:
                req_kwargs = _responses_call_kwargs(
                    provider,
                    kwargs,
                    thinking_enabled=thinking_enabled,
                    reasoning_effort=reasoning_effort,
                    session_id=session_id,
                )
                if used_stream_collect:
                    req_kwargs["stream"] = True
                raw_response = await client.responses.create(**req_kwargs)
                response = (
                    await _collect_responses_stream(raw_response, provider)
                    if used_stream_collect
                    else raw_response
                )
            else:
                if chat_kwargs_builder is None:
                    raise RuntimeError(
                        f"Provider {provider.name} uses chat_completions and no chat_kwargs_builder was supplied"
                    )
                chat_kwargs = chat_kwargs_builder(provider)
                response = await client.chat.completions.create(
                    **_call_kwargs(
                        provider,
                        chat_kwargs,
                        thinking_enabled=thinking_enabled,
                        reasoning_effort=reasoning_effort,
                    )
                )
        except asyncio.CancelledError as exc:
            _meter_attempt(usage_attribution, provider, attempt_started, exc=exc)
            raise
        except Exception as raw_exc:
            if isinstance(raw_exc, _StreamCollectError):
                exc = raw_exc.exc
                answer_started = raw_exc.answer_started
                reasoning_started = raw_exc.reasoning_started
            else:
                exc = raw_exc
            _meter_attempt(usage_attribution, provider, attempt_started, exc=exc)
            log_provider_failure(
                provider,
                exc,
                call_site=call_site,
                phase="stream" if used_stream_collect else "request",
            )
            can_failover = False
            if is_provider_unavailable(exc):
                key_cooled = await arecord_failure(provider, session_id, exc) or key_cooled
                can_failover = not answer_started
            elif used_stream_collect and is_in_stream_error(exc):
                await apin_session_to_other(provider, session_id)
                can_failover = not answer_started
            if not can_failover or index + 1 >= len(ordered):
                if isinstance(raw_exc, _StreamCollectError):
                    raise exc from None
                raise
            log_failover(
                provider,
                ordered[index + 1],
                call_site=call_site,
                after_reasoning=reasoning_started,
            )
            continue
        _meter_attempt(usage_attribution, provider, attempt_started, usage=getattr(response, "usage", None))
        if not key_cooled:
            await arecord_success(provider, session_id, ordered, thinking_enabled=thinking_enabled)
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
    if _requires_json_object(kwargs):
        ordered = [provider for provider in ordered if _supports_json_object(provider)]
        if not ordered:
            raise JSONResponseFormatUnavailableError(
                "No configured provider supports the required JSON object response format"
            )
    key_cooled = False
    for index, provider in enumerate(ordered):
        client = llm_providers.get_sync_client(provider, timeout=timeout, max_retries=max_retries)
        try:
            response = client.chat.completions.create(
                **_call_kwargs(provider, kwargs, thinking_enabled=thinking_enabled, reasoning_effort=reasoning_effort)
            )
        except Exception as exc:
            log_provider_failure(provider, exc, call_site=call_site)
            if not is_provider_unavailable(exc):
                raise
            key_cooled = record_failure(provider, session_id, exc) or key_cooled
            if index + 1 >= len(ordered):
                raise
            log_failover(provider, ordered[index + 1], call_site=call_site)
            continue
        if not key_cooled:
            record_success(provider, session_id, ordered, thinking_enabled=thinking_enabled)
        return response, provider
    raise AssertionError("unreachable: configured_providers() is never empty")


def reset_failover_state() -> None:
    """Clear in-process failover state; intended for tests."""
    global _redis_down_until
    _memory.clear()
    _redis_down_until = 0.0
