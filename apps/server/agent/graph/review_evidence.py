"""送审前的程序检测证据：逐字复读与数字事实对照。

writer 写完交给 quality_reviewer 时，writing_graph 用这里的纯函数对刚写的正文做两项
确定性检测，把命中项作为「[自动检测：需核对]」附在交接信息末尾，供审稿人核对：

- ``find_repetition_evidence``：和前两章逐字相同的片段、同一文件内完全相同的段落、
  相邻段落之间的重复。模型自己审稿时很难逐字比对，复读是新用户审计里反复出现的问题。
- ``find_numeric_fact_pairs``：同一主语的年龄 / 时间数字在本章、前两章、角色卡、本章
  其他段落里不一致（「孩子五岁」↔「四岁」）。只给对照，不下结论——时间跳跃、回忆
  都可能让数字合理地变化，由审稿人判断。

这里只做字符串计算：不碰数据库，不调用模型。读取正文、超时与失败处理在 writing_graph。
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 跨章 / 相邻段落逐字重复的最短长度（去掉空白和标点后的字符数）。
REPEAT_WINDOW = 15
# 同一文件内两段完全相同时的最短长度。
IDENTICAL_PARAGRAPH_MIN = 10
MAX_REPETITION_EVIDENCE = 8
MAX_FACT_PAIRS = 6
EXCERPT_CHARS = 30


# ----------------------------------------------------------------- 规范化


def _is_ignorable(ch: str) -> bool:
    """空白和标点（含全角标点、引号、破折号、省略号）不参与逐字比对。"""
    if ch.isspace():
        return True
    category = unicodedata.category(ch)
    return category.startswith("P") or category.startswith("Z") or category in {"Sk", "Sm", "So", "Cc", "Cf"}


@dataclass(frozen=True)
class _Normalized:
    text: str  # 去掉空白和标点后的字符
    origin: list[int]  # text[i] 在原文中的下标
    paragraph: list[int]  # text[i] 所在段落的序号（从 1 开始，只数非空段落）


_LINE_BREAK_RE = re.compile(r"\r\n|\r|\n")


def _paragraphs(text: str) -> list[str]:
    """非空段落（按换行切分；只含空白的行不算段落）。"""
    return [line for line in _LINE_BREAK_RE.split(text or "") if line.strip()]


def _normalize(text: str) -> _Normalized:
    chars: list[str] = []
    origin: list[int] = []
    paragraph: list[int] = []
    paragraph_no = 0
    in_paragraph = False
    for index, ch in enumerate(text or ""):
        if ch in "\r\n":
            in_paragraph = False
            continue
        if not in_paragraph and not ch.isspace():
            paragraph_no += 1
            in_paragraph = True
        if _is_ignorable(ch):
            continue
        chars.append(ch)
        origin.append(index)
        paragraph.append(paragraph_no)
    return _Normalized("".join(chars), origin, paragraph)


def _normalize_plain(text: str) -> str:
    return "".join(ch for ch in text or "" if not _is_ignorable(ch))


def _excerpt(raw: str) -> str:
    compact = re.sub(r"\s+", "", raw or "")
    if len(compact) <= EXCERPT_CHARS:
        return compact
    return compact[:EXCERPT_CHARS] + "…"


def _grams(text: str, size: int) -> dict[str, int]:
    """text 中每个长度为 size 的子串第一次出现的位置。"""
    positions: dict[str, int] = {}
    for start in range(len(text) - size + 1):
        positions.setdefault(text[start : start + size], start)
    return positions


def _shared_spans(source: str, other: str, size: int) -> list[tuple[int, int, int]]:
    """source 中与 other 逐字相同（≥ size）的片段：[(source 起点, source 终点, other 起点)]。

    以 size 字为窗口滑动，命中的窗口前后相连或重叠时合并成一段。
    """
    if len(source) < size or len(other) < size:
        return []
    other_grams = _grams(other, size)
    spans: list[tuple[int, int, int]] = []
    current: list[int] | None = None  # [start, end, other_start]
    for start in range(len(source) - size + 1):
        other_start = other_grams.get(source[start : start + size])
        if other_start is None:
            continue
        end = start + size
        if current is not None and start <= current[1]:
            current[1] = max(current[1], end)
            continue
        if current is not None:
            spans.append((current[0], current[1], current[2]))
        current = [start, end, other_start]
    if current is not None:
        spans.append((current[0], current[1], current[2]))
    return spans


def _raw_slice(text: str, normalized: _Normalized, start: int, end: int) -> str:
    return text[normalized.origin[start] : normalized.origin[end - 1] + 1]


# ----------------------------------------------------------------- 复读检测


def _repeat_line(title: str, paragraph: int, other_title: str, other_paragraph: int, raw: str, length: int) -> str:
    return (
        f"《{title}》第 {paragraph} 段与《{other_title}》第 {other_paragraph} 段逐字重复："
        f"「{_excerpt(raw)}」（共 {length} 字）"
    )


def find_repetition_evidence(
    target_title: str,
    target_text: str,
    neighbors: list[tuple[str, str]],
) -> list[str]:
    """逐字复读证据，按重复长度从长到短，最多 8 条。

    - 跨章：去掉空白和标点后，target 与每个前章逐字相同 ≥ 15 字的片段。
    - 同一文件：两段规范化后完全相同（≥ 10 字）；相邻两段之间逐字重复 ≥ 15 字。
    """
    target = _normalize(target_text)
    found: list[tuple[int, str]] = []
    seen_spans: set[tuple[int, int]] = set()

    for neighbor_title, neighbor_text in neighbors:
        other = _normalize(neighbor_text)
        for start, end, other_start in _shared_spans(target.text, other.text, REPEAT_WINDOW):
            if (start, end) in seen_spans:
                continue
            seen_spans.add((start, end))
            length = end - start
            found.append(
                (
                    length,
                    _repeat_line(
                        target_title,
                        target.paragraph[start],
                        neighbor_title,
                        other.paragraph[other_start],
                        _raw_slice(target_text, target, start, end),
                        length,
                    ),
                )
            )

    paragraphs = _paragraphs(target_text)
    normalized_paragraphs = [_normalize_plain(paragraph) for paragraph in paragraphs]

    # 完全相同的两段（不要求相邻）：每段只和它第一次出现的位置配对一次。
    identical_pairs: set[tuple[int, int]] = set()
    first_seen: dict[str, int] = {}
    for index, normalized in enumerate(normalized_paragraphs):
        if len(normalized) < IDENTICAL_PARAGRAPH_MIN:
            continue
        if normalized in first_seen:
            first = first_seen[normalized]
            identical_pairs.add((first, index))
            found.append(
                (
                    len(normalized),
                    _repeat_line(
                        target_title, index + 1, target_title, first + 1, paragraphs[index], len(normalized)
                    ),
                )
            )
        else:
            first_seen[normalized] = index

    # 相邻两段之间的逐字重复（完全相同的已在上面报过）。
    for index in range(1, len(paragraphs)):
        if (index - 1, index) in identical_pairs:
            continue
        current = _normalize(paragraphs[index])
        previous = normalized_paragraphs[index - 1]
        for start, end, _ in _shared_spans(current.text, previous, REPEAT_WINDOW):
            length = end - start
            found.append(
                (
                    length,
                    _repeat_line(
                        target_title,
                        index + 1,
                        target_title,
                        index,
                        _raw_slice(paragraphs[index], current, start, end),
                        length,
                    ),
                )
            )

    found.sort(key=lambda item: item[0], reverse=True)
    return [line for _, line in found[:MAX_REPETITION_EVIDENCE]]


# ----------------------------------------------------------------- 数字事实对照

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_NUMERAL_CHARS = "".join(_CN_DIGITS) + "".join(_CN_UNITS)
_SUBJECT_CHAR = rf"(?:(?![{_CN_NUMERAL_CHARS}])[一-龥])"
# 主语与数字之间常见的虚词 / 时间词；不剥掉的话「念念今年四岁」的主语会变成「今年」。
_FILLERS = tuple(
    sorted(
        {
            "还不到", "才刚满", "刚刚满", "今年", "今天", "现在", "如今", "目前", "当时", "那时",
            "那年", "已经", "刚满", "刚刚", "刚好", "整整", "足足", "只有", "不过", "已", "才",
            "刚", "满", "都", "快", "只", "还",
        },
        key=len,
        reverse=True,
    )
)
# 剥不掉虚词时（「她今年四岁」）主语落在这些词上，说不清是谁：正文里丢弃，
# 角色卡里归到这张卡的角色。
_NON_SUBJECT_KEYS = frozenset(
    {"今年", "今天", "现在", "如今", "目前", "当时", "那时", "那年", "已经", "年龄", "岁数", "大概", "大约"}
)
_FACT_RE = re.compile(
    rf"(?P<subject>{_SUBJECT_CHAR}{{2,4}}?)"
    rf"(?P<filler>{'|'.join(_FILLERS)})?"
    rf"(?P<number>\d+|[{_CN_NUMERAL_CHARS}]+)"
    r"(?P<unit>岁|年前|年后|个月)"
)
# 角色卡里的「年龄：四岁」「年龄: 4」：主语是这张卡的角色。
_CARD_AGE_RE = re.compile(rf"年龄\s*[:：]?\s*(?P<number>\d+|[{_CN_NUMERAL_CHARS}]+)\s*(?P<unit>岁)?")
_CARD_LABEL_PREFIX = "角色："


def _parse_number(raw: str) -> int | None:
    if raw.isdigit():
        return int(raw)
    total = 0
    pending: int | None = None
    for ch in raw:
        if ch in _CN_DIGITS:
            if pending is not None:
                return None  # 「三四岁」这类连写的约数，不当作确定的数字
            pending = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            total += (pending if pending is not None else 1) * _CN_UNITS[ch]
            pending = None
        else:
            return None
    if pending is not None:
        total += pending
    return total


def _subject_key(subject: str) -> str | None:
    """主语只取最后两个字：「他说孩子」「孩子」都归到「孩子」，「林念念」归到「念念」。"""
    if "的" in subject:
        subject = subject.rsplit("的", 1)[1]
    return subject[-2:] if len(subject) >= 2 else None


@dataclass(frozen=True)
class _Fact:
    key: str
    unit: str
    value: int
    text: str  # 展示用：「孩子五岁」
    paragraph: int


def _extract_facts(text: str, *, fallback_key: str | None = None) -> list[_Fact]:
    """抽取「主语 + 数字 + 岁/年前/年后/个月」。

    主语说不清是谁（「今年」「现在」）时：给了 fallback_key（角色卡的角色名）就归到它，
    否则丢弃。
    """
    facts: list[_Fact] = []
    for paragraph_no, paragraph in enumerate(_paragraphs(text), start=1):
        for match in _FACT_RE.finditer(paragraph):
            key = _subject_key(match.group("subject"))
            value = _parse_number(match.group("number"))
            if key is None or value is None:
                continue
            tail = f"{match.group('filler') or ''}{match.group('number')}{match.group('unit')}"
            if key in _NON_SUBJECT_KEYS:
                if fallback_key is None:
                    continue
                facts.append(_Fact(fallback_key, match.group("unit"), value, f"{key}{tail}", paragraph_no))
                continue
            facts.append(_Fact(key, match.group("unit"), value, f"{key}{tail}", paragraph_no))
    return facts


def _card_name_key(label: str) -> str | None:
    name = label[len(_CARD_LABEL_PREFIX) :] if label.startswith(_CARD_LABEL_PREFIX) else label
    name = re.split(r"[（(【\[\s:：·-]", name.strip(), maxsplit=1)[0]
    name = "".join(ch for ch in name if "\u4e00" <= ch <= "\u9fa5")
    return name[-2:] if len(name) >= 2 else None


def _extract_card_facts(label: str, text: str) -> list[_Fact]:
    key = _card_name_key(label)
    facts = _extract_facts(text, fallback_key=key)
    if key is None:
        return facts
    for paragraph_no, paragraph in enumerate(_paragraphs(text), start=1):
        for match in _CARD_AGE_RE.finditer(paragraph):
            value = _parse_number(match.group("number"))
            if value is None:
                continue
            shown = f"{match.group('number')}{match.group('unit') or ''}"
            fact = _Fact(key, "岁", value, shown, paragraph_no)
            if not any(f.key == key and f.unit == "岁" and f.value == value for f in facts):
                facts.append(fact)
    return facts


def _conflicts(fact: _Fact, other: _Fact) -> bool:
    return other.key == fact.key and other.unit == fact.unit and other.value != fact.value


def find_numeric_fact_pairs(
    target: tuple[str, str],
    references: list[tuple[str, str]],
) -> list[str]:
    """同一主语的年龄 / 时间数字前后不一致的对照，最多 6 条。

    target 为 (标题, 正文)；references 为 (标签, 正文)，标签原样放进《》里——
    前章用章节标题，角色卡用「角色：念念」（这种卡里的「年龄：四岁」归到角色名）。
    target 自身不同段落之间的不一致也会列出。
    """
    target_title, target_text = target
    target_facts = _extract_facts(target_text)
    if not target_facts:
        return []

    pairs: list[str] = []
    seen: set[tuple[str, str]] = set()

    def _add(fact: _Fact, other_ref: str) -> None:
        signature = (fact.text, other_ref)
        if signature in seen:
            return
        seen.add(signature)
        pairs.append(f"《{target_title}》「{fact.text}」↔{other_ref}")

    for label, text in references:
        if label.startswith(_CARD_LABEL_PREFIX):
            reference_facts = _extract_card_facts(label, text)
        else:
            reference_facts = _extract_facts(text)
        for fact in target_facts:
            for other in reference_facts:
                if _conflicts(fact, other):
                    _add(fact, f"《{label}》「{other.text}」")

    for index, fact in enumerate(target_facts):
        for other in target_facts[index + 1 :]:
            if other.paragraph != fact.paragraph and _conflicts(fact, other):
                _add(fact, f"《{target_title}》第 {other.paragraph} 段「{other.text}」")

    return pairs[:MAX_FACT_PAIRS]


__all__ = [
    "MAX_FACT_PAIRS",
    "MAX_REPETITION_EVIDENCE",
    "REPEAT_WINDOW",
    "find_numeric_fact_pairs",
    "find_repetition_evidence",
]
