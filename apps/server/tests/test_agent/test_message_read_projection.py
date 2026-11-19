"""Auxiliary history readers must not fetch message payloads they never use."""

import json
from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel

from agent.suggest_service import SuggestService
from models import ChatMessage, ChatSession, Project, User

pytestmark = pytest.mark.unit


@pytest.fixture
def message_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'messages.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            User(id="owner", username="owner", email="owner@example.invalid", hashed_password="unused"),
            User(id="other", username="other", email="other@example.invalid", hashed_password="unused"),
        ])
        session.flush()
        session.add(Project(id="project", name="Projection", owner_id="owner"))
        session.flush()
        session.add_all([
            ChatSession(id="active", user_id="owner", project_id="project"),
            ChatSession(id="archived", user_id="owner", project_id="project", is_active=False),
        ])
        session.commit()
    try:
        yield engine
    finally:
        engine.dispose()


@contextmanager
def message_result_columns(engine):
    """Observe actual DB result columns without retaining payload values."""
    columns = []

    def collect(_connection, cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith("SELECT") and "chat_message" in statement:
            columns.append({description[0] for description in cursor.description})

    event.listen(engine, "after_cursor_execute", collect)
    try:
        yield columns
    finally:
        event.remove(engine, "after_cursor_execute", collect)


def add_message(session, message_id, *, role="assistant", tool_calls=None, chat="active", at=None):
    session.add(ChatMessage(
        id=message_id,
        session_id=chat,
        role=role,
        content=f"正文 {message_id}",
        reasoning_content="思考" * 32_768,
        message_metadata=json.dumps({"diagnostic": "x" * 65_536}),
        tool_call_id="unused-tool-id",
        tool_calls=tool_calls,
        created_at=at or datetime(2026, 1, 1),
    ))


def test_suggestions_keep_latest_window_order_and_role_filter_without_hidden_payloads(message_engine):
    at = datetime(2026, 1, 1)
    with Session(message_engine) as session:
        for index in range(8):
            role = "system" if index == 5 else "user" if index % 2 == 0 else "assistant"
            add_message(session, f"msg-{index}", role=role,
                        tool_calls=json.dumps([{"result": "工具结果" * 32_768}]), at=at)
        add_message(session, "archived-message", chat="archived", at=at + timedelta(days=1))
        session.commit()

    # No constructor/model client is needed for this database-only reader.
    service = SuggestService.__new__(SuggestService)
    with message_result_columns(message_engine) as columns, Session(message_engine) as session:
        actual = service._get_recent_chat_history(session, "project", "owner")

    # The limit applies before role filtering; equal timestamps still use the ID tiebreaker.
    assert actual == [
        {"role": "assistant", "content": "正文 msg-3"},
        {"role": "user", "content": "正文 msg-4"},
        {"role": "user", "content": "正文 msg-6"},
        {"role": "assistant", "content": "正文 msg-7"},
    ]
    assert len(columns) == 1
    assert not columns[0] & {"reasoning_content", "tool_calls", "message_metadata", "tool_call_id"}

    with message_result_columns(message_engine) as denied_columns, Session(message_engine) as session:
        assert service._get_recent_chat_history(session, "project", "other") == []
    assert denied_columns == []
