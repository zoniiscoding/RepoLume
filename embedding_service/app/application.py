"""Private FastAPI embedding service and model lifecycle."""

import asyncio
import math
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Literal

import structlog
from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings, load_settings
from app.logging import configure_logging
from app.model import EmbeddingModelProtocol, FastEmbedModel
from app.schemas import (
    EmbeddingRequest,
    EmbeddingResponse,
    EmbeddingResult,
    LivenessResponse,
    ReadinessResponse,
)

logger = structlog.get_logger(__name__)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class _BodyTooLargeError(Exception):
    pass


class BodyLimitMiddleware:
    """Reject declared or streamed bodies beyond the configured byte ceiling."""

    def __init__(self, app: ASGIApp, *, max_bytes: int) -> None:
        self._app = app
        self._max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        content_length = headers.get(b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self._max_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                await self._reject(scope, receive, send)
                return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self._max_bytes:
                    raise _BodyTooLargeError
            return message

        try:
            await self._app(scope, limited_receive, send)
        except _BodyTooLargeError:
            await self._reject(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send) -> None:
        response = JSONResponse(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            content={"detail": {"code": "request_too_large"}},
        )
        await response(scope, receive, send)


@dataclass(slots=True)
class ModelRuntime:
    status: Literal["loading", "ready", "failed"] = "loading"


def create_app(
    settings: Settings | None = None,
    model: EmbeddingModelProtocol | None = None,
) -> FastAPI:
    resolved = settings or load_settings()
    configure_logging(level=resolved.log_level, render_json=resolved.log_json)
    resolved_model = model or FastEmbedModel(resolved)
    runtime = ModelRuntime()
    semaphore = asyncio.Semaphore(resolved.max_concurrent_requests)

    app = FastAPI(
        title="RepoLume Private Embedding Service",
        version="0.5.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lambda application: _lifespan(
            application,
            settings=resolved,
            model=resolved_model,
            runtime=runtime,
        ),
    )
    app.state.settings = resolved
    app.state.model = resolved_model
    app.state.runtime = runtime
    app.add_middleware(BodyLimitMiddleware, max_bytes=resolved.max_request_bytes)
    _register_error_handler(app)
    authenticate = _authentication_dependency(resolved)
    _register_health_routes(app, resolved, runtime, authenticate)
    _register_embedding_route(
        app,
        resolved,
        resolved_model,
        runtime,
        semaphore,
        authenticate,
    )
    return app


async def _load_and_warm(
    settings: Settings,
    model: EmbeddingModelProtocol,
    runtime: ModelRuntime,
) -> None:
    try:
        await asyncio.to_thread(model.load)
        vectors = await asyncio.to_thread(
            model.embed,
            "document",
            ["RepoLume deterministic model readiness check"],
        )
        _validate_vectors(vectors, expected=1, dimension=settings.model_dimension)
    except asyncio.CancelledError:
        raise
    except Exception as error:  # noqa: BLE001 -- never expose model/cache internals
        runtime.status = "failed"
        logger.error(  # noqa: TRY400 -- traceback can reveal model/cache internals
            "embedding_model_load_failed", error_type=type(error).__name__
        )
        return
    runtime.status = "ready"
    logger.info(
        "embedding_model_ready",
        model_identifier=settings.model_identifier,
        model_revision=settings.model_revision,
        dimension=settings.model_dimension,
    )


@asynccontextmanager
async def _lifespan(
    _: FastAPI,
    *,
    settings: Settings,
    model: EmbeddingModelProtocol,
    runtime: ModelRuntime,
) -> AsyncIterator[None]:
    logger.info("embedding_service_started", **settings.safe_summary())
    loader = asyncio.create_task(_load_and_warm(settings, model, runtime))
    try:
        yield
    finally:
        if not loader.done():
            loader.cancel()
        with suppress(asyncio.CancelledError):
            await loader
        await asyncio.to_thread(model.close)
        logger.info("embedding_service_stopped")


def _register_error_handler(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": {"code": "invalid_request"}},
        )


def _authentication_dependency(settings: Settings) -> Callable[[str | None], Awaitable[None]]:
    async def authenticate(
        authorization: str | None = Header(default=None),
    ) -> None:
        expected = f"Bearer {settings.service_token.get_secret_value()}"
        supplied = authorization or ""
        if not secrets.compare_digest(supplied.encode(), expected.encode()):
            raise _private_error(status.HTTP_401_UNAUTHORIZED, "authentication_required")

    return authenticate


def _register_health_routes(
    app: FastAPI,
    settings: Settings,
    runtime: ModelRuntime,
    authenticate: Callable[[str | None], Awaitable[None]],
) -> None:
    @app.get("/health/live")
    async def liveness() -> LivenessResponse:
        return LivenessResponse(status="ok")

    @app.get(
        "/health/ready",
        dependencies=[Depends(authenticate)],
        response_model=None,
    )
    async def readiness() -> ReadinessResponse | JSONResponse:
        response = ReadinessResponse(
            status=runtime.status,
            model=settings.model_identifier,
            revision=settings.model_revision,
            dimension=settings.model_dimension,
            normalized=True,
            maximum_tokens=settings.model_max_tokens,
        )
        if runtime.status == "ready":
            return response
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=response.model_dump()
        )


def _register_embedding_route(
    app: FastAPI,
    settings: Settings,
    model: EmbeddingModelProtocol,
    runtime: ModelRuntime,
    semaphore: asyncio.Semaphore,
    authenticate: Callable[[str | None], Awaitable[None]],
) -> None:
    @app.post("/v1/embeddings", dependencies=[Depends(authenticate)])
    async def embed(payload: EmbeddingRequest, request: Request) -> EmbeddingResponse:
        started = time.monotonic()
        request_id = _request_id(request.headers.get("X-Request-ID"))
        if runtime.status != "ready":
            raise _private_error(status.HTTP_503_SERVICE_UNAVAILABLE, "model_not_ready")
        if len(payload.documents) > settings.max_documents_per_request:
            raise _private_error(status.HTTP_413_CONTENT_TOO_LARGE, "too_many_documents")
        if payload.kind == "query" and len(payload.documents) != 1:
            raise _private_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_query_batch")
        if len({document.id for document in payload.documents}) != len(payload.documents):
            raise _private_error(status.HTTP_422_UNPROCESSABLE_CONTENT, "duplicate_document_id")
        text_bytes = [len(document.text.encode("utf-8")) for document in payload.documents]
        if any(size > settings.max_text_bytes_per_document for size in text_bytes):
            raise _private_error(status.HTTP_413_CONTENT_TOO_LARGE, "document_too_large")
        if sum(text_bytes) > settings.max_total_text_bytes:
            raise _private_error(status.HTTP_413_CONTENT_TOO_LARGE, "request_text_too_large")
        texts = [document.text for document in payload.documents]
        try:
            async with semaphore:
                vectors = await asyncio.wait_for(
                    asyncio.to_thread(_validated_embed, model, settings, payload.kind, texts),
                    timeout=settings.request_timeout_seconds,
                )
        except TimeoutError as error:
            logger.warning(
                "embedding_request_timeout",
                request_id=request_id,
                kind=payload.kind,
                document_count=len(payload.documents),
            )
            raise _private_error(status.HTTP_504_GATEWAY_TIMEOUT, "embedding_timeout") from error
        except _TokenLimitError as error:
            raise _private_error(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "model_token_limit_exceeded"
            ) from error
        except Exception as error:
            logger.error(  # noqa: TRY400 -- traceback can reveal private source text
                "embedding_request_failed",
                request_id=request_id,
                kind=payload.kind,
                document_count=len(payload.documents),
                error_type=type(error).__name__,
            )
            raise _private_error(
                status.HTTP_500_INTERNAL_SERVER_ERROR, "embedding_generation_failed"
            ) from error
        logger.info(
            "embedding_request_completed",
            request_id=request_id,
            kind=payload.kind,
            document_count=len(payload.documents),
            duration_ms=round((time.monotonic() - started) * 1000, 3),
        )
        return EmbeddingResponse(
            model=settings.model_identifier,
            revision=settings.model_revision,
            dimension=settings.model_dimension,
            normalized=True,
            results=[
                EmbeddingResult(id=document.id, embedding=vector)
                for document, vector in zip(payload.documents, vectors, strict=True)
            ],
        )


class _TokenLimitError(Exception):
    pass


def _validated_embed(
    model: EmbeddingModelProtocol,
    settings: Settings,
    kind: Literal["document", "query"],
    texts: Sequence[str],
) -> list[list[float]]:
    if any(model.token_count(text) > settings.model_max_tokens for text in texts):
        raise _TokenLimitError
    vectors = model.embed(kind, texts)
    _validate_vectors(vectors, expected=len(texts), dimension=settings.model_dimension)
    return vectors


def _validate_vectors(vectors: Sequence[Sequence[float]], *, expected: int, dimension: int) -> None:
    if len(vectors) != expected:
        raise ValueError("embedding_count_mismatch")
    for vector in vectors:
        if len(vector) != dimension or any(not math.isfinite(value) for value in vector):
            raise ValueError("invalid_embedding")
        norm = math.sqrt(sum(value * value for value in vector))
        if not math.isclose(norm, 1.0, rel_tol=1e-4, abs_tol=1e-4):
            raise ValueError("embedding_not_normalized")


def _request_id(supplied: str | None) -> str:
    if supplied is not None and _REQUEST_ID.fullmatch(supplied):
        return supplied
    return str(uuid.uuid4())


def _private_error(status_code: int, code: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code})
