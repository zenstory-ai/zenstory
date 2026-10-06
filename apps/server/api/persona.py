"""Persona onboarding API endpoints (server-side persistence + recommendations)."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from services.auth import get_current_active_user
from sqlmodel import Session, select

from config.datetime_utils import utcnow
from config.feature_flags import is_inspirations_enabled
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User, UserPersonaProfile

router = APIRouter(prefix="/api/v1/persona", tags=["persona"])

PERSONA_ONBOARDING_ROLLOUT_AT_ENV = "PERSONA_ONBOARDING_ROLLOUT_AT"
PERSONA_ONBOARDING_WINDOW_DAYS_ENV = "PERSONA_ONBOARDING_NEW_USER_WINDOW_DAYS"
PERSONA_ONBOARDING_DEFAULT_ROLLOUT_AT = "2026-03-05T16:00:00Z"
PERSONA_ONBOARDING_DEFAULT_WINDOW_DAYS = 7
MAX_PERSONA_SELECTION = 3
ALLOWED_EXPERIENCE_LEVELS = {"beginner", "intermediate", "advanced"}
ALLOWED_PERSONA_IDS = {"explorer", "serial", "professional", "fanfic", "studio"}
ALLOWED_GOAL_IDS = {"finishBook", "buildHabit", "improveQuality", "growAudience", "monetize"}


class PersonaRecommendation(BaseModel):
    id: str
    title: str
    description: str
    action: str


class PersonaProfileRead(BaseModel):
    version: int
    completed_at: datetime
    selected_personas: list[str]
    selected_goals: list[str]
    experience_level: str
    skipped: bool


class PersonaOnboardingStateResponse(BaseModel):
    required: bool
    rollout_at: datetime
    new_user_window_days: int
    profile: PersonaProfileRead | None
    recommendations: list[PersonaRecommendation]


class PersonaOnboardingUpsertRequest(BaseModel):
    selected_personas: list[str] = Field(default_factory=list)
    selected_goals: list[str] = Field(default_factory=list)
    experience_level: str = "beginner"
    skipped: bool = False


class PersonaRecommendationsResponse(BaseModel):
    recommendations: list[PersonaRecommendation]


def _normalize_datetime_to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _get_rollout_datetime() -> datetime:
    raw = os.getenv(PERSONA_ONBOARDING_ROLLOUT_AT_ENV, PERSONA_ONBOARDING_DEFAULT_ROLLOUT_AT).strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        parsed = datetime.fromisoformat(PERSONA_ONBOARDING_DEFAULT_ROLLOUT_AT.replace("Z", "+00:00"))
    return _normalize_datetime_to_utc(parsed)


def _get_rollout_window_days() -> int:
    raw = (os.getenv(PERSONA_ONBOARDING_WINDOW_DAYS_ENV, str(PERSONA_ONBOARDING_DEFAULT_WINDOW_DAYS)) or "").strip()
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return PERSONA_ONBOARDING_DEFAULT_WINDOW_DAYS


def _decode_json_string_array(raw: str) -> list[str]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).strip() for item in parsed if isinstance(item, str) and str(item).strip()]


def _encode_string_array(values: list[str]) -> str:
    normalized = [item.strip() for item in values if item and item.strip()]
    deduped = list(dict.fromkeys(normalized))
    return json.dumps(deduped, ensure_ascii=False)


def _build_recommendations(
    *,
    selected_personas: list[str],
    selected_goals: list[str],
    experience_level: str,
) -> list[PersonaRecommendation]:
    recommendations: list[PersonaRecommendation] = []

    if "explorer" in selected_personas and is_inspirations_enabled():
        recommendations.append(
            PersonaRecommendation(
                id="persona_explorer_template",
                title="探索型模板推荐",
                description="优先给你开放灵感模板和快速起稿入口。",
                action="/dashboard/inspirations",
            )
        )
    if "serial" in selected_personas:
        recommendations.append(
            PersonaRecommendation(
                id="persona_serial_streak",
                title="连载节奏建议",
                description="设置每天写作目标并追踪连更进度。",
                action="/dashboard",
            )
        )
    if "professional" in selected_personas:
        recommendations.append(
            PersonaRecommendation(
                id="persona_professional_efficiency",
                title="效率工作流",
                description="突出大纲-章节-改稿的一键流转能力。",
                action="/dashboard/projects",
            )
        )
    if "fanfic" in selected_personas:
        recommendations.append(
            PersonaRecommendation(
                id="persona_fanfic_consistency",
                title="设定一致性检查",
                description="优先推荐角色关系和设定一致性工具。",
                action="/dashboard/projects",
            )
        )
    if "studio" in selected_personas:
        recommendations.append(
            PersonaRecommendation(
                id="persona_studio_collab",
                title="团队协作入口",
                description="推荐更适合团队分工的项目组织方式。",
                action="/dashboard/projects",
            )
        )

    if "monetize" in selected_goals:
        recommendations.append(
            PersonaRecommendation(
                id="goal_monetize_upgrade",
                title="变现能力建议",
                description="查看升级权益，优先解锁增长与商业化能力。",
                action="/pricing",
            )
        )
    if "improveQuality" in selected_goals:
        recommendations.append(
            PersonaRecommendation(
                id="goal_quality_review",
                title="质量提升路径",
                description="使用 AI 评审与改稿建议提高文本质量。",
                action="/dashboard",
            )
        )
    if "buildHabit" in selected_goals:
        recommendations.append(
            PersonaRecommendation(
                id="goal_habit_activation",
                title="写作习惯养成",
                description="每日完成一个小目标，形成稳定创作节奏。",
                action="/dashboard",
            )
        )

    if experience_level == "beginner":
        recommendations.append(
            PersonaRecommendation(
                id="level_beginner_path",
                title="新手引导路径",
                description="先从模板起步，再逐步完善角色和章节。",
                action="/dashboard",
            )
        )
    elif experience_level == "advanced":
        recommendations.append(
            PersonaRecommendation(
                id="level_advanced_shortcut",
                title="进阶快捷模式",
                description="为你优先展示批量改稿和高阶编辑能力。",
                action="/dashboard/projects",
            )
        )

    deduped: list[PersonaRecommendation] = []
    seen: set[str] = set()
    for item in recommendations:
        if item.id in seen:
            continue
        deduped.append(item)
        seen.add(item.id)
    return deduped[:4]


def _profile_to_read(profile: UserPersonaProfile) -> PersonaProfileRead:
    return PersonaProfileRead(
        version=profile.version,
        completed_at=profile.completed_at,
        selected_personas=_decode_json_string_array(profile.selected_personas),
        selected_goals=_decode_json_string_array(profile.selected_goals),
        experience_level=profile.experience_level,
        skipped=profile.skipped,
    )


def _is_onboarding_required(*, user: User, profile: UserPersonaProfile | None) -> bool:
    if profile is not None:
        return False

    rollout_at = _get_rollout_datetime()
    created_at = _normalize_datetime_to_utc(user.created_at)
    if created_at < rollout_at:
        return False

    account_age = _normalize_datetime_to_utc(utcnow()) - created_at
    max_window = timedelta(days=_get_rollout_window_days())
    return timedelta(0) <= account_age <= max_window


def _validate_payload(payload: PersonaOnboardingUpsertRequest) -> None:
    if payload.experience_level not in ALLOWED_EXPERIENCE_LEVELS:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail="Invalid experience_level",
        )

    selected_personas = [item.strip() for item in payload.selected_personas if item and item.strip()]
    deduped_personas = list(dict.fromkeys(selected_personas))
    if any(item not in ALLOWED_PERSONA_IDS for item in deduped_personas):
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail="Invalid selected_personas",
        )
    if not payload.skipped and not deduped_personas:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail="At least one selected_persona is required",
        )
    if len(deduped_personas) > MAX_PERSONA_SELECTION:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail=f"selected_personas cannot exceed {MAX_PERSONA_SELECTION}",
        )

    selected_goals = [item.strip() for item in payload.selected_goals if item and item.strip()]
    if any(item not in ALLOWED_GOAL_IDS for item in selected_goals):
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail="Invalid selected_goals",
        )


@router.get("/onboarding", response_model=PersonaOnboardingStateResponse)
def get_persona_onboarding_state(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    profile = session.exec(
        select(UserPersonaProfile).where(UserPersonaProfile.user_id == current_user.id)
    ).first()
    profile_read = _profile_to_read(profile) if profile else None
    recommendations = (
        _build_recommendations(
            selected_personas=profile_read.selected_personas,
            selected_goals=profile_read.selected_goals,
            experience_level=profile_read.experience_level,
        )
        if profile_read
        else []
    )

    return PersonaOnboardingStateResponse(
        required=_is_onboarding_required(user=current_user, profile=profile),
        rollout_at=_get_rollout_datetime(),
        new_user_window_days=_get_rollout_window_days(),
        profile=profile_read,
        recommendations=recommendations,
    )


@router.put("/onboarding", response_model=PersonaOnboardingStateResponse)
def upsert_persona_onboarding(
    payload: PersonaOnboardingUpsertRequest,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    _validate_payload(payload)

    selected_personas = [] if payload.skipped else list(dict.fromkeys([item.strip() for item in payload.selected_personas if item.strip()]))
    selected_goals = [] if payload.skipped else list(dict.fromkeys([item.strip() for item in payload.selected_goals if item.strip()]))

    now = utcnow()
    profile = session.exec(
        select(UserPersonaProfile).where(UserPersonaProfile.user_id == current_user.id)
    ).first()
    if profile is None:
        profile = UserPersonaProfile(user_id=current_user.id)

    profile.version = 1
    profile.selected_personas = _encode_string_array(selected_personas)
    profile.selected_goals = _encode_string_array(selected_goals)
    profile.experience_level = payload.experience_level
    profile.skipped = payload.skipped
    profile.completed_at = now
    profile.updated_at = now

    session.add(profile)
    session.commit()
    session.refresh(profile)

    profile_read = _profile_to_read(profile)
    recommendations = _build_recommendations(
        selected_personas=profile_read.selected_personas,
        selected_goals=profile_read.selected_goals,
        experience_level=profile_read.experience_level,
    )

    return PersonaOnboardingStateResponse(
        required=False,
        rollout_at=_get_rollout_datetime(),
        new_user_window_days=_get_rollout_window_days(),
        profile=profile_read,
        recommendations=recommendations,
    )


@router.get("/recommendations", response_model=PersonaRecommendationsResponse)
def get_persona_recommendations(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    profile = session.exec(
        select(UserPersonaProfile).where(UserPersonaProfile.user_id == current_user.id)
    ).first()
    if profile is None:
        return PersonaRecommendationsResponse(recommendations=[])

    profile_read = _profile_to_read(profile)
    recommendations = _build_recommendations(
        selected_personas=profile_read.selected_personas,
        selected_goals=profile_read.selected_goals,
        experience_level=profile_read.experience_level,
    )
    return PersonaRecommendationsResponse(recommendations=recommendations)
