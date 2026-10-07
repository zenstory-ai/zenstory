"""Regression coverage for Agent API file field projections."""

from datetime import datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import event, inspect
from sqlmodel import Session

from models import File
from tests.test_api.test_agent_api import (
    create_test_api_key,
    create_test_file,
    create_test_project,
    create_test_user,
)


def _capture_file_loads_and_sql(session: Session):
    loaded_files: list[File] = []
    statements: list[str] = []

    def retain_loaded_file(_session, instance) -> None:
        if isinstance(instance, File):
            loaded_files.append(instance)

    def record_statement(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        statements.append(statement)

    event.listen(session, "loaded_as_persistent", retain_loaded_file)
    event.listen(session.get_bind(), "before_cursor_execute", record_statement)

    def stop() -> None:
        event.remove(session, "loaded_as_persistent", retain_loaded_file)
        event.remove(session.get_bind(), "before_cursor_execute", record_statement)

    return loaded_files, statements, stop


@pytest.mark.integration
async def test_list_files_projection_does_not_load_omitted_content(
    client: AsyncClient,
    db_session: Session,
):
    user = create_test_user(db_session, "agent_projection_list")
    project = create_test_project(db_session, user.id)
    timestamp = datetime(2026, 2, 3, 4, 5, 6)
    expected: list[dict[str, object]] = []
    for index in range(3):
        file = File(
            project_id=project.id,
            title=f"Chapter {index}",
            content=f"secret body {index}",
            file_type="draft",
            order=index,
            file_metadata=None,
            created_at=timestamp,
            updated_at=timestamp,
        )
        db_session.add(file)
        db_session.flush()
        expected.append(
            {
                "id": file.id,
                "title": file.title,
                "file_metadata": None,
                "updated_at": timestamp.isoformat(),
            }
        )
    db_session.commit()
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["read"])
    project_id = project.id
    db_session.expunge_all()

    loaded_files, statements, stop_capture = _capture_file_loads_and_sql(db_session)
    try:
        response = await client.get(
            f"/api/v1/agent/projects/{project_id}/files",
            headers={"X-Agent-API-Key": plain_key},
            params={"fields": "id,title,file_metadata,updated_at,unknown"},
        )
    finally:
        stop_capture()

    assert response.status_code == 200
    assert response.json()["files"] == expected
    assert len(loaded_files) == 3
    content_statements = [statement for statement in statements if "file.content" in statement.lower()]
    assert content_statements == [], f"expected 0 deferred content queries, got {len(content_statements)}"
    assert all("content" in inspect(file).unloaded for file in loaded_files)


@pytest.mark.integration
async def test_get_file_projection_preserves_datetime_none_and_unknown_field_behavior(
    client: AsyncClient,
    db_session: Session,
):
    user = create_test_user(db_session, "agent_projection_get")
    project = create_test_project(db_session, user.id)
    file = create_test_file(db_session, project.id, content="omitted body")
    file.file_metadata = None
    file.parent_id = None
    file.updated_at = datetime(2026, 2, 3, 4, 5, 6)
    db_session.add(file)
    db_session.commit()
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["read"])
    file_id = file.id
    db_session.expunge_all()

    loaded_files, statements, stop_capture = _capture_file_loads_and_sql(db_session)
    try:
        projected = await client.get(
            f"/api/v1/agent/files/{file_id}",
            headers={"X-Agent-API-Key": plain_key},
            params={"fields": "title,parent_id,file_metadata,updated_at,unknown"},
        )
    finally:
        stop_capture()

    assert projected.status_code == 200
    assert projected.json() == {
        "title": "Test File",
        "parent_id": None,
        "file_metadata": None,
        "updated_at": "2026-02-03T04:05:06",
    }
    assert len(loaded_files) == 1
    content_statements = [statement for statement in statements if "file.content" in statement.lower()]
    assert content_statements == [], f"expected 0 deferred content queries, got {len(content_statements)}"
    assert "content" in inspect(loaded_files[0]).unloaded

    unknown_only = await client.get(
        f"/api/v1/agent/files/{file_id}",
        headers={"X-Agent-API-Key": plain_key},
        params={"fields": "unknown"},
    )
    assert unknown_only.status_code == 200
    assert unknown_only.json() == {}

    content_only = await client.get(
        f"/api/v1/agent/files/{file_id}",
        headers={"X-Agent-API-Key": plain_key},
        params={"fields": "content"},
    )
    assert content_only.status_code == 200
    assert content_only.json() == {"content": "omitted body"}


@pytest.mark.integration
async def test_get_file_without_projection_keeps_complete_response(
    client: AsyncClient,
    db_session: Session,
):
    user = create_test_user(db_session, "agent_projection_full")
    project = create_test_project(db_session, user.id)
    file = create_test_file(db_session, project.id, content="complete body")
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["read"])

    response = await client.get(
        f"/api/v1/agent/files/{file.id}",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 200
    assert set(response.json()) == {
        "id",
        "project_id",
        "title",
        "content",
        "file_type",
        "parent_id",
        "order",
        "file_metadata",
        "created_at",
        "updated_at",
    }
    assert response.json()["content"] == "complete body"
