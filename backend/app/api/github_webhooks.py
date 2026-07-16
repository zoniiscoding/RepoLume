"""Authenticated GitHub App webhook ingress."""

from typing import cast

from fastapi import APIRouter, Request, Response, status

from app.core.config import Settings
from app.core.errors import APIError
from app.db.session import Database
from app.schemas.errors import ErrorCode
from app.schemas.webhooks import WebhookResponse
from app.services.webhooks import (
    WebhookPayloadError,
    WebhookService,
    WebhookSignatureError,
)

router = APIRouter()
MAX_WEBHOOK_BODY_BYTES = 1_048_576


async def _read_bounded_body(request: Request) -> bytes:
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_WEBHOOK_BODY_BYTES:
            raise APIError(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                code=ErrorCode.INVALID_REQUEST,
                message="Webhook payload is too large",
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/github")
async def github_webhook(request: Request, response: Response) -> WebhookResponse:
    """Validate the raw body before parsing and apply only bounded durable work."""
    body = await _read_bounded_body(request)
    service = WebhookService(
        cast(Database, request.app.state.database),
        cast(Settings, request.app.state.settings),
    )
    try:
        result = await service.handle(
            body=body,
            signature=request.headers.get("X-Hub-Signature-256"),
            delivery_id=request.headers.get("X-GitHub-Delivery"),
            event_name=request.headers.get("X-GitHub-Event"),
        )
    except WebhookSignatureError as error:
        raise APIError(
            status_code=status.HTTP_401_UNAUTHORIZED,
            code=ErrorCode.WEBHOOK_SIGNATURE_INVALID,
            message="Webhook signature is invalid",
        ) from error
    except WebhookPayloadError as error:
        raise APIError(
            status_code=status.HTTP_400_BAD_REQUEST,
            code=ErrorCode.INVALID_REQUEST,
            message="Webhook request is invalid",
        ) from error
    if result == "accepted":
        response.status_code = status.HTTP_202_ACCEPTED
    return WebhookResponse(status=result)
