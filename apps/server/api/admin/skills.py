"""
Admin Skill Review Management API endpoints.

This module contains all skill review management endpoints for admin operations.
"""
import logging

from fastapi import APIRouter, Depends, Query, Request, status
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import PublicSkill, User, UserSkill
from services import skill_package_service
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from services.features.points_service import points_service
from utils.logger import get_logger, log_with_context

from .schemas import (
    PendingSkillResponse,
    SkillReviewRequest,
    SkillReviewResourceResponse,
    SkillReviewResourcesResponse,
    SkillReviewStatus,
)

logger = get_logger(__name__)

router = APIRouter(tags=["admin-skills"])


# ==================== Skill Review Management ====================


@router.get("/skills/pending", response_model=list[PendingSkillResponse])
def get_pending_skills(
    review_status: SkillReviewStatus = Query("pending", alias="status"),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get all pending skills awaiting review.

    Requires superuser privileges.
    """
    stmt = (
        select(PublicSkill)
        .where(PublicSkill.status == review_status)
        .order_by(PublicSkill.created_at.asc())
    )
    skills = session.exec(stmt).all()
    _, resource_counts = skill_package_service.count_resources(
        session, public_skill_ids=[skill.id for skill in skills]
    )

    result = []
    for skill in skills:
        author_name = None
        if skill.author_id:
            author = session.get(User, skill.author_id)
            author_name = author.username if author else None

        reviewer_name = None
        if skill.reviewed_by:
            reviewer = session.get(User, skill.reviewed_by)
            reviewer_name = reviewer.username if reviewer else None

        result.append(PendingSkillResponse(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            instructions=skill.instructions,
            category=skill.category,
            tags=skill_package_service.parse_json_str_list(skill.tags),
            skill_metadata=skill_package_service.parse_json_object(skill.skill_metadata),
            resource_count=resource_counts.get(skill.id, 0),
            source=skill.source,
            author_id=skill.author_id,
            author_name=author_name,
            status=skill.status,
            reviewed_by=skill.reviewed_by,
            reviewer_name=reviewer_name,
            reviewed_at=skill.reviewed_at,
            rejection_reason=skill.rejection_reason,
            created_at=skill.created_at,
        ))

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved pending skills",
        user_id=current_user.id,
        count=len(result),
    )

    return result


@router.get("/skills/{skill_id}/resources", response_model=SkillReviewResourcesResponse)
def get_skill_review_resources(
    skill_id: str,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get every resource file (raw content) of a public skill, in any review status.

    核准后这些文件会经 read_skill_resource 原文交给其他用户的 agent，审核时必须能看到。
    Requires superuser privileges.
    """
    if session.get(PublicSkill, skill_id) is None:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found",
        )
    resources = skill_package_service.list_public_skill_resources(session, skill_id)
    log_with_context(
        logger,
        logging.INFO,
        "Retrieved skill review resources",
        user_id=current_user.id,
        skill_id=skill_id,
        count=len(resources),
    )
    return SkillReviewResourcesResponse(
        resources=[
            SkillReviewResourceResponse(path=item.path, size=item.size, content=item.content)
            for item in resources
        ]
    )


@router.post("/skills/{skill_id}/approve")
def approve_skill(
    skill_id: str,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Approve a pending skill.

    Requires superuser privileges.
    """
    skill = session.exec(
        select(PublicSkill).where(PublicSkill.id == skill_id).with_for_update()
    ).first()
    if not skill:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found",
        )

    if skill.status != "pending":
        raise APIException(
            error_code=ErrorCode.RESOURCE_CONFLICT,
            status_code=status.HTTP_409_CONFLICT,
            detail="Skill is not pending review",
        )

    skill.status = "approved"
    skill.reviewed_by = current_user.id
    skill.reviewed_at = utcnow()
    skill.updated_at = utcnow()

    session.add(skill)
    try:
        # 社区投稿的贡献积分与核准同一事务提交；同一个公共技能只发一次。
        reward = None
        if skill.source == "community" and skill.author_id:
            reward = points_service.award_skill_contribution(session, skill.author_id, skill.id)
        admin_audit_service.log_action(
            session, current_user.id, "approve_skill", "skill", skill_id,
            old_value={"status": "pending"},
            new_value={
                "status": "approved",
                "reviewed_by": current_user.id,
                "points_awarded": reward.amount if reward else 0,
            },
            request=http_request, commit=False,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger,
        logging.INFO,
        "Approved skill",
        user_id=current_user.id,
        skill_id=skill_id,
        skill_name=skill.name,
    )

    return {"message": "Skill approved successfully", "skill_id": skill_id}


@router.post("/skills/{skill_id}/reject")
def reject_skill(
    skill_id: str,
    request: SkillReviewRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Reject a pending skill.

    Requires superuser privileges.
    """
    skill = session.exec(
        select(PublicSkill).where(PublicSkill.id == skill_id).with_for_update()
    ).first()
    if not skill:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found",
        )

    if skill.status != "pending":
        raise APIException(
            error_code=ErrorCode.RESOURCE_CONFLICT,
            status_code=status.HTTP_409_CONFLICT,
            detail="Skill is not pending review",
        )

    skill.status = "rejected"
    skill.reviewed_by = current_user.id
    skill.reviewed_at = utcnow()
    skill.rejection_reason = request.rejection_reason
    skill.updated_at = utcnow()

    # Reset original user skill sharing state so author can resubmit after edits.
    user_skills = session.exec(
        select(UserSkill).where(UserSkill.shared_skill_id == skill.id)
    ).all()
    for user_skill in user_skills:
        user_skill.is_shared = False
        user_skill.shared_skill_id = None
        user_skill.updated_at = utcnow()
        session.add(user_skill)

    session.add(skill)
    try:
        admin_audit_service.log_action(
            session, current_user.id, "reject_skill", "skill", skill_id,
            old_value={"status": "pending"},
            new_value={
                "status": "rejected",
                "reviewed_by": current_user.id,
                "rejection_reason": request.rejection_reason,
            },
            request=http_request, commit=False,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger,
        logging.INFO,
        "Rejected skill",
        user_id=current_user.id,
        skill_id=skill_id,
        skill_name=skill.name,
        reason=request.rejection_reason,
    )

    return {"message": "Skill rejected", "skill_id": skill_id}


@router.post("/skills/{skill_id}/unpublish")
def unpublish_skill(
    skill_id: str,
    request: SkillReviewRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Take an approved public skill down (status -> unpublished).

    下架后公共库列表、技能目录、load_skill 与已添加用户的技能列表都不再返回它
    （各处只认 approved）。作者的技能保持「已分享」链接，能看到「已下架」状态，
    不能直接重投同一个技能。已发放的贡献积分不收回。
    Requires superuser privileges.
    """
    skill = session.exec(
        select(PublicSkill).where(PublicSkill.id == skill_id).with_for_update()
    ).first()
    if not skill:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found",
        )

    if skill.status != "approved":
        raise APIException(
            error_code=ErrorCode.RESOURCE_CONFLICT,
            status_code=status.HTTP_409_CONFLICT,
            detail="Only approved skills can be unpublished",
        )

    skill.status = "unpublished"
    skill.reviewed_by = current_user.id
    skill.reviewed_at = utcnow()
    skill.rejection_reason = request.rejection_reason
    skill.updated_at = utcnow()

    session.add(skill)
    try:
        admin_audit_service.log_action(
            session, current_user.id, "unpublish_skill", "skill", skill_id,
            old_value={"status": "approved"},
            new_value={
                "status": "unpublished",
                "reviewed_by": current_user.id,
                "rejection_reason": request.rejection_reason,
            },
            request=http_request, commit=False,
        )
        session.commit()
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger,
        logging.INFO,
        "Unpublished skill",
        user_id=current_user.id,
        skill_id=skill_id,
        skill_name=skill.name,
        reason=request.rejection_reason,
    )

    return {"message": "Skill unpublished", "skill_id": skill_id}
