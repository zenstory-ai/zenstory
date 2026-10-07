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

# Regex patterns (ordered by specificity)
_RE_CN_SEQ: Final[re.Pattern[str]] = re.compile(
    rf"第([零一二三四五六七八九十百千]+)([{_SEQ_UNITS}])"
)
_RE_ARABIC_SEQ: Final[re.Pattern[str]] = re.compile(rf"第(\d+)([{_SEQ_UNITS}])")
_RE_EN_CHAPTER: Final[re.Pattern[str]] = re.compile(r"chapter\s+(\d+)\b", re.IGNORECASE)
_RE_LEADING_NUM: Final[re.Pattern[str]] = re.compile(r"^(\d+)\b")
_SEQUENCE_METADATA_KEYS: Final[tuple[str, ...]] = (
    "chapter_number",
    "episode_number",
    "section_number",
)
_SEQUENCE_LOCKED_FILE_TYPES: Final[frozenset[str]] = frozenset(
    {"draft", "outline", "script"}
)


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

    match = _RE_CN_SEQ.search(text)
    if match:
        parsed = parse_chinese_number(match.group(1))
        if parsed:
            return parsed

    match = _RE_ARABIC_SEQ.search(text)
    if match:
        try:
            parsed = int(match.group(1))
        except ValueError:
            parsed = 0
        return parsed if parsed > 0 else None

    match = _RE_EN_CHAPTER.search(text)
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

    for key in _SEQUENCE_METADATA_KEYS:
        if (parsed := _coerce_positive_int(metadata.get(key))) is not None:
            return parsed

    return None


def extract_sequence_number_with_metadata(
    title: str | None,
    metadata: Mapping[str, Any] | None = None,
) -> int | None:
    """Prefer metadata sequence hints, then fall back to title parsing."""
    return extract_sequence_number_from_metadata(metadata) or extract_sequence_number(title)


def extract_title_first_sequence_number(
    title: str | None,
    metadata: Mapping[str, Any] | None = None,
) -> int | None:
    """Prefer the visible title sequence, then fall back to metadata hints."""
    return extract_sequence_number(title) or extract_sequence_number_from_metadata(metadata)


def has_sequence_locked_order(
    file_type: str | None,
    *,
    sequence_number: int | None,
) -> bool:
    """
    Whether a file's order should be locked to its parsed chapter/episode number.

    For writing artifacts with explicit chapter/episode titles, title sequence is
    the canonical ordering source; stored `order` is treated as derived data.
    """
    return bool(file_type in _SEQUENCE_LOCKED_FILE_TYPES and sequence_number is not None)


def normalize_explicit_sequence_order(
    order_value: int | None,
    *,
    sequence_number: int | None,
) -> int | None:
    """
    Normalize obviously mistyped explicit order values for chapter-like files.

    The common bad case we want to recover from is an LLM passing values such as
    580 for "第58章" (i.e. appending one or more trailing zeros). We only
    normalize that narrow pattern so intentional custom ordering still wins.
    """
    if order_value is None:
        return None

    normalized = int(order_value)
    if sequence_number is None or normalized <= 0 or normalized == sequence_number:
        return normalized

    collapsed = normalized
    while collapsed > 0 and collapsed % 10 == 0:
        collapsed //= 10
        if collapsed == sequence_number:
            return sequence_number

    return normalized


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
    try:
        order_value = int(raw_order or 0)
    except (TypeError, ValueError):
        order_value = 0

    chapter_like_sequence_number = extract_chapter_like_sequence_number(title)
    sequence_number = extract_title_first_sequence_number(title, metadata)
    normalized_order = normalize_explicit_sequence_order(
        order_value,
        sequence_number=sequence_number,
    )
    if has_sequence_locked_order(
        file_type,
        sequence_number=chapter_like_sequence_number,
    ):
        effective_order = int(chapter_like_sequence_number)
    else:
        effective_order = (
            sequence_number
            if normalized_order == 0 and sequence_number is not None
            else int(normalized_order or 0)
        )
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

    For chapter-like draft/outline/script files, the parsed title sequence is
    canonical and overrides any explicit order payload.
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

    match = _RE_CN_SEQ.search(text)
    if match:
        parsed = parse_chinese_number(match.group(1))
        if parsed:
            return parsed

    match = _RE_ARABIC_SEQ.search(text)
    if match:
        try:
            parsed = int(match.group(1))
        except ValueError:
            parsed = 0
        return parsed if parsed > 0 else None

    match = _RE_EN_CHAPTER.search(text)
    if match:
        try:
            parsed = int(match.group(1))
        except ValueError:
            parsed = 0
        return parsed if parsed > 0 else None

    return None
