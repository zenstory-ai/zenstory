"""
Context-related schemas for the agent module.

Defines context items, priorities, and assembled context data
for intelligent context management.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ContextPriority(StrEnum):
    """
    Context priority levels for budget allocation.

    Priority order (highest to lowest):
    - CRITICAL: Must include (current focus, essential constraints)
    - CONSTRAINT: Constraining text (character settings, timeline, style rules)
    - RELEVANT: Relevant materials (retrieved snippets)
    - INSPIRATION: Inspirational text (general references)
    """

    CRITICAL = "critical"
    """Must include - current focus content, essential constraints."""

    CONSTRAINT = "constraint"
    """Constraining text - character settings, high-importance lore, timeline."""

    RELEVANT = "relevant"
    """Relevant materials - retrieved snippets, related outlines."""

    INSPIRATION = "inspiration"
    """Inspirational text - low-importance lore, general references."""

    @classmethod
    def get_budget_allocation(cls) -> dict["ContextPriority", float]:
        """
        Get default budget allocation percentages.

        Returns:
            Dict mapping priority to percentage (0.0-1.0)
        """
        return {
            cls.CRITICAL: 0.30,      # 30% for focus content
            cls.CONSTRAINT: 0.35,    # 35% for constraints
            cls.RELEVANT: 0.25,      # 25% for relevant snippets
            cls.INSPIRATION: 0.10,   # 10% for inspiration
        }

    @classmethod
    def priority_order(cls) -> list["ContextPriority"]:
        """Get priorities in order from highest to lowest."""
        return [
            cls.CRITICAL,
            cls.CONSTRAINT,
            cls.RELEVANT,
            cls.INSPIRATION,
        ]


class ContextItemType(StrEnum):
    """Types of context items."""

    OUTLINE = "outline"
    """Chapter/scene outline."""

    DRAFT = "draft"
    """Draft content."""

    CHARACTER = "character"
    """Character profile."""

    LORE = "lore"
    """World-building/lore entry."""

    SNIPPET = "snippet"
    """Reference snippet."""


class ContextItem(BaseModel):
    """
    A single context item with metadata.

    Represents a piece of context (outline, character, lore, snippet)
    that can be included in the AI prompt.
    """

    id: str = Field(..., description="Item ID (UUID string)")

    type: str = Field(..., description="Item type: outline, character, lore, snippet")

    title: str = Field(..., description="Item title/name")

    content: str = Field(..., description="Item content")

    relevance_score: float | None = Field(
        default=None,
        description="Relevance score (0.0 to 1.0)"
    )

    priority: ContextPriority = Field(
        default=ContextPriority.INSPIRATION,
        description="Priority level"
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Additional metadata"
    )

    @property
    def token_estimate(self) -> int:
        """Estimate token count for this item."""
        # Rough estimate: 1 token ≈ 4 characters for Chinese/English mix
        text = f"{self.title}\n{self.content}"
        return max(1, len(text) // 4)

    @property
    def is_focus(self) -> bool:
        """Check if this is the focus item."""
        return bool(self.metadata.get("is_focus", False))

    @property
    def is_truncated(self) -> bool:
        """Check if this item was truncated."""
        return bool(self.metadata.get("truncated", False))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        from datetime import datetime

        # 防御性检查：确保metadata中不包含datetime对象
        safe_metadata = self.metadata
        if safe_metadata:
            # 简单的datetime转换（递归处理嵌套结构）
            def clean_datetime(obj):
                if isinstance(obj, datetime):
                    return obj.isoformat()
                elif isinstance(obj, dict):
                    return {k: clean_datetime(v) for k, v in obj.items()}
                elif isinstance(obj, list):
                    return [clean_datetime(item) for item in obj]
                return obj

            safe_metadata = clean_datetime(safe_metadata)

        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "content": self.content,
            "relevance_score": self.relevance_score,
            "priority": self.priority.value,
            "metadata": safe_metadata,
        }

    @classmethod
    def from_outline(
        cls,
        id: str,
        title: str,
        content: str,
        is_focus: bool = False,
        relation: str | None = None,
    ) -> "ContextItem":
        """Create from outline data."""
        priority = ContextPriority.CRITICAL if is_focus else ContextPriority.CONSTRAINT
        return cls(
            id=id,
            type="outline",
            title=title,
            content=content,
            relevance_score=1.0 if is_focus else 0.8,
            priority=priority,
            metadata={"is_focus": is_focus, "relation": relation},
        )

    @classmethod
    def from_character(
        cls,
        id: str,
        name: str,
        profile: str,
    ) -> "ContextItem":
        """Create from character data."""
        return cls(
            id=id,
            type="character",
            title=name,
            content=profile,
            relevance_score=0.7,
            priority=ContextPriority.CONSTRAINT,
            metadata={},
        )

    @classmethod
    def from_lore(
        cls,
        id: str,
        title: str,
        content: str,
        category: str | None = None,
        importance: str | None = None,
    ) -> "ContextItem":
        """Create from lore data."""
        # High importance lore is constraint, others are inspiration
        priority = (
            ContextPriority.CONSTRAINT
            if importance == "high"
            else ContextPriority.INSPIRATION
        )
        relevance = {"high": 0.8, "medium": 0.6, "low": 0.4}.get(importance or "", 0.5)

        return cls(
            id=id,
            type="lore",
            title=f"{category} - {title}" if category else title,
            content=content,
            relevance_score=relevance,
            priority=priority,
            metadata={"category": category, "importance": importance},
        )

    @classmethod
    def from_snippet(
        cls,
        id: str,
        title: str,
        content: str,
        relevance_score: float,
        source: str | None = None,
    ) -> "ContextItem":
        """Create from snippet data."""
        priority = (
            ContextPriority.RELEVANT
            if relevance_score > 0.5
            else ContextPriority.INSPIRATION
        )
        return cls(
            id=id,
            type="snippet",
            title=title,
            content=content,
            relevance_score=relevance_score,
            priority=priority,
            metadata={"source": source},
        )

    @classmethod
    def from_quote(
        cls,
        id: str,
        text: str,
        file_title: str,
    ) -> "ContextItem":
        """Create from user-selected text quote."""
        return cls(
            id=id,
            type="quote",
            title=f"引用自: {file_title}",
            content=text,
            relevance_score=1.0,
            priority=ContextPriority.CRITICAL,
            metadata={"file_title": file_title, "is_quote": True},
        )


class ContextData(BaseModel):
    """
    Assembled context data.

    Contains formatted context string and metadata about
    the context assembly process.
    """

    context: str = Field(..., description="Formatted context string for prompt")

    blocks: list[dict[str, str]] = Field(
        default_factory=list,
        description="The same content as keyed blocks (header + one per item) for the context snapshot",
    )

    items: list[dict[str, Any]] = Field(
        default_factory=list,
        description="List of context items used"
    )

    refs: list[str] = Field(
        default_factory=list,
        description="Referenced item IDs"
    )

    token_estimate: int = Field(
        default=0,
        description="Estimated token count"
    )

    original_item_count: int = Field(
        default=0,
        description="Number of items before trimming"
    )

    trimmed_item_count: int = Field(
        default=0,
        description="Number of items after trimming"
    )

    budget_used: dict[str, int] = Field(
        default_factory=dict,
        description="Token budget used per priority"
    )
