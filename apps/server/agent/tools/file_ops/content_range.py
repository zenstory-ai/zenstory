"""Range reads of one file's body for query_files (#274).

A long collection should not be pulled whole to read the previous chapter's
ending or one episode. The model asks for a heading (``section``), the last N
characters (``tail_chars``) or a character window (``start_char`` +
``max_chars``); the result says exactly which range it holds, so a partial
body is never mistaken for the whole file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from utils.text_metrics import count_words
from utils.title_sequence import parse_chinese_number

DEFAULT_RANGE_CHARS = 6000
MAX_RANGE_CHARS = 20000
# 短文件（或 start_char 之后剩余字符不超过该阈值）不切片分页：直接返回全文（或余下全文），
# 避免模型用 start_char 把几千字的小文件拆成 2–10 步逐块翻读。
SHORT_FILE_FULL_RETURN_MAX_CHARS = 12000
_HEADING_MAX_CHARS = 40
_MAX_LISTED_HEADINGS = 40
_HIGHER_UNITS = frozenset({"卷", "部", "篇", "part"})
_CHAPTER_UNITS = frozenset({"章", "集", "回", "chapter", "episode"})
_SUB_UNITS = frozenset({"节", "幕"})
_MD_PREFIX_RE = re.compile(r"^\s*(?:#{1,6}\s+)?(?:\*{1,2})?\s*")
_MD_BOLD_SUFFIX_RE = re.compile(r"\*{1,2}\s*$")
_HEADING_RE = re.compile(
    r"^\s*(?:(?P<hashes>#{1,6})\s+)?(?:\*{1,2})?\s*(?:"
    r"第\s*(?P<num>[零〇一二两三四五六七八九十百千万\d]+)\s*(?P<unit>[章集回节幕卷部篇])"
    r"|(?P<vol_unit>卷)\s*(?P<vol_num>[零〇一二两三四五六七八九十百千万\d]+)(?=$|[ \t\u3000:：·.、—\-*])"
    r"|(?P<en_unit>chapter|episode|part)\s+(?P<en_num>\d+)"
    r"|【(?P<bracketed>[^】]{1,20})】"
    r"|(?P<cn_section>[零〇一二两三四五六七八九十]+、\s*\S+)"
    r")",
    re.IGNORECASE,
)
_MD_GENERIC_HEADING_RE = re.compile(r"^\s*(?P<hashes>#{1,6})\s+\S+")


class ContentRangeError(ValueError):
    """The requested range cannot be read as asked; the message lists what exists."""

    def __init__(self, message: str, *, reason: str = "invalid") -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class _Heading:
    offset: int
    text: str
    plain: str
    bracket_inner: str | None
    unit: str | None
    number: int | None
    md_level: int | None
    rank: int


def _strip_heading_markup(text: str) -> str:
    cleaned = _MD_PREFIX_RE.sub("", text.strip())
    if text.strip().startswith("*") or cleaned.endswith("*"):
        cleaned = _MD_BOLD_SUFFIX_RE.sub("", cleaned)
    return cleaned.strip()


def _unbracket(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("【") and stripped.endswith("】") and len(stripped) > 2:
        return stripped[1:-1].strip()
    return stripped


def _number(value: str | None) -> int | None:
    if not value:
        return None
    return int(value) if value.isdigit() else parse_chinese_number(value)


def _heading_rank(
    *,
    unit: str | None,
    cn_section: bool,
    bracket_inner: str | None,
    md_level: int | None,
) -> int:
    """Structural hierarchy rank (smaller = higher level) for section stop rules."""
    if unit in _HIGHER_UNITS:
        return 1
    if cn_section:
        return 2
    if unit in _CHAPTER_UNITS:
        return 3
    if unit in _SUB_UNITS:
        return 4
    if bracket_inner is not None:
        return 5
    return md_level if md_level is not None else 6


def _stops_section(start: _Heading, candidate: _Heading) -> bool:
    """Whether ``candidate`` (after ``start``) terminates the section begun by ``start``."""
    if start.unit in _HIGHER_UNITS:
        if candidate.unit in _HIGHER_UNITS:
            return True
        if candidate.unit is not None:
            return False
        if start.md_level is not None and candidate.md_level is not None:
            return candidate.md_level <= start.md_level
        return candidate.rank <= start.rank
    if start.md_level is not None and candidate.md_level is not None:
        return candidate.md_level <= start.md_level
    if (
        start.unit in _CHAPTER_UNITS
        and candidate.unit in _CHAPTER_UNITS
        and candidate.unit != start.unit
    ):
        return False
    return candidate.rank <= start.rank


def _headings(content: str) -> list[_Heading]:
    found: list[_Heading] = []
    offset = 0
    for line in content.split("\n"):
        stripped = line.strip()
        if stripped and len(stripped) <= _HEADING_MAX_CHARS:
            match = _HEADING_RE.match(stripped)
            if match:
                hashes = match.group("hashes")
                md_level = len(hashes) if hashes else None
                unit = (
                    match.group("unit")
                    or match.group("vol_unit")
                    or match.group("en_unit")
                    or ""
                ).lower() or None
                number = _number(
                    match.group("num") or match.group("vol_num") or match.group("en_num")
                )
                bracket_inner = match.group("bracketed")
                bracket_cleaned = bracket_inner.strip() if bracket_inner else None
                cn_section = bool(match.group("cn_section"))
                found.append(
                    _Heading(
                        offset,
                        stripped,
                        _strip_heading_markup(stripped),
                        bracket_cleaned,
                        unit,
                        number,
                        md_level,
                        _heading_rank(
                            unit=unit,
                            cn_section=cn_section,
                            bracket_inner=bracket_cleaned,
                            md_level=md_level,
                        ),
                    )
                )
            else:
                generic = _MD_GENERIC_HEADING_RE.match(stripped)
                if generic:
                    md_level = len(generic.group("hashes"))
                    plain = _strip_heading_markup(stripped)
                    if plain:
                        found.append(
                            _Heading(
                                offset,
                                stripped,
                                plain,
                                None,
                                None,
                                None,
                                md_level,
                                md_level,
                            )
                        )
        offset += len(line) + 1
    return found


def _section_bounds(content: str, section: str) -> tuple[int, int, str]:
    headings = _headings(content)
    wanted = section.strip()
    wanted_match = _HEADING_RE.match(wanted)
    wanted_unit = (
        (
            wanted_match.group("unit")
            or wanted_match.group("vol_unit")
            or wanted_match.group("en_unit")
            or ""
        ).lower()
        if wanted_match
        else ""
    )
    wanted_number = (
        _number(
            wanted_match.group("num")
            or wanted_match.group("vol_num")
            or wanted_match.group("en_num")
        )
        if wanted_match
        else None
    )

    if wanted_unit and wanted_number is not None:
        # 「第3章」按章号比较：不会匹配「第13章」或「第3集」；「卷四」与「第4卷」统一按卷号比较。
        matches = [h for h in headings if h.unit == wanted_unit and h.number == wanted_number]
    else:
        wanted_plain = _strip_heading_markup(wanted)
        wanted_unbracketed = _unbracket(wanted_plain)
        # Prefer exact heading matches before startswith prefix matches so
        # `### 人物` is not treated as ambiguous alongside `### 人物关系`.
        exact_matches = [
            h
            for h in headings
            if (
                h.text == wanted
                or (wanted_plain and h.plain == wanted_plain)
                or (
                    h.bracket_inner is not None
                    and wanted_unbracketed
                    and h.bracket_inner == wanted_unbracketed
                )
            )
        ]
        if exact_matches:
            matches = exact_matches
        else:
            matches = [
                h
                for h in headings
                if (
                    h.text.startswith(wanted)
                    or (wanted_plain and h.plain.startswith(wanted_plain))
                    or (
                        h.bracket_inner is not None
                        and wanted_unbracketed
                        and h.bracket_inner.startswith(wanted_unbracketed)
                    )
                )
            ]
    if len(matches) != 1:
        listed = "、".join(h.text for h in headings[:_MAX_LISTED_HEADINGS]) or "（没有识别出章节标题）"
        reason_code = "ambiguous" if matches else "not_found"
        reason_text = "匹配到多个标题" if matches else "没有找到这个标题"
        raise ContentRangeError(
            f"section「{wanted}」{reason_text}。文件里识别到的标题：{listed}。"
            "可以换成准确的标题，或改用 start_char / tail_chars 读取。",
            reason=reason_code,
        )
    start = matches[0]
    later = [h for h in headings if h.offset > start.offset and _stops_section(start, h)]
    end = later[0].offset if later else len(content)
    return start.offset, end, start.text


def apply_content_range(row: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """Cut ``row["content"]`` to the requested range and describe it. ``row`` is one by-id result."""
    content = row.get("content")
    if not isinstance(content, str):
        return row
    total = len(content)
    section = args.get("section")
    tail = args.get("tail_chars")
    start_char = args.get("start_char")
    limit = args.get("max_chars")

    picked = [name for name, value in (("section", section), ("tail_chars", tail), ("start_char", start_char))
              if value not in (None, "")]
    if len(picked) > 1:
        raise ContentRangeError("section、tail_chars、start_char 一次只能用一个")

    label: str | None = None
    fallback_note: str | None = None
    if section not in (None, ""):
        if not isinstance(section, str):
            raise ContentRangeError("section 必须是标题文字，例如「第3章」")
        try:
            start, end, label = _section_bounds(content, section)
            if end - start > MAX_RANGE_CHARS:
                end = start + MAX_RANGE_CHARS
        except ContentRangeError as exc:
            # 短文件上按 section 未命中（常见于对单章正文传 section 或场景关键词）：
            # 直接返回全文并附简明说明，不再硬报错浪费一轮往返；多义匹配与超长文件仍报错。
            if total <= SHORT_FILE_FULL_RETURN_MAX_CHARS and exc.reason == "not_found":
                start, end = 0, total
                fallback_note = (
                    f"未找到 section「{section.strip()}」，但该文件较短（{total} 字符），已直接返回全文；"
                    "单章正文文件可直接按 id 读取全文，无需传 section。"
                )
            else:
                raise
    elif tail not in (None, ""):
        count = _positive_int(tail, "tail_chars")
        start, end = max(0, total - min(count, MAX_RANGE_CHARS)), total
    else:
        start = _non_negative_int(start_char, "start_char")
        count = _positive_int(limit, "max_chars") if limit not in (None, "") else DEFAULT_RANGE_CHARS
        if start > total:
            raise ContentRangeError(f"start_char={start} 超出正文长度 {total}")
        if total <= SHORT_FILE_FULL_RETURN_MAX_CHARS:
            start, end = 0, total
        elif total - start <= SHORT_FILE_FULL_RETURN_MAX_CHARS:
            end = total
        else:
            end = min(total, start + min(count, MAX_RANGE_CHARS))

    sliced = dict(row)
    sliced["content"] = content[start:end]
    sliced["content_length"] = total
    sliced["content_range"] = {"start": start, "end": end}
    sliced["content_partial"] = (start, end) != (0, total)
    # 这一段的编辑器字数（#277）：核对单章篇幅不必再自己数，也不必为一个数字读全文。
    sliced["range_word_count"] = count_words(sliced["content"])
    if label is not None:
        sliced["section"] = label
    if sliced["content_partial"]:
        sliced["note"] = (
            f"只包含正文第 {start}–{end} 个字符（全文 {total} 字符）；范围外的内容没有读到，"
            "不要当成全文。word_count 是整个文件的字数，range_word_count 是这一段的字数。"
        )
    elif fallback_note is not None:
        sliced["note"] = fallback_note
    return sliced


def wants_range(args: dict[str, Any]) -> bool:
    return any(args.get(key) not in (None, "") for key in ("section", "tail_chars", "start_char"))


def _positive_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ContentRangeError(f"{name} 必须是正整数") from None
    if number <= 0:
        raise ContentRangeError(f"{name} 必须是正整数")
    return number


def _non_negative_int(value: Any, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ContentRangeError(f"{name} 必须是非负整数") from None
    if number < 0:
        raise ContentRangeError(f"{name} 必须是非负整数")
    return number
