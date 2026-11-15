"""
实体任务模块
"""
from .character_tasks_v2 import (
    build_character_entity_task,
    extract_character_mentions_task,
)
from .meta_tasks import (
    build_meta_entities_task,
    extract_novel_meta_task,
)

__all__ = [
    # Character tasks V2 (两阶段提取)
    "extract_character_mentions_task",
    "build_character_entity_task",
    # Meta tasks (金手指 + 世界观)
    "extract_novel_meta_task",
    "build_meta_entities_task",
]
