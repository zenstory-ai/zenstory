"""Bounded-memory streaming reasoning repetition loop guard.

Faithful Python port of the official ``MiniMax-AI/minimax-code`` algorithm
(``packages/agent-core/src/pi-turn-runner/llm-reply-loop-guard.ts``):

- Splits streamed text into sentence/line units without splitting numbered lists
  (``1. ``) or decimals (``3.14``), and splits immediately on CJK stops
  (``。``, ``！``, ``？``) and newlines.
- Maintains a bounded ring buffer of the last ``LOOP_WINDOW = 256`` counted
  units (ignoring pure braces, numbers, hex hashes, and horizontal rules) and
  trips when ``n >= LOOP_WINDOW`` and distinct units ``<= LOOP_DISTINCT = 16``.
- Detects unbroken run-on loops (repeated words, emojis, or short periodic
  symbols without sentence breaks) when ``RUN_ON_UNITS = 3`` consecutive
  ``UNIT_MOST = 4096``-char chunks pass ``run_on_words``.
- Pure bounded syntax/periodicity checks using only stdlib ``unicodedata`` and
  ``re`` — zero natural-language keyword or intent heuristics.
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from dataclasses import dataclass

LOOP_WINDOW = 256
LOOP_DISTINCT = 16
UNIT_MOST = 4096
UNIT_KEPT = 256
RUN_ON_UNITS = 3
RUN_ON_DISTINCT = 8
_RUN_ON_PERIOD_MAX = 64

_ASCII_STOPS = frozenset(".!?")
_CJK_STOPS = frozenset("。！？")
_TRAILING_PUNCT_RE = re.compile(r"[.!?。！？,，;；:：…\s]+$")


@dataclass(frozen=True)
class ReasoningLoopTrip:
    """Structured details emitted when ``ReasoningLoopGuard`` trips."""

    window: int
    units: list[str]
    run_on: bool = False


def _is_letter(ch: str) -> bool:
    return unicodedata.category(ch).startswith("L")


def _is_digit(ch: str) -> bool:
    return unicodedata.category(ch) == "Nd"


def _is_word_char(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat.startswith("L") or cat == "Nd" or ch == "_"


def _is_emoji(ch: str) -> bool:
    """Return True for Unicode Extended_Pictographic / emoji symbols (excluding box drawing & ASCII rules)."""
    cp = ord(ch)
    if cp < 0x203C:
        return cp in (0x00A9, 0x00AE)
    if 0x1F000 <= cp <= 0x1FAFF:
        return True
    if 0x2600 <= cp <= 0x27BF:
        return True
    if 0x231A <= cp <= 0x23FA:
        return True
    if 0x2B05 <= cp <= 0x2B55:
        return True
    return cp in (0x203C, 0x2049, 0x2122, 0x2139, 0x3030, 0x303D, 0x3297, 0x3299) or (
        0x2194 <= cp <= 0x21AA
    )


def has_word_or_emoji(s: str) -> bool:
    """Return True iff ``s`` contains a contiguous ``[\\w]`` run with >=1 Unicode letter and 0 decimal digits, or an emoji.

    Intentionally returns False for pure braces, numbers, hex hashes/UUIDs, and separator rules.
    """
    in_run = False
    run_has_letter = False
    run_has_digit = False
    for ch in s:
        if _is_emoji(ch):
            return True
        cat = unicodedata.category(ch)
        if cat.startswith("L"):
            in_run = True
            run_has_letter = True
        elif cat == "Nd":
            in_run = True
            run_has_digit = True
        elif ch == "_":
            in_run = True
        else:
            if in_run:
                if run_has_letter and not run_has_digit:
                    return True
                in_run = False
                run_has_letter = False
                run_has_digit = False
    return in_run and run_has_letter and not run_has_digit


def unit_key(raw: str) -> str:
    """Normalize one unit into its ring-buffer key, or ``""`` if ignored."""
    s = _TRAILING_PUNCT_RE.sub("", raw.strip())
    if not s or not has_word_or_emoji(s):
        return ""
    return s.lower()[:UNIT_KEPT]


def run_on_words(s: str) -> list[str] | None:
    """Return representative repeated tokens if unbroken text ``s`` is a run-on loop, else ``None``."""
    worded = has_word_or_emoji(s)
    if not worded:
        has_alnum = False
        has_non_ws = False
        for ch in s:
            if not ch.isspace():
                has_non_ws = True
            cat = unicodedata.category(ch)
            if cat.startswith("L") or cat == "Nd":
                has_alnum = True
                break
        if has_alnum or not has_non_ws:
            return None

    length = len(s)
    for period in range(1, _RUN_ON_PERIOD_MAX + 1):
        if 4 * period > length:
            break
        if s[period:] == s[:-period]:
            sample = s[: min(3 * period, length)].strip()
            return [sample]

    if not worded:
        return None

    lowered = s.lower()
    counts: dict[str, int] = {}
    words = 0
    idx = 0
    n = len(lowered)
    while idx < n:
        ch = lowered[idx]
        if _is_word_char(ch):
            end = idx + 1
            while end < n and _is_word_char(lowered[end]):
                end += 1
            token = lowered[idx:end]
            counts[token] = counts.get(token, 0) + 1
            words += 1
            idx = end
        elif _is_emoji(ch):
            counts[ch] = counts.get(ch, 0) + 1
            words += 1
            idx += 1
        else:
            idx += 1

    if not counts or len(counts) > RUN_ON_DISTINCT:
        return None
    if words * 32 < length:
        return None
    return [tok for tok, _cnt in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


class UnitRun:
    """Streaming unit-window and run-on loop detector (bounded O(LOOP_WINDOW + UNIT_MOST) memory)."""

    def __init__(self) -> None:
        self._cur: list[str] = []
        self._cur_len: int = 0
        self._lettered: bool = False
        self._pending_stop: bool = False
        self._ring: deque[str] = deque()
        self._counts: dict[str, int] = {}
        self._run_on: int = 0
        self._trip: ReasoningLoopTrip | None = None

    @property
    def trip(self) -> ReasoningLoopTrip | None:
        return self._trip

    def reset(self) -> None:
        """Reset all streaming state (call on every ``response.created`` / new model turn)."""
        self._cur.clear()
        self._cur_len = 0
        self._lettered = False
        self._pending_stop = False
        self._ring.clear()
        self._counts.clear()
        self._run_on = 0
        self._trip = None

    def _end(self, *, prejoined: str | None = None) -> ReasoningLoopTrip | None:
        raw = prejoined if prejoined is not None else "".join(self._cur)
        self._cur.clear()
        self._cur_len = 0
        self._lettered = False
        self._pending_stop = False

        if len(raw) < UNIT_MOST and (len(raw) < UNIT_MOST // 8 or not run_on_words(raw)):
            self._run_on = 0

        key = unit_key(raw)
        if not key:
            return None

        if len(self._ring) == LOOP_WINDOW:
            evicted = self._ring.popleft()
            remaining = self._counts.get(evicted, 0) - 1
            if remaining <= 0:
                self._counts.pop(evicted, None)
            else:
                self._counts[evicted] = remaining

        self._ring.append(key)
        self._counts[key] = self._counts.get(key, 0) + 1

        if len(self._ring) >= LOOP_WINDOW and len(self._counts) <= LOOP_DISTINCT:
            units = [tok for tok, _cnt in sorted(self._counts.items(), key=lambda kv: (-kv[1], kv[0]))]
            self._trip = ReasoningLoopTrip(window=len(self._ring), units=units, run_on=False)
            return self._trip
        return None

    def feed(self, chunk: str) -> ReasoningLoopTrip | None:
        """Consume a streamed reasoning delta; return ``ReasoningLoopTrip`` once tripped, else ``None``."""
        if self._trip is not None:
            return self._trip
        if not chunk:
            return None

        for ch in chunk:
            if ch == "\n":
                if self._end() is not None:
                    return self._trip
                continue
            if self._pending_stop and ch.isspace():
                if self._end() is not None:
                    return self._trip
                continue

            self._cur.append(ch)
            self._cur_len += 1
            if not self._lettered and _is_letter(ch):
                self._lettered = True

            if ch in _CJK_STOPS:
                if self._end() is not None:
                    return self._trip
                continue

            self._pending_stop = self._lettered and ch in _ASCII_STOPS
            if self._cur_len >= UNIT_MOST:
                raw = "".join(self._cur)
                words = run_on_words(raw)
                if words:
                    self._run_on += 1
                    if self._run_on >= RUN_ON_UNITS:
                        self._trip = ReasoningLoopTrip(
                            window=self._run_on * UNIT_MOST,
                            units=words,
                            run_on=True,
                        )
                        return self._trip
                else:
                    self._run_on = 0
                if self._end(prejoined=raw) is not None:
                    return self._trip

        return self._trip


ReasoningLoopGuard = UnitRun

__all__ = [
    "LOOP_DISTINCT",
    "LOOP_WINDOW",
    "RUN_ON_DISTINCT",
    "RUN_ON_UNITS",
    "UNIT_KEPT",
    "UNIT_MOST",
    "ReasoningLoopGuard",
    "ReasoningLoopTrip",
    "UnitRun",
    "has_word_or_emoji",
    "run_on_words",
    "unit_key",
]
