"""
Permission decorators and exceptions for access control.

Provides:
- FeatureNotIncludedException: Exception for plan-gated features (402)
- QuotaExceededException: Exception for quota limit exceeded (402)
- require_quota: Decorator for checking and consuming feature quotas
"""
from collections.abc import Callable
from functools import wraps
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

FEATURE_NAMES_EN = {
    "material_upload": "Material Upload",
    "material_decompose": "Material Decomposition",
    "skill_create": "Custom Skill",
    "inspiration_copy": "Inspiration Copy",
    "ai_conversation": "AI Conversation",
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


def _validate_and_extract_args(
    kwargs: dict, _feature_type: str
) -> tuple[Any, Session, str]:
    """
    Validate and extract required arguments from kwargs.

    Args:
        kwargs: Keyword arguments from the decorated function
        feature_type: Feature type for error messages

    Returns:
        Tuple of (current_user, session, user_id)

    Raises:
        ValueError: If required arguments are missing
    """
    current_user = kwargs.get("current_user")
    session = kwargs.get("session")

    if current_user is None or session is None:
        raise ValueError(
            "require_quota decorator requires 'current_user' and 'session' "
            "keyword arguments. Ensure your route function includes: "
            "current_user: User = Depends(get_current_user), "
            "session: Session = Depends(get_session)"
        )

    return current_user, session, current_user.id


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


def _consume_quota_on_success(
    session: Session, user_id: str, feature_type: str, should_consume: bool
) -> None:
    """
    Consume quota after successful execution.

    Args:
        session: Database session
        user_id: User ID to consume quota for
        feature_type: Feature type to consume
        should_consume: Whether to consume (False for Pro users)
    """
    if should_consume:
        quota_service.consume_feature_quota(session, user_id, feature_type)


def require_quota(feature_type: str, *, consume_on_success: bool = True) -> Callable:
    """
    Decorator for checking and consuming feature quotas.

    This decorator:
    1. Checks if the feature is included in the current plan
    2. Checks if the user has available quota before executing the function
    3. Optionally consumes one unit of quota after successful execution
    4. Does NOT consume quota if execution fails (exception thrown)

    Args:
        feature_type: Type of feature to check quota for.
            Must be one of: "material_upload", "material_decompose",
            "skill_create", "inspiration_copy"

    Usage:
        @router.post("/upload")
        @require_quota("material_upload")
        async def upload_material(
            ...,
            current_user: User = Depends(get_current_user),
            session: Session = Depends(get_session)
        ):
            # Your upload logic here
            pass

    Important:
        The decorated function MUST have both `current_user` and `session`
        as keyword arguments (typically via FastAPI Depends).

    Returns:
        Decorated function with quota checking

    Raises:
        FeatureNotIncludedException: If the feature is not included (HTTP 402)
        QuotaExceededException: If quota limit is exceeded (HTTP 402)
        ValueError: If required arguments are missing
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            # Validate and extract required arguments
            _, session, user_id = _validate_and_extract_args(kwargs, feature_type)

            _ensure_feature_access(session, user_id, feature_type)
            _check_quota(session, user_id, feature_type)

            try:
                # Execute the original function
                result = await func(*args, **kwargs)

                # Success - consume quota
                _consume_quota_on_success(
                    session,
                    user_id,
                    feature_type,
                    consume_on_success,
                )

                return result

            except Exception:
                # Execution failed - do NOT consume quota
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            # Validate and extract required arguments
            _, session, user_id = _validate_and_extract_args(kwargs, feature_type)

            _ensure_feature_access(session, user_id, feature_type)
            _check_quota(session, user_id, feature_type)

            try:
                # Execute the original function
                result = func(*args, **kwargs)

                # Success - consume quota
                _consume_quota_on_success(
                    session,
                    user_id,
                    feature_type,
                    consume_on_success,
                )

                return result

            except Exception:
                # Execution failed - do NOT consume quota
                raise

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def check_quota(feature_type: str, session: Session, user_id: str) -> None:
    """
    Non-decorator quota check function for manual use.

    Use this when you need to check quota inside a function body
    rather than as a decorator.

    Args:
        feature_type: Type of feature to check
        session: Database session
        user_id: User ID to check quota for

    Raises:
        QuotaExceededException: If quota is exceeded

    Example:
        def some_function():
            check_quota("material_upload", session, user.id)
            # Continue with operation if no exception
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


def consume_quota(feature_type: str, session: Session, user_id: str) -> bool:
    """
    Non-decorator quota consumption for manual use.

    Use this to manually consume quota after an operation succeeds.

    Args:
        feature_type: Type of feature to consume quota for
        session: Database session
        user_id: User ID to consume quota for

    Returns:
        True if consumption succeeded, False otherwise

    Example:
        def some_function():
            # Do the operation
            result = do_something()

            # Consume quota on success
            consume_quota("material_upload", session, user.id)

            return result
    """
    return quota_service.consume_feature_quota(session, user_id, feature_type)
