"""作者看到的回复只有一份总结、字数用编辑器的数（I03 / I05 回归，全部 mock，不调用模型）。

- 被审稿取代的 writer 总结不进作者看到的回复（writing_graph CHAT_HOLD + StreamAdapter）
- IDLE 下独占一行的 <file> / </file> 不进对话（StreamProcessor）
- 自动质检门只看本轮写进 draft / script 的字数（body_writes）
- 送审交接带编辑器字数和作者在本会话 / 项目设定里说过的篇幅要求
- 同一个 agent 隔着工具调用的两段文字之间补空行（AgentTextShaper）

测试文本都是自己构造的，不来自真实作品。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agent.core.events import EventType
from agent.core.workflow_events import CHAT_HOLD_BEGIN, CHAT_HOLD_DISCARD, StreamEvent, StreamEventType
from tests.test_agent.body_write_events import create_file_events, edit_file_events

BODY = "林晚推开铁门，炉火映着她的侧脸。" * 30  # 450 字
WRITER_SUMMARY = "第三章写好了，共 2781 字。\n\n- 开头补了雨夜\n\n接着可以回：\n- 写第四章"
REVIEWER_SUMMARY = "《第三章》写好了，编辑器字数 450。\n\n可以接着写第四章。\n[TASK_COMPLETE]"


def _text(text: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.TEXT, data={"text": text})


def _handoff(target: str, context: str) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.HANDOFF,
        data={
            "target_agent": target,
            "reason": "第三章有一句和第一章重复，改一下",
            "context": context,
            "handoff_packet": {
                "target_agent": target,
                "reason": "第三章有一句和第一章重复，改一下",
                "context": context,
                "completed": [],
                "todo": ["改第三章结尾那句"],
                "evidence": [],
            },
        },
    )


def _writer_writes_chapter(*, summary: str = WRITER_SUMMARY, body: str = BODY):
    events = [*create_file_events("draft-3", "第三章"), _text(f"<file>{body}</file>\n\n"), _text(summary)]
    return events


async def _run_graph(
    fake_agent,
    *,
    generation_mode: str = "",
    session=None,
    project_id="p",
    session_id="s",
    auto_review: str = "true",
    workflow_agents: list[str] | None = None,
    initial_agent: str = "writer",
    routing_metadata: dict | None = None,
    auto_review_threshold: int = 100,
    max_iterations: int = 12,
):
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext

    router = AsyncMock(
        return_value={
            "current_agent": initial_agent,
            "workflow_plan": "standard" if workflow_agents else "quick",
            "workflow_agents": list(workflow_agents or []),
            "routing_metadata": routing_metadata or {},
        }
    )
    ToolContext.set_context(
        session=session, user_id="u", project_id=project_id, session_id=session_id, author_message="写第三章"
    )
    state = {
        "user_message": "写第三章",
        "router_message": "写第三章",
        "messages": [{"role": "user", "content": "写第三章"}],
        "system_prompt": "",
        "generation_mode": generation_mode,
    }
    try:
        with (
            patch.dict("os.environ", {"AGENT_ENABLE_GRAPH_AUTO_REVIEW": auto_review}),
            patch("agent.graph.writing_graph.router_node", router),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
            patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
            patch(
                "agent.graph.writing_graph._auto_finalize_task_board_on_completion",
                AsyncMock(return_value=[]),
            ),
        ):
            return [
                event
                async for event in run_writing_workflow_streaming(
                    state=state, thread_id="t", auto_review_threshold=auto_review_threshold,
                    max_iterations=max_iterations,
                )
            ]
    finally:
        ToolContext.clear_pending_empty_file()


async def _author_visible_text(graph_events: list[StreamEvent]) -> str:
    """把图的事件按生产顺序交给 StreamAdapter，拼出作者看到（也是存进历史）的回复正文。"""
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)

    async def _events():
        for event in graph_events:
            yield event

    chunks = [
        sse.data.get("text", "")
        async for sse in adapter.process_workflow_events(_events())
        if sse.type == EventType.CONTENT
    ]
    return "".join(chunks)


# ------------------------------------------------------------- 一份总结（I03）


PLANNER_SUMMARY = "前五章已经保存。\n\n人物关系已展开。\n\n结尾留下了账目疑点。\n\n可以接着写第六章。"
PLANNED_PROSE = {"agent_type": "planner", "write_content": True, "scope": "前五章大纲和正文"}


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("writer_addition", ["补" * 81, ""], ids=["81-word-fix", "read-only-writer"])
async def test_planned_planner_body_is_reviewed_after_writer_and_has_one_summary(writer_addition):
    """规划师先写五章，计划里的 writer 只补 81 字或核对原稿，仍按完整本轮正文送审。"""
    from agent.stream_adapter import create_stream_adapter

    calls: list[str] = []
    reviewer_summary = "前五章已完成，账目疑点已核对。\n[TASK_COMPLETE]"

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "planner":
            for index in range(1, 6):
                for event in create_file_events(f"draft-{index}", f"第{index}章"):
                    yield event
                yield _text(f"<file>{BODY}</file>\n\n")
            yield _text(PLANNER_SUMMARY)
        elif agent_type == "writer":
            if writer_addition:
                for event in edit_file_events("small-fix", writer_addition, file_id="draft-5"):
                    yield event
            yield _text("五章全部交付，结尾补好了。")
        else:
            yield _text(reviewer_summary)

    events = await _run_graph(
        fake_agent, generation_mode="quality", initial_agent="planner", workflow_agents=["writer"],
        routing_metadata=PLANNED_PROSE, auto_review_threshold=500,
    )
    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)

    async def stream():
        for event in events:
            yield event

    frames = [frame async for frame in adapter.process_workflow_events(stream())]
    visible = "".join(frame.data.get("text", "") for frame in frames if frame.type == EventType.CONTENT)
    assert calls == ["planner", "writer", "quality_reviewer"]
    assert PLANNER_SUMMARY not in visible
    assert "五章全部交付，结尾补好了。" not in visible
    assert reviewer_summary.replace("[TASK_COMPLETE]", "").strip() in visible
    assert adapter._save_file_content.await_count == 5
    assert [call.args for call in adapter._save_file_content.await_args_list] == [
        (f"draft-{index}", BODY) for index in range(1, 6)
    ], "扣留对话不能扣留或改变已经写出的正文"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    ("initial", "plan", "amounts", "skip_hook"),
    [
        ("hook_designer", ["writer"], {"hook_designer": 700}, False),
        ("planner", ["hook_designer", "writer"], {"planner": 700}, False),
        ("planner", ["hook_designer", "writer"], {"planner": 180, "hook_designer": 180, "writer": 180}, False),
        ("planner", ["hook_designer", "writer"], {"planner": 100, "hook_designer": 100}, False),
        ("planner", ["hook_designer", "writer"], {"planner": 700}, True),
    ],
    ids=["hook-body", "full-with-read-only-middle", "combined-threshold", "small-delivery", "planned-jump"],
)
async def test_planned_body_delivery_carries_across_writable_roles(initial, plan, amounts, skip_hook):
    """计量跟随同一计划的真实正文交付，而非特定角色或紧邻 writer 的形状。"""
    calls: list[str] = []
    summaries = {agent: f"{agent} 本程内容已保存。" for agent in ("planner", "hook_designer", "writer")}
    final_summary = "本轮正文已核对。\n[TASK_COMPLETE]"

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "quality_reviewer":
            yield _text(final_summary)
            return
        amount = amounts.get(agent_type, 0)
        if amount:
            for event in edit_file_events(f"{agent_type}-body", "雨" * amount, file_id="draft-current"):
                yield event
        yield _text(summaries[agent_type])
        if skip_hook and agent_type == "planner":
            yield _handoff("writer", "核对已保存正文")

    events = await _run_graph(
        fake_agent, generation_mode="quality", initial_agent=initial, workflow_agents=plan,
        routing_metadata={**PLANNED_PROSE, "agent_type": initial}, auto_review_threshold=500,
    )
    review_expected = sum(amounts.values()) >= 500
    expected = [initial, *(["writer"] if skip_hook else plan)]
    assert calls == [*expected, *(["quality_reviewer"] if review_expected else [])]
    visible = await _author_visible_text(events)
    cumulative = 0
    for agent in expected:
        cumulative += amounts.get(agent, 0)
        if review_expected and cumulative >= 500:
            assert summaries[agent] not in visible
        else:
            assert summaries[agent] in visible, "尚未到门槛的小额交付说明不能被扣掉"
    if review_expected:
        assert final_summary.replace("[TASK_COMPLETE]", "").strip() in visible


@pytest.mark.asyncio
@pytest.mark.unit
async def test_planned_body_delivery_does_not_resume_after_an_unplanned_hop():
    """残留计划仍有 writer，也不能把计划外角色的正文重新纳入本次接续。"""
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type in ("hook_designer", "planner"):
            for event in edit_file_events(f"{agent_type}-body", "雨" * 700, file_id="draft-current"):
                yield event
        yield _text(f"{agent_type} 内容已保存。")
        if agent_type == "hook_designer":
            yield _handoff("planner", "先核对结构")

    events = await _run_graph(
        fake_agent, generation_mode="quality", initial_agent="hook_designer", workflow_agents=["writer"],
        routing_metadata={**PLANNED_PROSE, "agent_type": "hook_designer"}, auto_review_threshold=500,
    )
    assert calls == ["hook_designer", "planner", "writer"]
    assert not any(e.type == StreamEventType.CHAT_HOLD and e.data.get("action") == CHAT_HOLD_DISCARD for e in events)
    visible = await _author_visible_text(events)
    assert "hook_designer 内容已保存。" in visible
    assert "planner 内容已保存。" in visible


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("case", ["outline", "failed", "auto-off", "planning-only", "read-only", "fast", "last-iteration", "review-limit", "unplanned"])
async def test_planned_hook_body_keeps_existing_review_boundaries(case):
    from contextlib import nullcontext

    calls: list[str] = []
    summary = "设计与已有正文已核对。"

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "hook_designer":
            for event in edit_file_events(
                "hook-body", "雨" * 700, file_id="draft-current",
                file_type="outline" if case == "outline" else "draft",
                status="error" if case == "failed" else "success",
            ):
                yield event
            yield _text(summary)
            if case == "unplanned":
                yield _handoff("writer", "核对现稿")
        else:
            yield _text("核对结束。")

    limit = patch("agent.graph.writing_graph.MAX_REVIEW_ROUNDS", 0) if case == "review-limit" else nullcontext()
    with limit:
        events = await _run_graph(
            fake_agent, initial_agent="hook_designer", workflow_agents=[] if case == "unplanned" else ["writer"],
            generation_mode="fast" if case == "fast" else "",
            routing_metadata={**PLANNED_PROSE, "agent_type": "hook_designer", "write_content": case != "planning-only", "read_only": case == "read-only"},
            auto_review="false" if case == "auto-off" else "true", auto_review_threshold=500,
            max_iterations=1 if case == "last-iteration" else 12,
        )
    assert "quality_reviewer" not in calls
    assert not any(e.type == StreamEventType.CHAT_HOLD and e.data.get("action") == CHAT_HOLD_DISCARD for e in events)
    visible = await _author_visible_text(events)
    if case == "fast":
        assert calls == ["writer"]
        assert not any(e.type == StreamEventType.CHAT_HOLD for e in events)
    else:
        assert summary in visible


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("stop", ["clarification", "tool-budget", "failure", "last-iteration"])
async def test_planned_body_middle_stop_releases_partial_explanation(stop):
    calls: list[str] = []
    summary = "已核对部分正文，尚未完成后续工作。"

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "planner":
            for event in edit_file_events("planner-body", "雨" * 700, file_id="draft-current"):
                yield event
            yield _text("正文已保存。")
            return
        yield _text(summary)
        if stop == "clarification":
            yield StreamEvent(type=StreamEventType.WORKFLOW_STOPPED, data={"reason": "clarification_needed", "agent_type": agent_type})
        elif stop == "tool-budget":
            yield StreamEvent(type=StreamEventType.ITERATION_EXHAUSTED, data={"layer": "tool_call", "agent_type": agent_type})
        elif stop == "failure":
            raise RuntimeError("mock middle failure")

    events = await _run_graph(
        fake_agent, generation_mode="quality", initial_agent="planner", workflow_agents=["hook_designer", "writer"],
        routing_metadata=PLANNED_PROSE, auto_review_threshold=500,
        max_iterations=2 if stop == "last-iteration" else 12,
    )
    assert calls == ["planner", "hook_designer"]
    assert summary in await _author_visible_text(events)
    assert not any(e.type == StreamEventType.HANDOFF and e.data.get("target_agent") == "writer" for e in events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_reviewer_rework_does_not_start_planned_body_carry():
    """审稿返工有剩余计划也不能开启新的上游正文累计或重复审查。"""
    calls: list[str] = []
    summary = "指定局部已修好。"

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "quality_reviewer":
            yield _handoff("hook_designer", "只修已指出的问题")
        elif agent_type == "hook_designer":
            for event in edit_file_events("hook-rework", "雨" * 700, file_id="draft-current"):
                yield event
            yield _text(summary)
        else:
            yield _text("核对结束。")

    events = await _run_graph(
        fake_agent, generation_mode="quality", initial_agent="quality_reviewer",
        workflow_agents=["hook_designer", "writer"], routing_metadata=PLANNED_PROSE, auto_review_threshold=500,
    )
    assert calls == ["quality_reviewer", "hook_designer", "writer"]
    assert not any(event.type == StreamEventType.CHAT_HOLD for event in events)
    assert summary in await _author_visible_text(events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_planned_planner_question_is_released_even_when_writer_continues():
    """作者已要正文，既有计划继续 writer，但规划师向作者提出的问题不能随总结丢掉。"""
    question = "第一章已经保存。\n\n需要保留这个悬念吗？"

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        if agent_type == "planner":
            for event in create_file_events("draft-1", "第一章"):
                yield event
            yield _text(f"<file>{BODY * 2}</file>\n\n{question}")
        else:
            yield _text("核对结束。")

    events = await _run_graph(
        fake_agent, generation_mode="quality", initial_agent="planner", workflow_agents=["writer"],
        routing_metadata=PLANNED_PROSE, auto_review_threshold=500,
    )
    assert question in await _author_visible_text(events)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    "writer_addition", ["", "补" * 200, "补" * 450],
    ids=["silent-writer", "below-threshold", "combined-review"],
)
async def test_small_planner_delivery_keeps_summary_and_carries_body_count(writer_addition):
    """规划师仅写 100 字时先保留说明；writer 后续补写仍能累计达到质检门槛。"""
    calls: list[str] = []
    summary = "第一章开头已保存。"
    writer_summary = "第一章已补完，人物关系和账目疑点都已展开。"
    reviewer_summary = "第一章核对结束。\n[TASK_COMPLETE]"

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "planner":
            for event in create_file_events("draft-1", "第一章"):
                yield event
            yield _text(f"<file>{'雨' * 100}</file>\n\n{summary}")
        elif agent_type == "writer" and writer_addition:
            for event in edit_file_events("writer-addition", writer_addition, file_id="draft-1"):
                yield event
            yield _text(writer_summary)
        elif agent_type == "quality_reviewer":
            yield _text(reviewer_summary)

    events = await _run_graph(
        fake_agent, generation_mode="quality", initial_agent="planner", workflow_agents=["writer"],
        routing_metadata=PLANNED_PROSE, auto_review_threshold=500,
    )
    visible = await _author_visible_text(events)
    assert summary in visible
    review_expected = len(writer_addition) == 450
    assert calls == ["planner", "writer", *(["quality_reviewer"] if review_expected else [])]
    if review_expected:
        assert writer_summary not in visible
        assert "第一章核对结束。" in visible
    elif writer_addition:
        assert writer_summary in visible
    assert not any(
        e.type == StreamEventType.CHAT_HOLD and e.data.get("reason") == "superseded_by_planned_writer"
        for e in events
    )


@pytest.mark.asyncio
@pytest.mark.unit
async def test_planned_writer_early_failure_releases_its_partial_delivery_text():
    """上游已到审查门槛，writer 提前出错时，原有错误及已写出的部分交付说明仍可见。"""
    partial_summary = "第一章已保存，后续核对尚未完成。"

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        if agent_type == "planner":
            for event in create_file_events("draft-1", "第一章"):
                yield event
            yield _text(f"<file>{BODY * 2}</file>\n\n第一章已保存。")
        else:
            yield _text(partial_summary)
            raise RuntimeError("mock writer failed")

    events = await _run_graph(
        fake_agent, generation_mode="quality", initial_agent="planner", workflow_agents=["writer"],
        routing_metadata=PLANNED_PROSE, auto_review_threshold=500,
    )
    assert any(e.type == StreamEventType.ERROR for e in events)
    assert partial_summary in await _author_visible_text(events)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("case", ["outline", "failed", "unplanned", "auto-off", "last-iteration", "planning-only", "read-only", "fast"])
async def test_planner_body_carry_requires_successful_planned_prose_handoff(case):
    """大纲、失败写入、未计划交接、关闭质检、无剩余轮次和只做规划不扩展送审范围。"""
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "planner":
            for event in edit_file_events(
                "planner-write", BODY * 2,
                file_type="outline" if case == "outline" else "draft",
                status="error" if case == "failed" else "success",
            ):
                yield event
            yield _text(PLANNER_SUMMARY)
            if case == "unplanned":
                yield _handoff("writer", "核对第一章")
        else:
            yield _text("核对结束。")

    events = await _run_graph(
        fake_agent, initial_agent="planner", workflow_agents=[] if case == "unplanned" else ["writer"],
        generation_mode="fast" if case == "fast" else "",
        routing_metadata={**PLANNED_PROSE, "write_content": case != "planning-only", "read_only": case == "read-only"},
        auto_review="false" if case == "auto-off" else "true", auto_review_threshold=500,
        max_iterations=1 if case == "last-iteration" else 12,
    )
    assert "quality_reviewer" not in calls
    assert not any(e.type == StreamEventType.CHAT_HOLD and e.data.get("action") == CHAT_HOLD_DISCARD for e in events)
    visible = await _author_visible_text(events)
    if case == "fast":
        assert calls == ["writer"], "快速模式沿用直接 writer 的既有路由，不运行规划师"
        assert not any(e.type == StreamEventType.CHAT_HOLD for e in events)
        assert "核对结束。" in visible
    else:
        assert PLANNER_SUMMARY in visible


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("stop", ["clarification", "tool-budget"])
async def test_planned_planner_stop_releases_saved_delivery_without_continuing(stop):
    """已写正文后结构化澄清或工具预算终止仍停在规划师，扣留说明原样释放。"""
    calls: list[str] = []
    summary = "第一章已保存，需要作者确认下一步。"

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        for event in edit_file_events("planner-body", BODY * 2, file_id="draft-1"):
            yield event
        yield _text(summary)
        if stop == "clarification":
            yield StreamEvent(
                type=StreamEventType.WORKFLOW_STOPPED,
                data={"reason": "clarification_needed", "agent_type": "planner", "message": "请选择结尾方向"},
            )
        else:
            yield StreamEvent(
                type=StreamEventType.ITERATION_EXHAUSTED,
                data={"layer": "tool_call", "agent_type": "planner"},
            )

    events = await _run_graph(
        fake_agent, generation_mode="quality", initial_agent="planner", workflow_agents=["writer"],
        routing_metadata=PLANNED_PROSE, auto_review_threshold=500,
    )
    assert calls == ["planner"]
    assert not any(event.type == StreamEventType.HANDOFF for event in events)
    assert [event.data["action"] for event in events if event.type == StreamEventType.CHAT_HOLD] == [CHAT_HOLD_BEGIN]
    assert summary in await _author_visible_text(events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_writer_summary_superseded_by_review_is_not_shown_to_author():
    """自动送审时 writer 的总结（含估算的「2781 字」）不出现，作者只看到审稿人那一份。"""
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "writer":
            for event in _writer_writes_chapter():
                yield event
            return
        yield _text(REVIEWER_SUMMARY)

    events = await _run_graph(fake_agent)

    assert calls == ["writer", "quality_reviewer"]
    kinds = [(e.type, e.data.get("action")) for e in events if e.type in (StreamEventType.CHAT_HOLD, StreamEventType.HANDOFF)]
    assert kinds == [
        (StreamEventType.CHAT_HOLD, CHAT_HOLD_BEGIN),
        (StreamEventType.CHAT_HOLD, CHAT_HOLD_DISCARD),
        (StreamEventType.HANDOFF, None),
    ]

    visible = await _author_visible_text(events)
    assert "2781" not in visible
    assert "第三章写好了，共" not in visible
    assert "编辑器字数 450" in visible
    assert visible.count("写好了") == 1, "作者只看到一份总结"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_writer_summary_is_shown_when_no_review_follows():
    """快速模式不送审：writer 的回复照常流式给作者（不扣）。"""

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        for event in _writer_writes_chapter(summary="第三章写好了。"):
            yield event

    events = await _run_graph(fake_agent, generation_mode="fast")

    assert not any(e.type == StreamEventType.CHAT_HOLD for e in events)
    assert "第三章写好了。" in await _author_visible_text(events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_held_summary_is_released_when_review_does_not_follow():
    """写完正文后先扣住文字，但正文太短不送审：扣着的文字照常放给作者，不丢。"""

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        for event in _writer_writes_chapter(body="短短一段。", summary="补了一句。"):
            yield event

    events = await _run_graph(fake_agent)

    assert [e.data.get("action") for e in events if e.type == StreamEventType.CHAT_HOLD] == [CHAT_HOLD_BEGIN]
    assert "补了一句。" in await _author_visible_text(events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rework_writer_is_told_its_earlier_summary_was_not_shown():
    """审稿人交回返修：writer 被告知送检前的说明作者没看到，修完的回复是唯一一份总结。"""
    from agent.graph.writing_graph import WRITER_REWORK_AFTER_SUPERSEDED_NOTE

    messages: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        messages.append(state["user_message"])
        if agent_type == "writer" and len(messages) == 1:
            for event in _writer_writes_chapter():
                yield event
            return
        if agent_type == "quality_reviewer":
            yield _handoff("writer", "把结尾那句换个写法")
            return
        for event in edit_file_events("fix-1", "她把炉门带上。", file_id="draft-3", title="第三章"):
            yield event
        yield _text("第三章结尾改好了，现在 452 字。")

    events = await _run_graph(fake_agent)

    assert len(messages) == 3
    assert WRITER_REWORK_AFTER_SUPERSEDED_NOTE in messages[2]
    visible = await _author_visible_text(events)
    assert "2781" not in visible
    assert "现在 452 字" in visible


@pytest.mark.asyncio
@pytest.mark.unit
async def test_steering_mid_run_does_not_release_the_held_summary():
    """作者在 writer 写完正文后、总结前发了一条引导：总结仍然被扣住，送审时丢掉，不出现两份总结。"""
    calls: list[str] = []
    reviewer_messages: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "writer":
            yield StreamEvent(type=StreamEventType.MESSAGE_START, data={})
            for event in create_file_events("draft-3", "第三章"):
                yield event
            yield _text(f"<file>{BODY}</file>\n\n我再核对一下字数。")
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={"id": "q1", "name": "query_files", "status": "complete", "input": {"id": "draft-3"}},
            )
            yield StreamEvent(
                type=StreamEventType.TOOL_RESULT,
                data={"tool_use_id": "q1", "name": "query_files", "result": {"status": "success", "data": {}}},
            )
            yield StreamEvent(
                type=StreamEventType.STEERING_RECEIVED,
                data={"message_id": "m1", "preview": "结尾别太煽情"},
            )
            yield _text(WRITER_SUMMARY)
            return
        reviewer_messages.append(state["user_message"])
        yield _text(REVIEWER_SUMMARY)

    events = await _run_graph(fake_agent)

    assert calls == ["writer", "quality_reviewer"]
    visible = await _author_visible_text(events)
    assert "2781" not in visible
    assert "我再核对一下字数" not in visible
    assert visible.count("写好了") == 1, "作者只看到一份总结"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_writer_question_survives_when_its_summary_is_superseded():
    """writer 以问作者的问题收尾、又被自动送审：问题原样交给审稿人和返修 writer，不随总结一起丢掉。"""
    from agent.graph.writing_graph import WRITER_QUESTION_HEADER

    question = "第二章结尾要走悲剧还是圆满？"
    messages: list[tuple[str, str]] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        messages.append((agent_type, state["user_message"]))
        if agent_type == "writer" and len(messages) == 1:
            for event in _writer_writes_chapter(summary=f"第一章写好了，约1200字。\n\n{question}"):
                yield event
            return
        if agent_type == "quality_reviewer":
            yield _handoff("writer", "把结尾那句换个写法")
            return
        for event in edit_file_events("fix-1", "她把炉门带上。", file_id="draft-3", title="第三章"):
            yield event
        yield _text("第一章结尾改好了。")

    events = await _run_graph(fake_agent)

    assert [agent for agent, _ in messages] == ["writer", "quality_reviewer", "writer"]
    reviewer_message = messages[1][1]
    assert f"{WRITER_QUESTION_HEADER}\n{question}" in reviewer_message
    assert "约1200字" not in reviewer_message.split(WRITER_QUESTION_HEADER, 1)[1]
    assert f"{WRITER_QUESTION_HEADER}\n{question}" in messages[2][1]
    assert question not in await _author_visible_text(events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_chat_hold_when_auto_review_is_disabled():
    """自动质检门关掉（非高质量模式）时不扣文字：没有审稿会取代 writer 的回复。"""

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        for event in _writer_writes_chapter(summary="第三章写好了。"):
            yield event

    events = await _run_graph(fake_agent, auto_review="false")

    assert not any(e.type == StreamEventType.CHAT_HOLD for e in events)
    assert "第三章写好了。" in await _author_visible_text(events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_planned_review_holds_summary_even_when_auto_review_is_off():
    """计划里下一个就是审稿人：自动质检门关着、正文只改几个字，也扣住 writer 的说明并在送审时丢掉。"""
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "writer":
            for event in edit_file_events("e1", "老锻", file_id="draft-3", title="第三章"):
                yield event
            yield _text("把名字改好了，共 2781 字。")
            return
        yield _text(REVIEWER_SUMMARY)

    events = await _run_graph(
        fake_agent, auto_review="false", workflow_agents=["writer", "quality_reviewer"]
    )

    assert calls == ["writer", "quality_reviewer"]
    assert [e.data.get("action") for e in events if e.type == StreamEventType.CHAT_HOLD] == [
        CHAT_HOLD_BEGIN,
        CHAT_HOLD_DISCARD,
    ]
    assert "2781" not in await _author_visible_text(events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_review_reads_share_one_budget(monkeypatch):
    """送审前的两次读取共用一份时间预算：一次慢了只丢它自己，不额外再等一份。"""
    import time

    from agent.graph import writing_graph

    monkeypatch.setattr(writing_graph, "REVIEW_AUTO_CHECK_TIMEOUT_SECONDS", 0.2)

    def fast():
        return "counts"

    def slow():
        time.sleep(0.5)
        return "requirements"

    started = time.monotonic()
    results = await writing_graph._bounded_review_reads({"counts": fast, "requirements": slow})
    elapsed = time.monotonic() - started

    assert results == {"counts": "counts", "requirements": None}
    assert elapsed < 0.4


# ------------------------------------------------------------- 独占一行的标签（I03）


@pytest.mark.asyncio
@pytest.mark.unit
async def test_lone_close_tag_after_summary_does_not_reach_chat():
    """U082 的形态：正文收尾后又读了一次文件，总结末尾单独一行「</file>」。"""
    from agent.stream_adapter import create_stream_adapter

    adapter = create_stream_adapter(project_id="p", user_id="u", process_file_markers=True)
    adapter._save_file_content = AsyncMock(return_value=True)
    events = [
        *create_file_events("draft-3", "第三章"),
        _text("<file>正文。</file>\n\n第三章写好了，我再通读一遍。"),
        StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={"id": "q1", "name": "query_files", "status": "complete", "input": {"id": "draft-3"}},
        ),
        _text("第三章按倒叙写完。\n\n接着可以回：\n- 写第四章\n</fi"),
        _text("le>\n\n我先读取第三章当前全文。"),
        StreamEvent(type=StreamEventType.MESSAGE_END, data={}),
    ]

    async def _events():
        for event in events:
            yield event

    chat = "".join(
        [sse.data.get("text", "") async for sse in adapter.process_workflow_events(_events()) if sse.type == EventType.CONTENT]
    )

    assert "</file>" not in chat
    assert "- 写第四章\n\n我先读取第三章当前全文。" in chat


@pytest.mark.unit
@pytest.mark.parametrize(
    "chunks, expected",
    [
        (["示例：\n```\n</file>\n```\n"], "示例：\n```\n</file>\n```\n"),
        (["写成 `</file>` 就行\n"], "写成 `</file>` 就行\n"),
        (["正文要用 <file> 包起来\n"], "正文要用 <file> 包起来\n"),
        (["结尾\n<file>\n第二段\n"], "结尾\n第二段\n"),
    ],
)
def test_lone_tag_rule_keeps_literal_tags(chunks, expected):
    from agent.core.stream_processor import StreamProcessor

    proc = StreamProcessor()
    out = "".join(proc.process_content(chunk).conversation_content for chunk in chunks)
    out += proc.finalize_on_stream_end().conversation_content
    assert out == expected


# ------------------------------------------------------------- 自动质检门（I03）


@pytest.mark.asyncio
@pytest.mark.unit
async def test_long_reply_after_outline_edit_is_not_auto_reviewed():
    """改大纲（outline）时说明写得再长，也不送审。"""
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        for event in edit_file_events("e1", "第三章改成倒叙。", file_id="outline-1", title="卷一大纲", file_type="outline"):
            yield event
        yield _text("大纲改好了。" + "这一卷的节奏调整说明。" * 60)

    events = await _run_graph(fake_agent)

    assert calls == ["writer"]
    assert not any(e.type == StreamEventType.HANDOFF for e in events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_small_draft_fix_with_long_explanation_is_not_auto_reviewed():
    """正文只改了几个字，说明却很长：按写进正文的字数判断，不送审。"""
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        for event in edit_file_events("e1", "老锻", file_id="draft-3", title="第三章"):
            yield event
        yield _text("把「老段」改成了「老锻」。" + "前后文里这个名字的用法说明。" * 60)

    events = await _run_graph(fake_agent)

    assert calls == ["writer"]
    # 只改几个字的 edit_file 到不了送审阈值：说明照常流式显示，不扣。
    assert not any(e.type == StreamEventType.CHAT_HOLD for e in events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_short_chat_after_long_streamed_body_is_auto_reviewed():
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "writer":
            for event in _writer_writes_chapter(summary="写好了。"):
                yield event
            return
        yield _text(REVIEWER_SUMMARY)

    await _run_graph(fake_agent)

    assert calls == ["writer", "quality_reviewer"]


# ------------------------------------------------------------- 字数与篇幅要求（I05）


@pytest.fixture
def project_with_history(db_session):
    from models import ChatMessage, ChatSession, File, Project, User

    user = User(
        email="reply_shaping@example.com",
        username="replyshaping",
        hashed_password="x",
        name="Reply Shaping",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(
        name="Reply Shaping",
        owner_id=user.id,
        project_type="novel",
        writing_style="第一人称，每章约2500字左右",
    )
    db_session.add(project)
    db_session.commit()
    chat = ChatSession(user_id=user.id, project_id=project.id)
    db_session.add(chat)
    db_session.commit()
    for content in ("写一部都市小说，单章约 2000 字。", "好的"):
        db_session.add(ChatMessage(session_id=chat.id, role="user", content=content))
    db_session.commit()
    chapter = File(project_id=project.id, title="第三章", content=BODY, file_type="draft")
    db_session.add(chapter)
    db_session.commit()
    db_session.refresh(chapter)
    return {"project": project, "chat": chat, "chapter": chapter}


@pytest.mark.asyncio
@pytest.mark.unit
async def test_review_handoff_carries_editor_count_and_earlier_length_requirements(db_session, project_with_history):
    """writer 交接写「约2800字」，审稿人拿到编辑器字数，以及本会话早先和项目设定里的篇幅要求。"""
    from agent.graph.writing_graph import BODY_WORD_COUNT_HEADER, LENGTH_REQUIREMENT_HEADER
    from utils.text_metrics import count_words

    chapter = project_with_history["chapter"]
    reviewer_messages: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        if agent_type == "writer":
            state["repeat_read_guard"]._record_write(chapter.id, "第三章")
            for event in edit_file_events("e1", BODY, file_id=chapter.id, title="第三章"):
                yield event
            yield _text("第三章写好了，约2800字，我再通读核对一遍。")
            yield _handoff("quality_reviewer", "第三章约2800字，请检查")
            return
        reviewer_messages.append(state["user_message"])
        yield _text(REVIEWER_SUMMARY)

    events = await _run_graph(
        fake_agent,
        session=db_session,
        project_id=project_with_history["project"].id,
        session_id=project_with_history["chat"].id,
    )

    assert len(reviewer_messages) == 1
    message = reviewer_messages[0]
    counts = message.split(BODY_WORD_COUNT_HEADER + "\n", 1)[1]
    assert counts.startswith(f"- 《第三章》{count_words(BODY)} 字\n")
    requirements = message.split(LENGTH_REQUIREMENT_HEADER + "\n", 1)[1]
    assert "「写一部都市小说，单章约 2000 字」（作者在本会话说过）" in requirements
    assert "「第一人称，每章约2500字左右」（项目写作风格）" in requirements
    packet = next(e for e in events if e.type == StreamEventType.HANDOFF).data["handoff_packet"]
    assert f"《第三章》编辑器字数 {count_words(BODY)}" in packet["evidence"]


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("writer_chat", ["创作输出声称第三章已经全部核对。", ""], ids=["writer-chat", "no-chat"])
async def test_review_read_failure_requires_current_file_read_before_judgment(writer_chat):
    """落库读取返回 None 时，原输出不能冒充当前正文；无聊天也必须传精确读取任务。"""
    reviewer_messages: list[str] = []
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "writer":
            state["repeat_read_guard"]._record_write("draft-current", "第三章")
            for event in edit_file_events("write-current", BODY * 2, file_id="draft-current"):
                yield event
            if writer_chat:
                yield _text(writer_chat)
        else:
            reviewer_messages.append(state["user_message"])
            yield _text("需要先读取第三章。")

    with patch("agent.graph.writing_graph._review_payload_text", AsyncMock(return_value=None)) as read:
        await _run_graph(fake_agent, generation_mode="quality", auto_review_threshold=500)

    assert calls == ["writer", "quality_reviewer"]
    read.assert_awaited_once_with(["draft-current"])
    assert len(reviewer_messages) == 1
    task = reviewer_messages[0]
    assert "当前落库正文未读取" in task
    assert "创作输出片段，不代表当前落库正文" in task
    assert "query_files" in task and "draft-current" in task
    assert "读取当前正文后再判断事实或总结" in task
    if writer_chat:
        assert writer_chat in task


@pytest.mark.asyncio
@pytest.mark.unit
async def test_review_receives_persisted_planner_body_when_writer_has_no_chat(db_session, project_with_history):
    """writer 只核对文件没有聊天时，送审仍携带上游落库的当前正文和字数。"""
    from agent.graph.writing_graph import BODY_WORD_COUNT_HEADER
    from utils.text_metrics import count_words

    chapter = project_with_history["chapter"]
    chapter.content = BODY * 2
    db_session.add(chapter)
    db_session.commit()
    reviewer_messages: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        if agent_type == "planner":
            state["repeat_read_guard"]._record_write(chapter.id, chapter.title)
            for event in edit_file_events("planner-body", BODY * 2, file_id=chapter.id, title=chapter.title):
                yield event
            yield _text("第三章已保存。")
        elif agent_type == "quality_reviewer":
            reviewer_messages.append(state["user_message"])
            yield _text(REVIEWER_SUMMARY)

    await _run_graph(
        fake_agent, generation_mode="quality", initial_agent="planner", workflow_agents=["writer"],
        routing_metadata=PLANNED_PROSE, auto_review_threshold=500, session=db_session,
        project_id=project_with_history["project"].id, session_id=project_with_history["chat"].id,
    )
    assert len(reviewer_messages) == 1
    assert "[待审查内容]\n" in reviewer_messages[0]
    assert BODY * 2 in reviewer_messages[0]
    assert BODY_WORD_COUNT_HEADER in reviewer_messages[0]
    assert f"《第三章》{count_words(BODY * 2)} 字" in reviewer_messages[0]


@pytest.mark.unit
def test_find_length_requirements_quotes_only_length_clauses():
    from agent.graph.review_evidence import find_length_requirements

    found = find_length_requirements(
        [
            ("项目写作风格", "节奏快。每集约500-900字，台词短"),
            ("作者在本会话说过", "主角叫陈默。他写了三千字的检讨"),
            ("作者在本会话说过", "第一章字数太少了，没有2500字"),
            ("作者本轮原话", "还得改，每章字数要求1000字以上"),
            ("作者本轮原话", "还得改，每章字数要求1000字以上"),
        ]
    )

    assert found == [
        "「每集约500-900字，台词短」（项目写作风格）",
        "「第一章字数太少了，没有2500字」（作者在本会话说过）",
        "「还得改，每章字数要求1000字以上」（作者本轮原话）",
    ]


# ------------------------------------------------------------- 段落分隔（I03）


@pytest.mark.unit
def test_segments_of_one_agent_separated_by_tool_calls_are_not_glued():
    from agent.core.author_facing_text import AgentTextShaper

    shaper = AgentTextShaper(previous_text="", drop_english_narration=True)
    out = shaper.feed("正在读取第三章。")
    out += shaper.end_segment(before_tool_call=True)
    out += shaper.feed("正在核对细纲。")
    out += shaper.end_segment(before_tool_call=True)
    out += shaper.feed("<file>正文</file>")
    out += shaper.end_segment(before_tool_call=False)

    assert out == "正在读取第三章。\n\n正在核对细纲。<file>正文</file>"


@pytest.mark.unit
def test_body_write_tracker_counts_only_successful_body_writes():
    import json

    from agent.graph.body_writes import BodyWriteTracker, body_words_written

    tracker = BodyWriteTracker()
    text = ""
    parallel_input = {
        "tasks": [
            {"type": "write_chapter", "params": {"title": "第四章", "content": "雨停了。" * 10, "parent_id": "d"}},
            {"type": "edit_file", "params": {"id": "lore-1", "edits": [{"op": "append", "text": "设定" * 50}]}},
            {"type": "write_chapter", "params": {"title": "第五章", "content": "失败的章节" * 20, "parent_id": "d"}},
        ]
    }
    parallel_result = {
        "status": "success",
        "data": {
            "tasks": [
                {"type": "write_chapter", "status": "completed", "result": {"data": {"id": "c4", "file_type": "draft"}}},
                {"type": "edit_file", "status": "completed", "result": {"data": {"id": "lore-1", "file_type": "lore"}}},
                {"type": "write_chapter", "status": "failed", "result": None},
            ]
        },
    }
    events = [
        *create_file_events("draft-3", "第三章"),
        StreamEvent(type=StreamEventType.TOOL_USE, data={"id": "p1", "name": "parallel_execute", "status": "complete", "input": parallel_input}),
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={"tool_use_id": "p1", "name": "parallel_execute", "result": {"content": [{"type": "text", "text": json.dumps(parallel_result, ensure_ascii=False)}]}},
        ),
        *edit_file_events("e-bad", "写不进去" * 50, file_id="draft-3", status="error"),
    ]
    for event in events[:2]:
        tracker.observe(event, text_length=len(text))
    text += "<file>" + "她笑了。" * 5 + "</file>\n\n写好了。"
    for event in events[2:]:
        tracker.observe(event, text_length=len(text))

    writes = tracker.finish(text)

    assert [(w.file_id, w.words, w.full_body) for w in writes] == [("draft-3", 15, True), ("c4", 30, True)]
    assert body_words_written(writes) == 45


def test_review_count_fallback_uses_counts_after_later_edits():
    """本地实测（N）：落库读取超时后，审稿人拿到的是建文件时的字数，看不到之后扩写的部分。"""
    import json

    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.graph.body_writes import BodyWriteTracker, latest_file_word_counts
    from tests.test_agent.body_write_events import create_file_events

    def edit(call_id: str, file_id: str, title: str, new_word_count: int | None) -> list[StreamEvent]:
        data = {"id": file_id, "title": title, "file_type": "draft"}
        if new_word_count is not None:
            data["new_word_count"] = new_word_count
        return [
            StreamEvent(type=StreamEventType.TOOL_USE, data={
                "id": call_id, "name": "edit_file", "status": "complete",
                "input": {"id": file_id, "edits": [{"op": "insert_after", "anchor": "x", "text": "补一段"}]},
            }),
            StreamEvent(type=StreamEventType.TOOL_RESULT, data={
                "tool_use_id": call_id, "name": "edit_file",
                "result": {"content": [{"type": "text", "text": json.dumps(
                    {"status": "success", "data": data}, ensure_ascii=False)}]},
            }),
        ]

    tracker = BodyWriteTracker()
    text = ""
    for event in create_file_events("ch1", "第一章 退潮"):
        tracker.observe(event, text_length=len(text))
    text += "<file>" + "潮" * 1093 + "</file>"
    for event in create_file_events("ch2", "第二章 旧锚"):
        tracker.observe(event, text_length=len(text))
    text += "<file>" + "锚" * 976 + "</file>"
    for event in [*edit("e1", "ch1", "第一章 退潮", 1189), *edit("e2", "ch2", "第二章 旧锚", None)]:
        tracker.observe(event, text_length=len(text))
    writes = tracker.finish(text)

    # 第二章那次扩写没带回字数：不列，也不拿建文件时的 976 顶替。
    assert latest_file_word_counts(writes) == [("第一章 退潮", 1189)]
