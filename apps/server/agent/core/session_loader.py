"""
Session loader helpers for author manual-edit alerts, session-written files,
session read snapshots, and loop status card history replay.
"""

import contextlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import case, desc, func, or_
from sqlalchemy.orm import aliased
from sqlmodel import Session, and_, select

from config.datetime_utils import normalize_datetime_to_utc
from utils.logger import get_logger, log_with_context
from utils.text_metrics import count_words

logger = get_logger(__name__)

WORKING_SET_USER_MESSAGE_LABEL = "【本轮用户消息】"
# 本轮开始时核对作者手改的非焦点文件上限。
AUTHOR_EDITS_MAX_FILES = 30
# session_written_file_ids 每次读多少条 assistant 行（只读 tool_calls 列）：凑够名额就不再往前读。
SESSION_WRITTEN_FILES_PAGE_ROWS = 20
# 本轮开始时从本会话结转未变更文件快照的上限（最多结转这么多文件、最多往前翻这么多页，控制 prepare_context 耗时）。
SESSION_SNAPSHOT_MAX_FILES = 20
SESSION_SNAPSHOT_MAX_PAGES = 2

_FAILED_TOOL_STATUSES = {"error", "failed", "failure"}
_PENDING_TOOL_STATUS = "pending"


@dataclass(frozen=True)
class _FileSnapshot:
    """Plain-Python snapshot of File metadata immune to SQLAlchemy session.rollback() expiration."""

    id: str
    title: str
    content: str
    updated_at: datetime | None


class SessionLoader:
    """
    Helpers for author manual-edit alerts, session-written files, session read snapshots,
    and loop status-card summaries.
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
        force_en: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> tuple[str, set[str], dict[str, Any] | None]:
        """用单次批量 ``FileVersion`` 元数据查询核对 ``ordered``（至多 ``AUTHOR_EDITS_MAX_FILES`` 个）
        中哪些文件在最近一次 AI 写入之后被作者手动修改过，返回 ``(<author_edits> 块, 被手改 id 集, id → File)``。

        判定规则（零 ``FileVersion.content`` TOAST 读取，零 ``SequenceMatcher``）：
        - 0 个版本或从未有过 AI 版本（作者原创/导入原稿）：不提醒；
        - 存在严格 AI 版本且最新版本不是严格 AI（作者编辑 / 作者回滚 / 审阅保存等）：提醒；
        - 最新版本是严格 AI，但正文字符数或字数与最新版本不一致、时间戳缺失，或
          ``file.updated_at > latest.created_at``（零宽限窗口，覆盖版本额度耗尽后的无版本手改）：提醒。
        查询失败时回滚并如实附上不完整提示。
        """
        from agent.tools.author_changes import (
            _REVIEWED_AI_EDIT_SUMMARY,
            render_manual_edit_alerts,
        )
        from models import File
        from models.file_version import FileVersion

        if not ordered:
            return "", set(), {}

        if cancel_event is not None and cancel_event.is_set():
            return "", set(), {}

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
            unchecked_ids = ordered[AUTHOR_EDITS_MAX_FILES:]
            return (
                render_manual_edit_alerts(
                    [],
                    force_en=force_en,
                    unchecked_titles=[
                        fallback_titles.get(file_id) or ("(untitled)" if force_en else "（未命名）")
                        for file_id in unchecked_ids
                    ],
                    failed=True,
                ),
                set(),
                None,
            )

        by_id = {str(f.id): f for f in files if f.file_type != "folder"}
        snapshots_by_id = {
            fid: _FileSnapshot(
                id=fid,
                title=str(f.title or "").strip() or ("(untitled)" if force_en else "（未命名）"),
                content=f.content or "",
                updated_at=f.updated_at,
            )
            for fid, f in by_id.items()
        }
        present = [file_id for file_id in ordered if file_id in by_id]
        checked, unchecked = present[:AUTHOR_EDITS_MAX_FILES], present[AUTHOR_EDITS_MAX_FILES:]
        unchecked_titles = [
            f'"{snapshots_by_id[fid].title}"' if force_en else f"《{snapshots_by_id[fid].title}》"
            for fid in unchecked
        ]

        if not checked:
            return (
                render_manual_edit_alerts([], force_en=force_en, unchecked_titles=unchecked_titles),
                set(),
                by_id,
            )

        if cancel_event is not None and cancel_event.is_set():
            all_unchecked = [
                f'"{snapshots_by_id[fid].title}"' if force_en else f"《{snapshots_by_id[fid].title}》"
                for fid in present
            ]
            return (
                render_manual_edit_alerts([], force_en=force_en, unchecked_titles=all_unchecked),
                set(),
                by_id,
            )

        potential_ai_expr = or_(
            FileVersion.change_source == "ai",
            FileVersion.change_type == "ai_edit",
        )
        strict_ai_expr = or_(
            FileVersion.change_source == "ai",
            and_(
                FileVersion.change_type == "ai_edit",
                or_(
                    FileVersion.change_summary.is_(None),
                    ~FileVersion.change_summary.startswith(_REVIEWED_AI_EDIT_SUMMARY),
                ),
            ),
        )
        try:
            agg_subq = (
                select(
                    FileVersion.file_id.label("file_id"),
                    func.max(FileVersion.version_number).label("max_ver"),
                    func.sum(case((potential_ai_expr, 1), else_=0)).label("potential_ai_count"),
                    func.max(case((potential_ai_expr, FileVersion.version_number), else_=None)).label(
                        "max_potential_ai_ver"
                    ),
                    func.max(case((strict_ai_expr, FileVersion.version_number), else_=None)).label(
                        "max_strict_ai_ver"
                    ),
                )
                .where(FileVersion.file_id.in_(checked))
                .group_by(FileVersion.file_id)
                .subquery()
            )
            ai_fv = aliased(FileVersion)
            ai_ver_expr = func.coalesce(agg_subq.c.max_strict_ai_ver, agg_subq.c.max_potential_ai_ver)
            summary_rows = session.exec(
                select(
                    agg_subq.c.file_id,
                    agg_subq.c.max_ver,
                    agg_subq.c.potential_ai_count,
                    agg_subq.c.max_potential_ai_ver,
                    agg_subq.c.max_strict_ai_ver,
                    FileVersion.char_count,
                    FileVersion.word_count,
                    FileVersion.created_at,
                    case((strict_ai_expr, 1), else_=0).label("latest_is_strict_ai"),
                    ai_fv.version_number.label("ai_version_number"),
                    ai_fv.char_count.label("ai_char_count"),
                    ai_fv.word_count.label("ai_word_count"),
                )
                .join(
                    FileVersion,
                    and_(
                        FileVersion.file_id == agg_subq.c.file_id,
                        FileVersion.version_number == agg_subq.c.max_ver,
                    ),
                )
                .outerjoin(
                    ai_fv,
                    and_(
                        ai_fv.file_id == agg_subq.c.file_id,
                        ai_fv.version_number == ai_ver_expr,
                    ),
                )
            ).all()
        except Exception as exc:
            with contextlib.suppress(Exception):
                session.rollback()
            log_with_context(
                logger,
                30,  # WARNING
                "Author edits version summary query failed",
                project_id=self.project_id,
                user_id=self.user_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return (
                render_manual_edit_alerts(
                    [], force_en=force_en, unchecked_titles=unchecked_titles, failed=True
                ),
                set(),
                by_id,
            )

        version_summary: dict[
            str,
            tuple[
                int,
                int,
                int | None,
                int | None,
                int,
                int,
                datetime | None,
                bool,
                int | None,
                int | None,
                int | None,
            ],
        ] = {
            str(fid): (
                int(max_ver or 0),
                int(potential_ai_count or 0),
                int(max_potential_ai_ver) if max_potential_ai_ver is not None else None,
                int(max_strict_ai_ver) if max_strict_ai_ver is not None else None,
                int(char_count or 0),
                int(word_count or 0),
                latest_created_at,
                bool(latest_is_strict_ai),
                int(ai_version_number) if ai_version_number is not None else None,
                int(ai_char_count) if ai_char_count is not None else None,
                int(ai_word_count) if ai_word_count is not None else None,
            )
            for (
                fid,
                max_ver,
                potential_ai_count,
                max_potential_ai_ver,
                max_strict_ai_ver,
                char_count,
                word_count,
                latest_created_at,
                latest_is_strict_ai,
                ai_version_number,
                ai_char_count,
                ai_word_count,
            ) in summary_rows
        }

        definite_edited_ids: set[str] = set()
        equal_len_candidates: dict[str, int] = {}
        for file_id in checked:
            snap = snapshots_by_id[file_id]
            summary = version_summary.get(snap.id)
            if summary is None:
                continue
            (
                max_ver,
                potential_ai_count,
                max_potential_ai_ver,
                max_strict_ai_ver,
                latest_char_count,
                latest_word_count,
                latest_created_at,
                latest_is_strict_ai,
                ai_version_number,
                ai_char_count,
                ai_word_count,
            ) = summary
            if potential_ai_count <= 0:
                continue
            if not latest_is_strict_ai:
                # A non-AI version (user edit, user restore, reviewed AI edit, system restore)
                # exists after a strict AI version, OR after a legacy "AI edit (reviewed)"
                # version (e.g. create/user -> ai_edit/reviewed -> edit/user).
                has_post_ai_ver = max_strict_ai_ver is not None or (
                    max_potential_ai_ver is not None and max_ver > max_potential_ai_ver
                )
                if not has_post_ai_ver:
                    continue
                if ai_version_number is None or ai_char_count is None or ai_word_count is None:
                    definite_edited_ids.add(file_id)
                elif len(snap.content) != ai_char_count or count_words(snap.content) != ai_word_count:
                    # Distinct length/word count vs the AI version -> edited with 0 TOAST reads.
                    definite_edited_ids.add(file_id)
                else:
                    # Rare equal-length/equal-word-count candidate: resolve AI version text in batch
                    # to distinguish same-length edits from no-op identical-content saves.
                    equal_len_candidates[file_id] = ai_version_number
                continue
            current_text = snap.content
            if len(current_text) != latest_char_count or count_words(current_text) != latest_word_count:
                # Unversioned manual edit after the latest AI version (e.g. version quota exhausted).
                definite_edited_ids.add(file_id)
            elif not isinstance(snap.updated_at, datetime) or not isinstance(latest_created_at, datetime):
                # Fail closed on missing timestamps.
                definite_edited_ids.add(file_id)
            elif normalize_datetime_to_utc(snap.updated_at) > normalize_datetime_to_utc(latest_created_at):
                # Zero grace window so even same-length unversioned edits immediately after an AI write are caught.
                definite_edited_ids.add(file_id)

        if equal_len_candidates:
            from services.features.file_version_service import FileVersionService

            try:
                pair_filters = [
                    and_(FileVersion.file_id == fid, FileVersion.version_number == ver)
                    for fid, ver in equal_len_candidates.items()
                ]
                target_versions = {
                    str(v.file_id): v
                    for v in session.exec(select(FileVersion).where(or_(*pair_filters))).all()
                }
                ai_contents = (
                    FileVersionService()._get_contents_for_versions(session, target_versions)
                    if target_versions
                    else {}
                )
            except Exception as exc:
                with contextlib.suppress(Exception):
                    session.rollback()
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Author edits AI version reconstruction failed",
                    project_id=self.project_id,
                    user_id=self.user_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return (
                    render_manual_edit_alerts(
                        [], force_en=force_en, unchecked_titles=unchecked_titles, failed=True
                    ),
                    set(),
                    by_id,
                )
            for fid in equal_len_candidates:
                reconstructed = ai_contents.get(fid)
                if reconstructed is None or snapshots_by_id[fid].content != reconstructed:
                    definite_edited_ids.add(fid)

        edited_files: list[tuple[str, str]] = []
        edited_ids: set[str] = set()
        for file_id in checked:
            if file_id in definite_edited_ids:
                snap = snapshots_by_id[file_id]
                edited_files.append((file_id, snap.title))
                edited_ids.add(file_id)

        block = render_manual_edit_alerts(
            edited_files,
            force_en=force_en,
            unchecked_titles=unchecked_titles,
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

        if limit <= 0 or (cancel_event is not None and cancel_event.is_set()):
            return []

        base_query = (
            select(ChatMessage.id, ChatMessage.created_at, ChatMessage.tool_calls)
            .where(
                and_(
                    ChatMessage.session_id == chat_session_id,
                    ChatMessage.role == "assistant",
                )
            )
            .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        )

        # 新 → 旧按页 keyset 分页（替代 OFFSET），每页结束后执行一次批量 SELECT File.id
        # 消除逐行 N+1，同时在凑够 limit 后立刻提前终止，不扫描更早的历史页。
        written: list[str] = []
        seen: set[str] = set()
        deleted: set[str] = set()
        cursor_created_at: datetime | None = None
        cursor_id: str | None = None

        while len(written) < limit:
            if cancel_event is not None and cancel_event.is_set():
                return written[:limit]
            stmt = base_query
            if cursor_created_at is not None and cursor_id is not None:
                stmt = stmt.where(
                    or_(
                        ChatMessage.created_at < cursor_created_at,
                        and_(
                            ChatMessage.created_at == cursor_created_at,
                            ChatMessage.id < cursor_id,
                        ),
                    )
                )
            page = session.exec(stmt.limit(SESSION_WRITTEN_FILES_PAGE_ROWS)).all()
            if not page:
                break

            page_candidates: list[str] = []
            for _row_id, _row_created_at, tool_calls in page:
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
                    page_candidates.append(file_id)

            if page_candidates:
                if file_types is None:
                    written.extend(page_candidates)
                else:
                    accepted = set(
                        session.exec(
                            select(File.id).where(
                                File.id.in_(page_candidates),
                                File.project_id == self.project_id,
                                File.is_deleted.is_(False),
                                File.file_type.in_(file_types),
                            )
                        ).all()
                    )
                    written.extend(fid for fid in page_candidates if fid in accepted)

            if len(written) >= limit or len(page) < SESSION_WRITTEN_FILES_PAGE_ROWS:
                break

            last_id, last_created_at, _ = page[-1]
            if last_created_at is None or last_id is None:
                break
            cursor_created_at = last_created_at
            cursor_id = str(last_id)

        return written[:limit]

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

    @staticmethod
    def _extract_undo_proof(undo: Any) -> tuple[str | None, str | None]:
        """从 edit_file / write_chapter(replaced) 的 undo 锚点提取内容哈希与时间戳凭证。

        必须包含 expected_after_content_sha256（仅时间戳无内容哈希不放行）；已撤销卡片
        （undo.undone is True）一律返回 (None, None) 以 fail closed。
        """
        if not isinstance(undo, dict) or undo.get("undone") is True:
            return None, None
        raw_sha = undo.get("expected_after_content_sha256")
        exp_sha = raw_sha.strip() if isinstance(raw_sha, str) and raw_sha.strip() else None
        if exp_sha is None:
            return None, None
        raw_ts = undo.get("expected_after_updated_at")
        exp_ts = raw_ts.strip() if isinstance(raw_ts, str) and raw_ts.strip() else None
        return exp_sha, exp_ts

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

        安全不变量（绝不绕过 StaleContentError）：
        - 只有当前 DB 状态与本会话最近一次工具触达时的精确凭证一致，才结转进 read_snapshots["initial"]：
          - query_files 全文读（原始正文或落库瘦身后的 content_sha256） / create_file 直接写入：
            正文或哈希逐字一致且（若有）updated_at 一致；
          - edit_file / write_chapter(replaced)：必须有 undo.expected_after_content_sha256 且未被撤销
            （undo.undone != True），并与当前正文哈希及（若有）expected_after_updated_at 一致；
          - write_chapter(created) / create_file(<file> 落库)：file.updated_at <= 该轮 assistant 行创建时间，
            且最新 FileVersion 为 AI 写入且正文与当前 file.content 逐字一致；
        - 只要作者在轮间改过文件（哪怕未推进 updated_at 或未建版本），一律不结转。
        - 仅扫描单循环（engine == "loop"）assistant 行，同时检查 user 与 assistant 行的有效压缩点。
        - 最多扫 SESSION_SNAPSHOT_MAX_PAGES 页、最多取 limit 个文件，单次批量 SELECT File。
        """
        from agent.loop.history import LOOP_ENGINE_METADATA_VALUE, valid_compaction
        from agent.tools.author_changes import AI, version_actor
        from models import ChatMessage, File
        from models.file_version import FileVersion
        from services.features.file_version_service import FileVersionService, content_sha256

        if limit <= 0 or (cancel_event is not None and cancel_event.is_set()):
            return {}

        excluded = set(exclude_ids or ())
        deleted: set[str] = set()
        # 较新的写工具若没有可验证凭证（或已撤销），也占位阻止更早的读记录盖过它。
        unverifiable: set[str] = set()
        candidates: dict[str, dict[str, Any]] = {}

        # 每轮包含 user + assistant 两行：按双倍行宽 keyset 分页以维持相同轮次覆盖深度。
        page_rows = SESSION_WRITTEN_FILES_PAGE_ROWS * 2
        query = (
            select(
                ChatMessage.id,
                ChatMessage.role,
                ChatMessage.tool_calls,
                ChatMessage.created_at,
                ChatMessage.message_metadata,
            )
            .where(
                and_(
                    ChatMessage.session_id == chat_session_id,
                    ChatMessage.role.in_(("user", "assistant")),
                )
            )
            .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        )

        hit_compaction = False
        cursor_created_at: datetime | None = None
        cursor_id: str | None = None
        for _page_index in range(SESSION_SNAPSHOT_MAX_PAGES):
            if cancel_event is not None and cancel_event.is_set():
                return {}
            stmt = query
            if cursor_created_at is not None and cursor_id is not None:
                stmt = stmt.where(
                    or_(
                        ChatMessage.created_at < cursor_created_at,
                        and_(
                            ChatMessage.created_at == cursor_created_at,
                            ChatMessage.id < cursor_id,
                        ),
                    )
                )
            rows = session.exec(stmt.limit(page_rows)).all()
            if not rows:
                break
            for _row_id, role, tool_calls_raw, row_created_at, metadata_raw in rows:
                meta = self._parse_json_value(metadata_raw)
                has_compaction = isinstance(meta, dict) and valid_compaction(meta) is not None
                if role == "user":
                    if has_compaction:
                        # 压缩发生在这一条未得到助手回复的轮次开始前：更早的轮次已被折叠进摘要。
                        hit_compaction = True
                        break
                    continue

                if has_compaction:
                    # 压缩发生在本轮开始前：本行的工具调用仍在压缩后历史里，更早的行不再扫描。
                    hit_compaction = True

                if not isinstance(meta, dict) or meta.get("engine") != LOOP_ENGINE_METADATA_VALUE:
                    if hit_compaction:
                        break
                    continue

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
                                if (
                                    item.get("file_type") == "folder"
                                    or item.get("content_truncated")
                                    or item.get("content_partial")
                                ):
                                    continue
                                updated_at = item.get("updated_at")
                                norm_ts = (
                                    updated_at.strip()
                                    if isinstance(updated_at, str) and updated_at.strip()
                                    else None
                                )
                                content = item.get("content")
                                raw_sha = item.get("content_sha256")
                                if isinstance(content, str):
                                    candidates[fid] = {
                                        "kind": "read",
                                        "content": content,
                                        "updated_at": norm_ts,
                                    }
                                    if len(candidates) >= limit:
                                        break
                                elif isinstance(raw_sha, str) and raw_sha.strip():
                                    candidates[fid] = {
                                        "kind": "read_sha256",
                                        "sha256": raw_sha.strip(),
                                        "updated_at": norm_ts,
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
                            exp_sha, exp_ts = self._extract_undo_proof(result.get("undo"))
                            if exp_sha is not None:
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
                                exp_sha, exp_ts = self._extract_undo_proof(result.get("undo"))
                                if exp_sha is not None:
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
            if len(candidates) >= limit or hit_compaction or len(rows) < page_rows:
                break
            last_row_id, _last_role, _last_tc, last_created_at, _last_meta = rows[-1]
            if last_created_at is None or last_row_id is None:
                break
            cursor_created_at = last_created_at
            cursor_id = str(last_row_id)

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
            elif kind in ("undo_write", "read_sha256"):
                if content_sha256(current_content) != spec["sha256"]:
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

    @staticmethod
    def _parse_json_value(value: Any) -> Any:
        """tool_calls / arguments 可能以 JSON 字符串持久化，统一解析成 Python 值。"""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (TypeError, json.JSONDecodeError):
                return None
        return value

    def _extract_file_actions(self, tool_calls_raw: Any) -> list[dict[str, Any]]:
        """把持久化的 tool_calls 归一成文件写入/删除动作列表（供 ``session_written_file_ids`` 使用）。"""
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
                        **unknown,
                    }
                )
        return actions

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
    def _workflow_stopped_card_summary(card: dict[str, Any]) -> str:
        """停止卡的回放文本。"""
        parts = ["[workflow_stopped]"]
        reason = str(card.get("reason") or "").strip()
        question = str(card.get("question") or card.get("message") or "").strip()
        context = str(card.get("context") or "").strip()
        details = card.get("details")

        if reason:
            parts.append(f"reason: {reason}")
        if question:
            parts.append(f"question: {question}")
        if context:
            parts.append(f"context: {context}")
        if isinstance(details, list):
            normalized_details = [
                str(detail).strip()
                for detail in details
                if detail is not None and str(detail).strip()
            ]
            if normalized_details:
                parts.append("details: " + "; ".join(normalized_details))
        return "\n".join(parts)

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
