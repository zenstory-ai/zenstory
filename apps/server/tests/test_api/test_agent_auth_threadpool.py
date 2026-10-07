from __future__ import annotations

import asyncio
import threading

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from api.agent_dependencies import require_project_access
from database import get_session
from models.agent_api_key import AgentApiKey
from models.entities import Project, User
from services.agent_auth_service import generate_api_key, get_agent_user, hash_api_key


class _FirstResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _BlockingSession:
    """Small session double that exposes which thread performs the DB call."""

    def __init__(self, api_key: AgentApiKey, owner: User, release: threading.Event):
        self.api_key = api_key
        self.owner = owner
        self.release = release
        self.exec_thread_id: int | None = None
        self.unblocked_by_event_loop_callback = False
        self.exec_calls = 0

    def exec(self, _statement):
        self.exec_calls += 1
        if self.exec_calls == 1:
            self.exec_thread_id = threading.get_ident()
            self.unblocked_by_event_loop_callback = self.release.wait(timeout=0.3)
            return _FirstResult(self.api_key)
        return _FirstResult(1)

    def get(self, _model, _identity):
        return self.owner

    def add(self, _value):
        return None

    def commit(self):
        return None

    def refresh(self, _value):
        return None


class _ProjectBlockingSession(_BlockingSession):
    def __init__(
        self,
        api_key: AgentApiKey,
        owner: User,
        project: Project,
        release: threading.Event,
    ):
        super().__init__(api_key, owner, release)
        self.project = project
        self.get_calls = 0
        self.project_get_thread_id: int | None = None

    def exec(self, _statement):
        self.exec_calls += 1
        return _FirstResult(self.api_key if self.exec_calls == 1 else 1)

    def get(self, _model, _identity):
        self.get_calls += 1
        if self.get_calls == 1:
            return self.owner
        self.project_get_thread_id = threading.get_ident()
        self.unblocked_by_event_loop_callback = self.release.wait(timeout=0.3)
        return self.project


@pytest.mark.asyncio
async def test_agent_auth_database_work_runs_off_the_event_loop():
    plain_key = generate_api_key()
    api_key = AgentApiKey(
        id="key-1",
        user_id="user-1",
        key_prefix=plain_key[:8],
        key_hash=hash_api_key(plain_key),
        name="Threadpool proof",
        scopes=["read"],
        is_active=True,
    )
    owner = User(
        id="user-1",
        email="threadpool@example.com",
        username="threadpool",
        hashed_password="hashed",
        is_active=True,
    )
    release = threading.Event()
    session = _BlockingSession(api_key, owner, release)
    test_app = FastAPI()

    def override_session():
        yield session

    @test_app.get("/agent-auth-proof")
    def proof(context=Depends(get_agent_user)):
        return {"user_id": context[1]}

    test_app.dependency_overrides[get_session] = override_session
    loop_thread_id = threading.get_ident()
    asyncio.get_running_loop().call_later(0.02, release.set)

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/agent-auth-proof", headers={"X-Agent-API-Key": plain_key}
        )

    assert response.status_code == 200
    assert response.json() == {"user_id": "user-1"}
    assert session.unblocked_by_event_loop_callback is True
    assert session.exec_thread_id != loop_thread_id


@pytest.mark.asyncio
async def test_agent_project_lookup_runs_off_the_event_loop():
    plain_key = generate_api_key()
    api_key = AgentApiKey(
        id="key-2",
        user_id="user-2",
        key_prefix=plain_key[:8],
        key_hash=hash_api_key(plain_key),
        name="Project threadpool proof",
        scopes=["read"],
        is_active=True,
    )
    owner = User(
        id="user-2",
        email="project-threadpool@example.com",
        username="project-threadpool",
        hashed_password="hashed",
        is_active=True,
    )
    project = Project(id="project-1", name="Proof", owner_id=owner.id)
    release = threading.Event()
    session = _ProjectBlockingSession(api_key, owner, project, release)
    test_app = FastAPI()

    def override_session():
        yield session

    @test_app.get("/projects/{project_id}")
    def proof(project_id: str, context=Depends(require_project_access("read"))):
        return {"project_id": project_id, "user_id": context[1]}

    test_app.dependency_overrides[get_session] = override_session
    loop_thread_id = threading.get_ident()
    asyncio.get_running_loop().call_later(0.02, release.set)

    async with AsyncClient(
        transport=ASGITransport(app=test_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/projects/project-1", headers={"X-Agent-API-Key": plain_key}
        )

    assert response.status_code == 200
    assert response.json() == {"project_id": "project-1", "user_id": "user-2"}
    assert session.unblocked_by_event_loop_callback is True
    assert session.project_get_thread_id != loop_thread_id


def test_agent_dependencies_parse_on_production_worker_python():
    import ast
    from pathlib import Path

    source = Path(__file__).parents[2] / "api" / "agent_dependencies.py"
    ast.parse(source.read_text(), filename=str(source), feature_version=(3, 11))
