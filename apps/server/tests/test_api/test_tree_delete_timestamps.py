"""Deletion invalidates the same persisted File token as other writers."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import BackgroundTasks

from agent.tools.file_ops.crud import FileCRUD
from api import agent_api
from api import files as files_api
from config.datetime_utils import normalize_datetime_to_utc
from models import File, Project, User
from models.agent_api_key import AgentApiKey

NOW = datetime(2026, 10, 6, 8, 0, 0, 123456, tzinfo=UTC)


@pytest.fixture
def ephemeral_keys(db_session):
    keys = []
    yield keys
    db_session.rollback()
    for key_id in keys:
        key = db_session.get(AgentApiKey, key_id)
        if key is not None:
            db_session.delete(key)
    db_session.commit()


@pytest.mark.parametrize("offset", [-1, 0, 1])
@pytest.mark.parametrize("entrypoint,recursive", [
    ("web", False), ("web", True), ("tool", False), ("tool", True), ("agent", False),
])
def test_delete_advances_only_deleted_rows_and_preserves_nonrecursive_children(
    db_session, ephemeral_keys, monkeypatch, entrypoint, recursive, offset,
):
    from agent.tools.file_ops import crud as crud_module

    user = User(username="delete-clock", email="delete-clock@example.test", hashed_password="unused")
    project = Project(name="Delete clock", owner_id=user.id)
    previous = NOW + timedelta(seconds=offset)
    root = File(project_id=project.id, title="Folder", file_type="folder", updated_at=previous)
    child = File(
        project_id=project.id, title="Child", parent_id=root.id, content="Kept body",
        updated_at=previous,
    )
    key = AgentApiKey(
        user_id=user.id, key_prefix="delclock", key_hash=f"delete-clock-{user.id}", name="Delete", scopes=["write"],
    )
    ephemeral_keys.append(key.id)
    db_session.add_all([user, project, root, child, key])
    db_session.commit()
    for module in [files_api, crud_module, agent_api]:
        monkeypatch.setattr(module, "utcnow", lambda: NOW)
    monkeypatch.setattr(FileCRUD, "_schedule_index_delete", lambda *_a, **_kw: None)

    if entrypoint == "web":
        files_api.delete_file(root.id, BackgroundTasks(), recursive=recursive, current_user=user, session=db_session)
    elif entrypoint == "tool":
        assert FileCRUD(db_session, user.id).delete_file(root.id, recursive=recursive) is True
    else:
        agent_api.delete_file(root.id, BackgroundTasks(), _rate_limit=0, context=(db_session, user.id, key))

    db_session.refresh(root)
    db_session.refresh(child)
    assert root.is_deleted
    assert normalize_datetime_to_utc(root.updated_at) > previous
    assert child.is_deleted is recursive
    assert child.content == "Kept body"
    if recursive:
        assert normalize_datetime_to_utc(child.updated_at) > previous
    else:
        assert normalize_datetime_to_utc(child.updated_at) == previous
