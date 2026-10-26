"""送审前的程序检测证据：逐字复读与角色卡事实。

writer 写完交给 quality_reviewer 时，writing_graph 用这里的纯函数对刚写的正文做两项
确定性检测，把命中项作为「[自动检测：需核对]」附在交接信息末尾，供审稿人核对：

- ``find_repetition_evidence``：和前两章逐字相同的片段、同一文件内完全相同的段落、
  相邻段落之间的重复。模型自己审稿时很难逐字比对，复读是新用户审计里反复出现的问题。
- ``list_character_card_facts``：角色卡上明写的「年龄：N」，原样列给审稿人对照本轮正文。
  不从正文里猜主语、不判定矛盾，由审稿人判断。

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
MAX_CARD_FACTS = 6
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


# ----------------------------------------------------------------- 角色卡事实

_CN_NUMERALS = "零〇一二两三四五六七八九十百千"
# 角色卡里明写的「年龄：四岁」「年龄: 52」。
_CARD_AGE_RE = re.compile(rf"年龄\s*[:：]?\s*(?P<value>(?:\d+|[{_CN_NUMERALS}]+)\s*岁?)")


def list_character_card_facts(cards: list[tuple[str, str]]) -> list[str]:
    """角色卡上明写的年龄字段，原样列出供审稿人对照本轮正文，最多 6 条。

    cards 为 (角色卡标题, 正文)。只读卡里的结构化字段，不从正文猜主语、不判定矛盾。
    """
    facts: list[str] = []
    for title, text in cards:
        for paragraph in _paragraphs(text):
            for match in _CARD_AGE_RE.finditer(paragraph):
                value = re.sub(r"\s+", "", match.group("value"))
                line = f"《角色：{title}》年龄：{value}"
                if line not in facts:
                    facts.append(line)
    return facts[:MAX_CARD_FACTS]


# ----------------------------------------------------------- 作者要求原文

_SOURCE_TRUNCATION_MARKER = "\n…（中间内容已截断）…\n"


def quote_author_requirement_sources(
    sources: list[tuple[str, str]], *, max_chars: int = 4000
) -> list[str]:
    """原文来源各留首尾份额，余量优先最新；不按语义筛选或改写。

    sources 按时间排列，返回仍按时间排列。标签、分隔换行和截断标记
    均计入预算；预算不足以容纳全部来源的最小摘录时，优先保留最新来源。
    """
    if max_chars <= 0:
        return []

    selected: list[tuple[str, str, int]] = []
    remaining = max_chars
    for source in reversed(sources or []):
        if not isinstance(source, (tuple, list)) or len(source) != 2:
            continue
        label, text = source
        if not isinstance(label, str) or not isinstance(text, str) or not text.strip():
            continue
        prefix = f"【{label.strip() or '来源'}】\n"
        minimum = min(len(text), len(_SOURCE_TRUNCATION_MARKER) + 2)
        cost = len(prefix) + minimum + bool(selected)
        if cost > remaining:
            break
        selected.append((prefix, text, minimum))
        remaining -= cost

    if not selected:
        return []
    selected.reverse()
    allocations = [minimum for _, _, minimum in selected]
    # 先为每个来源增加同等份额，避免长本轮样文饿死项目风格和早期要求。
    fair_extra = remaining // (2 * len(selected))
    for index, (_, text, _) in enumerate(selected):
        extra = min(fair_extra, len(text) - allocations[index])
        allocations[index] += extra
        remaining -= extra
    # 保留最新要求优先级；不尝试猜测哪一句更重要。
    for index in reversed(range(len(selected))):
        extra = min(remaining, len(selected[index][1]) - allocations[index])
        allocations[index] += extra
        remaining -= extra

    quoted = []
    for (prefix, text, _), allocation in zip(selected, allocations, strict=True):
        if allocation < len(text):
            available = allocation - len(_SOURCE_TRUNCATION_MARKER)
            tail_chars = available // 2
            text = (text[:available - tail_chars] + _SOURCE_TRUNCATION_MARKER
                    + (text[-tail_chars:] if tail_chars else ""))
        quoted.append(prefix + text)
    return quoted



__all__ = [
    "MAX_CARD_FACTS",
    "MAX_REPETITION_EVIDENCE",
    "REPEAT_WINDOW",
    "find_repetition_evidence",
    "list_character_card_facts",
    "quote_author_requirement_sources",
]
