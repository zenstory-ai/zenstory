"""单循环 agent 的文字在发给作者之前的整理（AgentTextShaper）。

作者写中文时，紧接工具调用的英文过程说明不发；同一个 agent 隔着工具调用的两段文字之间补空行。
"""

from __future__ import annotations

import re

# 整章替换 / 截断补全的改写最终没有落库时给作者的固定说明（StreamAdapter 拒绝截断覆盖、
# loop 引擎放弃整章替换时共用一份）。
REWRITE_NOT_SAVED_NOTICE = "本次改写未保存，原文保持不变。"
REWRITE_NOT_SAVED_NOTICE_EN = "This rewrite was not saved. The original text is unchanged."


def rewrite_not_saved_notice(english: bool) -> str:
    return REWRITE_NOT_SAVED_NOTICE_EN if english else REWRITE_NOT_SAVED_NOTICE


# 新正文写到单次回复的输出上限、在中途被截断（runner 的 output_capped）时给作者的固定说明：
# 截断处之前的正文已保存，不说这一章写完了。
BODY_CUT_AT_OUTPUT_CAP_NOTICE = "{titles}写到了单次回复的输出上限，正文已保存到中断处。"
BODY_CUT_AT_OUTPUT_CAP_NOTICE_EN = "{titles} reached the single-reply output limit; the text up to that point is saved."


def body_cut_at_output_cap_notice(english: bool, titles: str) -> str:
    return (BODY_CUT_AT_OUTPUT_CAP_NOTICE_EN if english else BODY_CUT_AT_OUTPUT_CAP_NOTICE).format(titles=titles)


_CJK = r"㐀-鿿豈-﫿"


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
    - 同一个 agent 隔着工具调用的两段文字也隔开（I03：U094 一连五句「正在…」在保存的回复里
      首尾相连）。直播时工具卡片把它们分开，但存进历史、复制出去的是拼起来的一整段。以 ``<``
      开头的一段（``<file>`` 正文）不补。
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
        # 已发出文字的结尾（判断要不要补空行）与「上一段之后调用过工具」。
        self._tail = self._previous_text[-2:]
        self._after_tool_call = False

    @staticmethod
    def _separated(previous_tail: str, text: str) -> str:
        if previous_tail.strip() and not previous_tail.endswith("\n\n") and not text.startswith("\n\n"):
            separator = "\n" if previous_tail.endswith("\n") or text.startswith("\n") else "\n\n"
            return separator + text
        return text

    def _emit(self, text: str) -> str:
        if not text:
            return ""
        if not self._started:
            self._started = True
            if self._previous_text.strip() and not text.lstrip().startswith("<"):
                text = self._separated(self._tail, text)
        elif self._after_tool_call and not text.lstrip().startswith("<"):
            text = self._separated(self._tail, text)
        self._after_tool_call = False
        self._tail = (self._tail + text)[-2:]
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
        if not buffered or (before_tool_call and is_english_process_narration(buffered)):
            emitted = ""
        else:
            emitted = self._emit(buffered)
        if before_tool_call:
            self._after_tool_call = True
        return emitted
