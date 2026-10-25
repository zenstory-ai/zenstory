"""Append-only ledger of chat-model calls (any provider), one row per model call."""

from datetime import datetime
from typing import Any

from sqlalchemy import Index, false
from sqlmodel import Field, SQLModel

from .utils import generate_uuid

LLM_USAGE_SOURCE_AGENT = "agent"
LLM_USAGE_SOURCE_ROUTER = "router"
LLM_USAGE_SOURCE_SUGGEST = "suggest"
LLM_USAGE_SOURCE_POLISH = "polish"
LLM_USAGE_SOURCE_MATERIAL = "material"
LLM_USAGE_SOURCES = (
    LLM_USAGE_SOURCE_AGENT,
    LLM_USAGE_SOURCE_ROUTER,
    LLM_USAGE_SOURCE_SUGGEST,
    LLM_USAGE_SOURCE_POLISH,
    LLM_USAGE_SOURCE_MATERIAL,
)

PRICE_BAND_PEAK = "peak"
PRICE_BAND_OFFPEAK = "offpeak"

# How one provider attempt ended (``outcome``). NULL: recorded before these
# columns existed, or by a call site that does not report it (material flows).
CALL_OUTCOME_SUCCESS = "success"
CALL_OUTCOME_ERROR = "error"
CALL_OUTCOME_TIMEOUT = "timeout"
CALL_OUTCOME_CANCELLED = "cancelled"
CALL_OUTCOMES = (CALL_OUTCOME_SUCCESS, CALL_OUTCOME_ERROR, CALL_OUTCOME_TIMEOUT, CALL_OUTCOME_CANCELLED)


def has_usage_tokens(columns: Any) -> Any:
    """SQL condition for rows that carry tokens (``LLMUsageEvent`` or its table ``.c``).

    Failed and cancelled attempts are recorded with zero tokens for the model
    metrics; usage, cost, growth and acquisition reports count only rows with
    tokens, which is every row they saw before those attempts were recorded.
    """
    return (columns.cache_hit_tokens + columns.cache_miss_tokens + columns.output_tokens) > 0


class LLMUsageEvent(SQLModel, table=True):
    """Token counts of one model call, priced by band and pricing version.

    Tokens are stored, money is not: cost is derived from the three token
    counts, ``price_band`` and ``pricing_version`` so it stays reproducible.
    """

    __tablename__ = "llm_usage_event"
    __table_args__ = (
        Index("ix_llm_usage_event_occurred_at", "occurred_at"),
        Index("ix_llm_usage_event_user_id_occurred_at", "user_id", "occurred_at"),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    project_id: str | None = Field(default=None, max_length=64)
    source: str = Field(max_length=16)
    model: str = Field(max_length=64)
    cache_hit_tokens: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    cache_miss_tokens: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    output_tokens: int = Field(default=0, sa_column_kwargs={"server_default": "0"})
    price_band: str = Field(max_length=8)
    pricing_version: str = Field(max_length=32)
    # Naive UTC, like every other timestamp column in this codebase.
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    # agent_run_id, novel:<id>, or chat_message:<id> for backfilled rows.
    correlation_id: str | None = Field(default=None, max_length=128)
    is_backfilled: bool = Field(default=False, sa_column_kwargs={"server_default": false()})
    # Model metrics for this provider attempt; NULL = not recorded (never "0" or "success").
    outcome: str | None = Field(default=None, max_length=16)
    # Request sent -> stream finished / response returned / error raised; includes
    # the OpenAI client's own HTTP retries inside this attempt.
    duration_ms: int | None = Field(default=None)
    # Streaming only: request sent -> first output delta (text, reasoning, refusal or
    # tool arguments). The SDK's response.created fires on any first chunk and is not counted.
    ttft_ms: int | None = Field(default=None)
