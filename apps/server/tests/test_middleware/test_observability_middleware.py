"""Tests for request logging, body size limits and the 500 path on small apps."""

import asyncio
import logging

import pytest
from fastapi import BackgroundTasks, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from core.error_handler import http_exception_handler
from middleware import logging_middleware as logging_middleware_module
from middleware.body_size_limit import BodySizeLimitMiddleware
from middleware.logging_middleware import LoggingMiddleware
from utils.logger import get_logger, log_with_context
from utils.request_context import get_log_context

background_logger = get_logger("tests.background")


class _Payload(BaseModel):
    text: str


def _build_app(max_body_bytes: int = 1024) -> FastAPI:
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)

    @app.get("/echo")
    async def echo():
        return {"ok": True}

    @app.post("/json")
    async def accept_json(payload: _Payload):
        return {"length": len(payload.text)}

    @app.post("/raw")
    async def accept_raw(request: Request):
        body = await request.body()
        return {"length": len(body)}

    @app.get("/stream")
    async def stream():
        async def events():
            for index in range(3):
                yield f"data: {index}\n\n"
                await asyncio.sleep(0)

        return StreamingResponse(events(), media_type="text/event-stream")

    @app.get("/background")
    async def with_background(tasks: BackgroundTasks):
        def task():
            log_with_context(background_logger, logging.INFO, "background ran")

        tasks.add_task(task)
        return {"ok": True}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("database password is hunter2")

    app.add_middleware(BodySizeLimitMiddleware, max_body_bytes=max_body_bytes)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://app.example.com"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _completion_records(caplog, path: str):
    return [
        record for record in caplog.records
        if record.name == "middleware.logging_middleware"
        and record.getMessage().startswith(f"GET {path} - ")
    ]


@pytest.mark.asyncio
async def test_query_params_are_redacted_in_request_logs(caplog):
    app = _build_app()
    with caplog.at_level(logging.INFO, logger="middleware.logging_middleware"):
        async with _client(app) as client:
            response = await client.get(
                "/echo",
                params={"token": "eyJsecret.jwt.value", "email": "dave@example.com", "page": "1"},
            )

    assert response.status_code == 200
    logged = [
        record.custom_fields["query_params"]
        for record in caplog.records
        if record.name == "middleware.logging_middleware" and "query_params" in record.custom_fields
    ]
    assert logged
    for query in logged:
        assert "eyJsecret" not in query
        assert "dave@example.com" not in query
        assert "token=[redacted]" in query
        assert "page=1" in query


@pytest.mark.asyncio
async def test_sse_stream_is_not_logged_as_slow(caplog, monkeypatch):
    monkeypatch.setattr(logging_middleware_module, "SLOW_REQUEST_THRESHOLD", -1)
    app = _build_app()
    with caplog.at_level(logging.INFO, logger="middleware.logging_middleware"):
        async with _client(app) as client:
            response = await client.get("/stream")
            plain = await client.get("/echo")

    assert response.status_code == 200
    assert plain.status_code == 200
    stream_records = _completion_records(caplog, "/stream")
    assert len(stream_records) == 1
    assert stream_records[0].levelno == logging.INFO
    assert stream_records[0].custom_fields["is_slow"] is False
    # Non-stream responses still use the threshold.
    echo_records = _completion_records(caplog, "/echo")
    assert echo_records[0].levelno == logging.WARNING


@pytest.mark.asyncio
async def test_stream_cut_off_mid_response_still_gets_completion_log(caplog):
    async def streaming_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": b"data: 1\n\n", "more_body": True})
        # The app returns without the final chunk, as when a client disconnects.

    middleware = LoggingMiddleware(streaming_app)
    sent: list[dict] = []

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": "/agent/stream", "raw_path": b"/agent/stream", "query_string": b"",
        "headers": [], "client": ("10.0.0.1", 1234), "server": ("test", 80),
    }
    with caplog.at_level(logging.INFO, logger="middleware.logging_middleware"):
        await middleware(scope, receive, send)

    completion = _completion_records(caplog, "/agent/stream")
    assert len(completion) == 1
    assert completion[0].custom_fields["client_disconnected"] is True
    assert completion[0].custom_fields["status_code"] == 200


@pytest.mark.asyncio
async def test_background_task_logs_keep_request_id(caplog):
    app = _build_app()
    with caplog.at_level(logging.INFO):
        async with _client(app) as client:
            response = await client.get("/background")

    request_id = response.headers["x-request-id"]
    background = [r for r in caplog.records if r.getMessage() == "background ran"]
    assert background
    assert background[0].custom_fields["request_id"] == request_id
    # Context is cleared once the request is fully done.
    assert "request_id" not in get_log_context()


@pytest.mark.asyncio
async def test_unhandled_exception_returns_500_with_cors_and_request_id(caplog):
    app = _build_app()
    with caplog.at_level(logging.ERROR):
        async with _client(app) as client:
            response = await client.get("/boom", headers={"Origin": "https://app.example.com"})

    assert response.status_code == 500
    request_id = response.headers["x-request-id"]
    assert response.headers["access-control-allow-origin"] == "https://app.example.com"
    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()
    assert response.json()["request_id"] == request_id
    assert "hunter2" not in response.text
    stack_records = [r for r in caplog.records if r.exc_info]
    assert stack_records
    assert stack_records[0].custom_fields["request_id"] == request_id


@pytest.mark.asyncio
async def test_body_over_limit_by_content_length_is_rejected():
    app = _build_app(max_body_bytes=100)
    async with _client(app) as client:
        response = await client.post("/json", json={"text": "x" * 500})

    assert response.status_code == 413
    assert response.headers.get("x-request-id")


@pytest.mark.asyncio
async def test_chunked_body_over_limit_is_rejected():
    app = _build_app(max_body_bytes=100)

    async def chunks():
        for _ in range(10):
            yield b"y" * 50

    async with _client(app) as client:
        # A generator body is sent without Content-Length (chunked).
        response = await client.post("/raw", content=chunks())

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_body_under_limit_and_streaming_responses_pass_through():
    app = _build_app(max_body_bytes=1000)
    async with _client(app) as client:
        accepted = await client.post("/json", json={"text": "x" * 100})
        streamed = await client.get("/stream")

    assert accepted.status_code == 200
    assert accepted.json() == {"length": 100}
    assert streamed.status_code == 200
    assert streamed.text.count("data:") == 3


@pytest.mark.unit
def test_max_request_body_bytes_reads_env(monkeypatch):
    from middleware.body_size_limit import DEFAULT_MAX_REQUEST_BODY_BYTES, get_max_request_body_bytes

    monkeypatch.delenv("MAX_REQUEST_BODY_BYTES", raising=False)
    assert get_max_request_body_bytes() == DEFAULT_MAX_REQUEST_BODY_BYTES == 25 * 1024 * 1024
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "1048576")
    assert get_max_request_body_bytes() == 1048576
    monkeypatch.setenv("MAX_REQUEST_BODY_BYTES", "nope")
    assert get_max_request_body_bytes() == DEFAULT_MAX_REQUEST_BODY_BYTES
