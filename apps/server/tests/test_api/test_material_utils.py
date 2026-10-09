"""Unit tests for material markdown formatting helpers."""

import pytest

from api.material_utils import (
    _safe_json_parse,
    format_character_to_markdown,
    format_goldenfinger_to_markdown,
    format_relationship_to_markdown,
    format_story_to_markdown,
    format_storyline_to_markdown,
    format_worldview_to_markdown,
)
from models.material_models import (
    Character,
    CharacterRelationship,
    GoldenFinger,
    Novel,
    Story,
    StoryLine,
    WorldView,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, []),
        ("", []),
        ("not json", []),
        ('{"k": 1}', []),
        ("[1, 2, 3]", [1, 2, 3]),
    ],
)
def test_safe_json_parse_handles_invalid_and_non_list_values(raw: str | None, expected: list[object]):
    assert _safe_json_parse(raw) == expected


@pytest.mark.unit
def test_format_character_to_markdown_handles_aliases_and_defaults():
    character = Character(
        novel_id=1,
        name="林凡",
        aliases='["小凡", "凡哥"]',
        description=None,
        archetype=None,
    )

    title, markdown = format_character_to_markdown(character, "测试小说")

    assert title == "林凡"
    assert "**类型**: 未知" in markdown
    assert "**别名**: 小凡、凡哥" in markdown
    assert "## 描述\n无" in markdown
    assert "参考来源: 《测试小说》" in markdown


@pytest.mark.unit
def test_format_worldview_and_goldenfinger_to_markdown_support_structured_json():
    world_view = WorldView(
        novel_id=1,
        power_system="灵力体系",
        world_structure="三界",
        key_factions='[{"name": "天机阁"}, "散修盟"]',
        special_rules="因果约束",
    )

    world_title, world_markdown = format_worldview_to_markdown(world_view, "测试小说")

    assert world_title == "世界观设定"
    assert "## 主要势力" in world_markdown
    assert "- 天机阁" in world_markdown
    assert "- 散修盟" in world_markdown

    golden_finger = GoldenFinger(
        novel_id=1,
        name="万象系统",
        type="system",
        description="可进化系统",
        evolution_history='[{"description": "初始形态"}, "完全体"]',
    )

    gf_title, gf_markdown = format_goldenfinger_to_markdown(golden_finger, "测试小说")

    assert gf_title == "万象系统"
    assert "## 进化历程" in gf_markdown
    assert "1. 初始形态" in gf_markdown
    assert "2. 完全体" in gf_markdown


@pytest.mark.unit
def test_format_storyline_to_markdown_includes_stories_and_metadata(db_session):
    novel = Novel(user_id="u1", title="测试小说")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    storyline = StoryLine(
        novel_id=novel.id,
        title="主线剧情",
        description="主角成长线",
        main_characters='["林凡", "师尊"]',
        themes='["成长", "复仇"]',
    )
    db_session.add(storyline)
    db_session.commit()
    db_session.refresh(storyline)

    db_session.add(
        Story(
            story_line_id=storyline.id,
            title="入门考核",
            synopsis="通过考核",
            core_objective="拜入宗门",
            core_conflict="同门竞争",
            story_type="成长",
            chapter_range="1-5",
        )
    )
    db_session.add(
        Story(
            story_line_id=storyline.id,
            title="宗门大比",
            synopsis="崭露头角",
            core_objective="提升排名",
            core_conflict="强敌压制",
            story_type="竞技",
            chapter_range="6-10",
        )
    )
    db_session.commit()

    title, markdown = format_storyline_to_markdown(storyline, db_session, novel.title)

    assert title == "主线剧情"
    assert "## 主要角色\n林凡、师尊" in markdown
    assert "## 主题\n成长、复仇" in markdown
    assert "- 入门考核" in markdown
    assert "- 宗门大比" in markdown


@pytest.mark.unit
def test_format_story_to_markdown_falls_back_to_raw_theme_string():
    story = Story(
        title="支线剧情",
        synopsis="支线简介",
        core_objective="完成任务",
        core_conflict="资源不足",
        story_type="冒险",
        chapter_range="11-15",
        themes="友情",
    )

    title, markdown = format_story_to_markdown(story, "测试小说")

    assert title == "支线剧情"
    assert "## 主题\n友情" in markdown
    assert "## 核心冲突\n资源不足" in markdown


@pytest.mark.unit
def test_format_relationship_to_markdown_supports_single_and_collection_titles(db_session):
    novel = Novel(user_id="u2", title="关系测试小说")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)

    char_a = Character(novel_id=novel.id, name="甲")
    char_b = Character(novel_id=novel.id, name="乙")
    char_c = Character(novel_id=novel.id, name="丙")
    db_session.add(char_a)
    db_session.add(char_b)
    db_session.add(char_c)
    db_session.commit()
    db_session.refresh(char_a)
    db_session.refresh(char_b)
    db_session.refresh(char_c)

    rel1 = CharacterRelationship(
        novel_id=novel.id,
        character_a_id=char_a.id,
        character_b_id=char_b.id,
        relationship_type="师徒",
        sentiment="亲密",
        description="长期合作",
    )
    rel2 = CharacterRelationship(
        novel_id=novel.id,
        character_a_id=char_b.id,
        character_b_id=char_c.id,
        relationship_type="对手",
    )
    db_session.add(rel1)
    db_session.add(rel2)
    db_session.commit()
    db_session.refresh(rel1)

    all_title, all_markdown = format_relationship_to_markdown(novel.id, db_session, novel.title)
    assert all_title == "角色关系"
    assert "## 甲 ↔ 乙" in all_markdown
    assert "## 乙 ↔ 丙" in all_markdown
    assert "- **态度**: 亲密" in all_markdown

    single_title, single_markdown = format_relationship_to_markdown(
        novel.id,
        db_session,
        novel.title,
        relationship_id=rel1.id,
    )
    assert single_title == "甲 ↔ 乙"
    assert "## 甲 ↔ 乙" in single_markdown
    assert "## 乙 ↔ 丙" not in single_markdown


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lang", "expected"),
    [
        ("zh", ["## 剧情概述\n", "## 剧情类型\n主线", "## 主题\n复仇、成长", "参考来源: 《测试小说》"]),
        ("en", ["## Synopsis\n", "## Story type\nMain plot", "## Themes\n复仇, 成长", "> Source: *测试小说*"]),
    ],
)
def test_format_story_to_markdown_labels_story_type_in_request_language(lang: str, expected: list[str]):
    story = Story(title="天机秘闻", synopsis="简介", story_type="main", themes='["复仇", "成长"]')

    _title, markdown = format_story_to_markdown(story, "测试小说", lang)

    for fragment in expected:
        assert fragment in markdown
    assert "\nmain\n" not in markdown


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lang", "gf_type", "archetype", "gf_label", "char_label"),
    [
        ("zh", "special_physique", "protagonist", "**类型**: 特殊体质", "**类型**: 主角"),
        ("en", "special_physique", "protagonist", "**Type**: Special physique", "**Type**: Protagonist"),
        # Unknown English enum values fall back to "other" rather than leaking raw keys.
        ("zh", "time_stop", "sidekick", "**类型**: 其他", "**类型**: 其他"),
        ("en", "time_stop", "sidekick", "**Type**: Other", "**Type**: Other"),
        # Free-text Chinese values pass through unchanged.
        ("en", "时间静止", "主角", "**Type**: 时间静止", "**Type**: 主角"),
    ],
)
def test_goldenfinger_and_character_types_are_labelled_not_raw(
    lang: str, gf_type: str, archetype: str, gf_label: str, char_label: str
):
    golden_finger = GoldenFinger(novel_id=1, name="万象", type=gf_type)
    character = Character(novel_id=1, name="林凡", archetype=archetype)

    _gf_title, gf_markdown = format_goldenfinger_to_markdown(golden_finger, "测试小说", lang)
    _char_title, char_markdown = format_character_to_markdown(character, "测试小说", lang)

    assert gf_label in gf_markdown
    assert char_label in char_markdown


@pytest.mark.unit
def test_english_markdown_has_no_chinese_headings():
    world_view = WorldView(novel_id=1, power_system="灵力", key_factions='["散修盟"]')
    golden_finger = GoldenFinger(novel_id=1, name="万象", type="system", evolution_history='["初始"]')

    world_title, world_markdown = format_worldview_to_markdown(world_view, "测试小说", "en")
    _gf_title, gf_markdown = format_goldenfinger_to_markdown(golden_finger, "测试小说", "en")

    assert world_title == "World building"
    for fragment in ("# World building", "## Power system\n灵力", "## World structure\nNone", "## Major factions"):
        assert fragment in world_markdown
    for fragment in ("## Description\nNone", "## Evolution\n1. 初始"):
        assert fragment in gf_markdown
    for chinese_heading in ("世界观", "力量体系", "描述", "进化历程", "参考来源"):
        assert chinese_heading not in world_markdown + gf_markdown


@pytest.mark.unit
@pytest.mark.parametrize(
    ("lang", "title", "expected"),
    [
        ("zh", "角色关系", ["- **关系**: 师徒", "- **态度**: 正面", "- **关系**: 其他"]),
        ("en", "Character relationships",
         ["- **Relationship**: Mentor & disciple", "- **Attitude**: Positive", "- **Relationship**: Other"]),
    ],
)
def test_format_relationship_to_markdown_labels_types_in_request_language(
    db_session, lang: str, title: str, expected: list[str]
):
    novel = Novel(user_id="u3", title="关系标签小说")
    db_session.add(novel)
    db_session.commit()
    db_session.refresh(novel)
    characters = [Character(novel_id=novel.id, name=name) for name in ("甲", "乙", "丙")]
    db_session.add_all(characters)
    db_session.commit()
    char_a, char_b, char_c = (c.id for c in characters)
    db_session.add_all([
        CharacterRelationship(
            novel_id=novel.id,
            character_a_id=char_a,
            character_b_id=char_b,
            relationship_type="master_disciple",
            sentiment="positive",
        ),
        CharacterRelationship(
            novel_id=novel.id,
            character_a_id=char_b,
            character_b_id=char_c,
            relationship_type="rival_sect_elder",
        ),
    ])
    db_session.commit()

    result_title, markdown = format_relationship_to_markdown(novel.id, db_session, novel.title, lang=lang)

    assert result_title == title
    assert markdown.startswith(f"# {title}\n")
    for fragment in expected:
        assert fragment in markdown
    assert "master_disciple" not in markdown
    assert "rival_sect_elder" not in markdown
