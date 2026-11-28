"""Project writing configurations come exclusively from the primary database.

Shared runtime protocols remain code; project content is administered in DB.
"""

from typing import Any

from sqlmodel import Session, select

from core.error_codes import ErrorCode
from core.error_handler import APIException
from database import sync_engine

from .base import (
    get_base_prompt,
    get_base_prompt_project_suffix,
    get_base_prompt_static_prefix,
)
from .suggestions import get_suggestion_prompt

# In-memory cache for database configurations
_db_config_cache: dict[str, dict[str, Any]] | None = None

# 题材规范接在 DB 项目配置之后；长篇只补核心看点的发展，不统一篇幅与节奏。
_GENRE_NORMS_PREAMBLE = "## 题材规范（常见写法；作者或项目设定另有要求时以作者为准；和上文通用格式说明不一致时以本节为准）"

# 网文口感：三类共用；是否适用由模型按作者给的书名、简介和已有章节判断，不做关键词判定。
_WEB_FICTION_VOICE = """
网文口感（仅在作者的书名、简介、梗概或已有章节属于中文网文、短剧写法时适用，如重生、系统、真假千金、逆袭打脸、萌宝、种田；作者明确要求偏现实、治愈、文艺、悬疑克制等风格时彻底服从作者，不硬套本节；与上文通用创作指导不一致时按这里写）：
- 简介里的主角身份、能力或金手指的来历、起点处境、关键人物关系，以及点名的事件走向和手段（谁主动做了什么、婚约、圣旨、某张符、某个系统）是核心前提，按原样写，不偷换设定、不把简介许诺的结果写反。
- 第一章（第一集）只写简介开头的一两个节点：开篇第一屏就让主角处在最抓人的处境里（被逼、被赶、被冤枉、生死对峙、交易上门），前史和身世用几句嵌在冲突里交代；写透一次交锋和一次兑现，简介后面的事件留给后续章节；不为制造悬念临时塞入没铺垫的新道具、规则、病名或新势力。
- 叙述贴着主角，直接写出人物的感受、判断和利害（委屈、愤怒、得意、谁在算计谁、这一下意味着什么），不靠器物、比喻、留白和欲言又止让读者自己猜；口吻随人物身份和题材，可以口语、吐槽、放狠话；喜剧向的简介写出喜感，不写成冷峻悲壮。
- 爽点、反击、恐怖或甜点当场兑现成看得见的结果：对手出丑、受损、改口，主角拿到具体的东西（钱、名额、等级、证据、一句认输）；不用「我知道」「等着瞧」「该你们睡不着了」这类预告或含笑不语代替兑现；一章写透一两次，不连续堆叠清算，兑现后不接落泪、回忆、感悟或「明天开始」式总结。
- 对白是在争东西：逼迫、讨价还价、拒绝、回击，每句接住上一句，说完局面有变化；反派的恶意可以藏在讲道理、装好人里；对白与具体动作、主角心里盘算交替推进，避免整页纯靠台词一问一答打乒乓，不写谜语式半截话。
- 段落轻快精炼，每段一两句、对白独立成段，每段推进一个动作、一句交锋或一个判断；抬眼、沉默、停顿、笑一声这类无信息反应不单独成段，也不插在两句交锋中间。
- 章末（集末）直接停在本章兑现的动作或由它直接引出的眼前压力上，不另起新线制造悬念、不写抒情旁白收尾；场景切换只用空行，不用「——」「---」「***」。
- 对照（只示意写法，不照抄句子）：不写「她垂下眼，指尖在杯沿停了很久。」，写「她心里一沉：这个家，从来没把她当过女儿。」；不写「他笑了笑：“你会后悔的。”」，写「他把转账记录拍在桌上：三百万，一笔不少，全是你这些年挪走的。二叔的脸当场白了。」；不单写一行「屋里安静了。」，写「屋里没人敢吭声，二婶憋了半天才挤出一句：“那钱……也不全是我拿的。”」"""

GENRE_NORMS: dict[str, str] = {
    "novel": _GENRE_NORMS_PREAMBLE + """
长篇：
- 从作者目标和前文辨认作品持续吸引读者的核心看点，在阶段剧情中继续发挥、发展它，不随情节升级丢掉原有特色；
- 让已建立的能力、关系和条件实际参与人物选择与事件结果，同时保留它们的限制和代价，避免用同类回合重复或单纯抬高数值代替进展。""" + _WEB_FICTION_VOICE,
    "short": _GENRE_NORMS_PREAMBLE + """
短篇（中文按网文短篇写；外文作品按该语言短篇小说的常见写法）：
- 前 200 字（英文作品约前 60 个单词）内抛出核心冲突或钩子；
- 围绕核心阅读期待推进，让情绪随事件、人物选择和关系变化形成层次；
- 段落轻快精炼、口语化，对白与具体行动交替推动情节，避免整段堆砌或无信息碎句；
- 常见篇幅 8000–15000 字（英文作品常见 3000–7500 个单词），按作者给的目标写；
- 结尾回应核心阅读期待，按题材与作者要求收束；回扣开头的意象或细节可选，避免重复总结与说教。""" + _WEB_FICTION_VOICE,
    "screenplay": _GENRE_NORMS_PREAMBLE + """
短剧（以下节奏与篇幅为竖屏短剧参考，其他体例按作者要求；中文剧本沿用下述中文剧本格式，非中文剧本严格使用该语言自身标准的剧本格式，不硬套中文场景标题或「△」）：
- 开场尽快进入与本集目标有关的冲突；前 1–3 集兑现剧名和简介许诺的核心卖点，每集新增的人物和线索有节制，优先推进已有的线；
- 每集 1–3 场、500–900 字仅作常见参考；实际场数与篇幅服从作者目标，时长结合台词、动作和停顿判断；
- 中文剧本场景标题写「集-场 地点 日/夜 内/外」，如「2-1 公司大堂 日 内」；所有出场角色起真实姓名（绝不用「女主」「庶妹」「男主」作角色名）；
- 中文剧本动作行用 △ 开头，只写能拍出来的画面和动作；
- 中文剧本台词写「角色：台词」（台词不加引号）；括号必须放在冒号前（如「角色（压低声音）：」），只在台词不加会被误读时才加极少数提示，凡是能拍出来的动作（抬头、扣手腕、扒车门等）一律单写 △ 动作行，不塞进括号，不用「·」叠加；
- 需要接续的集从本集行动、结果或关系变化中留下钩子，钩子是下一集要面对的具体压力（大纲里的承接点同样如此），不写感悟，钩子就是剧本最后一行；终集、独立短剧及作者要求收束时回应核心承诺，不强加新悬念；正文后不写「【本集完】」这类标注；
- 集号和文件名一致；
- 竖屏短剧可以用主角第一人称旁白开场（中文写成「角色（旁白）：」，外文按该语言标准旁白写法），一两句直接交代处境和卖点，再进入现场；旁白只交代观众看不出的，不复述画面。""" + _WEB_FICTION_VOICE,
}

_GENRE_NORMS_PREAMBLE_EN = (
    "## Genre Norms (Common Craft; Author or Project Requirements Always Take Precedence)"
)

_SERIAL_CRAFT_VOICE_EN = """
Serial & High-Hook Storytelling Craft (apply when the title, blurb, premise, or existing chapters lean toward commercial serial fiction, romance, fantasy, thriller, or vertical short drama; when the author requests literary, quiet, cozy, or understated realism, follow the author's tone completely):
- Honor the core premise verbatim: keep the protagonist's starting identity, special ability/rule, key relationships, and promised plot direction intact without flipping or contradicting them.
- Open strong: in Chapter 1 (or Episode 1), place the protagonist inside an engaging situation right away and weave brief backstory into live conflict rather than dumping exposition; pay off one concrete clash or turn while saving later premise beats for subsequent chapters.
- Stay close to the protagonist's stakes, choices, and immediate reactions; deliver concrete payoffs (evidence, leverage, status shifts, tangible consequences) on the page instead of vague foreshadowing lines ("you'll regret this").
- Make dialogue do real work — pressuring, negotiating, refusing, or countering so the scene shifts after each exchange; weave dialogue with physical action and character judgment rather than talking-head tennis.
- Keep paragraphs crisp and scannable; avoid empty reaction beats ("he stayed silent", "she paused") as standalone filler lines.
- End chapters on the decisive action, outcome, or immediate new pressure rather than trailing off into reflection or "tomorrow will be a new day" summaries; separate scenes with blank lines only."""

GENRE_NORMS_EN: dict[str, str] = {
    "novel": _GENRE_NORMS_PREAMBLE_EN + """
Novel:
- Identify the core reader appeal from the author's goal and prior chapters, and keep developing it as stakes rise without losing what makes the story distinctive.
- Let established abilities, relationships, and constraints actively shape choices and outcomes — preserve costs and limits instead of repeating identical beats or inflating numbers.""" + _SERIAL_CRAFT_VOICE_EN,
    "short": _GENRE_NORMS_PREAMBLE_EN + """
Short Story:
- Introduce the central conflict or hook within roughly the first 60 words (or equivalent in the manuscript's language).
- Build emotional escalation around the story's core promise through concrete choices, turns, and shifting relationships.
- Keep paragraphs brisk and natural, alternating dialogue with grounded action; avoid walls of summary or choppy fragment strings.
- Typical length is 3,000–7,500 words (follow whatever target the author specifies).
- Close by fulfilling the story's core emotional promise; echoing an opening image is optional, and avoid repetitive moralizing or wrap-up summaries.""" + _SERIAL_CRAFT_VOICE_EN,
    "screenplay": _GENRE_NORMS_PREAMBLE_EN + """
Short Drama / Episodic Script (vertical short-drama pacing below is a common reference; follow the author's requested format):
- Enter the episode's central conflict immediately; deliver the core hook promised by the title and logline within Episodes 1–3, introducing new characters and subplots sparingly.
- 1–3 scenes per episode (~500–900 words or ~1–2 minutes of screen time) is a common reference; follow the author's target length and pace runtime from spoken lines and shootable action.
- Format scene headings, action lines, dialogue, and voice-over strictly in the **manuscript's language** and the project's existing script conventions (for example, standard international script format such as `SCENE 2-1 - COMPANY LOBBY - INT. DAY`, concise present-tense action lines, `CHARACTER: Dialogue` or `CHARACTER (V.O.): ...` — never inject Chinese scene headings or `△` markers into non-Chinese scripts).
- Give every character a realistic, distinctive name fitting the story's setting (never placeholder labels like "Female Lead" or "Protagonist").
- Write only shootable visual action and audible dialogue; keep parentheticals rare and strictly for vocal delivery that would otherwise be misread — put physical gestures on their own action line.
- For continuing episodes, end directly on a concrete hook — an immediate threat, revelation, or turning pressure facing the next episode — as the script's final line with no end-of-episode meta tags. For finales or standalone scripts, deliver a satisfying payoff to the core promise.
- Keep episode numbers consistent with file titles; if opening with protagonist voice-over, keep it to 1–2 punchy lines that reveal what the camera cannot show, then cut straight into live action.""" + _SERIAL_CRAFT_VOICE_EN,
}

# English static project-type framing used when force_en=True (still strictly gated on an active DB
# SystemPromptConfig row existing for that project_type).
ENGLISH_PROJECT_TYPE_CONFIG: dict[str, dict[str, Any]] = {
    "novel": {
        "role_definition": (
            "You are a senior fiction editor and co-writer working inside ZenStory on a long-form novel project. "
            "You help the author plan arcs, maintain worldbuilding and character continuity, draft chapters, and revise prose."
        ),
        "capabilities": (
            "## Capabilities\n"
            "- Develop premises, volume/chapter outlines, character sheets, and worldbuilding lore.\n"
            "- Draft, continue, rewrite, and polish novel chapters while maintaining strict continuity with project files.\n"
            "- Diagnose pacing, hooks, character voices, and continuity issues across chapters."
        ),
        "content_structure": (
            "## Novel Project Structure\n"
            "- `lore`: world rules, magic/tech systems, factions, geography, and history.\n"
            "- `character`: character profiles, motives, arcs, and relationships.\n"
            "- `material`: original snippets, inspiration notes, and imported reference materials.\n"
            "- `outline`: overall synopsis, volume outlines, and chapter-by-chapter outlines.\n"
            "- `draft`: numbered manuscript chapters."
        ),
        "file_types": (
            "## File Types\n"
            "- `draft`: manuscript chapters (written via `write_chapter`).\n"
            "- `outline`, `character`, `lore`, `snippet`, `document`: reference and planning files (created via `create_file`)."
        ),
        "writing_guidelines": (
            "## Novel Writing Guidelines\n"
            "- Keep character voices, POV boundaries, timeline, and established lore consistent across all chapters.\n"
            "- Balance scene progression, tension, and character agency so every chapter moves the story forward."
        ),
        "directory_structure": (
            "## Project Directory Mapping\n"
            "- Lore / Worldbuilding folder: `{lore}`\n"
            "- Characters folder: `{character}`\n"
            "- Materials folder: `{material}`\n"
            "- Outlines folder: `{outline}`\n"
            "- Manuscript Drafts folder: `{draft}`"
        ),
    },
    "short": {
        "role_definition": (
            "You are a senior fiction editor and co-writer working inside ZenStory on a short story project. "
            "You help the author shape the emotional core, structure beats, draft, and polish tight, resonant short fiction."
        ),
        "capabilities": (
            "## Capabilities\n"
            "- Develop high-impact premises, beat sheets, and character dynamics tailored to short-form fiction.\n"
            "- Draft, expand, revise, and polish short stories with sharp pacing and emotional payoff."
        ),
        "content_structure": (
            "## Short Story Project Structure\n"
            "- `character`: character profiles and core relationships.\n"
            "- `outline`: concept notes, beat sheets, and story outlines.\n"
            "- `material`: original snippets and reference materials.\n"
            "- `draft`: short story manuscript files."
        ),
        "file_types": (
            "## File Types\n"
            "- `draft`: story manuscript (written via `write_chapter`).\n"
            "- `outline`, `character`, `snippet`, `document`: planning and reference files (created via `create_file`)."
        ),
        "writing_guidelines": (
            "## Short Story Guidelines\n"
            "- Focus tightly on the central conflict, emotional arc, and payoff; cut unnecessary subplots and exposition.\n"
            "- Make every scene and line of dialogue carry weight toward the ending."
        ),
        "directory_structure": (
            "## Project Directory Mapping\n"
            "- Characters folder: `{character}`\n"
            "- Concept / Outlines folder: `{outline}`\n"
            "- Materials folder: `{material}`\n"
            "- Manuscript Drafts folder: `{draft}`"
        ),
    },
    "screenplay": {
        "role_definition": (
            "You are a senior script editor and screenwriter working inside ZenStory on a short-drama / episodic screenplay project. "
            "You help the author build loglines, episode outlines, character sheets, and shootable scripts."
        ),
        "capabilities": (
            "## Capabilities\n"
            "- Design high-hook short-drama premises, synopses, and episode-by-episode outlines.\n"
            "- Write and tighten shootable scripts with vivid visual action, sharp subtext, and strong episode-end hooks."
        ),
        "content_structure": (
            "## Screenplay Project Structure\n"
            "- `character`: character profiles, relationships, and dramatic arcs.\n"
            "- `lore`: settings, locations, and story rules.\n"
            "- `material`: adaptation sources, reference clips, and snippets.\n"
            "- `outline`: series synopsis and episode outlines.\n"
            "- `script`: numbered episode scripts."
        ),
        "file_types": (
            "## File Types\n"
            "- `script`: episode scripts (written via `write_chapter` with `file_type='script'`).\n"
            "- `outline`, `character`, `lore`, `snippet`, `document`: planning and reference files (created via `create_file`)."
        ),
        "writing_guidelines": (
            "## Screenplay Guidelines\n"
            "- Show through camera-visible action and crisp dialogue; avoid unfilmable internal essays.\n"
            "- Keep episode pacing tight, conflict immediate, and hooks grounded in concrete dramatic stakes."
        ),
        "directory_structure": (
            "## Project Directory Mapping\n"
            "- Characters folder: `{character}`\n"
            "- Settings / Lore folder: `{lore}`\n"
            "- Materials folder: `{material}`\n"
            "- Episode Outlines folder: `{outline}`\n"
            "- Scripts folder: `{script}`"
        ),
    },
}


def get_genre_norms(project_type: str, *, force_en: bool = False) -> str:
    """Return the in-code genre norms appended after the DB config ("" when none)."""
    norms_map = GENRE_NORMS_EN if force_en else GENRE_NORMS
    return norms_map.get(project_type, "")


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


def get_prompt_parts_for_project_type(
    project_type: str,
    project_id: str,
    folder_ids: dict[str, str],
    *,
    force_en: bool = False,
) -> tuple[str, str]:
    """
    Return ``(static_prefix, project_suffix)`` for a project type.

    ``static_prefix`` contains only cross-project static rules (base protocols,
    DB type config, and in-code genre norms) so callers can append global loop
    rules and skill catalogs before the project-specific UUID suffix.

    Missing or inactive configurations fail explicitly; no cross-type fallback.
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

    effective_config = config
    if force_en and project_type in ENGLISH_PROJECT_TYPE_CONFIG:
        effective_config = {
            **ENGLISH_PROJECT_TYPE_CONFIG[project_type],
            "include_dialogue_guidelines": config.get("include_dialogue_guidelines", True),
        }

    static_prefix = get_base_prompt_static_prefix(effective_config, force_en=force_en)
    genre_norms = get_genre_norms(project_type, force_en=force_en)
    if genre_norms:
        static_prefix = f"{static_prefix}\n\n{genre_norms}"

    project_suffix = get_base_prompt_project_suffix(
        project_id, folder_ids, effective_config, force_en=force_en
    )
    return static_prefix, project_suffix


def get_prompt_for_project_type(
    project_type: str,
    project_id: str,
    folder_ids: dict[str, str],
    *,
    force_en: bool = False,
) -> str:
    """
    Get the complete system prompt for a project type.

    Missing or inactive configurations fail explicitly; no cross-type fallback.

    Args:
        project_type: Type of project (novel, short, screenplay)
        project_id: The project ID
        folder_ids: Dict mapping folder names to their IDs
        force_en: Whether to assemble the English static prompt bucket

    Returns:
        Complete system prompt string
    """
    static_prefix, project_suffix = get_prompt_parts_for_project_type(
        project_type, project_id, folder_ids, force_en=force_en
    )
    return f"{static_prefix}\n\n{project_suffix}"


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
    "ENGLISH_PROJECT_TYPE_CONFIG",
    "GENRE_NORMS",
    "GENRE_NORMS_EN",
    "get_base_prompt",
    "get_genre_norms",
    "get_prompt_for_project_type",
    "get_prompt_parts_for_project_type",
    "get_suggestion_prompt",
    "reload_prompts",
]
