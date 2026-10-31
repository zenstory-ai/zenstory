"""writing_style 的来源：只记作者原话或作者确认过的风格；AI 自己的风格设想不进这个字段，也不被当成交付要求。

只核对结构：规则落在记忆协议、工具说明和共享交付要求里，写作与审稿共用同一份交付要求，旧的「无条件服从 writing_style」措辞已删去。
"""

import pytest

from agent.prompts.base import TOOL_USAGE_PROTOCOL
from agent.prompts.subagents import AUTHOR_DELIVERY_REQUIREMENTS, QUALITY_REVIEWER_PROMPT, WRITER_PROMPT
from agent.tools.tool_schemas import UPDATE_PROJECT_TOOL


@pytest.mark.unit
def test_memory_rule_and_tool_schema_both_scope_writing_style_to_the_author():
    for text in (TOOL_USAGE_PROTOCOL, UPDATE_PROJECT_TOOL["description"]):
        assert "writing_style" in text
        assert "作者明确确认" in text


@pytest.mark.unit
def test_writer_and_reviewer_share_one_author_sourced_delivery_requirement():
    for prompt in (WRITER_PROMPT, QUALITY_REVIEWER_PROMPT):
        assert prompt.count(AUTHOR_DELIVERY_REQUIREMENTS) == 1
    # 旧措辞把任何 writing_style 都当硬要求；题材也不能单独推翻作者自己选的文风。
    assert "本轮指令、项目 writing_style、" not in AUTHOR_DELIVERY_REQUIREMENTS
    assert "或题材不符" not in AUTHOR_DELIVERY_REQUIREMENTS
