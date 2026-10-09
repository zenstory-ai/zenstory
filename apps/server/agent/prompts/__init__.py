"""Project writing configurations come exclusively from the primary database.

Shared runtime/role protocols remain code; project content is administered in DB.
"""

from typing import Any

from sqlmodel import Session, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import sync_engine

from .base import get_base_prompt
from .subagents import PLANNER_PROMPT, QUALITY_REVIEWER_PROMPT, WRITER_PROMPT
from .suggestions import get_suggestion_prompt

# In-memory cache for database configurations
_db_config_cache: dict[str, dict[str, Any]] | None = None

# 题材规范：短篇和竖屏短剧的行业常见写法，接在 DB 项目配置之后。长篇不加
# （长篇的节奏与篇幅差异太大，由 DB 配置和作者设定决定）。
_GENRE_NORMS_PREAMBLE = "## 题材规范（常见写法；作者或项目设定另有要求时以作者为准；和上文通用格式说明不一致时以本节为准）"

GENRE_NORMS: dict[str, str] = {
    "short": _GENRE_NORMS_PREAMBLE + """
短篇（盐言、番茄短篇这类网文短篇的常见写法）：
- 前 200 字内抛出核心冲突或钩子；
- 一条情绪主线写到底，节点清楚（压抑→爆发→反转或释怀）；
- 段落短、口语化，对白推动情节；
- 常见篇幅 8000–15000 字，按作者给的目标写；
- 结尾回扣开头的意象或细节，不写总结式升华，不说教。""",
    "screenplay": _GENRE_NORMS_PREAMBLE + """
竖屏短剧：
- 每集开场 3 秒内进冲突；
- 每集 1–3 场，常见 500–900 字；
- 场景标题写「集-场 地点 日/夜 内/外」，如「2-1 公司大堂 日 内」；
- 动作行用 △ 开头，只写能拍出来的；
- 台词写「角色：台词」，括号里只写语气或动作；
- 每集结尾留钩子（反转、悬念、对峙），钩子就是剧本最后一行，后面不写「【本集完】」这类标注；
- 集号和文件名一致。""",
}


def get_genre_norms(project_type: str) -> str:
    """Return the in-code genre norms appended after the DB config ("" when none)."""
    return GENRE_NORMS.get(project_type, "")


def _load_db_configs() -> dict[str, dict[str, Any]]:
    """
    Load all active system prompt configurations from database.

    Database errors intentionally propagate; missing rows never use source defaults.

    Returns:
        Dict mapping project_type to config dict
    """
    global _db_config_cache

    # Return cached configs if available
    if _db_config_cache is not None:
        return _db_config_cache

    db_configs, _version = _load_configs_from_engine(sync_engine)
    _db_config_cache = db_configs
    return db_configs


def _load_configs_from_engine(engine) -> tuple[dict[str, dict[str, Any]], int]:
    """
    Load prompt configurations from a specific database engine.

    Args:
        engine: SQLAlchemy engine to use
    Returns:
        Configs mapped by project type and the highest active config version.
    """
    configs = {}

    with Session(engine) as session:
        from models import SystemPromptConfig

        results = session.exec(
            select(SystemPromptConfig).where(SystemPromptConfig.is_active)
        ).all()

        for config in results:
            configs[config.project_type] = {
                "role_definition": config.role_definition,
                "capabilities": config.capabilities,
                "directory_structure": config.directory_structure or "",
                "content_structure": config.content_structure or "",
                "file_types": config.file_types or "",
                "writing_guidelines": config.writing_guidelines or "",
                "include_dialogue_guidelines": config.include_dialogue_guidelines,
            }

    max_version = max((config.version for config in results), default=0)
    return configs, max_version


def get_prompt_for_project_type(
    project_type: str,
    project_id: str,
    folder_ids: dict[str, str],
) -> str:
    """
    Get the complete system prompt for a project type.

    Missing or inactive configurations fail explicitly; no cross-type fallback.

    Args:
        project_type: Type of project (novel, short, screenplay)
        project_id: The project ID
        folder_ids: Dict mapping folder names to their IDs

    Returns:
        Complete system prompt string
    """
    db_configs = _load_db_configs()
    config = db_configs.get(project_type)

    if config is None:
        raise APIException(
            error_code=ErrorCode.SERVICE_UNAVAILABLE,
            status_code=503,
            detail={
                "message": "Writing configuration is unavailable. An administrator must configure and reload this project type.",
                "project_type": project_type,
            },
        )

    # Get base prompt with common sections
    base_prompt = get_base_prompt(project_id, folder_ids, config)

    genre_norms = get_genre_norms(project_type)
    if genre_norms:
        return f"{base_prompt}\n\n{genre_norms}"
    return base_prompt


def reload_prompts() -> dict[str, int | str]:
    """
    Eagerly reload prompt configurations from the primary database.

    This function should be called when system prompt configurations are
    updated through the admin interface to ensure changes take effect immediately.
    """
    global _db_config_cache
    configs, version = _load_configs_from_engine(sync_engine)
    _db_config_cache = configs
    return {
        "source": "primary_database",
        "count": len(configs),
        "version": version,
    }


__all__ = [
    "GENRE_NORMS",
    "get_genre_norms",
    "get_prompt_for_project_type",
    "get_suggestion_prompt",
    "reload_prompts",
    "PLANNER_PROMPT",
    "WRITER_PROMPT",
    "QUALITY_REVIEWER_PROMPT",
]
