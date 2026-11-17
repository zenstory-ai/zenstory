"""行文治理提示（节奏、元说明、指代、信息断点、逻辑断点、篇幅、同质化）的结构契约。

只检查固定前缀不含每轮变化的内容、题材规范与上下文标注的结构，
不比对整段文案，也不证明生成质量。见
.agents/notes/implemented/bug-fix/2026-10-31-prose-craft-and-continuity-self-check.md。
"""

import re

import pytest

from agent.context.assembler import PREVIOUS_CHAPTER_NOTE, ContextAssembler
from agent.loop.prompts import LOOP_WORKING_RULES
from agent.prompts import GENRE_NORMS
from agent.prompts.base import get_base_prompt
from agent.schemas.context import ContextItem


@pytest.mark.unit
def test_no_default_name_seeds_or_name_blacklists_in_the_prompts():
    """命名原则用规则表述：提示里不出现用滥的默认人名，既不当示例也不当禁词。"""
    combined = "\n".join([LOOP_WORKING_RULES, *GENRE_NORMS.values()])
    for name in ("苏晚", "沈砚", "陈默"):
        assert name not in combined


@pytest.mark.unit
def test_screenplay_norms_pay_off_the_premise_early_without_parenthetical_stacking():
    norms = GENRE_NORMS["screenplay"]
    assert "前 1–3 集" in norms
    assert "误读" in norms


@pytest.mark.unit
def test_working_rules_and_norms_are_static_prefix_text():
    """工作规则与题材规范是固定文本，没有残留占位符；系统提示两次装配逐字相同。"""
    for text in (LOOP_WORKING_RULES, *GENRE_NORMS.values()):
        assert not re.search(r"\{[A-Za-z_]+\}", text)
        assert PREVIOUS_CHAPTER_NOTE not in text
    config = {
        "role_definition": "R", "capabilities": "C", "directory_structure": "{draft}",
        "content_structure": "S", "file_types": "T", "writing_guidelines": "G",
    }
    first = get_base_prompt("p", {"draft": "d"}, config)
    assert first == get_base_prompt("p", {"draft": "d"}, config)
    assert PREVIOUS_CHAPTER_NOTE not in first


def _draft(item_id: str, title: str, relation: str | None) -> ContextItem:
    return ContextItem(
        id=item_id,
        type="outline",
        title=title,
        content=f"{title}的正文。",
        metadata={"file_type": "draft", "relation": relation},
    )


@pytest.mark.unit
def test_previous_chapter_is_labelled_as_plot_handoff_not_style_sample():
    """批量续写时碎句逐章自我强化：前一章条目前标明它只用于承接情节。"""
    context = ContextAssembler()._format_context(
        [_draft("a", "第9章", "previous"), _draft("b", "第3章", "sibling")]
    )
    assert context.count(PREVIOUS_CHAPTER_NOTE) == 1
    note_at = context.index(PREVIOUS_CHAPTER_NOTE)
    assert context.index("[previous] 第9章") < note_at < context.index("第9章的正文。")


@pytest.mark.unit
def test_context_without_previous_chapter_has_no_style_note():
    context = ContextAssembler()._format_context([_draft("b", "第3章", "sibling")])
    assert PREVIOUS_CHAPTER_NOTE not in context
