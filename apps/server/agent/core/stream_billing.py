"""/agent/stream 的计费判定：观察发给前端的 SSE 帧，决定本次预扣的对话额度是否退还。

规则（按优先级）：

1. 用户取消 / 客户端断线（CancelledError 或 GeneratorExit）：照常计费。
2. 请求级墙钟时限到期：本轮已有实质产出则计费，否则退还。
3. 失控停止（重复读取无进展 error 帧 reason=no_progress、请求级模型调用预算
   ERR_AGENT_MODEL_CALL_LIMIT、单 agent 工具调用轮数耗尽 iteration_exhausted
   layer=tool_call）且本轮没有任何写入成功：退还（runaway_no_progress）。
   模型自己原地打转、什么也没改，用户不该为此付一次额度；只看「有没有写入」，
   不看有没有串流文字——打转时模型照样会边读边念叨。
4. 正常结束（done / workflow_complete / 终止性 workflow_stopped，且没有 error 帧）：计费。
5. 失败：error 帧明确 ``refundable: false``（例如工具失败熔断）→ 计费；
   本轮已有实质产出（已串流正文、文件正文，或写文件工具成功）→ 计费；
   其余才是真正没有产出的平台侧故障 → 退还。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent.core.events import NON_TERMINAL_WORKFLOW_STOPPED_REASONS
from core.error_codes import ErrorCode

# 成功即意味着「本轮已经改动了用户的文件」的工具。
WRITE_TOOL_NAMES: frozenset[str] = frozenset({"create_file", "edit_file", "delete_file"})
# parallel_execute 中会改动文件的子任务类型。
PARALLEL_WRITE_TASK_TYPES: frozenset[str] = frozenset({"write_chapter", "edit_file", "delete_file"})

# 失控停止的信号：error 帧的 reason / code，iteration_exhausted 帧的 layer。
RUNAWAY_ERROR_REASONS: frozenset[str] = frozenset({"no_progress"})
RUNAWAY_ERROR_CODES: frozenset[str] = frozenset({ErrorCode.AGENT_MODEL_CALL_LIMIT})
RUNAWAY_ITERATION_LAYERS: frozenset[str] = frozenset({"tool_call"})
RUNAWAY_BILLING_REASON = "runaway_no_progress"

_TERMINAL_EVENT_TYPES = frozenset({"done", "workflow_complete"})


def _parse_sse(frame: str) -> tuple[str, Any]:
    event_type = ""
    data_raw: str | None = None
    for line in frame.splitlines():
        if line.startswith("event:"):
            event_type = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_raw = line.split(":", 1)[1]
    if data_raw is None:
        return event_type, None
    try:
        return event_type, json.loads(data_raw)
    except ValueError:
        return event_type, None


@dataclass
class StreamBillingTracker:
    saw_any_event: bool = False
    saw_terminal_event: bool = False
    saw_error_event: bool = False
    error_refundable: bool | None = None
    produced_output: bool = False
    # 本轮是否有写入真正落库（写工具成功 / 流式文件正文）。
    write_succeeded: bool = False
    # 本轮是否以失控方式停止（见模块说明第 3 条）。
    runaway_stop: bool = False

    def observe(self, frame: Any) -> None:
        if not isinstance(frame, str):
            return
        self.saw_any_event = True
        event_type, data = _parse_sse(frame)
        if not event_type:
            return  # SSE 注释帧（心跳）等
        payload = data if isinstance(data, dict) else {}

        if event_type in _TERMINAL_EVENT_TYPES:
            self.saw_terminal_event = True
        elif event_type == "workflow_stopped":
            # 只读请求拦下写交接的提示卡片也走 workflow_stopped，但它只是一条
            # 说明：之后若异常中断，仍按失败处理。
            if payload.get("reason") not in NON_TERMINAL_WORKFLOW_STOPPED_REASONS:
                self.saw_terminal_event = True
        elif event_type == "error":
            self.saw_error_event = True
            self.saw_terminal_event = True
            refundable = payload.get("refundable")
            if isinstance(refundable, bool) and self.error_refundable is not False:
                self.error_refundable = refundable
            if (
                payload.get("reason") in RUNAWAY_ERROR_REASONS
                or payload.get("code") in RUNAWAY_ERROR_CODES
            ):
                self.runaway_stop = True
        elif event_type == "iteration_exhausted" and payload.get("layer") in RUNAWAY_ITERATION_LAYERS:
            self.runaway_stop = True

        if not self.produced_output:
            self.produced_output = self._is_substantive_output(event_type, payload)
        if not self.write_succeeded:
            self.write_succeeded = self._is_committed_write(event_type, payload)

    @staticmethod
    def _is_committed_write(event_type: str, payload: dict[str, Any]) -> bool:
        if event_type == "file_content":
            return bool(str(payload.get("chunk") or "").strip())
        if event_type != "tool_result" or payload.get("status") != "success":
            return False
        tool_name = payload.get("tool_name")
        if tool_name in WRITE_TOOL_NAMES:
            return True
        if tool_name == "parallel_execute":
            data = payload.get("data")
            tasks = data.get("tasks") if isinstance(data, dict) else None
            return any(
                isinstance(task, dict)
                and task.get("type") in PARALLEL_WRITE_TASK_TYPES
                and task.get("status") == "completed"
                for task in tasks or []
            )
        return False

    @staticmethod
    def _is_substantive_output(event_type: str, payload: dict[str, Any]) -> bool:
        if event_type == "content":
            return bool(str(payload.get("text") or "").strip())
        if event_type == "file_content":
            return bool(str(payload.get("chunk") or "").strip())
        if event_type == "tool_result":
            return (
                payload.get("tool_name") in WRITE_TOOL_NAMES
                and payload.get("status") == "success"
            )
        return False

    def decide(
        self,
        *,
        user_cancelled: bool,
        unexpected_exception: bool,
        deadline_exceeded: bool = False,
    ) -> tuple[str, bool]:
        """返回 (billing_reason, should_refund)。"""
        if user_cancelled:
            return "user_cancelled", False
        if deadline_exceeded:
            return "run_deadline_exceeded", not self.produced_output
        if self.runaway_stop and not self.write_succeeded and not unexpected_exception:
            return RUNAWAY_BILLING_REASON, True
        if self.saw_terminal_event and not self.saw_error_event and not unexpected_exception:
            return "completed", False
        if self.error_refundable is False:
            return "non_refundable_error", False
        if self.produced_output:
            return "error_after_output", False
        if self.saw_any_event and not self.saw_terminal_event and not unexpected_exception:
            return "internal_error_no_terminal", True
        return "internal_error", True
