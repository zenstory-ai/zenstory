"""Model transport guard shared by SDK, router and sync/async LLM clients."""

import asyncio
from typing import Any

from services.usage.cost_budget import (
    reserve_model_call,
    settle_cancelled_stream_call,
    settle_model_call,
)
from utils.sanitize import (
    sanitize_chat_messages,
    sanitize_responses_input,
    sanitize_responses_text,
)

_RESPONSES_TERMINAL_USAGE_EVENTS = frozenset({
    "response.completed",
    "response.incomplete",
    "response.failed",
})
_RESPONSES_OUTPUT_DELTA_EVENTS = frozenset({
    "response.output_text.delta",
    "response.reasoning_text.delta",
    "response.reasoning_summary_text.delta",
    "response.refusal.delta",
    "response.function_call_arguments.delta",
})
# Strong references to background cancelled-stream settlement tasks so GC never
# drops them before commit; awaited on graceful shutdown via drain_pending_cost_settlements.
_pending_cancelled_settlements: set[asyncio.Task[Any]] = set()


async def drain_pending_cost_settlements() -> None:
    """Wait for any in-flight cancelled-stream cost settlement tasks to finish."""
    tasks = [task for task in _pending_cancelled_settlements if not task.done()]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _utf8_bytes(value: Any) -> int:
    return len(value.encode("utf-8")) if isinstance(value, str) and value else 0


def _extract_chunk_output_bytes(chunk: Any) -> int:
    """Count UTF-8 bytes of generated output in a Responses or Chat Completions stream chunk."""
    chunk_type = getattr(chunk, "type", None)
    if chunk_type in _RESPONSES_OUTPUT_DELTA_EVENTS:
        return _utf8_bytes(getattr(chunk, "delta", None))
    choices = getattr(chunk, "choices", None)
    if not isinstance(choices, list) or not choices:
        return 0
    total = 0
    for choice in choices:
        delta = choice.get("delta") if isinstance(choice, dict) else getattr(choice, "delta", None)
        if delta is None:
            continue
        if isinstance(delta, dict):
            total += _utf8_bytes(delta.get("content"))
            total += _utf8_bytes(delta.get("reasoning_content"))
            total += _utf8_bytes(delta.get("reasoning"))
            total += _utf8_bytes(delta.get("refusal"))
            tool_calls = delta.get("tool_calls")
        else:
            total += _utf8_bytes(getattr(delta, "content", None))
            total += _utf8_bytes(getattr(delta, "reasoning_content", None))
            total += _utf8_bytes(getattr(delta, "reasoning", None))
            total += _utf8_bytes(getattr(delta, "refusal", None))
            tool_calls = getattr(delta, "tool_calls", None)
        if isinstance(tool_calls, list):
            for tc in tool_calls:
                fn = tc.get("function") if isinstance(tc, dict) else getattr(tc, "function", None)
                if isinstance(fn, dict):
                    total += _utf8_bytes(fn.get("name"))
                    total += _utf8_bytes(fn.get("arguments"))
                elif fn is not None:
                    total += _utf8_bytes(getattr(fn, "name", None))
                    total += _utf8_bytes(getattr(fn, "arguments", None))
    return total


def _has_complete_usage_fields(usage: Any) -> bool:
    from services.usage.llm_usage_service import _int_field

    prompt = _int_field(usage, "prompt_tokens")
    if prompt is None:
        prompt = _int_field(usage, "input_tokens")
    output = _int_field(usage, "completion_tokens")
    if output is None:
        output = _int_field(usage, "output_tokens")
    return prompt is not None and output is not None


def _extract_chunk_usage(chunk: Any) -> Any:
    """Extract usage from Chat Completions chunks or Responses terminal events."""
    usage = getattr(chunk, "usage", None)
    if usage is not None:
        return usage
    chunk_type = getattr(chunk, "type", None)
    if chunk_type is not None and chunk_type not in _RESPONSES_TERMINAL_USAGE_EVENTS:
        return None
    response = getattr(chunk, "response", None)
    if response is not None:
        return getattr(response, "usage", None)
    return None


def _is_stream_requested(kwargs: dict[str, Any]) -> bool:
    return kwargs.get("stream") is True


def _sanitize_responses_kwargs(kwargs: dict[str, Any]) -> None:
    if isinstance(kwargs.get("instructions"), str):
        kwargs["instructions"] = sanitize_responses_text(kwargs["instructions"])
    if "input" in kwargs:
        kwargs["input"] = sanitize_responses_input(kwargs["input"])


class _BudgetedAsyncStream:
    def __init__(self, stream: Any, reservation: Any):
        self._stream = stream
        self._reservation = reservation
        self._streamed_output_bytes = 0
        self._usage_settled = False
        self._cancelled_settlement_task: asyncio.Task[Any] | None = None

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def _schedule_cancelled_settlement(self) -> asyncio.Task[Any] | None:
        """Schedule conservative settlement once when a stream closes without terminal usage.

        Non-awaiting so it is safe to call inside async generator ``finally`` blocks
        (including ``GeneratorExit`` and ``CancelledError`` paths).
        """
        if self._reservation is None or self._usage_settled:
            return None
        if self._cancelled_settlement_task is not None:
            return self._cancelled_settlement_task
        output_bytes = self._streamed_output_bytes
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            settle_cancelled_stream_call(self._reservation, streamed_output_bytes=output_bytes)
            self._usage_settled = True
            return None
        task = loop.create_task(
            asyncio.to_thread(
                settle_cancelled_stream_call,
                self._reservation,
                streamed_output_bytes=output_bytes,
            )
        )
        self._cancelled_settlement_task = task
        _pending_cancelled_settlements.add(task)
        task.add_done_callback(_pending_cancelled_settlements.discard)
        return task

    def __aiter__(self):
        async def chunks():
            try:
                async for chunk in self._stream:
                    self._streamed_output_bytes += _extract_chunk_output_bytes(chunk)
                    usage = _extract_chunk_usage(chunk)
                    if usage is not None:
                        await asyncio.to_thread(settle_model_call, self._reservation, usage)
                        if _has_complete_usage_fields(usage):
                            self._usage_settled = True
                    yield chunk
            finally:
                self._schedule_cancelled_settlement()

        return chunks()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.close()

    async def close(self):
        try:
            close_fn = getattr(self._stream, "close", None) or getattr(self._stream, "aclose", None)
            if callable(close_fn):
                await close_fn()
        finally:
            task = self._schedule_cancelled_settlement()
            if task is not None and not task.done():
                await asyncio.shield(task)


class _BudgetedAsyncAPIResponse:
    """Wraps ``client.responses.with_streaming_response.create()`` API response."""

    def __init__(self, api_response: Any, reservation: Any, *, stream: bool):
        self._api_response = api_response
        self._reservation = reservation
        self._stream = stream

    def __getattr__(self, name):
        return getattr(self._api_response, name)

    async def parse(self, *args, **kwargs):
        parsed = await self._api_response.parse(*args, **kwargs)
        if self._stream or hasattr(parsed, "__aiter__"):
            return _BudgetedAsyncStream(parsed, self._reservation)
        await asyncio.to_thread(settle_model_call, self._reservation, getattr(parsed, "usage", None))
        return parsed


class _BudgetedAsyncStreamingResponseCM:
    """Async context manager that enforces cost reservation before entering transport."""

    def __init__(
        self,
        *,
        original_wsr_create: Any,
        original_with_options: Any,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ):
        self._original_wsr_create = original_wsr_create
        self._original_with_options = original_with_options
        self._args = args
        self._kwargs = dict(kwargs)
        self._cm: Any = None

    async def __aenter__(self):
        _sanitize_responses_kwargs(self._kwargs)
        reservation = await asyncio.to_thread(reserve_model_call, self._kwargs)
        from services.usage.cost_budget import bind_call_start, notify_model_transport_start

        bind_call_start(reservation)
        transport_create = self._original_wsr_create
        if reservation is not None and callable(self._original_with_options):
            transport_create = self._original_with_options(
                max_retries=0
            ).responses.with_streaming_response.create
        notify_model_transport_start()
        self._cm = transport_create(*self._args, **self._kwargs)
        api_response = await self._cm.__aenter__()
        return _BudgetedAsyncAPIResponse(
            api_response,
            reservation,
            stream=_is_stream_requested(self._kwargs),
        )

    async def __aexit__(self, exc_type, exc, tb):
        if self._cm is not None:
            return await self._cm.__aexit__(exc_type, exc, tb)
        return False


class _BudgetedSyncStream:
    def __init__(self, stream: Any, reservation: Any):
        self._stream = stream
        self._reservation = reservation
        self._streamed_output_bytes = 0
        self._usage_settled = False
        self._cancelled_settled = False

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def _settle_if_unsettled(self) -> None:
        if self._reservation is None or self._usage_settled or self._cancelled_settled:
            return
        self._cancelled_settled = True
        settle_cancelled_stream_call(
            self._reservation,
            streamed_output_bytes=self._streamed_output_bytes,
        )

    def __iter__(self):
        try:
            for chunk in self._stream:
                self._streamed_output_bytes += _extract_chunk_output_bytes(chunk)
                usage = _extract_chunk_usage(chunk)
                if usage is not None:
                    settle_model_call(self._reservation, usage)
                    if _has_complete_usage_fields(usage):
                        self._usage_settled = True
                yield chunk
        finally:
            self._settle_if_unsettled()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        try:
            close_fn = getattr(self._stream, "close", None)
            if callable(close_fn):
                close_fn()
        finally:
            self._settle_if_unsettled()


def _install_async_responses_guard(client: Any, original_with_options: Any) -> None:
    responses = getattr(client, "responses", None)
    if responses is None or getattr(responses, "_zenstory_cost_guard", False):
        return
    original_create = getattr(responses, "create", None)
    if callable(original_create):
        async def responses_create(*args, **kwargs):
            _sanitize_responses_kwargs(kwargs)
            reservation = await asyncio.to_thread(reserve_model_call, kwargs)
            from services.usage.cost_budget import bind_call_start, notify_model_transport_start

            bind_call_start(reservation)
            transport = original_create
            if reservation is not None and callable(original_with_options):
                transport = original_with_options(max_retries=0).responses.create
            notify_model_transport_start()
            response = await transport(*args, **kwargs)
            if _is_stream_requested(kwargs):
                return _BudgetedAsyncStream(response, reservation)
            await asyncio.to_thread(settle_model_call, reservation, getattr(response, "usage", None))
            return response

        responses.create = responses_create

    wsr = getattr(responses, "with_streaming_response", None)
    original_wsr_create = getattr(wsr, "create", None)
    if wsr is not None and callable(original_wsr_create):
        def wsr_create(*args, **kwargs):
            return _BudgetedAsyncStreamingResponseCM(
                original_wsr_create=original_wsr_create,
                original_with_options=original_with_options,
                args=args,
                kwargs=kwargs,
            )

        wsr.create = wsr_create

    responses._zenstory_cost_guard = True


def _install_sync_responses_guard(client: Any, original_with_options: Any) -> None:
    responses = getattr(client, "responses", None)
    if responses is None or getattr(responses, "_zenstory_cost_guard", False):
        return
    original_create = getattr(responses, "create", None)
    if callable(original_create):
        def responses_create(*args, **kwargs):
            _sanitize_responses_kwargs(kwargs)
            reservation = reserve_model_call(kwargs)
            transport = (
                original_create
                if reservation is None or not callable(original_with_options)
                else original_with_options(max_retries=0).responses.create
            )
            response = transport(*args, **kwargs)
            if _is_stream_requested(kwargs):
                return _BudgetedSyncStream(response, reservation)
            settle_model_call(reservation, getattr(response, "usage", None))
            return response

        responses.create = responses_create
    responses._zenstory_cost_guard = True


def install_async_cost_guard(client: Any) -> Any:
    completions = client.chat.completions
    already_guarded = getattr(completions, "_zenstory_cost_guard", False)
    original_with_options = getattr(client, "_zenstory_original_with_options", None) or getattr(
        client, "with_options", None
    )
    if not already_guarded:
        original = completions.create

        async def create(*args, **kwargs):
            if "messages" in kwargs:
                kwargs["messages"] = sanitize_chat_messages(kwargs["messages"])
            # Reservation is committed before touching the provider. Cancellation of
            # the DB worker may leave a reservation, which is safe (never free spend).
            reservation = await asyncio.to_thread(reserve_model_call, kwargs)
            # to_thread does not propagate ContextVar changes back to the async task.
            from services.usage.cost_budget import bind_call_start, notify_model_transport_start

            bind_call_start(reservation)
            transport = original
            if reservation is not None and callable(original_with_options):
                # Automatic transport retries can incur unknown duplicate billing.
                transport = original_with_options(max_retries=0).chat.completions.create
            notify_model_transport_start()
            response = await transport(*args, **kwargs)
            if _is_stream_requested(kwargs):
                return _BudgetedAsyncStream(response, reservation)
            await asyncio.to_thread(settle_model_call, reservation, getattr(response, "usage", None))
            return response

        completions.create = create
        completions._zenstory_cost_guard = True
        if callable(original_with_options):
            # Agents SDK may clone the client to apply its retry policy. Keep the
            # guard on those clones; our internal no-retry transport stays unwrapped.
            client._zenstory_original_with_options = original_with_options

            def with_options(*args, **kwargs):
                return install_async_cost_guard(original_with_options(*args, **kwargs))

            client.with_options = with_options

    _install_async_responses_guard(client, original_with_options)
    return client


def install_sync_cost_guard(client: Any) -> Any:
    completions = client.chat.completions
    already_guarded = getattr(completions, "_zenstory_cost_guard", False)
    original_with_options = getattr(client, "_zenstory_original_with_options", None) or getattr(
        client, "with_options", None
    )
    if not already_guarded:
        original = completions.create

        def create(*args, **kwargs):
            reservation = reserve_model_call(kwargs)
            transport = (
                original
                if reservation is None or not callable(original_with_options)
                else original_with_options(max_retries=0).chat.completions.create
            )
            response = transport(*args, **kwargs)
            if _is_stream_requested(kwargs):
                return _BudgetedSyncStream(response, reservation)
            settle_model_call(reservation, getattr(response, "usage", None))
            return response

        completions.create = create
        completions._zenstory_cost_guard = True
        if callable(original_with_options):
            client._zenstory_original_with_options = original_with_options

            def with_options(*args, **kwargs):
                return install_sync_cost_guard(original_with_options(*args, **kwargs))

            client.with_options = with_options

    _install_sync_responses_guard(client, original_with_options)
    return client
