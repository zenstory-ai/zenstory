"""Metadata-driven continuation: local SDK stub only, no external model calls."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from agent.core.session_loader import SessionLoader
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.graph.nodes import run_streaming_agent
from agent.openai_agents.runner import normalize_messages_for_openai_agents
from tests.test_agent.test_agent_routing_continuity import _run_graph, _state
from tests.test_agent.test_output_truncated_stop import _max_tokens, _reasoning_chunks, _unused_edit
from tests.test_agent.test_tool_failure_breaker import _run_against_local_model


def _previous(reason="output_truncated"):
    return {"role": "assistant", "content": [], "stop_reason": reason,
            "routing": {"last_agent": "writer", "read_only": False, "scope": "只写第三章"}}


@pytest.mark.unit
def test_persisted_stop_metadata_survives_thinking_free_model_replay():
    row = SimpleNamespace(id="a1", role="assistant", content="", tool_calls=None,
                          reasoning_content="private reasoning",
                          message_metadata=json.dumps({"stop_reason": "output_truncated"}))
    loader = SessionLoader(project_id="p", user_id="u")
    history = loader._format_chat_message_for_history(row)
    replay = normalize_messages_for_openai_agents([history])
    assert len(replay) == 1
    assert replay[0]["role"] == "assistant"
    assert "[output_truncated]" in replay[0]["content"]
    assert "private reasoning" not in str(replay)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("reason, expected", [("output_truncated", True),
    ("run_deadline_exceeded", False), ("model_call_budget_exhausted", False),
    ("max_turns_exceeded", False), ("no_progress", False)])
async def test_only_output_truncation_continue_injects_system_guidance(reason, expected):
    captured = []

    async def fake_runner(state, agent_type, system_prompt, **kwargs):
        captured.append(kwargs.get("agent_directive", ""))
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "已完成本轮指定修改。"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    with patch("agent.graph.nodes.run_openai_agents_streaming_agent", new=fake_runner):
        await _run_graph(run_streaming_agent, _state("继续", _previous(reason)))
    assert captured
    assert ("上一步在思考阶段达到输出上限" in captured[0]) is expected
    if expected:
        assert "第一部分" in captured[0]
        assert "剩余" in captured[0]


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("reasons, repeated, count", [
    (["output_truncated"], True, 2),
    (["output_truncated", "output_truncated"], True, 3),
    (["output_truncated", "end_turn"], False, 1),
    (["output_truncated", "run_deadline_exceeded"], False, 1),
])
async def test_repeat_classification_uses_prior_assistant_metadata(monkeypatch, reasons, repeated, count):
    history = []
    for reason in reasons:
        history.extend([_previous(reason), {"role": "user", "content": "继续"}])
    events, requests, _ = await _run_against_local_model(
        monkeypatch, lambda _: _reasoning_chunks(_max_tokens()), _unused_edit,
        state_extra={"messages": history, "user_message": "继续"},
    )
    assert len(requests) == 1
    error = next(e.data for e in events if e.type == StreamEventType.ERROR)
    expected = "ERR_AGENT_OUTPUT_REPEATEDLY_TRUNCATED" if repeated else "ERR_AGENT_OUTPUT_TRUNCATED"
    assert error["code"] == expected
    assert error["output_truncation_count"] == count
    assert error["retryable"] is False
    assert error["refundable"] is True
    if repeated:
        assert "一章" in error["error"]


@pytest.mark.unit
def test_text_that_mentions_truncation_is_not_stop_metadata():
    from agent.graph.router import resume_route_after_exhaustion
    previous = {"role": "assistant", "content": "[output_truncated] reached cap", "stop_reason": "end_turn"}
    assert resume_route_after_exhaustion(_state("继续", previous)) is None


@pytest.mark.unit
def test_other_stop_reasons_do_not_create_replay_text():
    history = [{"role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "thinking", "thinking": "not model history"}]}]
    assert normalize_messages_for_openai_agents(history) == []


@pytest.mark.unit
def test_stop_metadata_persists_and_reloads_without_changing_assistant_body(db_session):
    from agent.core.message_manager import MessageManager
    from models import ChatMessage, ChatSession, Project, User

    user = User(email="truncation@example.com", username="truncation", hashed_password="test")
    db_session.add(user)
    db_session.commit()
    project = Project(name="自造作品", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    chat = ChatSession(user_id=user.id, project_id=project.id)
    db_session.add(chat)
    db_session.commit()
    manager = MessageManager(project_id=project.id, user_id=user.id)
    saved_id = manager._save_messages_with_session(
        db_session, chat.id, "只写第三章", "", reasoning_content="private reasoning",
        assistant_stop_reason="output_truncated",
    )
    saved = db_session.get(ChatMessage, saved_id)
    assert saved.content == ""
    assert json.loads(saved.message_metadata)["stop_reason"] == "output_truncated"
    history = SessionLoader(project_id=project.id, user_id=user.id)._format_chat_message_for_history(saved)
    replay = normalize_messages_for_openai_agents([history])
    assert "[output_truncated]" in replay[0]["content"]
    assert "private reasoning" not in str(replay)
