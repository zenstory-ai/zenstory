"""Convert ZenStory MCP-style tools into OpenAI Agents SDK FunctionTool objects."""

from __future__ import annotations

import functools
import inspect
import json
from collections.abc import Callable
from contextlib import nullcontext
from typing import Any

from agent.core.run_meter import AgentRunMeter
from agent.openai_agents.events import extract_tool_result_text, tool_error_text
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.tool_failure_breaker import (
    CLARIFY_FIRST_ERROR_TYPE,
    READ_ONLY_REQUEST_ERROR_TYPE,
    ROLE_NOT_PERMITTED_ERROR_TYPE,
    ToolFailureBreaker,
    _parse_error_payload,
)
from agent.tools.mcp_tools import ToolContext
from agent.tools.registry import FILE_WRITE_TOOL_NAMES, TOOL_FUNCTIONS, get_agent_tools, get_exposed_tools
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
        payload = _parse_error_payload(text, tool_name)
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


_WRITE_TOOLS_TEXT = "create_file / edit_file / delete_file / rename_file / parallel_execute"

# 写文件工具本轮为什么不可用：写进这次运行的系统指令（最后一条消息）。不改工具描述：
# 工具清单在请求里排在对话前面，按轮次变化会让整段前缀缓存失效。
READ_ONLY_TOOLS_NOTE = f"本轮作者要求只回答、不修改文件：{_WRITE_TOOLS_TEXT} 调用会被拒绝，直接在对话里回答。"


def read_only_refusal_text(tool_name: str) -> str:
    """只读请求下写文件工具的拒绝结果：可恢复错误，交还模型改为直接回答。"""
    return tool_error_text(
        "用户本轮明确要求不修改任何文件（只回答/只分析），该写操作未执行。"
        "不要重试任何写文件工具，请直接在对话里给出回答或分析。",
        error_type=READ_ONLY_REQUEST_ERROR_TYPE,
        tool_name=tool_name,
    )


# 要求太笼统、本轮先问清楚（graph/author_scope.py 的 clarify_first）。
CLARIFY_FIRST_TOOLS_NOTE = f"作者的要求还不清楚，本轮先问清楚再改：{_WRITE_TOOLS_TEXT} 调用会被拒绝。"
# 审稿人看到的工具清单与其他角色相同（前缀缓存），写文件工具只在调用时拒绝。
REVIEWER_TOOLS_NOTE = (
    f"你是审稿人，只读：{_WRITE_TOOLS_TEXT} 在你的角色里不可用，调用会被拒绝；"
    "需要改稿时用 handoff_to_agent 交给 writer。"
)


def role_refusal_text(tool_name: str, agent_type: str) -> str:
    """角色没有这个工具的权限：可恢复错误。"""
    return tool_error_text(
        f"{agent_type} 角色不能调用 {tool_name}，该操作未执行。不要重试；需要改文件时交给有写权限的角色。",
        error_type=ROLE_NOT_PERMITTED_ERROR_TYPE,
        tool_name=tool_name,
    )


# 两次回复协议：上一次回复建好的文件，这一次回复必须先以 <file> 开头写正文。没写正文就调用的工具
# 一律不执行（请作者澄清、非递归删掉本请求新建的这份待写文件除外，见 _cancels_overdue_file）；
# 先写正文再在同一条回复里建下一个文件照常允许。
FILE_BODY_PENDING_ALLOWED_TOOLS = frozenset({"request_clarification"})


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


def tool_availability_note(agent_type: str, *, read_only: bool, read_only_reason: str) -> str:
    """这次运行哪些工具调用会被拒绝（放进系统指令，不改工具清单）。"""
    notes = []
    if agent_type == "quality_reviewer":
        notes.append(REVIEWER_TOOLS_NOTE)
    elif read_only:
        notes.append(
            CLARIFY_FIRST_TOOLS_NOTE if read_only_reason == READ_ONLY_REASON_CLARIFY_FIRST else READ_ONLY_TOOLS_NOTE
        )
    return "\n".join(notes)


def clarify_first_refusal_text(tool_name: str) -> str:
    """要求太笼统时写文件工具的拒绝结果：先用一句话问作者改哪里、想要什么效果。"""
    return tool_error_text(
        "作者这次的要求没说改哪部分、想达到什么效果，本轮先不改文件，该写操作未执行。"
        "不要重试任何写文件工具：用一句话问作者想改哪部分、想要什么效果，"
        "给 2–3 个具体选项让作者直接回复。",
        error_type=CLARIFY_FIRST_ERROR_TYPE,
        tool_name=tool_name,
    )


# build_agent_function_tools 的 read_only_reason：为什么本轮写文件工具不可用。
READ_ONLY_REASON_USER_REQUEST = "user_request"
READ_ONLY_REASON_CLARIFY_FIRST = "clarify_first"


def build_agent_function_tools(
    agent_type: str,
    *,
    failure_breaker: ToolFailureBreaker | None = None,
    read_only: bool = False,
    read_guard: RepeatReadGuard | None = None,
    read_only_reason: str = READ_ONLY_REASON_USER_REQUEST,
    run_meter: AgentRunMeter | None = None,
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
    read_only_reason：写工具为什么不可用。user_request（默认）是作者明说不改文件；
    clarify_first 是作者的要求太笼统，本轮先问清楚（拒绝说明和描述前缀不同）。
    run_meter：请求级耗时记录器。只有权限、只读、待补正文与重复读取守卫都放行后，
    才记录首次工具开始并计量实际工具调用耗时；返回成功只表示调用结束，不代表业务写入成功。
    """
    from agents import FunctionTool

    clarify_first = read_only_reason == READ_ONLY_REASON_CLARIFY_FIRST
    read_only_refusal = clarify_first_refusal_text if clarify_first else read_only_refusal_text
    permitted = {str(schema.get("name") or "") for schema in get_agent_tools(agent_type)}

    function_tools: list[Any] = []
    # 每个角色、每一轮都给同一份工具清单（顺序与描述都不变）：工具清单在请求里排在对话前面，
    # 按角色或只读轮换清单会让整段前缀缓存失效。权限在调用时检查。
    for tool_schema in get_exposed_tools():
        name = str(tool_schema.get("name") or "").strip()
        if not name:
            continue
        refusal: Callable[[str], str] | None = None
        if name not in permitted:
            refusal = functools.partial(role_refusal_text, agent_type=agent_type)
        elif read_only and name in FILE_WRITE_TOOL_NAMES:
            refusal = read_only_refusal
        description = str(tool_schema.get("description") or "")

        async def _on_invoke_tool(
            _ctx: Any,
            raw_arguments: str,
            *,
            _tool_name: str = name,
            _refusal: Callable[[str], str] | None = refusal,
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
            overdue = ToolContext.overdue_file_bodies() if _tool_name not in FILE_BODY_PENDING_ALLOWED_TOOLS else []
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
            elif _refusal is not None:
                output = _refusal(_tool_name)
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
                # 模型在本 run 里接着做别的不会再被它拒绝；图在 run 边界的「已删除」核验照旧兜底。
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
