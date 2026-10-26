"""History reads must avoid loading a large unused page without changing replay."""

import json
import os
import sys
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.schema import CreateSchema, DropSchema
from sqlmodel import Session, SQLModel

from agent.core.session_loader import SessionLoader
from agent.openai_agents.runner import normalize_messages_for_openai_agents
from models import ChatMessage, ChatSession, Project, User

pytestmark = pytest.mark.unit


@pytest.fixture
def history_engine(tmp_path):
    # Opt-in PG uses its own schema, never shared test/application tables.
    url = os.getenv("ZENSTORY_TEST_POSTGRES_URL")
    base = create_engine(url or f"sqlite:///{tmp_path / 'history.db'}")
    schema = f"history_batch_{uuid4().hex}" if url else None
    if schema:
        assert base.dialect.name == "postgresql"
        with base.begin() as connection:
            connection.execute(CreateSchema(schema))
        engine = base.execution_options(schema_translate_map={None: schema})
    else:
        engine = base
    try:
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(User(id="history-user", username="history", email="history@example.invalid",
                             hashed_password="unused"))
            session.flush()
            session.add(Project(id="history-project", name="History", owner_id="history-user"))
            session.flush()
            # 同一作品只能有一个 active 会话（uq_chat_session_user_project_active）：另一个是已归档的旧对话。
            session.add_all([
                ChatSession(id="history-chat", user_id="history-user", project_id="history-project"),
                ChatSession(
                    id="another-chat", user_id="history-user", project_id="history-project", is_active=False
                ),
            ])
            session.commit()
        yield engine
    finally:
        if schema:
            with base.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        base.dispose()


def _seed(engine, count, *, hidden_chars=0, same_timestamp=False):
    start = datetime(2026, 1, 1)
    with engine.begin() as connection:
        connection.execute(
            ChatMessage.__table__.insert(),
            [
                {
                    "id": f"msg-{i:05}",
                    "session_id": "history-chat",
                    "role": "assistant" if i % 2 else "user",
                    "content": f"消息 {i}",
                    "reasoning_content": "思" * hidden_chars if i % 2 else None,
                    "tool_calls": None,
                    "message_metadata": None,
                    "created_at": start if same_timestamp else start + timedelta(seconds=i),
                }
                for i in range(count)
            ],
        )


def _load(engine, budget, **kwargs):
    pages = []
    payloads = []
    with Session(engine) as session:
        @event.listens_for(session, "do_orm_execute")
        def count_query(execution):
            # Pages of message rows; the ordinal COUNT that aligns the window is not a page.
            entities = [d.get("entity") for d in getattr(execution.statement, "column_descriptions", [])]
            if ChatMessage not in entities:
                return
            pages.append(0)
            payloads.append(0)

        @event.listens_for(session, "loaded_as_persistent")
        def count_row(_session, row):
            if isinstance(row, ChatMessage):
                pages[-1] += 1
                payloads[-1] += sum(
                    sys.getsizeof(value)
                    for value in (
                        row.content, row.reasoning_content, row.tool_calls, row.message_metadata
                    )
                    if isinstance(value, str)
                )

        result = SessionLoader("history-project", None)._load_history_window_under_token_budget(
            session=session, chat_session_id="history-chat", token_budget=budget, **kwargs
        )
    return result, pages, payloads


def test_large_unused_history_page_is_not_loaded(history_engine, monkeypatch):
    _seed(history_engine, 200, hidden_chars=16_000)
    monkeypatch.setattr("agent.core.session_loader.estimate_message_tokens", lambda _msg: 4000)

    expected, _, _ = _load(history_engine, 25_000, page_size=200)
    actual, pages, _ = _load(history_engine, 25_000)

    assert len(actual) == 6
    assert actual == expected
    assert [row["id"] for row in actual] == [f"msg-{i:05}" for i in range(194, 200)]
    assert len(pages) == 1
    assert sum(pages) <= 20, "Only a few rows fit; do not decode all 200 full payloads"


def test_small_rows_grow_batches_instead_of_querying_every_twenty(history_engine, monkeypatch):
    _seed(history_engine, 300, same_timestamp=True)
    monkeypatch.setattr("agent.core.session_loader.estimate_message_tokens", lambda _msg: 1)

    expected, _, _ = _load(history_engine, 1000, page_size=200)
    actual, pages, _ = _load(history_engine, 1000)

    assert actual == expected
    assert [row["id"] for row in actual] == [f"msg-{i:05}" for i in range(300)]
    assert sum(pages) == 300
    assert len(pages) <= 6, "A fixed tiny page makes short-message histories needlessly chatty"


def test_hidden_payload_size_limits_batch_growth(history_engine, monkeypatch):
    # Reasoning consumes memory but deliberately contributes no replay tokens.
    _seed(history_engine, 200, hidden_chars=250_000)
    monkeypatch.setattr("agent.core.session_loader.estimate_message_tokens", lambda _msg: 1)

    expected, _, _ = _load(history_engine, 125, page_size=200)
    actual, pages, payloads = _load(history_engine, 125)

    assert actual == expected
    # Truncated windows start on an anchor (a block-aligned user message), never below half the budget.
    assert 125 // 2 < len(actual) <= 125
    assert actual[0]["role"] == "user" and actual[0]["ordinal"] % 8 < 2
    assert max(pages) <= 40, "Small visible text must not hide multi-MB reasoning pages"
    assert max(payloads) < 12 * 1024 * 1024


@pytest.mark.parametrize("budget", [0, -10, 1])
def test_newest_oversized_message_is_kept(history_engine, monkeypatch, budget):
    _seed(history_engine, 45, same_timestamp=True)
    monkeypatch.setattr("agent.core.session_loader.estimate_message_tokens", lambda _msg: 1000)

    actual, pages, _ = _load(history_engine, budget)
    expected, _, _ = _load(history_engine, budget, page_size=200)

    assert actual == expected
    assert [row["id"] for row in actual] == ["msg-00044"]
    assert len(pages) == 1


@pytest.mark.parametrize("page_size", [1, 7, 20, 200, 0, -3])
def test_explicit_page_size_keeps_fixed_paging_compatibility(history_engine, monkeypatch, page_size):
    _seed(history_engine, 65, same_timestamp=True)
    monkeypatch.setattr("agent.core.session_loader.estimate_message_tokens", lambda _msg: 1)

    actual, pages, _ = _load(history_engine, 53, page_size=page_size)

    # The 53-message sliding window (12..64) starts at the first anchor in its older half.
    assert [row["id"] for row in actual] == [f"msg-{i:05}" for i in range(16, 65)]
    assert max(pages) <= max(1, page_size or 200)


@pytest.mark.parametrize("budget", [1, 19, 20, 21, 59, 60, 61, 65, 79, 80, 81])
def test_status_routing_and_tool_memory_are_identical_across_pages(history_engine, monkeypatch, budget):
    _seed(history_engine, 80, same_timestamp=True)
    with history_engine.begin() as connection:
        for i in range(1, 80, 2):
            connection.execute(
                ChatMessage.__table__.update().where(ChatMessage.id == f"msg-{i:05}").values(
                    content="" if i == 79 else f"正文 {i}",
                    reasoning_content="只供界面使用的思考",
                    tool_calls=json.dumps([
                        {"name": "query_files", "arguments": {"id": f"file-{i}"},
                         "status": "success", "result": {"id": f"file-{i}", "title": f"章节{i}"}}
                    ]),
                    message_metadata=json.dumps({
                        "stop_reason": "clarification_needed",
                        "routing": {"current_agent": "writer", "scope": "仅本章"},
                        "status_cards": [{"type": "workflow_stopped", "reason": "clarification_needed",
                                          "question": "用哪个视角？"}],
                    }),
                )
            )
        # These rows must not leak into this session's user/assistant history.
        connection.execute(ChatMessage.__table__.insert(), [
            {"id": "excluded-system", "session_id": "history-chat", "role": "system", "content": "exclude"},
            {"id": "excluded-other", "session_id": "another-chat", "role": "assistant", "content": "exclude"},
        ])
    monkeypatch.setattr("agent.core.session_loader.estimate_message_tokens", lambda _msg: 1)

    expected, _, _ = _load(history_engine, budget, page_size=200)
    actual, _, _ = _load(history_engine, budget)

    assert actual == expected
    if budget >= 80:
        assert len(actual) == 80
    else:
        assert min(budget, 80) // 2 <= len(actual) <= min(budget, 80)
    assert all(row["id"].startswith("msg-") for row in actual)
    assert actual[-1]["routing"]["scope"] == "仅本章"
    normalized = normalize_messages_for_openai_agents(actual)
    assert normalized == normalize_messages_for_openai_agents(expected)
    assert "clarification_needed" in normalized[-1]["content"]
    assert "用哪个视角？" in normalized[-1]["content"]
    assert "已读取文件" in normalized[-1]["content"]


def test_empty_history_stays_empty(history_engine):
    actual, pages, _ = _load(history_engine, 32_000)
    assert actual == []
    assert pages == [0]
