"""
Admin Inspiration Management API endpoints.

This module contains all inspiration management endpoints for admin operations.
"""
import json
import logging
from datetime import datetime
from typing import cast

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.orm import InstrumentedAttribute, defer
from sqlmodel import Session, func, select

from config.datetime_utils import utcnow
from config.feature_flags import require_inspirations_enabled
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import File, Inspiration, Project, User
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from services.inspiration_service import (
    create_inspiration_from_project,
    review_inspiration,
)
from utils.logger import get_logger, log_with_context

from .schemas import (
    CreateInspirationRequest,
    InspirationReviewRequest,
    UpdateInspirationRequest,
)

logger = get_logger(__name__)

router = APIRouter(
    tags=["admin-inspirations"],
    dependencies=[Depends(require_inspirations_enabled)],
)


# ==================== Inspiration Management ====================


def _audit_snapshot(inspiration: Inspiration) -> dict:
    """Admin-editable inspiration fields for AdminAuditLog old/new values."""
    return {
        "name": inspiration.name,
        "source": inspiration.source,
        "status": inspiration.status,
        "is_featured": inspiration.is_featured,
        "sort_order": inspiration.sort_order,
        "author_id": inspiration.author_id,
    }


class AdminInspirationResponse(BaseModel):
    """Admin-facing inspiration payload with parsed tags."""

    id: str
    name: str
    description: str | None
    cover_image: str | None
    tags: list[str]
    source: str
    status: str
    is_featured: bool
    sort_order: int
    copy_count: int
    creator_id: str | None
    creator_name: str | None = None
    reviewer_id: str | None = None
    reviewer_name: str | None = None
    reviewed_at: datetime | None = None
    rejection_reason: str | None = None
    original_project_id: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminInspirationsListResponse(BaseModel):
    """Paginated inspiration list response for admin panel."""

    items: list[AdminInspirationResponse]
    total: int


def _parse_tags(raw_tags: str | None) -> list[str]:
    """Parse stored JSON tags safely."""
    if not raw_tags:
        return []
    try:
        parsed = json.loads(raw_tags)
    except (json.JSONDecodeError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _to_admin_response(session: Session, inspiration: Inspiration) -> AdminInspirationResponse:
    """Convert inspiration model to admin response."""
    creator = session.get(User, inspiration.author_id) if inspiration.author_id else None
    reviewer = session.get(User, inspiration.reviewed_by) if inspiration.reviewed_by else None

    return AdminInspirationResponse(
        id=inspiration.id,
        name=inspiration.name,
        description=inspiration.description,
        cover_image=inspiration.cover_image,
        tags=_parse_tags(inspiration.tags),
        source=inspiration.source,
        status=inspiration.status,
        is_featured=inspiration.is_featured,
        sort_order=inspiration.sort_order,
        copy_count=inspiration.copy_count,
        creator_id=inspiration.author_id,
        creator_name=creator.username if creator else None,
        reviewer_id=inspiration.reviewed_by,
        reviewer_name=reviewer.username if reviewer else None,
        reviewed_at=inspiration.reviewed_at,
        rejection_reason=inspiration.rejection_reason,
        original_project_id=inspiration.original_project_id,
        created_at=inspiration.created_at,
        updated_at=inspiration.updated_at,
    )


def _build_user_name_lookup(
    session: Session,
    inspirations: list[Inspiration],
) -> dict[str, str]:
    """Batch-load creator/reviewer names for inspiration list rendering."""
    user_ids: set[str] = set()
    for inspiration in inspirations:
        if inspiration.author_id:
            user_ids.add(inspiration.author_id)
        if inspiration.reviewed_by:
            user_ids.add(inspiration.reviewed_by)

    if not user_ids:
        return {}

    users = session.exec(select(User).where(User.id.in_(user_ids))).all()
    return {user.id: user.username for user in users}


def _to_admin_response_with_lookup(
    inspiration: Inspiration,
    user_name_lookup: dict[str, str],
) -> AdminInspirationResponse:
    """Convert inspiration model to admin response using preloaded user names."""
    return AdminInspirationResponse(
        id=inspiration.id,
        name=inspiration.name,
        description=inspiration.description,
        cover_image=inspiration.cover_image,
        tags=_parse_tags(inspiration.tags),
        source=inspiration.source,
        status=inspiration.status,
        is_featured=inspiration.is_featured,
        sort_order=inspiration.sort_order,
        copy_count=inspiration.copy_count,
        creator_id=inspiration.author_id,
        creator_name=user_name_lookup.get(inspiration.author_id) if inspiration.author_id else None,
        reviewer_id=inspiration.reviewed_by,
        reviewer_name=user_name_lookup.get(inspiration.reviewed_by) if inspiration.reviewed_by else None,
        reviewed_at=inspiration.reviewed_at,
        rejection_reason=inspiration.rejection_reason,
        original_project_id=inspiration.original_project_id,
        created_at=inspiration.created_at,
        updated_at=inspiration.updated_at,
    )


@router.get("/inspirations", response_model=AdminInspirationsListResponse)
def list_inspirations_admin(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=100, description="Number of records to return"),
    status: str | None = Query(None, description="Filter by status"),
    status_filter: str | None = Query(None, include_in_schema=False),
    source: str | None = Query(None, description="Filter by source"),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    List all inspirations (admin view, includes all statuses).

    Requires superuser privileges.
    """
    effective_status = status or status_filter
    query = select(Inspiration).options(
        defer(cast(InstrumentedAttribute[str], Inspiration.snapshot_data))
    )

    if effective_status:
        query = query.where(Inspiration.status == effective_status)

    if source:
        query = query.where(Inspiration.source == source)

    count_query = select(func.count()).select_from(query.subquery())
    total = session.exec(count_query).one() or 0

    query = query.order_by(Inspiration.created_at.desc()).offset(skip).limit(limit)

    inspirations = session.exec(query).all()
    user_name_lookup = _build_user_name_lookup(session, list(inspirations))

    log_with_context(
        logger,
        logging.INFO,
        "Admin listed inspirations",
        user_id=current_user.id,
        count=len(inspirations),
        status_filter=effective_status,
        source=source,
        total=total,
    )

    return AdminInspirationsListResponse(
        items=[
            _to_admin_response_with_lookup(inspiration, user_name_lookup)
            for inspiration in inspirations
        ],
        total=total,
    )


@router.get("/inspirations/{inspiration_id}", response_model=AdminInspirationResponse)
def get_inspiration_admin(
    inspiration_id: str,
    _current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get a specific inspiration by ID (admin view).

    Requires superuser privileges.
    """
    inspiration = session.get(Inspiration, inspiration_id)
    if not inspiration:
        raise APIException(
            error_code=ErrorCode.INSPIRATION_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    return _to_admin_response(session, inspiration)


@router.post("/inspirations", response_model=AdminInspirationResponse, status_code=status.HTTP_201_CREATED)
def create_inspiration(
    request: CreateInspirationRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Create an inspiration from an existing project.

    Requires superuser privileges.
    """
    # Get the project
    project = session.get(Project, request.project_id)
    if not project:
        raise APIException(
            error_code=ErrorCode.PROJECT_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found",
        )

    # Get all files for the project
    files = session.exec(
        select(File).where(
            File.project_id == request.project_id,
            File.is_deleted.is_(False),
        )
    ).all()

    if not files:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project has no files to include in inspiration",
        )

    # Create inspiration
    inspiration = create_inspiration_from_project(
        session=session,
        project=project,
        files=list(files),
        source=request.source,
        author=current_user,
        name=request.name,
        description=request.description,
        cover_image=request.cover_image,
        tags=request.tags,
        is_featured=request.is_featured,
    )

    # Official inspirations are auto-approved
    if request.source == "official":
        inspiration.status = "approved"

    session.add(inspiration)
    # Publishing copies another user's project into a public template, so
    # record whose project it was.
    admin_audit_service.log_action(
        session, current_user.id, "create_inspiration", "inspiration", inspiration.id,
        new_value={
            **_audit_snapshot(inspiration),
            "project_id": project.id,
            "project_owner_id": project.owner_id,
            "file_count": len(files),
        },
        request=http_request, commit=False,
    )
    session.commit()
    session.refresh(inspiration)

    log_with_context(
        logger,
        logging.INFO,
        "Created inspiration",
        user_id=current_user.id,
        inspiration_id=inspiration.id,
        inspiration_name=inspiration.name,
        source=request.source,
    )

    return _to_admin_response(session, inspiration)


@router.patch("/inspirations/{inspiration_id}", response_model=AdminInspirationResponse)
def update_inspiration(
    inspiration_id: str,
    request: UpdateInspirationRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Update an inspiration.

    Requires superuser privileges.
    """
    inspiration = session.get(Inspiration, inspiration_id)
    if not inspiration:
        raise APIException(
            error_code=ErrorCode.INSPIRATION_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    old_value = _audit_snapshot(inspiration)

    # Update fields
    if request.name is not None:
        inspiration.name = request.name
    if request.description is not None:
        inspiration.description = request.description
    if request.cover_image is not None:
        inspiration.cover_image = request.cover_image
    if request.tags is not None:
        inspiration.tags = json.dumps(request.tags, ensure_ascii=False)
    if request.is_featured is not None:
        inspiration.is_featured = request.is_featured
    if request.sort_order is not None:
        inspiration.sort_order = request.sort_order
    if request.status is not None:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Status must be updated via /inspirations/{id}/review endpoint",
        )

    inspiration.updated_at = utcnow()

    session.add(inspiration)
    admin_audit_service.log_action(
        session, current_user.id, "update_inspiration", "inspiration", inspiration_id,
        old_value=old_value, new_value=_audit_snapshot(inspiration),
        request=http_request, commit=False,
    )
    session.commit()
    session.refresh(inspiration)

    log_with_context(
        logger,
        logging.INFO,
        "Updated inspiration",
        user_id=current_user.id,
        inspiration_id=inspiration_id,
    )

    return _to_admin_response(session, inspiration)


@router.post("/inspirations/{inspiration_id}/review")
def review_inspiration_endpoint(
    inspiration_id: str,
    request: InspirationReviewRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Review a pending inspiration (approve or reject).

    Requires superuser privileges.
    """
    inspiration = session.get(Inspiration, inspiration_id)
    if not inspiration:
        raise APIException(
            error_code=ErrorCode.INSPIRATION_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    if inspiration.status != "pending":
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inspiration is not pending review",
        )

    old_value = _audit_snapshot(inspiration)
    try:
        review_inspiration(
            session=session,
            inspiration=inspiration,
            reviewer=current_user,
            approve=request.approve,
            rejection_reason=request.rejection_reason,
            commit=False,
        )
    except ValueError as e:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    admin_audit_service.log_action(
        session, current_user.id,
        "approve_inspiration" if request.approve else "reject_inspiration",
        "inspiration", inspiration_id,
        old_value=old_value,
        new_value={**_audit_snapshot(inspiration), "rejection_reason": inspiration.rejection_reason},
        request=http_request,
        commit=False,
    )
    session.commit()

    log_with_context(
        logger,
        logging.INFO,
        f"{'Approved' if request.approve else 'Rejected'} inspiration",
        user_id=current_user.id,
        inspiration_id=inspiration_id,
        inspiration_name=inspiration.name,
    )

    return {
        "message": f"Inspiration {'approved' if request.approve else 'rejected'}",
        "inspiration_id": inspiration_id,
    }


@router.delete("/inspirations/{inspiration_id}")
def delete_inspiration(
    inspiration_id: str,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Delete an inspiration.

    Requires superuser privileges.
    """
    inspiration = session.get(Inspiration, inspiration_id)
    if not inspiration:
        raise APIException(
            error_code=ErrorCode.INSPIRATION_NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    admin_audit_service.log_action(
        session, current_user.id, "delete_inspiration", "inspiration", inspiration_id,
        old_value={**_audit_snapshot(inspiration), "original_project_id": inspiration.original_project_id},
        request=http_request, commit=False,
    )
    session.delete(inspiration)
    session.commit()

    log_with_context(
        logger,
        logging.INFO,
        "Deleted inspiration",
        user_id=current_user.id,
        inspiration_id=inspiration_id,
        inspiration_name=inspiration.name,
    )

    return {"message": "Inspiration deleted", "inspiration_id": inspiration_id}
