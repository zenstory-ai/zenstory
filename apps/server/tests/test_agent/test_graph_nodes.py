"""
Tests for agent/graph/nodes.py

Tests the graph-facing writing agent entrypoint and output evaluation helpers.
"""

from unittest.mock import patch

import pytest


@pytest.mark.unit
class TestEvaluationSignals:
    """Tests for heuristic evaluator and marker fallback behavior."""

    def test_detect_task_complete_explicit_marker(self):
        """[TASK_COMPLETE] at end of content → is_complete True, reason explicit_complete_marker."""
        from agent.graph.nodes import detect_task_complete

        result = detect_task_complete("这是最终输出内容。[TASK_COMPLETE]")
        assert result.is_complete is True
        assert result.confidence == 1.0
        assert result.reason == "explicit_complete_marker"

    def test_detect_task_complete_chinese_phrase_mid_text_no_longer_triggers(self):
        """'已完成' mid-text without the explicit marker must NOT flip should_complete (false positive fix)."""
        from agent.graph.nodes import detect_task_complete

        result = detect_task_complete(
            "任务已完成，最终结果如下：\n"
            "1. 已补齐章节结构与冲突线。\n"
            "2. 已修复角色名不一致问题并统一术语。\n"
            "3. 已完成文风润色、段落衔接和末尾悬念钩子优化。\n"
            "4. 关键改动与验证信息已整理完毕，准备交付。\n"
            "5. 附：涉及文件、测试结果、风险评估与后续建议均已在交付说明中完整列出。"
        )
        assert result.is_complete is False

    def test_detect_task_complete_empty_content_not_complete(self):
        """Empty / whitespace-only content → not complete."""
        from agent.graph.nodes import detect_task_complete

        assert detect_task_complete("").is_complete is False
        assert detect_task_complete("   ").is_complete is False


@pytest.mark.unit
class TestRunStreamingAgent:
    """Tests for the graph-facing streaming agent wrapper."""

    @pytest.mark.asyncio
    async def test_run_streaming_agent_persists_end_turn_assistant_text_for_downstream(self):
        """Assistant text from end_turn must be persisted into state.messages."""
        from agent.core.workflow_events import StreamEvent, StreamEventType
        from agent.graph.nodes import run_streaming_agent

        async def fake_openai_runner(
            state, agent_type, system_prompt, get_steering_messages=None, agent_directive=""
        ):
            assert agent_type == "planner"
            assert system_prompt == "base prompt"
            assert "## 当前角色：planner" in agent_directive
            assert get_steering_messages is None
            state["messages"] = [
                {"role": "user", "content": state["user_message"]},
                {"role": "assistant", "content": [{"type": "text", "text": "这是规划内容"}]},
            ]
            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "这是规划内容"},
            )
            yield StreamEvent(
                type=StreamEventType.MESSAGE_END,
                data={"stop_reason": "end_turn"},
            )

        state = {
            "user_message": "请先给我计划",
            "messages": [],
            "system_prompt": "base prompt",
        }

        with patch("agent.graph.nodes.run_openai_agents_streaming_agent", new=fake_openai_runner):
            events = [
                event async for event in run_streaming_agent(state=state, agent_type="planner")
            ]

        assert any(event.type == StreamEventType.TEXT for event in events)
        assert len(state["messages"]) == 2
        assert state["messages"][0]["role"] == "user"
        assert state["messages"][1]["role"] == "assistant"
        assistant_content = state["messages"][1]["content"]
        assert isinstance(assistant_content, list)
        assert assistant_content[0]["type"] == "text"
        assert assistant_content[0]["text"] == "这是规划内容"


@pytest.mark.integration
class TestAgentToolsMap:
    """Tests for AGENT_TOOLS_MAP configuration."""

    def test_agent_tools_map_exists(self):
        """Test that AGENT_TOOLS_MAP is properly configured."""
        from agent.tools.registry import AGENT_TOOLS_MAP

        expected_agents = [
            "planner",
            "hook_designer",
            "writer",
            "quality_reviewer",
        ]

        for agent in expected_agents:
            assert agent in AGENT_TOOLS_MAP, f"Agent {agent} not in AGENT_TOOLS_MAP"

    def test_quality_reviewer_has_restricted_tools(self):
        """Test that quality_reviewer uses restricted tool set."""
        from agent.tools.registry import AGENT_TOOLS_MAP, get_agent_tools

        reviewer_tools = get_agent_tools("quality_reviewer")
        assert AGENT_TOOLS_MAP["quality_reviewer"] == reviewer_tools


@pytest.mark.unit
class TestAuthorReplyTail:
    """交接只做文件协议分隔，不根据问号或列表猜语义。"""

    def test_keeps_all_author_facing_text_after_last_closed_file(self):
        from agent.graph.completion_evidence import author_reply_tail

        assert author_reply_tail(
            "<file>这是正文。他问：‘你是谁？’</file>\n\n"
            "已保存第三章。\n- 改了开场\n- 保留了悬念\n[TASK_COMPLETE]"
        ) == "已保存第三章。\n- 改了开场\n- 保留了悬念"

    def test_returns_empty_when_the_last_file_is_unclosed(self):
        from agent.graph.completion_evidence import author_reply_tail

        assert author_reply_tail("前置说明\n<file>仍在写入的正文？") == ""

    def test_caps_the_complete_payload_including_truncation_marker(self):
        from agent.graph.completion_evidence import author_reply_tail

        result = author_reply_tail("<file>正文</file>\n" + "补" * 800, max_chars=600)

        assert len(result) <= 600
        assert "内容已截断" in result
        assert "正文" not in result


@pytest.mark.unit
class TestScopeDirectiveInjection:
    """state.scope_directive 必须进入每个 agent 运行的系统指令，系统提示保持共用。"""

    @pytest.mark.asyncio
    async def test_scope_directive_goes_to_the_run_directive_not_the_system_prompt(self):
        from agent.graph.nodes import run_streaming_agent

        captured: dict[str, str] = {}

        async def fake_openai_runner(
            state, agent_type, system_prompt, get_steering_messages=None, agent_directive=""
        ):
            captured["system_prompt"] = system_prompt
            captured["agent_directive"] = agent_directive
            if False:  # pragma: no cover - 让函数成为异步生成器
                yield None

        state = {
            "user_message": "只写第1章",
            "messages": [],
            "system_prompt": "base prompt",
            "scope_directive": "用户要求的交付范围：只写第1章。",
        }
        with patch("agent.graph.nodes.run_openai_agents_streaming_agent", new=fake_openai_runner):
            _ = [event async for event in run_streaming_agent(state=state, agent_type="writer")]

        assert captured["system_prompt"] == "base prompt"
        directive = captured["agent_directive"]
        assert "## 本轮用户范围约束 [最高优先级]" in directive
        assert directive.rstrip().endswith("用户要求的交付范围：只写第1章。")

    @pytest.mark.asyncio
    async def test_no_scope_directive_leaves_it_out(self):
        from agent.graph.nodes import build_agent_directive

        directive = build_agent_directive({"user_message": "写", "system_prompt": "base prompt"}, "writer")

        assert "## 当前角色：writer" in directive
        assert "本轮用户范围约束" not in directive
