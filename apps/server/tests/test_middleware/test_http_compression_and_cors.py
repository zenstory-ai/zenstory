"""Regression tests for SSE-safe GZip compression and CORS preflight max_age=7200."""

from __future__ import annotations

import asyncio
import gzip
import json
from typing import Any

import pytest
from fastapi import APIRouter
from fastapi.responses import JSONResponse, Response, StreamingResponse
from httpx import AsyncClient

from main import SSESafeGZipMiddleware, _accepts_gzip, app


@pytest.mark.unit
def test_accepts_gzip_parses_quality_values():
    assert _accepts_gzip("gzip, deflate, br") is True
    assert _accepts_gzip("GZIP;q=0.8") is True
    assert _accepts_gzip("gzip;q=0, br") is False
    assert _accepts_gzip("gzip;q=0.000") is False
    assert _accepts_gzip("br, deflate") is False
    assert _accepts_gzip("") is False


@pytest.mark.asyncio
async def test_sse_safe_gzip_forwards_sse_headers_and_chunks_immediately_uncompressed():
    """SSE streams must receive http.response.start before any body chunk and flush every chunk unbuffered."""
    allow_chunk_1 = asyncio.Event()
    allow_chunk_2 = asyncio.Event()
    sent_messages: list[dict[str, Any]] = []
    start_received = asyncio.Event()
    chunk_1_received = asyncio.Event()

    async def sse_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/event-stream; charset=utf-8"),
                    (b"x-agent-run-id", b"run-sse-1"),
                ],
            }
        )
        # Do NOT emit chunk 1 until the consumer verifies headers arrived immediately!
        await allow_chunk_1.wait()
        await send(
            {
                "type": "http.response.body",
                "body": b"event: thinking\ndata: {\"step\": 1}\n\n" + (b": pad\n\n" * 120),
                "more_body": True,
            }
        )
        await allow_chunk_2.wait()
        await send(
            {
                "type": "http.response.body",
                "body": b"event: done\ndata: {\"ok\": true}\n\n",
                "more_body": False,
            }
        )

    async def capture_send(message: dict[str, Any]) -> None:
        sent_messages.append(message)
        if message["type"] == "http.response.start":
            start_received.set()
        elif message["type"] == "http.response.body" and len(sent_messages) == 2:
            chunk_1_received.set()

    middleware = SSESafeGZipMiddleware(sse_app, minimum_size=500, compresslevel=6)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/agent/stream",
        "headers": [(b"accept-encoding", b"gzip, deflate")],
    }

    async def dummy_receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    task = asyncio.create_task(middleware(scope, dummy_receive, capture_send))
    try:
        # Headers must arrive immediately even before the first SSE chunk is produced.
        await asyncio.wait_for(start_received.wait(), timeout=2.0)
        start_headers = {k.lower(): v for k, v in sent_messages[0]["headers"]}
        assert b"content-encoding" not in start_headers
        assert start_headers[b"x-agent-run-id"] == b"run-sse-1"

        # Chunk 1 must arrive immediately without waiting for chunk 2 (no gzip buffering).
        allow_chunk_1.set()
        await asyncio.wait_for(chunk_1_received.wait(), timeout=2.0)
        assert sent_messages[1]["body"].startswith(b"event: thinking\n")
        assert sent_messages[1]["more_body"] is True

        allow_chunk_2.set()
        await asyncio.wait_for(task, timeout=2.0)
        assert sent_messages[2]["body"] == b"event: done\ndata: {\"ok\": true}\n\n"
        assert sent_messages[2]["more_body"] is False
    finally:
        if not task.done():
            task.cancel()


@pytest.mark.asyncio
async def test_sse_safe_gzip_compresses_large_json_and_skips_small_or_binary():
    large_payload = json.dumps({"items": [{"id": i, "title": f"第{i}章 章节标题"} for i in range(60)]}).encode()
    assert len(large_payload) > 1000

    async def make_asgi_call(body: bytes, content_type: bytes, accept_encoding: bytes | None, *, more_body: bool = False):
        sent: list[dict[str, Any]] = []

        async def inner_app(scope, receive, send):
            await send(
                {
                    "type": "http.response.start",
                    "status": 200,
                    "headers": [(b"content-type", content_type), (b"content-length", str(len(body)).encode())],
                }
            )
            if more_body:
                half = len(body) // 2
                await send({"type": "http.response.body", "body": body[:half], "more_body": True})
                await send({"type": "http.response.body", "body": body[half:], "more_body": False})
            else:
                await send({"type": "http.response.body", "body": body, "more_body": False})

        middleware = SSESafeGZipMiddleware(inner_app, minimum_size=500, compresslevel=6)
        headers = [(b"accept-encoding", accept_encoding)] if accept_encoding is not None else []
        scope = {"type": "http", "method": "GET", "path": "/api/v1/test", "headers": headers}

        async def dummy_receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def capture_send(message: dict[str, Any]) -> None:
            sent.append(message)

        await middleware(scope, dummy_receive, capture_send)
        resp_headers = {k.lower().decode(): v.decode() for k, v in sent[0]["headers"]}
        wire_body = b"".join(msg.get("body", b"") for msg in sent[1:])
        return resp_headers, wire_body

    # 1. Large JSON with Accept-Encoding: gzip -> compressed
    headers, wire = await make_asgi_call(large_payload, b"application/json", b"gzip, deflate")
    assert headers.get("content-encoding") == "gzip"
    assert "accept-encoding" in headers.get("vary", "").lower()
    assert int(headers["content-length"]) == len(wire)
    assert len(wire) < len(large_payload)
    assert gzip.decompress(wire) == large_payload

    # 2. Large JSON without gzip -> uncompressed, still sets Vary: Accept-Encoding
    headers_no_gz, wire_no_gz = await make_asgi_call(large_payload, b"application/json", b"identity")
    assert "content-encoding" not in headers_no_gz
    assert "accept-encoding" in headers_no_gz.get("vary", "").lower()
    assert wire_no_gz == large_payload

    # 3. Small JSON (< 500 bytes) -> uncompressed
    small_payload = b'{"status":"healthy"}'
    headers_small, wire_small = await make_asgi_call(small_payload, b"application/json", b"gzip")
    assert "content-encoding" not in headers_small
    assert wire_small == small_payload

    # 4. Already-compressed binary (zip) -> bypassed untouched
    zip_payload = b"PK\x03\x04" + b"x" * 2000
    headers_zip, wire_zip = await make_asgi_call(zip_payload, b"application/zip", b"gzip")
    assert "content-encoding" not in headers_zip
    assert wire_zip == zip_payload

    # 5. Multi-chunk streaming JSON (more_body=True) -> bypassed untouched, never buffered
    headers_stream, wire_stream = await make_asgi_call(
        large_payload, b"application/x-ndjson", b"gzip", more_body=True
    )
    assert "content-encoding" not in headers_stream
    assert wire_stream == large_payload


@pytest.mark.asyncio
async def test_app_compresses_large_json_bypasses_sse_and_sets_cors_max_age_7200(client: AsyncClient):
    """End-to-end check on the real FastAPI app middleware stack."""
    router = APIRouter()

    async def large_json():
        return JSONResponse({"rows": [{"idx": i, "text": "章节内容预览" * 4} for i in range(40)]})

    async def sse_endpoint():
        async def gen():
            yield "event: thinking\ndata: {\"msg\":\"ok\"}\n\n"
            yield "event: content\ndata: {\"text\":\"" + ("正文" * 300) + "\"}\n\n"
            yield "event: done\ndata: {\"ok\":true}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    async def empty_204():
        return Response(status_code=204)

    router.add_api_route("/api/test/gzip-json", large_json, methods=["GET"])
    router.add_api_route("/api/test/gzip-sse", sse_endpoint, methods=["GET"])
    router.add_api_route("/api/test/gzip-204", empty_204, methods=["GET"])
    original_routes = app.routes.copy()
    app.routes.extend(router.routes)

    try:
        # Large JSON returns Content-Encoding: gzip and decodes cleanly
        resp_json = await client.get("/api/test/gzip-json", headers={"Accept-Encoding": "gzip"})
        assert resp_json.status_code == 200
        assert resp_json.headers.get("content-encoding") == "gzip"
        assert "accept-encoding" in resp_json.headers.get("vary", "").lower()
        assert len(resp_json.json()["rows"]) == 40

        # Small /health stays uncompressed
        resp_health = await client.get("/health", headers={"Accept-Encoding": "gzip"})
        assert resp_health.status_code == 200
        assert "content-encoding" not in resp_health.headers

        # 204 stays uncompressed
        resp_204 = await client.get("/api/test/gzip-204", headers={"Accept-Encoding": "gzip"})
        assert resp_204.status_code == 204
        assert "content-encoding" not in resp_204.headers

        # SSE stays uncompressed even when response body exceeds minimum_size
        resp_sse = await client.get("/api/test/gzip-sse", headers={"Accept-Encoding": "gzip"})
        assert resp_sse.status_code == 200
        assert resp_sse.headers.get("content-type", "").startswith("text/event-stream")
        assert "content-encoding" not in resp_sse.headers
        assert "event: done" in resp_sse.text

        # CORS preflight caches OPTIONS for 7200s
        preflight = await client.options(
            "/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )
        assert preflight.status_code == 200
        assert preflight.headers.get("access-control-max-age") == "7200"
    finally:
        app.routes[:] = original_routes
