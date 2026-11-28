"""
File edit operations for agent tools.

This module provides precise file editing operations:
- edit_file: Apply multiple edit operations (replace/insert/append/prepend/delete)

Supports fuzzy and approximate text matching for robust editing even when
the LLM provides slightly different text.
"""

import copy
import json
import re
from collections.abc import Callable, Collection, Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from sqlmodel import Session, col, or_, select

from agent.constants import coerce_bool
from agent.tools.permissions import (
    ForbiddenError,
    NotFoundError,
    check_file_access_in_tool_context,
    check_project_ownership,
)
from config.datetime_utils import advance_timestamp, normalize_datetime_to_utc, utcnow
from models import File
from models.file_version import (
    CHANGE_SOURCE_AI,
    CHANGE_SOURCE_SYSTEM,
    CHANGE_TYPE_AI_EDIT,
    CHANGE_TYPE_EDIT,
)
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import BEFORE_AI_EDIT_SUMMARY, FileVersionService, content_sha256
from services.file_tree_rules import apply_file_title_and_order, has_sibling_title_conflict
from utils.cjk_quotes import (
    QUOTE_STYLE_CURLY,
    detect_quote_style,
    has_style_quote_marks,
    normalize_inserted_spans,
    resolve_quote_style,
)
from utils.file_write_lock import (
    EVENT_LOOP_LOCK_WAIT_SECONDS,
    FileWriteBusyError,
    acquire_file_write_lock,
    file_write_lock,
)
from utils.logger import get_logger, log_with_context
from utils.text_metrics import count_words

from .content_range import format_match_snippets
from .paragraph_layout import (
    NOTE_INDENT_FOLLOWS_FILE,
    NOTE_SENTENCE_SNAP,
    exact_anchor_snap_forward,
    follow_span_indentation,
    indent_unindented_first_paragraph,
    is_sentence_boundary,
    is_sentence_shaped,
    layout_insertion,
    snap_to_sentence_boundary,
)
from .text_matching import (
    _edge_punct_class,
    build_span_previews,
    edge_punct_run,
    extend_span_over_edge_punct,
    find_approximate_match,
    find_fuzzy_spans,
    find_unique_line_span,
    fold_double_quotes,
    locate_exact_or_quote_equivalent,
    suggest_similar_lines,
)

logger = get_logger(__name__)

# Cap for replace_all fuzzy scanning. The exact-match path replaces EVERY
# occurrence, so the fuzzy path must not silently stop at a handful; this cap
# only guards pathological inputs and is surfaced as an explicit warning when
# actually hit.
REPLACE_ALL_MAX_FUZZY_MATCHES = 1000

# Cross-file deterministic batch replace caps.
BATCH_REPLACE_MAX_FILES = 30
BATCH_REPLACE_MAX_REMAINING_FILES = 30
BATCH_REPLACE_PREVIEW_SNIPPETS = 3
BATCH_REPLACE_SNIPPET_CONTEXT_CHARS = 35

# Failure-only paragraph/line-number prefix stripping (minimax-code edit-line-number-retry safety contract).
# Uses `[ \t]*` after `]` instead of `\s*` so Chinese full-width indentation (`\u3000\u3000`) is preserved.
_PARAGRAPH_MARKER_PREFIX_RE = re.compile(
    r"^(?:\[P\d+(?:\s*·\s*\d+字)?\][ \t]*|[ \t]*\d+→)"
)

# Exact prefix emitted by `content_range.format_paragraph_stats`: `[P01 · 84字] `.
# Intentionally does NOT match authentic prose markers like `[P1]`, `[P05]`, `[P05 备注]`,
# and only consumes the single ASCII space after `]` so Chinese full-width indent `\u3000\u3000` survives.
_PARAGRAPH_STATS_MARKER_LINE_RE = re.compile(r"^\[P\d+ · \d+字\] ")
_PARAGRAPH_STATS_MARKER_CONTAINS_RE = re.compile(r"\[P\d+ · \d+字\] ")


def _all_non_empty_lines_have_marker_prefix(text: str) -> bool:
    """Return True iff ``text`` has >=1 non-empty line and EVERY non-empty line starts with a view marker."""
    non_empty = [line for line in text.splitlines() if line.strip()]
    if not non_empty:
        return False
    return all(_PARAGRAPH_MARKER_PREFIX_RE.match(line) is not None for line in non_empty)


def _strip_marker_prefixes(text: str) -> str:
    """Strip one leading paragraph/line-number marker prefix from every non-empty line."""
    return "".join(
        _PARAGRAPH_MARKER_PREFIX_RE.sub("", line, count=1) if line.strip() else line
        for line in text.splitlines(keepends=True)
    )


def _content_has_paragraph_stats_marker(content: str) -> bool:
    """Return True when ``content`` already legitimately contains ``[Pxx · N字] ``."""
    return "[P" in content and _PARAGRAPH_STATS_MARKER_CONTAINS_RE.search(content) is not None


def _strip_paragraph_stats_marker_prefixes(text: str) -> tuple[str, bool]:
    """Strip exact ``[Pxx · N字] `` prefixes line-by-line from write inputs."""
    if "[P" not in text:
        return text, False
    stripped_any = False
    out_lines: list[str] = []
    for line in text.splitlines(keepends=True):
        updated, count = _PARAGRAPH_STATS_MARKER_LINE_RE.subn("", line, count=1)
        if count:
            stripped_any = True
        out_lines.append(updated)
    if not stripped_any:
        return text, False
    return "".join(out_lines), True


def _compile_overlapping_batch_pattern(old_text: str, new_text: str) -> re.Pattern[str] | None:
    """Compile left-to-right alternation protecting existing ``new_text`` when ``old_text in new_text``."""
    if not old_text or old_text == new_text or old_text not in new_text:
        return None
    return re.compile(f"{re.escape(new_text)}|({re.escape(old_text)})")


def _find_batch_replace_spans(
    content: str,
    old_text: str,
    new_text: str,
    *,
    overlap_pattern: re.Pattern[str] | None = None,
) -> list[tuple[int, int]]:
    """Find effective non-overlapping ``(start, end)`` spans to replace with ``new_text``.

    When ``old_text in new_text`` (e.g. ``陈砚→陈砚之``, ``小雨→林小雨``, ``…→……``, ``a→aba``),
    occurrences already inside an existing ``new_text`` instance are left untouched via
    left-to-right alternation so repeated calls never cascade.
    """
    if not content or not old_text or old_text == new_text:
        return []
    if old_text in new_text:
        pattern = overlap_pattern or _compile_overlapping_batch_pattern(old_text, new_text)
        if pattern is None:
            return []
        return [(m.start(1), m.end(1)) for m in pattern.finditer(content) if m.group(1) is not None]
    spans: list[tuple[int, int]] = []
    step = len(old_text)
    pos = content.find(old_text)
    while pos >= 0:
        spans.append((pos, pos + step))
        pos = content.find(old_text, pos + step)
    return spans


def _apply_batch_replace_spans(
    content: str,
    spans: Sequence[tuple[int, int]],
    new_text: str,
) -> str:
    """Apply non-overlapping ``(start, end)`` replacement spans to ``content``."""
    if not spans:
        return content
    parts: list[str] = []
    prev = 0
    for start, end in spans:
        parts.append(content[prev:start])
        parts.append(new_text)
        prev = end
    parts.append(content[prev:])
    return "".join(parts)


def _resolve_batch_replace_texts(
    old_content: str,
    old_text: str,
    new_text: str,
    *,
    crlf_old_text: str | None = None,
    stripped_new_text: str | None = None,
    has_marker_in_new: bool | None = None,
) -> tuple[str, str, bool]:
    """Resolve per-file ``(effective_old, effective_new, marker_stripped)`` for batch replace.

    Order of operations:
    1. Strip leaked ``[Pxx · N字] `` prefixes from ``new_text`` BEFORE CRLF adaptation when
       ``old_content`` does not already contain those markers. Reject marker-only inputs that
       strip to empty or whitespace/newline-only rather than silently deleting.
    2. Adapt ``effective_old`` and ``effective_new`` to CRLF when needed (preserving CRLF on
       CRLF files for LF-only tool inputs).
    """
    if _content_has_paragraph_stats_marker(old_content):
        base_new = new_text
        marker_stripped = False
    else:
        if has_marker_in_new is None or stripped_new_text is None:
            stripped_new_text, has_marker_in_new = _strip_paragraph_stats_marker_prefixes(new_text)
        if has_marker_in_new:
            if not stripped_new_text.strip():
                raise EditFileError(
                    'batch_replace_files 的 new_text 仅包含段落统计前缀，没有可替换的正文内容；'
                    '如需删除请显式传入 new_text=""。',
                    error_type=EDIT_ERROR_INVALID_EDIT,
                )
            base_new = stripped_new_text
            marker_stripped = True
        else:
            base_new = new_text
            marker_stripped = False

    effective_old = old_text
    effective_new = (
        _adapt_lf_to_crlf(base_new)
        if _is_crlf_content(old_content) and "\n" in base_new and "\r\n" not in base_new
        else base_new
    )
    if (
        old_text not in old_content
        and "\r\n" in old_content
        and "\n" in old_text
        and "\r\n" not in old_text
    ):
        crlf_candidate = crlf_old_text if crlf_old_text is not None else _adapt_lf_to_crlf(old_text)
        if crlf_candidate in old_content:
            effective_old = crlf_candidate
            effective_new = _adapt_lf_to_crlf(base_new)
    return effective_old, effective_new, marker_stripped


def _batch_replace_single_file_hint(file_id: str | None) -> str:
    """Build scoped single-file replacement hint so ambiguity errors never direct to unscoped project-wide replace."""
    if file_id:
        scoped_ids = json.dumps([file_id], ensure_ascii=False)
        return (
            f"（确实要把当前文件里多处相同原文全部替换时，请改用 "
            f"batch_replace_files(file_ids={scoped_ids}, ...)；"
            f"只改当前文件时切勿省略 file_ids，否则会扫描整个项目）"
        )
    return (
        "（确实要把当前文件里多处相同原文全部替换时，请改用 batch_replace_files "
        "并传入 file_ids=[当前文件ID] 限定仅修改当前文件，切勿省略 file_ids，否则会扫描整个项目）"
    )


class PreWriteHistoryUnavailableError(RuntimeError):
    """提交 AI 修改前没能先建立原稿历史锚点，已回滚、原文保持不变。"""

    error_type = "pre_write_history_unavailable"

    def __init__(
        self,
        file_id: str,
        *,
        action: str,
        title: str | None = None,
        file_type: str | None = None,
    ) -> None:
        self.file_id = file_id
        self.action = action
        self.title = title
        self.file_type = file_type
        super().__init__("原稿未能先保存历史备份，为避免丢失内容，此次修改未写入。")


# --- edit_file 的稳定错误类型 ---------------------------------------------
# error 字段是写给模型看的（带候选片段、occurrence 提示，模型靠它重新定位）；
# error_type 是稳定的机器可读分类，user_message 是给作者看的一句短话，前端
# 卡片据此展示，不再把模型指令、内部 id 直接甩给作者。
EDIT_ERROR_ANCHOR_NOT_FOUND = "anchor_not_found"
EDIT_ERROR_ANCHOR_AMBIGUOUS = "anchor_ambiguous"
EDIT_ERROR_FILE_NOT_FOUND = "file_not_found"
EDIT_ERROR_INVALID_EDIT = "invalid_edit"
# 编辑定位成功、执行后正文却一字未变（new 与原文相同，或引号按体例统一后相同）。
# 以前照样记一次 applied，模型和作者都以为改了。
EDIT_ERROR_NO_CHANGE = "no_change"
EDIT_ERROR_FILE_BUSY = "file_busy"
EDIT_ERROR_PERMISSION_DENIED = "permission_denied"
EDIT_ERROR_GENERIC = "edit_failed"
# 模型读过这个文件之后正文又变了（多半是作者在编辑器里改的）：不在旧正文上改。
EDIT_ERROR_STALE_CONTENT = "stale_content"
# 单循环 agent 改正文（draft / script）时的两道结构检查，见 check_body_edit_guards。
EDIT_ERROR_WHOLE_BODY_REPLACE = "whole_body_replace"
EDIT_ERROR_DUPLICATED_PARAGRAPH = "duplicated_paragraph"
EDIT_ERROR_PRE_WRITE_HISTORY_UNAVAILABLE = "pre_write_history_unavailable"
EDIT_ERROR_CHAPTER_REPLACE_PENDING = "chapter_replace_pending"
EDIT_ERROR_CHAPTER_TARGET_AMBIGUOUS = "chapter_target_ambiguous"
EDIT_ERROR_INVALID_PARENT_ID = "invalid_parent_id"
EDIT_ERROR_INVALID_READ_RANGE = "invalid_read_range"
EDIT_ERROR_SKILL_NOT_FOUND = "skill_not_found"
EDIT_ERROR_SKILL_RESOURCE_NOT_FOUND = "skill_resource_not_found"
EDIT_ERROR_SKILL_LIMIT_EXCEEDED = "skill_limit_exceeded"

EDIT_ERROR_USER_MESSAGES: dict[str, str] = {
    EDIT_ERROR_ANCHOR_NOT_FOUND: "未在原文中找到对应段落，文件未改动。",
    EDIT_ERROR_ANCHOR_AMBIGUOUS: "要修改的片段在原文中出现多处，文件未改动。",
    EDIT_ERROR_FILE_NOT_FOUND: "未找到目标文件，文件未改动。",
    EDIT_ERROR_INVALID_EDIT: "修改参数不完整，此次修改未执行，文件未改动。",
    EDIT_ERROR_PERMISSION_DENIED: "无权修改该文件，文件未改动。",
    EDIT_ERROR_STALE_CONTENT: "该文件在 AI 读取后又被修改过，未在旧内容上改动。",
    EDIT_ERROR_NO_CHANGE: "修改前后内容一致，无需改动。",
    EDIT_ERROR_FILE_BUSY: "目标文件正忙，此次修改未写入。",
    EDIT_ERROR_PRE_WRITE_HISTORY_UNAVAILABLE: "原稿未能先保存历史备份，为避免丢失内容，此次修改未写入。",
    EDIT_ERROR_WHOLE_BODY_REPLACE: "整章重写需按章号写入，此次局部编辑未改动文件。",
    EDIT_ERROR_DUPLICATED_PARAGRAPH: "此次修改会导致正文出现重复段落，未保存改动。",
    EDIT_ERROR_CHAPTER_REPLACE_PENDING: "该章节正在等待写入完整正文，此次修改未执行。",
    EDIT_ERROR_CHAPTER_TARGET_AMBIGUOUS: "找到多个同编号章节，无法确认目标文件，文件未改动。",
    EDIT_ERROR_INVALID_PARENT_ID: "目标文件夹不存在，未创建文件。",
    EDIT_ERROR_INVALID_READ_RANGE: "读取范围参数无效，未能读取目标内容。",
    EDIT_ERROR_SKILL_NOT_FOUND: "未找到已启用的目标技能。",
    EDIT_ERROR_SKILL_RESOURCE_NOT_FOUND: "未找到该技能对应的参考资料。",
    EDIT_ERROR_SKILL_LIMIT_EXCEEDED: "本轮加载的技能数量已达上限。",
}
EDIT_ERROR_GENERIC_USER_MESSAGE = "此次修改未能生效，文件未改动。"

_STANDARD_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_STANDARD_UUID_LEN = 36


def format_malformed_uuid_hint(file_ids: Sequence[str]) -> str:
    """Return a crisp format/length diagnostic when any missing ID is not a standard 36-char UUID.

    Never guesses, fuzzy-matches, or auto-corrects UUIDs.
    """
    malformed: list[str] = []
    for raw in file_ids:
        if not isinstance(raw, str):
            continue
        cleaned = raw.strip()
        if not cleaned or _STANDARD_UUID_RE.match(cleaned):
            continue
        if cleaned not in malformed:
            malformed.append(cleaned)
    if not malformed:
        return ""
    sample = "、".join(f"「{fid}」（长度 {len(fid)}）" for fid in malformed[:3])
    return (
        f" 注意：传入 ID {sample} 不符合标准 {_STANDARD_UUID_LEN} 位 UUID 格式（8-4-4-4-12），"
        "可能存在多抄、漏抄或截断；请直接从 <project_files> 或 search_files 结果逐字复制完整 ID。"
    )

# 近似/模糊匹配改动后附在 detail 上的提醒（模型与作者都能看到实际改了哪段原文）。
FUZZY_EDIT_WARNING = "已按近似匹配改动，请核对"


def edit_error_user_message(error_type: str | None) -> str:
    """error_type 对应的作者可读短句；未知类型用通用说法。"""
    return EDIT_ERROR_USER_MESSAGES.get(error_type or "", EDIT_ERROR_GENERIC_USER_MESSAGE)


class EditFileError(ValueError):
    """edit_file 的可恢复错误：str(e) 给模型，error_type / user_message 给界面。

    继承 ValueError：按 ValueError 捕获的调用方（含测试）照常接住。
    """

    def __init__(
        self,
        message: str,
        *,
        error_type: str = EDIT_ERROR_INVALID_EDIT,
        file_id: str | None = None,
        title: str | None = None,
        file_type: str | None = None,
    ):
        super().__init__(message)
        self.error_type = error_type
        self.file_id = file_id
        self.title = title
        self.file_type = file_type

    def attach_file_meta(
        self,
        *,
        file_id: str | None = None,
        title: str | None = None,
        file_type: str | None = None,
    ) -> "EditFileError":
        if file_id:
            self.file_id = file_id
        if title:
            self.title = title
        if file_type:
            self.file_type = file_type
        return self

    @property
    def user_message(self) -> str:
        return edit_error_user_message(self.error_type)

    def payload_fields(self) -> dict[str, Any]:
        """附加到工具错误结果上的结构化字段。"""
        fields: dict[str, Any] = {
            "error_type": self.error_type,
            "user_message": self.user_message,
            "edits_applied": 0,
            "mutation_applied": False,
        }
        if self.file_id:
            fields["id"] = self.file_id
        if self.title:
            fields["title"] = self.title
        if self.file_type:
            fields["file_type"] = self.file_type
        return fields


class StaleContentError(EditFileError):
    """模型读到的正文和锁内重读的当前正文不一致：不写库、不重试，把新改动告诉模型。

    complete：读过之后的改动都完整列出来了（处数没超、没有因为太长只显示改动附近）。这时调用方可以把
    「模型看到的正文」更新为当前正文；否则要模型先读全文。
    """

    MAX_LISTED = 6

    def __init__(
        self,
        seen: str,
        current: str,
        *,
        file_id: str | None = None,
        title: str | None = None,
        file_type: str | None = None,
    ):
        self.current_content = current
        changes, truncated = _line_changes(seen, current)
        self.complete = len(changes) <= self.MAX_LISTED and not truncated
        listed = "\n".join(changes[: self.MAX_LISTED])
        if self.complete:
            tail = "请基于现在的正文重新给出修改，新改的地方保留，不要改回去。"
        else:
            tail = (
                "改动没有全部列出（不完整）。先用 read_files(ids=[...]) 读一次全文，"
                "再基于现在的正文给出修改，新改的地方保留。"
            )
        super().__init__(
            "这个文件在你读过之后又被改过（可能是作者刚在编辑器里改的，也可能是本请求里别的步骤写的），"
            f"这次修改没有执行，一处都没改。读过之后的改动：\n{listed}\n{tail}",
            error_type=EDIT_ERROR_STALE_CONTENT,
            file_id=file_id,
            title=title,
            file_type=file_type,
        )


def _line_changes(before: str, after: str) -> tuple[list[str], bool]:
    """两份正文之间逐段落的改动说明，以及有没有因为太长只显示了改动附近。"""
    import difflib

    from agent.tools.author_changes import pair_clip

    old_lines, new_lines = before.split("\n"), after.split("\n")
    out: list[str] = []
    truncated = False
    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        removed, added, cut = pair_clip(
            "\n".join(line for line in old_lines[i1:i2] if line.strip()),
            "\n".join(line for line in new_lines[j1:j2] if line.strip()),
        )
        truncated = truncated or cut
        if removed and added:
            out.append(f"- 「{removed}」→「{added}」")
        elif added:
            out.append(f"- 新加了：「{added}」")
        elif removed:
            out.append(f"- 删掉了：「{removed}」")
    return out or ["- 只改了空白或换行"], truncated


class EditBatchError(EditFileError):
    """continue_on_error=false 时有编辑失败：整批回滚，列出全部失败项。"""

    def __init__(
        self,
        failed_edits: list[dict[str, Any]],
        edits_total: int,
        *,
        file_id: str | None = None,
        title: str | None = None,
        file_type: str | None = None,
    ):
        self.failed_edits = failed_edits
        self.edits_total = edits_total
        reasons = "；".join(str(item.get("error") or "") for item in failed_edits)
        message = (
            f"本次调用的 {edits_total} 处编辑全部未生效（已整体回滚），"
            f"请修正失败项后重新提交完整 edits 列表："
            f"失败项 edits[{'、'.join(str(item['index']) for item in failed_edits)}]"
            f"（0-based）。{reasons}"
        )
        if any(item.get("error_type") == EDIT_ERROR_ANCHOR_NOT_FOUND for item in failed_edits):
            # 事故里模型把整批回滚掉的 new 当成下一次的 old，反复找不到。候选片段取自本批前面各条
            # 编辑生效后的中间文本，可能带着已回滚的 new，不能当成已保存的正文。
            message += (
                "\n注意：本批所有 new 都没有保存，正文仍是这次调用之前的样子，不能把它们当作下一次的 old / anchor。"
                "失败项的候选片段是在本批前面几处编辑生效后的中间文本里找的，可能含已回滚的 new，"
                "不一定是已保存的原文。重建完整 edits 列表时只用确认是已保存原文的片段；"
                "确认不了就先用 read_files(ids=[...]) 读取相关段落。"
            )
        first_type = failed_edits[0].get("error_type") if failed_edits else None
        super().__init__(
            message,
            error_type=str(first_type or EDIT_ERROR_GENERIC),
            file_id=file_id,
            title=title,
            file_type=file_type,
        )

    def payload_fields(self) -> dict[str, Any]:
        fields = super().payload_fields()
        fields["edits_total"] = self.edits_total
        fields["failed_edits"] = self.failed_edits
        return fields


def _classify_edit_exception(exc: Exception) -> str:
    if isinstance(exc, EditFileError):
        return exc.error_type
    if isinstance(exc, ValueError):
        return EDIT_ERROR_INVALID_EDIT
    return EDIT_ERROR_GENERIC


def _not_found(message: str) -> EditFileError:
    return EditFileError(message, error_type=EDIT_ERROR_ANCHOR_NOT_FOUND)


def _ambiguous(message: str) -> EditFileError:
    return EditFileError(message, error_type=EDIT_ERROR_ANCHOR_AMBIGUOUS)


def _preview(text: str, max_len: int = 200) -> str:
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _approximate_not_found_advice(stats: dict[str, int], *, label: str) -> str:
    if stats.get("deadline_exceeded"):
        reason = "近似扫描因截止时间到达而未完成。"
    else:
        reason = "精确、引号等价、模糊和近似匹配都没有找到唯一位置。"
    return (
        f"{reason}请先用 read_files(ids=[...]) 重读当前文件"
        f"（仅超过 25000 字的超长文件才用 search_files(query=关键词, ids=[...]) 取逐字命中片段，或传 start_char + max_chars 分段读取），再从结果复制较短、逐字、"
        f"唯一的{label}；同一文件的多处独立修改合并进一次 edit_file 批量提交（超过 30 处或相互依赖时再拆分）；不要放宽匹配或整篇覆盖。"
    )


def _batch_anchor_invalidated(original: str, staged: str, edit: dict[str, Any]) -> bool:
    op = str(edit.get("op") or "")
    field = "anchor" if op in ("insert_after", "insert_before") else "old"
    target = edit.get(field)
    if not isinstance(target, str) or not target or original == staged:
        return False
    return (
        locate_exact_or_quote_equivalent(original, target) is not None
        and locate_exact_or_quote_equivalent(staged, target) is None
    )


def _compact_paragraph_break_excerpt(content: str, pattern: str, *, max_chars: int = 240) -> str:
    """When a cross-paragraph match is uniquely identifiable, return a compact verbatim excerpt."""
    spans = find_fuzzy_spans(
        content,
        pattern,
        ignore_punct_whitespace=True,
        max_matches=2,
        keep_line_breaks=False,
    )
    if len(spans) != 1:
        return ""
    start, end = spans[0]
    if "\n" not in content[start:end] and "\r" not in content[start:end]:
        return ""
    line_start = content.rfind("\n", 0, start)
    seg_start = 0 if line_start < 0 else line_start + 1
    line_end = content.find("\n", end)
    seg_end = len(content) if line_end < 0 else line_end
    excerpt = content[seg_start:seg_end]
    if len(excerpt) > max_chars:
        nl = content.find("\n", start, end)
        if nl >= 0:
            half = max_chars // 2
            left = max(seg_start, nl - half)
            right = min(seg_end, nl + half)
            excerpt = content[left:right]
        else:
            excerpt = excerpt[:max_chars]
    return f" 原文对应跨段片段（含实际换行与缩进）：{json.dumps(excerpt, ensure_ascii=False)}"


def _paragraph_break_error(
    edit_index: int,
    label: str,
    *,
    content: str | None = None,
    pattern: str | None = None,
) -> EditFileError:
    excerpt_hint = (
        _compact_paragraph_break_excerpt(content, pattern)
        if isinstance(content, str) and isinstance(pattern, str) and pattern
        else ""
    )
    return _not_found(
        f"Edit {edit_index}: {label}在原文里对应的那段跨过了段落分隔（原文这里有换行），"
        f"{label}里却没有换行；照这样改会把两段并成一段，已中止。请按原文的分段把换行"
        f"（以及下一段段首的缩进）照抄进 old 和 new，或者拆成每段一条 edit。{excerpt_hint}"
    )


def _no_change_error(edit_index: int, edit: dict[str, Any], quote_style: str | None) -> EditFileError:
    hint = ""
    written = edit.get("new") if edit.get("op") == "replace" else edit.get("text")
    if quote_style is not None and isinstance(written, str) and ('"' in written or "＂" in written):
        hint = "；new 里的半角双引号写入时会统一成全角引号，统一后与原文一致"
    return EditFileError(
        f"Edit {edit_index}: 这处编辑执行后正文没有任何变化（写入的文本与原文相同{hint}），"
        f"没有计入已应用。请核对 old/new；要换引号写法时，new 里直接写出目标引号。",
        error_type=EDIT_ERROR_NO_CHANGE,
    )


def _exact_spans(content: str, sub: str, limit: int = 3) -> list[tuple[int, int]]:
    """Return up to ``limit`` non-overlapping (start, end) spans of ``sub``."""
    spans: list[tuple[int, int]] = []
    start = 0
    while len(spans) < limit:
        k = content.find(sub, start)
        if k < 0:
            break
        spans.append((k, k + len(sub)))
        start = k + len(sub)
    return spans


_INLINE_WS_CHARS = " \t\u3000"
_INLINE_WS_SPLIT_RE = re.compile(r"[ \t\u3000]+")
_LINE_SPLIT_RE = re.compile(r"\r\n|\r|\n")
_BARE_LF_RE = re.compile(r"(?<!\r)\n")
_CRLF_NOT_FOUND_HINT = "（提示：目标文件使用 Windows CRLF 换行符 \\r\\n；请核对空行数量或上下文锚点。）"


def _is_crlf_content(content: str) -> bool:
    """Whether ``content`` uses Windows CRLF (``\\r\\n``) exclusively (no bare LF)."""
    return isinstance(content, str) and "\r\n" in content and "\n" not in content.replace("\r\n", "")


def _adapt_lf_to_crlf(text: str) -> str:
    """Convert bare LF (``\\n``) to CRLF (``\\r\\n``) without doubling existing ``\\r\\n``."""
    if not isinstance(text, str) or "\n" not in text:
        return text
    return _BARE_LF_RE.sub("\r\n", text)


def _crlf_hint_if_applicable(content: str, pattern: str) -> str:
    """Return a concise CRLF hint when editing a CRLF file with an LF-only pattern fails."""
    if isinstance(content, str) and isinstance(pattern, str) and "\r\n" in content and "\n" in pattern and "\r\n" not in pattern:
        return _CRLF_NOT_FOUND_HINT
    return ""


def _locate_crlf_exact_or_quote_equivalent(
    content: str, pattern: str
) -> tuple[str, str, str, str] | None:
    """Deterministic CRLF-adapted exact/quote-equivalent match for LF patterns on CRLF files."""
    if not pattern or "\r\n" not in content or "\n" not in pattern or "\r\n" in pattern:
        return None
    crlf_pattern = _adapt_lf_to_crlf(pattern)
    located = locate_exact_or_quote_equivalent(content, crlf_pattern)
    if located is None:
        return None
    exact_mode, haystack, needle = located
    return exact_mode, haystack, needle, crlf_pattern
_LINE_SEP_RUN_RE = re.compile(r"(?:\r\n|\r|\n)(?:[ \t\u3000]*(?:\r\n|\r|\n))*")
_NORM_LINE_SEP_PATTERN = r"[ \t\u3000]*(?:\r\n|\r|\n)(?:[ \t\u3000]*(?:\r\n|\r|\n))*[ \t\u3000]*"


def _locate_normalized_exact_spans(
    content: str,
    pattern: str,
    *,
    max_matches: int = 20,
) -> list[tuple[int, int]]:
    """Deterministic whitespace/newline/double-quote-normalized match.

    Allows CRLF/LF and blank-line count differences (``\\n`` vs ``\\n\\n``),
    horizontal whitespace/indentation differences, and double-quote style
    equivalence (via ``fold_double_quotes``), while keeping every other
    character and punctuation mark verbatim, preserving boundary newlines and
    inline whitespace, and NEVER bridging across a line break that ``pattern``
    lacks.
    """
    if not pattern:
        return []
    raw_lines = _LINE_SPLIT_RE.split(pattern)
    non_blank_indices = [
        idx for idx, line in enumerate(raw_lines) if line.strip(_INLINE_WS_CHARS)
    ]
    if not non_blank_indices:
        return []

    first_idx = non_blank_indices[0]
    last_idx = non_blank_indices[-1]

    line_patterns: list[str] = []
    for idx in non_blank_indices:
        stripped_line = raw_lines[idx].strip(_INLINE_WS_CHARS)
        folded_line = fold_double_quotes(stripped_line)
        tokens = [re.escape(tok) for tok in _INLINE_WS_SPLIT_RE.split(folded_line) if tok]
        if not tokens:
            return []
        line_patterns.append(r"[ \t\u3000]+".join(tokens))

    body_pattern = _NORM_LINE_SEP_PATTERN.join(line_patterns)

    if first_idx > 0:
        lead_ws = r"[ \t\u3000]*" if raw_lines[0] else ""
        leading_pattern = (
            lead_ws + r"(?:\r\n|\r|\n)(?:[ \t\u3000]*(?:\r\n|\r|\n))*[ \t\u3000]*"
        )
    elif raw_lines[0][0] in _INLINE_WS_CHARS:
        # Match existing horizontal whitespace (mid-line or line-start), or allow
        # an unindented line start when pattern only added leading indentation.
        leading_pattern = r"(?:[ \t\u3000]+|\A|(?<=[\r\n]))"
    else:
        leading_pattern = ""

    if last_idx < len(raw_lines) - 1:
        trail_ws = r"[ \t\u3000]*" if raw_lines[-1] else ""
        trailing_pattern = (
            r"[ \t\u3000]*(?:\r\n|\r|\n)(?:[ \t\u3000]*(?:\r\n|\r|\n))*" + trail_ws
        )
    elif raw_lines[-1][-1] in _INLINE_WS_CHARS:
        trailing_pattern = r"(?:[ \t\u3000]+|\Z|(?=[\r\n]))"
    else:
        trailing_pattern = ""

    regex = re.compile(leading_pattern + body_pattern + trailing_pattern)
    folded_content = fold_double_quotes(content)

    spans: list[tuple[int, int]] = []
    for match in regex.finditer(folded_content):
        spans.append((match.start(), match.end()))
        if len(spans) >= max_matches:
            break
    return spans


def _follow_span_line_separators(
    content: str, start: int, end: int, old: str, new: str
) -> str:
    """Preserve the file's paragraph separator (e.g. ``\\n\\n`` vs ``\\n``) on normalized/fuzzy matches.

    When the matched original span uses one uniform newline separator (``orig_sep``)
    and ``old`` uses a different uniform separator (``old_sep``), rewrite matching
    ``old_sep`` runs in ``new`` to ``orig_sep`` so a normalized match never silently
    collapses blank lines between paragraphs.
    """
    if not isinstance(new, str) or not new or start >= end:
        return new
    matched = content[start:end]
    orig_seps = {
        _INLINE_WS_SPLIT_RE.sub("", m.group(0)) for m in _LINE_SEP_RUN_RE.finditer(matched)
    }
    old_seps = {
        _INLINE_WS_SPLIT_RE.sub("", m.group(0)) for m in _LINE_SEP_RUN_RE.finditer(old)
    }
    if len(orig_seps) != 1 or len(old_seps) != 1:
        return new
    orig_sep = next(iter(orig_seps))
    old_sep = next(iter(old_seps))
    if orig_sep == old_sep:
        return new
    return _LINE_SEP_RUN_RE.sub(
        lambda m: orig_sep if _INLINE_WS_SPLIT_RE.sub("", m.group(0)) == old_sep else m.group(0),
        new,
    )


def _format_replacement_for_span(
    content: str, start: int, end: int, old_text: str, new_text: str
) -> tuple[str, bool]:
    """Align paragraph separator & indentation of ``new_text`` to the matched span."""
    sep_followed = _follow_span_line_separators(content, start, end, old_text, new_text)
    placed_text = follow_span_indentation(content, start, end, old_text, sep_followed)
    return placed_text, placed_text != sep_followed


def _numbered_previews(content: str, spans: list[tuple[int, int]]) -> list[str]:
    """给候选片段标出 1-based 序号，模型可以直接把序号填进 occurrence。"""
    return [
        f"[occurrence={i}] {preview}"
        for i, preview in enumerate(build_span_previews(content, spans), start=1)
    ]


# 应用内 agent 写正文时才做引号规范化；大纲、角色卡、设定里的引号往往是在
# 引用术语或英文原文，不属于对白体例，保持原样。
NORMALIZED_QUOTE_FILE_TYPES = frozenset({"draft", "script"})


def normalize_ai_body_first_indent(
    content: str, file_type: str | None, *, previous: str | None = None
) -> str:
    """应用内 agent 整章写入小说正文（draft）时，补上漏掉的第一段「　　」缩进。

    剧本（script）的场景标题、动作行、台词按剧本格式顶格，资料文件不是正文，都不动。
    ``previous`` 是被替换的原文：原文第一段本来就这样顶格时不补。
    """
    if file_type != "draft":
        return content
    return indent_unindented_first_paragraph(content, previous=previous or "")


def previous_chapter_content(
    session: Session,
    project_id: str,
    *,
    exclude_file_id: str | None = None,
    parent_id: str | None = None,
    order: int | None = None,
) -> str:
    """同项目「上一章」的正文，供新文件沿用引号体例；取不到返回空串。

    优先取同一父目录里排序在前的最近一章，其次取项目里最近更新的一份正文。
    """
    base = select(File.content).where(
        File.project_id == project_id,
        File.file_type.in_(NORMALIZED_QUOTE_FILE_TYPES),  # type: ignore[attr-defined]
        File.is_deleted.is_(False),  # type: ignore[attr-defined]
        File.content.is_not(None),  # type: ignore[union-attr]
        File.content != "",
    )
    if exclude_file_id:
        base = base.where(File.id != exclude_file_id)
    if order is not None:
        sibling = base.where(File.parent_id == parent_id, File.order < order).order_by(
            File.order.desc()  # type: ignore[attr-defined]
        )
        found = session.exec(sibling.limit(1)).first()
        if found:
            return found
    found = session.exec(
        base.order_by(File.updated_at.desc()).limit(1)  # type: ignore[attr-defined]
    ).first()
    return found or ""


def resolve_write_quote_style(
    session: Session,
    *,
    project_id: str,
    existing_content: str,
    incoming_text: str,
    exclude_file_id: str | None = None,
    parent_id: str | None = None,
    order: int | None = None,
) -> str:
    """AI 写入时用的引号风格：文件已有正文 → 上一章 → 本次写入的文本 → “”。"""
    if has_style_quote_marks(existing_content):
        return detect_quote_style(existing_content)
    reference = previous_chapter_content(
        session,
        project_id,
        exclude_file_id=exclude_file_id,
        parent_id=parent_id,
        order=order,
    )
    return resolve_quote_style(reference, incoming_text)


def stage_pre_ai_write_backup(session: Session, file_id: str, current_content: str) -> bool:
    """AI 覆盖正文之前，把还没进历史的当前正文存成一个系统版本。

    备份规则（何时备份、版本形态、不占额度）只在
    ``FileVersionService.backup_unversioned_content`` 一处定义，恢复历史版本前的
    备份用的也是它。这里只负责 AI 写入路径的失败策略：在调用方的同一个锁和事务
    里开 savepoint，任何失败只回滚 savepoint、记 WARNING、返回 False，不阻断 AI
    写入（沿用「版本失败正文照存」的语义）。edit_file 与整章替换另有更严的规则：
    原稿非空却拿不到撤销锚点（备份或锚点读取失败）时不提交（见 ``FileEditor._commit_ai_content``）。
    """
    try:
        with session.begin_nested():
            backup = FileVersionService().backup_unversioned_content(
                session,
                file_id,
                current_content,
                change_summary=BEFORE_AI_EDIT_SUMMARY,
            )
            return backup is not None
    except Exception as exc:
        logger.warning(
            "Failed to back up unversioned content before AI write; write will continue",
            exc_info=True,
            extra={"file_id": file_id, "error": str(exc)},
        )
        return False


@contextmanager
def written_snapshot_for_commit(
    file_id: str,
    content: str | None,
    *,
    full: bool = False,
    seen_spans: Sequence[tuple[int, int]] | None = None,
    request_context: Any | None = None,
) -> Iterator[None]:
    """包住本请求的提交：提交前就把快照更新成要写下的正文，提交失败再还原。

    PostgreSQL 上提交即放锁，同一文件的下一个并行写入立刻拿到锁、在锁内比对快照；
    提交之后才记快照，它会把这次自己的写入误判成「别人改了」。没有请求上下文时什么也不做。
    """
    if request_context is None:
        yield
        return

    state = None
    try:
        state = request_context.written_snapshot_state(file_id)
        request_context.record_written_snapshot(file_id, content, full=full, seen_spans=seen_spans)
    except Exception as exc:  # pragma: no cover - 防御：辅助信息不能拖垮写入
        logger.debug("Failed to record written snapshot", extra={"file_id": file_id, "error": str(exc)})
    try:
        yield
    except BaseException:
        try:
            request_context.restore_written_snapshot(file_id, state)
        except Exception as exc:  # pragma: no cover
            logger.debug("Failed to restore written snapshot", extra={"file_id": file_id, "error": str(exc)})
        raise


# 正文结构检查只对承载成稿正文的类型生效。
BODY_GUARD_FILE_TYPES = frozenset({"draft", "script"})
# 一段至少这么长、逐字出现两次以上才算重复段落（短句、对白、分隔线重复是正常写法）。
DUPLICATE_PARAGRAPH_MIN_CHARS = 80
CHAPTER_REPLACE_VERSION_SUMMARY = "AI 整章替换"


def _body_paragraphs(text: str) -> list[str]:
    return [line.strip() for line in (text or "").split("\n") if line.strip()]


def _duplicated_paragraphs(text: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for paragraph in _body_paragraphs(text):
        if len(paragraph) >= DUPLICATE_PARAGRAPH_MIN_CHARS:
            counts[paragraph] = counts.get(paragraph, 0) + 1
    return {paragraph: count for paragraph, count in counts.items() if count > 1}


def check_body_edit_guards(old_content: str, new_content: str, edits: Any) -> None:
    """单循环 agent 用 edit_file 改正文时的两道结构检查（逐字比较，不判断语义）。

    - 整章替换：一处 replace 的 old 就是整份原文；或这次只有一处 replace，原文有两段以上而改完
      一段都没留下（old 几乎覆盖全文）。整章替换走 write_chapter（按章号定位、diff 审阅与撤销），
      不经 edit_file。几处各自定位的小改动（逐段润色、统一标点）即使碰到了每一段也不算。
    - 新的重复段落：改完后有一段（≥ DUPLICATE_PARAGRAPH_MIN_CHARS 字）逐字出现两次以上，
      而改之前它没有重复。原文里本来就重复的段落不受影响。
    """
    old_stripped = (old_content or "").strip()
    old_stripped_lf = old_stripped.replace("\r\n", "\n")
    if old_stripped:
        whole = any(
            isinstance(edit, dict)
            and str(edit.get("op") or "replace").strip().lower() == "replace"
            and isinstance(edit.get("old"), str)
            and edit["old"].strip().replace("\r\n", "\n") == old_stripped_lf
            for edit in edits or []
        )
        old_paragraphs = _body_paragraphs(old_content)
        edit_list = [edit for edit in edits or [] if isinstance(edit, dict)]
        single_replace = len(edit_list) == 1 and str(edit_list[0].get("op") or "replace").strip().lower() == "replace"
        if not whole and single_replace and len(old_paragraphs) >= 2:
            kept = set(_body_paragraphs(new_content))
            whole = not any(paragraph in kept for paragraph in old_paragraphs)
        if whole:
            raise EditFileError(
                "edit_file 不能整份替换正文，这次没有改动。整章重写或把作者的稿子换进某一章，"
                "用 write_chapter（按章号定位这一章，下一次回复用 <file> 写完整新正文）；"
                "只改其中几处时用 edit_file 改那几段。",
                error_type=EDIT_ERROR_WHOLE_BODY_REPLACE,
            )
    before = _duplicated_paragraphs(old_content)
    for paragraph, count in _duplicated_paragraphs(new_content).items():
        if paragraph not in before:
            raise EditFileError(
                f"这次修改会让下面这段在正文里出现 {count} 次（逐字重复），没有改动：「{_preview(paragraph)}」。"
                "先用 read_files(ids=[...]) 读一次这一章现在的正文，确认这段已经在哪里，再只改需要改的地方，"
                "不要把已有的段落再插入或追加一遍。",
                error_type=EDIT_ERROR_DUPLICATED_PARAGRAPH,
            )


# 精确匹配（或无锚点的 append/prepend）回显给模型的冗余预览键：
# 模型刚在上一条 function_call.arguments 里发出了同样的原文与新文，再原样回传只白占上下文；
# 前端卡片与撤销仍经 AgentRequestContext UI 侧通道拿到完整 details 与 undo。
_EXACT_MATCH_MODES: frozenset[str] = frozenset({"exact", "exact_quote"})
_EXACT_DETAIL_ECHO_KEYS: frozenset[str] = frozenset(
    {"old_preview", "new_preview", "text_preview", "anchor_preview", "deleted_preview"}
)
_EMPTY_LIST_BOILERPLATE_KEYS: tuple[str, ...] = ("failed_edits", "unchanged_edits", "warnings")
_FALSY_BOOL_BOILERPLATE_KEYS: tuple[str, ...] = ("partial_success", "all_failed")


def _is_exact_detail_item(detail: dict[str, Any]) -> bool:
    match_mode = detail.get("match_mode")
    if isinstance(match_mode, str) and match_mode:
        return match_mode in _EXACT_MATCH_MODES
    return detail.get("op") in ("append", "prepend")


def slim_edit_file_result_for_llm(result: dict[str, Any]) -> dict[str, Any]:
    """为 LLM function_call_output 瘦身 edit_file 的结果字典（不修改原字典）。

    - 省略仅供前端撤销使用的 ``undo``（含 64 位 sha256）。
    - 精确匹配（exact / exact_quote）与 append / prepend 的 detail 省略与调用参数逐字重复的
      ``old_preview`` / ``new_preview`` / ``text_preview`` / ``anchor_preview`` / ``deleted_preview``；
      非精确匹配（fuzzy / approximate / fuzzy_paragraph）完整保留 ``matched_original``、``warning``、
      ``layout_notes`` 与预览等全部诊断字段。
    - 省略为空列表或 False 的样板字段（``failed_edits``、``unchanged_edits``、``warnings``、
      ``partial_success``、``all_failed``）；非空或 True 时原样保留。
    """
    slimmed: dict[str, Any] = {}
    for key, value in result.items():
        if key == "undo":
            continue
        if key in _EMPTY_LIST_BOILERPLATE_KEYS and isinstance(value, list) and not value:
            continue
        if key in _FALSY_BOOL_BOILERPLATE_KEYS and not value:
            continue
        if key == "details" and isinstance(value, list):
            slim_details: list[Any] = []
            for item in value:
                if isinstance(item, dict) and _is_exact_detail_item(item):
                    slim_details.append(
                        {k: v for k, v in item.items() if k not in _EXACT_DETAIL_ECHO_KEYS}
                    )
                else:
                    slim_details.append(item)
            slimmed[key] = slim_details
        else:
            slimmed[key] = value
    return slimmed


def extract_edit_file_ui_payload(result: dict[str, Any]) -> dict[str, Any]:
    """提取 edit_file 结果中仅供 SSE/UI 卡片与撤销使用的字段，写入 AgentRequestContext 侧通道。"""
    overlay: dict[str, Any] = {}
    if isinstance(result.get("undo"), dict):
        overlay["undo"] = result["undo"]
    details = result.get("details")
    if isinstance(details, list) and details:
        overlay["details"] = details
    return overlay


def slim_batch_replace_result_for_llm(result: dict[str, Any]) -> dict[str, Any]:
    """为 LLM function_call_output 瘦身 batch_replace_files 的结果字典（不修改原字典）。

    省略仅供前端卡片撤销使用的 ``files[].undo`` 锚点；前端卡片与落库经
    ``AgentRequestContext`` UI 侧通道还原完整 ``files``（含每文件 ``undo``）。
    """
    slimmed = dict(result)
    files = result.get("files")
    if isinstance(files, list):
        slim_files: list[Any] = []
        for item in files:
            if isinstance(item, dict) and "undo" in item:
                slim_files.append({k: v for k, v in item.items() if k != "undo"})
            else:
                slim_files.append(item)
        slimmed["files"] = slim_files
    return slimmed


def extract_batch_replace_ui_payload(result: dict[str, Any]) -> dict[str, Any]:
    """提取 batch_replace_files 真实修改结果中仅供 SSE/UI 卡片与撤销使用的完整数据。

    即使 LLM 侧载荷触发超限截断甚至最小占位降级，StreamAdapter 仍可通过侧通道完整还原
    每个已修改文件的 ``undo`` 锚点与核心汇总字段。
    """
    files = result.get("files")
    if result.get("dry_run") is True or not isinstance(files, list) or not files:
        return {}
    overlay: dict[str, Any] = {
        "dry_run": False,
        "mutation_applied": bool(result.get("mutation_applied")),
        "matched_files": result.get("matched_files", len(files)),
        "modified_files": result.get("modified_files", len(files)),
        "total_replacements": result.get("total_replacements", 0),
        "files": copy.deepcopy(files),
        "skipped_files": copy.deepcopy(result.get("skipped_files") or []),
        "remaining_files": copy.deepcopy(result.get("remaining_files") or []),
        "remaining_count": result.get("remaining_count", 0),
        "truncated": bool(result.get("truncated")),
    }
    old_text = result.get("old_text")
    new_text = result.get("new_text")
    if isinstance(old_text, str):
        overlay["old_text"] = _preview(old_text)
    if isinstance(new_text, str):
        overlay["new_text"] = _preview(new_text)
    return overlay


def _span_within_seen(start: int, end: int, seen_spans: Sequence[tuple[int, int]]) -> bool:
    return any(seen_start <= start and end <= seen_end for seen_start, seen_end in seen_spans)


def _find_approximate_in_spans(
    content: str,
    pattern: str,
    seen_spans: Sequence[tuple[int, int]],
    *,
    max_error_rate: float = 0.25,
    min_pattern_len: int = 8,
    keep_line_breaks: bool = False,
    stats: dict[str, int] | None = None,
) -> tuple[int, int, float, str] | None:
    """只在已读字符区间内做近似匹配，绝不命中未读段落。"""
    hits: list[tuple[int, int, float, str]] = []
    for seen_start, seen_end in seen_spans:
        sub = content[seen_start:seen_end]
        if not sub:
            continue
        local_stats: dict[str, int] = {}
        hit = find_approximate_match(
            sub,
            pattern,
            max_error_rate=max_error_rate,
            min_pattern_len=min_pattern_len,
            keep_line_breaks=keep_line_breaks,
            stats=local_stats,
        )
        if stats is not None:
            for k, v in local_stats.items():
                stats[k] = stats.get(k, 0) + v
        if hit is not None:
            rel_start, rel_end, similarity, matched_text = hit
            hits.append((seen_start + rel_start, seen_start + rel_end, similarity, matched_text))
    if len(hits) == 1:
        return hits[0]
    return None


def _find_unique_line_in_spans(
    content: str,
    anchor: str,
    seen_spans: Sequence[tuple[int, int]],
    *,
    ignore_punct_whitespace: bool = True,
) -> tuple[int, int, float] | None:
    """只在已读字符区间内做段落兜底匹配，绝不命中未读段落。"""
    hits: list[tuple[int, int, float]] = []
    for seen_start, seen_end in seen_spans:
        sub = content[seen_start:seen_end]
        if not sub:
            continue
        hit = find_unique_line_span(sub, anchor, ignore_punct_whitespace=ignore_punct_whitespace)
        if hit is not None:
            rel_start, rel_end, score = hit
            hits.append((seen_start + rel_start, seen_start + rel_end, score))
    if len(hits) == 1:
        return hits[0]
    return None


class FileEditor:
    """
    Editor for file content with robust text matching.

    This class provides precise editing operations on file content with:
    - Exact, fuzzy, and approximate text matching
    - Support for replace, insert, append, prepend, and delete operations
    - Version history tracking
    - Permission checking
    """

    def __init__(
        self,
        session: Session,
        user_id: str | None = None,
        *,
        request_context: Any | None = None,
    ):
        """
        Initialize file editor.

        Args:
            session: Database session
            user_id: Current user ID (UUID string, for permission checks)
            request_context: Optional request-scoped ``AgentRequestContext``
        """
        self.session = session
        self.user_id = user_id
        self.request_context = request_context

    def edit_file(
        self,
        id: str,
        edits: list[dict[str, Any]],
        continue_on_error: bool = False,
        normalize_quotes: bool = False,
        read_snapshot_fn: Callable[[], str | None] | None = None,
        exact_only: bool = False,
        seen_spans: Sequence[tuple[int, int]] | None = None,
        report_author_edits: bool = False,
        body_guards: bool = False,
    ) -> dict[str, Any]:
        """
        Apply precise edits to a file's content.

        Supports the following edit operations:
        - replace: Find and replace text (old -> new)
        - insert_after: Insert text after an anchor
        - insert_before: Insert text before an anchor
        - append: Add text at the end
        - prepend: Add text at the beginning
        - delete: Remove specified text

        Args:
            id: File ID to edit
            edits: List of edit operations, each containing:
                - op: Operation type
                - old: Original text (for replace/delete)
                - new: New text (for replace)
                - anchor: Anchor text (for insert_after/insert_before)
                - text: Text to insert (for insert_*/append/prepend)
                - replace_all: Whether to replace all occurrences (for replace)
                - occurrence: 1-based index of the occurrence to edit when the
                  old/anchor text matches several places (replace/delete/insert_*)
                - match_mode: "auto"(默认) 或 "exact"（禁用模糊/近似兜底）
                - ignore_punct_whitespace: 模糊匹配时是否忽略标点与空白（默认 true）
            continue_on_error: Whether to continue applying remaining edits when one edit fails
            normalize_quotes: In-app agent writes only. For draft/script files,
                normalize double quotes in each edit's new text (replace new,
                insert/append/prepend text) to the file's quote style; text
                outside the edits is never touched.
            read_snapshot_fn: In-app agent writes only. Returns the content the
                model last saw for this file; called after the locked re-read
                (so an earlier write of the same request is already recorded).
                When it differs, raise StaleContentError instead of editing an
                outdated copy.
            exact_only: In-app agent writes only, for files the model has not
                seen this request: locating ops (replace/delete/insert_*) only
                match the old/anchor text verbatim, whatever match_mode says.
            report_author_edits: In-app agent writes only. Add
                ``author_edits_touched`` when the edit rewrote or removed
                paragraphs the author wrote after the AI (agent/tools/author_changes.py).
            body_guards: Single loop agent only. On draft/script files refuse a
                whole-body replacement and an edit that adds a new verbatim
                duplicated paragraph (check_body_edit_guards); nothing is committed.

        Returns:
            Dict with edit results:
                - id: File ID
                - title: File title
                - edits_applied: Number of successful edits
                - new_length: New content length in characters (punctuation/whitespace included)
                - new_word_count: New word count, same as the editor's count
                - details: List of applied edit details
                - failed_edits: List of failed edit details (when continue_on_error=True)

        Raises:
            EditFileError: File not found (error_type=file_not_found)
            EditBatchError: continue_on_error=False and at least one edit
                failed. Every edit is still checked in memory so the error lists
                all failing indices; nothing is committed (the whole batch is
                rolled back, edits_applied=0).
            PermissionError: If user doesn't have permission
        """
        from database import is_postgres

        # continue_on_error 可能一路从 LLM 参数透传下来（strict_json_schema=False
        # 时布尔会被序列化成 "false"/"0"），朴素真值判断会把它们判真。
        continue_on_error = coerce_bool(continue_on_error, default=False)

        options = {
            "read_snapshot_fn": read_snapshot_fn,
            "exact_only": exact_only,
            "seen_spans": seen_spans,
            "report_author_edits": report_author_edits,
            "body_guards": body_guards,
        }
        if is_postgres:
            return self._edit_file_impl(id, edits, continue_on_error, normalize_quotes, **options)
        # SQLite has no row locks: serialize same-file read-modify-write with
        # the in-process per-file lock so a concurrent edit (tool calls of one
        # turn run as separate tasks on their own session/thread) cannot interleave between
        # our read and commit. 获取方式见 acquire_file_write_lock：事件循环线程
        # 上只做有界等待，避免整个进程陪着一个工作线程的长事务停摆。
        with acquire_file_write_lock(id):
            return self._edit_file_impl(id, edits, continue_on_error, normalize_quotes, **options)

    def _edit_file_impl(
        self,
        id: str,
        edits: list[dict[str, Any]],
        continue_on_error: bool,
        normalize_quotes: bool = False,
        *,
        read_snapshot_fn: Callable[[], str | None] | None = None,
        exact_only: bool = False,
        seen_spans: Sequence[tuple[int, int]] | None = None,
        report_author_edits: bool = False,  # noqa: ARG002 - kept for caller compatibility
        body_guards: bool = False,
    ) -> dict[str, Any]:
        # Get file. On PostgreSQL take a row lock so concurrent edit_file tasks
        # (concurrent tool calls each run on their own session) targeting the SAME file
        # serialize: the second locked SELECT blocks until the first commits and
        # then re-reads the updated content, preventing a lost update where the
        # later commit overwrites the earlier edit. FOR NO KEY UPDATE (not FOR
        # UPDATE) so the version snapshot's FK insert from its independent
        # session (KEY SHARE on this row) is not blocked by the held lock.
        from database import is_postgres

        if is_postgres:
            file = self.session.exec(
                select(File).where(File.id == id).with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            ).first()
        else:
            # The shared per-request session may already hold this File in its
            # identity map from context assembly (minutes before this edit);
            # Session.get would return that cached snapshot without any SQL.
            # Force a re-SELECT so the read-modify-write is based on the
            # current DB content, not on a stale copy that would silently
            # overwrite a concurrent user save.
            file = self.session.get(File, id, populate_existing=True)

        if not file or file.is_deleted:
            # Do not leak internal IDs to end users
            log_with_context(
                logger,
                40,  # ERROR
                "File not found for edit_file",
                file_id=id,
                user_id=self.user_id,
            )
            raise EditFileError(
                "文件不存在或已删除。不要猜测、枚举或改写相似 id；"
                "先用 search_files 不带关键词查询当前文件列表，再从结果逐字复制目标 id。"
                "确认前不要继续写，也不能把失败解释成权限问题。"
                f"{format_malformed_uuid_hint([id])}",
                error_type=EDIT_ERROR_FILE_NOT_FOUND,
            )

        # Check permission (target must belong to the current tool-context project)
        check_file_access_in_tool_context(
            self.session, file, self.user_id, request_context=self.request_context
        )
        already_edited_this_turn = (
            self.request_context is not None
            and hasattr(self.request_context, "has_written_file")
            and bool(self.request_context.has_written_file(file.id))
        )

        old_content = file.content or ""
        original_has_paragraph_stats_marker = _content_has_paragraph_stats_marker(old_content)
        # 锁内重读的正文和模型看到的不一样：作者（或别的写入）在模型读过之后又改了。
        # 不在旧正文上做近似匹配，否则作者刚改的字可能被模型的旧写法覆盖。快照在锁内取：
        # 本请求里先拿到锁的那次写入已经把快照更新成它写下的正文，不会误判成别人改的。
        read_snapshot = read_snapshot_fn() if read_snapshot_fn is not None else None
        if read_snapshot is not None and read_snapshot != old_content:
            raise StaleContentError(
                read_snapshot,
                old_content,
                file_id=file.id,
                title=file.title,
                file_type=file.file_type,
            )
        content = old_content
        active_spans: list[tuple[int, int]] | None = (
            [(int(s), int(e)) for s, e in seen_spans if e > s]
            if exact_only and seen_spans
            else None
        )
        applied_edits = []
        effective_edits: list[Any] = []
        failed_edits: list[dict[str, Any]] = []
        # old 与 new 一字不差、原文里确实有这段：模型要的就是原样，不算失败也不计入已应用。
        unchanged_edits: list[dict[str, Any]] = []
        warnings: list[str] = []

        # 只规范化每个 edit 自己写进去的新文本，不动没被编辑的段落，免得改稿
        # 审阅里冒出与本次修改无关的引号 diff。风格按文件已有正文判定。
        quote_style: str | None = None
        if normalize_quotes and file.file_type in NORMALIZED_QUOTE_FILE_TYPES:
            quote_style = resolve_write_quote_style(
                self.session,
                project_id=file.project_id,
                existing_content=old_content,
                incoming_text=self._edits_incoming_text(edits),
                exclude_file_id=file.id,
                parent_id=file.parent_id,
                order=file.order,
            )

        for i, edit in enumerate(edits):
            try:
                if not isinstance(edit, dict):
                    raise ValueError(f"Edit {i}: invalid edit object, must be JSON object")

                # 剥离模型偶发夹带的 NUL 字节（\x00），避免 PostgreSQL DataError。
                edit = {
                    k: (v.replace("\x00", "") if isinstance(v, str) and "\x00" in v else v)
                    for k, v in edit.items()
                }
                # Normalize common field aliases BEFORE op inference so omitted-op edits
                # using old_text/new_text or from/to still infer op=replace cleanly.
                # Check `is None` so explicit JSON null canonical keys still fall back while
                # preserving `new=""` deletions.
                if edit.get("old") is None:
                    for alias in ("old_text", "from"):
                        if isinstance(edit.get(alias), str):
                            edit["old"] = edit[alias]
                            break
                if edit.get("new") is None:
                    for alias in ("new_text", "to"):
                        if isinstance(edit.get(alias), str):
                            edit["new"] = edit[alias]
                            break
                if edit.get("text") is None and isinstance(edit.get("content"), str):
                    edit["text"] = edit.get("content")

                # Normalize op field (common LLM mistakes: op is null / uses alias keys)
                op_raw = edit.get("op")
                if op_raw is None:
                    op_raw = edit.get("operation") or edit.get("action") or edit.get("type")

                op = op_raw.strip().lower() if isinstance(op_raw, str) else ""
                op = op.replace("-", "_")

                if op == "replace_all":
                    op = "replace"
                    edit["replace_all"] = True

                alias_map = {
                    "insertafter": "insert_after",
                    "after": "insert_after",
                    "insertbefore": "insert_before",
                    "before": "insert_before",
                    "insert": "insert_after",
                    "add_after": "insert_after",
                    "add_before": "insert_before",
                }
                op = alias_map.get(op, op)
                if op in ("none", "null", "nil"):
                    op = ""

                # op 缺失时不按 position 等字样猜编辑类型。只有 old + new、没有 anchor / text 这一种字段
                # 形状只可能是 replace，照 replace 执行并留告警；其余缺 op 的组合报缺 op，让模型带上 op 重发。
                if not op:
                    # Ignore completely empty edits (common trailing null/empty item)
                    if not any(v for v in edit.values() if v not in (None, "", [], {})):
                        warnings.append(f"Edit {i}: empty edit ignored")
                        continue
                    if (
                        isinstance(edit.get("old"), str)
                        and isinstance(edit.get("new"), str)
                        and edit.get("anchor") is None
                        and edit.get("text") is None
                    ):
                        op = "replace"
                        warnings.append(f"Edit {i}: op omitted; old+new applied as op=replace")
                    else:
                        raise ValueError(
                            f"Edit {i}: missing op. Each edit must include op=replace/append/insert_after/insert_before"
                        )

                # Persist normalized op for subsequent logic
                edit["op"] = op
                if (
                    exact_only
                    and active_spans is None
                    and op in ("replace", "delete", "insert_after", "insert_before")
                    and str(edit.get("match_mode") or "auto").strip().lower() != "exact"
                ):
                    # 本请求没读过这个文件：不做模糊去标点或近似错字猜测，但允许确定性的
                    # 空白/换行/引号等价唯一匹配（调用方显式指定 match_mode="exact" 时保持纯逐字）。
                    # 若通过范围读取看过部分区间（active_spans 非空），区间内允许模糊/近似匹配，
                    # 区间外同样只允许逐字或确定性空白/换行/引号匹配（见下方 allowed_fuzzy_spans）。
                    edit["match_mode"] = "exact_normalized"

                if op in ("append", "prepend", "insert_after", "insert_before"):
                    self._require_insert_text(edit, i, op, warnings)

                write_prefixes_stripped = (
                    not original_has_paragraph_stats_marker
                    and self._strip_write_input_paragraph_stats_markers(edit, op, i)
                )

                content_before_edit = content
                applied_before_edit = len(applied_edits)
                warnings_before_edit = len(warnings)
                edit_hunks: list[tuple[int, int, int]] = []
                replace_already_matched: list[bool] = []

                try:
                    content, is_noop = self._run_single_edit_attempt(
                        content_before_edit,
                        edit,
                        i,
                        op,
                        applied_edits,
                        unchanged_edits,
                        warnings,
                        file_id=file.id,
                        quote_style=quote_style,
                        active_spans=active_spans,
                        edit_hunks=edit_hunks,
                        replace_already_matched=replace_already_matched,
                        allow_approx_with_marker_prefix=False,
                    )
                except EditFileError as first_exc:
                    stripped_edit = (
                        self._strip_edit_marker_prefixes(edit, op)
                        if first_exc.error_type == EDIT_ERROR_ANCHOR_NOT_FOUND
                        and op in ("replace", "delete", "insert_after", "insert_before")
                        else None
                    )
                    if stripped_edit is None:
                        raise
                    # Failure-only prefix-strip retry: clean partial state from attempt 1 and retry once.
                    del applied_edits[applied_before_edit:]
                    del warnings[warnings_before_edit:]
                    edit_hunks.clear()
                    replace_already_matched.clear()
                    content, is_noop = self._run_single_edit_attempt(
                        content_before_edit,
                        stripped_edit,
                        i,
                        op,
                        applied_edits,
                        unchanged_edits,
                        warnings,
                        file_id=file.id,
                        quote_style=quote_style,
                        active_spans=active_spans,
                        edit_hunks=edit_hunks,
                        replace_already_matched=replace_already_matched,
                        allow_approx_with_marker_prefix=True,
                    )
                    edit = stripped_edit
                    for detail in applied_edits[applied_before_edit:]:
                        if isinstance(detail, dict):
                            detail["paragraph_prefixes_stripped"] = True
                if write_prefixes_stripped:
                    for detail in applied_edits[applied_before_edit:]:
                        if isinstance(detail, dict):
                            detail["paragraph_prefixes_stripped"] = True
                if is_noop:
                    continue

                if content == content_before_edit and len(applied_edits) > applied_before_edit:
                    # 报告成功就必须真的改了：撤回这一条的 applied 记录。
                    del applied_edits[applied_before_edit:]
                    if op == "replace" and replace_already_matched:
                        # 命中的原文区间在引号规范化前就已等于 new_text（例如记忆陈旧但目标改动已存在）：
                        # 记入 unchanged_edits，不报 no_change 错误，也不回滚同批其他改动。
                        unchanged_edits.append({"index": i, "op": op, "reason": "already_matches"})
                        warnings.append(f"Edit {i}: 原文此处已是目标内容，没有修改，不用重发")
                        effective_edits.append(edit)
                        continue
                    raise _no_change_error(i, edit, quote_style)
                effective_edits.append(edit)
                if active_spans is not None and edit_hunks:
                    from agent.tools.tool_context import shift_spans_for_hunks

                    active_spans = shift_spans_for_hunks(active_spans, edit_hunks, new_len=len(content))
            except Exception as e:
                failed_op = str(edit.get("op", "")).strip() if isinstance(edit, dict) else ""
                error_text = str(e)
                if not error_text.startswith(f"Edit {i}"):
                    error_text = f"Edit {i}: {error_text}"
                error_type = _classify_edit_exception(e)
                batch_anchor_invalidated = (
                    error_type == EDIT_ERROR_ANCHOR_NOT_FOUND
                    and bool(applied_edits)
                    and isinstance(edit, dict)
                    and _batch_anchor_invalidated(old_content, content, edit)
                )
                if batch_anchor_invalidated:
                    error_text += (
                        "（前面的 edit 已改掉此项基于批次开始原文的 old/anchor；"
                        "同一文件的多处独立编辑请合并进一次 edit_file 的 edits 数组并避免前后项重叠；"
                        "依赖前项的改动请基于前一项后的文本重新定位，或拆成单独调用并先读取当前正文，不要忽略前项后强行匹配。）"
                    )
                elif already_edited_this_turn and error_type == EDIT_ERROR_ANCHOR_NOT_FOUND:
                    error_text += (
                        "（提示：本轮此前已修改过该文件，请把对同一文件的多处编辑合并进一次 edit_file 的 edits 数组，"
                        "避免拆成多次或并列调用导致旧锚点失效。）"
                    )
                if exact_only and failed_op in ("replace", "delete", "insert_after", "insert_before"):
                    if active_spans is None:
                        error_text += "（本请求还没读过这个文件，只按逐字匹配：先用 read_files(ids=[...]) 读一次，再照现在的正文改）"
                    elif error_type == EDIT_ERROR_ANCHOR_NOT_FOUND:
                        error_text += "（本请求只按范围读过该文件部分内容，未读区域只按逐字匹配：请先用 read_files(ids=[...]) 读取目标区域或全文后再改）"
                failed_edit: dict[str, Any] = {
                    "index": i,
                    "error": error_text,
                    "error_type": error_type,
                    "user_message": edit_error_user_message(error_type),
                    "op": failed_op,
                }
                if batch_anchor_invalidated:
                    failed_edit["batch_anchor_invalidated"] = True
                failed_edits.append(failed_edit)
                if continue_on_error:
                    warnings.append(f"Edit {i}: failed and skipped ({error_type})")
                # continue_on_error=false 时也继续检查后面的编辑（只在内存里），
                # 一次把所有失败项报给模型；循环结束后整批回滚，不会落库。

        if failed_edits and not continue_on_error:
            # 整批不生效：不提交、不建版本。错误里写明「全部未生效」并列出全部
            # 失败项，否则模型会以为只有报错那一条失败，只重发剩下的几条。
            raise EditBatchError(
                failed_edits,
                edits_total=len(edits),
                file_id=file.id,
                title=file.title,
                file_type=file.file_type,
            )

        if content != old_content and body_guards and file.file_type in BODY_GUARD_FILE_TYPES:
            try:
                check_body_edit_guards(old_content, content, effective_edits)
            except EditFileError as exc:
                raise exc.attach_file_meta(
                    file_id=file.id,
                    title=file.title,
                    file_type=file.file_type,
                ) from None

        undo: dict[str, Any] | None = None
        if content != old_content:
            undo = self._commit_ai_content(
                file,
                old_content,
                content,
                create_version=lambda: self._create_edit_version(id, content, applied_edits),
                tool="edit_file",
                seen_spans=active_spans,
            )

        # 统一补齐 new_preview：replace 类操作的 detail 里叫 new_preview，
        # append/prepend/insert_* 只有 text_preview，前端与 SSE 适配器要两处兼容
        # 才能显示"这次写进去的新内容"。在源头补一份别名，消费方只认 new_preview 即可。
        self._backfill_new_preview(applied_edits)

        result = {
            "id": file.id,
            "mutation_applied": content != old_content,
            "title": file.title,
            "file_type": file.file_type,
            "edits_applied": len(applied_edits),
            "new_length": len(content),
            "new_word_count": count_words(content),
            "details": applied_edits,
            "failed_edits": failed_edits,
            "unchanged_edits": unchanged_edits,
            "partial_success": bool(applied_edits and failed_edits),
            "all_failed": bool(failed_edits and not applied_edits),
            "warnings": warnings,
        }
        if undo is not None:
            result["undo"] = undo
        return result

    def _commit_ai_content(
        self,
        file: File,
        old_content: str,
        content: str,
        *,
        create_version: Callable[[], None],
        tool: str,
        full_snapshot: bool = False,
        new_title: str | None = None,
        seen_spans: Sequence[tuple[int, int]] | None = None,
    ) -> dict[str, Any] | None:
        """在持有这个文件写锁时提交一次 AI 写入，返回撤销锚点（拿不到时为 None）。

        edit_file 与 write_chapter 的整章替换共用这一条路径：先备份还没进历史的原稿，
        撤销锚点落在与原稿逐字相同的历史版本上，正文与 AI 版本在同一事务里提交。

        原稿非空却没有撤销锚点，说明备份或锚点读取失败、原稿不在任何历史版本里：
        回滚、不写入，抛 PreWriteHistoryUnavailableError（局部修改可能删掉不在最近保存附近的
        作者文字，没有历史就无处恢复）。空白原稿没有可丢的字，照常写入。
        """
        file_id = file.id
        # Stage content and snapshot in the same transaction while the per-file
        # lock is held. A savepoint keeps snapshot failures non-blocking without
        # allowing content and history to describe different writes.
        # 原稿可能是作者手动改过、还没进历史的正文：先备份（独立 savepoint），
        # 撤销锚点随后就能落在这份备份上；拿不到锚点时下面拒绝写入。
        stage_pre_ai_write_backup(self.session, file_id, old_content)
        before_version_number: int | None = None
        try:
            # Provenance is optional, but its read failure must not poison
            # the outer PostgreSQL content transaction. A loaded history
            # head is usable only if it exactly reconstructs the live input.
            with self.session.begin_nested():
                version_service = FileVersionService()
                latest = version_service.get_latest_version(self.session, file_id)
                if latest is None and not old_content.strip():
                    # 备份规则不存空白正文，这里只给「真要被改、没有历史」的空白文件补一个
                    # 基线，撤销才有落点。有了版本就不会再补；新建未改的文件不经过这里。
                    latest = version_service.create_version(
                        session=self.session,
                        file_id=file_id,
                        new_content=old_content,
                        change_type=CHANGE_TYPE_EDIT,
                        change_source=CHANGE_SOURCE_SYSTEM,
                        change_summary=BEFORE_AI_EDIT_SUMMARY,
                        force_base=True,
                        skip_quota=True,
                        commit=False,
                    )
                if latest and version_service._get_contents_for_versions(self.session, {file_id: latest})[file_id] == old_content:
                    before_version_number = latest.version_number
        except Exception:
            logger.warning("Unable to anchor edit undo", exc_info=True, extra={"file_id": file_id})
        if old_content.strip() and before_version_number is None:
            # 非空原稿备份后必然等于历史头；拿不到锚点 = 备份或读取失败，原稿无处恢复。
            self.session.rollback()
            logger.warning(
                "Refused AI write: original body is not in history",
                extra={"file_id": file_id, "tool": tool},
            )
            raise PreWriteHistoryUnavailableError(
                file_id,
                action=tool,
                title=file.title,
                file_type=file.file_type,
            )

        if new_title is not None and new_title != file.title:
            apply_file_title_and_order(file, new_title)
        file.content = content
        file.updated_at = advance_timestamp(file.updated_at, now=utcnow())
        # Commit releases the PostgreSQL lock; a subsequent refresh may
        # observe another writer. Undo must retain this edit's own token.
        after_updated_at = normalize_datetime_to_utc(file.updated_at).isoformat()
        try:
            with self.session.begin_nested():
                create_version()
        except Exception as exc:
            logger.warning(
                f"Failed to create version for {tool}; content will persist",
                exc_info=True,
                extra={"file_id": file_id, "error": str(exc)},
            )
        with written_snapshot_for_commit(
            file_id,
            content,
            full=full_snapshot,
            seen_spans=seen_spans,
            request_context=self.request_context,
        ):
            self.session.commit()
        self.session.refresh(file)
        activation_event_service.record_ai_write_accepted(
            self.session,
            user_id=self.user_id,
            project_id=file.project_id,
            file_id=file.id,
            file_type=file.file_type,
            tool=tool,
        )
        if before_version_number is None:
            return None
        return {
            "before_version_number": before_version_number,
            "expected_after_updated_at": after_updated_at,
            # 这次写入后的正文摘要：后面那次 AI 写入被撤销、正文又逐字回到这里时，
            # 这张卡的撤销仍能通过（版本令牌已经变了）。
            "expected_after_content_sha256": content_sha256(content),
        }

    def replace_body(
        self,
        id: str,
        content: str,
        *,
        expected_updated_at: str | None,
        title: str | None = None,
        normalize_quotes: bool = False,
    ) -> dict[str, Any]:
        """write_chapter 的整章替换：用流式写完的新正文整份替换这一章，走 edit_file 的提交路径。

        与 edit_file 一样在文件写锁内重读、核对权限，提交时备份原稿、建 AI 版本并给出撤销锚点
        （``undo``）。``expected_updated_at`` 是工具定位这一章时读到的版本：之后有任何写入
        （作者在编辑器里存过、别的写入）就拒绝（ConcurrentFileWriteError），不覆盖。
        ``title`` 非空时在同一写事务内一并更新章节标题与序号排序。
        返回里带原稿与新稿，供前端与撤销元数据使用；不进模型的工具结果。
        """
        from database import is_postgres
        from models.file_model import validate_file_title

        if "\x00" in content:
            content = content.replace("\x00", "")
        cleaned_title: str | None = None
        if isinstance(title, str):
            candidate = title.replace("\x00", "").strip()
            if candidate:
                validate_file_title(candidate)
                cleaned_title = candidate
        if is_postgres:
            return self._replace_body_impl(
                id, content, expected_updated_at, cleaned_title, normalize_quotes
            )
        with acquire_file_write_lock(id):
            return self._replace_body_impl(
                id, content, expected_updated_at, cleaned_title, normalize_quotes
            )

    def _replace_body_impl(
        self,
        id: str,
        content: str,
        expected_updated_at: str | None,
        title: str | None,
        normalize_quotes: bool,
    ) -> dict[str, Any]:
        from agent.tools.file_ops.crud import ConcurrentFileWriteError
        from database import is_postgres

        if is_postgres:
            file = self.session.exec(
                select(File).where(File.id == id).with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            ).first()
        else:
            file = self.session.get(File, id, populate_existing=True)
        if not file or file.is_deleted:
            raise EditFileError("文件不存在或已删除。", error_type=EDIT_ERROR_FILE_NOT_FOUND)
        check_file_access_in_tool_context(
            self.session, file, self.user_id, request_context=self.request_context
        )

        if expected_updated_at is not None:
            current_updated_at = file.updated_at.isoformat() if file.updated_at else None
            if current_updated_at != expected_updated_at:
                raise ConcurrentFileWriteError("文件在流式写入期间已被修改，本次整章内容未保存")
        old_content = file.content or ""
        previous_title = file.title or ""
        prospective_title = title if title is not None else previous_title
        if prospective_title != previous_title and has_sibling_title_conflict(
            self.session,
            project_id=file.project_id,
            parent_id=file.parent_id,
            title=prospective_title,
            exclude_file_id=file.id,
        ):
            prospective_title = previous_title
        title_changed = prospective_title != previous_title
        if normalize_quotes and content and file.file_type in NORMALIZED_QUOTE_FILE_TYPES:
            from utils.cjk_quotes import normalize_double_quotes

            content = normalize_double_quotes(
                content,
                resolve_write_quote_style(
                    self.session,
                    project_id=file.project_id,
                    existing_content=old_content,
                    incoming_text=content,
                    exclude_file_id=file.id,
                    parent_id=file.parent_id,
                    order=file.order,
                ),
            )
        if normalize_quotes and content:
            content = normalize_ai_body_first_indent(content, file.file_type, previous=old_content)

        content_changed = content != old_content
        undo: dict[str, Any] | None = None
        if content_changed:
            undo = self._commit_ai_content(
                file,
                old_content,
                content,
                create_version=lambda: self._create_ai_version(file.id, content, CHAPTER_REPLACE_VERSION_SUMMARY),
                tool="write_chapter",
                full_snapshot=True,
                new_title=prospective_title if title_changed else None,
            )
            if title_changed and previous_title and undo is not None:
                undo["before_title"] = previous_title
        elif title_changed:
            apply_file_title_and_order(file, prospective_title)
            file.updated_at = advance_timestamp(file.updated_at, now=utcnow())
            with written_snapshot_for_commit(
                file.id, content, full=True, request_context=self.request_context
            ):
                self.session.commit()
            self.session.refresh(file)
        result: dict[str, Any] = {
            "id": file.id,
            "title": file.title,
            "file_type": file.file_type,
            "mutation_applied": content_changed or title_changed,
            "previous_length": len(old_content),
            "previous_word_count": count_words(old_content),
            "new_length": len(content),
            "new_word_count": count_words(content),
            "original_content": old_content,
            "new_content": content,
            "updated_at": normalize_datetime_to_utc(file.updated_at).isoformat() if file.updated_at else None,
        }
        if undo is not None:
            result["undo"] = undo
        return result

    def batch_replace_files(
        self,
        *,
        project_id: str,
        old_text: str,
        new_text: str,
        file_ids: Sequence[str] | None = None,
        file_type: str | None = None,
        file_types: Sequence[str] | None = None,
        dry_run: bool = False,
        max_files: int = BATCH_REPLACE_MAX_FILES,
        body_guards: bool = False,
        read_snapshot_lookup: Callable[[str], str | None] | None = None,
        pending_file_ids: Collection[str] | None = None,
        exclude_file_ids: Collection[str] | None = None,
    ) -> dict[str, Any]:
        """Deterministic literal exact-match batch replace across project files.

        - Strictly scoped to ``project_id``; excludes soft-deleted files and folders.
        - Validates ownership via ``check_project_ownership`` and per-file
          ``check_file_access_in_tool_context``.
        - Zero fuzzy matching; only exact literal substring replacement.
        - Caps modifications to ``max_files`` (default 30) in deterministic chapter/order
          sequence and reports ``remaining_files`` when more files match.
        - When ``dry_run=False``, every modified file commits through
          ``FileEditor._commit_ai_content`` (write lock, pre-write backup, AI FileVersion,
          ``body_guards`` for draft/script, and per-file ``undo`` anchor). Per-file failures
          are isolated cleanly into ``skipped_files`` without corrupting sibling files.
        """
        if not isinstance(old_text, str) or not old_text:
            raise EditFileError("batch_replace_files 的 old_text 不能为空。", error_type=EDIT_ERROR_INVALID_EDIT)
        if not isinstance(new_text, str):
            raise EditFileError("batch_replace_files 的 new_text 必须是字符串。", error_type=EDIT_ERROR_INVALID_EDIT)
        if old_text == new_text:
            raise EditFileError("batch_replace_files 的 old_text 与 new_text 相同，无需替换。", error_type=EDIT_ERROR_INVALID_EDIT)
        normalized_file_types: list[str] = []
        raw_type_inputs: list[str] = []
        if file_type:
            raw_type_inputs.append(file_type)
        if file_types:
            raw_type_inputs.extend(file_types)
        for raw_ft in raw_type_inputs:
            if not isinstance(raw_ft, str):
                continue
            cleaned_ft = raw_ft.strip().lower()
            if not cleaned_ft:
                continue
            if cleaned_ft == "folder":
                raise EditFileError("batch_replace_files 不支持 folder 类型。", error_type=EDIT_ERROR_INVALID_EDIT)
            if cleaned_ft not in normalized_file_types:
                normalized_file_types.append(cleaned_ft)

        check_project_ownership(self.session, project_id, self.user_id)

        crlf_old_text = (
            _adapt_lf_to_crlf(old_text) if ("\n" in old_text and "\r\n" not in old_text) else None
        )
        skipped_files: list[dict[str, Any]] = []
        requested_ids: list[str] = []
        if file_ids is not None:
            requested_ids = list(dict.fromkeys(fid.strip() for fid in file_ids if isinstance(fid, str) and fid.strip()))
            stmt = select(File).where(
                File.id.in_(requested_ids),  # type: ignore[attr-defined]
                File.project_id == project_id,
                File.is_deleted.is_(False),
                File.file_type != "folder",
            )
            if normalized_file_types:
                stmt = stmt.where(File.file_type.in_(normalized_file_types))  # type: ignore[attr-defined]
            candidates = list(self.session.exec(stmt.execution_options(populate_existing=True)).all())
            by_id = {f.id: f for f in candidates}
            missing_ids = [fid for fid in requested_ids if fid not in by_id]
            if missing_ids and not candidates:
                raise EditFileError(
                    "未找到属于当前项目的有效文件（文件不存在、已删除、为文件夹或不属于当前项目）。"
                    f"{format_malformed_uuid_hint(missing_ids)}",
                    error_type=EDIT_ERROR_FILE_NOT_FOUND,
                )
            for missing_id in missing_ids:
                skipped_files.append(
                    {
                        "id": missing_id,
                        "reason": EDIT_ERROR_FILE_NOT_FOUND,
                        "user_message": edit_error_user_message(EDIT_ERROR_FILE_NOT_FOUND),
                        "error": (
                            "文件不存在、已删除、为文件夹或不属于当前项目。"
                            f"{format_malformed_uuid_hint([missing_id])}"
                        ),
                    }
                )
            ordered_candidates = [by_id[fid] for fid in requested_ids if fid in by_id]
        else:
            content_filter = (
                or_(
                    File.content.contains(old_text, autoescape=True),  # type: ignore[attr-defined]
                    File.content.contains(crlf_old_text, autoescape=True),  # type: ignore[attr-defined]
                )
                if crlf_old_text is not None
                else File.content.contains(old_text, autoescape=True)  # type: ignore[attr-defined]
            )
            stmt = (
                select(File)
                .where(
                    File.project_id == project_id,
                    File.is_deleted.is_(False),
                    File.file_type != "folder",
                    content_filter,
                )
                .order_by(
                    File.order.asc(),  # type: ignore[attr-defined]
                    col(File.created_at).asc(),
                    col(File.id).asc(),
                )
            )
            if normalized_file_types:
                stmt = stmt.where(File.file_type.in_(normalized_file_types))  # type: ignore[attr-defined]
            excluded_ids = [str(fid) for fid in (exclude_file_ids or ()) if fid]
            if excluded_ids:
                stmt = stmt.where(File.id.notin_(excluded_ids))  # type: ignore[attr-defined]
            ordered_candidates = list(self.session.exec(stmt.execution_options(populate_existing=True)).all())

        # Exact effective match filter in Python:
        # - SQLite LIKE is ASCII case-insensitive; support CRLF fallback for LF-only old_text.
        # - Strip leaked `[Pxx · N字] ` markers from `new_text` BEFORE CRLF adaptation unless the
        #   file already contains them; reject marker-only inputs on clean files instead of deleting.
        # - When `effective_old in effective_new`, skip occurrences already inside `effective_new`
        #   so already-updated files are excluded instead of starving remaining chapters.
        stripped_new_text, has_marker_in_new = _strip_paragraph_stats_marker_prefixes(new_text)
        raw_overlap_pattern = _compile_overlapping_batch_pattern(old_text, new_text)
        stripped_overlap_pattern = (
            _compile_overlapping_batch_pattern(old_text, stripped_new_text)
            if has_marker_in_new
            else raw_overlap_pattern
        )
        matching_entries: list[tuple[File, list[tuple[int, int]], str, bool]] = []
        for candidate in ordered_candidates:
            cand_content = candidate.content
            if not isinstance(cand_content, str) or (
                old_text not in cand_content
                and (crlf_old_text is None or crlf_old_text not in cand_content)
            ):
                continue
            effective_old, effective_new, marker_stripped = _resolve_batch_replace_texts(
                cand_content,
                old_text,
                new_text,
                crlf_old_text=crlf_old_text,
                stripped_new_text=stripped_new_text,
                has_marker_in_new=has_marker_in_new,
            )
            base_expected_new = stripped_new_text if marker_stripped else new_text
            if effective_old == old_text and effective_new == base_expected_new:
                overlap_pattern = stripped_overlap_pattern if marker_stripped else raw_overlap_pattern
            else:
                # Never reuse LF-compiled overlap patterns when either side was CRLF-adapted.
                overlap_pattern = None
            spans = _find_batch_replace_spans(
                cand_content,
                effective_old,
                effective_new,
                overlap_pattern=overlap_pattern,
            )
            if spans:
                matching_entries.append((candidate, spans, effective_old, marker_stripped))
        matched_total = len(matching_entries)
        effective_max = max(1, int(max_files))
        batch_entries = matching_entries[:effective_max]
        overflow_entries = matching_entries[effective_max:]
        remaining_count = len(overflow_entries)
        remaining_files = [
            {"id": f.id, "title": f.title, "file_type": f.file_type}
            for f, _, _, _ in overflow_entries[:BATCH_REPLACE_MAX_REMAINING_FILES]
        ]

        if dry_run:
            preview_files: list[dict[str, Any]] = []
            total_replacements = 0
            for candidate, spans, effective_old, _ in batch_entries:
                hit_count = len(spans)
                match_items = format_match_snippets(
                    candidate.content or "",
                    [start for start, _ in spans],
                    effective_old,
                    max_matches=BATCH_REPLACE_PREVIEW_SNIPPETS,
                    context_chars=BATCH_REPLACE_SNIPPET_CONTEXT_CHARS,
                )
                total_replacements += hit_count
                preview_files.append(
                    {
                        "id": candidate.id,
                        "title": candidate.title,
                        "file_type": candidate.file_type,
                        "replacements": hit_count,
                        "sample_snippets": match_items,
                    }
                )
            reported_dry_new_text = (
                stripped_new_text
                if has_marker_in_new and batch_entries and all(flag for _, _, _, flag in batch_entries)
                else new_text
            )
            return {
                "dry_run": True,
                "old_text": old_text,
                "new_text": reported_dry_new_text,
                "mutation_applied": False,
                "matched_files": matched_total,
                "modified_files": 0,
                "total_replacements": total_replacements,
                "files": preview_files,
                "skipped_files": skipped_files,
                "remaining_files": remaining_files,
                "remaining_count": remaining_count,
                "truncated": remaining_count > 0,
            }

        pending_set = set(pending_file_ids) if pending_file_ids else set()
        # Snapshot plain (id, title, file_type) tuples before commit/rollback expires ORM instances.
        target_specs = [
            (candidate.id, candidate.title, candidate.file_type) for candidate, _, _, _ in batch_entries
        ]
        modified_files: list[dict[str, Any]] = []
        modified_marker_stripped_flags: list[bool] = []
        total_replacements = 0
        for target_id, target_title, target_file_type in target_specs:
            if target_id in pending_set:
                skipped_files.append(
                    {
                        "id": target_id,
                        "title": target_title,
                        "file_type": target_file_type,
                        "reason": EDIT_ERROR_CHAPTER_REPLACE_PENDING,
                        "user_message": edit_error_user_message(EDIT_ERROR_CHAPTER_REPLACE_PENDING),
                        "error": "该文件正在等待 <file> 正文写入，已跳过。",
                    }
                )
                continue
            try:
                entry = self._batch_replace_one_file(
                    file_id=target_id,
                    old_text=old_text,
                    new_text=new_text,
                    body_guards=body_guards,
                    read_snapshot_lookup=read_snapshot_lookup,
                )
                if entry is not None:
                    modified_marker_stripped_flags.append(bool(entry.pop("_marker_stripped", False)))
                    modified_files.append(entry)
                    total_replacements += int(entry.get("replacements") or 0)
            except (
                EditFileError,
                FileWriteBusyError,
                PreWriteHistoryUnavailableError,
                NotFoundError,
                ForbiddenError,
            ) as exc:
                self.session.rollback()
                if isinstance(exc, EditFileError):
                    reason = exc.error_type
                elif isinstance(exc, FileWriteBusyError):
                    reason = EDIT_ERROR_FILE_BUSY
                elif isinstance(exc, PreWriteHistoryUnavailableError):
                    reason = exc.error_type
                elif isinstance(exc, NotFoundError):
                    reason = EDIT_ERROR_FILE_NOT_FOUND
                else:
                    reason = EDIT_ERROR_PERMISSION_DENIED
                skipped_files.append(
                    {
                        "id": target_id,
                        "title": target_title,
                        "file_type": target_file_type,
                        "reason": reason,
                        "user_message": edit_error_user_message(reason),
                        "error": str(exc),
                    }
                )
            except Exception as exc:
                try:
                    self.session.rollback()
                except Exception:
                    logger.warning(
                        "Failed to roll back session after batch_replace_files per-file error",
                        exc_info=True,
                        extra={"project_id": project_id, "file_id": target_id},
                    )
                logger.error(
                    "Unexpected error during batch_replace_files per-file commit",
                    exc_info=True,
                    extra={
                        "project_id": project_id,
                        "file_id": target_id,
                        "error_type": type(exc).__name__,
                        "already_committed_count": len(modified_files),
                    },
                )
                skipped_files.append(
                    {
                        "id": target_id,
                        "title": target_title,
                        "file_type": target_file_type,
                        "reason": "unexpected_error",
                        "user_message": edit_error_user_message(None),
                        "error": str(exc) or type(exc).__name__,
                    }
                )

        reported_new_text = (
            stripped_new_text
            if has_marker_in_new and modified_marker_stripped_flags and all(modified_marker_stripped_flags)
            else new_text
        )
        return {
            "dry_run": False,
            "old_text": old_text,
            "new_text": reported_new_text,
            "mutation_applied": bool(modified_files),
            "matched_files": matched_total,
            "modified_files": len(modified_files),
            "total_replacements": total_replacements,
            "files": modified_files,
            "skipped_files": skipped_files,
            "remaining_files": remaining_files,
            "remaining_count": remaining_count,
            "truncated": remaining_count > 0,
        }

    def _batch_replace_one_file(
        self,
        *,
        file_id: str,
        old_text: str,
        new_text: str,
        body_guards: bool,
        read_snapshot_lookup: Callable[[str], str | None] | None,
    ) -> dict[str, Any] | None:
        from database import is_postgres

        def _apply_locked() -> dict[str, Any] | None:
            if is_postgres:
                file = self.session.exec(
                    select(File)
                    .where(File.id == file_id)
                    .with_for_update(key_share=True)
                    .execution_options(populate_existing=True)
                ).first()
            else:
                file = self.session.get(File, file_id, populate_existing=True)
            if not file or file.is_deleted or file.file_type == "folder":
                raise EditFileError("文件不存在或已删除。", error_type=EDIT_ERROR_FILE_NOT_FOUND)
            check_file_access_in_tool_context(
                self.session, file, self.user_id, request_context=self.request_context
            )
            old_content = file.content or ""
            if read_snapshot_lookup is not None:
                snap = read_snapshot_lookup(file.id)
                if snap is not None and snap != old_content:
                    raise StaleContentError(snap, old_content)
            effective_old, effective_new, marker_stripped = _resolve_batch_replace_texts(
                old_content, old_text, new_text
            )
            spans = _find_batch_replace_spans(old_content, effective_old, effective_new)
            replacements = len(spans)
            if replacements <= 0:
                return None
            new_content = _apply_batch_replace_spans(old_content, spans, effective_new)
            if new_content == old_content:
                return None
            if body_guards and file.file_type in BODY_GUARD_FILE_TYPES:
                # Multi-occurrence replace_all (e.g. protagonist rename or punctuation fix across every paragraph)
                # represents multiple local edits, not a single whole-body rewrite; keep exact whole-body blocking,
                # single-occurrence near-whole-body blocking, and duplicated-paragraph checks intact.
                guard_edits = [
                    {"op": "replace", "old": effective_old, "new": effective_new, "replace_all": True}
                ] * min(replacements, 2)
                check_body_edit_guards(old_content, new_content, guard_edits)
            summary = f"AI 批量替换: {old_text[:20]} → {effective_new[:20]} ({replacements} 处)"
            undo = self._commit_ai_content(
                file,
                old_content,
                new_content,
                create_version=lambda: self._create_ai_version(file.id, new_content, summary),
                tool="batch_replace_files",
                full_snapshot=False,
            )
            entry: dict[str, Any] = {
                "id": file.id,
                "title": file.title,
                "file_type": file.file_type,
                "replacements": replacements,
                "new_length": len(new_content),
                "new_word_count": count_words(new_content),
                "_marker_stripped": marker_stripped,
            }
            if undo is not None:
                entry["undo"] = undo
            return entry

        if is_postgres:
            return _apply_locked()
        with acquire_file_write_lock(file_id):
            return _apply_locked()

    @staticmethod
    def _normalize_placed_spans(content: str, spans: list[tuple[int, int]], quote_style: str | None) -> str:
        """规范已经落到 content 里的新文本（spans）的双引号；不需要时原样返回。

        开/闭方向看新文本所在的整行：片段从对白中间开始时（行内它前面的引号是奇数个），
        第一个引号就是闭引号。只改 spans 内的字符，逐字一换一，偏移不变。
        """
        if quote_style is None or not spans:
            return content
        return normalize_inserted_spans(content, spans, quote_style)

    @classmethod
    def _place_new_text(
        cls, content: str, start: int, end: int, text: Any, quote_style: str | None
    ) -> tuple[str, Any]:
        """content[start:end] 换成 text 并按上下文规范它的引号；返回 (新 content, 实际写入的 text)。"""
        content = content[:start] + text + content[end:]
        if quote_style is None or not isinstance(text, str) or not text:
            return content, text
        content = cls._normalize_placed_spans(content, [(start, start + len(text))], quote_style)
        return content, content[start : start + len(text)]

    @staticmethod
    def _position_insert(
        content: str,
        pos: int,
        text: Any,
        *,
        direction: str,
        snap_sentence: bool,
        notes: list[str],
        exact_anchor: bool = False,
    ) -> tuple[int, Any]:
        """插入前把位置和分隔对齐到句子/段落边界；返回 (插入位置, 实际写入的文本)。

        - 一整句话（见 is_sentence_shaped）不能落在句子中间：insert_after 往后
          挪到这句话结束，insert_before 往前挪到这句话开头。分句（以逗号等开头）
          原样贴着锚点。
        - ``exact_anchor``：锚点是逐字命中的，模型看得见它的边缘。边缘是标点
          （引号、冒号、破折号……）时原样插入，只在字和它的标点、或半句话的两半
          之间才挪（exact_anchor_snap_forward）。模糊/近似窗口的边缘是猜的，照常挪。
        - 段落边界上的整段文字用文件自己的分段符和缩进隔开（paragraph_layout）。
        调整过的地方写进 ``notes``，调用方放进 detail 的 layout_notes。
        """
        if not isinstance(text, str) or not text:
            return pos, text
        if snap_sentence and is_sentence_shaped(text) and not is_sentence_boundary(content, pos):
            forward: bool | None = direction == "after"
            if exact_anchor:
                forward = exact_anchor_snap_forward(content, pos, direction=direction)
            if forward is not None:
                pos = snap_to_sentence_boundary(content, pos, forward=forward)
                notes.append(NOTE_SENTENCE_SNAP)
        pos, text, layout_notes = layout_insertion(content, pos, text, direction=direction)
        notes.extend(layout_notes)
        return pos, text

    @staticmethod
    def _check_approximate_edges(
        content: str,
        start: int,
        end: int,
        old_text: str,
        edit_index: int,
        *,
        require_sentence_boundaries: bool = False,
    ) -> None:
        """近似命中的范围不能在句子中间开始或结束。

        old 的边缘是标点（以句号收尾、以引号开头），命中范围扩展后的边缘却是
        一个字，说明窗口停在了句子中间；照样替换/删除会留下半句话或吞掉半句。
        破坏性的 delete（``require_sentence_boundaries=True``）无论 old 边缘是否带标点，
        都要求命中起止落在句子/行边界上，避免窗口收缩跳过主语或残句留下孤立碎片。
        """
        stripped = old_text.strip()
        if not stripped or start >= end:
            return
        if (
            (
                _edge_punct_class(stripped[-1]) is not None
                and _edge_punct_class(content[end - 1]) is None
                and end < len(content)
                and content[end] not in "\r\n"
            )
            or (require_sentence_boundaries and not is_sentence_boundary(content, end))
        ):
            where = "结束"
        elif (
            (
                _edge_punct_class(stripped[0]) is not None
                and _edge_punct_class(content[start]) is None
                and start > 0
                and content[start - 1] not in "\r\n"
            )
            or (require_sentence_boundaries and not is_sentence_boundary(content, start))
        ):
            where = "开始"
        else:
            return
        raise _not_found(
            f"Edit {edit_index}: 近似匹配找到的范围在句子中间{where}（命中的原文：「{_preview(content[start:end])}」），"
            f"照这样改会留下半句话，已中止。请从当前原文复制完整的句子作为 old。"
        )

    @classmethod
    def _edits_incoming_text(cls, edits: Any) -> str:
        """本次各 edit 要写入的文本拼在一起，只用于兜底判定引号风格。"""
        if not isinstance(edits, list):
            return ""
        keys = ("new", "text", *cls._REPLACE_NEW_ALIASES, *cls._INSERT_TEXT_ALIASES)
        parts: list[str] = []
        for edit in edits:
            if not isinstance(edit, dict):
                continue
            parts.extend(v for k in keys if isinstance(v := edit.get(k), str))
        return "\n".join(parts)

    @staticmethod
    def _backfill_new_preview(applied_edits: list[dict[str, Any]]) -> None:
        """给只有 text_preview 的 detail 补上同值的 new_preview。

        append/prepend/insert_after/insert_before 记录的是"插入的文本"（text_preview），
        replace 记录的是"替换后的新文本"（new_preview）。对下游（file_edit_applied 事件、
        前端 ToolResultCard）来说两者语义一致，都是"本次写入的新内容"。
        在这里统一补齐，避免每个消费方各写一遍 fallback 分支而漏掉某个 op。
        """
        for detail in applied_edits:
            if not isinstance(detail, dict):
                continue
            if detail.get("new_preview"):
                continue
            text_preview = detail.get("text_preview")
            if isinstance(text_preview, str) and text_preview:
                detail["new_preview"] = text_preview

    # replace 漏写 new 时可接受的替换文本键（模型常把 append 的 text 习惯带过来）。
    _REPLACE_NEW_ALIASES = ("text", "content", "new_text", "replacement")
    # append/prepend/insert_* 漏写 text 时可接受的写入文本键。
    _INSERT_TEXT_ALIASES = ("new", "new_text", "replacement")

    def _run_single_edit_attempt(
        self,
        content_before_edit: str,
        active_edit: dict[str, Any],
        edit_index: int,
        op: str,
        applied_edits: list[dict[str, Any]],
        unchanged_edits: list[dict[str, Any]],
        warnings: list[str],
        *,
        file_id: str | None = None,
        quote_style: str | None,
        active_spans: list[tuple[int, int]] | None,
        edit_hunks: list[tuple[int, int, int]],
        replace_already_matched: list[bool],
        allow_approx_with_marker_prefix: bool,
    ) -> tuple[str, bool]:
        """Execute one edit attempt. Returns ``(new_content, already_matched_noop)``."""
        old_field = active_edit.get("old")
        if (
            op == "replace"
            and isinstance(old_field, str)
            and old_field
            and self._peek_replace_new(active_edit) == old_field
        ):
            # old 与 new 相同即明确无修改意图：永不走模糊/近似定位（防止 F-01 误删原文标点），
            # 统一无害归入 unchanged_edits，不修改原文、不计入 failed_edits / partial_failed。
            matched_verbatim = locate_exact_or_quote_equivalent(content_before_edit, old_field) is not None
            unchanged_edits.append(
                {
                    "index": edit_index,
                    "op": op,
                    "reason": "already_matches" if matched_verbatim else "identical_old_new",
                }
            )
            if matched_verbatim:
                warnings.append(f"Edit {edit_index}: old 与 new 相同，原文这里已经是这样，没有修改，不用重发")
            else:
                warnings.append(
                    f"Edit {edit_index}: old 与 new 相同，没有需要执行的改动，已跳过（未修改原文）"
                )
            return content_before_edit, True

        if op == "replace":
            return (
                self._apply_replace(
                    content_before_edit,
                    active_edit,
                    edit_index,
                    applied_edits,
                    warnings,
                    file_id=file_id,
                    quote_style=quote_style,
                    allowed_fuzzy_spans=active_spans,
                    applied_hunks=edit_hunks,
                    already_matched=replace_already_matched,
                    allow_approx_with_marker_prefix=allow_approx_with_marker_prefix,
                ),
                False,
            )
        if op in ("insert_after", "insert_before"):
            return (
                self._apply_insert(
                    content_before_edit,
                    active_edit,
                    edit_index,
                    applied_edits,
                    warnings,
                    after=op == "insert_after",
                    quote_style=quote_style,
                    allowed_fuzzy_spans=active_spans,
                    applied_hunks=edit_hunks,
                    allow_approx_with_marker_prefix=allow_approx_with_marker_prefix,
                ),
                False,
            )
        if op in ("append", "prepend"):
            notes: list[str] = []
            pos, text = self._position_insert(
                content_before_edit,
                len(content_before_edit) if op == "append" else 0,
                active_edit.get("text", ""),
                direction="after" if op == "append" else "before",
                snap_sentence=False,
                notes=notes,
            )
            next_content, text = self._place_new_text(
                content_before_edit, pos, pos, text, quote_style
            )
            edit_hunks.append((pos, pos, len(text)))
            detail: dict[str, Any] = {
                "op": op,
                "text_len": len(text),
                "text_words": count_words(text),
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
            }
            if notes:
                detail["layout_notes"] = notes
            applied_edits.append(detail)
            return next_content, False
        if op == "delete":
            return (
                self._apply_delete(
                    content_before_edit,
                    active_edit,
                    edit_index,
                    applied_edits,
                    warnings,
                    allowed_fuzzy_spans=active_spans,
                    applied_hunks=edit_hunks,
                    allow_approx_with_marker_prefix=allow_approx_with_marker_prefix,
                ),
                False,
            )
        raise ValueError(
            f"Edit {edit_index}: unknown operation '{op}'. Valid ops: replace, append, insert_after, insert_before"
        )

    @classmethod
    def _strip_write_input_paragraph_stats_markers(
        cls,
        edit: dict[str, Any],
        op: str,
        edit_index: int,
    ) -> bool:
        """Strip exact ``[Pxx · N字] `` prefixes line-by-line from write inputs in place.

        Called only when the original file content does not already contain the exact
        ``format_paragraph_stats`` marker format. Does not require every non-empty line
        to carry a prefix, so partial-marked expansions, clean-old + marked-new edits,
        and marked append/prepend/insert texts never leak view annotations into saved text.

        Rejects marker-only inputs whose stripped result is empty or whitespace/newline-only
        so leaked prefixes are never silently turned into deletions or bare-newline writes.
        """
        stripped = False
        if op == "replace":
            for key in ("new", *cls._REPLACE_NEW_ALIASES):
                val = edit.get(key)
                if isinstance(val, str):
                    cleaned, changed = _strip_paragraph_stats_marker_prefixes(val)
                    if changed:
                        if not cleaned.strip():
                            raise EditFileError(
                                f"Edit {edit_index}: replace 的替换文本仅包含段落统计前缀，没有可写入的正文内容；"
                                '如需删除请显式传入 new=""。',
                                error_type=EDIT_ERROR_INVALID_EDIT,
                            )
                        edit[key] = cleaned
                        stripped = True
        elif op in ("append", "prepend", "insert_after", "insert_before"):
            for key in ("text", *cls._INSERT_TEXT_ALIASES):
                val = edit.get(key)
                if isinstance(val, str):
                    cleaned, changed = _strip_paragraph_stats_marker_prefixes(val)
                    if changed:
                        if not cleaned.strip():
                            raise EditFileError(
                                f"Edit {edit_index}: {op} 的 text 仅包含段落统计前缀，没有可写入的正文内容。",
                                error_type=EDIT_ERROR_INVALID_EDIT,
                            )
                        edit[key] = cleaned
                        stripped = True
        return stripped

    @classmethod
    def _strip_edit_marker_prefixes(cls, edit: dict[str, Any], op: str) -> dict[str, Any] | None:
        """Build a failure-only retry copy with paragraph/line prefixes stripped.

        Returns None unless EVERY non-empty line of the target (``old`` or ``anchor``)
        carries a marker prefix. When stripping ``old``/``anchor``, also strips
        ``new``/``text`` if every non-empty line of it carries a marker prefix so
        view markers are never written into the manuscript.
        """
        target_key = "anchor" if op in ("insert_after", "insert_before") else "old"
        target_val = edit.get(target_key)
        if not isinstance(target_val, str) or not _all_non_empty_lines_have_marker_prefix(target_val):
            return None
        stripped_target = _strip_marker_prefixes(target_val)
        if not stripped_target.strip():
            return None

        retry_edit = dict(edit)
        retry_edit[target_key] = stripped_target
        if op == "replace":
            for key in ("new", *cls._REPLACE_NEW_ALIASES):
                val = retry_edit.get(key)
                if isinstance(val, str) and _all_non_empty_lines_have_marker_prefix(val):
                    stripped_val = _strip_marker_prefixes(val)
                    if not stripped_val.strip():
                        raise EditFileError(
                            'replace 的替换文本仅包含段落/行号前缀，没有可写入的正文内容；如需删除请显式传入 new=""。',
                            error_type=EDIT_ERROR_INVALID_EDIT,
                        )
                    retry_edit[key] = stripped_val
        elif op in ("insert_after", "insert_before"):
            for key in ("text", *cls._INSERT_TEXT_ALIASES):
                val = retry_edit.get(key)
                if isinstance(val, str) and _all_non_empty_lines_have_marker_prefix(val):
                    stripped_val = _strip_marker_prefixes(val)
                    if not stripped_val.strip():
                        raise EditFileError(
                            f"{op} 的 text 仅包含段落/行号前缀，没有可写入的正文内容。",
                            error_type=EDIT_ERROR_INVALID_EDIT,
                        )
                    retry_edit[key] = stripped_val
        return retry_edit

    @classmethod
    def _require_insert_text(
        cls,
        edit: dict[str, Any],
        edit_index: int,
        op: str,
        warnings: list[str],
    ) -> None:
        """append/prepend/insert_* 必须有非空 text；用别名补齐时留告警。

        以前 text 缺失或为空时照样记一次 applied，模型和计费都以为写进去了，
        实际文件一个字没变（空正文纠偏轮因此白跑一轮）。
        """
        if edit.get("text") in (None, ""):
            for key in cls._INSERT_TEXT_ALIASES:
                value = edit.get(key)
                if isinstance(value, str) and value:
                    edit["text"] = value
                    warnings.append(
                        f"Edit {edit_index}: {op} 未提供 text，已使用 {key} 字段作为写入文本"
                    )
                    return
        text = edit.get("text")
        if not isinstance(text, str) or not text:
            raise EditFileError(
                f"Edit {edit_index}: {op} 的 text 为空，没有可写入的内容。"
                f"要写入的文本请放在 text 字段里。"
            )

    @classmethod
    def _peek_replace_new(cls, edit: dict[str, Any]) -> Any:
        """按 _resolve_replace_new 的优先级取替换文本，但不写警告、不报错（只用于比较）。"""
        new_text = edit.get("new")
        if new_text is not None:
            return new_text
        for key in cls._REPLACE_NEW_ALIASES:
            value = edit.get(key)
            if isinstance(value, str):
                return value
        return None

    @classmethod
    def _resolve_replace_new(
        cls,
        edit: dict[str, Any],
        edit_index: int,
        warnings: list[str],
    ) -> str:
        """取 replace 的 new；缺失时按别名补齐，仍缺失就报错，绝不当成删除。

        new="" 是模型明确要求替换为空，照常执行；只有「没给 new」才报错——
        以前缺 new 会默认成空串，台词被静默删除，结果还报成功。
        """
        new_text = edit.get("new")
        if new_text is None:
            for key in cls._REPLACE_NEW_ALIASES:
                value = edit.get(key)
                if isinstance(value, str):
                    warnings.append(
                        f"Edit {edit_index}: replace 未提供 new，已使用 {key} 字段作为替换文本"
                    )
                    return value
            raise EditFileError(
                f"Edit {edit_index}: replace 缺少 new 字段（替换后的新文本）。"
                '只想删除这段原文请显式传 new=""。'
            )
        if not isinstance(new_text, str):
            raise EditFileError(f"Edit {edit_index}: replace 的 new 必须是字符串")
        return new_text

    @staticmethod
    def _extend_fuzzy_span(
        content: str,
        start: int,
        end: int,
        pattern: str,
        *,
        replacement: str | None = None,
        leading: bool = True,
        trailing: bool = True,
    ) -> tuple[int, int]:
        """模糊/近似命中后，把原文边缘的同类标点并入改动范围。

        参照串优先取 pattern（old/anchor）自己的边缘标点；pattern 那一侧没有
        标点而 replacement（new）有时用 replacement 的——new 自带的引号/句号
        会重新写回去，原文那一个若不并入就会重复（「冷。。」「““」）。
        """
        lead_ref = edge_punct_run(pattern, leading=True) if leading else ""
        trail_ref = edge_punct_run(pattern, leading=False) if trailing else ""
        if replacement:
            if leading and not lead_ref:
                lead_ref = edge_punct_run(replacement, leading=True)
            if trailing and not trail_ref:
                trail_ref = edge_punct_run(replacement, leading=False)
        return extend_span_over_edge_punct(
            content, start, end, leading_ref=lead_ref, trailing_ref=trail_ref
        )

    @staticmethod
    def _parse_ignore_punct_whitespace(edit: dict[str, Any]) -> bool:
        """解析 ignore_punct_whitespace，默认 True。

        这是三值语义：未指定 = 用默认值 True，显式给了才转换。coerce_bool 对
        None 恒返回 False，所以必须先判 None，否则「没传」会被当成「传了 false」。
        原来的 ``bool(edit.get(..., True))`` 则相反——会把模型传来的字符串
        "false" 判成 True，等于这个开关根本关不掉。
        """
        raw = edit.get("ignore_punct_whitespace")
        if raw is None:
            return True
        return coerce_bool(raw, default=True)

    @staticmethod
    def _parse_occurrence(edit: dict[str, Any], edit_index: int) -> int | None:
        """解析 1-based 的 occurrence；未指定返回 None（继续走唯一性守卫）。

        replace/delete/insert_* 共用同一套解析，调用方不必再各自 int() 转换，
        「同一份 edits 契约里各 op 行为不一致」正是本次要消灭的问题。
        """
        raw = edit.get("occurrence")
        if raw is None:
            return None
        if isinstance(raw, str) and not raw.strip():
            return None
        try:
            occ = int(raw)
        except Exception as e:
            raise ValueError(
                f"Edit {edit_index}: occurrence must be an integer when provided"
            ) from e
        if occ <= 0:
            raise ValueError(
                f"Edit {edit_index}: occurrence must be >= 1 (1-based), got {occ}"
            )
        return occ

    @staticmethod
    def _select_exact_occurrence_start(
        content: str,
        sub: str,
        occurrence: Any,
        match_count: int,
        edit_index: int,
        *,
        label: str,
        extra_hint: str = "",
        preview_content: str | None = None,
    ) -> int:
        """Resolve the start index for an exact-substring op.

        Enforces the same uniqueness/occurrence guards the fuzzy path already
        applies, so the exact-match fast path can no longer silently edit the
        first of several occurrences or ignore a caller-supplied ``occurrence``:

        - ``occurrence`` is None and ``sub`` appears more than once -> abort
          (ambiguous) instead of editing the first occurrence.
        - ``occurrence`` provided -> validate its range and select that
          (1-based, non-overlapping) occurrence.

        ``content``/``sub`` may be the quote-folded haystack/needle; the
        candidate previews are then cut from ``preview_content`` (the real text,
        same indices) so the model sees the manuscript's own quotes.
        """
        if occurrence is None:
            if match_count > 1:
                previews = _numbered_previews(
                    preview_content if preview_content is not None else content,
                    _exact_spans(content, sub),
                )
                raise _ambiguous(
                    f"Edit {edit_index}: {label}匹配到多个位置（{match_count}处），"
                    f"为避免定位到错误位置已中止。推荐做法：保持原参数不变，"
                    f"加上 occurrence=N 指定要改第几处（1-based，见下方候选片段序号）；"
                    f"或提供更长且更唯一的{label}。{extra_hint}候选片段: {previews}"
                )
            return content.find(sub)

        try:
            occ = int(occurrence)
        except Exception as e:
            raise ValueError(
                f"Edit {edit_index}: occurrence must be an integer when provided"
            ) from e
        if occ <= 0 or occ > match_count:
            raise ValueError(
                f"Edit {edit_index}: occurrence out of range (1..{match_count})"
            )

        start = -1
        search_from = 0
        for _ in range(occ):
            start = content.find(sub, search_from)
            if start < 0:
                break
            search_from = start + len(sub)
        return start

    @staticmethod
    def _select_fuzzy_span(
        content: str,
        spans: list[tuple[int, int]],
        occurrence: int | None,
        edit_index: int,
        *,
        ambiguous: Callable[[int, list[str]], str],
    ) -> tuple[int, int]:
        """模糊匹配的多处候选里选一处：没给 occurrence 且不唯一就中止并列出带序号的候选。"""
        if len(spans) != 1 and occurrence is None:
            raise _ambiguous(ambiguous(len(spans), _numbered_previews(content, spans)))
        if occurrence is None:
            return spans[0]
        if occurrence > len(spans):
            raise EditFileError(
                f"Edit {edit_index}: occurrence out of range (1..{len(spans)})"
            )
        return spans[occurrence - 1]

    def _apply_replace(
        self,
        content: str,
        edit: dict[str, Any],
        edit_index: int,
        applied_edits: list[dict[str, Any]],
        warnings: list[str],
        *,
        file_id: str | None = None,
        quote_style: str | None = None,
        allowed_fuzzy_spans: Sequence[tuple[int, int]] | None = None,
        applied_hunks: list[tuple[int, int, int]] | None = None,
        already_matched: list[bool] | None = None,
        allow_approx_with_marker_prefix: bool = True,
    ) -> str:
        """Apply a replace edit operation."""
        batch_hint = _batch_replace_single_file_hint(file_id)
        old_text = edit.get("old", "")
        # replace_all 是本工具唯一的破坏性开关（无次数上限地替换全部匹配）。
        # strict_json_schema=False 时模型会把它序列化成 "false"/"0"，朴素真值
        # 判断会把这些字符串判真，把「改一处」变成「全改」，因此必须强转。
        replace_all = coerce_bool(edit.get("replace_all"), default=False)
        occurrence = self._parse_occurrence(edit, edit_index)

        match_mode = str(edit.get("match_mode") or "auto").strip().lower()
        ignore_punct_whitespace = self._parse_ignore_punct_whitespace(edit)

        if replace_all and occurrence is not None:
            raise ValueError(
                f"Edit {edit_index}: replace_all 与 occurrence 不能同时使用。"
                f"只改一处请去掉 replace_all，确实要全部替换请去掉 occurrence。"
            )

        # 缺 old 以前只记一条告警就跳过，整次调用仍报 success：模型以为改了，
        # 文件其实没动。现在与缺 new 一样直接报错，交给批量回滚语义处理。
        if not isinstance(old_text, str) or not old_text:
            raise EditFileError(
                f"Edit {edit_index}: replace 缺少 old 字段（要被替换的原文）。"
                f"请从当前文件原文中复制要改的那一段。"
            )
        new_text = self._resolve_replace_new(edit, edit_index, warnings)
        is_deletion = new_text == ""
        if (
            quote_style is not None
            and new_text != old_text
            and fold_double_quotes(new_text) == fold_double_quotes(old_text)
        ):
            # 只改引号写法的编辑就是在要求换体例（如「」改成 “”）。再按稿件体例
            # 规范化，半角引号会被改回原样，正文一字不变却报成功（U079）。半角
            # 引号此时按产品默认的 “” 写入；new 里直接写的 「」/“” 原样保留。
            quote_style = QUOTE_STYLE_CURLY

        # 1) Exact match first, then exact modulo double-quote style; in non-exact modes,
        #    also support deterministic CRLF line-ending exact match (preserving CRLF in new_text).
        located = locate_exact_or_quote_equivalent(content, old_text)
        effective_old = old_text
        effective_new = (
            _adapt_lf_to_crlf(new_text)
            if match_mode != "exact"
            and _is_crlf_content(content)
            and "\n" in new_text
            and "\r\n" not in new_text
            else new_text
        )
        if located is None and match_mode != "exact":
            crlf_located = _locate_crlf_exact_or_quote_equivalent(content, old_text)
            if crlf_located is not None:
                _crlf_mode, haystack, needle, effective_old = crlf_located
                located = ("normalized_exact", haystack, needle)
                effective_new = _adapt_lf_to_crlf(new_text)
        if located is not None:
            exact_mode, haystack, needle = located
            if replace_all:
                spans = _exact_spans(haystack, needle, limit=len(haystack) + 1)
                count = len(spans)
                if already_matched is not None and spans and all(
                    content[s:e] == effective_new for s, e in spans
                ):
                    already_matched.append(True)
                parts: list[str] = []
                placed: list[tuple[int, int]] = []
                prev = out_len = 0
                for start, end in spans:
                    parts.append(content[prev:start])
                    out_len += start - prev
                    parts.append(effective_new)
                    placed.append((out_len, out_len + len(effective_new)))
                    out_len += len(effective_new)
                    prev = end
                parts.append(content[prev:])
                content = self._normalize_placed_spans("".join(parts), placed, quote_style)
                if applied_hunks is not None:
                    applied_hunks.extend(
                        (start, end, p_end - p_start)
                        for (start, end), (p_start, p_end) in zip(spans, placed, strict=True)
                    )
                if placed:
                    effective_new = content[placed[0][0] : placed[0][1]]
                applied_edits.append({
                    "op": "replace",
                    "match_mode": exact_mode,
                    "old_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
                    "new_preview": effective_new[:200] + ("..." if len(effective_new) > 200 else ""),
                    "count": count,
                })
            else:
                match_count = haystack.count(needle)
                # 与 insert_* 共用同一套守卫：occurrence 未指定且有多处匹配时
                # 中止（并推荐 occurrence=N 这条非破坏性出路），指定了就精确
                # 定位到第 N 处，而不是像以前那样把 occurrence 整个忽略、只留
                # replace_all 这一条破坏性逃生通道。
                start = self._select_exact_occurrence_start(
                    haystack,
                    needle,
                    occurrence,
                    match_count,
                    edit_index,
                    label="原文片段",
                    extra_hint=batch_hint,
                    preview_content=content,
                )
                if already_matched is not None and content[start : start + len(effective_old)] == effective_new:
                    already_matched.append(True)
                content, written_new = self._place_new_text(
                    content, start, start + len(effective_old), effective_new, quote_style
                )
                if applied_hunks is not None:
                    applied_hunks.append((start, start + len(effective_old), len(written_new)))
                detail = {
                    "op": "replace",
                    "match_mode": exact_mode,
                    "match_count": match_count,
                    "old_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
                    "new_preview": written_new[:200] + ("..." if len(written_new) > 200 else ""),
                }
                if occurrence is not None:
                    detail["occurrence"] = occurrence
                applied_edits.append(detail)
        else:
            if match_mode == "exact":
                raise _not_found(
                    f"Edit {edit_index}: old text not found in content (exact match)"
                    f"{_crlf_hint_if_applicable(content, old_text)}"
                )

            # 2) Deterministic whitespace/newline/double-quote-normalized match.
            # Runs before fuzzy/approximate in all non-exact modes (unread, range-read,
            # and full-read) so exact_only, range-read, and auto modes stay symmetric
            # and never relax punctuation or bridge missing line breaks.
            norm_spans = _locate_normalized_exact_spans(
                content,
                old_text,
                max_matches=REPLACE_ALL_MAX_FUZZY_MATCHES if replace_all else 20,
            )
            if norm_spans:
                if replace_all:
                    parts = []
                    placed = []
                    norm_hunks: list[tuple[int, int, int]] = []
                    prev = out_len = 0
                    all_equal = True
                    indent_followed = False
                    for start, end in norm_spans:
                        placed_text, indent_changed = _format_replacement_for_span(
                            content, start, end, old_text, new_text
                        )
                        all_equal = all_equal and (content[start:end] == placed_text)
                        indent_followed = indent_followed or indent_changed
                        parts.append(content[prev:start])
                        out_len += start - prev
                        parts.append(placed_text)
                        placed.append((out_len, out_len + len(placed_text)))
                        out_len += len(placed_text)
                        norm_hunks.append((start, end, len(placed_text)))
                        prev = end
                    parts.append(content[prev:])
                    if already_matched is not None and all_equal:
                        already_matched.append(True)
                    content = self._normalize_placed_spans("".join(parts), placed, quote_style)
                    if applied_hunks is not None:
                        applied_hunks.extend(norm_hunks)
                    if placed:
                        new_text = content[placed[0][0] : placed[0][1]]
                    truncated = len(norm_spans) >= REPLACE_ALL_MAX_FUZZY_MATCHES
                    if truncated:
                        warnings.append(
                            f"Edit {edit_index}: replace_all 规范化精确匹配达到 {REPLACE_ALL_MAX_FUZZY_MATCHES} 处上限，"
                            f"可能仍有未替换的出现，请检查剩余内容"
                        )
                    detail: dict[str, Any] = {
                        "op": "replace",
                        "match_mode": "normalized_exact",
                        "old_preview": _preview(old_text),
                        "new_preview": _preview(new_text),
                        "count": len(norm_spans),
                    }
                    if truncated:
                        detail["truncated"] = True
                    if indent_followed:
                        detail["layout_notes"] = [NOTE_INDENT_FOLLOWS_FILE]
                    applied_edits.append(detail)
                    return content
                start, end = self._select_fuzzy_span(
                    content,
                    norm_spans,
                    occurrence,
                    edit_index,
                    ambiguous=lambda count, previews: (
                        f"Edit {edit_index}: 原文片段匹配到多个位置（{count}处），"
                        f"为避免误改已中止。推荐做法：保持原参数不变，加上 occurrence=N "
                        f"指定要改第几处（1-based，见下方候选片段序号）；或提供更长且更唯一的原文/锚点。"
                        f"{batch_hint}候选片段: {previews}"
                    ),
                )
                placed_text, indent_changed = _format_replacement_for_span(
                    content, start, end, old_text, new_text
                )
                if already_matched is not None and content[start:end] == placed_text:
                    already_matched.append(True)
                content, written = self._place_new_text(content, start, end, placed_text, quote_style)
                if applied_hunks is not None:
                    applied_hunks.append((start, end, len(written)))
                detail = {
                    "op": "replace",
                    "match_mode": "normalized_exact",
                    "match_count": len(norm_spans),
                    "old_preview": _preview(old_text),
                    "new_preview": _preview(written),
                }
                if occurrence is not None:
                    detail["occurrence"] = occurrence
                if indent_changed:
                    detail["layout_notes"] = [NOTE_INDENT_FOLLOWS_FILE]
                applied_edits.append(detail)
                return content
            if match_mode == "exact_normalized":
                raise _not_found(
                    f"Edit {edit_index}: old text not found in content (exact match)"
                    f"{_crlf_hint_if_applicable(content, old_text)}"
                )

            # 3) Fuzzy match (ignore punctuation/whitespace)
            # replace_all mirrors the exact path (which replaces EVERY
            # occurrence), so it must not stop at the default 20-match cap.
            fuzzy_stats: dict[str, int] = {}
            spans = find_fuzzy_spans(
                content,
                old_text,
                ignore_punct_whitespace=ignore_punct_whitespace,
                max_matches=REPLACE_ALL_MAX_FUZZY_MATCHES if replace_all else 20,
                stats=fuzzy_stats,
                keep_line_breaks=True,
            )
            if fuzzy_stats.get("boundary_rejected"):
                warnings.append(
                    f"Edit {edit_index}: {fuzzy_stats['boundary_rejected']} 处候选匹配因字符归一化展开边界不对齐被跳过（如罗马数字/合字），已避免吞掉未匹配的原文"
                )

            if spans and fuzzy_stats.get("paragraph_rejected"):
                warnings.append(
                    f"Edit {edit_index}: {fuzzy_stats['paragraph_rejected']} 处候选匹配跨过了原文的段落分隔（old 里没有那处换行），已排除"
                )
            if not spans and fuzzy_stats.get("paragraph_rejected"):
                # 逐字（忽略标点）能对上，只是跨了段落：这就是模型要改的那段，
                # 不能再退到近似匹配去改其中一段的局部，那样会重复写入另一段的开头。
                raise _paragraph_break_error(edit_index, "old", content=content, pattern=old_text)

            unseen_fuzzy_rejected = False
            if allowed_fuzzy_spans is not None and spans:
                if replace_all:
                    if not all(_span_within_seen(s, e, allowed_fuzzy_spans) for s, e in spans):
                        spans = []
                        unseen_fuzzy_rejected = True
                else:
                    spans = [sp for sp in spans if _span_within_seen(sp[0], sp[1], allowed_fuzzy_spans)]
                    if not spans:
                        unseen_fuzzy_rejected = True

            # 4) If fuzzy match fails, try approximate match (handles word errors)
            # replace_all=True 要求替换所有命中，不能退化为只改一处的近似匹配；
            # 若全文模糊匹配已命中但全部落在未读区间（unseen_fuzzy_rejected），也不得退化到
            # 已读区间做近似匹配，避免误伤已读区间里的相似句。
            approx_match = None
            approx_stats: dict[str, int] = {}
            defer_approx_for_prefix_retry = (
                not allow_approx_with_marker_prefix
                and _all_non_empty_lines_have_marker_prefix(old_text)
            )
            if not spans and not replace_all and not unseen_fuzzy_rejected and not defer_approx_for_prefix_retry:
                # Destructive replace(new="") enforces the same strict approximate-match guards as
                # op="delete" (<=12% error, >=15 chars, sentence/line boundaries) while exact and
                # normalized-exact short verbatim deletions remain unaffected above.
                approx_max_error_rate = 0.12 if is_deletion else 0.25
                approx_min_pattern_len = 15 if is_deletion else 8
                if allowed_fuzzy_spans is not None:
                    approx_match = _find_approximate_in_spans(
                        content,
                        old_text,
                        allowed_fuzzy_spans,
                        max_error_rate=approx_max_error_rate,
                        min_pattern_len=approx_min_pattern_len,
                        keep_line_breaks=True,
                        stats=approx_stats,
                    )
                else:
                    approx_match = find_approximate_match(
                        content,
                        old_text,
                        max_error_rate=approx_max_error_rate,
                        min_pattern_len=approx_min_pattern_len,
                        keep_line_breaks=True,
                        stats=approx_stats,
                    )
                if not approx_match and approx_stats.get("paragraph_rejected"):
                    raise _paragraph_break_error(edit_index, "old", content=content, pattern=old_text)
                if approx_match:
                    start, end, similarity, _matched_text = approx_match
                    # 近似匹配只会给出唯一的一处；调用方若点名了第 2 处及以后，
                    # 说明它以为文中有多处，此时套用这唯一一处就是改错位置。
                    if occurrence is not None and occurrence != 1:
                        raise EditFileError(
                            f"Edit {edit_index}: 近似匹配只找到 1 处，"
                            f"occurrence out of range (1..1)"
                        )
                    # 近似匹配同样在去标点的归一化空间里比较，边缘标点要一并纳入。
                    start, end = self._extend_fuzzy_span(
                        content, start, end, old_text, replacement=new_text
                    )
                    self._check_approximate_edges(
                        content,
                        start,
                        end,
                        old_text,
                        edit_index,
                        require_sentence_boundaries=is_deletion,
                    )
                    matched_text = content[start:end]
                    placed_text, indent_changed = _format_replacement_for_span(
                        content, start, end, old_text, new_text
                    )
                    if already_matched is not None and matched_text == placed_text:
                        already_matched.append(True)
                    # Single approximate match - use it
                    content, written = self._place_new_text(content, start, end, placed_text, quote_style)
                    if applied_hunks is not None:
                        applied_hunks.append((start, end, len(written)))
                    detail = {
                        "op": "replace",
                        "match_mode": "approximate",
                        "similarity": round(similarity, 3),
                        "matched_original": _preview(matched_text),
                        "warning": FUZZY_EDIT_WARNING,
                        "old_preview": _preview(old_text),
                        "new_preview": _preview(written),
                    }
                    if indent_changed:
                        detail["layout_notes"] = [NOTE_INDENT_FOLLOWS_FILE]
                    applied_edits.append(detail)
                    return content

            if not spans and not approx_match:
                suggestions = suggest_similar_lines(
                    content,
                    old_text,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
                raise _not_found(
                    f"Edit {edit_index}: 找不到要替换的原文片段。"
                    f"{_crlf_hint_if_applicable(content, old_text)}"
                    f"{_approximate_not_found_advice(approx_stats, label='原文片段')}"
                    f"候选片段: {suggestions}"
                )

            if replace_all:
                # Stitch segments in one pass (spans are sorted and
                # non-overlapping); repeated re-slicing would be O(n·k).
                parts: list[str] = []
                placed: list[tuple[int, int]] = []
                fuzzy_hunks: list[tuple[int, int, int]] = []
                prev = out_len = 0
                first_matched: str | None = None
                indent_followed = False
                all_equal = True
                for start, end in spans:
                    if ignore_punct_whitespace:
                        start, end = self._extend_fuzzy_span(
                            content, start, end, old_text, replacement=new_text
                        )
                        # 相邻两处之间只隔着标点时，前一处的尾扩展和后一处的
                        # 头扩展可能重叠；后一处从前一处的结尾开始。
                        start = max(start, prev)
                    if first_matched is None:
                        first_matched = content[start:end]
                    placed_text, indent_changed = _format_replacement_for_span(
                        content, start, end, old_text, new_text
                    )
                    all_equal = all_equal and (content[start:end] == placed_text)
                    indent_followed = indent_followed or indent_changed
                    parts.append(content[prev:start])
                    out_len += start - prev
                    parts.append(placed_text)
                    placed.append((out_len, out_len + len(placed_text)))
                    out_len += len(placed_text)
                    fuzzy_hunks.append((start, end, len(placed_text)))
                    prev = end
                parts.append(content[prev:])
                if already_matched is not None and spans and all_equal:
                    already_matched.append(True)
                content = self._normalize_placed_spans("".join(parts), placed, quote_style)
                if applied_hunks is not None:
                    applied_hunks.extend(fuzzy_hunks)
                if placed:
                    new_text = content[placed[0][0] : placed[0][1]]

                truncated = len(spans) >= REPLACE_ALL_MAX_FUZZY_MATCHES
                if truncated:
                    warnings.append(
                        f"Edit {edit_index}: replace_all 近似匹配达到 {REPLACE_ALL_MAX_FUZZY_MATCHES} 处上限，"
                        f"可能仍有未替换的出现，请检查剩余内容"
                    )
                detail = {
                    "op": "replace",
                    "match_mode": "fuzzy",
                    "ignore_punct_whitespace": ignore_punct_whitespace,
                    "matched_original": _preview(first_matched or ""),
                    "warning": FUZZY_EDIT_WARNING,
                    "old_preview": _preview(old_text),
                    "new_preview": _preview(new_text),
                    "count": len(spans),
                }
                if truncated:
                    detail["truncated"] = True
                if indent_followed:
                    detail["layout_notes"] = [NOTE_INDENT_FOLLOWS_FILE]
                applied_edits.append(detail)
            else:
                start, end = self._select_fuzzy_span(
                    content,
                    spans,
                    occurrence,
                    edit_index,
                    ambiguous=lambda count, previews: (
                        f"Edit {edit_index}: 原文片段匹配到多个位置（{count}处），"
                        f"为避免误改已中止。推荐做法：保持原参数不变，加上 occurrence=N "
                        f"指定要改第几处（1-based，见下方候选片段序号）；或提供更长且更唯一的原文/锚点。"
                        f"{batch_hint}候选片段: {previews}"
                    ),
                )
                if ignore_punct_whitespace:
                    start, end = self._extend_fuzzy_span(
                        content, start, end, old_text, replacement=new_text
                    )
                matched_text = content[start:end]
                placed_text, indent_changed = _format_replacement_for_span(
                    content, start, end, old_text, new_text
                )
                if already_matched is not None and matched_text == placed_text:
                    already_matched.append(True)
                content, written = self._place_new_text(content, start, end, placed_text, quote_style)
                if applied_hunks is not None:
                    applied_hunks.append((start, end, len(written)))
                detail = {
                    "op": "replace",
                    "match_mode": "fuzzy",
                    "ignore_punct_whitespace": ignore_punct_whitespace,
                    "matched_original": _preview(matched_text),
                    "warning": FUZZY_EDIT_WARNING,
                    "old_preview": _preview(old_text),
                    "new_preview": _preview(written),
                    "match_count": len(spans),
                }
                if occurrence is not None:
                    detail["occurrence"] = occurrence
                if indent_changed:
                    detail["layout_notes"] = [NOTE_INDENT_FOLLOWS_FILE]
                applied_edits.append(detail)

        return content

    def _apply_insert(
        self,
        content: str,
        edit: dict[str, Any],
        edit_index: int,
        applied_edits: list[dict[str, Any]],
        warnings: list[str],
        *,
        after: bool,
        quote_style: str | None = None,
        allowed_fuzzy_spans: Sequence[tuple[int, int]] | None = None,
        applied_hunks: list[tuple[int, int, int]] | None = None,
        allow_approx_with_marker_prefix: bool = True,
    ) -> str:
        """Apply an insert_after (after=True) or insert_before edit operation."""
        op = "insert_after" if after else "insert_before"
        direction = "after" if after else "before"
        anchor = edit.get("anchor", "")
        text = edit.get("text", "")

        match_mode = str(edit.get("match_mode") or "auto").strip().lower()
        ignore_punct_whitespace = self._parse_ignore_punct_whitespace(edit)
        occurrence = self._parse_occurrence(edit, edit_index)

        if not anchor:
            raise ValueError(f"Edit {edit_index}: 'anchor' field is required for {op} operation")

        def place(pos: int, detail: dict[str, Any], *, exact_anchor: bool = False) -> str:
            nonlocal text
            notes: list[str] = []
            pos, text = self._position_insert(
                content, pos, text, direction=direction, snap_sentence=True, notes=notes,
                exact_anchor=exact_anchor,
            )
            placed, text = self._place_new_text(content, pos, pos, text, quote_style)
            if applied_hunks is not None:
                applied_hunks.append((pos, pos, len(text)))
            applied_edits.append({
                "op": op,
                **detail,
                "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                "text_len": len(text),
                "text_words": count_words(text),
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
            })
            if notes:
                applied_edits[-1]["layout_notes"] = notes
            return placed

        located = locate_exact_or_quote_equivalent(content, anchor)
        effective_anchor = anchor
        if (
            located is not None
            and match_mode != "exact"
            and _is_crlf_content(content)
            and isinstance(text, str)
            and "\n" in text
            and "\r\n" not in text
        ):
            text = _adapt_lf_to_crlf(text)
        if located is None and match_mode != "exact":
            crlf_located = _locate_crlf_exact_or_quote_equivalent(content, anchor)
            if crlf_located is not None:
                _crlf_mode, haystack, needle, effective_anchor = crlf_located
                located = ("normalized_exact", haystack, needle)
                text = _adapt_lf_to_crlf(text)
        if located is not None:
            exact_mode, haystack, needle = located
            match_count = haystack.count(needle)
            start = self._select_exact_occurrence_start(
                haystack, needle, occurrence, match_count, edit_index, label="锚点",
                preview_content=content,
            )
            pos = start + len(effective_anchor) if after else start
            return place(pos, {"match_mode": exact_mode, "match_count": match_count}, exact_anchor=True)

        if match_mode == "exact":
            raise _not_found(
                f"Edit {edit_index}: anchor text not found in content (exact match)"
                f"{_crlf_hint_if_applicable(content, anchor)}"
            )

        norm_spans = _locate_normalized_exact_spans(content, anchor)
        if norm_spans:
            start, end = self._select_fuzzy_span(
                content,
                norm_spans,
                occurrence,
                edit_index,
                ambiguous=lambda count, previews: (
                    f"Edit {edit_index}: 锚点匹配到多个位置（{count}处），"
                    f"为避免插入到错误位置已中止。推荐做法：保持原参数不变，加上 occurrence=N "
                    f"指定第几处（1-based，见下方候选片段序号）；或提供更长且更唯一的锚点。"
                    f"候选片段: {previews}"
                ),
            )
            pos = end if after else start
            detail: dict[str, Any] = {
                "match_mode": "normalized_exact",
                "match_count": len(norm_spans),
            }
            if occurrence is not None:
                detail["occurrence"] = occurrence
            return place(pos, detail, exact_anchor=True)
        if match_mode == "exact_normalized":
            raise _not_found(
                f"Edit {edit_index}: anchor text not found in content (exact match)"
                f"{_crlf_hint_if_applicable(content, anchor)}"
            )

        fuzzy_stats: dict[str, int] = {}
        spans = find_fuzzy_spans(
            content,
            anchor,
            ignore_punct_whitespace=ignore_punct_whitespace,
            stats=fuzzy_stats,
        )
        if fuzzy_stats.get("boundary_rejected"):
            warnings.append(
                f"Edit {edit_index}: {fuzzy_stats['boundary_rejected']} 处候选锚点因字符归一化展开边界不对齐被跳过（如罗马数字/合字）"
            )
        unseen_fuzzy_rejected = False
        if allowed_fuzzy_spans is not None and spans:
            spans = [sp for sp in spans if _span_within_seen(sp[0], sp[1], allowed_fuzzy_spans)]
            if not spans:
                unseen_fuzzy_rejected = True
        if spans:
            start, end = self._select_fuzzy_span(
                content,
                spans,
                occurrence,
                edit_index,
                ambiguous=lambda count, previews: (
                    f"Edit {edit_index}: 锚点匹配到多个位置（{count}处），"
                    f"为避免插入到错误位置已中止。推荐做法：保持原参数不变，加上 occurrence=N "
                    f"指定第几处（1-based，见下方候选片段序号）；或提供更长且更唯一的锚点。"
                    f"候选片段: {previews}"
                ),
            )
            pos = end if after else start
            if ignore_punct_whitespace:
                if after:
                    _, pos = self._extend_fuzzy_span(content, start, end, anchor, leading=False)
                else:
                    pos, _ = self._extend_fuzzy_span(content, start, end, anchor, trailing=False)
            return place(pos, {
                "match_mode": "fuzzy",
                "ignore_punct_whitespace": ignore_punct_whitespace,
                "match_count": len(spans),
            })

        # Secondary fallback: approximate match (handles word errors)
        # 若全文模糊匹配已命中但全部落在未读区间，不得退化到已读区间做近似/段落兜底匹配。
        approx_stats: dict[str, int] = {}
        defer_approx_for_prefix_retry = (
            not allow_approx_with_marker_prefix
            and _all_non_empty_lines_have_marker_prefix(anchor)
        )
        if not unseen_fuzzy_rejected and not defer_approx_for_prefix_retry:
            if allowed_fuzzy_spans is not None:
                approx_match = _find_approximate_in_spans(
                    content,
                    anchor,
                    allowed_fuzzy_spans,
                    max_error_rate=0.25,
                    min_pattern_len=8,
                    stats=approx_stats,
                )
            else:
                approx_match = find_approximate_match(
                    content,
                    anchor,
                    max_error_rate=0.25,
                    min_pattern_len=8,
                    stats=approx_stats,
                )
            if approx_match:
                start, end, similarity, matched_text = approx_match
                if occurrence is not None and occurrence != 1:
                    raise ValueError(
                        f"Edit {edit_index}: 近似匹配只找到 1 处锚点，"
                        f"occurrence out of range (1..1)"
                    )
                if after:
                    # 锚点以句号/引号收尾时，插入点要越过原文对应的标点，不能插在「冷」和「。」之间。
                    _, pos = self._extend_fuzzy_span(content, start, end, anchor, leading=False)
                else:
                    # 锚点以引号开头时插入点要落在原文开引号之前，不能把新文本插进「“」和正文之间。
                    pos, _ = self._extend_fuzzy_span(content, start, end, anchor, trailing=False)
                return place(pos, {
                    "match_mode": "approximate",
                    "similarity": round(similarity, 3),
                    "matched_original": matched_text[:200] + ("..." if len(matched_text) > 200 else ""),
                })

            # Tertiary fallback: locate a unique best paragraph
            if allowed_fuzzy_spans is not None:
                block_span = _find_unique_line_in_spans(
                    content,
                    anchor,
                    allowed_fuzzy_spans,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
            else:
                block_span = find_unique_line_span(
                    content,
                    anchor,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
            if block_span:
                start, end, block_score = block_span
                # 段落兜底与近似匹配一样只给出唯一一处：点名第 2 处及以后就是插错位置。
                if occurrence is not None and occurrence != 1:
                    raise ValueError(
                        f"Edit {edit_index}: 段落兜底只找到 1 处锚点，"
                        f"occurrence out of range (1..1)"
                    )
                # fuzzy_paragraph 是「最像的整段」而不是逐字命中，必须把
                # 置信度与兜底性质写进详情，否则模型只看到 match_count=1，
                # 会误以为这是确定无疑的唯一匹配而不再复核。
                return place(end if after else start, {
                    "match_mode": "fuzzy_paragraph",
                    "ignore_punct_whitespace": ignore_punct_whitespace,
                    "match_count": 1,
                    "confidence": round(block_score, 3),
                    "fallback": True,
                })

        suggestions = suggest_similar_lines(
            content,
            anchor,
            ignore_punct_whitespace=ignore_punct_whitespace,
        )
        raise _not_found(
            f"Edit {edit_index}: 找不到插入锚点。"
            f"{_crlf_hint_if_applicable(content, anchor)}"
            f"{_approximate_not_found_advice(approx_stats, label='锚点')}"
            f"候选片段: {suggestions}"
        )

    def _apply_delete(
        self,
        content: str,
        edit: dict[str, Any],
        edit_index: int,
        applied_edits: list[dict[str, Any]],
        warnings: list[str],
        *,
        allowed_fuzzy_spans: Sequence[tuple[int, int]] | None = None,
        applied_hunks: list[tuple[int, int, int]] | None = None,
        allow_approx_with_marker_prefix: bool = True,
    ) -> str:
        """Apply a delete edit operation."""
        old_text = edit.get("old", "")

        match_mode = str(edit.get("match_mode") or "auto").strip().lower()
        ignore_punct_whitespace = self._parse_ignore_punct_whitespace(edit)
        occurrence = self._parse_occurrence(edit, edit_index)

        if not isinstance(old_text, str) or not old_text:
            raise EditFileError(
                f"Edit {edit_index}: delete 缺少 old 字段（要删除的原文）。"
                f"请从当前文件原文中复制要删的那一段。"
            )

        located = locate_exact_or_quote_equivalent(content, old_text)
        effective_old = old_text
        if located is None and match_mode != "exact":
            crlf_located = _locate_crlf_exact_or_quote_equivalent(content, old_text)
            if crlf_located is not None:
                _crlf_mode, haystack, needle, effective_old = crlf_located
                located = ("normalized_exact", haystack, needle)
        if located is not None:
            exact_mode, haystack, needle = located
            match_count = haystack.count(needle)
            # 与 replace/insert_* 一致：未指定 occurrence 且多处匹配时中止，
            # 指定了就精确删掉第 N 处（delete 没有 replace_all，以前这条路
            # 完全是死胡同——模型除了重写锚点别无出路）。
            start = self._select_exact_occurrence_start(
                haystack,
                needle,
                occurrence,
                match_count,
                edit_index,
                label="删除片段",
                preview_content=content,
            )
            content = content[:start] + content[start + len(effective_old):]
            if applied_hunks is not None:
                applied_hunks.append((start, start + len(effective_old), 0))
            detail = {
                "op": "delete",
                "match_mode": exact_mode,
                "match_count": match_count,
                "deleted_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
            }
            if occurrence is not None:
                detail["occurrence"] = occurrence
            applied_edits.append(detail)
        else:
            if match_mode == "exact":
                raise _not_found(
                    f"Edit {edit_index}: text to delete not found in content (exact match)"
                    f"{_crlf_hint_if_applicable(content, old_text)}"
                )

            norm_spans = _locate_normalized_exact_spans(content, old_text)
            if norm_spans:
                start, end = self._select_fuzzy_span(
                    content,
                    norm_spans,
                    occurrence,
                    edit_index,
                    ambiguous=lambda count, previews: (
                        f"Edit {edit_index}: 删除片段匹配到多个位置（{count}处），"
                        f"为避免误删已中止。推荐做法：保持原参数不变，加上 occurrence=N "
                        f"指定要删第几处（1-based，见下方候选片段序号）；或提供更长且更唯一的原文/锚点。"
                        f"候选片段: {previews}"
                    ),
                )
                matched_text = content[start:end]
                content = content[:start] + content[end:]
                if applied_hunks is not None:
                    applied_hunks.append((start, end, 0))
                detail = {
                    "op": "delete",
                    "match_mode": "normalized_exact",
                    "match_count": len(norm_spans),
                    "deleted_preview": _preview(matched_text),
                }
                if occurrence is not None:
                    detail["occurrence"] = occurrence
                applied_edits.append(detail)
                return content
            if match_mode == "exact_normalized":
                raise _not_found(
                    f"Edit {edit_index}: text to delete not found in content (exact match)"
                    f"{_crlf_hint_if_applicable(content, old_text)}"
                )

            fuzzy_stats: dict[str, int] = {}
            spans = find_fuzzy_spans(
                content,
                old_text,
                ignore_punct_whitespace=ignore_punct_whitespace,
                stats=fuzzy_stats,
                keep_line_breaks=True,
            )
            if fuzzy_stats.get("boundary_rejected"):
                warnings.append(
                    f"Edit {edit_index}: {fuzzy_stats['boundary_rejected']} 处候选匹配因字符归一化展开边界不对齐被跳过（如罗马数字/合字），已避免误删原文"
                )
            if spans and fuzzy_stats.get("paragraph_rejected"):
                warnings.append(
                    f"Edit {edit_index}: {fuzzy_stats['paragraph_rejected']} 处候选匹配跨过了原文的段落分隔（old 里没有那处换行），已排除"
                )
            if not spans and fuzzy_stats.get("paragraph_rejected"):
                raise _paragraph_break_error(edit_index, "old", content=content, pattern=old_text)

            unseen_fuzzy_rejected = False
            if allowed_fuzzy_spans is not None and spans:
                spans = [sp for sp in spans if _span_within_seen(sp[0], sp[1], allowed_fuzzy_spans)]
                if not spans:
                    unseen_fuzzy_rejected = True

            # For destructive delete, tighten approximate matching (<=12% diff, >=15 chars)
            # and require both match boundaries to fall on sentence/line boundaries so a
            # shrunk window cannot silently skip a subject/clause and leave orphaned text.
            approx_match = None
            approx_stats: dict[str, int] = {}
            defer_approx_for_prefix_retry = (
                not allow_approx_with_marker_prefix
                and _all_non_empty_lines_have_marker_prefix(old_text)
            )
            if not spans and not unseen_fuzzy_rejected and not defer_approx_for_prefix_retry:
                if allowed_fuzzy_spans is not None:
                    approx_match = _find_approximate_in_spans(
                        content,
                        old_text,
                        allowed_fuzzy_spans,
                        max_error_rate=0.12,
                        min_pattern_len=15,
                        keep_line_breaks=True,
                        stats=approx_stats,
                    )
                else:
                    approx_match = find_approximate_match(
                        content,
                        old_text,
                        max_error_rate=0.12,
                        min_pattern_len=15,
                        keep_line_breaks=True,
                        stats=approx_stats,
                    )
                if not approx_match and approx_stats.get("paragraph_rejected"):
                    raise _paragraph_break_error(edit_index, "old", content=content, pattern=old_text)
                if approx_match:
                    start, end, similarity, _matched_text = approx_match
                    if occurrence is not None and occurrence != 1:
                        raise EditFileError(
                            f"Edit {edit_index}: 近似匹配只找到 1 处，"
                            f"occurrence out of range (1..1)"
                        )
                    start, end = self._extend_fuzzy_span(content, start, end, old_text)
                    self._check_approximate_edges(
                        content,
                        start,
                        end,
                        old_text,
                        edit_index,
                        require_sentence_boundaries=True,
                    )
                    matched_text = content[start:end]
                    content = content[:start] + content[end:]
                    if applied_hunks is not None:
                        applied_hunks.append((start, end, 0))
                    applied_edits.append({
                        "op": "delete",
                        "match_mode": "approximate",
                        "similarity": round(similarity, 3),
                        "matched_original": _preview(matched_text),
                        "warning": FUZZY_EDIT_WARNING,
                        "deleted_preview": _preview(old_text),
                    })
                    return content

            if not spans:
                suggestions = suggest_similar_lines(
                    content,
                    old_text,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
                raise _not_found(
                    f"Edit {edit_index}: 找不到要删除的原文片段。"
                    f"{_approximate_not_found_advice(approx_stats, label='删除片段')}"
                    f"候选片段: {suggestions}"
                )

            start, end = self._select_fuzzy_span(
                content,
                spans,
                occurrence,
                edit_index,
                ambiguous=lambda count, previews: (
                    f"Edit {edit_index}: 删除片段匹配到多个位置（{count}处），"
                    f"为避免误删已中止。推荐做法：保持原参数不变，加上 occurrence=N "
                    f"指定要删第几处（1-based，见下方候选片段序号）；或提供更长且更唯一的原文/锚点。"
                    f"候选片段: {previews}"
                ),
            )
            if ignore_punct_whitespace:
                # 只按 old 自己的边缘标点扩展：删除没有 new 可参照，
                # old 带了引号/句号就把原文对应的那一个一起删掉，不留孤立的「“。」。
                start, end = self._extend_fuzzy_span(content, start, end, old_text)
            matched_text = content[start:end]
            content = content[:start] + content[end:]
            if applied_hunks is not None:
                applied_hunks.append((start, end, 0))
            detail = {
                "op": "delete",
                "match_mode": "fuzzy",
                "ignore_punct_whitespace": ignore_punct_whitespace,
                "matched_original": _preview(matched_text),
                "warning": FUZZY_EDIT_WARNING,
                "deleted_preview": _preview(old_text),
                "match_count": len(spans),
            }
            if occurrence is not None:
                detail["occurrence"] = occurrence
            applied_edits.append(detail)

        return content

    @staticmethod
    def _edit_version_summary(applied_edits: list[dict[str, Any]]) -> str:
        op_summaries = []
        for detail in applied_edits:
            op = detail.get("op", "unknown")
            if op == "replace":
                op_summaries.append("替换")
            elif op == "append":
                op_summaries.append("追加")
            elif op == "prepend":
                op_summaries.append("前置")
            elif op in ("insert_after", "insert_before"):
                op_summaries.append("插入")
            elif op == "delete":
                op_summaries.append("删除")

        change_summary = (
            f"AI 编辑: {', '.join(op_summaries[:3])}"
            if op_summaries
            else "AI 编辑"
        )
        if len(op_summaries) > 3:
            change_summary += f" 等 {len(op_summaries)} 处修改"
        return change_summary

    def _create_edit_version(
        self,
        file_id: str,
        content: str,
        applied_edits: list[dict[str, Any]],
    ) -> None:
        """Stage AI edit history in the caller's content transaction."""
        self._create_ai_version(file_id, content, self._edit_version_summary(applied_edits))

    def _create_ai_version(self, file_id: str, content: str, change_summary: str) -> None:
        FileVersionService().create_version(
            session=self.session,
            file_id=file_id,
            new_content=content,
            change_type=CHANGE_TYPE_AI_EDIT,
            change_source=CHANGE_SOURCE_AI,
            change_summary=change_summary,
            commit=False,
        )



__all__ = [
    "BATCH_REPLACE_MAX_FILES",
    "BATCH_REPLACE_MAX_REMAINING_FILES",
    "EDIT_ERROR_ANCHOR_AMBIGUOUS",
    "EDIT_ERROR_ANCHOR_NOT_FOUND",
    "EDIT_ERROR_CHAPTER_REPLACE_PENDING",
    "EDIT_ERROR_CHAPTER_TARGET_AMBIGUOUS",
    "EDIT_ERROR_FILE_BUSY",
    "EDIT_ERROR_FILE_NOT_FOUND",
    "EDIT_ERROR_GENERIC",
    "EDIT_ERROR_INVALID_EDIT",
    "EDIT_ERROR_INVALID_PARENT_ID",
    "EDIT_ERROR_INVALID_READ_RANGE",
    "EDIT_ERROR_NO_CHANGE",
    "EDIT_ERROR_PERMISSION_DENIED",
    "EDIT_ERROR_PRE_WRITE_HISTORY_UNAVAILABLE",
    "EDIT_ERROR_SKILL_LIMIT_EXCEEDED",
    "EDIT_ERROR_SKILL_NOT_FOUND",
    "EDIT_ERROR_SKILL_RESOURCE_NOT_FOUND",
    "EDIT_ERROR_USER_MESSAGES",
    "EVENT_LOOP_LOCK_WAIT_SECONDS",
    "EditBatchError",
    "EditFileError",
    "FileEditor",
    "FileWriteBusyError",
    "PreWriteHistoryUnavailableError",
    "edit_error_user_message",
    "extract_batch_replace_ui_payload",
    "extract_edit_file_ui_payload",
    "format_malformed_uuid_hint",
    "slim_batch_replace_result_for_llm",
    "slim_edit_file_result_for_llm",
    "acquire_file_write_lock",
    "file_write_lock",
]
