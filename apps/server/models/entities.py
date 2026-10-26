"""
Database entity models.

Defines all SQLModel entities for the application:
- User: User accounts
- Project: Novel projects
- Snapshot: Version snapshots
- ChatSession: AI chat sessions
- ChatMessage: Chat messages
"""

from datetime import datetime

from pydantic import field_serializer
from sqlalchemy import Index, text
from sqlmodel import Field, SQLModel

from config.project_notes import without_legacy_proposals
from config.project_status import PROJECT_STATUS_MAX_LENGTHS

from .utils import generate_uuid

# Project type constants
PROJECT_TYPE_NOVEL = "novel"           # Long-form novel
PROJECT_TYPE_SHORT_STORY = "short"     # Short story
PROJECT_TYPE_SCREENPLAY = "screenplay" # Screenplay / mini-drama


class User(SQLModel, table=True):
    """User account model."""

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    username: str = Field(index=True, unique=True)
    email: str = Field(index=True, unique=True)
    email_verified: bool = Field(default=False)
    hashed_password: str
    avatar_url: str | None = None
    is_active: bool = True
    is_superuser: bool = False
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Project(SQLModel, table=True):
    """Novel project model."""

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    name: str
    description: str | None = None
    owner_id: str | None = Field(default=None, foreign_key="user.id", index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Soft delete fields
    is_deleted: bool = Field(default=False, index=True, description="Soft delete flag")
    deleted_at: datetime | None = Field(default=None, description="Deletion timestamp")

    # Project type: novel, short, screenplay
    project_type: str = Field(
        default=PROJECT_TYPE_NOVEL,
        index=True,
        description="Project type: novel, short, screenplay"
    )

    # Project status fields for AI context awareness
    summary: str | None = Field(
        default=None,
        max_length=PROJECT_STATUS_MAX_LENGTHS["summary"],
        description="Project summary/background",
    )
    current_phase: str | None = Field(
        default=None,
        max_length=PROJECT_STATUS_MAX_LENGTHS["current_phase"],
        description="Current writing phase",
    )
    writing_style: str | None = Field(
        default=None,
        max_length=PROJECT_STATUS_MAX_LENGTHS["writing_style"],
        description="Writing style guidelines",
    )
    notes: str | None = Field(
        default=None,
        max_length=PROJECT_STATUS_MAX_LENGTHS["notes"],
        description="Notes for AI assistant",
    )

    @field_serializer("notes")
    def _serialize_notes(self, notes: str | None) -> str | None:
        # Every API view of the project shows notes without a legacy AI-proposal block.
        return without_legacy_proposals(notes)


class AgentArtifactLedger(SQLModel, table=True):
    """
    Persistent artifact ledger for agent tool outputs.

    Used to keep lightweight references of created/edited/deleted artifacts,
    enabling robust handoff across agent turns.
    """

    __tablename__ = "agent_artifact_ledger"
    __table_args__ = (
        Index(
            "ix_agent_artifact_ledger_project_session_action_created_at",
            "project_id",
            "session_id",
            "action",
            "created_at",
        ),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    session_id: str | None = Field(default=None, foreign_key="chat_session.id", index=True)
    user_id: str | None = Field(default=None, foreign_key="user.id", index=True)
    action: str = Field(index=True, description="Action type: create_file/edit_file/delete_file/update_project")
    tool_name: str = Field(index=True, description="Tool name that generated this artifact")
    artifact_ref: str = Field(index=True, description="Artifact reference (e.g. file_id, project:xxx)")
    payload: str | None = Field(default=None, description="Optional JSON payload snapshot")
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)


class Snapshot(SQLModel, table=True):
    """
    Version snapshot model.

    Stores complete project state at a point in time.
    Types:
    - auto: Automatic snapshot before AI edits
    - manual: User-created snapshots
    - pre_ai_edit: Before AI edit
    """

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    file_id: str | None = Field(
        default=None, foreign_key="file.id", index=True
    )
    data: str  # JSON string of state
    description: str | None = None  # User-provided description
    snapshot_type: str = "auto"  # 'auto', 'manual', 'pre_ai_edit'
    version: int = Field(default=2)  # Version of snapshot format (1=old with outline_id, 2=new with file_id)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChatSession(SQLModel, table=True):
    """
    Chat session model for AI assistant conversations.

    Each user+project combination can have multiple sessions.
    Sessions store conversation history for context-aware AI responses.
    """

    __tablename__ = "chat_session"
    # 每个 user+project 最多一个 active 会话。与迁移 20260307_230000 建的索引同名同定义，
    # create_all 建的库（测试、新库）因此与 prod 一致；已有库由迁移建，不需要新迁移。
    __table_args__ = (
        Index(
            "uq_chat_session_user_project_active",
            "user_id",
            "project_id",
            unique=True,
            postgresql_where=text("is_active = true"),
            sqlite_where=text("is_active = 1"),
        ),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_id: str = Field(foreign_key="user.id", index=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    title: str = Field(default="AI 助手对话")
    is_active: bool = Field(default=True)
    message_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ChatMessage(SQLModel, table=True):
    """
    Chat message model for storing conversation history.

    Roles:
    - user: User messages
    - assistant: AI responses
    - system: System messages (rarely stored)
    - tool: Tool call results
    """

    __tablename__ = "chat_message"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    session_id: str = Field(foreign_key="chat_session.id", index=True)
    role: str = Field(index=True)  # 'user', 'assistant', 'system', 'tool'
    content: str
    # For tool calls
    tool_calls: str | None = None  # JSON string of tool calls
    tool_call_id: str | None = None  # For tool result messages
    # AI reasoning content (GLM-4.7 thinking mode)
    reasoning_content: str | None = None  # AI reasoning/thinking process
    # Extra data
    message_metadata: str | None = None  # JSON string for additional data
    created_at: datetime = Field(default_factory=datetime.utcnow)


class SystemPromptConfig(SQLModel, table=True):
    """
    System prompt configuration model for different project types.

    Stores AI system prompt configurations that can be dynamically loaded
    and modified through the admin interface. Each project type can have
    its own customized system prompt configuration.
    """

    __tablename__ = "system_prompt_config"

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    project_type: str = Field(unique=True, index=True, description="Project type: novel, short, screenplay")
    role_definition: str = Field(description="AI role definition for this project type")
    capabilities: str = Field(description="AI capabilities description")
    directory_structure: str | None = Field(default=None, description="Directory structure guidance")
    content_structure: str | None = Field(default=None, description="Content structure guidance")
    file_types: str | None = Field(default=None, description="Supported file types")
    writing_guidelines: str | None = Field(default=None, description="Writing style guidelines")
    include_dialogue_guidelines: bool = Field(default=False, description="Whether to include dialogue guidelines")
    primary_content_type: str | None = Field(default=None, description="Primary content type")
    is_active: bool = Field(default=True, index=True, description="Whether this config is active")
    version: int = Field(default=1, description="Configuration version")
    created_by: str | None = Field(default=None, foreign_key="user.id", description="Creator user ID")
    updated_by: str | None = Field(default=None, foreign_key="user.id", description="Last updater user ID")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
