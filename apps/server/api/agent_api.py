"""
Agent API endpoints for external AI agents (e.g., OpenClaw).

Provides REST API endpoints for programmatic access using X-Agent-API-Key authentication.
All endpoints require valid API key with appropriate scopes.
"""

import asyncio
import contextlib
import contextvars
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import ClassVar, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func
from sqlalchemy.orm import load_only
from sqlmodel import col, select

from agent.context.assembler import ContextAssembler
from api.agent_dependencies import AgentAuthContext, require_project_access, require_scope
from config.datetime_utils import advance_timestamp, normalize_datetime_to_utc, utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import create_session
from middleware.rate_limit import require_agent_rate_limit
from models import File, FileVersion, Project
from models.file_version import CHANGE_SOURCE_USER, CHANGE_TYPE_AI_EDIT, CHANGE_TYPE_CREATE
from services.agent_auth_service import verify_project_access
from services.features.file_version_service import get_file_version_service
from services.file_tree_rules import (
    MAX_FILE_ORDER,
    ParentNotFoundError,
    lock_project_for_files,
    resolve_new_file_order,
    validate_parent_assignment,
)
from services.project_service import (
    create_project_with_default_folders,
    resolve_template_lang,
)
from utils.logger import get_logger, log_with_context
from utils.title_sequence import resolve_persisted_sequence_order

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/agent", tags=["Agent API"])


# ==================== Request/Response Models ====================


class ProjectResponse(BaseModel):
    """Response model for a project."""

    id: str
    name: str
    description: str | None
    project_type: str | None
    owner_id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FileCreate(BaseModel):
    """Request body for creating a file."""

    title: str = Field(..., description="File title")
    file_type: str = Field(default="draft", description="File type (outline, draft, character, lore, etc.)")
    content: str = Field(default="", description="File content")
    parent_id: str | None = Field(default=None, description="Parent folder ID (a folder in the same project)")
    order: int | None = Field(
        default=None,
        ge=0,
        le=MAX_FILE_ORDER,
        description=(
            "Sort order among siblings. Omitted: taken from a chapter number in the title/metadata, "
            "else appended after the last sibling. draft/outline/script files with chapter-like titles "
            "(第N章 / Chapter N) always sort by N."
        ),
    )
    metadata: dict | None = Field(default=None, description="Additional metadata")


class FileUpdate(BaseModel):
    """Request body for updating a file."""

    title: str | None = Field(default=None, description="New title")
    content: str | None = Field(default=None, description="New content")
    order: int | None = Field(default=None, ge=0, le=MAX_FILE_ORDER, description="New sort order among siblings")
    base_updated_at: datetime | None = Field(
        default=None,
        description="Optional optimistic concurrency token from the loaded file. Nonmatching timestamps return 409.",
    )


class FileMove(BaseModel):
    """Request body for moving a file."""

    parent_id: str | None = Field(..., description="Target folder ID in the same project, or null for the project root")
    order: int | None = Field(
        default=None, ge=0, le=MAX_FILE_ORDER, description="Optional new sort order among the new siblings"
    )


class FileResponse(BaseModel):
    """Response model for a file."""

    id: str
    project_id: str
    title: str
    content: str
    file_type: str
    parent_id: str | None
    order: int
    file_metadata: str | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    # All valid fields for ?fields= parameter
    VALID_FIELDS: ClassVar[set[str]] = {
        "id", "project_id", "title", "content", "file_type",
        "parent_id", "order", "file_metadata", "created_at", "updated_at",
    }


class FileWriteResponse(FileResponse):
    """Response for a content write (create / PUT). Mirrors the web PUT /files/{id}."""

    version_quota_exceeded: bool = Field(
        default=False,
        description=(
            "The content was saved, but no version was recorded because the plan's per-file "
            "version quota is full."
        ),
    )


class _FilteredFileResponse(BaseModel):
    """Dynamic response for filtered file fields."""
    model_config = ConfigDict(extra="allow")


class FileListResponse(BaseModel):
    """Paginated response for file listing."""

    files: list[_FilteredFileResponse]
    total: int
    limit: int
    offset: int


def _serialize_file_response(file: File, fields: set[str] | None) -> dict:
    """Serialize a complete file or read only the explicitly projected attributes."""
    if fields is None:
        return FileResponse.model_validate(file).model_dump()
    return {field: getattr(file, field) for field in FileResponse.model_fields if field in fields}


class FolderResponse(BaseModel):
    """A folder created with a new project."""

    id: str
    title: str
    file_type: str
    order: int

    model_config = ConfigDict(from_attributes=True)


class ProjectCreateResponse(ProjectResponse):
    """Response for project creation: the project plus its default folders."""

    folders: list[FolderResponse]


class FileVersionResponse(BaseModel):
    """Version metadata (no content). Mirrors the web versions API."""

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
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class FileVersionListResponse(BaseModel):
    """Paginated version history, newest first."""

    versions: list[FileVersionResponse]
    total: int
    limit: int
    offset: int
    file_id: str
    file_title: str


class FileVersionDetailResponse(FileVersionResponse):
    """One version including its full reconstructed content."""

    content: str


class FileRollbackResponse(BaseModel):
    """Result of restoring a file to an earlier version."""

    success: bool
    message: str
    file_id: str
    restored_version: int
    new_version_number: int | None
    snapshot_created: bool
    version_quota_exceeded: bool
    updated_at: datetime


class ProjectCreate(BaseModel):
    """Request body for creating a project."""

    name: str = Field(..., min_length=1, max_length=100, description="Project name")
    description: str | None = Field(default=None, max_length=500, description="Project description")
    project_type: Literal["novel", "short", "screenplay"] = Field(default="novel", description="Project type: novel, short, screenplay")


class ProjectUpdate(BaseModel):
    """Request body for updating a project."""

    name: str | None = Field(default=None, min_length=1, max_length=100, description="Project name")
    description: str | None = Field(default=None, max_length=500, description="Project description")


# ==================== Helpers ====================


def _load_accessible_file(session, user_id: str, api_key, file_id: str, *, for_write: bool = False) -> File:
    """Load a live file the key may use: another user's file is 404, a project outside the key's allowlist 403."""
    if for_write:
        from database import is_postgres

        if is_postgres:
            file = session.exec(
                select(File).where(File.id == file_id)
                .with_for_update(key_share=True)
                .execution_options(populate_existing=True)
            ).first()
        else:
            file = session.get(File, file_id, populate_existing=True)
    else:
        file = session.get(File, file_id)
    if not file or file.is_deleted:
        raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

    project = session.get(Project, file.project_id)
    if not project or project.owner_id != user_id or project.is_deleted:
        raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

    if not verify_project_access(api_key, file.project_id):
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=403,
            detail="API Key does not have access to this project",
        )
    return file


def _validate_parent(session, project_id: str, parent_id: str | None, *, moving_file_id: str | None = None, refresh_parent: bool = False) -> str | None:
    """services.file_tree_rules.validate_parent_assignment, translated to Agent API errors (400)."""
    try:
        return validate_parent_assignment(session, project_id, parent_id, moving_file_id=moving_file_id, refresh_parent=refresh_parent)
    except ParentNotFoundError as exc:
        raise APIException(
            error_code=ErrorCode.FILE_NOT_FOUND,
            status_code=400,
            detail=f"Parent folder {parent_id} not found in this project",
        ) from exc
    except ValueError as exc:
        # Parent is not a folder, or the move would put a folder under itself/its descendant.
        raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400, detail=str(exc)) from exc


def _file_metadata(file: File) -> dict:
    if not file.file_metadata:
        return {}
    try:
        parsed = json.loads(file.file_metadata)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _schedule_file_index_upsert(background_tasks: BackgroundTasks, file: File, user_id: str) -> None:
    """Fire-and-forget vector index upsert after a content change; never blocks the write."""
    try:
        extra_metadata = _file_metadata(file)
        if file.parent_id:
            extra_metadata = {**extra_metadata, "parent_id": file.parent_id}

        from services.llama_index import schedule_index_upsert

        background_tasks.add_task(
            schedule_index_upsert,
            project_id=file.project_id,
            entity_type=file.file_type,
            entity_id=file.id,
            title=file.title,
            content=file.content or "",
            extra_metadata=extra_metadata,
            user_id=user_id,
        )
    except Exception:
        log_with_context(
            logger,
            logging.DEBUG,
            "Failed to schedule vector index upsert for updated file",
            file_id=file.id,
            project_id=file.project_id,
        )


# ==================== Project Endpoints ====================


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(
    _rate_limit: int = Depends(require_agent_rate_limit("agent_read", 2000, 3600)),
    context: AgentAuthContext = Depends(require_scope("read")),
):
    """
    Get all projects accessible by the API key.

    Requires scope: read
    """
    session, user_id, api_key = context

    # Get all projects owned by the user
    stmt = select(Project).where(
        Project.owner_id == user_id,
        Project.is_deleted.is_(False),
    )
    # Filter by API key project access if restricted
    if api_key.project_ids is not None:
        if not api_key.project_ids:
            projects: list[Project] = []
        else:
            stmt = stmt.where(Project.id.in_(api_key.project_ids))
            projects = session.exec(stmt).all()
    else:
        projects = session.exec(stmt).all()

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Listed projects",
        user_id=user_id,
        api_key_id=api_key.id,
        project_count=len(projects),
    )

    return projects


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: str,
    _rate_limit: int = Depends(require_agent_rate_limit("agent_read", 2000, 3600)),
    context: AgentAuthContext = Depends(require_project_access("read")),
):
    """
    Get project details.

    Requires scope: read
    Requires project access
    """
    session, user_id, api_key = context

    project = session.get(Project, project_id)
    if not project or project.is_deleted:
        raise APIException(
            error_code=ErrorCode.PROJECT_NOT_FOUND,
            status_code=404,
        )

    return project


@router.post("/projects", response_model=ProjectCreateResponse)
def create_project(
    project_data: ProjectCreate,
    accept_language: str | None = Header(None, alias="Accept-Language"),
    _rate_limit: int = Depends(require_agent_rate_limit("agent_write", 1000, 3600)),
    context: AgentAuthContext = Depends(require_scope("write")),
):
    """
    Create a new project with the same default folders as the web app.

    Requires scope: write

    Folder titles follow Accept-Language (zh default, en supported); folder ids are
    `{project_id}-{kind}-folder`, e.g. `{project_id}-draft-folder`.

    402 QUOTA_PROJECTS_EXCEEDED when the plan's project limit is reached; 403 when the key
    is restricted to specific projects (it could not access the new project).
    """
    session, user_id, api_key = context

    if api_key.project_ids is not None:
        # The key could not read or write the project it creates.
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=403,
            detail=(
                "This API key is limited to specific projects and cannot create new projects. "
                "Use a key without a project restriction, or create the project in the web app."
            ),
        )

    project = Project(
        name=project_data.name,
        description=project_data.description,
        project_type=project_data.project_type,
        owner_id=user_id,
    )
    try:
        # Also enforces the plan's project limit (402) and records project_created, like the web.
        folders = create_project_with_default_folders(
            session, project, resolve_template_lang(accept_language)
        )
    except APIException:
        raise
    except Exception:
        logger.exception("Agent API: failed to create project with default folders")
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            detail="Failed to initialize project structure.",
        ) from None

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Created project",
        user_id=user_id,
        api_key_id=api_key.id,
        project_id=project.id,
        folder_count=len(folders),
    )

    return ProjectCreateResponse(
        **ProjectResponse.model_validate(project).model_dump(),
        folders=[FolderResponse.model_validate(f) for f in folders],
    )


@router.put("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    project_data: ProjectUpdate,
    _rate_limit: int = Depends(require_agent_rate_limit("agent_write", 1000, 3600)),
    context: AgentAuthContext = Depends(require_project_access("write")),
):
    """
    Update project details.

    Requires scope: write
    Requires project access
    """
    session, user_id, api_key = context

    project = session.get(Project, project_id)
    if not project or project.is_deleted:
        raise APIException(
            error_code=ErrorCode.PROJECT_NOT_FOUND,
            status_code=404,
        )

    if project_data.name is not None:
        project.name = project_data.name
    if project_data.description is not None:
        project.description = project_data.description

    project.updated_at = utcnow()
    session.commit()
    session.refresh(project)

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Updated project",
        user_id=user_id,
        api_key_id=api_key.id,
        project_id=project_id,
    )

    return project


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    _rate_limit: int = Depends(require_agent_rate_limit("agent_write", 1000, 3600)),
    context: AgentAuthContext = Depends(require_project_access("write")),
):
    """
    Delete a project (soft delete).

    Requires scope: write
    Requires project access
    """
    session, user_id, api_key = context

    project = session.get(Project, project_id)
    if not project or project.is_deleted:
        raise APIException(
            error_code=ErrorCode.PROJECT_NOT_FOUND,
            status_code=404,
        )

    project.is_deleted = True
    project.deleted_at = utcnow()
    session.commit()

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Deleted project",
        user_id=user_id,
        api_key_id=api_key.id,
        project_id=project_id,
    )

    return {"message": "Project deleted successfully"}


# ==================== File Endpoints ====================


@router.get("/projects/{project_id}/files", response_model=FileListResponse)
def list_files(
    project_id: str,
    file_type: str | None = Query(None, description="Filter by file type"),
    parent_id: str | None = Query(None, description="Filter by parent ID"),
    fields: str | None = Query(None, description="Comma-separated fields to return (e.g. 'id,title,content')"),
    limit: int = Query(50, ge=1, le=200, description="Max results per page"),
    offset: int = Query(0, ge=0, description="Results offset"),
    _rate_limit: int = Depends(require_agent_rate_limit("agent_read", 2000, 3600)),
    context: AgentAuthContext = Depends(require_project_access("read")),
):
    """
    Get files in a project (paginated).

    Requires scope: read
    Requires project access

    Query parameters:
    - file_type: Filter by file type (outline, draft, character, lore, etc.)
    - parent_id: Filter by parent folder ID
    - fields: Comma-separated fields to return (omit for all fields)
    - limit: Max results (1-200, default 50)
    - offset: Pagination offset (default 0)
    """
    session, user_id, api_key = context

    # Parse requested fields
    requested_fields = None
    if fields:
        requested_fields = {f.strip() for f in fields.split(",")}
        requested_fields &= FileResponse.VALID_FIELDS

    # Build column list for load_only based on requested fields
    include_content = requested_fields is None or "content" in requested_fields
    columns = [
        File.id,
        File.project_id,
        File.title,
        File.file_type,
        File.parent_id,
        File.order,
        File.file_metadata,
        File.created_at,
        File.updated_at,
    ]
    if include_content:
        columns.append(File.content)

    # Count query (no pagination, SQL COUNT only)
    count_stmt = select(func.count(File.id)).where(
        File.project_id == project_id,
        File.is_deleted.is_(False),
    )
    if file_type:
        count_stmt = count_stmt.where(File.file_type == file_type)
    if parent_id is not None:
        count_stmt = count_stmt.where(File.parent_id == parent_id)

    total = session.exec(count_stmt).one()

    # Data query with pagination
    query = (
        select(File)
        .options(load_only(*columns))
        .where(
            File.project_id == project_id,
            File.is_deleted.is_(False),
        )
    )

    if file_type:
        query = query.where(File.file_type == file_type)

    if parent_id is not None:
        query = query.where(File.parent_id == parent_id)

    # File.id is the final tiebreaker so offset pagination is deterministic.
    query = query.order_by(File.order.asc(), col(File.created_at).desc(), File.id.asc())
    query = query.offset(offset).limit(limit)

    files = session.exec(query).all()

    file_responses = [_serialize_file_response(file, requested_fields) for file in files]

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Listed files",
        user_id=user_id,
        api_key_id=api_key.id,
        project_id=project_id,
        file_count=len(file_responses),
        total=total,
        file_type_filter=file_type,
    )

    return FileListResponse(
        files=file_responses,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/projects/{project_id}/files", response_model=FileWriteResponse)
def create_file(
    project_id: str,
    file_data: FileCreate,
    background_tasks: BackgroundTasks,
    _rate_limit: int = Depends(require_agent_rate_limit("agent_write", 1000, 3600)),
    context: AgentAuthContext = Depends(require_project_access("write")),
):
    """
    Create a new file in a project.

    Requires scope: write
    Requires project access
    """
    session, user_id, api_key = context

    project = lock_project_for_files(session, project_id)
    if not project or project.is_deleted or project.owner_id != user_id:
        raise APIException(error_code=ErrorCode.PROJECT_NOT_FOUND, status_code=404)
    parent_id = _validate_parent(session, project_id, file_data.parent_id, refresh_parent=True)

    # Serialize metadata
    metadata_str = json.dumps(file_data.metadata) if file_data.metadata else None

    try:
        resolved_order = resolve_new_file_order(
            session,
            project_id,
            parent_id,
            title=file_data.title,
            metadata=file_data.metadata,
            file_type=file_data.file_type,
            requested_order=file_data.order,
        )
    except ValueError as exc:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR, status_code=400, message=str(exc),
        ) from exc

    file = File(
        project_id=project_id,
        title=file_data.title,
        content=file_data.content,
        file_type=file_data.file_type,
        parent_id=parent_id,
        order=resolved_order,
        file_metadata=metadata_str,
    )

    session.add(file)
    session.flush()
    version_quota_exceeded = False
    if file.content:
        # Version 1 = the created content, so the first later edit can be rolled back to it.
        version_quota_exceeded = _snapshot_agent_file_content(
            session, file, user_id, api_key.id,
            change_type=CHANGE_TYPE_CREATE, change_summary="Created via Agent API",
        )
    session.commit()
    session.refresh(file)

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Created file",
        user_id=user_id,
        api_key_id=api_key.id,
        project_id=project_id,
        file_id=file.id,
        file_type=file_data.file_type,
    )

    # Fire-and-forget vector index upsert
    try:
        from services.llama_index import schedule_index_upsert

        extra_metadata = file_data.metadata or {}
        if file.parent_id:
            extra_metadata = {**extra_metadata, "parent_id": file.parent_id}

        background_tasks.add_task(
            schedule_index_upsert,
            project_id=project_id,
            entity_type=file.file_type,
            entity_id=file.id,
            title=file.title,
            content=file.content or "",
            extra_metadata=extra_metadata,
            user_id=user_id,
        )
    except Exception:
        log_with_context(
            logger,
            logging.DEBUG,
            "Failed to schedule vector index upsert for created file",
            project_id=project_id,
            file_id=file.id,
        )

    response = FileWriteResponse.model_validate(file)
    response.version_quota_exceeded = version_quota_exceeded
    return response


@router.get("/files/{file_id}", response_model=_FilteredFileResponse)
def get_file(
    file_id: str,
    fields: str | None = Query(None, description="Comma-separated fields to return (e.g. 'id,title,content')"),
    _rate_limit: int = Depends(require_agent_rate_limit("agent_read", 2000, 3600)),
    context: AgentAuthContext = Depends(require_scope("read")),
):
    """
    Get file details.

    Requires scope: read
    Requires project access (via file ownership)

    Query parameters:
    - fields: Comma-separated fields to return (omit for all fields)
    """
    session, user_id, api_key = context

    # Parse requested fields for load_only optimization
    requested_fields = None
    if fields:
        requested_fields = {f.strip() for f in fields.split(",")}
        requested_fields &= FileResponse.VALID_FIELDS

    include_content = requested_fields is None or "content" in requested_fields
    columns = [
        File.id, File.project_id, File.title, File.file_type,
        File.parent_id, File.order, File.file_metadata,
        File.created_at, File.updated_at,
    ]
    if include_content:
        columns.append(File.content)

    file = session.exec(
        select(File).options(load_only(*columns)).where(File.id == file_id)
    ).first()
    if not file or file.is_deleted:
        raise APIException(
            error_code=ErrorCode.FILE_NOT_FOUND,
            status_code=404,
        )

    # Verify project access
    project = session.get(Project, file.project_id)
    if not project or project.owner_id != user_id or project.is_deleted:
        raise APIException(
            error_code=ErrorCode.FILE_NOT_FOUND,
            status_code=404,
        )

    if not verify_project_access(api_key, file.project_id):
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=403,
            detail="API Key does not have access to this project",
        )

    return _serialize_file_response(file, requested_fields)


def _snapshot_agent_file_content(
    session,
    file: File,
    user_id: str,
    api_key_id: str,
    *,
    change_type: str = CHANGE_TYPE_AI_EDIT,
    change_summary: str = "Updated via Agent API",
) -> bool:
    """Record a FileVersion for Agent API content (create or update), like the web PUT /files/{id}.

    Same contract as api/files.py update_file: the version is attributed to the user
    (quota_source=user, so an API key cannot bypass the per-file version quota), it is
    flushed inside a savepoint of the caller's transaction. Only an expected version
    quota overflow permits an unsnapshotted content save; unexpected failure aborts it.

    Returns:
        True when no version was recorded because the per-file version quota is full
        (reported to the caller as `version_quota_exceeded`).
    """
    try:
        with session.begin_nested():
            get_file_version_service().create_version(
                session=session,
                file_id=file.id,
                new_content=file.content,
                change_type=change_type,
                change_source=CHANGE_SOURCE_USER,
                change_summary=change_summary,
                user_id=user_id,
                quota_source=CHANGE_SOURCE_USER,
                commit=False,
            )
    except Exception as e:
        if isinstance(e, APIException) and e.error_code == ErrorCode.QUOTA_FILE_VERSIONS_EXCEEDED:
            log_with_context(
                logger,
                logging.INFO,
                "Agent API: version quota reached, saved file content without a version snapshot",
                file_id=file.id,
                api_key_id=api_key_id,
                operation=f"agent_file_{change_type}_create_version",
            )
            return True
        log_with_context(
            logger,
            logging.WARNING,
            "Agent API: version snapshot failed; rolling back file write",
            error=str(e),
            file_id=file.id,
            api_key_id=api_key_id,
            operation=f"agent_file_{change_type}_create_version",
        )
        session.rollback()
        raise
    return False


@router.put("/files/{file_id}", response_model=FileWriteResponse)
def update_file(
    file_id: str,
    file_data: FileUpdate,
    background_tasks: BackgroundTasks,
    _rate_limit: int = Depends(require_agent_rate_limit("agent_write", 1000, 3600)),
    context: AgentAuthContext = Depends(require_scope("write")),
):
    """
    Update file content, title and/or sort order.

    Requires scope: write
    Requires project access (via file ownership)
    """
    session, user_id, api_key = context

    from agent.tools.file_ops.edit import file_write_lock
    from database import is_postgres

    # Serialize with web/editor/tool writes and refresh after taking the lock.
    # This sync endpoint keeps DB IO and SQLite lock waits off the event loop.
    lock_ctx = contextlib.nullcontext() if is_postgres else file_write_lock(file_id)
    with lock_ctx:
        file = _load_accessible_file(session, user_id, api_key, file_id, for_write=True)
        current_stamp = normalize_datetime_to_utc(file.updated_at)
        if file_data.base_updated_at is not None:
            expected = normalize_datetime_to_utc(file_data.base_updated_at)
            if expected != current_stamp:
                raise APIException(
                    error_code=ErrorCode.RESOURCE_CONFLICT,
                    status_code=409,
                    detail={
                        "reason": "stale_write",
                        "file_id": file.id,
                        "current_updated_at": current_stamp.isoformat(),
                        "base_updated_at": expected.isoformat(),
                    },
                )

        prospective_title = file_data.title if file_data.title is not None else file.title
        prospective_content = file_data.content if file_data.content is not None else file.content
        content_changed = file_data.content is not None and file_data.content != file.content
        prospective_order = file.order
        if file_data.order is not None or file_data.title is not None:
            try:
                prospective_order = resolve_persisted_sequence_order(
                    file_data.order if file_data.order is not None else file.order,
                    title=prospective_title,
                    metadata=_file_metadata(file),
                    file_type=file.file_type,
                )
            except ValueError as exc:
                raise APIException(
                    error_code=ErrorCode.VALIDATION_ERROR, status_code=400, message=str(exc),
                ) from exc

        file.title = prospective_title
        file.content = prospective_content
        file.order = prospective_order

        file.updated_at = advance_timestamp(current_stamp, now=utcnow())

        version_quota_exceeded = False
        if content_changed:
            version_quota_exceeded = _snapshot_agent_file_content(session, file, user_id, api_key.id)

        session.commit()
        session.refresh(file)

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Updated file",
        user_id=user_id,
        api_key_id=api_key.id,
        file_id=file_id,
        project_id=file.project_id,
    )

    _schedule_file_index_upsert(background_tasks, file, user_id)

    response = FileWriteResponse.model_validate(file)
    response.version_quota_exceeded = version_quota_exceeded
    return response


@router.delete("/files/{file_id}")
def delete_file(
    file_id: str,
    background_tasks: BackgroundTasks,
    _rate_limit: int = Depends(require_agent_rate_limit("agent_write", 1000, 3600)),
    context: AgentAuthContext = Depends(require_scope("write")),
):
    """
    Delete a file (soft delete).

    Requires scope: write
    Requires project access (via file ownership)
    """
    session, user_id, api_key = context

    file = _load_accessible_file(session, user_id, api_key, file_id)
    lock_project_for_files(session, file.project_id, exclusive=True)
    from agent.tools.file_ops.edit import file_write_lock
    from database import is_postgres

    lock_ctx = contextlib.nullcontext() if is_postgres else file_write_lock(file_id)
    with lock_ctx:
        file = _load_accessible_file(session, user_id, api_key, file_id, for_write=True)

        # Soft delete
        file.is_deleted = True
        file.deleted_at = utcnow()
        file.updated_at = advance_timestamp(file.updated_at, now=utcnow())

        session.commit()

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Deleted file",
        user_id=user_id,
        api_key_id=api_key.id,
        file_id=file_id,
        project_id=file.project_id,
    )

    # Fire-and-forget vector index delete
    try:
        from services.llama_index import schedule_index_delete

        background_tasks.add_task(
            schedule_index_delete,
            project_id=file.project_id,
            entity_type=file.file_type,
            entity_id=file.id,
            user_id=user_id,
        )
    except Exception:
        log_with_context(
            logger,
            logging.DEBUG,
            "Failed to schedule vector index delete for deleted file",
            file_id=file_id,
            project_id=file.project_id,
        )

    return {"message": "File deleted successfully"}


@router.post("/files/{file_id}/move", response_model=FileResponse)
def move_file(
    file_id: str,
    move_data: FileMove,
    background_tasks: BackgroundTasks,
    _rate_limit: int = Depends(require_agent_rate_limit("agent_write", 1000, 3600)),
    context: AgentAuthContext = Depends(require_scope("write")),
):
    """
    Move a file into a folder of the same project (parent_id=null: project root).

    Requires scope: write
    Requires project access (via file ownership)

    The target must be a folder in the same project; a folder cannot be moved under
    itself or one of its descendants. Optional `order` sets the new sibling order.
    """
    session, user_id, api_key = context

    file = _load_accessible_file(session, user_id, api_key, file_id)
    lock_project_for_files(session, file.project_id, exclusive=True)
    from agent.tools.file_ops.edit import file_write_lock
    from database import is_postgres

    lock_ctx = contextlib.nullcontext() if is_postgres else file_write_lock(file_id)
    with lock_ctx:
        file = _load_accessible_file(session, user_id, api_key, file_id, for_write=True)
        prospective_parent_id = _validate_parent(
            session, file.project_id, move_data.parent_id,
            moving_file_id=file.id, refresh_parent=True,
        )
        prospective_order = file.order
        if move_data.order is not None:
            try:
                prospective_order = resolve_persisted_sequence_order(
                    move_data.order,
                    title=file.title,
                    metadata=_file_metadata(file),
                    file_type=file.file_type,
                )
            except ValueError as exc:
                raise APIException(
                    error_code=ErrorCode.VALIDATION_ERROR, status_code=400, message=str(exc),
                ) from exc
        file.parent_id = prospective_parent_id
        file.order = prospective_order
        file.updated_at = advance_timestamp(file.updated_at, now=utcnow())

        session.commit()
        session.refresh(file)

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Moved file",
        user_id=user_id,
        api_key_id=api_key.id,
        file_id=file_id,
        project_id=file.project_id,
        new_parent_id=file.parent_id,
    )

    # The index stores parent_id as metadata; keep it in sync with the new location.
    _schedule_file_index_upsert(background_tasks, file, user_id)

    return file


# ==================== Version Endpoints ====================


@router.get("/files/{file_id}/versions", response_model=FileVersionListResponse)
def list_file_versions(
    file_id: str,
    limit: int = Query(50, ge=1, le=100, description="Max results per page"),
    offset: int = Query(0, ge=0, description="Results offset"),
    include_auto_save: bool = Query(False, description="Include auto-save versions"),
    _rate_limit: int = Depends(require_agent_rate_limit("agent_read", 2000, 3600)),
    context: AgentAuthContext = Depends(require_scope("read")),
):
    """
    List a file's version history, newest first (metadata only, no content).

    Requires scope: read
    Requires project access (via file ownership)
    """
    session, user_id, api_key = context

    file = _load_accessible_file(session, user_id, api_key, file_id)
    service = get_file_version_service()
    versions = service.get_versions(
        session=session,
        file_id=file_id,
        limit=limit,
        offset=offset,
        include_auto_save=include_auto_save,
    )
    total = service.get_version_count(session, file_id, include_auto_save=include_auto_save)

    return FileVersionListResponse(
        versions=[FileVersionResponse.model_validate(v) for v in versions],
        total=total,
        limit=limit,
        offset=offset,
        file_id=file_id,
        file_title=file.title,
    )


def _require_version(session, file_id: str, version_number: int) -> FileVersion:
    version = get_file_version_service().get_version_by_number(session, file_id, version_number)
    if not version:
        raise APIException(
            error_code=ErrorCode.VERSION_NOT_FOUND,
            status_code=404,
            detail=f"Version {version_number} not found for this file",
        )
    return version


@router.get("/files/{file_id}/versions/{version_number}", response_model=FileVersionDetailResponse)
def get_file_version(
    file_id: str,
    version_number: int,
    _rate_limit: int = Depends(require_agent_rate_limit("agent_read", 2000, 3600)),
    context: AgentAuthContext = Depends(require_scope("read")),
):
    """
    Get one version of a file, including its full content.

    Requires scope: read
    Requires project access (via file ownership)
    """
    session, user_id, api_key = context

    _load_accessible_file(session, user_id, api_key, file_id)
    version = _require_version(session, file_id, version_number)
    content = get_file_version_service().get_content_at_version(session, file_id, version_number)

    return FileVersionDetailResponse(
        **FileVersionResponse.model_validate(version).model_dump(),
        content=content,
    )


@router.post("/files/{file_id}/versions/{version_number}/rollback", response_model=FileRollbackResponse)
def rollback_file_version(
    file_id: str,
    version_number: int,
    background_tasks: BackgroundTasks,
    _rate_limit: int = Depends(require_agent_rate_limit("agent_write", 1000, 3600)),
    context: AgentAuthContext = Depends(require_scope("write")),
):
    """
    Restore a file's content to an earlier version.

    Requires scope: write
    Requires project access (via file ownership)

    Same semantics as the web rollback: history is kept, the restored content becomes a new
    version (change_type=restore) when the per-file version quota allows it; the content is
    restored even when the quota is full (version_quota_exceeded=true, new_version_number=null).
    """
    session, user_id, api_key = context

    _load_accessible_file(session, user_id, api_key, file_id)
    _require_version(session, file_id, version_number)

    try:
        file, new_version, version_quota_exceeded = get_file_version_service().rollback_to_version(
            session,
            file_id,
            version_number,
            user_id=user_id,
        )
    except ValueError as e:
        raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=400, detail=str(e)) from e

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Rolled back file",
        user_id=user_id,
        api_key_id=api_key.id,
        file_id=file_id,
        project_id=file.project_id,
        restored_version=version_number,
        new_version_number=new_version.version_number if new_version is not None else None,
    )

    _schedule_file_index_upsert(background_tasks, file, user_id)

    return FileRollbackResponse(
        success=True,
        message=f"Successfully rolled back to version {version_number}",
        file_id=file_id,
        restored_version=version_number,
        new_version_number=new_version.version_number if new_version is not None else None,
        snapshot_created=new_version is not None,
        version_quota_exceeded=version_quota_exceeded,
        updated_at=file.updated_at,
    )


# ==================== Writing Context Endpoint ====================

MAX_CONTENT_SNIPPET = 500
MAX_PAYLOAD_BYTES = 50 * 1024  # 50KB
WRITING_CONTEXT_TIMEOUT_SECONDS = 10.0
WRITING_CONTEXT_WORKERS = 4

# Assembly can outlive the HTTP timeout because Python cannot stop a running
# thread. A dedicated pool plus an equal-size nonblocking gate bounds that work
# to four active calls without consuming asyncio's shared default executor or
# accepting an unbounded queue.
_writing_context_executor = ThreadPoolExecutor(
    max_workers=WRITING_CONTEXT_WORKERS,
    thread_name_prefix="writing-context",
)
_writing_context_capacity = threading.BoundedSemaphore(WRITING_CONTEXT_WORKERS)


def _submit_writing_context_work(work):
    gate = _writing_context_capacity
    if not gate.acquire(blocking=False):
        raise APIException(
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            status_code=503,
            detail="Writing context capacity is full",
        )

    try:
        request_context = contextvars.copy_context()
        future = _writing_context_executor.submit(request_context.run, work)
    except BaseException:
        gate.release()
        raise

    future.add_done_callback(lambda _future, acquired_gate=gate: acquired_gate.release())
    return future


@router.get("/projects/{project_id}/writing-context")
async def get_writing_context(
    project_id: str,
    file_id: str | None = Query(None, description="Focus file ID"),
    query: str | None = Query(None, description="Query for context retrieval"),
    max_items: int = Query(10, ge=1, le=30, description="Max items to return"),
    _rate_limit: int = Depends(require_agent_rate_limit("agent_context", 500, 3600)),
    context: AgentAuthContext = Depends(require_project_access("read")),
):
    """
    Get assembled writing context for a project.

    Requires scope: read
    Requires project access

    Returns structured context items (relevant chapters, characters, lore)
    assembled by the AI context engine. This is the primary differentiated
    endpoint — external agents get AI-curated writing context, not raw files.
    """
    session, user_id, api_key = context

    assembler = ContextAssembler()

    def _assemble_in_thread():
        thread_session = create_session()
        try:
            return assembler.assemble(
                session=thread_session,
                project_id=project_id,
                user_id=user_id,
                query=query,
                focus_file_id=file_id,
            )
        finally:
            thread_session.close()

    future = _submit_writing_context_work(_assemble_in_thread)
    try:
        context_data = await asyncio.wait_for(
            asyncio.wrap_future(future),
            timeout=WRITING_CONTEXT_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        log_with_context(
            logger,
            logging.WARNING,
            "Agent API: Writing context timeout",
            project_id=project_id,
            user_id=user_id,
        )
        raise APIException(
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            status_code=504,
            detail="Writing context assembly timed out",
        ) from None

    # Build response with content_snippet cap
    items = []
    payload_size = 0
    for item in context_data.items[:max_items]:
        snippet = item.get("content", "")
        if snippet and len(snippet) > MAX_CONTENT_SNIPPET:
            snippet = snippet[:MAX_CONTENT_SNIPPET] + "..."

        entry = {
            "type": item.get("type", ""),
            "title": item.get("title", ""),
            "content_snippet": snippet,
            "source_file_id": item.get("id", ""),
            "relevance": item.get("relevance_score", 0),
        }

        entry_size = len(json.dumps(entry, ensure_ascii=False))
        if payload_size + entry_size > MAX_PAYLOAD_BYTES:
            break

        items.append(entry)
        payload_size += entry_size

    log_with_context(
        logger,
        logging.INFO,
        "Agent API: Writing context",
        user_id=user_id,
        api_key_id=api_key.id,
        project_id=project_id,
        items_returned=len(items),
        total_available=len(context_data.items),
    )

    return {
        "items": items,
        "refs": context_data.refs,
        "total_available": len(context_data.items),
        "returned": len(items),
        "token_estimate": context_data.token_estimate,
    }
