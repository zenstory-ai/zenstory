"""作者在一部作品里的第一条消息：模型收到「先写再给方向」的隐藏提示，落库和界面仍是作者原话。"""

from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, select

from agent.core.message_manager import TURN_CONTEXT_OPEN
from agent.core.session_loader import WORKING_SET_USER_MESSAGE_LABEL
from agent.service import (
    DASHBOARD_IDEA_ENTRY,
    FIRST_MESSAGE_ENTRY,
    STARTER_EXAMPLE_ENTRY,
    first_message_hint,
)
from models import ChatMessage, ChatSession, Project, User
from services.core.auth_service import hash_password

pytestmark = pytest.mark.usefixtures("writing_prompt_configs")

IDEA = "一个关于外卖员的故事"


def _author_part(model_user_turn: str) -> str:
    """模型收到的本轮 user 消息里作者那一段：系统附上的 <turn_context> 在标签之前。"""
    turn_context, label, author_part = model_user_turn.rpartition(WORKING_SET_USER_MESSAGE_LABEL)
    assert label and turn_context.startswith(TURN_CONTEXT_OPEN)
    return author_part.lstrip("\n")


@pytest.fixture
def short_project(db_session: Session):
    user = User(
        email="kickoff_hint@example.com",
        username="kickoffhint",
        hashed_password=hash_password("password123"),
        name="Kickoff Hint",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="我的短篇", owner_id=user.id, project_type="short")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return user, project


@pytest.fixture
def agent_service():
    from agent.schemas.context import ContextData
    from agent.service import AgentService

    assembler = MagicMock()
    assembler.assemble.return_value = ContextData(items=[], context="", token_estimate=0)
    with patch("agent.service.get_context_assembler", return_value=assembler):
        yield AgentService(context_assembler=assembler)


async def _run(service, db_session, user, project, *, metadata, language=None, session_id=None):
    """跑一轮 process_stream，返回工作流收到的 state。"""
    from agent.core.workflow_events import StreamEvent, StreamEventType

    captured: dict = {}

    def fake_workflow(state, **_kwargs):
        captured.update(state)

        async def stream():
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "好的"})
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return stream()

    with patch("agent.service.run_writing_workflow_streaming", side_effect=fake_workflow):
        async for _event in service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message=IDEA,
            session=db_session,
            metadata=metadata,
            language=language,
            session_id=session_id,
        ):
            pass
    return captured


@pytest.mark.asyncio
async def test_dashboard_idea_adds_hint_for_model_only(agent_service, db_session, short_project):
    user, project = short_project

    state = await _run(
        agent_service, db_session, user, project, metadata={"entry": DASHBOARD_IDEA_ENTRY}
    )

    hint = first_message_hint("short", force_en=False)
    model_user_turn = _author_part(state["messages"][-1]["content"])
    assert model_user_turn.startswith(IDEA)
    assert hint in model_user_turn
    assert "短篇小说" in hint
    # 路由器看作者原话，另由作品类型那一行知道这是第一条（router.build_router_user_message）
    assert state["router_message"] == IDEA
    assert state["first_message_project_label"] == "短篇小说"

    saved = db_session.exec(
        select(ChatMessage).where(ChatMessage.role == "user")
    ).all()
    assert [m.content for m in saved] == [IDEA]


@pytest.mark.asyncio
async def test_without_entry_model_input_is_unchanged(agent_service, db_session, short_project):
    user, project = short_project

    with_other_metadata = await _run(
        agent_service, db_session, user, project, metadata={"entry": "chat_input"}
    )
    assert _author_part(with_other_metadata["messages"][-1]["content"]) == IDEA

    without_metadata = await _run(agent_service, db_session, user, project, metadata=None)
    assert _author_part(without_metadata["messages"][-1]["content"]) == IDEA
    assert without_metadata["first_message_project_label"] == ""


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [STARTER_EXAMPLE_ENTRY, FIRST_MESSAGE_ENTRY])
async def test_starter_and_typed_first_message_get_the_same_hint(
    agent_service, db_session, short_project, entry
):
    user, project = short_project

    state = await _run(agent_service, db_session, user, project, metadata={"entry": entry})

    assert first_message_hint("short", force_en=False) in state["messages"][-1]["content"]
    assert state["first_message_project_label"] == "短篇小说"


@pytest.mark.asyncio
@pytest.mark.parametrize("same_session", [True, False])
async def test_no_hint_once_the_work_has_an_ai_reply(agent_service, db_session, short_project, same_session):
    """作品里有过 AI 回复：同一会话里过期的标记、老作品里新开的空对话，都不当第一条处理。"""
    user, project = short_project
    earlier = ChatSession(user_id=user.id, project_id=project.id, title="对话")
    db_session.add(earlier)
    db_session.commit()
    db_session.refresh(earlier)
    db_session.add(ChatMessage(session_id=earlier.id, role="user", content="先写个开头"))
    db_session.add(ChatMessage(session_id=earlier.id, role="assistant", content="开头已经写进正文。"))
    db_session.commit()
    target = earlier
    if not same_session:
        target = ChatSession(user_id=user.id, project_id=project.id, title="新对话")
        db_session.add(target)
        db_session.commit()
        db_session.refresh(target)

    state = await _run(
        agent_service,
        db_session,
        user,
        project,
        metadata={"entry": FIRST_MESSAGE_ENTRY},
        session_id=target.id,
    )

    assert _author_part(state["messages"][-1]["content"]) == IDEA
    assert state["first_message_project_label"] == ""


@pytest.mark.asyncio
async def test_english_hint_follows_force_en(agent_service, db_session, short_project):
    user, project = short_project

    state = await _run(
        agent_service,
        db_session,
        user,
        project,
        metadata={"entry": DASHBOARD_IDEA_ENTRY},
        language="en",
    )

    model_user_turn = _author_part(state["messages"][-1]["content"])
    assert first_message_hint("short", force_en=True) in model_user_turn
    assert first_message_hint("short", force_en=False) not in model_user_turn
    assert "Short Story" in model_user_turn


def test_unknown_project_type_falls_back_to_generic_label():
    assert "「作品」" in first_message_hint(None, force_en=False)
    assert "screenplay" not in first_message_hint("screenplay", force_en=True)
    assert "短剧剧本" in first_message_hint("screenplay", force_en=False)


@pytest.mark.parametrize("force_en", [False, True])
def test_hint_writes_before_asking(force_en):
    """免费版每天 3 条：模糊想法也先写开头再给两个方向，不再「先问两三个问题」。"""
    hint = first_message_hint("novel", force_en=force_en)
    if force_en:
        assert "do not ask questions first" in hint
        assert "two alternative directions" in hint
        assert "ask two or three key questions" not in hint
    else:
        assert "不要先提问" in hint
        assert "约 800 字的开头写进正文" in hint
        assert "两个可以换的方向" in hint
        assert "先问两三个关键问题" not in hint
    assert "第 1 集剧本" in first_message_hint("screenplay", force_en=False)


@pytest.mark.asyncio
async def test_tools_see_the_author_words_without_the_hidden_hint(
    agent_service, db_session, short_project
):
    """update_project 核对「作者是否要求改名」时，看的是作者原话，不是拼了提示的模型输入。"""
    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.tools.mcp_tools import ToolContext

    user, project = short_project
    seen: dict = {}

    def fake_workflow(state, **_kwargs):
        seen["author_messages"] = ToolContext.get_author_messages()

        async def stream():
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return stream()

    with patch("agent.service.run_writing_workflow_streaming", side_effect=fake_workflow):
        async for _event in agent_service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message=IDEA,
            session=db_session,
            metadata={"entry": DASHBOARD_IDEA_ENTRY},
        ):
            pass

    assert seen["author_messages"] == [IDEA]


@pytest.mark.asyncio
async def test_first_conversation_check_runs_off_the_event_loop_on_postgres(
    agent_service, db_session, short_project, monkeypatch
):
    """PostgreSQL 上用独立短 session 在线程里查；SQLite 测试库直接用请求 session。"""
    from contextlib import contextmanager

    import agent.service as service_module

    _user, project = short_project

    @contextmanager
    def fake_session():
        yield db_session

    monkeypatch.setattr(service_module, "create_session", fake_session)
    monkeypatch.setattr(agent_service, "_should_offload_session_work", lambda _session: True)

    assert await agent_service._is_first_ai_conversation(db_session, project.id) is True


@pytest.mark.asyncio
async def test_first_conversation_check_failure_means_no_hint(agent_service, db_session, short_project, monkeypatch):
    """查不到时按「不是第一条」处理：宁可不加提示，也不误导路由。"""
    import agent.service as service_module

    _user, project = short_project

    def broken(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service_module, "_project_has_ai_reply", broken)

    assert await agent_service._is_first_ai_conversation(db_session, project.id) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_message_label", "expected_agents"),
    [("长篇小说", ["writer"]), ("", ["writer", "quality_reviewer"])],
)
async def test_first_message_round_ends_with_the_writers_own_summary(first_message_label, expected_agents):
    """高质量模式下，作品第一条消息写完开头不自动送审：作者看到的总结是「写了什么」，不是返修记录。"""
    from unittest.mock import AsyncMock

    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext
    from tests.test_agent.body_write_events import edit_file_events

    agents: list[str] = []

    async def fake_agent(_state, agent_type, *_args, **_kwargs):
        agents.append(agent_type)
        if agent_type == "writer":
            for event in edit_file_events("e1", "灯芯烧得只剩一指长。" * 80):
                yield event
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "写好了第一章开头。"})
        else:
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "审完了。[TASK_COMPLETE]"})

    state = {
        "user_message": IDEA,
        "messages": [],
        "system_prompt": "",
        "generation_mode": "quality",
        "first_message_project_label": first_message_label,
    }
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch(
                "agent.graph.writing_graph.router_node",
                AsyncMock(return_value={"routing_metadata": {"write_content": True}}),
            ),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
        ):
            events = [
                event
                async for event in run_writing_workflow_streaming(
                    state=state, thread_id="first-message", auto_review_threshold=100
                )
            ]
    finally:
        ToolContext.clear_context()

    assert agents == expected_agents
    if not first_message_label:
        return
    # 没有送审，writer 的总结不会被扣下丢掉。
    holds = [e.data.get("action") for e in events if e.type == StreamEventType.CHAT_HOLD]
    assert "discard" not in holds
    assert "写好了第一章开头。" in "".join(
        str(e.data.get("text") or "") for e in events if e.type == StreamEventType.TEXT
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first_message_label", "prose_before_handoff", "turn", "expected_agents"),
    [
        # The writer hands its fresh opening to the reviewer without a word to the author (as the
        # reply protocol asks before a handoff): it summarizes itself instead, no review.
        ("长篇小说", "", "handoff_only", ["writer", "writer"]),
        # It already told the author what it wrote: just drop the review.
        ("长篇小说", "写好了第一章开头，约 800 字。", "handoff_only", ["writer"]),
        # Same, with another tool called in the same turn before the handoff.
        ("长篇小说", "写好了第一章开头，约 800 字。", "with_update_project", ["writer"]),
        # The author sent a message mid-run: the steering follow-up round handles it, not a
        # summary-only rerun that would ignore it.
        ("长篇小说", "", "with_steering", ["writer", "writer"]),
        # Later rounds keep the writer's own handoff to review.
        ("", "", "handoff_only", ["writer", "quality_reviewer"]),
    ],
)
async def test_first_message_round_does_not_follow_the_writers_own_review_handoff(
    first_message_label, prose_before_handoff, turn, expected_agents
):
    """2026-10-20 production sample: with the auto-review gate off, the writer still called
    handoff_to_agent(quality_reviewer); a rework would have left the author a fix log."""
    from unittest.mock import AsyncMock

    from agent.core.workflow_events import StreamEvent, StreamEventType
    from agent.graph.writing_graph import run_writing_workflow_streaming
    from agent.tools.mcp_tools import ToolContext
    from tests.test_agent.body_write_events import edit_file_events

    calls: list[tuple[str, str]] = []

    async def fake_agent(state, agent_type, *_args, **_kwargs):
        calls.append((agent_type, str(state.get("user_message") or "")))
        writer_runs = sum(1 for agent, _ in calls if agent == "writer")
        if agent_type == "writer" and writer_runs == 1:
            yield StreamEvent(type=StreamEventType.MESSAGE_START, data={})
            for event in edit_file_events("e1", "灯芯烧得只剩一指长。" * 80):
                yield event
            if turn == "with_steering":
                yield StreamEvent(type=StreamEventType.STEERING_RECEIVED, data={"message": "主角改成女的"})
            if prose_before_handoff:
                yield StreamEvent(type=StreamEventType.TEXT, data={"text": prose_before_handoff})
            if turn == "with_update_project":
                yield StreamEvent(
                    type=StreamEventType.TOOL_USE,
                    data={"id": "u1", "name": "update_project", "status": "complete", "input": {}},
                )
            yield StreamEvent(
                type=StreamEventType.TOOL_USE,
                data={"id": "h1", "name": "handoff_to_agent", "status": "complete", "input": {}},
            )
            yield StreamEvent(
                type=StreamEventType.HANDOFF,
                data={"target_agent": "quality_reviewer", "reason": "送审", "context": "两份内容已写完，需通读核对"},
            )
        elif agent_type == "writer":
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "写好了第一章《九十三天》。"})
        else:
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "审完了。[TASK_COMPLETE]"})

    state = {
        "user_message": IDEA,
        "messages": [],
        "system_prompt": "",
        "generation_mode": "quality",
        "first_message_project_label": first_message_label,
    }
    ToolContext.set_context(session=None, user_id="u", project_id="p", session_id="s")
    try:
        with (
            patch(
                "agent.graph.writing_graph.router_node",
                AsyncMock(return_value={"routing_metadata": {"write_content": True}}),
            ),
            patch("agent.graph.writing_graph.get_next_node", return_value="writer"),
            patch("agent.graph.writing_graph.run_streaming_agent", new=fake_agent),
        ):
            events = [
                event
                async for event in run_writing_workflow_streaming(
                    state=state, thread_id="first-message-handoff", auto_review_threshold=100
                )
            ]
    finally:
        ToolContext.clear_context()

    assert [agent for agent, _ in calls] == expected_agents
    if not first_message_label:
        return
    assert not [e for e in events if e.type == StreamEventType.HANDOFF]
    text = "".join(str(e.data.get("text") or "") for e in events if e.type == StreamEventType.TEXT)
    if turn == "with_steering":
        # The second run follows the author's new message, not the summary-only instruction.
        assert "不送审" not in calls[1][1]
        assert "新的引导消息" in calls[1][1]
    elif len(calls) == 2:
        # The second writer run is told to write only the author's summary.
        assert "不送审" in calls[1][1]
        assert "写好了第一章《九十三天》。" in text
    else:
        assert prose_before_handoff in text
