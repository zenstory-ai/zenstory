from __future__ import annotations

import pytest
from fastapi import Request

from core.error_codes import ErrorCode
from core.error_handler import APIException, api_exception_handler, general_exception_handler


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/wave-d",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 12345),
            "query_string": b"",
        }
    )


def test_api_exception_accepts_message_alias():
    exc = APIException(
        error_code=ErrorCode.VALIDATION_ERROR,
        message="Friendly message",
    )

    assert exc.detail == "Friendly message"
    assert exc.error_code == ErrorCode.VALIDATION_ERROR


@pytest.mark.asyncio
async def test_api_exception_handler_surfaces_error_detail():
    response = await api_exception_handler(
        _request(),
        APIException(error_code=ErrorCode.NOT_FOUND, detail="Missing entity"),
    )

    assert response.status_code == 400
    assert response.body == b'{"detail":"ERR_NOT_FOUND","error_code":"ERR_NOT_FOUND","error_detail":"Missing entity"}'


@pytest.mark.asyncio
async def test_general_exception_handler_returns_500_payload():
    response = await general_exception_handler(_request(), RuntimeError("boom"))

    assert response.status_code == 500
    assert b'"detail":"ERR_INTERNAL_SERVER_ERROR"' in response.body


@pytest.mark.asyncio
async def test_http_exception_handler_preserves_headers():
    from fastapi import HTTPException

    from core.error_handler import http_exception_handler

    response = await http_exception_handler(
        _request(),
        HTTPException(status_code=429, detail="Rate limit exceeded.", headers={"Retry-After": "3600"}),
    )

    assert response.status_code == 429
    assert response.headers["retry-after"] == "3600"
    assert response.body == b'{"detail":"Rate limit exceeded."}'


@pytest.mark.asyncio
async def test_http_exception_handler_without_headers():
    from fastapi import HTTPException

    from core.error_handler import http_exception_handler

    response = await http_exception_handler(_request(), HTTPException(status_code=404, detail="nope"))

    assert response.status_code == 404
    assert "retry-after" not in response.headers


@pytest.mark.asyncio
async def test_api_exception_handler_preserves_headers():
    response = await api_exception_handler(
        _request(),
        APIException(error_code=ErrorCode.AUTH_TOKEN_EXPIRED, status_code=401, headers={"X-Custom-Header": "v"}),
    )

    assert response.headers["x-custom-header"] == "v"
