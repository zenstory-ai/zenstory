"""
Admin Audit Service - Logs admin actions for compliance and security.
"""
from fastapi import Request
from sqlalchemy import or_
from sqlmodel import Session, col, func, select

from middleware.rate_limit import get_client_ip
from models.subscription import AdminAuditLog


class AdminAuditService:
    """Service for logging admin actions."""

    def log_action(
        self,
        session: Session,
        admin_user_id: str,
        action: str,
        resource_type: str,
        resource_id: str | None = None,
        old_value: dict | None = None,
        new_value: dict | None = None,
        request: Request | None = None,
        *,
        commit: bool = True,
    ) -> AdminAuditLog:
        """
        Log an admin action.

        Args:
            session: Database session
            admin_user_id: ID of admin performing action
            action: Action type (e.g., "create_code", "update_subscription")
            resource_type: Resource type (e.g., "code", "subscription")
            resource_id: Optional resource ID
            old_value: Optional old value snapshot
            new_value: Optional new value snapshot
            request: Optional FastAPI request for IP/UA extraction

        Returns:
            Created AdminAuditLog entry
        """
        ip_address = None
        user_agent = None

        if request:
            ip_address = get_client_ip(request)
            if ip_address == "unknown":
                ip_address = None
            user_agent = request.headers.get("User-Agent")

        log = AdminAuditLog(
            admin_user_id=admin_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            old_value=old_value,
            new_value=new_value,
            ip_address=ip_address,
            user_agent=user_agent
        )
        session.add(log)
        if commit:
            session.commit()
            session.refresh(log)
        else:
            session.flush()
        return log

    def get_audit_logs(
        self,
        session: Session,
        admin_user_id: str | None = None,
        resource_type: str | None = None,
        action: str | None = None,
        resource_id: str | None = None,
        limit: int = 50,
        offset: int = 0
    ) -> list[AdminAuditLog]:
        """
        Get audit logs with filters.

        Args:
            session: Database session
            admin_user_id: Filter by admin user
            resource_type: Filter by resource type
            action: Filter by action type
            resource_id: Filter by resource ID
            limit: Max results
            offset: Offset for pagination

        Returns:
            List of matching audit logs
        """
        query = self._filtered_query(admin_user_id, resource_type, action, resource_id)
        query = query.order_by(
            col(AdminAuditLog.created_at).desc(), col(AdminAuditLog.id).desc()
        ).offset(offset).limit(limit)
        return session.exec(query).all()

    def count_audit_logs(self, session: Session, *, resource_type: str | None = None, action: str | None = None) -> int:
        query = self._filtered_query(None, resource_type, action, None)
        return session.exec(select(func.count()).select_from(query.subquery())).one()

    @staticmethod
    def _filtered_query(admin_user_id: str | None, resource_type: str | None, action: str | None, resource_id: str | None):
        query = select(AdminAuditLog)
        normalized_action = action.strip().lower() if action else None

        if admin_user_id:
            query = query.where(AdminAuditLog.admin_user_id == admin_user_id)
        if resource_type:
            query = query.where(AdminAuditLog.resource_type == resource_type)
        if normalized_action:
            # Support UI shorthand filters such as "create"/"update"/"delete"
            # while keeping backwards compatibility for exact action names
            # like "update_subscription" or "adjust_points".
            shorthand_actions = {"create", "update", "delete", "approve", "reject"}
            if normalized_action in shorthand_actions:
                query = query.where(
                    or_(
                        AdminAuditLog.action == normalized_action,
                        AdminAuditLog.action.startswith(f"{normalized_action}_"),
                    )
                )
            else:
                query = query.where(AdminAuditLog.action == normalized_action)
        if resource_id:
            query = query.where(AdminAuditLog.resource_id == resource_id)

        return query


# Singleton instance
admin_audit_service = AdminAuditService()
