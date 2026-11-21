"""Canonical token-usage extraction, key normalization, and merging for the writing agent.

Different boundaries inside the agent subsystem receive usage payloads in two
key families:
- OpenAI Chat Completions (``compaction.py``, legacy callers):
  ``prompt_tokens`` / ``completion_tokens`` / ``prompt_tokens_details.cached_tokens``
- OpenAI Agents SDK / Responses (``openai_agents/runner.py``):
  ``input_tokens`` / ``output_tokens`` / ``input_tokens_details.cached_tokens``

Downstream accounting and UI display expect canonical keys where
``cache_read_tokens`` is separated out from ``input_tokens`` (so cached prompt
tokens are never double-counted at full input price + cache-read price):
``input_tokens`` (uncached), ``cache_read_tokens``, ``output_tokens``, ``total_tokens``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

USAGE_KEY_ALIASES: dict[str, str] = {
    "prompt_tokens": "input_tokens",
    "completion_tokens": "output_tokens",
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _field(source: Any, name: str) -> Any:
    if source is None:
        return None
    if isinstance(source, Mapping):
        return source.get(name)
    return getattr(source, name, None)


def _nonneg_int_field(source: Any, name: str) -> int | None:
    value = _field(source, name)
    if not _is_number(value):
        return None
    return max(0, int(value))


def extract_response_usage(usage: Any) -> dict[str, int]:
    """Normalize a single LLM response usage object/mapping into canonical token counts.

    Accepts both Chat Completions (``prompt_tokens`` / ``completion_tokens`` /
    ``prompt_tokens_details.cached_tokens``) and openai-agents SDK ``Usage``
    (``input_tokens`` / ``output_tokens`` / ``input_tokens_details.cached_tokens``).

    ``cached_tokens`` is a subset of prompt/input tokens and is clamped to
    ``[0, prompt]`` before subtraction so malformed upstream payloads never
    produce negative ``input_tokens``. Zero-valued fields are omitted so callers
    can keep the existing ``bool(usage)`` check for whether real usage arrived.
    """
    if usage is None:
        return {}

    prompt = _nonneg_int_field(usage, "prompt_tokens")
    if prompt is None:
        prompt = _nonneg_int_field(usage, "input_tokens")
    prompt = prompt or 0

    cached = _nonneg_int_field(_field(usage, "prompt_tokens_details"), "cached_tokens")
    if cached is None:
        cached = _nonneg_int_field(_field(usage, "input_tokens_details"), "cached_tokens")
    cached = max(0, min(cached or 0, prompt))

    output = _nonneg_int_field(usage, "completion_tokens")
    if output is None:
        output = _nonneg_int_field(usage, "output_tokens")

    total = _nonneg_int_field(usage, "total_tokens") or 0

    totals = {
        "input_tokens": prompt - cached,
        "output_tokens": output or 0,
        "total_tokens": total,
        "cache_read_tokens": cached,
    }
    return {key: value for key, value in totals.items() if value}


def sum_response_usages(usages: Iterable[Any]) -> dict[str, int]:
    """Sum canonical usage dicts extracted from a sequence of raw response usage objects."""
    totals: dict[str, int] = {}
    for usage in usages:
        for key, value in extract_response_usage(usage).items():
            totals[key] = totals.get(key, 0) + value
    return totals


def normalize_usage_keys(usage: Mapping[str, Any]) -> dict[str, Any]:
    """Fold legacy Chat Completions alias keys onto canonical keys (summing numeric collisions)."""
    normalized: dict[str, Any] = {}
    for key, value in usage.items():
        canonical = USAGE_KEY_ALIASES.get(key, key)
        previous = normalized.get(canonical)
        if _is_number(value) and _is_number(previous):
            normalized[canonical] = previous + value
        else:
            normalized[canonical] = value
    return normalized


def merge_usage_dicts(
    accumulated: dict[str, Any] | None,
    incoming: Any,
) -> dict[str, Any] | None:
    """Merge an incoming ``MESSAGE_END`` usage dict into ``accumulated``.

    Normalizes alias keys on both sides, sums numeric fields, keeps the latest
    value for non-numeric fields, and ignores non-mapping ``incoming`` payloads.
    """
    if not isinstance(incoming, Mapping):
        return accumulated
    normalized_incoming = normalize_usage_keys(incoming)
    if not isinstance(accumulated, Mapping):
        return normalized_incoming

    merged: dict[str, Any] = normalize_usage_keys(accumulated)
    for key, value in normalized_incoming.items():
        previous = merged.get(key)
        if _is_number(value) and _is_number(previous):
            merged[key] = previous + value
        else:
            merged[key] = value
    return merged
