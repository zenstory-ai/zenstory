"""
叙事任务模块

使用 summary_based_aggregation 进行剧情聚合。
"""

from .handle_orphan_plots import (
    assign_orphans_with_llm_task,
    handle_orphan_plots_task,
)
from .intelligent_chunking import (
    intelligent_chunking_task,
)
from .summary_based_aggregation import (
    aggregate_plots_for_story_framework,
    extract_storylines_from_stories,
    identify_story_frameworks_from_summaries,
    identify_story_frameworks_with_chunking,
    save_stories_with_plots,
    save_storylines_with_stories,
)

__all__ = [
    "identify_story_frameworks_from_summaries",
    "identify_story_frameworks_with_chunking",
    "aggregate_plots_for_story_framework",
    "extract_storylines_from_stories",
    "save_stories_with_plots",
    "save_storylines_with_stories",
    "handle_orphan_plots_task",
    "assign_orphans_with_llm_task",
    "intelligent_chunking_task",
]
