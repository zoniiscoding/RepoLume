"""FastAPI application construction and lifecycle."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.router import api_router
from app.auth.tokens import TokenService
from app.core.config import Settings, load_settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging
from app.core.request_context import RequestContextMiddleware
from app.core.security_headers import SecurityHeadersMiddleware
from app.db.session import Database, DatabaseProtocol
from app.github.client import GitHubClient, GitHubClientProtocol
from app.queue import JobQueueProtocol, RedisJobQueue
from app.vector.qdrant import QdrantVectorStore, VectorReadinessProtocol

logger = structlog.get_logger(__name__)


def create_app(
    settings: Settings | None = None,
    database: DatabaseProtocol | None = None,
    github_client: GitHubClientProtocol | None = None,
    job_queue: JobQueueProtocol | None = None,
    vector_store: VectorReadinessProtocol | None = None,
) -> FastAPI:
    """Create a fully configured application with explicit dependencies."""
    resolved_settings = settings or load_settings()
    configure_logging(
        level=resolved_settings.log_level,
        render_json=resolved_settings.log_json,
    )
    resolved_database = database or Database.from_settings(resolved_settings)
    resolved_github_client = github_client or GitHubClient(resolved_settings)
    resolved_job_queue = job_queue or RedisJobQueue.from_settings(resolved_settings)
    resolved_vector_store = vector_store or QdrantVectorStore(resolved_settings)
    token_service = TokenService(resolved_settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        app.state.database = resolved_database
        app.state.github_client = resolved_github_client
        app.state.token_service = token_service
        app.state.job_queue = resolved_job_queue
        app.state.vector_store = resolved_vector_store
        logger.info("application_started", **resolved_settings.safe_summary())
        try:
            yield
        finally:
            await resolved_database.dispose()
            await resolved_github_client.close()
            await resolved_job_queue.close()
            await resolved_vector_store.close()
            logger.info("application_stopped")

    docs_url = "/docs" if resolved_settings.docs_enabled else None
    openapi_url = "/openapi.json" if resolved_settings.docs_enabled else None
    app = FastAPI(
        title=resolved_settings.app_name,
        version="0.5.0",
        docs_url=docs_url,
        redoc_url=None,
        openapi_url=openapi_url,
        lifespan=lifespan,
    )

    app.state.settings = resolved_settings
    app.state.database = resolved_database
    app.state.github_client = resolved_github_client
    app.state.token_service = token_service
    app.state.job_queue = resolved_job_queue
    app.state.vector_store = resolved_vector_store

    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=resolved_settings.trusted_hosts,
    )
    if resolved_settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[str(origin).rstrip("/") for origin in resolved_settings.cors_origins],
            allow_credentials=True,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "X-CSRF-Token",
                "X-Request-ID",
            ],
            expose_headers=["X-Request-ID"],
            max_age=600,
        )
    app.add_middleware(
        SecurityHeadersMiddleware,
        enable_hsts=resolved_settings.is_production,
    )
    app.add_middleware(RequestContextMiddleware)

    install_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app
