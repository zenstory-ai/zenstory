"""
FileVersion model - File-level version history.

Stores incremental versions of file content with diff-based storage
to minimize space usage while maintaining full version history.

Storage strategy:
- Base versions: Complete content snapshots (every N versions)
- Delta versions: Only store diff from previous version
"""

from datetime import datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from .utils import generate_uuid


class FileVersion(SQLModel, table=True):
    """
    File version model for tracking individual file changes.

    Stores either:
    - Full content (is_base_version=True)
    - Diff from previous version (is_base_version=False)

    This allows efficient storage while maintaining full history.
    """

    __tablename__ = "file_version"
    __table_args__ = (
        UniqueConstraint(
            "file_id",
            "version_number",
            name="uq_file_version_file_id_version_number",
        ),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    file_id: str = Field(foreign_key="file.id", index=True)
    project_id: str = Field(foreign_key="project.id", index=True)

    # Version number (auto-incrementing per file)
    version_number: int = Field(default=1, index=True)

    # Content storage
    content: str = Field(
        default="", description="Full content or diff based on is_base_version"
    )
    is_base_version: bool = Field(
        default=False, description="True if this stores full content, False if diff"
    )

    # Metadata
    word_count: int = Field(default=0, description="Word count at this version")
    char_count: int = Field(default=0, description="Character count at this version")

    # Change tracking
    change_type: str = Field(
        default="edit",
        description="Type of change: create/edit/ai_edit/restore",
    )
    change_source: str = Field(
        default="user",
        description=(
            "Trusted actor that persisted the version: user/ai/system. "
            "Use change_type=ai_edit for user-reviewed AI-originated content."
        ),
    )
    change_summary: str | None = Field(
        default=None, description="Brief description of changes (can be AI-generated)"
    )

    # Diff statistics (for delta versions)
    lines_added: int = Field(default=0)
    lines_removed: int = Field(default=0)

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

    # Optional link to project snapshot (for milestone versions)
    snapshot_id: str | None = Field(
        default=None, foreign_key="snapshot.id", index=True
    )


# Constants
VERSION_BASE_INTERVAL = 10  # Create a base version every N versions
CHANGE_TYPE_CREATE = "create"
CHANGE_TYPE_EDIT = "edit"
CHANGE_TYPE_AI_EDIT = "ai_edit"
CHANGE_TYPE_RESTORE = "restore"
CHANGE_TYPE_AUTO_SAVE = "auto_save"

CHANGE_SOURCE_USER = "user"
CHANGE_SOURCE_AI = "ai"
CHANGE_SOURCE_SYSTEM = "system"
