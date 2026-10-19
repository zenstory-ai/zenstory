"""DeepSeek model wiring for the OpenAI Agents SDK.

The writing agent intentionally supports one provider/model only:
DeepSeek's OpenAI-compatible Chat Completions endpoint with
``deepseek-flash``. Rollback is expected to happen via git revert rather
than runtime engine switching, so this module does not expose provider routing.
"""

from __future__ import annotations

from typing import Any

from agent.core.deepseek_client import (
    DEEPSEEK_CHAT_MODEL,
    get_deepseek_client,
    reset_deepseek_client_cache,
)

DEEPSEEK_WRITING_MODEL = DEEPSEEK_CHAT_MODEL

_model: Any | None = None
_model_client: Any | None = None


def get_deepseek_chat_model() -> Any:
    """Return the OpenAI Agents Chat Completions model for the current DeepSeek client.

    Rebuilt when get_deepseek_client() hands out a new client (a new event loop),
    so the model never keeps a client whose connections belong to a closed loop.
    """
    global _model, _model_client
    client = get_deepseek_client()
    if _model is not None and _model_client is client:
        return _model

    # Import lazily so unit tests that do not touch the SDK can still import the
    # server package before dependencies are installed.
    from agents import OpenAIChatCompletionsModel, set_tracing_disabled

    # DeepSeek is not platform.openai.com, so SDK tracing must not attempt to use
    # an OpenAI tracing key.
    set_tracing_disabled(True)

    _model = OpenAIChatCompletionsModel(
        model=DEEPSEEK_WRITING_MODEL,
        openai_client=client,
    )
    _model_client = client
    return _model


def reset_deepseek_sdk_cache() -> None:
    """Clear singleton SDK objects; intended for tests."""
    global _model, _model_client
    _model = None
    _model_client = None
    reset_deepseek_client_cache()
