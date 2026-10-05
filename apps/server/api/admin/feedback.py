"""
Admin feedback management API endpoints.

Provides listing, status updates, and screenshot retrieval for user feedback.
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse
from sqlmodel import Session, or_, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User, UserFeedback
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from utils.logger import get_logger, log_with_context

from .schemas import FeedbackStatusUpdateRequest

logger = get_logger(__name__)

router = APIRouter(tags=["admin-feedback"])

ALLOWED_FEEDBACK_STATUS = {"open", "processing", "resolved"}
ALLOWED_SOURCE_PAGES = {"dashboard", "editor"}
DEFAULT_FEEDBACK_UPLOAD_DIR = "uploads/feedback"


def _resolve_feedback_upload_root() -> Path:
    configured_dir = os.getenv("FEEDBACK_UPLOAD_DIR", DEFAULT_FEEDBACK_UPLOAD_DIR).strip() or DEFAULT_FEEDBACK_UPLOAD_DIR
    upload_dir = Path(configured_dir)
    if not upload_dir.is_absolute():
        upload_dir = Path.cwd() / upload_dir
    return upload_dir.resolve()


def _is_within_upload_root(path: Path, upload_root: Path) -> bool:
    try:
        return os.path.commonpath([str(upload_root), str(path)]) == str(upload_root)
    except ValueError:
        return False


def _resolve_existing_feedback_screenshot(raw_path: str | None) -> tuple[Path | None, bool]:
    """
    Resolve an existing screenshot file path with backward-compatible fallbacks.

    Returns:
    - resolved file path if found and allowed
    - bool flag indicating whether any candidate path was outside upload root
    """
    if not raw_path:
        return None, False

    upload_root = _resolve_feedback_upload_root()
    original = Path(raw_path).expanduser()
    candidates: list[Path] = []
    seen: set[Path] = set()

    def add_candidate(path: Path) -> None:
        if path in seen:
            return
        seen.add(path)
        candidates.append(path)

    add_candidate(original)
    add_candidate(upload_root / original)
    add_candidate(upload_root / original.name)

    # Legacy absolute path fallback:
    # if old deployments wrote absolute paths under a different runtime root,
    # preserve the file name under current upload root.
    for marker in ("feedback", "uploads"):
        if marker in original.parts:
            marker_index = original.parts.index(marker)
            trailing_parts = original.parts[marker_index + 1 :]
            if trailing_parts:
                add_candidate(upload_root.joinpath(*trailing_parts))

    found_outside_root = False
    for candidate in candidates:
        resolved = candidate.resolve()

        if not _is_within_upload_root(resolved, upload_root):
            found_outside_root = True
            continue

        if resolved.exists() and resolved.is_file():
            return resolved, found_outside_root

    return None, found_outside_root


def _to_admin_feedback_item(feedback: UserFeedback, user: User) -> dict:
    resolved_screenshot_path, _ = _resolve_existing_feedback_screenshot(feedback.screenshot_path)
    has_screenshot = resolved_screenshot_path is not None

    return {
        "id": feedback.id,
        "user_id": feedback.user_id,
        "username": user.username,
        "email": user.email,
        "source_page": feedback.source_page,
        "source_route": feedback.source_route,
        "issue_text": feedback.issue_text,
        "trace_id": feedback.trace_id,
        "request_id": feedback.request_id,
        "agent_run_id": feedback.agent_run_id,
        "project_id": feedback.project_id,
        "agent_session_id": feedback.agent_session_id,
        "has_screenshot": has_screenshot,
        "screenshot_original_name": feedback.screenshot_original_name,
        "screenshot_content_type": feedback.screenshot_content_type,
        "screenshot_size_bytes": feedback.screenshot_size_bytes,
        "screenshot_download_url": f"/api/admin/feedback/{feedback.id}/screenshot" if has_screenshot else None,
        "status": feedback.status,
        "created_at": feedback.created_at,
        "updated_at": feedback.updated_at,
    }


@router.get("/feedback")
def list_feedback_admin(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=100, description="Number of records to return"),
    status_filter: str | None = Query(None, alias="status", description="Filter by status"),
    source_page: str | None = Query(None, description="Filter by source page"),
    has_screenshot: bool | None = Query(None, description="Filter by screenshot presence"),
    search: str | None = Query(None, description="Search by username/email/issue text"),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    List feedback submissions for admin review.

    Requires superuser privileges.
    """
    normalized_status = status_filter.strip().lower() if status_filter else None
    if normalized_status and normalized_status not in ALLOWED_FEEDBACK_STATUS:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="status must be one of: open, processing, resolved.",
        )

    normalized_source_page = source_page.strip().lower() if source_page else None
    if normalized_source_page and normalized_source_page not in ALLOWED_SOURCE_PAGES:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="source_page must be one of: dashboard, editor.",
        )

    query = select(UserFeedback, User).join(User, User.id == UserFeedback.user_id)

    if normalized_status:
        query = query.where(UserFeedback.status == normalized_status)

    if normalized_source_page:
        query = query.where(UserFeedback.source_page == normalized_source_page)

    normalized_search = (search or "").strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        query = query.where(
            or_(
                User.username.ilike(pattern),
                User.email.ilike(pattern),
                UserFeedback.issue_text.ilike(pattern),
                UserFeedback.trace_id.ilike(pattern),
                UserFeedback.request_id.ilike(pattern),
                UserFeedback.agent_run_id.ilike(pattern),
                UserFeedback.project_id.ilike(pattern),
                UserFeedback.agent_session_id.ilike(pattern),
            )
        )

    rows = session.exec(query.order_by(UserFeedback.created_at.desc())).all()
    items = [_to_admin_feedback_item(feedback, user) for feedback, user in rows]

    if has_screenshot is True:
        items = [item for item in items if item["has_screenshot"]]
    elif has_screenshot is False:
        items = [item for item in items if not item["has_screenshot"]]

    total = len(items)
    items = items[skip : skip + limit]

    log_with_context(
        logger,
        logging.INFO,
        "Admin listed user feedback",
        admin_user_id=current_user.id,
        count=len(items),
        total=total,
        status_filter=normalized_status,
        source_page=normalized_source_page,
        has_screenshot=has_screenshot,
        search=normalized_search or None,
    )

    return {"items": items, "total": total}


@router.get("/feedback/{feedback_id}")
def get_feedback_admin(
    feedback_id: str,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Get a single feedback item by ID."""
    feedback = session.get(UserFeedback, feedback_id)
    if not feedback:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found.",
        )

    user = session.get(User, feedback.user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback owner not found.",
        )

    log_with_context(
        logger,
        logging.INFO,
        "Admin fetched feedback detail",
        admin_user_id=current_user.id,
        feedback_id=feedback_id,
    )

    return _to_admin_feedback_item(feedback, user)


@router.patch("/feedback/{feedback_id}/status")
def update_feedback_status_admin(
    feedback_id: str,
    payload: FeedbackStatusUpdateRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Update feedback status (open/processing/resolved)."""
    feedback = session.get(UserFeedback, feedback_id)
    if not feedback:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found.",
        )

    old_status = feedback.status
    feedback.status = payload.status
    feedback.updated_at = utcnow()
    session.add(feedback)
    admin_audit_service.log_action(
        session, current_user.id, "update_feedback_status", "feedback", feedback_id,
        old_value={"status": old_status}, new_value={"status": payload.status},
        request=http_request, commit=False,
    )
    session.commit()
    session.refresh(feedback)

    user = session.get(User, feedback.user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback owner not found.",
        )

    log_with_context(
        logger,
        logging.INFO,
        "Admin updated feedback status",
        admin_user_id=current_user.id,
        feedback_id=feedback_id,
        status=payload.status,
    )

    return _to_admin_feedback_item(feedback, user)


@router.get("/feedback/{feedback_id}/screenshot")
def get_feedback_screenshot_admin(
    feedback_id: str,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """Download feedback screenshot file for admin review."""
    feedback = session.get(UserFeedback, feedback_id)
    if not feedback:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback not found.",
        )

    if not feedback.screenshot_path:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback screenshot not found.",
        )

    file_path, has_outside_root_candidate = _resolve_existing_feedback_screenshot(feedback.screenshot_path)

    if not file_path and has_outside_root_candidate:
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Screenshot path is outside allowed upload directory.",
        )

    if not file_path:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback screenshot file not found.",
        )

    canonical_path = str(file_path)
    if feedback.screenshot_path != canonical_path:
        feedback.screenshot_path = canonical_path
        feedback.updated_at = utcnow()
        session.add(feedback)
        session.commit()

    log_with_context(
        logger,
        logging.INFO,
        "Admin downloaded feedback screenshot",
        admin_user_id=current_user.id,
        feedback_id=feedback_id,
    )

    return FileResponse(
        path=str(file_path),
        media_type=feedback.screenshot_content_type or "application/octet-stream",
        filename=feedback.screenshot_original_name or file_path.name,
    )
