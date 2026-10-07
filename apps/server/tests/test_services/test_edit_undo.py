"""An edit card may restore only its own anchored version and committed token."""

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import event
from sqlmodel import Session, select

from agent.core.message_manager import MessageManager
from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.stream_adapter import StreamAdapter
from agent.tools import mcp_tools
from agent.tools.file_ops.crud import FileCRUD
from agent.tools.file_ops.edit import FileEditor
from api import agent_api, files, versions
from config.datetime_utils import normalize_datetime_to_utc
from main import app
from models import ChatMessage, File, FileVersion, Project, User
from models.agent_api_key import AgentApiKey
from services.features.file_version_service import FileVersionService, get_file_version_service
from services.features.snapshot_service import VersionService

STAMP = datetime(2035, 1, 1, 12, 0, 0, 123456, tzinfo=UTC)


@pytest.fixture
def undo_file(db_session, monkeypatch):
    user = User(username="edit-undo", email="edit-undo@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="Edit undo", owner_id=user.id)
    file = File(project_id=project.id, title="Draft", file_type="draft", content="Before\n", updated_at=STAMP)
    key = AgentApiKey(user_id=user.id, key_prefix="undo-key", key_hash=f"edit-undo-{user.id}", name="Undo writer", scopes=["read", "write"])
    db_session.add_all([user, project, file, key])
    db_session.flush()
    get_file_version_service().create_initial_version(db_session, file)
    db_session.commit()
    monkeypatch.setitem(app.dependency_overrides, versions.get_current_active_user, lambda: user)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    monkeypatch.setattr(files.activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(files.activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_a, **_kw: None)
    indexed, cache_bumps = [], []
    monkeypatch.setattr("services.llama_index.schedule_index_upsert", lambda **kw: indexed.append(kw))
    monkeypatch.setattr("services.infra.dashboard_cache.dashboard_cache.bump_project_version", lambda *_a, **kw: cache_bumps.append(kw))
    return user, project, file, key, indexed, cache_bumps


def _count_versions(session, file_id):
    return len(session.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all())


def _edit(session, user, file):
    return FileEditor(session, user.id).edit_file(file.id, [{"op": "append", "text": "AI\n"}])


def test_edit_emits_exact_before_version_and_committed_after_token(db_session, undo_file):
    user, _, file, *_ = undo_file
    result = _edit(db_session, user, file)
    db_session.refresh(file)
    assert result["undo"] == {
        "before_version_number": 1,
        "expected_after_updated_at": normalize_datetime_to_utc(file.updated_at).isoformat(),
    }
    assert file.content == "Before\nAI\n"
    assert normalize_datetime_to_utc(file.updated_at) > STAMP
    assert _count_versions(db_session, file.id) == 2


@pytest.mark.parametrize("conditional", [False, True])
async def test_missing_rollback_version_matches_content_and_compare_404_without_effects(client, db_session, undo_file, conditional):
    user, _, file, _, indexed, cache_bumps = undo_file
    token = normalize_datetime_to_utc(file.updated_at).isoformat()
    before = (file.content, token, _count_versions(db_session, file.id))
    kwargs = {"json": {"expected_updated_at": token}} if conditional else {}
    restored = await client.post(f"/api/v1/files/{file.id}/versions/999/rollback", **kwargs)
    content = await client.get(f"/api/v1/files/{file.id}/versions/999/content")
    compared = await client.get(f"/api/v1/files/{file.id}/versions/compare?v1=1&v2=999")
    assert [(r.status_code, r.json()["error_code"]) for r in (restored, content, compared)] == [(404, "ERR_VERSION_NOT_FOUND")] * 3
    db_session.refresh(file)
    assert (file.content, normalize_datetime_to_utc(file.updated_at).isoformat(), _count_versions(db_session, file.id)) == before
    assert indexed == cache_bumps == []


@pytest.mark.parametrize("reason", ["missing", "mismatched", "broken_replay"])
def test_unanchored_edits_commit_without_an_undo_descriptor(db_session, undo_file, monkeypatch, reason):
    user, _, file, *_ = undo_file
    if reason == "missing":
        db_session.delete(get_file_version_service().get_latest_version(db_session, file.id))
        db_session.commit()
    elif reason == "mismatched":
        file.content = "Live content without history\n"
        db_session.add(file)
        db_session.commit()
    else:
        original = FileVersionService._get_contents_for_versions
        calls = []

        def fail_first(self, session, targets):
            calls.append(targets)
            if len(calls) == 1:
                raise ValueError("Corrupt prior replay")
            return original(self, session, targets)

        monkeypatch.setattr(FileVersionService, "_get_contents_for_versions", fail_first)
    original_content = file.content
    result = _edit(db_session, user, file)
    assert result.get("undo") is None
    db_session.refresh(file)
    assert file.content == original_content + "AI\n"
    assert normalize_datetime_to_utc(file.updated_at) > STAMP


@pytest.mark.parametrize("edits", [[{"op": "append", "text": ""}], [{"op": "delete", "old": "absent", "match_mode": "exact"}]])
def test_noop_or_all_failed_edits_do_not_offer_undo(db_session, undo_file, edits):
    user, _, file, *_ = undo_file
    result = FileEditor(db_session, user.id).edit_file(file.id, edits, continue_on_error=True)
    assert result.get("undo") is None
    db_session.refresh(file)
    assert file.content == "Before\n"
    assert normalize_datetime_to_utc(file.updated_at) == STAMP
    assert _count_versions(db_session, file.id) == 1


def test_edit_can_offer_anchored_undo_when_optional_after_history_fails(db_session, undo_file, monkeypatch):
    user, _, file, *_ = undo_file
    monkeypatch.setattr(FileEditor, "_create_edit_version", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("History unavailable")))
    result = _edit(db_session, user, file)
    db_session.refresh(file)
    assert result["undo"]["before_version_number"] == 1
    assert result["undo"]["expected_after_updated_at"] == normalize_datetime_to_utc(file.updated_at).isoformat()
    assert file.content == "Before\nAI\n"
    assert _count_versions(db_session, file.id) == 1


async def test_conditional_rollback_uses_exact_token_and_second_click_conflicts(client, db_session, undo_file):
    user, _, file, _, indexed, cache_bumps = undo_file
    _edit(db_session, user, file)
    db_session.refresh(file)
    token = normalize_datetime_to_utc(file.updated_at).isoformat()
    url = f"/api/v1/files/{file.id}/versions/1/rollback"
    first = await client.post(url, json={"expected_updated_at": token})
    assert first.status_code == 200
    assert first.json()["snapshot_created"] is True
    assert first.json()["version_quota_exceeded"] is False
    db_session.refresh(file)
    assert file.content == "Before\n"
    restored_token = normalize_datetime_to_utc(file.updated_at)
    count = _count_versions(db_session, file.id)
    indexed.clear()
    cache_bumps.clear()
    second = await client.post(url, json={"expected_updated_at": token})
    assert second.status_code == 409
    assert second.json()["error_code"] == "ERR_RESOURCE_CONFLICT"
    db_session.refresh(file)
    assert file.content == "Before\n"
    assert normalize_datetime_to_utc(file.updated_at) == restored_token
    assert _count_versions(db_session, file.id) == count
    assert indexed == cache_bumps == []


@pytest.mark.parametrize("writer", ["web", "agent", "tool_edit", "tool_title", "snapshot"])
async def test_intervening_writes_reject_old_undo_without_success_effects(client, db_session, undo_file, writer):
    user, project, file, key, indexed, cache_bumps = undo_file
    _edit(db_session, user, file)
    db_session.refresh(file)
    token = normalize_datetime_to_utc(file.updated_at).isoformat()
    if writer == "web":
        files.update_file(file.id, files.FileUpdate(content="New web content"), BackgroundTasks(), current_user=user, session=db_session)
    elif writer == "agent":
        agent_api.update_file(file.id, agent_api.FileUpdate(content="New agent content"), BackgroundTasks(), _rate_limit=0, context=(db_session, user.id, key))
    elif writer == "tool_edit":
        FileEditor(db_session, user.id).edit_file(file.id, [{"op": "append", "text": "Later tool edit"}])
    elif writer == "tool_title":
        FileCRUD(db_session, user.id).update_file(file.id, title="Renamed after AI edit")
    else:
        service = VersionService()
        snapshot = service.create_snapshot(db_session, project.id, file_id=file.id)
        service.rollback_to_snapshot(db_session, snapshot.id)
    db_session.refresh(file)
    expected = (file.content, file.title, file.updated_at, _count_versions(db_session, file.id))
    indexed.clear()
    cache_bumps.clear()
    response = await client.post(f"/api/v1/files/{file.id}/versions/1/rollback", json={"expected_updated_at": token})
    assert response.status_code == 409
    db_session.refresh(file)
    assert (file.content, file.title, file.updated_at, _count_versions(db_session, file.id)) == expected
    assert indexed == cache_bumps == []


@pytest.mark.parametrize("difference", [-1, 1, 1_000_000])
async def test_undo_requires_exact_equality_not_only_a_newer_current_stamp(client, db_session, undo_file, difference):
    user, _, file, _, indexed, cache_bumps = undo_file
    _edit(db_session, user, file)
    db_session.refresh(file)
    current = normalize_datetime_to_utc(file.updated_at)
    count = _count_versions(db_session, file.id)
    response = await client.post(f"/api/v1/files/{file.id}/versions/1/rollback", json={"expected_updated_at": (current + timedelta(microseconds=difference)).isoformat()})
    assert response.status_code == 409
    payload = response.json()
    assert payload["error_code"] == "ERR_RESOURCE_CONFLICT"
    assert payload["error_detail"]["current_updated_at"] == current.isoformat()
    db_session.refresh(file)
    assert file.content == "Before\nAI\n"
    assert normalize_datetime_to_utc(file.updated_at) == current
    assert _count_versions(db_session, file.id) == count
    assert indexed == cache_bumps == []


@pytest.mark.parametrize("token", ["", "invalid-date"])
async def test_invalid_undo_token_is_rejected_before_restore(client, db_session, undo_file, token):
    user, _, file, *_ = undo_file
    _edit(db_session, user, file)
    response = await client.post(f"/api/v1/files/{file.id}/versions/1/rollback", json={"expected_updated_at": token})
    assert response.status_code == 422
    db_session.refresh(file)
    assert file.content == "Before\nAI\n"


@pytest.mark.parametrize("body", [None, "equivalent-offset"])
async def test_history_rollback_compatibility_and_equivalent_utc_offset(client, db_session, undo_file, body):
    user, _, file, *_ = undo_file
    _edit(db_session, user, file)
    db_session.refresh(file)
    if body is None:
        response = await client.post(f"/api/v1/files/{file.id}/versions/1/rollback")
    else:
        from datetime import timezone

        token = normalize_datetime_to_utc(file.updated_at).astimezone(timezone(timedelta(hours=8))).isoformat()
        response = await client.post(f"/api/v1/files/{file.id}/versions/1/rollback", json={"expected_updated_at": token})
    assert response.status_code == 200
    db_session.refresh(file)
    assert file.content == "Before\n"


@pytest.mark.parametrize("omission", ["quota", "history_failure"])
async def test_conditional_rollback_preserves_content_and_reports_optional_history_omission(client, db_session, undo_file, monkeypatch, omission):
    user, _, file, *_ = undo_file
    _edit(db_session, user, file)
    db_session.refresh(file)
    token = normalize_datetime_to_utc(file.updated_at).isoformat()
    if omission == "quota":
        monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (False, 10, 10))
    else:
        monkeypatch.setattr(FileVersionService, "create_version", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("Restore history unavailable")))
    response = await client.post(f"/api/v1/files/{file.id}/versions/1/rollback", json={"expected_updated_at": token})
    assert response.status_code == 200
    payload = response.json()
    assert payload["snapshot_created"] is False
    assert payload["new_version_number"] is None
    assert payload["version_quota_exceeded"] is (omission == "quota")
    db_session.refresh(file)
    assert file.content == "Before\n"
    assert _count_versions(db_session, file.id) == 2


async def test_edit_descriptor_survives_actual_mcp_sse_and_chat_persistence(db_session, undo_file):
    user, project, file, *_ = undo_file
    engine = db_session.get_bind()
    mcp_tools.ToolContext.set_context(None, user.id, project.id, None, create_session_func=lambda: Session(engine))
    try:
        raw = await mcp_tools.edit_file({"id": file.id, "edits": [{"op": "append", "text": "AI\n"}]})
    finally:
        mcp_tools.ToolContext.clear_context()
    payload = json.loads(raw["content"][0]["text"])
    assert payload["status"] == "success"
    result = payload["data"]
    assert "undo" in result
    db_session.refresh(file)
    assert result["undo"]["expected_after_updated_at"] == normalize_datetime_to_utc(file.updated_at).isoformat()
    event = StreamEvent(type=StreamEventType.TOOL_RESULT, data={"name": "edit_file", "tool_use_id": "undo-edit", "result": raw})
    streamed = [item async for item in StreamAdapter()._process_workflow_event(event)]
    tool_result = next(item for item in streamed if item.type.value == "tool_result")
    assert tool_result.data["data"]["undo"] == result["undo"]
    manager = MessageManager(project.id, user.id)
    message_id = manager._save_messages_with_session(
        db_session, session_id=None, user_message="Edit", assistant_message="Edited",
        tool_calls=[{"id": "undo-edit", "name": "edit_file", "arguments": {"id": file.id}, "status": tool_result.data["status"], "result": tool_result.data["data"]}],
    )
    saved = db_session.get(ChatMessage, message_id)
    assert json.loads(saved.tool_calls)[0]["result"]["undo"] == result["undo"]


@pytest.mark.parametrize("delta", [False, True])
def test_undo_provenance_reuses_loaded_head_without_a_redundant_target_select(db_session, undo_file, delta):
    user, _, file, *_ = undo_file
    service = get_file_version_service()
    if delta:
        file.content += "Earlier edit\n"
        db_session.add(file)
        service.create_version(db_session, file.id, file.content, change_source="system", commit=False)
        db_session.commit()
    engine = db_session.get_bind()
    statements = []

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        result = _edit(db_session, user, file)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    start = next(index for index, statement in enumerate(statements) if statement.startswith("SAVEPOINT "))
    end = next(index for index, statement in enumerate(statements) if statement.startswith("RELEASE SAVEPOINT "))
    provenance_reads = [statement for statement in statements[start + 1:end] if statement.startswith("SELECT ") and "file_version" in statement]
    print(f"Undo provenance delta={delta}: {len(provenance_reads)} SELECTs plus read-savepoint pair")
    assert len(provenance_reads) == (3 if delta else 1)
    assert result["undo"]["before_version_number"] == (2 if delta else 1)
