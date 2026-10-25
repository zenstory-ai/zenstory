"""Project notes hold only author content; the model cannot add entries; legacy proposals vanish."""

import pytest

from config.project_notes import (
    CONFIRMED_HEADER,
    merge_project_notes,
    render_project_notes,
    split_project_notes,
    without_legacy_proposals,
)

LEGACY_PROPOSALS_HEADER = "【AI 提议·待作者确认】"


@pytest.mark.unit
def test_split_legacy_notes_are_unlabeled():
    sections = split_project_notes("反派身份第20章前不得揭露\n女主第5章才出场")
    assert sections.unlabeled == ["反派身份第20章前不得揭露", "女主第5章才出场"]
    assert sections.confirmed == []


@pytest.mark.unit
def test_split_recognizes_storage_and_rendered_headers():
    text = f"旧条目\n{CONFIRMED_HEADER}\n主角不会游泳"
    sections = split_project_notes(text)
    assert sections.unlabeled == ["旧条目"]
    assert sections.confirmed == ["主角不会游泳"]
    rendered = "\n".join(render_project_notes(text))
    assert split_project_notes(rendered) == sections


@pytest.mark.unit
@pytest.mark.parametrize(
    "header",
    [LEGACY_PROPOSALS_HEADER, "【备注·AI 提议·待作者确认】（尚未存为作者约定）", "【备注·作者已忽略的 AI 提议】（不要再提出）"],
)
def test_legacy_proposal_blocks_are_absent_on_read(header):
    stored = f"作者旧约定\n{CONFIRMED_HEADER}\n作者事实\n\n{header}\n北境封印；沈度是配角\n萧昀登基"
    assert without_legacy_proposals(stored) == f"作者旧约定\n{CONFIRMED_HEADER}\n作者事实"
    sections = split_project_notes(stored)
    assert sections.unlabeled == ["作者旧约定"]
    assert sections.confirmed == ["作者事实"]
    rendered = "\n".join(render_project_notes(stored))
    assert "北境" not in rendered and "提议" not in rendered


@pytest.mark.unit
def test_legacy_block_in_the_middle_stops_at_the_next_author_header():
    stored = f"甲\n{LEGACY_PROPOSALS_HEADER}\n旧提议\n{CONFIRMED_HEADER}\n乙"
    assert without_legacy_proposals(stored) == f"甲\n{CONFIRMED_HEADER}\n乙"


@pytest.mark.unit
def test_notes_without_legacy_block_are_returned_byte_for_byte():
    stored = "  甲\n\n\n乙  \n"
    assert without_legacy_proposals(stored) == stored
    assert without_legacy_proposals(None) is None


@pytest.mark.unit
def test_zero_width_escaped_header_lines_stay_author_text():
    # The web author textarea stores a typed header-shaped line behind U+200B (projectNotes.ts).
    text = f"旧条目\n\u200b【AI 提议】\n女主姓林\n\u200b{CONFIRMED_HEADER}\n\n{LEGACY_PROPOSALS_HEADER}\n北境封印"
    assert without_legacy_proposals(text) == f"旧条目\n\u200b【AI 提议】\n女主姓林\n\u200b{CONFIRMED_HEADER}"
    sections = split_project_notes(text)
    assert sections.unlabeled == ["旧条目", "\u200b【AI 提议】", "女主姓林", f"\u200b{CONFIRMED_HEADER}"]
    assert sections.confirmed == []


@pytest.mark.unit
def test_chat_notes_drop_new_entries_and_keep_author_content():
    existing = f"旧版未标来源约定\n{CONFIRMED_HEADER}\n主角不会游泳"
    merged = merge_project_notes(
        existing,
        "旧版未标来源约定\n【已定】主角不会游泳\n主角擅长游泳；新增推断\n新增推断",
        from_chat=True,
    )
    assert merged.text == existing
    assert merged.dropped == ["主角擅长游泳", "新增推断"]


@pytest.mark.unit
def test_chat_notes_omission_or_empty_never_removes_author_entries():
    existing = f"旧约定\n{CONFIRMED_HEADER}\n作者确认项"
    for notes in ("", "全新的模型笔记"):
        merged = merge_project_notes(existing, notes, from_chat=True)
        assert merged.text == existing
    assert merge_project_notes(existing, "", from_chat=True).dropped == []


@pytest.mark.unit
def test_chat_copied_rendered_notes_drop_nothing_and_remove_legacy_block():
    existing = f"作者旧约定\n{CONFIRMED_HEADER}\n作者事实\n\n{LEGACY_PROPOSALS_HEADER}\n北境封印"
    rendered = "\n".join(render_project_notes(existing))
    merged = merge_project_notes(existing, rendered, from_chat=True)
    assert merged.dropped == []
    assert merged.text == f"作者旧约定\n{CONFIRMED_HEADER}\n作者事实"


@pytest.mark.unit
def test_direct_non_chat_path_replaces_whole_field_without_legacy_block():
    existing = f"旧约定\n{LEGACY_PROPOSALS_HEADER}\n旧提议"
    assert merge_project_notes(existing, "  新备注  ", from_chat=False).text == "新备注"
    assert merge_project_notes(existing, "", from_chat=False).text == ""
    kept = merge_project_notes(existing, f"新约定\n{LEGACY_PROPOSALS_HEADER}\n复活的提议", from_chat=False)
    assert kept.text == "新约定"
