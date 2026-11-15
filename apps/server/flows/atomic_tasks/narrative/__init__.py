"""
叙事任务模块

推荐使用 summary_based_aggregation 进行剧情聚合。
"""

from .handle_orphan_plots import (
    assign_orphans_with_llm_task,
    handle_orphan_plots_task,
)
from .intelligent_chunking import (
    get_chunk_chapters,
    get_novel_chunks,
    intelligent_chunking_task,
)
from .summary_based_aggregation import (
    aggregate_plots_for_story_framework,
    extract_storylines_from_stories,
    identify_story_frameworks_from_summaries,
    identify_story_frameworks_with_chunking,
    save_stories_with_plots,
    save_storylines_with_stories,
    summary_based_story_aggregation,
)

aggregate_plots_to_stories_task = aggregate_plots_for_story_framework
save_stories_task = save_stories_with_plots
generate_storylines_task = extract_storylines_from_stories
save_storylines_task = save_storylines_with_stories

__all__ = [
    "summary_based_story_aggregation",
    "identify_story_frameworks_from_summaries",
    "identify_story_frameworks_with_chunking",
    "aggregate_plots_for_story_framework",
    "extract_storylines_from_stories",
    "save_stories_with_plots",
    "save_storylines_with_stories",
    "handle_orphan_plots_task",
    "assign_orphans_with_llm_task",
    "intelligent_chunking_task",
    "get_novel_chunks",
    "get_chunk_chapters",
    "aggregate_plots_to_stories_task",
    "save_stories_task",
    "generate_storylines_task",
    "save_storylines_task",
]
