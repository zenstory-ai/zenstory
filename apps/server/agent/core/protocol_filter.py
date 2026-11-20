"""Shared fence-aware leaked tool-protocol detection and MiniMax stripping."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

MINIMAX_DELIM = "]<\x5dminimax[>["
_DELIM_RE = r"\]<\]minimax\[>\["
_OPEN_TAG = rf"{_DELIM_RE}\s*<?\s*tool_call\b"
_CLOSE_TAG = rf"(?:{_DELIM_RE}\s*<?|<\s*)/\s*tool_call\s*>"

CODE_SPAN_OR_FENCE_RE = re.compile(
    r"(^[ \t]*(```|~~~)[^\r\n]*\r?\n.*?(?:^[ \t]*\2[ \t]*\r?$|\Z)|(`+)(?:(?!\3)[^\r\n])+\3)",
    re.DOTALL | re.MULTILINE,
)
_LEAKED_TOOL_PROTOCOL_RE = re.compile(
    rf"(?:{_DELIM_RE}|</\s*tool_call\s*>|<tool_call\b[^><\r\n]{{0,512}}>\s*(?:<|\Z)|</?(?:invoke|parameter)\b[^><\r\n]{{0,512}}>)",
    re.IGNORECASE,
)
_MM_TOOL_OPEN_RE = re.compile(rf"^{_OPEN_TAG}[^>]*>", re.IGNORECASE)
_MM_TOOL_CLOSE_RE = re.compile(_CLOSE_TAG, re.IGNORECASE)
_MM_TOOL_BLOCK_RE = re.compile(
    rf"{_OPEN_TAG}(?:[^>]*>.*?(?:{_CLOSE_TAG}\r?\n?|\Z)|[^>\r\n]*\Z)",
    re.DOTALL | re.IGNORECASE,
)
_MM_BARE_DELIM_RE = re.compile(_DELIM_RE, re.IGNORECASE)
_INLINE_CODE_STOP_RE = re.compile(r"[`\n]")
_PLAIN_STOP_RE = re.compile(r"[`\]]")
_MM_TOOL_OPEN_PREFIXES = (f"{MINIMAX_DELIM}<tool_call>", f"{MINIMAX_DELIM}tool_call>")
_MAX_LOOKAHEAD = 64
_CLOSE_TAIL_KEEP = 32


def _iter_code_segments(text: str) -> Iterator[tuple[str, bool]]:
    pos = 0
    for match in CODE_SPAN_OR_FENCE_RE.finditer(text):
        if match.start() > pos:
            yield text[pos : match.start()], False
        yield match.group(0), True
        pos = match.end()
    if pos < len(text):
        yield text[pos:], False


def strip_code_spans(text: str) -> str:
    """Return ``text`` with fenced code blocks and inline backtick spans removed."""
    return "".join(seg for seg, is_code in _iter_code_segments(text) if not is_code)


def contains_leaked_tool_protocol(text: str) -> bool:
    """Check whether ``text`` contains leaked tool-call protocol outside code spans."""
    return bool(text) and any(
        _LEAKED_TOOL_PROTOCOL_RE.search(seg) is not None for seg, is_code in _iter_code_segments(text) if not is_code
    )


def strip_minimax_tool_leaks(text: str) -> str:
    """Strip leaked MiniMax tool-call blocks and bare delimiters outside code spans."""
    if not text or "]" not in text:
        return text
    return "".join(
        seg if is_code or "]" not in seg else _MM_BARE_DELIM_RE.sub("", _MM_TOOL_BLOCK_RE.sub("", seg))
        for seg, is_code in _iter_code_segments(text)
    )


def _could_grow_tool_open(buf: str, compact: str) -> bool:
    return len(buf) <= _MAX_LOOKAHEAD and (
        any(p.startswith(compact) for p in _MM_TOOL_OPEN_PREFIXES)
        or (any(compact.startswith(p[:-1]) for p in _MM_TOOL_OPEN_PREFIXES) and ">" not in buf[len(MINIMAX_DELIM) :])
    )


@dataclass
class MiniMaxLeakFilter:
    """Streaming MiniMax leak filter relying on the caller's ``fence_open`` state."""

    swallowing: bool = False
    in_inline_code: bool = False
    buf: str = ""

    @property
    def has_pending(self) -> bool:
        return bool(self.buf) or self.swallowing

    def reset(self) -> None:
        self.swallowing = self.in_inline_code = False
        self.buf = ""

    def feed(
        self, text: str, *, fence_open: bool = False, at_eof: bool = False, project_id: str = "", user_id: str | None = None
    ) -> str:
        buf, self.buf = self.buf + text, ""
        if not buf and not (at_eof and self.swallowing):
            return ""
        if fence_open and not self.swallowing:
            self.in_inline_code = False
            if at_eof:
                self.reset()
            return buf

        out: list[str] = []
        while buf or (at_eof and self.swallowing):
            if self.swallowing:
                if (close := _MM_TOOL_CLOSE_RE.search(buf)) is not None:
                    self.swallowing = False
                    rest = buf[close.end() :]
                    buf = rest[2:] if rest.startswith("\r\n") else rest.removeprefix("\n")
                    continue
                if at_eof:
                    log_with_context(logger, 30, "Dropping an unclosed leaked MiniMax tool_call fragment from conversation", project_id=project_id, user_id=user_id)
                    self.swallowing, buf = False, ""
                else:
                    self.buf = buf[-_CLOSE_TAIL_KEEP:]
                break

            stop = (_INLINE_CODE_STOP_RE if self.in_inline_code else _PLAIN_STOP_RE).search(buf)
            if stop is None:
                out.append(buf)
                break
            if stop.start() > 0:
                out.append(buf[: stop.start()])
                buf = buf[stop.start() :]
            if self.in_inline_code:
                self.in_inline_code = False
                out.append(buf[0])
                buf = buf[1:]
                continue
            if buf[0] == "`":
                run_len = len(buf) - len(buf.lstrip("`"))
                self.in_inline_code = run_len == 1
                out.append(buf[:run_len])
                buf = buf[run_len:]
                continue

            if (open_match := _MM_TOOL_OPEN_RE.match(buf)) is not None:
                log_with_context(logger, 30, "Stripping leaked MiniMax tool_call protocol from conversation", project_id=project_id, user_id=user_id)
                self.swallowing, buf = True, buf[open_match.end() :]
                continue
            compact = re.sub(r"\s+", "", buf).lower()
            if _could_grow_tool_open(buf, compact):
                if not at_eof:
                    self.buf = buf
                elif not compact.startswith(MINIMAX_DELIM):
                    out.append(buf)
                break
            if buf.lower().startswith(MINIMAX_DELIM):
                buf = buf[len(MINIMAX_DELIM) :]
                continue
            out.append("]")
            buf = buf[1:]

        if at_eof:
            self.reset()
        return "".join(out)
