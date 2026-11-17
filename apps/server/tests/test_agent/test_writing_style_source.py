"""writing_style 的来源：只记作者原话或作者确认过的风格；AI 自己的风格设想不进这个字段，也不被当成交付要求。

只核对结构：规则落在记忆协议与工具说明里。
"""

import pytest

from agent.prompts.base import LOOP_TOOL_USAGE_PROTOCOL
from agent.tools.tool_schemas import UPDATE_PROJECT_TOOL


@pytest.mark.unit
def test_memory_rule_and_tool_schema_both_scope_writing_style_to_the_author():
    for text in (LOOP_TOOL_USAGE_PROTOCOL, UPDATE_PROJECT_TOOL["description"]):
        assert "writing_style" in text
        assert "作者明确确认" in text
