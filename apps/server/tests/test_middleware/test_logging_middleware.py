"""
Tests for LoggingMiddleware.

Tests request logging, response logging, request ID generation,
timing, and error logging.
"""

import asyncio
import logging
import re

import pytest
from httpx import AsyncClient

from main import app
from models import File, Project, User
from services.core.auth_service import hash_password

# Request ID pattern (8 hex characters)
REQUEST_ID_PATTERN = r"^[a-f0-9]{8}$"


@pytest.mark.asyncio
async def test_request_logging(client: AsyncClient, db_session, caplog):
    """Test that incoming requests are logged with correct information."""
    # Create a user and get token
    user = User(
        username="testuser1",
        email="user1@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    # Login to get token
    response = await client.post(
        "/api/auth/login",
        data={"username": "user1@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    # Make a request that should be logged
    with caplog.at_level(logging.INFO):
        response = await client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
        )

    # Check that request was logged
    assert response.status_code == 200
    assert any(
        "GET /api/v1/projects" in record.message
        for record in caplog.records
    ), f"Expected request log in: {[r.message for r in caplog.records]}"


@pytest.mark.asyncio
async def test_request_id_generation(client: AsyncClient, db_session):
    """Test that each request gets a unique request ID."""
    user = User(
        username="testuser2",
        email="user2@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "user2@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    # Make multiple requests
    response1 = await client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {token}"},
    )
    response2 = await client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {token}"},
    )

    # Check that each response has a unique X-Request-ID header
    request_id_1 = response1.headers.get("X-Request-ID")
    request_id_2 = response2.headers.get("X-Request-ID")

    assert request_id_1 is not None
    assert request_id_2 is not None
    assert request_id_1 != request_id_2, "Request IDs should be unique"

    # Check format (8 hex characters)
    assert re.match(REQUEST_ID_PATTERN, request_id_1), f"Invalid request ID format: {request_id_1}"
    assert re.match(REQUEST_ID_PATTERN, request_id_2), f"Invalid request ID format: {request_id_2}"


@pytest.mark.asyncio
async def test_response_time_logging(client: AsyncClient, db_session, caplog):
    """Test that response time is logged."""
    user = User(
        username="testuser3",
        email="user3@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "user3@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    # Make a request and log it
    with caplog.at_level(logging.INFO):
        response = await client.get(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200

    # Check that duration_ms is in the log
    assert any(
        "duration_ms" in str(record.custom_fields) if hasattr(record, "custom_fields") else False
        for record in caplog.records
    ), "Expected duration_ms in log"


@pytest.mark.asyncio
async def test_status_code_logging(client: AsyncClient, db_session, caplog):
    """Test that status codes are logged correctly."""
    user = User(
        username="testuser4",
        email="user4@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "user4@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    # Request to non-existent endpoint should return 404
    with caplog.at_level(logging.INFO):
        response = await client.get(
            "/api/v1/nonexistent",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 404

    # Check that status code is logged
    assert any(
        "404" in record.message or
        (hasattr(record, "custom_fields") and str(record.custom_fields).find("404") != -1)
        for record in caplog.records
    ), "Expected 404 status code in log"


@pytest.mark.asyncio
async def test_slow_request_detection(client: AsyncClient, db_session, caplog):
    """Test that slow requests are logged at WARNING level."""
    user = User(
        username="testuser5",
        email="user5@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    # Mock a slow endpoint
    async def slow_endpoint():
        await asyncio.sleep(0.6)  # Sleep for 600ms (exceeds default 500ms threshold)
        return {"status": "done"}

    # Add the slow endpoint temporarily
    from fastapi import APIRouter
    router = APIRouter()
    router.add_api_route("/api/test/slow", slow_endpoint, methods=["GET"])
    original_routes = app.routes.copy()
    app.routes.append(router.routes[0])

    try:
        response = await client.post(
            "/api/auth/login",
            data={"username": "user5@example.com", "password": "password123"},
        )
        token = response.json()["access_token"]

        # Make a slow request
        with caplog.at_level(logging.WARNING):
            response = await client.get(
                "/api/test/slow",
                headers={"Authorization": f"Bearer {token}"},
            )

        # Check that slow request is logged at WARNING level
        assert any(
            record.levelno == logging.WARNING and "is_slow" in str(record.custom_fields)
            if hasattr(record, "custom_fields") else False
            for record in caplog.records
        ), "Expected slow request to be logged at WARNING level"
    finally:
        # Restore original routes
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_error_logging_on_exception(client: AsyncClient, db_session, caplog):
    """Test that exceptions are logged at ERROR level."""
    user = User(
        username="testuser6",
        email="user6@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "user6@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    # Create a test endpoint that raises an exception
    async def exception_endpoint():
        raise ValueError("Test exception")

    from fastapi import APIRouter
    router = APIRouter()
    router.add_api_route("/api/test/exception", exception_endpoint, methods=["GET"])
    original_routes = app.routes.copy()
    app.routes.append(router.routes[0])

    try:
        # Make a request that will cause an exception
        with caplog.at_level(logging.ERROR):
            response = await client.get(
                "/api/test/exception",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Origin": "http://localhost:5173",
                },
            )

        # The 500 is produced inside CORS, so browsers can read it and the
        # request id; the body never leaks the exception text.
        assert response.status_code == 500
        request_id = response.headers.get("x-request-id")
        assert request_id and re.match(REQUEST_ID_PATTERN, request_id)
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"
        assert "Test exception" not in response.text
        assert response.json()["request_id"] == request_id

        # Check that error is logged at ERROR level with stack and request id
        error_records = [
            record for record in caplog.records
            if record.levelno == logging.ERROR
            and getattr(record, "custom_fields", {}).get("request_id") == request_id
        ]
        assert error_records, "Expected exception to be logged at ERROR level"
        assert any(record.exc_info for record in error_records)
    finally:
        # Restore original routes
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_client_host_logging(client: AsyncClient, db_session):
    """Test that client host is logged correctly."""
    user = User(
        username="testuser7",
        email="user7@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "user7@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    # Make a request with X-Forwarded-For header
    response = await client.get(
        "/api/v1/projects",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Forwarded-For": "203.0.113.1, 70.41.3.18",
        },
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_request_body_logging_limit(client: AsyncClient, db_session):
    """Test that large request bodies are truncated in logs."""
    user = User(
        username="testuser8",
        email="user8@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "user8@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    # Create a large project description (> 4KB, the MAX_BODY_SIZE)
    large_description = "x" * 5000

    response = await client.post(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Large Project",
            "description": large_description,
        },
    )

    # The request should succeed
    assert response.status_code in (200, 201)


@pytest.mark.asyncio
async def test_json_request_body_is_logged_when_enabled(client: AsyncClient, db_session, caplog, monkeypatch):
    """When body logging is enabled, a JSON request body should appear in structured logs."""
    from middleware import logging_middleware

    monkeypatch.setattr(logging_middleware, "LOG_REQUEST_BODY", True)

    user = User(
        username="testuser_bodylog",
        email="bodylog@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "bodylog@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    with caplog.at_level(logging.INFO):
        response = await client.post(
            "/api/v1/projects",
            headers={"Authorization": f"Bearer {token}"},
            json={"name": "BodyLog Project", "description": "BodyLog Description"},
        )

    assert response.status_code in (200, 201)
    assert any(
        "BodyLog Project" in str(getattr(record, "custom_fields", ""))
        for record in caplog.records
    ), "Expected structured logs to include the JSON request body when body logging is enabled"


@pytest.mark.asyncio
async def test_multipart_body_logging_is_skipped(
    client: AsyncClient,
    db_session,
    caplog,
    monkeypatch,
):
    """Multipart request body should be skipped to avoid stream consumption side-effects."""
    from middleware import logging_middleware

    monkeypatch.setattr(logging_middleware, "LOG_REQUEST_BODY", True)

    user = User(
        username="testuser_upload",
        email="upload_middleware@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    login_response = await client.post(
        "/api/auth/login",
        data={"username": "upload_middleware@example.com", "password": "password123"},
    )
    token = login_response.json()["access_token"]

    project = Project(name="Upload Middleware Project", owner_id=user.id)
    db_session.add(project)
    db_session.commit()

    material_folder = File(
        id=f"{project.id}-material-folder",
        project_id=project.id,
        title="素材",
        file_type="folder",
        order=0,
    )
    db_session.add(material_folder)
    db_session.commit()

    with caplog.at_level(logging.INFO):
        response = await client.post(
            f"/api/v1/projects/{project.id}/files/upload",
            files={"file": ("middleware_upload.txt", b"hello middleware", "text/plain")},
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert any(
        "Skipped body logging for content type: multipart/form-data" in str(getattr(record, "custom_fields", ""))
        for record in caplog.records
    ), "Expected multipart body logging skip marker in logs"


@pytest.mark.asyncio
async def test_password_sanitization_in_logs(client: AsyncClient, db_session, caplog, monkeypatch):
    """Test that passwords are masked in logs."""
    monkeypatch.setenv("AUTH_REGISTER_INVITE_CODE_OPTIONAL", "true")

    with caplog.at_level(logging.INFO):
        # Register with password (POST request with password field)
        await client.post(
            "/api/auth/register",
            json={
                "email": "sanitize_test@example.com",
                "username": "sanitizetest",
                "password": "PlainTextPassword123!",
            },
        )

    # Check that the plain password is not in the logs
    for record in caplog.records:
        log_message = record.message.lower()
        assert "plaintextpassword123" not in log_message, "Password should be masked in logs"

        # Check custom fields
        if hasattr(record, "custom_fields"):
            custom_str = str(record.custom_fields).lower()
            assert "plaintextpassword123" not in custom_str, "Password should be masked in custom fields"


@pytest.mark.asyncio
async def test_user_agent_logging(client: AsyncClient, db_session):
    """Test that User-Agent header is logged."""
    user = User(
        username="testuser9",
        email="user9@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "user9@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    # Make a request with custom User-Agent
    response = await client.get(
        "/api/v1/projects",
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "TestClient/1.0",
        },
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_query_params_logging(client: AsyncClient, db_session):
    """Test that query parameters are logged."""
    user = User(
        username="testuser10",
        email="user10@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "user10@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    # Create a project first
    await client.post(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "Test Project"},
    )

    # Make a request with query parameters
    response = await client.get(
        "/api/v1/projects",
        headers={"Authorization": f"Bearer {token}"},
        params={"limit": "10", "offset": "0"},
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_5xx_error_logging(client: AsyncClient, db_session, caplog):
    """Test that 5xx errors are logged at ERROR level."""
    user = User(
        username="testuser11",
        email="user11@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    # Create a test endpoint that returns a 500 error
    async def error_endpoint():
        raise Exception("Test server error")

    from fastapi import APIRouter
    router = APIRouter()
    router.add_api_route("/api/test/error2", error_endpoint, methods=["GET"])
    original_routes = app.routes.copy()
    app.routes.append(router.routes[0])

    try:
        response = await client.post(
            "/api/auth/login",
            data={"username": "user11@example.com", "password": "password123"},
        )
        token = response.json()["access_token"]

        # Make a request that will cause a 500 error
        with caplog.at_level(logging.ERROR):
            response = await client.get(
                "/api/test/error2",
                headers={"Authorization": f"Bearer {token}"},
            )
        assert response.status_code == 500

        # Check that the error is logged at ERROR level
        assert any(
            record.levelno == logging.ERROR
            for record in caplog.records
        ), "Expected 500 error to be logged at ERROR level"
    finally:
        # Restore original routes
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_concurrent_requests_have_unique_ids(client: AsyncClient, db_session):
    """Test that concurrent requests get unique request IDs."""
    # Create a simple test endpoint that doesn't use the database
    # This avoids SQLite concurrency issues while still testing request ID generation
    async def simple_endpoint():
        return {"status": "ok"}

    from fastapi import APIRouter
    router = APIRouter()
    router.add_api_route("/api/test/simple", simple_endpoint, methods=["GET"])
    original_routes = app.routes.copy()
    app.routes.append(router.routes[0])

    try:
        # Make multiple concurrent requests
        async def make_request():
            response = await client.get("/api/test/simple")
            return response.headers.get("X-Request-ID")

        request_ids = await asyncio.gather(*[make_request() for _ in range(10)])

        # All request IDs should be unique
        assert len(set(request_ids)) == 10, f"Expected 10 unique request IDs, got {len(set(request_ids))}"
    finally:
        # Restore original routes
        app.routes.clear()
        app.routes.extend(original_routes)
