"""模糊命中的边缘标点扩展：引号里面那个句末/停顿标点（F-01，审计 A-B1）。

模型常把闭引号前的「。」「，」漏抄（old 写「很好”」，原文是「很好。”」）。原来的
扩展从内往外逐个配对，第一个字符「。」与参照的「”」不同类就停下，原文的「。”」
整段留在替换范围外，new 自带的「”」再写一遍，得到「不错”。”」，结果仍报成功。
"""

import pytest

from agent.tools.file_ops.edit import FileEditor

_LINE = "林舟点头说：“这个方案很好。”然后走了。"


@pytest.fixture
def editor() -> FileEditor:
    return FileEditor(session=None)


def test_fuzzy_replace_old_missing_period_before_closing_quote_does_not_duplicate_quote(editor):
    applied: list[dict] = []
    result = editor._apply_replace(
        _LINE,
        {"op": "replace", "old": "林舟点头说：“这个方案很好”", "new": "林舟点头说：“这个方案不错”"},
        0,
        applied,
        [],
    )
    assert result == "林舟点头说：“这个方案不错”然后走了。"
    assert applied[0]["match_mode"] == "fuzzy"
    assert applied[0]["matched_original"] == "林舟点头说：“这个方案很好。”"


def test_fuzzy_replace_all_old_missing_period_before_closing_quote(editor):
    content = "她说：“这个方案很好。”他也说：“这个方案很好。”"
    applied: list[dict] = []
    result = editor._apply_replace(
        content,
        {"op": "replace", "old": "这个方案很好”", "new": "这个方案不错”", "replace_all": True},
        0,
        applied,
        [],
    )
    assert result == "她说：“这个方案不错”他也说：“这个方案不错”"


def test_fuzzy_insert_after_anchor_missing_period_lands_outside_the_quote(editor):
    """锚点漏了「。」：插入点要越过原文的「。”」，不能把旁白插进引语里。"""
    result = editor._apply_insert(
        _LINE,
        {"op": "insert_after", "anchor": "林舟点头说：“这个方案很好”", "text": "他笑了笑"},
        0,
        [],
        [],
        after=True,
    )
    assert result == "林舟点头说：“这个方案很好。”他笑了笑然后走了。"


def test_skip_does_not_swallow_the_next_dialogues_opening_quote(editor):
    """守卫：「。」后面是下一句的开引号时不跳过，不能把别人的开引号吞进替换范围。"""
    content = "他说完了这个方案很好。“下一句”"
    result = editor._apply_replace(
        content,
        {"op": "replace", "old": "这个方案很好”", "new": "这个方案不错”"},
        0,
        [],
        [],
    )
    assert result.endswith("。“下一句”")


def test_skip_does_not_cross_a_line_break(editor):
    """守卫：只在同一行内跳过，下一段开头的引号不并进来。"""
    content = "他说这个方案很好。\n”她答。"
    result = editor._apply_replace(
        content,
        {"op": "replace", "old": "这个方案很好”", "new": "这个方案不错”"},
        0,
        [],
        [],
    )
    assert result.endswith("。\n”她答。")
