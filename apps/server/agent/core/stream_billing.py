"""/agent/stream 的计费判定：观察发给前端的 SSE 帧，决定本次预扣的对话额度是否退还。

规则（按优先级）：

1. 作者主动停止（``POST /agent/stop``，见 agent.core.run_stop）或这一轮结束前客户端
   断线（CancelledError / GeneratorExit）：本轮已有实质产出则计费，否则退还
   （``user_stopped_no_output`` / ``client_disconnected_no_output``）。只想过、读过
   文件、只说了一句过渡旁白、只建了空文件的一轮，不该占作者一条额度；见
   ``architecture/2026-10-09-agent-graceful-stop-and-no-output-refund.md`` 与
   ``architecture/2026-10-09-stop-output-definition-and-round-outcome.md``。
   终止帧（done 等）已经产生之后才断线的，这一轮已经结束，按下面的规则结算，
   不算中断。
2. 请求级墙钟时限到期：本轮已有实质产出、或结束时模型正在给作者写回复
   （``ended_on_reply``）则计费，否则退还。
3. 失控停止（重复读取无进展 error 帧 reason=no_progress、请求级模型调用预算
   ERR_AGENT_MODEL_CALL_LIMIT、单 agent 工具调用轮数耗尽 iteration_exhausted
   layer=tool_call）且本轮没有任何写入成功：退还（runaway_no_progress）。
   模型自己原地打转、什么也没改，用户不该为此付一次额度；只看「有没有写入」，
   不看有没有串流文字——打转时模型照样会边读边念叨。
4. 正常结束（done / workflow_complete / 终止性 workflow_stopped，且没有 error 帧）：计费。
5. 失败：error 帧明确 ``refundable: false``（例如工具失败熔断）→ 计费；
   本轮已有实质产出、或出错时模型正在给作者写回复（``ended_on_reply``，例如一句
   不到 60 字的完整回答之后出错）→ 计费；其余才是真正没有产出的平台侧故障 → 退还。

「实质产出」（``produced_output``）只有两种：

- 写入留下了正文（``write_succeeded``）：流式文件正文非空；``create_file`` 带着非空
  正文新建（复用已有文件不算）；``edit_file`` 真的改动了文字（``mutation_applied``）；
  ``delete_file`` 成功；``parallel_execute`` 里有写入类子任务完成（整批的 tool_result，
  或批次中途被打断前已完成子任务的 ``parallel_task_end``）；``update_project`` 改了
  作品名、简介、文风或备注（只更新任务板 / 当前进度不算）。只建了一个空文件（后面的
  正文还没写进去）不算。
- 真正的回复正文：同一段正文（两次工具调用之间）累计到 ``PROSE_MIN_CHARS`` 个单位
  （见 :func:`prose_units`：汉字、数字、标点各算 1，一个拉丁字母单词算 1.5，英文的一句
  过渡旁白和中文一样到不了）。工具调用前那句「我先看一遍全书大纲。」这类过渡旁白不算。

墙钟时限到期时，只改了项目信息（简介 / 文风 / 备注）不算产出：作者要的东西还没写出来，
记忆里改一句简介不值一条额度（``file_written`` 与 ``prose_output`` 才算）。

退还真正落库（``_refund_quota`` 返回 True）之后，api/agent.py 在终止帧之后再补发
一帧 ``quota_refunded``（见 :func:`quota_refunded_frame`），前端据此告诉作者「这一轮
不计入今日 AI 消息」。判定发生在终止帧已经发出之后，所以不能把承诺写进终止帧本身。
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from agent.core.events import NON_TERMINAL_WORKFLOW_STOPPED_REASONS
from core.error_codes import ErrorCode

# 会改动用户文件的工具；是否算「写出了东西」见 StreamBillingTracker._write_effect。
WRITE_TOOL_NAMES: frozenset[str] = frozenset({"create_file", "edit_file", "delete_file", "rename_file"})
# 同一段回复正文累计到这么多可见字符才算「真正的回复」：工具调用前的一句过渡旁白
# （「我先看一遍全书大纲。」「正在查看已有的全书大纲，确认现有设定后再梳理主线。」）
# 通常 10～30 字，到不了这里。前端 lib/agentRoundProgress.ts 用同一个值。
PROSE_MIN_CHARS = 60
# 一个拉丁字母单词折合的单位：60 个单位约等于两句中文或 40 个英文单词。
LATIN_WORD_UNITS = 1.5
_LATIN_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")
# 这些帧意味着一段正文结束（模型转去调工具、换 agent）：之后的正文重新计数。
_SEGMENT_BOUNDARY_EVENTS: frozenset[str] = frozenset(
    {"tool_call", "tool_result", "handoff", "agent_selected", "file_created", "parallel_start"}
)
_FOLDER_FILE_TYPE = "folder"
# parallel_execute 中会改动文件的子任务类型。
PARALLEL_WRITE_TASK_TYPES: frozenset[str] = frozenset({"write_chapter", "edit_file", "delete_file"})
# update_project 里作者看得见的项目信息：改了这些才算产出。只更新任务板（tasks）或
# current_phase（进度标记，任务板更新时还会被自动推进）不算。
PROJECT_INFO_FIELDS: frozenset[str] = frozenset({"summary", "writing_style", "notes"})

# 失控停止的信号：error 帧的 reason / code，iteration_exhausted 帧的 layer。
RUNAWAY_ERROR_REASONS: frozenset[str] = frozenset({"no_progress"})
RUNAWAY_ERROR_CODES: frozenset[str] = frozenset({ErrorCode.AGENT_MODEL_CALL_LIMIT})
RUNAWAY_ITERATION_LAYERS: frozenset[str] = frozenset({"tool_call"})
RUNAWAY_BILLING_REASON = "runaway_no_progress"

_TERMINAL_EVENT_TYPES = frozenset({"done", "workflow_complete"})

# 退还落库后补发的 SSE 帧。kind 只区分前端要用的两种说法：
# no_progress（失控停止、没改文件）与 error（平台出错 / 超时且没有产出）。
QUOTA_REFUNDED_EVENT = "quota_refunded"
REFUND_KIND_NO_PROGRESS = "no_progress"
REFUND_KIND_ERROR = "error"
REFUND_KIND_STOPPED = "stopped"

USER_STOPPED_NO_OUTPUT_BILLING_REASON = "user_stopped_no_output"


def quota_refunded_frame(
    billing_reason: str,
    removed_files: list[dict[str, str]] | None = None,
) -> str:
    """额度已经退还后发给前端的说明帧；只在退还真正生效后调用。

    ``removed_files``：作者停止的这一轮新建、却还是空白的文件已被移除（见
    agent.core.round_outcome.remove_empty_placeholders），前端据此刷新文件树并告诉作者。
    """
    if billing_reason == RUNAWAY_BILLING_REASON:
        kind = REFUND_KIND_NO_PROGRESS
    elif billing_reason == USER_STOPPED_NO_OUTPUT_BILLING_REASON:
        kind = REFUND_KIND_STOPPED
    else:
        kind = REFUND_KIND_ERROR
    payload: dict[str, Any] = {"refunded": True, "kind": kind}
    if removed_files:
        payload["removed_files"] = removed_files
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {QUOTA_REFUNDED_EVENT}\ndata: {data}\n\n"


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


def _is_terminal_event(event_type: str, payload: dict[str, Any]) -> bool:
    return (
        event_type in _TERMINAL_EVENT_TYPES
        or event_type == "error"
        or (event_type == "workflow_stopped" and payload.get("reason") not in NON_TERMINAL_WORKFLOW_STOPPED_REASONS)
    )


def prose_units(text: str) -> float:
    """回复正文的长度：拉丁字母单词各算 LATIN_WORD_UNITS，其余非空白字符（汉字、数字、标点）各算 1。

    只数可见字符时，英文一句十来个词的过渡旁白就有六七十个字母，会被当成真正的回复。
    前端 lib/agentRoundProgress.ts 的 proseUnits 用同一口径。
    """
    words = _LATIN_WORD_RE.findall(text)
    letters = sum(len(word) for word in words)
    others = sum(1 for ch in text if not ch.isspace()) - letters
    return others + LATIN_WORD_UNITS * len(words)


@dataclass
class StreamBillingTracker:
    saw_any_event: bool = False
    saw_terminal_event: bool = False
    # Output accounting observes the producer before enqueue; this separate
    # flag is set only immediately before the API yields a frame to its caller.
    delivered_terminal_event: bool = False
    saw_error_event: bool = False
    error_refundable: bool | None = None
    # 本轮是否有实质产出（写入留下了正文，或真正的回复正文；见模块说明）。
    produced_output: bool = False
    # 本轮是否有写入留下了正文（见模块说明）。只建空文件不算。
    write_succeeded: bool = False
    # 写入是否落在文件上（不含只改项目信息的 update_project）；墙钟时限路径只认这个。
    file_written: bool = False
    # 是否出现过真正的回复正文（同一段累计到 PROSE_MIN_CHARS）。
    prose_output: bool = False
    # 本轮是否以失控方式停止（见模块说明第 3 条）。
    runaway_stop: bool = False
    started_at: float = field(default_factory=time.monotonic)
    # 第一次出现实质产出距开始的毫秒数。
    first_output_ms: int | None = None
    # 本轮 create_file 新建、当时还是空白的文件：id -> 标题（文件夹、复用已有文件不算）。
    created_empty_files: dict[str, str] = field(default_factory=dict)
    # 同一批文件在 create_file 结果里的版本（updated_at）：退还时只删版本仍是它的文件。
    created_empty_versions: dict[str, str] = field(default_factory=dict)
    # 收到过非空流式正文的文件 id。
    filled_file_ids: set[str] = field(default_factory=set)
    # session_started 帧里的聊天会话 id（停止路径的 done 帧带上它）。
    session_id: str | None = None
    # 这一轮结束（终止帧 / error 帧）时，模型最后在做的是给作者写回复：最后一段正文
    # 之后没有再调工具。出错 / 时限路径据此把「短回答之后出错」照旧计费。
    ended_on_reply: bool = False
    # 当前这段回复正文（两次工具调用之间）：到达 PROSE_MIN_CHARS 之前的原文（流式分片会把
    # 一个单词拆开，所以按累计原文重算 prose_units），以及是否已经到达。
    _segment_text: str = field(default="", repr=False)
    _segment_reached: bool = field(default=False, repr=False)
    # parallel_task_start 帧里的子任务类型：(execution_id, task_id) -> task_type。
    _parallel_task_types: dict[tuple[str, str], str] = field(default_factory=dict, repr=False)

    def observe_delivery(self, frame: Any) -> None:
        if isinstance(frame, str):
            event_type, data = _parse_sse(frame)
            if _is_terminal_event(event_type, data if isinstance(data, dict) else {}):
                self.delivered_terminal_event = True

    def observe(self, frame: Any) -> None:
        if not isinstance(frame, str):
            return
        self.saw_any_event = True
        event_type, data = _parse_sse(frame)
        if not event_type:
            return  # SSE 注释帧（心跳）等
        payload = data if isinstance(data, dict) else {}
        if _is_terminal_event(event_type, payload):
            self.saw_terminal_event = True

        if (self._segment_reached or self._segment_text.strip()) and (
            event_type in _TERMINAL_EVENT_TYPES
            or event_type == "error"
            or (
                event_type == "workflow_stopped"
                and payload.get("reason") not in NON_TERMINAL_WORKFLOW_STOPPED_REASONS
            )
        ):
            self.ended_on_reply = True

        if event_type == "session_started" and isinstance(payload.get("session_id"), str):
            self.session_id = payload["session_id"]
        elif event_type == "error":
            self.saw_error_event = True
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

        self._track_files(event_type, payload)
        wrote = self._write_effect(event_type, payload)
        if wrote:
            self.write_succeeded = True
            if not (event_type == "tool_result" and payload.get("tool_name") == "update_project"):
                self.file_written = True
        prose = self._prose_reached(event_type, payload)
        if prose:
            self.prose_output = True
        if not self.produced_output and (wrote or prose):
            self.produced_output = True
            self.first_output_ms = int((time.monotonic() - self.started_at) * 1000)

    def removable_placeholders(self) -> list[dict[str, str]]:
        """本轮新建、之后也没收到任何正文的文件（候选；落库前还要再查一次是否为空、版本是否未变）。"""
        return [
            {
                "id": file_id,
                "title": title,
                **({"updated_at": self.created_empty_versions[file_id]}
                   if file_id in self.created_empty_versions else {}),
            }
            for file_id, title in self.created_empty_files.items()
            if file_id not in self.filled_file_ids
        ]

    def _track_files(self, event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "file_content":
            file_id = payload.get("file_id")
            if isinstance(file_id, str) and str(payload.get("chunk") or "").strip():
                self.filled_file_ids.add(file_id)
            return
        if (
            event_type != "tool_result"
            or payload.get("tool_name") != "create_file"
            or payload.get("status") != "success"
        ):
            return
        data = payload.get("data")
        if not isinstance(data, dict):
            return
        file_id = data.get("id")
        if (
            isinstance(file_id, str)
            and file_id
            and not str(data.get("content") or "").strip()
            and data.get("reused_existing") is not True
            and data.get("file_type") != _FOLDER_FILE_TYPE
            # 作者只要建文件时，空文件本身就是交付物，退还时也不移除。
            and data.get("empty_by_request") is not True
        ):
            self.created_empty_files[file_id] = str(data.get("title") or "")
            version = data.get("updated_at")
            if isinstance(version, str) and version:
                self.created_empty_versions[file_id] = version

    def _write_effect(self, event_type: str, payload: dict[str, Any]) -> bool:
        """这一帧是否意味着某个文件被写进了正文（或被删除）、项目信息被改了。"""
        if event_type == "file_content":
            return bool(str(payload.get("chunk") or "").strip())
        if event_type in ("parallel_task_start", "parallel_task_end"):
            return self._parallel_task_wrote(event_type, payload)
        if event_type != "tool_result" or payload.get("status") != "success":
            return False
        tool_name = payload.get("tool_name")
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        if tool_name == "create_file":
            # 只建了一个空文件（正文随后流式写入）不算；复用已有文件不是本轮写的。
            return bool(str(data.get("content") or "").strip()) and data.get("reused_existing") is not True
        if tool_name == "edit_file":
            mutation_applied = data.get("mutation_applied")
            if isinstance(mutation_applied, bool):
                return mutation_applied
            edits_applied = data.get("edits_applied")
            if isinstance(edits_applied, int):
                return edits_applied > 0
            return True  # 旧格式结果没有这两个字段：成功即算
        if tool_name == "delete_file":
            return True
        if tool_name == "rename_file":
            return data.get("mutation_applied") is True
        if tool_name == "update_project":
            updated_fields = data.get("updated_fields")
            changed_info = isinstance(updated_fields, list) and any(
                field_name in PROJECT_INFO_FIELDS for field_name in updated_fields
            )
            return changed_info or data.get("project_name_updated") is True
        if tool_name == "parallel_execute":
            tasks = data.get("tasks")
            return any(
                isinstance(task, dict)
                and task.get("type") in PARALLEL_WRITE_TASK_TYPES
                and task.get("status") == "completed"
                for task in tasks or []
            )
        return False

    def _parallel_task_wrote(self, event_type: str, payload: dict[str, Any]) -> bool:
        """并行批次里的写入子任务完成了（批次中途被打断时，整批的 tool_result 还没来）。"""
        key = (str(payload.get("execution_id") or ""), str(payload.get("task_id") or ""))
        if event_type == "parallel_task_start":
            task_type = payload.get("task_type")
            if isinstance(task_type, str):
                self._parallel_task_types[key] = task_type
            return False
        return (
            payload.get("status") == "completed"
            and self._parallel_task_types.get(key) in PARALLEL_WRITE_TASK_TYPES
        )

    def _prose_reached(self, event_type: str, payload: dict[str, Any]) -> bool:
        """真正的回复正文：同一段正文累计到 PROSE_MIN_CHARS 个单位（见 prose_units）。"""
        if event_type in _SEGMENT_BOUNDARY_EVENTS:
            self._segment_text = ""
            self._segment_reached = False
            return False
        if event_type != "content":
            return False
        if not self._segment_reached:
            self._segment_text += str(payload.get("text") or "")
            if prose_units(self._segment_text) >= PROSE_MIN_CHARS:
                self._segment_reached = True
                self._segment_text = ""
        return self._segment_reached

    def decide(
        self,
        *,
        client_disconnected: bool,
        unexpected_exception: bool,
        deadline_exceeded: bool = False,
        user_stopped: bool = False,
        server_restarting: bool = False,
    ) -> tuple[str, bool]:
        """返回 (billing_reason, should_refund)。

        ``server_restarting``：时限是发布交接提前的（agent.core.server_drain），规则同墙钟超时，
        只换原因标签。"""
        if client_disconnected and not user_stopped and self.saw_terminal_event:
            # 这一轮已经结束（done / 终止性停止 / error 帧已产生）之后才断开：
            # 不是中断，按结束方式结算。
            client_disconnected = False
        if deadline_exceeded and self.saw_terminal_event and not self.saw_error_event:
            # 时限（或发布交接）在 done 等正常终止帧产生之后才到：这一轮已经结束，
            # 和完成后才断线一样按结束方式结算。
            deadline_exceeded = False
        if user_stopped or client_disconnected:
            reason = "user_stopped" if user_stopped else "client_disconnected"
            if self.produced_output:
                return reason, False
            return f"{reason}_no_output", True
        if deadline_exceeded:
            reason = "server_restarting" if server_restarting else "run_deadline_exceeded"
            return reason, not (self.file_written or self.prose_output or self.ended_on_reply)
        if self.runaway_stop and not self.write_succeeded and not unexpected_exception:
            return RUNAWAY_BILLING_REASON, True
        if self.saw_terminal_event and not self.saw_error_event and not unexpected_exception:
            return "completed", False
        if self.error_refundable is False:
            return "non_refundable_error", False
        if self.produced_output or self.ended_on_reply:
            return "error_after_output", False
        if self.saw_any_event and not self.saw_terminal_event and not unexpected_exception:
            return "internal_error_no_terminal", True
        return "internal_error", True
