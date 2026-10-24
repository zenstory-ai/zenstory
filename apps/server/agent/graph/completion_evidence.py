"""agent run 收尾时的文字信号，供 writing_graph 对照本请求的实际写入记录使用。

这次 run 是不是停在一段写完的 ``<file>…</file>`` 上（``</file>`` 之后没有给作者的话）。
流式写文件协议把正文放在回复文字里，而 openai-agents 把「不带工具调用的回复」当作
最终输出、直接结束 run。所以写完 ``</file>`` 没有接着调用工具时，run 结束不是模型决定
收尾，而是协议让正文这次回复成了最后一次回复：计划里没写的文件和给作者的总结都还没来得及写。

这里只看文字；有没有真的新建过文件由调用方用本次 run 的工具事件和读写台账（RepeatReadGuard）判断。
"""

from __future__ import annotations

import re

from agent.core.stream_processor import normalize_file_markers

_FILE_BLOCK_RE = re.compile(r"<file>.*?(?:</file>|\Z)", re.DOTALL)
_TASK_COMPLETE_RE = re.compile(
    r"(?:\[\s*task_complete\s*\]|[\[【]\s*任务完成\s*[\]】])", re.IGNORECASE
)


def author_reply_tail(tail_text: str, *, max_chars: int = 600) -> str:
    """Bound the post-file prose without interpreting questions, intent or consent."""
    text = normalize_file_markers(tail_text or "")
    close = text.rfind("</file>")
    if close != -1:
        text = text[close + len("</file>"):]
    if "<file>" in text:
        return ""
    text = _TASK_COMPLETE_RE.sub("", text).strip()
    if len(text) <= max_chars:
        return text
    marker = "\n…（中间内容已截断）…\n"
    if max_chars <= len(marker):
        return marker[:max(0, max_chars)]
    available = max_chars - len(marker)
    tail_chars = available // 2
    return text[:available - tail_chars] + marker + (text[-tail_chars:] if tail_chars else "")


def ends_on_file_block(tail_text: str) -> bool:
    """最后一次工具调用之后的文字是不是以一段非空、已闭合的 ``<file>`` 正文结束。

    ``</file>`` 之后只剩空白、完成标记或空的 ``<file></file>`` 时也算（作者看不到任何话）。
    """
    normalized = normalize_file_markers(tail_text or "")
    close = normalized.rfind("</file>")
    if close == -1:
        return False
    after = normalized[close + len("</file>"):]
    after = after.replace("<file>", "").replace("</file>", "")
    if _TASK_COMPLETE_RE.sub("", after).strip():
        return False
    before = normalized[:close]
    start = before.rfind("<file>")
    if start == -1:
        return False
    body = before[start + len("<file>"):]
    if body.strip():
        return True
    # 最后一对是空的 <file></file>（模型闭合后又多发了一对），往前找真正写了正文的那一段。
    blocks = [
        block[len("<file>"):].removesuffix("</file>")
        for block in _FILE_BLOCK_RE.findall(before[:start])
    ]
    return any(block.strip() for block in blocks)


__all__ = [
    "author_reply_tail",
    "ends_on_file_block",
]
