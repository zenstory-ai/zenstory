"""Completion hydration can fetch its exact message without downloading a window."""

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from services.auth import get_current_active_user
from sqlalchemy import create_engine
from sqlmodel import Session, SQLModel

from api.chat import router
from database import get_session
from models import ChatMessage, ChatSession, Project, User

pytestmark = pytest.mark.unit


@pytest.fixture
def history_client(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'targeted-history.db'}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all([
            User(id="owner", username="owner", email="owner@example.invalid", hashed_password="unused"),
            User(id="other", username="other", email="other@example.invalid", hashed_password="unused"),
        ])
        session.flush()
        session.add_all([
            Project(id="project", name="Owned", owner_id="owner"),
            Project(id="other-project", name="Other", owner_id="other"),
        ])
        session.flush()
        session.add_all([
            ChatSession(id="active", user_id="owner", project_id="project"),
            ChatSession(id="archived", user_id="owner", project_id="project", is_active=False),
            ChatSession(id="other-chat", user_id="other", project_id="other-project"),
        ])
        session.flush()
        for i in range(55):
            session.add(ChatMessage(
                id=f"message-{i:02}", session_id="active", role="assistant", content=f"正文 {i}",
                tool_calls='[{"name":"query_files"}]', message_metadata='{"feedback":{"vote":"up"}}',
                created_at=datetime(2026, 1, 1) + timedelta(seconds=i),
            ))
        for chat in ["archived", "other-chat"]:
            session.add(ChatMessage(id=f"{chat}-message", session_id=chat, role="assistant", content=chat))
        session.commit()
        user = session.get(User, "owner")

    def read_session():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = read_session
    app.dependency_overrides[get_current_active_user] = lambda: user
    try:
        with TestClient(app) as client:
            yield client
    finally:
        engine.dispose()


def test_target_filter_precedes_recent_limit_and_preserves_message_payload(history_client):
    response = history_client.get(
        "/api/v1/chat/session/project/recent", params={"limit": 50, "message_id": "message-00"},
    )
    assert response.status_code == 200
    messages = response.json()
    assert len(messages) == 1
    assert messages[0] == {
        "id": "message-00", "session_id": "active", "role": "assistant", "content": "正文 0",
        "tool_calls": '[{"name":"query_files"}]', "metadata": '{"feedback":{"vote":"up"}}',
        "created_at": "2026-01-01T00:00:00",
    }


@pytest.mark.parametrize("message_id", ["missing", "archived-message", "other-chat-message"])
def test_target_does_not_fall_back_to_other_session_or_unrelated_messages(history_client, message_id):
    response = history_client.get(
        "/api/v1/chat/session/project/recent", params={"message_id": message_id},
    )
    assert response.status_code == 200
    assert response.json() == []


def test_target_does_not_bypass_project_permission(history_client):
    response = history_client.get(
        "/api/v1/chat/session/other-project/recent", params={"message_id": "other-chat-message"},
    )
    assert response.status_code == 403


def test_unfiltered_recent_history_keeps_existing_window_and_chronology(history_client):
    response = history_client.get("/api/v1/chat/session/project/recent", params={"limit": 2})
    assert response.status_code == 200
    assert [message["id"] for message in response.json()] == ["message-53", "message-54"]


@pytest.mark.parametrize("message_id", ["", "x" * 129])
def test_target_id_has_bounded_protocol_length(history_client, message_id):
    response = history_client.get(
        "/api/v1/chat/session/project/recent", params={"message_id": message_id},
    )
    assert response.status_code == 422
