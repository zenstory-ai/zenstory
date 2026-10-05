"""
小说章节解析工具

解码与分章规则统一在 ``services.material.novel_text``，上传接口的预检与
worker 的阶段0使用同一套函数，API 接受的文件 worker 一定能解码并得到相同章节。
"""

from pathlib import Path
from typing import Any

from services.material.novel_text import (
    MIN_CHAPTER_LENGTH,
    decode_novel_bytes,
    extract_chapter_number,
    split_novel_text,
)


def chinese_num_to_int(chinese_num: str) -> int:
    """将中文数字转换为阿拉伯数字，解析失败返回 1（兼容旧调用）。"""
    from services.material.novel_text import chinese_num_to_int as _convert

    value = _convert(chinese_num)
    return value if value is not None else 1


def read_novel_text(file_path: str, encoding: str | None = None) -> tuple[str, str]:
    """
    读取小说文件并返回 ``(text, encoding)``。

    未指定编码时使用与上传接口相同的严格解码；指定编码时按该编码严格解码。
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    data = path.read_bytes()
    if encoding is None:
        return decode_novel_bytes(data)
    return data.decode(encoding), encoding


def split_txt_by_chapter(
    file_path: str,
    encoding: str | None = None,
    min_chapter_length: int = MIN_CHAPTER_LENGTH,
) -> list[dict[str, Any]]:
    """
    按章节分割 txt 文件

    Args:
        file_path: 文件路径
        encoding: 文件编码（None 表示与上传接口一致的自动解码）
        min_chapter_length: 最小章节长度（更短的章节并入相邻章节）

    Returns:
        List[Dict]: 章节列表
    """
    text, _ = read_novel_text(file_path, encoding)
    return split_novel_text(text, min_chapter_length=min_chapter_length)


def parse_novel_chapters(
    file_path: str,
    encoding: str | None = None,
) -> dict[str, Any]:
    """
    解析小说文件，提取章节信息

    Args:
        file_path: 文件路径
        encoding: 文件编码（None 表示自动解码）

    Returns:
        Dict: 解析结果
    """
    chapters = split_txt_by_chapter(file_path, encoding)

    # 推断小说标题（从文件名）
    path = Path(file_path)
    novel_title = path.stem

    return {
        "novel_title": novel_title,
        "chapters": chapters,
        "total_chapters": len(chapters),
        "source_file": str(path),
    }


__all__ = [
    "chinese_num_to_int",
    "extract_chapter_number",
    "parse_novel_chapters",
    "read_novel_text",
    "split_txt_by_chapter",
]
