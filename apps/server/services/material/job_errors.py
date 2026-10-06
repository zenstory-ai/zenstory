"""
Stable failure codes for material ingestion jobs.

``IngestionJob.error_message`` stores only an ``ERR_*`` code that the frontend
translates; raw exception text (internal URLs, container paths, LLM output)
is written to logs and never returned to users.
"""
from __future__ import annotations

import re

from core.error_codes import ErrorCode

_ERROR_CODE_RE = re.compile(r"^ERR_[A-Z0-9_]+$")

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

    def __init__(self, error_code: str, message: str = ""):
        super().__init__(message or error_code)
        self.error_code = error_code

    def __reduce__(self):
        return (self.__class__, (self.error_code, str(self)))


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


__all__ = [
    "REFUNDABLE_JOB_ERROR_CODES",
    "MaterialPipelineError",
    "is_job_error_code",
    "job_error_code_for_exception",
    "public_job_error",
]
