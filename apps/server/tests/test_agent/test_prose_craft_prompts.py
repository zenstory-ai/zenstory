"""行文治理提示（节奏、元说明、指代、信息断点、逻辑断点、篇幅、同质化）的结构契约。

只检查原则落在哪个角色、写作与审稿是否成对、固定前缀不含每轮变化的内容，
不比对整段文案，也不证明生成质量。见
.agents/notes/implemented/bug-fix/2026-10-31-prose-craft-and-continuity-self-check.md。
"""

import re

import pytest

from agent.context.assembler import PREVIOUS_CHAPTER_NOTE, ContextAssembler
from agent.graph.nodes import FAST_MODE_DIRECTIVE
from agent.graph.writing_graph import FAST_MODE_REVIEW_REFUSAL
from agent.prompts import GENRE_NORMS
from agent.prompts.base import get_base_prompt
from agent.prompts.subagents import QUALITY_REVIEWER_PROMPT, WRITER_PROMPT
from agent.schemas.context import ContextItem


def _section(prompt: str, heading: str) -> str:
    start = prompt.index(heading)
    nxt = prompt.find("\n## ", start + len(heading))
    return prompt[start:] if nxt == -1 else prompt[start:nxt]


@pytest.mark.unit
def test_writer_has_one_prose_section_covering_the_five_passes():
    prose = _section(WRITER_PROMPT, "## 行文")
    # 每条原则对应一个可观察的写法，审稿人用同样的名称检查
    for principle in ("节奏", "句式", "对白", "元说明", "指代", "首次出现", "接上前一章", "一致", "起名"):
        assert principle in prose, principle


@pytest.mark.unit
def test_reviewer_checks_mirror_writer_principles():
    """写作给出的每类原则，审稿都有可检查的对应项，双方用同一组名称。"""
    review = _section(QUALITY_REVIEWER_PROMPT, "## 审查方法")
    for check in ("节奏", "句式", "元说明", "指代", "信息断点", "矛盾", "内容红线"):
        assert check in review or check in QUALITY_REVIEWER_PROMPT, check


@pytest.mark.unit
def test_length_overflow_is_split_or_rewritten_not_trimmed_piecemeal():
    """超长章节逐段压缩容易分几轮仍超时；写作与审稿的超长处理须一致。"""
    for prompt in (WRITER_PROMPT, QUALITY_REVIEWER_PROMPT):
        assert "场景边界拆" in prompt
        assert "整章一次" in prompt
    assert "删到区间内" not in QUALITY_REVIEWER_PROMPT
    assert "用一次修改把冗长处收紧" not in WRITER_PROMPT
    # 拆章有前提：只拆最新一章、下一章编号空着、范围不限定只写这一章；内容移动而不是复制
    for prompt in (WRITER_PROMPT, QUALITY_REVIEWER_PROMPT):
        assert "最新一章" in prompt
        assert "下一章编号还没有文件" in prompt
        assert "不在两章重复保留" in prompt
    assert "拆章时不把段落复制进新章" not in WRITER_PROMPT
    # 作者区间是硬要求，推断区间只作参考（共享的交付要求里只说一次）
    assert QUALITY_REVIEWER_PROMPT.count("只作参考") == WRITER_PROMPT.count("只作参考") >= 1


@pytest.mark.unit
def test_no_default_name_seeds_or_name_blacklists_in_role_prompts():
    """命名原则用规则表述：提示里不出现用滥的默认人名，既不当示例也不当禁词。"""
    combined = "\n".join([WRITER_PROMPT, QUALITY_REVIEWER_PROMPT, *GENRE_NORMS.values()])
    for name in ("苏晚", "沈砚", "陈默"):
        assert name not in combined


@pytest.mark.unit
def test_screenplay_norms_pay_off_the_premise_early_without_parenthetical_stacking():
    norms = GENRE_NORMS["screenplay"]
    assert "前 1–3 集" in norms
    assert "·" in WRITER_PROMPT  # 写作规则点名不用「·」叠加括号提示
    assert "误读" in WRITER_PROMPT


@pytest.mark.unit
def test_fast_mode_self_check_lives_in_the_run_directive_not_the_prefix():
    """快速模式不送审：自查要求放在运行指令和交接拒绝说明里，固定前缀只写通用的成稿核对。"""
    for text in (FAST_MODE_DIRECTIVE, FAST_MODE_REVIEW_REFUSAL):
        assert "自查" in text
        assert "角色卡" in text
    assert "快速模式" not in WRITER_PROMPT


@pytest.mark.unit
def test_fast_mode_summary_never_steers_the_writer_to_downgrade_the_delivery():
    """快速模式的总结不向作者推荐换模式：两段说明都不出现模式名或「切换」之类的引导。"""
    for text in (FAST_MODE_DIRECTIVE, FAST_MODE_REVIEW_REFUSAL):
        assert "质量模式" not in text
        assert "切换" not in text
        assert "没有审稿，可以" not in text


@pytest.mark.unit
def test_fast_mode_keeps_a_neutral_note_when_the_author_asked_for_review():
    """作者明确要求审稿时，快速模式不送审也不能沉默：两段说明都保留一句「这轮没有安排审稿」的告知，
    并告诉作者怎样再要审稿（再发一句「审一下」）。"""
    for text in (FAST_MODE_DIRECTIVE, FAST_MODE_REVIEW_REFUSAL):
        assert "要求了审稿" in text
        assert "没有安排审稿" in text
        assert "审一下" in text


@pytest.mark.unit
def test_role_prompts_and_norms_are_static_prefix_text():
    """角色提示与题材规范是固定文本，没有残留占位符；系统提示两次装配逐字相同。

    只有题材规范在系统提示的固定前缀里；角色提示在运行指令（输入最后一条消息）里，
    每次运行的第一次调用照样未命中缓存，这里只保证它们不含每轮变化的内容。
    """
    for text in (WRITER_PROMPT, QUALITY_REVIEWER_PROMPT, *GENRE_NORMS.values()):
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
