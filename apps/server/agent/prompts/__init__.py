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

# 题材规范接在 DB 项目配置之后；长篇只补核心看点的发展，不统一篇幅与节奏。
_GENRE_NORMS_PREAMBLE = "## 题材规范（常见写法；作者或项目设定另有要求时以作者为准；和上文通用格式说明不一致时以本节为准）"

# 网文口感：三类共用；是否适用由模型按作者给的书名、简介和已有章节判断，不做关键词判定。
_WEB_FICTION_VOICE = """
网文口感（作者的书名、简介、梗概或已有章节是中文网文、短剧写法时适用，如重生、系统、真假千金、逆袭打脸、萌宝、种田；与上文通用创作指导不一致时按这里写；作者要的文风优先）：
- 简介里的主角身份、能力或金手指的来历、起点处境、关键人物关系，以及点名的事件和手段（谁主动做了什么、婚约、某张符、某个系统）是核心前提，按原样写，不换成别的设定或手段。
- 第一章（第一集）只写简介开头的一两个节点：开篇第一屏就让主角处在其中最抓人的处境里（被逼、被赶、被冤枉、生死对峙、交易上门），前史和身世用几句嵌在冲突里交代；写透一次交锋和一次兑现，简介后面的事件留给后续章节；不为制造悬念临时添加没铺垫的道具、规则、病情或新势力。
- 叙述贴着主角，直接写出人物的感受、判断和利害（委屈、愤怒、得意、谁在算计谁、这一下意味着什么），不靠器物、比喻、留白和欲言又止让读者自己猜；口吻随人物身份和题材，可以口语、吐槽、放狠话；喜剧向的简介写出喜感，不写成冷峻悲壮。
- 爽点、反击、恐怖或甜点当场兑现成看得见的结果：对手出丑、受损、改口，主角拿到具体的东西（钱、名额、等级、证据、一句认输）；不用「我知道」「等着瞧」、预告或含笑不语代替兑现；一章写透一两次，不连续堆叠清算，兑现后不连着用落泪、回忆和感悟收尾。
- 对白是在争东西：逼迫、讨价还价、拒绝、回击，每句接住上一句，说完局面有变化；反派的恶意可以藏在讲道理、装好人里；不让人物把双方都知道的背景说给读者听，不写谜语式的半截话。
- 一句一段是常见排版，每段推进一个动作、一句交锋或一个判断；抬眼、沉默、停顿、笑一声这类反应不单独成段，也不插在两句交锋中间。
- 章末（集末）停在本章兑现的动作或由它直接引出的下一步压力上，不另起一条新线制造悬念；场景切换用空行，不用「——」。
- 对照（只示意写法，不照抄句子）：不写「她垂下眼，指尖在杯沿停了很久。」，写「她心里一沉：这个家，从来没把她当过女儿。」；不写「他笑了笑：“你会后悔的。”」，写「他把转账记录拍在桌上：三百万，一笔不少，全是你这些年挪走的。二叔的脸当场白了。」；不单写一行「屋里安静了。」，写「屋里没人敢吭声，二婶憋了半天才挤出一句：“那钱……也不全是我拿的。”」"""

GENRE_NORMS: dict[str, str] = {
    "novel": _GENRE_NORMS_PREAMBLE + """
长篇：
- 从作者目标和前文辨认作品持续吸引读者的核心看点，在阶段剧情中继续发挥、发展它，不随情节升级丢掉原有特色；
- 让已建立的能力、关系和条件实际参与人物选择与事件结果，同时保留它们的限制和代价，避免用同类回合重复或单纯抬高数值代替进展。""" + _WEB_FICTION_VOICE,
    "short": _GENRE_NORMS_PREAMBLE + """
短篇（中文按网文短篇写；英文作品按英文短篇小说的常见写法）：
- 前 200 字（英文作品约前 60 个单词）内抛出核心冲突或钩子；
- 围绕核心阅读期待推进，让情绪随事件、人物选择和关系变化形成层次；
- 段落偏短、口语化，对白推动情节；短段不等于一句一行，同一串动作仍写成完整的句子；
- 常见篇幅 8000–15000 字（英文作品常见 3000–7500 个单词），按作者给的目标写；
- 结尾回应核心阅读期待，按题材与作者要求收束；回扣开头的意象或细节可选，避免重复总结与说教。""" + _WEB_FICTION_VOICE,
    "screenplay": _GENRE_NORMS_PREAMBLE + """
短剧（以下节奏与篇幅为竖屏短剧参考，其他体例按作者要求）：
- 开场尽快进入与本集目标有关的冲突；前 1–3 集兑现剧名和简介许诺的核心卖点，每集新增的人物和线索有节制，优先推进已有的线；
- 每集 1–3 场、500–900 字仅作常见参考；实际场数与篇幅服从作者目标，时长结合台词、动作和停顿判断；
- 场景标题写「集-场 地点 日/夜 内/外」，如「2-1 公司大堂 日 内」；
- 动作行用 △ 开头，只写能拍出来的；
- 台词写「角色：台词」，括号里只写语气或动作，只在台词会被误读时加；
- 需要接续的集从本集行动、结果或关系变化中留下钩子，钩子是下一集要面对的具体压力（大纲里的承接点同样如此），不写感悟，钩子就是剧本最后一行；终集、独立短剧及作者要求收束时回应核心承诺，不强加新悬念；正文后不写「【本集完】」这类标注；
- 集号和文件名一致。
- 竖屏短剧可以用主角第一人称旁白开场（写成「角色（旁白）：」），一两句直接交代处境和卖点，再进入现场；旁白只交代观众看不出的，不复述画面。""" + _WEB_FICTION_VOICE,
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
