"""Request-scoped agent execution context and shared mutable state holders.

Holds the strong-typed ``AgentRequestContext`` dataclass along with its
request-scoped mutable state containers (``_PendingEmptyFileState``,
``_ReadSnapshots``, and ``_ToolUiPayloadStore``), separated from the MCP tool
handlers in ``agent.tools.mcp_tools``.
"""

from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import difflib
import threading
from collections.abc import Callable, Iterator, Sequence
from typing import Any

from sqlmodel import Session

from agent.constants import CONTENT_FILE_TYPES
from agent.skills.content_budget import SkillContentBudget
from agent.tools.file_ops import FileToolExecutor
from services.usage.cost_budget import set_context_user_id
from utils.logger import get_logger

logger = get_logger(__name__)

# Per-invocation tool call id (copied cleanly into asyncio.to_thread worker
# threads and reset deterministically by AgentRequestContext.tool_call_scope).
_current_tool_call_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_tool_call_id", default=None
)


class _PendingEmptyFileState:
    """待写入空文件标记的可变持有者，随 ``AgentRequestContext`` 在所有子任务与工作线程间共享。

    ``AgentRequestContext`` 作为单一对象实例通过 ``RunContextWrapper[AgentRequestContext]``
    及显式参数传给 SDK 回调、工具工作线程、``StreamAdapter`` 与 ``loop/engine``；状态变更
    原地写入本容器并由同一把 ``threading.Lock`` 保护，天然跨 ``asyncio.create_task`` 与
    ``asyncio.to_thread`` 可见。

    结构从"单槽"改成"按 file_id 的集合"，并把检查与置位收进同一把
    ``threading.Lock``，原因有两条（均为实测缺陷）：

    1. 单槽会被后写者静默覆盖。两个并发的空文件 create_file 各自置位时，先建
       的那个空文件从此没有任何指针——文件躺在文件树里永远是空的，而工具却向
       模型报告全部成功。
    2. "先查 pending 再建文件"之间隔着 DB INSERT，PG 下还跨 asyncio.to_thread
       的线程边界，是典型 TOCTOU：两个任务会同时通过检查。因此对外只暴露
       ``try_reserve``（占坑）+ ``bind``（回填真实 file_id）/``release``（回滚）
       这一组原子操作，调用方不得再自己"先查后置"。

    另有一层"正文已在模型回复里写完"的标记（_streamed）：SDK 的 run loop 在
    执行本轮工具之前就 await 了 on_llm_end，而 StreamAdapter 是在消费侧滞后
    处理 </file>、落库后才按 file_id 清条目。同一条回复里"<file>A</file> +
    create_file(B)"时，create_file(B) 跑在 adapter 落库之前，会被 A 的条目误拒。
    on_llm_end 钩子确认本条回复里有闭合的 <file> 块后调 mark_body_streamed，
    try_reserve 只被尚未写出正文的条目挡住；条目本身仍由 adapter 落库后清除，
    has_pending / latest / snapshot 的语义不变（单循环 agent 的 run 边界核验照旧兜底）。
    """

    __slots__ = (
        "_lock", "_entries", "_file_types", "_repeat_absorptions", "_overdue_non_conflicting_uses",
        "_reservations", "_streamed",
        "_reply_seq", "_created_in", "_versions", "_prebody_reply", "_waiting_at_reply_start",
        "_replacements", "_closed", "_replacement_saved", "_chapter_bodies_saved",
        "_saved_body_types", "_cut_bodies_saved",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # file_id -> title，按插入顺序保存全部待写入空文件
        self._entries: dict[str, str] = {}
        # file_id -> 规范化 file_type（小写，未知时省略），供同目标重复守卫校验类型
        self._file_types: dict[str, str] = {}
        # file_id -> 本待写周期内已吸收的同目标重复 write_chapter / create_file 次数（上限 1）
        self._repeat_absorptions: dict[str, int] = {}
        # file_id -> 本待写周期 overdue 后放行的非冲突辅助工具插队次数（P1-5 上限 2）
        self._overdue_non_conflicting_uses: dict[str, int] = {}
        # ticket -> title，正在建库途中（还没拿到真实 file_id）的占坑
        self._reservations: dict[str, str] = {}
        # 正文已以闭合 <file> 块出现在模型回复里、只差 adapter 落库的条目
        self._streamed: set[str] = set()
        # 第几次模型回复（每次模型调用开始时 +1），以及每个待写文件是在第几次回复里建的：
        # 上一次回复建的文件，这一次回复必须先写它的正文（见 overdue_unwritten_files）。
        self._reply_seq = 0
        self._created_in: dict[str, int] = {}
        # create_file 结果里的版本（updated_at）：补不成时只回滚这之后没被任何写入动过的空文件。
        self._versions: dict[str, str] = {}
        # 没有待写文件时，这次回复先写出了一段闭合的 <file> 正文（第几次回复）：
        # 同一回复里第一个建空文件的 create_file 认领它（见 offer_reply_prebody）。
        self._prebody_reply: int | None = None
        # 这次回复开始时有没有还在等正文的文件。不能在回复结束时再看：StreamAdapter 边收边存，
        # 回复里写完 </file> 就会落库并清掉那个文件的条目，往往早于 on_llm_end。
        self._waiting_at_reply_start = False
        # write_chapter 定为整章替换、在等正文的已有章节：file_id -> 定位时的结果数据
        # （与本请求新建的空文件区分开，同一回复里再次 write_chapter 这一章时不能当成新建）。
        self._replacements: dict[str, dict[str, Any]] = {}
        # 这一轮已被 StreamAdapter 提前结束（整章替换被拒）：之后才建好的空文件由建它的工具自己撤销。
        self._closed = False
        # 本请求里 write_chapter 定过的整章替换：file_id -> 最近一次定位之后新正文是否已经落库
        # （StreamAdapter 提交 replace_body 成功后置 True）。条目在待写守卫清掉之后仍留着，
        # loop 引擎据此判断续跑说明能不能说「已保存」。
        self._replacement_saved: dict[str, bool] = {}
        # 本请求里 write_chapter 已经把正文写完并落库的章节（新建或整章替换，StreamAdapter 确认保存后记下）。
        # 待写条目清掉之后仍留着：同一请求里再对这一章 write_chapter 时拒绝整章重写（见 _write_chapter_replace）。
        # 本请求完整保存过正文的文件：id -> 标题（StreamAdapter._note_body_saved 记）。
        self._chapter_bodies_saved: dict[str, str] = {}
        # 同一批文件的类型（保存点知道时记下；空串表示不知道）：保存后自查只针对正文类文件。
        self._saved_body_types: dict[str, str] = {}
        # 没等到 </file>、按流结束补全保存下来的新正文：id -> 标题（StreamAdapter 保存成功后记下）。
        # loop 引擎在 run 边界取走（take_cut_bodies），结合 runner 的 output_capped 判断正文是否被输出上限截断。
        self._cut_bodies_saved: dict[str, str] = {}

    # ---- 只读视图 ----------------------------------------------------------
    def has_pending(self) -> bool:
        with self._lock:
            return bool(self._entries)

    def latest(self) -> dict[str, str] | None:
        """返回最近一次置位的待写入文件（与历史单槽语义一致）。"""
        with self._lock:
            if not self._entries:
                return None
            file_id, title = next(reversed(self._entries.items()))
            return {"file_id": file_id, "title": title}

    def snapshot(self) -> list[dict[str, str]]:
        """返回全部待写入文件（按置位先后）。"""
        with self._lock:
            return [
                {"file_id": file_id, "title": title}
                for file_id, title in self._entries.items()
            ]

    def entry_for(self, file_id: str) -> dict[str, Any] | None:
        """file_id 当前在待写入集合里时返回 {file_id, title, created_here, file_type}，否则为 None。"""
        with self._lock:
            if file_id not in self._entries:
                return None
            return {
                "file_id": file_id,
                "title": self._entries[file_id],
                "created_here": file_id in self._versions,
                "file_type": self._file_types.get(file_id, ""),
            }

    # ---- 写入 --------------------------------------------------------------
    def begin_reply(self) -> None:
        """一次新的模型调用开始。"""
        with self._lock:
            self._reply_seq += 1
            self._waiting_at_reply_start = any(file_id not in self._streamed for file_id in self._entries)

    def overdue_unwritten_files(self) -> list[dict[str, Any]]:
        """上一次（或更早的）回复建好、这次回复还没写出正文的文件：精确 {id, title, created_here, file_type}（按置位先后）。

        created_here：本请求新建、记下了建好时版本的文件（复用的已有剧集没有，可能已有正文）。
        """
        with self._lock:
            return [
                {
                    "id": file_id,
                    "title": title,
                    "created_here": file_id in self._versions,
                    "file_type": self._file_types.get(file_id, ""),
                }
                for file_id, title in self._entries.items()
                if file_id not in self._streamed and self._created_in.get(file_id, self._reply_seq) < self._reply_seq
            ]

    def _set_file_type_locked(self, file_id: str, file_type: str | None) -> None:
        normalized = file_type.strip().lower() if isinstance(file_type, str) else ""
        if normalized:
            self._file_types[file_id] = normalized
        else:
            self._file_types.pop(file_id, None)

    def set_file_type(self, file_id: str, file_type: str | None) -> None:
        """为当前仍在待写入集合里的 file_id 补充或更新规范化 file_type。"""
        normalized = file_type.strip().lower() if isinstance(file_type, str) else ""
        if not file_id or not normalized:
            return
        with self._lock:
            if file_id in self._entries:
                self._file_types[file_id] = normalized

    def consume_repeat_absorption(self, file_id: str, *, max_repeats: int = 1) -> bool:
        """为当前待写 file_id 消耗一次同目标重复调用吸收额度；超限时返回 False。"""
        if not file_id or max_repeats <= 0:
            return False
        with self._lock:
            if file_id not in self._entries:
                return False
            used = self._repeat_absorptions.get(file_id, 0)
            if used >= max_repeats:
                return False
            self._repeat_absorptions[file_id] = used + 1
            return True

    def consume_overdue_non_conflicting(self, file_id: str, *, max_uses: int = 2) -> bool:
        """P1-5：为当前 overdue 待写 file_id 消耗一次非冲突辅助工具插队额度；超限时返回 False。"""
        if not file_id or max_uses <= 0:
            return False
        with self._lock:
            if file_id not in self._entries:
                return False
            used = self._overdue_non_conflicting_uses.get(file_id, 0)
            if used >= max_uses:
                return False
            self._overdue_non_conflicting_uses[file_id] = used + 1
            return True

    def add(
        self,
        file_id: str,
        title: str,
        version: str | None = None,
        *,
        file_type: str | None = None,
    ) -> None:
        with self._lock:
            self._created_in[file_id] = self._reply_seq
            self._entries[file_id] = title
            self._set_version(file_id, version)
            self._set_file_type_locked(file_id, file_type)
            self._repeat_absorptions.pop(file_id, None)
            self._overdue_non_conflicting_uses.pop(file_id, None)
            self._replacements.pop(file_id, None)
            # 同一文件重新进入待写：旧的"正文已写出"标记不能沿用
            self._streamed.discard(file_id)

    def offer_reply_prebody(self) -> bool:
        """本次回复先写出了正文：回复开始时没有在等正文的文件就记下，返回是否记下。

        有这样的文件时，这段正文按两次回复协议属于它（mark_latest_body_streamed），不归下一个新文件；
        即使 StreamAdapter 已经把它存好、清掉了条目也一样。
        """
        with self._lock:
            if self._waiting_at_reply_start or any(file_id not in self._streamed for file_id in self._entries):
                return False
            self._prebody_reply = self._reply_seq
            return True

    def take_reply_prebody(self) -> bool:
        """本次回复先写出的正文还没被认领：认领它（只认领一次）。"""
        with self._lock:
            if self._prebody_reply is None or self._prebody_reply != self._reply_seq:
                return False
            self._prebody_reply = None
            return True

    def mark_body_streamed(self, file_id: str) -> None:
        """记录该待写文件的正文已以闭合 <file> 块完整出现在模型回复里。"""
        with self._lock:
            if file_id in self._entries:
                self._streamed.add(file_id)

    def mark_latest_body_streamed(self) -> None:
        """同 mark_body_streamed，作用于最近置位的条目（取条目与置位在同一把锁内）。"""
        with self._lock:
            if self._entries:
                self._streamed.add(next(reversed(self._entries)))

    def rearm(self, file_id: str) -> bool:
        """纠偏轮重新等这个文件的正文：没被捕获的「正文已写出」标记作废，建于第几次回复不变。

        返回这个文件是否仍是待写条目（不是就什么也不做）。
        """
        with self._lock:
            if file_id not in self._entries:
                return False
            self._streamed.discard(file_id)
            return True

    def discard(self, file_id: str | None = None) -> None:
        """file_id 为 None 时清空全部，否则只摘掉指定条目。"""
        with self._lock:
            if file_id is None:
                self._entries.clear()
                self._file_types.clear()
                self._repeat_absorptions.clear()
                self._overdue_non_conflicting_uses.clear()
                self._created_in.clear()
                self._streamed.clear()
                self._versions.clear()
                self._replacements.clear()
            else:
                self._entries.pop(file_id, None)
                self._file_types.pop(file_id, None)
                self._repeat_absorptions.pop(file_id, None)
                self._overdue_non_conflicting_uses.pop(file_id, None)
                self._created_in.pop(file_id, None)
                self._streamed.discard(file_id)
                self._versions.pop(file_id, None)
                self._replacements.pop(file_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._entries.clear()
            self._file_types.clear()
            self._repeat_absorptions.clear()
            self._overdue_non_conflicting_uses.clear()
            self._created_in.clear()
            self._reservations.clear()
            self._streamed.clear()
            self._versions.clear()
            self._replacements.clear()
            self._replacement_saved.clear()
            self._chapter_bodies_saved.clear()
            self._saved_body_types.clear()
            self._cut_bodies_saved.clear()
            self._prebody_reply = None
            self._waiting_at_reply_start = False
            self._closed = False

    def version(self, file_id: str) -> str | None:
        """这个待写文件建好时的版本；不是待写条目或没有记下时为 None。"""
        with self._lock:
            return self._versions.get(file_id) if file_id in self._entries else None

    def close(self) -> list[dict[str, str]]:
        """标记这一轮不再接收新的待写文件，并返回此刻全部待写条目（带建好时的版本）。

        与 bind 同一把锁：在这之前绑定的条目都在返回值里，之后绑定的由 is_closed 交给建它的工具处理。
        """
        with self._lock:
            self._closed = True
            return [
                {"file_id": file_id, "title": title, **({"updated_at": self._versions[file_id]} if file_id in self._versions else {})}
                for file_id, title in self._entries.items()
            ]

    def is_closed(self) -> bool:
        with self._lock:
            return self._closed

    def replacement(self, file_id: str) -> dict[str, Any] | None:
        """这个待写条目是 write_chapter 定下的整章替换时返回定位时的结果数据，否则 None。"""
        with self._lock:
            found = self._replacements.get(file_id) if file_id in self._entries else None
            return dict(found) if found is not None else None

    def mark_replacement_saved(self, file_id: str) -> None:
        with self._lock:
            if file_id in self._replacement_saved:
                self._replacement_saved[file_id] = True

    def unsaved_replacement_ids(self) -> set[str]:
        with self._lock:
            return {file_id for file_id, saved in self._replacement_saved.items() if not saved}

    def mark_chapter_body_saved(self, file_id: str, title: str = "", file_type: str = "") -> None:
        with self._lock:
            self._chapter_bodies_saved[file_id] = title or self._chapter_bodies_saved.get(file_id, "")
            if file_type:
                self._saved_body_types[file_id] = file_type.strip().lower()

    def mark_cut_body_saved(self, file_id: str, title: str = "") -> None:
        with self._lock:
            self._cut_bodies_saved[file_id] = title

    def take_cut_bodies(self) -> dict[str, str]:
        with self._lock:
            taken = dict(self._cut_bodies_saved)
            self._cut_bodies_saved.clear()
            return taken

    def chapter_body_saved(self, file_id: str) -> bool:
        with self._lock:
            return file_id in self._chapter_bodies_saved

    def saved_body_titles(self) -> dict[str, str]:
        with self._lock:
            return dict(self._chapter_bodies_saved)

    def saved_content_body_ids(self) -> list[str]:
        """已保存正文里的正文类文件（draft / script）；类型不明的也算，宁可多自查一次。"""
        with self._lock:
            return [
                file_id
                for file_id in self._chapter_bodies_saved
                if self._saved_body_types.get(file_id, "") in {"", *CONTENT_FILE_TYPES}
            ]

    def _set_version(self, file_id: str, version: str | None) -> None:
        if isinstance(version, str) and version:
            self._versions[file_id] = version
        else:
            self._versions.pop(file_id, None)

    # ---- 原子占坑 ----------------------------------------------------------
    def try_reserve(self, ticket: str, title: str) -> tuple[bool, str | None]:
        """原子地占用"待流式写入"名额。

        Returns:
            (ok, blocking_title)。ok 为 False 时 blocking_title 是挡住本次创建的
            文件标题，供调用方拼错误信息。
        """
        with self._lock:
            if self._reservations:
                # 有别的 create_file 正在建库途中：这是真正的并发竞争，
                # 直接拒绝且不计入陈旧计数（对方马上就会 bind 或 release）。
                return False, next(iter(self._reservations.values()))

            # 正文已在本条回复里写完、只等 adapter 落库的条目不再挡路
            unwritten = [
                title_
                for file_id, title_ in self._entries.items()
                if file_id not in self._streamed
            ]
            if unwritten:
                # Never discard unfinished artifacts merely because the model
                # repeated an invalid action. The workflow boundary owns recovery
                # (finish or roll back); forgetting the marker silently leaves an
                # empty file in the project.
                return False, unwritten[-1]

            self._reservations[ticket] = title
            return True, None

    def sole_unwritten_entry(self) -> dict[str, str] | None:
        """没有建库途中的占坑、且恰好只有一个尚未写出正文的条目时返回它，否则 None。

        create_file 被挡住时用来判断"模型是不是在重复创建正挡路的那个文件"。
        """
        with self._lock:
            if self._reservations:
                return None
            unwritten = [
                (file_id, title_)
                for file_id, title_ in self._entries.items()
                if file_id not in self._streamed
            ]
            if len(unwritten) != 1:
                return None
            file_id, title_ = unwritten[0]
            return {"file_id": file_id, "title": title_}

    def bind(
        self,
        ticket: str,
        file_id: str,
        title: str,
        version: str | None = None,
        replacement: dict[str, Any] | None = None,
        *,
        file_type: str | None = None,
    ) -> None:
        """占坑成功且文件已落库：把票据换成真实 file_id，并记下建好时的版本。

        replacement：write_chapter 整章替换已有章节时的定位结果（这时 version 为 None）。
        """
        effective_type = file_type
        if not effective_type and isinstance(replacement, dict):
            raw_type = replacement.get("file_type")
            if isinstance(raw_type, str):
                effective_type = raw_type
        with self._lock:
            self._reservations.pop(ticket, None)
            if file_id:
                self._entries[file_id] = title
                self._created_in[file_id] = self._reply_seq
                self._set_version(file_id, version)
                self._set_file_type_locked(file_id, effective_type)
                self._repeat_absorptions.pop(file_id, None)
                if replacement is not None:
                    self._replacements[file_id] = dict(replacement)
                    self._replacement_saved[file_id] = False
                else:
                    self._replacements.pop(file_id, None)
                # 幂等复用命中同一文件时它重新等待正文，旧标记作废
                self._streamed.discard(file_id)

    def release(self, ticket: str) -> None:
        """占坑作废（创建失败 / 建出来的是文件夹）：只回滚票据。"""
        with self._lock:
            self._reservations.pop(ticket, None)


def _merge_spans(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并重叠或相邻的半开字符区间 [start, end)。"""
    cleaned = sorted((max(0, int(s)), max(0, int(e))) for s, e in spans if e > s)
    if not cleaned:
        return []
    merged: list[tuple[int, int]] = [cleaned[0]]
    for start, end in cleaned[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


_SHIFT_FALLBACK_MAX_MID_LINES = 500


def _common_prefix_suffix_len(a: str, b: str) -> tuple[int, int]:
    """在 O(N) 内求两段文本的最长公共前后缀长度（按块比较，避免逐字 Python 循环）。"""
    min_len = min(len(a), len(b))
    step = 256
    prefix = 0
    while prefix + step <= min_len and a[prefix : prefix + step] == b[prefix : prefix + step]:
        prefix += step
    while prefix < min_len and a[prefix] == b[prefix]:
        prefix += 1

    max_suffix = min_len - prefix
    suffix = 0
    la, lb = len(a), len(b)
    while (
        suffix + step <= max_suffix
        and a[la - suffix - step : la - suffix] == b[lb - suffix - step : lb - suffix]
    ):
        suffix += step
    while suffix < max_suffix and a[la - 1 - suffix] == b[lb - 1 - suffix]:
        suffix += 1
    return prefix, suffix


def shift_spans_for_hunks(
    spans: Sequence[tuple[int, int]],
    hunks: Sequence[tuple[int, int, int]],
    *,
    new_len: int | None = None,
) -> list[tuple[int, int]]:
    """按具体编辑块 ``(old_start, old_end, replacement_len)`` 平移/更新已读字符区间。

    每个 hunk 均在修改前正文坐标下给出且互不重叠。从右向左依次套用，确保：
    - 落在已读区间之前的改动只做坐标平移，绝不桥接中间的未读段落；
    - 落在已读区间之后的改动不影响该区间；
    - 完全落在已读区间内部的改动按该 hunk 伸缩；
    - 跨出已读区间边界（触及未读文本）的改动只保留未被覆盖的已读部分，绝不把未读文本并入已读区间；
    - ``new_len`` 裁剪仅在全部 hunk 应用完毕后执行一次，避免右向左处理时过早截断高坐标区间。
    """
    current = _merge_spans(list(spans))
    if not current or not hunks:
        if new_len is not None and current:
            return _merge_spans([(min(s, new_len), min(e, new_len)) for s, e in current if min(e, new_len) > min(s, new_len)])
        return current
    ordered_hunks = sorted(
        ((int(s), int(e), max(0, int(n))) for s, e, n in hunks),
        key=lambda item: (item[0], item[1]),
        reverse=True,
    )
    for h_start, h_end, repl_len in ordered_hunks:
        delta = repl_len - (h_end - h_start)
        updated: list[tuple[int, int]] = []
        for start, end in current:
            if h_start >= end:
                # 改动完全在已读区间之后（含在区间右端点插入）
                new_start, new_end = start, end
            elif h_end <= start:
                # 改动完全在已读区间之前（含在区间左端点插入）：仅平移坐标
                new_start, new_end = start + delta, end + delta
            elif start <= h_start and h_end <= end:
                # 改动完全落在已读区间内部：按该 hunk 伸缩
                new_start, new_end = start, end + delta
            elif h_start < start and h_end > end:
                # 改动跨越已读区间两侧并覆盖未读文本：保守丢弃，绝不向未读区域扩张
                continue
            elif h_start < start:
                # 改动从左侧未读区域跨入已读区间：只保留右侧未受影响的已读后缀
                new_start, new_end = h_start + repl_len, end + delta
            else:
                # 改动从已读区间内部跨出到右侧未读区域：只保留左侧未受影响的已读前缀
                new_start, new_end = start, h_start
            new_start = max(0, new_start)
            new_end = max(0, new_end)
            if new_end > new_start:
                updated.append((new_start, new_end))
        current = _merge_spans(updated)
        if not current:
            break
    if new_len is not None and current:
        clamped = [(min(s, new_len), min(e, new_len)) for s, e in current]
        current = _merge_spans([(s, e) for s, e in clamped if e > s])
    return current


def shift_spans_for_edit(
    old_content: str,
    new_content: str,
    spans: list[tuple[int, int]],
) -> list[tuple[int, int]]:
    """无显式 hunk 时的兜底区间投影（O(N) 前后缀裁剪 + 有界按行 diff，绝不做 O(N^2) 字符级 diff）。"""
    merged = _merge_spans(list(spans))
    if not merged:
        return []
    if old_content == new_content:
        return merged

    prefix, suffix = _common_prefix_suffix_len(old_content, new_content)
    old_change_end = len(old_content) - suffix
    new_change_end = len(new_content) - suffix
    delta = len(new_content) - len(old_content)

    preserved: list[tuple[int, int]] = []
    overlapping: list[tuple[int, int]] = []
    for start, end in merged:
        if end <= prefix:
            preserved.append((start, end))
        elif start >= old_change_end:
            shifted_s = max(0, min(start + delta, len(new_content)))
            shifted_e = max(0, min(end + delta, len(new_content)))
            if shifted_e > shifted_s:
                preserved.append((shifted_s, shifted_e))
        else:
            overlapping.append((start, end))

    if not overlapping:
        return _merge_spans(preserved)

    old_mid = old_content[prefix:old_change_end]
    new_mid = new_content[prefix:new_change_end]
    old_lines = old_mid.splitlines(keepends=True)
    new_lines = new_mid.splitlines(keepends=True)
    if len(old_lines) + len(new_lines) > _SHIFT_FALLBACK_MAX_MID_LINES:
        # 变更窗口行数过大时保守丢弃重叠区间，不做无界 diff，绝不桥接未读区间。
        return _merge_spans(preserved)

    old_offsets = [prefix]
    for line in old_lines:
        old_offsets.append(old_offsets[-1] + len(line))
    new_offsets = [0]
    for line in new_lines:
        new_offsets.append(new_offsets[-1] + len(line))

    matcher = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    hunks = [
        (old_offsets[i1], old_offsets[i2], new_offsets[j2] - new_offsets[j1])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    ]
    shifted_overlapping = shift_spans_for_hunks(overlapping, hunks, new_len=len(new_content))
    return _merge_spans([*preserved, *shifted_overlapping])


class _ToolUiPayloadStore:
    """请求级工具 UI 侧通道（跨子任务/线程共享）。

    edit_file 返回给 LLM 的 function_call_output 省略了仅供前端卡片/撤销使用的
    undo 锚点与精确匹配回显预览；这里按 tool_call_id 暂存这些 UI 字段，
    由 StreamAdapter 在发 SSE tool_result 与落库前合回，请求结束时随
    AgentRequestContext.clear() 清空。
    """

    __slots__ = ("_fallback", "_lock", "_payloads")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._payloads: dict[str, dict[str, Any]] = {}
        # 仅用于未传 tool_call_id 的测试替身/直接调用（真实 SDK 均带 tool_call_id）。
        self._fallback: dict[tuple[str, str], list[dict[str, Any]]] = {}

    def put(
        self,
        call_id: str | None,
        payload: dict[str, Any],
        *,
        fallback_key: tuple[str, str] | None = None,
    ) -> None:
        if not payload:
            return
        with self._lock:
            if call_id:
                self._payloads[call_id] = dict(payload)
            elif fallback_key is not None:
                self._fallback.setdefault(fallback_key, []).append(dict(payload))

    def pop(
        self,
        call_id: str | None,
        *,
        fallback_key: tuple[str, str] | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if call_id and call_id in self._payloads:
                return self._payloads.pop(call_id)
            if fallback_key is not None:
                bucket = self._fallback.get(fallback_key)
                if bucket:
                    item = bucket.pop(0)
                    if not bucket:
                        self._fallback.pop(fallback_key, None)
                    return item
            return None

    def clear(self) -> None:
        with self._lock:
            self._payloads.clear()
            self._fallback.clear()


class _ReadSnapshots:
    """本次请求里模型看到的文件正文（跨子任务/线程共享，原因同上）。

    三个来源，取最近的一次：本轮开始时以全文给出的（上下文 [全文]、工作集全文块）、本请求自己写下的、
    当前这个 agent 这一程读到的（query_files 按 id 读全文、陈旧写入错误里完整列出的新改动）。
    读到的正文要等下一次模型调用才算数（promote_pending_reads，由 run hooks 的 on_llm_start 调用）：
    同一条回复里排在读取后面的修改是模型在看到结果之前写的。
    换下一个 agent 时清掉「读到的」：工具结果不跨 agent 回放，下一个 agent 没看到那些新正文。
    edit_file 在锁内重读后与它比较：不一致说明作者（或别的写入）在模型看过之后又改了，拒绝这次修改，
    不让基于旧正文的修改把新改动覆盖掉。

    「看过」（seen）比「有快照」窄：只用 edit_file 往一个没读过的文件里追加，模型知道自己写了什么，
    却没看到文件里原有的字；这时仍有快照做陈旧检查，但 old / anchor 只按逐字匹配。
    范围读取（query_files 的 section / tail_chars / start_char）同时记下完整正文快照与模型实际
    看过的字符区间（_range_spans）：陈旧检查仍按全文严格比对，edit_file 在已读区间内允许模糊/近似
    匹配，未读区间仍只按逐字匹配。
    """

    __slots__ = (
        "_full_writes",
        "_lock",
        "_pending",
        "_pending_range",
        "_pending_range_spans",
        "_range_spans",
        "_seq",
        "_sources",
    )

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._sources: dict[str, dict[str, tuple[int, str]]] = {
            "initial": {
                str(file_id): (0, content or "") for file_id, content in (initial or {}).items() if file_id
            },
            "written": {},
            "read": {},
            # 按范围读取（#274）：记下读库时的整份正文，用于严格的陈旧检查；
            # 实际看过的字符区间另记在 _range_spans，区间内允许模糊/近似匹配。
            "range": {},
        }
        self._pending: dict[str, tuple[int, str]] = {}
        self._pending_range: dict[str, tuple[int, str]] = {}
        self._pending_range_spans: dict[str, tuple[int, str, list[tuple[int, int]]]] = {}
        self._range_spans: dict[str, tuple[str, list[tuple[int, int]]]] = {}
        self._full_writes: set[str] = set()

    def _latest_locked(self, file_id: str) -> str | None:
        found = [source[file_id] for source in self._sources.values() if file_id in source]
        return max(found)[1] if found else None

    def ticket(self) -> int:
        """读库之前先取的序号：读到的正文按读库的时刻排序，不按记下来的时刻。"""
        with self._lock:
            self._seq += 1
            return self._seq

    def record(
        self,
        file_id: str,
        content: str | None,
        *,
        kind: str,
        full: bool = False,
        seq: int | None = None,
        span: tuple[int, int] | None = None,
        seen_spans: Sequence[tuple[int, int]] | None = None,
    ) -> None:
        if not file_id or kind not in ("written", "read", "range"):
            return
        normalized = content or ""
        with self._lock:
            self._seq += 1
            if kind in ("read", "range"):
                pending = self._pending if kind == "read" else self._pending_range
                order = seq if seq is not None else self._seq
                previous = pending.get(file_id)
                if previous is None or previous[0] <= order:
                    pending[file_id] = (order, normalized)
                if kind == "range" and span is not None and span[1] > span[0]:
                    prev_span = self._pending_range_spans.get(file_id)
                    if prev_span is None or prev_span[0] < order and prev_span[1] != normalized:
                        self._pending_range_spans[file_id] = (order, normalized, _merge_spans([span]))
                    elif prev_span[1] == normalized:
                        self._pending_range_spans[file_id] = (
                            max(prev_span[0], order),
                            normalized,
                            _merge_spans([*prev_span[2], span]),
                        )
                return
            previous_content = self._latest_locked(file_id)
            self._sources["written"][file_id] = (self._seq, normalized)
            if full or self._is_fully_seen_locked(file_id):
                if full:
                    self._full_writes.add(file_id)
                self._range_spans.pop(file_id, None)
            elif seen_spans is not None:
                merged = _merge_spans(list(seen_spans))
                if merged:
                    self._range_spans[file_id] = (normalized, merged)
                else:
                    self._range_spans.pop(file_id, None)
            elif file_id in self._range_spans and previous_content is not None:
                snap_content, spans = self._range_spans[file_id]
                if snap_content == previous_content:
                    shifted = shift_spans_for_edit(previous_content, normalized, spans)
                    if shifted:
                        self._range_spans[file_id] = (normalized, shifted)
                    else:
                        self._range_spans.pop(file_id, None)

    def written_state(self, file_id: str) -> tuple[tuple[int, str] | None, bool, tuple[str, list[tuple[int, int]]] | None]:
        with self._lock:
            span_entry = self._range_spans.get(file_id)
            saved_span = (span_entry[0], list(span_entry[1])) if span_entry is not None else None
            return self._sources["written"].get(file_id), file_id in self._full_writes, saved_span

    def restore_written(self, file_id: str, state: Any) -> None:
        if not isinstance(state, tuple) or len(state) not in (2, 3):
            return
        entry = state[0]
        full = state[1]
        saved_span = state[2] if len(state) == 3 else None
        with self._lock:
            if entry is None:
                self._sources["written"].pop(file_id, None)
            else:
                self._sources["written"][file_id] = entry
            if full:
                self._full_writes.add(file_id)
            else:
                self._full_writes.discard(file_id)
            if len(state) == 3:
                if saved_span is None:
                    self._range_spans.pop(file_id, None)
                else:
                    self._range_spans[file_id] = (saved_span[0], list(saved_span[1]))

    def discard_pending_read(self, file_id: str) -> None:
        with self._lock:
            self._pending.pop(file_id, None)
            self._pending_range.pop(file_id, None)
            self._pending_range_spans.pop(file_id, None)

    def promote_pending_reads(self) -> None:
        with self._lock:
            for file_id in self._pending:
                self._range_spans.pop(file_id, None)
            self._sources["read"].update(self._pending)
            self._pending.clear()
            self._sources["range"].update(self._pending_range)
            self._pending_range.clear()
            for file_id, (_order, content, spans) in self._pending_range_spans.items():
                if self._is_fully_seen_locked(file_id):
                    continue
                existing = self._range_spans.get(file_id)
                if existing is not None and existing[0] == content:
                    self._range_spans[file_id] = (content, _merge_spans([*existing[1], *spans]))
                else:
                    self._range_spans[file_id] = (content, _merge_spans(spans))
            self._pending_range_spans.clear()

    def begin_agent_run(self) -> None:
        with self._lock:
            self._sources["read"].clear()
            self._pending.clear()
            self._sources["range"].clear()
            self._pending_range.clear()
            self._range_spans.clear()
            self._pending_range_spans.clear()

    def drop_initial(self, file_ids: Any) -> None:
        """移除指定的初始快照（例如轮间压缩完成后撤销上一轮会话文件结转）。"""
        ids = {str(fid) for fid in (file_ids or ()) if fid}
        if not ids:
            return
        with self._lock:
            for fid in ids:
                self._sources["initial"].pop(fid, None)

    def get(self, file_id: str) -> str | None:
        with self._lock:
            return self._latest_locked(file_id)

    def _is_fully_seen_locked(self, file_id: str) -> bool:
        return (
            file_id in self._sources["initial"]
            or file_id in self._sources["read"]
            or file_id in self._full_writes
        )

    def seen(self, file_id: str) -> bool:
        with self._lock:
            return self._is_fully_seen_locked(file_id)

    def seen_spans(self, file_id: str) -> list[tuple[int, int]]:
        """返回模型通过范围读取看过的字符区间（仅当区间对应的正文仍是最新快照时有效）。"""
        with self._lock:
            entry = self._range_spans.get(file_id)
            if entry is None:
                return []
            snap_content, spans = entry
            latest = self._latest_locked(file_id)
            if latest is None or snap_content != latest:
                return []
            return list(spans)

    def known_file_ids(self, *, limit: int = 5) -> list[str]:
        """Return known file IDs in this request, prioritizing most recent reads/writes then initial focus files."""
        with self._lock:
            recent: list[tuple[int, str]] = []
            for kind in ("written", "read", "range"):
                for file_id, (seq, _content) in self._sources[kind].items():
                    if file_id:
                        recent.append((seq, file_id))
            recent.sort(key=lambda item: item[0], reverse=True)
            ordered: list[str] = []
            seen: set[str] = set()
            for _seq, file_id in recent:
                if file_id not in seen:
                    seen.add(file_id)
                    ordered.append(file_id)
                    if len(ordered) >= limit:
                        return ordered
            for file_id in self._sources["initial"]:
                if file_id and file_id not in seen:
                    seen.add(file_id)
                    ordered.append(file_id)
                    if len(ordered) >= limit:
                        return ordered
            return ordered


@dataclasses.dataclass
class AgentRequestContext:
    """Strong-typed, request-scoped execution context for the writing agent.

    Instantiated once in ``AgentService.process_stream`` and threaded natively
    through OpenAI Agents SDK ``RunContextWrapper[AgentRequestContext]`` (for
    ``FunctionTool.on_invoke_tool``, ``RunHooks``, ``call_model_input_filter``,
    ``tool_use_behavior``, and ``tool_error_formatter``) as well as explicitly
    into ``StreamAdapter``, ``loop/engine.py``, ``runner.py``, and MCP tool
    functions.

    Session lifecycle:
      - When ``create_session_func`` is configured (production path and
        concurrent worker-thread tests), :meth:`session_scope` ALWAYS opens a
        fresh short-lived SQLAlchemy ``Session`` per tool call / unit of work
        and closes it deterministically on exit — eliminating cross-thread
        Session sharing across ``asyncio.to_thread``.
      - Otherwise, when a caller-owned ``session`` is injected (unit tests),
        :meth:`session_scope` reuses it without closing it.
    """

    project_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    create_session_func: Callable[[], Session] | None = None
    session: Session | None = None
    current_agent: str | None = None
    notes_append_allowed: bool = False
    author_message: str | None = None
    author_steering: list[str] | None = None
    ui_action: str | None = None
    inflight_tool_writes: list[tuple[str, Any]] | None = None
    pending_empty_file_state: _PendingEmptyFileState = dataclasses.field(
        default_factory=_PendingEmptyFileState
    )
    skill_content_budget: SkillContentBudget = dataclasses.field(
        default_factory=SkillContentBudget
    )
    read_snapshots: _ReadSnapshots | None = None
    tool_ui_payloads: _ToolUiPayloadStore = dataclasses.field(
        default_factory=_ToolUiPayloadStore
    )
    _batch_replace_processed_lock: threading.Lock = dataclasses.field(
        default_factory=threading.Lock, repr=False
    )
    _batch_replace_processed: dict[tuple[str, str], set[str]] = dataclasses.field(
        default_factory=dict, repr=False
    )
    skill_tokens_used: dataclasses.InitVar[int] = 0

    def __post_init__(self, skill_tokens_used: int) -> None:
        if skill_tokens_used and self.skill_content_budget._used == 0:
            self.skill_content_budget = SkillContentBudget(used=skill_tokens_used)
        if isinstance(self.read_snapshots, dict):
            self.read_snapshots = _ReadSnapshots(self.read_snapshots)

    @property
    def is_loop_agent(self) -> bool:
        from agent.tools.registry import LOOP_AGENT_TYPE

        return self.current_agent == LOOP_AGENT_TYPE

    def author_wants_no_body(self) -> bool:
        """create_file 的 empty 是否生效（单循环 agent 由模型按提示判断，不从原话推断）。"""
        return self.is_loop_agent

    # ---- Session & executor lifecycle --------------------------------------
    @contextlib.contextmanager
    def session_scope(self) -> Iterator[Session]:
        """Yield a short-lived DB Session for one tool call or unit of work.

        Production sets ``create_session_func=create_session`` so every tool
        invocation (including concurrent ``asyncio.to_thread`` workers) opens
        and closes its own isolated ``Session``. Unit tests without a factory
        inject ``session=db_session``, which is yielded without closing.
        """
        if self.create_session_func is not None:
            session = self.create_session_func()
            try:
                yield session
            except Exception:
                if hasattr(session, "rollback"):
                    try:
                        session.rollback()
                    except Exception as exc:
                        logger.debug(f"Error rolling back short-lived session: {exc}")
                raise
            finally:
                try:
                    session.close()
                except Exception as exc:
                    logger.debug(f"Error closing short-lived session: {exc}")
            return

        if self.session is not None:
            try:
                yield self.session
            except Exception:
                if hasattr(self.session, "rollback"):
                    self.session.rollback()
                raise
            return

        raise RuntimeError("No session available in AgentRequestContext")

    def executor(self, session: Session) -> FileToolExecutor:
        """Build a ``FileToolExecutor`` bound to ``session`` and this request context."""
        return FileToolExecutor(session, self.user_id, request_context=self)

    @contextlib.contextmanager
    def executor_scope(self) -> Iterator[FileToolExecutor]:
        """Yield a ``FileToolExecutor`` backed by a short-lived :meth:`session_scope`."""
        with self.session_scope() as session:
            yield self.executor(session)

    @contextlib.contextmanager
    def bind_cost_budget_user(self) -> Iterator[None]:
        """Bind ``self.user_id`` for ambient cost-budget accounting during the request."""
        set_context_user_id(self.user_id)
        try:
            yield
        finally:
            set_context_user_id(None)

    def clear(self) -> None:
        """Clear shared mutable request containers at request end."""
        self.pending_empty_file_state.clear_all()
        self.tool_ui_payloads.clear()
        with self._batch_replace_processed_lock:
            self._batch_replace_processed.clear()
        _current_tool_call_id_var.set(None)

    # ---- Batch replace processed-file tracking -----------------------------
    def record_batch_replace_processed(
        self, old_text: str, new_text: str, file_ids: Sequence[str] | set[str]
    ) -> None:
        """Record file IDs already committed for ``(old_text, new_text)`` in this request."""
        cleaned = {str(fid) for fid in file_ids if fid}
        if not cleaned or not old_text:
            return
        key = (old_text, new_text)
        with self._batch_replace_processed_lock:
            bucket = self._batch_replace_processed.get(key)
            if bucket is None:
                self._batch_replace_processed[key] = cleaned
            else:
                bucket.update(cleaned)

    def batch_replace_processed_ids(self, old_text: str, new_text: str) -> frozenset[str]:
        """Return file IDs already modified for ``(old_text, new_text)`` in this request."""
        with self._batch_replace_processed_lock:
            bucket = self._batch_replace_processed.get((old_text, new_text))
            return frozenset(bucket) if bucket else frozenset()

    def invalidate_batch_replace_processed(self, file_id: str) -> None:
        """Drop ``file_id`` from tracked batch-replace sets after a subsequent file write."""
        if not file_id:
            return
        with self._batch_replace_processed_lock:
            for bucket in self._batch_replace_processed.values():
                bucket.discard(file_id)

    # ---- Read snapshots ----------------------------------------------------
    def tracks_reads(self) -> bool:
        """当前是应用内对话（有快照容器）：读文件时要记快照、附作者手改。"""
        return isinstance(self.read_snapshots, _ReadSnapshots)

    def read_snapshot(self, file_id: str) -> str | None:
        """模型在本请求里最后看到（或自己写下）的这个文件的正文；没有时为 None。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            return self.read_snapshots.get(str(file_id or ""))
        return None

    def has_seen_file(self, file_id: str) -> bool:
        """模型在本请求里看过这个文件的全文（上下文全文、按 id 读到、或整份写下的）。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            return self.read_snapshots.seen(str(file_id or ""))
        return False

    def seen_file_spans(self, file_id: str) -> list[tuple[int, int]]:
        """模型通过范围读取看到的字符区间列表；未范围读取或快照已变更时返回空列表。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            return self.read_snapshots.seen_spans(str(file_id or ""))
        return []

    def known_file_ids(self, *, limit: int = 5) -> list[str]:
        """本请求已知的文件 ID（优先最近读写的文件，其次本轮上下文/焦点文件）。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            return self.read_snapshots.known_file_ids(limit=limit)
        return []

    def discard_pending_read(self, file_id: str) -> None:
        """刚读到的正文没有完整交给模型（结果被截断）：不算看过。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            self.read_snapshots.discard_pending_read(str(file_id or ""))

    def promote_pending_reads(self) -> None:
        """一次新的模型调用开始：上一条回复里读到的正文，模型现在看到了。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            self.read_snapshots.promote_pending_reads()

    def read_ticket(self) -> int | None:
        """读库之前调用：返回这次读取的序号，记快照时传回 record_read_snapshot。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            return self.read_snapshots.ticket()
        return None

    def record_read_snapshot(
        self, file_id: str, content: str | None, *, ticket: int | None = None
    ) -> None:
        """记下当前 agent 这一程读到的正文（下一次模型调用起算数）。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            self.read_snapshots.record(str(file_id or ""), content or "", kind="read", seq=ticket)

    def record_range_read_snapshot(
        self,
        file_id: str,
        full_content: str | None,
        *,
        span: tuple[int, int] | None = None,
        ticket: int | None = None,
    ) -> None:
        """按范围读取时记下读库那一刻的整份正文与已读字符区间。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            self.read_snapshots.record(
                str(file_id or ""), full_content or "", kind="range", seq=ticket, span=span
            )

    def record_written_snapshot(
        self,
        file_id: str,
        content: str | None,
        *,
        full: bool = False,
        seen_spans: Sequence[tuple[int, int]] | None = None,
    ) -> None:
        """记下本请求自己写下的正文（立即生效）；full=整份写下，模型知道全文。"""
        fid = str(file_id or "")
        if fid:
            self.invalidate_batch_replace_processed(fid)
        if isinstance(self.read_snapshots, _ReadSnapshots):
            self.read_snapshots.record(
                fid, content or "", kind="written", full=full, seen_spans=seen_spans
            )

    def written_snapshot_state(self, file_id: str) -> Any:
        """写入前的快照状态，提交失败时用 restore_written_snapshot 还原。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            return self.read_snapshots.written_state(str(file_id or ""))
        return None

    def restore_written_snapshot(self, file_id: str, state: Any) -> None:
        if isinstance(self.read_snapshots, _ReadSnapshots):
            self.read_snapshots.restore_written(str(file_id or ""), state)

    def begin_agent_run(self) -> None:
        """新一程开始：上一程读到的正文模型看不到，不再算「模型看到的」。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            self.read_snapshots.begin_agent_run()

    def drop_initial_read_snapshots(self, file_ids: Any) -> None:
        """移除指定的初始快照（轮间压缩成功后撤销跨轮会话结转）。"""
        if isinstance(self.read_snapshots, _ReadSnapshots):
            self.read_snapshots.drop_initial(file_ids)

    # ---- Tool UI payload side-channel --------------------------------------
    @contextlib.contextmanager
    def tool_call_scope(self, call_id: str | None) -> Iterator[None]:
        """绑定当前正在执行的工具调用 id（按协程/工作线程隔离，供 UI 侧通道写入）。"""
        normalized = call_id.strip() if isinstance(call_id, str) and call_id.strip() else None
        token = _current_tool_call_id_var.set(normalized)
        try:
            yield
        finally:
            _current_tool_call_id_var.reset(token)

    def current_tool_call_id(self) -> str | None:
        return _current_tool_call_id_var.get()

    def record_tool_ui_payload(
        self,
        call_id: str | None,
        payload: dict[str, Any],
        *,
        fallback_key: tuple[str, str] | None = None,
    ) -> None:
        """按 tool_call_id 暂存仅供 SSE/UI 卡片与撤销使用的字段（不进入 LLM function_call_output）。"""
        if not payload or (not call_id and fallback_key is None):
            return
        self.tool_ui_payloads.put(
            str(call_id) if call_id else None, payload, fallback_key=fallback_key
        )

    def pop_tool_ui_payload(
        self,
        call_id: str | None,
        *,
        fallback_key: tuple[str, str] | None = None,
    ) -> dict[str, Any] | None:
        """取出并删除指定 tool_call_id 的 UI 侧通道字段（供 StreamAdapter 合并）。"""
        if not call_id and fallback_key is None:
            return None
        return self.tool_ui_payloads.pop(
            str(call_id) if call_id else None, fallback_key=fallback_key
        )

    # ---- Pending empty-file / chapter-replacement state --------------------
    def set_pending_empty_file(
        self,
        file_id: str,
        title: str,
        version: str | None = None,
        *,
        file_type: str | None = None,
    ) -> None:
        self.pending_empty_file_state.add(file_id, title, version, file_type=file_type)

    def set_pending_empty_file_type(self, file_id: str, file_type: str | None) -> None:
        self.pending_empty_file_state.set_file_type(file_id, file_type)

    def consume_pending_writer_repeat(self, file_id: str, *, max_repeats: int = 1) -> bool:
        return self.pending_empty_file_state.consume_repeat_absorption(
            file_id, max_repeats=max_repeats
        )

    def consume_overdue_non_conflicting(self, file_id: str, *, max_uses: int = 2) -> bool:
        return self.pending_empty_file_state.consume_overdue_non_conflicting(
            file_id, max_uses=max_uses
        )

    def clear_pending_empty_file(self, file_id: str | None = None) -> None:
        self.pending_empty_file_state.discard(file_id)

    def has_pending_empty_file(self) -> bool:
        return self.pending_empty_file_state.has_pending()

    def get_pending_empty_file(self) -> dict[str, str] | None:
        return self.pending_empty_file_state.latest()

    def get_pending_empty_files(self) -> list[dict[str, str]]:
        return self.pending_empty_file_state.snapshot()

    def pending_empty_file_for(self, file_id: str) -> dict[str, Any] | None:
        return self.pending_empty_file_state.entry_for(str(file_id or ""))

    def begin_model_reply(self) -> None:
        self.pending_empty_file_state.begin_reply()

    def overdue_file_bodies(self) -> list[dict[str, Any]]:
        return self.pending_empty_file_state.overdue_unwritten_files()

    def mark_pending_body_streamed(self, file_id: str) -> None:
        self.pending_empty_file_state.mark_body_streamed(file_id)

    def offer_reply_prebody(self) -> bool:
        return self.pending_empty_file_state.offer_reply_prebody()

    def take_reply_prebody(self) -> bool:
        return self.pending_empty_file_state.take_reply_prebody()

    def mark_latest_pending_body_streamed(self) -> None:
        self.pending_empty_file_state.mark_latest_body_streamed()

    def pending_empty_file_version(self, file_id: str) -> str | None:
        return self.pending_empty_file_state.version(file_id)

    def rearm_pending_file_body(self, file_id: str) -> bool:
        return self.pending_empty_file_state.rearm(file_id)

    def try_reserve_pending_empty_file(
        self, ticket: str, title: str
    ) -> tuple[bool, str | None]:
        return self.pending_empty_file_state.try_reserve(ticket, title)

    def bind_pending_empty_file(
        self,
        ticket: str,
        file_id: str,
        title: str,
        version: str | None = None,
        replacement: dict[str, Any] | None = None,
        *,
        file_type: str | None = None,
    ) -> None:
        self.pending_empty_file_state.bind(
            ticket, file_id, title, version, replacement, file_type=file_type
        )

    def release_pending_empty_file(self, ticket: str) -> None:
        self.pending_empty_file_state.release(ticket)

    def close_pending_body_files(self) -> list[dict[str, str]]:
        return self.pending_empty_file_state.close()

    def pending_body_files_closed(self) -> bool:
        return self.pending_empty_file_state.is_closed()

    def pending_chapter_replacement(self, file_id: str) -> dict[str, Any] | None:
        return self.pending_empty_file_state.replacement(file_id)

    def mark_chapter_replacement_saved(self, file_id: str) -> None:
        self.pending_empty_file_state.mark_replacement_saved(file_id)

    def mark_chapter_body_saved(
        self, file_id: str, title: str = "", file_type: str = ""
    ) -> None:
        if file_id:
            self.pending_empty_file_state.mark_chapter_body_saved(file_id, title, file_type)

    def mark_cut_body_saved(self, file_id: str, title: str = "") -> None:
        if file_id:
            self.pending_empty_file_state.mark_cut_body_saved(file_id, title)

    def take_cut_bodies(self) -> dict[str, str]:
        return self.pending_empty_file_state.take_cut_bodies()

    def saved_bodies_this_request(self) -> dict[str, str]:
        return self.pending_empty_file_state.saved_body_titles()

    def saved_content_body_ids(self) -> list[str]:
        return self.pending_empty_file_state.saved_content_body_ids()

    def chapter_body_saved_this_request(self, file_id: str) -> bool:
        return self.pending_empty_file_state.chapter_body_saved(file_id)

    def unsaved_chapter_replacement_ids(self) -> set[str]:
        return self.pending_empty_file_state.unsaved_replacement_ids()


def unwrap_request_context(source: Any) -> AgentRequestContext | None:
    """Extract an ``AgentRequestContext`` from itself or an SDK wrapper.

    Handles:
      - ``AgentRequestContext`` directly
      - ``RunContextWrapper[AgentRequestContext]`` / SDK ``ToolContext`` (``.context``)
      - ``CallModelData[AgentRequestContext]`` (``.context``)
      - ``ToolErrorFormatterArgs[AgentRequestContext]`` (``.run_context.context``)
    """
    if isinstance(source, AgentRequestContext):
        return source
    if source is None:
        return None
    inner = getattr(source, "context", None)
    if isinstance(inner, AgentRequestContext):
        return inner
    run_ctx = getattr(source, "run_context", None)
    if run_ctx is not None:
        inner = getattr(run_ctx, "context", None)
        if isinstance(inner, AgentRequestContext):
            return inner
    return None
