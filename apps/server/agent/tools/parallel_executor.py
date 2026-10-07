"""
Parallel execution tool for dispatching concurrent subagent tasks.

Allows the main agent to execute multiple independent tasks in parallel,
then aggregate results.
"""

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from agent.constants import coerce_bool
from utils.logger import get_logger

logger = get_logger(__name__)

MAX_PARALLEL_TASKS = 5
MAX_CONCURRENCY = 2
PARALLEL_TASK_TYPES = (
    "write_chapter",
    "edit_file",
    "delete_file",
    "query_files",
    "hybrid_search",
)


@dataclass
class SubagentTask:
    """A task to be executed by a subagent."""
    id: str
    task_type: str  # One of PARALLEL_TASK_TYPES
    description: str
    parameters: dict[str, Any]
    status: str = "pending"  # pending, running, completed, failed
    result: Any = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass
class ParallelExecutionResult:
    """Result of a parallel execution."""
    execution_id: str
    tasks: list[SubagentTask]
    all_completed: bool
    any_failed: bool
    total_duration_ms: int


# Tool definition
PARALLEL_EXECUTE_TOOL: dict[str, Any] = {
    "name": "parallel_execute",
    "description": """Execute multiple independent tasks in parallel using subagents.

Use this when you need to perform multiple independent operations simultaneously,
such as:
- Writing multiple chapters at once
- Editing multiple files concurrently
- Running multiple queries in parallel

All tasks must be independent (not depend on each other's results).
Maximum 5 parallel tasks per call — extra tasks are NOT executed. Split into
multiple calls instead of sending more than 5.

Task param conventions:
- write_chapter: params = {"title": "第三章", "content": "<full chapter text>", "parent_id": "<folder_id>"}
  - content is REQUIRED and must be inlined here. Parallel tasks cannot use the
    <file>...</file> streaming protocol; a task without content would create an
    empty file that never gets its body. Use a single create_file call instead
    when you want to stream the body.
- edit_file (recommended): params = {"id": "<file_id>", "edits": [...], "continue_on_error": false}
  - Legacy aliases: {"file_id": "..."} for id, {"operations": [...]} for edits
- delete_file: params = {"id": "<file_id>", "recursive": false}
""",
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {
                "type": "array",
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Task type: write_chapter, edit_file, delete_file, query_files, hybrid_search",
                            "enum": list(PARALLEL_TASK_TYPES),
                        },
                        "description": {
                            "type": "string",
                            "description": "Human-readable task description",
                        },
                        "params": {
                            "type": "object",
                            "description": (
                                "Task-specific parameters. "
                                "write_chapter: {title, content (required, inline full text), parent_id}; "
                                "edit_file: {id, edits, continue_on_error}; "
                                "delete_file: {id, recursive}; "
                                "query_files / hybrid_search: same params as the standalone tool."
                            ),
                        },
                    },
                    "required": ["type", "description", "params"],
                },
            },
        },
        "required": ["tasks"],
    },
}


# Per-task result body cap. Keeps the aggregate small enough that the unified
# tool-result guardrail (TOOL_RESULT_MAX_CHARS, default 200k) is not tripped —
# which would otherwise replace the whole `data` object (including any_failed /
# failed / per-task status+error) with a truncation stub and silently swallow
# failures. The full content is persisted and streamed via separate file events.
_MAX_TASK_RESULT_CHARS = 4000


def _bound_task_result(result: Any) -> Any:
    """Cap an individual task's result payload while preserving its shape."""
    if result is None:
        return None
    try:
        text = json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(result)
    if len(text) <= _MAX_TASK_RESULT_CHARS:
        return result
    return {
        "truncated": True,
        "original_length": len(text),
        "preview": text[:_MAX_TASK_RESULT_CHARS],
    }


def _make_result(data: Any) -> dict[str, Any]:
    """Create a tool result in MCP format."""
    return {
        "content": [{
            "type": "text",
            "text": json.dumps(data, ensure_ascii=False)
        }]
    }


def _make_error(error: str) -> dict[str, Any]:
    """Create an error result in MCP format."""
    return {
        "content": [{
            "type": "text",
            "text": json.dumps({"status": "error", "error": error}, ensure_ascii=False)
        }]
    }


def _result_preview(result: Any, max_length: int = 100) -> str | None:
    """Build a short preview of a task result for live progress events."""
    if result is None:
        return None
    try:
        text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    except (TypeError, ValueError):
        text = str(result)
    text = text.strip()
    return text[:max_length] if text else None


def _validate_write_chapter_params(params: dict[str, Any]) -> str | None:
    """校验 write_chapter 任务参数，返回错误信息（None 表示通过）。

    content 必须内联：并行任务的 tool_result 工具名是 "parallel_execute"，
    内层结果被包在 data.tasks[] 里，StreamAdapter 只在 tool_name == "create_file"
    的结果上进入 <file>…</file> 捕获，因此并行分支**永远等不到**流式正文。
    放行一个不带 content 的 write_chapter，等于确定性地产出一个空章节文件，
    而工具还会把它报成 completed。
    """
    content = params.get("content")
    if not isinstance(content, str) or not content.strip():
        title = params.get("title") or "未命名章节"
        return (
            f"write_chapter 任务「{title}」缺少 content：并行任务必须把整章正文内联在 "
            "params.content 里（并行分支不支持 <file>…</file> 流式写入）。"
            "若要流式写入，请改用单独的 create_file 调用。"
        )
    return None


async def handle_write_chapter(params: dict[str, Any]) -> dict[str, Any]:
    """Handle write_chapter task type - creates a draft file."""
    from agent.tools.mcp_tools import ToolContext, create_file

    project_id = ToolContext._get_context().get("project_id")
    if project_id is None:
        return _make_error("project_id not set")

    # 有空文件待补写时先快速失败，给模型一句可执行的提示。
    # 注意：这只是"友好前置检查"，不是守卫本身——真正的守卫是
    # mcp_tools._create_file_sync 里的原子占坑（try_reserve + bind），
    # 因为检查与建库之间隔着 await/线程边界，任何"先查后建"都是 TOCTOU。
    if ToolContext.has_pending_empty_file():
        pending = ToolContext.get_pending_empty_file()
        pending_title = pending.get("title", "unknown") if pending else "unknown"
        return _make_error(
            f"Please complete writing the previous file '{pending_title}' first."
        )

    validation_error = _validate_write_chapter_params(params)
    if validation_error:
        return _make_error(validation_error)

    try:
        result = await create_file({
            "title": params.get("title", "Untitled Chapter"),
            "file_type": "draft",
            "content": params.get("content", ""),
            "parent_id": params.get("parent_id"),
        })
        return result
    except RuntimeError as err:
        if str(err) == "No session available in ToolContext":
            return _make_error("project_id not set")
        raise


async def handle_edit_file(params: dict[str, Any]) -> dict[str, Any]:
    """Handle edit_file task type."""
    from agent.tools.mcp_tools import edit_file

    # Keep backward compatibility with the original parallel_execute contract
    # (file_id/operations) while aligning with the canonical edit_file tool
    # contract (id/edits) used everywhere else.
    file_id_raw = params.get("id") if params.get("id") is not None else params.get("file_id")
    if isinstance(file_id_raw, str):
        file_id = file_id_raw.strip()
    elif file_id_raw is None:
        file_id = ""
    else:
        return _make_error("Invalid edit_file task params: 'id' must be a string.")

    edits = params.get("edits")
    if edits is None:
        edits = params.get("operations", [])

    if not file_id:
        return _make_error(
            "Invalid edit_file task params: missing 'id'. "
            "Provide params.id (recommended) or params.file_id (legacy)."
        )
    if not isinstance(edits, list):
        return _make_error("Invalid edit_file task params: 'edits' must be an array.")

    result = await edit_file({
        "id": file_id,
        "edits": edits,
        # bool("false") is True —— 模型把布尔参数序列化成字符串时，朴素强转会
        # 让"失败即停"变成"失败继续"，且整体仍被报成 success。
        "continue_on_error": coerce_bool(params.get("continue_on_error")),
    })
    return result


async def handle_delete_file(params: dict[str, Any]) -> dict[str, Any]:
    """Handle delete_file task type."""
    from agent.tools.mcp_tools import delete_file

    file_id_raw = params.get("id") if params.get("id") is not None else params.get("file_id")
    if isinstance(file_id_raw, str):
        file_id = file_id_raw.strip()
    elif file_id_raw is None:
        file_id = ""
    else:
        return _make_error("Invalid delete_file task params: 'id' must be a string.")

    if not file_id:
        return _make_error(
            "Invalid delete_file task params: missing 'id'. "
            "Provide params.id (recommended) or params.file_id (legacy)."
        )

    return await delete_file({
        "id": file_id,
        # recursive 判真会软删除整棵子树；bool("false") is True，
        # 必须用 coerce_bool 而不是朴素强转。
        "recursive": coerce_bool(params.get("recursive")),
    })


async def handle_query_files(params: dict[str, Any]) -> dict[str, Any]:
    """Handle query_files task type."""
    from agent.tools.mcp_tools import ToolContext, query_files

    project_id = ToolContext._get_context().get("project_id")
    if project_id is None:
        return _make_error("project_id not set")

    try:
        result = await query_files({
            "project_id": project_id,
            "id": params.get("id"),
            "query": params.get("query"),
            "file_type": params.get("file_type"),
            "file_types": params.get("file_types"),
            "parent_id": params.get("parent_id"),
            "limit": params.get("limit", 50),
            "offset": params.get("offset", 0),
        })
        return result
    except RuntimeError as err:
        if str(err) == "No session available in ToolContext":
            return _make_error("project_id not set")
        raise


async def handle_hybrid_search(params: dict[str, Any]) -> dict[str, Any]:
    """Handle hybrid_search task type."""
    from agent.tools.mcp_tools import ToolContext, hybrid_search

    project_id = ToolContext._get_context().get("project_id")
    if project_id is None:
        return _make_error("project_id not set")

    try:
        result = await hybrid_search({
            "query": params.get("query", ""),
            "top_k": params.get("top_k", 10),
            "entity_types": params.get("entity_types"),
            "min_score": params.get("min_score", 0.0),
        })
        return result
    except RuntimeError as err:
        if str(err) == "No session available in ToolContext":
            return _make_error("project_id not set")
        raise


async def execute_parallel(
    tasks: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Execute multiple tasks in parallel with limited concurrency.

    Args:
        tasks: List of task specifications with type, description, and params

    Returns:
        ParallelExecutionResult as MCP-formatted dict
    """
    from agent.core.events import (
        parallel_end_event,
        parallel_start_event,
        parallel_task_end_event,
        parallel_task_start_event,
    )
    from agent.core.progress_channel import emit_progress
    from agent.tools.mcp_tools import ToolContext

    # Check if there's a pending empty file - parallel execution not allowed
    if ToolContext.has_pending_empty_file():
        pending = ToolContext.get_pending_empty_file()
        pending_title = pending.get("title", "unknown") if pending else "unknown"
        return _make_error(
            f"Cannot execute parallel tasks while file '{pending_title}' is pending. "
            "Please complete the file write first."
        )

    execution_id = f"par-{datetime.now().timestamp()}"
    start_time = datetime.now()

    # Limit tasks to MAX_PARALLEL_TASKS
    requested_count = len(tasks)
    limited_tasks = tasks[:MAX_PARALLEL_TASKS]
    dropped_tasks = [
        {
            "index": index,
            "type": t.get("type", "unknown") if isinstance(t, dict) else "unknown",
            "description": t.get("description", "") if isinstance(t, dict) else "",
        }
        for index, t in enumerate(tasks[MAX_PARALLEL_TASKS:], start=MAX_PARALLEL_TASKS)
    ]
    if dropped_tasks:
        logger.warning(
            f"parallel_execute: Truncated {requested_count} tasks to {MAX_PARALLEL_TASKS}"
        )

    # Create SubagentTask objects
    # Use .get() defensively — LLM may omit optional-ish fields despite
    # the schema marking them required.
    subagent_tasks = [
        SubagentTask(
            id=f"{execution_id}-{i}",
            task_type=t.get("type", "unknown"),
            description=t.get("description", ""),
            parameters=t.get("params", {}),
        )
        for i, t in enumerate(limited_tasks)
    ]

    # Announce parallel execution start so the UI can render live progress.
    emit_progress(
        parallel_start_event(
            execution_id=execution_id,
            task_count=len(subagent_tasks),
            task_descriptions=[t.description for t in subagent_tasks],
            # 截断信息必须随事件下发：否则前端只知道"本轮跑 5 个"，
            # 无从得知模型其实请求了 7 个、有 2 个根本没执行。
            requested_task_count=requested_count,
            dropped_count=len(dropped_tasks),
        )
    )

    # Map task types to handlers
    task_handlers: dict[str, Callable] = {
        "write_chapter": handle_write_chapter,
        "edit_file": handle_edit_file,
        "delete_file": handle_delete_file,
        "query_files": handle_query_files,
        "hybrid_search": handle_hybrid_search,
    }

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def execute_task(task: SubagentTask) -> SubagentTask:
        async with semaphore:
            task.status = "running"
            task.started_at = datetime.now()
            emit_progress(
                parallel_task_start_event(
                    execution_id=execution_id,
                    task_id=task.id,
                    task_type=task.task_type,
                    description=task.description,
                )
            )

            try:
                handler = task_handlers.get(task.task_type)
                if not handler:
                    raise ValueError(f"Unknown task type: {task.task_type}")

                # Give each task its own context so get_session() creates a
                # fresh session instead of sharing the parent ToolContext's
                # session across concurrent threads (SQLAlchemy sessions are
                # not thread-safe).
                #
                # 只清 task_ctx["session"] 是不够的：ToolContext.get_session() 的
                # 查找顺序是 context["session"] → _owned_session_var → create_func，
                # 而 _owned_session_var 是 ContextVar，子任务通过 contextvars 快照
                # 原样继承父上下文里已经懒建好的 Session（handoff 后
                # writing_graph 调 refresh_file_inventory() 就会把它填上）。
                # 于是两个子任务在两个真实 OS 线程上共用同一个 Session：并发
                # flush/commit，且先结束的那个在 finally 里 close 掉它，另一个
                # 还在同一 Session 上跑事务。必须三件事一起做——
                #   1) 隔离 _owned_session_var；
                #   2) 打上 SESSION_ISOLATION_KEY，让 get_session() 即使看到继承来的
                #      自有 session 也不复用（双保险）；
                #   3) finally 里先关掉本任务自建的 session，再 reset token。
                from agent.tools.mcp_tools import (
                    SESSION_ISOLATION_KEY,
                    ToolContext,
                    _owned_session_var,
                    _tool_context_var,
                )

                original_ctx = _tool_context_var.get()
                task_ctx = dict(original_ctx) if isinstance(original_ctx, dict) else {}
                task_ctx["session"] = None  # force get_session() to create a new one
                task_ctx[SESSION_ISOLATION_KEY] = True
                token = _tool_context_var.set(task_ctx)
                owned_token = _owned_session_var.set(None)
                try:
                    result = await handler(task.parameters)
                finally:
                    # 顺序要紧：先在"本任务的 owned 视图"里关闭自建 session，
                    # 再把 ContextVar 恢复成父上下文的值；反过来会把父上下文
                    # 的 session 当成自己的关掉。
                    ToolContext._cleanup_owned_session()
                    _owned_session_var.reset(owned_token)
                    _tool_context_var.reset(token)

                # Extract result text from MCP format
                content_list = result.get("content", [])
                if content_list and content_list[0].get("type") == "text":
                    result_text = content_list[0].get("text", "")
                    try:
                        task.result = json.loads(result_text)
                    except json.JSONDecodeError:
                        task.result = {"raw": result_text}
                else:
                    task.result = result

                if isinstance(task.result, dict) and task.result.get("status") == "error":
                    task.status = "failed"
                    task.error = str(
                        task.result.get("error")
                        or task.result.get("message")
                        or "Task reported error status"
                    )
                else:
                    task.status = "completed"
            except Exception as e:
                task.error = str(e)
                task.status = "failed"
                logger.error(f"parallel_execute task failed: {e}", exc_info=True)
            finally:
                task.completed_at = datetime.now()
                emit_progress(
                    parallel_task_end_event(
                        execution_id=execution_id,
                        task_id=task.id,
                        status=task.status,
                        result_preview=_result_preview(task.result),
                        error=task.error,
                    )
                )

            return task

    # Execute all tasks concurrently with semaphore limiting
    completed_tasks = await asyncio.gather(*[execute_task(t) for t in subagent_tasks])
    # Aggregate raw completed outcomes before bounding task bodies or the outer payload.
    mutation_applied = any(
        task.status == "completed"
        and isinstance(task.result, dict)
        and task.result.get("mutation_applied") is True
        for task in completed_tasks
    )

    end_time = datetime.now()
    duration_ms = int((end_time - start_time).total_seconds() * 1000)

    # Build result summary
    #
    # 被截断丢弃的任务必须是返回体里的**一等字段**：历史实现只写一条
    # logger.warning，payload 里 total_tasks 是截断后的数字、all_completed 仍为
    # true，模型据此向用户回复"7 个草稿已全部删除"，而第 6、7 个根本没执行。
    # 只要有任务被丢弃，all_completed 就恒为 False——工具不许谎报成功。
    result_data = {
        "execution_id": execution_id,
        "requested_tasks": requested_count,
        "max_parallel_tasks": MAX_PARALLEL_TASKS,
        "truncated": bool(dropped_tasks),
        "dropped": len(dropped_tasks),
        "dropped_tasks": dropped_tasks,
        "total_tasks": len(completed_tasks),
        "completed": sum(1 for t in completed_tasks if t.status == "completed"),
        "failed": sum(1 for t in completed_tasks if t.status == "failed"),
        "all_completed": (
            not dropped_tasks
            and all(t.status == "completed" for t in completed_tasks)
        ),
        "any_failed": any(t.status == "failed" for t in completed_tasks),
        "total_duration_ms": duration_ms,
        "tasks": [
            {
                "id": t.id,
                "type": t.task_type,
                "description": t.description,
                "status": t.status,
                # Cap each task's result body so the aggregate stays under the
                # tool-result guardrail (a write_chapter task echoes the full
                # chapter body here). This keeps id/type/description/status/error
                # verbatim, so the failure signal + per-task breakdown always
                # survive even when a task's content is large — the full content
                # is already persisted and streamed via separate file events.
                "result": _bound_task_result(t.result),
                "error": t.error,
            }
            for t in completed_tasks
        ],
    }

    if dropped_tasks:
        # 给模型一句可执行的下一步，否则它只会看到数字对不上却不知道该做什么。
        result_data["warning"] = (
            f"本次只执行了前 {len(completed_tasks)} 个任务，"
            f"第 {MAX_PARALLEL_TASKS + 1}~{requested_count} 个任务未执行"
            f"（单次最多 {MAX_PARALLEL_TASKS} 个）。"
            "请针对 dropped_tasks 里的任务再发起一次 parallel_execute，"
            "在此之前不要向用户声称全部完成。"
        )

    # Announce completion with the aggregate outcome for the UI summary line.
    emit_progress(
        parallel_end_event(
            execution_id=execution_id,
            total_tasks=result_data["total_tasks"],
            completed=result_data["completed"],
            failed=result_data["failed"],
            duration_ms=duration_ms,
            requested_task_count=requested_count,
            dropped_count=len(dropped_tasks),
        )
    )

    logger.info(
        f"parallel_execute completed: {result_data['completed']}/{result_data['total_tasks']} "
        f"tasks in {duration_ms}ms (requested={requested_count}, dropped={len(dropped_tasks)})"
    )

    # Always return a "success" envelope so the per-task breakdown (data.tasks[])
    # survives the stream adapter, which drops `data` for non-success tool
    # results. Partial/total failure is conveyed by data.any_failed / data.failed
    # and per-task status+error, which both the LLM and the UI card read.
    #
    # Route through the shared MCP payload builder so the aggregate is subject to
    # the same TOOL_RESULT_MAX_CHARS guardrail (+ overflow_ref) as every other
    # tool — the concatenated per-task results can otherwise be arbitrarily large.
    from agent.tools.mcp_tools import _make_result as _mcp_make_result

    return _mcp_make_result(
        {"status": "success", "mutation_applied": mutation_applied, "data": result_data},
        tool_name="parallel_execute",
    )
