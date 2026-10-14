"""
Title sequence helpers.

This module centralizes "chapter / episode / section" number extraction from
file titles, so different features (file tree sorting, auto-order inference,
export, stats) can share consistent behavior.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

MIN_FILE_ORDER: Final[int] = -2_147_483_648
MAX_FILE_ORDER: Final[int] = 2_147_483_647


def validate_persisted_file_order(order: int) -> int:
    """Bound the final order to the signed INTEGER storage domain."""
    if not MIN_FILE_ORDER <= order <= MAX_FILE_ORDER:
        raise ValueError(f"File order must be between {MIN_FILE_ORDER} and {MAX_FILE_ORDER}")
    return order


# Chinese number mapping (supports up to 千)
_CHINESE_NUMS: Final[dict[str, int]] = {
    "零": 0,
    "一": 1,
    "二": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
    "百": 100,
    "千": 1000,
}

# Common "第X{unit}" units we want to support.
_SEQ_UNITS: Final[str] = "章集回节话幕场卷"

# Heading patterns are deliberately anchored.  A subtitle such as
# ``第12章 第一节课`` must not let ``第一节`` replace the chapter ordinal.
_RE_CN_SEQ: Final[re.Pattern[str]] = re.compile(
    rf"^\s*第\s*([零一二三四五六七八九十百千]+)\s*([{_SEQ_UNITS}])"
)
_RE_ARABIC_SEQ: Final[re.Pattern[str]] = re.compile(
    rf"^\s*第\s*(\d+)\s*([{_SEQ_UNITS}])"
)
_RE_EN_CHAPTER: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:chapter|episode|ep\.?)\s+(\d+)\b(?!\s*[-–—~～]\s*\d)",
    re.IGNORECASE,
)
_RE_LEADING_NUM: Final[re.Pattern[str]] = re.compile(r"^\s*(\d+)\b")
_SEQUENCE_METADATA_KEYS: Final[tuple[str, ...]] = (
    "chapter_number",
    "episode_number",
    "section_number",
)
_METADATA_KEYS_BY_FILE_TYPE: Final[dict[str, tuple[str, ...]]] = {
    # chapter_number exists on historical draft rows even though the current
    # draft schema no longer advertises it.
    "draft": ("chapter_number",),
    "outline": ("chapter_number", "episode_number", "section_number"),
    "script": ("episode_number", "section_number"),
}


def _coerce_positive_int(value: Any) -> int | None:
    """Coerce common numeric-like values into a positive int."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        token = value.strip()
        if token.isdigit():
            parsed = int(token)
            return parsed if parsed > 0 else None
    return None


def parse_chinese_number(value: str | None) -> int | None:
    """Parse a Chinese number (e.g. 十一 / 二十 / 一百零二) to int."""
    token = (value or "").strip()
    if not token:
        return None

    result = 0
    temp = 0
    for char in token:
        num = _CHINESE_NUMS.get(char)
        if num is None:
            continue
        if num in {10, 100, 1000}:
            if temp == 0:
                temp = 1
            result += temp * num
            temp = 0
        else:
            temp = num
    result += temp

    return result if result > 0 else None


def extract_sequence_number(title: str | None) -> int | None:
    """
    Extract a sequence number from a title.

    Supported examples:
    - 第1章 / 第十章 / 第十一章
    - 第1集 / 第10集
    - Chapter 12
    - 12. Something / 12 Something
    """
    text = (title or "").strip()
    if not text:
        return None

    match = _RE_CN_SEQ.match(text)
    if match:
        parsed = parse_chinese_number(match.group(1))
        if parsed:
            return parsed

    match = _RE_ARABIC_SEQ.match(text)
    if match:
        try:
            parsed = int(match.group(1))
        except ValueError:
            parsed = 0
        return parsed if parsed > 0 else None

    match = _RE_EN_CHAPTER.match(text)
    if match:
        try:
            parsed = int(match.group(1))
        except ValueError:
            parsed = 0
        return parsed if parsed > 0 else None

    match = _RE_LEADING_NUM.match(text)
    if match:
        try:
            parsed = int(match.group(1))
        except ValueError:
            parsed = 0
        return parsed if parsed > 0 else None

    return None


def extract_sequence_number_from_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    file_type: str | None = None,
) -> int | None:
    """
    Extract a sequence number from file metadata.

    Supported keys:
    - chapter_number
    - episode_number
    - section_number
    """
    if not isinstance(metadata, Mapping):
        return None

    keys = (
        _METADATA_KEYS_BY_FILE_TYPE.get(file_type, ())
        if file_type is not None
        else _SEQUENCE_METADATA_KEYS
    )
    for key in keys:
        if (parsed := _coerce_positive_int(metadata.get(key))) is not None:
            return parsed

    return None


def extract_sequence_number_with_metadata(
    title: str | None,
    metadata: Mapping[str, Any] | None = None,
    *,
    file_type: str | None = None,
) -> int | None:
    """Prefer metadata sequence hints, then fall back to title parsing."""
    return (
        extract_sequence_number_from_metadata(metadata, file_type=file_type)
        or extract_sequence_number(title)
    )


def build_sequence_sort_key(
    raw_order: Any,
    *,
    title: str | None,
    metadata: Mapping[str, Any] | None = None,
    file_type: str | None = None,
) -> tuple[int, int]:
    """
    Build a stable sequence-aware sort key.

    Returns:
    - effective order
    - parsed sequence number fallback (or 999999 when unavailable)
    """
    if isinstance(raw_order, bool):
        order_value = 0
    else:
        try:
            order_value = int(raw_order or 0)
        except (TypeError, ValueError):
            order_value = 0

    metadata_sequence = extract_sequence_number_from_metadata(
        metadata, file_type=file_type,
    )
    title_sequence = extract_sequence_number(title)
    sequence_number = metadata_sequence or title_sequence
    effective_order = order_value if order_value != 0 else (sequence_number or 0)
    return effective_order, sequence_number or 999999


def resolve_persisted_sequence_order(
    raw_order: Any,
    *,
    title: str | None,
    metadata: Mapping[str, Any] | None = None,
    file_type: str | None = None,
) -> int:
    """
    Resolve the order value that should be persisted for a file.

    A validated non-zero explicit/stored order is authoritative.  Zero is the
    legacy unset value, so compatible metadata and then the leading title
    ordinal may fill it.
    """
    effective_order, _ = build_sequence_sort_key(
        raw_order,
        title=title,
        metadata=metadata,
        file_type=file_type,
    )
    return validate_persisted_file_order(int(effective_order))


def extract_chapter_like_sequence_number(title: str | None) -> int | None:
    """
    Extract chapter-like sequence numbers only.

    Supported examples:
    - 第1章 / 第十章 / 第11集
    - Chapter 12

    Intentionally excludes generic leading-number fallback to avoid
    over-inferring from non-chapter titles such as dates.
    """
    text = (title or "").strip()
    if not text:
        return None

    match = _RE_CN_SEQ.match(text)
    if match:
        parsed = parse_chinese_number(match.group(1))
        if parsed:
            return parsed

    match = _RE_ARABIC_SEQ.match(text)
    if match:
        try:
            parsed = int(match.group(1))
        except ValueError:
            parsed = 0
        return parsed if parsed > 0 else None

    match = _RE_EN_CHAPTER.match(text)
    if match:
        try:
            parsed = int(match.group(1))
        except ValueError:
            parsed = 0
        return parsed if parsed > 0 else None

    return None
