"""
File serialization utilities for converting File models to JSON-safe dicts.

This module provides serialization functions that convert SQLModel File objects
into JSON-serializable dictionaries, handling special types like datetime objects.
"""

from datetime import datetime
from typing import Any, Literal, cast

from models import File

QUERY_FILES_RESPONSE_MODE_SUMMARY = "summary"
QUERY_FILES_RESPONSE_MODE_FULL = "full"
QUERY_FILES_DEFAULT_RESPONSE_MODE = QUERY_FILES_RESPONSE_MODE_SUMMARY
QUERY_FILES_DEFAULT_CONTENT_PREVIEW_CHARS = 200

QueryFilesResponseMode = Literal["summary", "full"]


def serialize_file(
    file: File,
    *,
    include_content: bool = True,
    content_preview_chars: int | None = None,
    preloaded_content_preview: str | None = None,
) -> dict[str, Any]:
    """Serialize a File model to a JSON-safe dict.

    This function converts a File model instance into a dictionary that can be
    safely serialized to JSON. It handles datetime objects by converting them
    to ISO format strings. In summary mode (`include_content=False`), it strips
    full content and returns a preview.

    Args:
        file: A File model instance to serialize
        include_content: Whether to include full `content` field
        content_preview_chars: Preview length used when `include_content=False`

    Returns:
        A dictionary representation of the file with datetime fields converted
        to ISO format strings
    """
    if not include_content and preloaded_content_preview is not None:
        data = file.model_dump(exclude={"content"})
    else:
        data = file.model_dump()
    # Convert datetime fields to ISO strings
    if isinstance(data.get("created_at"), datetime):
        data["created_at"] = data["created_at"].isoformat()
    if isinstance(data.get("updated_at"), datetime):
        data["updated_at"] = data["updated_at"].isoformat()

    if include_content:
        return data

    preview_length = _normalize_content_preview_chars(content_preview_chars)
    content = data.pop("content", None) or ""
    if preloaded_content_preview is not None:
        content = preloaded_content_preview
    data["content_preview"] = content[:preview_length]
    return data


def serialize_query_file(
    file: File,
    *,
    response_mode: QueryFilesResponseMode | str = QUERY_FILES_DEFAULT_RESPONSE_MODE,
    content_preview_chars: int = QUERY_FILES_DEFAULT_CONTENT_PREVIEW_CHARS,
    include_content: bool | None = None,
    preloaded_content_preview: str | None = None,
) -> dict[str, Any]:
    """Serialize File for query_files output mode.

    Args:
        file: File model
        response_mode: "summary" or "full"
        content_preview_chars: Preview length for summary mode
        include_content: Backward-compatible override. `True` forces full mode.

    Returns:
        Serialized file payload based on selected mode.
    """
    normalized_mode = _normalize_response_mode(response_mode)
    should_include_content = (
        include_content is True or normalized_mode == QUERY_FILES_RESPONSE_MODE_FULL
    )
    return serialize_file(
        file,
        include_content=should_include_content,
        content_preview_chars=content_preview_chars,
        preloaded_content_preview=preloaded_content_preview,
    )


def _summary_projection_preview_length(
    response_mode: str,
    content_preview_chars: int | None,
    include_content: bool | None,
) -> int | None:
    """Return an eligible summary length without changing per-row error timing."""
    try:
        if (
            include_content is True
            or _normalize_response_mode(response_mode) != QUERY_FILES_RESPONSE_MODE_SUMMARY
        ):
            return None
        return _normalize_content_preview_chars(content_preview_chars)
    except (ValueError, TypeError, AttributeError):
        # Invalid options must still succeed on empty results and fail only when
        # the existing serializer visits a row. Full mode ignores preview errors.
        return None


def _normalize_response_mode(response_mode: str) -> QueryFilesResponseMode:
    mode = (response_mode or QUERY_FILES_DEFAULT_RESPONSE_MODE).strip().lower()
    if mode not in {QUERY_FILES_RESPONSE_MODE_SUMMARY, QUERY_FILES_RESPONSE_MODE_FULL}:
        raise ValueError("response_mode must be 'summary' or 'full'")
    return cast(QueryFilesResponseMode, mode)


def _normalize_content_preview_chars(content_preview_chars: int | None) -> int:
    if content_preview_chars is None:
        return QUERY_FILES_DEFAULT_CONTENT_PREVIEW_CHARS
    if isinstance(content_preview_chars, bool) or not isinstance(content_preview_chars, int):
        raise ValueError("content_preview_chars must be a non-negative integer")
    if content_preview_chars < 0:
        raise ValueError("content_preview_chars must be a non-negative integer")
    return content_preview_chars


__all__ = [
    "serialize_file",
    "serialize_query_file",
    "QUERY_FILES_RESPONSE_MODE_SUMMARY",
    "QUERY_FILES_RESPONSE_MODE_FULL",
    "QUERY_FILES_DEFAULT_RESPONSE_MODE",
    "QUERY_FILES_DEFAULT_CONTENT_PREVIEW_CHARS",
]
