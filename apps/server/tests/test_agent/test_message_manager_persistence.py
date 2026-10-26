"""History saving must not reload an expired assistant payload just to return its ID."""

import json
from datetime import datetime

import pytest
from sqlalchemy import event
from sqlmodel import Session, select

from agent.core.message_manager import MessageManager
from models import ChatMessage, ChatSession, Project, User


@pytest.fixture
def saved_chat(db_session: Session) -> tuple[str, str, str]:
    user = User(username="history-id", email="history-id@example.com", hashed_password="unused")
    db_session.add(user)
    db_session.flush()
    project = Project(name="History ID", owner_id=user.id)
    db_session.add(project)
    db_session.flush()
    chat = ChatSession(project_id=project.id, user_id=user.id)
    db_session.add(chat)
    db_session.commit()
    return user.id, project.id, chat.id


@pytest.mark.parametrize("payload", ["full", "empty", "forced_empty"])
def test_save_returns_committed_id_without_reloading_message(
    db_session: Session, saved_chat: tuple[str, str, str], payload: str
) -> None:
    user_id, project_id, chat_id = saved_chat
    manager = MessageManager(project_id=project_id, user_id=user_id)
    full = payload == "full"
    assistant_text = '正文\n"quoted" 雪😀' if full else ""
    reasoning = "完整思考\n\\keep" if full else None
    tool_calls = [{"id": "tool-1", "name": "query_files", "input": {"title": "雪😀"}}] if full else None
    status_cards = [{"type": "workflow_stopped", "question": "继续？"}] if full else None
    display_events = [{"type": "content", "text": assistant_text}] if full else None
    routing = {"scope": "完整保留", "write_content": False} if full else None
    sent_at = datetime(2026, 10, 17, 1, 2, 3)
    message_selects: list[str] = []

    def capture_select(_connection, _cursor, statement, _parameters, _context, _many):
        normalized = " ".join(statement.lower().split())
        if normalized.startswith("select ") and "from chat_message " in normalized:
            message_selects.append(statement)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", capture_select)
    try:
        # The shared test fixture disables expiration; use the production default.
        with Session(bind) as session:
            assert session.expire_on_commit
            assistant_id = manager._save_messages_with_session(
                session,
                chat_id,
                "作者原话",
                assistant_text,
                tool_calls=tool_calls,
                reasoning_content=reasoning,
                assistant_stop_reason="end_turn",
                assistant_usage={"total_tokens": 3},
                assistant_status_cards=status_cards,
                steering_messages=["作者插话", " ", ""],
                assistant_display_events=display_events,
                assistant_routing=routing,
                persist_empty_assistant=payload == "forced_empty",
                user_created_at=sent_at,
            )
    finally:
        event.remove(bind, "before_cursor_execute", capture_select)

    db_session.expire_all()
    messages = db_session.exec(select(ChatMessage).where(ChatMessage.session_id == chat_id)).all()
    user_messages = [row for row in messages if row.role == "user"]
    assert {row.content for row in user_messages} == {"作者原话", "作者插话"}
    assert next(row for row in user_messages if row.content == "作者原话").created_at == sent_at
    assistant_rows = [row for row in messages if row.role == "assistant"]
    assert len(assistant_rows) == (0 if payload == "empty" else 1)
    if assistant_rows:
        assistant = assistant_rows[0]
        assert assistant_id == assistant.id
        assert assistant.content == assistant_text
        assert assistant.reasoning_content == reasoning
        assert assistant.tool_calls == manager._serialize_tool_calls(tool_calls)
        expected_metadata = {"stop_reason": "end_turn", "usage": {"total_tokens": 3}}
        if full:
            expected_metadata.update(status_cards=status_cards, display_events=display_events, routing=routing)
        # These JSON strings are exposed unchanged by the history API.
        assert assistant.message_metadata == json.dumps(expected_metadata)
    else:
        assert assistant_id is None
    assert db_session.get(ChatSession, chat_id).message_count == len(messages)
    assert not message_selects, "Returning the ID must not SELECT the full committed assistant row"


def test_commit_failure_rolls_back_without_returning_assistant_id(
    db_session: Session, saved_chat: tuple[str, str, str]
) -> None:
    user_id, project_id, chat_id = saved_chat
    manager = MessageManager(project_id=project_id, user_id=user_id)

    def fail_commit(_session):
        raise RuntimeError("synthetic commit failure")

    with Session(db_session.get_bind()) as session:
        event.listen(session, "before_commit", fail_commit)
        try:
            with pytest.raises(RuntimeError, match="synthetic commit failure"):
                manager._save_messages_with_session(session, chat_id, "作者原话", "未提交正文")
        finally:
            event.remove(session, "before_commit", fail_commit)
        assert session.is_active
        assert not session.new
    db_session.expire_all()
    assert not db_session.exec(select(ChatMessage).where(ChatMessage.session_id == chat_id)).all()
    assert db_session.get(ChatSession, chat_id).message_count == 0


def test_save_requires_the_resolved_session_id(db_session: Session, saved_chat: tuple[str, str, str]) -> None:
    """An unresolved round must not silently write into the currently active chat."""
    user_id, project_id, chat_id = saved_chat
    manager = MessageManager(project_id=project_id, user_id=user_id)
    with pytest.raises(ValueError, match="session_id is required"):
        manager._save_messages_with_session(db_session, None, "作者原话", "不能串进其他会话")
    assert not db_session.exec(select(ChatMessage).where(ChatMessage.session_id == chat_id)).all()
    assert db_session.get(ChatSession, chat_id).message_count == 0
