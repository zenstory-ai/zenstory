"""跨轮路由延续、审稿闸门与交接细节的回归测试（全部 mock，不调用 DeepSeek）。

- 路由结果随 assistant 消息落库，下一轮「继续」/回答提问时读回（router / session_loader / service）
- 快速模式不送审、返工稿不自动送审（writing_graph / nodes）
- 工具调用轮数耗尽时回滚空文件、交接包 todo 渲染给下一个 agent（writing_graph）
- 守卫改写过的 parallel_execute 面包屑按类型配对（session_loader）
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session, desc, select

from agent.core.workflow_events import StreamEvent, StreamEventType
from tests.test_agent.body_write_events import edit_file_events
from tests.test_agent.file_body_resume_helpers import accept_resumes, resumable_target

LONG_TEXT = "第五章的正文。" * 200


def _text(text: str) -> StreamEvent:
    return StreamEvent(type=StreamEventType.TEXT, data={"text": text})


def _write_done(call_id: str) -> list[StreamEvent]:
    # 一次写进正文（draft）的成功 edit_file：自动质检门只看正文类文件的写入字数。
    return edit_file_events(call_id, "林晚推开门，雨还在下。" * 30)


def _handoff(target: str, context: str, todo: list[str] | None = None) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.HANDOFF,
        data={
            "target_agent": target,
            "reason": "交接",
            "context": context,
            "handoff_packet": {
                "target_agent": target,
                "reason": "交接",
                "context": context,
                "completed": [],
                "todo": todo or [],
                "evidence": ["第3段：“他慢慢地走了过去”"],
            },
        },
    )


def _state(user_message: str, previous: dict | None = None, **extra):
    messages: list[dict] = [{"role": "user", "content": "上一轮的请求"}]
    if previous is not None:
        messages.append({"role": "assistant", **previous})
    messages.append({"role": "user", "content": user_message})
    state = {
        "user_message": user_message,
        "router_message": user_message,
        "messages": messages,
        "system_prompt": "",
    }
    state.update(extra)
    return state


async def _run_graph(fake_agent, state, *, router=None, auto_review_threshold=100):
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext

    router = router or AsyncMock(
        return_value={"current_agent": "writer", "workflow_plan": "quick", "workflow_agents": []}
    )
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
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
                async for event in accept_resumes(
                    run_writing_workflow_streaming(
                        state=state, thread_id="t", auto_review_threshold=auto_review_threshold
                    )
                )
            ]
    finally:
        ToolContext.clear_context()


# ---------------------------------------------------------- 审稿闸门（F9）


@pytest.mark.asyncio
@pytest.mark.unit
async def test_fast_mode_drops_writer_handoff_to_reviewer():
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        for event in _write_done("w1"):
            yield event
        yield _text(LONG_TEXT)
        for event in _handoff_call("quality_reviewer", "请审查第五章\n[要告诉作者]\n无"):
            yield event

    events = await _run_graph(fake_agent, _state("写第五章", generation_mode="fast"))

    # The writer already wrote its summary: no rerun, nothing appended.
    assert calls == ["writer"]
    assert not any(e.type == StreamEventType.HANDOFF for e in events)
    assert not any(e.type == StreamEventType.TEXT and "无" == e.data.get("text", "").strip() for e in events)


def _handoff_call(target: str, context: str) -> list[StreamEvent]:
    """A real handoff: the handoff_to_agent tool call, then the graph's HANDOFF event."""
    return [
        StreamEvent(type=StreamEventType.TOOL_USE, data={"name": "handoff_to_agent", "id": "h1", "status": "start"}),
        _handoff(target, context),
    ]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_fast_mode_progress_only_handoff_gets_a_summary_with_the_author_notes():
    calls: list[tuple[str, str]] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append((agent_type, str(state.get("user_message") or "")))
        if len(calls) == 1:
            for event in _write_done("w1"):
                yield event
            yield _text("第五章扩写好了，我再通读核对一遍。")
            for event in _handoff_call(
                "quality_reviewer", "请审查第五章\n[要告诉作者]\n旧稿可以在历史版本里找回\n[审查重点]\n节奏"
            ):
                yield event
        else:
            yield _text("第五章写好了，2100 字。")

    events = await _run_graph(fake_agent, _state("把第五章扩写一下", generation_mode="fast"))

    assert [agent for agent, _ in calls] == ["writer", "writer"]
    summary_context = calls[1][1]
    assert "快速模式" in summary_context
    assert "旧稿可以在历史版本里找回" in summary_context
    assert "节奏" not in summary_context  # only the author-facing part is relayed
    assert not any(e.type == StreamEventType.HANDOFF for e in events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_fast_mode_dropped_handoff_after_a_summary_still_relays_author_notes():
    async def fake_agent(state, agent_type, *_args, **_kwargs):
        for event in _write_done("w1"):
            yield event
        yield _text(LONG_TEXT)
        for event in _handoff_call("quality_reviewer", "请审查\n[要告诉作者]\n旧稿可以在历史版本里找回"):
            yield event

    events = await _run_graph(fake_agent, _state("重写第五章", generation_mode="fast"))

    texts = "".join(e.data.get("text", "") for e in events if e.type == StreamEventType.TEXT)
    assert texts.endswith("旧稿可以在历史版本里找回")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_fast_mode_tells_non_reviewer_agents_not_to_hand_off_for_review():
    from agent.graph.nodes import FAST_MODE_DIRECTIVE, FAST_MODE_NON_WRITER_DIRECTIVE, run_streaming_agent

    prompts: dict[str, str] = {}

    async def fake_runner(*, state, agent_type, system_prompt, get_steering_messages=None, agent_directive=""):
        prompts[agent_type] = agent_directive
        yield _text("ok")

    with patch("agent.graph.nodes.run_openai_agents_streaming_agent", new=fake_runner):
        for agent_type in ("writer", "quality_reviewer", "planner"):
            async for _ in run_streaming_agent({"generation_mode": "fast"}, agent_type):
                pass

    assert FAST_MODE_DIRECTIVE in prompts["writer"]
    assert "快速模式" not in prompts["quality_reviewer"]
    # The planner still hands the prose to the writer; it is only told not to send it for review.
    assert FAST_MODE_DIRECTIVE not in prompts["planner"]
    assert FAST_MODE_NON_WRITER_DIRECTIVE in prompts["planner"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rework_after_review_is_not_auto_reviewed_again():
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "writer":
            for event in _write_done(f"w{len(calls)}"):
                yield event
            yield _text(LONG_TEXT)
            return
        yield _text("第三段节奏拖沓。")
        yield _handoff("writer", "第三段节奏拖沓，需要压缩")

    events = await _run_graph(fake_agent, _state("写第五章", generation_mode="quality"))

    assert calls == ["writer", "quality_reviewer", "writer"], "首稿自动送审，返工稿不再送审"
    handoff_targets = [e.data["target_agent"] for e in events if e.type == StreamEventType.HANDOFF]
    assert handoff_targets == ["quality_reviewer", "writer"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_rework_stays_unreviewed_across_empty_file_correction_round():
    """返工 writer 留下空文件时，纠偏轮之后也不能把返工稿再送审。"""
    from agent.tools.mcp_tools import ToolContext

    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        if agent_type == "quality_reviewer":
            yield _text("第三段节奏拖沓。")
            yield _handoff("writer", "第三段节奏拖沓，需要压缩")
            return
        for event in _write_done(f"w{len(calls)}"):
            yield event
        if len(calls) == 3:
            # 返工这一轮建了空文件就收尾，触发纠偏轮
            ToolContext.set_pending_empty_file("f9", "第五章（修订）")
        else:
            ToolContext.clear_pending_empty_file()
        yield _text(LONG_TEXT)

    with (
        patch("agent.graph.writing_graph._probe_pending_file_body", return_value="empty"),
        resumable_target(),
    ):
        await _run_graph(fake_agent, _state("写第五章", generation_mode="quality"))

    assert calls == ["writer", "quality_reviewer", "writer", "writer"]


# ------------------------------------------------- 交接包 todo / evidence（L5）


@pytest.mark.asyncio
@pytest.mark.unit
async def test_reviewer_todo_and_evidence_reach_the_next_agent():
    seen: dict[str, str] = {}

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        seen[agent_type] = state["user_message"]
        if agent_type == "quality_reviewer":
            yield _text("问题如下。")
            yield _handoff("writer", "按问题清单修改", todo=["把第3段的“慢慢地”删掉", "第7段补一句动机"])
            return
        for event in _write_done("w-edit"):
            yield event
        yield _text("已修改。")

    await _run_graph(
        fake_agent,
        _state("检查第三章", generation_mode="quality"),
        router=AsyncMock(
            return_value={
                "current_agent": "quality_reviewer",
                "workflow_plan": "review_only",
                "workflow_agents": [],
            }
        ),
    )

    writer_message = seen["writer"]
    assert "- 把第3段的“慢慢地”删掉" in writer_message
    assert "- 第7段补一句动机" in writer_message
    assert "第3段：“他慢慢地走了过去”" in writer_message


@pytest.mark.unit
def test_internal_evidence_markers_are_not_rendered():
    from agent.graph.writing_graph import _format_handoff_packet_items

    rendered = _format_handoff_packet_items(
        {"todo": [], "evidence": ["workflow_plan=standard", "auto_checks=repeat:2,facts:1"]}
    )
    assert rendered == ""


# ------------------------------------------- 完成标记的中文别名


@pytest.mark.unit
@pytest.mark.parametrize(
    ("content", "visible"),
    [
        ("审查通过，可以发布。\n[任务完成]", "审查通过，可以发布。"),
        ("审查通过。【任务完成】", "审查通过。"),
        ("审查通过。[TASK_COMPLETE]\n", "审查通过。"),
    ],
)
def test_chinese_task_complete_marker_counts_as_completion(content, visible):
    from agent.graph.completion_evidence import author_reply_tail
    from agent.graph.nodes import detect_task_complete

    result = detect_task_complete(content, "quality_reviewer")
    assert result.is_complete is True
    assert result.reason == "explicit_complete_marker"
    assert author_reply_tail(content) == visible


@pytest.mark.unit
def test_chinese_marker_mid_text_is_not_completion():
    from agent.graph.nodes import detect_task_complete

    assert detect_task_complete("上一轮写着[任务完成]，这一轮还要继续改第二段。", "writer").is_complete is False


@pytest.mark.unit
def test_question_prose_and_completion_marker_are_preserved_independently():
    from agent.graph.completion_evidence import author_reply_tail

    assert author_reply_tail("第一章写好了。需要我继续写第二章吗？\n[任务完成]") == (
        "第一章写好了。需要我继续写第二章吗？"
    )


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(("planned", "expected_calls"), [
    # The author asked for prose too (writer kept in the trimmed plan): a stage's marker
    # only ends that stage (#269).
    (["writer"], ["planner", "writer"]),
    # Outline only (writer trimmed by write_content=false): the marker ends the round.
    ([], ["planner"]),
])
async def test_stage_completion_marker_does_not_cancel_the_remaining_plan(planned, expected_calls):
    calls: list[str] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        yield _text("这一部分已完成。\n[任务完成]")

    events = await _run_graph(
        fake_agent,
        _state("先规划，再写第1—3章"),
        router=AsyncMock(
            return_value={"current_agent": "planner", "workflow_plan": "standard", "workflow_agents": planned}
        ),
    )

    assert calls == expected_calls
    complete = [e for e in events if e.type == StreamEventType.WORKFLOW_COMPLETE]
    assert complete and complete[-1].data["reason"] == "task_complete"


# ------------------------------------------- 轮数耗尽时的空文件回滚（L3）


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_call_exhaustion_rolls_back_pending_empty_file():
    from agent.tools.mcp_tools import ToolContext

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        ToolContext.set_pending_empty_file("f6", "第六章")
        yield StreamEvent(
            type=StreamEventType.ITERATION_EXHAUSTED,
            data={"layer": "tool_call", "iterations_used": 60, "max_iterations": 60, "last_agent": "writer"},
        )

    rollback = MagicMock(return_value={"f6"})
    with (
        patch("agent.graph.writing_graph._probe_pending_file_body", return_value="empty"),
        patch("agent.graph.writing_graph._rollback_unfinished_empty_files", rollback),
    ):
        events = await _run_graph(fake_agent, _state("写第六章"))

    rollback.assert_called_once_with([{"file_id": "f6", "title": "第六章"}])
    # 回滚之后才发这一轮唯一的耗尽卡，层不变，带上已撤销的文件；不再另发文字说明。
    [card] = [e.data for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card["layer"] == "tool_call" and card["iterations_used"] == 60
    assert card["rolled_back_files"] == [{"file_id": "f6", "title": "第六章"}]
    assert "unfinished_files" not in card
    assert not any(e.type == StreamEventType.TEXT for e in events)


@pytest.mark.asyncio
@pytest.mark.unit
async def test_tool_call_exhaustion_keeps_files_whose_body_was_written():
    from agent.tools.mcp_tools import ToolContext

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        ToolContext.set_pending_empty_file("f6", "第六章")
        yield StreamEvent(
            type=StreamEventType.ITERATION_EXHAUSTED,
            data={"layer": "tool_call", "iterations_used": 60, "max_iterations": 60},
        )

    rollback = MagicMock(return_value=set())
    with (
        patch("agent.graph.writing_graph._probe_pending_file_body", return_value="written"),
        patch("agent.graph.writing_graph._rollback_unfinished_empty_files", rollback),
    ):
        events = await _run_graph(fake_agent, _state("写第六章"))

    rollback.assert_not_called()
    assert not any(e.type == StreamEventType.TEXT for e in events)
    [card] = [e.data for e in events if e.type == StreamEventType.ITERATION_EXHAUSTED]
    assert card == {"layer": "tool_call", "iterations_used": 60, "max_iterations": 60}, "没有空文件时卡片不变"


# ----------------------------------------- session_loader：路由信号与面包屑


def _history_row(content: str, metadata: dict | None = None, tool_calls=None):
    return SimpleNamespace(
        id="m1",
        role="assistant",
        content=content,
        message_metadata=json.dumps(metadata) if metadata is not None else None,
        reasoning_content=None,
        tool_calls=json.dumps(tool_calls) if tool_calls is not None else None,
    )


@pytest.mark.unit
def test_history_dict_exposes_routing_last_agent_and_clarification():
    from agent.core.session_loader import SessionLoader

    loader = SessionLoader("p", "u")
    routing = {"initial_agent": "quality_reviewer", "read_only": True}
    exhausted = loader._format_chat_message_for_history(
        _history_row(
            "阶段总结",
            {
                "stop_reason": "max_turns_exceeded",
                "routing": routing,
                "status_cards": [{"type": "iteration_exhausted", "layer": "tool_call", "lastAgent": "quality_reviewer"}],
            },
        )
    )
    assert exhausted["routing"] == routing
    assert exhausted["last_agent"] == "quality_reviewer"
    assert exhausted["clarification_pending"] is False

    clarification = loader._format_chat_message_for_history(
        _history_row(
            "",
            {"status_cards": [{"type": "workflow_stopped", "reason": "clarification_needed", "question": "主角叫什么？"}]},
        )
    )
    assert clarification["clarification_pending"] is True
    assert "routing" not in clarification


@pytest.mark.unit
def test_guard_rewritten_parallel_batch_pairs_results_by_type():
    """守卫拦下第 1 个读取后实际只执行 2 个子任务：不能把后面的结果安到前面的子任务上。"""
    from agent.core.session_loader import SessionLoader

    tool_call = {
        "name": "parallel_execute",
        "status": "success",
        "arguments": {
            "tasks": [
                {"type": "query_files", "description": "重读第一章", "params": {"id": "c1"}},
                {"type": "query_files", "description": "读第二章", "params": {"id": "c2"}},
                {"type": "edit_file", "description": "改第三章", "params": {"id": "c3"}},
            ]
        },
        "result": {
            "tasks": [
                {
                    "type": "query_files",
                    "status": "completed",
                    "result": {"status": "success", "data": [{"id": "c2", "title": "第二章"}]},
                },
                {"type": "edit_file", "status": "failed", "error": "找不到原文", "result": None},
            ]
        },
    }

    actions = SessionLoader("p", "u")._extract_parallel_file_actions(tool_call)

    assert actions == [{"name": "query_files", "file_id": "c2", "title": "第二章"}]


# ------------------------------------------------------- service：路由落库


@pytest.fixture
def routing_service_project(db_session: Session):
    from models import Project, User
    from services.core.auth_service import hash_password

    user = User(
        email="routing_continuity@example.com",
        username="routingcontinuity",
        hashed_password=hash_password("password123"),
        name="Routing Continuity",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="Routing Continuity", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return {"user": user, "project": project}


@pytest.fixture
def routing_service():
    from agent.schemas.context import ContextData
    from agent.service import AgentService

    assembler = MagicMock()
    assembler.assemble.return_value = ContextData(items=[], context="", token_estimate=0)
    with patch("agent.service.get_context_assembler", return_value=assembler):
        yield AgentService(context_assembler=assembler)


def _router_decided(agent: str, workflow: str, **routing) -> StreamEvent:
    return StreamEvent(
        type=StreamEventType.ROUTER_DECIDED,
        data={
            "initial_agent": agent,
            "workflow_plan": workflow,
            "workflow_agents": [],
            "routing_metadata": {"agent_type": agent, "workflow_type": workflow, **routing},
        },
    )


def _latest_assistant(db_session: Session, project_id: str):
    from models import ChatMessage, ChatSession

    db_session.rollback()
    return db_session.exec(
        select(ChatMessage)
        .join(ChatSession, ChatMessage.session_id == ChatSession.id)
        .where(ChatSession.project_id == project_id)
        .where(ChatMessage.role == "assistant")
        .order_by(desc(ChatMessage.created_at))
    ).first()


@pytest.mark.asyncio
@pytest.mark.usefixtures("writing_prompt_configs")
async def test_service_passes_real_selected_and_reference_facts_to_router_state(
    routing_service, routing_service_project, db_session: Session
):
    captured: dict = {}
    project = routing_service_project["project"]
    user = routing_service_project["user"]

    async def events():
        yield _router_decided("quality_reviewer", "review_only", read_only=True)
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    def workflow(state, **_kwargs):
        captured.update(state)
        return events()

    with patch("agent.service.run_writing_workflow_streaming", side_effect=workflow):
        async for _ in routing_service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="看看这里",
            selected_text="被选中的一段",
            metadata={"attached_file_ids": ["synthetic-file-id"]},
            session=db_session,
        ):
            pass

    assert captured["has_selected_text"] is True
    assert captured["has_referenced_files"] is True


@pytest.mark.asyncio
@pytest.mark.usefixtures("writing_prompt_configs")
async def test_routing_round_trips_from_save_to_next_turn_resume(
    routing_service, routing_service_project, db_session: Session
):
    """本轮落库的路由，下一轮加载历史后能让「继续」恢复成只读审稿人。"""
    from agent.core.session_loader import SessionLoader
    from agent.graph.router import RouterDecision, _resolve_turn_decision

    project = routing_service_project["project"]
    user = routing_service_project["user"]

    async def workflow():
        yield _router_decided(
            "quality_reviewer", "review_only", read_only=True, write_content=False, scope="前60章"
        )
        yield StreamEvent(type=StreamEventType.AGENT_SELECTED, data={"agent_type": "quality_reviewer"})
        yield _text("阶段总结：已核对前 30 章。")
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "max_turns_exceeded"})

    with patch("agent.service.run_writing_workflow_streaming", return_value=workflow()):
        async for _ in routing_service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="检查前60章一致性，不要改文件",
            session=db_session,
        ):
            pass

    saved = _latest_assistant(db_session, str(project.id))
    routing = json.loads(saved.message_metadata)["routing"]
    assert routing == {
        "initial_agent": "quality_reviewer",
        "workflow_type": "review_only",
        "read_only": True,
        "write_content": False,
        "scope": "前60章",
        "scope_constraints": [],
        "requested_file_types": [],
        "last_agent": "quality_reviewer",
        "owner_agent": "quality_reviewer",
    }

    history = SessionLoader(str(project.id), str(user.id)).load_chat_session(db_session).history_messages
    state = {
        "user_message": "继续",
        "router_message": "继续",
        "messages": history + [{"role": "user", "content": "继续"}],
    }
    resumed, metadata = _resolve_turn_decision(
        state,
        RouterDecision(
            agent_type="writer",
            workflow_type="quick",
            turn_disposition="resume_interrupted_task",
            read_only=False,
            write_content=True,
        ),
    )
    assert resumed.agent_type == "quality_reviewer"
    assert resumed.read_only is True
    assert metadata["scope_constraints"] == ["前60章"]


@pytest.mark.asyncio
@pytest.mark.usefixtures("writing_prompt_configs")
async def test_reviewer_reached_by_handoff_is_not_saved_as_the_task_owner(
    routing_service, routing_service_project, db_session: Session
):
    """I16：writer 写完交给审稿人（自动质量门控 / 送审），审稿人在结尾提问；作者回答时
    下一轮要回到 writer。落库的 last_agent 仍是最后说话的审稿人，owner_agent 是 writer。"""
    from agent.core.session_loader import SessionLoader
    from agent.graph.router import RouterDecision, _resolve_turn_decision

    project = routing_service_project["project"]
    user = routing_service_project["user"]

    async def workflow():
        yield _router_decided("writer", "quick", write_content=True, scope="第四节")
        yield StreamEvent(type=StreamEventType.AGENT_SELECTED, data={"agent_type": "writer"})
        yield _text("第四节写好了，我再通读核对一遍。")
        yield StreamEvent(type=StreamEventType.AGENT_SELECTED, data={"agent_type": "quality_reviewer"})
        yield _text("没发现需要返工的地方。要不要把那句交代写实一点？")
        yield StreamEvent(
            type=StreamEventType.WORKFLOW_STOPPED,
            data={
                "reason": "clarification_needed",
                "agent_type": "quality_reviewer",
                "message": "要不要把那句交代写实一点？",
            },
        )
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    with patch("agent.service.run_writing_workflow_streaming", return_value=workflow()):
        async for _ in routing_service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="写第四节",
            session=db_session,
        ):
            pass

    saved = _latest_assistant(db_session, str(project.id))
    routing = json.loads(saved.message_metadata)["routing"]
    assert routing["last_agent"] == "quality_reviewer"
    assert routing["owner_agent"] == "writer"

    history = SessionLoader(str(project.id), str(user.id)).load_chat_session(db_session).history_messages
    state = {
        "user_message": "可以",
        "router_message": "可以",
        "messages": history + [{"role": "user", "content": "可以"}],
    }
    inherited, _metadata = _resolve_turn_decision(
        state,
        RouterDecision(
            agent_type="planner",
            workflow_type="standard",
            turn_disposition="answer_pending_clarification",
            read_only=False,
            write_content=True,
        ),
    )
    assert inherited.agent_type == "writer"


@pytest.mark.asyncio
@pytest.mark.usefixtures("writing_prompt_configs")
@pytest.mark.parametrize("deadline", [False, True])
async def test_cancelled_partial_save_persists_stop_reason_and_routing(
    routing_service, routing_service_project, db_session: Session, deadline
):
    """取消补存的 stop_reason 由路由层交来的原因决定，不按已用时长推断：发布交接把时限提前到
    几分钟时，这一轮也记成可「继续」的时限截停；之后记「已中断」终态也不会覆盖它。"""
    from agent.core.round_outcome import RunOutcome, record_round_outcome
    from agent.graph.router import RESUMABLE_STOP_REASONS
    from models import ChatMessage

    project = routing_service_project["project"]
    user = routing_service_project["user"]
    started = asyncio.Event()

    async def slow_workflow():
        yield _router_decided("writer", "quick", write_content=True, scope="")
        yield StreamEvent(type=StreamEventType.AGENT_SELECTED, data={"agent_type": "writer"})
        yield _text("写到一半")
        started.set()
        await asyncio.sleep(3600)

    run_outcome = RunOutcome()

    async def consume():
        async for _ in routing_service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message="写第六章",
            session=db_session,
            run_outcome=run_outcome,
        ):
            pass

    with (
        patch("agent.service.run_writing_workflow_streaming", return_value=slow_workflow()),
        patch("agent.service.create_session", side_effect=lambda: Session(db_session.get_bind())),
        patch("database.create_session", side_effect=lambda: Session(db_session.get_bind())),
    ):
        task = asyncio.create_task(consume())
        await started.wait()
        await asyncio.sleep(0)
        if deadline:
            # 路由层的时限分支（sse_pump 的 on_deadline）在取消送达之前同步记下原因。
            run_outcome.deadline_stop_reason = "run_deadline_exceeded"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        saved: ChatMessage | None = None
        for _ in range(100):
            await asyncio.sleep(0.05)
            saved = _latest_assistant(db_session, str(project.id))
            if saved is not None:
                break

        assert saved is not None
        metadata = json.loads(saved.message_metadata)
        assert metadata["stop_reason"] == ("run_deadline_exceeded" if deadline else "cancelled")
        assert metadata["routing"]["last_agent"] == "writer"
        if deadline:
            # 时限截停的一轮随后记「已中断」终态（api/agent.py 的 stop_kind="error"）。
            assert record_round_outcome(saved.id, stop_kind="error", outcome={"reason": "error"})
            db_session.expire_all()
            final = json.loads(db_session.get(ChatMessage, saved.id).message_metadata)
            assert final["stop_reason"] in RESUMABLE_STOP_REASONS


@pytest.mark.asyncio
@pytest.mark.unit
async def test_no_new_agent_run_starts_near_the_wall_clock_limit():
    import time

    from agent.core.run_meter import AgentRunMeter
    from agent.graph.writing_graph import WRAP_UP_TEXT

    calls: list[str] = []
    meter = AgentRunMeter(wrap_up_after_s=600)

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        meter.started_at = time.monotonic() - 700  # this run took the request past the wrap-up point
        for event in _write_done("w1"):
            yield event
        yield _text(LONG_TEXT)
        yield _handoff("quality_reviewer", "请审查第五章")

    with patch("agent.graph.writing_graph._ai_messages_left_today", return_value=True) as quota:
        events = await _run_graph(fake_agent, _state("写第五章", generation_mode="quality", run_meter=meter))

    assert calls == ["writer"]
    quota.assert_called_once_with("u")
    texts = "".join(e.data.get("text", "") for e in events if e.type == StreamEventType.TEXT)
    assert texts.endswith(WRAP_UP_TEXT)
    assert not any(e.type == StreamEventType.ITERATION_EXHAUSTED for e in events)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("trigger", ["wall_clock", "server_drain"])
async def test_wrap_up_keeps_the_writer_summary_when_review_will_not_run(trigger, monkeypatch):
    """writer 交给审稿人时已到收尾时间（墙钟或发布交接）：审稿不会再跑，不丢 writer 的总结、
    不发悬空的 HANDOFF；交接里【要告诉作者】的话照实转述，最后是收尾说明。"""
    import time

    from agent.core import server_drain
    from agent.core.run_meter import AgentRunMeter
    from agent.core.workflow_events import CHAT_HOLD_BEGIN, CHAT_HOLD_DISCARD
    from agent.graph.writing_graph import WRAP_UP_TEXT

    monkeypatch.setattr(server_drain, "_draining_since", None)
    calls: list[str] = []
    meter = AgentRunMeter(wrap_up_after_s=600)

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append(agent_type)
        for event in _write_done("w1"):
            yield event
        yield _text(LONG_TEXT)
        if trigger == "wall_clock":
            meter.started_at = time.monotonic() - 700
        else:
            monkeypatch.setattr(server_drain, "_draining_since", time.monotonic())
        for event in _handoff_call("quality_reviewer", "请审查第五章\n[要告诉作者]\n旧稿可以在历史版本里找回"):
            yield event

    with patch("agent.graph.writing_graph._ai_messages_left_today", return_value=True):
        events = await _run_graph(fake_agent, _state("写第五章", generation_mode="quality", run_meter=meter))

    assert calls == ["writer"]
    holds = [e.data.get("action") for e in events if e.type == StreamEventType.CHAT_HOLD]
    assert CHAT_HOLD_BEGIN in holds
    assert CHAT_HOLD_DISCARD not in holds
    assert not any(e.type == StreamEventType.HANDOFF for e in events)
    texts = "".join(e.data.get("text", "") for e in events if e.type == StreamEventType.TEXT)
    assert LONG_TEXT in texts
    assert texts.endswith(f"\n\n旧稿可以在历史版本里找回{WRAP_UP_TEXT}")


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(("messages_left", "continue_hint"), [(False, False), (None, True)])
async def test_wrap_up_does_not_say_reply_continue_once_today_is_used_up(messages_left, continue_hint):
    """最后一条免费消息撞上收尾：回复「继续」发不出去，不再这样说；读不到额度时保持原说明。"""
    import time

    from agent.core.run_meter import AgentRunMeter
    from agent.graph.writing_graph import WRAP_UP_TEXT, WRAP_UP_TEXT_NO_MESSAGES_LEFT

    meter = AgentRunMeter(wrap_up_after_s=600)

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        meter.started_at = time.monotonic() - 700
        for event in _write_done("w1"):
            yield event
        yield _text(LONG_TEXT)
        yield _handoff("quality_reviewer", "请审查第五章")

    with patch("agent.graph.writing_graph._ai_messages_left_today", return_value=messages_left):
        events = await _run_graph(fake_agent, _state("写第五章", generation_mode="quality", run_meter=meter))

    texts = "".join(e.data.get("text", "") for e in events if e.type == StreamEventType.TEXT)
    assert texts.endswith(WRAP_UP_TEXT if continue_hint else WRAP_UP_TEXT_NO_MESSAGES_LEFT)
    assert ("继续" in texts.rsplit(LONG_TEXT, 1)[-1]) is continue_hint


@pytest.mark.asyncio
@pytest.mark.unit
async def test_english_author_sees_english_status_and_wrap_up():
    """英文界面：路由状态条、交接原因和时限收尾说明都用英文，不夹中文固定文案。"""
    import time

    from agent.core.run_meter import AgentRunMeter
    from agent.graph.writing_graph import WRAP_UP_TEXT_EN

    meter = AgentRunMeter(wrap_up_after_s=600)

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        meter.started_at = time.monotonic() - 700
        for event in _write_done("w1"):
            yield event
        yield _text(LONG_TEXT)
        yield _handoff("quality_reviewer", "请审查第五章")

    with patch("agent.graph.writing_graph._ai_messages_left_today", return_value=True):
        events = await _run_graph(
            fake_agent,
            _state("Write chapter five", generation_mode="fast", run_meter=meter, language="en"),
        )

    router_messages = [e.data["message"] for e in events if e.type == StreamEventType.ROUTER_THINKING]
    assert router_messages == ["Fast mode: reading your request..."]
    texts = "".join(e.data.get("text", "") for e in events if e.type == StreamEventType.TEXT)
    assert texts.endswith(WRAP_UP_TEXT_EN)
