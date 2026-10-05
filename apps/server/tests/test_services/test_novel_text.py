"""Shared novel decoding and chapter splitting (upload pre-check == worker stage0)."""
from __future__ import annotations

import pytest

from flows.utils.helpers.novel_parser import parse_novel_chapters, read_novel_text
from services.material.novel_text import (
    NovelDecodeError,
    analyze_novel_bytes,
    decode_novel_bytes,
    extract_chapter_number,
    split_novel_text,
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
