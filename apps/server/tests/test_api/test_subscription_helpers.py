from __future__ import annotations

from sqlmodel import Session

from api.subscription import (
    _build_default_free_plan,
    _list_active_plans_or_default_free,
    _normalize_plan_entitlements,
    _normalize_plan_features_for_response,
)
from models.subscription import SubscriptionPlan


def test_normalize_plan_features_for_response_infers_material_access_and_export_formats():
    normalized = _normalize_plan_features_for_response(
        {"material_uploads": 1, "export_formats": ["docx", "txt", "docx"]}
    )

    assert normalized["materials_library_access"] is True
    assert normalized["export_formats"] == ["txt"]


def test_normalize_plan_entitlements_sanitizes_invalid_values():
    entitlements = _normalize_plan_entitlements(
        "pro",
        {
            "ai_conversations_per_day": "bad",
            "material_uploads_monthly": "oops",
            "materials_library_access": 0,
            "priority_queue_level": "urgent",
        },
    )

    assert entitlements["writing_credits_monthly"] == 0
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
