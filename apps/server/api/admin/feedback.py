"""
Admin feedback management API endpoints.

Provides listing, status updates, and screenshot retrieval for user feedback.
"""

import logging
import os
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.responses import FileResponse, Response
from sqlmodel import Session, col, func, or_, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User, UserFeedback
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from services.infra.admin_analytics_cache import (
    DEFAULT_CACHE_TTL_SECONDS,
    cached_admin_analytics,
)
from services.infra.upload_storage import (
    UploadNotFoundError,
    UploadReferenceError,
    UploadStorageError,
    get_upload_storage_or_503,
    resolve_feedback_upload_root,
)
from services.usage.admin_ux_metrics_service import (
    CHAT_VOTE_FILTERS,
    UX_HEALTH_DAYS,
    list_chat_feedback_votes,
)
from utils.logger import get_logger, log_with_context

from .schemas import ChatVotesListResponse, FeedbackStatusUpdateRequest

logger = get_logger(__name__)

router = APIRouter(tags=["admin-feedback"])

ALLOWED_FEEDBACK_STATUS = {"open", "processing", "resolved"}
ALLOWED_SOURCE_PAGES = {"dashboard", "editor"}


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

    upload_root = resolve_feedback_upload_root()
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


def _to_admin_feedback_item(feedback: UserFeedback, user: User, storage) -> dict:
    if feedback.screenshot_path and feedback.screenshot_path.startswith("s3://"):
        has_screenshot = storage.feedback_reference_exists(feedback.screenshot_path)
    else:
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

    conditions = []
    if normalized_status:
        conditions.append(UserFeedback.status == normalized_status)

    if normalized_source_page:
        conditions.append(UserFeedback.source_page == normalized_source_page)

    normalized_search = (search or "").strip()
    if normalized_search:
        pattern = f"%{normalized_search}%"
        conditions.append(
            or_(
                col(User.username).ilike(pattern),
                col(User.email).ilike(pattern),
                col(UserFeedback.issue_text).ilike(pattern),
                col(UserFeedback.trace_id).ilike(pattern),
                col(UserFeedback.request_id).ilike(pattern),
                col(UserFeedback.agent_run_id).ilike(pattern),
                col(UserFeedback.project_id).ilike(pattern),
                col(UserFeedback.agent_session_id).ilike(pattern),
            )
        )

    def _build_count_query(extra_conditions: list | None = None):
        stmt = select(func.count(UserFeedback.id)).select_from(UserFeedback)
        if normalized_search:
            stmt = stmt.join(User, col(User.id) == col(UserFeedback.user_id))
        all_conds = [*conditions, *(extra_conditions or ())]
        if all_conds:
            stmt = stmt.where(*all_conds)
        return stmt

    query = select(UserFeedback, User).join(User, col(User.id) == col(UserFeedback.user_id))
    if conditions:
        query = query.where(*conditions)

    def _has_valid_screenshot(screenshot_path: str | None, storage) -> bool:
        if not screenshot_path:
            return False
        if screenshot_path.startswith("s3://"):
            return storage.feedback_reference_exists(screenshot_path)
        resolved_path, _ = _resolve_existing_feedback_screenshot(screenshot_path)
        return resolved_path is not None

    storage = get_upload_storage_or_503()
    try:
        has_path_cond = (col(UserFeedback.screenshot_path).is_not(None), col(UserFeedback.screenshot_path) != "")
        no_path_cond = or_(col(UserFeedback.screenshot_path).is_(None), col(UserFeedback.screenshot_path) == "")

        if has_screenshot is None:
            total = int(session.exec(_build_count_query()).one() or 0)
            rows = session.exec(
                query.order_by(col(UserFeedback.created_at).desc(), col(UserFeedback.id).desc()).offset(skip).limit(limit)
            ).all()
            items = [_to_admin_feedback_item(feedback, user, storage) for feedback, user in rows]
        elif has_screenshot is True:
            # Restrict SQL scan strictly to rows with a non-empty screenshot_path.
            path_query = select(UserFeedback.id, UserFeedback.screenshot_path).select_from(UserFeedback)
            if normalized_search:
                path_query = path_query.join(User, col(User.id) == col(UserFeedback.user_id))
            path_rows = session.exec(
                path_query.where(*conditions, *has_path_cond)
                .order_by(col(UserFeedback.created_at).desc(), col(UserFeedback.id).desc())
            ).all()
            valid_ids = [
                fb_id for fb_id, sc_path in path_rows if _has_valid_screenshot(sc_path, storage)
            ]
            total = len(valid_ids)
            page_ids = valid_ids[skip : skip + limit]
            if page_ids:
                page_rows = session.exec(
                    select(UserFeedback, User)
                    .join(User, col(User.id) == col(UserFeedback.user_id))
                    .where(col(UserFeedback.id).in_(page_ids))
                    .order_by(col(UserFeedback.created_at).desc(), col(UserFeedback.id).desc())
                ).all()
                items = [_to_admin_feedback_item(feedback, user, storage) for feedback, user in page_rows]
            else:
                items = []
        else:
            # Rows without screenshot_path definitely have has_screenshot=False; only inspect
            # the (typically small) subset with non-empty paths for missing/invalid files.
            path_query = select(UserFeedback.id, UserFeedback.screenshot_path).select_from(UserFeedback)
            if normalized_search:
                path_query = path_query.join(User, col(User.id) == col(UserFeedback.user_id))
            path_rows = session.exec(path_query.where(*conditions, *has_path_cond)).all()
            broken_ids = [
                fb_id for fb_id, sc_path in path_rows if not _has_valid_screenshot(sc_path, storage)
            ]
            effective_no_screenshot = (
                or_(no_path_cond, col(UserFeedback.id).in_(broken_ids))
                if broken_ids
                else no_path_cond
            )
            total = int(session.exec(_build_count_query([effective_no_screenshot])).one() or 0)
            rows = session.exec(
                query.where(effective_no_screenshot)
                .order_by(col(UserFeedback.created_at).desc(), col(UserFeedback.id).desc())
                .offset(skip)
                .limit(limit)
            ).all()
            items = [_to_admin_feedback_item(feedback, user, storage) for feedback, user in rows]
    finally:
        getattr(storage, "close", lambda: None)()

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


@router.get("/feedback/chat-votes", response_model=ChatVotesListResponse)
def list_chat_votes_admin(
    days: int = Query(7),
    vote: str = Query("all"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    refresh: bool = Query(False),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """List explicit chat thumbs-up/down votes and window summary (no manuscript/prompt text)."""
    if days not in UX_HEALTH_DAYS:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="days must be one of: 7, 14, 30.",
        )
    normalized_vote = vote.strip().lower()
    if normalized_vote not in CHAT_VOTE_FILTERS:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="vote must be one of: all, up, down.",
        )

    payload = cached_admin_analytics(
        "feedback:chat_votes",
        lambda: list_chat_feedback_votes(
            session,
            days=days,
            vote=normalized_vote,
            page=page,
            page_size=page_size,
        ),
        ttl_seconds=60 if days == 7 else DEFAULT_CACHE_TTL_SECONDS,
        refresh=refresh,
        days=days,
        vote=normalized_vote,
        page=page,
        page_size=page_size,
    )

    log_with_context(
        logger,
        logging.INFO,
        "Admin listed chat feedback votes",
        admin_user_id=current_user.id,
        window_days=days,
        vote=normalized_vote,
        count=len(payload["items"]),
        total=payload["total"],
    )

    return ChatVotesListResponse(**payload)


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

    user = session.get(User, feedback.user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Feedback owner not found.",
        )

    storage = get_upload_storage_or_503()

    try:
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

        log_with_context(
            logger,
            logging.INFO,
            "Admin updated feedback status",
            admin_user_id=current_user.id,
            feedback_id=feedback_id,
            status=payload.status,
        )

        return _to_admin_feedback_item(feedback, user, storage)
    finally:
        getattr(storage, "close", lambda: None)()


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

    if feedback.screenshot_path.startswith("s3://"):
        storage = None
        try:
            storage = get_upload_storage_or_503()
            content = storage.read_feedback(feedback.screenshot_path)
        except UploadReferenceError as exc:
            raise APIException(
                error_code=ErrorCode.NOT_AUTHORIZED,
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Screenshot object reference is not allowed.",
            ) from exc
        except UploadNotFoundError as exc:
            raise APIException(
                error_code=ErrorCode.NOT_FOUND,
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Feedback screenshot file not found.",
            ) from exc
        except (UploadStorageError, OSError, ValueError) as exc:
            raise APIException(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        finally:
            if storage is not None:
                getattr(storage, "close", lambda: None)()
        log_with_context(
            logger,
            logging.INFO,
            "Admin downloaded feedback screenshot",
            admin_user_id=current_user.id,
            feedback_id=feedback_id,
        )
        return Response(
            content=content,
            media_type=feedback.screenshot_content_type or "application/octet-stream",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{feedback.screenshot_original_name or "screenshot"}"'
                )
            },
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
