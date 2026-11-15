"""
文件验证工具类
"""
from pathlib import Path


class FileValidator:
    """文件验证器"""

    SUPPORTED_FORMATS = {".txt", ".md", ".markdown"}

    def is_supported_format(self, file_path: Path) -> bool:
        return file_path.suffix.lower() in self.SUPPORTED_FORMATS
