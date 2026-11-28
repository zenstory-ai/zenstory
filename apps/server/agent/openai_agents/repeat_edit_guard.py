"""Conservative, strictly-structural guard against serial micro-edit loops.

Production telemetry showed two dominant failure modes behind >=4x serial
``edit_file`` chains on the same file:
1. Closed-loop word-count shaving/padding right after ``write_chapter`` (2–23
   serial calls rewriting 100%–978% of a newly written chapter).
2. Existing-file serial micro-polish loops (shaving/tweaking <=80 chars per
   call across 5–15 serial turns).

This guard relies solely on structured execution facts (per-file clean
``edit_file`` count, edit item count, op type, character length delta, and
``AgentRequestContext.saved_content_body_ids()``) — zero natural-language
keyword or regex heuristics.

Strict exemptions (never counted / never blocked):
- Failed or no-mutation calls (``status == "error"`` or ``mutation_applied=False``)
  — handled by ``ToolFailureBreaker``; ``status="partial"`` calls with
  ``mutation_applied=True`` DO count as real edits and update tracked length;
- Pure ``op="append"`` calls (e.g. ``_BODY_CUT_CONTEXT`` truncation continuation);
- Bulk batches (``len(edits) >= 6``) on existing files so legitimate 10–30 edit
  outline/chapter sweeps never get blocked;
- Different ``file_id`` targets are tracked independently.

Also provides a lightweight self-read guard: ``query_files(id=...)`` and
``query_files(ids=[...])`` (full read or ``start_char=0``) on a chapter just
written via ``write_chapter`` in the same request returns current metadata
(including ``word_count``) and a concise hint instead of re-injecting the
just-written body into context, unless the file just hit ``anchor_not_found``
or ``stale_content`` and needs a recovery read. Consecutive and per-file
intercepts (both ``self_read_guarded`` and ``blocked_edits``) are hard-capped
and escalate to ``ToolFailureBreaker`` so a stubborn model cannot spin until
``max_turns=60``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent.constants import coerce_bool
from agent.openai_agents.events import parse_json_object
from agent.tools.file_ops.serialization import (
    QUERY_FILES_MAX_BATCH_IDS,
    normalize_query_file_ids,
)
from utils.logger import get_logger, log_with_context
from utils.text_metrics import count_words

logger = get_logger(__name__)

EDIT_TOOL_NAME = "edit_file"
BATCH_REPLACE_TOOL_NAME = "batch_replace_files"
READ_TOOL_NAME = "query_files"
READ_TOOL_NAMES: frozenset[str] = frozenset({"query_files", "read_files"})

EDIT_REPEAT_LIMIT_ERROR_TYPE = "edit_repeat_limit"
# Escalated error type emitted once consecutive or per-file guard intercepts exceed
# their hard cap. Intentionally NOT in EDIT_GUARD_ERROR_TYPES so ToolFailureBreaker
# counts it and trips if a stubborn model keeps ignoring the guard.
EDIT_GUARD_ESCALATED_ERROR_TYPE = "edit_guard_loop_exhausted"
# Excluded from ToolFailureBreaker so hitting a micro-polish cap steers the
# model to finish cleanly rather than tripping a fatal circuit breaker on the first 1-2 blocks.
EDIT_GUARD_ERROR_TYPES: frozenset[str] = frozenset({EDIT_REPEAT_LIMIT_ERROR_TYPE})

# Hard caps on consecutive and per-file guard intercepts (self_read_guarded + blocked_edits).
# Once reached, the guard escalates from soft-skip / self_read_guarded to a real
# error payload (EDIT_GUARD_ESCALATED_ERROR_TYPE) that ToolFailureBreaker counts.
MAX_CONSECUTIVE_GUARD_INTERCEPTS = 3
MAX_PER_FILE_GUARD_INTERCEPTS = 4

# Bulk threshold: batches of >= 6 edits on an existing file are exempt from
# micro-polish caps and reset any consecutive micro-edit streak.
BULK_EDIT_MIN_ITEMS = 6

# Micro-edit definition on existing files: <= 4 non-append edits and net
# character delta |new_chars - old_chars| <= 80.
MICRO_EDIT_MAX_ITEMS = 4
MICRO_EDIT_MAX_NET_DELTA = 80

# Newly written chapter (saved via write_chapter in the same request):
# 1st clean non-bulk edit: normal; 2nd: allowed + edit_hint; 3rd (non-append): soft-skipped.
# One bulk self-check batch (len(edits) >= BULK_EDIT_MIN_ITEMS) is allowed on a newly
# written chapter before post_write_limit blocks so legitimate 6+ edit revisions aren't
# falsely blocked while preventing unbounded bulk loops.
POST_WRITE_HINT_AT = 2
POST_WRITE_BLOCK_AT = 3
POST_WRITE_BULK_ALLOWANCE = 1

# Existing file (not written via write_chapter in the same request):
# 1st-3rd clean non-bulk edits: normal; 4th: allowed + edit_hint;
# block on 5th+ when consecutive_micro_calls >= 2, or hard safety cap on 8th+.
EXISTING_HINT_AT = 4
EXISTING_MICRO_BLOCK_AT = 5
EXISTING_MICRO_STREAK_MIN = 2
EXISTING_HARD_BLOCK_AT = 8

# When cumulative replaced chars reach >= 60% of the file length on an existing
# chapter, remind the model to consider write_chapter instead of piecemeal edits.
CUMULATIVE_REWRITE_HINT_RATIO = 0.6

_SELF_READ_RECOVERY_ERROR_TYPES: frozenset[str] = frozenset({"anchor_not_found", "stale_content"})
_CHAPTER_FILE_TYPES: frozenset[str] = frozenset({"draft", "script", ""})


def _load_json(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text) if text else None
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _extract_file_id(args: dict[str, Any]) -> str:
    raw = args.get("id") or args.get("file_id") or args.get("fileId")
    return raw.strip() if isinstance(raw, str) else ""


def _normalize_edit_op(edit: dict[str, Any]) -> str:
    op_raw = edit.get("op")
    if op_raw is None:
        op_raw = edit.get("operation") or edit.get("action") or edit.get("type")
    op = op_raw.strip().lower().replace("-", "_") if isinstance(op_raw, str) else ""
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
    if (
        not op
        and isinstance(edit.get("old"), str)
        and isinstance(edit.get("new"), str)
        and "anchor" not in edit
        and "text" not in edit
    ):
        op = "replace"
    return op


def _inspect_edit_args(args: dict[str, Any]) -> tuple[int, bool, int, int, bool]:
    """Return (edit_count, is_append, arg_old_chars, arg_new_chars, has_replace_all)."""
    from agent.tools.tool_inputs import normalize_edit_file_args

    normalized = normalize_edit_file_args(args)
    raw_edits = normalized.get("edits") if isinstance(normalized, dict) else None
    if not isinstance(raw_edits, list):
        return 0, False, 0, 0, False

    edit_count = len(raw_edits)
    ops: list[str] = []
    old_chars = 0
    new_chars = 0
    has_replace_all = False

    for item in raw_edits:
        if not isinstance(item, dict):
            continue
        op = _normalize_edit_op(item)
        if op:
            ops.append(op)
        if op == "replace":
            old_val = (
                item.get("old")
                if isinstance(item.get("old"), str)
                else item.get("old_text")
                if isinstance(item.get("old_text"), str)
                else item.get("oldText")
                if isinstance(item.get("oldText"), str)
                else item.get("from")
            )
            new_val = (
                item.get("new")
                if isinstance(item.get("new"), str)
                else item.get("new_text")
                if isinstance(item.get("new_text"), str)
                else item.get("newText")
                if isinstance(item.get("newText"), str)
                else item.get("to")
            )
            if isinstance(old_val, str):
                old_chars += len(old_val)
            if isinstance(new_val, str):
                new_chars += len(new_val)
            if coerce_bool(item.get("replace_all"), default=False):
                has_replace_all = True
        elif op == "delete":
            old_val = (
                item.get("old")
                if isinstance(item.get("old"), str)
                else item.get("old_text")
                if isinstance(item.get("old_text"), str)
                else item.get("oldText")
                if isinstance(item.get("oldText"), str)
                else item.get("from")
            )
            if isinstance(old_val, str):
                old_chars += len(old_val)
        elif op in ("insert_after", "insert_before", "append", "prepend"):
            text_val = item.get("text") if isinstance(item.get("text"), str) else item.get("content")
            if isinstance(text_val, str):
                new_chars += len(text_val)

    is_append = bool(ops) and all(op == "append" for op in ops)
    return edit_count, is_append, old_chars, new_chars, has_replace_all


def _is_zero_offset(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return value == 0
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return False
        try:
            return int(stripped) == 0
        except ValueError:
            return False
    return False


def _is_written_this_turn(request_context: Any, file_id: str) -> bool:
    """True when ``file_id`` is a chapter body saved in this request."""
    if request_context is None or not file_id:
        return False
    content_ids_fn = getattr(request_context, "saved_content_body_ids", None)
    if callable(content_ids_fn):
        return file_id in content_ids_fn()
    saved_fn = getattr(request_context, "saved_bodies_this_request", None)
    if callable(saved_fn):
        return file_id in saved_fn()
    return False


def _saved_title(request_context: Any, file_id: str) -> str:
    if request_context is None or not file_id:
        return ""
    saved_fn = getattr(request_context, "saved_bodies_this_request", None)
    if callable(saved_fn):
        saved = saved_fn()
        if isinstance(saved, dict):
            return str(saved.get(file_id) or "")
    return ""


def _snapshot_content(request_context: Any, file_id: str) -> str | None:
    if request_context is None or not file_id:
        return None
    snap_fn = getattr(request_context, "read_snapshot", None)
    if callable(snap_fn):
        snap = snap_fn(file_id)
        if isinstance(snap, str):
            return snap
    return None


@dataclass
class _FileEditState:
    post_write_clean_calls: int = 0
    post_write_bulk_calls: int = 0
    clean_non_bulk_calls: int = 0
    consecutive_micro_calls: int = 0
    cumulative_replaced_chars: int = 0
    initial_length: int | None = None
    last_length: int | None = None
    last_word_count: int | None = None
    title: str = ""
    file_type: str = ""


@dataclass(frozen=True)
class EditPlan:
    """Plan returned before executing a tool call."""

    file_id: str = ""
    arguments: str | None = None
    parsed_arguments: dict[str, Any] | None = None
    edit_count: int = 0
    is_append: bool = False
    is_bulk: bool = False
    arg_old_chars: int = 0
    arg_new_chars: int = 0
    pre_edit_length: int | None = None
    written_this_turn: bool = False
    self_read_recovery_id: str = ""
    self_read_recovery_ids: tuple[str, ...] = ()
    self_read_guarded_ids: tuple[str, ...] = ()
    requested_ids: tuple[str, ...] = ()
    blocked_output: str | None = None


@dataclass
class RepeatEditGuard:
    """Per-request structural guard against serial micro-edit loops and post-write self-reads.

    Shared across continuation runs within a single user request (just like
    ``ToolFailureBreaker`` and ``RepeatReadGuard``). Soft-skips the first 1-2
    intercepts so well-behaved models converge cleanly, and escalates to
    ``EDIT_GUARD_ESCALATED_ERROR_TYPE`` (counted by ``ToolFailureBreaker``) once
    consecutive (``MAX_CONSECUTIVE_GUARD_INTERCEPTS``) or per-file
    (``MAX_PER_FILE_GUARD_INTERCEPTS``) intercepts hit the hard cap.
    """

    request_context: Any = field(default=None, repr=False, compare=False)
    blocked_edits: int = 0
    hinted_edits: int = 0
    guarded_self_reads: int = 0
    escalated_intercepts: int = 0
    _files: dict[str, _FileEditState] = field(default_factory=dict)
    _allow_self_read: set[str] = field(default_factory=set)
    _consecutive_intercepts: int = 0
    _per_file_intercepts: dict[str, int] = field(default_factory=dict)

    # ------------------------------------------------------------------ plan
    def plan(
        self,
        tool_name: str,
        raw_arguments: str,
        *,
        parsed_args: dict[str, Any] | None = None,
    ) -> EditPlan:
        if tool_name == EDIT_TOOL_NAME:
            return self._plan_edit(raw_arguments, parsed_args=parsed_args)
        if tool_name in READ_TOOL_NAMES:
            return self._plan_self_read(tool_name, raw_arguments, parsed_args=parsed_args)
        self._consecutive_intercepts = 0
        return EditPlan()

    def _plan_edit(
        self,
        raw_arguments: str,
        *,
        parsed_args: dict[str, Any] | None = None,
    ) -> EditPlan:
        if parsed_args is not None:
            args = parsed_args
        else:
            parsed, parse_error, _ = parse_json_object(raw_arguments or "", tool_name=EDIT_TOOL_NAME)
            if parse_error is not None or not isinstance(parsed, dict):
                return EditPlan()
            args = parsed

        file_id = _extract_file_id(args)
        if not file_id:
            return EditPlan()

        edit_count, is_append, arg_old_chars, arg_new_chars, _has_replace_all = _inspect_edit_args(args)
        is_bulk = edit_count >= BULK_EDIT_MIN_ITEMS
        written_this_turn = _is_written_this_turn(self.request_context, file_id)

        state = self._files.get(file_id)
        pre_edit_length: int | None = state.last_length if state is not None else None
        if pre_edit_length is None:
            snap = _snapshot_content(self.request_context, file_id)
            if snap is not None:
                pre_edit_length = len(snap)

        # Pure op="append" is always exempt (e.g. output-cap truncation continuation).
        if is_append:
            self._consecutive_intercepts = 0
            return EditPlan(
                file_id=file_id,
                edit_count=edit_count,
                is_append=True,
                is_bulk=is_bulk,
                arg_old_chars=arg_old_chars,
                arg_new_chars=arg_new_chars,
                pre_edit_length=pre_edit_length,
                written_this_turn=written_this_turn,
            )

        if state is not None:
            if written_this_turn:
                # Allow 1 bulk self-check revision (>= BULK_EDIT_MIN_ITEMS) on a newly written
                # chapter before post_write_limit blocks so 6+ edit sweeps aren't falsely skipped.
                if not (is_bulk and state.post_write_bulk_calls < POST_WRITE_BULK_ALLOWANCE):
                    next_calls = state.post_write_clean_calls + 1
                    if next_calls >= POST_WRITE_BLOCK_AT:
                        return self._block_edit(
                            file_id=file_id,
                            state=state,
                            written_this_turn=True,
                            clean_calls=state.post_write_clean_calls,
                            reason="post_write_limit",
                        )
            elif not is_bulk:
                next_non_bulk = state.clean_non_bulk_calls + 1
                if next_non_bulk >= EXISTING_HARD_BLOCK_AT:
                    return self._block_edit(
                        file_id=file_id,
                        state=state,
                        written_this_turn=False,
                        clean_calls=state.clean_non_bulk_calls,
                        reason="existing_safety_cap",
                    )
                if (
                    next_non_bulk >= EXISTING_MICRO_BLOCK_AT
                    and state.consecutive_micro_calls >= EXISTING_MICRO_STREAK_MIN
                ):
                    return self._block_edit(
                        file_id=file_id,
                        state=state,
                        written_this_turn=False,
                        clean_calls=state.clean_non_bulk_calls,
                        reason="existing_micro_loop",
                    )

        self._consecutive_intercepts = 0
        return EditPlan(
            file_id=file_id,
            edit_count=edit_count,
            is_append=False,
            is_bulk=is_bulk,
            arg_old_chars=arg_old_chars,
            arg_new_chars=arg_new_chars,
            pre_edit_length=pre_edit_length,
            written_this_turn=written_this_turn,
        )

    def _record_intercept(self, file_ids: list[str], *, increment_consecutive: bool = True) -> tuple[bool, int]:
        """Increment intercept counters for ``file_ids`` and return ``(should_escalate, max_per_file)``."""
        if increment_consecutive:
            self._consecutive_intercepts += 1
        max_per_file = 0
        for fid in file_ids:
            count = self._per_file_intercepts.get(fid, 0) + 1
            self._per_file_intercepts[fid] = count
            if count > max_per_file:
                max_per_file = count
        should_escalate = (
            self._consecutive_intercepts >= MAX_CONSECUTIVE_GUARD_INTERCEPTS
            or max_per_file >= MAX_PER_FILE_GUARD_INTERCEPTS
        )
        if should_escalate:
            self.escalated_intercepts += 1
        return should_escalate, max_per_file

    def _self_read_row(self, file_id: str) -> tuple[dict[str, Any], str, int | None]:
        state = self._files.get(file_id)
        title = (state.title if state is not None and state.title else "") or _saved_title(
            self.request_context, file_id
        )
        word_count = self._current_word_count(file_id, state)
        row: dict[str, Any] = {
            "id": file_id,
            "title": title,
            "content_omitted": True,
        }
        if word_count is not None:
            row["word_count"] = word_count
        return row, title, word_count

    def _plan_self_read(
        self,
        tool_name: str,
        raw_arguments: str,
        *,
        parsed_args: dict[str, Any] | None = None,
    ) -> EditPlan:
        """Guard against re-reading chapters just written via write_chapter in the same run.

        Supports both single-ID ``read_files(ids=[...])`` / ``query_files(id=...)`` and multi-ID
        batches:
        - When ALL requested IDs were just written this turn (and not unlocked for recovery),
          blocks synchronously without querying DB.
        - When a batch mixes just-written IDs with normal existing IDs, rewrites ``plan.arguments``
          (and ``plan.parsed_arguments``) to exclude ``guarded_ids`` before DB execution and merges
          slim ``content_omitted=True`` rows back in ``observe()`` (with defense-in-depth stripping
          if caller passed raw args), or escalates synchronously once a guarded file hits
          ``MAX_PER_FILE_GUARD_INTERCEPTS``.
        """
        if self.request_context is None:
            self._consecutive_intercepts = 0
            return EditPlan()
        if parsed_args is not None:
            args = parsed_args
        else:
            parsed, parse_error, _ = parse_json_object(raw_arguments or "", tool_name=tool_name)
            if parse_error is not None or not isinstance(parsed, dict):
                return EditPlan()
            args = parsed

        # Keyword search, section read, or tail read are not whole-chapter self-reads.
        raw_query = args.get("query")
        if isinstance(raw_query, str) and raw_query.strip():
            self._consecutive_intercepts = 0
            return EditPlan()
        if args.get("section") not in (None, "") or args.get("tail_chars") not in (None, ""):
            self._consecutive_intercepts = 0
            return EditPlan()

        start_char = args.get("start_char")
        if start_char not in (None, "") and not _is_zero_offset(start_char):
            self._consecutive_intercepts = 0
            return EditPlan()

        try:
            target_ids, _truncated = normalize_query_file_ids(
                file_id=args.get("id") or args.get("file_id") or args.get("fileId"),
                file_ids=args.get("ids"),
                max_ids=QUERY_FILES_MAX_BATCH_IDS,
            )
        except ValueError:
            return EditPlan()
        if not target_ids:
            self._consecutive_intercepts = 0
            return EditPlan()

        guarded_ids: list[str] = []
        recovery_ids: list[str] = []
        allowed_ids: list[str] = []
        for fid in target_ids:
            if not _is_written_this_turn(self.request_context, fid):
                allowed_ids.append(fid)
            elif fid in self._allow_self_read:
                recovery_ids.append(fid)
            else:
                guarded_ids.append(fid)

        if not guarded_ids:
            self._consecutive_intercepts = 0
            first_recovery = recovery_ids[0] if recovery_ids else ""
            return EditPlan(
                file_id=first_recovery,
                self_read_recovery_id=first_recovery,
                self_read_recovery_ids=tuple(recovery_ids),
            )

        # Mixed batch (some just-written IDs + some legitimate other/recovery IDs):
        # if any guarded ID has already reached the per-file cap, escalate synchronously so a
        # stubborn model cannot keep piggybacking a guarded file onto multi-ID batches forever.
        if allowed_ids or recovery_ids:
            cap_exceeding = [
                fid
                for fid in guarded_ids
                if self._per_file_intercepts.get(fid, 0) + 1 >= MAX_PER_FILE_GUARD_INTERCEPTS
            ]
            if not cap_exceeding:
                self._consecutive_intercepts = 0
                guarded_set = set(guarded_ids)
                remaining_ids = [fid for fid in target_ids if fid not in guarded_set]
                rewritten = dict(args)
                rewritten.pop("id", None)
                rewritten.pop("file_id", None)
                rewritten.pop("fileId", None)
                rewritten["ids"] = remaining_ids
                first_recovery = recovery_ids[0] if recovery_ids else ""
                return EditPlan(
                    file_id=first_recovery or guarded_ids[0],
                    arguments=json.dumps(rewritten, ensure_ascii=False, separators=(",", ":")),
                    parsed_arguments=rewritten,
                    self_read_recovery_id=first_recovery,
                    self_read_recovery_ids=tuple(recovery_ids),
                    self_read_guarded_ids=tuple(guarded_ids),
                    requested_ids=tuple(target_ids),
                )
            self.guarded_self_reads += 1
            self._record_intercept(cap_exceeding, increment_consecutive=True)
            rows = [self._self_read_row(fid)[0] for fid in cap_exceeding]
            return self._escalated_self_read_plan(
                tool_name=tool_name,
                guarded_ids=cap_exceeding,
                rows=rows,
                start_char=start_char,
            )

        # All requested IDs were just written this turn and unchanged: block synchronously.
        self.guarded_self_reads += 1
        escalate, _max_per_file = self._record_intercept(guarded_ids, increment_consecutive=True)
        rows: list[dict[str, Any]] = []
        labels: list[str] = []
        wc_parts: list[str] = []
        for fid in guarded_ids:
            row, title, word_count = self._self_read_row(fid)
            rows.append(row)
            label = f"《{title}》" if title else f"文件(id={fid})"
            labels.append(label)
            if word_count is not None:
                wc_parts.append(f"{label} {word_count} 字")

        if escalate:
            return self._escalated_self_read_plan(
                tool_name=tool_name,
                guarded_ids=guarded_ids,
                rows=rows,
                start_char=start_char,
            )

        log_with_context(
            logger,
            20,  # INFO
            "RepeatEditGuard self-read guarded",
            file_id=guarded_ids[0],
            file_ids=guarded_ids if len(guarded_ids) > 1 else None,
            start_char=start_char,
            word_count=rows[0].get("word_count") if len(rows) == 1 else None,
            consecutive_intercepts=self._consecutive_intercepts,
        )

        if len(guarded_ids) == 1:
            wc_val = rows[0].get("word_count")
            wc_part = f"（当前真实字数 {wc_val} 字）" if wc_val is not None else ""
            read_hint = (
                f"{labels[0]}是本轮刚写完并保存的正文{wc_part}，完整内容已在上方你刚写出的内容中，"
                "无需再次读取原文。请直接对照上方内容继续未完成任务或按真实字数简短回复作者；"
                "不要为了卡字数或微调措辞重读或反复修改。"
            )
        else:
            wc_summary = f"（当前真实字数：{'、'.join(wc_parts)}）" if wc_parts else ""
            read_hint = (
                f"{'、'.join(labels)}都是本轮刚写完并保存的正文{wc_summary}，完整内容已在上方你刚写出的内容中，"
                "无需再次读取原文。请直接对照上方内容继续未完成任务或按真实字数简短回复作者；"
                "不要为了卡字数或微调措辞重读或反复修改。"
            )

        payload: dict[str, Any] = {
            "status": "success",
            "self_read_guarded": True,
            "data": rows,
            "read_hint": read_hint,
        }
        return EditPlan(
            file_id=guarded_ids[0],
            self_read_guarded_ids=tuple(guarded_ids),
            blocked_output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )

    def _escalated_self_read_plan(
        self,
        *,
        tool_name: str = READ_TOOL_NAME,
        guarded_ids: list[str],
        rows: list[dict[str, Any]],
        start_char: Any,
    ) -> EditPlan:
        labels = [
            f"《{row['title']}》" if row.get("title") else f"文件(id={row['id']})"
            for row in rows
        ]
        log_with_context(
            logger,
            30,  # WARNING
            "RepeatEditGuard self-read intercept cap reached; escalating",
            file_id=guarded_ids[0],
            file_ids=guarded_ids if len(guarded_ids) > 1 else None,
            start_char=start_char,
            consecutive_intercepts=self._consecutive_intercepts,
            per_file_intercepts={fid: self._per_file_intercepts.get(fid, 0) for fid in guarded_ids},
        )
        error_msg = (
            f"已多次拦截对本轮刚写完章节（{'、'.join(labels)}）的重复读取，本次 {tool_name} 已拒绝执行。"
            "请立即停止调用工具，直接按上方已有内容和真实字数向作者简短回复收尾。"
        )
        payload: dict[str, Any] = {
            "status": "error",
            "error": error_msg,
            "error_type": EDIT_GUARD_ESCALATED_ERROR_TYPE,
            "reason": "self_read_loop_cap",
            "guard_escalated": True,
            "stop_calling_tools": True,
            "tool_name": tool_name,
            "id": guarded_ids[0],
            "data": rows,
            "user_message": "已完成保存，跳过重复读取。",
        }
        return EditPlan(
            file_id=guarded_ids[0],
            self_read_guarded_ids=tuple(guarded_ids),
            blocked_output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )

    def _block_edit(
        self,
        *,
        file_id: str,
        state: _FileEditState,
        written_this_turn: bool,
        clean_calls: int,
        reason: str,
    ) -> EditPlan:
        self.blocked_edits += 1
        escalate, per_file_count = self._record_intercept([file_id], increment_consecutive=True)
        title = state.title or _saved_title(self.request_context, file_id)
        label = f"《{title}》" if title else f"文件(id={file_id})"
        word_count = self._current_word_count(file_id, state)
        wc_part = f"（当前真实字数 {word_count} 字）" if word_count is not None else ""

        if escalate:
            error_msg = (
                f"{label}的 edit_file 已被重复拦截（连续 {self._consecutive_intercepts} 次 / "
                f"本文件 {per_file_count} 次{wc_part}），已触发硬上限并拒绝执行。"
                "请立即停止调用任何编辑或读取工具，直接按当前真实字数向作者简短回复并结束本轮。"
            )
            log_with_context(
                logger,
                30,  # WARNING
                "RepeatEditGuard edit intercept cap reached; escalating",
                file_id=file_id,
                written_this_turn=written_this_turn,
                clean_calls=clean_calls,
                consecutive_intercepts=self._consecutive_intercepts,
                per_file_intercepts=per_file_count,
                reason=reason,
            )
            # Omit top-level "id" (use "file_id") matching tool_error_text() so StreamAdapter
            # does not emit fake file_edit_start/file_edit_end events or pop UI overlays for an
            # unexecuted escalated edit_file call.
            payload: dict[str, Any] = {
                "status": "error",
                "error": error_msg,
                "error_type": EDIT_GUARD_ESCALATED_ERROR_TYPE,
                "reason": reason,
                "guard_escalated": True,
                "stop_calling_tools": True,
                "tool_name": EDIT_TOOL_NAME,
                "file_id": file_id,
                "edits_applied": 0,
                "mutation_applied": False,
                "user_message": "这一步的细微调整已跳过，之前的修改都已保存。",
            }
            if word_count is not None:
                payload["word_count"] = word_count
            return EditPlan(
                file_id=file_id,
                written_this_turn=written_this_turn,
                blocked_output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )

        if reason == "post_write_limit":
            error_msg = (
                f"{label}是本轮刚写完的章节，已完成 {clean_calls} 次修改{wc_part}，"
                "本次 edit_file 未执行。不要再为凑字数或细节微调反复修改这一章："
                "请直接继续本轮未完成的其他任务，或按当前真实字数向作者简短回复。"
            )
        elif reason == "existing_micro_loop":
            rewrite_tip = (
                "如需大幅重写请改用 write_chapter，"
                if state.file_type in _CHAPTER_FILE_TYPES
                else ""
            )
            error_msg = (
                f"{label}本轮已连续进行 {clean_calls} 次小幅修改{wc_part}，"
                f"本次 edit_file 未执行。不要再逐次微调或卡字数：{rewrite_tip}"
                "请停止微调并直接向作者汇报已完成的结果。"
            )
        else:
            error_msg = (
                f"{label}本轮小批量修改已达上限（已完成 {clean_calls} 次{wc_part}），"
                "本次 edit_file 未执行。请停止零散修改；若确有大范围改动请合并为一次批量 edits，"
                "否则直接向作者总结当前结果。"
            )

        log_with_context(
            logger,
            20,  # INFO
            "RepeatEditGuard triggered",
            file_id=file_id,
            written_this_turn=written_this_turn,
            clean_calls=clean_calls,
            consecutive_micro_calls=state.consecutive_micro_calls,
            consecutive_intercepts=self._consecutive_intercepts,
            per_file_intercepts=per_file_count,
            reason=reason,
        )

        payload = {
            "status": "skipped",
            "guard_skipped": True,
            "reason": reason,
            "message": error_msg,
            "error_type": EDIT_REPEAT_LIMIT_ERROR_TYPE,
            "tool_name": EDIT_TOOL_NAME,
            "id": file_id,
            "edits_applied": 0,
            "mutation_applied": False,
            "user_message": "这一步的细微调整已跳过，之前的修改都已保存。",
        }
        if word_count is not None:
            payload["word_count"] = word_count
        return EditPlan(
            file_id=file_id,
            written_this_turn=written_this_turn,
            blocked_output=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        )

    # --------------------------------------------------------------- observe
    @staticmethod
    def _has_self_read_recovery_error(payload: dict[str, Any], data: dict[str, Any]) -> bool:
        """True when top-level error_type or any partial failed_edits[*].error_type warrants 1 recovery read."""
        if str(payload.get("error_type") or "") in _SELF_READ_RECOVERY_ERROR_TYPES:
            return True
        failed_edits = data.get("failed_edits")
        if isinstance(failed_edits, list):
            for item in failed_edits:
                if isinstance(item, dict) and str(item.get("error_type") or "") in _SELF_READ_RECOVERY_ERROR_TYPES:
                    return True
        return False

    def observe(
        self,
        tool_name: str,
        plan: EditPlan,
        output_text: str,
        *,
        parsed_output: dict[str, Any] | None = None,
    ) -> str:
        if tool_name in READ_TOOL_NAMES:
            recovery_ids = plan.self_read_recovery_ids or (
                (plan.self_read_recovery_id,) if plan.self_read_recovery_id else ()
            )
            for rid in recovery_ids:
                self._allow_self_read.discard(rid)
            if plan.self_read_guarded_ids:
                return self._merge_or_strip_guarded_self_reads(
                    plan, output_text, parsed_output=parsed_output
                )
            return output_text

        if tool_name == BATCH_REPLACE_TOOL_NAME:
            payload = parsed_output if parsed_output is not None else _load_json(output_text)
            if payload is not None:
                self._observe_batch_replace(payload)
            return output_text

        if tool_name != EDIT_TOOL_NAME or not plan.file_id:
            return output_text

        payload = parsed_output if parsed_output is not None else _load_json(output_text)
        if payload is None:
            return output_text

        status = payload.get("status")
        mutation_applied = payload.get("mutation_applied")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        edits_applied = data.get("edits_applied")
        needs_recovery_read = self._has_self_read_recovery_error(payload, data)

        is_applied_edit = (
            (status == "success" and mutation_applied is not False)
            or (
                status == "partial"
                and (
                    mutation_applied is True
                    or (
                        mutation_applied is None
                        and isinstance(edits_applied, int)
                        and not isinstance(edits_applied, bool)
                        and edits_applied > 0
                    )
                )
            )
            or (status not in ("error", "skipped") and mutation_applied is True)
        )
        if not is_applied_edit:
            if needs_recovery_read:
                self._allow_self_read.add(plan.file_id)
            return output_text

        # A clean successful edit clears any pending self-read recovery allowance;
        # a partial edit whose failed_edits include anchor_not_found/stale_content unlocks 1 recovery read.
        if status == "success":
            self._allow_self_read.discard(plan.file_id)
        elif needs_recovery_read:
            self._allow_self_read.add(plan.file_id)

        state = self._files.setdefault(plan.file_id, _FileEditState())
        if isinstance(data.get("title"), str) and data["title"]:
            state.title = data["title"]
        if isinstance(data.get("file_type"), str) and data["file_type"]:
            state.file_type = data["file_type"].strip().lower()

        new_length = data.get("new_length")
        if isinstance(new_length, int) and not isinstance(new_length, bool):
            if state.initial_length is None:
                if plan.pre_edit_length is not None and plan.pre_edit_length > 0:
                    state.initial_length = plan.pre_edit_length
                else:
                    est_before = new_length - (plan.arg_new_chars - plan.arg_old_chars)
                    state.initial_length = est_before if est_before > 0 else new_length
            state.last_length = new_length
        elif state.initial_length is None and plan.pre_edit_length is not None and plan.pre_edit_length > 0:
            state.initial_length = plan.pre_edit_length

        new_wc = data.get("new_word_count")
        if isinstance(new_wc, int) and not isinstance(new_wc, bool):
            state.last_word_count = new_wc

        # Pure append is exempt from all counters and hints.
        if plan.is_append:
            return output_text

        state.cumulative_replaced_chars += plan.arg_old_chars

        if plan.written_this_turn:
            if plan.is_bulk and state.post_write_bulk_calls < POST_WRITE_BULK_ALLOWANCE:
                state.post_write_bulk_calls += 1
                return output_text
            state.post_write_clean_calls += 1
            if state.post_write_clean_calls == POST_WRITE_HINT_AT:
                return self._attach_hint(
                    payload,
                    self._post_write_hint(plan.file_id, state),
                    file_id=plan.file_id,
                    written_this_turn=True,
                    clean_calls=state.post_write_clean_calls,
                    consecutive_micro_calls=state.consecutive_micro_calls,
                    reason="post_write_hint",
                )
            return output_text

        # Existing file
        if plan.is_bulk:
            # Bulk batches (>= 6 edits) are exempt from micro-polish caps and break any micro streak.
            state.consecutive_micro_calls = 0
            return output_text

        state.clean_non_bulk_calls += 1
        if (
            plan.pre_edit_length is not None
            and isinstance(new_length, int)
            and not isinstance(new_length, bool)
        ):
            net_delta = abs(new_length - plan.pre_edit_length)
        else:
            net_delta = abs(plan.arg_new_chars - plan.arg_old_chars)

        is_micro = 1 <= plan.edit_count <= MICRO_EDIT_MAX_ITEMS and net_delta <= MICRO_EDIT_MAX_NET_DELTA
        if is_micro:
            state.consecutive_micro_calls += 1
        else:
            state.consecutive_micro_calls = 0

        if state.clean_non_bulk_calls >= EXISTING_HINT_AT:
            return self._attach_hint(
                payload,
                self._existing_file_hint(plan.file_id, state),
                file_id=plan.file_id,
                written_this_turn=False,
                clean_calls=state.clean_non_bulk_calls,
                consecutive_micro_calls=state.consecutive_micro_calls,
                reason="existing_hint",
            )
        return output_text

    def _observe_batch_replace(self, payload: dict[str, Any]) -> None:
        """Track cross-file ``batch_replace_files`` commits and stale-read recovery unlocks."""
        data = payload.get("data")
        if not isinstance(data, dict):
            return
        for skipped in data.get("skipped_files") or []:
            if not isinstance(skipped, dict):
                continue
            fid = str(skipped.get("id") or "").strip()
            reason = str(skipped.get("reason") or "").strip()
            if fid and reason in _SELF_READ_RECOVERY_ERROR_TYPES:
                self._allow_self_read.add(fid)
        if coerce_bool(data.get("dry_run"), default=False):
            return
        status = str(payload.get("status") or "").strip().lower()
        if status not in {"success", "partial"} or payload.get("mutation_applied") is not True:
            return
        for item in data.get("files") or []:
            if not isinstance(item, dict):
                continue
            fid = str(item.get("id") or "").strip()
            if not fid:
                continue
            self._allow_self_read.discard(fid)
            state = self._files.setdefault(fid, _FileEditState())
            if isinstance(item.get("title"), str) and item["title"]:
                state.title = item["title"]
            if isinstance(item.get("file_type"), str) and item["file_type"]:
                state.file_type = item["file_type"].strip().lower()
            new_len = item.get("new_length")
            if isinstance(new_len, int) and not isinstance(new_len, bool):
                if state.initial_length is None:
                    state.initial_length = new_len
                state.last_length = new_len
            new_wc = item.get("new_word_count")
            if isinstance(new_wc, int) and not isinstance(new_wc, bool):
                state.last_word_count = new_wc
            # Deterministic cross-file replace is not a serial micro-polish loop.
            state.consecutive_micro_calls = 0

    def _merge_or_strip_guarded_self_reads(
        self,
        plan: EditPlan,
        output_text: str,
        *,
        parsed_output: dict[str, Any] | None = None,
    ) -> str:
        """Merge slim ``content_omitted=True`` rows for excluded just-written chapters when
        ``plan.arguments`` was rewritten, or strip them defense-in-depth if caller passed raw args."""
        payload = parsed_output if parsed_output is not None else _load_json(output_text)
        if payload is None:
            return output_text

        guarded_ids = list(plan.self_read_guarded_ids)
        guarded_set = set(guarded_ids)

        if payload.get("status") != "success":
            if plan.arguments is None:
                return output_text
            # Caller executed the rewritten batch (without guarded_ids) and all remaining IDs
            # failed/missed; attach guarded metadata note so the model still knows why they were omitted.
            labels: list[str] = []
            for fid in guarded_ids:
                _row, title, _wc = self._self_read_row(fid)
                labels.append(f"《{title}》" if title else f"文件(id={fid})")
            self.guarded_self_reads += 1
            self._record_intercept(guarded_ids, increment_consecutive=False)
            patched = dict(payload)
            patched["self_read_guarded_ids"] = guarded_ids
            hint = (
                f"{'、'.join(labels)}是本轮刚写完并保存的正文，本次批量读取已跳过其读取；"
                "请直接对照上方内容继续任务，不要重读刚写完的章节。"
            )
            existing_hint = patched.get("read_hint")
            patched["read_hint"] = f"{existing_hint}\n{hint}" if isinstance(existing_hint, str) and existing_hint else hint
            return json.dumps(patched, ensure_ascii=False, separators=(",", ":"))

        data = payload.get("data")
        if not isinstance(data, list):
            return output_text

        handled_by_id: dict[str, dict[str, Any]] = {}
        labels_by_id: dict[str, str] = {}
        non_guarded_rows: list[Any] = []
        row_by_id: dict[str, dict[str, Any]] = {}

        for row in data:
            if isinstance(row, dict):
                fid = str(row.get("id") or "").strip()
                if fid in guarded_set:
                    # Defense-in-depth: strip body if caller did not pass rewritten plan.arguments.
                    slim_row, title, word_count = self._self_read_row(fid)
                    if not slim_row.get("title") and isinstance(row.get("title"), str):
                        slim_row["title"] = row["title"]
                        title = row["title"]
                    if word_count is None and isinstance(row.get("word_count"), int):
                        slim_row["word_count"] = row["word_count"]
                    handled_by_id[fid] = slim_row
                    labels_by_id[fid] = f"《{title}》" if title else f"文件(id={fid})"
                    row_by_id[fid] = slim_row
                    continue
                if fid:
                    row_by_id[fid] = row
            non_guarded_rows.append(row)

        # Rewritten-arguments path: merge slim rows back for guarded_ids excluded before DB execution.
        if plan.arguments is not None:
            for fid in guarded_ids:
                if fid in handled_by_id:
                    continue
                slim_row, title, _word_count = self._self_read_row(fid)
                handled_by_id[fid] = slim_row
                labels_by_id[fid] = f"《{title}》" if title else f"文件(id={fid})"
                row_by_id[fid] = slim_row

        if not handled_by_id:
            return output_text

        ordered_guarded = [fid for fid in guarded_ids if fid in handled_by_id]
        if plan.requested_ids:
            new_data: list[Any] = []
            emitted_ids: set[str] = set()
            for fid in plan.requested_ids:
                if fid in row_by_id and fid not in emitted_ids:
                    new_data.append(row_by_id[fid])
                    emitted_ids.add(fid)
            for row in non_guarded_rows:
                fid = str(row.get("id") or "").strip() if isinstance(row, dict) else ""
                if not fid or fid not in emitted_ids:
                    new_data.append(row)
        else:
            new_data = [*non_guarded_rows, *(handled_by_id[fid] for fid in ordered_guarded)]

        labels = [labels_by_id[fid] for fid in ordered_guarded]
        self.guarded_self_reads += 1
        self._record_intercept(ordered_guarded, increment_consecutive=False)
        log_with_context(
            logger,
            20,  # INFO
            "RepeatEditGuard guarded just-written chapter in batch query_files",
            file_ids=ordered_guarded,
            rewritten=plan.arguments is not None,
            per_file_intercepts={fid: self._per_file_intercepts.get(fid, 0) for fid in ordered_guarded},
        )
        patched = dict(payload)
        patched["data"] = new_data
        patched["self_read_guarded_ids"] = ordered_guarded
        hint = (
            f"{'、'.join(labels)}是本轮刚写完并保存的正文，本次批量读取已省略其正文以避免重复灌入上下文；"
            "请直接对照上方内容继续任务，不要重读刚写完的章节。"
        )
        existing_hint = patched.get("read_hint")
        patched["read_hint"] = f"{existing_hint}\n{hint}" if isinstance(existing_hint, str) and existing_hint else hint
        return json.dumps(patched, ensure_ascii=False, separators=(",", ":"))

    # --------------------------------------------------------------- helpers
    def _current_word_count(self, file_id: str, state: _FileEditState | None) -> int | None:
        if state is not None and state.last_word_count is not None:
            return state.last_word_count
        snap = _snapshot_content(self.request_context, file_id)
        if snap is not None:
            return count_words(snap)
        return None

    def _post_write_hint(self, file_id: str, state: _FileEditState) -> str:
        title = state.title or _saved_title(self.request_context, file_id)
        label = f"《{title}》" if title else "这一章"
        return (
            f"{label}是本轮刚写完的章节，已完成 {state.post_write_clean_calls} 次修改。"
            "请停止追逐精确字数或继续零碎微调（下一次 edit_file 将被拦截）："
            "如有其他待办请继续，否则直接按当前真实字数回复作者。"
        )

    def _existing_file_hint(self, file_id: str, state: _FileEditState) -> str:
        title = state.title or _saved_title(self.request_context, file_id)
        label = f"《{title}》" if title else "该文件"
        parts = [
            f"{label}本轮已连续调用 {state.clean_non_bulk_calls} 次小批量 edit_file。"
            "若还有剩余修改，请一次性合并进同一 edits 列表（continue_on_error=true）提交，不要再为了卡字数或措辞反复微调。"
        ]
        base_len = state.initial_length or state.last_length or 0
        if (
            base_len > 0
            and state.cumulative_replaced_chars >= base_len * CUMULATIVE_REWRITE_HINT_RATIO
            and state.file_type in _CHAPTER_FILE_TYPES
        ):
            parts.append("累计替换字数已超过全文 60%，如需整体重写请改用 write_chapter，不要零敲碎打逐段重写。")
        return "".join(parts)

    def _attach_hint(
        self,
        payload: dict[str, Any],
        hint: str,
        *,
        file_id: str,
        written_this_turn: bool,
        clean_calls: int,
        consecutive_micro_calls: int,
        reason: str,
    ) -> str:
        self.hinted_edits += 1
        log_with_context(
            logger,
            20,  # INFO
            "RepeatEditGuard hint attached",
            file_id=file_id,
            written_this_turn=written_this_turn,
            clean_calls=clean_calls,
            consecutive_micro_calls=consecutive_micro_calls,
            reason=reason,
        )
        hinted = dict(payload)
        hinted["edit_hint"] = hint
        return json.dumps(hinted, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "BULK_EDIT_MIN_ITEMS",
    "CUMULATIVE_REWRITE_HINT_RATIO",
    "EDIT_GUARD_ERROR_TYPES",
    "EDIT_GUARD_ESCALATED_ERROR_TYPE",
    "EDIT_REPEAT_LIMIT_ERROR_TYPE",
    "EXISTING_HARD_BLOCK_AT",
    "EXISTING_HINT_AT",
    "EXISTING_MICRO_BLOCK_AT",
    "EXISTING_MICRO_STREAK_MIN",
    "MAX_CONSECUTIVE_GUARD_INTERCEPTS",
    "MAX_PER_FILE_GUARD_INTERCEPTS",
    "MICRO_EDIT_MAX_ITEMS",
    "MICRO_EDIT_MAX_NET_DELTA",
    "POST_WRITE_BLOCK_AT",
    "POST_WRITE_BULK_ALLOWANCE",
    "POST_WRITE_HINT_AT",
    "EditPlan",
    "RepeatEditGuard",
]
