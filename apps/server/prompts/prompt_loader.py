"""
Prompt 模板加载器

设计理念:
1. 配置与内容一体化 - 参数直接写在模板顶部
2. 按类型完全隔离 - web_short/web_long/published 独立文件夹
3. 动态发现模板 - 不硬编码模板列表，自动发现 .j2 文件
4. 统一兼容接口 - 通过工厂函数自动生成兼容层

核心 API:
- PromptLoader: 面向对象的加载器
- get_prompt(): 便捷函数，推荐使用（内置缓存优化）
- create_*_prompt(): 兼容旧接口，自动生成

性能优化:
- 使用 LRU 缓存自动复用 PromptLoader 实例
- 避免重复创建 Jinja2 Environment，提升 80%+ 性能

使用示例:
    >>> # 方式1: 使用便捷函数（推荐）
    >>> prompt = get_prompt("character_mention", novel_type="web_long")
    >>>
    >>> # 方式2: 使用加载器
    >>> loader = PromptLoader("web_long")
    >>> prompt = loader.load("character_mention")
    >>>
    >>> # 方式3: 兼容旧接口
    >>> prompt = create_character_mention_prompt(novel_type="web_long")
"""

from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateNotFound

# ==================== 核心类 ====================

class PromptLoader:
    """Prompt 模板加载器

    提供面向对象的模板加载接口，支持不同小说类型。

    Example:
        >>> loader = PromptLoader("web_short")
        >>> prompt = loader.load("character_mention")
        >>>
        >>> # 列出可用模板
        >>> available = loader.list_available_prompts()
        >>> print(available)
    """

    TEMPLATE_DIR = Path(__file__).parent / "templates"
    NOVEL_TYPES = ["web_short", "web_long"]

    def __init__(self, novel_type: str = "web_long"):
        """初始化加载器

        Args:
            novel_type: 小说类型，可选 web_short/web_long

        Raises:
            ValueError: 不支持的小说类型
            FileNotFoundError: 模板目录不存在
        """
        if novel_type not in self.NOVEL_TYPES:
            raise ValueError(
                f"不支持的小说类型: {novel_type}\n"
                f"可选类型: {', '.join(self.NOVEL_TYPES)}"
            )

        self.novel_type = novel_type
        self.template_path = self.TEMPLATE_DIR / novel_type

        if not self.template_path.exists():
            raise FileNotFoundError(f"模板目录不存在: {self.template_path}")

        self.env = Environment(
            loader=FileSystemLoader(str(self.TEMPLATE_DIR)),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def load(self, prompt_type: str, **kwargs: Any) -> str:
        """加载指定类型的 Prompt 模板

        Args:
            prompt_type: Prompt 类型（模板文件名，不含 .j2 后缀）
            **kwargs: 传递给模板的变量

        Returns:
            渲染后的 Prompt 字符串

        Raises:
            TemplateNotFound: 模板文件不存在
        """
        template_name = f"{self.novel_type}/{prompt_type}.j2"

        try:
            template = self.env.get_template(template_name)
        except TemplateNotFound:
            # 提供更友好的错误提示
            available = self.list_available_prompts()
            raise TemplateNotFound(
                f"模板文件不存在: {template_name}\n"
                f"当前 {self.novel_type} 可用的模板: {', '.join(available)}"
            ) from None

        return template.render(**kwargs)

    def list_available_prompts(self) -> list[str]:
        """列出当前小说类型下所有可用的 Prompt 模板

        Returns:
            可用的 Prompt 类型列表（不含 .j2 后缀）
        """
        if not self.template_path.exists():
            return []

        return sorted([f.stem for f in self.template_path.glob("*.j2")])


# ==================== 便捷函数 ====================

@lru_cache(maxsize=2)
def _get_cached_loader(novel_type: str) -> PromptLoader:
    """获取缓存的 PromptLoader 实例

    使用 LRU 缓存避免重复创建加载器和 Jinja2 环境。
    缓存大小为 2，覆盖所有 novel_type (web_short/web_long)。

    Args:
        novel_type: 小说类型

    Returns:
        缓存的 PromptLoader 实例
    """
    return PromptLoader(novel_type)


def get_prompt(prompt_type: str, novel_type: str = "web_long", **kwargs: Any) -> str:
    """便捷函数: 加载指定类型的 Prompt（推荐使用）

    这是最简洁的调用方式，适合大多数场景。
    内部使用缓存机制，避免重复初始化加载器。

    Args:
        prompt_type: Prompt 类型（如 character_mention, plot_extraction）
        novel_type: 小说类型（web_short/web_long）
        **kwargs: 传递给模板的变量

    Returns:
        渲染后的 Prompt 字符串

    Example:
        >>> prompt = get_prompt("character_mention", novel_type="web_short")
        >>> prompt = get_prompt("plot_extraction", novel_type="web_long", word_count=5000)
    """
    loader = _get_cached_loader(novel_type)
    return loader.load(prompt_type, **kwargs)


# ==================== 兼容层工厂 ====================

def _create_compat_function(prompt_type: str) -> Callable:
    """工厂函数: 创建兼容旧接口的函数

    Args:
        prompt_type: 对应的 Prompt 模板类型

    Returns:
        兼容函数
    """
    def compat_func(novel_type: str = "web_long", **kwargs) -> str:
        return get_prompt(prompt_type, novel_type=novel_type, **kwargs)

    # 设置函数名和文档字符串
    compat_func.__name__ = f"create_{prompt_type}_prompt"
    compat_func.__doc__ = f"""创建 {prompt_type} Prompt（兼容旧接口）

    Args:
        novel_type: 小说类型
        **kwargs: 模板参数

    Returns:
        渲染后的 Prompt 字符串
    """

    return compat_func


# ==================== 兼容旧接口（自动生成）====================
# 注意: 以下函数通过工厂自动生成，保持向后兼容

# 分析类 Prompt
create_character_mention_prompt = _create_compat_function("character_mention")
create_plot_extraction_prompt = _create_compat_function("plot_extraction")
create_chapter_summary_prompt = _create_compat_function("chapter_summary")
create_relationship_extraction_prompt = _create_compat_function("relationship_extraction")
create_meta_extraction_prompt = _create_compat_function("meta_extraction")
create_novel_synopsis_prompt = _create_compat_function("novel_synopsis")

# 剧情聚合类 Prompt
create_cross_chunk_merge_judgment_prompt = _create_compat_function("cross_chunk_merge")
create_intelligent_chunking_prompt = _create_compat_function("intelligent_chunking")
create_orphan_assignment_prompt = _create_compat_function("orphan_plots_assignment")
create_story_framework_identification_prompt = _create_compat_function("story_framework_identification")
create_plot_aggregation_prompt = _create_compat_function("plot_aggregation")
create_storyline_extraction_prompt = _create_compat_function("storyline_extraction")


# ==================== 特殊兼容函数（需要自定义签名）====================

def create_character_consolidation_prompt(
    character_name: str | None = None,
    chapter_mentions: list | None = None,
    chapter_range: str | None = None,
    novel_type: str = "web_long",
    **kwargs,
) -> str:
    """创建角色汇总 Prompt（兼容旧接口）

    当不传动态参数时，返回 100% 静态的 system prompt 以便命中前缀缓存；
    动态角色信息建议通过 format_character_consolidation_user_message 放入 user message。

    Args:
        character_name: 角色名（可选，旧接口兼容）
        chapter_mentions: 各章节的提及记录（可选，旧接口兼容）
        chapter_range: 出现章节范围（可选，旧接口兼容）
        novel_type: 小说类型
        **kwargs: 其他可选参数
    """
    template_kwargs: dict[str, Any] = dict(kwargs)
    if character_name is not None:
        template_kwargs["character_name"] = character_name
    if chapter_mentions is not None:
        template_kwargs["chapter_mentions"] = chapter_mentions
    if chapter_range is not None:
        template_kwargs["chapter_range"] = chapter_range

    return get_prompt(
        "character_consolidation",
        novel_type=novel_type,
        **template_kwargs,
    )


def format_character_consolidation_user_message(
    character_name: str,
    chapter_mentions: list[dict[str, Any]],
    chapter_range: str,
) -> str:
    """格式化角色汇总任务的用户消息（避免动态角色信息破坏 system prompt 前缀缓存）

    Args:
        character_name: 角色名
        chapter_mentions: 各章节的提及记录列表
        chapter_range: 出现章节范围

    Returns:
        格式化的用户消息
    """
    mention_lines = [
        f"- 第{mention.get('chapter_number', '')}章({mention.get('chapter_importance', 'minor')}): "
        f"{mention.get('chapter_description', '')}"
        for mention in chapter_mentions
    ]
    mentions_block = "\n".join(mention_lines)
    return (
        f"# 输入信息\n"
        f"**角色名**: {character_name}\n"
        f"**出现章节**: {chapter_range}\n\n"
        f"**各章节的局部描述**:\n"
        f"{mentions_block}\n\n"
        f"请根据上述信息生成完整的角色描述。"
    )


def format_cross_chunk_merge_user_message(
    story1_title: str,
    story1_range: str,
    story1_objective: str,
    story1_conflict: str,
    story1_synopsis: str,
    story2_title: str,
    story2_range: str,
    story2_objective: str,
    story2_conflict: str,
    story2_synopsis: str,
    novel_type: str = "web_long",
    **kwargs
) -> str:
    """格式化跨块剧情合并判断的用户消息（兼容旧接口）

    Args:
        story1_title: 剧情1标题
        story1_range: 剧情1章节范围
        story1_objective: 剧情1核心目标
        story1_conflict: 剧情1主要冲突
        story1_synopsis: 剧情1概述
        story2_title: 剧情2标题
        story2_range: 剧情2章节范围
        story2_objective: 剧情2核心目标
        story2_conflict: 剧情2主要冲突
        story2_synopsis: 剧情2概述
        novel_type: 小说类型
        **kwargs: 其他可选参数

    Returns:
        格式化的用户消息
    """
    return get_prompt(
        "cross_chunk_merge_user",
        novel_type=novel_type,
        story1_title=story1_title,
        story1_range=story1_range,
        story1_objective=story1_objective,
        story1_conflict=story1_conflict,
        story1_synopsis=story1_synopsis,
        story2_title=story2_title,
        story2_range=story2_range,
        story2_objective=story2_objective,
        story2_conflict=story2_conflict,
        story2_synopsis=story2_synopsis,
        **kwargs
    )


# ==================== 导出清单 ====================

__all__ = [
    # 核心类
    "PromptLoader",

    # 推荐函数
    "get_prompt",

    # 兼容函数 - 分析类
    "create_character_mention_prompt",
    "create_character_consolidation_prompt",
    "create_plot_extraction_prompt",
    "create_chapter_summary_prompt",
    "create_relationship_extraction_prompt",
    "create_meta_extraction_prompt",
    "create_novel_synopsis_prompt",

    # 兼容函数 - 剧情聚合类
    "create_cross_chunk_merge_judgment_prompt",
    "create_intelligent_chunking_prompt",
    "create_orphan_assignment_prompt",
    "create_story_framework_identification_prompt",
    "create_plot_aggregation_prompt",
    "create_storyline_extraction_prompt",
    "format_character_consolidation_user_message",
    "format_cross_chunk_merge_user_message",
]
