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


def ends_on_unclosed_file_block(tail_text: str) -> bool:
    """最后一段文字是不是非空且仍未闭合的 ``<file>`` 正文。

    这里只识别流式文件协议的边界；是否属于可在 EOF 抢救保存的新文件正文，
    由调用方结合本次 run 的结构化写入记录判断。
    """
    normalized = normalize_file_markers(tail_text or "")
    start = normalized.rfind("<file>")
    if start == -1:
        return False
    body = normalized[start + len("<file>") :]
    return "</file>" not in body and bool(body.strip())


__all__ = [
    "author_reply_tail",
    "ends_on_file_block",
    "ends_on_unclosed_file_block",
]


# ---------------------------------------------------------------- 文字里没执行的工具调用

# 只认匿名样本里实际出现过的完整结构：成对的 <tool_call> … </tool_call>，里面至少一个
# <invoke name="已注册工具"> … </invoke>，其间可能夹着模型专用分隔符。只有开头或结尾的残片、
# 没有外层 <tool_call> 的 invoke、其他写法都不认（宁可漏报，不扩大词表去猜）。
_MODEL_TOOL_DELIMITER = "]<]minimax[>["
_TOOL_CALL_BLOCK_RE = re.compile(r"<tool_call>(.*?)</tool_call>", re.DOTALL)
_INVOKE_RE = re.compile(r"<invoke name=\"([^\"<>\n]{1,64})\">.*?</invoke>", re.DOTALL)
# 示例与转述：代码围栏（未闭合时到结尾）、行内代码、Markdown 引用行。
_FENCE_RE = re.compile(r"^[ \t]*(```|~~~)[^\n]*\n.*?(?:^[ \t]*\1[ \t]*$|\Z)", re.DOTALL | re.MULTILINE)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_BLOCKQUOTE_LINE_RE = re.compile(r"^[ \t]*>.*$", re.MULTILINE)


def unexecuted_tool_protocol(tail_text: str, registered_tools: set[str] | frozenset[str]) -> list[str]:
    """最后一次工具事件之后的文字里，写成文字的完整工具调用（按出现顺序返回工具名）。

    这是有界的机器协议结构识别，不判断作者意图或任务是否完成：调用方只在这次 run 的
    最后一次真实工具事件之后的文字上调用它，所以命中的调用一定没有被执行。``<file>`` 正文、
    代码、引用里的同形文字是正文或示例，不算；工具名必须属于当前 agent 实际注册的工具。
    """
    if not tail_text or "<tool_call>" not in tail_text:
        return []
    text = _FILE_BLOCK_RE.sub("", normalize_file_markers(tail_text))
    text = _FENCE_RE.sub("", text)
    text = _INLINE_CODE_RE.sub("", text)
    text = _BLOCKQUOTE_LINE_RE.sub("", text)
    text = text.replace(_MODEL_TOOL_DELIMITER, "\n")
    names: list[str] = []
    for block in _TOOL_CALL_BLOCK_RE.finditer(text):
        names.extend(
            name for name in _INVOKE_RE.findall(block.group(1)) if name in registered_tools
        )
    return names
