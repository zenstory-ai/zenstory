"""One-click plain-text export for extracted material breakdown results."""

from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy import select as sa_select
from sqlalchemy.orm import aliased
from sqlmodel import Session, col, select

from models.material_models import (
    Chapter,
    Character,
    CharacterRelationship,
    GoldenFinger,
    Novel,
    Plot,
    Story,
    StoryLine,
    WorldView,
)
from services.material.stories_service import story_in_novel

_UNSAFE_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# Enum value -> (zh, en) label. Mirrors apps/web/public/locales/{zh,en}/materials.json ("enums").
ENUM_LABELS: dict[str, dict[str, tuple[str, str]]] = {
    "plot_type": {
        "action": ("动作", "Action"),
        "reveal": ("揭示", "Reveal"),
        "dialogue": ("对话", "Dialogue"),
        "setup": ("铺垫", "Setup"),
        "conflict": ("冲突", "Conflict"),
        "turning_point": ("转折", "Turning point"),
        "resolution": ("收束", "Resolution"),
        "other": ("其他", "Other"),
    },
    "story_type": {
        "main": ("主线", "Main plot"),
        "romance": ("感情线", "Romance"),
        "growth": ("成长线", "Growth"),
        "revenge": ("复仇线", "Revenge"),
        "treasure": ("夺宝线", "Treasure hunt"),
        "conflict": ("冲突线", "Conflict"),
        "mystery": ("悬疑线", "Mystery"),
        "cultivation": ("修炼线", "Cultivation"),
        "daily": ("日常线", "Daily life"),
        "crisis": ("危机线", "Crisis"),
        "battle": ("战斗线", "Battle"),
        "exploration": ("探索线", "Exploration"),
        "other": ("其他", "Other"),
    },
    "relationship_type": {
        "family": ("亲人", "Family"),
        "master_disciple": ("师徒", "Mentor & disciple"),
        "friend": ("朋友", "Friends"),
        "enemy": ("敌对", "Enemies"),
        "lover": ("恋人", "Lovers"),
        "colleague": ("同事", "Colleagues"),
        "superior_subordinate": ("上下级", "Superior & subordinate"),
        "business": ("合作", "Business"),
        "mentor": ("师徒", "Mentor & disciple"),
        "ally": ("盟友", "Allies"),
        "other": ("其他", "Other"),
    },
    "golden_finger_type": {
        "system": ("系统", "System"),
        "space": ("随身空间", "Pocket space"),
        "rebirth": ("重生", "Rebirth"),
        "transmigration": ("穿越", "Transmigration"),
        "special_physique": ("特殊体质", "Special physique"),
        "special_ability": ("特殊能力", "Special ability"),
        "artifact": ("法宝", "Artifact"),
        "bloodline": ("血脉", "Bloodline"),
        "other": ("其他", "Other"),
    },
    "archetype": {
        "protagonist": ("主角", "Protagonist"),
        "antagonist": ("反派", "Antagonist"),
        "supporting": ("配角", "Supporting"),
        "minor": ("次要角色", "Minor"),
    },
    "sentiment": {
        "positive": ("正面", "Positive"),
        "negative": ("负面", "Negative"),
        "neutral": ("中立", "Neutral"),
        "complex": ("复杂", "Complex"),
    },
}

_EXPORT_TEXT: dict[str, dict[str, str]] = {
    "zh": {
        "fallback_title": "素材",
        "filename_suffix": "拆解",
        "author": "作者",
        "synopsis": "素材梗概",
        "chapter_summaries": "章节梗概",
        "characters": "角色",
        "archetype": "角色定位",
        "aliases": "别名",
        "first_appearance": "首次出现",
        "worldview": "世界观",
        "power_system": "力量体系",
        "world_structure": "世界结构",
        "factions": "关键势力",
        "leader": "领袖",
        "territory": "领地",
        "special_rules": "特殊规则",
        "golden_fingers": "金手指",
        "type": "类型",
        "evolution": "进化历史",
        "storylines": "故事线",
        "main_characters": "主要角色",
        "themes": "主题",
        "stories": "剧情",
        "story_type": "剧情类型",
        "chapter_range": "章节范围",
        "core_objective": "核心目标",
        "core_conflict": "核心冲突",
        "relationships": "角色关系",
        "relationship": "关系",
        "sentiment": "情感倾向",
        "plots": "情节点",
        "involved_characters": "涉及角色",
        "unknown": "未知",
        "other": "其他",
        "list_sep": "、",
    },
    "en": {
        "fallback_title": "material",
        "filename_suffix": "breakdown",
        "author": "Author",
        "synopsis": "Material Synopsis",
        "chapter_summaries": "Chapter Summaries",
        "characters": "Characters",
        "archetype": "Role",
        "aliases": "Aliases",
        "first_appearance": "First appearance",
        "worldview": "Worldbuilding",
        "power_system": "Power System",
        "world_structure": "World Structure",
        "factions": "Key Factions",
        "leader": "Leader",
        "territory": "Territory",
        "special_rules": "Special Rules",
        "golden_fingers": "Special Powers",
        "type": "Type",
        "evolution": "Evolution",
        "storylines": "Storylines",
        "main_characters": "Main characters",
        "themes": "Themes",
        "stories": "Plotlines",
        "story_type": "Story type",
        "chapter_range": "Chapter range",
        "core_objective": "Core objective",
        "core_conflict": "Core conflict",
        "relationships": "Relationships",
        "relationship": "Relationship",
        "sentiment": "Sentiment",
        "plots": "Plots",
        "involved_characters": "Characters",
        "unknown": "Unknown",
        "other": "Other",
        "list_sep": ", ",
    },
}


def enum_label(kind: str, value: str | None, lang: str = "zh") -> str:
    """Resolve localized label for an extracted enum value."""
    labels_text = _EXPORT_TEXT["en"] if lang == "en" else _EXPORT_TEXT["zh"]
    if not value or not value.strip():
        return labels_text["unknown"]
    labels = ENUM_LABELS[kind].get(value.strip().lower())
    if labels:
        return labels[1] if lang == "en" else labels[0]
    if value.isascii():
        return labels_text["other"]
    return value


def parse_json_list(json_str: str | None) -> list[Any]:
    """Parse a JSON array column, returning an empty list on null or non-list JSON."""
    if not json_str:
        return []
    try:
        parsed = json.loads(json_str)
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []


def _parse_themes(raw: str | None) -> list[str]:
    if not raw:
        return []
    parsed = parse_json_list(raw)
    if parsed:
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in raw.split(",") if part.strip()]


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None


def _chapter_label(number: int, lang: str) -> str:
    return f"Chapter {number}" if lang == "en" else f"第 {number} 章"


def _chapter_heading(number: int, title: str | None, lang: str) -> str:
    cleaned = (title or "").strip()
    if not cleaned:
        return _chapter_label(number, lang)
    lowered = cleaned.lower()
    if "第" in cleaned or "chapter" in lowered or str(number) in cleaned:
        return cleaned
    return f"{_chapter_label(number, lang)} · {cleaned}"


def _trial_coverage_note(source_meta_raw: str | None, lang: str) -> str | None:
    if not source_meta_raw:
        return None
    try:
        meta = json.loads(source_meta_raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(meta, dict):
        return None

    included = _positive_int(meta.get("chapter_count"))
    source = _positive_int(meta.get("source_chapter_count"))
    limit = _positive_int(meta.get("trial_chapter_limit"))
    is_known_complete = included is not None and source is not None and included == source

    if included is not None and source is not None and included < source:
        if lang == "en":
            if included == 1:
                return (
                    f"This trial material includes only chapter 1 of {source}; "
                    "its synopsis and categories cover only that chapter."
                )
            return (
                f"This trial material includes only the first {included} of {source} chapters; "
                "its synopsis and categories cover only those chapters."
            )
        return f"这份试读素材仅纳入原文前 {included}/{source} 章；素材梗概和分类仅基于该范围。"

    if limit is not None and not is_known_complete:
        if lang == "en":
            noun = "chapter" if limit == 1 else "chapters"
            return (
                f"This trial material includes at most {limit} {noun}; "
                "its synopsis and categories cover only the content actually included."
            )
        return f"这份试读素材最多纳入前 {limit} 章；素材梗概和分类仅基于实际纳入范围。"

    return None


def sanitize_export_filename_stem(title: str | None, fallback: str) -> str:
    """Replace path separators and OS-reserved filename characters with spaces."""
    cleaned = _UNSAFE_FILENAME_CHARS.sub(" ", title or "")
    normalized = " ".join(cleaned.split()).strip(" .")
    return normalized or fallback


def build_material_export_filename(title: str | None, *, lang: str = "zh") -> str:
    """Build safe `.txt` download filename for a material breakdown."""
    text = _EXPORT_TEXT["en"] if lang == "en" else _EXPORT_TEXT["zh"]
    stem = sanitize_export_filename_stem(title, text["fallback_title"])
    return f"{stem}_{text['filename_suffix']}.txt"


def export_material_breakdown_to_txt(
    session: Session,
    novel: Novel,
    *,
    lang: str = "zh",
) -> str:
    """
    Format all extracted breakdown results of ``novel`` as plain text.

    Never selects or outputs ``Chapter.original_content`` or internal storage paths.
    Returns ``""`` when the novel has zero extracted breakdown content yet.
    """
    if novel.id is None:
        return ""

    novel_id = novel.id
    text = _EXPORT_TEXT["en"] if lang == "en" else _EXPORT_TEXT["zh"]
    sep = text["list_sep"]

    chapters = session.execute(
        sa_select(
            col(Chapter.id),
            col(Chapter.chapter_number),
            col(Chapter.title),
            col(Chapter.summary),
        )
        .where(col(Chapter.novel_id) == novel_id)
        .order_by(col(Chapter.chapter_number).asc())
    ).all()
    chapter_num_by_id: dict[int, int] = {
        row.id: row.chapter_number for row in chapters if row.id is not None
    }
    summarized_chapters = [
        row for row in chapters if row.summary and row.summary.strip()
    ]

    characters = session.exec(
        select(Character)
        .where(Character.novel_id == novel_id)
        .order_by(col(Character.id).asc())
    ).all()
    world_view = session.exec(
        select(WorldView).where(WorldView.novel_id == novel_id)
    ).first()
    golden_fingers = session.exec(
        select(GoldenFinger)
        .where(GoldenFinger.novel_id == novel_id)
        .order_by(col(GoldenFinger.id).asc())
    ).all()
    storylines = session.exec(
        select(StoryLine)
        .where(StoryLine.novel_id == novel_id)
        .order_by(col(StoryLine.id).asc())
    ).all()
    stories = session.exec(
        select(Story)
        .where(story_in_novel(novel_id))
        .order_by(col(Story.id).asc())
    ).all()

    char_a = aliased(Character, name="character_a")
    char_b = aliased(Character, name="character_b")
    relationships = session.exec(
        select(
            CharacterRelationship,
            col(char_a.name).label("character_a_name"),
            col(char_b.name).label("character_b_name"),
        )
        .join(char_a, CharacterRelationship.character_a_id == char_a.id)
        .join(char_b, CharacterRelationship.character_b_id == char_b.id)
        .where(CharacterRelationship.novel_id == novel_id)
        .order_by(col(CharacterRelationship.id).asc())
    ).all()

    plots = session.exec(
        select(Plot, col(Chapter.chapter_number), col(Chapter.title))
        .join(Chapter, Plot.chapter_id == Chapter.id)
        .where(Chapter.novel_id == novel_id)
        .order_by(col(Chapter.chapter_number).asc(), col(Plot.index).asc())
    ).all()

    world_factions = parse_json_list(world_view.key_factions) if world_view else []
    has_world_view = bool(
        world_view
        and (
            (world_view.power_system and world_view.power_system.strip())
            or (world_view.world_structure and world_view.world_structure.strip())
            or world_factions
            or (world_view.special_rules and world_view.special_rules.strip())
        )
    )
    synopsis = (novel.synopsis or "").strip()

    if not any(
        (
            synopsis,
            summarized_chapters,
            characters,
            has_world_view,
            golden_fingers,
            storylines,
            stories,
            relationships,
            plots,
        )
    ):
        return ""

    blocks: list[str] = []

    header_lines = [f"# {novel.title}"]
    if novel.author and novel.author.strip():
        header_lines.append(f"{text['author']}: {novel.author.strip()}")
    coverage_note = _trial_coverage_note(novel.source_meta, lang)
    if coverage_note:
        header_lines.append(coverage_note)
    blocks.append("\n".join(header_lines))

    if synopsis:
        blocks.append(f"## {text['synopsis']}\n\n{synopsis}")

    if summarized_chapters:
        chapter_blocks = [
            f"### {_chapter_heading(ch.chapter_number, ch.title, lang)}\n{ch.summary.strip()}"
            for ch in summarized_chapters
        ]
        blocks.append(f"## {text['chapter_summaries']}\n\n" + "\n\n".join(chapter_blocks))

    if characters:
        char_blocks: list[str] = []
        for character in characters:
            lines = [f"### {character.name}"]
            if character.archetype and character.archetype.strip():
                lines.append(
                    f"- {text['archetype']}: {enum_label('archetype', character.archetype, lang)}"
                )
            aliases = [str(a).strip() for a in parse_json_list(character.aliases) if str(a).strip()]
            if aliases:
                lines.append(f"- {text['aliases']}: {sep.join(aliases)}")
            if (
                character.first_appearance_chapter_id is not None
                and character.first_appearance_chapter_id in chapter_num_by_id
            ):
                lines.append(
                    f"- {text['first_appearance']}: "
                    f"{_chapter_label(chapter_num_by_id[character.first_appearance_chapter_id], lang)}"
                )
            if character.description and character.description.strip():
                lines.append(character.description.strip())
            char_blocks.append("\n".join(lines))
        blocks.append(f"## {text['characters']}\n\n" + "\n\n".join(char_blocks))

    if has_world_view and world_view is not None:
        wv_blocks: list[str] = []
        if world_view.power_system and world_view.power_system.strip():
            wv_blocks.append(f"### {text['power_system']}\n{world_view.power_system.strip()}")
        if world_view.world_structure and world_view.world_structure.strip():
            wv_blocks.append(f"### {text['world_structure']}\n{world_view.world_structure.strip()}")
        if world_factions:
            faction_lines: list[str] = []
            for faction in world_factions:
                if isinstance(faction, dict):
                    name = str(faction.get("name") or text["unknown"]).strip()
                    meta_parts: list[str] = []
                    leader = str(faction.get("leader") or "").strip()
                    territory = str(faction.get("territory") or "").strip()
                    if leader:
                        meta_parts.append(f"{text['leader']}: {leader}")
                    if territory:
                        meta_parts.append(f"{text['territory']}: {territory}")
                    suffix = f" ({', '.join(meta_parts)})" if meta_parts else ""
                    desc = str(faction.get("description") or "").strip()
                    detail = f" — {desc}" if desc else ""
                    faction_lines.append(f"- {name}{suffix}{detail}")
                elif str(faction).strip():
                    faction_lines.append(f"- {str(faction).strip()}")
            if faction_lines:
                wv_blocks.append(f"### {text['factions']}\n" + "\n".join(faction_lines))
        if world_view.special_rules and world_view.special_rules.strip():
            wv_blocks.append(f"### {text['special_rules']}\n{world_view.special_rules.strip()}")
        if wv_blocks:
            blocks.append(f"## {text['worldview']}\n\n" + "\n\n".join(wv_blocks))

    if golden_fingers:
        gf_blocks: list[str] = []
        for gf in golden_fingers:
            lines = [f"### {gf.name}"]
            if gf.type and gf.type.strip():
                lines.append(
                    f"- {text['type']}: {enum_label('golden_finger_type', gf.type, lang)}"
                )
            if (
                gf.first_appearance_chapter_id is not None
                and gf.first_appearance_chapter_id in chapter_num_by_id
            ):
                lines.append(
                    f"- {text['first_appearance']}: "
                    f"{_chapter_label(chapter_num_by_id[gf.first_appearance_chapter_id], lang)}"
                )
            if gf.description and gf.description.strip():
                lines.append(gf.description.strip())
            evolution_stages = parse_json_list(gf.evolution_history)
            if evolution_stages:
                lines.append(f"- {text['evolution']}:")
                for idx, stage in enumerate(evolution_stages, 1):
                    if isinstance(stage, dict):
                        stage_parts: list[str] = []
                        stage_name = str(stage.get("stage") or "").strip()
                        if stage_name:
                            stage_parts.append(stage_name)
                        ch_num = _positive_int(stage.get("chapter"))
                        if ch_num is not None:
                            stage_parts.append(_chapter_label(ch_num, lang))
                        timestamp = str(stage.get("timestamp") or "").strip()
                        if timestamp:
                            stage_parts.append(timestamp)
                        desc = str(stage.get("description") or "").strip()
                        prefix = f"[{' · '.join(stage_parts)}] " if stage_parts else ""
                        lines.append(f"  {idx}. {prefix}{desc or text['unknown']}")
                    elif str(stage).strip():
                        lines.append(f"  {idx}. {str(stage).strip()}")
            gf_blocks.append("\n".join(lines))
        blocks.append(f"## {text['golden_fingers']}\n\n" + "\n\n".join(gf_blocks))

    if storylines:
        sl_blocks: list[str] = []
        for sl in storylines:
            lines = [f"### {sl.title}"]
            main_chars = [str(c).strip() for c in parse_json_list(sl.main_characters) if str(c).strip()]
            if main_chars:
                lines.append(f"- {text['main_characters']}: {sep.join(main_chars)}")
            themes = _parse_themes(sl.themes)
            if themes:
                lines.append(f"- {text['themes']}: {sep.join(themes)}")
            if sl.description and sl.description.strip():
                lines.append(sl.description.strip())
            sl_blocks.append("\n".join(lines))
        blocks.append(f"## {text['storylines']}\n\n" + "\n\n".join(sl_blocks))

    if stories:
        st_blocks: list[str] = []
        for story in stories:
            lines = [f"### {story.title}"]
            if story.story_type and story.story_type.strip():
                lines.append(
                    f"- {text['story_type']}: {enum_label('story_type', story.story_type, lang)}"
                )
            if story.chapter_range and story.chapter_range.strip():
                lines.append(f"- {text['chapter_range']}: {story.chapter_range.strip()}")
            themes = _parse_themes(story.themes)
            if themes:
                lines.append(f"- {text['themes']}: {sep.join(themes)}")
            if story.core_objective and story.core_objective.strip():
                lines.append(f"- {text['core_objective']}: {story.core_objective.strip()}")
            if story.core_conflict and story.core_conflict.strip():
                lines.append(f"- {text['core_conflict']}: {story.core_conflict.strip()}")
            if story.synopsis and story.synopsis.strip():
                lines.append(story.synopsis.strip())
            st_blocks.append("\n".join(lines))
        blocks.append(f"## {text['stories']}\n\n" + "\n\n".join(st_blocks))

    if relationships:
        rel_blocks: list[str] = []
        for rel, char_a_name, char_b_name in relationships:
            lines = [
                f"### {char_a_name} ↔ {char_b_name}",
                f"- {text['relationship']}: {enum_label('relationship_type', rel.relationship_type, lang)}",
            ]
            if rel.sentiment and rel.sentiment.strip():
                lines.append(
                    f"- {text['sentiment']}: {enum_label('sentiment', rel.sentiment, lang)}"
                )
            if rel.description and rel.description.strip():
                lines.append(f"- {rel.description.strip()}")
            rel_blocks.append("\n".join(lines))
        blocks.append(f"## {text['relationships']}\n\n" + "\n\n".join(rel_blocks))

    if plots:
        plot_groups: dict[tuple[int, str], list[str]] = {}
        for plot, ch_number, ch_title in plots:
            group_key = (ch_number, ch_title)
            group_lines = plot_groups.setdefault(group_key, [])
            plot_label = enum_label("plot_type", plot.plot_type, lang)
            involved = [str(c).strip() for c in parse_json_list(plot.characters) if str(c).strip()]
            involved_suffix = (
                f" ({text['involved_characters']}: {sep.join(involved)})" if involved else ""
            )
            group_lines.append(
                f"{len(group_lines) + 1}. [{plot_label}] {plot.description.strip()}{involved_suffix}"
            )
        plot_blocks = [
            f"### {_chapter_heading(ch_num, ch_title, lang)}\n" + "\n".join(lines)
            for (ch_num, ch_title), lines in plot_groups.items()
        ]
        blocks.append(f"## {text['plots']}\n\n" + "\n\n".join(plot_blocks))

    return "\n\n".join(blocks) + "\n"
