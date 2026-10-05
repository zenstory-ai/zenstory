"""
Skills API endpoints.

Provides FastAPI router for skill management:
- GET /skills - List user's skills (custom + added from public)
- POST /skills - Create user skill
- PUT /skills/{id} - Update user skill
- DELETE /skills/{id} - Delete user skill
- POST /skills/import - Import a standard skill package (.zip) or SKILL.md
- GET /skills/{id}/export - Export a skill as a standard skill package
- GET/PUT/DELETE /skills/{id}/resources - Manage skill resource files
"""

import asyncio
import json
from collections import Counter
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response
from fastapi.routing import APIRoute
from pydantic import BaseModel, BeforeValidator, Field
from services.auth import get_current_active_user
from sqlmodel import Session, select

from agent.skills.package import (
    MAX_DESCRIPTION_CHARS,
    MAX_INSTRUCTIONS_CHARS,
    MAX_NAME_CHARS,
    MAX_TRIGGER_CHARS,
    MAX_TRIGGERS,
    SkillPackageError,
    strip_nul_chars,
)
from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from core.permissions import require_quota
from database import get_session
from models import PublicSkill, User, UserAddedSkill, UserSkill
from services import skill_package_service
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/skills", tags=["Skills"])


# ==================== Request/Response Models ====================


def _safe_json_array(value: str | None, *, field_name: str, record_id: str) -> list[str]:
    """Parse JSON array payload safely and return [] on malformed data."""
    if not value:
        return []

    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        log_with_context(
            logger,
            30,
            "Invalid JSON field in skill record",
            field=field_name,
            record_id=record_id,
        )
        return []

    if not isinstance(parsed, list):
        log_with_context(
            logger,
            30,
            "JSON field is not an array in skill record",
            field=field_name,
            record_id=record_id,
        )
        return []

    return [str(item) for item in parsed if item is not None]


class SkillResponse(BaseModel):
    """Response model for a skill."""

    id: str
    name: str
    description: str | None
    triggers: list[str]
    instructions: str
    source: str  # "builtin", "user", or "added"
    is_active: bool = True
    resource_count: int = 0
    # 自建技能分享到公共库后的审核状态：pending / approved / unpublished；未分享为 None
    share_status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class SkillListResponse(BaseModel):
    """Response model for skill list."""

    skills: list[SkillResponse]
    total: int


def _strip_nul(value: Any) -> Any:
    """PostgreSQL 文本列不接受 NUL：在长度校验之前清掉，避免写库时 500。"""
    return strip_nul_chars(value) if isinstance(value, str) else value


# 长度限额与导入（agent/skills/package.py）共用同一组常量，手工创建/编辑与导入口径一致。
NulFreeStr = Annotated[str, BeforeValidator(_strip_nul)]
SkillName = Annotated[NulFreeStr, Field(min_length=1, max_length=MAX_NAME_CHARS)]
SkillDescription = Annotated[NulFreeStr, Field(max_length=MAX_DESCRIPTION_CHARS)]
SkillTriggers = Annotated[
    list[Annotated[NulFreeStr, Field(max_length=MAX_TRIGGER_CHARS)]],
    Field(max_length=MAX_TRIGGERS),
]
SkillInstructions = Annotated[NulFreeStr, Field(min_length=1, max_length=MAX_INSTRUCTIONS_CHARS)]
# 公共库分类白名单（与前端 ShareSkillModal 的选项一致）；任意字符串会进入公开分类列表，
# 超过 PublicSkill.category 的 VARCHAR(50) 还会在 PostgreSQL 上 500。
ShareCategory = Literal["writing", "character", "worldbuilding", "plot", "style"]


class CreateSkillRequest(BaseModel):
    """Request model for creating a skill."""

    name: SkillName
    description: SkillDescription | None = None
    triggers: SkillTriggers
    instructions: SkillInstructions


class UpdateSkillRequest(BaseModel):
    """Request model for updating a skill."""

    name: SkillName | None = None
    description: SkillDescription | None = None
    triggers: SkillTriggers | None = None
    instructions: SkillInstructions | None = None
    is_active: bool | None = None


# ==================== Endpoints ====================


@router.get("", response_model=SkillListResponse)
async def list_skills(
    search: str | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillListResponse:
    """List all skills for current user (custom + added from public).

    Args:
        search: Optional search query to filter skills by name or description (case-insensitive)
    """
    skills: list[SkillResponse] = []
    search_pattern = f"%{search}%" if search else None

    # Add user's custom skills
    stmt = select(UserSkill).where(UserSkill.user_id == current_user.id)
    if search_pattern:
        stmt = stmt.where(
            (UserSkill.name.ilike(search_pattern)) |
            (UserSkill.description.ilike(search_pattern))
        )
    user_skills = session.exec(stmt).all()

    # Add user's added public skills
    added_stmt = select(UserAddedSkill, PublicSkill).join(
        PublicSkill, UserAddedSkill.public_skill_id == PublicSkill.id
    ).where(
        UserAddedSkill.user_id == current_user.id,
        UserAddedSkill.is_active,
        # 与 load_active_skills 口径一致：下架/驳回的公共技能不再出现在列表里
        PublicSkill.status == "approved",
    )
    if search_pattern:
        added_stmt = added_stmt.where(
            (PublicSkill.name.ilike(search_pattern)) |
            (PublicSkill.description.ilike(search_pattern))
        )
    added_results = session.exec(added_stmt).all()

    user_resource_counts, public_resource_counts = skill_package_service.count_resources(
        session,
        user_skill_ids=[db_skill.id for db_skill in user_skills],
        public_skill_ids=[public.id for _added, public in added_results],
    )
    share_statuses = skill_package_service.get_share_statuses(session, user_skills)

    for db_skill in user_skills:
        triggers = _safe_json_array(
            db_skill.triggers,
            field_name="user_skill.triggers",
            record_id=db_skill.id,
        )
        skills.append(SkillResponse(
            id=db_skill.id,
            name=db_skill.name,
            description=db_skill.description,
            triggers=triggers,
            instructions=db_skill.instructions,
            source="user",
            is_active=db_skill.is_active,
            resource_count=user_resource_counts.get(db_skill.id, 0),
            share_status=share_statuses.get(db_skill.id),
            created_at=db_skill.created_at,
            updated_at=db_skill.updated_at,
        ))

    for added, public in added_results:
        # Parse tags as triggers
        triggers = _safe_json_array(
            public.tags,
            field_name="public_skill.tags",
            record_id=public.id,
        )
        skills.append(SkillResponse(
            id=added.id,
            name=added.custom_name or public.name,
            description=public.description,
            triggers=triggers,
            instructions=public.instructions,
            source="added",
            is_active=added.is_active,
            resource_count=public_resource_counts.get(public.id, 0),
        ))

    log_with_context(logger, 20, "Skills listed", user_id=current_user.id, count=len(skills))
    return SkillListResponse(skills=skills, total=len(skills))


@router.post("", response_model=SkillResponse)
@require_quota("skill_create")
async def create_skill(
    request: CreateSkillRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillResponse:
    """Create a new user skill."""
    db_skill = UserSkill(
        user_id=current_user.id,
        name=request.name,
        description=(request.description or "").strip() or None,
        triggers=json.dumps(request.triggers),
        instructions=request.instructions,
    )
    session.add(db_skill)
    session.commit()
    session.refresh(db_skill)

    log_with_context(logger, 20, "Skill created", user_id=current_user.id, skill_id=db_skill.id)
    return SkillResponse(
        id=db_skill.id,
        name=db_skill.name,
        description=db_skill.description,
        triggers=request.triggers,
        instructions=db_skill.instructions,
        source="user",
        is_active=db_skill.is_active,
        created_at=db_skill.created_at,
        updated_at=db_skill.updated_at,
    )


@router.put("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: str,
    request: UpdateSkillRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillResponse:
    """Update a user skill."""
    stmt = select(UserSkill).where(
        UserSkill.id == skill_id,
        UserSkill.user_id == current_user.id,
    )
    db_skill = session.exec(stmt).first()

    if not db_skill:
        raise APIException(
            status_code=404,
            error_code=ErrorCode.NOT_FOUND,
            detail="Skill not found",
        )

    # Update fields
    if request.name is not None:
        db_skill.name = request.name
    if request.description is not None:
        # 空字符串表示清空描述（前端编辑时清空输入框会送 ""）
        db_skill.description = request.description.strip() or None
    if request.triggers is not None:
        db_skill.triggers = json.dumps(request.triggers)
    if request.instructions is not None:
        db_skill.instructions = request.instructions
    if request.is_active is not None:
        db_skill.is_active = request.is_active

    db_skill.updated_at = utcnow()
    session.add(db_skill)
    session.commit()
    session.refresh(db_skill)

    triggers = _safe_json_array(
        db_skill.triggers,
        field_name="user_skill.triggers",
        record_id=db_skill.id,
    )
    user_resource_counts, _ = skill_package_service.count_resources(
        session, user_skill_ids=[db_skill.id]
    )
    log_with_context(logger, 20, "Skill updated", skill_id=skill_id)
    return SkillResponse(
        id=db_skill.id,
        name=db_skill.name,
        description=db_skill.description,
        triggers=triggers,
        instructions=db_skill.instructions,
        source="user",
        is_active=db_skill.is_active,
        resource_count=user_resource_counts.get(db_skill.id, 0),
        created_at=db_skill.created_at,
        updated_at=db_skill.updated_at,
    )


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Delete a user skill."""
    stmt = select(UserSkill).where(
        UserSkill.id == skill_id,
        UserSkill.user_id == current_user.id,
    )
    db_skill = session.exec(stmt).first()

    if not db_skill:
        raise APIException(
            status_code=404,
            error_code=ErrorCode.NOT_FOUND,
            detail="Skill not found",
        )

    skill_package_service.delete_user_skill_resources(session, db_skill.id)
    session.delete(db_skill)
    session.commit()

    log_with_context(logger, 20, "Skill deleted", skill_id=skill_id)
    return {"success": True, "message": "Skill deleted"}


# ==================== Statistics Endpoints ====================


class SkillStatsResponse(BaseModel):
    """Response model for skill usage statistics."""

    total_triggers: int
    builtin_count: int
    user_count: int
    top_skills: list[dict]
    daily_usage: list[dict]


@router.get("/stats/{project_id}", response_model=SkillStatsResponse)
async def get_skill_stats(
    project_id: str,
    days: int = 30,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillStatsResponse:
    """
    Get skill usage statistics for a project.

    Args:
        project_id: Project ID
        days: Number of days to look back (default 30, max 90)
    """
    from models import Project
    from services.skill_usage_service import get_skill_usage_stats

    # Verify project ownership
    project = session.get(Project, project_id)
    # Admin/superuser can access stats for any project for support/debugging.
    if (
        not project
        or project.is_deleted
        or (project.owner_id != current_user.id and not current_user.is_superuser)
    ):
        raise APIException(
            status_code=403,
            error_code=ErrorCode.NOT_AUTHORIZED,
            detail="Not authorized to access this project",
        )

    # Limit days to reasonable range
    days = min(max(days, 1), 90)

    stats = get_skill_usage_stats(session, project_id, days)

    log_with_context(
        logger, 20, "Skill stats retrieved",
        project_id=project_id,
        days=days,
        total_triggers=stats["total_triggers"],
    )

    return SkillStatsResponse(**stats)


# ==================== My Skills Endpoints ====================


class AddedSkillResponse(BaseModel):
    """Response model for an added public skill."""
    id: str
    public_skill_id: str
    name: str
    description: str | None
    instructions: str
    category: str
    source: str  # "added"
    is_active: bool
    added_at: datetime


class MySkillsResponse(BaseModel):
    """Response model for my skills list."""
    user_skills: list[SkillResponse]
    added_skills: list[AddedSkillResponse]
    total: int


@router.get("/my-skills", response_model=MySkillsResponse)
async def get_my_skills(
    search: str | None = None,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> MySkillsResponse:
    """Get all skills for current user (custom + added from public).

    Args:
        search: Optional search query to filter skills by name or description (case-insensitive)
    """
    search_pattern = f"%{search}%" if search else None

    # Get user's custom skills
    user_stmt = select(UserSkill).where(UserSkill.user_id == current_user.id)
    if search_pattern:
        user_stmt = user_stmt.where(
            (UserSkill.name.ilike(search_pattern)) |
            (UserSkill.description.ilike(search_pattern))
        )
    user_skills_db = session.exec(user_stmt).all()
    user_resource_counts, _ = skill_package_service.count_resources(
        session, user_skill_ids=[db_skill.id for db_skill in user_skills_db]
    )
    share_statuses = skill_package_service.get_share_statuses(session, user_skills_db)

    user_skills = []
    for db_skill in user_skills_db:
        triggers = _safe_json_array(
            db_skill.triggers,
            field_name="user_skill.triggers",
            record_id=db_skill.id,
        )
        user_skills.append(SkillResponse(
            id=db_skill.id,
            name=db_skill.name,
            description=db_skill.description,
            triggers=triggers,
            instructions=db_skill.instructions,
            source="user",
            is_active=db_skill.is_active,
            resource_count=user_resource_counts.get(db_skill.id, 0),
            share_status=share_statuses.get(db_skill.id),
            created_at=db_skill.created_at,
            updated_at=db_skill.updated_at,
        ))

    # Get user's added public skills
    added_stmt = select(UserAddedSkill, PublicSkill).join(
        PublicSkill, UserAddedSkill.public_skill_id == PublicSkill.id
    ).where(
        UserAddedSkill.user_id == current_user.id,
        UserAddedSkill.is_active,
        # 与 load_active_skills 口径一致：下架/驳回的公共技能不再出现在列表里
        PublicSkill.status == "approved",
    )
    if search_pattern:
        added_stmt = added_stmt.where(
            (PublicSkill.name.ilike(search_pattern)) |
            (PublicSkill.description.ilike(search_pattern))
        )
    added_results = session.exec(added_stmt).all()

    added_skills = []
    for added, public in added_results:
        added_skills.append(AddedSkillResponse(
            id=added.id,
            public_skill_id=public.id,
            name=added.custom_name or public.name,
            description=public.description,
            instructions=public.instructions,
            category=public.category,
            source="added",
            is_active=added.is_active,
            added_at=added.added_at,
        ))

    total = len(user_skills) + len(added_skills)
    log_with_context(logger, 20, "My skills listed", user_id=current_user.id, total=total)

    return MySkillsResponse(
        user_skills=user_skills,
        added_skills=added_skills,
        total=total,
    )


# ==================== Share Skill Endpoints ====================


class ShareSkillRequest(BaseModel):
    """Request model for sharing a skill."""
    category: ShareCategory = "writing"


class ShareSkillResponse(BaseModel):
    """Response model for sharing a skill."""
    success: bool
    message: str
    public_skill_id: str | None = None


@router.post("/{skill_id}/share", response_model=ShareSkillResponse)
async def share_skill(
    skill_id: str,
    request: ShareSkillRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> ShareSkillResponse:
    """Share a user skill to the public library (requires admin approval)."""
    # Find the user skill
    stmt = select(UserSkill).where(
        UserSkill.id == skill_id,
        UserSkill.user_id == current_user.id,
    )
    user_skill = session.exec(stmt).first()

    if not user_skill:
        raise APIException(
            status_code=404,
            error_code=ErrorCode.NOT_FOUND,
            detail="Skill not found",
        )

    if user_skill.is_shared:
        existing_public = (
            session.get(PublicSkill, user_skill.shared_skill_id)
            if user_skill.shared_skill_id
            else None
        )

        # Allow resubmission when previous shared skill was rejected or link is stale.
        if existing_public is None or existing_public.status == "rejected":
            user_skill.is_shared = False
            user_skill.shared_skill_id = None
            user_skill.updated_at = utcnow()
            session.add(user_skill)
        else:
            return ShareSkillResponse(
                success=False,
                message="Skill already shared",
                public_skill_id=user_skill.shared_skill_id,
            )

    # Create public skill (pending approval)
    public_skill = PublicSkill(
        name=user_skill.name,
        description=user_skill.description,
        instructions=user_skill.instructions,
        category=request.category,
        tags=json.dumps(_safe_json_array(
            user_skill.triggers,
            field_name="user_skill.triggers",
            record_id=user_skill.id,
        )),
        skill_metadata=user_skill.skill_metadata or "{}",
        source="community",
        author_id=current_user.id,
        status="pending",
    )
    try:
        session.add(public_skill)
        session.flush()
        skill_package_service.copy_resources_to_public_skill(
            session, user_skill.id, public_skill.id
        )

        # Stage the source-skill link in the same transaction as the public copy.
        user_skill.is_shared = True
        user_skill.shared_skill_id = public_skill.id
        user_skill.updated_at = utcnow()
        session.add(user_skill)
        session.commit()
        session.refresh(public_skill)
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger, 20, "Skill shared",
        user_id=current_user.id,
        skill_id=skill_id,
        public_skill_id=public_skill.id,
    )

    return ShareSkillResponse(
        success=True,
        message="Skill submitted for review",
        public_skill_id=public_skill.id,
    )


# ==================== Batch Operations Endpoints ====================


class BatchUpdateRequest(BaseModel):
    """Request model for batch skill operations."""
    skill_ids: list[str]
    action: str  # "enable" | "disable" | "delete"


class BatchUpdateResponse(BaseModel):
    """Response model for batch skill operations."""
    success: bool
    updated_count: int
    message: str


@router.post("/batch-update", response_model=BatchUpdateResponse)
async def batch_update_skills(
    request: BatchUpdateRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> BatchUpdateResponse:
    """Batch update skills (enable/disable/delete).

    Args:
        request: BatchUpdateRequest with skill_ids and action

    Actions:
        - enable: Set is_active=True for all specified skills
        - disable: Set is_active=False for all specified skills
        - delete: Delete all specified skills
    """
    if request.action not in ("enable", "disable", "delete"):
        raise APIException(
            status_code=400,
            error_code=ErrorCode.VALIDATION_ERROR,
            detail="Invalid action. Must be 'enable', 'disable', or 'delete'",
        )

    if not request.skill_ids:
        return BatchUpdateResponse(
            success=True,
            updated_count=0,
            message="No skills to update",
        )

    updated_count = 0

    # Process user skills
    user_stmt = select(UserSkill).where(
        UserSkill.id.in_(request.skill_ids),
        UserSkill.user_id == current_user.id,
    )
    user_skills = session.exec(user_stmt).all()

    for skill in user_skills:
        if request.action == "delete":
            skill_package_service.delete_user_skill_resources(session, skill.id)
            session.delete(skill)
        else:
            skill.is_active = request.action == "enable"
            skill.updated_at = utcnow()
            session.add(skill)
        updated_count += 1

    # Process added public skills
    added_stmt = select(UserAddedSkill).where(
        UserAddedSkill.id.in_(request.skill_ids),
        UserAddedSkill.user_id == current_user.id,
    )
    added_skills = session.exec(added_stmt).all()

    deleted_public_skill_ids: list[str] = []
    for added in added_skills:
        if request.action == "delete":
            deleted_public_skill_ids.append(added.public_skill_id)
            session.delete(added)
        else:
            added.is_active = request.action == "enable"
            session.add(added)
        updated_count += 1

    # Keep add_count consistent when added-skill links are removed in batch.
    if request.action == "delete" and deleted_public_skill_ids:
        public_skill_counts = Counter(deleted_public_skill_ids)
        public_skills = session.exec(
            select(PublicSkill).where(PublicSkill.id.in_(list(public_skill_counts.keys())))
        ).all()
        for public_skill in public_skills:
            dec = public_skill_counts.get(public_skill.id, 0)
            if dec > 0 and public_skill.add_count > 0:
                public_skill.add_count = max(public_skill.add_count - dec, 0)
                session.add(public_skill)

    session.commit()

    action_past = {
        "enable": "enabled",
        "disable": "disabled",
        "delete": "deleted",
    }[request.action]

    log_with_context(
        logger, 20, f"Skills batch {action_past}",
        user_id=current_user.id,
        action=request.action,
        count=updated_count,
    )

    return BatchUpdateResponse(
        success=True,
        updated_count=updated_count,
        message=f"Successfully {action_past} {updated_count} skill(s)",
    )


# ==================== Skill Package Endpoints ====================


class ImportSkillResponse(BaseModel):
    """Response model for importing a skill package."""

    skill: SkillResponse
    warnings: list[str]


class SkillResourceInfo(BaseModel):
    """Metadata of a skill resource file."""

    path: str
    size: int
    updated_at: datetime | None = None


class SkillResourceListResponse(BaseModel):
    """Response model for listing skill resources."""

    resources: list[SkillResourceInfo]


class SkillResourceContentResponse(BaseModel):
    """Response model for a skill resource's content."""

    path: str
    content: str


class UpsertSkillResourceRequest(BaseModel):
    """Request model for creating/replacing a skill resource."""

    path: str
    content: NulFreeStr


class _ImportUploadRoute(APIRoute):
    """
    按 Content-Length 在解析 multipart 之前拒收超大上传。

    FastAPI 在跑依赖和端点之前就会把整个表单读完（文件落到临时文件），端点里再判断
    已经晚了；所以这一检查放在路由处理器外层。没有 Content-Length（分块传输）时
    仍由端点里的读取上限兜底。
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original_handler = super().get_route_handler()

        async def handler(request: Request) -> Response:
            content_length = request.headers.get("content-length")
            if (
                content_length
                and content_length.strip().isdigit()
                and int(content_length) > skill_package_service.MAX_IMPORT_REQUEST_BYTES
            ):
                raise skill_package_service.package_error_to_api(
                    SkillPackageError(
                        f"技能包不能超过 {skill_package_service.MAX_ZIP_BYTES // 1024 // 1024} MiB",
                        kind="too_large",
                    )
                )
            return await original_handler(request)

        return handler


@require_quota("skill_create")
async def import_skill(
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> ImportSkillResponse:
    """Import a standard skill package (.zip) or a single SKILL.md (.md).

    Scripts and non-text files are never stored; they are reported in `warnings`.
    """
    try:
        skill_package_service.ensure_upload_size(file.size)
    except SkillPackageError as exc:
        raise skill_package_service.package_error_to_api(exc) from exc
    data = await file.read(skill_package_service.MAX_UPLOAD_READ_BYTES)
    # 解压、YAML 解析、校验都是纯 CPU 工作，放到线程池里跑，不阻塞事件循环；
    # 落库只有几条 INSERT，沿用本文件其他端点的做法留在请求 session 上。
    parsed = await asyncio.to_thread(
        skill_package_service.parse_skill_upload, file.filename, data
    )
    db_skill, warnings = skill_package_service.import_skill_package(
        session, current_user.id, file.filename, data, parsed=parsed
    )
    user_resource_counts, _ = skill_package_service.count_resources(
        session, user_skill_ids=[db_skill.id]
    )
    return ImportSkillResponse(
        skill=SkillResponse(
            id=db_skill.id,
            name=db_skill.name,
            description=db_skill.description,
            triggers=_safe_json_array(
                db_skill.triggers,
                field_name="user_skill.triggers",
                record_id=db_skill.id,
            ),
            instructions=db_skill.instructions,
            source="user",
            is_active=db_skill.is_active,
            resource_count=user_resource_counts.get(db_skill.id, 0),
            created_at=db_skill.created_at,
            updated_at=db_skill.updated_at,
        ),
        warnings=warnings,
    )


router.add_api_route(
    "/import",
    import_skill,
    methods=["POST"],
    response_model=ImportSkillResponse,
    route_class_override=_ImportUploadRoute,
)


@router.get("/{skill_id}/export")
async def export_skill(
    skill_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> Response:
    """Export an own or added skill as a standard skill package (zip attachment)."""
    filename, data = skill_package_service.export_skill_package(session, current_user.id, skill_id)
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/{skill_id}/resources", response_model=SkillResourceListResponse)
async def list_skill_resources(
    skill_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillResourceListResponse:
    """List resource files of an own or added skill."""
    resources = skill_package_service.list_resources(session, current_user.id, skill_id)
    return SkillResourceListResponse(
        resources=[
            SkillResourceInfo(path=item.path, size=item.size, updated_at=item.updated_at)
            for item in resources
        ]
    )


@router.get("/{skill_id}/resources/content", response_model=SkillResourceContentResponse)
async def get_skill_resource_content(
    skill_id: str,
    path: str = Query(..., description="Resource path, e.g. references/style.md"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillResourceContentResponse:
    """Get the content of one resource file of an own or added skill."""
    resource = skill_package_service.get_resource(session, current_user.id, skill_id, path)
    return SkillResourceContentResponse(path=resource.path, content=resource.content)


@router.put("/{skill_id}/resources", response_model=SkillResourceInfo)
async def upsert_skill_resource(
    skill_id: str,
    request: UpsertSkillResourceRequest,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> SkillResourceInfo:
    """Create or replace a resource file of an own skill."""
    resource = skill_package_service.upsert_resource(
        session, current_user.id, skill_id, request.path, request.content
    )
    return SkillResourceInfo(path=resource.path, size=resource.size, updated_at=resource.updated_at)


@router.delete("/{skill_id}/resources")
async def delete_skill_resource(
    skill_id: str,
    path: str = Query(..., description="Resource path, e.g. references/style.md"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Delete a resource file of an own skill."""
    skill_package_service.delete_resource(session, current_user.id, skill_id, path)
    return {"success": True, "message": "Skill resource deleted"}
