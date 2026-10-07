"""POST /projects/{id}/vector-index/rebuild：按用户限流 + 按项目单飞。"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from httpx import AsyncClient

from api import files as files_api
from models import Project, User
from services.core.auth_service import hash_password
from services.infra.single_flight_lock import (
    acquire_single_flight,
    release_single_flight,
    reset_memory_locks,
)


@pytest.fixture(autouse=True)
def _clean_locks(monkeypatch):
    monkeypatch.setenv("ASYNC_VECTOR_INDEX_ENABLED", "true")
    reset_memory_locks()
    yield
    reset_memory_locks()


async def _login_with_project(client: AsyncClient, db_session) -> tuple[str, Project]:
    suffix = uuid4().hex[:8]
    user = User(
        username=f"rebuild_{suffix}",
        email=f"rebuild_{suffix}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    project = Project(name="Rebuild Guard", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    response = await client.post(
        "/api/auth/login", data={"username": user.username, "password": "password123"}
    )
    assert response.status_code == 200
    return response.json()["access_token"], project


@pytest.mark.integration
async def test_rebuild_is_rate_limited_per_user(client: AsyncClient, db_session):
    token, project = await _login_with_project(client, db_session)
    headers = {"Authorization": f"Bearer {token}"}

    with patch.object(files_api, "_rebuild_vector_index_task") as task:
        first = await client.post(
            f"/api/v1/projects/{project.id}/vector-index/rebuild", headers=headers
        )
        second = await client.post(
            f"/api/v1/projects/{project.id}/vector-index/rebuild", headers=headers
        )

    assert first.status_code == 200
    assert second.status_code == 429
    assert task.call_count == 1


@pytest.mark.integration
async def test_rebuild_conflicts_while_same_project_is_rebuilding(client: AsyncClient, db_session):
    token, project = await _login_with_project(client, db_session)
    lock_name = files_api._vector_index_rebuild_lock_name(project.id)
    holder = acquire_single_flight(lock_name, 60)
    assert holder is not None

    try:
        with patch.object(files_api, "_rebuild_vector_index_task") as task:
            response = await client.post(
                f"/api/v1/projects/{project.id}/vector-index/rebuild",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 409
        task.assert_not_called()
    finally:
        release_single_flight(lock_name, holder)


@pytest.mark.integration
async def test_rebuild_disabled_does_not_queue_or_acquire_lock(
    client: AsyncClient, db_session, monkeypatch
):
    monkeypatch.setenv("ASYNC_VECTOR_INDEX_ENABLED", "false")
    token, project = await _login_with_project(client, db_session)

    with patch.object(files_api, "_rebuild_vector_index_task") as task:
        response = await client.post(
            f"/api/v1/projects/{project.id}/vector-index/rebuild",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "message": "Vector index rebuild disabled",
        "project_id": project.id,
        "queued": False,
    }
    task.assert_not_called()
    lock_name = files_api._vector_index_rebuild_lock_name(project.id)
    holder = acquire_single_flight(lock_name, 60)
    assert holder is not None
    release_single_flight(lock_name, holder)


def test_rebuild_task_releases_lock_even_when_indexing_fails():
    project_id = "project-lock-release"
    lock_name = files_api._vector_index_rebuild_lock_name(project_id)
    token = acquire_single_flight(lock_name, 60)
    assert token is not None

    class FailingService:
        def index_project(self, _session, _project_id):
            raise RuntimeError("embedding down")

    with (
        patch("services.llama_index.get_llama_index_service", return_value=FailingService()),
        pytest.raises(RuntimeError),
    ):
        files_api._rebuild_vector_index_task(project_id, token)

    again = acquire_single_flight(lock_name, 60)
    assert again is not None
    release_single_flight(lock_name, again)


def test_single_flight_lock_is_exclusive_until_released():
    first = acquire_single_flight("demo", 60)
    assert first is not None
    assert acquire_single_flight("demo", 60) is None
    release_single_flight("demo", "not-the-owner")
    assert acquire_single_flight("demo", 60) is None
    release_single_flight("demo", first)
    assert acquire_single_flight("demo", 60) is not None
