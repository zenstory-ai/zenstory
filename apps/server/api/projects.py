"""
Project management API endpoints
"""
import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlmodel import Session, col, func, select

from api.chat import MessageResponse, SessionResponse, load_active_session_recent_messages
from api.files import FileResponse, build_project_file_tree, serialize_file_tree_array_json
from config.datetime_utils import normalize_datetime_to_utc, utcnow
from config.project_notes import without_legacy_proposals
from config.project_status import (
    PROJECT_STATUS_MAX_LENGTHS,
    PROJECT_TITLE_AUTHOR_TOOL,
    PROJECT_TITLE_LEDGER_ACTION,
    PROJECT_TITLE_SOURCE_AUTHOR,
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
from core.i18n import resolve_user_language
from core.project_access import verify_project_ownership
from database import get_session
from models import (
    AgentArtifactLedger,
    File,
    Project,
    User,
)
from services.core.auth_service import get_current_active_user
from services.project_progress import get_projects_progress
from services.project_service import (
    create_project_with_default_folders,
    project_metadata_write_lock,
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
    # Optional exact draft origin; omitted by legacy unconditional callers.
    expected_notes: str | None = None


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

    # Resolve language from Accept-Language header, falling back to stored account ui_language
    lang = resolve_user_language(session, current_user.id, accept_language)

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
    Unsupported header values fall back to English; omitted header defaults to Chinese.
    """
    lang = resolve_template_lang(accept_language)
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


class ProjectBootstrapResponse(BaseModel):
    """Aggregated initial workbench state for a single project."""

    project: Project
    files: list[dict[str, Any]]
    initial_file: FileResponse | None = None
    chat_session: SessionResponse | None = None
    recent_messages: list[MessageResponse] = Field(default_factory=list)
    has_more_messages: bool = False


def _resolve_bootstrap_initial_file(
    session: Session,
    project_id: str,
    file_id: str | None,
) -> FileResponse | None:
    """Resolve an explicitly requested editable file in this project (None if missing, deleted, folder, or foreign),
    or when omitted, the most recently updated editable file."""
    if file_id is not None:
        if not file_id.strip():
            return None
        target = session.exec(
            select(File).where(
                File.id == file_id,
                File.project_id == project_id,
                File.is_deleted.is_(False),
                File.file_type != "folder",
            )
        ).first()
    else:
        target = session.exec(
            select(File)
            .where(
                File.project_id == project_id,
                File.is_deleted.is_(False),
                File.file_type != "folder",
            )
            .order_by(
                col(File.updated_at).desc(),
                col(File.created_at).desc(),
                col(File.id).desc(),
            )
            .limit(1)
        ).first()

    return FileResponse.model_validate(target) if target is not None else None


@router.get("/projects/{project_id}", response_model=Project)
def get_project(
    project_id: str,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Get a specific project."""
    project = verify_project_ownership(project_id, current_user, session)
    return project


def _serialize_project_bootstrap_json(
    *,
    project: Project,
    files: list[dict[str, Any]],
    initial_file: FileResponse | None,
    chat_session: SessionResponse | None,
    recent_messages: list[MessageResponse],
    has_more_messages: bool,
) -> str:
    """Encode ProjectBootstrapResponse without recursing through nested file-tree children."""
    shell = ProjectBootstrapResponse(
        project=project,
        files=[],
        initial_file=initial_file,
        chat_session=chat_session,
        recent_messages=recent_messages,
        has_more_messages=has_more_messages,
    )
    payload = shell.model_dump(mode="json")
    files_json = serialize_file_tree_array_json(files)

    def _dumps_compact(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=None,
            separators=(",", ":"),
        )

    return (
        f'{{"project":{_dumps_compact(payload["project"])},'
        f'"files":{files_json},'
        f'"initial_file":{_dumps_compact(payload["initial_file"])},'
        f'"chat_session":{_dumps_compact(payload["chat_session"])},'
        f'"recent_messages":{_dumps_compact(payload["recent_messages"])},'
        f'"has_more_messages":{_dumps_compact(payload["has_more_messages"])}}}'
    )


@router.get("/projects/{project_id}/bootstrap", response_model=ProjectBootstrapResponse)
def get_project_bootstrap(
    project_id: str,
    file_id: str | None = Query(None, description="Optional preferred editable file ID"),
    chat_limit: int = Query(15, ge=1, le=50, description="Number of recent chat messages to include"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """Return initial project metadata, file tree (without file bodies), active file, and recent chat in one round-trip."""
    project = verify_project_ownership(project_id, current_user, session)
    files = build_project_file_tree(session, project_id, include_content=False)
    initial_file = _resolve_bootstrap_initial_file(session, project_id, file_id)
    chat_session, recent_messages, has_more_messages = load_active_session_recent_messages(
        session,
        project_id=project_id,
        user_id=current_user.id,
        limit=chat_limit,
    )

    return Response(
        content=_serialize_project_bootstrap_json(
            project=project,
            files=files,
            initial_file=initial_file,
            chat_session=chat_session,
            recent_messages=recent_messages,
            has_more_messages=has_more_messages,
        ),
        media_type="application/json",
    )


@router.put("/projects/{project_id}", response_model=Project)
def update_project(
    project_id: str,
    request: UpdateProjectRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """Update a project."""
    with project_metadata_write_lock(project_id):
        verify_project_ownership(project_id, current_user, session)
        session.exec(
            select(Project).where(Project.id == project_id).with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        db_project = verify_project_ownership(project_id, current_user, session)

        project_data = request.model_dump(exclude_unset=True)
        for key, value in project_data.items():
            setattr(db_project, key, value)

        if "name" in project_data:
            # An explicit human title choice is durable provenance, including same-name
            # saves and returning to a name once proposed by AI.
            session.add(AgentArtifactLedger(
                project_id=project_id,
                user_id=current_user.id,
                action=PROJECT_TITLE_LEDGER_ACTION,
                tool_name=PROJECT_TITLE_AUTHOR_TOOL,
                artifact_ref=f"project:{project_id}",
                payload=json.dumps({"project_name": db_project.name, "source": PROJECT_TITLE_SOURCE_AUTHOR}, ensure_ascii=False),
            ))
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
    - expected_notes: Optional notes baseline, checked only when notes is written
    """
    with project_metadata_write_lock(project_id):
        verify_project_ownership(project_id, current_user, session)
        session.exec(
            select(Project).where(Project.id == project_id).with_for_update()
            .execution_options(populate_existing=True)
        ).one()
        project = verify_project_ownership(project_id, current_user, session)

        # Update only provided fields
        update_data = request.model_dump(exclude_unset=True)
        expected_notes_provided = "expected_notes" in update_data
        expected_notes = update_data.pop("expected_notes", None)
        # Compare before normalizing or mutating any field. Empty and NULL both
        # mean no notes, while all other draft-origin text is compared verbatim. Clients
        # never see a legacy AI-proposal block, so it is left out of both sides.
        if "notes" in update_data and expected_notes_provided and (
            without_legacy_proposals(expected_notes) or ""
        ) != (without_legacy_proposals(project.notes) or ""):
            raise APIException(
                error_code=ErrorCode.RESOURCE_CONFLICT,
                status_code=409,
                detail="Project notes have changed. Review the latest notes before saving this draft.",
            )
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
        if "notes" in normalized_status:
            # A legacy AI-proposal block is never written back, even by an old client.
            update_data["notes"] = without_legacy_proposals(normalized_status["notes"])

        for key, value in update_data.items():
            setattr(project, key, value)

        project.updated_at = utcnow()
        session.add(project)
        session.commit()
        session.refresh(project)

        return project
