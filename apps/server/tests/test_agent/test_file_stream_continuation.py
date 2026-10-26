"""写完 </file> 就结束 run 时同一个 agent 接着写（I08）。

流式写文件协议把正文放在回复文字里；openai-agents 把不带工具调用的回复当作最终输出，模型写完
一个文件没在同一次回复里接着调用 create_file 时，run 就停在 </file>：计划里没写的文件、给作者的
总结都没写，作者看到的回复是空的或只剩开场句。fake agent 按 runner 的事件形状模拟。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.completion_evidence import ends_on_file_block, ends_on_unclosed_file_block
from agent.openai_agents.repeat_read_guard import ReadPlan
from agent.tools.mcp_tools import ToolContext

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _text(text: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.TEXT, data={"text": text})


def _tool(call_id: str, name: str) -> list[StreamEvent]:
    payload = {"status": "success"}
    return [
        StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={"id": call_id, "name": name, "status": "complete", "input": {}},
        ),
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": call_id,
                "name": name,
                "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
            },
        ),
    ]


def _create(
    state,
    call_id: str,
    file_id: str,
    title: str,
    *,
    file_type: str | None = None,
    reused_existing: bool = False,
) -> list[StreamEvent]:
    """一次成功的 create_file：台账记账（runner 做的事）+ 工具事件。"""
    tool_input = {"title": title}
    data: dict[str, object] = {"id": file_id, "title": title}
    if file_type is not None:
        tool_input["file_type"] = file_type
        data["file_type"] = file_type
    if reused_existing:
        data.update({"reused_existing": True, "original_content_length": 100})
    payload = {"status": "success", "data": data}
    state["repeat_read_guard"].observe(
        "create_file",
        ReadPlan(arguments=json.dumps(tool_input)),
        json.dumps(payload),
    )
    return [
        StreamEvent(
            type=StreamEventType.TOOL_USE,
            data={
                "id": call_id,
                "name": "create_file",
                "status": "complete",
                "input": tool_input,
            },
        ),
        StreamEvent(
            type=StreamEventType.TOOL_RESULT,
            data={
                "tool_use_id": call_id,
                "name": "create_file",
                "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
            },
        ),
    ]


def _file(body: str) -> StreamEvent:
    return _text(f"<file>\n{body}\n</file>\n")


def _unclosed_file(body: str) -> StreamEvent:
    return _text(f"<file>\n{body}\n")


async def _run(
    fake_agent,
    *,
    message: str = "把后面几集都写了",
    language: str | None = None,
    initial: str = "writer",
    workflow_agents: list[str] | None = None,
    routing_metadata: dict | None = None,
    max_iterations: int = 12,
    tasks: list[dict] | None = None,
) -> list[StreamEvent]:
    from agent.graph.writing_graph import run_writing_workflow_streaming

    router_result = {
        "current_agent": initial,
        "workflow_plan": "standard" if workflow_agents else "quick",
        "workflow_agents": list(workflow_agents or []),
        "routing_metadata": routing_metadata or {"agent_type": initial},
    }
    state = {
        "user_message": message,
        "messages": [],
        "system_prompt": "",
        "language": language,
    }
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch("agent.graph.writing_graph.router_node", AsyncMock(return_value=router_result)),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
            patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
            patch(
                "services.infra.task_board_service.task_board_service.get_tasks",
                return_value=list(tasks or []),
            ),
            patch(
                "agent.graph.writing_graph._auto_finalize_task_board_on_completion",
                AsyncMock(return_value=[]),
            ),
        ):
            return [
                event
                async for event in run_writing_workflow_streaming(
                    state=state,
                    thread_id="t",
                    max_iterations=max_iterations,
                    auto_review_threshold=10**9,
                )
            ]
    finally:
        ToolContext.clear_context()


def _visible_text(events: list[StreamEvent]) -> str:
    return "".join(
        str(event.data.get("text") or "") for event in events if event.type == StreamEventType.TEXT
    )


# ---------------------------------------------------------------- I08: 续写


async def test_writer_that_stops_at_file_close_continues_and_summarizes():
    """U076 形状：要写完剩下几集，只写了第2集就停在 </file>；同一轮里接着写完并给总结。"""
    runs: list[tuple[str, str]] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        runs.append((agent_type, str(state.get("user_message") or "")))
        if len(runs) == 1:
            yield _text("我先写第2集。")
            for event in _create(state, "c1", "f2", "第2集 暗流"):
                yield event
            yield _file("第二集正文。" * 20)
        elif len(runs) == 2:
            for event in _create(state, "c2", "f3", "第3集 摊牌"):
                yield event
            yield _file("第三集正文。" * 20)
            yield _text("第2集、第3集都写好了，各约 120 字。")

    events = await _run(
        fake_agent,
        tasks=[{"task": "撰写第2-3集剧本", "status": "in_progress"}],
        routing_metadata={"agent_type": "writer", "scope": "第2-3集"},
    )

    assert [agent for agent, _ in runs] == ["writer", "writer"]
    continuation = runs[1][1]
    assert "上一次回复在文件正文结束时就停止了" in continuation
    assert "《第2集 暗流》" in continuation
    assert "撰写第2-3集剧本（进行中）" in continuation
    assert "第2-3集" in continuation
    # 作者最后看到的是总结，不是空回复或开场句。
    assert _visible_text(events).rstrip().endswith("各约 120 字。")
    assert not any(event.type == StreamEventType.ITERATION_EXHAUSTED for event in events)


async def test_english_writer_continuation_keeps_locale_and_delivery_context():
    """英文作者的同 agent 续跑使用英文说明，同时保留已存文件、任务进度和原始范围。"""
    runs: list[tuple[str, str]] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        runs.append((agent_type, str(state.get("user_message") or "")))
        if len(runs) == 1:
            for event in _create(state, "c1", "f1", "Chapter 1 — Rain"):
                yield event
            yield _file("The rain started before dawn. " * 20)
        else:
            yield _text("Chapter 1 is saved; chapter 2 remains incomplete.")

    scope = "chapters 1–2, about 800 words each"
    task = "Write chapters 1–2"
    progress = "Chapter 1 saved"
    events = await _run(
        fake_agent,
        message="Write the first two chapters in English.",
        language="en-US",
        tasks=[{"task": task, "status": "in_progress", "progress": progress}],
        routing_metadata={"agent_type": "writer", "scope": scope},
    )

    assert [agent for agent, _ in runs] == ["writer", "writer"]
    continuation = runs[1][1]
    assert "[系统提醒] 你上一次回复在文件正文结束时就停止了:" in continuation
    assert "Chapter 1 — Rain" in continuation
    assert task in continuation
    assert progress in continuation
    assert scope in continuation
    assert "Tasks still open on the task board" in continuation
    assert "任务计划板上还没完成的任务" not in continuation
    assert "作者这一轮要求的交付范围" not in continuation
    assert _visible_text(events).rstrip().endswith("chapter 2 remains incomplete.")


async def test_writer_with_new_body_at_unclosed_eof_continues_and_summarizes():
    """EOF 抢救已保存的新剧本仍须续跑同一 writer，给作者可见总结。"""
    runs: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        runs.append(agent_type)
        if len(runs) == 1:
            yield _text("正在新建第1集剧本文件。")
            for event in _create(
                state,
                "c1",
                "f1",
                "第1集 深夜单",
                file_type="script",
            ):
                yield event
            yield _unclosed_file("第一集剧本正文。" * 20)
        else:
            yield _text("第1集剧本已经写入，共约 160 字。")

    events = await _run(fake_agent)

    assert runs == ["writer", "writer"]
    assert _visible_text(events).rstrip().endswith("第1集剧本已经写入，共约 160 字。")


async def test_unclosed_reused_body_does_not_continue_after_overwrite_refusal():
    """已有非空正文的截断覆盖会被适配器拒绝，不能据此补跑总结。"""
    runs: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        runs.append(agent_type)
        for event in _create(
            state,
            "c1",
            "f1",
            "第1集 深夜单",
            file_type="script",
            reused_existing=True,
        ):
            yield event
        yield _unclosed_file("截断的替换稿。")

    await _run(fake_agent)

    assert runs == ["writer"]


async def test_carried_body_write_does_not_justify_later_unclosed_continuation():
    """上一轮正文记录不能替代当前 run 的 EOF 新正文保存证据。"""
    runs: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        runs.append(agent_type)
        if len(runs) == 1:
            for event in _create(
                state,
                "c1",
                "f1",
                "第1集 深夜单",
                file_type="script",
            ):
                yield event
            yield _file("第一集剧本正文。" * 20)
        else:
            for event in _create(
                state,
                "c2",
                "outline-2",
                "分集大纲",
                file_type="outline",
            ):
                yield event
            yield _unclosed_file("大纲正文。" * 20)

    await _run(fake_agent)

    assert runs == ["writer", "writer"]


async def test_planner_finishes_its_plan_before_the_planned_writer_runs():
    """U088 形状：规划师计划建卷二到卷五，只建了卷二就停在 </file>；先续写，再交给 writer。"""
    runs: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        runs.append(agent_type)
        if agent_type == "planner" and runs.count("planner") == 1:
            for event in _create(state, "p1", "v2", "卷二大纲"):
                yield event
            yield _file("卷二规划。" * 20)
        elif agent_type == "planner":
            for event in _create(state, "p2", "v3", "卷三大纲"):
                yield event
            yield _file("卷三规划。" * 20)
            yield _text("卷二、卷三大纲已写好。")
        else:
            yield _text("第一章写好了。")

    await _run(fake_agent, initial="planner", workflow_agents=["writer"])

    assert runs == ["planner", "planner", "writer"]


async def test_reply_with_summary_after_file_close_is_not_continued():
    runs: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        runs.append(agent_type)
        for event in _create(state, "c1", "f2", "第2集"):
            yield event
        yield _file("第二集正文。" * 20)
        yield _text("第2集写好了，约 120 字。")

    await _run(fake_agent)

    assert runs == ["writer"]


async def test_last_iteration_does_not_schedule_a_continuation():
    runs: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        runs.append(agent_type)
        for event in _create(state, "c1", "f2", "第2集"):
            yield event
        yield _file("第二集正文。" * 20)

    events = await _run(fake_agent, max_iterations=1)

    assert runs == ["writer"]
    assert any(event.type == StreamEventType.ITERATION_EXHAUSTED for event in events)


async def test_file_block_without_a_create_file_in_the_run_is_not_continued():
    """没有 create_file 的 <file> 块（U074）不归续写管：这一轮没有新建文件可接。"""
    runs: list[str] = []

    async def fake_agent(_state, agent_type, *_args, **_kwargs):
        runs.append(agent_type)
        yield _file("短剧改编方案。" * 20)

    await _run(fake_agent)

    assert runs == ["writer"]


async def test_rework_writer_that_stops_at_file_close_continues():
    """U076 06:20 形状：审稿人交回返修后 writer 写了两集就停在 </file>（不再自动送审）。"""
    runs: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        runs.append(agent_type)
        if agent_type == "quality_reviewer":
            yield _text("第2集结尾要补。")
            yield StreamEvent(
                type=StreamEventType.HANDOFF,
                data={"target_agent": "writer", "reason": "返修", "context": "补第2集结尾，再写第3集"},
            )
            return
        writer_runs = runs.count("writer")
        if writer_runs == 1:
            yield _text("第2集写好了。")
            yield StreamEvent(
                type=StreamEventType.HANDOFF,
                data={"target_agent": "quality_reviewer", "reason": "请审稿", "context": "请审稿"},
            )
        elif writer_runs == 2:
            for event in _create(state, "c3", "f3", "第3集"):
                yield event
            yield _file("第三集正文。" * 20)
        else:
            yield _text("第3集写好了，第2集结尾也补上了。")

    await _run(fake_agent)

    assert runs == ["writer", "quality_reviewer", "writer", "writer"]


# ------------------------------------------------------------ 文字信号


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        ("<file>\n正文\n</file>\n", True),
        ("<file>\n正文\n</file>\n[TASK_COMPLETE]", True),
        ("<file>\n正文\n</file>\n<file>\n</file>\n", True),
        ("<file>\n正文\n</file>\n写好了第2集。", False),
        ("<file>\n\n</file>\n", False),
        ("只是一句话", False),
        ("", False),
    ],
)
async def test_ends_on_file_block(tail, expected):
    assert ends_on_file_block(tail) is expected


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        ("<file>\n正文\n", True),
        ("<file>\n\n", False),
        ("<file>\n正文\n</file>\n", False),
        ("只是一句话", False),
        ("", False),
    ],
)
async def test_ends_on_unclosed_file_block(tail, expected):
    assert ends_on_unclosed_file_block(tail) is expected
