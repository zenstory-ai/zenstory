"""
Project management API endpoints
"""
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from services.auth import get_current_active_user
from sqlmodel import Session, col, func, select

from config.datetime_utils import normalize_datetime_to_utc, utcnow
from config.project_status import (
    PROJECT_STATUS_MAX_LENGTHS,
    normalize_project_status_payload,
)
from config.project_templates import (
    get_default_project_name,
)
from config.project_templates import (
    get_project_templates as get_project_templates_config,
)
from core.error_codes import ErrorCode
from core.error_handler import APIException
from core.project_access import verify_project_ownership
from database import get_session
from models import (
    File,
    Project,
    User,
)
from services.project_progress import get_projects_progress
from services.project_service import (
    create_project_with_default_folders,
    resolve_template_lang,
)
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["projects"])
ProjectTypeLiteral = Literal["novel", "short", "screenplay"]


# Request schemas
class CreateProjectRequest(BaseModel):
    """Request body for creating a project."""
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    description: str | None = None
    project_type: ProjectTypeLiteral = "novel"


class UpdateProjectRequest(BaseModel):
    """Request body for updating a project."""
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    description: str | None = None
    project_type: ProjectTypeLiteral | None = None

    @field_validator("name", "project_type", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("This field cannot be null")
        return value


class PatchProjectRequest(BaseModel):
    """Request body for patching project metadata (AI context fields)"""
    model_config = ConfigDict(extra="forbid")
    summary: str | None = Field(default=None, max_length=PROJECT_STATUS_MAX_LENGTHS["summary"])
    current_phase: str | None = Field(default=None, max_length=PROJECT_STATUS_MAX_LENGTHS["current_phase"])
    writing_style: str | None = Field(default=None, max_length=PROJECT_STATUS_MAX_LENGTHS["writing_style"])
    notes: str | None = Field(default=None, max_length=PROJECT_STATUS_MAX_LENGTHS["notes"])


# ==================== Project CRUD ====================
@router.get("/projects", response_model=list[Project])
def get_projects(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Get all projects for the current user (excluding soft-deleted).

    ``updated_at`` reports the project's last activity: the later of the
    project row's own timestamp and its newest non-deleted file edit. Editing
    a chapter does not touch the project row, so without this the dashboard
    card says "3 days ago" for a book written five minutes ago.
    """
    projects = session.exec(
        select(Project).where(
            Project.owner_id == current_user.id,
            Project.is_deleted.is_(False)
        )
    ).all()

    if projects:
        latest_file_edits = dict(
            session.exec(
                select(File.project_id, func.max(File.updated_at))
                .where(
                    col(File.project_id).in_([project.id for project in projects]),
                    File.is_deleted.is_(False),
                )
                .group_by(File.project_id)
            ).all()
        )
        activity_projects: list[Project] = []
        for project in projects:
            latest_file_edit = latest_file_edits.get(project.id)
            # 只改脱离 session 的副本：这是读接口，不能把活动时间写回 project 行。
            view = Project.model_validate(project.model_dump())
            if latest_file_edit is not None and normalize_datetime_to_utc(
                latest_file_edit
            ) > normalize_datetime_to_utc(project.updated_at):
                view.updated_at = latest_file_edit
            activity_projects.append(view)
        projects = activity_projects

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved user projects",
        user_id=current_user.id,
        project_count=len(projects),
    )

    return projects


@router.post("/projects", response_model=Project)
def create_project(
    request: CreateProjectRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
    accept_language: str | None = Header(None, alias="Accept-Language")
):
    """Create a new project with folders based on project type."""
    project = Project(
        name=request.name or "",
        description=request.description,
        owner_id=current_user.id,
        project_type=request.project_type,
    )

    # Parse language from Accept-Language header (default Chinese)
    lang = resolve_template_lang(accept_language)

    # If no name provided, use default project name in the selected language
    if not project.name:
        project.name = get_default_project_name(project.project_type, lang)

    log_with_context(
        logger,
        logging.INFO,
        "Creating new project",
        user_id=current_user.id,
        project_name=project.name,
        project_type=project.project_type,
        language=lang,
    )

    try:
        # Plan project limit + project row + template root folders + project_created
        # activation event, shared with the Agent API.
        folders = create_project_with_default_folders(session, project, lang)
    except APIException:
        raise
    except Exception:
        logger.exception("Failed to create project with initial folder structure")
        raise APIException(
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            status_code=500,
            detail="Failed to initialize project structure.",
        ) from None

    log_with_context(
        logger,
        logging.INFO,
        "Project created successfully",
        project_id=project.id,
        project_name=project.name,
        folder_count=len(folders),
    )

    # Re-fetch from database to ensure proper serialization
    db_project = session.get(Project, project.id)
    return db_project


@router.get("/project-templates")
def list_project_templates(
    accept_language: str | None = Header(None, alias="Accept-Language")
):
    """Get all available project templates.

    Supports language selection via Accept-Language header (e.g., 'zh' or 'en').
    Defaults to Chinese if not specified.
    """
    # Parse Accept-Language header (e.g., 'zh-CN,zh;q=0.9,en;q=0.8' -> 'zh')
    lang = "zh"  # Default to Chinese
    if accept_language:
        # Extract first language code
        lang = accept_language.split(",")[0].split("-")[0]

    return get_project_templates_config(lang)


class ProjectProgressItem(BaseModel):
    """一个作品的写作进度（作品列表卡片用）。"""

    project_id: str
    # 已写的章（长篇/短篇）或集（剧本）：内容非空的正文类文件数
    written_units: int
    word_count: int
    # 大纲/角色/设定已有、正文还没有
    framework_ready: bool


# 必须在 /projects/{project_id} 之前注册，否则 "progress" 会被当成作品 id。
@router.get("/projects/progress", response_model=list[ProjectProgressItem])
def list_projects_progress(
    project_id: str | None = Query(None, description="只看这一个作品"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """当前用户作品的写作进度：写了几章（集）、多少字，或框架已就绪还没开写（「写第一章」按钮也用它）。"""
    return [
        ProjectProgressItem(
            project_id=item.project_id,
            written_units=item.written_units,
            word_count=item.word_count,
            framework_ready=item.framework_ready,
        )
        for item in get_projects_progress(session, current_user.id, project_id)
    ]


@router.get("/projects/{project_id}", response_model=Project)
def get_project(
    project_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Get a specific project."""
    project = verify_project_ownership(project_id, current_user, session)
    return project


@router.put("/projects/{project_id}", response_model=Project)
def update_project(
    project_id: str,
    request: UpdateProjectRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Update a project."""
    db_project = verify_project_ownership(project_id, current_user, session)

    project_data = request.model_dump(exclude_unset=True)
    for key, value in project_data.items():
        setattr(db_project, key, value)

    db_project.updated_at = utcnow()
    session.add(db_project)
    session.commit()
    session.refresh(db_project)
    return db_project


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Delete a project (soft delete)."""
    project = verify_project_ownership(project_id, current_user, session)

    # Soft delete: mark as deleted instead of actual deletion
    project.is_deleted = True
    project.deleted_at = utcnow()
    session.add(project)
    session.commit()

    log_with_context(
        logger,
        logging.INFO,
        "Project deleted successfully",
        project_id=project_id,
        project_name=project.name,
        user_id=current_user.id,
    )

    return {"message": "Project deleted successfully"}


@router.patch("/projects/{project_id}", response_model=Project)
def patch_project(
    project_id: str,
    request: PatchProjectRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """
    Partially update a project's metadata.

    This endpoint is specifically designed for updating AI context fields:
    - summary: Project summary/background for AI understanding
    - current_phase: Current writing phase description
    - writing_style: Writing style guidelines for AI
    - notes: Additional notes for AI assistant
    """
    project = verify_project_ownership(project_id, current_user, session)

    # Update only provided fields
    update_data = request.model_dump(exclude_unset=True)
    try:
        normalized_status = normalize_project_status_payload(
            {
                "summary": update_data.get("summary"),
                "current_phase": update_data.get("current_phase"),
                "writing_style": update_data.get("writing_style"),
                "notes": update_data.get("notes"),
            }
        )
    except ValueError as exc:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=422,
            detail=str(exc),
        ) from exc

    for field_name, field_value in normalized_status.items():
        update_data[field_name] = field_value

    for key, value in update_data.items():
        setattr(project, key, value)

    project.updated_at = utcnow()
    session.add(project)
    session.commit()
    session.refresh(project)

    return project
