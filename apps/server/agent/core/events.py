"""
SSE event types and definitions for streaming responses.

Defines the event structure for Server-Sent Events (SSE) used
to stream agent responses to the frontend.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class EventType(StrEnum):
    """Event types for SSE stream."""

    # Progress events
    THINKING = "thinking"       # AI is processing the request
    THINKING_CONTENT = "thinking_content"  # AI reasoning content chunk
    CONTEXT = "context"         # Context has been assembled

    # Content events
    CONTENT = "content"         # Generated content chunk
    CONTENT_START = "content_start"  # Content generation started
    CONTENT_END = "content_end"      # Content generation finished

    # Tool events (new)
    TOOL_CALL = "tool_call"     # AI is calling a tool
    TOOL_RESULT = "tool_result" # Tool execution result

    # Result events
    CONFLICT = "conflict"       # Consistency conflict detected
    REFERENCE = "reference"     # Reference/citation info

    # File streaming events
    FILE_CREATED = "file_created"          # File created (for auto-select)
    FILE_CONTENT = "file_content"          # File content chunk (streaming)
    FILE_CONTENT_END = "file_content_end"  # File content streaming ended

    # File edit events
    FILE_EDIT_START = "file_edit_start"      # Started editing a file
    FILE_EDIT_APPLIED = "file_edit_applied"  # Single edit operation completed
    FILE_EDIT_END = "file_edit_end"          # All edits completed

    # Control events
    FALLBACK = "fallback"       # Need user confirmation for intent
    DONE = "done"               # Stream completed successfully
    ERROR = "error"             # Error occurred

    # Skill events
    SKILL_MATCHED = "skill_matched"  # Skill was matched and loaded
    SKILLS_MATCHED = "skills_matched"  # Multiple skills matched (multi-skill activation)

    # Agent events
    AGENT_SELECTED = "agent_selected"  # Agent was selected by router
    HANDOFF = "handoff"  # Agent handoff request emitted by workflow
    ITERATION_EXHAUSTED = "iteration_exhausted"  # Iteration limit reached

    # Router events
    ROUTER_THINKING = "router_thinking"  # Router is analyzing the request
    ROUTER_DECIDED = "router_decided"    # Router has made a decision

    # Workflow events
    WORKFLOW_STOPPED = "workflow_stopped"   # Workflow stopped (e.g., needs clarification)
    WORKFLOW_COMPLETE = "workflow_complete" # Workflow completed successfully

    # Session lifecycle events
    SESSION_STARTED = "session_started"    # Agent session started

    # Parallel execution events
    PARALLEL_START = "parallel_start"          # Parallel execution started
    PARALLEL_TASK_START = "parallel_task_start"  # Individual parallel task started
    PARALLEL_TASK_END = "parallel_task_end"    # Individual parallel task ended
    PARALLEL_END = "parallel_end"              # Parallel execution completed

    # Steering events
    STEERING_RECEIVED = "steering_received"  # Steering message received


# 只读请求拦下「交接给有写权限的 agent」时，WORKFLOW_STOPPED 提示卡片的 reason。
# 这张卡片在工作流收尾时（终止事件之前）才发出，只是一条说明，不代表流结束：
# api/agent.py 判定「是否收到终止事件」（决定兜底 error 帧与计费分类）时必须跳过它。
READ_ONLY_HANDOFF_BLOCKED_REASON = "read_only_handoff_blocked"
NON_TERMINAL_WORKFLOW_STOPPED_REASONS: frozenset[str] = frozenset(
    {READ_ONLY_HANDOFF_BLOCKED_REASON}
)


class StreamEvent(BaseModel):
    """
    A single SSE event.

    Serialized to SSE format:
        event: {type}
        data: {json(data)}
    """

    type: EventType = Field(..., description="Event type")
    data: dict[str, Any] = Field(default_factory=dict, description="Event payload")

    def to_sse(self) -> str:
        """Convert to SSE format string with datetime handling."""
        import json

        class DateTimeEncoder(json.JSONEncoder):
            """Custom JSON encoder that handles datetime objects."""

            def default(self, obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                return super().default(obj)

        data_json = json.dumps(self.data, ensure_ascii=False, cls=DateTimeEncoder)
        return f"event: {self.type.value}\ndata: {data_json}\n\n"


# ========== Convenience Event Builders ==========

class ThinkingEventData(BaseModel):
    """Data for thinking events."""
    message: str = Field(..., description="Status message")
    step: str | None = Field(default=None, description="Current step identifier")


class ThinkingContentEventData(BaseModel):
    """Data for thinking_content events."""
    content: str = Field(..., description="Reasoning content chunk")
    is_complete: bool = Field(default=False, description="Whether this is the final chunk")


class ContextEventData(BaseModel):
    """Data for context events."""
    items: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Context items used"
    )
    token_count: int | None = Field(
        default=None,
        description="Estimated token count"
    )


class ContentEventData(BaseModel):
    """Data for content events."""
    text: str = Field(..., description="Content chunk")
    index: int | None = Field(default=None, description="Chunk index")


class ConflictEventData(BaseModel):
    """Data for conflict events."""
    type: str = Field(..., description="Conflict type")
    severity: str = Field(..., description="Severity: low/medium/high")
    title: str = Field(..., description="Conflict title")
    description: str = Field(..., description="Conflict description")
    suggestions: list[str] = Field(default_factory=list, description="Fix suggestions")


class FallbackEventData(BaseModel):
    """Data for fallback events."""
    message: str = Field(..., description="Clarification message")
    options: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Intent options for user to choose"
    )
    classification: dict[str, Any] | None = Field(
        default=None,
        description="Intent classification result"
    )


class ErrorEventData(BaseModel):
    """Data for error events."""
    message: str = Field(..., description="Error message")
    code: str | None = Field(default=None, description="Error code")
    retryable: bool = Field(default=False, description="Whether the error is retryable")
    refundable: bool | None = Field(
        default=None,
        description=(
            "Whether this failure is a platform-side fault eligible for a quota refund; "
            "None means unspecified (treated as refundable). The API layer still keeps "
            "the charge when the turn already produced output."
        ),
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Machine-readable stop reason (e.g. 'no_progress' for the repeat-read guard); "
            "the billing tracker uses it to recognise runaway stops."
        ),
    )


class DoneEventData(BaseModel):
    """Data for done events."""
    file_mutated: bool = Field(
        default=False,
        description="This normally completed turn confirmed a committed file mutation",
    )
    apply_action: str | None = Field(
        default=None,
        description="Suggested apply action: insert/replace/new_snippet/reference_only"
    )
    refs: list[int] = Field(
        default_factory=list,
        description="Referenced snippet IDs"
    )
    intent: str | None = Field(
        default=None,
        description="Detected intent"
    )
    assistant_message_id: str | None = Field(
        default=None,
        description="Persisted assistant chat message ID for this turn",
    )
    session_id: str | None = Field(
        default=None,
        description="Chat session ID associated with this completed turn",
    )
    stop_reason: str | None = Field(
        default=None,
        description="Set when the author stopped this round (user_stopped)",
    )
    produced_output: bool | None = Field(
        default=None,
        description="Stopped rounds only: whether the round produced real output (prose or a write)",
    )


class ToolCallEventData(BaseModel):
    """Data for tool call events."""
    tool_use_id: str | None = Field(
        default=None,
        description="Tool use id from model/tool lifecycle",
    )
    tool_name: str = Field(..., description="Name of the tool being called")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Tool arguments"
    )


class ToolResultEventData(BaseModel):
    """Data for tool result events."""
    tool_use_id: str | None = Field(
        default=None,
        description="Tool use id from model/tool lifecycle",
    )
    tool_name: str = Field(..., description="Name of the tool that was executed")
    status: str = Field(..., description="Execution status: success/error")
    data: Any | None = Field(
        default=None,
        description="Tool execution result (if successful) - can be dict or list"
    )
    error: str | None = Field(
        default=None,
        description="Error message (if failed)"
    )


class FileCreatedEventData(BaseModel):
    """Data for file_created events."""
    file_id: str = Field(..., description="ID of the created file")
    file_type: str = Field(..., description="Type of the file (outline, draft, character, etc.)")
    title: str = Field(..., description="Title of the file")


class FileContentEventData(BaseModel):
    """Data for file_content events."""
    file_id: str = Field(..., description="ID of the file being streamed")
    chunk: str = Field(..., description="Content chunk")


class FileContentEndEventData(BaseModel):
    """Data for file_content_end events."""
    file_id: str = Field(..., description="ID of the file that finished streaming")


class FileEditStartEventData(BaseModel):
    """Data for file_edit_start events."""
    file_id: str = Field(..., description="ID of the file being edited")
    title: str = Field(..., description="Title of the file")
    total_edits: int = Field(..., description="Total number of edits to apply")
    file_type: str | None = Field(default=None, description="Type of the file")


class FileEditAppliedEventData(BaseModel):
    """Data for file_edit_applied events."""
    file_id: str = Field(..., description="ID of the file being edited")
    edit_index: int = Field(..., description="Index of the edit (0-based)")
    op: str = Field(..., description="Operation type (replace/insert_after/etc.)")
    old_preview: str | None = Field(default=None, description="Preview of old text (first 50 chars)")
    new_preview: str | None = Field(default=None, description="Preview of new text (first 50 chars)")
    success: bool = Field(default=True, description="Whether the edit succeeded")
    error: str | None = Field(default=None, description="Error message if failed")


class FileEditEndEventData(BaseModel):
    """Data for file_edit_end events."""
    file_id: str = Field(..., description="ID of the file that finished editing")
    edits_applied: int = Field(..., description="Number of edits successfully applied")
    new_length: int = Field(..., description="New content length in characters")
    new_content: str | None = Field(default=None, description="Full content after edits (for diff review)")
    original_content: str | None = Field(default=None, description="Content before edits (for diff review)")
    file_type: str | None = Field(default=None, description="Type of the edited file")
    title: str | None = Field(default=None, description="Title of the edited file")
    # 批量编辑可以部分失败（continue_on_error）。只报 edits_applied 会让前端把
    # "3 条里成功 1 条" 渲染成纯成功，用户看不到另外 2 条根本没改。
    failed_count: int = Field(default=0, description="Number of edits that failed")
    partial_success: bool = Field(
        default=False, description="True when some edits applied and some failed"
    )
    all_failed: bool = Field(default=False, description="True when every edit failed")
    warnings: list[str] = Field(
        default_factory=list, description="Non-fatal warnings raised while editing"
    )


# ========== Event Factory Functions ==========

def thinking_event(message: str, step: str | None = None) -> StreamEvent:
    """Create a thinking event."""
    return StreamEvent(
        type=EventType.THINKING,
        data=ThinkingEventData(message=message, step=step).model_dump()
    )


def thinking_content_event(content: str, is_complete: bool = False) -> StreamEvent:
    """Create a thinking_content event."""
    return StreamEvent(
        type=EventType.THINKING_CONTENT,
        data=ThinkingContentEventData(content=content, is_complete=is_complete).model_dump()
    )


def context_event(items: list[dict], token_count: int | None = None) -> StreamEvent:
    """Create a context event."""
    return StreamEvent(
        type=EventType.CONTEXT,
        data=ContextEventData(items=items, token_count=token_count).model_dump()
    )


def content_event(text: str, index: int | None = None) -> StreamEvent:
    """Create a content event."""
    return StreamEvent(
        type=EventType.CONTENT,
        data=ContentEventData(text=text, index=index).model_dump()
    )


def content_start_event() -> StreamEvent:
    """Create a content start event."""
    return StreamEvent(type=EventType.CONTENT_START, data={})


def content_end_event() -> StreamEvent:
    """Create a content end event."""
    return StreamEvent(type=EventType.CONTENT_END, data={})


def conflict_event(
    conflict_type: str,
    severity: str,
    title: str,
    description: str,
    suggestions: list[str] | None = None,
) -> StreamEvent:
    """Create a conflict event."""
    return StreamEvent(
        type=EventType.CONFLICT,
        data=ConflictEventData(
            type=conflict_type,
            severity=severity,
            title=title,
            description=description,
            suggestions=suggestions or [],
        ).model_dump()
    )


def fallback_event(
    message: str,
    options: list[dict[str, Any]],
    classification: dict[str, Any] | None = None,
) -> StreamEvent:
    """Create a fallback event."""
    return StreamEvent(
        type=EventType.FALLBACK,
        data=FallbackEventData(
            message=message,
            options=options,
            classification=classification,
        ).model_dump()
    )


def error_event(
    message: str,
    code: str | None = None,
    retryable: bool = False,
    refundable: bool | None = None,
    reason: str | None = None,
) -> StreamEvent:
    """Create an error event."""
    return StreamEvent(
        type=EventType.ERROR,
        data=ErrorEventData(
            message=message,
            code=code,
            retryable=retryable,
            refundable=refundable,
            reason=reason,
        ).model_dump()
    )


def done_event(
    apply_action: str | None = None,
    refs: list[int] | None = None,
    intent: str | None = None,
    assistant_message_id: str | None = None,
    session_id: str | None = None,
    file_mutated: bool = False,
    stop_reason: str | None = None,
    produced_output: bool | None = None,
) -> StreamEvent:
    """Create a done event."""
    data = DoneEventData(
        file_mutated=file_mutated,
        apply_action=apply_action,
        refs=refs or [],
        intent=intent,
        assistant_message_id=assistant_message_id,
        session_id=session_id,
        stop_reason=stop_reason,
        produced_output=produced_output,
    ).model_dump()
    # 只有作者停止的一轮才带这两个字段，其余 done 帧保持原样。
    for key in ("stop_reason", "produced_output"):
        if data.get(key) is None:
            data.pop(key, None)
    return StreamEvent(type=EventType.DONE, data=data)


def tool_call_event(
    tool_name: str,
    arguments: dict[str, Any],
    tool_use_id: str | None = None,
) -> StreamEvent:
    """Create a tool call event."""
    return StreamEvent(
        type=EventType.TOOL_CALL,
        data=ToolCallEventData(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            arguments=arguments,
        ).model_dump()
    )


def tool_result_event(
    tool_name: str,
    status: str,
    data: Any | None = None,
    error: str | None = None,
    tool_use_id: str | None = None,
) -> StreamEvent:
    """Create a tool result event."""
    return StreamEvent(
        type=EventType.TOOL_RESULT,
        data=ToolResultEventData(
            tool_use_id=tool_use_id,
            tool_name=tool_name,
            status=status,
            data=data,
            error=error,
        ).model_dump()
    )


def file_created_event(
    file_id: str,
    file_type: str,
    title: str,
) -> StreamEvent:
    """Create a file_created event for auto-selecting new files."""
    return StreamEvent(
        type=EventType.FILE_CREATED,
        data=FileCreatedEventData(
            file_id=file_id,
            file_type=file_type,
            title=title,
        ).model_dump()
    )


def file_content_event(
    file_id: str,
    chunk: str,
) -> StreamEvent:
    """Create a file_content event for streaming file content."""
    return StreamEvent(
        type=EventType.FILE_CONTENT,
        data=FileContentEventData(
            file_id=file_id,
            chunk=chunk,
        ).model_dump()
    )


def file_content_end_event(
    file_id: str,
) -> StreamEvent:
    """Create a file_content_end event when streaming is complete."""
    return StreamEvent(
        type=EventType.FILE_CONTENT_END,
        data=FileContentEndEventData(
            file_id=file_id,
        ).model_dump()
    )


def file_edit_start_event(
    file_id: str,
    title: str,
    total_edits: int,
    file_type: str | None = None,
) -> StreamEvent:
    """Create a file_edit_start event when starting file edits."""
    return StreamEvent(
        type=EventType.FILE_EDIT_START,
        data=FileEditStartEventData(
            file_id=file_id,
            title=title,
            total_edits=total_edits,
            file_type=file_type,
        ).model_dump()
    )


def file_edit_applied_event(
    file_id: str,
    edit_index: int,
    op: str,
    old_preview: str | None = None,
    new_preview: str | None = None,
    success: bool = True,
    error: str | None = None,
) -> StreamEvent:
    """Create a file_edit_applied event when a single edit is completed."""
    return StreamEvent(
        type=EventType.FILE_EDIT_APPLIED,
        data=FileEditAppliedEventData(
            file_id=file_id,
            edit_index=edit_index,
            op=op,
            old_preview=old_preview,
            new_preview=new_preview,
            success=success,
            error=error,
        ).model_dump()
    )


def file_edit_end_event(
    file_id: str,
    edits_applied: int,
    new_length: int,
    new_content: str | None = None,
    original_content: str | None = None,
    file_type: str | None = None,
    title: str | None = None,
    failed_count: int = 0,
    partial_success: bool = False,
    all_failed: bool = False,
    warnings: list[str] | None = None,
) -> StreamEvent:
    """Create a file_edit_end event when all edits are complete."""
    return StreamEvent(
        type=EventType.FILE_EDIT_END,
        data=FileEditEndEventData(
            file_id=file_id,
            edits_applied=edits_applied,
            new_length=new_length,
            new_content=new_content,
            original_content=original_content,
            file_type=file_type,
            title=title,
            failed_count=max(0, failed_count),
            partial_success=partial_success,
            all_failed=all_failed,
            warnings=[str(w) for w in (warnings or []) if w],
        ).model_dump()
    )


class SkillMatchedEventData(BaseModel):
    """Data for skill_matched events."""
    skill_id: str = Field(..., description="ID of the matched skill")
    skill_name: str = Field(..., description="Name of the matched skill")
    matched_trigger: str = Field(..., description="The trigger that matched")


class SkillsMatchedEventData(BaseModel):
    """Data for skills_matched events (multi-skill activation)."""
    skills: list[dict] = Field(
        ...,
        description="List of matched skills with id, name, trigger, confidence"
    )
    total_count: int = Field(..., description="Total number of matched skills")


def skill_matched_event(
    skill_id: str,
    skill_name: str,
    matched_trigger: str,
) -> StreamEvent:
    """Create a skill_matched event when a skill is loaded."""
    return StreamEvent(
        type=EventType.SKILL_MATCHED,
        data=SkillMatchedEventData(
            skill_id=skill_id,
            skill_name=skill_name,
            matched_trigger=matched_trigger,
        ).model_dump()
    )


def skills_matched_event(
    skills: list[dict],
) -> StreamEvent:
    """Create a skills_matched event when multiple skills are activated."""
    return StreamEvent(
        type=EventType.SKILLS_MATCHED,
        data=SkillsMatchedEventData(
            skills=skills,
            total_count=len(skills),
        ).model_dump()
    )


class AgentSelectedEventData(BaseModel):
    """Data for agent_selected events."""
    agent_type: str = Field(..., description="Type of the selected agent")
    agent_name: str = Field(..., description="Name of the selected agent")
    iteration: int | None = Field(default=None, description="Current iteration number")
    max_iterations: int | None = Field(default=None, description="Maximum iterations allowed")
    remaining: int | None = Field(default=None, description="Remaining iterations")


def agent_selected_event(
    agent_type: str,
    agent_name: str,
    iteration: int | None = None,
    max_iterations: int | None = None,
    remaining: int | None = None,
) -> StreamEvent:
    """Create an agent_selected event when router selects an agent."""
    return StreamEvent(
        type=EventType.AGENT_SELECTED,
        data=AgentSelectedEventData(
            agent_type=agent_type,
            agent_name=agent_name,
            iteration=iteration,
            max_iterations=max_iterations,
            remaining=remaining,
        ).model_dump()
    )


class IterationExhaustedEventData(BaseModel):
    """Data for iteration_exhausted events."""
    layer: str = Field(..., description="Which layer hit the limit: 'collaboration' or 'tool_call'")
    iterations_used: int = Field(..., description="Total iterations used")
    max_iterations: int = Field(..., description="Maximum allowed")
    reason: str = Field(..., description="Human-readable explanation")
    last_agent: str | None = Field(default=None, description="Last active agent")


class SessionStartedEventData(BaseModel):
    """Data for session_started events."""
    session_id: str = Field(..., description="ID of the started session")


class ParallelStartEventData(BaseModel):
    """Data for parallel_start events."""
    execution_id: str = Field(..., description="Unique ID for this parallel execution")
    task_count: int = Field(..., description="Number of tasks actually executed this round")
    task_descriptions: list[str] = Field(..., description="Descriptions of each executed task")
    # 单次并行有上限（MAX_PARALLEL_TASKS），超出的任务会被截断丢弃。
    # 只报 task_count 会让前端进度条显示 "5/5 完成" 而隐藏掉被丢弃的 2 个任务；
    # 只把 task_count 改成请求数又会让进度条永远停在 "5/7"。
    # 因此两者都发：task_count 驱动进度条，requested_task_count/dropped_count 用于
    # 在完成后追加 "2 个未执行" 的提示。
    requested_task_count: int = Field(
        default=0, description="Number of tasks the model originally requested"
    )
    dropped_count: int = Field(
        default=0, description="Number of requested tasks dropped due to the concurrency cap"
    )


class ParallelTaskStartEventData(BaseModel):
    """Data for parallel_task_start events."""
    execution_id: str = Field(..., description="ID of the parent parallel execution")
    task_id: str = Field(..., description="Unique ID for this task")
    task_type: str = Field(..., description="Type of the task")
    description: str = Field(..., description="Description of the task")


class ParallelTaskEndEventData(BaseModel):
    """Data for parallel_task_end events."""
    execution_id: str = Field(..., description="ID of the parent parallel execution")
    task_id: str = Field(..., description="Unique ID for this task")
    status: str = Field(..., description="Task status: 'completed' or 'failed'")
    result_preview: str | None = Field(default=None, description="Preview of the result (first 100 chars)")
    error: str | None = Field(default=None, description="Error message if failed")


class ParallelEndEventData(BaseModel):
    """Data for parallel_end events."""
    execution_id: str = Field(..., description="ID of the parallel execution")
    total_tasks: int = Field(..., description="Total number of tasks")
    completed: int = Field(..., description="Number of completed tasks")
    failed: int = Field(..., description="Number of failed tasks")
    duration_ms: int = Field(..., description="Total execution duration in milliseconds")
    # 与 ParallelStartEventData 同义：让 UI 在收尾时也能说明"还有 N 个未执行"，
    # 不必缓存 start 事件的字段。
    requested_task_count: int = Field(
        default=0, description="Number of tasks the model originally requested"
    )
    dropped_count: int = Field(
        default=0, description="Number of requested tasks dropped due to the concurrency cap"
    )


class SteeringReceivedEventData(BaseModel):
    """Data for steering_received events."""
    message_id: str = Field(..., description="ID of the steering message")
    preview: str = Field(..., description="Preview of the steering message content")


def iteration_exhausted_event(
    layer: str,
    iterations_used: int,
    max_iterations: int,
    reason: str,
    last_agent: str | None = None,
) -> StreamEvent:
    """Create an iteration_exhausted event when iteration limit is reached."""
    return StreamEvent(
        type=EventType.ITERATION_EXHAUSTED,
        data=IterationExhaustedEventData(
            layer=layer,
            iterations_used=iterations_used,
            max_iterations=max_iterations,
            reason=reason,
            last_agent=last_agent,
        ).model_dump()
    )


def session_started_event(session_id: str) -> StreamEvent:
    """Create a session_started event when an agent session begins."""
    return StreamEvent(
        type=EventType.SESSION_STARTED,
        data=SessionStartedEventData(session_id=session_id).model_dump()
    )


def parallel_start_event(
    execution_id: str,
    task_count: int,
    task_descriptions: list[str],
    requested_task_count: int | None = None,
    dropped_count: int = 0,
) -> StreamEvent:
    """Create a parallel_start event when parallel execution begins."""
    return StreamEvent(
        type=EventType.PARALLEL_START,
        data=ParallelStartEventData(
            execution_id=execution_id,
            task_count=task_count,
            task_descriptions=task_descriptions,
            requested_task_count=(
                task_count if requested_task_count is None else requested_task_count
            ),
            dropped_count=max(0, dropped_count),
        ).model_dump()
    )


def parallel_task_start_event(
    execution_id: str,
    task_id: str,
    task_type: str,
    description: str,
) -> StreamEvent:
    """Create a parallel_task_start event when a parallel task begins."""
    return StreamEvent(
        type=EventType.PARALLEL_TASK_START,
        data=ParallelTaskStartEventData(
            execution_id=execution_id,
            task_id=task_id,
            task_type=task_type,
            description=description,
        ).model_dump()
    )


def parallel_task_end_event(
    execution_id: str,
    task_id: str,
    status: str,
    result_preview: str | None = None,
    error: str | None = None,
) -> StreamEvent:
    """Create a parallel_task_end event when a parallel task completes."""
    return StreamEvent(
        type=EventType.PARALLEL_TASK_END,
        data=ParallelTaskEndEventData(
            execution_id=execution_id,
            task_id=task_id,
            status=status,
            result_preview=result_preview,
            error=error,
        ).model_dump()
    )


def parallel_end_event(
    execution_id: str,
    total_tasks: int,
    completed: int,
    failed: int,
    duration_ms: int,
    requested_task_count: int | None = None,
    dropped_count: int = 0,
) -> StreamEvent:
    """Create a parallel_end event when parallel execution completes."""
    return StreamEvent(
        type=EventType.PARALLEL_END,
        data=ParallelEndEventData(
            execution_id=execution_id,
            total_tasks=total_tasks,
            completed=completed,
            failed=failed,
            duration_ms=duration_ms,
            requested_task_count=(
                total_tasks if requested_task_count is None else requested_task_count
            ),
            dropped_count=max(0, dropped_count),
        ).model_dump()
    )


def steering_received_event(
    message_id: str,
    preview: str,
) -> StreamEvent:
    """Create a steering_received event when a steering message is received."""
    return StreamEvent(
        type=EventType.STEERING_RECEIVED,
        data=SteeringReceivedEventData(
            message_id=message_id,
            preview=preview,
        ).model_dump()
    )
