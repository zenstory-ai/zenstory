"""Agent 读到的字数与编辑器一致（全部 mock，不调用 DeepSeek）。

- serialize_file 给出 word_count = utils.text_metrics.count_words（与前端 countWords 同一定义）：
  full 模式总给；summary 模式只在预览就是全文时给；file_metadata 里的旧 word_count 被覆盖或删除。
"""

import json

import pytest

from models import File
from utils.text_metrics import count_words

# 2234 字（编辑器口径），字符数明显更多：标点、全角空格、换行都不算字。
_SENTENCE = "　　“你来了？”林晚推开门，雨水顺着伞骨往下淌。\n"
SAMPLE_2234 = "第三章　归来 Chapter Three\n" + _SENTENCE * 131


def _file(content: str, metadata: dict | None = None) -> File:
    return File(
        id="f3",
        project_id="p",
        title="第三章",
        file_type="draft",
        content=content,
        file_metadata=json.dumps(metadata) if metadata is not None else None,
    )


@pytest.mark.unit
def test_sample_counts_differ_by_definition():
    assert count_words(SAMPLE_2234) == 2234
    assert len(SAMPLE_2234) != 2234


@pytest.mark.unit
def test_full_mode_returns_editor_word_count():
    from agent.tools.file_ops.serialization import serialize_file, serialize_query_file

    data = serialize_file(_file(SAMPLE_2234), include_content=True)
    assert data["word_count"] == 2234

    queried = serialize_query_file(_file(SAMPLE_2234), response_mode="full")
    assert queried["word_count"] == 2234


@pytest.mark.unit
def test_summary_mode_counts_words_only_when_preview_is_whole_content():
    from agent.tools.file_ops.serialization import serialize_file

    short = "林晚推开门。\n雨停了。"
    whole = serialize_file(_file(short), include_content=False, content_preview_chars=200)
    assert whole["content_truncated"] is False
    assert whole["word_count"] == count_words(short) == 8

    truncated = serialize_file(_file(SAMPLE_2234), include_content=False, content_preview_chars=200)
    assert truncated["content_truncated"] is True
    assert "word_count" not in truncated, "预览算出的字数不是全文字数，不能给"
    assert truncated["content_length"] == len(SAMPLE_2234)


@pytest.mark.unit
def test_summary_projection_path_follows_the_same_rule():
    from agent.tools.file_ops.serialization import serialize_file

    preview = SAMPLE_2234[:200]
    projected = serialize_file(
        _file(""),
        include_content=False,
        content_preview_chars=200,
        preloaded_content_preview=preview,
        preloaded_content_length=len(SAMPLE_2234),
    )
    assert projected["content_truncated"] is True
    assert "word_count" not in projected

    short = "雨停了。"
    projected_short = serialize_file(
        _file(""),
        include_content=False,
        content_preview_chars=200,
        preloaded_content_preview=short,
        preloaded_content_length=len(short),
    )
    assert projected_short["word_count"] == 3


@pytest.mark.unit
def test_stale_metadata_word_count_is_overwritten_or_dropped_in_the_copy_only():
    from agent.tools.file_ops.serialization import serialize_file

    stale = {"word_count": 999, "word_count_target": 3000}
    file = _file(SAMPLE_2234, stale)
    original_metadata = file.file_metadata

    full = serialize_file(file, include_content=True)
    assert full["word_count"] == 2234
    assert json.loads(full["file_metadata"]) == {"word_count_target": 3000}

    truncated = serialize_file(file, include_content=False, content_preview_chars=200)
    assert json.loads(truncated["file_metadata"]) == {"word_count_target": 3000}

    assert file.file_metadata == original_metadata, "只改返回副本，不改模型实例"

    untouched = serialize_file(_file("雨停了。", {"word_count_target": 3000}), include_content=True)
    assert json.loads(untouched["file_metadata"]) == {"word_count_target": 3000}


# ------------------------------------------------------------- 自动送审的交接
