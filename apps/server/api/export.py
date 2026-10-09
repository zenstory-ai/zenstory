"""
Export API endpoints.

Provides endpoints for exporting project content to downloadable files.
"""
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from services.auth import get_current_active_user
from services.export_service import export_drafts_to_txt
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import Project, User
from services.quota_service import quota_service
from services.subscription.defaults import SUPPORTED_EXPORT_FORMATS, normalize_export_formats
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["export"])


@router.get("/projects/{project_id}/export/drafts")
def export_project_drafts(
    project_id: str,
    include_outline: bool = Query(False, description="Put the outline files before the chapters"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Export all drafts from a project as a single TXT file.

    The drafts are merged in chapter order with titles and separators, under the
    work title. With ``include_outline`` the non-empty outline files come first.

    Args:
        project_id: The project ID to export drafts from

    Returns:
        A downloadable TXT file with all drafts merged

    Raises:
        403: If the user doesn't own the project
        404: If the project doesn't exist or has no drafts
    """
    log_with_context(
        logger,
        20,  # INFO
        "export_project_drafts called",
        project_id=project_id,
        user_id=current_user.id,
    )

    # 1. Check project exists
    project = session.get(Project, project_id)
    if not project:
        log_with_context(
            logger,
            40,  # ERROR
            "export_project_drafts: Project not found",
            project_id=project_id,
            user_id=current_user.id,
        )
        raise APIException(error_code=ErrorCode.PROJECT_NOT_FOUND, status_code=404)

    # 2. Check ownership - user must own the project
    # Admin/superuser can export any project for support/debugging.
    if project.owner_id != current_user.id and not current_user.is_superuser:
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED_TO_EXPORT,
            status_code=403
        )

    # Check if project is soft-deleted
    if project.is_deleted:
        log_with_context(
            logger,
            40,  # ERROR
            "export_project_drafts: Project soft-deleted",
            project_id=project_id,
            user_id=current_user.id,
        )
        raise APIException(error_code=ErrorCode.PROJECT_NOT_FOUND, status_code=404)

    # Check export format restriction
    requested_format = "txt"
    plan = quota_service.get_user_plan(session, current_user.id)
    if plan:
        raw_allowed_formats = plan.features.get("export_formats")
        if raw_allowed_formats is None:
            allowed_formats = list(SUPPORTED_EXPORT_FORMATS)
        else:
            allowed_formats = normalize_export_formats(raw_allowed_formats)
        if requested_format not in allowed_formats:
            raise APIException(
                error_code=ErrorCode.QUOTA_EXPORT_FORMAT_RESTRICTED,
                status_code=402,
                detail=f"Export format '{requested_format}' is not available on your plan. Available: {', '.join(allowed_formats)}",
            )

    # 3. Generate export content
    content = export_drafts_to_txt(
        session,
        project_id,
        title=project.name,
        include_outline=include_outline,
    )
    filename = f"{project.name}_{'大纲和正文' if include_outline else '正文'}.txt"

    if not content:
        log_with_context(
            logger,
            20,  # INFO: valid business response for an empty project
            "export_project_drafts: No drafts found",
            project_id=project_id,
            user_id=current_user.id,
        )
        raise APIException(
            error_code=ErrorCode.EXPORT_NO_DRAFTS,
            status_code=404
        )

    log_with_context(
        logger,
        20,  # INFO
        "export_project_drafts completed",
        project_id=project_id,
        user_id=current_user.id,
        content_length=len(content),
        filename=filename,
    )

    # 4. Add UTF-8 BOM for Windows Notepad compatibility
    content_with_bom = '\ufeff' + content

    # 5. Build filename with RFC 5987 encoding for Chinese characters
    encoded_filename = quote(filename)

    # 6. Return as downloadable file
    return Response(
        content=content_with_bom.encode('utf-8'),
        media_type="text/plain; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
        }
    )
