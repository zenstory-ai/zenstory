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
from collections.abc import Callable, Iterable, Iterator
from typing import Any
from uuid import uuid4

from sqlalchemy import desc
from sqlmodel import Session, and_, select

from agent.constants import CONTENT_FILE_TYPES, INVENTORY_FILE_TYPES, coerce_bool
from agent.skills.content_budget import SkillContentBudget
from agent.tools.file_ops import FileToolExecutor
from agent.tools.file_ops.edit import (
    EDIT_ERROR_FILE_BUSY,
    EDIT_ERROR_FILE_NOT_FOUND,
    EDIT_ERROR_GENERIC,
    EDIT_ERROR_INVALID_EDIT,
    EDIT_ERROR_PERMISSION_DENIED,
    EditFileError,
    FileWriteBusyError,
    edit_error_user_message,
)
from agent.tools.file_ops.project import author_message_asks_rename
from agent.tools.file_ops.serialization import (
    QUERY_FILES_DEFAULT_LIMIT,
    QUERY_FILES_FULL_MODE_DEFAULT_LIMIT,
    QUERY_FILES_RESPONSE_MODE_FULL,
)
from agent.tools.permissions import NotFoundError
from utils.logger import get_logger, log_with_context
from utils.title_sequence import extract_chapter_like_sequence_number, parse_chinese_number

logger = get_logger(__name__)

DEFAULT_TOOL_RESULT_MAX_CHARS = 200_000
MIN_TOOL_RESULT_MAX_CHARS = 512
TOOL_RESULT_OVERFLOW_ACTION = "tool_result_overflow"
TOOL_RESULT_OVERFLOW_REF_PREFIX = "tool_result_overflow"
TOOL_RESULT_OVERFLOW_SCHEMA_VERSION = 1
TOOL_RESULT_OVERFLOW_PREVIEW_CHARS = 180
TOOL_RESULT_OVERFLOW_BACKFILL_LIMIT = 3
STANDARD_HANDOFF_ARTIFACT_ACTIONS = (
    "create_file",
    "edit_file",
    "delete_file",
    "update_project",
)

PROJECT_STATUS_FIELD_ALIASES: dict[str, str] = {
    "currentPhase": "current_phase",
    "writingStyle": "writing_style",
    "projectSummary": "summary",
}


def _should_offload_tool_execution() -> bool:
    """工具的同步实现是否必须放到线程池里执行。

    判据是"这段代码本身是不是 CPU/IO 密集"，与数据库类型**无关**：

    - 所有 ``*_sync`` 实现都做阻塞式 DB 读写。SQLite 还配了
      ``PRAGMA busy_timeout=30000``，写锁被别人占住时单次 commit 最长阻塞 30 秒。
    - ``_edit_file_sync`` 会对整章正文做近似匹配扫描与 difflib 比对（纯 CPU），
      并以**阻塞方式**获取按 file_id 分条带的 ``threading.Lock``；而该锁的另一个
      持有者 ``stream_adapter._save_file_content`` 本来就跑在
      ``asyncio.to_thread`` 的工作线程里。
    - ``_hybrid_search_sync`` 走向量检索，同样是 CPU 密集。

    历史实现返回 ``is_postgres``，于是在项目默认的 SQLite 部署下，上面这些工作
    全部同步跑在事件循环线程上：实测单次 edit_file 可让事件循环停顿 21.93 秒，
    期间该 worker 的所有 SSE 流、HTTP 请求与心跳一起停摆；threading.Lock 也变成
    "在事件循环线程上等另一个线程释放"的死等。因此这里恒为 True。
    """
    return True


HYBRID_SEARCH_DISABLED_ERROR_TYPE = "tool_disabled"
HYBRID_SEARCH_DISABLED_MESSAGE = "检索功能未启用，请改用 query_files(query=...)，不要再调用 hybrid_search"


def _is_hybrid_search_tool_enabled() -> bool:
    """Temporary kill switch for agent-side hybrid search calls."""
    raw = os.getenv("AGENT_TOOL_HYBRID_SEARCH_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _run_sync_tool_with_owned_session_cleanup(
    sync_func: Callable[[dict[str, Any]], dict[str, Any]],
    args: dict[str, Any],
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
        return sync_func(args)
    finally:
        ToolContext._cleanup_owned_session()


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
DEFAULT_ARTIFACT_REF_LOOKBACK = 20

# 请求级别的上下文变量
# Note: Using None as default to avoid mutable default argument (B039)
_tool_context_var: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    'tool_context', default=None
)
_owned_session_var: contextvars.ContextVar[Session | None] = contextvars.ContextVar(
    'owned_session', default=None
)
_pending_empty_file_var: contextvars.ContextVar[dict[str, str] | None] = contextvars.ContextVar(
    'pending_empty_file', default=None
)

# 并发子任务的 session 隔离标记（见 ToolContext.get_session 与
# parallel_executor.execute_task）。放在工具上下文 dict 里而不是 ContextVar，
# 因为它必须随 task_ctx 的浅拷贝一起进到 asyncio.to_thread 的线程副本。
SESSION_ISOLATION_KEY = "isolated_session"


class _PendingEmptyFileState:
    """待写入空文件标记的可变持有者，随请求上下文 dict 在所有子任务间共享。

    IMPORTANT — 该标记不能存成独立 ContextVar：openai-agents SDK 会为 run loop
    以及每一次 function tool 调用各包一层 asyncio.create_task，而 create_task 只
    拷贝当前 contextvars 快照，子任务里对 ContextVar 的重绑定对父上下文与兄弟
    任务均不可见（同理 asyncio.to_thread / asyncio.gather 的子任务）。因此这里
    只能原地修改这个持有者对象——set_context 在请求主上下文把它放进
    _tool_context_var 持有的 dict 后，所有子任务的上下文副本（含
    set_current_agent、parallel_executor task_ctx 的浅拷贝）引用的都是同一个
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
    """

    __slots__ = ("_lock", "_entries", "_reservations", "_reject_count")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # file_id -> title，按插入顺序保存全部待写入空文件
        self._entries: dict[str, str] = {}
        # ticket -> title，正在建库途中（还没拿到真实 file_id）的占坑
        self._reservations: dict[str, str] = {}
        self._reject_count = 0

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

    # ---- 写入 --------------------------------------------------------------
    def add(self, file_id: str, title: str) -> None:
        with self._lock:
            self._entries[file_id] = title
            self._reject_count = 0

    def discard(self, file_id: str | None = None) -> None:
        """file_id 为 None 时清空全部，否则只摘掉指定条目。"""
        with self._lock:
            if file_id is None:
                self._entries.clear()
            else:
                self._entries.pop(file_id, None)
            self._reject_count = 0

    def clear_all(self) -> None:
        with self._lock:
            self._entries.clear()
            self._reservations.clear()
            self._reject_count = 0

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

            if self._entries:
                # Never discard unfinished artifacts merely because the model
                # repeated an invalid action. The workflow boundary owns recovery
                # (finish or roll back); forgetting the marker silently leaves an
                # empty file in the project.
                self._reject_count += 1
                return False, next(reversed(self._entries.values()))

            self._reservations[ticket] = title
            return True, None

    def bind(self, ticket: str, file_id: str, title: str) -> None:
        """占坑成功且文件已落库：把票据换成真实 file_id。"""
        with self._lock:
            self._reservations.pop(ticket, None)
            if file_id:
                self._entries[file_id] = title
                self._reject_count = 0

    def release(self, ticket: str) -> None:
        """占坑作废（创建失败 / 建出来的是文件夹）：只回滚票据。"""
        with self._lock:
            self._reservations.pop(ticket, None)


class _SkillUsageClaims:
    """本次请求里已记录过用量的技能 ID（跨子任务/线程共享，原因同 _PendingEmptyFileState）。

    一次请求里同一技能只记一次用量：显式选择（selected）的技能预先登记，
    之后模型再 load_skill 它、或多次 load_skill 同一技能都不重复记。
    """

    __slots__ = ("_lock", "_skill_ids")

    def __init__(self, skill_ids: Iterable[str] | None = None) -> None:
        self._lock = threading.Lock()
        self._skill_ids: set[str] = {str(item) for item in (skill_ids or []) if item}

    def claim(self, skill_id: str) -> bool:
        """Return True the first time a skill id is claimed in this request."""
        with self._lock:
            if skill_id in self._skill_ids:
                return False
            self._skill_ids.add(skill_id)
            return True


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
        recorded_skill_ids: Iterable[str] | None = None,
        skill_tokens_used: int = 0,
        author_message: str | None = None,
        author_steering: list[str] | None = None,
    ) -> None:
        """Set the execution context for tools (request-scoped).

        Args:
            author_message: 作者这一轮的原话（不含选中文本、上下文等拼接内容）。
                服务端据此判断「作者是否明确要求改项目名」，见 update_project。
            author_steering: 本轮运行中作者追加的消息（service 的 consumed_steering，
                同一个 list 对象，运行中会继续追加），和 author_message 一起判断。
            recorded_skill_ids: 本次请求里已记录过用量的技能（显式选择的技能），
                load_skill 不再为它们重复记用量。
            skill_tokens_used: 显式选择的技能注入 system prompt 时已占用的技能内容 token，
                从本次请求的技能内容预算里先扣掉。
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
            "skill_usage_claims": _SkillUsageClaims(recorded_skill_ids),
            "skill_content_budget": SkillContentBudget(used=skill_tokens_used),
            "author_message": author_message,
            "author_steering": author_steering,
        })
        _owned_session_var.set(None)
        _pending_empty_file_var.set(None)

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

        # 隔离标记（parallel_executor 为每个并发子任务置位）：本任务**必须**
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
            yield shared
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
    def get_current_agent(cls) -> str | None:
        """Get current agent type bound to tool execution context."""
        context = cls._get_context()
        current_agent = context.get("current_agent")
        return current_agent if isinstance(current_agent, str) and current_agent else None

    @classmethod
    def set_current_agent(cls, current_agent: str | None) -> None:
        """Update current agent in request-scoped context.

        IMPORTANT — current_agent MUST remain in the mutable contextvar (_tool_context_var).
        Do NOT move it into any frozen/immutable container (e.g. a future RunContext dataclass).
        Rationale: it is mutated mid-run on every agent transition (writing_graph.py:360),
        reset to None at run end (:755), and read at handoff time (mcp_tools.py ~1292, ~1319).
        Freezing it at run-start would leave a stale value, causing silent wrong-agent routing
        at handoff time — the handoff tool would dispatch to the *previous* agent instead of
        the intended target.
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
        _pending_empty_file_var.set(None)

    @classmethod
    def claim_skill_usage(cls, skill_id: str) -> bool:
        """True 表示本次请求第一次为该技能记用量；没有请求上下文时总是 True。"""
        context = _tool_context_var.get()
        claims = context.get("skill_usage_claims") if isinstance(context, dict) else None
        if isinstance(claims, _SkillUsageClaims):
            return claims.claim(skill_id)
        return True

    @classmethod
    def get_skill_content_budget(cls) -> SkillContentBudget:
        """本次请求共享的技能内容预算；没有请求上下文时每次给一份完整预算。"""
        context = _tool_context_var.get()
        budget = context.get("skill_content_budget") if isinstance(context, dict) else None
        if isinstance(budget, SkillContentBudget):
            return budget
        return SkillContentBudget()

    @classmethod
    def _get_pending_empty_file_state(cls) -> _PendingEmptyFileState | None:
        """获取请求上下文中跨任务共享的待写入文件持有者（未建上下文时为 None）。"""
        context = _tool_context_var.get()
        if isinstance(context, dict):
            state = context.get("pending_empty_file_state")
            if isinstance(state, _PendingEmptyFileState):
                return state
        return None

    @classmethod
    def set_pending_empty_file(cls, file_id: str, title: str) -> None:
        """标记有一个空文件等待流式写入（追加进集合，不覆盖已有条目）。"""
        state = cls._get_pending_empty_file_state()
        if state is not None:
            state.add(file_id, title)
            return
        # 未调用 set_context 的场景（如直接 set/get 的单测）退回本上下文的 ContextVar
        _pending_empty_file_var.set({"file_id": file_id, "title": title})

    @classmethod
    def clear_pending_empty_file(cls, file_id: str | None = None) -> None:
        """清除待写入文件标记。

        Args:
            file_id: 传入时只摘掉指向该文件的条目（流式写入收尾用）；
                省略表示无条件清空全部（请求/流结束时的兜底清理，以及
                writing_graph 纠偏边界的清理）。
        """
        state = cls._get_pending_empty_file_state()
        if state is not None:
            state.discard(file_id)
        if file_id is None:
            _pending_empty_file_var.set(None)
        else:
            fallback = _pending_empty_file_var.get()
            if fallback is not None and fallback.get("file_id") == file_id:
                _pending_empty_file_var.set(None)

    @classmethod
    def has_pending_empty_file(cls) -> bool:
        """检查是否有待写入的空文件。"""
        return cls.get_pending_empty_file() is not None

    @classmethod
    def get_pending_empty_file(cls) -> dict[str, str] | None:
        """获取最近一个待写入的空文件信息（与历史单槽语义保持一致）。"""
        state = cls._get_pending_empty_file_state()
        if state is not None:
            return state.latest()
        return _pending_empty_file_var.get()

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
        state = cls._get_pending_empty_file_state()
        if state is not None:
            return state.try_reserve(ticket, title)

        # 未调用 set_context 的降级路径：没有共享持有者可锁，退回单槽检查。
        pending = _pending_empty_file_var.get()
        if pending is not None:
            return False, pending.get("title")
        return True, None

    @classmethod
    def bind_pending_empty_file(cls, ticket: str, file_id: str, title: str) -> None:
        """占坑成功且文件已落库：把票据换成真实 file_id。"""
        state = cls._get_pending_empty_file_state()
        if state is not None:
            state.bind(ticket, file_id, title)
            return
        _pending_empty_file_var.set({"file_id": file_id, "title": title})

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
        需要逐个补写/逐个上报的调用方（如 writing_graph 的纠偏分支）用这个。
        """
        state = cls._get_pending_empty_file_state()
        if state is not None:
            return state.snapshot()
        fallback = _pending_empty_file_var.get()
        return [fallback] if fallback is not None else []

    @classmethod
    def get_executor(cls) -> FileToolExecutor:
        """Get a FileToolExecutor with current context."""
        session = cls.get_session()
        context = cls._get_context()
        user_id = context.get("user_id")
        return FileToolExecutor(session, user_id)

    @classmethod
    def refresh_file_inventory(cls) -> dict[str, list[dict[str, Any]]] | None:
        """刷新文件清单，用于 handoff 时获取最新文件列表。

        Uses a column-only query (no File.content load) and the same
        sequence-sort ordering as ContextAssembler._get_file_inventory so
        that the handoff inventory matches the context-block inventory.
        """
        context = cls._get_context()
        project_id = context.get("project_id")
        if project_id is None:
            return None

        from sqlmodel import select

        from models import File
        from utils.title_sequence import build_sequence_sort_key

        # 桶必须覆盖除 folder 外的**全部**实体类型：漏掉的类型会在下面的
        # `if file_type not in inventory: continue` 处被整类丢弃，Agent 交接后
        # 看不见自己刚建的 script/document 文件，于是重复创建。
        # 用共享常量而非就地硬编码，保证与 ContextAssembler 的清单同集合。
        inventory: dict[str, list[dict[str, Any]]] = {
            file_type: [] for file_type in INVENTORY_FILE_TYPES
        }

        # Column-only select: avoids loading File.content (expensive on PG TOAST).
        # 短生命周期 session：查完立即归还连接（见 short_lived_session）。
        with cls.short_lived_session() as session:
            file_rows = session.exec(
                select(
                    File.id,
                    File.title,
                    File.file_type,
                    File.order,
                    File.created_at,
                ).where(
                    File.project_id == project_id,
                    File.file_type != "folder",
                    File.is_deleted.is_(False),
                )
            ).all()

        grouped: dict[str, list[tuple[tuple, dict[str, Any]]]] = {
            key: [] for key in inventory
        }

        for file_id, title, file_type, file_order, created_at in file_rows:
            if file_type not in inventory:
                continue

            effective_order, seq_num = build_sequence_sort_key(
                file_order,
                title=title,
                file_type=file_type,
            )
            sort_key = (effective_order, seq_num, created_at, file_id)

            grouped[file_type].append(
                (
                    sort_key,
                    {
                        "id": file_id,
                        "title": title,
                        # 渲染端（writing_graph._format_file_inventory）要按类型
                        # 分桶并标注，行里必须自带 file_type，不能只靠桶名。
                        "file_type": file_type,
                        "word_count": None,
                    },
                )
            )

        for file_type, rows in grouped.items():
            if rows:
                inventory[file_type] = [
                    item for _, item in sorted(rows, key=lambda x: x[0])
                ]

        return inventory


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


def _make_mcp_payload(payload: Any, *, tool_name: str | None = None) -> dict[str, Any]:
    """Create MCP payload with unified size guardrail."""
    text = _serialize_tool_payload(payload, tool_name=tool_name)
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


def _normalize_tool_name(tool_name: str | None) -> str:
    """Normalize tool name for storage metadata."""
    normalized = str(tool_name or "").strip()
    return normalized or "unknown_tool"


def _persist_tool_result_overflow(
    *,
    tool_name: str | None,
    status: str,
    serialized_payload: str,
) -> str | None:
    """Persist oversized tool payload into artifact ledger and return overflow ref."""
    overflow_ref = f"{TOOL_RESULT_OVERFLOW_REF_PREFIX}:{uuid4().hex}"
    stored = _record_artifact_ledger(
        action=TOOL_RESULT_OVERFLOW_ACTION,
        tool_name=_normalize_tool_name(tool_name),
        artifact_refs=[overflow_ref],
        payload={
            "schema_version": TOOL_RESULT_OVERFLOW_SCHEMA_VERSION,
            "status": str(status),
            "tool_name": _normalize_tool_name(tool_name),
            "original_length": len(serialized_payload),
            "serialized_payload": serialized_payload,
        },
    )
    return overflow_ref if stored else None


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
    "overflow_ref",
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
    overflow_ref: str | None,
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
    if overflow_ref:
        compact["overflow_ref"] = overflow_ref
    for min_keep in (_BOUND_MIN_KEEP_CHARS, 0):
        bounded, _ = bound_payload_strings(compact, TOOL_RESULT_MAX_CHARS, min_keep=min_keep)
        encoded = json.dumps(bounded, ensure_ascii=False)
        if len(encoded) <= TOOL_RESULT_MAX_CHARS:
            return encoded
    return None


def _serialize_tool_payload(payload: Any, *, tool_name: str | None = None) -> str:
    """Serialize tool payload and truncate oversized results safely."""
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized) <= TOOL_RESULT_MAX_CHARS:
        return serialized

    original_length = len(serialized)
    status = _normalize_payload_status(payload)
    overflow_ref = _persist_tool_result_overflow(
        tool_name=tool_name,
        status=status,
        serialized_payload=serialized,
    )

    if status != "error":
        structured = _compact_oversized_payload(
            payload, original_length=original_length, overflow_ref=overflow_ref
        )
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
    if overflow_ref:
        truncated_payload["overflow_ref"] = overflow_ref

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
    if overflow_ref:
        minimal_payload["overflow_ref"] = overflow_ref
    if status != "error":
        minimal_payload["data"] = {"truncated": True}
    else:
        minimal_payload["error"] = "Tool result truncated"
    return json.dumps(minimal_payload, ensure_ascii=False)


def _merge_unique_refs(*groups: list[str]) -> list[str]:
    """Merge artifact refs while preserving order and removing duplicates."""
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for ref in group:
            normalized = str(ref).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
    return merged


def _get_ledger_session() -> tuple[Session, bool]:
    """
    Get session for artifact-ledger operations.

    Returns:
        (session, owns_session)
    """
    context = ToolContext._get_context()
    create_func = context.get("create_session_func")
    if callable(create_func):
        return create_func(), True
    return ToolContext.get_session(), False


def _extract_tool_artifact_refs(tool_name: str, args: dict[str, Any], result: Any) -> list[str]:
    """Extract artifact refs from successful tool outputs."""
    refs: list[str] = []

    if tool_name == "create_file" and isinstance(result, dict) or tool_name == "edit_file" and isinstance(result, dict):
        file_id = result.get("id")
        if isinstance(file_id, str):
            refs.append(file_id)
    elif tool_name == "delete_file":
        file_id = args.get("id")
        if isinstance(file_id, str):
            refs.append(file_id)
    elif tool_name == "update_project":
        context = ToolContext._get_context()
        project_id = context.get("project_id")
        if isinstance(project_id, str):
            refs.append(f"project:{project_id}")
        session_id = context.get("session_id")
        if "tasks" in args and isinstance(session_id, str) and session_id.strip():
            refs.append(f"task_board:{session_id.strip()}")

    return _merge_unique_refs(refs)


def _record_artifact_ledger(
    *,
    action: str,
    tool_name: str,
    artifact_refs: list[str],
    payload: Any | None = None,
) -> bool:
    """
    Persist artifact refs for later handoff recovery.

    Best effort: failures should never break tool success path.
    """
    refs = _merge_unique_refs(artifact_refs)
    if not refs:
        return False

    context = ToolContext._get_context()
    project_id = context.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        return False

    try:
        from models import AgentArtifactLedger
    except Exception:
        return False

    try:
        session, owns_session = _get_ledger_session()
    except Exception:
        return False

    session_id = context.get("session_id") if isinstance(context.get("session_id"), str) else None
    user_id = context.get("user_id") if isinstance(context.get("user_id"), str) else None
    payload_json: str | None = None
    if payload is not None:
        try:
            payload_json = json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            payload_json = json.dumps({"raw": str(payload)}, ensure_ascii=False)

    try:
        for artifact_ref in refs:
            session.add(
                AgentArtifactLedger(
                    project_id=project_id,
                    session_id=session_id,
                    user_id=user_id,
                    action=action,
                    tool_name=tool_name,
                    artifact_ref=artifact_ref,
                    payload=payload_json,
                )
            )
        session.commit()
        return True
    except Exception as e:
        with contextlib.suppress(Exception):
            session.rollback()
        logger.warning(
            "Failed to persist agent artifact ledger entry",
            extra={
                "project_id": project_id,
                "tool_name": tool_name,
                "action": action,
                "error": str(e),
            },
        )
        return False
    finally:
        if owns_session:
            with contextlib.suppress(Exception):
                session.close()


def _load_recent_artifact_refs_for_handoff(
    *,
    project_id: str | None,
    session_id: str | None,
    limit: int = DEFAULT_ARTIFACT_REF_LOOKBACK,
) -> list[str]:
    """Load recent artifact refs from ledger for handoff payload enrichment."""
    if not isinstance(project_id, str) or not project_id.strip():
        return []

    try:
        from models import AgentArtifactLedger
    except Exception:
        return []

    try:
        session, owns_session = _get_ledger_session()
    except Exception:
        return []

    resolved_limit = max(1, int(limit or DEFAULT_ARTIFACT_REF_LOOKBACK))
    refs: list[str] = []
    overflow_refs: list[str] = []

    try:
        if isinstance(session_id, str) and session_id.strip():
            scoped_rows = session.exec(
                select(AgentArtifactLedger.artifact_ref)
                .where(
                    and_(
                        AgentArtifactLedger.project_id == project_id,
                        AgentArtifactLedger.session_id == session_id.strip(),
                        AgentArtifactLedger.action.in_(STANDARD_HANDOFF_ARTIFACT_ACTIONS),
                    )
                )
                .order_by(desc(AgentArtifactLedger.created_at))
                .limit(resolved_limit)
            ).all()
            refs = [str(row).strip() for row in scoped_rows if str(row).strip()]

        if not refs:
            fallback_rows = session.exec(
                select(AgentArtifactLedger.artifact_ref)
                .where(
                    and_(
                        AgentArtifactLedger.project_id == project_id,
                        AgentArtifactLedger.action.in_(STANDARD_HANDOFF_ARTIFACT_ACTIONS),
                    )
                )
                .order_by(desc(AgentArtifactLedger.created_at))
                .limit(resolved_limit)
            ).all()
            refs = [str(row).strip() for row in fallback_rows if str(row).strip()]

        overflow_limit = max(1, min(resolved_limit, TOOL_RESULT_OVERFLOW_BACKFILL_LIMIT))
        if isinstance(session_id, str) and session_id.strip():
            overflow_scoped_rows = session.exec(
                select(AgentArtifactLedger.artifact_ref)
                .where(
                    and_(
                        AgentArtifactLedger.project_id == project_id,
                        AgentArtifactLedger.session_id == session_id.strip(),
                        AgentArtifactLedger.action == TOOL_RESULT_OVERFLOW_ACTION,
                    )
                )
                .order_by(desc(AgentArtifactLedger.created_at))
                .limit(overflow_limit)
            ).all()
            overflow_refs = [str(row).strip() for row in overflow_scoped_rows if str(row).strip()]

        if not overflow_refs:
            overflow_fallback_rows = session.exec(
                select(AgentArtifactLedger.artifact_ref)
                .where(
                    and_(
                        AgentArtifactLedger.project_id == project_id,
                        AgentArtifactLedger.action == TOOL_RESULT_OVERFLOW_ACTION,
                    )
                )
                .order_by(desc(AgentArtifactLedger.created_at))
                .limit(overflow_limit)
            ).all()
            overflow_refs = [str(row).strip() for row in overflow_fallback_rows if str(row).strip()]
    except Exception as e:
        with contextlib.suppress(Exception):
            session.rollback()
        logger.warning(
            "Failed to load artifact refs from ledger",
            extra={"project_id": project_id, "session_id": session_id, "error": str(e)},
        )
        return []
    finally:
        if owns_session:
            with contextlib.suppress(Exception):
                session.close()

    return _merge_unique_refs(refs, overflow_refs)


def _load_tool_result_overflow_entry(overflow_ref: str) -> dict[str, Any] | None:
    """Load persisted tool-result overflow payload by ref."""
    normalized_ref = str(overflow_ref).strip()
    if not normalized_ref.startswith(f"{TOOL_RESULT_OVERFLOW_REF_PREFIX}:"):
        return None

    context = ToolContext._get_context()
    project_id = context.get("project_id")
    if not isinstance(project_id, str) or not project_id.strip():
        return None
    project_id = project_id.strip()
    session_id = context.get("session_id") if isinstance(context.get("session_id"), str) else None

    try:
        from models import AgentArtifactLedger
    except Exception:
        return None

    try:
        session, owns_session = _get_ledger_session()
    except Exception:
        return None

    payload_row: str | None = None
    try:
        stmt = (
            select(AgentArtifactLedger.payload)
            .where(
                and_(
                    AgentArtifactLedger.project_id == project_id,
                    AgentArtifactLedger.action == TOOL_RESULT_OVERFLOW_ACTION,
                    AgentArtifactLedger.artifact_ref == normalized_ref,
                )
            )
            .order_by(
                desc(AgentArtifactLedger.created_at),
                desc(AgentArtifactLedger.id),
            )
            .limit(1)
        )
        if isinstance(session_id, str) and session_id.strip():
            stmt = stmt.where(AgentArtifactLedger.session_id == session_id.strip())

        payload_row = session.exec(stmt).first()
    except Exception as e:
        with contextlib.suppress(Exception):
            session.rollback()
        logger.warning(
            "Failed to load tool result overflow entry",
            extra={
                "project_id": project_id,
                "session_id": session_id,
                "overflow_ref": normalized_ref,
                "error": str(e),
            },
        )
        return None
    finally:
        if owns_session:
            with contextlib.suppress(Exception):
                session.close()

    if not isinstance(payload_row, str) or not payload_row.strip():
        return None

    try:
        payload = json.loads(payload_row)
    except (TypeError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict):
        return None

    schema_version = payload.get("schema_version")
    if schema_version not in (None, TOOL_RESULT_OVERFLOW_SCHEMA_VERSION):
        return None

    serialized_payload = payload.get("serialized_payload")
    if not isinstance(serialized_payload, str):
        return None

    raw_original_length = payload.get("original_length")
    try:
        original_length = int(raw_original_length)
    except (TypeError, ValueError):
        original_length = len(serialized_payload)

    return {
        "overflow_ref": normalized_ref,
        "tool_name": _normalize_tool_name(payload.get("tool_name")),
        "status": str(payload.get("status") or "success"),
        "original_length": original_length,
        "serialized_payload": serialized_payload,
    }


def _build_tool_result_overflow_backfill_entries(
    artifact_refs: list[str],
    *,
    limit: int = TOOL_RESULT_OVERFLOW_BACKFILL_LIMIT,
) -> list[dict[str, Any]]:
    """Resolve overflow refs into lightweight backfill entries."""
    backfills: list[dict[str, Any]] = []
    if not artifact_refs:
        return backfills

    for ref in artifact_refs:
        if len(backfills) >= max(0, int(limit)):
            break
        entry = _load_tool_result_overflow_entry(str(ref))
        if not entry:
            continue
        serialized_payload = entry.get("serialized_payload", "")
        preview = serialized_payload[:TOOL_RESULT_OVERFLOW_PREVIEW_CHARS]
        if len(serialized_payload) > TOOL_RESULT_OVERFLOW_PREVIEW_CHARS:
            preview = f"{preview}..."
        backfills.append({
            "overflow_ref": entry["overflow_ref"],
            "tool_name": entry["tool_name"],
            "status": entry["status"],
            "original_length": entry["original_length"],
            "preview": preview,
        })

    return backfills


def _format_tool_result_overflow_backfill_context(backfills: list[dict[str, Any]]) -> str:
    """Format overflow backfill entries into compact handoff context text."""
    if not backfills:
        return ""

    lines = ["[工具外溢引用回填]"]
    for item in backfills:
        preview = str(item.get("preview", "")).replace("\n", "\\n")
        lines.append(
            "- "
            f"{item.get('overflow_ref', '')} "
            f"(tool={item.get('tool_name', '')}, status={item.get('status', '')}, "
            f"len={item.get('original_length', 0)}): {preview}"
        )
    return "\n".join(lines)


def _is_folder_file_type(result: dict[str, Any], args: dict[str, Any]) -> bool:
    """判断刚创建的节点是否是文件夹（以执行器落库后的类型为准，回退到入参）。"""
    raw = result.get("file_type") if isinstance(result, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raw = args.get("file_type") if isinstance(args, dict) else None
    return isinstance(raw, str) and raw.strip().lower() == "folder"


def _clear_settled_pending_empty_files() -> bool:
    """落库核验待写入空文件标记，清掉正文已写入或文件已不存在的条目。

    标记只在 <file>…</file> 流式写入收尾时清除；模型改用 edit_file(op=append)
    写完正文时标记会一直留着，后续每次不带 content 的 create_file 都会被它挡回。
    用列级查询读数据库当前值（绕开 ORM 身份映射里可能陈旧的实例）；查询失败时
    保守地不清除任何条目。返回是否清掉了至少一个条目。
    """
    from models import File

    cleared = False
    for entry in ToolContext.get_pending_empty_files():
        file_id = str(entry.get("file_id") or "")
        if not file_id:
            continue
        try:
            row = ToolContext.get_session().exec(
                select(File.content, File.is_deleted).where(File.id == file_id)
            ).first()
        except Exception as e:
            logger.debug(f"Pending empty-file probe failed: {e}")
            return cleared
        if row is None or row[1] or str(row[0] or "").strip():
            ToolContext.clear_pending_empty_file(file_id)
            cleared = True
    return cleared


def _pending_empty_file_rejection(blocking_title: str | None, title: str) -> dict[str, Any]:
    """create_file 被「上一个空文件还没写正文」挡回时的错误，点名解除方法。

    error_type 让工具失败熔断器把它识别为顺序性拒绝（先做前置动作即可成功），
    不按「同一调用连续失败」熔断；pending_file_id 让模型能直接 edit_file 补写。
    """
    blocking_name = blocking_title or "未知"
    blocking_id = ""
    for entry in ToolContext.get_pending_empty_files():
        if entry.get("title") == blocking_title:
            blocking_id = str(entry.get("file_id") or "")
    id_part = f"（id={blocking_id}）" if blocking_id else ""
    edit_part = (
        f"，或调用 edit_file(id={blocking_id}, op=append) 写入正文" if blocking_id else ""
    )
    payload: dict[str, Any] = {
        "status": "error",
        "error_type": "pending_empty_file_unwritten",
        "error": (
            f"文件「{title}」未创建：上一个文件「{blocking_name}」{id_part}正文还是空的，"
            "一次只能流式写入一个文件。"
            f"下一步：先在回复里用 <file>完整内容</file> 写出「{blocking_name}」的正文{edit_part}；"
            f"写完后再调用 create_file 创建「{title}」。在此之前重复调用 create_file 只会再次被拒绝。"
        ),
    }
    if blocking_id:
        payload["pending_file_id"] = blocking_id
    return _make_mcp_payload(payload, tool_name="create_file")


async def create_file(args: dict[str, Any]) -> dict[str, Any]:
    """创建新文件。"""
    if _should_offload_tool_execution():
        # asyncio.to_thread 运行在拷贝上下文中，但 pending-empty-file 标记通过
        # 请求上下文 dict 里的共享持有者原地修改，_create_file_sync 在工作线程
        # 里设置后对主上下文同样可见，无需在此回填。
        return await asyncio.to_thread(
            _run_sync_tool_with_owned_session_cleanup, _create_file_sync, args
        )
    return _create_file_sync(args)


def _create_file_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous create_file implementation."""
    tool_name = "create_file"
    executor = ToolContext.get_executor()
    project_id = ToolContext._get_context().get("project_id")

    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    content = args.get("content", "")
    title = args.get("title", "")

    # 检查是否已有待写入的空文件（防止连续创建空文件导致内容丢失）。
    # 检查与置位必须原子完成：两者之间隔着 DB INSERT（还跨 asyncio.to_thread
    # 的线程边界），先查后置会让并发的两次空文件创建同时通过检查，各自建出一个
    # 空文件，后置位的把先置位的覆盖掉——先建的空文件从此没有任何指针。
    # 因此这里先 try_reserve 占坑，落库后再 bind 真实 file_id。
    reservation: str | None = None
    if not content:
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
            return _pending_empty_file_rejection(blocking_title, title)

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
        # 并让 writing_graph 的纠偏分支把章节正文追加到文件夹节点上。
        if reservation is not None:
            file_id = result.get("id", "")
            if file_id and not _is_folder_file_type(result, args):
                ToolContext.bind_pending_empty_file(reservation, file_id, title)
            else:
                ToolContext.release_pending_empty_file(reservation)
            reservation = None

        _record_artifact_ledger(
            action=tool_name,
            tool_name=tool_name,
            artifact_refs=_extract_tool_artifact_refs(tool_name, args, result),
            payload={"title": result.get("title"), "file_type": result.get("file_type")},
        )

        return _make_result(
            {
                "status": "success",
                "mutation_applied": result.get("mutation_applied") is True,
                "data": _elide_reused_file_content(result),
            },
            tool_name=tool_name,
        )
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)
    finally:
        # 创建失败（异常或提前 return）时必须回滚占坑，否则这个永远不会被
        # bind 的票据会把本请求后续所有空文件创建全部挡在门外。
        if reservation is not None:
            ToolContext.release_pending_empty_file(reservation)


async def edit_file(args: dict[str, Any]) -> dict[str, Any]:
    """精确编辑文件内容。"""
    if _should_offload_tool_execution():
        return await asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, _edit_file_sync, args)
    return _edit_file_sync(args)


def _edit_file_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous edit_file implementation."""
    tool_name = "edit_file"
    executor = ToolContext.get_executor()
    project_id = ToolContext._get_context().get("project_id")

    # 写工具必须运行在绑定了项目的上下文中；执行器层会进一步校验目标文件
    # 属于该项目（check_file_access_in_tool_context）。
    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

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
        )
        _record_artifact_ledger(
            action=tool_name,
            tool_name=tool_name,
            artifact_refs=_extract_tool_artifact_refs(tool_name, args, result),
            payload={"edits_applied": result.get("edits_applied")},
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
    except EditFileError as e:
        # error 原样给模型（候选片段、occurrence 提示、全部失败项都在里面）；
        # error_type / user_message 给界面；edits_applied=0、mutation_applied=false
        # 明确告诉模型这次调用一处都没生效。
        return _make_edit_error(str(e), **e.payload_fields())
    except FileWriteBusyError as e:
        return _make_edit_error(str(e), error_type=EDIT_ERROR_FILE_BUSY)
    except NotFoundError as e:
        # 文件不在当前项目里与文件不存在对用户是同一回事（文案已统一为「文件不存在或已删除」）。
        return _make_edit_error(str(e), error_type=EDIT_ERROR_FILE_NOT_FOUND)
    except PermissionError as e:
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
    if _should_offload_tool_execution():
        return await asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, _delete_file_sync, args)
    return _delete_file_sync(args)


def _delete_file_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous delete_file implementation."""
    tool_name = "delete_file"
    executor = ToolContext.get_executor()
    project_id = ToolContext._get_context().get("project_id")

    # 删除工具必须运行在绑定了项目的上下文中；执行器层会进一步校验目标文件
    # 属于该项目（check_file_access_in_tool_context）。
    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    try:
        # recursive 是本项目破坏性最强的布尔参数：判真会软删除整棵子树。
        # 模型传来的 "false"/"0"/被截断修复出来的 "fals" 在朴素真值判断下全是
        # True，历史上把"删一个文件"变成"删掉整个文件夹的所有章节"。
        recursive = coerce_bool(args.get("recursive"))
        result = executor.delete_file(
            id=args.get("id", ""),
            recursive=recursive,
        )
        _record_artifact_ledger(
            action=tool_name,
            tool_name=tool_name,
            artifact_refs=_extract_tool_artifact_refs(tool_name, args, result),
            payload={"recursive": recursive},
        )
        return _make_result(
            {"status": "success", "mutation_applied": result is True, "data": result},
            tool_name=tool_name,
        )
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


async def query_files(args: dict[str, Any]) -> dict[str, Any]:
    """查询和搜索项目中的文件。"""
    if _should_offload_tool_execution():
        return await asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, _query_files_sync, args)
    return _query_files_sync(args)


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
            "metadata_filter": args.get("metadata_filter"),
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

        try:
            result = executor.query_files(**query_kwargs)
        except TypeError as exc:
            # Backward compatibility for older executors that don't support new args yet.
            if not _is_query_files_param_mismatch(exc):
                raise
            for key in optional_keys:
                query_kwargs.pop(key, None)
            result = executor.query_files(**query_kwargs)

        return _make_result({"status": "success", "data": result}, tool_name=tool_name)
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


def _is_query_files_param_mismatch(exc: TypeError) -> bool:
    """Check if TypeError is due to unsupported query_files kwargs."""
    message = str(exc)
    if "unexpected keyword argument" not in message:
        return False
    return any(param in message for param in ("id", "response_mode", "content_preview_chars", "include_content"))


async def hybrid_search(args: dict[str, Any]) -> dict[str, Any]:
    """混合检索（向量 + 关键词融合）。"""
    if _should_offload_tool_execution():
        return await asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, _hybrid_search_sync, args)
    return _hybrid_search_sync(args)


def _hybrid_search_sync(args: dict[str, Any]) -> dict[str, Any]:
    """Synchronous hybrid_search implementation."""
    tool_name = "hybrid_search"
    project_id = ToolContext._get_context().get("project_id")

    if project_id is None:
        return _make_error("project_id not set", tool_name=tool_name)

    query = args.get("query", "")
    top_k = args.get("top_k", 10)
    entity_types = args.get("entity_types")
    min_score = args.get("min_score", 0.0)

    if not _is_hybrid_search_tool_enabled():
        log_with_context(
            logger,
            30,  # WARNING
            "Agent hybrid_search tool disabled by env",
            project_id=project_id,
            top_k=top_k,
        )
        # 关闭时它本就不在 agent 工具清单里（registry.get_agent_tools）；仍被调用
        # （历史里见过、或 parallel_execute 子任务）时返回可恢复错误并点名替代工具。
        # 以前返回 success + 空结果，模型会把「没检索到」当成「项目里没有」继续乱试。
        return _make_mcp_payload(
            {
                "status": "error",
                "error_type": HYBRID_SEARCH_DISABLED_ERROR_TYPE,
                "error": HYBRID_SEARCH_DISABLED_MESSAGE,
                "entity_types": entity_types,
                "min_score": float(min_score or 0.0),
                "query": query,
            },
            tool_name=tool_name,
        )

    executor = ToolContext.get_executor()

    try:
        result = executor.hybrid_search(
            project_id=project_id,
            query=query,
            top_k=top_k,
            entity_types=entity_types,
            min_score=min_score,
        )
        return _make_result({"status": "success", "data": result}, tool_name=tool_name)
    except Exception as e:
        log_with_context(
            logger,
            30,  # WARNING
            "Agent hybrid_search failed",
            project_id=project_id,
            error=str(e),
            error_type=type(e).__name__,
        )
        return _make_error(str(e), tool_name=tool_name)


_PHASE_CN_CHAPTER_PATTERN = re.compile(r"第\s*([零一二三四五六七八九十百千\d]+)\s*章")
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


def _suggest_auto_current_phase_from_drafts(project_id: str) -> str | None:
    """
    Suggest monotonic chapter phase text from content files.

    Rules:
    - Only infer from content-file titles with explicit sequence numbers.
    - If existing current_phase has chapter number >= inferred, keep unchanged.
    - If existing current_phase is non-empty but not chapter-like, do not override.

    注意这里必须覆盖 CONTENT_FILE_TYPES 全部类型（draft + script），
    否则短剧项目（正文是 script）永远推不出 current_phase。
    """
    if not project_id:
        return None

    session = ToolContext.get_session()
    from models import File, Project

    project = session.get(Project, project_id)
    if not project:
        return None

    content_titles = session.exec(
        select(File.title).where(
            and_(
                File.project_id == project_id,
                File.file_type.in_(CONTENT_FILE_TYPES),
                File.is_deleted.is_(False),
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

    return f"已写至第{inferred_latest}章"


def _coerce_bool_arg(value: Any) -> bool:
    """模型有时把布尔参数写成字符串（"true"）；只认明确的真值。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


async def update_project(args: dict[str, Any]) -> dict[str, Any]:
    """更新项目信息和任务计划。"""
    if _should_offload_tool_execution():
        return await asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, _update_project_sync, args)
    return _update_project_sync(args)


def _update_project_sync(args: dict[str, Any]) -> dict[str, Any]:
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

        # 更新项目状态（支持空字符串清空字段）
        status_keys = ["summary", "current_phase", "writing_style", "notes"]
        has_status_update_args = any(k in normalized_args for k in status_keys)
        # title：作品名。项目还叫默认名或 AI 自动起的名字时改名；作者起的名字只在
        # author_requested=true（作者明确要求改名）时改（见 ProjectOperations）。
        # author_requested 不只听模型的：作者这一轮的原话里得真有改名的说法
        # （author_message_asks_rename），否则按没传处理，作者起的名字照样不动。
        title_arg = normalized_args.get("title")
        has_title_arg = title_arg is not None
        title_kwargs: dict[str, Any] = {}
        if has_title_arg:
            title_kwargs["title"] = title_arg
            if _coerce_bool_arg(normalized_args.get("author_requested")):
                if any(
                    author_message_asks_rename(text) for text in ToolContext.get_author_messages()
                ):
                    title_kwargs["author_requested"] = True
                else:
                    log_with_context(
                        logger,
                        20,  # INFO
                        "update_project author_requested ignored: no rename request in author message",
                        project_id=project_id,
                        user_id=user_id,
                    )
        if has_status_update_args or has_title_arg:
            status_result = executor.update_project_status(
                project_id=project_id,
                summary=normalized_args.get("summary"),
                current_phase=normalized_args.get("current_phase"),
                writing_style=normalized_args.get("writing_style"),
                notes=normalized_args.get("notes"),
                **title_kwargs,
            )
            result["project_status"] = status_result
            # Backward compatibility: keep common fields at top level for older clients.
            result["project_id"] = status_result.get("project_id")
            result["updated_fields"] = status_result.get("updated_fields", [])
            result["current_status"] = status_result.get("current_status", {})
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
        if "tasks" in normalized_args and session_id:
            plan_result = executor.execute_update_plan(
                session_id=session_id,
                tasks=normalized_args.get("tasks", []),
                user_id=user_id,
                project_id=project_id,
            )
            result["plan"] = plan_result

        # 任务板-only 更新是正常路径（prompt 和 workflow completion hook 都会这样调用）。
        # 这里仅做信息性记录，并尝试自动前推 current_phase（单调不回退）。
        if "tasks" in normalized_args and not has_status_update_args:
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
                log_with_context(
                    logger,
                    40,  # ERROR
                    "Failed to auto-sync current_phase from drafts",
                    project_id=project_id,
                    session_id=session_id,
                    error=str(exc),
                )

        _record_artifact_ledger(
            action=tool_name,
            tool_name=tool_name,
            artifact_refs=_extract_tool_artifact_refs(tool_name, normalized_args, result),
            payload={
                "updated_fields": result.get("updated_fields", []),
                "has_plan": "plan" in result,
            },
        )

        return _make_result({"status": "success", "data": result}, tool_name=tool_name)
    except Exception as e:
        return _make_error(str(e), tool_name=tool_name)


async def handoff_to_agent(args: dict[str, Any]) -> dict[str, Any]:
    """将任务交接给另一个Agent。这是一个特殊工具，返回值会被图处理。"""
    tool_name = "handoff_to_agent"
    target_agent = str(args.get("target_agent", "")).strip().lower()
    reason = str(args.get("reason", "")).strip()
    context = args.get("context", "")
    completed = args.get("completed", [])
    todo = args.get("todo", [])
    evidence = args.get("evidence", [])
    artifact_refs = args.get("artifact_refs", [])
    tool_context = ToolContext._get_context()
    project_id = tool_context.get("project_id")
    session_id = tool_context.get("session_id")

    if target_agent not in ("planner", "hook_designer", "writer", "quality_reviewer"):
        return _make_error(f"Invalid target_agent: {target_agent}", tool_name=tool_name)

    current_agent = ToolContext.get_current_agent()
    if current_agent and target_agent == current_agent:
        return _make_error(
            f"Self handoff is not allowed: {current_agent} -> {target_agent}",
            tool_name=tool_name,
        )

    # Normalize optional structured handoff fields
    completed_list = completed if isinstance(completed, list) else []
    todo_list = todo if isinstance(todo, list) else []
    evidence_list = evidence if isinstance(evidence, list) else []
    artifact_ref_list = artifact_refs if isinstance(artifact_refs, list) else []
    recent_artifact_refs = _load_recent_artifact_refs_for_handoff(
        project_id=project_id if isinstance(project_id, str) else None,
        session_id=session_id if isinstance(session_id, str) else None,
    )
    merged_artifact_refs = _merge_unique_refs(artifact_ref_list, recent_artifact_refs)
    overflow_backfills = _build_tool_result_overflow_backfill_entries(merged_artifact_refs)
    backfill_context = _format_tool_result_overflow_backfill_context(overflow_backfills)
    context_str = str(context).strip()
    if not context_str:
        # Some models omit `context` even though downstream workflow relies on it.
        # Fallback to `reason` (and then a stable generic message) to avoid losing
        # the handoff signal (e.g. quality_reviewer should not receive the original
        # user writing request as its task).
        context_str = reason
    if not context_str:
        current_agent = ToolContext.get_current_agent() or "agent"
        context_str = f"Handoff requested: {current_agent} -> {target_agent}"
    if backfill_context:
        context_str = f"{context_str}\n\n{backfill_context}" if context_str else backfill_context

    # 返回交接信息，由图节点处理
    return _make_result({
        "status": "handoff",
        "target_agent": target_agent,
        "reason": reason,
        "context": context_str,
        "completed": [str(item) for item in completed_list if str(item).strip()],
        "todo": [str(item) for item in todo_list if str(item).strip()],
        "evidence": [str(item) for item in evidence_list if str(item).strip()],
        "artifact_refs": merged_artifact_refs,
        "overflow_backfill": overflow_backfills,
    }, tool_name=tool_name)


async def request_clarification(args: dict[str, Any]) -> dict[str, Any]:
    """请求用户澄清，触发工作流暂停并等待用户回复。"""
    tool_name = "request_clarification"
    question = str(args.get("question", "")).strip()
    context = str(args.get("context", "")).strip()
    details = args.get("details", [])

    if not question:
        return _make_error("question is required", tool_name=tool_name)

    details_list = details if isinstance(details, list) else []

    return _make_result({
        "status": "clarification_needed",
        "question": question,
        "context": context,
        "details": [str(item).strip() for item in details_list if str(item).strip()],
    }, tool_name=tool_name)


async def load_skill(args: dict[str, Any]) -> dict[str, Any]:
    """按名称加载一个已启用技能的完整方法与资源清单（技能 L2）。"""
    if _should_offload_tool_execution():
        return await asyncio.to_thread(_run_sync_tool_with_owned_session_cleanup, _load_skill_sync, args)
    return _load_skill_sync(args)


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
    from services.skill_usage_service import record_skill_usage

    tool_name = "load_skill"
    name = str(args.get("name") or "").strip()
    try:
        session = ToolContext.get_session()
        skill, others, error = _resolve_skill_for_tool(session, tool_name, name)
        if error is not None:
            return error

        budget = ToolContext.get_skill_content_budget()
        if budget.remaining <= 0:
            return _skill_budget_exhausted_error(tool_name)
        segment = budget.take(skill.instructions)

        resources = [
            {"path": resource.path, "size": resource.size}
            for resource in list_active_skill_resources(session, skill)
        ]

        # 用量统计来自 load_skill 调用本身（而不是模型自报的文本标记）；一次请求里同一技能只记一次。
        # 用独立 session 记录：失败时回滚的只是这条用量，不会波及工具/请求 session 上的待提交状态。
        project_id = ToolContext.get_project_id()
        if project_id and ToolContext.claim_skill_usage(skill.id):
            try:
                with Session(session.get_bind()) as usage_session:
                    record_skill_usage(
                        session=usage_session,
                        project_id=project_id,
                        skill_id=skill.id,
                        skill_name=skill.name,
                        skill_source=skill.source,
                        matched_trigger="load_skill",
                        confidence=1.0,
                        user_id=ToolContext.get_user_id(),
                    )
            except Exception as exc:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Failed to record skill usage for load_skill",
                    skill_id=skill.id,
                    project_id=project_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

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
    if _should_offload_tool_execution():
        return await asyncio.to_thread(
            _run_sync_tool_with_owned_session_cleanup, _read_skill_resource_sync, args
        )
    return _read_skill_resource_sync(args)


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
    "edit_file": edit_file,
    "delete_file": delete_file,
    "query_files": query_files,
    "hybrid_search": hybrid_search,
    "update_project": update_project,
    "handoff_to_agent": handoff_to_agent,
    "request_clarification": request_clarification,
    "load_skill": load_skill,
    "read_skill_resource": read_skill_resource,
}

ALL_MCP_TOOLS = list(MCP_TOOL_HANDLERS.values())
