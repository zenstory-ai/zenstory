"""/agent/stream 的计费判定与产物回执追踪。

见 ``.agents/notes/implemented/bug-fix/2026-11-20-round-billing-model-error-only-refund.md``。

消息额度结算规则（按优先级）：

1. 终止帧（``done`` / 终态 ``error``）已产生后的断线或墙钟到点，按原结束方式结算。
2. 正常完成（已看到 ``done`` 等非错误终止帧，且无异常、非停止、非断线、非时限）：计费（``completed``）。
3. 尚未进入任何真实模型供应商调用（``provider_attempted == False``，例如进入模型前停止、断线、
   超时、鉴权后预启动失败，或首次调用即被成本预检 ``ERR_QUOTA_AI_DAILY_COST_EXCEEDED`` /
   ``ERR_AI_COST_BUDGET_UNAVAILABLE`` 拦截）：一律退还预扣额度。
4. 已进入真实模型调用，且因未恢复的上游模型服务 / 网络 / 响应流错误终止整轮
   （``terminal_model_failure == True``）：退还预扣额度（``upstream_model_error``），无论此前
   是否已流出部分正文。
5. 已进入真实模型调用且非上述上游模型错误终止（包括纯读取核对、只思考、简短旁白、作者主动停止、
   客户端断线、墙钟超时 / 发布交接、工具失败熔断、重复读取无进展、重复循环、模型调用上限、
   输出截断、SDK 协议错或本地非模型异常、以及第 2 次及以后调用撞上成本兜底）：一律计 1 轮 AI
   消息额度，不退还。

本类同时追踪本轮产物事实（``produced_output``、``write_succeeded``、``file_written``、
``prose_output``、``removable_placeholders()``），用于停止回执（``done.produced_output``、
``stop_outcome.saved_output``）、墙钟暂停卡（``file_written``）、前端停止后是否跳过建议请求
以及退还轮移除空白占位文件；这些产物标记不再决定 AI 消息额度是否退还。
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.error_codes import ErrorCode

# 会改动用户文件的工具；是否算「写出了东西」见 StreamBillingTracker._write_effect。
WRITE_TOOL_NAMES: frozenset[str] = frozenset({"create_file", "write_chapter", "edit_file", "delete_file", "rename_file"})
# 同一段回复正文累计到这么多可见字符才算「真正的回复」：工具调用前的一句过渡旁白
# （「我先看一遍全书大纲。」「正在查看已有的全书大纲，确认现有设定后再梳理主线。」）
# 通常 10～30 字，到不了这里。前端 lib/agentRoundProgress.ts 用同一个值。
PROSE_MIN_CHARS = 60
# 一个拉丁字母单词折合的单位：60 个单位约等于两句中文或 40 个英文单词。
LATIN_WORD_UNITS = 1.5
_LATIN_WORD_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]+")
# 这些帧意味着一段正文结束（模型转去调工具、建文件）：之后的正文重新计数。
_SEGMENT_BOUNDARY_EVENTS: frozenset[str] = frozenset(
    {"tool_call", "tool_result", "file_created"}
)
_FOLDER_FILE_TYPE = "folder"
# update_project 里作者看得见的项目信息：改了这些才算产出。只更新任务板（tasks）或
# current_phase（进度标记，任务板更新时还会被自动推进）不算。
PROJECT_INFO_FIELDS: frozenset[str] = frozenset({"summary", "writing_style", "notes"})

_TERMINAL_EVENT_TYPES = frozenset({"done", "error", "workflow_stopped"})

# 退还落库后补发的 SSE 帧。kind 区分前端要用的两种说法：
# stopped（模型开始处理前作者点了停止）与 error（未进入模型前失败 / 上游模型服务报错）。
QUOTA_REFUNDED_EVENT = "quota_refunded"
REFUND_KIND_ERROR = "error"
REFUND_KIND_STOPPED = "stopped"

USER_STOPPED_BEFORE_MODEL_BILLING_REASON = "user_stopped_before_model"


def quota_refunded_frame(
    billing_reason: str,
    removed_files: list[dict[str, str]] | None = None,
) -> str:
    """额度已经退还后发给前端的说明帧；只在退还真正生效后调用。

    ``removed_files``：被退还的这一轮新建、却还是空白的文件已被移除（见
    agent.core.round_outcome.remove_empty_placeholders），前端据此刷新文件树并告诉作者。
    """
    kind = (
        REFUND_KIND_STOPPED
        if billing_reason == USER_STOPPED_BEFORE_MODEL_BILLING_REASON
        else REFUND_KIND_ERROR
    )
    payload: dict[str, Any] = {"refunded": True, "kind": kind}
    if removed_files:
        payload["removed_files"] = removed_files
    data = json.dumps(payload, ensure_ascii=False)
    return f"event: {QUOTA_REFUNDED_EVENT}\ndata: {data}\n\n"


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


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


def _is_terminal_event(event_type: str) -> bool:
    return event_type in _TERMINAL_EVENT_TYPES


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
    # 本轮是否有实质产出（写入留下了正文，或真正的回复正文；供回执与 UI 门槛使用）。
    produced_output: bool = False
    # 本轮是否有写入留下了正文（只建空文件不算）。
    write_succeeded: bool = False
    # 写入是否落在文件上（不含只改项目信息的 update_project）；墙钟暂停卡认这个。
    file_written: bool = False
    # 是否出现过真正的回复正文（同一段累计到 PROSE_MIN_CHARS）。
    prose_output: bool = False
    started_at: float = field(default_factory=time.monotonic)
    # 第一次出现实质产出距开始的毫秒数。
    first_output_ms: int | None = None
    # 本轮 create_file 新建、当时还是空白的文件：id -> 标题（文件夹、复用已有文件不算）。
    created_empty_files: dict[str, str] = field(default_factory=dict)
    # 同一批文件在 create_file 结果里的版本（updated_at）：退还时只删版本仍是它的文件。
    created_empty_versions: dict[str, str] = field(default_factory=dict)
    # 收到过非空流式正文的文件 id。
    filled_file_ids: set[str] = field(default_factory=set)
    # write_chapter 定位为整章替换的已有章节（结果 action=replaced 的 id）：它们的流式正文不算写入。
    replace_target_ids: set[str] = field(default_factory=set)
    # 原本 0 字的已有空章走 write_chapter(replaced)：中途停止时流过的正文照常落库，但在开始流正文前（或保存失败后）仍属待正文状态。
    empty_replace_target_ids: set[str] = field(default_factory=set)
    # 收到了落库结果（body_saved）的替换目标：保存的事实已经由帧确认。
    confirmed_replacement_ids: set[str] = field(default_factory=set)
    # session_started 帧里的聊天会话 id（停止路径的 done 帧带上它）。
    session_id: str | None = None
    # 当前这段回复正文（两次工具调用之间）：到达 PROSE_MIN_CHARS 之前的原文（流式分片会把
    # 一个单词拆开，所以按累计原文重算 prose_units），以及是否已经到达。
    _segment_text: str = field(default="", repr=False)
    _segment_reached: bool = field(default=False, repr=False)
    # 非流式工具（create_file 带正文、edit_file、delete_file、rename_file、write_chapter 替换落库等）已确认的文件写入。
    _confirmed_tool_file_write: bool = field(default=False, repr=False)
    # update_project 改动了可见项目信息（简介、文风、备注、项目名）。
    _project_info_written: bool = field(default=False, repr=False)
    # 流式正文落库明确失败的文件 id：其此前流过的 file_content 不算已保存产出。
    _failed_save_file_ids: set[str] = field(default_factory=set, repr=False)
    # delete_file 调用记录的目标文件 id（tool_use_id -> file_id），供成功结果清理待写状态。
    _delete_targets: dict[str, str] = field(default_factory=dict, repr=False)

    def _recompute_output_flags(self) -> None:
        has_streamed_write = any(fid not in self.replace_target_ids for fid in self.filled_file_ids)
        self.file_written = self._confirmed_tool_file_write or has_streamed_write
        self.write_succeeded = self.file_written or self._project_info_written
        self.produced_output = self.write_succeeded or self.prose_output
        if not self.produced_output:
            self.first_output_ms = None
        elif self.first_output_ms is None:
            self.first_output_ms = int((time.monotonic() - self.started_at) * 1000)

    def _mark_file_save_failed(self, file_id: str) -> None:
        if not file_id:
            return
        self._failed_save_file_ids.add(file_id)
        self.filled_file_ids.discard(file_id)
        self._recompute_output_flags()

    def observe_delivery(self, frame: Any) -> None:
        if isinstance(frame, str):
            event_type, _ = _parse_sse(frame)
            if _is_terminal_event(event_type):
                self.delivered_terminal_event = True

    def observe(self, frame: Any) -> None:
        if not isinstance(frame, str):
            return
        self.saw_any_event = True
        event_type, data = _parse_sse(frame)
        if not event_type:
            return  # SSE 注释帧（心跳）等
        payload = data if isinstance(data, dict) else {}

        if event_type == "session_started" and isinstance(payload.get("session_id"), str):
            self.session_id = payload["session_id"]
        elif event_type == "error":
            self.saw_error_event = True
            if payload.get("code") == ErrorCode.AGENT_FILE_SAVE_FAILED and isinstance(payload.get("file_id"), str):
                self._mark_file_save_failed(payload["file_id"])

        self._observe_effects(event_type, payload)
        prose = self._prose_reached(event_type, payload)
        if prose and not self.file_body_pending():
            self.prose_output = True
        self._recompute_output_flags()

        if _is_terminal_event(event_type):
            self.saw_terminal_event = True

    def _observe_effects(self, event_type: str, payload: dict[str, Any]) -> bool:
        """一帧（或一份按帧格式还原的工具结果）对文件的影响：占位候选、写入标记。返回是否算写入。"""
        self._track_files(event_type, payload)
        wrote = self._write_effect(event_type, payload)
        if wrote:
            if event_type == "tool_result" and payload.get("tool_name") == "update_project":
                self._project_info_written = True
            elif event_type != "file_content":
                self._confirmed_tool_file_write = True
        self._recompute_output_flags()
        return wrote

    def note_committed_tool_results(self, results: list[dict[str, Any]]) -> None:
        """截停后才在线程里完成的写工具结果（service 等到的结构化结果，帧格式：tool_name / status / data）。

        与实时的 tool_result 帧走同一套规则：确认的修改、删除、改名算写入；新建的空文件成为
        占位候选（退还时照常移除）。没有对应的帧送到前端，所以不改卡片。
        """
        for payload in results:
            if not isinstance(payload, dict):
                continue
            self._observe_effects("tool_result", payload)

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
        if event_type == "tool_call" and payload.get("tool_name") == "delete_file":
            tool_use_id = payload.get("tool_use_id")
            args = payload.get("arguments")
            target_id = args.get("id") if isinstance(args, dict) else None
            if isinstance(tool_use_id, str) and tool_use_id and isinstance(target_id, str) and target_id:
                self._delete_targets[tool_use_id] = target_id
            return
        if event_type == "tool_result" and payload.get("tool_name") == "delete_file":
            tool_use_id = payload.get("tool_use_id")
            call_target = self._delete_targets.pop(tool_use_id, None) if isinstance(tool_use_id, str) else None
            data = payload.get("data")
            if payload.get("status") == "success" and (
                data is True or (isinstance(data, dict) and data.get("mutation_applied") is True)
            ):
                deleted_id = data.get("id") if isinstance(data, dict) and isinstance(data.get("id"), str) else call_target
                if deleted_id:
                    self.created_empty_files.pop(deleted_id, None)
                    self.created_empty_versions.pop(deleted_id, None)
                    self.replace_target_ids.discard(deleted_id)
                    self.empty_replace_target_ids.discard(deleted_id)
            return
        if event_type == "file_created":
            file_id = payload.get("file_id")
            if isinstance(file_id, str) and file_id:
                self._failed_save_file_ids.discard(file_id)
            return
        if event_type == "file_content":
            file_id = payload.get("file_id")
            if (
                isinstance(file_id, str)
                and file_id not in self._failed_save_file_ids
                and str(payload.get("chunk") or "").strip()
            ):
                self.filled_file_ids.add(file_id)
            return
        if (
            event_type != "tool_result"
            or payload.get("tool_name") not in ("create_file", "write_chapter")
            or payload.get("status") != "success"
        ):
            return
        data = payload.get("data")
        if not isinstance(data, dict):
            return
        if payload.get("tool_name") == "write_chapter" and isinstance(data.get("body_saved"), bool):
            file_id = data.get("id")
            if isinstance(file_id, str) and file_id:
                if data["body_saved"]:
                    self.confirmed_replacement_ids.add(file_id)
                else:
                    self.replace_target_ids.discard(file_id)
                    self.empty_replace_target_ids.discard(file_id)
                    self._mark_file_save_failed(file_id)
            return
        if payload.get("tool_name") == "write_chapter" and data.get("action") != "created":
            # 整章替换的目标是已有的章节，不是这一轮建的空白占位；有原文时它的流式正文要等落库结果才算写入。
            # 原文为空的章节没有要保护的正文（适配器的截断覆盖保护同样只看原长度 > 0）：中途停下时
            # 流过的正文照常落库，所以照常按 file_content 计入；但在收到流式正文前（或落库失败回滚后）仍属待写正文。
            if data.get("action") == "replaced" and isinstance(data.get("id"), str) and data["id"]:
                if _positive_int(data.get("original_content_length", data.get("previous_length"))):
                    self.replace_target_ids.add(data["id"])
                else:
                    self.empty_replace_target_ids.add(data["id"])
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

    def file_body_pending(self) -> bool:
        """本轮开始写的文件还在等正文：新建的空文件没收到正文，或整章替换还没落库。

        这时最后那段文字是 ``<file>`` 之前的引子：适配器把它扣在捕获缓冲里，出错 / 收尾时
        才放出，不算回复正文（``prose_output``）。
        """
        pending_empty_replace = self.empty_replace_target_ids - self.filled_file_ids - self.confirmed_replacement_ids
        return bool(self.removable_placeholders() or self.unconfirmed_replacement_ids() or pending_empty_replace)

    def unconfirmed_replacement_ids(self) -> set[str]:
        """定位了整章替换、还没收到落库结果的章节（停止 / 断线时保存可能还在途中）。"""
        return self.replace_target_ids - self.confirmed_replacement_ids

    def note_saved_receipts(self, receipts: Mapping[str, int | None]) -> None:
        """按 StreamAdapter 的保存回执补记写入：落库结果没来得及作为帧发出（停止 / 断线时保存仍在
        途中、之后才提交，或短正文只在解析器尾缓冲里、截停时才补存）时，回执里有字数就说明正文
        已经写进了文件，算写入。覆盖整章替换的目标和本轮新建的空文件；None（停止 / 断线时补存未确认）
        只表示不补记，不回滚停止前已流给编辑器的 file_content（平台侧落库报错由 ERR_AGENT_FILE_SAVE_FAILED 帧回滚）。"""
        committed_empty = {
            file_id
            for file_id in (set(self.created_empty_files) | self.empty_replace_target_ids)
            if isinstance(receipts.get(file_id), int)
        }
        if committed_empty:
            self._failed_save_file_ids -= committed_empty
            self.filled_file_ids |= committed_empty
        if any(
            isinstance(receipts.get(file_id), int) for file_id in self.unconfirmed_replacement_ids()
        ):
            self._confirmed_tool_file_write = True
        self._recompute_output_flags()

    def _write_effect(self, event_type: str, payload: dict[str, Any]) -> bool:
        """这一帧是否意味着某个文件被写进了正文（或被删除）、项目信息被改了。"""
        if event_type == "file_content":
            if payload.get("file_id") in self.replace_target_ids:
                # 有原文的整章替换，正文在 </file> 之前只在缓冲里，没写完的不覆盖原文：只认落库后补发的结果。
                return False
            return bool(str(payload.get("chunk") or "").strip())
        if event_type != "tool_result" or payload.get("status") != "success":
            return False
        tool_name = payload.get("tool_name")
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        if tool_name == "create_file":
            # 只建了一个空文件（正文随后流式写入）不算；复用已有文件不是本轮写的。
            return bool(str(data.get("content") or "").strip()) and data.get("reused_existing") is not True
        if tool_name == "write_chapter":
            if data.get("action") == "created":
                # 新建的一章同 create_file：空文件不算（结果里沿用的 mutation_applied 只说明建了文件），
                # 正文随 file_content 计入。
                return bool(str(data.get("content") or "").strip()) and data.get("reused_existing") is not True
            # 定位不算；只有整章替换落库后补发的结果（body_saved 且 mutation_applied）算写入。
            return data.get("body_saved") is True and data.get("mutation_applied") is True
        if tool_name == "edit_file":
            mutation_applied = data.get("mutation_applied")
            if isinstance(mutation_applied, bool):
                return mutation_applied
            edits_applied = data.get("edits_applied")
            if isinstance(edits_applied, int):
                return edits_applied > 0
            return True  # 旧格式结果没有这两个字段：成功即算
        if tool_name == "delete_file":
            return payload.get("data") is True or data.get("mutation_applied") is True
        if tool_name == "rename_file":
            return data.get("mutation_applied") is True
        if tool_name == "update_project":
            updated_fields = data.get("updated_fields")
            changed_info = isinstance(updated_fields, list) and any(
                field_name in PROJECT_INFO_FIELDS for field_name in updated_fields
            )
            return changed_info or data.get("project_name_updated") is True
        return False

    def _prose_reached(self, event_type: str, payload: dict[str, Any]) -> bool:
        """真正的回复正文：同一段正文累计到 PROSE_MIN_CHARS 个单位（见 prose_units）。

        文件还在等正文（file_body_pending）时出现的文字是 <file> 前被扣留的引子，不算回复正文。
        """
        if event_type in _SEGMENT_BOUNDARY_EVENTS:
            self._segment_text = ""
            self._segment_reached = False
            return False
        if event_type != "content" or self.file_body_pending():
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
        provider_attempted: bool = False,
        terminal_model_failure: bool = False,
    ) -> tuple[str, bool]:
        """返回 (billing_reason, should_refund)。

        ``provider_attempted`` 表示本轮是否已通过成本预检并真正进入供应商 transport；
        ``terminal_model_failure`` 表示整轮是否因未恢复的上游模型服务错误终止。
        """
        if client_disconnected and not user_stopped and self.saw_terminal_event:
            client_disconnected = False
        if deadline_exceeded and self.saw_terminal_event and not self.saw_error_event:
            deadline_exceeded = False
        if (
            self.saw_terminal_event
            and not self.saw_error_event
            and not unexpected_exception
            and not user_stopped
            and not client_disconnected
            and not deadline_exceeded
        ):
            return "completed", False
        if not provider_attempted:
            if user_stopped:
                return USER_STOPPED_BEFORE_MODEL_BILLING_REASON, True
            if client_disconnected:
                return "client_disconnected_before_model", True
            if deadline_exceeded:
                return ("server_restarting" if server_restarting else "run_deadline_exceeded"), True
            return "pre_model_error", True
        if terminal_model_failure:
            return "upstream_model_error", True
        if user_stopped:
            return "user_stopped", False
        if client_disconnected:
            return "client_disconnected", False
        if deadline_exceeded:
            return ("server_restarting" if server_restarting else "run_deadline_exceeded"), False
        return "non_model_error", False
