"""Actual persisted-order writer boundaries on a separately owned PostgreSQL DB."""

from __future__ import annotations

import json
import os
from datetime import datetime
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks
from pydantic import ValidationError
from sqlalchemy import create_engine, event
from sqlmodel import Session, SQLModel, select

from agent.tools.file_ops.crud import FileCRUD
from agent.tools.mcp_tools import ToolContext
from api import agent_api
from api import files as web
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import File, FileVersion, Project, Snapshot, User
from models.agent_api_key import AgentApiKey
from services.features.file_version_service import FileVersionService, get_file_version_service

MIN_ORDER = -2_147_483_648
MAX_ORDER = 2_147_483_647
STAMP = datetime(2026, 10, 6, 12, 0, 0, 123456)
TABLES = [User.__table__, Project.__table__, File.__table__, Snapshot.__table__,
          FileVersion.__table__, AgentApiKey.__table__]
pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="owned PostgreSQL URL required",
)


@pytest.fixture(scope="module")
def order_engine():
    engine = create_engine(os.environ["ZENSTORY_TEST_POSTGRES_URL"], connect_args={
        "options": "-c timezone=UTC -c statement_timeout=10000 -c lock_timeout=8000",
    })
    SQLModel.metadata.create_all(engine, tables=TABLES)
    try:
        yield engine
    finally:
        SQLModel.metadata.drop_all(engine, tables=TABLES)
        engine.dispose()


@pytest.fixture
def effects(monkeypatch):
    import database
    import services.llama_index as indexing
    from services.features.activation_event_service import activation_event_service
    from services.infra.dashboard_cache import dashboard_cache

    captured = {"index": [], "activation": [], "cache": []}
    monkeypatch.setattr(database, "is_postgres", True)
    monkeypatch.setattr(indexing, "schedule_index_upsert", lambda **kw: captured["index"].append(kw))
    monkeypatch.setattr(indexing, "schedule_index_delete", lambda **kw: captured["index"].append(kw))
    monkeypatch.setattr(activation_event_service, "record_once", lambda *a, **kw: captured["activation"].append(kw))
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *a, **kw: captured["activation"].append(kw))
    monkeypatch.setattr(dashboard_cache, "bump_project_version", lambda **kw: captured["cache"].append(kw))
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *a, **kw: (True, 0, 100))
    return captured


def _seed(engine, *, kind="plain"):
    with Session(engine) as session:
        suffix = uuid4().hex
        user = User(username=f"order-{suffix}", email=f"order-{suffix}@example.test", hashed_password="unused")
        session.add(user)
        session.flush()
        project = Project(name="Order boundaries", owner_id=user.id)
        foreign = Project(name="Separate order domain", owner_id=user.id)
        session.add_all([project, foreign])
        session.flush()
        a = File(project_id=project.id, title="Live ordinary A", file_type="folder")
        b = File(project_id=project.id, title="Live ordinary B", file_type="folder")
        session.add_all([a, b])
        session.flush()
        target = File(
            project_id=project.id, title=f"Chapter {MAX_ORDER + 1}" if kind == "title" else "Plain target",
            content="Original body", parent_id=a.id, file_type="draft", order=13,
            updated_at=STAMP,
            file_metadata=json.dumps({"chapter_number": MAX_ORDER + 1}) if kind == "metadata" else None,
        )
        peer = File(project_id=project.id, title="Mixed type peer", file_type="snippet", parent_id=a.id,
                    order=MAX_ORDER if kind in {"append", "title-max"} else 41)
        key = AgentApiKey(user_id=user.id, name="Order audit", key_prefix=suffix[:8], key_hash=suffix,
                          scopes=["read", "write"], project_ids=[project.id])
        session.add_all([target, peer, key])
        session.flush()
        get_file_version_service().create_initial_version(session, target)
        if kind == "scope":
            session.add_all([
                File(project_id=project.id, title="Other parent", parent_id=b.id, order=MAX_ORDER),
                File(project_id=project.id, title="Deleted peer", parent_id=a.id, order=MAX_ORDER, is_deleted=True),
                # FK-valid historical cross-project parent edge must not affect owned allocation.
                File(project_id=foreign.id, title="Foreign peer", parent_id=a.id, order=MAX_ORDER),
            ])
        session.commit()
        return {"user": user.id, "project": project.id, "target": target.id,
                "a": a.id, "b": b.id, "peer": peer.id, "key": key.id}


def _snapshot(engine, project_id):
    with Session(engine) as reader:
        return {
            "files": [row.model_dump(mode="json") for row in reader.exec(
                select(File).where(File.project_id == project_id).order_by(File.id)
            ).all()],
            "versions": [row.model_dump(mode="json") for row in reader.exec(
                select(FileVersion).where(FileVersion.project_id == project_id).order_by(FileVersion.id)
            ).all()],
        }


def _invoke(session, ids, writer, operation, payload, background):
    user = session.get(User, ids["user"])
    key = session.get(AgentApiKey, ids["key"])
    assert key.is_active and "write" in key.scopes and ids["project"] in key.project_ids
    if writer == "web":
        if operation == "create":
            return web.create_file(ids["project"], web.FileCreate(**payload), background, current_user=user, session=session)
        if operation == "update":
            return web.update_file(ids["target"], web.FileUpdate(**payload), background, current_user=user, session=session)
        if operation == "move":
            return web.move_file(ids["target"], web.MoveFileRequest(**payload), background, current_user=user, session=session)
        return web.reorder_files(ids["project"], web.ReorderFilesRequest(ordered_ids=[ids["target"], ids["peer"]]), current_user=user, session=session)
    if writer == "agent":
        context = (session, ids["user"], key)
        if operation == "create":
            return agent_api.create_file(ids["project"], agent_api.FileCreate(**payload), background, _rate_limit=0, context=context)
        if operation == "update":
            return agent_api.update_file(ids["target"], agent_api.FileUpdate(**payload), background, _rate_limit=0, context=context)
        return agent_api.move_file(ids["target"], agent_api.FileMove(**payload), background, _rate_limit=0, context=context)
    ToolContext.set_context(session, ids["user"], ids["project"], None)
    try:
        crud = FileCRUD(session, ids["user"])
        if operation == "create":
            return crud.create_file(ids["project"], **payload)
        return crud.update_file(ids["target"], **payload)
    finally:
        ToolContext.clear_context()


def _record(record_property, facts):
    value = json.dumps(facts, sort_keys=True, default=str)
    record_property("order_boundary", value)
    print(f"ORDER_BOUNDARY {value}")


INVALID_CASES = [
    (writer, "create", kind) for writer in ["web", "agent", "tool"]
    for kind in ["title", "metadata", "append"]
] + [
    (writer, "update", kind) for writer in ["web", "agent", "tool"]
    for kind in ["title", "metadata"]
] + [("agent", "move", kind) for kind in ["title", "metadata"]] + [
    ("web", "reorder", kind) for kind in ["title", "metadata"]
] + [(writer, operation, "below-min") for writer in ["web", "tool"] for operation in ["create", "update"]]


@pytest.mark.parametrize(("writer", "operation", "kind"), INVALID_CASES)
def test_invalid_persisted_order_is_domain_rejection_and_atomic(order_engine, effects, record_property, writer, operation, kind):
    ids = _seed(order_engine, kind=kind if operation in {"move", "reorder"} or kind == "append" else "plain")
    before = _snapshot(order_engine, ids["project"])
    if operation == "create":
        payload = {"title": "Plain new", "file_type": "draft", "content": "New body", "parent_id": ids["a"]}
    elif operation == "update":
        payload = {"content": "Changed body", "order": 0}
        if writer != "agent":
            payload["parent_id"] = ids["b"]
    elif operation == "move":
        payload = {"parent_id": ids["b"], "order": 0}
    else:
        payload = {}
    if kind == "title" and operation in {"create", "update"}:
        payload["title"] = f"Chapter {MAX_ORDER + 1}"
    if kind == "metadata" and operation in {"create", "update"}:
        if writer == "agent" and operation == "update":
            # Agent update has no metadata input; derive existing metadata with raworder0.
            with Session(order_engine) as setup:
                target = setup.get(File, ids["target"])
                target.file_metadata = json.dumps({"chapter_number": MAX_ORDER + 1})
                setup.commit()
            before = _snapshot(order_engine, ids["project"])
        else:
            payload["metadata"] = {"chapter_number": MAX_ORDER + 1}
    if kind == "below-min":
        payload["order"] = MIN_ORDER - 1
    sql_errors = []

    def capture(context):
        parameters = context.parameters
        sql_errors.append({
            "sqlstate": getattr(context.original_exception, "pgcode", None),
            "statement_kind": (context.statement or "").split(" ", 1)[0],
            "attempted_order": parameters.get("order") if isinstance(parameters, dict) else [
                item.get("order") for item in parameters if isinstance(item, dict)
            ] if isinstance(parameters, (list, tuple)) else None,
        })

    background = BackgroundTasks()
    error = None
    event.listen(order_engine, "handle_error", capture)
    try:
        with Session(order_engine) as session:
            try:
                _invoke(session, ids, writer, operation, payload, background)
            except Exception as exc:
                error = exc
            finally:
                session.rollback()  # Required before any inspection after PG failure.
    finally:
        event.remove(order_engine, "handle_error", capture)
    after = _snapshot(order_engine, ids["project"])
    _record(record_property, {
        "writer": writer, "operation": operation, "kind": kind,
        "exception": type(error).__name__ if error else None,
        "sql_errors": sql_errors, "all_persisted_fields_unchanged": after == before,
        "files_before_after": [len(before["files"]), len(after["files"])],
        "versions_before_after": [len(before["versions"]), len(after["versions"])],
        "target_before": next(f for f in before["files"] if f["id"] == ids["target"]),
        "target_after": next(f for f in after["files"] if f["id"] == ids["target"]),
        "background_tasks": len(background.tasks), "effects": {k: len(v) for k, v in effects.items()},
    })
    assert after == before, "Rejected order changed File/token/parent/content/metadata/history"
    assert background.tasks == []
    assert all(not calls for calls in effects.values())
    if writer == "tool":
        assert isinstance(error, ValueError), f"Expected tool input ValueError, got {type(error).__name__}; PG={sql_errors}"
    else:
        assert isinstance(error, APIException), f"Expected API validation error, got {type(error).__name__}; PG={sql_errors}"
        assert error.status_code == 400 and error.error_code == ErrorCode.VALIDATION_ERROR
    assert sql_errors == [], "Input-domain rejection must happen before out-of-range SQL"


CREATE_CONTROLS = [(writer, value) for writer in ["web", "agent", "tool"] for value in [17, MAX_ORDER]] + [
    (writer, value) for writer in ["web", "tool"] for value in [MIN_ORDER, -7]
] + [("agent", 0)]


@pytest.mark.parametrize(("writer", "order"), CREATE_CONTROLS)
def test_valid_explicit_create_bounds_keep_content_baseline(order_engine, effects, record_property, writer, order):
    ids = _seed(order_engine)
    before = _snapshot(order_engine, ids["project"])
    background = BackgroundTasks()
    with Session(order_engine) as session:
        result = _invoke(session, ids, writer, "create", {
            "title": "Plain created", "file_type": "draft", "content": "Created baseline body",
            "parent_id": ids["a"], "order": order,
        }, background)
        file_id = result["id"] if isinstance(result, dict) else result.id
    after = _snapshot(order_engine, ids["project"])
    row = next(f for f in after["files"] if f["id"] == file_id)
    assert row["order"] == order and row["content"] == "Created baseline body" and row["parent_id"] == ids["a"]
    versions = [v for v in after["versions"] if v["file_id"] == file_id]
    assert len(versions) == 1 and versions[0]["version_number"] == 1 and versions[0]["is_base_version"]
    assert versions[0]["content"] == "Created baseline body"
    assert len(after["files"]) == len(before["files"]) + 1
    assert len(background.tasks) + len(effects["index"]) == 1
    _record(record_property, {"writer": writer, "operation": "create", "control_order": order, "stored_order": row["order"], "initial_versions": 1})


@pytest.mark.parametrize("writer", ["web", "agent", "tool"])
@pytest.mark.parametrize("kind", ["title-max", "metadata-max", "scope"])
def test_valid_derived_create_order_and_sibling_domain(order_engine, effects, record_property, writer, kind):
    ids = _seed(order_engine, kind=kind)
    payload = {"title": f"Chapter {MAX_ORDER}" if kind == "title-max" else "Plain created",
               "file_type": "draft", "content": "Derived baseline", "parent_id": ids["a"]}
    if kind == "metadata-max":
        payload["metadata"] = {"chapter_number": MAX_ORDER}
    expected = 42 if kind == "scope" else MAX_ORDER
    with Session(order_engine) as session:
        result = _invoke(session, ids, writer, "create", payload, BackgroundTasks())
        file_id = result["id"] if isinstance(result, dict) else result.id
    after = _snapshot(order_engine, ids["project"])
    assert next(f for f in after["files"] if f["id"] == file_id)["order"] == expected
    assert len([v for v in after["versions"] if v["file_id"] == file_id]) == 1
    _record(record_property, {"writer": writer, "operation": "create", "control": kind, "stored_order": expected})


@pytest.mark.parametrize(("writer", "order"), [(w, MAX_ORDER) for w in ["web", "agent", "tool"]] + [(w, MIN_ORDER) for w in ["web", "tool"]])
def test_valid_update_bound_keeps_real_history_and_advances_token(order_engine, effects, record_property, writer, order):
    ids = _seed(order_engine)
    before = _snapshot(order_engine, ids["project"])
    original = next(f for f in before["files"] if f["id"] == ids["target"])
    background = BackgroundTasks()
    with Session(order_engine) as session:
        _invoke(session, ids, writer, "update", {"order": order, "content": "Updated control body"}, background)
    after = _snapshot(order_engine, ids["project"])
    changed = next(f for f in after["files"] if f["id"] == ids["target"])
    assert changed["order"] == order and changed["content"] == "Updated control body"
    assert changed["parent_id"] == original["parent_id"]
    assert changed["updated_at"] > original["updated_at"]
    assert len(after["versions"]) == len(before["versions"]) + 1
    assert len(background.tasks) + len(effects["index"]) == 1
    _record(record_property, {"writer": writer, "operation": "update", "control_order": order, "token_advanced": True, "history_added": 1})


@pytest.mark.parametrize("kind", ["title", "metadata"])
def test_web_move_does_not_resolve_order_from_legacy_title_metadata(order_engine, effects, record_property, kind):
    ids = _seed(order_engine, kind=kind)
    before = _snapshot(order_engine, ids["project"])
    old = next(f for f in before["files"] if f["id"] == ids["target"])
    background = BackgroundTasks()
    with Session(order_engine) as session:
        _invoke(session, ids, "web", "move", {"target_parent_id": ids["b"]}, background)
    after = _snapshot(order_engine, ids["project"])
    moved = next(f for f in after["files"] if f["id"] == ids["target"])
    assert moved["parent_id"] == ids["b"] and moved["order"] == 13 and moved["content"] == old["content"]
    assert moved["title"] == old["title"] and moved["file_metadata"] == old["file_metadata"]
    assert moved["updated_at"] > old["updated_at"] and after["versions"] == before["versions"]
    assert len(background.tasks) == 1
    _record(record_property, {"writer": "web", "operation": "move", "kind": kind, "stored_order": 13, "derived_order": False})


@pytest.mark.parametrize(("title", "expected"), [(f"Chapter {MAX_ORDER}", MAX_ORDER), ("Plain target", 17)])
def test_valid_agent_move_resolves_bound_and_advances_parent_token(order_engine, effects, record_property, title, expected):
    ids = _seed(order_engine)
    with Session(order_engine) as setup:
        setup.get(File, ids["target"]).title = title
        setup.commit()
    before = _snapshot(order_engine, ids["project"])
    background = BackgroundTasks()
    with Session(order_engine) as session:
        _invoke(session, ids, "agent", "move", {"parent_id": ids["b"], "order": 17}, background)
    after = _snapshot(order_engine, ids["project"])
    old = next(f for f in before["files"] if f["id"] == ids["target"])
    moved = next(f for f in after["files"] if f["id"] == ids["target"])
    assert moved["order"] == expected and moved["parent_id"] == ids["b"]
    assert moved["updated_at"] > old["updated_at"] and moved["content"] == old["content"]
    assert after["versions"] == before["versions"] and len(background.tasks) == 1
    _record(record_property, {"writer": "agent", "operation": "move", "control_title": title,
                              "stored_order": expected, "parent_token_advanced": True})


def test_valid_reorder_retains_title_max_and_other_sibling(order_engine, effects, record_property):
    ids = _seed(order_engine)
    with Session(order_engine) as setup:
        setup.get(File, ids["target"]).title = f"Chapter {MAX_ORDER}"
        setup.commit()
    before = _snapshot(order_engine, ids["project"])
    with Session(order_engine) as session:
        _invoke(session, ids, "web", "reorder", {}, BackgroundTasks())
    after = _snapshot(order_engine, ids["project"])
    target = next(f for f in after["files"] if f["id"] == ids["target"])
    peer = next(f for f in after["files"] if f["id"] == ids["peer"])
    assert target["order"] == MAX_ORDER and peer["order"] == 1
    assert target["content"] == "Original body" and after["versions"] == before["versions"]
    assert all(not v for v in effects.values())
    _record(record_property, {"writer": "web", "operation": "reorder", "control": "title-max", "stored_orders": [MAX_ORDER, 1]})


@pytest.mark.parametrize("model", [web.FileCreate, web.FileUpdate, agent_api.FileCreate, agent_api.FileUpdate, agent_api.FileMove])
def test_supplied_above_max_is_request_schema_rejection(model):
    payload = {"order": MAX_ORDER + 1}
    if model in {web.FileCreate, agent_api.FileCreate}:
        payload["title"] = "Plain supplied"
    if model is agent_api.FileMove:
        payload["parent_id"] = None
    with pytest.raises(ValidationError):
        model(**payload)


@pytest.mark.parametrize("model", [agent_api.FileCreate, agent_api.FileUpdate, agent_api.FileMove])
def test_agent_nonnegative_schema_policy_is_separate(model):
    payload = {"order": -7}
    if model is agent_api.FileCreate:
        payload["title"] = "Plain negative"
    if model is agent_api.FileMove:
        payload["parent_id"] = None
    with pytest.raises(ValidationError):
        model(**payload)


@pytest.mark.parametrize(("writer", "operation"), [(w, "update") for w in ["web", "agent", "tool"]] + [("agent", "move")])
def test_invalid_order_preserves_attached_fields_before_rollback(order_engine, effects, writer, operation):
    ids = _seed(order_engine, kind="metadata" if operation == "move" else "plain")
    before = _snapshot(order_engine, ids["project"])
    tasks = BackgroundTasks()
    with Session(order_engine) as session:
        row = session.get(File, ids["target"])
        attached_before = row.model_dump()
        attached_versions_before = [v.model_dump() for v in session.exec(
            select(FileVersion).where(FileVersion.project_id == ids["project"]).order_by(FileVersion.id)
        ).all()]
        payload = {"order": 0, "parent_id": ids["b"]} if operation == "move" else {
            "title": f"Chapter {MAX_ORDER + 1}", "content": "Prospective body", "order": 0,
        }
        if operation == "update" and writer != "agent":
            payload.update(parent_id=ids["b"], metadata={"custom": "prospective"})
        try:
            with pytest.raises(ValueError if writer == "tool" else APIException):
                _invoke(session, ids, writer, operation, payload, tasks)
            assert row.model_dump() == attached_before
            assert not session.dirty and not session.new
            assert [v.model_dump() for v in session.exec(
                select(FileVersion).where(FileVersion.project_id == ids["project"]).order_by(FileVersion.id)
            ).all()] == attached_versions_before
        finally:
            session.rollback()
    assert _snapshot(order_engine, ids["project"]) == before
    assert not tasks.tasks and all(not calls for calls in effects.values())


def test_later_invalid_reorder_preserves_earlier_attached_row(order_engine, effects):
    ids = _seed(order_engine)
    with Session(order_engine) as setup:
        setup.get(File, ids["peer"]).title = f"Chapter {MAX_ORDER + 1}"
        setup.get(File, ids["peer"]).file_type = "draft"
        setup.commit()
    before = _snapshot(order_engine, ids["project"])
    with Session(order_engine) as session:
        rows = [session.get(File, ids[k]) for k in ["target", "peer"]]
        attached_before = [row.model_dump() for row in rows]
        try:
            with pytest.raises(APIException):
                _invoke(session, ids, "web", "reorder", {}, BackgroundTasks())
            assert [row.model_dump() for row in rows] == attached_before
            assert not session.dirty and not session.new
        finally:
            session.rollback()
    assert _snapshot(order_engine, ids["project"]) == before
    assert all(not calls for calls in effects.values())


def test_invalid_tool_create_canonical_repair_remains_caller_rollback_owned(order_engine, effects):
    ids = _seed(order_engine)
    before = _snapshot(order_engine, ids["project"])
    canonical_id = f'{ids["project"]}-draft-folder'
    with Session(order_engine) as session:
        try:
            with pytest.raises(ValueError):
                _invoke(session, ids, "tool", "create", {
                    "title": f"Chapter {MAX_ORDER + 1}", "content": "Rejected", "file_type": "draft",
                    "parent_id": canonical_id,
                }, BackgroundTasks())
            assert session.get(File, canonical_id) is not None, "existing canonical repair may flush before allocation"
            assert session.is_active
            assert not session.new
        finally:
            session.rollback()
    assert _snapshot(order_engine, ids["project"]) == before
    assert all(not calls for calls in effects.values())


def test_invalid_screenplay_reuse_never_promotes_attached_candidate(order_engine, effects):
    ids = _seed(order_engine)
    title = f"第{MAX_ORDER + 1}集"
    script_parent_id = f'{ids["project"]}-script-folder'
    with Session(order_engine) as setup:
        setup.get(Project, ids["project"]).project_type = "screenplay"
        setup.add(File(id=script_parent_id, project_id=ids["project"], title="Scripts", file_type="folder"))
        setup.flush()
        target = setup.get(File, ids["target"])
        target.parent_id = script_parent_id
        target.title = title
        target.order = 0
        setup.commit()
    before = _snapshot(order_engine, ids["project"])
    with Session(order_engine) as session:
        candidate = session.get(File, ids["target"])
        attached_before = candidate.model_dump()
        try:
            with pytest.raises(ValueError):
                _invoke(session, ids, "tool", "create", {
                    "title": title, "content": "", "file_type": "draft", "parent_id": script_parent_id,
                }, BackgroundTasks())
            assert candidate.model_dump() == attached_before
            assert not session.dirty and not session.new
        finally:
            session.rollback()
    assert _snapshot(order_engine, ids["project"]) == before
    assert all(not calls for calls in effects.values())
