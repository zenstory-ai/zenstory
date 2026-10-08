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
QUERY_FILES_DEFAULT_LIMIT = 50
# 不按 id 的全文列表查询，未显式给 limit 时的默认条数（见 mcp_tools._query_files_sync）。
QUERY_FILES_FULL_MODE_DEFAULT_LIMIT = 10

QueryFilesResponseMode = Literal["summary", "full"]


def serialize_file(
    file: File,
    *,
    include_content: bool = True,
    content_preview_chars: int | None = None,
    preloaded_content_preview: str | None = None,
    preloaded_content_length: int | None = None,
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
        preloaded_content_preview: SQL-projected preview (summary fast path)
        preloaded_content_length: SQL-projected full content length (summary fast path)

    Summary mode also returns ``content_length`` (full content length in chars)
    and ``content_truncated`` (preview shorter than the content), so the model can
    tell a preview apart from a genuinely short file and knows when to read full.

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
    if preloaded_content_preview is not None and isinstance(preloaded_content_length, int):
        content_length = preloaded_content_length
    else:
        # 未走 SQL 投影（或 SQLite 含 NUL 的旧行，投影直接给了完整正文）时，content 就是全文。
        content_length = len(content)
    preview = content[:preview_length]
    data["content_preview"] = preview
    data["content_length"] = content_length
    data["content_truncated"] = content_length > len(preview)
    return data


def serialize_query_file(
    file: File,
    *,
    response_mode: QueryFilesResponseMode | str = QUERY_FILES_DEFAULT_RESPONSE_MODE,
    content_preview_chars: int | None = QUERY_FILES_DEFAULT_CONTENT_PREVIEW_CHARS,
    include_content: bool | None = None,
    preloaded_content_preview: str | None = None,
    preloaded_content_length: int | None = None,
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
        preloaded_content_length=preloaded_content_length,
    )


def resolve_query_files_response_mode(
    response_mode: str | None,
    *,
    file_id: str | None,
    include_content: bool | None,
    content_preview_chars: int | None,
) -> str:
    """决定 query_files 未显式给 response_mode 时用哪种模式。

    按 id 读取默认返回全文：模型按 id 读一个文件，几乎总是要读正文；以前默认
    summary 只给 200 字预览，模型要么误以为文件就这么短，要么紧接着再读一次 full。
    显式参数优先：include_content=True → full；给了 response_mode → 原样使用
    （非法值留给序列化时报错，保持「空结果不报错」的既有时序）；include_content=False
    或显式给了 content_preview_chars（想要预览）→ summary。其余（列表/搜索）默认 summary。
    """
    if include_content is True:
        return QUERY_FILES_RESPONSE_MODE_FULL
    if isinstance(response_mode, str) and response_mode.strip():
        return response_mode
    if response_mode is not None and not isinstance(response_mode, str):
        return response_mode  # 非法类型：交给 _normalize_response_mode 报错
    if include_content is False or content_preview_chars is not None:
        return QUERY_FILES_RESPONSE_MODE_SUMMARY
    if (file_id or "").strip():
        return QUERY_FILES_RESPONSE_MODE_FULL
    return QUERY_FILES_DEFAULT_RESPONSE_MODE


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
    "QUERY_FILES_DEFAULT_LIMIT",
    "QUERY_FILES_FULL_MODE_DEFAULT_LIMIT",
    "resolve_query_files_response_mode",
]
