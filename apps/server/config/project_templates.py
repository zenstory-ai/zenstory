"""
Project templates configuration with i18n support.

Defines folder structures and file type mappings for different project types:
- novel: Long-form novel (5万字+)
- short: Short story (5000-20000字)
- screenplay: Short drama script

Supports both Chinese (zh) and English (en) languages.
"""

from typing import Any, cast

# Project template definitions - Chinese (default)
PROJECT_TEMPLATES_ZH: dict[str, dict[str, Any]] = {
    "novel": {
        "name": "长篇小说",
        "description": "适合 5 万字以上的连载小说",
        "icon": "book",
        "folders": [
            {"id": "lore-folder", "title": "设定", "file_type": "folder", "order": 0},
            {"id": "character-folder", "title": "角色", "file_type": "folder", "order": 1},
            {"id": "material-folder", "title": "素材", "file_type": "folder", "order": 2},
            {"id": "outline-folder", "title": "大纲", "file_type": "folder", "order": 3},
            {"id": "draft-folder", "title": "正文", "file_type": "folder", "order": 4},
        ],
        "file_type_mapping": {
            "设定": "lore",
            "角色": "character",
            "素材": "snippet",
            "大纲": "outline",
            "正文": "draft",
        },
        "default_project_name": "我的小说",
    },
    "short": {
        "name": "短篇小说",
        "description": "适合 5000-20000 字的独立短篇",
        "icon": "file-text",
        "folders": [
            {"id": "character-folder", "title": "人物", "file_type": "folder", "order": 0},
            {"id": "outline-folder", "title": "构思", "file_type": "folder", "order": 1},
            {"id": "material-folder", "title": "素材", "file_type": "folder", "order": 2},
            {"id": "draft-folder", "title": "正文", "file_type": "folder", "order": 3},
        ],
        "file_type_mapping": {
            "人物": "character",
            "构思": "outline",
            "素材": "snippet",
            "正文": "draft",
        },
        "default_project_name": "我的短篇",
    },
    "screenplay": {
        "name": "短剧剧本",
        "description": "适合短视频剧本、微短剧创作",
        "icon": "clapperboard",
        "folders": [
            {"id": "character-folder", "title": "角色", "file_type": "folder", "order": 0},
            {"id": "lore-folder", "title": "设定", "file_type": "folder", "order": 1},
            {"id": "material-folder", "title": "素材", "file_type": "folder", "order": 2},
            {"id": "outline-folder", "title": "分集大纲", "file_type": "folder", "order": 3},
            {"id": "script-folder", "title": "剧本", "file_type": "folder", "order": 4},
        ],
        "file_type_mapping": {
            "角色": "character",
            "设定": "lore",
            "素材": "snippet",
            "分集大纲": "outline",
            "剧本": "script",
        },
        "default_project_name": "我的短剧",
    },
}

# Project template definitions - English
PROJECT_TEMPLATES_EN: dict[str, dict[str, Any]] = {
    "novel": {
        "name": "Novel",
        "description": "Suitable for serialized novels over 50k words",
        "icon": "book",
        "folders": [
            {"id": "lore-folder", "title": "World Building", "file_type": "folder", "order": 0},
            {"id": "character-folder", "title": "Characters", "file_type": "folder", "order": 1},
            {"id": "material-folder", "title": "Materials", "file_type": "folder", "order": 2},
            {"id": "outline-folder", "title": "Outlines", "file_type": "folder", "order": 3},
            {"id": "draft-folder", "title": "Drafts", "file_type": "folder", "order": 4},
        ],
        "file_type_mapping": {
            "World Building": "lore",
            "Characters": "character",
            "Materials": "snippet",
            "Outlines": "outline",
            "Drafts": "draft",
        },
        "default_project_name": "My Novel",
    },
    "short": {
        "name": "Short Story",
        "description": "Suitable for standalone short stories of 5k-20k words",
        "icon": "file-text",
        "folders": [
            {"id": "character-folder", "title": "Characters", "file_type": "folder", "order": 0},
            {"id": "outline-folder", "title": "Concept", "file_type": "folder", "order": 1},
            {"id": "material-folder", "title": "Materials", "file_type": "folder", "order": 2},
            {"id": "draft-folder", "title": "Drafts", "file_type": "folder", "order": 3},
        ],
        "file_type_mapping": {
            "Characters": "character",
            "Concept": "outline",
            "Materials": "snippet",
            "Drafts": "draft",
        },
        "default_project_name": "My Short Story",
    },
    "screenplay": {
        "name": "Short Drama Script",
        "description": "Suitable for short video scripts and mini-dramas",
        "icon": "clapperboard",
        "folders": [
            {"id": "character-folder", "title": "Characters", "file_type": "folder", "order": 0},
            {"id": "lore-folder", "title": "World Building", "file_type": "folder", "order": 1},
            {"id": "material-folder", "title": "Materials", "file_type": "folder", "order": 2},
            {"id": "outline-folder", "title": "Episode Outlines", "file_type": "folder", "order": 3},
            {"id": "script-folder", "title": "Scripts", "file_type": "folder", "order": 4},
        ],
        "file_type_mapping": {
            "Characters": "character",
            "World Building": "lore",
            "Materials": "snippet",
            "Episode Outlines": "outline",
            "Scripts": "script",
        },
        "default_project_name": "My Drama",
    },
}

# All language templates
PROJECT_TEMPLATES_BY_LANG: dict[str, dict[str, dict[str, Any]]] = {
    "zh": PROJECT_TEMPLATES_ZH,
    "en": PROJECT_TEMPLATES_EN,
}

# Default templates (Chinese)
PROJECT_TEMPLATES = PROJECT_TEMPLATES_ZH


def get_template_by_type(project_type: str, lang: str = "zh") -> dict[str, Any]:
    """
    Get template configuration for a project type.

    Args:
        project_type: Type of project (novel, short, screenplay)
        lang: Language code (zh, en), defaults to 'zh'

    Returns:
        Template configuration dict, defaults to novel if type not found
    """
    templates = PROJECT_TEMPLATES_BY_LANG.get(lang, PROJECT_TEMPLATES_ZH)
    return templates.get(project_type, templates["novel"])


def get_folders_for_type(project_type: str, lang: str = "zh") -> list[dict[str, Any]]:
    """
    Get folder configurations for a project type.

    Args:
        project_type: Type of project
        lang: Language code (zh, en), defaults to 'zh'

    Returns:
        List of folder configuration dicts
    """
    template = get_template_by_type(project_type, lang)
    return cast("list[dict[str, Any]]", template.get("folders", []))


def get_file_type_mapping(project_type: str, lang: str = "zh") -> dict[str, str]:
    """
    Get file type mapping for a project type.

    Maps folder titles to file types for that folder.

    Args:
        project_type: Type of project
        lang: Language code (zh, en), defaults to 'zh'

    Returns:
        Dict mapping folder title to file type
    """
    template = get_template_by_type(project_type, lang)
    return cast("dict[str, str]", template.get("file_type_mapping", {}))


def get_default_project_name(project_type: str, lang: str = "zh") -> str:
    """
    Get default project name for a project type.

    Args:
        project_type: Type of project
        lang: Language code (zh, en), defaults to 'zh'

    Returns:
        Default project name string
    """
    template = get_template_by_type(project_type, lang)
    return cast("str", template.get("default_project_name", "我的项目"))


def get_project_templates(lang: str | None = None) -> dict[str, dict[str, Any]]:
    """
    Get all available project templates in a specific language.

    Args:
        lang: Language code (zh, en), returns default templates if None

    Returns:
        Dict of project type -> template configuration
    """
    if lang:
        return PROJECT_TEMPLATES_BY_LANG.get(lang, PROJECT_TEMPLATES_ZH)
    return PROJECT_TEMPLATES
