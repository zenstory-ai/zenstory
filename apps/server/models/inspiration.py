"""
Inspiration database model (DORMANT).

Retained strictly for Alembic metadata compatibility and historical data
preservation (no DROP TABLE). Runtime code no longer reads or writes this table.
"""

from datetime import datetime

from sqlmodel import Column, Field, SQLModel, Text

from .utils import generate_uuid


class Inspiration(SQLModel, table=True):
    """
    Inspiration model for the project template discovery system.

    Inspirations can be official (from the platform) or community-contributed.
    Community inspirations require admin approval before becoming public.
    """

    __tablename__ = "inspiration"

    id: str = Field(default_factory=generate_uuid, primary_key=True)

    # Inspiration metadata
    name: str = Field(max_length=200, description="Display name of the inspiration")
    description: str | None = Field(
        default=None,
        max_length=1000,
        description="Brief description of the inspiration template"
    )
    cover_image: str | None = Field(
        default=None,
        max_length=500,
        description="URL to cover image for the inspiration"
    )

    # Categorization
    project_type: str = Field(
        default="novel",
        max_length=20,
        description="Project type: novel/short/screenplay"
    )
    tags: str = Field(
        default="[]",
        sa_column=Column(Text),
        description="JSON array of tags for filtering"
    )

    # Template data - complete file tree snapshot
    snapshot_data: str = Field(
        sa_column=Column(Text),
        description="JSON with complete file tree structure"
    )

    # Deduplication for synced data
    source_id: str | None = Field(
        default=None,
        max_length=100,
        description="External source ID for dedup, e.g. 'qimao:1979486'",
    )

    # Source and authorship
    source: str = Field(
        default="official",
        max_length=20,
        description="Source: 'official' or 'community'"
    )
    author_id: str | None = Field(
        default=None,
        foreign_key="user.id",
        description="Author user ID for community inspirations"
    )
    original_project_id: str | None = Field(
        default=None,
        foreign_key="project.id",
        description="Original project ID this inspiration was created from"
    )

    # Review status for community inspirations
    status: str = Field(
        default="approved",
        max_length=20,
        description="Status: pending/approved/rejected"
    )
    reviewed_by: str | None = Field(
        default=None,
        foreign_key="user.id",
        description="Admin who reviewed the inspiration"
    )
    reviewed_at: datetime | None = Field(
        default=None,
        description="When the inspiration was reviewed"
    )
    rejection_reason: str | None = Field(
        default=None,
        max_length=500,
        description="Reason for rejection if rejected"
    )

    # Statistics
    copy_count: int = Field(
        default=0,
        description="Number of times this inspiration has been copied by users"
    )

    # Display and ordering
    sort_order: int = Field(
        default=0,
        description="Sort order for display"
    )
    is_featured: bool = Field(
        default=False,
        description="Whether this inspiration is featured"
    )

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
