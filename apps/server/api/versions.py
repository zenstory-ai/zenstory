"""
File version API endpoints.

Provides REST endpoints for managing file version history.
"""


import contextlib
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, ConfigDict
from services.auth import get_current_active_user
from services.file_version import get_file_version_service
from sqlmodel import Session, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import File, FileVersion, Project, User
from models.file_version import CHANGE_SOURCE_USER
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["versions"])


# ==================== Helper Functions ====================


def verify_file_ownership(session: Session, file_id: str, user: User) -> File:
    """Verify that the user owns the file's project."""
    file = session.get(File, file_id)
    if not file or file.is_deleted:
        raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

    project = session.get(Project, file.project_id)
    if not project or project.owner_id != user.id:
        raise APIException(error_code=ErrorCode.NOT_AUTHORIZED, status_code=403)

    # Check if project is soft-deleted
    if project.is_deleted:
        raise APIException(error_code=ErrorCode.PROJECT_NOT_FOUND, status_code=404)

    return file


# ==================== Request/Response Models ====================


class FileVersionResponse(BaseModel):
    """Response model for file version."""

    id: str
    file_id: str
    project_id: str
    version_number: int
    is_base_version: bool
    word_count: int
    char_count: int
    change_type: str
    change_source: str
    change_summary: str | None
    lines_added: int
    lines_removed: int
    created_at: str

    model_config = ConfigDict(from_attributes=True)


class FileVersionListResponse(BaseModel):
    """Response model for version list."""

    versions: list[FileVersionResponse]
    total: int
    file_id: str
    file_title: str


class CreateVersionRequest(BaseModel):
    """Request model for creating a version.

    注意 `change_source` **不再**参与配额判定，也不会被原样落库：
    该端点在语义上就是「用户发起的写入」，来源由服务端强制为 user
    （见 create_file_version）。这里保留字段只为向后兼容旧客户端，
    并用 Literal 收窄取值，避免任意字符串进入数据库。
    """

    content: str
    change_type: Literal["create", "edit", "ai_edit", "restore", "auto_save"] = "edit"
    change_source: Literal["user", "ai", "system"] = "user"
    change_summary: str | None = None


class VersionComparisonResponse(BaseModel):
    """Response model for version comparison."""

    file_id: str
    version1: dict
    version2: dict
    unified_diff: str
    html_diff: list
    stats: dict


class VersionContentResponse(BaseModel):
    """Response model for version content."""

    file_id: str
    version_number: int
    content: str
    word_count: int
    char_count: int
    created_at: str


class RollbackResponse(BaseModel):
    """Response model for rollback operation."""

    success: bool
    message: str
    file_id: str
    restored_version: int
    new_version_number: int | None
    snapshot_created: bool
    version_quota_exceeded: bool


class RollbackRequest(BaseModel):
    """Optional exact token; absent body keeps intentional history restoration."""

    expected_updated_at: datetime | None = None


# ==================== API Endpoints ====================


@router.get("/files/{file_id}/versions", response_model=FileVersionListResponse)
def get_file_versions(
    file_id: str,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    include_auto_save: bool = Query(False),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get version history for a file.

    Returns a list of versions with metadata (not content).
    Use /versions/{version_id}/content to get actual content.
    """
    log_with_context(
        logger,
        20,  # INFO
        "get_file_versions called",
        file_id=file_id,
        user_id=current_user.id,
        limit=limit,
        offset=offset,
        include_auto_save=include_auto_save,
    )

    # Check file exists and user has access
    file = verify_file_ownership(session, file_id, current_user)

    service = get_file_version_service()
    versions = service.get_versions(
        session=session,
        file_id=file_id,
        limit=limit,
        offset=offset,
        include_auto_save=include_auto_save,
    )

    total = service.get_version_count(
        session,
        file_id,
        include_auto_save=include_auto_save,
    )

    log_with_context(
        logger,
        20,  # INFO
        "get_file_versions completed",
        file_id=file_id,
        user_id=current_user.id,
        version_count=len(versions),
        total=total,
    )

    return FileVersionListResponse(
        versions=[
            FileVersionResponse(
                id=v.id,
                file_id=v.file_id,
                project_id=v.project_id,
                version_number=v.version_number,
                is_base_version=v.is_base_version,
                word_count=v.word_count,
                char_count=v.char_count,
                change_type=v.change_type,
                change_source=v.change_source,
                change_summary=v.change_summary,
                lines_added=v.lines_added,
                lines_removed=v.lines_removed,
                created_at=v.created_at.isoformat(),
            )
            for v in versions
        ],
        total=total,
        file_id=file_id,
        file_title=file.title,
    )


@router.post("/files/{file_id}/versions", response_model=FileVersionResponse)
def create_file_version(
    file_id: str,
    request: CreateVersionRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Create a new version for a file.

    This is typically called when saving file content.
    """
    log_with_context(
        logger,
        20,  # INFO
        "create_file_version called",
        file_id=file_id,
        user_id=current_user.id,
        change_type=request.change_type,
        change_source=request.change_source,
        content_length=len(request.content),
    )

    # Check file exists and user has access
    verify_file_ownership(session, file_id, current_user)

    service = get_file_version_service()

    try:
        # 来源必须由服务端判定：这是一次带 Bearer token 的显式用户写入。
        # 若沿用 request.change_source，客户端只要传 "ai"/"system" 就能同时
        # 绕过配额闸门（闸门只拦 user）和配额计数（计数只数 user 行），
        # file_versions_per_file 会彻底失效。change_source 与 quota_source
        # 一起钉死成 user，闸门与计数口径才是同一个集合。
        from agent.tools.file_ops.edit import file_write_lock
        from database import is_postgres

        lock_ctx = (
            contextlib.nullcontext() if is_postgres else file_write_lock(file_id)
        )
        with lock_ctx:
            version = service.create_version(
                session=session,
                file_id=file_id,
                new_content=request.content,
                change_type=request.change_type,
                change_source=CHANGE_SOURCE_USER,
                change_summary=request.change_summary,
                user_id=current_user.id,
                quota_source=CHANGE_SOURCE_USER,
            )

        log_with_context(
            logger,
            20,  # INFO
            "create_file_version completed",
            file_id=file_id,
            user_id=current_user.id,
            version_number=version.version_number,
            change_type=request.change_type,
        )

        return FileVersionResponse(
            id=version.id,
            file_id=version.file_id,
            project_id=version.project_id,
            version_number=version.version_number,
            is_base_version=version.is_base_version,
            word_count=version.word_count,
            char_count=version.char_count,
            change_type=version.change_type,
            change_source=version.change_source,
            change_summary=version.change_summary,
            lines_added=version.lines_added,
            lines_removed=version.lines_removed,
            created_at=version.created_at.isoformat(),
        )
    except ValueError as e:
        raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400, detail=str(e)) from e


@router.get("/versions/{version_id}", response_model=FileVersionResponse)
def get_version(
    version_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Get a specific version by ID."""
    service = get_file_version_service()
    version = service.get_version(session, version_id)

    if not version:
        raise APIException(error_code=ErrorCode.VERSION_NOT_FOUND, status_code=404)

    # Verify ownership
    verify_file_ownership(session, version.file_id, current_user)

    return FileVersionResponse(
        id=version.id,
        file_id=version.file_id,
        project_id=version.project_id,
        version_number=version.version_number,
        is_base_version=version.is_base_version,
        word_count=version.word_count,
        char_count=version.char_count,
        change_type=version.change_type,
        change_source=version.change_source,
        change_summary=version.change_summary,
        lines_added=version.lines_added,
        lines_removed=version.lines_removed,
        created_at=version.created_at.isoformat(),
    )


@router.get("/files/{file_id}/versions/{version_number}/content", response_model=VersionContentResponse)
def get_version_content(
    file_id: str,
    version_number: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get the full content of a specific version.

    Reconstructs content from diffs if necessary.
    """
    # Verify ownership
    verify_file_ownership(session, file_id, current_user)

    service = get_file_version_service()

    try:
        content = service.get_content_at_version(session, file_id, version_number)

        # Get target version metadata directly (avoid pagination limits).
        version = session.exec(
            select(FileVersion).where(
                FileVersion.file_id == file_id,
                FileVersion.version_number == version_number,
            )
        ).first()

        if not version:
            raise APIException(error_code=ErrorCode.VERSION_NOT_FOUND, status_code=404)

        return VersionContentResponse(
            file_id=file_id,
            version_number=version_number,
            content=content,
            word_count=version.word_count,
            char_count=version.char_count,
            created_at=version.created_at.isoformat(),
        )
    except ValueError as e:
        raise APIException(error_code=ErrorCode.VERSION_NOT_FOUND, status_code=404, detail=str(e)) from e


@router.get(
    "/files/{file_id}/versions/compare",
    response_model=VersionComparisonResponse,
)
def compare_versions(
    file_id: str,
    v1: int = Query(..., description="First version number (older)"),
    v2: int = Query(..., description="Second version number (newer)"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Compare two versions of a file.

    Returns a unified diff and structured HTML diff data.
    """
    # Check file exists and user has access
    verify_file_ownership(session, file_id, current_user)

    service = get_file_version_service()

    try:
        comparison = service.compare_versions(session, file_id, v1, v2)
        return VersionComparisonResponse(**comparison)
    except ValueError as e:
        raise APIException(error_code=ErrorCode.VERSION_NOT_FOUND, status_code=404, detail=str(e)) from e


@router.post("/files/{file_id}/versions/{version_number}/rollback", response_model=RollbackResponse)
def rollback_to_version(
    file_id: str,
    version_number: int,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
    rollback_request: RollbackRequest | None = None,
):
    """
    Rollback a file to a previous version.

    Restores content, conditionally when a post-edit token is supplied, and
    records a new history snapshot when the user's version quota permits it.
    """
    # Check file exists and user has access
    verify_file_ownership(session, file_id, current_user)

    service = get_file_version_service()

    try:
        updated_file, new_version, version_quota_exceeded = service.rollback_to_version(
            session,
            file_id,
            version_number,
            user_id=current_user.id,
            expected_updated_at=(rollback_request.expected_updated_at if rollback_request else None),
        )

        try:
            from services.infra.dashboard_cache import dashboard_cache

            dashboard_cache.bump_project_version(
                user_id=current_user.id,
                project_id=updated_file.project_id,
            )
        except Exception as exc:
            log_with_context(
                logger,
                10,  # DEBUG
                "Failed to bump dashboard cache after version rollback",
                error=str(exc),
                file_id=file_id,
                project_id=updated_file.project_id,
            )

        try:
            from services.llama_index import schedule_index_upsert

            extra_metadata = updated_file.get_metadata()
            if updated_file.parent_id:
                extra_metadata = {**extra_metadata, "parent_id": updated_file.parent_id}
            background_tasks.add_task(
                schedule_index_upsert,
                project_id=updated_file.project_id,
                entity_type=updated_file.file_type,
                entity_id=updated_file.id,
                title=updated_file.title,
                content=updated_file.content or "",
                extra_metadata=extra_metadata,
                user_id=current_user.id,
            )
        except Exception as exc:
            log_with_context(
                logger,
                10,  # DEBUG
                "Failed to schedule vector reconciliation after version rollback",
                error=str(exc),
                file_id=file_id,
                project_id=updated_file.project_id,
            )

        return RollbackResponse(
            success=True,
            message=f"Successfully rolled back to version {version_number}",
            file_id=file_id,
            restored_version=version_number,
            new_version_number=(
                new_version.version_number if new_version is not None else None
            ),
            snapshot_created=new_version is not None,
            version_quota_exceeded=version_quota_exceeded,
        )
    except ValueError as e:
        raise APIException(error_code=ErrorCode.VERSION_NOT_FOUND, status_code=404, detail=str(e)) from e


@router.get("/files/{file_id}/versions/latest", response_model=FileVersionResponse)
def get_latest_version(
    file_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Get the latest version for a file."""
    # Verify ownership
    verify_file_ownership(session, file_id, current_user)

    service = get_file_version_service()
    version = service.get_latest_version(session, file_id)

    if not version:
        raise APIException(error_code=ErrorCode.VERSION_NO_VERSIONS_FOUND, status_code=404)

    return FileVersionResponse(
        id=version.id,
        file_id=version.file_id,
        project_id=version.project_id,
        version_number=version.version_number,
        is_base_version=version.is_base_version,
        word_count=version.word_count,
        char_count=version.char_count,
        change_type=version.change_type,
        change_source=version.change_source,
        change_summary=version.change_summary,
        lines_added=version.lines_added,
        lines_removed=version.lines_removed,
        created_at=version.created_at.isoformat(),
    )
