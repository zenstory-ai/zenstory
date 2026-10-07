"""Actual HTTP tree responses: supported depths and legacy-shape characterization."""

from __future__ import annotations

import json
import re
import sys
import traceback
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import create_engine, event, insert, text, update
from sqlmodel import Session, SQLModel

from api.files import get_current_active_user
from database import get_session
from main import app
from models import File, Project, User

BASE_TIME = datetime(2026, 10, 6, 12, tzinfo=UTC)


class TreeServerSerializationFailure(AssertionError):
    """A supported HTTP request failed in the server's nested-value encoder."""


def _nodes(tree):
    """Keep the test's traversal independent of Python's recursion depth."""
    stack = list(reversed(tree))
    while stack:
        node = stack.pop()
        yield node
        stack.extend(reversed(node["children"]))


def _row(project_id, file_id, *, parent_id=None, **values):
    return {
        "id": file_id,
        "project_id": project_id,
        "title": f"Node {file_id}",
        "content": f"body:{file_id}",
        "file_type": "folder",
        "parent_id": parent_id,
        "order": 0,
        "file_metadata": None,
        "created_at": BASE_TIME,
        "updated_at": BASE_TIME,
        "is_deleted": False,
        "deleted_at": None,
        **values,
    }


class TreeProbe:
    def __init__(self, engine, session, user, project, foreign_project):
        self.engine = engine
        self.session = session
        self.user = user
        self.project = project
        self.foreign_project = foreign_project

    def seed(self, rows):
        # Ordered executemany keeps parents ahead of children with FK checks on.
        self.session.execute(insert(File), rows)
        self.session.commit()
        assert self.session.exec(text("PRAGMA foreign_key_check")).all() == []

    async def get(self, *, include_content=False, raise_errors=False, project_id=None):
        statements = []

        def observe(_connection, _cursor, statement, _parameters, _context, _many):
            normalized = statement.lower().replace('"', "")
            if normalized.lstrip().startswith(("select", "with")) and re.search(
                rf"\b(?:from|join)\s+{re.escape(File.__tablename__)}\b", normalized
            ):
                statements.append(normalized)

        event.listen(self.engine, "before_cursor_execute", observe)
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app, raise_app_exceptions=raise_errors),
                base_url="http://test",
            ) as client:
                response = await client.get(
                    f"/api/v1/projects/{project_id or self.project.id}/file-tree",
                    params={} if include_content is None else {
                        "include_content": str(include_content).lower(),
                    },
                )
            return response, statements
        finally:
            event.remove(self.engine, "before_cursor_execute", observe)


@pytest.fixture
def tree_probe(tmp_path, monkeypatch, record_property):
    """Own the SQLite data, auth boundary, and cold request Session lifecycle."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'tree-response.db'}",
        connect_args={"check_same_thread": False},
    )

    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    event.listen(engine, "connect", enable_foreign_keys)
    SQLModel.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        suffix = uuid4().hex
        user = User(
            username=f"tree-{suffix}", email=f"tree-{suffix}@example.test",
            hashed_password="unused", email_verified=True,
        )
        foreign_user = User(
            username=f"foreign-{suffix}", email=f"foreign-{suffix}@example.test",
            hashed_password="unused", email_verified=True,
        )
        session.add_all([user, foreign_user])
        session.flush()
        project = Project(name="Tree response", owner_id=user.id)
        foreign_project = Project(name="Foreign tree", owner_id=foreign_user.id)
        session.add_all([project, foreign_project])
        session.commit()

        def request_session():
            # Production expiration setting, separate identity map for each GET.
            with Session(engine) as request:
                yield request

        monkeypatch.setitem(app.dependency_overrides, get_session, request_session)
        monkeypatch.setitem(app.dependency_overrides, get_current_active_user, lambda: user)
        try:
            yield TreeProbe(engine, session, user, project, foreign_project)
        finally:
            session.rollback()
    engine.dispose()
    # Delete only this test's owned SQLite file; no shared database cleanup.
    (tmp_path / "tree-response.db").unlink()
    assert not (tmp_path / "tree-response.db").exists()
    record_property("sqlite_fixture_cleanup", "owned database disposed and removed")


def _assert_projection(statements, include_content):
    assert len(statements) == 1, statements
    assert ("file.content" in statements[0]) is include_content


def _record(record_property, facts):
    payload = json.dumps(facts, sort_keys=True)
    record_property("tree_probe", payload)
    print(f"TREE_PROBE {payload}")


async def _server_failure(probe, include_content, caplog):
    """Observe a real server exception without decoding the response body."""
    start = len(caplog.records)
    try:
        response, statements = await probe.get(include_content=include_content, raise_errors=True)
    except Exception as exc:
        error, hook = exc, "raising ASGI transport"
    else:
        # LoggingMiddleware intentionally catches pre-response errors and sends
        # HTTP500 even with raise_app_exceptions=True. Read its real exc_info.
        assert response.status_code == 500
        assert len(statements) == 1
        logged = [record for record in caplog.records[start:] if record.exc_info]
        assert logged, "No server exception in the middleware logging hook"
        error = logged[-1].exc_info[1]
        hook = f"{logged[-1].name} LogRecord.exc_info"
    frames = traceback.extract_tb(error.__traceback__)
    encoder_frames = [
        {"file": frame.filename, "line": frame.lineno, "function": frame.name}
        for frame in frames if "fastapi/encoders.py" in frame.filename
    ]
    return {
        "type": type(error).__name__, "message": str(error), "hook": hook,
        "encoder_frame_count": len(encoder_frames), "encoder_frames": encoder_frames[-6:],
        "routing_frames": [
            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in frames if "fastapi/routing.py" in frame.filename
        ],
        "application_frames": [
            {"file": frame.filename, "line": frame.lineno, "function": frame.name}
            for frame in frames if frame.filename.endswith("/api/files.py")
        ],
    }


@pytest.mark.parametrize("include_content", [False, True])
@pytest.mark.parametrize("depth", [24, 70, 250, 1200])
async def test_legal_deep_tree_http_response(tree_probe, depth, include_content, record_property, caplog):
    ids = [f"chain-{index:04d}" for index in range(depth)]
    tree_probe.seed([
        _row(tree_probe.project.id, file_id, parent_id=ids[index - 1] if index else None)
        for index, file_id in enumerate(ids)
    ])
    original_limit = sys.getrecursionlimit()
    response, statements = await tree_probe.get(include_content=include_content)
    assert sys.getrecursionlimit() == original_limit
    _assert_projection(statements, include_content)
    facts = {
        "depth": depth, "include_content": include_content,
        "status": response.status_code, "file_selects": len(statements),
        "response_bytes": len(response.content),
        "request_recursion_limit": original_limit,
    }
    if response.status_code == 500:
        failure = await _server_failure(tree_probe, include_content, caplog)
        facts["server_exception"] = failure
        _record(record_property, facts)
        assert failure["type"] == "RecursionError", failure
        assert failure["encoder_frame_count"] > 0, failure
        raise TreeServerSerializationFailure(
            f"legal depth={depth}: HTTP500, RecursionError in FastAPI jsonable_encoder"
        )
    assert response.status_code == 200, response.text
    # Only the test decoder sees a higher limit, after ASGI completed. The
    # server request and serializer run at the original process setting.
    try:
        sys.setrecursionlimit(max(original_limit, depth * 4 + 1000))
        data = response.json()
    finally:
        sys.setrecursionlimit(original_limit)
    assert sys.getrecursionlimit() == original_limit
    facts["decoder_limit_restored"] = True
    nodes = list(_nodes(data["tree"]))
    facts["response_nodes"] = len(nodes)
    _record(record_property, facts)
    assert [node["id"] for node in nodes] == ids
    assert [node["content"] for node in nodes] == [
        f"body:{file_id}" if include_content else "" for file_id in ids
    ]


async def test_empty_and_omitted_content_flag(tree_probe, record_property):
    empty, statements = await tree_probe.get(include_content=None)
    assert empty.status_code == 200
    assert empty.content == b'{"tree":[]}'
    assert empty.headers["content-type"] == "application/json"
    assert int(empty.headers["content-length"]) == len(empty.content)
    assert empty.json() == {"tree": []}
    _assert_projection(statements, False)
    tree_probe.seed([_row(tree_probe.project.id, "default")])
    response, statements = await tree_probe.get(include_content=None)
    assert response.status_code == 200
    _assert_projection(statements, False)
    node = response.json()["tree"][0]
    assert node["id"] == "default"
    assert node["content"] == ""
    assert node["children"] == []
    _record(record_property, {
        "shape": "empty and omitted include_content", "status": 200,
        "response_nodes": [0, 1], "file_selects_per_request": 1,
    })


@pytest.mark.parametrize("include_content", [False, True])
@pytest.mark.parametrize("width", [6, 1000])
async def test_wide_tree_http_response(tree_probe, width, include_content, record_property):
    ids = [f"wide-{index:04d}" for index in range(width)]
    tree_probe.seed([
        _row(tree_probe.project.id, "root"),
        *[_row(tree_probe.project.id, file_id, parent_id="root", order=index + 1)
          for index, file_id in enumerate(ids)],
    ])
    response, statements = await tree_probe.get(include_content=include_content)
    assert response.status_code == 200
    _assert_projection(statements, include_content)
    nodes = list(_nodes(response.json()["tree"]))
    assert [node["id"] for node in nodes] == ["root", *ids]
    assert [node["content"] for node in nodes] == [
        f"body:{file_id}" if include_content else "" for file_id in ["root", *ids]
    ]
    _record(record_property, {
        "width": width, "include_content": include_content, "status": 200,
        "response_nodes": len(nodes), "file_selects": len(statements),
    })


@pytest.mark.parametrize("include_content", [False, True])
@pytest.mark.parametrize("cycle_size", [1, 2, 3])
async def test_legacy_unreachable_cycle_is_omitted(tree_probe, cycle_size, include_content, record_property):
    ids = [f"cycle-{index}" for index in range(cycle_size)]
    tree_probe.seed([
        _row(tree_probe.project.id, "visible"),
        *[_row(tree_probe.project.id, file_id) for file_id in ids],
        _row(tree_probe.project.id, "cycle-descendant", parent_id=ids[0]),
    ])
    for index, file_id in enumerate(ids):
        tree_probe.session.execute(update(File).where(File.id == file_id).values(
            parent_id=ids[(index + 1) % cycle_size]
        ))
    tree_probe.session.commit()
    assert tree_probe.session.exec(text("PRAGMA foreign_key_check")).all() == []
    response, statements = await tree_probe.get(include_content=include_content)
    assert response.status_code == 200
    _assert_projection(statements, include_content)
    assert [node["id"] for node in _nodes(response.json()["tree"])] == ["visible"]
    _record(record_property, {
        "cycle_size": cycle_size, "include_content": include_content,
        "status": 200, "seeded_nodes": cycle_size + 2, "response_nodes": 1,
        "file_selects": len(statements), "behavior": "unreachable component omitted",
    })


@pytest.mark.parametrize("include_content", [False, True])
async def test_deleted_and_foreign_parent_edges_promote_owned_children(tree_probe, include_content, record_property):
    tree_probe.seed([
        _row(tree_probe.project.id, "deleted-parent", is_deleted=True),
        _row(tree_probe.foreign_project.id, "foreign-parent", content="FOREIGN SECRET"),
        _row(tree_probe.project.id, "deleted-child", parent_id="deleted-parent"),
        _row(tree_probe.project.id, "foreign-child", parent_id="foreign-parent"),
        _row(tree_probe.foreign_project.id, "foreign-under-owned", parent_id="deleted-child", content="FOREIGN SECRET"),
    ])
    response, statements = await tree_probe.get(include_content=include_content)
    assert response.status_code == 200
    _assert_projection(statements, include_content)
    roots = response.json()["tree"]
    assert {node["id"] for node in roots} == {"deleted-child", "foreign-child"}
    assert all(node["children"] == [] for node in roots)
    assert "FOREIGN SECRET" not in response.text
    denied, foreign_queries = await tree_probe.get(project_id=tree_probe.foreign_project.id)
    assert denied.status_code == 403
    assert foreign_queries == []
    _record(record_property, {
        "include_content": include_content, "status": 200, "response_nodes": 2,
        "file_selects": len(statements), "foreign_project_status": 403,
    })


@pytest.mark.parametrize("include_content", [False, True])
async def test_explicit_legacy_missing_parent_is_promoted(tree_probe, include_content, record_property):
    tree_probe.seed([_row(tree_probe.project.id, "orphan")])
    # Deliberate legacy inconsistency, only on this test-owned SQLite database.
    # This is not a failed legal FK fixture and is never used for depth probes.
    with tree_probe.engine.connect() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        connection.execute(update(File).where(File.id == "orphan").values(parent_id="missing-parent"))
        connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert len(connection.exec_driver_sql("PRAGMA foreign_key_check").all()) == 1
    response, statements = await tree_probe.get(include_content=include_content)
    assert response.status_code == 200
    _assert_projection(statements, include_content)
    roots = response.json()["tree"]
    assert len(roots) == 1
    assert roots[0]["id"] == "orphan"
    assert roots[0]["parent_id"] == "missing-parent"
    assert roots[0]["content"] == ("body:orphan" if include_content else "")
    _record(record_property, {
        "shape": "deliberate legacy missing parent", "status": 200,
        "response_nodes": 1, "file_selects": len(statements), "include_content": include_content,
    })


async def test_sequence_order_and_repeated_ties_preserve_current_behavior(tree_probe, record_property):
    tree_probe.seed([
        _row(tree_probe.project.id, "root"),
        _row(tree_probe.project.id, "second", parent_id="root", title="第2章", file_type="draft", order=580),
        _row(tree_probe.project.id, "first", parent_id="root", title="第1章", file_type="draft", order=90),
        _row(tree_probe.project.id, "metadata-third", parent_id="root", title="Prologue", file_type="draft", file_metadata='{"chapter_number": 3}'),
        _row(tree_probe.project.id, "tie-b", parent_id="root", title="Tie B", order=10),
        _row(tree_probe.project.id, "tie-a", parent_id="root", title="Tie A", order=10),
    ])
    orders = []
    for _ in range(2):
        response, statements = await tree_probe.get()
        assert response.status_code == 200
        _assert_projection(statements, False)
        children = response.json()["tree"][0]["children"]
        orders.append([node["id"] for node in children])
    assert orders[0][:3] == ["first", "second", "metadata-third"]
    assert set(orders[0][3:]) == {"tie-a", "tie-b"}
    # Characterizes repeated requests on this SQLite fixture, not a cross-DB tie guarantee.
    assert orders[0] == orders[1]
    _record(record_property, {"shape": "sequence and frozen ties", "orders": orders, "file_selects_per_request": 1})


@pytest.mark.parametrize(("raw", "expected"), [
    (None, None), ("{broken", {}), ("[]", []), ('"scalar"', "scalar"), ("null", None),
])
async def test_legacy_metadata_response_shape(tree_probe, raw, expected, record_property):
    tree_probe.seed([_row(tree_probe.project.id, "metadata", file_metadata=raw)])
    response, statements = await tree_probe.get()
    assert response.status_code == 200
    _assert_projection(statements, False)
    assert response.json()["tree"][0]["metadata"] == expected
    _record(record_property, {"raw_metadata": raw, "returned_metadata": expected, "status": 200, "file_selects": 1})


@pytest.mark.parametrize("include_content", [False, True])
@pytest.mark.parametrize("metadata", [
    {"中文": '引号"与反斜杠\\', "nested": [True, None, {"control": "\n\t\u0000"}]},
    ["中文", {"value": 1.5}, False, None],
    '标量"\\\n',
    None,
], ids=["dict", "list", "scalar", "null"])
async def test_shallow_raw_json_parity_and_headers(tree_probe, metadata, include_content, record_property):
    unusual_text = '中文 "quotes" \\ backslash\n\r\t\b\f\u0000 preamble'
    rows = [
        _row(tree_probe.project.id, "parity-root", title=unusual_text, content=unusual_text,
             file_metadata=json.dumps(metadata, ensure_ascii=False)),
        _row(tree_probe.project.id, "child-one", parent_id="parity-root", order=1,
             title=unusual_text, content=unusual_text),
        _row(tree_probe.project.id, "child-two", parent_id="parity-root", order=2),
        _row(tree_probe.project.id, "second-root", order=10),
    ]
    tree_probe.seed(rows)
    response, statements = await tree_probe.get(include_content=include_content)
    assert response.status_code == 200
    _assert_projection(statements, include_content)
    expected_nodes = [{
        "id": row["id"], "title": row["title"], "file_type": row["file_type"],
        "parent_id": row["parent_id"], "order": row["order"],
        "created_at": BASE_TIME.replace(tzinfo=None).isoformat(),
        "content": row["content"] if include_content else "",
        "metadata": json.loads(row["file_metadata"]) if row["file_metadata"] else None,
        "children": [],
    } for row in rows]
    expected_nodes[0]["children"] = expected_nodes[1:3]
    expected = {"tree": [expected_nodes[0], expected_nodes[3]]}
    raw = json.dumps(
        expected, ensure_ascii=False, allow_nan=False, indent=None, separators=(",", ":"),
    ).encode("utf-8")
    assert response.content == raw
    assert response.json() == expected
    assert response.headers["content-type"] == "application/json"
    assert int(response.headers["content-length"]) == len(raw)
    _record(record_property, {
        "shape": "exact shallow JSON parity", "include_content": include_content,
        "metadata_type": type(metadata).__name__, "response_nodes": 4,
        "response_bytes": len(raw), "status": 200, "file_selects": 1,
    })


async def test_nan_metadata_keeps_http500_rejection(tree_probe, caplog, record_property):
    tree_probe.seed([_row(tree_probe.project.id, "nan", file_metadata='{"value": NaN}')])
    response, statements = await tree_probe.get()
    assert response.status_code == 500
    _assert_projection(statements, False)
    failure = await _server_failure(tree_probe, False, caplog)
    assert failure["type"] == "ValueError"
    assert "Out of range float values are not JSON compliant" in failure["message"]
    _record(record_property, {
        "shape": "NaN JSON rejected", "status": 500, "file_selects": 1,
        "server_exception": failure,
    })
