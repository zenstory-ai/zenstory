"""
In-app feedback API endpoints.

Provides issue feedback submission with optional screenshot uploads.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, UploadFile
from fastapi import File as FastAPIFile
from pydantic import BaseModel
from services.auth import get_current_active_user
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User, UserFeedback
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/feedback", tags=["feedback"])

MAX_ISSUE_TEXT_LENGTH = 2000
MAX_SOURCE_ROUTE_LENGTH = 255
MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024  # 5MB
ALLOWED_SOURCE_PAGES = {"dashboard", "editor"}
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/webp"}
DEFAULT_UPLOAD_DIR = "uploads/feedback"
IMAGE_CONTENT_TYPE_BY_FORMAT = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}


class FeedbackSubmitResponse(BaseModel):
    """Response model for feedback submit endpoint."""

    id: str
    message: str
    created_at: datetime


def _resolve_upload_dir() -> Path:
    configured_dir = os.getenv("FEEDBACK_UPLOAD_DIR", DEFAULT_UPLOAD_DIR).strip() or DEFAULT_UPLOAD_DIR
    upload_dir = Path(configured_dir)
    if not upload_dir.is_absolute():
        upload_dir = Path.cwd() / upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    return upload_dir


def _safe_extension(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    return ext if ext in ALLOWED_EXTENSIONS else ""


def _build_screenshot_filename(image_format: str) -> str:
    """Build an opaque server-owned filename with a canonical image extension."""
    generated_id = uuid4().hex
    if image_format == "png":
        return f"{generated_id}.png"
    if image_format == "jpeg":
        return f"{generated_id}.jpg"
    if image_format == "webp":
        return f"{generated_id}.webp"
    raise ValueError(f"Unsupported image format: {image_format}")


def _detect_image_format(content: bytes) -> str | None:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


@router.post("", response_model=FeedbackSubmitResponse)
def submit_feedback(
    issue_text: str = Form(..., max_length=MAX_ISSUE_TEXT_LENGTH),
    source_page: str = Form(...),
    source_route: str | None = Form(None),
    trace_id: str | None = Form(None, max_length=64),
    request_id: str | None = Form(None, max_length=64),
    agent_run_id: str | None = Form(None, max_length=64),
    project_id: str | None = Form(None, max_length=64),
    agent_session_id: str | None = Form(None, max_length=128),
    screenshot: UploadFile | None = FastAPIFile(default=None),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Submit user feedback with optional screenshot attachment."""
    normalized_issue = issue_text.strip()
    if not normalized_issue:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            detail="Issue text cannot be empty.",
        )

    normalized_source_page = source_page.strip().lower()
    if normalized_source_page not in ALLOWED_SOURCE_PAGES:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            detail="source_page must be one of: dashboard, editor.",
        )

    normalized_source_route = (source_route or "").strip() or None
    if normalized_source_route and len(normalized_source_route) > MAX_SOURCE_ROUTE_LENGTH:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            detail=f"source_route length must be <= {MAX_SOURCE_ROUTE_LENGTH}.",
        )

    normalized_trace_id = (trace_id or "").strip() or None
    normalized_request_id = (request_id or "").strip() or None
    normalized_agent_run_id = (agent_run_id or "").strip() or None
    normalized_project_id = (project_id or "").strip() or None
    normalized_agent_session_id = (agent_session_id or "").strip() or None

    screenshot_path: str | None = None
    screenshot_original_name: str | None = None
    screenshot_content_type: str | None = None
    screenshot_size_bytes: int | None = None
    created_screenshot_path: Path | None = None

    if screenshot is not None and screenshot.filename:
        ext = _safe_extension(screenshot.filename)
        if not ext:
            raise APIException(
                error_code=ErrorCode.FILE_TYPE_INVALID,
                status_code=400,
                detail="Only PNG/JPG/JPEG/WEBP screenshots are supported.",
            )

        if screenshot.content_type and screenshot.content_type.lower() not in ALLOWED_CONTENT_TYPES:
            raise APIException(
                error_code=ErrorCode.FILE_TYPE_INVALID,
                status_code=400,
                detail="Unsupported screenshot content type.",
            )

        content = screenshot.file.read(MAX_SCREENSHOT_BYTES + 1)
        content_size = len(content)
        if content_size == 0:
            raise APIException(
                error_code=ErrorCode.VALIDATION_ERROR,
                status_code=400,
                detail="Screenshot file is empty.",
            )
        if content_size > MAX_SCREENSHOT_BYTES:
            raise APIException(
                error_code=ErrorCode.FILE_TOO_LARGE,
                status_code=400,
                detail="Screenshot exceeds 5MB limit.",
            )

        detected_format = _detect_image_format(content)
        if detected_format is None:
            raise APIException(
                error_code=ErrorCode.FILE_TYPE_INVALID,
                status_code=400,
                detail="Screenshot must be a valid PNG/JPEG/WEBP image.",
            )

        expected_format = "jpeg" if ext in {".jpg", ".jpeg"} else ext.lstrip(".")
        if detected_format != expected_format:
            raise APIException(
                error_code=ErrorCode.FILE_TYPE_INVALID,
                status_code=400,
                detail="Screenshot extension does not match image content.",
            )

        expected_content_type = IMAGE_CONTENT_TYPE_BY_FORMAT[detected_format]
        if screenshot.content_type and screenshot.content_type.lower() != expected_content_type:
            raise APIException(
                error_code=ErrorCode.FILE_TYPE_INVALID,
                status_code=400,
                detail="Screenshot content type does not match image content.",
            )

        upload_dir = _resolve_upload_dir()
        filename = _build_screenshot_filename(detected_format)
        final_path = upload_dir / filename
        with open(final_path, "xb") as f:
            f.write(content)
        created_screenshot_path = final_path

        screenshot_path = str(final_path.resolve())
        screenshot_original_name = screenshot.filename
        screenshot_content_type = screenshot.content_type
        screenshot_size_bytes = content_size

    feedback = UserFeedback(
        user_id=current_user.id,
        source_page=normalized_source_page,
        source_route=normalized_source_route,
        issue_text=normalized_issue,
        trace_id=normalized_trace_id,
        request_id=normalized_request_id,
        agent_run_id=normalized_agent_run_id,
        project_id=normalized_project_id,
        agent_session_id=normalized_agent_session_id,
        screenshot_path=screenshot_path,
        screenshot_original_name=screenshot_original_name,
        screenshot_content_type=screenshot_content_type,
        screenshot_size_bytes=screenshot_size_bytes,
        status="open",
    )
    try:
        session.add(feedback)
        session.flush()
        session.refresh(feedback)
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception as rollback_error:
            logger.warning("Feedback persistence rollback failed: %s", rollback_error)
        if created_screenshot_path is not None:
            try:
                created_screenshot_path.unlink(missing_ok=True)
            except OSError as cleanup_error:
                logger.warning("Failed to remove uncommitted feedback screenshot: %s", cleanup_error)
        raise

    log_with_context(
        logger,
        logging.INFO,
        "User feedback submitted",
        feedback_id=feedback.id,
        user_id=current_user.id,
        source_page=feedback.source_page,
        has_screenshot=bool(feedback.screenshot_path),
        # Include related debug ids under distinct keys to avoid overriding request context.
        related_trace_id=feedback.trace_id,
        related_request_id=feedback.request_id,
        related_agent_run_id=feedback.agent_run_id,
    )

    return FeedbackSubmitResponse(
        id=feedback.id,
        message="Feedback submitted successfully.",
        created_at=feedback.created_at,
    )
