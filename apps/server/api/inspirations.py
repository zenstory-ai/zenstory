"""
Public Inspirations API endpoints.

Provides FastAPI router for inspiration discovery and copying:
- POST /inspirations - Submit user's project as inspiration (requires review)
- GET /inspirations - List inspirations (with filtering and pagination)
- GET /inspirations/{id} - Get inspiration details
- GET /inspirations/featured - Get featured inspirations
- POST /inspirations/{id}/copy - Copy inspiration to user's workspace
"""

import json
import logging
from datetime import datetime
from enum import StrEnum
from typing import cast

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from services.auth import get_current_active_user
from sqlalchemy.orm import InstrumentedAttribute, defer
from sqlmodel import Session, func, select

from config.datetime_utils import utcnow
from config.feature_flags import require_inspirations_enabled
from core.error_codes import ErrorCode
from core.error_handler import APIException
from core.permissions import QuotaExceededException
from core.project_access import verify_project_ownership
from database import get_session
from models import File, Inspiration, User
from services.inspiration_service import (
    copy_inspiration_to_project,
    create_inspiration_from_project,
    get_featured_inspirations,
    get_inspiration_detail,
    list_inspirations,
)
from services.quota_service import quota_service
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(
    prefix="/api/v1/inspirations",
    tags=["Inspirations"],
    dependencies=[Depends(require_inspirations_enabled)],
)


# ==================== Enums ====================


class InspirationProjectType(StrEnum):
    """Valid project types for inspirations."""

    novel = "novel"
    short = "short"
    screenplay = "screenplay"


# ==================== Response Models ====================


class InspirationResponse(BaseModel):
    """Response model for an inspiration."""

    id: str
    name: str
    description: str | None
    cover_image: str | None
    project_type: str
    tags: list[str]
    source: str
    author_id: str | None
    original_project_id: str | None
    copy_count: int
    is_featured: bool
    created_at: datetime


class InspirationDetailResponse(BaseModel):
    """Response model for inspiration detail with file preview."""

    id: str
    name: str
    description: str | None
    cover_image: str | None
    project_type: str
    tags: list[str]
    source: str
    author_id: str | None
    original_project_id: str | None
    copy_count: int
    is_featured: bool
    created_at: datetime
    file_preview: list[dict]  # List of files with basic info


class InspirationListResponse(BaseModel):
    """Response model for inspiration list."""

    inspirations: list[InspirationResponse]
    total: int
    page: int
    page_size: int


class CopyInspirationRequest(BaseModel):
    """Request model for copying an inspiration."""

    project_name: str | None = None


class CopyInspirationResponse(BaseModel):
    """Response model for copying an inspiration."""

    success: bool
    message: str
    project_id: str | None = None
    project_name: str | None = None


class SubmitInspirationRequest(BaseModel):
    """Request model for submitting a project as inspiration."""

    project_id: str
    name: str | None = None
    description: str | None = None
    cover_image: str | None = None
    tags: list[str] | None = None


class SubmitInspirationResponse(BaseModel):
    """Response model for inspiration submission."""

    success: bool
    message: str
    inspiration_id: str
    status: str


class MyInspirationSubmissionResponse(BaseModel):
    """Current user's submitted inspiration item."""

    id: str
    name: str
    description: str | None
    tags: list[str]
    status: str
    copy_count: int
    rejection_reason: str | None
    created_at: datetime
    reviewed_at: datetime | None


class MyInspirationSubmissionsResponse(BaseModel):
    """Paginated current-user submissions response."""

    items: list[MyInspirationSubmissionResponse]
    total: int
    page: int
    page_size: int


# ==================== Helper Functions ====================


def _inspiration_to_response(inspiration: Inspiration) -> InspirationResponse:
    """Convert Inspiration model to response."""
    tags = json.loads(inspiration.tags) if inspiration.tags else []
    return InspirationResponse(
        id=inspiration.id,
        name=inspiration.name,
        description=inspiration.description,
        cover_image=inspiration.cover_image,
        project_type=inspiration.project_type,
        tags=tags,
        source=inspiration.source,
        # Public endpoints should not expose internal user/project identifiers.
        author_id=None,
        original_project_id=None,
        copy_count=inspiration.copy_count,
        is_featured=inspiration.is_featured,
        created_at=inspiration.created_at,
    )


def _get_file_preview(inspiration: Inspiration) -> list[dict]:
    """Extract file preview from inspiration snapshot."""
    try:
        snapshot = json.loads(inspiration.snapshot_data)
        files = snapshot.get("files", [])
        # Return basic file info for preview
        return [
            {
                "title": f.get("title", "Untitled"),
                "file_type": f.get("file_type", "document"),
                "has_content": bool(f.get("content")),
            }
            for f in files[:10]  # Limit to first 10 files
        ]
    except (json.JSONDecodeError, TypeError):
        return []


def _parse_tags(raw_tags: str | None) -> list[str]:
    """Parse inspiration tags safely."""
    if not raw_tags:
        return []
    try:
        tags = json.loads(raw_tags)
    except (json.JSONDecodeError, TypeError):
        return []
    return tags if isinstance(tags, list) else []


# ==================== API Endpoints ====================


@router.post("", response_model=SubmitInspirationResponse, status_code=201)
def submit_inspiration(
    request: SubmitInspirationRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Submit user's project to inspiration library.

    - Regular users: submitted as community inspiration with `pending` status.
    - Superusers: auto-approved to satisfy admin no-review flow.
    """
    project = verify_project_ownership(request.project_id, current_user, session)

    files = session.exec(
        select(File).where(
            File.project_id == project.id,
            File.is_deleted.is_(False),
        )
    ).all()

    if not files:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail="Project has no files to include in inspiration",
        )

    inspiration = create_inspiration_from_project(
        session=session,
        project=project,
        files=list(files),
        source="community",
        author=current_user,
        name=request.name,
        description=request.description,
        cover_image=request.cover_image,
        tags=request.tags,
        is_featured=False,
    )

    # Admin users can publish directly without extra review step.
    if current_user.is_superuser:
        inspiration.status = "approved"
        inspiration.reviewed_by = current_user.id
        inspiration.reviewed_at = utcnow()

    session.add(inspiration)
    session.commit()
    session.refresh(inspiration)

    message = (
        "Inspiration published successfully"
        if inspiration.status == "approved"
        else "Inspiration submitted for review"
    )

    log_with_context(
        logger,
        logging.INFO,
        "User submitted inspiration",
        user_id=current_user.id,
        inspiration_id=inspiration.id,
        inspiration_status=inspiration.status,
        project_id=project.id,
    )

    return SubmitInspirationResponse(
        success=True,
        message=message,
        inspiration_id=inspiration.id,
        status=inspiration.status,
    )


@router.get("", response_model=InspirationListResponse)
def list_all_inspirations(
    project_type: InspirationProjectType | None = Query(None, description="Filter by project type"),
    search: str | None = Query(None, description="Search in name/description"),
    tags: str | None = Query(None, description="Comma-separated tags"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(12, ge=1, le=50, description="Results per page"),
    featured_only: bool = Query(False, description="Only show featured"),
    session: Session = Depends(get_session),
):
    """
    List all approved inspirations with filtering and pagination.

    Supports:
    - Filter by project_type (novel/short/screenplay)
    - Search in name and description
    - Filter by tags
    - Pagination
    - Featured only mode
    """
    # Parse tags
    tag_list = None
    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]

    inspirations, total = list_inspirations(
        session=session,
        project_type=project_type,
        search=search,
        tags=tag_list,
        page=page,
        page_size=page_size,
        featured_only=featured_only,
        metadata_only=True,
    )

    return InspirationListResponse(
        inspirations=[_inspiration_to_response(i) for i in inspirations],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/featured", response_model=list[InspirationResponse])
def get_featured(
    limit: int = Query(6, ge=1, le=20, description="Maximum results"),
    session: Session = Depends(get_session),
):
    """
    Get featured inspirations for homepage.

    Returns top featured inspirations ordered by sort_order and copy_count.
    """
    inspirations = get_featured_inspirations(session=session, limit=limit, metadata_only=True)
    return [_inspiration_to_response(i) for i in inspirations]


@router.get("/my-submissions", response_model=MyInspirationSubmissionsResponse)
def get_my_submissions(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(10, ge=1, le=50, description="Results per page"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get inspirations submitted by current user.

    Includes pending/approved/rejected records so users can track review progress.
    """
    base_query = (
        select(Inspiration)
        .options(defer(cast(InstrumentedAttribute[str], Inspiration.snapshot_data)))
        .where(Inspiration.author_id == current_user.id)
        .order_by(Inspiration.created_at.desc())
    )

    count_stmt = select(func.count()).select_from(base_query.subquery())
    total = session.exec(count_stmt).one() or 0

    offset = (page - 1) * page_size
    submissions = session.exec(base_query.offset(offset).limit(page_size)).all()

    items = [
        MyInspirationSubmissionResponse(
            id=item.id,
            name=item.name,
            description=item.description,
            tags=_parse_tags(item.tags),
            status=item.status,
            copy_count=item.copy_count,
            rejection_reason=item.rejection_reason,
            created_at=item.created_at,
            reviewed_at=item.reviewed_at,
        )
        for item in submissions
    ]

    return MyInspirationSubmissionsResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{inspiration_id}", response_model=InspirationDetailResponse)
def get_inspiration(
    inspiration_id: str,
    session: Session = Depends(get_session),
):
    """
    Get detailed information about a specific inspiration.

    Includes file preview showing the structure of the template.
    """
    inspiration = get_inspiration_detail(session=session, inspiration_id=inspiration_id)

    if not inspiration:
        raise APIException(
            error_code=ErrorCode.INSPIRATION_NOT_FOUND,
            status_code=404,
        )

    tags = json.loads(inspiration.tags) if inspiration.tags else []
    file_preview = _get_file_preview(inspiration)

    return InspirationDetailResponse(
        id=inspiration.id,
        name=inspiration.name,
        description=inspiration.description,
        cover_image=inspiration.cover_image,
        project_type=inspiration.project_type,
        tags=tags,
        source=inspiration.source,
        author_id=None,
        original_project_id=None,
        copy_count=inspiration.copy_count,
        is_featured=inspiration.is_featured,
        created_at=inspiration.created_at,
        file_preview=file_preview,
    )


@router.post("/{inspiration_id}/copy", response_model=CopyInspirationResponse)
def copy_inspiration(
    inspiration_id: str,
    request: CopyInspirationRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Copy an inspiration to the user's workspace.

    Creates a new project with all files from the inspiration template.
    Optionally accepts a custom project name.
    """
    log_with_context(
        logger,
        logging.INFO,
        "User copying inspiration",
        user_id=current_user.id,
        inspiration_id=inspiration_id,
        custom_name=request.project_name,
    )

    # Get inspiration
    inspiration = get_inspiration_detail(session=session, inspiration_id=inspiration_id)

    if not inspiration:
        log_with_context(
            logger,
            logging.WARNING,
            "Attempted to copy non-existent inspiration",
            user_id=current_user.id,
            inspiration_id=inspiration_id,
        )
        raise APIException(
            error_code=ErrorCode.INSPIRATION_NOT_FOUND,
            status_code=404,
        )

    # Advisory checks may normalize/create monthly quota before taking the
    # project creation gate; no commit-capable helper runs inside that gate.
    plan = quota_service.get_user_plan(session, current_user.id)
    should_consume_quota = not (plan and plan.name == "pro")
    if should_consume_quota:
        allowed, used, limit = quota_service.check_feature_quota(
            session,
            current_user.id,
            "inspiration_copy",
        )
        if not allowed:
            raise QuotaExceededException(
                feature_type="inspiration_copy",
                used=used,
                limit=limit,
            )

    try:
        project_allowed, existing_count, max_projects = quota_service.check_project_limit(
            session, current_user.id, for_creation=True
        )
        if not project_allowed:
            raise APIException(
                error_code=ErrorCode.QUOTA_PROJECTS_EXCEEDED,
                status_code=402,
                detail=f"Project limit reached ({existing_count}/{max_projects}). Please upgrade your plan.",
            )

        plan = quota_service.get_user_plan(session, current_user.id, commit=False)
        should_consume_quota = not (plan and plan.name == "pro")
        # A previous copy may have committed while this owner gate waited.
        session.refresh(inspiration)
        # Copy inspiration to user's workspace
        new_project = copy_inspiration_to_project(
            session=session,
            inspiration=inspiration,
            user=current_user,
            project_name=request.project_name,
            commit=False,
        )

        if should_consume_quota:
            reserved_period = quota_service.reserve_feature_quota(
                session,
                current_user.id,
                "inspiration_copy",
                commit=False,
            )
            if reserved_period is None:
                # Roll back copied project/files and report latest quota usage.
                session.rollback()
                _, latest_used, latest_limit = quota_service.check_feature_quota(
                    session,
                    current_user.id,
                    "inspiration_copy",
                )
                raise QuotaExceededException(
                    feature_type="inspiration_copy",
                    used=latest_used,
                    limit=latest_limit,
                )
        session.commit()

        session.refresh(new_project)

        log_with_context(
            logger,
            logging.INFO,
            "Inspiration copied successfully",
            user_id=current_user.id,
            inspiration_id=inspiration_id,
            project_id=new_project.id,
            project_name=new_project.name,
        )

        return CopyInspirationResponse(
            success=True,
            message="Inspiration copied successfully",
            project_id=new_project.id,
            project_name=new_project.name,
        )

    except APIException:
        session.rollback()
        raise
    except ValueError as e:
        session.rollback()
        log_with_context(
            logger,
            logging.ERROR,
            "Failed to copy inspiration",
            user_id=current_user.id,
            inspiration_id=inspiration_id,
            error=str(e),
        )
        raise APIException(
            error_code=ErrorCode.INSPIRATION_COPY_FAILED,
            status_code=400,
        ) from e
    except Exception:
        session.rollback()
        raise
