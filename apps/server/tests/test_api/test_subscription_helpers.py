from __future__ import annotations

from sqlmodel import Session

from api.subscription import (
    _build_default_free_plan,
    _list_active_plans_or_default_free,
    _normalize_plan_entitlements,
    _normalize_plan_features_for_response,
)
from models.subscription import SubscriptionPlan


def test_normalize_plan_features_for_response_uses_plan_defaults_and_filters_exports():
    normalized = _normalize_plan_features_for_response(
        {"export_formats": ["docx", "txt", "docx"]},
        plan_name="pro",
    )

    assert normalized["materials_library_access"] is True
    assert normalized["ai_conversations_per_day"] == -1
    assert normalized["max_projects"] == -1
    assert normalized["material_uploads"] == 5
    assert normalized["export_formats"] == ["txt"]
    # 生产专业版套餐行没有 priority_support；不能落到免费版的 False。
    assert normalized["priority_support"] is True
    assert _normalize_plan_features_for_response({}, plan_name="free")["priority_support"] is False


def test_normalize_plan_features_does_not_infer_access_from_free_upload_limit():
    normalized = _normalize_plan_features_for_response(
        {"material_uploads": 1},
        plan_name="free",
    )

    assert normalized["materials_library_access"] is False
    assert normalized["material_uploads"] == 1


def test_normalize_plan_features_replaces_legacy_free_message_limit():
    normalized = _normalize_plan_features_for_response(
        {"ai_conversations_per_day": 20},
        plan_name="free",
    )

    assert normalized["ai_conversations_per_day"] == 10


def test_normalize_plan_features_preserves_explicit_false_and_zero_overrides():
    normalized = _normalize_plan_features_for_response(
        {
            "ai_conversations_per_day": 0,
            "materials_library_access": False,
            "material_uploads": 0,
            "material_decompositions": 0,
            "custom_skills": 0,
        },
        plan_name="pro",
    )

    assert normalized["ai_conversations_per_day"] == 0
    assert normalized["materials_library_access"] is False
    assert normalized["material_uploads"] == 0
    assert normalized["material_decompositions"] == 0
    assert normalized["custom_skills"] == 0


def test_normalize_plan_entitlements_sanitizes_invalid_values():
    entitlements = _normalize_plan_entitlements(
        "pro",
        {
            "ai_conversations_per_day": "bad",
            "writing_credits_monthly": 999,
            "agent_runs_monthly": 999,
            "material_uploads_monthly": "oops",
            "material_uploads": "bad",
            "materials_library_access": 0,
            "priority_queue_level": "urgent",
        },
    )

    assert entitlements["ai_conversations_per_day"] == 0
    assert entitlements["writing_credits_monthly"] == 0
    assert entitlements["agent_runs_monthly"] == 0
    assert entitlements["material_uploads_monthly"] == 0
    assert entitlements["materials_library_access"] is False
    # Unimplemented perks are never derived from stored features.
    assert "priority_queue_level" not in entitlements
    assert "context_tokens_limit" not in entitlements


def test_list_active_plans_or_default_free_returns_fallback_plan(db_session: Session):
    plans = _list_active_plans_or_default_free(db_session)

    assert len(plans) == 1
    assert plans[0].name == _build_default_free_plan().name


def test_list_active_plans_or_default_free_prefers_db_plans(db_session: Session):
    plan = SubscriptionPlan(
        name="free",
        display_name="Free",
        display_name_en="Free",
        price_monthly_cents=0,
        price_yearly_cents=0,
        features={"materials_library_access": False},
        is_active=True,
    )
    db_session.add(plan)
    db_session.commit()

    plans = _list_active_plans_or_default_free(db_session)

    assert [item.id for item in plans] == [plan.id]


def test_build_default_free_plan_creates_independent_feature_copies():
    first = _build_default_free_plan()
    second = _build_default_free_plan()

    first.features["materials_library_access"] = "mutated"

    assert second.features["materials_library_access"] is False
    assert first.id == "default-free-plan"
    assert second.id == "default-free-plan"


def test_list_active_plans_or_default_free_ignores_inactive_public_plans(db_session: Session):
    inactive_plan = SubscriptionPlan(
        name="free",
        display_name="Free",
        display_name_en="Free",
        price_monthly_cents=0,
        price_yearly_cents=0,
        features={"materials_library_access": True},
        is_active=False,
    )
    db_session.add(inactive_plan)
    db_session.commit()

    plans = _list_active_plans_or_default_free(db_session)

    assert len(plans) == 1
    assert plans[0].id == "default-free-plan"
