"""Per-model-call usage metering for openai-agents runs.

``RunHooks.on_llm_end`` fires once after every model call of a run with that
call's own usage, including calls in runs that later raise or are cancelled
after the call finished. One ledger row per call keeps each call's own
timestamp, so peak/off-peak pricing is exact per call.

The agent run hooks also tell the pending-empty-file guard, before the SDK executes
this call's tools, that the latest pending file's body was fully written in this
reply (see ``PendingBodyStreamedMixin``).
"""

from __future__ import annotations

import types
from typing import Any

from agent.core.stream_processor import StreamProcessor
from agent.tools.mcp_tools import ToolContext
from models.llm_usage import LLM_USAGE_SOURCE_AGENT
from services.usage.llm_usage_service import LLMUsageAttribution, schedule_llm_usage_record
from utils.logger import get_logger
from utils.request_context import get_agent_run_id

logger = get_logger(__name__)


class UsageMeteringMixin:
    """``on_llm_end`` that writes one ``llm_usage_event`` row per model call."""

    def __init__(self, attribution: LLMUsageAttribution, model: str) -> None:
        self._attribution = attribution
        self._model = model

    @property
    def attribution(self) -> LLMUsageAttribution:
        return self._attribution

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
        # The SDK awaits this hook before its next model call or tool step, so
        # the write is scheduled in the background rather than awaited.
        del context, agent
        schedule_llm_usage_record(
            self._attribution,
            model=self._model,
            usage=getattr(response, "usage", None),
        )


def _response_message_text(response: Any) -> str:
    """拼出本次模型调用回复正文（message 的 output_text），不含 reasoning。"""
    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        if getattr(item, "type", None) != "message":
            continue
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", None) == "output_text":
                parts.append(getattr(part, "text", "") or "")
    return "".join(parts)


def _reply_has_closed_file_block(text: str) -> bool:
    """本条回复里是否有一个闭合且非空的 <file>…</file> 正文块。

    用一个全新的 StreamProcessor 把整段回复一次性 process_content，判定口径与
    StreamAdapter 捕获正文一致（代码块 / 行内代码里的 <file> 是字面量）。只认
    真实的 </file> 收尾：绝不调用 finalize_on_stream_end，它会把未闭合的块自动
    补全，误判为已写完。
    """
    if not text:
        return False
    processor = StreamProcessor()
    processor.start_file_write("on-llm-end-probe")
    result = processor.process_content(text)
    return (
        result.file_complete
        and not result.auto_completed
        and bool(result.final_content.strip())
    )


class PendingBodyStreamedMixin:
    """``on_llm_end`` 把"待写文件的正文已在本条回复里写完"同步告诉空文件守卫。

    SDK 先 await on_llm_end，再执行本回复里的工具调用；而 StreamAdapter 处理
    </file> 并落库、清除守卫条目是在消费侧滞后发生的。同一条回复里
    "<file>上一集</file> + create_file(下一集)" 时，create_file 若只等 adapter，
    就会被上一集尚未落库的条目误拒（"创建文件失败"，模型随后把已保存的正文原样
    重写进对话）。这里在工具执行前标记，守卫即可放行；本条回复里没有闭合
    <file> 块的真实违规（例如连发两个 create_file）照旧被拒。
    """

    async def on_llm_end(self, context: Any, agent: Any, response: Any) -> None:
        try:
            # 计费优先：计量放在 MRO 后面的 mixin 里，无论下面的检查结果如何都先执行
            await super().on_llm_end(context, agent, response)  # type: ignore[misc]
        finally:
            # on_llm_end 抛出的异常会让整个 SDK run 失败；检查只是放行优化，失败只记日志
            try:
                if ToolContext.has_pending_empty_file() and _reply_has_closed_file_block(
                    _response_message_text(response)
                ):
                    ToolContext.mark_latest_pending_body_streamed()
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"Pending file body check after model call failed: {exc}")


# Keyed by the SDK's RunHooks class so a stubbed ``agents`` module in tests
# never leaks a subclass of the stub into runs that use the real SDK.
_hook_classes: dict[Any, type] = {}


def _hooks_class() -> type:
    from agents import RunHooks

    cls = _hook_classes.get(RunHooks)
    if cls is None:
        # 检查 mixin 在前：它先 super() 到计量，再做检查
        cls = types.new_class(
            "AgentRunHooks", (PendingBodyStreamedMixin, UsageMeteringMixin, RunHooks)
        )
        _hook_classes[RunHooks] = cls
    return cls


def build_agent_run_hooks(model: str) -> Any:
    """Bind the current request's user/project/run id (read before the SDK task starts)."""
    attribution = LLMUsageAttribution(
        user_id=ToolContext.get_user_id(),
        source=LLM_USAGE_SOURCE_AGENT,
        project_id=ToolContext.get_project_id(),
        correlation_id=get_agent_run_id(),
    )
    return _hooks_class()(attribution, model)
