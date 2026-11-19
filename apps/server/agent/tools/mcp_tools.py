"""
Tool execution functions for zenstory Agent.

Defines tool functions that can be called by the writing-agent adapter.
"""

import asyncio
import contextlib
import contextvars
import copy
import json
import os
import re
import threading
from collections.abc import Callable, Iterator, Sequence
from typing import Any
from uuid import uuid4

from sqlalchemy import func
from sqlmodel import Session, and_, select

from agent.constants import CONTENT_FILE_TYPES, coerce_bool
from agent.skills.content_budget import SkillContentBudget
from agent.tools.file_ops import FileToolExecutor
from agent.tools.file_ops.content_range import ContentRangeError, apply_content_range, wants_range
from agent.tools.file_ops.edit import (
    EDIT_ERROR_FILE_BUSY,
    EDIT_ERROR_FILE_NOT_FOUND,
    EDIT_ERROR_GENERIC,
    EDIT_ERROR_INVALID_EDIT,
    EDIT_ERROR_PERMISSION_DENIED,
    EditFileError,
    FileWriteBusyError,
    StaleContentError,
    edit_error_user_message,
)
from agent.tools.file_ops.serialization import (
    QUERY_FILES_DEFAULT_LIMIT,
    QUERY_FILES_FULL_MODE_DEFAULT_LIMIT,
    QUERY_FILES_RESPONSE_MODE_FULL,
    serialize_file,
)
from agent.tools.permissions import ForbiddenError, NotFoundError
from agent.tools.write_errors import FileWriteRecoveryError
from config.project_notes import merge_project_notes
from utils.logger import get_logger, log_with_context
from utils.title_sequence import extract_chapter_like_sequence_number, parse_chinese_number

logger = get_logger(__name__)

DEFAULT_TOOL_RESULT_MAX_CHARS = 200_000
MIN_TOOL_RESULT_MAX_CHARS = 512

PROJECT_STATUS_FIELD_ALIASES: dict[str, str] = {
    "currentPhase": "current_phase",
    "writingStyle": "writing_style",
    "projectSummary": "summary",
}


def _run_sync_tool_with_owned_session_cleanup(
    sync_func: Callable[[dict[str, Any]], dict[str, Any]],
    args: dict[str, Any],
    *extra_args: Any,
) -> dict[str, Any]:
    """
    Run a sync tool helper and deterministically close any ToolContext-owned session.

    When PostgreSQL-mode tool calls are offloaded with ``asyncio.to_thread()``,
    ``ToolContext.get_session()`` may lazily create a session inside the worker
    thread. That worker context is separate from the main request context, so the
    normal ``ToolContext.clear_context()`` in the caller does not guarantee the
    thread-owned session is closed immediately. Close it here to avoid relying on
    GC timing for connection release.

    进线程后必须先把 ``_owned_session_var`` 清空：``asyncio.to_thread`` 拷贝的是
    调用方的 contextvars 快照，事件循环侧此前懒建的 Session 会被原样带进工作
    线程。若不清空，(1) 同一个 Session 会被事件循环线程与工作线程交替使用
    （SQLAlchemy Session 非线程安全），(2) 下面 finally 里的 ``_cleanup_owned_session``
    会把这个**属于事件循环上下文**的 Session 直接 close 掉，而事件循环那边的
    ContextVar 仍指向它（线程里的 set 对调用方不可见），后续调用拿到的是一个
    已关闭的 Session。清空后，本线程按需自建、用完即关，生命周期完全自洽。

    同理，``context["session"]``（调用方显式塞进来的**请求级** Session）也不能
    在工作线程里复用：``get_session()`` 的第一分支就是它，绕不过 _owned_session_var
    与 SESSION_ISOLATION_KEY。SDK 同一 turn 里的多个 tool_call 会并发成多个
    asyncio Task，各自 ``to_thread`` 出一个工作线程，一起在同一个 Session 上
    flush/commit/refresh。因此这里把上下文浅拷贝一份、抹掉 session 并打上隔离
    标记，让本线程按需自建。只有在拿得到 ``create_session_func`` 时才这么做——
    否则自建无从谈起，只能沿用调用方给的 session（测试里的典型用法）。
    """
    _owned_session_var.set(None)

    context = _tool_context_var.get()
    if (
        isinstance(context, dict)
        and context.get("session") is not None
        and context.get("create_session_func")
    ):
        isolated_context = dict(context)
        isolated_context["session"] = None
        isolated_context[SESSION_ISOLATION_KEY] = True
        # 这是工作线程私有的 contextvars 快照，set 不会影响事件循环侧。
        _tool_context_var.set(isolated_context)

    try:
        return sync_func(args, *extra_args)
    finally:
        ToolContext._cleanup_owned_session()


async def _offload_write_tool(
    tool_name: str, sync_func: Callable[[dict[str, Any]], dict[str, Any]], args: dict[str, Any]
) -> dict[str, Any]:
    """写工具在线程里执行；停止 / 断线 / 时限取消了等待时线程照常跑完，结果留给收尾结算。

    线程的 future 先登记进请求级的在途列表（``ToolContext.inflight_tool_writes``，属于
    ``RunOutcome``，比 ``clear_context()`` 活得长），正常返回或出错时移除；被取消时留着，
    由 service 的截停收尾等它的真实结果（``RunOutcome.settle_inflight_tool_writes``）。
    """
    future = asyncio.ensure_future(
        asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, sync_func, args)
    )
    # 取消之后没人再取结果时，先把异常取走（不留 "exception was never retrieved"）。
    future.add_done_callback(lambda done: done.cancelled() or done.exception())
    registry = ToolContext.inflight_tool_writes()
    entry = (tool_name, future)
    if registry is not None:
        registry.append(entry)
    try:
        result = await asyncio.shield(future)
    except asyncio.CancelledError:
        raise
    except BaseException:
        _discard_inflight(registry, entry)
        raise
    _discard_inflight(registry, entry)
    return result


def _discard_inflight(registry: list[tuple[str, Any]] | None, entry: tuple[str, Any]) -> None:
    if registry is not None and entry in registry:
        registry.remove(entry)


def _get_tool_result_max_chars() -> int:
    """Read tool-result max chars limit from env with safe fallback."""
    raw = os.getenv("AGENT_TOOL_RESULT_MAX_CHARS")
    if raw is None:
        return DEFAULT_TOOL_RESULT_MAX_CHARS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_TOOL_RESULT_MAX_CHARS
    return value if value >= MIN_TOOL_RESULT_MAX_CHARS else DEFAULT_TOOL_RESULT_MAX_CHARS


TOOL_RESULT_MAX_CHARS = _get_tool_result_max_chars()

# 请求级别的上下文变量
# Note: Using None as default to avoid mutable default argument (B039)
_tool_context_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    'tool_context', default=None
)
_owned_session_var: contextvars.ContextVar[Session | None] = contextvars.ContextVar(
    'owned_session', default=None
)

# 工作线程的 session 隔离标记（见 ToolContext.get_session 与 _run_sync_tool_in_thread）。
# 放在工具上下文 dict 里而不是 ContextVar，因为它必须随上下文的浅拷贝一起进到
# asyncio.to_thread 的线程副本。
SESSION_ISOLATION_KEY = "isolated_session"


class _PendingEmptyFileState:
    """待写入空文件标记的可变持有者，随请求上下文 dict 在所有子任务间共享。

    IMPORTANT — 该标记不能存成独立 ContextVar：openai-agents SDK 会为 run loop
    以及每一次 function tool 调用各包一层 asyncio.create_task，而 create_task 只
    拷贝当前 contextvars 快照，子任务里对 ContextVar 的重绑定对父上下文与兄弟
    任务均不可见（同理 asyncio.to_thread / asyncio.gather 的子任务）。因此这里
    只能原地修改这个持有者对象——set_context 在请求主上下文把它放进
    _tool_context_var 持有的 dict 后，所有子任务的上下文副本（含
    set_current_agent 的浅拷贝）引用的都是同一个
    实例，原地读写天然跨任务可见。

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
        "_lock", "_entries", "_reservations", "_streamed", "_reply_seq", "_created_in", "_versions",
        "_prebody_reply", "_waiting_at_reply_start", "_replacements", "_closed", "_replacement_saved",
        "_chapter_bodies_saved", "_saved_body_types", "_steering_at_arm", "_cut_bodies_saved",
    )

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # file_id -> title，按插入顺序保存全部待写入空文件
        self._entries: dict[str, str] = {}
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
        # 文件定为待写时本请求已收到几条作者插话（ToolContext.author_steering_count）：
        # 之后又有插话，正文那次调用的输入里可能带着它，tool_choice 不再限为 none（见 steering_since_armed）。
        self._steering_at_arm: dict[str, int] = {}
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

    def entry_for(self, file_id: str) -> dict[str, str] | None:
        """file_id 当前在待写入集合里时返回 {file_id, title}，否则为 None。"""
        with self._lock:
            if file_id not in self._entries:
                return None
            return {"file_id": file_id, "title": self._entries[file_id]}

    # ---- 写入 --------------------------------------------------------------
    def begin_reply(self) -> None:
        """一次新的模型调用开始。"""
        with self._lock:
            self._reply_seq += 1
            self._waiting_at_reply_start = any(file_id not in self._streamed for file_id in self._entries)

    def overdue_unwritten_files(self) -> list[dict[str, str]]:
        """上一次（或更早的）回复建好、这次回复还没写出正文的文件：精确 {id, title}（按置位先后）。

        created_here：本请求新建、记下了建好时版本的文件（复用的已有剧集没有，可能已有正文）。
        """
        with self._lock:
            return [
                {"id": file_id, "title": title, "created_here": file_id in self._versions}
                for file_id, title in self._entries.items()
                if file_id not in self._streamed and self._created_in.get(file_id, self._reply_seq) < self._reply_seq
            ]

    def steering_since_armed(self, file_ids: list[str], steering_count: int) -> bool:
        """这些待写文件里有没有定为待写之后又收到作者插话的。"""
        with self._lock:
            return any(steering_count > self._steering_at_arm.get(file_id, steering_count) for file_id in file_ids)

    def add(self, file_id: str, title: str, version: str | None = None, steering_count: int = 0) -> None:
        with self._lock:
            self._created_in[file_id] = self._reply_seq
            self._steering_at_arm[file_id] = steering_count
            self._entries[file_id] = title
            self._set_version(file_id, version)
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
                self._streamed.clear()
                self._versions.clear()
                self._replacements.clear()
            else:
                self._entries.pop(file_id, None)
                self._streamed.discard(file_id)
                self._versions.pop(file_id, None)
                self._replacements.pop(file_id, None)

    def clear_all(self) -> None:
        with self._lock:
            self._entries.clear()
            self._reservations.clear()
            self._streamed.clear()
            self._versions.clear()
            self._replacements.clear()
            self._replacement_saved.clear()
            self._chapter_bodies_saved.clear()
            self._cut_bodies_saved.clear()
            self._prebody_reply = None
            self._waiting_at_reply_start = False

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
        from agent.constants import CONTENT_FILE_TYPES

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
        steering_count: int = 0,
    ) -> None:
        """占坑成功且文件已落库：把票据换成真实 file_id，并记下建好时的版本。

        replacement：write_chapter 整章替换已有章节时的定位结果（这时 version 为 None）。
        """
        with self._lock:
            self._reservations.pop(ticket, None)
            if file_id:
                self._entries[file_id] = title
                self._created_in[file_id] = self._reply_seq
                self._steering_at_arm[file_id] = steering_count
                self._set_version(file_id, version)
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
    """

    __slots__ = ("_full_writes", "_lock", "_pending", "_pending_range", "_seq", "_sources")

    def __init__(self, initial: dict[str, str] | None = None) -> None:
        self._lock = threading.Lock()
        self._seq = 0
        self._sources: dict[str, dict[str, tuple[int, str]]] = {
            "initial": {
                str(file_id): (0, content or "") for file_id, content in (initial or {}).items() if file_id
            },
            "written": {},
            "read": {},
            # 按范围读取（#274）：记下读库时的整份正文，只用于陈旧检查；模型只看到其中一段，
            # 不算「看过」全文，old / anchor 仍只按逐字匹配。
            "range": {},
        }
        self._pending: dict[str, tuple[int, str]] = {}
        self._pending_range: dict[str, tuple[int, str]] = {}
        self._full_writes: set[str] = set()

    def ticket(self) -> int:
        """读库之前先取的序号：读到的正文按读库的时刻排序，不按记下来的时刻。"""
        with self._lock:
            self._seq += 1
            return self._seq

    def record(
        self, file_id: str, content: str | None, *, kind: str, full: bool = False, seq: int | None = None
    ) -> None:
        if not file_id or kind not in ("written", "read", "range"):
            return
        with self._lock:
            self._seq += 1
            if kind in ("read", "range"):
                pending = self._pending if kind == "read" else self._pending_range
                order = seq if seq is not None else self._seq
                previous = pending.get(file_id)
                if previous is None or previous[0] <= order:
                    pending[file_id] = (order, content or "")
                return
            self._sources["written"][file_id] = (self._seq, content or "")
            if full:
                self._full_writes.add(file_id)

    def written_state(self, file_id: str) -> tuple[tuple[int, str] | None, bool]:
        with self._lock:
            return self._sources["written"].get(file_id), file_id in self._full_writes

    def restore_written(self, file_id: str, state: Any) -> None:
        if not isinstance(state, tuple) or len(state) != 2:
            return
        entry, full = state
        with self._lock:
            if entry is None:
                self._sources["written"].pop(file_id, None)
            else:
                self._sources["written"][file_id] = entry
            if full:
                self._full_writes.add(file_id)
            else:
                self._full_writes.discard(file_id)

    def discard_pending_read(self, file_id: str) -> None:
        with self._lock:
            self._pending.pop(file_id, None)

    def promote_pending_reads(self) -> None:
        with self._lock:
            self._sources["read"].update(self._pending)
            self._pending.clear()
            self._sources["range"].update(self._pending_range)
            self._pending_range.clear()

    def begin_agent_run(self) -> None:
        with self._lock:
            self._sources["read"].clear()
            self._pending.clear()
            self._sources["range"].clear()
            self._pending_range.clear()

    def get(self, file_id: str) -> str | None:
        with self._lock:
            found = [source[file_id] for source in self._sources.values() if file_id in source]
        return max(found)[1] if found else None

    def seen(self, file_id: str) -> bool:
        with self._lock:
            return (
                file_id in self._sources["initial"]
                or file_id in self._sources["read"]
                or file_id in self._full_writes
            )


class ToolContext:
    """
    Holds session and user_id for tool execution.

    Uses contextvars for request-level isolation in async environments.
    Each concurrent request gets its own isolated context.
    """

    @classmethod
    def set_context(
        cls,
        session: Session | None,
        user_id: str | None,
        project_id: str,
        session_id: str | None,
        create_session_func: Callable[[], Session] | None = None,
        current_agent: str | None = None,
        skill_tokens_used: int = 0,
        author_message: str | None = None,
        author_steering: list[str] | None = None,
        read_snapshots: dict[str, str] | None = None,
        inflight_tool_writes: list[tuple[str, Any]] | None = None,
    ) -> None:
        """Set the execution context for tools (request-scoped).

        Args:
            author_message: 作者这一轮的原话（不含选中文本、上下文等拼接内容）。
                由模型理解本轮意图与范围；程序只校验结构化目标、权限、版本和恢复记录。
            author_steering: 本轮运行中作者追加的消息（service 的 consumed_steering，
                同一个 list 对象，运行中会继续追加），只作为模型上下文。
            skill_tokens_used: 显式选择的技能注入 system prompt 时已占用的技能内容 token，
                从本次请求的技能内容预算里先扣掉。
            read_snapshots: 本轮开始时以全文给模型看的文件正文（file_id → 正文），edit_file 的
                陈旧检查以它为起点（_ReadSnapshots）；None 表示不跟踪（外部调用路径）。
            inflight_tool_writes: 请求级的在途写工具登记（``RunOutcome.inflight_tool_writes``，
                同一个 list 对象）；None 表示不登记（见 _offload_write_tool）。
        """
        cls._cleanup_owned_session()
        _tool_context_var.set({
            "session": session,
            "user_id": user_id,
            "project_id": project_id,
            "session_id": session_id,
            "create_session_func": create_session_func,
            "current_agent": current_agent,
            "pending_empty_file_state": _PendingEmptyFileState(),
            "skill_content_budget": SkillContentBudget(used=skill_tokens_used),
            "author_message": author_message,
            "author_steering": author_steering,
            "read_snapshots": _ReadSnapshots(read_snapshots) if read_snapshots is not None else None,
            "inflight_tool_writes": inflight_tool_writes,
            "direct_deletions": {"lock": threading.Lock(), "file_ids": set()},
        })
        _owned_session_var.set(None)

    @classmethod
    def inflight_tool_writes(cls) -> list[tuple[str, Any]] | None:
        """请求级的在途写工具登记（见 set_context）；没有时为 None。"""
        registry = cls._get_context().get("inflight_tool_writes")
        return registry if isinstance(registry, list) else None

    @classmethod
    def _get_context(cls) -> dict[str, Any]:
        """Get current request's context."""
        context = _tool_context_var.get()
        return context if context is not None else {}

    @classmethod
    def get_session(cls) -> Session:
        """Get session, creating one if needed."""
        context = cls._get_context()
        if context.get("session"):
            return context["session"]

        # 隔离标记（工作线程拿到的上下文副本上置位）：本任务**必须**
        # 自建 session，绝不能复用 contextvars 快照从父上下文继承来的
        # _owned_session_var。否则多个工作线程会共用同一个 SQLAlchemy Session
        # （非线程安全），且先结束的线程会在 finally 里把它 close 掉，另一个
        # 线程还在同一 Session 上跑事务。
        # 注意不能用「context 里 session 键为 None」当判据：正常请求路径
        # （service.py 的 _should_offload_session_work 分支）本来就写的是
        # session=None + create_session_func，那时必须允许复用自有 session。
        isolated = bool(context.get(SESSION_ISOLATION_KEY))

        owned = _owned_session_var.get()
        if owned is not None and not isolated:
            return owned

        create_func = context.get("create_session_func")
        if create_func:
            new_session = create_func()
            _owned_session_var.set(new_session)
            if isolated:
                # 本任务已经拥有自己的 session，隔离标记完成使命：就地摘掉，
                # 后续 get_session() 复用它而不是每次新建（否则会泄漏连接）。
                # context 是本任务专属的 dict 副本，原地改不影响兄弟任务。
                context.pop(SESSION_ISOLATION_KEY, None)
            return new_session

        raise RuntimeError("No session available in ToolContext")

    @classmethod
    @contextlib.contextmanager
    def short_lived_session(cls) -> Iterator[Session]:
        """借一个只在 with 块内有效的 session，用完即关、归还连接。

        供工作流在 agent 边界做的一次性查询（文件清单、空文件核验、空文件回滚）
        使用：它们跑在 asyncio.to_thread 的工作线程里，不能走 get_session() 懒建
        并写入 _owned_session_var——那会让一条连接以 idle in transaction 状态
        一直挂到整轮 SSE 结束（clear_context 才关闭）。上下文里显式放了共享
        session（SQLite 测试/非 offload 路径）时直接复用它，不由这里关闭。
        """
        context = cls._get_context()
        shared = context.get("session")
        if shared is not None:
            try:
                yield shared
            except Exception:
                if hasattr(shared, "rollback"):
                    shared.rollback()
                raise
            return

        create_func = context.get("create_session_func")
        if not create_func:
            raise RuntimeError("No session available in ToolContext")

        session = create_func()
        try:
            yield session
        finally:
            try:
                session.close()
            except Exception as e:
                logger.debug(f"Error closing short-lived session: {e}")

    @classmethod
    def get_session_id(cls) -> str | None:
        """Get current request session_id."""
        context = cls._get_context()
        session_id = context.get("session_id")
        return session_id if isinstance(session_id, str) and session_id else None

    @classmethod
    def get_user_id(cls) -> str | None:
        """Get current request user_id."""
        context = cls._get_context()
        user_id = context.get("user_id")
        return user_id if isinstance(user_id, str) and user_id else None

    @classmethod
    def get_project_id(cls) -> str | None:
        """Get current request project_id."""
        context = cls._get_context()
        project_id = context.get("project_id")
        return project_id if isinstance(project_id, str) and project_id else None

    @classmethod
    def get_author_messages(cls) -> list[str]:
        """作者本轮的原话：首条消息加运行中追加的消息（拿不到时为空）。"""
        context = cls._get_context()
        messages: list[str] = []
        first = context.get("author_message")
        if isinstance(first, str):
            messages.append(first)
        steering = context.get("author_steering")
        if isinstance(steering, list):
            messages.extend(item for item in list(steering) if isinstance(item, str))
        return messages

    @classmethod
    def author_wants_no_body(cls) -> bool:
        """create_file 的 empty 是否生效。

        单循环 agent 由模型按提示判断：它传 empty=true 就是它的判断（写入照旧受撤销 / 版本保护），
        不从原话推断。
        """
        from agent.tools.registry import LOOP_AGENT_TYPE

        return cls.get_current_agent() == LOOP_AGENT_TYPE

    @classmethod
    def tracks_reads(cls) -> bool:
        """当前是应用内对话（有快照容器）：读文件时要记快照、附作者手改。"""
        return isinstance(cls._get_context().get("read_snapshots"), _ReadSnapshots)

    @classmethod
    def read_snapshot(cls, file_id: str) -> str | None:
        """模型在本请求里最后看到（或自己写下）的这个文件的正文；都没有（或没有上下文）时为 None。"""
        holder = cls._get_context().get("read_snapshots")
        if isinstance(holder, _ReadSnapshots):
            return holder.get(str(file_id or ""))
        return None

    @classmethod
    def has_seen_file(cls, file_id: str) -> bool:
        """模型在本请求里看过这个文件的全文（上下文全文、按 id 读到、或整份写下的）。"""
        holder = cls._get_context().get("read_snapshots")
        if isinstance(holder, _ReadSnapshots):
            return holder.seen(str(file_id or ""))
        return False

    @classmethod
    def discard_pending_read(cls, file_id: str) -> None:
        """刚读到的正文没有完整交给模型（结果被截断）：不算看过。"""
        holder = cls._get_context().get("read_snapshots")
        if isinstance(holder, _ReadSnapshots):
            holder.discard_pending_read(str(file_id or ""))

    @classmethod
    def promote_pending_reads(cls) -> None:
        """一次新的模型调用开始：上一条回复里读到的正文，模型现在看到了。"""
        holder = cls._get_context().get("read_snapshots")
        if isinstance(holder, _ReadSnapshots):
            holder.promote_pending_reads()

    @classmethod
    def read_ticket(cls) -> int | None:
        """读库之前调用：返回这次读取的序号，记快照时传回 record_read_snapshot。"""
        holder = cls._get_context().get("read_snapshots")
        if isinstance(holder, _ReadSnapshots):
            return holder.ticket()
        return None

    @classmethod
    def record_read_snapshot(cls, file_id: str, content: str | None, *, ticket: int | None = None) -> None:
        """记下当前 agent 这一程读到的正文（下一次模型调用起算数）；没有请求上下文时什么也不做。

        ticket：读库前取的序号。读库之后、记下之前本请求又写了这个文件时，写下的正文更新，不被这次读取盖过。
        """
        holder = cls._get_context().get("read_snapshots")
        if isinstance(holder, _ReadSnapshots):
            holder.record(str(file_id or ""), content or "", kind="read", seq=ticket)

    @classmethod
    def record_range_read_snapshot(cls, file_id: str, full_content: str | None, *, ticket: int | None = None) -> None:
        """按范围读取时记下读库那一刻的整份正文：只做陈旧检查，不算模型看过全文。"""
        holder = cls._get_context().get("read_snapshots")
        if isinstance(holder, _ReadSnapshots):
            holder.record(str(file_id or ""), full_content or "", kind="range", seq=ticket)

    @classmethod
    def record_written_snapshot(cls, file_id: str, content: str | None, *, full: bool = False) -> None:
        """记下本请求自己写下的正文（立即生效）；full=整份写下（创建 / 整份覆盖），模型知道全文。"""
        holder = cls._get_context().get("read_snapshots")
        if isinstance(holder, _ReadSnapshots):
            holder.record(str(file_id or ""), content or "", kind="written", full=full)

    @classmethod
    def written_snapshot_state(cls, file_id: str) -> Any:
        """写入前的快照状态，提交失败时用 restore_written_snapshot 还原。"""
        holder = cls._get_context().get("read_snapshots")
        if isinstance(holder, _ReadSnapshots):
            return holder.written_state(str(file_id or ""))
        return None

    @classmethod
    def restore_written_snapshot(cls, file_id: str, state: Any) -> None:
        holder = cls._get_context().get("read_snapshots")
        if isinstance(holder, _ReadSnapshots):
            holder.restore_written(str(file_id or ""), state)

    @classmethod
    def begin_agent_run(cls) -> None:
        """换下一个 agent（或同一 agent 的下一程）：上一程读到的正文它看不到，不再算「模型看到的」。"""
        holder = cls._get_context().get("read_snapshots")
        if isinstance(holder, _ReadSnapshots):
            holder.begin_agent_run()

    @classmethod
    def get_current_agent(cls) -> str | None:
        """Get current agent type bound to tool execution context."""
        context = cls._get_context()
        current_agent = context.get("current_agent")
        return current_agent if isinstance(current_agent, str) and current_agent else None

    @classmethod
    def set_notes_append_allowed(cls, allowed: bool) -> None:
        """Set only from the current validated router decision before agent tools run."""
        context = cls._get_context()
        if context:
            _tool_context_var.set({**context, "notes_append_allowed": allowed is True})

    @classmethod
    def set_current_agent(cls, current_agent: str | None) -> None:
        """Update current agent in request-scoped context.

        The runner sets it for each agent run and resets it to None at run end; tools read it
        (e.g. the loop-only create_file / write_chapter rules), so it lives in the mutable
        request context rather than a value frozen at run start.
        """
        context = cls._get_context()
        if not context:
            return
        next_context = dict(context)
        if isinstance(current_agent, str) and current_agent:
            next_context["current_agent"] = current_agent
        else:
            next_context.pop("current_agent", None)
        _tool_context_var.set(next_context)

    @classmethod
    def _cleanup_owned_session(cls) -> None:
        """Clean up owned session if exists."""
        owned = _owned_session_var.get()
        if owned is not None:
            try:
                owned.close()
            except Exception as e:
                logger.debug(f"Error closing owned session: {e}")
            _owned_session_var.set(None)

    @classmethod
    def clear_context(cls) -> None:
        """Clear context and clean up owned session."""
        cls._cleanup_owned_session()
        # 先清空共享持有者：其它任务里仍存活的上下文副本引用同一个对象，
        # 仅把本上下文的 ContextVar 置 None 无法让它们看到清理结果。
        state = cls._get_pending_empty_file_state()
        if state is not None:
            state.clear_all()
        _tool_context_var.set(None)

    @classmethod
    def get_skill_content_budget(cls) -> SkillContentBudget:
        """本次请求共享的技能内容预算；没有请求上下文时每次给一份完整预算。"""
        context = _tool_context_var.get()
        budget = context.get("skill_content_budget") if isinstance(context, dict) else None
        if isinstance(budget, SkillContentBudget):
            return budget
        return SkillContentBudget()

    @classmethod
    def _get_pending_empty_file_state(
        cls, *, create: bool = False
    ) -> _PendingEmptyFileState | None:
        """获取请求上下文中跨任务共享的待写入文件持有者（create=True 时为单测按需初始化）。"""
        context = _tool_context_var.get()
        if isinstance(context, dict):
            state = context.get("pending_empty_file_state")
            if isinstance(state, _PendingEmptyFileState):
                return state
            if create:
                state = _PendingEmptyFileState()
                context["pending_empty_file_state"] = state
                return state
        elif create:
            state = _PendingEmptyFileState()
            _tool_context_var.set({"pending_empty_file_state": state})
            return state
        return None

    @classmethod
    def set_pending_empty_file(cls, file_id: str, title: str, version: str | None = None) -> None:
        """标记有一个空文件等待流式写入（追加进集合，不覆盖已有条目）。

        version：文件建好时的 updated_at（create_file 结果里的值）；没有时补不成也不会回滚它。
        """
        state = cls._get_pending_empty_file_state(create=True)
        assert state is not None
        state.add(file_id, title, version, cls.author_steering_count())

    @classmethod
    def clear_pending_empty_file(cls, file_id: str | None = None) -> None:
        """清除待写入文件标记。

        Args:
            file_id: 传入时只摘掉指向该文件的条目（流式写入收尾用）；
                省略表示无条件清空全部（请求/流结束时的兜底清理）。
        """
        state = cls._get_pending_empty_file_state()
        if state is not None:
            state.discard(file_id)

    @classmethod
    def has_pending_empty_file(cls) -> bool:
        """检查是否有待写入的空文件。"""
        return cls.get_pending_empty_file() is not None

    @classmethod
    def get_pending_empty_file(cls) -> dict[str, str] | None:
        """获取最近一个待写入的空文件信息（与历史单槽语义保持一致）。"""
        state = cls._get_pending_empty_file_state()
        return state.latest() if state is not None else None

    @classmethod
    def pending_empty_file_for(cls, file_id: str) -> dict[str, str] | None:
        """file_id 当前是待写入正文或待整章替换的文件时返回 {file_id, title}，否则为 None。"""
        state = cls._get_pending_empty_file_state()
        return state.entry_for(str(file_id or "")) if state is not None else None

    @classmethod
    def begin_model_reply(cls) -> None:
        """一次新的模型调用开始（usage_hooks 的 on_llm_start）。"""
        state = cls._get_pending_empty_file_state()
        if state is not None:
            state.begin_reply()

    @classmethod
    def overdue_file_bodies(cls) -> list[dict[str, str]]:
        """上一次回复建好、这次回复还没写正文的文件（两次回复协议被跳过）：见 overdue_unwritten_files；没有请求上下文时为空。"""
        state = cls._get_pending_empty_file_state()
        return state.overdue_unwritten_files() if state is not None else []

    @classmethod
    def author_steering_count(cls) -> int:
        """本请求到目前为止收到（已从队列取出）的作者插话条数；没有请求上下文时为 0。"""
        steering = cls._get_context().get("author_steering")
        return len(steering) if isinstance(steering, list) else 0

    @classmethod
    def steering_since_file_bodies_armed(cls, file_ids: list[str]) -> bool:
        """这些待写文件定为待写之后，本请求又收到过作者插话（结构化计数，不看插话内容）。"""
        state = cls._get_pending_empty_file_state()
        return state.steering_since_armed(file_ids, cls.author_steering_count()) if state is not None else False

    @classmethod
    def mark_pending_body_streamed(cls, file_id: str) -> None:
        """这个待写文件的正文已完整出现在本条模型回复里（条目留着，等 adapter 落库后清除）。"""
        state = cls._get_pending_empty_file_state()
        if state is not None:
            state.mark_body_streamed(file_id)

    @classmethod
    def offer_reply_prebody(cls) -> bool:
        """本条回复在没有待写文件时先写出了闭合的 <file> 正文（on_llm_end 调用）。见 _PendingEmptyFileState。"""
        state = cls._get_pending_empty_file_state()
        return state.offer_reply_prebody() if state is not None else False

    @classmethod
    def take_reply_prebody(cls) -> bool:
        """同一回复里建空文件的 create_file 认领先写出的那段正文；未建请求上下文时为 False。"""
        state = cls._get_pending_empty_file_state()
        return state.take_reply_prebody() if state is not None else False

    @classmethod
    def mark_latest_pending_body_streamed(cls) -> None:
        """最近一个待写文件的正文已以闭合 <file> 块完整出现在本条模型回复里。

        由 on_llm_end 钩子在 SDK 执行本轮工具前调用（见 usage_hooks），让同一
        回复里紧跟的 create_file 不被尚未落库的上一份正文误拒。条目仍留着，
        等 adapter 落库后清除。未建请求上下文时为 no-op。
        """
        state = cls._get_pending_empty_file_state()
        if state is not None:
            state.mark_latest_body_streamed()

    @classmethod
    def pending_empty_file_version(cls, file_id: str) -> str | None:
        """待写空文件建好时的版本（create_file 结果里的 updated_at）；不知道时为 None。"""
        state = cls._get_pending_empty_file_state()
        return state.version(file_id) if state is not None else None

    @classmethod
    def rearm_pending_file_body(cls, file_id: str) -> bool:
        """StreamAdapter 为空文件纠偏轮重新等这个文件的正文（见 _PendingEmptyFileState.rearm）。

        下一次回复照样要先以 <file> 写正文，其他工具仍按 file_body_pending 拒绝。返回这个文件的
        待写守卫是否确实恢复：没有请求上下文、或它已不是待写条目时为 False，调用方不能恢复捕获。
        """
        state = cls._get_pending_empty_file_state()
        return state.rearm(file_id) if state is not None else False

    @classmethod
    def try_reserve_pending_empty_file(
        cls, ticket: str, title: str
    ) -> tuple[bool, str | None]:
        """原子地占用"待流式写入"名额（检查 + 置位在同一把锁内完成）。

        Args:
            ticket: 调用方生成的唯一票据；成功后必须用它调 bind/release 收尾。
            title: 本次要创建的文件标题（占坑期间用于拼错误信息）。

        Returns:
            (是否占坑成功, 挡住本次创建的文件标题)。
        """
        state = cls._get_pending_empty_file_state(create=True)
        assert state is not None
        return state.try_reserve(ticket, title)

    @classmethod
    def bind_pending_empty_file(
        cls,
        ticket: str,
        file_id: str,
        title: str,
        version: str | None = None,
        replacement: dict[str, Any] | None = None,
    ) -> None:
        """占坑成功且文件已落库：把票据换成真实 file_id，并记下建好时的版本（或整章替换的定位结果）。"""
        state = cls._get_pending_empty_file_state(create=True)
        assert state is not None
        state.bind(ticket, file_id, title, version, replacement, cls.author_steering_count())

    @classmethod
    def close_pending_body_files(cls) -> list[dict[str, str]]:
        """这一轮提前结束：不再接收新的待写文件，返回此刻的待写条目（见 _PendingEmptyFileState.close）。"""
        state = cls._get_pending_empty_file_state(create=True)
        assert state is not None
        return state.close()

    @classmethod
    def pending_body_files_closed(cls) -> bool:
        state = cls._get_pending_empty_file_state()
        return state.is_closed() if state is not None else False

    @classmethod
    def pending_chapter_replacement(cls, file_id: str) -> dict[str, Any] | None:
        """write_chapter 定为整章替换、还在等正文的已有章节的定位结果；不是时为 None。"""
        state = cls._get_pending_empty_file_state()
        return state.replacement(file_id) if state is not None else None

    @classmethod
    def mark_chapter_replacement_saved(cls, file_id: str) -> None:
        """StreamAdapter 已把这一章的整章替换正文落库（replace_body 成功）。"""
        state = cls._get_pending_empty_file_state()
        if state is not None:
            state.mark_replacement_saved(file_id)

    @classmethod
    def mark_chapter_body_saved(cls, file_id: str, title: str = "", file_type: str = "") -> None:
        """StreamAdapter 已把这个文件的完整正文落库（新建或整章替换，不含截断补全）。"""
        state = cls._get_pending_empty_file_state()
        if state is not None and file_id:
            state.mark_chapter_body_saved(file_id, title, file_type)

    @classmethod
    def mark_cut_body_saved(cls, file_id: str, title: str = "") -> None:
        """StreamAdapter 把一份没等到 </file>、按流结束补全的新正文落了库（截断补全，不是完整正文）。"""
        state = cls._get_pending_empty_file_state()
        if state is not None and file_id:
            state.mark_cut_body_saved(file_id, title)

    @classmethod
    def take_cut_bodies(cls) -> dict[str, str]:
        """取走上次取走之后按截断补全保存的新正文：id -> 标题（loop 引擎每个 run 边界取一次）。"""
        state = cls._get_pending_empty_file_state()
        return state.take_cut_bodies() if state is not None else {}

    @classmethod
    def saved_bodies_this_request(cls) -> dict[str, str]:
        """本请求确认落库的完整正文：id -> 标题（保存点记下的结构化事实，不看模型文字）。"""
        state = cls._get_pending_empty_file_state()
        return state.saved_body_titles() if state is not None else {}

    @classmethod
    def saved_content_body_ids(cls) -> list[str]:
        """本请求确认落库的完整正文里的章节 / 分集正文（大纲、人物等资料文件不在内）。"""
        state = cls._get_pending_empty_file_state()
        return state.saved_content_body_ids() if state is not None else []

    @classmethod
    def chapter_body_saved_this_request(cls, file_id: str) -> bool:
        """这一章的正文是不是本请求里已经由 write_chapter 写完并保存的。"""
        state = cls._get_pending_empty_file_state()
        return state.chapter_body_saved(file_id) if state is not None else False

    @classmethod
    def unsaved_chapter_replacement_ids(cls) -> set[str]:
        """本请求里 write_chapter 定过、最近一次定位之后新正文还没有落库的已有章节。"""
        state = cls._get_pending_empty_file_state()
        return state.unsaved_replacement_ids() if state is not None else set()

    @classmethod
    def release_pending_empty_file(cls, ticket: str) -> None:
        """占坑作废（创建失败 / 建出来的是文件夹）：回滚票据，不留残留守卫。"""
        state = cls._get_pending_empty_file_state()
        if state is not None:
            state.release(ticket)

    @classmethod
    def get_pending_empty_files(cls) -> list[dict[str, str]]:
        """获取**全部**待写入空文件（按置位先后）。

        单槽时代只能看到最后一个，先建的空文件会成为无人认领的孤儿；
        需要逐个补写/逐个上报的调用方（如单循环 agent 的纠偏分支）用这个。
        """
        state = cls._get_pending_empty_file_state()
        return state.snapshot() if state is not None else []

    @classmethod
    def get_executor(cls) -> FileToolExecutor:
        """Get a FileToolExecutor with current context."""
        session = cls.get_session()
        context = cls._get_context()
        user_id = context.get("user_id")
        return FileToolExecutor(session, user_id)

def _make_result(data: Any, *, tool_name: str | None = None) -> dict[str, Any]:
    """Create a tool result in MCP format."""
    return _make_mcp_payload(data, tool_name=tool_name)


def _elide_reused_file_content(result: Any) -> Any:
    """剧本分集幂等复用分支的 tool result 省略正文，只留长度元信息。

    复用分支返回的 content 是目标文件**已有的整集正文**（实测一集 8000 字）。
    它只服务于服务端判据（StreamAdapter 的截断覆盖保护），模型一个字都用不到；
    原样序列化进回给模型的 tool result 里，等于每次分集复用都白烧几千 token。

    这里只对复用分支下手，判据是 crud 复用分支才会置的 reused_existing。
    用户显式带 content 调 create_file 建新文件时回显 content 是既有行为，不受影响。

    显式字段 reused_existing / original_content_length 必须原样保留：
    StreamAdapter._resolve_original_content_length 优先读它们判定
    「目标文件原本非空」，不依赖 content 本身，所以省略正文不削弱覆盖保护。
    """
    if not isinstance(result, dict) or not result.get("reused_existing"):
        return result

    elided = dict(result)
    original_content = elided.get("content")
    if isinstance(original_content, str) and original_content:
        elided["content"] = ""
        elided["content_elided"] = True
        # crud 层正常会给出 original_content_length；这里兜底补齐，
        # 避免上游漏填时把「原本非空」丢成 0。
        if not isinstance(elided.get("original_content_length"), int):
            elided["original_content_length"] = len(original_content)
    return elided


def _make_error(error: str, *, tool_name: str | None = None) -> dict[str, Any]:
    """Create an error result in MCP format."""
    return _make_mcp_payload({"status": "error", "error": error}, tool_name=tool_name)


def _make_mcp_payload(payload: Any, *, tool_name: str | None = None) -> dict[str, Any]:  # noqa: ARG001
    """Create MCP payload with unified size guardrail.

    ``tool_name`` only labels the call site; the serialized result does not carry it.
    """
    text = _serialize_tool_payload(payload)
    return {
        "content": [{
            "type": "text",
            "text": text,
        }]
    }


def _normalize_payload_status(payload: Any) -> str:
    """Infer normalized status from tool payload."""
    status = "success"
    if isinstance(payload, dict):
        raw_status = payload.get("status")
        if isinstance(raw_status, str) and raw_status:
            status = raw_status
        elif "error" in payload:
            status = "error"
    return status


# 截断长字符串时永不改写的键：这些是模型定位文件、判断成败所需的结构化信号。
_BOUND_PROTECTED_KEYS: frozenset[str] = frozenset({
    "id",
    "file_id",
    "pending_file_id",
    "parent_id",
    "entity_id",
    "entity_type",
    "title",
    "file_type",
    "type",
    "status",
    "error",
    "error_type",
    "description",
    "execution_id",
    "note",
    "warning",
    "truncation_note",
})
# 截断后每个长字符串至少保留的开头字符数（第一轮）；放不下时第二轮降到 0。
_BOUND_MIN_KEEP_CHARS = 200
_BOUND_LIST_TRUNCATED_SUFFIX = "…[已截断]"

TOOL_RESULT_OVERSIZE_NOTE = (
    "结果超过单次工具返回上限，各条目的 id/title/file_type/status 均已保留，"
    "content 等长文本字段已截断（content_truncated=true，content_length 为原长）。"
    '需要某个文件的完整正文时，用 query_files(id=…, response_mode="full") 单独读取该文件，'
    "不要重复整批读取。"
)


def _json_chars(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def _collect_long_string_leaves(
    value: Any, min_chars: int, out: list[tuple[Any, Any, str]]
) -> None:
    """收集可截断的长字符串叶子 (container, key, original)。受保护键不收集。"""
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(child, str):
                if key not in _BOUND_PROTECTED_KEYS and len(child) > min_chars:
                    out.append((value, key, child))
            else:
                _collect_long_string_leaves(child, min_chars, out)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            if isinstance(child, str):
                if len(child) > min_chars:
                    out.append((value, index, child))
            else:
                _collect_long_string_leaves(child, min_chars, out)


def bound_payload_strings(
    value: Any, max_chars: int, *, min_keep: int = _BOUND_MIN_KEEP_CHARS
) -> tuple[Any, bool]:
    """把 JSON 载荷里的长字符串**就地截短**，使整体序列化不超过 ``max_chars``。

    与「把 json.dumps 结果切一刀」不同，这里保持结构：每个字典条目的 id/title/
    file_type/status/error 等原样保留，只截 content 一类的长文本字段，并在旁边写上
    ``<字段>_length``（content 为 ``content_length``）与 ``<字段>_truncated: true``，
    模型能看出这是截断预览而不是短文件。各长字段按「注水」方式均分预算（统一上限 L），
    不会出现一个条目被砍光、其他条目原封不动。

    返回 (新载荷, 是否发生截断)。放不下时返回尽力截短后的结果，由调用方检查大小再降级。
    原载荷不被修改。
    """
    if value is None or _json_chars(value) <= max_chars:
        return value, False

    # 在深拷贝上定位叶子：容器对象必须是拷贝里的那个，原载荷不被修改。
    work = copy.deepcopy(value)
    leaves: list[tuple[Any, Any, str]] = []
    _collect_long_string_leaves(work, min_keep, leaves)
    if not leaves:
        return value, False
    lengths = [len(original) for _, _, original in leaves]

    def apply(cap: int) -> None:
        for container, key, original in leaves:
            if len(original) <= cap:
                container[key] = original
                continue
            if isinstance(container, dict):
                container[key] = original[:cap]
                if key == "content":
                    if not isinstance(container.get("content_length"), int):
                        container["content_length"] = len(original)
                    container["content_truncated"] = True
                else:
                    container.setdefault(f"{key}_length", len(original))
                    container[f"{key}_truncated"] = True
            else:
                container[key] = original[:cap] + _BOUND_LIST_TRUNCATED_SUFFIX

    # 每个被截字段额外写入 *_length/*_truncated，按 64 字符/字段预留；转义导致的
    # 偏差由外层循环再补一次。
    reduction = _json_chars(value) - max_chars + 64 * len(leaves)
    for _ in range(6):
        low, high = min_keep, max(lengths)
        # 找最大的 cap，使 sum(max(0, len - cap)) >= reduction。
        while low < high:
            mid = (low + high + 1) // 2
            if sum(max(0, length - mid) for length in lengths) >= reduction:
                low = mid
            else:
                high = mid - 1
        apply(low)
        size = _json_chars(work)
        if size <= max_chars or low <= min_keep:
            break
        reduction += size - max_chars + 64
    return work, True


def _compact_oversized_payload(
    payload: Any,
    *,
    original_length: int,
) -> str | None:
    """超限的成功结果：保结构截长文本，而不是把整个 data 换成一个占位符。

    返回可直接发给模型的 JSON；连骨架都放不下时返回 None，由调用方走最小占位。
    """
    if not isinstance(payload, dict) or "data" not in payload:
        return None
    compact = dict(payload)
    compact["truncated"] = True
    compact["max_chars"] = TOOL_RESULT_MAX_CHARS
    compact["original_length"] = original_length
    compact["note"] = TOOL_RESULT_OVERSIZE_NOTE
    for min_keep in (_BOUND_MIN_KEEP_CHARS, 0):
        bounded, _ = bound_payload_strings(compact, TOOL_RESULT_MAX_CHARS, min_keep=min_keep)
        encoded = json.dumps(bounded, ensure_ascii=False)
        if len(encoded) <= TOOL_RESULT_MAX_CHARS:
            return encoded
    return None


def _serialize_tool_payload(payload: Any) -> str:
    """Serialize tool payload and truncate oversized results safely."""
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized) <= TOOL_RESULT_MAX_CHARS:
        return serialized

    original_length = len(serialized)
    status = _normalize_payload_status(payload)

    if status != "error":
        structured = _compact_oversized_payload(payload, original_length=original_length)
        if structured is not None:
            return structured

    truncated_payload: dict[str, Any] = {
        "status": status,
        "truncated": True,
        "max_chars": TOOL_RESULT_MAX_CHARS,
        "original_length": original_length,
    }
    # Commit-owned signal must survive content elision; statuses/previews are not proof.
    if isinstance(payload, dict) and isinstance(payload.get("mutation_applied"), bool):
        truncated_payload["mutation_applied"] = payload["mutation_applied"]

    if status == "error":
        error_message = ""
        if isinstance(payload, dict):
            raw_error = payload.get("error")
            if raw_error is not None:
                error_message = str(raw_error)
        truncated_payload["error"] = error_message or "Tool result exceeded max size and was truncated"
    else:
        truncated_payload["data"] = {
            "truncated": True,
            "max_chars": TOOL_RESULT_MAX_CHARS,
            "original_length": original_length,
        }

    encoded_truncated = json.dumps(truncated_payload, ensure_ascii=False)
    if len(encoded_truncated) <= TOOL_RESULT_MAX_CHARS:
        return encoded_truncated

    if status == "error" and "error" in truncated_payload:
        compact_payload = dict(truncated_payload)
        compact_payload["error"] = str(compact_payload["error"])
        encoded_truncated = json.dumps(compact_payload, ensure_ascii=False)
        while len(encoded_truncated) > TOOL_RESULT_MAX_CHARS and compact_payload["error"]:
            overflow = len(encoded_truncated) - TOOL_RESULT_MAX_CHARS
            trim_count = max(1, overflow)
            compact_payload["error"] = compact_payload["error"][:-trim_count]
            encoded_truncated = json.dumps(compact_payload, ensure_ascii=False)
        if len(encoded_truncated) <= TOOL_RESULT_MAX_CHARS:
            return encoded_truncated

    # Final safety fallback: always return a small valid payload.
    minimal_payload = {
        "status": status,
        "truncated": True,
        "max_chars": TOOL_RESULT_MAX_CHARS,
        "original_length": original_length,
    }
    if "mutation_applied" in truncated_payload:
        minimal_payload["mutation_applied"] = truncated_payload["mutation_applied"]
    if status != "error":
        minimal_payload["data"] = {"truncated": True}
    else:
        minimal_payload["error"] = "Tool result truncated"
    return json.dumps(minimal_payload, ensure_ascii=False)


def _is_folder_file_type(result: dict[str, Any], args: dict[str, Any]) -> bool:
    """判断刚创建的节点是否是文件夹（以执行器落库后的类型为准，回退到入参）。"""
    raw = result.get("file_type") if isinstance(result, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raw = args.get("file_type") if isinstance(args, dict) else None
    return isinstance(raw, str) and raw.strip().lower() == "folder"


def _run_session_probe(session: Session, fn: Callable[[Session], Any]) -> Any:
    """在 session 上跑只读探测查询；失败时回滚 savepoint 或事务，不把异常状态留在连接上。"""
    in_tx = bool(hasattr(session, "in_transaction") and session.in_transaction())
    if in_tx and hasattr(session, "begin_nested"):
        with session.begin_nested():
            return fn(session)
    try:
        return fn(session)
    except Exception:
        if hasattr(session, "rollback"):
            session.rollback()
        raise


def _clear_settled_pending_empty_files() -> bool:
    """落库核验待写入空文件标记，清掉正文已写入或文件已不存在的条目。

    标记只在 <file>…</file> 流式写入收尾时清除；模型改用 edit_file(op=append)
    写完正文时标记会一直留着，后续每次不带 content 的 create_file 都会被它挡回。
    用列级查询读数据库当前值（绕开 ORM 身份映射里可能陈旧的实例）；查询失败时
    保守地不清除任何条目。返回是否清掉了至少一个条目。

    整章替换的条目在它的 <file> 正文落库前一直待写，即使文件已有旧正文：由 adapter
    （保存或拒绝）和 engine（运行边界）清除，这里跳过（同 engine 的 _settle_pending_empty_files）。
    """
    from models import File

    cleared = False
    for entry in ToolContext.get_pending_empty_files():
        file_id = str(entry.get("file_id") or "")
        if not file_id or ToolContext.pending_chapter_replacement(file_id) is not None:
            continue
        try:
            row = _run_session_probe(
                ToolContext.get_session(),
                lambda s, fid=file_id: s.exec(
                    select(File.content, File.is_deleted).where(File.id == fid)
                ).first(),
            )
        except Exception as e:
            logger.debug(f"Pending empty-file probe failed: {e}")
            return cleared
        if row is None or row[1] or str(row[0] or "").strip():
            ToolContext.clear_pending_empty_file(file_id)
            cleared = True
    return cleared


def _pending_empty_file_rejection(
    blocking_title: str | None, title: str, *, tool_name: str = "create_file"
) -> dict[str, Any]:
    """create_file 被「上一个空文件还没写正文」挡回时的错误，点名解除方法。

    error_type 让工具失败熔断器把它识别为顺序性拒绝（先做前置动作即可成功），
    不按「同一调用连续失败」熔断。解除方法与 create_file 的 next_step、正文待写守卫
    （tools_adapter.file_body_pending_text）一致：下一次回复以 <file> 写正文；
    不建议 edit_file——下一次回复里它会被守卫拒绝。
    """
    blocking_name = blocking_title or "未知"
    blocking_id = ""
    for entry in ToolContext.get_pending_empty_files():
        if entry.get("title") == blocking_title:
            blocking_id = str(entry.get("file_id") or "")
    id_part = f"（id={blocking_id}）" if blocking_id else ""
    payload: dict[str, Any] = {
        "status": "error",
        "error_type": "pending_empty_file_unwritten",
        "error": (
            f"文件「{title}」未创建：上一个文件「{blocking_name}」{id_part}正文还是空的，"
            "一次只能流式写入一个文件。"
            f"下一步：下一次回复直接以 <file> 开头写完「{blocking_name}」的正文、用 </file> 结束；"
            f"写完后再调用 {tool_name} 写「{title}」。在此之前重复调用 {tool_name} 只会再次被拒绝。"
        ),
    }
    if blocking_id:
        payload["pending_file_id"] = blocking_id
    return _make_mcp_payload(payload, tool_name=tool_name)


def _body_in_reply_next_step(title: str, tool_name: str) -> str:
    """create_file / write_chapter 认领了本条回复里先写出的正文（body_in_reply）时给模型的下一步。"""
    return f"「{title}」的正文就是这条回复里 {tool_name} 之前写的那段 <file> 正文，已存进这个文件，不要再写一遍。"


def _awaiting_body_data(data: dict[str, Any], title: str) -> dict[str, Any]:
    """create_file 建好了空文件：告诉模型下一次回复就写正文。

    下一次回复没写正文就调用的工具会被拒绝（``tools_adapter.file_body_pending_text``）。
    """
    return {
        **data,
        "awaiting_body": True,
        "next_step": (
            f"「{title}」已建好，正文还是空的。下一次回复直接以 <file> 开头写它的正文，"
            "写完用 </file> 结束；不要先调用其他工具。"
        ),
    }


def _repeat_of_pending_empty_file(args: dict[str, Any], project_id: str) -> dict[str, Any] | None:
    """create_file 被挡住时，判断它是不是在重复创建正挡路的那个空文件；是就把那个文件交回去。

    模型常见的做法是：上一次回复 create_file(X) 成功，下一次回复本该以 <file> 开头写
    X 的正文，却又调用一次 create_file(X)。这次调用要的就是已经建好的那个空文件，
    按「一次只能流式写一个文件」拒绝它只会多烧一轮、给作者留一张失败卡。

    只在以下条件同时成立时认作重复：没有建库途中的占坑、恰好只有一个尚未写出正文的
    条目、标题相同，且库里那一行仍在本项目、未删除、正文为空、父目录与类型都对得上。
    不新增条目、不新建文件；返回的 content 为空且不带 reused_existing，
    StreamAdapter 会照常为同一个 file_id 重新等待 <file> 正文。
    """
    from models import File

    state = ToolContext._get_pending_empty_file_state()
    if state is None:
        return None
    entry = state.sole_unwritten_entry()
    if entry is None:
        return None

    title = str(args.get("title") or "").strip()
    if not title or str(entry.get("title") or "").strip() != title:
        return None

    file_id = str(entry.get("file_id") or "")
    if not file_id:
        return None
    try:
        row = _run_session_probe(
            ToolContext.get_session(),
            lambda s: s.exec(
                select(File).where(File.id == file_id).execution_options(populate_existing=True)
            ).first(),
        )
    except Exception as e:
        logger.debug(f"Pending empty-file repeat probe failed: {e}")
        return None
    if (
        row is None
        or row.is_deleted
        or row.project_id != project_id
        or str(row.content or "").strip()
        or str(row.title or "").strip() != title
    ):
        return None

    requested_parent = args.get("parent_id") or None
    if requested_parent != (row.parent_id or None):
        return None
    requested_type = str(args.get("file_type") or "draft").strip().lower()
    row_type = str(row.file_type or "").strip().lower()
    # crud 会把剧本目录下分集的 draft/document 规范成 script，重复调用照原参数传来时也算同一个文件
    if requested_type != row_type and not (
        row_type == "script" and requested_type in {"draft", "document"}
    ):
        return None

    log_with_context(
        logger,
        20,  # INFO
        "agent.create_file.same_title_repeat",
        project_id=project_id,
        file_id=file_id,
        title=title,
        file_type=row_type,
    )
    data = serialize_file(row)
    data["content"] = ""
    data = _awaiting_body_data(data, title)
    data["next_step"] = "不要再调用 create_file。" + data["next_step"]
    return _make_result(
        {"status": "success", "mutation_applied": False, "data": data},
        tool_name="create_file",
    )


# 单循环 agent 的正文（draft / script）只经 write_chapter 按章号 / 集号写入：create_file 不建正文，
# edit_file 不整份替换正文（见 FileEditor 的 body_guards）。
LOOP_BODY_CREATE_ERROR_TYPE = "chapter_body_requires_write_chapter"
WRITE_CHAPTER_TOOL_NAME = "write_chapter"
WRITE_CHAPTER_ACTION_CREATED = "created"
WRITE_CHAPTER_ACTION_REPLACED = "replaced"
WRITE_CHAPTER_AMBIGUOUS_ERROR_TYPE = "chapter_target_ambiguous"
WRITE_CHAPTER_INVALID_ERROR_TYPE = "invalid_chapter"
WRITE_CHAPTER_REPLACE_PENDING_ERROR_TYPE = "chapter_replace_pending"
WRITE_CHAPTER_WRITTEN_THIS_TURN_ERROR_TYPE = "chapter_written_this_turn"
# file_type → 标题里的序号单位（有界的标识符检查，只比对标题开头的「第N章 / 第N集 / Chapter N / Episode N」）。
_CHAPTER_UNIT_BY_TYPE = {"draft": "章", "script": "集"}
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_SOURCE_COPY_FILE_TYPES = frozenset({"outline", "character", "lore", "snippet", "document"})
SOURCE_TEXT_CONTEXT_MISSING_ERROR_TYPE = "source_text_context_missing"
SOURCE_TEXT_NOT_FOUND_ERROR_TYPE = "source_text_not_in_author_message"
SOURCE_TEXT_INVALID_ERROR_TYPE = "invalid_source_text"
SOURCE_TEXT_CONFLICT_ERROR_TYPE = "source_text_conflict"
SOURCE_TEXT_FILE_TYPE_ERROR_TYPE = "invalid_source_text_file_type"
SOURCE_TEXT_EXISTING_MISMATCH_ERROR_TYPE = "source_text_existing_mismatch"


def _loop_agent_type() -> str:
    from agent.tools.registry import LOOP_AGENT_TYPE

    return LOOP_AGENT_TYPE


def _source_copy_content(args: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    """Validate a model-selected exact span from this request's raw author message.

    The model chooses what the material means and which span belongs in a file.  This
    guard only proves that the bytes being saved came from the current request; it does
    not infer intent or expand the selected range from natural language.
    """
    if "source_text" not in args:
        return None, None

    source_text = args.get("source_text")
    if not isinstance(source_text, str) or not source_text.strip():
        return None, _make_mcp_payload(
            {
                "status": "error",
                "error_type": SOURCE_TEXT_INVALID_ERROR_TYPE,
                "error": "source_text 必须是作者本轮消息中的非空连续原文；没有新建文件。",
                "mutation_applied": False,
            },
            tool_name="create_file",
        )
    if "content" in args or coerce_bool(args.get("empty")):
        return None, _make_mcp_payload(
            {
                "status": "error",
                "error_type": SOURCE_TEXT_CONFLICT_ERROR_TYPE,
                "error": "source_text 不能与 content 或 empty 同用；没有新建文件。",
                "mutation_applied": False,
            },
            tool_name="create_file",
        )

    file_type = str(args.get("file_type") or "").strip().lower()
    if file_type not in _SOURCE_COPY_FILE_TYPES:
        return None, _make_mcp_payload(
            {
                "status": "error",
                "error_type": SOURCE_TEXT_FILE_TYPE_ERROR_TYPE,
                "error": "source_text 只用于大纲、人物、设定、素材或其他资料文件；没有新建文件。",
                "mutation_applied": False,
            },
            tool_name="create_file",
        )

    author_message = ToolContext._get_context().get("author_message")
    if not isinstance(author_message, str):
        return None, _make_mcp_payload(
            {
                "status": "error",
                "error_type": SOURCE_TEXT_CONTEXT_MISSING_ERROR_TYPE,
                "error": "当前调用没有可校验的作者原始消息，不能复制 source_text；没有新建文件。",
                "mutation_applied": False,
            },
            tool_name="create_file",
        )
    if source_text not in author_message:
        return None, _make_mcp_payload(
            {
                "status": "error",
                "error_type": SOURCE_TEXT_NOT_FOUND_ERROR_TYPE,
                "error": "source_text 不是作者本轮消息中的逐字连续原文；不要改写、拼接或补字段。没有新建文件。",
                "mutation_applied": False,
            },
            tool_name="create_file",
        )
    return source_text, None


def _loop_body_create_refusal(args: dict[str, Any]) -> dict[str, Any] | None:
    """单循环 agent 用 create_file 建正文类文件时拒绝，指向 write_chapter；其余情况返回 None。"""
    from agent.tools.registry import LOOP_AGENT_TYPE

    if ToolContext.get_current_agent() != LOOP_AGENT_TYPE:
        return None
    requested_type = str(args.get("file_type") or "draft").strip().lower()
    file_type = requested_type
    if file_type == "document":
        # create_file 会把剧本分集目录下带集号的 document 归一成 script（见 resolve_create_target）：
        # 按实际会建的类型判断，不让正文绕过 write_chapter。
        target = _resolve_chapter_target(
            args.get("parent_id") or None,
            title=str(args.get("title") or ""),
            file_type=file_type,
            metadata=args.get("metadata"),
        )
        file_type = target[1] if target is not None else file_type
    if file_type not in CONTENT_FILE_TYPES:
        return None
    return _make_mcp_payload(
        {
            "status": "error",
            "error_type": LOOP_BODY_CREATE_ERROR_TYPE,
            "error": (
                f"create_file 不建正文（file_type={file_type}），没有新建文件。"
                + (
                    f"（剧本分集目录下标题带集号的 {requested_type} 文件会被当作分集剧本；要另存一版请放进素材等资料目录，"
                    "标题不带集号。）"
                    if requested_type != file_type
                    else ""
                )
                + "新写一章或整章重写都用 "
                "write_chapter(chapter=章号, title=章名, file_type=draft 或 script)，它按章号定位：这一章不存在就新建，"
                "已存在就替换这一章；调用后下一次回复用 <file>…</file> 写正文。大纲、人物、设定等资料文件用 create_file 并写明 file_type。"
            ),
            "mutation_applied": False,
        },
        tool_name="create_file",
    )


def _resolve_chapter_target(
    parent_id: str | None,
    *,
    title: str,
    file_type: str,
    metadata: Any = None,
) -> tuple[str | None, str] | None:
    """create_file 实际会用的 (parent_id, file_type)（同 crud 的归一）；读不到项目时为 None。"""
    from agent.tools.file_ops.crud import resolve_create_target
    from models import Project

    project_id = ToolContext._get_context().get("project_id")
    if project_id is None:
        return None
    try:
        return _run_session_probe(
            ToolContext.get_session(),
            lambda s: resolve_create_target(
                s,
                project_id,
                getattr(s.get(Project, project_id), "project_type", None),
                parent_id,
                title=title,
                file_type=file_type,
                metadata=metadata if isinstance(metadata, dict) else None,
            ),
        )
    except Exception as e:  # noqa: BLE001 - 解析不了时按原参数处理，create_file 自己校验父节点
        logger.debug(f"Resolving the create target failed: {type(e).__name__}")
        return None


def _chapter_title(title: str, number: int, unit: str) -> str:
    """标题不带章号时补上（展示格式）：中文标题补「第N章 」，其余补「Chapter N: 」。"""
    text = title.strip()
    if _CJK_RE.search(text) or not text:
        return f"第{number}{unit} {text}".strip()
    label = "Chapter" if unit == "章" else "Episode"
    return f"{label} {number}: {text}"


def _prefixed_chapter_heading(raw_title: str, unit: str) -> tuple[int, str] | None:
    """识别导入拆章形如「{书名} - 第N章 …」的标题：返回 (标题里的章/集号, 章号所在半段 heading)，不匹配返回 None。"""
    from utils.title_sequence import extract_sequence_number

    text = raw_title.replace("\x00", "").strip()
    if " - " not in text:
        return None
    parts = text.split(" - ")
    for idx in range(1, len(parts)):
        prefix = " - ".join(parts[:idx]).strip()
        heading = " - ".join(parts[idx:]).strip()
        if not prefix or not heading:
            continue
        seq = extract_sequence_number(heading, unit=unit)
        if seq is not None:
            return seq, heading
    return None


def _chapter_title_has_subtitle(raw_title: str, unit: str) -> bool:
    """判断传入的章节标题是否带有章名（而非仅传入空串或「第N章 / Chapter N」这类裸编号）。"""
    from utils.title_sequence import _RE_ARABIC_SEQ, _RE_CN_SEQ, _RE_EN_CHAPTER, extract_sequence_number

    text = raw_title.replace("\x00", "").strip()
    if not text:
        return False
    prefixed = _prefixed_chapter_heading(text, unit)
    if prefixed is not None:
        text = prefixed[1]
    if extract_sequence_number(text, unit=unit) is None:
        return True
    for pattern in (_RE_CN_SEQ, _RE_ARABIC_SEQ, _RE_EN_CHAPTER):
        match = pattern.match(text)
        if match:
            remainder = text[match.end() :].lstrip(" \t\u3000:：·—-–")
            return bool(remainder)
    return True


def _row_chapter_number(row: Any, *, unit: str, expected_file_type: str) -> int | None:
    """按标题开头的章/集号解析；无前缀章号时（如导入拆章「书名 - 第N章 …」）回退读取 file_metadata。"""
    from collections.abc import Mapping

    from utils.title_sequence import extract_sequence_number, extract_sequence_number_from_metadata

    if getattr(row, "file_type", None) != expected_file_type:
        return None
    seq = extract_sequence_number(row.title, unit=unit)
    if seq is not None:
        return seq
    raw_meta = getattr(row, "file_metadata", None)
    if not raw_meta:
        return None
    if isinstance(raw_meta, Mapping):
        meta: Mapping[str, Any] | None = raw_meta
    elif isinstance(raw_meta, str):
        try:
            parsed = json.loads(raw_meta)
        except (json.JSONDecodeError, TypeError):
            return None
        meta = parsed if isinstance(parsed, Mapping) else None
    else:
        return None
    return extract_sequence_number_from_metadata(meta, file_type=expected_file_type)


def _write_chapter_error(error: str, *, error_type: str, **fields: Any) -> dict[str, Any]:
    return _make_mcp_payload(
        {"status": "error", "error": error, "error_type": error_type, "mutation_applied": False, **fields},
        tool_name=WRITE_CHAPTER_TOOL_NAME,
    )


async def write_chapter(args: dict[str, Any]) -> dict[str, Any]:
    """按章号 / 集号写一章正文：不存在就新建，已存在就替换这一章（正文随后以 <file> 写入）。"""
    return await _offload_write_tool(WRITE_CHAPTER_TOOL_NAME, _write_chapter_sync, args)


def _write_chapter_sync(args: dict[str, Any]) -> dict[str, Any]:
    """write_chapter 的同步实现。

    目标只按编号解析：本项目未删除、与请求同类型的正文文件里，标题开头的章号（draft）或集号
    （script）等于 ``chapter`` 的那一个（给了 parent_id 只看那个目录）。

    - 没有：按 create_file 的路径新建空文件（标题不带章号时补上），正文在下一次回复用 <file> 写入；
      两次回复协议、待写守卫、空文件纠偏与回滚、退还时移除空白占位都照旧。
    - 恰好一个：把这一章定为整章替换目标（不改数据库），记下当时的版本，同样等下一次回复的 <file>；
      正文写完后 StreamAdapter 用 ``FileEditor.replace_body`` 提交（版本核对、作者正文保护、AI 版本、
      撤销锚点、diff 审阅），结果补发到同一张卡上。作者保存过正文的章节在这里就拒绝。
    - 多个（重复的章节文件，或没给 parent_id 而多个目录都有这一章）：不写，列出候选让模型确认。
    """
    from models import File
    from utils.title_sequence import extract_sequence_number

    tool_name = WRITE_CHAPTER_TOOL_NAME
    project_id = ToolContext._get_context().get("project_id")
    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    raw_number = args.get("chapter")
    try:
        number = int(raw_number) if not isinstance(raw_number, bool) else 0
    except (TypeError, ValueError):
        number = 0
    if number <= 0 or (isinstance(raw_number, float) and not raw_number.is_integer()):
        return _write_chapter_error(
            "write_chapter 需要 chapter（正整数章号；剧本是集号），没有写入。",
            error_type=WRITE_CHAPTER_INVALID_ERROR_TYPE,
        )
    file_type = str(args.get("file_type") or "draft").strip().lower()
    unit = _CHAPTER_UNIT_BY_TYPE.get(file_type)
    if unit is None:
        return _write_chapter_error(
            "write_chapter 只写正文：file_type 是 draft（小说章节）或 script（剧本分集），没有写入。"
            "大纲、人物、设定等资料文件用 create_file。",
            error_type=WRITE_CHAPTER_INVALID_ERROR_TYPE,
        )
    raw_title = str(args.get("title") or "").strip()
    titled_number = extract_sequence_number(raw_title, unit=unit)
    prefixed_heading = _prefixed_chapter_heading(raw_title, unit) if titled_number is None else None
    if prefixed_heading is not None:
        titled_number = prefixed_heading[0]
    if titled_number is not None and titled_number != number:
        return _write_chapter_error(
            f"标题「{raw_title}」写的是第{titled_number}{unit}，chapter 却是 {number}，没有写入。"
            f"确认要写第几{unit}后让两者一致（title 可以只写章名）。",
            error_type=WRITE_CHAPTER_INVALID_ERROR_TYPE,
        )
    title = raw_title if titled_number is not None else _chapter_title(raw_title, number, unit)
    requested_parent_id = args.get("parent_id") or None
    parent_id = requested_parent_id
    if requested_parent_id is not None:
        # 与 create_file 同一套父节点归一（传成上一章的文件 id、剧本项目传了 -draft-folder）：
        # 查找与新建落在同一个目录，不会在已有这一章的目录旁边再建一个。
        target = _resolve_chapter_target(requested_parent_id, title=title, file_type=file_type)
        if target is not None:
            parent_id = target[0]

    try:
        rows = _run_session_probe(
            ToolContext.get_session(),
            lambda s: s.exec(
                select(File.id, File.title, File.parent_id, File.file_type, File.file_metadata).where(
                    File.project_id == project_id,
                    File.is_deleted.is_(False),
                    File.file_type == file_type,
                )
            ).all(),
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"读取章节列表失败，没有写入：{e}", tool_name=tool_name)
    matches = [
        row
        for row in rows
        if _row_chapter_number(row, unit=unit, expected_file_type=file_type) == number
        and (parent_id is None or (row.parent_id or None) == parent_id)
    ]
    if len(matches) > 1:
        listed = "；".join(f"《{row.title}》(id={row.id}, parent_id={row.parent_id})" for row in matches[:6])
        return _write_chapter_error(
            f"第{number}{unit}对应不止一个文件：{listed}。没有写入。"
            f"分卷时传 parent_id 指明是哪一卷；同一目录里重复的第{number}{unit}文件请告诉作者，"
            "由作者决定保留哪一个，不要自己删除或合并。",
            error_type=WRITE_CHAPTER_AMBIGUOUS_ERROR_TYPE,
            candidates=[{"id": row.id, "title": row.title, "parent_id": row.parent_id} for row in matches[:6]],
        )
    empty = coerce_bool(args.get("empty"))
    if matches and requested_parent_id is None and not empty:
        body_folders = {
            row.parent_id or None
            for row in rows
            if _row_chapter_number(row, unit=unit, expected_file_type=file_type) is not None
        }
        if len(body_folders) > 1:
            # 正文分在几个目录（分卷常各自从第1章编号）而没指明目录：唯一的同号章节不一定是要写的那一卷，
            # 不把它当替换目标。
            existing = matches[0]
            return _write_chapter_error(
                f"正文分在 {len(body_folders)} 个目录里，没给 parent_id 时不确定要写哪一个目录的第{number}{unit}"
                f"（已有的是《{existing.title}》，parent_id={existing.parent_id}）。没有写入。"
                "传 parent_id 指明目录：要替换它就传它所在的目录，要在别的卷新写就传那一卷的目录。",
                error_type=WRITE_CHAPTER_AMBIGUOUS_ERROR_TYPE,
                candidates=[{"id": existing.id, "title": existing.title, "parent_id": existing.parent_id}],
            )
    if not matches:
        if parent_id is None:
            parent_id = _default_chapter_folder(project_id, file_type, rows)
        return _write_chapter_create(
            args, title=title, number=number, unit=unit, file_type=file_type, parent_id=parent_id, empty=empty
        )
    if empty:
        # 只建空章节：这一章已经有文件，什么也不做。
        existing = matches[0]
        return _make_result(
            {
                "status": "success",
                "mutation_applied": False,
                "data": {
                    "id": existing.id,
                    "title": existing.title,
                    "file_type": existing.file_type,
                    "chapter": number,
                    "action": "exists",
                    "result": f"第{number}{unit}《{existing.title}》已经有文件，没有改动。",
                },
            },
            tool_name=tool_name,
        )
    existing_title = (matches[0].title or "").strip()
    if raw_title and raw_title == existing_title:
        requested_replace_title = None
    else:
        requested_replace_title = title if _chapter_title_has_subtitle(raw_title, unit) else None
    return _write_chapter_replace(
        matches[0].id,
        number=number,
        unit=unit,
        project_id=project_id,
        requested_title=requested_replace_title,
    )


def _default_chapter_folder(project_id: str, file_type: str, rows: list[Any]) -> str | None:
    """没给 parent_id 的新章节放哪：同类型章节都在同一个目录就用它，否则用项目的默认正文目录；都没有返回 None。"""
    from models import File

    parents = {row.parent_id or None for row in rows if row.file_type == file_type}
    if len(parents) == 1:
        (only,) = parents
        if only is not None:
            return only
    default_ids = [f"{project_id}-draft-folder", f"{project_id}-script-folder"]
    if file_type == "script":
        default_ids = default_ids[1:]
    try:
        found = _run_session_probe(
            ToolContext.get_session(),
            lambda s: s.exec(
                select(File.id).where(
                    File.id.in_(default_ids),
                    File.project_id == project_id,
                    File.is_deleted.is_(False),
                    File.file_type == "folder",
                )
            ).all(),
        )
    except Exception as e:  # noqa: BLE001 - 查不到默认目录时保持原行为
        logger.debug(f"Looking up the default chapter folder failed: {type(e).__name__}")
        return None
    return next((fid for fid in default_ids if fid in set(found)), None)


def _write_chapter_create(
    args: dict[str, Any],
    *,
    title: str,
    number: int,
    unit: str,
    file_type: str,
    parent_id: str | None,
    empty: bool,
) -> dict[str, Any]:
    create_args: dict[str, Any] = {"title": title, "file_type": file_type, "content": "", "empty": empty}
    if parent_id is not None:
        create_args["parent_id"] = parent_id
    if args.get("metadata") is not None:
        create_args["metadata"] = args.get("metadata")
    raw = _create_file_sync(create_args, tool_name=WRITE_CHAPTER_TOOL_NAME)
    payload = _payload_of(raw)
    if not isinstance(payload, dict) or payload.get("status") != "success":
        return raw
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    created_title = str(data.get("title") or title)
    data = {
        **data,
        "chapter": number,
        "action": WRITE_CHAPTER_ACTION_CREATED,
        "result": f"新建了第{number}{unit}《{created_title}》。",
    }
    return _make_result({**payload, "data": data}, tool_name=WRITE_CHAPTER_TOOL_NAME)


def _write_chapter_replace(
    file_id: str,
    *,
    number: int,
    unit: str,
    project_id: str,
    requested_title: str | None = None,
) -> dict[str, Any]:
    from models import File
    from models.file_model import validate_file_title
    from utils.text_metrics import count_words

    tool_name = WRITE_CHAPTER_TOOL_NAME
    pending_ids = {str(entry.get("file_id") or "") for entry in ToolContext.get_pending_empty_files()}
    session = ToolContext.get_session()
    try:
        row = _run_session_probe(
            session,
            lambda s: s.exec(
                select(File).where(File.id == file_id).execution_options(populate_existing=True)
            ).first(),
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"读取第{number}{unit}失败，没有写入：{e}", tool_name=tool_name)
    if row is None or row.is_deleted or row.project_id != project_id:
        return _write_chapter_error(
            f"第{number}{unit}的文件已不存在，没有写入。", error_type=EDIT_ERROR_FILE_NOT_FOUND
        )
    content = row.content or ""
    data = serialize_file(row)
    data.pop("content", None)
    data["chapter"] = number
    if file_id in pending_ids:
        armed = ToolContext.pending_chapter_replacement(file_id)
        if armed is not None:
            # 已有的这一章已经定为整章替换、在等正文（同一回复里重复或并行调用）：不当成新建，
            # 也不重新定位；第一次调用的结果与卡片照旧有效。
            return _write_chapter_error(
                f"第{number}{unit}《{row.title}》已经定为整章替换（原{armed.get('previous_word_count')}字），正在等正文，"
                "这次调用没有改动。不要再调用 write_chapter；下一次回复直接以 <file> 开头写这一章完整的新正文。",
                error_type=WRITE_CHAPTER_REPLACE_PENDING_ERROR_TYPE,
                id=file_id,
                chapter=number,
            )
        # 这一章就是本请求刚建、还在等正文的那个文件：交回它，下一次回复照常写正文。
        data = _awaiting_body_data(data, str(row.title or ""))
        data["action"] = WRITE_CHAPTER_ACTION_CREATED
        if content and ToolContext.pending_empty_file_version(file_id) is None:
            # 复用的已有文件（不是本请求建的）已经有正文：带上原长度，截断补全时不整体覆盖它。
            data["reused_existing"] = True
            data["original_content_length"] = len(content)
        data["next_step"] = "不要再调用 write_chapter。" + data["next_step"]
        return _make_result({"status": "success", "mutation_applied": False, "data": data}, tool_name=tool_name)

    if ToolContext.chapter_body_saved_this_request(file_id):
        # 这一章的正文这一轮已经由 write_chapter 写完并保存：不再整章重写（自查发现的问题用 edit_file 局部改）。
        # 看的是本请求里确认落库的结构化记录，不看模型的文字。
        return _write_chapter_error(
            f"第{number}{unit}《{row.title}》的正文这一轮已经用 write_chapter 写好并保存了，不再整章重写，没有写入。"
            "要改其中几处用 edit_file 局部修改；没有要改的就继续作者这一轮要求的下一步或回复作者。",
            error_type=WRITE_CHAPTER_WRITTEN_THIS_TURN_ERROR_TYPE,
            id=file_id,
            chapter=number,
        )

    new_title: str | None = None
    if isinstance(requested_title, str):
        cleaned_title = requested_title.replace("\x00", "").strip()
        if cleaned_title and cleaned_title != (row.title or ""):
            try:
                validate_file_title(cleaned_title)
            except ValueError as e:
                return _write_chapter_error(str(e), error_type=WRITE_CHAPTER_INVALID_ERROR_TYPE, id=file_id)
            clash = _run_session_probe(
                session,
                lambda s: s.exec(
                    select(File.id).where(
                        File.project_id == project_id,
                        File.parent_id == row.parent_id,
                        File.title == cleaned_title,
                        File.is_deleted.is_(False),
                        File.id != file_id,
                    )
                ).first(),
            )
            if clash is not None:
                return _write_chapter_error(
                    f"同一文件夹里已有名为《{cleaned_title}》的文件，没有写入；请换一个标题或先向作者确认。",
                    error_type=WRITE_CHAPTER_INVALID_ERROR_TYPE,
                    id=file_id,
                )
            new_title = cleaned_title

    reservation = f"reserve-{uuid4().hex}"
    acquired, blocking_title = ToolContext.try_reserve_pending_empty_file(reservation, str(new_title or row.title or ""))
    if not acquired and _clear_settled_pending_empty_files():
        acquired, blocking_title = ToolContext.try_reserve_pending_empty_file(reservation, str(new_title or row.title or ""))
    if not acquired:
        return _pending_empty_file_rejection(blocking_title, str(new_title or row.title or ""), tool_name=tool_name)
    previous_words = count_words(content)
    title_clause = (
        f"保存时标题一并更新为《{new_title}》。"
        if new_title
        else "标题沿用（写完正文前不要对这一章调用 rename_file）。"
    )
    data.update(
        {
            "action": WRITE_CHAPTER_ACTION_REPLACED,
            "awaiting_body": True,
            "previous_length": len(content),
            "previous_word_count": previous_words,
            "original_content_length": len(content),
            **({"new_title": new_title} if new_title else {}),
            "result": (
                f"覆盖已有的第{number}{unit}《{row.title}》（原{previous_words}字）：下一次回复写的正文会整章替换它，"
                f"{title_clause}"
            ),
            "next_step": (
                "下一次回复直接以 <file> 开头写这一章完整的新正文（不是改动片段），写完用 </file> 结束；"
                "不要先调用其他工具。保存后原稿可在历史版本里找回（也可在聊天修改卡撤销）。"
            ),
        }
    )
    # 不是本轮新建的文件：不记建好时的版本（补不成时不回滚、不算可以取消的空文件），记下替换的定位结果。
    ToolContext.bind_pending_empty_file(
        reservation, file_id, str(new_title or row.title or ""), None, replacement=data
    )
    if ToolContext.take_reply_prebody():
        # 本条回复在这次调用之前已写出正文：它就是这一章的替换稿，由 StreamAdapter 走整章替换落库。
        ToolContext.mark_pending_body_streamed(file_id)
        data = {
            **data,
            "body_in_reply": True,
            "next_step": _body_in_reply_next_step(str(row.title or ""), tool_name),
        }
    log_with_context(
        logger,
        20,  # INFO
        "agent.write_chapter.replace_armed",
        project_id=project_id,
        file_id=file_id,
        unit=unit,
        number=number,
        previous_length=len(content),
    )
    return _make_result({"status": "success", "mutation_applied": False, "data": data}, tool_name=tool_name)


def _payload_of(result: Any) -> Any:
    """MCP 结果里那段 JSON 文本解析回字典（解析不了返回 None）。"""
    try:
        return json.loads(result["content"][0]["text"])
    except (KeyError, IndexError, TypeError, json.JSONDecodeError):
        return None


async def create_file(args: dict[str, Any]) -> dict[str, Any]:
    """创建新文件。"""
    # asyncio.to_thread 运行在拷贝上下文中，但 pending-empty-file 标记通过
    # 请求上下文 dict 里的共享持有者原地修改，_create_file_sync 在工作线程
    # 里设置后对主上下文同样可见，无需在此回填。
    return await _offload_write_tool("create_file", _create_file_sync, args)


def _create_file_sync(args: dict[str, Any], *, tool_name: str = "create_file") -> dict[str, Any]:
    """Synchronous create_file implementation (write_chapter 新建一章时也走这里)."""
    project_id = ToolContext._get_context().get("project_id")

    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    title = args.get("title", "")
    if tool_name == "create_file":
        body_refusal = _loop_body_create_refusal(args)
        if body_refusal is not None:
            return body_refusal
        source_content, source_error = _source_copy_content(args)
        if source_error is not None:
            return source_error
    else:
        source_content = None
    content = source_content if source_content is not None else args.get("content", "")
    executor = ToolContext.get_executor()
    # 作者明确只要建文件（「只建标题，不写内容」）：这个空文件就是交付物，不等正文。
    # 只在路由判断作者本轮不要正文时生效，作者要正文时模型传了也照常等正文。
    empty_requested = coerce_bool(args.get("empty"))
    empty_by_request = empty_requested and not content and ToolContext.author_wants_no_body()

    # 检查是否已有待写入的空文件（防止连续创建空文件导致内容丢失）。
    # 检查与置位必须原子完成：两者之间隔着 DB INSERT（还跨 asyncio.to_thread
    # 的线程边界），先查后置会让并发的两次空文件创建同时通过检查，各自建出一个
    # 空文件，后置位的把先置位的覆盖掉——先建的空文件从此没有任何指针。
    # 因此这里先 try_reserve 占坑，落库后再 bind 真实 file_id。
    reservation: str | None = None
    if not content and not empty_by_request:
        reservation = f"reserve-{uuid4().hex}"
        acquired, blocking_title = ToolContext.try_reserve_pending_empty_file(
            reservation, title
        )
        if not acquired and _clear_settled_pending_empty_files():
            # 挡路的标记已陈旧（正文已经用 edit_file 等方式写进去了，或文件已删）：
            # 清掉后再占一次坑，不让模型为一个早已完成的文件反复撞墙。
            acquired, blocking_title = ToolContext.try_reserve_pending_empty_file(
                reservation, title
            )
        if not acquired:
            # 挡路的正是本次要建的这个空文件（同标题、同父目录、同类型）：交回它，
            # 让模型直接写正文；挡路的是别的文件时照旧拒绝。
            repeat = _repeat_of_pending_empty_file(args, project_id)
            if repeat is not None:
                return repeat
            return _pending_empty_file_rejection(blocking_title, title, tool_name=tool_name)

    try:
        order_value = args.get("order") if "order" in args else None
        if order_value is not None:
            try:
                order_value = int(order_value)
            except (TypeError, ValueError):
                order_value = None

        result = executor.create_file(
            project_id=project_id,
            title=title,
            file_type=args.get("file_type", "draft"),
            content=content,
            parent_id=args.get("parent_id"),
            order=order_value,
            metadata=args.get("metadata"),
            # 应用内 agent 写正文：draft/script 的双引号按稿件体例规范化。
            normalize_quotes=True,
        )

        # 如果创建的是空文件，把占坑换成真实 file_id
        # folder 例外：文件夹是纯容器节点，永远不会收到 <file>…</file> 正文，
        # 给它置标记后无人清除（标记跨子任务可见），会硬阻断本轮后续所有建档，
        # 并让纠偏分支把章节正文追加到文件夹节点上。
        awaiting_body = False
        body_in_reply = False
        if reservation is not None:
            file_id = result.get("id", "")
            if file_id and not _is_folder_file_type(result, args):
                # 只有本轮新建的文件记下建好时的版本（之后补不成可以回滚）；复用的既有空文件
                # 照样等正文，但不是本轮产物，不记版本，回滚不删它。
                created_version = None if result.get("reused_existing") is True else result.get("updated_at")
                ToolContext.bind_pending_empty_file(
                    reservation, file_id, title, created_version if isinstance(created_version, str) else None
                )
                reservation = None
                if ToolContext.pending_body_files_closed():
                    # 这一轮已经提前结束（StreamAdapter 不会再处理这个结果）：撤销刚建的空文件。
                    return _withdraw_file_created_after_close(file_id, title, result, tool_name=tool_name)
                awaiting_body = True
                # 本条回复在建它之前已写出正文：正文属于这个新文件，由 StreamAdapter 落库；
                # 守卫视同正文已写出，下一次回复不再要求先写它。复用的既有文件不认领。
                if result.get("reused_existing") is not True and ToolContext.take_reply_prebody():
                    ToolContext.mark_pending_body_streamed(file_id)
                    body_in_reply = True
            else:
                ToolContext.release_pending_empty_file(reservation)
            reservation = None


        if source_content is not None:
            if result.get("reused_existing") is True and result.get("content") != source_content:
                return _make_mcp_payload(
                    {
                        "status": "error",
                        "error_type": SOURCE_TEXT_EXISTING_MISMATCH_ERROR_TYPE,
                        "error": "同名文件已存在且内容与作者原文不同；现有文件保持原样，没有复制 source_text。",
                        "mutation_applied": False,
                    },
                    tool_name=tool_name,
                )
            result = {**result, "source_copied": True, "body_saved": True}
        data = _elide_reused_file_content(result)
        if body_in_reply:
            data = {**data, "body_in_reply": True, "next_step": _body_in_reply_next_step(title, tool_name)}
        elif awaiting_body:
            data = _awaiting_body_data(data, title)
            if empty_requested:
                data["next_step"] = "作者这一轮要正文，empty 不生效。" + data["next_step"]
        elif empty_by_request:
            data = {**data, "empty_by_request": True}
        return _make_result(
            {
                "status": "success",
                "mutation_applied": result.get("mutation_applied") is True,
                "data": data,
            },
            tool_name=tool_name,
        )
    except FileWriteRecoveryError as e:
        return _write_recovery_payload(e, tool_name=tool_name)
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)
    finally:
        # 创建失败（异常或提前 return）时必须回滚占坑，否则这个永远不会被
        # bind 的票据会把本请求后续所有空文件创建全部挡在门外。
        if reservation is not None:
            ToolContext.release_pending_empty_file(reservation)


def _withdraw_file_created_after_close(
    file_id: str, title: str, result: dict[str, Any], *, tool_name: str
) -> dict[str, Any]:
    """这一轮被提前结束之后才建好的空文件：只撤销本请求新建、版本未变的那个，然后报告没有建成。"""
    from agent.core.empty_files import rollback_unfinished_empty_files

    version = result.get("updated_at") if result.get("reused_existing") is not True else None
    if isinstance(version, str) and version:
        rollback_unfinished_empty_files([{"file_id": file_id, "title": title, "updated_at": version}])
    ToolContext.clear_pending_empty_file(file_id)
    return _make_mcp_payload(
        {
            "status": "error",
            "error": "这一轮已经结束，没有新建文件。",
            "error_type": "round_closed",
            "mutation_applied": False,
        },
        tool_name=tool_name,
    )


def _write_recovery_payload(error: FileWriteRecoveryError, *, tool_name: str) -> dict[str, Any]:
    return _make_mcp_payload(
        {"status": "error", "error": str(error), "error_type": error.error_type,
         "id": error.file_id, "mutation_applied": False, "user_message": str(error)},
        tool_name=tool_name,
    )


async def edit_file(args: dict[str, Any]) -> dict[str, Any]:
    """精确编辑文件内容。"""
    return await _offload_write_tool("edit_file", _edit_file_sync, args)


def _edit_file_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous edit_file implementation."""
    tool_name = "edit_file"
    executor = ToolContext.get_executor()
    project_id = ToolContext._get_context().get("project_id")

    # 写工具必须运行在绑定了项目的上下文中；执行器层会进一步校验目标文件
    # 属于该项目（check_file_access_in_tool_context）。
    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    read_ticket: int | None = None
    try:
        file_id_raw = args.get("id") or args.get("file_id") or args.get("fileId")
        if isinstance(file_id_raw, str):
            file_id = file_id_raw.strip()
        elif file_id_raw is None:
            file_id = ""
        else:
            # Do not silently accept non-string IDs; keep tool inputs strict to
            # avoid noisy "file not found" logs when the caller passes wrong types.
            return _make_edit_error(
                "edit_file: invalid param 'id' (must be a string).",
                error_type=EDIT_ERROR_INVALID_EDIT,
            )
        if not file_id:
            return _make_edit_error(
                "edit_file: missing required param 'id' (alias: file_id). "
                "Please query_files to get the correct id, or use the provided 当前文件 ID.",
                error_type=EDIT_ERROR_INVALID_EDIT,
            )

        edits = args.get("edits")
        if edits is None:
            edits = args.get("operations", [])
        if not isinstance(edits, list):
            return _make_edit_error(
                "edit_file: invalid param 'edits' (must be an array).",
                error_type=EDIT_ERROR_INVALID_EDIT,
            )

        if ToolContext.pending_chapter_replacement(file_id) is not None or (
            ToolContext.get_current_agent() == _loop_agent_type()
            and ToolContext.pending_empty_file_for(file_id) is not None
        ):
            pending_entry = ToolContext.pending_empty_file_for(file_id) or {"title": file_id}
            return _make_edit_error(
                f"《{pending_entry['title']}》已定为待写入正文（等待 <file>…</file>），edit_file 未执行（没有改动）。"
                "请先在下一次回复用 <file>…</file> 写完它的正文，保存之后确需局部修改再调用 edit_file。",
                error_type=WRITE_CHAPTER_REPLACE_PENDING_ERROR_TYPE,
                id=file_id,
            )

        # 作者手改过的文件不再整份拒绝：作者改了什么见 <author_edits>，由模型保留或按作者的新要求改；
        # 这里只核对事实。模型本请求里没看过这个文件的全文时，old / anchor 只按逐字匹配，
        # 不拿可能过时的记忆去模糊匹配作者刚改过的字。有快照（看过或自己写过）就做陈旧检查。
        has_snapshot = ToolContext.read_snapshot(file_id) is not None
        has_seen = ToolContext.has_seen_file(file_id)
        read_ticket = ToolContext.read_ticket()

        result = executor.edit_file(
            id=file_id,
            edits=edits,
            # 必须走 coerce_bool：strict_json_schema=False 让 schema 里的
            # "type": "boolean" 在运行时毫无约束力，模型常把它序列化成
            # "false"/"0"，而 bool("false") is True——一次编辑失败后本该中止的
            # 批量编辑会继续往下做，且整体仍被报成 success。
            continue_on_error=coerce_bool(args.get("continue_on_error")),
            # 只规范化各 edit 的新文本，未编辑的段落不动。
            normalize_quotes=True,
            # 在锁内取快照：同一请求里先写的那一步已经把快照更新为它写下的正文。
            read_snapshot_fn=(lambda: ToolContext.read_snapshot(file_id)) if has_snapshot else None,
            exact_only=not has_seen,
            report_author_edits=True,
            # 单循环 agent 改正文：不整份替换（走 write_chapter）、不新添逐字重复的段落。
            body_guards=ToolContext.get_current_agent() == _loop_agent_type(),
        )
        # status 必须按实际结果降级：continue_on_error 下部分/全部 edit 失败时，
        # 恒返回 "success" 会让模型以为改动已经落地、继续往下写，
        # 失败的 edit 只藏在 data.failed_edits 里没人看。
        status = _derive_edit_status(result)
        payload: dict[str, Any] = {
            "status": status,
            "mutation_applied": result.get("mutation_applied") is True,
            "data": result,
        }
        if status == "error":
            # 全部失败（continue_on_error=true）：与整批回滚一样带上稳定分类和给作者的短句。
            failed = result.get("failed_edits") if isinstance(result, dict) else None
            first = failed[0] if isinstance(failed, list) and failed and isinstance(failed[0], dict) else {}
            error_type = str(first.get("error_type") or EDIT_ERROR_GENERIC)
            payload["error_type"] = error_type
            payload["user_message"] = edit_error_user_message(error_type)
        return _make_result(payload, tool_name=tool_name)
    except StaleContentError as e:
        # 新改动已经完整列给模型：它看到这条结果之后（下一次模型调用起）基于现在的正文改即可；
        # 同一条回复里排在后面的修改仍按旧快照判陈旧。列不全时要模型先读全文。
        if e.complete:
            ToolContext.record_read_snapshot(file_id, e.current_content, ticket=read_ticket)
        return _make_edit_error(str(e), **e.payload_fields())
    except EditFileError as e:
        # error 原样给模型（候选片段、occurrence 提示、全部失败项都在里面）；
        # error_type / user_message 给界面；edits_applied=0、mutation_applied=false
        # 明确告诉模型这次调用一处都没生效。
        return _make_edit_error(str(e), **e.payload_fields())
    except FileWriteBusyError as e:
        return _make_edit_error(str(e), error_type=EDIT_ERROR_FILE_BUSY)
    except FileWriteRecoveryError as e:
        # 原稿没能先进历史：提交前已回滚，一处都没改。
        return _make_edit_error(str(e), error_type=e.error_type, user_message=str(e))
    except NotFoundError as e:
        # 文件不在当前项目里与文件不存在对用户是同一回事（文案已统一为「文件不存在或已删除」）。
        return _make_edit_error(str(e), error_type=EDIT_ERROR_FILE_NOT_FOUND)
    except ForbiddenError as e:
        # 工具层自己的权限拒绝（不是内建的 PermissionError/OSError），都发生在写入之前。
        return _make_edit_error(str(e), error_type=EDIT_ERROR_PERMISSION_DENIED)
    except Exception as e:
        # 未分类异常可能发生在提交之后（例如台账记录），不能断言「一处都没生效」。
        return _make_edit_error(str(e), error_type=EDIT_ERROR_GENERIC, known_unapplied=False)


def _make_edit_error(
    error: str,
    *,
    error_type: str,
    known_unapplied: bool = True,
    **fields: Any,
) -> dict[str, Any]:
    """edit_file 的错误结果：模型用的 error + 稳定 error_type + 作者看的 user_message。

    known_unapplied=True 表示错误发生在写入之前，附上 edits_applied=0 /
    mutation_applied=false 让模型确知本次调用一处都没生效。
    """
    payload: dict[str, Any] = {
        "status": "error",
        "error": error,
        "error_type": error_type,
        "user_message": fields.pop("user_message", None) or edit_error_user_message(error_type),
    }
    if known_unapplied:
        payload["edits_applied"] = 0
        payload["mutation_applied"] = False
    payload.update(fields)
    return _make_mcp_payload(payload, tool_name="edit_file")


def _derive_edit_status(result: dict[str, Any]) -> str:
    """由 edit_file 的执行结果推导返回给 LLM 的 status。

    - 全部 edit 失败 → "error"（模型必须重新定位再试）
    - 部分成功部分失败 → "partial"（模型需要针对失败项补做）
    - 其余 → "success"
    """
    if not isinstance(result, dict):
        return "success"
    if coerce_bool(result.get("all_failed")):
        return "error"
    if coerce_bool(result.get("partial_success")):
        return "partial"
    # 兜底：即便上游没给汇总标志，只要有 failed_edits 也不能报纯 success
    failed_edits = result.get("failed_edits")
    if isinstance(failed_edits, list) and failed_edits:
        applied = result.get("edits_applied")
        applied_count = applied if isinstance(applied, int) else len(applied or [])
        return "partial" if applied_count else "error"
    return "success"


async def delete_file(args: dict[str, Any]) -> dict[str, Any]:
    """删除文件。"""
    return await _offload_write_tool("delete_file", _delete_file_sync, args)


def _delete_file_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Move one exact batch to trash, or return a scope confirmation without writing."""
    from services.file_trash import FileTrashService

    tool_name = "delete_file"
    context = ToolContext._get_context()
    project_id = context.get("project_id")
    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)
    has_id, has_ids = "id" in args, "ids" in args
    if has_id == has_ids:
        return _make_error("delete_file 必须且只能提供 id 或 ids", tool_name=tool_name)
    raw_ids = [args["id"]] if has_id else args["ids"]
    if not isinstance(raw_ids, list) or not raw_ids or any(
        not isinstance(item, str) or not item.strip() for item in raw_ids
    ):
        return _make_error("delete_file 需要非空文件 ID 列表", tool_name=tool_name)
    ids = list(dict.fromkeys(item.strip() for item in raw_ids))
    if len(ids) > 100:
        return _make_error("一次最多指定 100 个文件 ID；删除整个文件夹时请提供文件夹 ID", tool_name=tool_name)
    recursive = coerce_bool(args.get("recursive"))
    state = context["direct_deletions"]
    try:
        service = FileTrashService(
            ToolContext.get_session(), user_id=context.get("user_id"), project_id=project_id
        )
        # Shared across copied thread contexts; split tool calls cannot evade the per-request limit.
        with state["lock"]:
            preview = service.preview(ids, recursive=recursive)
            targets = preview["targets"]
            leaves = {item["id"] for item in targets if item["file_type"] != "folder"}
            needs_confirmation = any(item["file_type"] == "folder" for item in targets) or len(
                state["file_ids"] | leaves
            ) > 5
            if needs_confirmation:
                data = {
                    "confirmation_required": True,
                    "mutation_applied": False,
                    "preview": preview,
                    "count": preview["count"],
                    "result": "尚未删除。请作者在这张卡片确认删除范围；确认后可从回收站恢复。不要拆分调用绕过确认。",
                }
                return _make_result({"status": "success", "mutation_applied": False, "data": data}, tool_name=tool_name)
            deleted = service.delete(
                ids,
                recursive=preview["recursive"],
                source="agent_direct",
                expected_target_hash=preview["target_hash"],
            )
            state["file_ids"].update(leaves)
        return _make_result({"status": "success", "mutation_applied": True, "data": deleted}, tool_name=tool_name)
    except Exception as exc:
        return _make_error(str(exc), tool_name=tool_name)


async def rename_file(args: dict[str, Any]) -> dict[str, Any]:
    """只改文件标题（#273）：不碰正文、不新建、不删除。"""
    return await _offload_write_tool("rename_file", _rename_file_sync, args)


def _rename_file_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous rename_file implementation with an atomic recovery record."""
    tool_name = "rename_file"
    executor = ToolContext.get_executor()
    project_id = ToolContext._get_context().get("project_id")
    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    file_id = args.get("id")
    title = args.get("title")
    if not isinstance(file_id, str) or not file_id.strip():
        return _make_error("rename_file 需要文件 id", tool_name=tool_name)
    if not isinstance(title, str) or not title.strip():
        return _make_error("rename_file 需要非空的新标题", tool_name=tool_name)
    file_id, title = file_id.strip(), title.strip()
    if (pending := ToolContext.pending_empty_file_for(file_id)) is not None:
        return _make_mcp_payload(
            {
                "status": "error",
                "error": (
                    f"《{pending['title']}》正在等待 <file>…</file> 写入正文，rename_file 未执行（没有改动）。"
                    "请先在下一次回复用 <file>…</file> 写完正文（write_chapter 替换时若已传入新章名，保存正文时会自动更新标题）；"
                    "保存之后若仍需单独改文件标题，再调用 rename_file。"
                ),
                "error_type": WRITE_CHAPTER_REPLACE_PENDING_ERROR_TYPE,
                "id": file_id,
                "mutation_applied": False,
            },
            tool_name=tool_name,
        )

    try:
        data = executor.rename_with_undo(id=file_id, title=title)
        return _make_result(
            {"status": "success", "mutation_applied": data.get("mutation_applied") is True, "data": data},
            tool_name=tool_name,
        )
    except FileWriteRecoveryError as exc:
        return _write_recovery_payload(exc, tool_name=tool_name)
    except Exception as exc:
        return _make_error(str(exc), tool_name=tool_name)


async def query_files(args: dict[str, Any]) -> dict[str, Any]:
    """查询和搜索项目中的文件。"""
    return await asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, _query_files_sync, args)


def _query_files_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous query_files implementation."""
    tool_name = "query_files"
    executor = ToolContext.get_executor()
    project_id = ToolContext._get_context().get("project_id")

    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    try:
        # 不按 id、却要全文时（列表 × 全文），未显式给 limit 就收紧到 10 条：
        # 默认 50 条全文很容易顶到 TOOL_RESULT_MAX_CHARS，被截断后模型只能再读一遍。
        raw_id = args.get("id")
        has_id = isinstance(raw_id, str) and bool(raw_id.strip())
        raw_include = args.get("include_content")
        wants_full = str(args.get("response_mode") or "").strip().lower() == QUERY_FILES_RESPONSE_MODE_FULL or (
            raw_include is not None and coerce_bool(raw_include)
        )
        default_limit = (
            QUERY_FILES_FULL_MODE_DEFAULT_LIMIT if wants_full and not has_id else QUERY_FILES_DEFAULT_LIMIT
        )
        query_kwargs: dict[str, Any] = {
            "project_id": project_id,
            "query": args.get("query"),
            "file_type": args.get("file_type"),
            "file_types": args.get("file_types"),
            "parent_id": args.get("parent_id"),
            "limit": args.get("limit", default_limit),
            "offset": args.get("offset", 0),
        }

        optional_keys = ("id", "response_mode", "content_preview_chars", "include_content")
        for key in optional_keys:
            if key in args:
                value = args.get(key)
                if key == "include_content" and value is not None:
                    # 三值语义：None 表示"未指定，按 response_mode 决定"，必须原样透传；
                    # 显式传值时才强转——下游用的是 `include_content is True`，
                    # 模型传字符串 "true" 会被判成"不含正文"，语义直接反了。
                    value = coerce_bool(value)
                query_kwargs[key] = value

        range_read = has_id and wants_range(args)
        if range_read:
            # 范围读取先取全文再在这里切：切之前的整份正文用作陈旧检查的快照。
            query_kwargs["response_mode"] = QUERY_FILES_RESPONSE_MODE_FULL
            query_kwargs.pop("include_content", None)
            query_kwargs.pop("content_preview_chars", None)

        read_ticket = ToolContext.read_ticket() if has_id else None
        result = executor.query_files(**query_kwargs)
        keyword = args.get("query")
        if not has_id and isinstance(keyword, str) and keyword.strip() and isinstance(result, list):
            _attach_query_matches(executor.session, result, keyword)

        if range_read:
            rows = result if isinstance(result, list) else []
            if len(rows) == 1 and isinstance(rows[0], dict) and isinstance(rows[0].get("content"), str):
                full_row = rows[0]
                sliced = apply_content_range(full_row, args)
                result = [sliced]
                if sliced.get("content_partial"):
                    ToolContext.record_range_read_snapshot(
                        str(full_row.get("id") or ""), full_row["content"], ticket=read_ticket
                    )
                    return _make_result({"status": "success", "data": result}, tool_name=tool_name)

        payload = {"status": "success", "data": result}
        if has_id and len(json.dumps(payload, ensure_ascii=False)) <= TOOL_RESULT_MAX_CHARS:
            # 结果超过上限会被截断，模型拿不到全文：那时不记快照。
            _note_file_read(result, ticket=read_ticket)
        return _make_result(payload, tool_name=tool_name)
    except ContentRangeError as e:
        return _make_error(str(e), tool_name=tool_name)
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


# 关键词查询的命中位置：每个文件最多列这么多处，每处前后各带这么多字原文。
QUERY_MATCHES_PER_FILE = 8
QUERY_MATCH_CONTEXT_CHARS = 30


def _attach_query_matches(session: Session, rows: list[Any], keyword: str) -> None:
    """给关键词查询的每个文件附上正文里的命中数和命中处原文（逐字，可直接作 edit_file 的 old/anchor）。

    以前只返回文件名和开头预览，改一个词要把每个命中文件读一遍全文。
    """
    from models import File

    ids = [row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)]
    if not ids:
        return
    contents = dict(session.exec(select(File.id, File.content).where(File.id.in_(ids))).all())
    for row in rows:
        if not isinstance(row, dict):
            continue
        content = contents.get(row.get("id")) or ""
        positions: list[int] = []
        start = content.find(keyword)
        while start >= 0:
            positions.append(start)
            start = content.find(keyword, start + len(keyword))
        row["match_count"] = len(positions)
        row["matches"] = [
            content[max(0, pos - QUERY_MATCH_CONTEXT_CHARS) : pos + len(keyword) + QUERY_MATCH_CONTEXT_CHARS]
            for pos in positions[:QUERY_MATCHES_PER_FILE]
        ]


def _note_file_read(result: Any, *, ticket: int | None = None) -> None:
    """按 id 读到全文时记下模型看到的正文（edit_file 的陈旧检查）。作者手改差异由 crud 附在同一行里。"""
    rows = result if isinstance(result, list) else []
    if len(rows) != 1 or not isinstance(rows[0], dict):
        return
    row = rows[0]
    file_id = str(row.get("id") or "")
    content = row.get("content")
    if file_id and isinstance(content, str) and not row.get("content_truncated"):
        ToolContext.record_read_snapshot(file_id, content, ticket=ticket)


# 剧本项目按「集」记进度（「已写至第3集」），小说 / 短篇按「章」。
_PHASE_CN_CHAPTER_PATTERN = re.compile(r"第\s*([零一二三四五六七八九十百千\d]+)\s*[章集]")
_PHASE_EN_CHAPTER_PATTERN = re.compile(r"\bchapter\s+(\d+)\b", re.IGNORECASE)


def _normalize_update_project_args(args: dict[str, Any]) -> dict[str, Any]:
    """Normalize update_project args with backward-compatible alias mapping."""
    normalized = dict(args)
    for alias, canonical in PROJECT_STATUS_FIELD_ALIASES.items():
        if canonical in normalized or alias not in normalized:
            continue
        normalized[canonical] = normalized[alias]
    return normalized


def _extract_phase_chapter_number(phase_text: str | None) -> int | None:
    """Extract chapter number from phase text using chapter-only patterns."""
    text = (phase_text or "").strip()
    if not text:
        return None

    cn_match = _PHASE_CN_CHAPTER_PATTERN.search(text)
    if cn_match:
        token = cn_match.group(1).strip()
        if token.isdigit():
            parsed = int(token)
            return parsed if parsed > 0 else None
        parsed = parse_chinese_number(token)
        return parsed if parsed and parsed > 0 else None

    en_match = _PHASE_EN_CHAPTER_PATTERN.search(text)
    if en_match:
        try:
            parsed = int(en_match.group(1))
        except ValueError:
            parsed = 0
        return parsed if parsed > 0 else None

    return None


_PHASE_BODY_TRIM_CHARS = " \t\r\n\u3000"


def _suggest_auto_current_phase_from_drafts(project_id: str) -> str | None:
    """
    Suggest monotonic chapter phase text from content files.

    Rules:
    - Only infer from content-file titles with explicit sequence numbers.
    - Only count files that have a non-blank body.
    - If existing current_phase has chapter number >= inferred, keep unchanged.
    - If existing current_phase is non-empty but not chapter-like, do not override.

    注意这里必须覆盖 CONTENT_FILE_TYPES 全部类型（draft + script），
    否则短剧项目（正文是 script）永远推不出 current_phase。
    """
    if not project_id:
        return None

    session = ToolContext.get_session()
    from models import File, Project
    from models.entities import PROJECT_TYPE_SCREENPLAY

    project = session.get(Project, project_id)
    if not project:
        return None

    content_titles = session.exec(
        select(File.title).where(
            and_(
                File.project_id == project_id,
                File.file_type.in_(CONTENT_FILE_TYPES),
                File.is_deleted.is_(False),
                # 只算写了正文的章：空标题壳不能把进度推成「已写至第20章」（F-10）。
                func.length(func.trim(File.content, _PHASE_BODY_TRIM_CHARS)) > 0,
            )
        )
    ).all()
    if not content_titles:
        return None

    inferred_latest = max(
        (
            seq
            for title in content_titles
            if (seq := extract_chapter_like_sequence_number(title)) is not None
        ),
        default=None,
    )
    if inferred_latest is None or inferred_latest <= 0:
        return None

    current_phase = (project.current_phase or "").strip()
    current_phase_chapter = _extract_phase_chapter_number(current_phase)
    if current_phase_chapter is not None and current_phase_chapter >= inferred_latest:
        return None
    if current_phase and current_phase_chapter is None:
        return None

    # live run 2 场景 A：短剧项目被写成「已写至第3章」。短篇也是按章写，只有剧本用「集」。
    unit = "集" if project.project_type == PROJECT_TYPE_SCREENPLAY else "章"
    return f"已写至第{inferred_latest}{unit}"


async def update_project(
    args: dict[str, Any],
    *,
    written_artifact_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """更新项目信息和任务计划。"""
    if written_artifact_ids is None:
        return await _offload_write_tool("update_project", _update_project_sync, args)
    return await asyncio.to_thread(
        _run_sync_tool_with_owned_session_cleanup,
        _update_project_sync,
        args,
        written_artifact_ids,
    )


NOTES_FULL_NOTE = "写作约定已接近 4000 字上限，这次没有记入；请作者在 AI 记忆里精简后再记。"


def _update_project_sync(
    args: dict[str, Any],
    written_artifact_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Synchronous update_project implementation."""
    tool_name = "update_project"
    executor = ToolContext.get_executor()
    project_id = ToolContext.get_project_id()
    session_id = ToolContext.get_session_id()
    user_id = ToolContext.get_user_id()

    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    try:
        normalized_args = _normalize_update_project_args(args)
        result = {}
        from_chat = isinstance(ToolContext._get_context().get("author_message"), str)
        if (
            "notes" in normalized_args
            and from_chat
            and (
                ToolContext._get_context().get("notes_append_allowed") is not True
                # Consumed steering is shared across SDK task/context snapshots.
                # Only subsequent tool calls narrow; completed writes are not undone.
                or bool(ToolContext._get_context().get("author_steering"))
            )
        ):
            normalized_args.pop("notes")
            result["notes_skipped_reason"] = "本轮未获追加许可或已有新的作者插话；不要重做历史记住请求。"

        # 应用内对话的 notes 只追加：已有作者内容逐字节保留，新条目作为普通备注追加，
        # 结果的 notes_added 列出存下的原文，结果卡可打开统一编辑面板。
        notes_added: list[str] = []
        notes_over_limit = False
        notes_transform: Callable[[str | None], str | None] | None = None
        notes_arg = normalized_args.get("notes")
        if isinstance(notes_arg, str):
            def transform_notes(existing_notes: str | None) -> str | None:
                nonlocal notes_added, notes_over_limit
                merged = merge_project_notes(existing_notes, notes_arg, from_chat=from_chat)
                notes_added = merged.added
                notes_over_limit = merged.over_limit
                # Unchanged notes are not written, so they do not count as an updated field (no
                # billable output).  A chat call that appends nothing does not write either, even
                # to drop a legacy proposal block: every read already hides it.
                if from_chat and not merged.added:
                    return None
                return None if merged.text == (existing_notes or "") else merged.text

            notes_transform = transform_notes

        # 更新项目状态（支持空字符串清空字段）
        status_keys = ["summary", "current_phase", "writing_style", "notes"]
        has_status_update_args = (
            any(k in normalized_args for k in status_keys)
            or notes_transform is not None
        )
        # App chat/model calls never carry human rename authority.  Direct human project APIs do
        # not use this tool and keep their existing behavior.
        title_arg = normalized_args.get("title")
        has_title_arg = title_arg is not None
        title_kwargs: dict[str, Any] = {}
        if has_title_arg:
            title_kwargs["title"] = title_arg
            title_kwargs["author_requested"] = False
        if has_status_update_args or has_title_arg:
            status_kwargs: dict[str, Any] = {
                "project_id": project_id,
                "summary": normalized_args.get("summary"),
                "current_phase": normalized_args.get("current_phase"),
                "writing_style": normalized_args.get("writing_style"),
                "notes": None if notes_transform is not None else normalized_args.get("notes"),
                **title_kwargs,
            }
            if notes_transform is not None:
                status_kwargs["notes_transform"] = notes_transform
            status_result = executor.update_project_status(
                **status_kwargs,
            )
            if notes_added or notes_over_limit:
                log_with_context(
                    logger,
                    20,
                    "update_project appended chat notes entries",
                    project_id=project_id,
                    added_count=len(notes_added),
                    over_limit=notes_over_limit,
                )
            result["project_status"] = status_result
            # Backward compatibility: keep common fields at top level for older clients.
            result["project_id"] = status_result.get("project_id")
            result["updated_fields"] = status_result.get("updated_fields", [])
            result["current_status"] = status_result.get("current_status", {})
            # Also on a partial plan rejection: the other fields were still saved.
            if notes_added and "notes" in result["updated_fields"]:
                result["notes_added"] = notes_added
            if notes_over_limit:
                result["notes_note"] = NOTES_FULL_NOTE
            for key in (
                "project_name_updated",
                "project_name",
                "title_skipped",
                "title_error",
                "title_note",
            ):
                if key in status_result:
                    result[key] = status_result[key]

        # 更新任务计划（允许传空数组以清空任务板）
        plan_rejected = False
        if "tasks" in normalized_args and session_id:
            plan_kwargs: dict[str, Any] = {
                "session_id": session_id,
                "tasks": normalized_args.get("tasks", []),
                "user_id": user_id,
                "project_id": project_id,
            }
            if written_artifact_ids is not None:
                plan_kwargs["written_artifact_ids"] = written_artifact_ids
            plan_result = executor.execute_update_plan(
                **plan_kwargs,
            )
            result["plan"] = plan_result
            plan_rejected = (
                isinstance(plan_result, dict)
                and str(plan_result.get("status") or "").lower() in {"ignored", "error"}
            )

        # 任务板-only 更新是正常路径（prompt 和 workflow completion hook 都会这样调用）。
        # 这里仅做信息性记录，并尝试自动前推 current_phase（单调不回退）。
        if "tasks" in normalized_args and not has_status_update_args and not plan_rejected:
            tasks_arg = normalized_args.get("tasks", [])
            task_count = len(tasks_arg) if isinstance(tasks_arg, list) else None
            log_with_context(
                logger,
                20,  # INFO
                "update_project received task-board-only payload",
                project_id=project_id,
                session_id=session_id,
                task_count=task_count,
                arg_keys=sorted(normalized_args.keys()),
            )

            try:
                suggested_phase = _suggest_auto_current_phase_from_drafts(project_id)
                if suggested_phase:
                    status_result = executor.update_project_status(
                        project_id=project_id,
                        summary=None,
                        current_phase=suggested_phase,
                        writing_style=None,
                        notes=None,
                    )
                    result["project_status"] = status_result
                    result["project_id"] = status_result.get("project_id")
                    result["updated_fields"] = status_result.get("updated_fields", [])
                    result["current_status"] = status_result.get("current_status", {})
                    log_with_context(
                        logger,
                        20,  # INFO
                        "Auto-synced project current_phase from draft progress",
                        project_id=project_id,
                        current_phase=suggested_phase,
                    )
            except Exception as exc:
                if hasattr(executor.session, "rollback"):
                    executor.session.rollback()
                log_with_context(
                    logger,
                    40,  # ERROR
                    "Failed to auto-sync current_phase from drafts",
                    project_id=project_id,
                    session_id=session_id,
                    error=str(exc),
                )

        if plan_rejected:
            project_mutated = bool(result.get("updated_fields")) or coerce_bool(
                result.get("project_name_updated")
            )
            plan = result.get("plan") if isinstance(result.get("plan"), dict) else {}
            return _make_result(
                {
                    "status": "partial" if project_mutated else "error",
                    "error_type": "update_plan_rejected",
                    "error": str(
                        plan.get("error")
                        or plan.get("message")
                        or "任务计划板未更新"
                    ),
                    "mutation_applied": project_mutated,
                    "data": result,
                },
                tool_name=tool_name,
            )

        return _make_result({"status": "success", "data": result}, tool_name=tool_name)
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


async def load_skill(args: dict[str, Any]) -> dict[str, Any]:
    """按名称加载一个已启用技能的完整方法与资源清单（技能 L2）。"""
    return await asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, _load_skill_sync, args)


def _resolve_skill_for_tool(session: Session, tool_name: str, name: str):
    """Resolve an active skill for the current user; returns (skill, same-name skills, error_result)."""
    from agent.skills.active_skills import (
        find_active_skill,
        load_active_skills,
        same_name_skills,
        suggest_skill_names,
    )

    user_id = ToolContext.get_user_id()
    if not user_id:
        return None, [], _make_error("user_id not set", tool_name=tool_name)
    if not name:
        return None, [], _make_error("name is required", tool_name=tool_name)

    skills = load_active_skills(session, user_id)
    skill = find_active_skill(skills, name)
    if skill is None:
        available = suggest_skill_names(skills, name)
        hint = f"可用技能：{'、'.join(available)}" if available else "当前用户没有启用中的技能"
        return None, [], _make_error(f"未找到已启用的技能「{name}」。{hint}", tool_name=tool_name)
    # 按 id 查到的就是指定的那个；按名称查到时把同名的其他技能告诉模型，需要时改传 id
    others = [] if name.strip() in (skill.id, skill.added_id) else same_name_skills(skills, skill)
    return skill, others, None


def _same_name_payload(others: list) -> dict[str, Any]:
    if not others:
        return {}
    return {
        "same_name_skills": [
            {"id": other.id, "name": other.name, "description": other.description[:200]}
            for other in others
        ],
        "same_name_note": "还有同名技能；如果要用的是其中某一个，用它的 id 作为 name 重新调用。",
    }


def _skill_budget_exhausted_error(tool_name: str) -> dict[str, Any]:
    from agent.skills.content_budget import SKILL_CONTENT_TOKEN_BUDGET

    return _make_error(
        f"本轮读取的技能内容已达上限（约 {SKILL_CONTENT_TOKEN_BUDGET} tokens）。"
        "请基于已加载的技能内容完成任务；还需要更多内容时，请用户在下一条消息中继续。",
        tool_name=tool_name,
    )


def _skill_limit_error(tool_name: str, skill_name: str) -> dict[str, Any]:
    from agent.skills.content_budget import MAX_LOADED_SKILLS_PER_REQUEST

    return _make_error(
        f"本轮最多加载 {MAX_LOADED_SKILLS_PER_REQUEST} 个不同的技能，已达上限，没有加载「{skill_name}」。"
        "请用已加载的技能完成任务；还需要其他技能时，请用户在下一条消息中继续。",
        tool_name=tool_name,
    )


def _segment_fields(segment, *, skill_id: str, path: str) -> dict[str, Any]:
    """截断时告诉模型如何续读（read_skill_resource + offset）。"""
    if not segment.truncated:
        return {"truncated": False}
    return {
        "truncated": True,
        "offset": segment.offset,
        "next_offset": segment.next_offset,
        "total_chars": segment.total_chars,
        "continue_hint": (
            f"内容过长，本次只返回了第 {segment.offset}–{segment.next_offset} 个字符（共 {segment.total_chars}）。"
            f"需要后续内容时调用 read_skill_resource(name=\"{skill_id}\", path=\"{path}\", "
            f"offset={segment.next_offset}) 分段读取。"
        ),
    }


def _load_skill_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous load_skill implementation."""
    from agent.skills.active_skills import list_active_skill_resources
    from agent.skills.content_budget import SKILL_BODY_PATH

    tool_name = "load_skill"
    name = str(args.get("name") or "").strip()
    try:
        session = ToolContext.get_session()
        skill, others, error = _resolve_skill_for_tool(session, tool_name, name)
        if error is not None:
            return error

        budget = ToolContext.get_skill_content_budget()
        if not budget.admit_skill(skill.id):
            return _skill_limit_error(tool_name, skill.name)
        if budget.remaining <= 0:
            return _skill_budget_exhausted_error(tool_name)
        segment = budget.take(skill.instructions)

        resources = [
            {"path": resource.path, "size": resource.size}
            for resource in list_active_skill_resources(session, skill)
        ]

        return _make_result({
            "status": "success",
            "data": {
                "skill_id": skill.id,
                "skill_name": skill.name,
                "source": skill.source,
                "instructions": segment.text,
                **_segment_fields(segment, skill_id=skill.id, path=SKILL_BODY_PATH),
                "resources": resources,
                **_same_name_payload(others),
            },
        }, tool_name=tool_name)
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


async def read_skill_resource(args: dict[str, Any]) -> dict[str, Any]:
    """读取已启用技能的一个参考文件（技能 L3）。"""
    return await asyncio.to_thread(
        _run_sync_tool_with_owned_session_cleanup, _read_skill_resource_sync, args
    )


def _read_skill_resource_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous read_skill_resource implementation."""
    from agent.skills.active_skills import get_skill_resource, list_active_skill_resources
    from agent.skills.content_budget import SKILL_BODY_PATH
    from agent.skills.package import normalize_resource_path

    tool_name = "read_skill_resource"
    name = str(args.get("name") or "").strip()
    # 与存储时一致做 NFC 归一化，模型给出的分解形式路径也能命中
    path = normalize_resource_path(str(args.get("path") or ""))
    try:
        offset = max(int(args.get("offset") or 0), 0)
    except (TypeError, ValueError):
        return _make_error("offset must be a non-negative integer", tool_name=tool_name)
    try:
        session = ToolContext.get_session()
        skill, _others, error = _resolve_skill_for_tool(session, tool_name, name)
        if error is not None:
            return error
        if not path:
            return _make_error("path is required", tool_name=tool_name)

        if path == SKILL_BODY_PATH:
            # 技能正文（load_skill 截断后按 offset 续读）
            content = skill.instructions
            resolved_path = SKILL_BODY_PATH
        else:
            resource = get_skill_resource(
                session,
                path,
                user_skill_id=skill.user_skill_id,
                public_skill_id=skill.public_skill_id,
            )
            if resource is None:
                available = [item.path for item in list_active_skill_resources(session, skill)]
                hint = f"可用资源：{'、'.join(available)}" if available else "该技能没有资源文件"
                return _make_error(f"技能「{skill.name}」中没有资源 {path}。{hint}", tool_name=tool_name)
            content = resource.content
            resolved_path = resource.path

        if offset > len(content):
            return _make_error(
                f"offset {offset} 超出内容长度（共 {len(content)} 个字符）", tool_name=tool_name
            )
        budget = ToolContext.get_skill_content_budget()
        if not budget.admit_skill(skill.id):
            return _skill_limit_error(tool_name, skill.name)
        if budget.remaining <= 0 and offset < len(content):
            return _skill_budget_exhausted_error(tool_name)
        segment = budget.take(content, offset)

        return _make_result({
            "status": "success",
            "data": {
                "skill_id": skill.id,
                "skill_name": skill.name,
                "path": resolved_path,
                "content": segment.text,
                **_segment_fields(segment, skill_id=skill.id, path=resolved_path),
            },
        }, tool_name=tool_name)
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


# Export all tools as a list for easy registration
MCP_TOOL_HANDLERS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "create_file": create_file,
    WRITE_CHAPTER_TOOL_NAME: write_chapter,
    "edit_file": edit_file,
    "delete_file": delete_file,
    "rename_file": rename_file,
    "query_files": query_files,
    "update_project": update_project,
    "load_skill": load_skill,
    "read_skill_resource": read_skill_resource,
}

