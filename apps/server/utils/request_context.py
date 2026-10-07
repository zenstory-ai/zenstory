"""Request-scoped context for structured logging.

This module provides a lightweight request context powered by ``contextvars``.
It enables correlating logs across:
- one HTTP request (request_id)
- one user action across multiple requests (trace_id)
- one agent streaming run (agent_run_id)

The context is injected automatically by ``log_with_context``.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_trace_id: ContextVar[str | None] = ContextVar("trace_id", default=None)
_agent_run_id: ContextVar[str | None] = ContextVar("agent_run_id", default=None)


def bind_request_context(
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
    agent_run_id: str | None = None,
) -> dict[str, Token[Any]]:
    """Bind request-scoped values into the current context.

    Returns a dict of tokens which can be passed to ``reset_request_context``.
    """
    tokens: dict[str, Token[Any]] = {}

    if request_id is not None:
        tokens["request_id"] = _request_id.set(request_id)

    if trace_id is not None:
        tokens["trace_id"] = _trace_id.set(trace_id)

    if agent_run_id is not None:
        tokens["agent_run_id"] = _agent_run_id.set(agent_run_id)

    return tokens


def reset_request_context(tokens: dict[str, Token[Any]]) -> None:
    """Reset previously bound context values."""
    # NOTE:
    # ContextVar Tokens must be reset in the *same* Context they were created in.
    # In practice this can be violated in a few scenarios in async servers:
    # - streaming responses (SSE/async generators) where `aclose()` may happen in a
    #   different task/context than where the token was created
    # - background tasks/task-groups that copy contexts
    #
    # When that happens `ContextVar.reset(token)` raises:
    #   ValueError: Token was created in a different Context
    #
    # We treat this as a best-effort cleanup operation for logging context:
    # if the token can't be reset, we clear the value in the *current* context
    # to avoid leaking request identifiers into unrelated logs.
    if token := tokens.get("agent_run_id"):
        try:
            _agent_run_id.reset(token)
        except ValueError:
            _agent_run_id.set(None)
    if token := tokens.get("trace_id"):
        try:
            _trace_id.reset(token)
        except ValueError:
            _trace_id.set(None)
    if token := tokens.get("request_id"):
        try:
            _request_id.reset(token)
        except ValueError:
            _request_id.set(None)


async def reset_request_context_async(tokens: dict[str, Token[Any]]) -> None:
    """Async wrapper so Starlette BackgroundTasks runs reset in-event-loop.

    Starlette runs sync background tasks in a worker thread; ContextVar tokens
    must be reset in the same context they were created in, so we expose an
    async wrapper for safe use in response.background tasks.
    """
    reset_request_context(tokens)


def get_agent_run_id() -> str | None:
    """Return the agent run id bound to the current context, if any."""
    return _agent_run_id.get()


def get_log_context() -> dict[str, str]:
    """Return the current request context as JSON-serializable fields."""
    ctx: dict[str, str] = {}

    request_id = _request_id.get()
    if request_id:
        ctx["request_id"] = request_id

    trace_id = _trace_id.get()
    if trace_id:
        ctx["trace_id"] = trace_id

    agent_run_id = _agent_run_id.get()
    if agent_run_id:
        ctx["agent_run_id"] = agent_run_id

    return ctx
