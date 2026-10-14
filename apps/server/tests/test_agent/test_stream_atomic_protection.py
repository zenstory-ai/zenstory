"""Atomic protection for full-body writes finalized by StreamAdapter."""

import json

import pytest
from sqlmodel import Session

from agent.core.workflow_events import StreamEvent, StreamEventType
from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools.file_ops.serialization import serialize_file
from agent.tools.mcp_tools import ToolContext
from api import files as files_api
from models import File, Project, User


@pytest.mark.asyncio
async def test_author_save_after_capture_baseline_blocks_stream_finalization(
    db_session: Session,
    monkeypatch,
):
    """A user save between create result and </file> must win without ledger mutation."""
    import database
    from tests.conftest import TestSessionLocal

    user = User(
        username="stream-race-owner",
        email="stream-race-owner@example.test",
        hashed_password="unused",
        email_verified=True,
    )
    project = Project(name="Stream race", owner_id=user.id)
    db_session.add_all([user, project])
    db_session.commit()
    target = File(project_id=project.id, title="第一章", file_type="draft", content="")
    db_session.add(target)
    db_session.commit()
    db_session.refresh(target)
    baseline = serialize_file(target)

    def get_session():
        session = TestSessionLocal()
        try:
            yield session
        finally:
            session.close()

    monkeypatch.setattr(database, "get_session", get_session)
    monkeypatch.setattr(database, "create_session", TestSessionLocal)
    monkeypatch.setattr(database, "is_postgres", False)
    from services.features.activation_event_service import activation_event_service

    monkeypatch.setattr(
        activation_event_service,
        "record_ai_write_accepted",
        lambda *_args, **_kwargs: None,
    )

    payload = json.dumps({"status": "success", "data": baseline}, ensure_ascii=False)
    create_result = StreamEvent(
        type=StreamEventType.TOOL_RESULT,
        data={
            "name": "create_file",
            "tool_use_id": "create-1",
            "result": {"content": [{"type": "text", "text": payload}]},
        },
    )

    async def events():
        yield create_result
        files_api.update_file(
            target.id,
            files_api.FileUpdate(content="作者刚保存的正文"),
            current_user=user,
            session=db_session,
        )
        yield StreamEvent(
            type=StreamEventType.TEXT,
            data={"text": "<file>模型准备整篇覆盖的正文</file>"},
        )
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    ToolContext.set_context(
        session=None,
        user_id=user.id,
        project_id=project.id,
        session_id="stream-race",
    )
    adapter = StreamAdapter(StreamAdapterConfig(project_id=project.id, user_id=user.id))
    try:
        emitted = [event async for event in adapter.process_workflow_events(events())]
    finally:
        ToolContext.clear_context()

    db_session.expire_all()
    stored = db_session.get(File, target.id)
    assert stored.content == "作者刚保存的正文"
    assert adapter._file_mutated is False
    assert any(event.type.value == "error" for event in emitted)

