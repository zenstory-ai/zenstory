"""
Admin Audit Log API endpoints.

This module contains audit log endpoints for admin operations.
"""
import logging

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from database import get_session
from models import User
from services.admin_audit_service import admin_audit_service
from services.core.auth_service import get_current_superuser
from utils.logger import get_logger, log_with_context

from .schemas import AuditLogListResponse, AuditLogResponse

logger = get_logger(__name__)

router = APIRouter(tags=["admin-audit"])


# ==================== Audit Logs ====================


@router.get("/audit-logs", response_model=AuditLogListResponse)
def list_audit_logs(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(50, ge=1, le=200, description="Items per page"),
    resource_type: str | None = Query(None, description="Filter by resource type"),
    action: str | None = Query(None, description="Filter by action"),
    include_total: bool = Query(True, description="Include full matching count"),
    current_user: User = Depends(get_current_superuser),
    session: Session = Depends(get_session),
):
    """
    List audit logs with filters.

    Requires superuser privileges.
    """
    logs = admin_audit_service.get_audit_logs(
        session,
        resource_type=resource_type,
        action=action,
        limit=page_size,
        offset=(page - 1) * page_size
    )

    log_with_context(
        logger,
        logging.INFO,
        "Retrieved audit logs",
        user_id=current_user.id,
        count=len(logs),
        resource_type=resource_type,
        action=action,
    )

    actor_ids = {log.admin_user_id for log in logs}
    actor_names = (
        dict(session.exec(select(User.id, User.username).where(User.id.in_(actor_ids))).all())
        if actor_ids
        else {}
    )
    total = (
        admin_audit_service.count_audit_logs(session, resource_type=resource_type, action=action)
        if include_total
        else len(logs)
    )
    return AuditLogListResponse(
        items=[AuditLogResponse(
            **log.model_dump(exclude={"admin_user_id"}),
            admin_id=log.admin_user_id,
            admin_name=actor_names.get(log.admin_user_id),
        ) for log in logs],
        page=page,
        page_size=page_size,
        total=total,
    )
