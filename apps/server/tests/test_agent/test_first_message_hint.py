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
    STARTER_MANUSCRIPT_ENTRY,
    first_message_hint,
)
from models import ChatMessage, ChatSession, Project, User
from services.core.auth_service import hash_password

pytestmark = pytest.mark.usefixtures("writing_prompt_configs", "shell_engine")

IDEA = "一个关于外卖员的故事"


def _author_part(state: dict) -> str:
    """模型收到的本轮 user 消息里作者那一段：系统附上的本轮上下文（有的话）在标签之前。"""
    turn_context, label, author_part = state["user_message"].rpartition(WORKING_SET_USER_MESSAGE_LABEL)
    if not label:
        return author_part
    assert turn_context.startswith(TURN_CONTEXT_OPEN)
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

    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_workflow):
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
    model_user_turn = _author_part(state)
    assert model_user_turn.startswith(IDEA)
    assert hint in model_user_turn
    assert "短篇小说" in hint
    assert state["router_message"] == IDEA

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
    assert _author_part(with_other_metadata) == IDEA

    without_metadata = await _run(agent_service, db_session, user, project, metadata=None)
    assert _author_part(without_metadata) == IDEA


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [STARTER_EXAMPLE_ENTRY, STARTER_MANUSCRIPT_ENTRY, FIRST_MESSAGE_ENTRY])
async def test_starter_and_typed_first_message_get_the_same_hint(
    agent_service, db_session, short_project, entry
):
    user, project = short_project

    state = await _run(agent_service, db_session, user, project, metadata={"entry": entry})

    assert first_message_hint("short", force_en=False) in _author_part(state)


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
        # 新开对话时旧对话归档（同一作品只有一个 active 会话）。
        earlier.is_active = False
        db_session.add(earlier)
        db_session.commit()
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

    assert _author_part(state) == IDEA


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

    model_user_turn = _author_part(state)
    assert first_message_hint("short", force_en=True) in model_user_turn
    assert first_message_hint("short", force_en=False) not in model_user_turn
    assert "Short Story" in model_user_turn


def test_unknown_project_type_falls_back_to_generic_label():
    assert "「作品」" in first_message_hint(None, force_en=False)
    assert "screenplay" not in first_message_hint("screenplay", force_en=True)
    assert "短剧剧本" in first_message_hint("screenplay", force_en=False)


@pytest.mark.asyncio
async def test_tools_see_the_author_words_without_the_hidden_hint(
    agent_service, db_session, short_project
):
    """update_project 核对「作者是否要求改名」时，看的是作者原话，不是拼了提示的模型输入。"""
    from agent.core.workflow_events import StreamEvent, StreamEventType

    user, project = short_project
    seen: dict = {}

    def fake_workflow(state, **_kwargs):
        req_ctx = state.get("request_context")
        seen["author_message"] = req_ctx.author_message if req_ctx is not None else None

        async def stream():
            yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

        return stream()

    with patch("agent.service.run_loop_agent_streaming", side_effect=fake_workflow):
        async for _event in agent_service.process_stream(
            project_id=str(project.id),
            user_id=str(user.id),
            message=IDEA,
            session=db_session,
            metadata={"entry": DASHBOARD_IDEA_ENTRY},
        ):
            pass

    assert seen["author_message"] == IDEA


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
    monkeypatch.setattr(service_module, "should_offload_session_work", lambda _session: True)

    assert await agent_service._is_first_ai_conversation(db_session, project.id) is True


@pytest.mark.asyncio
async def test_first_conversation_check_failure_means_no_hint(agent_service, db_session, short_project, monkeypatch):
    """查不到时按「不是第一条」处理：宁可不加提示。"""
    import agent.service as service_module

    _user, project = short_project

    def broken(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(service_module, "_project_has_ai_reply", broken)

    assert await agent_service._is_first_ai_conversation(db_session, project.id) is False
