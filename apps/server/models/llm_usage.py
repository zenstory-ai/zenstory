"""Append-only ledger of DeepSeek chat-model calls, one row per model call."""

from datetime import datetime

from sqlalchemy import Index
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
    cache_hit_tokens: int = Field(default=0)
    cache_miss_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    price_band: str = Field(max_length=8)
    pricing_version: str = Field(max_length=32)
    # Naive UTC, like every other timestamp column in this codebase.
    occurred_at: datetime = Field(default_factory=datetime.utcnow)
    # agent_run_id, novel:<id>, or chat_message:<id> for backfilled rows.
    correlation_id: str | None = Field(default=None, max_length=128)
    is_backfilled: bool = Field(default=False)
