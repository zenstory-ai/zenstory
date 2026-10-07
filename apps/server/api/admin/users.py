"""
Admin User Management API endpoints.

This module contains all user management endpoints for admin operations.
"""
import logging

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, or_, select

from config.datetime_utils import utcnow
from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from utils.email_identity import email_identity_matches, normalize_email_identity
from utils.logger import get_logger, log_with_context

from .schemas import AdminUserListResponse, AdminUserResponse, UserUpdateRequest

logger = get_logger(__name__)

router = APIRouter(tags=["admin-users"])


# ==================== User Management ====================

@router.get("/users", response_model=AdminUserListResponse)
def get_users(
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    limit: int = Query(20, ge=1, le=1000, description="Number of records to return"),
    search: str | None = Query(None, description="Search by username or email"),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get one page of users plus the total matching the search filter.

    Requires superuser privileges.
    """
    conditions = []
    if search:
        search_pattern = f"%{search}%"
        conditions.append(
            or_(User.username.ilike(search_pattern), User.email.ilike(search_pattern))
        )

    total = session.exec(select(func.count()).select_from(User).where(*conditions)).one()
    # Offset paging needs a total order, or PostgreSQL may repeat/skip rows across pages.
    users = session.exec(
        select(User)
        .where(*conditions)
        .order_by(User.created_at.desc(), User.id.desc())
        .offset(skip)
        .limit(limit)
    ).all()

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved users list",
        user_id=current_user.id,
        count=len(users),
        total=total,
        skip=skip,
        limit=limit,
        search=search,
    )

    return {"items": users, "total": total}


@router.get("/users/{user_id}", response_model=AdminUserResponse)
def get_user(
    user_id: str,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Get a specific user by ID.

    Requires superuser privileges.
    """
    user = session.get(User, user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved user details",
        user_id=current_user.id,
        target_user_id=user_id,
    )

    return user


@router.put("/users/{user_id}", response_model=AdminUserResponse)
def update_user(
    user_id: str,
    user_update: UserUpdateRequest,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Update a user's information.

    Requires superuser privileges.
    """
    user = session.get(User, user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Update fields if provided
    update_data = user_update.model_dump(exclude_unset=True)
    if any(value is None for value in update_data.values()):
        raise APIException(error_code=ErrorCode.VALIDATION_ERROR, status_code=422)

    removing_admin_access = update_data.get("is_active") is False or update_data.get("is_superuser") is False
    if user_id == current_user.id and removing_admin_access:
        raise APIException(error_code=ErrorCode.NOT_AUTHORIZED, status_code=400)
    if user.is_active and user.is_superuser and removing_admin_access:
        _lock_admin_access(session, current_user.id, user_id)

    if "username" in update_data and session.exec(
        select(User).where(User.username == update_data["username"], User.id != user_id)
    ).first():
        raise APIException(error_code=ErrorCode.AUTH_USERNAME_EXISTS, status_code=409)

    if "email" in update_data:
        normalized_email = normalize_email_identity(str(update_data["email"]))
        if session.exec(
            select(User).where(
                email_identity_matches(User.email, normalized_email),
                User.id != user_id,
            )
        ).first():
            raise APIException(error_code=ErrorCode.AUTH_EMAIL_EXISTS, status_code=409)
        update_data["email"] = normalized_email

    old_value = AdminUserResponse.model_validate(user).model_dump(mode="json")
    for field, value in update_data.items():
        setattr(user, field, value)

    user.updated_at = utcnow()

    try:
        session.add(user)
        admin_audit_service.log_action(
            session, current_user.id, "update_user", "user", user_id,
            old_value=old_value, new_value=AdminUserResponse.model_validate(user).model_dump(mode="json"),
            request=http_request, commit=False,
        )
        session.commit()
        session.refresh(user)
    except IntegrityError:
        session.rollback()
        raise APIException(error_code=ErrorCode.RESOURCE_CONFLICT, status_code=409) from None
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger,
        logging.INFO,
        "Updated user",
        user_id=current_user.id,
        target_user_id=user_id,
        updated_fields=list(update_data.keys()),
    )

    return user


def _lock_admin_access(session: Session, caller_id: str, target_id: str) -> None:
    """Serialize removals and recheck caller authority to preserve an active admin."""
    admins = session.exec(
        select(User).where(User.is_active.is_(True), User.is_superuser.is_(True))
        .order_by(User.id).with_for_update().execution_options(populate_existing=True)
    ).all()
    active_ids = {admin.id for admin in admins}
    if caller_id not in active_ids:
        raise APIException(error_code=ErrorCode.NOT_AUTHORIZED, status_code=403)
    if active_ids <= {target_id}:
        raise APIException(error_code=ErrorCode.RESOURCE_CONFLICT, status_code=409)


@router.delete("/users/{user_id}", response_model=AdminUserResponse)
def delete_user(
    user_id: str,
    http_request: Request,
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    Deactivate a user (is_active=False); reversible from the edit dialog.

    Requires superuser privileges.
    """
    user = session.get(User, user_id)
    if not user:
        raise APIException(
            error_code=ErrorCode.NOT_FOUND,
            status_code=status.HTTP_404_NOT_FOUND,
        )

    # Prevent self-deletion
    if user_id == current_user.id:
        raise APIException(
            error_code=ErrorCode.NOT_AUTHORIZED,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if user.is_active and user.is_superuser:
        _lock_admin_access(session, current_user.id, user_id)
    old_value = AdminUserResponse.model_validate(user).model_dump(mode="json")
    user.is_active = False
    user.updated_at = utcnow()

    try:
        session.add(user)
        admin_audit_service.log_action(
            session, current_user.id, "delete_user", "user", user_id,
            old_value=old_value, new_value=AdminUserResponse.model_validate(user).model_dump(mode="json"),
            request=http_request, commit=False,
        )
        session.commit()
        session.refresh(user)
    except Exception:
        session.rollback()
        raise

    log_with_context(
        logger,
        logging.INFO,
        "Soft deleted user",
        user_id=current_user.id,
        target_user_id=user_id,
    )

    return user
