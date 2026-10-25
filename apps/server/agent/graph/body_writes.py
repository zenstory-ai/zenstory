"""一个 writer run 往正文类文件（draft / script）里实际写了多少字。

自动质检门原来按 writer 本轮全部输出文字的字符数（`len(agent_content)`）判断，只要另有
一次写工具成功就送审：改大纲、改设定、补角色卡时，writer 附一段 500 字以上的说明，照样
被送去审稿（I03，10-10 / 10-11 生产仍有 5 次）。这里改为只看写进正文类文件的内容：

- create_file 建的是 draft / script：算紧随其后的 ``<file>…</file>`` 正文（writer 的
  TEXT 里能看到同一段正文，与 StreamAdapter 落库的是同一份）；复用已有正文的文件时，
  没有 ``</file>`` 结尾的正文会被 StreamAdapter 拒存，不算；
- edit_file 改的是 draft / script：算这次写进去的文字（edits 里 ``new`` / ``text``）；
- parallel_execute 的 write_chapter / edit_file 子任务同理。

字数用 `count_words`，与编辑器一致。只认成功的写入（edit_file 部分成功且确实改了文件
也算，但失败的那几处编辑——``failed_edits`` 里的 index——不算）；大纲、设定、角色、素材、
文档都不算。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent.constants import CONTENT_FILE_TYPES, coerce_bool
from agent.core.workflow_events import StreamEvent, StreamEventType
from utils.text_metrics import count_words

_FILE_BLOCK_RE = re.compile(
    r"<\s*file\b[^>]*>(.*?)(?:(?P<close><\s*/\s*file\s*>)|$)", re.IGNORECASE | re.DOTALL
)
_EDIT_TEXT_KEYS = ("new", "text")


@dataclass
class BodyWrite:
    """一次写进正文类文件的记录。words 是本次写进去的字数（编辑器口径）。"""

    file_id: str
    title: str
    file_type: str
    words: int = 0
    # True：words 是整份正文的字数（create_file / write_chapter）；False：只是这次改动写进去的字数。
    full_body: bool = False
    # create_file：正文在之后的 <file> 块里，记下当时 writer 文字的长度，run 结束时再取。
    text_offset: int | None = None
    # create_file 复用了已有正文的文件：StreamAdapter 不拿没收到 </file> 的残稿整体覆盖原文
    # （_should_refuse_truncated_overwrite），所以只有闭合的 <file> 块才算写进去了。
    requires_closed_block: bool = False
    # edit_file：工具返回的改后全文字数（new_word_count）；没返回时为 None。
    file_words: int | None = None
    # Diagnostic fact only: legacy success/word-count quality gating is unchanged.
    mutation_applied: bool = False


def _tool_payload(result: Any) -> dict[str, Any] | None:
    """TOOL_RESULT 的 result（MCP 文本载荷）里的 JSON 对象。"""
    if isinstance(result, dict) and "content" not in result and "status" in result:
        return result
    if not isinstance(result, dict):
        return None
    for block in result.get("content") or []:
        if not isinstance(block, dict):
            continue
        try:
            payload = json.loads(str(block.get("text") or ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


def _write_applied(payload: dict[str, Any]) -> bool:
    status = payload.get("status")
    if status == "success":
        data = payload.get("data")
        return not (isinstance(data, dict) and data.get("all_failed"))
    return status == "partial" and payload.get("mutation_applied") is True


def _ineffective_edit_indices(result_data: dict[str, Any]) -> set[int]:
    """edit_file 里没写入任何字的编辑（0-based index）：failed_edits 没生效，
    unchanged_edits 是 old 与 new 相同、原文已是这样的确认。"""
    indices: set[int] = set()
    for key in ("failed_edits", "unchanged_edits"):
        items = result_data.get(key)
        if not isinstance(items, list):
            continue
        indices.update(
            item["index"]
            for item in items
            if isinstance(item, dict) and isinstance(item.get("index"), int) and not isinstance(item.get("index"), bool)
        )
    return indices


def _edit_words(edits: Any, failed: set[int] | None = None) -> int:
    """这次写进去的字数；failed 里的编辑没写入任何字，不算。"""
    if not isinstance(edits, list):
        return 0
    total = 0
    for index, edit in enumerate(edits):
        if not isinstance(edit, dict) or edit.get("op") == "delete" or (failed and index in failed):
            continue
        for key in _EDIT_TEXT_KEYS:
            value = edit.get(key)
            if isinstance(value, str) and value:
                total += count_words(value)
                break
    return total


def _reuses_existing_body(data: dict[str, Any]) -> bool:
    """create_file 命中复用分支、且目标文件原本有正文（与 StreamAdapter 的覆盖保护同一判断）。"""
    if not coerce_bool(data.get("reused_existing")):
        return False
    content = data.get("content")
    try:
        original = int(data.get("original_content_length"))
    except (TypeError, ValueError):
        original = 0
    return max(original, len(content) if isinstance(content, str) else 0) > 0


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _is_body_type(file_type: Any) -> bool:
    return isinstance(file_type, str) and file_type in CONTENT_FILE_TYPES


@dataclass
class BodyWriteTracker:
    """跟着一个 agent run 的事件记下正文类文件的写入。"""

    writes: list[BodyWrite] = field(default_factory=list)
    _inputs: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)

    def observe(self, event: StreamEvent, *, text_length: int) -> BodyWrite | None:
        """看一个事件；是一次正文类文件的成功写入时返回这条记录。

        text_length 是到这个事件为止 writer 已输出文字的长度（create_file 的正文从这里往后找）。
        """
        if event.type == StreamEventType.TOOL_USE:
            if event.data.get("status") == "complete":
                call_id = str(event.data.get("id") or "")
                tool_input = event.data.get("input")
                if call_id:
                    self._inputs[call_id] = (
                        str(event.data.get("name") or ""),
                        tool_input if isinstance(tool_input, dict) else {},
                    )
            return None
        if event.type != StreamEventType.TOOL_RESULT:
            return None
        call_id = str(event.data.get("tool_use_id") or "")
        name, tool_input = self._inputs.get(call_id, ("", {}))
        name = str(event.data.get("name") or name)
        payload = _tool_payload(event.data.get("result"))
        if payload is None or not _write_applied(payload):
            return None
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        recorded: BodyWrite | None = None
        # 结果过大被压缩时 data 里可能没有 file_type：create_file 的参数里也有。
        created_type = data.get("file_type") or tool_input.get("file_type")
        if name == "create_file" and _is_body_type(created_type):
            recorded = BodyWrite(
                file_id=str(data.get("id") or ""),
                title=str(data.get("title") or tool_input.get("title") or ""),
                file_type=str(created_type),
                full_body=True,
                text_offset=text_length,
                requires_closed_block=_reuses_existing_body(data),
            )
        elif name == "edit_file" and _is_body_type(data.get("file_type")):
            recorded = BodyWrite(
                file_id=str(data.get("id") or tool_input.get("id") or ""),
                title=str(data.get("title") or ""),
                file_type=str(data.get("file_type")),
                words=_edit_words(tool_input.get("edits"), _ineffective_edit_indices(data)),
                file_words=_int_or_none(data.get("new_word_count")),
                mutation_applied=payload.get("mutation_applied") is True,
            )
        elif name == "parallel_execute":
            recorded = self._observe_parallel(tool_input, data)
            return recorded
        if recorded is not None:
            self.writes.append(recorded)
        return recorded

    def _observe_parallel(self, tool_input: dict[str, Any], data: dict[str, Any]) -> BodyWrite | None:
        planned = tool_input.get("tasks") if isinstance(tool_input.get("tasks"), list) else []
        results = data.get("tasks") if isinstance(data.get("tasks"), list) else []
        last: BodyWrite | None = None
        for index, task in enumerate(results):
            if not isinstance(task, dict) or task.get("status") != "completed":
                continue
            plan = planned[index] if index < len(planned) and isinstance(planned[index], dict) else {}
            params = plan.get("params") if isinstance(plan.get("params"), dict) else {}
            result = task.get("result")
            result_data = result.get("data") if isinstance(result, dict) else None
            result_data = result_data if isinstance(result_data, dict) else {}
            task_type = task.get("type")
            file_words: int | None = None
            if task_type == "write_chapter":
                file_type = result_data.get("file_type") or "draft"
                words = count_words(params.get("content") if isinstance(params.get("content"), str) else "")
                full_body = True
            elif task_type == "edit_file":
                file_type = result_data.get("file_type")
                words = _edit_words(params.get("edits"), _ineffective_edit_indices(result_data))
                full_body = False
                file_words = _int_or_none(result_data.get("new_word_count"))
            else:
                continue
            if not _is_body_type(file_type):
                continue
            last = BodyWrite(
                file_id=str(result_data.get("id") or params.get("id") or ""),
                title=str(result_data.get("title") or params.get("title") or ""),
                file_type=str(file_type),
                words=words,
                full_body=full_body,
                file_words=file_words,
                mutation_applied=isinstance(result, dict) and result.get("mutation_applied") is True,
            )
            self.writes.append(last)
        return last

    def finish(self, agent_text: str) -> list[BodyWrite]:
        """run 结束：create_file 记录取它之后第一段 <file> 正文的字数，返回本 run 的全部记录。"""
        offsets = [write.text_offset for write in self.writes if write.text_offset is not None]
        for write in self.writes:
            if write.text_offset is None:
                continue
            later = [offset for offset in offsets if offset > write.text_offset]
            segment = agent_text[write.text_offset : min(later) if later else len(agent_text)]
            match = _FILE_BLOCK_RE.search(segment)
            saved = match is not None and (match.group("close") is not None or not write.requires_closed_block)
            write.words = count_words(match.group(1)) if saved else 0
            write.text_offset = None
        return list(self.writes)


def body_words_written(writes: list[BodyWrite]) -> int:
    return sum(write.words for write in writes)


def measured_body_words(writes: list[BodyWrite]) -> int:
    """已经知道字数的写入合计（run 进行中：不含正文还没写出来的 create_file）。"""
    return sum(write.words for write in writes if write.text_offset is None)


def latest_file_word_counts(writes: list[BodyWrite]) -> list[tuple[str, int]] | None:
    """每个正文文件在最后一次写入后的全文字数，按首次写入的顺序。

    改动在建文件之后时取 edit_file 返回的 new_word_count；某次改动没返回字数时，这个文件不列，
    不拿建文件时的旧字数顶替。
    """
    latest: dict[str, tuple[str, int | None]] = {}
    for write in writes:
        key = write.file_id or write.title
        if not key:
            continue
        title = write.title or latest.get(key, ("", None))[0]
        latest[key] = (title, write.words if write.full_body else write.file_words)
    counts = [(title, words) for title, words in latest.values() if words is not None]
    return counts or None


__all__ = [
    "BodyWrite",
    "BodyWriteTracker",
    "body_words_written",
    "latest_file_word_counts",
    "measured_body_words",
]
