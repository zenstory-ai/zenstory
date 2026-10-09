"""
Agent API tests.

Tests for the AI agent streaming endpoints with mocked LangGraph workflow.
Updated for LangGraph architecture.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from models import Project, User
from services.core.auth_service import hash_password

_CHARGED_PERIOD = datetime(2026, 10, 5, 16, tzinfo=UTC)


# Test helper: Create mock LangGraph stream events
def create_mock_langgraph_events():
    """Create mock LangGraph StreamEvent objects."""
    from agent.core.workflow_events import StreamEvent, StreamEventType

    async def mock_stream():
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Hello"})
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "! How can I help?"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    return mock_stream()


@pytest.mark.integration
async def test_agent_stream_request_success(client: AsyncClient, db_session: Session, writing_prompt_configs):
    """Test successful agent streaming request with mocked LangGraph workflow."""
    # Create user
    user = User(
        username="agent_user1",
        email="agent_user1@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "agent_user1", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="Agent Test Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Mock the LangGraph workflow streaming
    with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
        mock_workflow.return_value = create_mock_langgraph_events()

        # Make request
        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Hello, agent!",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        # Check response
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]


@pytest.mark.integration
async def test_agent_stream_unauthorized(client: AsyncClient, db_session: Session):
    """Test that unauthorized requests are rejected."""
    # Create user and project (without auth token)
    user = User(
        username="agent_user2",
        email="agent_user2@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    project = Project(name="Agent Test Project 2", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    response = await client.post(
        "/api/v1/agent/stream",
        json={
            "project_id": str(project.id),
            "message": "Hello",
        },
    )

    assert response.status_code == 401


@pytest.mark.integration
async def test_agent_stream_missing_message(client: AsyncClient, db_session: Session):
    """Test that missing message field returns validation error."""
    # Create user
    user = User(
        username="agent_user3",
        email="agent_user3@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "agent_user3", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="Agent Test Project 3", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    response = await client.post(
        "/api/v1/agent/stream",
        json={
            "project_id": str(project.id),
            # Missing message field
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422


@pytest.mark.integration
async def test_agent_health_check(client: AsyncClient):
    """Test agent health check endpoint."""
    response = await client.get("/api/v1/agent/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "agent"


@pytest.mark.integration
async def test_agent_stream_workflow_error(client: AsyncClient, db_session: Session, writing_prompt_configs):
    """Test agent stream when LangGraph workflow returns error."""
    # Create user
    user = User(
        username="agent_user4",
        email="agent_user4@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "agent_user4", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="Agent Test Project 4", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Mock workflow error
    from agent.core.workflow_events import StreamEvent, StreamEventType

    async def mock_error_stream():
        yield StreamEvent(type=StreamEventType.ERROR, data={"error": "API error"})

    with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
        mock_workflow.return_value = mock_error_stream()

        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Hello",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200


@pytest.mark.integration
async def test_agent_stream_error_refunds_quota(client: AsyncClient, db_session: Session, writing_prompt_configs):
    """Internal stream errors should trigger quota compensation (refund)."""
    user = User(
        username="agent_user4b",
        email="agent_user4b@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_user4b", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Agent Test Project 4b", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    from agent.core.workflow_events import StreamEvent, StreamEventType

    async def mock_error_stream():
        yield StreamEvent(type=StreamEventType.ERROR, data={"error": "API error"})

    with (
        patch("agent.service.run_writing_workflow_streaming") as mock_workflow,
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD) as mock_consume,
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as mock_refund,
    ):
        mock_workflow.return_value = mock_error_stream()

        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Hello",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    mock_consume.assert_called_once()
    mock_refund.assert_called_once_with(
        db_session,
        user.id,
        period_start=_CHARGED_PERIOD,
    )


@pytest.mark.integration
async def test_agent_stream_success_consumes_quota(client: AsyncClient, db_session: Session, writing_prompt_configs):
    """Successful stream completion should consume ai_conversation quota exactly once."""
    user = User(
        username="agent_user4c",
        email="agent_user4c@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_user4c", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Agent Test Project 4c", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    with (
        patch("agent.service.run_writing_workflow_streaming") as mock_workflow,
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD) as mock_consume,
        patch("api.agent.quota_service.release_ai_conversation") as mock_refund,
    ):
        mock_workflow.return_value = create_mock_langgraph_events()
        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Hello",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    mock_consume.assert_called_once()
    assert mock_consume.call_args.args[1] == user.id
    mock_refund.assert_not_called()


@pytest.mark.integration
async def test_agent_stream_missing_terminal_event_refunds_quota(
    client: AsyncClient,
    db_session: Session,
):
    """Non-terminal stream end with output is charged; without output it is refunded."""
    user = User(
        username="agent_user4c_terminal",
        email="agent_user4c_terminal@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_user4c_terminal", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Agent Test Project 4c terminal", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    async def non_terminal_stream():
        # 真正的回复正文（不是工具调用前的一句过渡旁白）才算产出。
        yield 'event: content\ndata: {"text":"第三章的节奏偏慢：前两节都在交代旧书店的来历，主角直到第三节才第一次碰到那本没有书名的旧账本，读者等得太久，建议把账本提前到开头。"}\n\n'

    async def non_terminal_stream_without_output():
        yield 'event: thinking\ndata: {"message":"正在思考..."}\n\n'

    class MockAgentService:
        def __init__(self, source):
            self._source = source

        async def process_stream(self, **_kwargs):
            async for item in self._source():
                yield item

    # 已经串流过正文：用户拿到了产出，按「本轮已有实质产出」计费，不退款。
    with (
        patch("api.agent.get_agent_service", return_value=MockAgentService(non_terminal_stream)),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD) as mock_consume,
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as mock_refund,
    ):
        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Hello",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    mock_consume.assert_called_once()
    mock_refund.assert_not_called()

    # 没有任何产出就断掉：按内部错误退款。
    with (
        patch(
            "api.agent.get_agent_service",
            return_value=MockAgentService(non_terminal_stream_without_output),
        ),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as mock_refund,
    ):
        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Hello again",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    mock_refund.assert_called_once()


@pytest.mark.integration
async def test_agent_stream_returns_402_when_reserve_quota_fails(client: AsyncClient, db_session: Session):
    """If quota reservation fails at stream start, endpoint should return 402."""
    user = User(
        username="agent_user4d",
        email="agent_user4d@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_user4d", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Agent Test Project 4d", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    with (
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=None) as mock_consume,
        patch("agent.service.run_writing_workflow_streaming") as mock_workflow,
    ):
        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Hello",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 402
    mock_consume.assert_called_once()
    mock_workflow.assert_not_called()


@pytest.mark.integration
async def test_agent_stream_sse_format(client: AsyncClient, db_session: Session):
    """Test SSE response format validation."""
    # Create user
    user = User(
        username="agent_user5",
        email="agent_user5@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "agent_user5", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="Agent Test Project 5", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
        mock_workflow.return_value = create_mock_langgraph_events()

        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Test SSE",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        # Verify SSE headers
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]


@pytest.mark.integration
async def test_agent_verify_mock_used(client: AsyncClient, db_session: Session):
    """Verify that tests use mocked LangGraph workflow (no real API calls)."""
    # Create user
    user = User(
        username="agent_user6",
        email="agent_user6@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "agent_user6", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="Agent Test Project 6", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
        mock_workflow.return_value = create_mock_langgraph_events()

        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Test with mock",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        # Request should succeed (using mock)
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]


@pytest.mark.integration
async def test_agent_stream_with_selected_text(client: AsyncClient, db_session: Session):
    """Test agent stream with selected text context."""
    # Create user
    user = User(
        username="agent_user7",
        email="agent_user7@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "agent_user7", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="Agent Test Project 7", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
        mock_workflow.return_value = create_mock_langgraph_events()

        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Explain this",
                "selected_text": "Selected text for context",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200


@pytest.mark.integration
async def test_agent_stream_with_metadata(client: AsyncClient, db_session: Session):
    """Test agent stream with metadata (current_file_id, etc.)."""
    # Create user
    user = User(
        username="agent_user8",
        email="agent_user8@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "agent_user8", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Create project
    project = Project(name="Agent Test Project 8", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    with patch("agent.service.run_writing_workflow_streaming") as mock_workflow:
        mock_workflow.return_value = create_mock_langgraph_events()

        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Continue writing",
                "metadata": {
                    "current_file_id": str(project.id),
                    "current_file_type": "draft",
                    "current_file_title": "Chapter 1",
                },
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200


@pytest.mark.integration
async def test_agent_stream_invalid_project(client: AsyncClient, db_session: Session):
    """Test agent stream with invalid project ID."""
    # Create user
    user = User(
        username="agent_user9",
        email="agent_user9@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True
    )
    db_session.add(user)
    db_session.commit()

    # Login
    login_response = await client.post("/api/auth/login", data={"username": "agent_user9", "password": "password123"})
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    with patch("api.agent.quota_service.reserve_ai_conversation") as mock_consume:
        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": "00000000-0000-0000-0000-000000000000",
                "message": "Hello",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    # Project access now fails fast before starting SSE stream
    assert response.status_code == 403
    mock_consume.assert_not_called()


@pytest.mark.integration
async def test_agent_suggest_success(client: AsyncClient, db_session: Session):
    """Test suggest endpoint returns generated suggestions."""
    user = User(
        username="agent_user10",
        email="agent_user10@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_user10", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Agent Test Project 10", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    with patch("agent.suggest_service.get_suggest_service") as mock_get_suggest_service:
        mock_service = mock_get_suggest_service.return_value
        mock_service.generate_suggestions = AsyncMock(
            return_value=["继续写第二章", "补充角色动机", "设计剧情反转"]
        )

        response = await client.post(
            "/api/v1/agent/suggest",
            json={
                "project_id": str(project.id),
                "recent_messages": [{"role": "user", "content": "继续"}],
                "count": 3,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.json()["suggestions"] == ["继续写第二章", "补充角色动机", "设计剧情反转"]


@pytest.mark.integration
async def test_agent_suggest_falls_back_when_llm_unavailable(client: AsyncClient, db_session: Session):
    """Suggest endpoint should not 500 when LLM key/config is missing."""
    user = User(
        username="agent_user10b",
        email="agent_user10b@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_user10b", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Agent Test Project 10b", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    # Ensure get_suggest_service builds a SuggestService instance whose llm init fails.
    with patch("agent.suggest_service._service", None), patch(
        "agent.suggest_service.get_llm_client",
        side_effect=ValueError("DEEPSEEK_API_KEY is required"),
    ):
        response = await client.post(
            "/api/v1/agent/suggest",
            json={
                "project_id": str(project.id),
                "recent_messages": [{"role": "user", "content": "继续"}],
                "count": 3,
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["suggestions"] == [
        "写下一章的情节发展",
        "完善角色的人物动机",
        "设计一个情节转折点",
    ]


@pytest.mark.integration
async def test_agent_suggest_forbidden_project(client: AsyncClient, db_session: Session):
    """Test suggest endpoint enforces project ownership."""
    owner = User(
        username="agent_owner_11",
        email="agent_owner_11@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    intruder = User(
        username="agent_intruder_11",
        email="agent_intruder_11@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(owner)
    db_session.add(intruder)
    db_session.commit()

    project = Project(name="Owner Project", owner_id=owner.id)
    db_session.add(project)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_intruder_11", "password": "password123"},
    )
    assert login_response.status_code == 200
    intruder_token = login_response.json()["access_token"]

    response = await client.post(
        "/api/v1/agent/suggest",
        json={"project_id": str(project.id), "count": 3},
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert response.status_code == 403


@pytest.mark.integration
async def test_agent_steer_success(client: AsyncClient, db_session: Session):
    """Test steer endpoint accepts message for owned active session."""
    from agent.core.steering import (
        cleanup_steering_queue_async,
        create_steering_queue_async,
    )

    user = User(
        username="agent_user12",
        email="agent_user12@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_user12", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    session_id = "runtime-session-user12"
    await cleanup_steering_queue_async(session_id)
    await create_steering_queue_async(session_id, user.id)

    try:
        response = await client.post(
            "/api/v1/agent/steer",
            json={"session_id": session_id, "message": "请聚焦第二章"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["queued"] is True
        assert data["message_id"].startswith("steer-")
    finally:
        await cleanup_steering_queue_async(session_id)


@pytest.mark.integration
async def test_agent_steer_rejects_unknown_session(client: AsyncClient, db_session: Session):
    """Test steer endpoint rejects unknown runtime session."""
    user = User(
        username="agent_user13",
        email="agent_user13@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_user13", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    response = await client.post(
        "/api/v1/agent/steer",
        json={"session_id": "nonexistent-runtime-session", "message": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


@pytest.mark.integration
async def test_agent_steer_rejects_cross_user_session(client: AsyncClient, db_session: Session):
    """Test steer endpoint enforces runtime session ownership."""
    from agent.core.steering import (
        cleanup_steering_queue_async,
        create_steering_queue_async,
    )

    owner = User(
        username="agent_owner_14",
        email="agent_owner_14@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    intruder = User(
        username="agent_intruder_14",
        email="agent_intruder_14@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(owner)
    db_session.add(intruder)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_intruder_14", "password": "password123"},
    )
    assert login_response.status_code == 200
    intruder_token = login_response.json()["access_token"]

    session_id = "runtime-session-owner14"
    await cleanup_steering_queue_async(session_id)
    await create_steering_queue_async(session_id, owner.id)

    try:
        response = await client.post(
            "/api/v1/agent/steer",
            json={"session_id": session_id, "message": "恶意注入"},
            headers={"Authorization": f"Bearer {intruder_token}"},
        )
        assert response.status_code == 403
    finally:
        await cleanup_steering_queue_async(session_id)


@pytest.mark.integration
async def test_agent_stream_rejects_cross_user_runtime_session_id(
    client: AsyncClient,
    db_session: Session,
):
    """Stream endpoint should reject reusing a runtime session_id owned by another user."""
    from agent.core.steering import (
        cleanup_steering_queue_async,
        create_steering_queue_async,
    )

    owner = User(
        username="agent_owner_15",
        email="agent_owner_15@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    intruder = User(
        username="agent_intruder_15",
        email="agent_intruder_15@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(owner)
    db_session.add(intruder)
    db_session.commit()

    intruder_project = Project(name="Intruder Project 15", owner_id=intruder.id)
    db_session.add(intruder_project)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_intruder_15", "password": "password123"},
    )
    assert login_response.status_code == 200
    intruder_token = login_response.json()["access_token"]

    session_id = "runtime-session-owner15"
    await cleanup_steering_queue_async(session_id)
    await create_steering_queue_async(session_id, owner.id)

    try:
        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(intruder_project.id),
                "message": "继续写第二章",
                "session_id": session_id,
            },
            headers={"Authorization": f"Bearer {intruder_token}"},
        )
        assert response.status_code == 403
    finally:
        await cleanup_steering_queue_async(session_id)


@pytest.mark.integration
async def test_agent_steer_rejects_empty_message(client: AsyncClient, db_session: Session):
    """Test steer endpoint returns 400 for empty/whitespace message."""
    from agent.core.steering import (
        cleanup_steering_queue_async,
        create_steering_queue_async,
    )

    user = User(
        username="agent_user15",
        email="agent_user15@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_user15", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    session_id = "runtime-session-user15"
    await cleanup_steering_queue_async(session_id)
    await create_steering_queue_async(session_id, user.id)

    try:
        response = await client.post(
            "/api/v1/agent/steer",
            json={"session_id": session_id, "message": "   \n\t "},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 400
    finally:
        await cleanup_steering_queue_async(session_id)


@pytest.mark.integration
async def test_agent_stream_releases_request_transaction_before_streaming(
    client: AsyncClient,
    db_session: Session,
):
    """The request-scoped session must not hold an open transaction during SSE."""
    user = User(
        username="agent_user_txn",
        email="agent_user_txn@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_user_txn", "password": "password123"},
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    project = Project(name="Agent Txn Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    in_transaction_at_stream_start: list[bool] = []

    class MockAgentService:
        async def process_stream(self, **kwargs):
            in_transaction_at_stream_start.append(kwargs["session"].in_transaction())
            yield "event: done\ndata: {}\n\n"

    def _fresh_session():
        return Session(db_session.get_bind())

    # 清空 identity map，强制鉴权/权限校验真正发 SELECT 并 autobegin 事务
    # （否则测试内 add+commit 过的 User/Project 会命中缓存，压根开不了事务）。
    db_session.expunge_all()

    # 模拟 Postgres 路径：配额读写走独立 session，请求级 session 只承担
    # 鉴权/权限校验的 SELECT（正是会挂住 idle in transaction 的场景）。
    with (
        patch("api.agent.get_agent_service", return_value=MockAgentService()),
        patch("api.agent._should_offload_session_work", return_value=True),
        patch("api.agent.create_session", side_effect=_fresh_session),
    ):
        response = await client.post(
            "/api/v1/agent/stream",
            json={
                "project_id": str(project.id),
                "message": "Hello",
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert in_transaction_at_stream_start == [False]


@pytest.mark.integration
async def test_agent_stream_auth_failure_paths_unchanged_after_txn_release(
    client: AsyncClient,
    db_session: Session,
):
    """Releasing the request transaction must not alter auth/permission failures."""
    owner = User(
        username="agent_user_txn_owner",
        email="agent_user_txn_owner@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    intruder = User(
        username="agent_user_txn_intruder",
        email="agent_user_txn_intruder@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add_all([owner, intruder])
    db_session.commit()

    project = Project(name="Agent Txn Foreign Project", owner_id=owner.id)
    db_session.add(project)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "agent_user_txn_intruder", "password": "password123"},
    )
    assert login_response.status_code == 200
    intruder_token = login_response.json()["access_token"]

    # Unauthenticated request
    response = await client.post(
        "/api/v1/agent/stream",
        json={"project_id": str(project.id), "message": "Hello"},
    )
    assert response.status_code == 401

    # Authenticated but not the project owner
    response = await client.post(
        "/api/v1/agent/stream",
        json={"project_id": str(project.id), "message": "Hello"},
        headers={"Authorization": f"Bearer {intruder_token}"},
    )
    assert response.status_code == 403


async def _login_with_project(client: AsyncClient, db_session: Session, username: str):
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
        "/api/auth/login", data={"username": username, "password": "password123"}
    )
    assert login_response.status_code == 200
    project = Project(name=f"{username} project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()
    return login_response.json()["access_token"], project


_READ_ONLY_NOTICE_SSE = (
    'event: workflow_stopped\ndata: {"reason": "read_only_handoff_blocked", '
    '"agent_type": "quality_reviewer", "message": "已跳过交接", "target_agent": "writer"}\n\n'
)


@pytest.mark.integration
async def test_agent_stream_exception_after_read_only_notice_still_sends_fallback_error(
    client: AsyncClient,
    db_session: Session,
):
    """只读提示卡片（workflow_stopped/read_only_handoff_blocked）不是终止事件：
    之后 process_stream 抛异常时仍要补发兜底 error 帧（固定文案 + 错误码）。
    本轮已经串流过分析正文，按「已有实质产出」计费、不退款。"""
    from httpx import ASGITransport

    from main import app

    token, project = await _login_with_project(client, db_session, "agent_ro_notice_exc")

    class MockAgentService:
        async def process_stream(self, **_kwargs):
            yield 'event: content\ndata: {"text": "第三章的节奏偏慢：前两节都在交代旧书店的来历，主角直到第三节才第一次碰到那本没有书名的旧账本，读者等得太久，建议把账本提前到开头。"}\n\n'
            yield _READ_ONLY_NOTICE_SSE
            raise RuntimeError("history save failed")

    with (
        patch("api.agent.get_agent_service", return_value=MockAgentService()),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as mock_refund,
    ):
        # 默认 transport 会把应用异常直接抛给测试、丢掉已流出的帧；这里要检查帧本身。
        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as raw_client:
            response = await raw_client.post(
                "/api/v1/agent/stream",
                json={"project_id": str(project.id), "message": "只分析，别改文件"},
                headers={"Authorization": f"Bearer {token}"},
            )

    body = response.text
    assert "read_only_handoff_blocked" in body
    assert "event: error" in body
    assert body.index("read_only_handoff_blocked") < body.index("event: error")
    assert "ERR_AGENT_RUN_FAILED" in body
    assert "history save failed" not in body
    mock_refund.assert_not_called()


@pytest.mark.integration
async def test_agent_stream_read_only_notice_alone_is_not_terminal(
    client: AsyncClient,
    db_session: Session,
):
    """流只发了只读提示卡片就结束（没有 done）：仍按缺失终止事件退款。"""
    token, project = await _login_with_project(client, db_session, "agent_ro_notice_noterm")

    class MockAgentService:
        async def process_stream(self, **_kwargs):
            yield _READ_ONLY_NOTICE_SSE

    with (
        patch("api.agent.get_agent_service", return_value=MockAgentService()),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as mock_refund,
    ):
        response = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": str(project.id), "message": "只分析，别改文件"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    mock_refund.assert_called_once()


@pytest.mark.integration
async def test_agent_stream_read_only_notice_then_done_is_charged(
    client: AsyncClient,
    db_session: Session,
):
    """提示卡片之后正常收尾（done）：照常计费，不退款。"""
    token, project = await _login_with_project(client, db_session, "agent_ro_notice_done")

    class MockAgentService:
        async def process_stream(self, **_kwargs):
            yield _READ_ONLY_NOTICE_SSE
            yield 'event: done\ndata: {}\n\n'

    with (
        patch("api.agent.get_agent_service", return_value=MockAgentService()),
        patch("api.agent.quota_service.reserve_ai_conversation", return_value=_CHARGED_PERIOD),
        patch("api.agent.quota_service.release_ai_conversation", return_value=True) as mock_refund,
    ):
        response = await client.post(
            "/api/v1/agent/stream",
            json={"project_id": str(project.id), "message": "只分析，别改文件"},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    mock_refund.assert_not_called()
