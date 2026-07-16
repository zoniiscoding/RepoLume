"""Shared error model and exception-to-response translation."""

from http import HTTPStatus
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.request_context import get_request_id
from app.schemas.errors import ErrorBody, ErrorCode, ErrorEnvelope

logger = structlog.get_logger(__name__)


class APIError(Exception):
    """An intentional, client-safe API failure."""

    def __init__(
        self,
        *,
        status_code: int,
        code: ErrorCode,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(code.value)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def _error_response(
    *,
    status_code: int,
    code: ErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code=code,
            message=message,
            request_id=get_request_id(),
            details=details,
        )
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(mode="json"))


async def _api_error_handler(request: Request, error: APIError) -> JSONResponse:
    del request
    return _error_response(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        details=error.details,
    )


async def _validation_error_handler(
    request: Request,
    error: RequestValidationError,
) -> JSONResponse:
    del request
    issues = [
        {
            "location": ".".join(str(part) for part in issue["loc"]),
            "type": issue["type"],
        }
        for issue in error.errors()
    ]
    return _error_response(
        status_code=422,
        code=ErrorCode.VALIDATION_ERROR,
        message="Request validation failed",
        details={"issues": issues},
    )


async def _http_error_handler(
    request: Request,
    error: StarletteHTTPException,
) -> JSONResponse:
    del request
    status_code = error.status_code
    code = ErrorCode.NOT_FOUND if status_code == HTTPStatus.NOT_FOUND else ErrorCode.INVALID_REQUEST
    try:
        message = HTTPStatus(status_code).phrase
    except ValueError:
        message = "Request failed"
    return _error_response(status_code=status_code, code=code, message=message)


async def _unhandled_error_handler(request: Request, error: Exception) -> JSONResponse:
    del request
    logger.error("unhandled_exception", error_type=type(error).__name__)
    return _error_response(
        status_code=500,
        code=ErrorCode.INTERNAL_ERROR,
        message="An unexpected error occurred",
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Install the complete safe exception translation layer."""
    app.add_exception_handler(APIError, _api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_error_handler)
