"""插入正文时的句子与段落边界。

edit_file 的 insert_*/append/prepend 以前把 text 原样塞进定位到的位置，不看
那里是不是句子或段落的边界：

- 整段文字插在段尾却没有换行开头，就接在上一段行尾；插在段首（下一段的
  「　　」之前）却没有换行结尾，下一段的缩进就跟在新段落行尾（U068）。
- 锚点停在「冰」而原文是「冰。」，一整句插进去就成了「冰他转身。。」。

这里只决定「插在哪、前后补什么分隔」，规则全部来自文件自己：段落分隔用插入点
两侧原有的换行串，缩进用两侧段落共同的缩进。text 里没有任何分段标记的一句话
仍按原来的方式接在段尾/段首，不猜模型要不要另起一段。
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter

from .text_matching import _DASH_CHARS, _PAUSE_CHARS, _SENTENCE_END_CHARS

INLINE_SPACE = " \t　"
_NEWLINES = "\r\n"
# 句末标点之后还属于这一句的收尾符号（闭引号、闭括号、法式/俄式书名号）。
_CLOSING_MARKS = frozenset("”’」』»）)】》〉〕")
_OPENING_MARKS = frozenset("“‘「『«（(【《〈〔")
_COLON_CHARS = frozenset("：:")
_LEADING_BLANK_LINES = re.compile(r"\A(?:[ \t　]*\r?\n)+")
_NEWLINE_RUN = re.compile(r"(?:\r?\n)+")
# Any Unicode letter or digit (Latin + diacritics, Cyrillic, Arabic, Hangul, CJK, etc.).
_WORD_CHAR = re.compile(r"[^\W_]")
# 章节/分集标题行：「第31章 …」「第五集」「Chapter 3」「Bab 1」「Capítulo 2」「Глава 1」「제1장」「الفصل 1」、markdown 标题。
# 标题的缩进跟本文已有的标题走，不套正文段首缩进；「第三部电影…」这种正文不算（单位字后面必须是行尾、空白或标题分隔符）。
# 只匹配去掉行首缩进之后的部分（见 _is_heading_line）：正则开头不带空白量词，长串 Tab 不会回溯。
_HEADING_LINE = re.compile(
    r"(?:#{1,6}[ \t]"
    r"|第[0-9０-９零〇一二两三四五六七八九十百千万]+[章节回集卷幕部篇](?=$|[ \t　:：·.、—-])"
    r"|(?i:chapter|episode|ep\.?|bab|cap[íi]tulo|epis[óo]dio|chương|chuong|t[ậa]p|глава|эпизод|серия|chapitre|épisode|rozdzia[łl]|odcinek|الفصل|الحلقة)[ \t]+\d"
    r"|제[ \t]*\d+[ \t]*[장화](?=$|[ \t　:：·.、—-]))"
)


def _is_heading_line(line: str) -> bool:
    return bool(_HEADING_LINE.match(unicodedata.normalize("NFC", line.lstrip(INLINE_SPACE))))

NOTE_SENTENCE_SNAP = "插入点原本落在句子中间，已移到这句话的边界"
NOTE_PARAGRAPH = "已按本文的段落分隔与缩进补齐新段落的前后分隔"
NOTE_LINE_START = "插入点在段首缩进之前，已移到缩进之后"
NOTE_INDENT_FOLLOWS_FILE = "old 的段首缩进和原文不一样（靠模糊匹配才对上），new 的段首缩进已改成和原文这一段一致"


def _leading_indent(line: str) -> str:
    return line[: len(line) - len(line.lstrip(INLINE_SPACE))]


def follow_span_indentation(content: str, start: int, end: int, old: str, new: str) -> str:
    """模糊 / 近似命中后，new 的段首缩进跟原文这一段走，而不是跟 old 的写法走。

    live run 2 场景 C：原文段落没有缩进，模型写的 old 却带「　　」；模糊匹配忽略空白照样
    命中，new 也带着「　　」写进去，一章从没有缩进变成了混用。规则：

    - 只看段首行：命中范围里每个换行之后的行；第一行只在命中从行首（或行首缩进之后）开始时算。
    - 原文这些行的缩进只有一种（X），old 对应行的缩进也只有一种（Y）时，new 里缩进是 Y 的段首行
      换成 X；原文自己就混用、或 old 混用时不动。第一行在原文缩进之后开始时，只补缺的那部分，
      不会叠出两层缩进。
    - 标题行、空行、缩进既不是 X 也不是 Y 的行不动。逐字命中（exact）不走这里。
    - 命中从行中间开始、old 第一行却带着段首缩进时，new 第一行照抄的同一段缩进去掉。
    """
    if not isinstance(new, str) or not new or start >= end:
        return new
    line_start = content.rfind("\n", 0, start) + 1
    prefix = content[line_start:start]
    first_is_paragraph = not prefix.strip(INLINE_SPACE)
    # 命中从行中间开始（原文「相框。相框里…」），模型却把 old 的第一行当段首写了缩进、new 照抄：
    # 这段缩进写进去就成了「相框。　　相框里」，去掉。
    # 命中范围自己从空白开始时（「He said  hello」），那段空白是原文的，照常写回，不去。
    if not first_is_paragraph and content[start:start + 1] not in INLINE_SPACE:
        old_indent = _leading_indent(old.split("\n", 1)[0]) if isinstance(old, str) else ""
        if old_indent and new.startswith(old_indent):
            new = new[len(old_indent):]

    def paragraph_indents(lines: list[str], first_prefix: str) -> set[str]:
        found: set[str] = set()
        for index, line in enumerate(lines):
            if (index == 0 and not first_is_paragraph) or not line.strip() or _is_heading_line(line):
                continue
            indent = _leading_indent(line)
            found.add(first_prefix + indent if index == 0 else indent)
        return found

    original = paragraph_indents(content[start:end].split("\n"), prefix)
    written = paragraph_indents(old.split("\n"), "")
    if len(original) != 1 or len(written) != 1:
        return new
    target, source = original.pop(), written.pop()
    if target == source and not prefix:
        return new
    lines = new.split("\n")
    for index, line in enumerate(lines):
        if (index == 0 and not first_is_paragraph) or not line.strip() or _is_heading_line(line):
            continue
        indent = _leading_indent(line)
        if indent != source:
            continue
        if index == 0:
            if not target.startswith(prefix):
                continue
            lines[index] = target[len(prefix):] + line[len(indent):]
        else:
            lines[index] = target + line[len(indent):]
    return "\n".join(lines)


FULL_WIDTH_INDENT = "　　"
_FIRST_INDENT_MIN_FOLLOWING = 3
_FIRST_INDENT_MIN_SHARE = 0.8
_CLOSING_QUOTES = frozenset("”’」』»")
_PARAGRAPH_LEAD_MARKS = frozenset("—…")
# 只有编号的行（「1.」「一、」「二。」）是小节序号，不是一段话。
_SECTION_NUMBER = re.compile(r"[0-9０-９零〇一二两三四五六七八九十百千]+[.．、。]?")


def _ends_like_sentence(line: str) -> bool:
    """这一行以句末标点（可带闭引号/闭括号）、破折号收尾，或以闭引号收尾：像一段话，不像标题或编号。"""
    text = line.rstrip(INLINE_SPACE + _NEWLINES)
    if _SECTION_NUMBER.fullmatch(text.lstrip(INLINE_SPACE)):
        return False
    end = len(text)
    while end > 0 and _is_closing(text, end - 1):
        end -= 1
    if end < len(text) and (text[end] in _CLOSING_QUOTES or text[end] == '"'):
        return True
    return end > 0 and (text[end - 1] in _SENTENCE_END_CHARS or text[end - 1] in _DASH_CHARS)


def _starts_like_paragraph(line: str) -> bool:
    """段首是文字、开引号，或「——」「……」开头的对白/省略（整行至少有一个字，分隔行不算）。"""
    if _WORD_CHAR.match(line[0]) or _is_opening(line, 0):
        return True
    return line[0] in _PARAGRAPH_LEAD_MARKS and bool(_WORD_CHAR.search(line))


def indent_unindented_first_paragraph(content: str, *, previous: str = "") -> str:
    """AI 整章写入的正文：后面的段落都以「　　」开头、只有第一段顶格时，给第一段补上「　　」。

    模型写 ``<file>`` 正文时偶尔漏掉第一段的缩进（后面各段都有）。只在稿子自己的体例足够明确时补：
    第一段之后的非空、非标题段落至少 3 段，其中至少 80% 的段首缩进恰好是「　　」。开头的标题行跳过，
    看标题下面的第一段。第一段已有任何缩进、不以文字/开引号/「——」「……」开头（分隔行等）、
    或不像一段话（不以句末标点、破折号或闭引号收尾，如「序章」「一」「Chapter One」「1-1 日 内」）
    时不动；没有缩进或缩进混用的稿子不动，英文稿件不用「　　」，自然不受影响。
    只在第一段行首插入两个全角空格，字数不变。

    ``previous`` 是这次写入要替换的原文。原文的第一段就是这一行、同样顶格时，这是原文自己的写法
    （作者的稿子，AI 整章写回时照抄），不改它，免得只差一个缩进的写回也算一次改动。
    """
    if not isinstance(content, str) or not content:
        return content
    lines = content.split("\n")
    paragraphs = [index for index, line in enumerate(lines) if line.strip()]
    position = 0
    while position < len(paragraphs) and _is_heading_line(lines[paragraphs[position]]):
        position += 1
    if position >= len(paragraphs):
        return content
    first_index = paragraphs[position]
    first = lines[first_index]
    if first[0] in INLINE_SPACE or not _starts_like_paragraph(first) or not _ends_like_sentence(first):
        return content
    following = [lines[index] for index in paragraphs[position + 1:] if not _is_heading_line(lines[index])]
    if len(following) < _FIRST_INDENT_MIN_FOLLOWING:
        return content
    indented = sum(1 for line in following if _leading_indent(line) == FULL_WIDTH_INDENT)
    if indented < _FIRST_INDENT_MIN_SHARE * len(following):
        return content
    if previous and _first_paragraph_line(previous.split("\n")) == first.rstrip("\r"):
        return content
    lines[first_index] = FULL_WIDTH_INDENT + first
    return "\n".join(lines)


def _first_paragraph_line(lines: list[str]) -> str | None:
    """开头标题行之后的第一个非空行（去掉行尾的 \\r）。"""
    for line in lines:
        if line.strip() and not _is_heading_line(line):
            return line.rstrip("\r")
    return None


def _ascii_quote_closes(text: str, index: int) -> bool:
    """行内第偶数个 ASCII 双引号是闭引号（按出现顺序配对）。"""
    line_start = text.rfind("\n", 0, index) + 1
    return text.count('"', line_start, index) % 2 == 1


def _is_closing(text: str, index: int) -> bool:
    ch = text[index]
    if ch in _CLOSING_MARKS:
        return True
    return ch == '"' and _ascii_quote_closes(text, index)


def _is_opening(text: str, index: int) -> bool:
    ch = text[index]
    if ch in _OPENING_MARKS:
        return True
    return ch == '"' and not _ascii_quote_closes(text, index)


def _follows_sentence_end(content: str, pos: int, *, allow_colon: bool) -> bool:
    """``pos`` 前面（跳过空白和闭引号）是不是一句话的结尾。

    ``allow_colon``：后面跟的是开引号时，「说：|“」也算一句结束、引语开始。
    闭引号前的破折号是话被打断（「“你——”|他」），同样算结束。
    """
    j = pos - 1
    closed = False
    while j >= 0 and (content[j] in INLINE_SPACE or _is_closing(content, j)):
        closed = closed or content[j] not in INLINE_SPACE
        j -= 1
    if j < 0 or content[j] in _NEWLINES:
        return True
    ch = content[j]
    return (
        ch in _SENTENCE_END_CHARS
        or (allow_colon and ch in _COLON_CHARS)
        or (closed and ch in _DASH_CHARS)
    )


def is_sentence_boundary(content: str, pos: int) -> bool:
    """``pos`` 是否落在两句话之间（或行首、行尾、文件首尾）。

    - 句末标点和它后面的闭引号属于前一句：「冰|。」「冰。|”」都不是边界，
      「冰。”|他」是。行首缩进前后都算行首。
    - 引语的开头：句末或冒号后面的开引号，引号前后都是边界（「说：|“|我们」），
      插入的整句不会被挪出引语，也不会被挪进引语。句子中间的引号（「看着“老王”」）
      不算。
    - 被打断的话：「“你——”|他」是边界。
    """
    if pos <= 0 or pos >= len(content):
        return True
    if content[pos] in _NEWLINES or content[pos - 1] in _NEWLINES:
        return True
    nxt = content[pos]
    if nxt in _SENTENCE_END_CHARS or nxt in _PAUSE_CHARS or _is_closing(content, pos):
        return False
    quote_start = pos
    while quote_start > 0 and _is_opening(content, quote_start - 1):
        quote_start -= 1
    if quote_start < pos:  # 紧跟在开引号后面：看这个引号本身是不是在句首
        return _follows_sentence_end(content, quote_start, allow_colon=True)
    return _follows_sentence_end(content, pos, allow_colon=_is_opening(content, pos))


def exact_anchor_snap_forward(content: str, pos: int, *, direction: str) -> bool | None:
    """逐字命中的锚点要不要挪、往哪挪；None 表示原样插在锚点处。

    模型照着原文选的锚点，边缘是标点（引号、冒号、破折号、逗号……）时，那就是它
    要的位置：引语里、冒号后面、打断的话后面，都原样插入。只有插入点把一个字和
    它后面的标点、或者半句话的两半分开时才挪（「一会儿|。」「他转身|走了」）：
    insert_after 往后挪到句末；insert_before 往前挪到句首，锚点以句号/逗号开头时
    越过那串标点。调用前已确认 ``pos`` 不是句子边界，因此两侧都有字符。
    """
    if not content[pos - 1].isalnum():
        return None
    if direction == "after":
        return True
    return not content[pos].isalnum()


def snap_to_sentence_boundary(content: str, pos: int, *, forward: bool) -> int:
    """从 ``pos`` 往后（或往前）走到最近的句子边界；最远到行尾/行首。"""
    step = 1 if forward else -1
    while not is_sentence_boundary(content, pos):
        pos += step
    return pos


def is_sentence_shaped(text: str) -> bool:
    """text 是不是一整句（或几段）话，而不是接在半句后面的分句。

    以逗号、句号、闭引号开头的是分句或收尾，要紧贴锚点；带换行或段首缩进的、
    以句末标点（可带闭引号）收尾的才算整句。
    """
    stripped = text.strip()
    if not stripped:
        return False
    first = stripped[0]
    if first in _PAUSE_CHARS or first in _SENTENCE_END_CHARS or (
        first in _CLOSING_MARKS
    ):
        return False
    if _starts_with_indent(text) or text[0] in _NEWLINES or "\n" in stripped:
        return True
    if text.rstrip(INLINE_SPACE)[-1:] in ("\n", "\r"):
        return True
    end = len(stripped)
    while end > 0 and _is_closing(stripped, end - 1):
        end -= 1
    return end > 0 and stripped[end - 1] in _SENTENCE_END_CHARS


def _starts_with_indent(text: str) -> bool:
    """段首缩进：全角空格、制表符或至少两个半角空格。

    单个半角空格是英文词间距（「Hello world.」后追加「 Goodbye.」），不是分段。
    """
    return text[:1] in ("　", "\t") or text[:2] == "  "


def _paragraph_markers(text: str) -> tuple[bool, bool, bool, bool]:
    """(以换行开头, 以缩进开头, 以换行结尾, 中间有换行)。"""
    starts_break = bool(_LEADING_BLANK_LINES.match(text))
    starts_indent = not starts_break and _starts_with_indent(text)
    ends_break = text.rstrip(INLINE_SPACE)[-1:] in ("\n", "\r")
    inner_break = "\n" in text.strip()
    return starts_break, starts_indent, ends_break, inner_break


def _line_indent(content: str, line_start: int) -> str:
    end = line_start
    while end < len(content) and content[end] in INLINE_SPACE:
        end += 1
    return content[line_start:end]


def _dominant_separator(content: str, text: str) -> str:
    runs = Counter(_NEWLINE_RUN.findall(content))
    if runs:
        return runs.most_common(1)[0][0]
    own = _NEWLINE_RUN.search(text)
    return own.group(0) if own else "\n"


def _heading_indent(content: str) -> str:
    """本文已有标题行最常用的缩进；没有标题（或标题都顶格）时为空。"""
    indents = Counter(
        _line_indent(line, 0) for line in content.split("\n") if _is_heading_line(line)
    )
    return indents.most_common(1)[0][0] if indents else ""


def _apply_indent(block: str, indent: str, heading_indent: str = "") -> str:
    """给 block 里没有缩进、且有正文字符的行补上本文的段首缩进；标题行用本文标题的缩进。"""
    if not indent and not heading_indent:
        return block
    lines = block.split("\n")
    for n, line in enumerate(lines):
        if not line or line[0] in INLINE_SPACE:
            continue
        if _is_heading_line(line):
            lines[n] = heading_indent + line
        elif indent and (_WORD_CHAR.match(line[0]) or _is_opening(line, 0)):
            lines[n] = indent + line
    return "\n".join(lines)


def layout_insertion(
    content: str, pos: int, text: str, *, direction: str
) -> tuple[int, str, list[str]]:
    """决定把 ``text`` 插在 ``pos`` 附近的哪个位置、前后补什么分隔。

    ``direction`` 是 "after"（insert_after/append：text 跟在前文后面）或
    "before"（insert_before/prepend：text 放在后文前面）。返回
    ``(插入位置, 实际写入的文本, 说明)``，没有调整时说明为空列表。
    """
    if not text or not content.strip() or not text.strip():
        return pos, text, []

    before = content[:pos]
    prev_end = len(before.rstrip())
    after = content[pos:]
    next_start = pos + (len(after) - len(after.lstrip()))
    has_prev = prev_end > 0
    has_next = next_start < len(content)
    gap = content[prev_end:next_start]
    break_before = not has_prev or "\n" in content[prev_end:pos]
    break_after = not has_next or "\n" in content[pos:next_start]
    if not (break_before or break_after):
        return pos, text, []  # 段落中间：按模型给的位置原样插入

    starts_break, starts_indent, ends_break, inner_break = _paragraph_markers(text)
    paragraph = starts_break or starts_indent or ends_break or (inner_break and break_before)

    if not paragraph:
        # 一句话接在段尾或段首：位置不动，只是不能夹在段首缩进前面或两段之间。
        if has_prev and has_next and break_before and break_after:
            target = prev_end if direction == "after" else next_start
        elif break_before and has_next and not break_after:
            target = next_start
        else:
            return pos, text, []
        if target == pos:
            return pos, text, []
        return target, text, [NOTE_LINE_START]

    last_newline = gap.rfind("\n")
    next_line_start = prev_end + last_newline + 1 if last_newline >= 0 else prev_end
    prev_line_start = content.rfind("\n", 0, prev_end) + 1 if has_prev else 0
    prev_indent = _line_indent(content, prev_line_start) if has_prev else ""
    next_indent = _line_indent(content, next_line_start) if has_next else ""

    core = _LEADING_BLANK_LINES.sub("", text)
    heading_indent = _heading_indent(content)
    if has_prev and has_next:
        indent = prev_indent if prev_indent and prev_indent == next_indent else ""
        first_newline = gap.find("\n")
        if first_newline > 0 and gap[first_newline - 1] == "\r":
            first_newline -= 1
        separator = gap[first_newline : last_newline + 1]
        placed_at = prev_end
        written = separator + _apply_indent(core.rstrip(), indent, heading_indent)
    elif has_next:  # 文件开头
        written = core.rstrip() + _dominant_separator(content, text)
        placed_at = next_line_start
    else:  # 文件末尾：保留模型自己写的结尾，后面接着追加的写法不受影响
        placed_at = len(content)
        if "\n" in gap:
            written = _apply_indent(core, prev_indent, heading_indent)
        elif starts_break or starts_indent:
            written = _dominant_separator(content, text) + _apply_indent(core, prev_indent, heading_indent)
        else:
            # 前文没以换行收尾、text 开头也没有分段标记：这是接着上一块往下写
            # （续写被截断的半句，或分块追加），第一行接在行尾，不另起一段；
            # text 结尾的换行只说明下一块从新行开始。后面各行照常补缩进。
            first, newline, rest = core.partition("\n")
            written = first + newline + _apply_indent(rest, prev_indent, heading_indent)

    if (placed_at, written) == (pos, text):
        return pos, text, []
    return placed_at, written, [NOTE_PARAGRAPH]


__all__ = [
    "NOTE_LINE_START",
    "NOTE_PARAGRAPH",
    "NOTE_SENTENCE_SNAP",
    "exact_anchor_snap_forward",
    "indent_unindented_first_paragraph",
    "is_sentence_boundary",
    "is_sentence_shaped",
    "layout_insertion",
    "snap_to_sentence_boundary",
]
