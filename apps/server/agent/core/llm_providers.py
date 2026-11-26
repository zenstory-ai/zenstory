"""OpenAI-compatible chat model providers: one primary, an optional backup.

Every agentic LLM call (writing agent, router, suggestions, polish, material
ingestion) goes to the primary provider and, when it is unavailable, to the
backup (see ``agent/core/llm_failover.py``). Two providers are known, DeepSeek
and MiniMax; which one is primary, and the model each slot serves, is runtime
configuration:

- ``LLM_PRIMARY_PROVIDER`` (default ``deepseek``) / ``LLM_PRIMARY_MODEL``
- ``LLM_BACKUP_PROVIDER`` (unset: no backup) / ``LLM_BACKUP_MODEL``
- credentials per provider: ``DEEPSEEK_API_KEY`` / ``DEEPSEEK_BASE_URL``,
  ``MINIMAX_API_KEY`` / ``MINIMAX_BASE_URL``. MiniMax may additionally set
  ``MINIMAX_SECONDARY_API_KEY`` and
  ``MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT`` (default 20). An active session stays pinned
  to one key via a Redis idle sliding TTL (``LLM_KEY_ROUTING_WINDOW_S``, default 1800 s),
  re-drawing after going idle past the window; a run locks its chosen slot in memory for
  its whole length, and ``weight=0`` switches back to primary immediately.
- ``MINIMAX_REASONING_EFFORT`` (default ``high``): MiniMax defaults to ``max``
  when the request names no effort.
- stream / read timeouts:
  - Phase-1 first-event timeout (wait for the first meaningful output/terminal event;
    ``0`` disables): ``MINIMAX_FIRST_EVENT_TIMEOUT_S`` (default 95),
    ``DEEPSEEK_FIRST_EVENT_TIMEOUT_S`` (default 65). Setting this higher than
    ``*_CLIENT_TIMEOUT_S`` (or ``0``) is still bounded on zero-byte TCP silence by
    ``httpx``'s ``read_timeout_s()``.
  - Phase-2 stream idle timeout (longest silence between meaningful events; ``0`` disables
    the stream idle guard while the HTTP client socket timeout keeps the positive default):
    ``MINIMAX_CLIENT_TIMEOUT_S`` (default 120), ``DEEPSEEK_CLIENT_TIMEOUT_S`` (default 90).

A model name must have a price in ``services/usage/pricing.py``; the free-user
cost budget refuses calls to unpriced models.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import math
import os
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Any

from openai import AsyncOpenAI, OpenAI, Timeout

from config.env import get_int_env, get_positive_float_env
from utils.logger import get_logger, log_with_context
from utils.sanitize import sanitize_chat_messages  # noqa: F401 - re-exported for callers

logger = get_logger(__name__)

SLOT_PRIMARY = "primary"
SLOT_BACKUP = "backup"
KEY_SLOT_PRIMARY = "primary"
KEY_SLOT_SECONDARY = "secondary"
API_SURFACE_CHAT_COMPLETIONS = "chat_completions"
API_SURFACE_RESPONSES = "responses"
_VALID_API_SURFACES = frozenset({API_SURFACE_CHAT_COMPLETIONS, API_SURFACE_RESPONSES})
_PROMPT_CACHE_KEY_PREFIX = "zs:"
_PROMPT_CACHE_KEY_MAX_LEN = 64
_MINIMAX_SECONDARY_DEFAULT_WEIGHT_PERCENT = 20
# Coprime with 100: a full context-free cycle visits each percentage bucket once.
_MINIMAX_ROTATION_STEP = 37
# How many running agent runs remember their credential route (one process serves a whole run).
_KEY_ROUTE_MEMORY_RUNS = 4096

# Shared by every provider's request-path client (the names predate the backup).
# The read timeout (longest silence between bytes) is per provider: ProviderKind.read_timeout_s.
LLM_CLIENT_CONNECT_TIMEOUT_S = get_positive_float_env("DEEPSEEK_CLIENT_CONNECT_TIMEOUT_S", 5.0)
LLM_CLIENT_MAX_RETRIES = get_int_env("DEEPSEEK_CLIENT_MAX_RETRIES", 2, minimum=0)
# Idle sliding TTL for per-session MiniMax dual-key stickiness in Redis: active sessions
# refresh on every run so they never flip across UTC hour boundaries, while sessions idle
# longer than this window expire and re-draw at MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT.
LLM_KEY_ROUTING_WINDOW_S = get_positive_float_env("LLM_KEY_ROUTING_WINDOW_S", 1800.0)
_KEY_ROUTE_REDIS_PREFIX = "llm:key_route:"
_KEY_ROUTE_REDIS_MIN_TTL_S = 60
_KEY_ROUTE_REDIS_RETRY_AFTER_S = 30.0


def _get_non_negative_float_env(name: str, default: float) -> float:
    """Read a finite float >= 0 env var (``0`` explicitly disables a stream timeout tier)."""
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0 else default


@dataclass(frozen=True)
class ProviderKind:
    """What differs between providers' OpenAI-compatible endpoints."""

    name: str
    api_key_env: str
    base_url_env: str
    default_base_url: str
    default_model: str
    # DeepSeek turns reasoning off with ``thinking={"type": "disabled"}``;
    # MiniMax M3.1-Flash always reasons and answers that field with HTTP 400.
    can_disable_thinking: bool
    reasoning_efforts: frozenset[str]
    # DeepSeek in thinking mode answers HTTP 400 when an assistant tool-call message after the
    # last user message has no ``reasoning_content``; MiniMax accepts such a message.
    requires_tool_turn_reasoning: bool = False
    # Env var naming the effort sent when the caller names none (None: send nothing).
    reasoning_effort_env: str | None = None
    default_reasoning_effort: str | None = None
    # Longest silence between bytes before a call counts as timed out, and Phase-2 stream idle
    # timeout between meaningful events (0 disables Phase-2 stream guard).
    read_timeout_env: str = "DEEPSEEK_CLIENT_TIMEOUT_S"
    default_read_timeout_s: float = 90.0
    # Phase-1 stream timeout before the first meaningful event arrives (0 disables Phase-1 guard).
    first_event_timeout_env: str = "DEEPSEEK_FIRST_EVENT_TIMEOUT_S"
    default_first_event_timeout_s: float = 65.0
    # API surface for the writing agent loop and compaction: "responses" or "chat_completions".
    api_surface_env: str | None = None
    default_api_surface: str = API_SURFACE_CHAT_COMPLETIONS

    def read_timeout_s(self) -> float:
        return get_positive_float_env(self.read_timeout_env, self.default_read_timeout_s)

    def stream_idle_timeout_s(self) -> float:
        """Phase-2 stream idle timeout between meaningful events (0 disables)."""
        return _get_non_negative_float_env(self.read_timeout_env, self.default_read_timeout_s)

    def first_event_timeout_s(self) -> float:
        """Phase-1 stream timeout before the first meaningful event (0 disables)."""
        return _get_non_negative_float_env(
            self.first_event_timeout_env, self.default_first_event_timeout_s
        )


DEEPSEEK = ProviderKind(
    name="deepseek",
    api_key_env="DEEPSEEK_API_KEY",
    base_url_env="DEEPSEEK_BASE_URL",
    default_base_url="https://api.deepseek.com",
    default_model="deepseek-flash",
    can_disable_thinking=True,
    reasoning_efforts=frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"}),
    requires_tool_turn_reasoning=True,
    # Keep httpx socket read timeout (90s) cleanly above Phase-1 first_event timeout (65s)
    # so silent TCP stalls trigger Phase-1 first_event timeout & non-blocking teardown first.
    read_timeout_env="DEEPSEEK_CLIENT_TIMEOUT_S",
    default_read_timeout_s=90.0,
    first_event_timeout_env="DEEPSEEK_FIRST_EVENT_TIMEOUT_S",
    default_first_event_timeout_s=65.0,
)

MINIMAX = ProviderKind(
    name="minimax",
    api_key_env="MINIMAX_API_KEY",
    base_url_env="MINIMAX_BASE_URL",
    # Mainland endpoint, matching the CNY price table; international accounts
    # set MINIMAX_BASE_URL=https://api.minimax.io/v1.
    default_base_url="https://api.minimaxi.com/v1",
    default_model="MiniMax-M3.1-Flash",
    can_disable_thinking=False,
    reasoning_efforts=frozenset({"low", "medium", "high", "xhigh", "max"}),
    reasoning_effort_env="MINIMAX_REASONING_EFFORT",
    default_reasoning_effort="high",
    # Measured 2026-10-22: ~49s before the first byte on a ~320k-character uncached input;
    # production inputs reach 300k tokens. Phase-1 first_event timeout (95s) bounds dead-node
    # waits cleanly above measured max TTFT without falsely tripping the circuit breaker while
    # keeping Phase-2 idle timeout at 120s.
    read_timeout_env="MINIMAX_CLIENT_TIMEOUT_S",
    default_read_timeout_s=120.0,
    first_event_timeout_env="MINIMAX_FIRST_EVENT_TIMEOUT_S",
    default_first_event_timeout_s=95.0,
    # Instant env-only rollback to /v1/chat/completions via MINIMAX_API_SURFACE=chat_completions.
    api_surface_env="MINIMAX_API_SURFACE",
    default_api_surface=API_SURFACE_RESPONSES,
)

PROVIDER_KINDS: dict[str, ProviderKind] = {kind.name: kind for kind in (DEEPSEEK, MINIMAX)}

# Effort used in place of "thinking off" on a provider that cannot turn it off.
_LOWEST_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def prompt_cache_key_for_session(session_id: str | None) -> str | None:
    """Deterministic ``prompt_cache_key`` for a chat session (shared by loop and compaction)."""
    cleaned = (session_id or "").strip()
    if not cleaned:
        return None
    candidate = f"{_PROMPT_CACHE_KEY_PREFIX}{cleaned}"
    if len(candidate) <= _PROMPT_CACHE_KEY_MAX_LEN:
        return candidate
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:32]
    return f"{_PROMPT_CACHE_KEY_PREFIX}{digest}"


@dataclass(frozen=True)
class ModelProvider:
    """One configured slot: a provider, the model it serves and its credentials."""

    slot: str
    kind: ProviderKind
    model: str
    api_key: str
    base_url: str
    reasoning_effort: str | None
    # Safe credential identity for diagnostics. Never log ``api_key`` itself.
    key_slot: str = KEY_SLOT_PRIMARY
    api_surface: str = API_SURFACE_CHAT_COMPLETIONS
    # Resolved per-attempt stream timeouts (0 disables that phase; negative sentinel resolves from kind).
    first_event_timeout_s: float = -1.0
    stream_idle_timeout_s: float = -1.0

    def __post_init__(self) -> None:
        if self.first_event_timeout_s < 0:
            object.__setattr__(self, "first_event_timeout_s", self.kind.first_event_timeout_s())
        if self.stream_idle_timeout_s < 0:
            object.__setattr__(self, "stream_idle_timeout_s", self.kind.stream_idle_timeout_s())

    @property
    def name(self) -> str:
        return self.kind.name

    @property
    def uses_responses_api(self) -> bool:
        return self.api_surface == API_SURFACE_RESPONSES

    def reasoning_effort_for(self, requested: str | None, *, thinking_enabled: bool = True) -> str | None:
        """The ``reasoning_effort`` to send, or None to send nothing."""
        if not thinking_enabled and not self.kind.can_disable_thinking:
            return next(e for e in _LOWEST_EFFORT_ORDER if e in self.kind.reasoning_efforts)
        if requested and requested in self.kind.reasoning_efforts:
            return requested
        return self.reasoning_effort

    def request_options(self, *, thinking_enabled: bool = True, reasoning_effort: str | None = None) -> dict[str, Any]:
        """Provider-specific Chat Completions kwargs for a reasoning preference."""
        options: dict[str, Any] = {}
        if not thinking_enabled and self.kind.can_disable_thinking:
            options["extra_body"] = {"thinking": {"type": "disabled"}}
            return options
        effort = self.reasoning_effort_for(reasoning_effort, thinking_enabled=thinking_enabled)
        if effort:
            options["reasoning_effort"] = effort
        return options

    def responses_request_options(
        self,
        *,
        thinking_enabled: bool = True,
        reasoning_effort: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Provider-specific Responses API kwargs (stateless store, cache key, reasoning)."""
        options: dict[str, Any] = {"store": False}
        cache_key = prompt_cache_key_for_session(session_id)
        if cache_key:
            options["prompt_cache_key"] = cache_key
        effort = self.reasoning_effort_for(reasoning_effort, thinking_enabled=thinking_enabled)
        if effort:
            options["reasoning"] = {"effort": effort}
        return options


def _kind(name: str, env_name: str) -> ProviderKind:
    kind = PROVIDER_KINDS.get(name.strip().lower())
    if kind is None:
        raise ValueError(f"{env_name}={name!r} is not one of {sorted(PROVIDER_KINDS)}")
    return kind


_minimax_rotation_lock = threading.Lock()
_minimax_rotation_index = 0


def _minimax_secondary_weight_percent() -> int:
    raw = os.getenv("MINIMAX_SECONDARY_KEY_WEIGHT_PERCENT")
    if raw is None:
        return _MINIMAX_SECONDARY_DEFAULT_WEIGHT_PERCENT
    try:
        value = int(raw)
    except ValueError:
        return _MINIMAX_SECONDARY_DEFAULT_WEIGHT_PERCENT
    return value if 0 <= value <= 100 else _MINIMAX_SECONDARY_DEFAULT_WEIGHT_PERCENT


def _next_minimax_bucket() -> int:
    global _minimax_rotation_index
    with _minimax_rotation_lock:
        bucket = (_minimax_rotation_index * _MINIMAX_ROTATION_STEP) % 100
        _minimax_rotation_index = (_minimax_rotation_index + 1) % 100
    return bucket


def _hmac_slot(primary_key: str, seed: str, weight: int) -> str:
    # Keyed with primary_key: a session id may come from the client, which must not choose its bucket.
    digest = hmac.new(primary_key.encode("utf-8"), seed.encode("utf-8"), hashlib.sha256).digest()
    bucket = int.from_bytes(digest[:8], "big") % 100
    return KEY_SLOT_SECONDARY if bucket < weight else KEY_SLOT_PRIMARY


def _key_route_redis_key(session_key: str) -> str:
    return f"{_KEY_ROUTE_REDIS_PREFIX}{session_key}"


def _decode_slot(raw: Any) -> str | None:
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return raw if raw in (KEY_SLOT_PRIMARY, KEY_SLOT_SECONDARY) else None


_key_route_redis_down_until = 0.0


def _sticky_session_slot(
    session_key: str,
    *,
    primary_key: str,
    weight: int,
    run_id: str | None,
) -> str:
    """Resolve a session's sticky key slot using Redis idle sliding TTL, failing open to HMAC.

    - Hit in Redis -> refresh sliding TTL (``EXPIRE``) and return the stored slot.
    - Miss (new session or idle past ``LLM_KEY_ROUTING_WINDOW_S``) -> draw via HMAC on
      ``f"{session_key}:{run_id or nonce}"`` vs ``weight`` and write atomically with
      ``SET key slot NX EX ttl``. If a concurrent caller wins the NX race, ``GET`` and
      return the winner's slot.
    - Redis unconfigured or unavailable -> fail open (with 30s error cooldown) to
      deterministic HMAC on ``session_key``.
    """
    global _key_route_redis_down_until
    if os.getenv("REDIS_URL") and time.monotonic() >= _key_route_redis_down_until:
        from services.infra.redis_client import get_redis_client

        key = _key_route_redis_key(session_key)
        ttl = max(_KEY_ROUTE_REDIS_MIN_TTL_S, int(LLM_KEY_ROUTING_WINDOW_S))
        try:
            client = get_redis_client()
            existing = _decode_slot(client.get(key))
            if existing is not None:
                client.expire(key, ttl)
                return existing
            draw_seed = f"{session_key}:{run_id or uuid.uuid4().hex}"
            candidate = _hmac_slot(primary_key, draw_seed, weight)
            if client.set(key, candidate, nx=True, ex=ttl):
                return candidate
            winner = _decode_slot(client.get(key))
            return winner if winner is not None else candidate
        except Exception as exc:  # noqa: BLE001 - fail open to deterministic session hash
            _key_route_redis_down_until = time.monotonic() + _KEY_ROUTE_REDIS_RETRY_AFTER_S
            log_with_context(
                logger,
                30,  # WARNING
                "LLM key routing state: Redis unavailable, falling back to deterministic session hash",
                error_type=type(exc).__name__,
            )
    return _hmac_slot(primary_key, session_key, weight)


def _minimax_key(
    routing_key: str | None,
    *,
    rotate_if_unkeyed: bool,
    run_id: str | None = None,
) -> tuple[str, str]:
    primary_key = os.getenv(MINIMAX.api_key_env) or ""
    secondary_key = os.getenv("MINIMAX_SECONDARY_API_KEY") or ""
    weight = _minimax_secondary_weight_percent()
    # Guard #1: instant switchback when primary/secondary key is absent or weight == 0,
    # before consulting in-memory run routes or Redis sticky keys.
    if not primary_key or not secondary_key or weight == 0:
        return primary_key, KEY_SLOT_PRIMARY

    # Guard #2: intra-run lock in memory (0 extra Redis IO for subsequent calls in the same run).
    if run_id:
        with _key_route_lock:
            cached_slot = _key_routes.get(run_id)
        if cached_slot in (KEY_SLOT_PRIMARY, KEY_SLOT_SECONDARY):
            return (
                (secondary_key, KEY_SLOT_SECONDARY)
                if cached_slot == KEY_SLOT_SECONDARY
                else (primary_key, KEY_SLOT_PRIMARY)
            )

    if routing_key:
        slot = _sticky_session_slot(
            routing_key,
            primary_key=primary_key,
            weight=weight,
            run_id=run_id,
        )
    elif run_id:
        slot = _hmac_slot(primary_key, run_id, weight)
    elif rotate_if_unkeyed:
        slot = KEY_SLOT_SECONDARY if _next_minimax_bucket() < weight else KEY_SLOT_PRIMARY
    else:
        return primary_key, KEY_SLOT_PRIMARY

    if run_id:
        with _key_route_lock:
            slot = _key_routes.setdefault(run_id, slot)
            while len(_key_routes) > _KEY_ROUTE_MEMORY_RUNS:
                _key_routes.popitem(last=False)

    if slot == KEY_SLOT_SECONDARY:
        return secondary_key, KEY_SLOT_SECONDARY
    return primary_key, KEY_SLOT_PRIMARY


def other_key(provider: ModelProvider) -> ModelProvider | None:
    """The same provider on its other credential, when dual-key routing is active.

    Weight 0 means the secondary key was switched off on purpose: it is no alternative.
    """
    if provider.kind is not MINIMAX:
        return None
    secondary_key = os.getenv("MINIMAX_SECONDARY_API_KEY") or ""
    if not secondary_key or _minimax_secondary_weight_percent() == 0:
        return None
    if provider.key_slot == KEY_SLOT_SECONDARY:
        return replace(provider, api_key=os.getenv(MINIMAX.api_key_env) or "", key_slot=KEY_SLOT_PRIMARY)
    return replace(provider, api_key=secondary_key, key_slot=KEY_SLOT_SECONDARY)


_key_route_lock = threading.Lock()
_key_routes: OrderedDict[str, str] = OrderedDict()


def reset_key_routing_state() -> None:
    """Clear remembered run routes and Redis circuit cooldown; intended for tests."""
    global _key_route_redis_down_until
    with _key_route_lock:
        _key_routes.clear()
    _key_route_redis_down_until = 0.0


def _build_provider(
    slot: str,
    kind: ProviderKind,
    model_env: str,
    *,
    routing_key: str | None = None,
    rotate_if_unkeyed: bool = False,
    run_id: str | None = None,
) -> ModelProvider:
    effort = kind.default_reasoning_effort
    if kind.reasoning_effort_env:
        raw = (os.getenv(kind.reasoning_effort_env) or "").strip().lower()
        if raw in kind.reasoning_efforts:
            effort = raw
    surface = kind.default_api_surface
    if kind.api_surface_env:
        raw_surface = (os.getenv(kind.api_surface_env) or "").strip().lower()
        if raw_surface in _VALID_API_SURFACES:
            surface = raw_surface
    api_key, key_slot = (
        _minimax_key(routing_key, rotate_if_unkeyed=rotate_if_unkeyed, run_id=run_id)
        if kind is MINIMAX
        else (os.getenv(kind.api_key_env) or "", KEY_SLOT_PRIMARY)
    )
    return ModelProvider(
        slot=slot,
        kind=kind,
        model=(os.getenv(model_env) or "").strip() or kind.default_model,
        api_key=api_key,
        base_url=os.getenv(kind.base_url_env) or kind.default_base_url,
        reasoning_effort=effort,
        key_slot=key_slot,
        api_surface=surface,
        first_event_timeout_s=kind.first_event_timeout_s(),
        stream_idle_timeout_s=kind.stream_idle_timeout_s(),
    )


_warned_backup_without_key: set[str] = set()


def configured_providers(
    *,
    routing_key: str | None = None,
    rotate_if_unkeyed: bool = False,
) -> list[ModelProvider]:
    """``[primary]`` or ``[primary, backup]`` from the environment.

    A primary without an API key raises. A backup without one is skipped with
    a warning: a half-configured backup must not take the primary down.
    """
    # ``agent_run_id`` is generated server-side and keeps every model call in a run
    # together; the session decides that run's sticky key slot (via Redis idle sliding TTL,
    # failing open to deterministic HMAC). Do not route on a client-controlled request id.
    from utils.request_context import get_agent_run_id

    run_id = get_agent_run_id()
    primary = _build_provider(
        SLOT_PRIMARY,
        _kind(os.getenv("LLM_PRIMARY_PROVIDER") or DEEPSEEK.name, "LLM_PRIMARY_PROVIDER"),
        "LLM_PRIMARY_MODEL",
        routing_key=routing_key,
        rotate_if_unkeyed=rotate_if_unkeyed,
        run_id=run_id,
    )
    # A selected secondary credential does not make the documented primary
    # credential optional; deployment rollback must always have a primary key.
    if not (os.getenv(primary.kind.api_key_env) or ""):
        raise ValueError(f"{primary.kind.api_key_env} is required")
    providers = [primary]
    backup_name = (os.getenv("LLM_BACKUP_PROVIDER") or "").strip()
    if backup_name:
        backup = _build_provider(
            SLOT_BACKUP,
            _kind(backup_name, "LLM_BACKUP_PROVIDER"),
            "LLM_BACKUP_MODEL",
            routing_key=routing_key,
            rotate_if_unkeyed=rotate_if_unkeyed,
            run_id=run_id,
        )
        if os.getenv(backup.kind.api_key_env) or "":
            providers.append(backup)
        elif backup.kind.api_key_env not in _warned_backup_without_key:
            _warned_backup_without_key.add(backup.kind.api_key_env)
            log_with_context(
                logger,
                30,  # WARNING
                "LLM backup provider configured without an API key; running without a backup",
                provider=backup.name,
                api_key_env=backup.kind.api_key_env,
            )
    return providers


def primary_provider() -> ModelProvider:
    return configured_providers()[0]


def primary_model_name() -> str:
    """The configured primary model, without requiring its key (reports, logs)."""
    try:
        kind = _kind(os.getenv("LLM_PRIMARY_PROVIDER") or DEEPSEEK.name, "LLM_PRIMARY_PROVIDER")
    except ValueError:
        return ""
    return (os.getenv("LLM_PRIMARY_MODEL") or "").strip() or kind.default_model


def _running_loop() -> asyncio.AbstractEventLoop | None:
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return None


# (slot, base_url, api_key) -> (client, loop it was opened on)
_async_clients: dict[tuple[str, str, str], tuple[AsyncOpenAI, asyncio.AbstractEventLoop | None]] = {}
# (slot, base_url, api_key, timeout, max_retries) -> client
_sync_clients: dict[tuple[str, str, str, float, int], OpenAI] = {}


def get_async_client(provider: ModelProvider) -> AsyncOpenAI:
    """The AsyncOpenAI client for a provider slot, one per event loop.

    The client's connection pool belongs to the loop that opened it. openai 2.x
    raises "Event loop is closed" when a pooled connection from an earlier,
    closed loop is reused, so a new loop gets a new client. The API server runs
    one loop, so in production this is one client per slot.
    """
    from services.usage.model_call_guard import install_async_cost_guard

    key = (provider.slot, provider.base_url, provider.api_key)
    loop = _running_loop()
    cached = _async_clients.get(key)
    if cached is not None and not (loop is not None and cached[1] is not None and loop is not cached[1]):
        return install_async_cost_guard(cached[0])
    client = AsyncOpenAI(
        api_key=provider.api_key,
        base_url=provider.base_url,
        timeout=Timeout(provider.kind.read_timeout_s(), connect=LLM_CLIENT_CONNECT_TIMEOUT_S),
        max_retries=LLM_CLIENT_MAX_RETRIES,
    )
    _async_clients[key] = (client, loop)
    log_with_context(
        logger,
        20,  # INFO
        "LLM OpenAI-compatible client initialized",
        slot=provider.slot,
        provider=provider.name,
        key_slot=provider.key_slot,
        base_url=provider.base_url,
        model=provider.model,
    )
    return install_async_cost_guard(client)


def get_sync_client(
    provider: ModelProvider,
    *,
    timeout: float | None = None,
    max_retries: int | None = None,
) -> OpenAI:
    """A sync OpenAI client for a provider slot (material ingestion)."""
    from services.usage.model_call_guard import install_sync_cost_guard

    resolved_timeout = timeout if timeout is not None else provider.kind.read_timeout_s()
    resolved_retries = max_retries if max_retries is not None else LLM_CLIENT_MAX_RETRIES
    key = (provider.slot, provider.base_url, provider.api_key, resolved_timeout, resolved_retries)
    client = _sync_clients.get(key)
    if client is None:
        client = OpenAI(
            api_key=provider.api_key,
            base_url=provider.base_url,
            timeout=resolved_timeout,
            max_retries=resolved_retries,
        )
        _sync_clients[key] = client
        log_with_context(
            logger,
            20,  # INFO
            "LLM OpenAI-compatible sync client initialized",
            slot=provider.slot,
            provider=provider.name,
            key_slot=provider.key_slot,
            base_url=provider.base_url,
            model=provider.model,
        )
    return install_sync_cost_guard(client)


def reset_provider_client_cache() -> None:
    """Clear cached clients; intended for tests."""
    global _minimax_rotation_index
    _async_clients.clear()
    _sync_clients.clear()
    _warned_backup_without_key.clear()
    with _minimax_rotation_lock:
        _minimax_rotation_index = 0
