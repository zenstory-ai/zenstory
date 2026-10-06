"""
Version management service.

Handles snapshot creation, retrieval, rollback, and version management.
Uses the unified File model and integrates with FileVersion for version references.

Snapshot format v3:
- Stores file version references instead of full content
- Much more space-efficient
- Links to FileVersion for actual content
"""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import update
from sqlmodel import Session, func, select

from config.datetime_utils import advance_timestamp, utcnow
from models import File, FileVersion, Snapshot
from models.file_version import (
    CHANGE_SOURCE_SYSTEM,
    CHANGE_TYPE_CREATE,
    CHANGE_TYPE_EDIT,
    CHANGE_TYPE_RESTORE,
)
from models.utils import generate_uuid
from services.file_tree_rules import lock_project_for_files
from utils.logger import get_logger, log_with_context
from utils.text_metrics import count_words

from .file_version_service import get_file_version_service

logger = get_logger(__name__)

# Default to the same threshold used by HTTP logging middleware.
SLOW_SNAPSHOT_THRESHOLD_MS = int(
    os.getenv("SLOW_SNAPSHOT_THRESHOLD_MS", os.getenv("SLOW_REQUEST_THRESHOLD", "500"))
)


class VersionService:
    """Service for managing project snapshots (milestones)."""

    def create_snapshot(
        self,
        session: Session,
        project_id: str,
        file_id: str | None = None,
        description: str | None = None,
        snapshot_type: str = "auto",
        commit: bool = True,
    ) -> Snapshot:
        """
        Create a snapshot of the current state.

        Snapshots store file version references instead of full content,
        making them much more space-efficient.

        Args:
            session: Database session
            project_id: Project ID
            file_id: Optional specific file ID to snapshot
            description: Optional user description
            snapshot_type: Type of snapshot (auto/manual/pre_ai_edit)

        Returns:
            Created Snapshot object
        """
        start_time = time.perf_counter()

        # Standalone snapshots must not gather across a project rollback. The
        # rollback caller already owns the stronger gate in this transaction.
        lock_project_for_files(session, project_id)

        # Gather data to snapshot
        data, gather_stats = self._gather_snapshot_data(session, project_id, file_id)
        gather_ms = (time.perf_counter() - start_time) * 1000

        # Create snapshot
        snapshot = Snapshot(
            project_id=project_id,
            file_id=file_id,
            data=json.dumps(data, default=str),
            description=description,
            snapshot_type=snapshot_type,
            version=3
        )

        session.add(snapshot)
        # Flush snapshot insert so FK constraints pass when we link versions.
        flush_start = time.perf_counter()
        session.flush()
        flush_ms = (time.perf_counter() - flush_start) * 1000

        # Link file versions to this snapshot
        link_start = time.perf_counter()
        self._link_versions_to_snapshot(session, snapshot.id, data)
        link_ms = (time.perf_counter() - link_start) * 1000

        # Single commit for snapshot + version linking. Rollback owns the wider
        # transaction and therefore asks this helper to only flush.
        commit_start = time.perf_counter()
        if commit:
            session.commit()
            session.refresh(snapshot)
        commit_ms = (time.perf_counter() - commit_start) * 1000

        total_ms = (time.perf_counter() - start_time) * 1000
        if total_ms > SLOW_SNAPSHOT_THRESHOLD_MS:
            log_with_context(
                logger,
                logging.WARNING,
                "Snapshot creation slow",
                project_id=project_id,
                file_id=file_id,
                snapshot_id=snapshot.id,
                snapshot_type=snapshot_type,
                duration_ms=round(total_ms, 2),
                gather_ms=round(gather_ms, 2),
                flush_ms=round(flush_ms, 2),
                link_ms=round(link_ms, 2),
                commit_ms=round(commit_ms, 2),
                **gather_stats,
            )

        return snapshot

    def get_snapshots(
        self,
        session: Session,
        project_id: str,
        file_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Snapshot]:
        """
        Get snapshots for a project or file.

        Args:
            session: Database session
            project_id: Project ID
            file_id: Optional file ID to filter by
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            List of Snapshot objects
        """
        query = select(Snapshot).where(Snapshot.project_id == project_id)

        if file_id is not None:
            query = query.where(Snapshot.file_id == file_id)

        query = query.order_by(Snapshot.created_at.desc(), Snapshot.id.desc()).offset(offset).limit(limit)  # type: ignore[attr-defined]

        return list(session.exec(query).all())

    def get_snapshot(
        self,
        session: Session,
        snapshot_id: str
    ) -> Snapshot | None:
        """Get a specific snapshot by ID."""
        return session.get(Snapshot, snapshot_id)

    def update_description(
        self,
        session: Session,
        snapshot_id: str,
        description: str
    ) -> Snapshot:
        """Update snapshot description."""
        snapshot = session.get(Snapshot, snapshot_id)
        if not snapshot:
            raise ValueError(f"Snapshot {snapshot_id} not found")

        snapshot.description = description
        session.add(snapshot)
        session.commit()
        session.refresh(snapshot)

        return snapshot

    def rollback_to_snapshot(
        self,
        session: Session,
        snapshot_id: str
    ) -> dict[str, Any]:
        """
        Rollback project/file to a previous snapshot.

        This restores the data from the snapshot.
        Note: Creates a new snapshot before rollback for safety.

        Args:
            session: Database session
            snapshot_id: Snapshot ID to rollback to

        Returns:
            Dict with rollback information
        """
        snapshot = session.get(Snapshot, snapshot_id)
        if not snapshot:
            raise ValueError(f"Snapshot {snapshot_id} not found")

        # Parse snapshot data
        snapshot_data = json.loads(snapshot.data)

        try:
            lock_project_for_files(session, snapshot.project_id, rollback=True)
            files_query = select(File).where(File.project_id == snapshot.project_id)
            if snapshot.file_id:
                files_query = files_query.where(File.id == snapshot.file_id)
            from database import is_postgres

            if is_postgres:
                files_query = files_query.with_for_update(key_share=True)
            # Keep strong references and refresh before any safety/restore work.
            # Scope limits the File locks; the Project gate also covers recreation.
            locked_files = list(session.exec(
                files_query.order_by(File.id).execution_options(populate_existing=True)
            ).all())
            affected_file_ids = {file.id for file in locked_files}
            if snapshot.file_id:
                affected_file_ids = {snapshot.file_id}
            else:
                affected_file_ids.update(
                    item["id"] for item in snapshot_data.get("files_metadata", [])
                    if isinstance(item, dict) and item.get("id")
                )
                affected_file_ids.update(
                    item["file_id"] for item in snapshot_data.get("file_versions", [])
                    if isinstance(item, dict) and item.get("file_id")
                )

            # The safety snapshot, all file mutations, and all restore versions
            # form one transaction. A later-file failure must not leave a
            # partially restored project or a committed pre-rollback snapshot.
            pre_rollback = self.create_snapshot(
                session,
                project_id=snapshot.project_id,
                file_id=snapshot.file_id,
                description=f"Before rollback to snapshot {snapshot_id}",
                snapshot_type="pre_rollback",
                commit=False,
            )

            restored = self._restore_snapshot_data(
                session=session,
                snapshot_data=snapshot_data,
                project_id=snapshot.project_id,
                scope_file_id=snapshot.file_id,
                snapshot_id=snapshot.id,
            )
            session.commit()
        except Exception:
            session.rollback()
            raise

        return {
            "snapshot_id": snapshot_id,
            "pre_rollback_snapshot_id": pre_rollback.id,
            "restored": restored,
            "affected_file_ids": sorted(affected_file_ids),
        }

    def compare_snapshots(
        self,
        session: Session,
        snapshot_id_1: str | None = None,
        snapshot_id_2: str | None = None,
        snapshot1: Snapshot | None = None,
        snapshot2: Snapshot | None = None,
        parsed_data1: dict[str, Any] | None = None,
        parsed_data2: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """
        Compare two snapshots.

        Returns a structure showing which files changed between versions.

        Args:
            session: Database session
            snapshot_id_1: First snapshot ID (optional, use snapshot1 instead)
            snapshot_id_2: Second snapshot ID (optional, use snapshot2 instead)
            snapshot1: First snapshot object (recommended)
            snapshot2: Second snapshot object (recommended)
            parsed_data1: Pre-parsed JSON data for snapshot1 (optional, for optimization)
            parsed_data2: Pre-parsed JSON data for snapshot2 (optional, for optimization)

        Returns:
            Dict containing comparison data with snapshot1 being the older version
            and snapshot2 being the newer version
        """
        # Backward compatibility: fetch objects if IDs are provided
        if snapshot1 is None and snapshot_id_1:
            snapshot1 = session.get(Snapshot, snapshot_id_1)
        if snapshot2 is None and snapshot_id_2:
            snapshot2 = session.get(Snapshot, snapshot_id_2)

        if not snapshot1 or not snapshot2:
            raise ValueError("One or both snapshots not found")

        # Auto-sort by created_at timestamp to ensure old snapshot is first
        if snapshot1.created_at <= snapshot2.created_at:
            snap_old, snap_new = snapshot1, snapshot2
            data_old = parsed_data1 if parsed_data1 else json.loads(snap_old.data)
            data_new = parsed_data2 if parsed_data2 else json.loads(snap_new.data)
        else:
            snap_old, snap_new = snapshot2, snapshot1
            data_old = parsed_data2 if parsed_data2 else json.loads(snap_old.data)
            data_new = parsed_data1 if parsed_data1 else json.loads(snap_new.data)

        # Compare the union of content references and stored file metadata.
        fv_old = {fv["file_id"]: fv for fv in data_old.get("file_versions", [])}
        fv_new = {fv["file_id"]: fv for fv in data_new.get("file_versions", [])}
        metadata_old = {
            fm["id"]: fm
            for fm in data_old.get("files_metadata", [])
            if isinstance(fm, dict) and fm.get("id")
        }
        metadata_new = {
            fm["id"]: fm
            for fm in data_new.get("files_metadata", [])
            if isinstance(fm, dict) and fm.get("id")
        }

        old_ids = set(fv_old) | set(metadata_old)
        new_ids = set(fv_new) | set(metadata_new)

        def snapshot_entry(
            file_id: str,
            versions: dict[str, dict[str, Any]],
            metadata: dict[str, dict[str, Any]],
        ) -> dict[str, Any]:
            entry = {"file_id": file_id}
            entry.update(metadata.get(file_id, {}))
            entry.pop("id", None)
            entry.update(versions.get(file_id, {}))
            return entry

        changes = {
            "added": [
                snapshot_entry(fid, fv_new, metadata_new)
                for fid in sorted(new_ids - old_ids)
            ],
            "removed": [
                snapshot_entry(fid, fv_old, metadata_old)
                for fid in sorted(old_ids - new_ids)
            ],
            "modified": []
        }

        metadata_fields = ("title", "file_type", "parent_id", "order", "file_metadata")
        for file_id in sorted(old_ids & new_ids):
            old_version = fv_old.get(file_id, {}).get("version_number")
            new_version = fv_new.get(file_id, {}).get("version_number")
            metadata_changes = {
                field: {
                    "old": metadata_old.get(file_id, {}).get(field),
                    "new": metadata_new.get(file_id, {}).get(field),
                }
                for field in metadata_fields
                if metadata_old.get(file_id, {}).get(field)
                != metadata_new.get(file_id, {}).get(field)
            }
            if old_version != new_version or metadata_changes:
                modified = {
                    "file_id": file_id,
                    "old_version": old_version,
                    "new_version": new_version,
                    "old_title": metadata_old.get(file_id, {}).get("title"),
                    "new_title": metadata_new.get(file_id, {}).get("title"),
                    "old_file_type": metadata_old.get(file_id, {}).get("file_type"),
                    "new_file_type": metadata_new.get(file_id, {}).get("file_type"),
                }
                if metadata_changes:
                    modified["metadata_changes"] = metadata_changes
                changes["modified"].append(modified)

        return {
            "snapshot1": {"id": snap_old.id, "created_at": snap_old.created_at.isoformat()},
            "snapshot2": {"id": snap_new.id, "created_at": snap_new.created_at.isoformat()},
            "changes": changes
        }

    def cleanup_old_snapshots(
        self,
        session: Session,
        project_id: str,
        keep_recent: int = 20,
        keep_days: int = 30
    ) -> int:
        """
        Clean up old snapshots based on retention policy.

        Keeps:
        - Most recent N snapshots
        - All snapshots from the last N days

        Args:
            session: Database session
            project_id: Project ID
            keep_recent: Number of recent snapshots to keep
            keep_days: Number of days to keep snapshots

        Returns:
            Number of snapshots deleted
        """
        cutoff_date = utcnow() - timedelta(days=keep_days)

        # Get snapshots to delete
        query = select(Snapshot).where(
            Snapshot.project_id == project_id,
            Snapshot.created_at < cutoff_date
        ).order_by(Snapshot.created_at.desc()).offset(keep_recent)  # type: ignore[attr-defined]

        snapshots_to_delete = list(session.exec(query).all())

        try:
            if snapshots_to_delete:
                # Versions remain replayable after their optional snapshot link
                # is detached; both changes must share the deletion transaction.
                session.exec(
                    update(FileVersion)
                    .where(FileVersion.snapshot_id.in_([snapshot.id for snapshot in snapshots_to_delete]))
                    .values(snapshot_id=None)
                )
                for snapshot in snapshots_to_delete:
                    session.delete(snapshot)
            session.commit()
        except Exception:
            session.rollback()
            raise
        return len(snapshots_to_delete)

    # Private helper methods

    def _get_latest_versions_for_files(
        self,
        session: Session,
        file_ids: list[str],
    ) -> dict[str, FileVersion]:
        """Fetch latest FileVersion for each file in one query (avoids N+1)."""
        if not file_ids:
            return {}

        subq = (
            select(
                FileVersion.file_id,
                func.max(FileVersion.version_number).label("max_version"),
            )
            .where(FileVersion.file_id.in_(file_ids))
            .group_by(FileVersion.file_id)
            .subquery()
        )

        latest_versions = session.exec(
            select(FileVersion).join(
                subq,
                (FileVersion.file_id == subq.c.file_id)
                & (FileVersion.version_number == subq.c.max_version),
            )
        ).all()

        return {version.file_id: version for version in latest_versions}

    def _gather_snapshot_data(
        self,
        session: Session,
        project_id: str,
        file_id: str | None = None
    ) -> tuple[dict[str, Any], dict[str, int]]:
        """
        Gather data for snapshot using file version references.

        Stores references to FileVersion entries instead of full content.
        """
        file_version_service = get_file_version_service()
        data: dict[str, Any] = {
            "version": 3,
            "file_versions": [],  # List of {file_id, version_number, version_id}
            "files_metadata": [],  # File metadata without content
        }
        stats: dict[str, int] = {
            "file_count": 0,
            "content_file_count": 0,
            "version_ref_count": 0,
            "missing_version_file_count": 0,
            "baseline_version_created_count": 0,
            "live_version_created_count": 0,
        }

        # Get files
        if file_id:
            file = session.get(File, file_id, populate_existing=True)
            files = [file] if (file and not file.is_deleted) else []
        else:
            files = list(
                session.exec(
                    select(
                        File.id,
                        File.title,
                        File.content,
                        File.file_type,
                        File.parent_id,
                        File.order,
                        File.file_metadata,
                    ).where(
                        File.project_id == project_id,
                        File.is_deleted.is_(False),
                    )
                ).all()
            )

        # Identify content-bearing files (exclude folders) for bulk version lookup.
        content_file_ids: list[str] = []
        for file in files:
            if not file:
                continue
            file_type = getattr(file, "file_type", None)
            if file_type != "folder":
                content_file_ids.append(file.id)

        stats["file_count"] = len(files)
        stats["content_file_count"] = len(content_file_ids)

        latest_versions_by_file_id = self._get_latest_versions_for_files(
            session=session,
            file_ids=content_file_ids,
        )

        missing_version_file_ids = [fid for fid in content_file_ids if fid not in latest_versions_by_file_id]
        missing_version_file_id_set = set(missing_version_file_ids)
        stats["missing_version_file_count"] = len(missing_version_file_ids)
        if missing_version_file_ids:
            live_content_by_file_id = {
                file.id: (file.content or "")
                for file in files
                if file and file.id in missing_version_file_id_set
            }
            bind = session.get_bind()
            dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
            if dialect_name in {"postgresql", "sqlite"}:
                created_at = datetime.utcnow()
                baseline_rows: list[dict[str, Any]] = []
                for missing_file_id in missing_version_file_ids:
                    content = live_content_by_file_id.get(missing_file_id, "")
                    lines_added, lines_removed = file_version_service._calculate_diff_stats(  # type: ignore[attr-defined]
                        "", content
                    )
                    baseline_rows.append(
                        {
                            "id": generate_uuid(),
                            "file_id": missing_file_id,
                            "project_id": project_id,
                            "version_number": 1,
                            "content": content,
                            "is_base_version": True,
                            "word_count": count_words(content),
                            "char_count": len(content),
                            "change_type": CHANGE_TYPE_CREATE,
                            "change_source": CHANGE_SOURCE_SYSTEM,
                            "change_summary": "Snapshot baseline version",
                            "lines_added": lines_added,
                            "lines_removed": lines_removed,
                            "created_at": created_at,
                            "snapshot_id": None,
                        }
                    )

                if dialect_name == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert as dialect_insert
                else:
                    from sqlalchemy.dialects.sqlite import insert as dialect_insert

                insert_stmt = dialect_insert(FileVersion).values(baseline_rows)
                insert_stmt = insert_stmt.on_conflict_do_nothing(
                    index_elements=["file_id", "version_number"],
                )
                result = session.exec(insert_stmt)
                stats["baseline_version_created_count"] += max(
                    0, int(getattr(result, "rowcount", 0) or 0)
                )
                latest_versions_by_file_id.update(
                    self._get_latest_versions_for_files(
                        session=session,
                        file_ids=missing_version_file_ids,
                    )
                )
            else:
                for missing_file_id in missing_version_file_ids:
                    latest_version = file_version_service.create_version(
                        session=session,
                        file_id=missing_file_id,
                        new_content=live_content_by_file_id.get(missing_file_id, ""),
                        change_type=CHANGE_TYPE_CREATE,
                        change_source=CHANGE_SOURCE_SYSTEM,
                        change_summary="Snapshot baseline version",
                        force_base=True,
                        skip_quota=True,
                        commit=False,
                    )
                    stats["baseline_version_created_count"] += 1
                    latest_versions_by_file_id[missing_file_id] = latest_version

        version_contents = file_version_service._get_contents_for_versions(session, latest_versions_by_file_id)

        for file in files:
            if not file:
                continue

            file_id_value = file.id
            file_type_value = getattr(file, "file_type", None)

            if file_type_value != "folder":
                latest_version = latest_versions_by_file_id.get(file_id_value)
                live_content = file.content or ""
                latest_content = version_contents.get(file_id_value)
                if latest_content != live_content:
                    is_baseline = latest_version is None
                    latest_version = file_version_service.create_version(
                        session=session,
                        file_id=file_id_value,
                        new_content=live_content,
                        change_type=CHANGE_TYPE_CREATE if is_baseline else CHANGE_TYPE_EDIT,
                        change_source=CHANGE_SOURCE_SYSTEM,
                        change_summary=(
                            "Snapshot baseline version"
                            if is_baseline
                            else "Snapshot synchronized live content"
                        ),
                        force_base=is_baseline,
                        skip_quota=True,
                        commit=False,
                    )
                    stat_key = (
                        "baseline_version_created_count"
                        if is_baseline
                        else "live_version_created_count"
                    )
                    stats[stat_key] += 1
                    latest_versions_by_file_id[file_id_value] = latest_version

                if latest_version:
                    data["file_versions"].append(
                        {
                            "file_id": file_id_value,
                            "version_number": latest_version.version_number,
                            "version_id": latest_version.id,
                        }
                    )

            # Store file metadata (without content)
            data["files_metadata"].append({
                "id": file_id_value,
                "title": getattr(file, "title", None),
                "file_type": file_type_value,
                "parent_id": getattr(file, "parent_id", None),
                "order": getattr(file, "order", 0),
                "file_metadata": getattr(file, "file_metadata", None),
            })

        stats["version_ref_count"] = len(data["file_versions"])
        return data, stats

    def _link_versions_to_snapshot(
        self,
        session: Session,
        snapshot_id: str,
        snapshot_data: dict[str, Any]
    ):
        """Link file versions to a snapshot for easy querying."""
        version_ids = [
            fv.get("version_id")
            for fv in snapshot_data.get("file_versions", [])
            if isinstance(fv, dict) and fv.get("version_id")
        ]
        if not version_ids:
            return

        session.exec(
            update(FileVersion)
            .where(FileVersion.id.in_(version_ids))
            .values(snapshot_id=snapshot_id)
        )

    def _restore_snapshot_data(
        self,
        session: Session,
        snapshot_data: dict[str, Any],
        project_id: str,
        scope_file_id: str | None = None,
        snapshot_id: str | None = None,
    ) -> dict[str, int]:
        """Restore data from snapshot."""
        restored = {
            "files": 0,
            "recreated_files": 0,
            "undeleted_files": 0,
            "deleted_extra_files": 0,
            "restore_versions": 0,
        }
        file_version_service = get_file_version_service()
        now = utcnow()

        files_metadata = snapshot_data.get("files_metadata", [])
        metadata_map = {
            fm.get("id"): fm for fm in files_metadata if isinstance(fm, dict) and fm.get("id")
        }

        # Track file scope from both metadata and version references.
        snapshot_file_ids = set(metadata_map.keys())
        for fv in snapshot_data.get("file_versions", []):
            file_id = fv.get("file_id")
            if file_id:
                snapshot_file_ids.add(file_id)

        if scope_file_id:
            snapshot_file_ids = {scope_file_id}

        # Query project files once to support metadata restoration and full-project cleanup.
        project_files = list(session.exec(
            select(File).where(
                File.project_id == project_id,
            )
        ).all())
        project_file_map = {file.id: file for file in project_files}

        # Materialize every scoped row before assigning parent FKs. A missing
        # parent may appear later in the unordered snapshot IDs, and pending
        # inserts are not visible to Session.get when autoflush is disabled.
        recreated_file_ids: set[str] = set()
        for file_id in snapshot_file_ids:
            file = project_file_map.get(file_id)
            metadata = metadata_map.get(file_id)

            if file is None:
                if metadata is None:
                    continue

                file = File(
                    id=file_id,
                    project_id=project_id,
                    title=metadata.get("title", "Untitled"),
                    content="",
                    file_type=metadata.get("file_type", "draft"),
                    parent_id=None,
                    order=metadata.get("order", 0),
                    file_metadata=metadata.get("file_metadata"),
                    is_deleted=False,
                    deleted_at=None,
                    created_at=now,
                    updated_at=now,
                )
                project_file_map[file_id] = file
                recreated_file_ids.add(file_id)
                restored["recreated_files"] += 1
            else:
                if file.is_deleted:
                    file.is_deleted = False
                    file.deleted_at = None
                    restored["undeleted_files"] += 1

            session.add(file)

        if recreated_file_ids:
            session.flush()

        # All parent candidates now exist in the same-project map. Keep the
        # existing permissive parent policy and scoped outside-parent behavior.
        for file_id in snapshot_file_ids:
            file = project_file_map.get(file_id)
            metadata = metadata_map.get(file_id)
            if file is None or metadata is None:
                continue

            parent_id = metadata.get("parent_id")
            if parent_id and parent_id not in project_file_map:
                parent_id = None

            file.title = metadata.get("title", file.title)
            file.file_type = metadata.get("file_type", file.file_type)
            file.parent_id = parent_id
            file.order = metadata.get("order", file.order)
            if "file_metadata" in metadata:
                file.file_metadata = metadata["file_metadata"]
            if file_id not in recreated_file_ids:
                file.updated_at = advance_timestamp(file.updated_at, now=now)
            session.add(file)

        # Restore file content from version references
        for fv in snapshot_data.get("file_versions", []):
            file_id = fv.get("file_id")
            version_number = fv.get("version_number")
            if not file_id or version_number is None:
                continue

            if scope_file_id and file_id != scope_file_id:
                continue

            file = project_file_map.get(file_id)
            if not file:
                continue

            # Get content from the referenced version
            content = file_version_service.get_content_at_version(
                session, file_id, version_number
            )
            previous_content = file.content or ""
            content_changed = previous_content != content

            file.content = content
            file.updated_at = advance_timestamp(file.updated_at, now=now)
            file.is_deleted = False
            file.deleted_at = None
            session.add(file)
            restored["files"] += 1

            if content_changed:
                restored_version = file_version_service.create_version(
                    session=session,
                    file_id=file.id,
                    new_content=content,
                    change_type=CHANGE_TYPE_RESTORE,
                    change_source=CHANGE_SOURCE_SYSTEM,
                    change_summary=(
                        f"Restored from snapshot {snapshot_id}"
                        if snapshot_id
                        else "Restored from snapshot"
                    ),
                    force_base=True,
                    skip_quota=True,
                    commit=False,
                )
                if snapshot_id:
                    restored_version.snapshot_id = snapshot_id
                    session.add(restored_version)
                restored["restore_versions"] += 1

        # Full-project rollback should hide files that did not exist in the snapshot.
        if not scope_file_id:
            for project_file in project_files:
                if project_file.id in snapshot_file_ids:
                    continue
                if project_file.is_deleted:
                    continue

                project_file.is_deleted = True
                project_file.deleted_at = now
                project_file.updated_at = advance_timestamp(project_file.updated_at, now=now)
                session.add(project_file)
                restored["deleted_extra_files"] += 1

        return restored


# Singleton instance
_version_service: VersionService | None = None


def get_version_service() -> VersionService:
    """Get singleton version service instance."""
    global _version_service
    if _version_service is None:
        _version_service = VersionService()
    return _version_service
