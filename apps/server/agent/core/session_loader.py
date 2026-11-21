"""
Session loader helpers for legacy graph row replay, author edits, and session-written files.
"""

import contextlib
import json
import os
import threading
import time
from datetime import datetime
from typing import Any

from sqlalchemy import desc, func
from sqlmodel import Session, and_, select

from config.datetime_utils import normalize_datetime_to_utc
from utils.logger import get_logger, log_with_context

# 回放「上一轮没来得及执行的审稿返修要求」的终止原因：时限截停与停止 / 断线（取消补存）。
_REWORK_REPLAY_STOP_REASONS: frozenset[str] = frozenset(
    {"run_deadline_exceeded", "cancelled", "user_stopped", "client_disconnected"}
)
# 图写下的旧行里「未执行的审稿返修要求」回放块的固定标题。
PENDING_REWORK_HEADER = "[上一轮未执行的审稿返修要求]"
PENDING_REWORK_HEADER_EN = "[Unapplied review revisions from the previous round]"


def _breadcrumb_lang_is_english() -> bool:
    return (os.getenv("AGENT_HISTORY_BREADCRUMB_LANG") or "").strip().lower().startswith("en")


# 多 agent 图（已删除）里工具集含写文件工具的角色。只用于回放图写下的旧行，冻结为删除前 registry 的结果。
_LEGACY_GRAPH_WRITE_ROLES = frozenset({"planner", "hook_designer", "writer", "loop"})


def _agent_has_write_tools(agent_type: Any) -> bool:
    """图写下的旧行里，这个角色有没有写文件工具（审稿人 quality_reviewer 没有）。"""
    return isinstance(agent_type, str) and agent_type in _LEGACY_GRAPH_WRITE_ROLES


def _format_pending_rework(packet: dict[str, Any], *, english: bool) -> str:
    reason = str(packet.get("reason") or "").strip()
    context = str(packet.get("context") or "").strip()
    todo = [str(item).strip() for item in packet.get("todo") or [] if str(item).strip()]
    if english:
        lines = [
            PENDING_REWORK_HEADER_EN,
            "The reviewer handed these revisions over last round; they were not applied before the round ended. "
            "This is not a new authorization: what to do and which files may change this round still follow the "
            "author's current message and this round's routing.",
        ]
        labels = ("Reason: ", "Notes:", "To do:")
    else:
        lines = [
            PENDING_REWORK_HEADER,
            "上一轮审稿人交来的返修要求，这一轮结束前没有执行。这不是新的授权：本轮做什么、能改哪些文件，"
            "仍以作者这条消息和本轮路由为准。",
        ]
        labels = ("原因：", "说明：", "待改：")
    if reason:
        lines.append(f"{labels[0]}{reason}")
    if context and context != reason:
        lines.extend([labels[1], context])
    if todo:
        lines.append(labels[2])
        lines.extend(f"- {item}" for item in todo)
    return "\n".join(lines)

logger = get_logger(__name__)

WORKING_SET_USER_MESSAGE_LABEL = "【本轮用户消息】"
# 本轮开始时给哪些文件算作者手改差异：焦点 / 附加与本会话 AI 写过的正文文件，按这个顺序最多这么多个。
AUTHOR_EDITS_MAX_FILES = 30
# 算作者手改的总墙钟预算（秒）：用尽后剩余文件归入 unchecked_titles（明说未核对），避免阻塞本轮启动。
AUTHOR_EDITS_TIME_BUDGET_S = 2.0
# session_written_file_ids 每次读多少条 assistant 行（只读 tool_calls 列）：凑够名额就不再往前读。
SESSION_WRITTEN_FILES_PAGE_ROWS = 20
# 本轮开始时从本会话结转未变更文件快照的上限（最多结转这么多文件、最多往前翻这么多页，控制 prepare_context 耗时）。
SESSION_SNAPSHOT_MAX_FILES = 20
SESSION_SNAPSHOT_MAX_PAGES = 2

# 工具名 → 面包屑里的动作（读取类只认按 id 精确读取的 query_files）
_BREADCRUMB_VERBS_ZH = {
    "write_chapter": "已写入章节",
    "create_file": "已创建文件",
    "edit_file": "已编辑文件",
    "delete_file": "已删除文件",
    "rename_file": "已改名文件",
    "query_files": "已读取文件",
}
_BREADCRUMB_VERBS_EN = {
    "write_chapter": "Wrote chapter",
    "create_file": "Created file",
    "edit_file": "Edited file",
    "delete_file": "Deleted file",
    "rename_file": "Renamed file",
    "query_files": "Read file",
}
# 没有落下结果的工具调用（回合中途断开）：做没做成不知道。
_BREADCRUMB_UNKNOWN_ZH = {
    "write_chapter": "写入章节（结果未知）",
    "create_file": "创建文件（结果未知）",
    "edit_file": "编辑文件（结果未知）",
    "delete_file": "删除文件（结果未知）",
    "rename_file": "改名文件（结果未知）",
    "query_files": "读取文件（结果未知）",
}
_BREADCRUMB_UNKNOWN_EN = {
    "write_chapter": "Write chapter (outcome unknown)",
    "create_file": "Create file (outcome unknown)",
    "edit_file": "Edit file (outcome unknown)",
    "delete_file": "Delete file (outcome unknown)",
    "rename_file": "Rename file (outcome unknown)",
    "query_files": "Read file (outcome unknown)",
}
_FAILED_TOOL_STATUSES = {"error", "failed", "failure"}

# 补问卡文本上限：有正文轮次追加进回放的补问卡按此上限截断；
# 只有状态卡、没有正文的轮次仍回放完整卡片。
_CLARIFICATION_TEXT_MAX_CHARS = 400
_CLARIFICATION_DETAIL_MAX_CHARS = 160
_CLARIFICATION_MAX_DETAILS = 8
_PENDING_TOOL_STATUS = "pending"
# parallel_execute 子任务里改动（至少一部分）已经落地的状态。
_APPLIED_PARALLEL_TASK_STATUSES = {"completed", "partial"}


class SessionLoader:
    """
    Helpers for legacy graph row replay, author-edits diffing, and session-written files.
    """

    def __init__(
        self,
        project_id: str,
        user_id: str | None,
    ):
        self.project_id = project_id
        self.user_id = user_id

    def author_edits_for_files(
        self,
        session: Session,
        ordered: list[str],
        fallback_titles: dict[str, str],
        *,
        time_budget_s: float | None = AUTHOR_EDITS_TIME_BUDGET_S,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, set[str], dict[str, Any] | None]:
        """按 ordered 的先后（至多 AUTHOR_EDITS_MAX_FILES 个、总计不超过 time_budget_s 秒）算作者手改，
        返回 (<author_edits> 块, 作者改过的 id, id → File)。

        查询文件失败时块里明说「没算出来」，第三项为 None；预算用尽或收到 cancel_event 时，
        剩余文件归入 unchecked_titles（明说未核对）。
        """
        from agent.tools.author_changes import AuthorChanges, author_changes, render_author_edits_block
        from models import File

        try:
            files = session.exec(
                select(File).where(
                    File.id.in_(ordered),
                    File.project_id == self.project_id,
                    File.is_deleted.is_(False),
                )
            ).all()
        except Exception as exc:
            with contextlib.suppress(Exception):
                session.rollback()
            log_with_context(
                logger,
                30,  # WARNING
                "Author edits build failed",
                project_id=self.project_id,
                user_id=self.user_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            checked_ids, unchecked_ids = ordered[:AUTHOR_EDITS_MAX_FILES], ordered[AUTHOR_EDITS_MAX_FILES:]
            failed_entries = [
                (
                    file_id,
                    fallback_titles.get(file_id) or "（未命名）",
                    AuthorChanges(hunks=[], complete=False, failed=True),
                )
                for file_id in checked_ids
            ]
            return (
                render_author_edits_block(
                    failed_entries,
                    unchecked_titles=[
                        f"《{fallback_titles.get(file_id) or '（未命名）'}》"
                        for file_id in unchecked_ids
                    ],
                ),
                set(),
                None,
            )
        by_id = {str(f.id): f for f in files if f.file_type != "folder"}
        present = [file_id for file_id in ordered if file_id in by_id]
        checked, unchecked = present[:AUTHOR_EDITS_MAX_FILES], present[AUTHOR_EDITS_MAX_FILES:]

        def title_of(file: Any) -> str:
            return str(file.title or "").strip() or "（未命名）"

        entries = []
        edited_ids: set[str] = set()
        unchecked_ids = list(unchecked)
        deadline = (
            time.monotonic() + max(0.0, time_budget_s)
            if time_budget_s is not None
            else None
        )
        for index, file_id in enumerate(checked):
            if cancel_event is not None and cancel_event.is_set():
                unchecked_ids = [*checked[index:], *unchecked_ids]
                break
            if index > 0 and deadline is not None and time.monotonic() >= deadline:
                unchecked_ids = [*checked[index:], *unchecked_ids]
                log_with_context(
                    logger,
                    20,  # INFO
                    "Author edits time budget reached; remaining files left unchecked",
                    project_id=self.project_id,
                    user_id=self.user_id,
                    checked_count=index,
                    unchecked_count=len(unchecked_ids),
                    time_budget_s=time_budget_s,
                )
                break
            file = by_id[file_id]
            try:
                with session.begin_nested() as nested:
                    changes = author_changes(session, file)
                    if changes is not None and changes.failed:
                        nested.rollback()
            except Exception:
                changes = AuthorChanges(hunks=[], complete=False, failed=True)
            if changes is not None:
                entries.append((file_id, title_of(file), changes))
                if changes.hunks:
                    # 只核对不完整（窗口外 / 回放失败）的文件不标「作者改过」：不知道，就不下结论。
                    edited_ids.add(file_id)
        block = render_author_edits_block(
            entries, unchecked_titles=[f"《{title_of(by_id[file_id])}》" for file_id in unchecked_ids]
        )
        return block, edited_ids, by_id

    def session_written_file_ids(
        self,
        session: Session,
        *,
        chat_session_id: str,
        limit: int,
        file_types: tuple[str, ...] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> list[str]:
        """这个会话里 AI 成功写过（create_file / write_chapter / edit_file / rename_file）的文件 id，新 → 旧，至多 limit 个。

        之后被删掉的（结果未知的删除不算）不列。只读 assistant 行持久化的 tool_calls，不读正文。
        给了 file_types 时先按本项目、未删除、这些类型筛，再计 limit：别的类型的文件不占名额。
        """
        from models import ChatMessage, File

        query = (
            select(ChatMessage.tool_calls)
            .where(
                and_(
                    ChatMessage.session_id == chat_session_id,
                    ChatMessage.role == "assistant",
                )
            )
            .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        )

        def tool_payloads() -> Any:
            # 新 → 旧按页读：会话很长时不一次把所有行的 tool_calls 读进来。
            offset = 0
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    return
                page = session.exec(query.offset(offset).limit(SESSION_WRITTEN_FILES_PAGE_ROWS)).all()
                yield from page
                if len(page) < SESSION_WRITTEN_FILES_PAGE_ROWS:
                    return
                offset += SESSION_WRITTEN_FILES_PAGE_ROWS

        written: list[str] = []
        seen: set[str] = set()
        deleted: set[str] = set()
        pending: list[str] = []

        def accept_pending() -> None:
            if file_types is None:
                accepted = set(pending)
            else:
                accepted = set(
                    session.exec(
                        select(File.id).where(
                            File.id.in_(pending),
                            File.project_id == self.project_id,
                            File.is_deleted.is_(False),
                            File.file_type.in_(file_types),
                        )
                    ).all()
                )
            written.extend(fid for fid in pending if fid in accepted and fid not in deleted)
            pending.clear()

        for tool_calls in tool_payloads():
            actions = self._extract_file_actions(tool_calls)
            for action in actions:
                if action["name"] == "delete_file" and action["file_id"] and not action.get("outcome_unknown"):
                    deleted.add(action["file_id"])
            for action in actions:
                file_id = action["file_id"]
                if (
                    not file_id
                    or action["name"] in ("delete_file", "query_files")
                    or action.get("outcome_unknown")
                    or file_id in deleted
                    or file_id in seen
                ):
                    continue
                seen.add(file_id)
                pending.append(file_id)
            if pending:
                accept_pending()
            if len(written) >= limit:
                return written[:limit]
        return written

    @staticmethod
    def _same_utc_timestamp(db_dt: Any, iso_str: Any) -> bool:
        """Compare a DB datetime against an ISO timestamp string in UTC."""
        if not isinstance(db_dt, datetime) or not isinstance(iso_str, str) or not iso_str.strip():
            return False
        raw = iso_str.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return False
        return normalize_datetime_to_utc(db_dt) == normalize_datetime_to_utc(parsed)

    def session_read_snapshots(
        self,
        session: Session,
        *,
        chat_session_id: str,
        exclude_ids: set[str] | None = None,
        limit: int = SESSION_SNAPSHOT_MAX_FILES,
        cancel_event: threading.Event | None = None,
    ) -> dict[str, str]:
        """本会话此前轮次读过或写过、且至今未被修改的文件快照（file_id → 当前全文）。

        目的：单循环跨轮时，历史里已经回放了前几轮的 query_files / write_chapter / edit_file，
        但旧实现只把 <turn_context> 里的文件写进 read_snapshots["initial"]，导致跨轮 edit_file
        被误判为没看过文件（exact_only=True）而触发大量假阳性 anchor_not_found。

        安全不变量（绝不绕过 StaleContentError）：
        - 只有当前 DB 状态与本会话最近一次工具触达时的精确凭证一致，才结转进 read_snapshots["initial"]：
          - query_files 全文读 / create_file 直接写入：正文逐字一致且（若有）updated_at 一致；
          - edit_file / write_chapter(replaced)：undo.expected_after_content_sha256 与 expected_after_updated_at 一致；
          - write_chapter(created) / create_file(<file> 落库)：file.updated_at <= 该轮 assistant 行创建时间，
            且最新 FileVersion 为 AI 写入且正文与当前 file.content 逐字一致；
        - 只要作者在轮间改过文件（哪怕未推进 updated_at 或未建版本），一律不结转。
        - 遇到有效压缩点后停止往前扫描（压缩前的工具结果已不在模型上下文中）。
        - 最多扫 SESSION_SNAPSHOT_MAX_PAGES 页、最多取 limit 个文件，单次批量 SELECT File。
        """
        from agent.loop.history import valid_compaction
        from agent.tools.author_changes import AI, version_actor
        from models import ChatMessage, File
        from models.file_version import FileVersion
        from services.features.file_version_service import FileVersionService, content_sha256

        if limit <= 0 or (cancel_event is not None and cancel_event.is_set()):
            return {}

        excluded = set(exclude_ids or ())
        deleted: set[str] = set()
        # 较新的写工具若没有可验证凭证，也占位阻止更早的读记录盖过它。
        unverifiable: set[str] = set()
        candidates: dict[str, dict[str, Any]] = {}

        query = (
            select(ChatMessage.tool_calls, ChatMessage.created_at, ChatMessage.message_metadata)
            .where(
                and_(
                    ChatMessage.session_id == chat_session_id,
                    ChatMessage.role == "assistant",
                )
            )
            .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        )

        hit_compaction = False
        for page_index in range(SESSION_SNAPSHOT_MAX_PAGES):
            if cancel_event is not None and cancel_event.is_set():
                return {}
            rows = session.exec(
                query.offset(page_index * SESSION_WRITTEN_FILES_PAGE_ROWS).limit(SESSION_WRITTEN_FILES_PAGE_ROWS)
            ).all()
            if not rows:
                break
            for tool_calls_raw, row_created_at, metadata_raw in rows:
                meta = self._parse_json_value(metadata_raw)
                if isinstance(meta, dict) and valid_compaction(meta) is not None:
                    # 压缩发生在本轮开始前：本行的工具调用仍在压缩后历史里，更早的行不再扫描。
                    hit_compaction = True

                tool_calls = self._parse_json_value(tool_calls_raw)
                if isinstance(tool_calls, list):
                    for tc in reversed(tool_calls):
                        if not isinstance(tc, dict):
                            continue
                        name = str(tc.get("name") or "").strip()
                        status = str(tc.get("status") or "").strip().lower()
                        if status in _FAILED_TOOL_STATUSES or status == _PENDING_TOOL_STATUS or tc.get("error"):
                            continue

                        result = self._parse_json_value(tc.get("result"))
                        if name == "delete_file":
                            res_data = (
                                result.get("data")
                                if isinstance(result, dict) and "data" in result
                                else result
                            )
                            if (
                                result is not False
                                and res_data is not False
                                and not (
                                    isinstance(res_data, dict)
                                    and (res_data.get("mutation_applied") is False or res_data.get("restored") is True)
                                )
                            ):
                                fid = self._extract_breadcrumb_file_id(tc)
                                if fid:
                                    deleted.add(fid)
                            continue

                        if name == "query_files" and status == "success" and isinstance(result, list):
                            for item in reversed(result):
                                if not isinstance(item, dict):
                                    continue
                                fid = str(item.get("id") or "").strip()
                                if (
                                    not fid
                                    or fid in excluded
                                    or fid in deleted
                                    or fid in unverifiable
                                    or fid in candidates
                                ):
                                    continue
                                content = item.get("content")
                                if (
                                    isinstance(content, str)
                                    and item.get("file_type") != "folder"
                                    and not item.get("content_truncated")
                                    and not item.get("content_partial")
                                ):
                                    updated_at = item.get("updated_at")
                                    candidates[fid] = {
                                        "kind": "read",
                                        "content": content,
                                        "updated_at": (
                                            updated_at.strip()
                                            if isinstance(updated_at, str) and updated_at.strip()
                                            else None
                                        ),
                                    }
                                    if len(candidates) >= limit:
                                        break
                            if len(candidates) >= limit:
                                break
                            continue

                        if name == "edit_file" and isinstance(result, dict):
                            if result.get("all_failed"):
                                continue
                            applied = status == "success" or (
                                status == "partial" and result.get("mutation_applied") is True
                            )
                            if not applied or result.get("mutation_applied") is False:
                                continue
                            fid = self._extract_breadcrumb_file_id(tc)
                            if not fid or fid in excluded or fid in deleted or fid in unverifiable or fid in candidates:
                                continue
                            undo = result.get("undo")
                            exp_ts = (
                                undo.get("expected_after_updated_at").strip()
                                if isinstance(undo, dict)
                                and isinstance(undo.get("expected_after_updated_at"), str)
                                and undo.get("expected_after_updated_at").strip()
                                else None
                            )
                            exp_sha = (
                                undo.get("expected_after_content_sha256").strip()
                                if isinstance(undo, dict)
                                and isinstance(undo.get("expected_after_content_sha256"), str)
                                and undo.get("expected_after_content_sha256").strip()
                                else None
                            )
                            if exp_ts or exp_sha:
                                candidates[fid] = {
                                    "kind": "undo_write",
                                    "updated_at": exp_ts,
                                    "sha256": exp_sha,
                                }
                            else:
                                unverifiable.add(fid)
                            if len(candidates) >= limit:
                                break
                            continue

                        if name in {"write_chapter", "create_file"} and status == "success" and isinstance(result, dict):
                            fid = self._extract_breadcrumb_file_id(tc)
                            if not fid or fid in excluded or fid in deleted or fid in unverifiable or fid in candidates:
                                continue
                            if result.get("file_type") == "folder":
                                continue
                            if name == "write_chapter" and result.get("action") == "replaced":
                                if result.get("body_saved") is not True:
                                    continue
                                undo = result.get("undo")
                                exp_ts = (
                                    undo.get("expected_after_updated_at").strip()
                                    if isinstance(undo, dict)
                                    and isinstance(undo.get("expected_after_updated_at"), str)
                                    and undo.get("expected_after_updated_at").strip()
                                    else None
                                )
                                exp_sha = (
                                    undo.get("expected_after_content_sha256").strip()
                                    if isinstance(undo, dict)
                                    and isinstance(undo.get("expected_after_content_sha256"), str)
                                    and undo.get("expected_after_content_sha256").strip()
                                    else None
                                )
                                if exp_ts or exp_sha:
                                    candidates[fid] = {
                                        "kind": "undo_write",
                                        "updated_at": exp_ts,
                                        "sha256": exp_sha,
                                    }
                                else:
                                    unverifiable.add(fid)
                            elif result.get("awaiting_body") is True or result.get("body_in_reply") is True:
                                if result.get("body_saved") is False:
                                    continue
                                if isinstance(row_created_at, datetime):
                                    candidates[fid] = {
                                        "kind": "streamed_create",
                                        "turn_created_at": row_created_at,
                                    }
                                else:
                                    unverifiable.add(fid)
                            elif isinstance(result.get("content"), str):
                                updated_at = result.get("updated_at")
                                candidates[fid] = {
                                    "kind": "read",
                                    "content": result["content"],
                                    "updated_at": (
                                        updated_at.strip()
                                        if isinstance(updated_at, str) and updated_at.strip()
                                        else None
                                    ),
                                }
                            if len(candidates) >= limit:
                                break
                if len(candidates) >= limit or hit_compaction:
                    break
            if len(candidates) >= limit or hit_compaction or len(rows) < SESSION_WRITTEN_FILES_PAGE_ROWS:
                break

        if not candidates or (cancel_event is not None and cancel_event.is_set()):
            return {}

        files = session.exec(
            select(File).where(
                File.id.in_(list(candidates)),
                File.project_id == self.project_id,
                File.is_deleted.is_(False),
            )
        ).all()
        by_id = {str(f.id): f for f in files if f.file_type != "folder"}
        if not by_id:
            return {}

        snapshots: dict[str, str] = {}
        streamed_check_ids: list[str] = []

        for fid, spec in candidates.items():
            file = by_id.get(fid)
            if file is None:
                continue
            current_content = file.content or ""
            kind = spec["kind"]
            if kind == "read":
                if current_content != spec["content"]:
                    continue
                if spec["updated_at"] is not None and not self._same_utc_timestamp(
                    file.updated_at, spec["updated_at"]
                ):
                    continue
                snapshots[fid] = current_content
            elif kind == "undo_write":
                if spec["sha256"] is not None and content_sha256(current_content) != spec["sha256"]:
                    continue
                if spec["updated_at"] is not None and not self._same_utc_timestamp(
                    file.updated_at, spec["updated_at"]
                ):
                    continue
                snapshots[fid] = current_content
            elif (
                kind == "streamed_create"
                and isinstance(file.updated_at, datetime)
                and normalize_datetime_to_utc(file.updated_at)
                <= normalize_datetime_to_utc(spec["turn_created_at"])
            ):
                streamed_check_ids.append(fid)

        if streamed_check_ids and not (cancel_event is not None and cancel_event.is_set()):
            latest_subq = (
                select(
                    FileVersion.file_id,
                    func.max(FileVersion.version_number).label("max_ver"),
                )
                .where(FileVersion.file_id.in_(streamed_check_ids))
                .group_by(FileVersion.file_id)
                .subquery()
            )
            latest_rows = session.exec(
                select(FileVersion).join(
                    latest_subq,
                    and_(
                        FileVersion.file_id == latest_subq.c.file_id,
                        FileVersion.version_number == latest_subq.c.max_ver,
                    ),
                )
            ).all()
            ai_latest = {
                str(ver.file_id): ver
                for ver in latest_rows
                if version_actor(ver) == AI
            }
            if ai_latest:
                contents_by_id = FileVersionService()._get_contents_for_versions(session, ai_latest)
                for fid, expected_content in contents_by_id.items():
                    file = by_id.get(fid)
                    if file is not None and (file.content or "") == expected_content:
                        snapshots[fid] = file.content or ""

        return snapshots

    @staticmethod
    def attach_working_set_to_user_content(user_content: str, working_set_context: str) -> str:
        """把本轮上下文块放在本轮用户消息之前（同一条 user 消息里）。

        放在这里而不是系统提示里：系统提示与历史消息保持不变，前缀缓存可以
        一直命中到上一轮为止；这些块每轮都会变，只能放在最末尾。也不单独插一条
        user 消息，避免连续两条 user 消息。
        """
        if not working_set_context:
            return user_content
        return f"{working_set_context}\n\n{WORKING_SET_USER_MESSAGE_LABEL}\n{user_content}"

    def _format_chat_message_for_history(self, msg: Any, *, latest_assistant: bool = False) -> dict[str, Any]:
        """Convert ChatMessage ORM row to dict for agent history + token math.

        latest_assistant：这是会话里最近一条 assistant 消息（上一轮的回复）。只有它会附上
        「上一轮未执行的审稿返修要求」：之后又有新的一轮，那份要求就不再是“上一轮”的
        （下一轮的 writer 已经处理过，或作者换了话题），不再给后续轮次重复注入。
        """
        msg_data: dict[str, Any] = {
            "id": msg.id,
            "role": msg.role,
            "content": msg.content,
        }
        rework_block = ""

        # Include persisted assistant metadata needed for legacy history replay.
        if msg.role == "assistant" and getattr(msg, "message_metadata", None):
            try:
                metadata = json.loads(msg.message_metadata)
            except (TypeError, json.JSONDecodeError):
                metadata = {}

            if isinstance(metadata, dict):
                stop_reason = metadata.get("stop_reason")
                status_cards = metadata.get("status_cards")
                if isinstance(stop_reason, str) and stop_reason:
                    msg_data["stop_reason"] = stop_reason
                if latest_assistant:
                    rework_block = self._pending_rework_history_content(
                        metadata, getattr(msg, "tool_calls", None)
                    )
                if isinstance(status_cards, list):
                    synthesized_content = self._build_status_cards_history_content(status_cards)
                    if synthesized_content and not str(msg_data.get("content") or "").strip():
                        self._append_text_to_content(msg_data, synthesized_content)
                    else:
                        # 有正文时其他状态卡摘要是噪音，但补问卡的问题和选项不一定写在
                        # 正文里：不放进回放，作者回复选其中一项时模型看不到选项内容。
                        clarification = self._clarification_cards_history_content(status_cards)
                        if clarification:
                            self._append_text_to_content(msg_data, clarification)

        # Tool-turn breadcrumbs (cross-request tool memory).
        #
        # ChatMessage.tool_calls persists what the assistant DID last turn
        # (file create/edit/delete, exact-id query_files reads), but on a new
        # request the agent otherwise only sees the prose reply — it has no
        # record that it created/edited/read a file. We synthesize a COMPACT
        # assistant TEXT breadcrumb here so that memory survives across requests.
        # 文件的当前全文不放在这里（会被历史窗口按 token 预算裁掉），而是由
        # attach_working_set 另行渲染、拼到本轮用户消息前。
        #
        # CRITICAL: this is plain TEXT, never raw tool_use/tool_result blocks.
        # Re-emitting structured tool blocks from a prior turn risks orphaned
        # tool_call_id errors in the SDK (see the comment in
        # openai_agents/runner.py:extract_text_from_message_content). Mirrors the
        # text-synthesis pattern used for status cards above.
        if msg.role == "assistant" and getattr(msg, "tool_calls", None):
            breadcrumb = self._build_tool_calls_history_content(msg.tool_calls)
            if breadcrumb:
                self._append_text_to_content(msg_data, breadcrumb)

        # 审稿人要求返修、返修的 agent 没来得及跑：作者只看到简短说明，完整要求在这里只给模型看
        # （见 _pending_rework_history_content）。
        if rework_block:
            self._append_text_to_content(msg_data, rework_block)

        return msg_data

    def _pending_rework_history_content(self, metadata: dict[str, Any], tool_calls_raw: Any) -> str:
        """上一轮审稿人交给可写 agent、却没有执行的返修要求（只给模型看的回放块）。

        载体是审稿 run 期间就已落库的 ``handoff_to_agent`` 调用（参数与真实工具结果）：
        正常收尾、取消补存、硬超时补存都会带上它，比作者可见的收尾说明更早进入历史。
        只看结构化事实：

        - 这一轮停在时限或停止 / 断线上（``stop_reason``）；正常结束（例如达到审查上限、
          作者自己拍板）不回放，那条路径的完整意见本来就写在可见回复里。
        - 按 ``display_events`` 的先后：交接调用发生在 quality_reviewer 运行期间、工具结果是
          ``handoff``、目标 agent 有写文件工具，且之后没有任何 agent 再开始运行。

        回放块说明这不是新的授权：本轮的范围与权限仍以作者这条消息和本轮路由为准。
        """
        if metadata.get("stop_reason") not in _REWORK_REPLAY_STOP_REASONS:
            return ""
        display_events = metadata.get("display_events")
        tool_calls = self._parse_json_value(tool_calls_raw)
        if not isinstance(display_events, list) or not isinstance(tool_calls, list):
            return ""
        current_agent = ""
        pending: dict[str, Any] | None = None
        for event in display_events:
            if not isinstance(event, dict):
                continue
            if event.get("type") == "agent_selected":
                data = event.get("data")
                current_agent = str((data or {}).get("agent_type") or "") if isinstance(data, dict) else ""
                pending = None
                continue
            if event.get("type") != "tool_call" or current_agent != "quality_reviewer":
                continue
            index = event.get("tool_call_index")
            call = tool_calls[index] if isinstance(index, int) and 0 <= index < len(tool_calls) else None
            if not isinstance(call, dict) or call.get("name") != "handoff_to_agent":
                continue
            result = call.get("result")
            if (
                isinstance(result, dict)
                and result.get("status") == "handoff"
                and _agent_has_write_tools(result.get("target_agent"))
            ):
                pending = result
        if pending is None:
            return ""
        return _format_pending_rework(pending, english=_breadcrumb_lang_is_english())

    def _append_text_to_content(
        self,
        msg_data: dict[str, Any],
        text: str,
    ) -> None:
        """把一段合成文本（工具面包屑 / 状态卡摘要）追加为 assistant 的纯文本内容。"""
        content = msg_data.get("content")
        existing_text = str(content or "").strip()
        if existing_text:
            msg_data["content"] = f"{content}\n\n{text}"
        else:
            msg_data["content"] = text

    @staticmethod
    def _parse_json_value(value: Any) -> Any:
        """tool_calls / arguments 可能以 JSON 字符串持久化，统一解析成 Python 值。"""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return None
        return value

    @staticmethod
    def _find_title_for_id(payload: Any, file_id: str, depth: int = 0) -> str | None:
        """在 query_files 等工具结果里找到 id 对应的标题（结果可能是 dict / list / 嵌套 data）。"""
        if depth > 4 or payload is None:
            return None
        if isinstance(payload, dict):
            for key in ("id", "file_id", "fileId"):
                if str(payload.get(key) or "").strip() == file_id:
                    title = payload.get("title")
                    if isinstance(title, str) and title.strip():
                        return title.strip()
            for value in payload.values():
                if isinstance(value, dict | list):
                    found = SessionLoader._find_title_for_id(value, file_id, depth + 1)
                    if found:
                        return found
            return None
        if isinstance(payload, list):
            for value in payload:
                found = SessionLoader._find_title_for_id(value, file_id, depth + 1)
                if found:
                    return found
        return None

    @staticmethod
    def _first_id_in(payload: Any, keys: tuple[str, ...] = ("id", "file_id", "fileId")) -> str | None:
        """从 dict（或其 data 字段）里取第一个非空 id。"""
        for candidate in (payload, payload.get("data") if isinstance(payload, dict) else None):
            if not isinstance(candidate, dict):
                continue
            for key in keys:
                value = candidate.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    def _extract_file_actions(self, tool_calls_raw: Any) -> list[dict[str, Any]]:
        """把持久化的 tool_calls 归一成文件动作列表（按调用顺序）。

        每项 {name, file_id, title}，name ∈ create_file/edit_file/delete_file/query_files。
        - 失败的调用一律忽略；没有落下结果（status=pending）的调用另带
          outcome_unknown=True，面包屑写「结果未知」，删除也不当真；
        - query_files 只认按 id 精确读取（关键词搜索只是预览，不算读过全文）；
        - parallel_execute 只展开实际执行完的子任务（见 _extract_parallel_file_actions）。
        """
        tool_calls = self._parse_json_value(tool_calls_raw)
        if not isinstance(tool_calls, list):
            return []

        actions: list[dict[str, Any]] = []
        for tc in tool_calls:
            if not isinstance(tc, dict):
                continue

            name = str(tc.get("name") or "").strip()
            status = str(tc.get("status") or "").strip().lower()
            if status in _FAILED_TOOL_STATUSES or tc.get("error"):
                continue

            unknown = {"outcome_unknown": True} if status == _PENDING_TOOL_STATUS else {}
            if name == "delete_file":
                raw_res = self._parse_json_value(tc.get("result"))
                res_data = raw_res.get("data") if isinstance(raw_res, dict) and "data" in raw_res else raw_res
                if raw_res is False or res_data is False:
                    continue
                if isinstance(res_data, dict) and (
                    res_data.get("mutation_applied") is False or res_data.get("restored") is True
                ):
                    continue
            if name in {"create_file", "write_chapter", "edit_file", "delete_file", "rename_file"}:
                actions.append(
                    {
                        "name": name,
                        "file_id": self._extract_breadcrumb_file_id(tc),
                        "title": self._extract_breadcrumb_title(tc),
                        **unknown,
                    }
                )
                continue

            if name == "query_files":
                args = self._parse_json_value(tc.get("arguments"))
                result = tc.get("result")
                if isinstance(args, dict):
                    query_ids: list[str] = []
                    single_id = str(args.get("id") or "").strip()
                    if single_id:
                        query_ids.append(single_id)
                    raw_ids = args.get("ids")
                    if isinstance(raw_ids, list):
                        for item in raw_ids:
                            if isinstance(item, str) and item.strip():
                                query_ids.append(item.strip())
                    for file_id in dict.fromkeys(query_ids):
                        actions.append(
                            {
                                "name": "query_files",
                                "file_id": file_id,
                                "title": self._find_title_for_id(result, file_id),
                                **unknown,
                            }
                        )
                continue

            if name == "parallel_execute":
                actions.extend(self._extract_parallel_file_actions(tc))

        return actions

    def _extract_parallel_file_actions(self, tc: dict[str, Any]) -> list[dict[str, Any]]:
        """展开 parallel_execute 里实际执行完的、与文件相关的子任务。

        只看结果里的 tasks：超过单次上限被截掉的任务只在 dropped_tasks 里，被读取
        守卫拦下的读取根本没进执行器，两者都不在 tasks 里；失败、没有结果的子任务
        也不记。目标文件取执行器回显的 params（id / 标题）。

        旧结果没有回显 params：只有任务一个不少、类型逐个对上时才按下标去落库的
        原始参数里取目标；对不上就只用子任务结果里自带的 id。
        """
        result = self._parse_json_value(tc.get("result"))
        task_results = result.get("tasks") if isinstance(result, dict) else None
        if not isinstance(task_results, list):
            return []

        legacy_params: list[Any] = []
        if not any(isinstance(item, dict) and "params" in item for item in task_results):
            args = self._parse_json_value(tc.get("arguments"))
            requested = args.get("tasks") if isinstance(args, dict) else None
            if (
                isinstance(requested, list)
                and len(requested) == len(task_results)
                and all(
                    isinstance(task, dict)
                    and isinstance(item, dict)
                    and str(task.get("type") or "").strip() == str(item.get("type") or "").strip()
                    for task, item in zip(requested, task_results, strict=True)
                )
            ):
                legacy_params = [self._parse_json_value(task.get("params")) for task in requested]

        actions: list[dict[str, Any]] = []
        for position, task_result in enumerate(task_results):
            if not isinstance(task_result, dict):
                continue
            task_status = str(task_result.get("status") or "").strip().lower()
            if task_status not in _APPLIED_PARALLEL_TASK_STATUSES:
                continue
            task_type = str(task_result.get("type") or "").strip()
            params = task_result.get("params") if "params" in task_result else (
                legacy_params[position] if legacy_params else None
            )
            params = params if isinstance(params, dict) else {}
            task_payload = task_result.get("result")

            if task_type == "query_files":
                file_id = str(params.get("id") or "").strip()
                if file_id:
                    actions.append(
                        {
                            "name": "query_files",
                            "file_id": file_id,
                            "title": self._find_title_for_id(task_payload, file_id),
                        }
                    )
            elif task_type in {"edit_file", "delete_file"}:
                file_id = self._first_id_in(params) or self._first_id_in(task_payload)
                if file_id:
                    actions.append(
                        {
                            "name": task_type,
                            "file_id": file_id,
                            "title": self._find_title_for_id(task_payload, file_id),
                        }
                    )
            elif task_type == "write_chapter":
                file_id = self._first_id_in(task_payload)
                title = params.get("title") if isinstance(params.get("title"), str) else None
                if file_id or title:
                    actions.append({"name": "create_file", "file_id": file_id, "title": title})
        return actions

    def _build_tool_calls_history_content(self, tool_calls_raw: Any) -> str:
        """
        Synthesize a compact text breadcrumb from persisted ChatMessage.tool_calls.

        Summarizes file create/edit/delete operations and exact-id reads by
        title + id so the agent remembers what it did and read on prior turns.
        Tool arguments and full results are intentionally NOT dumped — only a
        one-line summary per file operation (duplicates collapsed).
        Returns an empty string when there is nothing worth recording.
        """
        force_en = (os.getenv("AGENT_HISTORY_BREADCRUMB_LANG") or "").strip().lower().startswith("en")

        lines: list[str] = []
        seen: set[str] = set()
        for action in self._extract_file_actions(tool_calls_raw):
            line = self._format_breadcrumb_line(
                action["name"],
                action["title"],
                action["file_id"],
                force_en=force_en,
                outcome_unknown=bool(action.get("outcome_unknown")),
            )
            if line and line not in seen:
                seen.add(line)
                lines.append(line)

        if not lines:
            return ""

        header = "[Previous tool actions]" if force_en else "[此前的工具操作]"
        return header + "\n" + "\n".join(lines)

    @staticmethod
    def _extract_breadcrumb_file_id(tc: dict[str, Any]) -> str | None:
        """Resolve the file id from a persisted tool-call record (result first, then args)."""
        result = tc.get("result")
        if isinstance(result, dict):
            for key in ("id", "file_id", "fileId"):
                value = result.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        args = tc.get("arguments")
        if isinstance(args, dict):
            for key in ("id", "file_id", "fileId"):
                value = args.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return None

    @staticmethod
    def _extract_breadcrumb_title(tc: dict[str, Any]) -> str | None:
        """Resolve a human-readable file title from a persisted tool-call record."""
        result = tc.get("result")
        if isinstance(result, dict):
            value = result.get("title")
            if isinstance(value, str) and value.strip():
                return value.strip()

        args = tc.get("arguments")
        if isinstance(args, dict):
            value = args.get("title")
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    @staticmethod
    def _format_breadcrumb_line(
        name: str,
        title: str | None,
        file_id: str | None,
        *,
        force_en: bool,
        outcome_unknown: bool = False,
    ) -> str:
        """Format a single compact breadcrumb line for a file operation."""
        title_part = f"《{title}》" if title else ("(untitled)" if force_en else "（未命名）")
        id_part = f" (id={file_id})" if file_id else ""

        if outcome_unknown:
            verbs = _BREADCRUMB_UNKNOWN_EN if force_en else _BREADCRUMB_UNKNOWN_ZH
        else:
            verbs = _BREADCRUMB_VERBS_EN if force_en else _BREADCRUMB_VERBS_ZH
        verb = verbs.get(name)
        if not verb:
            return ""
        return f"- {verb} {title_part}{id_part}"

    @staticmethod
    def _workflow_stopped_card_summary(card: dict[str, Any], *, bounded: bool = False) -> str:
        """停止卡的回放文本。bounded=True 时按补问卡上限截断。"""
        parts = ["[workflow_stopped]"]
        reason = str(card.get("reason") or "").strip()
        question = str(card.get("question") or card.get("message") or "").strip()
        context = str(card.get("context") or "").strip()
        details = card.get("details")
        text_max = _CLARIFICATION_TEXT_MAX_CHARS if bounded else None
        detail_max = _CLARIFICATION_DETAIL_MAX_CHARS if bounded else None

        if reason:
            parts.append(f"reason: {reason}")
        if question:
            parts.append(f"question: {question[:text_max]}")
        if context:
            parts.append(f"context: {context[:text_max]}")
        if isinstance(details, list):
            if bounded:
                details = details[:_CLARIFICATION_MAX_DETAILS]
            normalized_details = [
                str(detail).strip()[:detail_max]
                for detail in details
                if detail is not None and str(detail).strip()
            ]
            if normalized_details:
                parts.append("details: " + "; ".join(normalized_details))
        return "\n".join(parts)

    def _clarification_cards_history_content(self, status_cards: list[Any]) -> str:
        """只取补问卡（clarification_needed）的有上限回放文本，供正文非空的轮次追加。"""
        return "\n\n".join(
            self._workflow_stopped_card_summary(card, bounded=True)
            for card in status_cards
            if isinstance(card, dict)
            and str(card.get("type") or "").strip() == "workflow_stopped"
            and str(card.get("reason") or "").strip() == "clarification_needed"
        )

    def _build_status_cards_history_content(
        self,
        status_cards: list[Any],
    ) -> str:
        """Build a lightweight text summary so status-only turns remain visible to the model."""
        summaries: list[str] = []

        for card in status_cards:
            if not isinstance(card, dict):
                continue

            card_type = str(card.get("type") or "").strip()
            if card_type == "workflow_stopped":
                summaries.append(self._workflow_stopped_card_summary(card))
                continue

            if card_type == "iteration_exhausted":
                parts = ["[iteration_exhausted]"]
                layer = str(card.get("layer") or "").strip()
                iterations_used = card.get("iterationsUsed")
                max_iterations = card.get("maxIterations")
                reason = str(card.get("reason") or "").strip()
                last_agent = str(card.get("lastAgent") or "").strip()

                if layer:
                    parts.append(f"layer: {layer}")
                if iterations_used is not None or max_iterations is not None:
                    parts.append(
                        "iterations: "
                        f"{iterations_used if iterations_used is not None else '?'}"
                        "/"
                        f"{max_iterations if max_iterations is not None else '?'}"
                    )
                if reason:
                    parts.append(f"reason: {reason}")
                if last_agent:
                    parts.append(f"last_agent: {last_agent}")

                summaries.append("\n".join(parts))

        return "\n\n".join(summary for summary in summaries if summary)
