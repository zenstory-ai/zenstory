"""旧版非单循环（无 ``engine == "loop"``）历史行的精简纯文本回放测试。"""

import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from agent.loop.history import load_loop_history
from models import ChatMessage, ChatSession, Project, User


@pytest.mark.unit
def test_legacy_non_loop_rows_fallback_to_plain_text_content(db_session):
    """非单循环旧助手行回放为纯文本 row.content，空白旧行跳过，不注入旧多角色图面包屑或返修块。"""
    suffix = uuid4().hex[:8]
    user = User(
        email=f"legacy-plain-{suffix}@example.com",
        username=f"legacy_plain_{suffix}",
        hashed_password="h",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    project = Project(name="旧会话", owner_id=user.id, project_type="novel")
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    chat = ChatSession(user_id=user.id, project_id=project.id)
    db_session.add(chat)
    db_session.commit()
    db_session.refresh(chat)

    t0 = datetime(2026, 4, 1, 10, 0, 0)
    db_session.add_all(
        [
            ChatMessage(session_id=chat.id, role="user", content="写第一章", created_at=t0),
            ChatMessage(
                session_id=chat.id,
                role="assistant",
                content="已经写完第一章。",
                tool_calls=json.dumps([{"id": "c1", "name": "create_file", "status": "success"}]),
                message_metadata=json.dumps({"routing": {"initial_agent": "writer"}}),
                created_at=t0 + timedelta(seconds=1),
            ),
            ChatMessage(session_id=chat.id, role="user", content="继续", created_at=t0 + timedelta(seconds=2)),
            ChatMessage(
                session_id=chat.id,
                role="assistant",
                content="   ",
                message_metadata=json.dumps({"status_cards": [{"type": "workflow_stopped"}]}),
                created_at=t0 + timedelta(seconds=3),
            ),
        ]
    )
    db_session.commit()

    history = load_loop_history(db_session, project_id=project.id, user_id=user.id, chat_session_id=chat.id)
    assert history.items == [
        {"role": "user", "content": "写第一章"},
        {"role": "assistant", "content": "已经写完第一章。"},
        {"role": "user", "content": "继续"},
    ]
