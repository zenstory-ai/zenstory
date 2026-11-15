"""
Character mentions service - SQLModel version.
Handles CharacterMention operations for two-phase character extraction.
"""
from __future__ import annotations

import json

from sqlmodel import Session, select

from models.material_models import CharacterMention


class CharacterMentionsService:
    """Character mention service using SQLModel patterns."""

    def get_by_novel(self, session: Session, novel_id: int) -> list[CharacterMention]:
        """Get all character mentions for a novel."""
        return list(session.exec(
            select(CharacterMention).where(CharacterMention.novel_id == novel_id)
        ).all())

    def get_by_character_name_or_alias(
        self, session: Session, novel_id: int, name: str
    ) -> list[CharacterMention]:
        """Find mentions by character name or alias."""
        candidates = list(
            session.exec(
                select(CharacterMention).where(
                    CharacterMention.novel_id == novel_id
                )
            ).all()
        )
        return [
            mention
            for mention in candidates
            if mention.character_name == name or self._alias_matches(mention.aliases, name)
        ]

    def upsert_mention(
        self,
        session: Session,
        novel_id: int,
        chapter_id: int,
        character_name: str,
        data: dict
    ) -> CharacterMention:
        """Update or insert character mention (unique by novel_id + chapter_id + character_name)."""
        existing = session.exec(
            select(CharacterMention).where(
                CharacterMention.novel_id == novel_id,
                CharacterMention.chapter_id == chapter_id,
                CharacterMention.character_name == character_name
            )
        ).first()

        if existing:
            for key, value in data.items():
                if hasattr(existing, key):
                    setattr(existing, key, value)
            session.add(existing)
            session.flush()
            return existing
        mention = CharacterMention(
            novel_id=novel_id,
            chapter_id=chapter_id,
            character_name=character_name,
            **data
        )
        session.add(mention)
        session.flush()
        return mention

    @staticmethod
    def _alias_matches(raw_aliases: str | None, name: str) -> bool:
        """Match aliases exactly instead of relying on SQL substring semantics."""
        if not raw_aliases:
            return False

        try:
            parsed = json.loads(raw_aliases)
        except json.JSONDecodeError:
            parsed = raw_aliases

        if isinstance(parsed, list):
            return name in {str(alias).strip() for alias in parsed if str(alias).strip()}

        if isinstance(parsed, str):
            return name == parsed.strip()

        return False
