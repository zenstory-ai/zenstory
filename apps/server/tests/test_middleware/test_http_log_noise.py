"""Keep one completion record for routing misses without hiding real errors."""

import logging

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.error_codes import ErrorCode
from core.error_handler import APIException, api_exception_handler, http_exception_handler
from middleware.logging_middleware import LoggingMiddleware


@pytest.fixture
def logged_app():
    app = FastAPI()
    app.add_middleware(LoggingMiddleware)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(APIException, api_exception_handler)

    @app.get("/ok")
    async def ok():
        return {"ok": True}

    @app.get("/business-http/{code}")
    async def business_http(code: int):
        raise HTTPException(code, "synthetic business error", headers={"X-Error": "kept"})

    @app.get("/business-api")
    async def business_api():
        raise APIException(ErrorCode.FILE_NOT_FOUND, status_code=404)

    @app.get("/boom")
    async def boom():
        raise RuntimeError("synthetic failure")

    return app


def request_records(caplog, path):
    return [
        record for record in caplog.records
        if record.name in {"middleware.logging_middleware", "core.error_handler"}
        and getattr(record, "custom_fields", {}).get("path") == path
    ]


@pytest.mark.parametrize("method,path,status", [
    ("GET", "/ok", 200),
    ("GET", "/not-a-route", 404),
    ("POST", "/ok", 405),
])
@pytest.mark.asyncio
async def test_one_visible_completion_per_normal_or_unmatched_request(
    logged_app, caplog, method, path, status,
):
    caplog.set_level(logging.INFO)
    async with AsyncClient(transport=ASGITransport(app=logged_app), base_url="http://test") as client:
        response = await client.request(method, path, headers={"X-Trace-ID": "synthetic-trace"})

    records = request_records(caplog, path)
    assert response.status_code == status
    assert len(records) == 1
    fields = records[0].custom_fields
    assert fields["status_code"] == status
    assert fields["duration_ms"] >= 0
    assert fields["request_id"] == response.headers["x-request-id"]
    assert fields["trace_id"] == response.headers["x-trace-id"] == "synthetic-trace"
    if status == 405:
        assert "GET" in response.headers["allow"]


@pytest.mark.parametrize("code", [401, 403, 404, 405, 429, 500])
@pytest.mark.asyncio
async def test_matched_business_http_errors_remain_visible(logged_app, caplog, code):
    path = f"/business-http/{code}"
    caplog.set_level(logging.INFO)
    async with AsyncClient(transport=ASGITransport(app=logged_app), base_url="http://test") as client:
        response = await client.get(path)

    assert response.status_code == code
    assert response.json() == {"detail": "synthetic business error"}
    assert response.headers["x-error"] == "kept"
    records = request_records(caplog, path)
    handlers = [r for r in records if r.name == "core.error_handler"]
    assert len(handlers) == 1 and handlers[0].levelno == logging.WARNING
    completions = [r for r in records if "duration_ms" in r.custom_fields]
    assert len(completions) == 1
    assert completions[0].levelno == (logging.ERROR if code >= 500 else logging.INFO)


@pytest.mark.asyncio
async def test_business_api_not_found_is_not_treated_as_route_noise(logged_app, caplog):
    caplog.set_level(logging.INFO)
    async with AsyncClient(transport=ASGITransport(app=logged_app), base_url="http://test") as client:
        response = await client.get("/business-api")
    assert response.status_code == 404
    assert response.json()["error_code"] == ErrorCode.FILE_NOT_FOUND
    assert any(
        record.name == "core.error_handler" and record.levelno == logging.WARNING
        and record.custom_fields.get("error_code") == ErrorCode.FILE_NOT_FOUND
        for record in request_records(caplog, "/business-api")
    )


@pytest.mark.asyncio
async def test_routing_diagnostics_remain_available_at_debug(logged_app, caplog):
    caplog.set_level(logging.DEBUG)
    async with AsyncClient(transport=ASGITransport(app=logged_app), base_url="http://test") as client:
        await client.get("/not-a-route")
    records = request_records(caplog, "/not-a-route")
    assert len(records) == 3
    starts = [r for r in records if r.name == "middleware.logging_middleware" and "status_code" not in r.custom_fields]
    handlers = [r for r in records if r.name == "core.error_handler"]
    assert len(starts) == 1 and starts[0].levelno == logging.DEBUG
    assert len(handlers) == 1 and handlers[0].levelno == logging.DEBUG


@pytest.mark.asyncio
async def test_unexpected_error_keeps_stack_and_correlated_completion(logged_app, caplog):
    caplog.set_level(logging.INFO)
    async with AsyncClient(transport=ASGITransport(app=logged_app), base_url="http://test") as client:
        response = await client.get("/boom")
    assert response.status_code == 500
    records = request_records(caplog, "/boom")
    assert len(records) == 2
    assert any(r.levelno == logging.ERROR and r.exc_info for r in records)
    assert all(r.custom_fields["request_id"] == response.headers["x-request-id"] for r in records)
