"""Request-level circuit breaker for repeated tool-call failures.

SDK 的 max_turns（AGENT_TOOL_CALL_MAX_ITERATIONS）只限制模型轮数，挡不住
「同一个调用一直失败、模型一直原样重试」：SQLite 写锁不释放时，writer 曾每隔
约 37 秒重发一次完全相同的 edit_file，持续十多分钟，每一轮都在烧 token。

熔断器按**一次用户请求**记账：writing_graph 为整条工作流建一个，传给每一次
agent run（writer → quality_reviewer → writer 的往返不会让计数归零）。
熔断判定只看工具结果本身（工具名 + 归一化参数 + 归一化错误），不依赖错误来源：
- 同一调用（同名 + 同参数）以等价错误连续失败 MAX_IDENTICAL_TOOL_FAILURES 次 → 熔断
  （调用的是不存在的工具或被只读请求拒绝时原因记为 TRIP_REASON_TOOL_NOT_FOUND，文案不说「相同参数」）；
- 本次请求累计失败 MAX_TOOL_FAILURES_PER_REQUEST 次（参数/错误各不相同的乱试）→ 熔断。
第一、二次失败照常把错误交还模型，让它有机会换参数/换方法自行恢复；
同一调用一旦成功，它的连续失败计数清零。

本模块只负责记账与判定；「停下」由 runner 在 SDK 的 tool_use_behavior 中同步完成，
对外的停止事件也由 runner 发出（见 runner._stop_run_on_control_flow_tool）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent.openai_agents.events import parse_json_object, tool_error_text

# 同一调用以等价错误连续失败多少次后熔断。取 3：第 1 次失败让模型看到错误，
# 第 2 次（原样重试）在错误里附上「别再原样重试」的提示，第 3 次仍旧失败就停。
MAX_IDENTICAL_TOOL_FAILURES = 3

# 一次请求内累计失败多少次后熔断（不要求参数/错误相同），兜住「每次换一点
# 参数但始终失败」的乱试。正常的纠错（例如 edit_file 片段不唯一后换更长片段）
# 一般一两次就能成功，10 次远高于正常恢复所需。
MAX_TOOL_FAILURES_PER_REQUEST = 10

# 这些 error_type 是「先做另一件事再来」的顺序性拒绝（例如上一个空文件正文还没
# 写，create_file 被守卫挡回）：同一调用原样重试会被同样拒绝，但只要模型先做完
# 错误里点名的前置动作就会成功，属于正常的自我纠正路径。只计入累计失败上限，
# 不参与「同一调用连续失败」判定——否则模型补写正文前多试了两次 create_file，
# 整条工作流就被 ERROR 终止，空文件纠偏轮也跑不到，只留下一个空文件。
_ORDERING_ERROR_TYPES = frozenset({"pending_empty_file_unwritten"})

# 触发原因，写进停止事件的 data.reason，供日志/测试区分。
TRIP_REASON_IDENTICAL = "repeated_identical_tool_failure"
TRIP_REASON_TOTAL = "too_many_tool_failures"
# 同一个不存在的工具被连续调用（runner._format_tool_error 记账，参数记为空串）：
# 调用根本没执行，也没有「参数」可言，单独一个原因与文案，不说成「以相同参数失败」。
TRIP_REASON_TOOL_NOT_FOUND = "repeated_unavailable_tool_call"
_TOOL_NOT_FOUND_ERROR_TYPE = "tool_not_found"
# 调用根本没执行的错误：工具不存在，或只读请求拒绝了写工具。
_UNEXECUTED_CALL_ERROR_TYPES = frozenset({_TOOL_NOT_FOUND_ERROR_TYPE, "read_only_request"})

# 对外展示的错误摘要上限（SQL 报错可能带完整语句和参数）。
_ERROR_PREVIEW_CHARS = 200
# 参与「等价错误」比较的错误文本上限。
_ERROR_FINGERPRINT_CHARS = 300

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_HEX_RE = re.compile(r"\b0x[0-9a-f]+\b", re.IGNORECASE)
_DIGITS_RE = re.compile(r"\d+")
_SPACE_RE = re.compile(r"\s+")
# SQLAlchemy 报错尾部的 "[SQL: ...] [parameters: ...] (Background on this error at: ...)"
# 对用户没有意义，参数里还可能夹着正文片段；展示时截掉。
_SQL_DETAIL_RE = re.compile(r"\s*\[SQL:.*$", re.DOTALL)


def _normalize_arguments(raw_arguments: str) -> str:
    """把工具参数归一成可比较的键：键排序的紧凑 JSON；解析失败则用原文。"""
    raw = raw_arguments or ""
    parsed, parse_error, _metadata = parse_json_object(raw)
    if parse_error is not None:
        return raw.strip()
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _error_fingerprint(payload: dict[str, Any]) -> str:
    """「等价错误」的指纹：抹掉 id、数字等每次都会变的部分。

    例如 SQLite 的 "database is locked" 报错里带着本次 UPDATE 的时间戳参数，
    逐字比较会让每次重试都算作「不同错误」而永远不熔断。
    """
    error_type = payload.get("error_type")
    message = str(payload.get("error") or "")
    text = _UUID_RE.sub("<id>", message)
    text = _HEX_RE.sub("<hex>", text)
    text = _DIGITS_RE.sub("#", text)
    text = _SPACE_RE.sub(" ", text).strip().lower()[:_ERROR_FINGERPRINT_CHARS]
    if isinstance(error_type, str) and error_type:
        return f"{error_type}:{text}"
    return text


def _parallel_failure_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    """parallel_execute 的子任务失败折算成一次失败。

    parallel_execute 无论子任务成败都返回 status=success 的外壳（逐任务明细放在
    data.tasks 里，见 parallel_executor），只看顶层 status 会让「全部子任务都撞上
    数据库锁、模型原样重发同一批任务」永远不被计数。这里以 data.any_failed 判失败，
    错误指纹取各失败子任务（类型 + 错误）排序后的拼接，与参数无关的顺序不影响比较。
    """
    data = payload.get("data")
    if not isinstance(data, dict) or data.get("any_failed") is not True:
        return None
    task_errors = sorted(
        f"{task.get('type') or 'task'}: {task.get('error') or '未知错误'}"
        for task in data.get("tasks") or []
        if isinstance(task, dict) and task.get("status") == "failed"
    )
    failed = data.get("failed")
    total = data.get("total_tasks")
    summary = f"并行子任务失败 {failed}/{total}"
    return {
        "status": "error",
        "error_type": "parallel_tasks_failed",
        "error": f"{summary}：{'；'.join(task_errors)}" if task_errors else summary,
    }


def _parse_error_payload(output_text: str, tool_name: str = "") -> dict[str, Any] | None:
    """工具输出是否是失败结果；是则返回解析后的 payload。

    项目工具统一以 {"status": "error", "error": ...} 报错（file_ops.router、
    mcp_tools、tool_error_text）；其余 status（success/handoff/ignored…）都不算失败。
    唯一例外是 parallel_execute：子任务失败藏在 success 外壳里（见
    _parallel_failure_payload）。
    """
    if not output_text:
        return None
    try:
        payload = json.loads(output_text)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("status") == "error":
        return payload
    if tool_name == "parallel_execute" and payload.get("status") == "success":
        return _parallel_failure_payload(payload)
    return None


def _preview(text: str) -> str:
    text = _SQL_DETAIL_RE.sub("", text)
    text = _SPACE_RE.sub(" ", text).strip()
    if len(text) <= _ERROR_PREVIEW_CHARS:
        return text
    return text[:_ERROR_PREVIEW_CHARS] + "…"


@dataclass(frozen=True)
class ToolFailureTrip:
    """熔断时的现场：触发原因、出问题的工具与最后一次错误。"""

    reason: str
    tool_name: str
    failures: int
    threshold: int
    last_error: str

    def user_message(self) -> str:
        """给用户看的停止说明（经 ERROR 事件原样展示在对话里）。"""
        error_part = f"最后一次错误：{self.last_error}" if self.last_error else "未返回具体错误信息"
        if self.reason == TRIP_REASON_TOOL_NOT_FOUND:
            return (
                f"AI 连续 {self.failures} 次调用本轮不可用的工具 {self.tool_name}（调用均未执行），"
                "已自动停止本轮，避免继续消耗额度。"
                "请重新发送请求，或换个说法让 AI 改用其他方式完成。"
            )
        if self.reason == TRIP_REASON_IDENTICAL:
            return (
                f"工具 {self.tool_name} 以相同参数连续 {self.failures} 次失败，错误相同，"
                f"AI 已自动停止本轮，避免继续重试消耗额度。{error_part}。"
                "请稍后重试；若问题持续，可以换个说法让 AI 改用其他方式完成。"
            )
        return (
            f"本轮工具调用已累计失败 {self.failures} 次，AI 已自动停止本轮，"
            f"避免继续重试消耗额度。最后失败的工具：{self.tool_name}，{error_part}。"
            "请稍后重试，或把任务拆小后再试。"
        )

    def as_event_data(self, agent_type: str) -> dict[str, Any]:
        from agent.core.stream_errors import TOOL_FAILURE_ERROR_TYPE, tool_failure_error

        info = tool_failure_error()
        return {
            # error 仍是给用户看的具体说明（工具名、失败次数、去掉 SQL 的错误摘要）；
            # code / retryable / refundable 决定前端文案与计费：熔断属于模型或
            # 用户行为造成的停止，不可重试、不退还额度。
            "error": self.user_message(),
            "code": info.code,
            "retryable": info.retryable,
            "refundable": info.refundable,
            "error_type": TOOL_FAILURE_ERROR_TYPE,
            "reason": self.reason,
            "agent_type": agent_type,
            "tool_name": self.tool_name,
            "failures": self.failures,
            "threshold": self.threshold,
            "last_error": self.last_error,
        }


@dataclass
class _CallStreak:
    fingerprint: str
    count: int


@dataclass
class ToolFailureBreaker:
    """一次请求的工具失败熔断器（writing_graph 为整条工作流建一个，跨 agent run 共享）。

    observe() 在每次工具执行后同步调用（tools_adapter 的 FunctionTool 回调），
    此时 SDK 还没检查本轮的 tool_use_behavior，因此熔断能在同一轮内生效。
    """

    max_identical_failures: int = MAX_IDENTICAL_TOOL_FAILURES
    max_failures_per_request: int = MAX_TOOL_FAILURES_PER_REQUEST
    total_failures: int = 0
    trip: ToolFailureTrip | None = None
    _streaks: dict[tuple[str, str], _CallStreak] = field(default_factory=dict)

    @property
    def is_open(self) -> bool:
        return self.trip is not None

    def observe(self, tool_name: str, raw_arguments: str, output_text: str) -> str:
        """记录一次工具结果，返回交给模型的输出（重复失败时附带不要原样重试的提示）。"""
        key = (tool_name, _normalize_arguments(raw_arguments))
        payload = _parse_error_payload(output_text, tool_name)
        if payload is None:
            # 同一调用成功：它的连续失败计数清零。累计失败数不清零——那是整次请求的兜底。
            self._streaks.pop(key, None)
            return output_text

        self.total_failures += 1
        streak: _CallStreak | None = None
        if payload.get("error_type") not in _ORDERING_ERROR_TYPES:
            fingerprint = _error_fingerprint(payload)
            streak = self._streaks.get(key)
            if streak is not None and streak.fingerprint == fingerprint:
                streak.count += 1
            else:
                # 首次失败，或同一调用换了一种错误：从 1 重新计数。
                streak = _CallStreak(fingerprint=fingerprint, count=1)
                self._streaks[key] = streak

        last_error = _preview(str(payload.get("error") or ""))
        if self.trip is None:
            if streak is not None and streak.count >= self.max_identical_failures:
                self.trip = ToolFailureTrip(
                    reason=(
                        TRIP_REASON_TOOL_NOT_FOUND
                        if payload.get("error_type") in _UNEXECUTED_CALL_ERROR_TYPES
                        else TRIP_REASON_IDENTICAL
                    ),
                    tool_name=tool_name,
                    failures=streak.count,
                    threshold=self.max_identical_failures,
                    last_error=last_error,
                )
            elif self.total_failures >= self.max_failures_per_request:
                self.trip = ToolFailureTrip(
                    reason=TRIP_REASON_TOTAL,
                    tool_name=tool_name,
                    failures=self.total_failures,
                    threshold=self.max_failures_per_request,
                    last_error=last_error,
                )

        if self.trip is None and streak is not None and streak.count >= 2:
            return _with_repeat_hint(output_text, streak.count, self.max_identical_failures)
        return output_text

    def short_circuit_text(self, tool_name: str) -> str:
        """熔断后同一轮里剩下的工具调用：不执行，直接返回说明。"""
        return tool_error_text(
            "工具调用已熔断：本轮已有工具重复失败，后续调用未执行。",
            error_type="tool_failure_circuit_open",
            tool_name=tool_name,
        )


def _with_repeat_hint(output_text: str, count: int, threshold: int) -> str:
    """在原始工具输出上附加重复失败提示（parallel_execute 保留逐任务明细）。"""
    remaining = max(threshold - count, 0)
    hinted = dict(json.loads(output_text))
    hinted["repeated_failures"] = count
    hinted["retry_hint"] = (
        f"同一调用已以相同错误连续失败 {count} 次。不要原样重试："
        "请换参数或换方法；若是系统侧故障（如数据库被锁），请停止并向用户说明。"
        f"再以相同错误失败 {remaining} 次，本轮将被自动终止。"
    )
    return json.dumps(hinted, ensure_ascii=False)


__all__ = [
    "MAX_IDENTICAL_TOOL_FAILURES",
    "MAX_TOOL_FAILURES_PER_REQUEST",
    "TRIP_REASON_IDENTICAL",
    "TRIP_REASON_TOOL_NOT_FOUND",
    "TRIP_REASON_TOTAL",
    "ToolFailureBreaker",
    "ToolFailureTrip",
]
