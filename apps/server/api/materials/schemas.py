"""
Pydantic schemas for materials API.

Contains all request/response models for the material library endpoints.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

# ==================== Type Aliases ====================

# Valid entity types for material operations
MaterialEntityType = Literal["characters", "worldview", "goldenfingers", "storylines", "stories", "relationships"]


# ==================== Upload & Job Schemas ====================

class MaterialUploadResponse(BaseModel):
    """Response for material upload."""
    novel_id: int
    title: str
    job_id: int
    status: str
    message: str


class JobStatusResponse(BaseModel):
    """Job status response."""
    job_id: int
    novel_id: int
    status: str  # pending/processing/completed/completed_with_errors/failed
    total_chapters: int
    processed_chapters: int
    progress_percentage: float
    stage_progress: dict | None
    error_message: str | None  # ERR_* code
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


# ==================== Library Schemas ====================

class MaterialListItem(BaseModel):
    """Material library list item."""
    id: int
    title: str
    author: str | None
    synopsis: str | None
    original_filename: str | None = None
    created_at: datetime
    updated_at: datetime
    status: str | None  # Latest job status (pending/processing/completed/completed_with_errors/failed)
    error_message: str | None = None  # ERR_* code of the latest job failure
    chapters_count: int
    # Effective decomposition stages recorded at flow start (see MaterialDetailResponse).
    enabled_stages: dict[str, bool] | None = None


class MaterialDetailResponse(BaseModel):
    """Material library detail response."""
    id: int
    title: str
    author: str | None
    synopsis: str | None
    source_meta: dict | None
    status: str | None  # IngestionJob status: pending/processing/completed/completed_with_errors/failed
    # ERR_* code of the latest job failure (translated by the frontend).
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    chapters_count: int
    characters_count: int
    story_lines_count: int
    plots_count: int
    stories_count: int
    relationships_count: int
    golden_fingers_count: int
    has_world_view: bool
    # Effective decomposition stages of the latest job, keyed by
    # chapter_summaries/plots/characters/meta/synopsis/stories/storylines/relationships.
    # Older snapshots lack "storylines" (then "stories" covered both).
    # None for jobs created before the snapshot existed (UI keeps legacy behavior).
    enabled_stages: dict[str, bool] | None = None


class LibrarySummaryItem(BaseModel):
    """Library summary item for reference panel."""
    id: int
    title: str
    status: str | None
    counts: dict  # {characters, worldview, golden_fingers, storylines, relationships}


# ==================== Entity Schemas ====================

class ChapterDetailResponse(BaseModel):
    """Chapter detail response."""
    id: int
    novel_id: int
    chapter_number: int
    title: str
    summary: str | None
    content: str | None
    word_count: int
    created_at: datetime
    plots_count: int


class CharacterListItem(BaseModel):
    """Character list item."""
    id: int
    name: str
    aliases: list[str] | None
    description: str | None
    archetype: str | None
    first_appearance_chapter_id: int | None
    first_appearance_chapter: int | None = None


class PlotListItem(BaseModel):
    """Plot list item."""
    id: int
    chapter_id: int
    index: int
    plot_type: str
    description: str
    characters: list[str] | None


class CharacterRelationshipItem(BaseModel):
    """Character relationship item."""
    id: int
    character_a_id: int
    character_a_name: str
    character_b_id: int
    character_b_name: str
    relationship_type: str
    sentiment: str | None
    description: str | None


class StoryLineListItem(BaseModel):
    """StoryLine list item."""
    id: int
    novel_id: int
    title: str
    description: str | None
    main_characters: list[str] | None
    themes: list[str] | None
    stories_count: int
    created_at: datetime


class WorldViewResponse(BaseModel):
    """WorldView response."""
    id: int
    novel_id: int
    power_system: str | None
    world_structure: str | None
    key_factions: list[dict] | None
    special_rules: str | None
    created_at: datetime
    updated_at: datetime


class GoldenFingerListItem(BaseModel):
    """GoldenFinger list item."""
    id: int
    novel_id: int
    name: str
    type: str | None
    description: str | None
    first_appearance_chapter_id: int | None
    evolution_history: list[dict] | None
    created_at: datetime


# ==================== Search Schemas ====================

class MaterialSearchResult(BaseModel):
    """Material search result item."""
    novel_id: int
    novel_title: str
    entity_type: str
    entity_id: int
    name: str


# ==================== Preview Schemas ====================

class MaterialPreviewResponse(BaseModel):
    """Material preview response with formatted markdown."""
    title: str
    markdown: str
    novel_title: str
    suggested_file_type: str  # character, lore, outline, snippet
    suggested_folder_name: str  # 角色, 设定, 大纲, 素材
    suggested_file_name: str  # e.g. "萧炎-参考"


# ==================== Import Schemas ====================

class MaterialImportRequest(BaseModel):
    """Material import request."""
    project_id: str
    novel_id: int
    entity_type: MaterialEntityType  # Strict typing for entity types
    entity_id: int
    file_name: str | None = None  # Optional, uses suggested if not provided
    target_folder_id: str | None = None  # Optional, auto-matches if not provided


class MaterialImportResponse(BaseModel):
    """Material import response."""
    file_id: str
    title: str
    folder_name: str
    file_type: str


class BatchImportItem(BaseModel):
    """Batch import item."""
    novel_id: int
    entity_type: MaterialEntityType  # Strict typing for entity types
    entity_id: int


class BatchImportRequest(BaseModel):
    """Batch import request."""
    project_id: str
    items: list[BatchImportItem]


class BatchImportResult(BaseModel):
    """Batch import result."""
    file_id: str
    title: str
    folder_name: str
    file_type: str


class BatchImportResponse(BaseModel):
    """Batch import response."""
    results: list[BatchImportResult]
    failed_count: int


__all__ = [
    # Type aliases
    "MaterialEntityType",
    # Upload & Job
    "MaterialUploadResponse",
    "JobStatusResponse",
    # Library
    "MaterialListItem",
    "MaterialDetailResponse",
    "LibrarySummaryItem",
    # Entities
    "ChapterDetailResponse",
    "CharacterListItem",
    "PlotListItem",
    "CharacterRelationshipItem",
    "StoryLineListItem",
    "WorldViewResponse",
    "GoldenFingerListItem",
    # Search
    "MaterialSearchResult",
    # Preview
    "MaterialPreviewResponse",
    # Import
    "MaterialImportRequest",
    "MaterialImportResponse",
    "BatchImportItem",
    "BatchImportRequest",
    "BatchImportResult",
    "BatchImportResponse",
]
