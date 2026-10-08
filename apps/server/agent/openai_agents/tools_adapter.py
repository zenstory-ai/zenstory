"""Convert ZenStory MCP-style tools into OpenAI Agents SDK FunctionTool objects."""

from __future__ import annotations

import inspect
import json
from typing import Any

from agent.core.metrics import (
    TOOL_CALLS_DURATION_MS,
    TOOL_CALLS_ERRORS,
    TOOL_CALLS_TOTAL,
    get_metrics_collector,
)
from agent.openai_agents.events import extract_tool_result_text, tool_error_text
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker
from agent.tools.registry import FILE_WRITE_TOOL_NAMES, TOOL_FUNCTIONS, get_agent_tools
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


def _extract_params_schema(tool_schema: dict[str, Any]) -> dict[str, Any]:
    schema = tool_schema.get("input_schema")
    if isinstance(schema, dict):
        return schema
    return {"type": "object", "properties": {}, "additionalProperties": True}


def _normalize_tool_result_text(result: Any) -> str:
    """Return the text sent back to the model for a project tool result."""
    text = extract_tool_result_text(result)
    if text:
        return text

    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False)
    return str(result)


def _parse_tool_arguments(raw_arguments: str, *, tool_name: str) -> tuple[dict[str, Any] | None, str | None]:
    from agent.openai_agents.events import parse_json_object

    parsed, parse_error, parse_metadata = parse_json_object(raw_arguments, tool_name=tool_name)
    if parse_error is None:
        if parse_metadata.get("strategy") == "json_repair":
            log_with_context(
                logger,
                20,  # INFO
                "OpenAI Agents tool input JSON repaired",
                tool_name=tool_name,
                repair_actions=parse_metadata.get("repair_actions"),
            )
        return parsed, None

    log_with_context(
        logger,
        30,  # WARNING
        "Invalid OpenAI Agents tool input JSON",
        tool_name=tool_name,
        error=parse_error,
        repair_strategy=parse_metadata.get("strategy"),
        repair_error=parse_metadata.get("repair_error"),
    )
    return None, tool_error_text(
        "Invalid tool input JSON",
        error_type="invalid_tool_input_json",
        tool_name=tool_name,
    )


async def invoke_project_tool(tool_name: str, raw_arguments: str) -> str:
    """Invoke a project tool from an SDK FunctionTool callback."""
    metrics = get_metrics_collector()
    metrics.increment_counter(TOOL_CALLS_TOTAL)

    with metrics.time_histogram(TOOL_CALLS_DURATION_MS):
        parsed_args, parse_error_text = _parse_tool_arguments(raw_arguments, tool_name=tool_name)
        if parse_error_text is not None:
            metrics.increment_counter(TOOL_CALLS_ERRORS)
            return parse_error_text

        tool_func = TOOL_FUNCTIONS.get(tool_name)
        if tool_func is None:
            metrics.increment_counter(TOOL_CALLS_ERRORS)
            return tool_error_text(f"Unknown tool: {tool_name}", tool_name=tool_name)

        try:
            result = tool_func(parsed_args or {})
            if inspect.isawaitable(result):
                result = await result
            return _normalize_tool_result_text(result)
        except Exception as exc:
            metrics.increment_counter(TOOL_CALLS_ERRORS)
            log_with_context(
                logger,
                40,  # ERROR
                "OpenAI Agents tool execution error",
                tool_name=tool_name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return tool_error_text(str(exc), tool_name=tool_name)


# 只读请求下写文件工具的描述前缀：模型在工具清单里就能看到本轮不能写。
READ_ONLY_TOOL_DESCRIPTION_PREFIX = "【本轮不可用：用户要求只回答、不修改文件，调用会被拒绝】"


def read_only_refusal_text(tool_name: str) -> str:
    """只读请求下写文件工具的拒绝结果：可恢复错误，交还模型改为直接回答。"""
    return tool_error_text(
        "用户本轮明确要求不修改任何文件（只回答/只分析），该写操作未执行。"
        "不要重试任何写文件工具，请直接在对话里给出回答或分析。",
        error_type="read_only_request",
        tool_name=tool_name,
    )


def build_agent_function_tools(
    agent_type: str,
    *,
    failure_breaker: ToolFailureBreaker | None = None,
    read_only: bool = False,
    read_guard: RepeatReadGuard | None = None,
) -> list[Any]:
    """Build SDK FunctionTool instances for the given writing agent role.

    failure_breaker：本次请求的工具失败熔断器。每个工具结果都经它记账；
    熔断后同一轮里剩下的调用不再执行（尤其是写工具），直接返回熔断说明。
    read_only：用户明确要求本轮不改文件。写文件工具（FILE_WRITE_TOOL_NAMES）
    仍留在工具集里，但调用一律不执行、返回 error_type=read_only_request 的
    可恢复错误（见 read_only_refusal_text）。不直接从工具集里拿掉：writer/planner
    的提示词和会话历史都在用 create_file，模型照样会调；SDK 对「不存在的工具」
    默认抛 ModelBehaviorError，整轮以致命 ERROR 结束。留着工具、拒绝执行，模型
    拿到的是能看懂的拒绝说明，拒绝也经熔断器记账，乱试有上限。
    read_guard：本次请求的重复读取守卫。query_files(id) 与 parallel_execute 的读取
    子任务执行前经它判定（同一文件第 4 次起不执行），执行后经它记账、附提示；
    写工具成功会清零对应文件的读取计数。守卫判定无进展后，同一轮里剩下的调用
    同样不再执行。
    """
    from agents import FunctionTool

    function_tools: list[Any] = []
    for tool_schema in get_agent_tools(agent_type):
        name = str(tool_schema.get("name") or "").strip()
        if not name:
            continue
        refuse_as_read_only = read_only and name in FILE_WRITE_TOOL_NAMES
        description = str(tool_schema.get("description") or "")
        if refuse_as_read_only:
            description = f"{READ_ONLY_TOOL_DESCRIPTION_PREFIX}{description}"

        async def _on_invoke_tool(
            _ctx: Any,
            raw_arguments: str,
            *,
            _tool_name: str = name,
            _refuse: bool = refuse_as_read_only,
        ) -> str:
            if failure_breaker is not None and failure_breaker.is_open:
                return failure_breaker.short_circuit_text(_tool_name)
            if read_guard is not None and read_guard.is_open:
                return read_guard.short_circuit_text(_tool_name)
            if _refuse:
                output = read_only_refusal_text(_tool_name)
            elif read_guard is not None:
                plan = read_guard.plan(_tool_name, raw_arguments)
                if plan.blocked_output is not None:
                    output = plan.blocked_output
                else:
                    output = await invoke_project_tool(_tool_name, plan.arguments)
                    output = read_guard.observe(_tool_name, plan, output)
            else:
                output = await invoke_project_tool(_tool_name, raw_arguments)
            if failure_breaker is not None:
                output = failure_breaker.observe(_tool_name, raw_arguments, output)
            return output

        function_tools.append(
            FunctionTool(
                name=name,
                description=description,
                params_json_schema=_extract_params_schema(tool_schema),
                on_invoke_tool=_on_invoke_tool,
                strict_json_schema=False,
            )
        )

    return function_tools
