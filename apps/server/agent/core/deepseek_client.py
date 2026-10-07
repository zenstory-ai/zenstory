"""Shared DeepSeek OpenAI-compatible client wiring.

ZenStory intentionally supports one chat model for agentic LLM calls:
``deepseek-flash`` through DeepSeek's OpenAI-compatible endpoint. API keys
must be supplied at runtime via ``DEEPSEEK_API_KEY``.
"""

from __future__ import annotations

import os

import httpx
from openai import AsyncOpenAI

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

DEEPSEEK_CHAT_MODEL = "deepseek-flash"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


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


DEEPSEEK_CLIENT_TIMEOUT_S = _get_positive_float_env("DEEPSEEK_CLIENT_TIMEOUT_S", 45.0)
DEEPSEEK_CLIENT_CONNECT_TIMEOUT_S = _get_positive_float_env(
    "DEEPSEEK_CLIENT_CONNECT_TIMEOUT_S",
    5.0,
)
DEEPSEEK_CLIENT_MAX_RETRIES = _get_non_negative_int_env(
    "DEEPSEEK_CLIENT_MAX_RETRIES",
    2,
)

_client: AsyncOpenAI | None = None


def _is_empty_assistant_message(message: object) -> bool:
    """True for an assistant message carrying no content, tool_calls, or other payload.

    The openai-agents Chat Completions converter (0.17.x) can emit a spurious empty
    assistant message between a ``tool_calls`` assistant message and its ``tool`` results
    when DeepSeek returns a tool-call turn with empty text. DeepSeek then rejects the
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
    """Drop empty assistant no-op messages that DeepSeek's API rejects."""
    if not isinstance(messages, list):
        return messages
    if not any(_is_empty_assistant_message(m) for m in messages):
        return messages
    return [m for m in messages if not _is_empty_assistant_message(m)]


def _install_empty_assistant_message_guard(client: AsyncOpenAI) -> AsyncOpenAI:
    """Wrap chat.completions.create to sanitize outbound messages for DeepSeek."""
    completions = client.chat.completions
    original_create = completions.create

    async def _create(*args, **kwargs):
        if "messages" in kwargs:
            kwargs["messages"] = sanitize_chat_messages(kwargs["messages"])
        return await original_create(*args, **kwargs)

    completions.create = _create  # type: ignore[method-assign]
    return client


def get_deepseek_base_url() -> str:
    """Return the configured DeepSeek endpoint base URL."""
    return os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_DEEPSEEK_BASE_URL


def get_deepseek_client() -> AsyncOpenAI:
    """Return the singleton AsyncOpenAI client configured for DeepSeek."""
    global _client
    if _client is not None:
        from services.usage.model_call_guard import install_async_cost_guard
        return install_async_cost_guard(_client)

    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise ValueError("DEEPSEEK_API_KEY is required")

    base_url = get_deepseek_base_url()
    _client = _install_empty_assistant_message_guard(
        AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=httpx.Timeout(
                DEEPSEEK_CLIENT_TIMEOUT_S,
                connect=DEEPSEEK_CLIENT_CONNECT_TIMEOUT_S,
            ),
            max_retries=DEEPSEEK_CLIENT_MAX_RETRIES,
        )
    )

    log_with_context(
        logger,
        20,  # INFO
        "DeepSeek OpenAI-compatible client initialized",
        base_url=base_url,
        model=DEEPSEEK_CHAT_MODEL,
    )
    from services.usage.model_call_guard import install_async_cost_guard
    return install_async_cost_guard(_client)


def reset_deepseek_client_cache() -> None:
    """Clear singleton client; intended for tests."""
    global _client
    _client = None
