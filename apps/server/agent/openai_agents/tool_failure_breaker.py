"""Request-level circuit breaker for repeated tool-call failures.

SDK 的 max_turns（AGENT_TOOL_CALL_MAX_ITERATIONS）只限制模型轮数，挡不住
「同一个调用一直失败、模型一直原样重试」：SQLite 写锁不释放时，writer 曾每隔
约 37 秒重发一次完全相同的 edit_file，持续十多分钟，每一轮都在烧 token。

熔断器按**一次用户请求**记账：单循环 agent 为一轮建一个，传给每一次
agent run（同一轮里的续跑不会让计数归零）。
熔断判定只看工具结果本身（工具名 + 归一化参数 + 归一化错误），不依赖错误来源：
- 同一调用（同名 + 同参数）以等价错误连续失败 MAX_IDENTICAL_TOOL_FAILURES 次 → 熔断；
- edit_file 的无效文件标识（跨不同标识）累计 MAX_IDENTICAL_TOOL_FAILURES 次 → 按恢复失败族熔断；
- 本次请求累计失败 MAX_TOOL_FAILURES_PER_REQUEST 次（参数/错误各不相同的乱试）→ 熔断；
- 不存在或已停用的工具名（``tool_not_found``）未实际执行，只返回结构化错误引导模型改用可用工具，不计入熔断。
第一、二次失败照常把错误交还模型，让它有机会换参数/换方法自行恢复；
同一调用一旦成功，它的连续失败计数清零。

本模块只负责记账与判定；「停下」由 runner 在 SDK 的 tool_use_behavior 中同步完成，
对外的停止事件也由 runner 发出（见 runner._stop_run_after_tools）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from agent.openai_agents.events import parse_json_object, tool_error_text
from agent.openai_agents.repeat_edit_guard import EDIT_GUARD_ERROR_TYPES
from agent.openai_agents.repeat_read_guard import GUARD_ERROR_TYPES

# 同一调用以等价错误连续失败多少次后熔断。取 3：第 1 次失败让模型看到错误，
# 第 2 次（原样重试）在错误里附上「别再原样重试」的提示，第 3 次仍旧失败就停。
MAX_IDENTICAL_TOOL_FAILURES = 3

# 一次请求内连续未推进（无跨文件有效写入）累计失败多少次后熔断（不要求参数/错误相同），
# 兜住「每次换一点参数但始终失败」的乱试。正常的纠错（例如 edit_file 片段不唯一后换更长片段）
# 一般一两次就能成功，10 次远高于正常恢复所需。
MAX_TOOL_FAILURES_PER_REQUEST = 10

# 一次请求内即使穿插了跨文件有效写入，累计失败达到该倍数（默认 3 * 10 = 30）也强制熔断，
# 防止多文件交替空转无限循环。
MAX_LIFETIME_TOOL_FAILURES_MULTIPLIER = 3

# 允许作为「跨文件有效写入推进」重置连续失败计数的写工具。
_MUTATION_TOOL_NAMES: frozenset[str] = frozenset({
    "edit_file",
    "create_file",
    "write_chapter",
    "rename_file",
    "delete_file",
})

# 同一条模型回复里并列发出多个流式 create_file / write_chapter 时，第 1 个占住待写槽位，
# 同批其余调用在模型看到 awaiting_body 之前就被同步挡回（pending_empty_file_unwritten）。
# 这属于单批并列调用的顺序化，不是工具失败，也不计入请求级累计失败上限（否则一次批量建 5～6 份
# 角色档案就会凭空耗掉一半熔断额度）。
# 墙钟收尾提醒已注入后仍发出的工具调用由 tools_adapter 以 wall_clock_wrap_up 同步挡回，
# 同样不能计入熔断（否则模型并行发出多个工具时会误触发致命 tool_failure_circuit_open 错误，破坏干净软着陆）。
_PARALLEL_BATCH_ORDERING_ERROR_TYPES = frozenset({"pending_empty_file_unwritten", "wall_clock_wrap_up"})

# 建文件后的**下一次**模型回复仍未先写 <file> 正文就调工具时，tools_adapter 在执行前以
# file_body_pending 挡回，并由 runner 结束当前 run 交回 FILE_BODY_RESUME 纠偏：不参与「同参数连续
# 三次失败」熔断（否则纠偏轮跑不到），但仍计入请求累计失败上限作兜底。
_ORDERING_ERROR_TYPES = frozenset({"file_body_pending"})

# 重复读取守卫（repeat_read_guard.GUARD_ERROR_TYPES）主动拦下的结果：不是工具失败，由守卫
# 自己计数并判定「无进展」；熔断器若也记账，同一文件第 4～6 次读取会先以「相同参数连续失败」
# 熔断，抢走守卫的停止原因与计费口径。

# 触发原因，写进停止事件的 data.reason，供日志/测试区分。
TRIP_REASON_IDENTICAL = "repeated_identical_tool_failure"
TRIP_REASON_RECOVERY = "repeated_recoverable_tool_failure"
TRIP_REASON_TOTAL = "too_many_tool_failures"
# 调用不存在或已停用的工具名（如旧历史里的 query_files）时工具根本未执行，
# 只返回清晰结构化错误引导模型改用当前可用工具，不计入熔断（由 max_turns 兜底轮数上限）。
_NON_BREAKER_ERROR_TYPES = frozenset({"tool_not_found"})

# 这几类失败即使模型每次换一点参数，恢复动作也不变：文件标识无效时必须重新取得
# 当前真实标识；update_project 字段超限时必须大幅精炼至目标字数以内；同一文件的
# 锚点不存在时必须重读当前段落。按完整参数分别计数会让“枚举相似 UUID / 微删几个字
# 在 4000 边缘来回震荡”绕到请求累计十次才停。
_FILE_NOT_FOUND_ERROR_TYPE = "file_not_found"
_ANCHOR_NOT_FOUND_ERROR_TYPE = "anchor_not_found"
_PROJECT_FIELD_TOO_LONG_ERROR_TYPE = "project_field_too_long"
_FILE_NOT_FOUND_FAMILY = ("edit_file", _FILE_NOT_FOUND_ERROR_TYPE, "*")
_PROJECT_FIELD_TOO_LONG_FAMILY = ("update_project", _PROJECT_FIELD_TOO_LONG_ERROR_TYPE, "*")
_EARLY_TRIP_RECOVERY_FAMILIES: frozenset[tuple[str, str, str]] = frozenset({
    _FILE_NOT_FOUND_FAMILY,
    _PROJECT_FIELD_TOO_LONG_FAMILY,
})

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


def _normalize_arguments(
    raw_arguments: str, *, parsed_args: dict[str, Any] | None = None
) -> str:
    """把工具参数归一成可比较的键：键排序的紧凑 JSON；解析失败则用原文。"""
    if parsed_args is not None:
        return json.dumps(parsed_args, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw = raw_arguments or ""
    parsed, parse_error, _metadata = parse_json_object(raw)
    if parse_error is not None:
        return raw.strip()
    return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parsed_arguments(
    raw_arguments: str, *, parsed_args: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    if parsed_args is not None:
        return parsed_args
    parsed, parse_error, _metadata = parse_json_object(raw_arguments or "")
    return parsed if parse_error is None and isinstance(parsed, dict) else None


def _file_id_from_arguments(
    raw_arguments: str, *, parsed_args: dict[str, Any] | None = None
) -> str:
    parsed = _parsed_arguments(raw_arguments, parsed_args=parsed_args)
    if parsed is None:
        return ""
    raw_id = parsed.get("id") or parsed.get("file_id") or parsed.get("fileId")
    return raw_id.strip() if isinstance(raw_id, str) else ""


def _recovery_family_key(
    tool_name: str,
    raw_arguments: str,
    payload: dict[str, Any],
    *,
    parsed_args: dict[str, Any] | None = None,
) -> tuple[str, str, str] | None:
    """Return a structured recovery family; never infer meaning from prose."""
    error_type = payload.get("error_type")
    if tool_name == "update_project":
        if error_type == _PROJECT_FIELD_TOO_LONG_ERROR_TYPE:
            return _PROJECT_FIELD_TOO_LONG_FAMILY
        return None
    if tool_name != "edit_file":
        return None
    if error_type == _FILE_NOT_FOUND_ERROR_TYPE:
        # Different guessed IDs are the production failure mode, so the ID is intentionally
        # not part of this key. We do not choose or correct an ID; we only stop blind guessing.
        return _FILE_NOT_FOUND_FAMILY
    if error_type == _ANCHOR_NOT_FOUND_ERROR_TYPE:
        file_id = _file_id_from_arguments(raw_arguments, parsed_args=parsed_args)
        if file_id:
            return (tool_name, _ANCHOR_NOT_FOUND_ERROR_TYPE, file_id)
    return None


def _clear_recovery_families(
    families: dict[tuple[str, str, str], int],
    tool_name: str,
    raw_arguments: str,
    *,
    parsed_args: dict[str, Any] | None = None,
) -> None:
    if tool_name == "update_project":
        families.pop(_PROJECT_FIELD_TOO_LONG_FAMILY, None)
        return
    if tool_name != "edit_file":
        return
    # A successful edit_file proves that the caller now has a usable ID, even if the
    # requested mutation was a no-op. It is the only safe evidence for clearing the
    # request-wide invalid-ID family; a list read alone does not prove the copied ID.
    families.pop(_FILE_NOT_FOUND_FAMILY, None)
    file_id = _file_id_from_arguments(raw_arguments, parsed_args=parsed_args)
    if file_id:
        families.pop((tool_name, _ANCHOR_NOT_FOUND_ERROR_TYPE, file_id), None)


def _extract_error_message(payload: dict[str, Any]) -> str:
    """提取工具错误文本：优先顶层 error，缺失时兜底取 data.failed_edits 首项 error。"""
    top = str(payload.get("error") or "").strip()
    if top:
        return top
    data = payload.get("data")
    if isinstance(data, dict):
        failed_edits = data.get("failed_edits")
        if isinstance(failed_edits, list):
            for item in failed_edits:
                if isinstance(item, dict):
                    msg = str(item.get("error") or "").strip()
                    if msg:
                        return msg
    return ""


def _error_fingerprint(payload: dict[str, Any]) -> str:
    """「等价错误」的指纹：抹掉 id、数字等每次都会变的部分。

    例如 SQLite 的 "database is locked" 报错里带着本次 UPDATE 的时间戳参数，
    逐字比较会让每次重试都算作「不同错误」而永远不熔断。
    """
    error_type = payload.get("error_type")
    message = _extract_error_message(payload)
    text = _UUID_RE.sub("<id>", message)
    text = _HEX_RE.sub("<hex>", text)
    text = _DIGITS_RE.sub("#", text)
    text = _SPACE_RE.sub(" ", text).strip().lower()[:_ERROR_FINGERPRINT_CHARS]
    if isinstance(error_type, str) and error_type:
        return f"{error_type}:{text}"
    return text


def _parse_json_dict(output_text: str) -> dict[str, Any] | None:
    if not output_text:
        return None
    try:
        payload = json.loads(output_text)
    except (json.JSONDecodeError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _parse_error_payload(output_text: str) -> dict[str, Any] | None:
    """工具输出是否是失败结果；是则返回解析后的 payload。

    项目工具统一以 {"status": "error", "error": ...} 报错（mcp_tools、tool_error_text）；其余 status 都不算失败。
    """
    payload = _parse_json_dict(output_text)
    if payload is not None and payload.get("status") == "error":
        return payload
    return None


def _is_guard_skipped_payload(payload: dict[str, Any] | None) -> bool:
    """RepeatEditGuard 的软跳过回执：既不计为失败，也不当作有效写入清零连续失败或恢复族。"""
    if payload is None:
        return False
    if payload.get("guard_skipped") is True:
        return True
    if str(payload.get("status") or "").strip().lower() == "skipped":
        return True
    return payload.get("error_type") in EDIT_GUARD_ERROR_TYPES


def _verified_mutated_file_id(
    tool_name: str,
    raw_arguments: str,
    payload: dict[str, Any] | None,
    *,
    parsed_args: dict[str, Any] | None = None,
) -> str:
    """工具结果代表一次真实落盘的文件改动时返回目标 file_id，否则返回空串。

    未写正文的空占位（awaiting_body=True）、无改动回执（mutation_applied=False）、
    全失败（all_failed=True）与纯只读/元数据工具都不算有效文件改动。
    """
    if tool_name not in _MUTATION_TOOL_NAMES or payload is None:
        return ""
    status = str(payload.get("status") or "").strip().lower()
    if status not in {"success", "partial"}:
        return ""
    if payload.get("guard_skipped") is True or payload.get("mutation_applied") is False:
        return ""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    if data.get("all_failed") is True or data.get("awaiting_body") is True:
        return ""
    if data.get("mutation_applied") is False:
        return ""
    raw_id = data.get("id") or payload.get("id") or _file_id_from_arguments(raw_arguments, parsed_args=parsed_args)
    return raw_id.strip() if isinstance(raw_id, str) and raw_id.strip() else ""


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
        """给作者看的停止说明（经 ERROR 事件原样展示在对话里）。

        不带工具名、失败次数和错误原文：这些是给开发者排查用的，只写日志
        （runner 在熔断时记录 tool_name / failures / last_error）。
        """
        return (
            "AI 这一步连续几次都没做成，这一轮先停下了。之前改好的内容不受影响。"
            "可以换个说法，或者把任务拆小一点再试。"
        )

    def as_event_data(self, agent_type: str) -> dict[str, Any]:
        from agent.core.stream_errors import TOOL_FAILURE_ERROR_TYPE, tool_failure_error

        info = tool_failure_error()
        return {
            # error 是给作者看的停止说明，不含工具名与错误原文（只进日志）；
            # code / retryable / refundable 决定前端文案与计费：熔断属于模型或
            # 用户行为造成的停止，不可重试、不退还额度。
            "error": self.user_message(),
            "code": info.code,
            "retryable": info.retryable,
            "refundable": info.refundable,
            "error_type": TOOL_FAILURE_ERROR_TYPE,
            "reason": self.reason,
            "agent_type": agent_type,
            "failures": self.failures,
            "threshold": self.threshold,
        }


@dataclass
class _CallStreak:
    fingerprint: str
    count: int


@dataclass
class ToolFailureBreaker:
    """一次请求的工具失败熔断器（单循环 agent 为一轮建一个，跨 agent run 共享）。

    observe() 在每次工具执行后同步调用（tools_adapter 的 FunctionTool 回调），
    此时 SDK 还没检查本轮的 tool_use_behavior，因此熔断能在同一轮内生效。
    """

    max_identical_failures: int = MAX_IDENTICAL_TOOL_FAILURES
    max_failures_per_request: int = MAX_TOOL_FAILURES_PER_REQUEST
    max_lifetime_failures: int | None = None
    total_failures: int = 0
    consecutive_failures: int = 0
    trip: ToolFailureTrip | None = None
    _streaks: dict[tuple[str, str], _CallStreak] = field(default_factory=dict)
    _recovery_families: dict[tuple[str, str, str], int] = field(default_factory=dict)
    # 同一模型回复批次内并列发出的顺序守卫拒绝（如 11 个并行 delete_file 全触发 file_body_pending）
    # 最多按 1 次计入，避免抢跑击穿熔断器、阻断 runner 交回 FILE_BODY_RESUME。
    _counted_ordering_batches: set[tuple[str, int]] = field(default_factory=set)
    # 同一模型回复批次内并列发出的 edit_file file_not_found 最多按 1 次计入恢复族，
    # 避免 Step N 并行 2 个坏 UUID + Step N+1 首项坏 UUID 立即熔断误杀同批合法编辑。
    _counted_file_not_found_batches: set[int] = field(default_factory=set)
    _last_mutated_file_id: str = ""

    @property
    def is_open(self) -> bool:
        return self.trip is not None

    @property
    def lifetime_failure_cap(self) -> int:
        if self.max_lifetime_failures is not None:
            return self.max_lifetime_failures
        return self.max_failures_per_request * MAX_LIFETIME_TOOL_FAILURES_MULTIPLIER

    def observe(
        self,
        tool_name: str,
        raw_arguments: str,
        output_text: str,
        *,
        parsed_args: dict[str, Any] | None = None,
        parsed_output: dict[str, Any] | None = None,
        reply_seq: int | None = None,
    ) -> str:
        """记录一次工具结果，返回交给模型的输出（重复失败时附带不要原样重试的提示）。"""
        key = (tool_name, _normalize_arguments(raw_arguments, parsed_args=parsed_args))
        raw_payload = parsed_output if parsed_output is not None else _parse_json_dict(output_text)
        if _is_guard_skipped_payload(raw_payload):
            return output_text
        payload = raw_payload if raw_payload is not None and raw_payload.get("status") == "error" else None
        if payload is not None and payload.get("error_type") in (
            GUARD_ERROR_TYPES
            | EDIT_GUARD_ERROR_TYPES
            | _PARALLEL_BATCH_ORDERING_ERROR_TYPES
            | _NON_BREAKER_ERROR_TYPES
        ):
            return output_text
        if payload is None:
            # 同一调用成功：它的连续失败计数清零。
            self._streaks.pop(key, None)
            _clear_recovery_families(self._recovery_families, tool_name, raw_arguments, parsed_args=parsed_args)
            if tool_name == "edit_file":
                self._counted_file_not_found_batches.clear()
            # 跨文件的真实有效写入（非空占位、非无改动）推进了批量任务，重置连续失败计数；
            # 同文件重复写入不重置，防止单文件交替空转；total_failures 仍单调递增并受 lifetime_failure_cap 兜底。
            mutated_file_id = _verified_mutated_file_id(
                tool_name, raw_arguments, raw_payload, parsed_args=parsed_args
            )
            if mutated_file_id and mutated_file_id != self._last_mutated_file_id:
                self.consecutive_failures = 0
                self._last_mutated_file_id = mutated_file_id
            return output_text

        error_type = payload.get("error_type")
        if error_type in _ORDERING_ERROR_TYPES and reply_seq is not None and reply_seq > 0:
            batch_key = (str(error_type), int(reply_seq))
            if batch_key in self._counted_ordering_batches:
                return output_text
            self._counted_ordering_batches.add(batch_key)

        self.total_failures += 1
        self.consecutive_failures += 1
        streak: _CallStreak | None = None
        if error_type not in _ORDERING_ERROR_TYPES:
            fingerprint = _error_fingerprint(payload)
            streak = self._streaks.get(key)
            if streak is not None and streak.fingerprint == fingerprint:
                streak.count += 1
            else:
                # 首次失败，或同一调用换了一种错误：从 1 重新计数。
                streak = _CallStreak(fingerprint=fingerprint, count=1)
                self._streaks[key] = streak

        recovery_key = _recovery_family_key(
            tool_name, raw_arguments, payload, parsed_args=parsed_args
        )
        recovery_count = 0
        if recovery_key is not None:
            if (
                recovery_key == _FILE_NOT_FOUND_FAMILY
                and reply_seq is not None
                and reply_seq > 0
            ):
                batch_seq = int(reply_seq)
                if batch_seq in self._counted_file_not_found_batches:
                    recovery_count = self._recovery_families.get(recovery_key, 0)
                else:
                    self._counted_file_not_found_batches.add(batch_seq)
                    recovery_count = self._recovery_families.get(recovery_key, 0) + 1
                    self._recovery_families[recovery_key] = recovery_count
            else:
                recovery_count = self._recovery_families.get(recovery_key, 0) + 1
                self._recovery_families[recovery_key] = recovery_count

        last_error = _preview(_extract_error_message(payload))
        if self.trip is None:
            # 无效文件 id 与 update_project 字段超限提前熔断（防止猜 id 或在字数上限边缘微删空转）。
            # 同一文件的锚点失败只附恢复提示、计入连续/累计失败上限：生产 11-01～03 这类失败多是
            # 长 old 差一两个字、大批编辑整批回滚，改前 78% 之后都在同一文件上改成功了，三次即停反而掐断能自己恢复的轮次。
            if recovery_key in _EARLY_TRIP_RECOVERY_FAMILIES and recovery_count >= self.max_identical_failures:
                self.trip = ToolFailureTrip(
                    reason=TRIP_REASON_RECOVERY,
                    tool_name=tool_name,
                    failures=recovery_count,
                    threshold=self.max_identical_failures,
                    last_error=last_error,
                )
            elif streak is not None and streak.count >= self.max_identical_failures:
                self.trip = ToolFailureTrip(
                    reason=TRIP_REASON_IDENTICAL,
                    tool_name=tool_name,
                    failures=streak.count,
                    threshold=self.max_identical_failures,
                    last_error=last_error,
                )
            elif self.consecutive_failures >= self.max_failures_per_request:
                self.trip = ToolFailureTrip(
                    reason=TRIP_REASON_TOTAL,
                    tool_name=tool_name,
                    failures=self.consecutive_failures,
                    threshold=self.max_failures_per_request,
                    last_error=last_error,
                )
            elif self.total_failures >= self.lifetime_failure_cap:
                self.trip = ToolFailureTrip(
                    reason=TRIP_REASON_TOTAL,
                    tool_name=tool_name,
                    failures=self.total_failures,
                    threshold=self.lifetime_failure_cap,
                    last_error=last_error,
                )

        if self.trip is None and recovery_count >= 2:
            return _with_recovery_hint(
                output_text,
                str(payload.get("error_type") or ""),
                recovery_count,
                self.max_identical_failures,
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


def _with_recovery_hint(output_text: str, error_type: str, count: int, threshold: int) -> str:
    remaining = max(threshold - count, 0)
    hinted = dict(json.loads(output_text))
    hinted["repeated_failures"] = count
    if error_type == _FILE_NOT_FOUND_ERROR_TYPE:
        recovery = (
            "目标文件 id 已连续无效。不要猜测、枚举或改写相似 id；"
            "先用 search_files 不带关键词查询当前文件列表，再从结果逐字复制目标 id。"
            "只有确认准确 id 后才能继续写；不能确认就停止并如实说明未修改，"
            "不能把失败解释成权限问题。"
        )
    elif error_type == _PROJECT_FIELD_TOO_LONG_ERROR_TYPE:
        recovery = (
            "update_project 字段已连续超出字数上限。不要只微删几个字反复试探："
            "请按错误提示大幅精炼压缩旧细节至建议目标字数以内（summary ≤3000 字、"
            "current_phase / writing_style ≤700 字）后再提交一次；"
        )
    else:
        recovery = (
            "同一文件已连续定位失败。先用 read_files(ids=[...]) 重读当前文件"
            "（仅超过 25000 字的超长文件才用 search_files(query=关键词, ids=[...]) 精确取命中片段，或传 start_char 与 max_chars 按范围读取），"
            "从结果复制较短、逐字、唯一的 old/anchor；同一文件的多处独立修改合并进一次 edit_file "
            "批量提交（超过 30 处或相互依赖时再拆分）；不要放宽匹配或整篇覆盖。"
        )
        hinted["retry_hint"] = recovery
        return json.dumps(hinted, ensure_ascii=False)
    hinted["retry_hint"] = (
        f"{recovery}再发生 {remaining} 次同类失败，本轮将被自动终止。"
    )
    return json.dumps(hinted, ensure_ascii=False)


__all__ = [
    "MAX_IDENTICAL_TOOL_FAILURES",
    "MAX_LIFETIME_TOOL_FAILURES_MULTIPLIER",
    "MAX_TOOL_FAILURES_PER_REQUEST",
    "TRIP_REASON_IDENTICAL",
    "TRIP_REASON_RECOVERY",
    "TRIP_REASON_TOTAL",
    "ToolFailureBreaker",
    "ToolFailureTrip",
]
