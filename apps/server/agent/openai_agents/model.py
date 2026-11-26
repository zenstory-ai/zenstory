"""Chat model wiring for the OpenAI Agents SDK, with primary/backup failover.

Each agent run gets a ``FailoverChatModel``. Every model call of the run asks
``agent/core/llm_failover.py`` which provider to use (the chat session's pinned
slot, else the primary) and moves to the other provider when the call fails
before streaming anything the author or the run can use: lifecycle events such
as ``response.created`` are held back until the first output, and a provider that
fails after streaming only reasoning is replaced too (the reasoning already shown
stays; the backup's call adds its own). Once text, a refusal or tool arguments
have streamed the error propagates. Providers and models are configured in
``agent/core/llm_providers.py``.

Each provider attempt is timed for the admin model metrics: the usage hook
writes a successful call's row (with its tokens) from ``take_call_timing``;
failed and cancelled attempts are written here with zero tokens.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import time
from collections.abc import AsyncIterator, Callable
from typing import TYPE_CHECKING, Any

from agent.core import llm_failover, llm_providers
from agent.core.llm_providers import ModelProvider
from utils.logger import get_logger, log_with_context

if TYPE_CHECKING:
    from services.usage.llm_usage_service import CallTiming, LLMUsageAttribution

logger = get_logger(__name__)

# (slot, base_url, api_key, model, api_surface) -> (client, SDK model built on it)
_models: dict[tuple[str, str, str, str, str], tuple[Any, Any]] = {}

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
_INCOMPLETE_EVENT_TYPE = "response.incomplete"
_FAILED_EVENT_TYPE = "response.failed"
_REASONING_DELTA_EVENT_TYPES = frozenset({
    "response.reasoning_text.delta",
    "response.reasoning_summary_text.delta",
})
# Output the runner acts on (reply text, file bodies, tool calls). A call that failed
# before any of these produced nothing that the backup's answer would repeat.
_ANSWER_OUTPUT_EVENT_TYPES = frozenset({
    "response.output_text.delta",
    "response.refusal.delta",
    "response.function_call_arguments.delta",
})


# Bounded timeout for detached background teardown of a timed-out/wedged stream.
_STREAM_TEARDOWN_TIMEOUT_S = 2.0
# Strong references to detached teardown tasks so GC never drops them mid-cleanup.
_pending_stream_teardowns: set[asyncio.Task[Any]] = set()
_MISSING = object()


def _is_meaningful_stream_progress(event: Any) -> bool:
    """Whether ``event`` carries real generation progress (not an empty keepalive/lifecycle frame)."""
    event_type = getattr(event, "type", None)
    if event_type in (
        _COMPLETED_EVENT_TYPE,
        _INCOMPLETE_EVENT_TYPE,
        _FAILED_EVENT_TYPE,
        "error",
        "response.error",
    ):
        return True
    if event_type not in _FIRST_OUTPUT_EVENT_TYPES:
        return False
    delta = getattr(event, "delta", _MISSING)
    return delta is _MISSING or (isinstance(delta, str) and delta != "")


def _schedule_stream_teardown(
    aiter: Any,
    stream: Any | None = None,
    *,
    timeout_s: float | None = None,
) -> asyncio.Task[Any] | None:
    """Detach and close a timed-out/wedged stream in the background with a bounded timeout.

    On stream timeout, awaiting ``aiter.aclose()`` inline can block failover/recovery behind a
    hung TCP socket. Scheduling bounded cleanup in a tracked background task returns control
    right away while bounding resource cleanup.
    """
    aclose = getattr(aiter, "aclose", None)
    has_aclose = callable(aclose)
    close = (
        getattr(stream, "close", None)
        if stream is not None and (stream is not aiter or not has_aclose)
        else None
    )
    has_close = callable(close)
    if not has_aclose and not has_close:
        return None

    effective_timeout = _STREAM_TEARDOWN_TIMEOUT_S if timeout_s is None else timeout_s

    async def _teardown() -> None:
        deadline = time.monotonic() + max(0.0, effective_timeout)
        if has_aclose:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                with contextlib.suppress(Exception):
                    res = aclose()
                    if inspect.isawaitable(res):
                        await asyncio.wait_for(res, timeout=remaining)
        if has_close:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                with contextlib.suppress(Exception):
                    res = close()
                    if inspect.isawaitable(res):
                        await asyncio.wait_for(res, timeout=remaining)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    task = loop.create_task(_teardown())
    _pending_stream_teardowns.add(task)
    task.add_done_callback(_pending_stream_teardowns.discard)
    return task


def _build_stream_timeout_error(
    provider_name: str,
    *,
    phase: str,
    timeout_s: float,
) -> TimeoutError:
    log_with_context(
        logger,
        30,  # WARNING
        "LLM stream timeout",
        provider=provider_name,
        timeout_phase=phase,
        timeout_s=timeout_s,
    )
    if phase == "first_event":
        return TimeoutError(
            f"{provider_name} stream produced no output before first_event for {timeout_s:g}s (first_event timeout)"
        )
    return TimeoutError(
        f"{provider_name} stream produced no output for {timeout_s:g}s (idle timeout)"
    )


async def _iter_with_stream_timeouts(
    stream: AsyncIterator[Any],
    *,
    first_event_timeout_s: float,
    idle_timeout_s: float,
    provider_name: str,
) -> AsyncIterator[Any]:
    """Yield events from ``stream`` with two-tier per-attempt timeouts and non-blocking teardown.

    - Phase 1 (``first_event``): waits up to ``first_event_timeout_s`` until the first
      meaningful progress event arrives (``0`` disables Phase 1). Empty keepalives and
      lifecycle frames such as ``response.created`` never reset this timer.
    - Phase 2 (``idle``): once first meaningful progress arrives, waits up to
      ``idle_timeout_s`` between consecutive meaningful progress events (``0`` disables
      Phase 2). Empty keepalives still never reset this timer.
    - Runs ``__anext__()`` inside the caller's task/``contextvars.Context`` so OpenAI Agents
      SDK tracing spans remain valid across yields. On timeout, ``aclose()`` is detached into
      a bounded background task so a wedged TCP socket never blocks failover/recovery. Normal
      exits and caller cancellations still await ``aclose()`` inline.
    """
    aiter = stream.__aiter__()
    received_first_meaningful = False
    deadline: float | None = (
        time.monotonic() + first_event_timeout_s if first_event_timeout_s > 0 else None
    )
    timed_out = False
    exiting_via_generator_exit = False
    try:
        while True:
            if deadline is not None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    phase = "idle" if received_first_meaningful else "first_event"
                    timeout_s = idle_timeout_s if received_first_meaningful else first_event_timeout_s
                    raise _build_stream_timeout_error(provider_name, phase=phase, timeout_s=timeout_s)
                cm = asyncio.timeout(remaining)
                try:
                    async with cm:
                        event = await aiter.__anext__()
                except StopAsyncIteration:
                    break
                except TimeoutError:
                    if cm.expired():
                        timed_out = True
                        phase = "idle" if received_first_meaningful else "first_event"
                        timeout_s = idle_timeout_s if received_first_meaningful else first_event_timeout_s
                        raise _build_stream_timeout_error(
                            provider_name, phase=phase, timeout_s=timeout_s
                        ) from None
                    raise
            else:
                try:
                    event = await aiter.__anext__()
                except StopAsyncIteration:
                    break
            if _is_meaningful_stream_progress(event):
                received_first_meaningful = True
                deadline = time.monotonic() + idle_timeout_s if idle_timeout_s > 0 else None
            yield event
    except GeneratorExit:
        exiting_via_generator_exit = True
        raise
    finally:
        if timed_out or exiting_via_generator_exit:
            _schedule_stream_teardown(aiter, stream)
        else:
            aclose = getattr(aiter, "aclose", None)
            if callable(aclose):
                with contextlib.suppress(Exception):
                    res = aclose()
                    if inspect.isawaitable(res):
                        await asyncio.wait_for(res, timeout=_STREAM_TEARDOWN_TIMEOUT_S)


async def _iter_with_idle_timeout(
    stream: AsyncIterator[Any],
    idle_timeout_s: float,
    provider_name: str,
) -> AsyncIterator[Any]:
    """Compatibility wrapper applying ``idle_timeout_s`` to both first-event and idle phases."""
    guarded = _iter_with_stream_timeouts(
        stream,
        first_event_timeout_s=idle_timeout_s,
        idle_timeout_s=idle_timeout_s,
        provider_name=provider_name,
    )
    exiting_via_generator_exit = False
    try:
        async for event in guarded:
            yield event
    except GeneratorExit:
        exiting_via_generator_exit = True
        raise
    finally:
        if exiting_via_generator_exit:
            _schedule_stream_teardown(guarded)
        else:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(guarded.aclose(), timeout=_STREAM_TEARDOWN_TIMEOUT_S)


def _replay_reasoning_content(_context: Any) -> bool:
    """Send prior reasoning back as ``reasoning_content`` within a run.

    DeepSeek answers HTTP 400 when a tool-call turn lacks ``reasoning_content``.
    MiniMax M3.1-Flash accepts such a turn but its docs ask to keep the full
    reasoning for best results; dropping it has not been evaluated. After a mid-run
    failover the reasoning came from the other model; it is plain text either way.
    """
    return True


def bridge_responses_reasoning_for_chat_completions(input: Any) -> Any:
    """Copy Responses ``reasoning`` ``content[].reasoning_text`` into ``summary`` for Chat Completions replay.

    MiniMax ``/v1/responses`` returns reasoning text under ``content`` (with ``summary: []``),
    while the SDK's ``chatcmpl_converter.Converter.items_to_messages`` only populates DeepSeek's
    ``reasoning_content`` from ``summary``. Bridging on a shallow copy preserves mid-run failover
    to DeepSeek without mutating the Responses wire history.
    """
    if not isinstance(input, list):
        return input

    needs_bridge = False
    for item in input:
        if (
            isinstance(item, dict)
            and item.get("type") == "reasoning"
            and not item.get("summary")
            and isinstance(item.get("content"), list)
            and any(
                isinstance(part, dict)
                and part.get("type") == "reasoning_text"
                and isinstance(part.get("text"), str)
                and part["text"]
                for part in item["content"]
            )
        ):
            needs_bridge = True
            break
    if not needs_bridge:
        return input

    bridged: list[Any] = []
    for item in input:
        if (
            isinstance(item, dict)
            and item.get("type") == "reasoning"
            and not item.get("summary")
            and isinstance(item.get("content"), list)
        ):
            summary = [
                {"type": "summary_text", "text": part["text"]}
                for part in item["content"]
                if isinstance(part, dict)
                and part.get("type") == "reasoning_text"
                and isinstance(part.get("text"), str)
                and part["text"]
            ]
            if summary:
                bridged.append({**item, "summary": summary})
                continue
        bridged.append(item)
    return bridged


def _rejects_missing_tool_turn_reasoning(provider: ModelProvider, input: Any) -> bool:
    """Whether ``provider`` would reject this run input with HTTP 400 for missing reasoning.

    The SDK replays a tool-call turn's reasoning only when it is non-empty, so a call the
    other provider answered without thinking leaves an assistant tool-call message without
    ``reasoning_content``. Checked on the messages the SDK would send; a conversion error
    reports no gap and keeps the existing failover.
    """
    if not provider.kind.requires_tool_turn_reasoning or isinstance(input, str):
        return False
    try:
        from agents.models.chatcmpl_converter import Converter

        bridged = bridge_responses_reasoning_for_chat_completions(input)
        messages = Converter.items_to_messages(
            bridged, model=provider.model, should_replay_reasoning_content=_replay_reasoning_content
        )
    except Exception:
        return False
    last_user = max((i for i, m in enumerate(messages) if m.get("role") == "user"), default=-1)
    return any(
        m.get("role") == "assistant" and m.get("tool_calls") and not m.get("reasoning_content")
        for m in messages[last_user + 1:]
    )


def _sdk_model_for(provider: ModelProvider) -> Any:
    """The SDK model (Responses or Chat Completions) for a provider on the current loop's client.

    Rebuilt when get_async_client() hands out a new client (a new event loop),
    so the model never keeps a client whose connections belong to a closed loop.
    """
    client = llm_providers.get_async_client(provider)
    key = (provider.slot, provider.base_url, provider.api_key, provider.model, provider.api_surface)
    cached = _models.get(key)
    if cached is not None and cached[0] is client:
        return cached[1]

    # Import lazily so unit tests that do not touch the SDK can still import the
    # server package before dependencies are installed.
    from agents import OpenAIChatCompletionsModel, OpenAIResponsesModel, set_tracing_disabled

    # Neither provider is platform.openai.com, so SDK tracing must not attempt
    # to use an OpenAI tracing key.
    set_tracing_disabled(True)

    model: Any
    if provider.uses_responses_api:
        model = OpenAIResponsesModel(
            model=provider.model,
            openai_client=client,
        )
    else:
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


def _apply_surface_settings(provider: ModelProvider, model_settings: Any, session_id: str | None) -> Any:
    """Apply API-surface-specific settings without leaking Responses params to Chat Completions.

    - Responses providers (MiniMax on ``/v1/responses``): set ``store=False`` and inject the
      deterministic session ``prompt_cache_key`` into ``extra_args``.
    - Chat Completions providers (DeepSeek, or MiniMax rolled back via ``MINIMAX_API_SURFACE``):
      strip any ``prompt_cache_key`` from ``extra_args`` and clear ``store`` so unknown fields
      never leak on failover.
    """
    if not dataclasses.is_dataclass(model_settings):
        return model_settings
    updates: dict[str, Any] = {}
    existing_extra = getattr(model_settings, "extra_args", None)
    if provider.uses_responses_api:
        if getattr(model_settings, "store", None) is not False:
            updates["store"] = False
        cache_key = llm_providers.prompt_cache_key_for_session(session_id)
        if cache_key and not (isinstance(existing_extra, dict) and existing_extra.get("prompt_cache_key")):
            new_extra = dict(existing_extra) if isinstance(existing_extra, dict) else {}
            new_extra["prompt_cache_key"] = cache_key
            updates["extra_args"] = new_extra
    else:
        if getattr(model_settings, "store", None) is not None:
            updates["store"] = None
        if isinstance(existing_extra, dict) and "prompt_cache_key" in existing_extra:
            cleaned_extra = {k: v for k, v in existing_extra.items() if k != "prompt_cache_key"}
            updates["extra_args"] = cleaned_extra or None
    if not updates:
        return model_settings
    return dataclasses.replace(model_settings, **updates)


def _incomplete_max_output_tokens_to_completed(event: Any) -> Any | None:
    """Convert ``response.incomplete(reason='max_output_tokens')`` to a ``ResponseCompletedEvent``.

    ``OpenAIResponsesModel.stream_response`` and ``run_loop.run_single_turn_streamed`` treat
    ``response.incomplete`` as a fatal ``ModelBehaviorError``, whereas ZenStory handles
    max-output-token truncation gracefully (recording usage, saving partial ``<file>`` bodies,
    and emitting ``OUTPUT_CAPPED_KEY`` / ``OUTPUT_TRUNCATED_STOP_REASON``). Non-token
    incomplete reasons (e.g. ``content_filter``) still propagate untouched.
    """
    if getattr(event, "type", None) != _INCOMPLETE_EVENT_TYPE:
        return None
    resp = getattr(event, "response", None)
    if resp is None:
        return None
    details = getattr(resp, "incomplete_details", None)
    reason = details.get("reason") if isinstance(details, dict) else getattr(details, "reason", None)
    if reason != "max_output_tokens":
        return None
    seq = getattr(event, "sequence_number", 0)
    try:
        from openai.types.responses import Response, ResponseCompletedEvent

        if isinstance(resp, Response):
            return ResponseCompletedEvent(type=_COMPLETED_EVENT_TYPE, response=resp, sequence_number=seq)
        return ResponseCompletedEvent.model_construct(type=_COMPLETED_EVENT_TYPE, response=resp, sequence_number=seq)
    except Exception:  # noqa: BLE001 - lightweight stub fallback when openai types unavailable
        from types import SimpleNamespace

        return SimpleNamespace(type=_COMPLETED_EVENT_TYPE, response=resp, sequence_number=seq)


def _response_failed_to_api_error(event: Any, provider: ModelProvider) -> Exception | None:
    """Convert a Responses API ``response.failed`` SSE event into an in-stream ``openai.APIError``.

    ``OpenAIResponsesModel.stream_response`` raises ``ModelBehaviorError`` after yielding
    ``response.failed``, which bypasses ``FailoverChatModel``'s in-stream failover and causes
    ``StreamBillingTracker`` to treat upstream failures (such as MiniMax ``internal_error``)
    as ``non_model_error`` instead of ``upstream_model_error``. Raising an in-stream
    ``openai.APIError`` (``status_code=None``) routes it through the exact same pre-answer
    failover, session-pinning, and refund path as Chat Completions stream errors.
    """
    if getattr(event, "type", None) != _FAILED_EVENT_TYPE:
        return None
    import httpx
    import openai

    resp = getattr(event, "response", None)
    err = getattr(resp, "error", None)
    code: str | None = None
    message: str | None = None
    if isinstance(err, dict):
        raw_code = err.get("code")
        raw_msg = err.get("message")
        code = raw_code if isinstance(raw_code, str) and raw_code.strip() else None
        message = raw_msg if isinstance(raw_msg, str) and raw_msg.strip() else None
    elif err is not None:
        raw_code = getattr(err, "code", None)
        raw_msg = getattr(err, "message", None)
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


def _base_model_class() -> type:
    from agents.models.interface import Model

    return Model


class FailoverChatModel:
    """SDK ``Model`` that serves each call from the first available provider.

    Pending file-body replies keep the exact same ``tools`` and default ``tool_choice``
    (unset / ``"auto"``) as normal tool steps: MiniMax strips the ~4.3k tools block from
    its server-side template whenever ``tool_choice="none"``, shattering the cached prompt
    prefix and splitting writing rounds into two ping-ponging cache trees. The two-reply
    protocol is enforced by the synchronous ``file_body_pending`` guard in
    ``tools_adapter.py`` + ``_stop_run_after_tools`` in ``runner.py`` + ``FILE_BODY_RESUME``.
    """

    def __init__(
        self,
        session_id: str | None,
        *,
        on_served: Callable[[str], None] | None = None,
        on_served_provider: Callable[[ModelProvider], None] | None = None,
        usage_attribution: LLMUsageAttribution | None = None,
    ) -> None:
        self._session_id = session_id
        self._on_served = on_served
        self._on_served_provider = on_served_provider
        self._usage_attribution = usage_attribution
        # Provider/model identity that served the latest call (usage ledger, logs, cache key).
        self.served_provider_name: str | None = None
        self.served_model: str | None = None
        self.served_key_slot: str | None = None
        # Timing of the latest successful call, taken once by the usage hook.
        self._call_timing: CallTiming | None = None
        # The latest successful call's thinking before its first answer output
        # (reasoning characters, milliseconds or None when it never answered), taken
        # once by the usage hook and logged with that call's reasoning tokens.
        self._thinking_before_answer: tuple[int, int | None] | None = None
        # Upstream provider exception that escaped the latest call after failover was
        # exhausted or disallowed; read by runner.py to classify terminal model failures.
        self._unrecovered_provider_error: BaseException | None = None
        # True when the latest streamed Responses call ended with
        # ``response.incomplete(reason="max_output_tokens")`` and was adapted to a capped
        # completion so runner.py can handle truncation without a fatal ModelBehaviorError.
        self.last_response_output_capped: bool = False

    @property
    def unrecovered_provider_error(self) -> BaseException | None:
        return self._unrecovered_provider_error

    def take_call_timing(self) -> CallTiming | None:
        timing, self._call_timing = self._call_timing, None
        return timing

    def take_thinking_before_answer(self) -> tuple[int, int | None] | None:
        thinking, self._thinking_before_answer = self._thinking_before_answer, None
        return thinking

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

    def _served(self, provider: ModelProvider) -> None:
        self.served_provider_name = provider.name
        self.served_model = provider.model
        self.served_key_slot = provider.key_slot
        if self._on_served is not None:
            self._on_served(provider.model)
        if self._on_served_provider is not None:
            self._on_served_provider(provider)

    async def get_response(self, system_instructions: Any, input: Any, model_settings: Any, *args: Any, **kwargs: Any) -> Any:
        ordered = await llm_failover.aordered_providers(self._session_id)
        key_cooled = False
        self._call_timing = None
        self._unrecovered_provider_error = None
        self.last_response_output_capped = False
        for index, provider in enumerate(ordered):
            attempt_started = time.monotonic()
            effective_input = (
                input
                if provider.uses_responses_api
                else bridge_responses_reasoning_for_chat_completions(input)
            )
            effective_settings = _apply_surface_settings(
                provider, _settings_for(provider, model_settings), self._session_id
            )
            try:
                response = await _sdk_model_for(provider).get_response(
                    system_instructions, effective_input, effective_settings, *args, **kwargs
                )
            except asyncio.CancelledError as exc:
                self._record_failed_attempt(provider, attempt_started, exc)
                raise
            except Exception as exc:
                self._record_failed_attempt(provider, attempt_started, exc)
                llm_failover.log_provider_failure(provider, exc, call_site="agent")
                can_failover = llm_failover.is_provider_unavailable(exc)
                if can_failover:
                    key_cooled = await llm_failover.arecord_failure(provider, self._session_id, exc) or key_cooled
                if can_failover and index + 1 < len(ordered):
                    if _rejects_missing_tool_turn_reasoning(ordered[index + 1], input):
                        llm_failover.log_failover_skipped(provider, ordered[index + 1], call_site="agent")
                    else:
                        llm_failover.log_failover(provider, ordered[index + 1], call_site="agent")
                        continue
                if llm_failover.is_upstream_provider_error(exc):
                    self._unrecovered_provider_error = exc
                raise
            self._succeeded(attempt_started)
            self._served(provider)
            if not key_cooled:
                await llm_failover.arecord_success(provider, self._session_id, ordered)
            return response
        raise AssertionError("unreachable: configured_providers() is never empty")

    async def stream_response(
        self, system_instructions: Any, input: Any, model_settings: Any, *args: Any, **kwargs: Any
    ) -> AsyncIterator[Any]:
        ordered = await llm_failover.aordered_providers(self._session_id)
        key_cooled = False
        self._call_timing = None
        self._thinking_before_answer = None
        self._unrecovered_provider_error = None
        self.last_response_output_capped = False
        for index, provider in enumerate(ordered):
            # Events before the first output or completion (``response.created``,
            # ``output_item.added``) are held back: until output reaches the runner,
            # this attempt can still be dropped and the call served by the other provider.
            preamble: list[Any] = []
            started = False
            answered = False
            attempt_started = time.monotonic()
            ttft_ms: int | None = None
            # Observation only: how long the call thought before its first answer output.
            thinking_chars = 0
            first_answer_ms: int | None = None
            completed = False
            self.last_response_output_capped = False
            effective_input = (
                input
                if provider.uses_responses_api
                else bridge_responses_reasoning_for_chat_completions(input)
            )
            effective_settings = _apply_surface_settings(
                provider, _settings_for(provider, model_settings), self._session_id
            )
            guarded_stream: Any = None
            exiting_via_generator_exit = False
            try:
                raw_stream = _sdk_model_for(provider).stream_response(
                    system_instructions, effective_input, effective_settings, *args, **kwargs
                )
                guarded_stream = _iter_with_stream_timeouts(
                    raw_stream,
                    first_event_timeout_s=provider.first_event_timeout_s,
                    idle_timeout_s=provider.stream_idle_timeout_s,
                    provider_name=provider.name,
                )
                async for event in guarded_stream:
                    failed_exc = _response_failed_to_api_error(event, provider)
                    if failed_exc is not None:
                        raise failed_exc
                    converted_cap = _incomplete_max_output_tokens_to_completed(event)
                    if converted_cap is not None:
                        self.last_response_output_capped = True
                        event = converted_cap
                    event_type = getattr(event, "type", None)
                    # An empty delta (openai-agents 0.17.6 emits one for a role chunk with
                    # ``content: ""``) is not output yet.
                    if (
                        ttft_ms is None
                        and event_type in _FIRST_OUTPUT_EVENT_TYPES
                        and _is_meaningful_stream_progress(event)
                    ):
                        from services.usage.llm_usage_service import elapsed_ms

                        ttft_ms = elapsed_ms(attempt_started)
                    if event_type in _ANSWER_OUTPUT_EVENT_TYPES and getattr(event, "delta", None) != "":
                        if not answered:
                            from services.usage.llm_usage_service import elapsed_ms

                            first_answer_ms = elapsed_ms(attempt_started)
                        answered = True
                    elif event_type in _REASONING_DELTA_EVENT_TYPES and not answered:
                        delta = getattr(event, "delta", None)
                        thinking_chars += len(delta) if isinstance(delta, str) else 0
                    if event_type == _COMPLETED_EVENT_TYPE:
                        # Timed when the provider finished, before the SDK consumes the event.
                        completed = True
                        self._succeeded(attempt_started, ttft_ms)
                        self._thinking_before_answer = (thinking_chars, first_answer_ms)
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
                    if converted_cap is not None:
                        # Stop before OpenAIResponsesModel.stream_response raises
                        # ModelBehaviorError after yielding response.incomplete.
                        break
            except GeneratorExit as exc:
                exiting_via_generator_exit = True
                if not completed:
                    self._record_failed_attempt(provider, attempt_started, exc, ttft_ms)
                raise
            except asyncio.CancelledError as exc:
                if not completed:
                    self._record_failed_attempt(provider, attempt_started, exc, ttft_ms)
                raise
            except Exception as exc:
                if not completed:
                    self._record_failed_attempt(provider, attempt_started, exc, ttft_ms)
                    llm_failover.log_provider_failure(
                        provider, exc, call_site="agent", phase="stream" if started else "stream_start"
                    )
                    log_thinking_before_answer(
                        provider.model, outcome="failed", thinking_chars=thinking_chars,
                        first_answer_ms=first_answer_ms, started=attempt_started, key_slot=provider.key_slot,
                    )
                can_failover = False
                if llm_failover.is_provider_unavailable(exc):
                    # Pins the session to the other provider even mid-stream, so the
                    # author's retry goes there, and counts towards the provider's circuit.
                    key_cooled = await llm_failover.arecord_failure(provider, self._session_id, exc) or key_cooled
                    can_failover = True
                elif llm_failover.is_in_stream_error(exc):
                    # Possibly about this request's content: this session moves to the
                    # other provider, other authors stay where they are (no circuit count).
                    await llm_failover.apin_session_to_other(provider, self._session_id)
                    can_failover = True
                if can_failover:
                    # A call that streamed only reasoning fails over now (nothing it produced
                    # would be repeated); after answer output the error propagates.
                    if answered or completed or index + 1 >= len(ordered):
                        log_with_context(
                            logger, 30, "LLM stream failover not attempted",
                            provider=provider.name,
                            provider_count=len(ordered),
                            provider_index=index,
                            answer_started=answered,
                            response_completed=completed,
                            reason=(
                                "response_completed" if completed else "answer_started" if answered
                                else "single_provider" if len(ordered) == 1 else "providers_exhausted"
                            ),
                        )
                    elif _rejects_missing_tool_turn_reasoning(ordered[index + 1], input):
                        # The backup would answer this input with HTTP 400: end with the original error
                        # (retryable; the session pin above sends the author's retry to the backup).
                        llm_failover.log_failover_skipped(provider, ordered[index + 1], call_site="agent")
                    else:
                        llm_failover.log_failover(
                            provider, ordered[index + 1], call_site="agent", after_reasoning=started
                        )
                        continue
                if llm_failover.is_upstream_provider_error(exc):
                    self._unrecovered_provider_error = exc
                raise
            finally:
                if guarded_stream is not None:
                    if exiting_via_generator_exit:
                        _schedule_stream_teardown(guarded_stream)
                    else:
                        with contextlib.suppress(Exception):
                            await guarded_stream.aclose()
            if not started:
                self._served(provider)
                for held in preamble:
                    yield held
            if not completed:
                self._succeeded(attempt_started, ttft_ms)
                self._thinking_before_answer = (thinking_chars, first_answer_ms)
            if not key_cooled:
                await llm_failover.arecord_success(provider, self._session_id, ordered)
            return


def log_thinking_before_answer(
    model: str,
    *,
    outcome: str,
    thinking_chars: int,
    first_answer_ms: int | None,
    started: float | None = None,
    duration_ms: int | None = None,
    reasoning_tokens: int | None = None,
    output_tokens: int | None = None,
    key_slot: str | None = None,
) -> None:
    """One line per streamed agent model call: how much it thought before answering (numbers only).

    Observation for a future "thought too long without answering" cut-off; nothing reads it.
    ``first_answer_ms`` is None when the call produced no text, refusal or tool arguments.
    """
    from services.usage.llm_usage_service import elapsed_ms

    log_with_context(
        logger,
        20,  # INFO
        "LLM call thinking before answer",
        model=model,
        key_slot=key_slot,
        outcome=outcome,
        thinking_chars_before_answer=thinking_chars,
        first_answer_ms=first_answer_ms,
        duration_ms=duration_ms if duration_ms is not None else (elapsed_ms(started) if started is not None else None),
        reasoning_tokens=reasoning_tokens,
        output_tokens=output_tokens,
    )


_failover_classes: dict[Any, type] = {}


def build_failover_chat_model(
    session_id: str | None,
    *,
    on_served: Callable[[str], None] | None = None,
    on_served_provider: Callable[[ModelProvider], None] | None = None,
    usage_attribution: LLMUsageAttribution | None = None,
) -> Any:
    """A ``FailoverChatModel`` that is also an instance of the SDK's ``Model``."""
    base = _base_model_class()
    cls = _failover_classes.get(base)
    if cls is None:
        cls = type("FailoverChatModel", (FailoverChatModel, base), {})
        _failover_classes[base] = cls
    return cls(
        session_id,
        on_served=on_served,
        on_served_provider=on_served_provider,
        usage_attribution=usage_attribution,
    )


def reset_sdk_model_cache() -> None:
    """Clear cached SDK models and clients; intended for tests."""
    _models.clear()
    llm_providers.reset_provider_client_cache()
    llm_failover.reset_failover_state()
