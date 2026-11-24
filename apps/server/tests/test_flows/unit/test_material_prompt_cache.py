from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from flows.atomic_tasks.entities import character_tasks_v2 as char_tasks_mod
from flows.atomic_tasks.narrative import summary_based_aggregation as agg_mod
from flows.atomic_tasks.parsing import plot_tasks as plot_tasks_mod
from flows.utils.clients.llm import LLMResponse
from flows.utils.decorators import prefect as decorator_mod
from flows.utils.helpers import calculate_plots_range
from prompts import (
    PromptLoader,
    create_character_consolidation_prompt,
    create_plot_extraction_prompt,
    create_story_framework_identification_prompt,
    format_character_consolidation_user_message,
    get_prompt,
)


@pytest.mark.unit
@pytest.mark.parametrize("novel_type", ["web_long", "web_short"])
def test_character_consolidation_template_static_and_legacy_modes(novel_type: str):
    static_a = create_character_consolidation_prompt(novel_type=novel_type)
    static_b = get_prompt("character_consolidation", novel_type=novel_type)

    assert static_a == static_b
    assert "# 输入信息" not in static_a
    assert "角色名" not in static_a.split("# 示例")[0]
    assert "# 执行步骤" in static_a
    assert "# 输出格式" in static_a

    mentions = [
        {
            "chapter_number": 1,
            "chapter_importance": "major",
            "chapter_description": "初入学院测试天赋",
        },
        {
            "chapter_number": 5,
            "chapter_importance": "supporting",
            "chapter_description": "在秘境中觉醒血脉",
        },
    ]
    legacy = create_character_consolidation_prompt(
        character_name="苏辰",
        chapter_mentions=mentions,
        chapter_range="第1章 - 第5章",
        novel_type=novel_type,
    )
    assert "# 输入信息" in legacy
    assert "苏辰" in legacy
    assert "第1章 - 第5章" in legacy
    assert "第1章(major): 初入学院测试天赋" in legacy
    assert "第5章(supporting): 在秘境中觉醒血脉" in legacy

    # Positional legacy signature still works
    legacy_pos = create_character_consolidation_prompt(
        "苏辰",
        mentions,
        "第1章 - 第5章",
        novel_type,
    )
    assert legacy_pos == legacy


@pytest.mark.unit
def test_format_character_consolidation_user_message():
    mentions = [
        {
            "chapter_number": 2,
            "chapter_importance": "major",
            "chapter_description": "与宗门长老定下三年之约",
        },
        {
            "chapter_number": 9,
            "chapter_importance": "minor",
            "chapter_description": "闭关突破筑基期",
        },
    ]
    message = format_character_consolidation_user_message(
        character_name="叶凡",
        chapter_mentions=mentions,
        chapter_range="第2章 - 第9章",
    )

    assert "叶凡" in message
    assert "第2章 - 第9章" in message
    assert "- 第2章(major): 与宗门长老定下三年之约" in message
    assert "- 第9章(minor): 闭关突破筑基期" in message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("novel_type", "expected_default_range", "expected_density"),
    [
        ("web_long", "10-15个", "80-120字/个情节点"),
        ("web_short", "8-12个", "100-150字/个情节点"),
    ],
)
def test_plot_extraction_template_static_and_legacy_modes(
    novel_type: str,
    expected_default_range: str,
    expected_density: str,
):
    static_omitted = create_plot_extraction_prompt(novel_type=novel_type)
    static_none = get_prompt("plot_extraction", novel_type=novel_type, word_count=None)

    assert static_omitted == static_none
    assert "具体字数与目标情节点数量范围见用户输入" in static_omitted
    assert expected_default_range in static_omitted
    assert "1200" not in static_omitted

    legacy = get_prompt("plot_extraction", novel_type=novel_type, word_count=1200)
    assert "1200" in legacy
    assert expected_default_range in legacy
    assert expected_density in legacy


@pytest.mark.unit
@pytest.mark.parametrize("novel_type", ["web_long", "web_short"])
def test_story_framework_identification_static_vs_legacy_num_chapters(novel_type: str):
    static_prompt = create_story_framework_identification_prompt(novel_type=novel_type)
    legacy_prompt = create_story_framework_identification_prompt(
        novel_type=novel_type,
        num_chapters=24,
    )

    assert "24章" not in static_prompt
    assert "24章" in legacy_prompt


@pytest.mark.unit
@pytest.mark.parametrize("novel_type", ["web_long", "web_short"])
def test_all_material_system_prompt_templates_render_statically_without_args(novel_type: str):
    loader = PromptLoader(novel_type)
    for prompt_type in loader.list_available_prompts():
        if prompt_type == "cross_chunk_merge_user":
            continue
        rendered_first = get_prompt(prompt_type, novel_type=novel_type)
        rendered_second = get_prompt(prompt_type, novel_type=novel_type)
        assert rendered_first == rendered_second
        assert rendered_first.strip() != ""


@pytest.mark.unit
def test_build_character_entity_task_keeps_system_prompt_byte_identical_across_characters(
    monkeypatch,
    db_session,
):
    from models.material_models import Chapter, CharacterMention, Novel

    novel = Novel(user_id="u-cache-char", title="Prefix Cache Novel")
    db_session.add(novel)
    db_session.commit()

    ch1 = Chapter(novel_id=novel.id, chapter_number=1, title="第一章", original_content="正文1")
    ch2 = Chapter(novel_id=novel.id, chapter_number=2, title="第二章", original_content="正文2")
    db_session.add_all([ch1, ch2])
    db_session.commit()

    db_session.add_all(
        [
            CharacterMention(
                novel_id=novel.id,
                chapter_id=ch1.id,
                character_name="林雷",
                aliases=json.dumps(["龙血战士"], ensure_ascii=False),
                chapter_description="林雷在乌山镇开始修炼。",
                importance="major",
            ),
            CharacterMention(
                novel_id=novel.id,
                chapter_id=ch2.id,
                character_name="林雷",
                chapter_description="林雷进入魔兽山脉试炼。",
                importance="major",
            ),
            CharacterMention(
                novel_id=novel.id,
                chapter_id=ch2.id,
                character_name="贝贝",
                aliases=json.dumps(["噬神鼠"], ensure_ascii=False),
                chapter_description="贝贝与林雷缔结灵魂契约。",
                importance="supporting",
            ),
        ]
    )
    db_session.commit()

    @contextmanager
    def _session_ctx():
        yield db_session

    captured_calls: list[dict] = []

    def _fake_llm(*, messages, system_prompt, **_kwargs):
        captured_calls.append({"system_prompt": system_prompt, "messages": messages})
        return LLMResponse(content="{}", usage={}, model="m", finish_reason="stop")

    fake_client = MagicMock()
    fake_client.extract_json_from_response.side_effect = [
        {"archetype": "protagonist", "description": "林雷完整描述"},
        {"archetype": "supporting", "description": "贝贝完整描述"},
    ]

    logger = MagicMock()
    monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: logger)
    monkeypatch.setattr(char_tasks_mod, "get_run_logger", lambda: logger)
    monkeypatch.setattr(char_tasks_mod, "get_db_session", _session_ctx)
    monkeypatch.setattr(char_tasks_mod, "call_material_llm", _fake_llm)
    monkeypatch.setattr(char_tasks_mod, "get_material_llm_client", lambda: fake_client)

    char_tasks_mod.build_character_entity_task.fn(novel_id=novel.id, character_name="林雷")
    char_tasks_mod.build_character_entity_task.fn(novel_id=novel.id, character_name="贝贝")

    assert len(captured_calls) == 2
    sys_linlei = captured_calls[0]["system_prompt"]
    sys_beibei = captured_calls[1]["system_prompt"]

    # Byte-identical static system_prompt across different characters
    assert sys_linlei == sys_beibei
    assert sys_linlei == create_character_consolidation_prompt()
    assert "乌山镇开始修炼" not in sys_linlei
    assert "缔结灵魂契约" not in sys_linlei
    assert "# 输入信息" not in sys_linlei

    # Dynamic character context is faithfully carried in user_message
    user_linlei = captured_calls[0]["messages"][0]["content"]
    user_beibei = captured_calls[1]["messages"][0]["content"]

    assert "林雷" in user_linlei
    assert "第1章 - 第2章" in user_linlei
    assert "林雷在乌山镇开始修炼。" in user_linlei
    assert "林雷进入魔兽山脉试炼。" in user_linlei

    assert "贝贝" in user_beibei
    assert "第2章 - 第2章" in user_beibei
    assert "贝贝与林雷缔结灵魂契约。" in user_beibei


@pytest.mark.unit
def test_extract_chapter_plots_task_keeps_system_prompt_byte_identical_across_word_counts(
    monkeypatch,
    db_session,
):
    from models.material_models import Chapter, Novel

    novel = Novel(user_id="u-cache-plot", title="Plot Cache Novel")
    db_session.add(novel)
    db_session.commit()

    short_content = "星" * 600
    long_content = "月" * 3600
    ch_short = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="短章标题",
        original_content=short_content,
    )
    ch_long = Chapter(
        novel_id=novel.id,
        chapter_number=2,
        title="长章标题",
        original_content=long_content,
    )
    db_session.add_all([ch_short, ch_long])
    db_session.commit()

    @contextmanager
    def _session_ctx():
        yield db_session

    captured_calls: list[dict] = []

    def _fake_llm(*, messages, system_prompt, **_kwargs):
        captured_calls.append({"system_prompt": system_prompt, "messages": messages})
        return LLMResponse(content="{}", usage={}, model="m", finish_reason="stop")

    fake_client = MagicMock()
    fake_client.extract_json_from_response.return_value = {
        "plots": [
            {
                "sequence": 1,
                "plot_type": "ACTION",
                "description": "角色踏入大殿",
                "characters": ["林雷"],
            }
        ]
    }

    logger = MagicMock()
    monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: logger)
    monkeypatch.setattr(plot_tasks_mod, "get_run_logger", lambda: logger)
    monkeypatch.setattr(plot_tasks_mod, "get_db_session", _session_ctx)
    monkeypatch.setattr(plot_tasks_mod, "call_material_llm", _fake_llm)
    monkeypatch.setattr(plot_tasks_mod, "get_material_llm_client", lambda: fake_client)

    plot_tasks_mod.extract_chapter_plots_task.fn(chapter_id=ch_short.id)
    plot_tasks_mod.extract_chapter_plots_task.fn(chapter_id=ch_long.id)

    assert len(captured_calls) == 2
    sys_short = captured_calls[0]["system_prompt"]
    sys_long = captured_calls[1]["system_prompt"]

    # Byte-identical static system_prompt across chapters of differing lengths
    assert sys_short == sys_long
    assert sys_short == create_plot_extraction_prompt()
    assert "600" not in sys_short
    assert "3600" not in sys_short

    # Dynamic word count, plot range, and density are present in user_message
    for call, title, word_count in [
        (captured_calls[0], "短章标题", len(short_content)),
        (captured_calls[1], "长章标题", len(long_content)),
    ]:
        user_msg = call["messages"][0]["content"]
        min_plots, max_plots = calculate_plots_range(word_count)
        density_min = word_count / max_plots
        density_max = word_count / min_plots

        assert title in user_msg
        assert f"{word_count}" in user_msg
        assert f"{min_plots}-{max_plots}" in user_msg
        assert f"{density_min:.0f}-{density_max:.0f}" in user_msg


@pytest.mark.unit
def test_identify_story_frameworks_keeps_system_prompt_byte_identical_across_chunk_sizes(
    monkeypatch,
    db_session,
):
    from models.material_models import Chapter, Novel

    novel = Novel(user_id="u-cache-story", title="Story Framework Cache Novel")
    db_session.add(novel)
    db_session.commit()

    chapters = [
        Chapter(
            novel_id=novel.id,
            chapter_number=i,
            title=f"第{i}章",
            summary=f"第{i}章摘要内容",
        )
        for i in range(1, 37)
    ]
    db_session.add_all(chapters)
    db_session.commit()

    chunk_small_ids = [ch.id for ch in chapters[:15]]
    chunk_large_ids = [ch.id for ch in chapters[:36]]

    @contextmanager
    def _session_ctx():
        yield db_session

    captured_calls: list[dict] = []

    def _fake_llm(*, messages, system_prompt, **_kwargs):
        captured_calls.append({"system_prompt": system_prompt, "messages": messages})
        return LLMResponse(content="{}", usage={}, model="m", finish_reason="stop")

    fake_client = MagicMock()
    fake_client.extract_json_from_response.side_effect = [
        {"story_frameworks": [{"title": "小块剧情", "chapter_ids": chunk_small_ids}]},
        {"story_frameworks": [{"title": "大块剧情", "chapter_ids": chunk_large_ids}]},
    ]

    logger = MagicMock()
    monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: logger)
    monkeypatch.setattr(agg_mod, "get_run_logger", lambda: logger)
    monkeypatch.setattr(agg_mod, "get_db_session", _session_ctx)
    monkeypatch.setattr(agg_mod, "call_material_llm", _fake_llm)
    monkeypatch.setattr(agg_mod, "get_material_llm_client", lambda: fake_client)

    agg_mod.identify_story_frameworks_from_summaries.fn(
        novel_id=novel.id,
        chapter_ids=chunk_small_ids,
    )
    agg_mod.identify_story_frameworks_from_summaries.fn(
        novel_id=novel.id,
        chapter_ids=chunk_large_ids,
    )

    assert len(captured_calls) == 2
    sys_small = captured_calls[0]["system_prompt"]
    sys_large = captured_calls[1]["system_prompt"]

    # Byte-identical static system_prompt across chunks of different sizes
    assert sys_small == sys_large
    assert sys_small == create_story_framework_identification_prompt()
    assert "15章" not in sys_small
    assert "36章" not in sys_small

    # Dynamic chapter count & target framework count range are present in user_message
    for call, n in [(captured_calls[0], 15), (captured_calls[1], 36)]:
        user_msg = call["messages"][0]["content"]
        min_stories = int(n / 12 * 0.7)
        max_stories = int(n / 12 * 1.3)
        assert f"章节数量: {n}" in user_msg
        assert f"{n}章" in user_msg
        assert f"{min_stories}-{max_stories} 个独立剧情" in user_msg
