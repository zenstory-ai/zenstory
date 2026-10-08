"""Canonical default values for subscription plans."""

from copy import deepcopy
from typing import Any

DEFAULT_FREE_PLAN_NAME = "free"
DEFAULT_FREE_PLAN_DISPLAY_NAME = "免费试用"
DEFAULT_FREE_PLAN_DISPLAY_NAME_EN = "Free Trial"
DEFAULT_FREE_PLAN_PRICE_MONTHLY_CENTS = 0
DEFAULT_FREE_PLAN_PRICE_YEARLY_CENTS = 0

DEFAULT_FREE_PLAN_FEATURES: dict[str, Any] = {
    "ai_conversations_per_day": 10,
    "context_window_tokens": 4096,
    "file_versions_per_file": 10,
    "max_projects": 3,
    "export_formats": ["txt"],
    "custom_prompts": False,
    "materials_library_access": False,
    "material_uploads": 0,
    "material_decompositions": 0,
    "custom_skills": 3,
    "inspiration_copies_monthly": 10,
    "priority_support": False,
}

# What a paid Pro plan grants when its stored features omit a key. Production Pro
# rows predate custom_skills/inspiration_copies_monthly; without this fallback a
# missing key silently resolved to the free-tier limit.
DEFAULT_PRO_PLAN_FEATURES: dict[str, Any] = {
    "ai_conversations_per_day": -1,
    "context_window_tokens": 16384,
    "file_versions_per_file": 100,
    "max_projects": -1,
    "export_formats": ["txt"],
    "custom_prompts": True,
    "materials_library_access": True,
    "material_uploads": 5,
    "material_decompositions": 5,
    "custom_skills": 20,
    "inspiration_copies_monthly": 100,
}

PLAN_FEATURE_DEFAULTS: dict[str, dict[str, Any]] = {
    DEFAULT_FREE_PLAN_NAME: DEFAULT_FREE_PLAN_FEATURES,
    "pro": DEFAULT_PRO_PLAN_FEATURES,
}

SUPPORTED_EXPORT_FORMATS: tuple[str, ...] = ("txt",)


DEFAULT_FREE_TIER: dict[str, Any] = {
    "name": DEFAULT_FREE_PLAN_NAME,
    "display_name": DEFAULT_FREE_PLAN_DISPLAY_NAME,
    "display_name_en": DEFAULT_FREE_PLAN_DISPLAY_NAME_EN,
    "price_monthly_cents": DEFAULT_FREE_PLAN_PRICE_MONTHLY_CENTS,
    "price_yearly_cents": DEFAULT_FREE_PLAN_PRICE_YEARLY_CENTS,
    "features": DEFAULT_FREE_PLAN_FEATURES,
}


def clone_default_free_features() -> dict[str, Any]:
    """Return a mutable copy of free-tier features."""
    return deepcopy(DEFAULT_FREE_PLAN_FEATURES)


def plan_feature_default(plan_name: str | None, key: str, fallback: Any = 0) -> Any:
    """Default for one feature key of a plan; unknown plans use the free tier."""
    preset = PLAN_FEATURE_DEFAULTS.get(plan_name or "", DEFAULT_FREE_PLAN_FEATURES)
    if key in preset:
        return deepcopy(preset[key])
    return deepcopy(DEFAULT_FREE_PLAN_FEATURES.get(key, fallback))


def resolve_plan_feature(
    plan_name: str | None, features: dict[str, Any] | None, key: str, fallback: Any = 0
) -> Any:
    """A plan's stored feature value, or that plan's own default when absent."""
    if plan_name == DEFAULT_FREE_PLAN_NAME and key == "ai_conversations_per_day":
        return DEFAULT_FREE_PLAN_FEATURES[key]
    value = (features or {}).get(key)
    if value is not None:
        return value
    return plan_feature_default(plan_name, key, fallback)


def normalize_export_formats(export_formats: Any) -> list[str]:
    """
    Normalize export formats to currently supported, deduplicated values.

    Unsupported/invalid values are dropped to ensure subscription entitlements
    remain aligned with runtime export capability.
    """
    if not isinstance(export_formats, list):
        return []

    normalized: list[str] = []
    for raw_format in export_formats:
        if not isinstance(raw_format, str):
            continue
        candidate = raw_format.strip().lower()
        if candidate in SUPPORTED_EXPORT_FORMATS and candidate not in normalized:
            normalized.append(candidate)

    return normalized
