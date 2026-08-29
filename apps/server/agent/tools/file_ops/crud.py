"""
File CRUD operations for agent tools.

This module provides Create, Read, Update, Delete operations on the File model:
- create_file: Create any type of file
- update_file: Update existing file
- delete_file: Delete a file (with optional recursive deletion)
- query_files: Query and search files (unified)
- hybrid_search: Lexical + vector hybrid retrieval (RAG)

Extracted from the monolithic file_executor.py for better maintainability.
"""

import contextlib
import json
from typing import Any

from services.file_version import FileVersionService
from sqlalchemy import func
from sqlmodel import Session, col, select

from agent.constants import coerce_bool
from agent.tools.permissions import (
    check_file_access_in_tool_context,
    check_project_ownership,
)
from config.datetime_utils import utcnow
from models import File
from models.file_model import FILE_TYPE_FOLDER
from models.file_version import (
    CHANGE_SOURCE_AI,
    CHANGE_TYPE_AI_EDIT,
    CHANGE_TYPE_CREATE,
)
from services.file_tree_rules import (
    # 文件树结构不变量的唯一实现放在 services/file_tree_rules.py：
    # REST 层 api/files.py 与本模块共用同一份，避免两边各写一遍再慢慢漂移
    # （历史上 agent 侧就漏了「parent 必须是 folder」这一条）。
    # 这里保留同名再导出，既有调用方与测试的 `crud.validate_parent_assignment`
    # 依旧可用。
    is_descendant_of,
    validate_parent_assignment,
)
from utils.logger import get_logger, log_with_context
from utils.title_sequence import (
    extract_title_first_sequence_number,
    resolve_persisted_sequence_order,
)

from .edit import acquire_file_write_lock
from .serialization import (
    QUERY_FILES_DEFAULT_CONTENT_PREVIEW_CHARS,
    QUERY_FILES_DEFAULT_RESPONSE_MODE,
    serialize_file,
    serialize_query_file,
)

logger = get_logger(__name__)


def find_nearest_folder_ancestor(
    session: Session,
    project_id: str,
    file_id: str | None,
) -> str | None:
    """从 file_id 起沿 parent_id 向上，返回最近的「同项目、未删除的 folder」。

    file_id 自身若已经是合法 folder 则直接返回它；一路走到根都没有 folder
    （或中途遇到跨项目/已删除节点）时返回 None，表示应挂到根层。
    visited 集合同样用于抵御历史脏数据里的环。
    """
    if not file_id:
        return None

    visited: set[str] = set()
    current_id: str | None = file_id

    while current_id:
        if current_id in visited:
            return None
        visited.add(current_id)

        node = session.get(File, current_id)
        if node is None or node.project_id != project_id:
            return None
        if node.file_type == FILE_TYPE_FOLDER and not node.is_deleted:
            return node.id
        current_id = node.parent_id

    return None


class FileCRUD:
    """
    CRUD operations for File model.

    This class provides simple, unified CRUD operations on the File model
    with permission checking and version history support.
    """

    def __init__(self, session: Session, user_id: str | None = None):
        """
        Initialize file CRUD operations.

        Args:
            session: Database session
            user_id: Current user ID (UUID string, for permission checks)
        """
        self.session = session
        self.user_id = user_id

    def create_file(
        self,
        project_id: str,
        title: str,
        file_type: str = "document",
        content: str = "",
        parent_id: str | None = None,
        order: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new file.

        Args:
            project_id: Project ID
            title: File title/name
            file_type: File type (outline, character, lore, etc.)
            content: File content
            parent_id: Parent file ID (for folders)
            order: Sort order
            metadata: Type-specific metadata (JSON)

        Returns:
            Created file data

        Raises:
            PermissionError: If user doesn't have permission
        """
        log_with_context(
            logger,
            20,  # INFO
            "create_file started",
            project_id=project_id,
            user_id=self.user_id,
            file_type=file_type,
            title=title,
            content_length=len(content),
            parent_id=parent_id,
        )

        # Check project permission
        project = check_project_ownership(self.session, project_id, self.user_id)

        # Root folder repair (best-effort): some projects may have their root folders
        # soft-deleted or missing due to historical bugs/admin actions. Since agents
        # rely on predictable root folder ids (e.g. "{project_id}-draft-folder"),
        # we auto-restore/recreate expected root folders when referenced.
        expected_root_folders: dict[str, dict[str, Any]] = {}
        try:
            from config.project_templates import get_folders_for_type

            for cfg in get_folders_for_type(project.project_type, "zh"):
                cfg_id = (cfg or {}).get("id")
                if isinstance(cfg_id, str) and cfg_id:
                    expected_root_folders[f"{project_id}-{cfg_id}"] = cfg
        except Exception:
            expected_root_folders = {}

        def _repair_root_folder(folder_id: str) -> File | None:
            cfg = expected_root_folders.get(folder_id)
            if not cfg:
                return None

            existing = self.session.get(File, folder_id)
            if existing:
                if existing.project_id != project_id:
                    return None
                if existing.file_type != "folder":
                    return None

                changed = False
                if existing.is_deleted:
                    existing.is_deleted = False
                    existing.deleted_at = None
                    changed = True

                if existing.parent_id is not None:
                    existing.parent_id = None
                    changed = True

                if changed:
                    existing.updated_at = utcnow()
                    self.session.add(existing)
                    self.session.commit()
                    self.session.refresh(existing)

                    log_with_context(
                        logger,
                        30,  # WARNING
                        "Repaired missing/deleted root folder",
                        project_id=project_id,
                        user_id=self.user_id,
                        folder_id=folder_id,
                        folder_title=existing.title,
                        project_type=project.project_type,
                    )

                return existing

            folder = File(
                id=folder_id,
                project_id=project_id,
                title=str(cfg.get("title") or "folder"),
                file_type="folder",
                order=int(cfg.get("order") or 0),
                parent_id=None,
            )
            self.session.add(folder)
            self.session.commit()
            self.session.refresh(folder)

            log_with_context(
                logger,
                30,  # WARNING
                "Created missing root folder on-demand",
                project_id=project_id,
                user_id=self.user_id,
                folder_id=folder_id,
                folder_title=folder.title,
                project_type=project.project_type,
            )

            return folder

        # Validate parent_id exists and belongs to project
        if parent_id is not None:
            parent = self.session.get(File, parent_id)
            if not parent or parent.is_deleted or parent.project_id != project_id:
                # Best-effort repair for missing/deleted root folders (novel/short/screenplay)
                repaired_parent = _repair_root_folder(parent_id)
                if repaired_parent is not None:
                    parent = repaired_parent
                else:
                    # Recovery: screenplay projects do not have a "draft-folder".
                    # Some LLM calls may still pass "{project_id}-draft-folder" as the parent_id,
                    # which would otherwise hard-fail this tool call and interrupt the user flow.
                    should_fallback = (
                        project.project_type == "screenplay"
                        and parent_id == f"{project_id}-draft-folder"
                        and file_type in ("draft", "script", "document")
                    )
                    if should_fallback:
                        fallback_parent_id = f"{project_id}-script-folder"
                        fallback_parent = self.session.get(File, fallback_parent_id)
                        if not fallback_parent or fallback_parent.is_deleted:
                            fallback_parent = _repair_root_folder(fallback_parent_id)
                        if (
                            fallback_parent
                            and not fallback_parent.is_deleted
                            and fallback_parent.project_id == project_id
                        ):
                            log_with_context(
                                logger,
                                30,  # WARNING
                                "Parent file validation failed; falling back to screenplay script folder",
                                project_id=project_id,
                                user_id=self.user_id,
                                parent_id=parent_id,
                                fallback_parent_id=fallback_parent_id,
                                file_type=file_type,
                            )
                            parent_id = fallback_parent_id
                        else:
                            log_with_context(
                                logger,
                                40,  # ERROR
                                "Parent file validation failed (fallback target missing)",
                                project_id=project_id,
                                user_id=self.user_id,
                                parent_id=parent_id,
                                fallback_parent_id=fallback_parent_id,
                            )
                            raise ValueError(
                                f"Parent file {parent_id} not found in project {project_id}"
                            )
                    else:
                        log_with_context(
                            logger,
                            40,  # ERROR
                            "Parent file validation failed",
                            project_id=project_id,
                            user_id=self.user_id,
                            parent_id=parent_id,
                            file_type=file_type,
                        )
                        raise ValueError(
                            f"Parent file {parent_id} not found in project {project_id}"
                        )

        # 父节点必须是 folder（与 REST 层同一不变量，见 validate_parent_assignment）。
        # parent_id 直接来自 LLM，模型经常把「上一章的文件 id」当成 parent 传进来。
        # 这里不硬失败打断写作流（那会让本轮建档整体失败），而是就近上挂到最近的
        # folder 祖先并告警，保证「写出来的文件一定在文件树里可见」。
        parent_id = self._normalize_parent_to_folder(
            project_id,
            parent_id,
            title=title,
            file_type=file_type,
        )
        validate_parent_assignment(self.session, project_id, parent_id)

        normalized_title = (title or "").strip()
        if normalized_title and normalized_title != title:
            title = normalized_title

        # Pre-compute sequence number for ordering + screenplay episode safeguards.
        seq_num = extract_title_first_sequence_number(title, metadata)

        is_screenplay_project = project.project_type == "screenplay"
        screenplay_script_folder_id = f"{project_id}-script-folder"
        is_screenplay_script_folder = parent_id == screenplay_script_folder_id

        looks_like_episode = (
            is_screenplay_project
            and is_screenplay_script_folder
            and seq_num is not None
            and ("集" in title or "episode" in title.lower())
        )

        # Guardrail: if the agent is writing an episode under the screenplay script folder,
        # normalize accidental draft/document file types to "script" to keep query/search stable.
        if looks_like_episode and file_type in {"draft", "document"}:
            log_with_context(
                logger,
                30,  # WARNING
                "Normalizing screenplay episode file_type to script",
                project_id=project_id,
                user_id=self.user_id,
                parent_id=parent_id,
                title=title,
                original_file_type=file_type,
            )
            file_type = "script"

        resolved_order: int
        if order is not None:
            requested_order = int(order)
            resolved_order = resolve_persisted_sequence_order(
                requested_order,
                title=title,
                metadata=metadata,
                file_type=file_type,
            )
            if resolved_order != requested_order:
                log_with_context(
                    logger,
                    30,  # WARNING
                    "Resolved chapter-like file order from parsed sequence",
                    project_id=project_id,
                    user_id=self.user_id,
                    parent_id=parent_id,
                    title=title,
                    requested_order=requested_order,
                    normalized_order=resolved_order,
                    sequence_number=seq_num,
                )
        else:
            if seq_num is not None:
                resolved_order = int(seq_num)
            else:
                # Append to the end of siblings (stable insertion).
                max_order = self.session.exec(
                    select(func.max(File.order)).where(
                        File.project_id == project_id,
                        File.parent_id == parent_id,
                        File.is_deleted.is_(False),
                    )
                ).one()
                resolved_order = int(max_order or 0)
                if max_order is not None:
                    resolved_order += 1

        # Idempotency for screenplay episode streaming:
        # If an agent tries to create the same episode twice (often due to earlier
        # mismatched file_type/order), reuse the existing file to prevent duplicates.
        #
        # 复用分支的返回契约（不要再用 content="" 当协议信号）：
        # 这里返回**真实 content**，并附加显式字段 reused_existing / original_content_length。
        # 历史写法是把 content 谎报成 ""，只为让 StreamAdapter 进入 <file> 捕获模式；
        # 正常路径能跑通，但模型漏写 </file> 触发截断补全时，适配器以为目标文件本来
        # 就是空的，于是把残稿整体覆盖回去——整集正文被残稿吃掉且不可逆。
        # 现在由 StreamAdapter 依据 reused_existing 决定是否进入捕获，并据
        # original_content_length 判断「目标文件原本非空」以拒绝整体覆盖。
        if looks_like_episode and file_type == "script" and not content:
            existing_stmt = (
                select(File)
                .where(
                    File.project_id == project_id,
                    File.parent_id == parent_id,
                    File.title == title,
                    File.is_deleted.is_(False),
                )
                .order_by(
                    col(File.updated_at).desc(),  # type: ignore[attr-defined]
                    col(File.created_at).desc(),  # type: ignore[attr-defined]
                    col(File.id).desc(),  # type: ignore[attr-defined]
                )
            )
            existing_files = list(self.session.exec(existing_stmt).all())

            if existing_files:
                # Reuse the newest matching file; promote legacy draft/document into script.
                candidate = next(
                    (f for f in existing_files if f.file_type in {"script", "draft", "document"}),
                    None,
                )
                if candidate is not None:
                    changed = False
                    promoted = False

                    if candidate.file_type != "script":
                        candidate.file_type = "script"
                        promoted = True
                        changed = True

                    # Keep episode ordering stable: only fill order when it's unset (0).
                    if (candidate.order or 0) == 0 and resolved_order > 0:
                        candidate.order = resolved_order
                        changed = True

                    if changed:
                        candidate.updated_at = utcnow()
                        self.session.add(candidate)
                        self.session.commit()
                        self.session.refresh(candidate)
                        self._schedule_index_upsert(candidate)

                    log_with_context(
                        logger,
                        20,  # INFO
                        "Reusing existing screenplay episode file to prevent duplicates",
                        project_id=project_id,
                        user_id=self.user_id,
                        file_id=candidate.id,
                        title=title,
                        file_type=candidate.file_type,
                        promoted=promoted,
                        total_matches=len(existing_files),
                        original_content_length=len(candidate.content or ""),
                    )

                    reused = serialize_file(candidate)
                    original_content = candidate.content or ""
                    reused["content"] = original_content
                    reused["reused_existing"] = True
                    reused["original_content_length"] = len(original_content)
                    return reused

        # Create file
        file = File(
            project_id=project_id,
            title=title,
            content=content,
            file_type=file_type,
            parent_id=parent_id,
            order=resolved_order,
            file_metadata=self._serialize_metadata(metadata),
        )

        self.session.add(file)

        # Snapshot the initial content so the pre-first-edit original is
        # recoverable via version history, mirroring update_file/edit_file.
        # Empty create + streaming is unaffected: it gets version 1 from the
        # subsequent update_file that writes the streamed body.
        if content:
            try:
                with self.session.begin_nested():
                    self._create_version(
                        file.id,
                        content,
                        change_type=CHANGE_TYPE_CREATE,
                        change_summary="创建文件",
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to create initial file version; file will persist",
                    exc_info=True,
                    extra={"file_id": file.id, "error": str(exc)},
                )

        self.session.commit()
        self.session.refresh(file)

        # Fire-and-forget vector index upsert (do not block)
        self._schedule_index_upsert(file, metadata)

        log_with_context(
            logger,
            20,  # INFO
            "create_file completed",
            project_id=project_id,
            user_id=self.user_id,
            file_id=file.id,
            file_type=file.file_type,
            title=file.title,
            content_length=len(file.content or ""),
        )

        return serialize_file(file)

    def update_file(
        self,
        id: str,
        title: str | None = None,
        content: str | None = None,
        parent_id: str | None = None,
        order: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Update an existing file.

        Args:
            id: File ID to update
            title: New title
            content: New content
            parent_id: New parent ID
            order: New order
            metadata: New metadata (overwrites existing)

        Returns:
            Updated file data

        Raises:
            PermissionError: If user doesn't have permission
            ValueError: If file not found
        """
        from database import is_postgres

        if is_postgres:
            return self._update_file_impl(id, title, content, parent_id, order, metadata)
        # SQLite has no row locks: serialize same-file writes with the
        # in-process per-file lock (shared with edit_file) so concurrent
        # tasks cannot interleave between our read, commit and version
        # snapshot.
        #
        # 必须走 acquire_file_write_lock 而不是裸 `with file_write_lock(id)`：
        # 这把条带锁与 edit_file 共用，裸阻塞获取一旦发生在事件循环线程上，
        # 整个进程会陪着等到对方释放（最坏是 SQLite busy_timeout 的 30 秒）。
        # acquire 版本在工作线程里语义完全不变，只在事件循环线程上改为有界等待。
        with acquire_file_write_lock(id):
            return self._update_file_impl(id, title, content, parent_id, order, metadata)

    def _update_file_impl(
        self,
        id: str,
        title: str | None,
        content: str | None,
        parent_id: str | None,
        order: int | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        log_with_context(
            logger,
            20,  # INFO
            "update_file started",
            file_id=id,
            user_id=self.user_id,
            has_title=title is not None,
            has_content=content is not None,
            content_length=len(content) if content else 0,
        )

        # Get file. Same locking/read discipline as FileEditor.edit_file: on
        # PostgreSQL hold a row lock (FOR NO KEY UPDATE, so the version
        # snapshot's FK insert from its independent session is not blocked)
        # until both content and snapshot are written; on SQLite force a
        # re-SELECT past the shared session's identity map so the update is
        # based on current DB state instead of a stale cached instance.
        from database import is_postgres

        if is_postgres:
            file = self.session.exec(
                select(File).where(File.id == id).with_for_update(key_share=True)
            ).first()
        else:
            file = self.session.get(File, id, populate_existing=True)

        if not file:
            # Do not leak internal IDs to end users
            log_with_context(
                logger,
                40,  # ERROR
                "File not found",
                file_id=id,
                user_id=self.user_id,
            )
            raise ValueError("文件不存在或已删除")

        # Check permission (target must belong to the current tool-context project)
        check_file_access_in_tool_context(self.session, file, self.user_id)

        # Store old content for version history
        old_content = file.content

        # Update fields
        if title is not None:
            file.title = title

        if content is not None:
            file.content = content

        if parent_id is not None:
            # Empty string or "null" means move to root (no parent)
            if parent_id == "" or parent_id == "null":
                file.parent_id = None
            else:
                # Validate parent exists
                parent = self.session.get(File, parent_id)
                if not parent or parent.is_deleted or parent.project_id != file.project_id:
                    raise ValueError(f"Parent file {parent_id} not found in same project")
                # 与 create_file 走同一套「必须挂在 folder 下」的不变量：先就近
                # 归一到最近的 folder 祖先，再做完整校验（含自引用/成环拒绝）。
                normalized_parent_id = self._normalize_parent_to_folder(
                    file.project_id,
                    parent_id,
                    title=file.title,
                    file_type=file.file_type,
                    file_id=file.id,
                )
                validate_parent_assignment(
                    self.session,
                    file.project_id,
                    normalized_parent_id,
                    moving_file_id=file.id,
                )
                file.parent_id = normalized_parent_id

        if metadata is not None:
            file.file_metadata = self._serialize_metadata(metadata)

        if order is not None or title is not None or metadata is not None:
            file.order = resolve_persisted_sequence_order(
                order if order is not None else file.order,
                title=file.title,
                metadata=file.get_metadata(),
                file_type=file.file_type,
            )

        # Update timestamp
        file.updated_at = utcnow()

        # Check if content changed
        content_changed = content is not None and content != old_content

        # Stage content and snapshot in one locked transaction. The savepoint
        # lets content persist even if version generation itself fails.
        if content_changed:
            try:
                with self.session.begin_nested():
                    self._create_version(id, content)
            except Exception as exc:
                logger.warning(
                    "Failed to create file version; content will persist",
                    exc_info=True,
                    extra={"file_id": id, "error": str(exc)},
                )

        self.session.commit()
        self.session.refresh(file)

        # Fire-and-forget vector index upsert (do not block)
        self._schedule_index_upsert(file)

        log_with_context(
            logger,
            20,  # INFO
            "update_file completed",
            file_id=id,
            user_id=self.user_id,
            project_id=file.project_id,
            content_changed=content_changed,
        )

        return serialize_file(file)

    def delete_file(
        self,
        id: str,
        recursive: bool = False,
    ) -> bool:
        """
        Delete a file.

        Args:
            id: File ID to delete
            recursive: If True, also delete all children

        Returns:
            True if deleted successfully

        Raises:
            PermissionError: If user doesn't have permission
            ValueError: If file not found
        """
        # recursive 直接来自 LLM 的工具参数。工具调用走 strict_json_schema=False，
        # schema 里的 "type": "boolean" 在运行时没有约束力，模型完全可能发来
        # 字符串 "false"/"0"，而朴素的 `if recursive:` 会把它判成真——
        # 「只删这一个文件」当场变成「递归软删整棵子树」。
        # 这里先用 coerce_bool 收敛成真 bool，下面再用 `is True` 判断：
        # 即便上游某条入口漏了强转，也不会误触发级联删除。
        recursive = coerce_bool(recursive)

        log_with_context(
            logger,
            20,  # INFO
            "delete_file started",
            file_id=id,
            user_id=self.user_id,
            recursive=recursive,
        )

        # Get file
        file = self.session.get(File, id)

        if not file or file.is_deleted:
            # Do not leak internal IDs to end users
            log_with_context(
                logger,
                40,  # ERROR
                "File not found",
                file_id=id,
                user_id=self.user_id,
            )
            raise ValueError("文件不存在或已删除")

        # Check permission (target must belong to the current tool-context project)
        check_file_access_in_tool_context(self.session, file, self.user_id)

        deleted: list[File] = []

        # Delete recursively if requested
        if recursive is True:
            deleted = self._delete_recursive(file)
        else:
            # Soft delete: mark as deleted instead of removing from database
            file.is_deleted = True
            file.deleted_at = utcnow()
            deleted = [file]
            self.session.add(file)

        self.session.commit()

        # Fire-and-forget vector index delete (do not block)
        self._schedule_index_delete(deleted)

        log_with_context(
            logger,
            20,  # INFO
            "delete_file completed",
            file_id=id,
            user_id=self.user_id,
            project_id=file.project_id,
            deleted_count=len(deleted),
            recursive=recursive,
        )

        return True

    def query_files(
        self,
        project_id: str,
        id: str | None = None,
        query: str | None = None,
        file_type: str | None = None,
        file_types: list[str] | None = None,
        parent_id: str | None = None,
        metadata_filter: dict[str, Any] | None = None,
        limit: int = 50,
        offset: int = 0,
        response_mode: str = QUERY_FILES_DEFAULT_RESPONSE_MODE,
        content_preview_chars: int = QUERY_FILES_DEFAULT_CONTENT_PREVIEW_CHARS,
        include_content: bool | None = None,
    ) -> list[dict[str, Any]]:
        """
        Query and search files (unified method).

        Supports keyword search, type filtering, parent filtering, and metadata filtering.

        Args:
            project_id: Project ID
            id: Exact file ID lookup (optional, overrides keyword search)
            query: Search keyword for title/content (optional)
            file_type: Single file type filter (optional)
            file_types: Multiple file types filter (optional, use this OR file_type)
            parent_id: Parent file ID filter (optional)
            metadata_filter: Metadata field filters (optional)
            limit: Maximum results
            offset: Offset for pagination
            response_mode: Response format mode ("summary" or "full")
            content_preview_chars: Preview length in summary mode
            include_content: Backward-compatible override; True forces full content

        Returns:
            List of file data

        Raises:
            PermissionError: If user doesn't have permission
        """
        # include_content 是三值语义：None=未指定（按 response_mode 走），
        # True=强制返回全文。它同样来自 LLM 参数，字符串 "false" 会被下游的
        # `include_content is True` 之外的真值判断带偏，也会让日志里的取值失真，
        # 因此在入口就收敛成真 bool（None 保持 None，不丢「未指定」语义）。
        if include_content is not None:
            include_content = coerce_bool(include_content)

        # Check project permission
        check_project_ownership(self.session, project_id, self.user_id)

        # Fast path: exact ID lookup (avoids same-title ambiguity and extra tool calls).
        normalized_id = (id or "").strip()
        if normalized_id:
            stmt = select(File).where(
                File.id == normalized_id,
                File.project_id == project_id,
                File.is_deleted.is_(False),
            )
            if file_types:
                stmt = stmt.where(File.file_type.in_(file_types))  # type: ignore[attr-defined]
            if file_type:
                stmt = stmt.where(File.file_type == file_type)
            if parent_id is not None:
                stmt = stmt.where(File.parent_id == parent_id)
            results = list(self.session.exec(stmt).all())
            if metadata_filter:
                results = self._filter_by_metadata(results, metadata_filter)
            return [
                serialize_query_file(
                    r,
                    response_mode=response_mode,
                    content_preview_chars=content_preview_chars,
                    include_content=include_content,
                )
                for r in results
            ]

        # Determine which file types to query
        target_types = None
        if file_types:
            target_types = file_types
        elif file_type:
            target_types = [file_type]

        # limit/offset apply to the MERGED result set (a single statement),
        # so the declared "max results" cap and pagination semantics hold even
        # when multiple file_types are requested.
        results = self._query_files_page(
            project_id=project_id,
            file_types=target_types,
            query=query,
            parent_id=parent_id,
            limit=limit,
            offset=offset,
        )

        # Apply metadata filter in Python (SQLite JSON support is limited)
        if metadata_filter:
            results = self._filter_by_metadata(results, metadata_filter)

        return [
            serialize_query_file(
                r,
                response_mode=response_mode,
                content_preview_chars=content_preview_chars,
                include_content=include_content,
            )
            for r in results
        ]

    def hybrid_search(
        self,
        project_id: str,
        query: str,
        top_k: int = 10,
        entity_types: list[str] | None = None,
        min_score: float = 0.0,
    ) -> dict[str, Any]:
        """
        Hybrid retrieval (lexical + vector fusion).

        Args:
            project_id: Project ID
            query: Search query
            top_k: Maximum results to return
            entity_types: Filter by entity types
            min_score: Minimum fused score threshold

        Returns:
            Search results with hybrid fused scores
        """
        check_project_ownership(self.session, project_id, self.user_id)

        top_k = max(1, min(int(top_k or 10), 20))
        min_score = float(min_score or 0.0)

        from services.llama_index import get_llama_index_service

        svc = get_llama_index_service()
        results = svc.hybrid_search(
            project_id=project_id,
            query=query,
            top_k=top_k,
            entity_types=entity_types,
        )

        filtered = [
            r for r in results
            if (r.fused_score if r.fused_score is not None else (r.score or 0.0)) >= min_score
        ]

        return {
            "query": query,
            "top_k": top_k,
            "min_score": min_score,
            "search_mode": "hybrid",
            "results": [r.to_dict() for r in filtered],
            "result_count": len(filtered),
        }

    # ========== Helper Methods ==========

    def _normalize_parent_to_folder(
        self,
        project_id: str,
        parent_id: str | None,
        *,
        title: str,
        file_type: str,
        file_id: str | None = None,
    ) -> str | None:
        """把「挂到非 folder 节点下」的父节点就近归一到最近的 folder 祖先。

        为什么要归一而不是直接报错：parent_id 来自 LLM，模型高频把「上一章的
        文件 id」当作 parent 传进来。硬失败会打断整轮写作（模型往往原样重试），
        而归一后的结果恰好就是用户期望的位置（同一个章节文件夹）。
        真正非法、归一也救不了的情况（跨项目 / 已删除 / 成环）由随后的
        validate_parent_assignment 拒绝。

        Returns:
            归一后的 parent_id；一路向上都没有 folder 时返回 None（挂到根层）。
        """
        if parent_id is None:
            return None

        parent = self.session.get(File, parent_id)
        if parent is None or parent.is_deleted or parent.project_id != project_id:
            # 交给 validate_parent_assignment 统一报错，这里不做猜测
            return parent_id
        if parent.file_type == FILE_TYPE_FOLDER:
            return parent_id

        fallback_parent_id = find_nearest_folder_ancestor(
            self.session, project_id, parent_id
        )

        log_with_context(
            logger,
            30,  # WARNING
            "Parent is not a folder; re-anchoring to nearest folder ancestor",
            project_id=project_id,
            user_id=self.user_id,
            file_id=file_id,
            requested_parent_id=parent_id,
            requested_parent_type=parent.file_type,
            resolved_parent_id=fallback_parent_id,
            title=title,
            file_type=file_type,
        )

        return fallback_parent_id

    def _query_files_page(
        self,
        project_id: str,
        file_types: list[str] | None,
        query: str | None,
        parent_id: str | None,
        limit: int,
        offset: int,
    ) -> list[File]:
        """
        Query one page of files, optionally filtered to the given types.

        limit/offset are applied to the single combined statement, so they
        keep their declared semantics regardless of how many types are given.

        Args:
            project_id: Project ID
            file_types: File type filter (None for all types)
            query: Search keyword (optional)
            parent_id: Parent ID filter (optional)
            limit: Max results
            offset: Offset for pagination

        Returns:
            List of File objects
        """
        # Build base query
        stmt = select(File).where(
            File.project_id == project_id,
            File.is_deleted.is_(False)
        )

        # Apply file type filter
        if file_types:
            stmt = stmt.where(File.file_type.in_(file_types))  # type: ignore[attr-defined]

        # Apply keyword search
        if query:
            stmt = stmt.where(
                (File.title.contains(query)) | (File.content.contains(query))  # type: ignore[attr-defined]
            )

        # Apply parent filter
        if parent_id is not None:
            stmt = stmt.where(File.parent_id == parent_id)

        # Order and paginate. The id tiebreaker makes the total order
        # deterministic so pagination cannot duplicate/skip rows when
        # order/created_at tie across types.
        stmt = stmt.order_by(
            File.order.asc(),  # type: ignore[attr-defined]
            col(File.created_at).desc(),
            col(File.id).asc(),
        )
        stmt = stmt.offset(offset).limit(limit)

        return list(self.session.exec(stmt).all())

    def _filter_by_metadata(
        self,
        files: list[File],
        metadata_filter: dict[str, Any],
    ) -> list[File]:
        """
        Filter files by metadata fields.

        Since SQLite's JSON support is limited, we filter in Python.

        Args:
            files: List of File objects
            metadata_filter: Dict of field -> value to match

        Returns:
            Filtered list of files
        """
        filtered = []
        for file in files:
            # Parse file metadata
            if not file.file_metadata:
                continue

            try:
                file_meta = json.loads(file.file_metadata)
            except (json.JSONDecodeError, TypeError):
                continue

            # Check all filter conditions (AND logic)
            match = True
            for key, expected_value in metadata_filter.items():
                actual_value = file_meta.get(key)

                # Handle different comparison types
                if actual_value is None:
                    match = False
                    break
                elif isinstance(expected_value, list):
                    # For array fields like tags, check if any match
                    if isinstance(actual_value, list):
                        if not any(v in actual_value for v in expected_value):
                            match = False
                            break
                    else:
                        if actual_value not in expected_value:
                            match = False
                            break
                else:
                    # Direct equality comparison (case-insensitive for strings)
                    if isinstance(actual_value, str) and isinstance(expected_value, str):
                        if actual_value.lower() != expected_value.lower():
                            match = False
                            break
                    elif actual_value != expected_value:
                        match = False
                        break

            if match:
                filtered.append(file)

        return filtered

    def _delete_recursive(self, file: File, visited: set[str] | None = None) -> list[File]:
        """
        Delete a file and all its children recursively.

        Returns a list of File objects that were deleted (including the root file).
        Uses soft delete: marks files as deleted instead of removing from database.

        visited 用于抵御历史脏数据：库里可能已经存在自引用（parent_id 指向自己）
        或更长的环（这些行是本次修复前的 update_file 写进去的）。没有这层保护时
        递归删除会直接打成 RecursionError，整个工具调用失败且什么都删不掉。
        """
        if visited is None:
            visited = set()
        if file.id in visited:
            return []
        visited.add(file.id)

        deleted: list[File] = []

        # Get children that are not already deleted
        children = list(
            self.session.exec(
                select(File).where(
                    File.parent_id == file.id,
                    File.is_deleted.is_(False)
                )
            ).all()
        )
        for child in children:
            deleted.extend(self._delete_recursive(child, visited))

        deleted.append(file)
        # Soft delete: mark as deleted instead of removing from database
        file.is_deleted = True
        file.deleted_at = utcnow()

        return deleted

    def _serialize_metadata(self, metadata: dict[str, Any] | None) -> str | None:
        """Serialize metadata dict to JSON string."""
        if metadata is None:
            return None
        return json.dumps(metadata)

    def _create_version(
        self,
        file_id: str,
        content: str,
        *,
        change_type: str = CHANGE_TYPE_AI_EDIT,
        change_summary: str = "AI 更新文件内容",
    ) -> None:
        """Stage version history in the caller's content transaction."""
        FileVersionService().create_version(
            session=self.session,
            file_id=file_id,
            new_content=content,
            change_type=change_type,
            change_source=CHANGE_SOURCE_AI,
            change_summary=change_summary,
            commit=False,
        )

    def _schedule_index_upsert(
        self,
        file: File,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Fire-and-forget vector index upsert (do not block)."""
        try:
            from agent.tools.mcp_tools import ToolContext
            from services.llama_index import schedule_index_upsert

            metadata = extra_metadata or {}
            if file.file_metadata:
                with contextlib.suppress(Exception):
                    metadata = {**metadata, **json.loads(file.file_metadata)}
            if file.parent_id:
                metadata = {**metadata, "parent_id": file.parent_id}

            user_id = ToolContext._get_context().get("user_id")

            schedule_index_upsert(
                project_id=file.project_id,
                entity_type=file.file_type,
                entity_id=file.id,
                title=file.title,
                content=file.content or "",
                extra_metadata=metadata,
                user_id=user_id,
            )
        except Exception:
            pass

    def _schedule_index_delete(self, files: list[File]) -> None:
        """Fire-and-forget vector index delete (do not block)."""
        try:
            from agent.tools.mcp_tools import ToolContext
            from services.llama_index import schedule_index_delete

            user_id = ToolContext._get_context().get("user_id")

            for f in files:
                schedule_index_delete(
                    project_id=f.project_id,
                    entity_type=f.file_type,
                    entity_id=f.id,
                    user_id=user_id,
                )
        except Exception:
            pass


__all__ = [
    "FileCRUD",
    "find_nearest_folder_ancestor",
    "is_descendant_of",
    "validate_parent_assignment",
]
