"""
Prompt 模块 - Jinja2 模板化方案

改造说明:
- 已将核心 Prompt 迁移到 Jinja2 模板 (templates/ 目录)
- 配置参数内置于模板顶部,无需 Python 配置文件
- 支持多种小说类型: web_short (短篇网文) / web_long (长篇网文) / published (出版物)

使用方式:
    >>> from prompts import get_prompt
    >>>
    >>> # 方式1: 使用便捷函数 (推荐)
    >>> prompt = get_prompt("character_mention", novel_type="web_short")
    >>> prompt = get_prompt("plot_extraction", novel_type="web_long", word_count=5000)
    >>>
    >>> # 方式2: 使用加载器
    >>> from prompts import PromptLoader
    >>> loader = PromptLoader("web_short")
    >>> prompt = loader.load("character_mention")
    >>>
    >>> # 方式3: 兼容旧接口
    >>> from prompts import create_character_mention_prompt
    >>> prompt = create_character_mention_prompt(novel_type="web_short")

已迁移的 Prompt 类型 (web_short + web_long):

核心Prompt (已迁移到 Jinja2):
- character_mention: 角色提及提取
- character_consolidation: 角色汇总
- plot_extraction: 情节提取
- chapter_summary: 章节摘要
- relationship_extraction: 关系提取
- character_extraction: 角色提取 (含别名消解)
- meta_extraction: 金手指/世界观提取
- novel_synopsis: 小说梗概

高级Prompt (已迁移到 Jinja2):
- cross_chunk_merge: 跨块剧情合并判断
- intelligent_chunking: 智能分块
- orphan_plots_assignment: 孤儿情节点分配
- story_framework_identification: 剧情框架识别
- plot_aggregation: 情节点聚合到剧情
- storyline_extraction: 剧情线提炼

辅助函数 (仍需要 Python 实现):
- format_character_consolidation_user_message: 角色汇总用户消息格式化辅助函数
- format_cross_chunk_merge_user_message: 跨块合并消息格式化辅助函数
"""

from .prompt_loader import (
    PromptLoader,
    create_chapter_summary_prompt,
    create_character_consolidation_prompt,
    create_character_mention_prompt,
    create_cross_chunk_merge_judgment_prompt,
    create_intelligent_chunking_prompt,
    create_meta_extraction_prompt,
    create_novel_synopsis_prompt,
    create_orphan_assignment_prompt,
    create_plot_aggregation_prompt,
    create_plot_extraction_prompt,
    create_relationship_extraction_prompt,
    create_story_framework_identification_prompt,
    create_storyline_extraction_prompt,
    format_character_consolidation_user_message,
    format_cross_chunk_merge_user_message,
    get_prompt,
)

__all__ = [
    # 核心类
    "PromptLoader",
    # 便捷函数
    "get_prompt",
    # 兼容旧接口 - 核心Prompt
    "create_character_mention_prompt",
    "create_character_consolidation_prompt",
    "create_plot_extraction_prompt",
    "create_chapter_summary_prompt",
    "create_relationship_extraction_prompt",
    "create_meta_extraction_prompt",
    "create_novel_synopsis_prompt",
    # 兼容旧接口 - 高级Prompt
    "create_cross_chunk_merge_judgment_prompt",
    "create_intelligent_chunking_prompt",
    "create_orphan_assignment_prompt",
    "create_story_framework_identification_prompt",
    "create_plot_aggregation_prompt",
    "create_storyline_extraction_prompt",
    # 辅助函数
    "format_character_consolidation_user_message",
    "format_cross_chunk_merge_user_message",
]
