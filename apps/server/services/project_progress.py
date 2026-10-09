"""作品列表卡片上的写作进度：写了几章（集）、多少字，或者框架已就绪还没开写。

字数按编辑器口径，读 file_metadata 里当前口径的缓存；没有或旧口径的缓存才加载
content 重算并回写（见 models.file_model.cached_word_count）。只算正文类文件
（draft / script），内容为空的不算「已写」。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import load_only
from sqlmodel import Session, col, func, select

from models import File, Project
from models.file_model import cached_word_count, stamp_word_count
from services.project_next_step import PLANNING_FILE_TYPES, PROSE_FILE_TYPES
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


@dataclass(frozen=True)
class ProjectProgress:
    project_id: str
    written_units: int
    word_count: int
    framework_ready: bool


def get_projects_progress(session: Session, owner_id: str) -> list[ProjectProgress]:
    project_ids = list(
        session.exec(
            select(Project.id).where(Project.owner_id == owner_id, Project.is_deleted == False)  # noqa: E712
        ).all()
    )
    if not project_ids:
        return []

    prose_files = session.exec(
        select(File)
        .options(load_only(File.id, File.project_id, File.file_type, File.file_metadata))
        .where(
            col(File.project_id).in_(project_ids),
            col(File.file_type).in_(PROSE_FILE_TYPES),
            File.is_deleted == False,  # noqa: E712
        )
    ).all()

    words_by_file: dict[str, int] = {}
    stale_ids: list[str] = []
    for file in prose_files:
        cached = cached_word_count(file.file_metadata)
        if cached is None:
            stale_ids.append(file.id)
        else:
            words_by_file[file.id] = cached
    if stale_ids:
        for file in session.exec(select(File).where(col(File.id).in_(stale_ids))).all():
            words_by_file[file.id] = stamp_word_count(file)
            session.add(file)
        try:
            session.commit()
        except Exception as exc:  # pragma: no cover - infra dependent
            session.rollback()
            log_with_context(
                logger,
                30,  # WARNING
                "Failed to backfill prose word_count metadata (continuing)",
                owner_id=owner_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    units: dict[str, int] = {}
    words: dict[str, int] = {}
    for file in prose_files:
        count = words_by_file.get(file.id, 0)
        if count > 0:
            units[file.project_id] = units.get(file.project_id, 0) + 1
            words[file.project_id] = words.get(file.project_id, 0) + count

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
            project_id=project_id,
            written_units=units.get(project_id, 0),
            word_count=words.get(project_id, 0),
            framework_ready=units.get(project_id, 0) == 0 and project_id in planning_ready,
        )
        for project_id in project_ids
    ]
