"""审稿人返修交回 writer 时，writer 的输入里不再带只发给审稿人的「[质量检查任务]」消息。

根因追查（2026-10-09，loop-rootcause.md）：writer → 审稿人 → writer 时，审稿人那次 run 的
user 消息「[质量检查任务] 请审查上一个 Agent 完成的内容…[待审查内容] <旧稿>」经
state["messages"] 原样回放给第二次 writer，模型在思考里把自己当成审稿人反复纠结。
交给非审稿人 agent 时只过滤这一类消息；作者原话、writer 自己的输出、交接信息照常保留，
审稿人自己那次 run 仍能看到它的任务。
"""

from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.openai_agents.runner import (
    _append_assistant_turn_to_state_messages,
    build_history_messages,
)
from agent.tools.mcp_tools import ToolContext

REVIEW_TASK_MARKER = "[质量检查任务]"
HANDOFF_MARKER = "[来自上一个Agent的交接信息]"
USER_REQUEST = "写第三章"
WRITER_DRAFT = "第三章正文。" * 30
REVIEW_NOTES = "必改1：第3章结尾没写完，补写结尾。"


def _texts(messages: list[dict]) -> list[str]:
    return [str(message.get("content") or "") for message in messages]


def _has_review_task(messages: list[dict]) -> bool:
    return any(REVIEW_TASK_MARKER in text for text in _texts(messages))


async def _run_writer_reviewer_writer(*, block_content: bool) -> list[tuple[str, list[dict]]]:
    """跑一遍 writer → 审稿人 → writer，返回每次 run 的 (agent_type, SDK input)。

    fake agent 与真实 runner 一样：用 build_history_messages(state) 构造 SDK 输入，
    run 结束后用 _append_assistant_turn_to_state_messages 把这一轮写回 state["messages"]。
    block_content=True 时，审稿人 run 结束后把 user 消息改存成 content block 列表
    （落库/其他来源的历史可能是这种形状）。
    """
    from agent.graph.writing_graph import run_writing_workflow_streaming

    runs: list[tuple[str, list[dict]]] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        api_messages = build_history_messages(state)
        runs.append((agent_type, [dict(message) for message in api_messages]))
        writer_runs = sum(1 for kind, _ in runs if kind == "writer")

        if agent_type == "quality_reviewer":
            text = REVIEW_NOTES
            events = [
                StreamEvent(type=StreamEventType.TEXT, data={"text": text}),
                StreamEvent(
                    type=StreamEventType.HANDOFF,
                    data={"target_agent": "writer", "reason": "返修", "context": REVIEW_NOTES},
                ),
            ]
        elif writer_runs == 1:
            text = WRITER_DRAFT
            events = [
                StreamEvent(type=StreamEventType.TEXT, data={"text": text}),
                StreamEvent(
                    type=StreamEventType.HANDOFF,
                    data={"target_agent": "quality_reviewer", "reason": "请审稿", "context": "请审稿"},
                ),
            ]
        else:
            text = "已补写结尾。[TASK_COMPLETE]"
            events = [StreamEvent(type=StreamEventType.TEXT, data={"text": text})]

        for event in events:
            yield event

        _append_assistant_turn_to_state_messages(
            state, api_messages, assistant_text=text, thinking_text="", tool_uses=[]
        )
        if block_content and agent_type == "quality_reviewer":
            state["messages"] = [
                {"role": message["role"], "content": [{"type": "text", "text": message["content"]}]}
                if message.get("role") == "user" and isinstance(message.get("content"), str)
                else message
                for message in state["messages"]
            ]

    state = {"user_message": USER_REQUEST, "messages": [], "system_prompt": ""}
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value={})),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
            patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
            patch(
                "agent.graph.writing_graph._auto_finalize_task_board_on_completion",
                AsyncMock(return_value=[]),
            ),
        ):
            async for _event in run_writing_workflow_streaming(
                state=state, thread_id="t", auto_review_threshold=100000
            ):
                pass
    finally:
        ToolContext.clear_context()
    return runs


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("block_content", [False, True], ids=["string-content", "block-content"])
async def test_writer_after_review_does_not_see_reviewer_task(block_content):
    runs = await _run_writer_reviewer_writer(block_content=block_content)

    assert [agent for agent, _ in runs] == ["writer", "quality_reviewer", "writer"]
    _, reviewer_input = runs[1]
    _, second_writer_input = runs[2]

    # 审稿人自己那次 run 仍然收到它的任务（含待审查内容）。
    assert reviewer_input[-1]["role"] == "user"
    assert str(reviewer_input[-1]["content"]).startswith(REVIEW_TASK_MARKER)

    # 第二次 writer 的 SDK 输入里不再有审稿任务。
    assert not _has_review_task(second_writer_input)

    # 作者原话、writer 自己的初稿、审稿人交回的交接信息都还在。
    texts = _texts(second_writer_input)
    assert texts[0] == USER_REQUEST
    assert any(WRITER_DRAFT in text for text in texts)
    assert second_writer_input[-1]["role"] == "user"
    assert texts[-1].startswith(HANDOFF_MARKER)
    assert REVIEW_NOTES in texts[-1]


@pytest.mark.unit
def test_reviewer_task_filter_handles_string_and_block_content():
    from agent.graph.writing_graph import _drop_reviewer_task_messages

    task = f"{REVIEW_TASK_MARKER}\n\n请审查上一个 Agent 完成的内容。\n\n交接信息: [待审查内容]\n旧稿"
    messages = [
        {"role": "user", "content": USER_REQUEST},
        {"role": "assistant", "content": [{"type": "text", "text": WRITER_DRAFT}]},
        {"role": "user", "content": task},
        {"role": "user", "content": [{"type": "text", "text": f"  {task}"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "c1", "content": "{}"}]},
        # 作者原话里引用了这个标记，但不是以它开头：保留。
        {"role": "user", "content": f"为什么会出现{REVIEW_TASK_MARKER}？"},
        # 不是 user 消息：不过滤。
        {"role": "assistant", "content": f"{REVIEW_TASK_MARKER} 模型自己复述"},
        {"role": "user", "content": f"{HANDOFF_MARKER}: {REVIEW_NOTES}"},
    ]

    kept = _drop_reviewer_task_messages(messages)

    assert kept == [messages[0], messages[1], messages[4], messages[5], messages[6], messages[7]]
    assert _drop_reviewer_task_messages(None) == []
