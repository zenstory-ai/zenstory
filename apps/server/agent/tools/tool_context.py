"""Request-scoped tool execution context and shared state holders.

Holds ``ToolContext`` along with its request-scoped mutable state containers
(``_PendingEmptyFileState`` and ``_ReadSnapshots``), separated from the MCP tool
handlers in ``agent.tools.mcp_tools``.
"""

import contextlib
import contextvars
import threading
from collections.abc import Callable, Iterator
from typing import Any

from sqlmodel import Session

from agent.constants import CONTENT_FILE_TYPES
from agent.skills.content_budget import SkillContentBudget
from agent.tools.file_ops import FileToolExecutor
from utils.logger import get_logger

logger = get_logger(__name__)

# 请求级别的上下文变量
# Note: Using None as default to avoid mutable default argument (B039)
_tool_context_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "tool_context", default=None
)
_owned_session_var: contextvars.ContextVar[Session | None] = contextvars.ContextVar(
    "owned_session", default=None
)

# 工作线程的 session 隔离标记（见 ToolContext.get_session 与 _run_sync_tool_with_owned_session_cleanup）。
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

    def entry_for(self, file_id: str) -> dict[str, Any] | None:
        """file_id 当前在待写入集合里时返回 {file_id, title, created_here}，否则为 None。"""
        with self._lock:
            if file_id not in self._entries:
                return None
            return {
                "file_id": file_id,
                "title": self._entries[file_id],
                "created_here": file_id in self._versions,
            }

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
    def pending_empty_file_for(cls, file_id: str) -> dict[str, Any] | None:
        """file_id 当前是待写入正文或待整章替换的文件时返回 {file_id, title, created_here}，否则为 None。"""
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
