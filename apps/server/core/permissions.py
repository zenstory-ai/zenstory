"""
Permission helpers and exceptions for access control.

Provides:
- FeatureNotIncludedException: Exception for plan-gated features (402)
- QuotaExceededException: Exception for quota limit exceeded (402)
- check_quota: Quota check function
- require_custom_skill_slot: Custom skill slot check function
"""
from typing import Any

from fastapi import status
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from services.quota_service import quota_service

# Feature type display names (for error messages)
FEATURE_NAMES = {
    "material_upload": "素材库上传",
    "material_decompose": "素材拆解",
    "skill_create": "自定义技能",
    "inspiration_copy": "灵感复制",
    "ai_conversation": "AI 对话",
}

FEATURE_ACCESS_MAP = {
    "material_upload": "materials_library_access",
    "material_decompose": "materials_library_access",
}


class FeatureNotIncludedException(APIException):
    """Exception raised when the current plan does not include a feature."""

    def __init__(
        self,
        feature_type: str,
        headers: dict[str, Any] | None = None,
    ):
        self.feature_type = feature_type

        feature_name = FEATURE_NAMES.get(feature_type, feature_type)
        detail = {
            "message": f"当前套餐暂不包含: {feature_name}",
            "error_code": ErrorCode.FEATURE_NOT_INCLUDED,
            "feature_type": feature_type,
            "feature_name": feature_name,
            "upgrade_url": "/subscription/plans",
        }

        super().__init__(
            error_code=ErrorCode.FEATURE_NOT_INCLUDED,
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            headers=headers,
            detail=detail,
        )


class QuotaExceededException(APIException):
    """
    Exception raised when a quota limit is exceeded.

    Returns HTTP 402 (Payment Required) with upgrade guidance.
    """

    def __init__(
        self,
        feature_type: str,
        used: int,
        limit: int,
        headers: dict[str, Any] | None = None,
    ):
        self.feature_type = feature_type
        self.used = used
        self.limit = limit

        # Build detail with upgrade info
        feature_name = FEATURE_NAMES.get(feature_type, feature_type)
        detail = {
            "message": f"配额已用尽: {feature_name}",
            "error_code": ErrorCode.QUOTA_EXCEEDED,
            "feature_type": feature_type,
            "feature_name": feature_name,
            "used": used,
            "limit": limit,
            "upgrade_url": "/subscription/plans",
        }

        super().__init__(
            error_code=ErrorCode.QUOTA_EXCEEDED,
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            headers=headers,
            detail=detail,
        )


def _ensure_feature_access(
    session: Session,
    user_id: str,
    feature_type: str,
) -> None:
    """Raise when a feature is not included in the current plan."""
    feature_key = FEATURE_ACCESS_MAP.get(feature_type)
    if not feature_key:
        return

    if not quota_service.has_feature_access(session, user_id, feature_key):
        raise FeatureNotIncludedException(feature_type=feature_type)


def _check_quota(
    session: Session,
    user_id: str,
    feature_type: str,
) -> None:
    """
    Check quota and raise exception if exceeded.

    Raises:
        QuotaExceededException: If quota is exceeded
    """
    allowed, used, limit = quota_service.check_feature_quota(
        session, user_id, feature_type
    )

    if not allowed:
        raise QuotaExceededException(
            feature_type=feature_type,
            used=used,
            limit=limit,
        )


def check_quota(feature_type: str, session: Session, user_id: str) -> None:
    """
    Quota check function for manual use.

    Args:
        feature_type: Type of feature to check
        session: Database session
        user_id: User ID to check quota for

    Raises:
        FeatureNotIncludedException: If the feature is not included
        QuotaExceededException: If quota is exceeded
    """
    _ensure_feature_access(session, user_id, feature_type)
    _check_quota(session, user_id, feature_type)


def require_custom_skill_slot(session: Session, user_id: str) -> None:
    """
    Gate creating one more custom skill (create or import).

    Call right before inserting the skill and commit the insert in the same
    transaction: the check holds a write lock until then, so concurrent requests
    cannot both take the last slot. Owned skills are counted, so deleting a
    skill frees its slot.

    Raises:
        QuotaExceededException: If the user already owns the plan's maximum
    """
    allowed, used, limit = quota_service.check_custom_skill_slot(session, user_id)
    if not allowed:
        session.rollback()
        raise QuotaExceededException(feature_type="skill_create", used=used, limit=limit)
