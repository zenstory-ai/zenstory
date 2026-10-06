"""Automatic project checkpoints rely on committed writes, not UI event guesses."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlmodel import Session, select

from agent.core.events import done_event
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools import mcp_tools, parallel_executor
from agent.tools.file_ops import FileCRUD, FileEditor
from models import File, Project, User


def _payload(result):
    return json.loads(result["content"][0]["text"])


async def _drive(adapter, events):
    async def source():
        for event in events:
            yield event

    return [event async for event in adapter.process_workflow_events(source())]


def _tool_event(name, payload):
    return StreamEvent(type=StreamEventType.TOOL_RESULT, data={"name": name, "result": payload})


@pytest.fixture
def mutation_project(db_session, monkeypatch):
    user = User(email="mutation@example.com", username="mutation", hashed_password="test-hash")
    db_session.add(user)
    db_session.commit()
    project = Project(name="Mutation tests", owner_id=user.id, project_type="screenplay")
    db_session.add(project)
    db_session.commit()
    folder = File(id=f"{project.id}-script-folder", project_id=project.id, title="剧本", file_type="folder")
    db_session.add(folder)
    db_session.commit()
    mcp_tools.ToolContext.set_context(session=db_session, user_id=user.id, project_id=project.id, session_id=None)
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_a, **_kw: None)
    monkeypatch.setattr("agent.tools.file_ops.crud.activation_event_service.record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr("agent.tools.file_ops.edit.activation_event_service.record_ai_write_accepted", lambda *_a, **_kw: None)
    return user, project, folder


@pytest.mark.parametrize("content", ["", "Written body"])
def test_new_create_reports_committed_mutation(db_session, mutation_project, content):
    user, project, _ = mutation_project
    result = FileCRUD(db_session, user.id).create_file(project.id, "New draft", "draft", content=content)
    assert db_session.get(File, result["id"]).content == content
    assert result["mutation_applied"] is True


@pytest.mark.parametrize("file_type,order,expected", [("script", 3, False), ("draft", 3, True), ("script", 0, True)])
def test_reused_episode_reports_only_actual_promotion_or_order(db_session, mutation_project, file_type, order, expected):
    user, project, folder = mutation_project
    file = File(project_id=project.id, title="第3集", content="Existing body", file_type=file_type, parent_id=folder.id, order=order)
    db_session.add(file)
    db_session.commit()
    result = FileCRUD(db_session, user.id).create_file(project.id, "第3集", "script", parent_id=folder.id)
    assert result["id"] == file.id
    assert result["reused_existing"] is True
    assert result["mutation_applied"] is expected


@pytest.mark.parametrize("edits,expected", [
    ([], False),
    ([{"op": "append", "text": ""}], False),
    ([{"op": "replace", "old": "Body", "new": "Body"}], False),
    ([{"op": "replace", "old": "missing", "new": "new"}], False),
    ([{"op": "append", "text": " changed"}], True),
    ([{"op": "append", "text": " changed"}, {"op": "replace", "old": "missing", "new": "new"}], True),
])
def test_edit_reports_content_change_not_applied_count(db_session, mutation_project, edits, expected):
    user, project, _ = mutation_project
    file = File(project_id=project.id, title="Draft", content="Body", file_type="draft")
    db_session.add(file)
    db_session.commit()
    result = FileEditor(db_session, user.id).edit_file(file.id, edits, continue_on_error=True)
    assert result["mutation_applied"] is expected
    assert (db_session.get(File, file.id, populate_existing=True).content != "Body") is expected


@pytest.mark.parametrize("stale_content,new_content,expected", [("Body", "Body", False), ("Body", "Changed", True), ("Stale", "Body", False)])
def test_update_exposes_exact_locked_content_change(db_session, mutation_project, stale_content, new_content, expected):
    user, project, _ = mutation_project
    file = File(project_id=project.id, title="Draft", content=stale_content, file_type="draft")
    db_session.add(file)
    db_session.commit()
    with Session(db_session.get_bind()) as other:
        latest = other.get(File, file.id)
        latest.content = "Body"
        other.add(latest)
        other.commit()
    result = FileCRUD(db_session, user.id).update_file(file.id, content=new_content)
    assert result["content_changed"] is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name,changed", [("create_file", True), ("create_file", False), ("edit_file", True), ("edit_file", False), ("delete_file", True)])
async def test_direct_tool_envelopes_keep_explicit_mutation(monkeypatch, tool_name, changed):
    executor = MagicMock()
    result = {"id": "file", "content": "Body", "mutation_applied": changed}
    getattr(executor, tool_name).return_value = True if tool_name == "delete_file" else result
    monkeypatch.setattr(mcp_tools.ToolContext, "get_executor", lambda: executor)
    monkeypatch.setattr(mcp_tools.ToolContext, "_get_context", lambda: {"project_id": "project"})
    monkeypatch.setattr(mcp_tools, "_record_artifact_ledger", lambda **_kw: False)
    payload = _payload(await getattr(mcp_tools, tool_name)({"id": "file", "title": "Title", "content": "Body", "edits": []}))
    assert payload["mutation_applied"] is changed


@pytest.mark.asyncio
async def test_delete_failure_does_not_report_mutation(db_session, mutation_project):
    payload = _payload(await mcp_tools.delete_file({"id": "missing"}))
    assert payload["status"] == "error"
    assert payload.get("mutation_applied", False) is False


@pytest.mark.parametrize("changed", [True, False])
def test_large_result_retains_compact_mutation_signal(monkeypatch, changed):
    monkeypatch.setattr(mcp_tools, "TOOL_RESULT_MAX_CHARS", 512)
    monkeypatch.setattr(mcp_tools, "_persist_tool_result_overflow", lambda **_kw: None)
    payload = _payload(mcp_tools._make_result({"status": "success", "mutation_applied": changed, "data": {"content": "x" * 5000}}))
    assert payload["truncated"] is True
    assert payload["mutation_applied"] is changed


@pytest.mark.asyncio
@pytest.mark.parametrize("results,expected", [
    ([{"status": "success", "data": {"content": "read"}}], False),
    ([{"status": "error", "error": "failed", "mutation_applied": True}], False),
    ([{"status": "success", "mutation_applied": True, "data": {"content": "x" * 5000}}, {"status": "error", "error": "failed"}], True),
    ([{"status": "success", "mutation_applied": False}, {"status": "success", "mutation_applied": True}], True),
])
async def test_parallel_aggregates_before_task_and_outer_truncation(monkeypatch, results, expected):
    async def handler(params):
        return mcp_tools._make_result(results[params["index"]])

    monkeypatch.setattr(parallel_executor, "handle_query_files", handler)
    monkeypatch.setattr(mcp_tools, "TOOL_RESULT_MAX_CHARS", 512)
    monkeypatch.setattr(mcp_tools, "_persist_tool_result_overflow", lambda **_kw: None)
    payload = _payload(await parallel_executor.execute_parallel([
        {"type": "query_files", "description": "large" * 200, "params": {"index": i}} for i in range(len(results))
    ]))
    assert payload["mutation_applied"] is expected
    assert payload["truncated"] is True


@pytest.mark.asyncio
async def test_dropped_parallel_write_does_not_count(monkeypatch):
    calls = []

    async def handler(params):
        calls.append(params["write"])
        return mcp_tools._make_result({"status": "success", "mutation_applied": params["write"]})

    monkeypatch.setattr(parallel_executor, "handle_query_files", handler)
    tasks = [{"type": "query_files", "description": "Read", "params": {"write": False}} for _ in range(parallel_executor.MAX_PARALLEL_TASKS)]
    tasks.append({"type": "query_files", "description": "Dropped write", "params": {"write": True}})
    payload = _payload(await parallel_executor.execute_parallel(tasks))
    assert calls == [False] * parallel_executor.MAX_PARALLEL_TASKS
    assert payload["data"]["dropped"] == 1
    assert payload["mutation_applied"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,expected", [
    ({"status": "success", "data": {}}, False),
    ({"status": "success", "mutation_applied": False}, False),
    ({"status": "success", "mutation_applied": "true"}, False),
    ({"status": "success", "mutation_applied": True}, True),
    ({"status": "partial", "mutation_applied": True}, True),
    ({"status": "error", "mutation_applied": True}, False),
    ({"status": "success", "mutation_applied": True, "truncated": True}, True),
])
async def test_done_carries_only_confirmed_mutation_and_resets(payload, expected):
    adapter = StreamAdapter(StreamAdapterConfig(process_file_markers=False))
    events = await _drive(adapter, [_tool_event("parallel_execute", payload), _tool_event("query_files", {"status": "success"})])
    done = [event for event in events if event.type == "done"]
    assert len(done) == 1
    assert done[0].data["file_mutated"] is expected
    adapter.reset()
    reset_done = await _drive(adapter, [StreamEvent(type=StreamEventType.TEXT, data={"text": "AI prose"})])
    assert reset_done[-1].data["file_mutated"] is False


def test_done_default_is_conservatively_false():
    assert done_event().data["file_mutated"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("new_content,expected", [("Body", False), ("Changed", True)])
@pytest.mark.parametrize("postgres_branch", [False, True])
async def test_streamed_save_marks_only_changed_committed_content(db_session, mutation_project, monkeypatch, new_content, expected, postgres_branch):
    import database

    user, project, _ = mutation_project
    file = File(project_id=project.id, title="Draft", content="Body", file_type="draft")
    db_session.add(file)
    db_session.commit()
    engine = db_session.get_bind()

    def get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(database, "get_session", get_session)
    monkeypatch.setattr(database, "create_session", lambda: Session(engine))
    monkeypatch.setattr(database, "is_postgres", postgres_branch)
    adapter = StreamAdapter(StreamAdapterConfig(project_id=project.id, user_id=user.id))
    assert await adapter._save_file_content(file.id, new_content) is True
    events = await _drive(adapter, [])
    assert events[-1].data["file_mutated"] is expected
    assert db_session.get(File, file.id, populate_existing=True).content == new_content


@pytest.mark.asyncio
async def test_abort_background_save_is_not_a_new_turn_mutation(monkeypatch):
    adapter = StreamAdapter(StreamAdapterConfig())
    saved = []

    def save(file_id, content):
        saved.append((file_id, content))
        return True, True

    monkeypatch.setattr(adapter, "_save_file_content_sync", save)
    async for _ in adapter._process_workflow_event(_tool_event("create_file", {
        "status": "success", "mutation_applied": False,
        "data": {"id": "file", "file_type": "draft", "title": "Draft", "content": ""},
    })):
        pass
    async for _ in adapter._process_workflow_event(StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>partial"})):
        pass
    task = adapter._persist_active_capture_in_background()
    assert task is not None
    adapter.reset()
    assert await task is True
    assert saved == [("file", "partial")]
    assert (await _drive(adapter, []))[-1].data["file_mutated"] is False


@pytest.mark.asyncio
async def test_stream_save_exception_is_not_mutation(monkeypatch):
    adapter = StreamAdapter(StreamAdapterConfig())
    monkeypatch.setattr(adapter, "_save_file_content_sync", MagicMock(side_effect=RuntimeError("db unavailable")))
    assert await adapter._save_file_content("file", "body") is False
    assert (await _drive(adapter, []))[-1].data["file_mutated"] is False


@pytest.mark.asyncio
async def test_refused_truncated_reuse_end_is_not_mutation():
    adapter = StreamAdapter(StreamAdapterConfig())
    adapter._save_file_content = AsyncMock(return_value=True)
    events = await _drive(adapter, [
        _tool_event("create_file", {"status": "success", "mutation_applied": False, "data": {
            "id": "file", "title": "第3集", "file_type": "script", "content": "Existing body", "reused_existing": True, "original_content_length": 13,
        }}),
        StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>partial"}),
    ])
    adapter._save_file_content.assert_not_awaited()
    assert any(event.type == "file_content_end" for event in events)
    assert events[-1].data["file_mutated"] is False


@pytest.mark.asyncio
async def test_closed_reused_file_stream_flips_false_tool_flag_after_commit(db_session, mutation_project, monkeypatch):
    import database

    user, project, folder = mutation_project
    file = File(project_id=project.id, title="第3集", content="Original body", file_type="script", parent_id=folder.id, order=3)
    db_session.add(file)
    db_session.commit()
    engine = db_session.get_bind()

    def get_session():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(database, "get_session", get_session)
    monkeypatch.setattr(database, "is_postgres", False)
    result = await mcp_tools.create_file({"title": "第3集", "file_type": "script", "parent_id": folder.id})
    payload = _payload(result)
    assert payload["data"]["id"] == file.id
    assert payload["mutation_applied"] is False
    adapter = StreamAdapter(StreamAdapterConfig(project_id=project.id, user_id=user.id))
    events = await _drive(adapter, [
        _tool_event("create_file", result),
        StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>Changed body</file>"}),
    ])
    assert db_session.get(File, file.id, populate_existing=True).content == "Changed body"
    assert events[-1].data["file_mutated"] is True
    assert sum(event.type == "file_content_end" for event in events) == 1


@pytest.mark.asyncio
async def test_oversized_partial_committed_edit_keeps_done_signal(monkeypatch):
    monkeypatch.setattr(mcp_tools, "TOOL_RESULT_MAX_CHARS", 512)
    monkeypatch.setattr(mcp_tools, "_persist_tool_result_overflow", lambda **_kw: None)
    result = mcp_tools._make_result({"status": "partial", "mutation_applied": True, "data": {"content": "x" * 5000}}, tool_name="edit_file")
    assert _payload(result)["status"] == "partial"
    assert _payload(result)["truncated"] is True
    adapter = StreamAdapter(StreamAdapterConfig())
    events = await _drive(adapter, [_tool_event("edit_file", result)])
    assert events[-1].data["file_mutated"] is True


@pytest.mark.asyncio
async def test_failed_stream_save_does_not_emit_normal_done():
    adapter = StreamAdapter(StreamAdapterConfig())
    adapter._save_file_content = AsyncMock(return_value=False)
    events = await _drive(adapter, [
        _tool_event("create_file", {"status": "success", "mutation_applied": True, "data": {"id": "file", "title": "Draft", "file_type": "draft", "content": ""}}),
        StreamEvent(type=StreamEventType.TEXT, data={"text": "<file>body</file>"}),
    ])
    assert any(event.type == "error" for event in events)
    assert not any(event.type == "done" for event in events)


def test_update_commit_failure_never_returns_success(db_session, mutation_project, monkeypatch):
    user, project, _ = mutation_project
    file = File(project_id=project.id, title="Draft", content="Body", file_type="draft")
    db_session.add(file)
    db_session.commit()
    monkeypatch.setattr(db_session, "commit", MagicMock(side_effect=RuntimeError("commit failed")))
    with pytest.raises(RuntimeError, match="commit failed"):
        FileCRUD(db_session, user.id).update_file(file.id, content="Changed")
    db_session.rollback()
    assert db_session.exec(select(File).where(File.id == file.id)).one().content == "Body"
