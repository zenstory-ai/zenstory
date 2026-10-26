"""Chat model wiring for the OpenAI Agents SDK, with primary/backup failover.

Each agent run gets a ``FailoverChatModel``. Every model call of the run asks
``agent/core/llm_failover.py`` which provider to use (the chat session's pinned
slot, else the primary) and moves to the other provider only when the call
fails before streaming any output. Lifecycle events such as ``response.created``
are held back until the first output, so a provider that stalls after them still
fails over. Providers and models are configured in
``agent/core/llm_providers.py``.

Each provider attempt is timed for the admin model metrics: the usage hook
writes a successful call's row (with its tokens) from ``take_call_timing``;
failed and cancelled attempts are written here with zero tokens.
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

from agent.core import llm_failover, llm_providers
from agent.core.llm_providers import ModelProvider

if TYPE_CHECKING:
    from services.usage.llm_usage_service import CallTiming, LLMUsageAttribution

# (slot, base_url, api_key, model) -> (client, SDK model built on it)
_models: dict[tuple[str, str, str, str], tuple[Any, Any]] = {}

# The first event that carries generated output: text, reasoning, refusal or tool
# arguments (the SDK streams those as deltas). ``response.created`` is emitted on
# the first chunk of any kind and is not output.
_FIRST_OUTPUT_EVENT_TYPES = frozenset({
    "response.output_text.delta",
    "response.reasoning_text.delta",
    "response.reasoning_summary_text.delta",
    "response.refusal.delta",
    "response.function_call_arguments.delta",
})
_COMPLETED_EVENT_TYPE = "response.completed"


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
        usage_attribution: LLMUsageAttribution | None = None,
    ) -> None:
        self._session_id = session_id
        self._on_served = on_served
        self._usage_attribution = usage_attribution
        # Model name that served the latest call (usage ledger, logs).
        self.served_model: str | None = None
        # Timing of the latest successful call, taken once by the usage hook.
        self._call_timing: CallTiming | None = None

    def take_call_timing(self) -> CallTiming | None:
        timing, self._call_timing = self._call_timing, None
        return timing

    def _succeeded(self, started: float, ttft_ms: int | None = None) -> None:
        from models.llm_usage import CALL_OUTCOME_SUCCESS
        from services.usage.llm_usage_service import CallTiming, elapsed_ms

        self._call_timing = CallTiming(CALL_OUTCOME_SUCCESS, elapsed_ms(started), ttft_ms)

    def _record_failed_attempt(
        self, provider: ModelProvider, started: float, exc: BaseException, ttft_ms: int | None = None
    ) -> None:
        """A zero-token ledger row for an attempt that failed or was cancelled (never raises).

        Our own refusal before the request (the cost backstop) is not an attempt and writes nothing.
        """
        if self._usage_attribution is None:
            return
        from services.usage.llm_usage_service import (
            CallTiming,
            UsageTokens,
            elapsed_ms,
            failure_outcome,
            schedule_llm_usage_record,
        )

        outcome = failure_outcome(exc)
        if outcome is None:
            return
        schedule_llm_usage_record(
            self._usage_attribution,
            model=provider.model,
            tokens=UsageTokens(),
            timing=CallTiming(outcome, elapsed_ms(started), ttft_ms),
        )

    async def preferred_model(self) -> str:
        """The model the next call will try first."""
        return (await llm_failover.aordered_providers(self._session_id))[0].model

    def _served(self, provider: ModelProvider) -> None:
        self.served_model = provider.model
        if self._on_served is not None:
            self._on_served(provider.model)

    async def get_response(self, system_instructions: Any, input: Any, model_settings: Any, *args: Any, **kwargs: Any) -> Any:
        ordered = await llm_failover.aordered_providers(self._session_id)
        self._call_timing = None
        for index, provider in enumerate(ordered):
            attempt_started = time.monotonic()
            try:
                response = await _sdk_model_for(provider).get_response(
                    system_instructions, input, _settings_for(provider, model_settings), *args, **kwargs
                )
            except asyncio.CancelledError as exc:
                self._record_failed_attempt(provider, attempt_started, exc)
                raise
            except Exception as exc:
                self._record_failed_attempt(provider, attempt_started, exc)
                llm_failover.log_provider_failure(provider, exc, call_site="agent")
                if not llm_failover.is_provider_unavailable(exc):
                    raise
                await llm_failover.arecord_failure(provider, self._session_id, exc)
                if index + 1 >= len(ordered):
                    raise
                llm_failover.log_failover(provider, ordered[index + 1], call_site="agent")
                continue
            self._succeeded(attempt_started)
            self._served(provider)
            await llm_failover.arecord_success(provider, self._session_id, ordered)
            return response
        raise AssertionError("unreachable: configured_providers() is never empty")

    async def stream_response(
        self, system_instructions: Any, input: Any, model_settings: Any, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        ordered = await llm_failover.aordered_providers(self._session_id)
        self._call_timing = None
        for index, provider in enumerate(ordered):
            # Events before the first output or completion (``response.created``,
            # ``output_item.added``) are held back: until output reaches the runner,
            # this attempt can still be dropped and the call served by the other provider.
            preamble: list[Any] = []
            started = False
            attempt_started = time.monotonic()
            ttft_ms: int | None = None
            completed = False
            try:
                async for event in _sdk_model_for(provider).stream_response(
                    system_instructions, input, _settings_for(provider, model_settings), *args, **kwargs
                ):
                    event_type = getattr(event, "type", None)
                    # An empty delta (openai-agents 0.17.6 emits one for a role chunk with
                    # ``content: ""``) is not output yet.
                    if (
                        ttft_ms is None
                        and event_type in _FIRST_OUTPUT_EVENT_TYPES
                        and getattr(event, "delta", None) != ""
                    ):
                        from services.usage.llm_usage_service import elapsed_ms

                        ttft_ms = elapsed_ms(attempt_started)
                    if event_type == _COMPLETED_EVENT_TYPE:
                        # Timed when the provider finished, before the SDK consumes the event.
                        completed = True
                        self._succeeded(attempt_started, ttft_ms)
                    if not started:
                        if ttft_ms is None and not completed:
                            preamble.append(event)
                            continue
                        started = True
                        self._served(provider)
                        for held in preamble:
                            yield held
                        preamble.clear()
                    yield event
            except (asyncio.CancelledError, GeneratorExit) as exc:
                if not completed:
                    self._record_failed_attempt(provider, attempt_started, exc, ttft_ms)
                raise
            except Exception as exc:
                if not completed:
                    self._record_failed_attempt(provider, attempt_started, exc, ttft_ms)
                    llm_failover.log_provider_failure(
                        provider, exc, call_site="agent", phase="stream" if started else "stream_start"
                    )
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
                for held in preamble:
                    yield held
            if not completed:
                self._succeeded(attempt_started, ttft_ms)
            await llm_failover.arecord_success(provider, self._session_id, ordered)
            return


_failover_classes: dict[Any, type] = {}


def build_failover_chat_model(
    session_id: str | None,
    *,
    on_served: Callable[[str], None] | None = None,
    usage_attribution: LLMUsageAttribution | None = None,
) -> Any:
    """A ``FailoverChatModel`` that is also an instance of the SDK's ``Model``."""
    base = _base_model_class()
    cls = _failover_classes.get(base)
    if cls is None:
        cls = type("FailoverChatModel", (FailoverChatModel, base), {})
        _failover_classes[base] = cls
    return cls(session_id, on_served=on_served, usage_attribution=usage_attribution)


def reset_sdk_model_cache() -> None:
    """Clear cached SDK models and clients; intended for tests."""
    _models.clear()
    llm_providers.reset_provider_client_cache()
    llm_failover.reset_failover_state()
