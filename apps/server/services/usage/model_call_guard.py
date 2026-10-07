"""Model transport guard shared by SDK, router and sync/async LLM clients."""

import asyncio
from typing import Any

from services.usage.cost_budget import reserve_model_call, settle_model_call


class _BudgetedAsyncStream:
    def __init__(self, stream: Any, reservation: Any):
        self._stream = stream
        self._reservation = reservation

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def __aiter__(self):
        async def chunks():
            async for chunk in self._stream:
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    await asyncio.to_thread(settle_model_call, self._reservation, usage)
                yield chunk

        return chunks()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        await self.close()

    async def close(self):
        await self._stream.close()


class _BudgetedSyncStream:
    def __init__(self, stream: Any, reservation: Any):
        self._stream = stream
        self._reservation = reservation

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def __iter__(self):
        for chunk in self._stream:
            if getattr(chunk, "usage", None) is not None:
                settle_model_call(self._reservation, chunk.usage)
            yield chunk

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        self._stream.close()


def install_async_cost_guard(client: Any) -> Any:
    completions = client.chat.completions
    if getattr(completions, "_zenstory_cost_guard", False):
        return client
    original = completions.create
    original_with_options = getattr(client, "with_options", None)

    async def create(*args, **kwargs):
        from agent.core.deepseek_client import sanitize_chat_messages

        if "messages" in kwargs:
            kwargs["messages"] = sanitize_chat_messages(kwargs["messages"])
        # Reservation is committed before touching the provider. Cancellation of
        # the DB worker may leave a reservation, which is safe (never free spend).
        reservation = await asyncio.to_thread(reserve_model_call, kwargs)
        # to_thread does not propagate ContextVar changes back to the async task.
        from services.usage.cost_budget import bind_call_start

        bind_call_start(reservation)
        transport = original
        if reservation is not None and callable(original_with_options):
            # Automatic transport retries can incur unknown duplicate billing.
            transport = original_with_options(max_retries=0).chat.completions.create
        response = await transport(*args, **kwargs)
        if kwargs.get("stream"):
            return _BudgetedAsyncStream(response, reservation)
        await asyncio.to_thread(settle_model_call, reservation, getattr(response, "usage", None))
        return response

    completions.create = create
    completions._zenstory_cost_guard = True
    if callable(original_with_options):
        # Agents SDK may clone the client to apply its retry policy. Keep the
        # guard on those clones; our internal no-retry transport stays unwrapped.
        def with_options(*args, **kwargs):
            return install_async_cost_guard(original_with_options(*args, **kwargs))

        client.with_options = with_options
    return client


def install_sync_cost_guard(client: Any) -> Any:
    completions = client.chat.completions
    if getattr(completions, "_zenstory_cost_guard", False):
        return client
    original = completions.create
    original_with_options = getattr(client, "with_options", None)

    def create(*args, **kwargs):
        reservation = reserve_model_call(kwargs)
        transport = (
            original
            if reservation is None or not callable(original_with_options)
            else original_with_options(max_retries=0).chat.completions.create
        )
        response = transport(*args, **kwargs)
        if kwargs.get("stream"):
            return _BudgetedSyncStream(response, reservation)
        settle_model_call(reservation, getattr(response, "usage", None))
        return response

    completions.create = create
    completions._zenstory_cost_guard = True
    if callable(original_with_options):

        def with_options(*args, **kwargs):
            return install_sync_cost_guard(original_with_options(*args, **kwargs))

        client.with_options = with_options
    return client
