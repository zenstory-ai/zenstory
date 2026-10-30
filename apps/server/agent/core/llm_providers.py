"""OpenAI-compatible chat model providers: one primary, an optional backup.

Every agentic LLM call (writing agent, router, suggestions, polish, material
ingestion) goes to the primary provider and, when it is unavailable, to the
backup (see ``agent/core/llm_failover.py``). Two providers are known, DeepSeek
and MiniMax; which one is primary, and the model each slot serves, is runtime
configuration:

- ``LLM_PRIMARY_PROVIDER`` (default ``deepseek``) / ``LLM_PRIMARY_MODEL``
- ``LLM_BACKUP_PROVIDER`` (unset: no backup) / ``LLM_BACKUP_MODEL``
- credentials per provider: ``DEEPSEEK_API_KEY`` / ``DEEPSEEK_BASE_URL``,
  ``MINIMAX_API_KEY`` / ``MINIMAX_BASE_URL``
- ``MINIMAX_REASONING_EFFORT`` (default ``high``): MiniMax defaults to ``max``
  when the request names no effort.
- read timeout (longest silence between bytes, including the wait for the first byte):
  ``MINIMAX_CLIENT_TIMEOUT_S`` (default 120), ``DEEPSEEK_CLIENT_TIMEOUT_S`` (default 45).

A model name must have a price in ``services/usage/pricing.py``; the free-user
cost budget refuses calls to unpriced models.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, OpenAI, Timeout

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

SLOT_PRIMARY = "primary"
SLOT_BACKUP = "backup"


def _get_positive_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _get_non_negative_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


# Shared by every provider's request-path client (the names predate the backup).
# The read timeout (longest silence between bytes) is per provider: ProviderKind.read_timeout_s.
LLM_CLIENT_CONNECT_TIMEOUT_S = _get_positive_float_env("DEEPSEEK_CLIENT_CONNECT_TIMEOUT_S", 5.0)
LLM_CLIENT_MAX_RETRIES = _get_non_negative_int_env("DEEPSEEK_CLIENT_MAX_RETRIES", 2)


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
    # Longest silence between bytes before a call counts as timed out. Includes the wait for the
    # first byte, which grows with uncached input length.
    read_timeout_env: str = "DEEPSEEK_CLIENT_TIMEOUT_S"
    default_read_timeout_s: float = 45.0

    def read_timeout_s(self) -> float:
        return _get_positive_float_env(self.read_timeout_env, self.default_read_timeout_s)


DEEPSEEK = ProviderKind(
    name="deepseek",
    api_key_env="DEEPSEEK_API_KEY",
    base_url_env="DEEPSEEK_BASE_URL",
    default_base_url="https://api.deepseek.com",
    default_model="deepseek-flash",
    can_disable_thinking=True,
    reasoning_efforts=frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"}),
    requires_tool_turn_reasoning=True,
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
    # production inputs reach 260k tokens.
    read_timeout_env="MINIMAX_CLIENT_TIMEOUT_S",
    default_read_timeout_s=120.0,
)

PROVIDER_KINDS: dict[str, ProviderKind] = {kind.name: kind for kind in (DEEPSEEK, MINIMAX)}

# Effort used in place of "thinking off" on a provider that cannot turn it off.
_LOWEST_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelProvider:
    """One configured slot: a provider, the model it serves and its credentials."""

    slot: str
    kind: ProviderKind
    model: str
    api_key: str
    base_url: str
    reasoning_effort: str | None

    @property
    def name(self) -> str:
        return self.kind.name

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


def _kind(name: str, env_name: str) -> ProviderKind:
    kind = PROVIDER_KINDS.get(name.strip().lower())
    if kind is None:
        raise ValueError(f"{env_name}={name!r} is not one of {sorted(PROVIDER_KINDS)}")
    return kind


def _build_provider(slot: str, kind: ProviderKind, model_env: str) -> ModelProvider:
    effort = kind.default_reasoning_effort
    if kind.reasoning_effort_env:
        raw = (os.getenv(kind.reasoning_effort_env) or "").strip().lower()
        if raw in kind.reasoning_efforts:
            effort = raw
    return ModelProvider(
        slot=slot,
        kind=kind,
        model=(os.getenv(model_env) or "").strip() or kind.default_model,
        api_key=os.getenv(kind.api_key_env) or "",
        base_url=os.getenv(kind.base_url_env) or kind.default_base_url,
        reasoning_effort=effort,
    )


_warned_backup_without_key: set[str] = set()


def configured_providers() -> list[ModelProvider]:
    """``[primary]`` or ``[primary, backup]`` from the environment.

    A primary without an API key raises. A backup without one is skipped with
    a warning: a half-configured backup must not take the primary down.
    """
    primary = _build_provider(
        SLOT_PRIMARY,
        _kind(os.getenv("LLM_PRIMARY_PROVIDER") or DEEPSEEK.name, "LLM_PRIMARY_PROVIDER"),
        "LLM_PRIMARY_MODEL",
    )
    if not primary.api_key:
        raise ValueError(f"{primary.kind.api_key_env} is required")
    providers = [primary]
    backup_name = (os.getenv("LLM_BACKUP_PROVIDER") or "").strip()
    if backup_name:
        backup = _build_provider(SLOT_BACKUP, _kind(backup_name, "LLM_BACKUP_PROVIDER"), "LLM_BACKUP_MODEL")
        if backup.api_key:
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
    return _build_provider(SLOT_PRIMARY, kind, "LLM_PRIMARY_MODEL").model


def _is_empty_assistant_message(message: object) -> bool:
    """True for an assistant message carrying no content, tool_calls, or other payload.

    The openai-agents Chat Completions converter (0.17.x) can emit a spurious empty
    assistant message between a ``tool_calls`` assistant message and its ``tool`` results
    when the model returns a tool-call turn with empty text. DeepSeek then rejects the
    request with HTTP 400 ("an assistant message with 'tool_calls' must be followed by
    tool messages responding to each 'tool_call_id'"). Such empty assistant messages are
    pure no-ops, so dropping them keeps the tool-call/tool-result sequence well-formed.
    """
    if not isinstance(message, dict) or message.get("role") != "assistant":
        return False
    if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
        return False
    content = message.get("content")
    if isinstance(content, str):
        return not content.strip()
    return not content  # None or empty list/sequence


def sanitize_chat_messages(messages: object) -> object:
    """Drop empty assistant no-op messages that providers reject."""
    if not isinstance(messages, list):
        return messages
    if not any(_is_empty_assistant_message(m) for m in messages):
        return messages
    return [m for m in messages if not _is_empty_assistant_message(m)]


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
    return install_sync_cost_guard(client)


def reset_provider_client_cache() -> None:
    """Clear cached clients; intended for tests."""
    _async_clients.clear()
    _sync_clients.clear()
    _warned_backup_without_key.clear()
