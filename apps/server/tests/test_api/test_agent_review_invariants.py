from unittest.mock import MagicMock, patch

import pytest
from fastapi import BackgroundTasks
from sqlmodel import Session, select

from api.agent_api import FileCreate, FileUpdate, create_file, update_file
from api.agent_api_keys import CreateApiKeyRequest, UpdateApiKeyRequest, create_api_key, update_api_key
from core.error_codes import ErrorCode
from core.error_handler import APIException
from models import File, Project, User
from models.agent_api_key import AgentApiKey
from services.agent_auth_service import generate_api_key, get_agent_user, hash_api_key


@pytest.fixture
def agent_owner(db_session):
    user = User(username="review_agent", email="review-agent@example.test", hashed_password="unused", email_verified=True)
    project = Project(name="Review agent", owner_id=user.id)
    plain_key = generate_api_key()
    key = AgentApiKey(user_id=user.id, key_prefix=plain_key[:8], key_hash=hash_api_key(plain_key), name="Review", scopes=["read", "write"])
    file = File(project_id=project.id, title="Draft", file_type="draft", content="Original")
    db_session.add_all([user, project, key, file])
    db_session.commit()
    return user, project, key, plain_key, file


async def test_disabled_account_key_is_rejected_before_usage_mutation(client, db_session, agent_owner):
    user, _, key, plain_key, _ = agent_owner
    user.is_active = False
    db_session.commit()
    response = await client.get("/api/v1/agent/projects", headers={"X-Agent-API-Key": plain_key})
    assert response.status_code == 403
    assert response.json()["error_code"] == ErrorCode.AUTH_INACTIVE_USER
    db_session.refresh(key)
    assert key.request_count == 0
    assert key.last_used_at is None


async def test_key_with_missing_owner_is_rejected_before_usage_mutation():
    plain_key = generate_api_key()
    key = AgentApiKey(user_id="missing", key_prefix=plain_key[:8], key_hash=hash_api_key(plain_key), name="Review", scopes=["read"])
    session = MagicMock(spec=Session)
    session.exec.return_value.first.return_value = key
    session.get.return_value = None
    with pytest.raises(APIException) as exc:
        get_agent_user(x_agent_api_key=plain_key, session=session)
    assert exc.value.status_code == 403
    assert key.request_count == 0
    session.commit.assert_not_called()


def test_explicit_empty_scopes_cannot_create_key(db_session, agent_owner):
    user, _, _, _, _ = agent_owner
    with pytest.raises(APIException) as exc:
        create_api_key(CreateApiKeyRequest(name="Empty", scopes=[]), current_user=user, session=db_session)
    assert exc.value.status_code == 400
    assert len(db_session.exec(select(AgentApiKey).where(AgentApiKey.user_id == user.id)).all()) == 1


def test_explicit_empty_scopes_cannot_update_key(db_session, agent_owner):
    user, _, key, _, _ = agent_owner
    with pytest.raises(APIException) as exc:
        update_api_key(key.id, UpdateApiKeyRequest(scopes=[]), current_user=user, session=db_session)
    assert exc.value.status_code == 400
    db_session.refresh(key)
    assert key.scopes == ["read", "write"]


async def test_unexpected_version_failure_rolls_back_agent_update(db_session, agent_owner):
    user, _, key, _, file = agent_owner
    with patch("api.agent_api.get_file_version_service") as service:
        service.return_value.create_version.side_effect = RuntimeError("Snapshot storage unavailable")
        with pytest.raises(RuntimeError, match="Snapshot storage unavailable"):
            update_file(file.id, FileUpdate(content="Edited", title="Changed"), BackgroundTasks(), _rate_limit=0, context=(db_session, user.id, key))
    db_session.refresh(file)
    assert file.content == "Original"
    assert file.title == "Draft"


async def test_unexpected_version_failure_rolls_back_agent_create(db_session, agent_owner):
    user, project, key, _, _ = agent_owner
    before = db_session.exec(select(File).where(File.project_id == project.id)).all()
    with patch("api.agent_api.get_file_version_service") as service:
        service.return_value.create_version.side_effect = RuntimeError("Snapshot storage unavailable")
        with pytest.raises(RuntimeError, match="Snapshot storage unavailable"):
            create_file(project.id, FileCreate(title="New draft", file_type="draft", content="New content"), BackgroundTasks(), _rate_limit=0, context=(db_session, user.id, key))
    after = db_session.exec(select(File).where(File.project_id == project.id)).all()
    assert {file.id for file in after} == {file.id for file in before}


async def test_scope_denial_has_stable_code_distinct_from_inactive_account(client, db_session, agent_owner):
    _, _, key, plain_key, _ = agent_owner
    key.scopes = ["write"]
    db_session.commit()
    response = await client.get("/api/v1/agent/projects", headers={"X-Agent-API-Key": plain_key})
    assert response.status_code == 403
    assert response.json()["error_code"] == "ERR_AUTH_SCOPE_DENIED"


async def test_project_allowlist_denial_is_not_scope_denial(client, db_session, agent_owner):
    _, project, key, plain_key, _ = agent_owner
    key.project_ids = ["another-project"]
    db_session.commit()
    response = await client.get(f"/api/v1/agent/projects/{project.id}/files", headers={"X-Agent-API-Key": plain_key})
    assert response.status_code == 403
    assert response.json()["error_code"] == ErrorCode.NOT_AUTHORIZED
