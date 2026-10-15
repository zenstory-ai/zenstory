"""题材规范按项目类型选择，接在 DB 配置之后；不比对规范全文。"""

import pytest

from agent import prompts
from core.error_handler import APIException

FOLDER_IDS = {
    "lore": "lore-id",
    "character": "character-id",
    "outline": "outline-id",
    "draft": "draft-id",
    "script": "script-id",
    "material": "material-id",
}


def _config(role: str) -> dict:
    return {
        "role_definition": role,
        "capabilities": "",
        "directory_structure": "",
        "content_structure": "",
        "file_types": "",
        "writing_guidelines": f"{role} guidelines",
        "include_dialogue_guidelines": False,
    }


@pytest.fixture
def db_configs(monkeypatch):
    configs = {t: _config(f"DB {t}") for t in ("novel", "short", "screenplay")}
    monkeypatch.setattr(prompts, "_load_db_configs", lambda: configs)
    return configs


@pytest.mark.parametrize("project_type", ["novel", "short", "screenplay"])
def test_each_project_type_gets_its_own_norms_after_db_config(db_configs, project_type):
    rendered = prompts.get_prompt_for_project_type(project_type, "project-1", FOLDER_IDS)

    norms = prompts.get_genre_norms(project_type)
    assert norms
    assert rendered.endswith(norms)
    assert rendered.index(f"DB {project_type} guidelines") < rendered.index(norms)
    others = [v for k, v in prompts.GENRE_NORMS.items() if k != project_type]
    assert not any(other in rendered for other in others)


def test_unlisted_type_keeps_its_db_config_without_borrowing_norms(db_configs):
    db_configs["custom"] = _config("DB custom")
    rendered = prompts.get_prompt_for_project_type("custom", "project-1", FOLDER_IDS)

    assert "DB custom guidelines" in rendered
    assert prompts.get_genre_norms("custom") == ""
    assert not any(norms in rendered for norms in prompts.GENRE_NORMS.values())


@pytest.mark.parametrize("project_type", ["novel", "short", "screenplay"])
def test_missing_db_config_still_raises_503_before_norms(monkeypatch, project_type):
    monkeypatch.setattr(prompts, "_load_db_configs", lambda: {})

    with pytest.raises(APIException) as excinfo:
        prompts.get_prompt_for_project_type(project_type, "project-1", FOLDER_IDS)

    assert excinfo.value.status_code == 503


def test_screenplay_norms_end_episode_on_the_hook_without_end_marker():
    """短剧规范要盖过 DB 配置里的「【本集完】」：planner 直接写剧本时不读 WRITER_PROMPT，只看题材规范。"""
    norms = prompts.get_genre_norms("screenplay")

    assert "【本集完】" in norms
    assert "钩子就是剧本最后一行" in norms
    # 每类不超过 8 条
    assert sum(1 for line in norms.splitlines() if line.startswith("- ")) <= 8
