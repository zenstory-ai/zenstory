"""
Unified error handler for API exceptions.

Provides custom APIException class and global exception handling.
"""
import logging
from typing import Any

from fastapi import HTTPException, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.error_codes import ErrorCode
from utils.logger import get_logger, log_with_context

logger = get_logger(__name__)


class APIException(HTTPException):
    """
    Custom API exception with error code support.

    Usage:
        raise APIException(
            error_code=ErrorCode.PROJECT_NOT_FOUND,
            status_code=404
        )

    The response will include:
    {
        "detail": "ERR_PROJECT_NOT_FOUND",
        "error_code": "ERR_PROJECT_NOT_FOUND"
    }
    """

    def __init__(
        self,
        error_code: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        headers: dict[str, Any] | None = None,
        detail: Any | None = None,
        message: str | None = None,
        **kwargs,
    ):
        self.error_code = error_code
        # Backward compatibility: many callsites still pass `message=...`.
        if detail is None and message is not None:
            detail = message
        # If detail is provided, use it; otherwise use error_code
        detail_value = detail if detail is not None else error_code
        # Ignore unknown kwargs to prevent runtime crashes from legacy callsites.
        super().__init__(status_code=status_code, detail=detail_value, headers=headers)


async def api_exception_handler(request: Request, exc: APIException) -> JSONResponse:
    """
    Handle APIException instances.

    Returns standardized error response with error code.
    """
    log_with_context(
        logger,
        logging.WARNING,
        "APIException raised",
        error_code=exc.error_code,
        status_code=exc.status_code,
        path=request.url.path,
        method=request.method,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.error_code,
            "error_code": exc.error_code,
            "error_detail": exc.detail,
        },
        headers=getattr(exc, "headers", None),
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """
    Handle standard HTTPException instances.

    This is for backward compatibility with existing code that still uses
    standard HTTPException.
    """
    log_with_context(
        logger,
        logging.WARNING,
        "HTTPException raised",
        detail=str(exc.detail),
        status_code=exc.status_code,
        path=request.url.path,
        method=request.method,
    )

    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc.detail)},
        # Preserve headers such as Retry-After (429) and WWW-Authenticate (401).
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """
    Handle request validation errors.

    Returns which fields failed and why. The submitted values (``input``) and
    validator context (``ctx``) are dropped from both the log and the
    response: they can hold passwords or whole chapters, and ``ctx`` may hold
    exception objects that are not JSON serializable.
    """
    errors = summarize_validation_errors(exc.errors())
    log_with_context(
        logger,
        logging.WARNING,
        "Request validation failed",
        errors=errors,
        path=request.url.path,
        method=request.method,
    )

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=jsonable_encoder(
            {
                "detail": ErrorCode.VALIDATION_ERROR,
                "error_code": ErrorCode.VALIDATION_ERROR,
                "errors": errors,
            }
        ),
    )


def summarize_validation_errors(errors: Any) -> list[dict[str, Any]]:
    """Keep only ``loc``/``type``/``msg`` from Pydantic validation errors."""
    summary: list[dict[str, Any]] = []
    for error in errors or []:
        if not isinstance(error, dict):
            continue
        summary.append(
            {
                "loc": list(error.get("loc", ())),
                "type": str(error.get("type", "")),
                "msg": str(error.get("msg", "")),
            }
        )
    return summary


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    """
    Handle uncaught database integrity (unique/constraint) violations.

    Converts them into a clean 409 Conflict instead of leaking a raw 500, and
    never exposes the underlying SQL to clients. This is a safety net for any
    unhandled constraint collision (e.g. a unique-key race between a duplicate
    check and an insert/update).
    """
    log_with_context(
        logger,
        logging.WARNING,
        "Database integrity error",
        error_type=type(exc).__name__,
        error_message=str(getattr(exc, "orig", exc)),
        path=request.url.path,
        method=request.method,
    )

    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": ErrorCode.RESOURCE_CONFLICT,
            "error_code": ErrorCode.RESOURCE_CONFLICT,
        },
    )


def internal_error_response(request_id: str | None = None) -> JSONResponse:
    """Build the generic 500 body; internals never reach clients."""
    content: dict[str, Any] = {
        "detail": ErrorCode.INTERNAL_SERVER_ERROR,
        "error_code": ErrorCode.INTERNAL_SERVER_ERROR,
    }
    if request_id:
        content["request_id"] = request_id
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=content)


async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """
    Handle all unhandled exceptions.

    This is a catch-all handler for unexpected errors.
    """
    log_with_context(
        logger,
        logging.ERROR,
        "Unhandled exception",
        exc_info=exc,
        error_type=type(exc).__name__,
        error_message=str(exc),
        path=request.url.path,
        method=request.method,
    )

    # Don't expose internal errors to clients in production
    return internal_error_response(getattr(request.state, "request_id", None))
