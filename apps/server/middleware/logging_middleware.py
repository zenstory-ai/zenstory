"""
HTTP logging middleware for FastAPI.

Logs all incoming requests and responses with structured JSON format.
Includes request ID, timing, and slow request detection.
"""

import logging
import os
import time
import uuid
from typing import Any

from fastapi import Request, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.error_handler import internal_error_response
from middleware.rate_limit import get_client_ip
from utils.logger import get_logger, log_with_context
from utils.request_context import (
    bind_request_context,
    reset_request_context,
)
from utils.sanitize import sanitize_for_logging, sanitize_query_params

logger = get_logger(__name__)

# Slow request threshold (in milliseconds)
SLOW_REQUEST_THRESHOLD = int(os.getenv("SLOW_REQUEST_THRESHOLD", "500"))

# Maximum body size to log (in bytes, to avoid log spam)
MAX_BODY_SIZE = int(os.getenv("MAX_LOG_BODY_SIZE", "4096"))

# Request body logging is expensive for high-throughput APIs.
# Keep it opt-in in production.
LOG_REQUEST_BODY = os.getenv("LOG_REQUEST_BODY", "false").lower() == "true"

# Trace ID (user action correlation) header name.
TRACE_ID_HEADER = "X-Trace-ID"

# Maximum trace id length accepted from clients (defense-in-depth).
MAX_TRACE_ID_LENGTH = int(os.getenv("MAX_TRACE_ID_LENGTH", "64"))


class LoggingMiddleware:
    """
    Middleware for logging HTTP requests and responses.

    Features:
    - Generates unique request ID for each request
    - Logs request method, path, query params, and body
    - Measures request duration
    - Logs response status code and body
    - Marks slow requests (>500ms) with WARNING level
    - Sanitizes sensitive information in logs
    """

    def __init__(self, app: ASGIApp):
        """Initialize middleware."""
        self.app = app
        log_with_context(
            logger,
            logging.INFO,
            "Logging middleware initialized",
            slow_request_threshold_ms=SLOW_REQUEST_THRESHOLD,
            max_body_size_bytes=MAX_BODY_SIZE,
            log_request_body=LOG_REQUEST_BODY,
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        Process request and log it.

        Args:
            scope: ASGI scope
            receive: ASGI receive callable
            send: ASGI send callable
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        # Generate request ID and start time.
        request_id = str(uuid.uuid4())[:8]
        trace_id = self._resolve_trace_id(request)
        start_time = time.perf_counter()

        # Make request identifiers available to downstream handlers.
        request.state.request_id = request_id
        request.state.trace_id = trace_id

        # Bind identifiers into contextvars so all log_with_context calls
        # automatically include them (including deep agent/tool logs).
        ctx_tokens = bind_request_context(request_id=request_id, trace_id=trace_id)

        request_info, receive_wrapper = self._build_request_logging_state(
            request,
            receive,
            request_id,
            trace_id,
        )

        # Log incoming request
        log_with_context(
            logger,
            logging.INFO,
            f"{request.method} {request.url.path}",
            **request_info,
        )

        response_status_code = 500
        response_info: dict[str, Any] = {"status_code": 500, "content_type": "unknown"}
        response_started = False
        response_logged = False
        response_failed = False

        def log_completion(**extra_fields: Any) -> None:
            nonlocal response_logged
            duration_ms = (time.perf_counter() - start_time) * 1000
            is_stream = self._is_streaming_response(response_info)
            log_level = self._get_response_log_level(
                response_status_code,
                duration_ms,
                is_stream=is_stream,
            )
            log_with_context(
                logger,
                log_level,
                f"{request.method} {request.url.path} - {response_status_code}",
                # The stack (if any) is on the "Exception occurred" line.
                exc_info=False,
                **request_info,
                **response_info,
                duration_ms=round(duration_ms, 2),
                is_slow=(not is_stream) and duration_ms > SLOW_REQUEST_THRESHOLD,
                **extra_fields,
            )
            response_logged = True

        async def send_wrapper(message: Message) -> None:
            nonlocal response_status_code, response_info, response_started

            if message["type"] == "http.response.start":
                response_started = True
                response_status_code = int(message["status"])
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("utf-8")))
                headers.append((TRACE_ID_HEADER.lower().encode("utf-8"), trace_id.encode("utf-8")))
                message["headers"] = headers
                response_info = self._extract_response_info_from_asgi(message)

            if message["type"] == "http.response.body" and not message.get("more_body", False):
                log_completion()

            await send(message)

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        except Exception as e:
            # Log with the stack while request_id is still bound; exception
            # handlers registered for ``Exception`` run outside this middleware
            # (and outside CORS), after the context has been reset.
            duration_ms = (time.perf_counter() - start_time) * 1000
            log_with_context(
                logger,
                logging.ERROR,
                f"{request.method} {request.url.path} - Exception occurred",
                exc_info=e,
                **request_info,
                error=str(e),
                error_type=type(e).__name__,
                duration_ms=round(duration_ms, 2),
            )
            if response_started:
                response_failed = True
                raise
            # Answer here so the 500 still passes through CORSMiddleware and
            # carries X-Request-ID.
            await self._send_internal_error(send_wrapper, request_id)
        finally:
            if response_started and not response_logged:
                # The response never finished: the app raised mid-stream, or
                # the client went away (common for SSE streams).
                if response_failed:
                    log_completion(incomplete_response=True)
                else:
                    log_completion(client_disconnected=True)
            reset_request_context(ctx_tokens)

    async def _send_internal_error(self, send: Send, request_id: str) -> None:
        response = internal_error_response(request_id)
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": response.raw_headers,
            }
        )
        await send({"type": "http.response.body", "body": response.body})

    def _build_request_logging_state(
        self,
        request: Request,
        receive: Receive,
        request_id: str,
        trace_id: str,
    ) -> tuple[dict[str, Any], Receive]:
        """Prepare request logging info and a receive wrapper for optional body capture."""
        query_params = sanitize_query_params(request.query_params.multi_items())
        if request.url.path == "/api/v1/payments/zpay/notify":
            query_params = "[redacted payment callback]"

        info = {
            "request_id": request_id,
            "trace_id": trace_id,
            "method": request.method,
            "path": request.url.path,
            "query_params": query_params,
            "client_host": self._get_client_host(request),
            "user_agent": request.headers.get("user-agent", "unknown"),
        }

        async def passthrough_receive() -> Message:
            return await receive()

        if LOG_REQUEST_BODY and request.method in ["POST", "PUT", "PATCH"]:
            content_type = request.headers.get("content-type", "").lower()
            if self._should_skip_body_logging(content_type):
                info["request_body"] = f"[Skipped body logging for content type: {content_type or 'unknown'}]"
            else:
                body_chunks: bytearray = bytearray()
                body_truncated = False

                async def capture_receive() -> Message:
                    nonlocal body_truncated
                    message = await receive()
                    if message["type"] == "http.request":
                        body = message.get("body", b"")
                        if body and not body_truncated:
                            remaining = MAX_BODY_SIZE + 1 - len(body_chunks)
                            if remaining > 0:
                                body_chunks.extend(body[:remaining])
                            if len(body_chunks) > MAX_BODY_SIZE:
                                body_truncated = True
                        if not message.get("more_body", False):
                            info["request_body"] = self._format_captured_body(bytes(body_chunks), body_truncated)
                    return message

                return info, capture_receive

        return info, passthrough_receive

    def _resolve_trace_id(self, request: Request) -> str:
        raw = (request.headers.get(TRACE_ID_HEADER) or "").strip()
        if raw and len(raw) <= MAX_TRACE_ID_LENGTH:
            return raw
        return uuid.uuid4().hex

    def _extract_response_info(self, response: Response) -> dict[str, Any]:
        """
        Extract response information for logging.

        Args:
            response: HTTP response

        Returns:
            Dictionary with response information
        """
        info = {
            "status_code": response.status_code,
            "content_type": response.headers.get("content-type", "unknown"),
        }

        return info

    def _extract_response_info_from_asgi(self, message: Message) -> dict[str, Any]:
        """Extract response information from an ASGI http.response.start message."""
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in message.get("headers", [])
        }
        return {
            "status_code": int(message["status"]),
            "content_type": headers.get("content-type", "unknown"),
        }

    @staticmethod
    def _is_streaming_response(response_info: dict[str, Any]) -> bool:
        content_type = str(response_info.get("content_type", "")).lower()
        return content_type.startswith("text/event-stream")

    def _get_response_log_level(
        self,
        status_code: int,
        duration_ms: float,
        *,
        is_stream: bool = False,
    ) -> int:
        """Determine response log level.

        SSE streams stay open for the whole agent run, so their duration is
        not a latency signal and never marks them as slow.
        """
        if status_code >= 500:
            return logging.ERROR
        if not is_stream and duration_ms > SLOW_REQUEST_THRESHOLD:
            return logging.WARNING
        return logging.INFO

    async def _get_request_body(self, request: Request) -> str:
        """
        Get request body with size limit and sanitization.

        Args:
            request: HTTP request

        Returns:
            Sanitized request body string
        """
        try:
            body = await request.body()

            # Check size limit
            if len(body) > MAX_BODY_SIZE:
                return f"[Body too large: {len(body)} bytes]"

            # Try to parse as JSON for sanitization
            import json as json_module

            try:
                body_dict = json_module.loads(body.decode())
                sanitized = sanitize_for_logging(body_dict)
                return json_module.dumps(sanitized, ensure_ascii=False)
            except (json_module.JSONDecodeError, UnicodeDecodeError):
                # Not JSON, just sanitize the string
                return sanitize_for_logging(body.decode())
        except Exception:
            return "[Failed to read body]"

    def _format_captured_body(self, body: bytes, truncated: bool) -> str:
        """Format captured request body with size limit and sanitization."""
        if truncated:
            return f"[Body too large: >{MAX_BODY_SIZE} bytes]"

        try:
            import json as json_module

            body_dict = json_module.loads(body.decode())
            sanitized = sanitize_for_logging(body_dict)
            return json_module.dumps(sanitized, ensure_ascii=False)
        except (ValueError, UnicodeDecodeError):
            try:
                return sanitize_for_logging(body.decode())
            except Exception:
                return "[Failed to read body]"

    def _should_skip_body_logging(self, content_type: str) -> bool:
        """
        Return True for binary-like payloads where body logging is unsafe/noisy.

        Multipart parsing is especially sensitive to upstream body consumption,
        so we avoid reading those bodies in middleware.
        """
        if not content_type:
            return False

        return (
            "multipart/form-data" in content_type
            or "application/octet-stream" in content_type
        )

    def _get_client_host(self, request: Request) -> str:
        """
        Get client IP address.

        Args:
            request: HTTP request

        Returns:
            Client IP address
        """
        return get_client_ip(request)
