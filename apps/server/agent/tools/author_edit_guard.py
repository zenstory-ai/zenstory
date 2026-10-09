"""作者手动改过的文件：作者本轮没点名时，AI 不改、不删，改为问作者。

2026-10-09 审计 P1-3：作者把第 3 章的「老周」手动改成「老秦」（19 处），接着发「继续写
下一章」。AI 写完第 4 章后把第 3 章 19 处全改了回去，还在总结里说「顺手修的两处」。

规则（工具层兜底，提示词里有同一条要求）：
- 文件的当前正文最后是作者自己写下的（最新版本不是 AI 写的，或正文在 AI 版本之后又被
  改过却没留版本），而作者本轮的话没有指向这个文件时，edit_file / delete_file（含递归删除
  文件夹里的这类文件）/ create_file 复用同名剧集不执行，返回 error_type=author_edit_protected，
  告诉模型以作者的写法为准、有出入就问作者。
- 「指向这个文件」：作者点名它并要求改它（标题或标题里的词、第 N 章 / 集、前 N 章，同一分句
  或下一分句里有「改 / 润色 / 精简…」）、「全书 / 所有章节」并要求改、大纲 / 人设这类文件
  类别并要求改、附加或引用了它；作者正开着它时说「这一章 / 这里」，或者提了改动且没点名别的
  文件、不是要下一章或接着往下写（「把开头改得更有悬念一点」）；上一轮 AI 因为这条规则没改
  它、作者这一轮不点名别的章、说清了往哪改（「统一成老周吧」「改回老周」），且每处修改都是
  换成这个写法。上一轮 AI 问的是「要把第2章也改成老秦吗？」，所以「好 / 可以」答应的是改
  其他章，不放开作者改过的这一章。
- 点名了但不是要改它：拿它当标准 / 来源（「以第3章为准」「按第3章统一人名」「和第3章的
  人名保持统一」「第4章沿用第3章的改动」）；作者在讲它改过了（「第3章改好了」「第3章老周
  改成老秦了」「第3章有改动」「第3章是我改过的，以它为准统一一下其他章」——讲完改过的，
  下一分句的要求不算到它头上）。
- 不算要求改的说法：「改变 / 改天」（「把结局改变一下」算）、「继续更新」（发新章）、作者讲
  自己改过的（「我把老周改成老秦了」「第3章我改过了」）、抱怨（「你怎么把第3章改了」）；
  没点名哪一章时不带方向的「统一」（「人名要统一」「统一一下格式」）——作者改的写法是最新
  设定，往回统一要作者说清楚。
- 递归删除文件夹：作者点名这个文件夹并说删它（「正文里的第2章删掉」删的是里面的东西，不算）
  才整个删；否则里面作者手改过的文件要点名并在那一处说删。
- 「把老秦改成老周」这类改名要求：只替换 / 删除含「老秦」的原文时放行；「把主角的名字改成
  李明」只放行改成的文字里有「李明」的小段替换。
- 作者正开着的文件只追加（op=append）、且本轮不是写下一章时放行：作者写了半章说
  「继续写」，AI 接着往后写不改动作者已写的字。

只在应用内对话（ToolContext 带着作者原话）时生效；没有作者原话的调用路径不受影响。
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from utils.title_sequence import parse_chinese_number

AUTHOR_EDIT_PROTECTED_ERROR = "author_edit_protected"

# 版本来源（与 models.file_version 的常量一致；这里不导入 models，保持模块可单测）。
_AI_CHANGE_SOURCE = "ai"
_AI_EDIT_CHANGE_TYPE = "ai_edit"

_SEQ_UNITS = "章集回节话幕场卷"
_CN_DIGITS = "零〇一二两三四五六七八九十百千"
_NUMBER = rf"(?:\d+|[{_CN_DIGITS}]+)"
_SINGLE_SEQ_RE = re.compile(rf"第\s*({_NUMBER})\s*([{_SEQ_UNITS}])")
_RANGE_SEQ_RE = re.compile(
    rf"第\s*({_NUMBER})\s*(?:[{_SEQ_UNITS}])?\s*(?:-|－|—|~|～|到|至)\s*第?\s*({_NUMBER})\s*([{_SEQ_UNITS}])"
)
_FIRST_N_SEQ_RE = re.compile(rf"前\s*({_NUMBER})\s*([{_SEQ_UNITS}])")
_ENUM_SEQ_RE = re.compile(
    rf"第\s*((?:{_NUMBER}\s*[、,，和及与]\s*)+{_NUMBER})\s*([{_SEQ_UNITS}])"
)

# 指向「全书」的说法：作者明确要求跨文件改动（同一分句或下一分句里还要有改动的说法）。
# 「统一」不在这里：「统一一下格式再写下一章」「人名要统一」不等于授权改所有已有章节。
_GLOBAL_SCOPE_MARKERS: tuple[str, ...] = (
    "全书",
    "全文",
    "通篇",
    "整本",
    "所有章节",
    "全部章节",
    "所有的章节",
    "每一章",
    "每章",
    "各章",
    "所有集",
    "每一集",
    "每集",
    "所有文件",
    "全部文件",
)

# 「这一章 / 这里」：指作者正开着的文件。
_FOCUS_DEICTIC_MARKERS: tuple[str, ...] = (
    "这一章",
    "这章",
    "本章",
    "当前章",
    "这一集",
    "这集",
    "本集",
    "这一篇",
    "这篇",
    "本篇",
    "这个文件",
    "当前文件",
    "这一段",
    "这段",
    "这一句",
    "这句",
    "这里",
    "这儿",
    "这部分",
    "选中",
)

# 本轮要的是下一章 / 新章：作者正开着的文件也不往里改、不往里追加。
_NEXT_UNIT_MARKERS: tuple[str, ...] = (
    "下一章",
    "下章",
    "下一集",
    "下集",
    "下一节",
    "下一回",
    "新的一章",
    "新一章",
    "新章节",
    "新的一集",
    "新一集",
)

# 文件类别的叫法：作者说「改一下大纲 / 人设」时指向这一类文件（要同时有改动的说法，
# 「按大纲写第5章」不算）。正文 / 剧本不在这里——「继续写正文」不等于授权改已有章节。
_FILE_TYPE_MARKERS: dict[str, tuple[str, ...]] = {
    "outline": ("大纲", "细纲", "梗概"),
    "character": ("人设", "角色卡", "人物卡", "角色设定", "人物设定"),
    "lore": ("设定", "世界观"),
}
# 改动的说法。「写」「续写」不算：「继续写」不是要改已有的字。
_EDIT_VERBS: tuple[str, ...] = (
    "改",
    "修正",
    "修订",
    "修一下",
    "修修",
    "修掉",
    "修好",
    "调整",
    "更新",
    "补充",
    "补上",
    "完善",
    "重写",
    "删",
    "去掉",
    "加上",
    "添加",
    "同步",
    "统一",
    "润色",
    "精简",
    "压缩",
    "缩短",
    "扩写",
    "加强",
    "强化",
    "优化",
    "打磨",
    "收紧",
    "替换",
    "换成",
    "重做",
)

# 否定：「别改第3章」「不要动已经写好的章节」里的指向不算。只往前看同一分句里的几个字。
# 「别」排除「别的 / 别人 / 分别 / 特别」这类不是否定的词。
_NEGATION_RE = re.compile(
    r"(?:(?<![分区特告性级类识差个错辨离道送鉴判派])别(?![的人处])|不要|不用|不必|勿|没让|不许|禁止|不能|无需|不需要|先不)"
)
_NEGATION_LOOKBACK = 8
_CLAUSE_DELIMITERS = "，,。！？!?；;\n"

# 作者回答 AI「要不要改」的提问：不改的说法（整句里出现就不算答应）。
_DECLINE_RE = re.compile(
    r"(?:(?<![分区特告性级类识差个错辨离道送鉴判派])别(?![的人处])|不要|不用|不必|先不|算了|不改|保持|维持|"
    r"就叫|就这样|按我的|按我改的|无需|不需要|不了|我自己|我来改|自己来|\bno\b|don'?t|\bkeep\b)"
)
# 作者在让 AI 接着往下写（不是在回答要不要改）。
_MOVING_ON_MARKERS: tuple[str, ...] = ("继续写", "接着写", "往下写", "续写", "继续更新", "接着更新")

# 「把老秦改成老周」：改名 / 替换的来源词和目标词。
_RENAME_VERB_RE = re.compile(
    r"(?:改成|改为|改回|换成|替换成|替换为|改叫|改名为|改名成|改名叫|统一成|统一为|统一叫)"
)
_RENAME_SOURCE_SPLIT_RE = re.compile(r"[的把将，,。；;、\s「」“”\"'『』（）()]+")
_RENAME_SOURCE_TRAILING_RE = re.compile(r"(?:全部|全都|都|统一|一律|也|再)+$")
_RENAME_TARGET_SPLIT_RE = re.compile(r"[，,。；;、！？!?\s「」“”\"'『』（）()]+")
_RENAME_TARGET_TRAILING_RE = re.compile(r"(?:吧|了|啊|呀|哦|嘛|呢|就行|就好|即可|好了)+$")
# 「把主角的名字改成李明」：被换掉的是「名字」这类泛称，原文里找不到，按新名字判断。
_RENAME_GENERIC_SOURCES: tuple[str, ...] = ("名字", "名称", "称呼", "叫法", "姓名", "人名", "名号")
_RENAME_MAX_OLD_CHARS = 80

_TITLE_TOKEN_SPLIT_RE = re.compile(r"[\s　·・:：，,。、《》「」“”\"'（）()\-—_/|]+")
_BOOK_TITLE_RE = re.compile(r"《([^》]{1,40})》")
_INLINE_WHITESPACE_RE = re.compile(r"[ \t\r\f\v　]+")


def _normalize_text(text: str) -> str:
    """去掉空白（换行当分句处理，换成逗号），英文转小写。"""
    return _INLINE_WHITESPACE_RE.sub("", (text or "").replace("\n", "，")).lower()


def _is_negated(text: str, start: int) -> bool:
    """同一分句里、紧挨着前面有没有「别 / 不要 / 不用」这类否定。"""
    window = text[max(0, start - _NEGATION_LOOKBACK):start]
    for index in range(len(window) - 1, -1, -1):
        if window[index] in _CLAUSE_DELIMITERS:
            window = window[index + 1:]
            break
    return bool(_NEGATION_RE.search(window))


def _positive_occurrence(text: str, needle: str, start: int = 0, end: int | None = None) -> bool:
    """needle 在 text[start:end] 里至少出现一次、且这一次前面没有否定。"""
    if not needle:
        return False
    stop = len(text) if end is None else end
    position = text.find(needle, start, stop)
    while position != -1:
        if not _is_negated(text, position):
            return True
        position = text.find(needle, position + 1, stop)
    return False


def _any_positive(text: str, needles: Iterable[str], start: int = 0, end: int | None = None) -> bool:
    return any(_positive_occurrence(text, needle, start, end) for needle in needles)


# 「改」开头却不是要改稿的词：「主角的命运从此改变」「改天再说」。
_GAI_NON_EDIT_NEXT = "变天日革口观行嫁"
# 「把结局改变一下」「改变成…」是要改。
_GAIBIAN_AS_EDIT_RE = re.compile(r"^变(?:一下|一点|得|成|为)")
# 「更新」在网文里常指发新章（「继续更新」「今天更新两章」），不是改已有的字。
_UPDATE_AS_PUBLISH_BEFORE_RE = re.compile(r"(?:继续|接着|日|今天|今日|明天|每天|多|快)$")
_UPDATE_AS_PUBLISH_AFTER_RE = re.compile(rf"^了?{_NUMBER}?[章集]")
# 作者在说自己做过的改动（「我把第3章的老周改成老秦了」「第3章我改过了」），不是在要求改。
_REPORT_TIME_RE = re.compile(r"(?:已经|已|刚才|刚刚|刚|早就|之前|上次)$")
_FIRST_PERSON_RE = re.compile(r"(?<![帮给替让请叫])我(?!们)")
_REQUEST_INTENT_RE = re.compile(r"(?:想|要|希望|打算|准备|需要|得|觉得|认为)")
# 名词用法：「第3章有改动」「做了修改」「沿用第3章的改动」。
_NOUN_USE_BEFORE_RE = re.compile(r"(?:的|有|做了|做过|作了|进行了|进行过)修?$")
# 讲已经改完的：「第3章改好了」「调整了一下人名」「第3章老周改成老秦了」。
_DONE_AFTER_RE = re.compile(r"^(?:了|好了|完了|完毕|好啦|完啦)")
_CLAUSE_FINAL_DONE_RE = re.compile(r"[了啦]$")
# 带了这些就是在叫 AI 改：「帮我把第3章改了」「第3章该改改了」「第3章删了吧」。
_IMPERATIVE_BEFORE_RE = re.compile(r"(?:帮|请|麻烦|劳驾|给我|能不能|能否|可不可以|再|该|得|需要|必须|要)")
# 「你怎么把第3章改了」是在抱怨，不是在要求改。
_COMPLAINT_RE = re.compile(r"你(?:怎么|为什么|为啥|又|居然|竟然|干嘛)")
# 不带方向的「统一」：「人名要统一」「统一一下格式」。「统一成 / 统一为 / 统一叫」是带方向的改名。
_UNIFY_DIRECTION_NEXT = "成为叫"


def _is_report(text: str, verb: str, position: int) -> bool:
    """text[position:] 上的这个改动说法，是不是作者在讲已经改过的（不是在要求改）。

    「第3章我改过了」「我把老周改成老秦了」「第3章改好了」「第3章老周改成老秦了」
    「第3章调整了一下人名」「第3章有改动」「做了修改」「第3章刚改完」。
    带「帮 / 请 / 再 / 该 / 吧」或「把第3章删了」这样的祈使说法时不算。
    """
    after_position = position + len(verb)
    next_char = text[after_position:after_position + 1]
    clause_start, clause_end = _clause_bounds(text, position)
    before = text[clause_start:position]
    after = text[after_position:clause_end]
    if after.startswith("过") or _REPORT_TIME_RE.search(before):
        return True
    if _NOUN_USE_BEFORE_RE.search(before) and (verb != "改" or next_char in "动写" or before.endswith("修")):
        return True
    if _FIRST_PERSON_RE.search(before) and not _REQUEST_INTENT_RE.search(before):
        return True
    # 「第3章删了」口语里多半是叫 AI 删；删掉的东西也不会再被改回去，删的说法不按讲改过的算。
    if verb in _DELETE_VERBS or not (_DONE_AFTER_RE.match(after) or _CLAUSE_FINAL_DONE_RE.search(after)):
        return False
    if _IMPERATIVE_BEFORE_RE.search(before) or "吧" in after:
        return False
    # 「把第3章删了」「把第3章改了」：动词紧跟「了」的把字句是在叫 AI 动手。
    return not (re.search(r"[把将]", before) and after.startswith("了"))


def _verb_is_request(text: str, verb: str, position: int, *, allow_bare_unify: bool) -> bool:
    """text[position:] 上的这个改动说法，是不是作者在要求改稿。"""
    after_position = position + len(verb)
    next_char = text[after_position:after_position + 1]
    if (
        verb == "改"
        and next_char
        and next_char in _GAI_NON_EDIT_NEXT
        and not _GAIBIAN_AS_EDIT_RE.match(text[after_position:])
    ):
        return False
    clause_start, clause_end = _clause_bounds(text, position)
    before = text[clause_start:position]
    after = text[after_position:clause_end]
    if verb == "更新" and (
        _UPDATE_AS_PUBLISH_BEFORE_RE.search(before) or _UPDATE_AS_PUBLISH_AFTER_RE.match(after)
    ):
        return False
    if verb == "统一" and not allow_bare_unify and (not next_char or next_char not in _UNIFY_DIRECTION_NEXT):
        return False
    if _COMPLAINT_RE.search(before):
        return False
    return not _is_report(text, verb, position)


def _has_edit_verb(
    text: str, start: int = 0, end: int | None = None, *, allow_bare_unify: bool = True
) -> bool:
    """text[start:end] 里有没有作者要求改稿的说法。

    不算的：前面有否定（「别改」）、「改变 / 改天」、「继续更新」这类发新章的说法、作者在讲
    自己改过的（「我把老周改成老秦了」「第3章我改过了」）、抱怨（「你怎么把第3章改了」）。
    allow_bare_unify=False 时不带方向的「统一」也不算：作者没点名哪一章、只说「人名要统一」
    「统一一下格式」时，不能拿来放开作者手改过的文件（AI 会把作者改的名字统一回去）。
    """
    stop = len(text) if end is None else end
    for verb in _EDIT_VERBS:
        position = text.find(verb, start, stop)
        while position != -1:
            if not _is_negated(text, position) and _verb_is_request(
                text, verb, position, allow_bare_unify=allow_bare_unify
            ):
                return True
            position = text.find(verb, position + 1, stop)
    return False


# 删除的说法：递归删除文件夹时，作者要明说删它，才连带删掉里面作者手改过的文件。
_DELETE_VERBS: tuple[str, ...] = ("删", "去掉", "移除", "清空", "清掉", "不要了", "扔掉")


def _has_delete_verb(text: str, start: int = 0, end: int | None = None) -> bool:
    stop = len(text) if end is None else end
    for verb in _DELETE_VERBS:
        position = text.find(verb, start, stop)
        while position != -1:
            if not _is_negated(text, position) and _verb_is_request(
                text, verb, position, allow_bare_unify=False
            ):
                return True
            position = text.find(verb, position + 1, stop)
    return False


def _clause_bounds(text: str, position: int) -> tuple[int, int]:
    """position 所在分句的 [start, end)。"""
    start = position
    while start > 0 and text[start - 1] not in _CLAUSE_DELIMITERS:
        start -= 1
    end = position
    while end < len(text) and text[end] not in _CLAUSE_DELIMITERS:
        end += 1
    return start, end


def _names_a_unit(clause: str) -> bool:
    return bool(_SINGLE_SEQ_RE.search(clause) or _FIRST_N_SEQ_RE.search(clause) or "《" in clause)


# 拿点名的文件当标准 / 来源：「以第3章为准」「按第3章统一人名」「第4章沿用第3章的改动」
# 「同步第3章的人名到第4章」。「可以 / 所以」里的「以」不算。
_QUOTE_OPEN = "「『《“\"'"
_REFERENCE_BEFORE_RE = re.compile(
    rf"(?:(?<![可所难得足予加])以|按|按照|照|照着|参照|参考|根据|依照|依据|对照|基于|沿用|延续|同步|仿照)[{_QUOTE_OPEN}]?$"
)
# 「和第3章的人名保持统一」「把第2章改得跟第3章一样」：拿来比的那一章。「第2章和第3章统一一下」
# 是并列（「和」前面紧挨着另一章），两章都要改。
_COMPARE_BEFORE_RE = re.compile(
    rf"(?<![{_SEQ_UNITS}{_CN_DIGITS}》\d])(?:和|跟|与|同|像)[{_QUOTE_OPEN}]?$"
)
_COMPARE_AFTER_RE = re.compile(r"(?:一致|一样|统一|相同|同步|对齐|看齐|保持|对应|对上)")
_STANDARD_AFTER_RE = re.compile(
    r"^[」』》”\"']?(?:的[^，,。！？!?；;\n]{0,4})?(?:为准|为标准|为主|为参考|为依据|为例|为模板)"
)
# 下一分句说的是别的章 / 拿前面那章当标准：「…，以它为准统一一下其他章」「…，把前面的章节也统一一下」。
_OTHER_UNITS_RE = re.compile(
    r"(?:其他|其余|其它|别的|另外|前面|后面|之后|之前|剩下|以前|后续)的?(?:章|集|回|节|篇|文件)"
)
_STANDARD_PRONOUN_RE = re.compile(r"(?:(?:以|按|按照|照|照着|参照|根据|跟|和|与)(?:它|这章|那章|这一章|那一章)|为准)")


def _mention_is_reference(text: str, mention_start: int, mention_end: int) -> bool:
    """作者提到这个文件的这一处，是拿它当标准 / 来源（不是要改它）。"""
    clause_start, clause_end = _clause_bounds(text, mention_start)
    before = text[clause_start:mention_start]
    after = text[mention_end:clause_end]
    if _REFERENCE_BEFORE_RE.search(before) or _STANDARD_AFTER_RE.match(after):
        return True
    return bool(_COMPARE_BEFORE_RE.search(before) and _COMPARE_AFTER_RE.search(after))


def _clause_reports_edit(text: str, start: int, end: int) -> bool:
    """text[start:end] 这一分句里，作者在讲已经改过的（「第3章是我改过的」「第3章改好了」）。"""
    for verb in _EDIT_VERBS:
        position = text.find(verb, start, end)
        while position != -1:
            if not _is_negated(text, position) and _is_report(text, verb, position):
                return True
            position = text.find(verb, position + 1, end)
    return False


def _mention_asks_for(
    text: str,
    mention_start: int,
    mention_end: int,
    has_verb: Callable[[str, int, int], bool],
) -> bool:
    """作者提到某个文件的那一处，是不是在要求改（has_verb=_has_edit_verb）/ 删它。

    同一分句里有改动的说法，或者紧跟着的下一分句有、且下一分句没有另外点名章节（这一分句
    已经提了别的改动时，下一分句是另一件事：「第3章改一下，把废稿删了」不是删第3章）：
    「第3章写得太拖了，精简一下」算；「参考第3章的写法写第4章」「继续写第四章，夜市那段
    的氛围延续下去」「第2章不用改，第3章改成老周」里的第2章不算。
    拿它当标准 / 来源的（「以第3章为准」「和第3章的人名保持统一」）不算；作者在这一分句讲
    它已经改过了（「第3章改好了」「第3章是我改过的」）时，下一分句的要求（「以它为准统一一下
    其他章」「继续写下一章」）也不算到它头上；下一分句说的是别的章（「其他章 / 前面的章节」）
    同样不算。
    """
    if _mention_is_reference(text, mention_start, mention_end):
        return False
    start, end = _clause_bounds(text, mention_start)
    if has_verb(text, start, end):
        return True
    next_start = end + 1
    # 这一分句已经是一条完整的要求（「第3章改一下，把废稿删了」）或者在讲改过了，
    # 下一分句是另一件事。
    if next_start >= len(text) or _clause_reports_edit(text, start, end) or _has_edit_verb(text, start, end):
        return False
    _, next_end = _clause_bounds(text, next_start)
    next_clause = text[next_start:next_end]
    if (
        next_start >= next_end
        or _names_a_unit(next_clause)
        or _OTHER_UNITS_RE.search(next_clause)
        or _STANDARD_PRONOUN_RE.search(next_clause)
    ):
        return False
    return has_verb(text, next_start, next_end)


def _mention_asks_for_edit(text: str, mention_start: int, mention_end: int) -> bool:
    return _mention_asks_for(text, mention_start, mention_end, _has_edit_verb)


def _mention_asks_for_delete(text: str, mention_start: int, mention_end: int) -> bool:
    return _mention_asks_for(text, mention_start, mention_end, _has_delete_verb)


def _parse_number(token: str) -> int | None:
    token = (token or "").strip()
    if not token:
        return None
    if token.isdigit():
        value = int(token)
        return value if value > 0 else None
    return parse_chinese_number(token.replace("两", "二").replace("〇", "零"))


def _unit_family(unit: str) -> str:
    """章 / 回 / 节 / 话 / 卷 都是小说的分段；集 / 幕 / 场 是剧本的。"""
    return "script" if unit in "集幕场" else "novel"


def _title_sequence(title: str) -> tuple[int, str] | None:
    match = _SINGLE_SEQ_RE.search(title or "")
    if not match:
        return None
    number = _parse_number(match.group(1))
    if number is None:
        return None
    return number, _unit_family(match.group(2))


def _sequence_mentions(normalized: str) -> list[tuple[set[tuple[int, str]], int, int]]:
    """作者原话（已规整）里点到的章 / 集号和出现的位置 [start, end)；前面带否定的不算。"""
    found: list[tuple[set[tuple[int, str]], int, int]] = []
    for match in _RANGE_SEQ_RE.finditer(normalized):
        if _is_negated(normalized, match.start()):
            continue
        start, end = _parse_number(match.group(1)), _parse_number(match.group(2))
        if start and end and start <= end and end - start <= 200:
            family = _unit_family(match.group(3))
            found.append(({(number, family) for number in range(start, end + 1)}, match.start(), match.end()))
    for match in _FIRST_N_SEQ_RE.finditer(normalized):
        if _is_negated(normalized, match.start()):
            continue
        count = _parse_number(match.group(1))
        if count and count <= 200:
            family = _unit_family(match.group(2))
            found.append(({(number, family) for number in range(1, count + 1)}, match.start(), match.end()))
    for match in _ENUM_SEQ_RE.finditer(normalized):
        if _is_negated(normalized, match.start()):
            continue
        family = _unit_family(match.group(2))
        tokens = re.split(r"[、,，和及与]", match.group(1))
        numbers = {(number, family) for number in map(_parse_number, tokens) if number}
        if numbers:
            found.append((numbers, match.start(), match.end()))
    for match in _SINGLE_SEQ_RE.finditer(normalized):
        if _is_negated(normalized, match.start()):
            continue
        number = _parse_number(match.group(1))
        if number:
            found.append(({(number, _unit_family(match.group(2)))}, match.start(), match.end()))
    return found


def mentioned_sequences(text: str) -> set[tuple[int, str]]:
    """作者原话里点到的章 / 集号（第3章、第三集、第1-3章、前三章、第2、3章）。

    前面带否定的不算（「别动第3章」）。
    """
    found: set[tuple[int, str]] = set()
    for numbers, _, _ in _sequence_mentions(_normalize_text(text)):
        found.update(numbers)
    return found


def _rename_pairs(text: str) -> list[tuple[str, str]]:
    """「把老秦改成老周」「把主角的名字改成李明」里的（被换掉的词, 换成的词）。"""
    normalized = _normalize_text(text)
    pairs: list[tuple[str, str]] = []
    for match in _RENAME_VERB_RE.finditer(normalized):
        if _is_negated(normalized, match.start()):
            continue
        before = normalized[max(0, match.start() - 16):match.start()]
        segments = [segment for segment in _RENAME_SOURCE_SPLIT_RE.split(before) if segment]
        source = _RENAME_SOURCE_TRAILING_RE.sub("", segments[-1]) if segments else ""
        after = normalized[match.end():match.end() + 16]
        targets = [segment for segment in _RENAME_TARGET_SPLIT_RE.split(after) if segment]
        target = _RENAME_TARGET_TRAILING_RE.sub("", targets[0]) if targets else ""
        pairs.append((source, target))
    return pairs


def rename_source_terms(text: str) -> set[str]:
    """「把老秦改成老周」「老秦都换成老周」里被换掉的词（至少 2 个字）。"""
    return {source for source, _ in _rename_pairs(text) if len(source) >= 2}


def _edits_are_requested_rename(raw_text: str, edits: list[dict[str, Any]]) -> bool:
    """每一处替换 / 删除都是作者要的改名。

    原文含被换掉的词（「把老秦改成老周」只替换含「老秦」的原文）；被换掉的是「名字」
    这类泛称时（「把主角的名字改成李明」），改成的文字里有新名字、原文里没有，且原文
    只是一小段。
    """
    pairs = _rename_pairs(raw_text)
    ops = [str(edit.get("op") or "").strip() for edit in edits]
    if not pairs or not all(op in {"replace", "delete"} for op in ops):
        return False
    sources = {source for source, _ in pairs if len(source) >= 2}
    generic_targets = {
        target
        for source, target in pairs
        if 2 <= len(target) <= 8 and any(word in source for word in _RENAME_GENERIC_SOURCES)
    }

    def allowed(edit: dict[str, Any]) -> bool:
        old = str(edit.get("old") or "")
        new = str(edit.get("new") or "")
        if any(source in old for source in sources):
            return True
        return (
            str(edit.get("op") or "").strip() == "replace"
            and len(old) <= _RENAME_MAX_OLD_CHARS
            and any(target in new and target not in old for target in generic_targets)
        )

    return all(allowed(edit) for edit in edits)


def author_confirms_change(
    text: str, *, sequence: tuple[int, str] | None = None, edits: list[Any] | None = None
) -> bool:
    """上一轮 AI 因为作者手改过而没改这个文件，作者这一轮是不是说清了把它往哪改。

    上一轮 AI 问的是「要把第2章也改成老秦吗？」（拒绝说明和提示词都让它默认这样问），
    所以「好 / 可以 / 行 / 没问题」答应的是把其他章改成作者的写法，不放开作者改过的这一章；
    「可以，第2章也改成老秦」点名的是别的章，也不算。作者点名这一章要求改（「第3章改回
    老周」）走点名的规则。
    这里只放行没点名、但说清了往哪改的回答（「统一成老周吧」「改回老周」「可以，统一成老周，
    然后写下一章」），而且每一处修改都是换成这个写法（或替换掉作者说要换掉的词）：作者说
    「统一成老秦」（作者自己的写法）时，把这一章的老秦改成老周仍然不行。
    出现不改的说法（不用 / 别 / 先不 / 保持 / 就叫 / 我自己…）不算；删除、整份重写
    （没有逐处修改）不算。sequence：这个文件的章 / 集号。
    """
    normalized = _normalize_text(text)
    if not normalized or _DECLINE_RE.search(normalized):
        return False
    if any(sequence is None or sequence not in numbers for numbers, _, _ in _sequence_mentions(normalized)):
        return False
    if not edits or not all(isinstance(edit, dict) for edit in edits):
        return False
    pairs = [(source, target) for source, target in _rename_pairs(text) if target]
    if not pairs:
        return False
    sources = {source for source, _ in pairs if len(source) >= 2}
    targets = {target for _, target in pairs if len(target) <= 8}

    def toward_target(edit: dict[str, Any]) -> bool:
        op = str(edit.get("op") or "").strip()
        old = str(edit.get("old") or "")
        new = str(edit.get("new") or "")
        if op not in {"replace", "delete"}:
            return False
        if any(source in old for source in sources):
            return True
        return (
            op == "replace"
            and len(old) <= _RENAME_MAX_OLD_CHARS
            and any(target in new and target not in old for target in targets)
        )

    return all(toward_target(edit) for edit in edits)


def _title_tokens(title: str) -> list[str]:
    """标题里能单独拿来指代文件的词：去掉「第N章」，剩下至少 2 个字的部分。"""
    stripped = _SINGLE_SEQ_RE.sub(" ", title or "")
    return [token for token in _TITLE_TOKEN_SPLIT_RE.split(stripped) if len(token) >= 2]


def _title_mentions(text: str, title: str) -> list[tuple[int, int]]:
    """作者原话（已规整）里提到这个标题（完整标题或标题里的词）的位置 [start, end)；带否定的不算。"""
    needles = [_normalize_text(title)] + [_normalize_text(token) for token in _title_tokens(title)]
    found: list[tuple[int, int]] = []
    for needle in needles:
        if not needle:
            continue
        position = text.find(needle)
        while position != -1:
            if not _is_negated(text, position):
                found.append((position, position + len(needle)))
            position = text.find(needle, position + 1)
    return found


def _mentions_title_for_edit(text: str, title: str) -> bool:
    """作者原话里提到这个标题（完整标题或标题里的词）并要求改它。"""
    return any(_mention_asks_for_edit(text, start, end) for start, end in _title_mentions(text, title))


def _names_another_file(
    text: str, *, title: str, file_type: str, sequence: tuple[int, str] | None
) -> bool:
    """作者原话里点名了别的文件（别的章 / 集号、别的《标题》、别的文件类别）。"""
    for numbers, _, _ in _sequence_mentions(text):
        if sequence is None or sequence not in numbers:
            return True
    normalized_title = _normalize_text(title)
    for match in _BOOK_TITLE_RE.finditer(text):
        if match.group(1) not in normalized_title:
            return True
    for other_type, markers in _FILE_TYPE_MARKERS.items():
        if other_type != file_type and _any_positive(text, markers):
            return True
    return False


@dataclass(frozen=True)
class AuthorScope:
    """作者这一轮说了什么、指向了哪些文件。"""

    messages: tuple[str, ...] = ()
    referenced_file_ids: frozenset[str] = frozenset()
    confirmed_file_ids: frozenset[str] = frozenset()
    focus_file_id: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(message for message in self.messages if message)


def request_targets_file(
    scope: AuthorScope,
    *,
    file_id: str,
    title: str | None,
    file_type: str | None = None,
    edits: list[Any] | None = None,
) -> bool:
    """作者这一轮的话是否指向了这个文件（或这次修改本身就是作者要的改名 / 续写）。"""
    if file_id in scope.referenced_file_ids:
        return True

    raw_text = scope.text
    text = _normalize_text(raw_text)
    if not text:
        return False

    title_text = title or ""
    file_type_text = str(file_type or "")
    sequence = _title_sequence(title_text)

    # 上一轮因为作者手改过没改它：作者说清了往哪改（「统一成老周吧」）才放行，「好 / 可以」不算。
    if file_id in scope.confirmed_file_ids and author_confirms_change(
        raw_text, sequence=sequence, edits=edits
    ):
        return True

    # 全书 / 所有章节，并且要求改动。
    for marker in _GLOBAL_SCOPE_MARKERS:
        position = text.find(marker)
        while position != -1:
            if not _is_negated(text, position) and _mention_asks_for_edit(
                text, position, position + len(marker)
            ):
                return True
            position = text.find(marker, position + 1)

    # 作者正开着这个文件：说了「这一章 / 这里」，或者提了改动（「把开头改得更有悬念一点」），
    # 并且没点名别的文件、也不是在要下一章 / 接着往下写。没点名时不带方向的「统一」
    # （「人名要统一」「统一一下格式」）不算：作者刚改的名字正是要保住的。
    is_focus = bool(scope.focus_file_id) and file_id == scope.focus_file_id
    wants_next_unit = any(marker in text for marker in _NEXT_UNIT_MARKERS)
    moving_on = wants_next_unit or any(marker in text for marker in _MOVING_ON_MARKERS)
    if is_focus and not _names_another_file(
        text, title=title_text, file_type=file_type_text, sequence=sequence
    ):
        has_deictic = _any_positive(text, _FOCUS_DEICTIC_MARKERS)
        if has_deictic and (_has_edit_verb(text) or not wants_next_unit):
            return True
        if not moving_on and _has_edit_verb(text, allow_bare_unify=False):
            return True

    # 点名这个文件（标题、第 N 章 / 集）并要求改它。
    if title_text and _mentions_title_for_edit(text, title_text):
        return True
    if sequence is not None:
        for numbers, start, end in _sequence_mentions(text):
            if sequence in numbers and _mention_asks_for_edit(text, start, end):
                return True

    markers = _FILE_TYPE_MARKERS.get(file_type_text)
    if markers and _any_positive(text, markers) and _has_edit_verb(text):
        return True

    if edits:
        if not all(isinstance(edit, dict) for edit in edits):
            return False
        ops = [str(edit.get("op") or "").strip() for edit in edits]
        # 作者正开着这个文件、本轮不是写下一章：只往末尾追加，不动作者写过的字。
        if is_focus and all(op == "append" for op in ops) and not wants_next_unit:
            return True
        # 作者要的改名：只替换被换掉的那个词。
        if _edits_are_requested_rename(raw_text, edits):
            return True

    return False


# 「正文里的第2章删掉」「把正文里重复的段落删掉」：删的是文件夹里的东西，不是文件夹。
_INSIDE_AFTER_RE = re.compile(r"^[」』》”\"']?(?:文件夹|目录)?(?:里|中|内|下|底下)")


def request_deletes_folder(scope: AuthorScope, *, file_id: str, title: str | None) -> bool:
    """递归删除文件夹：作者这一轮是不是明说要删这个文件夹（「把『废稿』文件夹删了」）。

    要点名文件夹（标题或标题里的词）且同一分句或下一分句里说删；「正文改紧凑一点」
    这类只提到文件夹名的改动要求不算，「正文里的第2章删掉」「正文里多余的空行删掉」删的是
    里面的东西也不算，不能借它连带删掉里面作者手改过的章节。附加 / 引用了这个文件夹时，
    也要说删。
    """
    text = _normalize_text(scope.text)
    if not text or not _has_delete_verb(text):
        return False
    if file_id in scope.referenced_file_ids:
        return True
    for start, end in _title_mentions(text, title or ""):
        if _INSIDE_AFTER_RE.match(text[end:]):
            continue
        clause_start, clause_end = _clause_bounds(text, start)
        if _names_a_unit(text[clause_start:start] + text[end:clause_end]):
            continue
        if _mention_asks_for_delete(text, start, end):
            return True
    return False


def request_deletes_file(scope: AuthorScope, *, file_id: str, title: str | None) -> bool:
    """作者手改过的文件能不能删（delete_file 删它，或递归删除的文件夹里有它）。

    作者附加 / 引用了它并说了删；或者点名它（标题、第 N 章 / 集；正开着它时说「这一章」），
    并且就在点名的那一分句或下一分句说删（「第3章删掉」「把第3章和废稿都删了」）。
    「第3章改一下，把废稿删了」里的删说的是废稿，不算；只说「第3章改一下」不算。
    """
    text = _normalize_text(scope.text)
    if not text or not _has_delete_verb(text):
        return False
    if file_id in scope.referenced_file_ids:
        return True
    if scope.focus_file_id and file_id == scope.focus_file_id:
        for marker in _FOCUS_DEICTIC_MARKERS:
            position = text.find(marker)
            while position != -1:
                if not _is_negated(text, position) and _mention_asks_for_delete(
                    text, position, position + len(marker)
                ):
                    return True
                position = text.find(marker, position + 1)
    if title and any(_mention_asks_for_delete(text, start, end) for start, end in _title_mentions(text, title)):
        return True
    sequence = _title_sequence(title or "")
    if sequence is not None:
        for numbers, start, end in _sequence_mentions(text):
            if sequence in numbers and _mention_asks_for_delete(text, start, end):
                return True
    return False


def latest_text_is_authors(session: Any, file: Any) -> bool:
    """文件当前正文最后是不是作者写下的。

    - 正文为空：没什么可保护的，False。
    - 没有任何版本：来历不明（旧数据 / 测试数据），不拦，False。
    - 正文和最新版本不一致：最新版本之后有人改过却没留版本（作者的版本额度用满时
      就是这样保存的），True。
    - 最新版本是 AI 写的（change_source=ai，或作者审阅后接受的 AI 修改
      change_type=ai_edit）：False；其余（作者编辑、恢复、导入、系统备份）：True。
    """
    content = getattr(file, "content", None) or ""
    if not content.strip():
        return False

    from services.features.file_version_service import FileVersionService

    service = FileVersionService()
    latest = service.get_latest_version(session, file.id)
    if latest is None:
        return False
    latest_content = service._get_contents_for_versions(session, {file.id: latest})[file.id]
    if latest_content != content:
        return True
    return not (
        latest.change_source == _AI_CHANGE_SOURCE or latest.change_type == _AI_EDIT_CHANGE_TYPE
    )


def protected_refusal_message(title: str | None, *, action: str = "edit") -> str:
    """给模型看的拒绝说明：不要重试，以作者写法为准，有出入就问作者。

    action：edit（edit_file / delete_file）、rewrite（create_file 复用了同名的已有剧集，
    接下来的 <file> 正文会整份覆盖它）、folder（递归删除的文件夹里有这类文件）。
    """
    label = f"《{title}》" if title else "这个文件"
    if action == "rewrite":
        lead = (
            f"已经有一个{label}，正文是作者自己改过的，作者本轮没有要求重写它，这次没有复用它，"
            "不要用 <file> 写它的正文。要写新的一集请换成还没有的集数和标题。"
        )
    elif action == "folder":
        lead = f"这个文件夹里的{label}是作者自己改过的，作者本轮没有要求删它，这次删除没有执行。"
    else:
        lead = f"{label}现在的正文是作者自己改过的，作者本轮没有要求改它，这次修改没有执行。"
    return (
        f"{lead}"
        "作者手改的写法（人名、称呼、用词、情节）就是最新设定，以作者为准："
        "不要再尝试修改或删除这个文件，也不要把它改回以前的写法；新写的内容沿用作者的写法。"
        "如果你觉得它和其他章节有出入，只在给作者的回复里用一句话指出位置，问作者往哪边改，"
        "默认建议按作者改的写法改其他章节（例如「第3章是你手动改过的，我没动它：那里摊主叫老秦，"
        "第2章写的是老周，要把第2章也改成老秦吗？」），"
        "不要说「改不动」「被拦下」；作者同意后下一轮再改其他章节。"
        "给作者的选项里不要提议把作者改的写法改回去；只有作者自己说清把这个文件改成哪种写法"
        "（如「第3章改回老周」「统一成老周」）时才改它。"
    )


def protected_user_message(title: str | None) -> str:
    """给作者看的一句话（工具卡片）。"""
    label = f"《{title}》" if title else "这个文件"
    return f"{label}有你手动改过的内容，AI 没有动它；需要改的话会先问你。"


@dataclass
class AuthorEditRefusals:
    """本次请求里因为作者手改而没改成的文件（跨工作线程共享），落库后下一轮放行。"""

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _file_ids: list[str] = field(default_factory=list)

    def add(self, file_id: str) -> None:
        if not file_id:
            return
        with self._lock:
            if file_id not in self._file_ids:
                self._file_ids.append(file_id)

    def file_ids(self) -> list[str]:
        with self._lock:
            return list(self._file_ids)


# 落库在 assistant 消息 routing 里的字段名：上一轮问过作者的文件。
CONFIRM_FILE_IDS_ROUTING_KEY = "author_confirm_file_ids"
MAX_CONFIRM_FILE_IDS = 20


def confirmed_file_ids_from_history(history_messages: Iterable[Any]) -> list[str]:
    """上一条 assistant 消息里记下的「问过作者要不要改」的文件。"""
    for message in reversed(list(history_messages or [])):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        routing = message.get("routing")
        if not isinstance(routing, dict):
            return []
        raw = routing.get(CONFIRM_FILE_IDS_ROUTING_KEY)
        if not isinstance(raw, list):
            return []
        return [str(item) for item in raw if isinstance(item, str) and item][:MAX_CONFIRM_FILE_IDS]
    return []


def referenced_file_ids_from_metadata(metadata: dict[str, Any] | None) -> list[str]:
    """作者本轮附加的文件、引用的选中文本所在的文件。"""
    if not isinstance(metadata, dict):
        return []
    ids: list[str] = []
    attached = metadata.get("attached_file_ids")
    if isinstance(attached, list):
        ids.extend(str(item) for item in attached if isinstance(item, str) and item)
    quotes = metadata.get("text_quotes")
    if isinstance(quotes, list):
        for quote in quotes:
            if isinstance(quote, dict):
                quote_file_id = quote.get("fileId") or quote.get("file_id")
                if isinstance(quote_file_id, str) and quote_file_id:
                    ids.append(quote_file_id)
    return ids
