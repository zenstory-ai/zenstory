"""Project-note source sections and model-authority boundary."""

import pytest

from config.project_notes import (
    CONFIRMED_HEADER,
    PROPOSALS_HEADER,
    merge_project_notes,
    render_project_notes,
    split_project_notes,
)


@pytest.mark.unit
def test_split_legacy_notes_are_unlabeled():
    sections = split_project_notes("反派身份第20章前不得揭露\n女主第5章才出场")
    assert sections.unlabeled == ["反派身份第20章前不得揭露", "女主第5章才出场"]
    assert sections.confirmed == [] and sections.proposals == []


@pytest.mark.unit
def test_split_recognizes_storage_and_rendered_headers():
    text = f"旧条目\n{CONFIRMED_HEADER}\n主角不会游泳\n{PROPOSALS_HEADER}\n北境封印"
    sections = split_project_notes(text)
    assert sections.unlabeled == ["旧条目"]
    assert sections.confirmed == ["主角不会游泳"]
    assert sections.proposals == ["北境封印"]
    rendered = "\n".join(render_project_notes(text))
    assert split_project_notes(rendered) == sections


@pytest.mark.unit
def test_agent_notes_cannot_remove_or_rewrite_existing_authoritative_entries():
    existing = (
        "旧版未标来源约定\n"
        f"{CONFIRMED_HEADER}\n主角不会游泳\n"
        f"{PROPOSALS_HEADER}\n旧提议"
    )
    merged = merge_project_notes(
        existing,
        notes="主角擅长游泳\n新增推断",
        proposals=None,
        author_quotes=["主角擅长游泳"],
        author_messages=["主角擅长游泳吗？"],
    )
    sections = split_project_notes(merged.text)
    assert sections.unlabeled == ["旧版未标来源约定"]
    assert sections.confirmed == ["主角不会游泳"]
    assert sections.proposals == ["旧提议", "主角擅长游泳", "新增推断"]


@pytest.mark.unit
def test_agent_omission_preserves_all_authoritative_entries():
    existing = f"旧约定\n{CONFIRMED_HEADER}\n作者确认项"
    merged = merge_project_notes(
        existing,
        notes="全新的模型笔记",
        proposals=None,
        author_quotes=None,
        author_messages=["继续写"],
    )
    sections = split_project_notes(merged.text)
    assert sections.unlabeled == ["旧约定"]
    assert sections.confirmed == ["作者确认项"]
    assert sections.proposals == ["全新的模型笔记"]


@pytest.mark.unit
def test_agent_empty_notes_preserves_authoritative_entries_and_existing_proposals():
    existing = f"旧约定\n{CONFIRMED_HEADER}\n作者确认项\n{PROPOSALS_HEADER}\n旧提议"
    merged = merge_project_notes(
        existing,
        notes="",
        proposals=None,
        author_quotes=None,
        author_messages=["继续写"],
    )
    sections = split_project_notes(merged.text)
    assert sections.unlabeled == ["旧约定"]
    assert sections.confirmed == ["作者确认项"]
    assert sections.proposals == ["旧提议"]


@pytest.mark.unit
def test_agent_quotes_questions_rejections_and_numbered_replies_never_promote():
    existing = PROPOSALS_HEADER + "\n主角不会游泳\n魂灯点一次少活一年"
    for reply, quote in (
        ("确认第1条", "确认第1条"),
        ("沧会夺舍吗？", "沧会夺舍"),
        ("点一次少活一年？太狠了，不要", "点一次少活一年"),
    ):
        merged = merge_project_notes(
            existing,
            notes="主角不会游泳\n魂灯点一次少活一年\n沧会夺舍",
            proposals=None,
            author_quotes=[quote],
            author_messages=[reply],
            previous_proposals=[(1, "主角不会游泳"), (2, "魂灯点一次少活一年")],
        )
        sections = split_project_notes(merged.text)
        assert sections.confirmed == []
        assert sections.proposals == ["主角不会游泳", "魂灯点一次少活一年", "沧会夺舍"]


@pytest.mark.unit
def test_agent_can_replace_or_clear_only_proposals_section():
    existing = f"旧约定\n{CONFIRMED_HEADER}\n作者确认项\n{PROPOSALS_HEADER}\n旧提议"
    replaced = merge_project_notes(
        existing,
        notes=None,
        proposals="新提议",
        author_quotes=None,
        author_messages=["继续写"],
    )
    sections = split_project_notes(replaced.text)
    assert sections.unlabeled == ["旧约定"]
    assert sections.confirmed == ["作者确认项"]
    assert sections.proposals == ["新提议"]

    cleared = merge_project_notes(
        replaced.text,
        notes=None,
        proposals="",
        author_quotes=None,
        author_messages=["继续写"],
    )
    sections = split_project_notes(cleared.text)
    assert sections.unlabeled == ["旧约定"]
    assert sections.confirmed == ["作者确认项"]
    assert sections.proposals == []


@pytest.mark.unit
def test_agent_copied_sections_and_semicolon_entries_are_deduplicated_without_promotion():
    existing = f"旧约定\n{CONFIRMED_HEADER}\n作者确认项\n{PROPOSALS_HEADER}\n旧提议"
    merged = merge_project_notes(
        existing,
        notes=(
            f"旧约定\n{CONFIRMED_HEADER}\n作者确认项；新推断\n"
            f"{PROPOSALS_HEADER}\n旧提议；新推断"
        ),
        proposals=None,
        author_quotes=["新推断"],
        author_messages=["新推断"],
    )
    sections = split_project_notes(merged.text)
    assert sections.unlabeled == ["旧约定"]
    assert sections.confirmed == ["作者确认项"]
    assert sections.proposals == ["旧提议", "新推断"]


@pytest.mark.unit
def test_direct_human_path_keeps_whole_field_editing_behavior():
    existing = f"旧约定\n{CONFIRMED_HEADER}\n作者确认项\n{PROPOSALS_HEADER}\n旧提议"
    replaced = merge_project_notes(
        existing,
        notes="人类直接保存的新备注",
        proposals=None,
        author_quotes=None,
        author_messages=None,
    )
    assert replaced.text == "人类直接保存的新备注\n\n【AI 提议·待作者确认】\n旧提议"

    full_replacement = merge_project_notes(
        existing,
        notes=f"人类新约定\n{PROPOSALS_HEADER}\n人类保留的新提议",
        proposals=None,
        author_quotes=None,
        author_messages=None,
    )
    assert split_project_notes(full_replacement.text).proposals == ["人类保留的新提议"]

    cleared = merge_project_notes(
        existing,
        notes="",
        proposals=None,
        author_quotes=None,
        author_messages=None,
    )
    assert cleared.text == ""
