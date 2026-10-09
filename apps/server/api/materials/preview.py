"""
Material preview API endpoints.

Handles material preview operations:
- Get formatted markdown preview for material entities
"""
from fastapi import APIRouter, Depends, Header
from services.auth import get_current_active_user
from sqlmodel import Session, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import get_session
from models import User
from models.material_models import (
    Character,
    CharacterRelationship,
    GoldenFinger,
    Story,
    StoryLine,
    WorldView,
)
from services.material.stories_service import story_in_novel
from utils.logger import get_logger

from .helpers import _get_novel_or_404
from .schemas import MaterialPreviewResponse

logger = get_logger(__name__)

# Router without prefix/tags - will be set by parent router
router = APIRouter()


def _resolve_lang(accept_language: str | None) -> str:
    """Resolve supported language code from Accept-Language header."""
    if not accept_language:
        return "zh"
    lang = accept_language.split(",")[0].split("-")[0].strip().lower()
    return "en" if lang == "en" else "zh"


def _localized_import_hints(entity_type: str, lang: str) -> tuple[str, str]:
    """Return localized (suggested_folder_name, suggested_file_name_prefix)."""
    hints = {
        "zh": {
            "characters": ("角色", "参考"),
            "worldview": ("设定", "世界观"),
            "goldenfingers": ("设定", "金手指"),
            "storylines": ("大纲", "故事线"),
            "stories": ("大纲", "剧情"),
            "relationships": ("设定", "角色关系"),
        },
        "en": {
            "characters": ("Characters", "reference"),
            "worldview": ("World Building", "World Building"),
            "goldenfingers": ("World Building", "Golden Finger"),
            "storylines": ("Outlines", "Storyline"),
            "stories": ("Outlines", "Story"),
            "relationships": ("World Building", "Character Relations"),
        },
    }
    lang_hints = hints.get(lang, hints["zh"])
    return lang_hints.get(entity_type, ("", ""))


# ==================== Preview Endpoint ====================

@router.get("/{novel_id}/{entity_type}/{entity_id}/preview", response_model=MaterialPreviewResponse)
def get_material_preview(
    novel_id: int,
    entity_type: str,
    entity_id: int,
    accept_language: str | None = Header(None, alias="Accept-Language"),
    current_user: User = Depends(get_current_active_user),
    session: Session = Depends(get_session),
):
    """
    Get formatted markdown preview for a material entity.

    entity_type: characters, worldview, goldenfingers, storylines, stories, relationships
    """
    from api.material_utils import (
        format_character_to_markdown,
        format_goldenfinger_to_markdown,
        format_relationship_to_markdown,
        format_story_to_markdown,
        format_storyline_to_markdown,
        format_worldview_to_markdown,
    )

    # Verify novel ownership
    novel = _get_novel_or_404(session, novel_id, current_user.id)

    markdown = ""
    title = ""
    suggested_file_type = ""
    suggested_folder_name = ""
    suggested_file_name = ""
    lang = _resolve_lang(accept_language if isinstance(accept_language, str) else None)

    if entity_type == "characters":
        character = session.get(Character, entity_id)
        if not character or character.novel_id != novel_id:
            raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

        title, markdown = format_character_to_markdown(character, novel.title, lang)
        suggested_file_type = "character"
        suggested_folder_name, file_prefix = _localized_import_hints(entity_type, lang)
        suggested_file_name = f"{character.name}-{file_prefix}"

    elif entity_type == "worldview":
        world_view = session.exec(
            select(WorldView)
            .where(WorldView.novel_id == novel_id)
            .where(WorldView.id == entity_id)
        ).first()
        if not world_view:
            raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

        title, markdown = format_worldview_to_markdown(world_view, novel.title, lang)
        suggested_file_type = "lore"
        suggested_folder_name, file_prefix = _localized_import_hints(entity_type, lang)
        suggested_file_name = f"{file_prefix}-{novel.title}"

    elif entity_type == "goldenfingers":
        golden_finger = session.get(GoldenFinger, entity_id)
        if not golden_finger or golden_finger.novel_id != novel_id:
            raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

        title, markdown = format_goldenfinger_to_markdown(golden_finger, novel.title, lang)
        suggested_file_type = "lore"
        suggested_folder_name, file_prefix = _localized_import_hints(entity_type, lang)
        suggested_file_name = f"{file_prefix}-{golden_finger.name}"

    elif entity_type == "storylines":
        storyline = session.get(StoryLine, entity_id)
        if not storyline or storyline.novel_id != novel_id:
            raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

        title, markdown = format_storyline_to_markdown(storyline, session, novel.title, lang)
        suggested_file_type = "outline"
        suggested_folder_name, file_prefix = _localized_import_hints(entity_type, lang)
        suggested_file_name = f"{file_prefix}-{storyline.title}"

    elif entity_type == "stories":
        story = session.exec(
            select(Story)
            .where(Story.id == entity_id)
            .where(story_in_novel(novel_id))
        ).first()
        if not story:
            raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

        title, markdown = format_story_to_markdown(story, novel.title, lang)
        suggested_file_type = "outline"
        suggested_folder_name, file_prefix = _localized_import_hints(entity_type, lang)
        suggested_file_name = f"{file_prefix}-{story.title}"

    elif entity_type == "relationships":
        relationship = session.get(CharacterRelationship, entity_id)
        if not relationship or relationship.novel_id != novel_id:
            raise APIException(error_code=ErrorCode.FILE_NOT_FOUND, status_code=404)

        title, markdown = format_relationship_to_markdown(
            novel_id, session, novel.title, relationship_id=entity_id, lang=lang
        )
        suggested_file_type = "lore"
        suggested_folder_name, file_prefix = _localized_import_hints(entity_type, lang)
        suggested_file_name = f"{file_prefix}-{title}"

    else:
        raise APIException(
            error_code=ErrorCode.VALIDATION_ERROR,
            status_code=400,
            detail=f"Invalid entity_type: {entity_type}"
        )

    return MaterialPreviewResponse(
        title=title,
        markdown=markdown,
        novel_title=novel.title,
        suggested_file_type=suggested_file_type,
        suggested_folder_name=suggested_folder_name,
        suggested_file_name=suggested_file_name,
    )


__all__ = ["router"]
