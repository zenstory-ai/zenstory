"""Convert ZenStory MCP-style tools into OpenAI Agents SDK FunctionTool objects."""

from __future__ import annotations

import inspect
import json
from contextlib import nullcontext
from typing import Any

from agent.core.run_meter import AgentRunMeter
from agent.openai_agents.events import extract_tool_result_text, tool_error_text
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.tool_failure_breaker import (
    ToolFailureBreaker,
    _parse_error_payload,
)
from agent.tools.mcp_tools import ToolContext
from agent.tools.registry import TOOL_FUNCTIONS, get_exposed_tools
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


def _is_partial_result(text: str) -> bool:
    """status=partial: some edits applied, others failed (edit_file with continue_on_error)."""
    if '"partial"' not in text:  # cheap pre-check before parsing a possibly long result
        return False
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "partial"


def _record_business_failure(run_meter: AgentRunMeter, tool_name: str, args: dict[str, Any], text: str) -> None:
    """Count an executed tool's status=error / partial result in the run summary (never affects the result)."""
    try:
        payload = _parse_error_payload(text)
        if payload is not None:
            edits = args.get("edits")
            run_meter.record_tool_failure(
                tool_name,
                payload.get("error_type"),
                # Without continue_on_error one failed edit rolls the whole batch back.
                rolled_back_batch=isinstance(edits, list) and len(edits) > 1 and args.get("continue_on_error") is not True,
            )
        elif _is_partial_result(text):
            run_meter.record_tool_failure(tool_name, "partial", rolled_back_batch=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Tool failure count for the run summary failed: {type(exc).__name__}")


async def invoke_project_tool(
    tool_name: str,
    raw_arguments: str,
    *,
    run_meter: AgentRunMeter | None = None,
) -> str:
    """Invoke a project tool from an SDK FunctionTool callback."""
    parsed_args, parse_error_text = _parse_tool_arguments(raw_arguments, tool_name=tool_name)
    if parse_error_text is not None:
        return parse_error_text

    tool_func = TOOL_FUNCTIONS.get(tool_name)
    if tool_func is None:
        return tool_error_text(f"Unknown tool: {tool_name}", tool_name=tool_name)

    try:
        if run_meter is not None:
            run_meter.mark_first_tool_started()
        timing = run_meter.measure_stage("tool") if run_meter is not None else nullcontext()
        with timing:
            result = tool_func(parsed_args or {})
            if inspect.isawaitable(result):
                result = await result
        text = _normalize_tool_result_text(result)
        if run_meter is not None:
            _record_business_failure(run_meter, tool_name, parsed_args or {}, text)
        return text
    except Exception as exc:
        log_with_context(
            logger,
            40,  # ERROR
            "OpenAI Agents tool execution error",
            tool_name=tool_name,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return tool_error_text(str(exc), tool_name=tool_name)


# 两次回复协议：上一次回复建好的文件，这一次回复必须先以 <file> 开头写正文。没写正文就调用的工具
# 一律不执行（非递归删掉本请求新建的这份待写文件除外，见 _cancels_overdue_file）；
# 先写正文再在同一条回复里建下一个文件照常允许。


def _cancels_overdue_file(raw_arguments: str, overdue_ids: set[str]) -> str | None:
    """delete_file 的参数正好是「取消一份待写文件」（精确 id、非递归）时返回这个 id，否则 None。

    与执行路径用同一个参数解析和同一个 recursive 判定，放行的就是将要执行的那次删除。
    参数解析不了、没有 id、别的文件、递归删除（含删父目录）都不算。
    """
    from agent.constants import coerce_bool

    parsed, parse_error = _parse_tool_arguments(raw_arguments, tool_name="delete_file")
    if parse_error is not None or not isinstance(parsed, dict):
        return None
    file_id = parsed.get("id")
    if isinstance(file_id, str) and file_id in overdue_ids and not coerce_bool(parsed.get("recursive")):
        return file_id
    return None


def _deleted(output: str) -> bool:
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "success" and payload.get("mutation_applied") is True


def file_body_pending_text(tool_name: str, title: str) -> str:
    return tool_error_text(
        f"「{title}」已经建好但还没写正文，{tool_name} 未执行。这一次回复先以 <file> 开头写完它的正文、"
        "用 </file> 结束，然后再调用其他工具；不要用 edit_file 分段补写正文。",
        error_type="file_body_pending",
        tool_name=tool_name,
    )


def build_agent_function_tools(
    *,
    failure_breaker: ToolFailureBreaker | None = None,
    read_guard: RepeatReadGuard | None = None,
    run_meter: AgentRunMeter | None = None,
) -> list[Any]:
    """Build SDK FunctionTool instances for the loop agent's toolset.

    failure_breaker：本次请求的工具失败熔断器。每个工具结果都经它记账；
    熔断后同一轮里剩下的调用不再执行（尤其是写工具），直接返回熔断说明。
    read_guard：本次请求的重复读取守卫。query_files(id) 执行前经它判定（同一文件第 4 次起不执行），执行后经它记账、附提示；
    写工具成功会清零对应文件的读取计数。守卫判定无进展后，同一轮里剩下的调用
    同样不再执行。
    run_meter：请求级耗时记录器。只有熔断、待补正文与重复读取守卫都放行后，
    才记录首次工具开始并计量实际工具调用耗时；返回成功只表示调用结束，不代表业务写入成功。
    """
    from agents import FunctionTool

    function_tools: list[Any] = []
    # 每一轮都给同一份工具清单（顺序与描述都不变）：工具清单在请求里排在对话前面，
    # 轮换清单会让整段前缀缓存失效。
    for tool_schema in get_exposed_tools():
        name = str(tool_schema.get("name") or "").strip()
        if not name:
            continue
        description = str(tool_schema.get("description") or "")

        async def _on_invoke_tool(
            _ctx: Any,
            raw_arguments: str,
            *,
            _tool_name: str = name,
        ) -> str:
            async def invoke_allowed(arguments: str) -> str:
                if run_meter is None:
                    return await invoke_project_tool(_tool_name, arguments)
                return await invoke_project_tool(
                    _tool_name,
                    arguments,
                    run_meter=run_meter,
                )

            if failure_breaker is not None and failure_breaker.is_open:
                return failure_breaker.short_circuit_text(_tool_name)
            if read_guard is not None and read_guard.is_open:
                return read_guard.short_circuit_text(_tool_name)
            overdue = ToolContext.overdue_file_bodies()
            cancelled_id = (
                # 复用的已有剧集可能已有正文，不算「取消本请求建的空文件」。
                _cancels_overdue_file(raw_arguments, {entry["id"] for entry in overdue if entry["created_here"]})
                if overdue and _tool_name == "delete_file"
                else None
            )
            if cancelled_id is not None:
                overdue = []
            if overdue:
                output = file_body_pending_text(_tool_name, overdue[0]["title"])
                # 工具没有执行：只在运行摘要里记一次固定分类，不记首个工具开始、不算回滚批次。
                if run_meter is not None:
                    run_meter.record_tool_failure(_tool_name, "file_body_pending", rolled_back_batch=False)
            elif read_guard is not None:
                plan = read_guard.plan(_tool_name, raw_arguments)
                if plan.blocked_output is not None:
                    output = plan.blocked_output
                else:
                    output = await invoke_allowed(plan.arguments)
                    output = read_guard.observe(_tool_name, plan, output)
            else:
                output = await invoke_allowed(raw_arguments)
            if cancelled_id is not None and _deleted(output):
                # 待写文件确实删掉了：守卫随这次删除结果收敛（StreamAdapter 按同一结果解除捕获），
                # 模型在本 run 里接着做别的不会再被它拒绝；run 边界的「已删除」核验照旧兜底。
                ToolContext.clear_pending_empty_file(cancelled_id)
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
