"""模型输入的前缀缓存布局：[系统提示][历史] 跨轮、跨 agent 逐字不变，随轮次 / 角色变化的内容只在其后。

DeepSeek 按逐字前缀命中缓存（miss 的单价是 hit 的 50 倍）。系统提示里只要有一处随轮次或角色
变化，后面整段回放的历史就全部按 miss 计价。这里用真实的 process_stream / 工作流组装两轮输入、
以及同一请求内 writer → quality_reviewer → writer 的三次运行输入，逐字节比较共享前缀。
不调用模型。
"""

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session

from agent.core.message_manager import AGENT_DIRECTIVE_HEADER, TURN_CONTEXT_OPEN
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.nodes import run_streaming_agent
from agent.openai_agents.runner import (
    _append_assistant_turn_to_state_messages,
    build_history_messages,
    build_run_input,
)
from agent.schemas.context import ContextData
from models import ChatMessage, ChatSession, Project, User
from services.core.auth_service import hash_password

pytestmark = pytest.mark.usefixtures("writing_prompt_configs")


CHARACTER_CARD = "角色：码头邮差阿昼，左手有旧伤，只在雾天送信。" * 12


def _context(marker: str) -> ContextData:
    """一轮的组装上下文：项目状态与角色卡两轮相同，正文章节每轮不同。"""
    header = "项目状态 [核心信息]\n【项目简介】雾港来信，悬疑。"
    chapter = f"【正文详情】\n第{marker}章 (id=ch-{marker}) [全文]\n雾港的第{marker}封信到了。"
    card = f"【角色信息】\n阿昼 (id=char-1) [全文]\n{CHARACTER_CARD}"
    items = [
        {
            "id": "char-1",
            "type": "character",
            "title": "阿昼",
            "content": CHARACTER_CARD,
            "priority": "constraint",
            "metadata": {"file_type": "character"},
        }
    ]
    return ContextData(
        items=items,
        context="\n\n".join([header, chapter, card]),
        blocks=[
            {"key": "header", "text": header},
            {"key": f"【正文详情】:ch-{marker}", "text": chapter},
            {"key": "【角色信息】:char-1", "text": card},
        ],
        token_estimate=50,
    )


@pytest.fixture(autouse=True)
def _fresh_snapshot_store(monkeypatch):
    from agent.context.turn_snapshot import reset_snapshot_memory

    monkeypatch.delenv("REDIS_URL", raising=False)
    reset_snapshot_memory()
    yield
    reset_snapshot_memory()


@pytest.fixture
def project_with_history(db_session: Session):
    user = User(
        email="cache_prefix@example.com",
        username="cacheprefix",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="雾港来信", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="先写第一章"))
    db_session.add(ChatMessage(session_id=chat.id, role="assistant", content="第一章已经写进正文。"))
    db_session.commit()
    return user, project, chat


async def _run_turn(db_session, user, project, chat, *, message, context, metadata) -> dict[str, Any]:
    """跑一轮真实的 process_stream，返回工作流收到的 state（模型不调用）。"""
    from agent.service import AgentService

    assembler = MagicMock()
    assembler.assemble.return_value = context
    captured: dict[str, Any] = {}

    def fake_workflow(state, **_kwargs):
        captured.update(state)

        async def stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": f"已按「{message}」处理。"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return stream()

    with (
        patch("agent.service.get_context_assembler", return_value=assembler),
        patch("agent.service.run_writing_workflow_streaming", side_effect=fake_workflow),
    ):
        service = AgentService(context_assembler=assembler)
        async for _event in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message=message,
            session=db_session,
            session_id=chat.id,
            metadata=metadata,
        ):
            pass
    return captured


def _model_input(state: dict[str, Any], agent_type: str) -> tuple[str, list[dict[str, Any]]]:
    """这次 agent 运行发给模型的 (instructions, input)，与 nodes + runner 的组装一致。"""
    from agent.graph.nodes import build_agent_directive

    return state["system_prompt"], build_run_input(
        build_history_messages(state), build_agent_directive(state, agent_type), state["context_snapshot"]
    )


def _serialize(instructions: str, messages: list[dict[str, Any]]) -> bytes:
    """按发给模型的顺序序列化：系统提示在最前，之后逐条消息。"""
    payload = [{"role": "system", "content": instructions}, *messages]
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


@pytest.mark.asyncio
@pytest.mark.unit
async def test_consecutive_turns_share_system_prompt_and_history_byte_for_byte(db_session, project_with_history):
    user, project, chat = project_with_history

    first = await _run_turn(
        db_session, user, project, chat,
        message="续写第二章", context=_context("二"),
        metadata={"current_file_id": "ch-二", "current_file_type": "draft", "current_file_title": "第二章"},
    )
    second = await _run_turn(
        db_session, user, project, chat,
        message="Rewrite chapter three in English", context=_context("三"),
        metadata={"current_file_id": "ch-三", "current_file_type": "draft", "current_file_title": "第三章"},
    )

    system_1, input_1 = _model_input(first, "writer")
    system_2, input_2 = _model_input(second, "writer")

    # 系统提示跨轮逐字相同，不含任何一轮的上下文、当前文件、任务板或本轮语言。
    assert system_1 == system_2
    for per_turn in ("雾港的第二封信", "雾港的第三封信", "角色二", "ch-二", "## 当前任务计划板", "## 当前文件",
                     "## 本轮回复语言", "## Reply language for this turn"):
        assert per_turn not in system_1

    # [项目资料快照][上一轮之前的历史] 是两轮共同的前缀，序列化后逐字节相同：没变的上下文
    # （项目状态、角色卡、第二章）只在快照里发一次，第二轮不再重发。
    snapshot = input_1[0]
    assert input_2[0] == snapshot
    assert snapshot["content"].startswith("<project_snapshot>")
    assert "阿昼" in snapshot["content"] and "雾港的第二封信" in snapshot["content"]
    history = [{"role": "user", "content": "先写第一章"}, {"role": "assistant", "content": "第一章已经写进正文。"}]
    assert input_1[1:3] == history
    assert input_2[1:3] == history
    shared = _serialize(system_1, [snapshot, *history])
    assert _serialize(system_1, input_1).startswith(shared[:-1])
    assert _serialize(system_2, input_2).startswith(shared[:-1])

    # 本轮上下文只在本轮用户消息里：第一轮的资料都在快照里，第二轮只带新出现的第三章。
    turn_1, turn_2 = input_1[3]["content"], input_2[5]["content"]
    assert turn_1.startswith(TURN_CONTEXT_OPEN) and turn_2.startswith(TURN_CONTEXT_OPEN)
    assert "雾港的第二封信" not in turn_1 and "本轮作者用中文写消息" in turn_1
    assert "雾港的第三封信" in turn_2 and "本轮更新的项目资料" in turn_2
    assert CHARACTER_CARD not in turn_2 and "雾港的第二封信" not in turn_2
    assert "wrote this turn's message in English" in turn_2
    assert input_2[3] == {"role": "user", "content": "续写第二章"}
    assert input_2[4]["role"] == "assistant"

    # 角色与范围约束是这次运行的最后一条消息。
    assert input_1[-1]["content"].startswith(AGENT_DIRECTIVE_HEADER)
    assert "## 当前角色：writer" in input_1[-1]["content"]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_writer_reviewer_writer_share_system_prompt_history_and_turn_context(
    db_session, project_with_history, monkeypatch
):
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext
    from services.features.activation_event_service import activation_event_service

    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    user, project, chat = project_with_history
    state = await _run_turn(
        db_session, user, project, chat,
        message="续写第二章", context=_context("二"), metadata=None,
    )

    runs: list[tuple[str, str, list[dict[str, Any]]]] = []

    async def fake_runner(state, agent_type, system_prompt, get_steering_messages=None, agent_directive=""):
        api_messages = build_history_messages(state)
        runs.append(
            (agent_type, system_prompt, build_run_input(api_messages, agent_directive, state["context_snapshot"]))
        )
        if agent_type == "quality_reviewer":
            text = "第二章节奏偏慢，请压缩码头那段。"
            handoff = {"target_agent": "writer", "context": "按审稿意见压缩码头那段"}
        elif len(runs) == 1:
            text = "第二章写好了，交给审稿。"
            handoff = {"target_agent": "quality_reviewer", "context": "检查第二章"}
        else:
            text = "已按审稿意见修改。[TASK_COMPLETE]"
            handoff = None
        _append_assistant_turn_to_state_messages(
            state, api_messages, assistant_text=text, thinking_text="", tool_uses=[]
        )
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": text})
        if handoff:
            yield StreamEvent(type=StreamEventType.HANDOFF, data=handoff)
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    route = AsyncMock(return_value={
        "current_agent": "writer", "workflow_plan": "quick", "workflow_agents": [],
        "routing_metadata": {"agent_type": "writer", "scope": "只写第二章"},
    })
    ToolContext.set_context(db_session, user.id, project.id, chat.id, author_message="续写第二章")
    try:
        with (
            patch("agent.graph.writing_graph.router_node", route),
            patch("agent.graph.writing_graph.run_streaming_agent", new=run_streaming_agent),
            patch("agent.graph.nodes.run_openai_agents_streaming_agent", new=fake_runner),
            patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
            patch("agent.graph.writing_graph._auto_finalize_task_board_on_completion", AsyncMock(return_value=[])),
        ):
            _ = [event async for event in run_writing_workflow_streaming(state=state, thread_id="cache-prefix")]
    finally:
        ToolContext.clear_context()

    assert [agent for agent, _, _ in runs] == ["writer", "quality_reviewer", "writer"]
    systems = {system for _, system, _ in runs}
    assert len(systems) == 1  # 三次运行的系统提示逐字相同
    system = systems.pop()
    assert "## 当前角色：" not in system
    assert "## 本轮用户范围约束" not in system

    # [系统提示][项目资料快照][历史][本轮用户消息（含 <turn_context>）] 是三次运行共同的前缀，逐字节相同。
    shared_messages = runs[0][2][:4]
    assert shared_messages[0]["content"].startswith("<project_snapshot>")
    assert "雾港的第二封信" in shared_messages[0]["content"]
    assert shared_messages[3]["content"].startswith(TURN_CONTEXT_OPEN)
    shared = _serialize(system, shared_messages)
    for _agent, run_system, run_input in runs:
        assert run_input[:4] == shared_messages
        assert _serialize(run_system, run_input).startswith(shared[:-1])

    # 角色与范围约束只在各自运行的最后一条消息里，审稿人的指令不回流到返修 writer 的输入。
    directives = [run_input[-1]["content"] for _, _, run_input in runs]
    assert all(directive.startswith(AGENT_DIRECTIVE_HEADER) for directive in directives)
    assert "## 当前角色：writer" in directives[0] and "## 当前角色：writer" in directives[2]
    assert "## 当前角色：quality_reviewer" in directives[1]
    assert all("[最高优先级]" in directive for directive in directives)
    rework_input = runs[2][2]
    assert sum(AGENT_DIRECTIVE_HEADER in str(message["content"]) for message in rework_input) == 1
