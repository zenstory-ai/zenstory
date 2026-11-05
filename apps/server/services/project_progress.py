"""作品的写作进度：写了几章（集）、多少字，以及框架已就绪但还没开写。

作品列表卡片和聊天输入框上方的「写第一章」按钮共用这一个判断。字数按编辑器
口径，只算正文类文件，内容为空的不算「已写」。
"""

from __future__ import annotations

import json as json_module
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import load_only
from sqlmodel import Session, col, func, select, update

from agent.constants import CONTENT_FILE_TYPES
from models import File, Project
from models.file_model import WORD_COUNT_REV, cached_word_count
from utils.logger import get_logger, log_with_context
from utils.text_metrics import count_words

logger = get_logger(__name__)

PLANNING_FILE_TYPES = ("outline", "character", "lore")


def resolve_prose_word_counts(
    session: Session, files: list[File], **log_fields: Any
) -> dict[str, int]:
    """Editor word count per file id: trust the current-revision cache, recompute the rest.

    ``files`` may be loaded with only ``id`` and ``file_metadata``. Files whose cache is
    missing or from an older counting revision are reloaded with content and recounted.
    The backfill is written back only if the row still holds the content and metadata
    it was counted from, so it never overwrites a save that landed in between (that
    save stamps its own count); a failed backfill commit is logged and the computed
    counts returned.
    """
    counts: dict[str, int] = {}
    stale_ids: list[str] = []
    for file in files:
        cached = cached_word_count(file.file_metadata)
        if cached is None:
            stale_ids.append(file.id)
        else:
            counts[file.id] = cached
    if not stale_ids:
        return counts
    stale_rows = session.exec(
        select(File.id, File.content, File.file_metadata).where(col(File.id).in_(stale_ids))
    ).all()
    try:
        for file_id, content, raw_metadata in stale_rows:
            word_count = count_words(content)
            counts[file_id] = word_count
            try:
                parsed = json_module.loads(raw_metadata) if raw_metadata else {}
            except (TypeError, ValueError):
                parsed = {}
            metadata: dict[str, Any] = parsed if isinstance(parsed, dict) else {}
            metadata["word_count"] = word_count
            metadata["word_count_rev"] = WORD_COUNT_REV
            session.exec(
                update(File)
                .where(
                    col(File.id) == file_id,
                    col(File.content).is_(None) if content is None else col(File.content) == content,
                    col(File.file_metadata).is_(None)
                    if raw_metadata is None
                    else col(File.file_metadata) == raw_metadata,
                )
                .values(file_metadata=json_module.dumps(metadata))
                .execution_options(synchronize_session=False)
            )
        session.commit()
    except Exception as exc:  # pragma: no cover - infra dependent
        session.rollback()
        log_with_context(
            logger,
            30,  # WARNING
            "Failed to backfill prose word_count metadata (continuing)",
            error=str(exc),
            error_type=type(exc).__name__,
            **log_fields,
        )
    return counts


@dataclass(frozen=True)
class ProjectProgress:
    project_id: str
    written_units: int
    word_count: int
    framework_ready: bool


def get_projects_progress(
    session: Session, owner_id: str, project_id: str | None = None
) -> list[ProjectProgress]:
    """Progress of the owner's projects, or of one of them when ``project_id`` is given."""
    query = select(Project.id).where(Project.owner_id == owner_id, Project.is_deleted == False)  # noqa: E712
    if project_id is not None:
        query = query.where(Project.id == project_id)
    project_ids = list(session.exec(query).all())
    if not project_ids:
        return []

    prose_files = list(
        session.exec(
            select(File)
            .options(load_only(File.id, File.project_id, File.file_metadata))
            .where(
                col(File.project_id).in_(project_ids),
                col(File.file_type).in_(CONTENT_FILE_TYPES),
                File.is_deleted == False,  # noqa: E712
            )
        ).all()
    )
    project_of = {file.id: file.project_id for file in prose_files}
    units: dict[str, int] = {}
    words: dict[str, int] = {}
    for file_id, count in resolve_prose_word_counts(session, prose_files, owner_id=owner_id).items():
        if count > 0:
            owner_project = project_of[file_id]
            units[owner_project] = units.get(owner_project, 0) + 1
            words[owner_project] = words.get(owner_project, 0) + count

    planning_ready = set(
        session.exec(
            select(File.project_id)
            .where(
                col(File.project_id).in_(project_ids),
                col(File.file_type).in_(PLANNING_FILE_TYPES),
                File.is_deleted == False,  # noqa: E712
                func.length(func.trim(func.coalesce(File.content, ""))) > 0,
            )
            .distinct()
        ).all()
    )

    return [
        ProjectProgress(
            project_id=pid,
            written_units=units.get(pid, 0),
            word_count=words.get(pid, 0),
            framework_ready=units.get(pid, 0) == 0 and pid in planning_ready,
        )
        for pid in project_ids
    ]
