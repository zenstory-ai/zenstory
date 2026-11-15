"""
Chat API integration tests.

Tests for chat session management API endpoints.
"""


import json

import pytest
from httpx import AsyncClient
from sqlmodel import Session, select

from models import AgentArtifactLedger, ChatMessage, ChatSession, Project, User
from services.core.auth_service import hash_password


@pytest.mark.integration
class TestChatAPI:
    """Test chat session management API."""

    async def test_get_or_create_session_new(
        self, client: AsyncClient, db_session: Session
    ):
        """Test GET /chat/session/{project_id} - create new session."""
        # Create user and project
        user = User(
            username="testuser1",
            email="testuser1@example.com",
            hashed_password=hash_password("password123"),
            name="Test User 1",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        project = Project(
            id="proj-test-1",
            owner_id=user.id,
            name="Test Project",
            description="Test Description",
        )
        db_session.add(project)
        db_session.commit()

        # Login to get token
        response = await client.post(
            "/api/auth/login",
            data={"username": "testuser1", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]

        # Get or create session
        response = await client.get(
            f"/api/v1/chat/session/{project.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == user.id
        assert data["project_id"] == project.id
        assert data["title"] == "AI 助手对话"
        assert data["is_active"] is True
        assert data["message_count"] == 0
        assert "id" in data
        assert "created_at" in data
        assert "updated_at" in data

        # Verify session was created in database
        stmt = select(ChatSession).where(ChatSession.project_id == project.id)
        session = db_session.exec(stmt).first()
        assert session is not None
        assert session.user_id == user.id
        assert session.is_active is True

    async def test_get_or_create_session_existing(
        self, client: AsyncClient, db_session: Session
    ):
        """Test GET /chat/session/{project_id} - get existing session."""
        # Create user and project
        user = User(
            username="testuser2",
            email="testuser2@example.com",
            hashed_password=hash_password("password123"),
            name="Test User 2",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        project = Project(
            id="proj-test-2",
            owner_id=user.id,
            name="Test Project",
            description="Test Description",
        )
        db_session.add(project)
        db_session.commit()

        # Create existing session
        existing_session = ChatSession(
            user_id=user.id,
            project_id=project.id,
            title="Existing Session",
            is_active=True,
            message_count=5,
        )
        db_session.add(existing_session)
        db_session.commit()

        # Login to get token
        response = await client.post(
            "/api/auth/login",
            data={"username": "testuser2", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]

        # Get existing session
        response = await client.get(
            f"/api/v1/chat/session/{project.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == existing_session.id
        assert data["title"] == "Existing Session"
        assert data["message_count"] == 5
        assert data["is_active"] is True

    async def test_get_session_messages_empty(
        self, client: AsyncClient, db_session: Session
    ):
        """Test GET /chat/session/{project_id}/recent - empty session."""
        # Create user and project
        user = User(
            username="testuser3",
            email="testuser3@example.com",
            hashed_password=hash_password("password123"),
            name="Test User 3",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        project = Project(
            id="proj-test-3",
            owner_id=user.id,
            name="Test Project",
            description="Test Description",
        )
        db_session.add(project)
        db_session.commit()

        # Login to get token
        response = await client.post(
            "/api/auth/login",
            data={"username": "testuser3", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]

        # Get messages (should be empty)
        response = await client.get(
            f"/api/v1/chat/session/{project.id}/recent",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data == []

    async def test_get_session_messages_with_data(
        self, client: AsyncClient, db_session: Session
    ):
        """Test GET /chat/session/{project_id}/recent - with messages."""
        # Create user and project
        user = User(
            username="testuser4",
            email="testuser4@example.com",
            hashed_password=hash_password("password123"),
            name="Test User 4",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        project = Project(
            id="proj-test-4",
            owner_id=user.id,
            name="Test Project",
            description="Test Description",
        )
        db_session.add(project)
        db_session.commit()

        # Create session with messages
        session = ChatSession(
            user_id=user.id,
            project_id=project.id,
            title="Test Session",
            is_active=True,
            message_count=2,
        )
        db_session.add(session)
        db_session.commit()

        msg1 = ChatMessage(
            session_id=session.id,
            role="user",
            content="Hello, how are you?",
        )
        msg2 = ChatMessage(
            session_id=session.id,
            role="assistant",
            content="I'm doing well, thank you!",
        )
        db_session.add(msg1)
        db_session.add(msg2)
        db_session.commit()

        # Login to get token
        response = await client.post(
            "/api/auth/login",
            data={"username": "testuser4", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]

        # Get messages
        response = await client.get(
            f"/api/v1/chat/session/{project.id}/recent",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2
        assert data[0]["role"] == "user"
        assert data[0]["content"] == "Hello, how are you?"
        assert data[0]["session_id"] == session.id
        assert data[1]["role"] == "assistant"
        assert data[1]["content"] == "I'm doing well, thank you!"

    async def test_get_session_messages_returns_latest_when_over_limit(
        self, client: AsyncClient, db_session: Session
    ):
        """当消息数超过 limit 时应返回最近的 limit 条（升序输出），而非最旧的一页。"""
        from datetime import timedelta

        from config.datetime_utils import utcnow

        user = User(
            username="testuser4b",
            email="testuser4b@example.com",
            hashed_password=hash_password("password123"),
            name="Test User 4b",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        project = Project(
            id="proj-test-4b",
            owner_id=user.id,
            name="Test Project",
            description="Test Description",
        )
        db_session.add(project)
        db_session.commit()

        session = ChatSession(
            user_id=user.id,
            project_id=project.id,
            title="Test Session",
            is_active=True,
            message_count=5,
        )
        db_session.add(session)
        db_session.commit()

        base = utcnow()
        for i in range(5):
            db_session.add(
                ChatMessage(
                    session_id=session.id,
                    role="user" if i % 2 == 0 else "assistant",
                    content=f"message-{i}",
                    created_at=base + timedelta(seconds=i),
                )
            )
        db_session.commit()

        response = await client.post(
            "/api/auth/login",
            data={"username": "testuser4b", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]

        response = await client.get(
            f"/api/v1/chat/session/{project.id}/recent",
            params={"limit": 2},
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert [msg["content"] for msg in data] == ["message-3", "message-4"]

    @pytest.mark.parametrize("endpoint", ["recent"])
    @pytest.mark.parametrize("limit", [0, -1, 101])
    async def test_history_endpoints_reject_out_of_range_limits(
        self, client: AsyncClient, db_session: Session, endpoint: str, limit: int
    ):
        user = User(
            username=f"history-limit-{endpoint}-{limit}",
            email=f"history-limit-{endpoint}-{limit}@example.com",
            hashed_password=hash_password("password123"),
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()
        project = Project(
            id=f"proj-history-limit-{endpoint}-{limit}",
            owner_id=user.id,
            name="History limits",
        )
        db_session.add(project)
        db_session.commit()

        login = await client.post(
            "/api/auth/login",
            data={"username": user.username, "password": "password123"},
        )
        response = await client.get(
            f"/api/v1/chat/session/{project.id}/{endpoint}",
            params={"limit": limit},
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )

        assert response.status_code == 422

    async def test_clear_session_success(
        self, client: AsyncClient, db_session: Session
    ):
        """Test DELETE /chat/session/{project_id} - clear messages."""
        # Create user and project
        user = User(
            username="testuser5",
            email="testuser5@example.com",
            hashed_password=hash_password("password123"),
            name="Test User 5",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        project = Project(
            id="proj-test-5",
            owner_id=user.id,
            name="Test Project",
            description="Test Description",
        )
        db_session.add(project)
        db_session.commit()

        # Create session with messages
        session = ChatSession(
            user_id=user.id,
            project_id=project.id,
            title="Test Session",
            is_active=True,
            message_count=3,
        )
        db_session.add(session)
        db_session.commit()

        for i in range(3):
            msg = ChatMessage(
                session_id=session.id,
                role="user",
                content=f"Message {i + 1}",
            )
            db_session.add(msg)

        db_session.add(
            AgentArtifactLedger(
                project_id=project.id,
                session_id=session.id,
                user_id=user.id,
                action="create_file",
                tool_name="create_file",
                artifact_ref="file-keep-1",
                payload=json.dumps({"title": "chapter-1"}),
            )
        )
        db_session.commit()

        # Login to get token
        response = await client.post(
            "/api/auth/login",
            data={"username": "testuser5", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]

        # Clear session
        response = await client.delete(
            f"/api/v1/chat/session/{project.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "Cleared 3 messages" in data["message"]

        # Verify messages are deleted
        stmt = select(ChatMessage).where(ChatMessage.session_id == session.id)
        messages = db_session.exec(stmt).all()
        assert len(messages) == 0

        # Verify session message_count is reset
        db_session.refresh(session)
        assert session.message_count == 0

        # Verify non-message ledger rows remain untouched by clear_session
        other_rows = db_session.exec(
            select(AgentArtifactLedger).where(
                AgentArtifactLedger.project_id == project.id,
                AgentArtifactLedger.session_id == session.id,
                AgentArtifactLedger.action == "create_file",
            )
        ).all()
        assert len(other_rows) == 1

    async def test_clear_session_no_session(
        self, client: AsyncClient, db_session: Session
    ):
        """Test DELETE /chat/session/{project_id} - no session exists."""
        # Create user and project
        user = User(
            username="testuser6",
            email="testuser6@example.com",
            hashed_password=hash_password("password123"),
            name="Test User 6",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        project = Project(
            id="proj-test-6",
            owner_id=user.id,
            name="Test Project",
            description="Test Description",
        )
        db_session.add(project)
        db_session.commit()

        # Login to get token
        response = await client.post(
            "/api/auth/login",
            data={"username": "testuser6", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]

        # Clear non-existent session
        response = await client.delete(
            f"/api/v1/chat/session/{project.id}",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "No session to clear" in data["message"]

    async def test_create_new_session(
        self, client: AsyncClient, db_session: Session
    ):
        """Test POST /chat/session/{project_id}/new - create new session."""
        # Create user and project
        user = User(
            username="testuser7",
            email="testuser7@example.com",
            hashed_password=hash_password("password123"),
            name="Test User 7",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        project = Project(
            id="proj-test-7",
            owner_id=user.id,
            name="Test Project",
            description="Test Description",
        )
        db_session.add(project)
        db_session.commit()

        # Create existing active session
        existing_session = ChatSession(
            user_id=user.id,
            project_id=project.id,
            title="Old Session",
            is_active=True,
            message_count=5,
        )
        db_session.add(existing_session)
        db_session.commit()

        # Login to get token
        response = await client.post(
            "/api/auth/login",
            data={"username": "testuser7", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]

        # Create new session
        response = await client.post(
            f"/api/v1/chat/session/{project.id}/new",
            headers={"Authorization": f"Bearer {token}"},
            params={"title": "New Session"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "New Session"
        assert data["is_active"] is True
        assert data["message_count"] == 0
        assert data["id"] != existing_session.id

        # Verify old session is deactivated
        db_session.refresh(existing_session)
        assert existing_session.is_active is False

        # Verify new session is active
        stmt = select(ChatSession).where(ChatSession.id == data["id"])
        new_session = db_session.exec(stmt).first()
        assert new_session is not None
        assert new_session.is_active is True

    async def test_unauthorized_access(
        self, client: AsyncClient, db_session: Session
    ):
        """Test accessing chat without authentication."""
        response = await client.get("/api/v1/chat/session/test-project-id")
        assert response.status_code == 401

    async def test_access_other_user_session(
        self, client: AsyncClient, db_session: Session
    ):
        """Test accessing another user's chat session."""
        # Create two users
        user1 = User(
            username="testuser8a",
            email="testuser8a@example.com",
            hashed_password=hash_password("password123"),
            name="Test User 8A",
            email_verified=True,
            is_active=True,
        )
        user2 = User(
            username="testuser8b",
            email="testuser8b@example.com",
            hashed_password=hash_password("password123"),
            name="Test User 8B",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user1)
        db_session.add(user2)
        db_session.commit()

        # Create project for user1
        project = Project(
            id="proj-test-8",
            owner_id=user1.id,
            name="Test Project",
            description="Test Description",
        )
        db_session.add(project)
        db_session.commit()

        # Login as user2
        response = await client.post(
            "/api/auth/login",
            data={"username": "testuser8b", "password": "password123"},
        )
        assert response.status_code == 200
        token2 = response.json()["access_token"]

        # Try to access user1's project session
        response = await client.get(
            f"/api/v1/chat/session/{project.id}",
            headers={"Authorization": f"Bearer {token2}"},
        )
        assert response.status_code == 403

    async def test_admin_can_access_other_user_project_session(
        self, client: AsyncClient, db_session: Session
    ):
        """Superuser/admin should be able to open chat session for any project."""
        # Create owner + admin
        owner = User(
            username="chat_owner_1",
            email="chat_owner_1@example.com",
            hashed_password=hash_password("password123"),
            email_verified=True,
            is_active=True,
        )
        admin = User(
            username="chat_admin_1",
            email="chat_admin_1@example.com",
            hashed_password=hash_password("password123"),
            email_verified=True,
            is_active=True,
            is_superuser=True,
        )
        db_session.add(owner)
        db_session.add(admin)
        db_session.commit()

        project = Project(
            id="proj-chat-admin-1",
            owner_id=owner.id,
            name="Owner Project",
            description="Test Description",
        )
        db_session.add(project)
        db_session.commit()

        # Login as admin
        response = await client.post(
            "/api/auth/login",
            data={"username": "chat_admin_1", "password": "password123"},
        )
        assert response.status_code == 200
        admin_token = response.json()["access_token"]

        response = await client.get(
            f"/api/v1/chat/session/{project.id}",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["project_id"] == project.id
        assert data["user_id"] == admin.id

    async def test_project_not_found(
        self, client: AsyncClient, db_session: Session
    ):
        """Test accessing chat for non-existent project."""
        # Create user
        user = User(
            username="testuser9",
            email="testuser9@example.com",
            hashed_password=hash_password("password123"),
            name="Test User 9",
            email_verified=True,
            is_active=True,
        )
        db_session.add(user)
        db_session.commit()

        # Login to get token
        response = await client.post(
            "/api/auth/login",
            data={"username": "testuser9", "password": "password123"},
        )
        assert response.status_code == 200
        token = response.json()["access_token"]

        # Try to access non-existent project
        # Note: verify_project_access returns 403 when project doesn't exist or user doesn't own it
        response = await client.get(
            "/api/v1/chat/session/non-existent-project",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403
