"""Metadata-driven continuation: local SDK stub only, no external model calls."""

import json
from types import SimpleNamespace

import pytest

from agent.core.message_manager import MessageManager
from agent.core.session_loader import SessionLoader
from agent.core.workflow_events import StreamEventType
from agent.loop.history import LOOP_ENGINE_METADATA_VALUE, _row_items, load_loop_history
from agent.openai_agents.runner import normalize_messages_for_openai_agents
from models import ChatMessage, ChatSession, Project, User
from tests.test_agent.test_output_truncated_stop import _max_tokens, _reasoning_chunks, _unused_edit
from tests.test_agent.test_tool_failure_breaker import _run_against_local_model


@pytest.mark.unit
def test_persisted_stop_metadata_survives_thinking_free_model_replay():
    row = SimpleNamespace(
        id="a1",
        role="assistant",
        content="",
        tool_calls=None,
        reasoning_content="private reasoning",
        message_metadata=json.dumps({"engine": LOOP_ENGINE_METADATA_VALUE, "stop_reason": "output_truncated"}),
    )
    loader = SessionLoader(project_id="p", user_id="u")
    replay = _row_items(loader, row)
    assert len(replay) == 1
    assert replay[0]["role"] == "assistant"
    assert "输出上限" in replay[0]["content"]
    assert "private reasoning" not in str(replay)


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("reasons, repeated, count", [
    (["output_truncated"], True, 2),
    (["output_truncated", "output_truncated"], True, 3),
    (["output_truncated", "end_turn"], False, 1),
    (["output_truncated", "run_deadline_exceeded"], False, 1),
])
async def test_repeat_classification_uses_prior_assistant_metadata(
    db_session, monkeypatch, reasons, repeated, count
):
    user = User(email=f"trunc-{count}-{repeated}@example.com", username=f"trunc_{count}_{repeated}", hashed_password="x")
    db_session.add(user)
    db_session.commit()
    project = Project(name="自造作品", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    chat = ChatSession(user_id=user.id, project_id=project.id)
    db_session.add(chat)
    db_session.commit()
    manager = MessageManager(project_id=project.id, user_id=user.id)
    for reason in reasons:
        manager._save_messages_with_session(
            db_session,
            chat.id,
            "只写第三章",
            "" if reason == "output_truncated" else "写完了",
            reasoning_content="private reasoning",
            assistant_stop_reason=reason,
        )
    loaded = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    events, requests, _ = await _run_against_local_model(
        monkeypatch,
        lambda _: _reasoning_chunks(_max_tokens()),
        _unused_edit,
        state_extra={
            "history_items": loaded.items,
            "previous_output_truncations": loaded.trailing_output_truncations,
            "user_message": "继续",
        },
    )
    assert len(requests) == 1
    error = next(e.data for e in events if e.type == StreamEventType.ERROR)
    expected = "ERR_AGENT_OUTPUT_REPEATEDLY_TRUNCATED" if repeated else "ERR_AGENT_OUTPUT_TRUNCATED"
    assert error["code"] == expected
    assert error["output_truncation_count"] == count
    assert error["retryable"] is False
    assert error["refundable"] is False
    if repeated:
        assert "一章" in error["error"]


@pytest.mark.unit
def test_other_stop_reasons_do_not_create_replay_text():
    history = [{"role": "assistant", "stop_reason": "end_turn",
                "content": [{"type": "thinking", "thinking": "not model history"}]}]
    assert normalize_messages_for_openai_agents(history) == []


@pytest.mark.unit
def test_stop_metadata_persists_and_reloads_without_changing_assistant_body(db_session):
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
        assistant_extra_metadata={"engine": LOOP_ENGINE_METADATA_VALUE},
    )
    saved = db_session.get(ChatMessage, saved_id)
    assert saved.content == ""
    assert json.loads(saved.message_metadata)["stop_reason"] == "output_truncated"
    loaded = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert loaded.trailing_output_truncations == 1
    assert any(item.get("role") == "assistant" and "输出上限" in str(item.get("content")) for item in loaded.items)
    assert "private reasoning" not in str(loaded.items)
