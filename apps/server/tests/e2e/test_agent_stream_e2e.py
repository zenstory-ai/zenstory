"""
Real end-to-end agent API flows.

These tests intentionally exercise the HTTP API surface plus core runtime wiring
instead of only validating event-builder helpers in isolation.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from agent.core.workflow_events import StreamEvent, StreamEventType
from models import Project, User
from services.core.auth_service import hash_password

pytestmark = pytest.mark.e2e


async def _create_user_login_project(
    client: AsyncClient,
    db_session: Session,
    *,
    username: str,
) -> tuple[User, str, Project]:
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": username, "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name=f"{username}-project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    return user, token, project


def _mock_workflow_events() -> AsyncIterator[StreamEvent]:
    async def _stream() -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Hello from agent"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    return _stream()


def _parse_sse_payload(raw_text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for chunk in raw_text.strip().split("\n\n"):
        if not chunk.strip():
            continue
        event_type = ""
        payload: dict = {}
        for line in chunk.splitlines():
            if line.startswith("event:"):
                event_type = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                payload = json.loads(line.split(":", 1)[1].strip())
        if event_type:
            events.append((event_type, payload))
    return events


@pytest.mark.asyncio
async def test_agent_stream_emits_session_started_content_and_done(client: AsyncClient, db_session: Session, writing_prompt_configs):
    from unittest.mock import patch

    _, token, project = await _create_user_login_project(
        client,
        db_session,
        username="agent_e2e_stream",
    )

    with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
        mock_workflow.return_value = _mock_workflow_events()

        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Write the next paragraph",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    events = _parse_sse_payload(response.text)
    event_names = [name for name, _ in events]
    assert "session_started" in event_names
    assert "content" in event_names
    assert "done" in event_names


@pytest.mark.asyncio
async def test_agent_steer_enqueues_message_for_owned_runtime_session(client: AsyncClient, db_session: Session):
    from agent.core.steering import (
        cleanup_steering_queue_async,
        create_steering_queue_async,
        get_steering_queue_async,
    )

    user, token, _ = await _create_user_login_project(
        client,
        db_session,
        username="agent_e2e_steer",
    )

    session_id = "agent-e2e-steer-runtime"
    await cleanup_steering_queue_async(session_id)
    await create_steering_queue_async(session_id, user.id)

    try:
        response = await client.post(
            "/api/v1/agent/steer",
            json={"session_id": session_id, "message": "Focus on chapter two pacing"},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["queued"] is True

        queue = await get_steering_queue_async(session_id)
        pending = await queue.get_pending()
        assert len(pending) == 1
        assert pending[0].content == "Focus on chapter two pacing"
    finally:
        await cleanup_steering_queue_async(session_id)


@pytest.mark.asyncio
async def test_missing_writing_config_emits_error_without_starting_workflow(client: AsyncClient, db_session: Session):
    from unittest.mock import patch

    _, token, project = await _create_user_login_project(client, db_session, username="missing_config_e2e")
    with patch("agent.service.run_writing_workflow_streaming") as workflow:
        response = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": str(project.id), "message": "Write the next paragraph"},
            headers={"Authorization": f"Bearer {token}"},
        )
    # SSE headers are already sent: the explicit error event, not HTTP 503, is the boundary.
    assert response.status_code == 200
    events = _parse_sse_payload(response.text)
    error_payloads = [payload for name, payload in events if name == "error"]
    assert error_payloads
    assert any("ERR_SERVICE_UNAVAILABLE" in str(payload) for payload in error_payloads)
    # 管理员视角的英文配置说明只写日志，不推给前端。
    assert not any("Writing configuration is unavailable" in str(payload) for payload in error_payloads)
    assert not any(name in ("content", "done") for name, _ in events)
    workflow.assert_not_called()
