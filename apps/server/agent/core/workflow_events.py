"""Workflow streaming event types for the writing graph.

These events are the internal graph/runner contract consumed by the SSE
stream adapter. They are provider-neutral and must not depend on any LLM SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StreamEventType(Enum):
    """Types of internal workflow streaming events."""

    TEXT = "text"
    THINKING = "thinking"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    MESSAGE_START = "message_start"
    MESSAGE_END = "message_end"
    ERROR = "error"
    ITERATION_EXHAUSTED = "iteration_exhausted"
    STEERING_RECEIVED = "steering_received"
    # 单循环 agent → StreamAdapter 的内部控制事件，不发给前端：空文件纠偏轮开始前，请适配器为同一个
    # 文件重新等待 <file> 正文。data["request"] 是 FileBodyResume，适配器把结果写回它，
    # 引擎只在 armed 为 True 时才安排纠偏轮。见 StreamAdapter._resume_file_body。
    FILE_BODY_RESUME = "file_body_resume"


# FileBodyResume.refusal：适配器没有恢复捕获的固定原因（只进日志）。
FILE_BODY_RESUME_NOT_BOUND = "not_bound"  # 适配器没在等这个文件（不是本请求 create_file 的结果）
FILE_BODY_RESUME_NOT_EMPTY = "not_empty"  # 建文件时就有正文（复用命中的已写分集）
FILE_BODY_RESUME_VERSION_CHANGED = "version_changed"  # 建文件之后有人改过它
FILE_BODY_RESUME_CAPTURE_ACTIVE = "capture_active"  # 还有别的捕获在进行
FILE_BODY_RESUME_UNAVAILABLE = "unavailable"  # 不处理 <file> 标记，或流已出错
FILE_BODY_RESUME_GUARD_MISSING = "guard_missing"  # 这个文件的待写守卫已不在（或恢复失败）


@dataclass
class FileBodyResume:
    """空文件纠偏轮恢复正文捕获的请求与回执（FILE_BODY_RESUME 事件的 data["request"]）。"""

    file_id: str
    # 图刚从数据库读到的版本（updated_at.isoformat()）：必须与 create_file 结果里的版本相同。
    expected_updated_at: str
    # 捕获与这个文件的待写守卫都已恢复时才为 True。
    armed: bool = False
    refusal: str | None = None


@dataclass
class StreamEvent:
    """Internal workflow streaming event."""

    type: StreamEventType
    data: dict[str, Any] = field(default_factory=dict)
