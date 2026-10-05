"""
Novel text decoding and chapter splitting shared by the upload API and the
ingestion worker.

The upload endpoint pre-checks a file with exactly the same functions the
Prefect worker later uses in stage0, so a file the API accepts is a file the
worker can decode and split into the same chapters.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import chardet
import cn2an

UTF8_BOM = b"\xef\xbb\xbf"
UTF16_LE_BOM = b"\xff\xfe"
UTF16_BE_BOM = b"\xfe\xff"
ENCODING_DETECTION_SAMPLE_SIZE = 10_000
ENCODING_CONFIDENCE_THRESHOLD = 0.7

# Detector guesses that are never preferred over strict utf-8 / gb18030 for
# Chinese novels: they accept any byte sequence and would yield mojibake.
_PERMISSIVE_SINGLE_BYTE_ENCODINGS = {
    "ascii",
    "iso-8859-1",
    "latin-1",
    "windows-1252",
    "cp1252",
}
_ENCODING_ALIASES = {
    "utf8": "utf-8",
    "gbk": "gb18030",
    "gb2312": "gb18030",
    "gb-2312": "gb18030",
}

# Chapters shorter than this are merged into a neighbour instead of being
# sent to the LLM on their own (e.g. "请假条" or "上架感言" notes).
MIN_CHAPTER_LENGTH = 100
# A heading is a short line; longer lines are body text even if they start
# with "1、" or "第三章".
MAX_HEADING_LENGTH = 40
# Headings never end like a sentence.
_SENTENCE_ENDINGS = ("。", "，", ",", "；", ";")

_NUM = r"[零〇一二两兩三四五六七八九十百千万萬0-9０-９]+"
_VOLUME_PREFIX = rf"(?:第{_NUM}[卷部集篇]|卷{_NUM})"

# 第X章 / 第X节 / 第X回 (optionally prefixed by 第X卷 on the same line, or
# wrapped in 【】). "第一回合" is body text, not a heading.
_CHINESE_CHAPTER_RE = re.compile(
    rf"^(?:{_VOLUME_PREFIX}[^第]{{0,20}}?\s*)?【?第({_NUM})(?:[章节節]|回(?!合))】?"
    r"\s*[:：、.．]?\s*(.*)$"
)
# A volume line without a chapter on it ("第一卷 风起云涌") separates
# chapters; it is not chapter content.
_VOLUME_ONLY_RE = re.compile(rf"^{_VOLUME_PREFIX}(?:\s*[:：·\-—]?\s*[^第。，,]{{0,30}})?$")

_ENGLISH_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
    "twenty": 20,
}
_ROMAN_RE = r"[ivxlcdm]+"
_ENGLISH_CHAPTER_RE = re.compile(
    rf"^chapter\s+(\d{{1,4}}|{_ROMAN_RE}|{'|'.join(_ENGLISH_NUMBER_WORDS)})\b"
    r"\s*[:：.\-—]?\s*(.*)$",
    re.IGNORECASE,
)
# "12. 标题" / "12、标题". The title must not start with a digit so that
# "3.5亿年前" or "2023.10.01 晴" stay body text.
_NUMBERED_RE = re.compile(r"^(\d{1,4})[.、．]\s*([^\d.．].*)?$")

_PROLOGUE_WORDS = ("楔子", "序章", "序言", "序幕", "引子", "引言", "前言", "序")
_EPILOGUE_WORDS = ("尾声", "尾聲", "后记", "後記", "终章", "終章", "番外")
_SPECIAL_RE = re.compile(
    r"^(" + "|".join(_PROLOGUE_WORDS + _EPILOGUE_WORDS) + r")"
    r"(?:$|(?=[\s:：·\-—篇零一二三四五六七八九十0-9]))\s*[:：·\-—]?\s*(.*)$"
)

_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")
_TRADITIONAL_NUMERALS = str.maketrans({"兩": "两", "萬": "万"})


class NovelDecodeError(ValueError):
    """Raised when uploaded bytes are not text in a supported encoding."""


@dataclass(frozen=True)
class NovelTextAnalysis:
    """Result of decoding and splitting an uploaded novel."""

    encoding: str
    char_count: int
    chapter_count: int


def decode_novel_bytes(data: bytes) -> tuple[str, str]:
    """
    Decode novel bytes strictly and return ``(text, encoding)``.

    Order: BOM, a confident non-latin detector guess, utf-8, gb18030. Nothing
    is decoded with ``errors="ignore"``; undecodable input raises
    :class:`NovelDecodeError`.
    """
    if not data:
        return "", "utf-8"
    if data.startswith(UTF8_BOM):
        return data.decode("utf-8-sig"), "utf-8-sig"
    if data.startswith(UTF16_LE_BOM) or data.startswith(UTF16_BE_BOM):
        try:
            return data.decode("utf-16"), "utf-16"
        except UnicodeDecodeError as exc:
            raise NovelDecodeError("invalid utf-16 content") from exc

    candidates: list[str] = []
    detected = chardet.detect(data[:ENCODING_DETECTION_SAMPLE_SIZE])
    detected_encoding = detected.get("encoding")
    detected_confidence = float(detected.get("confidence") or 0.0)
    if (
        isinstance(detected_encoding, str)
        and detected_encoding
        and detected_confidence >= ENCODING_CONFIDENCE_THRESHOLD
    ):
        normalized = detected_encoding.lower().replace("_", "-")
        normalized = _ENCODING_ALIASES.get(normalized, normalized)
        if normalized not in _PERMISSIVE_SINGLE_BYTE_ENCODINGS:
            candidates.append(normalized)
    candidates.extend(["utf-8", "gb18030"])

    seen: set[str] = set()
    for encoding in candidates:
        if encoding in seen:
            continue
        seen.add(encoding)
        try:
            return data.decode(encoding), encoding
        except (UnicodeDecodeError, LookupError):
            continue

    raise NovelDecodeError("unsupported text encoding")


def chinese_num_to_int(token: str) -> int | None:
    """Convert a Chinese or Arabic numeral to int; None when unparsable."""
    normalized = (token or "").strip().translate(_FULLWIDTH_DIGITS)
    normalized = normalized.translate(_TRADITIONAL_NUMERALS)
    if not normalized:
        return None
    if normalized.isdigit():
        return int(normalized)
    try:
        return int(cn2an.cn2an(normalized, "smart"))
    except Exception:
        return None


def _roman_to_int(token: str) -> int | None:
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    total = 0
    previous = 0
    for char in reversed(token.lower()):
        value = values.get(char)
        if value is None:
            return None
        if value < previous:
            total -= value
        else:
            total += value
            previous = value
    return total or None


def _english_number(token: str) -> int | None:
    lowered = token.lower()
    if lowered.isdigit():
        return int(lowered)
    if lowered in _ENGLISH_NUMBER_WORDS:
        return _ENGLISH_NUMBER_WORDS[lowered]
    return _roman_to_int(lowered)


@dataclass(frozen=True)
class _Heading:
    number: int | None  # None: continue the sequence
    title: str
    prologue: bool = False


def _parse_heading(line: str) -> _Heading | None:
    """Return the heading on ``line`` (already stripped), or None for body text."""
    if len(line) > MAX_HEADING_LENGTH or line.endswith(_SENTENCE_ENDINGS):
        return None

    match = _CHINESE_CHAPTER_RE.match(line)
    if match:
        return _Heading(chinese_num_to_int(match.group(1)), match.group(2).strip())

    match = _ENGLISH_CHAPTER_RE.match(line)
    if match:
        return _Heading(_english_number(match.group(1)), match.group(2).strip())

    match = _NUMBERED_RE.match(line)
    if match:
        return _Heading(int(match.group(1)), (match.group(2) or "").strip())

    match = _SPECIAL_RE.match(line)
    if match:
        keyword = match.group(1)
        title = line if match.group(2) else keyword
        return _Heading(None, title, prologue=keyword in _PROLOGUE_WORDS)

    return None


def extract_chapter_number(title_line: str) -> tuple[int, str]:
    """
    Return ``(chapter_number, title)`` for a heading line, ``(0, line)`` otherwise.

    Kept for callers that only need the number; headings without a number
    (楔子、尾声) report 0 here, the splitter numbers them by position.
    """
    stripped = (title_line or "").strip()
    heading = _parse_heading(stripped)
    if heading is None or heading.number is None:
        return 0, stripped
    return heading.number, heading.title


def split_novel_text(
    text: str,
    min_chapter_length: int = MIN_CHAPTER_LENGTH,
) -> list[dict[str, Any]]:
    """
    Split novel text into chapters.

    - Recognises 第X章/节/回 (also after 第X卷 on the same line, or in 【】),
      Chapter N, "N. 标题", and 楔子/序章/尾声/番外 style headings.
    - Volume-only lines are separators, not content.
    - Text before the first heading becomes a prologue chapter when it is long
      enough; a text without any heading yields no chapters.
    - Chapters shorter than ``min_chapter_length`` are merged into the previous
      chapter (or the next one when they come first) instead of being dropped.
    """
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    preamble: list[str] = []
    raw_chapters: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    last_number = 0

    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            target = current["lines"] if current else preamble
            if target:
                target.append("")
            continue

        if _VOLUME_ONLY_RE.match(line) and len(line) <= MAX_HEADING_LENGTH:
            continue

        heading = _parse_heading(line)
        if heading is None:
            (current["lines"] if current else preamble).append(line)
            continue

        if heading.prologue and not raw_chapters and current is None:
            number = 0
        elif heading.number is not None:
            number = heading.number
        else:
            number = last_number + 1
        last_number = max(last_number, number)

        current = {
            "chapter_number": number,
            "title": heading.title or f"第{number}章",
            "original_title_line": line,
            "lines": [],
        }
        raw_chapters.append(current)

    if not raw_chapters:
        return []

    preamble_text = "\n".join(preamble).strip()
    if preamble_text:
        raw_chapters.insert(
            0,
            {
                "chapter_number": 0,
                "title": "序章",
                "original_title_line": "",
                "lines": [preamble_text],
            },
        )

    chapters: list[dict[str, Any]] = []
    carry = ""
    for raw in raw_chapters:
        content = "\n".join(raw["lines"]).strip()
        if carry:
            content = f"{carry}\n\n{content}".strip()
            carry = ""
        if len(content) < min_chapter_length:
            fragment = "\n".join(
                part for part in (raw["original_title_line"], content) if part
            )
            if chapters:
                chapters[-1]["content"] = f"{chapters[-1]['content']}\n\n{fragment}".strip()
            else:
                carry = fragment
            continue
        chapters.append(
            {
                "chapter_number": raw["chapter_number"],
                "title": raw["title"],
                "original_title_line": raw["original_title_line"],
                "content": content,
            }
        )

    return chapters


def analyze_novel_bytes(data: bytes) -> NovelTextAnalysis:
    """Decode and split uploaded bytes exactly like the ingestion worker does."""
    text, encoding = decode_novel_bytes(data)
    chapters = split_novel_text(text)
    return NovelTextAnalysis(
        encoding=encoding,
        char_count=len(text),
        chapter_count=len(chapters),
    )


__all__ = [
    "MIN_CHAPTER_LENGTH",
    "NovelDecodeError",
    "NovelTextAnalysis",
    "analyze_novel_bytes",
    "chinese_num_to_int",
    "decode_novel_bytes",
    "extract_chapter_number",
    "split_novel_text",
]
