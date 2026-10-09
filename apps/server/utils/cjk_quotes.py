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


_OPENING_MARKS = frozenset("“「")
_CLOSING_MARKS = frozenset("”」")


def _normalize_line_segment(
    chars: list[str], line_start: int, line_end: int, seg_start: int, seg_end: int, style: str
) -> None:
    """Rewrite the quotes of ``chars[seg_start:seg_end]`` using the whole line to pair them.

    The line is the resulting line (surrounding original text plus the inserted
    segment); only characters inside the segment are changed. Skipped when the
    line is ambiguous: no CJK, an odd quote count, or an existing directional
    mark anywhere on the line that disagrees with its position in the pairing.
    """
    if not any(chars[i] in _TRIGGER_CHARS for i in range(seg_start, seg_end)):
        return
    if not _CJK_PATTERN.search("".join(chars[line_start:line_end])):
        return
    quote_chars = _CORNER_QUOTE_CHARS if style == QUOTE_STYLE_CORNER else _BASE_QUOTE_CHARS
    positions = [i for i in range(line_start, line_end) if chars[i] in quote_chars]
    if len(positions) % 2:
        return
    for n, pos in enumerate(positions):
        opens = n % 2 == 0
        if (chars[pos] in _OPENING_MARKS and not opens) or (chars[pos] in _CLOSING_MARKS and opens):
            return
    opening, closing = _STYLE_PAIRS[style]
    for n, pos in enumerate(positions):
        if seg_start <= pos < seg_end:
            chars[pos] = opening if n % 2 == 0 else closing


def normalize_inserted_spans(
    text: str, spans: list[tuple[int, int]], style: str = QUOTE_STYLE_CURLY
) -> str:
    """Normalize double quotes only inside ``spans`` of ``text`` (the edited result).

    Each span is newly written text that already sits in its final place. The
    quote direction comes from the whole resulting line, so a fragment that
    starts mid-dialogue (an odd number of quotes before it on the line) begins
    with a closing quote. Characters outside the spans are never changed, and
    replacements are one character for one, so span offsets stay valid.
    """
    if not text or not spans or not any(ch in text for ch in _TRIGGER_CHARS):
        return text
    if style not in _STYLE_PAIRS:
        style = QUOTE_STYLE_CURLY
    chars = list(text)
    for start, end in spans:
        start, end = max(0, start), min(len(chars), end)
        pos = start
        while pos < end:
            line_start = text.rfind("\n", 0, pos) + 1
            line_end = text.find("\n", pos)
            if line_end == -1:
                line_end = len(text)
            _normalize_line_segment(chars, line_start, line_end, pos, min(end, line_end), style)
            pos = line_end + 1
    return "".join(chars)


__all__ = [
    "QUOTE_STYLE_CORNER",
    "QUOTE_STYLE_CURLY",
    "detect_quote_style",
    "has_style_quote_marks",
    "normalize_double_quotes",
    "normalize_inserted_spans",
    "resolve_quote_style",
]
