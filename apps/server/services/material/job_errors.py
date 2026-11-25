"""
Stable failure codes for material ingestion jobs.

``IngestionJob.error_message`` stores only an ``ERR_*`` code that the frontend
translates; raw exception text (internal URLs, container paths, LLM output)
is written to logs and never returned to users.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

from core.error_codes import ErrorCode

_ERROR_CODE_RE = re.compile(r"^ERR_[A-Z0-9_]+$")
_SAFE_TYPE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

MAX_PERSISTED_CHAPTER_FAILURES = 100

MATERIAL_FAILURE_REASONS = frozenset(
    {
        "content_filtered",
        "context_too_long",
        "request_rejected",
        "rate_limited",
        "timeout",
        "connection_error",
        "upstream_server_error",
        "llm_unavailable",
        "budget_exceeded",
        "invalid_llm_output",
        "validation_error",
        "transient_llm_error",
        "unexpected_error",
    }
)

_ALLOWED_CHAPTER_CAPABILITIES = frozenset({"summaries", "plots", "mentions"})
_ALLOWED_CHAPTER_STAGES = frozenset(
    {"stage1", "stage1.summaries", "stage1.plots", "stage1.mentions"}
)

_CONTENT_FILTER_PROVIDER_CODES = frozenset({1026, 1027})
_CONTENT_FILTER_CODE_STRINGS = frozenset(
    {
        "1026",
        "1027",
        "image_content_policy_violation",
        "content_policy_violation",
        "content_filter",
        "new_sensitive",
    }
)
_CONTENT_FILTER_PROTOCOL_MARKERS = (
    "output new_sensitive",
    "input new_sensitive",
    "(1027)",
    "(1026)",
    "image_content_policy_violation",
    "content_policy_violation",
)
_CONTEXT_TOO_LONG_PROVIDER_CODES = frozenset({1039})
_KNOWN_PROVIDER_ERROR_CODES = frozenset(
    {
        1000,
        1001,
        1002,
        1004,
        1008,
        1024,
        1026,
        1027,
        1033,
        1039,
        1041,
        1042,
        1046,
        2013,
        2014,
        2022,
        2045,
        2046,
        2047,
        2049,
        2053,
        2056,
        2057,
        2061,
        2062,
        2064,
        2066,
        2067,
        2068,
        2069,
    }
)
_BUDGET_ERROR_CODES = frozenset(
    {
        ErrorCode.MATERIAL_TRIAL_COST_LIMIT,
        ErrorCode.QUOTA_AI_DAILY_COST_EXCEEDED,
        ErrorCode.AI_COST_BUDGET_UNAVAILABLE,
        ErrorCode.MATERIAL_TRIAL_LIMIT_INVALID,
    }
)

# Failures caused by the platform or rejected before any LLM work: the job's
# quota is refunded so the user can retry at the normal price.
REFUNDABLE_JOB_ERROR_CODES = frozenset(
    {
        ErrorCode.MATERIAL_DISPATCH_FAILED,
        ErrorCode.MATERIAL_DISPATCH_TIMEOUT,
        ErrorCode.MATERIAL_PROCESSING_TIMEOUT,
        ErrorCode.MATERIAL_LLM_UNAVAILABLE,
        ErrorCode.MATERIAL_NO_CHAPTERS,
        ErrorCode.MATERIAL_TOO_MANY_CHAPTERS,
        ErrorCode.MATERIAL_FILE_UNREADABLE,
    }
)


class MaterialPipelineError(Exception):
    """A decomposition failure with a user-facing error code."""

    def __init__(
        self,
        error_code: str,
        message: str = "",
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message or error_code)
        self.error_code = error_code
        self.details = details

    def __reduce__(self):
        return (self.__class__, (self.error_code, str(self), self.details))


def is_job_error_code(value: str | None) -> bool:
    return bool(value) and bool(_ERROR_CODE_RE.match(value or ""))


def job_error_code_for_exception(exc: BaseException) -> str:
    """Return the error code carried by ``exc`` or its causes, else the generic code."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        code = getattr(current, "error_code", None)
        if isinstance(code, str) and is_job_error_code(code):
            return code
        current = current.__cause__ or current.__context__
    return ErrorCode.MATERIAL_DECOMPOSE_FAILED


def public_job_error(error_message: str | None) -> str | None:
    """Map a stored job error to what the API returns.

    Codes pass through; legacy free-text messages (raw exceptions written
    before codes existed) collapse to the generic decomposition failure code.
    """
    if not error_message:
        return None
    if is_job_error_code(error_message):
        return error_message
    return ErrorCode.MATERIAL_DECOMPOSE_FAILED


def _safe_type_name(exc: BaseException | str) -> str:
    name = exc if isinstance(exc, str) else type(exc).__name__
    if isinstance(name, str) and _SAFE_TYPE_NAME_RE.fullmatch(name):
        return name
    return "Exception"


def _safe_http_status(value: Any) -> int | None:
    if type(value) is int and 100 <= value <= 599:
        return value
    return None


def _safe_provider_code(value: Any) -> int | None:
    if type(value) is int and value in _KNOWN_PROVIDER_ERROR_CODES:
        return value
    if isinstance(value, str) and value.isdigit():
        parsed = int(value)
        if parsed in _KNOWN_PROVIDER_ERROR_CODES:
            return parsed
    return None


def _extract_provider_protocol_signals(exc: BaseException) -> tuple[int | None, int | None, bool]:
    """Extract safe HTTP status, known provider error code, and content-filter signal.

    Reads only exception instance data (``vars(exc)``) rather than arbitrary custom
    properties, and never returns raw provider text.
    """
    try:
        fields = vars(exc)
    except TypeError:
        fields = {}

    status = _safe_http_status(fields.get("status_code"))
    provider_code = _safe_provider_code(fields.get("provider_error_code"))
    content_filtered = False

    is_openai_error = False
    try:
        import openai

        is_openai_error = isinstance(exc, openai.APIError)
    except ImportError:  # pragma: no cover - openai is a hard dependency
        pass

    if not is_openai_error:
        return status, provider_code, content_filtered

    body = fields.get("body")
    error_obj: dict[str, Any] | None = None
    base_resp: dict[str, Any] | None = None
    if isinstance(body, dict):
        raw_base_resp = body.get("base_resp")
        if isinstance(raw_base_resp, dict):
            base_resp = raw_base_resp
        raw_error = body.get("error")
        if isinstance(raw_error, dict):
            error_obj = raw_error

    code_candidates: list[Any] = [
        base_resp.get("status_code") if base_resp else None,
        fields.get("code"),
        fields.get("type"),
        body.get("code") if isinstance(body, dict) else None,
        body.get("type") if isinstance(body, dict) else None,
        error_obj.get("code") if error_obj else None,
        error_obj.get("type") if error_obj else None,
    ]
    for candidate in code_candidates:
        if provider_code is None:
            provider_code = _safe_provider_code(candidate)
        if (
            isinstance(candidate, str)
            and candidate.strip().lower() in _CONTENT_FILTER_CODE_STRINGS
        ) or (type(candidate) is int and candidate in _CONTENT_FILTER_PROVIDER_CODES):
            content_filtered = True

    if status in {400, 422}:
        message_candidates: list[Any] = [
            fields.get("message"),
            body.get("message") if isinstance(body, dict) else None,
            error_obj.get("message") if error_obj else None,
            base_resp.get("status_msg") if base_resp else None,
        ]
        for raw_msg in message_candidates:
            if not isinstance(raw_msg, str):
                continue
            lowered = raw_msg.lower()
            if any(marker in lowered for marker in _CONTENT_FILTER_PROTOCOL_MARKERS):
                content_filtered = True
            if provider_code is None:
                if "(1027)" in lowered:
                    provider_code = 1027
                elif "(1026)" in lowered:
                    provider_code = 1026

    if provider_code in _CONTENT_FILTER_PROVIDER_CODES:
        content_filtered = True

    return status, provider_code, content_filtered


def _is_timeout_exception(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    try:
        import openai

        if isinstance(exc, openai.APITimeoutError):
            return True
    except ImportError:  # pragma: no cover
        pass
    for mod_name in ("httpx", "httpx2"):
        try:
            mod = __import__(mod_name)
        except ImportError:  # pragma: no cover
            continue
        timeout_exc = getattr(mod, "TimeoutException", None)
        if isinstance(timeout_exc, type) and isinstance(exc, timeout_exc):
            return True
    return False


def _is_connection_exception(exc: BaseException) -> bool:
    if isinstance(exc, ConnectionError):
        return True
    try:
        import openai

        if isinstance(exc, openai.APIConnectionError):
            return True
    except ImportError:  # pragma: no cover
        pass
    for mod_name in ("httpx", "httpx2"):
        try:
            mod = __import__(mod_name)
        except ImportError:  # pragma: no cover
            continue
        transport_exc = getattr(mod, "TransportError", None)
        if isinstance(transport_exc, type) and isinstance(exc, transport_exc):
            return True
    return False


def classify_material_exception(exc: BaseException) -> dict[str, Any]:
    """Classify a material ingestion exception into safe, bounded structured metadata.

    Never returns raw exception text, response prose, URLs, paths, or secrets.
    """
    from flows.utils.helpers.exceptions import (
        LLMAPIError,
        LLMNonRetryableError,
        LLMOutputError,
        ValidationError,
    )

    try:
        fields = vars(exc)
    except TypeError:
        fields = {}

    error_type = _safe_type_name(exc)
    raw_error_code = fields.get("error_code")
    error_code = raw_error_code if isinstance(raw_error_code, str) and is_job_error_code(raw_error_code) else None

    preset_reason = fields.get("failure_reason")
    http_status = _safe_http_status(fields.get("http_status"))
    provider_error_code = _safe_provider_code(fields.get("provider_error_code"))

    reason: str | None = (
        preset_reason
        if isinstance(preset_reason, str) and preset_reason in MATERIAL_FAILURE_REASONS
        else None
    )

    if reason is None:
        if isinstance(exc, LLMOutputError):
            reason = "invalid_llm_output"
        elif isinstance(exc, ValidationError):
            reason = "validation_error"
        elif error_code in _BUDGET_ERROR_CODES:
            reason = "budget_exceeded"
        elif error_code == ErrorCode.MATERIAL_LLM_UNAVAILABLE:
            reason = "llm_unavailable"

    # Inspect direct exception or underlying cause for transport/HTTP/provider codes.
    if reason is None or http_status is None or provider_error_code is None:
        targets: list[BaseException] = [exc]
        if exc.__cause__ is not None and exc.__cause__ is not exc:
            targets.append(exc.__cause__)
        for target in targets:
            if reason is None:
                if _is_timeout_exception(target):
                    reason = "timeout"
                elif _is_connection_exception(target):
                    reason = "connection_error"
            t_status, t_provider_code, content_filtered = _extract_provider_protocol_signals(target)
            if http_status is None and t_status is not None:
                http_status = t_status
            if provider_error_code is None and t_provider_code is not None:
                provider_error_code = t_provider_code
            if reason is None:
                if content_filtered:
                    reason = "content_filtered"
                elif t_status == 413 or t_provider_code in _CONTEXT_TOO_LONG_PROVIDER_CODES:
                    reason = "context_too_long"
                elif t_status == 429:
                    reason = "rate_limited"
                elif t_status is not None and t_status >= 500:
                    reason = "upstream_server_error"
                elif t_status in {401, 402, 403, 404}:
                    reason = "llm_unavailable"
                elif t_status in {400, 422}:
                    reason = "request_rejected"

    if reason is None:
        if isinstance(exc, LLMNonRetryableError):
            reason = "request_rejected"
        elif isinstance(exc, LLMAPIError):
            reason = "transient_llm_error"
        else:
            reason = "unexpected_error"

    result: dict[str, Any] = {
        "reason": reason,
        "error_type": error_type,
    }
    if error_code is not None:
        result["error_code"] = error_code
    if http_status is not None:
        result["http_status"] = http_status
    if provider_error_code is not None:
        result["provider_error_code"] = provider_error_code
    return result


def build_chapter_failure_detail(
    *,
    chapter_id: int,
    capability: str,
    exc: BaseException,
    chapter_index: int | None = None,
) -> dict[str, Any]:
    """Build one sanitized per-chapter failure record for persistence in ``error_details``."""
    safe_capability = capability if capability in _ALLOWED_CHAPTER_CAPABILITIES else "summaries"
    classified = classify_material_exception(exc)
    detail: dict[str, Any] = {
        "chapter_id": int(chapter_id),
        "stage": f"stage1.{safe_capability}",
        "capability": safe_capability,
        "reason": classified["reason"],
        "error_type": classified["error_type"],
    }
    if type(chapter_index) is int and chapter_index > 0:
        detail["chapter_index"] = chapter_index
    for optional_key in ("error_code", "http_status", "provider_error_code"):
        if optional_key in classified:
            detail[optional_key] = classified[optional_key]
    return detail


def sanitize_chapter_failure_entry(entry: Any) -> dict[str, Any] | None:
    """Validate and whitelist one chapter failure entry (e.g. loaded from checkpoint)."""
    if not isinstance(entry, dict):
        return None
    chapter_id = entry.get("chapter_id")
    if type(chapter_id) is not int or chapter_id <= 0:
        return None
    capability = entry.get("capability")
    stage = entry.get("stage")
    safe_capability = capability if isinstance(capability, str) and capability in _ALLOWED_CHAPTER_CAPABILITIES else None
    if isinstance(stage, str) and stage in _ALLOWED_CHAPTER_STAGES:
        safe_stage = stage
    elif safe_capability is not None:
        safe_stage = f"stage1.{safe_capability}"
    else:
        safe_stage = "stage1"
    raw_reason = entry.get("reason")
    reason = raw_reason if isinstance(raw_reason, str) and raw_reason in MATERIAL_FAILURE_REASONS else "unexpected_error"
    raw_error_type = entry.get("error_type")
    error_type = _safe_type_name(raw_error_type) if isinstance(raw_error_type, str) else "Exception"

    clean: dict[str, Any] = {
        "chapter_id": chapter_id,
        "stage": safe_stage,
        "reason": reason,
        "error_type": error_type,
    }
    if safe_capability is not None:
        clean["capability"] = safe_capability
    chapter_index = entry.get("chapter_index")
    if type(chapter_index) is int and chapter_index > 0:
        clean["chapter_index"] = chapter_index
    error_code = entry.get("error_code")
    if isinstance(error_code, str) and is_job_error_code(error_code):
        clean["error_code"] = error_code
    http_status = _safe_http_status(entry.get("http_status"))
    if http_status is not None:
        clean["http_status"] = http_status
    provider_error_code = _safe_provider_code(entry.get("provider_error_code"))
    if provider_error_code is not None:
        clean["provider_error_code"] = provider_error_code
    return clean


def summarize_failure_reasons(exceptions: list[BaseException]) -> dict[str, int]:
    """Count exceptions by safe categorized reason."""
    counts: Counter[str] = Counter()
    for exc in exceptions:
        counts[classify_material_exception(exc)["reason"]] += 1
    return dict(sorted(counts.items()))


def _sanitize_reason_counts(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    cleaned: dict[str, int] = {}
    for key, value in raw.items():
        if isinstance(key, str) and key in MATERIAL_FAILURE_REASONS and type(value) is int and value > 0:
            cleaned[key] = value
    return dict(sorted(cleaned.items()))


def build_partial_completion_error_details(
    *,
    chapter_ids: list[int],
    stage1_result: dict[str, Any] | None,
    story_result: dict[str, Any] | None = None,
    character_entity_result: dict[str, Any] | None = None,
    relationship_result: dict[str, Any] | None = None,
    meta_failure_detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build sanitized, structured ``error_details`` for ``completed_with_errors`` jobs."""
    stage1 = stage1_result or {}
    story = story_result or {}
    character = character_entity_result or {}
    relationship = relationship_result or {}

    chapter_index_by_id = {
        cid: idx
        for idx, cid in enumerate(chapter_ids, start=1)
        if type(cid) is int and cid > 0
    }

    sanitized_chapters: list[dict[str, Any]] = []
    seen_chapter_keys: set[tuple[int, str]] = set()
    raw_chapter_failures = stage1.get("chapter_failures")
    if isinstance(raw_chapter_failures, list):
        for raw_entry in raw_chapter_failures:
            entry = sanitize_chapter_failure_entry(raw_entry)
            if entry is None:
                continue
            if "chapter_index" not in entry and entry["chapter_id"] in chapter_index_by_id:
                entry["chapter_index"] = chapter_index_by_id[entry["chapter_id"]]
            dedupe_key = (entry["chapter_id"], entry["stage"])
            if dedupe_key in seen_chapter_keys:
                continue
            seen_chapter_keys.add(dedupe_key)
            sanitized_chapters.append(entry)

    # Backfill minimal safe records if a legacy checkpoint recorded failed chapter IDs
    # without detailed failure entries.
    covered_chapter_ids = {entry["chapter_id"] for entry in sanitized_chapters}
    fallback_lists = (
        ("failed_chapter_ids", "summaries"),
        ("failed_plot_chapter_ids", "plots"),
        ("failed_mention_chapter_ids", "mentions"),
        ("failed_mention_chapters", "mentions"),
    )
    for field_name, capability in fallback_lists:
        raw_ids = stage1.get(field_name)
        if not isinstance(raw_ids, list):
            continue
        stage_name = f"stage1.{capability}"
        for cid in raw_ids:
            if type(cid) is not int or cid <= 0:
                continue
            dedupe_key = (cid, stage_name)
            if dedupe_key in seen_chapter_keys:
                continue
            seen_chapter_keys.add(dedupe_key)
            covered_chapter_ids.add(cid)
            fallback_entry: dict[str, Any] = {
                "chapter_id": cid,
                "stage": stage_name,
                "capability": capability,
                "reason": "unexpected_error",
                "error_type": "Exception",
            }
            if cid in chapter_index_by_id:
                fallback_entry["chapter_index"] = chapter_index_by_id[cid]
            sanitized_chapters.append(fallback_entry)

    raw_failed_chapters = stage1.get("failed_chapters")
    if isinstance(raw_failed_chapters, list):
        for cid in raw_failed_chapters:
            if type(cid) is not int or cid <= 0 or cid in covered_chapter_ids:
                continue
            covered_chapter_ids.add(cid)
            fallback_entry = {
                "chapter_id": cid,
                "stage": "stage1",
                "reason": "unexpected_error",
                "error_type": "Exception",
            }
            if cid in chapter_index_by_id:
                fallback_entry["chapter_index"] = chapter_index_by_id[cid]
            sanitized_chapters.append(fallback_entry)

    reason_counts: Counter[str] = Counter()
    for entry in sanitized_chapters:
        reason_counts[entry["reason"]] += 1

    stage_failures: list[dict[str, Any]] = []
    meta_extraction = stage1.get("meta_extraction")
    if isinstance(meta_extraction, dict) and meta_extraction.get("succeeded") is False:
        meta_entry: dict[str, Any] = {
            "stage": "stage1.meta",
            "error_code": (
                meta_extraction["error_code"]
                if isinstance(meta_extraction.get("error_code"), str)
                and is_job_error_code(meta_extraction["error_code"])
                else "ERR_MATERIAL_META_EXTRACTION_FAILED"
            ),
            "reason": "unexpected_error",
            "error_type": "Exception",
        }
        if isinstance(meta_failure_detail, dict):
            raw_reason = meta_failure_detail.get("reason")
            if isinstance(raw_reason, str) and raw_reason in MATERIAL_FAILURE_REASONS:
                meta_entry["reason"] = raw_reason
            raw_type = meta_failure_detail.get("error_type")
            if isinstance(raw_type, str):
                meta_entry["error_type"] = _safe_type_name(raw_type)
            meta_status = _safe_http_status(meta_failure_detail.get("http_status"))
            if meta_status is not None:
                meta_entry["http_status"] = meta_status
            meta_provider_code = _safe_provider_code(meta_failure_detail.get("provider_error_code"))
            if meta_provider_code is not None:
                meta_entry["provider_error_code"] = meta_provider_code
        reason_counts[meta_entry["reason"]] += 1
        stage_failures.append(meta_entry)

    failed_stories = story.get("failed_stories")
    story_failed_count = len(failed_stories) if isinstance(failed_stories, list) else 0
    if story_failed_count > 0:
        story_reasons = _sanitize_reason_counts(story.get("story_failure_reasons"))
        if story_reasons:
            for r, c in story_reasons.items():
                reason_counts[r] += c
        else:
            reason_counts["unexpected_error"] += story_failed_count
        story_entry: dict[str, Any] = {
            "stage": "stage2.stories",
            "failed_count": story_failed_count,
        }
        if story_reasons:
            story_entry["reason_counts"] = story_reasons
        stage_failures.append(story_entry)

    char_failed_count = character.get("failed_count", 0)
    if type(char_failed_count) is not int or char_failed_count < 0:
        char_failed_count = 0
    if char_failed_count == 0 and isinstance(character.get("failed_characters"), list):
        char_failed_count = len(character["failed_characters"])
    if char_failed_count > 0:
        char_reasons = _sanitize_reason_counts(character.get("character_failure_reasons"))
        if char_reasons:
            for r, c in char_reasons.items():
                reason_counts[r] += c
        else:
            reason_counts["unexpected_error"] += char_failed_count
        char_entry: dict[str, Any] = {
            "stage": "stage2.characters",
            "failed_count": char_failed_count,
        }
        if char_reasons:
            char_entry["reason_counts"] = char_reasons
        stage_failures.append(char_entry)

    rel_status = relationship.get("status")
    if rel_status not in (None, "completed", "skipped"):
        reason_counts["unexpected_error"] += 1
        stage_failures.append(
            {
                "stage": "stage2.relationships",
                "failed_count": 1,
                "reason": "unexpected_error",
            }
        )

    stage1_failed_count = stage1.get("failed_count", 0)
    if type(stage1_failed_count) is not int or stage1_failed_count < 0:
        stage1_failed_count = len(covered_chapter_ids)
    total_failed_count = (
        max(stage1_failed_count, len(covered_chapter_ids))
        + story_failed_count
        + char_failed_count
        + int(isinstance(meta_extraction, dict) and meta_extraction.get("succeeded") is False)
        + int(rel_status not in (None, "completed", "skipped"))
    )

    details: dict[str, Any] = {
        "stage": "completed",
        "error_code": ErrorCode.MATERIAL_PARTIALLY_COMPLETED,
        "failed_count": total_failed_count,
        "reason_counts": dict(sorted(reason_counts.items())),
        "chapter_failures": sanitized_chapters[:MAX_PERSISTED_CHAPTER_FAILURES],
    }
    if len(sanitized_chapters) > MAX_PERSISTED_CHAPTER_FAILURES:
        details["chapter_failures_truncated"] = True
        details["total_chapter_failures"] = len(sanitized_chapters)
    if stage_failures:
        details["stage_failures"] = stage_failures
    return details


__all__ = [
    "MATERIAL_FAILURE_REASONS",
    "MAX_PERSISTED_CHAPTER_FAILURES",
    "REFUNDABLE_JOB_ERROR_CODES",
    "MaterialPipelineError",
    "build_chapter_failure_detail",
    "build_partial_completion_error_details",
    "classify_material_exception",
    "is_job_error_code",
    "job_error_code_for_exception",
    "public_job_error",
    "sanitize_chapter_failure_entry",
    "summarize_failure_reasons",
]
