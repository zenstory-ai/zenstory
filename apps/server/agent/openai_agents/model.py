"""Chat model wiring for the OpenAI Agents SDK, with primary/backup failover.

Each agent run gets a ``FailoverChatModel``. Every model call of the run asks
``agent/core/llm_failover.py`` which provider to use (the chat session's pinned
slot, else the primary) and moves to the other provider only when the call
fails before streaming anything. Providers and models are configured in
``agent/core/llm_providers.py``.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from agent.core import llm_failover, llm_providers
from agent.core.llm_providers import ModelProvider
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# (slot, base_url, api_key, model) -> (client, SDK model built on it)
_models: dict[tuple[str, str, str, str], tuple[Any, Any]] = {}


def _replay_reasoning_content(_context: Any) -> bool:
    """Send prior reasoning back as ``reasoning_content`` within a run.

    MiniMax M3.1-Flash requires the full ``reasoning_content`` of earlier
    tool-call turns; DeepSeek accepts it. After a mid-run failover the
    reasoning came from the other model; it is plain text either way.
    """
    return True


def _sdk_model_for(provider: ModelProvider) -> Any:
    """The SDK Chat Completions model for a provider on the current loop's client.

    Rebuilt when get_async_client() hands out a new client (a new event loop),
    so the model never keeps a client whose connections belong to a closed loop.
    """
    client = llm_providers.get_async_client(provider)
    key = (provider.slot, provider.base_url, provider.api_key, provider.model)
    cached = _models.get(key)
    if cached is not None and cached[0] is client:
        return cached[1]

    # Import lazily so unit tests that do not touch the SDK can still import the
    # server package before dependencies are installed.
    from agents import OpenAIChatCompletionsModel, set_tracing_disabled

    # Neither provider is platform.openai.com, so SDK tracing must not attempt
    # to use an OpenAI tracing key.
    set_tracing_disabled(True)

    model = OpenAIChatCompletionsModel(
        model=provider.model,
        openai_client=client,
        should_replay_reasoning_content=_replay_reasoning_content,
    )
    _models[key] = (client, model)
    return model


def _settings_for(provider: ModelProvider, model_settings: Any) -> Any:
    """Translate the run's reasoning setting into one this provider accepts."""
    reasoning = getattr(model_settings, "reasoning", None)
    requested = getattr(reasoning, "effort", None) if reasoning is not None else None
    effort = provider.reasoning_effort_for(requested)
    if effort == requested:
        return model_settings
    from openai.types.shared import Reasoning

    return dataclasses.replace(model_settings, reasoning=Reasoning(effort=effort) if effort else None)  # type: ignore[arg-type]


def _item_field(item: Any, name: str) -> Any:
    return item.get(name) if isinstance(item, dict) else getattr(item, name, None)


def _awaits_file_body(input_items: Any) -> bool:
    """Whether the latest tool results include a create_file still waiting for its body.

    The two-reply protocol: a reply ends with create_file, the next reply starts with
    <file> and streams the body. create_file marks that moment with ``awaiting_body``.
    Only the trailing batch of tool calls/results counts, so an older unwritten file
    never locks the model out of its tools.
    """
    if not isinstance(input_items, list):
        return False
    for item in reversed(input_items):
        item_type = _item_field(item, "type")
        if item_type in ("function_call", "reasoning"):
            continue
        if item_type != "function_call_output":
            return False
        output = _item_field(item, "output")
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except ValueError:
                continue
        data = output.get("data") if isinstance(output, dict) else None
        if isinstance(data, dict) and data.get("awaiting_body") is True:
            return True
    return False


def _with_body_turn_settings(input_items: Any, model_settings: Any) -> Any:
    """Right after create_file, only a text reply is allowed: the <file> body.

    Models sometimes call update_project/query_files/edit_file here instead and write the
    body through edit_file appends (no live body in the editor, several extra calls).
    tool_choice is a request option, not part of the prompt, so the cache prefix is kept.
    """
    if not _awaits_file_body(input_items) or getattr(model_settings, "tool_choice", None) == "none":
        return model_settings
    log_with_context(logger, 20, "Model call after create_file: text-only reply for the <file> body")
    return dataclasses.replace(model_settings, tool_choice="none")


def _base_model_class() -> type:
    from agents.models.interface import Model

    return Model


class FailoverChatModel:
    """SDK ``Model`` that serves each call from the first available provider."""

    def __init__(
        self,
        session_id: str | None,
        *,
        on_served: Callable[[str], None] | None = None,
    ) -> None:
        self._session_id = session_id
        self._on_served = on_served
        # Model name that served the latest call (usage ledger, logs).
        self.served_model: str | None = None

    async def preferred_model(self) -> str:
        """The model the next call will try first."""
        return (await llm_failover.aordered_providers(self._session_id))[0].model

    def _served(self, provider: ModelProvider) -> None:
        self.served_model = provider.model
        if self._on_served is not None:
            self._on_served(provider.model)

    async def get_response(self, system_instructions: Any, input: Any, model_settings: Any, *args: Any, **kwargs: Any) -> Any:
        model_settings = _with_body_turn_settings(input, model_settings)
        ordered = await llm_failover.aordered_providers(self._session_id)
        for index, provider in enumerate(ordered):
            try:
                response = await _sdk_model_for(provider).get_response(
                    system_instructions, input, _settings_for(provider, model_settings), *args, **kwargs
                )
            except Exception as exc:
                if not llm_failover.is_provider_unavailable(exc):
                    raise
                await llm_failover.arecord_failure(provider, self._session_id, exc)
                if index + 1 >= len(ordered):
                    raise
                llm_failover.log_failover(provider, ordered[index + 1], call_site="agent")
                continue
            self._served(provider)
            await llm_failover.arecord_success(provider, self._session_id, ordered)
            return response
        raise AssertionError("unreachable: configured_providers() is never empty")

    async def stream_response(
        self, system_instructions: Any, input: Any, model_settings: Any, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        model_settings = _with_body_turn_settings(input, model_settings)
        ordered = await llm_failover.aordered_providers(self._session_id)
        for index, provider in enumerate(ordered):
            started = False
            try:
                async for event in _sdk_model_for(provider).stream_response(
                    system_instructions, input, _settings_for(provider, model_settings), *args, **kwargs
                ):
                    if not started:
                        started = True
                        self._served(provider)
                    yield event
            except Exception as exc:
                if not llm_failover.is_provider_unavailable(exc):
                    raise
                # Pins the session to the other provider even mid-stream, so the
                # author's retry goes there; only a call with no output fails over now.
                await llm_failover.arecord_failure(provider, self._session_id, exc)
                if started or index + 1 >= len(ordered):
                    raise
                llm_failover.log_failover(provider, ordered[index + 1], call_site="agent")
                continue
            if not started:
                self._served(provider)
            await llm_failover.arecord_success(provider, self._session_id, ordered)
            return


_failover_classes: dict[Any, type] = {}


def build_failover_chat_model(
    session_id: str | None,
    *,
    on_served: Callable[[str], None] | None = None,
) -> Any:
    """A ``FailoverChatModel`` that is also an instance of the SDK's ``Model``."""
    base = _base_model_class()
    cls = _failover_classes.get(base)
    if cls is None:
        cls = type("FailoverChatModel", (FailoverChatModel, base), {})
        _failover_classes[base] = cls
    return cls(session_id, on_served=on_served)


def reset_sdk_model_cache() -> None:
    """Clear cached SDK models and clients; intended for tests."""
    _models.clear()
    llm_providers.reset_provider_client_cache()
    llm_failover.reset_failover_state()
