"""In-band progress channel for tool calls that want to stream live sub-events.

The openai-agents SDK owns the model+tool loop: while a tool's ``on_invoke_tool``
callback runs, the consumer of ``Runner.run_streamed().stream_events()`` is parked
awaiting the next SDK event, so a long-running tool
cannot surface intermediate progress through the normal SDK event flow.

This module provides a tiny context-local emitter the runner installs before
starting the SDK run. A tool executing inside the SDK background task can then
call :func:`emit_progress` to hand a pre-built workflow/SSE event back to the
runner, which interleaves it into the outbound stream.

Design notes:
- The channel holds a *callable* (not a queue) so the runner decides how to
  enqueue (it tags the event for its own merge loop). This keeps the channel
  generic and free of any runner/SDK imports.
- ``contextvars`` propagate into the SDK background task because
  ``asyncio.create_task`` (used by ``Runner.run_streamed``) copies the current
  context at creation time. The runner installs the emitter *before* starting
  the run, so the tool's context copy sees it.
- :func:`emit_progress` never raises and is a no-op when no channel is active
  (e.g. tools invoked directly from unit tests), so callers can emit freely
  without guarding.
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from typing import Any

# A context-local "emit this event" callback installed by the runner.
_progress_emitter: contextvars.ContextVar[Callable[[Any], None] | None] = (
    contextvars.ContextVar("agent_progress_emitter", default=None)
)


def set_progress_emitter(emitter: Callable[[Any], None]) -> contextvars.Token:
    """Install the progress emitter for the current context. Returns a reset token."""
    return _progress_emitter.set(emitter)


def reset_progress_emitter(token: contextvars.Token) -> None:
    """Restore the previous emitter using the token from :func:`set_progress_emitter`."""
    _progress_emitter.reset(token)


def emit_progress(event: Any) -> bool:
    """Hand a pre-built event to the active progress channel, if any.

    Safe to call from anywhere: returns ``False`` (no-op) when no channel is
    installed, and swallows any delivery error so progress emission can never
    break the tool it is reporting on.
    """
    emitter = _progress_emitter.get()
    if emitter is None:
        return False
    try:
        emitter(event)
        return True
    except Exception:
        return False
