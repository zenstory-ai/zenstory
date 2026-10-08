"""Request-level guard against re-reading the same file without making progress.

工具失败熔断器（tool_failure_breaker）只数失败：模型把同一份文件以 query_files(id=…)
成功读上 30 遍，每一次都是 status=success，看起来像「在干活」，一直跑到单 agent
的 max_turns 上限。线上 10-03 以来约三分之一的 agent 轮次里同一文件被读了 4 次
以上，这部分轮次花掉了 69% 的 agent 费用。

本模块按**一次用户请求**记账（writing_graph 为整条工作流建一个，跨 agent run
共享），键为 (读工具, 文件 id, 归一化读取模式)：

- 直接调用的 query_files（带 id），以及 parallel_execute 里 type=="query_files"
  且 params.id 非空的子任务，记在同一个键上；
- 同一文件被成功写入（create_file / edit_file / delete_file，及 parallel_execute
  的 write_chapter / edit_file / delete_file 子任务）后，该文件的读取计数清零；
- 同一键第 2 次成功读取：照常返回内容，附一句「已读过、请直接用上文」的提示；
  第 3 次：内容 + 更强的警告；第 4 次起：不执行，返回 error_type="repeated_read"
  的可恢复错误；
- 一次请求内被拦下的读取累计达到 MAX_BLOCKED_READS_PER_REQUEST 次 → 判定为
  「无进展」，与熔断器走同一个出口（runner 的 tool_use_behavior）结束本轮。

同时记下本请求里完整读过 / 写过的文件（id + 标题），供 writing_graph 写进下一个
agent 的交接信息，避免交接后从头再读一遍（只记标题与 id，不带正文）。

本模块只负责记账与判定；「停下」由 runner 完成，对外事件也由 runner 发出。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from agent.openai_agents.events import parse_json_object, tool_error_text
from agent.tools.file_ops.serialization import resolve_query_files_response_mode

READ_TOOL_NAME = "query_files"
PARALLEL_TOOL_NAME = "parallel_execute"
# 直接调用即写文件的工具（成功后清零该文件的读取计数）。
WRITE_TOOL_NAMES: frozenset[str] = frozenset({"create_file", "edit_file", "delete_file"})
# parallel_execute 里会改动文件的子任务类型。
PARALLEL_WRITE_TASK_TYPES: frozenset[str] = frozenset({"write_chapter", "edit_file", "delete_file"})

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

# 指标名（agent.core.metrics 的计数器按字符串注册）。
READ_DUPLICATE_TOTAL = "agent.read.duplicate.total"
READ_BLOCKED_TOTAL = "agent.read.blocked.total"
NO_PROGRESS_STOP_TOTAL = "agent.no_progress.stop.total"

_READ_HINT_TEXT = "该文件全文本轮已读取过且之后未被修改，请直接使用上文内容。"
_SUMMARY_READ_HINT_TEXT = "该文件摘要本轮已读取过且之后未被修改，请直接使用上文内容。"

ReadKey = tuple[str, str, str]


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if isinstance(value, int | float):
        return bool(value)
    return False


def _read_mode(params: dict[str, Any]) -> str:
    """归一化读取模式：full（全文）或 summary:<预览字数>。

    与 query_files 用同一个判定（resolve_query_files_response_mode）：按 id 读取且没给
    response_mode 时默认就是全文，否则 query_files(id=X) 与 query_files(id=X, response_mode="full")
    会被当成两种读取，拦截被绕开。summary 的预览长度不同算不同的读取（模型要更长的预览不是重复）。
    """
    include_raw = params.get("include_content")
    preview = params.get("content_preview_chars")
    preview_chars = preview if isinstance(preview, int) and not isinstance(preview, bool) else None
    mode = resolve_query_files_response_mode(
        params.get("response_mode"),
        file_id=str(params.get("id") or ""),
        include_content=None if include_raw is None else _coerce_bool(include_raw),
        content_preview_chars=preview_chars,
    )
    if str(mode).strip().lower() == "full":
        return "full"
    return f"summary:{preview_chars if preview_chars is not None else 'default'}"


def _read_key(params: Any) -> ReadKey | None:
    if not isinstance(params, dict):
        return None
    file_id = str(params.get("id") or "").strip()
    if not file_id:
        return None
    return (READ_TOOL_NAME, file_id, _read_mode(params))


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
    # 直接 query_files：[key]；parallel_execute：改写后 tasks 下标 → key。
    read_keys: dict[int, ReadKey] = field(default_factory=dict)
    # parallel_execute 中被拦下的子任务说明（附在结果里告诉模型）。
    blocked_notes: tuple[str, ...] = ()
    # parallel_execute 改写后的子任务（用于写入识别 id/标题）。
    tasks: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class NoProgressTrip:
    """无进展停止时的现场。"""

    blocked_reads: int
    duplicate_reads: int
    threshold: int
    last_file: str

    def user_message(self, *, wrote_files: bool) -> str:
        billing_part = "" if wrote_files else "本轮没有修改任何文件，不扣除本次对话额度。"
        return (
            f"AI 在本轮反复读取已经读过的文件（{self.last_file} 等），重复读取已被拦下 "
            f"{self.blocked_reads} 次仍没有产出，已自动停止本轮，避免继续消耗额度。"
            f"{billing_part}"
            "请把需求说得更具体（例如指明要改哪一章、改什么），或让 AI 直接基于已读内容作答。"
        )

    def as_event_data(self, agent_type: str, *, wrote_files: bool) -> dict[str, Any]:
        from agent.core.stream_errors import tool_failure_error

        info = tool_failure_error()
        return {
            "error": self.user_message(wrote_files=wrote_files),
            # 复用「工具调用反复失败」的错误码（前端已有文案）；与熔断不同的是：
            # 本请求没有任何写入成功时可以退还额度（由 refundable 与 reason 表达）。
            "code": info.code,
            "retryable": False,
            "refundable": not wrote_files,
            "error_type": NO_PROGRESS_ERROR_TYPE,
            "reason": NO_PROGRESS_STOP_REASON,
            "agent_type": agent_type,
            "blocked_reads": self.blocked_reads,
            "duplicate_reads": self.duplicate_reads,
            "threshold": self.threshold,
        }


@dataclass
class RepeatReadGuard:
    """一次请求的重复读取守卫 + 读写台账（writing_graph 建一个，跨 agent run 共享）。

    plan() 在工具执行前同步调用（决定是否拦下、改写 parallel_execute 的子任务），
    observe() 在执行后同步调用（记账、附提示）；二者都在 tools_adapter 的
    FunctionTool 回调里，SDK 检查本轮 tool_use_behavior 之前就已生效。
    """

    max_blocked_reads: int = MAX_BLOCKED_READS_PER_REQUEST
    duplicate_reads: int = 0
    blocked_reads: int = 0
    trip: NoProgressTrip | None = None
    _read_counts: dict[ReadKey, int] = field(default_factory=dict)
    # 本请求完整读过 / 写过的文件：id → 标题（保持首次出现的顺序）。
    files_read: dict[str, str] = field(default_factory=dict)
    files_written: dict[str, str] = field(default_factory=dict)
    # 任意一次写工具成功（有些写入拿不到 id，例如结果被截断）。
    write_succeeded: bool = False
    _titles: dict[str, str] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.trip is not None

    # ------------------------------------------------------------------ plan
    def plan(self, tool_name: str, raw_arguments: str) -> ReadPlan:
        if tool_name == READ_TOOL_NAME:
            return self._plan_direct_read(raw_arguments)
        if tool_name == PARALLEL_TOOL_NAME:
            return self._plan_parallel(raw_arguments)
        return ReadPlan(arguments=raw_arguments)

    def _plan_direct_read(self, raw_arguments: str) -> ReadPlan:
        args, parse_error, _ = parse_json_object(raw_arguments or "")
        if parse_error is not None:
            return ReadPlan(arguments=raw_arguments)
        key = _read_key(args)
        if key is None:
            return ReadPlan(arguments=raw_arguments)
        if self._read_counts.get(key, 0) + 1 >= READ_BLOCK_AT:
            label = self._register_blocked(key)
            return ReadPlan(
                arguments=raw_arguments,
                blocked_output=tool_error_text(
                    self._blocked_message(label, key),
                    error_type=REPEATED_READ_ERROR_TYPE,
                    tool_name=READ_TOOL_NAME,
                ),
            )
        return ReadPlan(arguments=raw_arguments, read_keys={0: key})

    def _plan_parallel(self, raw_arguments: str) -> ReadPlan:
        args, parse_error, _ = parse_json_object(raw_arguments or "")
        tasks = args.get("tasks") if parse_error is None else None
        if not isinstance(tasks, list):
            return ReadPlan(arguments=raw_arguments)

        kept: list[Any] = []
        read_keys: dict[int, ReadKey] = {}
        blocked_notes: list[str] = []
        # 同一批里重复的读取也要算上（同批两次读同一文件，第二次就是重复）。
        pending: dict[ReadKey, int] = {}
        for task in tasks:
            key = None
            if isinstance(task, dict) and task.get("type") == READ_TOOL_NAME:
                key = _read_key(task.get("params"))
            if key is None:
                kept.append(task)
                continue
            seen = self._read_counts.get(key, 0) + pending.get(key, 0)
            if seen + 1 >= READ_BLOCK_AT:
                label = self._register_blocked(key)
                blocked_notes.append(self._blocked_message(label, key))
                continue
            pending[key] = pending.get(key, 0) + 1
            read_keys[len(kept)] = key
            kept.append(task)

        kept_dicts = tuple(task for task in kept if isinstance(task, dict))
        if not blocked_notes:
            return ReadPlan(arguments=raw_arguments, read_keys=read_keys, tasks=kept_dicts)
        if not kept:
            return ReadPlan(
                arguments=raw_arguments,
                blocked_output=tool_error_text(
                    "；".join(blocked_notes),
                    error_type=REPEATED_READ_ERROR_TYPE,
                    tool_name=PARALLEL_TOOL_NAME,
                ),
            )
        rewritten = dict(args)
        rewritten["tasks"] = kept
        return ReadPlan(
            arguments=json.dumps(rewritten, ensure_ascii=False),
            read_keys=read_keys,
            blocked_notes=tuple(blocked_notes),
            tasks=kept_dicts,
        )

    def _register_blocked(self, key: ReadKey) -> str:
        self.blocked_reads += 1
        self.duplicate_reads += 1
        _increment_metric(READ_BLOCKED_TOTAL)
        _increment_metric(READ_DUPLICATE_TOTAL)
        label = self._file_label(key[1])
        if self.trip is None and self.blocked_reads >= self.max_blocked_reads:
            self.trip = NoProgressTrip(
                blocked_reads=self.blocked_reads,
                duplicate_reads=self.duplicate_reads,
                threshold=self.max_blocked_reads,
                last_file=label,
            )
        return label

    def _blocked_message(self, label: str, key: ReadKey) -> str:
        what = "全文" if key[2] == "full" else "摘要"
        remaining = max(self.max_blocked_reads - self.blocked_reads, 0)
        tail = (
            f"再有 {remaining} 次重复读取被拦下，本轮将被自动终止。"
            if remaining
            else "重复读取次数已达上限，本轮即将终止。"
        )
        return (
            f"文件{label}的{what}本轮已读取 {READ_BLOCK_AT - 1} 次且之后未被修改，内容就在上文的工具结果里，"
            "本次读取未执行。不要再读取它：请直接根据已读内容完成任务并输出结果；"
            f"如果确实缺少信息，请停止调用工具并向用户说明缺什么。{tail}"
        )

    # --------------------------------------------------------------- observe
    def observe(self, tool_name: str, plan: ReadPlan, output_text: str) -> str:
        """记录一次工具结果；返回交给模型的输出（重复读取时附提示）。"""
        if tool_name in WRITE_TOOL_NAMES:
            self._observe_direct_write(plan.arguments, output_text)
            return output_text
        if tool_name == READ_TOOL_NAME and plan.read_keys:
            return self._observe_direct_read(plan, output_text)
        if tool_name == PARALLEL_TOOL_NAME:
            return self._observe_parallel(plan, output_text)
        return output_text

    def _observe_direct_read(self, plan: ReadPlan, output_text: str) -> str:
        payload = _load_json(output_text)
        if payload is None or payload.get("status") != "success":
            return output_text
        title = _first_file_title(payload.get("data"))
        if title is None:
            # 文件不存在 / 结果被截断成 overflow 引用：模型没真正拿到内容，不算读过。
            return output_text
        key = plan.read_keys[0]
        count = self._record_read(key, title)
        hint = self._hint_for(key, count)
        if hint is None:
            return output_text
        hinted = dict(payload)
        hinted["repeated_reads"] = count
        hinted["read_hint"] = hint
        return json.dumps(hinted, ensure_ascii=False)

    def _observe_parallel(self, plan: ReadPlan, output_text: str) -> str:
        payload = _load_json(output_text)
        if payload is None:
            return output_text
        data = payload.get("data")
        task_results = data.get("tasks") if isinstance(data, dict) else None
        hints: list[str] = []
        if payload.get("status") == "success" and isinstance(task_results, list):
            for index, task_result in enumerate(task_results):
                if not isinstance(task_result, dict) or task_result.get("status") != "completed":
                    continue
                task_type = task_result.get("type")
                planned = plan.tasks[index] if index < len(plan.tasks) else {}
                params = planned.get("params") if isinstance(planned.get("params"), dict) else {}
                result = task_result.get("result")
                result_data = result.get("data") if isinstance(result, dict) else None
                if task_type == READ_TOOL_NAME and index in plan.read_keys:
                    title = _first_file_title(result_data)
                    complete = True
                    if title is None:
                        # 子任务结果被截断成预览时拿不到标题：仍算读过（模型看到了预览），
                        # 但不记进「已读取全文」的交接台账。
                        if isinstance(result, dict) and result.get("truncated"):
                            title = ""
                            complete = False
                        else:
                            continue
                    key = plan.read_keys[index]
                    count = self._record_read(key, title, complete=complete)
                    hint = self._hint_for(key, count)
                    if hint is not None:
                        hints.append(f"{self._file_label(key[1])}：{hint}")
                elif task_type in PARALLEL_WRITE_TASK_TYPES:
                    file_id = ""
                    title = ""
                    if isinstance(result_data, dict):
                        file_id = str(result_data.get("id") or "")
                        title = str(result_data.get("title") or "")
                    file_id = file_id or str(params.get("id") or "")
                    title = title or str(params.get("title") or "")
                    self._record_write(file_id, title)
        if not hints and not plan.blocked_notes:
            return output_text
        annotated = dict(payload)
        if hints:
            annotated["read_hint"] = "；".join(hints)
        if plan.blocked_notes:
            annotated["repeated_read_blocked"] = list(plan.blocked_notes)
        return json.dumps(annotated, ensure_ascii=False)

    def _observe_direct_write(self, raw_arguments: str, output_text: str) -> None:
        payload = _load_json(output_text)
        if payload is None or payload.get("status") != "success":
            return
        data = payload.get("data")
        if isinstance(data, dict) and data.get("all_failed"):
            return
        args, parse_error, _ = parse_json_object(raw_arguments or "")
        if parse_error is not None:
            args = {}
        file_id = str((data.get("id") if isinstance(data, dict) else None) or args.get("id") or "")
        title = str((data.get("title") if isinstance(data, dict) else None) or args.get("title") or "")
        self._record_write(file_id, title)

    # ------------------------------------------------------------- bookkeeping
    def _record_read(self, key: ReadKey, title: str, *, complete: bool = True) -> int:
        count = self._read_counts.get(key, 0) + 1
        self._read_counts[key] = count
        file_id = key[1]
        if title:
            self._titles[file_id] = title
        if complete and key[2] == "full" and file_id not in self.files_read:
            self.files_read[file_id] = self._titles.get(file_id, title)
        if count >= READ_HINT_AT:
            self.duplicate_reads += 1
            _increment_metric(READ_DUPLICATE_TOTAL)
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
        base = _READ_HINT_TEXT if key[2] == "full" else _SUMMARY_READ_HINT_TEXT
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

    # ------------------------------------------------------------- handoff
    def handoff_summary(self) -> str:
        """交接给下一个 agent 的读写摘要（只有标题与 id，不带正文）。"""
        parts: list[str] = []
        if self.files_read:
            reads = "、".join(_label(file_id, title) for file_id, title in self.files_read.items())
            parts.append(f"本请求已读取全文：{reads}")
        if self.files_written:
            writes = "、".join(
                _label(file_id, self._titles.get(file_id, title))
                for file_id, title in self.files_written.items()
            )
            parts.append(f"本请求已修改：{writes}")
        if not parts:
            return ""
        # 下一个 agent 的上下文里没有这些文件的正文（工具结果不跨 agent 回放），
        # 所以不说「别读」，只要求按需每个文件读一次。
        return "；".join(parts) + "。只读取完成你的任务确实需要的文件，每个文件读一次即可，不要重复读取同一文件。"

    def completed_items(self) -> list[str]:
        """写进 handoff_packet.completed 的条目。"""
        return [
            f"已修改{_label(file_id, self._titles.get(file_id, title))}"
            for file_id, title in self.files_written.items()
        ]

    def written_file_ids(self) -> list[str]:
        return list(self.files_written)


def _label(file_id: str, title: str) -> str:
    return f"《{title}》(id={file_id})" if title else f"(id={file_id})"


def _increment_metric(name: str) -> None:
    try:
        from agent.core.metrics import get_metrics_collector

        get_metrics_collector().increment_counter(name)
    except Exception:  # pragma: no cover - 指标失败不能影响工具调用
        pass


__all__ = [
    "GUARD_ERROR_TYPES",
    "MAX_BLOCKED_READS_PER_REQUEST",
    "NO_PROGRESS_STOP_REASON",
    "NO_PROGRESS_STOP_TOTAL",
    "READ_BLOCK_AT",
    "REPEATED_READ_ERROR_TYPE",
    "NoProgressTrip",
    "ReadPlan",
    "RepeatReadGuard",
]
