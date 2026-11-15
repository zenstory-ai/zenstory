"""
关联任务模块

核心任务:
- relationship_tasks: 人物关系提取和构建
"""

from .relationship_tasks import (
    build_character_relationships_task,
    extract_character_relationships_task,
)

__all__ = [
    "extract_character_relationships_task",
    "build_character_relationships_task",
]
