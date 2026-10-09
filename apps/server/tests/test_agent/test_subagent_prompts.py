"""Tests for agent subagent prompts consistency."""

import pytest

from agent.prompts.subagents import QUALITY_REVIEWER_PROMPT, WRITER_PROMPT
from config.agent_runtime import AGENT_AUTO_REVIEW_THRESHOLD_CHARS


@pytest.mark.unit
class TestWriterPromptConsistency:
    """Ensure writer prompt stays aligned with runtime workflow config."""

    def test_writer_handoff_target_uses_quality_reviewer(self):
        """Writer prompt should hand off to quality_reviewer, not reviewer."""
        assert 'target_agent: "quality_reviewer"' in WRITER_PROMPT
        assert 'target_agent: "reviewer"' not in WRITER_PROMPT

    def test_writer_review_threshold_uses_runtime_constant(self):
        """Writer prompt threshold text should use centralized runtime threshold."""
        assert f"超过 **{AGENT_AUTO_REVIEW_THRESHOLD_CHARS}字**" in WRITER_PROMPT
        assert f"少于 {AGENT_AUTO_REVIEW_THRESHOLD_CHARS} 字" in WRITER_PROMPT
        assert f"写完 {AGENT_AUTO_REVIEW_THRESHOLD_CHARS} 字以上内容后直接结束" in WRITER_PROMPT

    def test_writer_reports_untouched_quote_issues_after_a_partial_edit(self):
        """局部修改只规范化改到的文字（P1b）且不送审：没改到的半角/无引号对白要告诉作者，不能说已统一。

        集成探测里 writer 改完第二章最后一段，全章还有 5 行半角引号、4 句没带引号的对白，
        回复却写「全角引号与第一章统一」。
        """
        assert "只改局部时" in WRITER_PROMPT
        assert "不要说全章引号已经统一" in WRITER_PROMPT


@pytest.mark.unit
class TestQualityReviewerPromptConsistency:
    """Ensure reviewer prompt stays aligned with restricted tool permissions."""

    def test_quality_reviewer_prompt_does_not_require_direct_edit_file(self):
        assert "必要时直接使用 `edit_file` 修正" not in QUALITY_REVIEWER_PROMPT
        # 错别字/标点只进报告，不为它们单独返工（返工交接每请求最多一次，只针对阻断问题）
        assert "错别字、标点、格式问题也需交接给 writer 修正" not in QUALITY_REVIEWER_PROMPT
        assert "错别字/标点列入报告" in QUALITY_REVIEWER_PROMPT
        assert "每次请求最多一次返工交接" in QUALITY_REVIEWER_PROMPT
        assert "除非是小的格式/标点修正" not in QUALITY_REVIEWER_PROMPT
        assert "### 已自动修正" not in QUALITY_REVIEWER_PROMPT


@pytest.mark.unit
def test_every_role_reuses_handoff_content_instead_of_rereading():
    from agent.prompts.subagents import (
        HANDOFF_READ_RULE,
        HOOK_DESIGNER_PROMPT,
        PLANNER_PROMPT,
    )

    for prompt in (PLANNER_PROMPT, WRITER_PROMPT, HOOK_DESIGNER_PROMPT, QUALITY_REVIEWER_PROMPT):
        assert HANDOFF_READ_RULE in prompt
    assert "返工回来时只改报告列出的位置" in WRITER_PROMPT
    assert "不再送审" in WRITER_PROMPT
