"""Text metrics helpers shared by backend services."""

from __future__ import annotations

import re

# Per-character scripts: CJK Unified Ideographs (Basic + Ext A + Compatibility + Ext B-F)
# and Japanese Kana (Hiragana + Katakana, excluding middle-dot/double-hyphen punctuation).
# Hangul is intentionally excluded because Korean is space-delimited and counts by words.
_CJK_IDEOGRAPH_AND_KANA_RANGES = (
    r"\u3041-\u3096\u309d-\u309f"
    r"\u30a1-\u30fa\u30fc-\u30ff\u31f0-\u31ff"
    r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff"
    r"\U00020000-\U0002ebef"
)

# Combining marks (diacritics, Arabic tashkeel/harakat, Devanagari/Indic matras, etc.)
# attached to letters so NFD/accented words like "người", "żółć", "café", "e\u0301",
# "مَرْحَبًا", and "नमस्ते" stay intact as single words.
_COMBINING_MARK_RANGES = (
    r"\u0300-\u036f"
    r"\u0483-\u0489"
    r"\u0591-\u05bd\u05bf\u05c1-\u05c2\u05c4-\u05c5\u05c7"
    r"\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06dc\u06df-\u06e4\u06e7-\u06e8\u06ea-\u06ed"
    r"\u0900-\u0903\u093a-\u094f\u0951-\u0957\u0962-\u0963"
    r"\u0981-\u0983\u09bc\u09be-\u09c4\u09c7-\u09c8\u09cb-\u09cd\u09d7\u09e2-\u09e3"
    r"\u0e31\u0e34-\u0e3a\u0e47-\u0e4e"
    r"\u1ab0-\u1aff\u1dc0-\u1dff\u20d0-\u20ff\ufe20-\ufe2f"
)

_CJK_CHAR_PATTERN = re.compile(f"[{_CJK_IDEOGRAPH_AND_KANA_RANGES}]")
# Any Unicode letter outside CJK/Kana, followed by zero or more letters or combining marks.
# Digits (\d), underscores (_), whitespace, and punctuation (\W) act as word boundaries.
_NON_CJK_LETTER = rf"[^\W\d_{_CJK_IDEOGRAPH_AND_KANA_RANGES}]"
_WORD_TOKEN_PATTERN = re.compile(
    rf"{_NON_CJK_LETTER}(?:{_NON_CJK_LETTER}|[{_COMBINING_MARK_RANGES}])*"
)


def is_cjk_or_kana_char(ch: str) -> bool:
    """Return True when ``ch`` is a per-character CJK ideograph or Japanese kana."""
    return len(ch) == 1 and _CJK_CHAR_PATTERN.match(ch) is not None


def has_cjk_or_kana(text: str | None) -> bool:
    """Return True when ``text`` contains at least one CJK ideograph or Japanese kana."""
    if not text:
        return False
    return _CJK_CHAR_PATTERN.search(text) is not None


def count_words(content: str | None) -> int:
    """
    Count words across CJK and all Unicode alphabetic/syllabic scripts.

    - Each CJK ideograph or Japanese kana counts as 1 unit
    - Each contiguous Unicode word (Latin with diacritics/combining marks, Cyrillic,
      Arabic, Hangul, Greek, Devanagari, etc.) counts as 1 word
    - Pure numbers, punctuation, and symbols are not counted
    - Uses streaming ``finditer`` so large manuscripts stay O(1) in transient memory
    """
    if not content:
        return 0

    cjk_chars = sum(1 for _ in _CJK_CHAR_PATTERN.finditer(content))
    word_tokens = sum(1 for _ in _WORD_TOKEN_PATTERN.finditer(content))
    return cjk_chars + word_tokens
