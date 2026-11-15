from __future__ import annotations

import pytest
from jinja2 import TemplateNotFound

from prompts.prompt_loader import (
    PromptLoader,
    _get_cached_loader,
    create_character_mention_prompt,
    get_prompt,
)


@pytest.mark.parametrize("novel_type", ["invalid", "published"])
def test_prompt_loader_rejects_unknown_novel_type(novel_type: str):
    with pytest.raises(ValueError) as excinfo:
        PromptLoader(novel_type)

    assert "不支持的小说类型" in str(excinfo.value)


def test_prompt_loader_lists_available_prompts_for_web_long():
    loader = PromptLoader("web_long")

    available = loader.list_available_prompts()

    assert "character_mention" in available
    assert "chapter_summary" in available
    assert "plot_extraction" in available


def test_prompt_loader_missing_template_reports_available_choices():
    loader = PromptLoader("web_long")

    with pytest.raises(TemplateNotFound) as excinfo:
        loader.load("missing_template")

    message = str(excinfo.value)
    assert "missing_template" in message
    assert "character_mention" in message


def test_cached_loader_is_reused_and_get_prompt_renders_template():
    _get_cached_loader.cache_clear()

    first = _get_cached_loader("web_long")
    second = _get_cached_loader("web_long")
    rendered = get_prompt("plot_extraction", word_count=1200)

    assert first is second
    assert "10-15个" in rendered
    assert "1200" in rendered


def test_compatibility_function_matches_get_prompt_output():
    compat_prompt = create_character_mention_prompt(novel_type="web_short")
    direct_prompt = get_prompt("character_mention", novel_type="web_short")

    assert compat_prompt == direct_prompt
    assert "chapter_importance" in compat_prompt
