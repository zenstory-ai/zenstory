"""Only committed new bodies expose the same durable undo in live and replayed cards."""

import json

import pytest
from sqlmodel import Session

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.service import _stamp_body_receipts
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools.mcp_tools import ToolContext
from models import File, Project, User
from models.file_edit_operation import FileEditOperation
from services.features.activation_event_service import activation_event_service
from services.features.file_version_service import FileVersionService
from services.file_edit_operations import undo_edit_operation

BODY = "雨停了。她推开门，沿着河岸走向渡口。"


@pytest.fixture
def new_body(db_session, monkeypatch):
    user = User(username="new-body-owner", email="new-body@example.test", hashed_password="unused")
    project = Project(name="新正文撤销", owner_id=user.id)
    file = File(project_id=project.id, title="第1章 渡口", file_type="draft", content="", order=3)
    db_session.add_all([user, project, file])
    db_session.commit()
    engine = db_session.get_bind()

    def with_session(fn):
        with Session(engine) as session:
            return fn(session)

    monkeypatch.setattr("agent.stream_adapter._with_sync_session", with_session)
    monkeypatch.setattr(activation_event_service, "record_ai_write_accepted", lambda *_a, **_kw: None)
    monkeypatch.setattr(activation_event_service, "record_once", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    ToolContext.set_context(None, user.id, project.id, None, create_session_func=lambda: Session(engine))
    yield user, project, file
    ToolContext.clear_context()


def _card(file, tool_name):
    return {
        "id": file.id, "title": file.title, "file_type": file.file_type,
        "awaiting_body": True,
        **({"action": "created", "chapter": 1} if tool_name == "write_chapter" else {}),
    }


async def _arm(adapter, file, tool_name):
    card = _card(file, tool_name)
    raw = {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": card})}]}
    events = [event async for event in adapter._handle_workflow_tool_result(
        {"name": tool_name, "tool_use_id": "created-call", "result": raw}
    )]
    assert "undo" not in next(event.data["data"] for event in events if event.type.value == "tool_result")
    return card


async def _body(adapter, *, complete=True):
    async def workflow():
        yield StreamEvent(StreamEventType.TEXT, {"text": f"<file>{BODY}" + ("</file>" if complete else "")})
        if complete:
            yield StreamEvent(StreamEventType.MESSAGE_END, {"stop_reason": "end_turn"})
    return [event async for event in adapter.process_workflow_events(workflow())]


@pytest.mark.parametrize("tool_name", ["create_file", "write_chapter"])
async def test_live_new_body_card_undo_restores_blank_without_deleting(db_session, new_body, tool_name):
    user, project, file = new_body
    adapter = StreamAdapter(StreamAdapterConfig(user_id=user.id, project_id=project.id))
    await _arm(adapter, file, tool_name)
    events = await _body(adapter)
    receipt = next(event.data for event in events if event.type.value == "tool_result")
    assert receipt["tool_use_id"] == "created-call" and receipt["status"] == "success"
    assert receipt["data"]["body_saved"] is True
    undo = receipt["data"]["undo"]
    assert adapter.get_created_body_undos() == {"created-call": undo}
    db_session.expire_all()
    assert db_session.get(File, file.id).content == BODY
    assert db_session.get(FileEditOperation, undo["operation_id"]).file_id == file.id

    restored = undo_edit_operation(db_session, operation_id=undo["operation_id"], file_id=file.id, user_id=user.id)
    assert restored["status"] == "undone"
    db_session.refresh(file)
    assert (file.content, file.title, file.parent_id, file.order, file.is_deleted) == ("", "第1章 渡口", None, 3, False)


@pytest.mark.parametrize("tool_name", ["create_file", "write_chapter"])
async def test_body_before_creation_tool_receives_its_operation(db_session, new_body, tool_name):
    user, project, file = new_body
    adapter = StreamAdapter(StreamAdapterConfig(user_id=user.id, project_id=project.id))
    card = {**_card(file, tool_name), "body_in_reply": True}
    raw = {"content": [{"type": "text", "text": json.dumps({"status": "success", "data": card})}]}

    async def workflow():
        yield StreamEvent(StreamEventType.TEXT, {"text": f"<file>\n{BODY}\n</file>\n"})
        yield StreamEvent(StreamEventType.TOOL_RESULT, {
            "name": tool_name, "tool_use_id": "created-call", "result": raw,
        })
        yield StreamEvent(StreamEventType.MESSAGE_END, {"stop_reason": "end_turn"})

    events = [event async for event in adapter.process_workflow_events(workflow())]
    undo = adapter.get_created_body_undos()["created-call"]
    assert any(event.type.value == "tool_result" and event.data.get("data", {}).get("undo") == undo for event in events)
    db_session.expire_all()
    assert db_session.get(File, file.id).content == BODY + "\n"
    assert db_session.get(FileEditOperation, undo["operation_id"]).file_id == file.id


@pytest.mark.parametrize("tool_name", ["create_file", "write_chapter"])
async def test_stopped_new_body_history_gets_its_committed_undo(db_session, new_body, tool_name):
    user, project, file = new_body
    adapter = StreamAdapter(StreamAdapterConfig(user_id=user.id, project_id=project.id))
    card = await _arm(adapter, file, tool_name)
    # A partial stream has no live completed card; stop settles it before history stamping.
    await _body(adapter, complete=False)
    receipts = await adapter.settle_aborted_capture()
    calls = [{"id": "created-call", "name": tool_name, "status": "success", "result": card}]
    _stamp_body_receipts(calls, receipts, {}, adapter.get_created_body_undos())
    assert calls[0]["result"]["body_saved"] is True
    assert calls[0]["result"]["new_word_count"] == receipts[file.id]
    operation_id = calls[0]["result"]["undo"]["operation_id"]
    db_session.expire_all()
    assert db_session.get(File, file.id).content == BODY
    assert db_session.get(FileEditOperation, operation_id).file_id == file.id


async def test_failed_body_save_never_exposes_undo_or_saved_receipt(new_body, monkeypatch):
    user, project, file = new_body
    adapter = StreamAdapter(StreamAdapterConfig(user_id=user.id, project_id=project.id))
    card = await _arm(adapter, file, "write_chapter")

    def fail(*_a, **_kw):
        raise RuntimeError("required history unavailable")

    monkeypatch.setattr(adapter, "_save_file_content_sync", fail)
    events = await _body(adapter)
    assert any(event.type.value == "error" for event in events)
    assert adapter.get_created_body_undos() == {}
    calls = [{"id": "created-call", "name": "write_chapter", "status": "success", "result": card}]
    _stamp_body_receipts(calls, adapter.get_saved_file_word_counts(), {}, adapter.get_created_body_undos())
    assert calls[0]["result"]["body_saved"] is False
    assert "undo" not in calls[0]["result"]


def test_history_undo_is_bound_to_the_creation_call_not_only_file_id():
    calls = [{"id": "later-call", "name": "create_file", "status": "success", "result": {
        "id": "same-file", "awaiting_body": True,
    }}]
    _stamp_body_receipts(calls, {"same-file": 10}, {}, {"earlier-call": {"operation_id": "old-operation"}})
    assert "undo" not in calls[0]["result"]
