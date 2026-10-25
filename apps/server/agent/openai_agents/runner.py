"""Streaming runner that adapts openai-agents-python to ZenStory workflow events."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

from agent.core.message_manager import AGENT_DIRECTIVE_HEADER
from agent.core.output_truncation import (
    OUTPUT_TRUNCATED_HISTORY,
    trailing_output_truncations,
)
from agent.core.progress_channel import reset_progress_emitter, set_progress_emitter
from agent.core.run_meter import AgentRunMeter, TimingOutcome
from agent.core.stream_errors import (
    MODEL_CALL_LIMIT_ERROR_TYPE,
    classify_stream_exception,
    log_stream_exception,
    model_call_limit_error,
    output_truncated_error,
)
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.state import WritingState
from agent.openai_agents.events import (
    build_handoff_packet,
    extract_artifact_refs,
    mcp_text_result,
    merge_unique_strings,
    normalize_str_list,
    parse_json_object,
    tool_error_text,
)
from agent.openai_agents.model import build_failover_chat_model
from agent.openai_agents.repeat_read_guard import (
    NO_PROGRESS_STOP_REASON,
    NO_PROGRESS_STOP_TOTAL,
    RepeatReadGuard,
)
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker, _parse_error_payload
from agent.openai_agents.tools_adapter import (
    READ_ONLY_REASON_CLARIFY_FIRST,
    READ_ONLY_REASON_USER_REQUEST,
    build_agent_function_tools,
    tool_availability_note,
)
from config import agent_runtime
from config.agent_runtime import (
    AGENT_COLLABORATION_MAX_ITERATIONS,
    AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS,
    AGENT_TOOL_CALL_MAX_ITERATIONS,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

MessageList = list[dict[str, Any]]

# 控制流工具：调用成功即表示「本次 SDK run 到此为止」——
# handoff_to_agent 把控制权交给下一个 agent，request_clarification 把控制权交回用户。
# 工具名与 agent/tools/registry.py 的注册名一致（tools_adapter 原样透传给 SDK）。
CONTROL_FLOW_TOOL_NAMES: tuple[str, ...] = ("handoff_to_agent", "request_clarification")

# 控制流工具「调用成功」的判定：只有返回这些 status 才算真的要停下。
# 参数非法时（invalid target_agent / self handoff / question is required）工具返回 error，
# 此时必须让模型看到错误并在同一个 run 内自行纠正，绝不能把 run 截断掉。
_CONTROL_FLOW_STOP_STATUS: dict[str, str] = {
    "handoff_to_agent": "handoff",
    "request_clarification": "clarification_needed",
}


def _control_flow_status_of(tool_name: str, output_text: str) -> bool:
    """判断某次工具输出是否是「生效的控制流结果」。"""
    expected = _CONTROL_FLOW_STOP_STATUS.get(tool_name)
    if expected is None:
        return False
    try:
        payload = json.loads(output_text) if output_text else {}
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == expected


# 工具失败熔断后 MESSAGE_END 的 stop_reason（随 assistant 消息元数据落库）。
TOOL_FAILURE_STOP_REASON = "tool_failure_circuit_open"
# 请求级模型调用预算用尽、截停 SDK run 后 MESSAGE_END 的 stop_reason；预算快用完时
# 由收尾提醒软着陆（模型写了阶段总结后结束）也用它，随 assistant 消息落库。
MODEL_CALL_BUDGET_STOP_REASON = "model_call_budget_exhausted"
# 单个 agent run 的工具调用轮数（SDK max_turns）用尽，或已进入收尾提醒后结束时的
# stop_reason。随 assistant 消息落库；router 据此把下一条「继续」直接交给上一个 agent。
MAX_TURNS_STOP_REASON = "max_turns_exceeded"
# 接近请求墙钟时限、按提醒收尾（与 service 在时限取消时落库的 stop_reason 相同，路由按「没做完」续做）。
RUN_DEADLINE_STOP_REASON = "run_deadline_exceeded"
# 重复读取守卫判定无进展、结束本轮时的 stop_reason（与 ERROR 事件 data.reason 相同）。
NO_PROGRESS_STOP = NO_PROGRESS_STOP_REASON
# 最后一次模型响应只有思考、没有正文和工具调用就撞上输出上限（max_tokens）时
# MESSAGE_END 的 stop_reason。
OUTPUT_TRUNCATED_STOP_REASON = "output_truncated"
# 最后一次模型响应的 output_tokens 达到 max_tokens 的这个比例即视为被输出上限截断。
# openai-agents 0.17.x 的 Chat Completions 流式 handler 不处理 finish_reason
# （ResponseCompletedEvent 的 status 恒为 completed），只能看 usage 判断。
OUTPUT_CAP_RATIO = 0.98

# 距离上限还剩多少次模型调用时开始收尾：在这几次调用的输入末尾追加提醒，
# 要求模型停止调用工具、写阶段总结（已完成 / 剩余 / 用到的文件 id）。上限取
# 本次 run 的 max_turns 与请求级模型调用预算（AgentRunMeter）中先到的那个。
SOFT_LANDING_TURNS = 3
# 收尾提醒由哪个上限触发（_ModelCallCountingFilter.landing_limit）。
LANDING_LIMIT_MAX_TURNS = "max_turns"
LANDING_LIMIT_MODEL_CALL_BUDGET = "model_call_budget"
LANDING_LIMIT_WALL_CLOCK = "wall_clock"
SOFT_LANDING_PROMPT = (
    "[系统提醒] 本轮工具调用即将达到上限（本次之后最多还能调用模型 {remaining} 次）。"
    "请立即停止调用任何工具（包括 query_files / parallel_execute）。"
    "如果刚用 create_file 建了文件但还没写 <file> 正文，先在本次回复里写完该文件的"
    " <file> 正文，再做总结。"
    "然后直接用文字给出阶段总结：\n"
    "1. 已完成的工作；\n"
    "2. 尚未完成、需要下一轮继续的工作；\n"
    "3. 本轮用到或改动过的文件（标题 + id）。\n"
    "用户回复「继续」后会从这里接着完成剩余部分。"
)
TIME_LANDING_PROMPT = (
    "[系统提醒] 这次请求的时间快用完了。不要再开始新的文件或大段改写，也不要再读文件："
    "正在写的 <file> 正文先写完并用 </file> 结束；刚用 create_file 建了文件还没写正文的，"
    "在本次回复里写完它的 <file> 正文。然后直接用文字给作者写阶段总结：\n"
    "1. 已完成的工作；\n"
    "2. 尚未完成、需要下一轮继续的工作。\n"
    "用户回复「继续」后会从这里接着完成剩余部分。"
)


def _stop_run_on_control_flow_tool(
    _ctx: Any,
    tool_results: list[Any],
    *,
    failure_breaker: ToolFailureBreaker | None = None,
    run_meter: AgentRunMeter | None = None,
    read_guard: RepeatReadGuard | None = None,
) -> Any:
    """SDK 的 tool_use_behavior 回调：控制流工具生效后立即结束当前 run。

    为什么不能只靠 result.cancel(mode="after_turn")：cancel 是异步落地的——
    SDK run-loop 把 tool_output 放进自己的 _event_queue，经 _pump_sdk_events 转发到
    run_queue，本模块的消费循环才读到并 cancel；等标志位落地时 run-loop 早已发起了
    下一次模型调用，那一轮的正文会被推给前端、工具（create_file/edit_file/delete_file）
    会被真实执行——「工作流已暂停」却仍在改用户的文件。
    这里改用 SDK 在 turn 内同步检查的 tool_use_behavior（turn_resolution.py
    check_for_final_output_from_tools），控制流工具一返回就把本轮当作 final output，
    run-loop 不会再发起下一次模型调用。

    用可调用对象而非 StopAtTools(dict) 的原因：StopAtTools 只看工具名，
    控制流工具报错（例如 target_agent 非法）时也会把 run 掐断，模型将失去在同一个
    run 内纠错的机会；这里按返回 status 精确判定。

    工具失败熔断（failure_breaker）走同一个出口：熔断器在工具回调里同步记账，
    本轮工具一跑完这里就能看到，run-loop 不会再为「原样重试」发起下一次模型调用。
    重复读取守卫（read_guard）判定无进展时同样走这个出口。
    """
    from agents.agent import ToolsToFinalOutputResult

    if failure_breaker is not None and failure_breaker.trip is not None:
        return ToolsToFinalOutputResult(
            is_final_output=True,
            final_output=failure_breaker.trip.user_message(),
        )
    if read_guard is not None and read_guard.trip is not None:
        return ToolsToFinalOutputResult(
            is_final_output=True,
            final_output=read_guard.trip.user_message(),
        )

    for tool_result in tool_results or []:
        tool_name = str(getattr(getattr(tool_result, "tool", None), "name", "") or "")
        if tool_name not in CONTROL_FLOW_TOOL_NAMES:
            continue
        output = getattr(tool_result, "output", "")
        output_text = output if isinstance(output, str) else str(output)
        if _control_flow_status_of(tool_name, output_text):
            return ToolsToFinalOutputResult(is_final_output=True, final_output=output_text)

    # 请求级模型调用预算：本轮工具已经执行完，预算用尽就不再发起下一次模型调用
    # （与熔断同一个同步出口；控制流工具优先，让交接/澄清照常落地）。
    if run_meter is not None and run_meter.exhausted:
        run_meter.budget_stopped = True
        return ToolsToFinalOutputResult(is_final_output=True, final_output="")

    # 建文件后的下一次回复没写正文就调了工具、被前置守卫挡回（结构化 file_body_pending），且这份
    # 文件此刻仍在等正文：不让模型在本 run 里接着盲试，正常结束这次 run（MESSAGE_END 照常带用量），
    # 交给图落库核验，再经 FILE_BODY_RESUME 为同一个文件安排纠偏轮。以上终态都优先于它。
    if any(_is_file_body_pending(tool_result) for tool_result in tool_results or []):
        from agent.tools.mcp_tools import ToolContext

        if ToolContext.overdue_file_bodies():
            return ToolsToFinalOutputResult(is_final_output=True, final_output="")

    return ToolsToFinalOutputResult(is_final_output=False, final_output=None)


def _is_file_body_pending(tool_result: Any) -> bool:
    output = getattr(tool_result, "output", "")
    payload = _parse_error_payload(output if isinstance(output, str) else str(output))
    return payload is not None and payload.get("error_type") == "file_body_pending"


class _ModelCallCountingFilter:
    """call_model_input_filter：每次模型调用前计数，再交给内层过滤器处理输入。

    同时负责「到上限前软着陆」：本次之后还能发起的模型调用数——取本次 run 的
    max_turns 余量与请求级预算（run_meter）余量中较小的那个——少于
    SOFT_LANDING_TURNS 时，在内层过滤器的结果末尾追加一条收尾提醒
    （SOFT_LANDING_PROMPT），让模型停止调用工具、写阶段总结，而不是读到上限被
    SDK 截断 / 被预算硬停、什么也没留下。
    提醒追加在 input 末尾而不是改 instructions：系统提示是 DeepSeek 前缀缓存的开头，
    改它会让最后几次调用整段缓存失效。提醒只进本次调用，不写回 run 的历史。
    """

    def __init__(
        self,
        inner: Callable[[Any], Any],
        run_meter: AgentRunMeter,
        *,
        max_turns: int | None = None,
        landing_turns: int = SOFT_LANDING_TURNS,
    ) -> None:
        self._inner = inner
        self._run_meter = run_meter
        self._max_turns = max_turns
        self._landing_turns = landing_turns
        # 本次 SDK run 已发起的模型调用数（区别于 run_meter 的请求级累计）。
        self.calls = 0
        self.landing_injected = False
        # 最近一次收尾提醒是哪个上限触发的（LANDING_LIMIT_*）；没有提醒时为 None。
        self.landing_limit: str | None = None

    def _remaining_calls(self) -> tuple[int, str]:
        """本次之后还能发起的模型调用数，以及先到的那个上限。"""
        remaining = self._run_meter.max_model_calls - self._run_meter.model_calls
        limit = LANDING_LIMIT_MODEL_CALL_BUDGET
        if self._max_turns:
            per_run = self._max_turns - self.calls
            if per_run <= remaining:
                remaining, limit = per_run, LANDING_LIMIT_MAX_TURNS
        return remaining, limit

    def _landing_remaining(self) -> int | None:
        """进入收尾区间时返回本次之后还剩的调用次数，否则 None。

        余量 = min(max_turns - calls, run_meter.max_model_calls - run_meter.model_calls)。
        """
        if self._landing_turns <= 0:
            return None
        remaining, _limit = self._remaining_calls()
        if remaining >= self._landing_turns:
            return None
        return max(remaining, 0)

    def _with_landing(self, model_data: Any) -> Any:
        remaining = self._landing_remaining()
        if remaining is None and not self._run_meter.past_wrap_up:
            return model_data
        from agents.run_config import ModelInputData

        self.landing_injected = True
        if remaining is not None:
            self.landing_limit = self._remaining_calls()[1]
            reminder = {"role": "user", "content": SOFT_LANDING_PROMPT.format(remaining=remaining)}
        else:
            self.landing_limit = LANDING_LIMIT_WALL_CLOCK
            reminder = {"role": "user", "content": TIME_LANDING_PROMPT}
        return ModelInputData(
            input=[*list(model_data.input or []), reminder],
            instructions=model_data.instructions,
        )

    def __call__(self, data: Any) -> Any:
        self._run_meter.record_model_call()
        self.calls += 1
        result = self._inner(data)
        if inspect.isawaitable(result):

            async def _await_then_land() -> Any:
                return self._with_landing(await result)

            return _await_then_land()
        return self._with_landing(result)


def _is_new_model_turn_event(sdk_event: Any) -> bool:
    """事件是否意味着「模型又开了新的一轮」。

    单个 turn 内，模型响应流（raw_response_event）先跑完，SDK 才执行工具并发出
    tool_output；因此控制流工具的 tool_output 之后再出现任何 raw_response_event，
    或再出现 tool_called，都只能来自新的一轮模型调用。
    """
    event_type = str(getattr(sdk_event, "type", "") or "")
    if event_type == "raw_response_event":
        return True
    return event_type == "run_item_stream_event" and getattr(sdk_event, "name", "") == "tool_called"


def _safe_cancel(result: Any, mode: str) -> None:
    """调用 SDK 的 cancel，忽略实现差异/已结束 run 带来的异常。"""
    cancel = getattr(result, "cancel", None)
    if not callable(cancel):
        return
    with contextlib.suppress(Exception):
        cancel(mode=mode)


def extract_text_from_message_content(content: Any) -> str:
    """Convert persisted mixed content blocks to plain text for Chat Completions input."""
    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return "" if content is None else str(content)

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            if block.strip():
                parts.append(block)
            continue
        if not isinstance(block, dict):
            continue

        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text.strip():
                parts.append(text)
        # tool_use / tool_result blocks are intentionally omitted from replay. The SDK owns
        # the live tool-call loop within a single agent run; re-serializing a prior turn's
        # tool calls/results as plain prose would inject raw tool JSON (file ids, arguments)
        # into the next agent as if the *user* had said it, degrading instruction-following.
        # Cross-agent continuity is instead carried by the structured handoff packet
        # (context / completed / artifact_refs) and the refreshed file inventory, which also
        # avoids any orphaned-tool_call_id risk from replaying partial structured tool turns.
        # thinking/reasoning blocks are likewise omitted (persisted for UI/history only).

    return "\n".join(part for part in parts if part.strip())


def normalize_messages_for_openai_agents(messages: MessageList) -> MessageList:
    """Normalize project chat history to SDK easy-input messages."""
    normalized: MessageList = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        if role not in {"user", "assistant", "system", "developer"}:
            continue

        text = extract_text_from_message_content(message.get("content"))
        if role == "assistant" and message.get("stop_reason") == OUTPUT_TRUNCATED_STOP_REASON:
            text = f"{text}\n\n{OUTPUT_TRUNCATED_HISTORY}".strip()
        if not text.strip():
            continue

        normalized.append({"role": role, "content": text})

    return normalized


def should_append_user_message(messages: MessageList, user_message: str) -> bool:
    """Return True when the current user message is not already the last message."""
    if not user_message:
        return False
    if not messages:
        return True
    last = messages[-1]
    return last.get("role") != "user" or str(last.get("content") or "") != user_message


def build_history_messages(state: WritingState) -> MessageList:
    """Build normalized SDK input messages from workflow state."""
    api_messages = normalize_messages_for_openai_agents(list(state.get("messages", []) or []))
    user_message = str(state.get("user_message") or "")
    if should_append_user_message(api_messages, user_message):
        api_messages.append({"role": "user", "content": user_message})
    return api_messages


def build_run_input(
    api_messages: MessageList, agent_directive: str = "", context_snapshot: str = ""
) -> MessageList:
    """这次 SDK run 的输入：[项目资料快照][历史 + 本轮用户消息][这次运行的系统指令]。

    项目资料快照（agent/context/turn_snapshot.py）紧跟系统提示、在历史之前，跨轮、跨 agent
    逐字不变，没重建时整段命中前缀缓存。它只进模型输入，不写回 state["messages"]
    （runner 会把 api_messages 写回 state，下一个 agent 再加一次快照就重复了）。

    系统指令（角色、范围约束等，见 nodes.build_agent_directive）因角色而异，放在最后，
    让 [系统提示][历史][本轮用户消息] 在同一请求的各个 agent 之间逐字相同、命中前缀缓存。
    用 user 角色而不是 system：DeepSeek 的对话模板把所有 system 消息合并到最前面，
    放在末尾的 system 消息会重新打断前缀。它只进本次 SDK 输入，不写回 state["messages"]。
    """
    prefix = [{"role": "user", "content": context_snapshot}] if context_snapshot else []
    if not agent_directive:
        return [*prefix, *api_messages]
    return [*prefix, *api_messages, {"role": "user", "content": agent_directive}]


def _continued_run(state: WritingState, agent_type: str) -> dict[str, Any] | None:
    """The previous run to continue from, when the graph runs the same agent again right after it."""
    holder = state.get("native_run_holder")
    if not state.get("continue_native_run") or not isinstance(holder, dict):
        return None
    if holder.get("agent_type") != agent_type or not holder.get("items"):
        return None
    return holder


def _continued_run_input(
    previous: dict[str, Any],
    state: WritingState,
    agent_directive: str,
    initial_steering: MessageList,
) -> list[Any]:
    """[上一轮完整的 SDK 输入][这之后新增的消息]：请求只在末尾追加，前缀缓存整段命中。

    上一轮的工具调用与结果（读到的文件、写入结果）都还在，下一轮不用重读；同一 agent 的
    运行指令已在里面，只有内容变了才再附一次。只用于同一 agent 紧接着自己的那一轮。
    """
    items: list[Any] = list(previous["items"])
    later = list(state.get("messages") or [])[int(previous.get("messages_len") or 0):]
    items.extend(m for m in normalize_messages_for_openai_agents(later) if m.get("role") == "user")
    user_message = str(state.get("user_message") or "")
    if user_message and user_message != previous.get("user_message"):
        items.append({"role": "user", "content": user_message})
    items.extend(initial_steering)
    if agent_directive and agent_directive != previous.get("directive"):
        items.append({"role": "user", "content": agent_directive})
    return items


def _append_assistant_turn_to_state_messages(
    state: WritingState,
    api_messages: MessageList,
    *,
    assistant_text: str,
    thinking_text: str,
    tool_uses: list[dict[str, Any]],
) -> None:
    """Persist the SDK run progression for downstream graph agents."""
    updated: MessageList = list(api_messages)

    assistant_content: list[dict[str, Any]] = []
    if thinking_text.strip():
        assistant_content.append({"type": "thinking", "thinking": thinking_text})
    if assistant_text.strip():
        assistant_content.append({"type": "text", "text": assistant_text})
    for tool_use in tool_uses:
        assistant_content.append(
            {
                "type": "tool_use",
                "id": tool_use.get("id", ""),
                "name": tool_use.get("name", ""),
                "input": tool_use.get("input", {}),
            }
        )

    if assistant_content:
        updated.append({"role": "assistant", "content": assistant_content})

    tool_results = []
    for tool_use in tool_uses:
        if tool_use.get("result") is None:
            continue
        tool_results.append(
            {
                "type": "tool_result",
                "tool_use_id": tool_use.get("id", ""),
                "content": tool_use.get("result", ""),
            }
        )
    if tool_results:
        updated.append({"role": "user", "content": tool_results})

    state["messages"] = updated


def _raw_event_type(raw_event: Any) -> str:
    return str(getattr(raw_event, "type", "") or "")


def _raw_event_delta(raw_event: Any) -> str:
    delta = getattr(raw_event, "delta", "")
    return delta if isinstance(delta, str) else str(delta or "")


def _raw_item_value(raw_item: Any, key: str, default: Any = None) -> Any:
    if isinstance(raw_item, dict):
        return raw_item.get(key, default)
    return getattr(raw_item, key, default)


def _tool_call_payload(item: Any) -> tuple[str, str, dict[str, Any]]:
    tool_name = getattr(item, "tool_name", None) or _raw_item_value(getattr(item, "raw_item", None), "name", "")
    call_id = getattr(item, "call_id", None) or _raw_item_value(getattr(item, "raw_item", None), "call_id", "")
    raw_arguments = _raw_item_value(getattr(item, "raw_item", None), "arguments", "")
    if raw_arguments is None:
        raw_arguments = ""
    parsed, parse_error, _metadata = parse_json_object(str(raw_arguments), tool_name=str(tool_name or ""))
    if parse_error is not None:
        parsed = {}
    return str(tool_name or ""), str(call_id or ""), parsed


def _tool_output_payload(item: Any) -> tuple[str, str]:
    call_id = getattr(item, "call_id", None) or _raw_item_value(getattr(item, "raw_item", None), "call_id", "")
    output = getattr(item, "output", "")
    return str(call_id or ""), output if isinstance(output, str) else str(output)


def _int_attr(source: Any, name: str) -> int:
    """读取 usage 上的整数字段，缺失/None/非法一律按 0 处理。"""
    value = getattr(source, name, 0)
    if value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _last_response_output_tokens(result: Any) -> int:
    """本次 SDK run 最后一次模型响应的 output_tokens（DeepSeek 的 completion_tokens 含思考）。"""
    raw_responses = getattr(result, "raw_responses", None)
    if not isinstance(raw_responses, list) or not raw_responses:
        return 0
    return _int_attr(getattr(raw_responses[-1], "usage", None), "output_tokens")


def _last_response_is_thinking_only(result: Any) -> bool:
    """最后一次模型响应里只有思考：没有正文，也没有工具调用。

    只看最后一次响应，而不是整个 run：交接后的 writer 常先调工具（例如重读稿件），
    之后那次响应才只剩思考并撞上输出上限；按整个 run 判断会漏掉这种情况。
    """
    raw_responses = getattr(result, "raw_responses", None)
    if not isinstance(raw_responses, list) or not raw_responses:
        return False
    for item in getattr(raw_responses[-1], "output", None) or []:
        item_type = _raw_item_value(item, "type", "")
        if item_type == "function_call":
            return False
        if item_type == "message":
            for block in _raw_item_value(item, "content", None) or []:
                text = _raw_item_value(block, "text", "")
                if isinstance(text, str) and text.strip():
                    return False
    return True


def _usage_dict_from_result(result: Any) -> dict[str, int]:
    """汇总本次 SDK run 全部模型响应的 token 用量。

    cache_read_tokens：DeepSeek 的 prompt cache 命中量（OpenAI 兼容字段
    prompt_tokens_details.cached_tokens，SDK 映射为 input_tokens_details.cached_tokens）。
    它是 prompt_tokens 的**子集**，而 writing_stats_service 的计价公式是
    input * 输入价 + cache_read * 缓存价（相加），所以这里必须把命中缓存的部分从
    input_tokens 里扣掉，否则同一批 token 会被按输入价和缓存价重复计费。
    """
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cache_read_tokens": 0,
    }
    raw_responses = getattr(result, "raw_responses", None)
    if not isinstance(raw_responses, list):
        return {}

    for raw_response in raw_responses:
        response_usage = getattr(raw_response, "usage", None)
        if response_usage is None:
            continue

        input_tokens = _int_attr(response_usage, "input_tokens")
        cached_tokens = _int_attr(
            getattr(response_usage, "input_tokens_details", None), "cached_tokens"
        )
        cached_tokens = max(0, min(cached_tokens, input_tokens))

        totals["input_tokens"] += input_tokens - cached_tokens
        totals["cache_read_tokens"] += cached_tokens
        totals["output_tokens"] += _int_attr(response_usage, "output_tokens")
        totals["total_tokens"] += _int_attr(response_usage, "total_tokens")

    # 保持「无用量时返回空 dict」的既有契约：下游用非空判断是否拿到真实 usage。
    return {key: value for key, value in totals.items() if value}


def _format_tool_error(args: Any, *, failure_breaker: ToolFailureBreaker | None = None) -> str | None:
    """RunConfig.tool_error_formatter：把「工具不存在」改写成项目统一的结构化错误。

    只处理 kind == "tool_not_found"（其余返回 None 走 SDK 默认文案）。结果经
    请求级熔断器记账：模型反复调用不存在的工具时，与普通工具失败一样受
    「同一调用连续失败」与「累计失败」两条上限约束；前者触发时熔断原因是
    TRIP_REASON_TOOL_NOT_FOUND（调用没执行，文案不说「以相同参数失败」）。
    """
    if getattr(args, "kind", None) != "tool_not_found":
        return None
    tool_name = str(getattr(args, "tool_name", "") or "")
    text = tool_error_text(
        f"工具 {tool_name} 不在你本轮可用的工具集里，调用未执行。"
        "请只使用已提供的工具；需要改文件但没有写工具时，直接在回答里说明。",
        error_type="tool_not_found",
        tool_name=tool_name,
    )
    if failure_breaker is not None:
        text = failure_breaker.observe(tool_name, "", text)
    return text


async def _preferred_model_name() -> str:
    """The model this run's first call will try (MESSAGE_START, ledger fallback)."""
    from agent.core import llm_failover, llm_providers
    from agent.tools.mcp_tools import ToolContext

    try:
        return (await llm_failover.aordered_providers(ToolContext.get_session_id()))[0].model
    except Exception:  # noqa: BLE001 - a missing key surfaces from the model call itself
        return llm_providers.primary_model_name()


def _build_agent(
    agent_type: str,
    system_prompt: str,
    *,
    failure_breaker: ToolFailureBreaker | None = None,
    read_only: bool = False,
    run_meter: AgentRunMeter | None = None,
    read_guard: RepeatReadGuard | None = None,
    read_only_reason: str = READ_ONLY_REASON_USER_REQUEST,
) -> Any:
    # NOTE — Agent.as_tool was evaluated and rejected.
    # Agent.as_tool wraps an agent as a callable tool for a parent agent, which
    # would collapse each sub-agent's SSE events into a single opaque tool result
    # and eliminate the per-agent streaming visibility the UI depends on.  It would
    # also merge the two independent iteration budgets (AGENT_TOOL_CALL_MAX_ITERATIONS
    # per agent, AGENT_COLLABORATION_MAX_ITERATIONS across the graph) into one, making
    # it impossible to surface per-agent exhaustion cleanly.  The current explicit
    # graph loop + handoff packet approach is intentional; do not replace with as_tool.
    #
    # NOTE — reasoning lever (verified, intentionally not set for DeepSeek).
    # MiniMax gets MINIMAX_REASONING_EFFORT (model._settings_for) because its own default is max.
    # deepseek-flash's Chat Completions endpoint accepts two reasoning controls (live probe
    # 2026-10-03): ``reasoning_effort`` (none|minimal|low|medium|high|xhigh|ultra|max;
    # unknown values return 422) and ``extra_body={"thinking": {"type": "disabled"}}``.
    # ModelSettings exposes them as ``reasoning={"effort": ...}`` / ``extra_body``.
    # The writing agent deliberately runs at the model's default effort (high): lowering it
    # trades writing/tool-planning quality for latency and tokens, and that trade has not
    # been evaluated.  LLMClient.acomplete(thinking_enabled=False) already uses the
    # ``thinking`` toggle for short single-shot calls (suggestions, polish).
    # #278: AGENT_REASONING_EFFORT exists only for controlled comparisons; it is unset in
    # production (see .agents/notes/.../2026-10-19-reasoning-effort-evaluation.md).
    from agents import Agent, ModelSettings, set_tracing_disabled
    from openai.types.shared import Reasoning

    # 与 RunConfig(tracing_disabled=True) 双保险：在任何 run 开始前全局关掉 SDK tracing。
    set_tracing_disabled(True)

    from agent.openai_agents.usage_hooks import agent_usage_attribution
    from agent.tools.mcp_tools import ToolContext

    effort = agent_runtime.AGENT_REASONING_EFFORT

    return Agent(
        name=agent_type,
        instructions=system_prompt,
        model=build_failover_chat_model(
            ToolContext.get_session_id(),
            on_served=run_meter.record_served_model if run_meter is not None else None,
            # Failed and cancelled attempts get a zero-token ledger row (admin model metrics).
            usage_attribution=agent_usage_attribution(),
        ),
        model_settings=ModelSettings(
            temperature=1.0,
            top_p=0.95,
            max_tokens=AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS,
            # include_usage 决定请求体里是否带 stream_options={"include_usage": true}。
            # SDK 的默认值只在 client 指向 api.openai.com 时才是 True
            # （chatcmpl_helpers.get_stream_options_param），而本项目走 DeepSeek / MiniMax，
            # 不显式打开的话 OpenAI 兼容流式接口不会补发带 usage 的末尾 chunk，
            # MESSAGE_END 的 usage 恒为空，用量统计只能退化成「字符数/4」的估算。
            include_usage=True,
            **({"reasoning": Reasoning(effort=effort)} if effort else {}),  # type: ignore[arg-type]
        ),
        tools=build_agent_function_tools(
            agent_type,
            failure_breaker=failure_breaker,
            read_only=read_only,
            read_guard=read_guard,
            read_only_reason=read_only_reason,
            run_meter=run_meter,
        ),
        # 控制流工具生效、或工具失败熔断时即结束 run，避免 SDK 在「工作流已暂停」
        # 之后再跑一整轮模型调用并真实执行其工具（详见 _stop_run_on_control_flow_tool）。
        tool_use_behavior=functools.partial(
            _stop_run_on_control_flow_tool,
            failure_breaker=failure_breaker,
            run_meter=run_meter,
            read_guard=read_guard,
        ),
    )


async def _inject_initial_steering(
    api_messages: MessageList,
    get_steering_messages: Callable[[], Any] | None,
) -> AsyncIterator[StreamEvent]:
    if get_steering_messages is None:
        return

    try:
        steering_msgs = await get_steering_messages()
        for msg in steering_msgs or []:
            content = str(msg.get("content") or "") if isinstance(msg, dict) else ""
            if not content:
                continue
            api_messages.append({"role": "user", "content": content})
            from agent.core.events import steering_received_event

            yield steering_received_event(
                message_id=str(msg.get("id") or "") if isinstance(msg, dict) else "",
                preview=content[:50],
            )
    except Exception as exc:
        log_with_context(
            logger,
            40,  # ERROR
            "Failed to retrieve steering messages for OpenAI Agents run",
            error=str(exc),
            error_type=type(exc).__name__,
        )


async def _consume_boundary_steering(
    collected: list[str],
    get_steering_messages: Callable[[], Any] | None,
) -> AsyncIterator[StreamEvent]:
    """Consume steering that arrived while the SDK run is executing.

    openai-agents 不支持向进行中的 run 注入消息，工具输出边界只做消费与
    前端确认；内容暂存 collected，run 结束后写回 state.messages，由 graph
    决定是否追加一轮 writer 让引导在本次请求内生效。
    """
    if get_steering_messages is None:
        return
    try:
        steering_msgs = await get_steering_messages()
    except Exception as exc:
        log_with_context(
            logger,
            40,  # ERROR
            "Failed to retrieve steering messages at tool boundary",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return
    for msg in steering_msgs or []:
        content = str(msg.get("content") or "") if isinstance(msg, dict) else ""
        if not content:
            continue
        collected.append(content)
        from agent.core.events import steering_received_event

        yield steering_received_event(
            message_id=str(msg.get("id") or "") if isinstance(msg, dict) else "",
            preview=content[:50],
        )


async def _pump_sdk_events(result: Any, run_queue: asyncio.Queue[tuple[str, Any]]) -> None:
    """Forward SDK stream events onto the shared run queue, tagged by kind.

    Runs as a background task so the consumer can interleave live in-tool
    progress events (pushed onto the same queue via the progress channel) with
    the SDK's own events. The SDK parks ``stream_events()`` while a tool's
    callback runs, so without this pump those progress events could not be
    surfaced until the tool returned.

    Terminal protocol: on a stream exception, emits ``("error", exc)`` so the
    consumer can re-raise it in the original control flow; ``("done", None)`` is
    always emitted last so the consumer's drain loop can terminate.
    """
    try:
        async for sdk_event in result.stream_events():
            run_queue.put_nowait(("sdk", sdk_event))
    except asyncio.CancelledError:
        raise
    except BaseException as exc:  # noqa: BLE001 — forwarded to consumer for unified handling
        run_queue.put_nowait(("error", exc))
    finally:
        run_queue.put_nowait(("done", None))


async def run_openai_agents_streaming_agent(
    state: WritingState,
    agent_type: str,
    system_prompt: str,
    get_steering_messages: Callable[[], Any] | None = None,
    failure_breaker: ToolFailureBreaker | None = None,
    agent_directive: str = "",
) -> AsyncIterator[StreamEvent]:
    """Run one writing agent via openai-agents-python and yield workflow events.

    agent_directive：这次运行的系统指令（nodes.build_agent_directive），作为输入最后一条消息，
    不写回 state["messages"]（见 build_run_input）。

    failure_breaker：工具失败熔断器。不传时取 state["tool_failure_breaker"]
    （writing_graph 每个请求建一个、跨 agent run 共享）；都没有时本次 run 自建一个。
    重复读取守卫同理取 state["repeat_read_guard"]。
    """
    # Snapshot the persisted conversation before per-agent replay strips metadata.
    # Keep it across same-request handoffs, which are not separate author turns.
    state.setdefault(
        "previous_output_truncations", trailing_output_truncations(list(state.get("messages") or []))
    )
    api_messages = build_history_messages(state)
    assistant_text_parts: list[str] = []
    thinking_text_parts: list[str] = []
    tool_uses: list[dict[str, Any]] = []
    tool_inputs_by_call_id: dict[str, dict[str, Any]] = {}
    tool_names_by_call_id: dict[str, str] = {}
    mid_run_steering: list[str] = []
    artifact_refs_accumulated: list[str] = []
    handoff_event_data: dict[str, Any] | None = None
    clarification_event_data: dict[str, Any] | None = None
    # 控制流工具已生效：本 run 不应再有新的模型轮次（见 _is_new_model_turn_event）。
    control_flow_stopped = False
    # 工具失败熔断器按请求共享：writer → 审稿人 → writer 的往返不能让「同一调用
    # 连续失败」的计数归零。消费循环在 tool_output 处看到它已熔断后，与控制流
    # 工具一样不再接受新的模型轮次。
    if failure_breaker is None:
        shared_breaker = state.get("tool_failure_breaker")
        failure_breaker = (
            shared_breaker
            if isinstance(shared_breaker, ToolFailureBreaker)
            else ToolFailureBreaker()
        )
    failure_stopped = False
    # 重复读取守卫按请求共享读写台账与拦截次数，但读取计数按 agent run 清零：
    # 工具结果不回放给下一个 run，上一个 run 读过的内容不在这个 run 的上下文里。
    shared_guard = state.get("repeat_read_guard")
    read_guard = shared_guard if isinstance(shared_guard, RepeatReadGuard) else RepeatReadGuard()
    # 同一 agent 紧接着自己再跑一轮（续写、纠偏、只写总结……）时带上上一轮完整的 SDK 输入：
    # 读到的内容仍在上下文里，读取计数不清零（见 _continued_run_input）。
    native_runs = state.get("native_run_holder")
    continued = _continued_run(state, agent_type)
    if continued is None:
        read_guard.begin_agent_run()
    no_progress_stopped = False
    max_turns_exhausted = False
    turn_filter: _ModelCallCountingFilter | None = None
    # 请求级模型调用预算（writing_graph 建一个跨 agent run 共享；单独调用 runner
    # 时自建一个默认预算）。
    shared_meter = state.get("run_meter")
    run_meter = shared_meter if isinstance(shared_meter, AgentRunMeter) else AgentRunMeter()
    budget_stopped = False
    run_started_at = time.monotonic()
    # 这次 run 正常结束时它完整的 SDK 输入（原输入 + 模型输出与工具结果），供同一 agent 的下一轮接着用。
    completed_items: list[Any] | None = None

    # Read-only co-call instrumentation (item 1.4).
    # Approximation: within a single assistant turn, the SDK emits all tool_called events
    # first, then tool_output events (even with max_function_tool_concurrency=1, which
    # serialises *execution* but not the event ordering for calls batched in one response).
    # We track per-turn how many read-only tool calls appear before the first tool_output
    # of that turn; when ≥2 are observed we record a co-call event.  A new "turn" begins
    # after every tool_output (the model produced a fresh response with new tool requests).
    _READONLY_TOOLS: frozenset[str] = frozenset({"query_files"})
    _turn_readonly_pending: int = 0   # read-only calls seen since last tool_output
    _turn_has_output: bool = False    # whether the current turn has received any tool_output

    if run_meter.exhausted:
        # 上一个 agent run 已经把预算用完（例如最后一次调用恰好以交接结束）：
        # 不再发起任何模型调用，直接以预算用尽结束整条工作流。
        log_with_context(
            logger,
            30,  # WARNING
            "Model call budget exhausted before agent run",
            agent_type=agent_type,
            model_calls=run_meter.model_calls,
            max_model_calls=run_meter.max_model_calls,
        )
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data=model_call_limit_error().as_event_data(
                error_type=MODEL_CALL_LIMIT_ERROR_TYPE, agent_type=agent_type
            ),
        )
        return

    history_len = len(api_messages)
    async for steering_event in _inject_initial_steering(api_messages, get_steering_messages):
        yield steering_event
    initial_steering = api_messages[history_len:]

    start_model = await _preferred_model_name()
    yield StreamEvent(
        type=StreamEventType.MESSAGE_START,
        data={"model": start_model, "agent_type": agent_type},
    )

    # Shared queue interleaving SDK events (via _pump_sdk_events) with live
    # in-tool progress events (via the progress channel emitter installed below).
    run_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
    pump_task: asyncio.Task[None] | None = None
    run_hooks: Any = None
    timing_outcome: TimingOutcome = "completed"
    try:
        from agents import RunConfig, Runner, ToolExecutionConfig
        from agents.exceptions import MaxTurnsExceeded

        from .intra_run_trimmer import IntraRunToolOutputTrimmer
        from .usage_hooks import build_agent_run_hooks

        # 用户明确要求不改文件：写工具调用一律拒绝执行（由 writing_graph 按路由结果置位）。
        # 作者的要求太笼统、本轮先问清楚（clarify_first）时写工具同样拒绝执行，拒绝说明不同。
        read_only = state.get("read_only") is True or state.get("clarify_first") is True
        read_only_reason = (
            READ_ONLY_REASON_USER_REQUEST if state.get("read_only") is True else READ_ONLY_REASON_CLARIFY_FIRST
        )
        sdk_agent = _build_agent(
            agent_type,
            system_prompt,
            failure_breaker=failure_breaker,
            read_only=read_only,
            read_only_reason=read_only_reason,
            run_meter=run_meter,
            read_guard=read_guard,
        )
        # 工具清单对所有角色、所有轮次相同；本轮哪些调用会被拒绝写进这次运行的系统指令。
        availability_note = tool_availability_note(
            agent_type, read_only=read_only, read_only_reason=read_only_reason
        )
        if availability_note:
            agent_directive = (
                f"{agent_directive}\n\n## 本轮工具限制\n\n{availability_note}"
                if agent_directive
                else f"{AGENT_DIRECTIVE_HEADER}\n\n## 本轮工具限制\n\n{availability_note}"
            )
        # 外层计数器给请求级模型调用预算记账（每次模型调用前调用一次），并在
        # 接近 max_turns 时追加收尾提醒（软着陆）。
        turn_filter = _ModelCallCountingFilter(
            IntraRunToolOutputTrimmer(),
            run_meter,
            max_turns=AGENT_TOOL_CALL_MAX_ITERATIONS,
        )
        # Install the progress emitter only across run_streamed. The SDK creates
        # its background task synchronously inside run_streamed, copying the
        # current context (with the emitter) into it; that snapshot is
        # independent of ours, so we reset immediately afterwards to keep this
        # generator's context clean and avoid cross-yield contextvar tokens.
        emitter_token = set_progress_emitter(
            lambda event: run_queue.put_nowait(("progress", event))
        )
        try:
            run_hooks = build_agent_run_hooks(start_model, run_meter=run_meter)
            result = Runner.run_streamed(
                sdk_agent,
                input=(
                    _continued_run_input(continued, state, agent_directive, initial_steering)
                    if continued is not None
                    else build_run_input(api_messages, agent_directive, str(state.get("context_snapshot") or ""))
                ),
                max_turns=AGENT_TOOL_CALL_MAX_ITERATIONS,
                # 每次模型调用结束写一行用量账本（llm_usage_event），按调用自身时间计价；
                # 之后 run 报错/取消也不会丢掉已完成调用的用量。同一钩子在执行本轮工具前
                # 告诉空文件守卫"上一份正文已在本条回复里写完"（见 usage_hooks）。
                hooks=run_hooks,
                # When DeepSeek emits multiple tool_calls in one turn, the SDK would run them
                # concurrently (asyncio.create_task). Project tools share a single SQLAlchemy
                # Session via ToolContext, which is NOT safe for concurrent use. Serialize tool
                # execution to preserve the previous sequential contract and avoid Session races.
                run_config=RunConfig(
                    # 不向 OpenAI 的 trace 后端导出任何内容（作者的稿件与对话）。模型客户端在第一次
                    # 模型调用时才建，那时 SDK 已经为这次 run 开了 trace，所以这里按 run 关掉。
                    tracing_disabled=True,
                    trace_include_sensitive_data=False,
                    tool_execution=ToolExecutionConfig(max_function_tool_concurrency=1),
                    # IntraRunToolOutputTrimmer 只是上下文安全阀：输入未超 max_input_chars 时原样
                    # 返回、不改写任何历史（保住前缀缓存，也不让模型「忘掉」读过的文件而重读）；
                    # 超限才从最旧的读/写载荷开始折叠。控制流工具结果永不改动。见 intra_run_trimmer。
                    # 计数 + 软着陆见上方 turn_filter。
                    call_model_input_filter=turn_filter,
                    # 模型调用了本 agent 工具集里没有的工具（例如审稿人照着共享历史调
                    # edit_file、或幻觉出的工具名）：SDK 默认抛 ModelBehaviorError，整轮
                    # 以致命 ERROR 结束、原始异常文本直接展示给用户。改为把结构化错误
                    # 交还模型让它自行纠正；这类错误同样经熔断器记账（见
                    # _format_tool_error），乱试有上限。
                    tool_not_found_behavior="return_error_to_model",
                    tool_error_formatter=functools.partial(
                        _format_tool_error, failure_breaker=failure_breaker
                    ),
                ),
            )
        finally:
            reset_progress_emitter(emitter_token)

        pump_task = asyncio.create_task(_pump_sdk_events(result, run_queue))

        try:
            while True:
                kind, payload = await run_queue.get()
                if kind == "progress":
                    # Pre-built workflow/SSE event emitted from inside a tool call
                    # (e.g. parallel_execute sub-task progress). Forward as-is.
                    yield payload
                    continue
                if kind == "done":
                    break
                if kind == "error":
                    raise payload
                sdk_event = payload
                if (
                    control_flow_stopped or failure_stopped or budget_stopped or no_progress_stopped
                ) and _is_new_model_turn_event(sdk_event):
                    # 兜底护栏：正常情况下 tool_use_behavior 已经让 run-loop 在控制流
                    # 工具返回的那一刻结束，走不到这里。一旦走到（工具改名、SDK 行为
                    # 变化等），说明 SDK 又开了新的一轮——立即硬取消并截断消费循环，
                    # 绝不把暂停之后的正文/工具调用透给前端或写回 state.messages。
                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Control-flow/failure stop did not end the SDK run; truncating extra turn",
                        agent_type=agent_type,
                        sdk_event_type=str(getattr(sdk_event, "type", "") or ""),
                    )
                    _safe_cancel(result, "immediate")
                    break
                event_type = getattr(sdk_event, "type", "")

                if event_type == "raw_response_event":
                    raw = getattr(sdk_event, "data", None)
                    raw_type = _raw_event_type(raw)
                    if raw_type == "response.output_text.delta":
                        text = _raw_event_delta(raw)
                        if text:
                            assistant_text_parts.append(text)
                            yield StreamEvent(type=StreamEventType.TEXT, data={"text": text})
                    elif raw_type in {
                        "response.reasoning_text.delta",
                        "response.reasoning_summary_text.delta",
                    }:
                        thinking = _raw_event_delta(raw)
                        if thinking:
                            thinking_text_parts.append(thinking)
                            yield StreamEvent(type=StreamEventType.THINKING, data={"thinking": thinking})
                    continue

                if event_type != "run_item_stream_event":
                    continue

                event_name = getattr(sdk_event, "name", "")
                item = getattr(sdk_event, "item", None)
                if event_name == "tool_called":
                    tool_name, call_id, tool_input = _tool_call_payload(item)
                    if call_id:
                        tool_names_by_call_id[call_id] = tool_name
                        tool_inputs_by_call_id[call_id] = tool_input
                    tool_uses.append(
                        {
                            "id": call_id,
                            "name": tool_name,
                            "input": tool_input,
                            "result": None,
                        }
                    )
                    # Read-only co-call tracking: count read-only calls before outputs arrive.
                    # A tool_called arriving after a turn's outputs starts a NEW turn, so
                    # reset the per-turn tracking here (not in the tool_output block) — that
                    # keeps the turn guard set for the whole turn and avoids counting every
                    # tool_output as a separate turn.
                    if _turn_has_output:
                        _turn_has_output = False
                        _turn_readonly_pending = 0
                    if tool_name in _READONLY_TOOLS:
                        _turn_readonly_pending += 1
                    yield StreamEvent(
                        type=StreamEventType.TOOL_USE,
                        data={
                            "id": call_id,
                            "name": tool_name,
                            "status": "complete",
                            "input": tool_input,
                        },
                    )
                elif event_name == "tool_output":
                    # First tool_output of this turn: flush the pending read-only count.
                    # The guard stays set for the rest of the turn; the next tool_called
                    # batch resets it. This counts each turn exactly once (the previous
                    # in-block reset made it count every tool_output as a turn).
                    if not _turn_has_output:
                        _turn_has_output = True
                        from agent.core.metrics import (
                            TOOL_READONLY_COCALL_TOTAL,
                            TOOL_READONLY_TURNS_TOTAL,
                            get_metrics_collector,
                        )
                        _mc = get_metrics_collector()
                        _mc.increment_counter(TOOL_READONLY_TURNS_TOTAL)
                        if _turn_readonly_pending >= 2:
                            _mc.increment_counter(TOOL_READONLY_COCALL_TOTAL)
                    call_id, result_text = _tool_output_payload(item)
                    tool_name = tool_names_by_call_id.get(call_id, "")
                    tool_input = tool_inputs_by_call_id.get(call_id, {})
                    if tool_uses:
                        for tool_use in reversed(tool_uses):
                            if tool_use.get("id") == call_id:
                                tool_use["result"] = result_text
                                break
                    result_payload = mcp_text_result(result_text)

                    artifact_refs_accumulated = merge_unique_strings(
                        artifact_refs_accumulated,
                        extract_artifact_refs(tool_name, result_text, tool_input=tool_input),
                    )

                    yield StreamEvent(
                        type=StreamEventType.TOOL_RESULT,
                        data={
                            "tool_use_id": call_id,
                            "name": tool_name,
                            "result": result_payload,
                        },
                    )

                    try:
                        result_data = json.loads(result_text) if result_text else {}
                    except json.JSONDecodeError:
                        result_data = {}

                    if tool_name == "handoff_to_agent" and result_data.get("status") == _CONTROL_FLOW_STOP_STATUS["handoff_to_agent"]:
                        # DESIGN NOTE — do not migrate to the SDK's native Agent(handoffs=[...]).
                        #
                        # The SDK's built-in handoff mechanism passes a plain text string from
                        # one agent to the next.  ZenStory's handoff carries a structured packet
                        # (completed/todo/evidence/artifact_refs) assembled here from the live
                        # tool result, plus pre-handoff narration that has already been streamed
                        # as TEXT events.  The SDK's text-passing handoff cannot express this
                        # structured context, and rebuilding it inside the SDK's callback would
                        # require duplicating the packet-assembly and event-streaming logic that
                        # lives in build_handoff_packet / extract_artifact_refs.
                        #
                        # 「停下」由 Agent(tool_use_behavior=_stop_run_on_control_flow_tool)
                        # 在 SDK turn 内同步完成，run-loop 不会再发起下一次模型调用。
                        # 这里的 cancel(mode="after_turn") 只是第二道保险（万一 SDK 侧的
                        # 截断未生效，至少不让它跨过下一个 turn 边界）；历史上仅靠它是
                        # 不够的——cancel 标志异步落地，模型早已跑完并执行了额外的工具。
                        packet = build_handoff_packet(
                            result_data,
                            artifact_refs=artifact_refs_accumulated,
                        )
                        handoff_event_data = {
                            "target_agent": packet["target_agent"],
                            "reason": packet["reason"],
                            "context": packet["context"],
                            "handoff_packet": packet,
                        }
                        control_flow_stopped = True
                        _safe_cancel(result, "after_turn")
                    elif tool_name == "request_clarification" and result_data.get("status") == _CONTROL_FLOW_STOP_STATUS["request_clarification"]:
                        clarification_event_data = {
                            "reason": "clarification_needed",
                            "agent_type": agent_type,
                            "message": result_data.get("question", "等待您的回复"),
                            "question": result_data.get("question", ""),
                            "context": result_data.get("context", ""),
                            "details": normalize_str_list(result_data.get("details")),
                        }
                        control_flow_stopped = True
                        _safe_cancel(result, "after_turn")

                    if failure_breaker.is_open and not failure_stopped:
                        # 熔断由 tool_use_behavior 在 SDK turn 内同步生效；这里同样只是
                        # 第二道保险，并让上面的护栏截断任何越界的新一轮。
                        failure_stopped = True
                        _safe_cancel(result, "after_turn")

                    if read_guard.is_open and not no_progress_stopped:
                        # 无进展停止同样由 tool_use_behavior 同步生效，这里是第二道保险。
                        no_progress_stopped = True
                        _safe_cancel(result, "after_turn")

                    if run_meter.exhausted and not budget_stopped:
                        # 预算同样由 tool_use_behavior 同步截停；「工具不存在」这类没有
                        # FunctionTool 结果的轮次不会经过它，这里兜底。
                        budget_stopped = True
                        run_meter.budget_stopped = True
                        _safe_cancel(result, "after_turn")

                    # 工具输出边界是 run 内唯一能消费 steering 的时机（SDK 事件
                    # 消费循环的其余位置都在等模型流式输出）。
                    async for steering_event in _consume_boundary_steering(
                        mid_run_steering, get_steering_messages
                    ):
                        yield steering_event
        except MaxTurnsExceeded:
            # 轮数耗尽的对外事件放到熔断/无进展判定之后统一发出（见下方）。
            max_turns_exhausted = True

        failure_trip = failure_breaker.trip
        if failure_trip is not None:
            # 工具重复失败熔断：先发 MESSAGE_END 让本 run 已消耗的 usage 照常入账，
            # 再以 ERROR 结束——StreamAdapter 把 ERROR 当作致命错误：中断并关闭整个
            # 工作流生成器（不会再跑计划内交接/自动质检），并以 error 帧作为 SSE
            # 终止帧；api/agent.py 据此按 internal_error 退还本次对话额度。
            # 同一 run 里若还有生效的 handoff/clarification，一律让位给熔断。
            log_with_context(
                logger,
                30,  # WARNING
                "Tool failure circuit breaker opened; stopping agent run",
                agent_type=agent_type,
                trip_reason=failure_trip.reason,
                tool_name=failure_trip.tool_name,
                failures=failure_trip.failures,
                total_failures=failure_breaker.total_failures,
                last_error=failure_trip.last_error,
            )
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={
                    "stop_reason": TOOL_FAILURE_STOP_REASON,
                    "usage": _usage_dict_from_result(result),
                },
            )
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=failure_trip.as_event_data(agent_type),
            )
            return

        no_progress_trip = read_guard.trip
        if no_progress_trip is not None:
            # 重复读取无进展：与熔断同一个收尾——先让 usage 入账，再以 ERROR 结束整条
            # 工作流（不再交接/送审）。本请求没有任何写入成功时 refundable=True，
            # stream_billing 据 reason=no_progress 退还额度（billing_reason=runaway_no_progress）。
            from agent.core.metrics import get_metrics_collector

            get_metrics_collector().increment_counter(NO_PROGRESS_STOP_TOTAL)
            log_with_context(
                logger,
                30,  # WARNING
                "Repeated reads without progress; stopping agent run",
                agent_type=agent_type,
                blocked_reads=read_guard.blocked_reads,
                duplicate_reads=read_guard.duplicate_reads,
                files_read=len(read_guard.files_read),
                files_written=len(read_guard.files_written),
                write_succeeded=read_guard.write_succeeded,
                model_turns=turn_filter.calls if turn_filter is not None else 0,
            )
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={
                    "stop_reason": NO_PROGRESS_STOP,
                    "usage": _usage_dict_from_result(result),
                },
            )
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=no_progress_trip.as_event_data(
                    agent_type, wrote_files=read_guard.write_succeeded
                ),
            )
            return

        if run_meter.budget_stopped and clarification_event_data is None and handoff_event_data is None:
            # 请求级模型调用预算用尽：与熔断一样先让本 run 的 usage 入账，再以
            # ERROR 结束整条工作流（StreamAdapter 会关闭工作流生成器）。
            log_with_context(
                logger,
                30,  # WARNING
                "Model call budget exhausted; stopping agent run",
                agent_type=agent_type,
                model_calls=run_meter.model_calls,
                max_model_calls=run_meter.max_model_calls,
            )
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={
                    "stop_reason": MODEL_CALL_BUDGET_STOP_REASON,
                    "usage": _usage_dict_from_result(result),
                },
            )
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=model_call_limit_error().as_event_data(
                    error_type=MODEL_CALL_LIMIT_ERROR_TYPE, agent_type=agent_type
                ),
            )
            return

        last_output_tokens = _last_response_output_tokens(result)
        if (
            not max_turns_exhausted
            and handoff_event_data is None
            and clarification_event_data is None
            and AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS > 0
            and last_output_tokens >= AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS * OUTPUT_CAP_RATIO
            and _last_response_is_thinking_only(result)
        ):
            # 最后一次响应只有思考（没有正文、没有工具调用），又撞上了输出上限，本 run
            # 也没有交接/澄清——这是被截断，不是正常结束。之前按 end_turn 收尾，最后这
            # 一步什么都没交付，工作流静默结束，作者只看到一段思考、没有任何结果或提示。
            # 先让 usage 入账，再以不可重试、可回复「继续」的 ERROR 结束；是否退还额度由 stream_billing 的
            # 既有规则决定（本轮无产出退还，已有产出照常计费）。只修状态，不限制思考。
            log_with_context(
                logger,
                30,  # WARNING
                "Agent run hit the output cap with thinking only; ending as truncated",
                agent_type=agent_type,
                output_tokens=last_output_tokens,
                max_output_tokens=AGENT_OPENAI_AGENTS_MAX_OUTPUT_TOKENS,
                thinking_chars=sum(len(part) for part in thinking_text_parts),
                stop_basis="usage_output_tokens_at_cap",
                model_turns=len(getattr(result, "raw_responses", None) or []),
            )
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={
                    "stop_reason": OUTPUT_TRUNCATED_STOP_REASON,
                    "usage": _usage_dict_from_result(result),
                },
            )
            truncation_count = state["previous_output_truncations"] + 1
            yield StreamEvent(
                type=StreamEventType.ERROR,
                data=output_truncated_error(repeated=truncation_count > 1).as_event_data(
                    agent_type=agent_type, output_truncation_count=truncation_count
                ),
            )
            return

        model_turns = turn_filter.calls if turn_filter is not None else 0
        landed_without_control_flow = (
            turn_filter is not None
            and turn_filter.landing_injected
            and clarification_event_data is None
            and handoff_event_data is None
        )
        # 真正撞上 max_turns，或已进入收尾提醒且没有交接/澄清就结束（模型照提醒写了
        # 阶段总结）：都按「工具调用轮数用尽」收尾——发状态卡（前端给「继续」按钮并
        # 落库），工作流不再计划交接/自动送审；stop_reason 落库供 router 识别「继续」。
        turns_exhausted = max_turns_exhausted or landed_without_control_flow
        # 收尾提醒是请求级模型调用预算触发的（预算比本次 run 的 max_turns 先到）：
        # 同样按耗尽收尾（状态卡 + 「继续」入口），但 stop_reason 记为预算用尽，
        # 卡片里的次数也换成请求级的计数，而不是硬停成 ERROR。
        budget_landed = (
            not max_turns_exhausted
            and landed_without_control_flow
            and turn_filter is not None
            and turn_filter.landing_limit == LANDING_LIMIT_MODEL_CALL_BUDGET
        )
        time_landed = (
            not max_turns_exhausted
            and landed_without_control_flow
            and turn_filter is not None
            and turn_filter.landing_limit == LANDING_LIMIT_WALL_CLOCK
        )
        exhausted_stop_reason = (
            MODEL_CALL_BUDGET_STOP_REASON
            if budget_landed
            else RUN_DEADLINE_STOP_REASON
            if time_landed
            else MAX_TURNS_STOP_REASON
        )
        if turns_exhausted:
            log_with_context(
                logger,
                30,  # WARNING
                "Agent run reached the tool-call turn cap",
                agent_type=agent_type,
                model_turns=model_turns,
                max_turns=AGENT_TOOL_CALL_MAX_ITERATIONS,
                hard_exhausted=max_turns_exhausted,
                soft_landed=bool(turn_filter is not None and turn_filter.landing_injected),
                landing_limit=turn_filter.landing_limit if turn_filter is not None else None,
                model_calls=run_meter.model_calls,
                max_model_calls=run_meter.max_model_calls,
                duplicate_reads=read_guard.duplicate_reads,
                blocked_reads=read_guard.blocked_reads,
            )
            if time_landed:
                exhausted_data: dict[str, Any] = {
                    "layer": "tool_call",
                    "iterations_used": run_meter.model_calls,
                    "max_iterations": run_meter.max_model_calls,
                    "reason": (
                        "这一轮的时间快用完了，AI 先写下了阶段总结。"
                        "任务可能还没做完，回复「继续」就能接着做。"
                    ),
                    "last_agent": agent_type,
                }
            elif budget_landed:
                exhausted_data = {
                    "layer": "tool_call",
                    "iterations_used": run_meter.model_calls,
                    "max_iterations": run_meter.max_model_calls,
                    "reason": (
                        f"这一轮的模型调用次数快到上限（{run_meter.max_model_calls} 次），"
                        "AI 先写下了阶段总结。任务可能还没做完，回复「继续」就能接着做。"
                    ),
                    "last_agent": agent_type,
                }
            else:
                exhausted_data = {
                    "layer": "tool_call",
                    "iterations_used": model_turns or AGENT_TOOL_CALL_MAX_ITERATIONS,
                    "max_iterations": AGENT_TOOL_CALL_MAX_ITERATIONS,
                    "reason": (
                        f"单个 Agent（{agent_type}）的工具调用轮数已达上限"
                        f"（{AGENT_TOOL_CALL_MAX_ITERATIONS} 轮）。"
                        f"这是单个 Agent 内的工具调用限制，与 Agent 协作轮数"
                        f"（{AGENT_COLLABORATION_MAX_ITERATIONS} 轮）独立。"
                        "当前任务可能未完全完成，您可以回复「继续」让 AI 接着完成剩余工作。"
                    ),
                    "last_agent": agent_type,
                }
            # 前端据此选卡片文案：时限收尾时模型可能已经做完，只是收尾提醒让它写了总结。
            exhausted_data["limit"] = (
                LANDING_LIMIT_WALL_CLOCK
                if time_landed
                else LANDING_LIMIT_MODEL_CALL_BUDGET
                if budget_landed
                else "max_turns"
            )
            yield StreamEvent(type=StreamEventType.ITERATION_EXHAUSTED, data=exhausted_data)
        elif clarification_event_data is not None:
            from agent.core.metrics import AGENT_CLARIFICATION_TOTAL, get_metrics_collector

            get_metrics_collector().increment_counter(AGENT_CLARIFICATION_TOTAL)
            yield StreamEvent(type=StreamEventType.WORKFLOW_STOPPED, data=clarification_event_data)
        elif handoff_event_data is not None:
            yield StreamEvent(type=StreamEventType.HANDOFF, data=handoff_event_data)

        usage = _usage_dict_from_result(result)
        run_stop_reason = exhausted_stop_reason if turns_exhausted else "end_turn"
        yield StreamEvent(
            type=StreamEventType.MESSAGE_END,
            data={"stop_reason": run_stop_reason, "usage": usage},
        )

        log_with_context(
            logger,
            20,  # INFO
            "OpenAI Agents streaming run completed",
            agent_type=agent_type,
            model=(run_meter.last_served_model if run_meter is not None else None) or start_model,
            tool_calls=len(tool_uses),
            response_length=sum(len(part) for part in assistant_text_parts),
            model_turns=len(getattr(result, "raw_responses", None) or []),
            stop_reason=run_stop_reason,
            duplicate_reads=read_guard.duplicate_reads,
            blocked_reads=read_guard.blocked_reads,
            duration_ms=round((time.monotonic() - run_started_at) * 1000, 1),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_read_tokens=usage.get("cache_read_tokens", 0),
            continued_previous_run=continued is not None,
        )
        if not turns_exhausted:
            with contextlib.suppress(Exception):
                completed_items = list(result.to_input_list())

    except (asyncio.CancelledError, GeneratorExit):
        timing_outcome = "interrupted"
        raise
    except Exception as exc:
        timing_outcome = "error"
        error_info = classify_stream_exception(exc)
        log_stream_exception(
            logger,
            "OpenAI Agents streaming run failed",
            exc,
            error_info,
            agent_type=agent_type,
            model=(run_meter.last_served_model if run_meter is not None else None) or start_model,
            duration_ms=round((time.monotonic() - run_started_at) * 1000, 1),
        )
        yield StreamEvent(
            type=StreamEventType.ERROR,
            data=error_info.as_event_data(error_type=type(exc).__name__),
        )
    finally:
        # Stop the SDK pump if it is still running (e.g. the consumer aborted
        # early before the stream drained), so it cannot outlive this run.
        if pump_task is not None and not pump_task.done():
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await pump_task
        if run_hooks is not None:
            # A call lacking on_llm_end is unfinished, even if the consumer ended normally.
            run_hooks.finish_timing("error" if timing_outcome == "error" else "interrupted")
        run_meter.record_agent_run(
            (time.monotonic() - run_started_at) * 1000,
            agent_type=agent_type, outcome=timing_outcome,
        )
        _append_assistant_turn_to_state_messages(
            state,
            api_messages,
            assistant_text="".join(assistant_text_parts),
            thinking_text="".join(thinking_text_parts),
            tool_uses=tool_uses,
        )
        if isinstance(native_runs, dict):
            native_runs.clear()
            if completed_items:
                native_runs.update(
                    agent_type=agent_type,
                    items=completed_items,
                    directive=agent_directive,
                    user_message=str(state.get("user_message") or ""),
                    # 之后追加到 state["messages"] 的（运行中插话）下一轮要补进输入
                    messages_len=len(state.get("messages") or []),
                )
        if mid_run_steering:
            # 工具边界消费的 steering 作为用户消息进入后续迭代的输入
            # （本次 SDK run 已经无法注入）。
            state["messages"] = list(state.get("messages") or []) + [
                {"role": "user", "content": content} for content in mid_run_steering
            ]
