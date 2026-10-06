"""Agent API conditional writes must be checked inside the write transaction."""
import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
from threading import Barrier, get_ident
from unittest.mock import Mock

import pytest
from fastapi import BackgroundTasks
from sqlmodel import Session, select

from api import agent_api
from config.datetime_utils import normalize_datetime_to_utc
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import File, FileVersion, Project, User
from models.agent_api_key import AgentApiKey
from services.agent_auth_service import generate_api_key, hash_api_key

STAMP = datetime(2026, 1, 2, 0, 0, 0, 123456, tzinfo=UTC)


@pytest.fixture
def conditional_file(db_session):
    user = User(username="conditional-owner", email="conditional-owner@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="Conditional writes", owner_id=user.id)
    plain_key = generate_api_key()
    key = AgentApiKey(user_id=user.id, key_prefix=plain_key[:8], key_hash=hash_api_key(plain_key), name="Conditional", scopes=["read", "write"])
    file = File(project_id=project.id, title="Draft", file_type="draft", content="Original", updated_at=STAMP)
    db_session.add_all([user, project, key, file])
    db_session.commit()
    return file, {"X-Agent-API-Key": plain_key}


@pytest.mark.parametrize("expected", [STAMP - timedelta(seconds=1), STAMP + timedelta(seconds=1), STAMP - timedelta(microseconds=1)])
async def test_stale_agent_write_changes_nothing(client, db_session, conditional_file, monkeypatch, expected):
    file, headers = conditional_file
    schedule = Mock()
    monkeypatch.setattr(agent_api, "_schedule_file_index_upsert", schedule)
    response = await client.put(f"/api/v1/agent/files/{file.id}", headers=headers, json={
        "title": "Wrong", "content": "Stale replacement", "order": 9,
        "base_updated_at": expected.isoformat(),
    })

    assert response.status_code == 409
    assert response.json()["error_code"] == ErrorCode.RESOURCE_CONFLICT
    db_session.refresh(file)
    assert (file.title, file.content, file.order) == ("Draft", "Original", 0)
    assert normalize_datetime_to_utc(file.updated_at) == STAMP
    assert db_session.exec(select(FileVersion).where(FileVersion.file_id == file.id)).all() == []
    schedule.assert_not_called()


@pytest.mark.parametrize("expected", [STAMP.isoformat(), STAMP.replace(tzinfo=None).isoformat(), STAMP.astimezone(timezone(timedelta(hours=8))).isoformat()])
async def test_matching_agent_write_accepts_normalized_timestamp(client, db_session, conditional_file, monkeypatch, expected):
    file, headers = conditional_file
    monkeypatch.setattr(agent_api, "_schedule_file_index_upsert", Mock())
    response = await client.put(f"/api/v1/agent/files/{file.id}", headers=headers, json={"title": "Accepted", "base_updated_at": expected})
    assert response.status_code == 200
    db_session.refresh(file)
    assert file.title == "Accepted"


async def test_conditional_agent_write_advances_token_with_frozen_clock(client, db_session, conditional_file, monkeypatch):
    file, headers = conditional_file
    monkeypatch.setattr(agent_api, "utcnow", lambda: STAMP)
    monkeypatch.setattr(agent_api, "_schedule_file_index_upsert", Mock())
    body = {"content": "Accepted", "base_updated_at": STAMP.isoformat()}
    first = await client.put(f"/api/v1/agent/files/{file.id}", headers=headers, json=body)
    assert first.status_code == 200
    second = await client.put(f"/api/v1/agent/files/{file.id}", headers=headers, json={**body, "content": "Stale"})
    assert second.status_code == 409
    db_session.refresh(file)
    assert file.content == "Accepted"
    assert normalize_datetime_to_utc(file.updated_at) > STAMP
    assert len(db_session.exec(select(FileVersion).where(FileVersion.file_id == file.id)).all()) == 1


@pytest.mark.parametrize("writer", ["web", "edit_tool", "update_tool", "rollback"])
@pytest.mark.parametrize("clock_offset", [timedelta(0), timedelta(seconds=-1)])
async def test_agent_old_token_cannot_overwrite_other_content_writers(
    client, db_session, conditional_file, monkeypatch, writer, clock_offset,
):
    from agent.tools.file_ops import FileCRUD, FileEditor, crud, edit
    from api import files as files_api
    from services import llama_index
    from services.features import file_version_service as versions

    file, headers = conditional_file
    project = db_session.get(Project, file.project_id)
    owner = db_session.get(User, project.owner_id)
    for module in [files_api, crud, edit, versions]:
        monkeypatch.setattr(module, "utcnow", lambda: STAMP + clock_offset)
    monkeypatch.setattr(agent_api, "_schedule_file_index_upsert", Mock())
    monkeypatch.setattr(llama_index, "schedule_index_upsert", Mock())
    service = versions.get_file_version_service()
    monkeypatch.setattr(versions.FileVersionService, "check_user_version_quota", lambda *args, **kwargs: (True, 0, 10))
    if writer == "web":
        files_api.update_file(file.id, files_api.FileUpdate(content="Winner"), BackgroundTasks(), owner, db_session)
    elif writer == "edit_tool":
        FileEditor(db_session, user_id=owner.id).edit_file(file.id, [{"op": "replace", "old": "Original", "new": "Winner"}])
    elif writer == "update_tool":
        FileCRUD(db_session, user_id=owner.id).update_file(file.id, content="Winner")
    else:
        version = service.create_version(db_session, file.id, "Winner", skip_quota=True)
        service.rollback_to_version(db_session, file.id, version.version_number, owner.id)

    response = await client.put(f"/api/v1/agent/files/{file.id}", headers=headers, json={
        "content": "Stale overwrite", "base_updated_at": STAMP.isoformat(),
    })
    assert response.status_code == 409
    db_session.refresh(file)
    assert file.content == "Winner"
    assert normalize_datetime_to_utc(file.updated_at) > STAMP


async def test_agent_write_without_precondition_remains_supported(client, db_session, conditional_file, monkeypatch):
    file, headers = conditional_file
    monkeypatch.setattr(agent_api, "_schedule_file_index_upsert", Mock())
    response = await client.put(f"/api/v1/agent/files/{file.id}", headers=headers, json={"title": "Unconditional"})
    assert response.status_code == 200
    db_session.refresh(file)
    assert file.title == "Unconditional"


async def test_agent_write_runs_sync_database_work_off_event_loop(client, conditional_file, monkeypatch):
    file, headers = conditional_file
    loop_thread = get_ident()
    observed_threads = []
    load = agent_api._load_accessible_file

    def observe(*args, **kwargs):
        observed_threads.append(get_ident())
        return load(*args, **kwargs)

    monkeypatch.setattr(agent_api, "_load_accessible_file", observe)
    monkeypatch.setattr(agent_api, "_schedule_file_index_upsert", Mock())
    response = await client.put(f"/api/v1/agent/files/{file.id}", headers=headers, json={"title": "Worker"})
    assert response.status_code == 200
    assert len(observed_threads) == 1
    assert observed_threads[0] != loop_thread


async def test_malformed_precondition_cannot_be_ignored(client, db_session, conditional_file):
    file, headers = conditional_file
    response = await client.put(f"/api/v1/agent/files/{file.id}", headers=headers, json={"title": "Wrong", "base_updated_at": "not-a-timestamp"})
    assert response.status_code == 422
    db_session.refresh(file)
    assert file.title == "Draft"


@pytest.mark.parametrize("cross_owner", [True, False])
async def test_conditional_write_preserves_access_errors_before_conflict(client, db_session, conditional_file, cross_owner):
    file, headers = conditional_file
    key = db_session.exec(select(AgentApiKey).where(AgentApiKey.key_hash == hash_api_key(headers["X-Agent-API-Key"]))).one()
    if cross_owner:
        other = User(username="other-owner", email="other-owner@example.test", hashed_password="unused")
        db_session.add(other)
        project = db_session.get(Project, file.project_id)
        project.owner_id = other.id
        db_session.add(project)
    else:
        key.project_ids = []
        db_session.add(key)
    db_session.commit()
    response = await client.put(f"/api/v1/agent/files/{file.id}", headers=headers, json={"content": "Wrong", "base_updated_at": (STAMP - timedelta(seconds=1)).isoformat()})
    assert response.status_code == (404 if cross_owner else 403)
    db_session.refresh(file)
    assert file.content == "Original"


def test_sqlite_concurrent_agent_writes_accept_one_token_once(db_session, conditional_file, monkeypatch):
    file, headers = conditional_file
    file_id = file.id
    engine = db_session.get_bind()
    barrier = Barrier(2)
    monkeypatch.setattr(agent_api, "utcnow", lambda: STAMP)
    schedule = Mock()
    monkeypatch.setattr(agent_api, "_schedule_file_index_upsert", schedule)

    def write(content):
        with Session(engine) as session:
            loaded = session.get(File, file_id)
            assert loaded is not None
            key = session.exec(select(AgentApiKey).where(AgentApiKey.key_hash == hash_api_key(headers["X-Agent-API-Key"]))).one()
            user_id = key.user_id
            barrier.wait(timeout=5)
            try:
                result = agent_api.update_file(file_id, agent_api.FileUpdate(content=content, base_updated_at=STAMP), BackgroundTasks(), _rate_limit=0, context=(session, user_id, key))
                if inspect.isawaitable(result):
                    asyncio.run(result)
                return 200, content
            except APIException as exc:
                session.rollback()
                return exc.status_code, content

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(write, ["First writer", "Second writer"]))
    assert sorted(status for status, _ in results) == [200, 409]
    winner = next(content for status, content in results if status == 200)
    db_session.refresh(file)
    assert file.content == winner
    assert len(db_session.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()) == 1
    assert schedule.call_count == 1


@pytest.mark.parametrize("method,route_path,payload", [
    ("GET", "/projects", None),
    ("GET", "/projects/{project_id}", None),
    ("POST", "/projects", {"name": "Worker project"}),
    ("PUT", "/projects/{project_id}", {"name": "Worker renamed"}),
    ("DELETE", "/projects/{project_id}", None),
    ("GET", "/projects/{project_id}/files", None),
    ("POST", "/projects/{project_id}/files", {"title": "Worker file"}),
    ("GET", "/files/{file_id}", None),
    ("DELETE", "/files/{file_id}", None),
])
async def test_sync_agent_crud_handlers_run_in_workers(client, conditional_file, monkeypatch, method, route_path, payload):
    from main import app
    from services import llama_index

    file, headers = conditional_file
    path = "/api/v1/agent" + route_path
    route = next(route for route in app.routes if getattr(route, "path", None) == path and method in route.methods)
    original = route.dependant.call
    loop_thread = get_ident()
    observed = []
    monkeypatch.setattr(llama_index, "schedule_index_upsert", Mock())
    monkeypatch.setattr(llama_index, "schedule_index_delete", Mock())

    if inspect.iscoroutinefunction(original):
        async def observe(*args, **kwargs):
            observed.append(get_ident())
            return await original(*args, **kwargs)
    else:
        def observe(*args, **kwargs):
            observed.append(get_ident())
            return original(*args, **kwargs)

    monkeypatch.setattr(route.dependant, "call", observe)
    response = await client.request(method, path.format(project_id=file.project_id, file_id=file.id), headers=headers, json=payload)
    assert response.status_code == 200
    assert len(observed) == 1
    assert observed[0] != loop_thread
