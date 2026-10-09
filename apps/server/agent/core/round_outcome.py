"""作者停止 / 断线的一轮怎样收尾：助手消息 id、终态落库、空白占位文件。

见 ``.agents/notes/implemented/architecture/2026-10-09-stop-output-definition-and-round-outcome.md``。

- :class:`RunOutcome` 由 api/agent.py 创建并传给 ``AgentService.process_stream``：
  路由层在取消运行之前写入 ``stop_kind``（``user_stopped`` / ``client_disconnected``），
  service 在取消路径补存部分历史时据此落库 ``stop_reason``，并且即使这一轮什么都
  没产出也写一条助手消息，刷新后作者才看得到「已停止」；落库后通过
  :meth:`RunOutcome.resolve_message_id` 把助手消息 id 交回路由层，停止路径的
  ``done`` 帧据此带上 ``assistant_message_id``。
- :func:`record_round_outcome` 在计费结算之后把结果（是否计入、写入的内容是否保存、
  移除了哪些空白文件）写进这条助手消息的 ``message_metadata.stop_outcome``。
- :func:`remove_empty_placeholders` 只在这一轮被退还时调用：移除这一轮 ``create_file``
  新建、到现在仍然没有正文的文件。已有正文的、复用的、之前就存在的文件一律不动。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

STOP_KIND_USER_STOPPED = "user_stopped"
STOP_KIND_CLIENT_DISCONNECTED = "client_disconnected"
STOP_KINDS: frozenset[str] = frozenset({STOP_KIND_USER_STOPPED, STOP_KIND_CLIENT_DISCONNECTED})
# 写终态时可以被停止原因覆盖的 stop_reason：没有原因、或本来就是取消类的原因。
# 其余（例如可「继续」的 max_turns_exceeded、run_deadline_exceeded）说明这一轮在停止
# 之前已经按自己的方式结束并落库，保留原值，「继续」入口才不会丢。
_OVERWRITABLE_STOP_REASONS: frozenset[str] = frozenset({"", "cancelled", *STOP_KINDS})


@dataclass
class RunOutcome:
    """路由层与 process_stream 之间共享的这一轮收尾信息（同一事件循环内使用）。"""

    stop_kind: str | None = None
    # 作者停止时编辑器里还有没存下的字的文件（前端停止前的保存失败或超时，随 /stop 送来）：
    # 这一轮被退还时不当作空白占位文件移除，见 :meth:`removable`。
    keep_file_ids: set[str] = field(default_factory=set)
    _message_id: asyncio.Future[str | None] | None = field(default=None, repr=False)

    def removable(self, candidates: list[dict[str, str]]) -> list[dict[str, str]]:
        """去掉作者编辑器里还有未保存文字的文件，剩下的才交给 remove_empty_placeholders。"""
        if not self.keep_file_ids:
            return candidates
        return [item for item in candidates if item.get("id") not in self.keep_file_ids]

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

    async def wait_message_id(self, timeout_s: float) -> str | None:
        """等这一轮的助手消息 id；超时或不会落库时返回 None。

        等待中的请求被取消（客户端断开）时照常抛出 CancelledError，交给调用方的断线路径。
        """
        try:
            return await asyncio.wait_for(asyncio.shield(self._future()), timeout=timeout_s)
        except TimeoutError:
            return None


def remove_empty_placeholders(
    project_id: str,
    candidates: list[dict[str, str]],
    *,
    user_id: str | None = None,
) -> list[dict[str, str]]:
    """移除这一轮新建、到现在仍然没有正文的文件（软删除）；返回真正移除了的 {id, title}。

    调用方保证 ``candidates`` 只来自本轮 ``create_file`` 新建的空文件（见
    StreamBillingTracker.removable_placeholders）。这里在项目锁内逐个再查一次：
    文件仍在本项目、没被删、不是文件夹、正文为空、没有子节点，才移除。任何异常只记日志，
    不影响已经完成的退还。
    """
    if not candidates:
        return []
    from sqlmodel import select

    from config.datetime_utils import advance_timestamp, utcnow
    from database import create_session, is_postgres
    from models import File
    from services.file_tree_rules import lock_project_for_files

    removed: list[dict[str, str]] = []
    index_deletes: list[tuple[str, str]] = []
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
                if (
                    file is None
                    or file.project_id != project_id
                    or file.is_deleted
                    or file.file_type == "folder"
                    or (file.content or "").strip()
                ):
                    continue
                has_children = session.exec(
                    select(File.id).where(File.parent_id == file.id, File.is_deleted == False)  # noqa: E712
                ).first()
                if has_children is not None:
                    continue
                now = utcnow()
                file.is_deleted = True
                file.deleted_at = now
                file.updated_at = advance_timestamp(file.updated_at, now=now)
                session.add(file)
                removed.append({"id": file.id, "title": file.title or candidate.get("title", "")})
                index_deletes.append((file.id, file.file_type))
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
        try:
            from services.llama_index import schedule_index_delete

            for entity_id, entity_type in index_deletes:
                schedule_index_delete(
                    project_id=project_id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    user_id=user_id,
                )
        except Exception:  # pragma: no cover - 索引清理失败不影响结果
            pass
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
) -> dict[str, Any]:
    """落库到 message_metadata.stop_outcome 的结构（前端 RoundEndNote 读取）。"""
    outcome: dict[str, Any] = {
        "reason": stop_kind,
        "charged": charged,
        "saved_output": saved_output,
    }
    titles = [item.get("title", "") for item in removed_files or [] if item.get("title")]
    if titles:
        outcome["removed_files"] = titles
    return outcome


def record_round_outcome(message_id: str, *, stop_kind: str, outcome: dict[str, Any]) -> bool:
    """把停止 / 断线的结果写进助手消息的 message_metadata；返回是否写成。"""
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
    "RunOutcome",
    "build_stop_outcome",
    "record_round_outcome",
    "remove_empty_placeholders",
]
