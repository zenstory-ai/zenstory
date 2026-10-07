"""Actual multipart persistence must run off the request event-loop thread."""

from threading import get_ident
from uuid import uuid4

import pytest
from sqlmodel import select

from api.files import get_current_active_user
from main import app
from models import Project, User
from models.file_version import FileVersion
from services.features.file_version_service import FileVersionService


@pytest.mark.parametrize("kind", ["material", "drafts"])
async def test_upload_initial_version_uses_fastapi_worker(client, db_session, monkeypatch, kind):
    suffix = uuid4().hex
    owner = User(
        username=f"upload-worker-{suffix}", email=f"upload-worker-{suffix}@example.test",
        hashed_password="unused", email_verified=True,
    )
    db_session.add(owner)
    db_session.flush()
    project = Project(name="Upload worker boundary", owner_id=owner.id)
    db_session.add(project)
    db_session.commit()
    project_id = project.id
    monkeypatch.setitem(app.dependency_overrides, get_current_active_user, lambda: owner)

    # Keep strict history/persistence real; don't execute external indexing/cache.
    import services.llama_index as indexing
    from services.infra.dashboard_cache import dashboard_cache

    monkeypatch.setattr(indexing, "schedule_index_upsert", lambda **kwargs: None)
    monkeypatch.setattr(dashboard_cache, "bump_project_version", lambda **kwargs: None)
    original = FileVersionService.create_initial_version
    loop_thread = get_ident()
    observed = []

    def observe(self, *args, **kwargs):
        observed.append(get_ident())
        return original(self, *args, **kwargs)

    monkeypatch.setattr(FileVersionService, "create_initial_version", observe)
    path = "upload" if kind == "material" else "upload-drafts"
    field = "file" if kind == "material" else "files"
    response = await client.post(
        f"/api/v1/projects/{project_id}/files/{path}",
        files={field: ("worker.txt", b"Uploaded body", "text/plain")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    file_id = body["id"] if kind == "material" else body["files"][0]["id"]
    versions = db_session.exec(select(FileVersion).where(FileVersion.file_id == file_id)).all()
    assert len(versions) == 1
    assert versions[0].is_base_version and versions[0].version_number == 1
    assert versions[0].content == "Uploaded body"
    assert len(observed) == 1
    assert observed[0] != loop_thread
