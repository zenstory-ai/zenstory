"""Real MCP query_files characterization; budgets are proposed, not product failures.

No ORM/permission/serializer replacements. Each call owns a cold expiring Session.
The optional evidence destination records observations without locking in an
inefficient projection or asserting timing thresholds.
"""

import asyncio
import hashlib
import inspect
import json
import os
import statistics
import sys
import threading
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from agent.tools import mcp_tools
from agent.tools.file_ops.executor import FileToolExecutor
from agent.tools.registry import TOOL_FUNCTIONS
from models import File, Project, User


def _record(name, value):
    destination = os.getenv("M07_SUMMARY_EVIDENCE")
    if destination:
        Path(destination, f"{os.getenv('M07_SUMMARY_RUN', 'measurement')}-{name}.json").write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        )


def _body(index):
    prefix = f'file-{index}:中文🙂é"\\\n'
    unit = '汉🙂é"\\\n'
    remaining = 65536 - len(prefix.encode())
    repeats, padding = divmod(remaining, len(unit.encode()))
    result = prefix + unit * repeats + "x" * padding
    assert len(result.encode()) == 65536
    return result


class Store:
    def __init__(self, engine, rows, owner, project, foreign_project, deleted, foreign):
        self.engine = engine
        self.rows = rows
        self.owner = owner
        self.project = project
        self.foreign_project = foreign_project
        self.deleted = deleted
        self.foreign = foreign
        self.calls = []

    def call(self, args, *, project=None):
        observation = {
            "args": args,
            "sql": [],
            "file_loads": [],
            "model_dump": [],
            "serializer": [],
            "closed_sessions": 0,
            "profile_restored": False,
            "tokenizer_calls": [],
            "sql_preview_values": [],
        }
        sessions = []
        listeners = []

        def sql_listener(_conn, _cursor, statement, parameters, context, _executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                observation["sql"].append(
                    {
                        "statement": statement,
                        "selected_columns": [str(column) for column in context.compiled.statement.selected_columns],
                        "projection_keys": [column.keyname for column in context.compiled._result_columns],
                        "parameters": list(parameters),
                        "thread": threading.get_ident(),
                        "source": [
                            f"{f.filename}:{f.lineno}:{f.name}"
                            for f in traceback.extract_stack()
                            if "/agent/tools/" in f.filename
                        ],
                    }
                )

        def factory():
            session = Session(self.engine)  # production default expire_on_commit=True
            observation["expire_on_commit"] = session.expire_on_commit
            observation["initial_identity_map_size"] = len(session.identity_map)
            assert session.expire_on_commit and not session.identity_map
            sessions.append(session)
            observation["worker_thread"] = threading.get_ident()
            original_profile = sys.getprofile()

            def loaded(_session, instance):
                if isinstance(instance, File):
                    state = instance.__dict__  # never trigger a lazy content SELECT
                    content = state.get("content")
                    observation["file_loads"].append(
                        {
                            "id": state["id"],
                            "body_loaded": "content" in state,
                            "body_utf8_bytes": len(content.encode()) if isinstance(content, str) else 0,
                        }
                    )

            def profile(frame, kind, result):
                if kind == "call" and "/tiktoken/" in frame.f_code.co_filename:
                    observation["tokenizer_calls"].append(frame.f_code.co_name)
                if kind != "return":
                    return
                name = frame.f_code.co_name
                instance = frame.f_locals.get("self")
                if (
                    name == "model_dump"
                    and isinstance(instance, File)
                    and frame.f_code.co_filename.endswith("sqlmodel/main.py")
                ):
                    observation["model_dump"].append(
                        {
                            "id": result.get("id"),
                            "has_content": "content" in result,
                            "body_utf8_bytes": len(result.get("content", "").encode()),
                            "source": f"{frame.f_code.co_filename}:{frame.f_code.co_firstlineno}",
                        }
                    )
                elif name == "_execute_query_files" and isinstance(result, list):
                    observation["sql_preview_values"] = [
                        {"id": file.id, "utf8_bytes": len(preview.encode()) if isinstance(preview, str) else 0}
                        for file, preview, *_ in result
                    ]
                elif name == "serialize_file" and isinstance(result, dict):
                    observation["serializer"].append(
                        {
                            "id": result["id"],
                            "keys": sorted(result),
                            "preview_chars": len(result.get("content_preview", "")),
                        }
                    )
                elif name == "close" and instance is session:
                    observation["closed_sessions"] += 1
                elif name == "_run_sync_tool_with_owned_session_cleanup":
                    sys.setprofile(original_profile)
                    observation["profile_restored"] = sys.getprofile() is original_profile

            event.listen(session, "loaded_as_persistent", loaded)
            listeners.append((session, loaded))
            sys.setprofile(profile)
            return session

        async def invoke():
            observation["loop_thread"] = threading.get_ident()
            mcp_tools.ToolContext.set_context(
                None, self.owner, project or self.project, None, create_session_func=factory
            )
            try:
                assert TOOL_FUNCTIONS["query_files"] is mcp_tools.query_files
                return await TOOL_FUNCTIONS["query_files"](args)
            finally:
                mcp_tools.ToolContext.clear_context()

        event.listen(self.engine, "before_cursor_execute", sql_listener)
        started = time.perf_counter()
        try:
            envelope = asyncio.run(invoke())
            observation["elapsed_seconds"] = time.perf_counter() - started
            assert envelope.keys() == {"content"}
            assert len(envelope["content"]) == 1
            assert envelope["content"][0]["type"] == "text"
            text = envelope["content"][0]["text"]
            assert len(text) < mcp_tools.TOOL_RESULT_MAX_CHARS  # no overflow side path
            payload = json.loads(text)
            observation["status"] = payload["status"]
            observation["returned_ids"] = [row["id"] for row in payload.get("data", [])]
            observation["returned_content_utf8_bytes"] = sum(
                len(row.get("content", row.get("content_preview", "")).encode()) for row in payload.get("data", [])
            )
            observation["payload_chars"] = len(text)
            assert observation["worker_thread"] != observation["loop_thread"]
            assert observation["closed_sessions"] == len(sessions) == 1
            assert observation["profile_restored"]
            assert not observation["tokenizer_calls"]
            assert all(not session.identity_map for session in sessions)
            return payload, observation
        finally:
            event.remove(self.engine, "before_cursor_execute", sql_listener)
            for session, listener in listeners:
                event.remove(session, "loaded_as_persistent", listener)
                session.close()
            observation["pool_checked_out"] = self.engine.pool.checkedout()
            self.calls.append(observation)
            assert observation["pool_checked_out"] == 0

    def expected(self, rows, *, full=False, preview=200):
        result = []
        for row in rows:
            item = dict(row)
            if not full:
                content = item.pop("content")
                item["content_preview"] = content[:preview]
                # summary 同时给出全文长度，模型能分辨「预览」与「短文件」。
                item["content_length"] = len(content)
                item["content_truncated"] = len(content) > len(content[:preview])
            result.append(item)
        return {"status": "success", "data": result}


@pytest.fixture
def store(request, tmp_path):
    parameters = getattr(request, "param", 32)
    postgres = isinstance(parameters, dict) and parameters.get("backend") == "postgres"
    count = parameters["count"] if isinstance(parameters, dict) else parameters
    path = tmp_path / "query-files-owned.sqlite"
    if postgres:
        engine = create_engine(os.environ["ZENSTORY_TEST_POSTGRES_URL"])
    else:
        engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    def foreign_keys(conn, _record):
        conn.execute("PRAGMA foreign_keys=ON")

    if not postgres:
        event.listen(engine, "connect", foreign_keys)
    SQLModel.metadata.create_all(engine, tables=[User.__table__, Project.__table__, File.__table__])
    owner, foreign_owner = "summary-owner", "summary-other"
    project, foreign_project = "summary-project", "summary-foreign-project"
    rows = []
    now = datetime(2026, 10, 6, 12)
    with Session(engine) as session:
        session.add_all(
            [
                User(id=owner, username=owner, email=f"{owner}@example.invalid", hashed_password="unused"),
                User(
                    id=foreign_owner,
                    username=foreign_owner,
                    email=f"{foreign_owner}@example.invalid",
                    hashed_password="unused",
                ),
            ]
        )
        session.commit()
        session.add_all(
            [
                Project(id=project, name="Own", owner_id=owner),
                Project(id=foreign_project, name="Other", owner_id=foreign_owner),
            ]
        )
        session.commit()
        for index in range(count):
            metadata = (
                None
                if index == 0
                else "{"
                if index == 1
                else json.dumps(
                    {
                        "tag": "Keep" if index % 2 == 0 else "drop",
                        "tags": ["a", "b"],
                        "index": index,
                    }
                )
            )
            created = now + timedelta(seconds=index % 3)
            rows.append(
                {
                    "id": str(uuid5(NAMESPACE_URL, f"m07-summary-{index}")),
                    "project_id": project,
                    "title": f"Title {index}",
                    "content": _body(index),
                    "file_type": ["draft", "outline", "character"][index % 3],
                    "parent_id": None,
                    "order": index // 5,
                    "file_metadata": metadata,
                    "created_at": created.isoformat(),
                    "updated_at": now.isoformat(),
                    "is_deleted": False,
                    "deleted_at": None,
                }
            )
        instances = [
            File(**{**row, "created_at": datetime.fromisoformat(row["created_at"]), "updated_at": now})
            for row in reversed(rows)
        ]
        deleted, foreign = "summary-deleted", "summary-foreign"
        instances.extend(
            [
                File(id=deleted, project_id=project, title="Deleted", content="private-deleted", is_deleted=True),
                File(id=foreign, project_id=foreign_project, title="Foreign", content="private-foreign"),
            ]
        )
        session.add_all(instances)
        session.commit()
    rows.sort(key=lambda row: (row["order"], -datetime.fromisoformat(row["created_at"]).timestamp(), row["id"]))
    subject = Store(engine, rows, owner, project, foreign_project, deleted, foreign)
    try:
        yield subject
    finally:
        assert engine.pool.checkedout() == 0
        if postgres:
            SQLModel.metadata.drop_all(engine, tables=[File.__table__, Project.__table__, User.__table__])
        engine.dispose()
        if not postgres:
            event.remove(engine, "connect", foreign_keys)
            path.unlink()
        _record(
            request.node.name.replace("/", "_"),
            {
                "calls": subject.calls,
                "cleanup": {
                    "owned_database_removed": not path.exists(),
                    "backend": engine.dialect.name,
                    "sessions_closed": True,
                    "engine_disposed": True,
                },
            },
        )


@pytest.mark.parametrize("store", [32, 256], indirect=True)
def test_summary_scale_actual_mcp_cold_measurements(store):
    for _ in range(3):
        payload, observation = store.call({"limit": len(store.rows)})
        assert payload == store.expected(store.rows)
        assert len(observation["sql"]) == 2
        assert len({row["id"] for row in observation["file_loads"]}) == len(store.rows)
    values = [call["elapsed_seconds"] for call in store.calls]
    payload, _ = store.call({})
    assert payload == store.expected(store.rows[:50])
    payload, _ = store.call({"limit": 0})
    assert payload == store.expected([])
    _record(
        f"scale-{len(store.rows)}",
        {
            "median_seconds": statistics.median(values),
            "range_seconds": [min(values), max(values)],
            "calls": store.calls,
        },
    )


def test_summary_modes_and_exact_id_controls(store):
    row = store.rows[0]
    cases = [
        # 按 id 读取、未指定模式：默认全文。
        ({}, True, 200),
        ({"include_content": None}, True, 200),
        ({"content_preview_chars": None}, True, 200),
        # 显式要预览（summary / content_preview_chars / include_content=false）。
        ({"response_mode": "summary"}, False, 200),
        ({"content_preview_chars": 7}, False, 7),
        ({"content_preview_chars": 0}, False, 0),
        ({"include_content": "false"}, False, 200),
        ({"response_mode": "full"}, True, 200),
        ({"include_content": True}, True, 200),
        ({"include_content": "true"}, True, 200),
        ({"response_mode": "full", "include_content": False}, True, 200),
    ]
    for options, full, preview in cases:
        payload, _ = store.call({"id": f" {row['id']} ", "limit": 0, "offset": 999, "query": "no-match", **options})
        assert payload == store.expected([row], full=full, preview=preview)
    payload, _ = store.call({"response_mode": "full", "limit": 1})
    assert payload == store.expected([row], full=True)
    for options, fragment in [
        ({"response_mode": "invalid"}, "response_mode"),
        ({"content_preview_chars": -1}, "content_preview_chars"),
    ]:
        payload, _ = store.call({"id": row["id"], **options})
        assert payload["status"] == "error" and fragment in payload["error"]


def test_paging_type_keyword_and_zero_controls(store):
    cases = [
        ({}, store.rows[:50]),
        ({"limit": 0}, []),
        ({"limit": 6, "offset": 3}, store.rows[3:9]),
        ({"file_type": "draft", "limit": 4}, [r for r in store.rows if r["file_type"] == "draft"][:4]),
        (
            {"file_types": ["draft", "outline"], "file_type": "character", "limit": 5, "offset": 2},
            [r for r in store.rows if r["file_type"] in {"draft", "outline"}][2:7],
        ),
        ({"query": "file-12:"}, [r for r in store.rows if "file-12:" in r["content"]]),
    ]
    for args, expected in cases:
        payload, _ = store.call(args)
        assert payload == store.expected(expected)
    # Exact-ID applies both type constraints (different from merged page precedence).
    payload, _ = store.call({"id": store.rows[0]["id"], "file_type": "lore"})
    assert payload == store.expected([])


def test_scope_missing_deleted_and_metadata_after_paging(store):
    for target in [store.deleted, store.foreign, "not-present"]:
        payload, observation = store.call({"id": target})
        assert payload == store.expected([])
        assert not observation["file_loads"]
    payload, observation = store.call({}, project=store.foreign_project)
    assert payload["status"] == "error"
    assert not observation["file_loads"]
    assert len(observation["sql"]) == 1  # real project permission denied before File query
    first_page = store.rows[:6]
    expected = [
        row
        for row in first_page
        if row["file_metadata"] not in (None, "{") and json.loads(row["file_metadata"])["tag"].lower() == "keep"
    ]
    payload, observation = store.call({"limit": 6, "metadata_filter": {"tag": "KEEP"}})
    assert payload == store.expected(expected)
    assert 0 < len(expected) < 6
    assert len(observation["file_loads"]) == 6  # paging precedes Python metadata filtering
    for row in store.rows:
        if row["file_metadata"] in (None, "{"):
            payload, _ = store.call({"id": row["id"], "metadata_filter": {"tag": "keep"}})
            assert payload == store.expected([])


@pytest.mark.parametrize("store", [0], indirect=True)
def test_empty_active_project_summary(store):
    payload, observation = store.call({})
    assert payload == store.expected([])
    assert len(observation["sql"]) == 2
    assert not observation["file_loads"]


def test_actual_product_import_provenance():
    modules = [
        mcp_tools,
        sys.modules["agent.tools.file_ops.crud"],
        sys.modules["agent.tools.file_ops.serialization"],
        sys.modules["agent.tools.file_ops.executor"],
        sys.modules["agent.tools.permissions"],
    ]
    records = []
    for module in modules:
        path = Path(inspect.getfile(module)).resolve()
        records.append(
            {"module": module.__name__, "file": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        )
        if os.getenv("M07_SUMMARY_ROOT"):
            candidate = module.__name__ in {"agent.tools.file_ops.crud", "agent.tools.file_ops.serialization"}
            expected = "M07_SUMMARY_CHILD" if candidate and os.getenv("M07_SUMMARY_CHILD") else "M07_SUMMARY_ROOT"
            assert path.is_relative_to(Path(os.environ[expected]).resolve())
    _record("import-provenance", records)


def _assert_projection_budget(observation):
    """Prospective authorized budget, distinct from original functional correctness."""
    assert len(observation["sql"]) == 2  # real permission + one File SELECT, no lazy N+1
    file_queries = [
        query
        for query in observation["sql"]
        if "id" in query["projection_keys"] and "file_type" in query["projection_keys"]
    ]
    assert len(file_queries) == 1
    assert "content" not in file_queries[0]["projection_keys"]
    assert all(not row["body_loaded"] for row in observation["file_loads"])
    assert all(not row["has_content"] for row in observation["model_dump"])


@pytest.mark.parametrize("store", [32, 256], indirect=True)
@pytest.mark.parametrize("selection", ["exact", "page", "default"])
def test_prospective_summary_projection_budget(store, selection):
    args = (
        {"id": store.rows[0]["id"], "response_mode": "summary"}
        if selection == "exact"
        else {"limit": len(store.rows)}
        if selection == "page"
        else {}
    )
    expected = [store.rows[0]] if selection == "exact" else store.rows if selection == "page" else store.rows[:50]
    payload, observation = store.call(args)
    assert payload == store.expected(expected)
    _assert_projection_budget(observation)


def _replace_body(store, row, body):
    with Session(store.engine) as session:
        file = session.get(File, row["id"])
        file.content = body
        session.commit()
    row["content"] = body


@pytest.mark.parametrize("preview", [None, 0, 7, 200])
def test_preview_boundaries_and_sqlite_nul_fallback(store, preview):
    row = store.rows[0]
    for body in ["", "短🙂", '汉🙂é"\\\n' * 100, "\0abc汉🙂" * 50, "ab\0汉🙂" * 50, "x" * 220 + "\0tail🙂"]:
        _replace_body(store, row, body)
        payload, observation = store.call(
            {"id": row["id"], "response_mode": "summary", "content_preview_chars": preview}
        )
        assert payload == store.expected([row], preview=200 if preview is None else preview)
        _assert_projection_budget(observation)
        # SQLite NUL fallback is deliberately a full *separate value*, never ORM body hydration.
        # (Also for preview 0: the full value is what lets content_length be computed.)
        values = observation["sql_preview_values"]
        assert len(values) == 1
        limit = 200 if preview is None else preview
        expected_bytes = len(body.encode()) if "\0" in body else 0 if limit == 0 else len(body[:limit].encode())
        assert values[0]["utf8_bytes"] == expected_bytes


def test_invalid_options_empty_nonempty_and_full_ignore(store):
    row = store.rows[0]
    for options in [{"response_mode": "bad"}, {"content_preview_chars": -1}, {"content_preview_chars": True}]:
        payload, observation = store.call({"id": row["id"], **options})
        assert payload["status"] == "error"
        assert observation["file_loads"][0]["body_loaded"]  # legacy fallback/error timing
        payload, _ = store.call({"id": "missing", **options})
        assert payload == store.expected([])
    payload, observation = store.call({"id": row["id"], "response_mode": "full", "content_preview_chars": -1})
    assert payload == store.expected([row], full=True)
    assert observation["file_loads"][0]["body_loaded"]


def test_unknown_dialect_preserves_full_row_fallback(store, monkeypatch):
    # Exercise the eligibility guard on real SQLite; not a claim of another vendor's SQL support.
    monkeypatch.setattr(store.engine.dialect, "name", "test-unsupported")
    payload, observation = store.call({"id": store.rows[0]["id"], "response_mode": "summary"})
    assert payload == store.expected([store.rows[0]])
    assert observation["file_loads"][0]["body_loaded"]


def test_warm_summary_does_not_pollute_persistent_content(store):
    row = store.rows[0]
    with Session(store.engine) as session:
        file = session.get(File, row["id"])
        original = file.content
        executor = FileToolExecutor(session, store.owner)
        summary = executor.query_files(store.project, id=row["id"], content_preview_chars=7)
        assert {"status": "success", "data": summary} == store.expected([row], preview=7)
        assert file.content == original and not session.is_modified(file)
        full = executor.query_files(store.project, id=row["id"], response_mode="full")
        assert {"status": "success", "data": full} == store.expected([row], full=True)
        file.title = "Updated title after summary"
        session.commit()
    with Session(store.engine) as reader:
        persisted = reader.get(File, row["id"])
        assert persisted.content == original and persisted.title == "Updated title after summary"


@pytest.mark.skipif(not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="owned PostgreSQL URL not configured")
@pytest.mark.parametrize("store", [{"count": 32, "backend": "postgres"}], indirect=True)
def test_postgres_native_unicode_preview_parity(store):
    row = store.rows[0]
    for body in ["", "短🙂", '汉🙂é"\\\n' * 100, _body(900)]:
        _replace_body(store, row, body)
        for preview in [None, 0, 7, 200]:
            # 按 id 读取默认全文；这里验证摘要投影，所以显式要 summary。
            payload, observation = store.call(
                {"id": row["id"], "response_mode": "summary", "content_preview_chars": preview}
            )
            assert payload == store.expected([row], preview=200 if preview is None else preview)
            _assert_projection_budget(observation)
    payload, observation = store.call({"limit": 32})
    assert payload == store.expected(store.rows)
    _assert_projection_budget(observation)
    payload, observation = store.call({"query": "file-900:", "limit": 32})
    assert payload == store.expected([row])
    _assert_projection_budget(observation)
