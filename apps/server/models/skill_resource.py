"""
Skill resource database model.

Stores the text files that ship with a standard skill package
(`references/*`, `assets/*`, `examples/*`, `templates/*`). Exactly one owner: a UserSkill or a PublicSkill.
Scripts and binary files are never stored (zenstory never executes skill code).
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlmodel import Column, Field, SQLModel, Text

from .utils import generate_uuid


class SkillResource(SQLModel, table=True):
    """A single text resource file belonging to a skill."""

    __tablename__ = "skill_resource"
    __table_args__ = (
        CheckConstraint(
            "(user_skill_id IS NULL) <> (public_skill_id IS NULL)",
            name="ck_skill_resource_single_owner",
        ),
        UniqueConstraint("user_skill_id", "path", name="uq_skill_resource_user_skill_path"),
        UniqueConstraint("public_skill_id", "path", name="uq_skill_resource_public_skill_path"),
    )

    id: str = Field(default_factory=generate_uuid, primary_key=True)
    user_skill_id: str | None = Field(
        default=None,
        sa_column=Column(
            String,
            ForeignKey("user_skill.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
    )
    public_skill_id: str | None = Field(
        default=None,
        sa_column=Column(
            String,
            ForeignKey("public_skill.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
    )

    # Relative path inside the skill package, e.g. "references/style.md"
    path: str = Field(max_length=255)
    content: str = Field(sa_column=Column(Text, nullable=False))
    # UTF-8 byte length of content
    size: int = Field(default=0)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
