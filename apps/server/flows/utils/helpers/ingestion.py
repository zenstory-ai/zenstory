"""
摄取辅助工具函数

提供对小说文件的格式与大小验证及 MD5 校验和计算。
"""

import hashlib
import logging
from pathlib import Path
from typing import TypedDict

from ..processors.file import FileValidator

DEFAULT_MAX_SIZE = 100 * 1024 * 1024  # 100MB（小说文件可能较大）
HASH_CHUNK_SIZE = 1024 * 1024  # 1MB


class ValidationResult(TypedDict):
    """validate_input 的返回结构"""

    file_path: str
    file_size: int
    file_extension: str
    validation_passed: bool


class ChecksumResult(TypedDict):
    """calculate_checksum 的返回结构"""

    md5_checksum: str


def validate_input(file_path: str, max_size: int = DEFAULT_MAX_SIZE) -> ValidationResult:
    """验证输入文件是否存在、格式是否支持、大小是否合规。"""
    logger = logging.getLogger("helpers.ingestion.validate_input")

    if not file_path:
        raise ValueError("缺少文件路径参数")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    validator = FileValidator()
    if not validator.is_supported_format(path):
        raise ValueError(f"不支持的文件格式: {path.suffix}")

    file_size = path.stat().st_size
    if file_size > max_size:
        raise ValueError(f"文件过大: {file_size} bytes (最大: {max_size} bytes)")

    logger.info("文件验证通过: %s (%d bytes)", path, file_size)
    return {
        "file_path": str(path),
        "file_size": file_size,
        "file_extension": path.suffix.lower(),
        "validation_passed": True,
    }


def calculate_checksum(file_path: str) -> ChecksumResult:
    """计算文件的 MD5 校验和（用于小说去重与断点续传匹配）。"""
    logger = logging.getLogger("helpers.ingestion.calculate_checksum")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    md5_hash = hashlib.md5()

    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK_SIZE):
            md5_hash.update(chunk)

    result: ChecksumResult = {
        "md5_checksum": md5_hash.hexdigest(),
    }

    logger.info("校验和计算完成: md5=%s", result["md5_checksum"])
    return result
