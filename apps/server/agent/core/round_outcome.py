"""作者停止 / 断线的一轮怎样收尾：助手消息 id、终态落库、空白占位文件。

见 ``.agents/notes/implemented/architecture/2026-10-09-stop-output-definition-and-round-outcome.md``。

- :class:`RunOutcome` 由 api/agent.py 创建并传给 ``AgentService.process_stream``：
  路由层在取消运行之前写入 ``stop_kind``（``user_stopped`` / ``client_disconnected``），
  service 在取消路径补存部分历史时据此落库 ``stop_reason``，并且即使这一轮什么都
  没产出也写一条助手消息，刷新后作者才看得到「已停止」；落库后通过
  :meth:`RunOutcome.resolve_message_id` 把助手消息 id 交回路由层，停止路径的
  ``done`` 帧据此带上 ``assistant_message_id``。
- :func:`record_round_outcome` 在计费结算之后把结果（是否计入、写入的内容是否保存、
  写进了哪些文件、移除了哪些空白文件）写进这条助手消息的 ``message_metadata.stop_outcome``。
- :class:`RoundWrittenFiles` 和计费 tracker 一起观察这一轮发出的每一帧，记下写进了正文
  的文件（标题、能确定时的字数），停止 / 断线的终态据此告诉作者写了什么。
- :func:`remove_empty_placeholders` 只在这一轮被退还时调用：移除这一轮 ``create_file``
  新建、到现在仍然没有正文的文件。已有正文的、复用的、之前就存在的文件一律不动。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from agent.constants import coerce_bool
from agent.core.events import UNEXECUTED_TOOL_TEXT_LAYER, iteration_exhausted_status_card
from agent.core.stream_billing import _parse_sse as parse_sse_frame
from agent.openai_agents.events import parse_mcp_tool_payload
from utils.logger import get_logger, log_with_context
from utils.text_metrics import count_words

logger = get_logger(__name__)

STOP_KIND_USER_STOPPED = "user_stopped"
STOP_KIND_CLIENT_DISCONNECTED = "client_disconnected"
STOP_KINDS: frozenset[str] = frozenset({STOP_KIND_USER_STOPPED, STOP_KIND_CLIENT_DISCONNECTED})
# 时限截停落库的 stop_reason（与 runner 的同名原因一致；作者回复「继续」可以接着做）。
RUN_DEADLINE_STOP_REASON = "run_deadline_exceeded"
# 正常收尾的写入回执（不是停止）：只由真实提交结果生成，见 RoundWrittenFiles.completion_receipt。
ROUND_KIND_COMPLETED = "completed"
# 写终态时可以被停止原因覆盖的 stop_reason：没有原因、或本来就是取消类的原因。
# 其余（例如可「继续」的 max_turns_exceeded、run_deadline_exceeded）说明这一轮在停止
# 之前已经按自己的方式结束并落库，保留原值，「继续」入口才不会丢。
_OVERWRITABLE_STOP_REASONS: frozenset[str] = frozenset({"", "cancelled", *STOP_KINDS})


@dataclass
class RunOutcome:
    """路由层与 process_stream 之间共享的这一轮收尾信息（同一事件循环内使用）。"""

    stop_kind: str | None = None
    # 时限截停（墙钟或发布交接提前的时限）：路由层在取消送达 process_stream 之前写入
    # RUN_DEADLINE_STOP_REASON，残稿补存据此落库可「继续」的 stop_reason。
    deadline_stop_reason: str | None = None
    # 本轮单循环引擎已启动的 run 次数（墙钟硬超时暂停卡据此写 iterations_used）。
    runs_started: int = 0
    # 作者停止时编辑器里还有没存下的字的文件（前端停止前的保存失败或超时，随 /stop 送来）：
    # 这一轮被退还时不当作空白占位文件移除，见 :meth:`removable`。
    keep_file_ids: set[str] = field(default_factory=set)
    # 保存等待发生 TimeoutError、后台线程可能仍在提交的文件 id：结果未知，不当空白占位移除。
    # 同步已完成但明确失败 / 已回滚的保存（saved_file_word_counts[id] = None）不进这里，照常可回收。
    uncertain_save_file_ids: set[str] = field(default_factory=set)
    # 最后确认保存的整篇字数；取消补存可能包含尚未发出的控制标记尾部缓冲。
    # None 表示尚无成功保存；后续失败不能抹去之前已保存的正文回执。
    saved_file_word_counts: dict[str, int | None] = field(default_factory=dict)
    # 写工具在线程里执行、还没把结果交回 SDK 的调用：(tool_name, future)。ToolContext 拿着同一个
    # list（比 clear_context() 活得长），工具正常返回时自己移除；截停后留下的由 settle_inflight_tool_writes 等。
    inflight_tool_writes: list[tuple[str, asyncio.Future[Any]]] = field(default_factory=list)
    # 截停后才完成的写工具结果，按 tool_result 帧的格式（tool_name / status / data），交给计费 tracker。
    committed_tool_results: list[dict[str, Any]] = field(default_factory=list)
    _message_id: asyncio.Future[str | None] | None = field(default=None, repr=False)

    async def settle_inflight_tool_writes(self, timeout_s: float) -> None:
        """截停后等还在线程里的写工具（上限 timeout_s），把完成的结构化结果记进 committed_tool_results。

        线程出错、超时或结果解析不了的不记：计费照旧按没有写入处理（偏向作者）。
        """
        pending = list(self.inflight_tool_writes)
        self.inflight_tool_writes.clear()
        if not pending:
            return
        done, _ = await asyncio.wait({future for _name, future in pending}, timeout=timeout_s)
        for tool_name, future in pending:
            if future not in done or future.cancelled() or future.exception() is not None:
                continue
            payload = committed_tool_result(tool_name, future.result())
            if payload is not None:
                self.committed_tool_results.append(payload)

    def removable(self, candidates: list[dict[str, str]]) -> list[dict[str, str]]:
        """去掉不能当空白占位移除的文件，剩下的才交给 remove_empty_placeholders。

        作者编辑器里还有未保存文字的文件不动；已确认保存的文件（包括 0 字）不动；
        保存超时、后台线程可能稍后提交（uncertain_save_file_ids）的文件不动；
        同步明确失败且已回滚（saved_file_word_counts 为 None 且未超时）的空白占位允许回收。
        """
        confirmed = {fid for fid, words in self.saved_file_word_counts.items() if words is not None}
        keep = self.keep_file_ids | self.uncertain_save_file_ids | confirmed
        if not keep:
            return candidates
        return [item for item in candidates if item.get("id") not in keep]

    def _future(self) -> asyncio.Future[str | None]:
        if self._message_id is None:
            self._message_id = asyncio.get_running_loop().create_future()
        return self._message_id

    def resolve_message_id(self, message_id: str | None) -> None:
        """这一轮的助手消息落库了（或确定不会落库：None）。只有第一次调用生效。"""
        try:
            future = self._future()
        except RuntimeError:  # 没有运行中的事件循环
            return
        if not future.done():
            future.set_result(message_id if isinstance(message_id, str) and message_id else None)

    def message_id_settled(self) -> bool:
        """助手消息 id 已经交回（包括确定不会落库的 None）。"""
        return self._message_id is not None and self._message_id.done()

    async def wait_message_id(self, timeout_s: float) -> str | None:
        """等这一轮的助手消息 id；超时或不会落库时返回 None。

        等待中的请求被取消（客户端断开）时照常抛出 CancelledError，交给调用方的断线路径。
        """
        try:
            return await asyncio.wait_for(asyncio.shield(self._future()), timeout=timeout_s)
        except TimeoutError:
            return None


def tool_result_status(tool_name: str, parsed: Any) -> tuple[str, Any]:
    """解析后的工具结果 → (tool_result 帧的 status, data)。StreamAdapter 的实时帧与停止后的结果回放共用。

    有 status 时按它（error / failed / failure 为失败）；没有 status、只有 error 键的旧格式算失败；
    edit_file 全部编辑都没生效（``all_failed``）也算失败。
    """
    if not isinstance(parsed, dict):
        return "success", parsed
    data = parsed.get("data", parsed)
    if "status" in parsed:
        failed = str(parsed.get("status") or "").strip().lower() in {"error", "failed", "failure"}
    else:
        failed = "error" in parsed
    if tool_name == "edit_file" and isinstance(data, dict) and bool(data.get("all_failed")):
        failed = True
    return ("error" if failed else "success"), data


def committed_tool_result(tool_name: str, raw_result: Any) -> dict[str, Any] | None:
    """MCP 工具结果 → tool_result 帧的 payload（状态口径见 tool_result_status）。

    不是 JSON 对象时为 None：实时帧把它当 ``{"raw": ...}`` 发给前端，这里不把它当作提交的证据（偏向作者）。
    """
    parsed = parse_mcp_tool_payload(raw_result)
    if not isinstance(parsed, dict):
        return None
    status, data = tool_result_status(tool_name, parsed)
    return {"tool_name": tool_name, "status": status, "data": data}


# RoundWrittenFiles 关心的帧；其余帧（content、thinking、心跳……）不解析 JSON。
_WRITTEN_FILE_EVENTS: frozenset[str] = frozenset(
    {
        "tool_call",
        "tool_result",
        "file_created",
        "file_content",
        "iteration_exhausted",
    }
)
_FOLDER_FILE_TYPE = "folder"


@dataclass
class _WrittenFile:
    title: str
    # 本轮写进这个文件的正文（create_file 带的正文 + 流式正文）；字数据此按编辑器口径计算。
    body: str = ""
    # 本轮还用 edit_file 改过它：只改了其中几处，整篇字数无从得知，不报字数。
    edited: bool = False
    # 某次 edit_file 只生效了一部分（partial_success），其余几处没有改成。
    partial: bool = False


@dataclass
class RoundWrittenFiles:
    """这一轮写进了正文的文件，按第一次写入的先后排列。

    口径与 ``StreamBillingTracker.write_succeeded`` 一致（见 agent.core.stream_billing）：
    ``create_file`` 带非空正文新建、流式正文非空、``edit_file`` 真的改了文字才算；只建了
    空文件、读文件、失败的调用、文件夹都不算。幂等复用（``reused_existing``）命中一个
    本来就有正文的文件时，中断后的残稿会被截断覆盖保护拒绝落库（见 agent/stream_adapter.py），
    这里不把它列成「已保存」。

    ``delivery`` 用工具实际写入或 StreamAdapter 保存回执确认正文；本轮新建空壳在
    终态仍无确认写入时列为未写入，不把创建过程中的空壳当作失败。
    """

    _files: dict[str, _WrittenFile] = field(default_factory=dict)
    # create_file / file_created 里见过的标题：流式正文帧只带 file_id。
    _titles: dict[str, str] = field(default_factory=dict)
    # 复用时本来就有正文的文件：流式残稿不一定落库。
    _kept_bodies: set[str] = field(default_factory=set)
    _created_empty: dict[str, str] = field(default_factory=dict)
    _tool_writes: set[str] = field(default_factory=set)
    _delete_targets: dict[str, str] = field(default_factory=dict)
    # 空文件纠正失败是请求级粘性事实：临时空文件随后被安全撤回，也不能把这一轮记成 completed。
    _empty_file_incomplete: bool = False
    # 回复里有完整的工具调用只写成了文字、没有执行（layer=unexecuted_tool_text）：同样是请求级
    # 粘性事实，之后的 run 也不能把这一轮记成 completed。
    _unexecuted_tool_text: bool = False
    # 单次 agent run / 一轮里多次 run 的结构化耗尽事实，直到终态都如实记为 incomplete。
    _pending_run_exhaustion: bool = False

    @property
    def unexecuted_tool_text(self) -> bool:
        """回复里有完整的工具调用只写成了文字、没有执行：终态说明不提「可以继续」。"""
        return self._unexecuted_tool_text

    @property
    def known_incomplete(self) -> bool:
        """只合并结构化终止事实，不从模型回复、运行时长或任务内容猜完成度。"""
        return self._empty_file_incomplete or self._unexecuted_tool_text or self._pending_run_exhaustion

    def observe(self, frame: Any) -> None:
        if not isinstance(frame, str) or not frame.startswith("event:"):
            return
        event_type = frame[6 : frame.find("\n")].strip() if "\n" in frame else ""
        if event_type not in _WRITTEN_FILE_EVENTS:
            return
        _, data = parse_sse_frame(frame)
        payload = data if isinstance(data, dict) else {}
        if event_type == "tool_call":
            arguments = payload.get("arguments")
            if payload.get("tool_name") == "delete_file" and isinstance(arguments, dict):
                tool_id, file_id = payload.get("tool_use_id"), arguments.get("id")
                if isinstance(tool_id, str) and tool_id and isinstance(file_id, str) and file_id:
                    self._delete_targets[tool_id] = file_id
        elif event_type == "iteration_exhausted":
            layer = payload.get("layer")
            if layer == "empty_file_correction":
                self._empty_file_incomplete = True
            elif layer == UNEXECUTED_TOOL_TEXT_LAYER:
                self._unexecuted_tool_text = True
            elif layer in {"tool_call", "collaboration"}:
                self._pending_run_exhaustion = True
            # 空文件补不成时已撤销的空文件：它们不再存在，不列为「未确认保存」。
            for item in payload.get("rolled_back_files") or []:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    self._created_empty.pop(item["id"], None)
        elif event_type == "file_created":
            self._remember_title(payload.get("file_id"), payload.get("title"))
        elif event_type == "file_content":
            self._observe_chunk(payload)
        else:
            self._observe_tool_result(payload)

    def files(
        self,
        *,
        final_word_counts: dict[str, int | None] | None = None,
        include_words: bool = True,
    ) -> list[dict[str, Any]]:
        """[{id, title, words?}]：字数只在本轮写下的就是整篇正文时给出。"""
        listed: list[dict[str, Any]] = []
        final_word_counts = final_word_counts or {}
        file_ids = dict.fromkeys([
            *self._files,
            *(fid for fid in final_word_counts if fid in self._titles and fid not in self._kept_bodies),
        ])
        for file_id in file_ids:
            if file_id in final_word_counts and final_word_counts[file_id] is None:
                continue
            item = self._files.get(file_id, _WrittenFile(title=self._titles.get(file_id, "")))
            entry: dict[str, Any] = {"id": file_id, "title": item.title or self._titles.get(file_id, "")}
            if include_words and not item.edited:
                entry["words"] = final_word_counts.get(file_id, count_words(item.body))
            if item.partial:
                entry["partial"] = True
            listed.append(entry)
        return listed

    def _remember_title(self, file_id: Any, title: Any) -> None:
        if isinstance(file_id, str) and file_id and isinstance(title, str) and title:
            self._titles[file_id] = title
            if file_id in self._files and not self._files[file_id].title:
                self._files[file_id].title = title

    def delivery(self, *, final_word_counts: dict[str, int | None]) -> dict[str, Any]:
        """本轮确认写入与新建后尚未写入的文件；流式正文必须有真实保存回执。"""
        written = [item for item in self.files(final_word_counts=final_word_counts)
                   if item["id"] in self._tool_writes or final_word_counts.get(item["id"], 0)]
        written_ids = {item["id"] for item in written}
        unwritten = [{"id": fid, "title": title} for fid, title in self._created_empty.items()
                     if fid not in written_ids]
        return {**({"written_files": written} if written else {}),
                **({"unwritten_files": unwritten} if unwritten else {})}

    def stopped_delivery(
        self,
        *,
        final_word_counts: dict[str, int | None],
        removed_ids: set[str] | frozenset[str] = frozenset(),
    ) -> dict[str, Any]:
        """停止 / 断线（实时 ``done`` 与落库的 ``stop_outcome`` 同一份）的写入回执。

        与 :meth:`delivery` 同一口径，只少两类空壳：退还时真正移除了的；以及一份都没写成时，
        这一轮没往里写过正文（没有流式正文、也没有补存）的空壳——刚建好就停下，不算漏写。
        写过正文却没有确认保存回执的新建文件照样列为未确认保存。
        """
        delivery = self.delivery(final_word_counts=final_word_counts)
        written = delivery.get("written_files")
        unwritten = [
            item for item in delivery.get("unwritten_files", [])
            if item["id"] not in removed_ids
            and (written or self._body_attempted(item["id"], final_word_counts))
        ]
        return {**({"written_files": written} if written else {}),
                **({"unwritten_files": unwritten} if unwritten else {})}

    def saved_output(
        self,
        delivery: dict[str, Any],
        *,
        final_word_counts: dict[str, int | None],
        any_write_succeeded: bool,
    ) -> bool:
        """作者看到的「写入的内容已保存」：只由确认的写入推出，不看流式正文帧。

        有确认写入的文件就是 True。列不出文件的写入（批次中途截停的并行子任务、删除）沿用计费口径
        ``any_write_succeeded``，但这一轮只要有流式正文没拿到确认保存回执就不这么说。
        计费仍按 ``StreamBillingTracker.write_succeeded``，与这里无关。
        """
        if delivery.get("written_files"):
            return True
        return any_write_succeeded and not self._has_unconfirmed_body(final_word_counts)

    def _body_attempted(self, file_id: str, final_word_counts: dict[str, int | None]) -> bool:
        entry = self._files.get(file_id)
        return file_id in final_word_counts or bool(entry and entry.body.strip())

    def _has_unconfirmed_body(self, final_word_counts: dict[str, int | None]) -> bool:
        if any(count is None for count in final_word_counts.values()):
            return True
        return any(
            entry.body.strip() and file_id not in self._tool_writes and not final_word_counts.get(file_id)
            for file_id, entry in self._files.items()
        )

    def completion_receipt(self, *, final_word_counts: dict[str, int | None]) -> dict[str, Any]:
        """正常收尾的回执：只看工具实际结果和保存回执，列出确认写入的文件。"""
        return self.delivery(final_word_counts=final_word_counts)

    def _entry(self, file_id: str) -> _WrittenFile:
        if file_id not in self._files:
            self._files[file_id] = _WrittenFile(title=self._titles.get(file_id, ""))
        return self._files[file_id]

    def _observe_chunk(self, payload: dict[str, Any]) -> None:
        file_id = payload.get("file_id")
        chunk = str(payload.get("chunk") or "")
        if not isinstance(file_id, str) or not file_id or file_id in self._kept_bodies:
            return
        if file_id not in self._files and not chunk.strip():
            return
        self._entry(file_id).body += chunk

    def _observe_tool_result(self, payload: dict[str, Any]) -> None:
        if payload.get("tool_name") == "delete_file":
            file_id = self._delete_targets.pop(payload.get("tool_use_id"), None)
            data = payload.get("data")
            if payload.get("status") == "success":
                if data is True and file_id:
                    self._forget_file(file_id)
                elif isinstance(data, dict) and data.get("mutation_applied") is not False:
                    target_id = data.get("id") if isinstance(data.get("id"), str) else file_id
                    if target_id:
                        self._forget_file(target_id)
            return
        if payload.get("status") != "success":
            return
        tool_name = payload.get("tool_name")
        data = payload.get("data")
        data = data if isinstance(data, dict) else {}
        file_id = data.get("id")
        if not isinstance(file_id, str) or not file_id:
            return
        if tool_name == "write_chapter" and data.get("action") != "created":
            # 整章替换：定位时只记标题（正文随 file_content 记下、以保存回执为准）；
            # 落库后补发的结果确认这次写入。
            self._remember_title(file_id, data.get("title"))
            if data.get("mutation_applied") is True:
                self._tool_writes.add(file_id)
            return
        if tool_name in ("create_file", "write_chapter"):
            if data.get("file_type") == _FOLDER_FILE_TYPE:
                return
            self._remember_title(file_id, data.get("title"))
            content = str(data.get("content") or "")
            if coerce_bool(data.get("reused_existing")):
                if _reused_file_had_body(data, content):
                    self._kept_bodies.add(file_id)
                return
            if content.strip():
                self._entry(file_id).body += content
                self._tool_writes.add(file_id)
            elif data.get("empty_by_request") is not True:
                # 作者只要建文件（empty_by_request）时空文件本身就是交付物：不算未确认保存。
                self._created_empty[file_id] = str(data.get("title") or "")
        elif tool_name == "edit_file":
            mutation_applied = data.get("mutation_applied")
            edits_applied = data.get("edits_applied")
            if mutation_applied is False or (
                not isinstance(mutation_applied, bool)
                and isinstance(edits_applied, int)
                and edits_applied <= 0
            ):
                return
            self._remember_title(file_id, data.get("title"))
            entry = self._entry(file_id)
            entry.edited = True
            if coerce_bool(data.get("partial_success")):
                entry.partial = True
            self._tool_writes.add(file_id)

    def _forget_file(self, file_id: str) -> None:
        self._created_empty.pop(file_id, None)
        self._files.pop(file_id, None)
        self._titles.pop(file_id, None)
        self._tool_writes.discard(file_id)


def _reused_file_had_body(data: dict[str, Any], content: str) -> bool:
    """复用到的文件原本是否已有正文——与 StreamAdapter 截断覆盖保护同一判据。

    生产里复用分支的 content 会被 mcp_tools._elide_reused_file_content 省略成 ""，
    只留 content_elided / original_content_length；StreamAdapter 也是按
    original_content_length（缺失时退回 content 长度）判定「原本非空」，不能只看 content。
    """
    try:
        original_length = int(data.get("original_content_length") or 0)
    except (TypeError, ValueError):
        original_length = 0
    return original_length > 0 or bool(content) or data.get("content_elided") is True


def remove_empty_placeholders(
    project_id: str,
    candidates: list[dict[str, str]],
) -> list[dict[str, str]]:
    """移除这一轮新建、到现在仍然没有正文的文件（软删除）；返回真正移除了的 {id, title}。

    调用方保证 ``candidates`` 只来自本轮 ``create_file`` 新建的空文件（见
    StreamBillingTracker.removable_placeholders），每个带着 create_file 结果里的版本
    ``updated_at``。这里在项目锁内逐个再查一次：文件仍在本项目、没被删、不是文件夹、正文为空、
    没有子节点、版本仍是建好时那个（作者之后改过标题等就保留），才以带版本条件的 UPDATE 移除；
    没带版本的候选不删。任何异常只记日志，不影响已经完成的退还。
    """
    if not candidates:
        return []
    from sqlalchemy import update
    from sqlmodel import select

    from config.datetime_utils import advance_timestamp, utcnow
    from database import create_session, is_postgres
    from models import File
    from services.file_tree_rules import lock_project_for_files

    removed: list[dict[str, str]] = []
    try:
        with create_session() as session:
            lock_project_for_files(session, project_id, exclusive=True)
            for candidate in candidates:
                file_id = candidate.get("id")
                if not file_id:
                    continue
                query = select(File).where(File.id == file_id)
                if is_postgres:
                    query = query.with_for_update(key_share=True)
                file = session.exec(query.execution_options(populate_existing=True)).first()
                created_version = candidate.get("updated_at")
                if (
                    file is None
                    or file.project_id != project_id
                    or file.is_deleted
                    or file.file_type == "folder"
                    or (file.content or "").strip()
                    or not created_version
                    or file.updated_at is None
                    or file.updated_at.isoformat() != created_version
                ):
                    continue
                has_children = session.exec(
                    select(File.id).where(File.parent_id == file.id, File.is_deleted == False)  # noqa: E712
                ).first()
                if has_children is not None:
                    continue
                now = utcnow()
                result = session.exec(
                    update(File)
                    .where(
                        File.id == file.id,
                        File.project_id == project_id,
                        File.is_deleted.is_(False),
                        File.content == file.content,
                        File.updated_at == file.updated_at,
                    )
                    .values(is_deleted=True, deleted_at=now, updated_at=advance_timestamp(file.updated_at, now=now))
                )
                if result.rowcount == 1:
                    removed.append({"id": file.id, "title": file.title or candidate.get("title", "")})
            session.commit()
    except Exception as exc:
        log_with_context(
            logger,
            30,  # WARNING
            "Failed to remove empty placeholder files after a refunded stop",
            project_id=project_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return []

    if removed:
        log_with_context(
            logger,
            20,  # INFO
            "Removed empty placeholder files after a refunded stop",
            project_id=project_id,
            removed_count=len(removed),
        )
    return removed


def build_stop_outcome(
    *,
    stop_kind: str,
    charged: bool,
    saved_output: bool,
    removed_files: list[dict[str, str]] | None = None,
    written_files: list[dict[str, Any]] | None = None,
    unwritten_files: list[dict[str, Any]] | None = None,
    unexecuted_tool_text: bool = False,
) -> dict[str, Any]:
    """落库到 message_metadata.stop_outcome 的结构（前端 RoundEndNote 读取）。

    ``written_files``（:meth:`RoundWrittenFiles.files`）为空时不写这个键：旧消息和拿不到
    文件的写入（如删除）在前端照旧只显示「已写入的内容已保存」。
    """
    outcome: dict[str, Any] = {
        "reason": stop_kind,
        "charged": charged,
        "saved_output": saved_output,
    }
    titles = [item.get("title", "") for item in removed_files or [] if item.get("title")]
    if titles:
        outcome["removed_files"] = titles
    if written_files:
        outcome["written_files"] = written_files
    if unwritten_files:
        outcome["unwritten_files"] = unwritten_files
    if unexecuted_tool_text:
        # 有一步只写成了文字、没有执行（见 RoundWrittenFiles.unexecuted_tool_text）。
        outcome["unexecuted_tool_text"] = True
    return outcome


def record_round_outcome(
    message_id: str,
    *,
    stop_kind: str,
    outcome: dict[str, Any],
    pause_card: dict[str, Any] | None = None,
) -> bool:
    """把停止 / 断线的结果写进助手消息的 message_metadata；返回是否写成。

    ``pause_card``：路由层在墙钟时限上补发的暂停卡（iteration_exhausted 事件数据）。它不经过
    process_stream，补存的部分历史里没有它：这里追加进 status_cards（「继续」路由读 lastAgent）
    和已有的展示时间线末尾，刷新后作者看到同一张卡和继续按钮。
    """
    from database import create_session
    from models import ChatMessage

    try:
        with create_session() as session:
            message = session.get(ChatMessage, message_id)
            if message is None or message.role != "assistant":
                return False
            try:
                metadata = json.loads(message.message_metadata) if message.message_metadata else {}
            except (TypeError, ValueError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}
            existing_reason = metadata.get("stop_reason")
            if not isinstance(existing_reason, str) or existing_reason in _OVERWRITABLE_STOP_REASONS:
                metadata["stop_reason"] = stop_kind
            metadata["stop_outcome"] = outcome
            if pause_card is not None:
                cards = metadata.get("status_cards")
                metadata["status_cards"] = [
                    *(cards if isinstance(cards, list) else []),
                    iteration_exhausted_status_card(pause_card),
                ]
                display_events = metadata.get("display_events")
                if isinstance(display_events, list) and display_events:
                    display_events.append({"type": "iteration_exhausted", "data": dict(pause_card)})
            message.message_metadata = json.dumps(metadata, ensure_ascii=False)
            session.add(message)
            session.commit()
            return True
    except Exception as exc:
        log_with_context(
            logger,
            30,  # WARNING
            "Failed to record stopped round outcome",
            message_id=message_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False


__all__ = [
    "STOP_KINDS",
    "STOP_KIND_CLIENT_DISCONNECTED",
    "STOP_KIND_USER_STOPPED",
    "ROUND_KIND_COMPLETED",
    "RUN_DEADLINE_STOP_REASON",
    "RoundWrittenFiles",
    "RunOutcome",
    "build_stop_outcome",
    "record_round_outcome",
    "remove_empty_placeholders",
]
