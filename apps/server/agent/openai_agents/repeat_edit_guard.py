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
- Failed or partial calls (``status != "success"`` or ``mutation_applied=False``)
  — handled by ``ToolFailureBreaker``;
- Pure ``op="append"`` calls (e.g. ``_BODY_CUT_CONTEXT`` truncation continuation);
- Bulk batches (``len(edits) >= 6``) on existing files so legitimate 10–30 edit
  outline/chapter sweeps never get blocked;
- Different ``file_id`` targets are tracked independently.

Also provides a lightweight self-read guard: ``query_files(id=...)`` (full read
or ``start_char=0``) on a chapter just written via ``write_chapter`` in the same
request returns current metadata (including ``word_count``) and a concise hint
instead of re-injecting the just-written body into context, unless the file just
hit ``anchor_not_found`` or ``stale_content`` and needs a recovery read.
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
READ_TOOL_NAME = "query_files"

EDIT_REPEAT_LIMIT_ERROR_TYPE = "edit_repeat_limit"
# Excluded from ToolFailureBreaker so hitting a micro-polish cap steers the
# model to finish cleanly rather than tripping a fatal circuit breaker.
EDIT_GUARD_ERROR_TYPES: frozenset[str] = frozenset({EDIT_REPEAT_LIMIT_ERROR_TYPE})

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

# Total edit_repeat_limit soft-skips in a single request before triggering a clean
# synchronous early stop via runner._stop_run_after_tools so the model cannot spin
# against the guard until the 540s wall-clock cap.
MAX_BLOCKED_EDITS_PER_REQUEST = 3

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
    raw_edits = args.get("edits")
    if raw_edits is None:
        raw_edits = args.get("operations")
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
            old_val = item.get("old") if isinstance(item.get("old"), str) else item.get("from")
            new_val = item.get("new") if isinstance(item.get("new"), str) else item.get("to")
            if isinstance(old_val, str):
                old_chars += len(old_val)
            if isinstance(new_val, str):
                new_chars += len(new_val)
            if coerce_bool(item.get("replace_all"), default=False):
                has_replace_all = True
        elif op == "delete":
            old_val = item.get("old") if isinstance(item.get("old"), str) else item.get("from")
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
    edit_count: int = 0
    is_append: bool = False
    is_bulk: bool = False
    arg_old_chars: int = 0
    arg_new_chars: int = 0
    pre_edit_length: int | None = None
    written_this_turn: bool = False
    self_read_recovery_id: str = ""
    blocked_output: str | None = None


@dataclass
class RepeatEditGuard:
    """Per-request structural guard against serial micro-edit loops and post-write self-reads.

    Shared across continuation runs within a single user request (just like
    ``ToolFailureBreaker`` and ``RepeatReadGuard``). Does NOT trip a fatal error;
    when a micro-edit loop hits its cap, it synchronously returns a recoverable
    ``edit_repeat_limit`` payload with the current ``word_count`` so the model
    stops polishing and replies or proceeds to the next task.
    """

    request_context: Any = field(default=None, repr=False, compare=False)
    blocked_edits: int = 0
    hinted_edits: int = 0
    guarded_self_reads: int = 0
    _files: dict[str, _FileEditState] = field(default_factory=dict)
    _allow_self_read: set[str] = field(default_factory=set)

    @property
    def spin_limit_reached(self) -> bool:
        """Whether repeated ``edit_repeat_limit`` soft-skips reached the early-stop cap."""
        return self.blocked_edits >= MAX_BLOCKED_EDITS_PER_REQUEST

    def short_circuit_text(self, tool_name: str, raw_arguments: str = "") -> str:
        """Return a non-error soft-skip payload for trailing tool calls once spin_limit_reached."""
        file_id = ""
        word_count: int | None = None
        if raw_arguments:
            args, parse_error, _ = parse_json_object(raw_arguments, tool_name=tool_name)
            if parse_error is None and isinstance(args, dict):
                file_id = _extract_file_id(args)
                if file_id:
                    word_count = self._current_word_count(file_id, self._files.get(file_id))
        payload: dict[str, Any] = {
            "status": "skipped",
            "guard_skipped": True,
            "reason": "edit_spin_limit",
            "message": (
                f"本轮已多次触达连续微调上限，{tool_name} 未执行。"
                "之前的修改均已保存，请直接向作者简短说明已完成的结果。"
            ),
            "error_type": EDIT_REPEAT_LIMIT_ERROR_TYPE,
            "tool_name": tool_name,
            "edits_applied": 0,
            "mutation_applied": False,
            "user_message": "这一步的细微调整已跳过，之前的修改都已保存。",
        }
        if file_id:
            payload["id"] = file_id
        if word_count is not None:
            payload["word_count"] = word_count
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    # ------------------------------------------------------------------ plan
    def plan(self, tool_name: str, raw_arguments: str) -> EditPlan:
        if tool_name == EDIT_TOOL_NAME:
            return self._plan_edit(raw_arguments)
        if tool_name == READ_TOOL_NAME:
            return self._plan_self_read(raw_arguments)
        return EditPlan()

    def _plan_edit(self, raw_arguments: str) -> EditPlan:
        args, parse_error, _ = parse_json_object(raw_arguments or "", tool_name=EDIT_TOOL_NAME)
        if parse_error is not None or not isinstance(args, dict):
            return EditPlan()

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

    def _plan_self_read(self, raw_arguments: str) -> EditPlan:
        """Guard against re-reading a chapter just written via write_chapter in the same run."""
        if self.request_context is None:
            return EditPlan()
        args, parse_error, _ = parse_json_object(raw_arguments or "", tool_name=READ_TOOL_NAME)
        if parse_error is not None or not isinstance(args, dict):
            return EditPlan()

        # Keyword search, section read, or tail read are not whole-chapter self-reads.
        raw_query = args.get("query")
        if isinstance(raw_query, str) and raw_query.strip():
            return EditPlan()
        if args.get("section") not in (None, "") or args.get("tail_chars") not in (None, ""):
            return EditPlan()

        start_char = args.get("start_char")
        if start_char not in (None, "") and not _is_zero_offset(start_char):
            return EditPlan()

        try:
            target_ids, _truncated = normalize_query_file_ids(
                file_id=args.get("id"),
                file_ids=args.get("ids"),
                max_ids=QUERY_FILES_MAX_BATCH_IDS,
            )
        except ValueError:
            return EditPlan()
        if len(target_ids) != 1:
            return EditPlan()

        file_id = target_ids[0]
        if not _is_written_this_turn(self.request_context, file_id):
            return EditPlan()

        if file_id in self._allow_self_read:
            return EditPlan(file_id=file_id, self_read_recovery_id=file_id)

        state = self._files.get(file_id)
        title = (state.title if state is not None and state.title else "") or _saved_title(
            self.request_context, file_id
        )
        word_count = self._current_word_count(file_id, state)
        self.guarded_self_reads += 1
        log_with_context(
            logger,
            20,  # INFO
            "RepeatEditGuard self-read guarded",
            file_id=file_id,
            start_char=start_char,
            word_count=word_count,
        )

        label = f"《{title}》" if title else f"文件(id={file_id})"
        wc_part = f"（当前真实字数 {word_count} 字）" if word_count is not None else ""
        row: dict[str, Any] = {
            "id": file_id,
            "title": title,
            "content_omitted": True,
        }
        if word_count is not None:
            row["word_count"] = word_count
        payload: dict[str, Any] = {
            "status": "success",
            "self_read_guarded": True,
            "data": [row],
            "read_hint": (
                f"{label}是本轮刚写完并保存的正文{wc_part}，完整内容已在上方你刚写出的内容中，"
                "无需再次读取原文。请直接对照上方内容继续未完成任务或按真实字数简短回复作者；"
                "不要为了卡字数或微调措辞重读或反复修改。"
            ),
        }
        return EditPlan(
            file_id=file_id,
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
        title = state.title or _saved_title(self.request_context, file_id)
        label = f"《{title}》" if title else f"文件(id={file_id})"
        word_count = self._current_word_count(file_id, state)
        wc_part = f"（当前真实字数 {word_count} 字）" if word_count is not None else ""

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
            reason=reason,
        )

        payload: dict[str, Any] = {
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
    def observe(self, tool_name: str, plan: EditPlan, output_text: str) -> str:
        if tool_name == READ_TOOL_NAME:
            if plan.self_read_recovery_id:
                self._allow_self_read.discard(plan.self_read_recovery_id)
            return output_text

        if tool_name != EDIT_TOOL_NAME or not plan.file_id:
            return output_text

        payload = _load_json(output_text)
        if payload is None:
            return output_text

        status = payload.get("status")
        if status != "success":
            error_type = str(payload.get("error_type") or "")
            if error_type in _SELF_READ_RECOVERY_ERROR_TYPES:
                self._allow_self_read.add(plan.file_id)
            return output_text

        if payload.get("mutation_applied") is False:
            return output_text

        # A clean successful edit clears any pending self-read recovery allowance.
        self._allow_self_read.discard(plan.file_id)

        state = self._files.setdefault(plan.file_id, _FileEditState())
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
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
    "EDIT_REPEAT_LIMIT_ERROR_TYPE",
    "EXISTING_HARD_BLOCK_AT",
    "EXISTING_HINT_AT",
    "EXISTING_MICRO_BLOCK_AT",
    "EXISTING_MICRO_STREAK_MIN",
    "MAX_BLOCKED_EDITS_PER_REQUEST",
    "MICRO_EDIT_MAX_ITEMS",
    "MICRO_EDIT_MAX_NET_DELTA",
    "POST_WRITE_BLOCK_AT",
    "POST_WRITE_BULK_ALLOWANCE",
    "POST_WRITE_HINT_AT",
    "EditPlan",
    "RepeatEditGuard",
]
