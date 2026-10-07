"""
Tests for Agent API endpoints (external AI agent API).

Tests endpoints for programmatic access using X-Agent-API-Key authentication:
- GET /api/v1/agent/projects - List projects
- GET /api/v1/agent/projects/{project_id} - Get project
- GET /api/v1/agent/projects/{project_id}/files - List files
- POST /api/v1/agent/projects/{project_id}/files - Create file
- GET /api/v1/agent/files/{file_id} - Get file
- PUT /api/v1/agent/files/{file_id} - Update file
- DELETE /api/v1/agent/files/{file_id} - Delete file
- POST /api/v1/agent/chat - Chat with AI
- POST /api/v1/agent/projects/{project_id}/search - Semantic search
"""

import threading
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlmodel import Session

from config.datetime_utils import utcnow
from models import File, Project, User
from models.agent_api_key import AgentApiKey
from services.agent_auth_service import generate_api_key, hash_api_key
from services.core.auth_service import hash_password

# =============================================================================
# Test Fixtures Helpers
# =============================================================================


def create_test_api_key(
    session: Session,
    user_id: str,
    name: str = "Test Key",
    scopes: list[str] | None = None,
    project_ids: list[str] | None = None,
    is_active: bool = True,
    expires_at: datetime | None = None,
) -> tuple[AgentApiKey, str]:
    """
    Create a test API key and return (key_entity, plain_key).
    """
    plain_key = generate_api_key()
    key_hash = hash_api_key(plain_key)

    api_key = AgentApiKey(
        user_id=user_id,
        key_prefix="eg_",
        key_hash=key_hash,
        name=name,
        scopes=scopes or ["read", "write", "chat"],
        project_ids=project_ids,
        is_active=is_active,
        expires_at=expires_at,
    )
    session.add(api_key)
    session.commit()
    session.refresh(api_key)

    return api_key, plain_key


def create_test_user(session: Session, username: str = "testuser") -> User:
    """Create a test user."""
    user = User(
        username=username,
        email=f"{username}@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def create_test_project(
    session: Session, user_id: str, name: str = "Test Project"
) -> Project:
    """Create a test project."""
    project = Project(name=name, owner_id=user_id)
    session.add(project)
    session.commit()
    session.refresh(project)
    return project


def create_test_file(
    session: Session,
    project_id: str,
    title: str = "Test File",
    file_type: str = "draft",
    content: str = "Test content",
) -> File:
    """Create a test file."""
    file = File(
        project_id=project_id,
        title=title,
        file_type=file_type,
        content=content,
        order=0,
    )
    session.add(file)
    session.commit()
    session.refresh(file)
    return file


# =============================================================================
# Authentication Tests
# =============================================================================


@pytest.mark.integration
async def test_list_projects_missing_api_key(client: AsyncClient):
    """Test that missing X-Agent-API-Key header returns 401."""
    response = await client.get("/api/v1/agent/projects")
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data


@pytest.mark.integration
async def test_list_projects_invalid_api_key_format(client: AsyncClient):
    """Test that invalid API key format returns 401."""
    response = await client.get(
        "/api/v1/agent/projects",
        headers={"X-Agent-API-Key": "invalid_key_format"},
    )
    assert response.status_code == 401
    data = response.json()
    # Error handler returns error_code in detail field
    assert "error_code" in data or "detail" in data


@pytest.mark.integration
async def test_list_projects_nonexistent_api_key(client: AsyncClient):
    """Test that non-existent API key returns 401."""
    response = await client.get(
        "/api/v1/agent/projects",
        headers={"X-Agent-API-Key": "eg_nonexistent123456789"},
    )
    assert response.status_code == 401
    data = response.json()
    # Error handler returns error_code in detail field
    assert "error_code" in data or "detail" in data


@pytest.mark.integration
async def test_list_projects_inactive_api_key(client: AsyncClient, db_session: Session):
    """Test that inactive API key returns 403."""
    user = create_test_user(db_session, "inactive_user")
    _, plain_key = create_test_api_key(
        db_session, user.id, is_active=False
    )

    response = await client.get(
        "/api/v1/agent/projects",
        headers={"X-Agent-API-Key": plain_key},
    )
    assert response.status_code == 403
    data = response.json()
    assert "inactive" in data["detail"].lower()


@pytest.mark.integration
async def test_list_projects_expired_api_key(client: AsyncClient, db_session: Session):
    """Test that expired API key returns 401."""
    user = create_test_user(db_session, "expired_user")
    _, plain_key = create_test_api_key(
        db_session, user.id, expires_at=utcnow() - timedelta(days=1)
    )

    response = await client.get(
        "/api/v1/agent/projects",
        headers={"X-Agent-API-Key": plain_key},
    )
    assert response.status_code == 401
    data = response.json()
    assert "expired" in data["detail"].lower()


# =============================================================================
# Scope Validation Tests
# =============================================================================


@pytest.mark.integration
async def test_list_projects_read_scope_required(client: AsyncClient, db_session: Session):
    """Test that list projects requires 'read' scope."""
    user = create_test_user(db_session, "scope_user1")
    _, plain_key = create_test_api_key(
        db_session, user.id, scopes=["write"]  # No read scope
    )

    response = await client.get(
        "/api/v1/agent/projects",
        headers={"X-Agent-API-Key": plain_key},
    )
    assert response.status_code == 403
    data = response.json()
    # Error handler returns error_code in detail field (ERR_NOT_AUTHORIZED)
    assert "error_code" in data or "detail" in data


@pytest.mark.integration
async def test_create_file_write_scope_required(client: AsyncClient, db_session: Session):
    """Test that create file requires 'write' scope."""
    user = create_test_user(db_session, "scope_user2")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(
        db_session, user.id, scopes=["read"]  # No write scope
    )

    response = await client.post(
        f"/api/v1/agent/projects/{project.id}/files",
        headers={"X-Agent-API-Key": plain_key},
        json={"title": "New File", "file_type": "draft"},
    )
    assert response.status_code == 403
    data = response.json()
    # Error handler returns error_code in detail field (ERR_NOT_AUTHORIZED)
    assert "error_code" in data or "detail" in data


@pytest.mark.integration
@pytest.mark.skip(reason="POST /api/v1/agent/chat not implemented; chat is at /api/v1/agent/stream")
async def test_chat_endpoint_chat_scope_required(client: AsyncClient, db_session: Session):
    """Test that chat endpoint requires 'chat' scope."""
    user = create_test_user(db_session, "scope_user3")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(
        db_session, user.id, scopes=["read", "write"]  # No chat scope
    )

    response = await client.post(
        "/api/v1/agent/chat",
        headers={"X-Agent-API-Key": plain_key},
        json={"project_id": str(project.id), "message": "Hello"},
    )
    assert response.status_code == 403
    data = response.json()
    # Error handler returns error_code in detail field (ERR_NOT_AUTHORIZED)
    assert "error_code" in data or "detail" in data


# =============================================================================
# Project Endpoints Tests
# =============================================================================


@pytest.mark.integration
async def test_list_projects_success(client: AsyncClient, db_session: Session):
    """Test successful project listing."""
    user = create_test_user(db_session, "list_proj_user")
    create_test_project(db_session, user.id, "Project 1")
    create_test_project(db_session, user.id, "Project 2")
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.get(
        "/api/v1/agent/projects",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    project_names = [p["name"] for p in data]
    assert "Project 1" in project_names
    assert "Project 2" in project_names


@pytest.mark.integration
async def test_list_projects_with_project_restriction(client: AsyncClient, db_session: Session):
    """Test that API key with project restriction only sees allowed projects."""
    user = create_test_user(db_session, "restricted_user")
    project1 = create_test_project(db_session, user.id, "Allowed Project")
    create_test_project(db_session, user.id, "Blocked Project")
    _, plain_key = create_test_api_key(
        db_session, user.id, project_ids=[project1.id]
    )

    response = await client.get(
        "/api/v1/agent/projects",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Allowed Project"


@pytest.mark.integration
async def test_get_project_success(client: AsyncClient, db_session: Session):
    """Test successful project retrieval."""
    user = create_test_user(db_session, "get_proj_user")
    project = create_test_project(db_session, user.id, "Test Project")
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.get(
        f"/api/v1/agent/projects/{project.id}",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Project"
    assert data["owner_id"] == user.id


@pytest.mark.integration
async def test_get_project_not_found(client: AsyncClient, db_session: Session):
    """Test getting non-existent project returns 404."""
    user = create_test_user(db_session, "get_proj_user2")
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.get(
        "/api/v1/agent/projects/00000000-0000-0000-0000-000000000000",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_get_project_no_access(client: AsyncClient, db_session: Session):
    """Test getting project without access returns 404."""
    user = create_test_user(db_session, "get_proj_user3")
    other_user = create_test_user(db_session, "other_user1")
    project = create_test_project(db_session, other_user.id, "Other Project")
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.get(
        f"/api/v1/agent/projects/{project.id}",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 404


# =============================================================================
# File Endpoints Tests
# =============================================================================


@pytest.mark.integration
async def test_list_files_success(client: AsyncClient, db_session: Session):
    """Test successful file listing."""
    user = create_test_user(db_session, "list_files_user")
    project = create_test_project(db_session, user.id)
    create_test_file(db_session, project.id, "Chapter 1", "draft")
    create_test_file(db_session, project.id, "Main Character", "character")
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.get(
        f"/api/v1/agent/projects/{project.id}/files",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["files"]) == 2


@pytest.mark.integration
async def test_list_files_filter_by_type(client: AsyncClient, db_session: Session):
    """Test file listing with type filter."""
    user = create_test_user(db_session, "list_files_filter_user")
    project = create_test_project(db_session, user.id)
    create_test_file(db_session, project.id, "Chapter 1", "draft")
    create_test_file(db_session, project.id, "Main Character", "character")
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.get(
        f"/api/v1/agent/projects/{project.id}/files?file_type=draft",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert len(data["files"]) == 1
    assert data["files"][0]["file_type"] == "draft"


@pytest.mark.integration
async def test_create_file_success(client: AsyncClient, db_session: Session):
    """Test successful file creation."""
    user = create_test_user(db_session, "create_file_user")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["read", "write"])

    response = await client.post(
        f"/api/v1/agent/projects/{project.id}/files",
        headers={"X-Agent-API-Key": plain_key},
        json={
            "title": "New Chapter",
            "file_type": "draft",
            "content": "Once upon a time...",
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "New Chapter"
    assert data["file_type"] == "draft"
    assert data["content"] == "Once upon a time..."


@pytest.mark.integration
async def test_create_file_missing_title(client: AsyncClient, db_session: Session):
    """Test that creating file without title returns 422."""
    user = create_test_user(db_session, "create_file_user2")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["read", "write"])

    response = await client.post(
        f"/api/v1/agent/projects/{project.id}/files",
        headers={"X-Agent-API-Key": plain_key},
        json={"file_type": "draft"},
    )

    assert response.status_code == 422


@pytest.mark.integration
async def test_get_file_success(client: AsyncClient, db_session: Session):
    """Test successful file retrieval."""
    user = create_test_user(db_session, "get_file_user")
    project = create_test_project(db_session, user.id)
    file = create_test_file(db_session, project.id, "My Chapter")
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.get(
        f"/api/v1/agent/files/{file.id}",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "My Chapter"


@pytest.mark.integration
async def test_get_file_not_found(client: AsyncClient, db_session: Session):
    """Test getting non-existent file returns 404."""
    user = create_test_user(db_session, "get_file_user2")
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.get(
        "/api/v1/agent/files/00000000-0000-0000-0000-000000000000",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_update_file_success(client: AsyncClient, db_session: Session):
    """Test successful file update."""
    user = create_test_user(db_session, "update_file_user")
    project = create_test_project(db_session, user.id)
    file = create_test_file(db_session, project.id, "Original Title")
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["read", "write"])

    response = await client.put(
        f"/api/v1/agent/files/{file.id}",
        headers={"X-Agent-API-Key": plain_key},
        json={"title": "Updated Title", "content": "New content"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["content"] == "New content"


@pytest.mark.integration
async def test_update_file_partial(client: AsyncClient, db_session: Session):
    """Test partial file update (only title)."""
    user = create_test_user(db_session, "update_file_user2")
    project = create_test_project(db_session, user.id)
    file = create_test_file(db_session, project.id, "Original", content="Original content")
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["read", "write"])

    response = await client.put(
        f"/api/v1/agent/files/{file.id}",
        headers={"X-Agent-API-Key": plain_key},
        json={"title": "New Title"},  # Only update title
    )

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "New Title"
    assert data["content"] == "Original content"  # Unchanged


@pytest.mark.integration
async def test_delete_file_success(client: AsyncClient, db_session: Session):
    """Test successful file deletion (soft delete)."""
    user = create_test_user(db_session, "delete_file_user")
    project = create_test_project(db_session, user.id)
    file = create_test_file(db_session, project.id, "To Delete")
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["read", "write"])

    response = await client.delete(
        f"/api/v1/agent/files/{file.id}",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert "deleted" in data["message"].lower()

    # Verify soft delete
    db_session.refresh(file)
    assert file.is_deleted is True


@pytest.mark.integration
async def test_delete_file_not_found(client: AsyncClient, db_session: Session):
    """Test deleting non-existent file returns 404."""
    user = create_test_user(db_session, "delete_file_user2")
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["read", "write"])

    response = await client.delete(
        "/api/v1/agent/files/00000000-0000-0000-0000-000000000000",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 404


# =============================================================================
# Chat Endpoint Tests
# =============================================================================


@pytest.mark.integration
@pytest.mark.skip(reason="POST /api/v1/agent/chat not implemented; chat is at /api/v1/agent/stream")
async def test_chat_missing_project_id(client: AsyncClient, db_session: Session):
    """Test that chat without project_id returns 422."""
    user = create_test_user(db_session, "chat_user1")
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["chat"])

    response = await client.post(
        "/api/v1/agent/chat",
        headers={"X-Agent-API-Key": plain_key},
        json={"message": "Hello"},
    )

    assert response.status_code == 422


@pytest.mark.integration
async def test_chat_project_not_found(client: AsyncClient, db_session: Session):
    """Test chat with non-existent project returns 404."""
    user = create_test_user(db_session, "chat_user2")
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["chat"])

    response = await client.post(
        "/api/v1/agent/chat",
        headers={"X-Agent-API-Key": plain_key},
        json={
            "project_id": "00000000-0000-0000-0000-000000000000",
            "message": "Hello",
        },
    )

    assert response.status_code == 404


@pytest.mark.integration
@pytest.mark.skip(reason="POST /api/v1/agent/chat not implemented; chat is at /api/v1/agent/stream")
async def test_chat_success_with_mock(client: AsyncClient, db_session: Session):
    """Test chat endpoint with mocked LangGraph workflow."""
    user = create_test_user(db_session, "chat_user3")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(db_session, user.id, scopes=["chat"])

    # Mock the LangGraph workflow
    from agent.core.workflow_events import StreamEvent, StreamEventType

    async def mock_stream():
        yield StreamEvent(type=StreamEventType.TEXT, data={"text": "Hello"})
        yield StreamEvent(type=StreamEventType.MESSAGE_END, data={"stop_reason": "end_turn"})

    # Mock the service factory and its process_stream method
    with patch("agent.service.get_agent_service") as mock_get_service:
        mock_service = mock_get_service.return_value
        mock_service.process_stream.return_value = mock_stream()

        response = await client.post(
            "/api/v1/agent/chat",
            headers={"X-Agent-API-Key": plain_key},
            json={
                "project_id": str(project.id),
                "message": "Hello, agent!",
            },
        )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]


# =============================================================================
# Search Endpoint Tests
# =============================================================================


@pytest.mark.integration
async def test_search_missing_query(client: AsyncClient, db_session: Session):
    """Test that search without query returns 422."""
    user = create_test_user(db_session, "search_user1")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.post(
        f"/api/v1/agent/projects/{project.id}/search",
        headers={"X-Agent-API-Key": plain_key},
        json={"top_k": 10},
    )

    assert response.status_code == 422


@pytest.mark.integration
async def test_search_service_unavailable(client: AsyncClient, db_session: Session):
    """Test search when vector service is unavailable."""
    user = create_test_user(db_session, "search_user2")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(db_session, user.id)

    with patch("services.llama_index.get_llama_index_service") as mock_service:
        mock_service.side_effect = Exception("Service unavailable")

        response = await client.post(
            f"/api/v1/agent/projects/{project.id}/search",
            headers={"X-Agent-API-Key": plain_key},
            json={"query": "test query"},
        )

        assert response.status_code == 503


@pytest.mark.integration
async def test_search_success_with_mock(client: AsyncClient, db_session: Session):
    """Test successful search with mocked vector service."""
    user = create_test_user(db_session, "search_user3")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(db_session, user.id)

    # Mock search result
    from dataclasses import dataclass

    @dataclass
    class MockSearchResult:
        entity_id: str
        title: str
        entity_type: str
        content: str
        score: float
        snippet: str | None
        line_start: int | None
        fused_score: float | None
        sources: list[str] | None
        metadata: dict

    mock_results = [
        MockSearchResult(
            entity_id="file-1",
            title="Chapter 1",
            entity_type="draft",
            content="The hero begins...",
            score=0.95,
            snippet="The hero begins...",
            line_start=1,
            fused_score=0.98,
            sources=["semantic", "lexical"],
            metadata={},
        )
    ]

    with patch("services.llama_index.get_llama_index_service") as mock_service_factory:
        mock_service = mock_service_factory.return_value
        mock_service.hybrid_search.return_value = mock_results

        response = await client.post(
            f"/api/v1/agent/projects/{project.id}/search",
            headers={"X-Agent-API-Key": plain_key},
            json={"query": "hero", "top_k": 10},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "hero"
        assert data["result_count"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["title"] == "Chapter 1"
        assert data["results"][0]["content"] is None
        assert data["results"][0]["snippet"] == "The hero begins..."
        assert data["results"][0]["line_start"] == 1
        assert data["results"][0]["fused_score"] == 0.98
        assert data["results"][0]["sources"] == ["semantic", "lexical"]


@pytest.mark.integration
async def test_search_returns_lexical_results_when_embeddings_disabled(
    client: AsyncClient, db_session: Session, monkeypatch
):
    """The direct Agent search API remains usable while provider embeddings are paused."""
    from services.infra import vector_search_service as vss

    user = create_test_user(db_session, "search_user_embeddings_paused")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(db_session, user.id)

    monkeypatch.setenv("VECTOR_EMBEDDINGS_ENABLED", "false")
    monkeypatch.setattr(vss, "HYBRID_ENABLE_LEXICAL", True)
    monkeypatch.setattr(vss, "_LEXICAL_SEARCH_SEMAPHORE", threading.Semaphore(1))
    with patch.object(vss.LlamaIndexService, "__init__", return_value=None):
        service = vss.LlamaIndexService()
    service.semantic_search = MagicMock()  # type: ignore[method-assign]
    service._lexical_search = MagicMock(  # type: ignore[method-assign]
        return_value=[
            vss.SearchResult(
                entity_type="draft",
                entity_id="file-lexical",
                title="Lexical result",
                content="keyword context",
                score=0.5,
                snippet="keyword context",
                line_start=1,
                sources=["lexical"],
            )
        ]
    )

    import database

    monkeypatch.setattr(database, "create_session", lambda: MagicMock())
    with patch("services.llama_index.get_llama_index_service", return_value=service):
        response = await client.post(
            f"/api/v1/agent/projects/{project.id}/search",
            headers={"X-Agent-API-Key": plain_key},
            json={"query": "keyword", "include_content": True},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["result_count"] == 1
    assert data["results"][0]["title"] == "Lexical result"
    assert data["results"][0]["sources"] == ["lexical"]
    service.semantic_search.assert_not_called()


@pytest.mark.integration
async def test_search_offloads_sync_service_without_request_session(
    client: AsyncClient, db_session: Session
):
    user = create_test_user(db_session, "search_user_thread")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(db_session, user.id)

    async def run_inline(function, /, *args, **kwargs):
        return function(*args, **kwargs)

    with patch("services.llama_index.get_llama_index_service") as mock_factory:
        mock_factory.return_value.hybrid_search.return_value = []
        with patch("api.vector_search.asyncio.to_thread", new_callable=AsyncMock) as to_thread:
            to_thread.side_effect = run_inline
            response = await client.post(
                f"/api/v1/agent/projects/{project.id}/search",
                headers={"X-Agent-API-Key": plain_key},
                json={"query": "hero"},
            )

    assert response.status_code == 200
    to_thread.assert_awaited_once()
    _, *thread_args = to_thread.await_args.args
    assert db_session not in thread_args


@pytest.mark.integration
async def test_search_include_content_true(client: AsyncClient, db_session: Session):
    """Search should include full content only when include_content=true."""
    user = create_test_user(db_session, "search_user_content")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(db_session, user.id)

    from dataclasses import dataclass

    @dataclass
    class MockSearchResult:
        entity_id: str
        title: str
        entity_type: str
        content: str
        score: float
        snippet: str | None
        line_start: int | None
        fused_score: float | None
        sources: list[str] | None
        metadata: dict

    mock_results = [
        MockSearchResult(
            entity_id="file-content-1",
            title="Chapter Content",
            entity_type="draft",
            content="完整正文内容",
            score=0.82,
            snippet="正文片段",
            line_start=5,
            fused_score=0.84,
            sources=["semantic"],
            metadata={},
        )
    ]

    with patch("services.llama_index.get_llama_index_service") as mock_service_factory:
        mock_service = mock_service_factory.return_value
        mock_service.hybrid_search.return_value = mock_results

        response = await client.post(
            f"/api/v1/agent/projects/{project.id}/search",
            headers={"X-Agent-API-Key": plain_key},
            json={"query": "正文", "top_k": 10, "include_content": True},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["result_count"] == 1
        assert data["results"][0]["content"] == "完整正文内容"
        assert data["results"][0]["snippet"] == "正文片段"


@pytest.mark.integration
async def test_search_top_k_bounds(client: AsyncClient, db_session: Session):
    """Test search top_k parameter validation (Pydantic enforces max 50)."""
    user = create_test_user(db_session, "search_user4")
    project = create_test_project(db_session, user.id)
    _, plain_key = create_test_api_key(db_session, user.id)

    # Test top_k > 50 returns 422 (Pydantic validation)
    response = await client.post(
        f"/api/v1/agent/projects/{project.id}/search",
        headers={"X-Agent-API-Key": plain_key},
        json={"query": "test", "top_k": 100},  # Exceeds max of 50
    )

    assert response.status_code == 422
    data = response.json()
    assert "detail" in data


# =============================================================================
# Project Access Validation Tests
# =============================================================================


@pytest.mark.integration
async def test_file_access_from_different_project(client: AsyncClient, db_session: Session):
    """Test that user cannot access files from projects they don't own."""
    user = create_test_user(db_session, "access_user1")
    other_user = create_test_user(db_session, "other_access_user")
    other_project = create_test_project(db_session, other_user.id, "Other Project")
    other_file = create_test_file(db_session, other_project.id, "Other File")
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.get(
        f"/api/v1/agent/files/{other_file.id}",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 404


@pytest.mark.integration
async def test_api_key_project_restriction(client: AsyncClient, db_session: Session):
    """Test that API key can only access allowed projects."""
    user = create_test_user(db_session, "access_user2")
    project1 = create_test_project(db_session, user.id, "Allowed")
    project2 = create_test_project(db_session, user.id, "Blocked")
    _, plain_key = create_test_api_key(
        db_session, user.id, project_ids=[project1.id]
    )

    # Should work for allowed project
    response = await client.get(
        f"/api/v1/agent/projects/{project1.id}",
        headers={"X-Agent-API-Key": plain_key},
    )
    assert response.status_code == 200

    # Should fail for blocked project
    response = await client.get(
        f"/api/v1/agent/projects/{project2.id}",
        headers={"X-Agent-API-Key": plain_key},
    )
    assert response.status_code == 403


@pytest.mark.integration
async def test_soft_deleted_project_not_listed(client: AsyncClient, db_session: Session):
    """Test that soft-deleted projects are not listed."""
    user = create_test_user(db_session, "soft_delete_user")
    project = create_test_project(db_session, user.id, "Deleted Project")
    project.is_deleted = True
    db_session.add(project)
    db_session.commit()
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.get(
        "/api/v1/agent/projects",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 0


@pytest.mark.integration
async def test_soft_deleted_file_not_listed(client: AsyncClient, db_session: Session):
    """Test that soft-deleted files are not listed."""
    user = create_test_user(db_session, "soft_delete_file_user")
    project = create_test_project(db_session, user.id)
    file = create_test_file(db_session, project.id, "Deleted File")
    file.is_deleted = True
    db_session.add(file)
    db_session.commit()
    _, plain_key = create_test_api_key(db_session, user.id)

    response = await client.get(
        f"/api/v1/agent/projects/{project.id}/files",
        headers={"X-Agent-API-Key": plain_key},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert len(data["files"]) == 0
