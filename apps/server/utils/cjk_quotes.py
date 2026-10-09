"""Deterministic double-quote normalization for Chinese prose.

The writing model often emits ASCII ``"`` (or full-width ``＂``) around Chinese
dialogue even when the manuscript uses “” or 「」. This module rewrites those
marks, line by line, into the manuscript's own opening/closing quotes.

Only lines that are unambiguous are touched:

- the line contains an ASCII ``"`` or a full-width ``＂`` (otherwise there is
  nothing to fix, which also makes the function idempotent);
- the line contains at least one CJK ideograph (English stays as written);
- the line's double-quote-like marks (``" “ ” ＂``, plus ``「 」`` in corner
  style) add up to an even number, so they can be paired in reading order.

Single quotes are never touched. The function is pure and every replacement
is one character for one character, so length and line breaks are preserved.
"""

from __future__ import annotations

import re

QUOTE_STYLE_CURLY = "curly"  # “ ”
QUOTE_STYLE_CORNER = "corner"  # 「 」

_STYLE_PAIRS: dict[str, tuple[str, str]] = {
    QUOTE_STYLE_CURLY: ("“", "”"),
    QUOTE_STYLE_CORNER: ("「", "」"),
}

_TRIGGER_CHARS = ('"', "＂")
_BASE_QUOTE_CHARS = frozenset('"“”＂')
_CORNER_QUOTE_CHARS = _BASE_QUOTE_CHARS | frozenset("「」")
_CORNER_MARKS = frozenset("「」『』")
_CURLY_MARKS = frozenset("“”")
_CJK_PATTERN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")


def detect_quote_style(text: str | None) -> str:
    """Return the manuscript's quote style: corner when 「」『』 outnumber “”."""
    if not text:
        return QUOTE_STYLE_CURLY
    corner = sum(1 for ch in text if ch in _CORNER_MARKS)
    curly = sum(1 for ch in text if ch in _CURLY_MARKS)
    return QUOTE_STYLE_CORNER if corner > curly else QUOTE_STYLE_CURLY


def has_style_quote_marks(text: str | None) -> bool:
    """Whether ``text`` contains any typographic quote that reveals a style."""
    if not text:
        return False
    return any(ch in _CORNER_MARKS or ch in _CURLY_MARKS for ch in text)


def resolve_quote_style(*candidates: str | None) -> str:
    """Detect the style from the first candidate that has typographic quotes.

    Callers pass reference texts in priority order (the file's existing body,
    the previous chapter, then the incoming text itself); a candidate without
    any “”「」『』 says nothing about the style and is skipped. Defaults to “”.
    """
    for candidate in candidates:
        if has_style_quote_marks(candidate):
            return detect_quote_style(candidate)
    return QUOTE_STYLE_CURLY


def _normalize_line(line: str, style: str) -> str:
    if not any(ch in line for ch in _TRIGGER_CHARS):
        return line
    if not _CJK_PATTERN.search(line):
        return line
    quote_chars = _CORNER_QUOTE_CHARS if style == QUOTE_STYLE_CORNER else _BASE_QUOTE_CHARS
    positions = [i for i, ch in enumerate(line) if ch in quote_chars]
    if len(positions) % 2:
        return line
    opening, closing = _STYLE_PAIRS[style]
    chars = list(line)
    for n, pos in enumerate(positions):
        chars[pos] = opening if n % 2 == 0 else closing
    return "".join(chars)


def normalize_double_quotes(text: str, style: str = QUOTE_STYLE_CURLY) -> str:
    """Rewrite unambiguous double quotes in ``text`` into ``style`` quotes."""
    if not text or not any(ch in text for ch in _TRIGGER_CHARS):
        return text
    if style not in _STYLE_PAIRS:
        style = QUOTE_STYLE_CURLY
    return "\n".join(_normalize_line(line, style) for line in text.split("\n"))


__all__ = [
    "QUOTE_STYLE_CORNER",
    "QUOTE_STYLE_CURLY",
    "detect_quote_style",
    "has_style_quote_marks",
    "normalize_double_quotes",
    "resolve_quote_style",
]
