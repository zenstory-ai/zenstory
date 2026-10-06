"""Per-model-call usage metering for openai-agents runs.

``RunHooks.on_llm_end`` fires once after every model call of a run with that
call's own usage, including calls in runs that later raise or are cancelled
after the call finished. One ledger row per call keeps each call's own
timestamp, so peak/off-peak pricing is exact per call.
"""

from __future__ import annotations

import types
from typing import Any

from agent.tools.mcp_tools import ToolContext
from models.llm_usage import LLM_USAGE_SOURCE_AGENT
from services.usage.llm_usage_service import LLMUsageAttribution, record_llm_usage_async
from utils.request_context import get_agent_run_id


class UsageMeteringMixin:
    """``on_llm_end`` that writes one ``llm_usage_event`` row per model call."""

    def __init__(self, attribution: LLMUsageAttribution, model: str) -> None:
        self._attribution = attribution
        self._model = model

    @property
    def attribution(self) -> LLMUsageAttribution:
        return self._attribution

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
        del context, agent
        await record_llm_usage_async(
            self._attribution,
            model=self._model,
            usage=getattr(response, "usage", None),
        )


# Keyed by the SDK's RunHooks class so a stubbed ``agents`` module in tests
# never leaks a subclass of the stub into runs that use the real SDK.
_hook_classes: dict[Any, type] = {}


def _hooks_class() -> type:
    from agents import RunHooks

    cls = _hook_classes.get(RunHooks)
    if cls is None:
        cls = types.new_class("UsageMeteringRunHooks", (UsageMeteringMixin, RunHooks))
        _hook_classes[RunHooks] = cls
    return cls


def build_usage_metering_hooks(model: str) -> Any:
    """Bind the current request's user/project/run id (read before the SDK task starts)."""
    attribution = LLMUsageAttribution(
        user_id=ToolContext.get_user_id(),
        source=LLM_USAGE_SOURCE_AGENT,
        project_id=ToolContext.get_project_id(),
        correlation_id=get_agent_run_id(),
    )
    return _hooks_class()(attribution, model)
