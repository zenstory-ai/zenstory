"""Shared novel decoding and chapter splitting (upload pre-check == worker stage0)."""
from __future__ import annotations

import pytest

from flows.utils.helpers.novel_parser import parse_novel_chapters, read_novel_text
from services.material.novel_text import (
    MAX_CHAPTER_CHARACTERS,
    TARGET_SUBCHAPTER_CHARACTERS,
    NovelDecodeError,
    analyze_novel_bytes,
    decode_novel_bytes,
    extract_chapter_number,
    split_novel_text,
    truncate_novel_text,
)

BODY = "他推开门，院子里的雪已经积了半尺深，远处传来钟声。" * 6


@pytest.mark.unit
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("第一章 开始", (1, "开始")),
        ("第十回 风雪山神庙", (10, "风雪山神庙")),
        ("第一卷 第三章 少年", (3, "少年")),
        ("第二卷风起 第十二章 归来", (12, "归来")),
        ("【第五章】标题", (5, "标题")),
        ("第十二節 歸來", (12, "歸來")),
        ("Chapter One", (1, "")),
        ("Chapter 12: The Gate", (12, "The Gate")),
        ("12. 新的开始", (12, "新的开始")),
    ],
)
def test_supported_heading_formats(line, expected):
    assert extract_chapter_number(line) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "line",
    [
        "3.5亿年前，这片大陆还是一片汪洋",
        "2023.10.01 晴",
        "第一回合他就输了",
        "前言不搭后语",
        "1、他说道，" + "这件事情没有那么简单" * 6,
        "第三章的内容他早就背熟了，可是考官偏偏问到了最后一节。",
    ],
)
def test_body_text_is_not_a_heading(line):
    assert extract_chapter_number(line)[0] == 0


@pytest.mark.unit
def test_zhanghui_novel_with_prologue_epilogue_and_volumes():
    text = "\n".join(
        [
            "楔子",
            BODY,
            "第一卷 风起云涌",
            "第一回 风起",
            BODY,
            "第二回 云涌",
            BODY,
            "尾声",
            BODY,
        ]
    )

    chapters = split_novel_text(text)

    assert [(c["chapter_number"], c["title"]) for c in chapters] == [
        (0, "楔子"),
        (1, "风起"),
        (2, "云涌"),
        (3, "尾声"),
    ]
    assert all("第一卷" not in c["content"] for c in chapters)


@pytest.mark.unit
def test_volume_and_chapter_on_same_line():
    text = f"第一卷 初入江湖 第一章 下山\n{BODY}\n第一卷 初入江湖 第二章 遇险\n{BODY}"

    chapters = split_novel_text(text)

    assert [(c["chapter_number"], c["title"]) for c in chapters] == [(1, "下山"), (2, "遇险")]


@pytest.mark.unit
def test_text_before_first_heading_becomes_prologue():
    chapters = split_novel_text(f"{BODY}\n第一章 开始\n{BODY}")

    assert [c["title"] for c in chapters] == ["序章", "开始"]
    assert chapters[0]["content"] == BODY


@pytest.mark.unit
def test_short_chapters_are_merged_not_dropped():
    text = f"第一章 开始\n{BODY}\n第二章 请假条\n今天停更一天\n第三章 继续\n{BODY}"

    chapters = split_novel_text(text)

    assert [c["chapter_number"] for c in chapters] == [1, 3]
    assert "第二章 请假条" in chapters[0]["content"]
    assert "今天停更一天" in chapters[0]["content"]


@pytest.mark.unit
def test_short_first_chapter_merges_forward():
    chapters = split_novel_text(f"第一章 短\n很短\n第二章 正文\n{BODY}")

    assert len(chapters) == 1
    assert chapters[0]["chapter_number"] == 2
    assert chapters[0]["content"].startswith("第一章 短\n很短")


@pytest.mark.unit
def test_text_without_headings_has_no_chapters():
    assert split_novel_text(BODY * 10) == []
    assert analyze_novel_bytes((BODY * 10).encode("utf-8")).chapter_count == 0


@pytest.mark.unit
def test_undecodable_bytes_raise():
    with pytest.raises(NovelDecodeError):
        decode_novel_bytes(bytes([0x81, 0x30, 0xFF, 0xFF]) * 20)


@pytest.mark.unit
def test_worker_decodes_every_file_the_upload_accepts(tmp_path):
    """Short, repetitive GBK text gets a low-confidence guess; both sides must agree."""
    text = f"第一章 开始\n{BODY}\n第二章 继续\n{BODY}"
    data = text.encode("gbk")
    path = tmp_path / "short_gbk.txt"
    path.write_bytes(data)

    analysis = analyze_novel_bytes(data)
    worker_text, worker_encoding = read_novel_text(str(path))
    parsed = parse_novel_chapters(str(path))
    # Stage0 passes the encoding it decoded with back into the parser.
    parsed_with_encoding = parse_novel_chapters(str(path), worker_encoding)

    assert worker_text == text
    assert worker_encoding == analysis.encoding
    assert analysis.chapter_count == parsed["total_chapters"] == 2
    assert parsed_with_encoding["chapters"] == parsed["chapters"]


@pytest.mark.unit
def test_truncation_keeps_exactly_the_first_chapters_the_worker_would_split():
    """免费试拆在章节标题处截断：截断后的文本重新切分，前 N 章与整本切分完全一致。"""
    text = "\r\n".join(
        [
            BODY,  # becomes 序章
            "第一卷 风起",
            "第一章 开始",
            BODY,
            "第二章 请假条",
            "今天停更一天",  # short: merged into 第一章
            "第三章 继续",
            BODY,
            "第二卷 云涌",  # volume line right before the cut
            "第四章 远行",
            BODY,
            "第五章 归来",
            BODY,
        ]
    )
    whole = split_novel_text(text)
    assert len(whole) == 5

    kept = truncate_novel_text(text, 3)

    assert kept.truncated
    assert (kept.total_chapters, kept.kept_chapters) == (5, 3)
    assert split_novel_text(kept.text) == whole[:3]
    assert "第四章" not in kept.text


@pytest.mark.unit
def test_truncation_leaves_short_books_untouched():
    text = f"第一章 开始\n{BODY}\n第二章 继续\n{BODY}"

    kept = truncate_novel_text(text, 20)

    assert not kept.truncated
    assert kept.text == text
    assert (kept.total_chapters, kept.kept_chapters) == (2, 2)
    assert truncate_novel_text(BODY, 20).total_chapters == 0


# Whitespace where Python and JavaScript differ (str.strip vs trim, re "." vs
# JS "."). apps/web/src/lib/__tests__/novelChapterSplit.test.ts pins the same
# cases, so the browser pre-check counts the chapters the server will split.
@pytest.mark.unit
@pytest.mark.parametrize(
    ("middle", "expected"),
    [
        ("﻿第二章 继续", 1),  # str.strip keeps U+FEFF: body text
        ("第二章 标 题", 2),  # Python "." matches U+2028
        ("2. 标 题", 2),
        ("\x1c第二章 继续\x1f", 2),  # str.strip removes \x1c-\x1f
        ("\x85第二章 继续", 2),
        ("第二章\x85继续", 2),
    ],
)
def test_heading_whitespace_parity_cases(middle, expected):
    text = f"第一章 开始\n{BODY}\n{middle}\n{BODY}"

    assert len(split_novel_text(text)) == expected


@pytest.mark.unit
def test_stripped_control_characters_do_not_count_toward_chapter_length():
    text = f"第一章 开始\n{BODY}\n第二章 继续\n{'字' * 99}\x1c\n第三章 末\n{BODY}"

    # 99 characters after strip: merged into 第一章.
    assert len(split_novel_text(text)) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("§§§第一章 标题", (1, "标题")),
        ("§§§第一章 标题 §§§", (1, "标题")),
        ("§第二章 归来§", (2, "归来")),
        ("# 第一章 开始", (1, "开始")),
        ("### 第一章 标题 ###", (1, "标题")),
        ("### Chapter 1", (1, "")),
        ("### Chapter 12: The Gate ###", (12, "The Gate")),
        ("☆、第一章 标题", (1, "标题")),
        ("★. 第十二章 破晓", (12, "破晓")),
        ("◆ 第一章 启程", (1, "启程")),
        ("*** 第一章 标题 ***", (1, "标题")),
        ("--- 第一章 标题 ---", (1, "标题")),
        ("=== 第一章 标题 ===", (1, "标题")),
        ("**第一章 标题**", (1, "标题")),
        ("__第一章 标题__", (1, "标题")),
        ("### **第一章 标题**", (1, "标题")),
        ("正文 第一章 标题", (1, "标题")),
        ("正文 第12章", (12, "")),
        ("正文：第三章 风起", (3, "风起")),
        ("《第一章》标题", (1, "标题")),
        ("《第一章 标题》", (1, "标题")),
        ("【第一章 标题】", (1, "标题")),
        ("「第一章」标题", (1, "标题")),
        ("『第一章』标题", (1, "标题")),
        ("[第一章] 标题", (1, "标题")),
        ("【第一章】【标题】", (1, "【标题】")),
        ("第一卷 《第二章》 少年", (2, "少年")),
        ("第一章 喵~", (1, "喵~")),
        ("第一章 C#", (1, "C#")),
    ],
)
def test_decorative_heading_markers_and_wrappers(line, expected):
    assert extract_chapter_number(line) == expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "line",
    [
        "“第一章很好看”",
        "‘第一章很好看’",
        '"第一章很好看"',
        "'第一章很好看'",
        "\ufeff第二章 继续",
        "\ufeff§§§第二章 继续",
        "《第一章的秘密",
        "【第一章》标题",
        "**第一章的内容他早就背熟了。**",
        "§§§第一章 标题。",
        "***",
        "===",
        "§§§",
        "正文内容第一章节选，这里超过四十个字" + "继续写下去" * 10,
    ],
)
def test_decorative_heading_negative_cases_stay_body_text(line):
    assert extract_chapter_number(line)[0] == 0


@pytest.mark.unit
def test_section_marker_book_and_decorated_volumes_split_cleanly():
    text = "\n".join(
        [
            "§§§第一卷 初入江湖",
            "§§§第一章 下山",
            BODY,
            "### 第二章 遇险 ###",
            BODY,
            "☆、第三章 破局",
            BODY,
            "正文 第四章 归来",
            BODY,
        ]
    )

    chapters = split_novel_text(text)

    assert [(c["chapter_number"], c["title"]) for c in chapters] == [
        (1, "下山"),
        (2, "遇险"),
        (3, "破局"),
        (4, "归来"),
    ]
    assert all("第一卷" not in c["content"] for c in chapters)


@pytest.mark.unit
def test_oversized_chapter_splits_on_paragraph_boundaries_with_monotonic_numbers():
    paragraph = "他推开门，院子里的雪已经积了半尺深，远处传来钟声，风吹过屋檐发出低沉的呜咽声。" * 5
    # ~25,000 chars across ~125 paragraphs -> should split into balanced ~5k-6k parts.
    oversized_body = "\n".join(f"第{i}段：{paragraph}" for i in range(125))
    assert len(oversized_body) > MAX_CHAPTER_CHARACTERS

    text = f"第一章 开端\n{BODY}\n第二章 巨章\n{oversized_body}\n第三章 尾声\n{BODY}"
    chapters = split_novel_text(text)

    assert len(chapters) > 3
    # First and last normal chapters stay intact.
    assert chapters[0]["title"] == "开端"
    assert chapters[0]["chapter_number"] == 1
    assert chapters[-1]["title"] == "尾声"

    middle = chapters[1:-1]
    total_parts = len(middle)
    assert total_parts >= 4
    assert [c["title"] for c in middle] == [
        f"巨章（{idx}/{total_parts}）" for idx in range(1, total_parts + 1)
    ]
    for seg in middle:
        assert len(seg["content"]) <= MAX_CHAPTER_CHARACTERS
        # Paragraph boundaries preserved: every segment starts with "第" and ends with "。".
        assert seg["content"].startswith("第")
        assert seg["content"].endswith("。")

    # Strictly monotonically increasing chapter numbers across the whole book.
    numbers = [c["chapter_number"] for c in chapters]
    assert numbers == sorted(numbers)
    assert len(set(numbers)) == len(numbers)
    assert chapters[-1]["chapter_number"] > middle[-1]["chapter_number"]


@pytest.mark.unit
def test_oversized_single_line_falls_back_to_sentence_and_hard_boundaries():
    sentence = "风雪压低了屋檐，火塘里的木炭噼啪作响，他没有回头。"
    long_sentence_line = sentence * 1_000  # 25,000 chars, single line with "。"
    assert "\n" not in long_sentence_line
    assert len(long_sentence_line) > MAX_CHAPTER_CHARACTERS

    chapters_sent = split_novel_text(f"第一章 无换行长章\n{long_sentence_line}")
    assert len(chapters_sent) >= 4
    assert all(len(c["content"]) <= MAX_CHAPTER_CHARACTERS for c in chapters_sent)
    assert all(c["content"].endswith("。") for c in chapters_sent)
    assert "".join(c["content"] for c in chapters_sent) == long_sentence_line

    hard_line = "字" * 25_000  # 25,000 chars, no newlines and no punctuation
    chapters_hard = split_novel_text(f"第一章 无标点长章\n{hard_line}")
    assert len(chapters_hard) >= 4
    assert all(len(c["content"]) <= MAX_CHAPTER_CHARACTERS for c in chapters_hard)
    assert "".join(c["content"] for c in chapters_hard) == hard_line
    assert max(len(c["content"]) for c in chapters_hard) <= TARGET_SUBCHAPTER_CHARACTERS


@pytest.mark.unit
def test_chapter_at_exact_max_characters_boundary_is_not_split():
    exact_body = "字" * MAX_CHAPTER_CHARACTERS
    chapters = split_novel_text(f"第一章 边界\n{exact_body}")

    assert len(chapters) == 1
    assert chapters[0]["title"] == "边界"
    assert len(chapters[0]["content"]) == MAX_CHAPTER_CHARACTERS


@pytest.mark.unit
def test_oversized_text_without_headings_still_yields_no_chapters():
    assert split_novel_text("正文内容。" * 6_000) == []


@pytest.mark.unit
def test_truncate_novel_text_does_not_subsplit_oversized_chapters_for_free_trial():
    oversized_body = "\n".join("长章正文内容测试。" * 20 for _ in range(130))
    assert len(oversized_body) > MAX_CHAPTER_CHARACTERS
    text = f"第一章 超长章\n{oversized_body}\n第二章 正常章\n{BODY}"

    # Free-trial truncation keeps whole chapters intact so the API refuses >15k chars.
    kept = truncate_novel_text(text, 3)
    assert kept.total_chapters == 2
    assert kept.kept_chapters == 2
    assert not kept.truncated
    assert len(kept.text) > MAX_CHAPTER_CHARACTERS

    # While paid split_novel_text sub-splits the oversized chapter into balanced segments.
    paid = split_novel_text(text)
    assert len(paid) > 2
    assert all(len(c["content"]) <= MAX_CHAPTER_CHARACTERS for c in paid)
