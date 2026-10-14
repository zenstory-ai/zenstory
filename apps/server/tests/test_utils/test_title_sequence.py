import pytest

from utils.title_sequence import (
    build_sequence_sort_key,
    extract_chapter_like_sequence_number,
    extract_sequence_number,
    extract_sequence_number_from_metadata,
    resolve_persisted_sequence_order,
)


@pytest.mark.unit
def test_extract_chapter_like_sequence_number_supports_chapter_patterns():
    assert extract_chapter_like_sequence_number("第十章：冲突升级") == 10
    assert extract_chapter_like_sequence_number("第12集 终章") == 12
    assert extract_chapter_like_sequence_number("Chapter 7 - Twist") == 7


@pytest.mark.unit
def test_extract_chapter_like_sequence_number_ignores_leading_number_only():
    title = "2026-03-20 写作日志"
    assert extract_chapter_like_sequence_number(title) is None
    assert extract_sequence_number(title) == 2026


@pytest.mark.unit
def test_extract_sequence_number_from_metadata_prefers_numeric_hints():
    assert extract_sequence_number_from_metadata({"chapter_number": 58}, file_type="draft") == 58
    assert extract_sequence_number_from_metadata({"episode_number": "12"}, file_type="script") == 12
    assert extract_sequence_number_from_metadata({"section_number": " 7 "}, file_type="draft") is None


@pytest.mark.unit
def test_sequence_parser_only_reads_a_leading_heading():
    assert extract_sequence_number("第12章 第一节课") == 12
    assert extract_sequence_number("第2章 第一场雪") == 2
    assert extract_sequence_number("前言：第12章 第一节课") is None


@pytest.mark.unit
def test_build_sequence_sort_key_preserves_nonzero_stored_order():
    assert build_sequence_sort_key(580, title="第58章 真相", file_type="draft") == (580, 58)
    assert build_sequence_sort_key(-7, title="第58章 真相", file_type="draft") == (-7, 58)
    assert build_sequence_sort_key(0, title="第58章 真相") == (58, 58)


@pytest.mark.unit
def test_build_sequence_sort_key_explicit_order_wins_title_sequence():
    assert build_sequence_sort_key(
        1,
        title="第58章 真相",
        file_type="draft",
    ) == (1, 58)


@pytest.mark.unit
def test_build_sequence_sort_key_does_not_lock_non_chapter_like_numbered_drafts():
    assert build_sequence_sort_key(
        1,
        title="2026-03-20 写作日志",
        file_type="draft",
    ) == (1, 2026)


@pytest.mark.unit
def test_metadata_sequence_wins_conflicting_title_when_order_is_unset():
    assert build_sequence_sort_key(
        0,
        title="第58章 真相",
        metadata={"chapter_number": 99},
        file_type="draft",
    ) == (99, 99)


@pytest.mark.unit
def test_resolve_persisted_sequence_order_preserves_explicit_order_for_chapter_files():
    assert resolve_persisted_sequence_order(
        1,
        title="第58章 真相",
        file_type="draft",
    ) == 1
    assert resolve_persisted_sequence_order(
        1,
        title="角色设定 1",
        file_type="character",
    ) == 1
    assert resolve_persisted_sequence_order(
        0,
        title="剧情大纲",
        metadata={"chapter_number": 58},
        file_type="draft",
    ) == 58
