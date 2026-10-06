"""
Public Skills API endpoints.

Provides FastAPI router for public skill discovery:
- GET /public-skills - List public skills (with filtering)
- GET /public-skills/{id} - Get skill details
- GET /public-skills/categories - Get category list
- POST /public-skills/{id}/add - Add skill to user's collection
- DELETE /public-skills/{id}/remove - Remove from user's collection
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from services.auth import get_current_active_user
from sqlalchemy import case, delete, update
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, and_, col, func, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import PublicSkill, User, UserAddedSkill
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

router = APIRouter(prefix="/api/v1/public-skills", tags=["Public Skills"])


# ==================== Response Models ====================


# 列表接口只回传正文开头的预览（每页 20 个技能 × 最长 5 万字的正文太重）；
# 完整正文用 GET /public-skills/{id} 取。
LIST_INSTRUCTIONS_PREVIEW_CHARS = 500


class PublicSkillResponse(BaseModel):
    """Response model for a public skill."""
    id: str
    name: str
    description: str | None
    # 详情接口是完整正文；列表接口是开头的预览，被截断时 instructions_truncated 为 True
    instructions: str
    instructions_truncated: bool = False
    category: str
    tags: list[str]
    source: str
    author_id: str | None
    author_name: str | None = None
    status: str
    add_count: int
    created_at: datetime
    is_added: bool = False  # Whether current user has added this skill


class PublicSkillListResponse(BaseModel):
    """Response model for public skill list."""
    skills: list[PublicSkillResponse]
    total: int
    page: int
    page_size: int


class CategoryResponse(BaseModel):
    """Response model for category."""
    name: str
    count: int


class CategoryListResponse(BaseModel):
    """Response model for category list."""
    categories: list[CategoryResponse]


class AddSkillResponse(BaseModel):
    """Response model for adding a skill."""
    success: bool
    message: str
    added_skill_id: str | None = None


# ==================== Helper Functions ====================


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
            "Invalid JSON field in public skill record",
            field=field_name,
            record_id=record_id,
        )
        return []

    if not isinstance(parsed, list):
        log_with_context(
            logger,
            30,
            "JSON field is not an array in public skill record",
            field=field_name,
            record_id=record_id,
        )
        return []

    return [str(item) for item in parsed if item is not None]


def _skill_to_response(
    skill: PublicSkill,
    is_added: bool = False,
    author_name: str | None = None,
    preview_chars: int | None = None,
) -> PublicSkillResponse:
    """Convert PublicSkill model to response (preview_chars: 只回传正文开头这么多字符)."""
    tags = _safe_json_array(
        skill.tags,
        field_name="public_skill.tags",
        record_id=skill.id,
    )
    instructions = skill.instructions or ""
    truncated = preview_chars is not None and len(instructions) > preview_chars
    return PublicSkillResponse(
        id=skill.id,
        name=skill.name,
        description=skill.description,
        instructions=instructions[:preview_chars] if truncated else instructions,
        instructions_truncated=truncated,
        category=skill.category,
        tags=tags,
        source=skill.source,
        author_id=skill.author_id,
        author_name=author_name,
        status=skill.status,
        add_count=skill.add_count,
        created_at=skill.created_at,
        is_added=is_added,
    )


def _get_author_name_map(session: Session, skills: list[PublicSkill]) -> dict[str, str]:
    """Build author_id -> username map for a skill list."""
    author_ids = {skill.author_id for skill in skills if skill.author_id}
    if not author_ids:
        return {}

    stmt = select(User.id, User.username).where(User.id.in_(author_ids))
    return dict(session.exec(stmt).all())


# ==================== Endpoints ====================


@router.get("/categories", response_model=CategoryListResponse)
async def get_categories(
    session: Session = Depends(get_session),
    _current_user: User = Depends(get_current_active_user),
) -> CategoryListResponse:
    """Get list of skill categories with counts."""
    stmt = (
        select(PublicSkill.category, func.count(PublicSkill.id).label("count"))
        .where(PublicSkill.status == "approved")
        .group_by(PublicSkill.category)
        .order_by(func.count(PublicSkill.id).desc())
    )
    results = session.exec(stmt).all()

    categories = [
        CategoryResponse(name=row[0], count=row[1])
        for row in results
    ]

    return CategoryListResponse(categories=categories)


@router.get("", response_model=PublicSkillListResponse)
async def list_public_skills(
    category: str | None = Query(None, description="Filter by category"),
    source: str | None = Query(None, description="Filter by source (official/community)"),
    search: str | None = Query(None, description="Search in name and description"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> PublicSkillListResponse:
    """List public skills with filtering and pagination."""
    # Build base query for approved skills
    conditions = [PublicSkill.status == "approved"]

    if category:
        conditions.append(PublicSkill.category == category)
    if source:
        conditions.append(PublicSkill.source == source)
    if search:
        search_pattern = f"%{search}%"
        conditions.append(
            (PublicSkill.name.ilike(search_pattern)) |
            (PublicSkill.description.ilike(search_pattern))
        )

    # Count total
    count_stmt = select(func.count(PublicSkill.id)).where(and_(*conditions))
    total = session.exec(count_stmt).one()

    # Get paginated results
    offset = (page - 1) * page_size
    stmt = (
        select(PublicSkill)
        .where(and_(*conditions))
        .order_by(PublicSkill.add_count.desc(), PublicSkill.created_at.desc())
        .offset(offset)
        .limit(page_size)
    )
    skills = session.exec(stmt).all()
    author_name_map = _get_author_name_map(session, skills)

    # Get user's added skill IDs
    added_stmt = select(UserAddedSkill.public_skill_id).where(
        UserAddedSkill.user_id == current_user.id,
        UserAddedSkill.is_active,
    )
    added_ids = set(session.exec(added_stmt).all())

    # Convert to response
    skill_responses = [
        _skill_to_response(
            skill,
            is_added=skill.id in added_ids,
            author_name=author_name_map.get(skill.author_id),
            preview_chars=LIST_INSTRUCTIONS_PREVIEW_CHARS,
        )
        for skill in skills
    ]

    log_with_context(
        logger, 20, "Public skills listed",
        user_id=current_user.id,
        total=total,
        page=page,
        filters={"category": category, "source": source, "search": search}
    )

    return PublicSkillListResponse(
        skills=skill_responses,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{skill_id}", response_model=PublicSkillResponse)
async def get_public_skill(
    skill_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> PublicSkillResponse:
    """Get a public skill by ID."""
    skill = session.get(PublicSkill, skill_id)

    if not skill or skill.status != "approved":
        raise APIException(
            status_code=404,
            error_code=ErrorCode.NOT_FOUND,
            detail="Skill not found",
        )

    # Check if user has added this skill
    added_stmt = select(UserAddedSkill).where(
        UserAddedSkill.user_id == current_user.id,
        UserAddedSkill.public_skill_id == skill_id,
        UserAddedSkill.is_active,
    )
    is_added = session.exec(added_stmt).first() is not None
    author_name = None
    if skill.author_id:
        author = session.get(User, skill.author_id)
        author_name = author.username if author else None

    return _skill_to_response(skill, is_added=is_added, author_name=author_name)


@router.post("/{skill_id}/add", response_model=AddSkillResponse)
async def add_skill_to_collection(
    skill_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> AddSkillResponse:
    """Add a public skill to user's collection."""
    # Check skill exists and is approved
    skill = session.get(PublicSkill, skill_id)
    if not skill or skill.status != "approved":
        raise APIException(
            status_code=404,
            error_code=ErrorCode.NOT_FOUND,
            detail="Skill not found",
        )

    # Check if already added
    existing_stmt = select(UserAddedSkill).where(
        UserAddedSkill.user_id == current_user.id,
        UserAddedSkill.public_skill_id == skill_id,
    )
    existing = session.exec(existing_stmt).first()

    if existing:
        if not existing.is_active:
            existing.is_active = True
            session.add(existing)
            session.commit()
            session.refresh(existing)

            log_with_context(
                logger, 20, "Skill re-enabled in collection",
                user_id=current_user.id,
                skill_id=skill_id,
                added_skill_id=existing.id,
            )
            return AddSkillResponse(
                success=True,
                message="Skill re-enabled successfully",
                added_skill_id=existing.id,
            )

        return AddSkillResponse(
            success=False,
            message="Skill already added",
            added_skill_id=existing.id,
        )

    # Add skill
    added_skill = UserAddedSkill(
        user_id=current_user.id,
        public_skill_id=skill_id,
    )
    session.add(added_skill)

    try:
        # Flush the unique collection link before counting it, in the same transaction.
        session.flush()
        session.execute(
            update(PublicSkill)
            .where(col(PublicSkill.id) == skill_id)
            .values(add_count=col(PublicSkill.add_count) + 1)
            .execution_options(synchronize_session=False)
        )
        session.commit()
    except IntegrityError:
        # Handle concurrent add requests idempotently
        session.rollback()
        existing = session.exec(existing_stmt).first()
        if existing:
            return AddSkillResponse(
                success=False,
                message="Skill already added",
                added_skill_id=existing.id,
            )
        raise

    session.refresh(added_skill)

    log_with_context(
        logger, 20, "Skill added to collection",
        user_id=current_user.id,
        skill_id=skill_id,
        skill_name=skill.name,
    )

    return AddSkillResponse(
        success=True,
        message="Skill added successfully",
        added_skill_id=added_skill.id,
    )


@router.delete("/{skill_id}/remove", response_model=AddSkillResponse)
async def remove_skill_from_collection(
    skill_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_active_user),
) -> AddSkillResponse:
    """Remove a public skill from user's collection."""
    # Find the added skill
    stmt = select(UserAddedSkill).where(
        UserAddedSkill.user_id == current_user.id,
        UserAddedSkill.public_skill_id == skill_id,
    )
    added_skill = session.exec(stmt).first()

    if not added_skill:
        raise APIException(
            status_code=404,
            error_code=ErrorCode.NOT_FOUND,
            detail="Skill not in your collection",
        )

    # A concurrent request may already have removed the observed link.
    deleted_id = session.execute(
        delete(UserAddedSkill)
        .where(col(UserAddedSkill.id) == added_skill.id, col(UserAddedSkill.user_id) == current_user.id)
        .returning(col(UserAddedSkill.id))
        .execution_options(synchronize_session="fetch")
    ).scalar_one_or_none()
    if deleted_id is not None:
        session.execute(
            update(PublicSkill)
            .where(col(PublicSkill.id) == skill_id)
            .values(add_count=case((col(PublicSkill.add_count) > 0, col(PublicSkill.add_count) - 1), else_=0))
            .execution_options(synchronize_session=False)
        )
    session.commit()

    log_with_context(
        logger, 20, "Skill removed from collection",
        user_id=current_user.id,
        skill_id=skill_id,
    )

    return AddSkillResponse(
        success=True,
        message="Skill removed successfully",
    )
