"""
Permission checking utilities.

Provides shared dependency functions for verifying user access to resources.
"""

from sqlmodel import Session

from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import Project, User


def verify_project_access_sync(
    project_id: str,
    session: Session,
    current_user: User,
) -> Project:
    """
    Verify user's access to a project.

    Checks:
    - Project exists
    - User is the owner of the project
    - Project is not soft-deleted

    Args:
        project_id: The project ID to verify
        session: Database session
        current_user: Current authenticated user

    Returns:
        Project object if access is granted

    Raises:
        APIException(403): If user is not the owner
        APIException(404): If project not found or is deleted
    """
    project = session.get(Project, project_id)
    # NOTE: This helper intentionally returns 403 when the project doesn't exist
    # to avoid leaking project existence via this endpoint family (chat/agent).
    # Admin/superuser can access all projects for support/debugging.
    if not project or (project.owner_id != current_user.id and not current_user.is_superuser):
        raise APIException(error_code=ErrorCode.NOT_AUTHORIZED, status_code=403)
    if project.is_deleted:
        raise APIException(error_code=ErrorCode.PROJECT_NOT_FOUND, status_code=404)
    return project


async def verify_project_access(
    project_id: str,
    session: Session,
    current_user: User,
) -> Project:
    """Keep asynchronous callers compatible with the shared permission policy."""
    return verify_project_access_sync(project_id, session, current_user)
