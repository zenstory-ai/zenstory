"""
File edit operations for agent tools.

This module provides precise file editing operations:
- edit_file: Apply multiple edit operations (replace/insert/append/prepend/delete)

Supports fuzzy and approximate text matching for robust editing even when
the LLM provides slightly different text.

Extracted from the monolithic file_executor.py for better maintainability.
"""

import asyncio
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from services.file_version import FileVersionService
from sqlmodel import Session, select

from agent.constants import coerce_bool
from agent.tools.permissions import check_file_access_in_tool_context
from config.datetime_utils import advance_timestamp, normalize_datetime_to_utc, utcnow
from models import File
from models.file_version import (
    CHANGE_SOURCE_AI,
    CHANGE_TYPE_AI_EDIT,
)
from services.features.activation_event_service import activation_event_service
from utils.logger import get_logger, log_with_context

from .text_matching import (
    build_span_previews,
    find_approximate_match,
    find_fuzzy_spans,
    find_unique_line_span,
    suggest_similar_lines,
)

logger = get_logger(__name__)

# Cap for replace_all fuzzy scanning. The exact-match path replaces EVERY
# occurrence, so the fuzzy path must not silently stop at a handful; this cap
# only guards pathological inputs and is surfaced as an explicit warning when
# actually hit.
REPLACE_ALL_MAX_FUZZY_MATCHES = 1000

# SQLite has no row-level locks, so same-file read-modify-write sections are
# serialized with in-process striped locks instead (the default SQLite
# deployment runs a single server process; cross-process SQLite writers are
# not covered). Striping keeps memory bounded; distinct files may share a
# stripe, which only costs some extra serialization.
_FILE_WRITE_LOCK_STRIPES = 64
_file_write_locks = [threading.Lock() for _ in range(_FILE_WRITE_LOCK_STRIPES)]

# 事件循环线程上等待写锁的硬上限。持锁方可能是工作线程里的 commit + 版本快照
# （_create_version 会重放 diff 链并对整章正文跑 difflib），SQLite 写冲突时
# 还会撞上 PRAGMA busy_timeout=30000。在事件循环线程上同步等这么久等于整个
# 进程停摆：该 worker 的所有 SSE 流与 HTTP 请求一起卡住。因此事件循环线程上
# 只做有界等待，超时抛可重试错误，绝不把不确定时长的等待压在事件循环上。
EVENT_LOOP_LOCK_WAIT_SECONDS = 0.5


class FileWriteBusyError(RuntimeError):
    """写锁在有界等待内没拿到（文件正被另一个写入任务占用），可安全重试。"""


def file_write_lock(file_id: str) -> threading.Lock:
    """Return the in-process write lock striped by ``file_id``."""
    return _file_write_locks[hash(file_id) % _FILE_WRITE_LOCK_STRIPES]


def _running_on_event_loop_thread() -> bool:
    """当前线程是否正在跑 asyncio 事件循环。"""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@contextmanager
def acquire_file_write_lock(file_id: str) -> Iterator[None]:
    """获取按 ``file_id`` 分条带的进程内写锁，且不阻塞事件循环。

    - 工作线程（asyncio.to_thread / 线程池）：照常阻塞式获取，语义不变。
    - 事件循环线程：只等 ``EVENT_LOOP_LOCK_WAIT_SECONDS``，超时抛
      :class:`FileWriteBusyError`。写工具本应被 offload 到线程池执行，
      走到这里说明调用方仍在事件循环上同步调用；此时宁可让这一次编辑失败
      并提示重试，也不能让整个进程陪着等（最坏可达 SQLite busy_timeout 的 30 秒）。
    """
    lock = file_write_lock(file_id)
    if _running_on_event_loop_thread():
        if not lock.acquire(timeout=EVENT_LOOP_LOCK_WAIT_SECONDS):
            raise FileWriteBusyError(
                "该文件正被另一个写入任务占用，本次编辑未做任何修改，请稍后重试。"
            )
    else:
        lock.acquire()
    try:
        yield
    finally:
        lock.release()


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


def _numbered_previews(content: str, spans: list[tuple[int, int]]) -> list[str]:
    """给候选片段标出 1-based 序号，模型可以直接把序号填进 occurrence。"""
    return [
        f"[occurrence={i}] {preview}"
        for i, preview in enumerate(build_span_previews(content, spans), start=1)
    ]


class FileEditor:
    """
    Editor for file content with robust text matching.

    This class provides precise editing operations on file content with:
    - Exact, fuzzy, and approximate text matching
    - Support for replace, insert, append, prepend, and delete operations
    - Version history tracking
    - Permission checking
    """

    def __init__(self, session: Session, user_id: str | None = None):
        """
        Initialize file editor.

        Args:
            session: Database session
            user_id: Current user ID (UUID string, for permission checks)
        """
        self.session = session
        self.user_id = user_id

    def edit_file(
        self,
        id: str,
        edits: list[dict[str, Any]],
        continue_on_error: bool = False,
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

        Returns:
            Dict with edit results:
                - id: File ID
                - title: File title
                - edits_applied: Number of successful edits
                - new_length: New content length
                - details: List of applied edit details
                - failed_edits: List of failed edit details (when continue_on_error=True)

        Raises:
            ValueError: If file not found or edit operation fails
            PermissionError: If user doesn't have permission
        """
        from database import is_postgres

        # continue_on_error 可能一路从 LLM 参数透传下来（strict_json_schema=False
        # 时布尔会被序列化成 "false"/"0"），朴素真值判断会把它们判真。
        continue_on_error = coerce_bool(continue_on_error, default=False)

        if is_postgres:
            return self._edit_file_impl(id, edits, continue_on_error)
        # SQLite has no row locks: serialize same-file read-modify-write with
        # the in-process per-file lock so a concurrent edit (parallel_execute
        # runs each task on its own session/thread) cannot interleave between
        # our read and commit. 获取方式见 acquire_file_write_lock：事件循环线程
        # 上只做有界等待，避免整个进程陪着一个工作线程的长事务停摆。
        with acquire_file_write_lock(id):
            return self._edit_file_impl(id, edits, continue_on_error)

    def _edit_file_impl(
        self,
        id: str,
        edits: list[dict[str, Any]],
        continue_on_error: bool,
    ) -> dict[str, Any]:
        # Get file. On PostgreSQL take a row lock so concurrent edit_file tasks
        # (parallel_execute runs each on its own session) targeting the SAME file
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
            raise ValueError("文件不存在或已删除")

        # Check permission (target must belong to the current tool-context project)
        check_file_access_in_tool_context(self.session, file, self.user_id)

        old_content = file.content or ""
        content = old_content
        applied_edits = []
        failed_edits: list[dict[str, Any]] = []
        warnings: list[str] = []

        for i, edit in enumerate(edits):
            try:
                if not isinstance(edit, dict):
                    raise ValueError(f"Edit {i}: invalid edit object, must be JSON object")

                # Normalize op field (common LLM mistakes: op is null / uses alias keys)
                op_raw = edit.get("op")
                if op_raw is None:
                    op_raw = edit.get("operation") or edit.get("action") or edit.get("type")

                op = op_raw.strip().lower() if isinstance(op_raw, str) else ""
                op = op.replace("-", "_")

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

                # If op is still missing, try safe inference from fields.
                if not op:
                    has_old = isinstance(edit.get("old"), str) and bool(edit.get("old"))
                    has_new = isinstance(edit.get("new"), str)
                    has_anchor = isinstance(edit.get("anchor"), str) and bool(edit.get("anchor"))
                    has_text = isinstance(edit.get("text"), str) and bool(edit.get("text"))
                    pos_hint = str(edit.get("position") or edit.get("where") or "").lower()

                    inferred = None
                    if has_old and has_new:
                        inferred = "replace"
                    elif has_old and (not has_new) and (not has_anchor) and (not has_text):
                        inferred = "delete"
                    elif has_anchor and has_text:
                        if ("before" in pos_hint) or ("前" in pos_hint):
                            inferred = "insert_before"
                        elif ("after" in pos_hint) or ("后" in pos_hint):
                            inferred = "insert_after"
                        else:
                            # Default to insert_after; if multiple matches, later logic will stop safely.
                            inferred = "insert_after"
                    elif has_text and (not has_old) and (not has_anchor):
                        if ("before" in pos_hint) or ("pre" in pos_hint) or ("head" in pos_hint) or ("前" in pos_hint):
                            inferred = "prepend"
                        elif ("after" in pos_hint) or ("tail" in pos_hint) or ("后" in pos_hint):
                            inferred = "append"
                        else:
                            # Default to append for novel writing.
                            inferred = "append"

                    if inferred:
                        warnings.append(f"Edit {i}: op inferred as {inferred}")
                        op = inferred
                    else:
                        # Ignore completely empty edits (common trailing null/empty item)
                        if not any(v for v in edit.values() if v not in (None, "", [], {})):
                            warnings.append(f"Edit {i}: empty edit ignored")
                            continue
                        raise ValueError(
                            f"Edit {i}: missing op. Each edit must include op=replace/insert_after/insert_before/append/prepend/delete"
                        )

                # Normalize common field aliases
                if "old" not in edit and isinstance(edit.get("from"), str):
                    edit["old"] = edit.get("from")
                if "new" not in edit and isinstance(edit.get("to"), str):
                    edit["new"] = edit.get("to")
                if "text" not in edit and isinstance(edit.get("content"), str):
                    edit["text"] = edit.get("content")

                # Persist normalized op for subsequent logic
                edit["op"] = op

                if op == "replace":
                    content = self._apply_replace(
                        content, edit, i, applied_edits, warnings
                    )
                elif op == "insert_after":
                    content = self._apply_insert_after(
                        content, edit, i, applied_edits, warnings
                    )
                elif op == "insert_before":
                    content = self._apply_insert_before(
                        content, edit, i, applied_edits, warnings
                    )
                elif op == "append":
                    text = edit.get("text", "")
                    content = content + text
                    applied_edits.append({
                        "op": op,
                        "text_len": len(text),
                        "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                    })
                elif op == "prepend":
                    text = edit.get("text", "")
                    content = text + content
                    applied_edits.append({
                        "op": op,
                        "text_len": len(text),
                        "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                    })
                elif op == "delete":
                    content = self._apply_delete(
                        content, edit, i, applied_edits, warnings
                    )
                else:
                    raise ValueError(f"Edit {i}: unknown operation '{op}'. Valid ops: replace, insert_after, insert_before, append, prepend, delete")
            except Exception as e:
                if not continue_on_error:
                    raise
                failed_op = str(edit.get("op", "")).strip() if isinstance(edit, dict) else ""
                failed_edits.append({
                    "index": i,
                    "error": str(e),
                    "op": failed_op,
                })
                warnings.append(f"Edit {i}: failed and skipped ({e})")

        # Stage content and snapshot in the same transaction while the per-file
        # lock is held. A savepoint keeps snapshot failures non-blocking without
        # allowing content and history to describe different writes.
        undo: dict[str, Any] | None = None
        if content != old_content:
            before_version_number: int | None = None
            try:
                # Provenance is optional, but its read failure must not poison
                # the outer PostgreSQL content transaction. A loaded history
                # head is usable only if it exactly reconstructs the live input.
                with self.session.begin_nested():
                    version_service = FileVersionService()
                    latest = version_service.get_latest_version(self.session, id)
                    if latest and version_service._get_contents_for_versions(self.session, {id: latest})[id] == old_content:
                        before_version_number = latest.version_number
            except Exception:
                logger.warning("Unable to anchor edit undo; content edit will continue", exc_info=True, extra={"file_id": id})

            file.content = content
            file.updated_at = advance_timestamp(file.updated_at, now=utcnow())
            # Commit releases the PostgreSQL lock; a subsequent refresh may
            # observe another writer. Undo must retain this edit's own token.
            after_updated_at = normalize_datetime_to_utc(file.updated_at).isoformat()
            try:
                with self.session.begin_nested():
                    self._create_edit_version(id, content, applied_edits)
            except Exception as exc:
                logger.warning(
                    "Failed to create version for edit_file; content will persist",
                    exc_info=True,
                    extra={"file_id": id, "error": str(exc)},
                )
            self.session.commit()
            self.session.refresh(file)
            if before_version_number is not None:
                undo = {
                    "before_version_number": before_version_number,
                    "expected_after_updated_at": after_updated_at,
                }
            activation_event_service.record_ai_write_accepted(
                self.session,
                user_id=self.user_id,
                project_id=file.project_id,
                file_id=file.id,
                file_type=file.file_type,
                tool="edit_file",
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
            "details": applied_edits,
            "failed_edits": failed_edits,
            "partial_success": bool(applied_edits and failed_edits),
            "all_failed": bool(failed_edits and not applied_edits),
            "warnings": warnings,
        }
        if undo is not None:
            result["undo"] = undo
        return result

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
    ) -> int:
        """Resolve the start index for an exact-substring op.

        Enforces the same uniqueness/occurrence guards the fuzzy path already
        applies, so the exact-match fast path can no longer silently edit the
        first of several occurrences or ignore a caller-supplied ``occurrence``:

        - ``occurrence`` is None and ``sub`` appears more than once -> abort
          (ambiguous) instead of editing the first occurrence.
        - ``occurrence`` provided -> validate its range and select that
          (1-based, non-overlapping) occurrence.
        """
        if occurrence is None:
            if match_count > 1:
                previews = _numbered_previews(content, _exact_spans(content, sub))
                raise ValueError(
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

    def _apply_replace(
        self,
        content: str,
        edit: dict[str, Any],
        edit_index: int,
        applied_edits: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        """Apply a replace edit operation."""
        old_text = edit.get("old", "")
        new_text = edit.get("new", "")
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

        if not old_text:
            warnings.append(f"Edit {edit_index}: missing old for replace; skipped")
            return content

        if not isinstance(new_text, str):
            warnings.append(f"Edit {edit_index}: invalid new for replace; skipped")
            return content

        # 1) Exact match first
        if old_text in content:
            if replace_all:
                count = content.count(old_text)
                content = content.replace(old_text, new_text)
                applied_edits.append({
                    "op": "replace",
                    "match_mode": "exact",
                    "old_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
                    "new_preview": new_text[:200] + ("..." if len(new_text) > 200 else ""),
                    "count": count,
                })
            else:
                match_count = content.count(old_text)
                # 与 insert_* 共用同一套守卫：occurrence 未指定且有多处匹配时
                # 中止（并推荐 occurrence=N 这条非破坏性出路），指定了就精确
                # 定位到第 N 处，而不是像以前那样把 occurrence 整个忽略、只留
                # replace_all 这一条破坏性逃生通道。
                start = self._select_exact_occurrence_start(
                    content,
                    old_text,
                    occurrence,
                    match_count,
                    edit_index,
                    label="原文片段",
                    extra_hint="（确实要把这几处全部替换时，才使用 replace_all=true）",
                )
                content = content[:start] + new_text + content[start + len(old_text):]
                detail = {
                    "op": "replace",
                    "match_mode": "exact",
                    "match_count": match_count,
                    "old_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
                    "new_preview": new_text[:200] + ("..." if len(new_text) > 200 else ""),
                }
                if occurrence is not None:
                    detail["occurrence"] = occurrence
                applied_edits.append(detail)
        else:
            if match_mode == "exact":
                raise ValueError(
                    f"Edit {edit_index}: old text not found in content (exact match)"
                )

            # 2) Fuzzy match (ignore punctuation/whitespace)
            # replace_all mirrors the exact path (which replaces EVERY
            # occurrence), so it must not stop at the default 20-match cap.
            fuzzy_stats: dict[str, int] = {}
            spans = find_fuzzy_spans(
                content,
                old_text,
                ignore_punct_whitespace=ignore_punct_whitespace,
                max_matches=REPLACE_ALL_MAX_FUZZY_MATCHES if replace_all else 20,
                stats=fuzzy_stats,
            )
            if fuzzy_stats.get("boundary_rejected"):
                warnings.append(
                    f"Edit {edit_index}: {fuzzy_stats['boundary_rejected']} 处候选匹配因字符归一化展开边界不对齐被跳过（如罗马数字/合字），已避免吞掉未匹配的原文"
                )

            # 3) If fuzzy match fails, try approximate match (handles word errors)
            approx_match = None
            if not spans:
                approx_match = find_approximate_match(
                    content,
                    old_text,
                    max_error_rate=0.25,  # Allow up to 25% character difference
                    min_pattern_len=8,
                )
                if approx_match:
                    start, end, similarity, matched_text = approx_match
                    # 近似匹配只会给出唯一的一处；调用方若点名了第 2 处及以后，
                    # 说明它以为文中有多处，此时套用这唯一一处就是改错位置。
                    if occurrence is not None and occurrence != 1:
                        raise ValueError(
                            f"Edit {edit_index}: 近似匹配只找到 1 处，"
                            f"occurrence out of range (1..1)"
                        )
                    # Single approximate match - use it
                    content = content[:start] + new_text + content[end:]
                    applied_edits.append({
                        "op": "replace",
                        "match_mode": "approximate",
                        "similarity": round(similarity, 3),
                        "matched_original": matched_text[:200] + ("..." if len(matched_text) > 200 else ""),
                        "old_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
                        "new_preview": new_text[:200] + ("..." if len(new_text) > 200 else ""),
                    })
                    return content

            if not spans and not approx_match:
                suggestions = suggest_similar_lines(
                    content,
                    old_text,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
                raise ValueError(
                    f"Edit {edit_index}: 找不到要替换的原文片段。请从当前文件原文中复制更长且唯一的原文。候选片段: {suggestions}"
                )

            if replace_all:
                # Stitch segments in one pass (spans are sorted and
                # non-overlapping); repeated re-slicing would be O(n·k).
                parts: list[str] = []
                prev = 0
                for start, end in spans:
                    parts.append(content[prev:start])
                    parts.append(new_text)
                    prev = end
                parts.append(content[prev:])
                content = "".join(parts)

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
                    "old_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
                    "new_preview": new_text[:200] + ("..." if len(new_text) > 200 else ""),
                    "count": len(spans),
                }
                if truncated:
                    detail["truncated"] = True
                applied_edits.append(detail)
            else:
                if len(spans) != 1 and occurrence is None:
                    previews = _numbered_previews(content, spans)
                    raise ValueError(
                        f"Edit {edit_index}: 原文片段匹配到多个位置（{len(spans)}处），"
                        f"为避免误改已中止。推荐做法：保持原参数不变，加上 occurrence=N "
                        f"指定要改第几处（1-based，见下方候选片段序号）；或提供更长且更唯一的原文/锚点。"
                        f"（确实要把这几处全部替换时，才使用 replace_all=true）候选片段: {previews}"
                    )

                idx = 0
                if occurrence is not None:
                    if occurrence > len(spans):
                        raise ValueError(
                            f"Edit {edit_index}: occurrence out of range (1..{len(spans)})"
                        )
                    idx = occurrence - 1

                start, end = spans[idx]
                content = content[:start] + new_text + content[end:]
                detail = {
                    "op": "replace",
                    "match_mode": "fuzzy",
                    "ignore_punct_whitespace": ignore_punct_whitespace,
                    "old_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
                    "new_preview": new_text[:200] + ("..." if len(new_text) > 200 else ""),
                    "match_count": len(spans),
                }
                if occurrence is not None:
                    detail["occurrence"] = occurrence
                applied_edits.append(detail)

        return content

    def _apply_insert_after(
        self,
        content: str,
        edit: dict[str, Any],
        edit_index: int,
        applied_edits: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        """Apply an insert_after edit operation."""
        anchor = edit.get("anchor", "")
        text = edit.get("text", "")

        match_mode = str(edit.get("match_mode") or "auto").strip().lower()
        ignore_punct_whitespace = self._parse_ignore_punct_whitespace(edit)
        occurrence = self._parse_occurrence(edit, edit_index)

        if not anchor:
            raise ValueError(f"Edit {edit_index}: 'anchor' field is required for insert_after operation")

        if anchor in content:
            match_count = content.count(anchor)
            start = self._select_exact_occurrence_start(
                content, anchor, occurrence, match_count, edit_index, label="锚点"
            )
            pos = start + len(anchor)
            content = content[:pos] + text + content[pos:]
            applied_edits.append({
                "op": "insert_after",
                "match_mode": "exact",
                "match_count": match_count,
                "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                "text_len": len(text),
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
            })
        else:
            if match_mode == "exact":
                raise ValueError(
                    f"Edit {edit_index}: anchor text not found in content (exact match)"
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
            if not spans:
                # Secondary fallback: approximate match (handles word errors)
                approx_match = find_approximate_match(
                    content,
                    anchor,
                    max_error_rate=0.25,
                    min_pattern_len=8,
                )
                if approx_match:
                    start, end, similarity, matched_text = approx_match
                    if occurrence is not None and occurrence != 1:
                        raise ValueError(
                            f"Edit {edit_index}: 近似匹配只找到 1 处锚点，"
                            f"occurrence out of range (1..1)"
                        )
                    pos = end  # Insert after the matched text
                    content = content[:pos] + text + content[pos:]
                    applied_edits.append({
                        "op": "insert_after",
                        "match_mode": "approximate",
                        "similarity": round(similarity, 3),
                        "matched_original": matched_text[:200] + ("..." if len(matched_text) > 200 else ""),
                        "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                        "text_len": len(text),
                        "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                    })
                    return content

                # Tertiary fallback: locate a unique best paragraph
                block_span = find_unique_line_span(
                    content,
                    anchor,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
                if block_span:
                    start, end, block_score = block_span
                    pos = end
                    content = content[:pos] + text + content[pos:]
                    # fuzzy_paragraph 是「最像的整段」而不是逐字命中，必须把
                    # 置信度与兜底性质写进详情，否则模型只看到 match_count=1，
                    # 会误以为这是确定无疑的唯一匹配而不再复核。
                    applied_edits.append({
                        "op": "insert_after",
                        "match_mode": "fuzzy_paragraph",
                        "ignore_punct_whitespace": ignore_punct_whitespace,
                        "match_count": 1,
                        "confidence": round(block_score, 3),
                        "fallback": True,
                        "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                        "text_len": len(text),
                        "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                    })
                    return content

                suggestions = suggest_similar_lines(
                    content,
                    anchor,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
                raise ValueError(
                    f"Edit {edit_index}: 找不到插入锚点。请从当前文件原文中复制更长且唯一的锚点。候选片段: {suggestions}"
                )

            if len(spans) != 1 and occurrence is None:
                previews = _numbered_previews(content, spans)
                raise ValueError(
                    f"Edit {edit_index}: 锚点匹配到多个位置（{len(spans)}处），"
                    f"为避免插入到错误位置已中止。推荐做法：保持原参数不变，加上 occurrence=N "
                    f"指定第几处（1-based，见下方候选片段序号）；或提供更长且更唯一的锚点。"
                    f"候选片段: {previews}"
                )

            idx = 0
            if occurrence is not None:
                if occurrence > len(spans):
                    raise ValueError(
                        f"Edit {edit_index}: occurrence out of range (1..{len(spans)})"
                    )
                idx = occurrence - 1

            start, end = spans[idx]
            pos = end
            content = content[:pos] + text + content[pos:]
            applied_edits.append({
                "op": "insert_after",
                "match_mode": "fuzzy",
                "ignore_punct_whitespace": ignore_punct_whitespace,
                "match_count": len(spans),
                "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                "text_len": len(text),
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
            })

        return content

    def _apply_insert_before(
        self,
        content: str,
        edit: dict[str, Any],
        edit_index: int,
        applied_edits: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        """Apply an insert_before edit operation."""
        anchor = edit.get("anchor", "")
        text = edit.get("text", "")

        match_mode = str(edit.get("match_mode") or "auto").strip().lower()
        ignore_punct_whitespace = self._parse_ignore_punct_whitespace(edit)
        occurrence = self._parse_occurrence(edit, edit_index)

        if not anchor:
            raise ValueError(f"Edit {edit_index}: 'anchor' field is required for insert_before operation")

        if anchor in content:
            match_count = content.count(anchor)
            pos = self._select_exact_occurrence_start(
                content, anchor, occurrence, match_count, edit_index, label="锚点"
            )
            content = content[:pos] + text + content[pos:]
            applied_edits.append({
                "op": "insert_before",
                "match_mode": "exact",
                "match_count": match_count,
                "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                "text_len": len(text),
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
            })
        else:
            if match_mode == "exact":
                raise ValueError(
                    f"Edit {edit_index}: anchor text not found in content (exact match)"
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
            if not spans:
                # Secondary fallback: approximate match (handles word errors)
                approx_match = find_approximate_match(
                    content,
                    anchor,
                    max_error_rate=0.25,
                    min_pattern_len=8,
                )
                if approx_match:
                    start, end, similarity, matched_text = approx_match
                    if occurrence is not None and occurrence != 1:
                        raise ValueError(
                            f"Edit {edit_index}: 近似匹配只找到 1 处锚点，"
                            f"occurrence out of range (1..1)"
                        )
                    pos = start  # Insert before the matched text
                    content = content[:pos] + text + content[pos:]
                    applied_edits.append({
                        "op": "insert_before",
                        "match_mode": "approximate",
                        "similarity": round(similarity, 3),
                        "matched_original": matched_text[:200] + ("..." if len(matched_text) > 200 else ""),
                        "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                        "text_len": len(text),
                        "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                    })
                    return content

                # Tertiary fallback: locate a unique best paragraph
                block_span = find_unique_line_span(
                    content,
                    anchor,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
                if block_span:
                    start, end, block_score = block_span
                    pos = start
                    content = content[:pos] + text + content[pos:]
                    # 同 insert_after：兜底段落匹配必须自报置信度，不能伪装成确定匹配。
                    applied_edits.append({
                        "op": "insert_before",
                        "match_mode": "fuzzy_paragraph",
                        "ignore_punct_whitespace": ignore_punct_whitespace,
                        "match_count": 1,
                        "confidence": round(block_score, 3),
                        "fallback": True,
                        "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                        "text_len": len(text),
                        "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                    })
                    return content

                suggestions = suggest_similar_lines(
                    content,
                    anchor,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
                raise ValueError(
                    f"Edit {edit_index}: 找不到插入锚点。请从当前文件原文中复制更长且唯一的锚点。候选片段: {suggestions}"
                )

            if len(spans) != 1 and occurrence is None:
                previews = _numbered_previews(content, spans)
                raise ValueError(
                    f"Edit {edit_index}: 锚点匹配到多个位置（{len(spans)}处），"
                    f"为避免插入到错误位置已中止。推荐做法：保持原参数不变，加上 occurrence=N "
                    f"指定第几处（1-based，见下方候选片段序号）；或提供更长且更唯一的锚点。"
                    f"候选片段: {previews}"
                )

            idx = 0
            if occurrence is not None:
                if occurrence > len(spans):
                    raise ValueError(
                        f"Edit {edit_index}: occurrence out of range (1..{len(spans)})"
                    )
                idx = occurrence - 1

            start, end = spans[idx]
            pos = start
            content = content[:pos] + text + content[pos:]
            applied_edits.append({
                "op": "insert_before",
                "match_mode": "fuzzy",
                "ignore_punct_whitespace": ignore_punct_whitespace,
                "match_count": len(spans),
                "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                "text_len": len(text),
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
            })

        return content

    def _apply_delete(
        self,
        content: str,
        edit: dict[str, Any],
        edit_index: int,
        applied_edits: list[dict[str, Any]],
        warnings: list[str],
    ) -> str:
        """Apply a delete edit operation."""
        old_text = edit.get("old", "")

        match_mode = str(edit.get("match_mode") or "auto").strip().lower()
        ignore_punct_whitespace = self._parse_ignore_punct_whitespace(edit)
        occurrence = self._parse_occurrence(edit, edit_index)

        if not old_text:
            warnings.append(f"Edit {edit_index}: missing old for delete; skipped")
            return content

        if old_text in content:
            match_count = content.count(old_text)
            # 与 replace/insert_* 一致：未指定 occurrence 且多处匹配时中止，
            # 指定了就精确删掉第 N 处（delete 没有 replace_all，以前这条路
            # 完全是死胡同——模型除了重写锚点别无出路）。
            start = self._select_exact_occurrence_start(
                content,
                old_text,
                occurrence,
                match_count,
                edit_index,
                label="删除片段",
            )
            content = content[:start] + content[start + len(old_text):]
            detail = {
                "op": "delete",
                "match_mode": "exact",
                "match_count": match_count,
                "deleted_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
            }
            if occurrence is not None:
                detail["occurrence"] = occurrence
            applied_edits.append(detail)
        else:
            if match_mode == "exact":
                raise ValueError(
                    f"Edit {edit_index}: text to delete not found in content (exact match)"
                )

            fuzzy_stats: dict[str, int] = {}
            spans = find_fuzzy_spans(
                content,
                old_text,
                ignore_punct_whitespace=ignore_punct_whitespace,
                stats=fuzzy_stats,
            )
            if fuzzy_stats.get("boundary_rejected"):
                warnings.append(
                    f"Edit {edit_index}: {fuzzy_stats['boundary_rejected']} 处候选匹配因字符归一化展开边界不对齐被跳过（如罗马数字/合字），已避免误删原文"
                )
            if not spans:
                suggestions = suggest_similar_lines(
                    content,
                    old_text,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
                raise ValueError(
                    f"Edit {edit_index}: 找不到要删除的原文片段。请从当前文件原文中复制更长且唯一的原文。候选片段: {suggestions}"
                )

            if len(spans) != 1 and occurrence is None:
                previews = _numbered_previews(content, spans)
                raise ValueError(
                    f"Edit {edit_index}: 删除片段匹配到多个位置（{len(spans)}处），"
                    f"为避免误删已中止。推荐做法：保持原参数不变，加上 occurrence=N "
                    f"指定要删第几处（1-based，见下方候选片段序号）；或提供更长且更唯一的原文/锚点。"
                    f"候选片段: {previews}"
                )

            idx = 0
            if occurrence is not None:
                if occurrence > len(spans):
                    raise ValueError(
                        f"Edit {edit_index}: occurrence out of range (1..{len(spans)})"
                    )
                idx = occurrence - 1

            start, end = spans[idx]
            content = content[:start] + content[end:]
            detail = {
                "op": "delete",
                "match_mode": "fuzzy",
                "ignore_punct_whitespace": ignore_punct_whitespace,
                "deleted_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
                "match_count": len(spans),
            }
            if occurrence is not None:
                detail["occurrence"] = occurrence
            applied_edits.append(detail)

        return content

    def _create_edit_version(
        self,
        file_id: str,
        content: str,
        applied_edits: list[dict[str, Any]],
    ) -> None:
        """Stage AI edit history in the caller's content transaction."""
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
    "FileEditor",
    "FileWriteBusyError",
    "acquire_file_write_lock",
    "file_write_lock",
]
