"""
File version service.

Handles file version creation, retrieval, diff computation, and rollback.
Uses incremental diff storage with periodic base versions for efficiency.
"""

import contextlib
import difflib
import json
import re
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, col, func, select

from config.datetime_utils import advance_timestamp, normalize_datetime_to_utc, utcnow
from models import File, FileVersion
from models.file_version import (
    CHANGE_SOURCE_SYSTEM,
    CHANGE_SOURCE_USER,
    CHANGE_TYPE_AUTO_SAVE,
    CHANGE_TYPE_CREATE,
    CHANGE_TYPE_EDIT,
    CHANGE_TYPE_RESTORE,
    VERSION_BASE_INTERVAL,
)
from services.quota_service import quota_service
from utils.logger import get_logger
from utils.text_metrics import count_words

logger = get_logger(__name__)
MAX_CREATE_VERSION_RETRIES = 3
CONTENT_RECONSTRUCTION_BATCH_SIZE = 200


class FileVersionService:
    """Service for managing file versions with diff-based storage."""

    def create_initial_version(self, session: Session, file: File) -> FileVersion | None:
        """Capture a new populated file before its caller commits creation.

        This structural baseline is not a user save and does not consume their
        version quota. Only call for newly added files; existing-file history
        and its best-effort/quota semantics still use create_version directly.
        An unexpected error propagates so creation and v1 roll back together.
        """
        if file.file_type == "folder" or not file.content:
            return None

        session.flush()
        return self.create_version(
            session=session,
            file_id=file.id,
            new_content=file.content,
            change_type=CHANGE_TYPE_CREATE,
            change_source=CHANGE_SOURCE_SYSTEM,
            change_summary="Initial version",
            force_base=True,
            skip_quota=True,
            commit=False,
        )

    def create_version(
        self,
        session: Session,
        file_id: str,
        new_content: str,
        change_type: str = CHANGE_TYPE_EDIT,
        change_source: str = CHANGE_SOURCE_USER,
        change_summary: str | None = None,
        force_base: bool = False,
        user_id: str | None = None,
        skip_quota: bool = False,
        quota_source: str | None = None,
        commit: bool = True,
    ) -> FileVersion:
        """
        Create a new version of a file.

        Args:
            session: Database session
            file_id: ID of the file
            new_content: New content of the file
            change_type: Type of change (create/edit/ai_edit/restore/auto_save)
            change_source: Source of change (user/ai/system)
            change_summary: Optional description of changes
            force_base: Force this to be a base version (full content)
            user_id: Optional user ID for quota checking
            skip_quota: 跳过配额闸门（调用方已自行预检，或本次是不可被配额阻断的
                恢复类操作，例如版本回滚）
            quota_source: 配额判定用的来源。缺省时沿用 change_source；
                当 change_source 可能来自**客户端请求体**时，调用方必须显式传入
                服务端自己判定的来源（见下方说明），否则配额可被绕过。
            commit: Whether this service owns the transaction. Content-writing
                callers pass False, flush the version in their existing locked
                transaction, and commit content plus snapshot exactly once.

        Returns:
            Created FileVersion object
        """
        # 配额闸门只约束「用户来源」的版本。
        #
        # 历史缺陷：AI 侧（agent/tools/file_ops/*.py）建版本时不传 user_id，永远
        # 不被检查；而计数用的 get_version_count 又把 AI 行一并算进用户额度。
        # 结果免费用户（每文件 10 版）在十来次 AI 编辑之后，自己的手动保存和版本
        # 回滚被 AI 自己耗尽的额度挡住，且没有任何回收路径。
        # 因此这里把「被计数的集合」和「被闸门约束的集合」对齐成同一个：
        # 只有 user 来源的版本占用户额度，也只有它会被拒绝。
        #
        # 但「来源」绝不能直接取自请求体：POST /files/{id}/versions 的
        # change_source 是客户端可控字段，若拿它当闸门判据，任何登录用户发一个
        # {"change_source": "ai"} 就能无限建版本。因此判据独立成 quota_source，
        # 由服务端调用方决定；只有内部调用（agent / snapshot）才允许省略。
        effective_quota_source = quota_source if quota_source is not None else change_source
        enforce_user_quota = (
            bool(user_id)
            and not skip_quota
            and effective_quota_source == CHANGE_SOURCE_USER
        )

        # The quota count and version insert must share one per-file transaction
        # boundary. Otherwise two authenticated requests can both observe N slots
        # used and then insert N+1/N+2, exceeding the plan limit. PostgreSQL's file
        # row is the serialization point; SQLite REST callers use the same striped
        # process lock around this service call.
        if enforce_user_quota:
            from database import is_postgres

            if is_postgres:
                file = session.exec(
                    select(File)
                    .where(File.id == file_id)
                    .with_for_update(key_share=True)
                ).first()
            else:
                file = session.get(File, file_id, populate_existing=True)
        else:
            file = session.get(File, file_id)

        if not file or file.is_deleted:
            raise ValueError(f"File {file_id} not found")

        if enforce_user_quota:
            assert user_id is not None
            allowed, existing_count, max_versions = self.check_user_version_quota(
                session,
                file_id,
                user_id,
                # create_version owns (or participates in) the encompassing
                # transaction. A lazy subscription expiry must be committed
                # with the snapshot, never from this nested precheck.
                commit=False,
            )
            if not allowed:
                from core.error_codes import ErrorCode
                from core.error_handler import APIException

                raise APIException(
                    error_code=ErrorCode.QUOTA_FILE_VERSIONS_EXCEEDED,
                    status_code=402,
                    detail=f"Version limit reached ({existing_count}/{max_versions}). Please upgrade your plan.",
                )

        for attempt in range(1, MAX_CREATE_VERSION_RETRIES + 1):
            # Get latest version
            latest = self.get_latest_version(session, file_id)
            version_number = (latest.version_number + 1) if latest else 1

            # Determine if this should be a base version
            is_base = (
                force_base
                or version_number == 1
                or (version_number % VERSION_BASE_INTERVAL == 0)
                or change_type == CHANGE_TYPE_CREATE
            )

            # Get previous content for diff
            previous_content = ""
            if latest:
                previous_content = self.get_content_at_version(
                    session,
                    file_id,
                    latest.version_number,
                )

            # Calculate diff statistics
            lines_added, lines_removed = self._calculate_diff_stats(
                previous_content,
                new_content,
            )

            # Store content or diff
            content = (
                new_content
                if is_base
                else self._create_diff(previous_content, new_content)
            )

            # Calculate word/char counts
            word_count = count_words(new_content)
            char_count = len(new_content)

            # Create version
            version = FileVersion(
                file_id=file_id,
                project_id=file.project_id,
                version_number=version_number,
                content=content,
                is_base_version=is_base,
                word_count=word_count,
                char_count=char_count,
                change_type=change_type,
                change_source=change_source,
                change_summary=change_summary,
                lines_added=lines_added,
                lines_removed=lines_removed,
            )

            session.add(version)
            if not commit:
                session.flush()
                return version

            try:
                session.commit()
                session.refresh(version)
                return version
            except IntegrityError as err:
                session.rollback()
                if attempt >= MAX_CREATE_VERSION_RETRIES:
                    raise ValueError(
                        "Failed to create version due to concurrent updates. Please retry."
                    ) from err
                logger.warning(
                    "Version number conflict detected, retrying create_version",
                    extra={"file_id": file_id, "attempt": attempt},
                )

        raise ValueError("Failed to create file version")

    def get_versions(
        self,
        session: Session,
        file_id: str,
        limit: int = 50,
        offset: int = 0,
        include_auto_save: bool = False,
    ) -> list[FileVersion]:
        """
        Get version history for a file.

        Args:
            session: Database session
            file_id: ID of the file
            limit: Maximum number of versions to return
            offset: Number of versions to skip
            include_auto_save: Whether to include auto-save versions

        Returns:
            List of FileVersion objects (newest first)
        """
        query = select(FileVersion).where(FileVersion.file_id == file_id)

        if not include_auto_save:
            query = query.where(FileVersion.change_type != CHANGE_TYPE_AUTO_SAVE)

        query = (
            query.order_by(FileVersion.version_number.desc())  # type: ignore[attr-defined]
            .offset(offset)
            .limit(limit)
        )

        return list(session.exec(query).all())

    def get_version(
        self, session: Session, version_id: str
    ) -> FileVersion | None:
        """Get a specific version by ID."""
        return session.get(FileVersion, version_id)

    def get_version_by_number(
        self, session: Session, file_id: str, version_number: int
    ) -> FileVersion | None:
        """Get a file's version by its per-file version number."""
        return session.exec(
            select(FileVersion).where(
                FileVersion.file_id == file_id,
                FileVersion.version_number == version_number,
            )
        ).first()

    def get_latest_version(
        self, session: Session, file_id: str
    ) -> FileVersion | None:
        """Get the latest version for a file."""
        query = (
            select(FileVersion)
            .where(FileVersion.file_id == file_id)
            .order_by(FileVersion.version_number.desc())  # type: ignore[attr-defined]
            .limit(1)
        )
        return session.exec(query).first()

    def get_content_at_version(
        self, session: Session, file_id: str, version_number: int
    ) -> str:
        """
        Reconstruct content at a specific version.

        This may require applying diffs from the nearest base version.

        Args:
            session: Database session
            file_id: ID of the file
            version_number: Version number to retrieve

        Returns:
            Content at the specified version
        """
        # Get the target version
        target = session.exec(
            select(FileVersion).where(
                FileVersion.file_id == file_id,
                FileVersion.version_number == version_number,
            )
        ).first()

        if not target:
            raise ValueError(f"Version {version_number} not found for file {file_id}")

        return self._get_contents_for_versions(session, {file_id: target})[file_id]

    def _get_contents_for_versions(
        self, session: Session, targets: Mapping[str, FileVersion]
    ) -> dict[str, str]:
        """Replay already-selected targets, one per file, without per-file SQL.

        Full bases need no query. Delta targets fetch only their nearest base and
        bounded chains; chunking bounds query predicates, not the number of files.
        """
        contents = {file_id: target.content for file_id, target in targets.items() if target.is_base_version}
        deltas = [(file_id, target) for file_id, target in targets.items() if not target.is_base_version]
        for offset in range(0, len(deltas), CONTENT_RECONSTRUCTION_BATCH_SIZE):
            chunk = deltas[offset:offset + CONTENT_RECONSTRUCTION_BATCH_SIZE]
            target_bounds = or_(*(
                and_(col(FileVersion.file_id) == file_id, col(FileVersion.version_number) <= target.version_number)
                for file_id, target in chunk
            ))
            base_numbers = (
                select(FileVersion.file_id, func.max(col(FileVersion.version_number)).label("number"))
                .where(col(FileVersion.is_base_version).is_(True), target_bounds)
                .group_by(col(FileVersion.file_id))
                .subquery()
            )
            bases = {version.file_id: version for version in session.exec(
                select(FileVersion).join(base_numbers,
                    (col(FileVersion.file_id) == base_numbers.c.file_id)
                    & (col(FileVersion.version_number) == base_numbers.c.number))
            ).all()}
            chain_bounds = []
            for file_id, target in chunk:
                base = bases.get(file_id)
                contents[file_id] = base.content if base else ""
                chain_bounds.append(and_(
                    col(FileVersion.file_id) == file_id,
                    col(FileVersion.version_number) >= (base.version_number + 1 if base else 1),
                    col(FileVersion.version_number) <= target.version_number,
                ))
            chain = session.exec(
                select(FileVersion)
                .where(col(FileVersion.is_base_version).is_(False), or_(*chain_bounds))
                .order_by(col(FileVersion.file_id), col(FileVersion.version_number))
            ).all()
            for version in chain:
                contents[version.file_id] = self._apply_diff(contents[version.file_id], version.content)
        return contents

    def compare_versions(
        self,
        session: Session,
        file_id: str,
        version1: int,
        version2: int,
    ) -> dict[str, Any]:
        """
        Compare two versions of a file.

        Args:
            session: Database session
            file_id: ID of the file
            version1: First version number (older)
            version2: Second version number (newer)

        Returns:
            Dict with comparison data including unified diff
        """
        v1 = self.get_version_by_number(session, file_id, version1)
        if v1 is None:
            raise ValueError(f"Version {version1} not found for file {file_id}")
        v2 = v1 if version1 == version2 else self.get_version_by_number(session, file_id, version2)
        if v2 is None:
            raise ValueError(f"Version {version2} not found for file {file_id}")
        content1 = self._get_contents_for_versions(session, {file_id: v1})[file_id]
        content2 = content1 if version1 == version2 else self._get_contents_for_versions(session, {file_id: v2})[file_id]

        # Generate unified diff
        diff_lines = list(
            difflib.unified_diff(
                content1.splitlines(keepends=True),
                content2.splitlines(keepends=True),
                fromfile=f"v{version1}",
                tofile=f"v{version2}",
                lineterm="",
            )
        )

        # Generate HTML diff for display
        html_diff = self._generate_html_diff(content1, content2)

        # Structured rows already encode the same splitlines comparison used
        # for counts; do not run a third SequenceMatcher just for statistics.
        lines_added = sum(line["type"] == "added" for line in html_diff)
        lines_removed = sum(line["type"] == "removed" for line in html_diff)

        return {
            "file_id": file_id,
            "version1": {
                "number": version1,
                "created_at": v1.created_at.isoformat() if v1 else None,
                "change_type": v1.change_type if v1 else None,
                "change_source": v1.change_source if v1 else None,
                "word_count": v1.word_count if v1 else 0,
            },
            "version2": {
                "number": version2,
                "created_at": v2.created_at.isoformat() if v2 else None,
                "change_type": v2.change_type if v2 else None,
                "change_source": v2.change_source if v2 else None,
                "word_count": v2.word_count if v2 else 0,
            },
            "unified_diff": "".join(diff_lines),
            "html_diff": html_diff,
            "stats": {
                "lines_added": lines_added,
                "lines_removed": lines_removed,
                "word_diff": (v2.word_count if v2 else 0) - (v1.word_count if v1 else 0),
            },
        }

    def rollback_to_version(
        self,
        session: Session,
        file_id: str,
        version_number: int,
        user_id: str,
        *,
        expected_updated_at: datetime | None = None,
    ) -> tuple[File, FileVersion | None, bool]:
        """
        Rollback a file to a previous version.

        Creates a new version with the old content (doesn't delete history).

        Args:
            session: Database session
            file_id: ID of the file
            version_number: Version number to rollback to
            user_id: User ID for quota checking
            expected_updated_at: Optional exact post-edit token for immutable undo

        Returns:
            Tuple of (updated File, optional new FileVersion,
            version_quota_exceeded). When user version quota is full the content
            is still restored, but no snapshot is added.
        """
        # 回滚同样是「读旧内容 -> 整篇覆盖 File.content」的用户侧写入，必须和
        # agent 的 edit_file 走同一条串行化通道；否则 agent 的写入会插在读版本
        # 与提交之间，版本链和正文对不上。
        # 延迟 import：agent.tools.file_ops.edit 反过来依赖本模块。
        from agent.tools.file_ops.edit import file_write_lock
        from database import is_postgres

        lock_ctx = contextlib.nullcontext() if is_postgres else file_write_lock(file_id)

        with lock_ctx:
            # Get the file（PG 取行锁，SQLite 强制重新 SELECT，避免用到 identity
            # map 里的陈旧副本）
            if is_postgres:
                file = session.exec(
                    select(File).where(File.id == file_id).with_for_update(key_share=True)
                    .execution_options(populate_existing=True)
                ).first()
            else:
                file = session.get(File, file_id, populate_existing=True)
            if not file or file.is_deleted:
                raise ValueError(f"File {file_id} not found")

            if expected_updated_at is not None:
                current = normalize_datetime_to_utc(file.updated_at)
                expected = normalize_datetime_to_utc(expected_updated_at)
                if current != expected:
                    from core.error_codes import ErrorCode
                    from core.error_handler import APIException

                    raise APIException(
                        error_code=ErrorCode.RESOURCE_CONFLICT,
                        status_code=409,
                        detail={
                            "reason": "stale_write",
                            "file_id": file.id,
                            "current_updated_at": current.isoformat(),
                            "base_updated_at": expected.isoformat(),
                        },
                    )

            content = self.get_content_at_version(session, file_id, version_number)

            # Update file content
            file.content = content
            file.updated_at = advance_timestamp(file.updated_at, now=utcnow())
            session.add(file)

            # Restoration itself is never blocked. The audit snapshot still
            # belongs to the user's quota, though; otherwise repeated rollbacks
            # can create unlimited full base rows.
            has_quota, _used, _limit = self.check_user_version_quota(
                session,
                file_id,
                user_id,
                # rollback_to_version commits restored content and the optional
                # snapshot once, after this precheck.
                commit=False,
            )
            version_quota_exceeded = not has_quota
            new_version: FileVersion | None = None
            if has_quota:
                try:
                    with session.begin_nested():
                        new_version = self.create_version(
                            session=session,
                            file_id=file_id,
                            new_content=content,
                            change_type=CHANGE_TYPE_RESTORE,
                            change_source=CHANGE_SOURCE_USER,
                            change_summary=f"Restored to version {version_number}",
                            force_base=True,
                            user_id=user_id,
                            # The check above ran under the file write lock.
                            skip_quota=True,
                            commit=False,
                        )
                except Exception:
                    logger.warning(
                        "Failed to create rollback snapshot; restored content will persist",
                        exc_info=True,
                        extra={"file_id": file_id, "version_number": version_number},
                    )
                    new_version = None

            session.commit()
            session.refresh(file)

        return file, new_version, version_quota_exceeded

    def get_version_count(
        self,
        session: Session,
        file_id: str,
        include_auto_save: bool = True,
        change_source: str | None = None,
    ) -> int:
        """
        Get total number of versions for a file.

        Args:
            change_source: 只统计该来源（user/ai/system）的版本；None 表示不过滤。
                配额判定必须传 CHANGE_SOURCE_USER，否则 AI/系统写入的版本会占用
                用户的 per-file 版本额度。
        """
        query = select(func.count(FileVersion.id)).where(FileVersion.file_id == file_id)  # type: ignore[arg-type]

        if not include_auto_save:
            query = query.where(FileVersion.change_type != CHANGE_TYPE_AUTO_SAVE)

        if change_source is not None:
            query = query.where(FileVersion.change_source == change_source)

        result = session.exec(query).one()
        return int(result or 0)

    def check_user_version_quota(
        self,
        session: Session,
        file_id: str,
        user_id: str,
        *,
        commit: bool = True,
    ) -> tuple[bool, int, int]:
        """
        预检用户在该文件上的版本额度。

        供调用方在「改动落库之前」判断本次保存能否附带版本快照，避免出现
        「正文已 commit 却抛 402」的误导性失败（见 api/files.py 的 update_file）。

        Returns:
            (是否还有额度, 已占用的用户版本数, 计划上限；-1 表示不限)

        ``commit=False`` keeps lazy subscription-expiry writes inside the
        caller's transaction. Standalone checks retain the historical
        service-owned behavior by default.
        """
        plan = quota_service.get_user_plan(session, user_id, commit=commit)
        if not plan:
            return True, 0, -1

        max_versions = plan.features.get("file_versions_per_file", 10)
        if max_versions == -1:
            return True, 0, -1

        existing_count = self.get_version_count(
            session,
            file_id,
            change_source=CHANGE_SOURCE_USER,
        )
        return existing_count < max_versions, existing_count, max_versions

    # Private helper methods

    def _create_diff(self, old_content: str, new_content: str) -> str:
        """Create a diff between two contents (stored as JSON)."""
        diff = list(
            difflib.unified_diff(
                old_content.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
            )
        )
        return json.dumps(diff)

    def _apply_diff(self, content: str, diff_json: str) -> str:
        """Apply a diff to content to get new content."""
        try:
            diff_lines = json.loads(diff_json)
        except json.JSONDecodeError:
            # If diff is not valid JSON, assume it's raw content
            return diff_json

        if not diff_lines:
            return content

        # Parse unified diff and apply
        lines = content.splitlines(keepends=True)
        result_lines = []
        line_idx = 0
        in_hunk = False

        for line in diff_lines:
            # Parse hunk header (in-hunk content lines always start with
            # " ", "-" or "+", so "@@" here is unambiguous)
            if line.startswith("@@"):
                in_hunk = True
                # Extract line numbers from @@ -start,count +start,count @@
                match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
                if match:
                    old_start = int(match.group(1)) - 1  # 0-indexed

                    # Add unchanged lines before this hunk
                    while line_idx < old_start and line_idx < len(lines):
                        result_lines.append(lines[line_idx])
                        line_idx += 1
                continue

            # File headers ("---"/"+++") only appear before the first hunk;
            # inside a hunk the same prefixes can be real content (e.g. a
            # removed "---" separator line becomes "----"), so header
            # skipping must stop once the first hunk starts
            if not in_hunk:
                continue

            # Process diff content
            if line.startswith("-"):
                # Line removed - skip it in original
                line_idx += 1
            elif line.startswith("+"):
                # Line added - add to result
                result_lines.append(line[1:])
            elif line.startswith(" "):
                # Context line - copy from original
                if line_idx < len(lines):
                    result_lines.append(lines[line_idx])
                    line_idx += 1
            else:
                # Unknown line type, add as-is
                if line_idx < len(lines):
                    result_lines.append(lines[line_idx])
                    line_idx += 1

        # Add remaining lines
        while line_idx < len(lines):
            result_lines.append(lines[line_idx])
            line_idx += 1

        return "".join(result_lines)

    def _calculate_diff_stats(
        self, old_content: str, new_content: str
    ) -> tuple[int, int]:
        """Calculate lines added and removed."""
        old_lines = old_content.splitlines()
        new_lines = new_content.splitlines()

        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        added = 0
        removed = 0

        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "replace":
                removed += i2 - i1
                added += j2 - j1
            elif op == "delete":
                removed += i2 - i1
            elif op == "insert":
                added += j2 - j1

        return added, removed

    def _generate_html_diff(self, old_content: str, new_content: str) -> list[dict]:
        """
        Generate structured diff data for HTML rendering.

        Returns a list of diff operations for the frontend to render.
        """
        old_lines = old_content.splitlines()
        new_lines = new_content.splitlines()

        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        result = []

        for op, i1, i2, j1, j2 in matcher.get_opcodes():
            if op == "equal":
                for i in range(i1, i2):
                    result.append({
                        "type": "equal",
                        "old_line": i + 1,
                        "new_line": j1 + (i - i1) + 1,
                        "content": old_lines[i],
                    })
            elif op == "replace":
                # Show removed lines
                for i in range(i1, i2):
                    result.append({
                        "type": "removed",
                        "old_line": i + 1,
                        "new_line": None,
                        "content": old_lines[i],
                    })
                # Show added lines
                for j in range(j1, j2):
                    result.append({
                        "type": "added",
                        "old_line": None,
                        "new_line": j + 1,
                        "content": new_lines[j],
                    })
            elif op == "delete":
                for i in range(i1, i2):
                    result.append({
                        "type": "removed",
                        "old_line": i + 1,
                        "new_line": None,
                        "content": old_lines[i],
                    })
            elif op == "insert":
                for j in range(j1, j2):
                    result.append({
                        "type": "added",
                        "old_line": None,
                        "new_line": j + 1,
                        "content": new_lines[j],
                    })

        return result


# Singleton instance
_file_version_service: FileVersionService | None = None


def get_file_version_service() -> FileVersionService:
    """Get singleton file version service instance."""
    global _file_version_service
    if _file_version_service is None:
        _file_version_service = FileVersionService()
    return _file_version_service
