"""
Permission checking for Agent tool execution.

Provides centralized permission validation for file operations:
- Project ownership verification
- User authorization
- Access control
"""


from sqlmodel import Session

from models import File, Project
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


class PermissionError(Exception):
    """Base exception for permission errors."""
    pass


class ForbiddenError(PermissionError):
    """User does not have permission."""
    pass


class NotFoundError(PermissionError):
    """Resource not found."""
    pass


def check_project_ownership(
    session: Session,
    project_id: str,
    user_id: str | None = None,
) -> Project:
    """
    Check if user owns the project.

    Args:
        session: Database session
        project_id: Project ID to check
        user_id: User ID (None if not authenticated)

    Returns:
        The Project object if authorized

    Raises:
        NotFoundError: If project doesn't exist
        ForbiddenError: If user doesn't own the project
    """
    project = session.get(Project, project_id)

    if not project:
        log_with_context(
            logger,
            40,  # ERROR
            "Project not found",
            project_id=project_id,
            user_id=user_id,
        )
        raise NotFoundError(f"Project not found: {project_id}")

    # Check if project is soft-deleted
    if project.is_deleted:
        log_with_context(
            logger,
            40,  # ERROR
            "Project is soft-deleted",
            project_id=project_id,
            user_id=user_id,
        )
        raise NotFoundError(f"Project not found: {project_id}")

    # If user_id is provided, check ownership
    if user_id is not None and project.owner_id != user_id:
        log_with_context(
            logger,
            40,  # ERROR
            "Permission denied: User does not own project",
            project_id=project_id,
            user_id=user_id,
            owner_id=project.owner_id,
        )
        raise ForbiddenError(f"You don't have permission to access project {project_id}")

    return project


def check_file_ownership(
    session: Session,
    file_id: str,
    project_id: str,
    user_id: str | None = None,
) -> File:
    """
    Check if file belongs to the specified project and user has access.

    Args:
        session: Database session
        file_id: File ID
        project_id: Project ID to verify
        user_id: User ID

    Returns:
        The File object if authorized

    Raises:
        NotFoundError: If file doesn't exist, or doesn't belong to the project
            (跨项目访问对调用方表现为"文件不存在"，不泄漏其它项目文件的存在性)
        ForbiddenError: If user lacks access to the project
    """
    # First check project access
    check_project_ownership(session, project_id, user_id)

    # Get the file
    file = session.get(File, file_id)

    if not file or file.is_deleted:
        raise NotFoundError(f"File not found: {file_id}")

    # Verify file belongs to the project. A mismatch is reported as not-found:
    # 按 id 探测其它项目的文件不应得到与"不存在"可区分的响应；真实原因只进日志。
    if file.project_id != project_id:
        log_with_context(
            logger,
            40,  # ERROR
            "File does not belong to requested project",
            file_id=file_id,
            file_project_id=file.project_id,
            requested_project_id=project_id,
            user_id=user_id,
        )
        raise NotFoundError(f"File not found: {file_id}")

    return file


def check_file_access_in_tool_context(
    session: Session,
    file: File,
    user_id: str | None = None,
    *,
    project_id: str | None = None,
    request_context: object | None = None,
) -> None:
    """
    Check by-id file access for agent tool execution.

    Agent 工具按 id 定位文件时，目标必须属于当前 ``AgentRequestContext`` 绑定的项目：
    根文件夹 id 可由 project_id 推出（如 "{project_id}-draft-folder"），仅靠
    "用户拥有该文件所在项目"不足以阻止被注入文本诱导的跨项目写/删。
    未绑定请求上下文的调用（stream_adapter 落盘、后台任务等直接构造执行器的
    路径）退回文件自身项目的所有权校验。

    Args:
        session: Database session
        file: Already-loaded target file
        user_id: User ID
        project_id: Optional explicit project ID scope override
        request_context: Optional ``AgentRequestContext`` providing the scoped project ID

    Raises:
        NotFoundError: 文件不属于当前上下文项目（用户可见信息与文件不存在一致）
        ForbiddenError: 用户不拥有相关项目
    """
    context_project_id = project_id
    if context_project_id is None and request_context is not None:
        raw_project_id = getattr(request_context, "project_id", None)
        if isinstance(raw_project_id, str) and raw_project_id:
            context_project_id = raw_project_id

    if context_project_id is None:
        check_project_ownership(session, file.project_id, user_id)
        return

    try:
        check_file_ownership(session, file.id, context_project_id, user_id)
    except NotFoundError as e:
        # 与按 id 加载失败共用同一条用户可见文案（真实原因已在权限层日志中）
        raise NotFoundError("文件不存在或已删除") from e
