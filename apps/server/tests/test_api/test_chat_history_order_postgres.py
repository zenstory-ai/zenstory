"""PG characterization of HTTP/Suggest ties against SessionLoader's ID convention.

Opaque IDs provide a deterministic tie-break, not a claim about message age.
Run serially with both PostgreSQL URLs set before importing the actual app.
"""

import asyncio
import hashlib
import inspect
import json
import os
import random
import re
import sys
import threading
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, select

import database
from agent import suggest_service
from agent.core.llm_client import LLMClient
from agent.core.session_loader import SessionLoader
from main import app
from models import ChatMessage, ChatSession, Project, User
from services.core.auth_service import create_access_token

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"),
    reason="isolated PostgreSQL URL not configured",
)


def write_receipt(name, data):
    directory = os.getenv("M07_HISTORY_EVIDENCE")
    if directory:
        label = os.getenv("M07_HISTORY_RUN", "run")
        (Path(directory) / f"{label}-{name}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False, default=str) + "\n"
        )


def product_imports():
    expected = os.getenv("M07_HISTORY_PRODUCT", str(Path(database.__file__).resolve().parent))
    candidates = {
        "api.chat": Path(os.getenv("M07_HISTORY_CHAT_SOURCE", sys.modules["api.chat"].__file__)),
        "agent.suggest_service": Path(os.getenv(
            "M07_HISTORY_SUGGEST_SOURCE", suggest_service.__file__
        )),
    }
    assert Path(suggest_service.__file__).resolve() == candidates["agent.suggest_service"].resolve()
    result = {}
    for name, module in list(sys.modules.items()):
        filename = getattr(module, "__file__", None)
        if filename and (Path(filename).is_relative_to(expected) or name in candidates):
            path = Path(filename).resolve()
            result[name] = {
                "file": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
    for name in ("api.chat", "api.agent", "agent.suggest_service",
                 "agent.core.session_loader", "agent.service", "database"):
        assert name in result, f"missing authoritative root import: {name}"
        if name in candidates:
            assert Path(result[name]["file"]) == candidates[name].resolve()
    return result


@pytest.fixture(scope="module")
def local_history_settings():
    with pytest.MonkeyPatch.context() as settings:
        settings.delenv("REDIS_URL", raising=False)
        settings.setenv("AGENT_ENABLE_RETRIEVAL_SNIPPETS", "false")
        settings.setenv("DEEPSEEK_API_KEY", "local-unused-completion-boundary")
        settings.setattr(suggest_service, "_service", None)
        settings.setattr(sys.modules[LLMClient.__module__], "_llm_client", None)
        yield


@pytest.fixture(scope="module")
def pg_engine(local_history_settings):
    assert database.is_postgres
    assert database.sync_engine.dialect.name == "postgresql"
    assert os.environ["DATABASE_URL"] == os.environ["ZENSTORY_TEST_POSTGRES_URL"]
    assert not os.getenv("REDIS_URL")
    engine = database.sync_engine
    SQLModel.metadata.create_all(engine)
    chat_indexes = [sql for sql in database.POSTGRES_PERFORMANCE_INDEX_SQL
                    if "ON chat_session " in sql or "ON chat_message " in sql]
    with engine.begin() as connection:
        for sql in chat_indexes:
            connection.execute(text(sql))
        indexes = connection.execute(text(
            "SELECT indexname,indexdef FROM pg_indexes "
            "WHERE tablename IN ('chat_session','chat_message') ORDER BY indexname"
        )).all()
        foreign_keys = connection.execute(text(
            "SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE contype='f' AND conrelid IN "
            "('chat_session'::regclass,'chat_message'::regclass) ORDER BY conname"
        )).all()
    write_receipt("imports-start", product_imports())
    write_receipt("schema", {"indexes": [list(row) for row in indexes],
                             "foreign_keys": [list(row) for row in foreign_keys],
                             "index_sql": chat_indexes})
    try:
        yield engine
    finally:
        write_receipt("imports-end", product_imports())
        engine.dispose()
        if database.async_engine is not None:
            asyncio.run(database.async_engine.dispose())


@pytest.fixture
def observation(pg_engine, request):
    rows = []
    lock = threading.Lock()
    request_sessions = []
    previous_overrides = dict(app.dependency_overrides)

    def record(kind, **values):
        with lock:
            rows.append({"sequence": len(rows), "kind": kind,
                         "thread": threading.get_ident(), **values})

    def observe_sql(connection, cursor, statement, parameters, context, executemany):
        frames = [{"file": frame.filename, "line": frame.lineno,
                   "function": frame.function} for frame in inspect.stack()
                  if any(part in frame.filename for part in
                         ("/api/chat.py", "/agent/suggest_service.py",
                          "/agent/core/session_loader.py", "/auth_service.py"))]
        record("sql", sql=statement, parameters=parameters, frames=frames,
               backend_pid=connection.connection.driver_connection.info.backend_pid)

    def cold_session():
        with Session(pg_engine) as session:
            assert session.expire_on_commit is True
            assert not session.identity_map
            request_sessions.append(session)
            record("request_session_open", number=len(request_sessions))
            try:
                yield session
            finally:
                record("request_session_yield_finished", number=len(request_sessions))
        record("request_session_closed", number=len(request_sessions))

    app.dependency_overrides[database.get_session] = cold_session
    event.listen(pg_engine, "before_cursor_execute", observe_sql)
    try:
        yield record, rows
    finally:
        event.remove(pg_engine, "before_cursor_execute", observe_sql)
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous_overrides)
        assert all(not session.in_transaction() for session in request_sessions)
        record("cleanup", request_sessions=len(request_sessions),
               listeners_removed=True, overrides_restored=True,
               request_transactions_remaining=0)
        write_receipt(re.sub(r"[^a-zA-Z0-9_-]", "_", request.node.name), rows)


@pytest.fixture
def actors(pg_engine):
    namespace = uuid.uuid4()
    ids = {name: str(uuid.uuid5(namespace, name))
           for name in ("owner", "foreign", "project", "foreign_project", "empty_project",
                        "chat", "foreign_chat", "inactive_chat")}
    with Session(pg_engine) as session:
        for key in ("owner", "foreign"):
            session.add(User(id=ids[key], username=f"history-{ids[key]}",
                             email=f"{ids[key]}@example.test", hashed_password="not-used",
                             is_active=True, email_verified=True))
        session.commit()
        for key, owner in (("project", "owner"), ("foreign_project", "foreign"),
                           ("empty_project", "owner")):
            session.add(Project(id=ids[key], owner_id=ids[owner], name="History proof",
                                project_type="novel"))
        session.commit()
        for key, project, owner, active in (
            ("chat", "project", "owner", True),
            ("foreign_chat", "foreign_project", "foreign", True),
            ("inactive_chat", "project", "owner", False),
        ):
            session.add(ChatSession(id=ids[key], project_id=ids[project],
                                    user_id=ids[owner], is_active=active))
        session.commit()
        for key in ("foreign_chat", "inactive_chat"):
            session.add(ChatMessage(session_id=ids[key], role="user",
                                    content=f"excluded:{key}", created_at=datetime(2030, 1, 1)))
        session.commit()
    ids["namespace"] = namespace
    return ids


def seed_history(engine, actors, *, tied):
    base = datetime(2026, 6, 1, 12)
    count = 64 if tied else 8
    ids = [str(uuid.uuid5(actors["namespace"], f"message-{i}")) for i in range(count)]
    insertion = list(ids)
    random.Random(719).shuffle(insertion)
    assert insertion != sorted(ids) and insertion != sorted(ids, reverse=True)
    rows = []
    with Session(engine) as session:
        for index, message_id in enumerate(insertion):
            created_at = base if tied else base + timedelta(seconds=index)
            row = ChatMessage(id=message_id, session_id=actors["chat"],
                              role="user" if index % 2 == 0 else "assistant",
                              content=f"history:{message_id}", message_metadata='{"local":true}',
                              created_at=created_at)
            session.add(row)
        for label, offset in (("older", -3600), ("newer", 3600)):
            session.add(ChatMessage(id=str(uuid.uuid5(actors["namespace"], label)),
                                    session_id=actors["chat"], role="user",
                                    content=f"boundary:{label}",
                                    created_at=base + timedelta(seconds=offset)))
        session.commit()
    # The oracle uses persisted rows read in a fresh Session, then Python tuple sorting.
    with Session(engine) as session:
        for row in session.exec(select(ChatMessage).where(
            ChatMessage.session_id == actors["chat"]
        )).all():
            rows.append({"id": row.id, "session_id": row.session_id, "role": row.role,
                         "content": row.content, "tool_calls": row.tool_calls,
                         "metadata": row.message_metadata, "created_at": row.created_at})
    return {"stored": rows, "insertion_ids": insertion, "tied": tied}


def latest_window(snapshot, limit):
    descending = sorted(snapshot["stored"],
                        key=lambda row: (row["created_at"], row["id"]), reverse=True)
    return list(reversed(descending[:limit]))


def request_http(record, method, path, user_id, **kwargs):
    async def request():
        headers = {"Authorization": f"Bearer {create_access_token({'sub': user_id})}"}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://local.test") as client:
            response = await client.request(method, path, headers=headers, **kwargs)
        record("http", method=method, path=path, request=kwargs,
               status=response.status_code, body=response.json())
        return response

    return asyncio.run(request())


def assert_history_dto(data, snapshot):
    stored = {row["id"]: row for row in snapshot["stored"]}
    for row in data:
        assert set(row) == {"id", "session_id", "role", "content", "tool_calls",
                            "metadata", "created_at"}
        expected = stored[row["id"]]
        assert {key: value for key, value in row.items() if key != "created_at"} == {
            key: value for key, value in expected.items() if key != "created_at"
        }
        assert datetime.fromisoformat(row["created_at"]) == expected["created_at"]


@pytest.mark.parametrize("endpoint,default_limit", [("messages", 50), ("recent", 20)])
@pytest.mark.parametrize("requested_limit", [None, 5])
def test_http_tied_latest_window_matches_session_loader_convention(
    pg_engine, actors, observation, endpoint, default_limit, requested_limit
):
    """Runtime consistency RED; endpoint tie-contract adoption still needs review."""
    record, _ = observation
    snapshot = seed_history(pg_engine, actors, tied=True)
    limit = requested_limit if requested_limit is not None else default_limit
    expected = [row["id"] for row in latest_window(snapshot, limit)]
    record("oracle", snapshot=snapshot, limit=limit, expected_ids=expected,
           policy="existing SessionLoader (created_at,id); opaque ids have no age meaning")
    observed = []
    for _ in range(2):
        kwargs = {} if requested_limit is None else {"params": {"limit": requested_limit}}
        response = request_http(record, "GET",
                                f"/api/v1/chat/session/{actors['project']}/{endpoint}",
                                actors["owner"], **kwargs)
        assert response.status_code == 200
        data = response.json()
        assert len(data) == limit
        assert_history_dto(data, snapshot)
        observed.append([row["id"] for row in data])
    record("tie_comparison", expected_ids=expected, observed_ids=observed,
           unchanged_reads_equal=observed[0] == observed[1])
    assert observed == [expected, expected], "HTTP tied window differs from SessionLoader convention"


def test_real_session_loader_tie_policy_control(pg_engine, actors, observation):
    record, _ = observation
    snapshot = seed_history(pg_engine, actors, tied=True)
    with Session(pg_engine) as session:
        assert session.expire_on_commit and not session.identity_map
        result = SessionLoader(actors["project"], actors["owner"]).load_chat_session(session)
        expected = [row["id"] for row in latest_window(snapshot, len(snapshot["stored"]))]
        observed = [row["id"] for row in result.history_messages]
        record("session_loader", expected_ids=expected, observed_ids=observed,
               session_id=result.session_id)
        assert result.session_id == actors["chat"]
        assert observed == expected
        denied = SessionLoader(actors["foreign_project"], actors["owner"]).load_chat_session(session)
        assert denied.history_messages == [] and denied.session_id is None


@pytest.mark.parametrize("endpoint", ["messages", "recent"])
def test_http_non_tied_window_dto_and_limits_controls(pg_engine, actors, observation, endpoint):
    record, _ = observation
    snapshot = seed_history(pg_engine, actors, tied=False)
    for limit in (1, 3, 100):
        response = request_http(record, "GET",
                                f"/api/v1/chat/session/{actors['project']}/{endpoint}",
                                actors["owner"], params={"limit": limit})
        assert response.status_code == 200
        data = response.json()
        assert_history_dto(data, snapshot)
        assert [row["id"] for row in data] == [row["id"] for row in latest_window(snapshot, limit)]
    for limit in (0, -1, 101):
        response = request_http(record, "GET",
                                f"/api/v1/chat/session/{actors['project']}/{endpoint}",
                                actors["owner"], params={"limit": limit})
        assert response.status_code == 422


@pytest.mark.parametrize("endpoint", ["messages", "recent"])
def test_http_history_ownership_empty_and_auth_controls(actors, observation, endpoint):
    record, _ = observation
    foreign = request_http(record, "GET",
                           f"/api/v1/chat/session/{actors['foreign_project']}/{endpoint}",
                           actors["owner"])
    assert foreign.status_code == 403
    assert "excluded:" not in foreign.text
    empty = request_http(record, "GET",
                         f"/api/v1/chat/session/{actors['empty_project']}/{endpoint}",
                         actors["owner"])
    assert empty.status_code == 200 and empty.json() == []
    missing = request_http(record, "GET",
                           f"/api/v1/chat/session/{uuid.uuid4()}/{endpoint}", actors["owner"])
    # Current permission contract checks ownership before the missing-project branch.
    assert missing.status_code == 403
    async def unauthenticated():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://local.test") as client:
            return await client.get(f"/api/v1/chat/session/{actors['project']}/{endpoint}")
    response = asyncio.run(unauthenticated())
    record("unauthenticated", status=response.status_code, body=response.json())
    assert response.status_code == 401


@pytest.fixture
def completion_capture(monkeypatch):
    calls = []

    async def complete(self, **kwargs):
        assert self._sync_client is None and self._async_client is None
        calls.append(kwargs)
        return '{"suggestions":["完善角色动机","讨论情节伏笔","梳理章节结构"]}'

    # Sole fake: the chosen final provider completion, not service/history/permission/ORM.
    monkeypatch.setattr(LLMClient, "acomplete", complete)
    return calls


def suggest_call(record, actors):
    return request_http(record, "POST", "/api/v1/agent/suggest", actors["owner"],
                        json={"project_id": actors["project"], "count": 3})


def prompt_history_contents(prompt):
    return re.findall(r"(?:history:[0-9a-f-]{36}|boundary:(?:older|newer))", prompt)


def test_suggest_tied_prompt_window_matches_session_loader_convention(
    pg_engine, actors, observation, completion_capture
):
    record, _ = observation
    snapshot = seed_history(pg_engine, actors, tied=True)
    expected = [row["content"] for row in latest_window(snapshot, 5)]
    record("oracle", snapshot=snapshot, limit=5, expected_contents=expected)
    observed = []
    for _ in range(2):
        response = suggest_call(record, actors)
        assert response.status_code == 200
        assert response.json() == {"suggestions": ["完善角色动机", "讨论情节伏笔", "梳理章节结构"]}
        assert len(completion_capture) == len(observed) + 1, "actual history must reach completion"
        prompt = completion_capture[-1]["messages"][0]["content"]
        assert "小说写作助手" in prompt
        assert "excluded:" not in prompt
        contents = prompt_history_contents(prompt)
        record("actual_prompt", prompt=prompt, observed_contents=contents)
        observed.append(contents)
    record("tie_comparison", expected_contents=expected, observed_contents=observed,
           unchanged_reads_equal=observed[0] == observed[1])
    assert observed == [expected, expected], "Suggest tied prompt differs from SessionLoader convention"


def test_suggest_non_tied_novel_ownership_count_and_no_quota_consumption_controls(
    pg_engine, actors, observation, completion_capture
):
    record, rows = observation
    snapshot = seed_history(pg_engine, actors, tied=False)
    response = suggest_call(record, actors)
    assert response.status_code == 200
    assert len(response.json()["suggestions"]) == 3
    assert len(completion_capture) == 1
    prompt = completion_capture[0]["messages"][0]["content"]
    record("actual_prompt", prompt=prompt)
    assert "小说写作助手" in prompt and "excluded:" not in prompt
    assert prompt_history_contents(prompt) == [row["content"] for row in latest_window(snapshot, 5)]
    for project, count, status in ((actors["foreign_project"], 3, 403),
                                   (actors["project"], 0, 422),
                                   (actors["project"], 6, 422)):
        rejected = request_http(record, "POST", "/api/v1/agent/suggest", actors["owner"],
                                json={"project_id": project, "count": count})
        assert rejected.status_code == status
    assert len(completion_capture) == 1
    # This endpoint's documented contract does not consume AI conversation quota.
    assert not any(re.search(r"(?:INSERT INTO|UPDATE)\s+(?:usage_quota|user_subscription)",
                             row.get("sql", ""), re.IGNORECASE) for row in rows)
