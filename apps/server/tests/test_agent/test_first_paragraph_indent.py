"""AI 整章写入的小说正文：后面段落都用「　　」缩进、只有第一段顶格时补上第一段的缩进。"""

import pytest

from agent.tools.file_ops.edit import normalize_ai_body_first_indent
from agent.tools.file_ops.paragraph_layout import indent_unindented_first_paragraph
from utils.text_metrics import count_words

pytestmark = pytest.mark.unit

INDENTED_REST = "\n\n　　第二段合成正文。\n\n　　第三段合成正文。\n\n　　第四段合成正文。"


@pytest.mark.parametrize("lead", ["", "\n", "\n\n"])
@pytest.mark.parametrize("separator", ["\n", "\n\n", "\r\n"])
def test_unindented_first_paragraph_follows_the_indented_body(lead, separator):
    body = lead + "第一段合成正文。" + INDENTED_REST.replace("\n\n", separator)
    fixed = indent_unindented_first_paragraph(body)
    assert fixed == lead + "　　第一段合成正文。" + INDENTED_REST.replace("\n\n", separator)
    assert count_words(fixed) == count_words(body)
    assert indent_unindented_first_paragraph(fixed) == fixed


def test_opening_quote_first_paragraph_is_indented():
    body = "“走吧。”他说。" + INDENTED_REST
    assert indent_unindented_first_paragraph(body).startswith("　　“走吧。”")


def test_one_unindented_paragraph_among_five_still_counts_as_the_body_style():
    body = "第一段。\n　　二。\n　　三。\n　　四。\n五。\n　　六。"
    assert indent_unindented_first_paragraph(body).startswith("　　第一段。")


@pytest.mark.parametrize(
    "body",
    [
        "第一段。\n第二段。\n第三段。\n第四段。",  # 全书不缩进
        "第一段。\n　　二。\n三。\n　　四。\n五。",  # 混用
        "第一段。\n　　二。\n　　三。",  # 后面不足 3 段
        "第一段。\n    二。\n    三。\n    四。",  # 半角空格缩进不是「　　」
        "第一段。\n　　　　二。\n　　　　三。\n　　　　四。",  # 缩进不是恰好「　　」
        "　第一段。" + INDENTED_REST,  # 第一段已有缩进
        "\t第一段。" + INDENTED_REST,
        "第一章 雨夜" + INDENTED_REST,  # 第一行是标题
        "# 雨夜" + INDENTED_REST,
        "***" + INDENTED_REST,  # 分隔行
        "",
        "\n\n",
    ],
)
def test_untouched_when_the_body_style_is_not_clear(body):
    assert indent_unindented_first_paragraph(body) == body


def test_heading_lines_do_not_count_as_paragraphs():
    body = "第一段。\n第二章 渡口\n　　二。\n　　三。"
    assert indent_unindented_first_paragraph(body) == body
    body = "第一段。\n第二章 渡口\n　　二。\n　　三。\n　　四。"
    assert indent_unindented_first_paragraph(body).startswith("　　第一段。")


def test_english_prose_is_untouched():
    body = "The rain had not stopped.\n\nShe waited.\n\nHe came back.\n\nNobody spoke."
    assert indent_unindented_first_paragraph(body) == body


def test_only_draft_bodies_are_normalized():
    body = "△ 公司大堂，人来人往。" + INDENTED_REST
    assert normalize_ai_body_first_indent("第一段。" + INDENTED_REST, "draft").startswith("　　第一段。")
    assert normalize_ai_body_first_indent(body, "script") == body
    assert normalize_ai_body_first_indent("第一段。" + INDENTED_REST, "outline") == "第一段。" + INDENTED_REST


@pytest.mark.parametrize(
    "first",
    [
        "序章",
        "楔子",
        "一",
        "一、",
        "1",
        "1.",
        "二。",
        "第一章雨夜",  # 章字后面没有分隔符，不算标题行，但也不像一段话
        "Chapter One",
        "雨夜",
        "1-1 日 内 公司大堂",
        "（未完）",
    ],
)
def test_short_marker_or_title_lines_are_not_treated_as_the_first_paragraph(first):
    body = first + INDENTED_REST
    assert indent_unindented_first_paragraph(body) == body


@pytest.mark.parametrize(
    "first",
    [
        "——你终于来了。",
        "……雨还在下。",
        "“走吧”",
        "他刚要开口——",
        '"Go," she said.',
    ],
)
def test_dash_ellipsis_and_quote_paragraphs_are_indented(first):
    body = first + INDENTED_REST
    fixed = indent_unindented_first_paragraph(body)
    assert fixed == "　　" + body
    assert count_words(fixed) == count_words(body)


@pytest.mark.parametrize("separator_line", ["——", "……", "———"])
def test_dash_or_ellipsis_only_lines_are_separators(separator_line):
    body = separator_line + INDENTED_REST
    assert indent_unindented_first_paragraph(body) == body


def test_paragraph_under_a_leading_heading_is_indented():
    body = "第三章 雨夜\n第一段合成正文。" + INDENTED_REST
    fixed = indent_unindented_first_paragraph(body)
    assert fixed == "第三章 雨夜\n　　第一段合成正文。" + INDENTED_REST
    assert indent_unindented_first_paragraph(fixed) == fixed


def test_previous_unindented_first_paragraph_is_kept():
    body = "第一段合成正文。" + INDENTED_REST
    assert indent_unindented_first_paragraph(body, previous=body) == body
    assert indent_unindented_first_paragraph(body, previous="第一段合成正文。\r\n旧的第二段。") == body
    assert normalize_ai_body_first_indent(body, "draft", previous=body) == body
    # 原文第一段不同、或原文第一段本来有缩进时照常补。
    assert indent_unindented_first_paragraph(body, previous="另一段原文。" + INDENTED_REST) == "　　" + body
    assert indent_unindented_first_paragraph(body, previous="　　" + body) == "　　" + body
    assert indent_unindented_first_paragraph(body, previous="") == "　　" + body
