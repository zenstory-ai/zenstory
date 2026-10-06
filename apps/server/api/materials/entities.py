"""
Material entity retrieval API endpoints.

Handles all entity-related operations:
- Chapter detail retrieval
- Character list and details
- Story list
- Character relationships
- World view
- Plot list
- Storyline list
- Golden finger list
"""
import json

from fastapi import APIRouter, Depends
from services.auth import get_current_active_user
from sqlalchemy.orm import aliased
from sqlmodel import Session, func, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from models.material_models import (
    Chapter,
    Character,
    CharacterRelationship,
    GoldenFinger,
    Plot,
    Story,
    StoryLine,
    WorldView,
)
from services.material.stories_service import story_in_novel
from utils.logger import get_logger

from .helpers import _get_novel_or_404
from .schemas import (
    ChapterDetailResponse,
    CharacterListItem,
    CharacterRelationshipItem,
    GoldenFingerListItem,
    PlotListItem,
    StoryLineListItem,
    WorldViewResponse,
)

logger = get_logger(__name__)

# Router without prefix/tags - will be set by parent router
router = APIRouter()


# ==================== Chapter Endpoints ====================

@router.get("/{novel_id}/chapters/{chapter_id}", response_model=ChapterDetailResponse)
def get_chapter_detail(
    novel_id: int,
    chapter_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get chapter detail.

    Returns detailed information about a specific chapter including its content and plots.
    """
    # Verify novel ownership and soft delete check
    _get_novel_or_404(session, novel_id, current_user.id)

    # Get chapter
    chapter = session.get(Chapter, chapter_id)
    if not chapter or chapter.novel_id != novel_id:
        raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

    # Count plots
    plots_count = int(
        session.exec(
            select(func.count())
            .select_from(Plot)
            .where(Plot.chapter_id == chapter_id)
        ).one()
    )

    content = chapter.original_content or ""
    return ChapterDetailResponse(
        id=chapter.id,
        novel_id=chapter.novel_id,
        chapter_number=chapter.chapter_number,
        title=chapter.title,
        summary=chapter.summary,
        content=content,
        word_count=len(content),
        created_at=chapter.created_at,
        plots_count=plots_count,
    )


# ==================== Character Endpoints ====================

@router.get("/{novel_id}/characters", response_model=list[CharacterListItem])
def get_characters(
    novel_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get character list.

    Returns all characters extracted from the novel.
    """
    # Verify novel ownership and soft delete check
    _get_novel_or_404(session, novel_id, current_user.id)

    # Get all characters
    characters = session.exec(
        select(Character)
        .where(Character.novel_id == novel_id)
        .order_by(Character.first_appearance_chapter_id.asc())
    ).all()

    first_appearance_chapter_ids = {
        character.first_appearance_chapter_id
        for character in characters
        if character.first_appearance_chapter_id is not None
    }
    first_appearance_chapter_numbers: dict[int, int] = {}
    if first_appearance_chapter_ids:
        first_appearance_chapters = session.exec(
            select(Chapter.id, Chapter.chapter_number)
            .where(Chapter.novel_id == novel_id)
            .where(Chapter.id.in_(first_appearance_chapter_ids))
        ).all()
        first_appearance_chapter_numbers = dict(first_appearance_chapters)

    result = []
    for character in characters:
        # Parse aliases
        aliases = None
        if character.aliases:
            try:
                parsed = json.loads(character.aliases)
                # Ensure it's a list, not a dict
                if isinstance(parsed, list):
                    aliases = parsed
            except Exception:
                pass

        result.append(CharacterListItem(
            id=character.id,
            name=character.name,
            aliases=aliases,
            description=character.description,
            archetype=character.archetype,
            first_appearance_chapter_id=character.first_appearance_chapter_id,
            first_appearance_chapter=first_appearance_chapter_numbers.get(
                character.first_appearance_chapter_id
            ),
        ))

    return result


# ==================== Story Endpoints ====================

@router.get("/{novel_id}/stories")
async def get_stories(
    novel_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session)
):
    """获取剧情列表"""
    # 验证权限和软删除检查
    _get_novel_or_404(session, novel_id, current_user.id)

    # 获取剧情列表
    stories = session.exec(select(Story).where(story_in_novel(novel_id))).all()

    return [
        {
            "id": s.id,
            "title": s.title,
            "synopsis": s.synopsis,
            "core_objective": s.core_objective,
            "core_conflict": s.core_conflict,
            "story_type": s.story_type,
            "chapter_range": s.chapter_range,
            "themes": s.themes,
            "created_at": s.created_at.isoformat() if s.created_at else None
        }
        for s in stories
    ]


# ==================== Relationship Endpoints ====================

@router.get("/{novel_id}/relationships")
def get_character_relationships(
    novel_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get all character relationships for a novel.

    Returns all relationships between characters with their names.
    """
    # Verify novel ownership and soft delete check
    _get_novel_or_404(session, novel_id, current_user.id)

    # Create aliases for self-join on Character table
    CharacterA = aliased(Character, name="character_a")
    CharacterB = aliased(Character, name="character_b")

    # JOIN query: CharacterRelationship + Character (twice)
    stmt = (
        select(
            CharacterRelationship,
            CharacterA.name.label("character_a_name"),
            CharacterB.name.label("character_b_name")
        )
        .join(CharacterA, CharacterRelationship.character_a_id == CharacterA.id)
        .join(CharacterB, CharacterRelationship.character_b_id == CharacterB.id)
        .where(CharacterRelationship.novel_id == novel_id)
    )

    results = session.exec(stmt).all()

    return [
        CharacterRelationshipItem(
            id=rel.id,
            character_a_id=rel.character_a_id,
            character_a_name=char_a_name,
            character_b_id=rel.character_b_id,
            character_b_name=char_b_name,
            relationship_type=rel.relationship_type,
            sentiment=rel.sentiment,
            description=rel.description,
        )
        for rel, char_a_name, char_b_name in results
    ]


# ==================== WorldView Endpoints ====================

@router.get("/{novel_id}/worldview")
def get_world_view(
    novel_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get world view for a novel.

    Returns the world-building settings for the novel (1:1 relationship).
    """

    # Verify novel ownership and soft delete check
    _get_novel_or_404(session, novel_id, current_user.id)

    # Get world view (1:1 relationship)
    world_view = session.exec(
        select(WorldView).where(WorldView.novel_id == novel_id)
    ).first()

    if not world_view:
        return None

    # Parse key_factions JSON
    key_factions = None
    if world_view.key_factions:
        try:
            parsed = json.loads(world_view.key_factions)
            # Handle both list[str] and list[dict] formats
            if isinstance(parsed, list):
                # Convert string list to dict list or use as-is
                key_factions = [{"name": f} for f in parsed] if parsed and isinstance(parsed[0], str) else parsed
        except Exception:
            pass

    return WorldViewResponse(
        id=world_view.id,
        novel_id=world_view.novel_id,
        power_system=world_view.power_system,
        world_structure=world_view.world_structure,
        key_factions=key_factions,
        special_rules=world_view.special_rules,
        created_at=world_view.created_at,
        updated_at=world_view.updated_at,
    )


# ==================== Plot Endpoints ====================

@router.get("/{novel_id}/plots")
def get_plots(
    novel_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get all plots for a novel.

    Returns all plot points extracted from all chapters of the novel.
    """

    # Verify novel ownership and soft delete check
    _get_novel_or_404(session, novel_id, current_user.id)

    # Get all chapters for this novel
    chapter_ids = session.exec(
        select(Chapter.id).where(Chapter.novel_id == novel_id)
    ).all()

    if not chapter_ids:
        return []

    # Get all plots for these chapters
    plots = session.exec(
        select(Plot)
        .where(Plot.chapter_id.in_(chapter_ids))
        .order_by(Plot.chapter_id, Plot.index)
    ).all()

    result = []
    for plot in plots:
        # Parse characters JSON
        characters = None
        if plot.characters:
            try:
                parsed = json.loads(plot.characters)
                # Ensure it's a list, not a dict
                if isinstance(parsed, list):
                    characters = parsed
            except Exception:
                pass

        result.append(PlotListItem(
            id=plot.id,
            chapter_id=plot.chapter_id,
            index=plot.index,
            plot_type=plot.plot_type,
            description=plot.description,
            characters=characters,
        ))

    return result


# ==================== Storyline Endpoints ====================

@router.get("/{novel_id}/storylines")
def get_storylines(
    novel_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get all storylines for a novel.

    Returns all storylines with their story counts.
    """

    # Verify novel ownership and soft delete check
    _get_novel_or_404(session, novel_id, current_user.id)

    # Get all storylines
    storylines = session.exec(
        select(StoryLine).where(StoryLine.novel_id == novel_id)
    ).all()

    # Batch query for story counts
    storyline_ids = [sl.id for sl in storylines]
    story_counts_dict = {}

    if storyline_ids:
        story_counts = session.exec(
            select(Story.story_line_id, func.count(Story.id))
            .where(Story.story_line_id.in_(storyline_ids))
            .where(story_in_novel(novel_id))
            .group_by(Story.story_line_id)
        ).all()
        story_counts_dict = dict(story_counts)

    result = []
    for sl in storylines:
        # Parse JSON fields
        main_characters = None
        if sl.main_characters:
            try:
                parsed = json.loads(sl.main_characters)
                if isinstance(parsed, list):
                    main_characters = parsed
            except Exception:
                pass

        themes = None
        if sl.themes:
            try:
                parsed = json.loads(sl.themes)
                if isinstance(parsed, list):
                    themes = parsed
            except Exception:
                pass

        result.append(StoryLineListItem(
            id=sl.id,
            novel_id=sl.novel_id,
            title=sl.title,
            description=sl.description,
            main_characters=main_characters,
            themes=themes,
            stories_count=story_counts_dict.get(sl.id, 0),
            created_at=sl.created_at,
        ))

    return result


# ==================== GoldenFinger Endpoints ====================

@router.get("/{novel_id}/goldenfingers")
def get_golden_fingers(
    novel_id: int,
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get all golden fingers for a novel.

    Returns all special abilities/systems extracted from the novel.
    """

    # Verify novel ownership and soft delete check
    _get_novel_or_404(session, novel_id, current_user.id)

    # Get all golden fingers
    golden_fingers = session.exec(
        select(GoldenFinger).where(GoldenFinger.novel_id == novel_id)
    ).all()

    result = []
    for gf in golden_fingers:
        # Parse evolution_history JSON
        evolution_history = None
        if gf.evolution_history:
            try:
                parsed = json.loads(gf.evolution_history)
                if isinstance(parsed, list):
                    evolution_history = parsed
            except Exception:
                pass

        result.append(GoldenFingerListItem(
            id=gf.id,
            novel_id=gf.novel_id,
            name=gf.name,
            type=gf.type,
            description=gf.description,
            first_appearance_chapter_id=gf.first_appearance_chapter_id,
            evolution_history=evolution_history,
            created_at=gf.created_at,
        ))

    return result


__all__ = ["router"]
