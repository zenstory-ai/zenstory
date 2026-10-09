"""按作者这一轮的要求收窄工作流（2026-10-09 新用户审计 P2-13 / P2-16 / P2-21 / P3-16）。

走真实的 run_writing_workflow_streaming 和 router_node，只 mock 路由那次 LLM 调用和每个
agent 的事件流。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.author_facing_text import (
    AgentTextShaper,
    author_facing_text,
    is_english_process_narration,
)
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.author_scope import (
    PLANNED_WRITER_AFTER_QUESTION_NOTE,
    SCOPE_DIRECTIVE_CLARIFY_FIRST,
    SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING,
    chat_only_plan_units,
    is_vague_edit_request,
)


def _route(payload: dict[str, object]) -> AsyncMock:
    return AsyncMock(
        return_value={"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}
    )


async def _run(route_mock, fake_agent, *, message, messages=None, max_iterations=6):
    from agent.graph.writing_graph import run_writing_workflow_streaming

    environ = {"AGENT_ROUTER_STRATEGY": "llm", "AGENT_ENABLE_GRAPH_AUTO_REVIEW": "false"}
    with (
        patch.dict("os.environ", environ),
        patch("agent.graph.router._route_with_deepseek_chat", route_mock),
        patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
    ):
        return [
            event
            async for event in run_writing_workflow_streaming(
                state={
                    "user_message": message,
                    "router_message": message,
                    "messages": list(messages or []),
                    "system_prompt": "",
                },
                thread_id="author-scope",
                max_iterations=max_iterations,
                auto_review_threshold=50,
            )
        ]


def _texts(events) -> list[str]:
    return [str(e.data.get("text") or "") for e in events if e.type == StreamEventType.TEXT]


def _tool(name: str, call_id: str = "t1") -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.TOOL_USE,
        data={"id": call_id, "name": name, "status": "complete", "input": {}},
    )


# ------------------------------------------------------------------ P2-21


async def test_planner_question_keeps_writer_when_author_asked_for_prose():
    """作者首条就要「…大纲，然后直接写第一章」：规划师按老习惯问一句「要我开始写吗？」，
    不能把计划里的 writer 清掉——作者的话就是答案。"""
    calls: list[str] = []
    seen: dict[str, dict] = {}

    async def fake_agent(state, agent_type, **_kwargs):
        calls.append(agent_type)
        seen[agent_type] = {
            "scope": str(state.get("scope_directive") or ""),
            "message": str(state.get("user_message") or ""),
        }
        if agent_type == "planner":
            yield _tool("create_file")
            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "大纲写进《核心大纲》了。\n\n要我按这份大纲开始写第一章吗？"},
            )
            return
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "第一章写好了。"})

    events = await _run(
        _route({"agent_type": "planner", "workflow_type": "standard", "write_content": True,
                "scope": "大纲+第1章"}),
        fake_agent,
        message="帮我构思一个都市异能故事的大纲，然后直接写第一章",
    )

    assert calls == ["planner", "writer"]
    assert SCOPE_DIRECTIVE_WITH_CONTENT_AFTER_PLANNING in seen["planner"]["scope"]
    # writer 知道规划师问过、作者已经要正文：按推荐方案写，不再问。
    assert PLANNED_WRITER_AFTER_QUESTION_NOTE in seen["writer"]["message"]
    # 不重做、不扩大交付范围（P2-16 首轮自动交接追加了「拍摄执行版」）。
    assert "不追加作者没要的产出" in seen["writer"]["message"]
    assert any(e.type == StreamEventType.HANDOFF for e in events)


async def test_planner_question_still_waits_when_author_only_asked_for_a_framework():
    calls: list[str] = []

    async def fake_agent(_state, agent_type, **_kwargs):
        calls.append(agent_type)
        yield _tool("create_file")
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "要我按这份大纲开始写第一章吗？"})

    await _run(
        _route({"agent_type": "planner", "workflow_type": "standard", "write_content": False}),
        fake_agent,
        message="先帮我搭个故事框架",
    )

    assert calls == ["planner"]


# ------------------------------------------------------------------ P2-16


@pytest.mark.parametrize("message", ["帮我优化一下", "改改", "润色一下吧", "再帮我改一下。"])
def test_vague_edit_requests_are_recognised(message):
    assert is_vague_edit_request(message) is True


@pytest.mark.parametrize(
    "message",
    ["帮我优化一下第3集的台词", "把结尾改得狠一点", "继续写下一章", "帮我规划一下故事", "优化一下开头节奏"],
)
def test_specific_requests_are_not_vague(message):
    assert is_vague_edit_request(message) is False


async def test_vague_request_asks_once_before_editing_any_file():
    """「帮我优化一下」：不走路由、不交接、不写文件，writer 先问清楚改哪里。"""
    route = _route({"agent_type": "writer", "workflow_type": "quick", "write_content": True})
    calls: list[str] = []
    seen: dict[str, object] = {}

    async def fake_agent(state, agent_type, **_kwargs):
        calls.append(agent_type)
        seen["scope"] = state.get("scope_directive")
        seen["clarify_first"] = state.get("clarify_first")
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "想先优化哪部分？"})
        # 模型不听话想交给规划师：这一轮只问，不交接。
        yield StreamEvent(
            type=StreamEventType.HANDOFF,
            data={"target_agent": "planner", "reason": "重新规划", "context": "优化三集"},
        )

    events = await _run(route, fake_agent, message="帮我优化一下")

    route.assert_not_called()
    assert calls == ["writer"]
    assert seen["scope"] == SCOPE_DIRECTIVE_CLARIFY_FIRST
    # 工具层：runner 据此把写文件工具换成「先问清楚」的拒绝说明。
    assert seen["clarify_first"] is True
    decided = next(e for e in events if e.type == StreamEventType.ROUTER_DECIDED)
    assert decided.data["routing_metadata"]["clarify_first"] is True
    assert decided.data["workflow_agents"] == []


async def test_vague_reply_to_the_ais_question_is_not_asked_again():
    """上一轮 AI 以提问收尾（或已经为笼统要求问过一次），作者回「改改」就是在回答。"""
    calls: list[str] = []

    async def fake_agent(state, agent_type, **_kwargs):
        calls.append(agent_type)
        assert state.get("clarify_first") is not True
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "改好了。"})

    asked = [
        {"role": "user", "content": "帮我优化一下"},
        {"role": "assistant", "content": "改了一处。", "routing": {"initial_agent": "writer", "clarify_first": True}},
    ]
    route = _route({"agent_type": "writer", "workflow_type": "quick", "write_content": True})
    await _run(route, fake_agent, message="改改", messages=asked)

    route.assert_called_once()
    assert calls == ["writer"]


def test_clarify_first_tools_refuse_with_their_own_reason():
    from agent.openai_agents.tools_adapter import (
        CLARIFY_FIRST_TOOL_DESCRIPTION_PREFIX,
        READ_ONLY_REASON_CLARIFY_FIRST,
        build_agent_function_tools,
    )
    from agent.tools.registry import FILE_WRITE_TOOL_NAMES

    tools = build_agent_function_tools(
        "writer", read_only=True, read_only_reason=READ_ONLY_REASON_CLARIFY_FIRST
    )
    for tool in tools:
        marked = tool.description.startswith(CLARIFY_FIRST_TOOL_DESCRIPTION_PREFIX)
        assert marked is (tool.name in FILE_WRITE_TOOL_NAMES), tool.name


async def test_clarify_first_write_attempt_is_refused_without_executing():
    from agent.openai_agents.tools_adapter import (
        READ_ONLY_REASON_CLARIFY_FIRST,
        build_agent_function_tools,
    )

    executed: list[str] = []

    async def fake_tool(_args):
        executed.append("called")
        return {"content": [{"type": "text", "text": json.dumps({"status": "success"})}]}

    with patch.dict("agent.openai_agents.tools_adapter.TOOL_FUNCTIONS", {"edit_file": fake_tool}):
        tools = {
            tool.name: tool
            for tool in build_agent_function_tools(
                "writer", read_only=True, read_only_reason=READ_ONLY_REASON_CLARIFY_FIRST
            )
        }
        refused = json.loads(await tools["edit_file"].on_invoke_tool(None, '{"id": "x", "edits": []}'))

    assert refused["error_type"] == "clarify_first"
    assert "问作者" in refused["error"]
    assert executed == []


# ------------------------------------------------------------------ P2-13

_TEN_CHAPTER_PLAN = "前十章大纲如下：\n" + "\n".join(
    f"- 第{n}章 手背上的账（陈砚发现倒计时第{n}次跳动，被迫做出新的选择并付出代价）" for n in range(1, 11)
)


def test_chat_plan_detection_ignores_text_written_into_a_file():
    assert chat_only_plan_units(_TEN_CHAPTER_PLAN) == list(range(1, 11))
    assert chat_only_plan_units(f"<file>\n{_TEN_CHAPTER_PLAN}\n</file>\n已写进《前十章大纲》。") == []
    assert chat_only_plan_units("第1章写好了。") == []


async def test_plan_left_only_in_chat_is_saved_to_an_outline_file():
    """作者要的「前十章大纲」只贴在对话里时，规划师补一轮把它写进大纲文件（只补一次）。"""
    calls: list[str] = []
    messages: list[str] = []

    async def fake_agent(state, agent_type, **_kwargs):
        calls.append(agent_type)
        messages.append(str(state.get("user_message") or ""))
        yield _tool("update_project")
        # 第二轮仍然贴出来也不再补第三次。
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": _TEN_CHAPTER_PLAN})

    await _run(
        _route({"agent_type": "planner", "workflow_type": "standard", "write_content": False}),
        fake_agent,
        message="先给我列一下前十章的大纲",
    )

    assert calls == ["planner", "planner"]
    assert "还没有写进文件" in messages[1]
    assert "第1–10" in messages[1]


async def test_plan_already_written_to_a_file_is_not_redone():
    calls: list[str] = []

    async def fake_agent(_state, agent_type, **_kwargs):
        calls.append(agent_type)
        yield _tool("create_file")
        yield StreamEvent(
            type=StreamEventType.TEXT,
            data={"text": f"<file>\n{_TEN_CHAPTER_PLAN}\n</file>\n前十章大纲写进《前十章大纲》了。"},
        )

    await _run(
        _route({"agent_type": "planner", "workflow_type": "standard", "write_content": False}),
        fake_agent,
        message="先给我列一下前十章的大纲",
    )

    assert calls == ["planner"]


# ------------------------------------------------------------------ P3-16


async def test_english_narration_before_a_tool_call_is_not_shown_and_agents_do_not_glue():
    """审计原样：「Now create the 分集大纲 file.」出现在聊天里；规划师的「…定死」和内容
    创作者的「我先确认三个剧本文件的当前内容。」粘成一句。"""

    async def fake_agent(_state, agent_type, **_kwargs):
        if agent_type == "planner":
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Now create "})
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "the 分集大纲 file."})
            yield _tool("create_file")
            yield StreamEvent(
                type=StreamEventType.TEXT,
                data={"text": "「先补角色卡」——把三人的说话习惯和外形标签定死"},
            )
            return
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "我先确认三个剧本文件的当前内容。"})

    events = await _run(
        _route({"agent_type": "planner", "workflow_type": "standard", "write_content": True}),
        fake_agent,
        message="做一个竖屏短剧，先给我大纲和前三集剧本。",
    )

    joined = "".join(_texts(events))
    assert "Now create" not in joined
    assert "定死\n\n我先确认" in joined


async def test_english_author_keeps_english_narration():
    async def fake_agent(_state, _agent_type, **_kwargs):
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Now create the outline file."})
        yield _tool("create_file")
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Done."})

    events = await _run(
        _route({"agent_type": "writer", "workflow_type": "quick", "write_content": True}),
        fake_agent,
        message="Write an outline for my thriller.",
    )

    assert "Now create the outline file." in "".join(_texts(events))


def test_text_shaper_keeps_chinese_and_file_bodies_streaming():
    shaper = AgentTextShaper(previous_text="", drop_english_narration=True)
    # 中文立刻发出去，不等到工具调用。
    assert shaper.feed("第三章写好了，") == "第三章写好了，"
    assert shaper.end_segment(before_tool_call=True) == ""
    # <file> 正文不攒。
    assert shaper.feed("<file>\nHe said") == "<file>\nHe said"
    assert shaper.end_segment(before_tool_call=False) == ""
    # 最后的英文总结（不是紧接着工具调用）照常发。
    assert shaper.feed("OK") == ""
    assert shaper.end_segment(before_tool_call=False) == "OK"
    assert is_english_process_narration("Now create the 分集大纲 file.") is True
    assert is_english_process_narration("OK，第三章写好了") is False


def test_review_rule_jargon_is_not_shown_to_authors():
    reason = "与第1章逐字重复47字（超过15字阈值，按阻断处理）；手背倒计时跨章时间线矛盾"
    cleaned = author_facing_text(reason)
    assert "阈值" not in cleaned and "处理" not in cleaned and "阻断" not in cleaned
    assert cleaned.startswith("与第1章逐字重复47字")
    assert "时间线矛盾" in cleaned
    assert "阈值" not in author_facing_text("重复47字，超过15字阈值，按阻断处理，需要改")
