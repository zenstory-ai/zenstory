"""把 agent 之间的交接说明改写成作者能看懂的话。

交接原因（handoff reason）、审稿人留下的修改意见是模型写给下一个 agent 的，里面常有
文件 id、`word_count=812`、`[query_files]` 这类内部写法，以及「阻断」「返工」「交接」
这些流程用语。前端把交接原因原样拼进「接下来由{{agent}}继续：{{reason}}」，审查轮数
用完时审稿意见也会直接显示给作者。

这里只处理发给前端、存进展示记录的那一份；交给下一个 agent 的交接信息和交接包保持原样。
"""

from __future__ import annotations

import re

# 图自己生成的交接原因：直接换成作者口径。空字符串表示不写原因，前端只显示
# 「接下来由{{agent}}继续」。
SYSTEM_HANDOFF_REASONS: dict[str, str] = {
    "工作流自动交接": "",
    "自动质量门控": "正文写好了，检查一遍质量",
}

# 和前端 chat:workflow.agents.* 的中文名一致。
_AGENT_NAMES: dict[str, str] = {
    "quality_reviewer": "质量审稿人",
    "hook_designer": "爽点设计师",
    "planner": "大纲规划师",
    "writer": "内容创作者",
}

_ASCII_WORD_EDGE_BEFORE = r"(?<![A-Za-z0-9_])"
_ASCII_WORD_EDGE_AFTER = r"(?![A-Za-z0-9_])"

_UUID = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"

# [query_files]、【edit_file】、`content_length` 这类整段包起来的内部名字：连括号一起去掉。
_WRAPPED_IDENTIFIER_RE = re.compile(
    r"(?:调用|使用|通过|用)?\s*[\[【`]\s*[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*(?:\s*[=:：]\s*[^\]】`]*)?\s*[\]】`]"
)
# 括号里只剩 id / 字段的说明：(id=…)、（file_id: …）、(word_count=812, content_length=900)
_PAREN_FIELDS_RE = re.compile(
    r"[(（]\s*(?:[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*\s*[=:：]\s*[^()（）]*?|" + _UUID + r")\s*[)）]"
)
# key=value / key: value 形式的字段（id=…、file_id=…、word_count=812、content_length: 900）。
# 冒号写法只认蛇形命名和 id，免得把英文句子里的「Note: …」当成字段。
_FIELD_VALUE = r"\s*[^\s，。；、,;)）\]】]*"
_FIELD_ASSIGNMENT_RE = re.compile(
    _ASCII_WORD_EDGE_BEFORE
    + r"(?:[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)*\s*="
    + r"|(?:[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+|[Ii][Dd][Ss]?)\s*[:：](?=\s*[0-9A-Za-z]))"
    + _FIELD_VALUE
)
_UUID_RE = re.compile(_UUID)
# 剩下的蛇形命名（query_files、content_length、handoff_to_agent）：都是工具名或字段名。
# 前面的「用 / 调用 / 通过」一起去掉，免得剩下「用追加」。
_SNAKE_IDENTIFIER_RE = re.compile(
    r"(?:调用|使用|通过|用)?\s*"
    + _ASCII_WORD_EDGE_BEFORE
    + r"[a-z][a-z0-9]*(?:_[a-z0-9]+)+"
    + _ASCII_WORD_EDGE_AFTER
)
_AGENT_NAME_RE = re.compile(
    _ASCII_WORD_EDGE_BEFORE
    + r"(" + "|".join(re.escape(name) for name in _AGENT_NAMES) + r")"
    + _ASCII_WORD_EDGE_AFTER,
    re.IGNORECASE,
)

# 流程用语 → 作者口径。按顺序替换，长的在前。
_WORDING_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("阻断级问题", "需要先修正的问题"),
    ("阻断性问题", "需要先修正的问题"),
    ("阻断问题", "需要先修正的问题"),
    ("阻断项", "需要先修正的地方"),
    ("需要返工", "需要再改一轮"),
    ("需返工", "需要再改一轮"),
    ("返工", "再改一轮"),
    ("返修", "再改一轮"),
    ("阻断", "需要先修正"),
    ("交接给", "交给"),
    ("交接", "转交"),
    ("送审", "送去检查"),
)

# 审稿规则的内部说法（「超过15字阈值」「按阻断处理」）：作者不需要知道规则怎么判的。
# 括号里只说规则的整段去掉；不在括号里的按分句去掉。必须在流程用语替换（阻断 → 需要先修正）之前。
_RULE_JARGON_PAREN_RE = re.compile(
    r"[(（][^()（）]*(?:阈值|按[^()（）]{0,8}处理)[^()（）]*[)）]"
)
_RULE_JARGON_RE = re.compile(
    r"(?:超过|超出|高于|低于|达到)?\s*\d+\s*字(?:的)?阈值|"
    r"按(?:阻断级?问题|阻断项|阻断|客观缺陷|硬性问题)处理"
)

# 删掉内部写法时先留一个占位符，用来判断哪个分句被删空了。
_REMOVED = "\x00"
_CJK = r"㐀-鿿豈-﫿"
_CLAUSE_SPLIT_RE = re.compile(r"([，,；;。！？!?\n])")
# 分句里删掉内部写法以后，剩下的汉字、字母和数字少于这个数（如只剩「显示」），整个分句不要。
_MIN_CLAUSE_CHARS = 3
_MEANINGFUL_CHAR_RE = re.compile(rf"[{_CJK}A-Za-z0-9]")

_EMPTY_BRACKETS_RE = re.compile(r"[(（\[【]\s*[,，、;；:：]*\s*[)）\]】]")
_REPEATED_PUNCT_RE = re.compile(r"([,，、;；:：。])(?:\s*[,，、;；:：])+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,，、;；:：。！？!?)）\]】])")
_SPACE_AFTER_PUNCT_RE = re.compile(r"([，、；：。！？（【《])\s+")
_SPACE_BETWEEN_CJK_RE = re.compile(rf"(?<=[{_CJK}》」”])[ \t]+(?=[{_CJK}《「“])")
_HORIZONTAL_SPACE_RE = re.compile(r"[ \t]{2,}")
_EDGE_PUNCT = " \t,，、;；:：-—"


def _drop_emptied_clauses(line: str) -> str:
    if _REMOVED not in line:
        return line
    parts = _CLAUSE_SPLIT_RE.split(line)
    kept: list[str] = []
    for index in range(0, len(parts), 2):
        clause = parts[index]
        delimiter = parts[index + 1] if index + 1 < len(parts) else ""
        if _REMOVED in clause:
            clause = clause.replace(_REMOVED, "")
            if len(_MEANINGFUL_CHAR_RE.findall(clause)) < _MIN_CLAUSE_CHARS:
                # 分句被删空：去掉它，标点留给前一个分句收尾（例如句号）。
                if kept and delimiter and delimiter in "。！？!?":
                    previous = kept[-1].rstrip("，,；;：:")
                    if not previous.endswith(("。", "！", "？", "!", "?")):
                        previous += delimiter
                    kept[-1] = previous
                continue
        kept.append(clause + delimiter)
    return "".join(kept)


def _clean_line(line: str) -> str:
    line = _drop_emptied_clauses(line)
    is_list_item = line.lstrip().startswith("- ")
    previous = None
    while previous != line:
        previous = line
        line = _EMPTY_BRACKETS_RE.sub("", line)
        line = _REPEATED_PUNCT_RE.sub(r"\1", line)
        line = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", line)
        line = _SPACE_AFTER_PUNCT_RE.sub(r"\1", line)
        line = _SPACE_BETWEEN_CJK_RE.sub("", line)
        line = _HORIZONTAL_SPACE_RE.sub(" ", line)
    stripped = line.strip(_EDGE_PUNCT)
    # 去掉开头的标点后，列表项的「- 」也要保留。
    if is_list_item and stripped:
        return f"- {stripped.lstrip('- ').strip()}"
    return stripped


def author_facing_text(text: object) -> str:
    """去掉内部 id、字段名、工具名，并把流程用语换成作者能看懂的说法。"""
    if not isinstance(text, str):
        return ""
    result = text.replace(_REMOVED, "")
    for pattern in (
        _RULE_JARGON_PAREN_RE,
        _RULE_JARGON_RE,
        _WRAPPED_IDENTIFIER_RE,
        _PAREN_FIELDS_RE,
        _FIELD_ASSIGNMENT_RE,
        _UUID_RE,
    ):
        result = pattern.sub(_REMOVED, result)
    result = _AGENT_NAME_RE.sub(lambda m: _AGENT_NAMES[m.group(1).lower()], result)
    result = _SNAKE_IDENTIFIER_RE.sub(_REMOVED, result)
    for old, new in _WORDING_REPLACEMENTS:
        result = result.replace(old, new)
    lines = [_clean_line(line).replace(_REMOVED, "") for line in result.splitlines()]
    return "\n".join(line for line in lines if line).strip()


def author_facing_handoff_reason(reason: object) -> str:
    """交接原因的作者版本。图自己生成的原因按表替换，模型写的原因走 author_facing_text。"""
    if not isinstance(reason, str):
        return ""
    stripped = reason.strip()
    if stripped in SYSTEM_HANDOFF_REASONS:
        return SYSTEM_HANDOFF_REASONS[stripped]
    return author_facing_text(stripped)


# ------------------------------------------------------------------ 流式正文

# 作者写中文时，调用工具前那句以英文为主的过程说明（「Now create the 分集大纲 file.」）
# 不发给作者。只看「紧接着就调用工具」的那一小段：最后的总结、写进文件的正文都不过滤。
_CJK_CHAR_RE = re.compile(rf"[{_CJK}]")
_ASCII_LETTER_RE = re.compile(r"[A-Za-z]")
# 这么长还没定下来就照常发出去（不再等），过程说明通常就一句话。
NARRATION_BUFFER_MAX_CHARS = 120
# 汉字已经占多数、且有这么多个时，这段肯定不是英文过程句，立刻发出去。
_CJK_FLUSH_MIN_CHARS = 4


def is_english_process_narration(text: str) -> bool:
    """一小段以英文为主的说明：以英文开头，英文字母至少是汉字的两倍。"""
    stripped = (text or "").strip()
    if not stripped or len(stripped) > NARRATION_BUFFER_MAX_CHARS:
        return False
    if not _ASCII_LETTER_RE.match(stripped):
        return False
    letters = len(_ASCII_LETTER_RE.findall(stripped))
    cjk = len(_CJK_CHAR_RE.findall(stripped))
    return letters >= 4 and letters >= 2 * cjk


def text_contains_cjk(text: object) -> bool:
    return isinstance(text, str) and bool(_CJK_CHAR_RE.search(text))


class AgentTextShaper:
    """一个 agent run 的 TEXT 流在发给作者之前的整理。

    - 和前一个角色的文字隔开：之前的文字没有以空行结束时，本 agent 的第一段文字前补空行
      （审计 P3-16：规划师的「…外形标签定死」和内容创作者的「我先确认三个剧本文件的当前
      内容。」粘成了一句）。
    - drop_english_narration（作者写中文时开启）：每段文字先攒着，看出是中文就立刻发；
      以英文为主、并且紧接着就调用工具的一小段（过程说明）丢掉；超过
      NARRATION_BUFFER_MAX_CHARS、或出现 ``<``（``<file>`` 正文）时照常发出。
    """

    def __init__(self, *, previous_text: str, drop_english_narration: bool) -> None:
        self._previous_text = previous_text or ""
        self._drop_english_narration = drop_english_narration
        self._started = False
        self._buffer = ""
        self._passthrough = not drop_english_narration

    def _emit(self, text: str) -> str:
        if not text:
            return ""
        if not self._started:
            self._started = True
            previous = self._previous_text
            if previous.strip() and not previous.endswith("\n\n") and not text.startswith("\n\n"):
                separator = "\n" if previous.endswith("\n") or text.startswith("\n") else "\n\n"
                text = separator + text
        return text

    def feed(self, text: str) -> str:
        """收到一段 TEXT，返回现在该发出去的文字（可能为空）。"""
        if not text:
            return ""
        if self._passthrough:
            return self._emit(text)
        self._buffer += text
        stripped = self._buffer.lstrip()
        if not stripped:
            return ""
        cjk = len(_CJK_CHAR_RE.findall(self._buffer))
        letters = len(_ASCII_LETTER_RE.findall(self._buffer))
        if (
            not _ASCII_LETTER_RE.match(stripped)
            or (cjk >= _CJK_FLUSH_MIN_CHARS and cjk * 2 > letters)
            or "<" in self._buffer
            or len(self._buffer) > NARRATION_BUFFER_MAX_CHARS
        ):
            self._passthrough = True
            buffered, self._buffer = self._buffer, ""
            return self._emit(buffered)
        return ""

    def end_segment(self, *, before_tool_call: bool) -> str:
        """一段文字结束（遇到非文字事件，或 run 结束），返回还没发出去的文字。

        before_tool_call=True 且攒着的是英文过程句时丢掉。之后的下一段重新判断。
        """
        buffered, self._buffer = self._buffer, ""
        self._passthrough = not self._drop_english_narration
        if not buffered:
            return ""
        if before_tool_call and is_english_process_narration(buffered):
            return ""
        return self._emit(buffered)
