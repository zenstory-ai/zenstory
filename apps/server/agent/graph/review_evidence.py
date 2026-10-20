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


# ----------------------------------------------------------------- 篇幅要求

# 作者说过的篇幅要求（「单章约2000字」「每集500-900字」「每章字数要求1000字以上」）。
# 审稿人原来只认作者本轮原话里的篇幅（subagents.py「没达到用户原话里明确的篇幅」），
# 早先说过的、写进项目设定的要求它看不到（I05：U075 1369 字对 2000 字的要求被判通过，
# U070「作者本轮没有指定篇幅，不按篇幅返工」）。这里只把原话摘出来交给审稿人，
# 不换算成数字下结论：「8000字左右的短篇」说的是全篇还是单章，由审稿人看上下文判断。
MAX_LENGTH_REQUIREMENTS = 6
_LENGTH_CLAUSE_MAX_CHARS = 60
_LENGTH_CLAUSE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")
_LENGTH_NUMBER_RE = re.compile(
    r"(?:\d[\d,，]*(?:\.\d+)?|[一二两三四五六七八九十百千万]+)\s*(?:多|余)?\s*(?:[kK千万])?\s*字"
)
_LENGTH_CUE_RE = re.compile(
    r"每[一]?[章集节篇幕]|单[章集篇]|一[章集]|章节|全[文章篇]|本[章集]|这[一]?[章集]|篇幅|字数"
    r"|左右|上下|以上|以内|以下|不少于|不低于|不超过|至少|起码|控制在|保持在|扩[写充]?到|写到|写满|写够"
)


# 篇幅目标的容差：作者给的是单个目标数（「单章约2000字」）时，实际字数在目标 ±15% 内就算达标。
# 生产里返修 writer 为把 3377 字削到 2899 字连续调了约 25 次 edit_file（「2905，还多 5 字」），
# 每次都重发整段上下文；单个数字本来就是约数，几个百分点的差不是缺陷。作者给了范围或
# 明确上下限（「500-900字」「不超过3000字」「至少2000字」）时，那一侧是硬要求，不放宽。
LENGTH_TOLERANCE = 0.15
_AMOUNT = r"\d[\d,，]*(?:\.\d+)?|[零〇一二两三四五六七八九十百千万]+"
_LENGTH_AMOUNT_RE = re.compile(rf"(?P<number>{_AMOUNT})\s*(?P<more>多|余)?\s*(?P<unit>[kK千万])?\s*字")
_LENGTH_RANGE_RE = re.compile(
    rf"(?P<low>{_AMOUNT})\s*(?P<low_unit>[kK千万])?\s*字?\s*(?:-|~|～|—|–|至|到)+\s*"
    rf"(?P<high>{_AMOUNT})\s*(?P<high_unit>[kK千万])?\s*字"
)
# 上下限用语：「不超过 / 至少」在数字前面（中间最多隔几个字，如「最多写3000字」），
# 「以内 / 以上」紧跟在「字」后面。只看数字附近，「以下是要求：每章2000字」不算上限。
_LENGTH_UPPER_PREFIX_RE = re.compile(r"(?:不超过|不要超过|别超过|不能超过|不多于|最多|上限)[^\d零〇一二两三四五六七八九十]{0,3}$")
_LENGTH_LOWER_PREFIX_RE = re.compile(r"(?:不少于|不能少于|不低于|至少|起码|最少|下限|写满|写够)[^\d零〇一二两三四五六七八九十]{0,3}$")
_LENGTH_UPPER_SUFFIX_RE = re.compile(r"\s*(?:以内|以下|之内)")
_LENGTH_LOWER_SUFFIX_RE = re.compile(r"\s*以上")
_AMOUNT_UNITS = {"k": 1000, "K": 1000, "千": 1000, "万": 10000}


@dataclass(frozen=True)
class LengthBounds:
    """一条篇幅要求的达标区间（编辑器字数）；low / high 为 None 表示那一侧没有限制。"""

    low: int | None
    high: int | None
    hard: bool  # True：作者给的范围或上下限；False：单个目标数放宽 ±LENGTH_TOLERANCE

    def describe(self) -> str:
        if not self.hard:
            return f"达标区间 {self.low}–{self.high} 字（目标 ±{round(LENGTH_TOLERANCE * 100)}%）"
        if self.low is not None and self.high is not None:
            return f"硬要求 {self.low}–{self.high} 字"
        if self.high is not None:
            return f"硬上限 {self.high} 字"
        return f"硬下限 {self.low} 字"


def _parse_chinese_amount(raw: str) -> int | None:
    """「两千」「两千五」「一万五」「两千零五」「十五」；「三四千」这类约数返回 None。"""
    total = section = 0
    pending: int | None = None
    last_unit = 0
    for ch in raw:
        if ch in "零〇":
            last_unit = 1  # 「两千零五」：零后面的数字按个位算
            continue
        if ch in _CN_DIGITS:
            if pending is not None:
                return None
            pending = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            section += (pending if pending is not None else 1) * _CN_UNITS[ch]
            last_unit, pending = _CN_UNITS[ch], None
        elif ch == "万":
            total += (section + (pending or 0) or 1) * 10000
            section, last_unit, pending = 0, 10000, None
        else:
            return None
    if pending is not None:
        # 「两千五」= 2500：末尾的数字是上一个单位的下一位。
        section += pending * (last_unit // 10 if last_unit >= 100 else 1)
    return total + section


def _parse_amount(raw: str, unit: str | None) -> int | None:
    raw = raw.replace(",", "").replace("，", "")
    if raw[0].isdigit():
        try:
            value = float(raw)
        except ValueError:
            return None
    else:
        parsed = _parse_chinese_amount(raw)
        if parsed is None:
            return None
        value = float(parsed)
    return round(value * _AMOUNT_UNITS.get(unit or "", 1)) or None


def length_requirement_bounds(clause: str) -> LengthBounds | None:
    """把一条篇幅原话换算成达标区间；说不清（多个数字、「三千多字」、约数连写）时返回 None。

    - 「500-900字」「2000到3000字」：作者给的范围，硬要求。
    - 单个数字带「不超过 / 以内 / 最多」：硬上限；带「至少 / 不少于 / 以上」：硬下限。
    - 其余单个数字（「约2000字」「3000字左右」「扩写到3000字」）：目标 ±LENGTH_TOLERANCE。

    只换算数字，不判断说的是全篇还是单章：那由读原话的角色按上下文判断。
    """
    text = clause or ""
    range_match = _LENGTH_RANGE_RE.search(text)
    if range_match:
        if len(_LENGTH_AMOUNT_RE.findall(text[: range_match.start()] + text[range_match.end() :])):
            return None
        low_unit = range_match.group("low_unit") or range_match.group("high_unit")
        low = _parse_amount(range_match.group("low"), low_unit)
        high = _parse_amount(range_match.group("high"), range_match.group("high_unit"))
        if low is None or high is None or low >= high:
            return None
        return LengthBounds(low, high, hard=True)
    amounts = list(_LENGTH_AMOUNT_RE.finditer(text))
    if len(amounts) != 1 or amounts[0].group("more"):
        return None
    amount = amounts[0]
    target = _parse_amount(amount.group("number"), amount.group("unit"))
    if target is None:
        return None
    prefix, suffix = text[: amount.start()], text[amount.end() :]
    upper = bool(_LENGTH_UPPER_PREFIX_RE.search(prefix) or _LENGTH_UPPER_SUFFIX_RE.match(suffix))
    lower = bool(_LENGTH_LOWER_PREFIX_RE.search(prefix) or _LENGTH_LOWER_SUFFIX_RE.match(suffix))
    if upper != lower:
        return LengthBounds(None, target, hard=True) if upper else LengthBounds(target, None, hard=True)
    if upper:
        return None
    return LengthBounds(round(target * (1 - LENGTH_TOLERANCE)), round(target * (1 + LENGTH_TOLERANCE)), hard=False)


def find_length_requirements(sources: list[tuple[str, str]]) -> list[str]:
    """从作者的话和项目设定里摘出篇幅要求，返回「「原话」（来源）→ 达标区间」列表，按出现顺序、去重。

    sources 是 (来源说明, 文本)，按时间先后排列。只认同时带「数字 + 字」和篇幅用语的短分句；
    长句（多半是贴进来的正文）不看。最多保留最后 MAX_LENGTH_REQUIREMENTS 条。
    能换算时在原话后面附上达标区间（见 length_requirement_bounds），换算不了只给原话。
    """
    found: list[str] = []
    seen: set[str] = set()
    for label, text in sources:
        if not isinstance(text, str) or not text.strip():
            continue
        for clause in _LENGTH_CLAUSE_SPLIT_RE.split(text):
            clause = clause.strip(" \t，,、：:")
            if not clause or len(clause) > _LENGTH_CLAUSE_MAX_CHARS:
                continue
            if not _LENGTH_NUMBER_RE.search(clause) or not _LENGTH_CUE_RE.search(clause):
                continue
            key = re.sub(r"\s+", "", clause)
            if key in seen:
                continue
            seen.add(key)
            item = f"「{clause}」（{label}）" if label else f"「{clause}」"
            bounds = length_requirement_bounds(clause)
            found.append(f"{item} → {bounds.describe()}" if bounds else item)
    return found[-MAX_LENGTH_REQUIREMENTS:]


__all__ = [
    "LENGTH_TOLERANCE",
    "LengthBounds",
    "MAX_FACT_PAIRS",
    "MAX_LENGTH_REQUIREMENTS",
    "MAX_REPETITION_EVIDENCE",
    "REPEAT_WINDOW",
    "find_length_requirements",
    "find_numeric_fact_pairs",
    "find_repetition_evidence",
    "length_requirement_bounds",
]
