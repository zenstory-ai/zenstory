"""Shared utilities for formatting material entities to markdown."""

from sqlalchemy import or_
from sqlmodel import Session, select

from models.material_models import (
    Character,
    CharacterRelationship,
    GoldenFinger,
    Story,
    StoryLine,
    WorldView,
)
from services.material.export_service import (
    enum_label as _enum_label,
)
from services.material.export_service import (
    parse_json_list as _safe_json_parse,
)

# Heading/label text per language. Keep "zh" the default: the agent context
# assembler calls these formatters without a language.
_TEXT: dict[str, dict[str, str]] = {
    "zh": {
        "type": "类型",
        "aliases": "别名",
        "description": "描述",
        "source": "参考来源",
        "worldview": "世界观设定",
        "power_system": "力量体系",
        "world_structure": "世界结构",
        "factions": "主要势力",
        "special_rules": "特殊规则",
        "evolution": "进化历程",
        "main_characters": "主要角色",
        "themes": "主题",
        "stories": "包含剧情",
        "synopsis": "剧情概述",
        "core_objective": "核心目标",
        "core_conflict": "核心冲突",
        "story_type": "剧情类型",
        "chapter_range": "章节范围",
        "relationships": "角色关系",
        "relationship": "关系",
        "sentiment": "态度",
        "unknown": "未知",
        "none": "无",
        "other": "其他",
        "list_sep": "、",
    },
    "en": {
        "type": "Type",
        "aliases": "Aliases",
        "description": "Description",
        "source": "Source",
        "worldview": "World building",
        "power_system": "Power system",
        "world_structure": "World structure",
        "factions": "Major factions",
        "special_rules": "Special rules",
        "evolution": "Evolution",
        "main_characters": "Main characters",
        "themes": "Themes",
        "stories": "Stories",
        "synopsis": "Synopsis",
        "core_objective": "Core objective",
        "core_conflict": "Core conflict",
        "story_type": "Story type",
        "chapter_range": "Chapter range",
        "relationships": "Character relationships",
        "relationship": "Relationship",
        "sentiment": "Attitude",
        "unknown": "Unknown",
        "none": "None",
        "other": "Other",
        "list_sep": ", ",
    },
}

def _text(lang: str) -> dict[str, str]:
    return _TEXT["en"] if lang == "en" else _TEXT["zh"]


def _source_line(novel_title: str, lang: str) -> str:
    quoted = f"*{novel_title}*" if lang == "en" else f"《{novel_title}》"
    return f"---\n> {_text(lang)['source']}: {quoted}\n"


def format_character_to_markdown(
    character: Character, novel_title: str, lang: str = "zh"
) -> tuple[str, str]:
    """
    Format character to markdown.

    Returns:
        tuple of (title, markdown)
    """
    text = _text(lang)
    aliases = _safe_json_parse(character.aliases)

    title = character.name
    markdown = f"# {character.name}\n\n"
    markdown += f"**{text['type']}**: {_enum_label('archetype', character.archetype, lang)}\n"
    if aliases:
        markdown += f"**{text['aliases']}**: {text['list_sep'].join(str(a) for a in aliases)}\n"
    markdown += f"\n## {text['description']}\n{character.description or text['none']}\n\n"
    markdown += _source_line(novel_title, lang)

    return title, markdown


def format_worldview_to_markdown(
    world_view: WorldView, novel_title: str, lang: str = "zh"
) -> tuple[str, str]:
    """
    Format worldview to markdown.

    Returns:
        tuple of (title, markdown)
    """
    text = _text(lang)
    factions = _safe_json_parse(world_view.key_factions)
    factions_text = ""
    for faction in factions:
        if isinstance(faction, dict):
            factions_text += f"- {faction.get('name', text['unknown'])}\n"
        else:
            factions_text += f"- {faction}\n"

    title = text["worldview"]
    markdown = f"# {title}\n\n"
    markdown += f"## {text['power_system']}\n{world_view.power_system or text['none']}\n\n"
    markdown += f"## {text['world_structure']}\n{world_view.world_structure or text['none']}\n\n"
    if factions_text:
        markdown += f"## {text['factions']}\n{factions_text}\n"
    markdown += f"## {text['special_rules']}\n{world_view.special_rules or text['none']}\n\n"
    markdown += _source_line(novel_title, lang)

    return title, markdown


def format_goldenfinger_to_markdown(
    golden_finger: GoldenFinger, novel_title: str, lang: str = "zh"
) -> tuple[str, str]:
    """
    Format golden finger to markdown.

    Returns:
        tuple of (title, markdown)
    """
    text = _text(lang)
    evolution_stages = _safe_json_parse(golden_finger.evolution_history)
    evolution_text = ""
    for i, stage in enumerate(evolution_stages, 1):
        if isinstance(stage, dict):
            evolution_text += f"{i}. {stage.get('description', text['unknown'])}\n"
        else:
            evolution_text += f"{i}. {stage}\n"

    title = golden_finger.name
    markdown = f"# {golden_finger.name}\n\n"
    markdown += f"**{text['type']}**: {_enum_label('golden_finger_type', golden_finger.type, lang)}\n\n"
    markdown += f"## {text['description']}\n{golden_finger.description or text['none']}\n\n"
    if evolution_text:
        markdown += f"## {text['evolution']}\n{evolution_text}\n"
    markdown += _source_line(novel_title, lang)

    return title, markdown


def format_storyline_to_markdown(
    storyline: StoryLine, session: Session, novel_title: str, lang: str = "zh"
) -> tuple[str, str]:
    """
    Format storyline to markdown.

    Returns:
        tuple of (title, markdown)
    """
    text = _text(lang)
    sep = text["list_sep"]

    main_chars = _safe_json_parse(storyline.main_characters)
    main_chars_text = sep.join(str(c) for c in main_chars) if main_chars else ""

    themes = _safe_json_parse(storyline.themes)
    themes_text = sep.join(str(t) for t in themes) if themes else ""

    # Get stories under this storyline
    stories = session.exec(
        select(Story)
        .where(Story.story_line_id == storyline.id)
        .where(or_(Story.novel_id.is_(None), Story.novel_id == storyline.novel_id))
    ).all()
    stories_text = ""
    for story in stories:
        stories_text += f"- {story.title}\n"

    title = storyline.title
    markdown = f"# {storyline.title}\n\n"
    markdown += f"{storyline.description or text['none']}\n\n"
    if main_chars_text:
        markdown += f"## {text['main_characters']}\n{main_chars_text}\n\n"
    if themes_text:
        markdown += f"## {text['themes']}\n{themes_text}\n\n"
    if stories_text:
        markdown += f"## {text['stories']}\n{stories_text}\n"
    markdown += _source_line(novel_title, lang)

    return title, markdown


def format_story_to_markdown(story: Story, novel_title: str, lang: str = "zh") -> tuple[str, str]:
    """
    Format story to markdown.

    Returns:
        tuple of (title, markdown)
    """
    text = _text(lang)
    themes = _safe_json_parse(story.themes)
    themes_text = text["list_sep"].join(str(t) for t in themes) if themes else (story.themes or "")

    title = story.title
    markdown = f"# {story.title}\n\n"
    markdown += f"## {text['synopsis']}\n{story.synopsis or text['none']}\n\n"
    markdown += f"## {text['core_objective']}\n{story.core_objective or text['none']}\n\n"
    markdown += f"## {text['core_conflict']}\n{story.core_conflict or text['none']}\n\n"
    markdown += f"## {text['story_type']}\n{_enum_label('story_type', story.story_type, lang)}\n\n"
    markdown += f"## {text['chapter_range']}\n{story.chapter_range or text['unknown']}\n\n"
    if themes_text:
        markdown += f"## {text['themes']}\n{themes_text}\n\n"
    markdown += _source_line(novel_title, lang)

    return title, markdown


def format_relationship_to_markdown(
    novel_id: int,
    session: Session,
    novel_title: str,
    relationship_id: int | None = None,
    lang: str = "zh",
) -> tuple[str, str]:
    """
    Format all relationships for a novel to markdown.

    Returns:
        tuple of (title, markdown)
    """
    from sqlalchemy.orm import aliased

    text = _text(lang)
    CharacterA = aliased(Character, name="character_a")
    CharacterB = aliased(Character, name="character_b")

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
    if relationship_id is not None:
        stmt = stmt.where(CharacterRelationship.id == relationship_id)

    results = session.exec(stmt).all()

    if relationship_id is not None and len(results) == 1:
        _, char_a_name, char_b_name = results[0]
        title = f"{char_a_name} ↔ {char_b_name}"
    else:
        title = text["relationships"]
    markdown = f"# {text['relationships']}\n\n"
    for rel, char_a_name, char_b_name in results:
        relationship_label = _enum_label("relationship_type", rel.relationship_type, lang)
        markdown += f"## {char_a_name} ↔ {char_b_name}\n"
        markdown += f"- **{text['relationship']}**: {relationship_label}\n"
        if rel.sentiment:
            markdown += f"- **{text['sentiment']}**: {_enum_label('sentiment', rel.sentiment, lang)}\n"
        if rel.description:
            markdown += f"- {rel.description}\n"
        markdown += "\n"
    markdown += _source_line(novel_title, lang)

    return title, markdown
