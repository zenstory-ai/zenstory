"""
Request body size limit middleware.

Rejects request bodies larger than ``MAX_REQUEST_BODY_BYTES`` with 413 before
handlers buffer them in memory. Both a declared ``Content-Length`` and the
bytes actually received (chunked uploads, or a Content-Length that lies) are
checked. Responses, including SSE streams, are never touched.
"""

import logging
import os

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.error_codes import ErrorCode
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)

DEFAULT_MAX_REQUEST_BODY_BYTES = 25 * 1024 * 1024


def get_max_request_body_bytes() -> int:
    """Read the limit from the environment; invalid or non-positive values fall back to the default."""
    raw = os.getenv("MAX_REQUEST_BODY_BYTES", "").strip()
    if not raw:
        return DEFAULT_MAX_REQUEST_BODY_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_REQUEST_BODY_BYTES
    return value if value > 0 else DEFAULT_MAX_REQUEST_BODY_BYTES


class RequestBodyTooLarge(HTTPException):
    """Raised from ``receive`` once the streamed body passes the limit.

    It is an ``HTTPException`` so FastAPI's body parsing re-raises it as-is
    and the registered HTTP exception handler renders the 413.
    """

    def __init__(self) -> None:
        super().__init__(status_code=413, detail=ErrorCode.FILE_TOO_LARGE)


def _payload_too_large_response() -> JSONResponse:
    return JSONResponse(
        status_code=413,
        content={"detail": ErrorCode.FILE_TOO_LARGE, "error_code": ErrorCode.FILE_TOO_LARGE},
    )


class BodySizeLimitMiddleware:
    """ASGI middleware enforcing a global request body size limit."""

    def __init__(self, app: ASGIApp, max_body_bytes: int | None = None):
        self.app = app
        self.max_body_bytes = max_body_bytes or get_max_request_body_bytes()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared_length = self._declared_content_length(scope)
        if declared_length is not None and declared_length > self.max_body_bytes:
            self._log_rejection(scope, declared_length, "content_length")
            await _payload_too_large_response()(scope, receive, send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    self._log_rejection(scope, received, "streamed_body")
                    raise RequestBodyTooLarge()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except RequestBodyTooLarge:
            # Raised outside FastAPI's exception handling (e.g. a handler that
            # reads request.stream() itself). Answer if nothing was sent yet.
            if response_started:
                raise
            await _payload_too_large_response()(scope, receive, send)

    @staticmethod
    def _declared_content_length(scope: Scope) -> int | None:
        for key, value in scope.get("headers", []):
            if key == b"content-length":
                try:
                    return int(value)
                except ValueError:
                    return None
        return None

    def _log_rejection(self, scope: Scope, size: int, reason: str) -> None:
        log_with_context(
            logger,
            logging.WARNING,
            "Request body too large",
            path=scope.get("path"),
            method=scope.get("method"),
            body_bytes=size,
            max_body_bytes=self.max_body_bytes,
            reason=reason,
        )
