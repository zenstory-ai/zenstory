"""
Material library models for novel decomposition.

Defines SQLModel entities for the material library feature:
- Novel: Main novel table with user isolation
- Chapter: Chapter content and summaries
- Plot: Plot points within chapters
- Story: Cross-chapter story arcs
- StoryLine: Story line aggregations
- StoryPlotLink: Story-plot relationship table
- Character: Character entities
- CharacterRelationship: Character relationships
- GoldenFinger: Special abilities/systems
- WorldView: World-building settings
- IngestionJob: Import task tracking
- ProcessCheckpoint: Checkpoint for resume
- EventTimeline: Event timeline
"""

from datetime import datetime
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel

from config.datetime_utils import utcnow

# ============ Core Models ============

class Novel(SQLModel, table=True):
    """Novel main table with user isolation."""

    __tablename__ = "novels"

    id: int | None = Field(default=None, primary_key=True)
    user_id: str = Field(index=True)  # User isolation (critical field)
    title: str = Field(index=True, max_length=500)
    author: str | None = Field(default=None, max_length=200)
    synopsis: str | None = None
    source_meta: str | None = None  # JSON: {file_path, file_size, encoding, md5_checksum}
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    deleted_at: datetime | None = Field(default=None, index=True)  # Soft delete

    # Relationships
    chapters: list["Chapter"] = Relationship(back_populates="novel")
    characters: list["Character"] = Relationship(back_populates="novel")
    story_lines: list["StoryLine"] = Relationship(back_populates="novel")
    golden_fingers: list["GoldenFinger"] = Relationship(back_populates="novel")
    world_view: Optional["WorldView"] = Relationship(back_populates="novel")


class Chapter(SQLModel, table=True):
    """Chapter content and metadata."""

    __tablename__ = "chapters"

    id: int | None = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    chapter_number: int
    title: str = Field(max_length=500)
    summary: str | None = None
    original_content: str | None = None
    source_path: str | None = Field(default=None, max_length=1000)
    content_hash: str | None = Field(default=None, index=True, max_length=64)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    novel: Optional["Novel"] = Relationship(back_populates="chapters")
    plots: list["Plot"] = Relationship(back_populates="chapter")


class Plot(SQLModel, table=True):
    """Plot points (10-15 key events per chapter)."""

    __tablename__ = "plots"

    id: int | None = Field(default=None, primary_key=True)
    chapter_id: int = Field(foreign_key="chapters.id", index=True)
    index: int  # Index within chapter (starting from 0)
    plot_type: str = Field(max_length=50)  # CONFLICT/TURNING_POINT/REVEAL/ACTION/DIALOGUE/SETUP/RESOLUTION/OTHER
    description: str  # 客观白描
    characters: str | None = None  # JSON: 涉及的核心人物列表，如：["张三", "李四"]
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    chapter: Optional["Chapter"] = Relationship(back_populates="plots")


class Story(SQLModel, table=True):
    """剧情（跨章聚合，有完整起承转合）"""

    __tablename__ = "stories"

    id: int | None = Field(default=None, primary_key=True)
    # 所属小说。数据库层可为空（兼容历史数据），所有新写入必须设置。
    novel_id: int | None = Field(default=None, foreign_key="novels.id", index=True)
    story_line_id: int | None = Field(default=None, foreign_key="story_lines.id", index=True)
    title: str = Field(max_length=500)
    synopsis: str | None = None  # 100-300字剧情概述
    core_objective: str | None = None  # 核心目标
    core_conflict: str | None = None  # 核心冲突
    story_type: str | None = Field(default=None, max_length=50)  # 剧情类型
    themes: str | None = None  # JSON: 主题关键词（<=3个）
    chapter_range: str | None = Field(default=None, max_length=200)  # 章节范围（如 "第1章 - 第5章"）
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    story_line: Optional["StoryLine"] = Relationship(back_populates="stories")


class StoryLine(SQLModel, table=True):
    """Story line (aggregation of multiple stories)."""

    __tablename__ = "story_lines"

    id: int | None = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    title: str = Field(max_length=500)
    description: str | None = None
    main_characters: str | None = None  # JSON: main character ID list
    themes: str | None = None  # JSON: theme keywords
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    novel: Optional["Novel"] = Relationship(back_populates="story_lines")
    stories: list["Story"] = Relationship(back_populates="story_line")


class StoryPlotLink(SQLModel, table=True):
    """Story-plot relationship table (many-to-many)."""

    __tablename__ = "story_plot_links"

    id: int | None = Field(default=None, primary_key=True)
    story_id: int = Field(foreign_key="stories.id", index=True)
    plot_id: int = Field(foreign_key="plots.id", index=True)
    order_index: int | None = None  # Order within story
    role: str | None = Field(default=None, max_length=20)  # SETUP/DEVELOPMENT/CLIMAX/RESOLUTION


# ============ Entity Models ============

class Character(SQLModel, table=True):
    """Character entity."""

    __tablename__ = "characters"

    id: int | None = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    name: str = Field(index=True, max_length=200)
    aliases: str | None = None  # JSON: ["小明", "明哥"]
    description: str | None = None
    archetype: str | None = Field(default=None, max_length=100)  # protagonist/supporting/antagonist
    first_appearance_chapter_id: int | None = Field(default=None, foreign_key="chapters.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    novel: Optional["Novel"] = Relationship(back_populates="characters")


class CharacterRelationship(SQLModel, table=True):
    """Character relationship table."""

    __tablename__ = "character_relationships"

    id: int | None = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    character_a_id: int = Field(foreign_key="characters.id", index=True)
    character_b_id: int = Field(foreign_key="characters.id", index=True)
    relationship_type: str = Field(max_length=100)  # family/mentor/enemy/ally
    sentiment: str | None = Field(default=None, max_length=50)  # friendly/hostile/neutral
    description: str | None = None
    established_at_plot_id: int | None = Field(default=None, foreign_key="plots.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class GoldenFinger(SQLModel, table=True):
    """Golden finger/cheat/system."""

    __tablename__ = "golden_fingers"

    id: int | None = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    name: str = Field(max_length=200)
    type: str | None = Field(default=None, max_length=50)  # system/space/rebirth/special_ability
    description: str | None = None
    first_appearance_chapter_id: int | None = Field(default=None, foreign_key="chapters.id")
    evolution_history: str | None = None  # JSON: ability evolution records
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    novel: Optional["Novel"] = Relationship(back_populates="golden_fingers")


class WorldView(SQLModel, table=True):
    """World-building settings."""

    __tablename__ = "world_views"

    id: int | None = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", unique=True, index=True)
    power_system: str | None = None  # Power system
    world_structure: str | None = None  # World structure description
    key_factions: str | None = None  # JSON: key factions
    special_rules: str | None = None  # Special rules
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Relationships
    novel: Optional["Novel"] = Relationship(back_populates="world_view")


# ============ Task Tracking Models ============

class IngestionJob(SQLModel, table=True):
    """Novel import task table."""

    __tablename__ = "ingestion_jobs"

    id: int | None = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    source_path: str = Field(max_length=1000)
    status: str = Field(default="pending", index=True, max_length=50)  # pending/processing/completed/failed
    total_chapters: int = Field(default=0)
    processed_chapters: int = Field(default=0)
    stage_progress: str | None = None  # JSON: {stage0: {status, timestamp}, ...}
    error_message: str | None = None
    error_details: str | None = None  # JSON
    correlation_id: str | None = Field(default=None, index=True, max_length=100)  # V3: 关联ID
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    @property
    def progress_percentage(self) -> float:
        """Calculate progress percentage."""
        if self.total_chapters == 0:
            return 0.0
        return (self.processed_chapters / self.total_chapters) * 100

    def update_stage_progress(self, stage: str, status: str, **kwargs):
        """更新阶段进度"""
        import json
        if self.stage_progress is None:
            self.stage_progress = "{}"

        progress_dict = json.loads(self.stage_progress) if isinstance(self.stage_progress, str) else {}
        progress_dict[stage] = {
            "status": status,
            "timestamp": utcnow().isoformat(),
            **kwargs
        }
        self.stage_progress = json.dumps(progress_dict)


class ProcessCheckpoint(SQLModel, table=True):
    """Process checkpoint table (for resume)."""

    __tablename__ = "process_checkpoints"

    id: int | None = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    job_id: int | None = Field(default=None, foreign_key="ingestion_jobs.id", index=True)
    stage: str = Field(max_length=100)  # stage0/stage1/stage2a/stage2b
    stage_status: str = Field(max_length=50)  # pending/processing/completed/failed
    checkpoint_data: str | None = None  # JSON: {completed_chapter_ids, failed_chapter_ids, ...}
    error_message: str | None = None
    retry_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    def mark_failed(self, error: str):
        """标记阶段失败"""
        self.stage_status = "failed"
        self.error_message = error
        self.retry_count += 1

    def can_retry(self, max_retries: int = 3) -> bool:
        """是否可以重试"""
        return self.retry_count < max_retries


class EventTimeline(SQLModel, table=True):
    """Event timeline table."""

    __tablename__ = "event_timelines"

    id: int | None = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    chapter_id: int = Field(foreign_key="chapters.id", index=True)
    plot_id: int = Field(foreign_key="plots.id", index=True)
    rel_order: int  # Relative order position
    time_tag: str | None = Field(default=None, max_length=100)
    uncertain: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)


# ============ V3 Migration Models ============

class CharacterMention(SQLModel, table=True):
    """角色提及记录（章节级，两阶段提取中间表）"""
    __tablename__ = "character_mentions"

    id: int | None = Field(default=None, primary_key=True)
    novel_id: int = Field(foreign_key="novels.id", index=True)
    chapter_id: int = Field(foreign_key="chapters.id", index=True)
    character_name: str = Field(index=True, max_length=255)
    aliases: str | None = None  # JSON: 本章使用的别名
    chapter_description: str | None = None  # 本章的局部描述
    importance: str | None = Field(default=None, max_length=50)  # major/supporting/minor
    first_line: int | None = None  # 首次出现行号
    raw_data: str | None = None  # JSON: 原始提取数据
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ChapterContent(SQLModel, table=True):
    """生成内容的章节存储"""
    __tablename__ = "chapter_contents"

    id: int | None = Field(default=None, primary_key=True)
    generated_content_id: int = Field(foreign_key="generated_contents.id", index=True)
    chapter_number: int
    title: str = Field(max_length=500)
    content: str
    word_count: int
    status: str = Field(default="draft", max_length=20)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


# ============ Creation Models ============

class Outline(SQLModel, table=True):
    """用户创作的大纲"""
    __tablename__ = "outlines"

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(index=True, max_length=500)
    description: str | None = None
    main_character_ids: str | None = None  # JSON
    world_view_ids: str | None = None  # JSON
    golden_finger_ids: str | None = None  # JSON
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OutlineItem(SQLModel, table=True):
    """大纲中的剧情条目"""
    __tablename__ = "outline_items"

    id: int | None = Field(default=None, primary_key=True)
    outline_id: int = Field(foreign_key="outlines.id", index=True)
    source_story_id: int = Field(foreign_key="stories.id", index=True)
    order: int
    custom_title: str | None = Field(default=None, max_length=500)
    custom_description: str | None = None
    author_notes: str | None = None
    target_words: int = Field(default=15000)
    custom_world_view_ids: str | None = None  # JSON
    custom_golden_finger_ids: str | None = None  # JSON
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class OutlineCharacter(SQLModel, table=True):
    """剧情条目与角色的关联"""
    __tablename__ = "outline_characters"

    id: int | None = Field(default=None, primary_key=True)
    outline_item_id: int = Field(foreign_key="outline_items.id", index=True)
    character_id: int = Field(foreign_key="characters.id", index=True)
    role: str | None = Field(default=None, max_length=100)


class GeneratedContent(SQLModel, table=True):
    """AI 生成的剧情正文"""
    __tablename__ = "generated_contents"

    id: int | None = Field(default=None, primary_key=True)
    outline_item_id: int = Field(foreign_key="outline_items.id", unique=True, index=True)
    content: str
    word_count: int
    chapter_count: int
    status: str = Field(default="draft", max_length=20)
    task_id: str | None = Field(default=None, index=True, max_length=255)
    generation_status: str = Field(default="pending", max_length=20)
    chapter_plan: str | None = None  # JSON
    generation_params: str | None = None  # JSON
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
