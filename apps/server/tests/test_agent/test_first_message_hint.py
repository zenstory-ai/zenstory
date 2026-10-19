"""作者在一部作品里的第一条消息：模型收到「先写再给方向」的隐藏提示，落库和界面仍是作者原话。"""

from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, select

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
    model_user_turn = state["messages"][-1]["content"]
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
    assert with_other_metadata["messages"][-1]["content"] == IDEA

    without_metadata = await _run(agent_service, db_session, user, project, metadata=None)
    assert without_metadata["messages"][-1]["content"] == IDEA
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

    assert state["messages"][-1]["content"] == IDEA
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

    model_user_turn = state["messages"][-1]["content"]
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
