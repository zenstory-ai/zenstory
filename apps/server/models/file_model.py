"""
Unified File model - Everything is a file.

This replaces separate models (Outline, Character, Lore, Draft, Snippet)
with a single File model that can represent any file type.

File types:
- outline: 章节大纲
- draft: 草稿内容
- character: 角色档案
- lore: 世界设定
- snippet: 文本素材
- script: 剧本内容（短剧专用）
- document: 通用文档（File.file_type 与 create_file 工具的默认类型）
- folder: 文件夹（用于组织）
"""

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import event, inspect
from sqlmodel import Field, Relationship, SQLModel

from utils.text_metrics import count_words

from .utils import generate_uuid


class File(SQLModel, table=True):
    """
    Unified file model for all entity types.

    All project content (outlines, characters, lores, etc.) is stored as Files.
    The file_type field distinguishes the semantic meaning, and metadata
    stores type-specific attributes.
    """

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)

    # Basic file attributes
    title: str = Field(index=True, description="文件名/标题")
    content: str = Field(default="", description="文件内容")
    file_type: str = Field(
        default="document",
        index=True,
        description="文件类型：outline/draft/character/lore/snippet/script/document/folder",
    )

    # Hierarchy and ordering
    parent_id: str | None = Field(
        default=None,
        foreign_key="file.id",
        index=True,
        description="父文件ID（用于文件夹结构）",
    )
    order: int = Field(default=0, index=True, description="排序")

    # Metadata for type-specific attributes
    file_metadata: str | None = Field(
        default=None, description="JSON元数据：存储类型特定的扩展字段"
    )

    # Timestamps
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # Soft delete
    is_deleted: bool = Field(default=False, index=True, description="软删除标记")
    deleted_at: datetime | None = Field(default=None, description="删除时间")

    # Relationships - self-referential for folder hierarchy
    parent: Optional["File"] = Relationship(
        back_populates="children",
        sa_relationship_kwargs={
            "remote_side": "File.id",
            "foreign_keys": "[File.parent_id]",
        },
    )
    children: list["File"] = Relationship(
        back_populates="parent",
        sa_relationship_kwargs={"foreign_keys": "[File.parent_id]"},
    )

    # Metadata convenience helpers
    def get_metadata(self) -> dict[str, Any]:
        """Parse file_metadata JSON to dict."""
        import json

        if self.file_metadata:
            try:
                result: Any = json.loads(self.file_metadata)
                if isinstance(result, dict):
                    return result
                return {}
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    def set_metadata(self, data: dict[str, Any]) -> None:
        """Set file_metadata from dict."""
        import json

        self.file_metadata = json.dumps(data)

    def get_metadata_field(self, key: str, default: Any = None) -> Any:
        """Get a specific metadata field."""
        return self.get_metadata().get(key, default)

    def set_metadata_field(self, key: str, value: Any) -> None:
        """Set a specific metadata field."""
        metadata = self.get_metadata()
        metadata[key] = value
        self.set_metadata(metadata)


# File type constants (for convenience)
FILE_TYPE_OUTLINE = "outline"
FILE_TYPE_DRAFT = "draft"
FILE_TYPE_CHARACTER = "character"
FILE_TYPE_LORE = "lore"
FILE_TYPE_SNIPPET = "snippet"
FILE_TYPE_SCRIPT = "script"  # Script content (for screenplay)
# 通用文档：File.file_type 的默认值，也是 create_file 工具未显式指定类型时的落库类型。
# 之前只有字符串字面量散落在各处（crud.py / tool_schemas.py），没有对应常量，
# 导致文件清单等按常量枚举类型的地方会整类漏掉 document 文件。
FILE_TYPE_DOCUMENT = "document"
FILE_TYPE_FOLDER = "folder"


# Standard file type metadata schemas
# These help AI understand what metadata fields are expected for each type

FILE_TYPE_METADATA_SCHEMA = {
    FILE_TYPE_OUTLINE: {
        "description": "章节大纲",
        "optional_fields": ["chapter_number", "status", "word_count_target"],
    },
    FILE_TYPE_DRAFT: {
        "description": "草稿内容",
        "optional_fields": ["version", "is_current", "word_count"],
    },
    FILE_TYPE_CHARACTER: {
        "description": "角色档案",
        "optional_fields": ["age", "gender", "role", "personality", "appearance"],
    },
    FILE_TYPE_LORE: {
        "description": "世界设定",
        "optional_fields": ["category", "importance", "tags"],
    },
    FILE_TYPE_SNIPPET: {
        "description": "文本素材",
        "optional_fields": ["source", "tags", "importance"],
    },
    FILE_TYPE_SCRIPT: {
        "description": "剧本内容（短剧专用）",
        "optional_fields": ["episode_number", "scene_count", "duration"],
    },
    FILE_TYPE_DOCUMENT: {
        "description": "通用文档（默认类型）",
        "optional_fields": ["tags", "word_count"],
    },
    FILE_TYPE_FOLDER: {
        "description": "文件夹（用于组织）",
        "optional_fields": [],
    },
}


# ---------------------------------------------------------------------------
# 正文字数缓存
#
# 作者保存、AI 编辑/流式写入、上传、恢复版本都会改 content，以前只有作者保存
# 和上传会更新 file_metadata.word_count，AI 写过的文件缓存一直是旧值。现在正文类
# 文件任何改到 content 的 flush 都在这里统一重算，并打上 word_count_rev；读取方只
# 信任带当前 rev 的缓存，没有或旧口径的缓存按需重算（见 cached_word_count）。
# ---------------------------------------------------------------------------
WORD_COUNT_REV = 2


def stamp_word_count(file: "File") -> int:
    """按编辑器口径重算并写入 file_metadata.word_count，返回字数。"""
    word_count = count_words(file.content)
    metadata = file.get_metadata()
    metadata["word_count"] = word_count
    metadata["word_count_rev"] = WORD_COUNT_REV
    file.set_metadata(metadata)
    return word_count


def cached_word_count(raw_metadata: str | None) -> int | None:
    """当前口径的缓存字数；没有缓存或是旧口径时返回 None（调用方据 content 重算）。"""
    if not raw_metadata:
        return None
    try:
        metadata = json.loads(raw_metadata)
    except (TypeError, ValueError):
        return None
    if not isinstance(metadata, dict) or metadata.get("word_count_rev") != WORD_COUNT_REV:
        return None
    value = metadata.get("word_count")
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


# 只管正文类文件；新建的文件没有当前口径的缓存，读取时按需重算即可。
_WORD_COUNTED_FILE_TYPES = frozenset({FILE_TYPE_DRAFT, FILE_TYPE_SCRIPT})


@event.listens_for(File, "before_update")
def _stamp_word_count_on_update(_mapper, _connection, target: "File") -> None:
    if target.file_type in _WORD_COUNTED_FILE_TYPES and inspect(target).attrs.content.history.has_changes():
        stamp_word_count(target)
