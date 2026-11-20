"""Per-chapter word counts as program evidence (#277): counted, never judged."""

from agent.tools.file_ops.content_range import apply_content_range
from utils.text_metrics import count_words

CH = "雨还在下，她把信折好。"


def _collection(*chapters: tuple[str, int]) -> str:
    return "\n\n".join(f"{heading}\n{CH * repeats}" for heading, repeats in chapters)


def test_range_read_reports_the_word_count_of_its_part():
    body = _collection(("第1章 雨", 3), ("第2章 风", 40))

    row = apply_content_range({"id": "f", "content": body, "word_count": count_words(body)}, {"section": "第2章"})

    assert row["range_word_count"] == count_words(row["content"]) < row["word_count"]
