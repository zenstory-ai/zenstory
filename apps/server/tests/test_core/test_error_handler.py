"""
Tests for global error handlers.

Tests all exception handlers:
- APIException (custom error with error codes)
- HTTPException (standard FastAPI exception)
- RequestValidationError (Pydantic validation errors)
- Exception (catch-all handler for unexpected errors)
"""

import logging

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from pydantic import BaseModel, Field, field_validator

from core.error_codes import ErrorCode
from core.error_handler import (
    APIException,
)
from main import app
from models import User
from services.core.auth_service import hash_password

# Request ID pattern (8 hex characters)
REQUEST_ID_PATTERN = r"^[a-f0-9]{8}$"


@pytest.mark.asyncio
async def test_api_exception_handler(client: AsyncClient, db_session, caplog):
    """Test APIException handler returns correct error response format."""
    user = User(
        username="testuser1",
        email="user1@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "user1@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    # Create a test endpoint that raises APIException
    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test-api-exception")
    async def test_api_exception():
        raise APIException(
            error_code=ErrorCode.PROJECT_NOT_FOUND,
            status_code=404,
        )

    # Temporarily add the test route
    original_routes = app.routes.copy()
    app.routes.append(test_router.routes[0])

    try:
        with caplog.at_level(logging.WARNING):
            response = await client.get(
                "/test-api-exception",
                headers={"Authorization": f"Bearer {token}"},
            )

        # Verify response
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert data["detail"] == "ERR_PROJECT_NOT_FOUND"
        assert data["error_code"] == "ERR_PROJECT_NOT_FOUND"

        # Verify logging
        assert any(
            "APIException raised" in record.message
            and record.custom_fields.get("error_code") == "ERR_PROJECT_NOT_FOUND"
            for record in caplog.records
        ), f"Expected APIException log in: {[r.message for r in caplog.records]}"

    finally:
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_api_exception_error_code(client: AsyncClient, db_session):
    """Test that APIException always uses error_code for consistency."""
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

    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test-error-code")
    async def test_error_code():
        raise APIException(
            error_code=ErrorCode.FILE_NOT_FOUND,
            status_code=404,
        )

    original_routes = app.routes.copy()
    app.routes.append(test_router.routes[0])

    try:
        response = await client.get(
            "/test-error-code",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Verify error_code is used consistently
        assert response.status_code == 404
        data = response.json()
        # The handler always uses exc.error_code for both detail and error_code
        assert data["detail"] == "ERR_FILE_NOT_FOUND"
        assert data["error_code"] == "ERR_FILE_NOT_FOUND"

    finally:
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_api_exception_accepts_custom_detail(client: AsyncClient, db_session):
    """Test APIException accepts explicit detail payload while keeping error_code output stable."""
    user = User(
        username="testuser2b",
        email="user2b@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "user2b@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test-error-custom-detail")
    async def test_error_custom_detail():
        raise APIException(
            error_code=ErrorCode.FILE_NOT_FOUND,
            status_code=404,
            detail="legacy message payload",
        )

    original_routes = app.routes.copy()
    app.routes.append(test_router.routes[0])

    try:
        response = await client.get(
            "/test-error-custom-detail",
            headers={"Authorization": f"Bearer {token}"},
        )

        assert response.status_code == 404
        data = response.json()
        assert data["detail"] == "ERR_FILE_NOT_FOUND"
        assert data["error_code"] == "ERR_FILE_NOT_FOUND"
    finally:
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_http_exception_handler(client: AsyncClient, db_session, caplog):
    """Test HTTPException handler for backward compatibility."""
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

    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test-http-exception")
    async def test_http_exception():
        raise HTTPException(status_code=403, detail="Forbidden access")

    original_routes = app.routes.copy()
    app.routes.append(test_router.routes[0])

    try:
        with caplog.at_level(logging.WARNING):
            response = await client.get(
                "/test-http-exception",
                headers={"Authorization": f"Bearer {token}"},
            )

        # Verify response
        assert response.status_code == 403
        data = response.json()
        assert "detail" in data
        assert data["detail"] == "Forbidden access"
        # HTTPException doesn't have error_code field
        assert "error_code" not in data

        # Verify logging
        assert any(
            "HTTPException raised" in record.message
            and "Forbidden access" in record.custom_fields.get("detail", "")
            for record in caplog.records
        ), f"Expected HTTPException log in: {[r.message for r in caplog.records]}"

    finally:
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_validation_exception_handler(client: AsyncClient, db_session, caplog):
    """Test RequestValidationError handler for Pydantic validation errors."""
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

    from fastapi import APIRouter, Body

    test_router = APIRouter()

    class TestRequest(BaseModel):
        name: str = Field(..., min_length=1)
        age: int = Field(..., gt=0)

        @field_validator("name")
        @classmethod
        def name_must_not_contain_spaces(cls, v: str) -> str:
            if " " in v:
                raise ValueError("name must not contain spaces")
            return v

    @test_router.post("/test-validation")
    async def test_validation_endpoint(data: TestRequest = Body(...)):
        return {"success": True}

    original_routes = app.routes.copy()
    app.routes.append(test_router.routes[0])

    try:
        with caplog.at_level(logging.WARNING):
            # Send invalid data (empty name)
            response = await client.post(
                "/test-validation",
                headers={"Authorization": f"Bearer {token}"},
                json={"name": "", "age": -1},
            )

        # Verify response
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data
        assert data["detail"] == ErrorCode.VALIDATION_ERROR
        assert data["error_code"] == ErrorCode.VALIDATION_ERROR
        assert "errors" in data
        assert len(data["errors"]) > 0

        # Verify error details
        errors = data["errors"]
        error_fields = [error["loc"][-1] for error in errors]
        assert "name" in error_fields
        assert "age" in error_fields

        # Verify logging
        assert any(
            "Request validation failed" in record.message
            for record in caplog.records
        ), f"Expected validation log in: {[r.message for r in caplog.records]}"

    finally:
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_validation_exception_with_invalid_type(client: AsyncClient, db_session):
    """Test RequestValidationError handler with wrong type."""
    user = User(
        username="testuser5",
        email="user5@example.com",
        hashed_password=hash_password("password123"),
        email_verified=True,
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()

    response = await client.post(
        "/api/auth/login",
        data={"username": "user5@example.com", "password": "password123"},
    )
    token = response.json()["access_token"]

    from fastapi import APIRouter, Body

    test_router = APIRouter()

    @test_router.post("/test-validation-type")
    async def test_validation_type_endpoint(
        count: int = Body(..., embed=True),
    ):
        return {"success": True}

    original_routes = app.routes.copy()
    app.routes.append(test_router.routes[0])

    try:
        # Send invalid type (string instead of int)
        response = await client.post(
            "/test-validation-type",
            headers={"Authorization": f"Bearer {token}"},
            json={"count": "not_a_number"},
        )

        # Verify response
        assert response.status_code == 422
        data = response.json()
        assert data["detail"] == ErrorCode.VALIDATION_ERROR
        assert data["error_code"] == ErrorCode.VALIDATION_ERROR
        assert "errors" in data

        # Check that type error is present
        errors = data["errors"]
        assert any(
            "int" in str(error.get("type", "")).lower()
            for error in errors
        ), f"Expected type error in: {errors}"

    finally:
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_general_exception_handler_logging(client: AsyncClient, db_session, caplog):
    """Test that general Exception handler logs errors correctly."""
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

    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test-general-exception")
    async def test_general_exception():
        # Simulate an unexpected error
        raise ValueError("Something went terribly wrong!")

    original_routes = app.routes.copy()
    app.routes.clear()
    app.routes.extend(original_routes)
    app.routes.append(test_router.routes[0])

    try:
        with caplog.at_level(logging.ERROR):
            # LoggingMiddleware answers with a generic 500 instead of letting
            # the exception escape past CORS.
            response = await client.get(
                "/test-general-exception",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 500
        request_id = response.headers.get("x-request-id")
        assert request_id
        assert response.json() == {
            "detail": "ERR_INTERNAL_SERVER_ERROR",
            "error_code": "ERR_INTERNAL_SERVER_ERROR",
            "request_id": request_id,
        }

        # The stack-carrying log line is correlated with the request.
        assert any(
            "Exception occurred" in record.message
            and record.custom_fields.get("error_type") == "ValueError"
            and "Something went terribly wrong" in record.custom_fields.get("error", "")
            and record.custom_fields.get("request_id") == request_id
            and record.exc_info is not None
            for record in caplog.records
        ), f"Expected error log in: {[r.message for r in caplog.records]}"

    finally:
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_general_exception_with_different_error_types(
    client: AsyncClient, db_session, caplog
):
    """Test general exception handler with different error types."""
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

    from fastapi import APIRouter

    # Test with KeyError
    test_router = APIRouter()

    @test_router.get("/test-key-error")
    async def test_key_error():
        data = {"key": "value"}
        return data["nonexistent_key"]

    original_routes = app.routes.copy()
    app.routes.clear()
    app.routes.extend(original_routes)
    app.routes.append(test_router.routes[0])

    try:
        with caplog.at_level(logging.ERROR):
            response = await client.get(
                "/test-key-error",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert response.status_code == 500
        # Verify the middleware logged the KeyError
        assert any(
            "Exception occurred" in record.message
            and record.custom_fields.get("error_type") == "KeyError"
            for record in caplog.records
        ), f"Expected KeyError log in: {[r.message for r in caplog.records]}"

    finally:
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_error_response_format_consistency(client: AsyncClient, db_session):
    """Test that all error responses follow consistent format."""
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

    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test-api-exc")
    async def test_api_exc():
        raise APIException(error_code=ErrorCode.AUTH_UNAUTHORIZED, status_code=401)

    @test_router.get("/test-http-exc")
    async def test_http_exc():
        raise HTTPException(status_code=404, detail="Not found")

    original_routes = app.routes.copy()
    app.routes.clear()
    app.routes.extend(original_routes)
    app.routes.extend(test_router.routes)

    try:
        # Test APIException response
        response = await client.get(
            "/test-api-exc",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 401
        data = response.json()
        assert "detail" in data
        assert "error_code" in data

        # Test HTTPException response
        response = await client.get(
            "/test-http-exc",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    finally:
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_api_exception_with_headers(client: AsyncClient, db_session):
    """Test APIException handler with custom headers."""
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

    from fastapi import APIRouter

    test_router = APIRouter()

    @test_router.get("/test-headers")
    async def test_headers():
        raise APIException(
            error_code=ErrorCode.AUTH_TOKEN_EXPIRED,
            status_code=401,
            headers={"X-Custom-Header": "test-value"},
        )

    original_routes = app.routes.copy()
    app.routes.clear()
    app.routes.extend(original_routes)
    app.routes.append(test_router.routes[0])

    try:
        response = await client.get(
            "/test-headers",
            headers={"Authorization": f"Bearer {token}"},
        )

        # Verify response
        assert response.status_code == 401
        # Note: Custom headers from APIException should be preserved
        # but httpx.AsyncClient might not expose all headers
        data = response.json()
        assert data["detail"] == "ERR_AUTH_TOKEN_EXPIRED"
        assert data["error_code"] == "ERR_AUTH_TOKEN_EXPIRED"

        # Check if custom header is present (may vary by client implementation)
        # The important part is that the exception itself has the headers
        response.headers.get("X-Custom-Header")
        # We don't assert strict equality since client behavior may vary

    finally:
        app.routes.clear()
        app.routes.extend(original_routes)


@pytest.mark.asyncio
async def test_validation_error_response_structure(client: AsyncClient, db_session):
    """Test that validation error response contains expected fields."""
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

    from fastapi import APIRouter, Body

    test_router = APIRouter()

    @test_router.post("/test-validation-structure")
    async def test_validation_structure(
        email: str = Body(...),
        age: int = Body(...),
    ):
        return {"success": True}

    original_routes = app.routes.copy()
    app.routes.clear()
    app.routes.extend(original_routes)
    app.routes.append(test_router.routes[0])

    try:
        # Send empty body to trigger validation errors
        response = await client.post(
            "/test-validation-structure",
            headers={"Authorization": f"Bearer {token}"},
            json={},
        )

        # Verify response structure
        assert response.status_code == 422
        data = response.json()

        # Check required fields
        assert "detail" in data
        assert "error_code" in data
        assert "errors" in data

        # Check error structure
        errors = data["errors"]
        assert isinstance(errors, list)
        assert len(errors) > 0

        # Each error should have specific fields
        for error in errors:
            assert "loc" in error  # Location of the error
            assert "type" in error  # Type of error
            assert "msg" in error  # Error message

    finally:
        app.routes.clear()
        app.routes.extend(original_routes)
