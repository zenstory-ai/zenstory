"""单循环 agent：一个模型带写作工具集跑完整轮，没有路由、交接、计划或自动审稿。

它接在 ``AgentService.process_stream`` 的同一个生成器接口上（产出 ``core.workflow_events.StreamEvent``），
SSE 外壳、计费 / 退还、停止 / 插话、残稿补存、文件版本并发保护都沿用。一轮里同一个 agent 可能再跑几次
（都从上一次完整的 SDK 输入接着来，见 ``runner._continued_run_input``），只因为结构化事实：

- 空文件纠偏：create_file / write_chapter 建了文件、正文没用 <file> 写成（落库核验），为同一个文件恢复正文写入后
  再跑一次；补不成就回滚能确认的空文件并发 ``empty_file_correction`` 卡。write_chapter 定位到已有非空章节的
  整章替换不算空文件：没写正文时原文不动。
- 文件流续写：这一次 run 建文件或定位章节成功、回复停在写完的 </file> 上（SDK 把它当最终输出），接着跑一次。
- 运行中插话：工具边界或 run 边界消费到作者的插话，接着跑一次让它在本轮生效。

请求墙钟、模型调用预算、工具轮数上限与失控退还都由 runner / run_meter / stream_billing 原样执行；
这里额外的上限是本轮最多跑 ``AGENT_COLLABORATION_MAX_ITERATIONS`` 次。
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from agent.core.author_facing_text import AgentTextShaper, text_contains_cjk
from agent.core.events import UNEXECUTED_TOOL_TEXT_LAYER, WALL_CLOCK_LIMIT
from agent.core.run_meter import AgentRunMeter
from agent.core.stream_errors import classify_stream_exception, log_stream_exception
from agent.core.workflow_events import FileBodyResume, StreamEvent, StreamEventType
from agent.graph import writing_graph as graph_helpers
from agent.graph.completion_evidence import (
    ends_on_file_block,
    ends_on_unclosed_file_block,
    unexecuted_tool_protocol,
)
from agent.graph.state import WritingState
from agent.openai_agents.repeat_read_guard import RepeatReadGuard
from agent.openai_agents.runner import run_openai_agents_streaming_agent
from agent.openai_agents.tool_failure_breaker import ToolFailureBreaker
from agent.tools.mcp_tools import ToolContext
from agent.tools.registry import LOOP_AGENT_TYPE, LOOP_TOOL_NAMES
from config.agent_runtime import AGENT_COLLABORATION_MAX_ITERATIONS
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

_EMPTY_FILE_CORRECTION_CONTEXT = (
    "[系统提醒] 你创建的文件《{title}》(id={file_id}) 正文仍为空：上一次没有用 <file>…</file> 写完它的正文。"
    "系统已为这个文件重新打开正文写入。这一次回复第一行直接输出 <file>，接着写它的完整正文，最后用 </file> 结束；"
    "写完之前不要调用任何工具（write_chapter / create_file / edit_file 都会被拒绝），也不要只在对话里复述正文。"
)
# 调用后下一次回复用 <file> 写正文的工具（新建文件 / 按章号写一章）。
_BODY_WRITE_TOOLS = frozenset({"create_file", "write_chapter"})
_UNEXECUTED_TOOL_TEXT_REASON = "回复里有一步工具调用只写成了文字，没有执行。"
_UNEXECUTED_TOOL_TEXT_REASON_EN = "A tool call in the reply was written out as text and was not executed."


def _english(state: WritingState) -> bool:
    return str(state.get("language") or "").strip().lower().startswith("en")


def _word_count_note(guard: RepeatReadGuard) -> str:
    """续跑说明里附上本请求写过的正文落库后的字数，模型给作者报真实字数。"""
    try:
        counts = graph_helpers._collect_body_word_counts(guard.written_file_ids())
    except Exception as exc:  # noqa: BLE001 - 读不到字数不影响续跑
        logger.debug(f"Word counts for the continuation note failed: {type(exc).__name__}")
        return ""
    return graph_helpers._format_body_word_counts(counts)


async def _drain_boundary_steering(get_steering_messages: Any) -> list[dict[str, str]]:
    if get_steering_messages is None:
        return []
    try:
        raw = await get_steering_messages()
    except Exception as exc:  # noqa: BLE001
        log_with_context(
            logger,
            40,  # ERROR
            "Failed to retrieve steering messages at loop run boundary",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return []
    drained: list[dict[str, str]] = []
    for msg in raw or []:
        if isinstance(msg, dict) and str(msg.get("content") or ""):
            drained.append({"id": str(msg.get("id") or ""), "content": str(msg["content"])})
    return drained


async def _settle_pending_empty_files(
    *,
    can_correct: bool,
    english: bool,
) -> tuple[str | None, StreamEvent | None, list[StreamEvent]]:
    """run 边界核验仍在等正文的空文件。

    返回 (纠偏说明, FILE_BODY_RESUME 事件, 其余事件)。纠偏说明非空时调用方先把 FILE_BODY_RESUME
    交给 StreamAdapter（它决定能否为同一个文件恢复捕获），再跑纠偏；补不成时其余事件里是回滚后的
    ``empty_file_correction`` 卡，调用方以它结束这一轮。
    """
    unfinished: list[dict[str, str]] = []
    for entry in ToolContext.get_pending_empty_files():
        file_id = str(entry.get("file_id") or entry.get("id") or "")
        title = str(entry.get("title") or ("Untitled" if english else "未命名"))
        state = await asyncio.to_thread(graph_helpers._probe_pending_file_body, file_id)
        if state in (graph_helpers._PENDING_BODY_WRITTEN, graph_helpers._PENDING_BODY_GONE):
            ToolContext.clear_pending_empty_file(file_id)
            continue
        unfinished.append({"file_id": file_id, "title": title})
    if not unfinished:
        return None, None, []
    if can_correct:
        target = unfinished[-1]
        facts = await asyncio.to_thread(graph_helpers._resume_target_version, target["file_id"])
        if facts is not None:
            resume = FileBodyResume(file_id=target["file_id"], expected_updated_at=facts[0])
            context = _EMPTY_FILE_CORRECTION_CONTEXT.format(title=target["title"], file_id=target["file_id"])
            return context, StreamEvent(type=StreamEventType.FILE_BODY_RESUME, data={"request": resume}), []
    return None, None, [await _empty_file_card(unfinished, None)]


async def _empty_file_card(unfinished: list[dict[str, str]], resume: FileBodyResume | None) -> StreamEvent:
    """补不成时回滚能确认的空文件，发既有的 empty_file_correction 卡（同图的口径）。"""
    from agent.core.workflow_events import FILE_BODY_RESUME_VERSION_CHANGED

    kept_ids = (
        {resume.file_id}
        if resume is not None and resume.refusal == FILE_BODY_RESUME_VERSION_CHANGED
        else set()
    )
    rollback_entries = graph_helpers._with_creation_versions(
        [entry for entry in unfinished if entry["file_id"] not in kept_ids]
    )
    rolled_back = (
        await asyncio.to_thread(graph_helpers._rollback_unfinished_empty_files, rollback_entries)
        if rollback_entries
        else set()
    )
    for entry in unfinished:
        if entry["file_id"] in rolled_back:
            ToolContext.clear_pending_empty_file(entry["file_id"])
    unresolved = [entry for entry in unfinished if entry["file_id"] not in rolled_back]
    log_with_context(
        logger,
        30,  # WARNING
        "Loop empty-file correction not completed",
        unfinished_count=len(unfinished),
        rolled_back_count=len(rolled_back),
        unresolved_count=len(unresolved),
    )
    return StreamEvent(
        type=StreamEventType.ITERATION_EXHAUSTED,
        data={
            "layer": "empty_file_correction",
            "iterations_used": graph_helpers.MAX_FILE_CORRECTION_ATTEMPTS,
            "max_iterations": graph_helpers.MAX_FILE_CORRECTION_ATTEMPTS,
            "reason": (
                "文件正文补写未完成，且无法安全回滚空文件。请继续对话完成这些文件。"
                if unresolved
                else "文件正文没有写成，已撤销这些空文件。"
            ),
            "unfinished_files": unresolved,
            "rolled_back_files": [entry for entry in unfinished if entry["file_id"] in rolled_back],
            "last_agent": None,
        },
    )


async def run_loop_agent_streaming(
    state: WritingState,
    *,
    get_steering_messages: Any | None = None,
    max_runs: int = AGENT_COLLABORATION_MAX_ITERATIONS,
) -> AsyncIterator[StreamEvent]:
    """跑完单循环 agent 的一轮，产出与图相同类型的工作流事件（不含路由 / 角色 / 交接事件）。"""
    english = _english(state)
    state["tool_failure_breaker"] = ToolFailureBreaker()
    state["repeat_read_guard"] = RepeatReadGuard()
    if not isinstance(state.get("run_meter"), AgentRunMeter):
        state["run_meter"] = AgentRunMeter()
    run_meter: AgentRunMeter = state["run_meter"]
    read_guard: RepeatReadGuard = state["repeat_read_guard"]
    native_runs: dict[str, Any] = state.setdefault("native_run_holder", {})
    system_prompt = str(state.get("system_prompt") or "")
    drop_english_narration = text_contains_cjk(state.get("router_message") or state.get("user_message"))

    ToolContext.set_current_agent(LOOP_AGENT_TYPE)
    # 写作约定由模型按提示判断是否追加（只追加、不删改、expected_notes 冲突保护照旧）。
    ToolContext.set_notes_append_allowed(True)
    ToolContext.begin_agent_run()

    runs = 0
    correction_attempts = 0
    resumed_correction = False
    unexecuted_reported = False
    accumulated_text = ""
    terminated = False
    try:
        while runs < max_runs:
            if runs > 0 and run_meter.past_wrap_up:
                # 请求时间快用完：不再开新的 run，停在已经完成的部分（同图层收尾的暂停卡）。
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": await graph_helpers._wrap_up_text(english)})
                yield StreamEvent(
                    type=StreamEventType.ITERATION_EXHAUSTED,
                    data={
                        "layer": "collaboration",
                        "iterations_used": runs,
                        "max_iterations": max_runs,
                        "reason": graph_helpers.WRAP_UP_CARD_REASON_EN if english else graph_helpers.WRAP_UP_CARD_REASON,
                        "last_agent": None,
                        "limit": WALL_CLOCK_LIMIT,
                    },
                )
                terminated = True
                break
            runs += 1
            state["continue_native_run"] = runs > 1 and native_runs.get("agent_type") == LOOP_AGENT_TYPE
            run_resumed = resumed_correction
            resumed_correction = False

            tail_text = ""
            run_started = False
            mid_run_steering = False
            errored = False
            tool_call_exhausted = False
            deferred_exhausted: StreamEvent | None = None
            create_ids: set[str] = set()
            failed_ids: set[str] = set()
            shaper = AgentTextShaper(previous_text=accumulated_text, drop_english_narration=drop_english_narration)

            async for event in graph_helpers._shape_agent_text(
                run_openai_agents_streaming_agent(
                    state=state,
                    agent_type=LOOP_AGENT_TYPE,
                    system_prompt=system_prompt,
                    get_steering_messages=get_steering_messages,
                ),
                shaper,
            ):
                if event.type == StreamEventType.MESSAGE_START:
                    run_started = True
                elif event.type == StreamEventType.ERROR:
                    errored = True
                elif getattr(event.type, "value", "") == "steering_received" and run_started:
                    # MESSAGE_START 之后的插话是工具边界消费的，这次 run 已经用不上。
                    mid_run_steering = True
                if event.type == StreamEventType.TEXT:
                    text = str(event.data.get("text") or "")
                    tail_text += text
                    accumulated_text += text
                elif event.type in (StreamEventType.TOOL_USE, StreamEventType.TOOL_RESULT):
                    tail_text = ""
                    if event.type == StreamEventType.TOOL_USE and event.data.get("name") in _BODY_WRITE_TOOLS:
                        create_ids.add(str(event.data.get("id") or ""))
                    elif event.type == StreamEventType.TOOL_RESULT and graph_helpers._tool_result_failed(
                        event.data.get("result")
                    ):
                        failed_ids.add(str(event.data.get("tool_use_id") or ""))
                if event.type == StreamEventType.ITERATION_EXHAUSTED and event.data.get("layer") == "tool_call":
                    tool_call_exhausted = True
                    if ToolContext.has_pending_empty_file():
                        # 等 run 收尾、核验 / 回滚空文件之后再发，文件事实写在同一张卡上。
                        deferred_exhausted = event
                        continue
                yield event

            if deferred_exhausted is not None:
                file_facts = await graph_helpers._settle_empty_files_after_exhaustion(LOOP_AGENT_TYPE, english)
                yield StreamEvent(
                    type=StreamEventType.ITERATION_EXHAUSTED,
                    data={**deferred_exhausted.data, **file_facts},
                )
            if errored or tool_call_exhausted:
                terminated = True
                break

            if ToolContext.has_pending_empty_file():
                can_correct = (
                    runs < max_runs
                    and correction_attempts < graph_helpers.MAX_FILE_CORRECTION_ATTEMPTS
                    and not run_meter.exhausted
                    and not run_meter.past_wrap_up
                )
                context, resume_event, settle_events = await _settle_pending_empty_files(
                    can_correct=can_correct, english=english
                )
                if resume_event is not None:
                    yield resume_event
                    resume = resume_event.data["request"]
                    if resume.armed:
                        correction_attempts += 1
                        resumed_correction = True
                        state["user_message"] = context or ""
                        continue
                    pending = [
                        {"file_id": str(e.get("file_id") or ""), "title": str(e.get("title") or "")}
                        for e in ToolContext.get_pending_empty_files()
                    ]
                    settle_events = [await _empty_file_card(pending, resume)] if pending else []
                for settle_event in settle_events:
                    yield settle_event
                if settle_events:
                    terminated = True
                    break

            if not unexecuted_reported and (
                textual_calls := unexecuted_tool_protocol(tail_text, frozenset(LOOP_TOOL_NAMES))
            ):
                # 回复里写出了完整的工具调用协议却没有执行：只按协议结构识别，如实记为未完成，不重试。
                unexecuted_reported = True
                log_with_context(
                    logger, 30, "textual_tool_call", agent_type=LOOP_AGENT_TYPE, call_count=len(textual_calls)
                )
                yield StreamEvent(
                    type=StreamEventType.ITERATION_EXHAUSTED,
                    data={
                        "layer": UNEXECUTED_TOOL_TEXT_LAYER,
                        "iterations_used": runs,
                        "max_iterations": max_runs,
                        "reason": _UNEXECUTED_TOOL_TEXT_REASON_EN if english else _UNEXECUTED_TOOL_TEXT_REASON,
                        "last_agent": None,
                    },
                )

            followup: str | None = None
            ended_on_file = ends_on_file_block(tail_text) or (
                (bool(create_ids - failed_ids) or run_resumed) and ends_on_unclosed_file_block(tail_text)
            )
            if (
                (bool(create_ids - failed_ids) or run_resumed)
                and not ToolContext.has_pending_empty_file()
                and ended_on_file
            ):
                followup = graph_helpers._file_stream_continuation_context(
                    list(read_guard.files_written.values()),
                    graph_helpers._open_board_tasks(),
                    "",
                    english=english,
                ) + _word_count_note(read_guard)
            boundary_steering = await _drain_boundary_steering(get_steering_messages)
            if boundary_steering:
                state["messages"] = list(state.get("messages") or []) + [
                    {"role": "user", "content": msg["content"]} for msg in boundary_steering
                ]
                from agent.core.events import steering_received_event

                for msg in boundary_steering:
                    yield steering_received_event(message_id=msg["id"], preview=msg["content"][:50])
            if mid_run_steering or boundary_steering:
                followup = (
                    f"{followup}\n{graph_helpers.STEERING_FOLLOWUP_CONTEXT}"
                    if followup
                    else graph_helpers.STEERING_FOLLOWUP_CONTEXT
                )
            if followup is None:
                # 正常结束：与图相同，按本请求实际写出的文件把任务板上对应的任务收尾（什么都没写时不补）。
                for task_event in await graph_helpers._auto_finalize_task_board_on_completion(read_guard):
                    yield task_event
                terminated = True
                break
            log_with_context(
                logger,
                20,  # INFO
                "Loop agent continues within the turn",
                run=runs,
                ended_on_file=bool(ended_on_file),
                steering=bool(mid_run_steering or boundary_steering),
            )
            state["user_message"] = followup

        if not terminated and runs >= max_runs:
            yield StreamEvent(
                type=StreamEventType.ITERATION_EXHAUSTED,
                data={
                    "layer": "collaboration",
                    "iterations_used": runs,
                    "max_iterations": max_runs,
                    "reason": (
                        f"这一轮已连续运行 {max_runs} 次，先停在这里。"
                        "任务可能未完全完成，您可以继续对话让 AI 完成剩余工作。"
                    ),
                    "last_agent": None,
                },
            )
    except Exception as exc:
        info = classify_stream_exception(exc)
        log_stream_exception(logger, "Loop agent stream error", exc, info)
        yield StreamEvent(type=StreamEventType.ERROR, data=info.as_event_data(error_type=type(exc).__name__))
    finally:
        ToolContext.set_current_agent(None)
