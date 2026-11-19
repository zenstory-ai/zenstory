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
            expected_metadata.update(status_cards=status_cards, display_events=display_events)
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


def test_blank_content_display_events_do_not_bypass_empty_assistant_guard(
    db_session: Session, saved_chat: tuple[str, str, str]
) -> None:
    """Issue #413 (#2): pure-whitespace content events must not force an empty assistant row."""
    user_id, project_id, chat_id = saved_chat
    manager = MessageManager(project_id=project_id, user_id=user_id)

    assistant_id = manager._save_messages_with_session(
        db_session,
        chat_id,
        "建角色卡",
        "\n\n\n\n\n\n",
        assistant_display_events=[
            {"type": "history_compacted", "data": {}},
            {"type": "content", "content": "\n\n"},
            {"type": "content", "content": "  \r\n\t  "},
        ],
    )

    assert assistant_id is None
    db_session.expire_all()
    rows = db_session.exec(select(ChatMessage).where(ChatMessage.session_id == chat_id)).all()
    assert [(row.role, row.content) for row in rows] == [("user", "建角色卡")]
    assert db_session.get(ChatSession, chat_id).message_count == 1


def test_save_drops_isolated_blank_segments_while_preserving_indent_markdown_and_crlf(
    db_session: Session, saved_chat: tuple[str, str, str]
) -> None:
    """Issue #413 (#2): trim only leading line breaks on complete segments; preserve indentation & CRLF."""
    user_id, project_id, chat_id = saved_chat
    manager = MessageManager(project_id=project_id, user_id=user_id)
    prose_with_code = "    def keep_indent():\r\n        return 1\r\n\r\n　　第二段保留全角缩进与空行。"

    assistant_id = manager._save_messages_with_session(
        db_session,
        chat_id,
        "检查排版保留",
        "\r\n\n\n" + prose_with_code,
        tool_calls=[{"id": "tc-1", "name": "create_file", "arguments": {"title": "甲"}, "status": "success"}],
        assistant_display_events=[
            {"type": "tool_call", "tool_call_index": 0},
            {"type": "content", "content": "\n\n"},
            {"type": "content", "content": "\r\n\n" + prose_with_code},
        ],
    )

    assert assistant_id is not None
    db_session.expire_all()
    saved = db_session.get(ChatMessage, assistant_id)
    assert saved is not None
    assert saved.content == prose_with_code
    metadata = json.loads(saved.message_metadata or "{}")
    assert metadata["display_events"] == [
        {"type": "tool_call", "tool_call_index": 0},
        {"type": "content", "content": prose_with_code},
    ]


def test_internal_runs_single_newline_tab_crlf_and_triple_newlines_stay_verbatim(
    db_session: Session, saved_chat: tuple[str, str, str]
) -> None:
    """Internal whitespace runs must never be rewritten into synthetic paragraph breaks or spaces."""
    from agent.core.message_manager import (
        normalize_assistant_content,
        normalize_assistant_display_events,
    )

    assert normalize_assistant_content("".join(["甲", "\n", "乙"])) == "甲\n乙"
    assert normalize_assistant_content("".join(["甲", "\t", "乙"])) == "甲\t乙"
    assert normalize_assistant_content("".join(["甲", "\r\n", "乙"])) == "甲\r\n乙"
    assert normalize_assistant_content("".join(["甲", "\n\n\n", "乙"])) == "甲\n\n\n乙"
    assert normalize_assistant_content("\t首行Tab缩进\n次行") == "\t首行Tab缩进\n次行"

    user_id, project_id, chat_id = saved_chat
    manager = MessageManager(project_id=project_id, user_id=user_id)
    events = [
        {"type": "content", "content": "甲"},
        {"type": "tool_call", "tool_call_index": 0},
        {"type": "content", "content": "\n乙\r\n\r\n\r\n丙\t丁"},
    ]
    assert normalize_assistant_display_events(events) == events

    assistant_id = manager._save_messages_with_session(
        db_session,
        chat_id,
        "保留内部空白",
        "甲\n乙\r\n\r\n\r\n丙\t丁",
        tool_calls=[{"id": "tc-1", "name": "query_files", "arguments": {}, "status": "success"}],
        assistant_display_events=events,
    )
    assert assistant_id is not None
    db_session.expire_all()
    saved = db_session.get(ChatMessage, assistant_id)
    assert saved is not None
    assert saved.content == "甲\n乙\r\n\r\n\r\n丙\t丁"
    assert json.loads(saved.message_metadata or "{}")["display_events"] == events
