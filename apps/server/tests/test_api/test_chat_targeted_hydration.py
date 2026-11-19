"""Completion hydration can fetch its exact message without downloading a window."""

import json
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


def test_history_omits_the_author_text_a_compaction_keeps_for_the_model(history_client):
    """压缩点里给模型回放的作者原话不随历史发给浏览器；前端要的摘要照常返回。"""
    with next(history_client.app.dependency_overrides[get_session]()) as session:
        session.add(ChatMessage(
            id="compacted", session_id="active", role="assistant", content="第二章写好了。",
            message_metadata=json.dumps({
                "engine": "loop",
                "display_events": [{"type": "history_compacted", "data": {}}],
                "compaction": {
                    "summary": "合成交接摘要",
                    "covers_through_message_id": "message-54",
                    "kept_author_messages": ["很长的作者原话" * 50],
                    "created_at": "2026-01-02T00:00:00",
                },
            }, ensure_ascii=False),
            created_at=datetime(2026, 1, 2),
        ))
        session.commit()

    response = history_client.get(
        "/api/v1/chat/session/project/recent", params={"limit": 50, "message_id": "compacted"},
    )
    assert response.status_code == 200
    metadata = json.loads(response.json()[0]["metadata"])
    assert metadata["compaction"] == {
        "summary": "合成交接摘要",
        "covers_through_message_id": "message-54",
        "created_at": "2026-01-02T00:00:00",
    }
    assert metadata["display_events"] == [{"type": "history_compacted", "data": {}}]


def test_history_reads_durable_undo_state_without_rewriting_chat_json(history_client):
    from agent.tools.file_ops.edit import FileEditor
    from models import File
    from services.file_edit_operations import undo_edit_operation

    with next(history_client.app.dependency_overrides[get_session]()) as session:
        file = File(project_id="project", title="旧章名", content="作者原稿。")
        session.add(file)
        session.commit()
        result = FileEditor(session, "owner").rename_with_undo(file.id, "新章名")
        calls = [{"id": "rename-1", "name": "rename_file", "result": result}]
        raw = json.dumps(calls, ensure_ascii=False)
        session.add(ChatMessage(
            id="recovery-message", session_id="active", role="assistant", content="已改名。",
            tool_calls=raw, created_at=datetime(2026, 1, 3),
        ))
        session.commit()
        undone = undo_edit_operation(
            session, operation_id=result["undo"]["operation_id"], file_id=file.id, user_id="owner",
        )
        assert undone["status"] == "undone"

    response = history_client.get("/api/v1/chat/session/project/recent", params={"message_id": "recovery-message"})
    assert response.status_code == 200
    hydrated = json.loads(response.json()[0]["tool_calls"])
    assert hydrated[0]["result"]["undo"]["undone"] is True
    with next(history_client.app.dependency_overrides[get_session]()) as session:
        assert session.get(ChatMessage, "recovery-message").tool_calls == raw


def test_confirmed_delete_card_reloads_latest_batch_state_without_matching_file_menu_deletes(history_client):
    from datetime import timedelta

    from models import File
    from models.file_deletion_batch import FileDeletionBatch
    from services.file_trash import FileTrashService

    with next(history_client.app.dependency_overrides[get_session]()) as session:
        folder = File(project_id="project", title="废稿卷", file_type="folder")
        session.add(folder)
        session.flush()
        folder_id = folder.id
        session.add(File(project_id="project", parent_id=folder_id, title="废稿一", content="合成正文。"))
        session.commit()
        service = FileTrashService(session, user_id="owner", project_id="project")
        preview = service.preview([folder_id], recursive=True)
        session.add(ChatMessage(
            id="delete-confirmation", session_id="active", role="assistant", content="请确认范围。",
            tool_calls=json.dumps([{
                "id": "delete-1", "name": "delete_file", "result": {
                    "confirmation_required": True, "mutation_applied": False, "preview": preview,
                },
            }], ensure_ascii=False), created_at=datetime(2026, 1, 4),
        ))
        session.commit()
        # A manual sidebar delete (`file_menu`) must never flip an unconfirmed chat preview card.
        manual = service.delete([folder_id], recursive=True, source="file_menu")

    url = "/api/v1/chat/session/project/recent"
    params = {"message_id": "delete-confirmation"}
    unconfirmed = json.loads(history_client.get(url, params=params).json()[0]["tool_calls"])[0]["result"]
    assert unconfirmed["confirmation_required"] is True

    with next(history_client.app.dependency_overrides[get_session]()) as session:
        service = FileTrashService(session, user_id="owner", project_id="project")
        service.restore(manual["batch_id"])
        fresh_preview = service.preview([folder_id], recursive=True)
        msg = session.get(ChatMessage, "delete-confirmation")
        msg.tool_calls = json.dumps([{
            "id": "delete-1", "name": "delete_file", "result": {
                "confirmation_required": True, "mutation_applied": False, "preview": fresh_preview,
            },
        }], ensure_ascii=False)
        session.add(msg)
        session.commit()
        deleted = service.delete(
            [folder_id], recursive=True, source="agent_confirmed", expected_target_hash=fresh_preview["target_hash"],
        )

    response = history_client.get(url, params=params)
    assert response.status_code == 200
    data = json.loads(response.json()[0]["tool_calls"])[0]["result"]
    assert data["confirmation_required"] is False
    assert data["batch_id"] == deleted["batch_id"] and data["count"] == 2
    assert data["restored"] is False

    with next(history_client.app.dependency_overrides[get_session]()) as session:
        service = FileTrashService(session, user_id="owner", project_id="project")
        service.restore(deleted["batch_id"])
    data = json.loads(history_client.get(url, params=params).json()[0]["tool_calls"])[0]["result"]
    assert data["restored"] is True

    # If a later confirmed batch shares the same target_hash, the newest batch wins over the older restored one.
    with next(history_client.app.dependency_overrides[get_session]()) as session:
        old_batch = session.get(FileDeletionBatch, deleted["batch_id"])
        newer_batch = FileDeletionBatch(
            user_id="owner",
            project_id="project",
            root_ids_json=old_batch.root_ids_json,
            items_json=old_batch.items_json,
            target_hash=old_batch.target_hash,
            item_count=old_batch.item_count,
            source="agent_confirmed",
            created_at=old_batch.created_at + timedelta(seconds=10),
            restored_at=None,
        )
        session.add(newer_batch)
        session.commit()
        newer_id = newer_batch.id

    latest = json.loads(history_client.get(url, params=params).json()[0]["tool_calls"])[0]["result"]
    assert latest["batch_id"] == newer_id
    assert latest["restored"] is False
