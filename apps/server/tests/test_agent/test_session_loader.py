"""
Tests for SessionLoader history loading and token-budget windowing.
"""

import json
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlmodel import Session, select

from agent.core.session_loader import SessionLoader
from models import ChatMessage, ChatSession, Project, User


@pytest.fixture
def session_loader_test_data(db_session: Session):
    """Create user/project/chat session for session loader tests."""
    suffix = uuid4().hex[:8]
    user = User(
        email=f"session-loader-{suffix}@example.com",
        username=f"session_loader_{suffix}",
        hashed_password="hashed",
        name="Session Loader Test User",
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    project = Project(
        name=f"Session Loader Project {suffix}",
        owner_id=user.id,
        project_type="novel",
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)

    chat_session = ChatSession(
        user_id=user.id,
        project_id=project.id,
        title="Session Loader Test Chat",
        is_active=True,
        message_count=0,
    )
    db_session.add(chat_session)
    db_session.commit()
    db_session.refresh(chat_session)

    return {
        "user": user,
        "project": project,
        "chat_session": chat_session,
    }


def _add_chat_messages(
    db_session: Session,
    chat_session_id: str,
    messages: list[dict[str, str | None]],
) -> None:
    """Insert ordered chat messages with explicit timestamps."""
    base_time = datetime.utcnow()
    for index, message in enumerate(messages):
        db_session.add(
            ChatMessage(
                session_id=chat_session_id,
                role=message["role"] or "user",
                content=message["content"] or "",
                reasoning_content=message.get("reasoning_content"),
                created_at=base_time + timedelta(seconds=index),
            )
        )
    db_session.commit()


@pytest.mark.unit
class TestSessionLoaderHistoryBudget:
    """Tests for token-budget sliding window when loading chat history."""

    def test_load_chat_session_keeps_all_messages_within_budget(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """Should keep full history when total tokens are below budget."""
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 20)
        _add_chat_messages(
            db_session,
            session_loader_test_data["chat_session"].id,
            [
                {"role": "user", "content": "u" * 12},
                {"role": "assistant", "content": "a" * 12},
                {"role": "user", "content": "v" * 12},
            ],
        )

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        session_data = loader.load_chat_session(db_session)

        assert [msg["content"] for msg in session_data.history_messages] == [
            "u" * 12,
            "a" * 12,
            "v" * 12,
        ]

    def test_load_chat_session_truncates_oldest_messages_when_budget_exceeded(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """Should keep newest messages and preserve chronological order after truncation."""
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 40)
        _add_chat_messages(
            db_session,
            session_loader_test_data["chat_session"].id,
            [
                {"role": "user", "content": "m1" * 8},
                {"role": "assistant", "content": "m2" * 8},
                {"role": "user", "content": "m3" * 8},
                {
                    "role": "assistant",
                    "content": "m4" * 8,
                    "reasoning_content": "kept reasoning",
                },
            ],
        )

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        session_data = loader.load_chat_session(db_session)

        assert len(session_data.history_messages) == 2
        assert session_data.history_messages[0]["content"] == "m3" * 8

        # Assistant with reasoning_content is formatted as structured content blocks
        assistant_content = session_data.history_messages[1]["content"]
        assert isinstance(assistant_content, list)
        assert assistant_content[0] == {"type": "thinking", "thinking": "kept reasoning"}
        assert assistant_content[1] == {"type": "text", "text": "m4" * 8}

    def test_history_window_budget_ignores_ui_only_reasoning_content(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """Reasoning is dropped before replay, so it must not eat the history window."""
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 60)
        heavy_reasoning = "推理" * 800  # 单条即远超整个预算
        _add_chat_messages(
            db_session,
            session_loader_test_data["chat_session"].id,
            [
                {"role": "user", "content": "写下一章"},
                {"role": "assistant", "content": "好的", "reasoning_content": heavy_reasoning},
                {"role": "user", "content": "继续"},
                {"role": "assistant", "content": "收到", "reasoning_content": heavy_reasoning},
            ],
        )

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        session_data = loader.load_chat_session(db_session)

        # 正文 token 很小，四条消息都应留在窗口内
        assert len(session_data.history_messages) == 4
        assert session_data.history_messages[0]["content"] == "写下一章"

        # thinking 块仍保留在内容里供 UI/历史使用
        last_content = session_data.history_messages[-1]["content"]
        assert isinstance(last_content, list)
        assert last_content[0] == {"type": "thinking", "thinking": heavy_reasoning}
        assert last_content[1] == {"type": "text", "text": "收到"}

    def test_load_chat_session_returns_empty_history_for_no_messages(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """Should return empty history when session has no chat messages."""
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 8)

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        session_data = loader.load_chat_session(db_session)

        assert session_data.history_messages == []

    def test_load_chat_session_keeps_single_message_with_zero_budget(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """Should keep the newest single message even if budget is non-positive."""
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 0)
        _add_chat_messages(
            db_session,
            session_loader_test_data["chat_session"].id,
            [
                {"role": "user", "content": "single message content"},
            ],
        )

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        session_data = loader.load_chat_session(db_session)

        assert [msg["content"] for msg in session_data.history_messages] == ["single message content"]

    def test_load_chat_session_includes_usage_and_stop_reason_from_metadata(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """Assistant metadata should be hydrated into history messages for token math."""
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 100)
        chat_session_id = session_loader_test_data["chat_session"].id

        db_session.add(
            ChatMessage(
                session_id=chat_session_id,
                role="user",
                content="hello",
            )
        )
        db_session.add(
            ChatMessage(
                session_id=chat_session_id,
                role="assistant",
                content="world",
                message_metadata=json.dumps({
                    "stop_reason": "end_turn",
                    "usage": {
                        "input_tokens": 321,
                        "output_tokens": 123,
                        "total_tokens": 444,
                    },
                }),
            )
        )
        db_session.commit()

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        session_data = loader.load_chat_session(db_session)

        assistant_msg = session_data.history_messages[-1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["stop_reason"] == "end_turn"
        assert assistant_msg["usage"]["total_tokens"] == 444

    def test_load_chat_session_synthesizes_status_only_assistant_turns_into_history_content(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """Status-only assistant turns should remain visible in model history after reload."""
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 100)
        chat_session_id = session_loader_test_data["chat_session"].id

        db_session.add(
            ChatMessage(
                session_id=chat_session_id,
                role="user",
                content="继续",
            )
        )
        db_session.add(
            ChatMessage(
                session_id=chat_session_id,
                role="assistant",
                content="",
                message_metadata=json.dumps({
                    "status_cards": [
                        {
                            "type": "workflow_stopped",
                            "reason": "clarification_needed",
                            "question": "请确认主角姓名",
                            "context": "姓名会影响后续称呼",
                            "details": ["主角姓名", "时代背景"],
                        }
                    ],
                }),
            )
        )
        db_session.commit()

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        session_data = loader.load_chat_session(db_session)

        assistant_msg = session_data.history_messages[-1]
        assert assistant_msg["role"] == "assistant"
        assert assistant_msg["content"]
        assert "clarification_needed" in assistant_msg["content"]
        assert "请确认主角姓名" in assistant_msg["content"]
        assert assistant_msg["clarification_pending"] is True
        assert assistant_msg["pending_clarification"] == {
            "question": "请确认主角姓名",
            "context": "姓名会影响后续称呼",
            "details": ["主角姓名", "时代背景"],
        }

    def test_load_chat_session_prefers_latest_active_and_deactivates_stale(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
        legacy_db_without_active_chat_index,
    ):
        """Should deterministically keep latest active session and deactivate stale ones."""
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 100)
        user = session_loader_test_data["user"]
        project = session_loader_test_data["project"]
        original_session = session_loader_test_data["chat_session"]

        original_session.updated_at = datetime.utcnow() - timedelta(hours=2)
        db_session.add(original_session)

        newer_session = ChatSession(
            user_id=user.id,
            project_id=project.id,
            title="Newest session",
            is_active=True,
            message_count=0,
            created_at=datetime.utcnow() - timedelta(minutes=30),
            updated_at=datetime.utcnow(),
        )
        older_session = ChatSession(
            user_id=user.id,
            project_id=project.id,
            title="Older stale session",
            is_active=True,
            message_count=0,
            created_at=datetime.utcnow() - timedelta(hours=1),
            updated_at=datetime.utcnow() - timedelta(hours=1),
        )
        db_session.add(newer_session)
        db_session.add(older_session)
        db_session.commit()
        db_session.refresh(newer_session)
        db_session.refresh(older_session)

        _add_chat_messages(
            db_session,
            newer_session.id,
            [
                {"role": "user", "content": "newer-user"},
                {"role": "assistant", "content": "newer-assistant"},
            ],
        )
        _add_chat_messages(
            db_session,
            older_session.id,
            [
                {"role": "user", "content": "older-user"},
            ],
        )

        loader = SessionLoader(project_id=project.id, user_id=user.id)
        session_data = loader.load_chat_session(db_session)

        assert session_data.session_id == newer_session.id
        assert [msg["content"] for msg in session_data.history_messages] == [
            "newer-user",
            "newer-assistant",
        ]

        refreshed = db_session.exec(
            select(ChatSession).where(
                ChatSession.project_id == project.id,
                ChatSession.user_id == user.id,
            )
        ).all()
        active_sessions = [row for row in refreshed if row.is_active]
        assert len(active_sessions) == 1
        assert active_sessions[0].id == newer_session.id

    def test_load_chat_session_by_id_reads_that_session_even_if_inactive(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        """按本轮已解析的 id 读：另一个分页刚把它切成非活跃，也不改读最新活跃会话。"""
        user = session_loader_test_data["user"]
        project = session_loader_test_data["project"]
        resolved = session_loader_test_data["chat_session"]
        resolved.is_active = False
        db_session.add(resolved)
        other_tab = ChatSession(
            user_id=user.id,
            project_id=project.id,
            title="Other tab",
            is_active=True,
            message_count=0,
        )
        db_session.add(other_tab)
        db_session.commit()
        db_session.refresh(other_tab)
        _add_chat_messages(db_session, resolved.id, [{"role": "user", "content": "resolved-user"}])
        _add_chat_messages(db_session, other_tab.id, [{"role": "user", "content": "other-tab-user"}])

        loader = SessionLoader(project_id=project.id, user_id=user.id)
        session_data = loader.load_chat_session(db_session, chat_session_id=resolved.id)

        assert session_data.session_id == resolved.id
        assert [msg["content"] for msg in session_data.history_messages] == ["resolved-user"]
        # 按 id 读不修改活跃状态
        db_session.refresh(other_tab)
        db_session.refresh(resolved)
        assert other_tab.is_active is True
        assert resolved.is_active is False

    def test_load_chat_session_by_id_rejects_session_of_other_user_or_project(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        """越权 id：别人的会话、或自己在另一个项目的会话，都不能被读出来。"""
        user = session_loader_test_data["user"]
        project = session_loader_test_data["project"]

        suffix = uuid4().hex[:8]
        stranger = User(
            email=f"session-loader-stranger-{suffix}@example.com",
            username=f"session_loader_stranger_{suffix}",
            hashed_password="hashed",
            email_verified=True,
            is_active=True,
        )
        db_session.add(stranger)
        db_session.commit()
        other_project = Project(name=f"Other {suffix}", owner_id=user.id, project_type="novel")
        db_session.add(other_project)
        db_session.commit()
        foreign_sessions = [
            ChatSession(user_id=stranger.id, project_id=project.id, title="stranger", is_active=True),
            ChatSession(user_id=user.id, project_id=other_project.id, title="other project", is_active=True),
        ]
        for foreign in foreign_sessions:
            db_session.add(foreign)
            db_session.commit()
            db_session.refresh(foreign)
            _add_chat_messages(db_session, foreign.id, [{"role": "user", "content": "foreign-secret"}])

        loader = SessionLoader(project_id=project.id, user_id=user.id)
        for foreign in foreign_sessions:
            session_data = loader.load_chat_session(db_session, chat_session_id=foreign.id)
            assert session_data.chat_session is None
            assert session_data.session_id is None
            assert session_data.history_messages == []
            assert session_data.working_set_refs == []

        missing = loader.load_chat_session(db_session, chat_session_id="no-such-session")
        assert missing.chat_session is None
        assert missing.history_messages == []

    def test_load_chat_session_handles_long_history_over_100_turns(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """
        Long sessions (100+ turns) should still return a stable, chronological
        sliding window under token budget.
        """
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 70)
        chat_session_id = session_loader_test_data["chat_session"].id

        base_time = datetime.utcnow()
        for i in range(120):
            role = "user" if i % 2 == 0 else "assistant"
            content = f"turn-{i:03d}-" + ("x" * 32)
            db_session.add(
                ChatMessage(
                    session_id=chat_session_id,
                    role=role,
                    content=content,
                    created_at=base_time + timedelta(seconds=i),
                )
            )
        db_session.commit()

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        session_data = loader.load_chat_session(db_session)

        contents = [msg["content"] for msg in session_data.history_messages]
        assert len(contents) > 0
        assert len(contents) < 120  # sliding window trimmed old history
        assert contents[-1].startswith("turn-119-")  # newest turn preserved
        # remaining window should stay chronological
        turn_indexes = [int(text.split("-")[1]) for text in contents]
        assert turn_indexes == sorted(turn_indexes)

    def test_tool_call_turn_replays_as_text_breadcrumb(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """A prior tool-call turn should replay as a plain-text breadcrumb naming the file id."""
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 500)
        chat_session_id = session_loader_test_data["chat_session"].id

        db_session.add(
            ChatMessage(
                session_id=chat_session_id,
                role="user",
                content="帮我创建第一章大纲",
            )
        )
        db_session.add(
            ChatMessage(
                session_id=chat_session_id,
                role="assistant",
                content="好的，已经为你建立大纲。",
                tool_calls=json.dumps(
                    [
                        {
                            "id": "call_1",
                            "name": "create_file",
                            "arguments": {"title": "第一章大纲", "file_type": "outline"},
                            "status": "success",
                            "result": {"id": "file-abc-123", "title": "第一章大纲"},
                            "error": None,
                        }
                    ]
                ),
            )
        )
        db_session.commit()

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        session_data = loader.load_chat_session(db_session)

        assistant_msg = session_data.history_messages[-1]
        assert assistant_msg["role"] == "assistant"

        content = assistant_msg["content"]
        content_text = (
            content
            if isinstance(content, str)
            else "\n".join(
                block.get("text", "")
                for block in content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        )

        # Breadcrumb is plain TEXT and names the created file by id.
        assert "已创建文件" in content_text
        assert "file-abc-123" in content_text
        assert "第一章大纲" in content_text
        # The original prose reply is preserved alongside the breadcrumb.
        assert "已经为你建立大纲" in content_text

    def test_tool_call_breadcrumb_emits_no_raw_tool_blocks(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """Replayed history must never contain raw tool_use/tool_result blocks or orphaned tool_call_id."""
        from agent.openai_agents.runner import normalize_messages_for_openai_agents

        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 500)
        chat_session_id = session_loader_test_data["chat_session"].id

        db_session.add(
            ChatMessage(
                session_id=chat_session_id,
                role="user",
                content="把第二章删掉",
            )
        )
        db_session.add(
            ChatMessage(
                session_id=chat_session_id,
                role="assistant",
                content="",
                tool_calls=json.dumps(
                    [
                        {
                            "id": "call_del_1",
                            "name": "delete_file",
                            "arguments": {"id": "file-del-9"},
                            "status": "success",
                            "result": {"id": "file-del-9", "title": "第二章"},
                            "error": None,
                        }
                    ]
                ),
            )
        )
        db_session.commit()

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        session_data = loader.load_chat_session(db_session)

        # Even with empty prose, the breadcrumb keeps the turn visible.
        assistant_msg = session_data.history_messages[-1]
        breadcrumb_content = assistant_msg["content"]
        breadcrumb_text = (
            breadcrumb_content
            if isinstance(breadcrumb_content, str)
            else json.dumps(breadcrumb_content, ensure_ascii=False)
        )
        assert "已删除文件" in breadcrumb_text
        assert "file-del-9" in breadcrumb_text

        # Normalizing for the SDK must produce ONLY plain user/assistant text —
        # no tool_use / tool_result blocks and therefore no orphaned tool_call_id.
        normalized = normalize_messages_for_openai_agents(session_data.history_messages)
        for message in normalized:
            assert set(message.keys()) == {"role", "content"}
            assert message["role"] in {"user", "assistant"}
            assert isinstance(message["content"], str)
            assert "tool_use" not in message["content"]
            assert "tool_result" not in message["content"]
            assert "tool_call_id" not in message["content"]
            assert "call_del_1" not in message["content"]

    def test_failed_tool_call_turn_emits_no_breadcrumb(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """A failed tool call should not synthesize a misleading 'created file' breadcrumb."""
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 500)
        chat_session_id = session_loader_test_data["chat_session"].id

        db_session.add(
            ChatMessage(
                session_id=chat_session_id,
                role="user",
                content="创建文件",
            )
        )
        db_session.add(
            ChatMessage(
                session_id=chat_session_id,
                role="assistant",
                content="抱歉，创建失败了。",
                tool_calls=json.dumps(
                    [
                        {
                            "id": "call_err",
                            "name": "create_file",
                            "arguments": {"title": "失败文件"},
                            "status": "error",
                            "result": None,
                            "error": "permission denied",
                        }
                    ]
                ),
            )
        )
        db_session.commit()

        loader = SessionLoader(
            project_id=session_loader_test_data["project"].id,
            user_id=session_loader_test_data["user"].id,
        )
        session_data = loader.load_chat_session(db_session)

        assistant_msg = session_data.history_messages[-1]
        assert assistant_msg["content"] == "抱歉，创建失败了。"
        assert "已创建文件" not in str(assistant_msg["content"])

    def test_load_chat_session_allows_superuser_cross_project(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """Superuser should be able to load chat session for projects they do not own."""
        monkeypatch.setattr("agent.core.session_loader.AGENT_CHAT_HISTORY_TOKEN_BUDGET", 100)
        project = session_loader_test_data["project"]
        suffix = uuid4().hex[:8]
        superuser = User(
            email=f"session-loader-admin-{suffix}@example.com",
            username=f"session_loader_admin_{suffix}",
            hashed_password="hashed",
            name="Session Loader Admin",
            email_verified=True,
            is_active=True,
            is_superuser=True,
        )
        db_session.add(superuser)
        db_session.commit()
        db_session.refresh(superuser)

        admin_chat_session = ChatSession(
            user_id=superuser.id,
            project_id=project.id,
            title="Admin session",
            is_active=True,
            message_count=0,
        )
        db_session.add(admin_chat_session)
        db_session.commit()
        db_session.refresh(admin_chat_session)

        _add_chat_messages(
            db_session,
            admin_chat_session.id,
            [
                {"role": "user", "content": "admin-user"},
                {"role": "assistant", "content": "admin-assistant"},
            ],
        )

        loader = SessionLoader(project_id=project.id, user_id=superuser.id)
        session_data = loader.load_chat_session(db_session)

        assert session_data.session_id == admin_chat_session.id
        assert [msg["content"] for msg in session_data.history_messages] == [
            "admin-user",
            "admin-assistant",
        ]


def _add_turn(
    db_session: Session,
    chat_session_id: str,
    *,
    user_text: str,
    tool_calls: list[dict],
    at: datetime,
) -> None:
    """Persist one user + assistant turn with tool_calls in the production shape."""
    db_session.add(
        ChatMessage(session_id=chat_session_id, role="user", content=user_text, created_at=at)
    )
    db_session.add(
        ChatMessage(
            session_id=chat_session_id,
            role="assistant",
            content="好的。",
            tool_calls=json.dumps(tool_calls, ensure_ascii=False),
            created_at=at + timedelta(seconds=1),
        )
    )
    db_session.commit()


def _query_call(file_id: str, title: str) -> dict:
    return {
        "id": f"call_q_{file_id}",
        "name": "query_files",
        "arguments": {"id": file_id},
        "status": "success",
        "result": [{"id": file_id, "title": title, "content": "旧内容"}],
        "error": None,
    }


@pytest.mark.unit
class TestCrossTurnWorkingSet:
    """生产事故回归：下一轮不知道上一轮读过什么，于是从零开始反复 query_files。"""

    def _make_file(self, db_session: Session, project_id: str, title: str, content: str, **kw):
        from models import File

        file = File(project_id=project_id, title=title, content=content, file_type="outline", **kw)
        db_session.add(file)
        db_session.commit()
        db_session.refresh(file)
        return file

    def test_previous_reads_and_writes_are_injected_with_current_content(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        project = session_loader_test_data["project"]
        chat_session_id = session_loader_test_data["chat_session"].id

        volume = self._make_file(db_session, project.id, "卷纲", "卷纲旧版")
        chapter = self._make_file(db_session, project.id, "第一章", "第一章正文")
        character = self._make_file(db_session, project.id, "林小雨", "角色卡全文")
        deleted = self._make_file(db_session, project.id, "废稿", "不该出现", is_deleted=True)

        other_project = Project(name="其他项目", owner_id=session_loader_test_data["user"].id)
        db_session.add(other_project)
        db_session.commit()
        foreign = self._make_file(db_session, other_project.id, "别人的文件", "跨项目内容")

        base = datetime.utcnow() - timedelta(minutes=10)
        # 更早的一轮：超出最近两轮窗口，不应进入工作集
        stale = self._make_file(db_session, project.id, "很久以前读的", "旧轮次内容")
        _add_turn(
            db_session,
            chat_session_id,
            user_text="t0",
            tool_calls=[_query_call(stale.id, stale.title)],
            at=base,
        )
        _add_turn(
            db_session,
            chat_session_id,
            user_text="读一下卷纲",
            tool_calls=[
                _query_call(volume.id, "卷纲"),
                _query_call(volume.id, "卷纲"),  # 同轮重复读取只记一次
                _query_call(deleted.id, "废稿"),
                _query_call(foreign.id, "别人的文件"),
                # 关键词搜索只是预览，不算读过全文
                {
                    "id": "c_s",
                    "name": "query_files",
                    "arguments": {"query": "林"},
                    "status": "success",
                    "result": [],
                },
            ],
            at=base + timedelta(minutes=1),
        )
        _add_turn(
            db_session,
            chat_session_id,
            user_text="改第一章",
            tool_calls=[
                {
                    "id": "c_p",
                    "name": "parallel_execute",
                    "arguments": {
                        "tasks": [
                            {"type": "query_files", "description": "读角色", "params": {"id": character.id}},
                            {"type": "edit_file", "description": "改章", "params": {"id": chapter.id, "edits": []}},
                        ]
                    },
                    "status": "success",
                    "result": {
                        "tasks": [
                            {"index": 0, "params": {"id": character.id}, "type": "query_files", "status": "completed"},
                            {"index": 1, "params": {"id": chapter.id}, "type": "edit_file", "status": "completed"},
                        ]
                    },
                },
            ],
            at=base + timedelta(minutes=2),
        )

        # 上一轮之后文件又被改过：注入的必须是当前库内容
        volume.content = "卷纲最新版\n\n第二段保留换行"
        db_session.add(volume)
        db_session.commit()

        loader = SessionLoader(project_id=project.id, user_id=session_loader_test_data["user"].id)
        result = loader.load_chat_session(db_session)
        loader.attach_working_set(db_session, result)
        text = result.working_set_context

        assert "无需再次 query_files" in text
        for file in (volume, chapter, character):
            assert f"(id={file.id})" in text
        assert "卷纲最新版\n\n第二段保留换行" in text
        assert "卷纲旧版" not in text
        assert "上一轮已修改" in text  # parallel_execute 的 edit_file 子任务
        for excluded in (deleted, foreign, stale):
            assert excluded.id not in text

        # 面包屑同时记下读取过的文件（标题 + id）
        history_text = json.dumps(result.history_messages, ensure_ascii=False)
        assert "已读取文件" in history_text
        assert volume.id in history_text
        # 工作集全文不进历史消息（不受历史窗口裁剪、不改动已缓存的历史前缀）
        assert "卷纲最新版" not in history_text

        # 拼到本轮用户消息之前，用户原话在最后
        user_content = SessionLoader.attach_working_set_to_user_content("继续", text)
        assert user_content.startswith("<previous_turn_working_set>")
        assert user_content.endswith("【本轮用户消息】\n继续")

    def test_unexecuted_or_unknown_deletes_keep_files_in_the_working_set(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        """parallel_execute 截掉的尾部 delete、没有结果的 delete：文件没删，不能从工作集里拿掉。"""
        project = session_loader_test_data["project"]
        chat_session_id = session_loader_test_data["chat_session"].id
        files = [self._make_file(db_session, project.id, f"草稿{i}", f"草稿{i}正文") for i in range(7)]
        pending_target = self._make_file(db_session, project.id, "待删设定", "待删设定正文")

        base = datetime.utcnow() - timedelta(minutes=10)
        _add_turn(
            db_session,
            chat_session_id,
            user_text="读一下这些草稿",
            tool_calls=[_query_call(file.id, file.title) for file in [*files, pending_target]],
            at=base,
        )
        _add_turn(
            db_session,
            chat_session_id,
            user_text="删掉这些草稿",
            tool_calls=[
                {
                    "id": "c_p",
                    "name": "parallel_execute",
                    "arguments": {
                        "tasks": [{"type": "delete_file", "params": {"id": file.id}} for file in files]
                    },
                    "status": "success",
                    "result": {
                        "tasks": [
                            {
                                "index": i,
                                "params": {"id": file.id},
                                "type": "delete_file",
                                "status": "completed",
                                "result": {"status": "success", "data": True},
                            }
                            for i, file in enumerate(files[:5])
                        ],
                        "dropped": 2,
                    },
                },
                {"id": "c_d", "name": "delete_file", "arguments": {"id": pending_target.id}, "status": "pending"},
            ],
            at=base + timedelta(minutes=1),
        )

        loader = SessionLoader(project_id=project.id, user_id=session_loader_test_data["user"].id)
        result = loader.load_chat_session(db_session)
        ref_ids = {ref["file_id"] for ref in result.working_set_refs}

        assert not ref_ids & {file.id for file in files[:5]}
        assert {files[5].id, files[6].id, pending_target.id} <= ref_ids

    def test_working_set_marks_chapters_the_author_edited_after_the_ai(
        self,
        db_session: Session,
        session_loader_test_data,
    ):
        """审计 P1-3：作者把 AI 写的第 3 章「老周」手改成「老秦」，下一轮 AI 把它改了回去。
        工作集里要告诉 agent 这份正文是作者改过的，<author_edits> 里列出具体改了什么。"""
        from services.features.file_version_service import FileVersionService

        project = session_loader_test_data["project"]
        chat_session_id = session_loader_test_data["chat_session"].id
        versions = FileVersionService()

        edited = self._make_file(db_session, project.id, "第3章 三十年", "老周在收摊。")
        versions.create_version(db_session, edited.id, "老周在收摊。", change_type="create", change_source="ai")
        edited.content = "老秦在收摊。"
        db_session.add(edited)
        db_session.commit()
        versions.create_version(db_session, edited.id, "老秦在收摊。", change_type="edit", change_source="user")

        untouched = self._make_file(db_session, project.id, "第2章", "老周的摊子。")
        versions.create_version(db_session, untouched.id, "老周的摊子。", change_type="create", change_source="ai")

        _add_turn(
            db_session,
            chat_session_id,
            user_text="写第3章",
            tool_calls=[_query_call(edited.id, edited.title), _query_call(untouched.id, untouched.title)],
            at=datetime.utcnow() - timedelta(minutes=1),
        )

        loader = SessionLoader(project_id=project.id, user_id=session_loader_test_data["user"].id)
        result = loader.load_chat_session(db_session)
        loader.attach_author_edits(db_session, result)
        loader.attach_working_set(db_session, result)
        text = result.working_set_context

        edited_line = next(line for line in text.splitlines() if edited.id in line)
        untouched_line = next(line for line in text.splitlines() if untouched.id in line)
        assert "亲手改过" in edited_line and "<author_edits>" in edited_line
        assert "亲手改过" not in untouched_line
        # 具体改了什么：AI 写的原句 → 作者的写法；没改过的文件不列。
        assert "「老周在收摊。」→「老秦在收摊。」" in result.author_edits_context
        assert untouched.id not in result.author_edits_context
        # 本轮开始时模型看到全文的正文：edit_file 据此拒绝基于旧正文的修改。
        assert result.read_snapshots == {edited.id: "老秦在收摊。", untouched.id: "老周的摊子。"}

        # 超出工作集预算、只列了标题的文件：模型没看到全文，不记快照（它的 edit_file 只按逐字匹配）。
        seen: dict[str, str] = {}
        loader._build_working_set_context(
            session=db_session,
            refs=result.working_set_refs,
            full_in_context_ids=set(),
            max_files=1,
            rendered_full_contents=seen,
        )
        assert len(seen) == 1

    def test_working_set_labels_use_the_editor_word_count_not_characters(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        """live run 2 场景 C：标签把 len()=2467 写成「2467 字」（编辑器 2018），模型据此
        告诉作者「少了近 500 字」，实际只少了 43 字。标签要用编辑器口径的字数。"""
        from utils.text_metrics import count_words

        monkeypatch.setattr("agent.core.session_loader.AGENT_WORKING_SET_MAX_FILES", 1)
        project = session_loader_test_data["project"]
        chat_session_id = session_loader_test_data["chat_session"].id
        body = "　　炉火烧了三天。\n\n“师父，天亮了。”\n\n他没回头，Li Qing 只应了一声。"
        first = self._make_file(db_session, project.id, "第一章", body)
        second = self._make_file(db_session, project.id, "第二章", body * 2)
        _add_turn(
            db_session,
            chat_session_id,
            user_text="读",
            tool_calls=[_query_call(first.id, "第一章"), _query_call(second.id, "第二章")],
            at=datetime.utcnow() - timedelta(minutes=1),
        )

        loader = SessionLoader(project_id=project.id, user_id=session_loader_test_data["user"].id)
        result = loader.load_chat_session(db_session)
        loader.attach_working_set(db_session, result)
        text = result.working_set_context

        assert count_words(body) != len(body)
        assert f"[全文，{count_words(body)} 字；上一轮已读取]" in text
        assert f"《第二章》 (id={second.id})（{count_words(body * 2)} 字）" in text
        assert f"{len(body)} 字" not in text

    def test_working_set_respects_budget_and_context_dedupe(
        self,
        db_session: Session,
        session_loader_test_data,
        monkeypatch,
    ):
        from agent.schemas.context import ContextData

        monkeypatch.setattr("agent.core.session_loader.AGENT_WORKING_SET_MAX_FILES", 1)
        project = session_loader_test_data["project"]
        chat_session_id = session_loader_test_data["chat_session"].id

        in_context = self._make_file(db_session, project.id, "焦点章", "焦点全文")
        first = self._make_file(db_session, project.id, "大纲A", "A" * 50)
        second = self._make_file(db_session, project.id, "大纲B", "B" * 50)
        _add_turn(
            db_session,
            chat_session_id,
            user_text="读",
            tool_calls=[
                _query_call(in_context.id, "焦点章"),
                _query_call(first.id, "大纲A"),
                _query_call(second.id, "大纲B"),
            ],
            at=datetime.utcnow() - timedelta(minutes=1),
        )

        loader = SessionLoader(project_id=project.id, user_id=session_loader_test_data["user"].id)
        result = loader.load_chat_session(db_session)
        result.context_data = ContextData(
            context="项目上下文",
            items=[
                {
                    "id": in_context.id,
                    "type": "outline",
                    "title": "焦点章",
                    "content": "焦点全文",
                    "metadata": {"file_type": "outline", "is_focus": True},
                }
            ],
            refs=[in_context.id],
            token_estimate=10,
        )
        loader.attach_working_set(db_session, result)
        text = result.working_set_context

        # 已在系统上下文里标注[全文]的文件只列一行，不再附第二份全文
        assert "焦点全文" not in text
        assert "已在本轮上下文 <turn_context> 的项目上下文中标注[全文]" in text
        assert f"(id={in_context.id})" in text
        # 文件数上限：第一份附全文，第二份只列标题 + id
        assert "A" * 50 in text
        assert "B" * 50 not in text
        assert "未附全文" in text
        assert f"《大纲B》 (id={second.id})" in text


@pytest.mark.unit
def test_history_window_start_does_not_slide_every_turn():
    """Over budget, the window used to drop one old message per turn, so nothing after the
    system prompt was ever a prompt-cache hit across turns. It now stays on an anchor."""
    from agent.utils.token_utils import estimate_message_tokens

    loader = SessionLoader(project_id="p", user_id="u")
    budget = 8_000  # ~26 messages: a realistic 32k budget holds 16-30 replayed messages
    history: list[dict] = []
    starts: list[str] = []
    for turn in range(40):
        history.append({"id": f"u{turn}", "ordinal": 2 * turn, "role": "user", "content": "作者的要求。" * 60})
        history.append({"id": f"a{turn}", "ordinal": 2 * turn + 1, "role": "assistant", "content": "写好的总结。" * 60})
        window = loader._trim_history_to_token_budget(history, budget)
        assert sum(estimate_message_tokens(m) for m in window) <= budget
        assert window[-1] is history[-1]
        if len(window) < len(history):
            starts.append(window[0]["id"])

    assert len(starts) > 20
    moves = sum(1 for previous, current in zip(starts, starts[1:], strict=False) if previous != current)
    assert moves < len(starts) // 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_history_with_removed_proposal_tool_results_still_loads(
    db_session: Session,
    session_loader_test_data,
):
    """Chats from before the AI-proposal removal keep update_project results with proposal fields."""
    from agent.context.assembler import ContextAssembler

    project = session_loader_test_data["project"]
    project.notes = "【作者确认】\n女主前期隐藏身份\n\n【AI 提议·待作者确认】\n关键配角：沈度"
    db_session.add(project)
    db_session.commit()
    old_result = {
        "status": "success",
        "data": {
            "project_id": project.id,
            "updated_fields": ["notes"],
            "proposals_added": ["关键配角：沈度"],
            "proposals_removed": ["沈度是配角"],
            "notes_recorded_as_proposals": ["关键配角：沈度"],
            "notes_note": "已记在备注的「AI 提议·待作者确认」里",
        },
    }
    _add_turn(
        db_session,
        session_loader_test_data["chat_session"].id,
        user_text="记一下配角",
        tool_calls=[{
            "id": "call_up_old",
            "name": "update_project",
            "arguments": {"proposals": "关键配角：沈度"},
            "status": "success",
            "result": old_result,
        }],
        at=datetime.utcnow() - timedelta(minutes=5),
    )

    loader = SessionLoader(project_id=project.id, user_id=session_loader_test_data["user"].id)
    result = await loader.load_session_and_context(db_session, ContextAssembler(), query="继续")

    assert any(message.get("role") == "user" and "记一下配角" in str(message.get("content")) for message in result.history_messages)
    context = result.context_data.context
    assert "女主前期隐藏身份" in context
    assert "作者已忽略" not in context and "【备注·AI 提议" not in context
