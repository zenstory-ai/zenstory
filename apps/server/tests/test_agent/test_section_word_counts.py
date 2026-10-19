"""Per-chapter word counts as program evidence (#277): counted, never judged."""

import pytest

from agent.graph.writing_graph import _format_body_word_counts
from agent.tools.file_ops.content_range import apply_content_range, section_word_counts
from utils.text_metrics import count_words

CH = "雨还在下，她把信折好。"


def _collection(*chapters: tuple[str, int]) -> str:
    return "\n\n".join(f"{heading}\n{CH * repeats}" for heading, repeats in chapters)


def test_counts_only_the_chapters_the_change_touches():
    before = _collection(("第1章 雨", 3), ("第2章 风", 3))
    after = before + "\n\n" + _collection(("第3章 雪", 50), ("第4章 霜", 20))

    counts = section_word_counts(after, len(before), len(after))

    assert counts == [
        ("第3章 雪", count_words(f"第3章 雪\n{CH * 50}\n\n")),
        ("第4章 霜", count_words(f"第4章 霜\n{CH * 20}")),
    ]


@pytest.mark.parametrize("body", [
    "没有任何章节标题的一段正文。" * 10,  # no headings: nothing to split, nothing claimed
    "",
])
def test_text_without_headings_gets_no_section_counts(body):
    assert section_word_counts(body, 0, len(body)) == []


def test_episodes_and_scenes_split_by_their_own_unit():
    script = "第1集 重逢\n【场1】医院\n台词。\n【场2】走廊\n台词。\n第2集 误会\n台词台词。"

    units = [heading for heading, _ in section_word_counts(script, 0, len(script))]

    assert units[0] == "第1集 重逢" and "第2集 误会" in units


def test_range_read_reports_the_word_count_of_its_part():
    body = _collection(("第1章 雨", 3), ("第2章 风", 40))

    row = apply_content_range({"id": "f", "content": body, "word_count": count_words(body)}, {"section": "第2章"})

    assert row["range_word_count"] == count_words(row["content"]) < row["word_count"]


def test_handoff_lists_chapter_counts_without_a_verdict():
    text = _format_body_word_counts([("旧城合集", 52000)], [("旧城合集", "第41章 门", 1203), ("旧城合集", "第42章 灯", 980)])

    assert "《旧城合集》第41章 门 1203 字" in text and "《旧城合集》第42章 灯 980 字" in text
    assert "不达标" not in text and "通过" not in text
    assert _format_body_word_counts([("旧城合集", 52000)], None).count("各章") == 0
