"""Project notes are author content; chat can only append entries; legacy proposals vanish."""

import pytest

from config.project_notes import (
    CONFIRMED_HEADER,
    UNLABELED_HEADER,
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
def test_chat_notes_append_new_entries_and_keep_author_content_byte_for_byte():
    existing = "  旧版未标来源约定\n\n  缩进的作者原文  \n第三条"
    merged = merge_project_notes(
        existing,
        "旧版未标来源约定\n女主要到第五章才出场；主角左手有旧伤，阴天会疼\n女主要到第五章才出场",
        from_chat=True,
    )
    assert merged.text == existing + "\n女主要到第五章才出场\n主角左手有旧伤，阴天会疼"
    assert merged.text.startswith(existing)
    assert merged.added == ["女主要到第五章才出场", "主角左手有旧伤，阴天会疼"]
    assert merged.over_limit is False


@pytest.mark.unit
def test_chat_notes_after_a_confirmed_section_are_appended_as_plain_author_notes():
    existing = f"旧约定\n{CONFIRMED_HEADER}\n主角不会游泳"
    merged = merge_project_notes(existing, "- 【作者确认】反派第20章前不露面", from_chat=True)
    # Never promoted: the self-label and bullet are not stored; the entry goes under 未标来源.
    assert merged.text == f"{existing}\n{UNLABELED_HEADER}\n反派第20章前不露面"
    assert merged.added == ["反派第20章前不露面"]
    sections = split_project_notes(merged.text)
    assert sections.confirmed == ["主角不会游泳"]
    assert sections.unlabeled == ["旧约定", "反派第20章前不露面"]


@pytest.mark.unit
def test_chat_notes_cannot_delete_rewrite_or_reorder_author_text():
    existing = f"甲约定\n乙约定\n{CONFIRMED_HEADER}\n主角不会游泳"
    for notes in ("", "乙约定\n甲约定", "【已定】主角不会游泳", f"{CONFIRMED_HEADER}\n主角不会游泳\n乙约定"):
        merged = merge_project_notes(existing, notes, from_chat=True)
        assert merged.text == existing
        assert merged.added == []
    # A rewrite of an author entry is a new entry: appended, the author's original stays.
    rewritten = merge_project_notes(existing, "主角擅长游泳", from_chat=True)
    assert rewritten.text == f"{existing}\n{UNLABELED_HEADER}\n主角擅长游泳"


@pytest.mark.unit
def test_chat_notes_over_the_limit_save_nothing():
    existing = "甲" * 3990
    merged = merge_project_notes(existing, "这条新约定超过了上限", from_chat=True)
    assert merged.text == existing
    assert merged.added == []
    assert merged.over_limit is True
    fits = merge_project_notes("甲" * 3980, "刚好放得下", from_chat=True)
    assert fits.added == ["刚好放得下"] and len(fits.text) <= 4000


@pytest.mark.unit
def test_chat_copied_rendered_notes_add_nothing_and_remove_legacy_block():
    existing = f"作者旧约定\n{CONFIRMED_HEADER}\n作者事实\n\n{LEGACY_PROPOSALS_HEADER}\n北境封印"
    rendered = "\n".join(render_project_notes(existing))
    merged = merge_project_notes(existing, rendered, from_chat=True)
    assert merged.added == []
    assert merged.text == f"作者旧约定\n{CONFIRMED_HEADER}\n作者事实"
    # A copied-back legacy block is not re-added either.
    revived = merge_project_notes(existing, f"{LEGACY_PROPOSALS_HEADER}\n北境封印", from_chat=True)
    assert revived.added == []
    added = merge_project_notes(existing, "新约定", from_chat=True)
    assert added.text == f"作者旧约定\n{CONFIRMED_HEADER}\n作者事实\n{UNLABELED_HEADER}\n新约定"


@pytest.mark.parametrize(
    ("incoming", "expected_added"),
    [
        ("【备注·作者确认】；反派叫林墨", ["反派叫林墨"]),
        ("x；【AI 提议】；女主有秘密", ["x", "女主有秘密"]),
        ("【未标来源】；反派叫林墨", ["反派叫林墨"]),
    ],
)
@pytest.mark.unit
def test_chat_header_shaped_fragments_are_never_appended(incoming, expected_added):
    existing = f"主角冷静\n{CONFIRMED_HEADER}\n女主第5章出场"
    merged = merge_project_notes(existing, incoming, from_chat=True)
    assert merged.added == expected_added
    assert merged.text == f"{existing}\n{UNLABELED_HEADER}\n" + "\n".join(expected_added)
    sections = split_project_notes(merged.text)
    # Nothing the model sent is promoted to confirmed or hidden as a legacy proposal.
    assert sections.confirmed == ["女主第5章出场"]
    assert sections.unlabeled == ["主角冷静", *expected_added]


@pytest.mark.unit
def test_chat_rendered_notes_flattened_onto_one_line_add_nothing():
    existing = f"主角冷静\n{CONFIRMED_HEADER}\n女主第5章出场"
    flattened = "；".join(line for line in render_project_notes(existing) if line)
    merged = merge_project_notes(existing, flattened, from_chat=True)
    assert merged.added == []
    assert merged.text == existing


@pytest.mark.unit
def test_direct_non_chat_path_replaces_whole_field_without_legacy_block():
    existing = f"旧约定\n{LEGACY_PROPOSALS_HEADER}\n旧提议"
    assert merge_project_notes(existing, "  新备注  ", from_chat=False).text == "新备注"
    assert merge_project_notes(existing, "", from_chat=False).text == ""
    kept = merge_project_notes(existing, f"新约定\n{LEGACY_PROPOSALS_HEADER}\n复活的提议", from_chat=False)
    assert kept.text == "新约定"
