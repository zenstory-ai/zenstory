"""
Material library management API endpoints.

Handles library list, detail, tree, and delete operations:
- List all user's materials
- Get material detail with entity counts
- Get chapter tree structure
- Soft delete materials
"""
import contextlib
import json
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header
from fastapi.responses import Response
from services.auth import get_current_active_user
from sqlalchemy import select as sa_select
from sqlmodel import Session, col, func, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from models.material_models import (
    Chapter,
    Character,
    CharacterRelationship,
    GoldenFinger,
    IngestionJob,
    Novel,
    Plot,
    Story,
    StoryLine,
    WorldView,
)
from services.material.export_service import (
    build_material_export_filename,
    export_material_breakdown_to_txt,
)
from services.material.ingestion_jobs_service import IngestionJobsService
from services.material.job_errors import public_job_error
from services.material.stories_service import story_in_novel
from utils.logger import get_logger

from .helpers import _get_novel_or_404, _resolve_lang
from .schemas import (
    MaterialDetailResponse,
    MaterialListItem,
)

logger = get_logger(__name__)

# Router without prefix/tags - will be set by parent router
router = APIRouter()


def _reconcile_job_if_needed(session: Session, job: IngestionJob | None) -> IngestionJob | None:
    if not job:
        return None
    return IngestionJobsService().reconcile_stale_job(session, job)


# ==================== List Endpoints ====================

@router.get("/list", response_model=list[MaterialListItem])
def get_materials(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get user's material library list.

    Returns all novels uploaded by the current user with their latest job status.
    """
    # Single query to get all novels with their latest job status
    novels_with_jobs = session.exec(
        select(Novel, IngestionJob)
        .outerjoin(IngestionJob, IngestionJob.novel_id == Novel.id)
        .where(Novel.user_id == current_user.id)
        .where(Novel.deleted_at.is_(None))
        .order_by(Novel.created_at.desc(), IngestionJob.created_at.desc())
    ).all()

    # Group by novel and get latest job per novel
    novel_map: dict[int, tuple[Novel, IngestionJob | None]] = {}
    for novel, job in novels_with_jobs:
        if novel.id not in novel_map:
            novel_map[novel.id] = (novel, _reconcile_job_if_needed(session, job))
        elif job is not None and job.status == "failed":
            _reconcile_job_if_needed(session, job)

    novel_ids = list(novel_map)
    # Batch query for chapter counts
    chapter_counts_dict = {}

    if novel_ids:
        chapter_counts = session.exec(
            select(Chapter.novel_id, func.count(Chapter.id))
            .where(Chapter.novel_id.in_(novel_ids))
            .group_by(Chapter.novel_id)
        ).all()
        chapter_counts_dict = dict(chapter_counts)

    # Build result
    result = []
    for novel_id in novel_ids:
        novel, job = novel_map[novel_id]
        chapters_count = chapter_counts_dict.get(novel_id, 0)
        original_filename = None
        trial_chapter_limit = None
        source_chapter_count = None

        if novel.source_meta:
            with contextlib.suppress(Exception):
                parsed_source_meta = json.loads(novel.source_meta)
                if isinstance(parsed_source_meta, dict):
                    original_filename = parsed_source_meta.get("original_filename")
                    trial_chapter_limit = _positive_int(
                        parsed_source_meta.get("trial_chapter_limit")
                    )
                    source_chapter_count = _positive_int(
                        parsed_source_meta.get("source_chapter_count")
                    )

        logger.info(f"Novel {novel.id} ({novel.title}): chapters_count={chapters_count}")

        result.append(MaterialListItem(
            id=novel.id,
            title=novel.title,
            author=novel.author,
            synopsis=novel.synopsis,
            original_filename=original_filename,
            created_at=novel.created_at,
            updated_at=novel.updated_at,
            status=job.status if job else None,
            error_message=public_job_error(job.error_message) if job else None,
            chapters_count=chapters_count,
            enabled_stages=IngestionJobsService.get_enabled_stages(job),
            trial_chapter_limit=trial_chapter_limit,
            source_chapter_count=source_chapter_count,
        ))

    return result


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


# ==================== Detail Endpoints ====================

@router.get("/{novel_id}", response_model=MaterialDetailResponse)
def get_material_detail(
    novel_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get material library detail.

    Returns detailed information about a specific novel including counts of all entities.
    """
    # Get novel with user isolation and soft delete check
    novel = _get_novel_or_404(session, novel_id, current_user.id)

    # Count related entities in one round trip.
    detail_row = session.exec(
        select(
            select(func.count(Chapter.id))
            .where(Chapter.novel_id == novel_id)
            .scalar_subquery()
            .label("chapters_count"),
            select(func.count(Character.id))
            .where(Character.novel_id == novel_id)
            .scalar_subquery()
            .label("characters_count"),
            select(func.count(StoryLine.id))
            .where(StoryLine.novel_id == novel_id)
            .scalar_subquery()
            .label("story_lines_count"),
            select(func.count(Plot.id))
            .where(Plot.chapter_id.in_(select(Chapter.id).where(Chapter.novel_id == novel_id)))
            .scalar_subquery()
            .label("plots_count"),
            select(func.count(Story.id))
            .where(story_in_novel(novel_id))
            .scalar_subquery()
            .label("stories_count"),
            select(func.count(CharacterRelationship.id))
            .where(CharacterRelationship.novel_id == novel_id)
            .scalar_subquery()
            .label("relationships_count"),
            select(func.count(GoldenFinger.id))
            .where(GoldenFinger.novel_id == novel_id)
            .scalar_subquery()
            .label("golden_fingers_count"),
            select(func.count(WorldView.id))
            .where(WorldView.novel_id == novel_id)
            .scalar_subquery()
            .label("worldview_count"),
        )
    ).one()

    # Get latest job status
    latest_job = session.exec(
        select(IngestionJob)
        .where(IngestionJob.novel_id == novel_id)
        .order_by(IngestionJob.created_at.desc())
    ).first()
    latest_job = _reconcile_job_if_needed(session, latest_job)
    job_status = latest_job.status if latest_job else None

    # Parse source_meta
    source_meta = None
    if novel.source_meta:
        with contextlib.suppress(Exception):
            source_meta = json.loads(novel.source_meta)

    return MaterialDetailResponse(
        id=novel.id,
        title=novel.title,
        author=novel.author,
        synopsis=novel.synopsis,
        source_meta=source_meta,
        status=job_status,
        error_message=public_job_error(latest_job.error_message) if latest_job else None,
        created_at=novel.created_at,
        updated_at=novel.updated_at,
        chapters_count=int(detail_row.chapters_count or 0),
        characters_count=int(detail_row.characters_count or 0),
        story_lines_count=int(detail_row.story_lines_count or 0),
        plots_count=int(detail_row.plots_count or 0),
        stories_count=int(detail_row.stories_count or 0),
        relationships_count=int(detail_row.relationships_count or 0),
        golden_fingers_count=int(detail_row.golden_fingers_count or 0),
        has_world_view=int(detail_row.worldview_count or 0) > 0,
        enabled_stages=IngestionJobsService.get_enabled_stages(latest_job),
    )


# ==================== Tree Endpoints ====================

@router.get("/{novel_id}/tree")
def get_material_tree(
    novel_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get file tree structure for material library.

    Returns a hierarchical structure of chapters organized by their order.
    """
    # Verify novel ownership and soft delete check
    _get_novel_or_404(session, novel_id, current_user.id)

    # Get all chapters ordered by chapter_number
    chapters = session.execute(
        sa_select(col(Chapter.id), col(Chapter.chapter_number), col(Chapter.title), col(Chapter.summary), col(Chapter.created_at))
        .where(col(Chapter.novel_id) == novel_id)
        .order_by(Chapter.chapter_number.asc())
    ).all()

    # Batch query for plot counts
    chapter_ids = [c.id for c in chapters]
    plot_counts_dict = {}

    if chapter_ids:
        plot_counts = session.exec(
            select(Plot.chapter_id, func.count(Plot.id))
            .where(Plot.chapter_id.in_(chapter_ids))
            .group_by(Plot.chapter_id)
        ).all()
        plot_counts_dict = dict(plot_counts)

    # Build tree structure (format: { tree: MaterialTreeNode[] })
    tree_items = []
    for chapter in chapters:
        tree_items.append({
            "id": chapter.id,
            "type": "chapter",
            "title": chapter.title,
            "metadata": {
                "chapter_number": chapter.chapter_number,
                "summary": chapter.summary,
                "plots_count": plot_counts_dict.get(chapter.id, 0),
                "created_at": chapter.created_at.isoformat() if chapter.created_at else None,
            },
        })

    return {
        "tree": tree_items,
    }


# ==================== Export Endpoints ====================

@router.get("/{novel_id}/export")
def export_material_breakdown(
    novel_id: int,
    accept_language: Annotated[str | None, Header()] = None,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Export all extracted breakdown content for a material as a UTF-8 TXT download."""
    novel = _get_novel_or_404(session, novel_id, current_user.id)
    lang = _resolve_lang(accept_language)
    content = export_material_breakdown_to_txt(session, novel, lang=lang)
    if not content:
        raise APIException(error_code=ErrorCode.MATERIAL_EXPORT_EMPTY, status_code=404)

    filename = build_material_export_filename(novel.title, lang=lang)
    return Response(
        content="\ufeff" + content,
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


# ==================== Delete Endpoints ====================

@router.delete("/{novel_id}")
def delete_material(
    novel_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Delete material library (soft delete).

    Marks the novel as deleted without removing data from database.
    """
    # Verify novel ownership and check soft delete status
    novel = _get_novel_or_404(session, novel_id, current_user.id)

    # Soft delete: set deleted_at timestamp
    novel.deleted_at = utcnow()
    session.add(novel)
    session.commit()

    logger.info(f"Material library soft deleted: novel_id={novel_id}, user_id={current_user.id}")

    return {"message": "Material library deleted successfully"}


__all__ = ["router"]
