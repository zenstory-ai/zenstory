"""Project access verification utilities.

Provides centralized project ownership verification to reduce code duplication
and ensure consistent access control across API endpoints.
"""
from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import Project, User


def verify_project_ownership(
    project_id: str,
    current_user: User,
    session: Session,
    *,
    forbidden_error_code: str = ErrorCode.NOT_AUTHORIZED,
) -> Project:
    """
    Verify user owns the project.

    This function checks:
    1. Project exists
    2. User is the owner of the project
    3. Project is not soft-deleted

    Args:
        project_id: The project ID to verify
        current_user: The current authenticated user
        session: Database session
        forbidden_error_code: Error code raised on 403 ownership mismatch

    Raises:
        APIException: 404 if project not found or soft-deleted
        APIException: 403 if user doesn't own project

    Returns:
        Project: The verified project object
    """
    project = session.get(Project, project_id)
    if not project:
        raise APIException(error_code=ErrorCode.PROJECT_NOT_FOUND, status_code=404)

    # Admin/superuser can access all projects for support/debugging.
    if project.owner_id != current_user.id and not current_user.is_superuser:
        raise APIException(error_code=forbidden_error_code, status_code=403)

    if project.is_deleted:
        raise APIException(error_code=ErrorCode.PROJECT_NOT_FOUND, status_code=404)

    return project
