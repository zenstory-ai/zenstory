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
from agent.tools.file_ops.content_range import DEFAULT_RANGE_CHARS, wants_range
from agent.tools.file_ops.serialization import (
    QUERY_FILES_MAX_BATCH_IDS,
    normalize_query_file_ids,
    resolve_query_files_response_mode,
)

READ_TOOL_NAME = "query_files"
# 直接调用即写文件的工具（成功后清零该文件的读取计数）。
WRITE_TOOL_NAMES: frozenset[str] = frozenset({"create_file", "write_chapter", "edit_file", "delete_file", "rename_file"})

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
    mode = resolve_query_files_response_mode(
        params.get("response_mode"),
        file_id=str(params.get("id") or ""),
        file_ids=file_ids,
        include_content=None if include_raw is None else coerce_bool(include_raw),
        content_preview_chars=preview_chars,
        query=normalized_query,
    )
    if str(mode).strip().lower() == "full":
        return "full"
    if normalized_query:
        return f"search:{normalized_query}"
    return f"summary:{preview_chars if preview_chars is not None else 'default'}"


def _read_keys_and_truncated(params: Any) -> tuple[list[ReadKey], tuple[str, ...]]:
    """提取 query_files(id / ids) 对应的全部 ReadKey（去重、上限 5，保持请求顺序）及超限被截断的 id。"""
    if not isinstance(params, dict):
        return [], ()
    try:
        target_ids, truncated_ids = normalize_query_file_ids(
            file_id=params.get("id"),
            file_ids=params.get("ids"),
            max_ids=QUERY_FILES_MAX_BATCH_IDS,
        )
    except ValueError:
        return [], ()
    if not target_ids:
        return [], ()
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
    return key[2].startswith("full:")


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
    # 已经按「正文落库」清过读取计数的文件（ToolContext.saved_bodies_this_request 的 id）。
    _saved_bodies_seen: set[str] = field(default_factory=set)

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
    def plan(self, tool_name: str, raw_arguments: str) -> ReadPlan:
        self._forget_newly_saved_bodies()
        if tool_name == READ_TOOL_NAME:
            return self._plan_direct_read(raw_arguments)
        return ReadPlan(arguments=raw_arguments)

    def _plan_direct_read(self, raw_arguments: str) -> ReadPlan:
        args, parse_error, _ = parse_json_object(raw_arguments or "")
        if parse_error is not None or not isinstance(args, dict):
            return ReadPlan(arguments=raw_arguments)
        keys, truncated_ids = _read_keys_and_truncated(args)
        if not keys:
            return ReadPlan(arguments=raw_arguments)
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
                blocked_output=_blocked_error_text(
                    model_msg,
                    user_message=_blocked_user_message([self._titles.get(key[1], "") for key in blocked]),
                    tool_name=READ_TOOL_NAME,
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
            rewritten["ids"] = [key[1] for key in allowed]
            return ReadPlan(
                arguments=json.dumps(rewritten, ensure_ascii=False),
                read_key=allowed[0] if len(allowed) == 1 else None,
                read_keys=tuple(allowed),
                blocked_keys=tuple(blocked),
                truncated_ids=truncated_ids,
            )
        return ReadPlan(
            arguments=raw_arguments,
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
        what = "全文" if key[2] == "full" else ("这一段内容" if _is_range_key(key) else "摘要")
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

    # --------------------------------------------------------------- observe
    def observe(self, tool_name: str, plan: ReadPlan, output_text: str) -> str:
        """记录一次工具结果；返回交给模型的输出（重复读取时附提示）。"""
        if tool_name in WRITE_TOOL_NAMES:
            self._observe_direct_write(tool_name, plan.arguments, output_text)
            return output_text
        if tool_name == READ_TOOL_NAME and (plan.read_keys or plan.read_key is not None):
            return self._observe_direct_read(plan, output_text)
        return output_text

    def _observe_direct_read(self, plan: ReadPlan, output_text: str) -> str:
        keys = plan.read_keys or ((plan.read_key,) if plan.read_key is not None else ())
        if not keys:
            return output_text
        payload = _load_json(output_text)
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
            is_full = key[2] == "full" or _is_range_key(key)
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
        for key in [key for key in self._read_counts if key[1] == file_id]:
            del self._read_counts[key]

    def _forget_newly_saved_bodies(self) -> None:
        """StreamAdapter 落库的正文（整章替换、新建正文）不经过写工具的结果：按保存点的结构化记录清零读取计数。"""
        from agent.tools.mcp_tools import ToolContext

        for file_id in ToolContext.saved_bodies_this_request():
            if file_id not in self._saved_bodies_seen:
                self._saved_bodies_seen.add(file_id)
                self._forget_reads(file_id)

    def _observe_direct_write(self, tool_name: str, raw_arguments: str, output_text: str) -> None:
        payload = _load_json(output_text)
        if payload is None:
            return
        status = payload.get("status")
        if payload.get("error_type") == "stale_content":
            # 文件在模型读过之后被别人（多半是作者）改了：再读是在看新内容，不算重复，也不能提示「之后未被修改」。
            args, parse_error, _ = parse_json_object(raw_arguments or "")
            file_id = str(payload.get("id") or ("" if parse_error is not None else args.get("id") or ""))
            self._forget_reads(file_id)
            return
        # edit_file 部分成功（status=partial）时，已生效的几处确实改了文件：
        # 和计费（StreamBillingTracker 把 partial 当作写入）保持同一口径。
        applied_partial = status == "partial" and payload.get("mutation_applied") is True
        if status != "success" and not applied_partial:
            return
        data = payload.get("data")
        if isinstance(data, dict) and data.get("all_failed"):
            return
        args, parse_error, _ = parse_json_object(raw_arguments or "")
        if parse_error is not None:
            args = {}
        file_id = str((data.get("id") if isinstance(data, dict) else None) or args.get("id") or "")
        title = str((data.get("title") if isinstance(data, dict) else None) or args.get("title") or "")
        if payload.get("mutation_applied") is False:
            # 成功但什么都没改（已有章节 action=exists、同名 rename、整章替换的定位）：不是写入，只记目标。
            # 整章替换真正落库由 StreamAdapter 的保存点记进 ToolContext.saved_bodies_this_request。
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
        is_full = key[2] == "full" or _is_range_key(key)
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
        for key in [key for key in self._read_counts if key[1] == file_id]:
            del self._read_counts[key]
        self.files_written[file_id] = self._titles.get(file_id, title)

    def _hint_for(self, key: ReadKey, count: int) -> str | None:
        if key[2] == "full":
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

    def _file_label(self, file_id: str) -> str:
        title = self._titles.get(file_id)
        return f"《{title}》(id={file_id})" if title else f"(id={file_id})"

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
