"""
Material Library Settings
素材库功能配置（从 DeepNovel config 迁移）

阶段开关（MATERIAL_ENABLE_*）只表示"请求"；实际执行哪些阶段由
``resolve_enabled_stages`` 按依赖关系计算，流水线的每个阶段门控都必须
读取它的结果，而不是直接读取原始开关。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

_legacy_entity_flag_warned = False


class MaterialSettings(BaseSettings):
    """素材库功能配置（从 DeepNovel config 迁移）"""

    # 通用 LLM 参数
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 64000
    # 单次 DeepSeek 请求的超时与 SDK 内部重试次数（Prefect 任务重试另计）
    LLM_REQUEST_TIMEOUT_SECONDS: float = 180.0
    LLM_SDK_MAX_RETRIES: int = 2

    # ============ 并发控制 ============
    MAX_CONCURRENT_WORKFLOWS: int = 3
    MAX_CONCURRENT_CHAPTERS: int = 3

    # ============ 小说处理配置 ============
    NOVEL_MAX_CHARACTERS: int = 50000  # 单章最大字符数
    # 单次拆解的章节数上限（上传预检与阶段0共用）；超出直接拒绝，不扣额度
    MAX_CHAPTERS_PER_NOVEL: int = 3000
    # 免费试拆：没有素材库权益的账号可拆一本的前 N 章（一次）。默认关闭：
    # 阶段0 的章节上限要随 Prefect worker 一起发布，worker 上线后再打开。
    # 环境变量带 MATERIAL_ 前缀：MATERIAL_TRIAL_ENABLED / MATERIAL_TRIAL_MAX_CHAPTERS。
    TRIAL_ENABLED: bool = False
    TRIAL_MAX_CHAPTERS: int = 20
    MIN_PLOTS_PER_CHAPTER: int = 10
    MAX_PLOTS_PER_CHAPTER: int = 15
    MIN_PLOTS_PER_STORY: int = 3
    MAX_PLOTS_PER_STORY: int = 10

    # ============ 摘要配置 ============
    CHAPTER_SUMMARY_MAX_LENGTH: int = 500
    NOVEL_SYNOPSIS_MAX_LENGTH: int = 2000

    # ============ 人物关系配置 ============
    RELATIONSHIP_BATCH_SIZE: int = 5  # 每N章提取一次

    # ============ Feature Flags ============
    # 默认只保留"章节摘要 + 概要 + 角色 + 金手指/世界观"，
    # 逐章全文情节点提取及其下游（剧情聚合、故事线、人物关系）默认关闭以节省 token。
    # 阶段1: 按章节提取
    ENABLE_CHAPTER_SUMMARIES: bool = True
    ENABLE_PLOT_EXTRACTION: bool = False
    ENABLE_CHARACTER_EXTRACTION: bool = True  # 1c 角色提及 + 2c 角色实体整合
    ENABLE_META_EXTRACTION: bool = True  # 1d 金手指 + 世界观（前 20 章）
    # 已废弃：显式设置时作为 ENABLE_CHARACTER_EXTRACTION / ENABLE_META_EXTRACTION 的
    # 取值，但只作用于未显式设置（环境变量 / 构造参数）的新开关
    ENABLE_ENTITY_EXTRACTION: bool | None = None

    # 阶段2A: 剧情相关
    ENABLE_NOVEL_SYNOPSIS: bool = True
    # 剧情聚合需要章节摘要 + 情节点；故事线需要剧情聚合（剧情按 novel_id 归属，
    # 不依赖故事线即可在接口中可见）。
    ENABLE_STORY_AGGREGATION: bool = False
    ENABLE_STORYLINE_GENERATION: bool = False

    # 阶段2B: 人物关系（默认关闭以降低拆解成本，可通过环境变量显式开启）
    ENABLE_RELATIONSHIP_EXTRACTION: bool = False
    ENABLE_NEO4J_STORAGE: bool = False  # 禁用 Neo4j

    # ============ 上传配置 ============
    UPLOAD_FOLDER: str = "uploads"
    MAX_CONTENT_LENGTH: int = 20 * 1024 * 1024  # 20MB，与 api/materials/constants.py 一致

    # ============ Redis 配置（用于进度推送）============
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str | None = None
    REDIS_DB: int = 0
    REDIS_ENABLED: bool = False  # 无 Redis 时优雅降级

    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="MATERIAL_",  # 环境变量前缀
        extra="ignore",  # 忽略额外的环境变量
    )

    @model_validator(mode="after")
    def _warn_legacy_entity_flag(self) -> MaterialSettings:
        global _legacy_entity_flag_warned
        if self.ENABLE_ENTITY_EXTRACTION is not None and not _legacy_entity_flag_warned:
            _legacy_entity_flag_warned = True
            logger.warning(
                "MATERIAL_ENABLE_ENTITY_EXTRACTION is deprecated; it applies to "
                "MATERIAL_ENABLE_CHARACTER_EXTRACTION / MATERIAL_ENABLE_META_EXTRACTION "
                "only where those are not set explicitly (value=%s). Set the two new flags instead.",
                self.ENABLE_ENTITY_EXTRACTION,
            )
        return self


# ============ Effective stage resolution ============

# 可能被依赖约束丢弃的阶段 -> 对应的原始开关（用于判断是否为显式请求）
_DROPPABLE_STAGE_FLAGS: dict[str, str] = {
    "synopsis": "ENABLE_NOVEL_SYNOPSIS",
    "stories": "ENABLE_STORY_AGGREGATION",
    "storylines": "ENABLE_STORYLINE_GENERATION",
    "relationships": "ENABLE_RELATIONSHIP_EXTRACTION",
}

# 快照/接口使用的阶段键（写入 IngestionJob.stage_progress["enabled_stages"]）
STAGE_KEYS: tuple[str, ...] = (
    "chapter_summaries",  # 1a 章节摘要
    "plots",  # 1b 逐章情节点
    "characters",  # 1c 角色提及 + 2c 角色实体
    "meta",  # 1d 金手指 + 世界观
    "synopsis",  # 2a 小说概要
    "stories",  # 2a 剧情框架 + 剧情聚合 + 孤儿情节点
    "storylines",  # 2a 故事线
    "relationships",  # 2b 人物关系
)


@dataclass(frozen=True)
class EnabledStages:
    """实际生效的阶段集合（已应用依赖约束）。"""

    chapter_summaries: bool
    plots: bool
    characters: bool
    meta: bool
    synopsis: bool
    stories: bool
    storylines: bool
    relationships: bool
    requested: dict[str, bool] = field(default_factory=dict, compare=False)
    dropped: dict[str, str] = field(default_factory=dict, compare=False)
    # dropped 中由显式设置（环境变量 / 构造参数）请求的阶段：需要 WARNING 提示配置冲突
    explicitly_dropped: tuple[str, ...] = field(default=(), compare=False)

    @property
    def story_flow_needed(self) -> bool:
        """阶段2A（story_aggregate_flow）是否需要运行。"""
        return self.synopsis or self.stories

    def as_snapshot(self) -> dict[str, bool]:
        return {key: bool(getattr(self, key)) for key in STAGE_KEYS}

    def describe(self) -> str:
        effective = self.as_snapshot()
        parts = [
            f"requested={{{', '.join(k for k in STAGE_KEYS if self.requested.get(k)) or '-'}}}",
            f"effective={{{', '.join(k for k in STAGE_KEYS if effective[k]) or '-'}}}",
        ]
        if self.dropped:
            parts.append(
                "dropped={" + "; ".join(f"{k}: {v}" for k, v in self.dropped.items()) + "}"
            )
        return " ".join(parts)


def resolve_enabled_stages(settings: MaterialSettings) -> EnabledStages:
    """根据原始开关和阶段依赖计算实际生效的阶段（纯函数）。

    依赖规则:
    - synopsis 需要 chapter_summaries
    - stories（剧情聚合 + 孤儿情节点）需要 chapter_summaries 与 plots
    - storylines 需要 stories
    - relationships 需要 plots 与 characters
    - 已废弃的 ENABLE_ENTITY_EXTRACTION 显式设置时，只作为未显式设置的
      ENABLE_CHARACTER_EXTRACTION / ENABLE_META_EXTRACTION 的取值
    """
    explicit = set(getattr(settings, "model_fields_set", ()) or ())
    legacy = getattr(settings, "ENABLE_ENTITY_EXTRACTION", None)

    def _with_legacy(flag: str) -> bool:
        if legacy is None or flag in explicit:
            return bool(getattr(settings, flag))
        return bool(legacy)

    characters_req = _with_legacy("ENABLE_CHARACTER_EXTRACTION")
    meta_req = _with_legacy("ENABLE_META_EXTRACTION")
    aggregation_req = bool(settings.ENABLE_STORY_AGGREGATION)
    storyline_req = bool(settings.ENABLE_STORYLINE_GENERATION)

    requested = {
        "chapter_summaries": bool(settings.ENABLE_CHAPTER_SUMMARIES),
        "plots": bool(settings.ENABLE_PLOT_EXTRACTION),
        "characters": characters_req,
        "meta": meta_req,
        "synopsis": bool(settings.ENABLE_NOVEL_SYNOPSIS),
        "stories": aggregation_req,
        "storylines": storyline_req,
        "relationships": bool(settings.ENABLE_RELATIONSHIP_EXTRACTION),
    }
    dropped: dict[str, str] = {}

    summaries = requested["chapter_summaries"]
    plots = requested["plots"]

    synopsis = requested["synopsis"] and summaries
    if requested["synopsis"] and not synopsis:
        dropped["synopsis"] = "requires chapter_summaries"

    stories = False
    if requested["stories"]:
        missing = [name for name, ok in (("chapter_summaries", summaries), ("plots", plots)) if not ok]
        stories = not missing
        if missing:
            dropped["stories"] = "requires " + " + ".join(missing)

    storylines = requested["storylines"] and stories
    if requested["storylines"] and not storylines:
        dropped["storylines"] = "requires stories"

    relationships = False
    if requested["relationships"]:
        missing = [name for name, ok in (("plots", plots), ("characters", characters_req)) if not ok]
        relationships = not missing
        if missing:
            dropped["relationships"] = "requires " + " + ".join(missing)

    return EnabledStages(
        chapter_summaries=summaries,
        plots=plots,
        characters=characters_req,
        meta=meta_req,
        synopsis=synopsis,
        stories=stories,
        storylines=storylines,
        relationships=relationships,
        requested=requested,
        dropped=dropped,
        explicitly_dropped=tuple(
            stage for stage in dropped if _DROPPABLE_STAGE_FLAGS.get(stage) in explicit
        ),
    )


def warn_explicitly_dropped_stages(stages: EnabledStages, log: logging.Logger | logging.LoggerAdapter, context: str) -> None:
    """WARNING when a stage that was explicitly requested is dropped by its dependencies."""
    if not stages.explicitly_dropped:
        return
    log.warning(
        "[阶段开关] %s: explicitly enabled stage(s) dropped by dependencies: %s",
        context,
        "; ".join(f"{k}: {stages.dropped[k]}" for k in stages.explicitly_dropped),
    )


material_settings = MaterialSettings()
warn_explicitly_dropped_stages(resolve_enabled_stages(material_settings), logger, "settings load")
