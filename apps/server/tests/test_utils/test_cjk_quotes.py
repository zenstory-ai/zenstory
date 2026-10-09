"""Deterministic CJK double-quote normalization (utils.cjk_quotes)."""

import pytest

from utils.cjk_quotes import (
    QUOTE_STYLE_CORNER,
    QUOTE_STYLE_CURLY,
    detect_quote_style,
    normalize_double_quotes,
    resolve_quote_style,
)


def test_pairs_of_ascii_quotes_become_open_close_curly_quotes():
    text = '他说："你来了。"她点头："嗯，来了。"'
    assert normalize_double_quotes(text, QUOTE_STYLE_CURLY) == "他说：“你来了。”她点头：“嗯，来了。”"


def test_mixed_halfwidth_fullwidth_and_curly_quotes_are_alternated_by_position():
    # 模型常见的混用：开引号写成半角、闭引号写成全角或弯引号。
    text = '"走吧＂，他说，"别回头”。'
    assert normalize_double_quotes(text, QUOTE_STYLE_CURLY) == "“走吧”，他说，“别回头”。"


def test_odd_quote_count_line_is_left_untouched():
    text = '他说："这句话跨了行\n后半句。"'
    # 两行各只有一个引号：都有歧义，原样保留。
    assert normalize_double_quotes(text, QUOTE_STYLE_CURLY) == text


def test_line_without_cjk_is_left_untouched():
    text = 'He said "hello" and left.\n她说："好。"'
    assert normalize_double_quotes(text, QUOTE_STYLE_CURLY) == 'He said "hello" and left.\n她说：“好。”'


def test_corner_style_file_follows_corner_brackets():
    existing = "「我回来了。」他推开门。\n「欢迎。」"
    style = detect_quote_style(existing)
    assert style == QUOTE_STYLE_CORNER
    new_text = '她问："你去哪了？"'
    assert normalize_double_quotes(new_text, style) == "她问：「你去哪了？」"


def test_corner_style_counts_existing_corner_marks_in_the_pairing():
    # 「」风格下行内已有的「」也参与奇偶：一个「 + 一个半角 " 正好成对。
    text = '「你好"，他说。'
    assert normalize_double_quotes(text, QUOTE_STYLE_CORNER) == "「你好」，他说。"


def test_detect_style_defaults_to_curly():
    assert detect_quote_style("") == QUOTE_STYLE_CURLY
    assert detect_quote_style("“好。”「是。」") == QUOTE_STYLE_CURLY
    assert detect_quote_style("没有引号的正文。") == QUOTE_STYLE_CURLY


def test_resolve_style_uses_first_candidate_that_has_quote_marks():
    assert resolve_quote_style("", "没有引号", "「对白」") == QUOTE_STYLE_CORNER
    assert resolve_quote_style("“已有”", "「对白」") == QUOTE_STYLE_CURLY
    assert resolve_quote_style(None, "") == QUOTE_STYLE_CURLY


@pytest.mark.parametrize(
    "text",
    [
        "他说：“你来了。”",
        "「我回来了。」他推开门。",
        "没有任何引号的一段话。",
    ],
)
def test_already_fullwidth_content_is_idempotent(text):
    for style in (QUOTE_STYLE_CURLY, QUOTE_STYLE_CORNER):
        assert normalize_double_quotes(text, style) == text
        once = normalize_double_quotes('甲："一"乙："二"\n' + text, style)
        assert normalize_double_quotes(once, style) == once


def test_single_quotes_are_not_touched():
    text = "他说：'好'，又说：\"走。\""
    assert normalize_double_quotes(text, QUOTE_STYLE_CURLY) == "他说：'好'，又说：“走。”"


def test_length_and_line_breaks_are_preserved():
    text = '第一段："甲"\r\n\n　　第二段："乙"，"丙"\n\n'
    out = normalize_double_quotes(text, QUOTE_STYLE_CURLY)
    assert len(out) == len(text)
    assert out.split("\n") == ["第一段：“甲”\r", "", "　　第二段：“乙”，“丙”", "", ""]
    # 除引号以外每个字符都在原位。
    assert [a for a, b in zip(text, out, strict=True) if a != b] == ['"'] * 6
