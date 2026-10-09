"""工作台首条想法：模型收到隐藏提示，落库和界面仍是作者原话。"""

from unittest.mock import MagicMock, patch

import pytest
from sqlmodel import Session, select

from agent.service import DASHBOARD_IDEA_ENTRY, dashboard_kickoff_hint
from models import ChatMessage, Project, User
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


async def _run(service, db_session, user, project, *, metadata, language=None):
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
        ):
            pass
    return captured


@pytest.mark.asyncio
async def test_dashboard_idea_adds_hint_for_model_only(agent_service, db_session, short_project):
    user, project = short_project

    state = await _run(
        agent_service, db_session, user, project, metadata={"entry": DASHBOARD_IDEA_ENTRY}
    )

    hint = dashboard_kickoff_hint("short", force_en=False)
    model_user_turn = state["messages"][-1]["content"]
    assert model_user_turn.startswith(IDEA)
    assert hint in model_user_turn
    assert "短篇小说" in hint
    # 路由器只看作者原话，不受隐藏提示影响
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
    assert with_other_metadata["messages"][-1]["content"] == IDEA

    without_metadata = await _run(agent_service, db_session, user, project, metadata=None)
    assert without_metadata["messages"][-1]["content"] == IDEA


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
    assert dashboard_kickoff_hint("short", force_en=True) in model_user_turn
    assert dashboard_kickoff_hint("short", force_en=False) not in model_user_turn
    assert "Short Story" in model_user_turn


def test_unknown_project_type_falls_back_to_generic_label():
    assert "「作品」" in dashboard_kickoff_hint(None, force_en=False)
    assert "screenplay" not in dashboard_kickoff_hint("screenplay", force_en=True)
    assert "短剧剧本" in dashboard_kickoff_hint("screenplay", force_en=False)


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
