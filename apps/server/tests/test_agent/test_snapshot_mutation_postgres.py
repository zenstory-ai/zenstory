"""Exact streamed content-change outcome on a real isolated PostgreSQL database."""

import os

import pytest
from sqlmodel import Session

from agent.stream_adapter import StreamAdapter, StreamAdapterConfig
from agent.tools.file_ops import FileCRUD
from models import File, Project, User
from services.features.file_version_service import FileVersionService
from tests.test_services.test_snapshot_concurrency_postgres import pg_engine as pg_engine

pytestmark = pytest.mark.skipif(
    not os.getenv("ZENSTORY_TEST_POSTGRES_URL"), reason="isolated PostgreSQL URL not configured",
)


@pytest.mark.asyncio
@pytest.mark.parametrize("content,expected", [("Body", False), ("Changed", True)])
async def test_streamed_mutation_tracks_locked_postgres_commit(pg_engine, monkeypatch, content, expected):
    import database

    monkeypatch.setattr(database, "is_postgres", True)
    monkeypatch.setattr(database, "create_session", lambda: Session(pg_engine))
    monkeypatch.setattr(FileCRUD, "_schedule_index_upsert", lambda *_a, **_kw: None)
    monkeypatch.setattr(FileVersionService, "check_user_version_quota", lambda *_a, **_kw: (True, 0, 10))
    monkeypatch.setattr("agent.tools.file_ops.crud.activation_event_service.record_ai_write_accepted", lambda *_a, **_kw: None)
    with Session(pg_engine) as session:
        user = User(username=f"stream-mutation-{expected}", email=f"stream-mutation-{expected}@example.test", hashed_password="unused")
        session.add(user)
        session.flush()
        project = Project(name="Stream mutation", owner_id=user.id)
        session.add(project)
        session.flush()
        file = File(project_id=project.id, title="Draft", file_type="draft", content="Body")
        session.add(file)
        session.commit()
        user_id, project_id, file_id = user.id, project.id, file.id

    adapter = StreamAdapter(StreamAdapterConfig(user_id=user_id, project_id=project_id))
    assert await adapter._save_file_content(file_id, content) is True

    async def empty_source():
        if False:
            yield

    events = [event async for event in adapter.process_workflow_events(empty_source())]
    assert events[-1].data["file_mutated"] is expected
    with Session(pg_engine) as verify:
        assert verify.get(File, file_id).content == content
