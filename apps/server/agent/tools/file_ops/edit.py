"""
File edit operations for agent tools.

This module provides precise file editing operations:
- edit_file: Apply multiple edit operations (replace/insert/append/prepend/delete)

Supports fuzzy and approximate text matching for robust editing even when
the LLM provides slightly different text.
"""

import asyncio
import threading
from collections.abc import Callable, Iterator
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
from services.features.file_version_service import BEFORE_AI_EDIT_SUMMARY
from utils.cjk_quotes import (
    QUOTE_STYLE_CURLY,
    detect_quote_style,
    has_style_quote_marks,
    normalize_inserted_spans,
    resolve_quote_style,
)
from utils.logger import get_logger, log_with_context
from utils.text_metrics import count_words

from .paragraph_layout import (
    NOTE_INDENT_FOLLOWS_FILE,
    NOTE_SENTENCE_SNAP,
    exact_anchor_snap_forward,
    follow_span_indentation,
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

EDIT_ERROR_USER_MESSAGES: dict[str, str] = {
    EDIT_ERROR_ANCHOR_NOT_FOUND: "AI 没在原文里找到要改的那一段，正在重新定位。",
    EDIT_ERROR_ANCHOR_AMBIGUOUS: "要改的这句话在文中出现了好几次，AI 正在确认是哪一处。",
    EDIT_ERROR_FILE_NOT_FOUND: "这个文件已经不存在了，可能刚被删除。",
    EDIT_ERROR_STALE_CONTENT: "这个文件在 AI 读过之后又被改过（多半是你刚改的），AI 没有在旧内容上改，这一步没有改动。",
}
EDIT_ERROR_GENERIC_USER_MESSAGE = "这一步没做成，AI 会换个方式继续。"

# 近似/模糊匹配改动后附在 detail 上的提醒（模型与作者都能看到实际改了哪段原文）。
FUZZY_EDIT_WARNING = "已按近似匹配改动，请核对"


def edit_error_user_message(error_type: str | None) -> str:
    """error_type 对应的作者可读短句；未知类型用通用说法。"""
    return EDIT_ERROR_USER_MESSAGES.get(error_type or "", EDIT_ERROR_GENERIC_USER_MESSAGE)


class EditFileError(ValueError):
    """edit_file 的可恢复错误：str(e) 给模型，error_type / user_message 给界面。

    继承 ValueError：按 ValueError 捕获的调用方（含测试）照常接住。
    """

    def __init__(self, message: str, *, error_type: str = EDIT_ERROR_INVALID_EDIT):
        super().__init__(message)
        self.error_type = error_type

    @property
    def user_message(self) -> str:
        return edit_error_user_message(self.error_type)

    def payload_fields(self) -> dict[str, Any]:
        """附加到工具错误结果上的结构化字段。"""
        return {
            "error_type": self.error_type,
            "user_message": self.user_message,
            "edits_applied": 0,
            "mutation_applied": False,
        }


class StaleContentError(EditFileError):
    """模型读到的正文和锁内重读的当前正文不一致：不写库、不重试，把新改动告诉模型。

    complete：读过之后的改动都完整列出来了（处数没超、没有因为太长只显示改动附近）。这时调用方可以把
    「模型看到的正文」更新为当前正文；否则要模型先读全文。
    """

    MAX_LISTED = 6

    def __init__(self, seen: str, current: str):
        self.current_content = current
        changes, truncated = _line_changes(seen, current)
        self.complete = len(changes) <= self.MAX_LISTED and not truncated
        listed = "\n".join(changes[: self.MAX_LISTED])
        if self.complete:
            tail = "请基于现在的正文重新给出修改，新改的地方保留，不要改回去。"
        else:
            tail = (
                "改动没有全部列出（不完整）。先用 query_files 按 id 读一次全文，"
                "再基于现在的正文给出修改，新改的地方保留。"
            )
        super().__init__(
            "这个文件在你读过之后又被改过（可能是作者刚在编辑器里改的，也可能是本请求里别的步骤写的），"
            f"这次修改没有执行，一处都没改。读过之后的改动：\n{listed}\n{tail}",
            error_type=EDIT_ERROR_STALE_CONTENT,
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

    def __init__(self, failed_edits: list[dict[str, Any]], edits_total: int):
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
                "确认不了就先用 query_files 按 id 读取相关段落。"
            )
        first_type = failed_edits[0].get("error_type") if failed_edits else None
        super().__init__(message, error_type=str(first_type or EDIT_ERROR_GENERIC))

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


def _preview(text: str) -> str:
    return text[:200] + ("..." if len(text) > 200 else "")


def _paragraph_break_error(edit_index: int, label: str) -> EditFileError:
    return _not_found(
        f"Edit {edit_index}: {label}在原文里对应的那段跨过了段落分隔（原文这里有换行），"
        f"{label}里却没有换行；照这样改会把两段并成一段，已中止。请按原文的分段把换行"
        f"（以及下一段段首的缩进）照抄进 old 和 new，或者拆成每段一条 edit。"
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


# 应用内 agent 写正文时才做引号规范化；大纲、角色卡、设定里的引号往往是在
# 引用术语或英文原文，不属于对白体例，保持原样。
NORMALIZED_QUOTE_FILE_TYPES = frozenset({"draft", "script"})


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
    写入（沿用「版本失败正文照存」的语义）。

    这里也是 AI 覆盖正文前唯一的必经点，顺带记下本请求第一次写入前的正文，
    送审时据此只附本请求改动的那一段（见 ToolContext.remember_pre_write_content）。
    """
    remember_pre_write_content(file_id, current_content)
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


def attach_author_edits_for_agent(session: Session, file: Any, row: dict[str, Any]) -> None:
    """应用内 agent 按 id 读全文时，在返回行里附上作者在 AI 之后的手改（agent/tools/author_changes.py）。

    只在请求上下文里有快照容器（应用内对话）时算，外部 Agent API 与列表查询不受影响；出错时什么也不加。
    """
    try:
        from agent.tools.mcp_tools import ToolContext

        if not ToolContext.tracks_reads():
            return
        from agent.tools.author_changes import author_changes, author_edits_payload

        changes = author_changes(session, file)
        if changes is not None:
            row["author_edits"] = author_edits_payload(changes)
    except Exception as exc:  # pragma: no cover - 防御：辅助信息不能拖垮读取
        logger.debug("Failed to attach author edits", extra={"file_id": getattr(file, "id", ""), "error": str(exc)})


@contextmanager
def written_snapshot_for_commit(file_id: str, content: str | None, *, full: bool = False) -> Iterator[None]:
    """包住本请求的提交：提交前就把快照更新成要写下的正文，提交失败再还原。

    PostgreSQL 上提交即放锁，同一文件的下一个并行写入立刻拿到锁、在锁内比对快照；
    提交之后才记快照，它会把这次自己的写入误判成「别人改了」。没有请求上下文时什么也不做。
    """
    from agent.tools.mcp_tools import ToolContext

    state = None
    try:
        state = ToolContext.written_snapshot_state(file_id)
        ToolContext.record_written_snapshot(file_id, content, full=full)
    except Exception as exc:  # pragma: no cover - 防御：辅助信息不能拖垮写入
        logger.debug("Failed to record written snapshot", extra={"file_id": file_id, "error": str(exc)})
    try:
        yield
    except BaseException:
        try:
            ToolContext.restore_written_snapshot(file_id, state)
        except Exception as exc:  # pragma: no cover
            logger.debug("Failed to restore written snapshot", extra={"file_id": file_id, "error": str(exc)})
        raise


def remember_pre_write_content(file_id: str, content: str | None) -> None:
    """记下本请求第一次写入前的正文；失败只记 DEBUG，不影响写入。"""
    try:
        from agent.tools.mcp_tools import ToolContext

        ToolContext.remember_pre_write_content(file_id, content)
    except Exception as exc:  # pragma: no cover - 防御：送审辅助信息不能拖垮写入
        logger.debug("Failed to remember pre-write content", extra={"file_id": file_id, "error": str(exc)})


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
        normalize_quotes: bool = False,
        read_snapshot_fn: Callable[[], str | None] | None = None,
        exact_only: bool = False,
        report_author_edits: bool = False,
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
            "report_author_edits": report_author_edits,
        }
        if is_postgres:
            return self._edit_file_impl(id, edits, continue_on_error, normalize_quotes, **options)
        # SQLite has no row locks: serialize same-file read-modify-write with
        # the in-process per-file lock so a concurrent edit (parallel_execute
        # runs each task on its own session/thread) cannot interleave between
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
        report_author_edits: bool = False,
    ) -> dict[str, Any]:
        # Get file. On PostgreSQL take a row lock so concurrent edit_file tasks
        # (parallel_execute runs each on its own session) targeting the SAME file
        # serialize: the second locked SELECT blocks until the first commits and
        # then re-reads the updated content, preventing a lost update where the
        # later commit overwrites the earlier edit. FOR NO KEY UPDATE (not FOR
        # UPDATE) so the version snapshot's FK insert from its independent
        # session (KEY SHARE on this row) is not blocked by the held lock.
        from database import is_postgres

        # 作者在 AI 之后亲手写的段落（改到了要如实告诉模型和作者）；只在本应用的 agent 写入时算。
        # 回放版本链较慢，放在加锁之前，不拖长同一文件上其它写入的等待；锁内正文变了再重算。
        author_blame = None
        blamed_content: str | None = None
        if report_author_edits:
            blamed_content = self.session.exec(select(File.content).where(File.id == id)).first()
            if blamed_content is not None:
                author_blame = self._author_blame(id, blamed_content)

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
            raise EditFileError("文件不存在或已删除", error_type=EDIT_ERROR_FILE_NOT_FOUND)

        # Check permission (target must belong to the current tool-context project)
        check_file_access_in_tool_context(self.session, file, self.user_id)

        old_content = file.content or ""
        # 锁内重读的正文和模型看到的不一样：作者（或别的写入）在模型读过之后又改了。
        # 不在旧正文上做近似匹配，否则作者刚改的字可能被模型的旧写法覆盖。快照在锁内取：
        # 本请求里先拿到锁的那次写入已经把快照更新成它写下的正文，不会误判成别人改的。
        read_snapshot = read_snapshot_fn() if read_snapshot_fn is not None else None
        if read_snapshot is not None and read_snapshot != old_content:
            raise StaleContentError(read_snapshot, old_content)
        if report_author_edits and blamed_content != old_content:
            author_blame = self._author_blame(file.id, old_content)
        content = old_content
        applied_edits = []
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
                if exact_only and op in ("replace", "delete", "insert_after", "insert_before"):
                    # 本请求没读过这个文件：old / anchor 只按逐字匹配，不拿可能过时的记忆去
                    # 模糊命中作者刚改过的字（模型给了 match_mode 也一样）。
                    edit["match_mode"] = "exact"

                if op in ("append", "prepend", "insert_after", "insert_before"):
                    self._require_insert_text(edit, i, op, warnings)

                content_before_edit = content
                applied_before_edit = len(applied_edits)

                old_field = edit.get("old")
                if op == "replace" and isinstance(old_field, str) and old_field and self._peek_replace_new(edit) == old_field:
                    # old 与 new 相同就是不改。只核对原文里逐字（含引号写法等价）有这段，不走模糊/近似
                    # 定位：那条路按原文边缘标点扩展范围，会把模型漏抄的逗号、句号删掉还报成功（F-01）。
                    if locate_exact_or_quote_equivalent(content, old_field) is None:
                        suggestions = suggest_similar_lines(content, old_field)
                        raise _not_found(
                            f"Edit {i}: old 与 new 相同，但原文里没有逐字对应的这段（标点也要一致），没有修改。"
                            f"不需要改这里就去掉这条；要改请从当前文件原文中复制。候选片段: {suggestions}"
                        )
                    # 模型重复确认已完成的改动：如实报未修改，但不算失败（否则它会原样重发到熔断）。
                    unchanged_edits.append({"index": i, "op": op, "reason": "already_matches"})
                    warnings.append(f"Edit {i}: old 与 new 相同，原文这里已经是这样，没有修改，不用重发")
                    continue

                if op == "replace":
                    content = self._apply_replace(
                        content, edit, i, applied_edits, warnings, quote_style=quote_style
                    )
                elif op == "insert_after":
                    content = self._apply_insert_after(
                        content, edit, i, applied_edits, warnings, quote_style=quote_style
                    )
                elif op == "insert_before":
                    content = self._apply_insert_before(
                        content, edit, i, applied_edits, warnings, quote_style=quote_style
                    )
                elif op in ("append", "prepend"):
                    notes: list[str] = []
                    pos, text = self._position_insert(
                        content,
                        len(content) if op == "append" else 0,
                        edit.get("text", ""),
                        direction="after" if op == "append" else "before",
                        snap_sentence=False,
                        notes=notes,
                    )
                    content, text = self._place_new_text(content, pos, pos, text, quote_style)
                    detail = {
                        "op": op,
                        "text_len": len(text),
                        "text_words": count_words(text),
                        "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                    }
                    if notes:
                        detail["layout_notes"] = notes
                    applied_edits.append(detail)
                elif op == "delete":
                    content = self._apply_delete(
                        content, edit, i, applied_edits, warnings
                    )
                else:
                    raise ValueError(f"Edit {i}: unknown operation '{op}'. Valid ops: replace, insert_after, insert_before, append, prepend, delete")

                if content == content_before_edit and len(applied_edits) > applied_before_edit:
                    # 报告成功就必须真的改了：撤回这一条的 applied 记录。
                    del applied_edits[applied_before_edit:]
                    raise _no_change_error(i, edit, quote_style)
            except Exception as e:
                failed_op = str(edit.get("op", "")).strip() if isinstance(edit, dict) else ""
                error_text = str(e)
                if not error_text.startswith(f"Edit {i}"):
                    error_text = f"Edit {i}: {error_text}"
                if exact_only and failed_op in ("replace", "delete", "insert_after", "insert_before"):
                    error_text += "（本请求还没读过这个文件，只按逐字匹配：先用 query_files 按 id 读一次，再照现在的正文改）"
                failed_edits.append({
                    "index": i,
                    "error": error_text,
                    "error_type": _classify_edit_exception(e),
                    "op": failed_op,
                })
                if continue_on_error:
                    warnings.append(f"Edit {i}: failed and skipped ({e})")
                # continue_on_error=false 时也继续检查后面的编辑（只在内存里），
                # 一次把所有失败项报给模型；循环结束后整批回滚，不会落库。

        if failed_edits and not continue_on_error:
            # 整批不生效：不提交、不建版本。错误里写明「全部未生效」并列出全部
            # 失败项，否则模型会以为只有报错那一条失败，只重发剩下的几条。
            raise EditBatchError(failed_edits, edits_total=len(edits))

        # Stage content and snapshot in the same transaction while the per-file
        # lock is held. A savepoint keeps snapshot failures non-blocking without
        # allowing content and history to describe different writes.
        undo: dict[str, Any] | None = None
        if content != old_content:
            # 原稿可能是作者手动改过、还没进历史的正文：先备份（独立 savepoint，
            # 失败不阻断），撤销锚点随后就能落在这份备份上。
            stage_pre_ai_write_backup(self.session, id, old_content)
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
            with written_snapshot_for_commit(file.id, content):
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
        if content != old_content and author_blame is not None:
            from agent.tools.author_changes import touched_author_text

            touched = touched_author_text(author_blame, old_content, content)
            if touched:
                result["author_edits_touched"] = touched
        return result

    def _author_blame(self, file_id: str, content: str) -> Any:
        """content 每一段是谁写的；出错时返回 None（只是少了「改到了作者的字」的提示）。

        在 savepoint 里读：可选信息的查询出错不能让写事务失效。
        """
        try:
            from agent.tools.author_changes import line_blame

            with self.session.begin_nested():
                return line_blame(self.session, file_id, content)
        except Exception:
            logger.warning("Author blame failed; edit continues without it", exc_info=True, extra={"file_id": file_id})
            return None

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
        content: str, start: int, end: int, old_text: str, edit_index: int
    ) -> None:
        """近似命中的范围不能在句子中间开始或结束。

        old 的边缘是标点（以句号收尾、以引号开头），命中范围扩展后的边缘却是
        一个字，说明窗口停在了句子中间；照样替换/删除会留下半句话或吞掉半句。
        这种时候宁可报错让模型复制完整原文，也不猜着多删几个字。
        """
        stripped = old_text.strip()
        if not stripped or start >= end:
            return
        if _edge_punct_class(stripped[-1]) is not None and _edge_punct_class(content[end - 1]) is None:
            where = "结束"
        elif _edge_punct_class(stripped[0]) is not None and _edge_punct_class(content[start]) is None:
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
        if edit.get("text") is None:
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
                f"只想删除这段原文请改用 op=delete。"
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

    def _apply_replace(
        self,
        content: str,
        edit: dict[str, Any],
        edit_index: int,
        applied_edits: list[dict[str, Any]],
        warnings: list[str],
        *,
        quote_style: str | None = None,
    ) -> str:
        """Apply a replace edit operation."""
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
        if (
            quote_style is not None
            and new_text != old_text
            and fold_double_quotes(new_text) == fold_double_quotes(old_text)
        ):
            # 只改引号写法的编辑就是在要求换体例（如「」改成 “”）。再按稿件体例
            # 规范化，半角引号会被改回原样，正文一字不变却报成功（U079）。半角
            # 引号此时按产品默认的 “” 写入；new 里直接写的 「」/“” 原样保留。
            quote_style = QUOTE_STYLE_CURLY

        # 1) Exact match first, then exact modulo double-quote style
        located = locate_exact_or_quote_equivalent(content, old_text)
        if located is not None:
            exact_mode, haystack, needle = located
            if replace_all:
                spans = _exact_spans(haystack, needle, limit=len(haystack) + 1)
                count = len(spans)
                parts: list[str] = []
                placed: list[tuple[int, int]] = []
                prev = out_len = 0
                for start, end in spans:
                    parts.append(content[prev:start])
                    out_len += start - prev
                    parts.append(new_text)
                    placed.append((out_len, out_len + len(new_text)))
                    out_len += len(new_text)
                    prev = end
                parts.append(content[prev:])
                content = self._normalize_placed_spans("".join(parts), placed, quote_style)
                if placed:
                    new_text = content[placed[0][0] : placed[0][1]]
                applied_edits.append({
                    "op": "replace",
                    "match_mode": exact_mode,
                    "old_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
                    "new_preview": new_text[:200] + ("..." if len(new_text) > 200 else ""),
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
                    extra_hint="（确实要把这几处全部替换时，才使用 replace_all=true）",
                    preview_content=content,
                )
                content, new_text = self._place_new_text(
                    content, start, start + len(old_text), new_text, quote_style
                )
                detail = {
                    "op": "replace",
                    "match_mode": exact_mode,
                    "match_count": match_count,
                    "old_preview": old_text[:200] + ("..." if len(old_text) > 200 else ""),
                    "new_preview": new_text[:200] + ("..." if len(new_text) > 200 else ""),
                }
                if occurrence is not None:
                    detail["occurrence"] = occurrence
                applied_edits.append(detail)
        else:
            if match_mode == "exact":
                raise _not_found(
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
                raise _paragraph_break_error(edit_index, "old")

            # 3) If fuzzy match fails, try approximate match (handles word errors)
            approx_match = None
            if not spans:
                approx_stats: dict[str, int] = {}
                approx_match = find_approximate_match(
                    content,
                    old_text,
                    max_error_rate=0.25,  # Allow up to 25% character difference
                    min_pattern_len=8,
                    keep_line_breaks=True,
                    stats=approx_stats,
                )
                if not approx_match and approx_stats.get("paragraph_rejected"):
                    raise _paragraph_break_error(edit_index, "old")
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
                    self._check_approximate_edges(content, start, end, old_text, edit_index)
                    matched_text = content[start:end]
                    placed_text = follow_span_indentation(content, start, end, old_text, new_text)
                    # Single approximate match - use it
                    content, written = self._place_new_text(content, start, end, placed_text, quote_style)
                    detail = {
                        "op": "replace",
                        "match_mode": "approximate",
                        "similarity": round(similarity, 3),
                        "matched_original": _preview(matched_text),
                        "warning": FUZZY_EDIT_WARNING,
                        "old_preview": _preview(old_text),
                        "new_preview": _preview(written),
                    }
                    if placed_text != new_text:
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
                    f"Edit {edit_index}: 找不到要替换的原文片段。请从当前文件原文中复制更长且唯一的原文。候选片段: {suggestions}"
                )

            if replace_all:
                # Stitch segments in one pass (spans are sorted and
                # non-overlapping); repeated re-slicing would be O(n·k).
                parts: list[str] = []
                placed: list[tuple[int, int]] = []
                prev = out_len = 0
                first_matched: str | None = None
                indent_followed = False
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
                    placed_text = follow_span_indentation(content, start, end, old_text, new_text)
                    indent_followed = indent_followed or placed_text != new_text
                    parts.append(content[prev:start])
                    out_len += start - prev
                    parts.append(placed_text)
                    placed.append((out_len, out_len + len(placed_text)))
                    out_len += len(placed_text)
                    prev = end
                parts.append(content[prev:])
                content = self._normalize_placed_spans("".join(parts), placed, quote_style)
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
                if len(spans) != 1 and occurrence is None:
                    previews = _numbered_previews(content, spans)
                    raise _ambiguous(
                        f"Edit {edit_index}: 原文片段匹配到多个位置（{len(spans)}处），"
                        f"为避免误改已中止。推荐做法：保持原参数不变，加上 occurrence=N "
                        f"指定要改第几处（1-based，见下方候选片段序号）；或提供更长且更唯一的原文/锚点。"
                        f"（确实要把这几处全部替换时，才使用 replace_all=true）候选片段: {previews}"
                    )

                idx = 0
                if occurrence is not None:
                    if occurrence > len(spans):
                        raise EditFileError(
                            f"Edit {edit_index}: occurrence out of range (1..{len(spans)})"
                        )
                    idx = occurrence - 1

                start, end = spans[idx]
                if ignore_punct_whitespace:
                    start, end = self._extend_fuzzy_span(
                        content, start, end, old_text, replacement=new_text
                    )
                matched_text = content[start:end]
                placed_text = follow_span_indentation(content, start, end, old_text, new_text)
                content, written = self._place_new_text(content, start, end, placed_text, quote_style)
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
                if placed_text != new_text:
                    detail["layout_notes"] = [NOTE_INDENT_FOLLOWS_FILE]
                applied_edits.append(detail)

        return content

    def _apply_insert_after(
        self,
        content: str,
        edit: dict[str, Any],
        edit_index: int,
        applied_edits: list[dict[str, Any]],
        warnings: list[str],
        *,
        quote_style: str | None = None,
    ) -> str:
        """Apply an insert_after edit operation."""
        anchor = edit.get("anchor", "")
        text = edit.get("text", "")

        match_mode = str(edit.get("match_mode") or "auto").strip().lower()
        ignore_punct_whitespace = self._parse_ignore_punct_whitespace(edit)
        occurrence = self._parse_occurrence(edit, edit_index)

        if not anchor:
            raise ValueError(f"Edit {edit_index}: 'anchor' field is required for insert_after operation")

        located = locate_exact_or_quote_equivalent(content, anchor)
        if located is not None:
            exact_mode, haystack, needle = located
            match_count = haystack.count(needle)
            start = self._select_exact_occurrence_start(
                haystack, needle, occurrence, match_count, edit_index, label="锚点",
                preview_content=content,
            )
            pos = start + len(anchor)
            notes: list[str] = []
            pos, text = self._position_insert(
                content, pos, text, direction="after", snap_sentence=True, notes=notes,
                exact_anchor=True,
            )
            content, text = self._place_new_text(content, pos, pos, text, quote_style)
            applied_edits.append({
                "op": "insert_after",
                "match_mode": exact_mode,
                "match_count": match_count,
                "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                "text_len": len(text),
                "text_words": count_words(text),
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
            })
            if notes:
                applied_edits[-1]["layout_notes"] = notes
        else:
            if match_mode == "exact":
                raise _not_found(
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
                    # Insert after the matched text：锚点以句号/引号收尾时，
                    # 插入点要越过原文对应的标点，不能插在「冷」和「。」之间。
                    _, pos = self._extend_fuzzy_span(content, start, end, anchor, leading=False)
                    notes = []
                    pos, text = self._position_insert(
                        content, pos, text, direction="after", snap_sentence=True, notes=notes
                    )
                    content, text = self._place_new_text(content, pos, pos, text, quote_style)
                    applied_edits.append({
                        "op": "insert_after",
                        "match_mode": "approximate",
                        "similarity": round(similarity, 3),
                        "matched_original": matched_text[:200] + ("..." if len(matched_text) > 200 else ""),
                        "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                        "text_len": len(text),
                        "text_words": count_words(text),
                        "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                    })
                    if notes:
                        applied_edits[-1]["layout_notes"] = notes
                    return content

                # Tertiary fallback: locate a unique best paragraph
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
                    pos = end
                    notes = []
                    pos, text = self._position_insert(
                        content, pos, text, direction="after", snap_sentence=True, notes=notes
                    )
                    content, text = self._place_new_text(content, pos, pos, text, quote_style)
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
                        "text_words": count_words(text),
                        "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                    })
                    if notes:
                        applied_edits[-1]["layout_notes"] = notes
                    return content

                suggestions = suggest_similar_lines(
                    content,
                    anchor,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
                raise _not_found(
                    f"Edit {edit_index}: 找不到插入锚点。请从当前文件原文中复制更长且唯一的锚点。候选片段: {suggestions}"
                )

            if len(spans) != 1 and occurrence is None:
                previews = _numbered_previews(content, spans)
                raise _ambiguous(
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
            if ignore_punct_whitespace:
                _, pos = self._extend_fuzzy_span(content, start, end, anchor, leading=False)
            notes = []
            pos, text = self._position_insert(
                content, pos, text, direction="after", snap_sentence=True, notes=notes
            )
            content, text = self._place_new_text(content, pos, pos, text, quote_style)
            applied_edits.append({
                "op": "insert_after",
                "match_mode": "fuzzy",
                "ignore_punct_whitespace": ignore_punct_whitespace,
                "match_count": len(spans),
                "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                "text_len": len(text),
                "text_words": count_words(text),
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
            })
            if notes:
                applied_edits[-1]["layout_notes"] = notes

        return content

    def _apply_insert_before(
        self,
        content: str,
        edit: dict[str, Any],
        edit_index: int,
        applied_edits: list[dict[str, Any]],
        warnings: list[str],
        *,
        quote_style: str | None = None,
    ) -> str:
        """Apply an insert_before edit operation."""
        anchor = edit.get("anchor", "")
        text = edit.get("text", "")

        match_mode = str(edit.get("match_mode") or "auto").strip().lower()
        ignore_punct_whitespace = self._parse_ignore_punct_whitespace(edit)
        occurrence = self._parse_occurrence(edit, edit_index)

        if not anchor:
            raise ValueError(f"Edit {edit_index}: 'anchor' field is required for insert_before operation")

        located = locate_exact_or_quote_equivalent(content, anchor)
        if located is not None:
            exact_mode, haystack, needle = located
            match_count = haystack.count(needle)
            pos = self._select_exact_occurrence_start(
                haystack, needle, occurrence, match_count, edit_index, label="锚点",
                preview_content=content,
            )
            notes: list[str] = []
            pos, text = self._position_insert(
                content, pos, text, direction="before", snap_sentence=True, notes=notes,
                exact_anchor=True,
            )
            content, text = self._place_new_text(content, pos, pos, text, quote_style)
            applied_edits.append({
                "op": "insert_before",
                "match_mode": exact_mode,
                "match_count": match_count,
                "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                "text_len": len(text),
                "text_words": count_words(text),
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
            })
            if notes:
                applied_edits[-1]["layout_notes"] = notes
        else:
            if match_mode == "exact":
                raise _not_found(
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
                    # Insert before the matched text：锚点以引号开头时插入点要
                    # 落在原文开引号之前，不能把新文本插进「“」和正文之间。
                    pos, _ = self._extend_fuzzy_span(content, start, end, anchor, trailing=False)
                    notes = []
                    pos, text = self._position_insert(
                        content, pos, text, direction="before", snap_sentence=True, notes=notes
                    )
                    content, text = self._place_new_text(content, pos, pos, text, quote_style)
                    applied_edits.append({
                        "op": "insert_before",
                        "match_mode": "approximate",
                        "similarity": round(similarity, 3),
                        "matched_original": matched_text[:200] + ("..." if len(matched_text) > 200 else ""),
                        "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                        "text_len": len(text),
                        "text_words": count_words(text),
                        "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                    })
                    if notes:
                        applied_edits[-1]["layout_notes"] = notes
                    return content

                # Tertiary fallback: locate a unique best paragraph
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
                    pos = start
                    notes = []
                    pos, text = self._position_insert(
                        content, pos, text, direction="before", snap_sentence=True, notes=notes
                    )
                    content, text = self._place_new_text(content, pos, pos, text, quote_style)
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
                        "text_words": count_words(text),
                        "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
                    })
                    if notes:
                        applied_edits[-1]["layout_notes"] = notes
                    return content

                suggestions = suggest_similar_lines(
                    content,
                    anchor,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
                raise _not_found(
                    f"Edit {edit_index}: 找不到插入锚点。请从当前文件原文中复制更长且唯一的锚点。候选片段: {suggestions}"
                )

            if len(spans) != 1 and occurrence is None:
                previews = _numbered_previews(content, spans)
                raise _ambiguous(
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
            if ignore_punct_whitespace:
                pos, _ = self._extend_fuzzy_span(content, start, end, anchor, trailing=False)
            notes = []
            pos, text = self._position_insert(
                content, pos, text, direction="before", snap_sentence=True, notes=notes
            )
            content, text = self._place_new_text(content, pos, pos, text, quote_style)
            applied_edits.append({
                "op": "insert_before",
                "match_mode": "fuzzy",
                "ignore_punct_whitespace": ignore_punct_whitespace,
                "match_count": len(spans),
                "anchor_preview": anchor[:200] + ("..." if len(anchor) > 200 else ""),
                "text_len": len(text),
                "text_words": count_words(text),
                "text_preview": text[:200] + ("..." if len(text) > 200 else ""),
            })
            if notes:
                applied_edits[-1]["layout_notes"] = notes

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

        if not isinstance(old_text, str) or not old_text:
            raise EditFileError(
                f"Edit {edit_index}: delete 缺少 old 字段（要删除的原文）。"
                f"请从当前文件原文中复制要删的那一段。"
            )

        located = locate_exact_or_quote_equivalent(content, old_text)
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
            content = content[:start] + content[start + len(old_text):]
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
                raise _paragraph_break_error(edit_index, "old")
            if not spans:
                suggestions = suggest_similar_lines(
                    content,
                    old_text,
                    ignore_punct_whitespace=ignore_punct_whitespace,
                )
                raise _not_found(
                    f"Edit {edit_index}: 找不到要删除的原文片段。请从当前文件原文中复制更长且唯一的原文。候选片段: {suggestions}"
                )

            if len(spans) != 1 and occurrence is None:
                previews = _numbered_previews(content, spans)
                raise _ambiguous(
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
            if ignore_punct_whitespace:
                # 只按 old 自己的边缘标点扩展：删除没有 new 可参照，
                # old 带了引号/句号就把原文对应的那一个一起删掉，不留孤立的「“。」。
                start, end = self._extend_fuzzy_span(content, start, end, old_text)
            matched_text = content[start:end]
            content = content[:start] + content[end:]
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
    "EDIT_ERROR_ANCHOR_AMBIGUOUS",
    "EDIT_ERROR_ANCHOR_NOT_FOUND",
    "EDIT_ERROR_FILE_BUSY",
    "EDIT_ERROR_FILE_NOT_FOUND",
    "EDIT_ERROR_GENERIC",
    "EDIT_ERROR_INVALID_EDIT",
    "EDIT_ERROR_NO_CHANGE",
    "EDIT_ERROR_PERMISSION_DENIED",
    "EditBatchError",
    "EditFileError",
    "FileEditor",
    "FileWriteBusyError",
    "edit_error_user_message",
    "acquire_file_write_lock",
    "file_write_lock",
]
