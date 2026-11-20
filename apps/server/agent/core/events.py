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

    # File streaming events
    FILE_CREATED = "file_created"          # File created (for auto-select)
    FILE_CONTENT = "file_content"          # File content chunk (streaming)
    FILE_CONTENT_END = "file_content_end"  # File content streaming ended

    # File edit events
    FILE_EDIT_START = "file_edit_start"      # Started editing a file
    FILE_EDIT_APPLIED = "file_edit_applied"  # Single edit operation completed
    FILE_EDIT_END = "file_edit_end"          # All edits completed

    # Control events
    DONE = "done"               # Stream completed successfully
    ERROR = "error"             # Error occurred

    # Skill events
    SKILL_MATCHED = "skill_matched"  # Skill was matched and loaded

    # Agent / workflow events
    ITERATION_EXHAUSTED = "iteration_exhausted"  # Iteration limit reached
    WORKFLOW_STOPPED = "workflow_stopped"   # Workflow stopped (e.g., author stop)

    # Session lifecycle events
    SESSION_STARTED = "session_started"    # Agent session started

    # Steering events
    STEERING_RECEIVED = "steering_received"  # Steering message received

    # History compaction (single loop agent, between rounds)
    HISTORY_COMPACTED = "history_compacted"  # Earlier conversation was summarized for this round


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


class ErrorEventData(BaseModel):
    """Data for error events."""
    message: str = Field(..., description="Error message")
    code: str | None = Field(default=None, description="Error code")
    retryable: bool = Field(default=False, description="Whether the error is retryable")
    refundable: bool | None = Field(
        default=None,
        description=(
            "Whether this failure is eligible for a quota refund (unrecovered upstream "
            "model service error or a failure before model execution started)."
        ),
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Machine-readable stop reason (e.g. 'no_progress' for the repeat-read guard); "
            "the billing tracker uses it to recognise runaway stops."
        ),
    )
    file_id: str | None = Field(
        default=None,
        description="Target file ID when a streamed file write failed to persist",
    )
    written_files: list[dict[str, Any]] | None = None
    unwritten_files: list[dict[str, Any]] | None = None


class DoneEventData(BaseModel):
    """Data for done events."""
    file_mutated: bool = Field(
        default=False,
        description="This normally completed turn confirmed a committed file mutation",
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
    charged: bool | None = Field(
        default=None,
        description="Stopped rounds only: whether the server charged an AI message for this round",
    )
    produced_output: bool | None = Field(
        default=None,
        description="Stopped rounds only: whether the round produced real output (prose or a write)",
    )
    written_files: list[dict[str, Any]] | None = Field(
        default=None,
        description=(
            "When non-empty: files this round confirmed writing text into "
            "([{id, title, words?, partial?}], same as message_metadata.stop_outcome.written_files). "
            "Built from tool results and save receipts only, never from the reply text."
        ),
    )
    unwritten_files: list[dict[str, Any]] | None = None


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


# thinking 事件的 step：轮与轮之间的历史压缩开始 / 没压缩成（前端据此显示「正在压缩对话…」并在结束时收起）。
HISTORY_COMPACTION_STEP = "history_compaction"
HISTORY_COMPACTION_SKIPPED_STEP = "history_compaction_skipped"


def history_compacted_event(summary: str) -> StreamEvent:
    """历史压缩成功：前端把「正在压缩对话…」变成会话里的「对话已压缩」标记，可展开看交接摘要。"""
    return StreamEvent(type=EventType.HISTORY_COMPACTED, data={"summary": summary})


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


def error_event(
    message: str,
    code: str | None = None,
    retryable: bool = False,
    refundable: bool | None = None,
    reason: str | None = None,
    file_id: str | None = None,
) -> StreamEvent:
    """Create an error event."""
    data = ErrorEventData(
        message=message,
        code=code,
        retryable=retryable,
        refundable=refundable,
        reason=reason,
        file_id=file_id,
    ).model_dump(exclude={"written_files", "unwritten_files"})
    if data.get("file_id") is None:
        data.pop("file_id", None)
    return StreamEvent(type=EventType.ERROR, data=data)


def done_event(
    assistant_message_id: str | None = None,
    session_id: str | None = None,
    file_mutated: bool = False,
    stop_reason: str | None = None,
    charged: bool | None = None,
    produced_output: bool | None = None,
    written_files: list[dict[str, Any]] | None = None,
    unwritten_files: list[dict[str, Any]] | None = None,
) -> StreamEvent:
    """Create a done event."""
    data = DoneEventData(
        file_mutated=file_mutated,
        assistant_message_id=assistant_message_id,
        session_id=session_id,
        stop_reason=stop_reason,
        charged=charged,
        produced_output=produced_output,
        written_files=written_files or None,
        unwritten_files=unwritten_files or None,
    ).model_dump()
    # 只有提供这些可选回执时才带字段，其余 done 帧保持原样。
    for key in ("stop_reason", "charged", "produced_output", "written_files", "unwritten_files"):
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


# iteration_exhausted 的 limit：请求时间快用完了（runner 的时限收尾、循环收尾、墙钟硬超时且已有写入）。
# 前端据此显示「这一轮先做到这里」与继续按钮；StreamAdapter 据此把这一轮记成可「继续」的时限截停。
WALL_CLOCK_LIMIT = "wall_clock"
# iteration_exhausted 的一层：回复里有写成文字的完整工具调用、它没有执行（见
# loop.completion_evidence.unexecuted_tool_protocol）。不带 limit，不改 stop_reason，不是失控停止。
UNEXECUTED_TOOL_TEXT_LAYER = "unexecuted_tool_text"


class IterationExhaustedFile(BaseModel):
    """A file named on an iteration_exhausted card: id and title only, never its text."""
    id: str
    title: str


class IterationExhaustedEventData(BaseModel):
    """Data for iteration_exhausted events."""
    layer: str = Field(
        ...,
        description=(
            "Which layer stopped the round: 'collaboration', 'tool_call', 'empty_file_correction' "
            "(a created file never got its body: it was rolled back or is left unfinished), or "
            "'unexecuted_tool_text' (the reply wrote a complete tool call as text; it did not run)"
        ),
    )
    iterations_used: int = Field(..., description="Total iterations used")
    max_iterations: int = Field(..., description="Maximum allowed")
    reason: str = Field(..., description="Human-readable explanation")
    last_agent: str | None = Field(default=None, description="Last active agent")
    limit: str | None = Field(
        default=None,
        description="What ended the run: 'wall_clock' (time wrap-up), 'model_call_budget' or 'max_turns'",
    )
    unfinished_files: list[IterationExhaustedFile] | None = Field(
        default=None, description="empty_file_correction: files left without a body (kept, could not be rolled back)",
    )
    rolled_back_files: list[IterationExhaustedFile] | None = Field(
        default=None, description="empty_file_correction: empty files this round created and removed again",
    )


class SessionStartedEventData(BaseModel):
    """Data for session_started events."""
    session_id: str = Field(..., description="ID of the started session")


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
    limit: str | None = None,
    unfinished_files: list[dict[str, str]] | None = None,
    rolled_back_files: list[dict[str, str]] | None = None,
) -> StreamEvent:
    """Create an iteration_exhausted event when iteration limit is reached."""
    data = IterationExhaustedEventData(
        layer=layer,
        iterations_used=iterations_used,
        max_iterations=max_iterations,
        reason=reason,
        last_agent=last_agent,
        limit=limit,
        unfinished_files=unfinished_files,
        rolled_back_files=rolled_back_files,
    ).model_dump()
    # Only the stops that carry them keep these keys; other events' stored shape is unchanged.
    for key, value in (("limit", limit), ("unfinished_files", unfinished_files), ("rolled_back_files", rolled_back_files)):
        if value is None:
            data.pop(key)
    return StreamEvent(type=EventType.ITERATION_EXHAUSTED, data=data)


def iteration_exhausted_status_card(data: dict[str, Any]) -> dict[str, Any]:
    """iteration_exhausted 事件落库成 message_metadata.status_cards 里的一张卡（前端 statusCards 的形状）。"""
    return {
        "type": "iteration_exhausted",
        "layer": data.get("layer"),
        "iterationsUsed": data.get("iterations_used"),
        "maxIterations": data.get("max_iterations"),
        "reason": data.get("reason"),
        "lastAgent": data.get("last_agent"),
        "limit": data.get("limit"),
        # 空文件补不成：未写完 / 已撤销的文件（id 与标题），刷新后照样说清楚。
        **{
            card_key: data[event_key]
            for event_key, card_key in (
                ("unfinished_files", "unfinishedFiles"),
                ("rolled_back_files", "rolledBackFiles"),
            )
            if isinstance(data.get(event_key), list)
        },
    }


def session_started_event(session_id: str) -> StreamEvent:
    """Create a session_started event when an agent session begins."""
    return StreamEvent(
        type=EventType.SESSION_STARTED,
        data=SessionStartedEventData(session_id=session_id).model_dump()
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
