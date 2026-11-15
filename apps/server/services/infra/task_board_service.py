"""
Task board service for persisting Agent task state in Redis.
"""
import json
from typing import Any

import redis

from services.infra.redis_client import get_redis_client
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

# Redis key prefix for task board
TASK_BOARD_KEY_PREFIX = "task_board"

# TTL for task board entries (7 days in seconds)
TASK_BOARD_TTL = 604800


class TaskBoardService:
    """
    Singleton service for managing Agent task board in Redis.
    """

    _instance: "TaskBoardService | None" = None

    def __new__(cls) -> "TaskBoardService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _get_key(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> str:
        """
        Generate Redis key for a session's task board.

        Args:
            session_id: The session identifier
            user_id: Optional runtime user scope
            project_id: Optional runtime project scope

        Returns:
            str: Redis key.
                 - Scoped key: 'task_board:{user_id}:{project_id}:{session_id}'
                 - Legacy key (when scope is unavailable): 'task_board:{session_id}'
        """
        normalized_session_id = (session_id or "").strip()
        normalized_user_id = (user_id or "").strip()
        normalized_project_id = (project_id or "").strip()

        if normalized_session_id and normalized_user_id and normalized_project_id:
            return (
                f"{TASK_BOARD_KEY_PREFIX}:"
                f"{normalized_user_id}:{normalized_project_id}:{normalized_session_id}"
            )
        return f"{TASK_BOARD_KEY_PREFIX}:{normalized_session_id}"

    def save_tasks(
        self,
        session_id: str,
        tasks: list[dict[str, Any]],
        *,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> bool:
        """
        Save tasks to Redis for a session.

        Args:
            session_id: The session identifier
            tasks: List of task dictionaries with 'task' and 'status' fields
            user_id: Optional runtime user scope
            project_id: Optional runtime project scope

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            client = get_redis_client()
            key = self._get_key(
                session_id,
                user_id=user_id,
                project_id=project_id,
            )
            tasks_json = json.dumps(tasks, ensure_ascii=False)
            client.setex(key, TASK_BOARD_TTL, tasks_json)

            log_with_context(
                logger,
                20,  # INFO
                "Saved tasks to task board",
                session_id=session_id,
                user_id=user_id,
                project_id=project_id,
                task_count=len(tasks),
            )

            return True
        except (redis.RedisError, TypeError, ValueError, OSError) as e:
            log_with_context(
                logger,
                40,  # ERROR
                "Error saving tasks to task board",
                session_id=session_id,
                user_id=user_id,
                project_id=project_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

    def get_tasks(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> list[dict[str, Any]] | None:
        """
        Retrieve tasks from Redis for a session.

        Args:
            session_id: The session identifier
            user_id: Optional runtime user scope
            project_id: Optional runtime project scope

        Returns:
            Optional[List[Dict[str, Any]]]: List of task dictionaries if exists, None otherwise
        """
        try:
            client = get_redis_client()
            key = self._get_key(
                session_id,
                user_id=user_id,
                project_id=project_id,
            )
            tasks_json = client.get(key)

            if tasks_json is None:
                log_with_context(
                    logger,
                    10,  # DEBUG
                    "No tasks found in task board",
                    session_id=session_id,
                    user_id=user_id,
                    project_id=project_id,
                )
                return None

            tasks: list[dict[str, Any]] = json.loads(str(tasks_json))

            log_with_context(
                logger,
                20,  # INFO
                "Retrieved tasks from task board",
                session_id=session_id,
                user_id=user_id,
                project_id=project_id,
                task_count=len(tasks),
            )

            return tasks
        except (redis.RedisError, json.JSONDecodeError, OSError) as e:
            log_with_context(
                logger,
                40,  # ERROR
                "Error retrieving tasks from task board",
                session_id=session_id,
                user_id=user_id,
                project_id=project_id,
                error=str(e),
                error_type=type(e).__name__,
            )
            return None


# Singleton instance
task_board_service = TaskBoardService()
