"""Write per-call LLM usage rows to the ``llm_usage_event`` ledger.

Metering must never break a user request: every public function here catches
and logs its own errors, and writes through its own short-lived session (never
the caller's, which may be mid-transaction or shared with agent tools).
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlmodel import Session

from config.datetime_utils import utcnow
from models.llm_usage import LLMUsageEvent
from services.usage.pricing import PRICING_VERSION, naive_utc, price_band
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

SessionFactory = Callable[[], Session]


def _default_session_factory() -> Session:
    from database import create_session

    return create_session()


# Tests point this at their own database.
_session_factory: SessionFactory = _default_session_factory


@dataclass(frozen=True)
class UsageTokens:
    cache_hit: int = 0
    cache_miss: int = 0
    output: int = 0

    @property
    def total(self) -> int:
        return self.cache_hit + self.cache_miss + self.output


@dataclass(frozen=True)
class LLMUsageAttribution:
    """Who a model call is billed to; passed down to the call site."""

    user_id: str | None
    source: str
    project_id: str | None = None
    correlation_id: str | None = None


def _field(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _int_field(source: Any, name: str) -> int | None:
    """Integer usage field, or None when absent or not a real number."""
    value = _field(source, name)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return max(0, int(value))


def extract_usage_tokens(usage: Any) -> UsageTokens:
    """Split any usage object/dict into cache-hit, cache-miss and output tokens.

    Accepts Chat Completions usage (``prompt_tokens`` / ``completion_tokens``)
    and the openai-agents ``Usage`` (``input_tokens`` / ``output_tokens``).
    DeepSeek's ``prompt_cache_hit_tokens`` / ``prompt_cache_miss_tokens`` win
    when present; otherwise the hit count comes from
    ``prompt_tokens_details.cached_tokens`` (or ``input_tokens_details``) and
    the miss count is prompt minus hit. Output includes reasoning tokens.
    """
    if usage is None:
        return UsageTokens()

    prompt = _int_field(usage, "prompt_tokens")
    if prompt is None:
        prompt = _int_field(usage, "input_tokens")
    output = _int_field(usage, "completion_tokens")
    if output is None:
        output = _int_field(usage, "output_tokens")

    hit = _int_field(usage, "prompt_cache_hit_tokens")
    miss = _int_field(usage, "prompt_cache_miss_tokens")
    if hit is None:
        hit = _int_field(_field(usage, "prompt_tokens_details"), "cached_tokens")
    if hit is None:
        hit = _int_field(_field(usage, "input_tokens_details"), "cached_tokens")
    hit = hit or 0
    if miss is None:
        miss = max(0, (prompt or 0) - hit)
    return UsageTokens(cache_hit=hit, cache_miss=miss, output=output or 0)


def build_usage_event(
    *,
    user_id: str,
    source: str,
    model: str,
    tokens: UsageTokens,
    project_id: str | None = None,
    correlation_id: str | None = None,
    occurred_at: datetime | None = None,
    is_backfilled: bool = False,
) -> LLMUsageEvent:
    moment = naive_utc(occurred_at or utcnow())
    return LLMUsageEvent(
        user_id=user_id,
        project_id=project_id,
        source=source,
        model=model,
        cache_hit_tokens=tokens.cache_hit,
        cache_miss_tokens=tokens.cache_miss,
        output_tokens=tokens.output,
        price_band=price_band(moment),
        pricing_version=PRICING_VERSION,
        occurred_at=moment,
        correlation_id=correlation_id,
        is_backfilled=is_backfilled,
    )


def record_llm_usage(
    attribution: LLMUsageAttribution | None,
    *,
    model: str,
    usage: Any = None,
    tokens: UsageTokens | None = None,
    occurred_at: datetime | None = None,
    session_factory: SessionFactory | None = None,
) -> bool:
    """Insert one ledger row. Returns True when a row was written.

    Skips silently when there is no user to bill or no tokens; swallows and
    logs every error.
    """
    try:
        if attribution is None or not attribution.user_id:
            return False
        counted = tokens if tokens is not None else extract_usage_tokens(usage)
        if counted.total <= 0:
            return False
        event = build_usage_event(
            user_id=attribution.user_id,
            source=attribution.source,
            model=model,
            tokens=counted,
            project_id=attribution.project_id,
            correlation_id=attribution.correlation_id,
            occurred_at=occurred_at,
        )
        session = (session_factory or _session_factory)()
        try:
            session.add(event)
            session.commit()
        finally:
            session.close()
        return True
    except Exception as exc:
        log_with_context(
            logger,
            40,  # ERROR
            "LLM usage metering failed",
            source=getattr(attribution, "source", None),
            user_id=getattr(attribution, "user_id", None),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False


async def record_llm_usage_async(
    attribution: LLMUsageAttribution | None,
    *,
    model: str,
    usage: Any = None,
    tokens: UsageTokens | None = None,
) -> bool:
    """Async wrapper: extracts tokens on the loop, writes off the loop."""
    try:
        if attribution is None or not attribution.user_id:
            return False
        counted = tokens if tokens is not None else extract_usage_tokens(usage)
        if counted.total <= 0:
            return False
        # Stamp the call time now, not when the worker thread gets to run.
        occurred_at = utcnow()
        return await asyncio.to_thread(
            record_llm_usage,
            attribution,
            model=model,
            tokens=counted,
            occurred_at=occurred_at,
        )
    except Exception as exc:
        log_with_context(
            logger,
            40,  # ERROR
            "LLM usage metering failed",
            source=getattr(attribution, "source", None),
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False


# ---------------------------------------------------------------------------
# Material flows: bill the novel owner
# ---------------------------------------------------------------------------

_owner_cache_lock = threading.Lock()
_novel_owner_cache: dict[int, str] = {}
_chapter_novel_cache: dict[int, int] = {}


def resolve_material_owner(
    session: Session,
    *,
    novel_id: int | None = None,
    chapter_id: int | None = None,
) -> tuple[str | None, int | None]:
    """Return (user_id, novel_id) of the novel that a flow call works on."""
    from models.material_models import Chapter, Novel

    if novel_id is None and chapter_id is not None:
        with _owner_cache_lock:
            novel_id = _chapter_novel_cache.get(chapter_id)
        if novel_id is None:
            chapter = session.get(Chapter, chapter_id)
            if chapter is None:
                return None, None
            novel_id = chapter.novel_id
            with _owner_cache_lock:
                _chapter_novel_cache[chapter_id] = novel_id
    if novel_id is None:
        return None, None
    with _owner_cache_lock:
        owner = _novel_owner_cache.get(novel_id)
    if owner is None:
        novel = session.get(Novel, novel_id)
        if novel is None:
            return None, novel_id
        owner = novel.user_id
        with _owner_cache_lock:
            _novel_owner_cache[novel_id] = owner
    return owner, novel_id


def record_material_llm_usage(
    *,
    model: str,
    usage: Any,
    novel_id: int | None = None,
    chapter_id: int | None = None,
    session_factory: SessionFactory | None = None,
) -> bool:
    """Meter one material-flow call, billed to the owner of the novel."""
    from models.llm_usage import LLM_USAGE_SOURCE_MATERIAL

    try:
        if novel_id is None and chapter_id is None:
            return False
        tokens = extract_usage_tokens(usage)
        if tokens.total <= 0:
            return False
        factory = session_factory or _session_factory
        session = factory()
        try:
            user_id, resolved_novel_id = resolve_material_owner(
                session, novel_id=novel_id, chapter_id=chapter_id
            )
        finally:
            session.close()
        if not user_id:
            log_with_context(
                logger,
                30,  # WARNING
                "LLM usage metering skipped: novel owner not found",
                novel_id=novel_id,
                chapter_id=chapter_id,
            )
            return False
        attribution = LLMUsageAttribution(
            user_id=user_id,
            source=LLM_USAGE_SOURCE_MATERIAL,
            correlation_id=f"novel:{resolved_novel_id}",
        )
        return record_llm_usage(
            attribution, model=model, tokens=tokens, session_factory=factory
        )
    except Exception as exc:
        log_with_context(
            logger,
            40,  # ERROR
            "LLM usage metering failed",
            source="material",
            novel_id=novel_id,
            chapter_id=chapter_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False
