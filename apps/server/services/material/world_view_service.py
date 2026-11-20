"""
WorldView service - SQLModel version.
Handles WorldView upsert and query operations.
"""
from __future__ import annotations

from typing import Any

from sqlmodel import Session, select

from models.material_models import WorldView

from .serialization import serialize_optional_json


class WorldViewService:
    """WorldView domain service using SQLModel patterns."""

    def get_by_novel(
        self, session: Session, novel_id: int
    ) -> WorldView | None:
        """Get world view for a novel (one-to-one relationship)."""
        statement = select(WorldView).where(WorldView.novel_id == novel_id)
        return session.exec(statement).first()

    def upsert(
        self, session: Session, novel_id: int, data: dict[str, Any]
    ) -> str:
        """Upsert world view for a novel. Returns 'created' or 'updated'."""
        existing = self.get_by_novel(session, novel_id)
        if existing:
            existing.power_system = data.get("power_system") or existing.power_system
            existing.world_structure = (
                data.get("world_structure") or existing.world_structure
            )
            existing.key_factions = serialize_optional_json(data.get("key_factions")) or existing.key_factions
            existing.special_rules = serialize_optional_json(data.get("special_rules")) or existing.special_rules
            session.add(existing)
            session.flush()
            return "updated"
        new_wv = WorldView(
            novel_id=novel_id,
            power_system=data.get("power_system"),
            world_structure=data.get("world_structure"),
            key_factions=serialize_optional_json(data.get("key_factions")),
            special_rules=serialize_optional_json(data.get("special_rules")),
        )
        session.add(new_wv)
        session.flush()
        return "created"
