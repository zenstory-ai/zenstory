"""Guard against re-reading the same file without making progress.

工具失败熔断器（tool_failure_breaker）只数失败：模型把同一份文件以 query_files(id=…)
成功读上 30 遍，每一次都是 status=success，看起来像「在干活」，一直跑到单 agent
的 max_turns 上限。线上 10-03 以来约三分之一的 agent 轮次里同一文件被读了 4 次
以上，这部分轮次花掉了 69% 的 agent 费用。

单循环 agent 为一轮建一个守卫（跨 agent run 共享），但**读取计数按 agent
run 隔离**：runner 每开始一个 agent run 就调用 begin_agent_run() 清零计数。工具
结果不会回放给下一个 agent（runner 只回放纯文本），planner 读过的文件不在
writer / 审稿人的上下文里，它们各读一次是正常的，不算重复。读写台账
（files_read / files_written / write_succeeded）和被拦下的次数（blocked_reads，
无进展判定用）仍按整个请求累计。

读取计数的键为 (读工具, 文件 id, 归一化读取模式)；按范围读取（section / tail_chars /
start_char + max_chars）时，模式里带上归一化后的范围，不同窗口是不同的读取：

- 直接调用的 query_files（带 id），以及 parallel_execute 里 type=="query_files"
  且 params.id 非空的子任务，记在同一个键上；
- 同一文件被成功写入（create_file / edit_file / delete_file，及 parallel_execute
  的 write_chapter / edit_file / delete_file 子任务）后，该文件的读取计数清零；
- 同一 agent run 内同一键第 2 次成功读取：照常返回内容，附一句「本次运行中已读过、
  请直接用之前的工具结果」的提示；第 3 次：内容 + 更强的警告；第 4 次起：不执行，
  返回 error_type="repeated_read" 的可恢复错误（error 写给模型，另带一句给作者看的
  user_message）；
- 一次请求内被拦下的读取累计达到 MAX_BLOCKED_READS_PER_REQUEST 次 → 判定为
  「无进展」，与熔断器走同一个出口（runner 的 tool_use_behavior）结束本轮。

同时记下本请求里完整读过 / 写过的文件（id + 标题），供任务板收尾等按本请求的写入核对
（只记标题与 id，不带正文）。范围读取结果
content_partial=True 时只读到一段，不算读过全文。

本模块只负责记账与判定；「停下」由 runner 完成，对外事件也由 runner 发出。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent.constants import coerce_bool
from agent.openai_agents.events import parse_json_object, tool_error_text
from agent.skills.active_skills import skill_name_key
from agent.skills.package import normalize_resource_path
from agent.tools.file_ops.content_range import DEFAULT_RANGE_CHARS, wants_range
from agent.tools.file_ops.serialization import (
    QUERY_FILES_MAX_BATCH_IDS,
    normalize_query_file_ids,
    resolve_query_files_response_mode,
)

READ_TOOL_NAME = "query_files"
READ_TOOL_NAMES: frozenset[str] = frozenset({"query_files", "read_files"})
SKILL_LOAD_TOOL_NAME = "load_skill"
SKILL_RESOURCE_TOOL_NAME = "read_skill_resource"
SKILL_READ_TOOL_NAMES: frozenset[str] = frozenset({SKILL_LOAD_TOOL_NAME, SKILL_RESOURCE_TOOL_NAME})
# 直接调用即写文件的工具（成功后清零该文件的读取计数）。
WRITE_TOOL_NAMES: frozenset[str] = frozenset(
    {
        "create_file",
        "write_chapter",
        "edit_file",
        "delete_file",
        "rename_file",
        "batch_replace_files",
    }
)

# 同一键第几次成功读取开始附提示 / 强警告，第几次起拦下不执行。
READ_HINT_AT = 2
READ_WARN_AT = 3
READ_BLOCK_AT = 4
# 一次请求内被拦下的读取累计多少次判定为无进展、结束本轮。
MAX_BLOCKED_READS_PER_REQUEST = 3

REPEATED_READ_ERROR_TYPE = "repeated_read"
NO_PROGRESS_SHORT_CIRCUIT_ERROR_TYPE = "no_progress_stop"
# 熔断器不为这些错误记账：它们是本模块主动拦下的结果，由本模块自己计数。
GUARD_ERROR_TYPES: frozenset[str] = frozenset(
    {REPEATED_READ_ERROR_TYPE, NO_PROGRESS_SHORT_CIRCUIT_ERROR_TYPE}
)

# 停止原因：MESSAGE_END.stop_reason 与 ERROR 事件 data.reason 都用它，计费据此识别。
NO_PROGRESS_STOP_REASON = "no_progress"
NO_PROGRESS_ERROR_TYPE = "NoProgressStop"

# 计数只在一个 agent run 内累计，所以提示里说的「之前的工具结果」确实在当前上下文里。
_READ_HINT_TEXT = "该文件全文本次运行中已读取过且之后未被修改，请直接使用本次运行中之前的工具结果。"
_SUMMARY_READ_HINT_TEXT = "该文件摘要本次运行中已读取过且之后未被修改，请直接使用本次运行中之前的工具结果。"
_RANGE_READ_HINT_TEXT = "该文件的这一段内容本次运行中已读取过且之后未被修改，请直接使用本次运行中之前的工具结果。"
_SKILL_LOAD_HINT_TEXT = "该技能正文本次运行中已加载过，请直接使用本次运行中之前的工具结果。"
_SKILL_RESOURCE_HINT_TEXT = "该技能的这一参考内容本次运行中已读取过，请直接使用本次运行中之前的工具结果。"

# 无进展停止时给作者看的说明（ERROR 事件的 error；前端按 ERR_AGENT_NO_PROGRESS 取 i18n）。
NO_PROGRESS_USER_MESSAGE = (
    "AI 一直在翻看同样的资料，迟迟没动笔，这一轮先停下了。"
    "可以直接告诉它改哪一章、改什么，或者回复「继续」让它接着写。"
)

ReadKey = tuple[str, str, str]


def _range_part(value: Any) -> str:
    """范围参数的归一化写法：100 与 "100" 是同一个窗口；非法值原样保留（工具会报错）。"""
    if isinstance(value, bool):
        return str(value)
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return str(value).strip()


def _range_mode(params: dict[str, Any]) -> str | None:
    """按范围读取时的归一化范围（与 content_range 用同一组参数）；不是范围读取时为 None。"""
    if not wants_range(params):
        return None
    section = params.get("section")
    if section not in (None, ""):
        return f"section:{str(section).strip()}"
    tail = params.get("tail_chars")
    if tail not in (None, ""):
        return f"tail:{_range_part(tail)}"
    limit = params.get("max_chars")
    count = DEFAULT_RANGE_CHARS if limit in (None, "") else limit
    return f"chars:{_range_part(params.get('start_char'))}+{_range_part(count)}"


def _read_mode(params: dict[str, Any], *, file_ids: list[str] | None = None) -> str:
    """归一化读取模式：full（全文）、full:<范围>（按范围读取）、search:<关键词> 或 summary:<预览字数>。

    与 query_files 用同一个判定（resolve_query_files_response_mode）：按 id / ids 读取且没给
    response_mode 时默认就是全文，否则 query_files(id=X) 与 query_files(id=X, response_mode="full")
    会被当成两种读取，拦截被绕开。同时带 id 与 query 且未显式要求 full 时是文件内搜索，不同关键词
    互不冲突且不算读过全文。summary 的预览长度不同算不同的读取（模型要更长的预览不是重复）。
    """
    include_raw = params.get("include_content")
    preview = params.get("content_preview_chars")
    preview_chars = preview if isinstance(preview, int) and not isinstance(preview, bool) else None
    raw_query = params.get("query")
    normalized_query = raw_query.strip() if isinstance(raw_query, str) and raw_query.strip() else None
    raw_mode = params.get("response_mode")
    if isinstance(raw_mode, str) and raw_mode.strip().lower() == "matches":
        return f"matches:{normalized_query or ''}"
    mode = resolve_query_files_response_mode(
        raw_mode,
        file_id=str(params.get("id") or ""),
        file_ids=file_ids,
        include_content=None if include_raw is None else coerce_bool(include_raw),
        content_preview_chars=preview_chars,
        query=normalized_query,
    )
    mode_lower = str(mode).strip().lower()
    if mode_lower == "full":
        if coerce_bool(params.get("show_paragraph_stats"), default=False):
            return "full:pstats"
        return "full"
    if normalized_query:
        return f"search:{normalized_query}"
    return f"summary:{preview_chars if preview_chars is not None else 'default'}"


def _read_keys_and_truncated(
    params: Any,
    *,
    tool_name: str = READ_TOOL_NAME,
) -> tuple[list[ReadKey], tuple[str, ...]]:
    """提取 read_files / query_files(id / ids) 对应的全部 ReadKey（去重、上限 5，保持请求顺序）及超限被截断的 id。"""
    if not isinstance(params, dict):
        return [], ()
    try:
        target_ids, truncated_ids = normalize_query_file_ids(
            file_id=params.get("id") or params.get("file_id") or params.get("fileId"),
            file_ids=params.get("ids"),
            max_ids=QUERY_FILES_MAX_BATCH_IDS,
        )
    except ValueError:
        return [], ()
    if not target_ids:
        return [], ()
    if tool_name == "read_files":
        range_mode = _range_mode(params)
        if range_mode is not None:
            if len(target_ids) > 1:
                return [], ()
            return [(READ_TOOL_NAME, target_ids[0], f"full:{range_mode}")], tuple(truncated_ids)
        mode = "full:pstats" if coerce_bool(params.get("show_paragraph_stats"), default=False) else "full"
        return [(READ_TOOL_NAME, fid, mode) for fid in target_ids], tuple(truncated_ids)
    raw_mode = params.get("response_mode")
    if isinstance(raw_mode, str) and raw_mode.strip().lower() == "matches":
        mode = _read_mode(params, file_ids=target_ids)
        return [(READ_TOOL_NAME, fid, mode) for fid in target_ids], tuple(truncated_ids)
    range_mode = _range_mode(params)
    if range_mode is not None:
        if len(target_ids) > 1:
            # 批量多 id 不支持范围参数：交给工具返回校验错误，不记读取。
            return [], ()
        return [(READ_TOOL_NAME, target_ids[0], f"full:{range_mode}")], tuple(truncated_ids)
    mode = _read_mode(params, file_ids=target_ids)
    return [(READ_TOOL_NAME, fid, mode) for fid in target_ids], tuple(truncated_ids)


def _read_keys(params: Any) -> list[ReadKey]:
    """提取 query_files(id / ids) 对应的全部 ReadKey（去重、上限 5，保持请求顺序）。"""
    return _read_keys_and_truncated(params)[0]


def _read_key(params: Any) -> ReadKey | None:
    keys = _read_keys(params)
    return keys[0] if len(keys) == 1 else None


def _is_range_key(key: ReadKey) -> bool:
    return key[2].startswith("full:") and key[2] != "full:pstats"


def _is_full_key(key: ReadKey) -> bool:
    return key[2] in ("full", "full:pstats")


def _parse_skill_offset(raw: Any) -> int | None:
    if raw is None:
        return 0
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw if raw >= 0 else None
    if isinstance(raw, str):
        stripped = raw.strip()
        if not stripped:
            return None
        try:
            parsed = int(stripped)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None
    return None


def _parse_skill_read_args(tool_name: str, params: Any) -> tuple[str, str] | None:
    """提取技能只读工具的 (requested_name, normalized_mode)；参数非法时返回 None 交由工具校验。"""
    if not isinstance(params, dict):
        return None
    raw_name = params.get("name")
    if not isinstance(raw_name, str) or not raw_name.strip():
        return None
    requested_name = raw_name.strip()
    if tool_name == SKILL_LOAD_TOOL_NAME:
        return requested_name, "body"
    if tool_name == SKILL_RESOURCE_TOOL_NAME:
        raw_path = params.get("path")
        if not isinstance(raw_path, str):
            return None
        path = normalize_resource_path(raw_path)
        if not path:
            return None
        offset = _parse_skill_offset(params.get("offset"))
        if offset is None:
            return None
        return requested_name, f"{path}@{offset}"
    return None


def _is_partial(data: Any) -> bool:
    """query_files 结果的第一条只是正文的一段（范围读取）。"""
    return isinstance(data, list) and bool(data) and isinstance(data[0], dict) and data[0].get("content_partial") is True


def _load_json(text: str) -> dict[str, Any] | None:
    try:
        payload = json.loads(text) if text else None
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _first_file_title(data: Any) -> str | None:
    """query_files 成功结果里第一条文件的标题；结果为空（文件不存在）时返回 None。"""
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return str(data[0].get("title") or "")
    return None


@dataclass(frozen=True)
class ReadPlan:
    """一次工具调用的读取计划：执行用的参数，以及（若整次被拦下）直接返回的输出。"""

    arguments: str
    parsed_arguments: dict[str, Any] | None = None
    blocked_output: str | None = None
    read_key: ReadKey | None = None
    read_keys: tuple[ReadKey, ...] = ()
    blocked_keys: tuple[ReadKey, ...] = ()
    truncated_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class NoProgressTrip:
    """无进展停止时的现场。"""

    blocked_reads: int
    duplicate_reads: int
    threshold: int
    last_file: str

    def user_message(self) -> str:
        # 是否退还额度由 refundable / reason 表达，不写进这句话。
        return NO_PROGRESS_USER_MESSAGE

    def as_event_data(self, agent_type: str) -> dict[str, Any]:
        from core.error_codes import ErrorCode

        return {
            "error": self.user_message(),
            # 专用错误码：这不是「工具失败」，而且回复「继续」可以接着做。
            # 已进入模型执行的护栏停止照常计费，不退还额度。
            "code": ErrorCode.AGENT_NO_PROGRESS,
            "retryable": False,
            "refundable": False,
            "error_type": NO_PROGRESS_ERROR_TYPE,
            "reason": NO_PROGRESS_STOP_REASON,
            "agent_type": agent_type,
            "blocked_reads": self.blocked_reads,
            "duplicate_reads": self.duplicate_reads,
            "threshold": self.threshold,
            "last_file": self.last_file,
        }


@dataclass
class RepeatReadGuard:
    """一次请求的重复读取守卫 + 读写台账（单循环 agent 建一个，跨 agent run 共享）。

    plan() 在工具执行前同步调用（决定是否拦下、改写 parallel_execute 的子任务），
    observe() 在执行后同步调用（记账、附提示）；二者都在 tools_adapter 的
    FunctionTool 回调里，SDK 检查本轮 tool_use_behavior 之前就已生效。
    runner 在每个 agent run 开始时调用 begin_agent_run()：读取计数只在一个 run 内有效。
    """

    max_blocked_reads: int = MAX_BLOCKED_READS_PER_REQUEST
    duplicate_reads: int = 0
    blocked_reads: int = 0
    trip: NoProgressTrip | None = None
    _read_counts: dict[ReadKey, int] = field(default_factory=dict)
    # 本请求完整读过 / 写过的文件：id → 标题（保持首次出现的顺序）。
    files_read: dict[str, str] = field(default_factory=dict)
    files_written: dict[str, str] = field(default_factory=dict)
    # 写工具成功但没改任何东西的目标（mutation_applied=false：已有章节、同名 rename、整章替换的定位）。
    files_targeted: dict[str, str] = field(default_factory=dict)
    # 任意一次写工具成功（有些写入拿不到 id，例如结果被截断）。
    write_succeeded: bool = False
    _titles: dict[str, str] = field(default_factory=dict)
    # 技能别名与展示名映射（与项目文件 _titles / files_read 隔离）：requested_name/id → canonical skill_id。
    _skill_aliases: dict[str, str] = field(default_factory=dict)
    _skill_names: dict[str, str] = field(default_factory=dict)
    # 已经按「正文落库」清过读取计数的文件（AgentRequestContext.saved_bodies_this_request 的 id）。
    _saved_bodies_seen: set[str] = field(default_factory=set)
    # 本次请求的强类型上下文（用于读取已落库正文集合，不再查全局 ContextVar）。
    request_context: Any = field(default=None, repr=False, compare=False)

    @property
    def is_open(self) -> bool:
        return self.trip is not None

    def begin_agent_run(self) -> None:
        """新的 agent run 开始：清零读取计数，读写台账和请求级拦截次数保留。

        工具结果不跨 agent run 回放，上一个 run 读过的内容不在这个 run 的上下文里；
        沿用旧计数会让 writer / 审稿人第一次读就收到「已读过」提示，甚至被拦下。
        """
        self._read_counts.clear()

    # ------------------------------------------------------------------ plan
    def plan(
        self,
        tool_name: str,
        raw_arguments: str,
        *,
        parsed_args: dict[str, Any] | None = None,
    ) -> ReadPlan:
        self._forget_newly_saved_bodies()
        if tool_name in READ_TOOL_NAMES:
            return self._plan_direct_read(tool_name, raw_arguments, parsed_args=parsed_args)
        if tool_name in SKILL_READ_TOOL_NAMES:
            return self._plan_skill_read(tool_name, raw_arguments, parsed_args=parsed_args)
        return ReadPlan(arguments=raw_arguments, parsed_arguments=parsed_args)

    def _plan_skill_read(
        self,
        tool_name: str,
        raw_arguments: str,
        *,
        parsed_args: dict[str, Any] | None = None,
    ) -> ReadPlan:
        if parsed_args is not None:
            args = parsed_args
        else:
            parsed_obj, parse_error, _ = parse_json_object(raw_arguments or "")
            if parse_error is not None or not isinstance(parsed_obj, dict):
                return ReadPlan(arguments=raw_arguments)
            args = parsed_obj
        parsed = _parse_skill_read_args(tool_name, args)
        if parsed is None:
            return ReadPlan(arguments=raw_arguments, parsed_arguments=args)
        requested_name, mode = parsed
        alias_key = skill_name_key(requested_name)
        skill_ident = self._skill_aliases.get(alias_key, alias_key)
        key: ReadKey = (tool_name, skill_ident, mode)
        if self._read_counts.get(key, 0) + 1 >= READ_BLOCK_AT:
            label = self._register_blocked(key, display_label=self._skill_label(skill_ident))
            model_msg = self._skill_blocked_message(label, key)
            return ReadPlan(
                arguments=raw_arguments,
                parsed_arguments=args,
                blocked_output=_blocked_error_text(
                    model_msg,
                    user_message=_blocked_skill_user_message(tool_name, self._skill_names.get(skill_ident, "")),
                    tool_name=tool_name,
                ),
            )
        return ReadPlan(
            arguments=raw_arguments,
            parsed_arguments=args,
            read_key=key,
            read_keys=(key,),
        )

    def _plan_direct_read(
        self,
        tool_name: str,
        raw_arguments: str,
        *,
        parsed_args: dict[str, Any] | None = None,
    ) -> ReadPlan:
        if parsed_args is not None:
            args = parsed_args
        else:
            parsed, parse_error, _ = parse_json_object(raw_arguments or "")
            if parse_error is not None or not isinstance(parsed, dict):
                return ReadPlan(arguments=raw_arguments)
            args = parsed
        keys, truncated_ids = _read_keys_and_truncated(args, tool_name=tool_name)
        if not keys:
            return ReadPlan(arguments=raw_arguments, parsed_arguments=args)
        allowed = [key for key in keys if self._read_counts.get(key, 0) + 1 < READ_BLOCK_AT]
        blocked = [key for key in keys if self._read_counts.get(key, 0) + 1 >= READ_BLOCK_AT]
        if blocked and not allowed:
            if len(blocked) == 1:
                label = self._register_blocked(blocked[0])
                model_msg = self._blocked_message(label, blocked[0])
            else:
                labels = [self._file_label(key[1]) for key in blocked]
                label = self._register_blocked(blocked[0], display_label="、".join(labels))
                model_msg = self._blocked_message(label, blocked[0])
            extra: dict[str, Any] | None = None
            if truncated_ids:
                extra = {
                    "truncated_ids": list(truncated_ids),
                    "note": (
                        f"ids 单次最多读取 {QUERY_FILES_MAX_BATCH_IDS} 个文件；"
                        f"超出上限的 {len(truncated_ids)} 个未读文件请另起调用读取。"
                    ),
                }
            return ReadPlan(
                arguments=raw_arguments,
                parsed_arguments=args,
                blocked_output=_blocked_error_text(
                    model_msg,
                    user_message=_blocked_user_message([self._titles.get(key[1], "") for key in blocked]),
                    tool_name=tool_name,
                    extra=extra,
                ),
            )
        if blocked and allowed:
            # 批量读取里只有部分文件被拦：只查询剩余允许的文件，跳过已重复的 id（单次调用只记一次拦截），
            # 同时在 plan 上保留超限被截断的 truncated_ids，observe 时完整回传不丢未读 id。
            labels = [self._file_label(key[1]) for key in blocked]
            self._register_blocked(blocked[0], display_label="、".join(labels))
            rewritten = dict(args)
            rewritten.pop("id", None)
            rewritten.pop("file_id", None)
            rewritten.pop("fileId", None)
            rewritten["ids"] = [key[1] for key in allowed]
            return ReadPlan(
                arguments=json.dumps(rewritten, ensure_ascii=False),
                parsed_arguments=rewritten,
                read_key=allowed[0] if len(allowed) == 1 else None,
                read_keys=tuple(allowed),
                blocked_keys=tuple(blocked),
                truncated_ids=truncated_ids,
            )
        return ReadPlan(
            arguments=raw_arguments,
            parsed_arguments=args,
            read_key=allowed[0] if len(allowed) == 1 else None,
            read_keys=tuple(allowed),
            truncated_ids=truncated_ids,
        )

    def _register_blocked(self, key: ReadKey, *, display_label: str | None = None) -> str:
        self.blocked_reads += 1
        self.duplicate_reads += 1
        label = display_label or self._file_label(key[1])
        if self.trip is None and self.blocked_reads >= self.max_blocked_reads:
            self.trip = NoProgressTrip(
                blocked_reads=self.blocked_reads,
                duplicate_reads=self.duplicate_reads,
                threshold=self.max_blocked_reads,
                last_file=label,
            )
        return label

    def _blocked_message(self, label: str, key: ReadKey) -> str:
        what = "全文" if _is_full_key(key) else ("这一段内容" if _is_range_key(key) else "摘要")
        remaining = max(self.max_blocked_reads - self.blocked_reads, 0)
        tail = (
            f"再有 {remaining} 次重复读取被拦下，本轮将被自动终止。"
            if remaining
            else "重复读取次数已达上限，本轮即将终止。"
        )
        return (
            f"文件{label}的{what}本次运行中已读取 {READ_BLOCK_AT - 1} 次且之后未被修改，"
            "内容就在本次运行之前的工具结果里，本次读取未执行。"
            "不要再读取它：请直接根据已读内容完成任务并输出结果；"
            f"如果确实缺少信息，请停止调用工具并向用户说明缺什么。{tail}"
        )

    def _skill_blocked_message(self, label: str, key: ReadKey) -> str:
        remaining = max(self.max_blocked_reads - self.blocked_reads, 0)
        tail = (
            f"再有 {remaining} 次重复读取被拦下，本轮将被自动终止。"
            if remaining
            else "重复读取次数已达上限，本轮即将终止。"
        )
        if key[0] == SKILL_LOAD_TOOL_NAME:
            return (
                f"技能{label}的正文本次运行中已加载 {READ_BLOCK_AT - 1} 次，"
                "内容就在本次运行之前的工具结果里，本次调用未执行。"
                "不要再重复加载它：请直接根据已读内容完成任务并输出结果；"
                f"如果确实缺少信息，请停止调用工具并向用户说明缺什么。{tail}"
            )
        path, _, offset_str = key[2].rpartition("@")
        path_desc = f"{path} (offset={offset_str})" if offset_str and offset_str != "0" else (path or key[2])
        return (
            f"技能{label}的资源（{path_desc}）本次运行中已读取 {READ_BLOCK_AT - 1} 次，"
            "内容就在本次运行之前的工具结果里，本次读取未执行。"
            "不要再重复读取它：请直接根据已读内容完成任务并输出结果；"
            f"如果确实缺少信息，请停止调用工具并向用户说明缺什么。{tail}"
        )

    # --------------------------------------------------------------- observe
    def observe(
        self,
        tool_name: str,
        plan: ReadPlan,
        output_text: str,
        *,
        parsed_output: dict[str, Any] | None = None,
    ) -> str:
        """记录一次工具结果；返回交给模型的输出（重复读取时附提示）。"""
        if tool_name in WRITE_TOOL_NAMES:
            self._observe_direct_write(
                tool_name,
                plan.arguments,
                output_text,
                parsed_args=plan.parsed_arguments,
                parsed_output=parsed_output,
            )
            return output_text
        if tool_name in READ_TOOL_NAMES and (plan.read_keys or plan.read_key is not None):
            return self._observe_direct_read(plan, output_text, parsed_output=parsed_output)
        if tool_name in SKILL_READ_TOOL_NAMES and plan.read_key is not None:
            return self._observe_skill_read(tool_name, plan, output_text, parsed_output=parsed_output)
        return output_text

    def _observe_skill_read(
        self,
        tool_name: str,
        plan: ReadPlan,
        output_text: str,
        *,
        parsed_output: dict[str, Any] | None = None,
    ) -> str:
        payload = parsed_output if parsed_output is not None else _load_json(output_text)
        if payload is None or payload.get("status") != "success":
            return output_text
        data = payload.get("data")
        if not isinstance(data, dict):
            return output_text

        planned_key = plan.read_key
        assert planned_key is not None
        skill_id = str(data.get("skill_id") or "").strip()
        skill_name = str(data.get("skill_name") or "").strip()
        target_id = skill_id or planned_key[1]

        if tool_name == SKILL_RESOURCE_TOOL_NAME:
            raw_path = data.get("path")
            resolved_path = normalize_resource_path(raw_path) if isinstance(raw_path, str) else ""
            planned_path, _, planned_offset = planned_key[2].rpartition("@")
            final_path = resolved_path or planned_path
            parsed_offset = _parse_skill_offset(data.get("offset"))
            final_offset = str(parsed_offset) if parsed_offset is not None else (planned_offset or "0")
            mode = f"{final_path}@{final_offset}"
        else:
            mode = "body"

        if skill_id:
            if plan.parsed_arguments is not None:
                args = plan.parsed_arguments
                parse_error = None
            else:
                args, parse_error, _ = parse_json_object(plan.arguments or "")
            if parse_error is None and isinstance(args, dict):
                raw_name = args.get("name")
                if isinstance(raw_name, str) and raw_name.strip():
                    self._skill_aliases[skill_name_key(raw_name)] = skill_id
            self._skill_aliases[skill_name_key(skill_id)] = skill_id
            if skill_name:
                self._skill_names[skill_id] = skill_name

        canonical_key: ReadKey = (tool_name, target_id, mode)
        count = self._read_counts.get(canonical_key, 0) + 1
        self._read_counts[canonical_key] = count
        if count >= READ_HINT_AT:
            self.duplicate_reads += 1

        hint = self._skill_hint_for(tool_name, count)
        if hint is None:
            return output_text
        hinted = dict(payload)
        hinted["repeated_reads"] = count
        hinted["read_hint"] = hint
        return json.dumps(hinted, ensure_ascii=False, separators=(",", ":"))

    def _observe_direct_read(
        self,
        plan: ReadPlan,
        output_text: str,
        *,
        parsed_output: dict[str, Any] | None = None,
    ) -> str:
        keys = plan.read_keys or ((plan.read_key,) if plan.read_key is not None else ())
        if not keys:
            return output_text
        payload = parsed_output if parsed_output is not None else _load_json(output_text)
        if payload is None:
            return output_text

        blocked_ids = [key[1] for key in plan.blocked_keys]
        truncated_ids = list(plan.truncated_ids) if plan.blocked_keys and plan.truncated_ids else []

        if payload.get("status") != "success":
            if not blocked_ids and not truncated_ids:
                return output_text
            hinted = dict(payload)
            if blocked_ids:
                hinted["blocked_ids"] = blocked_ids
                skipped_labels = "、".join(self._file_label(fid) for fid in blocked_ids)
                hinted["read_hint"] = (
                    f"以下文件本次运行已达重复读取上限，未重复读取，请直接用之前的结果：{skipped_labels}。"
                )
            if truncated_ids and "truncated_ids" not in hinted:
                hinted["truncated_ids"] = truncated_ids
                hinted.setdefault(
                    "note",
                    f"ids 单次最多读取 {QUERY_FILES_MAX_BATCH_IDS} 个文件，已处理前 {QUERY_FILES_MAX_BATCH_IDS} 个；"
                    f"其余 {len(truncated_ids)} 个请另起调用读取。",
                )
            return json.dumps(hinted, ensure_ascii=False, separators=(",", ":"))

        data = payload.get("data")
        if not isinstance(data, list) or not data:
            # 文件不存在 / 结果被截断成最小占位：模型没真正拿到内容，不算读过。
            if not blocked_ids and not truncated_ids:
                return output_text
            hinted = dict(payload)
            if blocked_ids:
                hinted["blocked_ids"] = blocked_ids
                skipped_labels = "、".join(self._file_label(fid) for fid in blocked_ids)
                hinted["read_hint"] = (
                    f"以下文件本次运行已达重复读取上限，未重复读取，请直接用之前的结果：{skipped_labels}。"
                )
            if truncated_ids and "truncated_ids" not in hinted:
                hinted["truncated_ids"] = truncated_ids
                hinted.setdefault(
                    "note",
                    f"ids 单次最多读取 {QUERY_FILES_MAX_BATCH_IDS} 个文件，已处理前 {QUERY_FILES_MAX_BATCH_IDS} 个；"
                    f"其余 {len(truncated_ids)} 个请另起调用读取。",
                )
            return json.dumps(hinted, ensure_ascii=False, separators=(",", ":"))

        key_by_id = {key[1]: key for key in keys}
        max_count = 0
        hints: list[str] = []
        recorded_any = False
        for row in data:
            if not isinstance(row, dict):
                continue
            fid = str(row.get("id") or "").strip()
            key = key_by_id.get(fid)
            if key is None and len(keys) == 1 and len(data) == 1:
                key = keys[0]
            if key is None:
                continue
            title = str(row.get("title") or "")
            is_full = _is_full_key(key) or _is_range_key(key)
            complete = row.get("content_partial") is not True and not (
                is_full and row.get("content_truncated") is True
            )
            count = self._record_read(key, title, complete=complete)
            recorded_any = True
            if count > max_count:
                max_count = count
            hint = self._hint_for(key, count)
            if hint is not None:
                hints.append(f"{self._file_label(key[1])}：{hint}" if len(keys) > 1 else hint)

        if not recorded_any and not blocked_ids and not truncated_ids:
            return output_text

        if not hints and not blocked_ids and not truncated_ids:
            return output_text
        hinted = dict(payload)
        if max_count >= READ_HINT_AT:
            hinted["repeated_reads"] = max_count
        if blocked_ids:
            hinted["blocked_ids"] = blocked_ids
            skipped_labels = "、".join(self._file_label(fid) for fid in blocked_ids)
            hints.append(
                f"以下文件本次运行已达重复读取上限，未重复读取，请直接用之前的结果：{skipped_labels}。"
            )
        if truncated_ids and "truncated_ids" not in hinted:
            hinted["truncated_ids"] = truncated_ids
            hinted.setdefault(
                "note",
                f"ids 单次最多读取 {QUERY_FILES_MAX_BATCH_IDS} 个文件，已处理前 {QUERY_FILES_MAX_BATCH_IDS} 个；"
                f"其余 {len(truncated_ids)} 个请另起调用读取。",
            )
        if hints:
            hinted["read_hint"] = " ".join(hints)
        return json.dumps(hinted, ensure_ascii=False, separators=(",", ":"))

    def _forget_reads(self, file_id: str) -> None:
        """这个文件读过之后被别人改了：之前的读取计数作废。"""
        if not file_id:
            return
        for key in [key for key in self._read_counts if key[0] == READ_TOOL_NAME and key[1] == file_id]:
            del self._read_counts[key]

    def _forget_newly_saved_bodies(self) -> None:
        """StreamAdapter 落库的正文（整章替换、新建正文）不经过写工具的结果：按保存点的结构化记录清零读取计数。"""
        if self.request_context is None:
            return
        for file_id in self.request_context.saved_bodies_this_request():
            if file_id not in self._saved_bodies_seen:
                self._saved_bodies_seen.add(file_id)
                self._forget_reads(file_id)

    def _observe_direct_write(
        self,
        tool_name: str,
        raw_arguments: str,
        output_text: str,
        *,
        parsed_args: dict[str, Any] | None = None,
        parsed_output: dict[str, Any] | None = None,
    ) -> None:
        payload = parsed_output if parsed_output is not None else _load_json(output_text)
        if payload is None:
            return
        status = payload.get("status")
        applied_partial = status == "partial" and payload.get("mutation_applied") is True
        if tool_name == "batch_replace_files":
            data = payload.get("data")
            if isinstance(data, dict):
                # Always forget reads for any file skipped due to stale_content, even when
                # every target failed (`status="error"`) or another error preceded it.
                for skipped in data.get("skipped_files") or []:
                    if isinstance(skipped, dict) and skipped.get("reason") == "stale_content":
                        self._forget_reads(str(skipped.get("id") or ""))
                if (status == "success" or applied_partial) and payload.get("mutation_applied") is True:
                    modified = data.get("files") or []
                    recorded = False
                    for item in modified:
                        if isinstance(item, dict):
                            fid = str(item.get("id") or "").strip()
                            ftitle = str(item.get("title") or "")
                            if fid:
                                self._record_write(fid, ftitle)
                                recorded = True
                    if not recorded and int(data.get("modified_files") or 0) > 0:
                        self._record_write("", "")
            return
        if payload.get("error_type") == "stale_content":
            # 文件在模型读过之后被别人（多半是作者）改了：再读是在看新内容，不算重复，也不能提示「之后未被修改」。
            if parsed_args is not None:
                args = parsed_args
                parse_error = None
            else:
                args, parse_error, _ = parse_json_object(raw_arguments or "")
            file_id = str(
                payload.get("id")
                or ("" if parse_error is not None or not isinstance(args, dict) else args.get("id") or "")
            )
            self._forget_reads(file_id)
            return
        # edit_file 部分成功（status=partial）时，已生效的几处确实改了文件：
        # 和计费（StreamBillingTracker 把 partial 当作写入）保持同一口径。
        if status != "success" and not applied_partial:
            return
        data = payload.get("data")
        if isinstance(data, dict) and data.get("all_failed"):
            return
        if parsed_args is not None:
            args = parsed_args
        else:
            parsed, parse_error, _ = parse_json_object(raw_arguments or "")
            args = parsed if parse_error is None and isinstance(parsed, dict) else {}
        file_id = str((data.get("id") if isinstance(data, dict) else None) or args.get("id") or "")
        title = str((data.get("title") if isinstance(data, dict) else None) or args.get("title") or "")
        if payload.get("mutation_applied") is False:
            # 成功但什么都没改（已有章节 action=exists、同名 rename、整章替换的定位）：不是写入，只记目标。
            # 整章替换真正落库由 StreamAdapter 的保存点记进 AgentRequestContext.saved_bodies_this_request。
            if file_id:
                if title:
                    self._titles[file_id] = title
                self.files_targeted[file_id] = self._titles.get(file_id, title)
                if tool_name == "write_chapter":
                    # 定为待写 / 整章替换：接下来这一章会被新正文换掉，之前的读取不再代表文件内容。
                    self._forget_reads(file_id)
            return
        self._record_write(file_id, title)

    # ------------------------------------------------------------- bookkeeping
    def _record_read(self, key: ReadKey, title: str, *, complete: bool = True) -> int:
        count = self._read_counts.get(key, 0) + 1
        self._read_counts[key] = count
        file_id = key[1]
        if title:
            self._titles[file_id] = title
        is_full = _is_full_key(key) or _is_range_key(key)
        if complete and is_full and file_id not in self.files_read:
            self.files_read[file_id] = self._titles.get(file_id, title)
        if count >= READ_HINT_AT:
            self.duplicate_reads += 1
        return count

    def _record_write(self, file_id: str, title: str) -> None:
        self.write_succeeded = True
        if title and file_id:
            self._titles[file_id] = title
        if not file_id:
            return
        # 文件被改过：之后再读是在看新内容，不算重复。
        for key in [key for key in self._read_counts if key[0] == READ_TOOL_NAME and key[1] == file_id]:
            del self._read_counts[key]
        self.files_written[file_id] = self._titles.get(file_id, title)

    def _hint_for(self, key: ReadKey, count: int) -> str | None:
        if _is_full_key(key):
            base = _READ_HINT_TEXT
        elif _is_range_key(key):
            base = _RANGE_READ_HINT_TEXT
        else:
            base = _SUMMARY_READ_HINT_TEXT
        if count == READ_HINT_AT:
            return base
        if count >= READ_WARN_AT:
            return (
                f"警告：{base}这是第 {count} 次读取同一内容；"
                "下一次重复读取将被拒绝执行。请立即基于已读内容产出结果，"
                "若缺少信息请停止调用工具并向用户说明。"
            )
        return None

    def _skill_hint_for(self, tool_name: str, count: int) -> str | None:
        base = _SKILL_LOAD_HINT_TEXT if tool_name == SKILL_LOAD_TOOL_NAME else _SKILL_RESOURCE_HINT_TEXT
        if count == READ_HINT_AT:
            return base
        if count >= READ_WARN_AT:
            return (
                f"警告：{base}这是第 {count} 次读取同一内容；"
                "下一次重复读取将被拒绝执行。请立即基于已读内容产出结果，"
                "若缺少信息请停止调用工具并向用户说明。"
            )
        return None

    def _file_label(self, file_id: str) -> str:
        title = self._titles.get(file_id)
        return f"《{title}》(id={file_id})" if title else f"(id={file_id})"

    def _skill_label(self, skill_ident: str) -> str:
        name = self._skill_names.get(skill_ident)
        return f"「{name}」(id={skill_ident})" if name else f"「{skill_ident}」"

    def short_circuit_text(self, tool_name: str) -> str:
        """无进展停止后同一轮里剩下的工具调用：不执行，直接返回说明。"""
        return tool_error_text(
            "本轮已因反复读取同一文件、没有进展而停止，后续工具调用未执行。",
            error_type=NO_PROGRESS_SHORT_CIRCUIT_ERROR_TYPE,
            tool_name=tool_name,
        )

    def written_file_ids(self) -> list[str]:
        return list(self.files_written)


def _blocked_user_message(titles: list[str]) -> str:
    """被拦下的读取给作者看的一句话：不带 id，也不带写给模型的指令。"""
    named = list(dict.fromkeys(f"《{title}》" for title in titles if title))
    if not named:
        return "这个文件刚才已经读过，AI 直接用已读内容继续。"
    return f"{'、'.join(named)}刚才已经读过，AI 直接用已读内容继续。"


def _blocked_skill_user_message(tool_name: str, skill_name: str) -> str:
    """被拦下的技能读取给作者看的一句话：绝不包含技能正文、id 或模型指令。"""
    if tool_name == SKILL_LOAD_TOOL_NAME:
        if skill_name:
            return f"技能「{skill_name}」刚才已经加载过，AI 直接用已读内容继续。"
        return "这个技能刚才已经加载过，AI 直接用已读内容继续。"
    if skill_name:
        return f"技能「{skill_name}」的参考资料刚才已经读过，AI 直接用已读内容继续。"
    return "这份技能参考资料刚才已经读过，AI 直接用已读内容继续。"


def _blocked_error_text(
    model_text: str,
    *,
    user_message: str,
    tool_name: str,
    extra: dict[str, Any] | None = None,
) -> str:
    """被拦下的读取的工具结果：error 写给模型（精确），user_message 写给作者（简短）。"""
    payload = json.loads(
        tool_error_text(model_text, error_type=REPEATED_READ_ERROR_TYPE, tool_name=tool_name)
    )
    payload["user_message"] = user_message
    if extra:
        payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "GUARD_ERROR_TYPES",
    "MAX_BLOCKED_READS_PER_REQUEST",
    "NO_PROGRESS_STOP_REASON",
    "NO_PROGRESS_USER_MESSAGE",
    "READ_BLOCK_AT",
    "REPEATED_READ_ERROR_TYPE",
    "NoProgressTrip",
    "ReadPlan",
    "RepeatReadGuard",
]
