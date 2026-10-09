"""作者手动改过的文件：作者本轮没点名时，AI 不改、不删，改为问作者。

2026-10-09 审计 P1-3：作者把第 3 章的「老周」手动改成「老秦」（19 处），接着发「继续写
下一章」。AI 写完第 4 章后把第 3 章 19 处全改了回去，还在总结里说「顺手修的两处」。

规则（工具层兜底，提示词里有同一条要求）：
- 文件的当前正文最后是作者自己写下的（最新版本不是 AI 写的，或正文在 AI 版本之后又被
  改过却没留版本），而作者本轮的话没有指向这个文件时，edit_file / delete_file 不执行，
  返回 error_type=author_edit_protected，告诉模型以作者的写法为准、有出入就问作者。
- 「指向这个文件」：作者原话里提到它（标题、第 N 章 / 集、前 N 章、大纲 / 人设这类
  文件类别、「全书 / 所有章节 / 统一」这类全局说法、「这一章」且它正开着）、附加或引用了
  它，或者上一轮 AI 因为这条规则问过作者要不要改它（作者这一轮就是在回答）。
- 「把老秦改成老周」这类改名要求：只替换 / 删除含「老秦」的原文时放行。
- 作者正开着的文件只追加（op=append）、且本轮不是写下一章时放行：作者写了半章说
  「继续写」，AI 接着往后写不改动作者已写的字。

只在应用内对话（ToolContext 带着作者原话）时生效；没有作者原话的调用路径不受影响。
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable
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

# 指向「全书」的说法：作者明确要求跨文件改动。前面带否定（「别改…」）时不算。
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
    "统一",
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

# 本轮要的是下一章 / 新章：作者正开着的文件也不往里追加。
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
_EDIT_VERBS: tuple[str, ...] = (
    "改",
    "调整",
    "更新",
    "补充",
    "补上",
    "完善",
    "重写",
    "删",
    "加上",
    "添加",
    "同步",
)

# 否定：「别改第3章」「不要动已经写好的章节」里的指向不算。只往前看同一分句里的几个字。
_NEGATION_RE = re.compile(r"(?:别|不要|不用|不必|勿|没让|不许|禁止|不能|无需|不需要|先不)")
_NEGATION_LOOKBACK = 8
_CLAUSE_DELIMITERS = "，,。！？!?；;\n"

# 「把老秦改成老周」：改名 / 替换的来源词。
_RENAME_VERB_RE = re.compile(r"(?:改成|改为|改回|换成|替换成|替换为|改叫|改名为|改名成|改名叫)")
_RENAME_SOURCE_SPLIT_RE = re.compile(r"[的把将，,。；;、\s「」“”\"'『』（）()]+")
_RENAME_SOURCE_TRAILING_RE = re.compile(r"(?:全部|全都|都|统一|一律|也|再)+$")

_TITLE_TOKEN_SPLIT_RE = re.compile(r"[\s　·・:：，,。、《》「」“”\"'（）()\-—_/|]+")
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


def _positive_occurrence(text: str, needle: str) -> bool:
    """needle 在 text 里至少出现一次、且这一次前面没有否定。"""
    if not needle:
        return False
    start = text.find(needle)
    while start != -1:
        if not _is_negated(text, start):
            return True
        start = text.find(needle, start + 1)
    return False


def _any_positive(text: str, needles: Iterable[str]) -> bool:
    return any(_positive_occurrence(text, needle) for needle in needles)


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


def mentioned_sequences(text: str) -> set[tuple[int, str]]:
    """作者原话里点到的章 / 集号（第3章、第三集、第1-3章、前三章、第2、3章）。

    前面带否定的不算（「别动第3章」）。
    """
    normalized = _normalize_text(text)
    found: set[tuple[int, str]] = set()
    for match in _RANGE_SEQ_RE.finditer(normalized):
        if _is_negated(normalized, match.start()):
            continue
        start, end = _parse_number(match.group(1)), _parse_number(match.group(2))
        if start and end and start <= end and end - start <= 200:
            family = _unit_family(match.group(3))
            found.update((number, family) for number in range(start, end + 1))
    for match in _FIRST_N_SEQ_RE.finditer(normalized):
        if _is_negated(normalized, match.start()):
            continue
        count = _parse_number(match.group(1))
        if count and count <= 200:
            family = _unit_family(match.group(2))
            found.update((number, family) for number in range(1, count + 1))
    for match in _ENUM_SEQ_RE.finditer(normalized):
        if _is_negated(normalized, match.start()):
            continue
        family = _unit_family(match.group(2))
        for token in re.split(r"[、,，和及与]", match.group(1)):
            number = _parse_number(token)
            if number:
                found.add((number, family))
    for match in _SINGLE_SEQ_RE.finditer(normalized):
        if _is_negated(normalized, match.start()):
            continue
        number = _parse_number(match.group(1))
        if number:
            found.add((number, _unit_family(match.group(2))))
    return found


def rename_source_terms(text: str) -> set[str]:
    """「把老秦改成老周」「老秦都换成老周」里被换掉的词（至少 2 个字）。"""
    normalized = _normalize_text(text)
    terms: set[str] = set()
    for match in _RENAME_VERB_RE.finditer(normalized):
        if _is_negated(normalized, match.start()):
            continue
        before = normalized[max(0, match.start() - 16):match.start()]
        segments = [segment for segment in _RENAME_SOURCE_SPLIT_RE.split(before) if segment]
        if not segments:
            continue
        term = _RENAME_SOURCE_TRAILING_RE.sub("", segments[-1])
        if len(term) >= 2:
            terms.add(term)
    return terms


def _title_tokens(title: str) -> list[str]:
    """标题里能单独拿来指代文件的词：去掉「第N章」，剩下至少 2 个字的部分。"""
    stripped = _SINGLE_SEQ_RE.sub(" ", title or "")
    return [token for token in _TITLE_TOKEN_SPLIT_RE.split(stripped) if len(token) >= 2]


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
    if file_id in scope.referenced_file_ids or file_id in scope.confirmed_file_ids:
        return True

    raw_text = scope.text
    text = _normalize_text(raw_text)
    if not text:
        return False

    if _any_positive(text, _GLOBAL_SCOPE_MARKERS):
        return True

    is_focus = bool(scope.focus_file_id) and file_id == scope.focus_file_id
    if is_focus and _any_positive(text, _FOCUS_DEICTIC_MARKERS):
        return True

    normalized_title = _normalize_text(title or "")
    if normalized_title and _positive_occurrence(text, normalized_title):
        return True
    if _any_positive(text, [_normalize_text(token) for token in _title_tokens(title or "")]):
        return True

    sequence = _title_sequence(title or "")
    if sequence is not None and sequence in mentioned_sequences(raw_text):
        return True

    markers = _FILE_TYPE_MARKERS.get(str(file_type or ""))
    if markers and _any_positive(text, markers) and _any_positive(text, _EDIT_VERBS):
        return True

    if edits:
        ops = [str(edit.get("op") or "").strip() for edit in edits if isinstance(edit, dict)]
        if len(ops) != len(edits):
            return False
        # 作者正开着这个文件、本轮不是写下一章：只往末尾追加，不动作者写过的字。
        if (
            is_focus
            and all(op == "append" for op in ops)
            and not any(marker in text for marker in _NEXT_UNIT_MARKERS)
        ):
            return True
        # 「把老秦改成老周」：每一处替换 / 删除的原文里都有被换掉的那个词。
        terms = rename_source_terms(raw_text)
        if (
            terms
            and all(op in {"replace", "delete"} for op in ops)
            and all(any(term in str(edit.get("old") or "") for term in terms) for edit in edits)
        ):
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


def protected_refusal_message(title: str | None) -> str:
    """给模型看的拒绝说明：不要重试，以作者写法为准，有出入就问作者。"""
    label = f"《{title}》" if title else "这个文件"
    return (
        f"{label}现在的正文是作者自己改过的，作者本轮没有要求改它，这次修改没有执行。"
        "作者手改的写法（人名、称呼、用词、情节）就是最新设定，以作者为准："
        "不要再尝试修改或删除这个文件，也不要把它改回以前的写法；新写的内容沿用作者的写法。"
        "如果你觉得它和其他章节有出入，只在给作者的回复里用一句话指出位置，问作者要不要改"
        "（例如「第3章是你手动改过的，我没动它：那里摊主叫老秦，第2章写的是老周，要统一吗？」），"
        "不要说「改不动」「被拦下」；作者同意后下一轮再改。"
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
