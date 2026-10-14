"""Author-issued project renames remain authoritative across repeated names."""

import json

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from agent.tools import mcp_tools
from models import Project, User
from services.core.auth_service import hash_password


@pytest.fixture
def title_owner(db_session: Session) -> User:
    user = User(
        username="title-provenance-owner",
        email="title-provenance-owner@example.test",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _project(session: Session, owner: User, name: str = "我的小说") -> Project:
    project = Project(name=name, owner_id=owner.id, project_type="novel")
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


async def _agent_title(session: Session, owner: User, project: Project, title: str) -> dict:
    engine = session.get_bind()
    mcp_tools.ToolContext.set_context(
        None,
        owner.id,
        project.id,
        None,
        create_session_func=lambda: Session(engine),
    )
    try:
        raw = await mcp_tools.update_project({"title": title})
    finally:
        mcp_tools.ToolContext.clear_context()
    return json.loads(raw["content"][0]["text"])


async def _author_headers(client: AsyncClient, owner: User) -> dict[str, str]:
    response = await client.post(
        "/api/auth/login",
        data={"username": owner.username, "password": "password123"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


async def _author_title(
    client: AsyncClient,
    project: Project,
    headers: dict[str, str],
    title: str,
) -> None:
    response = await client.put(
        f"/api/v1/projects/{project.id}",
        headers=headers,
        json={"name": title},
    )
    assert response.status_code == 200
    assert response.json()["name"] == title


@pytest.mark.integration
async def test_author_round_trip_to_an_old_ai_name_still_locks_title(
    client: AsyncClient,
    db_session: Session,
    title_owner: User,
):
    project = _project(db_session, title_owner)
    first = await _agent_title(db_session, title_owner, project, "雾港来信")
    assert first["data"]["project_name_updated"] is True

    headers = await _author_headers(client, title_owner)
    await _author_title(client, project, headers, "作者暂定名")
    await _author_title(client, project, headers, "雾港来信")

    refused = await _agent_title(db_session, title_owner, project, "雾港旧事")

    assert refused["data"]["project_name_updated"] is False
    assert refused["data"]["title_skipped"] == "author_named"
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "雾港来信"


@pytest.mark.integration
async def test_author_explicitly_choosing_a_default_name_locks_title(
    client: AsyncClient,
    db_session: Session,
    title_owner: User,
):
    project = _project(db_session, title_owner, "作者原名")
    headers = await _author_headers(client, title_owner)
    await _author_title(client, project, headers, "我的小说")

    refused = await _agent_title(db_session, title_owner, project, "AI 新名字")

    assert refused["data"]["project_name_updated"] is False
    assert refused["data"]["title_skipped"] == "author_named"
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "我的小说"


@pytest.mark.integration
async def test_author_reaffirming_the_current_ai_name_locks_title(
    client: AsyncClient,
    db_session: Session,
    title_owner: User,
):
    project = _project(db_session, title_owner)
    first = await _agent_title(db_session, title_owner, project, "雾港来信")
    assert first["data"]["project_name_updated"] is True

    headers = await _author_headers(client, title_owner)
    await _author_title(client, project, headers, "雾港来信")

    refused = await _agent_title(db_session, title_owner, project, "雾港旧事")

    assert refused["data"]["project_name_updated"] is False
    assert refused["data"]["title_skipped"] == "author_named"
    db_session.expire_all()
    assert db_session.get(Project, project.id).name == "雾港来信"
