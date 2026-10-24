"""Author delivery inputs and synthetic artifacts; no model-quality claims."""

import json
from unittest.mock import AsyncMock, patch

import pytest

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.nodes import run_streaming_agent
from agent.tools.mcp_tools import ToolContext
from models import ChatMessage, ChatSession, File, Project, User


@pytest.fixture
def delivery_project(db_session, monkeypatch):
    from services.features.activation_event_service import activation_event_service
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **_kw: None)
    user = User(email="delivery@example.com", username="delivery", hashed_password="synthetic")
    db_session.add(user)
    db_session.commit()
    project = Project(name="雾港来信", owner_id=user.id,
                      writing_style="自然口语，少形容词，第三人称，结尾不解释道理")
    db_session.add(project)
    db_session.commit()
    chat = ChatSession(user_id=user.id, project_id=project.id)
    db_session.add(chat)
    db_session.commit()
    db_session.add(ChatMessage(session_id=chat.id, role="user", content="以后每章不少于1800字"))
    db_session.commit()
    steering = []
    ToolContext.set_context(db_session, user.id, project.id, chat.id,
                           author_message="续写第三章", author_steering=steering)
    try:
        yield project, chat, steering
    finally:
        ToolContext.clear_context()


async def _run_delivery(state, fake_runner):
    from agent.graph.writing_graph import run_writing_workflow_streaming
    route = AsyncMock(return_value={"current_agent": "writer", "workflow_plan": "quick",
        "workflow_agents": [], "routing_metadata": {"agent_type": "writer", "scope": "只写第三章"}})
    with (
        patch("agent.graph.writing_graph.router_node", route),
        patch("agent.graph.writing_graph.run_streaming_agent", new=run_streaming_agent),
        patch("agent.graph.nodes.run_openai_agents_streaming_agent", new=fake_runner),
        patch("agent.tools.mcp_tools.ToolContext.refresh_file_inventory", return_value={}),
        patch("agent.graph.writing_graph._auto_finalize_task_board_on_completion", AsyncMock(return_value=[])),
    ):
        return [event async for event in run_writing_workflow_streaming(state=state, thread_id="delivery")]


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("latest", ["续写第三章", "续写第三章，这章改为1200字左右", "续写第三章，这一章不用写满2000字"])
async def test_first_writer_receives_history_and_latest_length_before_writing(delivery_project, latest):
    from agent.graph import writing_graph
    context = ToolContext._get_context()
    context["author_message"] = latest
    captured = []
    calls = []
    collect = writing_graph._collect_author_requirement_sources

    def record_sources():
        calls.append(True)
        return collect()

    async def fake_runner(state, agent_type, system_prompt, **kwargs):
        captured.append((agent_type, kwargs.get("agent_directive", ""), state.get("user_message")))
        if agent_type == "writer":
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "本轮稿件待核对。"})
            yield StreamEvent(type=StreamEventType.HANDOFF,
                              data={"target_agent": "quality_reviewer", "context": "检查本轮稿件"})
        else:
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "本轮尚无已保存正文，不报告完成。[TASK_COMPLETE]"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    with patch.object(writing_graph, "_collect_author_requirement_sources", new=record_sources):
        await _run_delivery({"user_message": latest, "router_message": latest, "messages": [],
                             "system_prompt": "项目风格：自然口语，少形容词。"}, fake_runner)
    writer = next(prompt for role, prompt, _ in captured if role == "writer")
    assert "[作者要求来源摘录（可能包含无关内容）]" in writer
    assert "每章不少于1800字" in writer
    assert "作者在本会话说过" in writer
    if "1200" in latest:
        assert writer.index("1800") < writer.index("1200")
        assert "作者最新要求为准" in writer
        assert "作者本轮原话" in writer
    if "不用写满" in latest:
        assert latest in writer
        assert "硬下限" not in writer
    reviewer_input = next(message for role, _, message in captured if role == "quality_reviewer")
    assert "每章不少于1800字" in reviewer_input
    if "1200" in latest:
        assert "1200" in reviewer_input
    assert len(calls) == 1  # reviewer reuses the same request snapshot


@pytest.mark.asyncio
@pytest.mark.unit
async def test_new_author_requirement_refreshes_before_following_writer(delivery_project):
    _, _, steering = delivery_project
    captured = []

    async def fake_runner(state, agent_type, system_prompt, **kwargs):
        captured.append((agent_type, kwargs.get("agent_directive", "")))
        if len(captured) == 1:
            steering.append("第三章改为1200字左右")
            yield StreamEvent(type=StreamEventType.HANDOFF, data={"target_agent": "planner", "context": "核对新篇幅"})
        elif agent_type == "planner":
            yield StreamEvent(type=StreamEventType.HANDOFF, data={"target_agent": "writer", "context": "按新的1200字要求写第三章"})
        else:
            yield StreamEvent(type=StreamEventType.TEXT, data={"text": "尚未写入，不声称完成。[TASK_COMPLETE]"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    await _run_delivery({"user_message": "续写第三章", "messages": [], "system_prompt": ""}, fake_runner)
    writers = [prompt for role, prompt in captured if role == "writer"]
    assert len(writers) == 2
    assert "1200" not in writers[0]
    assert "1200" in writers[1]
    assert "1800" in writers[1]


@pytest.mark.asyncio
@pytest.mark.unit
async def test_synthetic_local_revision_preserves_other_paragraphs(delivery_project, db_session):
    from agent.tools import mcp_tools
    project, _, _ = delivery_project
    original = "第一段：林禾在旧码头等信。\n\n第二段：她把钥匙交给周岭。\n\n第三段：船还没有靠岸。"
    file = File(project_id=project.id, user_id=project.owner_id, title="第四节", content=original, file_type="draft")
    db_session.add(file)
    db_session.commit()
    ToolContext._get_context()["author_message"] = "只修改《第四节》的第二段，其他内容保持原样"
    await mcp_tools.query_files({"id": file.id})
    result = await mcp_tools.edit_file({"id": file.id, "edits": [{"op": "replace",
        "old": "第二段：她把钥匙交给周岭。", "new": "第二段：她把钥匙收回口袋，摇了摇头。"}]})
    assert json.loads(result["content"][0]["text"])["status"] == "success"
    db_session.expire_all()
    actual = db_session.get(File, file.id).content
    assert actual.split("\n\n")[0] == original.split("\n\n")[0]
    assert actual.split("\n\n")[2] == original.split("\n\n")[2]
    assert "收回口袋" in actual
