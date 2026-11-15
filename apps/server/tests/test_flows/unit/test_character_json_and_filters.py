from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest

from flows.atomic_tasks.entities import character_tasks_v2 as char_tasks_mod
from flows.atomic_tasks.narrative import summary_based_aggregation as agg_mod
from flows.utils.clients.llm import LLMResponse
from flows.utils.decorators import prefect as decorator_mod
from flows.utils.helpers.filters import filter_character_data


@pytest.mark.unit
def test_filter_character_data_rejects_invalid_single_char_alias_without_logger():
    raw = [
        {
            "name": "  林雷： ",
            "aliases": [
                {"alias": "雷", "alias_type": "nickname", "confidence": 0.60},
                {"alias": "雷", "alias_type": "title", "confidence": 0.99},
                {"alias": "阿", "alias_type": "nickname", "confidence": 0.98},
                "龙血战士：",
                "龙血战士",
                "大哥",
            ],
        },
        {"name": "张大人", "aliases": []},
    ]

    filtered = filter_character_data(raw, logger=None)

    assert len(filtered) == 1
    assert filtered[0]["name"] == "林雷"
    assert filtered[0]["aliases"] == ["阿", "龙血战士"]


@pytest.mark.integration
def test_build_character_entity_parses_and_serializes_json_aliases_and_logs_old_archetype(
    monkeypatch, db_session
):
    from models.material_models import Chapter, Character, CharacterMention, Novel

    novel = Novel(user_id="u-char", title="Char Novel")
    db_session.add(novel)
    db_session.commit()

    chapter = Chapter(novel_id=novel.id, chapter_number=1, title="第一章", original_content="正文")
    db_session.add(chapter)
    db_session.commit()

    mention = CharacterMention(
        novel_id=novel.id,
        chapter_id=chapter.id,
        character_name="林雷",
        aliases=json.dumps(["龙血战士", "雷哥"], ensure_ascii=False),
        chapter_description="林雷击败强敌。",
        importance="major",
    )
    existing = Character(
        novel_id=novel.id,
        name="林雷·巴鲁克",
        archetype="minor",
        aliases=json.dumps(["林雷"], ensure_ascii=False),
        description="旧描述",
    )
    db_session.add_all([mention, existing])
    db_session.commit()

    @contextmanager
    def _session_ctx():
        yield db_session

    logger = MagicMock()
    monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: logger)
    monkeypatch.setattr(char_tasks_mod, "get_run_logger", lambda: logger)
    monkeypatch.setattr(char_tasks_mod, "get_db_session", _session_ctx)
    monkeypatch.setattr(
        char_tasks_mod,
        "call_material_llm",
        lambda **_kwargs: LLMResponse(
            content='{"archetype": "protagonist", "description": "新完整描述"}',
            usage={},
            model="m",
            finish_reason="stop",
        ),
    )
    client = MagicMock()
    client.extract_json_from_response.return_value = {
        "archetype": "protagonist",
        "description": "新完整描述",
    }
    monkeypatch.setattr(char_tasks_mod, "get_material_llm_client", lambda: client)

    result = char_tasks_mod.build_character_entity_task.fn(
        novel_id=novel.id,
        character_name="林雷",
    )
    db_session.commit()
    db_session.refresh(existing)

    assert result["status"] == "updated"
    assert result["character_id"] == existing.id
    assert isinstance(existing.aliases, str)
    assert json.loads(existing.aliases) == ["林雷", "龙血战士", "雷哥"]
    info_messages = [str(call.args[0]) for call in logger.info.call_args_list if call.args]
    assert any("minor -> protagonist" in msg for msg in info_messages)

    # Also verify creating a brand-new character serializes aliases to JSON string
    mention_new = CharacterMention(
        novel_id=novel.id,
        chapter_id=chapter.id,
        character_name="贝贝",
        aliases=json.dumps(["小黑鼠"], ensure_ascii=False),
        chapter_description="贝贝助战。",
        importance="supporting",
    )
    db_session.add(mention_new)
    db_session.commit()

    created_res = char_tasks_mod.build_character_entity_task.fn(
        novel_id=novel.id,
        character_name="贝贝",
    )
    db_session.commit()
    created_char = db_session.get(Character, created_res["character_id"])
    assert created_char is not None
    assert created_res["status"] == "created"
    assert isinstance(created_char.aliases, str)
    assert json.loads(created_char.aliases) == ["小黑鼠"]


@pytest.mark.integration
def test_aggregate_plots_for_story_framework_parses_plot_characters_json(monkeypatch, db_session):
    from models.material_models import Chapter, Novel, Plot

    novel = Novel(user_id="u-agg", title="Agg Novel")
    db_session.add(novel)
    db_session.commit()
    chapter = Chapter(
        novel_id=novel.id,
        chapter_number=1,
        title="第一章",
        summary="林雷与贝贝同行。",
    )
    db_session.add(chapter)
    db_session.commit()
    plot = Plot(
        chapter_id=chapter.id,
        index=1,
        plot_type="CONFLICT",
        description="林雷与贝贝激战云豹",
        characters=json.dumps(["林雷", "贝贝"], ensure_ascii=False),
    )
    db_session.add(plot)
    db_session.commit()

    @contextmanager
    def _session_ctx():
        yield db_session

    captured_messages: list[list[dict]] = []

    def _fake_llm(*, messages, **_kwargs):
        captured_messages.append(messages)
        return LLMResponse(content="{}", usage={}, model="m", finish_reason="stop")

    fake_client = MagicMock()
    fake_client.extract_json_from_response.return_value = {
        "story": {
            "title": "魔兽山脉试炼",
            "synopsis": "林雷和贝贝在魔兽山脉历练并击败黑纹云豹的完整试炼过程。",
            "plot_id_ranges": [[plot.id, plot.id]],
            "themes": ["历练"],
        }
    }

    monkeypatch.setattr(decorator_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(agg_mod, "get_run_logger", lambda: MagicMock())
    monkeypatch.setattr(agg_mod, "get_db_session", _session_ctx)
    monkeypatch.setattr(agg_mod, "call_material_llm", _fake_llm)
    monkeypatch.setattr(agg_mod, "get_material_llm_client", lambda: fake_client)

    res = agg_mod.aggregate_plots_for_story_framework.fn(
        story_framework={
            "title": "魔兽山脉试炼",
            "chapter_ids": [chapter.id],
            "brief_description": "山脉历练",
            "main_characters": ["林雷"],
        },
        novel_id=novel.id,
    )

    assert res["story"]["plot_ids"] == [plot.id]
    user_prompt = captured_messages[0][0]["content"]
    assert '"characters": [\n      "林雷",\n      "贝贝"\n    ]' in user_prompt
