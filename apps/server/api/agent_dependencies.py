"""Shared authentication dependencies for Agent API routers."""

from fastapi import Depends
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import AgentApiKey, Project
from services.agent_auth_service import (
    get_agent_user,
    verify_project_access,
    verify_scope,
)

type AgentAuthContext = tuple[Session, str, AgentApiKey]


def require_scope(required_scope: str):
    """Require an API key scope and return agent auth context."""

    async def dependency(
        context: AgentAuthContext = Depends(get_agent_user),
    ) -> AgentAuthContext:
        session, user_id, api_key = context

        if not verify_scope(api_key, required_scope):
            raise APIException(
                error_code=ErrorCode.AUTH_SCOPE_DENIED,
                status_code=403,
                detail=f"API Key lacks required scope: {required_scope}",
            )

        return (session, user_id, api_key)

    return dependency


def require_project_access(required_scope: str = "read"):
    """Require scope + project allowlist + project ownership."""

    def dependency(
        project_id: str,
        context: AgentAuthContext = Depends(get_agent_user),
    ) -> AgentAuthContext:
        session, user_id, api_key = context

        if not verify_scope(api_key, required_scope):
            raise APIException(
                error_code=ErrorCode.AUTH_SCOPE_DENIED,
                status_code=403,
                detail=f"API Key lacks required scope: {required_scope}",
            )

        if not verify_project_access(api_key, project_id):
            raise APIException(
                error_code=ErrorCode.NOT_AUTHORIZED,
                status_code=403,
                detail="API Key does not have access to this project",
            )

        project = session.get(Project, project_id)
        if not project or project.owner_id != user_id or project.is_deleted:
            raise APIException(
                error_code=ErrorCode.PROJECT_NOT_FOUND,
                status_code=404,
                detail="Project not found",
            )

        return (session, user_id, api_key)

    return dependency
