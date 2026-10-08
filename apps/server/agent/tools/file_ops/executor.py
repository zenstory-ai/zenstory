"""
Composite FileToolExecutor for agent tools.

This module provides the main FileToolExecutor class that composes and delegates
to the modular operation classes (FileCRUD, FileEditor, ProjectOperations).

This is the primary executor class used by the agent system for file operations,
providing a unified interface over all file-related capabilities.

Extracted from the monolithic file_executor.py for better maintainability.
"""

from typing import Any

from sqlmodel import Session

from .crud import FileCRUD
from .edit import FileEditor
from .project import ProjectOperations


class FileToolExecutor:
    """
    Composite executor for file-based tools.

    This class provides a unified interface over all file operations by
    composing and delegating to specialized operation classes:
    - FileCRUD: Create, read, update, delete, query files
    - FileEditor: Precise content editing operations
    - ProjectOperations: Project status and task board operations

    Simple CRUD operations on the File model with permission checking
    and version history support.
    """

    def __init__(self, session: Session, user_id: str | None = None):
        """
        Initialize file tool executor.

        Args:
            session: Database session
            user_id: Current user ID (UUID string, for permission checks)
        """
        self.session = session
        self.user_id = user_id

        # Initialize operation handlers
        self._crud = FileCRUD(session, user_id)
        self._editor = FileEditor(session, user_id)
        self._project = ProjectOperations(session, user_id)

    # ========== CRUD Operations ==========

    def create_file(
        self,
        project_id: str,
        title: str,
        file_type: str = "document",
        content: str = "",
        parent_id: str | None = None,
        order: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Create a new file.

        Args:
            project_id: Project ID
            title: File title/name
            file_type: File type (outline, character, lore, etc.)
            content: File content
            parent_id: Parent file ID (for folders)
            order: Sort order
            metadata: Type-specific metadata (JSON)

        Returns:
            Created file data

        Raises:
            PermissionError: If user doesn't have permission
        """
        return self._crud.create_file(
            project_id=project_id,
            title=title,
            file_type=file_type,
            content=content,
            parent_id=parent_id,
            order=order,
            metadata=metadata,
        )

    def update_file(
        self,
        id: str,
        title: str | None = None,
        content: str | None = None,
        parent_id: str | None = None,
        order: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Update an existing file.

        Args:
            id: File ID to update
            title: New title
            content: New content
            parent_id: New parent ID
            order: New order
            metadata: New metadata (overwrites existing)

        Returns:
            Updated file data

        Raises:
            PermissionError: If user doesn't have permission
            ValueError: If file not found
        """
        return self._crud.update_file(
            id=id,
            title=title,
            content=content,
            parent_id=parent_id,
            order=order,
            metadata=metadata,
        )

    def delete_file(
        self,
        id: str,
        recursive: bool = False,
    ) -> bool:
        """
        Delete a file.

        Args:
            id: File ID to delete
            recursive: If True, also delete all children

        Returns:
            True if deleted successfully

        Raises:
            PermissionError: If user doesn't have permission
            ValueError: If file not found
        """
        return self._crud.delete_file(id=id, recursive=recursive)

    def query_files(
        self,
        project_id: str,
        id: str | None = None,
        query: str | None = None,
        file_type: str | None = None,
        file_types: list[str] | None = None,
        parent_id: str | None = None,
        metadata_filter: dict[str, Any] | None = None,
        limit: int = 50,
        offset: int = 0,
        response_mode: str | None = None,
        content_preview_chars: int | None = None,
        include_content: bool | None = None,
    ) -> list[dict[str, Any]]:
        """
        Query and search files (unified method).

        Supports keyword search, type filtering, parent filtering, and metadata filtering.

        Args:
            project_id: Project ID
            query: Search keyword for title/content (optional)
            file_type: Single file type filter (optional)
            file_types: Multiple file types filter (optional, use this OR file_type)
            parent_id: Parent file ID filter (optional)
            metadata_filter: Metadata field filters (optional)
            limit: Maximum results
            offset: Offset for pagination
            response_mode: "summary" / "full"; None = full for ``id`` lookups,
                summary otherwise

        Returns:
            List of file data

        Raises:
            PermissionError: If user doesn't have permission
        """
        return self._crud.query_files(
            project_id=project_id,
            id=id,
            query=query,
            file_type=file_type,
            file_types=file_types,
            parent_id=parent_id,
            metadata_filter=metadata_filter,
            limit=limit,
            offset=offset,
            response_mode=response_mode,
            content_preview_chars=content_preview_chars,
            include_content=include_content,
        )

    def hybrid_search(
        self,
        project_id: str,
        query: str,
        top_k: int = 10,
        entity_types: list[str] | None = None,
        min_score: float = 0.0,
    ) -> dict[str, Any]:
        """
        Hybrid retrieval (lexical + vector fusion).
        """
        return self._crud.hybrid_search(
            project_id=project_id,
            query=query,
            top_k=top_k,
            entity_types=entity_types,
            min_score=min_score,
        )

    # ========== Edit Operations ==========

    def edit_file(
        self,
        id: str,
        edits: list[dict[str, Any]],
        continue_on_error: bool = False,
    ) -> dict[str, Any]:
        """
        Apply precise edits to a file's content.

        Supports the following edit operations:
        - replace: Find and replace text (old -> new)
        - insert_after: Insert text after an anchor
        - insert_before: Insert text before an anchor
        - append: Add text at the end
        - prepend: Add text at the beginning
        - delete: Remove specified text

        Args:
            id: File ID to edit
            edits: List of edit operations, each containing:
                - op: Operation type
                - old: Original text (for replace/delete)
                - new: New text (for replace)
                - anchor: Anchor text (for insert_after/insert_before)
                - text: Text to insert (for insert_*/append/prepend)
                - replace_all: Whether to replace all occurrences (for replace)

        Returns:
            Dict with edit results:
                - id: File ID
                - title: File title
                - edits_applied: Number of successful edits
                - new_length: New content length
                - details: List of applied edit details

        Raises:
            ValueError: If file not found or edit operation fails
            PermissionError: If user doesn't have permission
        """
        return self._editor.edit_file(
            id=id,
            edits=edits,
            continue_on_error=continue_on_error,
        )

    # ========== Project Operations ==========

    def update_project_status(
        self,
        project_id: str,
        summary: str | None = None,
        current_phase: str | None = None,
        writing_style: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """
        Update project status information for AI context awareness.

        Args:
            project_id: Project ID
            summary: Project summary/background
            current_phase: Current writing phase description
            writing_style: Writing style guidelines
            notes: Additional notes for AI assistant

        Returns:
            Updated project status fields

        Raises:
            PermissionError: If user doesn't have permission
            ValueError: If project not found
        """
        return self._project.update_project_status(
            project_id=project_id,
            summary=summary,
            current_phase=current_phase,
            writing_style=writing_style,
            notes=notes,
        )

    def execute_update_plan(
        self,
        session_id: str,
        tasks: list[dict[str, Any]],
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Execute update_plan tool - save tasks to Redis task board.

        Args:
            session_id: Session identifier for task board
            tasks: List of task dictionaries with 'task' and 'status' fields
            user_id: Current user ID for runtime isolation
            project_id: Current project ID for runtime isolation

        Returns:
            Success message with task list summary

        Raises:
            ValueError: If Redis connection fails
        """
        return self._project.execute_update_plan(
            session_id=session_id,
            tasks=tasks,
            user_id=user_id,
            project_id=project_id,
        )


__all__ = [
    "FileToolExecutor",
]
