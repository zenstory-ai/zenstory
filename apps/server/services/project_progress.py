"""作品的写作进度：写了几章（集）、多少字，以及框架已就绪但还没开写。

作品列表卡片和聊天输入框上方的「写第一章」按钮共用这一个判断。字数按编辑器
口径（见 writing_stats_service.resolve_prose_word_counts），只算正文类文件，
内容为空的不算「已写」。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import load_only
from sqlmodel import Session, col, func, select

from agent.constants import CONTENT_FILE_TYPES
from models import File, Project
from services.features.writing_stats_service import resolve_prose_word_counts

PLANNING_FILE_TYPES = ("outline", "character", "lore")


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
