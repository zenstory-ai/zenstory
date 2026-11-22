"""Append-only per-round agent latency & reliability fact table.

Strictly numeric, enum, ID, and boolean columns ONLY:
- Zero manuscript text
- Zero prompt text
- Zero user PII
"""

from datetime import datetime

from sqlalchemy import Index, false
from sqlmodel import Field, SQLModel

from .utils import generate_uuid


class AgentRoundMetric(SQLModel, table=True):
    """Per-round execution timing and reliability facts written once at /agent/stream settlement."""

    __tablename__ = "agent_round_metric"
    __table_args__ = (
        Index("ix_agent_round_metric_occurred_at", "occurred_at"),
        Index("ix_agent_round_metric_user_id_occurred_at", "user_id", "occurred_at"),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    # Naive UTC, matching every other timestamp column in this codebase.
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    user_id: str = Field(foreign_key="user.id", index=True)
    project_id: str | None = Field(default=None, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)
    agent_run_id: str = Field(max_length=64, unique=True, index=True)

    is_mobile: bool | None = Field(default=None)
    ui_action: str | None = Field(default=None, max_length=32)
    primary_model: str | None = Field(default=None, max_length=64)

    # Per-round stage and perceived-wait timings in milliseconds (None when stage/milestone did not occur).
    prepare_context_ms: int | None = Field(default=None)
    first_output_ms: int | None = Field(default=None)
    first_tool_started_ms: int | None = Field(default=None)
    chapter_first_prose_ms: int | None = Field(default=None)
    first_body_persisted_ms: int | None = Field(default=None)
    total_duration_ms: int | None = Field(default=None)

    # Structured terminal outcomes and reliability counters.
    stop_reason: str | None = Field(default=None, max_length=64)
    billing_reason: str | None = Field(default=None, max_length=64)
    user_stopped: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    client_disconnected: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    deadline_exceeded: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    write_succeeded: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    runaway_stop: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    file_body_pending_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    leaked_tool_call_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    tool_calls_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    tool_failures_count: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
