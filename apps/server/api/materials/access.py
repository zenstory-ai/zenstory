"""Materials-library entitlement access helpers."""

from fastapi import Depends
from services.auth import get_current_active_user
from sqlmodel import Session

from core.permissions import FeatureNotIncludedException
from database import get_session
from models import User
from services.quota_service import quota_service


def require_materials_library_access(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> User:
    """Require access to the paid materials-library workspace."""
    if not quota_service.has_feature_access(
        session,
        current_user.id,
        "materials_library_access",
    ):
        raise FeatureNotIncludedException(feature_type="material_decompose")
    return current_user


def require_materials_library_read(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> User:
    """读取自己的素材库：有素材库权益，或者已经用过免费试拆（看自己拆的那一本）。"""
    if quota_service.has_feature_access(session, current_user.id, "materials_library_access"):
        return current_user
    if quota_service.material_trial_status(session, current_user.id)["used"]:
        return current_user
    raise FeatureNotIncludedException(feature_type="material_decompose")


def require_materials_upload(
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
) -> User:
    """上传拆书：有素材库权益（按月度次数），或者免费试拆还没用过。"""
    if quota_service.has_feature_access(session, current_user.id, "materials_library_access"):
        return current_user
    if quota_service.material_trial_status(session, current_user.id)["available"]:
        return current_user
    raise FeatureNotIncludedException(feature_type="material_decompose")
