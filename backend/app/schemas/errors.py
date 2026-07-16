"""Public error response schemas."""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    """Stable public error codes."""

    INVALID_REQUEST = "invalid_request"
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    OAUTH_STATE_INVALID = "oauth_state_invalid"
    TOKEN_REUSE_DETECTED = "token_reuse_detected"
    WEBHOOK_SIGNATURE_INVALID = "webhook_signature_invalid"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"
    SERVICE_UNAVAILABLE = "service_unavailable"
    INTERNAL_ERROR = "internal_error"


class ErrorBody(BaseModel):
    """Stable client-visible error fields."""

    model_config = ConfigDict(extra="forbid")

    code: ErrorCode
    message: str
    request_id: str
    details: dict[str, Any] | None = None


class ErrorEnvelope(BaseModel):
    """Top-level API error envelope."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorBody
