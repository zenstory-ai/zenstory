"""Convert ZenStory MCP-style tools into OpenAI Agents SDK FunctionTool objects."""

from __future__ import annotations

import inspect
import json
from contextlib import nullcontext
from typing import Any

from agent.core.run_meter import AgentRunMeter
from agent.openai_agents.events import extract_tool_result_text, tool_error_text
from agent.openai_agents.repeat_edit_guard import RepeatEditGuard
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.tool_failure_breaker import (
    ToolFailureBreaker,
    _parse_error_payload,
)
from agent.tools.registry import TOOL_FUNCTIONS, get_exposed_tools
from agent.tools.tool_context import AgentRequestContext, unwrap_request_context
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
        return json.dumps(result, ensure_ascii=False, separators=(",", ":"))
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


def _is_rolled_back_edit_batch(tool_name: str, args: dict[str, Any]) -> bool:
    """Return True when a multi-edit call failed with effective continue_on_error=False."""
    raw_edits = args.get("edits") if isinstance(args.get("edits"), list) else args.get("operations")
    if not isinstance(raw_edits, list) or len(raw_edits) <= 1:
        return False
    if tool_name == "edit_file":
        try:
            from agent.tools.tool_inputs import EditFileInput

            validated = EditFileInput.model_validate(args)
            return len(validated.edits) > 1 and not validated.continue_on_error
        except Exception:  # noqa: BLE001
            pass
    from agent.constants import coerce_bool

    raw_continue = args.get("continue_on_error")
    if raw_continue is None:
        return tool_name != "edit_file"
    return not coerce_bool(raw_continue, default=False)


def _record_pending_empty_file_type(
    request_context: AgentRequestContext,
    tool_name: str,
    text: str,
    args: dict[str, Any],
) -> None:
    """Stamp file_type onto a newly bound pending empty-file entry from create_file / write_chapter output."""
    if tool_name not in {"create_file", "write_chapter"} or not request_context.has_pending_empty_file():
        return
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return
    data = payload.get("data")
    if not isinstance(data, dict):
        return
    file_id = str(data.get("id") or "").strip()
    if not file_id:
        return
    raw_type = data.get("file_type")
    if not isinstance(raw_type, str) or not raw_type.strip():
        raw_type = args.get("file_type")
    file_type = raw_type.strip().lower() if isinstance(raw_type, str) and raw_type.strip() else (
        "draft" if tool_name == "write_chapter" else ""
    )
    if file_type:
        request_context.set_pending_empty_file_type(file_id, file_type)


def _record_business_failure(run_meter: AgentRunMeter, tool_name: str, args: dict[str, Any], text: str) -> None:
    """Count an executed tool's status=error / partial result in the run summary (never affects the result)."""
    try:
        payload = _parse_error_payload(text)
        if payload is not None:
            run_meter.record_tool_failure(
                tool_name,
                payload.get("error_type"),
                # Without continue_on_error one failed edit rolls the whole batch back.
                rolled_back_batch=_is_rolled_back_edit_batch(tool_name, args),
            )
        elif _is_partial_result(text):
            run_meter.record_tool_failure(tool_name, "partial", rolled_back_batch=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"Tool failure count for the run summary failed: {type(exc).__name__}")


def _call_tool_handler(
    tool_func: Any,
    args: dict[str, Any],
    *,
    request_context: AgentRequestContext | None = None,
    tool_call_id: str | None = None,
) -> Any:
    """Invoke a registered tool handler, forwarding typed context kwargs when accepted."""
    try:
        params = inspect.signature(tool_func).parameters
    except (TypeError, ValueError):
        params = {}
    has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
    kwargs: dict[str, Any] = {}
    if request_context is not None and (has_var_kw or "request_context" in params):
        kwargs["request_context"] = request_context
    if tool_call_id is not None and (has_var_kw or "tool_call_id" in params):
        kwargs["tool_call_id"] = tool_call_id
    return tool_func(args, **kwargs)


async def invoke_project_tool(
    tool_name: str,
    raw_arguments: str,
    *,
    run_meter: AgentRunMeter | None = None,
    tool_call_id: str | None = None,
    request_context: AgentRequestContext | None = None,
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
        call_scope = (
            request_context.tool_call_scope(tool_call_id)
            if request_context is not None
            else nullcontext()
        )
        with timing, call_scope:
            result = _call_tool_handler(
                tool_func,
                parsed_args or {},
                request_context=request_context,
                tool_call_id=tool_call_id,
            )
            if inspect.isawaitable(result):
                result = await result
        text = _normalize_tool_result_text(result)
        if request_context is not None:
            _record_pending_empty_file_type(request_context, tool_name, text, parsed_args or {})
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


# 两次回复协议：上一次回复建好的文件，这一次回复必须先以 <file> 开头写正文。
# 只读/元数据工具与不占用 <file> 槽位的 create_file（folder / empty=True / source_text）以及
# 针对同一待写目标的重复 write_chapter / create_file 允许继续执行（附带简洁提醒或走工具既有幂等吸收，
# 不触发 file_body_pending / runner 提前结束）；真正冲突的写操作（edit_file / delete_file[除精确取消] /
# rename_file / 另一章 write_chapter / 新的待写 create_file）照旧以 file_body_pending 硬挡回。
_OVERDUE_NON_CONFLICTING_TOOLS: frozenset[str] = frozenset({
    "query_files",
    "update_project",
    "load_skill",
    "read_skill_resource",
})


def _is_non_slotted_create_file(raw_arguments: str) -> bool:
    """create_file 不占用 <file> 正文槽位（folder / empty=True / 非空 source_text）时返回 True。"""
    from agent.constants import coerce_bool

    parsed, parse_error = _parse_tool_arguments(raw_arguments, tool_name="create_file")
    if parse_error is not None or not isinstance(parsed, dict):
        return False
    file_type = parsed.get("file_type")
    if isinstance(file_type, str) and file_type.strip().lower() == "folder":
        return True
    if coerce_bool(parsed.get("empty"), default=False):
        return True
    source_text = parsed.get("source_text")
    return isinstance(source_text, str) and bool(source_text.strip())


def _is_overdue_non_conflicting_tool(tool_name: str, raw_arguments: str) -> bool:
    """严格不与待写 <file> 槽位冲突的只读/元数据调用。"""
    if tool_name in _OVERDUE_NON_CONFLICTING_TOOLS:
        return True
    if tool_name == "create_file":
        return _is_non_slotted_create_file(raw_arguments)
    return False


_MAX_ABSORBED_PENDING_WRITER_REPEATS = 1


def _match_same_target_pending_writer_repeat(
    tool_name: str,
    raw_arguments: str,
    overdue: list[dict[str, Any]],
    req_ctx: AgentRequestContext,
) -> str | None:
    """若 write_chapter / create_file 指向已在等待正文的同一目标（且 file_type 兼容），返回其 file_id。"""
    if tool_name == "create_file":
        parsed, parse_error = _parse_tool_arguments(raw_arguments, tool_name="create_file")
        if parse_error is not None or not isinstance(parsed, dict):
            return None
        raw_title = parsed.get("title")
        title = raw_title.strip() if isinstance(raw_title, str) else ""
        if not title:
            return None
        raw_type = parsed.get("file_type")
        req_type = (
            raw_type.strip().lower()
            if isinstance(raw_type, str) and raw_type.strip()
            else "draft"
        )
        for entry in overdue:
            entry_id = str(entry.get("id") or "")
            if not entry_id or str(entry.get("title") or "").strip() != title:
                continue
            entry_type = str(entry.get("file_type") or "").strip().lower()
            if (
                entry_type
                and req_type != entry_type
                and not (entry_type == "script" and req_type in {"draft", "document"})
            ):
                continue
            return entry_id
        return None

    if tool_name == "write_chapter":
        from agent.tools.tool_inputs import WriteChapterInput, validate_tool_input
        from utils.title_sequence import extract_sequence_number, prefixed_chapter_heading

        parsed, parse_error = _parse_tool_arguments(raw_arguments, tool_name="write_chapter")
        if parse_error is not None or not isinstance(parsed, dict):
            return None
        validated, validation_error = validate_tool_input(
            WriteChapterInput, parsed, tool_name="write_chapter"
        )
        if validation_error is not None or validated is None:
            return None
        if bool(validated.empty):
            return None

        unit = "章" if validated.file_type == "draft" else "集"
        for entry in overdue:
            entry_id = str(entry.get("id") or "")
            if not entry_id:
                continue
            entry_type = str(entry.get("file_type") or "").strip().lower()
            if entry_type and entry_type != validated.file_type:
                continue
            armed = req_ctx.pending_chapter_replacement(entry_id)
            if isinstance(armed, dict):
                armed_chapter = armed.get("chapter")
                armed_type = str(armed.get("file_type") or "").strip().lower()
                if armed_chapter == validated.chapter and (not armed_type or armed_type == validated.file_type):
                    return entry_id
            entry_title = str(entry.get("title") or "").strip()
            if entry_title:
                entry_seq = extract_sequence_number(entry_title, unit=unit)
                if entry_seq is None:
                    prefixed = prefixed_chapter_heading(entry_title, unit)
                    entry_seq = prefixed[0] if prefixed is not None else None
                if entry_seq is not None and entry_seq == validated.chapter:
                    return entry_id
                if validated.title and entry_title == validated.title:
                    return entry_id
        return None

    return None


def _is_same_target_pending_writer_repeat(
    tool_name: str,
    raw_arguments: str,
    overdue: list[dict[str, Any]],
    req_ctx: AgentRequestContext,
) -> bool:
    """判断 write_chapter / create_file 是否指向当前已在等待正文的同一个目标，交由工具层既有吸收逻辑处理。"""
    return _match_same_target_pending_writer_repeat(tool_name, raw_arguments, overdue, req_ctx) is not None


def _attach_pending_file_reminder(output: str, title: str) -> str:
    """在放行的非冲突工具结果上附带一行待写 <file> 提醒，不改变 status，不触发 runner 提前结束。"""
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return output
    if not isinstance(payload, dict):
        return output
    if "pending_file_reminder" in payload:
        return output
    # 同目标重复 write_chapter / create_file 已自带 awaiting_body + next_step，不重复拼接提醒。
    data = payload.get("data")
    if isinstance(data, dict) and data.get("awaiting_body") is True:
        return output
    reminded = dict(payload)
    reminded["pending_file_reminder"] = (
        f"「{title}」仍在等待正文，完成本步后请以 <file> 开头写完它的正文并用 </file> 结束。"
    )
    return json.dumps(reminded, ensure_ascii=False, separators=(",", ":"))


def _cancels_overdue_file(raw_arguments: str, overdue_ids: set[str]) -> str | None:
    """delete_file 的参数正好是「取消一份待写文件」（精确 id、非递归）时返回这个 id，否则 None。

    与执行路径共用 ``DeleteFileInput`` 校验（含 ``id`` 别名与 ``recursive`` 布尔归一），
    放行的就是将要执行的那次删除。参数解析不了、没有 id、别的文件、递归删除（含删父目录）都不算。
    """
    from agent.tools.tool_inputs import DeleteFileInput, validate_tool_input

    parsed, parse_error = _parse_tool_arguments(raw_arguments, tool_name="delete_file")
    if parse_error is not None or not isinstance(parsed, dict):
        return None
    validated, validation_error = validate_tool_input(DeleteFileInput, parsed, tool_name="delete_file")
    if validation_error is not None or validated is None:
        return None
    if validated.id in overdue_ids and not validated.recursive:
        return validated.id
    return None


def _deleted(output: str) -> bool:
    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "success" and payload.get("mutation_applied") is True


WALL_CLOCK_WRAP_UP_ERROR_TYPE = "wall_clock_wrap_up"
_WALL_CLOCK_WRAP_UP_USER_MESSAGE = "本轮已进入收尾阶段，这一步留到下一轮继续。"


def file_body_pending_text(tool_name: str, title: str) -> str:
    return tool_error_text(
        f"「{title}」已经建好但还没写正文，{tool_name} 未执行。这一次回复先以 <file> 开头写完它的正文、"
        "用 </file> 结束，然后再调用其他工具；不要用 edit_file 分段补写正文。",
        error_type="file_body_pending",
        tool_name=tool_name,
    )


def wall_clock_wrap_up_text(tool_name: str) -> str:
    return tool_error_text(
        f"本轮已进入时间收尾阶段，{tool_name} 未执行。"
        "请立即停止调用任何工具，思考尽量简短，直接用文字向作者总结已完成和尚未完成的工作。",
        error_type=WALL_CLOCK_WRAP_UP_ERROR_TYPE,
        tool_name=tool_name,
        user_message=_WALL_CLOCK_WRAP_UP_USER_MESSAGE,
    )


def build_agent_function_tools(
    *,
    failure_breaker: ToolFailureBreaker | None = None,
    read_guard: RepeatReadGuard | None = None,
    edit_guard: RepeatEditGuard | None = None,
    run_meter: AgentRunMeter | None = None,
) -> list[Any]:
    """Build SDK FunctionTool instances for the loop agent's toolset.

    failure_breaker：本次请求的工具失败熔断器。每个工具结果都经它记账；
    熔断后同一轮里剩下的调用不再执行（尤其是写工具），直接返回熔断说明。
    read_guard：本次请求的重复读取守卫。query_files(id) 执行前经它判定（同一文件第 4 次起不执行），执行后经它记账、附提示；
    写工具成功会清零对应文件的读取计数。守卫判定无进展后，同一轮里剩下的调用
    同样不再执行。
    edit_guard：本次请求的串行微调收敛守卫（兼刚写章节自读拦截）。在 edit_file / query_files
    执行前按结构化计数与增量判定，不触发致命熔断。
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
            ctx: Any,
            raw_arguments: str,
            *,
            _tool_name: str = name,
        ) -> str:
            req_ctx = unwrap_request_context(ctx)
            raw_call_id = getattr(ctx, "tool_call_id", None)
            call_id = raw_call_id if isinstance(raw_call_id, str) and raw_call_id else None

            async def invoke_allowed(arguments: str) -> str:
                kwargs: dict[str, Any] = {}
                if run_meter is not None:
                    kwargs["run_meter"] = run_meter
                if call_id is not None:
                    kwargs["tool_call_id"] = call_id
                if req_ctx is not None:
                    kwargs["request_context"] = req_ctx
                return await invoke_project_tool(_tool_name, arguments, **kwargs)

            if failure_breaker is not None and failure_breaker.is_open:
                return failure_breaker.short_circuit_text(_tool_name)
            if read_guard is not None and read_guard.is_open:
                return read_guard.short_circuit_text(_tool_name)
            overdue = req_ctx.overdue_file_bodies() if req_ctx is not None else []
            cancelled_id = (
                # 复用的已有剧集可能已有正文，不算「取消本请求建的空文件」。
                _cancels_overdue_file(raw_arguments, {entry["id"] for entry in overdue if entry["created_here"]})
                if overdue and _tool_name == "delete_file"
                else None
            )
            overdue_reminder_title: str | None = None
            wall_clock_wrapping = (
                run_meter is not None
                and getattr(run_meter, "wall_clock_landing_injected", False)
            )
            if cancelled_id is not None:
                overdue = []
            elif overdue and req_ctx is not None and _is_overdue_non_conflicting_tool(_tool_name, raw_arguments):
                overdue_reminder_title = overdue[0]["title"]
                overdue = []
            elif overdue and req_ctx is not None:
                matched_repeat_id = _match_same_target_pending_writer_repeat(
                    _tool_name, raw_arguments, overdue, req_ctx
                )
                if matched_repeat_id is not None and (
                    wall_clock_wrapping
                    or req_ctx.consume_pending_writer_repeat(
                        matched_repeat_id, max_repeats=_MAX_ABSORBED_PENDING_WRITER_REPEATS
                    )
                ):
                    overdue_reminder_title = overdue[0]["title"]
                    overdue = []
            if overdue:
                output = file_body_pending_text(_tool_name, overdue[0]["title"])
                # 工具没有执行：只在运行摘要里记一次固定分类，不记首个工具开始、不算回滚批次。
                if run_meter is not None:
                    run_meter.record_tool_failure(_tool_name, "file_body_pending", rolled_back_batch=False)
            elif (
                run_meter is not None
                and getattr(run_meter, "wall_clock_landing_injected", False)
                and cancelled_id is None
            ):
                # 本次模型调用开始前已注入墙钟收尾提醒，但模型仍继续调用工具：同步拦截不执行，
                # 避免继续耗尽剩余时间窗或在收尾阶段新建空白待写文件，下一跳直接输出阶段总结软着陆。
                output = wall_clock_wrap_up_text(_tool_name)
                run_meter.record_tool_failure(
                    _tool_name, WALL_CLOCK_WRAP_UP_ERROR_TYPE, rolled_back_batch=False
                )
            else:
                edit_plan = edit_guard.plan(_tool_name, raw_arguments) if edit_guard is not None else None
                if edit_plan is not None and edit_plan.blocked_output is not None:
                    output = edit_plan.blocked_output
                elif read_guard is not None:
                    plan = read_guard.plan(_tool_name, raw_arguments)
                    if plan.blocked_output is not None:
                        output = plan.blocked_output
                    else:
                        output = await invoke_allowed(plan.arguments)
                        output = read_guard.observe(_tool_name, plan, output)
                else:
                    output = await invoke_allowed(raw_arguments)
                if edit_guard is not None and edit_plan is not None and edit_plan.blocked_output is None:
                    output = edit_guard.observe(_tool_name, edit_plan, output)
                if overdue_reminder_title is not None and req_ctx is not None and req_ctx.overdue_file_bodies():
                    output = _attach_pending_file_reminder(output, overdue_reminder_title)
            if cancelled_id is not None and _deleted(output) and req_ctx is not None:
                # 待写文件确实删掉了：守卫随这次删除结果收敛（StreamAdapter 按同一结果解除捕获），
                # 模型在本 run 里接着做别的不会再被它拒绝；run 边界的「已删除」核验照旧兜底。
                req_ctx.clear_pending_empty_file(cancelled_id)
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
